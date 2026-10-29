"""Run hybrid retrieval for every sub-query and fuse the lists."""

from __future__ import annotations

from dataclasses import dataclass

from aeo.rank.hybrid import RankedDoc, fuse_subqueries, hybrid_retrieve


@dataclass(frozen=True)
class ScoredPassage:
    id: str
    score: float
    rank: int


def retrieve_passages(
    subqueries: list[str],
    docs: list[RankedDoc],
    embedder,
    *,
    rrf_k: int = 60,
    per_query_limit: int = 8,
    final_limit: int = 8,
) -> tuple[list[ScoredPassage], list[dict]]:
    """Return fused passages and a per-sub-query trace."""
    trace: list[dict] = []
    rankings: list[list[str]] = []
    if not docs or not subqueries:
        return [], trace
    for subquery in subqueries:
        fused = hybrid_retrieve(subquery, docs, embedder, rrf_k=rrf_k)
        top = fused[:per_query_limit]
        rankings.append([doc_id for doc_id, _score in top])
        trace.append(
            {
                "query": subquery,
                "passages": [{"id": doc_id, "score": score} for doc_id, score in top],
            }
        )
    fused_all = fuse_subqueries(rankings, rrf_k=rrf_k)[:final_limit]
    passages = [
        ScoredPassage(id=doc_id, score=score, rank=index + 1) for index, (doc_id, score) in enumerate(fused_all)
    ]
    return passages, trace
