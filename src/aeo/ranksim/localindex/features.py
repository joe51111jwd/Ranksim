"""BM25F retrieval and rank features read from the local index.

The frozen full-coverage scorer is not used here and is not changed.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from aeo.crawl.urls import normalize_url
from aeo.ranksim.bm25f import bm25f_scores
from aeo.ranksim.features import PageDoc, raw_features
from aeo.ranksim.localindex.records import PageRecord
from aeo.ranksim.localindex.store import IndexStore


def default_index_dir() -> Path | None:
    """The index to read, when one is configured or already on disk."""
    chosen = os.environ.get("RANKSIM_INDEX_DIR", "").strip()
    path = Path(chosen) if chosen else Path("data/index")
    if (path / "index.sqlite").is_file():
        return path
    return None


def page_docs(index_dir: str | Path) -> dict[str, PageDoc]:
    store = IndexStore(index_dir)
    try:
        records = store.pages()
    finally:
        store.close()
    docs: dict[str, PageDoc] = {}
    for record in records.values():
        doc = to_page_doc(record)
        docs[record.url] = doc
        try:
            docs[normalize_url(record.url)] = doc
        except ValueError:
            continue
    return docs


def to_page_doc(record: PageRecord) -> PageDoc:
    headings = " ".join(record.headings)
    return PageDoc(
        url=record.url,
        title=record.title,
        h1=record.headings[0] if record.headings else "",
        headings=headings,
        body=record.main_text if record.status == "full" else "",
        word_count=record.word_count if record.status == "full" else 0,
        table_count=record.table_count,
        has_faq="FAQPage" in record.schema_types or "faq" in headings.lower(),
        link_count=len(record.outlinks),
        updated=record.updated or record.published,
        anchors=record.anchor_text,
        pagerank=record.pagerank,
        domain_rank=record.domain_rank,
        authority=record.pagerank,
        content_missing=record.status != "full",
    )


def retrieve_bm25f(query: str, index_dir: str | Path, *, limit: int | None = None) -> list[tuple[str, float]]:
    """Rank indexed pages by BM25F over title, URL, headings, body, and anchors."""
    docs = [doc for doc in _unique_docs(page_docs(index_dir)) if doc.body or doc.anchors or doc.title]
    if not docs:
        return []
    from aeo.ranksim.features import url_text

    scores = bm25f_scores(
        query,
        [
            {
                "title": doc.title,
                "h1": doc.h1,
                "headings": doc.headings,
                "url": url_text(doc.url),
                "anchor": doc.anchors,
                "body": doc.body,
            }
            for doc in docs
        ],
    )
    ranked = sorted(zip(docs, scores), key=lambda pair: (-pair[1], pair[0].url))
    if limit is not None:
        ranked = ranked[:limit]
    return [(doc.url, score) for doc, score in ranked]


def feature_rows(
    query: str,
    urls: list[str],
    index_dir: str | Path,
    *,
    intent: str = "commercial",
    today: date | None = None,
) -> list[dict[str, float]]:
    """Feature vectors for ``urls``, using index text and anchors."""
    docs = page_docs(index_dir)
    ordered: list[PageDoc] = []
    for url in urls:
        doc = docs.get(url)
        if doc is None:
            try:
                doc = docs.get(normalize_url(url))
            except ValueError:
                doc = None
        if doc is None:
            doc = PageDoc(url=url, content_missing=True)
        ordered.append(doc)
    return raw_features(query, intent, ordered, today or date.today())


def overlay_index(docs: dict, index_dir: str | Path, urls: set[str] | None = None) -> None:
    """Fill page docs from the index. SQL text is kept when it is longer."""
    indexed = page_docs(index_dir)
    if not indexed:
        return
    for key, doc in list(docs.items()):
        record = indexed.get(key) or indexed.get(getattr(doc, "url", ""))
        if record is None:
            continue
        _apply(doc, record)
    if urls is None:
        return
    for url in urls:
        if url in docs:
            continue
        record = indexed.get(url)
        if record is None:
            try:
                record = indexed.get(normalize_url(url))
            except ValueError:
                record = None
        if record is None:
            continue
        docs[url] = record
        try:
            docs[normalize_url(url)] = record
        except ValueError:
            continue


def _apply(doc, record: PageDoc) -> None:
    incoming = len((record.body or "").split())
    current = len((doc.body or "").split())
    if record.body and not record.content_missing and incoming >= current:
        doc.title = record.title
        doc.h1 = record.h1
        doc.headings = record.headings
        doc.body = record.body
        doc.word_count = record.word_count
        doc.table_count = record.table_count
        doc.has_faq = record.has_faq
        doc.content_missing = False
    elif not (doc.body or doc.title or "").strip():
        doc.content_missing = bool(record.content_missing)
    doc.anchors = record.anchors
    doc.pagerank = record.pagerank
    doc.domain_rank = record.domain_rank
    doc.authority = record.pagerank


def _unique_docs(docs: dict[str, PageDoc]) -> list[PageDoc]:
    seen: set[int] = set()
    ordered: list[PageDoc] = []
    for doc in docs.values():
        marker = id(doc)
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(doc)
    return ordered
