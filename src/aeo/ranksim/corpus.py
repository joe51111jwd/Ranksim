"""Load crawled pages and attach link-graph features from stored HTML."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.graph import domain_pagerank, pagerank
from aeo.ranksim.loop import page_from_row
from aeo.ranksim.store import RsPage


def load_corpus_docs(session: Session, urls: Iterable[str] | None = None) -> dict[str, object]:
    """PageDoc map keyed by stored URL and by normalized URL.

    With urls, only stored rows whose URL (or normalized URL) is in that set are
    loaded, so the link features are computed on that page set alone. A project
    uses this so pages stored for another project do not move its numbers.
    """
    rows = session.query(RsPage).filter(RsPage.status == "fetched").all()
    if urls is not None:
        wanted = set()
        for url in urls:
            wanted.add(url)
            try:
                wanted.add(normalize_url(url))
            except ValueError:
                continue
        rows = [row for row in rows if row.url in wanted or _normal_or_none(row.url) in wanted]
    docs = {row.url: page_from_row(row) for row in rows}
    html_by_url = {row.url: row.raw_html or "" for row in rows}
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
        doc.authority = doc.pagerank
        try:
            key = normalize_url(url)
        except ValueError:
            continue
        docs[key] = doc
    return docs


def _normal_or_none(url: str) -> str | None:
    try:
        return normalize_url(url)
    except ValueError:
        return None


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
