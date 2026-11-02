"""One HTML page for the pre-publish rank check."""

from __future__ import annotations

from html import escape

from aeo.ranksim.check import CheckResult, LinkFix, SiteRow
from aeo.ranksim.linkgraph import LinkGraph


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
        "<p class=\"eyebrow\">SmartCrew demo <span class=\"pill\">PREDICTED</span></p>",
        "<h1>Pre-publish rank check</h1>",
        "<p class=\"lead\">Score a draft against the stored Google results for one query. Same-set: the crawled competitors, plus this draft only.</p>",
        f"<section class=\"banner\"><p>{escape(accuracy_line)}</p><p>{escape(calibration_note)}</p><p>{escape(llm_note)}</p><p>{escape(relevance_note)}</p></section>",
    ]
    if error:
        body.append(f"<p class=\"warn\">{escape(error)}</p>")
    if result is not None:
        body.append(_result_html(result))
    body.append("<form method=\"post\" action=\"/check\">")
    body.append("<label>Query<select name=\"query\">" + options + "</select></label>")
    body.append(f"<label>Draft URL<input name=\"url\" value=\"{escape(url_value)}\" placeholder=\"https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/\"></label>")
    body.append(f"<label>Pasted HTML<textarea name=\"html\" rows=\"8\">{escape(html_value)}</textarea></label>")
    body.append("<label class=\"check\"><input type=\"checkbox\" name=\"ai\" value=\"1\" checked> Run the AI layer (stub when no key is set)</label>")
    body.append("<div class=\"actions\"><button type=\"submit\">Score this draft</button> <a class=\"button\" href=\"/?demo=1\">Load SmartCrew sample</a></div>")
    body.append("</form>")
    body.append("<h2>Whole site</h2>")
    body.append("<p>Each stored SmartCrew page, scored on the stored query its title matches best. PREDICTED.</p>")
    body.append(_site_table(site_rows))
    body.append("<h2>Link map</h2>")
    body.append(_map_svg(graph))
    body.append("<h2>Internal link fixes</h2>")
    body.append("<p>Shown only when rescoring predicts a better rank. PREDICTED.</p>")
    body.append(_fixes(fixes))
    body.append("</main></body></html>")
    return "\n".join(body)


def _result_html(result: CheckResult) -> str:
    if result.error:
        return f"<section class=\"card\"><p class=\"warn\">{escape(result.error)}</p></section>"
    rank = "n/a" if result.predicted_rank is None else f"{result.predicted_rank} of {result.predicted_of}"
    added = ""
    if result.added_draft:
        added = "<p>This URL was not in the stored results. It was added as one draft beside the crawled competitors.</p>"
    rows = []
    for row in result.competitors:
        stored = "not in file" if row.stored_position is None else f"stored Google #{row.stored_position:g}"
        rows.append(
            "<li><strong>PREDICTED "
            + escape(row.outcome)
            + f"</strong> {escape(row.title)} "
            + f"<span class=\"muted\">predicted #{row.predicted_rank}, {escape(stored)}</span></li>"
        )
    gaps = "".join(
        f"<li><strong>{escape(gap.feature)}</strong> {escape(gap.detail)} {escape(gap.fix)}</li>" for gap in result.gaps
    ) or "<li>No scored feature on a page ahead is higher than this draft.</li>"
    missing = ""
    if result.missing_urls:
        missing = "<p class=\"muted\">Not scored, no stored HTML: " + escape(", ".join(result.missing_urls[:6])) + "</p>"
    rounds = "".join(
        f"<li>Round {item.round_index}: PREDICTED {item.predicted_rank} of {item.predicted_of}. {escape(item.note)}</li>"
        for item in result.fix_rounds
    )
    explain_label = "Template explanation" if result.explanation_stub else "LLM explanation"
    return f"""
<section class="card hero">
  <p class="pill">{escape(result.confidence)}</p>
  <p class="rank">PREDICTED {escape(rank)}</p>
  <p>{escape(result.query)}</p>
  {added}
  <p class="muted">{escape(result.confidence_note)}</p>
</section>
<section class="card">
  <h2>Beats and losses</h2>
  <ul>{''.join(rows)}</ul>
  {missing}
</section>
<section class="card">
  <h2>Top feature gaps</h2>
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
    body = ["<table><thead><tr><th>Page</th><th>Query</th><th>PREDICTED rank</th><th>Depth</th><th>Orphan</th></tr></thead><tbody>"]
    for row in rows[:40]:
        rank = "n/a" if row.predicted_rank is None else f"{row.predicted_rank} of {row.predicted_of}"
        orphan = "unknown" if row.orphan is None else ("yes" if row.orphan else "no")
        depth = "unknown" if row.depth is None else str(row.depth)
        body.append(
            f"<tr><td>{escape(row.url)}</td><td>{escape(row.query)}</td><td>{escape(rank)}</td><td>{depth}</td><td>{orphan}</td></tr>"
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
.rank { font-family: Georgia, serif; font-size: 42px; margin: 8px 0; }
label { display: block; margin: 10px 0; font-weight: 600; }
input, select, textarea { width: 100%; box-sizing: border-box; margin-top: 4px; padding: 8px; border: 1px solid #d9d0c2; border-radius: 8px; font: inherit; background: white; }
.check { font-weight: 500; }
button, .button { background: #8f3d1b; color: white; border: 0; border-radius: 8px; padding: 10px 14px; text-decoration: none; display: inline-block; }
table { width: 100%; border-collapse: collapse; background: #fffdf8; }
th, td { text-align: left; border-bottom: 1px solid #eadfce; padding: 8px; vertical-align: top; font-size: 14px; }
.map { width: 100%; background: #fffdf8; border: 1px solid #e2d8c8; border-radius: 12px; }
.map line { stroke: #d7c4ae; stroke-width: 1; }
.map text { font-size: 11px; fill: #1f1a14; }
.warn { color: #8f2d2d; }
code { font-size: 13px; }
"""
