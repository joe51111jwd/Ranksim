"""DataForSEO and Serper clients against httpx.MockTransport. No network, no keys, no real sleeping.

Fixtures under tests/fixtures/forecast/ are DataForSEO's own docs examples (trimmed, values from 2019
to 2025, so their costs are old and never used to check prices), the real 401 answer recorded on
2026-11-22, the poison-repaired item from DataForSEO's blog, and the shape of a stored Serper answer.
Every status variant below (not ready, 40103, 40106, a spell block, a missing task) is a SYNTHETIC
EDIT of those examples, made in this file.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from aeo.ranksim.forecast import budget
from aeo.ranksim.forecast.budget import BudgetError, ProviderError, SpendLedger
from aeo.ranksim.forecast.dataforseo import (
    DataForSeoClient,
    DataForSeoSerpSource,
    parse_serp_task,
    read_raw,
)
from aeo.ranksim.forecast.serper import SerperSerpSource, parse_serper_pages
from aeo.ranksim.forecast.types import PullSpec, SerpSource

FIXTURES = Path(__file__).parent / "fixtures" / "forecast"
SECRET = "s3cr3t-dfs-value"
AUSTIN = 1026201  # "Austin,Texas,United States", City, from DataForSEO's public locations CSV


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Clock:
    """A UTC clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 22, 23, 59, 0, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.moment += timedelta(seconds=seconds)


def ledger_for(tmp_path: Path, clock: Clock, approve: str | None = "5.00") -> SpendLedger:
    ledger = SpendLedger(tmp_path / "spend.jsonl", now=clock.now)
    if approve:
        ledger.approve(Decimal(approve), by="James", note="test")
    return ledger


def dfs_client(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], clock: Clock, ledger: SpendLedger | None) -> DataForSeoClient:
    return DataForSeoClient(
        "test-login",
        SECRET,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        ledger=ledger,
        raw_dir=tmp_path / "raw",
        sleep=clock.sleep,
        now=clock.now,
    )


def spec(search: str, depth: int = 50) -> PullSpec:
    return PullSpec(search=search, location_code=AUSTIN, depth=depth)


# -- a fake DataForSEO Standard queue built from the docs examples --------------------------------


def organic(rank: int, domain: str, url: str, rank_absolute: int, checks: list[str] | None = None) -> dict[str, Any]:
    item = copy.deepcopy(load("dfs_task_get_regular.json")["tasks"][0]["result"][0]["items"][0])
    item.update({"rank_group": rank, "rank_absolute": rank_absolute, "domain": domain, "url": url, "title": f"{domain} page"})
    if checks is not None:
        item["checks"] = checks
    return item


def task_get_body(task_id: str, keyword: str, tag: str, code: int, message: str, items: list[dict] | None, spell: dict | None = None) -> dict:
    body = copy.deepcopy(load("dfs_task_get_regular.json"))
    task = body["tasks"][0]
    task.update({"id": task_id, "status_code": code, "status_message": message})
    task["path"] = ["v3", "serp", "google", "organic", "task_get", "advanced", task_id]
    task["data"].update({"keyword": keyword, "tag": tag, "location_code": AUSTIN})
    if items is None:
        task["result"] = None
    else:
        result = task["result"][0]
        result.update({"keyword": keyword, "location_code": AUSTIN, "items": items, "items_count": len(items), "spell": spell})
    return body


class FakeQueue:
    """task_post, tasks_ready and task_get/advanced. `scripts` maps a keyword to its task_get answers in order."""

    def __init__(self, scripts: dict[str, list[tuple]], *, post_status: dict[str, int] | None = None, drop: set[str] | None = None):
        self.scripts = {keyword: list(answers) for keyword, answers in scripts.items()}
        self.post_status = post_status or {}
        self.drop = drop or set()
        self.posts: list[list[dict]] = []
        self.gets: list[str] = []
        self.ready_calls = 0
        self.tasks: dict[str, tuple[str, str]] = {}
        self.counter = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path
        if path == "/v3/serp/google/organic/task_post":
            body = json.loads(request.content)
            self.posts.append(body)
            template = load("dfs_task_post.json")
            tasks = []
            for task_in in body:
                if task_in["keyword"] in self.drop:
                    continue
                self.counter += 1
                task_id = f"11221200-1535-0066-0000-{self.counter:012d}"
                task = copy.deepcopy(template["tasks"][0])
                code = self.post_status.get(task_in["keyword"], 20100)
                task.update({"id": task_id, "status_code": code, "cost": 0.0036 if code == 20100 else 0})
                task["status_message"] = "Task Created." if code == 20100 else "Invalid Field."
                task["data"].update({k: task_in[k] for k in ("keyword", "location_code", "tag")})
                tasks.append(task)
                if code == 20100:
                    self.tasks[task_id] = (task_in["keyword"], task_in["tag"])
            template.update({"tasks": tasks, "tasks_count": len(tasks), "cost": round(sum(t["cost"] for t in tasks), 6)})
            return httpx.Response(200, json=template)
        if path == "/v3/serp/google/organic/tasks_ready":
            self.ready_calls += 1
            body = load("dfs_tasks_ready.json")
            template = body["tasks"][0]["result"][0]
            body["tasks"][0]["result"] = [
                {**template, "id": task_id, "tag": tag, "endpoint_advanced": f"/v3/serp/google/organic/task_get/advanced/{task_id}"}
                for task_id, (_keyword, tag) in self.tasks.items()
            ]
            return httpx.Response(200, json=body)
        if path.startswith("/v3/serp/google/organic/task_get/advanced/"):
            task_id = path.rsplit("/", 1)[-1]
            self.gets.append(task_id)
            keyword, tag = self.tasks[task_id]
            answers = self.scripts[keyword]
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            kind = answer[0]
            if kind == "not_ready":
                return httpx.Response(200, json=task_get_body(task_id, keyword, tag, 40602, "Task In Queue.", None))
            if kind == "ok":
                return httpx.Response(200, json=task_get_body(task_id, keyword, tag, 20000, "Ok.", answer[1], answer[2] if len(answer) > 2 else None))
            if kind == "partial":
                return httpx.Response(200, json=task_get_body(task_id, keyword, tag, 40106, "Task completed with partial results.", answer[1]))
            if kind == "fail":
                return httpx.Response(200, json=task_get_body(task_id, keyword, tag, answer[1], answer[2], None))
            if kind == "empty_ok":
                return httpx.Response(200, json=task_get_body(task_id, keyword, tag, 20000, "Ok.", None))
        raise AssertionError(f"unexpected request {request.method} {path}")


def alpha_items() -> list[dict]:
    repaired = load("dfs_poison_repaired_item.json")
    return [
        {"type": "people_also_ask", "rank_group": 1, "rank_absolute": 2},
        organic(2, "www.skyscanner.com", "https://www.skyscanner.com/routes/aus", 7),
        repaired,
    ]


def test_full_standard_queue_flow(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    queue = FakeQueue(
        {
            "alpha": [("not_ready",), ("ok", alpha_items())],
            "beta": [("fail", 40103, "Task execution failed. Please try to resubmit the task.")],
            "gamma": [("partial", [organic(1, "a.com", "https://a.com/", 1), organic(2, "b.co.uk", "https://shop.b.co.uk/x", 3)])],
            "delta": [("ok", [organic(1, "c.com", "https://c.com/", 1)], {"keyword": "delta fixed", "type": "showing_results_for"})],
        }
    )
    client = dfs_client(tmp_path, queue, clock, ledger)
    pulls = client.serp_fetch([spec("alpha"), spec("beta"), spec("gamma"), spec("delta")], max_resubmits=0)

    assert [pull.spec.search for pull in pulls] == ["alpha", "beta", "gamma", "delta"]
    (posted,) = queue.posts
    for task in posted:
        assert task["location_code"] == AUSTIN and task["language_code"] == "en" and task["device"] == "desktop"
        assert task["depth"] == 50 and task["priority"] == 1 and task["resolve_poisoned_results"] is True
        assert task["tag"].startswith("rs:dfs-")
    assert [task["tag"][3:] for task in posted] == [pull.pull_id for pull in pulls]

    alpha, beta, gamma, delta = pulls
    assert alpha.ok and not alpha.partial and alpha.repaired and not alpha.spell_corrected
    assert [(hit.rank, hit.site, hit.repaired, hit.rank_absolute) for hit in alpha.hits] == [
        (1, "example.com", True, 1),
        (2, "skyscanner.com", False, 7),
    ]
    assert alpha.provider_datetime == "2019-11-15T12:57:46Z"
    assert alpha.check_url.startswith("https://www.google.com/search?q=")
    assert alpha.cost == pytest.approx(0.0036)
    assert alpha.requested_at == "2026-11-22T23:59:00Z" and alpha.completed_at == "2026-11-23T00:00:00Z"

    assert beta.ok is False and beta.status_code == 40103 and beta.hits == ()
    assert "resubmit" in beta.status_message
    assert gamma.ok and gamma.partial and [hit.site for hit in gamma.hits] == ["a.com", "b.co.uk"]
    assert delta.ok and delta.spell_corrected

    # One poll round waited for alpha, under the injected clock.
    assert clock.sleeps == [60.0]
    # The raw file is the final answer, saved under the UTC date the task was posted, and its hash matches.
    assert Path(alpha.raw_path) == tmp_path / "raw" / "dataforseo" / "2026-11-22" / f"{alpha.task_id}.json.gz"
    content = read_raw(alpha.raw_path)
    assert hashlib.sha256(content).hexdigest() == alpha.raw_sha256
    assert json.loads(content)["tasks"][0]["status_code"] == 20000
    assert gzip.decompress(Path(alpha.raw_path).read_bytes()) == content
    # Charged what the provider said it charged for the four tasks.
    assert ledger.spent_total == Decimal("0.014400")
    assert ledger.open_reservations() == []


def test_failed_task_is_resubmitted_once_and_charged_again(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    queue = FakeQueue({"beta": [("fail", 40103, "Task execution failed."), ("ok", [organic(1, "x.com", "https://x.com/", 1)])]})
    client = dfs_client(tmp_path, queue, clock, ledger)
    (pull,) = client.serp_fetch([spec("beta")])
    assert pull.ok and pull.hits[0].site == "x.com"
    assert len(queue.posts) == 2 and queue.posts[1][0]["tag"].endswith(":a1")
    assert pull.pull_id in queue.posts[1][0]["tag"]
    assert ledger.spent_total == Decimal("0.007200")
    assert 60.0 in clock.sleeps


def test_task_that_never_finishes_is_a_failed_pull_after_the_timeout(tmp_path):
    clock = Clock()
    queue = FakeQueue({"slow": [("not_ready",)]})
    client = dfs_client(tmp_path, queue, clock, ledger_for(tmp_path, clock))
    client.wait_timeout_seconds = 300
    (pull,) = client.serp_fetch([spec("slow")])
    assert pull.ok is False and pull.status_code is None and "Not ready after 300 seconds" in pull.status_message
    assert pull.task_id is not None
    assert sum(clock.sleeps) == pytest.approx(300.0)


def test_rejected_and_missing_tasks_are_failed_pulls_never_empty(tmp_path):
    clock = Clock()
    queue = FakeQueue(
        {"ok": [("ok", [organic(1, "x.com", "https://x.com/", 1)])], "hollow": [("empty_ok",)]},
        post_status={"bad": 40501},
        drop={"lost"},
    )
    client = dfs_client(tmp_path, queue, clock, ledger_for(tmp_path, clock))
    ok, bad, lost, hollow = client.serp_fetch([spec("ok"), spec("bad"), spec("lost"), spec("hollow")])
    assert ok.ok
    assert bad.ok is False and bad.status_code == 40501 and bad.task_id is None
    assert lost.ok is False and lost.status_message == "No task came back for this pull."
    assert hollow.ok is False and "held no result" in hollow.status_message
    assert all(task_id in queue.tasks for task_id in queue.gets)


def test_no_results_status_is_an_ok_empty_pull(tmp_path):
    clock = Clock()
    queue = FakeQueue({"zzz": [("fail", 40102, "No Search Results.")]})
    client = dfs_client(tmp_path, queue, clock, ledger_for(tmp_path, clock))
    (pull,) = client.serp_fetch([spec("zzz")])
    assert pull.ok is True and pull.status_code == 40102 and pull.hits == ()


def test_did_you_mean_is_not_a_corrected_answer():
    task = task_get_body("t", "k", "rs:x", 20000, "Ok.", [organic(1, "a.com", "https://a.com/", 1)], {"keyword": "k2", "type": "did_you_mean"})
    parsed = parse_serp_task(task["tasks"][0])
    assert parsed.spell_type == "did_you_mean" and parsed.spell_corrected is False
    task = task_get_body("t", "k", "rs:x", 20000, "Ok.", [], {"keyword": "k2", "type": "including_results_for"})
    assert parse_serp_task(task["tasks"][0]).spell_corrected is True


def test_401_raises_provider_error_and_charges_nothing(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json=load("dfs_401.json"))

    client = dfs_client(tmp_path, handler, clock, ledger)
    with pytest.raises(ProviderError) as raised:
        client.serp_fetch([spec("alpha")])
    assert raised.value.status_code == 40100 and raised.value.http_status == 401
    assert "API password" in str(raised.value) and SECRET not in str(raised.value) and SECRET not in repr(client)
    assert ledger.spent_total == Decimal("0") and ledger.open_reservations() == []
    with pytest.raises(ProviderError):
        client.user_data()


def test_balance_error_on_a_task_stops_the_batch(tmp_path):
    clock = Clock()
    beta_items = [organic(1, "b.com", "https://b.com/", 1)]
    queue = FakeQueue({"beta": [("ok", beta_items)]}, post_status={"alpha": 40210})
    client = dfs_client(tmp_path, queue, clock, ledger_for(tmp_path, clock))
    with pytest.raises(ProviderError, match="balance") as raised:
        client.serp_fetch([spec("alpha"), spec("beta")])
    # beta was created, so it was paid for: it comes back on the error, collected, with its task id.
    alpha, beta = raised.value.pulls
    assert alpha.ok is False and alpha.status_code == 40210 and alpha.spec.search == "alpha"
    assert beta.ok is True and beta.task_id in raised.value.posted_task_ids and beta.hits[0].site == "b.com"


def test_fresh_ledger_refuses_before_any_request(tmp_path):
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    client = dfs_client(tmp_path, handler, clock, ledger_for(tmp_path, clock, approve=None))
    with pytest.raises(BudgetError, match="approve"):
        client.serp_fetch([spec("alpha")])
    with pytest.raises(BudgetError, match="no spend ledger"):
        dfs_client(tmp_path, handler, clock, None).serp_fetch([spec("alpha")])


def test_live_mode_runs_only_by_name_and_is_metered_at_the_live_price(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v3/serp/google/organic/live/advanced"
        return httpx.Response(200, json=load("dfs_live_advanced.json"))

    client = dfs_client(tmp_path, handler, clock, ledger)
    (pull,) = client.serp_fetch([spec("flight ticket new york san francisco")], mode="live")
    body = json.loads(seen[0].content)
    assert len(body) == 1 and "priority" not in body[0] and body[0]["resolve_poisoned_results"] is True
    assert pull.ok and pull.endpoint.endswith("live/advanced")
    assert [(hit.rank, hit.site, hit.repaired) for hit in pull.hits] == [(26, "t-mobile.com", False)]
    assert ledger.spent_for_kind(budget.KIND_SERP_LIVE) == Decimal("0.003000")
    with pytest.raises(ValueError):
        client.post_serp_tasks([spec("x")], mode="live")


def test_serp_source_satisfies_the_protocol(tmp_path):
    clock = Clock()
    source = DataForSeoSerpSource(dfs_client(tmp_path, FakeQueue({}), clock, None))
    assert isinstance(source, SerpSource)
    assert source.name == "dataforseo"
    assert source.estimate_cost(5, 50) == pytest.approx(0.018)
    assert DataForSeoSerpSource(source.client, resolve_poisoned_results=False).estimate_cost(5, 50) == pytest.approx(0.015)
    assert source.fetch([]) == []


def test_post_splits_batches_at_one_hundred(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    ledger.record_sample(budget.KIND_SERP_STANDARD_NORMAL, {"done": True})
    queue = FakeQueue({})
    client = dfs_client(tmp_path, queue, clock, ledger)
    posted = client.post_serp_tasks([spec(f"kw {index}") for index in range(250)])
    assert [len(batch) for batch in queue.posts] == [100, 100, 50]
    assert len(posted) == 250 and all(task.created for task in posted)
    assert len({task.pull_id for task in posted}) == 250


# -- Labs ---------------------------------------------------------------------------------------


def labs_handler(log: list[dict], answers: dict[str, Callable[[dict], dict]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        function = request.url.path.split("/")[-2]
        body = json.loads(request.content)
        assert len(body) == 1
        log.append({"function": function, "task": body[0]})
        return httpx.Response(200, json=answers[function](body[0]))

    return handler


def test_historical_serps_rows_and_raw(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    log: list[dict] = []
    client = dfs_client(tmp_path, labs_handler(log, {"historical_serps": lambda task: load("dfs_historical_serps.json")}), clock, ledger)
    result = client.historical_serps("albert einstein", 2840, "en", date(2025, 11, 1), date(2026, 11, 1))
    assert log[0]["task"] == {"keyword": "albert einstein", "location_code": 2840, "language_code": "en", "date_from": "2025-11-01", "date_to": "2026-11-01"}
    assert result.ok and result.total_count == 5
    (snapshot,) = result.snapshots
    assert snapshot.datetime == "2021-08-01T08:05:11Z" and snapshot.snapshot_date == date(2021, 8, 1)
    (hit,) = snapshot.organic
    assert (hit.rank_group, hit.site, hit.is_new, hit.previous_rank_absolute) == (1, "dataforseo.com", False, 1)
    assert hashlib.sha256(read_raw(result.call.raw_path)).hexdigest() == result.call.raw_sha256
    assert ledger.spent_for_kind(budget.KIND_HISTORICAL_SERPS) == Decimal("0.000500")


def test_traffic_and_ranked_keywords_rows(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    log: list[dict] = []
    answers = {
        "historical_bulk_traffic_estimation": lambda task: load("dfs_historical_bulk_traffic.json"),
        "bulk_traffic_estimation": lambda task: load("dfs_bulk_traffic.json"),
        "ranked_keywords": lambda task: load("dfs_ranked_keywords.json"),
    }
    client = dfs_client(tmp_path, labs_handler(log, answers), clock, ledger)
    history = client.historical_bulk_traffic_estimation(["dataforseo.com", "cnn.com", "forbes.com"], 2840, "en")
    assert history.targets_returned == ("cnn.com",)
    assert history.targets_with_data() == ("cnn.com",)
    organic_months = [(row.year, row.month, row.count) for row in history.rows if row.item_type == "organic"]
    assert organic_months == [(2021, 1, 9812595), (2021, 2, 15936794), (2021, 3, 16031288)]
    assert log[0]["task"]["targets"] == ["dataforseo.com", "cnn.com", "forbes.com"]

    now = client.bulk_traffic_estimation(["dataforseo.com", "cnn.com", "forbes.com"], 2840, "en")
    assert [(row.item_type, row.count) for row in now.rows] == [("organic", 11786473), ("paid", 0)]

    ranked = client.ranked_keywords("dataforseo.com", 2840, "en", limit=10)
    assert ranked.total_count == 3696
    (row,) = ranked.rows
    assert (row.keyword, row.search_volume, row.rank_group, row.site) == ("1000 keywords", 140, 1, "dataforseo.com")
    assert row.last_updated_time == "2025-05-19T22:51:01Z" and row.is_lost is False
    assert log[2]["task"]["limit"] == 10 and "include_clickstream_data" not in log[2]["task"]


def test_labs_targets_are_checked_before_spending(tmp_path):
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    client = dfs_client(tmp_path, handler, clock, ledger_for(tmp_path, clock))
    with pytest.raises(ValueError, match="client site"):
        client.ranked_keywords("smartcrewhq.com")
    with pytest.raises(ValueError, match="www"):
        client.historical_bulk_traffic_estimation(["www.example.com"])
    with pytest.raises(ValueError, match="domains only"):
        client.historical_bulk_traffic_estimation(["https://example.com/page"])
    with pytest.raises(ValueError):
        client.historical_bulk_traffic_estimation([f"s{index}.com" for index in range(1001)])


def test_chunked_traffic_resumes_without_paying_twice(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    log: list[dict] = []
    client = dfs_client(tmp_path, labs_handler(log, {"historical_bulk_traffic_estimation": lambda task: load("dfs_historical_bulk_traffic.json")}), clock, ledger)
    first = client.historical_traffic_chunks(["a.com", "b.com", "cnn.com"], chunk_size=2)
    assert [call["task"]["targets"] for call in log] == [["a.com", "b.com"], ["cnn.com"]]
    assert [result.call.from_cache for result in first] == [False, False]
    spent = ledger.spent_total

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a saved chunk was paid for again")

    again = dfs_client(tmp_path, refuse, clock, ledger).historical_traffic_chunks(["a.com", "b.com", "cnn.com"], chunk_size=2)
    assert [result.call.from_cache for result in again] == [True, True]
    assert [result.rows for result in again] == [result.rows for result in first]
    assert ledger.spent_total == spent


def test_a_failed_labs_task_is_asked_again_on_resume(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    calls: list[int] = []

    def answer(task: dict) -> dict:
        calls.append(1)
        body = load("dfs_ranked_keywords.json")
        if len(calls) == 1:
            body["tasks"][0].update({"status_code": 50000, "status_message": "Internal Error.", "result": None, "cost": 0})
        return body

    client = dfs_client(tmp_path, labs_handler([], {"ranked_keywords": answer}), clock, ledger)
    first = client.ranked_keywords("dataforseo.com", limit=10)
    assert first.ok is False and first.rows == () and ".failed-" in first.call.raw_path
    second = client.ranked_keywords("dataforseo.com", limit=10)
    assert second.ok and len(second.rows) == 1 and len(calls) == 2


def test_user_data_balance_is_free_and_does_not_keep_the_login(tmp_path):
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/appendix/user_data" and request.method == "GET"
        envelope = load("dfs_task_get_regular.json")  # SYNTHETIC: the docs' user_data result wrapped in the standard envelope
        envelope["tasks"][0].update({"path": ["v3", "appendix", "user_data"], "data": {"api": "appendix", "function": "user_data"}})
        envelope["tasks"][0]["result"] = [load("dfs_user_data_result.json")]
        return httpx.Response(200, json=envelope)

    balance = dfs_client(tmp_path, handler, clock, None).user_data()
    assert balance.balance_usd == Decimal("-3809.628325") and balance.total_usd == Decimal("1")
    assert "login" not in balance.__dataclass_fields__
    assert Path(balance.raw_path).exists()


def test_tasks_ready_parses_the_docs_example(tmp_path):
    clock = Clock()
    client = dfs_client(tmp_path, lambda request: httpx.Response(200, json=load("dfs_tasks_ready.json")), clock, None)
    (ready,) = client.tasks_ready()
    assert ready.task_id == "11081554-0696-0066-0000-27e68ec15871" and ready.tag == "tag1"


def test_free_get_retries_a_server_error_with_the_injected_sleep(tmp_path):
    clock = Clock()
    answers = [httpx.Response(500, text="oops"), httpx.Response(200, json=load("dfs_tasks_ready.json"))]
    client = dfs_client(tmp_path, lambda request: answers.pop(0), clock, None)
    assert len(client.tasks_ready()) == 1
    assert clock.sleeps == [5.0]


# -- the $1 samples -----------------------------------------------------------------------------


def test_historical_serps_sample_reports_how_far_back_and_how_deep(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    log: list[dict] = []

    def answer(task: dict) -> dict:
        body = load("dfs_historical_serps.json")
        if "date_from" in task and task["keyword"] == "first":
            body["tasks"][0].update({"status_code": 40501, "status_message": "Invalid Field: 'date_from'.", "result": None, "cost": 0})
        return body

    client = dfs_client(tmp_path, labs_handler(log, {"historical_serps": answer}), clock, ledger)
    report = client.sample_historical_serps(["first", "second"], 2840, "en")
    assert [entry["task"].get("date_from") for entry in log] == ["2024-11-01", None, "2024-11-01"]
    findings = ledger.sample_findings(budget.KIND_HISTORICAL_SERPS)
    assert findings == report.findings
    assert findings["earliest_snapshot"] == "2021-08-01" and findings["deepest_organic_rank"] == 1
    assert findings["refused_start_dates"][0]["status_code"] == 40501
    assert Decimal(findings["spent_usd"]) <= budget.SAMPLE_MAX_USD
    # The big pull is now allowed, and a second sample is refused.
    ledger.approve(Decimal("20"), by="James", note="big pull")
    ledger.reserve("5,000 searches", budget.KIND_HISTORICAL_SERPS, Decimal("14.40"))
    with pytest.raises(BudgetError, match="already recorded"):
        client.sample_historical_serps(["third"], 2840, "en")


def test_traffic_sample_counts_small_sites_with_data_and_stays_under_one_dollar(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    log: list[dict] = []

    def answer(task: dict) -> dict:
        body = load("dfs_historical_bulk_traffic.json")
        item = body["tasks"][0]["result"][0]["items"][0]
        first, second, third = (copy.deepcopy(item) for _ in range(3))
        first["target"], second["target"], third["target"] = task["targets"][0], task["targets"][1], task["targets"][2]
        third["metrics"]["organic"] = [{"year": 2026, "month": 10, "etv": 0, "count": 0}]
        third["metrics"]["paid"] = []
        body["tasks"][0]["result"][0]["items"] = [first, second, third]
        body["tasks"][0]["cost"] = body["cost"] = 0.9996
        return body

    client = dfs_client(tmp_path, labs_handler(log, {"historical_bulk_traffic_estimation": answer}), clock, ledger)
    report = client.sample_historical_traffic([f"tiny{index}.com" for index in range(1000)])
    assert len(log[0]["task"]["targets"]) == 733  # 0.12 + 733 x 0.0012 = 0.9996, the most under 1.00
    assert report.findings["sites_asked"] == 733
    assert report.findings["sites_returned"] == 3 and report.findings["sites_with_data"] == 2
    assert ledger.sample_recorded(budget.KIND_HISTORICAL_TRAFFIC)
    assert ledger.spent_for_kind(budget.KIND_HISTORICAL_TRAFFIC) == Decimal("0.999600")


# -- Serper -------------------------------------------------------------------------------------


def serper_page(page: int, links: list[str], position_start: int = 1) -> dict:
    body = load("serper_search.json")
    template = body["organic"][0]
    body["searchParameters"]["page"] = page
    body["organic"] = [{**template, "link": link, "position": position_start + index} for index, link in enumerate(links)]
    return body


def serper_source(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], clock: Clock, ledger: SpendLedger | None) -> SerperSerpSource:
    return SerperSerpSource(
        "serper-test-key",
        locations={AUSTIN: "Austin,Texas,United States"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        ledger=ledger,
        raw_dir=tmp_path / "raw",
        sleep=clock.sleep,
        now=clock.now,
    )


def test_serper_pages_through_and_keeps_positions_continuous(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    bodies: list[dict] = []
    pages = {
        1: [f"https://site{index}.com/" for index in range(1, 11)],
        2: [f"https://www.page{index}.com/a" for index in range(1, 10)],  # 9 results, positions restart at 1
        3: ["https://site3.com/", "https://last.com/", "https://after.com/"],  # a repeat of a page-1 URL
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "google.serper.dev" and request.headers["x-api-key"] == "serper-test-key"
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=serper_page(body["page"], pages[body["page"]]))

    source = serper_source(tmp_path, handler, clock, ledger)
    assert isinstance(source, SerpSource)
    (pull,) = source.fetch([spec("example query", depth=21)])
    assert [body["page"] for body in bodies] == [1, 2, 3]
    assert all(body["num"] == 10 and body["gl"] == "us" and body["hl"] == "en" for body in bodies)
    assert bodies[0]["location"] == "Austin,Texas,United States" and bodies[0]["q"] == "example query"
    assert pull.ok and [hit.rank for hit in pull.hits] == list(range(1, 22))
    assert pull.hits[10].site == "page1.com" and pull.hits[19].url == "https://last.com/"
    assert pull.hits[20].url == "https://after.com/"
    assert all(hit.rank_absolute is None and not hit.repaired for hit in pull.hits)
    assert ledger.spent_for_kind(budget.KIND_SERPER_SEARCH) == Decimal("0.003000")
    raw = json.loads(read_raw(pull.raw_path))
    assert [page["searchParameters"]["page"] for page in raw] == [1, 2, 3]
    assert hashlib.sha256(read_raw(pull.raw_path)).hexdigest() == pull.raw_sha256
    assert source.estimate_cost(5, 50) == pytest.approx(0.025)


def test_serper_bad_key_raises_and_charges_nothing(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    source = serper_source(tmp_path, lambda request: httpx.Response(403, json={"message": "Unauthorized."}), clock, ledger)
    with pytest.raises(ProviderError, match="HTTP 403"):
        source.fetch([spec("q")])
    assert ledger.spent_total == Decimal("0") and ledger.open_reservations() == []


def test_serper_failed_page_is_a_failed_pull(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    answers = {1: httpx.Response(200, json=serper_page(1, ["https://a.com/"])), 2: httpx.Response(400, text="bad page")}
    source = serper_source(tmp_path, lambda request: answers[json.loads(request.content)["page"]], clock, ledger)
    (pull,) = source.fetch([spec("q", depth=20)])
    assert pull.ok is False and pull.status_code == 400 and pull.hits == ()
    raw = json.loads(read_raw(pull.raw_path))
    assert raw[1] == {"body_text": "bad page", "http_status": 400}
    assert ledger.spent_for_kind(budget.KIND_SERPER_SEARCH) == Decimal("0.001000")


def test_serper_refuses_an_unknown_city_before_spending(tmp_path):
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    source = serper_source(tmp_path, handler, clock, ledger_for(tmp_path, clock))
    with pytest.raises(ValueError, match="location"):
        source.fetch([PullSpec(search="q", location_code=999)])


def test_parse_serper_pages_stops_at_depth():
    pages = [serper_page(1, [f"https://s{index}.com/" for index in range(10)]), serper_page(2, ["https://t.com/", "https://u.com/"])]
    assert len(parse_serper_pages(pages, 11)) == 11


# -- bugs found in review, each with its own test ------------------------------------------------


def test_serper_page_that_is_not_json_is_a_failed_pull_not_an_empty_one(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    answers = {1: httpx.Response(200, text="<html>busy</html>"), 2: httpx.Response(200, json={"credits": 1})}
    source = serper_source(tmp_path, lambda request: answers[json.loads(request.content)["page"]], clock, ledger)
    (html_page,) = source.fetch([spec("q", depth=10)])
    assert html_page.ok is False and "no organic result list" in html_page.status_message
    assert html_page.cost is None  # no credits field, so the ledger kept the estimate
    assert ledger.spent_for_kind(budget.KIND_SERPER_SEARCH) == Decimal("0.001000")
    answers[1] = httpx.Response(200, json={"credits": 1})
    (no_organic,) = source.fetch([spec("q", depth=10)])
    assert no_organic.ok is False and no_organic.hits == ()


def test_live_mode_checks_every_spec_before_paying_for_any(tmp_path):
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    client = dfs_client(tmp_path, handler, clock, ledger_for(tmp_path, clock))
    with pytest.raises(ValueError, match="depth"):
        client.serp_fetch([spec("a"), spec("b"), spec("c", depth=300)], mode="live")


def test_ranked_keywords_accepts_a_www_page_but_not_a_bare_www_domain(tmp_path):
    clock = Clock()
    log: list[dict] = []
    client = dfs_client(tmp_path, labs_handler(log, {"ranked_keywords": lambda task: load("dfs_ranked_keywords.json")}), clock, ledger_for(tmp_path, clock))
    client.ranked_keywords("www.example.com/page", limit=10)
    assert log[0]["task"]["target"] == "www.example.com/page"
    with pytest.raises(ValueError, match="www"):
        client.ranked_keywords("www.example.com", limit=10)


def test_historical_serps_refuses_dates_in_the_wrong_order(tmp_path):
    clock = Clock()
    client = dfs_client(tmp_path, FakeQueue({}), clock, ledger_for(tmp_path, clock))
    with pytest.raises(ValueError, match="after"):
        client.historical_serps("k", 2840, "en", date(2026, 5, 1), date(2026, 1, 1))


def test_serp_sample_marks_its_reservations_and_records_findings(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    queue = FakeQueue({"a": [("ok", [organic(1, "x.com", "https://x.com/", 1)])], "b": [("partial", [])]})
    client = dfs_client(tmp_path, queue, clock, ledger)
    report = client.sample_serp([spec("a"), spec("b")])
    assert report.findings["ok"] == 2 and report.findings["partial"] == 1
    rows = [json.loads(line)["row"] for line in ledger.path.read_text().splitlines()]
    assert [row["sample"] for row in rows if row["event"] == "reserve"] == [True]
    assert ledger.sample_recorded(budget.KIND_SERP_STANDARD_NORMAL)


# -- paid work is never thrown away, refusals come before spending ------------------------------

from aeo.ranksim.forecast.dataforseo import DFS_SANDBOX, PROVIDER_SANDBOX  # noqa: E402
from aeo.ranksim.forecast.reader import build_clean_reading, read_clean  # noqa: E402
from aeo.ranksim.forecast.types import REAL, SYNTHETIC  # noqa: E402


def many_specs(count: int) -> list[PullSpec]:
    return [spec(f"search {index:03d}") for index in range(count)]


def test_a_round_the_approval_cannot_cover_is_refused_before_any_task_is_posted(tmp_path):
    """$0.50 approved, 150 searches at depth 50 ($0.54 in two task_post calls): nothing is posted, nothing spent."""
    clock = Clock()
    ledger = ledger_for(tmp_path, clock, approve="0.50")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent when the batch does not fit the approval")

    source = DataForSeoSerpSource(dfs_client(tmp_path, handler, clock, ledger))
    result = read_clean(source, many_specs(150), pulls=1, sleep=clock.sleep, now=clock.now)
    assert ledger.spent_total == Decimal("0") and ledger.open_reservations() == []
    assert result.pulls == () and len(result.failed_rounds) == 1 and "BudgetError" in result.failed_rounds[0][1]


def test_tasks_already_paid_for_come_back_as_pulls_when_a_later_chunk_is_refused(tmp_path):
    """Chunk 1 (100 tasks) is posted and paid. DataForSEO refuses a task of chunk 2 (40210): every created task
    still comes back from read_clean as a Pull with its task id, and the unsent ones are failed pulls that say why."""
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    specs = many_specs(150)
    items = [organic(1, "a.com", "https://a.com/", 1)]
    queue = FakeQueue({item.search: [("ok", items)] for item in specs}, post_status={"search 120": 40210})
    source = DataForSeoSerpSource(dfs_client(tmp_path, queue, clock, ledger))

    result = read_clean(source, specs, pulls=1, sleep=clock.sleep, now=clock.now)

    assert [len(batch) for batch in queue.posts] == [100, 50]
    paid = set(queue.tasks)  # every task the fake queue created, chunk 1 and the rest of chunk 2
    assert len(paid) == 149 and ledger.spent_total > 0
    returned = {pull.task_id for pull in result.pulls if pull.task_id}
    assert paid <= returned
    assert len(result.pulls) == 150 and sum(pull.ok for pull in result.pulls) == 149
    (refused,) = [pull for pull in result.pulls if not pull.ok]
    assert refused.spec.search == "search 120" and refused.status_code == 40210
    assert len(result.failed_rounds) == 1 and "40210" in result.failed_rounds[0][1]
    # The readings are built from the kept pulls (one pull each here, so degraded, but not empty of pulls).
    assert all(reading.pulls_total == 1 for reading in result.readings)


# -- the sandbox is dummy data ------------------------------------------------------------------


def test_sandbox_pulls_are_not_real_cost_nothing_and_keep_their_own_raw_folder(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    queue = FakeQueue({"emergency plumber": [("ok", [organic(1, "a.com", "https://a.com/", 1)])]})
    client = DataForSeoClient("test-login", SECRET, client=httpx.Client(transport=httpx.MockTransport(queue)), ledger=ledger,
                              raw_dir=tmp_path / "raw", sleep=clock.sleep, now=clock.now, base_url=DFS_SANDBOX)
    pulls = []
    for _ in range(3):
        pulls += client.serp_fetch([spec("emergency plumber")], max_resubmits=0)
        clock.sleep(3600)
    assert all(pull.ok and pull.provider == PROVIDER_SANDBOX for pull in pulls)
    assert all("/dataforseo-sandbox/" in str(pull.raw_path) for pull in pulls)
    assert build_clean_reading(pulls).label == SYNTHETIC
    assert ledger.spent_total == Decimal("0") and ledger.state().rows == 1  # only the approval
    assert DataForSeoSerpSource(client).estimate_cost(5) == 0.0 and DataForSeoSerpSource(client).name == PROVIDER_SANDBOX
    with pytest.raises(BudgetError, match="sandbox"):
        client.sample_serp([spec("emergency plumber")])


def test_production_pulls_stay_real(tmp_path):
    clock = Clock()
    queue = FakeQueue({"emergency plumber": [("ok", [organic(1, "a.com", "https://a.com/", 1)])]})
    client = dfs_client(tmp_path, queue, clock, ledger_for(tmp_path, clock))
    pulls = []
    for _ in range(3):
        pulls += client.serp_fetch([spec("emergency plumber")], max_resubmits=0)
        clock.sleep(3600)
    assert all(pull.provider == "dataforseo" for pull in pulls) and build_clean_reading(pulls).label == REAL


# -- the x5 operator charge ---------------------------------------------------------------------


def test_reservation_for_an_operator_keyword_follows_the_x5_billing_rule(tmp_path):
    """Provider map 3.6: base x pages, x5 for a search operator, + base for resolve_poisoned_results."""
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)
    queue = FakeQueue({})
    client = dfs_client(tmp_path, queue, clock, ledger)
    client.post_serp_tasks([spec("site:example.com plumber"), spec("plumber austin")])
    rows = [json.loads(line)["row"] for line in ledger.path.read_text().splitlines()]
    (reserve,) = [row for row in rows if row["event"] == "reserve"]
    assert reserve["estimate_micros"] == 15600 + 3600  # 0.0006 x 5 x 5 + 0.0006, and 0.0006 x 5 + 0.0006


# -- a $1 sample that learned nothing keeps the gate closed ------------------------------------


def test_a_historical_serps_sample_where_every_call_failed_does_not_unlock_the_big_pull(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock, approve="20.00")
    log: list[dict] = []

    def failing(task: dict) -> dict:
        body = load("dfs_historical_serps.json")
        body["tasks"][0].update({"status_code": 50000, "status_message": "Internal Error.", "result": None, "cost": 0})
        return body

    client = dfs_client(tmp_path, labs_handler(log, {"historical_serps": failing}), clock, ledger)
    report = client.sample_historical_serps(["plumber austin", "roofer austin"], 2840, "en")
    assert report.recorded is False and report.findings["earliest_snapshot"] is None
    assert not ledger.sample_recorded(budget.KIND_HISTORICAL_SERPS)
    assert ledger.failed_samples(budget.KIND_HISTORICAL_SERPS)[0]["not_recorded_because"]
    with pytest.raises(BudgetError, match="no recorded"):
        ledger.reserve("Historical SERPs, 5,000 searches x 24 months", budget.KIND_HISTORICAL_SERPS, Decimal("14.40"))
    # The sample may be run again (no "already recorded"), and a successful run opens the gate.
    client = dfs_client(tmp_path, labs_handler(log, {"historical_serps": lambda task: load("dfs_historical_serps.json")}), clock, ledger)
    assert client.sample_historical_serps(["plumber austin"], 2840, "en").recorded is True
    ledger.reserve("Historical SERPs, 5,000 searches x 24 months", budget.KIND_HISTORICAL_SERPS, Decimal("14.40"))


def test_big_traffic_pull_is_refused_after_a_sample_whose_only_task_failed(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)

    def failing(task: dict) -> dict:
        body = load("dfs_historical_bulk_traffic.json")
        body["tasks"][0].update({"status_code": 40501, "status_message": "Invalid Field: 'item_types'.", "result": None, "cost": 0})
        body["cost"] = 0
        return body

    client = dfs_client(tmp_path, labs_handler([], {"historical_bulk_traffic_estimation": failing}), clock, ledger)
    report = client.sample_historical_traffic([f"tiny{index}.com" for index in range(1000)])
    assert report.recorded is False and report.findings["share_with_data"] is None
    with pytest.raises(BudgetError, match="no recorded"):
        client.historical_bulk_traffic_estimation([f"big{index}.com" for index in range(1000)])


def test_a_serper_sample_with_no_usable_pull_is_not_recorded(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "server error"})

    report = serper_source(tmp_path, handler, clock, ledger).sample([spec("water heater repair", depth=10)])
    assert report.recorded is False and not ledger.sample_recorded(budget.KIND_SERPER_SEARCH)


# -- Serper: a short pull is partial ------------------------------------------------------------


def test_serper_pull_with_10_of_50_results_is_marked_partial(tmp_path):
    clock = Clock()
    ledger = ledger_for(tmp_path, clock)

    def handler(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["page"]
        links = [f"https://site{index}.com/" for index in range(10)] if page == 1 else []
        return httpx.Response(200, json=serper_page(page, links))

    (pull,) = serper_source(tmp_path, handler, clock, ledger).fetch([spec("water heater repair", depth=50)])
    assert pull.ok and len(pull.hits) == 10 and pull.partial is True

    def full(request: httpx.Request) -> httpx.Response:
        page = json.loads(request.content)["page"]
        return httpx.Response(200, json=serper_page(page, [f"https://p{page}s{index}.com/" for index in range(10)], 10 * page - 9))

    (whole,) = serper_source(tmp_path, full, clock, ledger).fetch([spec("water heater repair", depth=50)])
    assert whole.ok and len(whole.hits) == 50 and whole.partial is False
