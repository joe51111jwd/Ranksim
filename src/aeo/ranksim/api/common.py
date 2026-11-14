"""Helpers shared by every Studio API route, and by plugin routes.

A write route starts with _check_header_csrf(ui, request), then _not_public(ui) when the public demo must not use
it, then reads its body with _json_body(request, limit). Errors are JSON: {"error": "..."} via _error.
"""

from __future__ import annotations

import json
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

CSRF_EXPIRED = "This page has expired. Reload it and try again."


def _serve():
    from aeo.ranksim import serve

    return serve


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


def _not_public(ui) -> JSONResponse | None:
    if ui.public_demo:
        return _error(403, "The public demo is read only. Run your own Ranksim to use real projects.")
    return None


def _split(value) -> list[str]:
    from aeo.ranksim.project import split_searches

    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return split_searches(str(value or ""))


def _join(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


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


def _check_header_csrf(ui, request: Request) -> None:
    from aeo.ranksim.studio import CSRF_HEADER

    token = request.headers.get(CSRF_HEADER, "")
    if not token or not secrets.compare_digest(token, ui.csrf):
        raise HTTPException(status_code=403, detail=CSRF_EXPIRED)


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


def _engine_error(ui, exc: Exception) -> JSONResponse:
    """The 500 every route returns when the engine could not be loaded, scrubbed of keys."""
    return _error(500, ui.scrub(f"The engine could not be loaded: {type(exc).__name__}: {str(exc)[:200]}"))
