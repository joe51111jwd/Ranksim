"""The three-screen web workspace: Setup, Comparisons, Draft test. Server-rendered HTML.

Routes (registered by serve.create_app)
  GET  /                                  projects, or an empty state with the demo buttons
  GET  /go?p=SLUG                         project switcher target
  POST /demo/synthetic                    build the synthetic demo (no keys, no network)
  POST /demo/historical                   build the SmartCrew historical demo (loopback only)
  GET  /p/{slug}                          1. Setup: data source, coverage by query, competitors, import
  POST /p/{slug}/competitors              save competitor domains
  GET  /p/{slug}/comparisons?q=&sel=      2. Comparisons: observed order next to PREDICTED order for one query
  GET  /p/{slug}/draft?q=&from=           3. Draft test form, and recent runs
  POST /p/{slug}/draft                    run a draft test and save it
  GET  /p/{slug}/draft/{run_id}           a saved draft test result

Every model output is labeled PREDICTED and scoped to the candidates shown.
Synthetic data is labeled SYNTHETIC on every page of a synthetic project.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import workspace as ws

NONE_SMALL = '<span class="muted small">none</span>'
CURRENT = ' aria-current="true"'
TABS = (("setup", "Setup", ""), ("comparisons", "Comparisons", "/comparisons"), ("draft", "Draft test", "/draft"))
MAX_DRAFT_BYTES = 2 * 1024 * 1024


def _s():
    from aeo.ranksim import serve

    return serve


def e(value) -> str:
    return _s().e(value)


def _q(params: dict) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    return ("?" + urlencode(clean)) if clean else ""


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


def dataset_badge(meta: ws.WorkspaceMeta | None) -> str:
    if meta is None or meta.dataset == ws.DATASET_NONE:
        return ""
    if meta.dataset == ws.DATASET_SYNTHETIC:
        return '<span class="badge badge-synth">SYNTHETIC data</span>'
    if meta.dataset == ws.DATASET_HISTORICAL:
        return '<span class="badge badge-hist">Historical export</span>'
    return '<span class="badge badge-obs">Observed data</span>'


def data_strip(meta: ws.WorkspaceMeta | None) -> str:
    if meta is None:
        return ""
    if meta.dataset == ws.DATASET_SYNTHETIC:
        return (
            '<div class="strip strip-synth" role="note"><div class="page">'
            "<b>SYNTHETIC example data.</b> <span class=\"hide-sm\">The sites (.example domains), positions and pages were invented for this demo. "
            "They are not Google results and say nothing about real rankings.</span><span class=\"show-sm\">Invented sites and positions. Not Google results.</span></div></div>"
        )
    if meta.dataset == ws.DATASET_HISTORICAL:
        return (
            '<div class="strip strip-hist" role="note"><div class="page">'
            "<b>Historical observed export.</b> SmartCrew positions from smartcrew/serp.csv. Pull date not recorded. "
            "Not a live Google result. Local use only.</div></div>"
        )
    return ""


def shell(
    ui,
    title: str,
    body: str,
    *,
    project=None,
    meta: ws.WorkspaceMeta | None = None,
    tab: str | None = None,
    query: str = "",
    crumbs: list[tuple[str, str | None]] | None = None,
    status: int = 200,
    wide: bool = True,
) -> HTMLResponse:
    from aeo.ranksim.project import list_projects

    try:
        projects = list_projects(root=ui.root)
    except Exception:  # noqa: BLE001 - a broken project folder must not take every page down
        projects = []
    current = project.slug if project is not None else ""
    switcher = ""
    if projects:
        options = "".join(
            f'<option value="{e(row.slug)}"{" selected" if row.slug == current else ""}>{e(row.name)}</option>' for row in projects
        )
        if not current:
            options = '<option value="" selected>Choose a project</option>' + options
        switcher = (
            '<form class="switcher" method="get" action="/go" data-autosubmit>'
            '<label class="sr-only" for="project-switch">Project</label>'
            f'<select id="project-switch" name="p">{options}</select>'
            '<button type="submit" class="btn btn-sm js-hide">Open</button></form>'
        )
    tabs = ""
    if project is not None:
        links = []
        for number, (key, label, suffix) in enumerate(TABS, start=1):
            href = f"/p/{e(project.slug)}{suffix}" + (e(_q({"q": query})) if query else "")
            here = ' aria-current="page"' if key == tab else ""
            links.append(f'<a class="tab" href="{href}"{here}><span class="tab-n" aria-hidden="true">{number}</span>{label}</a>')
        tabs = '<nav class="tabs" aria-label="Screens">' + "".join(links) + "</nav>"
    context = ""
    if project is not None:
        parts = [f'<span class="hide-sm"><span class="ctx-k">Project</span> {e(project.name)}</span>', f'<span><span class="ctx-k">Site</span> {e(project.site)}</span>']
        if query:
            parts.append(f'<span class="ctx-q"><span class="ctx-k">Query</span> {e(query)}</span>')
        context = f'<div class="context"><div class="page ctx-row">{"".join(parts)}{dataset_badge(meta)}</div></div>'
    elif crumbs:
        trail = ['<a href="/">Projects</a>']
        for label, href in crumbs:
            trail.append(f'<a href="{e(href)}">{e(label)}</a>' if href else f"<span>{e(label)}</span>")
        context = f'<div class="context"><nav class="page ctx-row crumbs" aria-label="Breadcrumb">{" / ".join(trail)}</nav></div>'
    footer = (
        '<footer class="foot"><div class="page">PREDICTED means the model\'s order of the candidates shown, never a Google rank. '
        "Observed means positions from the file or provider named on the page, with its date when one was recorded.</div></footer>"
    )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f"<title>{e(title)} | Ranksim</title>"
        '<link rel="stylesheet" href="/static/app.css"><script src="/static/app.js" defer></script></head><body>'
        '<a class="skip" href="#main">Skip to content</a>'
        '<header class="appbar"><div class="page appbar-row">'
        '<a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span>Ranksim</a>'
        f"{switcher}{tabs}</div></header>"
        f"{context}{data_strip(meta)}"
        f'<main id="main" class="page{"" if wide else " narrow"}" tabindex="-1">{body}</main>{footer}'
        "</body></html>"
    )
    return HTMLResponse(page, status_code=status)


def _alert(text: str, kind: str = "info", *, role: str = "status") -> str:
    return f'<div class="alert alert-{kind}" role="{role}">{text}</div>'


def _pred(text: str = "PREDICTED") -> str:
    return f'<span class="tag tag-pred">{e(text)}</span>'


def _obs(meta: ws.WorkspaceMeta | None, text: str = "") -> str:
    if meta is not None and meta.synthetic:
        return f'<span class="tag tag-synth">{e(text or "SYNTHETIC")}</span>'
    return f'<span class="tag tag-obs">{e(text or "OBSERVED")}</span>'


def _role_chip(role: str) -> str:
    return {
        "you": '<span class="chip chip-you">You</span>',
        "competitor": '<span class="chip chip-comp">Competitor</span>',
    }.get(role, "")


def _confidence_chip(label: str) -> str:
    if not label or label == "n/a":
        return '<span class="chip chip-na">n/a</span>'
    css = "chip-unsure" if label == "UNSURE" else "chip-sure"
    return f'<span class="chip {css}">{e(label)}</span>'


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------


def home_body(ui, *, error: str = "", values=None) -> str:
    from aeo.ranksim.project import list_projects

    values = values or {}
    rows = list_projects(root=ui.root)
    parts = ['<div class="page-head"><div><h1>Projects</h1>'
             '<p class="sub">Compare your pages with competitors on the queries you care about, then test a draft against the same pages.</p></div></div>']
    if error:
        parts.append(_alert(e(error), "bad", role="alert"))
    demo = _demo_panel(ui, empty=not rows)
    if rows:
        body = []
        for row in rows:
            try:
                meta = ws.load_meta(ui.project(row.slug))
            except Exception:  # noqa: BLE001
                meta = None
            data = row.latest_google or '<span class="muted">no data yet</span>'
            body.append(
                "<tr>"
                f'<td><a class="strong" href="/p/{e(row.slug)}">{e(row.name)}</a><div class="muted small">{e(row.site)}</div></td>'
                f"<td>{dataset_badge(meta) or NONE_SMALL}</td>"
                f'<td class="num">{row.searches}</td>'
                f'<td class="small hide-sm">{data if not row.latest_google else e(data)}</td>'
                f'<td class="actions"><a href="/p/{e(row.slug)}">Setup</a><a href="/p/{e(row.slug)}/comparisons">Compare</a><a href="/p/{e(row.slug)}/draft">Draft test</a></td>'
                "</tr>"
            )
        parts.append(
            '<section class="panel"><div class="panel-head"><h2>Your projects</h2></div>'
            '<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Project</th><th scope="col">Data</th>'
            '<th scope="col" class="num">Queries</th><th scope="col" class="hide-sm">Latest positions</th><th scope="col"><span class="sr-only">Open</span></th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div></section>'
        )
    layout = ['<div class="grid-2 grid-top">', demo, _create_form(ui, values), "</div>"]
    parts.append("".join(layout))
    return "".join(parts)


def _demo_panel(ui, *, empty: bool) -> str:
    csrf = _s()._csrf_input(ui)
    lead = (
        "<p>No projects yet. The fastest way to see the whole flow is the synthetic demo: six queries, invented .example sites, no API keys and no network.</p>"
        if empty
        else "<p>Rebuild or open the demo project. It needs no API keys and makes no network calls.</p>"
    )
    historical = ""
    if historical_allowed(ui):
        historical = (
            '<form method="post" action="/demo/historical" data-busy="Loading the SmartCrew export...">'
            + csrf
            + '<button type="submit" class="btn">Load historical SmartCrew export</button></form>'
            '<p class="small muted">Local only. Positions from smartcrew/serp.csv (pull date not recorded) and the HTML saved in this repository. '
            "Pages without saved HTML stay missing; nothing is fetched.</p>"
        )
    return (
        f'<section class="panel{" panel-accent" if empty else ""}"><div class="panel-head"><h2>{"Start with the demo" if empty else "Demo data"}</h2></div>'
        f'<div class="panel-body">{lead}'
        '<form method="post" action="/demo/synthetic" data-busy="Building the demo...">'
        + csrf
        + f'<button type="submit" class="btn {"btn-primary" if empty else ""}">Load synthetic demo</button></form>'
        '<p class="small muted">Every page of the demo project is marked SYNTHETIC. It is never presented as Google results.</p>'
        f"{historical}</div></section>"
    )


def _create_form(ui, values) -> str:
    if ui.public_demo:
        return (
            '<section class="panel"><div class="panel-head"><h2>Your own data</h2></div><div class="panel-body">'
            "<p>This public demo is read-only apart from draft tests. Run Ranksim on your own computer to create projects and import your own positions:</p>"
            '<pre class="code">pip install -e .\npython -m aeo.ranksim serve</pre></div></section>'
        )
    get = (lambda key: str(values.get(key, "") or "")) if values else (lambda key: "")
    return (
        '<section class="panel"><div class="panel-head"><h2>New project</h2></div><div class="panel-body">'
        '<form method="post" action="/projects" enctype="multipart/form-data" class="form" data-busy="Creating...">'
        + _s()._csrf_input(ui)
        + f'<div class="field"><label for="np-name">Name</label><input id="np-name" name="name" required maxlength="80" value="{e(get("name"))}" autocomplete="off"></div>'
        + f'<div class="field"><label for="np-site">Your site</label><input id="np-site" name="site" required maxlength="200" value="{e(get("site"))}" placeholder="www.yoursite.com" inputmode="url" aria-describedby="np-site-h"><p class="hint" id="np-site-h">Domain or URL. Pages on this domain are marked You.</p></div>'
        + f'<div class="field"><label for="np-comp">Competitors <span class="opt">optional</span></label><textarea id="np-comp" name="competitors" rows="3" placeholder="competitor-one.com&#10;https://www.competitor-two.com/" aria-describedby="np-comp-h">{e(get("competitors"))}</textarea><p class="hint" id="np-comp-h">One domain or URL per line. Used to label and filter candidates.</p></div>'
        + f'<div class="field"><label for="np-q">Queries <span class="opt">optional with a CSV</span></label><textarea id="np-q" name="searches" rows="3" placeholder="one query per line" aria-describedby="np-q-h">{e(get("searches"))}</textarea><p class="hint" id="np-q-h">Leave empty to take the queries from the CSV below.</p></div>'
        + '<div class="field"><label for="np-csv">Positions CSV <span class="opt">optional</span></label><input id="np-csv" type="file" name="csv_file" accept=".csv,text/csv" aria-describedby="np-csv-h"><p class="hint" id="np-csv-h">Columns query,url,position. Checked line by line before anything is saved.</p></div>'
        + '<div class="form-actions"><button type="submit" class="btn btn-primary">Create project</button></div></form></div></section>'
    )


def historical_allowed(ui) -> bool:
    from pathlib import Path

    return not ui.public_demo and Path("smartcrew/serp.csv").is_file()


# ---------------------------------------------------------------------------
# 1. Setup
# ---------------------------------------------------------------------------


def setup_body(ui, project, meta, *, running=None, message: str = "", error: str = "", csv_check: ws.CsvCheck | None = None, competitor_problems=None, query: str = "") -> str:
    from aeo.ranksim.project import latest_google, list_pulls

    serve = _s()
    csrf = serve._csrf_input(ui)
    google = latest_google(project)
    cov = ws.coverage(project, ui.database, meta)
    pulls = [item for item in list_pulls(project) if item.finished]
    engine_ready = any(item.has_engine for item in pulls)
    parts = [
        '<div class="page-head"><div><h1>Setup</h1>'
        "<p class=\"sub\">Which observed results the comparisons use, which domains are your competitors, and how much page HTML is stored.</p></div>"
        + _setup_cta(project, engine_ready, google, query)
        + "</div>"
    ]
    if message:
        parts.append(_alert(e(message), "good"))
    if error:
        parts.append(_alert(e(error), "bad", role="alert"))
    if csv_check is not None and not csv_check.ok:
        items = "".join(f"<li>{e(item)}</li>" for item in csv_check.errors)
        more = f"<li>{csv_check.more_errors} more problems not shown.</li>" if csv_check.more_errors else ""
        parts.append(
            '<div class="alert alert-bad" role="alert"><b>The CSV was not imported.</b> Fix these lines and upload it again:'
            f'<ul class="tight">{items}{more}</ul></div>'
        )
    if running is not None:
        parts.append(serve._job_panel(ui, running, redirect=False))
    main = [_source_panel(ui, project, meta, google, pulls, engine_ready, csrf), _coverage_panel(project, meta, cov, engine_ready)]
    side = [_competitor_panel(ui, project, meta, cov, csrf, competitor_problems), _import_panel(ui, project, meta, csrf), _keys_panel(ui)]
    parts.append(f'<div class="split"><div class="split-main">{"".join(main)}</div><aside class="split-side">{"".join(side)}</aside></div>')
    return "".join(parts)


def _setup_cta(project, engine_ready: bool, google, query: str) -> str:
    if not engine_ready or google is None:
        return ""
    href = f"/p/{e(project.slug)}/comparisons" + (e(_q({"q": query})) if query else "")
    return f'<div class="head-actions"><a class="btn btn-primary" href="{href}">Open comparisons</a></div>'


def _source_panel(ui, project, meta, google, pulls, engine_ready: bool, csrf: str) -> str:
    from aeo.ranksim.project import load_pull_report

    if google is None:
        empty = (
            "<p><b>No positions yet.</b> Import a CSV of query,url,position rows (for example a rank tracker or Search Console export), "
            "or load the synthetic demo from the Projects page.</p>"
        )
        if serve_google_ready(ui):
            empty += "<p>A Google results key is set on this server, so <b>Fetch pages and build</b> can also ask Google directly.</p>"
        return f'<section class="panel"><div class="panel-head"><h2>Data source</h2></div><div class="panel-body">{empty}{_build_form(ui, project, meta, csrf, engine_ready=False, has_google=False)}</div></section>'
    sources = sorted({item.source for item in google.searches if item.source})
    dates = sorted({item.pulled_label for item in google.searches if item.results})
    report = None
    for info in reversed(pulls):
        report = load_pull_report(project, info.pull_id)
        if report is not None and report.urls:
            break
    counts = report.counts if report else {}
    last_build = pulls[-1].written_at[:16].replace("T", " ") + " UTC" if pulls else "never"
    kind_label = {
        ws.DATASET_SYNTHETIC: "SYNTHETIC example positions",
        ws.DATASET_HISTORICAL: "Historical observed positions",
    }.get(meta.dataset, "Observed positions")
    rows = [
        ("Positions", f"{_obs(meta, 'SYNTHETIC' if meta.synthetic else 'OBSERVED')} {e(kind_label)}"),
        ("Source", "<br>".join(e(item) for item in sources) or "n/a"),
        ("Positions seen", "n/a: invented for this demo" if meta.synthetic else (e(", ".join(dates)) if dates else "n/a")),
        ("Queries with positions", f"{sum(1 for item in google.searches if item.results)} of {len(project.searches)}"),
        ("Last build", e(last_build) + (" (engine ready)" if engine_ready else " (no engine yet)")),
    ]
    if counts:
        stored = counts.get("stored", 0) + counts.get("fetched_now", 0) + counts.get("kept_stored", 0)
        failed = sum(value for key, value in counts.items() if key not in {"stored", "fetched_now", "kept_stored"})
        rows.append(("Page HTML", f"{stored} stored, {failed} could not be fetched" + _fail_detail(counts)))
    dl = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in rows)
    return (
        '<section class="panel"><div class="panel-head"><h2>Data source</h2></div><div class="panel-body">'
        f'<dl class="facts">{dl}</dl>{_build_form(ui, project, meta, csrf, engine_ready=engine_ready, has_google=True)}</div></section>'
    )


def serve_google_ready(ui) -> bool:
    return _s()._google_ready(ui.settings)


def _fail_detail(counts: dict) -> str:
    names = {"blocked_robots": "blocked by robots.txt", "http_error": "HTTP errors", "error": "other errors", "unsafe": "refused addresses"}
    parts = [f"{value} {names[key]}" for key, value in counts.items() if key in names and value]
    return f" ({', '.join(parts)})" if parts else ""


def _build_form(ui, project, meta, csrf: str, *, engine_ready: bool, has_google: bool) -> str:
    if meta.dataset == ws.DATASET_SYNTHETIC:
        return (
            '<form method="post" action="/demo/synthetic" class="inline-form" data-busy="Rebuilding...">' + csrf
            + '<button type="submit" class="btn">Rebuild demo</button>'
            '<span class="hint">Re-imports the synthetic positions and pages. Your draft tests are kept.</span></form>'
        )
    if meta.dataset == ws.DATASET_HISTORICAL:
        if not historical_allowed(ui):
            return ""
        return (
            '<form method="post" action="/demo/historical" class="inline-form" data-busy="Rebuilding...">' + csrf
            + '<button type="submit" class="btn">Rebuild from saved files</button>'
            '<span class="hint">Uses only the HTML saved in this repository. Nothing is fetched.</span></form>'
        )
    if ui.public_demo:
        return ""
    if not has_google and not serve_google_ready(ui):
        return ""
    label = "Fetch pages and rebuild" if engine_ready else "Fetch pages and build"
    return (
        f'<form method="post" action="/p/{e(project.slug)}/pull" class="inline-form" data-busy="Starting...">' + csrf
        + f'<button type="submit" class="btn btn-primary">{label}</button>'
        '<span class="hint">Fetches each candidate page (robots.txt respected) and trains the model. Progress shows here.</span></form>'
    )


def _coverage_panel(project, meta, cov: list[ws.QueryCoverage], engine_ready: bool) -> str:
    if not cov:
        return (
            '<section class="panel"><div class="panel-head"><h2>Coverage by query</h2></div><div class="panel-body">'
            f'<p class="muted">Nothing to show until positions are imported. The project has {len(project.searches)} queries: '
            + e(", ".join(project.searches[:6]))
            + ("..." if len(project.searches) > 6 else "")
            + "</p></div></section>"
        )
    rows = []
    for item in cov:
        compare = f'/p/{e(project.slug)}/comparisons{e(_q({"q": item.search}))}'
        if item.error:
            stored_cell = f'<span class="muted">{e(item.error)}</span>'
            rows.append(
                f'<tr><th scope="row">{e(item.search)}</th><td colspan="4" data-label="Status">{stored_cell}</td><td class="hide-sm"></td><td></td></tr>'
            )
            continue
        share = item.stored / item.candidates if item.candidates else 0
        meter_cls = "meter-ok" if share >= 0.75 else ("meter-mid" if share >= 0.5 else "meter-low")
        missing = ""
        if item.missing:
            links = "".join(f"<li>{_s().ext_link(url)}</li>" for url in item.missing)
            missing = f'<details class="mini"><summary class="nowrap">{len(item.missing)} missing</summary><ul class="urls">{links}</ul></details>'
        else:
            missing = '<span class="muted small">none</span>'
        yours = f"#{item.yours}" if item.yours else '<span class="muted">not listed</span>'
        comps = e(", ".join(item.competitors)) if item.competitors else '<span class="muted">none listed</span>'
        action = f'<a href="{compare}">Compare</a>' if engine_ready else '<span class="muted small">build first</span>'
        rows.append(
            f'<tr><th scope="row">{e(item.search)}</th>'
            f'<td class="num cov-cand" data-label="Candidates">{item.candidates}</td>'
            f'<td class="num" data-label="HTML stored"><span class="meter {meter_cls}"><span class="sr-only">{item.stored} of {item.candidates} stored</span><span aria-hidden="true">{item.stored}/{item.candidates}</span></span></td>'
            f'<td data-label="Missing">{missing}</td>'
            f'<td class="num" data-label="You (observed)">{yours}</td>'
            f'<td class="small hide-sm hide-md">{comps}</td>'
            f'<td class="cov-action">{action}</td></tr>'
        )
    total = sum(item.candidates for item in cov)
    stored = sum(item.stored for item in cov)
    return (
        '<section class="panel"><div class="panel-head"><h2>Coverage by query</h2>'
        f'<p class="panel-note">{stored} of {total} candidate pages have stored HTML. Missing pages keep their observed position and get PREDICTED n/a.</p></div>'
        '<div class="table-wrap"><table class="table table-cov"><thead><tr>'
        '<th scope="col">Query</th><th scope="col" class="num">Candidates</th><th scope="col" class="num">HTML stored</th>'
        '<th scope="col">Missing</th><th scope="col" class="num">You (observed)</th><th scope="col" class="hide-sm hide-md">Competitors present</th><th scope="col"><span class="sr-only">Open</span></th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _competitor_panel(ui, project, meta, cov, csrf: str, problems) -> str:
    present = {domain for item in cov for domain in item.competitors}
    listed = []
    for domain in meta.competitors:
        state = '<span class="chip chip-ok">in results</span>' if domain in present else '<span class="chip chip-na">not in results</span>'
        listed.append(f"<li><span>{e(domain)}</span>{state}</li>")
    current = "\n".join(meta.competitors)
    issues = ""
    if problems:
        issues = _alert("<ul class=\"tight\">" + "".join(f"<li>{e(item)}</li>" for item in problems) + "</ul>", "warn")
    form = ""
    if not ui.public_demo:
        form = (
            f'<form method="post" action="/p/{e(project.slug)}/competitors" class="form">' + csrf
            + f'<div class="field"><label for="comp">Competitor domains or URLs</label><textarea id="comp" name="competitors" rows="4" aria-describedby="comp-h">{e(current)}</textarea>'
            '<p class="hint" id="comp-h">One per line. Subdomains count as the same competitor.</p></div>'
            '<div class="form-actions"><button type="submit" class="btn">Save competitors</button></div></form>'
        )
    return (
        '<section class="panel"><div class="panel-head"><h2>Competitors</h2></div><div class="panel-body">'
        f'<p class="small">Your site: <b>{e(project.site)}</b></p>'
        + (f'<ul class="domain-list">{"".join(listed)}</ul>' if listed else '<p class="muted small">No competitors named yet. Every observed result is still compared; naming competitors labels them and lets a draft test use only them.</p>')
        + issues
        + form
        + "</div></section>"
    )


def _import_panel(ui, project, meta, csrf: str) -> str:
    if ui.public_demo or meta.dataset in {ws.DATASET_SYNTHETIC, ws.DATASET_HISTORICAL}:
        return ""
    return (
        '<section class="panel"><div class="panel-head"><h2>Import positions</h2></div><div class="panel-body">'
        f'<form method="post" action="/p/{e(project.slug)}/import" enctype="multipart/form-data" class="form" data-busy="Checking the file...">' + csrf
        + '<div class="field"><label for="csv">CSV file</label><input id="csv" type="file" name="csv_file" accept=".csv,text/csv" required aria-describedby="csv-h">'
        '<p class="hint" id="csv-h">Columns query,url,position. Search Console exports (Query, Page, Position) work too. Max 5 MB.</p></div>'
        '<div class="field"><label for="csv-date">Date the positions were seen <span class="opt">optional</span></label><input id="csv-date" type="date" name="snapshot_date" aria-describedby="csv-date-h">'
        '<p class="hint" id="csv-date-h">Left empty, the data is shown as "pull date not recorded".</p></div>'
        '<label class="check"><input type="checkbox" name="add_searches" value="1" checked> Add queries from the file to this project</label>'
        '<div class="form-actions"><button type="submit" class="btn">Check and import</button></div></form></div></section>'
    )


def _keys_panel(ui) -> str:
    google = "configured" if serve_google_ready(ui) else "not configured: import a CSV instead"
    ai = "configured" if ui.llm_ready() else f"not configured (set {ws.AI_KEYS} before starting)"
    return (
        '<section class="panel panel-quiet"><div class="panel-head"><h2>Providers on this server</h2></div><div class="panel-body">'
        f'<dl class="facts facts-stack"><div><dt>Live Google results</dt><dd>{e(google)}</dd></div>'
        f"<div><dt>AI notes</dt><dd>{e(ai)}</dd></div></dl>"
        '<p class="hint">Key values are never shown or sent to the browser.</p></div></section>'
    )


# ---------------------------------------------------------------------------
# 2. Comparisons
# ---------------------------------------------------------------------------


def comparisons_body(ui, project, meta, ctx, query: str, sel: str, sort: str, *, load_error: str = "") -> str:
    parts = [
        '<div class="page-head"><div><h1>Comparisons</h1>'
        f'<p class="sub">One query at a time: the observed order of the candidates next to the {_pred()} order the model gives the same pages.</p></div></div>'
    ]
    if load_error:
        parts.append(_alert(e(load_error), "warn"))
        return "".join(parts)
    searches = list(project.searches)
    cov = {item.search: item for item in ws.coverage(project, ui.database, meta)}
    picker = _query_picker(project, searches, cov, query, "comparisons")
    view = ws.compare_search(ctx, query, meta)
    main = _comparison_main(project, meta, view, sel, sort)
    parts.append(f'<div class="cmp-layout">{picker}<div class="cmp-main">{main}</div></div>')
    return "".join(parts)


def _query_picker(project, searches, cov, query: str, screen: str, *, with_select: bool = True) -> str:
    items = []
    for search in searches:
        item = cov.get(search)
        meta_line = f"{item.stored}/{item.candidates} stored" if item and item.candidates else "no data"
        if item and item.yours:
            meta_line += f", you #{item.yours}"
        here = ' aria-current="true"' if search == query else ""
        items.append(
            f'<li><a href="/p/{e(project.slug)}/{screen}{e(_q({"q": search}))}"{here}><span class="qp-q">{e(search)}</span>'
            f'<span class="qp-m">{e(meta_line)}</span></a></li>'
        )
    options = "".join(f'<option value="{e(search)}"{" selected" if search == query else ""}>{e(search)}</option>' for search in searches)
    select = (
        f'<form class="qp-select" method="get" action="/p/{e(project.slug)}/{screen}" data-autosubmit>'
        '<label for="qp">Query</label>'
        f'<select id="qp" name="q">{options}</select><button type="submit" class="btn btn-sm js-hide">Show</button></form>'
        if with_select
        else ""
    )
    return (
        f'<nav class="qpicker{"" if with_select else " qp-list-only"}" aria-label="Queries">{select}'
        f'<h2 class="qp-title">Queries <span class="muted">({len(searches)})</span></h2><ul class="qp-list">{"".join(items)}</ul></nav>'
    )


def _comparison_main(project, meta, view: ws.ComparisonView, sel: str, sort: str) -> str:
    slug = e(project.slug)
    head = (
        f'<div class="cmp-head"><h2 class="cmp-query">{e(view.search)}</h2>'
        f'<div class="cmp-meta"><span>{view.total} candidates</span><span>{view.scored} with HTML</span>'
        f'<span>{view.total - view.scored} missing HTML</span><span>intent: {e(view.intent)}</span>'
        f'<span>positions seen: {e(_seen(meta, view.pulled))}</span></div></div>'
    )
    scope = f'<p class="scope">{e(ws.SCOPE_NOTE)}</p>'
    if view.error:
        table = _candidate_table(project, meta, view, None, sort) if view.candidates else ""
        return head + scope + _alert(e(view.error), "warn") + table
    selected = _selected(view, sel)
    summary = _summary_row(project, meta, view)
    table = _candidate_table(project, meta, view, selected, sort)
    detail = _detail_panel(project, meta, view, selected)
    return head + scope + summary + f'<div class="cmp-body"><div class="cmp-table">{table}</div>{detail}</div>'


def _seen(meta, pulled: str) -> str:
    if meta is not None and meta.synthetic:
        return "invented for this demo"
    return pulled or "n/a"


def _selected(view: ws.ComparisonView, sel: str):
    if sel.isdigit():
        index = int(sel)
        if 0 <= index < len(view.candidates):
            return view.candidates[index]
    mine = next((cand for cand in view.candidates if cand.role == "you"), None)
    return mine or view.candidates[0]


def _summary_row(project, meta, view: ws.ComparisonView) -> str:
    if view.your_url is None:
        you = (
            '<div class="sum-item"><span class="sum-k">Your page</span><span class="sum-v muted">Not in the observed results</span>'
            f'<a class="small" href="/p/{e(project.slug)}/draft{e(_q({"q": view.search}))}">Test a draft for this query</a></div>'
        )
    else:
        predicted = f"#{view.your_predicted}" if view.your_predicted else "n/a"
        you = (
            '<div class="sum-item"><span class="sum-k">Your page</span>'
            f'<span class="sum-v">Observed #{view.your_observed} <span class="sep" aria-hidden="true">|</span> {_pred()} {e(predicted)}'
            f' <span class="muted small">of {view.scored} scored</span></span>{_confidence_chip(view.confidence)}</div>'
        )
    agree = "n/a" if view.agreement is None else f"{view.agreement:+.2f}"
    backtest = "n/a" if view.query_pairwise is None else f"{view.query_pairwise:.2f} on {view.query_pairs} pairs"
    model = (
        '<div class="sum-item"><span class="sum-k">Agreement with observed order</span>'
        f'<span class="sum-v">{e(agree)} <span class="muted small">Spearman, 1 = same order</span></span></div>'
        '<div class="sum-item"><span class="sum-k">Held-out backtest, this query</span>'
        f'<span class="sum-v">{e(backtest)} <span class="muted small">pairwise, 0.50 = coin flip</span></span></div>'
    )
    return f'<div class="summary">{you}{model}</div>'


def _candidate_table(project, meta, view: ws.ComparisonView, selected, sort: str) -> str:
    slug = e(project.slug)
    cands = list(enumerate(view.candidates))
    if sort == "predicted":
        cands.sort(key=lambda pair: (pair[1].predicted is None, pair[1].predicted or 0, pair[1].observed))
    base = {"q": view.search}
    sort_links = (
        '<div class="sortbar" role="group" aria-label="Order rows by">'
        f'<span class="small muted">Order rows by</span>'
        f'<a class="seg" href="/p/{slug}/comparisons{e(_q({**base, "sort": ""}))}"{CURRENT if sort != "predicted" else ""}>Observed</a>'
        f'<a class="seg" href="/p/{slug}/comparisons{e(_q({**base, "sort": "predicted"}))}"{CURRENT if sort == "predicted" else ""}>PREDICTED</a></div>'
    )
    rows = []
    for index, cand in cands:
        is_sel = selected is not None and cand is selected
        cls = " ".join(item for item in ("row-you" if cand.role == "you" else "", "row-sel" if is_sel else "", "row-missing" if not cand.stored else "") if item)
        predicted = f"{cand.predicted}" if cand.predicted is not None else '<span class="na">n/a</span>'
        if cand.shift is None:
            shift = '<span class="na">n/a</span>'
        elif cand.shift == 0:
            shift = '<span class="muted">same</span>'
        else:
            arrow = "up" if cand.shift > 0 else "down"
            shift = f'<span class="shift shift-{arrow}">{"+" if cand.shift > 0 else ""}{cand.shift}</span>'
        title = cand.title or cand.domain
        html_cell = (
            f'<span class="ok-dot" aria-hidden="true"></span>{e(cand.stored_at or "stored")}' if cand.stored else '<span class="miss">missing</span>'
        )
        words = f"{cand.word_count:,}" if cand.word_count is not None else '<span class="na">n/a</span>'
        link = f'/p/{slug}/comparisons{e(_q({**base, "sel": index, "sort": sort if sort == "predicted" else ""}))}#detail'
        rows.append(
            f'<tr class="{cls}">'
            f'<td class="num pos">{cand.observed}</td>'
            f'<td class="num pos pos-pred">{predicted}</td>'
            f'<td class="num hide-sm">{shift}</td>'
            f'<td class="page-cell"><a class="page-link" href="{link}"{CURRENT if is_sel else ""}>{e(title)}</a>'
            f'<div class="page-sub"><span class="url">{e(cand.domain)}</span>{_role_chip(cand.role)}</div></td>'
            f'<td class="hide-sm small nowrap">{html_cell}</td>'
            f'<td class="num hide-md small">{words}</td></tr>'
        )
    caption = (
        f'<caption class="sr-only">Candidates for {e(view.search)}: observed position and PREDICTED position among the {view.scored} candidates with stored HTML.</caption>'
    )
    return (
        sort_links
        + '<div class="table-wrap"><table class="table table-cmp">'
        + caption
        + '<thead><tr><th scope="col" class="num">Observed</th>'
        f'<th scope="col" class="num">PREDICTED</th><th scope="col" class="num hide-sm" title="Observed rank among scored candidates minus PREDICTED rank">Shift</th>'
        '<th scope="col">Page</th><th scope="col" class="hide-sm">HTML stored</th><th scope="col" class="num hide-md">Words</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        + f'<p class="table-note small muted">PREDICTED ranks 1 to {view.scored} among candidates with stored HTML. Shift compares that with their observed order. '
        "Select a page to see its evidence.</p>"
    )


def _detail_panel(project, meta, view: ws.ComparisonView, cand) -> str:
    serve = _s()
    if cand is None:
        return ""
    role = {"you": "Your page", "competitor": "Competitor", "other": "Other observed result"}[cand.role]
    facts = [
        ("Observed position", f"#{cand.observed} {_obs(meta)}"),
        ("PREDICTED among scored", (f"#{cand.predicted} of {view.scored}" if cand.predicted else "n/a: no stored HTML")),
        ("HTML", (f"stored {e(cand.stored_at)}" if cand.stored_at else "stored") + (f", from {e(cand.seeded_from)}" if cand.seeded_from else "") if cand.stored else "missing: not fetched or blocked"),
    ]
    if cand.stored:
        facts.extend(
            [
                ("H1", e(cand.h1) or '<span class="na">none</span>'),
                ("Words", f"{cand.word_count:,}"),
                ("Table / FAQ", f"{'yes' if cand.has_table else 'no'} / {'yes' if cand.has_faq else 'no'}"),
                ("Visible date", e(cand.updated) or '<span class="na">none found</span>'),
            ]
        )
    dl = "".join(f"<div><dt>{label}</dt><dd>{value}</dd></div>" for label, value in facts)
    evidence = ""
    if cand.evidence:
        rows = "".join(
            f'<li><span class="ev-k">{e(item["label"])}</span><span class="ev-v">{e(item["value"])}</span>'
            f'<span class="ev-r{" ev-top" if item["rank"] == 1 else ""}">{item["rank"]} of {item["of"]}</span></li>'
            for item in cand.evidence
        )
        evidence = (
            '<h4>Evidence the model reads <span class="opt">value, and rank among the scored pages</span></h4>'
            f'<ul class="ev">{rows}</ul>'
        )
    elif not cand.stored:
        evidence = '<p class="small">No evidence: the page HTML is not stored, so the model never read it. It is not guessed.</p>'
    limits = [ws.SCOPE_NOTE, ws.CONTENT_LIMIT_NOTE]
    if view.overall_pairwise is not None:
        limits.append(
            f"Held-out backtest across {view.overall_queries} queries: pairwise {view.overall_pairwise:.2f} (0.50 is a coin flip)."
            + (" On SYNTHETIC data this only shows the pipeline runs; it says nothing about real accuracy." if meta.synthetic else "")
        )
    if view.confidence_note and cand.role == "you":
        limits.append(f"Confidence {view.confidence}: {view.confidence_note}")
    limits.extend(view.notes)
    limits.append("No backlink data is configured. Site signals come from the crawled pages only.")
    lim = "".join(f"<li>{e(item)}</li>" for item in limits)
    return (
        f'<aside class="detail" id="detail" aria-labelledby="detail-title"><div class="detail-head"><p class="eyebrow">{e(role)}</p>'
        f'<h3 id="detail-title">{e(cand.title or cand.domain)}</h3><p class="url break">{serve.ext_link(cand.url)}</p></div>'
        f'<dl class="facts facts-tight">{dl}</dl>{evidence}'
        f'<details class="limits" open><summary>Limitations</summary><ul class="tight">{lim}</ul></details>'
        + (f'<p><a class="btn btn-sm" href="/p/{e(project.slug)}/draft{e(_q({"q": view.search}))}">Test a draft against these candidates</a></p>' if cand.role == "you" or view.your_url is None else "")
        + "</aside>"
    )


# ---------------------------------------------------------------------------
# 3. Draft test
# ---------------------------------------------------------------------------


def draft_body(ui, project, meta, ctx, query: str, *, values=None, error: str = "", load_error: str = "", note: str = "") -> str:
    serve = _s()
    parts = [
        '<div class="page-head"><div><h1>Draft test</h1>'
        f'<p class="sub">Score an original page and a draft against the same candidates with the same model. Every rank here is {_pred()}.</p></div></div>'
    ]
    if load_error:
        parts.append(_alert(e(load_error), "warn"))
        return "".join(parts)
    if error:
        parts.append(_alert(e(error), "bad", role="alert"))
    if note:
        parts.append(_alert(e(note), "good"))
    values = values or {}
    view = ws.compare_search(ctx, query, meta)
    mine_url = view.your_url
    stored_mine = next((cand for cand in view.candidates if cand.role == "you" and cand.stored), None)
    stored_all, _missing_all, _ = _safe_set(ctx, query, meta, "all", mine_url or "")
    stored_comp, _missing_comp, _ = _safe_set(ctx, query, meta, "competitors", mine_url or "")
    default_url = values.get("url") or mine_url or f"https://{project.site}/new-page/"
    which = values.get("which") or "all"
    original_mode = values.get("original_mode") or ("stored" if stored_mine else "none")
    if original_mode == "stored" and stored_mine is None:
        original_mode = "none"
    queries = "".join(f'<option value="{e(item)}"{" selected" if item == query else ""}>{e(item)}</option>' for item in project.searches)
    comp_disabled = "" if stored_comp else " disabled"
    comp_note = f"{len(stored_comp)} with HTML" if meta.competitors else "no competitors named"
    stored_opt = ""
    if stored_mine is not None:
        stored_opt = (
            f'<label class="radio"><input type="radio" name="original_mode" value="stored"{" checked" if original_mode == "stored" else ""}>'
            f' Stored copy of your observed page <span class="muted small">({e(stored_mine.stored_at or "stored")})</span></label>'
        )
    ai_field = (
        '<label class="check"><input type="checkbox" name="ai_notes" value="1"> Add AI notes <span class="muted small">(uses your configured provider; sends the measured differences, not the page)</span></label>'
        if ui.llm_ready()
        else f'<div class="unavailable" role="note"><b>AI notes unavailable.</b> No AI provider is configured on this server. Set {e(ws.AI_KEYS)} and restart to enable them. The test below runs without them.</div>'
    )
    original_text = values.get("original_html", "")
    draft_text = values.get("draft_html", "")
    form = (
        f'<form method="post" action="/p/{e(project.slug)}/draft" class="form draft-form" data-busy="Scoring...">' + serve._csrf_input(ui)
        + '<div class="form-grid">'
        f'<div class="field"><label for="d-q">Query</label><select id="d-q" name="q" data-navigate="/p/{e(project.slug)}/draft?q=">{queries}</select></div>'
        '<fieldset class="field"><legend>Compare against</legend>'
        f'<label class="radio"><input type="radio" name="which" value="all"{" checked" if which != "competitors" else ""}> All observed candidates <span class="muted small">({len(stored_all)} with HTML)</span></label>'
        f'<label class="radio"><input type="radio" name="which" value="competitors"{" checked" if which == "competitors" and stored_comp else ""}{comp_disabled}> Named competitors only <span class="muted small">({e(comp_note)})</span></label>'
        "</fieldset>"
        f'<div class="field span-2"><label for="d-url">Page URL</label><input id="d-url" name="url" value="{e(default_url)}" inputmode="url" maxlength="2048" aria-describedby="d-url-h">'
        f'<p class="hint" id="d-url-h">{"Your page in the observed results for this query. " if mine_url else "Your site has no observed page for this query, so this is scored as a new page. "}Link signals come from this URL.</p></div>'
        "</div>"
        '<fieldset class="field"><legend>Original</legend>'
        f"{stored_opt}"
        f'<label class="radio"><input type="radio" name="original_mode" value="paste"{" checked" if original_mode == "paste" else ""}> Paste the original</label>'
        f'<label class="radio"><input type="radio" name="original_mode" value="none"{" checked" if original_mode == "none" else ""}> No original (a new page)</label>'
        f'<div class="field" data-show-when="paste"><label for="d-orig">Original HTML or text</label><textarea id="d-orig" name="original_html" rows="7" spellcheck="false">{e(original_text)}</textarea></div>'
        "</fieldset>"
        f'<div class="field"><label for="d-draft">Draft HTML or text</label><textarea id="d-draft" name="draft_html" rows="12" required spellcheck="false" aria-describedby="d-draft-h">{e(draft_text)}</textarea>'
        '<p class="hint" id="d-draft-h">Paste full HTML, or plain text: the first line becomes the title and H1. Max 2 MB. The original is saved unchanged next to the draft.</p></div>'
        f"{ai_field}"
        '<div class="form-actions"><button type="submit" class="btn btn-primary">Run PREDICTED test</button>'
        f'<span class="hint">Ranks only the {len(stored_all)} candidates above, not Google\'s index.</span></div></form>'
    )
    runs = ws.list_draft_runs(project)
    recent = _recent_runs(project, runs)
    cov = {item.search: item for item in ws.coverage(project, ui.database, meta)}
    picker = _query_picker(project, list(project.searches), cov, query, "draft", with_select=False)
    parts.append(
        f'<div class="cmp-layout">{picker}<div class="cmp-main"><section class="panel"><div class="panel-body">{form}</div></section>{recent}</div></div>'
    )
    return "".join(parts)


def _safe_set(ctx, query, meta, which, url):
    try:
        return ws.candidate_set(ctx, query, meta, which, url)
    except ws.WorkspaceError:
        return [], [], ""


def _recent_runs(project, runs: list[dict]) -> str:
    if not runs:
        return (
            '<section class="panel panel-quiet"><div class="panel-head"><h2>Saved tests</h2></div><div class="panel-body">'
            '<p class="muted small">No draft tests yet. Each run is saved with its original and draft, so you can reopen it after a reload.</p></div></section>'
        )
    rows = []
    for run in runs:
        before = (run.get("before") or {}).get("position")
        after = (run.get("after") or {}).get("position")
        of = (run.get("after") or {}).get("of")
        change = f"#{before} to #{after}" if before else f"#{after} (no original)"
        rows.append(
            f'<tr><td><a href="/p/{e(project.slug)}/draft/{e(run["id"])}">{e(run.get("search"))}</a></td>'
            f'<td class="num">{e(change)} <span class="muted small">of {e(of)}</span></td>'
            f'<td class="small hide-sm">{e(run.get("set_label"))}</td><td class="small muted hide-sm">{e((run.get("created") or "")[:16].replace("T", " "))} UTC</td></tr>'
        )
    return (
        '<section class="panel"><div class="panel-head"><h2>Saved tests</h2></div>'
        '<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Query</th><th scope="col" class="num">PREDICTED</th>'
        f'<th scope="col" class="hide-sm">Candidates</th><th scope="col" class="hide-sm">Run at</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def draft_result_body(ui, project, meta, run: dict) -> str:
    serve = _s()
    after = run["after"]
    before = run.get("before")
    slug = e(project.slug)
    head = (
        '<div class="page-head"><div><p class="eyebrow">Draft test result</p>'
        f'<h1>{e(run["search"])}</h1><p class="sub">{e(run["candidates"])} candidates ({e(run["set_label"])}), saved '
        f'{e((run.get("created") or "")[:16].replace("T", " "))} UTC. Page URL: <span class="url">{e(run["url"])}</span></p></div>'
        f'<div class="head-actions"><a class="btn" href="/p/{slug}/draft{e(_q({"q": run["search"], "from": run["id"]}))}">Edit draft and run again</a>'
        f'<a class="btn btn-ghost" href="/p/{slug}/comparisons{e(_q({"q": run["search"]}))}">Back to comparison</a></div></div>'
    )
    of = after["of"]
    if before is not None:
        change = before["position"] - after["position"]
        if change > 0:
            verdict = f"The draft is {_pred()} {change} place{'s' if change != 1 else ''} higher than the original among these {of - 1} candidates."
            tone = "good"
        elif change < 0:
            verdict = f"The draft is {_pred()} {-change} place{'s' if change != -1 else ''} lower than the original among these {of - 1} candidates."
            tone = "bad"
        else:
            verdict = f"The draft gets the same {_pred()} position as the original among these {of - 1} candidates."
            tone = "neutral"
        tiles = (
            f'<div class="ba"><div class="ba-col"><span class="ba-k">Original</span><span class="ba-v">#{before["position"]}</span><span class="ba-of">of {of}</span>{_confidence_chip(before["confidence"])}</div>'
            '<div class="ba-arrow" aria-hidden="true"></div>'
            f'<div class="ba-col ba-after"><span class="ba-k">Draft</span><span class="ba-v">#{after["position"]}</span><span class="ba-of">of {of}</span>{_confidence_chip(after["confidence"])}</div>'
            f'<p class="ba-verdict verdict-{tone}">{verdict}</p></div>'
        )
    else:
        tiles = (
            f'<div class="ba"><div class="ba-col ba-after"><span class="ba-k">Draft</span><span class="ba-v">#{after["position"]}</span><span class="ba-of">of {of}</span>{_confidence_chip(after["confidence"])}</div>'
            f'<p class="ba-verdict">No original was given, so there is no before. The draft is {_pred()} #{after["position"]} among {of - 1} candidates plus the draft.</p></div>'
        )
    query_bt = "n/a" if run.get("query_pairwise") is None else "pairwise {:.2f} on {} pairs".format(run["query_pairwise"], run.get("query_pairs"))
    overall_bt = "n/a" if run.get("overall_pairwise") is None else "{:.2f}".format(run["overall_pairwise"])
    caveat = (
        f'<div class="caveat"><p><b>What this does not show.</b> {e(ws.DRAFT_LIMIT_NOTE)} {e(ws.CONTENT_LIMIT_NOTE)}</p>'
        f'<p class="small">Held-out backtest for this query: {query_bt}. '
        f'Across all queries: {overall_bt} (0.50 is a coin flip). '
        + ("These numbers come from SYNTHETIC data and say nothing about real accuracy. " if run.get("dataset") == ws.DATASET_SYNTHETIC else "")
        + "UNSURE means the score gap to the next page is within the model's noise.</p></div>"
    )
    run_notes = "".join(f"<li>{e(item)}</li>" for item in run.get("notes") or [])
    notes_html = f'<ul class="tight run-notes">{run_notes}</ul>' if run_notes else ""
    summary = f'<section class="panel"><div class="panel-body">{tiles}{notes_html}{caveat}</div></section>'
    orders = _order_compare(project, meta, run)
    diff = _facts_diff(run)
    topic = _topic_panel(run)
    ai = _ai_panel(ui, run)
    missing = ""
    if run.get("missing_candidates"):
        links = "".join(f"<li>{serve.ext_link(url)}</li>" for url in run["missing_candidates"])
        missing = (
            '<section class="panel panel-quiet"><div class="panel-head"><h2>Candidates left out</h2></div><div class="panel-body">'
            f'<p class="small">{len(run["missing_candidates"])} observed result{"s" if len(run["missing_candidates"]) != 1 else ""} in this set {"have" if len(run["missing_candidates"]) != 1 else "has"} no stored HTML, so {"they were" if len(run["missing_candidates"]) != 1 else "it was"} not scored. The effect on the order is unknown, not zero.</p>'
            f'<ul class="urls">{links}</ul></div></section>'
        )
    sources = (
        '<section class="panel panel-quiet"><div class="panel-head"><h2>Saved inputs</h2></div><div class="panel-body small">'
        f'<p>Original: {e(run.get("original_label") or "none")}. Draft and original HTML are saved with this run and are not changed by later runs.</p>'
        f'<p><a href="/p/{slug}/draft/{e(run["id"])}/draft.txt">View draft source</a>'
        + (f' <span class="sep" aria-hidden="true">|</span> <a href="/p/{slug}/draft/{e(run["id"])}/original.txt">View original source</a>' if run.get("has_original") and run.get("original_label", "").startswith(("pasted", "stored")) else "")
        + f'</p><p class="muted">Positions source: {e(run.get("source") or "n/a")} Seen: {e(_seen(meta, run.get("pulled") or ""))}.</p></div></section>'
    )
    return head + summary + f'<div class="grid-2 grid-top">{orders}{diff}</div>' + f'<div class="grid-2 grid-top">{topic}{ai}</div>' + missing + sources


def _order_compare(project, meta, run: dict) -> str:
    before = run.get("before")
    after = run["after"]

    def column(result: dict, label: str) -> str:
        rows = []
        for item in result["order"]:
            if item["page"]:
                rows.append(f'<li class="ord-you"><span class="ord-n">{item["predicted"]}</span><span class="ord-name"><b>{e(label)}</b></span></li>')
            else:
                chip = _role_chip(ws.role_of(project, meta, item["url"]))
                rows.append(
                    f'<li><span class="ord-n">{item["predicted"]}</span><span class="ord-name">{e(item["domain"])} {chip}'
                    f'<span class="ord-obs">observed #{item["observed"]}</span></span></li>'
                )
        return f'<div class="ord-col"><h3>With the {e(label.lower())}</h3><ol class="ord">{"".join(rows)}</ol></div>'

    cols = (column(before, "Original") if before else "") + column(after, "Draft")
    return (
        '<section class="panel"><div class="panel-head"><h2>PREDICTED order</h2>'
        '<p class="panel-note">Same candidates, same model. Only your page changes.</p></div>'
        f'<div class="panel-body"><div class="ord-wrap{" ord-two" if before else ""}">{cols}</div></div></section>'
    )


def _facts_diff(run: dict) -> str:
    after = run["facts_after"]
    before = run.get("facts_before")
    rows = []
    for key, label in ws.PAGE_FACTS:
        new = after.get(key)
        old = before.get(key) if before else None
        changed = before is not None and str(old) != str(new)
        old_cell = e(old) if before is not None else '<span class="na">n/a</span>'
        rows.append(
            f'<tr class="{"row-changed" if changed else ""}"><th scope="row">{e(label)}</th><td>{old_cell}</td><td>{e(new)}'
            + ('<span class="sr-only"> (changed)</span>' if changed else "")
            + "</td></tr>"
        )
    feats = []
    fa = (run["after"] or {}).get("features") or {}
    fb = ((run.get("before") or {}).get("features")) or {}
    for name in ws.EVIDENCE_FEATURES:
        if name not in fa:
            continue
        new = fa.get(name)
        old = fb.get(name) if before is not None else None
        if new is None and old is None:
            continue
        changed = before is not None and old is not None and new is not None and abs(old - new) > 0.005
        feats.append(
            f'<tr class="{"row-changed" if changed else ""}"><th scope="row">{e(ws._feature_label(name))}</th>'
            f'<td class="num">{_num(old) if before is not None else "n/a"}</td><td class="num">{_num(new)}</td></tr>'
        )
    return (
        '<section class="panel"><div class="panel-head"><h2>What changed on the page</h2>'
        '<p class="panel-note">Highlighted rows differ between the original and the draft.</p></div>'
        '<div class="table-wrap"><table class="table table-mini table-diff"><thead><tr><th scope="col">Fact</th><th scope="col">Original</th><th scope="col">Draft</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<details class="mini pad"><summary>Model signals (raw values)</summary><div class="table-wrap"><table class="table table-mini"><thead><tr><th scope="col">Signal</th>'
        f'<th scope="col" class="num">Original</th><th scope="col" class="num">Draft</th></tr></thead><tbody>{"".join(feats)}</tbody></table></div></details></section>'
    )


def _topic_panel(run: dict) -> str:
    def block(label: str, topic: dict | None) -> str:
        if topic is None:
            return f'<div class="topic"><h3>{e(label)}</h3><p class="muted small">n/a</p></div>'
        status = topic.get("status") or "not checked"
        cls = "chip-ok" if topic.get("on_topic") is True else ("chip-na" if topic.get("on_topic") is None else "chip-bad")
        missing = topic.get("missing") or []
        words = f'<p class="small">Query words not found: {e(", ".join(missing))}</p>' if missing else ""
        return f'<div class="topic"><h3>{e(label)} <span class="chip {cls}">{e(status)}</span></h3><p class="small">{e(_observed_words(topic.get("text") or ""))}</p>{words}</div>'

    parts = []
    if run.get("has_original"):
        parts.append(block("Original", run.get("topic_before")))
    parts.append(block("Draft", run.get("topic_after")))
    return (
        '<section class="panel"><div class="panel-head"><h2>On-topic check</h2>'
        f'<p class="panel-note">{_pred()} A separate check that the page covers the query and does not repeat itself.</p></div>'
        f'<div class="panel-body">{"".join(parts)}</div></section>'
    )


def _observed_words(text: str) -> str:
    """The engine's wording says 'Google ranks'; on this screen the pages are the observed candidates."""
    return (
        text.replace("pages Google ranks here", "the observed pages here")
        .replace("page Google ranks in this project", "observed page in this project")
        .replace("pages Google ranks", "observed pages")
    )


def _ai_panel(ui, run: dict) -> str:
    ai = run.get("ai") or {}
    status = ai.get("status")
    if status == "ok":
        body = f'<p class="small muted">Model text from {e(ai.get("provider") or "the configured provider")}. Not measured, not a ranking claim.</p><div class="ai-text">{e(ai.get("text"))}</div>'
    elif status == "error":
        body = _alert(e(ai.get("text")), "bad")
    elif status == "unavailable":
        body = f'<div class="unavailable"><b>AI notes unavailable.</b> {e(ai.get("text"))}</div>'
    else:
        body = (
            '<p class="small muted">Not requested for this run.</p>'
            if ui.llm_ready()
            else f'<div class="unavailable"><b>AI notes unavailable.</b> No AI provider is configured on this server. Set {e(ws.AI_KEYS)} to enable them.</div>'
        )
    return f'<section class="panel"><div class="panel-head"><h2>AI notes <span class="opt">optional</span></h2></div><div class="panel-body">{body}</div></section>'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register(app: FastAPI, ui) -> None:
    serve = _s()

    def load(slug: str):
        project = ui.project(slug)
        return project, ws.load_meta(project)

    def pick_query(project, q: str) -> str:
        if q in project.searches:
            return q
        return project.searches[0] if project.searches else ""

    def context_or_error(slug: str):
        from aeo.ranksim.project import ProjectError

        try:
            return ui.context(slug), ""
        except ProjectError as exc:
            return None, f"{exc} Go to Setup to import positions or load the demo."
        except Exception as exc:  # noqa: BLE001 - shown, scrubbed
            return None, ui.scrub(f"The engine could not be loaded: {type(exc).__name__}: {str(exc)[:200]}")

    @app.get("/", response_class=HTMLResponse)
    def home(demo: int = 0) -> Response:
        if demo:
            return RedirectResponse("/demo?demo=1", status_code=303)
        return shell(ui, "Projects", home_body(ui), wide=True)

    @app.get("/go")
    def go(p: str = "") -> Response:
        if not serve.SLUG_RE.match(p or ""):
            return RedirectResponse("/", status_code=303)
        return RedirectResponse(f"/p/{p}", status_code=303)

    @app.post("/demo/synthetic")
    async def demo_synthetic(request: Request) -> Response:
        form = await request.form()
        serve._check_csrf(ui, form)
        slug = ws.DEMO_SLUG
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return shell(ui, "Projects", home_body(ui, error="The demo is being built right now. Try again in a few seconds."), status=409)
        try:
            await run_in_threadpool(ws.load_synthetic_demo, ui.root, ui.settings, ui.database)
        except Exception as exc:  # noqa: BLE001
            return shell(ui, "Projects", home_body(ui, error=ui.scrub(f"The demo could not be built: {type(exc).__name__}: {str(exc)[:200]}")), status=500)
        finally:
            lock.release()
        ui.forget(slug)
        return RedirectResponse(f"/p/{slug}?msg=demo", status_code=303)

    @app.post("/demo/historical")
    async def demo_historical(request: Request) -> Response:
        form = await request.form()
        serve._check_csrf(ui, form)
        if not historical_allowed(ui):
            return shell(ui, "Projects", home_body(ui, error="The historical SmartCrew export is not available on this server."), status=404)
        slug = "smartcrew-historical"
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return shell(ui, "Projects", home_body(ui, error="The export is being loaded right now. Try again in a few seconds."), status=409)
        try:
            await run_in_threadpool(ws.load_historical_demo, ui.root, ui.settings, ui.database)
        except Exception as exc:  # noqa: BLE001
            return shell(ui, "Projects", home_body(ui, error=ui.scrub(f"The export could not be loaded: {type(exc).__name__}: {str(exc)[:200]}")), status=500)
        finally:
            lock.release()
        ui.forget(slug)
        return RedirectResponse(f"/p/{slug}?msg=historical", status_code=303)

    @app.post("/projects")
    async def create(request: Request) -> Response:
        try:
            form = await request.form(max_files=1, max_fields=20, max_part_size=ui.max_upload_bytes + 1024)
        except Exception:  # noqa: BLE001
            return shell(ui, "Projects", home_body(ui, error="That form could not be read. Uploads are limited to 5 MB."), status=400)
        serve._check_csrf(ui, form)
        from starlette.datastructures import UploadFile

        from aeo.ranksim.project import ProjectError, create_project, split_searches

        values = {key: str(form.get(key) or "") for key in ("name", "site", "searches", "competitors")}
        if ui.public_demo:
            return shell(ui, "Projects", home_body(ui, error="This public demo does not create projects. Run Ranksim locally to use your own data."), status=403)
        searches = split_searches(values["searches"])
        upload = form.get("csv_file")
        data = b""
        if isinstance(upload, UploadFile) and upload.filename:
            if not upload.filename.lower().endswith(".csv"):
                return shell(ui, "Projects", home_body(ui, error="The positions file must be a .csv file with the columns query, url, position.", values=values), status=400)
            data = await upload.read(ui.max_upload_bytes + 1)
            if len(data) > ui.max_upload_bytes:
                return shell(ui, "Projects", home_body(ui, error="That CSV is over 5 MB.", values=values), status=413)
            check = ws.check_csv(data)
            if not check.ok:
                detail = " ".join(check.errors[:4]) + (f" ({len(check.errors) - 4 + check.more_errors} more.)" if len(check.errors) > 4 or check.more_errors else "")
                return shell(ui, "Projects", home_body(ui, error=f"The CSV was not used, so no project was created. {detail}", values=values), status=400)
            if not searches:
                searches = check.queries[: serve.MAX_SEARCHES]
        if len(searches) > serve.MAX_SEARCHES:
            return shell(ui, "Projects", home_body(ui, error=f"At most {serve.MAX_SEARCHES} queries per project.", values=values), status=400)
        if not searches:
            return shell(ui, "Projects", home_body(ui, error="Add at least one query, or attach a CSV of positions to take the queries from.", values=values), status=400)
        try:
            made = create_project(values["name"], values["site"], searches, root=ui.root)
        except (ProjectError, ValueError) as exc:
            return shell(ui, "Projects", home_body(ui, error=str(exc), values=values), status=400)
        meta = ws.WorkspaceMeta()
        competitors, _problems = ws.parse_competitors(values["competitors"], made.domain)
        meta.competitors = competitors
        meta.dataset = ws.DATASET_NONE
        ws.save_meta(made, meta)
        if data:
            report = await run_in_threadpool(_import_bytes, ui, made, data, "", True)
            if isinstance(report, str):
                return RedirectResponse(f"/p/{made.slug}?err={quote(report[:300])}", status_code=303)
            return RedirectResponse(f"/p/{made.slug}?msg=imported-{report.rows}", status_code=303)
        return RedirectResponse(f"/p/{made.slug}", status_code=303)

    @app.get("/p/{slug}", response_class=HTMLResponse)
    def setup_page(slug: str, job: str = "", msg: str = "", err: str = "", q: str = "") -> Response:
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        running = ui.jobs.get(job) if job else ui.jobs.active(slug, "pull")
        if running is not None and running.slug != slug:
            running = None
        message = _message_text(msg)
        body = setup_body(ui, project, meta, running=running, message=message, error=err[:400], query=q if q in project.searches else "")
        return shell(ui, f"Setup: {project.name}", body, project=project, meta=meta, tab="setup", query=q if q in project.searches else "")

    @app.post("/p/{slug}/competitors")
    async def save_competitors(slug: str, request: Request) -> Response:
        form = await request.form()
        serve._check_csrf(ui, form)
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        if ui.public_demo:
            return shell(ui, "Setup", setup_body(ui, project, meta, error="This public demo is read-only."), project=project, meta=meta, tab="setup", status=403)
        found, problems = ws.parse_competitors(str(form.get("competitors") or ""), project.domain)
        meta.competitors = found
        ws.save_meta(project, meta)
        if problems:
            body = setup_body(ui, project, meta, message=f"Saved {len(found)} competitors.", competitor_problems=problems)
            return shell(ui, f"Setup: {project.name}", body, project=project, meta=meta, tab="setup")
        return RedirectResponse(f"/p/{slug}?msg=competitors-{len(found)}", status_code=303)

    @app.get("/p/{slug}/comparisons", response_class=HTMLResponse)
    def comparisons_page(slug: str, q: str = "", sel: str = "", sort: str = "") -> Response:
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        query = pick_query(project, q)
        ctx, error = context_or_error(slug)
        body = comparisons_body(ui, project, meta, ctx, query, sel, sort, load_error=error)
        return shell(ui, f"{query}: comparison", body, project=project, meta=meta, tab="comparisons", query=query)

    @app.get("/p/{slug}/draft", response_class=HTMLResponse)
    def draft_page(slug: str, q: str = "", from_: str = Query("", alias="from")) -> Response:
        values = None
        if from_:
            try:
                project = ui.project(slug)
            except LookupError:
                return serve._not_found(ui)
            values = draft_prefill(ui, project, from_)
            if values is not None:
                q = values["q"]
        return _draft_form_response(slug, q, values, from_run=bool(values))

    @app.get("/p/{slug}/draft/{run_id}", response_class=HTMLResponse)
    def draft_result(slug: str, run_id: str) -> Response:
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        run = ws.load_draft_run(project, run_id)
        if run is None:
            return shell(
                ui,
                "Draft test",
                _alert("This saved test was not found. It may have been removed, or this server keeps no durable storage.", "warn")
                + f'<p><a class="btn" href="/p/{e(slug)}/draft">Start a new draft test</a></p>',
                project=project,
                meta=meta,
                tab="draft",
                status=404,
            )
        return shell(ui, f"Draft test: {run['search']}", draft_result_body(ui, project, meta, run), project=project, meta=meta, tab="draft", query=run["search"])

    @app.get("/p/{slug}/draft/{run_id}/{name}.txt")
    def draft_source(slug: str, run_id: str, name: str) -> Response:
        try:
            project = ui.project(slug)
        except LookupError:
            return Response("Not found.", status_code=404, media_type="text/plain")
        text = ws.draft_html_of(project, run_id, name)
        if text is None:
            return Response("Not found.", status_code=404, media_type="text/plain")
        return Response(text, media_type="text/plain; charset=utf-8")

    @app.post("/p/{slug}/draft")
    async def draft_run(slug: str, request: Request) -> Response:
        try:
            form = await request.form(max_files=0, max_fields=20, max_part_size=MAX_DRAFT_BYTES + 1024)
        except Exception:  # noqa: BLE001
            return shell(ui, "Draft test", _alert("That form could not be read. Drafts are limited to 2 MB.", "bad"), status=400)
        serve._check_csrf(ui, form)
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        values = {key: str(form.get(key) or "") for key in ("q", "which", "url", "original_mode", "original_html", "draft_html")}
        query = pick_query(project, values["q"])
        if values["q"] not in project.searches:
            return _draft_form_response(slug, query, values, error="Pick a query from this project.", status=400)
        if len(values["draft_html"].encode("utf-8")) > MAX_DRAFT_BYTES or len(values["original_html"].encode("utf-8")) > MAX_DRAFT_BYTES:
            return _draft_form_response(slug, query, values, error="The draft or original is over 2 MB. Nothing was run.", status=413)
        if not values["draft_html"].strip():
            return _draft_form_response(slug, query, values, error="Paste the draft HTML or text. Nothing was run.", status=400)
        try:
            url = serve._clean_url(serve._with_scheme(values["url"] or f"https://{project.site}/new-page/"), what="page URL")
        except serve._InputError as exc:
            return _draft_form_response(slug, query, values, error=str(exc), status=400)
        ctx, error = context_or_error(slug)
        if ctx is None:
            return _draft_form_response(slug, query, values, error=error, status=409)
        original_html = None
        original_label = ""
        mode = values["original_mode"]
        if mode == "paste":
            if not values["original_html"].strip():
                return _draft_form_response(slug, query, values, error="'Paste the original' is selected but the original box is empty. Paste it, or choose another option.", status=400)
            original_html = values["original_html"]
            original_label = "pasted original"
        elif mode == "stored":
            from aeo.ranksim.tester import stored_html

            found = None
            try:
                found = stored_html(ui.database, url)
            except Exception:  # noqa: BLE001
                found = None
            if found is None:
                return _draft_form_response(slug, query, values, error="No stored copy of that URL was found. Paste the original instead.", status=400)
            original_html = found[0]
            original_label = f"stored copy of {found[1]} (stored {found[2]})"
        llm = ui.llm_factory() if (form.get("ai_notes") and ui.llm_ready()) else None
        notes = (lambda run: ws.ai_notes_text(llm, run)) if llm is not None else None
        try:
            run = await run_in_threadpool(
                lambda: ws.run_draft(
                    ctx, query, values["draft_html"], url, original_html=original_html, original_label=original_label,
                    which=values["which"] if values["which"] in {"all", "competitors"} else "all", meta=meta, ai_notes=notes,
                )
            )
        except ws.WorkspaceError as exc:
            return _draft_form_response(slug, query, values, error=str(exc), status=400)
        if not ui.llm_ready():
            run["ai"] = {"status": "unavailable", "text": f"No AI provider is configured on this server. Set {ws.AI_KEYS} to enable them."}
        run_id = await run_in_threadpool(ws.save_draft_run, project, run, original_html, values["draft_html"])
        return RedirectResponse(f"/p/{slug}/draft/{run_id}", status_code=303)

    def _draft_form_response(slug: str, q: str, values, *, error: str = "", status: int = 200, from_run: bool = False) -> Response:
        try:
            project, meta = load(slug)
        except LookupError:
            return serve._not_found(ui)
        query = pick_query(project, q)
        ctx, load_error = context_or_error(slug)
        note = "Loaded the saved draft and its original. Edit the draft and run again; the earlier run stays saved." if from_run else ""
        body = draft_body(ui, project, meta, ctx, query, values=values, error=error, load_error=load_error, note=note)
        return shell(ui, f"Draft test: {query}", body, project=project, meta=meta, tab="draft", query=query, status=status)


def draft_prefill(ui, project, run_id: str) -> dict | None:
    run = ws.load_draft_run(project, run_id)
    if run is None:
        return None
    values = {"q": run["search"], "which": run.get("which") or "all", "url": run["url"], "draft_html": ws.draft_html_of(project, run_id, "draft") or ""}
    original = ws.draft_html_of(project, run_id, "original")
    if original is not None:
        values["original_mode"] = "paste"
        values["original_html"] = original
    else:
        values["original_mode"] = "none"
    return values


def _import_bytes(ui, project, data: bytes, snapshot: str, add: bool):
    """Write a checked CSV under the project and import it. Returns the ImportReport or an error string."""
    from datetime import datetime, timezone

    from aeo.ranksim.project import PULL_DATE_NOT_RECORDED, ProjectError, import_serp_csv

    folder = project.dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}-positions.csv"
    path.write_bytes(data)
    when = f"positions seen {snapshot}" if snapshot else PULL_DATE_NOT_RECORDED
    try:
        report = import_serp_csv(project, path, snapshot or None, f"uploaded CSV ({when})", add_searches=add)
    except (ProjectError, ValueError, KeyError) as exc:
        return f"Import failed: {exc}"
    meta = ws.load_meta(project)
    if meta.dataset == ws.DATASET_NONE:
        meta.dataset = ws.DATASET_OBSERVED
        meta.dataset_label = f"uploaded CSV ({when})"
        ws.save_meta(project, meta)
    return report


def _message_text(msg: str) -> str:
    if msg == "demo":
        return "Synthetic demo built: 6 queries, invented .example sites. Open Comparisons to see observed and PREDICTED order side by side."
    if msg == "historical":
        return "Historical SmartCrew export loaded from the saved files. Pages without saved HTML stay missing."
    if msg == "searches":
        return "Queries saved."
    if msg.startswith("imported-"):
        count = msg.split("-", 1)[1]
        return f"Imported {count} observed positions. Next: fetch the candidate pages and build, then open Comparisons."
    if msg.startswith("competitors-"):
        return f"Saved {msg.split('-', 1)[1]} competitors."
    return ""
