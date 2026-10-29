"""CLI for the SmartCrew rank simulator.

Examples:
  python -m aeo.ranksim keywords
  python -m aeo.ranksim gaps
  python -m aeo.ranksim collect --mock
  python -m aeo.ranksim calibrate
  python -m aeo.ranksim test page.html --url https://www.smartcrewhq.com/dirtwork-software/
  python -m aeo.ranksim track
  python -m aeo.ranksim report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from aeo.config import get_settings
from aeo.crawl.fetch import Crawler
from aeo.errors import ProviderConfigError, UnsafeURL
from aeo.ranksim.authority import authority_client
from aeo.ranksim.keywords import build_keywords, gap_summary, page_gaps, refresh_sitemap, write_keywords_csv
from aeo.ranksim.loop import FetchResult, attach_authority, collect_corpus, calibrate, page_from_html, run_test, track_ranks
from aeo.ranksim.report import render_report
from aeo.ranksim.serp import resolve_serp
from aeo.ranksim.store import open_session


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ranksim", description="SmartCrew Google rank simulator")
    parser.add_argument("--database", default=None, help="SQLAlchemy URL. Default is RANKSIM_DATABASE_URL.")
    sub = parser.add_subparsers(dest="command", required=True)

    keywords = sub.add_parser("keywords", help="Write keywords.csv")
    keywords.add_argument("--refresh-sitemap", action="store_true")

    sub.add_parser("gaps", help="Print the page gap list")

    collect = sub.add_parser("collect", help="Fetch SERPs and crawl the focused corpus")
    collect.add_argument("--provider", default="auto")
    collect.add_argument("--mock", action="store_true", help="Use the synthetic SERP. Not Google.")
    collect.add_argument("--depth", type=int, default=None)
    collect.add_argument("--max-pages", type=int, default=None)

    sub.add_parser("calibrate", help="Fit weights to the stored SERP order")

    test = sub.add_parser("test", help="Re-score every query after one page change")
    test.add_argument("source", help="A page URL, a staging URL, or a local HTML file")
    test.add_argument("--url", default=None, help="Canonical URL when source is a file or staging host")
    test.add_argument("--replaces", default=None, help="Stored URL whose HTML should be replaced")

    track = sub.add_parser("track", help="Record today's real ranks for the keyword set")
    track.add_argument("--provider", default="auto")
    track.add_argument("--mock", action="store_true")
    track.add_argument("--depth", type=int, default=None)

    report = sub.add_parser("report", help="Write the HTML report")
    report.add_argument("--out", default=None)

    args = parser.parse_args(argv)
    settings = get_settings()
    database = args.database or settings.ranksim_database_url
    try:
        if args.command == "keywords":
            _keywords(args.refresh_sitemap)
        elif args.command == "gaps":
            _gaps()
        elif args.command == "collect":
            _collect(settings, database, args)
        elif args.command == "calibrate":
            _calibrate(database)
        elif args.command == "test":
            _test(settings, database, args)
        elif args.command == "track":
            _track(settings, database, args)
        elif args.command == "report":
            _report(database, args.out)
        else:
            parser.error(f"Unknown command {args.command}")
    except ProviderConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


def _keywords(refresh: bool) -> None:
    if refresh:
        response = httpx.get("https://www.smartcrewhq.com/sitemap.xml", timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        refresh_sitemap(response.text)
        print("Refreshed sitemap snapshot from https://www.smartcrewhq.com/sitemap.xml")
    path = write_keywords_csv()
    summary = gap_summary()
    print(f"Wrote {path}")
    print(f"{summary['queries']} queries, {summary['with_page']} with a sitemap page, {summary['gaps']} page gaps")


def _gaps() -> None:
    rows = build_keywords()
    summary = gap_summary(rows)
    print(f"{summary['queries']} queries")
    print(f"{summary['with_page']} map to a URL in the 2026-10-29 sitemap")
    print(f"{summary['gaps']} page gaps")
    for row in page_gaps(rows):
        print(f"GAP\t{row.query}\t{row.suggested_target_page}")


def _collect(settings, database: str, args) -> None:
    choice = "mock" if args.mock else args.provider
    provider = resolve_serp(settings, choice)
    keywords = build_keywords()
    depth = args.depth or settings.ranksim_serp_depth
    max_pages = args.max_pages or settings.ranksim_max_pages
    if max_pages > 1000:
        print("Capping max pages at 1000.")
        max_pages = 1000
    fetcher, closer = _live_fetcher(settings)
    session = open_session(database)
    try:
        result = collect_corpus(session, keywords, provider, fetcher, depth=depth, max_pages=max_pages)
    finally:
        session.close()
        closer()
        close = getattr(provider, "close", None)
        if close:
            close()
    print(f"Provider: {result.provider}. Google: {'yes' if result.is_google else 'no'}. Synthetic: {'yes' if result.synthetic else 'no'}.")
    print(f"Queries fetched: {result.queries}. SERP hits: {result.serp_hits}. Pages stored: {result.pages_stored}.")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    authority = authority_client(settings)
    if authority is None:
        print("Authority proxy skipped. It stays unused unless RANKSIM_AUTHORITY=dataforseo and DataForSEO credentials are set.")
        return
    session = open_session(database)
    try:
        scored = attach_authority(session, authority)
    finally:
        session.close()
        authority.close()
    print(f"Authority scores stored for {scored} pages. Source: DataForSEO backlink rank.")


def _calibrate(database: str) -> None:
    session = open_session(database)
    try:
        report, label, provider = calibrate(session, build_keywords())
    finally:
        session.close()
    print(f"Label: {label}")
    print(f"Provider: {provider}")
    print(f"Queries: {report.n_queries}. Pairs: {report.n_pairs}.")
    print(f"Mean Spearman: {_num(report.spearman)}")
    print(f"Mean NDCG at 10: {_num(report.ndcg_at_10)}")
    if label != "calibrated_on_google_serp":
        print("This is not a Google accuracy number.")


def _test(settings, database: str, args) -> None:
    html, url, label = _load_source(settings, args.source, args.url)
    replaces = args.replaces or url
    doc = page_from_html(url, html, is_ours=True)
    session = open_session(database)
    try:
        deltas = run_test(session, build_keywords(), doc, replaces=replaces, source_label=label)
    finally:
        session.close()
    improved = sum(1 for row in deltas if row.delta is not None and row.delta > 0)
    regressed = sum(1 for row in deltas if row.delta is not None and row.delta < 0)
    print(f"Tested {label}")
    print("Positions are predicted. They are not live Google ranks.")
    print(f"Improved: {improved}. Regressed: {regressed}. Queries: {len(deltas)}.")
    print("query\tbefore\tafter\tchange\tcompetitor_gap")
    for row in deltas:
        gap = " | ".join(row.competitor_gaps)
        print(
            f"{row.query}\t{_cell(row.before_predicted_position)}\t{_cell(row.after_predicted_position)}\t{_cell(row.delta)}\t{gap}"
        )


def _track(settings, database: str, args) -> None:
    choice = "mock" if args.mock else args.provider
    provider = resolve_serp(settings, choice)
    depth = args.depth or settings.ranksim_serp_depth
    session = open_session(database)
    try:
        rows = track_ranks(session, build_keywords(), provider, depth=depth)
    finally:
        session.close()
        close = getattr(provider, "close", None)
        if close:
            close()
    found = sum(1 for row in rows if row.our_position is not None)
    kind = "Google" if rows and rows[0].is_google and not rows[0].synthetic else provider.name
    print(f"Recorded {len(rows)} queries from {kind}.")
    if provider.synthetic:
        print("Synthetic mock tracker. Not a Google rank check.")
    print(f"SmartCrew was inside the fetched results for {found} queries.")
    print("A blank position means the domain was outside this fetch, not a made-up rank.")


def _report(database: str, out: str | None) -> None:
    html = render_report(database)
    target = Path(out) if out else Path("smartcrew") / "report.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)
    print(f"Wrote {target}")


def _load_source(settings, source: str, url: str | None) -> tuple[str, str, str]:
    path = Path(source)
    if path.exists() and path.is_file():
        if not url:
            raise SystemExit("A local HTML file needs --url, the canonical page you are testing.")
        return path.read_text(), url, f"file:{path}"
    if source.startswith("http://") or source.startswith("https://"):
        fetched = _fetch_one(settings, source)
        if fetched.status != "fetched":
            raise SystemExit(f"Fetch failed for {source}: {fetched.error or fetched.status}")
        canonical = url or source
        return fetched.html, canonical, source
    raise SystemExit(f"Source not found: {source}")


def _fetch_one(settings, url: str) -> FetchResult:
    import time

    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=0,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )
    try:
        started = time.perf_counter()
        try:
            result = crawler.fetch(url)
        except UnsafeURL as exc:
            return FetchResult(url=url, html="", status="blocked", error=str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        if result.blocked or result.error or not result.html:
            return FetchResult(url=url, html="", status="error", error=result.error or result.robots_status, fetch_ms=elapsed)
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed)
    finally:
        crawler.close()


def _live_fetcher(settings):
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=settings.crawl_delay_seconds,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )

    def fetch(url: str) -> FetchResult:
        import time

        started = time.perf_counter()
        try:
            result = crawler.fetch(url)
        except UnsafeURL as exc:
            return FetchResult(url=url, html="", status="blocked", error=str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        if result.blocked:
            return FetchResult(url=url, html="", status="blocked", error=result.error, fetch_ms=elapsed)
        if result.error or not result.html:
            return FetchResult(url=url, html="", status="error", error=result.error or "empty response", fetch_ms=elapsed)
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed)

    return fetch, crawler.close


def _num(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value)


if __name__ == "__main__":
    main()
