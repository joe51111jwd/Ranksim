"""Round 2 loop 1, Target 3: the latent-order scorer (evals/claude/theory.md, section 3, "What gets built in loop 1").

Nothing in this file is fitted on any data. The half-life, the prior and the two call
rules are fixed by the theory text. Every output is PREDICTED.

  python scripts/claude/r2_t3_latent.py design
  python scripts/claude/r2_t3_latent.py score --day-a D1 --day-b D2 --max-date D2 --out DIR --prereg-dir evals/preregistered/claude-r2-<name>

`design` runs the one design transition (2026-11-16 to 2026-11-17, original 30 searches)
with max_date 2026-11-17 and writes evals/claude/r2/t3-latent/design.json and design.md.
`score` is for the lead, once per held-out transition, after the freeze. It refuses to
run unless --prereg-dir holds a manifest that verifies, lists this file, and is
committed and pushed.

THE MODEL (theory.md section 3). Over a day or two each URL pair has a stable chance q
that one pull shows it in a given order. A pull is one draw. A three-pull consensus is
the majority of three draws. A flip between two days is mostly two draws landing on
different sides.

WHICH VOTES ARE USED, exactly. For one transition (day A to day B) and one search:

  * The pair universe is every pair (x, y) of URLs in day A's consensus top 10 that is
    strictly ordered there (x better). This is rsdata.flip_pairs' pair definition and
    orientation, taken on day A alone: no day B information decides which pairs get a
    probability or a call. The output rows are then the rsdata.flip_pairs rows (both
    URLs in both top 10s, strictly ordered on both days), with `flipped` from there.
  * Votes come from every top-10 pull of the search dated on or before day A: the
    pulls of day A's consensus session, any other pull on day A (for Nov 16, the
    morning pull), and every pull of every earlier day. Deep (top 20 to 30) lists are
    not pulls and do not vote. No pull dated after day A is read; the code asserts it.
  * Each pull votes once for the order it shows. A URL missing from the pull counts as
    position 11, so a pull that shows only one of the two votes for that one on top.
    A pull that shows neither does not vote.
  * Each vote has weight 0.5 ** (age / 2), where age is the number of calendar days from
    the pull's snapshot day to day A (the day of day A's consensus session). All pulls
    on day A, voting or not, have age 0 and weight 1. A pull one day older weighs 0.71,
    two days 0.5, three days 0.35, ten days 0.03.
  * p = (weighted reverse votes + 0.5) / (all weighted votes + 1). This is the share of
    votes showing y above x, smoothed with a Jeffreys prior (0.5 on each side).
  * P(flip) = p^3 + 3 p^2 (1 - p): the chance that a strict majority of three
    independent pulls shows y above x, which is what day B's three-pull consensus
    shows in the theory's noise model. This is a fixed formula, not a fitted map.

WHY THIS IS NOT CIRCULAR. Day A's consensus is itself one such draw, and its orientation
(which order counts as "reverse") is computed from the same day A pulls that vote. That
does not double count anything: day A's consensus order is a function of those pulls,
so it adds no information beyond them, and each pull enters the share exactly once, as
one vote. What the share estimates is q, the per-pull order chance, and the day A pulls
are honest draws of q. The quantity predicted is a fresh draw: day B's consensus, built
only from day B's pulls, none of which is read. Given q, day B's pulls are independent
of every vote used, so P(flip) = B3(q) evaluated at an estimate of q made from the past.
The orientation does select on day A's draw (a pair that day A shows in its unusual
order is a pair whose day A votes lean reverse), and that is exactly the signal the
theory says to use: such a pair flips back with probability about q. Nothing about day
B, not even which pairs survive into day B's top 10, enters a probability or a call.

THE TWO CALL RULES (frozen by the theory):

  R1  call a flip on every pair with P(flip) above one half (equivalently, the weighted
      reverse votes outweigh the weighted keep votes).
  R2  sort the pair universe by P(flip), highest first, and call the largest leading
      set whose mean P(flip), the expected share of right calls, is at least one half.
      Pairs with equal P(flip) are taken or left together, so the set never depends on
      an arbitrary order among ties. R2 always contains R1.

R2's set is computed once per transition over the whole scored population (the
searches passed in, for example all 30 original searches on the design transition).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.freeze import verify_manifest  # noqa: E402

ABSENT = rs.ABSENT
HALF_LIFE_DAYS = 2.0  # fixed by theory.md section 3
PRIOR = 0.5  # Jeffreys: half a vote on each side
CONSENSUS_PULLS = 3  # day B's consensus is a majority of three pulls
R1_THRESHOLD = 0.5  # call when P(flip) is above this
R2_FLOOR = 0.5  # mean P(flip) of the called set must be at least this
TIE_DECIMALS = 12  # P(flip) values equal to 12 decimals are one tie group for R2

DESIGN_DAY_A = "2026-11-16"
DESIGN_DAY_B = "2026-11-17"
DESIGN_GROUPS = ("o30",)
DECISION_GROUPS = ("o30", "a70")
FIRST_HELDOUT_DAY = "2026-11-18"
FIRST_LEAD_PULLED_DAY = "2026-11-19"
LEAD_PULLS_PER_DAY = 3
DESIGN_OUT = ROOT / "evals" / "claude" / "r2" / "t3-latent"
THIS_FILE = str(Path(__file__).resolve().relative_to(ROOT))

# The theory's stated design expectation for a predictor of this kind that sees the four Nov 16 pulls.
THEORY_DESIGN = {"calls": 38, "right": 16, "flip_recall": 0.36, "precision": 0.42}
# The theory's PREDICTED range for the held-out transitions in hand.
THEORY_HELDOUT = {"flip_recall": [0.35, 0.55], "precision": [0.40, 0.55]}


# --------------------------------------------------------------------------- the model


def vote_weight(pull_day: str, day_a: str) -> float:
    """0.5 ** (age / 2), age = calendar days from the pull's day to day A. Day A itself is age 0, weight 1."""
    age = rs.calendar_gap(pull_day, day_a)
    if age < 0:
        raise AssertionError(f"pull dated {pull_day} is after day A {day_a}")
    return 0.5 ** (age / HALF_LIFE_DAYS)


def smoothed_share(w_rev: float, w_keep: float) -> float:
    """Weighted share of votes for the reverse order with a Jeffreys prior (0.5 added to each side)."""
    return (w_rev + PRIOR) / (w_rev + w_keep + 2 * PRIOR)


def p_flip_from_share(p):
    """P(a strict majority of 3 independent pulls shows the reverse order) = p^3 + 3 p^2 (1 - p)."""
    return p**3 + 3 * p**2 * (1 - p)


def pull_vote(ranks: dict, key_x: str, key_y: str) -> int:
    """+1 if the pull shows x above y, -1 if y above x, 0 if it shows neither. Missing = position 11."""
    rx, ry = ranks.get(key_x, ABSENT), ranks.get(key_y, ABSENT)
    if rx == ry:
        return 0
    return 1 if rx < ry else -1


def assert_history(history_rows: pd.DataFrame, day_a: str) -> None:
    """The features for day B may only read rows dated on or before day A."""
    if len(history_rows) and str(history_rows["day"].max()) > str(day_a):
        raise AssertionError(f"history holds day {history_rows['day'].max()}, after day A {day_a}")


def latent_pairs(history_rows: pd.DataFrame, day_a: str, queries: Iterable[str] | None = None) -> pd.DataFrame:
    """Day A's pair universe with pooled votes and P(flip). Reads rows dated on or before day A only.

    One row per strictly ordered pair of day A's consensus top 10 (key_x better on day A).
    Columns: query, day_a, key_x, key_y, a_x, a_y, n_pulls, n_votes, n_rev, w_keep, w_rev,
    share_rev, p_flip.
    """
    assert_history(history_rows, day_a)
    pulls = rs.pulls_by_query_day(history_rows)
    wanted = None if queries is None else set(queries)
    by_query: dict[str, list[str]] = {}
    for query, day in pulls:
        if wanted is None or query in wanted:
            by_query.setdefault(query, []).append(day)
    records = []
    for query in sorted(by_query):
        if (query, day_a) not in pulls:
            continue
        voting = rs.consensus_session(pulls[(query, day_a)])
        keys = []
        for pull in voting:
            for key in pull.ranks:
                if key not in keys:
                    keys.append(key)
        cons_a = {key: rs.consensus_position([pull.ranks.get(key, ABSENT) for pull in voting]) for key in keys}
        weighted = []  # (weight, ranks), oldest pull first
        for day in sorted(by_query[query]):
            if day > day_a:
                raise AssertionError(f"pull dated {day} is after day A {day_a}")
            weight = vote_weight(day, day_a)
            for pull in pulls[(query, day)]:
                if pull.day > day_a:
                    raise AssertionError(f"pull dated {pull.day} is after day A {day_a}")
                weighted.append((weight, pull.ranks))
        top = sorted(((key, value) for key, value in cons_a.items() if value <= 10), key=lambda item: (item[1], item[0]))
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                (key_x, a_x), (key_y, a_y) = top[i], top[j]
                if a_x == a_y:
                    continue
                keep, rev = [], []
                for weight, ranks in weighted:
                    vote = pull_vote(ranks, key_x, key_y)
                    if vote > 0:
                        keep.append(weight)
                    elif vote < 0:
                        rev.append(weight)
                w_keep, w_rev = math.fsum(keep), math.fsum(rev)  # exact sums: equal vote sets give equal shares
                share = smoothed_share(w_rev, w_keep)
                records.append(
                    {
                        "query": query,
                        "day_a": day_a,
                        "key_x": key_x,
                        "key_y": key_y,
                        "a_x": int(a_x),
                        "a_y": int(a_y),
                        "n_pulls": len(weighted),
                        "n_votes": len(keep) + len(rev),
                        "n_rev": len(rev),
                        "w_keep": w_keep,
                        "w_rev": w_rev,
                        "share_rev": share,
                        "p_flip": float(p_flip_from_share(share)),
                    }
                )
    columns = ["query", "day_a", "key_x", "key_y", "a_x", "a_y", "n_pulls", "n_votes", "n_rev", "w_keep", "w_rev", "share_rev", "p_flip"]
    return pd.DataFrame.from_records(records, columns=columns)


def r1_calls(p_flip) -> np.ndarray:
    """R1: call every pair with P(flip) above one half."""
    return np.asarray(p_flip, dtype=float) > R1_THRESHOLD


def r2_calls(p_flip) -> np.ndarray:
    """R2: the largest leading set, in descending P(flip), whose mean P(flip) is at least one half.

    Equal P(flip) values (to 12 decimals) are taken or left as one group.
    """
    values = np.asarray(p_flip, dtype=float)
    if values.size == 0:
        return np.zeros(0, dtype=bool)
    rounded = np.round(values, TIE_DECIMALS)
    cut = None
    count = 0
    total = 0.0
    for value in sorted(set(rounded.tolist()), reverse=True):
        members = rounded == value
        new_count = count + int(members.sum())
        new_total = total + float(values[members].sum())
        if new_total / new_count < R2_FLOOR:
            break  # the running mean only falls from here on
        count, total, cut = new_count, new_total, value
    if cut is None:
        return np.zeros(values.size, dtype=bool)
    return rounded >= cut


def apply_rules(universe: pd.DataFrame) -> pd.DataFrame:
    """Add call_r1 and call_r2. R2's budget runs over every row given (one transition's population)."""
    out = universe.copy()
    out["call_r1"] = r1_calls(out["p_flip"])
    out["call_r2"] = r2_calls(out["p_flip"])
    return out


# --------------------------------------------------------------------------- one transition


def aligned_queries(cons: pd.DataFrame, day_a: str, day_b: str) -> set[str]:
    """Searches whose last pulled day before day B is day A (same filter as the round 1 harness)."""
    days = rs.query_days(cons)
    return {query for query, listed in days.items() if day_b in listed and rs.previous_day(listed, day_b) == day_a}


def transition_calls(rows: pd.DataFrame, day_a: str, queries: Iterable[str]) -> pd.DataFrame:
    """Day A's pair universe for the given searches, with P(flip) and both calls. Only rows dated <= day A are passed on."""
    history = rows[rows["day"] <= day_a]
    assert_history(history, day_a)
    return apply_rules(latent_pairs(history, day_a, queries))


def score_transition(rows: pd.DataFrame, cons: pd.DataFrame, day_a: str, day_b: str, queries: Iterable[str] | None = None) -> pd.DataFrame:
    """One row per rsdata.flip_pairs pair: query, key_x, key_y, p_flip, call_r1, call_r2, flipped, and helpers.

    ``rows`` and ``cons`` may hold day B: only rsdata.flip_pairs reads it, for the pair
    rows and the outcome. P(flip) and the calls come from transition_calls, which is
    handed rows dated on or before day A. ``queries`` defaults to every search whose last
    pulled day before day B is day A. R2 is computed over day A's universe of those searches.
    """
    if not str(day_a) < str(day_b):
        raise ValueError("day_a must be before day_b")
    chosen = aligned_queries(cons, day_a, day_b) if queries is None else set(queries)
    universe = transition_calls(rows, day_a, chosen)
    # Day A's consensus from history must equal the table the outcome comes from.
    cons_a = cons[(cons["day"] == day_a) & (cons["cons"] <= 10) & cons["query"].isin(chosen)]
    expected = {(q, k): int(v) for q, k, v in zip(cons_a["query"], cons_a["key"], cons_a["cons"], strict=True)}
    found = {}
    for q, kx, ky, ax, ay in zip(universe["query"], universe["key_x"], universe["key_y"], universe["a_x"], universe["a_y"], strict=True):
        found[(q, kx)], found[(q, ky)] = int(ax), int(ay)
    if any(expected.get(key) != value for key, value in found.items()):
        raise AssertionError("day A consensus from history differs from the consensus table")
    pairs = rs.flip_pairs(cons, day_a, day_b)
    if pairs.empty:
        return pairs
    pairs = pairs[pairs["query"].isin(chosen)].reset_index(drop=True)
    keep = ["query", "key_x", "key_y", "n_pulls", "n_votes", "n_rev", "w_keep", "w_rev", "share_rev", "p_flip", "call_r1", "call_r2"]
    frame = pairs.merge(universe[keep], on=["query", "key_x", "key_y"], how="left", validate="one_to_one")
    if frame["p_flip"].isna().any():
        raise AssertionError("a flip_pairs row has no day A universe row")
    frame["flipped"] = frame["flipped"].astype(bool)
    frame["call_r1"] = frame["call_r1"].astype(bool)
    frame["call_r2"] = frame["call_r2"].astype(bool)
    frame["persist_correct"] = (~frame["flipped"]).astype(int)
    for rule in ("r1", "r2"):
        frame[f"correct_{rule}"] = (frame[f"call_{rule}"] == frame["flipped"]).astype(int)
    return frame


def _empty_rate() -> dict:
    return {"hits": 0, "n": 0, "rate": None, "lo": None, "hi": None, "queries": 0}


CALIBRATION_BANDS = (0.0, 0.05, 0.2, 0.5, 1.0)  # (lo, hi] bands of P(flip); the first band includes 0


def calibration(frame: pd.DataFrame) -> list[dict]:
    """Scored pairs, model-expected flips and observed flips per P(flip) band. A check of the noise model, not a rule."""
    out = []
    for lo, hi in zip(CALIBRATION_BANDS[:-1], CALIBRATION_BANDS[1:], strict=True):
        part = frame[(frame["p_flip"] > lo) & (frame["p_flip"] <= hi)] if lo > 0 else frame[frame["p_flip"] <= hi]
        out.append({"p_flip_band": f"{'[' if lo == 0 else '('}{lo:g}, {hi:g}]", "pairs": int(len(part)), "expected_flips": float(part["p_flip"].sum()), "observed_flips": int(part["flipped"].sum())})
    return out


def summarise(frame: pd.DataFrame) -> dict:
    """Flip recall, precision and all-pairs accuracy minus persistence for each rule, with query-bootstrap intervals."""
    out: dict = {
        "pairs": int(len(frame)),
        "flips": int(frame["flipped"].sum()) if len(frame) else 0,
        "queries": int(frame["query"].nunique()) if len(frame) else 0,
        "flip_queries": int(frame.loc[frame["flipped"], "query"].nunique()) if len(frame) else 0,
        "expected_flips_model": float(frame["p_flip"].sum()) if len(frame) else 0.0,
        "persistence": m.bootstrap_rate(frame, "persist_correct") if len(frame) else _empty_rate(),
        "calibration": calibration(frame) if len(frame) else [],
    }
    for rule in ("r1", "r2"):
        if frame.empty:
            out[rule] = {"calls": 0}
            continue
        call = frame[f"call_{rule}"].astype(bool)
        flipped = frame["flipped"].astype(bool)
        flips = frame[flipped].assign(hit=call[flipped].astype(int))
        called = frame[call].assign(hit=flipped[call].astype(int))
        recall = m.bootstrap_rate(flips, "hit") if len(flips) else _empty_rate()
        precision = m.bootstrap_rate(called, "hit") if len(called) else _empty_rate()
        diff = m.bootstrap_difference(frame, f"correct_{rule}", "persist_correct")
        out[rule] = {
            "calls": int(call.sum()),
            "right": int((call & flipped).sum()),
            "flip_recall": recall,
            "precision": precision,
            "all_pairs_rule": m.bootstrap_rate(frame, f"correct_{rule}"),
            "all_pairs_minus_persistence": diff,
            "expected_precision_model": float(frame.loc[call, "p_flip"].mean()) if call.any() else None,
            "expected_recall_model": float(frame.loc[call, "p_flip"].sum() / frame["p_flip"].sum()),
            "recall_lower_bound_above_half": bool(recall["lo"] is not None and recall["lo"] > 0.5),
            "not_below_persistence": bool(diff["diff"] is not None and diff["diff"] >= 0),
        }
    return out


# --------------------------------------------------------------------------- reports


def _fmt_rate(block: dict) -> str:
    return m.fmt_rate(block) if block.get("n") else "0/0"


def universe_summary(universe: pd.DataFrame) -> dict:
    if universe.empty:
        return {"pairs": 0}
    cut = universe.loc[universe["call_r2"], "p_flip"].min() if universe["call_r2"].any() else None
    return {
        "pairs": int(len(universe)),
        "queries": int(universe["query"].nunique()),
        "calls_r1": int(universe["call_r1"].sum()),
        "calls_r2": int(universe["call_r2"].sum()),
        "r2_lowest_p_flip_called": None if cut is None else float(cut),
        "r2_mean_p_flip_called": float(universe.loc[universe["call_r2"], "p_flip"].mean()) if universe["call_r2"].any() else None,
        "pulls_per_search": {str(k): int(v) for k, v in universe.groupby("query")["n_pulls"].first().value_counts().sort_index().items()},
    }


def vote_weights_table(rows: pd.DataFrame, day_a: str, queries: Iterable[str]) -> list[dict]:
    """Pull days used on or before day A, with their vote weight and the number of pulls."""
    history = rows[(rows["day"] <= day_a) & rows["query"].isin(set(queries))]
    pulls = rs.pulls_by_query_day(history)
    counts: dict[str, int] = {}
    for (_, day), listed in pulls.items():
        counts[day] = counts.get(day, 0) + len(listed)
    return [{"day": day, "age_days": rs.calendar_gap(day, day_a), "weight": vote_weight(day, day_a), "pulls": counts[day]} for day in sorted(counts)]


def _rule_lines(summary: dict) -> list[str]:
    lines = [
        "| Rule | Calls | Flipped pairs called right (flip recall) | Calls that were right (precision) | All pairs: rule minus persistence |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for rule, label in (("r1", "R1: P(flip) above 0.5"), ("r2", "R2: largest set with mean P(flip) at least 0.5")):
        block = summary[rule]
        if not block.get("calls") and "flip_recall" not in block:
            lines.append(f"| {label} | 0 | n/a | n/a | n/a |")
            continue
        lines.append(
            f"| {label} | {block['calls']} | {_fmt_rate(block['flip_recall'])} | {_fmt_rate(block['precision'])} | {m.fmt_diff(block['all_pairs_minus_persistence'])} |"
        )
    return lines


def write_report(path_stem: Path, report: dict, title: str, label: str) -> None:
    path_stem.parent.mkdir(parents=True, exist_ok=True)
    path_stem.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    main = report["summary"]
    lines = [
        f"# {title}",
        "",
        label,
        "",
        f"Transition {report['day_a']} to {report['day_b']}, search groups {', '.join(report['groups'])}, max_date {report['max_date']}.",
        f"Half-life {HALF_LIFE_DAYS:g} days, Jeffreys prior {PRIOR:g} on each side, P(flip) = p^3 + 3 p^2 (1 - p). Nothing fitted.",
        f"Script: `{THIS_FILE}`. Intervals: 95% query bootstrap, {m.BOOT_DRAWS} draws, seed {m.BOOT_SEED}.",
        "",
        f"Scored pairs (rsdata.flip_pairs): {main['pairs']} on {main['queries']} searches, {main['flips']} flipped. "
        f"Persistence is right on {_fmt_rate(main['persistence'])}.",
        f"Flips expected by the model (sum of P(flip) over scored pairs): {main['expected_flips_model']:.1f}.",
        f"Day A pair universe (no day B information): {report['universe']['pairs']} pairs; R1 calls {report['universe']['calls_r1']}, "
        f"R2 calls {report['universe']['calls_r2']}.",
        "",
    ]
    lines += _rule_lines(main)
    lines += ["", "Calibration of the noise model on the scored pairs (a check, not a rule):", "", "| P(flip) | Pairs | Expected flips | Observed flips |", "| --- | ---: | ---: | ---: |"]
    for item in main["calibration"]:
        lines.append(f"| {item['p_flip_band']} | {item['pairs']} | {item['expected_flips']:.1f} | {item['observed_flips']} |")
    lines += ["", "Votes by pull day (weight = 0.5 ** (age / 2), age in calendar days before day A):", "", "| Day | Age | Weight | Pulls |", "| --- | ---: | ---: | ---: |"]
    for item in report["vote_weights"]:
        lines.append(f"| {item['day']} | {item['age_days']} | {item['weight']:.3f} | {item['pulls']} |")
    for name, block in report.get("by_group", {}).items():
        lines += ["", f"Group {name}: {block['pairs']} pairs, {block['flips']} flipped.", ""]
        lines += _rule_lines(block)
    extra = report.get("diagnostics")
    if extra:
        lines += ["", "Diagnostics (not part of the rules):", ""]
        for key, value in extra.items():
            lines.append(f"- {key}: {value}")
    path_stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_summary(summary: dict) -> None:
    print(f"  pairs {summary['pairs']}, flips {summary['flips']}, model-expected flips {summary['expected_flips_model']:.1f}")
    for rule in ("r1", "r2"):
        block = summary[rule]
        if "flip_recall" not in block:
            print(f"  {rule}: no pairs")
            continue
        print(
            f"  {rule}: calls {block['calls']}, flip recall {_fmt_rate(block['flip_recall'])}, precision {_fmt_rate(block['precision'])}, "
            f"all pairs minus persistence {m.fmt_diff(block['all_pairs_minus_persistence'])}"
        )


def run_design() -> dict:
    """The one design transition: Nov 16 to Nov 17, original 30 searches, rows through 2026-11-17."""
    if rs.DESIGN_MAX_DATE != "2026-11-17":
        raise AssertionError("design cutoff changed")
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    if str(rows["day"].max()) > rs.DESIGN_MAX_DATE:
        raise AssertionError("sealed day loaded")
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows)
    queries = sorted(q for q in aligned_queries(cons, DESIGN_DAY_A, DESIGN_DAY_B) if groups.get(q) in DESIGN_GROUPS)
    frame = score_transition(rows, cons, DESIGN_DAY_A, DESIGN_DAY_B, queries)
    universe = transition_calls(rows, DESIGN_DAY_A, queries)
    summary = summarise(frame)
    # Diagnostic only: R2 with its budget run over the scored pairs instead of day A's universe.
    scored_r2 = r2_calls(frame["p_flip"])
    flipped = frame["flipped"].to_numpy(dtype=bool)
    report = {
        "label": "DESIGN, PREDICTED, not a test",
        "day_a": DESIGN_DAY_A,
        "day_b": DESIGN_DAY_B,
        "max_date": rs.DESIGN_MAX_DATE,
        "max_day_loaded": str(rows["day"].max()),
        "groups": list(DESIGN_GROUPS),
        "searches": len(queries),
        "fixed_by_theory": {"half_life_days": HALF_LIFE_DAYS, "prior_each_side": PRIOR, "consensus_pulls": CONSENSUS_PULLS, "r1_threshold": R1_THRESHOLD, "r2_floor": R2_FLOOR},
        "vote_weights": vote_weights_table(rows, DESIGN_DAY_A, queries),
        "universe": universe_summary(universe),
        "summary": summary,
        "theory_expectation_four_nov16_pulls": THEORY_DESIGN,
        "theory_heldout_prediction": THEORY_HELDOUT,
        "diagnostics": {
            "R2 with its budget run over the scored pairs instead of day A's universe: calls / right": f"{int(scored_r2.sum())} / {int((scored_r2 & flipped).sum())}",
            "scored pairs with P(flip) exactly 0.5 (weighted tie)": int((np.round(frame["p_flip"], TIE_DECIMALS) == 0.5).sum()),
        },
    }
    write_report(DESIGN_OUT / "design", report, "Target 3 latent-order scorer, design transition", "DESIGN, PREDICTED, not a test. Design days only (through 2026-11-17).")
    print(f"T3 latent-order scorer, DESIGN transition {DESIGN_DAY_A} -> {DESIGN_DAY_B} (PREDICTED, not a test)")
    _print_summary(summary)
    return report


# --------------------------------------------------------------------------- held-out scoring (lead only)


def require_prereg(directory: Path | None, script: str = THIS_FILE) -> dict:
    """Refuse unless the preregistration folder's manifest verifies, lists ``script``, and is committed and pushed."""
    if directory is None:
        raise SystemExit("score needs --prereg-dir: a preregistration folder whose manifest verifies.")
    directory = Path(directory)
    if not (directory / "manifest.json").is_file():
        raise SystemExit(f"No manifest.json in {directory}. Freeze the instrument first.")
    manifest = verify_manifest(directory)
    if script not in manifest.get("sha256", {}):
        raise SystemExit(f"The manifest in {directory} does not freeze {script}.")
    relative = str(directory.resolve().relative_to(ROOT))
    tracked = [relative] + list(manifest["sha256"])
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *tracked], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit(f"Frozen files have uncommitted changes:\n{dirty}")
    unpushed = subprocess.run(["git", "log", "--oneline", "@{upstream}..HEAD", "--", *tracked], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if unpushed:
        raise SystemExit(f"The freeze is committed but not pushed:\n{unpushed}")
    return manifest


def check_score_args(day_a: str, day_b: str, max_date: str) -> None:
    for value in (day_a, day_b, max_date):
        rs._check_date(value)
    if not day_a < day_b:
        raise SystemExit("day_a must be before day_b.")
    if max_date != day_b:
        raise SystemExit("--max-date must equal --day-b: nothing after the outcome day is loaded.")
    if day_b < FIRST_HELDOUT_DAY:
        raise SystemExit(f"{day_b} is a design day. Use the design subcommand.")


def pull_counts(cons: pd.DataFrame, queries: Iterable[str], day: str) -> dict:
    part = cons[(cons["day"] == day) & cons["query"].isin(set(queries))]
    per_query = part.groupby("query")["n_pulls"].max()
    return {
        "day": day,
        "searches": int(len(per_query)),
        "min_pulls": int(per_query.min()) if len(per_query) else 0,
        "max_pulls": int(per_query.max()) if len(per_query) else 0,
        "by_count": {str(int(k)): int(v) for k, v in per_query.value_counts().sort_index().items()},
    }


def run_score(day_a: str, day_b: str, max_date: str, out: Path, prereg: Path | None, groups_used: tuple[str, ...], force: bool, allow_incomplete: bool) -> dict:
    manifest = require_prereg(prereg)  # before anything is loaded
    check_score_args(day_a, day_b, max_date)
    stem = Path(out) / f"latent-{day_a}_{day_b}"
    if stem.with_suffix(".json").exists() and not force:
        raise SystemExit(f"{stem.with_suffix('.json')} already exists. A held-out transition is scored once. Use --force only to redo an identical run.")
    rows = rs.load_rows(max_date)
    cons = rs.consensus_table(rows)
    groups = rs.query_groups(rows)
    queries = sorted(q for q in aligned_queries(cons, day_a, day_b) if groups.get(q) in groups_used)
    if not queries:
        raise SystemExit(f"No search in groups {groups_used} has day {day_a} as its last pulled day before {day_b}.")
    counts = {"day_a": pull_counts(cons, queries, day_a), "day_b": pull_counts(cons, queries, day_b)}
    for block in counts.values():
        if block["day"] >= FIRST_LEAD_PULLED_DAY and block["min_pulls"] < LEAD_PULLS_PER_DAY and not allow_incomplete:
            raise SystemExit(f"{block['day']} has a search with fewer than {LEAD_PULLS_PER_DAY} pulls: {block['by_count']}.")
    frame = score_transition(rows, cons, day_a, day_b, queries)
    if frame.empty:
        raise SystemExit(f"No pairs to score on {day_a} -> {day_b}.")
    frame["grp"] = frame["query"].map(groups)
    universe = transition_calls(rows, day_a, queries)
    report = {
        "label": "PREDICTED",
        "day_a": day_a,
        "day_b": day_b,
        "gap_days": rs.calendar_gap(day_a, day_b),
        "max_date": max_date,
        "groups": list(groups_used),
        "searches": len(queries),
        "prereg": str(Path(prereg).resolve().relative_to(ROOT)),
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "pulls": counts,
        "fixed_by_theory": {"half_life_days": HALF_LIFE_DAYS, "prior_each_side": PRIOR, "consensus_pulls": CONSENSUS_PULLS, "r1_threshold": R1_THRESHOLD, "r2_floor": R2_FLOOR},
        "vote_weights": vote_weights_table(rows, day_a, queries),
        "universe": universe_summary(universe),
        "summary": summarise(frame),
        "by_group": {str(name): summarise(part) for name, part in frame.groupby("grp")},
    }
    Path(out).mkdir(parents=True, exist_ok=True)
    frame.to_csv(stem.parent / f"{stem.name}-pairs.csv", index=False)
    write_report(stem, report, "Target 3 latent-order scorer, held-out transition", "PREDICTED. Scored once after the freeze.")
    print(f"T3 latent-order scorer, held-out transition {day_a} -> {day_b} (PREDICTED)")
    _print_summary(report["summary"])
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
    score.add_argument("--groups", default=",".join(DECISION_GROUPS), help="Search groups scored together (default o30,a70).")
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
