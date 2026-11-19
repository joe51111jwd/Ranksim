"""Round 2: free Wayback Machine backfill of earlier versions of tracked pages.

URL list: every distinct URL in a top-10 SERP row of the SmartCrew searches
(rsdata groups o30 then a70) with snapshot_date <= DESIGN_MAX_DATE. Rows dated
after that are sealed and never read.

Steps, all resumable and safe to rerun:
  cdx       one CDX query per URL (window 2026-10-01..2026-11-17, status 200,
            collapse=digest). Raw responses go to cdx.jsonl keyed by sha1(url).
  download  the original bytes (``id_`` replay form) of up to 6 distinct-digest
            captures per URL (first, last, evenly spaced). One new ``pages`` row
            per capture through lib.db.insert_page, in an IMMEDIATE transaction
            with the busy timeout and retry of refetch_html.write_batch.
  run       cdx, then download.
  probe     fallback when web.archive.org cannot be reached: the official
            availability API on archive.org, three probe timestamps per URL.
            Gives capture timestamps only (no digest, no content).
  measure   counts on design days only (pairs, material changes, usable rows,
            same-day cross-source noise floor). Writes measure.json + CSVs.

Row convention for captures stored here:
  pages.fetched_at_utc = the Wayback capture time (UTC, from the 14-digit
                         timestamp). Same meaning as the 138 older 'wayback'
                         rows, which hold the capture date at midnight.
  pages.fetch_method   = 'wayback:<14-digit capture timestamp>'. It starts with
                         'wayback', so rsdata.load_page_index keeps excluding it.
  The download time is not in ``pages``; it is downloaded_at_utc in
  downloads.jsonl, joined on page_id.

Politeness: one request at a time, at least 1.5 s between requests (measured
from the end of the previous one on both the wall and monotonic clocks), a
generic User-Agent, backoff on 429/5xx/connection errors (Retry-After honoured),
and a clean stop after several refusals in a row.

Nothing prints a URL. Sidecars hold sha1(url) plus the CDX rows (whose
'original' field is the archived URL of a design-day page). No Serper, no key.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import pickle
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_ROOT = Path(os.environ.get("RANKSIM_DATA_ROOT", "/workspace/ranksim-data"))
if str(DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_ROOT))

OUT_DIR = ROOT / "evals" / "claude" / "r2" / "wayback"
LOCK_PATH = Path("/tmp/r2_wayback.lock")
EXTRACT_CACHE = Path("/tmp/r2-wayback-extract-v1.pkl")

CDX_URL = "https://web.archive.org/cdx/search/cdx"
REPLAY_FMT = "https://web.archive.org/web/{ts}id_/{url}"
AVAILABLE_URL = "https://archive.org/wayback/available"
UA = "ranksim-research-bot/0.1"

WINDOW_FROM = "20261001"
WINDOW_TO = "20261117"
CUTOFF_TS = "20261117235959"
CDX_FIELDS = ("timestamp", "original", "statuscode", "mimetype", "digest", "length")
HTML_MIMES = ("text/html", "application/xhtml+xml")
MAX_PER_URL = 6
MIN_GAP = 1.5
BODY_CAP = 5 * 1024 * 1024
CDX_CAP = 20 * 1024 * 1024
METHOD_PREFIX = "wayback:"
GROUP_ORDER = ("o30", "a70")
PROBE_TS = ("20261117235959", "20261106000000", "20261001000000")
MIN_HTML_BYTES = 500  # same floor as t2_common


# ---------------------------------------------------------------- small pure helpers


def url_sha1(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def valid_ts(ts: object) -> bool:
    text = str(ts or "")
    if len(text) != 14 or not text.isdigit():
        return False
    try:
        datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return False
    return True


def ts_to_iso(ts: str) -> str:
    """'20261105123456' -> '2026-11-05T12:34:56.000Z' (Wayback timestamps are UTC)."""
    if not valid_ts(ts):
        raise ValueError(f"not a 14-digit Wayback timestamp: {ts!r}")
    return datetime.strptime(ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%S.000Z")


def method_for(ts: str) -> str:
    if not valid_ts(ts):
        raise ValueError(f"not a 14-digit Wayback timestamp: {ts!r}")
    return METHOD_PREFIX + ts


def ts_from_method(method: str | None) -> str | None:
    text = str(method or "")
    if not text.startswith(METHOD_PREFIX):
        return None
    ts = text[len(METHOD_PREFIX) : len(METHOD_PREFIX) + 14]
    return ts if valid_ts(ts) else None


def parse_cdx(payload: object, fields: tuple[str, ...] = CDX_FIELDS) -> list[dict]:
    """CDX output=json -> list of dicts. Accepts the response text or the parsed list.

    The JSON form is a list of lists whose first row is the field header. An
    empty body or '[]' means no captures. A row of the wrong length is dropped.
    """
    if payload is None:
        return []
    if isinstance(payload, (bytes, str)):
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        if not text.strip():
            return []
        payload = json.loads(text)
    if not isinstance(payload, list) or not payload:
        return []
    rows = payload
    header = [str(item) for item in rows[0]] if isinstance(rows[0], list) else None
    names = list(fields)
    if header and header[0] == "timestamp" and set(header) >= {"timestamp"}:
        names = header
        rows = rows[1:]
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            continue
        out.append({name: ("" if value is None else str(value)) for name, value in zip(names, row)})
    return out


def is_html_mime(mime: str | None) -> bool:
    text = (mime or "").lower()
    return any(text.startswith(kind) for kind in HTML_MIMES)


def keep_captures(rows: list[dict], cutoff: str = CUTOFF_TS) -> list[dict]:
    """Status-200 HTML captures with a valid timestamp <= cutoff, oldest first.

    Same timestamp twice keeps the first. Adjacent equal digests collapse to the
    first (the server's collapse=digest does this too; repeated here in case it
    was not applied). A later return to an earlier digest is kept: A, B, A is
    three versions.
    """
    found = [row for row in rows if valid_ts(row.get("timestamp")) and row["timestamp"] <= cutoff]
    found = [row for row in found if str(row.get("statuscode", "200")) == "200" and is_html_mime(row.get("mimetype"))]
    found.sort(key=lambda row: row["timestamp"])
    out: list[dict] = []
    seen_ts: set[str] = set()
    for row in found:
        if row["timestamp"] in seen_ts:
            continue
        if out and row.get("digest") and row.get("digest") == out[-1].get("digest"):
            continue
        seen_ts.add(row["timestamp"])
        out.append(row)
    return out


def select_captures(captures: list[dict], cap: int = MAX_PER_URL) -> list[dict]:
    """First, last, and evenly spaced between, at most ``cap`` (input must be oldest first)."""
    n = len(captures)
    if n <= cap:
        return list(captures)
    if cap <= 1:
        return [captures[0]]
    picks = sorted({int(round(i * (n - 1) / (cap - 1))) for i in range(cap)})
    return [captures[i] for i in picks]


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(str(value).strip()))
    except ValueError:
        return None


def load_jsonl(path: Path) -> list[dict]:
    """Records of a JSONL sidecar. A torn last line (crash, snapshot restore) is skipped."""
    if not path.is_file():
        return []
    out = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    return out


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------- resumability state

def cdx_is_final(record: dict) -> bool:
    outcome = str(record.get("outcome", ""))
    if outcome == "ok":
        return True
    if outcome.startswith("http_"):
        try:
            code = int(outcome.split("_", 1)[1])
        except ValueError:
            return False
        return 400 <= code < 500 and code != 429
    return False


def cdx_latest(records: Iterable[dict], window: tuple[str, str] = (WINDOW_FROM, WINDOW_TO)) -> dict[str, dict]:
    """Latest final CDX record per url_sha1 for this window (a later final record wins)."""
    out: dict[str, dict] = {}
    for record in records:
        if (record.get("from"), record.get("to")) != window or not cdx_is_final(record):
            continue
        out[str(record.get("url_sha1"))] = record
    return out


DOWNLOAD_FINAL = ("stored", "duplicate", "already_stored")


def download_is_final(record: dict) -> bool:
    """A capture never to be requested again. Transient failures are retried on the next run."""
    outcome = str(record.get("outcome", ""))
    if outcome in DOWNLOAD_FINAL:
        return True
    return outcome.startswith("failed:") and not outcome.startswith("failed:transient")


def downloads_done(records: Iterable[dict]) -> set[tuple[str, str]]:
    return {(str(r.get("url_sha1")), str(r.get("timestamp"))) for r in records if download_is_final(r)}


def probes_done(records: Iterable[dict]) -> set[tuple[str, str]]:
    return {(str(r.get("url_sha1")), str(r.get("probe_ts"))) for r in records if cdx_is_final(r)}


def stored_page_id(con: sqlite3.Connection, url: str, method: str) -> int | None:
    row = con.execute(
        "SELECT id FROM pages WHERE url = ? AND fetch_method = ? AND html IS NOT NULL ORDER BY id LIMIT 1",
        (url, method),
    ).fetchone()
    return int(row[0]) if row else None


# ---------------------------------------------------------------- polite HTTP


class ServiceRefused(RuntimeError):
    """The service kept refusing (429, 5xx or no connection). The run stops cleanly."""


@dataclass
class Fetched:
    status: int | None
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    reason: str = ""  # '' or 'body_cap' or 'error:<ExceptionName>'
    location: str = ""

    @property
    def refused(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


class Pacer:
    """At least ``gap`` seconds between the end of one request and the start of the next.

    Both the wall clock and the monotonic clock must show the gap. A wall clock
    that went backwards is ignored. Sleeps are short so a suspended box resumes
    by re-reading the clocks instead of trusting one long sleep.
    """

    def __init__(self, gap: float = MIN_GAP, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.gap = float(gap)
        self.clock, self.mono, self.sleep = clock, mono, sleep
        self.last_wall: float | None = None
        self.last_mono: float | None = None

    def remaining(self) -> float:
        if self.last_wall is None or self.last_mono is None:
            return 0.0
        wall = self.clock() - self.last_wall
        mono = self.mono() - self.last_mono
        need = self.gap - mono
        if wall >= 0:
            need = max(need, self.gap - wall)
        return need

    def wait(self) -> None:
        while (need := self.remaining()) > 0:
            self.sleep(min(need, 1.0))

    def mark(self) -> None:
        self.last_wall, self.last_mono = self.clock(), self.mono()


def nap(seconds: float, clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep) -> None:
    """Sleep ``seconds`` in short steps. Ends early if the wall clock shows the time passed (box suspend)."""
    start_wall, start_mono = clock(), mono()
    while True:
        wall = clock() - start_wall
        done = mono() - start_mono
        if done >= seconds or (wall >= seconds):
            return
        sleep(min(seconds - done, 5.0))


class Polite:
    """One request at a time with pacing, per-request retries and a global refusal stop."""

    def __init__(self, client: httpx.Client, *, gap: float = MIN_GAP, attempts: int = 3, max_refusals: int = 6,
                 base_backoff: float = 15.0, max_backoff: float = 600.0, clock=time.time, mono=time.monotonic,
                 sleep=time.sleep, out: Callable[[str], None] = print) -> None:
        self.client = client
        self.pacer = Pacer(gap, clock, mono, sleep)
        self.attempts = max(1, attempts)
        self.max_refusals = max(1, max_refusals)
        self.base_backoff, self.max_backoff = base_backoff, max_backoff
        self.clock, self.mono, self.sleep, self.out = clock, mono, sleep, out
        self.refusals = 0  # consecutive, across requests
        self.requests = 0
        self.refused_total = 0
        self.status_hist: Counter = Counter()

    def _once(self, url: str, params: dict | None, cap: int) -> Fetched:
        try:
            with self.client.stream("GET", url, params=params) as resp:
                chunks, size, reason = [], 0, ""
                for chunk in resp.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > cap:
                        reason = "body_cap"
                        break
                body = b"".join(chunks)[:cap] if reason else b"".join(chunks)
                return Fetched(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, body, reason,
                               resp.headers.get("location", ""))
        except httpx.HTTPError as exc:  # never log the message: it can carry the URL
            return Fetched(None, {}, b"", "error:" + type(exc).__name__)

    def get(self, url: str, params: dict | None = None, cap: int = BODY_CAP) -> Fetched:
        last = Fetched(None, reason="error:not_attempted")
        for attempt in range(1, self.attempts + 1):
            self.pacer.wait()
            res = self._once(url, params, cap)
            self.pacer.mark()
            self.requests += 1
            self.status_hist[str(res.status) if res.status is not None else res.reason] += 1
            if not res.refused:
                self.refusals = 0
                return res
            self.refusals += 1
            self.refused_total += 1
            last = res
            label = f"http_{res.status}" if res.status is not None else res.reason
            if self.refusals >= self.max_refusals:
                raise ServiceRefused(f"{self.refusals} refusals in a row (last {label})")
            if attempt < self.attempts:
                delay = retry_after_seconds(res.headers.get("retry-after"))
                if delay is None:
                    delay = self.base_backoff * (2 ** (self.refusals - 1))
                delay = min(self.max_backoff, delay)
                self.out(f"backoff {delay:.0f}s after {label} (refusals in a row: {self.refusals})")
                nap(delay, self.clock, self.mono, self.sleep)
        return last


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(20.0, read=timeout), follow_redirects=False,
                        headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})


# ---------------------------------------------------------------- URL list (design days only)


def build_url_list(max_date: str | None = None) -> list[tuple[str, str]]:
    """(url, group) for every distinct top-10 URL of o30 then a70, snapshot_date <= max_date.

    Order: group (o30 first), then the URL's first day in that group, then its best position, then the URL.
    A URL seen in both groups is listed once, as o30.
    """
    from scripts.claude import rsdata as rs

    max_date = max_date or rs.DESIGN_MAX_DATE
    if max_date > rs.DESIGN_MAX_DATE:
        raise ValueError("the URL list must stay on design days")
    rows = rs.load_rows(max_date)
    assert rows["day"].max() <= max_date
    groups = rs.query_groups(rows)
    top = rows[rows["kind"] == "top10"].copy()
    top["group"] = top["query"].map(groups)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in GROUP_ORDER:
        part = top[top["group"] == group]
        stats = part.groupby("url").agg(first=("day", "min"), best=("position", "min")).reset_index()
        stats = stats.sort_values(["first", "best", "url"])
        for url in stats["url"]:
            if url not in seen:
                seen.add(url)
                out.append((url, group))
    return out


# ---------------------------------------------------------------- CDX, download, probe


def cdx_record(polite: Polite, url: str, window: tuple[str, str] = (WINDOW_FROM, WINDOW_TO)) -> dict:
    params = {"url": url, "from": window[0], "to": window[1], "output": "json", "fl": ",".join(CDX_FIELDS),
              "filter": "statuscode:200", "collapse": "digest"}
    queried = now_utc_iso()
    res = polite.get(CDX_URL, params=params, cap=CDX_CAP)
    record = {"url_sha1": url_sha1(url), "from": window[0], "to": window[1], "queried_at_utc": queried,
              "http_status": res.status, "bytes": len(res.body)}
    if res.status == 200 and not res.reason:
        try:
            text = res.body.decode("utf-8", "replace")
            raw = json.loads(text) if text.strip() else []
        except json.JSONDecodeError:
            record["outcome"] = "transient:bad_json"
            return record
        record["outcome"] = "ok"
        record["raw"] = raw
        record["n_rows"] = len(parse_cdx(raw))
    elif res.status is None:
        record["outcome"] = "transient:" + res.reason
    elif res.refused or res.reason:
        record["outcome"] = f"transient:http_{res.status}" + (f":{res.reason}" if res.reason else "")
    else:
        record["outcome"] = f"http_{res.status}"
    return record


def _replay_target_ok(location: str, ts: str) -> bool:
    marker = f"/web/{ts}id_/"
    return location.startswith("https://web.archive.org" + marker) or location.startswith(marker)


def download_capture(polite: Polite, url: str, capture: dict) -> tuple[dict | None, dict]:
    """Fetch one capture in the id_ form. Returns (pages row or None, sidecar meta without page_id)."""
    ts = capture["timestamp"]
    source = capture.get("original") or url
    target = REPLAY_FMT.format(ts=ts, url=source)
    meta = {"url_sha1": url_sha1(url), "timestamp": ts, "digest": capture.get("digest"),
            "cdx_length": capture.get("length"), "attempted_at_utc": now_utc_iso()}
    res = polite.get(target, cap=BODY_CAP)
    if res.status is not None and 300 <= res.status < 400 and _replay_target_ok(res.location, ts):
        follow = res.location if res.location.startswith("http") else "https://web.archive.org" + res.location
        res = polite.get(follow, cap=BODY_CAP)
    meta.update(http_status=res.status, bytes=len(res.body), content_type=res.headers.get("content-type"))
    if res.status is None or res.refused:
        meta["outcome"] = "failed:transient:" + (res.reason if res.status is None else f"http_{res.status}")
        return None, meta
    if res.reason == "body_cap":
        meta["outcome"] = "failed:body_cap"
        return None, meta
    if 300 <= res.status < 400:
        meta["outcome"] = "failed:redirect"
        return None, meta
    if res.status != 200:
        meta["outcome"] = f"failed:http_{res.status}"
        return None, meta
    ctype = res.headers.get("content-type", "")
    if ctype and "html" not in ctype.lower():
        meta["outcome"] = "failed:not_html"
        return None, meta
    if not res.body:
        meta["outcome"] = "failed:empty"
        return None, meta
    meta["downloaded_at_utc"] = now_utc_iso()
    sha = hashlib.sha256(res.body).hexdigest()
    meta["sha256"] = sha
    row = {
        "url": url,
        "fetched_at_utc": ts_to_iso(ts),
        "fetch_method": method_for(ts),
        "http_status": res.status,
        "content_type": ctype or None,
        "html": res.body.decode("utf-8", errors="replace"),  # same decoding as refetch_html
        "sha256": sha,
    }
    return row, meta


def probe_record(polite: Polite, url: str, probe_ts: str, cutoff: str = CUTOFF_TS) -> dict:
    """One availability API call. A closest capture after the cutoff is flagged, its timestamp not kept."""
    queried = now_utc_iso()
    res = polite.get(AVAILABLE_URL, params={"url": url, "timestamp": probe_ts}, cap=1024 * 1024)
    record = {"url_sha1": url_sha1(url), "probe_ts": probe_ts, "queried_at_utc": queried, "http_status": res.status}
    if res.status != 200 or res.reason:
        record["outcome"] = ("transient:" + res.reason) if res.status is None else (
            f"transient:http_{res.status}" if res.refused else f"http_{res.status}")
        return record
    try:
        data = json.loads(res.body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        record["outcome"] = "transient:bad_json"
        return record
    record.update(parse_available(data, cutoff))
    record["outcome"] = "ok"
    return record


def parse_available(data: object, cutoff: str = CUTOFF_TS) -> dict:
    closest = {}
    if isinstance(data, dict):
        snaps = data.get("archived_snapshots") or {}
        if isinstance(snaps, dict) and isinstance(snaps.get("closest"), dict):
            closest = snaps["closest"]
    ts = str(closest.get("timestamp") or "")
    available = bool(closest.get("available")) and valid_ts(ts)
    after = available and ts > cutoff
    return {
        "available": available,
        "closest_ts": ts if available and not after else None,
        "closest_after_cutoff": bool(after),
        "closest_status": str(closest.get("status") or "") if available and not after else None,
    }


# ---------------------------------------------------------------- run loops


class RunLock:
    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("w")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise SystemExit("another r2_wayback run holds the lock; not starting a second one") from exc
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def cutoff_for(window: tuple[str, str]) -> str:
    """Last capture timestamp kept for a window: the end day at 23:59:59 UTC."""
    return window[1] + "235959"


def run_cdx(polite: Polite, urls: list[tuple[str, str]], out_dir: Path, *, window: tuple[str, str] = (WINDOW_FROM, WINDOW_TO),
            out=print, progress_every: int = 25) -> Counter:
    path = out_dir / "cdx.jsonl"
    done = set(cdx_latest(load_jsonl(path), window))
    stats: Counter = Counter()
    for index, (url, _group) in enumerate(urls, 1):
        if url_sha1(url) in done:  # checked again before every request
            stats["skipped_done"] += 1
            continue
        record = cdx_record(polite, url, window)
        append_jsonl(path, record)
        stats["queried"] += 1
        stats["outcome:" + record["outcome"].split(":")[0]] += 1
        if cdx_is_final(record):
            done.add(record["url_sha1"])
            if record.get("n_rows"):
                stats["urls_with_rows"] += 1
        if progress_every and index % progress_every == 0:
            out(f"cdx {index}/{len(urls)} " + json.dumps(dict(stats), sort_keys=True))
    return stats


def run_download(polite: Polite, con: sqlite3.Connection, urls: list[tuple[str, str]], out_dir: Path, *,
                 write_rows: Callable[[sqlite3.Connection, list[dict]], list[int | None]] | None = None,
                 window: tuple[str, str] = (WINDOW_FROM, WINDOW_TO), out=print, progress_every: int = 25) -> Counter:
    if write_rows is None:
        from scripts.claude.refetch_html import write_batch as write_rows  # IMMEDIATE txn + busy retry
    cdx = cdx_latest(load_jsonl(out_dir / "cdx.jsonl"), window)
    side = out_dir / "downloads.jsonl"
    logged = load_jsonl(side)
    done = downloads_done(logged)
    stats: Counter = Counter()
    for index, (url, _group) in enumerate(urls, 1):
        sha = url_sha1(url)
        record = cdx.get(sha)
        if record is None or record.get("outcome") != "ok":
            continue
        captures = select_captures(keep_captures(parse_cdx(record.get("raw")), cutoff_for(window)))
        for capture in captures:
            ts = capture["timestamp"]
            if (sha, ts) in done:
                stats["skipped_logged"] += 1
                continue
            method = method_for(ts)
            page_id = stored_page_id(con, url, method)  # what is stored now, before each request
            if page_id is not None:
                meta = {"url_sha1": sha, "timestamp": ts, "digest": capture.get("digest"), "page_id": page_id,
                        "outcome": "already_stored", "attempted_at_utc": None, "downloaded_at_utc": None}
                append_jsonl(side, meta)
                done.add((sha, ts))
                stats["already_stored"] += 1
                continue
            row, meta = download_capture(polite, url, capture)
            if row is not None:
                ids = write_rows(con, [row])
                meta["page_id"] = ids[0]
                meta["outcome"] = "stored" if ids[0] is not None else "duplicate"
            append_jsonl(side, meta)
            stats[meta["outcome"]] += 1
            if download_is_final(meta):
                done.add((sha, ts))
        if progress_every and index % progress_every == 0:
            out(f"download {index}/{len(urls)} " + json.dumps(dict(stats), sort_keys=True))
    return stats


def run_probe(polite: Polite, urls: list[tuple[str, str]], out_dir: Path, *, probe_ts: tuple[str, ...] = PROBE_TS,
              out=print, progress_every: int = 25) -> Counter:
    path = out_dir / "availability.jsonl"
    done = probes_done(load_jsonl(path))
    stats: Counter = Counter()
    for index, (url, _group) in enumerate(urls, 1):
        sha = url_sha1(url)
        for pts in probe_ts:
            if (sha, pts) in done:
                stats["skipped_done"] += 1
                continue
            record = probe_record(polite, url, pts)
            append_jsonl(path, record)
            stats["queried"] += 1
            stats["outcome:" + record["outcome"]] += 1
            if cdx_is_final(record):
                done.add((sha, pts))
        if progress_every and index % progress_every == 0:
            out(f"probe {index}/{len(urls)} " + json.dumps(dict(stats), sort_keys=True))
    return stats


# ---------------------------------------------------------------- measure (design days only)


def et_day(iso: str) -> str:
    import pandas as pd

    return pd.Timestamp(iso).tz_convert("America/New_York").strftime("%Y-%m-%d")


def build_pairs(w_caps: list[dict], h_fetches: list[dict]) -> list[dict]:
    """Pair specs per URL key (pure). Items: key, id, time (ISO UTC), day, source.

    ww     consecutive Wayback captures (capture time order)
    wh     latest Wayback capture on an ET day before the earliest own http fetch day -> that fetch
    noise  Wayback capture (not legacy) and own http fetch on the same ET day, closest in time
    """
    import pandas as pd

    by_key_w: dict[str, list[dict]] = defaultdict(list)
    for item in w_caps:
        by_key_w[item["key"]].append(item)
    by_key_h: dict[str, list[dict]] = defaultdict(list)
    for item in h_fetches:
        by_key_h[item["key"]].append(item)
    out = []
    for key, caps in sorted(by_key_w.items()):
        caps = sorted(caps, key=lambda c: (c["time"], c["id"]))
        for a, b in zip(caps, caps[1:]):
            out.append({"type": "ww", "key": key, "a": a, "b": b})
        hs = sorted(by_key_h.get(key, []), key=lambda h: (h["time"], h["id"]))
        if hs:
            # Earliest fetch day; on that day the round 1 one_per_day pick when marked, else the latest fetch.
            first_day = min(h["day"] for h in hs)
            day_items = [h for h in hs if h["day"] == first_day]
            first = next((h for h in day_items if h.get("pick")), day_items[-1])
            before = [c for c in caps if c["day"] < first_day]
            if before:
                out.append({"type": "wh", "key": key, "a": before[-1], "b": first})
        for cap in caps:
            if cap.get("source") == "wayback_legacy":
                continue
            same = [h for h in hs if h["day"] == cap["day"]]
            if not same:
                continue
            t0 = pd.Timestamp(cap["time"])
            near = min(same, key=lambda h: (abs((pd.Timestamp(h["time"]) - t0).total_seconds()), h["id"]))
            a, b = (cap, near) if cap["time"] <= near["time"] else (near, cap)
            hours = abs((pd.Timestamp(near["time"]) - t0).total_seconds()) / 3600.0
            out.append({"type": "noise", "key": key, "a": a, "b": b, "hours": round(hours, 2)})
    return out


def change_rows(changes: list[dict], cons_pos: dict, query_days: dict[str, list[str]], first_day: dict,
                queries_by_key: dict[str, set[str]], group_of: dict[str, str]) -> list[dict]:
    """(query, key) rows for material changes (pure). Positions are consensus (or single pull), absent = 11.

    strict: round 1 alignment (t2_dataset._serp_day): SERP day A in [day_a - 1, day_a], B in [day_b, day_b + 1].
    loose:  SERP day A = latest pulled day <= day_a, B = earliest pulled day >= day_b, any gap.
    Both need B > A and the key seen in the query's consensus table on some day <= A (the round 1 row
    universe). ``has_pos_a`` marks rows whose position on A is a real position (<= 10).
    """
    import pandas as pd

    def serp_day(days, day, side, strict):
        stamp = pd.Timestamp(day)
        if side == "a":
            lo = stamp - pd.Timedelta(days=1) if strict else pd.Timestamp.min
            window = [d for d in days if lo <= pd.Timestamp(d) <= stamp]
            return window[-1] if window else None
        hi = stamp + pd.Timedelta(days=1) if strict else pd.Timestamp.max
        window = [d for d in days if stamp <= pd.Timestamp(d) <= hi]
        return window[0] if window else None

    out = []
    for change in changes:
        key = change["key"]
        for query in sorted(queries_by_key.get(key, ())):
            days = query_days.get(query, [])
            for mode in ("strict", "loose"):
                serp_a = serp_day(days, change["day_a"], "a", mode == "strict")
                serp_b = serp_day(days, change["day_b"], "b", mode == "strict")
                if serp_a is None or serp_b is None or serp_b <= serp_a:
                    continue
                first = first_day.get((query, key))
                if first is None or first > serp_a:
                    continue
                pos_a = cons_pos.get((query, serp_a, key), 11)
                pos_b = cons_pos.get((query, serp_b, key), 11)
                out.append({"mode": mode, "query": query, "group": group_of.get(query, "other"), "key": key,
                            "site": change.get("site"), "pair_type": change["type"], "day_a": change["day_a"],
                            "day_b": change["day_b"], "serp_day_a": serp_a, "serp_day_b": serp_b,
                            "pos_a": int(pos_a), "pos_b": int(pos_b), "has_pos_a": int(pos_a) <= 10,
                            "moved": int(pos_a) != int(pos_b),
                            "overlaps_round1": bool(change.get("overlaps_round1_material", False))})
    return out


def _extract(ids_urls: list[tuple[int, str]]) -> dict[int, dict]:
    from scripts.claude import rsdata as rs
    from scripts.claude.t2_changes import extract_page

    cache: dict[int, dict] = {}
    if EXTRACT_CACHE.is_file():
        try:
            cache = pickle.loads(EXTRACT_CACHE.read_bytes())
        except Exception:  # a torn cache file is rebuilt
            cache = {}
    missing = [(pid, url) for pid, url in ids_urls if int(pid) not in cache]
    for pid, url in missing:
        cache[int(pid)] = extract_page(rs.load_page_html(int(pid)), url)
    if missing:
        EXTRACT_CACHE.write_bytes(pickle.dumps(cache, protocol=4))
    return {int(pid): cache[int(pid)] for pid, _url in ids_urls}


def _quant(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(math.floor(q * (len(ordered) - 1) + 0.5)))]  # noqa: E731
    return {"p50": pick(0.5), "p90": pick(0.9), "max": ordered[-1]}


def measure(out_dir: Path = OUT_DIR, db: Path | None = None) -> dict:
    import pandas as pd

    from scripts.claude import rsdata as rs
    from scripts.claude.t2_changes import MATERIAL_RULE, RULE_VERSION, diff_extracted
    from scripts.claude.t2_common import fetch_table, one_per_day

    max_date = rs.DESIGN_MAX_DATE
    urls = build_url_list(max_date)
    url_group = dict(urls)
    keys = {rs.url_key(url): url for url, _g in urls}
    key_group = {rs.url_key(url): g for url, g in urls}
    result: dict = {"design_max_date": max_date, "cutoff_ts": CUTOFF_TS, "window": [WINDOW_FROM, WINDOW_TO],
                    "rule_version": RULE_VERSION, "material_rule": MATERIAL_RULE}
    result["url_list"] = {"urls": len(urls), "by_group": dict(Counter(g for _u, g in urls)),
                          "sites": len({rs.site_key(u) for u, _g in urls})}

    # CDX coverage
    sha_group = {url_sha1(u): g for u, g in urls}
    cdx = {s: r for s, r in cdx_latest(load_jsonl(out_dir / "cdx.jsonl")).items() if s in sha_group}
    cov = Counter()
    for sha, record in cdx.items():
        caps = keep_captures(parse_cdx(record.get("raw"))) if record.get("outcome") == "ok" else []
        grp = sha_group[sha]
        cov[f"queried_{grp}"] += 1
        if caps:
            cov[f"with_capture_{grp}"] += 1
            cov["captures_kept"] += len(caps)
            cov["captures_selected"] += len(select_captures(caps))
        if len({c.get("digest") for c in caps}) >= 2:
            cov[f"two_plus_digests_{grp}"] += 1
    result["cdx"] = {"urls_queried": len(cdx), **dict(cov)}
    dl = load_jsonl(out_dir / "downloads.jsonl")
    result["downloads"] = dict(Counter(str(r.get("outcome")) for r in dl))

    # availability probe (fallback) coverage
    probes = [r for r in load_jsonl(out_dir / "availability.jsonl") if r.get("outcome") == "ok" and r.get("url_sha1") in sha_group]
    sha_key = {url_sha1(url): rs.url_key(url) for url, _g in urls}
    probe_by_key: dict[str, set[str]] = {}
    if probes:
        ts_by = defaultdict(set)
        probed = defaultdict(set)
        for r in probes:
            probed[r["url_sha1"]].add(r["probe_ts"])
            ts = r.get("closest_ts")
            if ts and WINDOW_FROM <= ts[:8] and ts <= CUTOFF_TS and r.get("closest_status") == "200":
                ts_by[r["url_sha1"]].add(ts)
        full = [s for s, p in probed.items() if set(PROBE_TS) <= p]
        after_cut = {r["url_sha1"] for r in probes if r.get("closest_after_cutoff")}
        pc = Counter()
        for s in full:
            g = sha_group[s]
            found = ts_by.get(s, set())
            pc[f"probed_{g}"] += 1
            if found:
                pc[f"with_capture_{g}"] += 1
            if len(found) >= 2:
                pc[f"two_plus_captures_{g}"] += 1
            if any(t >= "20261106" for t in found):
                pc[f"capture_nov6_to_nov17_{g}"] += 1
            if sum(1 for t in found if t >= "20261106") >= 2:
                pc[f"two_plus_nov6_to_nov17_{g}"] += 1
            pc["distinct_in_window_ts"] += len(found)
            if s in after_cut:
                pc["closest_after_cutoff_on_some_probe"] += 1
        result["availability_probe"] = {"urls_fully_probed": len(full), "probe_ts": list(PROBE_TS), **dict(pc)}
        probe_by_key = {sha_key[s]: ts_by.get(s, set()) for s in full}

    # Wayback rows in pages (this run's 'wayback:<ts>' rows plus the older 'wayback' rows), read only
    con = rs.connect(db or rs.DB)
    try:
        wrows = con.execute(
            "SELECT id, url, fetched_at_utc, fetch_method, sha256, length(html) FROM pages "
            "WHERE fetch_method LIKE 'wayback%' AND html IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    new_caps, legacy = [], []
    for pid, url, fetched, method, sha, hlen in wrows:
        key = rs.url_key(url)
        if key not in keys or (hlen or 0) < MIN_HTML_BYTES:
            continue
        ts = ts_from_method(method)
        if ts is not None:
            if ts > CUTOFF_TS or ts[:8] < WINDOW_FROM:
                continue
            iso = ts_to_iso(ts)
            new_caps.append({"key": key, "id": int(pid), "url": url, "time": iso, "day": et_day(iso), "ts": ts,
                             "source": "wayback_r2", "sha256": sha})
        elif method == "wayback":
            day = str(fetched)[:10]
            if not ("2026-10-01" <= day <= max_date):
                continue
            legacy.append({"key": key, "id": int(pid), "url": url, "time": str(fetched), "day": day, "ts": None,
                           "source": "wayback_legacy", "sha256": sha})
    r2_days = {(c["key"], c["time"][:10]) for c in new_caps}
    legacy = [c for c in legacy if (c["key"], c["day"]) not in r2_days]
    w_caps = new_caps + legacy
    result["wayback_rows"] = {"r2_rows_in_window": len(new_caps), "r2_urls": len({c["key"] for c in new_caps}),
                              "legacy_rows_in_window": len(legacy), "legacy_urls": len({c["key"] for c in legacy})}

    # own http fetches (sealed loader), only for keys with a Wayback capture
    w_keys = {c["key"] for c in w_caps}
    fetches = fetch_table(max_date)

    # Where Wayback can add brackets: own usable timed http fetch days per URL (round 1 pairs need two).
    own = fetches[(fetches["fetch_method"] == "http") & ~fetches["date_only"] & fetches["key"].isin(set(keys))]
    own_days = own.groupby("key")["day"].nunique().to_dict()
    oc: Counter = Counter()
    for key, url in keys.items():
        grp = key_group[key]
        n = int(own_days.get(key, 0))
        band = "0" if n == 0 else ("1" if n == 1 else "2plus")
        oc[f"{grp}_own_http_days_{band}"] += 1
        found = probe_by_key.get(key, set())
        late = sum(1 for t in found if t >= "20261106")
        if n <= 1 and late >= 1:
            oc[f"{grp}_own_days_le1_and_probe_capture_nov6_17"] += 1
        if n <= 1 and late >= 2:
            oc[f"{grp}_own_days_le1_and_2plus_probe_captures_nov6_17"] += 1
    result["own_fetch_coverage"] = dict(oc)

    http = fetches[(fetches["fetch_method"] == "http") & ~fetches["date_only"] & fetches["key"].isin(w_keys)]
    ids_urls = [(int(i), u) for i, u in zip(http["id"], http["url"])] + [(c["id"], c["url"]) for c in w_caps]
    pages = _extract(ids_urls)
    words = {pid: p["word_count"] for pid, p in pages.items()}
    picked = one_per_day(http, words) if len(http) else http
    picked_ids = set(int(i) for i in picked["id"]) if len(http) else set()
    h_items = [{"key": k, "id": int(i), "url": u, "time": t, "day": d, "source": "http", "pick": int(i) in picked_ids}
               for k, i, u, t, d in zip(http["key"], http["id"], http["url"], http["fetched_at"], http["day"])]

    specs = build_pairs(w_caps, h_items)
    records = []
    for spec in specs:
        a, b = spec["a"], spec["b"]
        diff = diff_extracted(pages[a["id"]], pages[b["id"]])
        records.append({
            "type": spec["type"], "key": spec["key"], "site": rs.site_key(keys[spec["key"]]),
            "group": key_group[spec["key"]], "source_a": a["source"], "source_b": b["source"],
            "time_a": a["time"], "time_b": b["time"], "day_a": a["day"], "day_b": b["day"],
            "id_a": a["id"], "id_b": b["id"], "hours": spec.get("hours"),
            "same_sha256": a.get("sha256") is not None and a.get("sha256") == b.get("sha256"),
            **{k: diff[k] for k in ("usable", "material", "material_reasons", "wc_a", "wc_b", "edit_tokens",
                                    "edit_share", "title_changed", "h1_changed", "headings_added", "headings_removed")},
        })
    frame = pd.DataFrame.from_records(records)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_dir / "pairs.csv", index=False)

    def block(kind: str) -> dict:
        if frame.empty:
            return {"pairs": 0}
        part = frame[frame["type"] == kind]
        use = part[part["usable"]]
        mat = use[use["material"]]
        info = {"pairs": int(len(part)), "usable": int(len(use)), "material": int(len(mat)),
                "material_urls": int(mat["key"].nunique()), "material_sites": int(mat["site"].nunique()),
                "material_reasons": dict(Counter(mat["material_reasons"])),
                "by_source": dict(Counter(part["source_a"] + ">" + part["source_b"]))}
        if kind == "noise":
            info["same_sha256"] = int(part["same_sha256"].sum())
            info["edit_tokens"] = _quant([float(x) for x in use["edit_tokens"]])
            info["title_changed"] = int(use["title_changed"].sum())
            info["h1_changed"] = int(use["h1_changed"].sum())
            info["hours_apart"] = _quant([float(x) for x in part["hours"]])
        return info

    result["pairs"] = {kind: block(kind) for kind in ("ww", "wh", "noise")}

    # material changes and their brackets
    changes = []
    if not frame.empty:
        mat = frame[(frame["type"] != "noise") & frame["usable"] & frame["material"]]
        changes = mat.to_dict("records")
    round1 = ROOT / "evals/claude/t2-edits/fetch_pairs.csv"
    r1 = pd.read_csv(round1) if round1.is_file() else pd.DataFrame(columns=["key", "day_a", "day_b", "material", "usable"])
    r1m = r1[r1["usable"].astype(bool) & r1["material"].astype(bool)] if len(r1) else r1
    for change in changes:
        same = r1m[(r1m["key"] == change["key"]) & (r1m["day_a"] < change["day_b"]) & (r1m["day_b"] > change["day_a"])]
        change["overlaps_round1_material"] = bool(len(same))
    pd.DataFrame.from_records(changes).to_csv(out_dir / "changes.csv", index=False)
    result["material_changes"] = {
        "total": len(changes), "urls": len({c["key"] for c in changes}), "sites": len({c["site"] for c in changes}),
        "overlapping_round1": sum(1 for c in changes if c["overlaps_round1_material"]),
        "brackets": [{"site": c["site"], "type": c["type"], "day_a": c["day_a"], "day_b": c["day_b"],
                      "reasons": c["material_reasons"]} for c in changes],
    }

    # usable rows for the edit-direction dataset
    rows = rs.load_rows(max_date)
    assert rows["day"].max() <= max_date
    groups = rs.query_groups(rows)
    rows = rows[rows["query"].map(groups).isin(GROUP_ORDER)]
    cons = rs.consensus_table(rows)
    qdays = rs.query_days(cons)
    cons_pos = {(r.query, r.day, r.key): int(r.cons) for r in cons.itertuples(index=False)}
    first_day: dict = {}
    qbk: dict[str, set[str]] = defaultdict(set)
    for r in cons.itertuples(index=False):
        pair = (r.query, r.key)
        if pair not in first_day or r.day < first_day[pair]:
            first_day[pair] = r.day
        qbk[r.key].add(r.query)
    urows = change_rows(changes, cons_pos, qdays, first_day, qbk, groups)
    uframe = pd.DataFrame.from_records(urows)
    # Counts only: the position values (and so the direction of a move) are not written out.
    uframe.drop(columns=[c for c in ("pos_a", "pos_b") if c in uframe.columns]).to_csv(out_dir / "usable_rows.csv", index=False)

    def ublock(mode: str) -> dict:
        if uframe.empty:
            return {"rows": 0}
        part = uframe[uframe["mode"] == mode]
        fresh = part[part["has_pos_a"] & ~part["overlaps_round1"]]
        return {"rows": int(len(part)), "rows_with_position_before": int(part["has_pos_a"].sum()),
                "rows_with_position_before_and_moved": int((part["has_pos_a"] & part["moved"]).sum()),
                "rows_with_position_before_not_in_round1": int(len(fresh)), "queries": int(part["query"].nunique()),
                "urls": int(part["key"].nunique()), "by_group": dict(Counter(part["group"]))}

    result["usable_rows"] = {"strict": ublock("strict"), "loose": ublock("loose"),
                             "pulled_days": {g: sorted({d for q, ds in qdays.items() if groups.get(q) == g for d in ds})
                                             for g in GROUP_ORDER}}
    (out_dir / "measure.json").write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return result


# ---------------------------------------------------------------- CLI


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free Wayback backfill of tracked pages (counts only, no URLs printed).")
    parser.add_argument("command", choices=["urls", "cdx", "download", "run", "probe", "measure"])
    parser.add_argument("--db", default=None, help="raw store (default /workspace/ranksim-data/ranksim_raw.sqlite)")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--limit", type=int, default=None, help="first N URLs of the ordered list (smoke runs)")
    parser.add_argument("--group", choices=list(GROUP_ORDER), default=None)
    parser.add_argument("--gap", type=float, default=MIN_GAP, help="seconds between requests (>= 1.5)")
    parser.add_argument("--max-refusals", type=int, default=6)
    parser.add_argument("--from", dest="window_from", default=WINDOW_FROM, help="CDX window start YYYYMMDD")
    parser.add_argument("--to", dest="window_to", default=WINDOW_TO,
                        help="CDX window end YYYYMMDD (captures up to 23:59:59 UTC that day). Later than "
                             f"{WINDOW_TO} only once those days are unsealed.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out_dir = Path(args.out_dir)
    if args.gap < MIN_GAP:
        print(f"--gap below {MIN_GAP} s is not allowed", file=sys.stderr)
        return 2

    if args.command == "measure":
        print(json.dumps(measure(out_dir, Path(args.db) if args.db else None), indent=2, sort_keys=True, default=str))
        return 0

    urls = build_url_list()
    if args.group:
        urls = [item for item in urls if item[1] == args.group]
    if args.limit is not None:
        urls = urls[: args.limit]
    if args.command == "urls":
        listing = [{"order": i, "url_sha1": url_sha1(u), "group": g} for i, (u, g) in enumerate(urls)]
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "urls.json").write_text(json.dumps(listing, indent=1) + "\n")
        print(json.dumps({"urls": len(urls), "by_group": dict(Counter(g for _u, g in urls))}))
        return 0

    window = (args.window_from, args.window_to)
    if not all(len(w) == 8 and w.isdigit() for w in window) or window[0] > window[1]:
        print("--from/--to must be YYYYMMDD with from <= to", file=sys.stderr)
        return 2
    started = now_utc_iso()
    summary: dict = {"command": args.command, "started_utc": started, "urls": len(urls), "gap": args.gap,
                     "window": list(window)}
    code = 0
    with RunLock():
        client = make_client()
        polite = Polite(client, gap=args.gap, max_refusals=args.max_refusals)
        con = None
        try:
            if args.command in ("cdx", "run"):
                summary["cdx"] = dict(run_cdx(polite, urls, out_dir, window=window))
            if args.command in ("download", "run"):
                from lib.db import DEFAULT_DB, connect

                db = Path(args.db) if args.db else DEFAULT_DB
                if not db.exists():
                    print("database not found; refusing to create a new one", file=sys.stderr)
                    return 2
                con = connect(db)  # 60 s busy timeout
                summary["download"] = dict(run_download(polite, con, urls, out_dir, window=window))
            if args.command == "probe":
                summary["probe"] = dict(run_probe(polite, urls, out_dir))
            summary["stopped"] = "complete"
        except ServiceRefused as exc:
            summary["stopped"] = f"service_refused: {exc}"
            code = 3
        except KeyboardInterrupt:
            summary["stopped"] = "interrupted"
            code = 130
        finally:
            summary.update(finished_utc=now_utc_iso(), requests=polite.requests, refused_attempts=polite.refused_total,
                           status_hist=dict(polite.status_hist))
            append_jsonl(out_dir / "runs.jsonl", summary)
            client.close()
            if con is not None:
                con.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
