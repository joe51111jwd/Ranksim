import math

from aeo.providers.embeddings import MockEmbedder
from aeo.rank.bm25 import bm25_scores
from aeo.rank.hybrid import RankedDoc, hybrid_retrieve
from aeo.rank.rrf import reciprocal_rank_fusion
from aeo.rank.vectors import cosine, hash_embed


def test_bm25_scores_a_matching_document_and_ignores_stopwords():
    scores = bm25_scores("the trail", ["trail mix", "office cat"])
    assert scores[1] == 0
    assert scores[0] == math.log(2)
    assert bm25_scores("the", ["trail mix"]) == [0]


def test_reciprocal_rank_fusion_order_and_scores():
    fused = reciprocal_rank_fusion([["d1", "d2", "d3"], ["d3", "d2", "d4"]], k=60)
    assert [doc_id for doc_id, _score in fused] == ["d3", "d2", "d1", "d4"]
    scores = dict(fused)
    assert scores["d1"] == 1 / 61
    assert scores["d2"] == 1 / 62 + 1 / 62
    assert scores["d3"] == 1 / 63 + 1 / 61
    assert scores["d4"] == 1 / 63


def test_rrf_dedupes_within_one_list():
    fused = reciprocal_rank_fusion([["a", "a", "b"]], k=60)
    assert [doc_id for doc_id, _score in fused] == ["a", "b"]
    assert dict(fused)["a"] == 1 / 61


def test_hybrid_prefers_the_relevant_passage():
    embedder = MockEmbedder(dim=64)
    docs = [
        RankedDoc("relevant", "best trail mix for long hikes with almonds and salt", embedder.embed("best trail mix for long hikes with almonds and salt"), 0),
        RankedDoc("other", "quarterly tax filing for a small bakery", embedder.embed("quarterly tax filing for a small bakery"), 1),
    ]
    ranked = hybrid_retrieve("best trail mix for long hikes", docs, embedder)
    assert ranked[0][0] == "relevant"
    assert "other" not in {doc_id for doc_id, _score in ranked} or ranked[0][0] == "relevant"


def test_hybrid_keeps_a_vector_only_hit():
    class Fixed:
        def embed(self, text: str) -> list[float]:
            return [0.3, 1.0]

    docs = [
        RankedDoc("lexical", "trail mix hike", [1.0, 0.0], 0),
        RankedDoc("vector", "bananas and porridge", [0.0, 1.0], 1),
    ]
    ranked = dict(hybrid_retrieve("trail mix hike", docs, Fixed()))
    assert "lexical" in ranked
    assert "vector" in ranked
    assert ranked["lexical"] > ranked["vector"]


def test_hash_embed_is_stable_and_similar_for_the_same_words():
    left = hash_embed("Trail mix for hikes")
    right = hash_embed("trail mix for hikes")
    assert left == right
    assert cosine(left, left) == 1
    unrelated = hash_embed("municipal bond yields")
    assert cosine(left, unrelated) < cosine(left, hash_embed("hikes trail mix almonds"))
