"""Shared metric definitions for the Claude goal experiments.

Two readings of "within 3" are both scored, because GOAL.md says "within-3"
and the earlier gates say "3-wide":

  band3   the repo's 3-wide band: truth is one of the three positions starting
          at max(1, prediction - 1). Same rule as aeo.ranksim.hybrid.covers_three_wide.
  err3    absolute error of at most 3 positions.

Every interval is a query bootstrap: queries are resampled with replacement,
all rows of a drawn query come along. 95% percentile interval.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BOOT_DRAWS = 2000
BOOT_SEED = 20261119


def band3_hit(pred: int, truth: int) -> bool:
    low = max(1, int(pred) - 1)
    return low <= int(truth) <= low + 2


def err3_hit(pred: int, truth: int) -> bool:
    return abs(int(pred) - int(truth)) <= 3


def _per_query(frame: pd.DataFrame, columns: list[str], query_col: str) -> tuple[np.ndarray, np.ndarray]:
    grouped = frame.groupby(query_col, sort=True)
    sums = grouped[columns].sum().to_numpy(dtype=float)
    counts = grouped.size().to_numpy(dtype=float)
    return sums, counts


def bootstrap_rate(
    frame: pd.DataFrame, hit_col: str, *, query_col: str = "query", draws: int = BOOT_DRAWS, seed: int = BOOT_SEED
) -> dict:
    """Pooled rate of a 0/1 column with a query-bootstrap 95% interval."""
    if frame.empty:
        return {"hits": 0, "n": 0, "rate": None, "lo": None, "hi": None, "queries": 0}
    sums, counts = _per_query(frame, [hit_col], query_col)
    rng = np.random.default_rng(seed)
    size = len(counts)
    index = rng.integers(0, size, size=(draws, size))
    num = sums[:, 0][index].sum(axis=1)
    den = counts[index].sum(axis=1)
    stats = num / den
    return {
        "hits": int(sums[:, 0].sum()),
        "n": int(counts.sum()),
        "rate": float(sums[:, 0].sum() / counts.sum()),
        "lo": float(np.percentile(stats, 2.5)),
        "hi": float(np.percentile(stats, 97.5)),
        "queries": int(size),
    }


def bootstrap_difference(
    frame: pd.DataFrame,
    hit_col: str,
    base_col: str,
    *,
    query_col: str = "query",
    draws: int = BOOT_DRAWS,
    seed: int = BOOT_SEED,
) -> dict:
    """Paired difference of two 0/1 columns on the same rows, query bootstrap."""
    if frame.empty:
        return {"diff": None, "lo": None, "hi": None, "n": 0, "queries": 0}
    sums, counts = _per_query(frame, [hit_col, base_col], query_col)
    rng = np.random.default_rng(seed)
    size = len(counts)
    index = rng.integers(0, size, size=(draws, size))
    den = counts[index].sum(axis=1)
    stats = (sums[:, 0][index].sum(axis=1) - sums[:, 1][index].sum(axis=1)) / den
    total = counts.sum()
    return {
        "diff": float((sums[:, 0].sum() - sums[:, 1].sum()) / total),
        "lo": float(np.percentile(stats, 2.5)),
        "hi": float(np.percentile(stats, 97.5)),
        "n": int(total),
        "queries": int(size),
    }


def fmt_rate(result: dict) -> str:
    if not result.get("n"):
        return "0/0"
    return f"{result['hits']}/{result['n']} ({result['rate']:.3f}) [{result['lo']:.3f}, {result['hi']:.3f}]"


def fmt_diff(result: dict) -> str:
    if result.get("diff") is None:
        return "n/a"
    return f"{result['diff']:+.3f} [{result['lo']:+.3f}, {result['hi']:+.3f}]"
