"""Extract the fields answer engines actually read from raw HTML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from aeo.textutil import word_count

_LD_JSON = re.compile(r"ld\+json", re.I)
_JUNK_TAGS = ("script", "style", "noscript", "nav", "footer", "form", "svg", "iframe")
_DATE_PUBLISHED = {"article:published_time", "date", "pubdate"}
_DATE_UPDATED = {"article:modified_time", "og:updated_time"}


@dataclass
class ExtractedPage:
    title: str = ""
    meta_description: str = ""
    canonical: str = ""
    h1: str = ""
    headings: list[dict[str, object]] = field(default_factory=list)
    schema_objects: list[object] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    published: str | None = None
    updated: str | None = None
    main_text: str = ""
    sections: list[tuple[str, str]] = field(default_factory=list)
    word_count: int = 0


def _rel_is(tag, expected: str) -> bool:
    rel = tag.get("rel")
    if not rel:
        return False
    if isinstance(rel, list):
        return expected in [item.lower() for item in rel]
    return str(rel).lower() == expected


def _walk_dates(node: object, found: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if lowered in {"datepublished", "datecreated"} and isinstance(value, str):
                found.setdefault("published", value.strip())
            elif lowered in {"datemodified", "uploaddate"} and isinstance(value, str):
                found.setdefault("updated", value.strip())
            else:
                _walk_dates(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_dates(item, found)


def _sections_from(root) -> list[tuple[str, str]]:
    stack: list[str] = []
    current = ""
    buffer: list[str] = []
    sections: list[tuple[str, str]] = []

    def flush() -> None:
        body = "\n".join(part for part in buffer if part).strip()
        if current and body:
            text = current + "\n" + body
        else:
            text = body or current
        if text:
            sections.append((current, text))

    for element in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "pre"]):
        if element.find_parent(["p", "li", "blockquote", "pre"]) is not None and element.name not in {
            "h1",
            "h2",
            "h3",
        }:
            continue
        if element.name in {"h1", "h2", "h3"}:
            flush()
            buffer = []
            level = int(element.name[1])
            title = element.get_text(" ", strip=True)
            stack = stack[: level - 1]
            while len(stack) < level - 1:
                stack.append("")
            if len(stack) == level - 1:
                stack.append(title)
            else:
                stack[level - 1] = title
            current = " > ".join(part for part in stack if part)
        else:
            text = element.get_text(" ", strip=True)
            if text:
                buffer.append(text)
    flush()
    if sections:
        return sections
    fallback = root.get_text("\n", strip=True)
    if fallback:
        return [("", fallback)]
    return []


def extract_html(html: str, base_url: str) -> ExtractedPage:
    soup = BeautifulSoup(html or "", "lxml")
    extracted = ExtractedPage()
    if soup.title:
        extracted.title = soup.title.get_text(" ", strip=True)

    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").strip().lower()
        content = (tag.get("content") or "").strip()
        if not content:
            continue
        if name in {"description", "og:description"} and not extracted.meta_description:
            extracted.meta_description = content
        if name in _DATE_PUBLISHED and not extracted.published:
            extracted.published = content
        if name in _DATE_UPDATED and not extracted.updated:
            extracted.updated = content

    for link in soup.find_all("link"):
        if _rel_is(link, "canonical") and link.get("href"):
            extracted.canonical = urljoin(base_url, link["href"]).split("#")[0]
            break

    headings: list[dict[str, object]] = []
    for level in range(1, 4):
        for heading in soup.find_all(f"h{level}"):
            text = heading.get_text(" ", strip=True)
            if not text:
                continue
            headings.append({"level": level, "text": text})
            if level == 1 and not extracted.h1:
                extracted.h1 = text
    extracted.headings = headings

    dates: dict[str, str] = {}
    schemas: list[object] = []
    for script in soup.find_all("script"):
        script_type = script.get("type") or ""
        if not _LD_JSON.search(str(script_type)):
            continue
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        schemas.append(data)
        _walk_dates(data, dates)
    extracted.schema_objects = schemas
    extracted.published = extracted.published or dates.get("published")
    extracted.updated = extracted.updated or dates.get("updated")

    time_tag = soup.find("time")
    if time_tag is not None and not extracted.published:
        extracted.published = (time_tag.get("datetime") or time_tag.get_text(strip=True) or "").strip() or None

    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    extracted.links = links

    for tag_name in _JUNK_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    extracted.sections = _sections_from(root)
    extracted.main_text = "\n\n".join(text for _, text in extracted.sections).strip()
    extracted.word_count = word_count(extracted.main_text)
    return extracted
