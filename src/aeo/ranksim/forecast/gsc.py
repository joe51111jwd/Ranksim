"""Search Console import: Google's first reaction to a new site (data source 10, audit F20).

What is REAL here: the clicks, impressions, CTR and average position Search
Console reports for the site, as exported or as returned by the API, with the
date Google gives them and the time we fetched them. What is PREDICTED here:
nothing. `early_signals` only summarises those numbers for the early-reading
updater (part 7).

What Search Console position is, and is not:

- It is an impression-weighted average over every place, device and query where
  the site was shown. For a page or site total it is the topmost result of the site.
- It is not comparable to the clean reader, which reads one pinned city on desktop.
- Queries issued by few people are anonymised and have no row, so small sites
  lose most long-tail queries. A missing query means "not shown in Search Console",
  never "not ranked".
- It is never used as a graded outcome. Outcomes come only from the clean reader.

Dates: `data_date` is the calendar date Search Console reports. Google states that
the API's dates are in Pacific time. `fetched_at` is the UTC time we read it.
The newest PRELIMINARY_DAYS days of any daily export are marked preliminary,
because Google keeps revising its freshest data. `early_signals` uses only data
dated on or before `as_of - PRELIMINARY_DAYS`.

Supported inputs:

- The performance export from the Search Console UI (a zip or a folder):
  Queries.csv ("Top queries", Clicks, Impressions, CTR, Position), Pages.csv
  ("Top pages", ...), and the date table (Dates.csv, or Chart.csv in newer exports:
  Date, Clicks, Impressions, CTR, Position). CTR is text like 3.77%.
- A per-day table: date plus any of query, page, country, device, then clicks,
  impressions, ctr and position (or avg_position). CTR as a fraction, or a
  ctr_percent column. Extra columns are ignored.
- searchanalytics.query API JSON rows: keys, clicks, impressions, ctr, position.
- An indexed-pages table: date, indexed, not_indexed. Or the URL Inspection
  coverage summary: date, type, coverageState, count.

Rows for excluded sites (config.EXCLUDED_SITES) are dropped by every loader and counted.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..site_history import site_key
from .asof import LeakError, as_date, parse_utc, utc_iso
from .config import EXCLUDED_SITES

PRELIMINARY_DAYS = 2
BOM = "\ufeff"
API_ROOT = "https://www.googleapis.com/webmasters/v3"
API_ROW_LIMIT = 25000
API_MAX_PAGES = 400
API_RETRIES = 2
API_RETRY_SECONDS = 2.0
API_TIMEOUT_SECONDS = 30.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
API_DIMENSIONS = frozenset({"date", "query", "page", "country", "device", "searchAppearance"})
DATA_STATES = ("final", "all")
DAYS_PER_WEEK = 7

SOURCE_UI_QUERIES = "ui_queries"
SOURCE_UI_PAGES = "ui_pages"
SOURCE_UI_DATES = "ui_dates"
SOURCE_DAILY = "daily_table"
SOURCE_API = "api"


class GscError(RuntimeError):
    """A Search Console request failed. The message never holds the access token."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GscParseError(ValueError):
    """A Search Console file or payload does not have the expected shape. The message names the row and column."""


@dataclass(frozen=True)
class GscRow:
    """One Search Console row. REAL.

    A daily row has `period_start == data_date`. A row from a date-range table
    (Queries.csv, Pages.csv, or the API without the date dimension) covers
    `period_start` to `data_date`. `position` and `ctr` are None when there were
    no impressions. They are never 0.
    """

    data_date: date
    query: str | None
    page: str | None
    clicks: int
    impressions: int
    ctr: float | None
    position: float | None
    fetched_at: str | None
    is_preliminary: bool
    period_start: date
    source: str
    country: str | None = None
    device: str | None = None

    @property
    def is_daily(self) -> bool:
        return self.period_start == self.data_date


@dataclass(frozen=True)
class GscTable:
    """Parsed rows and what was dropped on the way."""

    rows: tuple[GscRow, ...]
    source: str
    excluded_rows: int = 0
    notes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- cell parsing


def _read_text(source: str | Path | bytes) -> str:
    """Bytes are file content. A Path, or text with no line break, is a file path. Other text is the content."""
    if isinstance(source, bytes):
        return source.decode("utf-8-sig")
    if isinstance(source, Path) or "\n" not in source:
        return Path(source).read_text(encoding="utf-8-sig")
    return source.removeprefix(BOM)


def _int_cell(text: str, where: str) -> int:
    cleaned = str(text).strip().replace(",", "")
    if cleaned == "":
        raise GscParseError(f"{where} is empty")
    try:
        number = float(cleaned)
    except ValueError:
        raise GscParseError(f"{where} is not a number: {text!r}") from None
    if not math.isfinite(number) or not number.is_integer() or number < 0:
        raise GscParseError(f"{where} must be a whole number of 0 or more, not {text!r}")
    return int(number)


def _float_cell(text: str, where: str) -> float | None:
    cleaned = str(text).strip()
    if cleaned == "":
        return None
    try:
        number = float(cleaned)
    except ValueError:
        raise GscParseError(f"{where} is not a number: {text!r}") from None
    if not math.isfinite(number):
        raise GscParseError(f"{where} is not a finite number: {text!r}")
    return number


def _ctr_cell(text: str, where: str, *, percent: bool) -> float | None:
    cleaned = str(text).strip()
    if cleaned.endswith("%"):
        cleaned, percent = cleaned[:-1].strip(), True
    value = _float_cell(cleaned, where)
    if value is None:
        return None
    if percent:
        value /= 100.0
    if not 0.0 <= value <= 1.0:
        raise GscParseError(f"{where} must be a click rate from 0 to 1 (or 0% to 100%), not {text!r}")
    return value


def _date_cell(text: str, where: str) -> date:
    cleaned = str(text).strip()
    try:
        if len(cleaned) != 10:
            raise ValueError
        return date.fromisoformat(cleaned)
    except ValueError:
        raise GscParseError(f"{where} is not a date like 2026-11-30: {text!r}") from None


def _finish_metrics(
    clicks: int, impressions: int, ctr: float | None, position: float | None, where: str
) -> tuple[float | None, float | None]:
    """Search Console prints position 0 and CTR 0 for days with no impressions. Those are None."""
    if clicks > impressions:
        raise GscParseError(f"{where} has {clicks} clicks but only {impressions} impressions")
    if impressions == 0:
        return None, None
    if position is not None and position < 1.0:
        raise GscParseError(f"{where} has position {position} with {impressions} impressions. Positions start at 1.")
    return ctr, position


def _excluded(page: str | None) -> bool:
    return page is not None and site_key(page) in EXCLUDED_SITES


def _mark_preliminary(rows: list[GscRow]) -> list[GscRow]:
    """Mark daily rows in the newest PRELIMINARY_DAYS distinct-calendar days of the table as preliminary."""
    daily_dates = [row.data_date for row in rows if row.is_daily]
    if not daily_dates:
        return rows
    newest = max(daily_dates)
    first_preliminary = newest - timedelta(days=PRELIMINARY_DAYS - 1)
    return [
        replace(row, is_preliminary=True) if row.is_daily and row.data_date >= first_preliminary else row
        for row in rows
    ]


# --------------------------------------------------------------------------- UI export


_HEADER_ALIASES: dict[str, str] = {
    "date": "date",
    "day": "date",
    "query": "query",
    "queries": "query",
    "top queries": "query",
    "page": "page",
    "pages": "page",
    "top pages": "page",
    "url": "page",
    "page_url": "page",
    "country": "country",
    "countries": "country",
    "device": "device",
    "devices": "device",
    "clicks": "clicks",
    "impressions": "impressions",
    "ctr": "ctr",
    "ctr_percent": "ctr_percent",
    "position": "position",
    "avg_position": "position",
    "average position": "position",
}


def _header_map(header: Sequence[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, name in enumerate(header):
        key = _HEADER_ALIASES.get(str(name).strip().casefold())
        if key is None:
            continue
        if key in mapping:
            raise GscParseError(f"the header has two columns for {key}: {list(header)}")
        mapping[key] = index
    if "ctr" in mapping and "ctr_percent" in mapping:
        raise GscParseError("the header has both ctr and ctr_percent")
    for needed in ("clicks", "impressions"):
        if needed not in mapping:
            raise GscParseError(f"the header has no {needed} column: {list(header)}")
    return mapping


def _parse_table(
    text: str,
    *,
    source: str,
    fetched_at: str | None,
    period: tuple[date, date] | None,
    label: str,
) -> GscTable:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise GscParseError(f"{label} is empty") from None
    cols = _header_map(header)
    if "date" not in cols and period is None:
        raise GscParseError(f"{label} has no date column, so its period must be given")
    rows: list[GscRow] = []
    excluded = 0
    for line_no, cells in enumerate(reader, start=2):
        if not cells or all(not cell.strip() for cell in cells):
            continue
        if len(cells) < len(header):
            raise GscParseError(f"{label} line {line_no} has {len(cells)} cells, the header has {len(header)}")

        row_cells = tuple(cells)

        def cell(key: str, row_cells: tuple[str, ...] = row_cells) -> str | None:
            return row_cells[cols[key]] if key in cols else None

        where = f"{label} line {line_no}"
        clicks = _int_cell(cell("clicks") or "", f"{where} clicks")
        impressions = _int_cell(cell("impressions") or "", f"{where} impressions")
        if "ctr" in cols:
            ctr = _ctr_cell(cell("ctr") or "", f"{where} ctr", percent=False)
        elif "ctr_percent" in cols:
            ctr = _ctr_cell(cell("ctr_percent") or "", f"{where} ctr_percent", percent=True)
        else:
            ctr = None
        position = _float_cell(cell("position") or "", f"{where} position") if "position" in cols else None
        ctr, position = _finish_metrics(clicks, impressions, ctr, position, where)
        query = ((cell("query") or "").strip() or None) if "query" in cols else None
        page = ((cell("page") or "").strip() or None) if "page" in cols else None
        if _excluded(page):
            excluded += 1
            continue
        if "date" in cols:
            day = _date_cell(cell("date") or "", f"{where} date")
            start = day
        else:
            assert period is not None
            start, day = period
        rows.append(
            GscRow(
                data_date=day,
                query=query,
                page=page,
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                position=position,
                fetched_at=fetched_at,
                is_preliminary=False,
                period_start=start,
                source=source,
                country=((cell("country") or "").strip() or None) if "country" in cols else None,
                device=((cell("device") or "").strip().lower() or None) if "device" in cols else None,
            )
        )
    notes = (f"{excluded} rows for excluded sites were dropped",) if excluded else ()
    return GscTable(rows=tuple(_mark_preliminary(rows)), source=source, excluded_rows=excluded, notes=notes)


def _period(period_start: date | str | None, period_end: date | str | None) -> tuple[date, date] | None:
    if period_start is None and period_end is None:
        return None
    if period_start is None or period_end is None:
        raise ValueError("give both period_start and period_end, or neither")
    start, end = as_date(period_start), as_date(period_end)
    if start > end:
        raise ValueError(f"period_start {start} is after period_end {end}")
    return start, end


def _fetched(fetched_at: datetime | str | None) -> str | None:
    if fetched_at is None:
        return None
    if isinstance(fetched_at, datetime):
        return utc_iso(fetched_at)
    return utc_iso(parse_utc(fetched_at))


def parse_daily_table(source: str | Path | bytes, *, fetched_at: datetime | str | None = None) -> GscTable:
    """A per-day table with a date column: Dates.csv, Chart.csv, or a per-day query or page export."""
    return _parse_table(_read_text(source), source=SOURCE_DAILY, fetched_at=_fetched(fetched_at), period=None, label="daily table")


def parse_range_table(
    source: str | Path | bytes,
    *,
    period_start: date | str,
    period_end: date | str,
    fetched_at: datetime | str | None = None,
    final: bool = False,
) -> GscTable:
    """Queries.csv or Pages.csv: totals over one date range, with no date column.

    Each row is dated `period_end`, the newest day it includes, and keeps
    `period_start`. The range includes the export's newest days, so its rows are
    preliminary unless `final=True` says the range ended at least PRELIMINARY_DAYS
    before the export was made.
    """
    period = _period(period_start, period_end)
    assert period is not None
    text = _read_text(source)
    try:
        header = next(csv.reader(io.StringIO(text)))
    except StopIteration:
        raise GscParseError("the range table is empty") from None
    cols = _header_map(header)
    if "date" in cols:
        raise GscParseError("this table has a date column. Use parse_daily_table for it.")
    kind = SOURCE_UI_PAGES if ("page" in cols and "query" not in cols) else SOURCE_UI_QUERIES
    table = _parse_table(text, source=kind, fetched_at=_fetched(fetched_at), period=period, label=kind)
    rows = tuple(replace(row, is_preliminary=not final) for row in table.rows)
    return replace(table, rows=rows)


@dataclass(frozen=True)
class UiExport:
    """The tables of one Search Console UI performance export."""

    dates: GscTable
    queries: GscTable | None
    pages: GscTable | None
    period_start: date
    period_end: date
    period_from: str

    def all_rows(self) -> list[GscRow]:
        rows = list(self.dates.rows)
        for table in (self.queries, self.pages):
            if table is not None:
                rows.extend(table.rows)
        return rows


def _ui_members(path: Path) -> dict[str, str]:
    """Basename (lower case) to text for the CSV files of a UI export zip or folder."""
    found: dict[str, str] = {}
    if path.is_dir():
        for child in sorted(path.iterdir()):
            if child.is_file() and child.suffix.lower() == ".csv":
                found[child.name.casefold()] = child.read_text(encoding="utf-8-sig")
        return found
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            if not name.endswith(".csv"):
                continue
            if name in found:
                raise GscParseError(f"the export has two files named {name}")
            found[name] = archive.read(info).decode("utf-8-sig")
    return found


def parse_ui_export(
    path: str | Path,
    *,
    period_start: date | str | None = None,
    period_end: date | str | None = None,
    fetched_at: datetime | str | None = None,
    final: bool = False,
) -> UiExport:
    """Read a Search Console UI performance export (zip or unpacked folder).

    The date table is Dates.csv or Chart.csv. Queries.csv and Pages.csv cover the
    export's whole date range. When the period is not given it is taken from the
    first and last day of the date table, which spans the same filter.
    """
    members = _ui_members(Path(path))
    date_name = "dates.csv" if "dates.csv" in members else "chart.csv" if "chart.csv" in members else None
    if date_name is None:
        raise GscParseError(f"{path} has no Dates.csv or Chart.csv")
    dates = _parse_table(members[date_name], source=SOURCE_UI_DATES, fetched_at=_fetched(fetched_at), period=None, label=date_name)
    period = _period(period_start, period_end)
    period_from = "given"
    if period is None:
        if not dates.rows:
            raise GscParseError(f"{date_name} has no rows, so the export's period is unknown. Give it.")
        period = (min(row.data_date for row in dates.rows), max(row.data_date for row in dates.rows))
        period_from = date_name
    tables: dict[str, GscTable | None] = {"queries.csv": None, "pages.csv": None}
    for name in tables:
        if name in members:
            tables[name] = parse_range_table(
                members[name].encode("utf-8"), period_start=period[0], period_end=period[1], fetched_at=fetched_at, final=final
            )
    return UiExport(
        dates=dates,
        queries=tables["queries.csv"],
        pages=tables["pages.csv"],
        period_start=period[0],
        period_end=period[1],
        period_from=period_from,
    )


# --------------------------------------------------------------------------- API rows


def parse_api_rows(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    dimensions: Sequence[str],
    fetched_at: datetime | str | None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    mark_preliminary: bool = True,
) -> GscTable:
    """searchanalytics.query rows to GscRow.

    `payload` is one response body (with "rows") or a list of row objects. `keys`
    follow `dimensions` in order. Without the date dimension the rows cover
    `start_date` to `end_date`, which must be given. A response with no "rows" key
    is Google saying there is no data for the request: an empty table, not an error.
    """
    dims = list(dimensions)
    unknown = sorted(set(dims) - API_DIMENSIONS)
    if unknown:
        raise GscParseError(f"unknown dimensions {unknown}")
    if isinstance(payload, Mapping):
        raw_rows = payload.get("rows", [])
    else:
        raw_rows = list(payload)
    if not isinstance(raw_rows, list):
        raise GscParseError("rows must be a list")
    period = _period(start_date, end_date)
    if "date" not in dims and period is None:
        raise GscParseError("rows without the date dimension need start_date and end_date")
    stamp = _fetched(fetched_at)
    rows: list[GscRow] = []
    excluded = 0
    for index, raw in enumerate(raw_rows):
        where = f"api row {index}"
        if not isinstance(raw, Mapping):
            raise GscParseError(f"{where} is not an object")
        keys = raw.get("keys", [])
        if not isinstance(keys, list) or len(keys) != len(dims):
            raise GscParseError(f"{where} has keys {keys!r} for dimensions {dims}")
        values = dict(zip(dims, (str(key) for key in keys)))
        for name in ("clicks", "impressions"):
            if name not in raw:
                raise GscParseError(f"{where} has no {name}")
        clicks = _int_cell(str(raw["clicks"]), f"{where} clicks")
        impressions = _int_cell(str(raw["impressions"]), f"{where} impressions")
        ctr = _ctr_cell(str(raw["ctr"]), f"{where} ctr", percent=False) if raw.get("ctr") is not None else None
        position = _float_cell(str(raw["position"]), f"{where} position") if raw.get("position") is not None else None
        ctr, position = _finish_metrics(clicks, impressions, ctr, position, where)
        page = values.get("page")
        if _excluded(page):
            excluded += 1
            continue
        if "date" in values:
            day = _date_cell(values["date"], f"{where} date")
            start = day
        else:
            assert period is not None
            start, day = period
        rows.append(
            GscRow(
                data_date=day,
                query=values.get("query"),
                page=page,
                clicks=clicks,
                impressions=impressions,
                ctr=ctr,
                position=position,
                fetched_at=stamp,
                is_preliminary=False,
                period_start=start,
                source=SOURCE_API,
                country=values.get("country"),
                device=values["device"].lower() if "device" in values else None,
            )
        )
    if mark_preliminary:
        rows = _mark_preliminary(rows)
    notes = (f"{excluded} rows for excluded sites were dropped",) if excluded else ()
    return GscTable(rows=tuple(rows), source=SOURCE_API, excluded_rows=excluded, notes=notes)


# --------------------------------------------------------------------------- API client


@dataclass(frozen=True)
class GscSite:
    """One Search Console property the token can see."""

    site_url: str
    permission_level: str


@dataclass(frozen=True)
class GscQueryResult:
    """All pages of one searchanalytics.query request."""

    site_url: str
    start_date: date
    end_date: date
    dimensions: tuple[str, ...]
    data_state: str
    table: GscTable
    pages_fetched: int
    first_incomplete_date: date | None
    fetched_at: str

    @property
    def rows(self) -> tuple[GscRow, ...]:
        return self.table.rows


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GscClient:
    """A read-only, sync client for the Search Console API: the sites list and searchanalytics.query.

    The token is sent only in the Authorization header. It is never logged, never
    put in an error message, and hidden from repr. Pass `client=` to inject an
    httpx.Client (tests use httpx.MockTransport). Retries on 429 and 5xx use the
    injected `sleep`, so tests never wait.
    """

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = API_RETRIES,
        retry_seconds: float = API_RETRY_SECONDS,
        max_pages: int = API_MAX_PAGES,
    ) -> None:
        if not isinstance(access_token, str) or not access_token.strip():
            raise ValueError("an access token is required")
        self._token = access_token.strip()
        self._own_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=API_TIMEOUT_SECONDS)
        self._clock = clock
        self._sleep = sleep
        self._retries = max(0, int(retries))
        self._retry_seconds = float(retry_seconds)
        self._max_pages = int(max_pages)

    def __repr__(self) -> str:
        return "GscClient(access_token=<hidden>)"

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> GscClient:  # noqa: PYI034
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<hidden>")

    def _send(self, method: str, url: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, headers=self._headers(), json=body)
            except httpx.HTTPError as error:
                if attempt < self._retries:
                    attempt += 1
                    self._sleep(self._retry_seconds * attempt)
                    continue
                raise GscError(self._scrub(f"Search Console request failed: {type(error).__name__}: {error}")) from None
            status = response.status_code
            if status in RETRY_STATUSES and attempt < self._retries:
                attempt += 1
                self._sleep(self._retry_seconds * attempt)
                continue
            if status != 200:
                raise GscError(self._scrub(self._error_text(response)), status_code=status)
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                raise GscError("Search Console answered 200 with a body that is not JSON", status_code=status) from None
            if not isinstance(data, dict):
                raise GscError("Search Console answered 200 with JSON that is not an object", status_code=status)
            return data

    @staticmethod
    def _error_text(response: httpx.Response) -> str:
        status = response.status_code
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                detail = str(body["error"].get("message") or "")
        except (json.JSONDecodeError, ValueError):
            detail = response.text[:200]
        meaning = {
            400: "the request was rejected",
            401: "the access token is missing, wrong or expired",
            403: "the token has no access to this property",
            404: "the property was not found",
            429: "the quota was used up",
        }.get(status, "the server failed" if status >= 500 else "unexpected status")
        text = f"Search Console returned {status}: {meaning}."
        return f"{text} {detail[:200]}" if detail else text

    def list_sites(self) -> list[GscSite]:
        """The properties this token can see, sorted by site URL."""
        data = self._send("GET", f"{API_ROOT}/sites")
        entries = data.get("siteEntry", [])
        if not isinstance(entries, list):
            raise GscError("siteEntry is not a list")
        sites = []
        for entry in entries:
            if not isinstance(entry, Mapping) or "siteUrl" not in entry:
                raise GscError("a siteEntry has no siteUrl")
            sites.append(GscSite(site_url=str(entry["siteUrl"]), permission_level=str(entry.get("permissionLevel", ""))))
        return sorted(sites, key=lambda site: site.site_url)

    def query(
        self,
        site_url: str,
        start_date: date | str,
        end_date: date | str,
        *,
        dimensions: Sequence[str] = ("date", "query", "page"),
        search_type: str = "web",
        data_state: str = "final",
        row_limit: int = API_ROW_LIMIT,
    ) -> GscQueryResult:
        """Every row of one searchanalytics.query, paged with startRow until a short page comes back."""
        start, end = as_date(start_date), as_date(end_date)
        if start > end:
            raise ValueError(f"start_date {start} is after end_date {end}")
        if data_state not in DATA_STATES:
            raise ValueError(f"data_state must be one of {DATA_STATES}")
        if not 1 <= int(row_limit) <= API_ROW_LIMIT:
            raise ValueError(f"row_limit must be 1 to {API_ROW_LIMIT}")
        dims = tuple(dimensions)
        unknown = sorted(set(dims) - API_DIMENSIONS)
        if unknown:
            raise ValueError(f"unknown dimensions {unknown}")
        property_key = site_url.removeprefix("sc-domain:")
        if site_key(property_key) in EXCLUDED_SITES:
            raise GscError(f"{site_url} is an excluded site. Its Search Console data is never used.")
        url = f"{API_ROOT}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
        fetched = utc_iso(self._clock())
        raw_rows: list[Mapping[str, Any]] = []
        first_incomplete: date | None = None
        pages = 0
        start_row = 0
        while True:
            if pages >= self._max_pages:
                raise GscError(f"stopped after {pages} pages. The result is incomplete, so nothing is returned.")
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": list(dims),
                "type": search_type,
                "dataState": data_state,
                "rowLimit": int(row_limit),
                "startRow": start_row,
            }
            data = self._send("POST", url, body)
            pages += 1
            page_rows = data.get("rows", [])
            if not isinstance(page_rows, list):
                raise GscError("rows is not a list")
            raw_rows.extend(page_rows)
            metadata = data.get("metadata")
            if isinstance(metadata, Mapping) and metadata.get("first_incomplete_date"):
                first_incomplete = as_date(str(metadata["first_incomplete_date"]))
            if len(page_rows) < int(row_limit):
                break
            start_row += len(page_rows)
        table = parse_api_rows(
            raw_rows,
            dimensions=dims,
            fetched_at=fetched,
            start_date=None if "date" in dims else start,
            end_date=None if "date" in dims else end,
        )
        if first_incomplete is not None:
            rows = tuple(
                replace(row, is_preliminary=True) if row.data_date >= first_incomplete else row for row in table.rows
            )
            table = replace(table, rows=rows)
        return GscQueryResult(
            site_url=site_url,
            start_date=start,
            end_date=end,
            dimensions=dims,
            data_state=data_state,
            table=table,
            pages_fetched=pages,
            first_incomplete_date=first_incomplete,
            fetched_at=fetched,
        )


# --------------------------------------------------------------------------- early signals


def _norm_query(text: str) -> str:
    return " ".join(str(text).casefold().split())


def signal_key(name: str, search: str) -> str:
    """The stable feature name of a per-search signal, for example sc_query_present[plumber leeds]."""
    return f"{name}[{_norm_query(search)}]"


def _kind(row: GscRow) -> tuple[bool, bool]:
    return (row.query is not None, row.page is not None)


def _dedupe(rows: Iterable[GscRow]) -> list[GscRow]:
    """One row per (date, query, page, country, device). The latest fetch wins. Ties keep the first seen."""
    best: dict[tuple[Any, ...], GscRow] = {}
    for row in rows:
        key = (row.data_date, row.query, row.page, row.country, row.device)
        held = best.get(key)
        if held is None or (row.fetched_at or "") > (held.fetched_at or ""):
            best[key] = row
    return [best[key] for key in sorted(best, key=lambda k: tuple("" if part is None else str(part) for part in k))]


def _slope(values: Sequence[float]) -> float | None:
    count = len(values)
    if count < 2:
        return None
    mean_x = (count - 1) / 2.0
    mean_y = sum(values) / count
    num = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    den = sum((index - mean_x) ** 2 for index in range(count))
    return num / den


EARLY_SIGNAL_NAMES: tuple[str, ...] = (
    "sc_days_observed",  # days from launch to the cutoff, both included
    "sc_complete_weeks",  # whole 7-day weeks from launch inside the cutoff
    "sc_days_to_first_impression",  # first day with an impression minus launch, in days
    "sc_impressions_total",  # impressions from launch to the cutoff
    "sc_impressions_per_week",  # mean impressions per complete week
    "sc_impressions_weekly_slope",  # least-squares slope of weekly impressions, per week
    "sc_distinct_queries_per_week",  # mean number of distinct queries per complete week
    "sc_distinct_queries_total",  # distinct queries from launch to the cutoff
    "sc_clicks_total",  # clicks from launch to the cutoff
    "sc_pages_with_impressions",  # distinct pages with at least one impression
    "sc_target_queries_present_share",  # share of target searches that Search Console shows at all
    "sc_rows_ignored_late",  # rows dated after the cutoff (ignored when strict is False)
    "sc_rows_ignored_preliminary",  # rows marked preliminary on or before the cutoff
    "sc_rows_ignored_range",  # rows that cover a date range, not one day
    "sc_rows_ignored_before_launch",  # rows dated before launch
    "sc_rows_ignored_excluded",  # rows for excluded sites (config.EXCLUDED_SITES)
)


def early_signals(
    rows: Iterable[GscRow],
    *,
    launch_date: date | str,
    as_of: date | str,
    target_searches: Sequence[str],
    strict: bool = False,
) -> dict[str, float | None]:
    """Search Console signals for the early-reading updater, knowable on `as_of`.

    The cutoff is `as_of - PRELIMINARY_DAYS`. Rows dated after it raise
    asof.LeakError when `strict` is True, and are ignored and counted otherwise.
    Rows marked preliminary are ignored and counted. Only daily rows dated from
    launch to the cutoff are used. The rows are taken to be complete for that
    span, so a day with no row had no impressions. Pass rows only for a site whose
    Search Console is connected.

    Totals come from the most complete kind of row present: date totals first
    (no query, no page), then page rows, then query rows. Query rows lose
    anonymised queries, so they undercount.

    Weeks run from launch: week k is days 7k to 7k + 6. Only complete weeks count
    toward per-week means and the slope.

    Per target search: `sc_query_present[...]` is 1.0 when a daily query row for it
    has an impression, else 0.0. `sc_avg_position[...]` is the impression-weighted
    mean position over those rows, or None when the query is not present.

    Search Console position is an impression-weighted average over all places and
    devices. It is not comparable to the clean reader. It is never used as an outcome.
    """
    launch = as_date(launch_date)
    asof_day = as_date(as_of)
    cutoff = asof_day - timedelta(days=PRELIMINARY_DAYS)
    late = preliminary = ranged = before = excluded = 0
    usable: list[GscRow] = []
    for row in rows:
        if _excluded(row.page):
            excluded += 1
            continue
        if row.data_date > cutoff:
            if strict:
                raise LeakError(
                    f"a Search Console row is dated {row.data_date}, after the cutoff {cutoff} "
                    f"(as_of {asof_day} minus {PRELIMINARY_DAYS} preliminary days)"
                )
            late += 1
            continue
        if not row.is_daily:
            ranged += 1
            continue
        if row.is_preliminary:
            preliminary += 1
            continue
        if row.data_date < launch:
            before += 1
            continue
        usable.append(row)
    usable = _dedupe(usable)

    days_observed = (cutoff - launch).days + 1
    complete_weeks = days_observed // DAYS_PER_WEEK if days_observed > 0 else 0
    targets = sorted({_norm_query(search) for search in target_searches if _norm_query(search)})
    result: dict[str, float | None] = {name: None for name in EARLY_SIGNAL_NAMES}
    result["sc_rows_ignored_late"] = float(late)
    result["sc_rows_ignored_preliminary"] = float(preliminary)
    result["sc_rows_ignored_range"] = float(ranged)
    result["sc_rows_ignored_before_launch"] = float(before)
    result["sc_rows_ignored_excluded"] = float(excluded)
    for search in targets:
        result[signal_key("sc_query_present", search)] = None
        result[signal_key("sc_avg_position", search)] = None
    if days_observed <= 0:
        return result
    result["sc_days_observed"] = float(days_observed)
    result["sc_complete_weeks"] = float(complete_weeks)

    by_kind: dict[tuple[bool, bool], list[GscRow]] = defaultdict(list)
    for row in usable:
        by_kind[_kind(row)].append(row)
    for kind, kind_rows in by_kind.items():
        split = {row.country is not None or row.device is not None for row in kind_rows}
        if len(split) > 1:
            raise ValueError(
                f"rows with query={kind[0]} page={kind[1]} mix country or device splits with unsplit rows. "
                "Their sums would count impressions twice."
            )
    # Most complete first: date totals, page rows, query rows, then query and page rows.
    total_rows: list[GscRow] = []
    for kind in ((False, False), (False, True), (True, False), (True, True)):
        if by_kind.get(kind):
            total_rows = by_kind[kind]
            break
    query_rows = by_kind.get((True, False)) or by_kind.get((True, True)) or []
    page_rows = by_kind.get((False, True)) or by_kind.get((True, True)) or []

    weekly_impressions = [0.0] * complete_weeks
    impressions_total = 0
    clicks_total = 0
    first_day: date | None = None
    for row in total_rows:
        impressions_total += row.impressions
        clicks_total += row.clicks
        if row.impressions > 0 and (first_day is None or row.data_date < first_day):
            first_day = row.data_date
        week = (row.data_date - launch).days // DAYS_PER_WEEK
        if week < complete_weeks:
            weekly_impressions[week] += row.impressions
    # A first impression can show in query or page rows even when the totals table is coarse.
    for row in query_rows + page_rows:
        if row.impressions > 0 and (first_day is None or row.data_date < first_day):
            first_day = row.data_date

    weekly_queries: list[set[str]] = [set() for _ in range(complete_weeks)]
    all_queries: set[str] = set()
    weight: dict[str, float] = defaultdict(float)
    weighted_pos: dict[str, float] = defaultdict(float)
    present: set[str] = set()
    for row in query_rows:
        if row.impressions <= 0 or row.query is None:
            continue
        norm = _norm_query(row.query)
        all_queries.add(norm)
        week = (row.data_date - launch).days // DAYS_PER_WEEK
        if week < complete_weeks:
            weekly_queries[week].add(norm)
        present.add(norm)
        if row.position is not None:
            weight[norm] += row.impressions
            weighted_pos[norm] += row.position * row.impressions
    pages_seen = {row.page for row in page_rows if row.impressions > 0 and row.page is not None}

    # With no impressions at all the site was shown for no query and no page: a real zero.
    # With impressions but no query (or page) rows, query (or page) counts are unknown.
    no_impressions = impressions_total == 0 and not any(row.impressions for row in query_rows + page_rows)
    know_queries = bool(query_rows) or no_impressions
    know_pages = bool(page_rows) or no_impressions
    result["sc_days_to_first_impression"] = None if first_day is None else float((first_day - launch).days)
    result["sc_impressions_total"] = float(impressions_total)
    result["sc_clicks_total"] = float(clicks_total)
    result["sc_distinct_queries_total"] = float(len(all_queries)) if know_queries else None
    result["sc_pages_with_impressions"] = float(len(pages_seen)) if know_pages else None
    if complete_weeks:
        result["sc_impressions_per_week"] = sum(weekly_impressions) / complete_weeks
        result["sc_impressions_weekly_slope"] = _slope(weekly_impressions)
        if know_queries:
            result["sc_distinct_queries_per_week"] = sum(len(week) for week in weekly_queries) / complete_weeks
    if targets and know_queries:
        hits = 0
        for search in targets:
            is_present = search in present
            hits += int(is_present)
            result[signal_key("sc_query_present", search)] = 1.0 if is_present else 0.0
            if is_present and weight.get(search):
                result[signal_key("sc_avg_position", search)] = weighted_pos[search] / weight[search]
        result["sc_target_queries_present_share"] = hits / len(targets)
    return result


# --------------------------------------------------------------------------- index status


@dataclass(frozen=True)
class IndexStatusRow:
    """Indexed and not-indexed page counts on one date. REAL."""

    day: date
    indexed: int
    not_indexed: int | None


_INDEXED_STATES = ("submitted and indexed", "indexed, not submitted in sitemap", "indexed")


def _is_indexed_state(state: str) -> bool:
    text = state.strip().casefold()
    return any(text == item or text.startswith(item + " ") for item in _INDEXED_STATES) or text.startswith("indexed,")


def parse_index_status(source: str | Path | bytes) -> list[IndexStatusRow]:
    """Indexed-page counts over time, sorted by date.

    Reads date,indexed,not_indexed. Also reads the URL Inspection coverage summary
    (date,type,coverageState,count), where "Submitted and indexed" and "Indexed, not
    submitted in sitemap" count as indexed and every other state as not indexed.
    A date listed twice in the simple layout is an error.
    """
    text = _read_text(source)
    reader = csv.reader(io.StringIO(text))
    try:
        header = [name.strip().casefold() for name in next(reader)]
    except StopIteration:
        raise GscParseError("the index status table is empty") from None
    rows: dict[date, list[int]] = {}
    if {"date", "indexed"} <= set(header):
        di, ii = header.index("date"), header.index("indexed")
        ni = header.index("not_indexed") if "not_indexed" in header else None
        out: dict[date, IndexStatusRow] = {}
        for line_no, cells in enumerate(reader, start=2):
            if not cells or all(not cell.strip() for cell in cells):
                continue
            where = f"index status line {line_no}"
            day = _date_cell(cells[di], f"{where} date")
            if day in out:
                raise GscParseError(f"{where}: the date {day} is listed twice")
            not_indexed = None
            if ni is not None and cells[ni].strip():
                not_indexed = _int_cell(cells[ni], f"{where} not_indexed")
            out[day] = IndexStatusRow(day, _int_cell(cells[ii], f"{where} indexed"), not_indexed)
        return [out[day] for day in sorted(out)]
    if {"date", "coveragestate", "count"} <= set(header):
        di, si, ci = header.index("date"), header.index("coveragestate"), header.index("count")
        for line_no, cells in enumerate(reader, start=2):
            if not cells or all(not cell.strip() for cell in cells):
                continue
            where = f"coverage summary line {line_no}"
            day = _date_cell(cells[di], f"{where} date")
            count = _int_cell(cells[ci], f"{where} count")
            bucket = rows.setdefault(day, [0, 0])
            bucket[0 if _is_indexed_state(cells[si]) else 1] += count
        return [IndexStatusRow(day, rows[day][0], rows[day][1]) for day in sorted(rows)]
    raise GscParseError(f"the index status header is not recognised: {header}")


INDEX_SIGNAL_NAMES: tuple[str, ...] = (
    "idx_days_to_first_indexed",  # first listed date with an indexed page minus launch, in days (an upper bound)
    "idx_indexed_latest",  # indexed pages on the newest date on or before as_of
    "idx_indexed_share_latest",  # indexed / (indexed + not indexed) on that date
    "idx_indexed_per_week",  # indexed pages on that date divided by weeks since launch
    "idx_rows_ignored_late",  # rows dated after as_of
)


def index_signals(
    rows: Sequence[IndexStatusRow], *, launch_date: date | str, as_of: date | str, strict: bool = False
) -> dict[str, float | None]:
    """How fast pages get indexed, knowable on `as_of`. Later rows raise LeakError when strict, else are counted."""
    launch = as_date(launch_date)
    asof_day = as_date(as_of)
    kept: list[IndexStatusRow] = []
    late = 0
    for row in rows:
        if row.day > asof_day:
            if strict:
                raise LeakError(f"an index status row is dated {row.day}, after as_of {asof_day}")
            late += 1
            continue
        if row.day >= launch:
            kept.append(row)
    kept.sort(key=lambda row: row.day)
    result: dict[str, float | None] = {name: None for name in INDEX_SIGNAL_NAMES}
    result["idx_rows_ignored_late"] = float(late)
    if not kept:
        return result
    first = next((row for row in kept if row.indexed > 0), None)
    latest = kept[-1]
    result["idx_days_to_first_indexed"] = None if first is None else float((first.day - launch).days)
    result["idx_indexed_latest"] = float(latest.indexed)
    if latest.not_indexed is not None and latest.indexed + latest.not_indexed > 0:
        result["idx_indexed_share_latest"] = latest.indexed / (latest.indexed + latest.not_indexed)
    elapsed = (latest.day - launch).days
    if elapsed > 0:
        result["idx_indexed_per_week"] = latest.indexed / (elapsed / DAYS_PER_WEEK)
    return result
