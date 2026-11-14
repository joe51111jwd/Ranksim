"""Saved editor draft versions (plugins/drafts.py): the store and its routes. No network."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aeo.ranksim import workspace as ws
from aeo.ranksim.plugins import drafts
from aeo.ranksim.serve import create_app
from tests.test_workspace import make_settings

URL = "https://acme.test/pricing/"
SOURCE = "<!doctype html><html><head><script>track()</script></head><body><h1>Pricing</h1></body></html>"
BASE = '<!doctype html>\n<html data-rs-id="1"><head data-rs-id="2"></head><body data-rs-id="4"><h1 data-rs-id="5">Pricing</h1></body></html>'
DRAFT = BASE.replace(">Pricing<", ">Simple pricing<")


class FakeProject:
    def __init__(self, folder: Path):
        self.dir = folder


@pytest.fixture()
def project(tmp_path):
    return FakeProject(tmp_path / "proj")


def _save(project, name="First", draft=DRAFT, now=None):
    source = drafts.put_blob(project, SOURCE)
    base = drafts.put_blob(project, BASE)
    return drafts.save_version(project, URL, name, source, base, draft, now=now)


def test_save_list_load_delete_round_trip(project):
    saved = _save(project)
    assert re.match(r"^[0-9a-f]{12}-[0-9a-f]{10}$", saved["id"])
    assert saved["name"] == "First" and saved["url"] == URL and saved["size"] == len(DRAFT)
    listed = drafts.list_versions(project, URL)
    assert [item["id"] for item in listed] == [saved["id"]]
    assert "draft" not in listed[0], "the list leaves bodies out"
    loaded = drafts.load_version(project, saved["id"])
    assert loaded["draft"] == DRAFT and loaded["source"] == SOURCE and loaded["base"] == BASE
    folder = project.dir / "plugins" / "drafts"
    assert len(list((folder / "blobs").glob("*.html"))) == 2
    drafts.delete_version(project, saved["id"])
    assert drafts.list_versions(project, URL) == []
    assert list((folder / "blobs").glob("*.html")) == [], "unused page copies go with the last version"


def test_blobs_are_stored_once(project):
    first = drafts.put_blob(project, SOURCE)
    again = drafts.put_blob(project, SOURCE)
    assert first == again == drafts.sha_of(SOURCE)
    _save(project, "a")
    _save(project, "b")
    assert len(list((project.dir / "plugins" / "drafts" / "blobs").glob("*.html"))) == 2


def test_versions_are_newest_first_and_capped(project):
    start = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    for k in range(drafts.MAX_VERSIONS + 3):
        _save(project, f"v{k}", now=start + timedelta(minutes=k))
    listed = drafts.list_versions(project, URL)
    assert len(listed) == drafts.MAX_VERSIONS
    assert listed[0]["name"] == f"v{drafts.MAX_VERSIONS + 2}"
    assert listed[-1]["name"] == "v3", "the oldest were dropped"


def test_names_are_trimmed_and_defaulted(project):
    named = _save(project, "  Client   review\n draft " + "x" * 200)
    assert named["name"].startswith("Client review draft") and len(named["name"]) == drafts.MAX_NAME
    unnamed = _save(project, "", now=datetime(2026, 11, 14, 9, 5, tzinfo=timezone.utc))
    assert unnamed["name"] == "Version Nov 14 09:05"


def test_pages_are_kept_apart(project):
    _save(project)
    other = "https://acme.test/about/"
    assert drafts.list_versions(project, other) == []


def test_refusals(project):
    with pytest.raises(drafts.DraftError) as missing:
        drafts.save_version(project, URL, "x", "0" * 64, "1" * 64, DRAFT)
    assert missing.value.status == 409 and missing.value.extra == {"missing": ["source", "base"]}
    for bad in ("ftp://acme.test/", "acme.test", "https://acme.test/a b", "https://" + "a" * 3000):
        with pytest.raises(drafts.DraftError):
            drafts.list_versions(project, bad)
    with pytest.raises(drafts.DraftError) as big:
        drafts.put_blob(project, "x" * (drafts.MAX_BLOB + 1))
    assert big.value.status == 413
    source = drafts.put_blob(project, SOURCE)
    with pytest.raises(drafts.DraftError) as huge:
        drafts.save_version(project, URL, "x", source, source, "x" * (drafts.MAX_DRAFT + 1))
    assert huge.value.status == 413
    with pytest.raises(drafts.DraftError):
        drafts.save_version(project, URL, "x", source, source, "")
    for bad_id in ("../../etc/passwd", "abc", "0" * 12 + "-" + "z" * 10):
        with pytest.raises(drafts.DraftError) as nope:
            drafts.load_version(project, bad_id)
        assert nope.value.status == 404
    assert drafts.get_blob(project, "../x") is None


# -- routes -------------------------------------------------------------------------------------------------


def _csrf(client: TestClient) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text)
    assert found
    return found.group(1)


@pytest.fixture()
def app_client(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False)
    if not any(getattr(route, "path", "") == "/api/v1/projects/{slug}/drafts" for route in app.routes):
        drafts.register(app, app.state.ui)  # before the plugin loader exists
    client = TestClient(app)
    token = _csrf(client)
    assert client.post("/api/v1/demo", json={}, headers={"x-ranksim-csrf": token}).status_code == 200
    return client, token


def test_routes_save_list_load_delete(app_client):
    client, token = app_client
    base = f"/api/v1/projects/{ws.DEMO_SLUG}/drafts"
    headers = {"x-ranksim-csrf": token}
    body = {"url": URL, "name": "Client review", "source": drafts.sha_of(SOURCE), "base": drafts.sha_of(BASE), "draft": DRAFT}
    first = client.post(base, json=body, headers=headers)
    assert first.status_code == 409 and sorted(first.json()["missing"]) == ["base", "source"]
    for text in (SOURCE, BASE):
        up = client.post(f"{base}/blobs", json={"text": text}, headers=headers)
        assert up.status_code == 200 and up.json()["sha"] == drafts.sha_of(text)
    saved = client.post(base, json=body, headers=headers)
    assert saved.status_code == 200, saved.text
    version_id = saved.json()["id"]
    listed = client.get(base, params={"url": URL}).json()
    assert [item["id"] for item in listed["versions"]] == [version_id]
    loaded = client.get(f"{base}/{version_id}").json()
    assert loaded["draft"] == DRAFT and loaded["source"] == SOURCE
    assert client.delete(f"{base}/{version_id}", headers=headers).json() == {"ok": True}
    assert client.get(f"{base}/{version_id}").status_code == 404
    assert client.get(base, params={"url": "javascript:alert(1)"}).status_code == 400


def test_routes_need_the_csrf_header_and_a_project(app_client):
    client, token = app_client
    base = f"/api/v1/projects/{ws.DEMO_SLUG}/drafts"
    assert client.post(f"{base}/blobs", json={"text": SOURCE}).status_code == 403
    assert client.post(base, json={"url": URL}).status_code == 403
    assert client.delete(f"{base}/0123456789ab-0123456789").status_code == 403
    assert client.get("/api/v1/projects/nope/drafts", params={"url": URL}).status_code == 404
    assert client.post("/api/v1/projects/nope/drafts/blobs", json={"text": "x"}, headers={"x-ranksim-csrf": token}).status_code == 404


def test_routes_refused_in_the_public_demo(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=True)
    if not any(getattr(route, "path", "") == "/api/v1/projects/{slug}/drafts" for route in app.routes):
        drafts.register(app, app.state.ui)
    client = TestClient(app)
    assert client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/drafts", params={"url": URL}).status_code == 403
