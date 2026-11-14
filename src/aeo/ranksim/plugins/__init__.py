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
    DELETE route a plugin adds is refused with the same 403 when the header is missing or wrong; set
    endpoint.csrf_exempt = True on the endpoint function only for a route that proves itself another way.

extend_report(report: dict, *, project, meta, database, ctx, ui) -> None
    Called at the end of build_report. Add keys under your own namespace, e.g. report["gsc"] = {...}. Never
    remove or change an existing key. ctx is the loaded engine or None; ui is None when the report is built
    outside the API.

discovery_sources(*, project, pages, ui) -> list[dict]
    Called during analysis. Return {"q": str, "weight": float, "source": str} rows that discovery merges into the
    search list (discovery.discover_searches documents the merge: weights use the same scale as the site's own
    phrases, a title phrase scores 3.0 and a phrase needs 2.5 to be picked; rows are deduplicated and the list is
    capped at discovery.MAX_SEARCHES). project is None on a site's first analysis. pages are site.SitePage rows.

audit_checks() -> list[callable]
    Called by audit.audit. Each check is check(ctx) -> list[issue dict] (issue shape in BUILD_PLAN 4.4). They run
    after the built-in audit.CHECKS with the same audit.AuditContext. Issues without a severity (high, medium,
    low), a kind and a title are dropped.

after_pull(*, project, report, ui) -> None
    Called after every pull or analysis finished (the Studio pull, the analysis, and the classic /p/{slug}/pull).
    report is the pull summary (the "report" of the pull stream's done line), or None when an analysis ran without
    search data. It runs in the pull's background thread. In the Studio pull and the analysis it runs before the
    project is saved to cloud storage, so files written under data_dir() here are kept on a cloud host. Keep it
    short: the pull is not done until it returns.

settings_schema() -> dict
    Optional. Describes plugin settings shown in Settings. GET /api/v1/plugins lists every loaded plugin with its
    schema (JSON).

On the public demo (ui.public_demo) a plugin must not call a provider or save anything: start write routes with
api.common._not_public(ui), and skip paid calls in the other hooks.

Helpers for plugins and the core:
    call_all(name, *args, **kwargs) -> list   one result per plugin that defines the hook and did not raise
    collect(name, *args, **kwargs) -> list    the same results with lists flattened and None dropped
    data_dir(project, feature) -> Path        project.dir / "plugins" / feature, created. Cloud storage mirrors the
                                              projects folder, so this persists on Vercel.
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

HOOKS = ("register", "extend_report", "discovery_sources", "audit_checks", "after_pull", "settings_schema")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FEATURE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

log = logging.getLogger(__name__)
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
    """Forget the loaded plugins so the next load() imports them again. For tests."""
    global _modules
    with _lock:
        _modules = None
        _failed.clear()
        for key in [key for key in sys.modules if key.startswith(__name__ + ".")]:
            del sys.modules[key]


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


def data_dir(project, feature: str) -> Path:
    """project.dir / "plugins" / feature, created. feature is a short lowercase name such as "gsc"."""
    if not isinstance(feature, str) or not FEATURE_RE.match(feature):
        raise ValueError("A plugin data folder name is lowercase letters, digits, - and _.")
    folder = Path(project.dir) / "plugins" / feature
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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
