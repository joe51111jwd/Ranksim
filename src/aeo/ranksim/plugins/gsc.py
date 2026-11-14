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
from types import SimpleNamespace
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
REDIRECT_ENV = "GOOGLE_OAUTH_REDIRECT_URI"  # optional: the whole redirect URI
PUBLIC_URL_ENV = "RANKSIM_PUBLIC_URL"  # optional: pins the redirect URI's base (https://ranksim.example), else the request

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
DISCOVERY_TOP = 5.0
DISCOVERY_FLOOR = 2.5
DISCOVERY_MIN_IMPRESSIONS = 10
LABEL = "Google Search Console data"
GOOD_PERMISSIONS = {"siteOwner", "siteFullUser", "siteRestrictedUser"}

SETUP_STEPS = [
    "In Google Cloud Console, enable the Google Search Console API.",
    "Set up the OAuth consent screen (External) with the scope webmasters.readonly, and set it to In production. "
    "In Testing, Google ends access after 7 days.",
    "Create an OAuth client ID of type Web application with this redirect URI: {redirect_uri}",
    f"Set {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV} on the server (on Vercel: Environment Variables), then redeploy.",
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
    """The owner's setup steps. The redirect URI is this server's own address when a request gives it."""
    config = oauth_config()
    uri = redirect_uri or "this server's address followed by " + CALLBACK_PATH
    steps = [step.replace("{redirect_uri}", uri) for step in SETUP_STEPS]
    if secretbox.SECRET_ENV in config["missing"]:
        steps.append(f"Set {secretbox.SECRET_ENV} to a random value of at least {secretbox.MIN_SECRET_CHARS} characters (openssl rand -base64 32).")
    return {"missing": config["missing"], "steps": steps, "redirect_uri": redirect_uri or "", "scope": SCOPE}


def redirect_uri_for(request) -> str:
    override = os.environ.get(REDIRECT_ENV, "").strip()
    if override:
        return override
    public = os.environ.get(PUBLIC_URL_ENV, "").strip().rstrip("/")
    if public:
        parts = urlsplit(public if "://" in public else "https://" + public)
        if parts.scheme in {"http", "https"} and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}{CALLBACK_PATH}"
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
    from aeo.ranksim import plugins

    return plugins.workspace_dir(ui, NAME) / "account.json"


def project_dir(project) -> Path:
    from aeo.ranksim import plugins

    return plugins.data_dir(project, NAME)


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

    def exchange_code(self, config: dict, code: str, redirect_uri: str, verifier: str = "") -> dict:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        }
        if verifier:
            form["code_verifier"] = verifier
        response = self._post_form(TOKEN_URL, form)
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
    if error in {"invalid_client", "unauthorized_client"}:
        # The OAuth client is wrong (a mistyped or rotated secret). The stored grant is still good: keep it.
        raise GscError(f"Google refused the OAuth client. Check {CLIENT_ID_ENV} and {CLIENT_SECRET_ENV}.", code="client_config", status=502)
    if error == "invalid_grant":
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


def make_state(ui, request, slug: str, redirect_uri: str) -> tuple[str, str, str]:
    """(sealed state, nonce for the cookie, PKCE challenge for the authorization URL)."""
    nonce = secrets.token_urlsafe(24)
    # PKCE (S256): the verifier travels only inside the sealed state, so an intercepted code cannot be redeemed alone.
    payload = {
        "slug": slug,
        "nonce": nonce,
        "exp": int(_now(ui) + STATE_SECONDS),
        "sess": session_binding(ui, request),
        "redirect_uri": redirect_uri,
        "verifier": secrets.token_urlsafe(48),
    }
    return secretbox.seal(json.dumps(payload, sort_keys=True), purpose=STATE_PURPOSE), nonce, pkce_challenge(payload["verifier"])


def check_state(ui, request, state: str, *, bind_session: bool = True) -> dict:
    try:
        payload = json.loads(secretbox.unseal(state or "", purpose=STATE_PURPOSE))
    except (secretbox.SecretBoxError, ValueError):
        raise GscError("That sign in link is not valid. Start again from Settings.", code="bad_state") from None
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) < _now(ui):
        raise GscError("That sign in took too long. Start again from Settings.", code="expired_state")
    cookie = request.cookies.get(NONCE_COOKIE) or ""
    if not cookie or not secrets.compare_digest(cookie, str(payload.get("nonce") or "")):
        raise GscError("That sign in was started in another browser. Start again from Settings.", code="bad_nonce")
    if bind_session and not secrets.compare_digest(str(payload.get("sess") or ""), session_binding(ui, request)):
        raise GscError("That sign in belongs to another session. Start again from Settings.", code="bad_session")
    return payload


PENDING_COOKIE = "rs_gsc_pending"
PENDING_PURPOSE = "gsc-pending-callback"


def complete_connection(ui, project, code: str, payload: dict) -> str:
    """Exchange the code, store the tokens, turn the project on, match its property and load its data. Returns the
    result code for the Settings screen. Raises GscError, CloudError or SecretBoxError."""
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and getattr(cloud, "enabled", False):
        cloud.refresh(force=True)
    tokens = client_for(ui).exchange_code(oauth_config(), code, str(payload["redirect_uri"]), str(payload.get("verifier") or ""))
    if SCOPE not in str(tokens.get("scope") or SCOPE).split():
        # Not stored. No revoke: Google's revoke ends the whole grant, which could cut a working connection.
        raise GscError("Search Console access was not granted.", code="scope_missing")
    # A reconnect replaces the stored tokens. The older refresh token is not revoked: for the same account and
    # client, revoking any token ends the whole grant, including the token just issued.
    store_tokens(ui, tokens)
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


def pkce_challenge(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def auth_url(config: dict, redirect_uri: str, state: str, *, challenge: str = "") -> str:
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
    if challenge:
        query["code_challenge"] = challenge
        query["code_challenge_method"] = "S256"
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


def match_property(project, properties: list[dict], *, strict: bool = False) -> str:
    """The best usable property for the project's site. strict (automatic matching without the owner) leaves out
    parent domain properties: client.agency.com never silently gets the data for all of agency.com."""
    usable = {item["url"].lower(): item["url"] for item in properties if item.get("usable")}
    names = candidate_properties(project)
    if strict:
        host = _site_host(project)
        bare = host[4:] if host.startswith("www.") else host
        names = [name for name in names if not name.startswith("sc-domain:") or name == f"sc-domain:{bare}"]
    for name in names:
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
    snapshot = collect_snapshot(ui, property_url)
    save_snapshot(project, snapshot)
    return snapshot


def collect_snapshot(ui, property_url: str) -> dict:
    """The four Search Analytics calls for the last 28 days of final data, as one snapshot. Saves nothing."""
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
    data["opportunities"] = opportunities(snapshot)
    return data


PAGE_QUERIES = 10


def page_key(url: str) -> str:
    """A page address compared loosely: no scheme, no www, no query or fragment, no trailing slash, lowercase host."""
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return f"{host}{path}"


def page_data(ui, project, url: str) -> dict:
    """Search Console numbers for one page and its top searches, from the stored snapshot (no network)."""
    data = project_state(ui, project)
    data["url"] = url
    snapshot = load_snapshot(project)
    if "range" not in data or not snapshot:
        return data
    key = page_key(url)
    row = next((item for item in snapshot.get("pages") or [] if page_key(item["page"]) == key), None)
    queries = sorted(
        ({k: pair[k] for k in ("q", "clicks", "impressions", "ctr", "position")} for pair in snapshot.get("query_pages") or [] if page_key(pair["page"]) == key),
        key=lambda item: (-item["impressions"], -item["clicks"], item["q"]),
    )
    data["page"] = {k: row[k] for k in ("page", "clicks", "impressions", "ctr", "position")} if row else None
    data["page_queries"] = queries[:PAGE_QUERIES]
    data["page_query_count"] = len(queries)
    return data


NEAR_MIN, NEAR_MAX = 8.0, 20.0
OPPORTUNITY_LIMIT = 25
# A rough typical click rate by position on page one (organic, desktop and mobile mixed). A benchmark for spotting
# outliers, not Google data; the UI says so.
TYPICAL_CTR = {1: 0.28, 2: 0.15, 3: 0.10, 4: 0.07, 5: 0.05, 6: 0.04, 7: 0.03, 8: 0.025, 9: 0.02, 10: 0.018}


def _min_impressions(snapshot: dict) -> int:
    total = int((snapshot.get("totals") or {}).get("impressions") or 0)
    return max(20, round(total * 0.01))


def typical_ctr(position: float) -> float | None:
    slot = int(round(position or 0))
    return TYPICAL_CTR.get(slot)


def opportunities(snapshot: dict) -> dict:
    """Two lists from one snapshot. near_page_one: queries with many impressions at an average position of 8 to 20,
    with the page that gets most of their impressions. low_ctr: pages on page one with many impressions and under
    half the typical click rate for their position. Both sorted by impressions."""
    floor = _min_impressions(snapshot)
    best_page: dict[str, dict] = {}
    for pair in snapshot.get("query_pages") or []:
        key = _loose(pair["q"])
        if key not in best_page or pair["impressions"] > best_page[key]["impressions"]:
            best_page[key] = pair
    near = []
    for row in snapshot.get("queries") or []:
        if row["impressions"] >= floor and NEAR_MIN <= row["position"] <= NEAR_MAX:
            item = {key: row[key] for key in ("q", "clicks", "impressions", "ctr", "position")}
            page = best_page.get(_loose(row["q"]))
            item["page"] = page["page"] if page else ""
            near.append(item)
    low = []
    for row in snapshot.get("pages") or []:
        expected = typical_ctr(row["position"])
        if expected is None or row["impressions"] < floor or row["ctr"] >= expected * 0.5:
            continue
        item = {key: row[key] for key in ("page", "clicks", "impressions", "ctr", "position")}
        item["benchmark_ctr"] = expected  # a rough benchmark, not Google data
        top_query = max((pair for pair in snapshot.get("query_pages") or [] if pair["page"] == row["page"]), key=lambda pair: pair["impressions"], default=None)
        item["q"] = top_query["q"] if top_query else ""
        low.append(item)
    near.sort(key=lambda item: (-item["impressions"], item["position"]))
    low.sort(key=lambda item: (-item["impressions"], item["page"]))
    return {
        "near_page_one": near[:OPPORTUNITY_LIMIT],
        "low_ctr": low[:OPPORTUNITY_LIMIT],
        "min_impressions": floor,
        "rules": {
            "near_page_one": f"Searches with at least {floor} impressions at an average position of 8 to 20.",
            "low_ctr": f"Pages on page one with at least {floor} impressions and under half a benchmark click rate for "
            "their position. The benchmark is a rough industry curve, not Google data.",
        },
    }


# ---------------------------------------------------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------------------------------------------------


def _active(ui, project) -> bool:
    if project is None or getattr(ui, "public_demo", False) or not oauth_config()["ready"]:
        return False
    settings = load_settings(project)
    return bool(settings["enabled"] and settings["property"] and connected(ui))


def discovery_weight(impressions: int, top: int) -> float:
    """On discovery's scale (a page title phrase scores 3.0, a phrase needs 2.5 to be picked): 2.5 plus 2.5 times the
    share of the top query's impressions. Every Search Console query qualifies; the most seen ones rank first."""
    share = max(0.0, min(1.0, impressions / top)) if top else 0.0
    return round(DISCOVERY_FLOOR + (DISCOVERY_TOP - DISCOVERY_FLOOR) * share, 3)


PENDING_SECONDS = 3600


def _pending_path(ui, host: str) -> Path:
    from aeo.ranksim import plugins

    digest = hashlib.sha256(host.lower().encode("utf-8")).hexdigest()[:24]
    return plugins.workspace_dir(ui, NAME) / "pending" / f"{digest}.json"


def _prune_pending(ui) -> None:
    """Parked first-analysis data older than PENDING_SECONDS is deleted (an analysis that stopped never adopts it)."""
    folder = _pending_path(ui, "x").parent
    if not folder.is_dir():
        return
    for path in folder.glob("*.json"):
        data = _read_json(path) or {}
        if _now(ui) - float(data.get("at") or 0) > PENDING_SECONDS:
            path.unlink(missing_ok=True)


ADOPT_RETRY_SECONDS = 24 * 3600


def _adopt_checked_recently(ui, project) -> bool:
    from aeo.ranksim import plugins

    checks = _read_json(plugins.workspace_dir(ui, NAME) / "adopt_checks.json") or {}
    return _now(ui) - float(checks.get(project.slug) or 0) < ADOPT_RETRY_SECONDS


def _record_adopt_check(ui, project) -> None:
    from aeo.ranksim import plugins

    path = plugins.workspace_dir(ui, NAME) / "adopt_checks.json"
    checks = _read_json(path) or {}
    checks[project.slug] = _now(ui)
    _write_json(path, checks)


def _host_of_pages(pages) -> str:
    for page in pages or []:
        host = (urlsplit(str(getattr(page, "url", "") or "")).hostname or "").lower()
        if host:
            return host
    return ""


def _first_analysis_snapshot(ui, pages) -> dict | None:
    """A site's first analysis runs before its project exists. When Google is connected and one of the account's
    properties covers the site, load its data now so its queries count, and park it for after_pull to adopt."""
    if getattr(ui, "public_demo", False) or not oauth_config()["ready"] or not connected(ui):
        return None
    _prune_pending(ui)
    host = _host_of_pages(pages)
    if not host:
        return None
    try:
        chosen = match_property(SimpleNamespace(site=host), list_properties(ui), strict=True)
        if not chosen:
            return None
        snapshot = collect_snapshot(ui, chosen)
    except GscError:
        return None
    _write_json(_pending_path(ui, host), {"host": host, "property": chosen, "at": _now(ui), "snapshot": snapshot})
    return snapshot


def _adopt(ui, project) -> None:
    """A project that never had GSC settings: turn it on when Google is connected. Uses the data the first analysis
    parked when there is some, else matches the property now. A project the owner turned off stays off."""
    if getattr(ui, "public_demo", False) or not oauth_config()["ready"] or not connected(ui):
        return
    if (project_dir(project) / "settings.json").exists():
        return
    _prune_pending(ui)
    host = _site_host(project)
    parked = _read_json(_pending_path(ui, host)) if host else None
    if parked and _now(ui) - float(parked.get("at") or 0) <= PENDING_SECONDS and isinstance(parked.get("snapshot"), dict):
        save_snapshot(project, parked["snapshot"])
        save_settings(ui, project, {"enabled": True, "property": str(parked.get("property") or ""), "auto": True})
        _pending_path(ui, host).unlink(missing_ok=True)
        return
    if _adopt_checked_recently(ui, project):
        return  # no matching property last time; ask Google again once a day, not on every pull
    try:
        chosen = match_property(project, list_properties(ui), strict=True)
    except GscError:
        return
    if chosen:
        save_settings(ui, project, {"enabled": True, "property": chosen, "auto": True})
    else:
        _record_adopt_check(ui, project)


def discovery_min_impressions(top: int) -> int:
    """A query reaches discovery only with at least max(10, 1% of the top query's) impressions over the 28 days, so
    a search seen once or twice never becomes a tracked search that costs a paid pull."""
    return max(DISCOVERY_MIN_IMPRESSIONS, -(-int(top) // 100))


def discovery_sources(*, project, pages=None, ui=None) -> list[dict]:
    """Top Search Console queries for the site, weighted on discovery's scale. For a project, only when GSC is on. On a
    site's first analysis (no project yet), when Google is connected and a property covers the site."""
    if ui is None:
        return []
    if project is None:
        snapshot = _first_analysis_snapshot(ui, pages)
    elif not _active(ui, project):
        return []
    else:
        try:
            snapshot = refresh_project(ui, project)
        except GscError:
            snapshot = load_snapshot(project)
        if snapshot and snapshot.get("property") != load_settings(project)["property"]:
            snapshot = None
    if not snapshot:
        return []
    ranked = [item for item in snapshot.get("queries") or [] if item.get("impressions", 0) > 0]
    if not ranked:
        return []
    top = max(item["impressions"] for item in ranked)
    floor = discovery_min_impressions(top)
    queries = [item for item in ranked if item["impressions"] >= floor][:DISCOVERY_LIMIT]
    if not queries:
        return []
    return [
        {
            "q": item["q"],
            "weight": discovery_weight(item["impressions"], top),
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
    section = report_section(ui, project)
    section.update(rank_source(section, report.get("rankings") or []))
    report[NAME] = section


def rank_source(section: dict, rankings: list) -> dict:
    """Search Console as a free rank source. For each tracked search Google has no results for yet (no search data
    key, or not pulled), the Search Console average position and the page that gets most of its clicks. Kept apart
    from Google top 10 positions: its own key and its own label, never merged into a ranking row."""
    if "range" not in section:
        return {}
    searches = section.get("searches") or {}
    positions = {}
    for row in rankings:
        if not isinstance(row, dict) or row.get("checked"):
            continue
        q = str(row.get("q") or "")
        hit = searches.get(q)
        if hit and hit.get("position"):
            positions[q] = {key: hit[key] for key in ("position", "clicks", "impressions", "ctr") if key in hit}
            positions[q]["page"] = hit.get("page", "")
    return {
        "positions": positions,
        "positions_label": f"Search Console average position, {section['range']['label']}",
        "positions_note": "Average position over 28 days from Google Search Console, for searches without Google top 10 results "
        "yet. It is not a Google top 10 position and is never mixed into one.",
    }


FEED_LIMIT = 15


def feed_urls(project, snapshot: dict) -> list[str]:
    """The site's own pages Search Console says matter: the best page for each tracked search, then the pages with the
    most clicks. Only the project's own site."""
    wanted: list[str] = []
    tracked = {_loose(q) for q in getattr(project, "searches", None) or []}
    best: dict[str, dict] = {}
    for pair in snapshot.get("query_pages") or []:
        key = _loose(pair["q"])
        if key in tracked and (key not in best or (pair["clicks"], pair["impressions"]) > (best[key]["clicks"], best[key]["impressions"])):
            best[key] = pair
    wanted.extend(pair["page"] for pair in best.values())
    wanted.extend(row["page"] for row in snapshot.get("pages") or [])
    out, seen = [], set()
    for url in wanted:
        key = page_key(url)
        if key in seen or not project.is_our_url(url) or urlsplit(url).scheme not in {"http", "https"}:
            continue
        seen.add(key)
        out.append(url)
    return out[:FEED_LIMIT]


def feed_pages(ui, project) -> list[str]:
    """Fetch and store the Search Console pages the site read has not stored yet, so Issues covers them. Returns the
    URLs fetched. Uses the app's crawler (robots respected, private addresses refused), or the test fetcher."""
    snapshot = load_snapshot(project)
    if not snapshot or snapshot.get("property") != load_settings(project)["property"]:
        return []
    urls = feed_urls(project, snapshot)
    if not urls:
        return []
    from aeo.ranksim.project import _crawler_batch, _record_fetch
    from aeo.ranksim.store import RsPage, open_session

    session = open_session(ui.database)
    try:
        stored = {page_key(row.url) for row in session.query(RsPage).filter(RsPage.domain.in_([project.domain, "www." + project.domain.removeprefix("www.")])).all() if row.status == "fetched"}
        missing = [url for url in urls if page_key(url) not in stored]
        if not missing:
            return []
        fetch = (getattr(ui, "pull_options", None) or {}).get("fetcher") or _crawler_batch(ui.settings, 0.2)
        fetched = []
        for url in missing:
            try:
                result = fetch([url])[0]
            except Exception:  # noqa: BLE001 - one page that cannot be read never stops the rest
                continue
            _record_fetch(session, url, result, None, True, [])
            fetched.append(url)
        session.commit()
    finally:
        session.close()
    return fetched


def audit_checks() -> list:
    return [check_title_uses_search]


def check_title_uses_search(ctx) -> list[dict]:
    """For tracked searches Google has no results for yet: when Search Console shows the site's best page for the search
    and that page's title does not use the search's words, say so on the title."""
    project = getattr(ctx, "project", None)
    if project is None or not getattr(project, "dir", None):
        return []
    settings = load_settings(project)
    snapshot = load_snapshot(project)
    if not (settings["enabled"] and settings["property"] and snapshot and snapshot.get("property") == settings["property"]):
        return []
    section = {"range": {"label": _range_label(snapshot["start"], snapshot["end"])}, "searches": _search_rows(project, snapshot)}
    positions = rank_source(section, getattr(ctx, "rankings", None) or [])["positions"]
    pages = {page_key(page.url): page for page in getattr(ctx, "readable", None) or []}
    issues = []
    for q, hit in positions.items():
        page = pages.get(page_key(hit.get("page") or ""))
        if page is None:
            continue
        words = [word for word in _loose(q).split() if len(word) > 2]
        title = _loose(page.title)
        if not words or all(word in title for word in words):
            continue
        issues.append(
            {
                "severity": "medium",
                "kind": "gsc-title-search",
                "title": f'Title does not use "{q}"',
                "comment": f'Google Search Console shows this page at average position {hit["position"]} for "{q}" '
                f'({hit.get("impressions", 0)} impressions, {section["range"]["label"]}). Its title does not use those words.',
                "fix": "Work the search into the title, near the start, in words a person would click.",
                "page": page.url,
                "search": q,
                "element": {"selector": "title", "text": (page.title or "")[:160]},
                "evidence": [f"Search Console average position {hit['position']}", f"{hit.get('impressions', 0)} impressions in 28 days"],
            }
        )
    return issues


def _search_rows(project, snapshot: dict) -> dict:
    by_query = {_loose(item["q"]): item for item in snapshot.get("queries") or []}
    best: dict[str, dict] = {}
    for pair in snapshot.get("query_pages") or []:
        key = _loose(pair["q"])
        if key not in best or (pair["clicks"], pair["impressions"]) > (best[key]["clicks"], best[key]["impressions"]):
            best[key] = pair
    rows = {}
    for q in getattr(project, "searches", None) or []:
        row = by_query.get(_loose(q))
        if row:
            rows[q] = {**{k: row[k] for k in ("clicks", "impressions", "ctr", "position")}, "page": best[_loose(q)]["page"] if _loose(q) in best else ""}
    return rows


def after_pull(*, project=None, report=None, ui=None) -> None:
    if ui is None or project is None:
        return
    _adopt(ui, project)
    if not _active(ui, project):
        return
    try:
        refresh_project(ui, project)
    except GscError:
        pass  # recorded in settings; the pull itself is not affected
    try:
        feed_pages(ui, project)
    except Exception as exc:  # noqa: BLE001 - reading extra pages never breaks a pull
        print(f"ranksim: gsc: page feed skipped: {type(exc).__name__}", flush=True)


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


def _save_cloud(ui) -> None:
    """Save to cloud storage for writes the middleware does not cover (a GET, a failed POST). Never fatal here."""
    from aeo.ranksim import plugins

    try:
        plugins.save(ui)
    except Exception as exc:  # noqa: BLE001 - the change is on this instance; report but do not fail the request
        print(f"ranksim: gsc: cloud save failed: {type(exc).__name__}", flush=True)


def register(app, ui) -> None:
    from aeo.ranksim.api import common

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

    @app.get("/api/v1/gsc/projects/{slug}/page")
    def gsc_page(slug: str, url: str = "") -> JSONResponse:
        project = project_or_404(slug)
        if project is None:
            return common._error(404, "No such project.")
        return JSONResponse(page_data(ui, project, url[:2000]))

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
        state, nonce, challenge = make_state(ui, request, project.slug, uri)
        response = JSONResponse({"url": auth_url(config, uri, state, challenge=challenge)})
        secure = uri.startswith("https://")
        response.set_cookie(NONCE_COOKIE, nonce, max_age=STATE_SECONDS, httponly=True, secure=secure, samesite="lax", path="/api/v1/gsc")
        return response

    def back(slug: str | None, result: str, *, keep_nonce: bool = False) -> RedirectResponse:
        target = f"/app/{slug}/settings?gsc={result}" if slug else f"/app?gsc={result}"
        response = RedirectResponse(target, status_code=303)
        if not keep_nonce:
            response.delete_cookie(NONCE_COOKIE, path="/api/v1/gsc")
        response.delete_cookie(PENDING_COOKIE, path="/api/v1/gsc")
        return response

    async def finish(project, code: str, payload: dict) -> str:
        from aeo.ranksim.cloud import CloudError

        try:
            return await run_in_threadpool(complete_connection, ui, project, code, payload)
        except CloudError:
            return "storage"
        except GscError as exc:
            return exc.code
        except secretbox.SecretBoxError:
            return "not_configured"

    @app.get("/api/v1/gsc/callback")
    async def gsc_callback(request: Request) -> RedirectResponse:
        """Google's redirect back. login_exempt so a signed-out owner is sent to sign in instead of a JSON 401: the
        state is still checked (sealed, unexpired, nonce cookie from this browser) before anything else, and the code
        is only parked in a sealed, HttpOnly cookie until the owner signs in and the app calls /resume."""
        params = request.query_params
        if ui.public_demo or not oauth_config()["ready"]:
            return back(None, "not_configured")
        signed_in = common._signed_in(ui, request)
        try:
            payload = check_state(ui, request, params.get("state") or "", bind_session=signed_in)
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
        if not signed_in:
            parked = secretbox.seal(json.dumps({"code": code, "state": params.get("state"), "exp": payload["exp"]}), purpose=PENDING_PURPOSE)
            response = back(project.slug, "signin", keep_nonce=True)
            secure = str(payload.get("redirect_uri") or "").startswith("https://")
            left = max(1, int(float(payload["exp"]) - _now(ui)))
            response.set_cookie(PENDING_COOKIE, parked, max_age=left, httponly=True, secure=secure, samesite="lax", path="/api/v1/gsc")
            return response
        return back(project.slug, await finish(project, code, payload))

    gsc_callback.login_exempt = True

    @app.post("/api/v1/gsc/resume")
    async def gsc_resume(request: Request) -> JSONResponse:
        """Finish a connection Google sent back while the owner was signed out. Signed in (middleware) and CSRF
        checked; the parked state must still be valid with its nonce cookie. The old session binding is not checked,
        because the owner signed in again in between; the nonce ties the flow to this browser."""
        blocked = guard_write(request)
        if blocked is not None:
            return blocked

        def answer(result: str, slug: str = "", status: int = 200) -> JSONResponse:
            body = {"result": result, "slug": slug}
            if status >= 400:
                body["error"] = "Search Console sign in did not finish. Turn it on again to retry."
            response = JSONResponse(body, status_code=status)
            response.delete_cookie(PENDING_COOKIE, path="/api/v1/gsc")
            response.delete_cookie(NONCE_COOKIE, path="/api/v1/gsc")
            return response

        try:
            parked = json.loads(secretbox.unseal(request.cookies.get(PENDING_COOKIE) or "", purpose=PENDING_PURPOSE))
            payload = check_state(ui, request, str(parked.get("state") or ""), bind_session=False)
        except (secretbox.SecretBoxError, ValueError, AttributeError):
            return answer("nothing_pending", status=409)
        except GscError as exc:
            return answer(exc.code, status=409)
        project = project_or_404(str(payload.get("slug") or ""))
        if project is None:
            return answer("no_project", status=404)
        return answer(await finish(project, str(parked.get("code") or ""), payload), project.slug)

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
            await run_in_threadpool(_save_cloud, ui)  # a failed POST is not mirrored by the middleware; keep the error state
            return fail(exc)
        return JSONResponse(status_payload(request, project))

    @app.post("/api/v1/gsc/disconnect")
    async def gsc_disconnect(request: Request) -> JSONResponse:
        blocked = guard_write(request)
        if blocked is not None:
            return blocked
        revoked = await run_in_threadpool(revoke, ui)
        return JSONResponse({"ok": True, "revoked": revoked, "connected": False})
