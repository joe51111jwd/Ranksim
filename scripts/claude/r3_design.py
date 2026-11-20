"""Round 3 design evaluation: what orders the pages of one site for one search? Train lists only.

  python scripts/claude/r3_design.py [--labels validity-a train-a] [--no-semantic]

Reads only the training lists (train sites x train search groups, see
evals/claude/r3/plans/universe.json). Every model number is PREDICTED and comes
from site-held-out cross-validation: each site's lists are scored by a model that
never saw any list of that site. The stricter column also drops the held-out
fold's search groups from the training side.

This is design, not a test. The held-out test lists (plan test1) are pulled only
after the chosen scorer is frozen.

Output: evals/claude/r3/design/results.json, tables.md, table.csv.gz (features, no HTML).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_dataset, r3_model as R  # noqa: E402
from scripts.claude import r3_plans  # noqa: E402

OUT = REPO / "evals" / "claude" / "r3" / "design"


def strata(pairs: pd.DataFrame, table: pd.DataFrame) -> dict[str, np.ndarray]:
    """Pre-stated slices of the pair universe (boolean masks)."""
    from urllib.parse import urlsplit

    def segs(url: str) -> list[str]:
        return [seg for seg in urlsplit(url).path.split("/") if seg]

    url = table["url"]
    seg_hi = [segs(u) for u in url.loc[pairs["hi"]]]
    seg_lo = [segs(u) for u in url.loc[pairs["lo"]]]
    same_template = np.array([len(a) == len(b) and len(a) >= 2 and a[0].lower() == b[0].lower() for a, b in zip(seg_hi, seg_lo, strict=True)])
    gap = (pairs["rank_lo"] - pairs["rank_hi"]).to_numpy()
    return {
        "all pairs": np.ones(len(pairs), dtype=bool),
        "pairs with the list's first page": (pairs["rank_hi"] == 1).to_numpy(),
        "pairs without the first page": (pairs["rank_hi"] > 1).to_numpy(),
        "adjacent in the list (gap 1)": gap == 1,
        "gap 2 to 4": (gap >= 2) & (gap <= 4),
        "gap 5 or more": gap >= 5,
        "same template (same first folder, same depth, depth 2 or more)": same_template,
        "both in the list's top 5": (pairs["rank_lo"] <= 5).to_numpy(),
    }


def swap_pairs(pairs: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Pairs of pages that appear in two lists of the same site in opposite orders.

    A scorer that ignores the search gives the same answer in both lists and is
    right exactly once. So accuracy above one half on these rows can only come
    from reading the search against the page.
    """
    key = table["key"]
    frame = pairs.assign(key_hi=key.loc[pairs["hi"]].to_numpy(), key_lo=key.loc[pairs["lo"]].to_numpy())
    frame["a"] = np.where(frame["key_hi"] < frame["key_lo"], frame["key_hi"], frame["key_lo"])
    frame["b"] = np.where(frame["key_hi"] < frame["key_lo"], frame["key_lo"], frame["key_hi"])
    frame["a_first"] = frame["key_hi"] == frame["a"]
    keep = []
    for (_site, _a, _b), group in frame.groupby(["site", "a", "b"], sort=True):
        if group["list_id"].nunique() >= 2 and group["a_first"].nunique() == 2:
            keep.extend(group.index.tolist())
    return frame.loc[keep]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels", nargs="+", default=["validity-a", "train-a"])
    parser.add_argument("--no-semantic", action="store_true")
    args = parser.parse_args(argv)

    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    table, df_table = r3_dataset.build_table(args.labels, with_semantic=not args.no_semantic)
    # Train cell only: train sites and train search groups.
    site_fold = universe["site_fold"]
    group_fold = universe["group_fold"]
    in_cell = table["site"].map(lambda site: site_fold.get(site) == "train") & table["query"].map(lambda q: group_fold.get(str(group_of.get(q))) == "train")
    dropped = int((~in_cell).sum())
    table = table[in_cell].reset_index(drop=True)
    available = set(table.columns)
    pairs = R.list_pairs_index(table)
    usable = table[table["usable"]]
    res: dict = {
        "labels": args.labels,
        "rows_dropped_outside_train_cell": dropped,
        "counts": {
            "lists_pulled": int(table["list_id"].nunique()),
            "listed_pages": int(len(table)),
            "usable_pages": int(len(usable)),
            "lists_with_pairs": int(pairs["list_id"].nunique()),
            "pairs": int(len(pairs)),
            "sites_with_pairs": int(pairs["site"].nunique()),
            "searches_with_pairs": int(pairs["query"].nunique()),
            "distinct_usable_urls": int(usable["key"].nunique()),
            "top5_sites_by_pairs": pairs["site"].value_counts().head(5).to_dict(),
        },
        "semantic_features": sorted(set(R.TIERS["semantic"]) & available),
    }

    # Single features, raw direction.
    singles = {}
    for name in sorted(set(sum(R.TIERS.values(), [])) & available - set(R.TIERS["query"])):
        hits = R.pair_hits(pairs, table[name].astype(float).fillna(table.groupby("list_id")[name].transform("mean")).fillna(0.0))
        entry = R.accuracy(pairs, hits)
        decided = hits != 0.5
        entry["decided"] = int(decided.sum())
        entry["decided_rate"] = float(hits[decided].mean()) if decided.any() else None
        singles[name] = entry
    res["single_features"] = singles

    scorers: dict[str, pd.Series] = {}
    scorers["bm25f"] = table["bm25f"].astype(float)
    scorers["vote"] = R.vote_scores(table)
    sem = [name for name in R.TIERS["semantic"] if name in available]
    if sem:
        scorers["ce_title alone"] = table["ce_title"].astype(float)
        scorers["ce_body_max alone"] = table["ce_body_max"].astype(float)
    variants = {
        "logit text": ("logit", ["lexical", "semantic"]),
        "logit text+url": ("logit", ["lexical", "semantic", "url"]),
        "logit text+url+shape": ("logit", ["lexical", "semantic", "url", "shape"]),
        "logit text+url+shape+site": ("logit", ["lexical", "semantic", "url", "shape", "site"]),
        "logit lexical only": ("logit", ["lexical"]),
        "logit lexical+url": ("logit", ["lexical", "url"]),
        "gbm text+url+shape": ("gbm", ["lexical", "semantic", "url", "shape", "query"]),
        "gbm text+url+shape+site": ("gbm", ["lexical", "semantic", "url", "shape", "site", "query"]),
        "blend text+url+shape": ("blend", ["lexical", "semantic", "url", "shape", "query"]),
        "blend text+url+shape+site": ("blend", ["lexical", "semantic", "url", "shape", "site", "query"]),
    }
    strict: dict[str, pd.Series] = {}
    for name, (kind, tiers) in variants.items():
        feats = R.feature_set(tiers, available)
        if kind == "logit":
            feats = [f for f in feats if f not in R.TIERS["query"]]
        scorers[name] = R.cross_validate(table, kind, feats)
        strict[name] = R.cross_validate(table, kind, feats, also_hold_out_queries=True, group_of=group_of)

    base_hits = R.pair_hits(pairs, scorers["bm25f"])
    masks = strata(pairs, table)
    out_scorers = {}
    for name, scores in scorers.items():
        hits = R.pair_hits(pairs, scores.fillna(0.0))
        entry = {"all": R.accuracy(pairs, hits), "minus_bm25f": R.difference(pairs, hits, base_hits)}
        entry["strata"] = {label: R.accuracy(pairs[mask], hits[mask]) for label, mask in masks.items()}
        if name in strict:
            s_hits = R.pair_hits(pairs, strict[name].fillna(0.0))
            entry["strict_sites_and_search_groups"] = R.accuracy(pairs, s_hits)
        out_scorers[name] = entry
    res["scorers"] = out_scorers

    swaps = swap_pairs(pairs, table)
    res["swap_pairs"] = {"rows": int(len(swaps)), "sites": int(swaps["site"].nunique()) if len(swaps) else 0}
    if len(swaps):
        for name, scores in scorers.items():
            hits = R.pair_hits(swaps, scores.fillna(0.0))
            res["swap_pairs"][name] = R.accuracy(swaps, hits)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    keep_cols = [c for c in table.columns if c not in ("url",)]
    table[keep_cols].to_csv(OUT / "table.csv.gz", index=False)
    (OUT / "df_table.json").write_text(json.dumps(df_table) + "\n", encoding="utf-8")

    lines = [
        "# Round 3 design: ordering the pages of one site for one search (scripts/claude/r3_design.py)",
        "",
        f"Train lists only ({', '.join(args.labels)}). REAL list order from Serper. Model numbers PREDICTED, from site-held-out cross-validation. Ties count half. Design, not a test.",
        "",
        f"Counts: {json.dumps(res['counts'])}",
        "",
        "| Scorer | All pairs | Minus BM25F | Also holding out search groups |",
        "| --- | --- | --- | --- |",
    ]
    for name, entry in out_scorers.items():
        strict_text = R.fmt(entry["strict_sites_and_search_groups"]) if "strict_sites_and_search_groups" in entry else ""
        lines.append(f"| {name} | {R.fmt(entry['all'])} | {R.fmt_diff(entry['minus_bm25f'])} | {strict_text} |")
    lines += ["", "## Slices", ""]
    show = [name for name in ("bm25f", "vote", "logit text+url+shape", "gbm text+url+shape", "blend text+url+shape", "blend text+url+shape+site") if name in out_scorers]
    lines.append("| Slice | " + " | ".join(show) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in show) + " |")
    for label in masks:
        cells = []
        for name in show:
            e = out_scorers[name]["strata"][label]
            cells.append(f"{e['rate']:.3f} ({e['n']})" if e.get("n") else "n/a")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if len(swaps):
        cells = [f"{res['swap_pairs'][name]['rate']:.3f}" for name in show]
        lines.append(f"| swap pairs ({len(swaps)} rows on {res['swap_pairs']['sites']} sites) | " + " | ".join(cells) + " |")
    lines += ["", "## Single features (larger value first; ties half)", "", "| Feature | All pairs | Decided pairs |", "| --- | --- | --- |"]
    for name, entry in sorted(singles.items(), key=lambda item: -abs(item[1]["rate"] - 0.5)):
        dec = f"{entry['decided_rate']:.3f} of {entry['decided']}" if entry["decided"] else "n/a"
        lines.append(f"| {name} | {entry['rate']:.3f} [{entry['ci_site'][0]:.3f}, {entry['ci_site'][1]:.3f}] | {dec} |")
    (OUT / "tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
