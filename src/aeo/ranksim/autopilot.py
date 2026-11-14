"""Autopilot: type a site, get a report.

1. read_site (site.py): fetch the homepage and the pages its sitemap lists (robots.txt respected), and store them.
2. discover_searches (discovery.py): the searches the site is written for, taken from its own titles and headings.
3. The project's normal pull asks Google for each search, fetches every ranking page and trains the engine.
4. pick_competitors (competitors.py): the domains that take the most top 10 slots across those searches.
5. build_report (report.py): where the site ranks, who it is up against, and specific issues with a comment on each
   (audit.py).

Nothing is invented. A search the site does not rank for says so. Without a Google results key on the server, the
report still checks the site's own pages and says that rankings need search data.

This module only re-exports those names so older imports keep working. New code imports from the modules above.
"""

from __future__ import annotations

from aeo.ranksim.audit import (
    CHECKS,
    META_LIMIT,
    THIN_WORDS,
    TITLE_LIMIT,
    AuditContext,
    _issue,
    _short,
    audit,
)
from aeo.ranksim.competitors import PLATFORMS, _base, pick_competitors, share_of_top10
from aeo.ranksim.discovery import (
    BRAND_AFFIXES,
    COMMERCIAL,
    GENERIC,
    LABEL_STARTS,
    LEADING,
    MAX_SEARCHES,
    MIN_SCORE,
    SPLIT,
    _clean,
    _jaccard,
    brand_of,
    brand_tokens,
    discover_searches,
)
from aeo.ranksim.report import _best_page, _pages_for, _tokens, build_report, stored_site_pages
from aeo.ranksim.site import MAX_SITE_PAGES, SitePage, _is_page, _same_site, _spread, _unique, page_facts, read_site

__all__ = [
    "BRAND_AFFIXES",
    "CHECKS",
    "COMMERCIAL",
    "GENERIC",
    "LABEL_STARTS",
    "LEADING",
    "MAX_SEARCHES",
    "MAX_SITE_PAGES",
    "META_LIMIT",
    "MIN_SCORE",
    "PLATFORMS",
    "SPLIT",
    "THIN_WORDS",
    "TITLE_LIMIT",
    "AuditContext",
    "SitePage",
    "_base",
    "_best_page",
    "_clean",
    "_is_page",
    "_issue",
    "_jaccard",
    "_pages_for",
    "_same_site",
    "_short",
    "_spread",
    "_tokens",
    "_unique",
    "audit",
    "brand_of",
    "brand_tokens",
    "build_report",
    "discover_searches",
    "page_facts",
    "pick_competitors",
    "read_site",
    "share_of_top10",
    "stored_site_pages",
]
