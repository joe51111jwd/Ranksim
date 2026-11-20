"""Round 3 pull plans: which site-restricted searches to send, chosen from design days only.

  python scripts/claude/r3_plans.py validity     # study 1: does site: order agree with ordinary order?

Every plan is built from SERP rows dated 2026-11-17 or earlier (rsdata.DESIGN_MAX_DATE),
with a fixed seed, and written to evals/claude/r3/plans/<name>.jsonl plus a
<name>.meta.json that records how it was drawn. The client's own site is never
in a plan (see CLIENT_SITES): its searches carry the pre-registered PR 45 edit and no
after-merge result for it may be read before those predictions are frozen.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import rsdata as rs  # noqa: E402

PLAN_DIR = REPO / "evals" / "claude" / "r3" / "plans"
SEED = 20261119
CLIENT_SITES = {"smartcrewhq.com"}


def site_query(site: str, query: str) -> str:
    return f"site:{site} {query}"


def write_plan(name: str, requests: list[dict], meta: dict) -> Path:
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PLAN_DIR / f"{name}.jsonl"
    text = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests)
    path.write_text(text, encoding="utf-8")
    meta = dict(meta, requests=len(requests), sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    (PLAN_DIR / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def validity_plan(per_site: int = 4, total: int = 70) -> tuple[list[dict], dict]:
    """(search, site) combos with two or more URLs of the site in the Nov 16 deep list (positions 1 to 30).

    These are the combos where the ordinary list already orders same-site pages,
    so the site-restricted order can be checked against it. At most ``per_site``
    combos per site, drawn in a seeded random order, then the first ``total``.
    """
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    deep = rs.deep_table(rows)
    deep = deep[~deep["site"].isin(CLIENT_SITES)]
    counts = deep.groupby(["query", "site"]).size().reset_index(name="urls")
    multi = counts[counts["urls"] >= 2].sort_values(["site", "query"]).reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    order = rng.permutation(len(multi))
    taken: dict[str, int] = {}
    picked = []
    for index in order:
        record = multi.iloc[int(index)]
        if taken.get(record.site, 0) >= per_site:
            continue
        taken[record.site] = taken.get(record.site, 0) + 1
        picked.append({"q": site_query(record.site, record["query"]), "page": 1, "site": record.site, "query": record["query"]})
        if len(picked) >= total:
            break
    meta = {
        "plan": "validity",
        "design_max_date": rs.DESIGN_MAX_DATE,
        "seed": SEED,
        "per_site_cap": per_site,
        "combos_available": int(len(multi)),
        "sites_available": int(multi["site"].nunique()),
        "sites_in_plan": len(taken),
        "excluded_sites": sorted(CLIENT_SITES),
    }
    return picked, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plan", choices=["validity"])
    args = parser.parse_args(argv)
    if args.plan == "validity":
        requests, meta = validity_plan()
        path = write_plan("validity", requests, meta)
        print(f"{path.relative_to(REPO)}: {len(requests)} requests on {meta['sites_in_plan']} sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
