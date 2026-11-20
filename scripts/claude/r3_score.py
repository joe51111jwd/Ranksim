"""Round 3: score the frozen content scorer on held-out site-restricted lists. Once. PREDICTED.

  python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite

Everything the run needs is named in the preregistration's test.json: the frozen
plan, the two pull labels, the frozen models and the pass rule. The run refuses
unless the manifest verifies, is committed and is pushed
(scripts/claude/r2_score.require_frozen_dir), unless both pulls are exactly the
frozen plan, were made after the freeze commit, and sit at least the stated
number of minutes apart for every list. It never overwrites a result.

Truth. Each held-out list is pulled twice. A pair of readable pages from the
first pull is a
  consensus pair   when both pages are in the second pull in the same order,
  flipped pair     when both are in the second pull in the opposite order,
  unconfirmed pair when a page is missing from the second pull.
The primary number is pair accuracy on consensus pairs (ties half). The
single-pull number on all pairs of the first pull is reported next to it, and so
are the flipped and unconfirmed pairs.

Reported on the same pairs: the frozen scorer, BM25F alone, the current ranker
with no history (its content part), the paired differences, the secondary
scorers named in test.json, the pre-stated slices and swap pairs, and coverage.
Intervals are 95% bootstraps over lists, over sites and over searches (2000
draws, seed 20261119). The pass rule is applied mechanically.
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


def freeze_commit_time(directory: Path) -> pd.Timestamp:
    """Time of the last commit that touched the preregistration manifest."""
    out = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "--", str((directory / "manifest.json").relative_to(REPO))],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        raise SystemExit("The preregistration manifest has no commit.")
    return pd.Timestamp(out).tz_convert("UTC")


def check_pulls(plan: list[dict], first: pd.DataFrame, second: pd.DataFrame, frozen_at: pd.Timestamp, min_minutes: float) -> dict:
    """Both pulls are exactly the plan, after the freeze, and far enough apart for every list."""
    wanted = sorted(item["q"] for item in plan)
    for name, requests in (("first", first), ("second", second)):
        if sorted(requests["q"]) != wanted:
            raise SystemExit(f"The {name} pull is not exactly the frozen plan.")
    t1 = pd.to_datetime(first.set_index("q")["fetched_at"], utc=True, format="ISO8601")
    t2 = pd.to_datetime(second.set_index("q")["fetched_at"], utc=True, format="ISO8601")
    if t1.min() <= frozen_at or t2.min() <= frozen_at:
        raise SystemExit(f"A held-out list was pulled at or before the freeze commit ({frozen_at}).")
    gap = (t2 - t1.reindex(t2.index)).dt.total_seconds() / 60.0
    if gap.min() < min_minutes:
        raise SystemExit(f"Two pulls of one list are only {gap.min():.1f} minutes apart. The rule says at least {min_minutes}.")
    return {"first_pull_start": str(t1.min()), "second_pull_start": str(t2.min()), "min_gap_minutes": float(gap.min()), "max_gap_minutes": float(gap.max())}


def confirm_pairs(pairs: pd.DataFrame, table: pd.DataFrame, second: pd.DataFrame) -> np.ndarray:
    """'consensus', 'flipped' or 'unconfirmed' for each pair of the first pull."""
    pos2 = {(q, key): int(position) for q, key, position in zip(second["q"], second["key"], second["position"], strict=True)}
    status = []
    for row in pairs.itertuples(index=False):
        q = table.at[row.hi, "q"]
        a, b = pos2.get((q, table.at[row.hi, "key"])), pos2.get((q, table.at[row.lo, "key"]))
        if a is None or b is None:
            status.append("unconfirmed")
        elif a < b:
            status.append("consensus")
        else:
            status.append("flipped")
    return np.asarray(status)


def block(pairs: pd.DataFrame, hits: dict[str, np.ndarray], mask: np.ndarray, main: str, baselines: list[str]) -> dict:
    part = pairs[mask]
    out = {"scorers": {name: R.accuracy(part, values[mask]) for name, values in hits.items()}, "differences": {}}
    if len(part):
        for base in baselines:
            out["differences"][f"{main} minus {base}"] = R.difference(part, hits[main][mask], hits[base][mask])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prereg", required=True)
    args = parser.parse_args(argv)

    prereg = (REPO / args.prereg).resolve()
    require_frozen_dir(prereg)
    spec = json.loads((prereg / "test.json").read_text(encoding="utf-8"))
    label_a, label_b = spec["labels"]
    out_dir = OUT_ROOT / prereg.name
    if (out_dir / "results.json").exists():
        raise SystemExit(f"{out_dir}/results.json exists. A held-out result is not overwritten.")

    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    plan = [json.loads(line) for line in (REPO / spec["plan"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    for item in plan:
        if universe["site_fold"].get(item["site"]) != spec["site_fold"] or universe["group_fold"].get(str(group_of.get(item["query"]))) != spec["query_fold"]:
            raise SystemExit(f"Plan row outside the frozen test cell: {item['q']}")
    frozen_at = freeze_commit_time(prereg)
    timing = check_pulls(plan, r3_data.load_requests(label_a), r3_data.load_requests(label_b), frozen_at, float(spec["min_minutes_between_pulls"]))

    main_name = spec["primary"]["name"]
    models = {main_name: REPO / spec["primary"]["model_dir"]}
    models.update({item["name"]: REPO / item["model_dir"] for item in spec.get("secondary", [])})
    df_table = json.loads((models[main_name] / "df_table.json").read_text(encoding="utf-8"))
    table, _ = r3_dataset.build_table([label_a], df_table=df_table)
    table = table.reset_index(drop=True)
    pairs = R.list_pairs_index(table)
    status = confirm_pairs(pairs, table, r3_data.load_label(label_b))

    scores: dict[str, pd.Series] = {}
    for name, directory in models.items():
        scorer = R.FrozenScorer.load(directory)
        missing = [feature for feature in scorer.spec["features"] if feature not in table.columns]
        if missing:
            raise SystemExit(f"Features missing at scoring time for {name}: {missing}")
        scores[name] = scorer.score(table)
    scores["bm25f"] = table.loc[table["usable"], "bm25f"].astype(float)
    scores["current ranker, no history"] = r3_ranker_baseline.scores(table)
    hits = {name: R.pair_hits(pairs, values.reindex(table.index).fillna(0.0)) for name, values in scores.items()}
    baselines = ["bm25f", "current ranker, no history"]

    consensus = status == "consensus"
    usable = table[table["usable"]]
    res: dict = {
        "label": "PREDICTED",
        "prereg": prereg.name,
        "freeze_commit_time": str(frozen_at),
        "timing": timing,
        "counts": {
            "lists_in_plan": len(plan),
            "lists_with_results_first_pull": int(table["list_id"].nunique()),
            "listed_pages_first_pull": int(len(table)),
            "usable_pages_first_pull": int(len(usable)),
            "lists_with_pairs": int(pairs["list_id"].nunique()),
            "pairs_first_pull": int(len(pairs)),
            "consensus_pairs": int(consensus.sum()),
            "flipped_pairs": int((status == "flipped").sum()),
            "unconfirmed_pairs": int((status == "unconfirmed").sum()),
            "pull_agreement_on_pairs_in_both": float(consensus.sum() / max(1, (status != "unconfirmed").sum())),
            "sites_with_consensus_pairs": int(pairs[consensus]["site"].nunique()),
            "searches_with_consensus_pairs": int(pairs[consensus]["query"].nunique()),
            "top5_sites_by_consensus_pairs": pairs[consensus]["site"].value_counts().head(5).to_dict(),
        },
        "primary_scorer": main_name,
        "consensus_pairs": block(pairs, hits, consensus, main_name, baselines),
        "all_pairs_first_pull": block(pairs, hits, np.ones(len(pairs), dtype=bool), main_name, baselines),
        "flipped_pairs": block(pairs, hits, status == "flipped", main_name, []),
        "unconfirmed_pairs": block(pairs, hits, status == "unconfirmed", main_name, []),
    }
    masks = r3_design.strata(pairs, table)
    res["slices_consensus"] = {label: block(pairs, hits, mask & consensus, main_name, [])["scorers"] for label, mask in masks.items()}
    res["slices_all_pairs"] = {label: block(pairs, hits, mask, main_name, [])["scorers"] for label, mask in masks.items()}
    swaps = r3_design.swap_pairs(pairs[consensus], table)
    res["swap_pairs_consensus"] = {"rows": int(len(swaps)), "sites": int(swaps["site"].nunique()) if len(swaps) else 0}
    if len(swaps):
        res["swap_pairs_consensus"]["scorers"] = {name: R.accuracy(swaps, R.pair_hits(swaps, values.reindex(table.index).fillna(0.0))) for name, values in scores.items()}

    rule = spec["pass_rule"]
    entry = res["consensus_pairs"]["scorers"][main_name]
    diffs = res["consensus_pairs"]["differences"]
    lower = min(entry["ci_site"][0], entry["ci_query"][0]) if entry.get("n") else float("nan")
    checks = {
        "enough_data": bool(entry.get("n", 0) >= rule["min_pairs"] and entry.get("sites", 0) >= rule["min_sites"]),
        "lower_bound_above_threshold": bool(lower > rule["threshold"]),
        "beats_bm25f": bool(min(diffs[f"{main_name} minus bm25f"]["ci_site"][0], diffs[f"{main_name} minus bm25f"]["ci_query"][0]) > 0),
        "beats_current_ranker_content": bool(
            min(diffs[f"{main_name} minus current ranker, no history"]["ci_site"][0], diffs[f"{main_name} minus current ranker, no history"]["ci_query"][0]) > 0
        ),
    }
    res["pass_rule"] = rule
    res["lower_bound_used"] = lower
    res["checks"] = checks
    res["passed"] = bool(all(checks.values()))
    res["stretch_passed"] = bool(res["passed"] and lower > rule["stretch"])
    single = res["all_pairs_first_pull"]["scorers"][main_name]
    res["single_pull_lower_bound"] = min(single["ci_site"][0], single["ci_query"][0])
    res["single_pull_reading_would_pass"] = bool(res["single_pull_lower_bound"] > rule["threshold"])

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    pair_out = pairs.drop(columns=["hi", "lo"]).assign(status=status, **{f"hit|{name}": values for name, values in hits.items()})
    pair_out.to_csv(out_dir / "pairs.csv", index=False)

    lines = [
        f"# Round 3 held-out result: {prereg.name} (scripts/claude/r3_score.py)",
        "",
        "REAL list order from Serper (two pulls per list). Scorer outputs PREDICTED. Ties count half. Scored once.",
        "",
        f"Freeze commit {frozen_at}. First pull from {timing['first_pull_start']}, second from {timing['second_pull_start']}, {timing['min_gap_minutes']:.0f} to {timing['max_gap_minutes']:.0f} minutes apart.",
        f"Counts: {json.dumps(res['counts'])}",
    ]
    for title, key in (("Consensus pairs (primary)", "consensus_pairs"), ("All pairs of the first pull (single-pull reading)", "all_pairs_first_pull"), ("Flipped pairs", "flipped_pairs"), ("Unconfirmed pairs", "unconfirmed_pairs")):
        lines += ["", f"## {title}", "", "| Scorer | Pair accuracy |", "| --- | --- |"]
        for name, value in res[key]["scorers"].items():
            lines.append(f"| {name} | {R.fmt(value)} |")
        for name, value in res[key]["differences"].items():
            lines.append(f"| {name} | {R.fmt_diff(value)} |")
    names = list(scores)
    lines += ["", "## Slices of the consensus pairs (rate, site interval, pairs)", "", "| Slice | " + " | ".join(names) + " |", "| --- | " + " | ".join("---" for _ in names) + " |"]
    for label, by_scorer in res["slices_consensus"].items():
        cells = [f"{e['rate']:.3f} [{e['ci_site'][0]:.3f}, {e['ci_site'][1]:.3f}] ({e['n']})" if e.get("n") else "n/a" for e in (by_scorer[name] for name in names)]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if len(swaps):
        cells = [f"{e['rate']:.3f} [{e['ci_site'][0]:.3f}, {e['ci_site'][1]:.3f}] ({e['n']})" for e in (res["swap_pairs_consensus"]["scorers"][name] for name in names)]
        lines.append(f"| swap pairs ({res['swap_pairs_consensus']['sites']} sites) | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Pass rule",
        "",
        f"On consensus pairs: lower bound of both the site and the search bootstrap above {rule['threshold']} (stretch {rule['stretch']}), at least {rule['min_sites']} sites and {rule['min_pairs']} pairs, and both paired differences above zero on both bootstraps.",
        f"Lower bound used: {lower:.3f}. Checks: {json.dumps(checks)}.",
        f"Single-pull reading (all pairs of the first pull): lower bound {res['single_pull_lower_bound']:.3f}, would pass: {res['single_pull_reading_would_pass']}.",
        "",
        f"**{'PASS' if res['passed'] else 'FAIL'}**" + (" (stretch also met)" if res["stretch_passed"] else ""),
    ]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
