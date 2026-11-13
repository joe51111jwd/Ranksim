"""On-page features for the rank simulator. Missing authority stays unset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aeo.rank.bm25 import bm25_scores
from aeo.rank.vectors import cosine, hash_embed, l2_normalize
from aeo.ranksim.bm25f import bm25f_scores
from aeo.textutil import salient_terms, tokenize_for_search

FEATURE_NAMES = (
    "bm25f",
    "bm25_title",
    "bm25_h1",
    "bm25_headings",
    "bm25_body",
    "has_headings",
    "has_body",
    "bm25_url",
    "bm25_anchor",
    "embedding",
    "site_focus",
    "site_radius",
    "pagerank",
    "domain_rank",
    "homepage_authority",
    "intent_match",
    "freshness",
    "topic_coverage",
    "has_table",
    "has_faq",
    "word_count",
    "content_effort",
    "originality",
    "thin_content",
    "readability",
    "ad_density",
    "links",
    "speed",
    "authority",
    "url_exact",
    "navboost_prior",
    "orphan",
    "link_depth",
    "crawl_inlinks",
    "content_missing",
)

# Crawl timing and link-budget fields describe this crawl, not the page. The audit
# confidence check trains without them. The product ranker still uses FEATURE_NAMES.
CRAWL_ARTIFACT_FEATURES = frozenset({"speed", "orphan", "link_depth", "crawl_inlinks"})
MODEL_FEATURES = tuple(name for name in FEATURE_NAMES if name not in CRAWL_ARTIFACT_FEATURES)

# Higher values should raise the predicted score. Other features stay unconstrained.
MONOTONE_UP = frozenset(
    {
        "bm25f",
        "bm25_title",
        "bm25_h1",
        "bm25_headings",
        "bm25_body",
        "topic_coverage",
    }
)
THIN_WORD_GATE = 150


@dataclass
class PageDoc:
    url: str
    title: str = ""
    h1: str = ""
    headings: str = ""
    body: str = ""
    word_count: int = 0
    table_count: int = 0
    has_faq: bool = False
    link_count: int = 0
    fetch_ms: float | None = None
    updated: str | None = None
    authority: float | None = None
    anchors: str = ""
    pagerank: float = 0.0
    domain_rank: float = 0.0
    homepage_authority: float = 0.0
    ad_hits: int = 0
    navboost_prior: float = 0.0
    is_ours: bool = False
    # None means the link crawl did not observe this page. That is missing, not zero.
    orphan: bool | None = None
    link_depth: int | None = None
    crawl_inlinks: int | None = None
    # True when this row has no page text. Lexical features are then unknown, not a low score.
    content_missing: bool = False


def url_text(url: str) -> str:
    return url.replace("https://", " ").replace("http://", " ").replace("/", " ").replace("-", " ").replace(".", " ")


def freshness_score(updated: str | None, today: date) -> float:
    if not updated:
        return 0.0
    try:
        stamp = datetime.fromisoformat(updated[:10]).date()
    except ValueError:
        return 0.0
    age = max(0, (today - stamp).days)
    return 1.0 / (1.0 + age / 180.0)


def word_count_score(count: int) -> float:
    if count < 150:
        return 0.4 * (count / 150) if count > 0 else 0.0
    if count < 800:
        return 0.4 + 0.6 * ((count - 150) / 650)
    if count <= 2500:
        return 1.0
    return max(0.3, 1.0 - (count - 2500) / 5000)


def speed_score(fetch_ms: float | None) -> float:
    if fetch_ms is None:
        return 0.0
    return 1.0 / (1.0 + max(fetch_ms, 0.0) / 800.0)


def intent_match(intent: str, url: str, title: str) -> float:
    haystack = f"{url} {title}".lower()
    if intent == "comparison":
        markers = ("/compare/", "alternative", " vs ", "versus")
        return 1.0 if any(marker in haystack for marker in markers) else 0.0
    if intent == "transactional":
        return 1.0 if any(marker in haystack for marker in ("pricing", "demo", "/faq")) else 0.0
    if intent == "informational":
        if "/blog/" in haystack or "/guides/" in haystack:
            return 1.0
        if "/features/" in haystack:
            return 0.3
        return 0.0
    if any(marker in haystack for marker in ("/industries/", "/dirtwork", "/features/", "/blog/best", "software")):
        return 1.0
    return 0.4


def topic_coverage(query: str, page: PageDoc, peers: list[PageDoc]) -> float:
    terms = salient_terms(query)
    peer_text = " ".join(f"{peer.title} {peer.h1}" for peer in peers if peer.url != page.url)
    common = [term for term in salient_terms(peer_text) if peer_text.lower().count(term) >= 2]
    for term in common:
        if term not in terms:
            terms.append(term)
    if not terms:
        return 0.0
    page_terms = set(salient_terms(f"{page.title} {page.h1} {page.headings} {page.body[:4000]}"))
    return sum(1 for term in terms if term in page_terms) / len(terms)


def _trigrams(text: str) -> set[tuple[str, str, str]]:
    tokens = tokenize_for_search(text)[:400]
    return {tuple(tokens[index : index + 3]) for index in range(len(tokens) - 2)}  # type: ignore[misc]


def _originality(pages: list[PageDoc]) -> list[float]:
    grams = [_trigrams(f"{page.title} {page.h1} {page.body[:2500]}") for page in pages]
    scores: list[float] = []
    for index, own in enumerate(grams):
        if not own:
            scores.append(0.0)
            continue
        others: set[tuple[str, str, str]] = set()
        for other_index, other in enumerate(grams):
            if other_index != index:
                others.update(other)
        scores.append(sum(1 for gram in own if gram not in others) / len(own))
    return scores


def _site_focus(pages: list[PageDoc]) -> tuple[list[float], list[float]]:
    from aeo.crawl.urls import domain_of

    vectors = [hash_embed(f"{page.title} {page.h1} {page.body[:800]}") for page in pages]
    domains = [domain_of(page.url) for page in pages]
    focus = [0.0] * len(pages)
    radius = [0.0] * len(pages)
    grouped: dict[str, list[int]] = {}
    for index, domain in enumerate(domains):
        grouped.setdefault(domain or "", []).append(index)
    for indexes in grouped.values():
        if len(indexes) < 2:
            for index in indexes:
                focus[index] = 1.0
                radius[index] = 0.0
            continue
        width = len(vectors[indexes[0]])
        centroid = [0.0] * width
        for index in indexes:
            for dim, value in enumerate(vectors[index]):
                centroid[dim] += value
        centroid = l2_normalize([value / len(indexes) for value in centroid])
        for index in indexes:
            similarity = cosine(vectors[index], centroid)
            focus[index] = similarity
            radius[index] = 1.0 - similarity
    return focus, radius


def _readability(text: str) -> float:
    chunks = [part.strip() for part in text.replace("!", ".").replace("?", ".").split(".") if part.strip()]
    if not chunks:
        return 0.0
    lengths = [len(part.split()) for part in chunks]
    average = sum(lengths) / len(lengths)
    return 1.0 / (1.0 + abs(average - 18.0) / 18.0)


def _content_effort(page: PageDoc) -> float:
    tokens = tokenize_for_search(page.body)
    if not tokens:
        return 0.0
    unique_ratio = len(set(tokens)) / len(tokens)
    return unique_ratio * word_count_score(page.word_count)


def _observed_flag(value: bool | None) -> float:
    if value is None:
        return float("nan")
    return 1.0 if value else 0.0


def _match_or_missing(score: float, text: str) -> float:
    """A zero or blank heading/body match is missing, not a real zero.

    LightGBM then keeps that split out of the numeric threshold that was
    treating 'no heading text' as a good score.
    """
    if not text or not str(text).strip():
        return float("nan")
    if score <= 0.0:
        return float("nan")
    return float(score)


def raw_features(query: str, intent: str, pages: list[PageDoc], today: date) -> list[dict[str, float]]:
    titles = [page.title for page in pages]
    h1s = [page.h1 for page in pages]
    headings = [page.headings for page in pages]
    bodies = [page.body[:8000] for page in pages]
    urls = [url_text(page.url) for page in pages]
    title_scores = bm25_scores(query, titles)
    h1_scores = bm25_scores(query, h1s)
    heading_scores = bm25_scores(query, headings)
    body_scores = bm25_scores(query, bodies)
    url_scores = bm25_scores(query, urls)
    anchors = [page.anchors for page in pages]
    anchor_scores = bm25_scores(query, anchors)
    fielded = bm25f_scores(
        query,
        [
            {
                "title": page.title,
                "h1": page.h1,
                "headings": page.headings,
                "url": url_text(page.url),
                "anchor": page.anchors,
                "body": page.body[:8000],
            }
            for page in pages
        ],
    )
    query_vector = hash_embed(query)
    query_terms = salient_terms(query)
    originality = _originality(pages)
    focus, radius = _site_focus(pages)
    rows: list[dict[str, float]] = []
    for index, page in enumerate(pages):
        page_vector = hash_embed(f"{page.title} {page.h1} {page.body[:2000]}")
        try:
            similarity = cosine(query_vector, page_vector)
        except ValueError:
            similarity = 0.0
        authority = 0.0 if page.authority is None else float(page.authority)
        url_terms = set(tokenize_for_search(url_text(page.url)))
        exact = 0.0 if not query_terms else sum(1 for term in query_terms if term in url_terms) / len(query_terms)
        rows.append(
            {
                "bm25f": fielded[index],
                "bm25_title": title_scores[index],
                "bm25_h1": _match_or_missing(h1_scores[index], page.h1),
                "bm25_headings": _match_or_missing(heading_scores[index], page.headings or page.h1),
                "bm25_body": _match_or_missing(body_scores[index], page.body),
                "has_headings": 1.0 if (page.headings or page.h1 or "").strip() else 0.0,
                "has_body": 1.0 if (page.body or "").strip() else 0.0,
                "bm25_url": url_scores[index],
                "bm25_anchor": anchor_scores[index],
                "embedding": similarity,
                "site_focus": focus[index],
                "site_radius": radius[index],
                "pagerank": page.pagerank,
                "domain_rank": page.domain_rank,
                "homepage_authority": page.homepage_authority,
                "intent_match": intent_match(intent, page.url, page.title),
                "freshness": freshness_score(page.updated, today),
                "topic_coverage": topic_coverage(query, page, pages),
                "has_table": 1.0 if page.table_count else 0.0,
                "has_faq": 1.0 if page.has_faq else 0.0,
                "word_count": word_count_score(page.word_count),
                "content_effort": _content_effort(page),
                "originality": originality[index],
                "thin_content": 1.0 if page.word_count < THIN_WORD_GATE else 0.0,
                "readability": _readability(page.body),
                "ad_density": float(page.ad_hits),
                "links": float(page.link_count),
                "speed": speed_score(page.fetch_ms),
                "authority": authority,
                "url_exact": exact,
                "navboost_prior": page.navboost_prior,
                "orphan": _observed_flag(page.orphan),
                "link_depth": float("nan") if page.link_depth is None else float(page.link_depth),
                "crawl_inlinks": float("nan") if page.crawl_inlinks is None else float(page.crawl_inlinks),
                "content_missing": 1.0 if page.content_missing else 0.0,
            }
        )
    return rows


def normalize_rows(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    if not rows:
        return []
    spans = {}
    for name in FEATURE_NAMES:
        values = [row[name] for row in rows if row[name] == row[name]]
        if not values:
            spans[name] = (0.0, 0.0)
        else:
            spans[name] = (min(values), max(values))
    normalized: list[dict[str, float]] = []
    for row in rows:
        scaled = {}
        for name in FEATURE_NAMES:
            value = row[name]
            if value != value:
                scaled[name] = 0.0
                continue
            low, high = spans[name]
            if high == low:
                scaled[name] = 0.0
            else:
                scaled[name] = (value - low) / (high - low)
        normalized.append(scaled)
    return normalized
