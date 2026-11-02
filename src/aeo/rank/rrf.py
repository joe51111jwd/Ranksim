"""Reciprocal rank fusion."""

from __future__ import annotations


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Score is the sum of ``1 / (k + rank)`` with 1-based ranks.

    Ids are de-duplicated inside each list (first occurrence wins). Ties break
    on id so the result does not depend on dict order.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        rank = 0
        for doc_id in ranking:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            rank += 1
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
