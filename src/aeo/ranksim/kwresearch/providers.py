"""Volume providers. The first source that returns a real number wins.

Order: seodata.dev, Semrush public US overview tables, then DataForSEO and
Google Ads Keyword Planner when their env keys are set. A 404, a captcha, or a
zero that the source itself marks as "no data" is not a volume.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

import httpx

from aeo.ranksim.kwresearch.http import PoliteHttp, scrub
from aeo.ranksim.kwresearch.models import (
    Attempt,
    VolumeRecord,
    display_volume,
    norm_keyword,
    now_iso,
    parse_float,
    parse_int,
    slugify_keyword,
)
from aeo.ranksim.site_history import site_key

SEODATA_API = "https://api.seodata.dev/v1/keyword"
SEODATA_PAGE = "https://www.seodata.dev/keyword/{slug}"
SEODATA_SOURCE = "seodata.dev, Google Ads API data, US"
SEMRUSH_PAGE = "https://www.semrush.com/website/{domain}/overview/"
SEMRUSH_SOURCE = "Semrush public US organic keywords table"
DATAFORSEO_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
DATAFORSEO_SOURCE = "DataForSEO Google Ads search_volume, US"
GOOGLE_ADS_DOCS = "https://developers.google.com/google-ads/api/docs/keyword-planning/generate-historical-metrics"
GOOGLE_ADS_SOURCE = "Google Ads API Keyword Planner, US"
GOOGLE_ADS_VERSION = "v26"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

NAV_TOKENS = {"login", "log", "in", "signin", "sign", "app", "pricing", "reviews", "review", "account"}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
SEMRUSH_ROW = re.compile(
    r'"keyword":\[0,"((?:\\.|[^"\\])*)"\].{0,500}?"searchVolume":\[0,(\d+)\](?:.{0,200}?"cpc":\[0,([0-9.]+)\])?',
    re.S,
)
CAPTCHA_MARKERS = ("g-recaptcha", "recaptcha", "cf-challenge", "verify you are human", "just a moment")


@dataclass
class PaidConfig:
    dataforseo_login: str | None = None
    dataforseo_password: str | None = None
    google_ads_developer_token: str | None = None
    google_ads_client_id: str | None = None
    google_ads_client_secret: str | None = None
    google_ads_refresh_token: str | None = None
    google_ads_customer_id: str | None = None
    google_ads_login_customer_id: str | None = None
    google_ads_api_version: str = GOOGLE_ADS_VERSION

    @classmethod
    def from_env(cls) -> PaidConfig:
        def env(name: str) -> str | None:
            value = os.environ.get(name, "").strip()
            return value or None

        return cls(
            dataforseo_login=env("DATAFORSEO_LOGIN"),
            dataforseo_password=env("DATAFORSEO_PASSWORD"),
            google_ads_developer_token=env("GOOGLE_ADS_DEVELOPER_TOKEN"),
            google_ads_client_id=env("GOOGLE_ADS_CLIENT_ID"),
            google_ads_client_secret=env("GOOGLE_ADS_CLIENT_SECRET"),
            google_ads_refresh_token=env("GOOGLE_ADS_REFRESH_TOKEN"),
            google_ads_customer_id=env("GOOGLE_ADS_CUSTOMER_ID"),
            google_ads_login_customer_id=env("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
            google_ads_api_version=env("GOOGLE_ADS_API_VERSION") or GOOGLE_ADS_VERSION,
        )

    def dataforseo_ready(self) -> bool:
        return bool(self.dataforseo_login and self.dataforseo_password)

    def google_ads_ready(self) -> bool:
        return bool(
            self.google_ads_developer_token
            and self.google_ads_client_id
            and self.google_ads_client_secret
            and self.google_ads_refresh_token
            and self.google_ads_customer_id
        )

    def secrets(self) -> list[str]:
        return [
            self.dataforseo_login or "",
            self.dataforseo_password or "",
            self.google_ads_developer_token or "",
            self.google_ads_client_id or "",
            self.google_ads_client_secret or "",
            self.google_ads_refresh_token or "",
            self.google_ads_customer_id or "",
            self.google_ads_login_customer_id or "",
        ]


@dataclass
class SemrushTable:
    domain: str
    url: str
    status: str
    data_date: str
    series_month: str
    fetched_at: str
    rows: dict[str, dict] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "url": self.url,
            "status": self.status,
            "data_date": self.data_date,
            "series_month": self.series_month,
            "fetched_at": self.fetched_at,
            "rows": self.rows,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SemrushTable:
        return cls(
            domain=str(data.get("domain") or ""),
            url=str(data.get("url") or ""),
            status=str(data.get("status") or ""),
            data_date=str(data.get("data_date") or ""),
            series_month=str(data.get("series_month") or ""),
            fetched_at=str(data.get("fetched_at") or ""),
            rows=dict(data.get("rows") or {}),
            detail=str(data.get("detail") or ""),
        )


def seodata_page_url(keyword: str) -> str:
    return SEODATA_PAGE.format(slug=slugify_keyword(keyword) or "keyword")


def parse_seodata_api(payload: dict, status: int, keyword: str) -> VolumeRecord | None:
    """HTTP 404, or a body with volume 0 and cached false, is no data, not zero searches."""
    if not isinstance(payload, dict):
        return None
    if status == 404 or (payload.get("cached") is False and parse_int(payload.get("volume")) in {None, 0}):
        return None
    if status != 200:
        return None
    returned = norm_keyword(str(payload.get("keyword") or ""))
    if returned and returned != norm_keyword(keyword):
        return None
    volume = parse_int(payload.get("volume"))
    if volume is None:
        return None
    if volume == 0 and not payload.get("cached"):
        return None
    cached_at = str(payload.get("cached_at") or "")
    data_date = cached_at[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cached_at[:10]) else ""
    if not data_date:
        return None
    cpc = parse_float(payload.get("cpc"))
    competition = payload.get("competition")
    competition_text = "" if competition in (None, "") else str(competition)
    return _live(
        keyword,
        volume=volume,
        provider="seodata.dev",
        volume_source=SEODATA_SOURCE,
        source_url=seodata_page_url(keyword),
        data_date=data_date,
        cpc=cpc,
        competition=competition_text,
        notes="Monthly average for US. seodata.dev says this is Google Ads API data. The figure is a Keyword Planner bucket.",
    )


def parse_seodata_html(page: str, status: int, keyword: str) -> VolumeRecord | None:
    if status == 404 or re.search(r"No search-volume data|No search data", page, re.I):
        return None
    text = html.unescape(page)
    updated = re.search(r"updated\s+(\d{4}-\d{2}-\d{2})", text)
    described = re.search(
        r"Monthly search volume for .+? is ([0-9][0-9,]*)\.\s*CPC:\s*\$([0-9][0-9.]*)\.\s*Competition:\s*([0-9.]+)",
        text,
        re.I,
    )
    if not updated or not described:
        return None
    volume = parse_int(described.group(1))
    cpc = parse_float(described.group(2))
    if volume is None:
        return None
    return _live(
        keyword,
        volume=volume,
        provider="seodata.dev",
        volume_source=SEODATA_SOURCE,
        source_url=seodata_page_url(keyword),
        data_date=updated.group(1),
        cpc=cpc,
        competition=described.group(3),
        notes="Parsed from the public seodata.dev keyword page. The page says the numbers are from the Google Ads API.",
    )


def fetch_seodata(http: PoliteHttp, keyword: str) -> tuple[VolumeRecord | None, Attempt]:
    params = {"q": keyword, "country": "us"}
    try:
        response = http.request("GET", SEODATA_API, params=params)
    except httpx.HTTPError:
        return None, Attempt("seodata.dev", "error", "request failed")
    payload = _json_or_none(response)
    if isinstance(payload, dict):
        parsed = parse_seodata_api(payload, response.status_code, keyword)
        if parsed is not None:
            return parsed, Attempt("seodata.dev", "hit", f"HTTP {response.status_code}")
        if response.status_code == 404 or (payload.get("cached") is False and parse_int(payload.get("volume")) in {None, 0}):
            return None, Attempt("seodata.dev", "miss", "HTTP 404, no Google Ads volume (a volume of 0 on that response is not treated as zero searches)")
        if response.status_code == 200 and not str(payload.get("cached_at") or "")[:10]:
            return None, Attempt("seodata.dev", "miss", "HTTP 200 without a data date")
    if response.status_code == 404:
        return None, Attempt("seodata.dev", "miss", "HTTP 404, no Google Ads volume")
    try:
        page = http.request("GET", seodata_page_url(keyword))
    except httpx.HTTPError:
        return None, Attempt("seodata.dev", "error", f"API HTTP {response.status_code}; keyword page request failed")
    parsed = parse_seodata_html(page.text, page.status_code, keyword)
    if parsed is not None:
        return parsed, Attempt("seodata.dev", "hit", f"keyword page HTTP {page.status_code}")
    if page.status_code == 404:
        return None, Attempt("seodata.dev", "miss", "HTTP 404, no Google Ads volume")
    return None, Attempt("seodata.dev", "miss", f"HTTP {page.status_code}, no monthly volume")


def parse_semrush_overview(page: str, *, domain: str, url: str, status_code: int = 200) -> SemrushTable:
    fetched = now_iso()
    text = html.unescape(page)
    lowered = text.lower()
    has_rows = "searchvolume" in lowered
    captcha = status_code in {401, 403, 429, 503} or (
        any(marker in lowered for marker in CAPTCHA_MARKERS) and not has_rows
    )
    if captcha:
        return SemrushTable(domain, url, "captcha", "", "", fetched, {}, "captcha or login wall, not bypassed")
    if '"country":[0,"US"]' not in text:
        return SemrushTable(domain, url, "no_table", "", "", fetched, {}, "no public US organic keyword table")
    start = text.find('"country":[0,"US"]')
    chunk = text[max(0, start - 80): start + 20000]
    rows: dict[str, dict] = {}
    for keyword, volume_text, cpc_text in SEMRUSH_ROW.findall(chunk):
        keyword = keyword.replace('\\"', '"').replace("\\/", "/")
        volume = parse_int(volume_text)
        if volume is None:
            continue
        rows[norm_keyword(keyword)] = {
            "keyword": keyword,
            "volume": volume,
            "cpc": parse_float(cpc_text) if cpc_text else None,
        }
    updated = re.search(r"Last updated:\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    series = re.search(r'"displayDate":\[0,"(\d{4}-\d{2}-\d{2})"\],"country":\[0,"US"\]', text)
    data_date = ""
    if updated and updated.group(1).lower() in MONTHS:
        month = MONTHS[updated.group(1).lower()]
        data_date = f"{int(updated.group(3)):04d}-{month:02d}-{int(updated.group(2)):02d}"
    series_month = series.group(1) if series else ""
    if not data_date:
        data_date = series_month
    if not rows or not data_date:
        return SemrushTable(domain, url, "no_table", data_date, series_month, fetched, {}, "US table had no dated keyword rows")
    return SemrushTable(domain, url, "ok", data_date, series_month, fetched, rows, "")


def semrush_record(keyword: str, table: SemrushTable) -> VolumeRecord | None:
    row = table.rows.get(norm_keyword(keyword))
    if not row or table.status != "ok" or not table.data_date:
        return None
    volume = parse_int(row.get("volume"))
    if volume is None:
        return None
    note = "Public US organic keyword table"
    if table.series_month:
        note += f". Keyword table month {table.series_month}"
    if table.data_date:
        note += f". Page date {table.data_date}"
    record = _live(
        keyword,
        volume=volume,
        provider="semrush",
        volume_source=SEMRUSH_SOURCE,
        source_url=table.url,
        data_date=table.data_date,
        cpc=parse_float(row.get("cpc")),
        competition="",
        notes=note,
    )
    record.semrush_us_volume = volume
    record.semrush_url = table.url
    record.semrush_data_date = table.data_date
    return record


def guess_semrush_domains(keyword: str) -> list[str]:
    tokens = norm_keyword(keyword).split()
    if not tokens or not re.fullmatch(r"[a-z0-9]{3,}", tokens[0]):
        return []
    if len(tokens) == 1 or any(token in NAV_TOKENS for token in tokens[1:]):
        return [f"{tokens[0]}.com"]
    return []


def load_domain_file(path: str) -> list[str]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        domain = _domain(text)
        if domain and domain not in found:
            found.append(domain)
    return found


def fetch_dataforseo(http: PoliteHttp, keywords: list[str], config: PaidConfig) -> dict[str, tuple[VolumeRecord | None, Attempt]]:
    out: dict[str, tuple[VolumeRecord | None, Attempt]] = {}
    if not keywords:
        return out
    if not config.dataforseo_ready():
        for keyword in keywords:
            out[keyword] = (None, Attempt("dataforseo", "skip", "DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are not set"))
        return out
    body = [{"keywords": keywords, "location_code": 2840, "language_code": "en"}]
    try:
        response = http.request(
            "POST",
            DATAFORSEO_URL,
            json=body,
            auth=(config.dataforseo_login or "", config.dataforseo_password or ""),
        )
    except httpx.HTTPError:
        detail = scrub("request failed", config.secrets())
        for keyword in keywords:
            out[keyword] = (None, Attempt("dataforseo", "error", detail))
        return out
    if response.status_code >= 400:
        detail = f"HTTP {response.status_code}"
        for keyword in keywords:
            out[keyword] = (None, Attempt("dataforseo", "error", detail))
        return out
    payload = _json_or_none(response) or {}
    found: dict[str, VolumeRecord] = {}
    for task in payload.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if task.get("status_code") not in (None, 20000):
            continue
        for row in task.get("result") or []:
            if not isinstance(row, dict):
                continue
            record = _dataforseo_row(row)
            if record is not None:
                found[norm_keyword(record.keyword)] = record
    for keyword in keywords:
        record = found.get(norm_keyword(keyword))
        if record is None:
            out[keyword] = (None, Attempt("dataforseo", "miss", "no US monthly volume in the Google Ads search_volume response"))
        else:
            record.keyword = keyword
            out[keyword] = (record, Attempt("dataforseo", "hit", "Google Ads search_volume"))
    return out


def _dataforseo_row(row: dict) -> VolumeRecord | None:
    keyword = str(row.get("keyword") or "").strip()
    if not keyword:
        return None
    volume = parse_int(row.get("search_volume")) if row.get("search_volume") is not None else None
    if row.get("search_volume") is None or volume is None:
        return None
    months = row.get("monthly_searches") or []
    latest = _latest_month(months, year_key="year", month_key="month", number=True)
    if not latest:
        return None
    if volume == 0 and not months:
        return None
    label = str(row.get("competition") or "").strip()
    index = parse_int(row.get("competition_index"))
    competition = label if label and label.upper() not in {"", "NONE", "UNSPECIFIED"} else ("" if index is None else str(index))
    record = _live(
        keyword,
        volume=volume,
        provider="dataforseo",
        volume_source=DATAFORSEO_SOURCE,
        source_url=DATAFORSEO_URL,
        data_date=latest,
        cpc=parse_float(row.get("cpc")),
        competition=competition,
        notes="location_code 2840 (United States), language en. search_volume is the Google Ads figure DataForSEO returns.",
    )
    record.competition_index = index
    return record


def fetch_google_ads(http: PoliteHttp, keywords: list[str], config: PaidConfig) -> dict[str, tuple[VolumeRecord | None, Attempt]]:
    out: dict[str, tuple[VolumeRecord | None, Attempt]] = {}
    if not keywords:
        return out
    if not config.google_ads_ready():
        for keyword in keywords:
            out[keyword] = (None, Attempt("google_ads", "skip", "GOOGLE_ADS_DEVELOPER_TOKEN, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, and CUSTOMER_ID are not all set"))
        return out
    secrets = config.secrets() + [re.sub(r"\D", "", config.google_ads_customer_id or "")]
    try:
        token_response = http.request(
            "POST",
            GOOGLE_TOKEN_URL,
            data={
                "client_id": config.google_ads_client_id,
                "client_secret": config.google_ads_client_secret,
                "refresh_token": config.google_ads_refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except httpx.HTTPError:
        detail = scrub("token request failed", secrets)
        for keyword in keywords:
            out[keyword] = (None, Attempt("google_ads", "error", detail))
        return out
    token_body = _json_or_none(token_response) or {}
    access = token_body.get("access_token") if token_response.status_code == 200 else None
    if not isinstance(access, str) or not access:
        for keyword in keywords:
            out[keyword] = (None, Attempt("google_ads", "error", f"token request HTTP {token_response.status_code}"))
        return out
    customer = re.sub(r"\D", "", config.google_ads_customer_id or "")
    version = re.sub(r"[^0-9A-Za-z._-]", "", config.google_ads_api_version or GOOGLE_ADS_VERSION) or GOOGLE_ADS_VERSION
    url = f"https://googleads.googleapis.com/{version}/customers/{customer}:generateKeywordHistoricalMetrics"
    headers = {
        "Authorization": f"Bearer {access}",
        "developer-token": config.google_ads_developer_token or "",
        "Content-Type": "application/json",
    }
    login = re.sub(r"\D", "", config.google_ads_login_customer_id or "")
    if login:
        headers["login-customer-id"] = login
    body = {
        "keywords": keywords,
        "geoTargetConstants": ["geoTargetConstants/2840"],
        "language": "languageConstants/1000",
        "keywordPlanNetwork": "GOOGLE_SEARCH",
        "historicalMetricsOptions": {"includeAverageCpc": True},
    }
    try:
        response = http.request("POST", url, json=body, headers=headers)
    except httpx.HTTPError:
        for keyword in keywords:
            out[keyword] = (None, Attempt("google_ads", "error", scrub("historical metrics request failed", secrets + [access])))
        return out
    if response.status_code >= 400:
        for keyword in keywords:
            out[keyword] = (None, Attempt("google_ads", "error", f"HTTP {response.status_code}"))
        return out
    payload = _json_or_none(response) or {}
    found: dict[str, VolumeRecord] = {}
    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        record = _google_ads_row(row)
        if record is None:
            continue
        names = [record.keyword]
        names.extend(str(item) for item in (row.get("closeVariants") or row.get("close_variants") or []))
        for name in names:
            key = norm_keyword(name)
            if key:
                found[key] = record
    for keyword in keywords:
        record = found.get(norm_keyword(keyword))
        if record is None:
            out[keyword] = (None, Attempt("google_ads", "miss", "keyword not in the Keyword Planner historical metrics response"))
            continue
        copied = VolumeRecord.from_dict(record.to_dict())
        copied.keyword = keyword
        if norm_keyword(record.keyword) != norm_keyword(keyword):
            copied.notes = (copied.notes + f" Google Ads grouped this with {record.keyword}.").strip()
        out[keyword] = (copied, Attempt("google_ads", "hit", "generateKeywordHistoricalMetrics"))
    return out


def _google_ads_row(row: dict) -> VolumeRecord | None:
    keyword = str(row.get("text") or row.get("keyword") or "").strip()
    metrics = row.get("keywordMetrics") or row.get("keyword_metrics") or {}
    if not keyword or not isinstance(metrics, dict):
        return None
    if metrics.get("avgMonthlySearches") is None and metrics.get("avg_monthly_searches") is None:
        return None
    volume = parse_int(metrics.get("avgMonthlySearches") if metrics.get("avgMonthlySearches") is not None else metrics.get("avg_monthly_searches"))
    if volume is None:
        return None
    months = metrics.get("monthlySearchVolumes") or metrics.get("monthly_search_volumes") or []
    latest = _latest_google_month(months)
    if not latest:
        return None
    label = str(metrics.get("competition") or "").strip().upper()
    index = parse_int(metrics.get("competitionIndex") if metrics.get("competitionIndex") is not None else metrics.get("competition_index"))
    competition = label if label in {"LOW", "MEDIUM", "HIGH"} else ("" if index is None else str(index))
    micros = metrics.get("averageCpcMicros")
    if micros is None:
        micros = metrics.get("average_cpc_micros")
    cpc = None
    parsed_micros = parse_int(micros)
    if parsed_micros is not None:
        cpc = parsed_micros / 1_000_000
    record = _live(
        keyword,
        volume=volume,
        provider="google_ads",
        volume_source=GOOGLE_ADS_SOURCE,
        source_url=GOOGLE_ADS_DOCS,
        data_date=latest,
        cpc=cpc,
        competition=competition,
        notes=(
            "generateKeywordHistoricalMetrics, geoTargetConstants/2840 (United States), "
            "languageConstants/1000 (English), keywordPlanNetwork GOOGLE_SEARCH. "
            "The volume is avg monthly searches. The data date is the latest month in the returned series. "
            "CPC is averageCpcMicros when includeAverageCpc is set. Top-of-page bids are not used as CPC."
        ),
    )
    record.competition_index = index
    return record


def _latest_month(months: list, *, year_key: str, month_key: str, number: bool) -> str:
    best: tuple[int, int] | None = None
    for item in months:
        if not isinstance(item, dict):
            continue
        year = parse_int(item.get(year_key))
        month = parse_int(item.get(month_key)) if number else None
        if year is None or month is None or not 1 <= month <= 12:
            continue
        if best is None or (year, month) > best:
            best = (year, month)
    if best is None:
        return ""
    return f"{best[0]:04d}-{best[1]:02d}"


def _latest_google_month(months: list) -> str:
    best: tuple[int, int] | None = None
    for item in months:
        if not isinstance(item, dict):
            continue
        year = parse_int(item.get("year"))
        month_name = str(item.get("month") or "").strip().lower()
        month = MONTHS.get(month_name) or parse_int(item.get("month"))
        if year is None or month is None or not 1 <= month <= 12:
            continue
        if best is None or (year, month) > best:
            best = (year, month)
    if best is None:
        return ""
    return f"{best[0]:04d}-{best[1]:02d}"


def _live(
    keyword: str,
    *,
    volume: int,
    provider: str,
    volume_source: str,
    source_url: str,
    data_date: str,
    cpc: float | None,
    competition: str,
    notes: str,
) -> VolumeRecord:
    return VolumeRecord(
        keyword=keyword,
        us_monthly_volume=volume,
        volume_display=display_volume(volume),
        provider=provider,
        volume_source=volume_source,
        source_url=source_url,
        data_date=data_date,
        fetched_at=now_iso(),
        cpc_usd=cpc,
        competition=competition,
        notes=notes,
    )


def _json_or_none(response: httpx.Response) -> dict | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _domain(value: str) -> str:
    text = value.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        return site_key(text)
    except (ValueError, IndexError):
        return ""


def overview_url(domain: str) -> str:
    return SEMRUSH_PAGE.format(domain=quote(domain, safe=".-"))


def utc_day(moment: date | None = None) -> str:
    return (moment or date.today()).isoformat()
