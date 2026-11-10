"""Load crawled pages and attach link-graph features from stored HTML."""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.graph import domain_pagerank, pagerank
from aeo.ranksim.loop import page_from_row
from aeo.ranksim.store import EVALUATION_EXCLUDED_SOURCES, RsPage


def load_corpus_docs(session: Session) -> dict[str, object]:
    """PageDoc map keyed by stored URL and by normalized URL. A normalized key never replaces the
    page stored at exactly that URL, so a draft stored at its slash twin does not take its place."""
    rows = session.query(RsPage).filter(RsPage.status == "fetched").all()
    docs = {row.url: page_from_row(row) for row in rows}
    # Draft HTML is not on the live web, so its outlinks do not feed PageRank or anchor text.
    html_by_url = {
        row.url: row.raw_html or "" for row in rows if (row.source or "unknown") not in EVALUATION_EXCLUDED_SOURCES
    }
    known = set(docs)
    normalized = {}
    for url in list(known):
        try:
            normalized[normalize_url(url)] = url
        except ValueError:
            continue
    edges: list[tuple[str, str]] = []
    anchors: dict[str, list[str]] = defaultdict(list)
    for source, html in html_by_url.items():
        if not html:
            continue
        for href, text in _anchors(html, source):
            target = _match(href, known, normalized)
            if not target or target == source:
                continue
            edges.append((source, target))
            if text:
                anchors[target].append(text)
    ranks = pagerank(edges)
    domains = domain_pagerank(edges)
    home_rank: dict[str, float] = {}
    for url, score in ranks.items():
        try:
            path = normalize_url(url)
        except ValueError:
            continue
        host = domain_of(url)
        if host and (path.rstrip("/").count("/") == 2 or path.endswith("://") or url.rstrip("/").endswith(host)):
            # homepage-ish: no extra path
            from urllib.parse import urlsplit

            if (urlsplit(url).path or "/") in {"", "/"}:
                home_rank[host] = max(home_rank.get(host, 0.0), score)
    for url, doc in list(docs.items()):
        host = domain_of(url)
        doc.pagerank = float(ranks.get(url, 0.0))
        doc.domain_rank = float(domains.get(host, 0.0))
        doc.homepage_authority = float(home_rank.get(host, 0.0))
        doc.anchors = " ".join(anchors.get(url, []))
        # authority stays whatever the store holds (a backlink provider's value, or None).
        # It used to be overwritten with in-corpus PageRank, which duplicated `pagerank`.
        try:
            key = normalize_url(url)
        except ValueError:
            continue
        if key not in known:  # a page stored at exactly that URL keeps its own key
            docs[key] = doc
    return docs


def _anchors(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    found: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base, href).split("#")[0]
        if not absolute.startswith("http"):
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        found.append((absolute, text[:180]))
    return found


def _match(url: str, known: set[str], normalized: dict[str, str]) -> str | None:
    if url in known:
        return url
    try:
        key = normalize_url(url)
    except ValueError:
        return None
    if key in known:
        return key
    return normalized.get(key)
