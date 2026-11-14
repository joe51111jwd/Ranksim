"""Plugins: one module per feature in this folder, each owned by one build session.

Every module in src/aeo/ranksim/plugins/ is imported at startup (sorted by name; names starting with "_" are
skipped). A module may define any of these functions. All are optional. A plugin that raises is logged and
skipped, never fatal: a plugin that fails to import is left out (and listed under "failed" by GET /api/v1/plugins),
and a hook that raises is skipped for that call only.

Hooks (docs/BUILD_PLAN.md 4.2):

register(app, ui) -> None
    Called once, after core routes. Add FastAPI routes here.
    Every route except the shell, assets, session and login requires the signed-in owner (the login middleware
    does it for plugin routes too). Every write route needs the x-ranksim-csrf header: call
    api.common._check_header_csrf(ui, request) first, as core routes do. As a backstop, a POST, PUT, PATCH or
    DELETE route a plugin adds is refused with the same 403 when the header is missing or wrong.
    A route that outside callers must reach, and that proves itself another way, opts out on its endpoint function:
        endpoint.login_exempt = True   the login middleware lets a signed-out request through (a read-only share
                                       link with a signed token, a Vercel cron GET with an Authorization: Bearer
                                       CRON_SECRET header). The route MUST check that token itself (compare it with
                                       secrets.compare_digest) and answer 401 or 404 without it.
        endpoint.csrf_exempt = True    the CSRF backstop lets a write through without the x-ranksim-csrf header. An
                                       outside caller never has that header, so a login_exempt POST needs this too.
    Only the route the router picks for a request counts, so a login_exempt route never opens a core route that
    shares its path. Each login_exempt route is logged at startup.

extend_report(report: dict, *, project, meta, database, ctx, ui) -> None
    Called at the end of build_report, once per plugin in load order. Add keys under your own namespace, e.g.
    report["gsc"] = {...}. Never remove or change an existing key. Values must be JSON as the API sends it: str,
    int, float (not NaN or infinity), bool, None, list, and dict with str keys; no dates, sets or objects. A plugin
    whose call raises, removes a key, or leaves anything that is not JSON has all of its changes to the report
    undone (and is logged); the other plugins keep theirs. It runs on every GET of the report, so keep it
    read-only: a file it writes is not saved to cloud storage unless it calls save(ui) (see below).
    ctx is the loaded engine or None; ui is None when the report is built outside the API.

discovery_sources(*, project, pages, ui) -> list[dict]
    Called during analysis. Return {"q": str, "weight": float, "source": str} rows that discovery merges into the
    search list (discovery.discover_searches documents the merge: weights use the same scale as the site's own
    phrases, a title phrase scores 3.0 and a phrase needs 2.5 to be picked; rows are deduplicated and the list is
    capped at discovery.MAX_SEARCHES). project is None on a site's first analysis. pages are site.SitePage rows.

audit_checks() -> list[callable]
    Called by audit.audit. Each check is check(ctx) -> list[issue dict] (issue shape in BUILD_PLAN 4.4). They run
    after the built-in audit.CHECKS with the same audit.AuditContext. Issues without a severity (high, medium,
    low), a kind and a title are dropped, and so are issues that are not JSON (the extend_report rule). An issue
    without an id gets one from its kind, page, search and element.selector. audit() then makes every id in the
    list unique by adding -2, -3 and so on.

after_pull(*, project, report, ui) -> None
    Called after every pull or analysis finished (the Studio pull, the analysis, and the classic /p/{slug}/pull).
    report is the pull summary (the "report" of the pull stream's done line), or None when an analysis ran without
    search data. It runs in the pull's background thread. In the Studio pull and the analysis it runs before the
    project is saved to cloud storage, so files written under data_dir() here are kept on a cloud host. The classic
    /p/{slug}/pull does not save after it (see below). Keep it short: the pull is not done until it returns.

settings_schema() -> dict
    Optional. Describes plugin settings shown in Settings. GET /api/v1/plugins lists every loaded plugin with its
    schema (JSON).

csp(path: str) -> str | None
    Optional. The Content-Security-Policy for a path this plugin serves (for example a public share page under
    /r/), or None to leave the path alone. Consulted only for paths outside the Studio pages, the classic pages that
    have their own policy, /api/, /studio/ and /static/, so a plugin cannot loosen those. Answer only for paths
    your plugin serves. The first plugin (load order) that returns a string wins.

On the public demo (ui.public_demo) a plugin must not call a provider or save anything: start write routes with
api.common._not_public(ui), and skip paid calls in the other hooks.

Helpers for plugins and the core:
    call_all(name, *args, **kwargs) -> list   one result per plugin that defines the hook and did not raise
    collect(name, *args, **kwargs) -> list    the same results with lists flattened and None dropped
    extend(name, data, *args, **kwargs)       call hook(data, ...) on each plugin in turn for a hook that adds to a
                                              dict (build_report uses it for extend_report), undoing one plugin's
                                              changes when it raises, removes a key or leaves data that is not JSON
    json_error(value) -> str | None           None when value encodes as the API sends JSON, else the reason
    data_dir(project, feature) -> Path        project.dir / "plugins" / feature, created
    workspace_dir(ui, feature) -> Path        <projects root> / "_plugins" / feature, created: data that belongs
                                              to the whole workspace (one Google connection, a usage counter,
                                              branding), not to one project. Cloud storage keeps it like project
                                              data. Never put workspace data anywhere else under the projects root.
    csp_for(path) -> str | None               the first plugin csp(path) answer (serve.csp_for uses it)
    save(ui) -> bool                          save the data folder to cloud storage now (see below)

When plugin data is saved on a cloud host (Vercel). Each instance has its own disk. Cloud storage keeps one copy of
the whole data folder (every project folder, so every data_dir() too), and an instance loads the newest copy on a
cold start and before requests. A file that was written but never saved is lost on a cold start, or replaced when
another instance saves. Writes are saved automatically only:
    - in a POST, PUT, PATCH or DELETE route that answers below 400 and whose path does not end in /pull, /fetch,
      /login or /logout. The middleware saves once the route has answered; for a streamed answer that is when the
      stream starts, so a write made while it streams is not covered.
    - in after_pull during the Studio pull and the analysis, and in discovery_sources during the analysis: both
      run before the save the pull or analysis makes when it finishes.
Everything else must call save(ui) after its writes: a GET route (a Vercel cron is a GET), extend_report, an audit
check, a streamed route that writes while it streams, after_pull in the classic /p/{slug}/pull, and a background
thread. save(ui) uploads this instance's whole data folder as the newest copy, so call it once after the writes, not
once per file. Without cloud storage (a local run, the public demo) it does nothing and returns False. It raises
aeo.ranksim.cloud.CloudError when the upload fails. It blocks while it uploads: call it from a plain def route
(FastAPI runs those in a worker thread) or through starlette.concurrency.run_in_threadpool.

Routes use the shared helpers in aeo.ranksim.api.common (_check_header_csrf, _json_body, _not_public, _error, _text).
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import re
import secrets
import sys
import threading
from pathlib import Path
from types import ModuleType

HOOKS = ("register", "extend_report", "discovery_sources", "audit_checks", "after_pull", "settings_schema", "csp")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

log = logging.getLogger(__name__)
HOME = Path(__file__).resolve().parent  # the real plugins folder
_lock = threading.RLock()
_modules: list[ModuleType] | None = None
_failed: list[dict] = []
_scrub = None  # the app's scrubber (UiState.scrub) once register_all ran


def _clean_text(text: str) -> str:
    if _scrub is not None:
        try:
            return _scrub(text)
        except Exception:  # noqa: BLE001 - fall back to the generic rules
            pass
    value = re.sub(r"(?i)(api[_-]?key|key|token|password|secret)=([^&\s\"']+)", r"\1=[redacted]", str(text or ""))
    return re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [redacted]", value)


def _describe(exc: BaseException) -> str:
    return _clean_text(f"{type(exc).__name__}: {str(exc)[:300]}")


def name_of(module: ModuleType) -> str:
    return module.__name__.rsplit(".", 1)[-1]


def load(*, refresh: bool = False) -> list[ModuleType]:
    """Import every plugin module once (sorted by name, "_" names skipped) and return them in that order."""
    global _modules
    with _lock:
        if _modules is not None and not refresh:
            return list(_modules)
        if refresh:
            importlib.invalidate_caches()
        modules: list[ModuleType] = []
        failed: list[dict] = []
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
            if info.name.startswith("_"):
                continue
            try:
                modules.append(importlib.import_module(f"{__name__}.{info.name}"))
            except Exception as exc:  # noqa: BLE001 - one broken plugin never stops the app
                failed.append({"name": info.name, "error": _describe(exc)})
                log.warning("ranksim plugins: %s did not load and is skipped: %s", info.name, _describe(exc))
                log.debug("ranksim plugins: %s import traceback", info.name, exc_info=True)
        _modules = modules
        _failed[:] = failed
        return list(modules)


def reset() -> None:
    """Forget the loaded plugins so the next load() lists the folder again. For tests.

    Modules imported from another folder (a test's temporary plugin folder) are dropped from sys.modules, so they
    are imported fresh. The real plugin modules (in HOME) stay imported: a test file that imported one at the top
    and monkeypatches it keeps patching the copy the app uses.
    """
    global _modules
    with _lock:
        _modules = None
        _failed.clear()
        for key, module in list(sys.modules.items()):
            if key.startswith(__name__ + ".") and not _from_home(module):
                del sys.modules[key]


def _from_home(module) -> bool:
    found = getattr(module, "__file__", None)
    if not isinstance(found, str) or not found:
        return False
    try:
        return Path(found).resolve().parent == HOME
    except (OSError, RuntimeError):
        return False


def names() -> list[str]:
    return [name_of(module) for module in load()]


def guarded(label: str, fn, *args, **kwargs) -> tuple[bool, object]:
    """Call fn and return (True, result), or log the exception and return (False, None)."""
    try:
        return True, fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - a plugin never breaks startup or a request
        log.warning("ranksim plugins: %s failed and was skipped: %s", label, _describe(exc))
        log.debug("ranksim plugins: %s traceback", label, exc_info=True)
        return False, None


def call_all(name: str, *args, **kwargs) -> list:
    """Call hook `name` on every plugin that defines it, in load order. One result per plugin that did not raise."""
    results = []
    for module in load():
        hook = getattr(module, name, None)
        if not callable(hook):
            continue
        ok, result = guarded(f"{name_of(module)}.{name}", hook, *args, **kwargs)
        if ok:
            results.append(result)
    return results


def collect(name: str, *args, **kwargs) -> list:
    """call_all, flattened: list and tuple results are joined, None results dropped."""
    out: list = []
    for result in call_all(name, *args, **kwargs):
        if result is None:
            continue
        if isinstance(result, (list, tuple)):
            out.extend(result)
        else:
            out.append(result)
    return out


def json_error(value) -> str | None:
    """None when value encodes exactly as the API sends JSON (JSONResponse: no NaN or infinity, UTF-8), else why not."""
    return _encode(value)[1]


def _encode(value) -> tuple[bytes | None, str | None]:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"), None
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:  # UnicodeEncodeError is a ValueError
        return None, _describe(exc)


def extend(name: str, data: dict, *args, **kwargs) -> None:
    """Call hook `name`(data, *args, **kwargs) on every plugin that defines it, one at a time, in load order.

    For a hook that adds to a dict the API sends as JSON (build_report calls it for extend_report). A plugin whose
    call raises, removes a key of data, or leaves data that is not JSON (json_error) has all of its changes undone,
    and is logged; the plugins before and after it keep theirs. When data is not JSON before any plugin runs there
    is nothing to protect, and the hooks are simply called.
    """
    snapshot, _problem = _encode(data)
    for module in load():
        hook = getattr(module, name, None)
        if not callable(hook):
            continue
        label = f"{name_of(module)}.{name}"
        keys = set(data)
        ok, _result = guarded(label, hook, data, *args, **kwargs)
        if snapshot is None:
            continue
        if ok:
            removed = keys - set(data)
            if removed:
                problem = f"removed {', '.join(sorted(str(key) for key in removed))[:200]}"
            else:
                after, problem = _encode(data)
                if after is not None:
                    snapshot = after
                    continue
                problem = f"left a value that is not JSON ({problem})"
            log.warning("ranksim plugins: %s %s; its changes were undone.", label, problem)
        data.clear()
        data.update(json.loads(snapshot))


def save(ui) -> bool:
    """Save the data folder (every project, so every data_dir()) to cloud storage now. For writes the middleware does
    not save (module docstring). Returns False without cloud storage or on the public demo; raises
    aeo.ranksim.cloud.CloudError when the upload fails. Blocks while it uploads."""
    cloud = getattr(ui, "cloud", None)
    if cloud is None or not getattr(cloud, "enabled", False) or getattr(ui, "public_demo", False):
        return False
    cloud.save()
    return True


def is_login_exempt(app, scope) -> bool:
    """True when the route the router would pick for this request has endpoint.login_exempt = True.

    It walks app.router.routes in order and stops at the first full match, as the router does, so a login_exempt
    route never opens a route registered before it on the same path."""
    from starlette.routing import Match

    for route in app.router.routes:
        try:
            match, _child = route.matches(scope)
        except Exception:  # noqa: BLE001 - a route that cannot match this scope is not the one picked
            continue
        if match == Match.FULL:
            return getattr(getattr(route, "endpoint", None), "login_exempt", False) is True
    return False


def data_dir(project, feature: str) -> Path:
    """project.dir / "plugins" / feature, created. feature is a short lowercase name such as "gsc"."""
    if not isinstance(feature, str) or not FEATURE_RE.match(feature):
        raise ValueError("A plugin data folder name is lowercase letters, digits, - and _.")
    folder = Path(project.dir) / "plugins" / feature
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def workspace_dir(ui, feature: str) -> Path:
    """<projects root> / "_plugins" / feature, created. For workspace-wide plugin data (module docstring)."""
    if not isinstance(feature, str) or not FEATURE_RE.match(feature):
        raise ValueError("A plugin data folder name is lowercase letters, digits, - and _.")
    root = getattr(ui, "root", None)
    if root is None:
        raise ValueError("This app has no projects folder.")
    folder = Path(root) / "_plugins" / feature
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def csp_for(path: str) -> str | None:
    """The first plugin csp(path) answer that is a non-empty string, else None. A hook that raises is skipped."""
    for answer in call_all("csp", path):
        if isinstance(answer, str) and answer.strip():
            return answer
    return None


def register_all(app, ui) -> None:
    """Add GET /api/v1/plugins, then call every plugin's register(app, ui). Called by studio.register after the
    core routes."""
    global _scrub
    _scrub = getattr(ui, "scrub", None)
    load()
    _routes(app, ui)
    for module in load():
        hook = getattr(module, "register", None)
        if not callable(hook):
            continue
        before = len(app.router.routes)
        guarded(f"{name_of(module)}.register", hook, app, ui)
        for route in app.router.routes[before:]:
            _guard_writes(route, ui)
            if getattr(getattr(route, "endpoint", None), "login_exempt", False) is True:
                methods = ",".join(sorted(getattr(route, "methods", None) or ())) or "ANY"
                log.info("ranksim plugins: %s added %s %s without the login (login_exempt); it checks its own token.", name_of(module), methods, getattr(route, "path", "?"))


def describe() -> dict:
    """Every loaded plugin with the hooks it defines and its settings schema, and the plugins that failed to load."""
    rows = []
    for module in load():
        name = name_of(module)
        schema = None
        if callable(getattr(module, "settings_schema", None)):
            ok, found = guarded(f"{name}.settings_schema", module.settings_schema)
            if ok and isinstance(found, dict):
                try:
                    json.dumps(found)
                    schema = found
                except (TypeError, ValueError):
                    log.warning("ranksim plugins: %s.settings_schema is not JSON; skipped.", name)
        rows.append({"name": name, "hooks": [hook for hook in HOOKS if callable(getattr(module, hook, None))], "schema": schema})
    with _lock:
        failed = [dict(item) for item in _failed]
    return {"plugins": rows, "failed": failed}


def _routes(app, ui) -> None:
    from fastapi.responses import JSONResponse

    @app.get("/api/v1/plugins")
    def api_plugins() -> JSONResponse:
        data = describe()
        for item in data["failed"]:
            item["error"] = ui.scrub(item["error"]) if hasattr(ui, "scrub") else item["error"]
        return JSONResponse(data)


def _guard_writes(route, ui) -> None:
    """Refuse a plugin's write route without the CSRF header, exactly as api.common._check_header_csrf does."""
    methods = set(getattr(route, "methods", None) or ())
    endpoint = getattr(route, "endpoint", None)
    inner = getattr(route, "app", None)
    if not methods & WRITE_METHODS or inner is None or getattr(endpoint, "csrf_exempt", False):
        return
    from starlette.datastructures import Headers

    from aeo.ranksim.api.common import CSRF_EXPIRED, _error
    from aeo.ranksim.studio import CSRF_HEADER

    async def guarded_app(scope, receive, send):
        if scope.get("type") == "http" and scope.get("method") in WRITE_METHODS:
            token = Headers(scope=scope).get(CSRF_HEADER, "")
            if not token or not secrets.compare_digest(token, ui.csrf):
                await _error(403, CSRF_EXPIRED)(scope, receive, send)
                return
        await inner(scope, receive, send)

    route.app = guarded_app
