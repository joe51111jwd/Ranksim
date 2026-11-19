"""One top-10 Serper pull of a query list, appended to the raw SQLite store.

Usage:
  python scripts/claude/pull_serps.py --label pull-a --queries /abs/path/queries.txt [--date YYYY-MM-DD]

The key is read from SERPER_API_KEY or /workspace/ranksim-secrets/serper.env and is
never printed, logged, or written. The run refuses to start when the balance after
the planned spend would fall under the reserve (default 260, the brief says keep 250).
SERP rows go to ranksim_raw.sqlite through the existing append-only helpers in
/workspace/ranksim-data/lib. No HTML is fetched here (scripts/claude/refetch_html.py does that).
Output on screen is counts only, so a held-out day can be pulled without being read.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_ROOT = Path("/workspace/ranksim-data")
SECRETS = Path("/workspace/ranksim-secrets/serper.env")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", required=True, help="Pull label, for example pull-a")
    parser.add_argument("--queries", required=True, action="append", help="Absolute path to a query file. Repeatable.")
    parser.add_argument("--date", default=None, help="Snapshot date (America/New_York). Default: today.")
    parser.add_argument("--reserve", type=int, default=260, help="Credits that must remain after this pull.")
    parser.add_argument("--db", default=str(DATA_ROOT / "ranksim_raw.sqlite"))
    parser.add_argument("--out-root", default=str(DATA_ROOT / "snapshots"))
    parser.add_argument("--source-prefix", default="claude-daily")
    parser.add_argument("--dry-run", action="store_true", help="Check the plan and the balance. Spend nothing.")
    parser.add_argument("--retries", type=int, default=6)
    args = parser.parse_args(argv)

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

    key = load_key()
    balance = cs.account_balance(key)
    if balance is None:
        raise SystemExit("Could not read the Serper balance. Refusing to spend.")
    planned = len(queries)
    print(f"date={date} label={args.label} queries={planned} balance_before={balance} reserve={args.reserve}")
    if balance - planned < args.reserve:
        raise SystemExit(f"Refusing: {balance} - {planned} would fall under the reserve of {args.reserve}.")
    if args.dry_run:
        print("dry run: nothing spent")
        return 0

    out_dir = Path(args.out_root) / f"{date}-claude-{args.label}"
    out_dir.mkdir(parents=True, exist_ok=True)
    source = f"{args.source_prefix}:{date}:{args.label}"
    con = init_db(args.db)
    spent_total = 0
    records: list[dict] = []
    try:
        for attempt in range(1, args.retries + 1):
            try:
                # collect_serps resumes: a query with a raw file from this run is skipped.
                spent, records = cs.collect_serps(
                    out_dir=out_dir,
                    queries=queries,
                    snapshot_date=date,
                    source=source,
                    key=key,
                    credit_cap=planned,
                    already_spent=spent_total,
                    con=con,
                )
                spent_total += spent
                break
            except SystemExit:
                raise
            except Exception as exc:  # network errors: wait and resume
                print(f"attempt {attempt} failed with {type(exc).__name__}; resuming after a pause")
                time.sleep(20 * attempt)
        else:
            raise SystemExit("Pull did not finish after the allowed retries.")
        con.commit()
    finally:
        con.close()
    done = sum(1 for record in records if record.get("status") == 200)
    rows = cs.write_serp_csv(out_dir, records)
    after = cs.account_balance(key)
    print(f"finished label={args.label} requests_ok={done}/{planned} csv_rows={len(rows)} balance_after={after}")
    return 0 if done == planned else 1


if __name__ == "__main__":
    raise SystemExit(main())
