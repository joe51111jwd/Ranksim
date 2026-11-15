"""The audit: specific issues on the site's own pages and searches. Each issue points at one exact element of the
page, says in plain words what is wrong with the numbers, says what to change and, where one can be built from the
page's own words, carries a ready-to-paste suggestion. Nothing here invents facts: suggestions reuse the page's own
title, headings, paragraphs, file names and URLs, and the searches the project tracks.

Shape of one issue (docs/BUILD_PLAN.md 4.4, plus a few extra keys)::

    {id, severity, kind, title, comment, fix, page, search?, element?: {selector, text, tag, attr?},
     suggestion?, action?, evidence?: [str], impact, effort, score, pages?: [url], count?}

``element.selector`` matches exactly one element in the page HTML (checked with soupsieve, and built to match in a
browser too); an element that a browser would build differently is left unpinned. ``action`` says how
``suggestion`` applies to that element: ``text`` (replace its text), ``attr`` (set ``element.attr``), ``retag``
(change the tag name to the suggestion), ``append`` / ``prepend`` / ``after`` (insert the suggestion as HTML),
``copy`` (nothing to apply in the page; the person copies it).

Every built-in check is registered in CHECKS (in order) with the @check decorator. A check takes the AuditContext
and returns a list of issue dicts. audit() runs the built-in CHECKS, then the checks plugins return from their
audit_checks hook with the same context. Built-in issues are deduplicated, a template element repeated on many
pages becomes one issue, and every issue gets impact (1-10), effort (1-3) and score. The list is sorted high, medium,
low, then by score, and every id is made unique.

AuditContext keeps what the plugin contract promises (pages and docs as site.SitePage rows, readable, rankings,
database, meta) and adds the parsed HTML the built-in checks read: ``parsed`` (PageDoc per own page), ``ok_docs``
(the ones Google can read) and ``doc(url)`` for the top 10 pages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from statistics import median
from typing import Callable, Iterable
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import CData, Comment, Declaration, Doctype, ProcessingInstruction

from aeo.ranksim.site import SitePage, page_facts

log = logging.getLogger(__name__)

# Changes with this file, so a deploy with new checks never serves results cached by the old ones.
AUDIT_VERSION = hashlib.sha1(open(__file__, "rb").read()).hexdigest()[:12]

TITLE_MAX = 60
TITLE_MIN = 25
META_MAX = 160
META_MIN = 70
META_TARGET = 155
TITLE_LIMIT = TITLE_MAX  # the names audit.py had before the element-level checks
META_LIMIT = META_MAX
THIN_WORDS = 300
FEW_LINKS = 3
SCRIPT_BUILT_WORDS = 50
HUGE_IMAGE_BYTES = 300_000
HUGE_IMAGE_WIDTH = 1600
PER_PAGE_CAP = 10  # most issues of one kind pinned on one page; the last one says how many more there are
URL_MAX = 115

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# kind: (severity, impact 1-10, effort 1-3). Effort 1 is a text edit, 2 is a content or template change, 3 is a
# rebuild or new page.
RULES: dict[str, tuple[str, int, int]] = {
    "unreadable": ("high", 10, 2),
    "robots-blocked": ("high", 10, 1),
    "noindex": ("high", 10, 1),
    "canonical-other": ("high", 8, 1),
    "script-built": ("high", 9, 3),
    "not-ranking": ("high", 8, 2),
    "no-page": ("high", 7, 3),
    "title-missing": ("high", 8, 1),
    "h1-missing": ("high", 6, 1),
    "viewport-missing": ("medium", 6, 1),
    "broken-link": ("medium", 6, 1),
    "behind": ("medium", 6, 2),
    "thin-vs-top": ("medium", 6, 2),
    "title-search": ("medium", 6, 1),
    "title-duplicate": ("medium", 5, 1),
    "title-long": ("medium", 4, 1),
    "meta-missing": ("medium", 4, 1),
    "h1-search": ("medium", 5, 1),
    "no-answer": ("medium", 5, 2),
    "thin": ("medium", 5, 2),
    "schema-invalid": ("medium", 4, 1),
    "schema-gap": ("medium", 4, 2),
    "meta-duplicate": ("low", 3, 1),
    "meta-long": ("low", 2, 1),
    "meta-short": ("low", 2, 1),
    "title-short": ("low", 3, 1),
    "h1-many": ("low", 3, 1),
    "heading-skip": ("low", 2, 1),
    "few-links": ("low", 3, 1),
    "link-generic": ("low", 2, 1),
    "img-alt": ("low", 2, 1),
    "img-huge": ("low", 3, 1),
    "schema-missing": ("low", 3, 1),
    "og-missing": ("low", 2, 1),
    "twitter-missing": ("low", 1, 1),
    "canonical-missing": ("low", 2, 1),
    "url-hygiene": ("low", 2, 3),
}
RANKING_KINDS = {"not-ranking", "no-page", "behind", "thin-vs-top", "schema-gap"}
# Kinds where the same element on many pages (a footer link, a logo) is one fix in a shared template.
TEMPLATE_KINDS = {"link-generic", "img-alt", "img-huge", "broken-link", "twitter-missing", "viewport-missing"}

SKIP_TAGS = {"script", "style", "template", "svg", "iframe", "object", "embed", "canvas", "noscript", "head"}
HIDDEN_CLASSES = {"sr-only", "visually-hidden", "visuallyhidden", "screen-reader-text", "screenreader", "hidden"}
APP_ROOTS = ("#root", "#app", "#__next", "#__nuxt", "#___gatsby", "#svelte", "[data-reactroot]", "[ng-version]")
GENERIC_ANCHORS = {
    "click here", "here", "read more", "learn more", "more", "this", "this page", "link", "click", "go", "see more",
    "find out more", "more info", "details", "continue", "continue reading", "view more", "this link", "website",
}
STOP = {"and", "the", "for", "with", "your", "you", "a", "an", "of", "to", "in", "on", "or", "is", "are", "vs", "how", "what", "best"}
SPLIT = re.compile(r"\s+[|\-\u2013\u2014:•·]\s+|\s*[|•·]\s*")
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?")
SCHEMA_TYPES = ("FAQPage", "Product", "Organization", "SoftwareApplication", "LocalBusiness", "HowTo", "Article", "BreadcrumbList", "Review", "AggregateRating", "VideoObject")
GAP_TYPES = ("FAQPage", "Product", "Organization", "SoftwareApplication", "LocalBusiness", "HowTo", "BreadcrumbList", "AggregateRating")
SIZE_HINT = re.compile(r"(?<![0-9])(\d{3,5})\s*[x×]\s*(\d{3,5})(?![0-9])")
LD_SCRIPT = re.compile(r"<script[^>]*type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>", re.I | re.S)
ITEMTYPE = re.compile(r"itemtype\s*=\s*[\"']([^\"']+)[\"']", re.I)
WIDTH_HINT = re.compile(r"(?:[-_@](\d{4,5})w(?![a-z])|[?&](?:w|width)=(\d{4,5})(?!\d))", re.I)


# -- text helpers ---------------------------------------------------------------------------------


INLINE = {"a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u", "var", "wbr", "font"}
BANNER = re.compile(r"banner|announce|promo|alert|notice|cookie|consent|topbar|top-bar|marquee|ticker", re.I)


def _streamed(tag) -> bool:
    ident = tag.get("id") if isinstance(tag, Tag) else None
    return tag.name == "div" and isinstance(ident, str) and ident.startswith("S:")


def text_of(tag) -> str:
    """An element's text as a reader sees it: inline parts joined without a space ("QR Code Form" + "s" reads
    "QR Code Forms"), blocks and line breaks with one."""
    if tag is None:
        return ""
    parts: list[str] = []
    for node in tag.descendants:
        if isinstance(node, (Comment, CData, Declaration, Doctype, ProcessingInstruction)):
            continue
        if isinstance(node, NavigableString):
            if not any(parent.name in {"script", "style", "template"} for parent in node.parents if isinstance(parent, Tag)):
                parts.append(str(node))
        elif isinstance(node, Tag) and (node.name == "br" or node.name not in INLINE):
            parts.append(" ")
    return squash("".join(parts))


def words_in(text: str) -> list[str]:
    return WORD.findall(text or "")


def plural(count: int, noun: str) -> str:
    return f"{count:,} {noun}" + ("" if count == 1 else "s")


@lru_cache(maxsize=65536)
def _split(url: str):
    try:
        parts = urlsplit(url or "")
        parts.port  # noqa: B018 - raises on a bad port
        return parts
    except ValueError:
        return urlsplit("")


def safe_join(base: str, href: str) -> str:
    """urljoin that never raises on a malformed address (it returns "")."""
    try:
        joined = urljoin(base, href)
        urlsplit(joined).port  # noqa: B018 - raises on a bad port or host
        return joined
    except ValueError:
        return ""


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def short(text: str, limit: int) -> str:
    """For quoting in a comment: the text cut at a word boundary with "..." when it is longer than limit."""
    text = squash(text)
    if len(text) <= limit:
        return text
    head = text[: limit - 3]
    cut = head if text[limit - 3 : limit - 2] == " " else head.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.|") + "..."


def fold(text: str) -> str:
    """Lower case, accents removed (Términos -> terminos), digits 1 to 3 as words."""
    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()
    return re.sub(r"(?<![\w.])([123])(?![\w.])", lambda m: {"1": "one", "2": "two", "3": "three"}[m.group(1)], plain)


def compact(text: str) -> str:
    """Folded text with every space, hyphen and other non-word character removed: "dirt-work" and "dirtwork" meet."""
    return re.sub(r"[\W_]+", "", fold(text))


def tokens(text: str) -> set[str]:
    return {word for word in words_in(fold(text)) if len(word) > 1 and word not in STOP}


def _variants(words: set[str]) -> set[str]:
    return words | {word[:-1] for word in words if word.endswith("s")} | {word + "s" for word in words}


def _present(word: str, have: set[str], flat: str) -> bool:
    return word in have or (len(word) >= 4 and word in flat)


def covers(search: str, text: str, share: float = 0.75) -> bool:
    want = tokens(search)
    if not want:
        return True
    if compact(search) and compact(search) in compact(text):
        return True
    have = _variants(tokens(text))
    flat = compact(text)
    return sum(1 for word in want if _present(word, have, flat)) / len(want) >= share


def missing_words(search: str, text: str) -> list[str]:
    have = _variants(tokens(text))
    flat = compact(text)
    return [word for word in dict.fromkeys(words_in(fold(search))) if word not in STOP and len(word) > 1 and not _present(word, have, flat)]


def missing_text(search: str, text: str) -> str:
    """How a comment names what is missing: the words, or the whole search when most of it is missing."""
    gap = missing_words(search, text)
    total = [word for word in dict.fromkeys(words_in(fold(search))) if word not in STOP and len(word) > 1]
    if len(gap) > max(1, len(total) // 2):
        return f'the search "{search}"'
    return ", ".join(f'"{word}"' for word in gap)


def cased(phrase: str, text: str) -> str:
    """The phrase in sentence case, keeping a word's casing and accents from the page when the page writes it that
    way (HCSS, QuickBooks, Términos). Never adds words."""
    seen: dict[str, Counter] = defaultdict(Counter)
    for word in words_in(text or ""):
        seen[fold(word)][word] += 1
    out = []
    for word in phrase.split():
        found = seen.get(fold(word))
        if found:
            lower = [item for item in found if item == item.lower()]
            if lower:
                out.append(Counter({item: found[item] for item in lower}).most_common(1)[0][0])
            elif all(item[:1].isupper() and item[1:].islower() for item in found):
                out.append(found.most_common(1)[0][0].lower())
            else:
                out.append(found.most_common(1)[0][0])
        else:
            out.append(word.lower())
    if out and out[0][:1].islower():
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


# Rough widths of Arial at Google's title size (20px), in pixels. Google cuts titles near 600 pixels.
NARROW = set("iljtfrI.,;:'!|()[] ")
WIDE = set("mwMW@%")
TITLE_PIXELS = 600


def pixels(text: str) -> int:
    width = 0.0
    for ch in text or "":
        if ch in NARROW:
            width += 5.6
        elif ch in WIDE:
            width += 16.7
        elif ch.isupper():
            width += 13.3
        elif ch.isdigit():
            width += 11.1
        else:
            width += 10.6
    return int(width)


def clean_cut(text: str, limit: int) -> str | None:
    """Text cut to limit at the latest sentence, clause or list break, never with "...". None when no clean cut
    keeps half of it."""
    text = squash(text)
    if len(text) <= limit:
        return text
    head = text[:limit]
    best = -1
    mark_at = ""
    for mark in (". ", "! ", "? ", "; ", ", ", " | ", ": ", " & ", " and "):
        at = head.rfind(mark)
        if at > best:
            best, mark_at = at, mark
    if best < limit // 2:
        return None
    return head[:best].rstrip(" ,;:|&") + ("." if mark_at.strip() in {".", "!", "?"} else "")


INFO = re.compile(r"[$\u00a3\u20ac]|(?<![\d.])(?!(?:19|20)\d\d(?!\d))\d{1,4}(?![\d])|\b(price|pricing|priced|cost|costs|free|trial|per month|per user|plans?)\b", re.I)


def keep_sentences(text: str, limit: int = META_TARGET) -> str | None:
    """The text shortened to limit by dropping whole sentences, keeping the ones that carry facts a buyer wants
    (a price, a number, "free trial") ahead of the rest, in their original order. None when no set fits."""
    from itertools import combinations

    text = squash(text)
    if len(text) <= limit:
        return text
    parts = [part for part in re.split(r"(?<=[.!?])\s+", text) if part]
    if len(parts) > 8:
        parts = parts[:8]
    best, best_score = None, None
    for size in range(len(parts), 0, -1):
        for picked in combinations(range(len(parts)), size):
            candidate = " ".join(parts[i] for i in picked)
            if len(candidate) > limit:
                continue
            score = (0 in picked, sum(1 for i in picked if INFO.search(parts[i])), len(candidate))
            if best_score is None or score > best_score:
                best, best_score = candidate, score
    return best


def undash(text: str | None) -> str | None:
    """Our own words never carry an em or en dash: a spaced one becomes a comma, a range becomes a hyphen."""
    if not text:
        return text
    text = re.sub(r"\s+[\u2013\u2014]\s+", ", ", text)
    return text.replace("\u2013", "-").replace("\u2014", ", ")


def trim_sentences(text: str, limit: int = META_TARGET) -> str:
    text = squash(text)
    if len(text) <= limit:
        return text
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{kept} {sentence}".strip()
        if len(candidate) > limit:
            break
        kept = candidate
    return kept if len(kept) >= 50 else (clean_cut(text, limit) or "")


@lru_cache(maxsize=65536)
def path_of(url: str) -> str:
    parts = _split(url or "")
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


@lru_cache(maxsize=65536)
def url_key(url: str) -> str:
    """One spelling per page: no scheme, no www, no query, no fragment, no trailing slash, lower-case host.
    /?try=home and / are the same page, and so are www.example.com and example.com."""
    parts = _split((url or "").strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or ""
    return f"{host}{path}"


@lru_cache(maxsize=65536)
def host_of(url: str) -> str:
    return (_split(url or "").hostname or "").lower().removeprefix("www.")


# -- the page ------------------------------------------------------------------------------------


@dataclass
class PageDoc:
    """One page's HTML, parsed once, with the facts the checks need. ``status`` is fetched, blocked or error;
    ``http_status`` is the HTTP code when the crawl kept it."""

    url: str
    html: str = ""
    status: str = "fetched"
    error: str = ""
    http_status: int | None = None
    headers: dict = field(default_factory=dict)
    image_bytes: dict = field(default_factory=dict)  # image URL -> size in bytes, when the crawl measured it
    facts: SitePage | None = None  # stored facts: a top 10 page is compared on these without parsing its HTML

    @property
    def ok(self) -> bool:
        return self.status == "fetched" and bool((self.html or "").strip()) and (self.http_status is None or self.http_status < 400)

    @property
    def path(self) -> str:
        return path_of(self.url)

    @property
    def is_home(self) -> bool:
        return _split(self.url).path in {"", "/"}

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.html or "", "lxml")

    @cached_property
    def body(self) -> Tag:
        return self.soup.body or self.soup

    @cached_property
    def _index(self) -> tuple[dict[str, list[Tag]], dict[str, list[Tag]]]:
        """Every tag by name, for the whole document and for the body, from one walk each. Also counts ids on the
        soup so selector_for can tell a unique id without another walk."""
        everywhere: dict[str, list[Tag]] = defaultdict(list)
        ids: Counter = Counter()
        for tag in self.soup.descendants:
            if isinstance(tag, Tag):
                everywhere[tag.name].append(tag)
                ident = tag.get("id")
                if isinstance(ident, str):
                    ids[ident] += 1
        self.soup.rs_id_counts = ids
        body: dict[str, list[Tag]] = defaultdict(list)
        if self.body is self.soup:
            body = everywhere
        else:
            for tag in self.body.descendants:
                if isinstance(tag, Tag):
                    body[tag.name].append(tag)
        return everywhere, body

    def tags(self, name: str) -> list[Tag]:
        return self._index[0].get(name, [])

    def body_tags(self, *names: str) -> list[Tag]:
        if len(names) == 1:
            return self._index[1].get(names[0], [])
        wanted = set(names)
        return [tag for tag in self.body.descendants if isinstance(tag, Tag) and tag.name in wanted]

    @cached_property
    def head(self) -> Tag | None:
        return self.soup.head

    def _meta(self, **attrs) -> Tag | None:
        for tag in self.tags("meta"):
            if all((tag.get(key) or "").strip().lower() == value for key, value in attrs.items()):
                return tag
        return None

    @cached_property
    def title_el(self) -> Tag | None:
        return next((tag for tag in self.tags("title") if not any(parent.name in {"svg", "symbol", "math"} for parent in tag.parents)), None)

    @property
    def title(self) -> str:
        return squash(self.title_el.get_text()) if self.title_el else ""

    @cached_property
    def meta_el(self) -> Tag | None:
        return self._meta(name="description")

    @property
    def meta(self) -> str:
        return squash(self.meta_el.get("content", "")) if self.meta_el else ""

    @cached_property
    def robots_els(self) -> list[Tag]:
        return [tag for tag in self.tags("meta") if (tag.get("name") or "").strip().lower() in {"robots", "googlebot"}]

    @cached_property
    def canonical_el(self) -> Tag | None:
        for tag in self.tags("link"):
            rel = tag.get("rel") or []
            rel = rel if isinstance(rel, list) else str(rel).split()
            if "canonical" in [item.lower() for item in rel] and tag.get("href"):
                return tag
        return None

    @property
    def canonical(self) -> str:
        return safe_join(self.url, self.canonical_el.get("href", "").strip()) if self.canonical_el else ""

    @cached_property
    def viewport_el(self) -> Tag | None:
        return self._meta(name="viewport")

    @cached_property
    def headings(self) -> list[Tag]:
        return [tag for tag in self.body_tags("h1", "h2", "h3", "h4", "h5", "h6") if not self._in_skipped(tag)]

    @property
    def h1s(self) -> list[Tag]:
        return [tag for tag in self.headings if tag.name == "h1"]

    @property
    def h1(self) -> str:
        return text_of(self.h1s[0]) if self.h1s else ""

    @cached_property
    def main(self) -> Tag:
        """Where the page's content sits: main, the one article, or the body."""
        found = self.body.find("main") or self.body.find(attrs={"role": "main"})
        if found is None:
            articles = self.body_tags("article")
            found = articles[0] if len(articles) == 1 else None
        return found or self.body

    def _in_skipped(self, tag: Tag) -> bool:
        return any(parent.name in {"template", "noscript", "svg"} for parent in tag.parents)

    @cached_property
    def hidden_classes(self) -> set[str]:
        """Classes the page's own CSS hides (outside media queries): .crawl-boot{clip:rect(0,0,0,0)} and friends."""
        found = set(HIDDEN_CLASSES)
        for style in self.tags("style"):
            css = re.sub(r"/\*.*?\*/", "", style.get_text() or "", flags=re.S)
            css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
            for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
                rule = body.replace(" ", "").lower()
                hides = "display:none" in rule or "visibility:hidden" in rule or "clip:rect(0" in rule or "clip-path:inset(50%" in rule or ("width:1px" in rule and "height:1px" in rule)
                if not hides:
                    continue
                for selector in selectors.split(","):
                    selector = selector.strip()
                    if re.fullmatch(r"\.[A-Za-z_][\w-]*", selector):
                        found.add(selector[1:])
        return found

    def is_hidden(self, tag: Tag) -> bool:
        if tag.has_attr("hidden"):
            return not _streamed(tag)  # React streaming parks real content in <div hidden id="S:n"> until a script swaps it in
        style = (tag.get("style") or "").replace(" ", "").lower()
        if "display:none" in style or "visibility:hidden" in style or "clip:rect(0" in style:
            return True
        classes = tag.get("class") or []
        if any(":" in name for name in classes):
            return False  # responsive utilities (hidden md:block) show the element on some screens
        return any(name in self.hidden_classes for name in classes)

    @cached_property
    def _text(self) -> tuple[list[str], list[str], list[Tag]]:
        """(visible text pieces, hidden text pieces, outermost hidden blocks with text)."""
        visible: list[str] = []
        hidden: list[str] = []
        blocks: list[Tag] = []
        stack: list[tuple[Tag, bool]] = [(self.body, False)]
        while stack:
            node, under_hidden = stack.pop()
            for child in node.children:
                if isinstance(child, (Comment, CData, Declaration, Doctype, ProcessingInstruction)):
                    continue
                if isinstance(child, NavigableString):
                    text = str(child)
                    if text.strip():
                        (hidden if under_hidden else visible).append(text)
                    continue
                if not isinstance(child, Tag) or child.name in SKIP_TAGS:
                    continue
                now_hidden = under_hidden or self.is_hidden(child)
                if now_hidden and not under_hidden and len(words_in(child.get_text(" "))) >= 5:
                    blocks.append(child)
                stack.append((child, now_hidden))
        return visible, hidden, blocks

    @cached_property
    def visible_words(self) -> int:
        return len(words_in(" ".join(self._text[0])))

    @cached_property
    def hidden_words(self) -> int:
        return len(words_in(" ".join(self._text[1])))

    @property
    def words(self) -> int:
        if self.facts is not None and self.facts.status == "fetched" and "soup" not in self.__dict__:
            return self.facts.words
        return self.visible_words + self.hidden_words

    @property
    def hidden_blocks(self) -> list[Tag]:
        return self._text[2]

    @cached_property
    def text(self) -> str:
        return squash(" ".join(self._text[0] + self._text[1]))

    @cached_property
    def script_bytes(self) -> int:
        total = 0
        for script in self.tags("script"):
            kind = (script.get("type") or "").lower()
            if kind in {"application/ld+json", "application/json"}:
                continue
            total += len(script.get_text() or "") + (5000 if script.get("src") else 0)
        return total

    def inside(self, tag: Tag, *names: str) -> bool:
        return any(parent.name in names for parent in tag.parents)

    @cached_property
    def links(self) -> list[tuple[Tag, str, str]]:
        """(a tag, absolute URL, anchor text) for every followable link in the body."""
        out = []
        for tag in self.body_tags("a"):
            if not tag.has_attr("href"):
                continue
            href = tag["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:", "data:")) or self._in_skipped(tag):
                continue
            label = text_of(tag) or squash(tag.get("aria-label", "")) or squash(" ".join(img.get("alt", "") for img in tag.find_all("img")))
            joined = safe_join(self.url, href).split("#", 1)[0]
            if joined:
                out.append((tag, joined, label))
        return out

    def is_internal(self, url: str) -> bool:
        return host_of(url) == host_of(self.url)

    @cached_property
    def images(self) -> list[Tag]:
        return [tag for tag in self.body_tags("img") if not self._in_skipped(tag)]

    @cached_property
    def jsonld(self) -> list[dict]:
        """Each JSON-LD block: {el, data, error, types}."""
        out = []
        for script in self.tags("script"):
            if (script.get("type") or "").strip().lower() != "application/ld+json":
                continue
            raw = script.string if script.string is not None else script.get_text()
            raw = (raw or "").strip()
            entry = {"el": script, "raw": raw, "data": None, "error": "", "types": set()}
            if not raw:
                entry["error"] = "the block is empty"
            else:
                try:
                    entry["data"] = json.loads(raw)
                except ValueError as exc:
                    entry["error"] = str(exc)
                else:
                    entry["types"] = schema_types(entry["data"])
                    if not entry["types"]:
                        entry["error"] = "it has no @type"
            out.append(entry)
        return out

    @cached_property
    def types(self) -> set[str]:
        found: set[str] = set()
        if self.facts is not None and "soup" not in self.__dict__:
            for raw in LD_SCRIPT.findall(self.html or ""):
                try:
                    found |= schema_types(json.loads(raw.strip()))
                except ValueError:
                    continue
            found |= {value.rstrip("/").rsplit("/", 1)[-1] for value in ITEMTYPE.findall(self.html or "")}
            return found
        for entry in self.jsonld:
            found |= entry["types"]
        for tag in (item for items in self._index[0].values() for item in items if item.has_attr("itemtype")):
            found.add(str(tag["itemtype"]).rstrip("/").rsplit("/", 1)[-1])
        return found

    @property
    def has_faq(self) -> bool:
        if self.facts is not None and "soup" not in self.__dict__:
            return self.facts.has_faq or "FAQPage" in self.types
        return "FAQPage" in self.types or any("faq" in squash(tag.get_text()).lower() or "frequently asked" in squash(tag.get_text()).lower() for tag in self.headings)

    @cached_property
    def tables(self) -> int:
        if self.facts is not None and "soup" not in self.__dict__:
            return self.facts.tables
        return len(self.body_tags("table"))

    def paragraphs(self, *, min_words: int = 8, main_only: bool = False) -> list[str]:
        out = []
        for tag in self.body_tags("p"):
            if self._in_skipped(tag) or self.inside(tag, "nav", "footer", "header", "form", "aside"):
                continue
            if main_only and (self.main is not self.body and self.main not in tag.parents):
                continue
            if any(BANNER.search(" ".join(parent.get("class") or []) + " " + str(parent.get("id") or "")) for parent in [tag, *tag.parents] if isinstance(parent, Tag)):
                continue
            text = text_of(tag)
            lower = text.lower()
            if len(words_in(text)) >= min_words and not any(bad in lower for bad in ("cookie", "\u00a9", "copyright", "all rights reserved")):
                out.append(text)
        return out

    @cached_property
    def content_words(self) -> int:
        """Words in the main content: no nav, header, footer, aside, form, script or hidden block."""
        count = 0
        roots = [self.main] + [tag for tag in self.body_tags("div") if _streamed(tag) and self.main not in tag.parents and tag is not self.main]
        stack = [(root, False) for root in roots]
        while stack:
            node, hidden = stack.pop()
            for child in node.children:
                if isinstance(child, (Comment, CData, Declaration, Doctype, ProcessingInstruction)):
                    continue
                if isinstance(child, NavigableString):
                    if not hidden:
                        count += len(words_in(str(child)))
                    continue
                if not isinstance(child, Tag) or child.name in SKIP_TAGS or child.name in {"nav", "header", "footer", "aside", "form"}:
                    continue
                stack.append((child, hidden or self.is_hidden(child)))
        return count

    def og(self, prop: str) -> Tag | None:
        return self._meta(property=prop) or self._meta(name=prop)

    def site_name(self) -> str:
        tag = self.og("og:site_name")
        return squash(tag.get("content", "")) if tag else ""


def schema_types(data) -> set[str]:
    found: set[str] = set()
    stack = [data]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            kind = item.get("@type")
            for value in kind if isinstance(kind, list) else [kind]:
                if isinstance(value, str) and value:
                    found.add(value.rsplit("/", 1)[-1])
            stack.extend(value for value in item.values() if isinstance(value, (dict, list)))
    return found


# -- selectors -----------------------------------------------------------------------------------


def _ident(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][\w-]*", value or ""))


def _attr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _id_count(soup: BeautifulSoup, ident: str) -> int:
    counts = getattr(soup, "rs_id_counts", None)
    if isinstance(counts, Counter):
        return counts.get(ident, 0)
    return len(soup.find_all(id=ident))


def _matches_only(soup: BeautifulSoup, selector: str, tag: Tag) -> bool:
    try:
        found = soup.select(selector, limit=2)
    except Exception:  # noqa: BLE001 - soupsieve rejects a selector: fall back to the path
        return False
    return len(found) == 1 and found[0] is tag


TABLE_PARTS = {"caption", "colgroup", "thead", "tbody", "tfoot", "tr", "script", "template", "style"}
BLOCKS = {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "menu", "nav", "ol", "p", "pre", "section", "table", "ul"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
TAG_NAME = re.compile(r"[a-z][a-z0-9-]*")


def _browser_safe(tag: Tag) -> bool:
    """False when a browser would build this part of the tree differently from lxml (a stray element inside a
    table, a block inside a paragraph, a heading inside a heading, nested forms or links). A selector for it
    could then match nothing or the wrong element in the editor, so the issue is not pinned."""
    chain = [tag] + [parent for parent in tag.parents if isinstance(parent, Tag) and not isinstance(parent, BeautifulSoup)]
    for index, node in enumerate(chain):
        if not TAG_NAME.fullmatch(node.name or ""):
            return False
        parent = chain[index + 1] if index + 1 < len(chain) else None
        if parent is None:
            continue
        if parent.name == "table" and node.name not in TABLE_PARTS:
            return False
        if parent.name in {"tbody", "thead", "tfoot"} and node.name not in {"tr", "script", "template"}:
            return False
        if parent.name == "tr" and node.name not in {"td", "th", "script", "template"}:
            return False
        above = {item.name for item in chain[index + 1 :]}
        if node.name in BLOCKS and "p" in above:
            return False
        if node.name in HEADINGS and above & HEADINGS:
            return False
        if node.name in {"form", "a", "nobr", "button"} and node.name in above:
            return False
    if tag.name == "image":
        return False
    return True


def selector_for(tag: Tag, soup: BeautifulSoup | None = None) -> str:
    """A CSS selector that matches exactly this element, in BeautifulSoup and in a browser, or "" when no safe
    one exists (the issue then has no element and is listed without a pin).

    Head tags use their attributes (``meta[name="description"]``). Body tags use the nearest unique id, then
    ``tag:nth-of-type(n)`` steps. A loose table row gets two alternatives, with and without the tbody a browser
    adds, joined into one selector list."""
    soup = soup or next((parent for parent in tag.parents if isinstance(parent, BeautifulSoup)), None)
    if soup is None or not _browser_safe(tag):
        return ""
    if tag.name in {"head", "body", "html"}:
        return tag.name
    if tag.name == "title":
        selector = "head > title"
        return selector if _matches_only(soup, selector, tag) else ""
    if tag.name in {"meta", "link"}:
        for key in ("name", "property", "rel", "http-equiv", "itemprop"):
            value = tag.get(key)
            if isinstance(value, list):
                value = " ".join(value)
            value = squash(value or "")
            if value and " " not in value:
                op = "~=" if key == "rel" else "="
                selector = f'{tag.name}[{key}{op}"{_attr_value(value)}"]'
                if _matches_only(soup, selector, tag):
                    return selector
    alternatives = [""]
    node: Tag | None = tag
    while node is not None and isinstance(node, Tag) and not isinstance(node, BeautifulSoup):
        if node.name == "html":
            break
        if node.name in {"body", "head"}:
            options = [node.name]
            node = None
        else:
            ident = node.get("id")
            parent = node.parent
            if isinstance(ident, str) and _ident(ident) and _id_count(soup, ident) == 1:
                options = [f"{node.name}#{ident}"]
                node = None
            else:
                same = [child for child in parent.children if isinstance(child, Tag) and child.name == node.name] if parent is not None else [node]
                step = node.name if len(same) <= 1 else f"{node.name}:nth-of-type({same.index(node) + 1})"
                options = [step, f"tbody > {step}"] if node.name == "tr" and parent is not None and parent.name == "table" else [step]
                node = parent
        alternatives = [f"{option} > {rest}" if rest else option for option in options for rest in alternatives]
        if len(alternatives) > 8:
            return ""
    selector = ", ".join(alternatives)
    return selector if _matches_only(soup, selector, tag) else ""


def element_of(tag: Tag | None, doc: PageDoc, *, attr: str | None = None) -> dict | None:
    if tag is None:
        return None
    if tag.name == "meta":
        text = tag.get("content", "")
    elif tag.name == "img":
        text = tag.get("alt") or tag.get("src") or ""
    elif tag.name == "link":
        text = tag.get("href", "")
    elif tag.name == "script":
        text = tag.string or tag.get_text() or ""
    elif tag.name in {"main", "body", "article", "head"}:
        text = ""
    else:
        text = text_of(tag)
    doc._index  # noqa: B018 - counts ids once for selector_for
    selector = selector_for(tag, doc.soup)
    if not selector:
        return None
    out = {"selector": selector, "text": short(text, 160), "tag": tag.name}
    if attr:
        out["attr"] = attr
    return out


# -- the context the checks read -----------------------------------------------------------------


@dataclass
class AuditContext:
    """What every check reads. Checks must not change it.

    pages and docs may be given as site.SitePage rows (with their HTML in ``html`` or loaded from ``database``),
    as PageDoc objects, or docs as raw HTML strings. Either way ``pages``, ``readable`` and ``docs`` are SitePage
    rows for plugins, and ``parsed``, ``ok_docs`` and ``doc()`` are PageDocs for the element checks."""

    project: object | None = None
    pages: list = field(default_factory=list)  # the site's own pages, readable or not
    rankings: list[dict] = field(default_factory=list)  # build_report rows: q, position, url, checked, top
    docs: dict = field(default_factory=dict)  # stored copies of the top 10 pages, by URL
    database: str | None = None
    meta: object | None = None  # the project's workspace meta, when the report passes it
    html: dict[str, str] | None = None  # raw HTML by URL; loaded from database when None
    robots_txt: str | None = None
    statuses: dict[str, object] = field(default_factory=dict)  # crawl status by URL: 404, "error", "fetched"
    brand_hint: str = ""  # the brand when known; otherwise worked out from the pages on first use
    readable: list[SitePage] = field(init=False)
    own_by_url: dict[str, SitePage] = field(init=False)

    def __post_init__(self) -> None:
        given = list(self.pages or [])
        doc_items = dict(self.docs or {})
        html = dict(self.html or {})
        wanted = [page.url for page in given if isinstance(page, SitePage) and page.url not in html]
        wanted += [url for url, doc in doc_items.items() if isinstance(doc, SitePage) and url not in html]
        if wanted and self.database and self.html is None:
            html.update(load_html(self.database, wanted))
        self.parsed: list[PageDoc] = [page if isinstance(page, PageDoc) else page_doc(page, html.get(page.url, "")) for page in given]
        # SitePages read fine but whose HTML is not at hand: only their stored facts can be checked
        self.facts_only: list[SitePage] = [page for page in given if isinstance(page, SitePage) and page.status == "fetched" and not (html.get(page.url) or "").strip()]
        facts_urls = {page.url for page in self.facts_only}
        self.parsed = _dedupe([page for page in self.parsed if page.url not in facts_urls])
        self.pages = [page if isinstance(page, SitePage) else site_page(page) for page in given]
        parsed_docs: dict[str, PageDoc] = {}
        site_docs: dict[str, SitePage] = {}
        for url, doc in doc_items.items():
            if isinstance(doc, PageDoc):
                parsed_docs[url], site_docs[url] = doc, site_page(doc)
            elif isinstance(doc, str):
                parsed_docs[url] = PageDoc(url, doc)
                site_docs[url] = site_page(parsed_docs[url])
            elif isinstance(doc, SitePage):
                parsed_docs[url], site_docs[url] = page_doc(doc, html.get(url, "")), doc
                parsed_docs[url].facts = doc
        self.docs = site_docs
        self._html = html
        self.readable = [page for page in self.pages if page.status == "fetched"]
        self.own_by_url = {page.url: page for page in self.readable}
        self._by_key = {url_key(page.url): page for page in self.parsed}
        self._facts_by_key = {url_key(page.url): page for page in self.facts_only}
        self._site_by_key = {url_key(page.url): page for page in self.pages}
        self._docs_by_key = {url_key(url): doc for url, doc in parsed_docs.items()}

    @cached_property
    def brand(self) -> str:
        """The site's brand. Lazy: guessing it reads every page's title, and a cached audit never needs that."""
        return self.brand_hint or guess_brand(self.parsed, getattr(self.project, "name", "") or "")

    @cached_property
    def robots(self):
        if not self.robots_txt:
            return None
        from urllib.robotparser import RobotFileParser

        parser = RobotFileParser()
        parser.parse(self.robots_txt.splitlines())
        return parser

    def blocked(self, page: PageDoc) -> bool:
        return page.status == "blocked" or (self.robots is not None and not self.robots.can_fetch("Googlebot", page.url))

    @cached_property
    def aliases(self) -> dict[str, PageDoc | None]:
        """Pages whose canonical names another page that shows the same content (or one we did not read): an alias
        or a copy. Their canonical is right, and every content issue belongs to the page it names. url_key -> the
        canonical page (None when it was not read)."""
        out: dict[str, PageDoc | None] = {}
        for page in self.parsed:
            if not page.ok or not page.canonical or url_key(page.canonical) == url_key(page.url):
                continue
            target = self._by_key.get(url_key(page.canonical))
            if target is None or not target.ok or same_content(page, target):
                out[url_key(page.url)] = target
        return out

    def is_alias(self, page: PageDoc) -> bool:
        return url_key(page.url) in self.aliases

    @cached_property
    def ok_docs(self) -> list[PageDoc]:
        """Own pages Google can fetch and read, aliases left out. A page robots.txt blocks gets one issue and
        nothing else; an alias of another page gets none (its canonical page carries them)."""
        return [page for page in self.parsed if page.ok and not self.blocked(page) and not self.is_alias(page)]

    @cached_property
    def content_docs(self) -> list[PageDoc]:
        """ok_docs without legal, account and form pages: the pages title, description, heading and URL advice is for."""
        return [page for page in self.ok_docs if not self.utility(page)]

    def utility(self, page: PageDoc) -> bool:
        """Legal, account, form and other utility pages: no content, link or search advice for them."""
        return bool(UTILITY_PATH.search(_split(page.url).path or "/"))

    def indexed(self, page: PageDoc) -> bool:
        """False for a page that asks not to be indexed or names another page as the original."""
        if any("noindex" in (tag.get("content") or "").lower() for tag in page.robots_els):
            return False
        return not (page.canonical and url_key(page.canonical) != url_key(page.url))

    def page(self, url: str | None) -> PageDoc | None:
        return self._by_key.get(url_key(url)) if url else None

    def ranking_page(self, url: str | None) -> PageDoc | None:
        """The page a ranking row is about, with an alias swapped for the page it names."""
        page = self.page(url)
        if page is not None and self.is_alias(page) and self.aliases[url_key(page.url)] is not None:
            return self.aliases[url_key(page.url)]
        return page

    def doc(self, url: str) -> PageDoc | None:
        return self._docs_by_key.get(url_key(url))

    def is_ours(self, url: str) -> bool:
        project = self.project
        if project is not None and hasattr(project, "is_our_url"):
            return bool(project.is_our_url(url))
        hosts = {host_of(page.url) for page in self.parsed}
        return host_of(url) in hosts

    @cached_property
    def searches_by_page(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for row in self.rankings:
            if row.get("url"):
                page = self.ranking_page(row["url"])  # a ranking on an alias counts for the page it names
                out[url_key(page.url if page is not None else row["url"])].append(row)
        return out

    def searches_for(self, page: PageDoc) -> list[dict]:
        """The searches this page answers, only those with real Google data: the ones it ranks for, or, when it
        ranks for none, the ones the report picked it as the best page for."""
        rows = [row for row in self.searches_by_page.get(url_key(page.url), []) if row.get("checked")]
        ranked = [row for row in rows if row.get("position")]
        return sorted(ranked or rows, key=lambda row: (row.get("position") or 99))

    def settled(self, page: PageDoc) -> bool:
        """True when the page already ranks in the top 3 for a tracked search: never push another search on it."""
        return any((row.get("position") or 99) <= 3 for row in self.searches_for(page))

    def target(self, page: PageDoc) -> str | None:
        """The search to name in the page's title and H1: one with Google data, and only for a page that is not
        already in the top 3 for its search."""
        if self.settled(page):
            return None
        rows = self.searches_for(page)
        return rows[0]["q"] if rows else None

    def comparable_words(self, doc: PageDoc, rivals: list[PageDoc]) -> int:
        """Word counts that can be compared: when the top pages are counted from their stored facts, our page is
        counted the same way (its stored facts), never one way for us and another for them."""
        if doc.facts is not None and doc.facts.status == "fetched":
            return doc.facts.words
        if any(rival.facts is not None for rival in rivals):
            facts = self._site_by_key.get(url_key(doc.url))
            if facts is not None and facts.status == "fetched":
                return facts.words
        return doc.words

    def top_docs(self, row: dict, count: int = 3) -> list[PageDoc]:
        out = []
        for item in row.get("top") or []:
            if item.get("you") or self.is_ours(item.get("url", "")):
                continue
            doc = self.doc(item.get("url", ""))
            if doc is not None and doc.ok:
                out.append(doc)
            if len(out) >= count:
                break
        return out

    def leaders(self, row: dict, count: int = 3) -> list[dict]:
        return [item for item in (row.get("top") or []) if not item.get("you") and not self.is_ours(item.get("url", ""))][:count]

    def link_status(self, url: str):
        key = url_key(url)
        for known, value in self.statuses.items():
            if url_key(known) == key:
                return value
        page = self.page(url)
        if page is not None and page.http_status is not None:
            return page.http_status  # only a real HTTP answer: our own timeout is never a broken link
        return None


LANGUAGE_CODE = re.compile(r"[a-z]{2}(-[A-Za-z]{2,4})?")
NOT_A_PAGE = re.compile(r"content.type\W+(image|video|audio|font|application/(pdf|zip|octet|json|xml))", re.I)
NOT_A_PAGE_URL = re.compile(r"\.(png|jpe?g|gif|webp|avif|svg|ico|pdf|zip|mp4|mov|webm|mp3|woff2?|ttf|css|js|json|xml)$", re.I)
SLOW_FETCH = re.compile(r"time ?out|timed out|took too long|read timeout|deadline", re.I)
UTILITY_PATH = re.compile(
    r"/(legal|privacy|terms|tos|cookies?|policy|policies|gdpr|imprint|impressum|disclaimer|accessibility|security|"
    r"rechtliches|datenschutz\w*|nutzungsbedingungen|agb|juridi\w*|aviso-legal|mentions-legales|privacidad|privacite|"
    r"terminos\w*|condiciones|voorwaarden|privacybeleid|"
    r"login|log-in|signin|sign-in|signup|sign-up|register|account|cart|checkout|unsubscribe|thank-you|thanks|"
    r"search|404|careers|jobs|press|newsroom|referrals?|schedule|book-a-demo|summit|sitemap|dev-[a-z0-9-]*|staging)(/|$|[-_])",
    re.I,
)


def same_content(one: PageDoc, two: PageDoc) -> bool:
    """True when two pages are the same page: a URL that redirects (the crawl stores the page it lands on), or an
    alias serving its canonical page's HTML. Pages fetched at different times differ in dates, related links and
    tokens, so the same title and H1 is enough; otherwise the text must nearly match."""
    if (one.html or "") == (two.html or ""):
        return True
    if one.title and fold(one.title) == fold(two.title) and fold(one.h1) == fold(two.h1):
        return True
    a, b = set(words_in(fold(one.text))), set(words_in(fold(two.text)))
    return bool(a) and bool(b) and len(a & b) / len(a | b) >= 0.8


def _dedupe(pages: list[PageDoc]) -> list[PageDoc]:
    """One PageDoc per url_key (www and bare hosts, / and /?try=home), keeping a readable one first."""
    kept: dict[str, PageDoc] = {}
    for page in pages:
        key = url_key(page.url)
        if key not in kept or (page.ok and not kept[key].ok):
            kept[key] = page
    return list(kept.values())


def page_doc(page: SitePage, html: str) -> PageDoc:
    """A PageDoc for a stored SitePage and its raw HTML. "HTTP 404" in the crawl error becomes http_status."""
    found = re.search(r"\bHTTP\s+(\d{3})\b", page.error or "")
    return PageDoc(page.url, html or "", status=page.status, error=page.error or "", http_status=int(found.group(1)) if found else None)


def site_page(doc: PageDoc) -> SitePage:
    """The SitePage plugins read, built from the parsed page (no second parse)."""
    if not doc.ok:
        error = doc.error or (f"HTTP {doc.http_status}" if doc.http_status else "")
        return SitePage(url=doc.url, status=doc.status if doc.status != "fetched" else "error", error=error)
    return SitePage(
        url=doc.url,
        status="fetched",
        title=doc.title,
        meta=doc.meta,
        h1=doc.h1,
        h1_count=len(doc.h1s),
        h2s=[text_of(tag) for tag in doc.headings if tag.name == "h2"][:12],
        words=doc.words,
        tables=doc.tables,
        has_faq=doc.has_faq,
        links=[url for _tag, url, _label in doc.links],
    )


def load_html(database: str, urls: list[str]) -> dict[str, str]:
    """Stored raw HTML for these URLs, matched however the store spells them."""
    from aeo.ranksim.project import loose_key
    from aeo.ranksim.store import RsPage, open_session

    if not urls:
        return {}
    wanted = set(urls) | {url.rstrip("/") for url in urls} | {url.rstrip("/") + "/" for url in urls}
    session = open_session(database)
    try:
        rows = session.query(RsPage.url, RsPage.raw_html).filter(RsPage.url.in_(sorted(wanted))).all()
    finally:
        session.close()
    found = {loose_key(url): raw or "" for url, raw in rows}
    return {url: found[loose_key(url)] for url in urls if loose_key(url) in found}


def guess_brand(pages: list[PageDoc], fallback: str = "") -> str:
    readable = [page for page in pages if page.ok]
    for page in sorted(readable, key=lambda page: not page.is_home):
        name = page.site_name()
        if name:
            return name
    ends = Counter()
    for page in readable:
        parts = [part.strip() for part in SPLIT.split(page.title) if part.strip()]
        if len(parts) > 1:
            ends[parts[-1]] += 1
    if ends:
        name, count = ends.most_common(1)[0]
        if count >= max(1, len(readable) // 2) and len(name) <= 30:
            return name
    return fallback


def _is_brand(part: str, brand: str) -> bool:
    if not brand:
        return False
    a, b = compact(part), compact(brand)
    return bool(a) and bool(b) and (a == b or a.startswith(b) or b.startswith(a))


def core_title(title: str, brand: str) -> str:
    """The title without its brand part ("Mitti (by SafetyCulture)" counts as the brand "Mitti")."""
    parts = [part.strip() for part in SPLIT.split(title or "") if part.strip()]
    kept = [part for part in parts if not _is_brand(part, brand)]
    return " | ".join(kept) if kept else squash(title)


def add_missing(search: str, text: str, page_text: str) -> str | None:
    """The text with only the search words it lacks added, when those words open or close the search
    ("all in one" + "Dirtwork Software for Crews" -> "All in one dirtwork software for crews"). None otherwise."""
    words = search.split()
    gap = set(missing_words(search, text))
    if not gap or len(gap) == len([w for w in words if fold(w) not in STOP]):
        return None
    flags = [fold(word) in gap or fold(word) in STOP for word in words]
    lead = 0
    while lead < len(words) and flags[lead]:
        lead += 1
    tail = len(words)
    while tail > 0 and flags[tail - 1]:
        tail -= 1
    inner_missing = any(fold(word) in gap for word in words[lead:tail])
    if inner_missing:
        return None
    if tail < len(words) and (len(text.split()) > 6 or re.search(r"[?!.:]", text)):
        return None  # words tacked onto the end of a sentence or question never read well
    shown = [word for word in text.split() if word[:1].isalpha() and fold(word) not in STOP and fold(word) not in {"of", "to", "in", "on", "at", "by"}]
    titled = bool(shown) and sum(1 for word in shown if word[:1].isupper()) / len(shown) >= 0.6

    def style(part: str, first: bool) -> str:
        part = cased(part, page_text)
        if not first and not titled and not part[:2].isupper():
            part = part[:1].lower() + part[1:]
        if titled:
            return " ".join(word if word[:1].isupper() or fold(word) in STOP else word[:1].upper() + word[1:] for word in part.split())
        return part

    before = style(" ".join(words[:lead]), True) if lead else ""
    after = style(" ".join(words[tail:]), False) if tail < len(words) else ""
    if before and not titled and not text[:2].isupper():
        text = text[:1].lower() + text[1:]
    out = squash(f"{before} {text} {after}")
    return out[:1].upper() + out[1:]


def natural_title(page: "PageDoc", search: str, brand: str) -> str | None:
    """A title that names the search and still reads like the page: the H1 when it already names the search, else
    the search in front of the page's own title. None when neither fits; an exact-match title is never suggested."""
    core = core_title(page.title, brand)
    options = []
    if page.h1 and covers(search, page.h1) and fold(page.h1) != fold(search):
        options.append(page.h1)
    lead = cased(search, page.text)
    clause = re.split(r"\s*[:|?]\s+", core)[0] if core else ""
    for base in dict.fromkeys(item for item in (core, clause) if item):
        added = add_missing(search, base, page.text)
        if added:
            options.append(added)
    if core and covers(core, lead, 0.9):
        options.append(lead)  # the search already holds every word of the title: it is the natural title
    elif core and not covers(lead, core, 0.5):
        options.append(f"{lead}: {core}")
    for option in options:
        option = squash(re.sub(r"\s*[\u2013\u2014]\s*", ", ", option))
        for candidate in ([f"{option} | {brand}"] if brand and compact(brand) not in compact(option) else []) + [option]:
            if len(candidate) <= TITLE_MAX + 10 and pixels(candidate) <= TITLE_PIXELS:
                return candidate
    return None


def visible_title(title: str) -> str:
    """Roughly what Google shows of a title: the words that fit in TITLE_PIXELS."""
    out = []
    for word in squash(title).split(" "):
        if pixels(" ".join(out + [word])) > TITLE_PIXELS - 20:
            break
        out.append(word)
    return " ".join(out).rstrip(" ,;:|-")


def compose_title(lead: str, rest: str, brand: str, limit: int = TITLE_MAX) -> str | None:
    """The first of lead | rest | brand, lead | brand, lead | rest, lead that fits."""
    lead = squash(lead)
    rest = squash(rest)
    options = []
    if rest and not covers(rest, lead, 0.9):
        options += [f"{lead} | {rest} | {brand}" if brand else "", f"{lead} | {rest}"]
    if brand and compact(brand) not in compact(lead):
        options.append(f"{lead} | {brand}")
    options.append(lead)
    for option in options:
        if option and len(option) <= limit and pixels(option) <= TITLE_PIXELS:
            return option
    return clean_cut(lead, limit) if lead else None


# -- issues --------------------------------------------------------------------------------------


def make_issue(
    kind: str,
    title: str,
    comment: str,
    *,
    page: PageDoc | str | None = None,
    search: str | None = None,
    fix: str = "",
    el: Tag | dict | None = None,
    attr: str | None = None,
    suggestion: str | None = None,
    action: str | None = None,
    evidence: Iterable[str] | None = None,
    severity: str | None = None,
    key: str = "",
) -> dict:
    rule = RULES.get(kind, ("medium", 4, 2))
    url = page.url if isinstance(page, PageDoc) else page
    element = element_of(el, page, attr=attr) if isinstance(el, Tag) and isinstance(page, PageDoc) else el
    issue = {
        "id": "",
        "severity": severity or rule[0],
        "kind": kind,
        "title": title,
        "comment": comment,
        "fix": fix,
        "page": url,
        "search": search,
        "impact": rule[1],
        "effort": rule[2],
    }
    if element:
        issue["element"] = element
    if suggestion:
        issue["suggestion"] = suggestion if action == "retag" else undash(suggestion)
        issue["action"] = action or ("attr" if attr else "text")
    if evidence:
        issue["evidence"] = [str(item) for item in evidence if item]
    selector = (element or {}).get("selector", "")
    issue["id"] = _issue_id(kind, url, search, " ".join(part for part in (selector, key) if part) or None)
    return issue


def _issue_id(kind: str, page: str | None, search: str | None, selector: str | None = None) -> str:
    """kind-page-search[-selector] in lowercase letters, digits and dashes. Over 120 characters it is cut to 111 and
    ends in a short hash of the whole, so two long ids that share a start still differ."""
    parts = [kind, page or "", search or ""] + ([selector] if selector else [])
    full = re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")
    if len(full) <= 120:
        return full
    return f"{full[:111].rstrip('-')}-{hashlib.sha1(full.encode()).hexdigest()[:8]}"


ID_LIMIT = 120  # share's ISSUE_ID_RE: ^[a-z0-9][a-z0-9-]{0,119}$


def _id_shape(value: str) -> str:
    """An id share and publish accept: lowercase letters, digits and dashes, at most ID_LIMIT characters."""
    clean = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "issue"
    if len(clean) <= ID_LIMIT:
        return clean
    return f"{clean[: ID_LIMIT - 9].rstrip('-')}-{hashlib.sha1(clean.encode()).hexdigest()[:8]}"


def _unique_ids(issues: list[dict]) -> None:
    """Give every issue a different id, in list order: a repeated id gets -2, -3 and so on (never an id in use).
    The suffix fits inside ID_LIMIT: the base is cut first, so a long id stays a valid id."""
    for issue in issues:
        issue["id"] = _id_shape(issue["id"])
    taken = {issue["id"] for issue in issues}
    seen: set[str] = set()
    for issue in issues:
        base = issue["id"]
        if base not in seen:
            seen.add(base)
            continue
        number = 2
        while True:
            suffix = f"-{number}"
            candidate = f"{base[: ID_LIMIT - len(suffix)].rstrip('-')}{suffix}"
            if candidate not in taken and candidate not in seen:
                break
            number += 1
        issue["id"] = candidate
        seen.add(candidate)


def _issue(severity: str, kind: str, title: str, comment: str, *, page: str | None = None, search: str | None = None, fix: str = "") -> dict:
    """The page-level issue builder audit.py had before element checks (kept for older callers)."""
    return make_issue(kind, title, comment, page=page, search=search, fix=fix, severity=severity)


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."


CheckFn = Callable[[AuditContext], list]
CHECKS: list[CheckFn] = []


def check(fn: CheckFn) -> CheckFn:
    """Register a built-in check. Checks run in the order they are registered."""
    if fn not in CHECKS:
        CHECKS.append(fn)
    return fn


register_check = check


def _capped(items: list, page: PageDoc, noun: str) -> list:
    """Keep the first PER_PAGE_CAP; the last kept one says how many more there are."""
    if len(items) <= PER_PAGE_CAP:
        return items
    kept = items[:PER_PAGE_CAP]
    kept[-1]["comment"] += f" {len(items) - PER_PAGE_CAP} more {noun} on {page.path} have the same problem."
    return kept


# -- checks: can Google read and index the page ----------------------------------------------------


def _sitewide(issues: list[dict], kind: str, ctx: "AuditContext", title: str, comment: str, fix: str, limit: int = 5) -> list[dict]:
    """More than limit pages with the same page-wide gap is one template fix: one issue that lists the pages."""
    same = [issue for issue in issues if issue["kind"] == kind]
    if len(same) <= limit:
        return issues
    pages = [issue["page"] for issue in same]
    home = next((page for page in ctx.content_docs if page.is_home), None)
    anchor = home.url if home is not None and home.url in pages else pages[0]
    merged = make_issue(kind, title.format(count=len(pages)), comment.format(count=len(pages)), page=anchor, fix=fix, key="sitewide")
    merged["pages"] = pages
    merged["count"] = len(pages)
    return [issue for issue in issues if issue["kind"] != kind] + [merged]


@check
def check_indexable(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.parsed:
        if ctx.blocked(page):
            if ctx.utility(page):
                continue  # a staging, account or legal path kept out of Google on purpose
            issues.append(
                make_issue(
                    "robots-blocked", f"robots.txt blocks {page.path}", f"Google is told not to crawl {page.path}, so it can't read the page or rank its content.",
                    page=page, fix="Remove the Disallow rule that covers this path in robots.txt, unless you mean to hide it.",
                    evidence=[line for line in (ctx.robots_txt or "").splitlines() if line.lower().startswith("disallow")][:5],
                )
            )
            continue
        if page.status != "fetched" or (page.http_status is not None and page.http_status >= 400) or not (page.html or "").strip():
            reason = f"HTTP {page.http_status}" if page.http_status else (page.error or page.status or "no HTML")
            if NOT_A_PAGE.search(reason) or NOT_A_PAGE_URL.search(_split(page.url).path or ""):
                continue  # an image, PDF or other file the sitemap lists: not a page, nothing to say
            if page.http_status is None and SLOW_FETCH.search(reason):
                issues.append(
                    make_issue(
                        "unreadable", f"We could not read {page.path} in time", f"Our reader gave up waiting for {page.path} ({reason}). That is our limit, not proof the page is down, so nothing else was checked on it.",
                        page=page.url, severity="low", key="slow", evidence=[reason],
                        fix="Open the page yourself. If it is slow for visitors too, speed it up; otherwise refresh the site here later.",
                    )
                )
                continue
            issues.append(
                make_issue(
                    "unreadable", f"We couldn't read {page.path}", f"It answered with {reason}. If Google gets the same, this page can't rank.",
                    page=page.url, fix="Check the page loads for visitors and returns 200, and that robots.txt doesn't block it.", evidence=[reason],
                )
            )
            continue
        for tag in page.robots_els:
            content = (tag.get("content") or "").lower()
            if "noindex" in content or "none" in [part.strip() for part in content.split(",")]:
                fixed = ", ".join(part.strip().replace("noindex", "index") for part in content.split(",") if part.strip() and part.strip() != "none") or "index, follow"
                issues.append(
                    make_issue(
                        "noindex", f"{page.path} tells Google not to index it", f"The {tag.get('name', 'robots')} tag says \"{tag.get('content', '')}\". Google drops the page from results while it stays.",
                        page=page, el=tag, attr="content", suggestion=fixed, fix="Change noindex to index, or remove the tag, if this page should show in Google.",
                    )
                )
        header = str({key.lower(): value for key, value in (page.headers or {}).items()}.get("x-robots-tag", ""))
        if "noindex" in header.lower():
            issues.append(make_issue("noindex", f"{page.path} sends a noindex header", f"The server sends X-Robots-Tag: {header}. Google won't index the page.", page=page, fix="Remove noindex from the X-Robots-Tag header in the server or CDN settings.", key="header"))
        if page.http_status and 300 <= page.http_status < 400:
            issues.append(make_issue("unreadable", f"{page.path} redirects", f"It answered with HTTP {page.http_status}. Link to the final address so Google doesn't have to follow the hop.", page=page.url, fix="Update links and the sitemap to the final URL.", severity="low", key="redirect"))
        canonical = page.canonical
        if canonical and url_key(canonical) != url_key(page.url) and ctx.is_alias(page):
            if ctx.aliases[url_key(page.url)] is None:  # it names a page we did not read: say so, never call it wrong
                issues.append(
                    make_issue(
                        "canonical-other", f"{page.path} names another page as the original",
                        f"The canonical tag names {canonical}, which we did not read. If {page.path} is a copy of it, that is right and nothing else is checked here.",
                        page=page, el=page.canonical_el, attr="href", severity="low", key="unread",
                        fix="Nothing to do if this page is a copy. If it is its own page, point the canonical at its own URL.",
                    )
                )
            continue  # an alias of the page it names: the canonical is right, and that page carries the issues
        if canonical and url_key(canonical) != url_key(page.url):
            issues.append(
                make_issue(
                    "canonical-other", f"{page.path} points its canonical at another page",
                    f"The canonical tag names {canonical}, which shows different content. Google treats that page as the original and may never show {page.path}.",
                    page=page, el=page.canonical_el, attr="href", suggestion=page.url,
                    fix="Point the canonical at this page's own URL unless it really is a copy.",
                )
            )
        elif not page.canonical_el:
            issues.append(
                make_issue(
                    "canonical-missing", f"{page.path} has no canonical tag", "Without one, Google picks the main URL itself when the page is reachable at several addresses.",
                    page=page, el=page.head, suggestion=f'<link rel="canonical" href="{page.url}">', action="append",
                    fix="Add a canonical link to the page's own URL in the head.",
                )
            )
    return _sitewide(
        issues, "canonical-missing", ctx, "{count} pages have no canonical tag",
        "{count} pages have no canonical tag, so Google picks the main address itself when a page is reachable at several.",
        "Add a canonical link to each page's own URL in the site's page template.",
    )


@check
def check_script_built(ctx: AuditContext) -> list[dict]:
    """Pages whose HTML is nearly empty because a script draws them. A crawler that doesn't run the script, and
    Google's first pass, see only the HTML."""
    issues = []
    for page in ctx.ok_docs:
        visible = page.visible_words
        if visible >= SCRIPT_BUILT_WORDS:
            continue
        heavy = page.script_bytes >= 2000 or any(tag.has_attr("src") for tag in page.tags("script"))
        if not heavy:
            continue  # no scripts: a short page, not one a script draws
        roots = [found for selector in APP_ROOTS for found in [page.soup.select_one(selector)] if found is not None]
        empty_root = any(len(words_in(" ".join(piece for piece in root.find_all(string=True) if piece.parent.name not in SKIP_TAGS))) < 10 for root in roots)
        hidden = page.hidden_words
        block = max(page.hidden_blocks, key=lambda tag: len(words_in(tag.get_text(" "))), default=None)
        crawl_copy = block is not None and hidden >= 20 and hidden >= 2 * visible and len(words_in(block.get_text(" "))) >= 0.6 * hidden
        if not (crawl_copy or (empty_root and visible < 20)):
            continue
        if not crawl_copy:
            block = None
        where = None
        if block is not None:
            classes = ".".join(block.get("class") or [])
            where = f"{block.name}.{classes}" if classes else block.name
        if hidden and block is not None:
            comment = (
                f"Browsers build {page.path} with scripts. Its HTML shows {plural(visible, 'word')} to a reader, and the real text "
                f"({hidden} words) sits in a hidden {where} block. Google may treat hidden text as less important, and "
                "tools that don't run scripts see an empty page."
            )
        else:
            comment = f"Browsers build {page.path} with scripts. Its HTML has only {plural(visible, 'word')}, so Google's first pass and tools that don't run scripts see an empty page."
        issues.append(
            make_issue(
                "script-built", f"{page.path} is drawn by scripts", comment, page=page, el=block or page.main,
                fix="Render the page's text into the HTML on the server (prerender or server rendering), and make the crawl copy the visible page instead of a hidden block.",
                evidence=[f"visible words: {visible}", f"hidden words: {hidden}", f"script bytes: {page.script_bytes}"],
            )
        )
    return issues


# -- checks: title and meta description ------------------------------------------------------------


@check
def check_titles(ctx: AuditContext) -> list[dict]:
    issues = []
    brand = ctx.brand
    by_title: dict[str, list[PageDoc]] = defaultdict(list)
    for page in ctx.content_docs:
        title = page.title
        target = ctx.target(page)
        if title and ctx.indexed(page):
            by_title[title.lower()].append(page)
        if not title:
            lead = page.h1 or (cased(target, page.text) if target else "")
            issues.append(
                make_issue(
                    "title-missing", f"{page.path} has no title", "Google shows the title as the blue link. Without one it makes one up from the page.",
                    page=page, el=page.title_el or page.head, suggestion=compose_title(lead, "", brand) if lead else None,
                    action="text" if page.title_el else "append",
                    fix="Add a title that starts with the search this page should win.",
                )
            )
            if issues[-1].get("action") == "append" and issues[-1].get("suggestion"):
                issues[-1]["suggestion"] = f"<title>{issues[-1]['suggestion']}</title>"
            continue
        width = pixels(title)
        core = core_title(title, brand)
        if width > TITLE_PIXELS and pixels(core) > TITLE_PIXELS - 20:  # a cut that only loses the brand is fine
            suggestion = compose_title(core, "", brand) or clean_cut(core, TITLE_MAX)
            issues.append(
                make_issue(
                    "title-long", f"Title is {len(title)} characters, too wide for Google",
                    f"Google shows about {TITLE_PIXELS} pixels of a title, so \"{visible_title(title)}...\" is roughly what people see for {page.path}.",
                    page=page, el=page.title_el, suggestion=suggestion if suggestion and suggestion != title and pixels(suggestion) <= TITLE_PIXELS else None,
                    severity="medium" if width > TITLE_PIXELS * 1.2 else "low",
                    fix="Move the main search to the front and cut the rest.",
                )
            )
        elif len(title) < TITLE_MIN:
            lead = cased(target, page.text) if target and not covers(target, title) else (page.h1 if page.h1 and page.h1.lower() not in title.lower() else "")
            suggestion = compose_title(lead, core_title(title, brand), brand) if lead else None
            issues.append(
                make_issue(
                    "title-short", f"Title is only {len(title)} characters", f"\"{title}\" leaves room to say what {page.path} is about.",
                    page=page, el=page.title_el, suggestion=suggestion, fix="Add the main search this page should rank for.",
                )
            )
        if target and not covers(target, title) and not ctx.utility(page):
            lead = cased(target, page.text)
            issues.append(
                make_issue(
                    "title-search", f"Title doesn't name \"{target}\"", f"{page.path} is your page for \"{target}\" in Google, but its title is missing {missing_text(target, title)}. The title is the strongest on-page signal.",
                    page=page, search=target, el=page.title_el, suggestion=natural_title(page, target, brand),
                    fix="Work the search into the start of the title in plain words, then the brand."
                )
            )
    for _title, group in by_title.items():
        if len(group) < 2:
            continue
        for page in group:
            others = [other.path for other in group if other is not page]
            lead = page.h1 if page.h1 and page.h1.lower() != page.title.lower() else ""
            suggestion = compose_title(lead, "", ctx.brand) if lead else None
            if suggestion and any(suggestion.lower() == (compose_title(other.h1, "", ctx.brand) or "").lower() for other in group if other is not page and other.h1):
                suggestion = None
            issues.append(
                make_issue(
                    "title-duplicate", f"{page.path} shares its title with {plural(len(others), 'other page')}",
                    f"{page.path} and {', '.join(others[:3])} all use \"{short(page.title, 60)}\". Google may show only one of them.",
                    page=page, el=page.title_el, suggestion=suggestion, evidence=others, fix="Give each page its own title for its own search.",
                )
            )
    return issues


def good_meta(text: str | None) -> str | None:
    """A description worth pasting: 70 to 160 characters, whole sentences, no "...", no dashes from banners."""
    text = squash(text or "")
    if not text or "\u2013" in text or "\u2014" in text or text.endswith(("...", "\u2026")):
        return None
    trimmed = trim_sentences(text, META_TARGET)
    if not trimmed or trimmed.endswith(("...", "\u2026")) or not META_MIN <= len(trimmed) <= META_MAX:
        return None
    return trimmed


def _meta_from_page(page: PageDoc) -> str | None:
    """The page's own words for a description: og:description when it differs, else a paragraph from the main
    content (never the header, nav, footer, a banner or an announcement bar)."""
    og = page.og("og:description")
    if og is not None:
        found = good_meta(og.get("content", ""))
        if found and found.lower() != page.meta.lower():
            return found
    for text in page.paragraphs(min_words=10, main_only=True):
        found = good_meta(text)
        if found:
            return found
    return None


@check
def check_meta(ctx: AuditContext) -> list[dict]:
    issues = []
    by_meta: dict[str, list[PageDoc]] = defaultdict(list)
    for page in ctx.content_docs:
        meta = page.meta
        if not meta:
            suggestion = _meta_from_page(page)
            issues.append(
                make_issue(
                    "meta-missing", f"{page.path} has no meta description", "Google pulls a line from the page for the snippet, often not the line you want.",
                    page=page, el=page.meta_el or page.head, attr="content" if page.meta_el else None,
                    suggestion=(suggestion if page.meta_el else f'<meta name="description" content="{_html_attr(suggestion)}">') if suggestion else None,
                    action="attr" if page.meta_el else "append",
                    fix="Write one or two sentences, about 150 characters, that answer the search.",
                )
            )
            continue
        if ctx.indexed(page):
            by_meta[meta.lower()].append(page)
        if len(meta) > META_MAX:
            issues.append(
                make_issue(
                    "meta-long", f"Meta description is {len(meta)} characters", f"Google cuts it near {META_MAX}, so the end of the description of {page.path} won't show.",
                    page=page, el=page.meta_el, attr="content", suggestion=good_meta(undash(keep_sentences(meta, META_TARGET) or clean_cut(meta, META_TARGET))), fix="Shorten it and put the point first.",
                )
            )
        elif len(meta) < META_MIN:
            extra = _meta_from_page(page)
            suggestion = None
            if extra and not covers(extra, meta, 0.6):
                suggestion = good_meta(f"{meta.rstrip('.')}. {extra}") or extra
            issues.append(
                make_issue(
                    "meta-short", f"Meta description is only {len(meta)} characters", f"\"{meta}\" uses less than half the room Google gives the snippet for {page.path}.",
                    page=page, el=page.meta_el, attr="content", suggestion=suggestion, fix="Add what the reader gets on the page, in about 150 characters.",
                )
            )
    for _meta, group in by_meta.items():
        if len(group) < 2:
            continue
        for page in group:
            others = [other.path for other in group if other is not page]
            suggestion = _meta_from_page(page)
            issues.append(
                make_issue(
                    "meta-duplicate", f"{page.path} shares its meta description with {plural(len(others), 'other page')}",
                    f"{page.path} and {', '.join(others[:3])} use the same description, so the snippets don't say how the pages differ.",
                    page=page, el=page.meta_el, attr="content", suggestion=suggestion if suggestion and suggestion.lower() != page.meta.lower() else None,
                    evidence=others, fix="Write a description for each page about that page.",
                )
            )
    return issues


def _html_attr(text: str | None) -> str:
    return (text or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _html_text(text: str | None) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# -- checks: headings and content ----------------------------------------------------------------


@check
def check_headings(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.content_docs:
        h1s = page.h1s
        target = ctx.target(page)
        if not h1s:
            lead = core_title(page.title, ctx.brand) if page.title else (cased(target, page.text) if target else "")
            issues.append(
                make_issue(
                    "h1-missing", f"{page.path} has no H1", "The main heading tells Google and readers what the page is about.",
                    page=page, el=page.main, suggestion=f"<h1>{_html_text(lead)}</h1>" if lead else None, action="prepend",
                    fix="Add one H1 at the top that matches the title's main search.",
                )
            )
        else:
            for extra in h1s[1:]:
                issues.append(
                    make_issue(
                        "h1-many", f"{len(h1s)} H1 headings on {page.path}", f"\"{short(text_of(extra), 60)}\" is a second main heading, which blurs the page's topic.",
                        page=page, el=extra, suggestion="h2", action="retag", fix="Keep one H1 and turn the others into H2s.",
                    )
                )
            if target and not covers(target, page.h1) and not ctx.utility(page):
                lead = cased(target, page.text)
                suggestion = add_missing(target, page.h1, page.text)
                share = len(missing_words(target, page.h1)) / max(1, len(tokens(target)))
                if suggestion is None and share >= 0.5 and len(lead) + len(page.h1) <= 70:
                    suggestion = f"{lead}: {page.h1}"
                issues.append(
                    make_issue(
                        "h1-search", f"H1 doesn't name \"{target}\"", f"The H1 on {page.path} says \"{short(page.h1, 70)}\" and is missing {missing_text(target, page.h1)}.",
                        page=page, search=target, el=h1s[0], suggestion=suggestion, fix="Work the search into the main heading in plain words.",
                    )
                )
        found = []
        last = 0
        for tag in page.headings:
            level = int(tag.name[1])
            if tag.find(["p", "div", "section", "article", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6"]) is not None:
                continue  # a heading wrapped around a whole block is broken markup, not an outline step
            if last and level > last + 1:
                found.append(
                    make_issue(
                        "heading-skip", f"Heading jumps from H{last} to H{level}", f"\"{short(text_of(tag), 60)}\" on {page.path} skips a level, which makes the outline harder for Google to follow.",
                        page=page, el=tag, suggestion=f"h{last + 1}", action="retag", fix=f"Make it an H{last + 1}, or add the missing level above it.",
                    )
                )
            last = level
        issues += _capped(found, page, "headings")
    return issues


@check
def check_content(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.ok_docs:
        if ctx.utility(page):
            continue  # legal, account and form pages are short on purpose
        words = page.content_words
        if words < THIN_WORDS and not (page.is_home and page.words >= THIN_WORDS):
            issues.append(
                make_issue(
                    "thin", f"{page.path} has {plural(words, 'word')} of content", f"Not counting the menu, header and footer, {page.path} has {plural(words, 'word')}. Pages under {THIN_WORDS} rarely win competitive searches.",
                    page=page, el=page.main, fix="Add the details a buyer needs: prices, steps, comparisons, questions.",
                )
            )
        for row in ctx.searches_for(page):
            if (row.get("position") or 99) <= 3:
                continue  # already in the top 3 for it
            search = row["q"]
            rivals = ctx.top_docs(row, 3)
            mine = ctx.comparable_words(page, rivals)
            if len(rivals) >= 2 and not page.is_home:
                mid = int(median(ctx.comparable_words(doc, rivals) for doc in rivals))
                if mid >= 200 and mine < 0.65 * mid:
                    names = ", ".join(host_of(doc.url) for doc in rivals)
                    issues.append(
                        make_issue(
                            "thin-vs-top", f"{page.path} is thinner than the top pages for \"{search}\"",
                            f"The top pages we compared for \"{search}\" ({names}) have a median of {mid:,} words. {page.path} has {mine:,}.",
                            page=page, search=search, el=page.main, evidence=[f"{doc.url}: {ctx.comparable_words(doc, rivals):,} words" for doc in rivals],
                            fix=f"Cover what the top pages cover. Aim for about {int(mid * 0.8):,} words of useful detail, not filler.",
                        )
                    )
            heads = [tag for tag in page.headings if tag.name in {"h1", "h2", "h3"}]
            if not any(covers(search, text_of(tag), 0.6) for tag in heads):
                lead = cased(search, page.text)
                issues.append(
                    make_issue(
                        "no-answer", f"No section answers \"{search}\"", f"None of the {len(heads)} headings on {page.path} names \"{search}\", so Google has no section to match the search to.",
                        page=page, search=search, el=page.main,
                        fix=f"Add a section under a heading such as \"{lead}\", and answer the search in its first two sentences.",
                    )
                )
    return issues


# -- checks: links and images --------------------------------------------------------------------


def _label_for(url: str, ctx: AuditContext, page: PageDoc) -> str | None:
    target = ctx.page(url)
    if target is not None and target.ok:
        label = target.h1 or core_title(target.title, ctx.brand)
        if label:
            return short(label, 70)
    slug = unquote(_split(url).path.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"\.(html?|php|aspx?)$", "", slug)
    parts = [part for part in re.split(r"[-_]+", slug) if part]
    if len(parts) >= 2 and not any(re.fullmatch(r"[0-9a-f]{8,}|\d+", part) for part in parts):
        return cased(" ".join(parts), page.text)
    return None


@check
def check_links(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.ok_docs:
        internal = [(tag, url, label) for tag, url, label in page.links if page.is_internal(url)]
        content = {url_key(url) for tag, url, label in internal if not page.inside(tag, "nav", "header", "footer") and url_key(url) != url_key(page.url)}
        if len(content) < FEW_LINKS and page.content_words >= 80 and not ctx.utility(page):
            issues.append(
                make_issue(
                    "few-links", f"{page.path} links to {len(content)} of your other pages", f"Outside the menu and footer, {page.path} links to {plural(len(content), 'other page')}. Links in the text tell Google which pages matter and what they are about.",
                    page=page, el=page.main, fix="Link to two or three related pages from the body text, with words that describe them.",
                )
            )
        broken, generic = [], []
        seen = set()
        for tag, url, label in internal:
            status = ctx.link_status(url)
            bad = (isinstance(status, int) and status >= 400) or status in {"missing", "not-found"}
            if bad and (url_key(url), "broken") not in seen:
                seen.add((url_key(url), "broken"))
                broken.append(
                    make_issue(
                        "broken-link", f"Broken link to {path_of(url)}", f"\"{short(label, 50) or path_of(url)}\" on {page.path} goes to {path_of(url)}, which answered {status}.",
                        page=page, el=tag, attr="href", fix="Point the link at a page that works, or remove it.", evidence=[f"{url}: {status}"], key=url,
                    )
                )
        said = set()
        for tag, url, label in page.links:
            if not page.is_internal(url):
                continue  # the words on links to other sites describe them, not you
            if (url_key(url), label.lower()) in said:
                continue  # the desktop and phone menus repeat the same link
            if label.lower().strip(" .!>›→") in GENERIC_ANCHORS:
                said.add((url_key(url), label.lower()))
                suggestion = _label_for(url, ctx, page) if page.is_internal(url) else None
                generic.append(
                    make_issue(
                        "link-generic", f"Link text \"{label}\" says nothing", f"The link to {path_of(url) if page.is_internal(url) else url} on {page.path} reads \"{label}\". Google uses link words to learn what the target page is about.",
                        page=page, el=tag, suggestion=suggestion, fix="Use words that name the page you link to.", key=url,
                    )
                )
        issues += _capped(broken, page, "links") + _capped(generic, page, "links")
    return issues


def _decorative(tag: Tag, src: str) -> bool:
    """A small icon (an SVG, or 48 pixels or less) beside the words of a link, button or heading: its text already
    says it all, so alt text would only repeat it."""
    small = src.lower().split("?", 1)[0].endswith(".svg")
    for key in ("width", "height"):
        try:
            small = small or int(str(tag.get(key) or "0").rstrip("px")) <= 48 and bool(tag.get(key))
        except ValueError:
            pass
    if not small:
        return False
    for parent in tag.parents:
        if not isinstance(parent, Tag) or parent.name in {"body", "main", "section", "article"}:
            break
        if parent.name in {"a", "button", "h1", "h2", "h3", "h4", "h5", "h6", "li", "label"} or len(parent.contents) > 1:
            return bool(text_of(parent))
    return False


def _image_width(tag: Tag, src: str) -> int:
    best = 0
    for item in (tag.get("srcset") or "").split(","):
        match = re.search(r"\s(\d+)w\s*$", item.strip())
        if match:
            best = max(best, int(match.group(1)))
    hint = SIZE_HINT.search(src) or None
    if hint:
        best = max(best, int(hint.group(1)))
    else:
        match = WIDTH_HINT.search(src)
        value = int(match.group(1) or match.group(2)) if match else 0
        if 1000 <= value <= 10000:
            best = max(best, value)
    return best


@check
def check_images(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.content_docs:
        missing, huge = [], []
        for tag in page.images:
            src = (tag.get("src") or tag.get("data-src") or "").strip()
            if tag.get("width") in {"1", "0"} or (tag.get("role") or "") == "presentation" or (tag.get("aria-hidden") or "") == "true":
                continue
            if not tag.has_attr("alt") and not _decorative(tag, src):
                name = unquote(_split(src).path.rsplit("/", 1)[-1]) if src and not src.startswith("data:") else ""
                stem = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
                parts = [part for part in re.split(r"[-_ .]+", stem) if part and not re.fullmatch(r"\d+(x\d+)?|[0-9a-f]{6,}|img|image|photo|pic|dsc|copy|final|scaled|w\d+|\d+w|v\d+", part, flags=re.I)]
                suggestion = cased(" ".join(parts), page.text) if len(parts) >= 2 else None
                missing.append(
                    make_issue(
                        "img-alt", "Image has no alt text", f"The image {name or 'here'} on {page.path} has no alt attribute, so Google and screen readers can't tell what it shows.",
                        page=page, el=tag, attr="alt", suggestion=suggestion, key=src,
                        fix="Describe what the image shows in a few words. Use alt=\"\" only for decoration.",
                    )
                )
            absolute = safe_join(page.url, src) if src else ""
            size = page.image_bytes.get(absolute) or page.image_bytes.get(src)
            width = _image_width(tag, src)
            sized = tag.has_attr("width") and tag.has_attr("height")
            styled = re.search(r"(^|;)\s*(width|height|aspect-ratio)\s*:", tag.get("style") or "")
            responsive = tag.has_attr("sizes") and tag.has_attr("srcset")
            if not sized and not styled and not (responsive and not size) and ((size and size >= HUGE_IMAGE_BYTES) or width >= HUGE_IMAGE_WIDTH):
                facts = []
                if size:
                    facts.append(f"{size // 1000:,} KB")
                if width:
                    facts.append(f"{width:,} pixels wide")
                huge.append(
                    make_issue(
                        "img-huge", "Large image with no width and height", f"The image {short(_split(src).path.rsplit('/', 1)[-1], 60)} on {page.path} is {' and '.join(facts)} and sets no size, so the page jumps while it loads and loads slowly on phones.",
                        page=page, el=tag, key=src, fix="Add width and height attributes, serve a smaller version, and lazy-load it if it is below the fold.",
                    )
                )
        issues += _capped(missing, page, "images") + _capped(huge, page, "images")
    return issues


# -- checks: structured data, social tags, mobile, URL ---------------------------------------------


def _faq_jsonld(page: PageDoc) -> str | None:
    """FAQPage JSON-LD from the page's own question headings and the paragraph under each. None if fewer than 2."""
    pairs = []
    for tag in page.headings:
        question = text_of(tag)
        if not question.endswith("?") or len(question) > 160:
            continue
        answer = []
        for sibling in tag.find_next_siblings():
            if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
                break
            text = squash(sibling.get_text(" ")) if isinstance(sibling, Tag) else ""
            if text:
                answer.append(text)
            if len(" ".join(answer)) > 400:
                break
        if answer:
            pairs.append((question, short(" ".join(answer), 500)))
    if len(pairs) < 2:
        return None
    data = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs[:10]]}
    return _ld_script(data)


def _ld_script(data: dict) -> str:
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def _origin(url: str) -> str:
    parts = _split(url)
    return f"{parts.scheme}://{parts.netloc}/"


def _org_jsonld(page: PageDoc, brand: str) -> str | None:
    if not brand:
        return None
    return _ld_script({"@context": "https://schema.org", "@type": "Organization", "name": brand, "url": _origin(page.url)})


def _basic_jsonld(page: PageDoc, brand: str) -> str | None:
    if page.is_home and brand:
        return _ld_script(
            {"@context": "https://schema.org", "@graph": [
                {"@type": "Organization", "name": brand, "url": _origin(page.url)},
                {"@type": "WebSite", "name": brand, "url": _origin(page.url)},
            ]}
        )
    if not page.title:
        return None
    data = {"@context": "https://schema.org", "@type": "WebPage", "name": core_title(page.title, brand), "url": page.url}
    if page.meta:
        data["description"] = page.meta
    return _ld_script(data)


def _repair_json(raw: str) -> str | None:
    candidate = re.sub(r",\s*([}\]])", r"\1", raw)
    candidate = re.sub(r"(?<!:)//[^\n\"]*$", "", candidate, flags=re.M) if candidate == raw else candidate
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    return json.dumps(data, ensure_ascii=False, indent=2) if schema_types(data) else None


@check
def check_structured_data(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.ok_docs:
        for index, entry in enumerate(page.jsonld):
            if entry["error"]:
                issues.append(
                    make_issue(
                        "schema-invalid", f"Structured data on {page.path} is broken", f"JSON-LD block {index + 1} can't be read: {entry['error']}. Google ignores the whole block.",
                        page=page, el=entry["el"], suggestion=_repair_json(entry["raw"]) if entry["raw"] else None, key=str(index),
                        fix="Fix the JSON so it parses, and check it with Google's Rich Results Test.",
                    )
                )
        if not page.types and not ctx.utility(page):
            issues.append(
                make_issue(
                    "schema-missing", f"{page.path} has no structured data", "Structured data tells Google what the page is: a company, a product, an article, questions and answers.",
                    page=page, el=page.head, suggestion=_basic_jsonld(page, ctx.brand), action="append",
                    fix="Add JSON-LD for what the page is. Only describe things the page really says.",
                )
            )
        gaps: dict[str, list[tuple[str, list[PageDoc]]]] = defaultdict(list)
        for row in ctx.searches_for(page):
            if (row.get("position") or 99) <= 3:
                continue
            rivals = ctx.top_docs(row, 3)
            if len(rivals) < 2:
                continue
            counts = Counter(kind for doc in rivals for kind in set(doc.types) & set(GAP_TYPES))
            for kind, count in counts.items():
                if count >= 2 and kind not in page.types and not (kind == "BreadcrumbList" and page.is_home):
                    gaps[kind].append((row["q"], [doc for doc in rivals if kind in doc.types]))
        for kind, seen in gaps.items():
            search, users = seen[0]
            suggestion = _faq_jsonld(page) if kind == "FAQPage" else _org_jsonld(page, ctx.brand) if kind == "Organization" else None
            has = sorted(set(page.types) & set(SCHEMA_TYPES))
            compared = len(ctx.top_docs(next(row for row in ctx.rankings if row.get("q") == search), 3))
            issues.append(
                make_issue(
                    "schema-gap", f"Top pages use {kind} schema and {page.path} doesn't",
                    f"For \"{search}\", {len(users)} of the {compared} top pages we compared mark up {kind}. {page.path} has " + (f"{', '.join(has)}." if has else ("other structured data." if page.types else "no structured data.")),
                    page=page, search=search, el=page.head, suggestion=suggestion, action="append" if suggestion else None, key=kind,
                    evidence=[f"searches: {', '.join(q for q, _ in seen)}"] + [doc.url for doc in users],
                    fix=f"Add {kind} JSON-LD that matches what the page shows." + (" Write the questions and answers on the page first." if kind == "FAQPage" and not suggestion else ""),
                )
            )
    return _sitewide(
        issues, "schema-missing", ctx, "{count} pages have no structured data",
        "{count} pages have no structured data, so Google has to guess what each one is: a product, an article, a company.",
        "Add JSON-LD for what each kind of page is in its template. Only describe things the pages really say.",
    )


@check
def check_social(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.ok_docs:
        if ctx.utility(page):
            continue
        missing = [prop for prop in ("og:title", "og:description", "og:image") if page.og(prop) is None]
        if missing:
            tags = []
            if "og:title" in missing and page.title:
                tags.append(f'<meta property="og:title" content="{_html_attr(page.title)}">')
            if "og:description" in missing and page.meta:
                tags.append(f'<meta property="og:description" content="{_html_attr(page.meta)}">')
            if "og:image" in missing:
                first = next((tag for tag in page.images if (tag.get("src") or "").strip() and not tag["src"].startswith("data:")), None)
                if first is not None:
                    tags.append(f'<meta property="og:image" content="{_html_attr(safe_join(page.url, first["src"].strip()))}">')
            issues.append(
                make_issue(
                    "og-missing", f"{page.path} is missing {', '.join(missing)}", ("When someone shares the page, Slack, LinkedIn and Facebook build the preview from Open Graph tags. Without them the preview is blank or wrong." if len(missing) == 3 else f"Shared links build their preview from Open Graph tags, and this page has no {' or '.join(missing)}, so the preview is missing that part."),
                    page=page, el=page.head, suggestion="\n".join(tags) or None, action="append", fix=f"Add {', '.join(missing)} to the head.",
                )
            )
        if page.og("twitter:card") is None:
            card = "summary_large_image" if page.og("og:image") is not None else "summary"
            issues.append(
                make_issue(
                    "twitter-missing", f"{page.path} has no Twitter card tag", "Without twitter:card, X shows a plain link instead of a preview.",
                    page=page, el=page.head, suggestion=f'<meta name="twitter:card" content="{card}">', action="append", fix="Add a twitter:card meta tag.",
                )
            )
    return issues


@check
def check_mobile(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.ok_docs:
        tag = page.viewport_el
        if tag is None:
            issues.append(
                make_issue(
                    "viewport-missing", f"{page.path} has no viewport tag", "Without it, phones show the desktop layout zoomed out. Google ranks with the phone version of the page.",
                    page=page, el=page.head, suggestion='<meta name="viewport" content="width=device-width, initial-scale=1">', action="append",
                    fix="Add the viewport meta tag to the head.",
                )
            )
        elif "width=device-width" not in (tag.get("content") or "").replace(" ", "").lower():
            issues.append(
                make_issue(
                    "viewport-missing", f"{page.path} has a fixed-width viewport", f"The viewport says \"{tag.get('content', '')}\", so phones don't get a layout that fits.",
                    page=page, el=tag, attr="content", suggestion="width=device-width, initial-scale=1", severity="low", key="fixed",
                    fix="Set the viewport to width=device-width.",
                )
            )
    return issues


@check
def check_urls(ctx: AuditContext) -> list[dict]:
    issues = []
    for page in ctx.content_docs:
        parts = _split(page.url)
        path = unquote(parts.path or "/")
        problems = []
        segments = [part for part in path.split("/") if part and not LANGUAGE_CODE.fullmatch(part)]
        if any(ch.isupper() for ch in "/".join(segments)):
            problems.append("capital letters")
        if "_" in path:
            problems.append("underscores")
        if " " in path or re.search(r"[^\x00-\x7f]", path):
            problems.append("spaces or special characters")
        if "//" in path:
            problems.append("a double slash")
        if parts.query:
            problems.append("a query string")
        if len(page.url) > URL_MAX:
            problems.append(f"{len(page.url)} characters")
        if path.strip("/").count("/") >= 4:
            problems.append(f"{path.strip('/').count('/') + 1} folders deep")
        if not problems:
            continue
        clean = re.sub(r"/{2,}", "/", path.lower().replace("_", "-").replace(" ", "-"))
        clean = re.sub(r"-{2,}", "-", clean)
        suggestion = f"{parts.scheme}://{parts.netloc}{clean}" if clean != path or parts.query else None
        issues.append(
            make_issue(
                "url-hygiene", f"{page.path} has a messy address", f"The URL has {', '.join(problems)}. Short lower-case addresses with hyphens are easier to share and to rank.",
                page=page.url, suggestion=suggestion, action="copy" if suggestion else None, evidence=problems,
                fix="Only change a URL that has no rankings yet, or add a 301 redirect from the old address.",
            )
        )
    return issues


# -- checks: rankings per search --------------------------------------------------------------------


@check
def check_rankings(ctx: AuditContext) -> list[dict]:
    issues = []
    for row in ctx.rankings:
        if not row.get("checked"):
            continue
        search = row["q"]
        leaders = ctx.leaders(row)
        names = ", ".join(item.get("domain") or host_of(item.get("url", "")) for item in leaders) or "other sites"
        page = ctx.ranking_page(row.get("url"))
        facts = ctx._facts_by_key.get(url_key(row.get("url") or "")) if page is None else None
        if row.get("position") is None:
            if facts is not None:
                issues.append(
                    make_issue(
                        "not-ranking", f"Not in the top 10 for \"{search}\"", f"{facts.path} is about this search but isn't on the first page. {names} hold the top 3.",
                        page=facts.url, search=search, evidence=[f"{item.get('position')}. {item.get('url')}" for item in leaders],
                        fix="Open it in the editor, close the gaps, and test the change before you publish.",
                    )
                )
                continue
            if page is not None and page.ok:
                rivals = ctx.top_docs(row, 3)
                notes = _gaps(ctx, page, rivals, search)
                detail = f" Compared with the top pages we have stored, {'; '.join(notes)}." if notes else ""
                issues.append(
                    make_issue(
                        "not-ranking", f"Not in the top 10 for \"{search}\"", f"{page.path} is about this search but isn't on the first page. {names} hold the top 3.{detail}",
                        page=page, search=search, el=page.title_el if notes and "title" in notes[-1] else page.main,
                        evidence=[f"{item.get('position')}. {item.get('url')}" for item in leaders],
                        fix="Open it in the editor, close the gaps above, and test the change before you publish." if notes else "Open it next to the top pages in the editor, add what they cover, and test the change before you publish.",
                    )
                )
            else:
                issues.append(
                    make_issue(
                        "no-page", f"No page for \"{search}\"", f"No page on your site targets this search, and you aren't in the top 10. {names} hold the top 3.",
                        search=search, evidence=[f"{item.get('position')}. {item.get('url')}" for item in leaders],
                        fix="Write a page for it and test it in the editor before you publish.",
                    )
                )
            continue
        if page is None or not page.ok or row["position"] <= 3:
            continue
        rivals = ctx.top_docs(row, 3)
        notes = _gaps(ctx, page, rivals, search)
        detail = "; ".join(notes) if notes else f"{names} are ahead of you"
        title_gap = any("title" in note for note in notes)
        issues.append(
            make_issue(
                "behind", f"#{row['position']} for \"{search}\"", f"{page.path} is on page one, but {detail}.",
                page=page, search=search, el=page.title_el if title_gap else page.main,
                suggestion=natural_title(page, search, ctx.brand) if title_gap and page.title_el is not None else None,
                evidence=[f"{item.get('position')}. {item.get('url')}" for item in leaders],
                fix="Open it in the editor, make the change, and test it.",
            )
        )
    return issues


def _gaps(ctx: AuditContext, page: PageDoc, rivals: list[PageDoc], search: str) -> list[str]:
    """What the top pages we compared have that this page lacks, for one search. Title and H1 words are only
    suggested when the page is not already in the top 3 for another search."""
    notes = []
    if rivals:
        names = ", ".join(host_of(doc.url) for doc in rivals)
        mid = median(ctx.comparable_words(doc, rivals) for doc in rivals)
        mine = ctx.comparable_words(page, rivals)
        if mid and mine < 0.65 * mid and not page.is_home:
            notes.append(f"the top pages we compared ({names}) have a median of {int(mid):,} words and yours has {mine:,}")
        tables = sum(1 for doc in rivals if doc.tables)
        if tables >= 2 and not page.tables:
            notes.append(f"{tables} of those pages have a table and yours doesn't")
        faqs = sum(1 for doc in rivals if doc.has_faq)
        if faqs >= 2 and not page.has_faq:
            notes.append(f"{faqs} of those pages answer common questions in an FAQ")
        kinds = Counter(kind for doc in rivals for kind in set(doc.types) & set(GAP_TYPES))
        lacking = sorted(kind for kind, count in kinds.items() if count >= 2 and kind not in page.types and kind != "FAQPage" and not (kind == "BreadcrumbList" and page.is_home))
        if lacking:
            notes.append(f"the top pages mark up {', '.join(lacking)} and yours doesn't")
    if ctx.settled(page) or ctx.utility(page):
        return notes
    if page.h1 and not covers(search, page.h1):
        notes.append(f"your H1 is missing {missing_text(search, page.h1)}")
    if missing_words(search, page.title):
        notes.append(f"your title is missing {missing_text(search, page.title)}")
    return notes


# -- pages known only by their stored facts ----------------------------------------------------------


@check
def check_stored_facts(ctx: AuditContext) -> list[dict]:
    """Page-level checks for pages whose HTML is not at hand (an old caller passing SitePage rows only). The
    element checks above cover every page that has HTML."""
    issues = []
    titles: dict[str, list[SitePage]] = defaultdict(list)
    for page in ctx.facts_only:
        url, where = page.url, page.path
        if page.title:
            titles[page.title.strip().lower()].append(page)
        if not page.title:
            issues.append(make_issue("title-missing", f"{where} has no title", "Google shows the title as the blue link. Without one it makes one up.", page=url, fix="Add a title that starts with the search this page should win."))
        elif len(page.title) > TITLE_MAX:
            issues.append(make_issue("title-long", f"Title is {len(page.title)} characters", f"Google shows about {TITLE_MAX}, so \"{short(page.title, TITLE_MAX)}\" is roughly what people see for {where}.", page=url, fix="Move the main search to the front and cut the rest."))
        elif len(page.title) < TITLE_MIN:
            issues.append(make_issue("title-short", f"Title is only {len(page.title)} characters", f"\"{page.title}\" leaves room to say what {where} is about.", page=url, fix="Add the main search this page should rank for."))
        if not page.meta:
            issues.append(make_issue("meta-missing", f"{where} has no meta description", "Google pulls a line from the page for the snippet, often not the line you want.", page=url, fix="Write one or two sentences, about 150 characters, that answer the search."))
        elif len(page.meta) > META_MAX:
            issues.append(make_issue("meta-long", f"Meta description is {len(page.meta)} characters", f"Google cuts it near {META_MAX}, so the end of the description of {where} won't show.", page=url, fix="Shorten it and put the point first."))
        if not page.h1:
            issues.append(make_issue("h1-missing", f"{where} has no H1", "The main heading tells Google and readers what the page is about.", page=url, fix="Add one H1 at the top that matches the title's main search."))
        elif page.h1_count > 1:
            issues.append(make_issue("h1-many", f"{page.h1_count} H1 headings on {where}", "More than one main heading blurs the page's topic.", page=url, fix="Keep one H1 and turn the others into H2s."))
        if page.words < THIN_WORDS:
            issues.append(make_issue("thin", f"{where} has {page.words} words", f"Pages under {THIN_WORDS} words rarely win competitive searches. {where} has {page.words}.", page=url, fix="Add the details a buyer needs: prices, steps, comparisons, questions."))
    for group in titles.values():
        for page in group if len(group) > 1 else []:
            others = [other.path for other in group if other is not page]
            issues.append(make_issue("title-duplicate", f"{page.path} shares its title with {plural(len(others), 'other page')}", f"{page.path} and {', '.join(others[:3])} all use \"{short(page.title, 50)}\". Google may show only one of them.", page=page.url, evidence=others, fix="Give each page its own title for its own search."))
    return issues


# -- running the audit ---------------------------------------------------------------------------


def plugin_checks() -> list[CheckFn]:
    """Checks other plugins add through the audit_checks hook (non-callables are logged and skipped)."""
    from aeo.ranksim import plugins

    found = []
    for run in plugins.collect("audit_checks"):
        if not callable(run):
            log.warning("ranksim plugins: audit check %r is not callable; skipped.", run)
            continue
        found.append(run)
    return found


def _plugin_issues(ctx: AuditContext) -> list[dict]:
    """Issues from the checks plugins return from audit_checks. A check that raises or returns a bad issue is
    logged and skipped; it never breaks the audit."""
    from aeo.ranksim import plugins

    issues: list[dict] = []
    for run in plugin_checks():
        label = f"audit check {getattr(run, '__module__', '?')}.{getattr(run, '__qualname__', repr(run))}"
        ok, found = plugins.guarded(label, run, ctx)
        if not ok:
            continue
        for item in found or []:
            issue = _plugin_issue(item)
            if issue is None:
                log.warning("ranksim plugins: %s returned an issue without a severity (high, medium, low), kind and title; skipped.", label)
                continue
            problem = plugins.json_error(issue)
            if problem is not None:
                log.warning("ranksim plugins: %s returned an issue that is not JSON (%s); skipped.", label, problem)
                continue
            cleaned = _clean_issue(issue)
            if cleaned is not None:
                issues.append(cleaned)
    return issues


def _plugin_issue(item) -> dict | None:
    if not isinstance(item, dict) or item.get("severity") not in SEVERITY_ORDER:
        return None
    if not isinstance(item.get("kind"), str) or not isinstance(item.get("title"), str):
        return None
    issue = dict(item)
    for key, default in (("comment", ""), ("fix", ""), ("page", None), ("search", None)):
        issue.setdefault(key, default)
    if not issue.get("id") or not isinstance(issue["id"], str):
        element = issue.get("element")
        selector = element.get("selector") if isinstance(element, dict) else None
        issue["id"] = _issue_id(issue["kind"], issue["page"] if isinstance(issue["page"], str) else None, issue["search"] if isinstance(issue["search"], str) else None, selector if isinstance(selector, str) else None)
    return issue


TEMPLATE_NOTE = re.compile(r" The same element is on \d+ pages, so it is likely one fix in a shared template\.$")


def _merge_template(issues: list[dict]) -> list[dict]:
    """The same element with the same problem and the same fix on many pages (a footer link, a logo) is one
    issue that lists the pages. Safe to run again on its own output."""
    out: list[dict] = []
    groups: dict[tuple, dict] = {}
    for issue in issues:
        element = issue.get("element") or {}
        pages = [url for url in (issue.get("pages") or []) if isinstance(url, str)] or ([issue["page"]] if issue.get("page") else [])
        if issue["kind"] not in TEMPLATE_KINDS or not element.get("selector") or not pages:
            out.append(issue)
            continue
        key = (issue["kind"], element.get("selector"), element.get("text"), issue.get("suggestion"), issue.get("search"))
        first = groups.get(key)
        if first is None:
            groups[key] = issue
            issue["pages"] = list(dict.fromkeys(pages))
            out.append(issue)
            continue
        first["pages"] += [url for url in pages if url not in first["pages"]]
    for issue in groups.values():
        count = len(issue["pages"])
        issue["comment"] = TEMPLATE_NOTE.sub("", issue["comment"])
        if count > 1:
            issue["count"] = count
            issue["comment"] += f" The same element is on {count} pages, so it is likely one fix in a shared template."
        else:
            issue.pop("pages", None)
            issue.pop("count", None)
    return out


def _clean_issue(issue) -> dict | None:
    """Coerce one issue (possibly from a plugin) to the shape, or None when it can't be."""
    if not isinstance(issue, dict):
        return None
    kind, title = issue.get("kind"), issue.get("title")
    if not isinstance(kind, str) or not kind or title is None or title == "":
        return None
    issue["title"] = str(title)
    for key in ("comment", "fix"):
        issue[key] = "" if issue.get(key) is None else str(issue[key])
    for key in ("page", "search", "suggestion", "action"):
        value = issue.get(key)
        if value is not None and not isinstance(value, str):
            if key in {"suggestion", "action"}:
                issue.pop(key, None)
            else:
                issue[key] = None
        else:
            issue.setdefault(key, None) if key in {"page", "search"} else None
    element = issue.get("element")
    if element is not None and not (isinstance(element, dict) and isinstance(element.get("selector"), str) and element["selector"]):
        issue.pop("element", None)
    if "pages" in issue and not isinstance(issue["pages"], list):
        issue.pop("pages", None)
    if "evidence" in issue and not isinstance(issue["evidence"], list):
        issue["evidence"] = [str(issue["evidence"])]
    if issue.get("severity") not in SEVERITY_ORDER:
        issue["severity"] = RULES.get(kind, ("medium",))[0]
    rule = RULES.get(kind, (issue["severity"], 4, 2))
    for key, default, low, high in (("impact", rule[1], 1, 10), ("effort", rule[2], 1, 3)):
        try:
            issue[key] = min(high, max(low, int(issue.get(key, default))))
        except (TypeError, ValueError):
            issue[key] = default
    if not isinstance(issue.get("id"), str) or not issue["id"]:
        issue["id"] = make_issue(kind, issue["title"], "", page=issue.get("page"), search=issue.get("search"), key=issue["title"])["id"]
    return issue


def finalize(issues: list[dict]) -> list[dict]:
    """Drop broken and duplicate issues, merge template repeats, score, and sort: severity, then score, then
    ranking issues first, then title."""
    seen_ids: set[str] = set()
    seen_keys: set[tuple] = set()
    unique = []
    for raw in issues:
        issue = _clean_issue(raw)
        if issue is None:
            continue
        element = issue.get("element") or {}
        key = (issue["kind"], issue.get("page"), issue.get("search"), element.get("selector"), issue["title"])
        if issue["id"] in seen_ids or key in seen_keys:
            continue
        seen_ids.add(issue["id"])
        seen_keys.add(key)
        unique.append(issue)
    merged = _merge_template(unique)
    for issue in merged:
        weight = 1.0 + 0.1 * min(len(issue.get("pages") or [1]) - 1, 5)
        issue["score"] = round(issue["impact"] * weight / max(1, issue["effort"]), 1)
    merged.sort(key=_sort_key)
    return merged


def _run_builtin(ctx: AuditContext, checks: Iterable[CheckFn]) -> list[dict]:
    issues: list[dict] = []
    for run in checks:
        try:
            found = list(run(ctx) or [])
        except Exception:  # noqa: BLE001
            log.exception("audit check %s failed; running it page by page", getattr(run, "__name__", run))
            found = []
            for page in ctx.parsed:  # one odd page must not hide the check's issues on every other page
                single = AuditContext(ctx.project, [page], ctx.rankings, {}, html={}, robots_txt=ctx.robots_txt, statuses=ctx.statuses, brand_hint=ctx.brand)
                single._docs_by_key = ctx._docs_by_key
                try:
                    found.extend(run(single) or [])
                except Exception:  # noqa: BLE001
                    log.warning("audit check %s failed on %s", getattr(run, "__name__", run), page.url)
        issues.extend(found)
    return issues


def _sort_key(issue: dict):
    return (SEVERITY_ORDER[issue["severity"]], -issue.get("score", 0), issue["kind"] not in RANKING_KINDS, issue["title"], issue["id"])


_CACHE: dict[str, str] = {}  # digest -> the finalized built-in issues as JSON
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 16


def _cache_key(ctx: AuditContext) -> str | None:
    """A digest of everything the built-in checks read, or None when a page has no HTML string to hash."""
    try:
        digest = hashlib.sha1()
        for page in ctx.parsed:
            digest.update(f"{page.url}|{page.status}|{page.error}|{page.http_status}|{json.dumps(page.headers, sort_keys=True)}|{json.dumps(page.image_bytes, sort_keys=True)}|".encode())
            digest.update(hashlib.sha1((page.html or "").encode("utf-8", "replace")).digest())
        for url, doc in sorted(ctx._docs_by_key.items()):
            digest.update(f"{url}|{doc.status}|{doc.facts.words if doc.facts else ''}|{doc.facts.tables if doc.facts else ''}|{doc.facts.has_faq if doc.facts else ''}|".encode())
            digest.update(hashlib.sha1((doc.html or "").encode("utf-8", "replace")).digest())
        for page in ctx.facts_only:
            digest.update(json.dumps([page.url, page.title, page.meta, page.h1, page.h1_count, page.words]).encode())
        project = ctx.project
        digest.update(json.dumps([getattr(project, "domain", None), getattr(project, "site", None), getattr(project, "name", None)], default=str).encode())
        rankings = [{key: value for key, value in row.items() if key != "predicted"} for row in ctx.rankings]  # the audit never reads the model's guess
        digest.update(json.dumps([AUDIT_VERSION, rankings, ctx.robots_txt, {str(k): str(v) for k, v in ctx.statuses.items()}, ctx.brand_hint, [getattr(fn, "__qualname__", "") for fn in CHECKS]], sort_keys=True, default=str).encode())
        return digest.hexdigest()
    except (TypeError, ValueError):
        return None


DISK_KEEP = 4  # results kept per project: the current pages plus a few recent versions


def _disk_file(project):
    """plugins/audit/issues.json under the project folder, saved with the project like any plugin data."""
    if project is None or not getattr(project, "dir", None):
        return None
    try:
        from aeo.ranksim import plugins

        return plugins.data_dir(project, "audit") / "issues.json"
    except Exception:  # noqa: BLE001 - no folder means no disk cache, never a failed audit
        return None


def _disk_get(project, key: str) -> str | None:
    path = _disk_file(project)
    if path is None:
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        hit = stored.get("results", {}).get(key)
        return hit if isinstance(hit, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def _disk_put(project, key: str, value: str) -> None:
    path = _disk_file(project)
    if path is None:
        return
    try:
        try:
            results = json.loads(path.read_text(encoding="utf-8")).get("results", {})
        except (OSError, ValueError, AttributeError):
            results = {}
        results.pop(key, None)
        results[key] = value
        while len(results) > DISK_KEEP:
            results.pop(next(iter(results)))
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({"version": AUDIT_VERSION, "results": results}), encoding="utf-8")
        temp.replace(path)
    except OSError:
        log.warning("audit: could not write the cache for %s", getattr(project, "slug", project))


def run_audit(ctx: AuditContext, *, checks: Iterable[CheckFn] | None = None) -> list[dict]:
    """The built-in CHECKS (or the given checks), then every plugin check when no checks are given. Built-in issues
    are deduplicated, merged and scored (finalize); plugin issues keep their own ids. Every id ends up unique.
    Built-in results are cached by a digest of the pages, the top 10 pages and the rankings, so a report that is
    loaded again without changes skips the parsing."""
    key = _cache_key(ctx) if checks is None else None
    with _CACHE_LOCK:
        hit = _CACHE.get(key) if key is not None else None
    if hit is None and key is not None:
        hit = _disk_get(ctx.project, key)
    if hit is not None:
        issues = json.loads(hit)
    else:
        issues = finalize(_run_builtin(ctx, list(checks) if checks is not None else list(CHECKS)))
        if key is not None:
            hit = json.dumps(issues)
            _disk_put(ctx.project, key, hit)
    if key is not None and hit is not None:
        with _CACHE_LOCK:
            while len(_CACHE) >= _CACHE_SIZE:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = hit
    if checks is None:
        extra = _plugin_issues(ctx)
        for issue in extra:
            issue["score"] = round(issue["impact"] / max(1, issue["effort"]), 1)
        issues = sorted(issues + extra, key=_sort_key)
    _unique_ids(issues)
    return issues


def audit(project, pages: list, rankings: list[dict], docs: dict, *, database: str | None = None, meta=None, html: dict[str, str] | None = None, robots_txt: str | None = None, statuses: dict | None = None) -> list[dict]:
    """Specific issues, each pinned on an element where one exists, with a plain comment that names the page, the
    numbers and what to change. pages and docs are site.SitePage rows (their raw HTML is read from database) or
    PageDocs."""
    ctx = AuditContext(project, pages, rankings, docs, database=database, meta=meta, html=html, robots_txt=robots_txt, statuses=statuses or {})
    return run_audit(ctx)


def audit_html(
    pages: dict[str, str] | list[PageDoc],
    *,
    rankings: list[dict] | None = None,
    docs: dict[str, str | PageDoc] | None = None,
    project=None,
    robots_txt: str | None = None,
    statuses: dict | None = None,
    brand: str = "",
) -> list[dict]:
    """Audit pages given as {url: html} or PageDoc objects (tests and tools)."""
    page_docs = [PageDoc(url, html) for url, html in pages.items()] if isinstance(pages, dict) else list(pages)
    ctx = AuditContext(project, page_docs, rankings or [], dict(docs or {}), html={}, robots_txt=robots_txt, statuses=statuses or {}, brand_hint=brand)
    return run_audit(ctx)


def group_issues(issues: list[dict]) -> dict:
    """The same issues grouped by page and by search, most urgent first. Groups hold issue ids."""
    pages: dict[str, dict] = {}
    searches: dict[str, dict] = {}
    for issue in issues:
        for url in issue.get("pages") or ([issue["page"]] if issue.get("page") else []):
            group = pages.setdefault(url, {"page": url, "path": path_of(url), "ids": [], "high": 0, "medium": 0, "low": 0, "score": 0.0})
            group["ids"].append(issue["id"])
            group[issue["severity"]] += 1
            group["score"] = round(group["score"] + issue.get("score", 0), 1)
        if issue.get("search"):
            group = searches.setdefault(issue["search"], {"search": issue["search"], "ids": [], "high": 0, "medium": 0, "low": 0, "score": 0.0})
            group["ids"].append(issue["id"])
            group[issue["severity"]] += 1
            group["score"] = round(group["score"] + issue.get("score", 0), 1)
    order = lambda group: (-group["high"], -group["score"])  # noqa: E731
    return {"pages": sorted(pages.values(), key=order), "searches": sorted(searches.values(), key=order)}


def summarize(issues: list[dict]) -> dict:
    counts = Counter(issue["severity"] for issue in issues)
    return {"total": len(issues), "high": counts["high"], "medium": counts["medium"], "low": counts["low"], "kinds": dict(Counter(issue["kind"] for issue in issues))}
