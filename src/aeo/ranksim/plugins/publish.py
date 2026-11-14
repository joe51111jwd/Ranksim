"""Publish plugin: prove you own a site, connect where it is hosted, push editor changes live, check, roll back.

Routes (every write route checks the CSRF header; no route ever returns a credential):

    GET    /api/v1/publish/platforms                                   what each platform can and cannot change
    GET    /api/v1/projects/{slug}/publish/status?url=                 ownership + connections for a page
    POST   /api/v1/projects/{slug}/publish/ownership                   {site} -> token and instructions
    POST   /api/v1/projects/{slug}/publish/ownership/verify            {site, method?}
    POST   /api/v1/projects/{slug}/publish/ownership/reset             {site} -> a new token (unverifies)
    GET    /api/v1/projects/{slug}/publish/connections
    POST   /api/v1/projects/{slug}/publish/connections                 {kind, site, label, settings, secret}
    DELETE /api/v1/projects/{slug}/publish/connections/{id}
    POST   /api/v1/projects/{slug}/publish/plan                        {url, html, connection_id?}
    POST   /api/v1/projects/{slug}/publish                             {url, html, connection_id, confirm, options}
    GET    /api/v1/projects/{slug}/publish/history?url=
    POST   /api/v1/projects/{slug}/publish/history/{id}/verify
    POST   /api/v1/projects/{slug}/publish/history/{id}/rollback

The work lives in ``aeo.ranksim.publishing``; this module only wires it to the app.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from aeo.ranksim.publishing.service import PublishError, Publisher, catalog
from aeo.ranksim.publishing.store import PublishStore

log = logging.getLogger(__name__)
BODY_LIMIT = 5 * 1024 * 1024  # two copies of a 2 MB page plus room


def _helpers():
    try:  # after the foundation refactor
        from aeo.ranksim.api import common
    except ImportError:  # before it
        from aeo.ranksim import studio as common
    return common


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def publisher_for(ui, project) -> Publisher:
    # Tests set ui.publish_overrides (a mocked http_factory, no sleeping). The app never does.
    kwargs = dict(getattr(ui, "publish_overrides", None) or {})
    return Publisher(
        PublishStore.for_project(project),
        fetch=ui.preview_fetch,
        allow_private=bool(getattr(ui.settings, "crawl_allow_private", False)),
        who="owner",
        **kwargs,
    )


def register(app, ui) -> None:
    common = _helpers()

    def guard(slug: str):
        blocked = common._not_public(ui)
        if blocked is not None:
            return None, blocked
        try:
            project = ui.project(slug)
        except LookupError:
            return None, _error(404, "No such project.")
        return publisher_for(ui, project), None

    async def run(fn, *args, **kwargs):
        try:
            return JSONResponse(await run_in_threadpool(fn, *args, **kwargs))
        except PublishError as exc:
            return _error(exc.status, str(exc), **exc.extra)

    async def body_of(request: Request) -> dict:
        common._check_header_csrf(ui, request)
        return await common._json_body(request, BODY_LIMIT)

    def text(value, limit=2000) -> str:
        return str(value or "").strip()[:limit]

    @app.get("/api/v1/publish/platforms")
    def publish_platforms():
        return JSONResponse({"platforms": catalog()})

    @app.get("/api/v1/projects/{slug}/publish/status")
    async def publish_status(slug: str, url: str = ""):
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        target = url or f"https://{ui.project(slug).site}/"
        return await run(lambda: {**pub.status(target), "history": pub.history(url=target, limit=10)})

    @app.post("/api/v1/projects/{slug}/publish/ownership")
    async def publish_ownership(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(pub.start_ownership, text(body.get("site")))

    @app.post("/api/v1/projects/{slug}/publish/ownership/verify")
    async def publish_ownership_verify(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        method = text(body.get("method"), 10)
        return await run(pub.verify_ownership, text(body.get("site")), [method] if method else None)

    @app.post("/api/v1/projects/{slug}/publish/ownership/reset")
    async def publish_ownership_reset(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(pub.new_token, text(body.get("site")))

    @app.get("/api/v1/projects/{slug}/publish/connections")
    async def publish_connections(slug: str):
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        from aeo.ranksim.publishing.store import public_connection

        return JSONResponse({"connections": [public_connection(c) for c in pub.store.connections()]})

    @app.post("/api/v1/projects/{slug}/publish/connections")
    async def publish_connect(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        secret = body.get("secret") if isinstance(body.get("secret"), dict) else {}
        return await run(
            pub.add_connection,
            kind=text(body.get("kind"), 20),
            site=text(body.get("site")),
            label=text(body.get("label"), 120),
            settings=settings,
            secret=secret,
        )

    @app.delete("/api/v1/projects/{slug}/publish/connections/{connection_id}")
    async def publish_disconnect(slug: str, connection_id: str, request: Request):
        common._check_header_csrf(ui, request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        if not await run_in_threadpool(pub.remove_connection, connection_id):
            return _error(404, "No such connection.")
        return JSONResponse({"ok": True})

    @app.post("/api/v1/projects/{slug}/publish/plan")
    async def publish_plan(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(pub.plan, text(body.get("url")), str(body.get("html") or ""), text(body.get("connection_id"), 40) or None)

    @app.post("/api/v1/projects/{slug}/publish")
    async def publish_now(slug: str, request: Request):
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        confirm = [text(c, 40) for c in body.get("confirm") or [] if isinstance(c, str)]
        options = body.get("options") if isinstance(body.get("options"), dict) else {}
        return await run(
            pub.publish,
            text(body.get("url")),
            str(body.get("html") or ""),
            text(body.get("connection_id"), 40),
            confirm=confirm,
            options=options,
        )

    @app.get("/api/v1/projects/{slug}/publish/history")
    async def publish_history(slug: str, url: str = ""):
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(lambda: {"history": pub.history(url=url or None)})

    @app.post("/api/v1/projects/{slug}/publish/history/{entry_id}/verify")
    async def publish_verify(slug: str, entry_id: str, request: Request):
        common._check_header_csrf(ui, request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(pub.verify_live, entry_id)

    @app.post("/api/v1/projects/{slug}/publish/history/{entry_id}/rollback")
    async def publish_rollback(slug: str, entry_id: str, request: Request):
        common._check_header_csrf(ui, request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        return await run(pub.rollback, entry_id)


def after_pull(*, project, report=None, ui=None, **_ignored) -> None:
    """Periodic re-check: every pull re-checks ownership proofs that are older than a day."""
    if ui is None:
        return
    try:
        publisher_for(ui, project).recheck_stale()
    except Exception:  # noqa: BLE001 - a plugin hook never breaks a pull
        log.exception("publish: ownership re-check failed")
