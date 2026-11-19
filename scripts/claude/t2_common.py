"""Target 2 shared helpers: sealed page-fetch table, ET-day alignment, extraction cache.

Every page fetch comes from rsdata.load_page_index(max_date) + rsdata.load_page_html(id).
Nothing here opens the SQLite DB directly or reads a snapshot directory.

Fetch-day alignment (frozen):
- A fetch's day is the America/New_York calendar date of fetched_at_utc, the same
  convention as the SERP snapshot_date. Example: the "2026-11-06" snapshot pages
  were fetched at 2026-11-07T03:1xZ, which is 22:1x on Nov 6 in New York.
- A stamp of exactly T00:00:00.000Z is a date-only import (the Nov 16 index crawl
  and browser pass). Its day is that UTC date and ``date_only`` is True.
- Only fetch_method 'http' and 'browser' with HTML of at least 500 bytes and an
  HTTP status of 200 or unrecorded are kept. Challenge pages, 4xx/5xx, robots
  blocks, serper_scrape and wayback records are dropped.
- One fetch per (url key, day, method) enters the dataset: the latest timed fetch
  of that day whose main text extracts to >= MIN_WORDS words; if none is usable,
  the latest timed fetch. Date-only stamps never enter the dataset (their real
  fetch time and crawler setup are unknown). The choice reads page content only,
  never a rank.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import rsdata  # noqa: E402
from scripts.claude.t2_changes import MIN_WORDS, extract_page  # noqa: E402

OUT_DIR = ROOT / "evals/claude/t2-edits"
EXTRACT_CACHE = Path("/tmp/t2-extract-cache-v1.pkl")
KEEP_METHODS = ("http", "browser")
MIN_HTML_BYTES = 500


def fetch_table(max_date: str = rsdata.DESIGN_MAX_DATE) -> pd.DataFrame:
    """All usable fetches with ET day, sealed at max_date (ET day must be <= max_date too)."""
    index = rsdata.load_page_index(max_date)
    frame = index[index["fetch_method"].isin(KEEP_METHODS)].copy()
    frame = frame[frame["html_len"].fillna(0) >= MIN_HTML_BYTES]
    frame = frame[frame["http_status"].isna() | (frame["http_status"] == 200)]
    stamps = pd.to_datetime(frame["fetched_at"], utc=True, format="ISO8601")
    frame["date_only"] = frame["fetched_at"].str.endswith("T00:00:00.000Z") | frame["fetched_at"].str.endswith("T00:00:00Z")
    et_day = stamps.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    frame["day"] = et_day.where(~frame["date_only"], frame["fetched_at"].str.slice(0, 10))
    frame = frame[frame["day"] <= max_date]
    return frame.sort_values(["key", "fetch_method", "day", "fetched_at", "id"]).reset_index(drop=True)


def one_per_day(fetches: pd.DataFrame, words: dict[int, int]) -> pd.DataFrame:
    """Latest usable timed fetch per (key, method, day); else the latest timed fetch."""
    timed = fetches[~fetches["date_only"]].copy()
    timed["_usable"] = timed["id"].map(lambda pid: words.get(int(pid), 0) >= MIN_WORDS)
    ordered = timed.sort_values(["key", "fetch_method", "day", "_usable", "fetched_at", "id"])
    picked = ordered.groupby(["key", "fetch_method", "day"], as_index=False).tail(1)
    return picked.drop(columns="_usable").reset_index(drop=True)


def same_day_pairs(fetches: pd.DataFrame) -> pd.DataFrame:
    """Noise reference: earliest vs latest timed fetch of the same key, method and day, >= 1 hour apart."""
    timed = fetches[~fetches["date_only"]].sort_values(["key", "fetch_method", "day", "fetched_at", "id"])
    out = []
    for (key, method, day), group in timed.groupby(["key", "fetch_method", "day"], sort=True):
        if len(group) < 2:
            continue
        first, last = group.iloc[0], group.iloc[-1]
        gap = (pd.Timestamp(last["fetched_at"]) - pd.Timestamp(first["fetched_at"])).total_seconds() / 3600.0
        if gap < 1.0:
            continue
        out.append({"key": key, "method": method, "day_a": day, "day_b": day, "id_a": int(first["id"]),
                    "id_b": int(last["id"]), "url": last["url"], "hours": round(gap, 2)})
    return pd.DataFrame.from_records(out)


def extracted(ids_urls: list[tuple[int, str]]) -> dict[int, dict]:
    """extract_page() output per page id. Derived fields cached in /tmp (no HTML in the repo)."""
    cache: dict[int, dict] = {}
    if EXTRACT_CACHE.is_file():
        cache = pickle.loads(EXTRACT_CACHE.read_bytes())
    missing = [(pid, url) for pid, url in ids_urls if int(pid) not in cache]
    for count, (pid, url) in enumerate(missing, 1):
        cache[int(pid)] = extract_page(rsdata.load_page_html(int(pid)), url)
        if count % 200 == 0:
            print(f"  extracted {count}/{len(missing)}", flush=True)
    if missing:
        EXTRACT_CACHE.write_bytes(pickle.dumps(cache, protocol=4))
    return {int(pid): cache[int(pid)] for pid, _url in ids_urls}
