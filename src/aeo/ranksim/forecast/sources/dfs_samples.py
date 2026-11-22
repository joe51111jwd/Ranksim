"""Streaming parsers for DataForSEO's free sample databases and the paid Labs historical_serps response.

What is REAL here: every row is copied from a DataForSEO file or response. The
positions are Google organic ranks that DataForSEO recorded on the date in
`snapshot_time`. Keyword volume, CPC, competition, keyword difficulty, intent,
ETV and the WHOIS metrics are DataForSEO's own estimates. They are REAL in the
sense that they are what the vendor published. They are not measured by us.
Nothing in this module is PREDICTED.

Files read (all gzip, one JSON record per line, layouts confirmed by opening the
real files on 2026-11-22):

- `google_history/serp_advanced/<CC>_<lang>.json.gz`: one keyword per line with
  `serp_info_history`, a list of dated result pages. Each page has `check_url`,
  `items_count`, `keyword_difficulty`, `se_results_count`, `time_update`,
  `previous_updated_time`, `item_types` and `serp` (the items).
- `google/serp_advanced/<CC>_<lang>.json.gz`: the same record with one page in `serp_info`.
- `google/keywords/<CC>_<lang>.json.gz`: `keyword_info` (volume, cpc, competition,
  monthly `history` keyed YYYYMM), `extra.keyword_difficulty`, `search_intent_info`.
- `google_history/keywords/<CC>_<lang>.json.gz`: `keyword_info_history`, one
  `keyword_info` per month key YYYYMM.
- `whois/full/data.json.gz`: one domain per line. Only whitelisted fields are
  copied (dates, status codes, ranking counts, backlink counts). The file has no
  registrant or contact fields, and none would be copied if it had.

The paid endpoint `dataforseo_labs/google/historical_serps/live` returns the same
organic item fields, with the rank change fields nested under `rank_changes` and
the page date in `datetime`. `iter_labs_historical_serps` yields the same rows,
so the paid pull needs no new code.

Conventions: `rank` is `rank_group` (organic rank). `previous_rank_absolute` of 0
is not a rank (ranks start at 1), so it is stored as None. A missing value stays
None. Rows for `config.EXCLUDED_SITES` are dropped here and again at load.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from ...site_history import site_key
from ..asof import parse_utc
from ..config import EXCLUDED_SITES

SAMPLE_BASE_URL = "https://data.dataforseo.com/examples/databases/"
LOCALES_URL = "https://cdn.dataforseo.com/v3/locations/locations_and_languages_databases_2026_11_02.csv"

DATASET_HISTORY = "dfs_history"
DATASET_SNAPSHOT = "dfs_serp"
DATASET_LABS = "dfs_labs_historical"

# Google served altered result pages to scrapers from about 20 September to mid
# October 2026 (DataForSEO blog 16.11.2026; docs/plan/PLAN_AUDIT.md E3). Pages
# dated in this window are flagged and left out of training.
POISON_WINDOW_START = date(2026, 9, 20)
POISON_WINDOW_END = date(2026, 10, 15)

LABS_OK_STATUS = 20000

PANEL_ROW_FIELDS: tuple[str, ...] = (
    "dataset",
    "locale",
    "location_code",
    "language",
    "keyword",
    "keyword_id",
    "snapshot_time",
    "snapshot_date",
    "rank",
    "rank_absolute",
    "domain",
    "site",
    "url",
    "title",
    "description",
    "is_new",
    "previous_rank_absolute",
    "main_domain_rank",
    "page_rank",
    "etv",
    "kd",
    "search_volume",
    "intent",
    "items_count",
    "se_results_count",
    "poison_window",
)

KEYWORD_FIELDS: tuple[str, ...] = (
    "dataset",
    "locale",
    "location_code",
    "language",
    "keyword",
    "keyword_id",
    "snapshot_month",
    "time_update",
    "search_volume",
    "cpc",
    "competition",
    "competition_level",
    "low_top_of_page_bid",
    "high_top_of_page_bid",
    "kd",
    "intent",
    "intent_updated",
    "detected_language",
    "categories",
    "monthly_volume",
)

WHOIS_POSITION_KEYS: tuple[str, ...] = (
    "pos_1",
    "pos_2_3",
    "pos_4_10",
    "pos_11_20",
    "pos_21_30",
    "pos_31_40",
    "pos_41_50",
    "pos_51_60",
    "pos_61_70",
    "pos_71_80",
    "pos_81_90",
    "pos_91_100",
)
WHOIS_BACKLINK_KEYS: tuple[str, ...] = (
    "dofollow",
    "backlinks",
    "referring_pages",
    "referring_domains",
    "referring_main_domains",
)
WHOIS_FIELDS: tuple[str, ...] = (
    ("domain", "site", "tld", "registered", "created_datetime", "changed_datetime", "expiration_datetime")
    + ("updated_datetime", "first_seen", "epp_status_codes")
    + tuple(f"organic_{key}" for key in WHOIS_POSITION_KEYS)
    + ("organic_etv", "organic_estimated_paid_traffic_cost")
    + tuple(f"paid_{key}" for key in WHOIS_POSITION_KEYS)
    + ("paid_etv", "metrics_time_update", "backlinks_time_update")
    + tuple(f"bl_{key}" for key in WHOIS_BACKLINK_KEYS)
)


@dataclass
class ParseStats:
    """What a parser saw and what it dropped. Every drop has a reason."""

    path: str = ""
    records: int = 0
    bad_lines: int = 0
    records_without_pages: int = 0
    snapshots: int = 0
    snapshots_without_time: int = 0
    organic_items: int = 0
    rows: int = 0
    dropped_no_rank: int = 0
    dropped_no_site: int = 0
    dropped_excluded_site: int = 0
    dropped_duplicate: int = 0
    failed_tasks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def keyword_id(locale: str, keyword: str) -> str:
    """First 16 hex characters of sha256 of `locale|keyword` (spec section 8). The keyword is used as given."""
    return hashlib.sha256(f"{locale}|{keyword}".encode("utf-8")).hexdigest()[:16]


def locale_from_path(path: str | Path) -> str:
    """`US_en` from `.../US_en.json.gz`. A file name that is not CC_lang is refused."""
    name = Path(path).name
    stem = name.split(".")[0]
    parts = stem.split("_")
    if len(parts) != 2 or len(parts[0]) != 2 or not parts[0].isupper() or not parts[1]:
        raise ValueError(f"cannot read a locale from {name!r}: expected CC_lang.json.gz")
    return stem


def sample_url(path: str | Path, root: str | Path) -> str:
    """The public URL a sample file was fetched from, rebuilt from its path under the download root."""
    rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    return SAMPLE_BASE_URL + rel


def file_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def iter_jsonl(path: str | Path, stats: ParseStats | None = None) -> Iterator[dict[str, Any]]:
    """One dict per non-empty line of a gzip (or plain) JSON-lines file. Lines that do not parse are counted, not raised."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                if stats is not None:
                    stats.bad_lines += 1
                continue
            if not isinstance(record, dict):
                if stats is not None:
                    stats.bad_lines += 1
                continue
            if stats is not None:
                stats.records += 1
            yield record


# ---------------------------------------------------------------------------
# small coercions: a value that is not what it claims to be becomes None, never 0


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    try:
        number = float(str(value))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def _rank(value: Any) -> int | None:
    number = _int(value)
    return number if number is not None and number >= 1 else None


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_utc(str(value))
    except ValueError:
        return None


def in_poison_window(day: date) -> bool:
    """True when a result page dated `day` falls in the altered-pages window."""
    return POISON_WINDOW_START <= day <= POISON_WINDOW_END


def _month_key(day: date) -> str:
    return f"{day.year:04d}{day.month:02d}"


def volume_known_at(keyword_info: Mapping[str, Any] | None, day: date) -> int | None:
    """The monthly volume of the last full month before `day`'s month, from `keyword_info.history`.

    The headline `search_volume` of a sample file is dated by its `time_update`
    (2026-04 in the samples) and would leak into older pages, so the panel takes
    the dated monthly value instead. Google publishes a month's volume some days
    after the month ends, so for a page taken early in a month this value may
    have been published a few days after the page. A missing month stays None.
    """
    if not keyword_info:
        return None
    history = keyword_info.get("history")
    if not isinstance(history, Mapping):
        return None
    target = _month_key(day)
    earlier = sorted(key for key in history if str(key).isdigit() and len(str(key)) == 6 and str(key) < target)
    if not earlier:
        return None
    return _int(history.get(earlier[-1]))


def site_of_item(item: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """(domain, site) of a result item. site = site_key(main_domain or domain), falling back to the URL host."""
    domain = _str(item.get("domain"))
    base = _str(item.get("main_domain")) or domain or _str(item.get("url"))
    site = site_key(base) if base else ""
    return domain, (site or None)


def organic_row(
    item: Mapping[str, Any],
    *,
    dataset: str,
    locale: str,
    location_code: int | None,
    language: str | None,
    keyword: str,
    snapshot_time: datetime,
    kd: int | float | None,
    search_volume: int | None,
    intent: str | None,
    items_count: int | None,
    se_results_count: int | None,
    stats: ParseStats | None = None,
) -> dict[str, Any] | None:
    """One panel row from one organic item, or None when the item cannot be placed (counted in `stats`)."""
    rank = _rank(item.get("rank_group"))
    if rank is None:
        if stats is not None:
            stats.dropped_no_rank += 1
        return None
    domain, site = site_of_item(item)
    if site is None:
        if stats is not None:
            stats.dropped_no_site += 1
        return None
    if site in EXCLUDED_SITES:
        if stats is not None:
            stats.dropped_excluded_site += 1
        return None
    changes = item.get("rank_changes") if isinstance(item.get("rank_changes"), Mapping) else {}
    is_new = _bool(item["is_new"]) if "is_new" in item else _bool(changes.get("is_new"))
    previous = item["previous_rank_absolute"] if "previous_rank_absolute" in item else changes.get("previous_rank_absolute")
    rank_info = item.get("rank_info") if isinstance(item.get("rank_info"), Mapping) else {}
    day = snapshot_time.date()
    row = {
        "dataset": dataset,
        "locale": locale,
        "location_code": location_code,
        "language": language,
        "keyword": keyword,
        "keyword_id": keyword_id(locale, keyword),
        "snapshot_time": snapshot_time,
        "snapshot_date": day,
        "rank": rank,
        "rank_absolute": _rank(item.get("rank_absolute")),
        "domain": domain,
        "site": site,
        "url": _str(item.get("url")),
        "title": _str(item.get("title")),
        "description": _str(item.get("description")),
        "is_new": is_new,
        "previous_rank_absolute": _rank(previous),
        "main_domain_rank": _int(rank_info.get("main_domain_rank")),
        "page_rank": _int(rank_info.get("page_rank")),
        "etv": _float(item.get("etv")),
        "kd": _float(kd),
        "search_volume": search_volume,
        "intent": intent,
        "items_count": items_count,
        "se_results_count": se_results_count,
        "poison_window": in_poison_window(day),
    }
    if stats is not None:
        stats.rows += 1
    return row


def _page_rows(
    page: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    dataset: str,
    locale: str,
    stats: ParseStats,
) -> Iterator[dict[str, Any]]:
    """Rows of one dated result page of a sample record (history or single snapshot)."""
    moment = _time(page.get("time_update"))
    if moment is None:
        stats.snapshots_without_time += 1
        return
    stats.snapshots += 1
    keyword = str(record.get("keyword") or "")
    keyword_info = record.get("keyword_info") if isinstance(record.get("keyword_info"), Mapping) else None
    intent_info = record.get("search_intent_info") if isinstance(record.get("search_intent_info"), Mapping) else {}
    volume = volume_known_at(keyword_info, moment.date())
    seen: set[tuple[int, int | None, str | None]] = set()
    for item in page.get("serp") or []:
        if not isinstance(item, Mapping) or item.get("type") != "organic":
            continue
        stats.organic_items += 1
        row = organic_row(
            item,
            dataset=dataset,
            locale=locale,
            location_code=_int(record.get("location")),
            language=_str(record.get("language")),
            keyword=keyword,
            snapshot_time=moment,
            kd=page.get("keyword_difficulty"),
            search_volume=volume,
            intent=_str(intent_info.get("main_intent")),
            items_count=_int(page.get("items_count")),
            se_results_count=_int(page.get("se_results_count")),
            stats=stats,
        )
        if row is None:
            continue
        key = (row["rank"], row["rank_absolute"], row["url"])
        if key in seen:  # the same item listed twice on one page
            stats.rows -= 1
            stats.dropped_duplicate += 1
            continue
        seen.add(key)
        yield row


def iter_history_rows(path: str | Path, stats: ParseStats | None = None, *, locale: str | None = None) -> Iterator[dict[str, Any]]:
    """Panel rows of a `google_history/serp_advanced` sample file: every organic item of every dated page."""
    stats = stats if stats is not None else ParseStats()
    stats.path = str(path)
    loc = locale or locale_from_path(path)
    for record in iter_jsonl(path, stats):
        pages = record.get("serp_info_history")
        if not isinstance(pages, list) or not pages:
            stats.records_without_pages += 1
            continue
        for page in pages:
            if isinstance(page, Mapping):
                yield from _page_rows(page, record, dataset=DATASET_HISTORY, locale=loc, stats=stats)


def iter_snapshot_rows(path: str | Path, stats: ParseStats | None = None, *, locale: str | None = None) -> Iterator[dict[str, Any]]:
    """Panel rows of a `google/serp_advanced` sample file: one dated page per keyword, in `serp_info`."""
    stats = stats if stats is not None else ParseStats()
    stats.path = str(path)
    loc = locale or locale_from_path(path)
    for record in iter_jsonl(path, stats):
        page = record.get("serp_info")
        if not isinstance(page, Mapping):
            stats.records_without_pages += 1
            continue
        yield from _page_rows(page, record, dataset=DATASET_SNAPSHOT, locale=loc, stats=stats)


# ---------------------------------------------------------------------------
# locales


@dataclass(frozen=True)
class LocaleInfo:
    locale: str
    location_code: int
    location_name: str
    country_iso_code: str
    language_name: str
    language_code: str


def load_locales(path: str | Path) -> dict[str, LocaleInfo]:
    """The DataForSEO databases locale list (locales.csv) keyed by `CC_lang`."""
    out: dict[str, LocaleInfo] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            code = _int(row.get("location_code"))
            iso = (row.get("country_iso_code") or "").strip()
            lang = (row.get("language_code") or "").strip()
            if code is None or not iso or not lang:
                continue
            key = f"{iso}_{lang}"
            out[key] = LocaleInfo(key, code, row.get("location_name") or "", iso, row.get("language_name") or "", lang)
    return out


def locale_for(location_code: int, language_code: str, locales: Mapping[str, LocaleInfo]) -> str:
    """`CC_lang` for a location code and language. Refuses to guess when the pair is not in the list."""
    for info in locales.values():
        if info.location_code == location_code and info.language_code == language_code:
            return info.locale
    raise ValueError(f"location {location_code} with language {language_code!r} is not in the locale list")


# ---------------------------------------------------------------------------
# the paid Labs historical_serps response


def iter_labs_historical_serps(
    payload: Mapping[str, Any] | str | Path,
    stats: ParseStats | None = None,
    *,
    locale: str | None = None,
    locales: Mapping[str, LocaleInfo] | None = None,
) -> Iterator[dict[str, Any]]:
    """Panel rows from a saved `dataforseo_labs/google/historical_serps/live` response.

    Layout: `tasks[].result[].items[]` is one dated page (`datetime`,
    `items_count`, `se_results_count`, `items[]`). A task whose status is not
    20000 is a failed pull: it is recorded in `stats.failed_tasks` and yields
    nothing. It is never read as an empty result page. The locale is `locale`
    when given, else looked up in `locales`; with neither the call is refused.
    The Labs response has no keyword difficulty and no monthly volume, so `kd`
    and `search_volume` are None.
    """
    stats = stats if stats is not None else ParseStats()
    if isinstance(payload, (str, Path)):
        stats.path = str(payload)
        with open(payload, encoding="utf-8") as handle:
            payload = json.load(handle)
    assert isinstance(payload, Mapping)
    for task in payload.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        if _int(task.get("status_code")) != LABS_OK_STATUS:
            stats.failed_tasks.append(f"{task.get('id')}: {task.get('status_code')} {task.get('status_message')}")
            continue
        for result in task.get("result") or []:
            if not isinstance(result, Mapping):
                continue
            stats.records += 1
            keyword = str(result.get("keyword") or "")
            code = _int(result.get("location_code"))
            language = _str(result.get("language_code"))
            if locale is not None:
                loc = locale
            elif locales is not None and code is not None and language is not None:
                loc = locale_for(code, language, locales)
            else:
                raise ValueError("pass locale= or locales= so the keyword id matches the sample panel")
            pages = result.get("items") or []
            if not pages:
                stats.records_without_pages += 1
            for page in pages:
                if not isinstance(page, Mapping):
                    continue
                moment = _time(page.get("datetime"))
                if moment is None:
                    stats.snapshots_without_time += 1
                    continue
                stats.snapshots += 1
                seen: set[tuple[int, int | None, str | None]] = set()
                for item in page.get("items") or []:
                    if not isinstance(item, Mapping) or item.get("type") != "organic":
                        continue
                    stats.organic_items += 1
                    row = organic_row(
                        item,
                        dataset=DATASET_LABS,
                        locale=loc,
                        location_code=code,
                        language=language,
                        keyword=keyword,
                        snapshot_time=moment,
                        kd=None,
                        search_volume=None,
                        intent=None,
                        items_count=_int(page.get("items_count")),
                        se_results_count=_int(page.get("se_results_count")),
                        stats=stats,
                    )
                    if row is None:
                        continue
                    key = (row["rank"], row["rank_absolute"], row["url"])
                    if key in seen:
                        stats.rows -= 1
                        stats.dropped_duplicate += 1
                        continue
                    seen.add(key)
                    yield row


# ---------------------------------------------------------------------------
# keyword tables


def _monthly(history: Any) -> str | None:
    """Monthly volumes as JSON text {"YYYY-MM": volume}, months with no value kept as null."""
    if not isinstance(history, Mapping):
        return None
    months = {}
    for key in sorted(history):
        text = str(key)
        if len(text) == 6 and text.isdigit():
            months[f"{text[:4]}-{text[4:]}"] = _int(history[key])
    return json.dumps(months, sort_keys=True) if months else None


def _keyword_row(
    info: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    dataset: str,
    locale: str,
    snapshot_month: str | None,
) -> dict[str, Any]:
    extra = record.get("extra") if isinstance(record.get("extra"), Mapping) else {}
    intent = record.get("search_intent_info") if isinstance(record.get("search_intent_info"), Mapping) else {}
    keyword = str(record.get("keyword") or "")
    updated = _time(info.get("time_update"))
    month = snapshot_month or (updated.strftime("%Y-%m") if updated else None)
    categories = info.get("categories")
    return {
        "dataset": dataset,
        "locale": locale,
        "location_code": _int(record.get("location")),
        "language": _str(record.get("language")),
        "keyword": keyword,
        "keyword_id": keyword_id(locale, keyword),
        "snapshot_month": month,
        "time_update": updated,
        "search_volume": _int(info.get("search_volume")),
        "cpc": _float(info.get("cpc")),
        "competition": _float(info.get("competition")),
        "competition_level": _str(info.get("competition_level")),
        "low_top_of_page_bid": _float(info.get("low_top_of_page_bid")),
        "high_top_of_page_bid": _float(info.get("high_top_of_page_bid")),
        "kd": _float(extra.get("keyword_difficulty")),
        "intent": _str(intent.get("main_intent")),
        "intent_updated": _time(intent.get("last_updated_time")),
        "detected_language": _str(extra.get("detected_language")),
        "categories": json.dumps(categories) if isinstance(categories, list) else None,
        "monthly_volume": _monthly(info.get("history")),
    }


def iter_keyword_rows(path: str | Path, dataset: str, stats: ParseStats | None = None, *, locale: str | None = None) -> Iterator[dict[str, Any]]:
    """Keyword-table rows from any sample file that carries keyword data.

    `keyword_info_history` (google_history/keywords) gives one row per month key.
    A plain `keyword_info` (google/keywords and the SERP files) gives one row,
    dated by its `time_update`. `kd` is the record's `extra.keyword_difficulty`,
    which the file does not date: treat it as known at `time_update`.
    """
    stats = stats if stats is not None else ParseStats()
    stats.path = str(path)
    loc = locale or locale_from_path(path)
    for record in iter_jsonl(path, stats):
        history = record.get("keyword_info_history")
        if isinstance(history, Mapping) and history:
            for key in sorted(history):
                info = history[key]
                text = str(key)
                if isinstance(info, Mapping):
                    month = f"{text[:4]}-{text[4:]}" if len(text) == 6 and text.isdigit() else None
                    stats.rows += 1
                    yield _keyword_row(info, record, dataset=dataset, locale=loc, snapshot_month=month)
            continue
        info = record.get("keyword_info")
        if isinstance(info, Mapping):
            stats.rows += 1
            yield _keyword_row(info, record, dataset=dataset, locale=loc, snapshot_month=None)
        else:
            stats.records_without_pages += 1


# ---------------------------------------------------------------------------
# WHOIS plus metrics


def iter_whois_rows(path: str | Path, stats: ParseStats | None = None) -> Iterator[dict[str, Any]]:
    """WHOIS-table rows. Only whitelisted, non-personal fields are copied. Metrics are a 2026-03/04 snapshot."""
    stats = stats if stats is not None else ParseStats()
    stats.path = str(path)
    for record in iter_jsonl(path, stats):
        domain = _str(record.get("domain"))
        site = site_key(domain) if domain else ""
        if not site:
            stats.dropped_no_site += 1
            continue
        if site in EXCLUDED_SITES:
            stats.dropped_excluded_site += 1
            continue
        metrics = record.get("metrics") if isinstance(record.get("metrics"), Mapping) else {}
        organic = metrics.get("organic") if isinstance(metrics.get("organic"), Mapping) else {}
        paid = metrics.get("paid") if isinstance(metrics.get("paid"), Mapping) else {}
        links = record.get("backlinks_info") if isinstance(record.get("backlinks_info"), Mapping) else {}
        codes = record.get("epp_status_codes")
        row: dict[str, Any] = {
            "domain": domain,
            "site": site,
            "tld": _str(record.get("tld")),
            "registered": _bool(record.get("registered")),
            "created_datetime": _time(record.get("created_datetime")),
            "changed_datetime": _time(record.get("changed_datetime")),
            "expiration_datetime": _time(record.get("expiration_datetime")),
            "updated_datetime": _time(record.get("updated_datetime")),
            "first_seen": _time(record.get("first_seen")),
            "epp_status_codes": json.dumps(codes) if isinstance(codes, list) else None,
        }
        for key in WHOIS_POSITION_KEYS:
            row[f"organic_{key}"] = _int(organic.get(key))
        row["organic_etv"] = _float(organic.get("etv"))
        row["organic_estimated_paid_traffic_cost"] = _float(organic.get("estimated_paid_traffic_cost"))
        for key in WHOIS_POSITION_KEYS:
            row[f"paid_{key}"] = _int(paid.get(key))
        row["paid_etv"] = _float(paid.get("etv"))
        row["metrics_time_update"] = _time(metrics.get("metrics_time_update"))
        row["backlinks_time_update"] = _time(links.get("time_update"))
        for key in WHOIS_BACKLINK_KEYS:
            row[f"bl_{key}"] = _int(links.get(key))
        stats.rows += 1
        yield row


def read_text_sample(path: str | Path, limit: int = 3) -> list[dict[str, Any]]:
    """The first `limit` records of a JSON-lines file, for inspection."""
    out = []
    for record in iter_jsonl(path):
        out.append(record)
        if len(out) >= limit:
            break
    return out


def rows_to_records(rows: Iterable[Mapping[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Rows reduced to `fields`, in order. A missing field is None."""
    return [{name: row.get(name) for name in fields} for row in rows]


__all__ = [
    "DATASET_HISTORY",
    "DATASET_LABS",
    "DATASET_SNAPSHOT",
    "KEYWORD_FIELDS",
    "LocaleInfo",
    "PANEL_ROW_FIELDS",
    "POISON_WINDOW_END",
    "POISON_WINDOW_START",
    "ParseStats",
    "WHOIS_FIELDS",
    "file_sha256",
    "in_poison_window",
    "iter_history_rows",
    "iter_jsonl",
    "iter_keyword_rows",
    "iter_labs_historical_serps",
    "iter_snapshot_rows",
    "iter_whois_rows",
    "keyword_id",
    "load_locales",
    "locale_for",
    "locale_from_path",
    "sample_url",
    "volume_known_at",
]
