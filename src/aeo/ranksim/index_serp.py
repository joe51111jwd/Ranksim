"""Crawl the URLs in a real Google export, plus the SmartCrew sitemap."""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from aeo.config import Settings
from aeo.crawl.fetch import Crawler
from aeo.crawl.urls import domain_of, normalize_url
from aeo.errors import ProviderConfigError, UnsafeURL
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.keywords import load_sitemap_urls
from aeo.ranksim.lambdamart import _lookup_page
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.saved_html import load_saved_html
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.urlkey import match_key
from aeo.ranksim.store import EVALUATION_EXCLUDED_SOURCES, RsPage, append_serp, collection_dated, latest_collection, open_session


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
    # Search Console positions are averages over a period for your own pages, not a results page.
    # They are not stored as collections, so export-serp, noise and the ledger never read them as one.
    search_console = bool(labels) and labels[0].kind == "search_console_avg"
    if not search_console:
        for label in labels:
            if label.observed_on and _observed_time(label.observed_on) is None:
                # 11/5/2026 could be May or November. Refuse it before anything is stored.
                raise ValueError(
                    f"observed_on {label.observed_on!r} for query {label.query!r} is not a YYYY-MM-DD date. "
                    "Nothing was stored. Write the dates as YYYY-MM-DD and run index-serp again."
                )
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
    # Training finds a label's page with _lookup_page: the exact URL, then normalize_url, then
    # another spelling of the same page. When that join reaches a draft, the live page is not
    # stored for this label yet, even when it is stored under a third spelling, so crawl it.
    joined = _joined_sources(database) if skip_fetched else {}

    def is_stored(url: str) -> bool:
        return match_key(url) in already and _lookup_page(joined, url) not in EVALUATION_EXCLUDED_SOURCES

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
    behind_dated = 0  # queries of an undated file whose dated results list stays the latest
    marked = 0  # lists an older version stored from these averages, now marked not Google
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
        if search_console:
            marked = _mark_stored_averages(session, labels)
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
            observed = _observed_time(first.observed_on)
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
                collected_at=observed,
                source="label_file",
            )
            if observed is None:
                latest = latest_collection(session, query)
                if latest is not None and collection_dated(latest):
                    behind_dated += 1
    finally:
        session.close()
    return {
        "labels": len(labels),
        "queries": len({row.query for row in labels}),
        "attempted": len(to_crawl),
        "saved_missing": missing_files,
        "search_console": search_console,
        "search_console_marked": marked,
        "dated_rows": any(label.observed_on for label in labels),
        "undated_behind_dated": behind_dated,
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


def _joined_sources(database: str) -> dict[str, str]:
    """The source of each fetched row, keyed as corpus.load_corpus_docs keys pages for training:
    by stored URL, then by normalize_url in stored order, never over another row's stored URL.
    A draft found this way is not a crawl."""
    session = open_session(database)
    try:
        rows = session.query(RsPage.url, RsPage.source).filter(RsPage.status == "fetched").all()
    finally:
        session.close()
    sources = {url: source or "unknown" for url, source in rows}
    stored = set(sources)
    for url, source in list(sources.items()):
        try:
            key = normalize_url(url)
        except ValueError:
            continue
        if key not in stored:
            sources[key] = source
    return sources


def _mark_stored_averages(session, labels) -> int:
    """An older version stored a Search Console file as a results list. Mark each list it could
    have stored from this file as not Google, so export-serp, the latest list and the ledger never
    read it as a results page: same query, the same rounded positions and URLs, and the same day.
    That version stored a query's list at UTC midnight of its date in the file, or at the import
    time when it had none, so a dated label list on another day, or on any day for a file with no
    dates, is left alone. Nothing is deleted. Returns how many."""
    from aeo.ranksim.store import RsSerpCollection, _utc, collection_hits, legacy_dated_import

    wanted: dict[str, list] = defaultdict(list)
    days: dict[str, set] = defaultdict(set)
    undated: set[str] = set()
    for label in labels:
        wanted[label.query].append((int(round(label.position)), label.url))
        observed = _observed_time(label.observed_on)
        if observed is None:
            undated.add(label.query)
        else:
            days[label.query].add(observed.date())
    marked = 0
    for query, hits in wanted.items():
        stored = session.query(RsSerpCollection).filter(
            RsSerpCollection.query == query,
            RsSerpCollection.source == "label_file",
            RsSerpCollection.provider == "browser_export",
            RsSerpCollection.is_google.is_(True),
            RsSerpCollection.dated.is_(None),  # only rows from before the dated column
        )
        for collection in stored.all():
            if legacy_dated_import(collection):
                if _utc(collection.collected_at).date() not in days[query]:
                    continue
            elif query not in undated:
                continue
            rows = collection_hits(session, collection.id)
            if sorted((row.position, row.url) for row in rows) != sorted(hits):
                continue
            collection.is_google = False
            for row in rows:
                row.is_google = False
            marked += 1
    session.commit()
    return marked


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

