"""Free dated re-fetch of page HTML for every URL in recent top-10 SERPs.

Each pass does one plain-HTTP fetch per URL with the existing collector logic
(/workspace/ranksim-data/lib/collect_snapshot.py: robots.txt for the bot UA,
public-host check by hostname, per-host delay, 5 MB body cap) and appends one
row per URL to ``pages`` in the raw store. Earlier fetches of the same URL are
never skipped: the point is a dated series so content changes can be found.

Sealed data: the URL list includes SERP rows dated on held-out days. Nothing
here prints, logs, or writes a URL. Output is aggregate counts plus a sidecar
JSONL keyed by sha1(url).

No Serper, no paid service, no API key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

DATA_ROOT = Path(os.environ.get("RANKSIM_DATA_ROOT", "/workspace/ranksim-data"))
if str(DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_ROOT))

from lib import collect_snapshot as cs  # noqa: E402
from lib.db import DEFAULT_DB, connect, insert_page, now_utc_iso, to_utc_iso  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SIDECAR_DIR = REPO / "evals" / "claude" / "pipeline"
NY = ZoneInfo("America/New_York")

# Same exclusions as scripts/claude/rsdata.py.
SKIP_SOURCES = ("smartcrew-index-deep:2026-11-16",)
SKIP_PREFIX = "keyword-sample:"

SELECT_SQL = """
SELECT DISTINCT url FROM serp_results
WHERE snapshot_date >= ?
  AND position IS NOT NULL AND position <= 10
  AND url IS NOT NULL AND url != ''
  AND instr(ifnull(source, ''), ?) != 1
  AND ifnull(source, '') NOT IN ({skip})
ORDER BY url
"""

FetchFn = Callable[[str], dict]


def cutoff_date(since_days: int, today: date | None = None) -> str:
    """today (America/New_York) minus since_days, as YYYY-MM-DD."""
    if today is None:
        today = datetime.now(NY).date()
    return (today - timedelta(days=since_days)).isoformat()


def select_urls(con: sqlite3.Connection, since: str, limit: int | None = None) -> list[str]:
    sql = SELECT_SQL.format(skip=",".join("?" for _ in SKIP_SOURCES))
    urls = [row[0] for row in con.execute(sql, (since, SKIP_PREFIX, *SKIP_SOURCES))]
    if limit is not None:
        urls = urls[:limit]
    return urls


def url_sha1(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def previous_http_sha(con: sqlite3.Connection, urls: list[str]) -> dict[str, str]:
    """Latest earlier successful plain-HTTP body sha256 per URL (for unchanged/changed counts)."""
    out: dict[str, str] = {}
    for start in range(0, len(urls), 500):
        chunk = urls[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        rows = con.execute(
            f"SELECT url, sha256 FROM pages WHERE url IN ({marks}) AND fetch_method = 'http' "
            "AND html IS NOT NULL ORDER BY id",
            chunk,
        )
        for url, sha in rows:
            out[url] = sha
    return out


def page_row(rec: dict) -> dict:
    """Map an http_fetch_one record to a pages row, exactly as collect_snapshot.fetch_pages does."""
    body = rec.get("body")
    ok = bool(body) and not rec.get("reason")
    return {
        "url": rec["url"],
        "fetched_at_utc": to_utc_iso(rec.get("captured_at")) or now_utc_iso(),
        "fetch_method": "http" if ok else f"http:{rec.get('reason') or 'failed'}",
        "http_status": rec.get("status"),
        "content_type": rec.get("mime"),
        "html": body.decode("utf-8", errors="replace") if ok else None,
        "sha256": rec.get("sha256"),
    }


def write_batch(con: sqlite3.Connection, rows: list[dict], *, retries: int = 12, sleep=time.sleep) -> list[int | None]:
    """Insert rows in one IMMEDIATE transaction. Retry the whole batch on a locked or busy DB."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            con.execute("BEGIN IMMEDIATE")
            ids = [insert_page(con, row) for row in rows]
            con.commit()
            return ids
        except sqlite3.OperationalError as exc:
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last_exc = exc
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            sleep(min(60.0, 2.0**attempt))
    raise RuntimeError(f"database stayed locked after {retries} attempts") from last_exc


def make_http_fetch() -> FetchFn:
    """The collector's http_fetch_one with shared robots cache, per-host locks and delays.

    robots.txt is fetched with the same function and UA, but under a per-origin
    lock so one slow robots.txt does not stall every other host. The fetch then
    finds it in the cache.
    """
    robots_cache: dict = {}
    locks: dict = {}
    last: dict = {}
    guard = threading.Lock()
    origin_locks: dict = {}

    def fetch(url: str) -> dict:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and cs._public_host(parts.hostname):
            origin = f"{parts.scheme}://{parts.netloc}"
            with guard:
                lk = origin_locks.setdefault(origin, threading.Lock())
            cs._robots_ok(url, cs.UA, robots_cache, lk)
        return cs.http_fetch_one(url, robots_cache=robots_cache, locks=locks, last=last, guard=guard)

    return fetch


def _safe_fetch(fetch_fn: FetchFn, url: str) -> dict:
    try:
        rec = fetch_fn(url)
    except Exception as exc:  # never let one URL kill the pass; never log the URL
        rec = {"url": url, "status": None, "mime": None, "bytes": 0, "sha256": None,
               "captured_at": None, "reason": "error:" + type(exc).__name__, "body": None}
    rec["url"] = url
    return rec


def run_pass(
    con: sqlite3.Connection,
    urls: list[str],
    fetch_fn: FetchFn,
    sidecar_path: Path,
    *,
    workers: int = 10,
    batch_size: int = 20,
    progress_every: int = 100,
    out=print,
) -> dict:
    """Fetch every URL once and append one pages row each. Returns aggregate counts only."""
    prev = previous_http_sha(con, urls)
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        by_host[(urlsplit(url).hostname or "").lower()].append(url)
    # Longest host queue first so the per-host delay does not dominate the tail.
    host_queues = sorted(by_host.values(), key=len, reverse=True)

    results: queue.Queue = queue.Queue(maxsize=64)
    stop = threading.Event()

    def host_worker(host_urls: list[str]) -> None:
        for url in host_urls:
            if stop.is_set():
                return
            rec = _safe_fetch(fetch_fn, url)
            while not stop.is_set():
                try:
                    results.put(rec, timeout=1)
                    break
                except queue.Full:
                    continue

    stats = Counter()
    reasons = Counter()
    status_hist = Counter()
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[dict, dict]] = []

    def flush(handle) -> None:
        nonlocal pending
        if not pending:
            return
        ids = write_batch(con, [row for row, _ in pending])
        for (row, meta), page_id in zip(pending, ids):
            stats["rows_inserted" if page_id is not None else "rows_skipped_duplicate"] += 1
            meta["page_id"] = page_id
            handle.write(json.dumps(meta, sort_keys=True) + "\n")
        handle.flush()
        pending = []

    def take(rec: dict, handle) -> None:
        row = page_row(rec)
        ok = row["fetch_method"] == "http"
        stats["fetched_ok" if ok else "failed"] += 1
        if not ok:
            reasons[row["fetch_method"].split(":", 1)[1]] += 1
        status_hist[str(row["http_status"])] += 1
        same = None
        if ok:
            stats["body_bytes_ok"] += int(rec.get("bytes") or 0)
            stats["html_chars_stored"] += len(row["html"] or "")
            before = prev.get(rec["url"])
            if before is None:
                stats["ok_no_prior_http_fetch"] += 1
            else:
                same = before == row["sha256"]
                stats["ok_bytes_unchanged_vs_prior" if same else "ok_bytes_changed_vs_prior"] += 1
        meta = {
            "url_sha1": url_sha1(rec["url"]),
            "fetched_at_utc": row["fetched_at_utc"],
            "sha256": row["sha256"],
            "http_status": row["http_status"],
            "fetch_method": row["fetch_method"],
            "bytes": int(rec.get("bytes") or 0),
            "same_sha256_as_prior_http": same,
        }
        pending.append((row, meta))
        if len(pending) >= batch_size:
            flush(handle)

    received = 0
    with sidecar_path.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max(1, workers)) as pool:
        futures = [pool.submit(host_worker, q) for q in host_queues]
        try:
            while received < len(urls):
                try:
                    rec = results.get(timeout=5)
                except queue.Empty:
                    dead = [f for f in futures if f.done() and f.exception() is not None]
                    if dead:
                        raise RuntimeError("a host worker crashed") from dead[0].exception()
                    continue
                received += 1
                take(rec, handle)
                if progress_every and received % progress_every == 0:
                    out(f"progress {received}/{len(urls)} ok={stats['fetched_ok']} failed={stats['failed']}")
            flush(handle)
        finally:
            # Unblocks and stops the workers if the writer failed; a no-op after a full pass.
            stop.set()
        for f in futures:
            f.result()

    return {
        "urls_selected": len(urls),
        "hosts": len(host_queues),
        "fetched_ok": stats["fetched_ok"],
        "failed": stats["failed"],
        "failure_reasons": dict(reasons.most_common()),
        "http_status_hist": dict(status_hist.most_common()),
        "rows_inserted": stats["rows_inserted"],
        "rows_skipped_duplicate": stats["rows_skipped_duplicate"],
        "ok_bytes_unchanged_vs_prior": stats["ok_bytes_unchanged_vs_prior"],
        "ok_bytes_changed_vs_prior": stats["ok_bytes_changed_vs_prior"],
        "ok_no_prior_http_fetch": stats["ok_no_prior_http_fetch"],
        "body_bytes_ok": stats["body_bytes_ok"],
        "html_chars_stored": stats["html_chars_stored"],
    }


def _db_bytes(db: Path) -> dict:
    sizes = {}
    for suffix in ("", "-wal"):
        path = Path(str(db) + suffix)
        sizes["db" if not suffix else "wal"] = path.stat().st_size if path.exists() else 0
    sizes["total"] = sizes["db"] + sizes["wal"]
    return sizes


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Free dated HTML re-fetch of recent top-10 URLs. Prints aggregates only.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--since-days", type=int, default=5)
    parser.add_argument("--today", default=None, help="Override today (America/New_York), YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Counts only, no network, no writes")
    parser.add_argument("--label", default="")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--sidecar-dir", default=str(SIDECAR_DIR))
    args = parser.parse_args(list(argv) if argv is not None else None)

    db = Path(args.db)
    if not db.exists():
        print("database not found; refusing to create a new one", file=sys.stderr)
        return 2
    today = date.fromisoformat(args.today) if args.today else None
    since = cutoff_date(args.since_days, today)
    con = connect(db)
    try:
        urls = select_urls(con, since, args.limit)
        hosts = len({(urlsplit(u).hostname or "").lower() for u in urls})
        if args.dry_run:
            print(json.dumps({"dry_run": True, "since": since, "urls_selected": len(urls), "hosts": hosts, "label": args.label}))
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sidecar = Path(args.sidecar_dir) / f"refetch-{stamp}.jsonl"
        size_before = _db_bytes(db)
        start_utc = now_utc_iso()
        t0 = time.monotonic()
        summary = run_pass(con, urls, make_http_fetch(), sidecar, workers=args.workers, batch_size=args.batch_size)
        wall = time.monotonic() - t0
        summary.update(
            label=args.label,
            since=since,
            started_utc=start_utc,
            finished_utc=now_utc_iso(),
            wall_seconds=round(wall, 1),
            db_bytes_before=size_before,
            db_bytes_after=_db_bytes(db),
            sidecar=str(sidecar.relative_to(REPO)) if sidecar.is_relative_to(REPO) else sidecar.name,
        )
        sidecar.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # no traceback: a third-party message could carry a URL
        detail = f": {exc}" if isinstance(exc, (sqlite3.Error, RuntimeError)) else ""
        print(f"refetch failed: {type(exc).__name__}{detail}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
