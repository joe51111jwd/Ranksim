"""Hybrid placement. Persistence keeps URLs it saw yesterday. The model only places the rest.

A new URL, or a URL whose page text changed, is inserted into yesterday's order
where the model ranks it against those surviving URLs. The others shift down.
Predictions are PREDICTED.
"""

from __future__ import annotations

import random
from collections import defaultdict


def three_wide(pred: int) -> tuple[int, int]:
    """Inclusive width 3. A prediction of 1 covers 1 through 3."""
    low = max(1, int(pred) - 1)
    return low, low + 2


def covers_three_wide(pred: int, truth: int) -> bool:
    low, high = three_wide(pred)
    return low <= int(truth) <= high


def insert_into_persistence(
    anchors: list[str],
    placed: list[str],
    scores: dict[str, float],
) -> list[str]:
    """Anchors stay in the given order. Each placed URL is inserted after the anchors the model ranks above it."""
    groups: dict[int, list[str]] = defaultdict(list)
    for key in placed:
        score = scores.get(key, float("-inf"))
        better = sum(1 for anchor in anchors if scores.get(anchor, float("-inf")) > score)
        groups[better].append(key)
    for keys in groups.values():
        keys.sort(key=lambda key: (-scores.get(key, float("-inf")), key))
    order: list[str] = []
    for index, anchor in enumerate(anchors):
        order.extend(groups.pop(index, []))
        order.append(anchor)
    for index in sorted(groups):
        order.extend(groups[index])
    return order


def positions_from_order(order: list[str]) -> dict[str, int]:
    return {key: index + 1 for index, key in enumerate(order)}


def new_url_scores(pred: int, truth: int, shown: tuple[int, ...]) -> dict[str, float]:
    """One new URL. Top-3 entry is whether a top-3 arrival was predicted in the top 3."""
    return {
        "exact": 1.0 if int(pred) == int(truth) else 0.0,
        "three_wide": 1.0 if covers_three_wide(pred, truth) else 0.0,
        "top3_hit": 1.0 if int(truth) <= 3 and int(pred) <= 3 else 0.0,
        "top3_case": 1.0 if int(truth) <= 3 else 0.0,
        "observed": 1.0 if int(pred) in shown else 0.0,
        "count": 1.0,
    }


def pool_new(rows: list[dict[str, float]]) -> dict[str, float]:
    totals = {"exact": 0.0, "three_wide": 0.0, "top3_hit": 0.0, "top3_case": 0.0, "observed": 0.0, "count": 0.0}
    for row in rows:
        for key in totals:
            totals[key] += row[key]
    return totals


def rate(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def bootstrap_difference(
    hybrid: list[tuple[float, float]],
    baseline: list[tuple[float, float]],
    *,
    draws: int = 1000,
    seed: int = 20261116,
) -> tuple[float | None, float | None, float | None]:
    """Query bootstrap of hybrid coverage minus baseline coverage. Lists are aligned by query."""
    if len(hybrid) != len(baseline) or not hybrid:
        return None, None, None
    rng = random.Random(seed)
    stats: list[float] = []
    size = len(hybrid)
    for _ in range(draws):
        h_num = h_den = b_num = b_den = 0.0
        for _draw in range(size):
            index = rng.randrange(size)
            h_num += hybrid[index][0]
            h_den += hybrid[index][1]
            b_num += baseline[index][0]
            b_den += baseline[index][1]
        if h_den == 0 or b_den == 0:
            continue
        stats.append(h_num / h_den - b_num / b_den)
    if not stats:
        return None, None, None
    stats.sort()
    point = rate(sum(item[0] for item in hybrid), sum(item[1] for item in hybrid)) - rate(
        sum(item[0] for item in baseline), sum(item[1] for item in baseline)
    )
    return point, stats[int((len(stats) - 1) * 0.025)], stats[int((len(stats) - 1) * 0.975)]
