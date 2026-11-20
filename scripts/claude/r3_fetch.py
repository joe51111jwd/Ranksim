"""Round 3: plain-HTTP fetch of the pages listed in site-restricted SERP pulls.

  python scripts/claude/r3_fetch.py --label validity-a [--label ...] [--fresh-since 2026-11-16]

One polite fetch per URL (robots.txt obeyed, per-host delay, same collector code
as scripts/claude/refetch_html.py), appended to ``pages`` in the raw store. A URL
that already has a good plain-HTTP fetch on or after ``--fresh-since`` is not
fetched again. No Serper, no key, nothing paid. Screen output is counts only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_data  # noqa: E402
from scripts.claude import refetch_html as rf  # noqa: E402

SIDECAR_DIR = REPO / "evals" / "claude" / "r3" / "fetch"


def urls_for(labels: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for label in labels:
        frame = r3_data.load_label(label)
        for key, url in zip(frame["key"], frame["url"], strict=True):
            seen.setdefault(key, url)
    return sorted(seen.values())


def already_fresh(con, urls: list[str], since: str) -> set[str]:
    fresh: set[str] = set()
    for start in range(0, len(urls), 400):
        part = urls[start : start + 400]
        marks = ",".join("?" for _ in part)
        query = (
            f"SELECT DISTINCT url FROM pages WHERE url IN ({marks}) AND fetch_method = 'http' "
            "AND fetched_at_utc >= ? AND html IS NOT NULL AND length(html) > 500"
        )
        fresh.update(row[0] for row in con.execute(query, (*part, since)))
    return fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", action="append", required=True)
    parser.add_argument("--fresh-since", default="2026-11-16")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--db", default=str(rf.DEFAULT_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    urls = urls_for(args.label)
    con = rf.connect(args.db)
    try:
        fresh = already_fresh(con, urls, args.fresh_since)
        todo = [url for url in urls if url not in fresh]
        print(f"labels={args.label} urls={len(urls)} already_fresh={len(fresh)} to_fetch={len(todo)}")
        if args.dry_run or not todo:
            return 0
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        sidecar = SIDECAR_DIR / f"fetch-{'-'.join(args.label)[:60]}-{stamp}.jsonl"
        summary = rf.run_pass(con, todo, rf.make_http_fetch(), sidecar, workers=args.workers)
    finally:
        con.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    (SIDECAR_DIR / f"summary-{'-'.join(args.label)[:60]}-{stamp}.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
