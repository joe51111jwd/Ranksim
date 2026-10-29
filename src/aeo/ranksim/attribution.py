"""Which features changed between two versions of a page.

Numbers are predicted score parts on our index, not Google's feature weights.
"""

from __future__ import annotations

from datetime import date

from aeo.ranksim.features import FEATURE_NAMES, PageDoc, raw_features
from aeo.ranksim.score import HAND_WEIGHTS

LABELS = {
    "bm25f": "BM25F",
    "bm25_title": "title match",
    "bm25_h1": "H1 match",
    "bm25_headings": "heading match",
    "bm25_body": "body match",
    "bm25_url": "URL match",
    "bm25_anchor": "anchor text",
    "embedding": "embedding similarity",
    "site_focus": "site focus",
    "site_radius": "site radius",
    "pagerank": "crawl PageRank",
    "domain_rank": "domain PageRank",
    "homepage_authority": "homepage authority",
    "intent_match": "intent match",
    "freshness": "freshness",
    "topic_coverage": "entity coverage",
    "entity_coverage": "entity coverage",
    "has_table": "table",
    "has_faq": "FAQ",
    "word_count": "word count",
    "content_effort": "content effort",
    "originality": "originality",
    "thin_content": "thin content",
    "readability": "readability",
    "ad_density": "ad density",
    "links": "link count",
    "speed": "fetch speed",
    "authority": "authority",
    "url_exact": "URL term match",
    "navboost_prior": "Navboost prior",
}


def feature_moves(
    query: str,
    intent: str,
    before: PageDoc,
    after: PageDoc,
    peers: list[PageDoc] | None = None,
    *,
    today: date | None = None,
    weights: dict[str, float] | None = None,
    limit: int = 6,
) -> list[str]:
    """Return the features that moved, largest weighted raw change first."""
    chosen = weights or HAND_WEIGHTS
    context = [before, after, *(peers or [])]
    raw = raw_features(query, intent, context, today or date.today())
    if len(raw) < 2:
        return []
    moves: list[tuple[float, str]] = []
    for name in FEATURE_NAMES:
        delta = raw[1][name] - raw[0][name]
        if abs(delta) < 1e-9:
            continue
        weight = chosen.get(name, 0.0)
        moves.append((abs(weight * delta), _line(name, raw[0][name], raw[1][name])))
    moves.sort(key=lambda item: item[0], reverse=True)
    lines = [line for _score, line in moves[:limit]]
    if not lines:
        return ["No scored feature changed."]
    return lines


def _line(name: str, before: float, after: float) -> str:
    label = LABELS.get(name, name)
    return f"{label} {before:.3f} -> {after:.3f}"
