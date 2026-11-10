"""Pages whose content is drawn by JavaScript after load ("client-rendered shells").

The crawler stores the HTML the server sends. For a client-rendered app that is an almost empty
document: a mount point such as ``<div id="root"></div>`` and script tags. Google renders
scripts before indexing; this tool does not. So a shell's stored text is not the page Google
ranked, and scoring it as a thin page would be wrong. Shells are reported as their own coverage
reason and left out of training and evaluation, like unpublished drafts.

Detection is deliberately narrow: fewer than ``SHELL_MAX_WORDS`` visible words in the whole body
(scripts and styles removed), at least one script, and an empty app mount point, a ``<noscript>``
asking for JavaScript, or almost no visible text at all.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from aeo.textutil import word_count

SHELL_MAX_WORDS = 50
MOUNT_IDS = frozenset({"root", "app", "__next", "__nuxt", "___gatsby", "svelte", "main-app", "q-app"})


def js_shell_reason(html: str) -> str | None:
    """Why this HTML looks like a client-rendered shell, or None."""
    if not html or "<script" not in html.lower():
        return None
    soup = BeautifulSoup(html, "lxml")
    if not soup.find("script"):
        return None
    noscript = " ".join(tag.get_text(" ", strip=True) for tag in soup.find_all("noscript")).lower()
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    words = word_count(body.get_text(" ", strip=True))
    if words >= SHELL_MAX_WORDS:
        return None
    for tag in body.find_all(id=True):
        if tag.get("id") in MOUNT_IDS and not tag.get_text(strip=True):
            return f"empty #{tag.get('id')} mount point, {words} visible words"
    if "javascript" in noscript:
        return f"a noscript message asks for JavaScript, {words} visible words"
    if words < 10:
        return f"{words} visible words and only scripts"
    return None


def is_js_shell(html: str, main_words: int | None = None) -> bool:
    """Cheap when ``main_words`` (the extracted main-text word count) is known and not small."""
    if main_words is not None and main_words >= SHELL_MAX_WORDS:
        return False
    return js_shell_reason(html) is not None
