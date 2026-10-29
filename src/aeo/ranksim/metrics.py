"""Rank agreement metrics. Undefined correlations stay None instead of a fake zero."""

from __future__ import annotations

import math


def average_ranks(values: list[float]) -> list[float]:
    """1-based average ranks. Ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end) / 2 + 1
        for offset in range(cursor, end + 1):
            ranks[order[offset]] = average
        cursor = end + 1
    return ranks


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_scale = math.sqrt(sum((a - mean_left) ** 2 for a in left))
    right_scale = math.sqrt(sum((b - mean_right) ** 2 for b in right))
    if left_scale == 0 or right_scale == 0:
        return None
    return numerator / (left_scale * right_scale)


def spearman(predicted_scores: list[float], actual_positions: list[float]) -> float | None:
    """Agreement of predicted rank and actual rank. +1 is a perfect match.

    Higher scores are better. Lower positions are better. The correlation is
    on ranks, so the sign is positive when the model orders pages like the SERP.
    """
    if len(predicted_scores) != len(actual_positions):
        raise ValueError("scores and positions must be the same length")
    predicted_rank = average_ranks([-score for score in predicted_scores])
    actual_rank = average_ranks(list(actual_positions))
    return pearson(predicted_rank, actual_rank)


def _dcg(gains: list[float]) -> float:
    total = 0.0
    for index, gain in enumerate(gains):
        total += (2**gain - 1) / math.log2(index + 2)
    return total


def ndcg_at_k(ranked_ids: list[str], gains: dict[str, float], k: int = 10) -> float | None:
    if k < 1 or not gains:
        return None
    predicted = [gains.get(doc_id, 0.0) for doc_id in ranked_ids[:k]]
    ideal = sorted(gains.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    if ideal_dcg == 0:
        return None
    return _dcg(predicted) / ideal_dcg


def mean_metric(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)
