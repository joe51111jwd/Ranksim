"""A failed SERP collection is never an empty result."""

import httpx
import pytest

from aeo.ranksim.keywords import Keyword
from aeo.ranksim.loop import collect_corpus, track_ranks
from aeo.ranksim.serp import (
    DataForSeoProvider,
    SerpApiProvider,
    SerpCollectError,
    SerperProvider,
    SerpHit,
    SerpProvider,
    parse_dataforseo,
    parse_serpapi,
    parse_serper,
)
from aeo.ranksim.store import RsRankCheck, open_session, replace_serp, serp_for_query


def test_error_payloads_raise_instead_of_returning_nothing():
    with pytest.raises(SerpCollectError, match="40100"):
        parse_dataforseo({"status_code": 20000, "tasks": [{"status_code": 40100, "status_message": "unauthorized", "result": None}]}, 10)
    with pytest.raises(SerpCollectError, match="50000"):
        parse_dataforseo({"status_code": 50000, "status_message": "internal", "tasks": []}, 10)
    with pytest.raises(SerpCollectError, match="Invalid API key"):
        parse_serpapi({"error": "Invalid API key."}, 10)
    assert parse_serpapi({"error": "Google hasn't returned any results for this query."}, 10) == []
    with pytest.raises(SerpCollectError):
        parse_serper({"message": "Unauthorized."}, 10)


def test_raw_position_survives_organic_renumbering():
    hits = parse_serpapi({"organic_results": [{"link": "https://a.example/", "position": 1}, {"link": "https://b.example/", "position": 4}]}, 10)
    assert [(hit.position, hit.raw_position) for hit in hits] == [(1, 1), (2, 4)]


def test_transient_http_errors_are_retried(monkeypatch):
    import aeo.ranksim.serp as serp_module

    monkeypatch.setattr(serp_module.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"organic": [{"link": "https://a.example/", "position": 1}]})

    provider = SerperProvider("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert [hit.url for hit in provider.search("q")] == ["https://a.example/"]
    assert calls["n"] == 3

    def forbidden(request):
        return httpx.Response(403, json={})

    provider = SerpApiProvider("k", client=httpx.Client(transport=httpx.MockTransport(forbidden)))
    with pytest.raises(SerpCollectError, match="403"):
        provider.search("q")


class Failing(SerpProvider):
    name = "failing"
    is_google = True

    def search(self, query, count=10):
        raise SerpCollectError("dataforseo task status 40100: unauthorized")


def test_collect_keeps_the_stored_serp_and_track_records_a_failure(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    kept = [SerpHit(1, "https://a.example/", "A", "", "serper", False, True)]
    replace_serp(session, "dirt work software", kept)
    keyword = Keyword("dirt work software", "commercial", "core", "https://www.smartcrewhq.com/", True, "")
    result = collect_corpus(session, [keyword], Failing(), lambda url: None)
    assert [row.url for row in serp_for_query(session, "dirt work software")] == ["https://a.example/"]
    assert any("collection failed" in warning for warning in result.warnings)
    rows = track_ranks(session, [keyword], Failing(), checked_on="2026-11-05")
    assert rows[0].status == "error" and "40100" in rows[0].error
    stored = session.query(RsRankCheck).one()
    assert stored.status == "error" and stored.our_position is None
    session.close()
