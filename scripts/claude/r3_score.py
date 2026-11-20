"""Round 3: score the frozen content scorer on held-out site-restricted lists. Once. PREDICTED.

  python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite --label test1-a

Refuses to run unless the preregistration manifest verifies, is committed and is
pushed (scripts/claude/r2_score.require_frozen_dir), and refuses to overwrite an
earlier result. The held-out lists must come from the plan named in the
preregistration (test sites x test search groups, never used for any design choice),
and must have been pulled after the freeze commit.

Reported, all on the same pairs (both pages of the pair have readable stored HTML):
  - pair accuracy of the frozen scorer, ties half, with 95% bootstrap intervals over
    lists, over sites and over searches (2000 draws, seed 20261119);
  - BM25F alone and the current ranker with no history (its content part), and the
    paired differences;
  - the pre-stated slices (first page, adjacent, same template, top 5, swap pairs);
  - coverage: lists pulled, lists with a scorable pair, pages listed and readable.
The pass rule is in the preregistration and is applied here mechanically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_data, r3_dataset, r3_design, r3_model as R, r3_plans, r3_ranker_baseline  # noqa: E402
from scripts.claude.r2_score import require_frozen_dir  # noqa: E402

OUT_ROOT = REPO / "evals" / "claude" / "r3" / "heldout"


def freeze_commit_time(directory: Path) -> str:
    """ISO time of the last commit that touched the preregistration manifest."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", str((directory / "manifest.json").relative_to(REPO))],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        raise SystemExit("The preregistration manifest has no commit.")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prereg", required=True)
    parser.add_argument("--label", required=True, help="Label of the held-out pull, for example test1-a")
    args = parser.parse_args(argv)

    prereg = (REPO / args.prereg).resolve()
    require_frozen_dir(prereg)
    test_spec = json.loads((prereg / "test.json").read_text(encoding="utf-8"))
    out_dir = OUT_ROOT / f"{prereg.name}-{args.label}"
    if (out_dir / "results.json").exists():
        raise SystemExit(f"{out_dir}/results.json exists. A held-out result is not overwritten.")
    if args.label != test_spec["label"]:
        raise SystemExit(f"The preregistration names label {test_spec['label']}, not {args.label}.")

    # The held-out lists: exactly the frozen plan, pulled after the freeze, inside the frozen test cell.
    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    plan = [json.loads(line) for line in (REPO / test_spec["plan"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    requests = r3_data.load_requests(args.label)
    if sorted(requests["q"]) != sorted(item["q"] for item in plan):
        raise SystemExit("The pulled requests are not exactly the frozen plan.")
    frozen_at = pd.Timestamp(freeze_commit_time(prereg))
    first_pull = pd.to_datetime(requests["fetched_at"], utc=True, format="ISO8601").min()
    if first_pull <= frozen_at.tz_convert("UTC"):
        raise SystemExit(f"A held-out list was pulled at {first_pull}, not after the freeze commit at {frozen_at}.")
    for item in plan:
        if universe["site_fold"].get(item["site"]) != test_spec["site_fold"] or universe["group_fold"].get(str(group_of.get(item["query"]))) != test_spec["query_fold"]:
            raise SystemExit(f"Plan row outside the frozen test cell: {item['q']}")

    model_dir = REPO / test_spec["model_dir"]
    scorer = R.FrozenScorer.load(model_dir)
    df_table = json.loads((model_dir / "df_table.json").read_text(encoding="utf-8"))
    table, _ = r3_dataset.build_table([args.label], df_table=df_table)
    missing = [name for name in scorer.spec["features"] if name not in table.columns]
    if missing:
        raise SystemExit(f"Features missing at scoring time: {missing}")
    table = table.reset_index(drop=True)
    pairs = R.list_pairs_index(table)

    scores = {
        "frozen scorer": scorer.score(table),
        "bm25f": table.loc[table["usable"], "bm25f"].astype(float),
        "current ranker, no history": r3_ranker_baseline.scores(table),
    }
    hits = {name: R.pair_hits(pairs, values.reindex(table.index).fillna(0.0)) for name, values in scores.items()}
    masks = r3_design.strata(pairs, table)
    swaps = r3_design.swap_pairs(pairs, table)
    usable = table[table["usable"]]
    res: dict = {
        "label": "PREDICTED",
        "prereg": prereg.name,
        "heldout_label": args.label,
        "freeze_commit_time": str(frozen_at),
        "first_heldout_pull": str(first_pull),
        "counts": {
            "lists_in_plan": len(plan),
            "lists_with_results": int(table["list_id"].nunique()),
            "listed_pages": int(len(table)),
            "usable_pages": int(len(usable)),
            "lists_with_pairs": int(pairs["list_id"].nunique()),
            "pairs": int(len(pairs)),
            "sites_with_pairs": int(pairs["site"].nunique()),
            "searches_with_pairs": int(pairs["query"].nunique()),
            "top5_sites_by_pairs": pairs["site"].value_counts().head(5).to_dict(),
        },
        "scorers": {},
    }
    for name, values in hits.items():
        entry = {"all": R.accuracy(pairs, values), "strata": {label: R.accuracy(pairs[mask], values[mask]) for label, mask in masks.items()}}
        if len(swaps):
            entry["swap_pairs"] = R.accuracy(swaps, R.pair_hits(swaps, scores[name].reindex(table.index).fillna(0.0)))
        res["scorers"][name] = entry
    res["differences"] = {
        "frozen scorer minus bm25f": R.difference(pairs, hits["frozen scorer"], hits["bm25f"]),
        "frozen scorer minus current ranker, no history": R.difference(pairs, hits["frozen scorer"], hits["current ranker, no history"]),
    }

    main_entry = res["scorers"]["frozen scorer"]["all"]
    rule = test_spec["pass_rule"]
    lower = min(main_entry["ci_site"][0], main_entry["ci_query"][0])
    d_bm = res["differences"]["frozen scorer minus bm25f"]
    d_rk = res["differences"]["frozen scorer minus current ranker, no history"]
    checks = {
        "enough_data": bool(main_entry["sites"] >= rule["min_sites"] and main_entry["n"] >= rule["min_pairs"]),
        "lower_bound_above_threshold": bool(lower > rule["threshold"]),
        "beats_bm25f": bool(min(d_bm["ci_site"][0], d_bm["ci_query"][0]) > 0),
        "beats_current_ranker_content": bool(min(d_rk["ci_site"][0], d_rk["ci_query"][0]) > 0),
    }
    res["pass_rule"] = rule
    res["lower_bound_used"] = lower
    res["checks"] = checks
    res["passed"] = bool(all(checks.values()))
    res["stretch_passed"] = bool(all(checks.values()) and lower > rule["stretch"])

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    pair_out = pairs.assign(**{f"hit_{name.replace(' ', '_').replace(',', '')}": values for name, values in hits.items()})
    pair_out.drop(columns=["hi", "lo"]).to_csv(out_dir / "pairs.csv", index=False)

    lines = [
        f"# Round 3 held-out result: {prereg.name} on {args.label} (scripts/claude/r3_score.py)",
        "",
        "REAL list order from Serper. Scorer outputs PREDICTED. Ties count half. Scored once.",
        "",
        f"Freeze commit {frozen_at}. First held-out pull {first_pull}.",
        f"Counts: {json.dumps(res['counts'])}",
        "",
        "| Scorer | All pairs |",
        "| --- | --- |",
    ]
    for name, entry in res["scorers"].items():
        lines.append(f"| {name} | {R.fmt(entry['all'])} |")
    lines += ["", "| Difference | Value |", "| --- | --- |"]
    for name, entry in res["differences"].items():
        lines.append(f"| {name} | {R.fmt_diff(entry)} |")
    lines += ["", "| Slice | " + " | ".join(res["scorers"]) + " |", "| --- | " + " | ".join("---" for _ in res["scorers"]) + " |"]
    for label in masks:
        cells = []
        for name in res["scorers"]:
            e = res["scorers"][name]["strata"][label]
            cells.append(f"{e['rate']:.3f} [{e['ci_site'][0]:.3f}, {e['ci_site'][1]:.3f}] ({e['n']})" if e.get("n") else "n/a")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if len(swaps):
        cells = [f"{res['scorers'][name]['swap_pairs']['rate']:.3f} ({res['scorers'][name]['swap_pairs']['n']})" for name in res["scorers"]]
        lines.append("| swap pairs | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"Pass rule: lower bound of both the site and the search bootstrap above {rule['threshold']} (stretch {rule['stretch']}), "
        f"at least {rule['min_sites']} sites and {rule['min_pairs']} pairs, and both paired differences above zero on both bootstraps.",
        f"Lower bound used: {lower:.3f}. Checks: {json.dumps(checks)}.",
        f"**{'PASS' if res['passed'] else 'FAIL'}**" + (" (stretch also met)" if res["stretch_passed"] else ""),
    ]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
