"""The user's own site: read it, store it, and pull the facts the report and the audit use.

read_site fetches the homepage and the pages its sitemap lists (robots.txt respected), stores every answer, and
returns one SitePage per page. page_facts turns stored or fetched HTML into a SitePage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

MAX_SITE_PAGES = 30


@dataclass
class SitePage:
    url: str
    status: str  # fetched, blocked, error
    error: str = ""
    title: str = ""
    meta: str = ""
    h1: str = ""
    h1_count: int = 0
    h2s: list[str] = field(default_factory=list)
    words: int = 0
    tables: int = 0
    has_faq: bool = False
    updated: str | None = None
    links: list[str] = field(default_factory=list)
    fetch_ms: float | None = None

    @property
    def path(self) -> str:
        parts = urlsplit(self.url)
        return parts.path or "/"


def page_facts(url: str, html: str, status: str = "fetched", error: str = "", fetch_ms: float | None = None) -> SitePage:
    from aeo.crawl.extract import extract_html

    if status != "fetched" or not (html or "").strip():
        return SitePage(url=url, status=status or "error", error=error, fetch_ms=fetch_ms)
    found = extract_html(html, url)
    h2s = [str(item.get("text") or "").strip() for item in found.headings if str(item.get("level") or item.get("tag") or "").lower() in {"2", "h2"}]
    lower = html.lower()
    return SitePage(
        url=url,
        status="fetched",
        title=(found.title or "").strip(),
        meta=(found.meta_description or "").strip(),
        h1=(found.h1 or "").strip(),
        h1_count=len(re.findall(r"<h1[\s>]", lower)),
        h2s=[text for text in h2s if text][:12],
        words=int(found.word_count or 0),
        tables=lower.count("<table"),
        has_faq="faq" in " ".join(str(item.get("text") or "") for item in found.headings).lower() or "faqpage" in lower,
        updated=found.updated or found.published,
        links=list(found.links or []),
        fetch_ms=fetch_ms,
    )


def read_site(site: str, settings, database: str, *, fetcher=None, max_pages: int = MAX_SITE_PAGES, progress=None) -> list[SitePage]:
    """Fetch the homepage and up to max_pages of the site's own pages, store them, and return their facts."""
    from aeo.crawl.sitemap import parse_sitemap
    from aeo.ranksim.loop import FetchResult
    from aeo.ranksim.project import _crawler_batch, _record_fetch, clean_site
    from aeo.ranksim.store import open_session

    host = clean_site(site)
    home = f"https://{host}/"
    fetch = fetcher or _crawler_batch(settings, 0.2)
    say = progress or (lambda *_args: None)

    say("Reading your homepage.", 0, 1)
    first = fetch([home])[0]
    home_page = page_facts(home, first.html, first.status, first.error or "", first.fetch_ms)
    results: list[tuple[str, FetchResult]] = [(home, first)]

    urls: list[str] = []
    for start in (f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"):
        try:
            got = fetch([start])[0]
        except Exception:  # noqa: BLE001 - a missing sitemap is normal
            continue
        if got.status != "fetched" or not got.html:
            continue
        try:
            kind, locs = parse_sitemap(got.html)
        except ValueError:
            continue
        if kind == "urlset":
            urls.extend(locs)
        else:
            for child in locs[:3]:
                item = fetch([child])[0]
                if item.status == "fetched" and item.html:
                    try:
                        child_kind, child_locs = parse_sitemap(item.html)
                    except ValueError:
                        continue
                    if child_kind == "urlset":
                        urls.extend(child_locs)
                if len(urls) >= max_pages * 3:
                    break
        if urls:
            break
    if not urls:
        urls = [urljoin(home, link) for link in home_page.links]
    own = [url for url in _unique(urls) if _same_site(url, host) and _is_page(url) and url.rstrip("/") != home.rstrip("/")]
    own = _spread(own, max_pages - 1)
    for index, url in enumerate(own, start=1):
        say(f"Reading {urlsplit(url).path or '/'}", index, len(own))
        results.append((url, fetch([url])[0]))

    session = open_session(database)
    try:
        for url, result in results:
            _record_fetch(session, url, result, None, True, [])
        session.commit()
    finally:
        session.close()
    pages = [home_page] + [page_facts(url, result.html, result.status, result.error or "", result.fetch_ms) for url, result in results[1:]]
    return pages


def _unique(items):
    seen = set()
    out = []
    for item in items:
        key = item.rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _same_site(url: str, host: str) -> bool:
    bare = host.removeprefix("www.")
    found = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return found == bare


def _is_page(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return not re.search(r"\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|xml|css|js|ico|mp4|mp3|woff2?)$", path) and "/wp-content/" not in path and "/tag/" not in path


def _spread(urls: list[str], limit: int) -> list[str]:
    """Shallow pages first (they are usually the ones that target searches), then the rest in sitemap order."""
    ranked = sorted(urls, key=lambda url: (urlsplit(url).path.strip("/").count("/"), len(url)))
    return ranked[: max(0, limit)]
