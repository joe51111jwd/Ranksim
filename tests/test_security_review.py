"""Regression tests for the security review (2026-11): storage calls from signed-out requests, share link writes,
security headers on the login middleware's answers, and cross-site writes. No network, no provider keys."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE

from aeo.ranksim.access import Access
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.plugins import share
from aeo.ranksim.serve import create_app
from tests.test_budget import Clock, CountingStore
from tests.test_hosted import PASSWORD
from tests.test_serve_project import make_settings

SECRET = "security-review-secret-0123456789abcdef"


def _csrf(client: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


@pytest.fixture()
def hosted(tmp_path):
    """A private app on a counting cloud store, with one signed-in owner client and one signed-out client."""
    store, clock = CountingStore(), Clock()
    cloud = CloudState(data_dir=tmp_path, db_file=tmp_path / "rank.db", enabled=True, store=store, clock=clock)
    settings = make_settings(tmp_path)
    settings.ranksim_database_url = f"sqlite:///{tmp_path / 'rank.db'}"
    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret=SECRET,
        cloud=cloud,
    )
    app.state.ui.access = Access(PASSWORD, SECRET, shared=cloud)
    cloud.refresh(force=True)
    owner = TestClient(app)
    token = _csrf(owner)
    assert owner.post("/api/v1/login", json={"password": PASSWORD}, headers={"x-ranksim-csrf": token}).status_code == 200
    store.reset()
    return {"app": app, "owner": owner, "token": token, "store": store, "clock": clock, "cloud": cloud, "stranger": TestClient(app)}


def _owner_post(h, path, body):
    return h["owner"].post(path, json=body, headers={"x-ranksim-csrf": h["token"]})


def test_signed_out_requests_cannot_force_or_hint_a_storage_check(hosted):
    """Before the fix, a signed-out POST to /app/<anything> forced one Blob head per request, and a forged rs_sync
    cookie forced one on any /app/ or /r/ path every few seconds. Vercel Hobby blocks Blob for 30 days past its cap."""
    h = hosted
    stranger, store = h["stranger"], h["store"]
    stranger.cookies.set("rs_sync", "99999999999999999")
    for _ in range(25):
        h["clock"].now += 6  # past the per-instance hint interval every time
        stranger.post("/app/x")
        stranger.put("/app/x/y")
        stranger.delete("/app/x")
        stranger.get("/app/x")
        stranger.get("/r/" + "a" * 24 + "." + "b" * 22)
        stranger.get("/r/whatever/data")
    assert "put" not in store.ops and "list" not in store.ops
    assert len(store.ops) <= 1, store.ops  # at most the routine check that any instance makes


def test_signed_in_writes_still_check_storage_first(hosted):
    h = hosted
    made = _owner_post(h, "/api/v1/projects", {"name": "Dirt Crew", "site": SITE, "queries": SEARCHES})
    assert made.status_code == 200, made.text
    ops = h["store"].ops
    assert "put" in ops and ops.index("put") > 0, ops  # the write checked storage first, then saved


def test_a_public_share_view_never_writes_storage_or_links_json(hosted):
    h = hosted
    made = _owner_post(h, "/api/v1/projects", {"name": "Dirt Crew", "site": SITE, "queries": SEARCHES})
    slug = made.json()["slug"]
    link = _owner_post(h, f"/api/v1/projects/{slug}/share", {"label": "client"}).json()
    ui = h["app"].state.ui
    links_file = share._links_path(ui.project(slug))
    before = links_file.read_bytes()
    h["store"].reset()
    for _ in range(5):
        answer = h["stranger"].get(f"/r/{link['token']}/data")
        assert answer.status_code in (200, 503), answer.text
    assert "put" not in h["store"].ops and "list" not in h["store"].ops, h["store"].ops
    # The view is remembered, but in views.json: links.json (revocations) is never rewritten by a signed-out request,
    # so the storage merge (a file both instances changed keeps the local copy) cannot drop a revocation.
    assert links_file.read_bytes() == before
    listed = h["owner"].get(f"/api/v1/projects/{slug}/share").json()["links"]
    assert listed[0]["last_used"]


def test_touch_link_leaves_links_json_alone(tmp_path):
    from types import SimpleNamespace

    project = SimpleNamespace(dir=tmp_path / "acme", slug="acme")
    link = share.create_link(tmp_path, project, label="x")
    before = share._links_path(project).read_bytes()
    assert share.touch_link(project, link["id"]) is True
    assert share.touch_link(project, link["id"]) is False  # at most every ten minutes
    assert share._links_path(project).read_bytes() == before
    assert share.list_links(tmp_path, project)[0]["last_used"]
    assert share.revoke_link(project, link["id"]) is True
    assert share.touch_link(project, "z" * 24) is False  # an unknown id writes nothing


def test_login_middleware_answers_carry_the_security_headers(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "p", public_demo=False, access_password=PASSWORD, access_secret=SECRET)
    client = TestClient(app, base_url="https://testserver")
    for path, status in (("/p/anything", 303), ("/api/v1/projects", 401)):
        answer = client.get(path, follow_redirects=False)
        assert answer.status_code == status
        assert "frame-ancestors 'none'" in answer.headers.get("content-security-policy", "")
        assert answer.headers.get("x-frame-options") == "DENY"
        assert answer.headers.get("cache-control") == "no-store"
        assert answer.headers.get("strict-transport-security")


def test_the_body_cap_applies_before_the_login(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "p", public_demo=False, access_password=PASSWORD, access_secret=SECRET, max_upload_bytes=1024)
    client = TestClient(app)
    answer = client.post("/api/v1/projects", content=b"x" * 200_000, headers={"content-type": "application/json"})
    assert answer.status_code == 413
    assert client.request("DELETE", "/api/v1/projects", content=b"x" * 200_000).status_code == 413


def test_cross_site_writes_are_refused(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "p", public_demo=False, access_password=PASSWORD, access_secret=SECRET)
    client = TestClient(app)
    token = _csrf(client)
    headers = {"x-ranksim-csrf": token}
    refused = client.post("/api/v1/login", json={"password": PASSWORD}, headers={**headers, "sec-fetch-site": "cross-site"})
    assert refused.status_code == 403 and "rs_session" not in refused.cookies
    form = client.post("/p/demo/searches", data={"csrf": token, "searches": "a"}, headers={"sec-fetch-site": "cross-site"}, follow_redirects=False)
    assert form.status_code == 403
    for site in ("same-origin", "none", None):
        extra = {"sec-fetch-site": site} if site else {}
        assert client.post("/api/v1/login", json={"password": PASSWORD}, headers={**headers, **extra}).status_code == 200
    assert client.get("/api/v1/session", headers={"sec-fetch-site": "cross-site"}).status_code == 200  # reads are not refused


def test_hosting_secrets_are_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_fake_value_123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX-fake-client-secret")
    monkeypatch.setenv("RANKSIM_SECRET", SECRET)
    settings = make_settings(tmp_path)
    ui = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "p", public_demo=False).state.ui
    text = ui.scrub(f"failed with vercel_blob_rw_fake_value_123 and GOCSPX-fake-client-secret and {SECRET}")
    assert "fake" not in text and SECRET not in text and text.count("[redacted]") == 3
