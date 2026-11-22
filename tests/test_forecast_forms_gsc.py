"""Tests for the agency form and the Search Console importer. No network: the API client uses httpx.MockTransport."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from aeo.ranksim.forecast import agency_form as af
from aeo.ranksim.forecast import gsc
from aeo.ranksim.forecast.asof import LeakError
from aeo.ranksim.forecast.gsc import GscClient, GscError, GscParseError, GscRow

FIXTURES = Path(__file__).parent / "fixtures" / "forecast_gsc"
TOKEN = "ya29.test-token-never-real"


# =========================================================================== agency form


def answers(**changes) -> dict:
    base = {
        "site": "https://www.acmeplumbing.co.uk/",
        "niche": "plumber",
        "brand_names": ["Acme Plumbing", "Acme"],
        "filled_by": "agency person",
        "filled_on": "2026-11-25",
        "footprint": {
            "review_count": 40,
            "review_rating": 4.6,
            "review_platform": "Google",
            "reviews_date": "2026-11-24",
            "brand_search_volume": 90,
            "brand_search_volume_source": "keyword tool",
            "brand_search_volume_date": "2026-11-20",
            "referring_domains": None,
            "has_old_site": True,
            "old_domain": "acme-old.co.uk",
            "old_domain_first_seen": "2016-11-25",
            "years_trading": 12,
            "social_profiles": [],
            "business_listing": True,
        },
        "launch_plan": {
            "planned_launch_date": "2026-11-30",
            "links_planned_count": 5,
            "links_planned_kind": "local directories",
            "press_planned": False,
            "monthly_ad_budget_usd": 250,
            "pages_at_launch": 12,
        },
    }
    for key, value in changes.items():
        section, _, field = key.partition("__")
        if field:
            base[section][field] = value
        else:
            base[section] = value
    return base


def test_load_form_reads_answers_and_keeps_unknowns_as_none():
    form = af.load_form(answers(footprint__years_trading="", launch_plan__links_planned_kind="  "))
    assert form.site == "acmeplumbing.co.uk"
    assert form.footprint.referring_domains is None
    assert form.footprint.years_trading is None
    assert form.launch_plan.links_planned_kind is None
    assert form.footprint.social_profiles == ()
    assert form.filled_on == date(2026, 11, 25)
    features = af.to_features(form, "2026-11-30")
    assert list(features) == list(af.FEATURE_NAMES)
    assert features["form_referring_domains"] is None
    assert features["form_years_trading"] is None
    assert features["form_social_profile_count"] == 0.0
    assert features["form_review_count"] == 40.0
    assert features["form_press_planned"] == 0.0
    assert features["form_business_listing"] == 1.0
    assert features["form_old_site_age_days"] == float((date(2026, 11, 30) - date(2016, 11, 25)).days)
    assert features["form_days_to_planned_launch"] == 0.0
    later = af.to_features(form, date(2026, 12, 30))
    assert later["form_days_to_planned_launch"] == -30.0


def test_unknown_social_profiles_stay_none_not_zero():
    form = af.load_form(answers(footprint__social_profiles=None, footprint__business_listing=None))
    features = af.to_features(form, "2026-12-01")
    assert features["form_social_profile_count"] is None
    assert features["form_business_listing"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"footprint__review_count": "40"}, "footprint.review_count must be a whole number"),
        ({"footprint__review_count": True}, "footprint.review_count must be a whole number, not true or false"),
        ({"footprint__review_count": -1}, "footprint.review_count must be 0 or more"),
        ({"footprint__review_rating": 6}, "footprint.review_rating must be 1 to 5"),
        ({"footprint__review_count": 0}, "footprint.review_rating is given but footprint.review_count is 0"),
        ({"footprint__brand_search_volume_date": None}, "footprint.brand_search_volume_date is needed"),
        ({"footprint__brand_search_volume_date": "2026-11-26"}, "footprint.brand_search_volume_date 2026-11-26 is after filled_on"),
        ({"footprint__has_old_site": False}, "footprint.old_domain is given but footprint.has_old_site is false"),
        ({"footprint__business_listing": "yes"}, "footprint.business_listing must be true or false"),
        ({"footprint__social_profiles": "https://x.example"}, "footprint.social_profiles must be a list"),
        ({"launch_plan__monthly_ad_budget_usd": -5}, "launch_plan.monthly_ad_budget_usd must be 0 or more"),
        ({"launch_plan__planned_launch_date": "30/11/2026"}, "launch_plan.planned_launch_date must be a date"),
        ({"launch_plan__pages_at_launch": 0}, "launch_plan.pages_at_launch must be 1 or more"),
        ({"filled_on": None}, "filled_on is required"),
        ({"filled_on": datetime(2026, 11, 25, 9, tzinfo=UTC)}, "filled_on must be a calendar date"),
        ({"filled_on": "2026-11-25T09:00:00Z"}, "filled_on must be a date like"),
        ({"site": "not a domain"}, "is not a domain"),
        ({"site": "smartcrewhq.com"}, "excluded"),
    ],
)
def test_form_errors_name_the_field(change, message):
    with pytest.raises(af.FormError, match=message.replace(".", r"\.")):
        af.load_form(answers(**change))


def test_form_refuses_unknown_fields():
    bad = answers()
    bad["footprint"]["reveiw_count"] = 3
    with pytest.raises(af.FormError, match="footprint has unknown fields"):
        af.load_form(bad)
    with pytest.raises(af.FormError, match="unknown fields"):
        af.load_form({**answers(), "extra": 1})


def test_to_features_refuses_answers_dated_after_as_of():
    form = af.load_form(answers())
    with pytest.raises(LeakError):
        af.to_features(form, "2026-11-24")  # the form was filled on 25 November
    with pytest.raises(LeakError):
        af.to_features(form, "2026-11-21")  # reviews counted 24 November
    assert af.to_features(form, "2026-11-25")["form_review_count"] == 40.0


def test_template_round_trip(tmp_path):
    path = af.write_template(tmp_path / "form.json")
    template = json.loads(path.read_text())
    assert set(template["questions"]) >= {"site", "footprint.review_count", "launch_plan.pages_at_launch"}
    assert all(chr(0x2014) not in q and chr(0x2013) not in q for q in template["questions"].values())
    with pytest.raises(af.FormError, match="site is required"):
        af.load_form(path)
    template["answers"].update({"site": "newsite.example", "filled_by": "me", "filled_on": "2026-11-22"})
    path.write_text(json.dumps(template))
    form = af.load_form(path)
    assert form.footprint == af.Footprint()
    assert all(value is None for value in af.to_features(form, "2026-11-22").values())
    with pytest.raises(FileExistsError):
        af.write_template(path)
    assert af.load_form(form.to_payload()) == form


def test_brand_terms_and_brand_search_flag():
    form = af.load_form(answers())
    assert af.brand_terms(form) == ("acme", "acme plumbing", "acmeplumbing")
    assert af.is_brand_search("Acme Plumbing Leeds", form)
    assert af.is_brand_search("acmeplumbing reviews", form)
    assert af.is_brand_search("acme-plumbing phone number", form)
    assert not af.is_brand_search("plumbing leeds", form)
    assert not af.is_brand_search("acmes", form)
    art = af.load_form(answers(brand_names=["Art House"], site="arthouse.example"))
    assert af.is_brand_search("art house cafe", art)
    assert af.is_brand_search("arthouse cafe", art)
    assert not af.is_brand_search("start house now", art)
    short = af.load_form(answers(brand_names=["GO"], site="go.example"))
    assert af.brand_terms(short) == ()


# =========================================================================== Search Console parsers


def test_ui_export_folder_and_zip(tmp_path):
    folder = FIXTURES / "ui_export"
    export = gsc.parse_ui_export(folder, fetched_at="2026-12-07T09:00:00Z")
    assert (export.period_start, export.period_end, export.period_from) == (date(2026, 12, 1), date(2026, 12, 5), "dates.csv")
    days = {row.data_date: row for row in export.dates.rows}
    assert days[date(2026, 12, 1)].impressions == 0
    assert days[date(2026, 12, 1)].position is None and days[date(2026, 12, 1)].ctr is None
    assert days[date(2026, 12, 3)].ctr == pytest.approx(0.0833)
    assert days[date(2026, 12, 3)].position == pytest.approx(18.5)
    assert days[date(2026, 12, 3)].fetched_at == "2026-12-07T09:00:00Z"
    # The newest 2 days of the table are preliminary.
    assert [day for day, row in sorted(days.items()) if row.is_preliminary] == [date(2026, 12, 4), date(2026, 12, 5)]
    queries = {row.query: row for row in export.queries.rows}
    assert queries["boiler repair, leeds"].impressions == 14
    assert queries["emergency plumber leeds"].period_start == date(2026, 12, 1)
    assert queries["emergency plumber leeds"].data_date == date(2026, 12, 5)
    assert all(row.is_preliminary and not row.is_daily for row in export.queries.rows)
    assert {row.page for row in export.pages.rows} == {"https://seedsite.example/", "https://seedsite.example/boiler-repair/"}
    assert all(row.query is None for row in export.pages.rows)

    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in ("Queries.csv", "Pages.csv", "Filters.csv"):
            handle.write(folder / name, f"site-Performance/{name}")
        handle.write(folder / "Dates.csv", "site-Performance/Chart.csv")
    zipped = gsc.parse_ui_export(archive, period_start="2026-11-29", period_end="2026-12-05", final=True)
    assert zipped.period_from == "given" and zipped.period_start == date(2026, 11, 29)
    assert len(zipped.dates.rows) == 5 and len(zipped.queries.rows) == 3
    assert not any(row.is_preliminary for row in zipped.queries.rows)
    assert len(zipped.all_rows()) == 10


def test_ui_export_without_a_date_table_is_refused(tmp_path):
    (tmp_path / "Queries.csv").write_text((FIXTURES / "ui_export" / "Queries.csv").read_text())
    with pytest.raises(GscParseError, match="no Dates.csv or Chart.csv"):
        gsc.parse_ui_export(tmp_path)
    with pytest.raises(GscParseError, match="period must be given"):
        gsc._parse_table("Top queries,Clicks,Impressions\nx,1,2\n", source="q", fetched_at=None, period=None, label="q")


def test_daily_query_export_and_exclusion(monkeypatch):
    monkeypatch.setattr(gsc, "EXCLUDED_SITES", frozenset({"excluded.example"}))
    table = gsc.parse_daily_table(FIXTURES / "daily_queries.csv", fetched_at=datetime(2026, 12, 8, 6, tzinfo=UTC))
    assert table.excluded_rows == 1
    assert len(table.rows) == 5
    assert {row.query for row in table.rows} >= {'"quoted query"', "boiler repair leeds"}
    first = table.rows[0]
    assert (first.data_date, first.clicks, first.impressions, first.ctr, first.position) == (
        date(2026, 12, 3), 1, 4, 0.25, 35.5
    )
    assert first.fetched_at == "2026-12-08T06:00:00Z" and first.is_daily
    assert [row.data_date for row in table.rows if row.is_preliminary] == [date(2026, 12, 5), date(2026, 12, 6)]


def test_totals_table_turns_zero_position_into_none():
    table = gsc.parse_daily_table((FIXTURES / "totals.csv").read_text())
    zero = table.rows[0]
    assert zero.impressions == 0 and zero.position is None and zero.ctr is None
    assert table.rows[1].position == pytest.approx(40.2)
    assert all(row.query is None and row.page is None for row in table.rows)


def test_split_table_with_ctr_percent_and_extra_columns():
    table = gsc.parse_daily_table((FIXTURES / "daily_split.csv").read_text())
    mobile = next(row for row in table.rows if row.device == "mobile")
    assert mobile.ctr == pytest.approx(0.25) and mobile.country == "GBR" and mobile.position == 7.5


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("date,clicks,impressions,position\n2026-12-01,5,3,2.0\n", "5 clicks but only 3 impressions"),
        ("date,clicks,impressions,position\n2026-12-01,0,3,0.5\n", "Positions start at 1"),
        ("date,clicks,impressions,ctr\n2026-12-01,0,3,1.5\n", "click rate from 0 to 1"),
        ("date,clicks,impressions\n2026/12/01,0,3\n", "line 2 date is not a date"),
        ("date,clicks,impressions\n2026-12-01,x,3\n", "line 2 clicks is not a number"),
        ("date,clicks\n2026-12-01,0\n", "no impressions column"),
        ("date,clicks,impressions\n2026-12-01,0\n", "line 2 has 2 cells"),
    ],
)
def test_daily_table_errors_name_row_and_column(text, message):
    with pytest.raises(GscParseError, match=message):
        gsc.parse_daily_table(text)


def test_range_table_refuses_a_date_column():
    with pytest.raises(GscParseError, match="date column"):
        gsc.parse_range_table((FIXTURES / "totals.csv").read_text(), period_start="2026-12-01", period_end="2026-12-04")
    with pytest.raises(ValueError, match="after period_end"):
        gsc.parse_range_table(FIXTURES / "ui_export" / "Queries.csv", period_start="2026-12-05", period_end="2026-12-01")


def test_api_rows_parse_and_preliminary_rule():
    payload = {
        "rows": [
            {"keys": ["2026-12-01", "plumber leeds"], "clicks": 0, "impressions": 2, "ctr": 0, "position": 33.5},
            {"keys": ["2026-12-02", "plumber leeds"], "clicks": 1, "impressions": 4, "ctr": 0.25, "position": 20},
            {"keys": ["2026-12-03", "plumber leeds"], "clicks": 0, "impressions": 1, "ctr": 0, "position": 18},
        ],
        "responseAggregationType": "byProperty",
    }
    table = gsc.parse_api_rows(payload, dimensions=["date", "query"], fetched_at="2026-12-06T00:00:00Z")
    assert [row.is_preliminary for row in table.rows] == [False, True, True]
    assert table.rows[1].ctr == 0.25 and table.rows[1].page is None
    assert gsc.parse_api_rows({}, dimensions=["date"], fetched_at=None).rows == ()
    ranged = gsc.parse_api_rows(
        {"rows": [{"keys": ["q"], "clicks": 0, "impressions": 3, "ctr": 0, "position": 9}]},
        dimensions=["query"], fetched_at=None, start_date="2026-11-01", end_date="2026-11-28",
    )
    assert ranged.rows[0].period_start == date(2026, 11, 1) and ranged.rows[0].data_date == date(2026, 11, 28)
    with pytest.raises(GscParseError, match="need start_date and end_date"):
        gsc.parse_api_rows({"rows": []}, dimensions=["query"], fetched_at=None)
    with pytest.raises(GscParseError, match="keys"):
        gsc.parse_api_rows({"rows": [{"keys": ["2026-12-01"], "clicks": 0, "impressions": 1}]}, dimensions=["date", "query"], fetched_at=None)
    with pytest.raises(GscParseError, match="unknown dimensions"):
        gsc.parse_api_rows({"rows": []}, dimensions=["date", "city"], fetched_at=None)


def test_index_status_both_layouts_and_signals():
    simple = gsc.parse_index_status(FIXTURES / "index_status.csv")
    assert [(row.day, row.indexed, row.not_indexed) for row in simple][1] == (date(2026, 12, 4), 3, 9)
    coverage = gsc.parse_index_status((FIXTURES / "coverage_summary.csv").read_text())
    assert [(row.day, row.indexed, row.not_indexed) for row in coverage] == [
        (date(2026, 12, 4), 4, 9),
        (date(2026, 12, 5), 5, 2),
    ]
    signals = gsc.index_signals(simple, launch_date="2026-12-01", as_of="2026-12-08")
    assert signals["idx_days_to_first_indexed"] == 3.0
    assert signals["idx_indexed_latest"] == 7.0
    assert signals["idx_indexed_share_latest"] == pytest.approx(7 / 12)
    assert signals["idx_indexed_per_week"] == pytest.approx(7.0)
    assert signals["idx_rows_ignored_late"] == 1.0
    with pytest.raises(LeakError):
        gsc.index_signals(simple, launch_date="2026-12-01", as_of="2026-12-08", strict=True)
    with pytest.raises(GscParseError, match="listed twice"):
        gsc.parse_index_status("date,indexed,not_indexed\n2026-12-01,1,2\n2026-12-01,1,2\n")
    empty = gsc.index_signals(simple, launch_date="2026-12-20", as_of="2026-12-21")
    assert empty["idx_indexed_latest"] is None


# =========================================================================== API client


def make_client(handler, **kwargs) -> tuple[GscClient, list[float]]:
    slept: list[float] = []
    client = GscClient(
        TOKEN,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: datetime(2026, 12, 20, 8, 30, tzinfo=UTC),
        sleep=slept.append,
        **kwargs,
    )
    return client, slept


def api_row(index: int) -> dict:
    return {"keys": [f"2026-12-{index + 1:02d}", f"query {index}"], "clicks": 0, "impressions": 1, "ctr": 0, "position": 10 + index}


def test_client_pages_with_start_row_until_a_short_page():
    seen: list[dict] = []
    all_rows = [api_row(index) for index in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.url.raw_path == b"/webmasters/v3/sites/sc-domain%3Aseedsite.example/searchAnalytics/query"
        body = json.loads(request.content)
        seen.append(body)
        start, limit = body["startRow"], body["rowLimit"]
        return httpx.Response(200, json={"rows": all_rows[start : start + limit], "responseAggregationType": "byProperty"})

    client, slept = make_client(handler)
    result = client.query("sc-domain:seedsite.example", "2026-12-01", "2026-12-05", dimensions=["date", "query"], row_limit=2)
    assert [body["startRow"] for body in seen] == [0, 2, 4]
    assert seen[0]["startDate"] == "2026-12-01" and seen[0]["dataState"] == "final" and seen[0]["type"] == "web"
    assert result.pages_fetched == 3 and len(result.rows) == 5 and slept == []
    assert result.fetched_at == "2026-12-20T08:30:00Z"
    assert [row.query for row in result.rows] == [f"query {i}" for i in range(5)]
    assert [row.is_preliminary for row in result.rows] == [False, False, False, True, True]


def test_client_stops_on_an_empty_page_after_an_exact_multiple():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["startRow"])
        if body["startRow"] >= 4:
            return httpx.Response(200, json={"responseAggregationType": "byProperty"})
        return httpx.Response(200, json={"rows": [api_row(body["startRow"]), api_row(body["startRow"] + 1)]})

    client, _ = make_client(handler)
    result = client.query("https://seedsite.example/", "2026-12-01", "2026-12-05", dimensions=["date", "query"], row_limit=2)
    assert calls == [0, 2, 4] and len(result.rows) == 4


def test_client_marks_rows_from_first_incomplete_date():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rows": [api_row(i) for i in range(5)], "metadata": {"first_incomplete_date": "2026-12-02"}})

    client, _ = make_client(handler)
    result = client.query("https://seedsite.example/", "2026-12-01", "2026-12-05", dimensions=["date", "query"], data_state="all")
    assert result.first_incomplete_date == date(2026, 12, 2)
    assert [row.is_preliminary for row in result.rows] == [False, True, True, True, True]


def test_client_retries_then_fails_without_showing_the_token():
    statuses = iter([503, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json={"siteEntry": [{"siteUrl": "sc-domain:b.example", "permissionLevel": "siteOwner"}, {"siteUrl": "https://a.example/", "permissionLevel": "siteFullUser"}]} if status == 200 else {})

    client, slept = make_client(flaky)
    sites = client.list_sites()
    assert [site.site_url for site in sites] == ["https://a.example/", "sc-domain:b.example"]
    assert slept == [2.0]

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "message": f"Invalid token {TOKEN}"}})

    client, _ = make_client(denied)
    with pytest.raises(GscError) as caught:
        client.list_sites()
    assert caught.value.status_code == 401
    assert TOKEN not in str(caught.value) and "expired" in str(caught.value)
    assert TOKEN not in repr(client)

    def always_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "quota"}})

    client, slept = make_client(always_429)
    with pytest.raises(GscError, match="429"):
        client.query("https://seedsite.example/", "2026-12-01", "2026-12-02")
    assert slept == [2.0, 4.0]

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    client, _ = make_client(not_json)
    with pytest.raises(GscError, match="not JSON"):
        client.list_sites()

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with {TOKEN}")

    client, slept = make_client(broken)
    with pytest.raises(GscError) as caught:
        client.list_sites()
    assert TOKEN not in str(caught.value) and len(slept) == 2


def test_client_refuses_bad_requests_before_sending():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("no request should be sent")

    client, _ = make_client(handler)
    with pytest.raises(GscError, match="excluded"):
        client.query("sc-domain:smartcrewhq.com", "2026-12-01", "2026-12-02")
    with pytest.raises(ValueError):
        client.query("https://a.example/", "2026-12-03", "2026-12-02")
    with pytest.raises(ValueError):
        client.query("https://a.example/", "2026-12-01", "2026-12-02", row_limit=25001)
    with pytest.raises(ValueError):
        client.query("https://a.example/", "2026-12-01", "2026-12-02", data_state="fresh")
    with pytest.raises(ValueError):
        GscClient("  ")


def test_client_gives_up_past_the_page_cap():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rows": [api_row(0)]})

    client, _ = make_client(handler, max_pages=3)
    with pytest.raises(GscError, match="incomplete"):
        client.query("https://a.example/", "2026-12-01", "2026-12-02", dimensions=["date", "query"], row_limit=1)


# =========================================================================== early signals


LAUNCH = date(2026, 11, 30)
AS_OF = date(2026, 12, 16)  # cutoff 14 December: days 0 to 14, so 15 days and 2 complete weeks
TARGETS = ["Emergency Plumber Leeds", "boiler repair leeds", "blocked drain leeds", "water heater install"]


def row(day: str, impressions: int, *, clicks: int = 0, query=None, page=None, position=None, prelim=False, start=None) -> GscRow:
    data_date = date.fromisoformat(day)
    return GscRow(
        data_date=data_date,
        query=query,
        page=page,
        clicks=clicks,
        impressions=impressions,
        ctr=None if impressions == 0 else clicks / impressions,
        position=position,
        fetched_at="2026-12-16T06:00:00Z",
        is_preliminary=prelim,
        period_start=date.fromisoformat(start) if start else data_date,
        source="test",
    )


def hand_series() -> list[GscRow]:
    totals = [
        row("2026-12-03", 2, position=40.0),
        row("2026-12-05", 4, position=35.0),
        row("2026-12-08", 10, clicks=1, position=25.0),
        row("2026-12-12", 8, position=55.0),
        row("2026-12-14", 5, position=9.0),
        row("2026-12-15", 100, position=3.0),  # after the cutoff
        row("2026-12-16", 50, position=3.0),  # after the cutoff
        row("2026-12-10", 70, prelim=True),  # an early fetch's preliminary day
        row("2026-11-28", 3, position=60.0),  # before launch
    ]
    queries = [
        row("2026-12-03", 2, query="emergency plumber leeds", page="/a", position=40.0),
        row("2026-12-08", 6, query="emergency plumber leeds", page="/a", position=30.0),
        row("2026-12-08", 4, query="boiler repair leeds", page="/b", position=12.0),
        row("2026-12-12", 8, query="plumber near me", page="/a", position=55.0),
        row("2026-12-14", 5, query="boiler repair leeds", page="/b", position=9.0),
        row("2026-12-15", 3, query="blocked drain leeds", page="/c", position=20.0),  # after the cutoff
        row("2026-12-14", 40, query="big range query", page="/a", position=5.0, start="2026-12-01"),  # a range row
    ]
    return totals + queries


def test_early_signals_on_a_hand_made_series():
    result = gsc.early_signals(hand_series(), launch_date=LAUNCH, as_of=AS_OF, target_searches=TARGETS)
    assert result["sc_days_observed"] == 15.0
    assert result["sc_complete_weeks"] == 2.0
    assert result["sc_days_to_first_impression"] == 3.0  # 3 December
    assert result["sc_impressions_total"] == 29.0  # 2 + 4 + 10 + 8 + 5
    assert result["sc_clicks_total"] == 1.0
    assert result["sc_impressions_per_week"] == 12.0  # week 0: 6, week 1: 18
    assert result["sc_impressions_weekly_slope"] == 12.0
    assert result["sc_distinct_queries_total"] == 3.0
    assert result["sc_distinct_queries_per_week"] == 2.0  # week 0: 1 query, week 1: 3 queries
    assert result["sc_pages_with_impressions"] == 2.0  # /a and /b
    assert result["sc_query_present[emergency plumber leeds]"] == 1.0
    assert result["sc_avg_position[emergency plumber leeds]"] == pytest.approx(32.5)  # (2*40 + 6*30) / 8
    assert result["sc_avg_position[boiler repair leeds]"] == pytest.approx(93 / 9)  # (4*12 + 5*9) / 9
    assert result["sc_query_present[blocked drain leeds]"] == 0.0  # only seen after the cutoff
    assert result["sc_avg_position[blocked drain leeds]"] is None
    assert result["sc_query_present[water heater install]"] == 0.0
    assert result["sc_target_queries_present_share"] == 0.5
    assert result["sc_rows_ignored_late"] == 3.0
    assert result["sc_rows_ignored_preliminary"] == 1.0
    assert result["sc_rows_ignored_range"] == 1.0
    assert result["sc_rows_ignored_before_launch"] == 1.0
    assert result["sc_rows_ignored_excluded"] == 0.0


def test_early_signals_one_complete_week_has_no_slope_and_drops_excluded_rows():
    rows = [row("2026-12-01", 3), row("2026-12-02", 4, page="https://www.smartcrewhq.com/x")]
    # as_of 9 December: cutoff 7 December, days 0 to 7, so 8 days and 1 complete week.
    result = gsc.early_signals(rows, launch_date=LAUNCH, as_of=date(2026, 12, 9), target_searches=[])
    assert result["sc_complete_weeks"] == 1.0
    assert result["sc_impressions_per_week"] == 3.0
    assert result["sc_impressions_weekly_slope"] is None
    assert result["sc_rows_ignored_excluded"] == 1.0
    short = gsc.early_signals(rows[:1], launch_date=LAUNCH, as_of=date(2026, 12, 6), target_searches=[])
    assert short["sc_complete_weeks"] == 0.0 and short["sc_impressions_per_week"] is None
    assert short["sc_impressions_total"] == 3.0


def test_early_signals_strict_refuses_late_rows():
    with pytest.raises(LeakError, match="after the cutoff 2026-12-14"):
        gsc.early_signals(hand_series(), launch_date=LAUNCH, as_of=AS_OF, target_searches=TARGETS, strict=True)
    on_time = [item for item in hand_series() if item.data_date <= date(2026, 12, 14)]
    strict = gsc.early_signals(on_time, launch_date=LAUNCH, as_of=AS_OF, target_searches=TARGETS, strict=True)
    assert strict["sc_impressions_total"] == 29.0


def test_preliminary_day_rule_moves_with_as_of():
    # A row dated as_of minus 2 is used. A row dated as_of minus 1 is not.
    rows = [row("2026-12-14", 5), row("2026-12-15", 7)]
    result = gsc.early_signals(rows, launch_date=LAUNCH, as_of=AS_OF, target_searches=[])
    assert result["sc_impressions_total"] == 5.0 and result["sc_rows_ignored_late"] == 1.0


def test_early_signals_zero_and_unknown_are_different():
    nothing = gsc.early_signals([], launch_date=LAUNCH, as_of=AS_OF, target_searches=["x"])
    assert nothing["sc_impressions_total"] == 0.0
    assert nothing["sc_days_to_first_impression"] is None
    assert nothing["sc_query_present[x]"] == 0.0 and nothing["sc_pages_with_impressions"] == 0.0
    totals_only = gsc.early_signals([row("2026-12-03", 4)], launch_date=LAUNCH, as_of=AS_OF, target_searches=["x"])
    assert totals_only["sc_impressions_total"] == 4.0
    assert totals_only["sc_query_present[x]"] is None
    assert totals_only["sc_distinct_queries_total"] is None and totals_only["sc_pages_with_impressions"] is None
    before_launch = gsc.early_signals([], launch_date=LAUNCH, as_of=date(2026, 12, 1), target_searches=["x"])
    assert before_launch["sc_days_observed"] is None and before_launch["sc_impressions_total"] is None


def test_early_signals_dedupes_refetched_days_and_refuses_mixed_splits():
    old = row("2026-12-03", 4)
    new = GscRow(**{**old.__dict__, "impressions": 6, "fetched_at": "2026-12-16T07:00:00Z"})
    result = gsc.early_signals([new, old], launch_date=LAUNCH, as_of=AS_OF, target_searches=[])
    assert result["sc_impressions_total"] == 6.0
    split = GscRow(**{**old.__dict__, "device": "mobile", "data_date": date(2026, 12, 4), "period_start": date(2026, 12, 4)})
    with pytest.raises(ValueError, match="count impressions twice"):
        gsc.early_signals([old, split], launch_date=LAUNCH, as_of=AS_OF, target_searches=[])


def test_early_signals_from_parsed_fixture():
    table = gsc.parse_daily_table(FIXTURES / "daily_queries.csv")
    result = gsc.early_signals(table.rows, launch_date="2026-12-01", as_of="2026-12-07", target_searches=["boiler repair leeds"])
    # Cutoff 5 December. Rows on 5 and 6 December are the newest two days of the file, so preliminary or late.
    assert result["sc_impressions_total"] == 11.0  # 4 + 1 + 6, query rows only, so an undercount
    assert result["sc_query_present[boiler repair leeds]"] == 1.0
    assert result["sc_avg_position[boiler repair leeds]"] == 12.0
    assert result["sc_days_to_first_impression"] == 2.0
    assert result["sc_rows_ignored_late"] == 1.0 and result["sc_rows_ignored_preliminary"] == 2.0


def test_no_async_client_in_my_modules():
    for name in ("gsc.py", "seeds.py", "agency_form.py"):
        text = (Path(gsc.__file__).parent / name).read_text(encoding="utf-8")
        assert "Async" + "Client" not in text
