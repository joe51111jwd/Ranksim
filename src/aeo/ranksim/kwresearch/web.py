"""Minimal keyword page and JSON endpoints for the ranksim web app."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aeo.ranksim.kwresearch.providers import PaidConfig
from aeo.ranksim.kwresearch.service import (
    import_volumes,
    keyword_difficulty,
    keyword_report,
    keyword_volume,
    resolve_cache,
)


class KeywordBody(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    project: str | None = None
    refresh: bool = False
    credit_cap: int = 50


def register(app: FastAPI, ui) -> None:
    from aeo.ranksim.serve import _check_csrf, _csrf_input, e

    def cache_for(project: str | None) -> str:
        return str(resolve_cache(project or None, None, ui.root))

    def blocked():
        from aeo.ranksim.workspace_ui import shell

        body = '<section class="panel"><div class="panel-body"><p>Run Ranksim locally to look up keywords.</p></div></section>'
        return shell(ui, "Keywords", body, crumbs=[("Keywords", None)], status=403)

    @app.get("/keywords")
    def keywords_page(project: str = "") -> object:
        from aeo.ranksim.workspace_ui import shell

        if ui.public_demo:
            return blocked()
        return shell(ui, "Keywords", _form(ui, project, "", _csrf_input(ui)), crumbs=[("Keywords", None)])

    @app.post("/keywords")
    async def keywords_post(request: Request) -> object:
        from aeo.ranksim.workspace_ui import shell

        if ui.public_demo:
            return blocked()
        form = await request.form()
        _check_csrf(ui, form)
        action = str(form.get("action") or "volume")
        project = str(form.get("project") or "").strip()
        text = str(form.get("keywords") or "")
        refresh = str(form.get("refresh") or "") == "1"
        keywords = _lines(text)
        message = ""
        table = ""
        if not keywords:
            message = "Add at least one keyword."
        else:
            cache = cache_for(project or None)
            try:
                if action == "difficulty":
                    rows = keyword_difficulty(
                        keywords,
                        cache,
                        refresh=refresh,
                        api_key=ui.settings.serper_api_key,
                        credit_cap=50,
                        log=lambda line: None,
                    )
                    table = _difficulty_table(rows)
                elif action == "report":
                    rows = keyword_report(keywords, cache, refresh=refresh, paid=PaidConfig.from_env(), api_key=ui.settings.serper_api_key)
                    table = _report_table(rows)
                else:
                    rows = keyword_volume(keywords, cache, refresh=refresh, paid=PaidConfig.from_env(), delay=1.0)
                    table = _volume_table(rows)
            except Exception as exc:  # noqa: BLE001 - shown without secrets
                message = ui.scrub(f"{type(exc).__name__}")
        page = _form(ui, project, text, _csrf_input(ui))
        if message:
            page += f'<p class="muted">{e(message)}</p>'
        page += table
        return shell(ui, "Keywords", page, crumbs=[("Keywords", None)])

    @app.post("/keywords/import")
    async def keywords_import(request: Request) -> object:
        from aeo.ranksim.workspace_ui import shell

        if ui.public_demo:
            return blocked()
        form = await request.form()
        _check_csrf(ui, form)
        project = str(form.get("project") or "").strip()
        upload = form.get("csv")
        message = "Choose a CSV."
        if upload is not None and hasattr(upload, "read"):
            data = await upload.read()
            path = ui.root / "_keywords" / "import.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            try:
                stored, blanks = import_volumes(path, cache_for(project or None))
                message = f"Imported {stored}. {blanks} without a volume."
            except ValueError as exc:
                message = str(exc)
        page = _form(ui, project, "", _csrf_input(ui)) + f'<p>{e(message)}</p>'
        return shell(ui, "Keywords", page, crumbs=[("Keywords", None)])

    @app.post("/api/keywords/volume")
    def api_volume(body: KeywordBody) -> JSONResponse:
        if ui.public_demo:
            return JSONResponse({"error": "not available"}, status_code=403)
        rows = keyword_volume(body.keywords, cache_for(body.project), refresh=body.refresh, paid=PaidConfig.from_env())
        return JSONResponse({"keywords": [row.to_dict() for row in rows], "note": "Blank means no source returned a number."})

    @app.post("/api/keywords/difficulty")
    def api_difficulty(body: KeywordBody) -> JSONResponse:
        if ui.public_demo:
            return JSONResponse({"error": "not available"}, status_code=403)
        rows = keyword_difficulty(
            body.keywords,
            cache_for(body.project),
            refresh=body.refresh,
            api_key=ui.settings.serper_api_key,
            credit_cap=body.credit_cap,
            log=lambda line: None,
        )
        return JSONResponse({
            "keywords": [row.to_dict() for row in rows],
            "note": "Heuristic score, not a Google prediction.",
        })

    @app.post("/api/keywords/report")
    def api_report(body: KeywordBody) -> JSONResponse:
        if ui.public_demo:
            return JSONResponse({"error": "not available"}, status_code=403)
        rows = keyword_report(body.keywords, cache_for(body.project), refresh=body.refresh, paid=PaidConfig.from_env())
        return JSONResponse({"keywords": rows, "note": "Heuristic difficulty is not a Google prediction. Blank volume means no source returned a number."})


def _lines(text: str) -> list[str]:
    rows = []
    seen = set()
    for line in text.splitlines():
        item = " ".join(line.split())
        key = item.lower()
        if item and not item.startswith("#") and key not in seen:
            seen.add(key)
            rows.append(item)
    return rows


def _form(ui, project: str, keywords: str, csrf: str) -> str:
    from aeo.ranksim.serve import e

    return (
        '<section class="panel"><div class="panel-head"><h2>Keywords</h2></div><div class="panel-body">'
        f'<form method="post" action="/keywords">{csrf}'
        f'<div class="field"><label for="kw">Keywords</label><textarea id="kw" name="keywords" rows="6">{e(keywords)}</textarea></div>'
        f'<div class="field"><label for="kw-project">Project</label><input id="kw-project" name="project" value="{e(project)}"></div>'
        '<label class="small"><input type="checkbox" name="refresh" value="1"> Refresh</label>'
        '<div class="head-actions" style="margin-top:12px">'
        '<button class="btn btn-primary" name="action" value="volume">Volume</button>'
        '<button class="btn" name="action" value="report">Report</button>'
        '<button class="btn" name="action" value="difficulty">Difficulty</button>'
        '</div><p class="small muted">Difficulty uses 1 Serper credit per uncached keyword. The score is a heuristic, not a Google prediction.</p>'
        '</form>'
        f'<form method="post" action="/keywords/import" enctype="multipart/form-data">{csrf}'
        f'<div class="field"><label for="kw-import-project">Project</label><input id="kw-import-project" name="project" value="{e(project)}"></div>'
        '<div class="field"><label for="kw-csv">Import CSV</label><input id="kw-csv" type="file" name="csv" accept=".csv,text/csv"></div>'
        '<button class="btn">Import</button></form>'
        '</div></section>'
    )


def _volume_table(rows) -> str:
    from aeo.ranksim.serve import e

    body = []
    for row in rows:
        volume = row.volume_display or ""
        body.append(
            "<tr>"
            f"<td>{e(row.keyword)}</td>"
            f'<td class="num">{e(volume)}</td>'
            f"<td>{e(row.provider)}</td>"
            f"<td>{e(row.data_date)}</td>"
            f"<td>{e(row.blank_reason)}</td>"
            "</tr>"
        )
    return _table("Volume", ("Keyword", "Volume", "Source", "Data date", "Blank reason"), body)


def _report_table(rows) -> str:
    from aeo.ranksim.serve import e

    body = []
    for row in rows:
        difficulty = "" if row.get("difficulty") is None else str(row.get("difficulty"))
        body.append(
            "<tr>"
            f"<td>{e(row.get('keyword') or '')}</td>"
            f"<td class=\"num\">{e(row.get('volume_display') or '')}</td>"
            f"<td class=\"num\">{e(difficulty)}</td>"
            f"<td>{e(row.get('provider') or '')}</td>"
            f"<td>{e(row.get('data_date') or '')}</td>"
            "</tr>"
        )
    return _table("Report", ("Keyword", "Volume", "Difficulty", "Source", "Data date"), body)


def _difficulty_table(rows) -> str:
    from aeo.ranksim.serve import e

    body = []
    for row in rows:
        score = "" if row.score is None else str(row.score)
        body.append(
            "<tr>"
            f"<td>{e(row.keyword)}</td>"
            f'<td class="num">{e(score)}</td>'
            f"<td class=\"num\">{e(f'{row.coverage:.2f}')}</td>"
            f"<td>{e(row.label)}</td>"
            "</tr>"
        )
    return _table("Difficulty", ("Keyword", "Score", "Coverage", "Label"), body)


def _table(title: str, headers: tuple[str, ...], body: list[str]) -> str:
    from aeo.ranksim.serve import e

    heads = "".join(f"<th>{e(item)}</th>" for item in headers)
    return (
        f'<section class="panel"><div class="panel-head"><h2>{e(title)}</h2></div>'
        f'<div class="table-wrap"><table class="table"><thead><tr>{heads}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></section>'
    )
