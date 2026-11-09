"""Ranksim Studio: the web app at / and the JSON API it reads.

The Studio is one page (static/studio.html, .css, .js) built around the product's main idea: edit a page, preview it,
and see where it is PREDICTED to land among the candidate pages for a query before it is published.

Nothing here changes the ranker. Every number comes from the same calls the classic screens use
(workspace.compare_search, workspace.run_draft). Positions from the model are PREDICTED, never a Google rank.
On a public host (public_demo) the API serves only the synthetic demo, never saves a draft, and never calls a provider.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import workspace as ws

STATIC = Path(__file__).resolve().parent / "static"
MAX_DRAFT_BYTES = 2 * 1024 * 1024
CSRF_HEADER = "x-ranksim-csrf"
ASSETS = {"studio.css": "text/css; charset=utf-8", "studio.js": "application/javascript; charset=utf-8"}


def _serve():
    from aeo.ranksim import serve

    return serve


def _shell(ui) -> str:
    """The app shell with this process's CSRF token. All content is rendered by studio.js from the API."""
    from html import escape

    page = (STATIC / "studio.html").read_text(encoding="utf-8")
    return (
        page.replace("{{csrf}}", escape(ui.csrf, quote=True))
        .replace("{{mode}}", "public" if ui.public_demo else "local")
        .replace("{{version}}", _asset_version())
    )


def _asset_version() -> str:
    """Changes whenever the CSS or JS changes, so a browser never mixes an old script with a new shell."""
    import hashlib

    digest = hashlib.sha256()
    for name in sorted(ASSETS):
        digest.update((STATIC / name).read_bytes())
    return digest.hexdigest()[:10]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _check_header_csrf(ui, request: Request) -> None:
    token = request.headers.get(CSRF_HEADER, "")
    if not token or not secrets.compare_digest(token, ui.csrf):
        raise HTTPException(status_code=403, detail="This page has expired. Reload it and try again.")


async def _json_body(request: Request, limit: int) -> dict:
    if "application/json" not in (request.headers.get("content-type") or ""):
        raise HTTPException(status_code=415, detail="Send JSON.")
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="The draft is over 2 MB. Nothing was run.")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="That request could not be read.") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="That request could not be read.")
    return data


def _text(value, limit: int = 4000) -> str:
    return str(value if value is not None else "")[:limit]


def project_list(ui) -> list[dict]:
    from aeo.ranksim.project import list_projects

    try:
        rows = list_projects(root=ui.root)
    except Exception:  # noqa: BLE001 - a broken project folder must not take the app down
        rows = []
    if ui.public_demo:
        rows = [row for row in rows if row.slug == ws.DEMO_SLUG]
    out = []
    for row in rows:
        try:
            meta = ws.load_meta(ui.project(row.slug))
        except LookupError:
            continue
        out.append(
            {
                "slug": row.slug,
                "name": row.name,
                "site": row.site,
                "queries": row.searches,
                "built": bool(row.has_engine),
                "dataset": meta.dataset,
                "dataset_label": meta.dataset_label,
            }
        )
    return out


def _dataset_info(meta: ws.WorkspaceMeta) -> dict:
    labels = {
        ws.DATASET_SYNTHETIC: ("Demo data", "Invented .example sites and positions. Not Google results."),
        ws.DATASET_HISTORICAL: ("Historical export", meta.dataset_label or ws.HISTORICAL_SOURCE),
        ws.DATASET_OBSERVED: ("Observed positions", meta.dataset_label or "Positions from an imported file or provider."),
        ws.DATASET_NONE: ("No positions yet", "Import positions on the classic Setup screen."),
    }
    short, long = labels.get(meta.dataset, labels[ws.DATASET_NONE])
    return {"kind": meta.dataset, "short": short, "long": long}


def project_overview(ui, slug: str) -> dict:
    project = ui.project(slug)
    meta = ws.load_meta(project)
    ready, reason = ws.engine_state(project)
    data = {
        "slug": project.slug,
        "name": project.name,
        "site": project.site,
        "domain": project.domain,
        "data": _dataset_info(meta),
        "competitors": list(meta.competitors),
        "public_demo": ui.public_demo,
        "ai": ui.llm_ready(),
        "engine": {"ready": ready, "reason": reason},
        "model": None,
        "queries": [{"q": search, "intent": project.intents.get(search, "")} for search in project.searches],
        "error": "",
    }
    if not ready:
        return data
    from aeo.ranksim.project import ProjectError

    try:
        ctx = ui.context(slug)
    except ProjectError as exc:
        data["error"] = str(exc)
        return data
    metrics = ctx.metrics
    data["model"] = {
        "pairwise": metrics.pairwise,
        "ndcg": metrics.ndcg,
        "queries": metrics.n_searches,
        "pairs": metrics.n_pairs,
        "calibration": ctx.fit.calibration.note,
        "kind": "LambdaMART, each query held out",
    }
    rows = []
    for search in project.searches:
        view = ws.compare_search(ctx, search, meta)
        you = next((cand for cand in view.candidates if cand.url == view.your_url), None)
        rows.append(
            {
                "q": search,
                "intent": view.intent,
                "candidates": view.total,
                "scored": view.scored,
                "your_url": view.your_url or ws.your_url(ctx, search, meta),
                "your_title": you.title if you else "",
                "observed": view.your_observed,
                "predicted": view.your_predicted,
                "of": view.scored,
                "confidence": view.confidence,
                "pairwise": view.query_pairwise,
                "pairs": view.query_pairs,
                "error": view.error,
            }
        )
    data["queries"] = rows
    return data


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


def register(app: FastAPI, ui) -> None:
    serve = _serve()

    def shell_response() -> HTMLResponse:
        return HTMLResponse(_shell(ui))

    @app.get("/", response_class=HTMLResponse)
    def studio_home(demo: int = 0) -> Response:
        if demo:
            return RedirectResponse("/demo?demo=1", status_code=303)  # the old SmartCrew demo link
        return shell_response()

    @app.get("/app", response_class=HTMLResponse)
    def studio_root() -> Response:
        return shell_response()

    @app.get("/app/{slug}", response_class=HTMLResponse)
    def studio_project(slug: str) -> Response:
        return shell_response()

    @app.get("/app/{slug}/{screen}", response_class=HTMLResponse)
    def studio_screen(slug: str, screen: str) -> Response:
        if screen not in {"studio", "rankings"}:
            return serve._not_found(ui)
        return shell_response()

    @app.get("/studio/{name}")
    def studio_asset(name: str) -> Response:
        media = ASSETS.get(name)
        if media is None:
            return Response("Not found.", status_code=404, media_type="text/plain")
        return FileResponse(STATIC / name, media_type=media, headers={"cache-control": "no-cache"})

    # -- JSON API --------------------------------------------------------------

    @app.get("/api/v1/projects")
    def api_projects() -> JSONResponse:
        return JSONResponse({"public_demo": ui.public_demo, "projects": project_list(ui), "demo_slug": ws.DEMO_SLUG})

    @app.get("/api/v1/projects/{slug}")
    async def api_project(slug: str) -> JSONResponse:
        try:
            data = await run_in_threadpool(project_overview, ui, slug)
        except LookupError:
            return _error(404, "No such project.")
        except Exception as exc:  # noqa: BLE001 - shown, scrubbed
            return _error(500, ui.scrub(f"The engine could not be loaded: {type(exc).__name__}: {str(exc)[:200]}"))
        return JSONResponse(data)

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
            return _error(500, ui.scrub(f"The engine could not be loaded: {type(exc).__name__}: {str(exc)[:200]}"))
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
        body = await _json_body(request, 2 * MAX_DRAFT_BYTES + 64 * 1024)
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
            return _error(413, "The draft is over 2 MB. Nothing was run.")
        if not draft.strip():
            return _error(400, "Write or paste a draft first. Nothing was run.")
        from aeo.ranksim.project import ProjectError

        try:
            ctx = await run_in_threadpool(ui.context, slug)
        except ProjectError as exc:
            return _error(409, f"{exc} Finish setup on the classic screens first.")
        except Exception as exc:  # noqa: BLE001
            return _error(500, ui.scrub(f"The engine could not be loaded: {type(exc).__name__}: {str(exc)[:200]}"))
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
        for key in ("topic_after", "topic_before"):
            if isinstance(run.get(key), dict):
                run[key] = dict(run[key], text=ws._observed_text(run[key].get("text") or ""))  # observed pages, not "Google ranks"
        run["saved"] = False
        if not ui.public_demo:
            run["id"] = await run_in_threadpool(ws.save_draft_run, project, run, original_html, draft, ui.database)
            run["saved"] = True
        return JSONResponse(run)

    @app.post("/api/v1/demo")
    async def api_demo(request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        slug = ws.DEMO_SLUG
        if ws.demo_ready(ui.root):
            return JSONResponse({"slug": slug, "built": False})
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return _error(409, "The demo is being built right now. Try again in a few seconds.")
        try:
            await run_in_threadpool(ws.load_synthetic_demo, ui.root, ui.settings, ui.database)
        except Exception as exc:  # noqa: BLE001
            return _error(500, ui.scrub(f"The demo could not be built: {type(exc).__name__}: {str(exc)[:200]}"))
        finally:
            lock.release()
        ui.forget(slug)
        return JSONResponse({"slug": slug, "built": True})

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)
