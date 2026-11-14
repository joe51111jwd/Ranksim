"""Blob operation budget. Vercel Hobby allows 10,000 simple (head, get) and 2,000 advanced (put, list, copy)
operations a month, and blocks Blob for 30 days past a cap. These tests count every storage call in the scenarios
that matter, with a counting store. Run with -s to see the counts."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE, TODAY, FakeCrawler

from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore
from aeo.ranksim.serve import create_app
from tests.test_hosted import PASSWORD, FakeGoogle
from tests.test_serve_project import make_settings

SIMPLE, ADVANCED = {"head", "get"}, {"put", "list"}


class CountingStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.ops: list[str] = []

    def put(self, name, body, **kwargs):
        self.ops.append("put")
        return super().put(name, body, **kwargs)

    def get(self, name):
        self.ops.append("get")
        return super().get(name)

    def head(self, name):
        self.ops.append("head")
        return super().head(name)

    def list(self, prefix):
        self.ops.append("list")
        return super().list(prefix)

    def delete(self, names):
        self.ops.append("delete")  # free on Vercel Blob
        return super().delete(names)

    def counts(self) -> dict:
        return {
            "simple": sum(op in SIMPLE for op in self.ops),
            "advanced": sum(op in ADVANCED for op in self.ops),
            "lists": self.ops.count("list"),
        }

    def reset(self) -> None:
        self.ops.clear()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def hosted_cloud(tmp_path):
    from aeo.ranksim.access import Access

    store, clock = CountingStore(), Clock()
    cloud = CloudState(data_dir=tmp_path, db_file=tmp_path / "rank.db", enabled=True, store=store, clock=clock)
    settings = make_settings(tmp_path, serpapi_key="sk-budget-test")
    settings.ranksim_database_url = f"sqlite:///{tmp_path / 'rank.db'}"
    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret="budget-secret",
        cloud=cloud,
        pull_options={"serp_provider": FakeGoogle(), "fetcher": FakeCrawler(), "today": TODAY, "workers": 2},
    )
    app.state.ui.access = Access(PASSWORD, "budget-secret", shared=cloud)
    cloud.refresh(force=True)  # the one-time bootstrap of a brand-new store (a few lists, once per store, ever)
    store.reset()
    client = TestClient(app)
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)
    assert client.post("/api/v1/login", json={"password": PASSWORD}, headers={"x-ranksim-csrf": token}).status_code == 200
    return {"client": client, "token": token, "store": store, "clock": clock, "cloud": cloud, "app": app}


def _post(h, path, body):
    return h["client"].post(path, json=body, headers={"x-ranksim-csrf": h["token"]})


def _project(h) -> str:
    made = _post(h, "/api/v1/projects", {"name": "Dirt Crew", "site": SITE, "queries": SEARCHES})
    assert made.status_code == 200, made.text
    return made.json()["slug"]


def _report(name: str, counts: dict) -> None:
    print(f"\nBLOB BUDGET {name}: simple={counts['simple']} advanced={counts['advanced']} (lists={counts['lists']})")


def test_ordinary_reads_make_no_blob_call(hosted_cloud):
    h = hosted_cloud
    slug = _project(h)
    lines = [json.loads(line) for line in _post(h, f"/api/v1/projects/{slug}/pull", {}).text.splitlines() if line.strip()]
    assert lines[-1]["type"] == "done", lines[-2:]
    h["store"].reset()
    for _ in range(20):
        for path in (f"/api/v1/projects/{slug}/report", f"/api/v1/projects/{slug}", f"/api/v1/projects/{slug}/setup", "/api/v1/projects", "/api/v1/session"):
            assert h["client"].get(path).status_code == 200, path
    assert h["store"].ops == []  # no list, no head, nothing


def test_report_and_overview_never_list(hosted_cloud):
    h = hosted_cloud
    slug = _project(h)
    for _ in range(3):
        h["clock"].now += 400  # past the refresh interval: the check is one head, never a list
        h["store"].reset()
        assert h["client"].get(f"/api/v1/projects/{slug}/report").status_code == 200
        assert h["client"].get(f"/api/v1/projects/{slug}").status_code == 200
        assert "list" not in h["store"].ops and h["store"].ops.count("head") <= 1


def test_one_hour_of_an_owner_clicking_around(hosted_cloud):
    h = hosted_cloud
    slug = _project(h)
    h["store"].reset()
    for minute in range(60):
        h["clock"].now += 60
        for path in (f"/api/v1/projects/{slug}/report", f"/api/v1/projects/{slug}"):
            h["client"].get(path)
        if minute % 6 == 0:  # a settings change every six minutes: ten writes, each one a real change
            competitors = [f"rival{minute}.example"]
            assert _post(h, f"/api/v1/projects/{slug}/settings", {"competitors": competitors}).status_code == 200
    counts = h["store"].counts()
    _report("one hour of clicking (120 reads, 10 writes)", counts)
    assert counts["lists"] == 0
    assert counts["advanced"] <= 2 * 10 + 2 and counts["simple"] <= 2 * 10 + 15  # two of each per write, plus a head every 5 min


def test_one_analysis_and_one_pull(hosted_cloud):
    h = hosted_cloud
    h["store"].reset()
    lines = [json.loads(line) for line in _post(h, "/api/v1/sites", {"site": SITE}).text.splitlines() if line.strip()]
    assert lines[-1]["type"] == "done", lines[-2:]
    assert lines[-1]["sync"].isdigit()  # the browser keeps it, so the next request anywhere sees this save
    analysis = h["store"].counts()
    _report("one analysis", analysis)
    h["store"].reset()
    lines = [json.loads(line) for line in _post(h, f"/api/v1/projects/{lines[-1]['slug']}/pull", {}).text.splitlines() if line.strip()]
    assert lines[-1]["type"] == "done"
    pull = h["store"].counts()
    _report("one pull", pull)
    assert analysis["advanced"] <= 15 and pull["advanced"] <= 12
    assert analysis["lists"] <= 2 and pull["lists"] <= 2


def test_a_cron_style_recheck(hosted_cloud):
    """A scheduled re-check is a GET that writes plugin data and calls plugins.save(ui)."""
    from aeo.ranksim import plugins

    h = hosted_cloud
    slug = _project(h)
    ui = h["app"].state.ui
    h["store"].reset()
    h["clock"].now += 3600  # the instance may be cold or stale: the save checks the manifest first
    folder = plugins.data_dir(ui.project(slug), "tracking")
    (folder / "history.json").write_text('{"checked": 1}')
    assert plugins.save(ui) is True
    counts = h["store"].counts()
    _report("one cron re-check (save only)", counts)
    assert counts["lists"] == 0 and counts["advanced"] <= 2 and counts["simple"] <= 3


def test_idle_instances_use_nothing(hosted_cloud):
    h = hosted_cloud
    _project(h)
    h["store"].reset()
    h["clock"].now += 24 * 3600  # a day with no requests: nothing runs in the background
    assert h["store"].ops == []


def test_wrong_passwords_cost_almost_nothing(hosted_cloud, monkeypatch):
    import aeo.ranksim.access as access_module

    monkeypatch.setattr(access_module, "FAILURE_DELAY_SECONDS", 0)
    monkeypatch.setattr("aeo.ranksim.access.time.sleep", lambda _s: None)
    h = hosted_cloud
    h["store"].reset()
    for _ in range(200):
        _post(h, "/api/v1/login", {"password": "wrong"})
    counts = h["store"].counts()
    _report("200 wrong passwords", counts)
    assert counts["advanced"] <= 2 and counts["lists"] == 0  # one published lockout at most


def test_a_stale_instance_catches_up_from_the_cookie_hint(tmp_path):
    """Two instances behind one browser: a save on A is seen by B on the next request, through rs_sync."""
    store = CountingStore()
    a = CloudState(data_dir=tmp_path / "a", db_file=tmp_path / "a" / "rank.db", enabled=True, store=store, clock=Clock())
    b = CloudState(data_dir=tmp_path / "b", db_file=tmp_path / "b" / "rank.db", enabled=True, store=store, clock=Clock())
    (tmp_path / "a" / "projects" / "demo").mkdir(parents=True)
    (tmp_path / "a" / "projects" / "demo" / "project.json").write_text("{}")
    b.refresh()  # cold start
    a.save()
    store.reset()
    assert b.refresh() is False and store.ops == []  # no hint, not due: no call
    assert b.refresh(hint=a.stamp) is True  # the browser saw a's save: b catches up at once
    assert (tmp_path / "b" / "projects" / "demo" / "project.json").is_file()
    assert store.ops.count("list") == 0


def test_anonymous_traffic_never_starts_a_cold_sync(hosted_cloud, tmp_path):
    """QA finding: a cold instance must not run its cold-start sync for anonymous requests to login-exempt routes."""
    from aeo.ranksim.cloud import CloudState

    h = hosted_cloud
    _project(h)
    cold_dir = tmp_path / "cold"
    cold = CloudState(data_dir=cold_dir, db_file=cold_dir / "rank.db", enabled=True, store=h["store"], clock=Clock())
    settings = make_settings(cold_dir, serpapi_key="sk-budget-test")
    app = create_app(settings=settings, projects_root=cold_dir / "projects", public_demo=False, access_password=PASSWORD,
                     access_secret="budget-secret", cloud=cold)
    anon = TestClient(app)
    h["store"].reset()
    for path in ("/r/nonsense", "/r/x/y/z", "/r/assets/share.js", "/api/v1/cron/recheck", "/r/" + "a" * 24 + ".short",
                 "/api/v1/projects", "/app/anything", "/healthz"):
        anon.get(path)
        anon.post(path, json={})
    assert h["store"].ops == []  # nothing: no list, no head, no get
    assert cold.stamp == 0  # still cold
    anon.get("/r/" + "a" * 24 + "." + "b" * 22)  # a well-formed (unknown) token: the routine cold start, once
    first = list(h["store"].ops)
    assert first and "list" not in first and "put" not in first
    h["store"].reset()
    for _ in range(20):
        anon.get("/r/" + "a" * 24 + "." + "b" * 22 + "/data")
    assert h["store"].ops == []  # warm now, and anonymous requests never force a check
