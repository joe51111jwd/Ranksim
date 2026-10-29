"""BM25F retrieval over title, URL, H1, headings, body, and anchor text.

Field term frequencies are weighted and length-normalized before BM25
saturation, which is the BM25F form in Robertson, Zaragoza, and Taylor
(CIKM 2004) and the TREC field-weighted variant.
"""

from __future__ import annotations

import math

from aeo.textutil import tokenize_for_search

# Conventional field boosts for stage-1 retrieval, not Google's weights.
FIELD_WEIGHTS = {
    "title": 5.0,
    "h1": 4.0,
    "headings": 2.0,
    "url": 3.0,
    "anchor": 4.0,
    "body": 1.0,
}
K1 = 1.5
B_FIELD = 0.75


def bm25f_scores(query: str, documents: list[dict[str, str]]) -> list[float]:
    """Score structured documents. Each document maps field name to text."""
    query_terms = tokenize_for_search(query)
    scores = [0.0] * len(documents)
    if not query_terms or not documents:
        return scores

    tokenized: list[dict[str, list[str]]] = []
    for document in documents:
        tokenized.append({name: tokenize_for_search(document.get(name) or "") for name in FIELD_WEIGHTS})

    averages: dict[str, float] = {}
    for name in FIELD_WEIGHTS:
        lengths = [len(fields[name]) for fields in tokenized]
        averages[name] = (sum(lengths) / len(lengths)) if lengths else 0.0

    doc_freq: dict[str, int] = {}
    for fields in tokenized:
        present = set()
        for tokens in fields.values():
            present.update(tokens)
        for term in present:
            doc_freq[term] = doc_freq.get(term, 0) + 1

    n_docs = len(documents)
    for index, fields in enumerate(tokenized):
        score = 0.0
        for term in query_terms:
            weighted_tf = 0.0
            for name, weight in FIELD_WEIGHTS.items():
                tokens = fields[name]
                if not tokens:
                    continue
                tf = tokens.count(term)
                if tf == 0:
                    continue
                average = averages[name] or 1.0
                norm = 1.0 + B_FIELD * ((len(tokens) / average) - 1.0)
                if norm <= 0:
                    norm = 1.0
                weighted_tf += weight * (tf / norm)
            if weighted_tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            score += idf * (weighted_tf * (K1 + 1.0)) / (K1 + weighted_tf)
        scores[index] = score
    return scores
