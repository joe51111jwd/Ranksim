"""Google's ranking update calendar, from the Search Status Dashboard.

What is REAL here: the names, kinds and dates of ranking updates exactly as
Google lists them on https://status.search.google.com (the Ranking product
history page and each incident page). Nothing here is PREDICTED.

The dashboard history page lists each incident with a summary, a start day and
a duration. Each incident page lists its posts with a day and a Pacific time
(PDT or PST). `start` is the time of the first post and `end` the time of the
last post when the incident is closed ("Available status" on the history
page). An open incident (for example a rollout still in progress) has end None.
When an incident page cannot be read, the start is the listed day at 00:00 UTC
and the end is that start plus the listed duration, and `precision` says so.

The parsed list is written to data/forecast/cache/updates/google_updates.json
when fetched, and a copy taken on 2026-11-22 ships with the code in
sources/data/google_updates.json so the calendar works with no network.
`load_updates()` reads the newest of the two. `overlaps(start, end)` returns
the updates whose window overlaps a date window, for the scoreboard's
`core_updates` table and for flagging backtest windows.

Coverage: the dashboard starts on 1 December 2021. Earlier updates are not in
it, and `coverage_start` in the JSON says so.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import httpx
from bs4 import BeautifulSoup

from aeo.ranksim.forecast.asof import as_date, parse_utc, utc_iso
from aeo.ranksim.forecast.chain import sha256_bytes
from aeo.ranksim.forecast.config import forecast_paths

DASHBOARD_BASE = "https://status.search.google.com"
RANKING_PRODUCT_ID = "rGHU1u87FJnkP6W2GwMi"
HISTORY_URL = f"{DASHBOARD_BASE}/products/{RANKING_PRODUCT_ID}/history"
INCIDENTS_JSON_URL = f"{DASHBOARD_BASE}/incidents.json"
MIN_INTERVAL_SECONDS = 1.0
COVERAGE_START = "2021-12-01"
PACIFIC_OFFSETS = {"PDT": -7, "PST": -8}
PACKAGED_PATH = Path(__file__).resolve().parent / "data" / "google_updates.json"

_MONTHS = {name: index for index, name in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


@dataclass(frozen=True)
class RankingUpdate:
    """One ranking incident on the dashboard. REAL. Times are UTC ISO text; end None means still open."""

    incident_id: str
    name: str
    kind: str
    start: str
    end: str | None
    listed_day: str
    listed_duration: str
    status: str
    precision: str
    url: str

    @property
    def start_date(self) -> date:
        return as_date(self.start)

    @property
    def end_date(self) -> date | None:
        return as_date(self.end) if self.end else None


def update_kind(name: str) -> str:
    """core, spam, helpful_content, reviews, discover, page_experience, incident or other."""
    text = name.lower()
    if "core update" in text:
        return "core"
    if "spam" in text:
        return "spam"
    if "helpful content" in text:
        return "helpful_content"
    if "review" in text:
        return "reviews"
    if "discover" in text:
        return "discover"
    if "page experience" in text:
        return "page_experience"
    if "issue" in text or "experiencing" in text:
        return "incident"
    return "other"


def parse_day(text: str) -> date:
    """'10 Nov 2026' -> date(2026, 11, 10)."""
    day, month, year = text.strip().split()
    return date(int(year), _MONTHS[month[:3]], int(day))


def parse_duration(text: str) -> timedelta | None:
    """'13 days, 16 hours' or '19 hours, 30 minutes' -> timedelta. None when it is not a duration."""
    total = timedelta()
    found = False
    for amount, unit in re.findall(r"(\d+)\s*(day|hour|minute)s?", text):
        found = True
        total += {"day": timedelta(days=int(amount)), "hour": timedelta(hours=int(amount)),
                  "minute": timedelta(minutes=int(amount))}[unit]
    return total if found else None


def parse_history_html(html: str | bytes) -> list[dict[str, str]]:
    """Rows of the Ranking history page: incident id, summary, listed day, duration text, status label."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            link = tr.find("a")
            day_cell = tr.find("td", class_=lambda value: bool(value) and value.endswith("__date"))
            duration_cell = tr.find("td", class_=lambda value: bool(value) and value.endswith("__duration"))
            if link is None or day_cell is None or duration_cell is None:
                continue
            summary = link.find("span")
            icon = duration_cell.find("svg")
            href = link.get("href") or ""
            rows.append({
                "incident_id": href.rstrip("/").split("/")[-1],
                "name": (summary or link).get_text(strip=True),
                "listed_day": day_cell.get_text(strip=True),
                "listed_duration": duration_cell.get_text(" ", strip=True),
                "status": (icon.get("aria-label") if icon is not None else "") or "",
            })
    return rows


def parse_incident_html(html: str | bytes) -> list[datetime]:
    """UTC times of the posts on one incident page (Date | Time | Description rows, times in PDT or PST)."""
    soup = BeautifulSoup(html, "lxml")
    times: list[datetime] = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        match = None
        day = None
        for index in range(1, len(cells)):
            match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(PDT|PST)", cells[index])
            if match:
                try:
                    day = parse_day(cells[index - 1])
                except (KeyError, ValueError):
                    day = None
                break
        if match is None or day is None:
            continue
        local = datetime(day.year, day.month, day.day, int(match.group(1)), int(match.group(2)))
        offset = timezone(timedelta(hours=PACIFIC_OFFSETS[match.group(3)]))
        times.append(local.replace(tzinfo=offset).astimezone(timezone.utc))
    return sorted(times)


def build_update(row: dict[str, str], post_times: Sequence[datetime] | None) -> RankingUpdate:
    """One update from a history row and, when read, its incident page post times."""
    closed = row["status"].lower().startswith("available")
    listed = parse_day(row["listed_day"])
    duration = parse_duration(row["listed_duration"])
    if post_times:
        start = post_times[0]
        end = post_times[-1] if closed and len(post_times) > 1 else None
        if closed and end is None and duration is not None:
            end = start + duration
        precision = "incident_page"
    else:
        start = datetime(listed.year, listed.month, listed.day, tzinfo=timezone.utc)
        end = start + duration if closed and duration is not None else None
        precision = "day_plus_duration"
    return RankingUpdate(
        incident_id=row["incident_id"], name=row["name"], kind=update_kind(row["name"]),
        start=utc_iso(start), end=utc_iso(end) if end else None, listed_day=listed.isoformat(),
        listed_duration=row["listed_duration"], status="closed" if closed else "open", precision=precision,
        url=f"{DASHBOARD_BASE}/incidents/{row['incident_id']}",
    )


def fetch_updates(
    client: httpx.Client | None = None,
    *,
    root: str | Path | None = None,
    read_incident_pages: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch the history page (and each incident page), parse, write the cache JSON, and return it."""
    own = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True,
                                  headers={"User-Agent": "ranksim-forecast-data/1.0 (research loader)"})
    try:
        response = http.get(HISTORY_URL)
        response.raise_for_status()
        history_bytes = response.content
        rows = parse_history_html(history_bytes)
        updates: list[RankingUpdate] = []
        for row in rows:
            times: list[datetime] | None = None
            if read_incident_pages:
                sleep(MIN_INTERVAL_SECONDS)
                try:
                    page = http.get(f"{DASHBOARD_BASE}/incidents/{row['incident_id']}")
                    if page.status_code == 200:
                        times = parse_incident_html(page.content) or None
                except httpx.HTTPError:
                    times = None
            updates.append(build_update(row, times))
    finally:
        if own:
            http.close()
    payload = {
        "source": HISTORY_URL,
        "fetched_at": utc_iso(datetime.now(timezone.utc)),
        "history_sha256": sha256_bytes(history_bytes),
        "coverage_start": COVERAGE_START,
        "count": len(updates),
        "updates": [asdict(item) for item in sorted(updates, key=lambda item: (item.start, item.incident_id))],
    }
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    return payload


def cache_path(root: str | Path | None = None) -> Path:
    return forecast_paths(root).cache / "updates" / "google_updates.json"


def _from_payload(payload: dict[str, Any]) -> list[RankingUpdate]:
    return [RankingUpdate(**item) for item in payload.get("updates", [])]


def load_updates(root: str | Path | None = None, path: str | Path | None = None) -> list[RankingUpdate]:
    """The calendar with no network: the given file, else the newer of the cache file and the packaged copy."""
    candidates = [Path(path)] if path else [cache_path(root), PACKAGED_PATH]
    best: dict[str, Any] | None = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        if best is None or str(payload.get("fetched_at", "")) > str(best.get("fetched_at", "")):
            best = payload
    if best is None:
        raise FileNotFoundError("no Google update calendar found. Run fetch_updates() once with network.")
    return _from_payload(best)


def overlaps(
    start: date | datetime | str,
    end: date | datetime | str,
    updates: Iterable[RankingUpdate] | None = None,
    *,
    kinds: Iterable[str] | None = None,
    open_until: date | None = None,
) -> list[RankingUpdate]:
    """Updates whose window [start, end] overlaps the closed date window [start, end].

    An open update (end None) runs until `open_until`, or forever when that is None.
    Dates are compared as UTC calendar days, both ends included.
    """
    lo = as_date(start)
    hi = as_date(end)
    if hi < lo:
        raise ValueError(f"window end {hi} is before its start {lo}")
    wanted = set(kinds) if kinds is not None else None
    chosen = load_updates() if updates is None else list(updates)
    out = []
    for item in chosen:
        if wanted is not None and item.kind not in wanted:
            continue
        u_start = item.start_date
        u_end = item.end_date or open_until or date.max
        if u_start <= hi and u_end >= lo:
            out.append(item)
    return sorted(out, key=lambda item: (item.start, item.incident_id))


def scoreboard_rows(updates: Iterable[RankingUpdate], recorded_at: datetime | None = None) -> list[dict[str, Any]]:
    """Rows for the scoreboard's core_updates table: name, kind, start, end, source URL, and when we recorded them."""
    stamp = utc_iso(recorded_at or datetime.now(timezone.utc))
    return [{"name": item.name, "kind": item.kind, "start": item.start, "end": item.end, "status": item.status,
             "source_url": item.url, "recorded_at": stamp} for item in updates]


def main() -> int:
    payload = fetch_updates()
    packaged = dict(payload)
    PACKAGED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKAGED_PATH.write_text(json.dumps(packaged, indent=1, sort_keys=True), encoding="utf-8")
    counts: dict[str, int] = {}
    for item in payload["updates"]:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    print(json.dumps({"count": payload["count"], "kinds": counts, "first": payload["updates"][0]["start"],
                      "last": payload["updates"][-1]["start"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
