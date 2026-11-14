"""Tests for the job registry (jobs.py): no network, fake clocks, two registries on one store act as two instances."""

from __future__ import annotations

import json
import threading
import time

import pytest

from aeo.ranksim import jobs
from aeo.ranksim.jobs import JobPaused, JobRegistry, ResumableFetch, valid_job_id
from aeo.ranksim.loop import FetchResult
from aeo.ranksim.objects import DirStore, MemoryStore

SECRET = "sk-test-FAKE-0123456789"


class Clock:
    """A controllable clock: time moves only when the test says so."""

    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def scrub(text: str) -> str:
    return text.replace(SECRET, "[redacted]")


def wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def blocking_work(gate: threading.Event, started: threading.Event | None = None, checkpoint: dict | None = None):
    """Work that saves a checkpoint (which persists), emits progress, then waits for the gate."""

    def work(ctx):
        ctx.emit({"type": "progress", "message": "crawling", "done": 1})
        ctx.save_checkpoint(checkpoint or {"step": 1})
        if started is not None:
            started.set()
        gate.wait(5)
        return {"attempt": ctx.attempt}

    return work


def job_snapshots(store, job_id: str) -> list[str]:
    return [n for n in store.list(f"ranksim/v2/jobs/{job_id}/") if n.endswith(".json")]


# -- basics ------------------------------------------------------------------------------------------


def test_start_finish_events_and_cursor_paging():
    reg = JobRegistry(clock=Clock())

    def work(ctx):
        assert ctx.params == {"site": "example.com"}
        assert ctx.checkpoint == {} and ctx.attempt == 1
        for i in range(3):
            ctx.emit({"type": "progress", "n": i})
        return {"pages": 3}

    job = reg.start("analyze", "site:example.com", "Analyze example.com", work, params={"site": "example.com"})
    assert valid_job_id(job["id"]) and len(job["id"]) >= 16
    assert reg.join(job["id"], 2)
    record = reg.get(job["id"])
    assert record["status"] == "done" and record["result"] == {"pages": 3}
    assert record["kind"] == "analyze" and record["key"] == "site:example.com" and record["attempts"] == 1

    everything = reg.events_since(job["id"], 0)
    seqs = [e["seq"] for e in everything["events"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert [e["n"] for e in everything["events"] if e.get("type") == "progress"] == [0, 1, 2]
    assert everything["events"][-1] == {**everything["events"][-1], "type": "status", "status": "done"}
    assert everything["cursor"] == seqs[-1]

    # Paging: only events after the cursor, and the cursor stays put when nothing is new.
    middle = seqs[1]
    page = reg.events_since(job["id"], middle)
    assert [e["seq"] for e in page["events"]] == seqs[2:]
    empty = reg.events_since(job["id"], everything["cursor"])
    assert empty["events"] == [] and empty["cursor"] == everything["cursor"]
    assert empty["resumable"] is False and empty["stalled"] is False


def test_error_is_scrubbed_and_secret_never_stored():
    store = MemoryStore()
    reg = JobRegistry(store, scrub=scrub, clock=Clock())

    def work(ctx):
        ctx.emit({"type": "log", "message": f"using {SECRET}", "nested": {"headers": [f"Bearer {SECRET}"]}})
        raise RuntimeError(f"provider refused key {SECRET}")

    job = reg.start("pull", "project:demo", "Pull", work)
    reg.join(job["id"], 2)
    record = reg.get(job["id"])
    assert record["status"] == "error"
    assert record["error"].startswith("RuntimeError: provider refused key [redacted]")
    assert SECRET not in json.dumps(record)
    assert all(SECRET.encode() not in body for body in store.items.values())


def test_custom_error_text_and_events_cap():
    reg = JobRegistry(clock=Clock())

    def work(ctx):
        for i in range(jobs.MAX_EVENTS + 20):
            ctx.emit({"n": i})
        raise ValueError("boom")

    job = reg.start("analyze", "site:cap.test", "Cap", work, error_text=lambda exc: "Analysis failed, try again.")
    reg.join(job["id"], 2)
    record = reg.get(job["id"])
    assert record["error"] == "Analysis failed, try again."
    assert len(record["events"]) == jobs.MAX_EVENTS
    assert record["events"][-1]["seq"] > jobs.MAX_EVENTS  # seq keeps counting past the cap
    assert reg.events_since(job["id"], 0)["truncated"] is True


def test_job_id_validation_rejects_path_tricks():
    reg = JobRegistry(clock=Clock())
    for bad in ["../../etc/passwd", "a/b", "..", "", "abc", "x" * 65, "abcdefghijklmnop/..", "abcdefghijklmnop--1", None, 12]:
        assert not valid_job_id(bad)
        assert reg.get(bad) is None
        assert reg.events_since(bad, 0) is None
        assert reg.resume(bad, lambda ctx: {}) is None
        assert list(reg.follow(bad)) == []
    assert reg.get("A" * 20) is None  # well formed but unknown


# -- two instances -----------------------------------------------------------------------------------


def test_second_instance_reads_progress_and_attaches_instead_of_starting():
    store = MemoryStore()
    clock = Clock()
    a = JobRegistry(store, clock=clock, instance="a")
    b = JobRegistry(store, clock=clock, instance="b")
    gate, started = threading.Event(), threading.Event()
    try:
        job = a.start("analyze", "site:shared.test", "Analyze", blocking_work(gate, started))
        assert started.wait(2)
        seen = b.events_since(job["id"], 0)  # from the stored snapshot: B does not run the job
        assert seen["status"] == "running" and seen["stalled"] is False
        assert any(e.get("message") == "crawling" for e in seen["events"])

        ran = []
        attached = b.start("analyze", "site:shared.test", "Analyze again", lambda ctx: ran.append(1) or {})
        assert attached["attached"] is True and attached["id"] == job["id"]
        assert [j["id"] for j in b.active("site:shared.test")] == [job["id"]]
        assert [j["id"] for j in b.active()] == [job["id"]]
        # Same instance, same key: attaches too.
        assert a.start("analyze", "site:shared.test", "Again", lambda ctx: {})["attached"] is True
    finally:
        gate.set()
    assert a.join(job["id"], 2)
    assert b.get(job["id"])["status"] == "done"
    assert ran == []
    assert b.active("site:shared.test") == []


def test_different_keys_run_concurrently():
    store = MemoryStore()
    a = JobRegistry(store, clock=Clock(), instance="a")
    b = JobRegistry(store, clock=Clock(), instance="b")
    gate = threading.Event()
    s1, s2 = threading.Event(), threading.Event()
    try:
        one = a.start("analyze", "site:one.test", "One", blocking_work(gate, s1))
        two = b.start("analyze", "site:two.test", "Two", blocking_work(gate, s2))
        assert s1.wait(2) and s2.wait(2)
        assert "attached" not in one and "attached" not in two and one["id"] != two["id"]
        assert {j["key"] for j in a.active()} == {"site:one.test", "site:two.test"}
    finally:
        gate.set()
    assert a.join(one["id"], 2) and b.join(two["id"], 2)
    assert a.get(two["id"])["status"] == "done" and b.get(one["id"])["status"] == "done"


def test_stale_job_is_resumable_and_another_instance_resumes_with_checkpoint():
    store = MemoryStore()
    clock_a, clock_b = Clock(1_000.0), Clock(1_000.0)
    a = JobRegistry(store, clock=clock_a, instance="a")
    b = JobRegistry(store, clock=clock_b, instance="b")
    gate, started = threading.Event(), threading.Event()
    job = a.start("analyze", "site:stale.test", "Analyze", blocking_work(gate, started, {"step": 7}))
    assert started.wait(2)
    try:
        # Instance A freezes (its clock stops, so no heartbeat). 50 s later B sees a stalled job.
        clock_b.advance(jobs.STALE_SECONDS + 5)
        view = b.events_since(job["id"], 0)
        assert view["status"] == "running" and view["stalled"] is True and view["resumable"] is True
        assert b.active("site:stale.test") == []
        assert [j["id"] for j in b.active("site:stale.test", include_stalled=True)] == [job["id"]]

        seen = {}

        def finish(ctx):
            seen.update(checkpoint=ctx.checkpoint, attempt=ctx.attempt)
            return {"finished_by": "b"}

        resumed = b.resume(job["id"], finish)
        assert resumed["id"] == job["id"] and "attached" not in resumed
        assert b.join(job["id"], 2)
        assert seen == {"checkpoint": {"step": 7}, "attempt": 2}
    finally:
        gate.set()  # A thaws and its work returns: it must notice it was superseded and not overwrite B
    assert a.join(job["id"], 2)
    final = b.get(job["id"])
    assert final["status"] == "done" and final["result"] == {"finished_by": "b"} and final["attempts"] == 2
    assert a.get(job["id"])["result"] == {"finished_by": "b"}
    assert any(e.get("type") == "resumed" for e in final["events"])
    # Done jobs are returned unchanged by resume.
    assert b.resume(job["id"], lambda ctx: {"again": True})["result"] == {"finished_by": "b"}


def test_resume_of_running_job_attaches():
    store = MemoryStore()
    clock = Clock()
    a = JobRegistry(store, clock=clock, instance="a")
    b = JobRegistry(store, clock=clock, instance="b")
    gate, started = threading.Event(), threading.Event()
    try:
        job = a.start("pull", "project:p", "Pull", blocking_work(gate, started))
        assert started.wait(2)
        assert b.resume(job["id"], lambda ctx: {})["attached"] is True
        assert a.resume(job["id"], lambda ctx: {})["attached"] is True
    finally:
        gate.set()
    a.join(job["id"], 2)


# -- pause and resume --------------------------------------------------------------------------------


def test_pause_before_budget_then_resume_without_refetching():
    store = MemoryStore()
    clock = Clock()
    reg = JobRegistry(store, clock=clock, budget_seconds=100, safety_seconds=25)
    urls = [f"https://example.com/p{i}" for i in range(10)]
    calls: dict[str, int] = {}
    database: dict[str, FetchResult] = {}

    def fetcher(batch):
        assert len(batch) == 1  # one URL per call, so pausing is fine grained
        out = []
        for url in batch:
            calls[url] = calls.get(url, 0) + 1
            clock.advance(10)  # each page takes 10 s of the budget
            result = FetchResult(url=url, html=f"<p>{url}</p>", status="ok")
            database[url] = result
            out.append(result)
        return out

    def work(ctx):
        fetch = ResumableFetch(ctx, fetcher, lookup=database.get, namespace="crawl")
        pages = fetch(urls)
        ctx.emit({"type": "fetched", "fetched": fetch.fetched, "reused": fetch.reused})
        return {"pages": len(pages), "html": [p.html for p in pages]}

    job = reg.start("analyze", "site:example.com", "Analyze", work)
    reg.join(job["id"], 2)
    paused = reg.get(job["id"])
    assert paused["status"] == "paused" and paused["resumable"] is True
    first_round = len(paused["checkpoint"]["crawl"]["done"])
    assert 0 < first_round < len(urls)  # stopped with time to spare, not after the budget
    # The paused job still holds the key, so a new start attaches to it.
    assert reg.start("analyze", "site:example.com", "Again", work)["attached"] is True

    resumed = reg.resume(job["id"], work)
    assert "attached" not in resumed
    reg.join(job["id"], 2)
    done = reg.get(job["id"])
    assert done["status"] == "done" and done["attempts"] == 2
    assert done["result"]["pages"] == len(urls)
    assert done["result"]["html"] == [f"<p>{u}</p>" for u in urls]
    assert all(calls[u] == 1 for u in urls), calls  # nothing fetched twice
    fetched = [e for e in done["events"] if e.get("type") == "fetched"][-1]
    assert fetched["reused"] == first_round and fetched["fetched"] == len(urls) - first_round


def test_abandoned_pause_stops_blocking_the_key():
    clock = Clock()
    reg = JobRegistry(clock=clock)

    def pause(ctx):
        ctx.save_checkpoint({"at": 1})
        raise JobPaused()

    job = reg.start("analyze", "site:p.test", "P", pause)
    reg.join(job["id"], 2)
    assert reg.start("analyze", "site:p.test", "P", lambda ctx: {})["attached"] is True
    clock.advance(jobs.PAUSED_HOLD_SECONDS + 1)
    fresh = reg.start("analyze", "site:p.test", "P2", lambda ctx: {"ok": 1})
    assert "attached" not in fresh and fresh["id"] != job["id"]
    reg.join(fresh["id"], 2)
    assert reg.get(fresh["id"])["status"] == "done"


def test_resume_refused_after_max_attempts(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 2)
    reg = JobRegistry(clock=Clock())

    def pause(ctx):
        raise JobPaused()

    job = reg.start("analyze", "site:m.test", "M", pause)
    reg.join(job["id"], 2)
    reg.resume(job["id"], pause)
    reg.join(job["id"], 2)
    stuck = reg.resume(job["id"], lambda ctx: {"never": True})
    assert stuck["status"] == "paused" and stuck["attempts"] == 2 and stuck["resumable"] is False


def test_should_pause_tracks_budget():
    clock = Clock()
    reg = JobRegistry(clock=clock, budget_seconds=60, safety_seconds=25)
    seen = []

    def work(ctx):
        seen.append((ctx.time_left(), ctx.should_pause()))
        clock.advance(40)
        seen.append((ctx.time_left(), ctx.should_pause()))
        return {}

    reg.start("analyze", "site:t.test", "T", work, background=False)
    assert seen == [(60, False), (20, True)]


def test_budget_from_environment(monkeypatch):
    monkeypatch.setenv("RANKSIM_JOB_BUDGET_SECONDS", "123")
    assert JobRegistry().budget_seconds == 123
    monkeypatch.delenv("RANKSIM_JOB_BUDGET_SECONDS")
    assert JobRegistry().budget_seconds == 240


# -- locks, persistence and pruning -------------------------------------------------------------------


def test_lock_released_after_finish_so_new_start_works():
    store = MemoryStore()
    reg = JobRegistry(store, clock=Clock())
    first = reg.start("analyze", "site:x.test", "X", lambda ctx: {"n": 1}, background=False)
    assert first["status"] == "done"
    assert store.list("ranksim/v2/locks/") == []
    second = reg.start("analyze", "site:x.test", "X", lambda ctx: {"n": 2}, background=False)
    assert "attached" not in second and second["id"] != first["id"] and second["result"] == {"n": 2}
    assert store.list("ranksim/v2/locks/") == []


def test_dead_lock_entries_are_cleaned_up():
    store = MemoryStore()
    reg = JobRegistry(store, clock=Clock())
    job = reg.start("analyze", "site:y.test", "Y", lambda ctx: {}, background=False)
    # Simulate a release that never happened: a leftover entry for a finished job.
    folder = reg._lock_dir("site:y.test")
    store.put(f"{folder}{1:020d}-abcdef--{job['id']}.1", b"{}")
    fresh = reg.start("analyze", "site:y.test", "Y", lambda ctx: {}, background=False)
    assert "attached" not in fresh
    assert store.list(folder) == []


def test_old_snapshots_pruned_to_two():
    store = MemoryStore()
    reg = JobRegistry(store, clock=Clock())

    def work(ctx):
        for i in range(6):
            ctx.save_checkpoint({"i": i})
        return {"ok": True}

    job = reg.start("analyze", "site:z.test", "Z", work, background=False)
    assert len(job_snapshots(store, job["id"])) == jobs.KEEP_SNAPSHOTS
    assert reg.get(job["id"])["checkpoint"] == {"i": 5}


def test_throttled_events_flush_and_heartbeat():
    store = MemoryStore()
    clock = Clock()
    a = JobRegistry(store, clock=clock, instance="a")
    b = JobRegistry(store, clock=clock, instance="b")
    gate, emitted = threading.Event(), threading.Event()

    def work(ctx):
        ctx.emit({"type": "progress", "n": 1})
        emitted.set()
        gate.wait(5)
        return {}

    try:
        job = a.start("analyze", "site:h.test", "H", work)
        assert emitted.wait(2)
        count = len(job_snapshots(store, job["id"]))
        # The plain event is held back (throttle) until time moves past PERSIST_EVERY_SECONDS.
        assert not any(e.get("n") == 1 for e in b.events_since(job["id"], 0)["events"])
        clock.advance(jobs.PERSIST_EVERY_SECONDS + 0.1)
        assert wait_for(lambda: any(e.get("n") == 1 for e in b.events_since(job["id"], 0)["events"]))
        # With nothing new, a heartbeat still refreshes `updated` so the job never looks stalled.
        before = b.get(job["id"])["updated"]
        clock.advance(jobs.HEARTBEAT_SECONDS + 1)
        assert wait_for(lambda: b.get(job["id"])["updated"] > before)
        assert b.get(job["id"])["stalled"] is False
        assert len(job_snapshots(store, job["id"])) <= max(count, jobs.KEEP_SNAPSHOTS)
    finally:
        gate.set()
    a.join(job["id"], 2)


def test_follow_streams_events_then_end():
    reg = JobRegistry(clock=Clock())
    gate = threading.Event()

    def work(ctx):
        ctx.emit({"type": "progress", "n": 1})
        gate.wait(5)
        ctx.emit({"type": "progress", "n": 2})
        return {"ok": True}

    job = reg.start("analyze", "site:f.test", "F", work)
    stream = reg.follow(job["id"], 0, timeout=3)
    got = []
    for item in stream:
        got.append(item)
        if item.get("n") == 1:
            gate.set()
    assert [e["n"] for e in got if e.get("type") == "progress"] == [1, 2]
    assert got[-1]["type"] == "end" and got[-1]["job"]["status"] == "done" and got[-1]["job"]["result"] == {"ok": True}
    # Following a finished job replays what is stored.
    replay = list(reg.follow(job["id"], 0))
    assert replay[-1]["type"] == "end" and len(replay) == len(got)


def test_prune_finished_jobs_older_than_a_week():
    store = MemoryStore()
    now = time.time()
    clock = Clock(now)
    reg = JobRegistry(store, clock=clock)
    old = reg.start("analyze", "site:old.test", "Old", lambda ctx: {}, background=False)
    assert reg.prune() == 0  # a fresh finished job is kept
    clock.advance(jobs.PRUNE_AFTER_SECONDS + 3600)
    assert reg.prune() == 1
    assert reg.get(old["id"]) is None or job_snapshots(store, old["id"]) == []
    assert store.list("ranksim/v2/jobs/") == []


def test_dirstore_backend(tmp_path):
    store = DirStore(tmp_path / "objects")
    clock = Clock()
    a = JobRegistry(store, clock=clock, instance="a", scrub=scrub)
    b = JobRegistry(DirStore(tmp_path / "objects"), clock=clock, instance="b")

    def work(ctx):
        ctx.emit({"type": "progress", "message": f"key {SECRET}"})
        ctx.save_checkpoint({"step": 1})
        return {"ok": True}

    job = a.start("pull", "project:demo", "Pull", work, background=False)
    view = b.events_since(job["id"], 0)
    assert view["status"] == "done" and view["result"] == {"ok": True}
    assert any(e.get("message") == "key [redacted]" for e in view["events"])
    assert len(job_snapshots(store, job["id"])) == jobs.KEEP_SNAPSHOTS
    assert store.list("ranksim/v2/locks/") == []
    assert SECRET not in "".join(p.read_text() for p in (tmp_path / "objects").rglob("*.json"))
    assert b.start("pull", "project:demo", "Pull", lambda ctx: {}, background=False)["status"] == "done"


def test_non_json_result_becomes_error():
    reg = JobRegistry(clock=Clock())
    job = reg.start("analyze", "site:j.test", "J", lambda ctx: {"bad": object()}, background=False)
    assert job["status"] == "error" and job["error"].startswith("TypeError")


def test_two_threads_racing_one_key_start_one_job():
    store = MemoryStore()
    clock = Clock()
    regs = [JobRegistry(store, clock=clock, instance=f"i{i}") for i in range(4)]
    gate = threading.Event()
    results = []

    def go(reg):
        results.append(reg.start("analyze", "site:race.test", "Race", lambda ctx: gate.wait(5) and {}))

    threads = [threading.Thread(target=go, args=(reg,)) for reg in regs for _ in range(2)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(3)
        owners = [r for r in results if not r.get("attached")]
        assert len(owners) == 1
        assert {r["id"] for r in results} == {owners[0]["id"]}
    finally:
        gate.set()
    for reg in regs:
        reg.join(owners[0]["id"], 2)


@pytest.fixture(autouse=True)
def _fast_flush(monkeypatch):
    monkeypatch.setattr(jobs, "FLUSH_POLL_SECONDS", 0.02)


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
