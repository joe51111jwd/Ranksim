"""The searches a site is written for, found from its own pages, plus any searches plugins add.

brand_of names the site, discover_searches turns titles and headings into a short list of searches (branded
phrases, section labels and price fragments are dropped, near duplicates merged), and discover_with_plugins adds
the rows from every plugin's discovery_sources hook (for example Search Console queries).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from aeo.ranksim.site import SitePage

MAX_SEARCHES = 15
GENERIC = {
    "home", "homepage", "about", "about us", "contact", "contact us", "blog", "pricing", "login", "log in", "sign in",
    "sign up", "privacy policy", "privacy", "terms", "terms of service", "terms and conditions", "careers", "faq",
    "faqs", "news", "resources", "support", "help", "menu", "search", "page not found", "cookie policy", "sitemap",
    "thank you", "our team", "get started", "learn more", "read more", "request a demo", "book a demo", "features",
    "testimonials", "case studies", "partners", "press", "events", "jobs", "team", "products", "services", "solutions",
}
LEADING = ("the ", "a ", "an ", "our ", "your ", "welcome to ", "meet ", "introducing ")
# Section labels, not searches: "Questions people ask", "Guides for ...", "Why choose us".
LABEL_STARTS = (
    "questions", "frequently asked", "common questions", "articles", "guides", "resources", "related", "latest", "more ",
    "why choose", "why ", "how it works", "what our", "what people", "get in touch", "ready to", "trusted by", "join ",
    "see how", "learn ", "explore", "read ", "browse", "all ", "featured", "popular", "recent", "head to head",
    "where ", "who ", "built for", "made for", "everything you", "start ", "try ", "talk to", "contact", "our ",
    "meet ", "about ", "see ", "watch ", "download", "sign ", "book ", "request ", "schedule ", "pricing", "plans",
)
BRAND_AFFIXES = ("hq", "app", "inc", "co", "get", "try", "use", "the", "my", "go", "io")
COMMERCIAL = ("software", "app", "apps", "tool", "tools", "alternative", "alternatives", " vs ", "best ", "cost", "price", "how to", "platform", "system", "contractor", "contractors", "what is ")
MIN_SCORE = 2.5  # a title or H1 phrase always passes; an H2 alone passes only when it reads like a search
SPLIT = re.compile(r"\s+[|\-–—:•·]\s+|\s*[|•·]\s*")
SOURCE_WEIGHT = 3.0  # a plugin row with no weight counts as much as a page title phrase
MAX_SOURCE_PHRASE = 100


def brand_tokens(host: str) -> set[str]:
    """The site's own name as search words: smartcrewhq.com gives {"smartcrewhq", "smartcrew"}."""
    label = host.removeprefix("www.").split(".")[0].lower()
    tokens = {label, label.replace("-", "")}
    for affix in BRAND_AFFIXES:
        if label.endswith(affix) and len(label) - len(affix) >= 3:
            tokens.add(label[: -len(affix)])
        if label.startswith(affix) and len(label) - len(affix) >= 3:
            tokens.add(label[len(affix):])
    tokens.update(part for part in label.split("-") if len(part) > 2)
    return {token for token in tokens if token}


def brand_of(pages: list[SitePage], host: str) -> str:
    """The site's name: the title part most pages repeat, or the domain name with the casing the pages use."""
    counts: Counter = Counter()
    for page in pages:
        parts = [part.strip() for part in SPLIT.split(page.title or "") if part.strip()]
        if len(parts) > 1:
            counts.update({parts[-1]: 1, parts[0]: 0.5})
    readable = [page for page in pages if page.status == "fetched"]
    if counts:
        name, seen = counts.most_common(1)[0]
        # A brand is a short name, not a tagline: "SmartCrew", not "All-in-one dirtwork software: HCSS ...".
        if seen >= max(2, len(readable) * 0.3) and len(name) <= 28 and len(name.split()) <= 3:
            return name
    tokens = sorted(brand_tokens(host), key=len)
    text = " ".join(f"{page.title} {page.h1}" for page in readable)
    for token in tokens:
        match = re.search(rf"\b({re.escape(token)})\b", text, re.I)
        if match and len(token) >= 4:
            return match.group(1) if not match.group(1).islower() else match.group(1).title()
    return tokens[-1].replace("-", " ").title() if tokens else host


def _clean(text: str) -> str:
    value = text.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9&' ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" '")
    for lead in LEADING:
        if value.startswith(lead):
            value = value[len(lead):]
    return value.strip()


def _branded(phrase: str, brand_phrase: str, brand_words: set[str]) -> bool:
    words = phrase.split()
    return bool(brand_phrase and f" {brand_phrase} " in f" {phrase} ") or any(
        word in brand_words or any(word.startswith(b) for b in brand_words if len(b) >= 5) for word in words
    )


def discover_searches(pages: list[SitePage], brand: str, *, limit: int = MAX_SEARCHES, host: str = "", extra: list[dict] | None = None) -> list[str]:
    """Searches the site's own pages are written for: title parts and H1s first, then H2s. Branded phrases, section
    labels and price fragments are dropped; near duplicates are merged.

    extra holds rows from other sources, {"q": str, "weight": float, "source": str}, such as the rows plugins return
    from discovery_sources. The merge:
    - A row is a real search, so the heading rules (word count, section labels, numbers) do not apply to it. Its
      text is lowercased with spaces collapsed. Rows that are empty, over 100 characters, branded (the same brand
      test as the site's phrases), or that have a weight that is not a positive number are dropped. A row without
      a weight counts 3.0.
    - Weights use the same scale as the site's phrases: a title or H1 phrase scores 3.0 (plus 0.5 for 2 to 5 words
      and 1.0 for a buying word such as software or cost), an H2 1.0, and a phrase needs 2.5 in total to be picked.
      A row for a phrase the site also uses adds its weight to that phrase; the same phrase from several rows adds up.
    - Everything is then picked together: highest score first, near duplicates of a picked phrase skipped, at most
      limit phrases. With no rows the result is exactly what the site's pages alone give.
    """
    brand_phrase = _clean(brand)
    brand_words = {brand_phrase.replace(" ", "")} if brand_phrase else set()
    if brand_phrase and " " not in brand_phrase:
        brand_words.add(brand_phrase)
    if host:
        brand_words |= brand_tokens(host)
    brand_words = {word for word in brand_words if len(word) > 2}
    scores: Counter = Counter()
    for page in pages:
        if page.status != "fetched":
            continue
        candidates = [(part, 3.0) for part in SPLIT.split(page.title or "")]
        candidates.append((page.h1, 3.0))
        candidates.extend((text, 1.0) for text in page.h2s[:6])
        for text, weight in candidates:
            phrase = _clean(text or "")
            words = phrase.split()
            if not 2 <= len(words) <= 6 or phrase in GENERIC or len(phrase) > 60:
                continue
            if _branded(phrase, brand_phrase, brand_words):
                continue  # people searching your name already find you; these are not the searches you compete on
            if phrase.startswith(LABEL_STARTS) or phrase.endswith((" us", " here", " today", " now", " is", " facts")):
                continue
            if phrase.count(" and ") >= 1 and weight < 3 and not any(marker in f" {phrase} " for marker in ("software", "apps", "tools")):
                continue
            if sum(1 for word in words if word.isdigit()) >= 1 and not re.search(r"\b(19|20)\d\d\b", phrase):
                continue
            bonus = 0.5 if 2 <= len(words) <= 5 else 0.0
            if any(marker in f" {phrase} " for marker in COMMERCIAL):
                bonus += 1.0
            scores[phrase] += weight + bonus
    for phrase, weight in _source_rows(extra, brand_phrase, brand_words):
        scores[phrase] += weight
    picked: list[str] = []
    for phrase, score in sorted(scores.items(), key=lambda item: (-item[1], len(item[0]))):
        if score < MIN_SCORE:
            break
        tokens = set(phrase.split())
        if any(_jaccard(tokens, set(other.split())) >= 0.6 for other in picked):
            continue
        picked.append(phrase)
        if len(picked) >= limit:
            break
    return picked


def _source_rows(rows, brand_phrase: str, brand_words: set[str]) -> list[tuple[str, float]]:
    """The usable (phrase, weight) pairs from rows other sources returned. See discover_searches for the rules."""
    out: list[tuple[str, float]] = []
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("q"), str):
            continue
        weight = row.get("weight", SOURCE_WEIGHT)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            continue
        phrase = " ".join(row["q"].lower().replace("’", "'").split())
        if not phrase or len(phrase) > MAX_SOURCE_PHRASE or _branded(phrase, brand_phrase, brand_words):
            continue
        out.append((phrase, float(weight)))
    return out


def discover_with_plugins(pages: list[SitePage], brand: str, *, host: str = "", project=None, ui=None, limit: int = MAX_SEARCHES) -> list[str]:
    """discover_searches with the rows every plugin's discovery_sources hook returns merged in.

    project is None on a site's first analysis (the project is created after its searches are found).
    """
    from aeo.ranksim import plugins

    rows = plugins.collect("discovery_sources", project=project, pages=pages, ui=ui)
    return discover_searches(pages, brand, limit=limit, host=host, extra=rows)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0
