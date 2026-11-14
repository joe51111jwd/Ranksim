"""Google Search Console plugin and the secretbox it stores tokens with. Every Google endpoint is a fake; no network."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from aeo.ranksim import secretbox
from aeo.ranksim.plugins import gsc
from aeo.ranksim.project import create_project, load_project
from aeo.ranksim.serve import create_app
from tests.test_serve_project import make_settings

PASSWORD = "gsc owner password"
CLIENT_ID = "fake-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "fake-client-secret-XYZ"
SECRET = "test-ranksim-secret-0123456789-abcdefgh"
NOW = 1_794_000_000.0  # 2026-11-06 (UTC)
DASHES = ("—", "–")


# -- a fake Google ------------------------------------------------------------------------------------------------


class FakeGoogle:
    """Answers the token, revoke, sites.list and searchAnalytics.query endpoints and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.issued = 0
        self.valid_access: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.consents = 0
        self.revoked: list[str] = []
        self.sites = [
            {"siteUrl": "sc-domain:dirtcrew.example", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://other.example/", "permissionLevel": "siteFullUser"},
            {"siteUrl": "https://unverified.example/", "permissionLevel": "siteUnverifiedUser"},
        ]
        self.scope = gsc.SCOPE
        self.give_refresh = True
        self.fail_next_api_with: int | None = None
        self.forbidden_sites: set[str] = set()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _token(self) -> str:
        self.issued += 1
        token = f"access-{self.issued}"
        self.valid_access.add(token)
        return token

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == gsc.TOKEN_URL:
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.calls.append(("token", form["grant_type"], form))
            if form.get("client_id") != CLIENT_ID or form.get("client_secret") != CLIENT_SECRET:
                return httpx.Response(401, json={"error": "invalid_client"})
            if form["grant_type"] == "authorization_code":
                if form.get("code") != "good-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                reply = {"access_token": self._token(), "expires_in": 3599, "scope": self.scope, "token_type": "Bearer"}
                if self.give_refresh:
                    self.consents += 1
                    refresh = f"refresh-{self.consents}"  # prompt=consent: a new refresh token every time
                    self.refresh_tokens.add(refresh)
                    reply["refresh_token"] = refresh
                return httpx.Response(200, json=reply)
            if form["grant_type"] == "refresh_token":
                if form.get("refresh_token") not in self.refresh_tokens:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
                return httpx.Response(200, json={"access_token": self._token(), "expires_in": 3599, "scope": self.scope})
        if request.method == "POST" and url == gsc.REVOKE_URL:
            token = parse_qs(request.content.decode())["token"][0]
            self.calls.append(("revoke", token, {}))
            self.revoked.append(token)
            # Like Google: revoking any token ends the whole grant for this account and client.
            self.refresh_tokens.clear()
            self.valid_access.clear()
            return httpx.Response(200, json={})
        if url.startswith(gsc.API_BASE):
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ")
            path = url[len(gsc.API_BASE) :]
            body = json.loads(request.content or b"{}") if request.method == "POST" else {}
            self.calls.append(("api", path, body))
            if self.fail_next_api_with is not None:
                status, self.fail_next_api_with = self.fail_next_api_with, None
                return httpx.Response(status, json={"error": {"code": status, "message": "fake failure"}})
            if token not in self.valid_access:
                return httpx.Response(401, json={"error": {"code": 401, "message": "Request had invalid authentication credentials."}})
            if path == "/sites":
                return httpx.Response(200, json={"siteEntry": self.sites})
            match = re.match(r"^/sites/([^/]+)/searchAnalytics/query$", path)
            if match:
                site = unquote(match.group(1))
                if site in self.forbidden_sites:
                    return httpx.Response(403, json={"error": {"code": 403, "message": "User does not have sufficient permission"}})
                return httpx.Response(200, json=self.analytics(body))
        return httpx.Response(404, json={"error": "unexpected call " + url})

    @staticmethod
    def analytics(body: dict) -> dict:
        dims = body.get("dimensions") or []
        if not dims:
            return {"rows": [{"clicks": 120, "impressions": 6400, "ctr": 0.018751, "position": 14.23}]}
        if dims == ["query"]:
            return {
                "rows": [
                    {"keys": ["dirt work software"], "clicks": 30, "impressions": 900, "ctr": 0.0333, "position": 8.4},
                    {"keys": ["heavy civil software"], "clicks": 50, "impressions": 3000, "ctr": 0.01667, "position": 11.06},
                    {"keys": ["dirt crew login"], "clicks": 40, "impressions": 100, "ctr": 0.4, "position": 1.0},
                ]
            }
        if dims == ["page"]:
            return {
                "rows": [
                    {"keys": ["https://www.dirtcrew.example/"], "clicks": 70, "impressions": 3500, "ctr": 0.02, "position": 9.0},
                    {"keys": ["https://www.dirtcrew.example/pricing"], "clicks": 50, "impressions": 900, "ctr": 0.0556, "position": 6.2},
                ]
            }
        if dims == ["query", "page"]:
            return {
                "rows": [
                    {"keys": ["heavy civil software", "https://www.dirtcrew.example/"], "clicks": 45, "impressions": 2800, "ctr": 0.016, "position": 10.9},
                    {"keys": ["heavy civil software", "https://www.dirtcrew.example/pricing"], "clicks": 5, "impressions": 200, "ctr": 0.025, "position": 13.0},
                    {"keys": ["dirt work software", "https://www.dirtcrew.example/pricing"], "clicks": 30, "impressions": 900, "ctr": 0.0333, "position": 8.4},
                ]
            }
        return {"rows": []}

    def api_bodies(self) -> list[dict]:
        return [body for kind, path, body in self.calls if kind == "api" and path.endswith("/searchAnalytics/query")]


# -- fixtures -----------------------------------------------------------------------------------------------------


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv(gsc.CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(gsc.CLIENT_SECRET_ENV, CLIENT_SECRET)
    monkeypatch.setenv(secretbox.SECRET_ENV, SECRET)
    monkeypatch.delenv(gsc.REDIRECT_ENV, raising=False)


@pytest.fixture()
def no_env(monkeypatch):
    for name in (gsc.CLIENT_ID_ENV, gsc.CLIENT_SECRET_ENV, gsc.REDIRECT_ENV, secretbox.SECRET_ENV):
        monkeypatch.delenv(name, raising=False)


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _app(tmp_path, *, password: str | None = PASSWORD, public: bool = False, site: str = "www.dirtcrew.example"):
    settings = make_settings(tmp_path)
    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        public_demo=public,
        access_password=password,
        access_secret="session-secret",
    )
    ui = app.state.ui
    google = FakeGoogle()
    ui.gsc_transport = google.transport()
    ui.gsc_clock = Clock()
    gsc.register(app, ui)  # until the plugin loader lands, the test wires the plugin in directly
    project = None
    if not public:
        project = create_project("Dirt Crew", site, ["heavy civil software", "dirt work software", "excavation estimating"], root=tmp_path / "projects")
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)
    return SimpleNamespace(app=app, ui=ui, google=google, client=client, token=token, project=project, tmp=tmp_path)


def _post(box, path, body=None, *, csrf=True):
    headers = {"x-ranksim-csrf": box.token} if csrf else {}
    return box.client.post(path, json=body or {}, headers=headers)


def _sign_in(box) -> None:
    assert _post(box, "/api/v1/login", {"password": PASSWORD}).status_code == 200


def _connect(box) -> dict:
    """Start the flow and return the parsed Google authorization URL query."""
    started = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/connect")
    assert started.status_code == 200, started.text
    url = started.json()["url"]
    assert url.startswith(gsc.AUTH_URL + "?")
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def _callback(box, params: dict):
    return box.client.get("/api/v1/gsc/callback", params=params, follow_redirects=False)


def _project(box):
    return load_project(box.project.slug, root=box.tmp / "projects")


# -- secretbox ----------------------------------------------------------------------------------------------------


def test_secretbox_round_trip_and_fresh_nonce(monkeypatch):
    monkeypatch.setenv(secretbox.SECRET_ENV, SECRET)
    one = secretbox.seal("refresh-token-value", purpose="gsc-refresh-token")
    two = secretbox.seal("refresh-token-value", purpose="gsc-refresh-token")
    assert one != two and one.startswith("v1.")
    assert "refresh-token-value" not in one
    assert secretbox.unseal(one, purpose="gsc-refresh-token") == "refresh-token-value"
    assert secretbox.unseal(secretbox.seal("", purpose="p"), purpose="p") == ""
    assert secretbox.unseal(secretbox.seal("café \U0001f600", purpose="p"), purpose="p") == "café \U0001f600"


def test_secretbox_rejects_tampering(monkeypatch):
    monkeypatch.setenv(secretbox.SECRET_ENV, SECRET)
    token = secretbox.seal("secret value", purpose="p")
    body = token[3:]
    for index in (0, len(body) // 2, len(body) - 2):
        flipped = body[:index] + ("A" if body[index] != "A" else "B") + body[index + 1 :]
        with pytest.raises(secretbox.SecretBoxError):
            secretbox.unseal("v1." + flipped, purpose="p")
    for bad in ("", "v1.", "v2." + body, body, "v1.!!!!", "v1." + body[:10], None, 42):
        with pytest.raises(secretbox.SecretBoxError):
            secretbox.unseal(bad, purpose="p")


def test_secretbox_wrong_purpose_and_wrong_key(monkeypatch):
    monkeypatch.setenv(secretbox.SECRET_ENV, SECRET)
    token = secretbox.seal("secret value", purpose="gsc-refresh-token")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(token, purpose="publish-credentials")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.unseal(token, purpose="")
    monkeypatch.setenv(secretbox.SECRET_ENV, "a-completely-different-secret")
    with pytest.raises(secretbox.SecretBoxError) as caught:
        secretbox.unseal(token, purpose="gsc-refresh-token")
    assert "secret value" not in str(caught.value) and SECRET not in str(caught.value)


def test_secretbox_needs_a_secret(monkeypatch):
    monkeypatch.delenv(secretbox.SECRET_ENV, raising=False)
    assert not secretbox.configured()
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.seal("x", purpose="p")
    monkeypatch.setenv(secretbox.SECRET_ENV, "short-password-123")
    assert not secretbox.configured()
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.seal("x", purpose="p")
    with pytest.raises(secretbox.SecretBoxError):
        secretbox.seal(b"bytes", purpose="p")  # type: ignore[arg-type]


# -- not set up ---------------------------------------------------------------------------------------------------


def test_not_configured_says_exactly_what_to_do(tmp_path, no_env):
    box = _app(tmp_path)
    _sign_in(box)
    status = box.client.get("/api/v1/gsc/status").json()
    assert status["configured"] is False and status["connected"] is False
    setup = status["setup"]
    assert set(setup["missing"]) == {gsc.CLIENT_ID_ENV, gsc.CLIENT_SECRET_ENV, secretbox.SECRET_ENV}
    text = " ".join(setup["steps"])
    for needed in ("Web application", "Search Console API", gsc.HOSTED_REDIRECT_URI, gsc.CLIENT_ID_ENV, gsc.CLIENT_SECRET_ENV, "Vercel", "In production"):
        assert needed in text
    assert "http://testserver/api/v1/gsc/callback" in text  # the address this server answers on is listed too
    project = box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()
    assert project["state"] == "not_configured" and project["setup"]["steps"]
    refused = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/connect")
    assert refused.status_code == 409 and gsc.CLIENT_ID_ENV in refused.json()["error"]
    report: dict = {}
    gsc.extend_report(report, project=box.project, meta=None, database=None, ctx=None, ui=box.ui)
    assert report["gsc"]["state"] == "not_configured" and report["gsc"]["label"] == gsc.LABEL
    assert gsc.discovery_sources(project=box.project, pages=[], ui=box.ui) == []
    copy = json.dumps([status, project, report], ensure_ascii=False)
    assert not any(dash in copy for dash in DASHES)
    assert box.google.calls == []


# -- the full flow ------------------------------------------------------------------------------------------------


def test_full_flow_connect_pick_fetch_report_refresh_disconnect(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    slug = box.project.slug
    assert box.client.get(f"/api/v1/gsc/projects/{slug}").json()["state"] == "off"

    turned_on = _post(box, f"/api/v1/gsc/projects/{slug}", {"enabled": True})
    assert turned_on.status_code == 200
    assert turned_on.json()["state"] == "not_connected" and turned_on.json()["needs_connect"] is True

    query = _connect(box)
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == "http://testserver/api/v1/gsc/callback"
    assert query["scope"] == gsc.SCOPE
    assert query["access_type"] == "offline" and query["prompt"] == "consent" and query["response_type"] == "code"
    assert CLIENT_SECRET not in json.dumps(query)
    assert box.client.cookies.get(gsc.NONCE_COOKIE)

    done = _callback(box, {"code": "good-code", "state": query["state"]})
    assert done.status_code == 303
    assert done.headers["location"] == f"/app/{slug}/settings?gsc=connected"

    exchange = [form for kind, grant, form in box.google.calls if kind == "token"][0]
    assert exchange["grant_type"] == "authorization_code" and exchange["redirect_uri"] == query["redirect_uri"]

    # Tokens are sealed at rest: nothing on disk holds them in the clear.
    stored = gsc.account_path(box.ui).read_text()
    for plain in ("refresh-1", "access-1", CLIENT_SECRET):
        assert plain not in stored
    for path in (box.tmp / "projects").rglob("*"):
        if path.is_file():
            assert "refresh-1" not in path.read_text(errors="ignore")

    # The domain property was matched automatically and 28 days of data were fetched.
    status = box.client.get(f"/api/v1/gsc/projects/{slug}").json()
    assert status["state"] == "ready" and status["connected"] and status["enabled"]
    assert status["property"] == "sc-domain:dirtcrew.example"
    assert status["range"] == {"start": "2026-10-08", "end": "2026-11-04", "days": 28, "label": "Oct 8 to Nov 4, 2026"}
    assert status["totals"] == {"clicks": 120, "impressions": 6400, "ctr": 0.0188, "position": 14.2}
    bodies = box.google.api_bodies()
    assert [body["dimensions"] for body in bodies] == [[], ["query"], ["page"], ["query", "page"]]
    assert all(body["startDate"] == "2026-10-08" and body["endDate"] == "2026-11-04" for body in bodies)
    assert all(body.get("rowLimit") == 1000 for body in bodies[1:])
    assert any(path == "/sites/sc-domain%3Adirtcrew.example/searchAnalytics/query" for kind, path, _ in box.google.calls)

    snapshots = list((box.project.dir / "plugins" / "gsc" / "snapshots").glob("*.json"))
    assert len(snapshots) == 1 and (box.project.dir / "plugins" / "gsc" / "latest.json").is_file()

    # The report section: labeled, dated, top queries by impressions, top pages by clicks, per-search numbers.
    report: dict = {"rankings": []}
    gsc.extend_report(report, project=_project(box), meta=None, database=None, ctx=None, ui=box.ui)
    section = report["gsc"]
    assert report["rankings"] == [] and section["label"] == "Google Search Console data"
    assert [row["q"] for row in section["queries"]] == ["heavy civil software", "dirt work software", "dirt crew login"]
    assert section["queries"][0] == {"q": "heavy civil software", "clicks": 50, "impressions": 3000, "ctr": 0.0167, "position": 11.1}
    assert [row["page"] for row in section["pages"]] == ["https://www.dirtcrew.example/", "https://www.dirtcrew.example/pricing"]
    assert section["searches"]["heavy civil software"]["page"] == "https://www.dirtcrew.example/"
    assert section["searches"]["dirt work software"]["clicks"] == 30
    assert "excavation estimating" not in section["searches"]

    # Discovery gets the top queries weighted by impressions.
    sources = gsc.discovery_sources(project=_project(box), pages=[], ui=box.ui)
    assert [row["q"] for row in sources] == ["heavy civil software", "dirt work software", "dirt crew login"]
    assert sources[0]["weight"] == 1.0 and sources[1]["weight"] == 0.3 and all(row["source"] == "gsc" for row in sources)
    calls_before = len(box.google.calls)
    gsc.after_pull(project=_project(box), report=None, ui=box.ui)
    assert len(box.google.calls) == calls_before  # fresh data is not fetched again

    # An hour later the access token has expired: a refresh grant is used, then the data is fetched again.
    box.ui.gsc_clock.now += 13 * 3600
    refreshed = _post(box, f"/api/v1/gsc/projects/{slug}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["state"] == "ready"
    grants = [grant for kind, grant, _ in box.google.calls if kind == "token"]
    assert grants == ["authorization_code", "refresh_token"]
    assert len(list((box.project.dir / "plugins" / "gsc" / "snapshots").glob("*.json"))) == 2

    # Disconnect revokes the grant at Google and forgets it here.
    gone = _post(box, "/api/v1/gsc/disconnect")
    assert gone.status_code == 200 and gone.json()["revoked"] is True
    assert box.google.revoked == ["refresh-1"]
    assert not gsc.account_path(box.ui).exists()
    after = box.client.get(f"/api/v1/gsc/projects/{slug}").json()
    assert after["state"] == "not_connected" and after["connected"] is False
    assert gsc.discovery_sources(project=_project(box), pages=[], ui=box.ui) == []


def test_turning_off_hides_the_data_from_discovery_but_keeps_the_connection(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    off = _post(box, f"/api/v1/gsc/projects/{box.project.slug}", {"enabled": False})
    assert off.status_code == 200 and off.json()["state"] == "off" and off.json()["connected"] is True
    assert gsc.discovery_sources(project=_project(box), pages=[], ui=box.ui) == []
    report: dict = {}
    gsc.extend_report(report, project=_project(box), meta=None, database=None, ctx=None, ui=box.ui)
    assert report["gsc"]["state"] == "off" and "queries" not in report["gsc"]
    on = _post(box, f"/api/v1/gsc/projects/{box.project.slug}", {"enabled": True})
    assert on.json()["state"] == "ready" and "needs_connect" not in on.json()


def test_a_401_from_the_api_refreshes_once_and_retries(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    box.google.valid_access.clear()  # Google forgot the access token early
    result = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert result.status_code == 200 and result.json()["state"] == "ready"
    assert [grant for kind, grant, _ in box.google.calls if kind == "token"] == ["authorization_code", "refresh_token"]


def test_revoked_grant_turns_into_not_connected(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    box.google.refresh_tokens.clear()  # the user removed access in their Google account
    box.ui.gsc_clock.now += 2 * 3600
    result = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert result.status_code == 401 and "Turn it on again" in result.json()["error"]
    assert not gsc.account_path(box.ui).exists()
    assert box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()["state"] == "not_connected"


# -- property selection -------------------------------------------------------------------------------------------


def test_no_matching_property_asks_the_owner_to_pick(tmp_path, env):
    box = _app(tmp_path, site="www.newclient.example")
    _sign_in(box)
    query = _connect(box)
    done = _callback(box, {"code": "good-code", "state": query["state"]})
    assert done.headers["location"].endswith("?gsc=needs_property")
    slug = box.project.slug
    assert box.client.get(f"/api/v1/gsc/projects/{slug}").json()["state"] == "needs_property"
    listed = box.client.get(f"/api/v1/gsc/projects/{slug}/properties").json()
    assert [item["url"] for item in listed["properties"]] == ["sc-domain:dirtcrew.example", "https://other.example/", "https://unverified.example/"]
    assert listed["suggested"] == "" and listed["properties"][-1]["usable"] is False
    assert _post(box, f"/api/v1/gsc/projects/{slug}", {"property": "https://unverified.example/"}).status_code == 400
    assert _post(box, f"/api/v1/gsc/projects/{slug}", {"property": "https://stranger.example/"}).status_code == 400
    picked = _post(box, f"/api/v1/gsc/projects/{slug}", {"property": "https://other.example/"})
    assert picked.status_code == 200 and picked.json()["state"] == "ready" and picked.json()["property"] == "https://other.example/"
    assert box.google.api_bodies()  # data was fetched for the chosen property


def test_property_matching_rules():
    def project(site):
        return SimpleNamespace(site=site)

    def props(*urls, level="siteOwner"):
        return [{"url": url, "usable": level in gsc.GOOD_PERMISSIONS} for url in urls]

    assert gsc.match_property(project("www.example.com"), props("https://www.example.com/", "sc-domain:example.com")) == "sc-domain:example.com"
    assert gsc.match_property(project("www.example.com"), props("https://www.example.com/", "http://www.example.com/")) == "https://www.example.com/"
    assert gsc.match_property(project("example.com"), props("https://www.example.com/")) == "https://www.example.com/"
    assert gsc.match_property(project("blog.example.com"), props("sc-domain:example.com")) == "sc-domain:example.com"
    assert gsc.match_property(project("www.example.com"), props("sc-domain:example.com", level="siteUnverifiedUser")) == ""
    assert gsc.match_property(project("www.example.com"), props("sc-domain:notexample.com", "https://example.co/")) == ""
    assert gsc.match_property(project("WWW.Example.COM"), props("sc-domain:example.com")) == "sc-domain:example.com"


def test_a_forbidden_property_shows_an_error_state(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    box.google.forbidden_sites.add("sc-domain:dirtcrew.example")
    query = _connect(box)
    done = _callback(box, {"code": "good-code", "state": query["state"]})
    assert done.headers["location"].endswith("?gsc=connected")
    status = box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()
    assert status["state"] == "error" and "cannot read sc-domain:dirtcrew.example" in status["message"]
    box.google.forbidden_sites.clear()
    fixed = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert fixed.status_code == 200 and fixed.json()["state"] == "ready"


# -- state, CSRF and the session ----------------------------------------------------------------------------------


def test_callback_rejects_bad_state(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    state = query["state"]
    tampered = state[:-3] + ("AAA" if not state.endswith("AAA") else "BBB")
    assert _callback(box, {"code": "good-code", "state": tampered}).headers["location"] == "/app?gsc=bad_state"
    assert _callback(box, {"code": "good-code"}).headers["location"] == "/app?gsc=bad_state"
    forged = secretbox.seal(json.dumps({"slug": box.project.slug, "nonce": "x", "exp": NOW + 600, "sess": "", "redirect_uri": "x"}), purpose="other")
    assert _callback(box, {"code": "good-code", "state": forged}).headers["location"] == "/app?gsc=bad_state"

    # The nonce cookie must come from the browser that started the flow.
    box.client.cookies.delete(gsc.NONCE_COOKIE, path="/api/v1/gsc")
    assert _callback(box, {"code": "good-code", "state": state}).headers["location"] == "/app?gsc=bad_nonce"

    # Expired after 10 minutes.
    query = _connect(box)
    box.ui.gsc_clock.now += gsc.STATE_SECONDS + 1
    assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"] == "/app?gsc=expired_state"
    assert [kind for kind, _, _ in box.google.calls] == []  # nothing reached Google
    assert not gsc.account_path(box.ui).exists()


def test_state_is_bound_to_the_owner_session(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    payload = json.loads(secretbox.unseal(query["state"], purpose=gsc.STATE_PURPOSE))
    assert payload["sess"] and payload["slug"] == box.project.slug
    nonce = box.client.cookies.get(gsc.NONCE_COOKIE)
    from aeo.ranksim.access import COOKIE

    other_session = SimpleNamespace(cookies={COOKIE: "999.other", gsc.NONCE_COOKIE: nonce})
    with pytest.raises(gsc.GscError) as caught:
        gsc.check_state(box.ui, other_session, query["state"])
    assert caught.value.code == "bad_session"
    same_session = SimpleNamespace(cookies={COOKIE: box.client.cookies.get(COOKIE), gsc.NONCE_COOKIE: nonce})
    assert gsc.check_state(box.ui, same_session, query["state"])["slug"] == box.project.slug


def test_callback_needs_the_signed_in_owner(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _post(box, "/api/v1/logout")
    refused = _callback(box, {"code": "good-code", "state": query["state"]})
    assert refused.status_code == 401 and refused.json()["login"] is True
    assert box.google.calls == []


def test_write_routes_need_the_csrf_header(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    slug = box.project.slug
    for path, body in (
        (f"/api/v1/gsc/projects/{slug}/connect", {}),
        (f"/api/v1/gsc/projects/{slug}", {"enabled": True}),
        (f"/api/v1/gsc/projects/{slug}/refresh", {}),
        ("/api/v1/gsc/disconnect", {}),
    ):
        assert _post(box, path, body, csrf=False).status_code == 403, path
    assert gsc.load_settings(box.project)["enabled"] is False


def test_everything_needs_login(tmp_path, env):
    box = _app(tmp_path)
    for path in ("/api/v1/gsc/status", f"/api/v1/gsc/projects/{box.project.slug}", f"/api/v1/gsc/projects/{box.project.slug}/properties"):
        assert box.client.get(path).status_code == 401
    assert _post(box, f"/api/v1/gsc/projects/{box.project.slug}/connect").status_code == 401


def test_google_denied_and_missing_scope(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    denied = _callback(box, {"error": "access_denied", "state": query["state"]})
    assert denied.headers["location"].endswith("?gsc=denied")
    box.google.scope = "openid"  # the user unticked Search Console on Google's consent screen
    query = _connect(box)
    missing = _callback(box, {"code": "good-code", "state": query["state"]})
    assert missing.headers["location"].endswith("?gsc=scope_missing")
    assert not gsc.account_path(box.ui).exists() and box.google.revoked == []
    box.google.scope = gsc.SCOPE
    box.google.give_refresh = False
    query = _connect(box)
    no_refresh = _callback(box, {"code": "good-code", "state": query["state"]})
    assert no_refresh.headers["location"].endswith("?gsc=no_refresh_token")
    query = _connect(box)
    bad_code = _callback(box, {"code": "wrong-code", "state": query["state"]})
    assert bad_code.headers["location"].endswith("?gsc=invalid_grant")


def test_changed_secret_means_reconnect(tmp_path, env, monkeypatch):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    monkeypatch.setenv(secretbox.SECRET_ENV, "rotated-secret-value-0123456789-abcdefgh")
    box.ui.gsc_clock.now += 2 * 3600
    result = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert result.status_code == 409 and "reconnect" in result.json()["error"]
    assert box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()["state"] == "not_connected"


def test_local_mode_without_a_password_still_checks_the_nonce(tmp_path, env):
    box = _app(tmp_path, password=None)
    query = _connect(box)
    payload = json.loads(secretbox.unseal(query["state"], purpose=gsc.STATE_PURPOSE))
    assert payload["sess"] == ""
    done = _callback(box, {"code": "good-code", "state": query["state"]})
    assert done.headers["location"].endswith("?gsc=connected")
    assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"] == "/app?gsc=bad_nonce"  # single use


def test_public_demo_never_offers_gsc(tmp_path, env):
    box = _app(tmp_path, password=None, public=True)
    assert box.client.get("/api/v1/gsc/status").json()["configured"] is False
    assert _post(box, "/api/v1/gsc/disconnect").status_code == 403
    assert _callback(box, {"code": "good-code", "state": "x"}).headers["location"] == "/app?gsc=not_configured"
    demo = SimpleNamespace(dir=tmp_path / "demo", site="demo.example", searches=["a"])
    assert gsc.project_state(box.ui, demo)["state"] == "not_available"
    assert gsc.discovery_sources(project=demo, pages=[], ui=box.ui) == []
    assert box.google.calls == []


def test_hooks_are_safe_without_context():
    report: dict = {}
    gsc.extend_report(report, project=None, meta=None, database=None, ctx=None, ui=None)
    assert report == {}
    assert gsc.discovery_sources(project=None, pages=[], ui=None) == []
    gsc.after_pull(project=None, report=None, ui=None)
    schema = gsc.settings_schema()
    assert schema["key"] == "gsc"


def test_the_redirect_uri_follows_the_host_or_the_override(monkeypatch):
    request = SimpleNamespace(headers={"x-forwarded-proto": "https", "host": "ranksim.vercel.app"}, url=SimpleNamespace(scheme="http", netloc="x"))
    monkeypatch.delenv(gsc.REDIRECT_ENV, raising=False)
    assert gsc.redirect_uri_for(request) == gsc.HOSTED_REDIRECT_URI
    monkeypatch.setenv(gsc.REDIRECT_ENV, "https://seo.example/api/v1/gsc/callback")
    assert gsc.redirect_uri_for(request) == "https://seo.example/api/v1/gsc/callback"


def test_reconnecting_keeps_the_new_grant_working(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    for _ in range(2):
        query = _connect(box)
        assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"].endswith("?gsc=connected")
    assert box.google.revoked == []
    box.ui.gsc_clock.now += 2 * 3600  # the access token expired; the stored refresh token must still work
    result = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert result.status_code == 200 and result.json()["state"] == "ready"
    refresh = [form["refresh_token"] for kind, grant, form in box.google.calls if kind == "token" and grant == "refresh_token"]
    assert refresh == ["refresh-2"]


def test_a_wrong_client_secret_keeps_the_stored_grant(tmp_path, env, monkeypatch):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    monkeypatch.setenv(gsc.CLIENT_SECRET_ENV, "mistyped-secret")
    box.ui.gsc_clock.now += 2 * 3600
    result = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert result.status_code == 502 and gsc.CLIENT_SECRET_ENV in result.json()["error"]
    assert "mistyped-secret" not in result.text
    assert gsc.account_path(box.ui).exists()
    assert box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()["state"] == "error"
    monkeypatch.setenv(gsc.CLIENT_SECRET_ENV, CLIENT_SECRET)
    fixed = _post(box, f"/api/v1/gsc/projects/{box.project.slug}/refresh")
    assert fixed.status_code == 200 and fixed.json()["state"] == "ready"


def test_a_storage_failure_in_the_callback_redirects(tmp_path, env):
    from aeo.ranksim.cloud import CloudError

    box = _app(tmp_path)
    _sign_in(box)

    class BrokenCloud:
        enabled = True
        on_change: list = []

        def refresh(self, force=False):
            raise CloudError("Storage is not answering.")

        def save(self):
            raise CloudError("Storage is not answering.")

    query = _connect(box)
    box.ui.cloud = BrokenCloud()
    try:
        done = box.client.get("/api/v1/gsc/callback", params={"code": "good-code", "state": query["state"]}, follow_redirects=False)
    finally:
        box.ui.cloud = None
    # The storage middleware answers 503 for /api/ paths before the route runs; either way no raw 500 and nothing stored.
    assert done.status_code in {303, 503}
    assert not gsc.account_path(box.ui).exists()
