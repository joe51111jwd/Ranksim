"""Reports. Empty sections stay empty instead of showing made-up ranks.

render_report: the one-page HTML report of the SmartCrew keyword run (the CLI and the old API).
build_report: the Studio report for one project (GET /api/v1/projects/{slug}/report): where the site ranks, who
it is up against, and the audit's issues. Plugins add their own keys at the end through extend_report.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from aeo.crawl.urls import domain_of
from aeo.ranksim.competitors import share_of_top10
from aeo.ranksim.discovery import _clean
from aeo.ranksim.keywords import Keyword, build_keywords, gap_summary, page_gaps
from aeo.ranksim.site import SitePage, page_facts
from aeo.ranksim.store import RsPage, latest_checks, latest_test, latest_weights, open_session


def render_report(database_url: str | None = None, keywords: list[Keyword] | None = None) -> str:
    rows = keywords if keywords is not None else build_keywords()
    summary = gap_summary(rows)
    gaps = page_gaps(rows)
    calibration = _calibration_block(database_url)
    test_block = _test_block(database_url)
    track_block = _track_block(database_url)
    gap_rows = "\n".join(
        "<tr>"
        f"<td>{escape(row.query)}</td>"
        f"<td>{escape(row.intent)}</td>"
        f"<td>{escape(row.suggested_target_page)}</td>"
        f"<td>{escape(row.notes)}</td>"
        "</tr>"
        for row in gaps
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SmartCrew rank simulator</title>
<style>
  body {{ font-family: Georgia, serif; margin: 0; background: #f4f1ea; color: #1c1915; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 2rem; line-height: 1.2; margin-bottom: 0.25rem; }}
  h2 {{ font-size: 1.25rem; margin-top: 2rem; }}
  p, li {{ line-height: 1.45; }}
  .banner {{ background: #fff; border: 1px solid #d9d2c5; padding: 12px 14px; }}
  .stats {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .stat {{ background: #fff; border: 1px solid #d9d2c5; padding: 12px 14px; min-width: 140px; }}
  .stat b {{ display: block; font-size: 1.4rem; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 0.92rem; }}
  th, td {{ border-bottom: 1px solid #e4ddd0; text-align: left; padding: 8px; vertical-align: top; }}
  th {{ background: #efeae0; }}
  code {{ font-family: ui-monospace, monospace; font-size: 0.85em; }}
</style>
</head>
<body>
<main>
  <h1>SmartCrew rank simulator</h1>
  <p class="banner">Predicted positions are model scores on our index. They are not live Google ranks. LambdaMART accuracy is omitted until a file of real Google positions is provided. See docs/accuracy.md.</p>
  <div class="stats">
    <div class="stat"><b>{summary["queries"]}</b>target queries</div>
    <div class="stat"><b>{summary["with_page"]}</b>queries with a sitemap page</div>
    <div class="stat"><b>{summary["gaps"]}</b>page gaps</div>
  </div>
  <h2>Calibration</h2>
  {calibration}
  <h2>Latest page test</h2>
  {test_block}
  <h2>Real rank tracker</h2>
  {track_block}
  <h2>Page gap list</h2>
  <p>A gap means the suggested URL is not in the sitemap snapshot from 2026-10-29. The list is the keyword map, not a rank.</p>
  <table>
    <thead><tr><th>Query</th><th>Intent</th><th>Suggested page</th><th>Note</th></tr></thead>
    <tbody>
    {gap_rows}
    </tbody>
  </table>
</main>
</body>
</html>
"""


def _calibration_block(database_url: str | None) -> str:
    row = _load(database_url, latest_weights)
    if row is None:
        return (
            "<p>Google calibration has not been run. No stored SERP is loaded, "
            "so there is no Spearman or NDCG number on this page.</p>"
        )
    spearman = _fmt(row.spearman)
    ndcg = _fmt(row.ndcg_at_10)
    kind = "Google" if row.is_google and not row.synthetic else "not Google"
    synthetic = " Synthetic mock data." if row.synthetic else ""
    return (
        f"<p>Label: {escape(row.label)}. Provider: {escape(row.provider)}. "
        f"Source is {kind}.{synthetic} "
        f"Queries used: {row.n_queries}. Pairs: {row.n_pairs}. "
        f"Mean Spearman: {spearman}. Mean NDCG at 10: {ndcg}. "
        f"Authority feature used: {'yes' if row.authority_used else 'no'}.</p>"
    )


def _test_block(database_url: str | None) -> str:
    row = _load(database_url, latest_test)
    if row is None:
        return "<p>No page test has been saved. Run <code>python -m aeo.ranksim test</code> after a corpus exists.</p>"
    summary = row.summary_json or {}
    synthetic = " The corpus is synthetic." if row.synthetic_corpus else ""
    google = "Google SERP" if row.is_google else "a non-Google or unlabeled source"
    lines = [
        f"<p>Source: {escape(row.source_label)}. Weights: {escape(row.weight_label)}. "
        f"Compared with {google}.{synthetic} "
        f"Improved: {summary.get('improved', 0)}. Regressed: {summary.get('regressed', 0)}. "
        "Positions are predicted.</p>"
    ]
    body_rows = []
    for item in summary.get("rows") or []:
        gaps = "; ".join(item.get("competitor_gaps") or [])
        body_rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('query') or ''))}</td>"
            f"<td>{_fmt_pos(item.get('before_predicted_position'))}</td>"
            f"<td>{_fmt_pos(item.get('after_predicted_position'))}</td>"
            f"<td>{_fmt_pos(item.get('delta'))}</td>"
            f"<td>{escape(gaps)}</td>"
            "</tr>"
        )
    lines.append(
        "<table><thead><tr><th>Query</th><th>Before</th><th>After</th><th>Change</th><th>Competitor gap</th></tr></thead><tbody>"
        + "\n".join(body_rows)
        + "</tbody></table>"
    )
    return "\n".join(lines)


def _track_block(database_url: str | None) -> str:
    rows = _load(database_url, latest_checks) or []
    if not rows:
        return (
            "<p>No daily rank check is stored. "
            "<code>python -m aeo.ranksim track</code> records positions only from a SERP provider. "
            "It does not backfill missing days.</p>"
        )
    found = sum(1 for row in rows if row.our_position is not None)
    sample = rows[0]
    kind = "Google" if sample.is_google and not sample.synthetic else sample.provider
    synthetic = " Synthetic." if sample.synthetic else ""
    return (
        f"<p>Latest check {escape(sample.checked_on)} via {escape(kind)}.{synthetic} "
        f"SmartCrew appeared in {found} of {len(rows)} stored queries. "
        "A blank position means the domain was outside the fetched results, not that it ranks at the bottom.</p>"
    )


def _load(database_url: str | None, reader):
    if not database_url:
        return None
    try:
        session = open_session(database_url)
    except Exception:
        return None
    try:
        return reader(session)
    except Exception:
        return None
    finally:
        session.close()


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _fmt_pos(value) -> str:
    if value is None:
        return "n/a"
    return escape(str(value))


# -- the Studio report ----------------------------------------------------------------------------


def stored_site_pages(project, database: str, limit: int = 80) -> list[SitePage]:
    session = open_session(database)
    try:
        rows = session.query(RsPage).filter(RsPage.domain.in_([project.domain, "www." + project.domain.removeprefix("www.")])).limit(limit * 2).all()
        pages = []
        for row in rows:
            if not project.is_our_url(row.url):
                continue
            pages.append(page_facts(row.url, row.raw_html or "", row.status or "error", row.error or "", row.fetch_ms))
    finally:
        session.close()
    pages.sort(key=lambda page: (urlsplit(page.url).path.strip("/").count("/"), page.url))
    return pages[:limit]


def _pages_for(database: str, urls: list[str]) -> dict[str, SitePage]:
    from aeo.ranksim.project import loose_key

    if not urls:
        return {}
    session = open_session(database)
    try:
        wanted = set(urls) | {url.rstrip("/") for url in urls} | {url.rstrip("/") + "/" for url in urls}
        rows = session.query(RsPage).filter(RsPage.url.in_(sorted(wanted))).all()
        found = {}
        for row in rows:
            if row.status == "fetched" and (row.raw_html or "").strip():
                found[loose_key(row.url)] = page_facts(row.url, row.raw_html)
    finally:
        session.close()
    return {url: found[loose_key(url)] for url in urls if loose_key(url) in found}


def build_report(project, meta, database: str, *, ctx=None, google_ready: bool = False, ui=None) -> dict:
    """Rankings, competitors and issues for one project, from what is stored. Positions are Google's; predicted
    positions, when an engine is built, are the model's order among the stored candidates.

    Every plugin's extend_report(report, project=, meta=, database=, ctx=, ui=) runs last and adds keys under its
    own name. ui is the app state when the report is built for the API, else None.
    """
    from aeo.ranksim import plugins
    from aeo.ranksim.audit import audit
    from aeo.ranksim.project import latest_google

    google = latest_google(project)
    own_pages = stored_site_pages(project, database)
    readable = [page for page in own_pages if page.status == "fetched"]
    by_search = google.by_search() if google is not None else {}
    result_urls = [item.url for entry in (google.searches if google else []) for item in entry.results if 1 <= item.position <= 10]
    docs = _pages_for(database, result_urls)
    competitors = set(meta.competitors or [])

    rankings = []
    for search in project.searches:
        entry = by_search.get(search)
        results = sorted(entry.results, key=lambda item: item.position) if entry else []
        top = [item for item in results if 1 <= item.position <= 10]
        mine = next((item for item in top if project.is_our_url(item.url)), None)
        predicted = None
        if ctx is not None and mine is not None:
            try:
                from aeo.ranksim import workspace as ws

                view = ws.compare_search(ctx, search, meta)
                predicted = {"position": view.your_predicted, "of": view.scored}
            except Exception:  # noqa: BLE001 - the report never fails on one search
                predicted = None
        rankings.append(
            {
                "q": search,
                "position": mine.position if mine else None,
                "url": mine.url if mine else _best_page(search, readable),
                "predicted": predicted,
                "checked": bool(results),
                "top": [
                    {
                        "position": item.position,
                        "url": item.url,
                        "domain": (item.domain or domain_of(item.url)).removeprefix("www."),
                        "title": item.title or (docs.get(item.url).title if docs.get(item.url) else ""),
                        "you": project.is_our_url(item.url),
                        "competitor": (item.domain or domain_of(item.url)).removeprefix("www.") in competitors,
                    }
                    for item in top
                ],
            }
        )

    share = share_of_top10(google, project.domain)
    for row in share:
        row["competitor"] = row["domain"] in competitors
    issues = audit(project, own_pages, rankings, docs, database=database, meta=meta)
    ranked = [row for row in rankings if row["position"]]
    checked = [row for row in rankings if row["checked"]]
    report = {
        "site": project.site,
        "name": project.name,
        "pages_read": len(readable),
        "pages_failed": len(own_pages) - len(readable),
        "search_data": bool(checked),
        "google_ready": google_ready,
        "pulled": next((entry.pulled_label for entry in (google.searches if google else []) if entry.results), ""),
        "kpis": {
            "searches": len(project.searches),
            "checked": len(checked),
            "top10": len(ranked),
            "top3": sum(1 for row in ranked if row["position"] <= 3),
            "average": round(sum(row["position"] for row in ranked) / len(ranked), 1) if ranked else None,
            "issues": len(issues),
            "high": sum(1 for issue in issues if issue["severity"] == "high"),
        },
        "rankings": rankings,
        "share": share[:12],
        "competitors": list(meta.competitors or []),
        "issues": issues,
        "pages": [
            {"url": page.url, "path": page.path, "status": page.status, "title": page.title, "words": page.words, "issues": sum(1 for issue in issues if issue.get("page") == page.url)}
            for page in own_pages
        ],
    }
    plugins.call_all("extend_report", report, project=project, meta=meta, database=database, ctx=ctx, ui=ui)
    return report


def _tokens(text: str) -> set[str]:
    return {word for word in _clean(text).split() if len(word) > 2 and word not in {"and", "the", "for", "with", "your", "you"}}


def _best_page(search: str, pages: list[SitePage]) -> str | None:
    want = _tokens(search)
    best, score = None, 0.0
    for page in pages:
        have = _tokens(f"{page.title} {page.h1}")
        overlap = len(want & have) / len(want) if want else 0.0
        if overlap > score:
            best, score = page.url, overlap
    return best if score >= 0.6 and len(want) >= 2 else None
