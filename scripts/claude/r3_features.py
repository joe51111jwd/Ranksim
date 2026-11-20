"""Round 3 history-free features for one (search, page) and the baselines scored against them.

Everything here reads only: the search text, the page URL, and the page's own
stored HTML (title, meta description, H1, headings, main text, links). No SERP
position, no rank history, no Google-written title or snippet, no site-level
authority. So the same function scores an unpublished draft or an edited copy.

Feature tiers (used for ablations):
  text       search-to-text match in the page HTML (lexical and semantic)
  url        search terms in the URL path, depth, home page, page-type words
  shape      size and layout of the page (words, headings, links, schema types)
  site       position of the URL inside its own site's link structure, read from
             the other stored pages of the same list (history-free, but not
             something a text edit changes)

BM25F here is the same definition as the round 2 same-site study (0.597 there):
fields title 3.0, H1 3.0, headings 2.0, body 1.0, k1 1.2, IDF from the pages of
the list's own corpus (see ``Corpus``).
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude.r2_samesite_pairs import (  # noqa: E402
    Corpus,
    contains_seq,
    coverage,
    ident,
    page_fields,
    query_terms,
    toks,
    url_features,
)

PAGE_TYPE_WORDS = {
    "pt_blog": {"blog", "blogs", "article", "articles", "news", "post", "posts", "insights", "resources", "resource", "learn", "guide", "guides", "library", "stories"},
    "pt_product": {"product", "products", "features", "feature", "solutions", "solution", "software", "platform", "tools", "tool", "app", "apps"},
    "pt_pricing": {"pricing", "price", "prices", "plans", "cost"},
    "pt_compare": {"compare", "comparison", "comparisons", "alternative", "alternatives", "vs", "versus", "competitors"},
    "pt_help": {"help", "support", "docs", "doc", "documentation", "kb", "knowledge", "faq", "faqs", "community", "forum", "forums", "academy", "training"},
    "pt_listing": {"category", "categories", "tag", "tags", "topics", "topic", "search", "directory", "list", "author", "page"},
    "pt_industry": {"industries", "industry", "customers", "customer", "case", "studies", "study"},
}
QUERY_INTENT_WORDS = {
    "qi_pricing": {"pricing", "price", "cost", "prices", "free", "cheap"},
    "qi_compare": {"alternative", "alternatives", "vs", "versus", "compare", "comparison", "competitors"},
    "qi_reviews": {"review", "reviews", "rating", "ratings"},
    "qi_best": {"best", "top"},
    "qi_howto": {"how", "what", "why", "guide", "tutorial", "template", "example", "examples"},
    "qi_login": {"login", "signin", "sign", "app", "download"},
}
_WORD = re.compile(r"[a-z0-9]+")


def raw_tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def _min_window(tokens: list[str], terms: list[str]) -> float:
    """Length of the shortest token window that holds every search term found in the text (0 when fewer than 2 found)."""
    want = set(terms)
    positions = [(i, t) for i, t in enumerate(tokens) if t in want]
    found = {t for _i, t in positions}
    if len(found) < 2:
        return 0.0
    best = len(tokens)
    counts: Counter = Counter()
    have = 0
    left = 0
    for right in range(len(positions)):
        term = positions[right][1]
        counts[term] += 1
        if counts[term] == 1:
            have += 1
        while have == len(found):
            best = min(best, positions[right][0] - positions[left][0] + 1)
            drop = positions[left][1]
            counts[drop] -= 1
            if counts[drop] == 0:
                have -= 1
            left += 1
    return float(best) / len(found)


def _phrase_count(tokens: list[str], phrase: list[str]) -> int:
    if not phrase or len(phrase) > len(tokens):
        return 0
    width = len(phrase)
    return sum(1 for i in range(len(tokens) - width + 1) if tokens[i : i + width] == phrase)


def static_page(page: dict, url: str) -> dict:
    """Search-independent parts of a page, computed once per page."""
    fields = page_fields(page)
    urlf = url_features(url)
    path_words = set(raw_tokens(urlsplit(url).path))
    schema = {str(value).lower() for value in (page.get("schema_types") or [])}
    words = int(page.get("word_count") or 0)
    headings = page.get("headings") or []
    out = {
        "fields": fields,
        "meta_tokens": toks(page.get("meta") or ""),
        "first100": fields["body"][:100],
        "slug_tokens": urlf["slug_tokens"],
        "url_depth": urlf["url_depth"],
        "is_home": urlf["is_home"],
        "url_len": urlf["url_len"],
        "log_words": math.log1p(words),
        "n_headings": math.log1p(len(headings)),
        "log_internal_links": math.log1p(int(page.get("internal_links") or 0)),
        "log_external_links": math.log1p(int(page.get("external_links") or 0)),
        "title_len": float(len(fields["title"])),
        "has_faq_schema": float(any("faq" in value for value in schema)),
        "has_article_schema": float(any(value in {"article", "blogposting", "newsarticle"} for value in schema)),
        "has_product_schema": float(any(value in {"product", "softwareapplication", "service"} for value in schema)),
        "has_date": float(bool(page.get("published") or page.get("updated"))),
    }
    for name, words_set in PAGE_TYPE_WORDS.items():
        out[name] = float(bool(path_words & words_set))
    return out


def lexical_features(query: str, static: dict, corpus: Corpus) -> dict:
    terms = query_terms(query)
    phrase = toks(query)
    fields = static["fields"]
    body = fields["body"]
    body_counts = Counter(body)
    idf = {term: corpus.idf(term) for term in terms}
    idf_total = sum(idf.values()) or 1.0

    def idf_cov(tokens: list[str]) -> float:
        have = set(tokens)
        return sum(value for term, value in idf.items() if term in have) / idf_total

    title = fields["title"]
    matched_title = sum(1 for token in title if token in set(terms))
    out = {
        "bm25f": corpus.bm25f(fields, terms),
        "bm25_body": corpus.bm25(body, terms),
        "title_cov": coverage(title, terms),
        "h1_cov": coverage(fields["h1"], terms),
        "head_cov": coverage(fields["head"], terms),
        "meta_cov": coverage(static["meta_tokens"], terms),
        "slug_cov": coverage(static["slug_tokens"], terms),
        "first100_cov": coverage(static["first100"], terms),
        "body_cov": coverage(body, terms),
        "title_idf_cov": idf_cov(title),
        "h1_idf_cov": idf_cov(fields["h1"]),
        "slug_idf_cov": idf_cov(static["slug_tokens"]),
        "title_phrase": float(contains_seq(title, phrase)),
        "h1_phrase": float(contains_seq(fields["h1"], phrase)),
        "slug_phrase": float(contains_seq(static["slug_tokens"], phrase)),
        "body_phrase_log": math.log1p(_phrase_count(body, phrase)),
        "title_focus": (matched_title / len(title)) if title else 0.0,
        "slug_focus": (sum(1 for token in static["slug_tokens"] if token in set(terms)) / len(static["slug_tokens"])) if static["slug_tokens"] else 0.0,
        "body_density": (sum(body_counts.get(term, 0) for term in terms) / len(body)) if body else 0.0,
        "body_min_window": _min_window(body[:3000], terms),
        "all_terms_title": float(bool(terms) and coverage(title, terms) == 1.0),
        "all_terms_fields": float(bool(terms) and coverage(title + fields["h1"] + static["slug_tokens"], terms) == 1.0),
    }
    return out


def query_flags(query: str) -> dict:
    words = set(raw_tokens(query))
    out = {name: float(bool(words & words_set)) for name, words_set in QUERY_INTENT_WORDS.items()}
    out["q_len"] = float(len(query_terms(query)))
    return out


def page_text_for_title(page: dict) -> str:
    """Title plus meta description plus H1: the page's own summary of itself."""
    parts = [page.get("title") or "", page.get("h1") or "", page.get("meta") or ""]
    return " . ".join(part.strip() for part in parts if part and part.strip())[:600]


def site_structure(keys_in_list: list[str], pages: dict[str, dict], home_page: dict | None) -> dict[str, dict]:
    """Link position of each listed URL inside its own site, from stored pages only.

    in_list_links: how many OTHER usable pages of the same list link to the URL.
    home_linked: 1 when the site's stored home page links to the URL (0 when it
    does not, NaN when no home page is stored).
    """
    idents = {key: ident("https://" + key if not key.startswith("http") else key) for key in keys_in_list}
    outgoing = {}
    for key in keys_in_list:
        page = pages.get(key)
        if page is not None:
            outgoing[key] = {ident(link) for link, _text in (page.get("links") or [])}
    home_links = {ident(link) for link, _text in (home_page.get("links") or [])} if home_page else None
    result = {}
    for key in keys_in_list:
        own = idents[key]
        count = sum(1 for other, links in outgoing.items() if other != key and own in links)
        result[key] = {
            "in_list_links": float(count),
            "home_linked": (float(own in home_links) if home_links is not None else float("nan")),
        }
    return result


TEXT_LEXICAL = [
    "bm25f", "bm25_body", "title_cov", "h1_cov", "head_cov", "meta_cov", "first100_cov", "body_cov",
    "title_idf_cov", "h1_idf_cov", "title_phrase", "h1_phrase", "body_phrase_log", "title_focus",
    "body_density", "body_min_window", "all_terms_title",
]
TEXT_SEMANTIC = ["ce_title", "ce_body_max", "ce_body_top3", "emb_title", "emb_body_max"]
URL_FEATURES = ["slug_cov", "slug_idf_cov", "slug_phrase", "slug_focus", "all_terms_fields", "url_depth", "is_home", "url_len"] + list(PAGE_TYPE_WORDS)
SHAPE_FEATURES = [
    "log_words", "n_headings", "log_internal_links", "log_external_links", "title_len",
    "has_faq_schema", "has_article_schema", "has_product_schema", "has_date",
]
SITE_FEATURES = ["in_list_links", "home_linked"]
QUERY_FEATURES = list(QUERY_INTENT_WORDS) + ["q_len"]


def zscore_within(values: np.ndarray) -> np.ndarray:
    """Within-list standardisation (NaN kept as 0 after centring)."""
    finite = np.isfinite(values)
    if finite.sum() < 2:
        return np.zeros_like(values, dtype=float)
    mean = values[finite].mean()
    std = values[finite].std()
    out = np.where(finite, (values - mean) / (std if std > 1e-9 else 1.0), 0.0)
    return out
