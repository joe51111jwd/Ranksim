"""On-page features for the rank simulator. Missing authority stays unset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from aeo.rank.bm25 import bm25_scores
from aeo.rank.vectors import cosine, hash_embed
from aeo.textutil import salient_terms

FEATURE_NAMES = (
    "bm25_title",
    "bm25_h1",
    "bm25_headings",
    "bm25_body",
    "bm25_url",
    "embedding",
    "intent_match",
    "freshness",
    "topic_coverage",
    "has_table",
    "has_faq",
    "word_count",
    "links",
    "speed",
    "authority",
)


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
    is_ours: bool = False


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
    query_vector = hash_embed(query)
    rows: list[dict[str, float]] = []
    for index, page in enumerate(pages):
        page_vector = hash_embed(f"{page.title} {page.h1} {page.body[:2000]}")
        try:
            similarity = cosine(query_vector, page_vector)
        except ValueError:
            similarity = 0.0
        authority = 0.0 if page.authority is None else float(page.authority)
        rows.append(
            {
                "bm25_title": title_scores[index],
                "bm25_h1": h1_scores[index],
                "bm25_headings": heading_scores[index],
                "bm25_body": body_scores[index],
                "bm25_url": url_scores[index],
                "embedding": similarity,
                "intent_match": intent_match(intent, page.url, page.title),
                "freshness": freshness_score(page.updated, today),
                "topic_coverage": topic_coverage(query, page, pages),
                "has_table": 1.0 if page.table_count else 0.0,
                "has_faq": 1.0 if page.has_faq else 0.0,
                "word_count": word_count_score(page.word_count),
                "links": float(page.link_count),
                "speed": speed_score(page.fetch_ms),
                "authority": authority,
            }
        )
    return rows


def normalize_rows(rows: list[dict[str, float]]) -> list[dict[str, float]]:
    if not rows:
        return []
    spans = {}
    for name in FEATURE_NAMES:
        values = [row[name] for row in rows]
        spans[name] = (min(values), max(values))
    normalized: list[dict[str, float]] = []
    for row in rows:
        scaled = {}
        for name in FEATURE_NAMES:
            low, high = spans[name]
            if high == low:
                scaled[name] = 0.0
            else:
                scaled[name] = (row[name] - low) / (high - low)
        normalized.append(scaled)
    return normalized
