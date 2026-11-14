"""The on-disk report cache: served without a build when nothing changed, rebuilt when anything it reads changed,
and precomputed by every pull so a GET on another instance does not pay the build. No network."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE, TODAY, FakeCrawler

from aeo.ranksim import reportcache
from aeo.ranksim.serve import create_app
from tests.test_hosted import FakeGoogle
from tests.test_serve_project import make_settings


def _app(tmp_path):
    settings = make_settings(tmp_path, serpapi_key="sk-cache-test")
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=False,
                     pull_options={"serp_provider": FakeGoogle(), "fetcher": FakeCrawler(), "today": TODAY, "workers": 2})
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)
    return app, client, token


@pytest.fixture()
def pulled(tmp_path):
    app, client, token = _app(tmp_path)
    slug = client.post("/api/v1/projects", json={"name": "Dirt Crew", "site": SITE, "queries": SEARCHES}, headers={"x-ranksim-csrf": token}).json()["slug"]
    lines = [json.loads(line) for line in client.post(f"/api/v1/projects/{slug}/pull", json={}, headers={"x-ranksim-csrf": token}).text.splitlines() if line.strip()]
    assert lines[-1]["type"] == "done", lines[-2:]
    return {"tmp": tmp_path, "app": app, "client": client, "token": token, "slug": slug}


def test_a_pull_precomputes_the_report_and_a_new_instance_serves_it_without_building(pulled, monkeypatch):
    ui = pulled["app"].state.ui
    project = ui.project(pulled["slug"])
    key = reportcache.signature(ui, project, google_ready=True)
    assert reportcache.load(project, key) is not None  # written by the pull, before its save
    first = pulled["client"].get(f"/api/v1/projects/{pulled['slug']}/report").json()

    import aeo.ranksim.report as report_module

    def no_build(*_args, **_kwargs):
        raise AssertionError("the report was built although nothing changed")

    monkeypatch.setattr(report_module, "build_report", no_build)
    app2, client2, _token = _app(pulled["tmp"])  # another instance on the same files
    assert client2.get(f"/api/v1/projects/{pulled['slug']}/report").json() == first


def test_the_key_follows_project_files_the_database_and_plugin_data(pulled):
    ui = pulled["app"].state.ui
    project = ui.project(pulled["slug"])
    key = reportcache.signature(ui, project, google_ready=True)
    assert reportcache.signature(ui, project, google_ready=True) == key  # stable
    assert reportcache.signature(ui, project, google_ready=False) != key
    (project.dir / "plugins" / "reportcache" / "noise.json").write_text("{}")
    (project.dir / "plugins" / "pagefacts").mkdir(parents=True, exist_ok=True)
    (project.dir / "plugins" / "pagefacts" / "x.json").write_text("{}")
    assert reportcache.signature(ui, project, google_ready=True) == key  # its own caches do not count
    settings = pulled["client"].post(f"/api/v1/projects/{pulled['slug']}/settings", json={"competitors": ["new-rival.example"]}, headers={"x-ranksim-csrf": pulled["token"]})
    assert settings.status_code == 200
    after_settings = reportcache.signature(ui, project, google_ready=True)
    assert after_settings != key
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    session = open_session(ui.database)
    store_fetched_page(session, FetchResult(url=f"https://{SITE}/brand-new/", html="<p>new</p>", status="fetched"))
    session.commit()
    session.close()
    after_db = reportcache.signature(ui, project, google_ready=True)
    assert after_db != after_settings
    workspace = ui.root / "_plugins" / "gsc"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "data.json").write_text("{}")
    assert reportcache.signature(ui, project, google_ready=True) != after_db


def test_a_change_rebuilds_the_report(pulled):
    client, slug = pulled["client"], pulled["slug"]
    before = client.get(f"/api/v1/projects/{slug}/report").json()
    client.post(f"/api/v1/projects/{slug}/settings", json={"competitors": ["new-rival.example"]}, headers={"x-ranksim-csrf": pulled["token"]})
    after = client.get(f"/api/v1/projects/{slug}/report").json()
    assert json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True) or "new-rival.example" in json.dumps(after)
