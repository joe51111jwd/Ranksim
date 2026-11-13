"""Free off-page sources. A signup key is not requested, and a missing domain stays missing."""

from __future__ import annotations

import httpx

from aeo.ranksim.offpage import load_majestic_million, load_tranco_popularity, open_pagerank_scores, signal_tables


def test_majestic_million_uses_referring_subnets_and_skips_unknown_domains(tmp_path):
    path = tmp_path / "majestic_million.csv"
    path.write_text(
        "GlobalRank,TldRank,Domain,TLD,RefSubNets,RefIPs\n"
        "1,1,example.com,com,100,80\n"
        "2,2,other.com,com,0,0\n",
        encoding="utf-8",
    )
    ranks = load_majestic_million(path)
    assert "example.com" in ranks
    assert ranks["example.com"] > ranks["other.com"]
    assert "missing.com" not in ranks


def test_tranco_omits_a_domain_that_is_not_listed(tmp_path):
    path = tmp_path / "top-1m.csv"
    path.write_text("1,example.com\n2,other.com\n", encoding="utf-8")
    ranks = load_tranco_popularity(path)
    assert ranks["example.com"] > ranks["other.com"]
    assert "missing.com" not in ranks


def test_open_pagerank_does_not_call_without_a_key():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"response": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert open_pagerank_scores(["example.com"], None, client) == {}
    assert calls == []
    found = open_pagerank_scores(
        ["example.com"],
        "opr-test-key",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"response": [{"domain": "example.com", "page_rank_decimal": 4.2}]},
                )
            )
        ),
    )
    assert found == {"example.com": 4.2}
    assert "opr-test-key" not in str(found)
    tables = signal_tables(open_pagerank=found)
    assert tables["open_pagerank"]["example.com"] == 4.2


def test_open_pagerank_sends_the_key_as_a_header_not_a_query_param():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("api-opr") or ""
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"response": [{"domain": "example.com", "page_rank_decimal": 1}]})

    open_pagerank_scores(["example.com"], "opr-test-key", httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["header"] == "opr-test-key"
    assert "opr-test-key" not in seen["url"]
