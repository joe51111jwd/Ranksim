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
ASSETS = {
    "studio.css": "text/css; charset=utf-8",
    "studio.js": "application/javascript; charset=utf-8",
    "sans.woff2": "font/woff2",
    "mono.woff2": "font/woff2",
}
VERSIONED = ("studio.css", "studio.js")
SCREENS = {"studio", "rankings", "data", "overview", "issues", "searches", "competitors", "editor", "settings"}
MAX_CSV_BYTES = 5 * 1024 * 1024
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
# Paths that answer without a login: the app shell (it holds no data), its assets, and the login itself.
OPEN_EXACT = {"/", "/app", "/start", "/healthz", "/favicon.ico", "/api/v1/session", "/api/v1/login", "/api/v1/logout"}
OPEN_PREFIX = ("/studio/", "/app/")
NO_STATE_PREFIX = ("/studio/", "/static/")
NO_SAVE_SUFFIX = ("/pull", "/fetch", "/login", "/logout")


def _serve():
    from aeo.ranksim import serve

    return serve


def _shell(ui) -> str:
    """The app shell with this process's CSRF token. All content is rendered by studio.js from the API."""
    from html import escape

    page = (STATIC / "studio.html").read_text(encoding="utf-8")
    return (
        page.replace("{{csrf}}", escape(ui.csrf, quote=True))
        .replace("{{mode}}", mode_of(ui))
        .replace("{{version}}", _asset_version())
    )


def _asset_version() -> str:
    """Changes whenever the CSS or JS changes, so a browser never mixes an old script with a new shell."""
    import hashlib

    digest = hashlib.sha256()
    for name in VERSIONED:
        digest.update((STATIC / name).read_bytes())
    return digest.hexdigest()[:10]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def mode_of(ui) -> str:
    """public: the anonymous synthetic demo. private: a hosted Ranksim behind a password. local: this computer."""
    if ui.public_demo:
        return "public"
    access = getattr(ui, "access", None)
    return "private" if access is not None and access.enabled else "local"


def google_status(ui) -> dict:
    """Which live Google source is configured. Key values never leave the server."""
    from aeo.ranksim.project import GOOGLE_KEYS

    settings = ui.settings
    provider = None
    if not ui.public_demo:
        if settings.serpapi_key:
            provider = "SerpAPI"
        elif settings.serper_api_key:
            provider = "Serper"
        elif settings.dataforseo_login and settings.dataforseo_password:
            provider = "DataForSEO"
    return {"ready": provider is not None, "provider": provider, "keys": GOOGLE_KEYS}


def storage_kind(ui) -> str:
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        return "cloud"
    return "temporary" if ui.public_demo else "disk"


def _signed_in(ui, request: Request) -> bool:
    from aeo.ranksim.access import COOKIE

    return ui.access.valid(request.cookies.get(COOKIE))


def _is_open(path: str) -> bool:
    return path in OPEN_EXACT or path.startswith(OPEN_PREFIX)


def _not_public(ui) -> JSONResponse | None:
    if ui.public_demo:
        return _error(403, "The public demo is read only. Run your own Ranksim to use real projects.")
    return None


def _split(value) -> list[str]:
    from aeo.ranksim.project import split_searches

    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return split_searches(str(value or ""))


def _pull_summary(report) -> dict:
    metrics = report.metrics
    return {
        "pull_id": report.pull_id,
        "seconds": report.total_seconds,
        "google_kind": report.google_kind,
        "google_note": report.google_note,
        "google_source": report.google_source,
        "searches": report.searches_total,
        "searches_with_results": report.searches_with_results,
        "counts": dict(report.counts or {}),
        # The save step's detail names a server folder; it never leaves the server.
        "steps": [{"step": step.step, "seconds": step.seconds, "detail": "" if step.step == "save" else step.detail} for step in report.all_steps()],
        "pairwise": getattr(metrics, "pairwise", None),
        "queries_scored": getattr(metrics, "n_searches", None),
        "pairs": getattr(metrics, "n_pairs", None),
        "notes": list(report.notes or []),
    }


def project_setup(ui, slug: str) -> dict:
    """Everything the Data screen shows: per-query coverage, the pulls so far, and whether the engine is built."""
    from aeo.ranksim.project import list_pulls, load_pull_report

    project = ui.project(slug)
    meta = ws.load_meta(project)
    ready, reason = ws.engine_state(project)
    rows = []
    for item in ws.coverage(project, ui.database, meta):
        rows.append(
            {
                "q": item.search,
                "candidates": item.candidates,
                "stored": item.stored,
                "missing": item.missing[:12],
                "yours": item.yours,
                "competitors": item.competitors,
                "source": item.source,
                "pulled": item.pulled,
                "error": item.error,
            }
        )
    covered = {row["q"] for row in rows}
    rows.extend({"q": q, "candidates": 0, "stored": 0, "missing": [], "yours": None, "competitors": [], "source": "", "pulled": "", "error": "no results yet"} for q in project.searches if q not in covered)
    pulls = [
        {
            "id": info.pull_id,
            "when": info.written_at,
            "kind": info.google_kind or info.kind,
            "with_results": info.searches_with_results,
            "engine": info.has_engine,
            "pairwise": info.pairwise,
            "seconds": info.total_seconds,
        }
        for info in list_pulls(project)
        if info.finished
    ][-10:]
    last = load_pull_report(project)
    return {
        "slug": project.slug,
        "name": project.name,
        "site": project.site,
        "queries": rows,
        "competitors": list(meta.competitors),
        "data": _dataset_info(meta),
        "engine": {"ready": ready, "reason": reason},
        "pulls": list(reversed(pulls)),
        "last_pull": _pull_summary(last) if last is not None else None,
        "google": google_status(ui),
        "storage": storage_kind(ui),
        "read_only": ui.public_demo,
    }


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
    if not hasattr(ui, "access"):
        from aeo.ranksim.access import Access

        ui.access = Access(None)
    if not hasattr(ui, "cloud"):
        ui.cloud = None

    def shell_response() -> HTMLResponse:
        return HTMLResponse(_shell(ui))

    # -- storage sync and login (the login check is added last, so it runs first) ----------------

    @app.middleware("http")
    async def _sync_state(request: Request, call_next):
        cloud = ui.cloud
        path = request.url.path
        if cloud is None or not cloud.enabled or path.startswith(NO_STATE_PREFIX) or path in OPEN_EXACT:
            return await call_next(request)
        from aeo.ranksim.cloud import CloudError

        writing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        try:
            await run_in_threadpool(lambda: cloud.refresh(force=writing))
        except CloudError as exc:
            if path.startswith("/api/"):
                return _error(503, f"{exc} Nothing was changed. Try again in a moment.")
            return Response(f"{exc} Try again in a moment.", status_code=503, media_type="text/plain")
        response = await call_next(request)
        if writing and response.status_code < 400 and not path.endswith(NO_SAVE_SUFFIX):
            try:
                await run_in_threadpool(cloud.save)
            except CloudError as exc:
                print(f"ranksim: {exc}", flush=True)
                if path.startswith("/api/"):
                    return _error(503, f"{exc} The change was made on this server but may not last. Try again.")
        return response

    @app.middleware("http")
    async def _require_login(request: Request, call_next):
        if ui.access.enabled and not _is_open(request.url.path) and not _signed_in(ui, request):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"error": "Sign in first.", "login": True}, status_code=401)
            return RedirectResponse("/", status_code=303)
        return await call_next(request)

    # -- session -------------------------------------------------------------------------------

    @app.get("/api/v1/session")
    def api_session(request: Request) -> JSONResponse:
        signed_in = _signed_in(ui, request)
        data = {"mode": mode_of(ui), "login_required": ui.access.enabled, "signed_in": signed_in}
        if signed_in:
            data.update({"google": google_status(ui), "ai": ui.llm_ready(), "storage": storage_kind(ui)})
        return JSONResponse(data)

    @app.post("/api/v1/login")
    async def api_login(request: Request) -> JSONResponse:
        from aeo.ranksim.access import COOKIE, MAX_AGE

        _check_header_csrf(ui, request)
        body = await _json_body(request, 8192)
        if not ui.access.enabled:
            return JSONResponse({"ok": True})
        if ui.access.locked_out():
            return _error(429, "Too many tries. Wait a minute and try again.")
        if not await run_in_threadpool(ui.access.check_password, _text(body.get("password"), 500)):
            return _error(401, "That password isn't right.")
        response = JSONResponse({"ok": True})
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, ui.access.issue(), max_age=MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")
        return response

    @app.post("/api/v1/logout")
    async def api_logout(request: Request) -> JSONResponse:
        from aeo.ranksim.access import COOKIE

        _check_header_csrf(ui, request)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/", response_class=HTMLResponse)
    def studio_home(demo: int = 0) -> Response:
        if demo:
            return RedirectResponse("/demo?demo=1", status_code=303)  # the old SmartCrew demo link
        return shell_response()

    @app.get("/app", response_class=HTMLResponse)
    def studio_root() -> Response:
        return shell_response()

    @app.get("/start", response_class=HTMLResponse)
    def studio_start() -> Response:
        return shell_response()

    @app.get("/app/{slug}", response_class=HTMLResponse)
    def studio_project(slug: str) -> Response:
        return shell_response()

    @app.get("/app/{slug}/{screen}", response_class=HTMLResponse)
    def studio_screen(slug: str, screen: str) -> Response:
        if screen not in SCREENS:
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

    # -- projects: create, settings, import, pull, fetch, delete, history ---------------------------

    @app.post("/api/v1/projects")
    async def api_create(request: Request) -> JSONResponse:
        from aeo.ranksim.project import ProjectError, create_project

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 256 * 1024)
        searches = _split(body.get("queries"))
        if not searches:
            return _error(400, "Add at least one search query.")
        if len(searches) > serve.MAX_SEARCHES:
            return _error(400, f"At most {serve.MAX_SEARCHES} queries per project.")
        try:
            made = create_project(_text(body.get("name"), 200), _text(body.get("site"), 300), searches, root=ui.root)
        except (ProjectError, ValueError) as exc:
            message = str(exc).replace(f" in {ui.root}", "").replace(str(ui.root), "the projects folder")
            return _error(400, message)
        meta = ws.WorkspaceMeta()
        meta.competitors, problems = ws.parse_competitors(_text(_join(body.get("competitors")), 20000), made.domain)
        meta.dataset = ws.DATASET_NONE
        ws.save_meta(made, meta)
        return JSONResponse({"slug": made.slug, "problems": problems})

    @app.get("/api/v1/projects/{slug}/setup")
    async def api_setup(slug: str) -> JSONResponse:
        try:
            return JSONResponse(await run_in_threadpool(project_setup, ui, slug))
        except LookupError:
            return _error(404, "No such project.")

    @app.post("/api/v1/projects/{slug}/settings")
    async def api_settings(slug: str, request: Request) -> JSONResponse:
        from aeo.ranksim.project import ProjectError, update_searches

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 256 * 1024)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        problems: list[str] = []
        if "queries" in body:
            searches = _split(body.get("queries"))
            if len(searches) > serve.MAX_SEARCHES:
                return _error(400, f"At most {serve.MAX_SEARCHES} queries per project.")
            try:
                project = update_searches(project, replace=searches)
            except ProjectError as exc:
                return _error(400, str(exc))
        if "competitors" in body:
            meta = ws.load_meta(project)
            meta.competitors, problems = ws.parse_competitors(_text(_join(body.get("competitors")), 20000), project.domain)
            ws.save_meta(project, meta)
        ui.forget(slug)
        return JSONResponse({"ok": True, "problems": problems, "queries": project.searches})

    @app.post("/api/v1/projects/{slug}/import")
    async def api_import(slug: str, request: Request) -> JSONResponse:
        import re

        from aeo.ranksim.workspace_ui import _import_bytes

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, MAX_CSV_BYTES + 64 * 1024)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        data = _text(body.get("csv"), MAX_CSV_BYTES + 1).encode("utf-8")
        if not data.strip():
            return _error(400, "That file is empty.")
        if len(data) > MAX_CSV_BYTES:
            return _error(413, "That CSV is over 5 MB.")
        seen = _text(body.get("date"), 20).strip()
        if seen and not re.match(DATE_RE, seen):
            return _error(400, "Give the date the positions were seen as YYYY-MM-DD, or leave it blank.")
        check = ws.check_csv(data)
        if not check.ok:
            return JSONResponse({"error": "The CSV was not imported.", "problems": check.errors[:8]}, status_code=400)
        report = await run_in_threadpool(_import_bytes, ui, project, data, seen, bool(body.get("add_queries", True)))
        if isinstance(report, str):
            return _error(400, report)
        ui.forget(slug)
        return JSONResponse(
            {
                "rows": report.rows,
                "imported": report.searches_imported,
                "added": report.searches_added,
                "missing": report.searches_missing,
                "warnings": check.warnings[:8],
            }
        )

    @app.post("/api/v1/projects/{slug}/pull")
    async def api_pull(slug: str, request: Request) -> Response:
        """Ask Google (when a key is set), fetch every candidate page that is missing or stale, and train the engine.

        The answer streams one JSON object per line: progress events, then a done or error line. The work finishes
        and is saved even if the browser stops reading.
        """
        import queue
        import threading

        from fastapi.responses import StreamingResponse

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return _error(409, "A pull is already running for this project.")
        events: queue.Queue = queue.Queue()
        use_google = bool(body.get("google", True)) and google_status(ui)["ready"]

        def work() -> None:
            try:
                events.put({"type": "done", "report": _run_pull(ui, project, use_google, events)})
            except Exception as exc:  # noqa: BLE001 - shown to the owner, scrubbed of keys
                events.put({"type": "error", "error": _pull_error(ui, project, exc)})
            finally:
                lock.release()
                events.put(None)

        threading.Thread(target=work, name=f"pull-{slug}", daemon=True).start()

        async def stream():
            yield json.dumps({"type": "start", "google": use_google}) + "\n"
            while True:
                item = await run_in_threadpool(events.get)
                if item is None:
                    break
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"cache-control": "no-store", "x-accel-buffering": "no"})

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

    @app.post("/api/v1/projects/{slug}/delete")
    async def api_delete(slug: str, request: Request) -> JSONResponse:
        import shutil

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 4096)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        if _text(body.get("confirm"), 100) != project.slug:
            return _error(400, "Type the project's short name to confirm.")
        folder = Path(project.dir).resolve()
        if folder.parent != ui.root:
            return _error(400, "That project folder is outside the projects folder. Nothing was deleted.")
        await run_in_threadpool(shutil.rmtree, folder)
        ui.forget(slug)
        return JSONResponse({"ok": True})

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

    # -- autopilot: a site in, a report out ----------------------------------------------------------

    @app.post("/api/v1/sites")
    async def api_analyze(request: Request) -> Response:
        """Read the site, find its searches, ask Google, fetch the ranking pages, train, pick competitors.

        Streams one JSON object per line: step events, then done (with the project slug) or error.
        """
        import queue
        import threading

        from fastapi.responses import StreamingResponse

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        site = _text(body.get("site"), 300).strip()
        if not site:
            return _error(400, "Type your site's address, for example yoursite.com.")
        lock = ui.jobs.project_lock("__analyze__")
        if not lock.acquire(blocking=False):
            return _error(409, "Another site is being analyzed right now. Try again in a minute.")
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                events.put({"type": "done", **_analyze(ui, site, events, body.get("searches"))})
            except Exception as exc:  # noqa: BLE001 - shown to the owner, scrubbed of keys
                events.put({"type": "error", "error": _analyze_error(ui, exc)})
            finally:
                lock.release()
                events.put(None)

        threading.Thread(target=work, name="analyze", daemon=True).start()

        async def stream():
            yield json.dumps({"type": "start", "google": google_status(ui)["ready"]}) + "\n"
            while True:
                item = await run_in_threadpool(events.get)
                if item is None:
                    break
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    @app.get("/api/v1/projects/{slug}/report")
    async def api_report(slug: str) -> JSONResponse:
        try:
            return JSONResponse(await run_in_threadpool(project_report, ui, slug))
        except LookupError:
            return _error(404, "No such project.")

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)


def _join(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


def _run_pull(ui, project, use_google: bool, events) -> dict:
    """One pull, the same call the classic screens make, with hosted defaults: no AI engines, Google only with a key."""
    from datetime import date

    from aeo.ranksim.project import pull

    def progress(event) -> None:
        events.put(
            {
                "type": "progress",
                "step": event.step,
                "message": ui.scrub(event.message),
                "done": event.done,
                "total": event.total,
                "seconds": round(float(getattr(event, "seconds", 0.0) or 0.0), 1),
            }
        )

    options = {"engines": [], "engine_runs": 1, **ui.pull_options}
    meta = ws.load_meta(project)
    if meta.dataset == ws.DATASET_SYNTHETIC:
        options.update({"fetcher": ws.synthetic_fetcher, "skip_google": True, "fresh_days": 0})
    elif meta.dataset == ws.DATASET_HISTORICAL:
        options.update({"fetcher": ws.offline_fetcher, "skip_google": True, "fresh_days": 36500})
    else:
        options.setdefault("skip_google", not use_google)
    report = pull(project, ui.settings, database=ui.database, progress=progress, **options)
    ui.forget(project.slug)
    if report.google_kind == "api":
        from aeo.ranksim import autopilot
        from aeo.ranksim.project import latest_google

        fresh = ws.load_meta(project)
        if not fresh.competitors:  # competitors are picked for you; editing them in Settings sticks
            fresh.competitors = autopilot.pick_competitors(latest_google(project), project.domain)
            ws.save_meta(project, fresh)
    if report.google_kind == "api" and meta.dataset in {ws.DATASET_NONE, ws.DATASET_OBSERVED}:
        meta = ws.load_meta(project)
        meta.dataset = ws.DATASET_OBSERVED
        meta.dataset_label = f"Google results via {report.google_source or 'the configured provider'}, pulled {date.today().isoformat()}"
        ws.save_meta(project, meta)
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        events.put({"type": "progress", "step": "store", "message": "Saving the project, pages and engine.", "done": 0, "total": 1, "seconds": 0})
        cloud.save()
    return _pull_summary(report)


def _pull_error(ui, project, exc: Exception) -> str:
    from aeo.errors import ProviderConfigError

    if isinstance(exc, ProviderConfigError) and "No REAL Google results" in str(exc):
        return (
            f"No Google results for {project.name} yet. Add a Google results key (SERPER_API_KEY) to this server, "
            "or import a CSV of positions (query, url, position). Nothing was invented."
        )
    return ui.scrub(f"The pull failed: {type(exc).__name__}: {str(exc)[:300]}")


def project_report(ui, slug: str) -> dict:
    from aeo.ranksim import autopilot

    project = ui.project(slug)
    meta = ws.load_meta(project)
    ready, _reason = ws.engine_state(project)
    ctx = None
    if ready:
        try:
            ctx = ui.context(slug)
        except Exception:  # noqa: BLE001 - the report shows what it can without the engine
            ctx = None
    data = autopilot.build_report(project, meta, ui.database, ctx=ctx, google_ready=google_status(ui)["ready"])
    data["slug"] = project.slug
    data["data"] = _dataset_info(meta)
    data["engine"] = {"ready": ready}
    return data


def _analyze(ui, site: str, events, searches=None) -> dict:
    """The whole autopilot for one site. Every step reports progress on events."""
    from aeo.ranksim import autopilot
    from aeo.ranksim.project import ProjectError, clean_site, create_project, load_project, slugify, update_searches

    def step(name: str, message: str, done: int = 0, total: int = 0, state: str = "active", **extra) -> None:
        events.put({"type": "step", "step": name, "state": state, "message": ui.scrub(message), "done": done, "total": total, **extra})

    host = clean_site(site)
    fetcher = ui.pull_options.get("fetcher")
    step("site", f"Reading {host}.")
    pages = autopilot.read_site(host, ui.settings, ui.database, fetcher=fetcher, progress=lambda message, done, total: step("site", message, done, total))
    readable = [page for page in pages if page.status == "fetched"]
    if not readable:
        first = pages[0] if pages else None
        reason = (first.error or first.status) if first else "no answer"
        raise ProjectError(f"We couldn't read {host} ({reason}). Check the address, or that the site allows crawlers.")
    step("site", f"Read {len(readable)} of {len(pages)} pages.", len(pages), len(pages), state="done")

    brand = autopilot.brand_of(pages, host)
    chosen = _split(searches) if searches else autopilot.discover_searches(pages, brand, host=host)
    if not chosen:
        chosen = [brand.lower()]
    step("searches", f"Found {len(chosen)} searches your pages are written for.", len(chosen), len(chosen), state="done", searches=chosen)

    project = None
    want_domain = host.removeprefix("www.")
    for name in (brand, f"{brand} {want_domain}"):
        try:
            found = load_project(slugify(name), root=ui.root)
        except (ProjectError, FileNotFoundError, OSError, ValueError):
            project = create_project(name, host, chosen, root=ui.root)
            break
        if found.domain.removeprefix("www.") == want_domain:
            project = update_searches(found, replace=chosen)  # a new analysis starts from what the site says now
            break
    if project is None:
        raise ProjectError(f"A different site already uses the name {brand}. Rename that project first.")
    meta = ws.load_meta(project)
    ws.save_meta(project, meta)

    google = google_status(ui)
    if google["ready"]:
        pull_events: list = []

        class Relay:
            def put(self, item):
                if item.get("type") == "progress":
                    name = {"google": "google", "crawl": "pages", "train": "model", "store": "save"}.get(item.get("step"))
                    if name:
                        events.put({"type": "step", "step": name, "state": "active", "message": item.get("message", ""), "done": item.get("done", 0), "total": item.get("total", 0)})
                pull_events.append(item)

        summary = _run_pull(ui, project, True, Relay())
        meta = ws.load_meta(project)
        from aeo.ranksim.project import latest_google

        picked = autopilot.pick_competitors(latest_google(project), project.domain)
        if picked and not meta.competitors:
            meta.competitors = picked
            ws.save_meta(project, meta)
        step("competitors", f"Found {len(picked)} competitors.", len(picked), len(picked), state="done", competitors=picked)
        cloud = getattr(ui, "cloud", None)
        if cloud is not None and cloud.enabled:
            cloud.save()
    else:
        summary = None
        step("google", "Search data isn't connected on this server yet, so rankings are skipped. Your pages were still checked.", state="skipped")
        cloud = getattr(ui, "cloud", None)
        if cloud is not None and cloud.enabled:
            cloud.save()
    ui.forget(project.slug)
    step("report", "Report ready.", 1, 1, state="done")
    return {"slug": project.slug, "searches": chosen, "pull": summary}


def _analyze_error(ui, exc: Exception) -> str:
    from aeo.ranksim.project import ProjectError

    if isinstance(exc, ProjectError):
        return ui.scrub(str(exc).replace(str(ui.root), "the projects folder"))
    return ui.scrub(f"The analysis stopped: {type(exc).__name__}: {str(exc)[:300]}")
