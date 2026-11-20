"""Round 2 loop 1, Target 3: the frozen permutation noise check (evals/claude/theory.md, section 3).

  python scripts/claude/r2_t3_noise_check.py design
  python scripts/claude/r2_t3_noise_check.py score --day-a D1 --day-b D2 --max-date D2 --out DIR --prereg-dir evals/preregistered/claude-r2-<name>

For one transition (day A to day B) and each search, the voting pulls of the two days
(each day's consensus session, rsdata.consensus_session) are pooled and shuffled between
the days, keeping the number of pulls per day. Both consensus lists are recomputed with
the rsdata rule and flips are counted with the rsdata.flip_pairs rule (both URLs in both
top 10s, strictly ordered on both days). The consensus and pair code is imported from
scripts/claude/r2_t3_noise.py, which produced the design number in theory.md.

2,000 permutations, seed 20261119. In each permutation every search gets its own random
shuffle (searches in sorted order, one numpy Generator). Reported:

  observed flips and pairs (the real split; checked against rsdata.flip_pairs),
  expected flips and pairs under shuffling (mean over the permutations),
  the 2.5% and 97.5% points of the shuffled total flip count, and P(shuffled >= observed),
  noise share of the flip count = expected flips / observed flips, with a 95% query
  bootstrap interval (2000 draws, seed 20261119, every search with pulls on both days
  resampled, the same bootstrap as r2_t3_noise.qboot).

As a check, the exact expectation over every split of each search's pulls is also
reported (for 3 + 3 pulls that is all 20 splits). That is what the design study used.

PREDICTED in theory.md: the noise share of the flip count is between 0.75 and 1.3 on
each held-out consecutive transition. Below 0.7 would mean real overnight change is common.
`score` refuses to run unless --prereg-dir holds a manifest that verifies, lists this
file, and is committed and pushed. It is for the lead, once per held-out transition.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import r2_t3_latent as lt  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.r2_t3_noise import cons_of, qboot, scored_pairs  # noqa: E402

PERMUTATIONS = 2000
SEED = 20261119
EXACT_MAX_SPLITS = 5000  # exact enumeration per search when the number of splits is at most this
THEORY_BAND = (0.75, 1.3)  # PREDICTED noise share on each held-out consecutive transition
THEORY_FALSIFIER = 0.7  # a noise share under this means real overnight change is common
# The design number in theory.md (exact enumeration over all 20 splits per search).
THEORY_DESIGN = {"expected_flips": 45.0, "expected_pairs": 811.9, "observed_flips": 44, "observed_pairs": 767, "noise_share": 1.02, "lo": 0.78, "hi": 1.47}
THIS_FILE = str(Path(__file__).resolve().relative_to(ROOT))
DESIGN_OUT = lt.DESIGN_OUT


def voting_pulls(rows: pd.DataFrame, day_a: str, day_b: str, queries: Iterable[str]) -> dict[str, tuple[list[dict], list[dict]]]:
    """Each search's voting pulls (ranks dicts) on day A and day B. Searches missing either day are left out."""
    pulls = rs.pulls_by_query_day(rows)
    out = {}
    for query in sorted(set(queries)):
        if (query, day_a) in pulls and (query, day_b) in pulls:
            day_a_votes = [pull.ranks for pull in rs.consensus_session(pulls[(query, day_a)])]
            day_b_votes = [pull.ranks for pull in rs.consensus_session(pulls[(query, day_b)])]
            out[query] = (day_a_votes, day_b_votes)
    return out


def split_count(pool: list[dict], subset: tuple[int, ...]) -> tuple[int, int]:
    """Flips and scored pairs when the pulls in ``subset`` form pseudo day A and the rest pseudo day B."""
    chosen = set(subset)
    pairs = scored_pairs(cons_of([pool[i] for i in subset]), cons_of([pool[i] for i in range(len(pool)) if i not in chosen]))
    return sum(1 for pair in pairs if pair[6]), len(pairs)


def permutation_null(searches: dict[str, tuple[list[dict], list[dict]]], permutations: int = PERMUTATIONS, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """F[k, i] and P[k, i]: flips and pairs of search i (sorted order) under random shuffle k."""
    queries = sorted(searches)
    rng = np.random.default_rng(seed)
    flips = np.zeros((permutations, len(queries)))
    pairs = np.zeros((permutations, len(queries)))
    cache: dict[tuple[str, tuple[int, ...]], tuple[int, int]] = {}
    for k in range(permutations):
        for i, query in enumerate(queries):
            day_a_votes, day_b_votes = searches[query]
            pool = day_a_votes + day_b_votes
            order = rng.permutation(len(pool))
            subset = tuple(sorted(int(index) for index in order[: len(day_a_votes)]))
            key = (query, subset)
            if key not in cache:
                cache[key] = split_count(pool, subset)
            flips[k, i], pairs[k, i] = cache[key]
    return flips, pairs


def exact_null(day_a_votes: list[dict], day_b_votes: list[dict]) -> tuple[float, float] | None:
    """Mean flips and pairs over every split of the pooled pulls (None when there are too many splits)."""
    pool = day_a_votes + day_b_votes
    size = len(day_a_votes)
    if math.comb(len(pool), size) > EXACT_MAX_SPLITS:
        return None
    counts = [split_count(pool, subset) for subset in combinations(range(len(pool)), size)]
    return float(np.mean([c[0] for c in counts])), float(np.mean([c[1] for c in counts]))


def search_table(searches: dict[str, tuple[list[dict], list[dict]]], flips: np.ndarray, pairs: np.ndarray) -> pd.DataFrame:
    """One row per search: observed and expected (shuffled and exact) flips and pairs."""
    records = []
    for i, query in enumerate(sorted(searches)):
        day_a_votes, day_b_votes = searches[query]
        obs_f, obs_p = split_count(day_a_votes + day_b_votes, tuple(range(len(day_a_votes))))
        exact = exact_null(day_a_votes, day_b_votes)
        records.append(
            {
                "query": query,
                "pulls_a": len(day_a_votes),
                "pulls_b": len(day_b_votes),
                "obs_f": obs_f,
                "obs_p": obs_p,
                "null_f": float(flips[:, i].mean()),
                "null_p": float(pairs[:, i].mean()),
                "exact_f": None if exact is None else exact[0],
                "exact_p": None if exact is None else exact[1],
            }
        )
    return pd.DataFrame.from_records(records).set_index("query")


def check_observed(table: pd.DataFrame, cons: pd.DataFrame, day_a: str, day_b: str) -> None:
    """The real split must give exactly the rsdata.flip_pairs count for these searches."""
    ref = rs.flip_pairs(cons, day_a, day_b)
    ref = ref[ref["query"].isin(set(table.index))] if len(ref) else ref
    flips = int(ref["flipped"].sum()) if len(ref) else 0
    if int(table["obs_p"].sum()) != len(ref) or int(table["obs_f"].sum()) != flips:
        raise AssertionError(f"real split gives {table['obs_f'].sum()}/{table['obs_p'].sum()}, rsdata.flip_pairs gives {flips}/{len(ref)}")


def summarise_noise(table: pd.DataFrame, flips: np.ndarray, pairs: np.ndarray) -> dict:
    """Observed and shuffled counts and the noise share of the flip count, with a query-bootstrap interval."""
    obs_f, obs_p = float(table["obs_f"].sum()), float(table["obs_p"].sum())
    totals = flips.sum(axis=1)
    out = {
        "searches": int(len(table)),
        "searches_with_pairs": int((table["obs_p"] > 0).sum()),
        "observed": {"flips": int(obs_f), "pairs": int(obs_p), "rate": obs_f / obs_p if obs_p else None},
        "shuffled": {
            "permutations": int(flips.shape[0]),
            "seed": SEED,
            "expected_flips": float(table["null_f"].sum()),
            "expected_pairs": float(table["null_p"].sum()),
            "rate": float(table["null_f"].sum() / table["null_p"].sum()) if table["null_p"].sum() else None,
            "total_flips_2.5_97.5": [float(np.percentile(totals, 2.5)), float(np.percentile(totals, 97.5))],
            "p_shuffled_flips_ge_observed": float((totals >= obs_f).mean()),
        },
        "noise_share_of_flip_count": qboot(table[["null_f", "obs_f"]], lambda d: d["null_f"] / d["obs_f"]),
    }
    share = out["noise_share_of_flip_count"]["value"]
    out["theory_band"] = list(THEORY_BAND)
    out["point_in_theory_band"] = bool(share is not None and THEORY_BAND[0] <= share <= THEORY_BAND[1])
    out["point_below_falsifier_0.7"] = bool(share is not None and share < THEORY_FALSIFIER)
    if table["exact_f"].notna().all():
        exact = table[["exact_f", "obs_f"]].astype(float)
        out["exact_check"] = {
            "expected_flips": float(table["exact_f"].sum()),
            "expected_pairs": float(table["exact_p"].sum()),
            "noise_share_of_flip_count": qboot(exact, lambda d: d["exact_f"] / d["obs_f"]),
        }
    return out


def _fmt_share(block: dict) -> str:
    if block.get("value") is None:
        return "n/a"
    lo = "n/a" if block["lo"] is None else f"{block['lo']:.2f}"
    hi = "n/a" if block["hi"] is None else f"{block['hi']:.2f}"
    return f"{block['value']:.2f} [{lo}, {hi}]"


def _lines(summary: dict) -> list[str]:
    obs, sh = summary["observed"], summary["shuffled"]
    lines = [
        f"- Observed: {obs['flips']} flips on {obs['pairs']} pairs ({summary['searches']} searches with pulls on both days, {summary['searches_with_pairs']} with pairs).",
        f"- Shuffled ({sh['permutations']} permutations, seed {sh['seed']}): {sh['expected_flips']:.1f} expected flips on {sh['expected_pairs']:.1f} pairs. "
        f"Total shuffled flips 2.5% to 97.5%: {sh['total_flips_2.5_97.5'][0]:.0f} to {sh['total_flips_2.5_97.5'][1]:.0f}. P(shuffled >= observed) = {sh['p_shuffled_flips_ge_observed']:.3f}.",
        f"- Noise share of the flip count (expected / observed): {_fmt_share(summary['noise_share_of_flip_count'])}. "
        f"Theory band {THEORY_BAND[0]} to {THEORY_BAND[1]}: point inside = {summary['point_in_theory_band']}. Under {THEORY_FALSIFIER}: {summary['point_below_falsifier_0.7']}.",
    ]
    if "exact_check" in summary:
        ex = summary["exact_check"]
        lines.append(
            f"- Exact check (every split of each search's pulls): {ex['expected_flips']:.1f} expected flips on {ex['expected_pairs']:.1f} pairs, "
            f"noise share {_fmt_share(ex['noise_share_of_flip_count'])}."
        )
    return lines


def write_report(stem: Path, report: dict, title: str, label: str) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# {title}",
        "",
        label,
        "",
        f"Transition {report['day_a']} to {report['day_b']}, search groups {', '.join(report['groups'])}, max_date {report['max_date']}.",
        f"Script: `{THIS_FILE}` (consensus and pair rule imported from `scripts/claude/r2_t3_noise.py`). "
        f"Interval: 95% query bootstrap, {m.BOOT_DRAWS} draws, seed {m.BOOT_SEED}.",
        "",
    ]
    lines += _lines(report["summary"])
    for name, block in report.get("by_group", {}).items():
        lines += ["", f"Group {name}:", ""] + _lines(block)
    if "theory_design" in report:
        lines += ["", f"theory.md design number: {json.dumps(report['theory_design'])}. Reproduced: {json.dumps(report['reproduction'])}."]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_transition(rows: pd.DataFrame, cons: pd.DataFrame, day_a: str, day_b: str, queries: Iterable[str], groups: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict, dict]:
    searches = voting_pulls(rows, day_a, day_b, queries)
    if not searches:
        raise SystemExit(f"No search has voting pulls on both {day_a} and {day_b}.")
    flips, pairs = permutation_null(searches)
    table = search_table(searches, flips, pairs)
    check_observed(table, cons, day_a, day_b)
    summary = summarise_noise(table, flips, pairs)
    by_group = {}
    if groups is not None:
        table["grp"] = [groups.get(query) for query in table.index]
        for name in sorted(set(table["grp"].dropna())):
            mask = (table["grp"] == name).to_numpy()
            by_group[name] = summarise_noise(table[mask], flips[:, mask], pairs[:, mask])
    return table, summary, by_group


def run_design() -> dict:
    if rs.DESIGN_MAX_DATE != "2026-11-17":
        raise AssertionError("design cutoff changed")
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    if str(rows["day"].max()) > rs.DESIGN_MAX_DATE:
        raise AssertionError("sealed day loaded")
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows)
    queries = sorted(q for q in lt.aligned_queries(cons, lt.DESIGN_DAY_A, lt.DESIGN_DAY_B) if groups.get(q) in lt.DESIGN_GROUPS)
    _, summary, _ = run_transition(rows, cons, lt.DESIGN_DAY_A, lt.DESIGN_DAY_B, queries)
    exact = summary.get("exact_check", {})
    exact_share = exact.get("noise_share_of_flip_count", {})
    shuffled_share = summary["noise_share_of_flip_count"]

    def rounded(flips, pairs, share) -> dict:
        return {
            "expected_flips": round(flips, 1),
            "expected_pairs": round(pairs, 1),
            "noise_share": None if share.get("value") is None else round(share["value"], 2),
            "lo": None if share.get("lo") is None else round(share["lo"], 2),
            "hi": None if share.get("hi") is None else round(share["hi"], 2),
        }

    target = {key: THEORY_DESIGN[key] for key in ("expected_flips", "expected_pairs", "noise_share", "lo", "hi")}
    exact_round = rounded(exact.get("expected_flips", float("nan")), exact.get("expected_pairs", float("nan")), exact_share)
    shuffled_round = rounded(summary["shuffled"]["expected_flips"], summary["shuffled"]["expected_pairs"], shuffled_share)
    report = {
        "label": "DESIGN, not a test",
        "day_a": lt.DESIGN_DAY_A,
        "day_b": lt.DESIGN_DAY_B,
        "max_date": rs.DESIGN_MAX_DATE,
        "max_day_loaded": str(rows["day"].max()),
        "groups": list(lt.DESIGN_GROUPS),
        "summary": summary,
        "theory_design": THEORY_DESIGN,
        "reproduction": {
            "observed_matches": summary["observed"]["flips"] == THEORY_DESIGN["observed_flips"] and summary["observed"]["pairs"] == THEORY_DESIGN["observed_pairs"],
            "exact_enumeration_rounded": exact_round,
            "exact_enumeration_matches_to_stated_digits": exact_round == target,
            "shuffled_2000_rounded": shuffled_round,
            "shuffled_2000_matches_to_stated_digits": shuffled_round == target,
        },
    }
    write_report(DESIGN_OUT / "noise_check_design", report, "Target 3 permutation noise check, design transition", "DESIGN, not a test. Design days only (through 2026-11-17).")
    print(f"T3 noise check, DESIGN transition {lt.DESIGN_DAY_A} -> {lt.DESIGN_DAY_B}")
    for line in _lines(summary):
        print(" ", line)
    print(f"  reproduction: {json.dumps(report['reproduction'])}")
    return report


def run_score(day_a: str, day_b: str, max_date: str, out: Path, prereg: Path | None, groups_used: tuple[str, ...], force: bool, allow_incomplete: bool) -> dict:
    manifest = lt.require_prereg(prereg, script=THIS_FILE)  # before anything is loaded
    lt.check_score_args(day_a, day_b, max_date)
    stem = Path(out) / f"noise-{day_a}_{day_b}"
    if stem.with_suffix(".json").exists() and not force:
        raise SystemExit(f"{stem.with_suffix('.json')} already exists. A held-out transition is scored once. Use --force only to redo an identical run.")
    rows = rs.load_rows(max_date)
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows)
    queries = sorted(q for q in lt.aligned_queries(cons, day_a, day_b) if groups.get(q) in groups_used)
    if not queries:
        raise SystemExit(f"No search in groups {groups_used} has day {day_a} as its last pulled day before {day_b}.")
    counts = {"day_a": lt.pull_counts(cons, queries, day_a), "day_b": lt.pull_counts(cons, queries, day_b)}
    for block in counts.values():
        if block["day"] >= lt.FIRST_LEAD_PULLED_DAY and block["min_pulls"] < lt.LEAD_PULLS_PER_DAY and not allow_incomplete:
            raise SystemExit(f"{block['day']} has a search with fewer than {lt.LEAD_PULLS_PER_DAY} pulls: {block['by_count']}.")
    table, summary, by_group = run_transition(rows, cons, day_a, day_b, queries, groups)
    report = {
        "label": "PREDICTED band checked after the freeze",
        "day_a": day_a,
        "day_b": day_b,
        "gap_days": rs.calendar_gap(day_a, day_b),
        "max_date": max_date,
        "groups": list(groups_used),
        "prereg": str(Path(prereg).resolve().relative_to(ROOT)),
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "pulls": counts,
        "summary": summary,
        "by_group": by_group,
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    table.reset_index().to_csv(stem.parent / f"{stem.name}-searches.csv", index=False)
    write_report(stem, report, "Target 3 permutation noise check, held-out transition", "Scored once after the freeze.")
    print(f"T3 noise check, held-out transition {day_a} -> {day_b}")
    for line in _lines(summary):
        print(" ", line)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("design", help="Design transition 2026-11-16 -> 2026-11-17, original 30 searches.")
    score = sub.add_parser("score", help="Held-out transition. Lead only, after the freeze.")
    score.add_argument("--day-a", required=True)
    score.add_argument("--day-b", required=True)
    score.add_argument("--max-date", required=True, help="Must equal --day-b.")
    score.add_argument("--out", required=True, help="Output folder.")
    score.add_argument("--prereg-dir", help="Preregistration folder whose manifest.json freezes this file.")
    score.add_argument("--groups", default=",".join(lt.DECISION_GROUPS), help="Search groups (default o30,a70).")
    score.add_argument("--force", action="store_true", help="Overwrite an existing scored file (identical rerun only).")
    score.add_argument("--allow-incomplete", action="store_true", help="Score a lead-pulled day with fewer than three pulls. Not for the record.")
    args = parser.parse_args(argv)
    if args.command == "design":
        run_design()
        return 0
    prereg = (ROOT / args.prereg_dir).resolve() if args.prereg_dir else None
    groups_used = tuple(name.strip() for name in args.groups.split(",") if name.strip())
    run_score(args.day_a, args.day_b, args.max_date, (ROOT / args.out).resolve(), prereg, groups_used, args.force, args.allow_incomplete)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
