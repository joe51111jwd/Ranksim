"""Round 3: score the frozen content scorer on held-out site-restricted lists. Once. PREDICTED.

  python scripts/claude/r3_score.py --prereg evals/preregistered/claude-r3-samesite

Everything the run needs is named in the preregistration's test.json: the frozen
plan, the two pull labels, the frozen models, the fetch rule and the pass rule.

Guards (the run refuses otherwise):
  - the manifest verifies, is committed and is pushed (r2_score.require_frozen_dir);
  - no result exists yet, on disk or on the pushed branch;
  - both pulls are exactly the frozen plan, every request once, inside the frozen test cell;
  - no request of the plan exists under any other round 3 label, in git or in the raw store;
  - every chunk file of both pulls was committed once, never changed, and its commit
    descends from the freeze commit;
  - for every list the second pull is at least the stated minutes after the first
    and at most the stated hours;
  - pages come from one plain-HTTP fetch pass that started after the first pull
    ended; no page version stored after that pass ended is read.

The held-out lists were never used to fit or choose the scorer. (Their sites'
ordinary result lists of design days were part of the round 2 study that
suggested the feature families, and the universe was drawn from them; the
report has a slice without the pairs those lists already showed.)

Truth. The pair universe is every pair of readable own-site pages in the first
pull. The second pull then says, for each pair, with a page missing from its top
10 counted as below every page that is present:
  consensus      it orders the pair the same way (both present, or only the lower page missing)
  disagreement   it orders the pair the other way (both present and flipped, or only the higher page missing)
  both missing   neither page is in the second pull (excluded from the primary set)
The primary number is pair accuracy on consensus pairs (ties half). Next to it,
always: the single-pull number on all pairs, half credit on all pairs (a
disagreement counts one half), the both-present consensus pairs, and every status
with its count.

Intervals: 95% bootstraps (2000 draws, seed 20261119) over sites, over search
groups, and two-way over sites and search groups together. The pass rule uses the
two-way interval. It is applied mechanically.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import r3_data, r3_dataset, r3_design, r3_model as R, r3_pages, r3_plans, r3_ranker_baseline  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.r2_score import require_frozen_dir  # noqa: E402

OUT_ROOT = REPO / "evals" / "claude" / "r3" / "heldout"
FETCH_DIR = REPO / "evals" / "claude" / "r3" / "fetch"
STATUSES = ("consensus_both", "consensus_lower_missing", "disagree_flipped", "disagree_higher_missing", "both_missing")


def _git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check).stdout.strip()


def freeze_commit(directory: Path) -> str:
    """The last commit that touched the preregistration manifest."""
    commit = _git("log", "-1", "--format=%H", "--", str((directory / "manifest.json").relative_to(REPO)))
    if not commit:
        raise SystemExit("The preregistration manifest has no commit.")
    return commit


def check_no_result(out_dir: Path) -> None:
    if (out_dir / "results.json").exists():
        raise SystemExit(f"{out_dir}/results.json exists. A held-out result is not overwritten.")
    _git("fetch", "-q", "origin", check=False)
    upstream = _git("rev-parse", "--abbrev-ref", "@{upstream}", check=False)
    if upstream:
        listed = _git("ls-tree", "-r", "--name-only", upstream, "--", str(out_dir.relative_to(REPO)), check=False)
        if "results.json" in listed:
            raise SystemExit("A result for this test is already on the pushed branch. A held-out result is not overwritten.")


def check_chunks(labels: list[str], frozen: str) -> dict:
    """Each chunk file was committed exactly once, is unchanged, and descends from the freeze commit."""
    count = 0
    for label in labels:
        paths = r3_data.chunk_paths(label)
        if not paths:
            raise SystemExit(f"No chunk files for label {label}.")
        for path in paths:
            relative = str(path.relative_to(REPO))
            commits = [line for line in _git("log", "--format=%H", "--", relative).splitlines() if line]
            if len(commits) != 1:
                raise SystemExit(f"{relative} has {len(commits)} commits. A held-out chunk is committed once and never changed.")
            if _git("status", "--porcelain", "--", relative):
                raise SystemExit(f"{relative} differs from its committed version.")
            ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", frozen, commits[0]], cwd=REPO)
            if ancestor.returncode != 0:
                raise SystemExit(f"{relative} was committed before the freeze commit.")
            count += 1
    return {"chunk_files": count}


def check_no_other_pull(plan_qs: set[str], labels: list[str]) -> dict:
    """No request of the plan under any other round 3 label, in git or in the raw store; each request once per label."""
    for folder in sorted(r3_data.SERP_DIR.iterdir()):
        if not folder.is_dir() or folder.name in labels:
            continue
        for path in sorted(folder.glob("chunk-*.json.gz")):
            payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            hit = [item["q"] for item in payload["items"] if item["q"] in plan_qs]
            if hit:
                raise SystemExit(f"A held-out request also appears under label {folder.name}: {len(hit)} requests.")
    sources = [f"claude-r3:{label}" for label in labels]
    with sqlite3.connect(f"file:{rs.DB}?mode=ro", uri=True, timeout=60) as con:
        rows = con.execute("SELECT query, source, count(*) FROM serp_raw WHERE source LIKE 'claude-r3:%' GROUP BY 1, 2").fetchall()
    per = {}
    for query, source, n in rows:
        if query in plan_qs:
            if source not in sources:
                raise SystemExit(f"A held-out request is in the raw store under source {source}.")
            per[(query, source)] = n
    extra = [key for key, n in per.items() if n != 1]
    if extra:
        raise SystemExit(f"{len(extra)} held-out requests were stored more than once under one label.")
    return {"raw_store_rows_for_plan": int(sum(per.values()))}


def check_pulls(plan: list[dict], first: pd.DataFrame, second: pd.DataFrame, frozen_at: pd.Timestamp, min_minutes: float, max_hours: float) -> dict:
    """Both pulls are exactly the plan (each request once), after the freeze, and the right time apart for every list."""
    wanted = sorted(item["q"] for item in plan)
    for name, requests in (("first", first), ("second", second)):
        if sorted(requests["q"]) != wanted:
            raise SystemExit(f"The {name} pull is not exactly the frozen plan, each request once.")
    t1 = pd.to_datetime(first.set_index("q")["fetched_at"], utc=True, format="ISO8601")
    t2 = pd.to_datetime(second.set_index("q")["fetched_at"], utc=True, format="ISO8601")
    if t1.min() <= frozen_at or t2.min() <= frozen_at:
        raise SystemExit(f"A held-out list was pulled at or before the freeze commit ({frozen_at}).")
    gap = (t2 - t1.reindex(t2.index)).dt.total_seconds() / 60.0
    if gap.min() < min_minutes:
        raise SystemExit(f"Two pulls of one list are only {gap.min():.1f} minutes apart. The rule says at least {min_minutes}.")
    if gap.max() > max_hours * 60.0:
        raise SystemExit(f"Two pulls of one list are {gap.max() / 60.0:.1f} hours apart. The rule says at most {max_hours}.")
    return {
        "first_pull_start": str(t1.min()),
        "first_pull_end": str(t1.max()),
        "second_pull_start": str(t2.min()),
        "min_gap_minutes": float(gap.min()),
        "max_gap_minutes": float(gap.max()),
        "lists_with_no_results_first_pull": int((first["n_results"] == 0).sum()),
        "lists_with_no_results_second_pull": int((second["n_results"] == 0).sum()),
    }


def fetch_cutoff(label_a: str, first_pull_end: str) -> dict:
    """The one plain-HTTP fetch pass of the first pull's pages: it must start after that pull ended."""
    summaries = sorted(FETCH_DIR.glob(f"summary-{label_a}-*.json"))
    if len(summaries) != 1:
        raise SystemExit(f"Expected exactly one fetch pass for {label_a}, found {len(summaries)}.")
    relative = str(summaries[0].relative_to(REPO))
    if len([line for line in _git("log", "--format=%H", "--", relative).splitlines() if line]) != 1 or _git("status", "--porcelain", "--", relative):
        raise SystemExit("The fetch summary must be committed once and unchanged.")
    summary = json.loads(summaries[0].read_text(encoding="utf-8"))
    if summary.get("labels") != [label_a]:
        raise SystemExit("The fetch pass must cover the first held-out pull only.")
    started = pd.Timestamp(summary["started_at_utc"])
    if started <= pd.Timestamp(first_pull_end):
        raise SystemExit("The fetch pass started before the first pull ended.")
    return {"file": relative, "started_at_utc": summary["started_at_utc"], "finished_at_utc": summary["finished_at_utc"], "fetched_ok": summary.get("fetched_ok"), "failed": summary.get("failed"), "already_fresh": summary.get("already_fresh")}


def pair_status(pairs: pd.DataFrame, table: pd.DataFrame, second: pd.DataFrame) -> np.ndarray:
    """Status of each first-pull pair under the second pull (see the module docstring)."""
    pos2 = {(q, key): int(position) for q, key, position in zip(second["q"], second["key"], second["position"], strict=True)}
    status = []
    for row in pairs.itertuples(index=False):
        q = table.at[row.hi, "q"]
        a, b = pos2.get((q, table.at[row.hi, "key"])), pos2.get((q, table.at[row.lo, "key"]))
        if a is None and b is None:
            status.append("both_missing")
        elif b is None:
            status.append("consensus_lower_missing")
        elif a is None:
            status.append("disagree_higher_missing")
        elif a < b:
            status.append("consensus_both")
        else:
            status.append("disagree_flipped")
    return np.asarray(status)


def block(pairs: pd.DataFrame, hits: dict[str, np.ndarray], mask: np.ndarray, main: str, baselines: list[str]) -> dict:
    part = pairs[mask]
    out = {"scorers": {name: R.accuracy(part, values[mask]) for name, values in hits.items()}, "differences": {}}
    if len(part):
        for base in baselines:
            out["differences"][f"{main} minus {base}"] = R.difference(part, hits[main][mask], hits[base][mask])
    return out


def extra_slices(pairs: pd.DataFrame, table: pd.DataFrame, slices: dict) -> dict[str, np.ndarray]:
    """Slices pre-stated in the preregistration (boolean masks over the pairs)."""
    by_query = slices["searches"]
    topic = pairs["query"].map(lambda q: by_query[q]["topic"]).to_numpy()
    bare = pairs["query"].map(lambda q: bool(by_query[q]["bare_brand"])).to_numpy()
    brand_seen = pairs["query"].map(lambda q: bool(by_query[q]["brand_in_training"])).to_numpy()
    sister = pairs["site"].isin(set(slices["sister_sites_of_training_sites"])).to_numpy()
    design = rs.load_rows(rs.DESIGN_MAX_DATE)
    seen = set(zip(design["query"], design["key"], strict=True))
    key = table["key"]
    both_seen = np.array([
        (query, key.at[hi]) in seen and (query, key.at[lo]) in seen
        for query, hi, lo in zip(pairs["query"], pairs["hi"], pairs["lo"], strict=True)
    ])
    masks = {f"topic area: {name}": topic == name for name in sorted(set(topic))}
    masks.update({
        "one-word brand searches": bare,
        "without one-word brand searches": ~bare,
        "searches whose brand is also in a training search": brand_seen,
        "without sister domains of training sites": ~sister,
        "without pairs both seen in a design-day ordinary list of the search": ~both_seen,
        "pairs both seen in a design-day ordinary list of the search": both_seen,
    })
    return masks


def score_lists(spec: dict, plan: list[dict], label_a: str, label_b: str, cutoff: str | None, header: dict, title: str) -> tuple[dict, pd.DataFrame, list[str]]:
    """Everything after the guards: features, scores, truth, tables. Returns (results, pair table, report lines)."""
    guards, timing, fetch = header["guards"], header["timing"], header["fetch"]
    frozen, frozen_at = guards["freeze_commit"], guards["freeze_commit_time"]
    main_name = spec["primary"]["name"]
    models = {main_name: REPO / spec["primary"]["model_dir"]}
    models.update({item["name"]: REPO / item["model_dir"] for item in spec.get("secondary", [])})
    df_table = json.loads((models[main_name] / "df_table.json").read_text(encoding="utf-8"))
    table, _ = r3_dataset.build_table([label_a], df_table=df_table, max_fetched_at=cutoff)
    table = table.reset_index(drop=True)
    pairs = R.list_pairs_index(table)
    status = pair_status(pairs, table, r3_data.load_label(label_b))

    scores: dict[str, pd.Series] = {}
    for name, directory in models.items():
        scorer = R.FrozenScorer.load(directory)
        missing = [feature for feature in scorer.spec["features"] if feature not in table.columns]
        if missing:
            raise SystemExit(f"Features missing at scoring time for {name}: {missing}")
        scores[name] = scorer.score(table)
    usable = table[table["usable"]]
    scores["bm25f"] = usable["bm25f"].astype(float)
    scores["cross-encoder on title text, unfitted"] = usable["ce_title"].astype(float)
    scores["vote, unfitted"] = R.vote_scores(table)
    ranker_names = []
    for variant in r3_ranker_baseline.VARIANTS:
        name = f"current ranker, no history ({variant})"
        scores[name] = r3_ranker_baseline.scores(table, variant=variant, max_fetched_at=cutoff)
        ranker_names.append(name)
    hits = {name: R.pair_hits(pairs, values) for name, values in scores.items()}
    unscored = {name: R.unscored_pairs(pairs, values) for name, values in scores.items()}
    baselines = ["bm25f", *ranker_names, "cross-encoder on title text, unfitted", "vote, unfitted"]

    consensus = np.isin(status, ["consensus_both", "consensus_lower_missing"])
    disagree = np.isin(status, ["disagree_flipped", "disagree_higher_missing"])
    everything = np.ones(len(pairs), dtype=bool)
    half = {name: np.where(disagree, 0.5, values) for name, values in hits.items()}
    by_rank = table.groupby("rank")["usable"].agg(["sum", "size"])
    res: dict = {
        "label": "PREDICTED",
        "prereg": title,
        "guards": guards,
        "timing": timing,
        "fetch": fetch,
        "counts": {
            "lists_in_plan": len(plan),
            "lists_with_results_first_pull": int(table["list_id"].nunique()),
            "listed_pages_first_pull": int(len(table)),
            "usable_pages_first_pull": int(len(usable)),
            "usable_share_by_list_position": {str(int(rank)): [int(row["sum"]), int(row["size"])] for rank, row in by_rank.iterrows()},
            "lists_with_pairs": int(pairs["list_id"].nunique()),
            "pairs_first_pull": int(len(pairs)),
            "status": {name: int((status == name).sum()) for name in STATUSES},
            "consensus_pairs": int(consensus.sum()),
            "pull_agreement_both_present": float((status == "consensus_both").sum() / max(1, np.isin(status, ["consensus_both", "disagree_flipped"]).sum())),
            "pull_agreement_all_but_both_missing": float(consensus.sum() / max(1, (consensus | disagree).sum())),
            "sites_with_consensus_pairs": int(pairs[consensus]["site"].nunique()),
            "search_groups_with_consensus_pairs": int(pairs[consensus]["group"].nunique()),
            "searches_with_consensus_pairs": int(pairs[consensus]["query"].nunique()),
            "top5_sites_by_consensus_pairs": pairs[consensus]["site"].value_counts().head(5).to_dict(),
            "unscored_pairs_counted_as_ties": unscored,
        },
        "primary_scorer": main_name,
        "consensus_pairs": block(pairs, hits, consensus, main_name, baselines),
        "all_pairs_first_pull": block(pairs, hits, everything, main_name, baselines),
        "half_credit_all_pairs": block(pairs, half, everything, main_name, baselines),
        "consensus_both_present": block(pairs, hits, status == "consensus_both", main_name, baselines),
        "by_status": {name: block(pairs, hits, status == name, main_name, [])["scorers"] for name in STATUSES},
    }
    masks = r3_design.strata(pairs, table)
    slices = json.loads((REPO / spec["slices"]).read_text(encoding="utf-8"))
    masks.update(extra_slices(pairs, table, slices))
    res["slices_consensus"] = {label: block(pairs, hits, mask & consensus, main_name, [])["scorers"] for label, mask in masks.items()}
    res["slices_all_pairs"] = {label: block(pairs, hits, mask, main_name, [])["scorers"] for label, mask in masks.items()}
    swaps = r3_design.swap_pairs(pairs[consensus], table)
    res["swap_pairs_consensus"] = {"rows": int(len(swaps)), "sites": int(swaps["site"].nunique()) if len(swaps) else 0, "note": "descriptive only: few sites have two lists"}
    if len(swaps):
        res["swap_pairs_consensus"]["scorers"] = {name: R.accuracy(swaps, R.pair_hits(swaps, values)) for name, values in scores.items()}

    rule = spec["pass_rule"]
    entry = res["consensus_pairs"]["scorers"][main_name]
    diffs = res["consensus_pairs"]["differences"]
    lower = entry["ci_twoway"][0] if entry.get("n") else float("nan")
    checks = {
        "enough_data": bool(entry.get("n", 0) >= rule["min_pairs"] and entry.get("sites", 0) >= rule["min_sites"]),
        "lower_bound_above_threshold": bool(lower > rule["threshold"]),
        "beats_bm25f": bool(diffs[f"{main_name} minus bm25f"]["ci_twoway"][0] > 0),
        "beats_current_ranker_content": bool(all(diffs[f"{main_name} minus {name}"]["ci_twoway"][0] > 0 for name in ranker_names)),
    }
    res["pass_rule"] = rule
    res["lower_bound_used"] = lower
    res["checks"] = checks
    res["passed"] = bool(all(checks.values()))
    res["stretch_passed"] = bool(res["passed"] and lower > rule["stretch"])
    single = res["all_pairs_first_pull"]["scorers"][main_name]
    res["other_readings"] = {
        "consensus_pairs_one_way_lower_bounds": {"site": entry["ci_site"][0], "search_group": entry["ci_query"][0]},
        "consensus_pairs_one_way_both_above_threshold": bool(min(entry["ci_site"][0], entry["ci_query"][0]) > rule["threshold"]),
        "single_pull_two_way_lower_bound": single["ci_twoway"][0],
        "single_pull_two_way_above_threshold": bool(single["ci_twoway"][0] > rule["threshold"]),
    }

    pair_out = pairs.drop(columns=["hi", "lo"]).assign(
        status=status,
        page_id_hi=table.loc[pairs["hi"], "page_id"].to_numpy(),
        page_id_lo=table.loc[pairs["lo"], "page_id"].to_numpy(),
        **{f"hit|{name}": values for name, values in hits.items()},
    )
    names = list(scores)
    lines = [
        f"# Round 3 held-out result: {title} (scripts/claude/r3_score.py)",
        "",
        "REAL site-restricted list order from Serper (two pulls per list). Scorer outputs PREDICTED. Ties count half. Scored once.",
        "",
        f"Freeze commit {frozen[:10]} at {frozen_at}. First pull {timing['first_pull_start']} to {timing['first_pull_end']}. Second pull from {timing['second_pull_start']}, {timing['min_gap_minutes']:.0f} to {timing['max_gap_minutes']:.0f} minutes after the first. Page fetch pass {fetch['started_at_utc']} to {fetch['finished_at_utc']}.",
        "",
        f"Counts: {json.dumps(res['counts'])}",
    ]
    for title, key in (
        ("Consensus pairs (primary)", "consensus_pairs"),
        ("All pairs of the first pull (single-pull reading)", "all_pairs_first_pull"),
        ("Half credit on all pairs (a disagreement between the pulls counts one half)", "half_credit_all_pairs"),
        ("Consensus pairs with both pages in the second pull", "consensus_both_present"),
    ):
        lines += ["", f"## {title}", "", "| Scorer | Pair accuracy | Macro by site |", "| --- | --- | --- |"]
        for name, value in res[key]["scorers"].items():
            lines.append(f"| {name} | {R.fmt(value)} | {value.get('macro_site', float('nan')):.3f} |")
        if res[key]["differences"]:
            lines += ["", "| Difference | Value |", "| --- | --- |"]
            for name, value in res[key]["differences"].items():
                lines.append(f"| {name} | {R.fmt_diff(value)} |")
    lines += ["", "## By status of the pair in the second pull (rate and pairs)", "", "| Status | " + " | ".join(names) + " |", "| --- | " + " | ".join("---" for _ in names) + " |"]
    for name in STATUSES:
        cells = [f"{e['rate']:.3f} ({e['n']})" if e.get("n") else "n/a" for e in (res["by_status"][name][scorer] for scorer in names)]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Slices of the consensus pairs (rate, two-way interval, pairs)", "", "| Slice | " + " | ".join(names) + " |", "| --- | " + " | ".join("---" for _ in names) + " |"]
    for label, by_scorer in res["slices_consensus"].items():
        cells = [f"{e['rate']:.3f} [{e['ci_twoway'][0]:.3f}, {e['ci_twoway'][1]:.3f}] ({e['n']})" if e.get("n") else "n/a" for e in (by_scorer[name] for name in names)]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if len(swaps):
        cells = [f"{e['rate']:.3f} ({e['n']})" for e in (res["swap_pairs_consensus"]["scorers"][name] for name in names)]
        lines.append(f"| swap pairs, descriptive ({res['swap_pairs_consensus']['sites']} sites) | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Pass rule",
        "",
        f"On consensus pairs, primary scorer: two-way (site and search group) lower bound above {rule['threshold']} (stretch {rule['stretch']}), at least {rule['min_sites']} sites and {rule['min_pairs']} pairs, and the paired difference against BM25F and against both current-ranker variants above zero on the two-way interval.",
        f"Lower bound used: {lower:.3f}. Checks: {json.dumps(checks)}.",
        f"Other readings, not the pass rule: {json.dumps(res['other_readings'])}.",
        "",
        f"**{'PASS' if res['passed'] else 'FAIL'}**" + (" (stretch also met)" if res["stretch_passed"] else ""),
    ]
    return res, pair_out, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prereg", required=True)
    args = parser.parse_args(argv)

    prereg = (REPO / args.prereg).resolve()
    require_frozen_dir(prereg)
    spec = json.loads((prereg / "test.json").read_text(encoding="utf-8"))
    label_a, label_b = spec["labels"]
    out_dir = OUT_ROOT / prereg.name
    check_no_result(out_dir)

    universe = r3_plans.load_universe()
    group_of = {query: index for index, group in enumerate(universe["groups"]) for query in group}
    plan = [json.loads(line) for line in (REPO / spec["plan"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    for item in plan:
        if universe["site_fold"].get(item["site"]) != spec["site_fold"] or universe["group_fold"].get(str(group_of.get(item["query"]))) != spec["query_fold"]:
            raise SystemExit(f"Plan row outside the frozen test cell: {item['q']}")
    frozen = freeze_commit(prereg)
    frozen_at = pd.Timestamp(_git("log", "-1", "--format=%cI", frozen)).tz_convert("UTC")
    guards = {"freeze_commit": frozen, "freeze_commit_time": str(frozen_at)}
    guards.update(check_chunks([label_a, label_b], frozen))
    guards.update(check_no_other_pull({item["q"] for item in plan}, [label_a, label_b]))
    timing = check_pulls(plan, r3_data.load_requests(label_a), r3_data.load_requests(label_b), frozen_at, float(spec["min_minutes_between_pulls"]), float(spec["max_hours_between_pulls"]))
    fetch = fetch_cutoff(label_a, timing["first_pull_end"])
    cutoff = fetch["finished_at_utc"]

    header = {"guards": guards, "timing": timing, "fetch": fetch}
    res, pair_out, lines = score_lists(spec, plan, label_a, label_b, cutoff, header, prereg.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    pair_out.to_csv(out_dir / "pairs.csv", index=False)
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
