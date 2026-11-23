"""Site strength as of a date, from dated public lists.

What is REAL here: every number is copied from a public list as published.
Common Crawl domain ranks (harmonic centrality and PageRank of the domain
graph), Open PageRank monthly scores, Chrome UX Report popularity buckets
(the zakird/crux-top-lists mirror), Tranco daily lists and today's Majestic
Million. Nothing in this module is PREDICTED. No model runs here.

The leakage rule. A value is wrapped in `asof.Dated` whose `snapshot_date` is
the date the snapshot was PUBLISHED, not the end of its data window. A list
about June that came out on 3 July is knowable from 3 July only.
`lookup(sites, as_of)` returns, per site, the newest snapshot published on or
before `as_of`, and `strength_features` passes every value through an
`AsOfGuard`, which refuses anything later.

How each publication date is set:

- Common Crawl domain ranks. The Last-Modified time of the release's
  `domain-ranks.txt.gz` on data.commoncrawl.org, read with HEAD requests on
  2026-11-22 and frozen in `CC_RELEASES`. The file was public from that time
  on. A release that is not in the table and whose Last-Modified cannot be
  read gets a conservative date: the end of its last crawl plus
  `CC_FALLBACK_LAG_DAYS` (60). The largest lag in the table is 45 days.
- Open PageRank history. One column per month. The history file was
  downloaded once (its zip entry is dated 2026-10-25). A column labelled
  2024-06 is taken as published on the first day of the next month
  (2024-07-01). That is conservative for timing, but two limits stay: (1) only
  domains in the CURRENT (2026-10) top 10 million are in the file, so the mere
  presence of a site carries future information (survivorship). Every value
  carries `survivorship_limited` = 1.0 for that reason. (2) We cannot prove
  DomCop did not recompute old columns later.
- Chrome UX Report top lists. The file for month M is published with the CrUX
  release in month M+1, which Google ships on the second Tuesday (day 8 to 14).
  We use day `CRUX_PUBLISH_DAY` (15) of month M+1, which is on or after every
  second Tuesday. The bucket scheme changed: early files use 1k, 10k, 100k and
  1M; later files add 5k, 50k and 500k. `crux_bucket_coarse` maps every bucket
  up to the old scheme so months compare.
- Tranco. The list's own `created_on` date from the Tranco API.
- Majestic Million. Only today's file exists. It is undated
  (`snapshot_date` None), so a backtest guard refuses it and only a live
  forecast may use it.

Every download is recorded in a SOURCES.json beside the cache with its URL,
fetch time, size and sha256. Rows for excluded sites (SmartCrew) are dropped.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from aeo.ranksim.forecast.asof import AsOfGuard, Dated, add_months, as_date, utc_iso
from aeo.ranksim.forecast.config import EXCLUDED_SITES, forecast_paths
from aeo.ranksim.site_history import site_key

USER_AGENT = "ranksim-forecast-data/1.0 (research loader; contact via repository)"
HTTP_TIMEOUT_SECONDS = 60.0
CHUNK_BYTES = 1 << 20
MAX_STREAM_RETRIES = 8
DEFAULT_MIN_INTERVAL_SECONDS = 0.5

CC_BASE = "https://data.commoncrawl.org/projects/hyperlinkgraph"
CC_GRAPHINFO_URL = "https://index.commoncrawl.org/graphinfo.json"
CC_COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
CC_FALLBACK_LAG_DAYS = 60

OPR_HISTORY_URL = "https://download.openpagerank.net/top10milliondomains-history.csv.zip"
OPR_HISTORY_FILE = "top10milliondomains-history.csv.zip"
OPR_SURVIVORSHIP_NOTE = "Only domains in the 2026-10 Open PageRank top 10 million are present."

CRUX_URL = "https://raw.githubusercontent.com/zakird/crux-top-lists/main/data/global/{month}.csv.gz"
CRUX_PUBLISH_DAY = 15
CRUX_COARSE_BUCKETS: tuple[int, ...] = (1000, 10000, 100000, 1000000)
CRUX_FINE_ONLY_BUCKETS: frozenset[int] = frozenset({5000, 50000, 500000})

TRANCO_API = "https://tranco-list.eu/api/lists/date/{day}"
TRANCO_MIN_INTERVAL_SECONDS = 2.0

MAJESTIC_URL = "https://downloads.majestic.com/majestic_million.csv"

# Release id, crawls, data window start, data window end (from collinfo.json),
# publication date (Last-Modified of domain-ranks.txt.gz, HEAD on 2026-11-22),
# domain nodes (graphinfo.json) and the ranks file size in bytes.
CC_RELEASES: tuple[tuple[str, tuple[str, ...], str, str, str, int, int], ...] = (
    ("cc-main-2026-aug-sep-oct", ("CC-MAIN-2026-34", "CC-MAIN-2026-39", "CC-MAIN-2026-43"), "2026-08-07", "2026-10-18", "2026-10-22", 134979706, 2544184794),
    ("cc-main-2026-jul-aug-sep", ("CC-MAIN-2026-30", "CC-MAIN-2026-34", "CC-MAIN-2026-39"), "2026-07-10", "2026-09-17", "2026-09-22", 133241980, 2515862336),
    ("cc-main-2026-jun-jul-aug", ("CC-MAIN-2026-25", "CC-MAIN-2026-30", "CC-MAIN-2026-34"), "2026-06-05", "2026-08-20", "2026-08-25", 119722885, 2364792440),
    ("cc-main-2026-may-jun-jul", ("CC-MAIN-2026-21", "CC-MAIN-2026-25", "CC-MAIN-2026-30"), "2026-05-08", "2026-07-23", "2026-07-28", 117963409, 2349720599),
    ("cc-main-2026-apr-may-jun", ("CC-MAIN-2026-17", "CC-MAIN-2026-21", "CC-MAIN-2026-25"), "2026-04-10", "2026-06-18", "2026-06-24", 121091933, 2388634678),
    ("cc-main-2026-mar-apr-may", ("CC-MAIN-2026-12", "CC-MAIN-2026-17", "CC-MAIN-2026-21"), "2026-03-05", "2026-05-21", "2026-05-28", 118760321, 2348299329),
    ("cc-main-2026-feb-mar-apr", ("CC-MAIN-2026-08", "CC-MAIN-2026-12", "CC-MAIN-2026-17"), "2026-02-06", "2026-04-23", "2026-04-30", 124646710, 2414416937),
    ("cc-main-2026-jan-feb-mar", ("CC-MAIN-2026-04", "CC-MAIN-2026-08", "CC-MAIN-2026-12"), "2026-01-12", "2026-03-17", "2026-03-24", 120025754, 2365541332),
    ("cc-main-2025-26-dec-jan-feb", ("CC-MAIN-2025-51", "CC-MAIN-2026-04", "CC-MAIN-2026-08"), "2025-12-04", "2026-02-19", "2026-02-24", 134222466, 2549908791),
    ("cc-main-2025-26-nov-dec-jan", ("CC-MAIN-2025-47", "CC-MAIN-2025-51", "CC-MAIN-2026-04"), "2025-11-06", "2026-01-25", "2026-01-30", 122294847, 2395355438),
    ("cc-main-2025-oct-nov-dec", ("CC-MAIN-2025-43", "CC-MAIN-2025-47", "CC-MAIN-2025-51"), "2025-10-05", "2025-12-17", "2025-12-23", 121304598, 2385402702),
    ("cc-main-2025-sep-oct-nov", ("CC-MAIN-2025-38", "CC-MAIN-2025-43", "CC-MAIN-2025-47"), "2025-09-05", "2025-11-19", "2025-11-24", 100662487, 2141803659),
    ("cc-main-2025-aug-sep-oct", ("CC-MAIN-2025-33", "CC-MAIN-2025-38", "CC-MAIN-2025-43"), "2025-08-02", "2025-10-19", "2025-10-25", 97717162, 2122715619),
    ("cc-main-2025-jul-aug-sep", ("CC-MAIN-2025-30", "CC-MAIN-2025-33", "CC-MAIN-2025-38"), "2025-07-07", "2025-09-18", "2025-09-25", 184587023, 3150533052),
    ("cc-main-2025-jun-jul-aug", ("CC-MAIN-2025-26", "CC-MAIN-2025-30", "CC-MAIN-2025-33"), "2025-06-12", "2025-08-15", "2025-08-21", 207560453, 3457867197),
    ("cc-main-2025-may-jun-jul", ("CC-MAIN-2025-21", "CC-MAIN-2025-26", "CC-MAIN-2025-30"), "2025-05-12", "2025-07-20", "2025-07-25", 209497416, 3500262267),
    ("cc-main-2025-apr-may-jun", ("CC-MAIN-2025-18", "CC-MAIN-2025-21", "CC-MAIN-2025-26"), "2025-04-17", "2025-06-25", "2025-07-03", 161816883, 2960986701),
    ("cc-main-2025-mar-apr-may", ("CC-MAIN-2025-13", "CC-MAIN-2025-18", "CC-MAIN-2025-21"), "2025-03-15", "2025-05-25", "2025-05-29", 156144386, 2865217547),
    ("cc-main-2025-feb-mar-apr", ("CC-MAIN-2025-08", "CC-MAIN-2025-13", "CC-MAIN-2025-18"), "2025-02-06", "2025-05-01", "2025-05-05", 157123147, 2882629831),
    ("cc-main-2025-jan-feb-mar", ("CC-MAIN-2025-05", "CC-MAIN-2025-08", "CC-MAIN-2025-13"), "2025-01-12", "2025-03-28", "2025-04-01", 135153877, 2618227904),
    ("cc-main-2024-25-dec-jan-feb", ("CC-MAIN-2024-51", "CC-MAIN-2025-05", "CC-MAIN-2025-08"), "2024-12-01", "2025-02-19", "2025-02-25", 106478170, 2257853192),
    ("cc-main-2024-25-nov-dec-jan", ("CC-MAIN-2024-46", "CC-MAIN-2024-51", "CC-MAIN-2025-05"), "2024-11-01", "2025-01-26", "2025-02-01", 100765998, 2187001661),
    ("cc-main-2024-oct-nov-dec", ("CC-MAIN-2024-42", "CC-MAIN-2024-46", "CC-MAIN-2024-51"), "2024-10-03", "2024-12-15", "2024-12-21", 98727688, 2135813487),
    ("cc-main-2024-sep-oct-nov", ("CC-MAIN-2024-38", "CC-MAIN-2024-42", "CC-MAIN-2024-46"), "2024-09-07", "2024-11-15", "2024-11-20", 98031234, 2149583735),
    ("cc-main-2024-aug-sep-oct", ("CC-MAIN-2024-33", "CC-MAIN-2024-38", "CC-MAIN-2024-42"), "2024-08-02", "2024-10-16", "2024-10-22", 96021021, 2112047491),
    ("cc-main-2024-jul-aug-sep", ("CC-MAIN-2024-30", "CC-MAIN-2024-33", "CC-MAIN-2024-38"), "2024-07-12", "2024-09-21", "2024-09-26", 95369395, 2108984265),
    ("cc-main-2024-jun-jul-aug", ("CC-MAIN-2024-26", "CC-MAIN-2024-30", "CC-MAIN-2024-33"), "2024-06-12", "2024-08-16", "2024-08-21", 159674761, 2852771266),
    ("cc-main-2024-may-jun-jul", ("CC-MAIN-2024-22", "CC-MAIN-2024-26", "CC-MAIN-2024-30"), "2024-05-17", "2024-07-25", "2024-07-30", 163003160, 2924701337),
    ("cc-main-2024-apr-may-jun", ("CC-MAIN-2024-18", "CC-MAIN-2024-22", "CC-MAIN-2024-26"), "2024-04-12", "2024-06-25", "2024-06-30", 173391896, 3053735091),
    ("cc-main-2024-feb-apr-may", ("CC-MAIN-2024-10", "CC-MAIN-2024-18", "CC-MAIN-2024-22"), "2024-02-20", "2024-05-31", "2024-06-04", 127746477, 2575227373),
    ("cc-main-2024-nov-feb-apr", ("CC-MAIN-2023-50", "CC-MAIN-2024-10", "CC-MAIN-2024-18"), "2023-11-28", "2024-04-25", "2024-05-05", 113858457, 2435413880),
    ("cc-main-2023-24-sep-nov-feb", ("CC-MAIN-2023-40", "CC-MAIN-2023-50", "CC-MAIN-2024-10"), "2023-09-21", "2024-03-05", "2024-03-13", 101323206, 2280733764),
    ("cc-main-2023-may-sep-nov", ("CC-MAIN-2023-23", "CC-MAIN-2023-40", "CC-MAIN-2023-50"), "2023-05-27", "2023-12-12", "2023-12-22", 93942613, 2141920238),
    ("cc-main-2023-mar-may-oct", ("CC-MAIN-2023-14", "CC-MAIN-2023-23", "CC-MAIN-2023-40"), "2023-03-20", "2023-10-05", "2023-11-04", 94197637, 2157847731),
    ("cc-main-2022-23-sep-nov-jan", ("CC-MAIN-2022-40", "CC-MAIN-2022-49", "CC-MAIN-2023-06"), "2022-09-24", "2023-02-09", "2023-03-15", 88092985, 2044431226),
    ("cc-main-2022-may-jun-aug", ("CC-MAIN-2022-21", "CC-MAIN-2022-27", "CC-MAIN-2022-33"), "2022-05-16", "2022-08-20", "2022-09-21", 91068127, 2107692287),
    ("cc-main-2021-22-oct-nov-jan", ("CC-MAIN-2021-43", "CC-MAIN-2021-49", "CC-MAIN-2022-05"), "2021-10-15", "2022-01-29", "2022-03-15", 90296538, 2075633262),
    ("cc-main-2021-jun-jul-sep", ("CC-MAIN-2021-25", "CC-MAIN-2021-31", "CC-MAIN-2021-39"), "2021-06-12", "2021-09-29", "2021-10-07", 87870767, 2028648971),
    ("cc-main-2021-feb-apr-may", ("CC-MAIN-2021-10", "CC-MAIN-2021-17", "CC-MAIN-2021-21"), "2021-02-24", "2021-05-19", "2021-05-26", 87749728, 2027847994),
    ("cc-main-2020-21-oct-nov-jan", ("CC-MAIN-2020-45", "CC-MAIN-2020-50", "CC-MAIN-2021-04"), "2020-10-19", "2021-01-28", "2021-02-08", 85908635, 1982761407),
    ("cc-main-2020-jul-aug-sep", ("CC-MAIN-2020-29", "CC-MAIN-2020-34", "CC-MAIN-2020-40"), "2020-07-02", "2020-10-02", "2020-10-12", 88591818, 2050699149),
    ("cc-main-2020-feb-mar-may", ("CC-MAIN-2020-10", "CC-MAIN-2020-16", "CC-MAIN-2020-24"), "2020-02-16", "2020-06-07", "2020-06-16", 90983688, 2099838032),
    ("cc-main-2019-20-nov-dec-jan", ("CC-MAIN-2019-47", "CC-MAIN-2019-51", "CC-MAIN-2020-05"), "2019-11-11", "2020-01-29", "2020-02-10", 85840531, 1998657732),
    ("cc-main-2019-aug-sep-oct", ("CC-MAIN-2019-35", "CC-MAIN-2019-39", "CC-MAIN-2019-43"), "2019-08-17", "2019-10-24", "2019-11-11", 92747835, 2113667438),
    ("cc-main-2019-may-jun-jul", ("CC-MAIN-2019-22", "CC-MAIN-2019-26", "CC-MAIN-2019-30"), "2019-05-19", "2019-07-24", "2019-08-07", 88258311, 2048021934),
    ("cc-main-2019-feb-mar-apr", ("CC-MAIN-2019-09", "CC-MAIN-2019-13", "CC-MAIN-2019-18"), "2019-02-15", "2019-04-26", "2019-05-07", 90757643, 2123519328),
    ("cc-main-2018-19-nov-dec-jan", ("CC-MAIN-2018-47", "CC-MAIN-2018-51", "CC-MAIN-2019-04"), "2018-11-12", "2019-01-24", "2019-02-05", 90031628, 2109444980),
    ("cc-main-2018-aug-sep-oct", ("CC-MAIN-2018-34", "CC-MAIN-2018-39", "CC-MAIN-2018-43"), "2018-08-14", "2018-10-24", "2018-11-07", 87160632, 2024270715),
    ("cc-main-2018-may-jun-jul", ("CC-MAIN-2018-22", "CC-MAIN-2018-26", "CC-MAIN-2018-30"), "2018-05-20", "2018-07-23", "2018-08-08", 92055751, 2101783848),
    ("cc-main-2018-feb-mar-apr", ("CC-MAIN-2018-09", "CC-MAIN-2018-13", "CC-MAIN-2018-17"), "2018-02-17", "2018-04-27", "2018-05-04", 97912758, 2206865748),
    ("cc-main-2018-jan", ("CC-MAIN-2018-05",), "2018-01-16", "2018-01-24", "2018-02-21", 70367284, 1563738765),
    ("cc-main-2017-18-nov-dec-jan", ("CC-MAIN-2017-47", "CC-MAIN-2017-51", "CC-MAIN-2018-05"), "2017-11-17", "2018-01-24", "2018-02-27", 94681319, 2122285347),
)


# ---------------------------------------------------------------------------
# Shared pieces


@dataclass(frozen=True)
class Snapshot:
    """One published snapshot of a list: its id, the data window it describes, and when it was published.

    `published` None means the snapshot has no knowable publication date (undated).
    """

    snapshot_id: str
    data_start: date | None
    data_end: date | None
    published: date | None
    url: str | None = None
    note: str = ""


@runtime_checkable
class StrengthSource(Protocol):
    """A dated strength list. `lookup` returns the newest snapshot published on or before `as_of`."""

    name: str

    def snapshots(self) -> list[Snapshot]: ...

    def lookup(self, sites: Iterable[str], as_of: date) -> dict[str, Dated]: ...


def pick_snapshot(snapshots: Sequence[Snapshot], as_of: date | str) -> Snapshot | None:
    """The newest snapshot published on or before `as_of`. Undated snapshots are never picked here."""
    day = as_date(as_of)
    dated = [snap for snap in snapshots if snap.published is not None and snap.published <= day]
    if not dated:
        return None
    return max(dated, key=lambda snap: (snap.published, snap.data_end or date.min, snap.snapshot_id))


def normalise_site(text: str | None) -> str | None:
    """The registrable site for a domain, host, origin or URL, or None when empty or excluded."""
    raw = str(text or "").strip().lower()
    if not raw:
        return None
    site = site_key(raw)
    if not site or "." not in site or site in EXCLUDED_SITES:
        return None
    return site


def wanted_sites(sites: Iterable[str]) -> set[str]:
    """Normalised, de-duplicated sites with excluded sites removed."""
    out: set[str] = set()
    for item in sites:
        site = normalise_site(item)
        if site:
            out.add(site)
    return out


def reverse_domain(domain: str) -> str:
    """example.co.uk -> uk.co.example (the Common Crawl key), and back again."""
    return ".".join(reversed([part for part in domain.strip(".").split(".") if part]))


def default_root(root: str | Path | None = None) -> Path:
    """The forecast data root (RANKSIM_FORECAST_DIR or ./data/forecast)."""
    return forecast_paths(root).root


def make_client(timeout: float = HTTP_TIMEOUT_SECONDS) -> httpx.Client:
    """A sync client that honours the proxy and CA settings of the environment and verifies TLS."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=30.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


class RateLimiter:
    """At most one request per `min_interval` seconds per host."""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS, sleep: Callable[[float], None] = time.sleep):
        self.min_interval = float(min_interval)
        self._last: dict[str, float] = {}
        self._sleep = sleep

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        now = time.monotonic()
        last = self._last.get(host)
        if last is not None and now - last < self.min_interval:
            self._sleep(self.min_interval - (now - last))
        self._last[host] = time.monotonic()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def record_source(directory: Path, key: str, entry: dict[str, Any]) -> None:
    """Upsert one provenance entry in `directory`/SOURCES.json (URL, fetch time, sha256, ...)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SOURCES.json"
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data[key] = entry
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class ListUnreachable(httpx.ConnectError):
    """The first connection of a download could not be made: no server ever answered. Never retried.

    No network, a refused proxy or a dead host does not heal in a few minutes, so waiting through
    the retries meant for a broken stream would only hide the failure. Nothing is marked searched.
    """


# Connection failures before any server answered: the download fails at once instead of retrying.
FIRST_CONNECTION_ERRORS: tuple[type[Exception], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)

def _sleep(seconds: float) -> None:
    """time.sleep looked up at call time, so a test can replace either this or time.sleep."""
    time.sleep(seconds)


def _unreachable(url: str, error: Exception) -> ListUnreachable:
    host = urlsplit(url).netloc or url
    return ListUnreachable(f"the first connection to {host} could not be made ({type(error).__name__}: {error}). "
                           "No server answered, so it was not retried. Check the network or the proxy",
                           request=getattr(error, "_request", None))


def _wait_before_retry(log: Callable[[str], None] | None, message: str, seconds: float) -> None:
    """Say what is being waited for, then wait. A long wait is never silent: with no log the line goes to stderr."""
    line = f"{message} Waiting {seconds:.0f} s before retrying."
    if log is not None:
        log(line)
    else:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    _sleep(seconds)


def http_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return None


def download_resumable(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    limiter: RateLimiter | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Download `url` to `dest`, resuming a partial `.part` file with HTTP Range. Returns provenance.

    A complete `dest` is never fetched again. A server that ignores Range makes the
    download start over (the partial file is discarded).
    """
    dest = Path(dest).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta_path = dest.with_name(dest.name + ".meta.json")
    if dest.exists():
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding="utf-8"))
        meta = {"url": url, "path": str(dest), "bytes": dest.stat().st_size, "sha256": sha256_file(dest),
                "fetched_at": None, "last_modified": None, "note": "file was present before provenance was kept"}
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return meta
    part = dest.with_name(dest.name + ".part")
    attempts = 0
    connected = False  # a server answered at least once in this call; until then a connection failure is not retried
    last_modified: str | None = None
    etag: str | None = None
    total: int | None = None
    while True:
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        if limiter:
            limiter.wait(url)
        try:
            with client.stream("GET", url, headers=headers) as response:
                connected = True
                if response.status_code == 416:
                    # Range not satisfiable: the partial file may already be complete.
                    size_text = (response.headers.get("content-range") or "").rpartition("/")[2]
                    if size_text.isdigit() and offset >= int(size_text):
                        break
                if response.status_code not in (200, 206):
                    raise httpx.HTTPStatusError(f"HTTP {response.status_code} for {url}", request=response.request, response=response)
                if offset and response.status_code == 200:
                    offset = 0  # the server ignored Range: start over
                    mode = "wb"
                else:
                    mode = "ab" if offset else "wb"
                last_modified = response.headers.get("last-modified") or last_modified
                etag = response.headers.get("etag") or etag
                length = response.headers.get("content-length")
                if length is not None:
                    total = offset + int(length)
                with open(part, mode) as handle:
                    for block in response.iter_bytes(CHUNK_BYTES):
                        handle.write(block)
            if total is None or part.stat().st_size >= total:
                break
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            attempts += 1
            if isinstance(error, httpx.HTTPStatusError) and error.response is not None and error.response.status_code in (403, 404, 410):
                raise
            if isinstance(error, FIRST_CONNECTION_ERRORS) and not connected:
                raise _unreachable(url, error) from error
            if attempts > MAX_STREAM_RETRIES:
                raise
            _wait_before_retry(log, f"The download of {url} broke at {part.stat().st_size if part.exists() else 0} bytes "
                                    f"({type(error).__name__}), attempt {attempts} of {MAX_STREAM_RETRIES}.",
                               min(60.0, 2.0 ** attempts))
    os.replace(part, dest)
    meta = {
        "url": url,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": sha256_file(dest),
        "fetched_at": utc_iso(datetime.now(timezone.utc)),
        "last_modified": last_modified,
        "etag": etag,
    }
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def _write_parquet_atomic(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def _read_searched(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def _write_searched(path: Path, sites: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as handle:
        for site in sorted(set(sites)):
            handle.write(site + "\n")
    os.replace(tmp, path)


def _merge_rows(old: pa.Table | None, new_rows: list[dict[str, Any]], schema: pa.Schema, key: str = "site") -> pa.Table:
    """Old rows plus new rows. A site in the new rows replaces its old rows."""
    new = pa.Table.from_pylist(new_rows, schema=schema)
    if old is None or old.num_rows == 0:
        return new
    replaced = pa.array(sorted(set(new.column(key).to_pylist())), type=pa.string())
    if len(replaced):
        old = old.filter(pc.invert(pc.is_in(old.column(key), value_set=replaced)))
    return pa.concat_tables([old.cast(schema), new])


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number


# ---------------------------------------------------------------------------
# Common Crawl domain ranks


CC_SCHEMA = pa.schema([
    ("site", pa.string()),
    ("harmonicc_pos", pa.int64()),
    ("harmonicc_val", pa.float64()),
    ("pr_pos", pa.int64()),
    ("pr_val", pa.float64()),
    ("n_hosts", pa.int64()),
])


def parse_rank_line(line: bytes | str) -> tuple[str, int, float, int, float, int] | None:
    """One row of a domain-ranks file: (domain, hc_pos, hc_val, pr_pos, pr_val, n_hosts). None for the header.

    The file is tab separated with the domain reversed: `1  3.2E7  2  0.0177  com.googleapis  3753`.
    """
    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
    text = text.rstrip("\r\n")
    if not text or text.startswith("#"):
        return None
    parts = text.split("\t")
    if len(parts) < 6:
        return None
    try:
        return (reverse_domain(parts[4]).lower(), int(parts[0]), float(parts[1]), int(parts[2]), float(parts[3]), int(parts[5]))
    except ValueError:
        return None


def stream_gzip_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Lines of a (possibly multi-member) gzip stream given as byte chunks."""
    decoder = zlib.decompressobj(wbits=47)
    pending = b""
    for chunk in chunks:
        data = chunk
        while data:
            out = decoder.decompress(data)
            if out:
                pending += out
                if b"\n" in pending:
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    yield from lines
            if decoder.eof:
                data = decoder.unused_data
                decoder = zlib.decompressobj(wbits=47)
            else:
                data = b""
    tail = decoder.flush()
    if tail:
        pending += tail
    if pending:
        yield from pending.split(b"\n")


def filter_rank_lines(lines: Iterable[bytes], wanted_reversed: set[bytes]) -> Iterator[tuple[str, int, float, int, float, int]]:
    """Rows whose reversed domain is in `wanted_reversed`. Cheap check first, full parse only on a hit."""
    for line in lines:
        end = line.rfind(b"\t")
        if end < 0:
            continue
        start = line.rfind(b"\t", 0, end)
        if line[start + 1:end] in wanted_reversed:
            row = parse_rank_line(line)
            if row is not None:
                yield row


class _ResumingStream:
    """Byte chunks of a URL. A broken connection is resumed with a Range request from the last byte."""

    def __init__(self, client: httpx.Client, url: str, log: Callable[[str], None] | None = None):
        self.client = client
        self.url = url
        self.log = log
        self.offset = 0
        self.sha256 = hashlib.sha256()
        self.last_modified: str | None = None
        self.etag: str | None = None
        self.total: int | None = None

    def __iter__(self) -> Iterator[bytes]:
        attempts = 0
        connected = False  # a server answered at least once; until then a connection failure is not retried
        while True:
            headers: dict[str, str] = {}
            if self.offset:
                headers["Range"] = f"bytes={self.offset}-"
                if self.etag:
                    headers["If-Range"] = self.etag
            try:
                with self.client.stream("GET", self.url, headers=headers) as response:
                    connected = True
                    if self.offset and response.status_code != 206:
                        raise RuntimeError(f"{self.url} did not resume at byte {self.offset} (HTTP {response.status_code}).")
                    if not self.offset and response.status_code != 200:
                        raise RuntimeError(f"{self.url} answered HTTP {response.status_code}.")
                    if not self.offset:
                        self.last_modified = response.headers.get("last-modified")
                        self.etag = response.headers.get("etag")
                        length = response.headers.get("content-length")
                        self.total = int(length) if length else None
                    for block in response.iter_raw():
                        self.offset += len(block)
                        self.sha256.update(block)
                        yield block
                if self.total is None or self.offset >= self.total:
                    return
                raise httpx.ReadError("stream ended early")
            except httpx.TransportError as error:
                attempts += 1
                if isinstance(error, FIRST_CONNECTION_ERRORS) and not connected and not self.offset:
                    raise _unreachable(self.url, error) from error
                if attempts > MAX_STREAM_RETRIES:
                    raise
                _wait_before_retry(self.log, f"The stream of {self.url} broke at byte {self.offset} ({type(error).__name__}), "
                                             f"attempt {attempts} of {MAX_STREAM_RETRIES}. It resumes from there.",
                                   min(60.0, 2.0 ** attempts))


@dataclass
class CommonCrawlDomainRanks:
    """Common Crawl web graph domain ranks, one snapshot per release, keyed by publication date."""

    root: Path | None = None
    client: httpx.Client | None = None
    log: Callable[[str], None] | None = None
    name: str = "commoncrawl_domain_ranks"
    _loaded: dict[str, tuple[set[str], dict[str, dict[str, Any]]]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = default_root(self.root)

    def _http(self) -> httpx.Client:
        """One client per source object, made on first use (tests inject a mock client instead)."""
        if self.client is None:
            self.client = make_client(timeout=120.0)
        return self.client

    @property
    def cache_dir(self) -> Path:
        return Path(self.root) / "cache" / "strength" / "commoncrawl"

    @staticmethod
    def releases() -> dict[str, dict[str, Any]]:
        """Every known domain-ranks release with crawls, window, publication date, node count and size."""
        out: dict[str, dict[str, Any]] = {}
        for rid, crawls, start, end, published, nodes, size in CC_RELEASES:
            out[rid] = {"crawls": crawls, "data_start": date.fromisoformat(start), "data_end": date.fromisoformat(end),
                        "published": date.fromisoformat(published), "domain_nodes": nodes, "bytes": size,
                        "url": f"{CC_BASE}/{rid}/domain/{rid}-domain-ranks.txt.gz"}
        return out

    @staticmethod
    def fallback_published(data_end: date) -> date:
        """The conservative publication date for a release whose Last-Modified is unknown."""
        return data_end + timedelta(days=CC_FALLBACK_LAG_DAYS)

    def snapshots(self) -> list[Snapshot]:
        return [Snapshot(rid, info["data_start"], info["data_end"], info["published"], info["url"],
                         note=f"crawls {','.join(info['crawls'])}; {info['domain_nodes']} domains")
                for rid, info in sorted(self.releases().items(), key=lambda kv: kv[1]["published"])]

    def releases_between(self, first_month: str, last_month: str) -> list[str]:
        """Releases PUBLISHED in the months first_month to last_month (YYYY-MM, inclusive), oldest first."""
        lo = as_date(first_month)
        hi = add_months(as_date(last_month), 1)
        return [snap.snapshot_id for snap in self.snapshots() if snap.published and lo <= snap.published < hi]

    def _paths(self, release: str) -> tuple[Path, Path]:
        return self.cache_dir / f"{release}.parquet", self.cache_dir / f"{release}.searched.txt.gz"

    def searched(self, release: str) -> set[str]:
        return _read_searched(self._paths(release)[1])

    def materialised_releases(self) -> list[str]:
        known = self.releases()
        return [rid for rid in known if self._paths(rid)[1].exists()]

    def missing_sites(self, sites: Iterable[str], release: str) -> set[str]:
        return wanted_sites(sites) - self.searched(release)

    def materialise(self, sites: Iterable[str], releases: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Stream each release's domain-ranks file once and keep only the wanted sites.

        Resumable and incremental: a release whose searched list already holds every
        wanted site does no network work. When new sites are asked for, the whole
        file is streamed again for the missing sites only (the file is sorted by
        rank, not by domain, so there is no way to read part of it).
        Returns per release: sites asked, found, seconds and bytes streamed.
        """
        wanted = wanted_sites(sites)
        known = self.releases()
        report: dict[str, dict[str, Any]] = {}
        for release in releases:
            if release not in known:
                raise ValueError(f"unknown Common Crawl release {release}")
            parquet_path, searched_path = self._paths(release)
            done = _read_searched(searched_path)
            missing = wanted - done
            if not missing:
                report[release] = {"status": "cached", "asked": len(wanted), "streamed_bytes": 0, "seconds": 0.0}
                continue
            client = self._http()
            started = time.monotonic()
            stream = _ResumingStream(client, known[release]["url"], log=self.log)
            wanted_rev = {reverse_domain(site).encode("utf-8") for site in missing}
            rows: list[dict[str, Any]] = []
            for domain, hc_pos, hc_val, pr_pos, pr_val, n_hosts in filter_rank_lines(stream_gzip_lines(stream), wanted_rev):
                rows.append({"site": domain, "harmonicc_pos": hc_pos, "harmonicc_val": hc_val,
                             "pr_pos": pr_pos, "pr_val": pr_val, "n_hosts": n_hosts})
            old = pq.read_table(parquet_path) if parquet_path.exists() else None
            _write_parquet_atomic(_merge_rows(old, rows, CC_SCHEMA), parquet_path)
            _write_searched(searched_path, done | missing)
            seconds = time.monotonic() - started
            record_source(self.cache_dir, release, {
                "url": known[release]["url"],
                "fetched_at": utc_iso(datetime.now(timezone.utc)),
                "bytes_streamed": stream.offset,
                "sha256_of_stream": stream.sha256.hexdigest() if stream.offset == (stream.total or stream.offset) else None,
                "last_modified": stream.last_modified,
                "published_used": known[release]["published"].isoformat(),
                "sites_searched_total": len(done | missing),
            })
            self._loaded.pop(release, None)
            report[release] = {"status": "streamed", "asked": len(missing), "found": len(rows),
                               "streamed_bytes": stream.offset, "seconds": round(seconds, 1)}
            if self.log:
                self.log(f"{release}: {len(rows)} of {len(missing)} sites found, {stream.offset} bytes, {seconds:.0f} s")
        return report

    def _load(self, release: str) -> tuple[set[str], dict[str, dict[str, Any]]]:
        if release not in self._loaded:
            parquet_path, searched_path = self._paths(release)
            rows: dict[str, dict[str, Any]] = {}
            if parquet_path.exists():
                data = pq.read_table(parquet_path).to_pydict()
                names = list(data)
                for values in zip(*(data[name] for name in names)):
                    row = dict(zip(names, values))
                    rows[row["site"]] = row
            if len(self._loaded) >= 3:
                self._loaded.pop(next(iter(self._loaded)))
            self._loaded[release] = (_read_searched(searched_path), rows)
        return self._loaded[release]

    def lookup(self, sites: Iterable[str], as_of: date | str) -> dict[str, Dated]:
        """Per site, the newest MATERIALISED release published on or before `as_of` that searched it.

        A site searched but not found gets `present` 0.0 and None metrics (no inbound links seen).
        A site never searched in an eligible release is left out. When a newer release was
        published by `as_of` but is not materialised, the older one is used and
        `newer_release_not_materialised` is 1.0, so a stale value is visible, never silent.
        """
        day = as_date(as_of)
        known = self.releases()
        eligible = sorted((rid for rid in self.materialised_releases() if known[rid]["published"] <= day),
                          key=lambda rid: known[rid]["published"], reverse=True)
        newest_published = pick_snapshot(self.snapshots(), day)
        remaining = wanted_sites(sites)
        out: dict[str, Dated] = {}
        for release in eligible:
            if not remaining:
                break
            searched, rows = self._load(release)
            info = known[release]
            stale = 0.0 if newest_published is None or newest_published.snapshot_id == release else 1.0
            for site in list(remaining & searched):
                row = rows.get(site)
                value: dict[str, Any] = {
                    "snapshot_id": release,
                    "data_end": info["data_end"].isoformat(),
                    "newer_release_not_materialised": stale,
                    "present": 1.0 if row else 0.0,
                    "hc_pos": _float(row["harmonicc_pos"]) if row else None,
                    "hc_val": _float(row["harmonicc_val"]) if row else None,
                    "pr_pos": _float(row["pr_pos"]) if row else None,
                    "pr_val": _float(row["pr_val"]) if row else None,
                    "n_hosts": _float(row["n_hosts"]) if row else None,
                    "hc_pos_pct": (row["harmonicc_pos"] / info["domain_nodes"]) if row else None,
                }
                out[site] = Dated(value=value, source=self.name, snapshot_date=info["published"])
                remaining.discard(site)
        return out


# ---------------------------------------------------------------------------
# Open PageRank history


OPR_SCHEMA = pa.schema([
    ("site", pa.string()),
    ("domain", pa.string()),
    ("month", pa.string()),
    ("opr", pa.float64()),
    ("current_rank", pa.int64()),
    ("current_referring_domains", pa.int64()),
])


def opr_month_published(month: str) -> date:
    """A column labelled YYYY-MM is taken as published on the first day of the next month."""
    return add_months(as_date(month), 1)


def parse_opr_rows(lines: Iterable[str], wanted: set[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Read the history CSV (header Rank,Domain,Extension,Open Page Rank,Referring Domains,<months>).

    Returns the month columns and long rows (site, domain, month, opr, ...) for wanted sites.
    An empty month cell means the domain was not ranked that month and gives no row.
    When several domains map to one site, the row whose domain equals the site wins, else the best rank.
    """
    iterator = iter(lines)
    header = next(csv.reader([next(iterator)]))
    months = [name.strip() for name in header[5:] if name.strip()]
    best: dict[str, tuple[tuple[int, int], list[str]]] = {}
    for line in iterator:
        first = line.find(",")
        second = line.find(",", first + 1)
        if first < 0 or second < 0:
            continue
        domain = line[first + 1:second].strip().lower()
        site = normalise_site(domain)
        if site is None or site not in wanted:
            continue
        fields = next(csv.reader([line]))
        try:
            rank = int(fields[0])
        except ValueError:
            continue
        score = (0 if domain == site else 1, rank)
        if site not in best or score < best[site][0]:
            best[site] = (score, fields)
    rows: list[dict[str, Any]] = []
    for site, (_, fields) in sorted(best.items()):
        domain = fields[1].strip().lower()
        current_rank = int(fields[0])
        referring = int(fields[4]) if fields[4].strip().isdigit() else None
        for month, cell in zip(months, fields[5:]):
            value = _float(cell.strip()) if cell.strip() else None
            if value is None:
                continue
            rows.append({"site": site, "domain": domain, "month": month, "opr": value,
                         "current_rank": current_rank, "current_referring_domains": referring})
    return months, rows


@dataclass
class OpenPageRankHistory:
    """Open PageRank top 10 million, one score per month. Survivorship limited: see `SURVIVORSHIP_LIMITED`."""

    root: Path | None = None
    client: httpx.Client | None = None
    log: Callable[[str], None] | None = None
    name: str = "openpagerank_history"
    survivorship_limited: bool = True
    survivorship_note: str = OPR_SURVIVORSHIP_NOTE

    def __post_init__(self) -> None:
        self.root = default_root(self.root)

    def _http(self) -> httpx.Client:
        """One client per source object, made on first use (tests inject a mock client instead)."""
        if self.client is None:
            self.client = make_client(timeout=120.0)
        return self.client

    @property
    def download_path(self) -> Path:
        return Path(self.root) / "public" / "openpagerank" / OPR_HISTORY_FILE

    @property
    def cache_dir(self) -> Path:
        return Path(self.root) / "cache" / "strength" / "openpagerank"

    @property
    def parquet_path(self) -> Path:
        return self.cache_dir / "opr_history.parquet"

    @property
    def searched_path(self) -> Path:
        return self.cache_dir / "opr_history.searched.txt.gz"

    @property
    def months_path(self) -> Path:
        return self.cache_dir / "months.json"

    def download(self) -> dict[str, Any]:
        """The history zip (about 952 MB), fetched once with a resumable Range download."""
        client = self._http()
        meta = download_resumable(client, OPR_HISTORY_URL, self.download_path, log=self.log)
        record_source(self.download_path.parent, OPR_HISTORY_FILE, meta)
        return meta

    def months(self) -> list[str]:
        if self.months_path.exists():
            return json.loads(self.months_path.read_text(encoding="utf-8"))
        return []

    def snapshots(self) -> list[Snapshot]:
        out = []
        for month in self.months():
            start = as_date(month)
            out.append(Snapshot(f"opr-{month}", start, add_months(start, 1) - timedelta(days=1),
                                opr_month_published(month), OPR_HISTORY_URL, note=OPR_SURVIVORSHIP_NOTE))
        return out

    def materialise(self, sites: Iterable[str]) -> dict[str, Any]:
        """Stream the inner CSV of the local zip and keep every dated score of the wanted sites.

        Incremental: sites already searched are skipped. New sites mean one more pass
        over the local zip (2.5 GB of CSV inflated on the fly). No network is used once
        the zip is on disk.
        """
        wanted = wanted_sites(sites)
        done = _read_searched(self.searched_path)
        missing = wanted - done
        if not missing:
            return {"status": "cached", "asked": len(wanted)}
        if not self.download_path.exists():
            self.download()
        started = time.monotonic()
        with zipfile.ZipFile(self.download_path) as archive:
            member = archive.namelist()[0]
            with archive.open(member) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
                months, rows = parse_opr_rows(text, missing)
        old = pq.read_table(self.parquet_path) if self.parquet_path.exists() else None
        _write_parquet_atomic(_merge_rows(old, rows, OPR_SCHEMA), self.parquet_path)
        self.months_path.parent.mkdir(parents=True, exist_ok=True)
        self.months_path.write_text(json.dumps(months), encoding="utf-8")
        _write_searched(self.searched_path, done | missing)
        meta_path = self.download_path.with_name(self.download_path.name + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        record_source(self.cache_dir, "opr_history", {"read_from": str(self.download_path), "url": OPR_HISTORY_URL,
                                                      "sha256": meta.get("sha256"), "built_at": utc_iso(datetime.now(timezone.utc)),
                                                      "months": months, "survivorship": OPR_SURVIVORSHIP_NOTE})
        found = len({row["site"] for row in rows})
        return {"status": "streamed", "asked": len(missing), "found": found, "seconds": round(time.monotonic() - started, 1)}

    def history(self, sites: Iterable[str] | None = None) -> dict[str, dict[str, float]]:
        """site -> {month: opr} from the materialised parquet, for the given sites (all when None)."""
        if not self.parquet_path.exists():
            return {}
        table = pq.read_table(self.parquet_path, columns=["site", "month", "opr"])
        if sites is not None:
            table = table.filter(pc.is_in(table.column("site"), value_set=pa.array(sorted(wanted_sites(sites)), type=pa.string())))
        out: dict[str, dict[str, float]] = {}
        data = table.to_pydict()
        for site, month, value in zip(data["site"], data["month"], data["opr"]):
            out.setdefault(site, {})[month] = value
        return out

    def month_scores(self, month: str) -> dict[str, float]:
        """site -> score for one month column, read with a parquet filter (no full load)."""
        if not self.parquet_path.exists():
            return {}
        data = pq.read_table(self.parquet_path, columns=["site", "opr"], filters=[("month", "=", month)]).to_pydict()
        return dict(zip(data["site"], data["opr"]))

    def first_months(self, sites: Iterable[str] | None = None) -> dict[str, str]:
        """site -> the first month with a score."""
        if not self.parquet_path.exists():
            return {}
        table = pq.read_table(self.parquet_path, columns=["site", "month"])
        if sites is not None:
            table = table.filter(pc.is_in(table.column("site"), value_set=pa.array(sorted(wanted_sites(sites)), type=pa.string())))
        grouped = table.group_by("site").aggregate([("month", "min")]).to_pydict()
        return dict(zip(grouped["site"], grouped["month_min"]))

    def lookup(self, sites: Iterable[str], as_of: date | str) -> dict[str, Dated]:
        """Per searched site, the score in the newest month column published on or before `as_of`.

        A searched site that is in the file but has no score in that month gets
        `present` 0.0 and opr None: a real reading of that month's column. A
        searched site with no row anywhere in the file gets `present` None and
        opr None: the file only holds the 2026-10 top 10 million (survivorship),
        so it cannot say whether that site was listed in an earlier month, and a
        0 would be made up from a 2026 fact.
        """
        snap = pick_snapshot(self.snapshots(), as_of)
        if snap is None:
            return {}
        month = snap.snapshot_id.removeprefix("opr-")
        searched = _read_searched(self.searched_path)
        asked = wanted_sites(sites) & searched
        scores = self.month_scores(month)
        in_file = set(self.first_months(asked)) if asked else set()
        out: dict[str, Dated] = {}
        for site in asked:
            value = scores.get(site)
            present = None if site not in in_file else (1.0 if value is not None else 0.0)
            out[site] = Dated(value={"snapshot_id": snap.snapshot_id, "present": present,
                                     "opr": value, "survivorship_limited": 1.0}, source=self.name, snapshot_date=snap.published)
        return out


# ---------------------------------------------------------------------------
# Chrome UX Report top lists (zakird mirror)


CRUX_SCHEMA = pa.schema([
    ("site", pa.string()),
    ("bucket", pa.int64()),
    ("bucket_coarse", pa.int64()),
    ("n_origins", pa.int64()),
])


def crux_month_published(month: str) -> date:
    """File YYYYMM (or YYYY-MM) is taken as published on day 15 of the following month."""
    text = month.replace("-", "")
    start = date(int(text[:4]), int(text[4:6]), 1)
    return add_months(start, 1).replace(day=CRUX_PUBLISH_DAY)


def crux_coarse_bucket(bucket: int | None) -> int | None:
    """Map a bucket to the old 1k/10k/100k/1M scheme: 5k -> 10k, 50k -> 100k, 500k -> 1M. Unknown -> None."""
    if bucket is None:
        return None
    for coarse in CRUX_COARSE_BUCKETS:
        if bucket <= coarse:
            return coarse
    return None


def origin_site(origin: str) -> str | None:
    """https://www.example.co.uk:8443 -> example.co.uk."""
    text = origin.strip()
    host = urlsplit(text).hostname if "://" in text else text.split("/")[0].split(":")[0]
    return normalise_site(host)


def parse_crux_rows(lines: Iterable[str], wanted: set[str] | None) -> tuple[dict[str, tuple[int, int]], str]:
    """Best (lowest) bucket per wanted site across its origins, plus the bucket scheme seen ('fine' or 'coarse')."""
    iterator = iter(lines)
    header = next(iterator).strip().lower()
    if header != "origin,rank":
        raise ValueError(f"unexpected CrUX header {header!r}")
    best: dict[str, tuple[int, int]] = {}
    scheme = "coarse"
    for line in iterator:
        origin, _, rank = line.strip().rpartition(",")
        if not origin or not rank.isdigit():
            continue
        bucket = int(rank)
        if bucket in CRUX_FINE_ONLY_BUCKETS:
            scheme = "fine"
        site = origin_site(origin)
        if site is None or (wanted is not None and site not in wanted):
            continue
        if site in best:
            old_bucket, count = best[site]
            best[site] = (min(old_bucket, bucket), count + 1)
        else:
            best[site] = (bucket, 1)
    return best, scheme


@dataclass
class CruxTopLists:
    """Chrome UX Report global top 1M origins per month, as a best bucket per registrable site."""

    root: Path | None = None
    client: httpx.Client | None = None
    log: Callable[[str], None] | None = None
    name: str = "crux_top_lists"

    def __post_init__(self) -> None:
        self.root = default_root(self.root)

    def _http(self) -> httpx.Client:
        """One client per source object, made on first use (tests inject a mock client instead)."""
        if self.client is None:
            self.client = make_client(timeout=120.0)
        return self.client

    @property
    def public_dir(self) -> Path:
        return Path(self.root) / "public" / "crux" / "global"

    @property
    def cache_dir(self) -> Path:
        return Path(self.root) / "cache" / "strength" / "crux"

    def _paths(self, month: str) -> tuple[Path, Path, Path]:
        key = month.replace("-", "")
        return (self.public_dir / f"{key}.csv.gz", self.cache_dir / f"{key}.parquet", self.cache_dir / f"{key}.searched.txt.gz")

    def materialised_months(self) -> list[str]:
        if not self.cache_dir.exists():
            return []
        return sorted(path.name.split(".")[0] for path in self.cache_dir.glob("*.searched.txt.gz"))

    def snapshots(self) -> list[Snapshot]:
        out = []
        for key in self.materialised_months():
            start = date(int(key[:4]), int(key[4:6]), 1)
            out.append(Snapshot(f"crux-{key}", start, add_months(start, 1) - timedelta(days=1), crux_month_published(key),
                                CRUX_URL.format(month=key)))
        return out

    def download(self, month: str) -> dict[str, Any]:
        raw_path = self._paths(month)[0]
        client = self._http()
        meta = download_resumable(client, CRUX_URL.format(month=month.replace("-", "")), raw_path, log=self.log)
        record_source(self.public_dir, raw_path.name, meta)
        return meta

    def materialise(self, sites: Iterable[str], months: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Download each month once and keep the wanted sites. Rerunning for new sites rereads the local file only."""
        wanted = wanted_sites(sites)
        report: dict[str, dict[str, Any]] = {}
        for month in months:
            key = month.replace("-", "")
            raw_path, parquet_path, searched_path = self._paths(key)
            done = _read_searched(searched_path)
            missing = wanted - done
            if not missing:
                report[key] = {"status": "cached", "asked": len(wanted)}
                continue
            started = time.monotonic()
            if not raw_path.exists():
                self.download(key)
            with gzip.open(raw_path, "rt", encoding="utf-8", errors="replace") as handle:
                best, scheme = parse_crux_rows(handle, missing)
            rows = [{"site": site, "bucket": bucket, "bucket_coarse": crux_coarse_bucket(bucket), "n_origins": count}
                    for site, (bucket, count) in sorted(best.items())]
            old = pq.read_table(parquet_path) if parquet_path.exists() else None
            _write_parquet_atomic(_merge_rows(old, rows, CRUX_SCHEMA), parquet_path)
            _write_searched(searched_path, done | missing)
            record_source(self.cache_dir, key, {"url": CRUX_URL.format(month=key), "built_at": utc_iso(datetime.now(timezone.utc)),
                                                "bucket_scheme": scheme, "published_used": crux_month_published(key).isoformat()})
            report[key] = {"status": "built", "asked": len(missing), "found": len(rows), "scheme": scheme,
                           "seconds": round(time.monotonic() - started, 1)}
        return report

    def lookup(self, sites: Iterable[str], as_of: date | str) -> dict[str, Dated]:
        snap = pick_snapshot(self.snapshots(), as_of)
        if snap is None:
            return {}
        key = snap.snapshot_id.removeprefix("crux-")
        _, parquet_path, searched_path = self._paths(key)
        searched = _read_searched(searched_path)
        rows = {row["site"]: row for row in pq.read_table(parquet_path).to_pylist()} if parquet_path.exists() else {}
        out: dict[str, Dated] = {}
        for site in wanted_sites(sites) & searched:
            row = rows.get(site)
            out[site] = Dated(value={"snapshot_id": snap.snapshot_id, "present": 1.0 if row else 0.0,
                                     "bucket": _float(row["bucket"]) if row else None,
                                     "bucket_coarse": _float(row["bucket_coarse"]) if row else None},
                              source=self.name, snapshot_date=snap.published)
        return out


# ---------------------------------------------------------------------------
# Tranco


TRANCO_SCHEMA = pa.schema([("site", pa.string()), ("rank", pa.int64())])


def parse_tranco_rows(lines: Iterable[str], wanted: set[str]) -> dict[str, int]:
    """rank,domain rows (no header). The best rank per wanted site."""
    best: dict[str, int] = {}
    for line in lines:
        rank_text, _, domain = line.strip().partition(",")
        if not rank_text.isdigit():
            continue
        site = normalise_site(domain)
        if site is None or site not in wanted:
            continue
        rank = int(rank_text)
        if site not in best or rank < best[site]:
            best[site] = rank
    return best


@dataclass
class TrancoByDate:
    """Tranco research top-1M list for a date. The first list of each month bounds the work."""

    root: Path | None = None
    client: httpx.Client | None = None
    log: Callable[[str], None] | None = None
    name: str = "tranco"
    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(TRANCO_MIN_INTERVAL_SECONDS))

    def __post_init__(self) -> None:
        self.root = default_root(self.root)

    def _http(self) -> httpx.Client:
        """One client per source object, made on first use (tests inject a mock client instead)."""
        if self.client is None:
            self.client = make_client(timeout=120.0)
        return self.client

    @property
    def public_dir(self) -> Path:
        return Path(self.root) / "public" / "tranco"

    @property
    def cache_dir(self) -> Path:
        return Path(self.root) / "cache" / "strength" / "tranco"

    def _index_path(self) -> Path:
        return self.cache_dir / "lists.json"

    def _index(self) -> dict[str, dict[str, Any]]:
        path = self._index_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def list_for_date(self, day: date | str) -> dict[str, Any]:
        """The Tranco API record for a date (list id, download URL, created_on, configuration). Cached."""
        key = as_date(day).isoformat()
        index = self._index()
        if key in index:
            return index[key]
        client = self._http()
        url = TRANCO_API.format(day=key)
        self.limiter.wait(url)
        try:
            response = client.get(url)
        except FIRST_CONNECTION_ERRORS as error:
            raise _unreachable(url, error) from error
        response.raise_for_status()
        record = response.json()
        if not record.get("available", False) or not record.get("list_id"):
            raise ValueError(f"no Tranco list for {key}")
        index[key] = record
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(json.dumps(index, indent=1, sort_keys=True), encoding="utf-8")
        return record

    def snapshots(self) -> list[Snapshot]:
        out = []
        for key, record in sorted(self._index().items()):
            config = record.get("configuration") or {}
            created = as_date(record["created_on"]) if record.get("created_on") else None
            out.append(Snapshot(f"tranco-{record['list_id']}", as_date(config["startDate"]) if config.get("startDate") else None,
                                as_date(config["endDate"]) if config.get("endDate") else as_date(key), created,
                                record.get("download"), note=",".join(config.get("providers", []))))
        return out

    def materialise(self, sites: Iterable[str], months: Sequence[str]) -> dict[str, dict[str, Any]]:
        wanted = wanted_sites(sites)
        report: dict[str, dict[str, Any]] = {}
        for month in months:
            record = self.list_for_date(as_date(month))
            list_id = record["list_id"]
            parquet_path = self.cache_dir / f"{list_id}.parquet"
            searched_path = self.cache_dir / f"{list_id}.searched.txt.gz"
            done = _read_searched(searched_path)
            missing = wanted - done
            if not missing:
                report[month] = {"status": "cached", "list_id": list_id}
                continue
            raw_path = self.public_dir / f"tranco_{list_id}.csv"
            if not raw_path.exists():
                meta = download_resumable(self._http(), record["download"], raw_path, limiter=self.limiter, log=self.log)
                record_source(self.public_dir, raw_path.name, meta)
            with open(raw_path, encoding="utf-8", errors="replace") as handle:
                best = parse_tranco_rows(handle, missing)
            rows = [{"site": site, "rank": rank} for site, rank in sorted(best.items())]
            old = pq.read_table(parquet_path) if parquet_path.exists() else None
            _write_parquet_atomic(_merge_rows(old, rows, TRANCO_SCHEMA), parquet_path)
            _write_searched(searched_path, done | missing)
            report[month] = {"status": "built", "list_id": list_id, "asked": len(missing), "found": len(rows)}
        return report

    def lookup(self, sites: Iterable[str], as_of: date | str) -> dict[str, Dated]:
        snap = pick_snapshot(self.snapshots(), as_of)
        if snap is None:
            return {}
        list_id = snap.snapshot_id.removeprefix("tranco-")
        parquet_path = self.cache_dir / f"{list_id}.parquet"
        searched = _read_searched(self.cache_dir / f"{list_id}.searched.txt.gz")
        rows = {row["site"]: row["rank"] for row in pq.read_table(parquet_path).to_pylist()} if parquet_path.exists() else {}
        out: dict[str, Dated] = {}
        for site in wanted_sites(sites) & searched:
            rank = rows.get(site)
            out[site] = Dated(value={"snapshot_id": snap.snapshot_id, "present": 1.0 if rank else 0.0,
                                     "rank": _float(rank)}, source=self.name, snapshot_date=snap.published)
        return out


# ---------------------------------------------------------------------------
# Majestic Million (undated)


def parse_majestic_rows(lines: Iterable[str], wanted: set[str]) -> dict[str, dict[str, float | None]]:
    """GlobalRank,TldRank,Domain,TLD,RefSubNets,RefIPs,... rows for wanted sites."""
    reader = csv.DictReader(lines)
    out: dict[str, dict[str, float | None]] = {}
    for row in reader:
        site = normalise_site(row.get("Domain"))
        if site is None or site not in wanted:
            continue
        rank = _float(row.get("GlobalRank"))
        if site in out and (out[site]["rank"] or 0) <= (rank or 0):
            continue
        out[site] = {"rank": rank, "ref_subnets": _float(row.get("RefSubNets")), "ref_ips": _float(row.get("RefIPs"))}
    return out


@dataclass
class MajesticToday:
    """Today's Majestic Million. It has no dated history, so every value is undated (snapshot_date None)."""

    path: Path | None = None
    name: str = "majestic_million_today"

    @staticmethod
    def fetch(root: str | Path | None = None, client: httpx.Client | None = None) -> "MajesticToday":
        """Download today's file (about 80 MB) to public/majestic/ with provenance, and return a source on it."""
        today = datetime.now(timezone.utc).date().isoformat()
        dest = default_root(root) / "public" / "majestic" / f"majestic_million_{today}.csv"
        meta = download_resumable(client or make_client(timeout=120.0), MAJESTIC_URL, dest)
        record_source(dest.parent, dest.name, {**meta, "dated": False})
        return MajesticToday(path=dest)

    def snapshots(self) -> list[Snapshot]:
        return [Snapshot("majestic-today", None, None, None, MAJESTIC_URL, note="undated: only the current file exists")]

    def lookup(self, sites: Iterable[str], as_of: date | str | None = None) -> dict[str, Dated]:
        """Values from the local file at `path`. `as_of` is ignored: the guard decides whether undated is allowed."""
        if self.path is None or not Path(self.path).exists():
            return {}
        wanted = wanted_sites(sites)
        with open(self.path, encoding="utf-8", errors="replace", newline="") as handle:
            found = parse_majestic_rows(handle, wanted)
        return {site: Dated(value={"snapshot_id": "majestic-today", "present": 1.0, **values}, source=self.name, snapshot_date=None)
                for site, values in found.items()}


# ---------------------------------------------------------------------------
# Combined features


FEATURE_PREFIX: dict[str, str] = {
    "commoncrawl_domain_ranks": "cc",
    "openpagerank_history": "opr",
    "crux_top_lists": "crux",
    "tranco": "tranco",
    "majestic_million_today": "majestic",
}

FEATURE_KEYS: dict[str, tuple[str, ...]] = {
    "commoncrawl_domain_ranks": ("present", "hc_pos", "hc_val", "pr_pos", "pr_val", "n_hosts", "hc_pos_pct",
                                 "newer_release_not_materialised"),
    "openpagerank_history": ("present", "opr", "survivorship_limited"),
    "crux_top_lists": ("present", "bucket", "bucket_coarse"),
    "tranco": ("present", "rank"),
    "majestic_million_today": ("present", "rank", "ref_subnets", "ref_ips"),
}


def default_sources(root: str | Path | None = None) -> list[StrengthSource]:
    """The dated sources used in backtests. Majestic is not among them: add it only for live forecasts."""
    base = default_root(root)
    return [CommonCrawlDomainRanks(base), OpenPageRankHistory(base), CruxTopLists(base), TrancoByDate(base)]


def strength_features(
    sites: Iterable[str],
    as_of: date | str,
    guard: AsOfGuard,
    sources: Sequence[StrengthSource] | None = None,
    root: str | Path | None = None,
) -> dict[str, dict[str, float | None]]:
    """Per site, a flat dict of floats (None when missing). Every value goes through `guard.take`.

    Keys are `<prefix>_<metric>` plus `<prefix>_snapshot_age_days` (days from publication to as_of).
    A later or undated snapshot raises `asof.LeakError` from the guard. It is never dropped silently.
    """
    day = as_date(as_of)
    if guard.as_of != day:
        raise ValueError(f"the guard is for {guard.as_of} but features were asked for {day}")
    chosen = list(sources) if sources is not None else default_sources(root)
    site_list = sorted(wanted_sites(sites))
    out: dict[str, dict[str, float | None]] = {site: {} for site in site_list}
    for source in chosen:
        prefix = FEATURE_PREFIX.get(source.name, source.name)
        keys = FEATURE_KEYS.get(source.name, ("present",))
        found = source.lookup(site_list, day)
        for site in site_list:
            dated = found.get(site)
            value = guard.take(dated)
            row = out[site]
            for key in keys:
                row[f"{prefix}_{key}"] = _float(value.get(key)) if isinstance(value, dict) else None
            age = (day - dated.snapshot_date).days if dated is not None and dated.snapshot_date is not None else None
            row[f"{prefix}_snapshot_age_days"] = float(age) if age is not None else None
    return out


# ---------------------------------------------------------------------------
# Panel sites and the CLI


def extract_panel_sites(paths: Iterable[Path], organic_only: bool = True) -> tuple[set[str], dict[str, Any]]:
    """Distinct registrable sites of `main_domain` over DataForSEO history sample files (JSON lines, gzip).

    Streams one keyword object per line. Excluded sites are dropped.
    """
    sites: set[str] = set()
    stats = {"files": 0, "keywords": 0, "snapshots": 0, "items": 0, "excluded": 0, "no_main_domain": 0}
    for path in sorted(paths):
        stats["files"] += 1
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                stats["keywords"] += 1
                for snapshot in record.get("serp_info_history") or []:
                    stats["snapshots"] += 1
                    for item in snapshot.get("serp") or []:
                        if organic_only and item.get("type") != "organic":
                            continue
                        stats["items"] += 1
                        main = item.get("main_domain") or item.get("domain")
                        if not main:
                            stats["no_main_domain"] += 1
                            continue
                        raw = site_key(str(main).lower())
                        if raw in EXCLUDED_SITES:
                            stats["excluded"] += 1
                            continue
                        site = normalise_site(main)
                        if site:
                            sites.add(site)
    stats["sites"] = len(sites)
    return sites, stats


def read_sites_file(path: str | Path) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.startswith("#")]


def _months(first: str, last: str) -> list[str]:
    out = []
    current = as_date(first)
    end = as_date(last)
    while current <= end:
        out.append(current.strftime("%Y%m"))
        current = add_months(current, 1)
    return out


def _log(message: str) -> None:
    print(f"{utc_iso(datetime.now(timezone.utc))} {message}", flush=True)


def coverage_report(sites: Iterable[str], root: str | Path | None = None) -> list[dict[str, Any]]:
    """Per materialised snapshot: sites asked, searched, found, and the share found. Reads the cache only."""
    base = default_root(root)
    wanted = wanted_sites(sites)
    rows: list[dict[str, Any]] = []

    def add(source: str, snapshot: str, published: date | None, searched_path: Path, parquet_path: Path) -> None:
        searched = _read_searched(searched_path) & wanted
        found: set[str] = set()
        if parquet_path.exists():
            found = set(pq.read_table(parquet_path, columns=["site"]).column("site").to_pylist()) & searched
        rows.append({"source": source, "snapshot": snapshot, "published": published.isoformat() if published else None,
                     "asked": len(wanted), "searched": len(searched), "found": len(found),
                     "share_found": round(len(found) / len(searched), 4) if searched else None})

    cc = CommonCrawlDomainRanks(base)
    known = cc.releases()
    for release in sorted(cc.materialised_releases(), key=lambda rid: known[rid]["published"]):
        parquet_path, searched_path = cc._paths(release)
        add(cc.name, release, known[release]["published"], searched_path, parquet_path)
    opr = OpenPageRankHistory(base)
    if opr.searched_path.exists():
        add(opr.name, "history (any month)", None, opr.searched_path, opr.parquet_path)
    crux = CruxTopLists(base)
    for key in crux.materialised_months():
        _, parquet_path, searched_path = crux._paths(key)
        add(crux.name, key, crux_month_published(key), searched_path, parquet_path)
    tranco = TrancoByDate(base)
    for snap in tranco.snapshots():
        list_id = snap.snapshot_id.removeprefix("tranco-")
        add(tranco.name, list_id, snap.published, tranco.cache_dir / f"{list_id}.searched.txt.gz", tranco.cache_dir / f"{list_id}.parquet")
    return rows


# ---------------------------------------------------------------------------
# Complete reads only (forecast strength sync)
#
# A site is marked searched in a release only when the release was read to its end. A stream
# that stops early would otherwise mark sites searched that were never compared with the rest of
# the file, and absence would then look like the list's floor.


class IncompleteRead(ValueError):
    """A list file or stream ended before its end. Nothing read from it is marked searched."""


def stream_gzip_lines_strict(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Lines of a gzip stream, like stream_gzip_lines, but IncompleteRead when it stops inside a member.

    An empty stream is incomplete too: a release file always holds at least one member.
    """
    decoder = zlib.decompressobj(wbits=47)
    pending = b""
    inside = False
    members = 0
    for chunk in chunks:
        data = chunk
        while data:
            inside = True
            out = decoder.decompress(data)
            if out:
                pending += out
                if b"\n" in pending:
                    lines = pending.split(b"\n")
                    pending = lines.pop()
                    yield from lines
            if decoder.eof:
                members += 1
                inside = False
                data = decoder.unused_data
                decoder = zlib.decompressobj(wbits=47)
            else:
                data = b""
    if inside or members == 0:
        raise IncompleteRead("the gzip stream ended before its end marker")
    if pending:
        yield from pending.split(b"\n")


def materialise_cc_release_strict(cc: CommonCrawlDomainRanks, sites: Iterable[str], release: str) -> dict[str, Any]:
    """CommonCrawlDomainRanks.materialise for one release, marking sites searched only after a complete read.

    The stream must reach the gzip end marker and, when the server gave a length, every byte of it.
    On IncompleteRead, an HTTP error or a broken stream nothing is written: the sites stay unsearched.
    """
    wanted = wanted_sites(sites)
    known = cc.releases()
    if release not in known:
        raise ValueError(f"unknown Common Crawl release {release}")
    parquet_path, searched_path = cc._paths(release)
    done = _read_searched(searched_path)
    missing = wanted - done
    if not missing:
        return {"status": "cached", "asked": len(wanted), "streamed_bytes": 0, "seconds": 0.0}
    started = time.monotonic()
    stream = _ResumingStream(cc._http(), known[release]["url"], log=cc.log)
    wanted_rev = {reverse_domain(site).encode("utf-8") for site in missing}
    rows: list[dict[str, Any]] = []
    for domain, hc_pos, hc_val, pr_pos, pr_val, n_hosts in filter_rank_lines(stream_gzip_lines_strict(stream), wanted_rev):
        rows.append({"site": domain, "harmonicc_pos": hc_pos, "harmonicc_val": hc_val,
                     "pr_pos": pr_pos, "pr_val": pr_val, "n_hosts": n_hosts})
    if stream.total is not None and stream.offset < stream.total:
        raise IncompleteRead(f"{release}: {stream.offset} of {stream.total} bytes were read")
    old = pq.read_table(parquet_path) if parquet_path.exists() else None
    _write_parquet_atomic(_merge_rows(old, rows, CC_SCHEMA), parquet_path)
    _write_searched(searched_path, done | missing)
    seconds = time.monotonic() - started
    record_source(cc.cache_dir, release, {
        "url": known[release]["url"],
        "fetched_at": utc_iso(datetime.now(timezone.utc)),
        "bytes_streamed": stream.offset,
        "sha256_of_stream": stream.sha256.hexdigest(),
        "last_modified": stream.last_modified,
        "published_used": known[release]["published"].isoformat(),
        "sites_searched_total": len(done | missing),
        "read_to_end": True,
    })
    cc._loaded.pop(release, None)
    return {"status": "streamed", "asked": len(missing), "found": len(rows), "streamed_bytes": stream.offset,
            "seconds": round(seconds, 1)}


def gzip_file_complete(path: str | Path) -> None:
    """Read a local gzip file to its end. IncompleteRead when it is cut short or is not gzip."""
    try:
        with gzip.open(path, "rb") as handle:
            while handle.read(CHUNK_BYTES):
                pass
    except (EOFError, zlib.error, OSError) as error:
        raise IncompleteRead(f"{path} is not a complete gzip file ({error})") from error


def tranco_file_complete(path: str | Path, expected_rows: int | None = None) -> int:
    """Check a downloaded Tranco CSV: ranks run 1, 2, 3 ... with no gap, and `expected_rows` rows when given.

    Returns the row count. IncompleteRead otherwise, so a cut download is never read as the whole list.
    """
    count = 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rank_text, _, domain = text.partition(",")
            if not rank_text.isdigit() or int(rank_text) != count + 1 or not domain:
                raise IncompleteRead(f"{path}: row {count + 1} is {text[:60]!r}, not rank {count + 1}")
            count += 1
    if count == 0 or (expected_rows is not None and count != expected_rows):
        raise IncompleteRead(f"{path} holds {count} rows, not {expected_rows if expected_rows is not None else 'any'}")
    return count


def crux_candidate_snapshots(as_of: date | str, months_back: int = 24) -> list[Snapshot]:
    """Every CrUX month from `months_back` months before `as_of` to its own month, published or not yet.

    pick_snapshot over this list gives the newest month published on or before `as_of`; a month
    published later is in the list and is never picked.
    """
    day = as_date(as_of)
    out = []
    for step in range(months_back, -1, -1):
        start = add_months(day, -step)
        key = start.strftime("%Y%m")
        out.append(Snapshot(f"crux-{key}", start, add_months(start, 1) - timedelta(days=1), crux_month_published(key),
                            CRUX_URL.format(month=key)))
    return out


def tranco_candidate_snapshots(as_of: date | str, index: dict[str, dict[str, Any]], months_back: int = 24) -> list[Snapshot]:
    """The first-of-month Tranco lists from `months_back` months before `as_of` to its own month.

    A list already in the cached API index carries its own created_on date. One not fetched yet is
    taken as created on its day (every cached list was created that day at 22:00 UTC) and has no id.
    """
    day = as_date(as_of)
    out = []
    for step in range(months_back, -1, -1):
        first = add_months(day, -step)
        record = index.get(first.isoformat())
        if record and record.get("created_on"):
            out.append(Snapshot(f"tranco-{record['list_id']}", None, first, as_date(record["created_on"]), record.get("download"),
                                note=first.isoformat()))
        else:
            out.append(Snapshot(f"tranco-for-{first.isoformat()}", None, first, first, None, note=first.isoformat()))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.sources.strength_lists",
                                     description="Materialise dated site-strength lists for a list of sites.")
    sub = parser.add_subparsers(dest="command", required=True)
    panel = sub.add_parser("panel-sites", help="extract the distinct sites of the DataForSEO history samples")
    panel.add_argument("--glob", default="data/forecast/public/dataforseo_samples/google_history/serp_advanced/*.json.gz")
    panel.add_argument("--out", default="data/forecast/cache/strength/panel_sites.txt")
    mat = sub.add_parser("materialise", help="stream the dated lists and keep the wanted sites")
    mat.add_argument("--sites", required=True)
    mat.add_argument("--from", dest="first", required=True, help="first month YYYY-MM")
    mat.add_argument("--to", dest="last", required=True, help="last month YYYY-MM")
    mat.add_argument("--sources", default="commoncrawl,openpagerank,crux",
                     help="comma list of commoncrawl, openpagerank, crux, tranco")
    mat.add_argument("--root", default=None)
    mat.add_argument("--extra-release", action="append", default=[], help="also materialise this Common Crawl release")
    cov = sub.add_parser("coverage", help="share of the sites found in each materialised snapshot (cache only)")
    cov.add_argument("--sites", required=True)
    cov.add_argument("--root", default=None)
    args = parser.parse_args(argv)

    if args.command == "coverage":
        for row in coverage_report(read_sites_file(args.sites), args.root):
            print(json.dumps(row, sort_keys=True))
        return 0

    if args.command == "panel-sites":
        from glob import glob

        paths = [Path(item) for item in glob(args.glob)]
        sites, stats = extract_panel_sites(paths)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(f"{site}\n" for site in sorted(sites)), encoding="utf-8")
        print(json.dumps(stats, sort_keys=True))
        return 0

    sites = read_sites_file(args.sites)
    chosen = {item.strip() for item in args.sources.split(",") if item.strip()}
    _log(f"{len(wanted_sites(sites))} sites, months {args.first} to {args.last}, sources {sorted(chosen)}")
    if "crux" in chosen:
        crux = CruxTopLists(args.root, log=_log)
        for month in _months(args.first, args.last):
            try:
                _log(f"crux {month}: {json.dumps(crux.materialise(sites, [month]).get(month))}")
            except httpx.HTTPError as error:
                _log(f"crux {month}: failed ({error})")
    if "tranco" in chosen:
        tranco = TrancoByDate(args.root, log=_log)
        for month in _months(args.first, args.last):
            day = f"{month[:4]}-{month[4:]}-01"
            try:
                _log(f"tranco {day}: {json.dumps(tranco.materialise(sites, [day]).get(day))}")
            except (httpx.HTTPError, ValueError) as error:
                _log(f"tranco {day}: failed ({error})")
    if "openpagerank" in chosen:
        opr = OpenPageRankHistory(args.root, log=_log)
        _log(f"openpagerank: {json.dumps(opr.materialise(sites))}")
    if "commoncrawl" in chosen:
        cc = CommonCrawlDomainRanks(args.root, log=_log)
        releases = cc.releases_between(args.first, args.last) + list(args.extra_release)
        _log(f"commoncrawl releases: {releases}")
        for release in releases:
            try:
                _log(f"commoncrawl {release}: {json.dumps(cc.materialise(sites, [release]).get(release))}")
            except (httpx.HTTPError, RuntimeError) as error:
                _log(f"commoncrawl {release}: failed ({error})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
