"""Keyword volume and difficulty. Fixtures only. No network."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from aeo.ranksim import cli
from aeo.ranksim.kwresearch.difficulty import load_composition, score_difficulty
from aeo.ranksim.kwresearch.models import VolumeRecord, now_iso
from aeo.ranksim.kwresearch.providers import (
    PaidConfig,
    parse_seodata_api,
    parse_seodata_html,
    parse_semrush_overview,
    semrush_record,
)
from aeo.ranksim.kwresearch.service import (
    import_volumes,
    keyword_difficulty,
    keyword_report,
    keyword_volume,
    read_keyword_list,
)
from aeo.ranksim.serve import create_app
from tests.conftest import make_settings

FIXTURES = Path("tests/fixtures/keyword-providers")
SEED = Path("tests/fixtures/keyword-seed")
PRIORITY = [
    "busybusy",
    "hcss",
    "raken",
    "construction time tracking software",
    "construction scheduling software",
    "workyard",
    "b2w",
    "agtek",
    "quoteiq",
    "heavy job",
    "trimble viewpoint",
    "assignar",
    "crew scheduling software",
    "job costing software",
    "heavybid",
    "hcss alternative",
    "best dirt work software",
]
PRIORITY_VOLUME = {
    "busybusy": 9900,
    "hcss": 8100,
    "raken": 8100,
    "construction time tracking software": 3600,
    "construction scheduling software": 2900,
    "workyard": 2400,
    "b2w": 1900,
    "agtek": 1600,
    "quoteiq": 1600,
    "heavy job": 1000,
    "trimble viewpoint": 880,
    "assignar": 720,
    "crew scheduling software": 590,
    "job costing software": 590,
    "heavybid": 590,
    "hcss alternative": None,
    "best dirt work software": None,
}


def test_seodata_api_parses_the_recorded_bucket_and_404_is_not_zero():
    payload = json.loads((FIXTURES / "seodata-busybusy.json").read_text())
    record = parse_seodata_api(payload, 200, "busybusy")
    assert record is not None and record.usable()
    assert record.us_monthly_volume == 9900
    assert record.volume_display == "9,900"
    assert record.cpc_usd == 7.54
    assert record.competition == "0.43"
    assert record.data_date == "2026-05-18"
    assert record.source_url == "https://www.seodata.dev/keyword/busybusy"
    assert record.provider == "seodata.dev"

    missing = json.loads((FIXTURES / "seodata-404.json").read_text())
    assert parse_seodata_api(missing, 404, "hcss alternative") is None
    page = (FIXTURES / "seodata-busybusy.html").read_text()
    html_record = parse_seodata_html(page, 200, "busybusy")
    assert html_record is not None
    assert html_record.us_monthly_volume == 9900 and html_record.data_date == "2026-05-18"
    assert parse_seodata_html((FIXTURES / "seodata-404.html").read_text(), 404, "hcss alternative") is None


def test_semrush_parser_reads_the_us_table_and_stops_on_captcha():
    page = (FIXTURES / "semrush-procore-escaped.html").read_text()
    table = parse_semrush_overview(page, domain="procore.com", url="https://www.semrush.com/website/procore.com/overview/")
    assert table.status == "ok"
    assert table.data_date == "2026-11-11"
    assert table.series_month == "2026-10-15"
    record = semrush_record("procore login", table)
    assert record is not None
    assert record.us_monthly_volume == 110000
    assert record.volume_display == "110,000"
    assert record.cpc_usd == 9.19
    assert record.provider == "semrush"
    assert record.source_url.endswith("/procore.com/overview/")
    plain = parse_semrush_overview(
        (FIXTURES / "semrush-procore.html").read_text(),
        domain="procore.com",
        url="https://www.semrush.com/website/procore.com/overview/",
    )
    assert plain.rows["procore"]["volume"] == 301000
    blocked = parse_semrush_overview(
        (FIXTURES / "semrush-captcha.html").read_text(),
        domain="procore.com",
        url="https://www.semrush.com/website/procore.com/overview/",
        status_code=200,
    )
    assert blocked.status == "captcha"
    assert semrush_record("procore", blocked) is None


def test_provider_chain_order_and_paid_keys_stay_out_of_the_result(tmp_path):
    busy = json.loads((FIXTURES / "seodata-busybusy.json").read_text())
    semrush = (FIXTURES / "semrush-procore-escaped.html").read_text()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "api.seodata.dev":
            query = request.url.params["q"]
            if query == "busybusy":
                return httpx.Response(200, json=busy)
            return httpx.Response(404, json={"keyword": query, "volume": 0, "cpc": 0, "competition": 0, "country": "us", "cached": False})
        if request.url.host == "www.semrush.com":
            return httpx.Response(200, text=semrush)
        if request.url.host == "api.dataforseo.com":
            raise AssertionError("DataForSEO should not be called when no keys are set")
        return httpx.Response(404, text="no")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = keyword_volume(
        ["busybusy", "procore login"],
        tmp_path / "kw.sqlite",
        client=client,
        delay=0,
        retries=1,
        paid=PaidConfig(),
    )
    by_keyword = {row.keyword: row for row in rows}
    assert by_keyword["busybusy"].us_monthly_volume == 9900
    assert by_keyword["busybusy"].data_date == "2026-05-18"
    assert by_keyword["procore login"].us_monthly_volume == 110000
    assert by_keyword["procore login"].provider == "semrush"
    assert calls[0] == "api.seodata.dev"
    assert "www.semrush.com" in calls
    assert calls.count("api.seodata.dev") == 2
    assert calls.count("www.semrush.com") == 1


def test_dataforseo_is_used_only_when_configured_and_secrets_are_not_stored(tmp_path):
    secret_user = "user-secret-login"
    secret_pass = "pass-secret-value"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.seodata.dev":
            return httpx.Response(404, json={"keyword": "zzyyxx", "volume": 0, "cached": False, "country": "us"})
        if request.url.host == "www.semrush.com":
            return httpx.Response(404, text="<html><title>missing</title></html>")
        if request.url.host == "api.dataforseo.com":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={
                "tasks": [{
                    "status_code": 20000,
                    "result": [{
                        "keyword": "zzyyxx",
                        "search_volume": 480,
                        "competition": "LOW",
                        "competition_index": 12,
                        "cpc": 3.5,
                        "monthly_searches": [{"year": 2026, "month": 10, "search_volume": 480}],
                    }],
                }]
            })
        raise AssertionError(request.url.host)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    skipped = keyword_volume(["zzyyxx"], tmp_path / "a.sqlite", client=client, delay=0, retries=1, paid=PaidConfig())
    assert skipped[0].us_monthly_volume is None
    assert "not set" in skipped[0].blank_reason

    client = httpx.Client(transport=httpx.MockTransport(handler))
    found = keyword_volume(
        ["zzyyxx"],
        tmp_path / "b.sqlite",
        client=client,
        delay=0,
        retries=1,
        paid=PaidConfig(dataforseo_login=secret_user, dataforseo_password=secret_pass),
    )
    text = json.dumps(found[0].to_dict())
    assert found[0].us_monthly_volume == 480
    assert found[0].data_date == "2026-10"
    assert found[0].provider == "dataforseo"
    assert secret_user not in text and secret_pass not in text


def test_google_ads_historical_metrics_parser_via_the_chain(tmp_path):
    token = "ya29-access-token-secret"
    developer = "dev-token-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.seodata.dev":
            return httpx.Response(404, json={"keyword": "busybusy", "volume": 0, "cached": False})
        if request.url.host == "www.semrush.com":
            return httpx.Response(200, text="<html>no table</html>")
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": token})
        if request.url.host == "googleads.googleapis.com":
            assert developer not in str(request.url)
            assert request.headers["developer-token"] == developer
            return httpx.Response(200, json={
                "results": [{
                    "text": "busybusy",
                    "keywordMetrics": {
                        "competition": "MEDIUM",
                        "competitionIndex": "43",
                        "avgMonthlySearches": "9900",
                        "averageCpcMicros": "7540000",
                        "monthlySearchVolumes": [{"month": "OCTOBER", "year": "2026", "monthlySearches": "8100"}],
                    },
                }]
            })
        raise AssertionError(request.url.host)

    paid = PaidConfig(
        google_ads_developer_token=developer,
        google_ads_client_id="client-id-secret",
        google_ads_client_secret="client-secret-value",
        google_ads_refresh_token="refresh-token-secret",
        google_ads_customer_id="123-456-7890",
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    row = keyword_volume(["busybusy"], tmp_path / "g.sqlite", client=client, delay=0, retries=1, paid=paid)[0]
    blob = json.dumps(row.to_dict())
    assert row.us_monthly_volume == 9900
    assert row.data_date == "2026-10"
    assert row.cpc_usd == 7.54
    assert row.competition == "MEDIUM"
    assert row.competition_index == 43
    assert row.provider == "google_ads"
    for secret in (token, developer, "client-secret-value", "refresh-token-secret", "1234567890"):
        assert secret not in blob


def test_import_keeps_source_and_report_lists_the_priority_keywords(tmp_path, capsys):
    cache = tmp_path / "seed.sqlite"
    stored, blanks = import_volumes(SEED / "volumes.csv", cache)
    assert stored == 200
    assert blanks == 75
    rows = keyword_report(PRIORITY, cache, paid=PaidConfig())
    by_keyword = {row["keyword"]: row for row in rows}
    assert list(by_keyword) == PRIORITY
    for keyword, volume in PRIORITY_VOLUME.items():
        row = by_keyword[keyword]
        assert row["us_monthly_volume"] == volume
        if volume is None:
            assert row["volume_display"] == ""
            assert row["blank_reason"]
        else:
            assert row["provider"]
            assert row["source_url"].startswith("http")
            assert row["data_date"]
            assert row["volume_display"] == f"{volume:,}"
    assert by_keyword["busybusy"]["source_url"] == "https://www.seodata.dev/keyword/busybusy"
    assert by_keyword["busybusy"]["data_date"] == "2026-05-18"
    assert by_keyword["busybusy"]["difficulty"] is None
    assert by_keyword["hcss alternative"]["difficulty_reason"] == "difficulty not run"

    assert cli.main(["keywords", "report", "--file", str(SEED / "priority.txt"), "--cache", str(cache), "--json"]) is None
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed[0]["keyword"] == "busybusy"
    assert parsed[0]["us_monthly_volume"] == 9900
    assert parsed[0]["volume_display"] == "9,900"


def test_keywords_command_without_a_subcommand_still_writes_the_gap_list(monkeypatch, capsys):
    monkeypatch.setattr(cli, "write_keywords_csv", lambda: "smartcrew/keywords.csv")
    monkeypatch.setattr(cli, "gap_summary", lambda: {"queries": 3, "with_page": 2, "gaps": 1})
    cli.main(["keywords"])
    text = capsys.readouterr().out
    assert "smartcrew/keywords.csv" in text
    assert "3 queries" in text


def test_refresh_keeps_an_imported_number_when_live_sources_miss(tmp_path):
    cache = tmp_path / "kw.sqlite"
    import_volumes(SEED / "volumes.csv", cache)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.seodata.dev":
            return httpx.Response(404, json={"keyword": request.url.params["q"], "volume": 0, "cached": False})
        return httpx.Response(200, text="<html>no table</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    row = keyword_volume(["busybusy"], cache, refresh=True, client=client, delay=0, retries=1, paid=PaidConfig())[0]
    assert row.us_monthly_volume == 9900
    assert row.provider == "seodata.dev"
    assert "Refresh kept this stored volume" in row.notes


def test_difficulty_renormalizes_when_factors_are_missing_and_ai_overview_is_unknown():
    organic = [
        {"url": "https://example.com/other", "title": "Something else"},
        {"url": "https://niche.example/blog/notes", "title": "Notes"},
    ]
    result = score_difficulty(
        "zzzz unknown phrase",
        organic=organic,
        payload={"answerBox": {"title": "snippet"}},
        volume=None,
        tranco=None,
        majestic=None,
    )
    by_name = {factor.name: factor for factor in result.factors}
    assert by_name["ai_overview"].status == "unknown"
    assert by_name["domain_authority"].status == "missing"
    assert by_name["cpc"].status == "missing"
    assert by_name["ads_competition"].status == "missing"
    assert by_name["serp_composition"].status == "present"
    assert by_name["exact_match"].status == "present"
    assert by_name["serp_composition"].subscore == 25
    assert by_name["exact_match"].subscore == 0
    present_weight = 0.30 + 0.15
    assert result.coverage == round(present_weight, 4)
    assert math.isclose(by_name["serp_composition"].weight_used, 0.30 / present_weight)
    assert math.isclose(by_name["exact_match"].weight_used, 0.15 / present_weight)
    assert result.score == 17
    assert result.kind == "heuristic"
    assert "not a Google prediction" in result.label


def test_difficulty_uses_authority_exact_match_cpc_and_a_real_ai_overview():
    organic = [
        {"url": "https://google.com/", "title": "Google"},
        {"url": "https://www.g2.com/products/hcss", "title": "HCSS reviews"},
        {"url": "https://example.com/construction-time-tracking-software", "title": "Construction Time Tracking Software"},
        {"url": "https://smallco.example/blog/other", "title": "Other"},
    ]
    payload = {"organic": [], "aiOverview": {"sources": [{"link": "https://www.forbes.com/sites/example"}]}}
    volume = VolumeRecord(
        keyword="construction time tracking software",
        cpc_usd=7.54,
        competition="0.43",
        provider="seodata.dev",
    )
    result = score_difficulty(
        "construction time tracking software",
        organic=organic,
        payload=payload,
        volume=volume,
        tranco={"google.com": 1, "g2.com": 500},
        majestic=None,
        composition=load_composition(),
    )
    by_name = {factor.name: factor for factor in result.factors}
    assert by_name["serp_composition"].raw.startswith("brand_homepages=1, review_ugc=1, niche=2")
    assert by_name["serp_composition"].subscore == 55
    assert "not listed" in by_name["domain_authority"].raw
    assert "example.com" in by_name["domain_authority"].raw
    assert by_name["exact_match"].raw == "1 of 4"
    assert by_name["ai_overview"].status == "present"
    assert "forbes.com" in by_name["ai_overview"].raw
    assert by_name["cpc"].status == "present"
    assert by_name["ads_competition"].subscore == 43
    assert result.coverage == 1
    expected_cpc = 100.0 * math.log1p(7.54) / math.log1p(50.0)
    authority = by_name["domain_authority"].subscore
    weighted = (55 * 0.30) + (authority * 0.25) + (25 * 0.15) + ((55 + 8) * 0.10) + (expected_cpc * 0.10) + (43 * 0.10)
    assert result.score == int(math.floor(weighted + 0.5))
    assert authority == 54


def test_empty_ai_overview_is_present_and_a_missing_field_is_unknown():
    empty = score_difficulty("q", organic=[], payload={"aiOverview": None}, volume=None, tranco={}, majestic={})
    assert {factor.name: factor.status for factor in empty.factors}["ai_overview"] == "present"
    unknown = score_difficulty("q", organic=None, payload={}, volume=None, tranco=None, majestic=None)
    assert {factor.name: factor.status for factor in unknown.factors}["ai_overview"] == "unknown"
    assert unknown.score is None


def test_serp_cache_makes_the_second_difficulty_run_free(tmp_path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert "X-API-KEY" in request.headers
        return httpx.Response(200, json={
            "organic": [{"position": 1, "title": "HCSS", "link": "https://www.hcss.com/", "snippet": ""}],
            "credits": 1,
        })

    cache = tmp_path / "kw.sqlite"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = keyword_difficulty(
        ["hcss"],
        cache,
        api_key="serper-test-key",
        credit_cap=5,
        client=client,
        today="2026-11-14",
        fetch_authority=False,
    )
    assert calls["n"] == 1
    assert first[0].credits == 1
    assert first[0].serp_cached is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("second run called Serper")

    again = keyword_difficulty(
        ["hcss"],
        cache,
        api_key="serper-test-key",
        credit_cap=5,
        client=httpx.Client(transport=httpx.MockTransport(boom)),
        today="2026-11-14",
        fetch_authority=False,
    )
    assert again[0].serp_cached is True
    assert again[0].credits == 0
    assert again[0].score is not None


def test_credit_cap_blocks_the_serper_call(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("cap is 0")

    rows = keyword_difficulty(
        ["hcss"],
        tmp_path / "kw.sqlite",
        api_key="serper-test-key",
        credit_cap=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        today="2026-11-14",
        fetch_authority=False,
    )
    assert "cap" in rows[0].reason.lower()


def test_keywords_page_uses_the_imported_cache(tmp_path):
    app = create_app(
        settings=make_settings(tmp_path),
        database=f"sqlite:///{tmp_path / 'aeo.db'}",
        projects_root=tmp_path / "projects",
    )
    client = TestClient(app)
    page = client.get("/keywords")
    assert page.status_code == 200
    assert "Volume" in page.text and "Difficulty" in page.text
    token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    uploaded = client.post(
        "/keywords/import",
        data={"csrf": token, "project": "Seed"},
        files={"csv": ("volumes.csv", (SEED / "volumes.csv").read_bytes(), "text/csv")},
    )
    assert uploaded.status_code == 200
    assert "Imported 200" in uploaded.text
    shown = client.post(
        "/keywords",
        data={"csrf": token, "project": "Seed", "action": "volume", "keywords": "busybusy\nhcss alternative"},
    )
    assert shown.status_code == 200
    assert "9,900" in shown.text
    assert "seodata.dev" in shown.text
    api = client.post("/api/keywords/report", json={"keywords": ["busybusy", "hcss alternative"], "project": "Seed"})
    body = api.json()
    assert body["keywords"][0]["us_monthly_volume"] == 9900
    assert body["keywords"][1]["us_monthly_volume"] is None


def test_read_keyword_list_accepts_commas_and_a_file(tmp_path):
    path = tmp_path / "seeds.txt"
    path.write_text("# comment\nbusybusy\n\nhcss\n")
    assert read_keyword_list(path=str(path)) == ["busybusy", "hcss"]
    assert read_keyword_list(keywords="busybusy, hcss") == ["busybusy", "hcss"]


def test_stale_live_cache_is_not_treated_as_fresh(tmp_path):
    from aeo.ranksim.kwresearch.store import KeywordStore

    cache = tmp_path / "kw.sqlite"
    store = KeywordStore(cache)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    store.put_volume(VolumeRecord(
        keyword="busybusy",
        us_monthly_volume=1,
        volume_display="1",
        provider="seodata.dev",
        volume_source="seodata.dev, Google Ads API data, US",
        source_url="https://www.seodata.dev/keyword/busybusy",
        data_date="2020-01-01",
        fetched_at=old,
        sticky=False,
    ))
    store.close()
    payload = json.loads((FIXTURES / "seodata-busybusy.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    row = keyword_volume(
        ["busybusy"],
        cache,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        delay=0,
        retries=1,
        paid=PaidConfig(),
    )[0]
    assert row.us_monthly_volume == 9900
    assert row.fetched_at != old


def _zip_bytes(text: str) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("top-1m.csv", text)
    return buffer.getvalue()


def test_authority_lists_download_once_and_name_their_dates(tmp_path):
    from aeo.ranksim.kwresearch.authority_cache import load_authority_sources

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if request.url.host == "tranco-list.eu":
            return httpx.Response(
                200,
                content=_zip_bytes("1,google.com\n4,busybusy.com\n"),
                headers={"last-modified": "Fri, 13 Nov 2026 12:00:00 GMT"},
            )
        if request.url.host == "downloads.majestic.com":
            body = "GlobalRank,Domain\n10,google.com\n20,workyard.com\n"
            return httpx.Response(200, content=body.encode(), headers={"last-modified": "Sun, 01 Nov 2026 00:00:00 GMT"})
        raise AssertionError(request.url.host)

    cache = tmp_path / "authority"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tranco, majestic = load_authority_sources(cache_dir=cache, fetch=True, client=client)
    assert tranco.ranks is not None and tranco.ranks["busybusy.com"] == 4
    assert tranco.list_date == "2026-11-13"
    assert majestic.ranks is not None and majestic.ranks["workyard.com"] == 20
    assert majestic.list_date == "2026-11-01"
    assert calls["n"] == 2

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"refetched {request.url.host}")

    again, _majestic = load_authority_sources(
        cache_dir=cache,
        fetch=True,
        client=httpx.Client(transport=httpx.MockTransport(boom)),
    )
    assert again.ranks is not None and again.ranks["busybusy.com"] == 4
    assert again.list_date == "2026-11-13"


def test_authority_cache_refreshes_after_30_days(tmp_path):
    from datetime import datetime, timedelta, timezone

    from aeo.ranksim.kwresearch.authority_cache import MANIFEST_NAME, load_authority_sources

    cache = tmp_path / "authority"
    cache.mkdir()
    (cache / "tranco-top-1m.csv.zip").write_bytes(_zip_bytes("4,busybusy.com\n"))
    (cache / "majestic_million.csv").write_text("GlobalRank,Domain\n20,workyard.com\n", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(days=31)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (cache / MANIFEST_NAME).write_text(
        '{"tranco": {"fetched_at": "%s", "list_date": "2026-10-01", "source_url": "https://tranco-list.eu/top-1m.csv.zip"},'
        '"majestic": {"fetched_at": "%s", "list_date": "2026-10-01", "source_url": "https://downloads.majestic.com/majestic_million.csv"}}\n'
        % (old, old),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "tranco-list.eu":
            return httpx.Response(
                200,
                content=_zip_bytes("9,busybusy.com\n"),
                headers={"last-modified": "Fri, 13 Nov 2026 12:00:00 GMT"},
            )
        if request.url.host == "downloads.majestic.com":
            return httpx.Response(503, text="no")
        raise AssertionError(request.url.host)

    tranco, majestic = load_authority_sources(
        cache_dir=cache,
        fetch=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert tranco.ranks is not None and tranco.ranks["busybusy.com"] == 9
    assert tranco.list_date == "2026-11-13"
    assert majestic.ranks is not None and majestic.ranks["workyard.com"] == 20
    assert majestic.list_date == "2026-10-01"
    assert "cached list" in majestic.note


def test_authority_download_failure_drops_the_list(tmp_path):
    from aeo.ranksim.kwresearch.authority_cache import load_authority_sources

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    tranco, majestic = load_authority_sources(
        cache_dir=tmp_path / "empty",
        fetch=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert tranco.ranks is None and majestic.ranks is None
    assert "download failed" in tranco.note


def test_navigational_brand_at_position_1_is_flagged_and_counts_as_a_homepage():
    from aeo.ranksim.kwresearch.difficulty import WEIGHTS

    assert WEIGHTS == {
        "serp_composition": 0.30,
        "domain_authority": 0.25,
        "exact_match": 0.15,
        "ai_overview": 0.10,
        "cpc": 0.10,
        "ads_competition": 0.10,
    }
    for keyword, url in (
        ("busybusy", "https://www.busybusy.com/"),
        ("quoteiq", "https://quoteiq.com/"),
        ("workyard", "https://www.workyard.com/"),
    ):
        result = score_difficulty(
            keyword,
            organic=[{"position": 1, "url": url, "title": keyword}],
            payload={},
            volume=None,
            tranco=None,
            majestic=None,
            tranco_date="2026-11-13",
        )
        assert result.navigational is True
        assert "brand owning its own SERP" in result.navigational_note
        composition = next(factor for factor in result.factors if factor.name == "serp_composition")
        assert composition.raw.startswith("brand_homepages=1")
        assert composition.weight == 0.30
        authority = next(factor for factor in result.factors if factor.name == "domain_authority")
        assert authority.status == "missing"

    inner = score_difficulty(
        "busybusy",
        organic=[{"position": 1, "url": "https://busybusy.com/pricing", "title": "Pricing"}],
        payload={},
        volume=None,
        tranco=None,
        majestic=None,
    )
    assert inner.navigational is True
    composition = next(factor for factor in inner.factors if factor.name == "serp_composition")
    assert composition.raw.startswith("brand_homepages=0")

    other = score_difficulty(
        "construction time tracking software",
        organic=[{"position": 1, "url": "https://www.busybusy.com/", "title": "BusyBusy"}],
        payload={},
        volume=None,
        tranco=None,
        majestic=None,
    )
    assert other.navigational is False
    assert other.navigational_note == ""


def test_difficulty_command_uses_the_cached_lists_and_shows_the_date(tmp_path):
    from aeo.ranksim.kwresearch.store import KeywordStore

    cache = tmp_path / "kw.sqlite"
    store = KeywordStore(cache)
    store.put_serp(
        "busybusy",
        "2026-11-14",
        {"organic": [{"position": 1, "title": "BusyBusy", "link": "https://www.busybusy.com/", "snippet": ""}]},
        "2026-11-14T00:00:00Z",
    )
    store.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "tranco-list.eu":
            return httpx.Response(
                200,
                content=_zip_bytes("12,busybusy.com\n"),
                headers={"last-modified": "Fri, 13 Nov 2026 12:00:00 GMT"},
            )
        if request.url.host == "downloads.majestic.com":
            return httpx.Response(503, text="no")
        raise AssertionError(request.url.host)

    rows = keyword_difficulty(
        ["busybusy"],
        cache,
        api_key=None,
        credit_cap=0,
        today="2026-11-14",
        fetch_authority=True,
        authority_cache=tmp_path / "lists",
        authority_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = rows[0]
    assert result.navigational is True
    assert result.credits == 0
    authority = next(factor for factor in result.factors if factor.name == "domain_authority")
    assert authority.status == "present"
    assert "Tranco list date 2026-11-13" in authority.raw
    assert "Majestic" not in authority.raw
    assert "busybusy.com (Tranco 12)" in authority.raw

