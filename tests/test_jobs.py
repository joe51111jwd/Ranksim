"""Jobs: ids, events, per-key locks across instances, pause and resume, reattach. No network, no keys."""

from __future__ import annotations

import threading
import time

import pytest

from aeo.ranksim.jobs import JobPaused, JobRegistry, valid_job_id
from aeo.ranksim.objects import DirStore, MemoryStore

SECRET = "sk-live-JOBS-0123456789"


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _wait(reg, job_id, timeout=5.0):
    assert reg.join(job_id, timeout), "the job did not finish"


def _scrub(text: str) -> str:
    return text.replace(SECRET, "[key]")


def test_a_job_runs_emits_and_finishes():
    reg = JobRegistry(MemoryStore(), scrub=_scrub)

    def work(ctx):
        for n in range(3):
            ctx.emit({"type": "step", "n": n, "message": f"step {n} with {SECRET}"})
        return {"answer": 42}

    record = reg.start("analyze", "site:a.example", "Analysis", work)
    assert valid_job_id(record["id"]) and record["status"] in {"running", "done"}
    _wait(reg, record["id"])
    view = reg.events_since(record["id"], 0)
    assert view["status"] == "done" and view["result"] == {"answer": 42}
    assert [e["n"] for e in view["events"]] == [0, 1, 2] and view["cursor"] == 3
    assert reg.events_since(record["id"], 2)["events"][0]["n"] == 2
    assert SECRET not in str(view)


def test_errors_are_scrubbed_and_the_lock_is_released():
    store = MemoryStore()
    reg = JobRegistry(store, scrub=_scrub)

    def boom(ctx):
        raise RuntimeError(f"provider said no to {SECRET}")

    record = reg.start("pull", "site:a.example", "Pull", boom)
    _wait(reg, record["id"])
    assert reg.get(record["id"])["status"] == "error" and SECRET not in reg.get(record["id"])["error"]
    assert not any(name.startswith("ranksim/v2/locks/") for name in store.items)
    again = reg.start("pull", "site:a.example", "Pull", lambda ctx: {"ok": True})
    assert again["id"] != record["id"] and not again.get("attached")
    assert all(SECRET.encode() not in body for body in store.items.values())


def test_a_second_instance_attaches_instead_of_starting_twice_and_other_keys_run():
    store = MemoryStore()
    a, b = JobRegistry(store), JobRegistry(store)
    gate = threading.Event()
    first = a.start("analyze", "site:a.example", "A", lambda ctx: gate.wait(5) and {"slug": "a"})
    attached = b.start("analyze", "site:a.example", "A again", lambda ctx: pytest.fail("must not run twice"))
    assert attached["attached"] is True and attached["id"] == first["id"] and attached["status"] == "running"
    seen = b.get(first["id"])  # another instance reads the job from its lock
    assert seen["status"] == "running" and not seen["stalled"]
    other = b.start("analyze", "site:b.example", "B", lambda ctx: {"slug": "b"})
    assert not other.get("attached")
    _wait(b, other["id"])
    gate.set()
    _wait(a, first["id"])
    assert b.get(first["id"])["status"] == "done" and b.get(first["id"])["result"] == {"slug": "a"}


def test_a_job_whose_instance_died_is_resumed_elsewhere():
    store, clock = MemoryStore(), Clock()
    a = JobRegistry(store, clock=clock, budget_seconds=240)
    b = JobRegistry(store, clock=clock, budget_seconds=240)
    hang = threading.Event()
    record = a.start("analyze", "site:a.example", "A", lambda ctx: hang.wait(10) or {})
    clock.now += 240 + 61  # past the deadline in the lock: its instance is gone
    view = b.get(record["id"])
    assert view["stalled"] and view["resumable"]
    resumed = b.resume(record["id"], lambda ctx: {"attempt": ctx.attempt})
    assert not resumed.get("attached")
    _wait(b, record["id"])
    assert b.get(record["id"])["result"] == {"attempt": 2}
    hang.set()
    _wait(a, record["id"])
    # The old runner woke up late: it must not overwrite the new runner's result.
    assert b.get(record["id"])["result"] == {"attempt": 2}


def test_pause_keeps_the_checkpoint_and_resume_continues():
    store, clock = MemoryStore(), Clock()
    a = JobRegistry(store, clock=clock, budget_seconds=30, safety_seconds=25)
    b = JobRegistry(store, clock=clock, budget_seconds=30, safety_seconds=25)
    done_urls: list[str] = []

    def crawl(ctx):
        todo = [f"https://a.example/{n}" for n in range(10)]
        for url in todo:
            if url in ctx.checkpoint.get("done", []):
                continue
            if ctx.should_pause():
                raise JobPaused()
            done_urls.append(url)
            ctx.checkpoint.setdefault("done", []).append(url)
            clock.now += 1
        return {"fetched": len(ctx.checkpoint["done"])}

    record = a.start("pull", "site:a.example", "Pull", crawl)
    _wait(a, record["id"])
    assert a.get(record["id"])["status"] == "paused" and 0 < len(done_urls) < 10
    attached = b.start("pull", "site:a.example", "Pull", crawl)  # posting the site again finds the paused job
    assert attached["attached"] and attached["status"] == "paused" and attached["resumable"]
    b.resume(record["id"], crawl)
    _wait(b, record["id"])
    assert b.get(record["id"])["status"] == "done" and b.get(record["id"])["result"] == {"fetched": 10}
    assert len(done_urls) == len(set(done_urls)) == 10  # nothing fetched twice


def test_an_abandoned_pause_stops_blocking_the_key():
    store, clock = MemoryStore(), Clock()
    reg = JobRegistry(store, clock=clock, budget_seconds=30, safety_seconds=40)

    def pause(ctx):
        raise JobPaused()

    record = reg.start("pull", "site:a.example", "Pull", pause)
    _wait(reg, record["id"])
    clock.now += 601
    fresh = JobRegistry(store, clock=clock).start("pull", "site:a.example", "Pull", lambda ctx: {})
    assert not fresh.get("attached") and fresh["id"] != record["id"]


def test_on_finish_carries_the_record_and_recall_finds_it():
    store = MemoryStore()
    kept: dict = {}

    def on_finish(record):
        kept[record["id"]] = record
        return True

    a = JobRegistry(store, on_finish=on_finish, recall=lambda job_id, fresh: kept.get(job_id))
    record = a.start("analyze", "site:a.example", "A", lambda ctx: {"slug": "a"})
    _wait(a, record["id"])
    assert not any(name.startswith("ranksim/v2/jobs/") for name in store.items)  # no separate record object
    b = JobRegistry(store, recall=lambda job_id, fresh: kept.get(job_id))
    assert b.get(record["id"])["result"] == {"slug": "a"}


def test_job_ids_are_checked():
    reg = JobRegistry(MemoryStore())
    for bad in ("../../etc/passwd", "short", "a" * 65, "has space in it here", None, 7):
        assert reg.get(bad) is None and reg.resume(bad, lambda ctx: {}) is None
    assert reg.get("a" * 24) is None


def test_follow_streams_events_live():
    reg = JobRegistry(MemoryStore())
    gate = threading.Event()

    def work(ctx):
        ctx.emit({"type": "step", "n": 1})
        gate.wait(5)
        ctx.emit({"type": "step", "n": 2})
        return {}

    record = reg.start("analyze", "site:a.example", "A", work)
    stream = reg.follow(record["id"])
    assert next(stream)["n"] == 1
    gate.set()
    rest = list(stream)
    assert rest[0]["n"] == 2 and rest[-1]["type"] == "end" and rest[-1]["job"]["status"] == "done"


def test_budget_from_the_environment(monkeypatch):
    monkeypatch.setenv("RANKSIM_JOB_BUDGET_SECONDS", "90")
    assert JobRegistry(MemoryStore()).budget_seconds == 90.0


def test_dir_store_backend(tmp_path):
    a, b = JobRegistry(DirStore(tmp_path)), JobRegistry(DirStore(tmp_path))
    gate = threading.Event()
    record = a.start("analyze", "site:a.example", "A", lambda ctx: gate.wait(5) and {"ok": 1})
    assert b.start("analyze", "site:a.example", "A", lambda ctx: {})["attached"]
    gate.set()
    _wait(a, record["id"])
    assert b.get(record["id"])["result"] == {"ok": 1}


def test_many_threads_on_one_key_start_exactly_one_job():
    store = MemoryStore()
    regs = [JobRegistry(store) for _ in range(4)]
    gate = threading.Event()
    ids, ran = [], []

    def work(ctx):
        ran.append(ctx.job_id)
        gate.wait(5)
        return {}

    def go(reg):
        ids.append(reg.start("analyze", "site:a.example", "A", work)["id"])

    threads = [threading.Thread(target=go, args=(regs[n % 4],)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    time.sleep(0.05)
    gate.set()
    assert len(set(ids)) == 1 and len(ran) == 1


# -- the routes: analyses and pulls as jobs ------------------------------------------------------

import json as _json  # noqa: E402
import re as _re  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


def _route_app(tmp_path, crawler, google, *, registry=None):
    from aeo.ranksim.serve import create_app
    from tests.test_project import TODAY
    from tests.test_serve_project import make_settings

    settings = make_settings(tmp_path, serpapi_key="sk-test-jobs")
    app = create_app(
        settings=settings,
        projects_root=tmp_path / "projects",
        public_demo=False,
        pull_options={"serp_provider": google, "fetcher": crawler, "today": TODAY, "workers": 2},
    )
    if registry is not None:
        app.state.ui.job_registry = registry
    client = TestClient(app)
    token = _re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)
    return app, client, token


def _lines(response):
    return [_json.loads(line) for line in response.text.splitlines() if line.strip()]


class CountingGoogle:
    def __init__(self):
        from tests.test_hosted import FakeGoogle

        self.inner = FakeGoogle()
        self.name, self.is_google, self.synthetic = "fakegoogle", True, False
        self.asked = []

    def search(self, query, count=10):
        self.asked.append(query)
        return self.inner.search(query, count)


def test_a_long_analysis_pauses_saves_and_resumes_without_repeating_work(tmp_path):
    from test_autopilot import _site_pages

    from tests.test_project import SITE, FakeCrawler

    now = [1000.0]
    crawler = FakeCrawler(_site_pages())

    def slow_fetch(urls):
        now[0] += 1.0  # each page "takes" one second of the job's budget
        return crawler(urls)

    google = CountingGoogle()
    reg = JobRegistry(MemoryStore(), clock=lambda: now[0], budget_seconds=31, safety_seconds=25)
    _app, client, token = _route_app(tmp_path, slow_fetch, google, registry=reg)
    first = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token}))
    assert first[0]["type"] == "start" and first[0]["job"]
    assert first[-1]["type"] == "paused", first[-3:]
    job = first[0]["job"]
    asked_before = list(crawler.asked)
    googled_before = list(google.asked)
    assert googled_before  # Google was asked in the first attempt

    polled = client.get(f"/api/v1/jobs/{job}", params={"cursor": 0}).json()
    assert polled["status"] == "paused" and polled["resumable"] and polled["events"]
    assert "checkpoint" not in polled and "params" not in polled
    later = client.get(f"/api/v1/jobs/{job}", params={"cursor": polled["cursor"]}).json()
    assert later["events"] == []

    for _attempt in range(10):  # posting the same site again continues the job
        lines = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token}))
        assert lines[0]["job"] == job and lines[0]["attached"] is True
        if lines[-1]["type"] != "paused":
            break
    assert lines[-1]["type"] == "done", lines[-3:]
    assert lines[-1]["slug"]
    assert google.asked == googled_before  # Google is never asked twice for one analysis
    resumed_fetches = [url for url in crawler.asked[len(asked_before):] if not url.endswith("sitemap.xml")]
    got_before = {url for url in asked_before if url in crawler.pages}  # pages that answered (failures are retried)
    assert resumed_fetches and not set(resumed_fetches) & got_before  # pages fetched before the pause are not fetched again
    assert client.get(f"/api/v1/projects/{lines[-1]['slug']}/setup").json()["engine"]["ready"]
    assert client.get(f"/api/v1/jobs/{job}").json()["status"] == "done"


def test_two_sites_run_at_once_and_a_second_post_reattaches(tmp_path):
    import threading

    from test_autopilot import _site_pages

    from tests.test_project import SITE, FakeCrawler

    other = "other-site.example"
    pages = _site_pages()
    pages[f"https://{other}/"] = "<html><head><title>Paving estimates | Other</title></head><body><h1>Paving estimates</h1><p>We pave.</p></body></html>"
    crawler = FakeCrawler(pages)
    gate = threading.Event()
    entered = threading.Event()

    def gated(urls):
        if any(SITE in url for url in urls):
            entered.set()
            assert gate.wait(20), "the test never opened the gate"
        return crawler(urls)

    _app, client, token = _route_app(tmp_path, gated, CountingGoogle())
    results = {}

    def post(name, site):
        results[name] = _lines(client.post("/api/v1/sites", json={"site": site}, headers={"x-ranksim-csrf": token}))

    first = threading.Thread(target=post, args=("first", SITE))
    first.start()
    assert entered.wait(20)
    # A different site does not wait for the first one (the old app answered 409 here).
    post("other", other)
    assert results["other"][-1]["type"] == "done", results["other"][-3:]
    # The same site again reattaches to the running job instead of starting a second one.
    again = threading.Thread(target=post, args=("again", SITE))
    again.start()
    jobs = []
    for _ in range(200):
        jobs = client.get("/api/v1/jobs").json()["jobs"]
        if any(row["key"] == f"site:{SITE.removeprefix('www.')}" for row in jobs):
            break
    assert any(row["status"] == "running" for row in jobs)
    # A pull of the same site while it is analyzed is refused with a clear message, not run twice.
    gate.set()
    first.join(30)
    again.join(30)
    assert results["first"][-1]["type"] == "done" and results["again"][-1]["type"] == "done"
    assert results["again"][0]["attached"] is True and results["again"][0]["job"] == results["first"][0]["job"]
    assert results["again"][-1]["slug"] == results["first"][-1]["slug"]
    assert [e["seq"] for e in results["again"] if "seq" in e] == [e["seq"] for e in results["first"] if "seq" in e]


def test_a_pull_of_a_site_being_analyzed_is_refused(tmp_path):
    import threading

    from test_autopilot import _site_pages

    from tests.test_project import SITE, FakeCrawler

    crawler = FakeCrawler(_site_pages())
    gate, entered = threading.Event(), threading.Event()

    def gated(urls):
        entered.set()
        assert gate.wait(20)
        return crawler(urls)

    _app, client, token = _route_app(tmp_path, crawler, CountingGoogle())
    done = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token}))
    slug = done[-1]["slug"]
    _app.state.ui.pull_options["fetcher"] = gated
    _app.state.ui.pull_options["fresh_days"] = 0  # refetch everything so the analysis reaches the gate
    holder = threading.Thread(target=lambda: client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token}))
    holder.start()
    assert entered.wait(20)
    refused = client.post(f"/api/v1/projects/{slug}/pull", json={}, headers={"x-ranksim-csrf": token})
    assert refused.status_code == 409 and "analyzed" in refused.json()["error"]
    gate.set()
    holder.join(30)
    again = _lines(client.post(f"/api/v1/projects/{slug}/pull", json={}, headers={"x-ranksim-csrf": token}))
    assert again[0]["job"] and again[-1]["type"] == "done"


def test_job_routes_check_ids_and_need_the_csrf_header(tmp_path):
    from tests.test_project import FakeCrawler

    _app, client, token = _route_app(tmp_path, FakeCrawler(), CountingGoogle())
    assert client.get("/api/v1/jobs/..%2F..%2Fsecret").status_code == 404
    assert client.get("/api/v1/jobs/" + "a" * 20).status_code == 404
    assert client.post("/api/v1/jobs/" + "a" * 20 + "/resume", json={}).status_code == 403
    assert client.post("/api/v1/jobs/" + "a" * 20 + "/resume", json={}, headers={"x-ranksim-csrf": token}).status_code == 404
    assert client.get("/api/v1/jobs").json() == {"jobs": []}
