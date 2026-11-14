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
