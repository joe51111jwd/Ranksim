"""Detect pages whose crawlable HTML is nearly empty because scripts draw them.

The crawler does not run JavaScript, and many search and answer-engine crawlers fetch raw HTML too. A page
that is an empty ``<div id="root"></div>`` plus a bundle has almost nothing for them to read. This module
flags such pages from the raw HTML alone; it does not guess what the rendered page would say.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Below this many visible words the page is thin enough to look for a script cause.
THIN_WORDS = 40
# Inline script share of the HTML at which a thin page counts as script-drawn even with no known app root.
SCRIPT_SHARE = 0.3

_HIDDEN = {"script", "style", "noscript", "template", "head", "svg"}
_ROOT_IDS = {"root", "app", "__next", "__nuxt", "___gatsby", "svelte", "q-app", "react-root", "app-root", "main-app"}
_ROOT_TAGS = {"app-root"}  # Angular's default root element
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.hidden_stack: list[str] = []
        self.words = 0
        self.script_bytes = 0
        self.external_scripts = 0
        self.in_script = False
        # Each open app root: [tag, nesting count of that tag, words seen when it opened]
        self.roots: list[list] = []
        self.root_hits: list[tuple[str, int]] = []  # (label, words inside) for every closed or unclosed root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "body" and "head" in self.hidden_stack:
            self.handle_endtag("head")  # a missing </head> must not hide the whole body
        if tag == "script":
            self.in_script = True
            if attr.get("src"):
                self.external_scripts += 1
        for root in self.roots:
            if root[0] == tag:
                root[1] += 1
        ident = attr.get("id", "").strip().lower()
        if tag not in _VOID and (tag in _ROOT_TAGS or ident in _ROOT_IDS or "ng-app" in attr or "data-reactroot" in attr):
            label = f"<{tag}>" if tag in _ROOT_TAGS else f'<{tag} id="{ident}">' if ident else f"<{tag}>"
            self.roots.append([tag, 1, self.words, label])
        if tag in _HIDDEN:
            self.hidden_stack.append(tag)
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script":
            self.in_script = False
        if tag in _HIDDEN and tag in self.hidden_stack:
            while self.hidden_stack:
                self.hidden_depth -= 1
                if self.hidden_stack.pop() == tag:
                    break
        for root in list(self.roots):
            if root[0] == tag:
                root[1] -= 1
                if root[1] == 0:
                    self.roots.remove(root)
                    self.root_hits.append((root[3], self.words - root[2]))

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.script_bytes += len(data.encode("utf-8", "replace"))
        if self.hidden_depth:
            return
        self.words += len(_WORD.findall(data))

    def finish(self) -> None:
        self.close()
        for root in self.roots:
            self.root_hits.append((root[3], self.words - root[2]))
        self.roots = []


def script_drawn(html: str) -> dict | None:
    """Return a finding when ``html`` looks drawn by scripts, else None.

    The finding is ``{"visible_words", "script_bytes", "external_scripts", "script_share", "reason"}``.
    Visible words exclude ``script``, ``style``, ``noscript``, ``template``, ``svg`` and the ``head``.
    A short page with no script cause (a plain stub, a 404 text) returns None.
    """
    if not html or not html.strip():
        return None
    scan = _Scan()
    try:
        scan.feed(html)
        scan.finish()
    except Exception:  # noqa: BLE001 - html.parser is lenient; a crash just means no finding
        return None
    words = scan.words
    if words >= THIN_WORDS:
        return None
    total = max(len(html.encode("utf-8", "replace")), 1)
    share = scan.script_bytes / total
    empty_roots = [label for label, inside in scan.root_hits if inside <= 3]
    if empty_roots:
        reason = f"empty app root {empty_roots[0]}; scripts draw the content"
    elif share >= SCRIPT_SHARE:
        reason = f"inline scripts are {round(share * 100)}% of the HTML and only {words} words are visible"
    elif scan.external_scripts and words < 15:
        reason = f"only {words} visible words; {scan.external_scripts} external script(s) load the page"
    else:
        return None
    return {
        "visible_words": words,
        "script_bytes": scan.script_bytes,
        "external_scripts": scan.external_scripts,
        "script_share": round(share, 3),
        "reason": reason,
    }
