"""One HTML page for the pre-publish rank check."""

from __future__ import annotations

from html import escape

from aeo.ranksim.check import CheckResult, LinkFix, SiteRow
from aeo.ranksim.linkgraph import LinkGraph
from aeo.ranksim.space import SpaceReport


def render_page(
    *,
    queries: list[str],
    selected: str,
    url_value: str,
    html_value: str,
    result: CheckResult | None,
    accuracy_line: str,
    calibration_note: str,
    llm_note: str,
    relevance_note: str,
    site_rows: list[SiteRow],
    graph: LinkGraph | None,
    fixes: list[LinkFix],
    error: str = "",
    freshness_note: str = "",
    csrf: str = "",
) -> str:
    options = "\n".join(
        f'<option value="{escape(query)}"{" selected" if query == selected else ""}>{escape(query)}</option>'
        for query in queries
    )
    body = [
        "<!doctype html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Pre-publish rank check</title>",
        f"<style>{CSS}</style></head><body>",
        "<main>",
        "<p class=\"eyebrow\">SmartCrew demo <span class=\"pill\">PREDICTED</span> <a href=\"/space\">Space view</a></p>",
        "<h1>Pre-publish rank check</h1>",
        "<p class=\"lead\">Score a draft against the stored Google results for one query. Same-set: the crawled competitors, plus this draft only. Results are a slot among those supplied pages, not a Google position.</p>",
    ]
    if error:
        body.append(f"<p class=\"warn\" role=\"alert\">{escape(error)}</p>")
    if result is not None:
        body.append(_result_html(result, freshness_note))
    body.append(
        f"<section class=\"banner\"><p>{escape(freshness_note)}</p><p>{escape(accuracy_line)}</p><p>{escape(calibration_note)}</p>"
        f"<p>{escape(llm_note)}</p><p>{escape(relevance_note)}</p></section>"
    )
    body.append("<form method=\"post\" action=\"/check#result\">")
    body.append(f"<input type=\"hidden\" name=\"csrf\" value=\"{escape(csrf)}\">")
    body.append("<label>Query<select name=\"query\">" + options + "</select></label>")
    body.append(f"<label>Draft URL<input name=\"url\" value=\"{escape(url_value)}\" placeholder=\"https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/\"></label>")
    body.append(f"<label>Pasted HTML<textarea name=\"html\" rows=\"8\">{escape(html_value)}</textarea></label>")
    body.append("<label class=\"check\"><input type=\"checkbox\" name=\"ai\" value=\"1\"> Run the AI layer (paid when a key is set; stub otherwise)</label>")
    body.append("<div class=\"actions\"><button type=\"submit\">Score this draft</button> <a class=\"button\" href=\"/?demo=1\">Load SmartCrew sample</a></div>")
    body.append("</form>")
    body.append("<h2>Whole site</h2>")
    body.append("<p>Each stored SmartCrew page, scored on the stored query its title matches best. PREDICTED slot among the stored pages for that query, not a Google position.</p>")
    body.append(_site_table(site_rows))
    body.append("<h2>Link map</h2>")
    body.append(_map_svg(graph))
    body.append("<h2>Internal link fixes</h2>")
    body.append("<p>Shown only when rescoring predicts a better slot. That is a simulator move from adding anchor text, not evidence the link would change Google rankings.</p>")
    body.append(_fixes(fixes))
    body.append("</main></body></html>")
    return "\n".join(body)


def render_space_page(report: SpaceReport, llm_note: str) -> str:
    share_rows = []
    for row in report.shares:
        pred3 = "PREDICTED n/a" if row.predicted_queries == 0 else f"PREDICTED {row.predicted_top3} of {row.predicted_queries}"
        pred10 = "PREDICTED n/a" if row.predicted_queries == 0 else f"PREDICTED {row.predicted_top10} of {row.predicted_queries}"
        share_rows.append(
            "<tr>"
            f"<td>{escape(row.domain)}</td>"
            f"<td>REAL {row.real_top3} of {row.tracked_queries}</td>"
            f"<td>REAL {row.real_top10} of {row.tracked_queries}</td>"
            f"<td>{escape(pred3)}</td>"
            f"<td>{escape(pred10)}</td>"
            "</tr>"
        )
    h2h_rows = []
    for row in report.headtohead:
        if row.real_queries == 0 and row.predicted_queries == 0:
            continue
        real = "REAL n/a" if row.real_queries == 0 else f"REAL {row.real_wins}-{row.real_losses}-{row.real_ties} over {row.real_queries}"
        pred = "PREDICTED n/a" if row.predicted_queries == 0 else f"PREDICTED {row.predicted_wins}-{row.predicted_losses}-{row.predicted_ties} over {row.predicted_queries}"
        h2h_rows.append(f"<tr><td>{escape(row.domain)}</td><td>{escape(real)}</td><td>{escape(pred)}</td></tr>")
    boards = []
    for board in report.boards:
        lines = [f"<h3>{escape(board.query)}</h3><p class=\"muted\">{board.scored_pages} scored pages</p><ul>"]
        for player in board.players:
            real = "REAL n/a" if player.real_position is None else f"REAL #{player.real_position:g}"
            if player.predicted_rank is None:
                pred = "PREDICTED n/a"
            else:
                pred = f"PREDICTED {player.predicted_rank} of {player.predicted_of}"
            lines.append(f"<li><strong>{escape(player.domain)}</strong> {escape(real)} / {escape(pred)}</li>")
        lines.append("</ul>")
        boards.append("".join(lines))
    fixes = "".join(
        f"<li>PREDICTED {fix.predicted_rank} of {fix.predicted_of} on {escape(fix.query)} {escape(fix.url)}</li>"
        for fix in report.fixes
    ) or "<li>No client page had a predicted rank.</li>"
    notes = "".join(f"<p class=\"muted\">{escape(note)}</p>" for note in report.notes)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How you stack up</title>
<style>{CSS}</style></head><body><main>
<p class="eyebrow">SmartCrew demo <span class="pill">PREDICTED</span> <a href="/">Rank check</a></p>
<h1>How you stack up</h1>
<p class="lead">Stored Google results are REAL. Model ranks are PREDICTED, and only for pages we stored.</p>
<section class="banner"><p>{escape(llm_note)}</p></section>
<h2>Share of tracked queries</h2>
<table><thead><tr><th>Domain</th><th>REAL top 3</th><th>REAL top 10</th><th>PREDICTED top 3</th><th>PREDICTED top 10</th></tr></thead>
<tbody>{''.join(share_rows)}</tbody></table>
<h2>Head to head</h2>
<p class="muted">Record is wins-losses-ties. REAL only when both domains are in the stored file. PREDICTED only when both have stored HTML.</p>
<table><thead><tr><th>Competitor</th><th>REAL</th><th>PREDICTED</th></tr></thead><tbody>{''.join(h2h_rows)}</tbody></table>
<h2>Gaps</h2>
<ul>
<li>{escape(report.content_gap)}</li>
<li>{escape(report.link_gap)}</li>
<li>{escape(report.authority_gap)}</li>
</ul>
<h2>Pages to fix first</h2>
<ul>{fixes}</ul>
<h2>Every player, every query</h2>
{''.join(boards)}
{notes}
</main></body></html>
"""


def _result_html(result: CheckResult, freshness_note: str = "") -> str:
    if result.error:
        return f"<section class=\"card\"><p class=\"warn\">{escape(result.error)}</p></section>"
    rank = "n/a" if result.predicted_rank is None else f"{result.predicted_rank} of {result.predicted_of}"
    added = ""
    if result.added_draft:
        added = "<p>This URL was not in the stored results. It was added as one draft beside the crawled competitors.</p>"
    scored = len(result.competitors)
    missing_count = len(result.missing_urls)
    coverage = (
        f"Compared with {scored} of {scored + missing_count} competitors in the stored Google results for this query"
        + (f"; {missing_count} had no stored HTML and were not scored." if missing_count else ".")
    )
    unsure = result.confidence != "CLEARER"
    verdict = "UNSURE: no supported call" if unsure else "CLEARER call (still PREDICTED)"
    rows = []
    for row in result.competitors:
        stored = "not in file" if row.stored_position is None else f"stored Google #{row.stored_position:g}"
        side = "ahead of" if row.outcome == "beats" else "behind"
        rows.append(
            f"<li><strong>PREDICTED {escape(side)}</strong> {escape(row.title)} "
            f"<span class=\"pill\">{escape(row.confidence)}</span> "
            f"<span class=\"muted\">slot {row.predicted_rank}, {escape(stored)}</span></li>"
        )
    gaps = "".join(
        f"<li><strong>{escape(gap.feature)}</strong> {escape(gap.detail)} {escape(gap.fix)}</li>" for gap in result.gaps
    ) or "<li>No scored feature on a page ahead is higher than this draft.</li>"
    missing = ""
    if result.missing_urls:
        shown = ", ".join(result.missing_urls[:6])
        more = f" and {missing_count - 6} more" if missing_count > 6 else ""
        missing = f"<p class=\"muted\">Not scored, no stored HTML ({missing_count}): " + escape(shown + more) + "</p>"
    rounds = "".join(
        f"<li>Round {item.round_index}: PREDICTED slot {item.predicted_rank} of {item.predicted_of}. {escape(item.note)}</li>"
        for item in result.fix_rounds
    )
    explain_label = "Template explanation" if result.explanation_stub else "LLM explanation"
    return f"""
<section class="card hero" id="result">
  <p class="verdict{' unsure' if unsure else ''}">{escape(verdict)}</p>
  <p class="rank">PREDICTED slot {escape(rank)} supplied pages</p>
  <p>{escape(result.query)}</p>
  <p class="coverage">{escape(coverage)} The slot is among those pages only; it is not a Google position.</p>
  <p class="muted">{escape(freshness_note)}</p>
  {added}
  <p class="muted">{escape(result.confidence_note)}</p>
</section>
<section class="card">
  <h2>Ahead of and behind</h2>
  <ul>{''.join(rows)}</ul>
  {missing}
</section>
<section class="card">
  <h2>Top feature gaps</h2>
  <p class="muted">What the simulator scores higher on pages it puts ahead. Changing these moves the simulator; that is not evidence it would move Google.</p>
  <ol>{gaps}</ol>
</section>
<section class="card">
  <h2>{escape(explain_label)}</h2>
  <p>{escape(result.explanation)}</p>
  <h2>AI fix loop</h2>
  <p>{escape(result.fix_note)}</p>
  <ol>{rounds}</ol>
</section>
"""


def _site_table(rows: list[SiteRow]) -> str:
    if not rows:
        return "<p>No SmartCrew pages are stored yet.</p>"
    body = ["<table><thead><tr><th>Page</th><th>Query</th><th>PREDICTED slot</th><th>Depth</th><th>Orphan</th></tr></thead><tbody>"]
    for row in rows[:40]:
        rank = "n/a" if row.predicted_rank is None else f"{row.predicted_rank} of {row.predicted_of}"
        orphan = "unknown" if row.orphan is None else ("yes" if row.orphan else "no")
        depth = "unknown" if row.depth is None else str(row.depth)
        draft = " <span class=\"pill\">DRAFT HTML, not the live page</span>" if row.source == "draft_bundle" else ""
        body.append(
            f"<tr><td>{escape(row.url)}{draft}</td><td>{escape(row.query)}</td><td>{escape(rank)}</td><td>{depth}</td><td>{orphan}</td></tr>"
        )
    body.append("</tbody></table>")
    return "\n".join(body)


def _fixes(fixes: list[LinkFix]) -> str:
    if not fixes:
        return "<p>No internal link change improved a predicted rank on the stored queries.</p>"
    items = []
    for fix in fixes:
        items.append(
            "<li>Link "
            f"<code>{escape(fix.source)}</code> to <code>{escape(fix.target)}</code> "
            f"with anchor <strong>{escape(fix.anchor)}</strong>. "
            f"PREDICTED {fix.before_rank} to {fix.after_rank} of {fix.of} on {escape(fix.query)}.</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _map_svg(graph: LinkGraph | None) -> str:
    if graph is None or not graph.nodes:
        return "<p>No link graph yet. Run <code>python -m aeo.ranksim graph</code>.</p>"
    ours = [node for node in graph.nodes.values() if "smartcrewhq.com" in node.url]
    ours.sort(key=lambda node: (node.depth, node.url))
    columns: dict[int, list] = {}
    for node in ours[:28]:
        columns.setdefault(node.depth, []).append(node)
    pos = {}
    for depth, group in columns.items():
        for index, node in enumerate(group):
            pos[node.url] = (36 + depth * 210, 28 + index * 32)
    width = 40 + (max(columns) + 1) * 210 if columns else 400
    height = 40 + max((len(group) for group in columns.values()), default=1) * 32
    parts = [f'<svg viewBox="0 0 {width} {height}" class="map" role="img" aria-label="SmartCrew link map">']
    for src, dst, _anchor in graph.edges:
        if src in pos and dst in pos:
            x1, y1 = pos[src]
            x2, y2 = pos[dst]
            parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" />')
    for url, (x, y) in pos.items():
        node = graph.nodes[url]
        color = "#8f2d2d" if node.orphan else "#8f3d1b"
        label = url.replace("https://www.smartcrewhq.com", "") or "/"
        parts.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{color}"><title>{escape(label)} depth {node.depth}</title></circle>')
        parts.append(f'<text x="{x + 10}" y="{y + 4}">{escape(label[:32])}</text>')
    parts.append("</svg>")
    note = f"<p class=\"muted\">{len(graph.nodes)} pages in the crawl, {len(ours)} on SmartCrew, {graph.blocked} robots blocks, {graph.fetches} fetches.</p>"
    return note + "".join(parts)


CSS = """
:root { color-scheme: light; }
body { margin: 0; background: #efe8dc; color: #1f1a14; font: 16px/1.5 "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-family: Georgia, serif; font-size: 40px; line-height: 1.1; margin: 0 0 8px; }
h2 { font-family: Georgia, serif; font-size: 26px; margin: 28px 0 8px; }
.lead, .muted { color: #5e564c; }
.eyebrow { letter-spacing: 0.04em; text-transform: uppercase; font-size: 12px; }
.pill { display: inline-block; border: 1px solid #1f1a14; border-radius: 999px; padding: 2px 8px; font-size: 12px; }
.banner, .card, form { background: #fffdf8; border: 1px solid #e2d8c8; border-radius: 12px; padding: 16px 18px; margin: 16px 0; }
.rank { font-family: Georgia, serif; font-size: 26px; margin: 4px 0; }
.verdict { font-family: Georgia, serif; font-size: 34px; margin: 4px 0; }
.verdict.unsure { color: #8f2d2d; }
.coverage { font-weight: 600; }
label { display: block; margin: 10px 0; font-weight: 600; }
input, select, textarea { width: 100%; box-sizing: border-box; margin-top: 4px; padding: 8px; border: 1px solid #d9d0c2; border-radius: 8px; font: inherit; background: white; }
.check { font-weight: 500; }
button, .button { background: #8f3d1b; color: white; border: 0; border-radius: 8px; padding: 10px 14px; text-decoration: none; display: inline-block; }
table { width: 100%; border-collapse: collapse; background: #fffdf8; display: block; overflow-x: auto; }
th, td { text-align: left; border-bottom: 1px solid #eadfce; padding: 8px; vertical-align: top; font-size: 14px; }
.map { width: 100%; background: #fffdf8; border: 1px solid #e2d8c8; border-radius: 12px; }
.map line { stroke: #d7c4ae; stroke-width: 1; }
.map text { font-size: 11px; fill: #1f1a14; }
.warn { color: #8f2d2d; }
code { font-size: 13px; }
"""
