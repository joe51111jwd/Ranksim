"""Round 3 Serper pull of an arbitrary plan of searches (mostly site-restricted), stored raw.

Usage:
  python scripts/claude/r3_pull.py --label NAME --plan /abs/path/plan.jsonl [--dry-run]

A plan is a JSONL file, one request per line: {"q": "site:example.com some search", "page": 1}.
``page`` is optional (default 1). Every request costs one Serper credit (num=10).

Where the data goes:
  * evals/claude/r3/serp/<label>/chunk-NNN.json.gz in git, committed and pushed
    after every chunk, so a machine restore loses one chunk of credits at most.
    A rerun imports chunks already on disk and only pulls what is missing.
  * the append-only raw store, table ``serp_raw`` only (source ``claude-r3:<label>``).
    Nothing is written to ``serp_results``: that table feeds the sealed day
    series of the frozen Target 1 to 3 scorers (scripts/claude/rsdata.py), and
    these searches are a different kind of data. Round 3 code reads the chunk
    files through scripts/claude/r3_data.py.

The key is read from SERPER_API_KEY or the secrets file and is never printed,
logged or written. A chunk is refused when the balance after it would be under
the reserve (default 260, the brief says keep 250). Screen output is counts only.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude.pull_serps import DATA_ROOT, ENDPOINT, load_key, persist, sync_branch, today_et  # noqa: E402

SERP_DIR = REPO / "evals" / "claude" / "r3" / "serp"
BASE_PARAMS = {"gl": "us", "hl": "en", "num": 10}
SOURCE_PREFIX = "claude-r3"


def load_plan(path: Path) -> list[dict]:
    """Requests of a plan file, in file order, exact duplicates dropped."""
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = json.loads(line)
        request = {"q": str(item["q"]).strip(), "page": int(item.get("page") or 1)}
        ident = (request["q"], request["page"])
        if request["q"] and ident not in seen:
            seen.add(ident)
            out.append(request)
    return out


def request_body(request: dict) -> dict:
    body = {"q": request["q"], **BASE_PARAMS}
    if request["page"] != 1:
        body["page"] = request["page"]
    return body


def import_chunk(con, payload: dict) -> int:
    """Append a chunk's raw responses to serp_raw. Safe to repeat."""
    from lib.db import insert_serp_raw, to_utc_iso

    added = 0
    for item in payload["items"]:
        body = json.loads(item["raw"])
        raw_id = insert_serp_raw(
            con,
            {
                "query": item["q"],
                "fetched_at_utc": to_utc_iso(item["start"]),
                "gl": BASE_PARAMS["gl"],
                "hl": BASE_PARAMS["hl"],
                "num": BASE_PARAMS["num"],
                "endpoint": ENDPOINT,
                "provider": "serper",
                "request_params": json.dumps(request_body(item), ensure_ascii=False),
                "raw_json": item["raw"],
                "http_status": item["status"],
                "credits": body.get("credits"),
                "source": payload["source"],
                "snapshot_date": payload["date"],
            },
        )
        added += 1 if raw_id is not None else 0
    con.commit()
    return added


def pull_chunk(client, key: str, requests: list[dict], pause: float) -> list[dict]:
    items = []
    for request in requests:
        last_error = "no attempt"
        for attempt in range(1, 5):
            start = datetime.now().astimezone().isoformat()
            try:
                response = client.post(
                    ENDPOINT, headers={"X-API-KEY": key, "Content-Type": "application/json"}, json=request_body(request)
                )
            except Exception as exc:  # network error: wait and try again
                last_error = type(exc).__name__
                time.sleep(10 * attempt)
                continue
            end = datetime.now().astimezone().isoformat()
            text = response.text
            if key in text:
                raise SystemExit("Refusing to store a response that contains the API key.")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            # A site-restricted search with no hits returns 200 with no "organic" list. That is a valid answer.
            if response.status_code == 200 and isinstance(parsed, dict) and "searchParameters" in parsed:
                items.append({"q": request["q"], "page": request["page"], "start": start, "end": end, "status": 200, "raw": text})
                break
            last_error = f"http {response.status_code}"
            time.sleep(10 * attempt)
        else:
            raise SystemExit(f"A request failed four times ({last_error}). Stopping. Rerun to resume.")
        time.sleep(pause)
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True)
    parser.add_argument("--plan", required=True, help="Absolute path to a JSONL plan file.")
    parser.add_argument("--date", default=None, help="Snapshot date (America/New_York). Default: today.")
    parser.add_argument("--reserve", type=int, default=260)
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--pause", type=float, default=0.35, help="Seconds between requests (the account allows 5 a second).")
    parser.add_argument("--max-credits", type=int, default=None, help="Refuse to start if the plan would spend more than this.")
    parser.add_argument("--db", default=str(DATA_ROOT / "ranksim_raw.sqlite"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-git", action="store_true", help="Do not commit or push chunk files (tests only).")
    args = parser.parse_args(argv)

    import httpx
    from lib import collect_snapshot as cs
    from lib.db import init_db

    plan_path = Path(args.plan)
    if not plan_path.is_absolute() or not plan_path.exists():
        raise SystemExit(f"Plan must be an existing absolute path: {args.plan}")
    plan = load_plan(plan_path)
    if not plan:
        raise SystemExit("Empty plan.")
    chunks = [plan[index : index + args.chunk_size] for index in range(0, len(plan), args.chunk_size)]
    out_dir = SERP_DIR / args.label
    missing = sum(len(chunk) for number, chunk in enumerate(chunks, 1) if not (out_dir / f"chunk-{number:03d}.json.gz").exists())
    date = args.date or today_et()
    source = f"{SOURCE_PREFIX}:{args.label}"

    key = load_key()
    balance = cs.account_balance(key)
    if balance is None:
        raise SystemExit("Could not read the Serper balance. Refusing to spend.")
    print(f"label={args.label} requests={len(plan)} chunks={len(chunks)} still_to_pull={missing} balance_before={balance} reserve={args.reserve}")
    if args.max_credits is not None and missing > args.max_credits:
        raise SystemExit(f"Plan needs {missing} credits, over --max-credits {args.max_credits}.")
    if balance - missing < args.reserve:
        raise SystemExit(f"Plan needs {missing} credits and would leave {balance - missing}, under the reserve {args.reserve}.")
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
                path = out_dir / f"chunk-{number:03d}.json.gz"
                if path.exists():
                    payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                    if [(item["q"], item["page"]) for item in payload["items"]] != [(item["q"], item["page"]) for item in chunk]:
                        raise SystemExit(f"{path.name} does not match the plan. Use a new label.")
                    imported += import_chunk(con, payload)
                    continue
                balance = cs.account_balance(key)
                if balance is None or balance - len(chunk) < args.reserve:
                    raise SystemExit(f"Refusing chunk {number}: balance {balance} minus {len(chunk)} is under the reserve {args.reserve}.")
                payload = {
                    "label": args.label,
                    "date": date,
                    "source": source,
                    "chunk": number,
                    "items": pull_chunk(client, key, chunk, args.pause),
                }
                blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if key.encode("utf-8") in blob:
                    raise SystemExit("Refusing to write a file that contains the API key.")
                path.write_bytes(gzip.compress(blob, mtime=0))
                import_chunk(con, payload)
                if not args.no_git:
                    persist(path, f"Round 3 raw SERP pull {args.label} chunk {number:03d} of {len(chunks)}.")
                pulled += len(chunk)
                print(f"chunk {number}/{len(chunks)} stored ({len(chunk)} requests)", flush=True)
    finally:
        con.close()
    after = cs.account_balance(key)
    print(f"finished label={args.label} pulled_now={pulled} reimported={imported} balance_after={after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
