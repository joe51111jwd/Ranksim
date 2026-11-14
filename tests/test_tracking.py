"""Tracking plugin: monthly counter and cap, freshness reuse, cron auth and due selection, rank history, SERP features.

Fakes only: no network, no real keys.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE, TODAY, FakeCrawler, _results

from aeo.errors import ProviderConfigError
from aeo.ranksim import project as proj
from aeo.ranksim import workspace as ws
from aeo.ranksim.plugins import tracking
from aeo.ranksim.serp import (
    DataForSeoProvider,
    SerpApiProvider,
    SerperProvider,
    SerpHit,
    SerpProvider,
    parse_features,
)

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
OURS = "https://www.dirtcrew.example/dirt-work-software/"


class Clock:
    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


class FakeGoogle:
    """A Google provider that counts requests and returns SERP features for each search."""

    name = "fakegoogle"
    is_google = True
    synthetic = False

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail or set()
        self.lock = threading.Lock()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        return self.search_with_features(query, count)[0]

    def search_with_features(self, query: str, count: int = 10):
        with self.lock:
            self.calls.append(query)
        if query in self.fail:
            raise RuntimeError("boom")
        urls = _results().get(query, [])[:count]
        hits = [
            SerpHit(position=index, url=url, title=f"Result {index}", snippet="", provider=self.name, synthetic=False, is_google=True)
            for index, url in enumerate(urls, start=1)
        ]
        return hits, {"related_searches": [f"{query} free"], "people_also_ask": [{"question": f"What is {query}?"}]}


class PlainGoogle:
    """An older provider with only search(): the pull must still work and store no features."""

    name = "plaingoogle"
    is_google = True
    synthetic = False

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        return [SerpHit(position=1, url=f"https://alpha.example/{query.replace(' ', '-')}", title="", snippet="", provider=self.name, synthetic=False, is_google=True)]


def _settings(tmp_path: Path, **keys):
    from aeo.config import Settings

    values = {
        "database_url": f"sqlite:///{tmp_path / 'aeo.db'}",
        "ranksim_database_url": f"sqlite:///{tmp_path / 'rank.db'}",
        "serpapi_key": None,
        "serper_api_key": None,
        "dataforseo_login": None,
        "dataforseo_password": None,
        "brave_api_key": None,
        "crawl_delay_seconds": 0,
    }
    values.update(keys)
    return Settings(**values)


def _project(root: Path, slug: str = "dirt-crew", searches=None):
    return proj.create_project("Dirt Crew", SITE, searches or SEARCHES, root=root, slug=slug, discover_sitemap=False)


def _write_snapshot(project, pull_id: str, written: datetime, rows: dict[str, list[str]], *, kind: str = "api", pulled: datetime | None = None, features=None):
    """A stored google.json like a pull writes, without crawling or training."""
    folder = Path(project.dir) / "pulls" / pull_id
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (pulled or written).isoformat(timespec="seconds")
    searches = []
    for search, urls in rows.items():
        searches.append(
            {
                "search": search,
                "results": [{"position": index, "url": url, "domain": ""} for index, url in enumerate(urls, start=1)],
                "source": "fake",
                "provider": "fake",
                "pulled_at": stamp,
                "error": "",
                "features": (features or {}).get(search, {}),
            }
        )
    data = {"pull_id": pull_id, "written_at": written.isoformat(timespec="seconds"), "kind": kind, "searches": searches}
    (folder / "google.json").write_text(json.dumps(data), encoding="utf-8")


def _entry(pulled_at: str | None, results=1):
    return SimpleNamespace(results=[1] * results, error="", pulled_at=pulled_at)


# -- counter and cap --------------------------------------------------------------------------------


def test_counter_counts_calls_and_persists_in_the_projects_folder(tmp_path):
    budget = tracking.Budget(tmp_path, cap=3, clock=Clock())
    assert budget.claim("a") == "call"
    assert budget.claim("b") == "call"
    again = tracking.Budget(tmp_path, cap=3, clock=Clock())  # a new process reads the same file
    assert again.used() == 2 and again.remaining() == 1
    assert (tmp_path / ".tracking" / "usage.json").is_file()
    assert again.claim("c") == "call"
    verdict = again.claim("d")
    assert verdict.startswith("monthly search cap reached: 3 of 3 searches used in November 2026")
    assert "RANKSIM_MONTHLY_SEARCH_CAP" in verdict and "December 1" in verdict
    month = again.month()
    assert month["searches"] == 3 and month["capped"] == 1


def test_month_rolls_over_and_cap_comes_from_env(tmp_path, monkeypatch):
    clock = Clock(datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc))
    monkeypatch.setenv(tracking.CAP_ENV, "1")
    budget = tracking.Budget(tmp_path, clock=clock)
    assert budget.cap == 1
    assert budget.claim("a") == "call"
    assert budget.claim("b") != "call"
    assert "January 1" in budget.cap_message()
    clock.moment = datetime(2027, 1, 1, 0, 5, tzinfo=timezone.utc)
    assert budget.used() == 0 and budget.claim("a") == "call"
    monkeypatch.setenv(tracking.CAP_ENV, "not a number")
    assert budget.cap == tracking.DEFAULT_CAP
    monkeypatch.delenv(tracking.CAP_ENV)
    assert budget.cap == tracking.DEFAULT_CAP == 500


def test_counter_is_exact_under_threads(tmp_path):
    budget = tracking.Budget(tmp_path, cap=25, clock=Clock())
    verdicts: list[str] = []
    lock = threading.Lock()

    def work():
        for _ in range(10):
            verdict = budget.claim("x")
            with lock:
                verdicts.append(verdict)

    threads = [threading.Thread(target=work) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert verdicts.count("call") == 25
    assert budget.used() == 25 and budget.month()["capped"] == 25


def test_health_records_last_ok_and_last_error(tmp_path):
    clock = Clock()
    budget = tracking.Budget(tmp_path, clock=clock)
    assert tracking._healthy(budget.usage()) is None
    budget.record(ok=True)
    assert tracking._healthy(budget.usage()) is True
    clock.moment = NOW + timedelta(minutes=1)
    budget.record(ok=False, error="HTTP 403")
    data = budget.usage()
    assert data["last_error"] == "HTTP 403" and tracking._healthy(data) is False
    assert budget.month()["errors"] == 1


# -- freshness ------------------------------------------------------------------------------------


def test_freshness_window(tmp_path):
    budget = tracking.Budget(tmp_path, cap=10, fresh_hours=6, clock=Clock())
    assert budget.is_fresh(_entry((NOW - timedelta(hours=5)).isoformat()))
    assert not budget.is_fresh(_entry((NOW - timedelta(hours=7)).isoformat()))
    assert not budget.is_fresh(_entry((NOW + timedelta(hours=1)).isoformat()))  # a clock in the future is not fresh
    assert not budget.is_fresh(_entry((NOW - timedelta(hours=1)).isoformat(), results=0))
    assert not budget.is_fresh(_entry(None))
    assert not budget.is_fresh(None)
    assert budget.claim("a", _entry((NOW - timedelta(hours=1)).isoformat())) == "reuse"
    assert budget.used() == 0 and budget.month()["reused"] == 1
    off = tracking.Budget(tmp_path, cap=10, fresh_hours=0, clock=Clock())
    assert not off.is_fresh(_entry((NOW - timedelta(minutes=1)).isoformat()))


def test_refusal_only_when_a_paid_search_is_needed(tmp_path):
    budget = tracking.Budget(tmp_path, cap=0, fresh_hours=6, clock=Clock())
    fresh = {"a": _entry((NOW - timedelta(hours=1)).isoformat())}
    assert budget.refusal(["a"], fresh) is None
    assert budget.refusal(["a", "b"], fresh).startswith("monthly search cap reached")
    with pytest.raises(tracking.SearchCapError) as raised:
        budget.refuse(budget.refusal(["b"], {}))
    assert isinstance(raised.value, ProviderConfigError)
    assert str(raised.value).startswith("Monthly search cap reached") and "nothing was invented" in str(raised.value)


# -- the pull with a budget ----------------------------------------------------------------------


def test_pull_with_budget_counts_reuses_and_stores_features(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path / "projects")
    google = FakeGoogle()
    budget = tracking.Budget(tmp_path / "projects", cap=100, fresh_hours=6)
    first = proj.pull(project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, workers=2, budget=budget, engines=[])
    assert first.google_kind == "api" and budget.used() == len(SEARCHES)
    snapshot = proj.latest_google(project)
    entry = snapshot.by_search()["dirt work software"]
    assert entry.features["related_searches"] == ["dirt work software free"]
    assert tracking.serp_features(project)["dirt work software"]["people_also_ask"][0]["question"] == "What is dirt work software?"
    stamps = {item.search: item.pulled_at for item in snapshot.searches}

    second = proj.pull(project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, workers=2, budget=budget, engines=[])
    assert len(google.calls) == len(SEARCHES)  # nothing paid again
    assert budget.used() == len(SEARCHES) and budget.month()["reused"] == len(SEARCHES)
    assert "reused REAL results" in second.google_note
    again = proj.latest_google(project)
    assert {item.search: item.pulled_at for item in again.searches} == stamps  # reused rows keep their own pull date
    assert all(not item.error for item in again.searches)
    assert again.by_search()["dirt work software"].features  # features travel with reused rows


def test_pull_hits_the_cap_midway_and_keeps_earlier_results(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path / "projects")
    proj.pull(project, settings, serp_provider=FakeGoogle(), fetcher=FakeCrawler(), today=TODAY, workers=2, engines=[])
    budget = tracking.Budget(tmp_path / "projects", cap=2, fresh_hours=0)
    google = FakeGoogle()
    report = proj.pull(project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, workers=2, budget=budget, engines=[])
    assert len(google.calls) == 2 and budget.used() == 2
    snapshot = proj.latest_google(project)
    capped = [item for item in snapshot.searches if "monthly search cap reached" in item.error]
    assert len(capped) == len(SEARCHES) - 2
    assert all(item.results for item in capped)  # earlier REAL results kept, with their date
    assert report.searches_with_results == len(SEARCHES)


def test_pull_over_cap_carries_or_refuses(tmp_path):
    settings = _settings(tmp_path)
    fresh_project = _project(tmp_path / "projects", slug="fresh", searches=["nobody has this search"])
    budget = tracking.Budget(tmp_path / "projects", cap=0)
    google = FakeGoogle()
    with pytest.raises(tracking.SearchCapError) as raised:
        proj.pull(fresh_project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, budget=budget, engines=[])
    assert "Monthly search cap reached: 0 of 0" in str(raised.value)
    assert google.calls == [] and proj.list_pulls(fresh_project) == []

    project = _project(tmp_path / "projects")
    proj.pull(project, settings, serp_provider=FakeGoogle(), fetcher=FakeCrawler(), today=TODAY, workers=2, engines=[])
    fresh = proj.pull(project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, workers=2, budget=budget, engines=[])
    assert google.calls == [] and fresh.google_kind == "api"  # results pulled seconds ago need no paid request
    stale = tracking.Budget(tmp_path / "projects", cap=0, fresh_hours=0)
    report = proj.pull(project, settings, serp_provider=google, fetcher=FakeCrawler(), today=TODAY, workers=2, budget=stale, engines=[])
    assert google.calls == []
    assert report.google_kind == "carried" and "monthly search cap reached" in report.google_note


def test_failed_request_is_counted_and_recorded(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path / "projects")
    budget = tracking.Budget(tmp_path / "projects", cap=100)
    proj.pull(project, settings, serp_provider=FakeGoogle(fail={"grading job costing"}), fetcher=FakeCrawler(), today=TODAY, workers=2, budget=budget, engines=[])
    data = budget.usage()
    assert budget.used() == len(SEARCHES) and budget.month()["errors"] == 1
    assert data["last_error"] == "RuntimeError" and data["last_ok_at"]


def test_provider_without_features_still_pulls(tmp_path):
    settings = _settings(tmp_path)
    project = _project(tmp_path / "projects", searches=["dirt work software"])
    proj.pull(project, settings, serp_provider=PlainGoogle(), fetcher=FakeCrawler(), today=TODAY, engines=[])
    entry = proj.latest_google(project).by_search()["dirt work software"]
    assert entry.results and entry.features == {}


def test_old_snapshot_files_read_without_features():
    entry = proj.SearchResults.from_dict({"search": "a", "results": [{"position": 1, "url": "https://x.example/"}], "source": "", "provider": "", "pulled_at": None})
    assert entry.features == {}
    assert proj.SearchResults.from_dict({"search": "a", "results": [], "features": ["bad"]}).features == {}
    assert entry.to_dict()["features"] == {}


# -- SERP features --------------------------------------------------------------------------------

SERPER_PAYLOAD = {
    "organic": [
        {"position": 1, "link": "https://a.example/", "title": "A", "sitelinks": [{"title": "Pricing", "link": "https://a.example/pricing"}]},
        {"position": 2, "link": "https://b.example/", "title": "B"},
    ],
    "peopleAlsoAsk": [{"question": "What is dirt work?", "snippet": "Moving soil.", "link": "https://c.example/q"}, {"snippet": "no question"}],
    "relatedSearches": [{"query": "dirt work software free"}, {"query": "excavation app"}],
    "answerBox": {"title": "Dirt work", "snippet": "Earthmoving", "link": "https://d.example/"},
    "knowledgeGraph": {"title": "HCSS", "type": "Software company", "description": "Makes software.", "website": "https://hcss.example/"},
    "topStories": [{"title": "News", "link": "https://news.example/1", "source": "Daily", "date": "2 days ago"}],
    "videos": [{"title": "How to", "link": "https://video.example/v", "channel": "Crew TV"}],
    "images": [{"title": "Excavator", "imageUrl": "https://img.example/1.jpg"}],
}


def test_parse_features_serper():
    found = parse_features("serper", SERPER_PAYLOAD)
    assert found["people_also_ask"] == [{"question": "What is dirt work?", "snippet": "Moving soil.", "url": "https://c.example/q"}]
    assert found["related_searches"] == ["dirt work software free", "excavation app"]
    assert found["answer_box"] == {"title": "Dirt work", "snippet": "Earthmoving", "url": "https://d.example/"}
    assert found["knowledge_panel"]["type"] == "Software company" and found["knowledge_panel"]["url"] == "https://hcss.example/"
    assert found["top_stories"][0]["source"] == "Daily"
    assert found["videos"][0] == {"title": "How to", "url": "https://video.example/v", "source": "Crew TV"}
    assert found["images"][0]["url"] == "https://img.example/1.jpg"
    assert found["sitelinks"] == [{"position": 1, "url": "https://a.example/", "links": [{"title": "Pricing", "url": "https://a.example/pricing"}]}]
    assert list(found) == [key for key in parse_features.__globals__["FEATURE_KEYS"] if key in found]


def test_parse_features_serpapi_and_dataforseo():
    serpapi = parse_features(
        "serpapi",
        {
            "organic_results": [{"position": 1, "link": "https://a.example/", "sitelinks": {"inline": [{"title": "Docs", "link": "https://a.example/docs"}]}}],
            "related_questions": [{"question": "Is it free?", "snippet": "Sometimes.", "link": "https://q.example/"}],
            "related_searches": [{"query": "cheap dirt software"}],
            "answer_box": {"answer": "42", "link": "https://ans.example/"},
            "knowledge_graph": {"title": "Crew Co", "type": "Company", "source": {"link": "https://crew.example/"}},
            "top_stories": [{"title": "T", "link": "https://t.example/"}],
            "inline_videos": [{"title": "V", "link": "https://v.example/", "platform": "YouTube"}],
            "inline_images": [{"title": "I", "original": "https://i.example/1.png"}],
        },
    )
    assert serpapi["people_also_ask"][0]["question"] == "Is it free?"
    assert serpapi["related_searches"] == ["cheap dirt software"]
    assert serpapi["answer_box"] == {"snippet": "42", "url": "https://ans.example/"}
    assert serpapi["knowledge_panel"]["url"] == "https://crew.example/"
    assert serpapi["videos"][0]["source"] == "YouTube"
    assert serpapi["sitelinks"][0]["links"][0]["url"] == "https://a.example/docs"
    dfs = parse_features(
        "dataforseo",
        {
            "tasks": [
                {
                    "result": [
                        {
                            "items": [
                                {"type": "organic", "rank_group": 1, "url": "https://a.example/", "links": [{"title": "Plans", "url": "https://a.example/plans"}]},
                                {"type": "people_also_ask", "items": [{"title": "How much?", "expanded_element": [{"description": "Ten.", "url": "https://p.example/"}]}]},
                                {"type": "related_searches", "items": ["dirt app", "grading app"]},
                                {"type": "featured_snippet", "title": "FS", "description": "Snippet", "url": "https://fs.example/"},
                                {"type": "knowledge_graph", "title": "KG", "sub_title": "Company"},
                                {"type": "top_stories", "items": [{"title": "S", "url": "https://s.example/", "source": "Paper"}]},
                                {"type": "video", "items": [{"title": "V", "url": "https://v.example/", "source": "YouTube"}]},
                                {"type": "images", "items": [{"alt": "pic", "url": "https://i.example/"}]},
                            ]
                        }
                    ]
                }
            ]
        },
    )
    assert dfs["people_also_ask"] == [{"question": "How much?", "snippet": "Ten.", "url": "https://p.example/"}]
    assert dfs["related_searches"] == ["dirt app", "grading app"]
    assert dfs["answer_box"]["title"] == "FS" and dfs["knowledge_panel"]["type"] == "Company"
    assert dfs["top_stories"][0]["source"] == "Paper" and dfs["images"][0]["title"] == "pic"
    assert dfs["sitelinks"][0]["links"][0]["title"] == "Plans"


def test_parse_features_is_defensive():
    assert parse_features("serper", None) == {}
    assert parse_features("brave", SERPER_PAYLOAD) == {}
    assert parse_features("serper", {"organic": "nope", "peopleAlsoAsk": [None, 3], "relatedSearches": "x"}) == {}
    long = parse_features("serper", {"relatedSearches": [{"query": "q" * 1000}] * 30, "answerBox": {"link": "javascript:alert(1)"}})
    assert len(long["related_searches"]) == 10 and len(long["related_searches"][0]) == 300
    assert "answer_box" not in long  # a non-http link is dropped, and an empty box is not stored


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.requests = 0

    def post(self, *args, **kwargs):
        self.requests += 1
        return _Response(self.payload)

    get = post


def test_providers_return_features_with_one_request():
    client = _Client(SERPER_PAYLOAD)
    hits, features = SerperProvider("fake-key", client=client).search_with_features("dirt work", 10)
    assert [hit.url for hit in hits] == ["https://a.example/", "https://b.example/"] and client.requests == 1
    assert features["related_searches"][0] == "dirt work software free"
    assert [hit.url for hit in SerperProvider("fake-key", client=_Client(SERPER_PAYLOAD)).search("dirt work", 10)] == ["https://a.example/", "https://b.example/"]
    _hits, found = SerpApiProvider("fake", client=_Client({"organic_results": [], "related_searches": [{"query": "x"}]})).search_with_features("q")
    assert found == {"related_searches": ["x"]}
    _hits, found = DataForSeoProvider("l", "p", client=_Client({"tasks": []})).search_with_features("q")
    assert found == {}

    class Bare(SerpProvider):
        def search(self, query, count=10):
            return []

    assert Bare().search_with_features("q") == ([], {})


# -- rank history ---------------------------------------------------------------------------------


def _history_project(tmp_path):
    project = _project(tmp_path / "projects", searches=["alpha", "beta", "gamma", "delta", "epsilon"])
    day1, day2, day3 = NOW - timedelta(days=14), NOW - timedelta(days=7), NOW
    rival = "https://rival.example/"
    _write_snapshot(project, "20261031T120000Z", day1, {
        "alpha": ["https://x.example/", rival, OURS],          # ours 3
        "beta": [OURS, rival],                                  # ours 1
        "gamma": ["https://x.example/"],                       # not ranked
        "delta": [OURS],
    })
    _write_snapshot(project, "20261107T120000Z", day2, {
        "alpha": [rival, "https://x.example/", "https://y.example/", "https://z.example/", OURS],  # ours 5
        "beta": [rival, "https://x.example/", OURS],                                          # ours 3
        "gamma": ["https://x.example/"],
        "delta": [OURS],
    })
    # a carried pull repeats day 2 and must not count as a check
    _write_snapshot(project, "20261110T120000Z", NOW - timedelta(days=4), {"alpha": [OURS]}, kind="carried", pulled=day2)
    _write_snapshot(project, "20261114T120000Z", day3, {
        "alpha": [OURS, rival],                       # up from 5 to 1
        "beta": ["https://x.example/", rival],        # lost
        "gamma": ["https://x.example/", OURS],        # new at 2
        "delta": [OURS],                              # same
        "epsilon": [rival],                           # first check
    }, features={"alpha": {"related_searches": ["alpha free"], "people_also_ask": [{"question": "Why alpha?"}]}})
    return project


def test_history_positions_and_changes(tmp_path):
    project = _history_project(tmp_path)
    meta = ws.WorkspaceMeta(competitors=["rival.example"])
    history = tracking.build_history(project, meta)
    rows = {row["search"]: row for row in history["searches"]}
    assert history["checks"] == 3
    assert [point["position"] for point in rows["alpha"]["points"]] == [3, 5, 1]
    assert rows["alpha"]["change"] == "up" and rows["alpha"]["delta"] == 4 and rows["alpha"]["url"] == OURS
    assert rows["beta"]["change"] == "lost" and rows["beta"]["position"] is None and rows["beta"]["previous"] == 3
    assert rows["gamma"]["change"] == "new" and rows["gamma"]["position"] == 2
    assert rows["delta"]["change"] == "same" and rows["delta"]["delta"] == 0
    assert rows["epsilon"]["change"] == "first" and rows["epsilon"]["position"] is None
    assert history["changes"] == {"up": 1, "down": 0, "new": 1, "lost": 1, "same": 1, "none": 0, "first": 1}
    assert [point["position"] for point in rows["alpha"]["competitors"]["rival.example"]] == [2, 1, 2]
    assert rows["alpha"]["features"]["related_searches"] == ["alpha free"]
    assert history["competitors"] == ["rival.example"]
    assert [row["ranked"] for row in history["trend"]] == [3, 3, 3]
    assert history["trend"][-1]["average"] == round((1 + 2 + 1) / 3, 1)
    assert history["schedule"] == "weekly"
    assert history["next_check_at"] == (NOW + timedelta(days=7)).isoformat(timespec="seconds")


def test_history_down_and_none_and_depth(tmp_path):
    project = _project(tmp_path / "projects", searches=["a", "b", "c"])
    deep = [f"https://n{index}.example/" for index in range(11)] + [OURS]  # ours at 12: outside the top 10
    _write_snapshot(project, "20261101T000000Z", NOW - timedelta(days=2), {"a": [OURS], "b": ["https://x.example/"], "c": [OURS]})
    _write_snapshot(project, "20261102T000000Z", NOW - timedelta(days=1), {"a": ["https://x.example/", OURS], "b": ["https://x.example/"], "c": deep})
    rows = {row["search"]: row for row in tracking.build_history(project, ws.WorkspaceMeta())["searches"]}
    assert rows["a"]["change"] == "down" and rows["a"]["delta"] == -1
    assert rows["b"]["change"] == "none"
    assert rows["c"]["change"] == "lost"


def test_history_with_no_pulls_and_competitor_fallback(tmp_path):
    project = _project(tmp_path / "projects", searches=["alpha"])
    empty = tracking.build_history(project, ws.WorkspaceMeta())
    assert empty["checks"] == 0 and empty["searches"][0]["change"] == "none" and empty["next_check_at"] is None
    project = _history_project(tmp_path / "other")
    picked = tracking.build_history(project, ws.WorkspaceMeta())
    assert "rival.example" in picked["competitors"] and "dirtcrew.example" not in " ".join(picked["competitors"])


def test_compact_history_for_the_report(tmp_path):
    project = _history_project(tmp_path)
    report: dict = {"kept": True}
    tracking.extend_report(report, project=project, meta=ws.WorkspaceMeta(), database=None, ctx=None, ui=None)
    assert report["kept"] is True
    compact = report["history"]
    alpha = next(row for row in compact["searches"] if row["search"] == "alpha")
    assert alpha["spark"] == [3, 5, 1]
    assert alpha["features"] == ["people_also_ask", "related_searches"]
    assert "points" not in alpha and compact["changes"]["up"] == 1
    json.dumps(report)  # serializable


# -- schedules and the cron ----------------------------------------------------------------------


class FakeJobs:
    def __init__(self):
        self.locks: dict[str, threading.Lock] = {}

    def project_lock(self, slug):
        return self.locks.setdefault(slug, threading.Lock())


def _ui(tmp_path, **keys):
    return SimpleNamespace(
        root=tmp_path / "projects",
        settings=_settings(tmp_path, **keys),
        public_demo=False,
        pull_options={},
        jobs=FakeJobs(),
        cloud=None,
        csrf="test-csrf",
        scrub=lambda text: text.replace("sk-secret", "[redacted]"),
    )


def test_schedule_settings(tmp_path):
    project = _project(tmp_path / "projects")
    assert tracking.load_schedule(project) == "weekly"
    assert tracking.save_schedule(project, " Daily ") == "daily"
    assert tracking.load_schedule(project) == "daily"
    with pytest.raises(ValueError):
        tracking.save_schedule(project, "hourly")
    (Path(project.dir) / "plugins" / "tracking" / "settings.json").write_text('{"schedule": "bogus"}')
    assert tracking.load_schedule(project) == "weekly"


def test_due_selection_oldest_first(tmp_path):
    root = tmp_path / "projects"
    never = _project(root, slug="never")
    old = _project(root, slug="old")
    _write_snapshot(old, "20261001T000000Z", NOW - timedelta(days=30), {SEARCHES[0]: [OURS]})
    recent = _project(root, slug="recent")
    _write_snapshot(recent, "20261110T000000Z", NOW - timedelta(days=4), {SEARCHES[0]: [OURS]})
    week = _project(root, slug="week")
    _write_snapshot(week, "20261107T000000Z", NOW - timedelta(days=7) + timedelta(hours=1), {SEARCHES[0]: [OURS]})  # within slack
    daily = _project(root, slug="daily")
    tracking.save_schedule(daily, "daily")
    _write_snapshot(daily, "20261113T000000Z", NOW - timedelta(hours=23), {SEARCHES[0]: [OURS]})
    off = _project(root, slug="off")
    tracking.save_schedule(off, "off")
    demo = _project(root, slug="demo")
    meta = ws.load_meta(demo)
    meta.dataset = ws.DATASET_SYNTHETIC
    ws.save_meta(demo, meta)
    tried = _project(root, slug="tried")
    tracking.save_state(tried, {"last_attempt_at": (NOW - timedelta(hours=3)).isoformat()})
    carried_only = _project(root, slug="carried")
    _write_snapshot(carried_only, "20261113T000000Z", NOW - timedelta(hours=1), {SEARCHES[0]: [OURS]}, kind="carried")

    due, skipped = tracking.due_projects(root, now=NOW)
    assert [item.slug for item in due] == ["carried", "never", "old", "week", "daily"]
    reasons = {row["slug"]: row["reason"] for row in skipped}
    assert reasons["recent"] == "not due yet"
    assert reasons["off"] == "re-checks are off"
    assert "synthetic" in reasons["demo"]
    assert reasons["tried"] == "already tried today"
    assert never and due[1].last_check_at is None


def test_cron_auth():
    assert not tracking.cron_authorized("Bearer abc", secret="")
    assert not tracking.cron_authorized(None, secret="abc")
    assert not tracking.cron_authorized("Bearer abd", secret="abc")
    assert not tracking.cron_authorized("abc", secret="abc")
    assert tracking.cron_authorized("Bearer abc", secret="abc")


def test_recheck_runs_due_projects_within_cap_and_time(tmp_path):
    ui = _ui(tmp_path, serper_api_key="fake-serper")
    root = ui.root
    first = _project(root, slug="a-first", searches=["one", "two"])
    _write_snapshot(first, "20261001T000000Z", NOW - timedelta(days=40), {"one": [OURS]})
    big = _project(root, slug="b-big", searches=[f"s{index}" for index in range(5)])
    _write_snapshot(big, "20261002T000000Z", NOW - timedelta(days=39), {"s0": [OURS]})
    slow = _project(root, slug="c-slow", searches=["one"])
    _write_snapshot(slow, "20261003T000000Z", NOW - timedelta(days=38), {"one": [OURS]})
    budget = tracking.install_budget(ui)
    budget._cap = 4
    budget.clock = Clock()
    ran: list[str] = []
    ticks = iter([0.0, 1.0, 2.0, 50.0, 300.0, 300.0, 300.0])

    def run_pull(ui_, project):
        ran.append(project.slug)
        for search in project.searches:
            assert budget.claim(search) == "call"
        return {"pull_id": "x", "searches_with_results": len(project.searches), "secret": "no"}

    result = tracking.recheck(ui, now=NOW, run_pull=run_pull, monotonic=lambda: next(ticks), limit_seconds=60)
    assert ran == ["a-first"]
    assert result["ran"][0]["ok"] and result["ran"][0]["summary"] == {"pull_id": "x", "searches_with_results": 2}
    deferred = {row["slug"]: row["reason"] for row in result["deferred"]}
    assert deferred["b-big"] == "needs 5 searches, 2 left this month"
    assert deferred["c-slow"].startswith("out of time")
    assert result["used"] == 2 and result["cap"] == 4
    assert tracking.load_state(first)["last_result"] == "ok"
    assert "last_attempt_at" not in tracking.load_state(big)  # deferred projects are tried again next run


def test_recheck_records_failures_and_needs_a_provider(tmp_path):
    ui = _ui(tmp_path)
    _project(ui.root, slug="p")
    result = tracking.recheck(ui, now=NOW, run_pull=lambda *_: pytest.fail("must not pull"))
    assert "SERPER_API_KEY" in result["note"] and result["ran"] == []
    ui = _ui(tmp_path, serper_api_key="fake-serper")

    def boom(ui_, project):
        raise RuntimeError("provider said sk-secret is wrong")

    result = tracking.recheck(ui, now=NOW, run_pull=boom, limit_seconds=600)
    assert result["ran"][0]["ok"] is False and "[redacted]" in result["ran"][0]["error"]
    state = tracking.load_state(proj.load_project("p", root=ui.root))
    assert state["last_result"] == "error" and "sk-secret" not in state["last_error"]
    again = tracking.recheck(ui, now=NOW + timedelta(hours=1), run_pull=boom, limit_seconds=600)
    assert again["ran"] == [] and again["skipped"][0]["reason"] == "already tried today"


def test_recheck_skips_a_project_whose_pull_is_running(tmp_path):
    ui = _ui(tmp_path, serper_api_key="fake-serper")
    _project(ui.root, slug="busy")
    lock = ui.jobs.project_lock("busy")
    lock.acquire()
    try:
        result = tracking.recheck(ui, now=NOW, run_pull=lambda *_: pytest.fail("must not pull"), limit_seconds=600)
    finally:
        lock.release()
    assert result["deferred"] == [{"slug": "busy", "reason": "a pull is already running"}]


# -- routes ---------------------------------------------------------------------------------------


def _client(tmp_path, **keys):
    from aeo.ranksim.serve import UiState

    ui = UiState(settings=_settings(tmp_path, **keys), database=f"sqlite:///{tmp_path / 'rank.db'}", projects_root=tmp_path / "projects", csrf_token="test-csrf")
    app = FastAPI()
    tracking.register(app, ui)
    return TestClient(app), ui


def test_status_route(tmp_path, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client, ui = _client(tmp_path)
    data = client.get("/api/v1/tracking/status").json()
    assert data["configured"] is False and data["provider"] is None
    assert data["used"] == 0 and data["cap"] == 500 and data["cron"]["secret_set"] is False
    client, ui = _client(tmp_path, serper_api_key="sk-secret-serper")
    assert isinstance(ui.pull_options["budget"], tracking.Budget)
    ui.pull_options["budget"].claim("x")
    response = client.get("/api/v1/tracking/status")
    data = response.json()
    assert data["provider"] == "Serper" and data["configured"] and data["used"] == 1 and data["remaining"] == 499
    assert "sk-secret" not in response.text
    both = tracking.provider_status(_settings(tmp_path, serper_api_key="a", serpapi_key="b"))
    assert both["provider"] == "SerpAPI"  # the one resolve_serp really uses
    assert tracking.provider_status(_settings(tmp_path, serper_api_key="a"), public_demo=True)["configured"] is False


def test_history_and_schedule_routes(tmp_path):
    client, ui = _client(tmp_path)
    _history_project(tmp_path)
    data = client.get("/api/v1/projects/dirt-crew/history").json()
    assert data["checks"] == 3 and {row["search"] for row in data["searches"]} >= {"alpha", "beta"}
    assert client.get("/api/v1/projects/nope/history").status_code == 404
    assert client.get("/api/v1/projects/dirt-crew/tracking").json()["schedule"] == "weekly"
    assert client.post("/api/v1/projects/dirt-crew/tracking", json={"schedule": "daily"}).status_code == 403  # no CSRF header
    response = client.post("/api/v1/projects/dirt-crew/tracking", json={"schedule": "daily"}, headers={"x-ranksim-csrf": "test-csrf"})
    assert response.status_code == 200 and response.json()["schedule"] == "daily"
    bad = client.post("/api/v1/projects/dirt-crew/tracking", json={"schedule": "hourly"}, headers={"x-ranksim-csrf": "test-csrf"})
    assert bad.status_code == 400


def test_cron_route_auth(tmp_path, monkeypatch):
    client, ui = _client(tmp_path)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get(tracking.CRON_PATH, headers={"authorization": "Bearer "}).status_code == 401
    monkeypatch.setenv("CRON_SECRET", "cron-test-secret")
    assert client.get(tracking.CRON_PATH).status_code == 401
    assert client.get(tracking.CRON_PATH, headers={"authorization": "Bearer wrong"}).status_code == 401
    ok = client.get(tracking.CRON_PATH, headers={"authorization": "Bearer cron-test-secret"})
    assert ok.status_code == 200 and "SERPER_API_KEY" in ok.json()["note"]
    ui.public_demo = True
    assert client.get(tracking.CRON_PATH, headers={"authorization": "Bearer cron-test-secret"}).status_code == 403


def test_cron_path_is_open_to_the_login_check(tmp_path):
    from aeo.ranksim import studio

    _client(tmp_path)
    assert studio._is_open(tracking.CRON_PATH)
