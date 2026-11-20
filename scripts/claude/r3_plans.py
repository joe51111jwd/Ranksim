"""Round 3 pull plans: which site-restricted searches to send, chosen from design days only.

  python scripts/claude/r3_plans.py validity     # study 1: does site: order agree with ordinary order?
  python scripts/claude/r3_plans.py universe     # the frozen split of sites and search groups
  python scripts/claude/r3_plans.py train        # lists to learn from (train sites x train search groups)
  python scripts/claude/r3_plans.py test1        # held-out lists (test sites x test search groups), pulled only after the freeze
  python scripts/claude/r3_plans.py noise        # a repeat of some validity lists, to measure label noise
  python scripts/claude/r3_plans.py heldout1     # the held-out plan that is actually pulled: 104 lists, one per site first

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


# ---------------------------------------------------------------------------------------------
# The split. Fixed from design days and a seed, before any round 3 model exists.

SITE_TRAIN_SHARE = 0.40
SITE_TEST1_SHARE = 0.35  # the rest (0.25) is a second held-out site set, kept back for a later loop
UNIVERSE_PATH = PLAN_DIR / "universe.json"


def _unit(text: str) -> float:
    return int(hashlib.sha1(f"{SEED}:{text}".encode("utf-8")).hexdigest()[:12], 16) / float(16**12)


def fetchable_sites() -> dict[str, float]:
    """Share of good plain-HTTP fetches per site in the raw store (page content only, no ranks)."""
    import sqlite3

    with sqlite3.connect(f"file:{rs.DB}?mode=ro", uri=True) as con:
        frame = pd.read_sql_query(
            "SELECT url, fetch_method, length(html) AS n FROM pages WHERE fetch_method LIKE 'http%' AND fetched_at_utc < '2026-11-20T02:00:00'",
            con,
        )
    frame["site"] = frame["url"].map(rs.site_key)
    frame["ok"] = (frame["fetch_method"] == "http") & (frame["n"].fillna(0) >= 500)
    stats = frame.groupby("site")["ok"].agg(["sum", "size"])
    return (stats["sum"] / stats["size"]).to_dict()


def build_universe() -> dict:
    """Every (search, site) combo of design days, with the frozen fold of its site and of its search group.

    Search groups: near-duplicate searches are linked (r2_samesite_pairs.assign_folds), so
    two wordings of one search never sit on both sides. A group that the validity plan
    already used is a train group. Every other group is a test group.
    Sites: a validity-plan site is a train site. Every other site is drawn by a seeded
    hash into train (0.40), test1 (0.35) or test2 (0.25, kept back).
    Left out: the client's site, user-content platforms (each URL has a different author),
    and sites where no plain-HTTP fetch has ever worked (the content model cannot read them).
    """
    from scripts.claude.r2_samesite_pairs import UGC, assign_folds

    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    labels = rs.query_groups(rows)
    _fold, groups = assign_folds(sorted(labels))
    group_of = {query: index for index, group in enumerate(groups) for query in group}
    validity = [json.loads(line) for line in (PLAN_DIR / "validity.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    validity_sites = {item["site"] for item in validity}
    touched = {group_of[item["query"]] for item in validity}
    rate = fetchable_sites()
    combos = rows.groupby(["query", "site"]).size().reset_index(name="rows")
    combos = combos[~combos["site"].isin(CLIENT_SITES | set(UGC))]
    combos = combos[combos["site"].map(lambda site: rate.get(site, 0.0) > 0.0)]
    site_fold = {}
    for site in sorted(set(combos["site"])):
        if site in validity_sites:
            site_fold[site] = "train"
        else:
            draw = _unit("site:" + site)
            site_fold[site] = "train" if draw < SITE_TRAIN_SHARE else ("test1" if draw < SITE_TRAIN_SHARE + SITE_TEST1_SHARE else "test2")
    group_fold = {index: ("train" if index in touched else "test") for index in range(len(groups))}
    records = []
    for query, site in zip(combos["query"], combos["site"], strict=True):
        records.append({"query": query, "site": site, "group": group_of[query], "site_fold": site_fold[site], "query_fold": group_fold[group_of[query]]})
    return {
        "design_max_date": rs.DESIGN_MAX_DATE,
        "seed": SEED,
        "site_shares": {"train": SITE_TRAIN_SHARE, "test1": SITE_TEST1_SHARE, "test2": round(1 - SITE_TRAIN_SHARE - SITE_TEST1_SHARE, 2)},
        "groups": [sorted(group) for group in groups],
        "group_fold": {str(index): fold for index, fold in group_fold.items()},
        "site_fold": site_fold,
        "combos": records,
    }


def load_universe() -> dict:
    return json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))


def cell(universe: dict, site_fold: str, query_fold: str) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(universe["combos"])
    return frame[(frame["site_fold"] == site_fold) & (frame["query_fold"] == query_fold)].reset_index(drop=True)


def draw_lists(frame: pd.DataFrame, total: int, per_site: int, per_query: int, skip: set[tuple[str, str]], site_used: dict[str, int], tag: str) -> list[dict]:
    """Seeded random order, at most per_site lists a site and per_query lists a search."""
    frame = frame.sort_values(["site", "query"]).reset_index(drop=True)
    order = np.random.default_rng(int(hashlib.sha1(f"{SEED}:{tag}".encode()).hexdigest()[:8], 16)).permutation(len(frame))
    sites = dict(site_used)
    queries: dict[str, int] = {}
    picked = []
    for index in order:
        record = frame.iloc[int(index)]
        if (record["query"], record["site"]) in skip:
            continue
        if sites.get(record["site"], 0) >= per_site or queries.get(record["query"], 0) >= per_query:
            continue
        sites[record["site"]] = sites.get(record["site"], 0) + 1
        queries[record["query"]] = queries.get(record["query"], 0) + 1
        picked.append({"q": site_query(record["site"], record["query"]), "page": 1, "site": record["site"], "query": record["query"]})
        if len(picked) >= total:
            break
    return picked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("plan", choices=["validity", "universe", "train", "test1", "noise", "heldout1"])
    parser.add_argument("--total", type=int, default=None)
    args = parser.parse_args(argv)
    if args.plan == "validity":
        requests, meta = validity_plan()
        path = write_plan("validity", requests, meta)
        print(f"{path.relative_to(REPO)}: {len(requests)} requests on {meta['sites_in_plan']} sites")
        return 0
    if args.plan == "universe":
        universe = build_universe()
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        UNIVERSE_PATH.write_text(json.dumps(universe, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        frame = pd.DataFrame.from_records(universe["combos"])
        print(f"combos={len(frame)} sites={frame['site'].nunique()} searches={frame['query'].nunique()} groups={len(universe['groups'])}")
        print(frame.groupby(["site_fold", "query_fold"]).agg(combos=("site", "size"), sites=("site", "nunique"), searches=("query", "nunique")).to_string())
        return 0
    universe = load_universe()
    validity = [json.loads(line) for line in (PLAN_DIR / "validity.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.plan == "train":
        used = {(item["query"], item["site"]) for item in validity}
        site_used: dict[str, int] = {}
        for item in validity:
            site_used[item["site"]] = site_used.get(item["site"], 0) + 1
        requests = draw_lists(cell(universe, "train", "train"), args.total or 180, 3, 8, used, site_used, "train")
        meta = {"plan": "train", "cell": "train sites x train search groups", "per_site_cap": 3, "per_query_cap": 8, "validity_lists_count_toward_site_cap": True}
    elif args.plan == "test1":
        requests = draw_lists(cell(universe, "test1", "test"), args.total or 140, 3, 8, set(), {}, "test1")
        meta = {"plan": "test1", "cell": "test1 sites x test search groups", "per_site_cap": 3, "per_query_cap": 8,
                "status": "superseded by heldout1 before any pull; never pulled"}
    elif args.plan == "heldout1":
        # Each list is pulled twice, so the credit budget allows about 104 lists. The interval is driven by
        # the number of sites, so every test1 site gets one list first, then second lists fill the rest.
        total = args.total or 104
        frame = cell(universe, "test1", "test")
        first = draw_lists(frame, total, 1, 8, set(), {}, "heldout1-first")
        used = {(item["query"], item["site"]) for item in first}
        site_used = {item["site"]: 1 for item in first}
        query_used: dict[str, int] = {}
        for item in first:
            query_used[item["query"]] = query_used.get(item["query"], 0) + 1
        extra = []
        if len(first) < total:
            for item in draw_lists(frame, len(frame), 2, 8, used, site_used, "heldout1-second"):
                if query_used.get(item["query"], 0) >= 8:
                    continue
                query_used[item["query"]] = query_used.get(item["query"], 0) + 1
                extra.append(item)
                if len(first) + len(extra) >= total:
                    break
        requests = first + extra
        meta = {"plan": "heldout1", "cell": "test1 sites x test search groups", "first_pass": "one list per site", "second_pass": "a second list for some sites",
                "per_query_cap": 8, "lists_first_pass": len(first), "lists_second_pass": len(extra), "pulls_per_list": 2}
    else:
        # Repeat one list per validity site that had readable pages, seeded order.
        rate = fetchable_sites()
        seen: set[str] = set()
        requests = []
        order = np.random.default_rng(SEED + 1).permutation(len(validity))
        for index in order:
            item = validity[int(index)]
            if item["site"] in seen or rate.get(item["site"], 0.0) < 0.5:
                continue
            seen.add(item["site"])
            requests.append(item)
            if len(requests) >= (args.total or 30):
                break
        meta = {"plan": "noise", "rule": "one validity list per site with a plain-HTTP success share of 0.5 or more, seeded order"}
    meta.update({"design_max_date": rs.DESIGN_MAX_DATE, "seed": SEED, "sites_in_plan": len({item["site"] for item in requests}), "searches_in_plan": len({item["query"] for item in requests})})
    path = write_plan(args.plan, requests, meta)
    print(f"{path.relative_to(REPO)}: {len(requests)} requests on {meta['sites_in_plan']} sites and {meta['searches_in_plan']} searches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
