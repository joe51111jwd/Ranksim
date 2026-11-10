"""Crawl the URLs in a real Google export, plus the SmartCrew sitemap."""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from aeo.config import Settings
from aeo.crawl.fetch import Crawler
from aeo.crawl.urls import domain_of
from aeo.errors import ProviderConfigError, UnsafeURL
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.keywords import load_sitemap_urls
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.saved_html import load_saved_html
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.urlkey import match_key
from aeo.ranksim.store import EVALUATION_EXCLUDED_SOURCES, RsPage, append_serp, open_session


def index_serp_csv(
    path: str | Path,
    database: str,
    settings: Settings,
    *,
    include_sitemap: bool = True,
    delay_seconds: float = 0.4,
    workers: int = 8,
    saved_dir: Path | None = None,
    skip_fetched: bool = True,
) -> dict:
    labels = load_serp_file(path)
    saved, missing_files = load_saved_html(saved_dir)
    urls = []
    seen = set()
    for label in labels:
        if label.url not in seen:
            seen.add(label.url)
            urls.append(label.url)
    if include_sitemap:
        for url in sorted(load_sitemap_urls()):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    already = _already_fetched(database) if skip_fetched else set()
    # A draft stored at this exact URL is not a crawl of it, even when the live page is stored
    # under another spelling: every join prefers the exact URL, so it would keep finding the draft.
    drafts = _stored_drafts(database) if skip_fetched else set()

    def is_stored(url: str) -> bool:
        return match_key(url) in already and url not in drafts

    saved_used: list[str] = []
    to_crawl: list[str] = []
    for url in urls:
        if url in saved and saved[url].strip():
            saved_used.append(url)
            continue
        if is_stored(url):
            continue
        to_crawl.append(url)
    by_host: dict[str, list[str]] = defaultdict(list)
    for url in to_crawl:
        by_host[domain_of(url) or url].append(url)
    fetched: list[tuple[str, FetchResult]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_fetch_host, host_urls, settings, delay_seconds) for host_urls in by_host.values()
        ]
        for future in as_completed(futures):
            fetched.extend(future.result())
    session = open_session(database)
    skipped = [url for url in urls if is_stored(url) and url not in saved]
    counts = {"fetched": 0, "blocked": 0, "error": 0, "saved_html": 0, "skipped": len(skipped)}
    # Search Console positions are averages over a period for your own pages, not a results page.
    # They are not stored as collections, so export-serp, noise and the ledger never read them as one.
    search_console = bool(labels) and labels[0].kind == "search_console_avg"
    try:
        for url in saved_used:
            store_fetched_page(
                session,
                FetchResult(url=url, html=saved[url], status="fetched", fetch_ms=None, source="saved_html"),
            )
            counts["saved_html"] += 1
        for requested, result in fetched:
            result.url = requested
            store_fetched_page(session, result)
            counts[result.status] = counts.get(result.status, 0) + 1
        grouped: dict[str, list] = defaultdict(list)
        for label in ([] if search_console else labels):
            grouped[label.query].append(label)
        for query, rows in grouped.items():
            hits = [
                SerpHit(
                    position=int(round(row.position)),
                    url=row.url,
                    title="",
                    snippet="",
                    provider="browser_export",
                    synthetic=False,
                    is_google=True,
                )
                for row in sorted(rows, key=lambda item: item.position)
            ]
            first = rows[0]
            append_serp(
                session,
                query,
                hits,
                provider="browser_export",
                synthetic=False,
                is_google=True,
                country=first.country,
                language=first.language,
                device=first.device,
                collected_at=_observed_time(first.observed_on),
                source="label_file",
            )
    finally:
        session.close()
    return {
        "labels": len(labels),
        "queries": len({row.query for row in labels}),
        "attempted": len(to_crawl),
        "saved_missing": missing_files,
        "search_console": search_console,
        **counts,
        "blocked_urls": [url for url, result in fetched if result.status == "blocked"],
        "error_urls": [(url, result.error) for url, result in fetched if result.status == "error"],
    }


def _already_fetched(database: str) -> set[str]:
    session = open_session(database)
    try:
        rows = session.query(RsPage).filter(RsPage.status == "fetched").all()
        # A draft stored under a live URL is not a crawl of that URL: crawl it so the live page
        # can replace the draft as the evaluation snapshot.
        return {
            match_key(row.url)
            for row in rows
            if (row.raw_html or "").strip() and (row.source or "unknown") not in EVALUATION_EXCLUDED_SOURCES
        }
    finally:
        session.close()


def _stored_drafts(database: str) -> set[str]:
    """Exact URLs whose stored row is a draft, so a crawl of the live page can replace it."""
    session = open_session(database)
    try:
        rows = session.query(RsPage.url, RsPage.source).all()
        return {url for url, source in rows if (source or "unknown") in EVALUATION_EXCLUDED_SOURCES}
    finally:
        session.close()


def _fetch_host(urls: list[str], settings: Settings, delay_seconds: float) -> list[tuple[str, FetchResult]]:
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=delay_seconds,
        allow_private=settings.crawl_allow_private,
        timeout=min(settings.request_timeout_seconds, 20.0),
    )
    out: list[tuple[str, FetchResult]] = []
    try:
        for url in urls:
            started = time.perf_counter()
            try:
                result = crawler.fetch(url)
            except UnsafeURL as exc:
                out.append((url, FetchResult(url=url, html="", status="blocked", error=f"Unsafe URL: {exc}")))
                continue
            except ProviderConfigError:
                raise  # a missing crawler contact stops the crawl with its message, not once per URL
            except Exception as exc:  # noqa: BLE001 - one bad URL must not abort the whole crawl
                out.append((url, FetchResult(url=url, html="", status="error", error=f"{type(exc).__name__}: {exc}")))
                continue
            elapsed = (time.perf_counter() - started) * 1000
            if result.blocked:
                out.append((url, FetchResult(url=url, html="", status="blocked", error=result.error, fetch_ms=elapsed)))
            elif result.error or not result.html:
                out.append(
                    (url, FetchResult(url=url, html="", status="error", error=result.error or "empty response", fetch_ms=elapsed))
                )
            else:
                out.append((url, FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed, final_url=result.final_url)))
    finally:
        crawler.close()
    return out


def _observed_time(observed_on: str | None):
    """The label file's collection date as a UTC midnight, or None (then the import time is used)."""
    from datetime import date, datetime, timezone

    if not observed_on:
        return None
    try:
        day = date.fromisoformat(observed_on[:10])
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

