"""Build, refresh, and report the local page index."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from aeo.ranksim.localindex.candidates import expand_internal, load_snapshot, read_queries_file
from aeo.ranksim.localindex.coverage import coverage_lines, coverage_report
from aeo.ranksim.localindex.credits import MAX_SEARCH_NUM, SCRAPE_TYPICAL, CreditEstimate, CreditSpend
from aeo.ranksim.localindex.fetch import IndexFetcher
from aeo.ranksim.localindex.records import Candidate, PageRecord, canon_url
from aeo.ranksim.localindex.signals import apply_signals
from aeo.ranksim.localindex.store import IndexStore


def run_status(index_dir: str | Path, emit=print) -> int:
    path = Path(index_dir)
    if not (path / "index.sqlite").is_file():
        emit(f"No index at {path}.")
        return 2
    store = IndexStore(path)
    try:
        report = coverage_report(store.candidates(), store.pages())
        for line in coverage_lines(report):
            emit(line)
        row = store.db.execute("SELECT * FROM builds ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None:
            emit(
                f"Last {row['mode']} spent {row['credits_spent']} credits "
                f"(search {row['search_credits']}, scrape {row['scrape_credits']}) of cap {row['credit_cap']}."
            )
    finally:
        store.close()
    return 0


def run_build(
    index_dir: str | Path,
    *,
    snapshots: list[str] | None = None,
    queries_file: str | None = None,
    num: int = 100,
    depth: int = 1,
    credit_cap: int = 300,
    offline: bool = False,
    estimate_only: bool = False,
    refresh: bool = False,
    skip_search: bool = False,
    stale_days: int = 14,
    delay_seconds: float = 1.0,
    use_browser: bool = True,
    max_internal: int = 50,
    allow_private: bool = False,
    user_agent: str = "AEOSimulatorBot/0.1 (+https://example.com/aeo-simulator)",
    api_key: str | None = None,
    timeout: float = 20.0,
    client=None,
    crawler=None,
    browser=None,
    today: str | None = None,
    emit=print,
    seed_candidates: list[Candidate] | None = None,
    tracked_queries: list[str] | None = None,
    only_queries: list[str] | None = None,
    on_progress=None,
) -> int:
    num = max(1, min(int(num), MAX_SEARCH_NUM))
    depth = max(0, int(depth))
    store = IndexStore(index_dir)
    started = _now()
    try:
        pages = store.pages()
        candidates = store.candidates()
        extra_queries = read_queries_file(Path(queries_file)) if queries_file else []
        for query in tracked_queries or []:
            text = " ".join(str(query).split())
            if text and text not in extra_queries:
                extra_queries.append(text)
        if seed_candidates:
            candidates = _merge_candidates(candidates, seed_candidates)
        if not snapshots and not extra_queries and not candidates:
            emit("Pass --snapshot or --queries. The index has no candidates yet.")
            return 2
        for snapshot in snapshots or []:
            loaded, seeded = load_snapshot(Path(snapshot))
            candidates = _merge_candidates(candidates, loaded)
            for url, record in seeded.items():
                pages[url] = _prefer(pages.get(url), record, refresh=refresh, stale_days=stale_days, today=today or date.today().isoformat())
        if only_queries is not None:
            allowed = {" ".join(str(query).split()) for query in only_queries if str(query).strip()}
            candidates = [item for item in candidates if item.query in allowed]
            extra_queries = [query for query in extra_queries if query in allowed]
            if allowed:
                marks = ",".join("?" for _ in allowed)
                store.db.execute(f"DELETE FROM candidates WHERE query NOT IN ({marks})", tuple(sorted(allowed)))
            else:
                store.db.execute("DELETE FROM candidates")
        _remember(store, candidates, pages)
        added = expand_internal(candidates, pages, depth=depth, max_internal=max_internal)
        candidates = _merge_candidates(candidates, added)
        _placeholder(candidates, pages, "offline" if offline else "not_fetched")
        _remember(store, candidates, pages)

        queries = _tracked_queries(candidates, extra_queries)
        top10, labeled, missing_urls = _missing_counts(candidates, pages)
        estimate = CreditEstimate(
            queries=0 if skip_search else len(queries),
            num=num,
            missing_top10=top10,
            missing_labeled=labeled,
            missing_urls=missing_urls,
            credit_cap=credit_cap,
            offline=offline or estimate_only,
            key_set=bool(api_key),
        )
        lines = estimate.lines()
        if api_key and not offline and not estimate_only:
            lines.append("Positions 11-100 are fetched after the search. Their scrapes use the same cap, after top-10 URLs.")
        for line in lines:
            emit(line)
        store.write_estimate(lines)
        if on_progress is not None:
            if not api_key and not offline and not estimate_only:
                on_progress(
                    "index_estimate",
                    "SERPER_API_KEY is not set. Serper search and scrape are skipped. HTTP, the browser, Common Crawl, and Wayback still run.",
                    1,
                    1,
                )
            else:
                on_progress("index_estimate", lines[0], 1, 1)
        if estimate_only:
            _finish(store, candidates, pages, mode="estimate", credit_cap=credit_cap, spent=CreditSpend(credit_cap), offline=True, started=started)
            return 0
        if offline:
            _finish(store, candidates, pages, mode="offline", credit_cap=credit_cap, spent=CreditSpend(credit_cap), offline=True, started=started)
            for line in coverage_lines(coverage_report(candidates, pages)):
                emit(line)
            return 0

        credits = CreditSpend(credit_cap)
        fetcher = IndexFetcher(
            user_agent=user_agent,
            credits=credits,
            api_key=api_key,
            delay_seconds=delay_seconds,
            allow_private=allow_private,
            timeout=timeout,
            use_browser=use_browser,
            client=client,
            crawler=crawler,
            browser=browser,
            today=today,
            scrape_reserve=top10 * SCRAPE_TYPICAL if api_key else 0,
        )
        try:
            if not api_key:
                skip_search = True
            if not skip_search:
                for query in queries:
                    hits, _attempts = fetcher.search(query, num)
                    found = []
                    for position, url, _title, _snippet in hits:
                        canonical = canon_url(url)
                        if canonical is None:
                            continue
                        found.append(
                            Candidate(
                                url=canonical,
                                query=query,
                                position=int(position),
                                source="serper",
                                snapshot_id="live",
                            )
                        )
                    candidates = _merge_candidates(candidates, found)
            _remember(store, candidates, pages)
            for _hop in range(depth + 1):
                queue = _fetch_queue(candidates, pages, refresh=refresh, stale_days=stale_days, today=today or fetcher.today)
                total = len(queue)
                for index, url in enumerate(queue, start=1):
                    if on_progress is not None:
                        on_progress("index_fetch", f"Fetching {url}", index - 1, total)
                    record = fetcher.fetch(url)
                    pages[url] = record
                    store.upsert_page(record)
                    if on_progress is not None:
                        state = "text stored" if record.status == "full" else (record.miss_reason or "missing")
                        on_progress("index_fetch", f"{url}: {state}", index, total)
                added = expand_internal(candidates, pages, depth=depth, max_internal=max_internal)
                if not added:
                    break
                candidates = _merge_candidates(candidates, added)
                _placeholder(candidates, pages, "not_fetched")
                _remember(store, candidates, pages)
        finally:
            fetcher.close()
        _finish(store, candidates, pages, mode="refresh" if refresh else "build", credit_cap=credit_cap, spent=credits, offline=False, started=started)
        report = coverage_report(candidates, pages)
        for line in coverage_lines(report):
            emit(line)
        if on_progress is not None:
            on_progress(
                "index_coverage",
                f"Top-10 text coverage: {report['top10_with_text']} of {report['top10_rows']} ({report['top10_text_pct']:.1%}).",
                int(report["top10_with_text"]),
                int(report["top10_rows"]),
            )
        emit(f"Credits spent: {credits.spent} (search {credits.search}, scrape {credits.scrape}) of cap {credits.cap}.")
        return 0
    finally:
        store.close()


def _finish(store: IndexStore, candidates: list[Candidate], pages: dict[str, PageRecord], *, mode: str, credit_cap: int, spent: CreditSpend, offline: bool, started: str) -> None:
    _remember(store, candidates, pages)
    stats = apply_signals(pages)
    for page in pages.values():
        store.update_signals(page)
    edges = []
    known = set(pages)
    for src, page in pages.items():
        for dst, anchor in page.outlinks:
            if dst in known and dst != src:
                edges.append((src, dst, anchor))
    store.replace_edges(edges)
    store.replace_domain_stats(stats)
    store.commit()
    report = coverage_report(candidates, pages)
    store.write_coverage(report)
    store.write_parquet()
    store.add_build(
        started=started,
        finished=_now(),
        mode=mode,
        credit_cap=credit_cap,
        credits_spent=spent.spent,
        search_credits=spent.search,
        scrape_credits=spent.scrape,
        offline=1 if offline else 0,
    )


def _remember(store: IndexStore, candidates: list[Candidate], pages: dict[str, PageRecord]) -> None:
    for item in candidates:
        store.upsert_candidate(item)
    for page in pages.values():
        store.upsert_page(page)


def _merge_candidates(current: list[Candidate], extra: list[Candidate]) -> list[Candidate]:
    seen = {(item.url, item.query, item.source, item.snapshot_id): item for item in current}
    for item in extra:
        seen[(item.url, item.query, item.source, item.snapshot_id)] = item
    return list(seen.values())


def _prefer(old: PageRecord | None, new: PageRecord, *, refresh: bool, stale_days: int, today: str) -> PageRecord:
    if old is None:
        return new
    if new.status == "full" and old.status != "full":
        return new
    if new.status == "full" and old.status == "full" and refresh and _stale(old, stale_days, today):
        return new
    return old


def _placeholder(candidates: list[Candidate], pages: dict[str, PageRecord], reason: str) -> None:
    for item in candidates:
        if item.url in pages:
            continue
        pages[item.url] = PageRecord(url=item.url, status="missing", miss_reason=reason, source="")


def _tracked_queries(candidates: list[Candidate], extra: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item.position is None or item.query in seen:
            continue
        seen.add(item.query)
        found.append(item.query)
    for query in extra:
        if query not in seen:
            seen.add(query)
            found.append(query)
    return found


def _missing_counts(candidates: list[Candidate], pages: dict[str, PageRecord]) -> tuple[int, int, int]:
    top10: set[str] = set()
    labeled: set[str] = set()
    urls: set[str] = set()
    for item in candidates:
        urls.add(item.url)
        if _is_full(pages.get(item.url)):
            continue
        if item.position is None:
            continue
        labeled.add(item.url)
        if item.position <= 10:
            top10.add(item.url)
    missing_urls = sum(1 for url in urls if not _is_full(pages.get(url)))
    return len(top10), len(labeled), missing_urls


def _is_full(page: PageRecord | None) -> bool:
    return page is not None and page.status == "full" and bool((page.main_text or "").strip())


def _fetch_queue(candidates, pages, *, refresh: bool, stale_days: int, today: str) -> list[str]:
    urls = {item.url for item in candidates}
    wanted = [url for url in urls if _needs_fetch(pages.get(url), refresh=refresh, stale_days=stale_days, today=today)]

    def sort_key(url: str) -> tuple:
        positions = [item.position for item in candidates if item.url == url and item.position is not None]
        best = min(positions) if positions else 10_000
        group = 0 if best <= 10 else 1 if best < 10_000 else 2
        return (group, best, url)

    wanted.sort(key=sort_key)
    return wanted


def _needs_fetch(page: PageRecord | None, *, refresh: bool, stale_days: int, today: str) -> bool:
    if page is None or page.status != "full":
        return True
    return bool(refresh and _stale(page, stale_days, today))


def _stale(page: PageRecord, stale_days: int, today: str) -> bool:
    if stale_days <= 0:
        return True
    if not page.captured_on:
        return True
    try:
        captured = date.fromisoformat(page.captured_on[:10])
        current = date.fromisoformat(today[:10])
    except ValueError:
        return True
    return (current - captured).days >= stale_days


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
