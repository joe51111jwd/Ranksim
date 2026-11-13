"""Corpus signals computed from the pages we actually stored."""

from __future__ import annotations

from collections import defaultdict

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.graph import domain_pagerank, pagerank
from aeo.ranksim.localindex.records import PageRecord


def apply_signals(pages: dict[str, PageRecord]) -> list[dict]:
    """In-corpus edges, anchor text, PageRank, and per-domain stats.

    Links that leave the corpus are kept on the page as outlinks and are not
    graph edges. PageRank here is the corpus graph, not web-wide PageRank.
    """
    known = set(pages)
    normalized = {}
    for url in known:
        try:
            normalized[normalize_url(url)] = url
        except ValueError:
            continue
    edges: list[tuple[str, str, str]] = []
    anchors: dict[str, list[str]] = defaultdict(list)
    inlinks: dict[str, list[str]] = defaultdict(list)
    for source, page in pages.items():
        for href, text in page.outlinks:
            target = _match(href, known, normalized)
            if not target or target == source:
                continue
            edges.append((source, target, text))
            inlinks[target].append(source)
            if text:
                anchors[target].append(text)
    ranks = pagerank([(src, dst) for src, dst, _anchor in edges])
    domains = domain_pagerank([(src, dst) for src, dst, _anchor in edges])
    for url, page in pages.items():
        host = domain_of(url)
        page.pagerank = float(ranks.get(url, 0.0))
        page.domain_rank = float(domains.get(host, 0.0))
        page.anchor_text = " ".join(anchors.get(url, []))
        page.inlinks = inlinks.get(url, [])
    stats: dict[str, dict] = {}
    for url, page in pages.items():
        host = domain_of(url) or ""
        row = stats.setdefault(
            host,
            {"domain": host, "pages": 0, "full_text": 0, "outlinks": 0, "inlinks": 0, "mean_pagerank": 0.0, "_rank": 0.0},
        )
        row["pages"] += 1
        if page.status == "full":
            row["full_text"] += 1
        row["outlinks"] += sum(1 for src, _dst, _anchor in edges if src == url)
        row["inlinks"] += len(page.inlinks)
        row["_rank"] += page.pagerank
    result = []
    for row in stats.values():
        count = row["pages"] or 1
        result.append(
            {
                "domain": row["domain"],
                "pages": row["pages"],
                "full_text": row["full_text"],
                "outlinks": row["outlinks"],
                "inlinks": row["inlinks"],
                "mean_pagerank": row["_rank"] / count,
            }
        )
    result.sort(key=lambda item: item["domain"])
    return result


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
