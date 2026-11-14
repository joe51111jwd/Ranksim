"""The editor's API: a stored page, any live page, the comparison for one search, a draft test, and past tests.

Positions from the model are PREDICTED, never a Google rank.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import workspace as ws
from aeo.ranksim.api.common import _check_header_csrf, _engine_error, _error, _json_body, _not_public, _serve, _text

DRAFT_TOO_LARGE = "The draft is over 2 MB. Nothing was run."


def compare(ui, slug: str, query: str) -> dict:
    project = ui.project(slug)
    meta = ws.load_meta(project)
    if query not in project.searches:
        raise LookupError("That query is not in this project.")
    ctx = ui.context(slug)
    view = ws.compare_search(ctx, query, meta)
    data = asdict(view)
    data["pulled"] = view.pulled or ""
    return data


def page_html(ui, slug: str, url: str) -> dict | None:
    """Stored HTML for one candidate or one of your own pages, for the preview and the editor."""
    from aeo.ranksim.project import latest_google, loose_key

    project = ui.project(slug)
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")) or len(url) > 2048:
        return None
    google = latest_google(project)
    known = set()
    if google is not None:
        for entry in google.searches:
            known.update(loose_key(item.url) for item in entry.results)
    if loose_key(url) not in known and not project.is_our_url(url):
        return None  # only pages this project already lists
    if ui.public_demo:
        html = ws.synthetic_page(url)
        return None if html is None else {"url": url, "html": html, "stored": "synthetic fixture"}
    from aeo.ranksim.tester import stored_html

    try:
        found = stored_html(ui.database, url)
    except Exception:  # noqa: BLE001 - no database yet means nothing stored
        found = None
    if found is None and ws.synthetic_page(url) is not None and project.slug == ws.DEMO_SLUG:
        return {"url": url, "html": ws.synthetic_page(url), "stored": "synthetic fixture"}
    if found is None:
        return None
    return {"url": found[1], "html": found[0], "stored": found[2]}


def urlsplit_host(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").lower()


def register(app: FastAPI, ui) -> None:
    from aeo.ranksim.studio import MAX_DRAFT_BYTES

    serve = _serve()

    @app.get("/api/v1/projects/{slug}/compare")
    async def api_compare(slug: str, q: str = "") -> JSONResponse:
        from aeo.ranksim.project import ProjectError

        try:
            data = await run_in_threadpool(compare, ui, slug, q)
        except LookupError as exc:
            return _error(404, str(exc) if "query" in str(exc) else "No such project.")
        except ProjectError as exc:
            return _error(409, str(exc))
        except Exception as exc:  # noqa: BLE001
            return _engine_error(ui, exc)
        return JSONResponse(data)

    @app.get("/api/v1/projects/{slug}/page")
    async def api_page(slug: str, url: str = "") -> JSONResponse:
        try:
            found = await run_in_threadpool(page_html, ui, slug, url)
        except LookupError:
            return _error(404, "No such project.")
        if found is None:
            return _error(404, "No stored copy of that page.")
        return JSONResponse(found)

    @app.post("/api/v1/projects/{slug}/test")
    async def api_test(slug: str, request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        body = await _json_body(request, 2 * MAX_DRAFT_BYTES + 64 * 1024, too_large=DRAFT_TOO_LARGE)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        meta = ws.load_meta(project)
        query = _text(body.get("q"), 500)
        draft = _text(body.get("draft"), MAX_DRAFT_BYTES + 1)
        original_mode = _text(body.get("original"), 20) or "none"
        original_text = _text(body.get("original_html"), MAX_DRAFT_BYTES + 1)
        which = "competitors" if body.get("which") == "competitors" else "all"
        if query not in project.searches:
            return _error(400, "Pick a query from this project.")
        if len(draft.encode("utf-8")) > MAX_DRAFT_BYTES or len(original_text.encode("utf-8")) > MAX_DRAFT_BYTES:
            return _error(413, DRAFT_TOO_LARGE)
        if not draft.strip():
            return _error(400, "Write or paste a draft first. Nothing was run.")
        from aeo.ranksim.project import ProjectError

        try:
            ctx = await run_in_threadpool(ui.context, slug)
        except ProjectError as exc:
            return _error(409, f"{exc} Finish setup on the classic screens first.")
        except Exception as exc:  # noqa: BLE001
            return _engine_error(ui, exc)
        typed = _text(body.get("url"), 2048).strip() or ws.your_url(ctx, query, meta) or f"https://{project.site}/new-page/"
        try:
            url = serve._clean_url(serve._with_scheme(typed), what="page URL")
        except serve._InputError as exc:
            return _error(400, str(exc))
        original_html = None
        original_label = ""
        if original_mode == "paste" and original_text.strip():
            original_html, original_label = original_text, "pasted original"
        elif original_mode == "current" and original_text.strip():
            # The stored copy as it was loaded into the editor, so an unchanged draft scores exactly the same.
            original_html, original_label = original_text, "your current page, as loaded in the editor"
        elif original_mode == "stored":
            found = await run_in_threadpool(page_html, ui, slug, url)
            if found is not None:
                original_html, original_label = found["html"], f"stored copy of {found['url']} ({found['stored']})"
        llm = ui.llm_factory() if (body.get("ai") and ui.llm_ready()) else None
        notes = (lambda run: ws.ai_notes_text(llm, run)) if llm is not None else None
        try:
            run = await run_in_threadpool(
                lambda: ws.run_draft(
                    ctx, query, draft, url, original_html=original_html, original_label=original_label, which=which, meta=meta, ai_notes=notes
                )
            )
        except ws.WorkspaceError as exc:
            return _error(400, str(exc))
        run["predict"] = await run_in_threadpool(_predict_block, ctx, draft, url, original_html, query, body.get("searches"))
        for key in ("topic_after", "topic_before"):
            if isinstance(run.get(key), dict):
                run[key] = dict(run[key], text=ws._observed_text(run[key].get("text") or ""))  # observed pages, not "Google ranks"
        run["saved"] = False
        if not ui.public_demo:
            run["id"] = await run_in_threadpool(ws.save_draft_run, project, run, original_html, draft, ui.database)
            run["saved"] = True
        return JSONResponse(run)

    @app.post("/api/v1/projects/{slug}/fetch")
    async def api_fetch(slug: str, request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        try:
            ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        try:
            url = serve._clean_url(serve._with_scheme(_text(body.get("url"), 2048).strip()), what="page URL")
        except serve._InputError as exc:
            return _error(400, str(exc))
        html, error = await run_in_threadpool(ui.preview_fetch, url)
        if not html:
            return _error(400, ui.scrub(f"Could not fetch {url}: {error or 'no HTML came back'}. Nothing was loaded."))
        return JSONResponse({"url": url, "html": html})

    @app.get("/api/v1/projects/{slug}/tests")
    async def api_tests(slug: str) -> JSONResponse:
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        if ui.public_demo:
            return JSONResponse({"tests": []})
        runs = await run_in_threadpool(ws.list_draft_runs, project, 30)
        rows = []
        for run in runs:
            before = (run.get("before") or {}).get("position")
            after = (run.get("after") or {}).get("position")
            rows.append(
                {
                    "id": run.get("id"),
                    "q": run.get("search"),
                    "url": run.get("url"),
                    "created": run.get("created"),
                    "before": before,
                    "after": after,
                    "of": (run.get("after") or {}).get("of"),
                    "change": run.get("change"),
                    "title": (run.get("facts_after") or {}).get("title", ""),
                }
            )
        return JSONResponse({"tests": rows})

    @app.get("/api/v1/projects/{slug}/tests/{run_id}")
    async def api_test_run(slug: str, run_id: str) -> JSONResponse:
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        if ui.public_demo or not ws.DRAFT_ID_RE.match(run_id or ""):
            return _error(404, "No such test.")
        run = ws.load_draft_run(project, run_id)
        if run is None:
            return _error(404, "No such test.")
        run["draft_html"] = ws.draft_html_of(project, run_id, "draft") or ""
        run["original_html"] = ws.draft_html_of(project, run_id, "original") or ""
        return JSONResponse(run)

    @app.post("/api/v1/fetch")
    async def api_fetch_any(request: Request) -> JSONResponse:
        """Any public page, for the live preview. robots.txt respected, private addresses refused, nothing stored."""
        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        try:
            url = serve._clean_url(serve._with_scheme(_text(body.get("url"), 2048).strip()), what="page address")
        except serve._InputError as exc:
            return _error(400, str(exc))
        html, error = await run_in_threadpool(ui.preview_fetch, url)
        if not html:
            return _error(400, ui.scrub(f"Could not open {url}: {error or 'no page came back'}."))
        from aeo.ranksim.project import list_projects

        domain = (urlsplit_host(url) or "").removeprefix("www.")
        owner = None
        for row in list_projects(root=ui.root):
            if row.site.removeprefix("www.") == domain:
                owner = row.slug
                break
        return JSONResponse({"url": url, "html": html, "project": owner})


def _predict_block(ctx, draft: str, url: str, original_html: str | None, query: str, searches) -> dict:
    """Every target search in one call (predict workstream). A failure here never breaks the main test."""
    from aeo.ranksim import predict_service

    wanted = None
    if isinstance(searches, list):
        wanted = [str(item)[:500] for item in searches[: predict_service.MAX_SEARCHES] if isinstance(item, str)]
    try:
        return predict_service.test_searches(ctx, ws._as_html(draft), url, searches=wanted or None, original_html=ws._as_html(original_html) if original_html else None, first=query)
    except predict_service.PredictError as exc:
        return {"label": "PREDICTED", "searches": [], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"label": "PREDICTED", "searches": [], "error": f"The per-search check could not run: {type(exc).__name__}."}

