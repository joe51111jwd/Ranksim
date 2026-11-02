"""Turn HTML into stored pages and embedded passages."""

from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy.orm import Session

from aeo.crawl.chunk import ChunkDraft, chunk_sections
from aeo.crawl.extract import extract_html
from aeo.db import insert_chunk_index, replace_chunk_index
from aeo.models import Chunk, Page, utcnow


def index_html(
    session: Session,
    page: Page,
    html: str,
    embedder,
    *,
    rendered_html: str | None = None,
) -> None:
    raw = extract_html(html, page.url)
    rendered = extract_html(rendered_html, page.url) if rendered_html else None
    chosen = raw
    js_dependent = False
    if rendered is not None and rendered.word_count >= max(80, raw.word_count * 2) and raw.word_count < 40:
        js_dependent = True
        chosen = rendered
    elif rendered is not None and rendered.word_count > raw.word_count:
        chosen = rendered

    page.raw_html = html
    page.rendered_html = rendered_html
    page.title = raw.title or (rendered.title if rendered else None)
    page.meta_description = raw.meta_description or (rendered.meta_description if rendered else None)
    page.canonical = raw.canonical or (rendered.canonical if rendered else None)
    page.h1 = raw.h1 or (rendered.h1 if rendered else None)
    page.headings_json = raw.headings
    page.schema_json = raw.schema_objects
    page.links_json = raw.links[:100]
    page.published_label = raw.published
    page.updated_label = raw.updated
    page.main_text = chosen.main_text
    page.word_count = chosen.word_count
    page.raw_word_count = raw.word_count
    page.js_dependent = js_dependent
    page.content_hash = hashlib.sha256(chosen.main_text.encode()).hexdigest()
    page.status = "fetched"
    page.error = None
    page.fetched_at = utcnow()

    drafts = chunk_sections(chosen.sections)
    if not drafts and chosen.main_text.strip():
        drafts = [ChunkDraft("", " ".join(chosen.main_text.split())[:900])]
    vectors = embedder.embed_many([draft.text for draft in drafts]) if drafts else []
    _replace_chunks(session, page.id, drafts, vectors)


def _replace_chunks(
    session: Session,
    page_id: str,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
) -> None:
    old_ids = [row[0] for row in session.query(Chunk.id).filter(Chunk.page_id == page_id).all()]
    if old_ids:
        session.query(Chunk).filter(Chunk.id.in_(old_ids)).delete(synchronize_session=False)
        replace_chunk_index(session, old_ids)
    for ordinal, (draft, vector) in enumerate(zip(drafts, vectors)):
        chunk = Chunk(
            id=str(uuid4()),
            page_id=page_id,
            ordinal=ordinal,
            heading_path=draft.heading_path,
            body=draft.text,
            embedding_json=vector,
        )
        session.add(chunk)
        session.flush()
        insert_chunk_index(session, chunk_id=chunk.id, body=chunk.body, embedding=vector)
