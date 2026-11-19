"""Held-out scoring for the Claude goal targets. Written by the lead, independent of the design workers.

A frozen predictor is called with history strictly before the outcome day. The
outcome day's rows are used only as truth and to name the rows to predict.
Every model number is PREDICTED.
"""

from __future__ import annotations

import pandas as pd

from scripts.claude import metrics as m
from scripts.claude import rsdata as rs

T1_BASELINES = ("domain_prior", "const7", "const11")
# A slice smaller than this cannot pass: a query bootstrap on a handful of rows has no meaning.
T1_MIN_ROWS = 20
T1_MIN_QUERIES = 10


def history_before(rows: pd.DataFrame, day: str) -> pd.DataFrame:
    history = rows[rows["day"] < day]
    if len(history) and history["day"].max() >= day:
        raise AssertionError("history contains the outcome day")
    return history.copy()


# ---------------------------------------------------------------- Target 1


def t1_frame(rows: pd.DataFrame, cons: pd.DataFrame, day: str, predict_rows, deep_queries: set[str]) -> pd.DataFrame:
    """Non-survivor top-10 rows of ``day`` with the frozen prediction, the baselines, and hit flags.

    strict_new marks the primary class: klass == 'new' on a query whose history
    holds a deep (top 20 to 30) list, so "new to the top 30" is checkable.
    """
    placed = rs.placement_rows(rows, cons, day)
    if placed.empty:
        return placed
    targets = placed[placed["klass"] != "survivor"].reset_index(drop=True)
    if targets.empty:
        return targets
    history = history_before(rows, day)
    preds = predict_rows(history, targets[["query", "key", "url", "site", "day"]].copy())
    if len(preds) != len(targets):
        raise AssertionError("predictor returned the wrong number of rows")
    targets["pred"] = [min(max(int(value), 1), rs.ABSENT) for value in preds]
    targets["const7"] = 7
    targets["const11"] = 11
    targets["strict_new"] = (targets["klass"] == "new") & targets["query"].isin(deep_queries)
    for name in ("pred",) + T1_BASELINES:
        targets[f"band3_{name}"] = [int(m.band3_hit(p, t)) for p, t in zip(targets[name], targets["truth"], strict=True)]
        targets[f"err3_{name}"] = [int(m.err3_hit(p, t)) for p, t in zip(targets[name], targets["truth"], strict=True)]
    return targets


def t1_summary(frame: pd.DataFrame) -> dict:
    """Rates for the model and the three baselines, and the paired difference against the best baseline."""
    out: dict = {"n": int(len(frame)), "queries": int(frame["query"].nunique()) if len(frame) else 0}
    big_enough = out["n"] >= T1_MIN_ROWS and out["queries"] >= T1_MIN_QUERIES
    out["big_enough"] = bool(big_enough)
    for metric in ("band3", "err3"):
        block: dict = {}
        for name in ("pred",) + T1_BASELINES:
            block[name] = m.bootstrap_rate(frame, f"{metric}_{name}")
        if len(frame):
            best = max(T1_BASELINES, key=lambda name: (block[name]["rate"], name))
            block["best_baseline"] = best
            block["diff_vs_best"] = m.bootstrap_difference(frame, f"{metric}_pred", f"{metric}_{best}")
            block["pass"] = bool(big_enough and block["diff_vs_best"]["lo"] is not None and block["diff_vs_best"]["lo"] > 0)
        else:
            block["best_baseline"] = None
            block["diff_vs_best"] = {"diff": None, "lo": None, "hi": None, "n": 0, "queries": 0}
            block["pass"] = False
        out[metric] = block
    return out


# ---------------------------------------------------------------- Target 3


def t3_frame(rows: pd.DataFrame, cons: pd.DataFrame, day_a: str, day_b: str, score_urls) -> pd.DataFrame:
    """Every strictly ordered both-day pair with the frozen scorer's call.

    calls_flip is True when the scorer ranks the pair opposite to day_a. A score
    tie keeps day_a's order. correct is calls_flip == flipped.
    """
    pairs = rs.flip_pairs(cons, day_a, day_b)
    if pairs.empty:
        return pairs
    history = history_before(rows, day_b)
    calls = []
    for query, group in pairs.groupby("query", sort=True):
        keys = sorted(set(group["key_x"]) | set(group["key_y"]))
        scores = score_urls(history, query, keys, day_b)
        for key_x, key_y in zip(group["key_x"], group["key_y"], strict=True):
            calls.append((query, key_x, key_y, bool(float(scores[key_y]) < float(scores[key_x]))))
    called = pd.DataFrame(calls, columns=["query", "key_x", "key_y", "calls_flip"])
    frame = pairs.merge(called, on=["query", "key_x", "key_y"], how="left", validate="one_to_one")
    frame["flipped"] = frame["flipped"].astype(bool)
    frame["correct"] = (frame["calls_flip"] == frame["flipped"]).astype(int)
    frame["persist_correct"] = (~frame["flipped"]).astype(int)
    return frame


def t3_summary(frame: pd.DataFrame) -> dict:
    flipped = frame[frame["flipped"]]
    steady = frame[~frame["flipped"]]
    called = frame[frame["calls_flip"]]
    flip_acc = m.bootstrap_rate(flipped, "correct")
    all_diff = m.bootstrap_difference(frame, "correct", "persist_correct")
    return {
        "pairs": int(len(frame)),
        "flips": int(len(flipped)),
        "flip_accuracy": flip_acc,
        "nonflip_accuracy": m.bootstrap_rate(steady, "correct"),
        "all_pairs_model": m.bootstrap_rate(frame, "correct"),
        "all_pairs_persistence": m.bootstrap_rate(frame, "persist_correct"),
        "all_pairs_diff": all_diff,
        "flip_calls": int(len(called)),
        "flip_call_precision": m.bootstrap_rate(called.assign(hit=called["flipped"].astype(int)), "hit") if len(called) else None,
        "goal_criterion_pass": bool(flip_acc["lo"] is not None and flip_acc["lo"] > 0.5),
        "guard_pass": bool(all_diff["diff"] is not None and all_diff["diff"] >= 0),
    }


def deep_queries(rows: pd.DataFrame, before_day: str) -> set[str]:
    """Queries with a deep list dated strictly before ``before_day``."""
    deep = rows[(rows["kind"] == "deep") & (rows["day"] < before_day)]
    return set(deep["query"])
