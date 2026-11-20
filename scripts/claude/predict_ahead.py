"""Write the frozen predictors' answers for a day BEFORE that day is pulled. PREDICTED.

  python scripts/claude/predict_ahead.py --day 2026-11-19

Reads SERP rows through the calendar day before --day only. Requires the Target 1 and
Target 3 freezes to be committed and pushed. Writes one gzip JSON file under
evals/claude/ahead/ and appends its sha256 to evals/claude/ahead/ledger.jsonl. Commit and
push both before pulling the day. The scorer later checks that what it computes equals
this file, so the predictions provably existed before the outcome.

Target 3: for every search, yesterday's consensus top 10 with scorer A's score per URL and
scorer B's flip call for every ordered pair.
Target 1: for every search, the predicted position of a new URL from each site already
seen for that search, the prediction for a new URL from any other site, and the predicted
position of every URL seen before that was not in yesterday's top 10 (a reentrant).

Screen output is counts and a hash only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import rsdata as rs  # noqa: E402

AHEAD = ROOT / "evals" / "claude" / "ahead"
UNSEEN_SITE = "__unseen_site__"


def ahead_path(day: str) -> Path:
    return AHEAD / f"ahead-{day}.json.gz"


def build(day: str) -> dict:
    from scripts.claude import t1_placement, t3_contested, t3_movers

    cutoff = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    rows = rs.load_rows(cutoff)
    if len(rows) and rows["day"].max() >= day:
        raise AssertionError("history reaches the day being predicted")
    cons = rs.consensus_table(rows)
    days = rs.query_days(cons)
    by_query_day = {key: group for key, group in cons.groupby(["query", "day"])}
    out: dict = {"label": "PREDICTED", "day": day, "history_through": cutoff, "t3": {}, "t1": {}}

    history = t1_placement.History(rows)
    for query in sorted(days):
        day_a = days[query][-1]
        top = by_query_day[(query, day_a)]
        top = top[top["cons"] <= 10].sort_values(["cons", "key"])
        keys = list(top["key"])
        position = dict(zip(top["key"], top["cons"], strict=True))
        pairs = [(x, y) for x in keys for y in keys if position[x] < position[y]]
        scores = t3_movers.score_urls(rows, query, keys, day) if keys else {}
        flags = t3_contested.call_flips(rows, query, pairs, day) if pairs else []
        out["t3"][query] = {
            "day_a": day_a,
            "scores_a": {key: float(scores[key]) for key in keys},
            "flips_b": [[x, y, bool(flag)] for (x, y), flag in zip(pairs, flags, strict=True)],
        }

        mine = rows[rows["query"] == query]
        sites = sorted(set(mine["site"]))
        new_targets = pd.DataFrame(
            {
                "query": query,
                "key": [f"__new__::{site}" for site in sites] + [f"__new__::{UNSEEN_SITE}"],
                "url": [f"https://{site}/__new__" for site in sites] + ["https://unseen-site.invalid/__new__"],
                "site": sites + [UNSEEN_SITE],
                "day": day,
            }
        )
        new_preds = t1_placement.predict_rows(rows, new_targets, hist=history)
        seen = mine.drop_duplicates("key")[["key", "url", "site"]]
        reentrant = seen[~seen["key"].isin(set(keys))].copy()
        reentrant["query"] = query
        reentrant["day"] = day
        re_preds = t1_placement.predict_rows(rows, reentrant[["query", "key", "url", "site", "day"]], hist=history) if len(reentrant) else []
        out["t1"][query] = {
            "prev_day": day_a,
            "new_by_site": {site: int(value) for site, value in zip(new_targets["site"], new_preds, strict=True)},
            "reentrant_by_key": {key: int(value) for key, value in zip(reentrant["key"], re_preds, strict=True)},
        }
    return out


def load(day: str) -> dict | None:
    path = ahead_path(day)
    if not path.exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--day", required=True, help="The day to be predicted (not yet pulled).")
    args = parser.parse_args(argv)

    from scripts.claude.score_heldout import require_frozen

    require_frozen("t1")
    require_frozen("t3")
    path = ahead_path(args.day)
    if path.exists():
        raise SystemExit(f"{path.name} already exists. Ahead files are written once.")
    payload = build(args.day)
    blob = gzip.compress(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"), mtime=0)
    AHEAD.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    entry = {
        "day": args.day,
        "file": str(path.relative_to(ROOT)),
        "sha256": digest,
        "written_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_through": payload["history_through"],
        "searches": len(payload["t3"]),
    }
    with (AHEAD / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    pairs = sum(len(block["flips_b"]) for block in payload["t3"].values())
    print(f"ahead file for {args.day}: searches={len(payload['t3'])} t3_pairs={pairs} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
