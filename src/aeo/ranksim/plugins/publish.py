"""Publish plugin: prove you own a site, connect where it is hosted, push editor changes live, check, roll back.

Routes (every write route checks the CSRF header; no route ever returns a credential):

    GET    /api/v1/publish/platforms                                   what each platform can and cannot change
    GET    /api/v1/projects/{slug}/publish/status?url=                 ownership + connections for a page
    POST   /api/v1/projects/{slug}/publish/ownership                   {site} -> token and instructions
    POST   /api/v1/projects/{slug}/publish/ownership/verify            {site, method?}
    POST   /api/v1/projects/{slug}/publish/ownership/reset             {site} -> a new token (unverifies)
    GET    /api/v1/projects/{slug}/publish/connections
    POST   /api/v1/projects/{slug}/publish/connections                 {kind, site, label, settings, secret}
    POST   /api/v1/projects/{slug}/publish/connections/check           {kind, settings, secret, url} -> steps, saves nothing
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
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from aeo.ranksim.api import common

# The publishing package (BeautifulSoup, lxml) loads on the first publish request, never at startup: a cold start on
# Vercel must stay light (tests/test_coldstart.py).

log = logging.getLogger(__name__)
BODY_LIMIT = 5 * 1024 * 1024  # two copies of a 2 MB page plus room


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def proof_fetcher(settings):
    """Like ui.preview_fetch (robots respected, private addresses refused) but also returns the final URL, so an
    ownership proof served after a redirect to another host is not accepted."""
    from aeo.crawl.fetch import Crawler
    from aeo.errors import UnsafeURL

    def fetch(url: str) -> tuple[str, str, str]:
        crawler = Crawler(
            user_agent=settings.crawl_user_agent,
            delay_seconds=0,
            allow_private=settings.crawl_allow_private,
            timeout=settings.request_timeout_seconds,
        )
        try:
            fetched = crawler.fetch(url)
        except UnsafeURL as exc:
            return "", f"refused: {exc}", url
        except Exception as exc:  # noqa: BLE001 - reported as a check that could not run
            return "", f"fetch failed: {type(exc).__name__}", url
        finally:
            crawler.close()
        if fetched.blocked:
            return "", "blocked by robots.txt", fetched.final_url
        if fetched.error or not fetched.html:
            return "", fetched.error or "empty response", fetched.final_url
        return fetched.html, "", fetched.final_url

    return fetch


def publisher_for(ui, project):
    from aeo.ranksim.publishing.service import Publisher
    from aeo.ranksim.publishing.store import PublishStore

    # Tests set ui.publish_overrides (a mocked http_factory and fetcher, no sleeping). The app never does.
    kwargs = dict(getattr(ui, "publish_overrides", None) or {})
    if "proof_fetch" not in kwargs:
        kwargs["proof_fetch"] = proof_fetcher(ui.settings)
    kwargs.setdefault("checkpoint", lambda: save_quietly(ui))
    return Publisher(
        PublishStore.for_project(project),
        fetch=ui.preview_fetch,
        allow_private=bool(getattr(ui.settings, "crawl_allow_private", False)),
        who="owner",
        **kwargs,
    )


def register(app, ui) -> None:

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
        except Exception as exc:
            from aeo.ranksim.publishing.service import PublishError

            if not isinstance(exc, PublishError):
                raise
            # An error answer is not saved by the middleware, but it may follow a write: a failed publish keeps its
            # history entry, and a stale proof that is gone is revoked. Save those now.
            await run_in_threadpool(save_quietly, ui)
            return _error(exc.status, str(exc), **exc.extra)

    async def body_of(request: Request) -> dict:
        common._check_header_csrf(ui, request)
        return await common._json_body(request, BODY_LIMIT)

    def text(value, limit=2000) -> str:
        return str(value or "").strip()[:limit]

    @app.get("/api/v1/publish/platforms")
    def publish_platforms():
        from aeo.ranksim.publishing.service import catalog

        return JSONResponse({"platforms": catalog()})

    @app.get("/api/v1/projects/{slug}/publish/status")
    async def publish_status(slug: str, url: str = ""):
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        target = url or f"https://{ui.project(slug).site}/"
        from aeo.ranksim.publishing.store import encryption_ready

        return await run(lambda: {**pub.status(target), "history": pub.history(url=target, limit=10), "encryption_ready": encryption_ready()})

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

    @app.post("/api/v1/projects/{slug}/publish/connections/check")
    async def publish_connect_check(slug: str, request: Request):
        """The guided connect flow's live check. Tests what was typed, step by step. Saves nothing, returns no secret."""
        body = await body_of(request)
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        settings = body.get("settings") if isinstance(body.get("settings"), dict) else {}
        secret = body.get("secret") if isinstance(body.get("secret"), dict) else {}
        return await run(pub.check_connection, kind=text(body.get("kind"), 20), settings=settings, secret=secret, url=text(body.get("url")))

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
        def publish_and_log():
            entry = pub.publish(
                text(body.get("url")),
                str(body.get("html") or ""),
                text(body.get("connection_id"), 40),
                confirm=confirm,
                options=options,
                fixes=body.get("fixes") if isinstance(body.get("fixes"), list) else None,
            )
            return after_change(ui, slug, pub, entry)

        return await run(publish_and_log)

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
        return await run(lambda: after_change(ui, slug, pub, pub.verify_live(entry_id)))

    @app.post("/api/v1/projects/{slug}/publish/history/{entry_id}/rollback")
    async def publish_rollback(slug: str, entry_id: str, request: Request):
        common._check_header_csrf(ui, request)
        connection_id = None
        if "application/json" in (request.headers.get("content-type") or ""):
            body = await common._json_body(request, 8192)
            connection_id = text(body.get("connection_id"), 40) or None
        pub, blocked = guard(slug)
        if blocked:
            return blocked
        def rollback_and_log():
            return after_change(ui, slug, pub, pub.rollback(entry_id, connection_id))

        return await run(rollback_and_log)


def after_change(ui, slug: str, pub, entry: dict) -> dict:
    """After a publish, a Check again or a rollback: bring the change log and the issue list in line with what is
    really live. Reads the newest saved copy first (another instance may have changed issues or the log while the
    platform call ran). A project deleted meanwhile just skips this."""
    from aeo.ranksim.publishing.store import public_history

    refresh_quietly(ui)
    try:
        project = ui.project(slug)
    except LookupError:
        return entry
    full = pub.store.history_entry(entry["id"])
    if not full:
        return entry
    log_change(project, full)
    sync_fixes(pub, project, full)
    return public_history(pub.store.history_entry(entry["id"]) or full)


def refresh_quietly(ui) -> None:
    cloud = getattr(ui, "cloud", None)
    if cloud is None or not getattr(cloud, "enabled", False):
        return
    try:
        cloud.refresh(force=True)
    except Exception:  # noqa: BLE001 - work on the local copy rather than fail the answer
        log.exception("publish: refreshing from cloud storage failed")


def _share():
    try:
        from aeo.ranksim.plugins import share
    except ImportError:
        return None
    return share


def sync_fixes(pub, project, entry: dict) -> None:
    """Issues fixed by this publish, in the client report (the share plugin's issue status).

    Done when the change is live and the live check confirms every field (a pull request waits for its merge, a cache
    for Check again). Only issues that were not already Done are marked, and their earlier status is kept. A
    finished rollback sets exactly those issues back to that earlier status, unless someone changed them since, and
    logs "Reopened". Idempotent: the entry records what it marked."""
    fixes = entry.get("fixes") or []
    share = _share()
    if not fixes or share is None:
        return
    rolled = bool(entry.get("rolled_back_at"))
    confirmed = (entry.get("verify") or {}).get("status") == "confirmed"
    if rolled:
        if entry.get("fixes_marked") != "done":
            return
        want = "open"
    elif entry.get("status") == "live" and confirmed and not entry.get("rollback_pending"):
        if entry.get("fixes_marked") == "done":
            return
        want = "done"
    else:
        return
    try:
        plans = share.issue_plans(project)
    except Exception:  # noqa: BLE001
        log.exception("publish: reading issue status failed")
        return
    marked, prior = [], dict(entry.get("fixes_prior") or {})
    path = urlsplit(entry.get("url") or "").path or "/"
    for fix in fixes:
        current = plans.get(fix["id"]) or {}
        try:
            if want == "done":
                if current.get("status") == "done":
                    continue  # already done before this publish: not ours to reopen later
                prior[fix["id"]] = current.get("status") or "open"
                share.set_issue_plan(project, fix["id"], "done", plan=current.get("plan") or "", title=fix.get("title") or "")
                marked.append(fix["id"])
            else:
                if fix["id"] not in (entry.get("fixes_marked_ids") or []) or current.get("status") != "done":
                    continue  # not marked by this publish, or changed by someone since
                share.set_issue_plan(project, fix["id"], prior.get(fix["id"]) or "open", plan=current.get("plan") or "", title=fix.get("title") or "")
                share.log_change(project, "issue", f"Reopened: {fix.get('title') or fix['id']}", detail=f"The change to {path} was rolled back.", key=f"reopen:{entry['id']}:{fix['id']}")
                marked.append(fix["id"])
        except Exception:  # noqa: BLE001 - the issue list never breaks a publish
            log.exception("publish: updating issue %s failed", fix["id"])
    with pub.store.lock:
        fresh = pub.store.history_entry(entry["id"]) or entry
        fresh["fixes_marked"] = want
        fresh["fixes_prior"] = prior
        if want == "done":
            fresh["fixes_marked_ids"] = marked
        else:
            fresh["fixes_reopened_ids"] = marked
        pub.store.put_history(fresh)


def log_change(project, entry: dict) -> None:
    """The client change log line for where this entry stands. Only what really went live is public: a pull request,
    a saved draft, a closed pull request or a pending revert are owner-only notes. Keyed, so repeats add nothing."""
    share = _share()
    if share is None:
        return
    path = urlsplit(entry.get("url") or "").path or "/"
    fields = ", ".join(row["label"].lower() for row in entry.get("rows") or [] if row.get("apply"))
    platform = (entry.get("connection") or {}).get("kind", "")
    rollback = entry.get("rollback") or {}
    lines = []
    if entry.get("status") == "live":
        lines.append((True, f"Published changes to {path}: {fields}." if fields else f"Published changes to {path}.", f"publish:{entry['id']}",
                      f"{platform}, live check: {(entry.get('verify') or {}).get('status', '')}"))
    elif entry.get("status") == "pull_request":
        lines.append((False, f"Opened a pull request for {path}.", f"publish-pr:{entry['id']}", platform))
    elif entry.get("status") == "saved":
        lines.append((False, f"Saved changes to {path} as a draft.", f"publish-saved:{entry['id']}", platform))
    if entry.get("rollback_pending"):
        lines.append((False, f"Opened a pull request to undo the change to {path}.", f"publish-revert-pr:{entry['id']}", rollback.get("note", "")))
    if entry.get("rolled_back_at"):
        if rollback.get("status") == "closed":
            lines.append((False, f"Closed the pull request for {path}. Nothing went live.", f"publish-closed:{entry['id']}", platform))
        elif entry.get("status") == "live":
            lines.append((True, f"Rolled back the change to {path}.", f"publish-rollback:{entry['id']}", f"{platform}, {rollback.get('status', '')}".strip(", ")))
    for public, summary, key, detail in lines:
        try:
            share.log_change(project, "publish", summary, detail=detail, public=public, key=key)
        except Exception:  # noqa: BLE001 - the change log never breaks a publish
            log.exception("publish: change log entry failed")


def save_quietly(ui) -> None:
    from aeo.ranksim import plugins

    try:
        plugins.save(ui)
    except Exception:  # noqa: BLE001 - CloudError; the answer still goes out
        log.exception("publish: saving to cloud storage failed")


def after_pull(*, project, report=None, ui=None, **_ignored) -> None:
    """Periodic re-check: every pull re-checks ownership proofs that are older than a day."""
    if ui is None:
        return
    try:
        if publisher_for(ui, project).recheck_stale():
            save_quietly(ui)  # the classic pull does not save after this hook
    except Exception:  # noqa: BLE001 - a plugin hook never breaks a pull
        log.exception("publish: ownership re-check failed")
