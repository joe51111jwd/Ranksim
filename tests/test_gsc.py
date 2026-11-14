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
        self.challenge = ""  # the code_challenge of the last authorization URL, checked at the exchange

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
                if self.challenge and gsc.pkce_challenge(form.get("code_verifier", "")) != self.challenge:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Missing code verifier."})
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
                    {"keys": ["https://www.dirtcrew.example/features"], "clicks": 6, "impressions": 1200, "ctr": 0.005, "position": 4.2},
                ]
            }
        if dims == ["query", "page"]:
            return {
                "rows": [
                    {"keys": ["heavy civil software", "https://www.dirtcrew.example/"], "clicks": 45, "impressions": 2800, "ctr": 0.016, "position": 10.9},
                    {"keys": ["heavy civil software", "https://www.dirtcrew.example/pricing"], "clicks": 5, "impressions": 200, "ctr": 0.025, "position": 13.0},
                    {"keys": ["dirt work software", "https://www.dirtcrew.example/pricing"], "clicks": 30, "impressions": 900, "ctr": 0.0333, "position": 8.4},
                    {"keys": ["estimating software", "https://www.dirtcrew.example/features"], "clicks": 6, "impressions": 1200, "ctr": 0.005, "position": 4.2},
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
    monkeypatch.delenv(gsc.PUBLIC_URL_ENV, raising=False)


@pytest.fixture()
def no_env(monkeypatch):
    for name in (gsc.CLIENT_ID_ENV, gsc.CLIENT_SECRET_ENV, gsc.REDIRECT_ENV, gsc.PUBLIC_URL_ENV, secretbox.SECRET_ENV):
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
    query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    box.google.challenge = query["code_challenge"]  # what Google remembers for this code
    return query


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
    assert len(setup["steps"]) <= 5 and gsc.HOSTED_REDIRECT_URI not in text  # built from this server's address
    for needed in ("Web application", "Search Console API", gsc.CLIENT_ID_ENV, gsc.CLIENT_SECRET_ENV, "In production", secretbox.SECRET_ENV):
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
    assert [row["page"] for row in section["pages"]] == ["https://www.dirtcrew.example/", "https://www.dirtcrew.example/pricing", "https://www.dirtcrew.example/features"]
    assert section["searches"]["heavy civil software"]["page"] == "https://www.dirtcrew.example/"
    assert section["searches"]["dirt work software"]["clicks"] == 30
    assert "excavation estimating" not in section["searches"]

    # Discovery gets the top queries weighted by impressions.
    sources = gsc.discovery_sources(project=_project(box), pages=[], ui=box.ui)
    assert [row["q"] for row in sources] == ["heavy civil software", "dirt work software", "dirt crew login"]
    assert sources[0]["weight"] == 5.0 and sources[1]["weight"] == gsc.discovery_weight(900, 3000) and all(row["source"] == "gsc" for row in sources)
    assert sources[1]["weight"] == 3.25 and sources[2]["weight"] == round(2.5 + 2.5 * 100 / 3000, 3)
    assert all(2.5 <= row["weight"] <= 5.0 for row in sources)  # every GSC query clears discovery's 2.5 bar
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


def test_signed_out_callback_sends_the_owner_to_sign_in_then_finishes(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _post(box, "/api/v1/logout")
    parked = _callback(box, {"code": "good-code", "state": query["state"]})
    assert parked.status_code == 303 and parked.headers["location"] == f"/app/{box.project.slug}/settings?gsc=signin"
    pending = box.client.cookies.get(gsc.PENDING_COOKIE)
    assert pending and "good-code" not in pending  # sealed, never the bare code
    assert box.google.calls == [] and not gsc.account_path(box.ui).exists()
    # Signed out, nothing else opens: the resume route needs the login.
    assert _post(box, "/api/v1/gsc/resume").status_code == 401
    _sign_in(box)
    assert _post(box, "/api/v1/gsc/resume", csrf=False).status_code == 403
    done = _post(box, "/api/v1/gsc/resume")
    assert done.status_code == 200 and done.json()["result"] == "connected" and done.json()["slug"] == box.project.slug
    assert gsc.account_path(box.ui).exists()
    assert box.client.get(f"/api/v1/gsc/projects/{box.project.slug}").json()["state"] == "ready"
    again = _post(box, "/api/v1/gsc/resume")
    assert again.status_code == 409 and again.json()["result"] == "nothing_pending"  # single use


def test_signed_out_callback_still_checks_the_state(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _post(box, "/api/v1/logout")
    tampered = query["state"][:-3] + ("AAA" if not query["state"].endswith("AAA") else "BBB")
    assert _callback(box, {"code": "good-code", "state": tampered}).headers["location"] == "/app?gsc=bad_state"
    assert gsc.PENDING_COOKIE not in box.client.cookies
    # Another browser (no nonce cookie) gets nothing parked.
    other = TestClient(box.app)
    refused = other.get("/api/v1/gsc/callback", params={"code": "good-code", "state": query["state"]}, follow_redirects=False)
    assert refused.headers["location"] == "/app?gsc=bad_nonce" and gsc.PENDING_COOKIE not in other.cookies
    # A parked code expires with its state. (A failed callback ended the flow above, so start a new one.)
    _sign_in(box)
    query = _connect(box)
    _post(box, "/api/v1/logout")
    assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"].endswith("?gsc=signin")
    _sign_in(box)
    box.ui.gsc_clock.now += gsc.STATE_SECONDS + 1
    late = _post(box, "/api/v1/gsc/resume")
    assert late.status_code == 409 and late.json()["result"] == "expired_state"
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


def test_the_app_loads_the_plugin_and_the_report_carries_gsc(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    listed = box.client.get("/api/v1/plugins").json()
    gsc_row = next(row for row in listed["plugins"] if row["name"] == "gsc")
    assert {"register", "extend_report", "discovery_sources", "after_pull"} <= set(gsc_row["hooks"])
    paths = [getattr(route, "path", "") for route in box.app.router.routes]
    assert paths.count("/api/v1/gsc/callback") == 1  # registered once, by the loader
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    report = box.client.get(f"/api/v1/projects/{box.project.slug}/report")
    assert report.status_code == 200, report.text
    assert report.json()["gsc"]["state"] == "ready" and report.json()["gsc"]["totals"]["clicks"] == 120


def test_discovery_merges_gsc_queries(tmp_path, env):
    from aeo.ranksim import discovery

    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    found = discovery.discover_with_plugins([], "Dirt Crew", host="www.dirtcrew.example", project=_project(box), ui=box.ui)
    assert found[:2] == ["heavy civil software", "dirt work software"]
    assert "dirt crew login" not in found  # branded, so discovery drops it


def test_discovery_weight_range():
    assert gsc.discovery_weight(3000, 3000) == 5.0
    assert gsc.discovery_weight(1, 100000) >= 2.5
    assert gsc.discovery_weight(0, 0) == 2.5
    assert all(2.5 <= gsc.discovery_weight(n, 500) <= 5.0 for n in (0, 1, 250, 500, 9999))


def test_first_analysis_uses_gsc_before_the_project_exists(tmp_path, env):
    from aeo.ranksim import discovery

    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    from aeo.ranksim.site import SitePage

    pages = [SitePage(url="https://www.other.example/", status="fetched")]  # a real page row, as discovery reads every field
    found = discovery.discover_with_plugins(pages, "Other", host="www.other.example", project=None, ui=box.ui)
    assert found[:2] == ["heavy civil software", "dirt work software"]
    assert any(path.endswith("/sites/https%3A%2F%2Fother.example%2F/searchAnalytics/query") for kind, path, _ in box.google.calls)
    # The analysis then creates the project and runs after_pull: GSC is on with the parked data, no second fetch.
    project = create_project("Other", "www.other.example", found, root=tmp_path / "projects")
    calls = len(box.google.calls)
    gsc.after_pull(project=project, report=None, ui=box.ui)
    settings = gsc.load_settings(project)
    assert settings["enabled"] and settings["property"] == "https://other.example/"
    assert gsc.project_state(box.ui, project)["state"] == "ready"
    assert len(box.google.calls) == calls
    assert not list((gsc.account_path(box.ui).parent / "pending").glob("*.json"))


def test_a_new_project_is_turned_on_but_an_off_switch_is_kept(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    fresh = create_project("Other", "other.example", ["a search"], root=tmp_path / "projects")
    gsc.after_pull(project=fresh, report=None, ui=box.ui)
    assert gsc.project_state(box.ui, fresh)["state"] == "ready"  # matched and fetched, no parked data needed
    _post(box, f"/api/v1/gsc/projects/{fresh.slug}", {"enabled": False})
    gsc.after_pull(project=fresh, report=None, ui=box.ui)
    assert gsc.load_settings(fresh)["enabled"] is False


def test_first_analysis_without_a_matching_property_adds_nothing(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    pages = [SimpleNamespace(url="https://unknown.example/")]
    assert gsc.discovery_sources(project=None, pages=pages, ui=box.ui) == []


def test_opportunities_near_page_one_and_low_ctr(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    section = box.client.get(f"/api/v1/projects/{box.project.slug}/report").json()["gsc"]
    opp = section["opportunities"]
    assert opp["min_impressions"] == 64  # 1% of 6,400
    near = opp["near_page_one"]
    assert [row["q"] for row in near] == ["heavy civil software", "dirt work software"]  # positions 11.1 and 8.4
    assert near[0]["page"] == "https://www.dirtcrew.example/"  # the page with most of its impressions
    low = opp["low_ctr"]
    assert [row["page"] for row in low] == ["https://www.dirtcrew.example/features"]  # 0.5% CTR at position 4
    assert low[0]["benchmark_ctr"] == 0.07 and low[0]["q"] == "estimating software" and "missed_clicks" not in low[0]
    assert "not Google data" in opp["rules"]["low_ctr"]
    text = json.dumps(opp, ensure_ascii=False)
    assert not any(dash in text for dash in DASHES)


def test_opportunity_rules():
    snapshot = {
        "totals": {"impressions": 1000},
        "queries": [
            {"q": "a", "clicks": 0, "impressions": 19, "ctr": 0.0, "position": 9.0},  # under the 20 floor
            {"q": "b", "clicks": 1, "impressions": 50, "ctr": 0.02, "position": 7.9},  # above page one's bottom
            {"q": "c", "clicks": 1, "impressions": 50, "ctr": 0.02, "position": 20.0},
            {"q": "d", "clicks": 1, "impressions": 50, "ctr": 0.02, "position": 20.1},
        ],
        "pages": [
            {"page": "p1", "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 1.0},  # 10% at #1: low
            {"page": "p2", "clicks": 3, "impressions": 100, "ctr": 0.03, "position": 7.0},  # typical: fine
            {"page": "p3", "clicks": 0, "impressions": 500, "ctr": 0.0, "position": 14.0},  # not on page one
        ],
        "query_pages": [],
    }
    opp = gsc.opportunities(snapshot)
    assert [row["q"] for row in opp["near_page_one"]] == ["c"]
    assert [row["page"] for row in opp["low_ctr"]] == ["p1"] and opp["low_ctr"][0]["page"] == "p1"


def test_automatic_matching_never_takes_a_parent_domain(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    pages = [SimpleNamespace(url="https://client.dirtcrew.example/")]
    assert gsc.discovery_sources(project=None, pages=pages, ui=box.ui) == []  # sc-domain:dirtcrew.example is a parent
    sub = create_project("Client", "client.dirtcrew.example", ["a search"], root=tmp_path / "projects")
    gsc.after_pull(project=sub, report=None, ui=box.ui)
    assert gsc.load_settings(sub)["enabled"] is False
    listed = len([c for c in box.google.calls if c[1] == "/sites"])
    gsc.after_pull(project=sub, report=None, ui=box.ui)
    assert len([c for c in box.google.calls if c[1] == "/sites"]) == listed  # checked once a day, not every pull
    box.ui.gsc_clock.now += gsc.ADOPT_RETRY_SECONDS + 1
    gsc.after_pull(project=sub, report=None, ui=box.ui)
    assert len([c for c in box.google.calls if c[1] == "/sites"]) == listed + 1
    # The owner can still pick the parent domain by hand.
    assert gsc.match_property(sub, [{"url": "sc-domain:dirtcrew.example", "usable": True}]) == "sc-domain:dirtcrew.example"


def test_stale_parked_data_is_deleted(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    gsc.discovery_sources(project=None, pages=[SimpleNamespace(url="https://www.other.example/")], ui=box.ui)
    folder = gsc.account_path(box.ui).parent / "pending"
    assert len(list(folder.glob("*.json"))) == 1
    box.ui.gsc_clock.now += gsc.PENDING_SECONDS + 1
    gsc.discovery_sources(project=None, pages=[SimpleNamespace(url="https://nomatch.example/")], ui=box.ui)
    assert list(folder.glob("*.json")) == []


def test_discovery_skips_rarely_seen_queries(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    box.google.analytics = staticmethod(lambda body: {"rows": [
        {"keys": ["big search"], "clicks": 10, "impressions": 5000, "ctr": 0.002, "position": 9.0},
        {"keys": ["fifty search"], "clicks": 0, "impressions": 50, "ctr": 0.0, "position": 30.0},
        {"keys": ["forty nine"], "clicks": 0, "impressions": 49, "ctr": 0.0, "position": 30.0},
        {"keys": ["seen once"], "clicks": 0, "impressions": 1, "ctr": 0.0, "position": 60.0},
    ]} if body.get("dimensions") == ["query"] else FakeGoogle.analytics(body))
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    rows = gsc.discovery_sources(project=_project(box), pages=[], ui=box.ui)
    assert [row["q"] for row in rows] == ["big search", "fifty search"]  # 1% of 5,000 is 50
    assert gsc.discovery_min_impressions(300) == 10 and gsc.discovery_min_impressions(5000) == 50 and gsc.discovery_min_impressions(5001) == 51


def test_page_data_for_the_editor_panel(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    slug = box.project.slug
    off = box.client.get(f"/api/v1/gsc/projects/{slug}/page", params={"url": "https://www.dirtcrew.example/pricing"}).json()
    assert off["state"] == "off" and "page" not in off
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    data = box.client.get(f"/api/v1/gsc/projects/{slug}/page", params={"url": "http://dirtcrew.example/pricing/?utm=x#top"}).json()
    assert data["state"] == "ready" and data["range"]["days"] == 28 and data["label"] == gsc.LABEL
    assert data["page"] == {"page": "https://www.dirtcrew.example/pricing", "clicks": 50, "impressions": 900, "ctr": 0.0556, "position": 6.2}
    assert [row["q"] for row in data["page_queries"]] == ["dirt work software", "heavy civil software"]
    home = box.client.get(f"/api/v1/gsc/projects/{slug}/page", params={"url": "https://www.dirtcrew.example"}).json()
    assert home["page"]["clicks"] == 70 and home["page_queries"][0]["q"] == "heavy civil software"
    none = box.client.get(f"/api/v1/gsc/projects/{slug}/page", params={"url": "https://www.dirtcrew.example/new-page"}).json()
    assert none["page"] is None and none["page_queries"] == []
    assert gsc.page_key("HTTPS://WWW.Example.com/A/") == "example.com/A" and gsc.page_key("https://example.com") == "example.com/"


def test_pkce_s256(tmp_path, env):
    box = _app(tmp_path)
    _sign_in(box)
    query = _connect(box)
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
    payload = json.loads(secretbox.unseal(query["state"], purpose=gsc.STATE_PURPOSE))
    assert gsc.pkce_challenge(payload["verifier"]) == query["code_challenge"]
    assert payload["verifier"] not in json.dumps(query)  # never in the URL, only sealed inside the state
    done = _callback(box, {"code": "good-code", "state": query["state"]})
    assert done.headers["location"].endswith("?gsc=connected")
    exchange = [form for kind, grant, form in box.google.calls if kind == "token"][0]
    assert exchange["code_verifier"] == payload["verifier"]
    # A code redeemed with another flow's verifier is refused by Google.
    other = _connect(box)
    box.google.challenge = "not-the-challenge-for-this-verifier-0000000"
    refused = _callback(box, {"code": "good-code", "state": other["state"]})
    assert refused.headers["location"].endswith("?gsc=invalid_grant")


def test_public_url_pins_the_redirect_uri(monkeypatch):
    request = SimpleNamespace(headers={"host": "evil.example"}, url=SimpleNamespace(scheme="http", netloc="evil.example"))
    monkeypatch.delenv(gsc.REDIRECT_ENV, raising=False)
    monkeypatch.delenv(gsc.PUBLIC_URL_ENV, raising=False)
    assert gsc.redirect_uri_for(request) == "http://evil.example/api/v1/gsc/callback"
    monkeypatch.setenv(gsc.PUBLIC_URL_ENV, "https://ranksim.example/")
    assert gsc.redirect_uri_for(request) == "https://ranksim.example/api/v1/gsc/callback"
    monkeypatch.setenv(gsc.PUBLIC_URL_ENV, "ranksim.example")
    assert gsc.redirect_uri_for(request) == "https://ranksim.example/api/v1/gsc/callback"
    monkeypatch.setenv(gsc.REDIRECT_ENV, "https://other.example/api/v1/gsc/callback")
    assert gsc.redirect_uri_for(request) == "https://other.example/api/v1/gsc/callback"


# -- Search Console as a free rank source -------------------------------------------------------------------------


def _connected_box(tmp_path, pages=None):
    from test_project import FakeCrawler

    box = _app(tmp_path)
    _sign_in(box)
    box.crawler = FakeCrawler(pages or {})
    box.ui.pull_options = {**(box.ui.pull_options or {}), "fetcher": box.crawler}
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    return box


def test_gsc_positions_for_searches_without_google_results(tmp_path, env):
    box = _connected_box(tmp_path)
    report = box.client.get(f"/api/v1/projects/{box.project.slug}/report").json()
    assert all(not row["checked"] and row["position"] is None for row in report["rankings"])  # no Google data, unchanged
    section = report["gsc"]
    assert section["positions_label"] == f"Search Console average position, {section['range']['label']}"
    assert section["positions"]["heavy civil software"] == {"position": 11.1, "clicks": 50, "impressions": 3000, "ctr": 0.0167, "page": "https://www.dirtcrew.example/"}
    assert section["positions"]["dirt work software"]["page"] == "https://www.dirtcrew.example/pricing"
    assert "excavation estimating" not in section["positions"]  # no Search Console data for it either
    assert "not a Google top 10 position" in section["positions_note"]


def test_gsc_positions_never_touch_google_rows():
    section = {"range": {"label": "Oct 1 to Oct 28, 2026"}, "searches": {"a": {"position": 4.2, "clicks": 1, "impressions": 9, "ctr": 0.1, "page": "p"}, "b": {"position": 7.0, "clicks": 0, "impressions": 3, "ctr": 0.0}}}
    rankings = [{"q": "a", "checked": True, "position": 3}, {"q": "b", "checked": False, "position": None}, {"q": "c", "checked": False}]
    out = gsc.rank_source(section, rankings)
    assert list(out["positions"]) == ["b"] and rankings[0]["position"] == 3 and "gsc" not in rankings[1]
    assert gsc.rank_source({"searches": {}}, rankings) == {}  # no data, nothing added


def test_gsc_pages_are_read_for_issues(tmp_path, env):
    from test_project import page_html

    pages = {
        "https://www.dirtcrew.example/": page_html("Heavy Civil Software for Contractors", "Heavy civil software"),
        "https://www.dirtcrew.example/pricing": page_html("Pricing | Dirt Crew", "Plans and pricing"),
        "https://www.dirtcrew.example/features": page_html("Features", "Features"),
    }
    box = _connected_box(tmp_path, pages)
    gsc.after_pull(project=_project(box), report=None, ui=box.ui)
    assert sorted(box.crawler.asked) == sorted(pages)  # best pages for tracked searches, then pages by clicks
    gsc.after_pull(project=_project(box), report=None, ui=box.ui)
    assert len(box.crawler.asked) == 3  # stored pages are not fetched again
    report = box.client.get(f"/api/v1/projects/{box.project.slug}/report").json()
    assert report["pages_read"] >= 3
    found = [issue for issue in report["issues"] if issue["kind"] == "gsc-title-search"]
    assert [(issue["search"], issue["page"]) for issue in found] == [("dirt work software", "https://www.dirtcrew.example/pricing")]
    issue = found[0]
    assert issue["element"]["selector"] == "title" and "average position 8.4" in issue["comment"] and "Google Search Console" in issue["comment"]
    assert not any(dash in json.dumps(found, ensure_ascii=False) for dash in DASHES)


def test_page_feed_stays_on_the_site_and_off_when_gsc_is_off(tmp_path, env):
    project = SimpleNamespace(searches=["x"], is_our_url=lambda url: "dirtcrew.example" in url)
    snapshot = {"query_pages": [{"q": "x", "page": "https://evil.example/x", "clicks": 9, "impressions": 9}], "pages": [{"page": "https://www.dirtcrew.example/a"}, {"page": "javascript:alert(1)"}]}
    assert gsc.feed_urls(project, snapshot) == ["https://www.dirtcrew.example/a"]
    box = _connected_box(tmp_path)
    _post(box, f"/api/v1/gsc/projects/{box.project.slug}", {"enabled": False})
    gsc.after_pull(project=_project(box), report=None, ui=box.ui)
    assert box.crawler.asked == []


def test_client_report_carries_search_console_apart_from_google(tmp_path, env):
    from aeo.ranksim.plugins import share

    box = _connected_box(tmp_path)
    data = share.client_report(box.ui, _project(box))
    sc = data["search_console"]
    assert sc["label"] == gsc.LABEL and sc["range"] and sc["totals"]["clicks"] == 120
    assert [row["q"] for row in sc["positions"]] == ["dirt work software", "heavy civil software"]  # best average first
    assert sc["positions"][0] == {"q": "dirt work software", "position": 8.4, "impressions": 900, "clicks": 30, "page": "/pricing"}
    assert all(row["position"] is None for row in data["rankings"])  # Google positions stay empty
    assert "not Google top 10 positions" in sc["note"]
    _post(box, f"/api/v1/gsc/projects/{box.project.slug}", {"enabled": False})
    share_state = share._state(box.ui)
    share_state["cache"].clear()
    assert "search_console" not in share.client_report(box.ui, _project(box))  # absent when off: the key set stays as before


# -- end to end scenarios ------------------------------------------------------------------------------------------


def _instance(tmp_path, google):
    settings = make_settings(tmp_path)
    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret="session-secret",
        csrf_token="shared-deployment-csrf-token-0123456789",
    )
    app.state.ui.gsc_transport = google.transport()
    app.state.ui.gsc_clock = Clock()
    return app


def test_connect_on_one_instance_and_finish_on_another(tmp_path, env):
    google = FakeGoogle()
    one, two = _instance(tmp_path, google), _instance(tmp_path, google)
    project = create_project("Dirt Crew", "www.dirtcrew.example", ["heavy civil software"], root=tmp_path / "projects")
    a, b = TestClient(one), TestClient(two)
    headers = {"x-ranksim-csrf": "shared-deployment-csrf-token-0123456789"}
    assert a.post("/api/v1/login", json={"password": PASSWORD}, headers=headers).status_code == 200
    started = a.post(f"/api/v1/gsc/projects/{project.slug}/connect", json={}, headers=headers).json()["url"]
    query = {key: values[0] for key, values in parse_qs(urlsplit(started).query).items()}
    google.challenge = query["code_challenge"]
    for name, value in a.cookies.items():  # the same browser, routed to another instance
        b.cookies.set(name, value)
    done = b.get("/api/v1/gsc/callback", params={"code": "good-code", "state": query["state"]}, follow_redirects=False)
    assert done.headers["location"].endswith("?gsc=connected")
    # The first instance reads what the second stored (one disk here; on Vercel the cloud mirror carries it).
    assert a.get(f"/api/v1/gsc/projects/{project.slug}").json()["state"] == "ready"


def test_token_expiring_mid_use_is_refreshed_once_and_kept(tmp_path, env):
    box = _connected_box(tmp_path)
    slug = box.project.slug
    box.ui.gsc_clock.now += 3600  # the access token expires while the owner works
    assert box.client.get(f"/api/v1/gsc/projects/{slug}/properties").status_code == 200
    assert box.client.get(f"/api/v1/gsc/projects/{slug}/properties").status_code == 200
    grants = [grant for kind, grant, _ in box.google.calls if kind == "token"]
    assert grants == ["authorization_code", "refresh_token"]  # refreshed once, then the new token is reused
    # Reports never call Google, even with an expired token.
    calls = len(box.google.calls)
    box.ui.gsc_clock.now += 3600
    assert box.client.get(f"/api/v1/projects/{slug}/report").json()["gsc"]["state"] == "ready"
    assert len(box.google.calls) == calls


def test_reconnecting_a_different_google_account_replaces_the_old_one(tmp_path, env):
    box = _connected_box(tmp_path)
    slug = box.project.slug
    assert _post(box, "/api/v1/gsc/disconnect").json()["revoked"] is True
    assert box.client.get(f"/api/v1/gsc/projects/{slug}").json()["state"] == "not_connected"
    # The other account only has a URL-prefix property: the stored property no longer exists for it.
    box.google.sites = [{"siteUrl": "https://www.dirtcrew.example/", "permissionLevel": "siteFullUser"}]
    box.google.refresh_tokens.clear()
    query = _connect(box)
    assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"].endswith("?gsc=connected")
    status = box.client.get(f"/api/v1/gsc/projects/{slug}").json()
    assert status["connected"] and status["property"] == "https://www.dirtcrew.example/"  # re-matched for this account
    assert status["state"] == "ready"
    # An account that cannot see the site at all: the stale property is cleared and the owner is asked to pick.
    _post(box, "/api/v1/gsc/disconnect")
    box.google.sites = [{"siteUrl": "https://other.example/", "permissionLevel": "siteOwner"}]
    box.google.refresh_tokens.clear()
    query = _connect(box)
    assert _callback(box, {"code": "good-code", "state": query["state"]}).headers["location"].endswith("?gsc=needs_property")
    assert box.client.get(f"/api/v1/gsc/projects/{slug}").json()["state"] == "needs_property"


def test_turning_on_a_second_site_picks_its_property(tmp_path, env):
    box = _connected_box(tmp_path)
    second = create_project("Other", "other.example", ["a search"], root=tmp_path / "projects")
    _post(box, f"/api/v1/gsc/projects/{second.slug}", {"enabled": False})  # the owner had it off
    on = _post(box, f"/api/v1/gsc/projects/{second.slug}", {"enabled": True}).json()
    assert on["property"] == "https://other.example/" and on["state"] == "ready" and "needs_connect" not in on


def test_reconnecting_another_account_rematches_every_site(tmp_path, env):
    box = _connected_box(tmp_path)
    second = create_project("Other", "other.example", ["a search"], root=tmp_path / "projects")
    _post(box, f"/api/v1/gsc/projects/{second.slug}", {"enabled": True})
    assert gsc.load_settings(second)["property"] == "https://other.example/"
    _post(box, "/api/v1/gsc/disconnect")
    box.google.sites = [{"siteUrl": "https://www.dirtcrew.example/", "permissionLevel": "siteOwner"}]
    query = _connect(box)
    _callback(box, {"code": "good-code", "state": query["state"]})
    assert gsc.load_settings(second)["property"] == ""  # this account cannot read it: Settings asks for a property
    assert gsc.project_state(box.ui, second)["state"] == "needs_property"
