"""Client delivery: read-only share links, the client report, CSV export, agency branding and a change log.

The owner creates a link per project. ``/r/<token>`` serves a small static page (static/share/) that reads a
whitelisted JSON copy of the report: overview numbers, positions per search, issues and what is being done about
them, competitors and a short change log. No editor, no settings, no other projects, no internal notes, no page
HTML, no keys. Every Google number carries its data date; every model number is labeled PREDICTED; no accuracy
score of any kind is sent.

Storage:
  <projects root>/_plugins/share/   branding.json, logo.<ext>, secret.key  (not a project: no project.json)
  <project>/plugins/share/          links.json, changelog.jsonl, issues.json, views.json (when each link was last opened)

Other plugins add change log entries with ``log_change(project, kind, summary, detail=..., key=...)``.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

STATIC = Path(__file__).resolve().parent.parent / "static"
SHARE_STATIC = STATIC / "share"
PUBLIC_PREFIX = "/r/"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{22}$")
LINK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
ISSUE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")
MAX_LINKS = 50
MAX_EXPIRY_DAYS = 365
MAX_LOG_ENTRIES = 500
MAX_LOGO_BYTES = 200 * 1024
MAX_AGENCY_CHARS = 80
REPORT_TTL_SECONDS = 30.0
REQUESTS_PER_MINUTE = 300
BAD_TOKENS_PER_MINUTE = 20
LAST_USED_EVERY_SECONDS = 600
ROBOTS = "noindex, nofollow, noarchive, nosnippet"
SHARE_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; "
    "form-action 'none'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
GONE_TEXT = "This report link has expired or was turned off. Ask for a new one."
LOGO_TYPES = {
    "image/png": ("png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ("jpg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "image/webp": ("webp", lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
}
LOGO_MIME = {ext: mime for mime, (ext, _check) in LOGO_TYPES.items()}
ASSETS = {
    "share.css": ("text/css; charset=utf-8", SHARE_STATIC / "share.css"),
    "share.js": ("application/javascript; charset=utf-8", SHARE_STATIC / "share.js"),
    "sans.woff2": ("font/woff2", STATIC / "fonts" / "sans.woff2"),
    "mono.woff2": ("font/woff2", STATIC / "fonts" / "mono.woff2"),
}
KINDS = {
    "analysis": "Analysis",
    "google": "Google data",
    "test": "Test",
    "publish": "Published",
    "issue": "Issue",
    "share": "Report",
    "note": "Note",
}
STATUSES = {"open": "Not started", "planned": "Planned", "doing": "In progress", "done": "Done"}
SEVERITIES = ("high", "medium", "low")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^(\d{8})T(\d{6})Z", text)
    if match:
        try:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _text(value, limit: int) -> str:
    text = CONTROL_RE.sub("", str(value if value is not None else "")).strip()
    return text[:limit]


def _int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _url(value) -> str | None:
    text = _text(value, 600)
    return text if re.match(r"^https?://[^\s<>\"']+$", text) else None


def _domain(value) -> str:
    text = _text(value, 200).lower()
    return text if re.match(r"^[a-z0-9.-]+$", text) else ""


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def global_dir(root) -> Path:
    """Agency-wide data. The leading underscore keeps it out of the project list (slugs start with a letter or digit)."""
    return Path(root) / "_plugins" / "share"


def project_dir(project) -> Path:
    return Path(project.dir) / "plugins" / "share"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# signing key and tokens
# ---------------------------------------------------------------------------

_keys: dict[str, bytes] = {}


def signing_key(root, *, create: bool = True) -> bytes | None:
    """The link signing key. With RANKSIM_SECRET set (required for several instances) it is derived from that alone.
    Otherwise a random key kept with the data, re-read whenever the file changes (a cloud refresh replaces it).
    Read paths pass create=False, so a public GET never mints a key."""
    env = os.environ.get("RANKSIM_SECRET", "")
    if env:
        return hashlib.sha256(f"ranksim-share|env|{env}".encode("utf-8")).digest()
    base = global_dir(root)
    path = base / "secret.key"
    with _lock:
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        cache_key = f"{base.resolve()}|{stamp}"
        cached = _keys.get(cache_key)
        if cached is not None:
            return cached
        stored = ""
        try:
            stored = path.read_text(encoding="ascii").strip()
        except OSError:
            stored = ""
        if not re.match(r"^[0-9a-f]{64}$", stored):
            if not create:
                return None
            stored = secrets.token_hex(32)
            base.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".secret.{secrets.token_hex(4)}.tmp")
            tmp.write_text(stored, encoding="ascii")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            stamp = path.stat().st_mtime_ns
            cache_key = f"{base.resolve()}|{stamp}"
        key = hashlib.sha256(f"ranksim-share|file|{stored}".encode("utf-8")).digest()
        _keys.clear()
        _keys[cache_key] = key
        return key


def _signature(root, slug: str, link_id: str, *, create: bool = True) -> str | None:
    key = signing_key(root, create=create)
    if key is None:
        return None
    digest = hmac.new(key, f"share|{slug}|{link_id}".encode("utf-8"), hashlib.sha256).digest()
    return _b64(digest)[:22]


def token_for(root, slug: str, link_id: str) -> str:
    return f"{link_id}.{_signature(root, slug, link_id)}"


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


def _links_path(project) -> Path:
    return project_dir(project) / "links.json"


def _load_links(project) -> list[dict]:
    data = _read_json(_links_path(project), [])
    return [row for row in data if isinstance(row, dict) and LINK_ID_RE.match(str(row.get("id") or ""))] if isinstance(data, list) else []


def link_status(link: dict, now: datetime | None = None) -> str:
    if link.get("revoked"):
        return "revoked"
    expires = _parse_time(link.get("expires"))
    if expires is not None and expires <= (now or _now()):
        return "expired"
    return "active"


def _public_link(root, project, link: dict, now: datetime | None = None, views: dict | None = None) -> dict:
    token = token_for(root, project.slug, link["id"])
    seen = (views if views is not None else _load_views(project)).get(link["id"]) or link.get("last_used")
    return {
        "id": link["id"],
        "label": link.get("label") or "",
        "created": link.get("created"),
        "expires": link.get("expires"),
        "revoked": link.get("revoked"),
        "last_used": seen,
        "status": link_status(link, now),
        "token": token,
        "path": f"{PUBLIC_PREFIX}{token}",
    }


def create_link(root, project, *, label: str = "", expires_days=None, now: datetime | None = None) -> dict:
    now = now or _now()
    days = None
    if expires_days not in (None, "", 0, "0"):
        text = str(expires_days).strip()
        days = int(text) if not isinstance(expires_days, (bool, float)) and re.match(r"^[0-9]{1,4}$", text) else -1
    if days is not None and not 1 <= days <= MAX_EXPIRY_DAYS:
        raise ValueError(f"Pick an expiry between 1 and {MAX_EXPIRY_DAYS} days, or none.")
    with _lock:
        links = _load_links(project)
        if sum(1 for link in links if link_status(link, now) == "active") >= MAX_LINKS:
            raise ValueError(f"This project already has {MAX_LINKS} active links. Turn one off first.")
        link = {
            "id": _b64(secrets.token_bytes(18)),
            "label": _text(label, 80),
            "created": _iso(now),
            "expires": _iso(now + timedelta(days=days)) if days else None,
            "revoked": None,
            "last_used": None,
        }
        links.append(link)
        _write_json(_links_path(project), links)
    log_change(project, "share", "Client report link created", detail=f"Link {link['label'] or link['id'][:6]}", public=False)
    return _public_link(root, project, link, now)


def list_links(root, project, now: datetime | None = None) -> list[dict]:
    views = _load_views(project)
    return [_public_link(root, project, link, now, views) for link in reversed(_load_links(project))]


def revoke_link(project, link_id: str, now: datetime | None = None) -> bool:
    if not LINK_ID_RE.match(link_id or ""):
        return False
    with _lock:
        links = _load_links(project)
        for link in links:
            if link["id"] == link_id:
                if not link.get("revoked"):
                    link["revoked"] = _iso(now or _now())
                    _write_json(_links_path(project), links)
                    log_change(project, "share", "Client report link turned off", detail=f"Link {link.get('label') or link_id[:6]}", public=False)
                return True
    return False


def resolve_token(ui, token: str, now: datetime | None = None):
    """(project, link) for a live token, else None. Format, signature, stored link, expiry and revocation all count."""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None
    link_id, _dot, signature = token.partition(".")
    now = now or _now()
    root = Path(ui.root)
    try:
        folders = sorted(path for path in root.iterdir() if path.is_dir() and SLUG_RE.match(path.name))
    except OSError:
        return None
    for folder in folders:
        links_file = folder / "plugins" / "share" / "links.json"
        if not links_file.is_file():
            continue
        expected = _signature(root, folder.name, link_id, create=False)
        if expected is None:
            return None
        if not hmac.compare_digest(signature, expected):
            continue
        try:
            project = ui.project(folder.name)
        except Exception:  # noqa: BLE001 - a missing or blocked project resolves to nothing
            return None
        for link in _load_links(project):
            if hmac.compare_digest(link["id"], link_id):
                return (project, link) if link_status(link, now) == "active" else None
        return None
    return None


def _views_path(project) -> Path:
    return project_dir(project) / "views.json"


def _load_views(project) -> dict[str, str]:
    data = _read_json(_views_path(project), {})
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if LINK_ID_RE.match(str(key)) and isinstance(value, str)}


def touch_link(project, link_id: str, now: datetime | None = None) -> bool:
    """Record when a client last opened the link, at most every ten minutes. True when it wrote.

    The time goes to views.json, never to links.json. A public view writes on whichever instance served it and is not
    saved to cloud storage then, and cloud storage merges a file both sides changed by keeping the local copy. If a
    view rewrote links.json, a revocation saved by another instance could be dropped there (and saved back later),
    so a turned-off link would keep working."""
    now = now or _now()
    with _lock:
        link = next((row for row in _load_links(project) if row["id"] == link_id), None)
        if link is None:
            return False
        views = _load_views(project)
        last = _parse_time(views.get(link_id) or link.get("last_used"))
        if last is not None and (now - last).total_seconds() < LAST_USED_EVERY_SECONDS:
            return False
        views[link_id] = _iso(now)
        try:
            _write_json(_views_path(project), views)
        except OSError:
            return False
        return True


# ---------------------------------------------------------------------------
# branding
# ---------------------------------------------------------------------------


def load_branding(root) -> dict:
    data = _read_json(global_dir(root) / "branding.json", {})
    data = data if isinstance(data, dict) else {}
    logo = logo_file(root)
    return {"agency": _text(data.get("agency"), MAX_AGENCY_CHARS), "logo": logo is not None, "updated": data.get("updated")}


def save_branding(root, agency) -> dict:
    name = re.sub(r"\s+", " ", _text(agency, 500))
    if len(name) > MAX_AGENCY_CHARS:
        raise ValueError(f"Keep the agency name under {MAX_AGENCY_CHARS} characters.")
    with _lock:
        _write_json(global_dir(root) / "branding.json", {"agency": name, "updated": _iso(_now())})
    return load_branding(root)


def check_logo(data: bytes, content_type: str) -> str:
    """The file extension for a valid logo, or ValueError with a plain reason."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in LOGO_TYPES:
        raise ValueError("Use a PNG, JPEG or WebP image.")
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"Keep the logo under {MAX_LOGO_BYTES // 1024} KB.")
    ext, looks_right = LOGO_TYPES[mime]
    if not looks_right(data):
        raise ValueError("That file is not the image type it says it is.")
    return ext


def save_logo(root, data: bytes, content_type: str) -> dict:
    ext = check_logo(data, content_type)
    base = global_dir(root)
    with _lock:
        base.mkdir(parents=True, exist_ok=True)
        tmp = base / f".logo.{secrets.token_hex(4)}.tmp"
        tmp.write_bytes(data)
        for old in LOGO_MIME:
            if old != ext:
                (base / f"logo.{old}").unlink(missing_ok=True)
        os.replace(tmp, base / f"logo.{ext}")
    return load_branding(root)


def delete_logo(root) -> dict:
    with _lock:
        for ext in LOGO_MIME:
            (global_dir(root) / f"logo.{ext}").unlink(missing_ok=True)
    return load_branding(root)


def logo_file(root) -> tuple[Path, str] | None:
    for ext, mime in LOGO_MIME.items():
        path = global_dir(root) / f"logo.{ext}"
        if path.is_file() and path.stat().st_size <= MAX_LOGO_BYTES:
            return path, mime
    return None


# ---------------------------------------------------------------------------
# change log
# ---------------------------------------------------------------------------


def _log_path(project) -> Path:
    return project_dir(project) / "changelog.jsonl"


def _load_log(project) -> list[dict]:
    rows = []
    try:
        lines = _log_path(project).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("at") and row.get("summary"):
            rows.append(row)
    return rows


def log_change(project, kind: str, summary: str, *, detail: str = "", public: bool = True, key: str | None = None, when=None) -> dict | None:
    """Add one change log entry. ``key`` makes it idempotent (a second call with the same key does nothing).

    ``summary`` is client facing (short, plain). ``detail`` is for the owner only. ``public=False`` keeps the
    whole entry away from the client report."""
    summary = _text(summary, 200)
    if not summary:
        return None
    moment = _parse_time(when) if when is not None and not isinstance(when, datetime) else when
    entry = {
        "id": secrets.token_hex(6),
        "at": _iso(moment or _now()),
        "kind": kind if kind in KINDS else "note",
        "summary": summary,
        "detail": _text(detail, 1000),
        "public": bool(public),
        "key": _text(key, 120) or None,
    }
    with _lock:
        rows = _load_log(project)
        if entry["key"] and any(row.get("key") == entry["key"] for row in rows):
            return None
        rows.append(entry)
        rows = rows[-MAX_LOG_ENTRIES:]
        path = _log_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        os.replace(tmp, path)
    return entry


def _pull_entry(pull_id: str, google_kind: str, with_results, when) -> dict:
    count = _int(with_results) or 0
    if google_kind == "api":
        kind, summary = "google", f"Google data refreshed for {count} search{'es' if count != 1 else ''}"
    elif google_kind == "import":
        kind, summary = "analysis", f"Search positions imported for {count} search{'es' if count != 1 else ''}"
    elif google_kind == "carried":
        kind, summary = "analysis", "Site pages read again"
    else:
        kind, summary = "analysis", "Site analysis run"
    return {"kind": kind, "summary": summary, "detail": f"Pull {pull_id}", "key": f"pull:{pull_id}", "when": when}


def _derived(project) -> list[dict]:
    """Entries for work already on disk: finished pulls and draft tests."""
    found: list[dict] = []
    try:
        from aeo.ranksim.project import list_pulls

        for info in list_pulls(project):
            if info.finished:
                item = _pull_entry(info.pull_id, info.google_kind, info.searches_with_results, info.written_at or info.pull_id)
                found.append(item)
    except Exception:  # noqa: BLE001 - the log shows what it can
        pass
    try:
        from aeo.ranksim import workspace as ws

        for run in ws.list_draft_runs(project, 30):
            before = (run.get("before") or {}).get("position")
            after = (run.get("after") or {}).get("position")
            of = (run.get("after") or {}).get("of")
            detail = f"PREDICTED position {before or 'none'} before, {after or 'none'} after, of {of or '?'}"
            found.append(
                {
                    "kind": "test",
                    "summary": f'Page change tested for "{_text(run.get("search"), 120)}"',
                    "detail": detail,
                    "key": f"test:{run.get('id')}",
                    "when": run.get("created") or run.get("id"),
                    "public": False,  # exploratory tests stay with the owner
                }
            )
    except Exception:  # noqa: BLE001
        pass
    return found


def changelog(project, *, public_only: bool = False, limit: int = 100) -> list[dict]:
    rows = _load_log(project)
    keys = {row.get("key") for row in rows if row.get("key")}
    for item in _derived(project):
        if item["key"] in keys:
            continue
        moment = _parse_time(item["when"])
        if moment is None:
            continue
        rows.append({"id": item["key"], "at": _iso(moment), "kind": item["kind"], "summary": item["summary"], "detail": item["detail"], "public": item.get("public", True), "key": item["key"]})
    rows.sort(key=lambda row: row.get("at") or "", reverse=True)
    out = []
    for row in rows:
        if public_only and not row.get("public"):
            continue
        kind = row.get("kind") if row.get("kind") in KINDS else "note"
        entry = {"at": row.get("at"), "date": str(row.get("at") or "")[:10], "kind": kind, "label": KINDS[kind], "summary": _text(row.get("summary"), 200)}
        if not public_only:
            entry.update({"id": row.get("id"), "detail": row.get("detail") or "", "public": bool(row.get("public"))})
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def after_pull(*, project, report, ui=None) -> None:
    """Plugin hook: every pull or analysis that finished becomes a change log entry."""
    def get(name, default=None):
        if isinstance(report, dict):
            return report.get(name, default)
        return getattr(report, name, default)

    pull_id = _text(get("pull_id"), 80)
    if not pull_id:
        return
    item = _pull_entry(pull_id, _text(get("google_kind"), 20), get("searches_with_results", get("searches")), None)
    log_change(project, item["kind"], item["summary"], detail=item["detail"], key=item["key"])
    if ui is not None:
        _forget(ui, project.slug)


# ---------------------------------------------------------------------------
# issue plans: what is being done about each issue
# ---------------------------------------------------------------------------


def _plans_path(project) -> Path:
    return project_dir(project) / "issues.json"


def issue_plans(project) -> dict:
    data = _read_json(_plans_path(project), {})
    return {key: value for key, value in data.items() if ISSUE_ID_RE.match(str(key)) and isinstance(value, dict)} if isinstance(data, dict) else {}


def set_issue_plan(project, issue_id: str, status: str, plan: str = "", title: str = "") -> dict:
    if not ISSUE_ID_RE.match(issue_id or ""):
        raise ValueError("No such issue.")
    if status not in STATUSES:
        raise ValueError("Pick a status: " + ", ".join(STATUSES.values()) + ".")
    with _lock:
        plans = issue_plans(project)
        before = (plans.get(issue_id) or {}).get("status")
        row = {"status": status, "plan": _text(plan, 400), "updated": _iso(_now())}
        plans[issue_id] = row
        _write_json(_plans_path(project), plans)
    if status == "done" and before != "done":
        name = _text(title, 160) or "an issue"
        log_change(project, "issue", f"Issue resolved: {name}", detail=f"Issue {issue_id}")
    return row


# ---------------------------------------------------------------------------
# the client report (a whitelist, never a filtered copy of the owner report)
# ---------------------------------------------------------------------------


def _common():
    try:
        from aeo.ranksim.api import common  # after P0

        return common
    except ImportError:
        from aeo.ranksim import studio

        return studio


def _project_report_fn():
    try:
        from aeo.ranksim.api.projects import project_report  # after P0

        return project_report
    except ImportError:
        from aeo.ranksim.studio import project_report

        return project_report


def _state(ui) -> dict:
    state = getattr(ui, "_share_state", None)
    if state is None:
        state = {
            "cache": {},
            "requests": RateLimiter(REQUESTS_PER_MINUTE),
            "bad": RateLimiter(BAD_TOKENS_PER_MINUTE),
        }
        ui._share_state = state
    return state


def _forget(ui, slug: str) -> None:
    _state(ui)["cache"].pop(slug, None)


_build_locks: dict[str, threading.Lock] = {}


def owner_report(ui, slug: str) -> dict:
    """The owner report, cached briefly. One build per project at a time: concurrent callers wait for it."""
    state = _state(ui)
    with _lock:
        build_lock = _build_locks.setdefault(f"{id(ui)}|{slug}", threading.Lock())
    with build_lock:
        hit = state["cache"].get(slug)
        if hit is not None and time.monotonic() - hit[0] < REPORT_TTL_SECONDS:
            return hit[1]
        data = _project_report_fn()(ui, slug)
        state["cache"][slug] = (time.monotonic(), data)
        return data


def _search_dates(project) -> dict[str, tuple[str, str]]:
    """search -> (sortable pull time, label). Searches can carry results pulled on different days."""
    try:
        from aeo.ranksim.project import PULL_DATE_NOT_RECORDED, latest_google

        google = latest_google(project)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for entry in (google.searches if google is not None else []):
        if not entry.results:
            continue
        label = _text(entry.pulled_label, 40)
        if not entry.pulled_at or label == PULL_DATE_NOT_RECORDED:
            label = ""
        out[entry.search] = (str(entry.pulled_at or ""), label)
    return out


def client_report(ui, project, *, preview: bool = False) -> dict:
    report = owner_report(ui, project.slug)
    plans = issue_plans(project)
    dated = _search_dates(project)
    branding = load_branding(ui.root)
    data = report.get("data") or {}
    kind = _text(data.get("kind"), 40)
    labels = [label for _stamp, label in sorted({value for value in dated.values() if value[1]})]
    if not labels:
        google_date = ""
    elif len(labels) == 1:
        google_date = labels[0]
    else:
        google_date = f"{labels[0]} to {labels[-1]}"
    kpis = report.get("kpis") or {}
    rankings = []
    for row in report.get("rankings") or []:
        if not isinstance(row, dict):
            continue
        predicted = row.get("predicted") if isinstance(row.get("predicted"), dict) else None
        position = _int((predicted or {}).get("position"))
        rankings.append(
            {
                "q": _text(row.get("q"), 200),
                "position": _int(row.get("position")),
                "url": _url(row.get("url")) if _int(row.get("position")) else None,
                "checked": bool(row.get("checked")),
                "date": dated.get(str(row.get("q") or ""), ("", ""))[1],
                "predicted": {"position": position, "of": _int(predicted.get("of")), "label": "PREDICTED"} if predicted and position and _int(predicted.get("of")) else None,
                "top": [
                    {
                        "position": _int(item.get("position")),
                        "domain": _domain(item.get("domain")),
                        "title": _text(item.get("title"), 160),
                        "url": _url(item.get("url")),
                        "you": bool(item.get("you")),
                        "competitor": bool(item.get("competitor")),
                    }
                    for item in (row.get("top") or [])[:10]
                    if isinstance(item, dict)
                ],
            }
        )
    issues = []
    for issue in report.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        issue_id = _text(issue.get("id"), 120)
        plan = plans.get(issue_id) or {}
        status = plan.get("status") if plan.get("status") in STATUSES else "open"
        issues.append(
            {
                "id": issue_id,
                "severity": issue.get("severity") if issue.get("severity") in SEVERITIES else "low",
                "title": _text(issue.get("title"), 200),
                "comment": _text(issue.get("comment"), 600),
                "fix": _text(issue.get("fix"), 400),
                "page": _url(issue.get("page")),
                "search": _text(issue.get("search"), 200) or None,
                "status": status,
                "status_label": STATUSES[status],
                "plan": _text(plan.get("plan"), 400),
            }
        )
    share = [
        {
            "domain": _domain(row.get("domain")),
            "slots": _int(row.get("slots")),
            "searches": _int(row.get("searches")),
            "best": _int(row.get("best")),
            "average": _num(row.get("average")),
            "you": bool(row.get("you")),
            "competitor": bool(row.get("competitor")),
        }
        for row in (report.get("share") or [])[:12]
        if isinstance(row, dict)
    ]
    demo = kind == "synthetic"
    source = "Demo" if demo else "Google"
    if google_date:
        date_note = f"{source} positions are from results pulled {google_date}."
    elif report.get("search_data"):
        date_note = f"{source} positions are shown; the date they were pulled was not recorded."
    else:
        date_note = "No search results have been pulled yet."
    return {
        "preview": bool(preview),
        "agency": {"name": branding["agency"], "logo": branding["logo"]},
        "name": _text(report.get("name"), 120),
        "site": _domain(report.get("site")) or _text(report.get("site"), 120),
        "report_date": _iso(_now())[:10],
        "google_date": google_date,
        "source": source,
        "data": {
            "kind": kind,
            "label": _text(data.get("short"), 60),
            "demo": demo,
            "note": "Demo data. Invented sites and positions, not Google results." if demo else "",
        },
        "search_data": bool(report.get("search_data")),
        "predictions": any(row["predicted"] for row in rankings),
        "kpis": {key: _num(kpis.get(key)) for key in ("searches", "checked", "top10", "top3", "average", "issues", "high")},
        "rankings": rankings,
        "issues": issues,
        "share": share,
        "competitors": [domain for domain in (_domain(item) for item in (report.get("competitors") or [])[:20]) if domain],
        "changes": changelog(project, public_only=True, limit=15),
        "notes": [
            date_note,
            "PREDICTED positions are Ranksim's estimate among the stored pages for that search. They are not Google ranks.",
        ],
    }


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return out.getvalue()


def rankings_csv(data: dict) -> str:
    rows = []
    for row in data["rankings"]:
        predicted = row.get("predicted") or {}
        leader = next((item for item in row["top"] if item.get("position") == 1), None)
        rows.append(
            [
                row["q"],
                row["position"] if row["position"] else ("not in top 10" if row["checked"] else "not checked"),
                row.get("date") or "",
                row["url"] or "",
                predicted.get("position") or "",
                predicted.get("of") or "",
                (leader or {}).get("domain", ""),
            ]
        )
    source = data.get("source") or "Google"
    return _csv(["Search", f"{source} position", f"{source} data date", "Page", "PREDICTED position", "PREDICTED of", f"{source} #1"], rows)


def issues_csv(data: dict) -> str:
    rows = [[i["severity"], i["title"], i["page"] or "", i["search"] or "", i["comment"], i["fix"], i["status_label"], i["plan"]] for i in data["issues"]]
    return _csv(["Severity", "Issue", "Page", "Search", "Comment", "Fix", "Status", "Plan"], rows)


def _filename(data: dict, what: str) -> str:
    base = re.sub(r"[^a-z0-9.-]+", "-", (data.get("site") or "report").lower()).strip("-.") or "report"
    return f"{base}-{what}-{data['report_date']}.csv"


# ---------------------------------------------------------------------------
# rate limit
# ---------------------------------------------------------------------------


class RateLimiter:
    """A sliding one-minute window per key. Bounded memory: the oldest keys are dropped past max_keys."""

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 10000) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    for stale in list(self._hits)[: self.max_keys // 10 or 1]:
                        self._hits.pop(stale, None)
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            return bool(hits) and sum(1 for stamp in hits if now - stamp < self.window) >= self.limit


def client_address(request) -> str:
    """The caller's address. Behind Vercel the edge sets x-real-ip and overwrites x-forwarded-for; elsewhere the socket."""
    if os.environ.get("VERCEL"):
        forwarded = request.headers.get("x-real-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0]
        if forwarded.strip():
            return _bucket(forwarded.strip()[:64])
    client = getattr(request, "client", None)
    address = (client.host if client else "") or "unknown"
    return _bucket(address)


def _bucket(address: str) -> str:
    """IPv6 callers are limited per /64, the block one subscriber usually holds."""
    if ":" in address:
        import ipaddress

        try:
            return str(ipaddress.ip_network(f"{address.strip('[]')}/64", strict=False))
        except ValueError:
            return address
    return address


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


def _asset_version() -> str:
    digest = hashlib.sha256()
    for name in ("share.css", "share.js"):
        try:
            digest.update(ASSETS[name][1].read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:10]


def csp(path: str) -> str | None:
    """Plugin hook: the policy for the public share pages. Everything else is left to the core."""
    return SHARE_CSP if isinstance(path, str) and path.startswith(PUBLIC_PREFIX) else None


def page_html() -> str:
    return (SHARE_STATIC / "index.html").read_text(encoding="utf-8").replace("{{v}}", _asset_version())


def register(app, ui) -> None:
    common = _common()
    # GuardMiddleware adds no-referrer, no-store, nosniff, DENY, and SHARE_CSP through the csp() hook below.
    public_headers = {"x-robots-tag": ROBOTS}

    def public(endpoint):
        """Open to signed-out callers. Each such route verifies the signed token itself and answers 404 without it
        (the asset route serves only fixed static files that hold no data)."""
        endpoint.login_exempt = True
        return endpoint

    def remember_view(project, link_id: str) -> None:
        # Written on this instance only, never saved to cloud storage from here: this is a signed-out request, and
        # anyone holding a link could otherwise spend Blob writes (Vercel Hobby blocks Blob for 30 days past its cap).
        # The time goes up with this instance's next owner save; until then the list may show an older time.
        touch_link(project, link_id)

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    def owner_project(slug: str):
        try:
            return ui.project(slug)
        except LookupError:
            return None

    def gone(as_json: bool) -> Response:
        if as_json:
            return JSONResponse({"error": GONE_TEXT}, status_code=404, headers=public_headers)
        return Response(GONE_TEXT, status_code=404, media_type="text/plain; charset=utf-8", headers=public_headers)

    def limited(request: Request) -> Response | None:
        address = client_address(request)
        state = _state(ui)
        if not state["requests"].allow(address):
            return Response("Too many requests. Wait a minute and try again.", status_code=429, media_type="text/plain; charset=utf-8", headers={**public_headers, "retry-after": "60"})
        return None

    def too_many() -> Response:
        return Response("Too many requests. Wait a minute and try again.", status_code=429, media_type="text/plain; charset=utf-8", headers={**public_headers, "retry-after": "60"})

    def bad_attempt(request: Request) -> Response | None:
        """Count a dead or unknown token. Only failures are limited, so nobody can lock clients out of a live link."""
        address = client_address(request)
        bad = _state(ui)["bad"]
        if bad.blocked(address):
            return too_many()
        bad.allow(address)
        return None

    async def resolve(request: Request, token: str):
        blocked = limited(request)
        if blocked is not None:
            return None, blocked
        found = await run_in_threadpool(resolve_token, ui, token)
        if found is None:
            return None, bad_attempt(request)
        return found, None

    def csv_response(text: str, filename: str, public: bool) -> Response:
        headers = {"content-disposition": f'attachment; filename="{filename}"'}
        if public:
            headers.update(public_headers)
        return Response("﻿" + text, media_type="text/csv; charset=utf-8", headers=headers)

    # -- public: the client report ----------------------------------------------------------------

    @public
    @app.get("/r/assets/{name}")
    def share_asset(name: str, request: Request) -> Response:
        blocked = limited(request)
        if blocked is not None:
            return blocked
        if name not in ASSETS or not ASSETS[name][1].is_file():
            return Response("Not found", status_code=404, media_type="text/plain", headers=public_headers)
        mime, path = ASSETS[name]
        return FileResponse(path, media_type=mime, headers=public_headers)

    @public
    @app.get("/r/{token}")
    async def share_page(token: str, request: Request) -> Response:
        found, blocked = await resolve(request, token)
        if blocked is not None:
            return blocked
        if found is None:
            return gone(False)
        return HTMLResponse(page_html(), headers=public_headers)

    @public
    @app.get("/r/{token}/data")
    async def share_data(token: str, request: Request) -> Response:
        found, blocked = await resolve(request, token)
        if blocked is not None:
            return blocked
        if found is None:
            return gone(True)
        project, link = found
        await run_in_threadpool(remember_view, project, link["id"])
        try:
            data = await run_in_threadpool(client_report, ui, project)
        except Exception:  # noqa: BLE001 - never echo internals to a client
            return JSONResponse({"error": "The report could not be built right now. Try again later."}, status_code=503, headers=public_headers)
        return JSONResponse(data, headers=public_headers)

    @public
    @app.get("/r/{token}/logo")
    async def share_logo(token: str, request: Request) -> Response:
        found, blocked = await resolve(request, token)
        if blocked is not None:
            return blocked
        logo = logo_file(ui.root) if found is not None else None
        if logo is None:
            return gone(False)
        return FileResponse(logo[0], media_type=logo[1], headers=public_headers)

    @public
    @app.get("/r/{token}/{what}.csv")
    async def share_csv(token: str, what: str, request: Request) -> Response:
        found, blocked = await resolve(request, token)
        if blocked is not None:
            return blocked
        if found is None or what not in {"rankings", "issues"}:
            return gone(False)
        try:
            data = await run_in_threadpool(client_report, ui, found[0])
        except Exception:  # noqa: BLE001
            return Response("The report could not be built right now.", status_code=503, media_type="text/plain", headers=public_headers)
        text = rankings_csv(data) if what == "rankings" else issues_csv(data)
        return csv_response(text, _filename(data, what), True)

    @public
    @app.get("/r/{rest:path}")
    def share_unknown(rest: str, request: Request) -> Response:
        """Anything else under /r/ gets the same answer as a dead link."""
        blocked = limited(request)
        if blocked is not None:
            return blocked
        return bad_attempt(request) or gone(rest.endswith("/data"))

    # -- owner: links, report preview, CSV, issue plans, change log ---------------------------------

    @app.get("/report/{slug}")
    def report_preview(slug: str) -> Response:
        if owner_project(slug) is None:
            return Response("Not found", status_code=404, media_type="text/plain")
        return HTMLResponse(page_html(), headers={"x-robots-tag": ROBOTS})

    @app.get("/api/v1/projects/{slug}/share")
    def api_links(slug: str) -> JSONResponse:
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        if ui.public_demo:
            return JSONResponse({"links": [], "branding": load_branding(ui.root), "read_only": True})
        return JSONResponse({"links": list_links(ui.root, project), "branding": load_branding(ui.root), "read_only": False})

    @app.post("/api/v1/projects/{slug}/share")
    async def api_create_link(slug: str, request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        body = await common._json_body(request, 8192)
        try:
            link = create_link(ui.root, project, label=body.get("label") or "", expires_days=body.get("expires_days"))
        except ValueError as exc:
            return error(400, str(exc))
        return JSONResponse(link)

    @app.post("/api/v1/projects/{slug}/share/{link_id}/revoke")
    async def api_revoke_link(slug: str, link_id: str, request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        if not revoke_link(project, link_id):
            return error(404, "No such link.")
        return JSONResponse({"ok": True, "links": list_links(ui.root, project)})

    @app.get("/api/v1/projects/{slug}/client-report")
    async def api_client_preview(slug: str) -> JSONResponse:
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        return JSONResponse(await run_in_threadpool(lambda: client_report(ui, project, preview=True)))

    @app.get("/api/v1/projects/{slug}/export/{what}.csv")
    async def api_export(slug: str, what: str) -> Response:
        project = owner_project(slug)
        if project is None or what not in {"rankings", "issues"}:
            return error(404, "No such export.")
        data = await run_in_threadpool(client_report, ui, project)
        return csv_response(rankings_csv(data) if what == "rankings" else issues_csv(data), _filename(data, what), False)

    @app.get("/api/v1/projects/{slug}/changelog")
    def api_changelog(slug: str) -> JSONResponse:
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        return JSONResponse({"entries": changelog(project), "kinds": KINDS})

    @app.post("/api/v1/projects/{slug}/changelog")
    async def api_add_note(slug: str, request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        body = await common._json_body(request, 8192)
        entry = log_change(project, "note", body.get("summary") or "", detail=body.get("detail") or "", public=body.get("public") is True)
        if entry is None:
            return error(400, "Write a short note first.")
        return JSONResponse({"ok": True, "entries": changelog(project)})

    @app.get("/api/v1/projects/{slug}/issue-plans")
    def api_issue_plans(slug: str) -> JSONResponse:
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        return JSONResponse({"plans": issue_plans(project), "statuses": STATUSES})

    @app.post("/api/v1/projects/{slug}/issues/{issue_id}/plan")
    async def api_set_plan(slug: str, issue_id: str, request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        project = owner_project(slug)
        if project is None:
            return error(404, "No such project.")
        body = await common._json_body(request, 8192)
        try:
            row = set_issue_plan(project, issue_id, _text(body.get("status"), 20), body.get("plan") or "", body.get("title") or "")
        except ValueError as exc:
            return error(400, str(exc))
        _forget(ui, slug)
        return JSONResponse({"ok": True, "plan": row})

    # -- owner: agency branding -------------------------------------------------------------------

    @app.get("/api/v1/branding")
    def api_branding() -> JSONResponse:
        return JSONResponse(load_branding(ui.root))

    @app.post("/api/v1/branding")
    async def api_save_branding(request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        body = await common._json_body(request, 8192)
        try:
            return JSONResponse(save_branding(ui.root, body.get("agency") or ""))
        except ValueError as exc:
            return error(400, str(exc))

    @app.get("/api/v1/branding/logo")
    def api_logo() -> Response:
        logo = logo_file(ui.root)
        if logo is None:
            return error(404, "No logo yet.")
        return FileResponse(logo[0], media_type=logo[1])

    @app.post("/api/v1/branding/logo")
    async def api_upload_logo(request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        length = _int(request.headers.get("content-length"))
        if length is not None and length > MAX_LOGO_BYTES:
            return error(413, f"Keep the logo under {MAX_LOGO_BYTES // 1024} KB.")
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_LOGO_BYTES:
                return error(413, f"Keep the logo under {MAX_LOGO_BYTES // 1024} KB.")
        try:
            return JSONResponse(save_logo(ui.root, raw, request.headers.get("content-type") or ""))
        except ValueError as exc:
            return error(400, str(exc))

    @app.post("/api/v1/branding/logo/delete")
    async def api_delete_logo(request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        return JSONResponse(delete_logo(ui.root))
