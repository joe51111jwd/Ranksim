"""One HTML report. Empty sections stay empty instead of showing made-up ranks."""

from __future__ import annotations

from html import escape

from aeo.ranksim.keywords import Keyword, build_keywords, gap_summary, page_gaps
from aeo.ranksim.store import latest_checks, latest_test, latest_weights, open_session


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
        f"In-sample (fit and scored on the same queries, not held out): mean Spearman {spearman}, mean NDCG at 10 {ndcg}. "
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
        f"Improved: {escape(str(summary.get('improved', 'n/a')))}. Regressed: {escape(str(summary.get('regressed', 'n/a')))}. "
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
