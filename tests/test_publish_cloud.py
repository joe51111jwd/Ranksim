"""Publish on a cloud host: two app instances share one (fake) Blob store, as on Vercel.

The issue status round trip (publish, live, Done in the client report, rollback, back to its earlier status) must
hold when the steps land on different instances, and concurrent requests on two instances must not lose or double
anything. Every platform and page fetch is mocked.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from aeo.ranksim.cloud import CloudState
from aeo.ranksim.serve import create_app
from tests.test_hosted import FakeBlob
from tests.test_publish import Router, Web, box, page  # noqa: F401  (box: the autouse fake secretbox)
from tests.test_serve_project import make_settings

H = {"x-ranksim-csrf": "csrf-token"}
URL = "https://acme.com/about"
HOOK = "https://hooks.acme.test/rs"
ISSUES = [
    {"id": "meta-missing-about", "severity": "high", "kind": "meta", "title": "No meta description"},
    {"id": "h1-weak-about", "severity": "low", "kind": "h1", "title": "Weak heading"},
    {"id": "other-issue", "severity": "low", "kind": "x", "title": "Other issue"},
]
FIXES = [{"id": "meta-missing-about", "title": "No meta description"}, {"id": "h1-weak-about", "title": "Weak heading"}]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from vercel.blob import sync

    from aeo.ranksim.plugins import share

    fake = FakeBlob()
    for name in ("put_blob", "head_blob", "query_blobs", "get_blob", "delete_blob", "BlobError", "BlobNotFoundError"):
        monkeypatch.setattr(sync, name, getattr(fake, name))
    monkeypatch.setattr(share, "_project_report_fn", lambda: (lambda ui, slug: {"issues": ISSUES, "kpis": {}, "rankings": []}))
    web, router = Web(), Router()
    web.pages[URL] = page()
    router.during = []

    def hook(request, m):
        data = json.loads(request.content)
        if data["event"] == "ranksim.publish":
            web.pages[URL] = data["html"]
        elif data["event"] == "ranksim.rollback":
            web.pages[URL] = page()
        for callback in list(router.during):
            callback(data["event"])
        return httpx.Response(200)

    router.on("POST", r"hooks\.acme\.test", hook)
    clock = Clock()

    def instance(name):
        folder = tmp_path / name
        folder.mkdir()
        settings = make_settings(folder, crawl_allow_private=True)
        cloud = CloudState(data_dir=folder, db_file=folder / "rank.db", enabled=True, clock=clock)
        app = create_app(
            settings=settings,
            database=settings.ranksim_database_url,
            projects_root=folder / "projects",
            public_demo=False,
            csrf_token="csrf-token",
            preview_fetch=web,
            cloud=cloud,
        )
        app.state.ui.publish_overrides = {"http_factory": router.client, "sleep": lambda s: None, "verify_waits": (0.0,), "proof_fetch": web}
        return {"client": TestClient(app), "cloud": cloud}

    return {"a": instance("a"), "b": instance("b"), "web": web, "router": router, "clock": clock}


def setup_project(env):
    a, web = env["a"]["client"], env["web"]
    made = a.post("/api/v1/projects", json={"name": "Acme", "site": "acme.com", "queries": ["acme widgets"]}, headers=H)
    assert made.status_code == 200, made.text
    slug = made.json()["slug"]
    record = a.post(f"/api/v1/projects/{slug}/publish/ownership", json={"site": "acme.com"}, headers=H).json()
    web.pages["https://acme.com/"] = page(extra=f'<meta name="ranksim-verification" content="{record["token"]}">')
    assert a.post(f"/api/v1/projects/{slug}/publish/ownership/verify", json={"site": "acme.com"}, headers=H).json()["verified"]
    conn = a.post(
        f"/api/v1/projects/{slug}/publish/connections",
        json={"kind": "webhook", "site": "acme.com", "settings": {"url": HOOK}, "secret": {"signing_secret": "k" * 24}},
        headers=H,
    )
    assert conn.status_code == 200, conn.text
    return slug, conn.json()["id"]


def statuses(report):
    return {issue["id"]: issue["status"] for issue in report["issues"]}


def log_rows(client, slug):
    return [row["summary"] for row in client.get(f"/api/v1/projects/{slug}/changelog").json()["entries"]]


def test_issue_round_trip_across_two_instances(env):
    a, b = env["a"]["client"], env["b"]["client"]
    slug, conn = setup_project(env)
    a.post(f"/api/v1/projects/{slug}/issues/meta-missing-about/plan", json={"status": "planned", "plan": "Write one", "title": "No meta description"}, headers=H)
    entry = a.post(f"/api/v1/projects/{slug}/publish", json={"url": URL, "html": page(h1="New", desc="Better."), "connection_id": conn, "fixes": FIXES}, headers=H).json()
    assert entry["status"] == "live" and entry["fixes_marked"] == "done"

    env["b"]["cloud"].refresh(force=True)
    report = b.get(f"/api/v1/projects/{slug}/client-report").json()
    assert statuses(report)["meta-missing-about"] == "done" and statuses(report)["h1-weak-about"] == "done"

    assert b.post(f"/api/v1/projects/{slug}/publish/history/{entry['id']}/verify", headers=H).status_code == 200
    rolled = b.post(f"/api/v1/projects/{slug}/publish/history/{entry['id']}/rollback", headers=H)
    assert rolled.status_code == 200 and rolled.json()["fixes_marked"] == "open"

    env["a"]["cloud"].refresh(force=True)
    report = a.get(f"/api/v1/projects/{slug}/client-report").json()
    assert statuses(report)["meta-missing-about"] == "planned" and statuses(report)["h1-weak-about"] == "open"
    rows = log_rows(a, slug)
    assert rows.count("Fixed: Weak heading") == 1 and rows.count("Reopened: Weak heading") == 1
    assert sum(1 for row in rows if row.startswith("Published changes to /about:")) == 1
    assert rows.count("Rolled back the published change to /about.") == 1


def test_one_rollback_when_two_instances_are_clicked_at_once(env):
    a, b = env["a"]["client"], env["b"]["client"]
    slug, conn = setup_project(env)
    entry = a.post(f"/api/v1/projects/{slug}/publish", json={"url": URL, "html": page(h1="New"), "connection_id": conn, "fixes": FIXES}, headers=H).json()
    b.get(f"/api/v1/projects/{slug}/publish/history")
    platform_rollbacks, second = [], []

    def during(event):
        if event == "ranksim.rollback":
            platform_rollbacks.append(event)
            if not second:  # while A's platform rollback runs, the owner clicks Roll back on B
                second.append(b.post(f"/api/v1/projects/{slug}/publish/history/{entry['id']}/rollback", headers=H).status_code)

    env["router"].during.append(during)
    assert a.post(f"/api/v1/projects/{slug}/publish/history/{entry['id']}/rollback", headers=H).status_code == 200
    assert second == [409] and len(platform_rollbacks) == 1


def test_issue_edit_on_another_instance_during_a_publish_is_kept(env):
    a, b = env["a"]["client"], env["b"]["client"]
    slug, conn = setup_project(env)
    b.get(f"/api/v1/projects/{slug}/issue-plans")
    edits = []

    def during(event):
        if event == "ranksim.publish" and not edits:
            edits.append(b.post(f"/api/v1/projects/{slug}/issues/other-issue/plan", json={"status": "doing", "plan": "B is on it", "title": "Other issue"}, headers=H).status_code)

    env["router"].during.append(during)
    entry = a.post(f"/api/v1/projects/{slug}/publish", json={"url": URL, "html": page(h1="New", desc="Better."), "connection_id": conn, "fixes": FIXES}, headers=H).json()
    assert edits == [200] and entry["fixes_marked"] == "done"
    for side in ("a", "b"):
        env[side]["cloud"].refresh(force=True)
        plans = env[side]["client"].get(f"/api/v1/projects/{slug}/issue-plans").json()["plans"]
        assert plans["other-issue"]["status"] == "doing" and plans["meta-missing-about"]["status"] == "done"
