"""Read the parts of a page that publishing touches: title, meta description, main heading and body region.

Everything here is pure: HTML in, plain values out. The connectors use it to turn "the edited page" into the few
platform fields that can really be changed, and the live check uses it to confirm those fields after a publish.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

FIELDS = ("title", "description", "h1", "body")
FIELD_LABELS = {"title": "Title tag", "description": "Meta description", "h1": "Main heading", "body": "Body content"}
# Elements whose loss in the edited copy means the export dropped something the live page relies on.
EMBED_TAGS = ("script", "iframe", "form", "video", "embed", "object")
_SPACE = re.compile(r"\s+")


def squash(text: str | None) -> str:
    return _SPACE.sub(" ", text or "").strip()


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def _text(node) -> str:
    if node is None:
        return ""
    for hidden in node.find_all(["script", "style", "noscript", "template"]):
        hidden.decompose()
    return squash(node.get_text(" "))


def text_of(fragment: str) -> str:
    """Visible text of an HTML fragment, spaces collapsed."""
    return _text(soup_of(fragment))


@dataclass
class PageParts:
    title: str = ""
    description: str = ""
    h1: str = ""
    region_selector: str = ""
    region_html: str = ""  # inner HTML of the content region, as serialized by BeautifulSoup
    region_text: str = ""
    body_text: str = ""
    embeds: dict | None = None  # tag -> count inside the region (or body when no region)

    def value(self, field: str) -> str:
        if field == "body":
            return self.region_text or self.body_text
        return getattr(self, field)


def parts(html: str, selectors: list[str] | tuple[str, ...] = ()) -> PageParts:
    soup = soup_of(html)
    title = squash(soup.title.get_text()) if soup.title else ""
    description = ""
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() == "description":
            description = squash(str(meta.get("content", "")))
            break
    h1_node = soup.find("h1")
    h1 = squash(h1_node.get_text(" ")) if h1_node else ""
    region = None
    chosen = ""
    for selector in selectors:
        try:
            region = soup.select_one(selector)
        except Exception:  # noqa: BLE001 - a bad selector in settings is skipped, not fatal
            region = None
        if region is not None:
            chosen = selector
            break
    scope = region if region is not None else soup.body
    embeds = {tag: len(scope.find_all(tag)) for tag in EMBED_TAGS} if scope is not None else {}
    region_html = region.decode_contents() if region is not None else ""
    region_text = _text(soup_of(region_html)) if region is not None else ""
    body_text = ""
    if soup.body is not None:
        body_copy = soup_of(soup.body.decode_contents())
        heading = body_copy.find("h1")
        if heading is not None:  # the heading is its own field, so a heading edit is not also a body edit
            heading.decompose()
        body_text = _text(body_copy)
    return PageParts(
        title=title,
        description=description,
        h1=h1,
        region_selector=chosen,
        region_html=region_html.strip(),
        region_text=region_text,
        body_text=body_text,
        embeds=embeds,
    )


def changes(before: PageParts, after: PageParts) -> list[dict]:
    """Field level differences between the live page and the edited page."""
    found: list[dict] = []
    for field in ("title", "description", "h1"):
        old, new = getattr(before, field), getattr(after, field)
        if old != new:
            found.append({"field": field, "label": FIELD_LABELS[field], "before": old, "after": new})
    if before.region_selector and after.region_selector:
        if before.region_html != after.region_html:
            found.append(
                {
                    "field": "body",
                    "label": FIELD_LABELS["body"],
                    "before": before.region_text,
                    "after": after.region_text,
                    "selector": after.region_selector,
                }
            )
    elif before.body_text != after.body_text:
        found.append({"field": "body", "label": FIELD_LABELS["body"], "before": before.body_text, "after": after.body_text, "selector": ""})
    return found


def lost_embeds(before: PageParts, after: PageParts) -> list[str]:
    """Embed tags the edited copy has fewer of than the live page (scripts, forms, iframes...)."""
    old, new = before.embeds or {}, after.embeds or {}
    return [tag for tag in EMBED_TAGS if old.get(tag, 0) > new.get(tag, 0)]


def same_document(left: str, right: str) -> bool:
    """True when two HTML files are the same page apart from whitespace."""
    return squash(left) == squash(right)


def short(text: str, limit: int = 240) -> str:
    text = squash(text)
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def excerpt_pair(before: str, after: str, context: int = 12, limit: int = 3) -> tuple[str, str, int]:
    """Where two long texts differ, in words: the changed spans with a few words around each, "..." for the gaps.
    Returns (before excerpt, after excerpt, number of changed places). A page body is long and starts with the
    navigation, so showing its first characters would show the same words on both sides."""
    old, new = squash(before).split(" "), squash(after).split(" ")
    spans = [op for op in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not spans:
        return short(before), short(after), 0

    def cut(words: list[str], ranges: list[tuple[int, int]]) -> str:
        pieces, last = [], 0
        for start, end in ranges:
            lo, hi = max(0, start - context), min(len(words), end + context)
            if lo > last or (not pieces and lo > 0):
                pieces.append("...")
            pieces.append(" ".join(words[max(lo, last):hi]))
            last = hi
        if last < len(words):
            pieces.append("...")
        return " ".join(p for p in pieces if p).replace(" ... ", " ... ").strip()

    shown = spans[:limit]
    return (
        cut(old, [(i1, i2) for _tag, i1, i2, _j1, _j2 in shown]),
        cut(new, [(j1, j2) for _tag, _i1, _i2, j1, j2 in shown]),
        len(spans),
    )


def display(row: dict) -> dict:
    """A change row as the Publish sheet shows it: the body as excerpts around what changed, other fields shortened."""
    before, after = row.get("before", ""), row.get("after", "")
    if row.get("field") == "body":
        old, new, places = excerpt_pair(before, after)
        extra = {"places": places} if places > 3 else {}
        return {**row, "before": old, "after": new, **extra}
    return {**row, "before": short(before), "after": short(after)}
