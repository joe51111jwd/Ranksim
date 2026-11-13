"""Page and candidate records stored in the local index."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from aeo.crawl.extract import extract_html
from aeo.crawl.layered import _schema_types, is_challenge, usable_text
from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.linkgraph import extract_anchors


def canon_url(url: str) -> str | None:
    text = (url or "").strip()
    if not text:
        return None
    try:
        return normalize_url(text)
    except ValueError:
        return None


def rank_band(position: int | None) -> str:
    if position is None or position <= 0:
        return "unlabeled"
    if position <= 10:
        return "1-10"
    if position <= 20:
        return "11-20"
    if position <= 50:
        return "21-50"
    if position <= 100:
        return "51-100"
    return "100+"


def content_hash(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Attempt:
    method: str
    ok: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"method": self.method, "ok": self.ok, "reason": self.reason}


@dataclass
class PageRecord:
    url: str
    final_url: str = ""
    canonical: str = ""
    title: str = ""
    meta_description: str = ""
    headings: list[str] = field(default_factory=list)
    main_text: str = ""
    word_count: int = 0
    schema_types: list[str] = field(default_factory=list)
    outlinks: list[tuple[str, str]] = field(default_factory=list)
    content_hash: str = ""
    published: str | None = None
    updated: str | None = None
    source: str = ""
    captured_on: str = ""
    status: str = "missing"
    miss_reason: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    pagerank: float = 0.0
    domain_rank: float = 0.0
    domain: str = ""
    anchor_text: str = ""
    inlinks: list[str] = field(default_factory=list)
    table_count: int = 0

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = domain_of(self.final_url or self.url)


@dataclass(frozen=True)
class Candidate:
    url: str
    query: str
    position: int | None
    source: str
    snapshot_id: str = ""

    @property
    def band(self) -> str:
        return rank_band(self.position)


def page_from_html(
    url: str,
    html: str,
    *,
    source: str,
    captured_on: str,
    final_url: str = "",
    attempts: list[Attempt] | None = None,
    miss_reason: str = "",
) -> PageRecord:
    """Turn HTML into a page record. Thin, empty, and challenge pages stay missing."""
    final = final_url or url
    record = PageRecord(
        url=url,
        final_url=final,
        source=source,
        captured_on=captured_on[:10] if captured_on else "",
        attempts=list(attempts or []),
        domain=domain_of(final or url),
    )
    if not html or not html.strip():
        record.status = "missing"
        record.miss_reason = miss_reason or "empty"
        return record
    if is_challenge(html, ""):
        record.status = "missing"
        record.miss_reason = "challenge_page"
        return record
    extracted = extract_html(html, final or url)
    text = extracted.main_text or ""
    ok, reason = usable_text(html, text, extracted.title)
    record.title = extracted.title
    record.meta_description = extracted.meta_description
    record.canonical = extracted.canonical or final or url
    record.headings = [str(item.get("text") or "") for item in extracted.headings if str(item.get("text") or "").strip()]
    record.schema_types = _schema_types(extracted.schema_objects)
    record.published = extracted.published
    record.updated = extracted.updated
    record.outlinks = [(href, anchor) for href, anchor in extract_anchors(html, final or url) if href != url]
    record.table_count = html.lower().count("<table")
    record.main_text = text
    record.word_count = len(text.split()) if text else 0
    if ok:
        record.status = "full"
        record.miss_reason = ""
        record.content_hash = content_hash(text)
    else:
        record.status = "missing"
        record.miss_reason = miss_reason or reason or "thin_or_empty"
        record.main_text = ""
        record.word_count = 0
    return record


def attempts_json(attempts: list[Attempt]) -> str:
    return json.dumps([item.as_dict() for item in attempts], ensure_ascii=False)


def outlinks_json(links: list[tuple[str, str]]) -> str:
    return json.dumps([{"url": url, "anchor": anchor} for url, anchor in links], ensure_ascii=False)
