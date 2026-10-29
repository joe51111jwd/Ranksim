"""Hybrid retrieval: BM25 and cosine, fused with reciprocal rank fusion."""

from __future__ import annotations

from dataclasses import dataclass

from aeo.rank.bm25 import bm25_rank
from aeo.rank.rrf import reciprocal_rank_fusion
from aeo.rank.vectors import cosine


@dataclass(frozen=True)
class RankedDoc:
    id: str
    text: str
    embedding: list[float]
    ordinal: int = 0


class QueryEmbedder:
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


# Hashing embeddings collide. Ignore weak cosine matches so shared noise
# does not retrieve a page that does not actually discuss the query.
MIN_COSINE = 0.2


def vector_rank(query_vector: list[float], docs: list[RankedDoc]) -> list[str]:
    scored: list[tuple[float, int, str]] = []
    for doc in docs:
        if len(doc.embedding) != len(query_vector):
            continue
        score = cosine(query_vector, doc.embedding)
        if score >= MIN_COSINE:
            scored.append((score, doc.ordinal, doc.id))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [doc_id for _, _, doc_id in scored]


def hybrid_retrieve(
    query: str,
    docs: list[RankedDoc],
    embedder: QueryEmbedder,
    *,
    rrf_k: int = 60,
) -> list[tuple[str, float]]:
    """Rank ``docs`` for one query. Returns ``(id, rrf_score)`` best first."""
    lexical = bm25_rank(query, [doc.id for doc in docs], [doc.text for doc in docs])
    vector = vector_rank(embedder.embed(query), docs)
    return reciprocal_rank_fusion([lexical, vector], k=rrf_k)


def fuse_subqueries(
    per_query_rankings: list[list[str]],
    *,
    rrf_k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse the hybrid ranking of each sub-query into one passage list."""
    usable = [ranking for ranking in per_query_rankings if ranking]
    if not usable:
        return []
    return reciprocal_rank_fusion(usable, k=rrf_k)
