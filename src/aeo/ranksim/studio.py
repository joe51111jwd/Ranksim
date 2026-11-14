"""Ranksim Studio: the web app at / and the JSON API it reads.

The Studio is one page (static/studio.html, .css, .js) built around the product's main idea: edit a page, preview it,
and see where it is PREDICTED to land among the candidate pages for a query before it is published.

Nothing here changes the ranker. Every number comes from the same calls the classic screens use
(workspace.compare_search, workspace.run_draft). Positions from the model are PREDICTED, never a Google rank.
On a public host (public_demo) the API serves only the synthetic demo, never saves a draft, and never calls a provider.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import plugins
from aeo.ranksim.api import editor, projects, session, sites

# The JSON API moved to aeo.ranksim.api. These names are re-exported so older imports keep working.
from aeo.ranksim.api.common import (  # noqa: F401
    _check_header_csrf,
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
# The files under static/ that /studio/<path> serves, by extension. Anything else (the shell template, notes) is a 404.
ASSETS = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
# Feature plugins: the shell lists every file in these folders, so adding a file is enough (see their README.md).
PLUGIN_JS = STATIC / "js" / "plugins"
PLUGIN_CSS = STATIC / "css" / "plugins"
SCREENS = {"studio", "rankings", "data", "overview", "issues", "searches", "competitors", "editor", "settings"}
MAX_CSV_BYTES = 5 * 1024 * 1024
DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
# /app/<slug>/<key> serves the shell for any key of this shape, so a screen a frontend plugin registers survives a
# reload or a pasted link. The client router decides what an unknown key shows.
SCREEN_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
# Paths that answer without a login: the app shell (it holds no data), its assets, and the login itself.
OPEN_EXACT = {"/", "/app", "/start", "/edit", "/healthz", "/favicon.ico", "/api/v1/session", "/api/v1/login", "/api/v1/logout"}
OPEN_PREFIX = ("/studio/", "/app/")
NO_STATE_PREFIX = ("/studio/", "/static/")
NO_SAVE_SUFFIX = ("/pull", "/fetch", "/login", "/logout", "/sites", "/resume")  # jobs save themselves
SYNC_COOKIE = "rs_sync"


def _serve():
    from aeo.ranksim import serve

    return serve


def _shell(ui) -> str:
    """The app shell with this process's CSRF token. All content is rendered by js/app.js from the API.

    Stylesheets come first (css/base.css, the other css/*.css, then css/plugins/*.css), each with a version query so a
    new deploy restyles at once. Scripts are ES modules without query strings, because a module is keyed by its URL
    and every module must share one copy of core.js; /studio/ answers them with no-cache and an ETag instead.
    app.js comes before the plugins and boots on DOMContentLoaded, after every plugin has registered.
    css/print.css (the printable report) loads with media="print", so its rules never restyle the app on screen.
    """
    from html import escape

    styles, modules, plugins = _shell_assets()
    media = {"css/print.css": ' media="print"'}
    lines = [f'<link rel="stylesheet" href="/studio/{escape(name)}?v={_asset_version([path])}"{media.get(name, "")}>' for name, path in styles]
    lines += [f'<link rel="modulepreload" href="/studio/{escape(name)}">' for name, _path in modules if name != "js/app.js"]
    lines.append('<script type="module" src="/studio/js/app.js"></script>')
    lines += [f'<script type="module" src="/studio/{escape(name)}"></script>' for name, _path in plugins]
    page = (STATIC / "studio.html").read_text(encoding="utf-8")
    return (
        page.replace("{{csrf}}", escape(ui.csrf, quote=True))
        .replace("{{mode}}", mode_of(ui))
        .replace("{{assets}}", "\n".join(lines))
    )


def _shell_assets() -> tuple[list[tuple[str, Path]], list[tuple[str, Path]], list[tuple[str, Path]]]:
    """(stylesheets, core modules, plugin modules) as (published name, file) pairs in load order."""

    def listed(folder: Path, prefix: str, suffix: str) -> list[tuple[str, Path]]:
        if not folder.is_dir():
            return []
        found = [item for item in folder.iterdir() if item.suffix == suffix and item.is_file() and _plain(item.name)]
        return [(f"{prefix}/{item.name}", item) for item in sorted(found, key=lambda item: item.name)]

    css = listed(STATIC / "css", "css", ".css")
    css.sort(key=lambda pair: pair[0] != "css/base.css")  # the design tokens load first; sort is stable
    modules = []
    for path in sorted((STATIC / "js").rglob("*.js")):
        rel = path.relative_to(STATIC / "js")
        if rel.parts[0] != "plugins" and all(_plain(part) for part in rel.parts):
            modules.append(("js/" + rel.as_posix(), path))
    return css + listed(PLUGIN_CSS, "css/plugins", ".css"), modules, listed(PLUGIN_JS, "js/plugins", ".js")


def _plain(part: str) -> bool:
    """A file or folder name that is safe to publish: letters, digits, dot, dash and underscore, no leading dot."""
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part))


def _asset_file(name: str) -> Path | None:
    """The file behind /studio/<name>: plain path parts only, an allowed extension, and never outside its folder."""
    parts = name.split("/")
    if not name or len(parts) > 4 or not all(_plain(part) for part in parts):
        return None
    if Path(parts[-1]).suffix not in ASSETS:
        return None
    if len(parts) == 3 and parts[:2] == ["js", "plugins"]:
        root, rest = PLUGIN_JS, parts[2:]
    elif len(parts) == 3 and parts[:2] == ["css", "plugins"]:
        root, rest = PLUGIN_CSS, parts[2:]
    else:
        root, rest = STATIC, parts
    try:
        root = root.resolve()
        path = root.joinpath(*rest).resolve()
    except (OSError, RuntimeError):
        return None
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def _asset_version(paths=None) -> str:
    """A short hash of the files' bytes (by default every file the shell lists). It changes whenever one changes."""
    import hashlib

    if paths is None:
        styles, modules, plugins = _shell_assets()
        paths = [path for _name, path in styles + modules + plugins]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()[:10]


def _error(status: int, message: str) -> JSONResponse:
    """The middleware's JSON error. Routes and plugins use api.common._error, which answers the same."""
    return JSONResponse({"error": message}, status_code=status)


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

        # Blob operations are budgeted (cloud.py): a read checks storage only on a cold start, every few minutes, or
        # when the browser's rs_sync cookie (the newest save it has seen) is newer than this instance; a write checks
        # first so it never builds on stale files. The cookie is a hint, not a secret: a forged one costs one check.
        writing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        hint_text = request.cookies.get(SYNC_COOKIE, "")
        hint = int(hint_text) if hint_text.isdigit() and len(hint_text) <= 20 else 0
        hinted = hasattr(cloud, "stamp")  # a stand-in cloud object (tests) may not take hints
        try:
            await run_in_threadpool(lambda: cloud.refresh(force=writing, hint=hint) if hinted else cloud.refresh(force=writing))
        except CloudError as exc:
            if path.startswith("/api/"):
                return _error(503, f"{exc} Nothing was changed. Try again in a moment.")
            return Response(f"{exc} Try again in a moment.", status_code=503, media_type="text/plain")
        response = await call_next(request)
        if hinted and response.status_code == 404 and request.method == "GET" and path.startswith("/api/v1/projects/"):
            # A project made on another instance a moment ago: check storage once, then answer again.
            if await run_in_threadpool(lambda: cloud.refresh(hint=cloud.stamp + 1)):
                response = await call_next(request)
        if writing and response.status_code < 400 and not path.endswith(NO_SAVE_SUFFIX):
            try:
                await run_in_threadpool(cloud.save)
            except CloudError as exc:
                print(f"ranksim: {exc}", flush=True)
                if path.startswith("/api/"):
                    return _error(503, f"{exc} The change was made on this server but may not last. Try again.")
        if hinted and cloud.stamp > hint:
            response.set_cookie(SYNC_COOKIE, str(cloud.stamp), max_age=30 * 24 * 3600, httponly=False, samesite="lax", path="/")
        return response

    @app.middleware("http")
    async def _require_login(request: Request, call_next):
        # A plugin route marked endpoint.login_exempt checks its own token (a share link, a cron secret).
        if ui.access.enabled and not _is_open(request.url.path) and not _signed_in(ui, request) and not plugins.is_login_exempt(app, request.scope):
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
        if not SCREEN_KEY.fullmatch(screen):  # every built-in name in SCREENS has this shape too
            return serve._not_found(ui)
        return shell_response()

    @app.get("/studio/{name:path}")
    def studio_asset(name: str, request: Request) -> Response:
        import hashlib

        path = _asset_file(name)
        if path is None:
            return Response("Not found.", status_code=404, media_type="text/plain")
        data = path.read_bytes()
        # no-cache plus a content ETag: the browser revalidates every load (a 304 is cheap), so a deploy is never stale
        etag = f'"{hashlib.sha256(data).hexdigest()[:20]}"'
        headers = {"cache-control": "no-cache", "etag": etag}
        wanted = {tag.strip().removeprefix("W/") for tag in request.headers.get("if-none-match", "").split(",")}
        if etag in wanted or "*" in wanted:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type=ASSETS[path.suffix], headers=headers)

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

