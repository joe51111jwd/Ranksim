"""One top-10 Serper pull of a query list, appended to the raw SQLite store and saved to git in chunks.

Usage:
  python scripts/claude/pull_serps.py --label pull-a --queries /abs/path/queries.txt [--date YYYY-MM-DD]

Why chunks and git: this machine is snapshotted and restored from time to time,
and a restore rolls the local disk back while spent credits stay spent. So every
chunk of raw responses is written to evals/claude/serp/<date>/<label>/chunk-NN.json.gz,
committed, and pushed before the next chunk starts. A rerun first imports every
chunk that is already in git and only pulls what is missing. A restore can lose
one chunk of credits at most.

The key is read from SERPER_API_KEY or /workspace/ranksim-secrets/serper.env and is
never printed, logged, or written. The run refuses to start a chunk when the balance
after it would fall under the reserve (default 260, the brief says keep 250).
SERP rows go to ranksim_raw.sqlite through the existing append-only helpers in
/workspace/ranksim-data/lib. No HTML is fetched here (scripts/claude/refetch_html.py does that).
Output on screen is counts only, so a held-out day can be pulled without being read.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = Path("/workspace/ranksim-data")
SECRETS = Path("/workspace/ranksim-secrets/serper.env")
SERP_DIR = REPO / "evals" / "claude" / "serp"
ENDPOINT = "https://google.serper.dev/search"
PARAMS = {"gl": "us", "hl": "en", "num": 10}
if str(DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_ROOT))


def load_key() -> str:
    key = (os.environ.get("SERPER_API_KEY") or "").strip()
    if key:
        return key
    if not SECRETS.exists():
        raise SystemExit("SERPER_API_KEY is not set and the secrets file is missing.")
    for line in SECRETS.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("SERPER_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\r\n ")
    raise SystemExit("SERPER_API_KEY not found in the secrets file.")


def today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def sync_branch() -> None:
    """Bring the local branch up to the pushed branch (a restore can leave it behind)."""
    _git("fetch", "-q", "origin", check=False)
    counts = _git("rev-list", "--left-right", "--count", "HEAD...@{upstream}", check=False).stdout.split()
    if len(counts) != 2:
        return
    ahead, behind = int(counts[0]), int(counts[1])
    if behind and not ahead:
        _git("merge", "-q", "--ff-only", "@{upstream}")
    elif behind and ahead:
        _git("pull", "-q", "--rebase", "--autostash")


def persist(path: Path, message: str) -> None:
    """Commit one file and push it. Retries through a rejected push."""
    relative = str(path.relative_to(REPO))
    _git("add", "--", relative)
    _git("commit", "-q", "-m", message, "--", relative)
    for attempt in range(1, 6):
        if _git("push", "-q", check=False).returncode == 0:
            return
        time.sleep(3 * attempt)
        sync_branch()
    raise SystemExit(f"Could not push {relative}. The chunk is committed locally.")


def import_chunk(con, payload: dict) -> int:
    """Insert a chunk's responses into the raw store. Safe to repeat: the helpers skip rows already there."""
    from lib.db import insert_serp_raw, insert_serp_result, to_utc_iso

    added = 0
    for item in payload["items"]:
        body = json.loads(item["raw"])
        fetched = to_utc_iso(item["start"])
        raw_id = insert_serp_raw(
            con,
            {
                "query": item["query"],
                "fetched_at_utc": fetched,
                "gl": PARAMS["gl"],
                "hl": PARAMS["hl"],
                "num": PARAMS["num"],
                "endpoint": ENDPOINT,
                "provider": "serper",
                "request_params": json.dumps(PARAMS, ensure_ascii=False),
                "raw_json": item["raw"],
                "http_status": item["status"],
                "credits": body.get("credits"),
                "source": payload["source"],
                "snapshot_date": payload["date"],
            },
        )
        for organic in body.get("organic") or []:
            insert_serp_result(
                con,
                {
                    "query": item["query"],
                    "snapshot_date": payload["date"],
                    "fetched_at_utc": fetched,
                    "gl": PARAMS["gl"],
                    "hl": PARAMS["hl"],
                    "num": PARAMS["num"],
                    "position": organic.get("position"),
                    "url": organic.get("link"),
                    "title": organic.get("title"),
                    "snippet": organic.get("snippet"),
                    "raw_json": json.dumps(organic, ensure_ascii=False),
                    "source": payload["source"],
                },
            )
        added += 1 if raw_id is not None else 0
    con.commit()
    return added


def pull_chunk(client, key: str, queries: list[str]) -> list[dict]:
    items = []
    for query in queries:
        last_error = "no attempt"
        for attempt in range(1, 5):
            start = datetime.now().astimezone().isoformat()
            try:
                response = client.post(ENDPOINT, headers={"X-API-KEY": key, "Content-Type": "application/json"}, json={"q": query, **PARAMS})
            except Exception as exc:  # network error: wait and try again
                last_error = type(exc).__name__
                time.sleep(10 * attempt)
                continue
            end = datetime.now().astimezone().isoformat()
            text = response.text
            if key in text:
                raise SystemExit("Refusing to store a response that contains the API key.")
            try:
                organic = json.loads(text).get("organic")
            except ValueError:
                organic = None
            if response.status_code == 200 and isinstance(organic, list):
                items.append({"query": query, "start": start, "end": end, "status": 200, "raw": text})
                break
            last_error = f"http {response.status_code}"
            time.sleep(10 * attempt)
        else:
            raise SystemExit(f"A query failed four times ({last_error}). Stopping. Rerun to resume.")
        time.sleep(1.0)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="Pull label, for example pull-a")
    parser.add_argument("--queries", required=True, action="append", help="Absolute path to a query file. Repeatable.")
    parser.add_argument("--date", default=None, help="Snapshot date (America/New_York). Default: today.")
    parser.add_argument("--reserve", type=int, default=260, help="Credits that must remain after each chunk.")
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--db", default=str(DATA_ROOT / "ranksim_raw.sqlite"))
    parser.add_argument("--source-prefix", default="claude-daily")
    parser.add_argument("--dry-run", action="store_true", help="Check the plan and the balance. Spend nothing.")
    parser.add_argument("--no-git", action="store_true", help="Do not commit or push chunk files (tests only).")
    args = parser.parse_args(argv)

    import httpx
    from lib import collect_snapshot as cs
    from lib.db import init_db

    date = args.date or today_et()
    queries: list[str] = []
    for name in args.queries:
        path = Path(name)
        if not path.is_absolute() or not path.exists():
            raise SystemExit(f"Query file must be an existing absolute path: {name}")
        for query in cs.load_queries(path):
            if query not in queries:
                queries.append(query)
    if not queries:
        raise SystemExit("No queries.")
    chunks = [queries[index : index + args.chunk_size] for index in range(0, len(queries), args.chunk_size)]
    source = f"{args.source_prefix}:{date}:{args.label}"
    out_dir = SERP_DIR / date / args.label

    key = load_key()
    balance = cs.account_balance(key)
    if balance is None:
        raise SystemExit("Could not read the Serper balance. Refusing to spend.")
    print(f"date={date} label={args.label} queries={len(queries)} chunks={len(chunks)} balance_before={balance} reserve={args.reserve}")
    if args.dry_run:
        print("dry run: nothing spent")
        return 0

    if not args.no_git:
        sync_branch()
    out_dir.mkdir(parents=True, exist_ok=True)
    con = init_db(args.db)
    pulled = imported = 0
    try:
        with httpx.Client(timeout=30.0) as client:
            for number, chunk in enumerate(chunks, 1):
                path = out_dir / f"chunk-{number:02d}.json.gz"
                if path.exists():
                    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                    if [item["query"] for item in payload["items"]] != chunk:
                        raise SystemExit(f"{path.name} does not match the query list. Use a new label.")
                    imported += import_chunk(con, payload)
                    continue
                balance = cs.account_balance(key)
                if balance is None or balance - len(chunk) < args.reserve:
                    raise SystemExit(f"Refusing chunk {number}: balance {balance} minus {len(chunk)} is under the reserve {args.reserve}.")
                payload = {"label": args.label, "date": date, "source": source, "chunk": number, "items": pull_chunk(client, key, chunk)}
                blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if key.encode("utf-8") in blob:
                    raise SystemExit("Refusing to write a file that contains the API key.")
                path.write_bytes(gzip.compress(blob, mtime=0))
                import_chunk(con, payload)
                if not args.no_git:
                    persist(path, f"Raw SERP pull {date} {args.label} chunk {number:02d} of {len(chunks)}.")
                pulled += len(chunk)
                print(f"chunk {number}/{len(chunks)} stored ({len(chunk)} queries)")
    finally:
        con.close()
    after = cs.account_balance(key)
    print(f"finished label={args.label} pulled_now={pulled} reimported_rows={imported} balance_after={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
