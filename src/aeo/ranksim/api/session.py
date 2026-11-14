"""Who is signed in, and the owner's login and logout. These answer without a login (studio.OPEN_EXACT)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim.api.common import _check_header_csrf, _error, _json_body, _signed_in, _text, google_status, mode_of, storage_kind


def register(app: FastAPI, ui) -> None:
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
        # The limit counts per client as well as overall. Vercel sets x-real-ip itself; a client cannot forge it there.
        client = request.headers.get("x-real-ip") or (request.client.host if request.client else None)
        if await run_in_threadpool(lambda: ui.access.locked_out(client=client)):
            refused = _error(429, "Too many tries. Wait a minute and try again.")
            refused.headers["retry-after"] = str(max(1, ui.access.retry_after(client=client)))
            return refused
        if not await run_in_threadpool(lambda: ui.access.check_password(_text(body.get("password"), 500), client=client)):
            return _error(401, "That password isn't right.")
        response = JSONResponse({"ok": True})
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, ui.access.issue(), max_age=MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")
        return response

    @app.post("/api/v1/logout")
    async def api_logout(request: Request) -> JSONResponse:
        from aeo.ranksim.access import COOKIE

        _check_header_csrf(ui, request)
        try:
            everywhere = bool((await _json_body(request, 1024)).get("everywhere"))
        except Exception:  # noqa: BLE001 - an empty or odd body is a plain sign out
            everywhere = False
        if everywhere and ui.access.enabled and _signed_in(ui, request):
            await run_in_threadpool(ui.access.revoke_all)  # every session on every instance, this one included
        response = JSONResponse({"ok": True, "everywhere": everywhere})
        response.delete_cookie(COOKIE, path="/")
        return response
