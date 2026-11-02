"""Okapi BM25 over an in-memory corpus.

The simulator ranks the pages in scope for a question (the submitted site
plus that question's competitor pages). That set is small, so BM25 runs in
process. The same scores are used on SQLite and Postgres.
"""

from __future__ import annotations

import math

from aeo.textutil import tokenize_for_search

K1 = 1.5
B = 0.75


def bm25_scores(
    query: str,
    documents: list[str],
    *,
    k1: float = K1,
    b: float = B,
) -> list[float]:
    """Return one Okapi BM25 score per document, aligned with ``documents``."""
    query_terms = tokenize_for_search(query)
    tokenized = [tokenize_for_search(document) for document in documents]
    scores = [0.0] * len(documents)
    if not query_terms or not documents:
        return scores

    lengths = [len(tokens) for tokens in tokenized]
    avgdl = sum(lengths) / len(lengths)
    if avgdl == 0:
        return scores

    n_docs = len(documents)
    df: dict[str, int] = {}
    for tokens in tokenized:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1

    for index, tokens in enumerate(tokenized):
        if not tokens:
            continue
        tf: dict[str, int] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        dl = lengths[index]
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            doc_freq = df.get(term, 0)
            idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
            denom = freq + k1 * (1.0 - b + b * dl / avgdl)
            score += idf * (freq * (k1 + 1.0)) / denom
        scores[index] = score
    return scores


def bm25_rank(query: str, doc_ids: list[str], documents: list[str]) -> list[str]:
    """Document ids with a positive BM25 score, best first.

    Ties break on the original order, then on id, so ranking is stable.
    Documents that share no query terms are omitted and receive no fusion credit.
    """
    if len(doc_ids) != len(documents):
        raise ValueError("doc_ids and documents must be the same length")
    scores = bm25_scores(query, documents)
    order = sorted(
        range(len(doc_ids)),
        key=lambda index: (-scores[index], index, doc_ids[index]),
    )
    return [doc_ids[index] for index in order if scores[index] > 0]
