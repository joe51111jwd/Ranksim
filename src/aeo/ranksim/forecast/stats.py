"""Intervals for rates measured on clustered forecasts.

Forecasts for one site share that site's fate, so they are not independent. The
cluster bootstrap resamples whole clusters (sites, or searches). The Wilson
interval is given too and is only right when the forecasts are independent.
These are statistics of REAL grades. They do not make a forecast right.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Hashable, Sequence

import numpy as np

from .config import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED

Z95 = 1.959963984540054


def wilson(successes: float, total: float, z: float = Z95) -> tuple[float | None, float | None]:
    """Wilson score interval for a share. (None, None) when there is nothing to count."""
    if total <= 0:
        return None, None
    share = successes / total
    denom = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / denom
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _cluster_sums(numer: Sequence[float], denom: Sequence[float], clusters: Sequence[Hashable]) -> tuple[np.ndarray, np.ndarray]:
    if not (len(numer) == len(denom) == len(clusters)):
        raise ValueError("numer, denom and clusters must have the same length")
    sums: dict[Hashable, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for top, bottom, key in zip(numer, denom, clusters):
        entry = sums[key]
        entry[0] += float(top)
        entry[1] += float(bottom)
    keys = sorted(sums, key=repr)
    return np.array([sums[k][0] for k in keys]), np.array([sums[k][1] for k in keys])


def cluster_bootstrap_ratio(
    numer: Sequence[float],
    denom: Sequence[float],
    clusters: Sequence[Hashable],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """95% interval of sum(numer)/sum(denom), resampling whole clusters.

    For a hit rate pass numer = 1 or 0 per forecast and denom = 1. For precision
    pass denom = 1 only on page-one calls. Draws with an empty denominator are
    skipped. Fewer than 2 clusters gives (None, None): one cluster has no spread to resample.
    """
    tops, bottoms = _cluster_sums(numer, denom, clusters)
    count = len(tops)
    if count < 2 or bottoms.sum() <= 0:
        return None, None
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, count, size=(draws, count))
    top = tops[picks].sum(axis=1)
    bottom = bottoms[picks].sum(axis=1)
    keep = bottom > 0
    if not keep.any():
        return None, None
    rates = top[keep] / bottom[keep]
    low, high = np.quantile(rates, [0.025, 0.975])
    return float(low), float(high)


def cluster_bootstrap_diff(
    first: Sequence[float],
    second: Sequence[float],
    clusters: Sequence[Hashable],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None, float | None]:
    """Mean of (first - second) on the same forecasts, with a 95% cluster interval.

    Returns (difference, low, high). Use it for "model minus baseline" on identical forecasts.
    """
    if not (len(first) == len(second) == len(clusters)):
        raise ValueError("first, second and clusters must have the same length")
    if not first:
        return None, None, None
    gaps = [float(a) - float(b) for a, b in zip(first, second)]
    point = sum(gaps) / len(gaps)
    low, high = cluster_bootstrap_ratio(gaps, [1.0] * len(gaps), clusters, draws=draws, seed=seed)
    return point, low, high


def design_effect(values: Sequence[float], clusters: Sequence[Hashable]) -> float | None:
    """How much clustering inflates the variance of a mean, from the one-way intraclass correlation.

    1.0 means the forecasts behave as independent. None when it cannot be estimated.
    """
    groups: dict[Hashable, list[float]] = defaultdict(list)
    for value, key in zip(values, clusters):
        groups[key].append(float(value))
    total = sum(len(g) for g in groups.values())
    k = len(groups)
    if k < 2 or total <= k:
        return None
    grand = sum(sum(g) for g in groups.values()) / total
    between = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups.values()) / (k - 1)
    within = sum(sum((v - sum(g) / len(g)) ** 2 for v in g) for g in groups.values()) / (total - k)
    n0 = (total - sum(len(g) ** 2 for g in groups.values()) / total) / (k - 1)
    if n0 <= 0 or (between + (n0 - 1) * within) <= 0:
        return None
    icc = max(0.0, (between - within) / (between + (n0 - 1) * within))
    mean_size = total / k
    return 1.0 + (mean_size - 1.0) * icc
