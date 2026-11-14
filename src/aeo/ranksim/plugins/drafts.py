"""Saved draft versions for the editor, per project and page.

A version is the editor's draft (the cleaned page with data-rs-id markers) plus the two pages it is measured against:
the source (the page as fetched, scripts and all) and the base (the cleaned source). Sources and bases are large and
shared by many versions, so they are stored once per content hash as blobs, and uploaded only when the server lacks
them. That keeps every request well under hosting body limits.

Layout under ``project.dir / "plugins" / "drafts"``::

    blobs/<sha256>.html             a source or base page
    pages/<pagekey>/page.json       {"url": ...}
    pages/<pagekey>/<id>.json       one version: name, url, created, source, base (hashes), draft

Routes (signed in owner only; writes need the CSRF header; refused in the public demo):

    GET    /api/v1/projects/{slug}/drafts?url=      versions of one page, newest first, without bodies
    POST   /api/v1/projects/{slug}/drafts/blobs     {"text"} -> {"sha"}
    POST   /api/v1/projects/{slug}/drafts           {"url", "name", "source", "base", "draft"} (hashes) -> version
           409 {"missing": [...]} when a blob must be uploaded first
    GET    /api/v1/projects/{slug}/drafts/{id}      one version with its draft, source and base
    DELETE /api/v1/projects/{slug}/drafts/{id}      remove it (and blobs nothing uses any more)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

MAX_DRAFT = 2 * 1024 * 1024
MAX_BLOB = 3 * 1024 * 1024
MAX_VERSIONS = 30
MAX_NAME = 80
BLOB_GRACE_SECONDS = 3600  # a blob uploaded for a save that is still on its way is never collected
ID_RE = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{10}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class DraftError(ValueError):
    """A request the store refuses, with a message fit to show."""

    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def root_of(project) -> Path:
    return Path(project.dir) / "plugins" / "drafts"


def page_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_url(url) -> str:
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")) or len(url) > 2048 or any(ch in url for ch in "\r\n\t "):
        raise DraftError("That page address can't be saved.")
    return url


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def put_blob(project, text) -> str:
    if not isinstance(text, str) or not text:
        raise DraftError("Nothing to store.")
    if len(text.encode("utf-8")) > MAX_BLOB:
        raise DraftError("That page is over 3 MB, so it can't be saved as a version.", 413)
    sha = sha_of(text)
    path = root_of(project) / "blobs" / f"{sha}.html"
    if path.is_file():
        try:
            os.utime(path)  # fresh again, so collection leaves it alone while the save that needs it lands
        except OSError:
            pass
    else:
        _write(path, text)
    return sha


def get_blob(project, sha: str) -> str | None:
    if not SHA_RE.match(sha or ""):
        return None
    try:
        return (root_of(project) / "blobs" / f"{sha}.html").read_text(encoding="utf-8")
    except OSError:
        return None


def _meta(data: dict) -> dict:
    return {key: data.get(key) for key in ("id", "name", "url", "created", "size")}


def _order(data: dict) -> tuple:
    return (int(data.get("seq") or 0), data.get("created") or "", data.get("id") or "")


def list_versions(project, url) -> list[dict]:
    url = _check_url(url)
    folder = root_of(project) / "pages" / page_key(url)
    out = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            if path.name == "page.json":
                continue
            data = _read_json(path)
            if data and data.get("url") == url and ID_RE.match(str(data.get("id", ""))):
                out.append((_order(data), _meta(data)))
    out.sort(key=lambda pair: pair[0], reverse=True)
    return [meta for _, meta in out]


def save_version(project, url, name, source, base, draft, *, now: datetime | None = None) -> dict:
    url = _check_url(url)
    if not isinstance(draft, str) or not draft:
        raise DraftError("There is no draft to save.")
    if len(draft.encode("utf-8")) > MAX_DRAFT:
        raise DraftError("The draft is over 2 MB. Nothing was saved.", 413)
    missing = [field for field, sha in (("source", source), ("base", base)) if not SHA_RE.match(str(sha or "")) or get_blob(project, sha) is None]
    if missing:
        raise DraftError("Upload the page first.", 409, missing=missing)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    clean_name = re.sub(r"\s+", " ", str(name or "")).strip()[:MAX_NAME] or f"Version {stamp.strftime('%b %d %H:%M')}"
    key = page_key(url)
    folder = root_of(project) / "pages" / key
    if not (folder / "page.json").is_file():
        _write(folder / "page.json", json.dumps({"url": url}))
    data = {
        "id": f"{key}-{secrets.token_hex(5)}",
        "name": clean_name,
        "url": url,
        "created": stamp.isoformat(timespec="seconds"),
        "size": len(draft),
        "seq": time.time_ns(),
        "source": source,
        "base": base,
        "draft": draft,
    }
    _write(folder / f"{data['id']}.json", json.dumps(data))
    versions = list_versions(project, url)
    for old in versions[MAX_VERSIONS:]:
        (folder / f"{old['id']}.json").unlink(missing_ok=True)
    if len(versions) > MAX_VERSIONS:
        collect_garbage(project)
    return _meta(data)


def _version_path(project, version_id: str) -> Path:
    if not ID_RE.match(version_id or ""):
        raise DraftError("No such version.", 404)
    return root_of(project) / "pages" / version_id.split("-")[0] / f"{version_id}.json"


def load_version(project, version_id: str) -> dict:
    data = _read_json(_version_path(project, version_id))
    if not data or data.get("id") != version_id:
        raise DraftError("No such version.", 404)
    source = get_blob(project, str(data.get("source") or ""))
    base = get_blob(project, str(data.get("base") or ""))
    if source is None or base is None:
        raise DraftError("This version's page copy is missing.", 410)
    return {**_meta(data), "draft": data.get("draft", ""), "source": source, "base": base}


def delete_version(project, version_id: str) -> None:
    path = _version_path(project, version_id)
    try:
        path.unlink()
    except FileNotFoundError:
        raise DraftError("No such version.", 404) from None
    collect_garbage(project)


def collect_garbage(project, *, grace: float = BLOB_GRACE_SECONDS) -> int:
    """Removes blobs no version refers to and nobody touched in the last `grace` seconds. Returns how many went."""
    root = root_of(project)
    used = set()
    for path in (root / "pages").glob("*/*.json"):
        if path.name == "page.json":
            continue
        data = _read_json(path) or {}
        used.update(str(data.get(field) or "") for field in ("source", "base"))
    gone = 0
    cutoff = time.time() - grace
    for blob in (root / "blobs").glob("*.html"):
        try:
            fresh = blob.stat().st_mtime > cutoff
        except OSError:
            continue
        if blob.stem not in used and not fresh:
            blob.unlink(missing_ok=True)
            gone += 1
    return gone


# -- routes ------------------------------------------------------------------------------------------------


def _helpers():
    try:  # after the foundation refactor
        from aeo.ranksim.api.common import _check_header_csrf, _error, _json_body, _not_public
    except ImportError:  # before it
        from aeo.ranksim.studio import _check_header_csrf, _error, _json_body, _not_public
    return _check_header_csrf, _error, _json_body, _not_public


def register(app, ui) -> None:
    check_csrf, error, json_body, not_public = _helpers()

    def project_or_none(slug: str):
        try:
            return ui.project(slug)
        except LookupError:
            return None

    def fail(exc: DraftError) -> JSONResponse:
        response = error(exc.status, str(exc))
        if exc.extra:
            body = json.loads(response.body)
            body.update(exc.extra)
            return JSONResponse(body, status_code=exc.status)
        return response

    @app.get("/api/v1/projects/{slug}/drafts")
    async def drafts_list(slug: str, url: str = "") -> JSONResponse:
        blocked = not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_none(slug)
        if project is None:
            return error(404, "No such project.")
        try:
            return JSONResponse({"url": url, "versions": await run_in_threadpool(list_versions, project, url)})
        except DraftError as exc:
            return fail(exc)

    @app.post("/api/v1/projects/{slug}/drafts/blobs")
    async def drafts_blob(slug: str, request: Request) -> JSONResponse:
        check_csrf(ui, request)
        blocked = not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_none(slug)
        if project is None:
            return error(404, "No such project.")
        body = await json_body(request, 2 * MAX_BLOB + 64 * 1024)
        try:
            return JSONResponse({"sha": await run_in_threadpool(put_blob, project, body.get("text"))})
        except DraftError as exc:
            return fail(exc)

    @app.post("/api/v1/projects/{slug}/drafts")
    async def drafts_save(slug: str, request: Request) -> JSONResponse:
        check_csrf(ui, request)
        blocked = not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_none(slug)
        if project is None:
            return error(404, "No such project.")
        body = await json_body(request, 2 * MAX_DRAFT + 64 * 1024)
        try:
            saved = await run_in_threadpool(save_version, project, body.get("url"), body.get("name"), body.get("source"), body.get("base"), body.get("draft"))
        except DraftError as exc:
            return fail(exc)
        return JSONResponse(saved)

    @app.get("/api/v1/projects/{slug}/drafts/{version_id}")
    async def drafts_load(slug: str, version_id: str) -> JSONResponse:
        blocked = not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_none(slug)
        if project is None:
            return error(404, "No such project.")
        try:
            return JSONResponse(await run_in_threadpool(load_version, project, version_id))
        except DraftError as exc:
            return fail(exc)

    @app.delete("/api/v1/projects/{slug}/drafts/{version_id}")
    async def drafts_delete(slug: str, version_id: str, request: Request) -> JSONResponse:
        check_csrf(ui, request)
        blocked = not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_none(slug)
        if project is None:
            return error(404, "No such project.")
        try:
            await run_in_threadpool(delete_version, project, version_id)
        except DraftError as exc:
            return fail(exc)
        return JSONResponse({"ok": True})
