"""Three signals that are not another lexical mix.

Page type comes from the URL and from schema.org types in the HTML.
Tranco is a public popularity rank, not a backlink count.
Common Crawl domain ranks are harmonic centrality and PageRank from that
project's hyperlink graph. A domain the crawl did not publish stays missing.
"""

from __future__ import annotations

import csv
import json
import math
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from aeo.crawl.urls import domain_of
from aeo.ranksim.site_history import site_key

VIDEO_HOSTS = frozenset({"youtube.com", "youtu.be", "vimeo.com"})
FORUM_HOSTS = frozenset({"reddit.com", "facebook.com", "quora.com", "linkedin.com", "x.com", "twitter.com"})
REVIEW_HOSTS = frozenset(
    {"g2.com", "capterra.com", "softwareadvice.com", "trustradius.com", "getapp.com", "gartner.com", "sourceforge.net"}
)
PAGE_TYPE_FEATURES = (
    "pt_video",
    "pt_forum",
    "pt_review",
    "pt_docs",
    "pt_home",
    "pt_article",
    "pt_schema_article",
    "pt_schema_product",
    "pt_schema_video",
    "pt_schema_faq",
)
TRANCO_FEATURES = ("tranco_listed", "tranco_popularity")
CC_FEATURES = ("cc_pagerank", "cc_harmonic")
_LDJSON = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_SCHEMA_FEATURE = {
    "Article": "pt_schema_article",
    "NewsArticle": "pt_schema_article",
    "BlogPosting": "pt_schema_article",
    "Product": "pt_schema_product",
    "SoftwareApplication": "pt_schema_product",
    "VideoObject": "pt_schema_video",
    "FAQPage": "pt_schema_faq",
}


def _host(url: str) -> str:
    return domain_of(url)


def page_type_features(url: str, html: str = "") -> dict[str, float]:
    """Structural page type. The labels are fixed; they are not fit to a rank."""
    host = _host(url)
    site = site_key(url)
    path = urlsplit(url).path or "/"
    kind = "other"
    if host in VIDEO_HOSTS or host.startswith("videos.") or host.startswith("video."):
        kind = "video"
    elif site in FORUM_HOSTS or host in FORUM_HOSTS:
        kind = "forum"
    elif site in REVIEW_HOSTS or host in REVIEW_HOSTS:
        kind = "review"
    elif host.startswith(("help.", "docs.", "support.")) or any(piece in path.lower() for piece in ("/help/", "/support/", "/kb/")):
        kind = "docs"
    elif path in {"", "/"}:
        kind = "home"
    elif any(piece in path.lower() for piece in ("/blog/", "/article/", "/articles/", "/compare/", "/alternatives/", "/news/")):
        kind = "article"
    row = {name: 0.0 for name in PAGE_TYPE_FEATURES[:6]}
    if kind != "other":
        row[f"pt_{kind}"] = 1.0
    flags = {name: 0.0 for name in PAGE_TYPE_FEATURES[6:]}
    for match in _LDJSON.finditer(html or ""):
        try:
            payload = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        _mark_schema(payload, flags)
    row.update(flags)
    return row


def _mark_schema(node, flags: dict[str, float]) -> None:
    if isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        for item in kinds:
            feature = _SCHEMA_FEATURE.get(str(item))
            if feature:
                flags[feature] = 1.0
        for value in node.values():
            _mark_schema(value, flags)
    elif isinstance(node, list):
        for item in node:
            _mark_schema(item, flags)


def load_tranco(path: str | Path) -> dict[str, int]:
    """Map pay-level domain to Tranco rank. Rank 1 is the most popular domain on that list."""
    file_path = Path(path)
    if file_path.suffix == ".zip":
        with zipfile.ZipFile(file_path) as archive:
            name = archive.namelist()[0]
            text = archive.read(name).decode("utf-8", "replace")
        lines = text.splitlines()
    else:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    ranks: dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rank_text, domain = line.split(",", 1)
        ranks[domain.strip().lower()] = int(rank_text)
    return ranks


def tranco_features(url: str, ranks: dict[str, int]) -> dict[str, float]:
    """Popularity proxy. Absence from the top list is 'not listed', not a backlink count."""
    rank = ranks.get(site_key(url))
    if rank is None:
        return {"tranco_listed": 0.0, "tranco_popularity": 0.0}
    return {"tranco_listed": 1.0, "tranco_popularity": 1.0 / math.log2(rank + 1.0)}


def load_cc_domain_ranks(path: str | Path) -> dict[str, dict[str, float]]:
    """Rows kept from a Common Crawl domain-rank file: pagerank and harmonic centrality."""
    found: dict[str, dict[str, float]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            domain = (row.get("domain") or "").strip().lower()
            if not domain:
                continue
            found[domain] = {
                "pr_val": float(row["pr_val"]),
                "harmonicc_val": float(row["harmonicc_val"]),
                "pr_pos": float(row["pr_pos"]),
                "harmonicc_pos": float(row["harmonicc_pos"]),
                "n_hosts": float(row["n_hosts"]),
            }
    return found


def cc_features(url: str, ranks: dict[str, dict[str, float]]) -> dict[str, float]:
    """Link-graph scores. A domain the crawl file does not contain stays missing, not zero."""
    row = ranks.get(site_key(url))
    if row is None:
        return {"cc_pagerank": float("nan"), "cc_harmonic": float("nan")}
    return {"cc_pagerank": row["pr_val"], "cc_harmonic": row["harmonicc_val"]}
