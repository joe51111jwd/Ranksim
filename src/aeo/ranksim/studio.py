"""Ranksim Studio: the web app at / and the JSON API it reads.

The Studio is one page (static/studio.html, .css, .js) built around the product's main idea: edit a page, preview it,
and see where it is PREDICTED to land among the candidate pages for a query before it is published.

Nothing here changes the ranker. Every number comes from the same calls the classic screens use
(workspace.compare_search, workspace.run_draft). Positions from the model are PREDICTED, never a Google rank.
On a public host (public_demo) the API serves only the synthetic demo, never saves a draft, and never calls a provider.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import plugins
from aeo.ranksim.api import editor, projects, session, sites

# The JSON API moved to aeo.ranksim.api. These names are re-exported so older imports keep working.
from aeo.ranksim.api.common import (  # noqa: F401
    _check_header_csrf,
    _error,
    _join,
    _json_body,
    _not_public,
    _pull_summary,
    _signed_in,
    _split,
    _text,
    google_status,
    mode_of,
    storage_kind,
)
from aeo.ranksim.api.editor import compare, page_html, urlsplit_host  # noqa: F401
from aeo.ranksim.api.projects import _dataset_info, project_list, project_overview, project_report, project_setup  # noqa: F401
from aeo.ranksim.api.sites import _analyze, _analyze_error, _pull_error, _run_pull  # noqa: F401

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
OPEN_EXACT = {"/", "/app", "/start", "/edit", "/healthz", "/favicon.ico", "/api/v1/session", "/api/v1/login", "/api/v1/logout"}
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


def _is_open(path: str) -> bool:
    return path in OPEN_EXACT or path.startswith(OPEN_PREFIX)


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

    @app.get("/edit", response_class=HTMLResponse)
    def studio_edit() -> Response:
        return shell_response()  # /edit?url=... opens any page in the editor

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

    # -- JSON API: the core routes by area, then every plugin's routes ------------------------------

    session.register(app, ui)
    projects.register(app, ui)
    sites.register(app, ui)
    editor.register(app, ui)
    plugins.register_all(app, ui)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/v1/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

