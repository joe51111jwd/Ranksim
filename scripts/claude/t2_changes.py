"""Target 2: deterministic page-content change detector.

Input: two HTML strings of the same URL (older, newer), fetched the same way
(http vs http, browser vs browser). Output: a flat dict of diff features and a
boolean ``material`` under the frozen rule ``MATERIAL_RULE`` below.

Text extraction reuses the repo's own extractor (aeo.crawl.extract.extract_html,
the one pair_order.parse_html uses for the frozen ranker), so "main text" here
is the same text the ranker sees. Nothing in this module reads a SERP, a rank,
a date, or the database. It is a pure function of the two HTML strings (plus an
optional query string for the query-term counts), so it can be run mechanically
on held-out fetches.

How the rule was chosen (design data only, days <= 2026-11-17; details and
examples in evals/claude/t2-edits/notes.md and rulecheck.md):
- Noise reference: 61 same-URL, same-method http re-fetches a few hours apart on
  2026-11-16 (13:xx vs 21:xx UTC). A real edit in that window is unlikely.
- Rotating widgets ("related posts", "industry insights" cards, testimonials,
  app-store reviews) change up to 185 main-text tokens and up to 3-4 headings on
  design pairs. Real body rewrites start at about 300 tokens. Heading-set change
  alone does not separate noise from edits, so it is reported but not a trigger.
- Comparisons are on lowercase alphanumeric tokens with digits masked, so case,
  punctuation ("#-person" vs "# person"), dates, counters and prices never count.

The thresholds are FROZEN under RULE_VERSION. Do not tune them on held-out pairs.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from aeo.crawl.extract import extract_html
from aeo.ranksim.site_history import site_key
from aeo.textutil import STOPWORDS, salient_terms, tokenize

RULE_VERSION = "t2-material-v1"
MIN_WORDS = 50
SHINGLE = 5

EDIT_TOKENS_MIN = 250  # main-text tokens not covered by any 5-gram shared with the other version
EDIT_SHARE_MIN = 0.02  # ... as a share of both versions' token counts
WC_RATIO_MIN = 0.25  # |log((wc_b+1)/(wc_a+1))| >= log(1.25)
WC_DELTA_MIN = 150  # ... and at least this many words of difference

MATERIAL_RULE = (
    "usable (both versions extract >= 50 main-text words) AND any of: "
    "(a) title token sequence changed; "
    "(b) H1 token sequence changed (including gaining or losing an H1); "
    "(c) main-text edit >= 250 tokens AND >= 0.02 of the two versions' combined tokens, where an edited "
    "token is one not covered by any word 5-gram present in the other version; "
    "(d) main-text word count changed by >= 25% AND by >= 150 words. "
    "Tokens are lowercase [a-z0-9]+ with every digit run masked to '#'. Headings, meta description, "
    "schema types, canonical, dates and link counts are reported but never make a change material on their own."
)

_DIGITS = re.compile(r"\d+")


def norm_tokens(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, digit runs masked to '#'."""
    return ["#" if token.isdigit() else token for token in tokenize(_DIGITS.sub(" 0 ", text or ""))]


def norm(text: str) -> str:
    return " ".join(norm_tokens(text))


def _shingles(tokens: list[str], size: int = SHINGLE) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def _uncovered(tokens: list[str], other: set[tuple[str, ...]], size: int = SHINGLE) -> int:
    """Tokens not inside any 5-gram that also occurs in the other version (linear time)."""
    if not tokens:
        return 0
    if len(tokens) < size:
        return 0 if tuple(tokens) in other else len(tokens)
    covered = [False] * len(tokens)
    for start in range(len(tokens) - size + 1):
        if tuple(tokens[start : start + size]) in other:
            for index in range(start, start + size):
                covered[index] = True
    return sum(1 for flag in covered if not flag)


def _jaccard_distance(left: set, right: set) -> float:
    if not left and not right:
        return 0.0
    union = len(left | right)
    return 1.0 - (len(left & right) / union if union else 0.0)


def _schema_types(objects: list) -> list[str]:
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            for item in kind if isinstance(kind, list) else [kind]:
                if isinstance(item, str) and item:
                    found.add(item)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(objects)
    return sorted(found)


EMPTY_PAGE = {
    "title": "",
    "meta": "",
    "h1": "",
    "headings": [],
    "blocks": [],
    "main_text": "",
    "word_count": 0,
    "schema_types": [],
    "canonical": "",
    "internal_links": 0,
    "external_links": 0,
    "published": "",
    "updated": "",
}


def extract_page(html: str, url: str) -> dict:
    """Fields the diff uses. Same extractor as the frozen ranker's parse_html."""
    try:
        page = extract_html(html or "", url)
    except (ValueError, TypeError, AttributeError):
        return dict(EMPTY_PAGE)
    own = site_key(url) if url else ""
    internal = 0
    external = 0
    for link in page.links:
        try:
            same = site_key(link) == own
        except ValueError:
            same = False
        if same:
            internal += 1
        else:
            external += 1
    blocks = [line.strip() for line in (page.main_text or "").split("\n") if line.strip()]
    return {
        "title": page.title or "",
        "meta": page.meta_description or "",
        "h1": page.h1 or "",
        "headings": [str(item.get("text") or "") for item in page.headings],
        "blocks": blocks,
        "main_text": page.main_text or "",
        "word_count": int(page.word_count or 0),
        "schema_types": _schema_types(page.schema_objects),
        "canonical": page.canonical or "",
        "internal_links": internal,
        "external_links": external,
        "published": page.published or "",
        "updated": page.updated or "",
    }


def _block_diff(old_blocks: list[str], new_blocks: list[str]) -> tuple[int, int]:
    """Element blocks (one <p>/<li>/<h*>) present on one side only, normalized multiset."""
    old = Counter(norm(block) for block in old_blocks)
    new = Counter(norm(block) for block in new_blocks)
    return sum((new - old).values()), sum((old - new).values())


def _term_count(tokens: list[str], terms: list[str]) -> int:
    if not terms:
        return 0
    counts = Counter(tokens)
    return sum(counts[term] for term in terms)


def _coverage(tokens: list[str], terms: list[str]) -> float:
    if not terms:
        return 0.0
    have = set(tokens)
    return sum(1 for term in terms if term in have) / len(terms)


def diff_extracted(old: dict, new: dict, query: str | None = None) -> dict:
    """Diff features of two extracted pages plus the frozen ``material`` flag."""
    wc_a, wc_b = int(old["word_count"]), int(new["word_count"])
    usable = wc_a >= MIN_WORDS and wc_b >= MIN_WORDS
    title_a, title_b = norm_tokens(old["title"]), norm_tokens(new["title"])
    h1_a, h1_b = norm_tokens(old["h1"]), norm_tokens(new["h1"])
    head_a = {norm(text) for text in old["headings"] if norm(text)}
    head_b = {norm(text) for text in new["headings"] if norm(text)}
    tok_a = norm_tokens(old["main_text"])
    tok_b = norm_tokens(new["main_text"])
    sh_a, sh_b = _shingles(tok_a), _shingles(tok_b)
    removed_tokens = _uncovered(tok_a, sh_b)
    added_tokens = _uncovered(tok_b, sh_a)
    edit_tokens = removed_tokens + added_tokens
    edit_share = edit_tokens / max(1, len(tok_a) + len(tok_b))
    added_blocks, removed_blocks = _block_diff(old["blocks"], new["blocks"])
    wc_log_ratio = math.log((wc_b + 1.0) / (wc_a + 1.0))
    features = {
        "rule_version": RULE_VERSION,
        "usable": bool(usable),
        "wc_a": wc_a,
        "wc_b": wc_b,
        "wc_delta": wc_b - wc_a,
        "wc_log_ratio": round(wc_log_ratio, 6),
        "title_changed": title_a != title_b,
        "title_token_dist": round(_jaccard_distance(set(title_a) - STOPWORDS, set(title_b) - STOPWORDS), 6),
        "h1_changed": h1_a != h1_b,
        "headings_a": len(head_a),
        "headings_b": len(head_b),
        "headings_added": len(head_b - head_a),
        "headings_removed": len(head_a - head_b),
        "headings_dist": round(_jaccard_distance(head_a, head_b), 6),
        "text_shingle_dist": round(_jaccard_distance(sh_a, sh_b), 6),
        "text_token_dist": round(_jaccard_distance(set(tok_a), set(tok_b)), 6),
        "added_tokens": added_tokens,
        "removed_tokens": removed_tokens,
        "edit_tokens": edit_tokens,
        "edit_share": round(edit_share, 6),
        "added_share": round(added_tokens / max(1, len(tok_b)), 6),
        "removed_share": round(removed_tokens / max(1, len(tok_a)), 6),
        "added_blocks": added_blocks,
        "removed_blocks": removed_blocks,
        "meta_changed": norm(old["meta"]) != norm(new["meta"]),
        "schema_changed": old["schema_types"] != new["schema_types"],
        "canonical_changed": (old["canonical"] or "") != (new["canonical"] or ""),
        "internal_links_a": int(old["internal_links"]),
        "internal_links_b": int(new["internal_links"]),
        "internal_links_delta": int(new["internal_links"]) - int(old["internal_links"]),
        "external_links_delta": int(new["external_links"]) - int(old["external_links"]),
        "updated_changed": (old["updated"] or "") != (new["updated"] or ""),
        "published_changed": (old["published"] or "") != (new["published"] or ""),
    }
    reasons = []
    if usable:
        if features["title_changed"]:
            reasons.append("title")
        if features["h1_changed"]:
            reasons.append("h1")
        if edit_tokens >= EDIT_TOKENS_MIN and edit_share >= EDIT_SHARE_MIN:
            reasons.append("text")
        if abs(wc_log_ratio) >= math.log(1.0 + WC_RATIO_MIN) and abs(wc_b - wc_a) >= WC_DELTA_MIN:
            reasons.append("size")
    features["material_reasons"] = "+".join(reasons)
    features["material"] = bool(reasons)
    if query:
        features.update(query_features(old, new, query, tok_a, tok_b))
    return features


def query_features(old: dict, new: dict, query: str, tok_a: list[str] | None = None, tok_b: list[str] | None = None) -> dict:
    """Query-term counts and coverage in title/H1/headings/body, before (a) and after (b)."""
    terms = list(dict.fromkeys(norm(term) for term in salient_terms(query)))
    tok_a = norm_tokens(old["main_text"]) if tok_a is None else tok_a
    tok_b = norm_tokens(new["main_text"]) if tok_b is None else tok_b
    out: dict = {}
    for side, page, body in (("a", old, tok_a), ("b", new, tok_b)):
        title = norm_tokens(page["title"])
        h1 = norm_tokens(page["h1"])
        heads = norm_tokens(" ".join(page["headings"]))
        out[f"q_title_{side}"] = _term_count(title, terms)
        out[f"q_h1_{side}"] = _term_count(h1, terms)
        out[f"q_head_{side}"] = _term_count(heads, terms)
        out[f"q_body_{side}"] = _term_count(body, terms)
        out[f"q_titlecov_{side}"] = round(_coverage(title, terms), 6)
        out[f"q_h1cov_{side}"] = round(_coverage(h1, terms), 6)
        out[f"q_bodycov_{side}"] = round(_coverage(body, terms), 6)
        out[f"q_density_{side}"] = round(out[f"q_body_{side}"] / max(1, len(body)), 8)
    for name in ("title", "h1", "head", "body"):
        out[f"q_{name}_delta"] = out[f"q_{name}_b"] - out[f"q_{name}_a"]
    for name in ("titlecov", "h1cov", "bodycov", "density"):
        out[f"q_{name}_delta"] = round(out[f"q_{name}_b"] - out[f"q_{name}_a"], 8)
    return out


def diff_pages(old_html: str, new_html: str, url: str = "", query: str | None = None) -> dict:
    """Public entry point: two HTML strings (older, newer) -> diff features + ``material``."""
    return diff_extracted(extract_page(old_html, url), extract_page(new_html, url), query)
