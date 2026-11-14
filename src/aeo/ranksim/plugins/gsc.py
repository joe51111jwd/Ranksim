"""Google Search Console: the one thing a user connects, as a simple on/off per project.

How it works
- The owner sets GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET (an OAuth client of type Web application) and
  RANKSIM_SECRET. Without them every project shows the "not set up yet" state with the exact steps.
- Turning GSC on for a project starts Google's OAuth 2.0 web flow (scope webmasters.readonly, offline access, consent
  prompt). The `state` parameter is sealed with secretbox, expires after 10 minutes, carries a nonce that must match a
  short-lived cookie in the same browser, and is bound to the signed-in owner session cookie.
- One Google connection serves every project (the owner's account). Its refresh token and access token are sealed with
  secretbox at rest, under the projects folder in `_plugins/gsc/account.json`, which the cloud store mirrors.
- Per project: `project.dir / "plugins" / "gsc" / settings.json` holds on/off and the chosen property; Search Analytics
  snapshots (last 28 days of final data: totals, queries, pages, query+page, up to 1000 rows each) are stored next to it.
- Hooks: discovery_sources feeds top GSC queries into search discovery, extend_report adds report["gsc"], after_pull
  refreshes stale data, register adds the /api/v1/gsc routes.

Numbers here are Google Search Console data for the stated date range, never predictions.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import secretbox

NAME = "gsc"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/webmasters/v3"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CALLBACK_PATH = "/api/v1/gsc/callback"
HOSTED_REDIRECT_URI = "https://ranksim.vercel.app" + CALLBACK_PATH

CLIENT_ID_ENV = "GOOGLE_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "GOOGLE_OAUTH_CLIENT_SECRET"
REDIRECT_ENV = "GOOGLE_OAUTH_REDIRECT_URI"  # optional override when the public address differs from the Host header

STATE_PURPOSE = "gsc-oauth-state"
REFRESH_PURPOSE = "gsc-refresh-token"
ACCESS_PURPOSE = "gsc-access-token"
NONCE_COOKIE = "rs_gsc_nonce"
STATE_SECONDS = 600
TOKEN_SKEW_SECONDS = 60
TIMEOUT_SECONDS = 20.0

DAYS = 28
LAG_DAYS = 2  # Search Console's final data trails today by about two days
ROW_LIMIT = 1000
STALE_SECONDS = 12 * 3600
KEEP_SNAPSHOTS = 8
REPORT_QUERIES = 100
REPORT_PAGES = 50
DISCOVERY_LIMIT = 30
LABEL = "Google Search Console data"
GOOD_PERMISSIONS = {"siteOwner", "siteFullUser", "siteRestrictedUser"}

SETUP_STEPS = [
    "Open Google Cloud Console (console.cloud.google.com) and pick or create a project.",
    "Open APIs and Services, then Library, search for Google Search Console API and click Enable.",
    "Open the OAuth consent screen. Choose External, fill in the app name and your email, and add the scope "
    "https://www.googleapis.com/auth/webmasters.readonly.",
    "Set the publishing status to In production. In Testing, Google ends access after 7 days. Google shows an "
    "unverified app warning when you connect; click Advanced, then continue.",
    "Open Credentials, click Create credentials, then OAuth client ID. Choose the type Web application.",
    f"Under Authorized redirect URIs add {HOSTED_REDIRECT_URI} and save.",
    f"In Vercel, open the ranksim project, Settings, Environment Variables. Add {CLIENT_ID_ENV} and "
    f"{CLIENT_SECRET_ENV} with the values Google shows, then redeploy.",
]


class GscError(Exception):
    """A user-facing problem. The message is plain and never holds a token."""

    def __init__(self, message: str, *, code: str = "error", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class GscAuthError(GscError):
    """Google refused the stored grant (revoked, expired, or the client changed). The owner must connect again."""


# ---------------------------------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------------------------------


def oauth_config() -> dict:
    client_id = os.environ.get(CLIENT_ID_ENV, "").strip()
    client_secret = os.environ.get(CLIENT_SECRET_ENV, "").strip()
    missing = [name for name, value in ((CLIENT_ID_ENV, client_id), (CLIENT_SECRET_ENV, client_secret)) if not value]
    if not secretbox.configured():
        missing.append(secretbox.SECRET_ENV)
    return {"client_id": client_id, "client_secret": client_secret, "missing": missing, "ready": not missing}


def setup_info(redirect_uri: str | None = None) -> dict:
    config = oauth_config()
    steps = list(SETUP_STEPS)
    if redirect_uri and redirect_uri != HOSTED_REDIRECT_URI:
        steps[5] = f"Under Authorized redirect URIs add {HOSTED_REDIRECT_URI} and {redirect_uri}, then save."
    if secretbox.SECRET_ENV in config["missing"]:
        steps.append(f"Also set {secretbox.SECRET_ENV} on Vercel to a long random value. Ranksim uses it to encrypt the Google token.")
    return {"missing": config["missing"], "steps": steps, "redirect_uri": redirect_uri or HOSTED_REDIRECT_URI, "scope": SCOPE}


def redirect_uri_for(request) -> str:
    override = os.environ.get(REDIRECT_ENV, "").strip()
    if override:
        return override
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip().lower()
    if proto not in {"http", "https"}:
        proto = "https"
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}{CALLBACK_PATH}"


def _now(ui) -> float:
    clock = getattr(ui, "gsc_clock", None)
    return float(clock()) if callable(clock) else time.time()


def _today(ui) -> date:
    return datetime.fromtimestamp(_now(ui), tz=timezone.utc).date()


def _stamp(ui) -> str:
    return datetime.fromtimestamp(_now(ui), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------------------


def account_path(ui) -> Path:
    return Path(ui.root) / "_plugins" / NAME / "account.json"


def project_dir(project) -> Path:
    return Path(project.dir) / "plugins" / NAME


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def load_account(ui) -> dict | None:
    return _read_json(account_path(ui))


def save_account(ui, data: dict) -> None:
    _write_json(account_path(ui), data)


def delete_account(ui) -> None:
    account_path(ui).unlink(missing_ok=True)


def connected(ui) -> bool:
    account = load_account(ui)
    return bool(account and account.get("refresh_token"))


DEFAULT_SETTINGS = {"enabled": False, "property": "", "last_error": "", "error_at": "", "updated": ""}


def load_settings(project) -> dict:
    data = dict(DEFAULT_SETTINGS)
    data.update(_read_json(project_dir(project) / "settings.json") or {})
    data["enabled"] = bool(data.get("enabled"))
    data["property"] = str(data.get("property") or "")
    return data


def save_settings(ui, project, changes: dict) -> dict:
    data = load_settings(project)
    data.update(changes)
    data["updated"] = _stamp(ui)
    _write_json(project_dir(project) / "settings.json", data)
    return data


def load_snapshot(project) -> dict | None:
    return _read_json(project_dir(project) / "latest.json")


def save_snapshot(project, snapshot: dict) -> None:
    folder = project_dir(project) / "snapshots"
    stamp = snapshot["fetched_at"].replace("-", "").replace(":", "")
    _write_json(folder / f"{stamp}.json", snapshot)
    _write_json(project_dir(project) / "latest.json", snapshot)
    old = sorted(folder.glob("*.json"))[:-KEEP_SNAPSHOTS]
    for path in old:
        path.unlink(missing_ok=True)


def clear_project(project) -> None:
    shutil.rmtree(project_dir(project), ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------------
# Google endpoints
# ---------------------------------------------------------------------------------------------------------------------


class GoogleClient:
    """The five Google calls. `transport` lets tests (and a local fake) answer without the network."""

    def __init__(self, transport: httpx.BaseTransport | None = None, timeout: float = TIMEOUT_SECONDS) -> None:
        self.transport = transport
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=self.timeout, follow_redirects=False)

    def _post_form(self, url: str, form: dict) -> httpx.Response:
        try:
            with self._client() as client:
                return client.post(url, data=form, headers={"accept": "application/json"})
        except httpx.HTTPError as exc:
            raise GscError(f"Google did not answer ({type(exc).__name__}). Try again in a moment.", code="network", status=502) from None

    def exchange_code(self, config: dict, code: str, redirect_uri: str) -> dict:
        response = self._post_form(
            TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        return _token_reply(response, during="connect")

    def refresh(self, config: dict, refresh_token: str) -> dict:
        response = self._post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        return _token_reply(response, during="refresh")

    def revoke(self, token: str) -> bool:
        try:
            response = self._post_form(REVOKE_URL, {"token": token})
        except GscError:
            return False
        return response.status_code == 200

    def _api(self, method: str, path: str, access_token: str, body: dict | None = None) -> httpx.Response:
        headers = {"authorization": f"Bearer {access_token}", "accept": "application/json"}
        try:
            with self._client() as client:
                if method == "GET":
                    return client.get(API_BASE + path, headers=headers)
                return client.post(API_BASE + path, headers=headers, json=body or {})
        except httpx.HTTPError as exc:
            raise GscError(f"Search Console did not answer ({type(exc).__name__}). Try again in a moment.", code="network", status=502) from None

    def list_sites(self, access_token: str) -> httpx.Response:
        return self._api("GET", "/sites", access_token)

    def search_analytics(self, access_token: str, site_url: str, body: dict) -> httpx.Response:
        return self._api("POST", f"/sites/{quote(site_url, safe='')}/searchAnalytics/query", access_token, body)


def client_for(ui) -> GoogleClient:
    return GoogleClient(transport=getattr(ui, "gsc_transport", None))


def _json_of(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _token_reply(response: httpx.Response, *, during: str) -> dict:
    data = _json_of(response)
    if response.status_code == 200 and data.get("access_token"):
        return data
    error = str(data.get("error") or "")
    if error in {"invalid_grant", "unauthorized_client", "invalid_client"}:
        if during == "connect":
            raise GscAuthError("Google did not accept the sign in. Try connecting again.", code=error, status=400)
        raise GscAuthError("Google access for Search Console ended. Turn it on again to reconnect.", code=error, status=401)
    raise GscError(f"Google refused the token request ({response.status_code}). Try again in a moment.", code="token_error", status=502)


def _api_error(response: httpx.Response, property_url: str = "") -> GscError:
    data = _json_of(response)
    detail = data.get("error") if isinstance(data.get("error"), dict) else {}
    message = str(detail.get("message") or "")[:200]
    if response.status_code == 403:
        if "has not been used" in message or "disabled" in message.lower():
            return GscError("The Search Console API is not enabled in Google Cloud. Enable it, then try again.", code="api_disabled", status=502)
        target = f" {property_url}" if property_url else " this property"
        return GscError(f"This Google account cannot read{target} in Search Console.", code="forbidden", status=403)
    if response.status_code == 429:
        return GscError("Search Console is limiting requests right now. Try again in a few minutes.", code="rate_limited", status=429)
    if response.status_code == 404:
        return GscError("Search Console does not know that property.", code="not_found", status=404)
    return GscError(f"Search Console answered {response.status_code}. Try again in a moment.", code="api_error", status=502)


# ---------------------------------------------------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------------------------------------------------


def store_tokens(ui, tokens: dict, *, keep_refresh: str = "") -> dict:
    refresh = str(tokens.get("refresh_token") or keep_refresh or "")
    if not refresh:
        raise GscError("Google did not return lasting access. Remove Ranksim from your Google account's third party access, then connect again.", code="no_refresh_token")
    previous = load_account(ui) or {}
    account = {
        "refresh_token": secretbox.seal(refresh, purpose=REFRESH_PURPOSE),
        "access_token": secretbox.seal(str(tokens["access_token"]), purpose=ACCESS_PURPOSE),
        "expires_at": int(_now(ui) + int(tokens.get("expires_in") or 3600)),
        "scope": str(tokens.get("scope") or previous.get("scope") or SCOPE),
        "connected_at": previous.get("connected_at") if keep_refresh else _stamp(ui),
        "refreshed_at": _stamp(ui),
    }
    save_account(ui, account)
    return account


def access_token(ui, *, force_refresh: bool = False) -> str:
    account = load_account(ui)
    if not account or not account.get("refresh_token"):
        raise GscError("Search Console is not connected.", code="not_connected", status=409)
    if not force_refresh and account.get("access_token") and float(account.get("expires_at") or 0) - TOKEN_SKEW_SECONDS > _now(ui):
        try:
            return secretbox.unseal(account["access_token"], purpose=ACCESS_PURPOSE)
        except secretbox.SecretBoxError:
            pass  # fall through to a refresh
    try:
        refresh = secretbox.unseal(account["refresh_token"], purpose=REFRESH_PURPOSE)
    except secretbox.SecretBoxError:
        delete_account(ui)
        raise GscAuthError("The stored Google access can no longer be read (the secret changed). Turn it on again to reconnect.", code="unreadable", status=409) from None
    config = oauth_config()
    try:
        tokens = client_for(ui).refresh(config, refresh)
    except GscAuthError:
        delete_account(ui)
        raise
    store_tokens(ui, tokens, keep_refresh=refresh)
    return str(tokens["access_token"])


def _api_call(ui, call, *args, property_url: str = ""):
    """One Search Console call with a fresh token, retried once after a refresh if Google says the token expired."""
    client = client_for(ui)
    token = access_token(ui)
    response = getattr(client, call)(token, *args)
    if response.status_code == 401:
        token = access_token(ui, force_refresh=True)
        response = getattr(client, call)(token, *args)
    if response.status_code != 200:
        raise _api_error(response, property_url)
    return _json_of(response)


def revoke(ui) -> bool:
    """Revoke the grant at Google (best effort) and forget it here. Returns whether Google confirmed the revoke."""
    account = load_account(ui)
    if not account:
        return False
    revoked = False
    for field, purpose in (("refresh_token", REFRESH_PURPOSE), ("access_token", ACCESS_PURPOSE)):
        try:
            token = secretbox.unseal(str(account.get(field) or ""), purpose=purpose)
        except secretbox.SecretBoxError:
            continue
        if client_for(ui).revoke(token):
            revoked = True
            break  # revoking either token ends the whole grant
    delete_account(ui)
    return revoked


# ---------------------------------------------------------------------------------------------------------------------
# OAuth state (CSRF): sealed, short-lived, nonce in a cookie, bound to the owner session
# ---------------------------------------------------------------------------------------------------------------------


def session_binding(ui, request) -> str:
    access = getattr(ui, "access", None)
    if access is None or not access.enabled:
        return ""
    from aeo.ranksim.access import COOKIE

    value = request.cookies.get(COOKIE) or ""
    return hashlib.sha256(f"gsc-state|{value}".encode("utf-8")).hexdigest()[:32] if value else ""


def make_state(ui, request, slug: str, redirect_uri: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(24)
    payload = {"slug": slug, "nonce": nonce, "exp": int(_now(ui) + STATE_SECONDS), "sess": session_binding(ui, request), "redirect_uri": redirect_uri}
    return secretbox.seal(json.dumps(payload, sort_keys=True), purpose=STATE_PURPOSE), nonce


def check_state(ui, request, state: str) -> dict:
    try:
        payload = json.loads(secretbox.unseal(state or "", purpose=STATE_PURPOSE))
    except (secretbox.SecretBoxError, ValueError):
        raise GscError("That sign in link is not valid. Start again from Settings.", code="bad_state") from None
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < _now(ui):
        raise GscError("That sign in took too long. Start again from Settings.", code="expired_state")
    cookie = request.cookies.get(NONCE_COOKIE) or ""
    if not cookie or not secrets.compare_digest(cookie, str(payload.get("nonce") or "")):
        raise GscError("That sign in was started in another browser. Start again from Settings.", code="bad_nonce")
    if not secrets.compare_digest(str(payload.get("sess") or ""), session_binding(ui, request)):
        raise GscError("That sign in belongs to another session. Start again from Settings.", code="bad_session")
    return payload


def auth_url(config: dict, redirect_uri: str, state: str) -> str:
    query = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(query)


# ---------------------------------------------------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------------------------------------------------


def list_properties(ui) -> list[dict]:
    data = _api_call(ui, "list_sites")
    found = []
    for entry in data.get("siteEntry") or []:
        url = str(entry.get("siteUrl") or "")
        level = str(entry.get("permissionLevel") or "")
        if not url:
            continue
        found.append({"url": url, "permission": level, "kind": "domain" if url.startswith("sc-domain:") else "url", "usable": level in GOOD_PERMISSIONS})
    found.sort(key=lambda item: (not item["usable"], item["kind"] != "domain", item["url"]))
    return found


def _site_host(project) -> str:
    site = str(getattr(project, "site", "") or "")
    if "://" not in site:
        site = "https://" + site
    return (urlsplit(site).hostname or "").lower()


def candidate_properties(project) -> list[str]:
    """Property names that would cover the project's site, best first."""
    host = _site_host(project)
    if not host:
        return []
    bare = host[4:] if host.startswith("www.") else host
    names = [f"sc-domain:{bare}"]
    labels = bare.split(".")
    for start in range(1, len(labels) - 1):
        names.append(f"sc-domain:{'.'.join(labels[start:])}")
    for scheme in ("https", "http"):
        for name in (host, f"www.{bare}", bare):
            url = f"{scheme}://{name}/"
            if url not in names:
                names.append(url)
    return names


def match_property(project, properties: list[dict]) -> str:
    usable = {item["url"].lower(): item["url"] for item in properties if item.get("usable")}
    for name in candidate_properties(project):
        if name.lower() in usable:
            return usable[name.lower()]
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# Search Analytics
# ---------------------------------------------------------------------------------------------------------------------


def date_range(ui) -> tuple[str, str]:
    end = _today(ui) - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=DAYS - 1)
    return start.isoformat(), end.isoformat()


def _metrics(row: dict) -> dict:
    return {
        "clicks": int(row.get("clicks") or 0),
        "impressions": int(row.get("impressions") or 0),
        "ctr": round(float(row.get("ctr") or 0.0), 4),
        "position": round(float(row.get("position") or 0.0), 1),
    }


def fetch_snapshot(ui, project, property_url: str) -> dict:
    start, end = date_range(ui)

    def rows(dimensions: list[str]) -> tuple[list[dict], bool]:
        body = {"startDate": start, "endDate": end, "dimensions": dimensions, "type": "web"}
        if dimensions:
            body["rowLimit"] = ROW_LIMIT
        data = _api_call(ui, "search_analytics", property_url, body, property_url=property_url)
        found = [row for row in data.get("rows") or [] if isinstance(row, dict)]
        return found, len(found) >= ROW_LIMIT

    total_rows, _ = rows([])
    query_rows, queries_capped = rows(["query"])
    page_rows, pages_capped = rows(["page"])
    pair_rows, pairs_capped = rows(["query", "page"])
    by_impressions = lambda item: (-item["impressions"], -item["clicks"], item.get("q") or item.get("page") or "")  # noqa: E731
    queries = sorted(({"q": str(row["keys"][0]), **_metrics(row)} for row in query_rows if row.get("keys")), key=by_impressions)
    pages = sorted(({"page": str(row["keys"][0]), **_metrics(row)} for row in page_rows if row.get("keys")), key=lambda item: (-item["clicks"], -item["impressions"], item["page"]))
    pairs = sorted(
        ({"q": str(row["keys"][0]), "page": str(row["keys"][1]), **_metrics(row)} for row in pair_rows if len(row.get("keys") or []) >= 2),
        key=by_impressions,
    )
    totals = _metrics(total_rows[0]) if total_rows else {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}
    snapshot = {
        "source": "google_search_console",
        "label": LABEL,
        "property": property_url,
        "fetched_at": _stamp(ui),
        "start": start,
        "end": end,
        "days": DAYS,
        "search_type": "web",
        "row_limit": ROW_LIMIT,
        "capped": {"queries": queries_capped, "pages": pages_capped, "query_pages": pairs_capped},
        "totals": totals,
        "queries": queries,
        "pages": pages,
        "query_pages": pairs,
    }
    save_snapshot(project, snapshot)
    return snapshot


def refresh_project(ui, project, *, force: bool = False) -> dict | None:
    """Fetch fresh data when the project is on, has a property and its data is stale (or force). Errors are recorded."""
    settings = load_settings(project)
    if not (oauth_config()["ready"] and settings["enabled"] and settings["property"] and connected(ui)):
        return load_snapshot(project)
    snapshot = load_snapshot(project)
    if not force and snapshot and snapshot.get("property") == settings["property"] and not _stale(ui, snapshot):
        return snapshot
    try:
        snapshot = fetch_snapshot(ui, project, settings["property"])
    except GscError as exc:
        save_settings(ui, project, {"last_error": str(exc), "error_at": _stamp(ui)})
        raise
    save_settings(ui, project, {"last_error": "", "error_at": ""})
    return snapshot


def _stale(ui, snapshot: dict) -> bool:
    try:
        fetched = datetime.strptime(str(snapshot.get("fetched_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return True
    return _now(ui) - fetched > STALE_SECONDS


# ---------------------------------------------------------------------------------------------------------------------
# State for the UI and the report
# ---------------------------------------------------------------------------------------------------------------------


def _range_label(start: str, end: str) -> str:
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return ""
    left = f"{a:%b} {a.day}" + (f", {a.year}" if a.year != b.year else "")
    return f"{left} to {b:%b} {b.day}, {b.year}"


def _loose(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def project_state(ui, project) -> dict:
    """Everything the UI needs about GSC for one project, from stored files only (no network)."""
    config = oauth_config()
    settings = load_settings(project)
    account = load_account(ui) or {}
    is_connected = bool(account.get("refresh_token"))
    snapshot = load_snapshot(project)
    if getattr(ui, "public_demo", False):
        state, message = "not_available", "Search Console is not available on the public demo."
    elif not config["ready"]:
        state, message = "not_configured", "Not set up yet."
    elif not settings["enabled"]:
        state, message = "off", "Search Console is off for this project."
    elif not is_connected:
        state, message = "not_connected", "Connect your Google account to turn Search Console on."
    elif not settings["property"]:
        state, message = "needs_property", "Pick the Search Console property for this site."
    elif settings["last_error"]:
        state, message = "error", settings["last_error"]
    elif snapshot and snapshot.get("property") == settings["property"]:
        state, message = "ready", ""
    else:
        state, message = "no_data", "Search Console data has not been loaded yet."
    data = {
        "state": state,
        "message": message,
        "label": LABEL,
        "configured": config["ready"],
        "connected": is_connected,
        "enabled": settings["enabled"],
        "property": settings["property"],
        "connected_at": account.get("connected_at") or "",
    }
    if state == "not_configured":
        data["setup"] = setup_info()
    if state in {"ready", "error"} and snapshot and snapshot.get("property") == settings["property"]:
        data.update(
            {
                "range": {"start": snapshot["start"], "end": snapshot["end"], "days": snapshot.get("days", DAYS), "label": _range_label(snapshot["start"], snapshot["end"])},
                "fetched_at": snapshot.get("fetched_at", ""),
                "totals": snapshot.get("totals", {}),
                "capped": snapshot.get("capped", {}),
            }
        )
    return data


def report_section(ui, project) -> dict:
    data = project_state(ui, project)
    snapshot = load_snapshot(project)
    if "range" not in data or not snapshot:
        return data
    queries = snapshot.get("queries") or []
    pairs = snapshot.get("query_pages") or []
    data["queries"] = queries[:REPORT_QUERIES]
    data["pages"] = (snapshot.get("pages") or [])[:REPORT_PAGES]
    data["query_count"] = len(queries)
    data["page_count"] = len(snapshot.get("pages") or [])
    by_query = {_loose(item["q"]): item for item in queries}
    top_page: dict[str, dict] = {}
    for pair in pairs:
        key = _loose(pair["q"])
        best = top_page.get(key)
        if best is None or (pair["clicks"], pair["impressions"]) > (best["clicks"], best["impressions"]):
            top_page[key] = pair
    searches = {}
    for q in getattr(project, "searches", None) or []:
        row = by_query.get(_loose(q))
        if row is None:
            continue
        entry = {key: row[key] for key in ("clicks", "impressions", "ctr", "position")}
        if _loose(q) in top_page:
            entry["page"] = top_page[_loose(q)]["page"]
        searches[q] = entry
    data["searches"] = searches
    return data


# ---------------------------------------------------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------------------------------------------------


def _active(ui, project) -> bool:
    if project is None or getattr(ui, "public_demo", False) or not oauth_config()["ready"]:
        return False
    settings = load_settings(project)
    return bool(settings["enabled"] and settings["property"] and connected(ui))


def discovery_sources(*, project, pages=None, ui=None) -> list[dict]:
    """Top Search Console queries for the project, weighted by impressions (1.0 for the most seen). Only when on."""
    if ui is None or not _active(ui, project):
        return []
    try:
        snapshot = refresh_project(ui, project)
    except GscError:
        snapshot = load_snapshot(project)
    if not snapshot or snapshot.get("property") != load_settings(project)["property"]:
        return []
    queries = [item for item in snapshot.get("queries") or [] if item.get("impressions", 0) > 0][:DISCOVERY_LIMIT]
    if not queries:
        return []
    top = max(item["impressions"] for item in queries) or 1
    return [
        {
            "q": item["q"],
            "weight": round(max(item["impressions"] / top, 0.01), 4),
            "source": NAME,
            "impressions": item["impressions"],
            "clicks": item["clicks"],
            "position": item["position"],
        }
        for item in queries
    ]


def extend_report(report: dict, *, project=None, meta=None, database=None, ctx=None, ui=None) -> None:
    if ui is None or project is None:
        return
    report[NAME] = report_section(ui, project)


def after_pull(*, project=None, report=None, ui=None) -> None:
    if ui is None or not _active(ui, project):
        return
    try:
        refresh_project(ui, project)
    except GscError:
        pass  # recorded in settings; the pull itself is not affected


def settings_schema() -> dict:
    return {
        "key": NAME,
        "label": "Google Search Console",
        "fields": [
            {"key": "enabled", "type": "toggle", "label": "Search Console"},
            {"key": "property", "type": "select", "label": "Property", "source": "/api/v1/gsc/projects/{slug}/properties"},
        ],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------------------------------


def _common():
    try:
        from aeo.ranksim.api import common  # after the plugin refactor

        return common
    except ImportError:
        from aeo.ranksim import studio

        return studio


def _save_cloud(ui) -> None:
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and getattr(cloud, "enabled", False):
        try:
            cloud.save()
        except Exception as exc:  # noqa: BLE001 - the change is on this instance; report but do not fail the redirect
            print(f"ranksim: gsc: cloud save failed: {type(exc).__name__}", flush=True)


def register(app, ui) -> None:
    common = _common()

    def project_or_404(slug: str):
        try:
            return ui.project(slug)
        except LookupError:
            return None

    def fail(exc: GscError) -> JSONResponse:
        return common._error(exc.status, ui.scrub(str(exc)) if hasattr(ui, "scrub") else str(exc))

    def guard_write(request: Request):
        common._check_header_csrf(ui, request)
        return common._not_public(ui)

    def status_payload(request: Request, project) -> dict:
        data = project_state(ui, project)
        data["redirect_uri"] = redirect_uri_for(request)
        if data["state"] == "not_configured":
            data["setup"] = setup_info(data["redirect_uri"])
        return data

    @app.get("/api/v1/gsc/status")
    def gsc_status(request: Request) -> JSONResponse:
        config = oauth_config()
        account = load_account(ui) or {}
        uri = redirect_uri_for(request)
        data = {
            "label": LABEL,
            "configured": config["ready"] and not ui.public_demo,
            "connected": bool(account.get("refresh_token")) and not ui.public_demo,
            "connected_at": account.get("connected_at") or "",
            "redirect_uri": uri,
            "public": bool(ui.public_demo),
        }
        if not config["ready"]:
            data["setup"] = setup_info(uri)
        return JSONResponse(data)

    @app.get("/api/v1/gsc/projects/{slug}")
    def gsc_project(slug: str, request: Request) -> JSONResponse:
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        return JSONResponse(status_payload(request, project))

    @app.get("/api/v1/gsc/projects/{slug}/properties")
    async def gsc_properties(slug: str) -> JSONResponse:
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        if not oauth_config()["ready"]:
            return common._error(409, "Search Console is not set up yet.")
        try:
            properties = await run_in_threadpool(list_properties, ui)
        except GscError as exc:
            return fail(exc)
        return JSONResponse({"properties": properties, "suggested": match_property(project, properties), "selected": load_settings(project)["property"]})

    @app.post("/api/v1/gsc/projects/{slug}/connect")
    async def gsc_connect(slug: str, request: Request) -> JSONResponse:
        blocked = guard_write(request)
        if blocked is not None:
            return blocked
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        config = oauth_config()
        if not config["ready"]:
            return common._error(409, "Search Console is not set up yet. " + " ".join(f"Set {name}." for name in config["missing"]))
        uri = redirect_uri_for(request)
        state, nonce = make_state(ui, request, project.slug, uri)
        response = JSONResponse({"url": auth_url(config, uri, state)})
        secure = uri.startswith("https://")
        response.set_cookie(NONCE_COOKIE, nonce, max_age=STATE_SECONDS, httponly=True, secure=secure, samesite="lax", path="/api/v1/gsc")
        return response

    @app.get("/api/v1/gsc/callback")
    async def gsc_callback(request: Request) -> RedirectResponse:
        params = request.query_params

        def back(slug: str | None, result: str) -> RedirectResponse:
            target = f"/app/{slug}/settings?gsc={result}" if slug else f"/app?gsc={result}"
            response = RedirectResponse(target, status_code=303)
            response.delete_cookie(NONCE_COOKIE, path="/api/v1/gsc")
            return response

        if ui.public_demo or not oauth_config()["ready"]:
            return back(None, "not_configured")
        try:
            payload = check_state(ui, request, params.get("state") or "")
        except GscError as exc:
            return back(None, exc.code)
        slug = str(payload.get("slug") or "")
        project = project_or_404(slug)
        if project is None:
            return back(None, "no_project")
        if params.get("error"):
            return back(project.slug, "denied" if params.get("error") == "access_denied" else "google_error")
        code = params.get("code") or ""
        if not code:
            return back(project.slug, "google_error")

        def finish() -> str:
            cloud = getattr(ui, "cloud", None)
            if cloud is not None and getattr(cloud, "enabled", False):
                cloud.refresh(force=True)
            tokens = client_for(ui).exchange_code(oauth_config(), code, str(payload["redirect_uri"]))
            if SCOPE not in str(tokens.get("scope") or SCOPE).split():
                client_for(ui).revoke(str(tokens.get("refresh_token") or tokens["access_token"]))
                raise GscError("Search Console access was not granted.", code="scope_missing")
            old = load_account(ui)
            store_tokens(ui, tokens)
            if old and old.get("refresh_token"):
                try:
                    old_refresh = secretbox.unseal(old["refresh_token"], purpose=REFRESH_PURPOSE)
                    if old_refresh != tokens.get("refresh_token"):
                        client_for(ui).revoke(old_refresh)
                except secretbox.SecretBoxError:
                    pass
            settings = save_settings(ui, project, {"enabled": True, "last_error": "", "error_at": ""})
            result = "connected"
            if not settings["property"]:
                try:
                    chosen = match_property(project, list_properties(ui))
                except GscError:
                    chosen = ""
                if chosen:
                    save_settings(ui, project, {"property": chosen})
                else:
                    result = "needs_property"
            if load_settings(project)["property"]:
                try:
                    refresh_project(ui, project, force=True)
                except GscError:
                    pass  # shown as the project's error state
            _save_cloud(ui)
            return result

        try:
            result = await run_in_threadpool(finish)
        except GscError as exc:
            return back(project.slug, exc.code)
        except secretbox.SecretBoxError:
            return back(project.slug, "not_configured")
        return back(project.slug, result)

    @app.post("/api/v1/gsc/projects/{slug}")
    async def gsc_settings(slug: str, request: Request) -> JSONResponse:
        blocked = guard_write(request)
        if blocked is not None:
            return blocked
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        body = await common._json_body(request, 8192)
        changes: dict = {}
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                return common._error(400, "enabled must be true or false.")
            changes["enabled"] = body["enabled"]
        if "property" in body:
            wanted = str(body.get("property") or "").strip()[:500]
            if wanted:
                if not oauth_config()["ready"]:
                    return common._error(409, "Search Console is not set up yet.")
                try:
                    properties = await run_in_threadpool(list_properties, ui)
                except GscError as exc:
                    return fail(exc)
                match = next((item for item in properties if item["url"] == wanted), None)
                if match is None or not match["usable"]:
                    return common._error(400, "That property is not in your Search Console account.")
            changes["property"] = wanted
            changes["last_error"] = ""
            changes["error_at"] = ""
        if not changes:
            return common._error(400, "Nothing to change.")
        current = save_settings(ui, project, changes)
        if current["enabled"] and current["property"] and connected(ui) and oauth_config()["ready"]:
            try:
                await run_in_threadpool(refresh_project, ui, project)  # fetches only when missing, stale or a new property
            except GscError:
                pass  # shown as the error state
        data = status_payload(request, project)
        if current["enabled"] and not data["connected"]:
            data["needs_connect"] = True
        return JSONResponse(data)

    @app.post("/api/v1/gsc/projects/{slug}/refresh")
    async def gsc_refresh(slug: str, request: Request) -> JSONResponse:
        blocked = guard_write(request)
        if blocked is not None:
            return blocked
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        settings = load_settings(project)
        if not oauth_config()["ready"]:
            return common._error(409, "Search Console is not set up yet.")
        if not settings["enabled"]:
            return common._error(409, "Search Console is off for this project.")
        if not settings["property"]:
            return common._error(409, "Pick a Search Console property first.")
        try:
            await run_in_threadpool(lambda: refresh_project(ui, project, force=True))
        except GscError as exc:
            return fail(exc)
        return JSONResponse(status_payload(request, project))

    @app.post("/api/v1/gsc/disconnect")
    async def gsc_disconnect(request: Request) -> JSONResponse:
        blocked = guard_write(request)
        if blocked is not None:
            return blocked
        revoked = await run_in_threadpool(revoke, ui)
        return JSONResponse({"ok": True, "revoked": revoked, "connected": False})
