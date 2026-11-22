"""Offline tests for the dated strength lists, site start dates and the Google update calendar.

All data comes from tiny fixtures in tests/fixtures/forecast_sources/ written in the real file layouts.
No test touches the network: HTTP goes through httpx.MockTransport.
"""

from __future__ import annotations

import gzip
import json
import shutil
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest

from aeo.ranksim.forecast.asof import AsOfGuard, Dated, LeakError
from aeo.ranksim.forecast.sources import domain_age, strength_lists, updates
from aeo.ranksim.forecast.sources.strength_lists import (
    CommonCrawlDomainRanks,
    CruxTopLists,
    MajesticToday,
    OpenPageRankHistory,
    Snapshot,
    TrancoByDate,
    crux_coarse_bucket,
    crux_month_published,
    extract_panel_sites,
    filter_rank_lines,
    parse_crux_rows,
    parse_rank_line,
    pick_snapshot,
    reverse_domain,
    stream_gzip_lines,
    strength_features,
    wanted_sites,
)

FIXTURES = Path(__file__).parent / "fixtures" / "forecast_sources"
SITE = "example-shop.co.uk"
OLD_RELEASE = "cc-main-2025-jan-feb-mar"  # published 2025-04-01
NEW_RELEASE = "cc-main-2025-feb-mar-apr"  # published 2025-05-05


def failing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no network call was expected, got {request.method} {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


class _Stream(httpx.SyncByteStream):
    """A streamed body, as a real server sends it (an in-memory body cannot be read raw)."""

    def __init__(self, data: bytes, cut: int | None = None):
        self.data = data
        self.cut = cut

    def __iter__(self):
        if self.cut is None:
            yield self.data
            return
        yield self.data[: self.cut]
        raise httpx.ReadError("connection reset")


def ranks_client(calls: list[str]) -> httpx.Client:
    payload = (FIXTURES / "cc_domain_ranks_sample.txt.gz").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.host == "data.commoncrawl.org"
        return httpx.Response(200, stream=_Stream(payload), headers={"content-length": str(len(payload)),
                                                                     "last-modified": "Tue, 01 Apr 2025 14:59:11 GMT"})

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# As-of selection


def test_pick_snapshot_takes_newest_published_on_or_before_and_never_later() -> None:
    snaps = [
        Snapshot("a", date(2025, 1, 1), date(2025, 3, 28), date(2025, 4, 1)),
        Snapshot("b", date(2025, 2, 6), date(2025, 5, 1), date(2025, 5, 5)),
        Snapshot("undated", None, None, None),
    ]
    assert pick_snapshot(snaps, date(2025, 3, 31)) is None
    assert pick_snapshot(snaps, date(2025, 4, 1)).snapshot_id == "a"
    assert pick_snapshot(snaps, date(2025, 5, 4)).snapshot_id == "a"  # b's data ends 05-01 but it came out 05-05
    assert pick_snapshot(snaps, date(2025, 5, 5)).snapshot_id == "b"
    assert pick_snapshot(snaps, "2030-01-01").snapshot_id == "b"


def test_cc_release_table_is_keyed_by_publication_date() -> None:
    releases = CommonCrawlDomainRanks.releases()
    info = releases[OLD_RELEASE]
    assert info["published"] == date(2025, 4, 1)
    assert info["data_end"] == date(2025, 3, 28)
    for item in releases.values():
        assert item["published"] >= item["data_end"]
        assert (item["published"] - item["data_end"]).days <= strength_lists.CC_FALLBACK_LAG_DAYS
    cc = CommonCrawlDomainRanks(root=Path("/nonexistent"))
    chosen = cc.releases_between("2024-10", "2026-04")
    assert chosen[0] == "cc-main-2024-aug-sep-oct" and chosen[-1] == "cc-main-2026-feb-mar-apr"
    assert len(chosen) == 19
    assert CommonCrawlDomainRanks.fallback_published(date(2026, 1, 1)) == date(2026, 3, 2)


def test_cc_lookup_picks_newest_materialised_release_published_by_as_of(tmp_path: Path) -> None:
    calls: list[str] = []
    cc = CommonCrawlDomainRanks(root=tmp_path, client=ranks_client(calls))
    report = cc.materialise([SITE, "googleapis.com", "never-linked.example", "smartcrewhq.com"], [OLD_RELEASE, NEW_RELEASE])
    assert report[OLD_RELEASE]["found"] == 2 and report[NEW_RELEASE]["found"] == 2
    assert len(calls) == 2
    found = cc.lookup([SITE, "never-linked.example", "smartcrewhq.com"], date(2025, 5, 4))
    assert found[SITE].snapshot_date == date(2025, 4, 1)
    assert found[SITE].value["snapshot_id"] == OLD_RELEASE
    assert found[SITE].value["hc_pos"] == 5123456.0 and found[SITE].value["n_hosts"] == 2.0
    assert found[SITE].value["newer_release_not_materialised"] == 0.0
    assert found["never-linked.example"].value["present"] == 0.0
    assert found["never-linked.example"].value["hc_pos"] is None
    assert "smartcrewhq.com" not in found
    assert cc.lookup([SITE], date(2025, 5, 5))[SITE].value["snapshot_id"] == NEW_RELEASE
    assert cc.lookup([SITE], date(2025, 3, 31)) == {}
    coverage = strength_lists.coverage_report([SITE, "googleapis.com", "never-linked.example"], tmp_path)
    assert [(row["snapshot"], row["searched"], row["found"]) for row in coverage] == [(OLD_RELEASE, 3, 2), (NEW_RELEASE, 3, 2)]


# ---------------------------------------------------------------------------
# The guard


class _FutureSource:
    name = "planted_future"

    def snapshots(self) -> list[Snapshot]:
        return [Snapshot("future", date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1))]

    def lookup(self, sites, as_of):
        return {site: Dated(value={"present": 1.0}, source=self.name, snapshot_date=date(2026, 2, 1)) for site in sites}


def test_planted_future_snapshot_is_refused_by_the_guard() -> None:
    guard = AsOfGuard(date(2025, 6, 1))
    with pytest.raises(LeakError):
        strength_features([SITE], date(2025, 6, 1), guard, sources=[_FutureSource()])
    live = AsOfGuard(date(2026, 3, 1))
    out = strength_features([SITE], date(2026, 3, 1), live, sources=[_FutureSource()])
    assert out[SITE]["planted_future_present"] == 1.0
    assert out[SITE]["planted_future_snapshot_age_days"] == 28.0


def test_guard_date_must_match_feature_date() -> None:
    with pytest.raises(ValueError):
        strength_features([SITE], date(2025, 6, 1), AsOfGuard(date(2025, 6, 2)), sources=[])


def test_undated_majestic_is_refused_in_a_backtest_and_allowed_live() -> None:
    majestic = MajesticToday(path=FIXTURES / "majestic_sample.csv")
    assert majestic.snapshots()[0].published is None
    found = majestic.lookup([SITE, "google.com"])
    assert found[SITE].snapshot_date is None
    assert found[SITE].value["ref_subnets"] == 12.0
    with pytest.raises(LeakError):
        strength_features([SITE], date(2025, 6, 1), AsOfGuard(date(2025, 6, 1)), sources=[majestic])
    today = date(2026, 11, 22)
    live = strength_features([SITE, "unknown-site.org"], today, AsOfGuard(today, allow_undated=True), sources=[majestic])
    assert live[SITE]["majestic_rank"] == 999999.0
    assert live[SITE]["majestic_snapshot_age_days"] is None
    assert live["unknown-site.org"]["majestic_rank"] is None  # missing stays None, never 0


def test_strength_features_flat_floats_with_none_for_missing(tmp_path: Path) -> None:
    cc = CommonCrawlDomainRanks(root=tmp_path, client=ranks_client([]))
    cc.materialise([SITE, "googleapis.com"], [OLD_RELEASE])
    as_of = date(2025, 4, 15)
    guard = AsOfGuard(as_of)
    out = strength_features([SITE, "not-searched.net"], as_of, guard, sources=[cc])
    assert out[SITE]["cc_present"] == 1.0
    assert out[SITE]["cc_snapshot_age_days"] == 14.0
    assert out[SITE]["cc_newer_release_not_materialised"] == 0.0
    later = strength_features([SITE], date(2025, 6, 1), AsOfGuard(date(2025, 6, 1)), sources=[cc])
    assert later[SITE]["cc_newer_release_not_materialised"] == 1.0  # newer releases were published but not streamed
    assert out[SITE]["cc_hc_pos_pct"] == pytest.approx(5123456 / 135153877)
    assert set(out["not-searched.net"].values()) == {None}
    assert all(value is None or isinstance(value, float) for row in out.values() for value in row.values())
    assert guard.knowable_table() == [{"source": "commoncrawl_domain_ranks", "newest_snapshot": "2025-04-01"}]


# ---------------------------------------------------------------------------
# Parsing


def test_reversed_domain_parsing_of_common_crawl_rows() -> None:
    assert reverse_domain("example-shop.co.uk") == "uk.co.example-shop"
    assert reverse_domain("uk.co.example-shop") == "example-shop.co.uk"
    assert parse_rank_line("#harmonicc_pos\t#harmonicc_val\t#pr_pos\t#pr_val\t#host_rev\t#n_hosts") is None
    row = parse_rank_line(b"1\t3.2240352E7\t2\t0.017687114512854166\tcom.googleapis\t3753\n")
    assert row == ("googleapis.com", 1, 3.2240352e7, 2, 0.017687114512854166, 3753)
    lines = list(stream_gzip_lines([(FIXTURES / "cc_domain_ranks_sample.txt.gz").read_bytes()]))
    wanted = {reverse_domain(site).encode() for site in wanted_sites([SITE, "google.com", "smartcrewhq.com"])}
    hits = list(filter_rank_lines(lines, wanted))
    assert [hit[0] for hit in hits] == ["google.com", SITE]
    assert hits[1][1:] == (5123456, 3.501e6, 7123456, 1.2e-9, 2)


def test_gzip_stream_handles_split_chunks_and_multiple_members() -> None:
    body = b"#h\n1\t1.0\t1\t0.1\tcom.a\t1\n"
    data = gzip.compress(body) + gzip.compress(b"2\t0.5\t2\t0.05\tcom.b\t3\n")
    chunks = [data[i:i + 7] for i in range(0, len(data), 7)]
    lines = [line for line in stream_gzip_lines(chunks) if line]
    assert lines == [b"#h", b"1\t1.0\t1\t0.1\tcom.a\t1", b"2\t0.5\t2\t0.05\tcom.b\t3"]


def test_crux_bucket_normalisation_and_scheme_detection() -> None:
    assert [crux_coarse_bucket(b) for b in (1000, 5000, 10000, 50000, 100000, 500000, 1000000)] == \
        [1000, 10000, 10000, 100000, 100000, 1000000, 1000000]
    assert crux_coarse_bucket(None) is None
    assert crux_coarse_bucket(2000000) is None
    with gzip.open(FIXTURES / "crux_202102_sample.csv.gz", "rt") as handle:
        old, old_scheme = parse_crux_rows(handle, {SITE})
    with gzip.open(FIXTURES / "crux_202510_sample.csv.gz", "rt") as handle:
        new, new_scheme = parse_crux_rows(handle, {SITE})
    assert old_scheme == "coarse" and new_scheme == "fine"
    assert old[SITE] == (100000, 2)  # best of two origins
    assert new[SITE] == (50000, 2)  # shop. subdomain and :8080 origin fold into the site
    assert crux_coarse_bucket(new[SITE][0]) == crux_coarse_bucket(old[SITE][0]) == 100000
    assert crux_month_published("202510") == date(2025, 11, 15)


def test_crux_materialise_and_lookup(tmp_path: Path) -> None:
    crux = CruxTopLists(root=tmp_path, client=failing_client())
    for month, name in (("202102", "crux_202102_sample.csv.gz"), ("202510", "crux_202510_sample.csv.gz")):
        target = crux.public_dir / f"{month}.csv.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / name, target)
    report = crux.materialise([SITE, "mangalib.me"], ["2021-02", "2025-10"])
    assert report["202510"]["scheme"] == "fine"
    assert crux.lookup([SITE], date(2025, 11, 14))[SITE].value["snapshot_id"] == "crux-202102"
    value = crux.lookup([SITE], date(2025, 11, 15))[SITE]
    assert value.value["bucket"] == 50000.0 and value.value["bucket_coarse"] == 100000.0
    assert crux.materialise([SITE], ["202510"])["202510"]["status"] == "cached"


def test_tranco_lookup_uses_list_creation_date(tmp_path: Path) -> None:
    record = {"list_id": "L7NK4", "available": True, "failed": False, "download": "https://tranco-list.eu/download/L7NK4/1000000",
              "created_on": "2025-11-01T22:00:01.998999",
              "configuration": {"endDate": "2025-11-01", "startDate": "2025-10-03", "providers": ["crux", "farsight", "majestic", "radar", "umbrella"]}}
    csv_bytes = (FIXTURES / "tranco_L7NK4_sample.csv").read_bytes()
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.startswith("/api/lists/date/"):
            return httpx.Response(200, json=record)
        return httpx.Response(200, content=csv_bytes)

    tranco = TrancoByDate(root=tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    tranco.limiter.min_interval = 0.0
    tranco.materialise([SITE, "google.com"], ["2025-11-01"])
    assert tranco.lookup([SITE], date(2025, 10, 31)) == {}
    assert tranco.lookup([SITE], date(2025, 11, 1))[SITE].value["rank"] == 777777.0
    tranco.client = failing_client()
    assert tranco.materialise([SITE], ["2025-11-01"])["2025-11-01"]["status"] == "cached"


def test_opr_materialise_from_local_zip_and_survivorship_field(tmp_path: Path) -> None:
    opr = OpenPageRankHistory(root=tmp_path, client=failing_client())
    opr.download_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(opr.download_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(FIXTURES / "opr_history_sample.csv", arcname="top10milliondomains-history.csv")
    report = opr.materialise(["interface-eu.org", "googleapis.com", "absent-site.com"])
    assert report["found"] == 2
    assert opr.survivorship_limited is True
    months = opr.months()
    assert len(months) == 51 and months[0] == "2018-01" and months[-1] == "2026-10"
    history = opr.history()
    assert min(history["interface-eu.org"]) == "2024-06"
    early = opr.lookup(["interface-eu.org"], date(2024, 6, 30))["interface-eu.org"]
    assert early.value["snapshot_id"] == "opr-2024-05" and early.value["opr"] is None and early.value["present"] == 0.0
    later = opr.lookup(["interface-eu.org", "absent-site.com"], date(2024, 7, 1))
    assert later["interface-eu.org"].value["opr"] == 0.53
    assert later["interface-eu.org"].snapshot_date == date(2024, 7, 1)
    assert later["interface-eu.org"].value["survivorship_limited"] == 1.0
    assert later["absent-site.com"].value["present"] == 0.0
    assert opr.materialise(["interface-eu.org"])["status"] == "cached"


# ---------------------------------------------------------------------------
# Resumability


def test_second_materialise_does_no_network_work_when_cache_is_complete(tmp_path: Path) -> None:
    calls: list[str] = []
    CommonCrawlDomainRanks(root=tmp_path, client=ranks_client(calls)).materialise([SITE, "google.com"], [OLD_RELEASE])
    assert len(calls) == 1
    again = CommonCrawlDomainRanks(root=tmp_path, client=failing_client())
    report = again.materialise([SITE, "www.google.com"], [OLD_RELEASE])
    assert report[OLD_RELEASE] == {"status": "cached", "asked": 2, "streamed_bytes": 0, "seconds": 0.0}
    # Asking for one more site streams again for the missing site only, and keeps the old rows.
    more = CommonCrawlDomainRanks(root=tmp_path, client=ranks_client(calls))
    report = more.materialise([SITE, "google.com", "facebook.com"], [OLD_RELEASE])
    assert report[OLD_RELEASE]["asked"] == 1 and report[OLD_RELEASE]["found"] == 1
    table = pq.read_table(tmp_path / "cache" / "strength" / "commoncrawl" / f"{OLD_RELEASE}.parquet").to_pydict()
    assert sorted(table["site"]) == sorted([SITE, "google.com", "facebook.com"])
    sources = json.loads((tmp_path / "cache" / "strength" / "commoncrawl" / "SOURCES.json").read_text())
    assert sources[OLD_RELEASE]["sha256_of_stream"]


def test_stream_resumes_with_a_range_request_after_a_break(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strength_lists.time, "sleep", lambda seconds: None)
    payload = (FIXTURES / "cc_domain_ranks_sample.txt.gz").read_bytes()
    ranges: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers.get("range"))
        if "range" not in request.headers:
            return httpx.Response(200, headers={"content-length": str(len(payload)), "etag": '"x"'}, stream=_Stream(payload, cut=100))
        start = int(request.headers["range"].split("=")[1].rstrip("-"))
        return httpx.Response(206, stream=_Stream(payload[start:]), headers={"content-length": str(len(payload) - start)})

    cc = CommonCrawlDomainRanks(root=tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)))
    report = cc.materialise([SITE, "google.com"], [OLD_RELEASE])
    assert ranges == [None, "bytes=100-"]
    assert report[OLD_RELEASE]["found"] == 2 and report[OLD_RELEASE]["streamed_bytes"] == len(payload)


def test_download_resumable_continues_a_partial_file(tmp_path: Path) -> None:
    payload = b"0123456789" * 50
    dest = tmp_path / "file.bin"
    (tmp_path / "file.bin.part").write_bytes(payload[:120])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=120-"
        return httpx.Response(206, content=payload[120:], headers={"content-length": str(len(payload) - 120)})

    meta = strength_lists.download_resumable(httpx.Client(transport=httpx.MockTransport(handler)), "https://example.org/f", dest)
    assert dest.read_bytes() == payload and meta["bytes"] == len(payload)
    assert strength_lists.download_resumable(failing_client(), "https://example.org/f", dest)["sha256"] == meta["sha256"]
    # A complete .part left by a crash before the rename: the server answers 416 and the file is kept.
    done = tmp_path / "done.bin"
    (tmp_path / "done.bin.part").write_bytes(payload)

    def full(request: httpx.Request) -> httpx.Response:
        return httpx.Response(416, headers={"content-range": f"bytes */{len(payload)}"})

    assert strength_lists.download_resumable(httpx.Client(transport=httpx.MockTransport(full)), "https://example.org/g", done)["bytes"] == len(payload)


# ---------------------------------------------------------------------------
# Panel sites


def test_extract_panel_sites_streams_main_domain_of_organic_items(tmp_path: Path) -> None:
    record = {"keyword": "x", "location": 2840, "language": "en", "serp_info_history": [
        {"time_update": "2026-03-22T00:16:41Z", "serp": [
            {"type": "organic", "domain": "www.tipsyelves.com", "main_domain": "tipsyelves.com", "rank_group": 1},
            {"type": "organic", "domain": "shop.example-shop.co.uk", "main_domain": "example-shop.co.uk", "rank_group": 2},
            {"type": "organic", "domain": "www.smartcrewhq.com", "main_domain": "smartcrewhq.com", "rank_group": 3},
            {"type": "images", "domain": "images.example", "main_domain": "images-only.com"},
        ]}]}
    path = tmp_path / "US_en.json.gz"
    with gzip.open(path, "wt") as handle:
        handle.write(json.dumps(record) + "\n")
    sites, stats = extract_panel_sites([path])
    assert sites == {"tipsyelves.com", SITE}
    assert stats["excluded"] == 1 and stats["items"] == 3


# ---------------------------------------------------------------------------
# RDAP and start dates


def rdap_client(calls: list[str]) -> httpx.Client:
    bootstrap = (FIXTURES / "rdap_dns_sample.json").read_bytes()
    example = (FIXTURES / "rdap_example_com.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "data.iana.org":
            return httpx.Response(200, content=bootstrap)
        if str(request.url) == "https://rdap.verisign.com/com/v1/domain/example.com":
            return httpx.Response(200, content=example, headers={"content-type": "application/rdap+json"})
        if "busy-site" in str(request.url):
            return httpx.Response(429)
        return httpx.Response(404, json={"errorCode": 404})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rdap_parsing_and_bootstrap() -> None:
    payload = json.loads((FIXTURES / "rdap_example_com.json").read_text())
    parsed = domain_age.parse_rdap(payload)
    assert parsed["registration"] == "1995-08-14T04:00:00Z"
    assert parsed["expiration"] == "2027-08-13T04:00:00Z"
    assert "entities" not in parsed
    boot = domain_age.parse_bootstrap(json.loads((FIXTURES / "rdap_dns_sample.json").read_text()))
    assert domain_age.rdap_url("example.com", boot) == "https://rdap.verisign.com/com/v1/domain/example.com"
    assert domain_age.rdap_url("example.co.uk", boot) == "https://rdap.nominet.uk/uk/domain/example.co.uk"
    assert boot["kg"] == "https://rdap.cctld.kg/"  # an HTTP-only server is asked over HTTPS
    assert domain_age.rdap_url("example.se", boot) is None


def test_rdap_lookup_caches_records_and_failures_stay_missing(tmp_path: Path) -> None:
    calls: list[str] = []
    rdap = domain_age.RdapLookup(root=tmp_path, client=rdap_client(calls), sleep=lambda seconds: None)
    records = rdap.lookup(["example.com", "gone-domain.com", "example.se", "busy-site.org"])
    assert records["example.com"]["status"] == "ok"
    assert records["gone-domain.com"]["status"] == "not_found"
    assert records["example.se"]["status"] == "no_rdap_service"
    assert records["busy-site.org"]["status"] == "rate_limited"
    assert rdap._interval["rdap.publicinterestregistry.org"] == 2.0  # that server is now asked more slowly
    boot = domain_age.parse_bootstrap(json.loads((FIXTURES / "rdap_dns_sample.json").read_text()))
    order = domain_age._interleave_by_server(["a.com", "b.com", "c.com", "x.org", "y.co.uk"], boot)
    assert order == ["y.co.uk", "x.org", "a.com", "b.com", "c.com"]
    dates = rdap.registration_dates(["example.com", "gone-domain.com"])
    assert set(dates) == {"example.com"}
    assert dates["example.com"].snapshot_date == date(1995, 8, 14)
    assert dates["example.com"].source == "rdap_registration"
    stored = (tmp_path / "cache" / "domain_age" / "rdap.jsonl").read_text()
    assert "entities" not in stored
    # Final answers are never asked again. Only the rate-limited site is retried.
    calls.clear()
    rdap.lookup(["example.com", "gone-domain.com", "example.se", "busy-site.org"])
    assert calls == ["https://rdap.publicinterestregistry.org/rdap/domain/busy-site.org"]
    rdap_offline = domain_age.RdapLookup(root=tmp_path, client=failing_client(), sleep=lambda seconds: None)
    assert rdap_offline.lookup(["example.com"])["example.com"]["status"] == "ok"


def _seen(day: str, source: str, censored: bool = False) -> Dated:
    return Dated(value={"date": day, "kind": "first_seen", "left_censored": censored}, source=source, snapshot_date=date.fromisoformat(day))


def _registered(day: str) -> Dated:
    return Dated(value={"date": day, "kind": "registration"}, source="rdap_registration", snapshot_date=date.fromisoformat(day))


def test_best_start_date_precedence_and_flags() -> None:
    both = domain_age.best_start_date("a.com", [_registered("2024-01-01"), _seen("2024-04-30", "opr"), _seen("2024-06-15", "cc")])
    assert both.method == "between_registration_and_first_seen"
    assert both.lower == date(2024, 1, 1) and both.upper == date(2024, 4, 30)
    assert both.estimate == date(2024, 3, 1) and both.uncertainty_days == 60
    assert "registration_is_not_launch" in both.flags and both.kind == "PREDICTED"

    rereg = domain_age.best_start_date("b.com", [_registered("2025-03-01"), _seen("2019-05-31", "opr")])
    assert "possibly_reregistered" in rereg.flags
    assert rereg.estimate == date(2025, 3, 1)

    dormant = domain_age.best_start_date("c.com", [_registered("2010-01-01"), _seen("2024-06-30", "cc")])
    assert "registered_long_before_first_seen" in dormant.flags
    assert dormant.estimate == date(2024, 4, 1)

    censored = domain_age.best_start_date("d.com", [_seen("2018-01-31", "opr", censored=True)])
    assert censored.estimate is None and censored.upper == date(2018, 1, 31) and "left_censored" in censored.flags

    only_seen = domain_age.best_start_date("e.com", [_seen("2025-06-30", "opr")])
    assert only_seen.estimate == date(2025, 4, 1) and only_seen.uncertainty_days == domain_age.FIRST_SEEN_UNCERTAINTY_DAYS

    monthly = domain_age.best_start_date("h.com", [Dated(value={"date": "2024-06-30", "kind": "first_seen", "absent_by": "2024-05-31",
                                                             "left_censored": False}, source="opr", snapshot_date=date(2024, 7, 1))])
    assert monthly.method == "between_last_absent_and_first_seen"
    assert monthly.estimate == date(2024, 5, 1) and monthly.uncertainty_days == 60

    nothing = domain_age.best_start_date("f.com", [])
    assert nothing.estimate is None and nothing.method == "no_evidence"

    limited = domain_age.best_start_date("g.com", [_registered("2024-01-01"), _seen("2024-04-30", "opr")], as_of=date(2024, 2, 1))
    assert limited.method == "registration_only" and limited.ignored_after_as_of == ("opr",)


def test_first_seen_from_materialised_lists(tmp_path: Path) -> None:
    cc = CommonCrawlDomainRanks(root=tmp_path, client=ranks_client([]))
    cc.materialise(["google.com"], [OLD_RELEASE])  # the shop site is not searched in the old release
    cc.materialise([SITE, "google.com", "never-linked.example"], [NEW_RELEASE])
    first = domain_age.cc_first_release([SITE, "google.com", "never-linked.example"], tmp_path)
    assert first[SITE].value["release"] == NEW_RELEASE and first[SITE].value["left_censored"] is True
    assert first[SITE].snapshot_date == date(2025, 5, 5)
    assert first["google.com"].value["release"] == OLD_RELEASE and first["google.com"].value["absent_by"] is None
    assert "never-linked.example" not in first

    opr = OpenPageRankHistory(root=tmp_path, client=failing_client())
    opr.download_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(opr.download_path, "w") as archive:
        archive.write(FIXTURES / "opr_history_sample.csv", arcname="top10milliondomains-history.csv")
    opr.materialise(["interface-eu.org", "googleapis.com"])
    months = domain_age.opr_first_month(["interface-eu.org", "googleapis.com"], tmp_path)
    assert months["interface-eu.org"].value["first_month"] == "2024-06"
    assert months["interface-eu.org"].value["date"] == "2024-06-30"
    assert months["interface-eu.org"].value["absent_by"] == "2024-05-31"
    assert months["interface-eu.org"].snapshot_date == date(2024, 7, 1)
    assert months["googleapis.com"].value["left_censored"] is True
    estimate = domain_age.best_start_date("interface-eu.org", root=tmp_path)
    assert estimate.upper == date(2024, 6, 30) and "survivorship_limited" in estimate.flags


# ---------------------------------------------------------------------------
# Google update calendar


def _update(name: str, kind: str, start: str, end: str | None) -> updates.RankingUpdate:
    return updates.RankingUpdate(incident_id=name, name=name, kind=kind, start=start, end=end, listed_day=start[:10],
                                 listed_duration="", status="closed" if end else "open", precision="incident_page", url="")


def test_update_overlap_arithmetic() -> None:
    calendar = [
        _update("core-a", "core", "2025-03-13T16:00:00Z", "2025-03-27T05:00:00Z"),
        _update("spam-b", "spam", "2025-08-26T17:00:00Z", "2025-09-22T07:00:00Z"),
        _update("core-open", "core", "2026-11-10T18:07:00Z", None),
    ]
    assert [u.name for u in updates.overlaps("2025-03-27", "2025-04-30", calendar)] == ["core-a"]  # end day counts
    assert updates.overlaps("2025-03-28", "2025-08-25", calendar) == []
    assert [u.name for u in updates.overlaps("2025-01-01", "2025-12-31", calendar)] == ["core-a", "spam-b"]
    assert [u.name for u in updates.overlaps("2025-01-01", "2025-12-31", calendar, kinds=["spam"])] == ["spam-b"]
    assert [u.name for u in updates.overlaps("2030-01-01", "2030-02-01", calendar)] == ["core-open"]
    assert updates.overlaps("2030-01-01", "2030-02-01", calendar, open_until=date(2026, 11, 22)) == []
    assert [u.name for u in updates.overlaps(date(2026, 11, 10), date(2026, 11, 10), calendar)] == ["core-open"]
    with pytest.raises(ValueError):
        updates.overlaps("2025-02-01", "2025-01-01", calendar)


def test_status_dashboard_parsing_from_saved_pages() -> None:
    rows = updates.parse_history_html((FIXTURES / "google_status_history_sample.html").read_bytes())
    names = [row["name"] for row in rows]
    assert names == ["November 2026 core update", "September 2026 spam update", "December 2021 product reviews update"]
    assert rows[0]["status"] == "Informational status" and rows[1]["status"] == "Available status"
    times = updates.parse_incident_html((FIXTURES / "google_status_incident_sample.html").read_bytes())
    assert times == [datetime(2025, 6, 30, 14, 34, tzinfo=timezone.utc), datetime(2025, 7, 17, 8, 18, tzinfo=timezone.utc)]
    june = updates.build_update({"incident_id": "riq", "name": "June 2025 core update", "listed_day": "30 Jun 2025",
                                 "listed_duration": "16 days, 18 hours", "status": "Available status"}, times)
    assert (june.kind, june.start, june.end, june.precision) == ("core", "2025-06-30T14:34:00Z", "2025-07-17T08:18:00Z", "incident_page")
    backfilled = updates.build_update(rows[2], [datetime(2023, 3, 22, 13, 31, tzinfo=timezone.utc)])
    assert backfilled.start == "2021-12-01T00:00:00Z" and backfilled.end == "2021-12-21T00:00:00Z"
    assert backfilled.precision == "day_plus_duration" and backfilled.kind == "reviews"
    open_core = updates.build_update(rows[0], None)
    assert open_core.end is None and open_core.status == "open"
    assert updates.parse_duration("19 hours, 30 minutes").total_seconds() == 19.5 * 3600
    assert updates.parse_duration("Information") is None


def test_packaged_calendar_loads_offline(tmp_path: Path) -> None:
    calendar = updates.load_updates(root=tmp_path)  # no cache under tmp_path: the packaged copy is used
    assert len(calendar) >= 41
    kinds = {item.kind for item in calendar}
    assert {"core", "spam"} <= kinds
    assert calendar[0].start_date == date(2021, 12, 1)
    core_2025 = [u.name for u in updates.overlaps("2025-01-01", "2025-12-31", calendar, kinds=["core"])]
    assert core_2025 == ["March 2025 core update", "June 2025 core update", "December 2025 core update"]
    rows = updates.scoreboard_rows(calendar[:1])
    assert rows == [{"name": "December 2021 product reviews update", "kind": "reviews", "started_on": "2021-12-01",
                     "ended_on": "2021-12-21", "source_url": "https://status.search.google.com/incidents/hgwP7ei5H1uKbYSeZrBZ"}]
