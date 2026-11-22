"""Tests for the result-page panel, the DataForSEO sample parsers and the noise sets.

All fixtures are tiny hand-written files in the real layouts under
tests/fixtures/forecast_panel/. No network, no downloaded data.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest

from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.sources import dfs_samples as dfs
from aeo.ranksim.forecast.sources import noise_sets as ns
from aeo.ranksim.forecast.sources import panel as pn

FIX = Path(__file__).parent / "fixtures" / "forecast_panel"


def _gz(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "rb") as handle, gzip.open(dst, "wb") as out:
        out.write(handle.read())
    return dst


@pytest.fixture()
def sample_root(tmp_path: Path) -> Path:
    """The fixtures laid out like the real download root."""
    root = tmp_path / "dataforseo_samples"
    _gz(FIX / "history_US_en.jsonl", root / "google_history/serp_advanced/US_en.json.gz")
    _gz(FIX / "serp_advanced_US_en.jsonl", root / "google/serp_advanced/US_en.json.gz")
    _gz(FIX / "keywords_US_en.jsonl", root / "google/keywords/US_en.json.gz")
    _gz(FIX / "keywords_history_US_en.jsonl", root / "google_history/keywords/US_en.json.gz")
    _gz(FIX / "whois.jsonl", root / "whois/full/data.json.gz")
    return root


def _history_rows(root: Path) -> tuple[list[dict], dfs.ParseStats]:
    stats = dfs.ParseStats()
    rows = list(dfs.iter_history_rows(root / "google_history/serp_advanced/US_en.json.gz", stats))
    return rows, stats


def test_keyword_id_is_sha256_prefix_of_locale_and_keyword():
    expected = hashlib.sha256("US_en|best widget".encode()).hexdigest()[:16]
    assert dfs.keyword_id("US_en", "best widget") == expected
    assert dfs.keyword_id("GB_en", "best widget") != expected


def test_locale_from_path_refuses_other_names():
    assert dfs.locale_from_path("/x/US_en.json.gz") == "US_en"
    with pytest.raises(ValueError):
        dfs.locale_from_path("/x/data.json.gz")


def test_history_parser_reads_real_layout(sample_root: Path):
    rows, stats = _history_rows(sample_root)
    assert stats.records == 3 and stats.bad_lines == 1
    assert stats.records_without_pages == 1
    assert stats.snapshots == 5
    assert stats.dropped_no_rank == 1
    assert stats.dropped_excluded_site == 1
    assert all(row["site"] != "smartcrewhq.com" for row in rows)
    first = [row for row in rows if row["snapshot_time"] == datetime(2025, 6, 1, 10, 0, 0, 123456, tzinfo=timezone.utc)]
    assert [row["rank"] for row in first] == [1, 2, 4, 5, 60]
    beta = next(row for row in first if row["rank"] == 2)
    assert beta["site"] == "beta.co.uk" and beta["domain"] == "blog.beta.co.uk" and beta["rank_absolute"] == 3
    alpha = first[0]
    assert alpha["previous_rank_absolute"] is None  # 0 is not a rank
    assert alpha["is_new"] is True
    assert next(row for row in first if row["rank"] == 4)["previous_rank_absolute"] == 7
    assert alpha["kd"] == 7.0  # the difficulty recorded with this page, not the record-level one
    # No monthly volume was known on 2025-06-01: May counts from 2025-07-16, April (null) from 2025-06-15.
    assert alpha["search_volume"] is None
    assert alpha["main_domain_rank"] == 300 and alpha["page_rank"] == 0
    # The intent label is dated 2025-12-27 and the record's etv comes from the 2026-04-12 headline volume:
    # neither existed on this 2025-06-01 page, so both are null on it.
    assert alpha["intent"] is None and alpha["etv"] is None and alpha["location_code"] == 2840
    assert alpha["poison_risk"] is False
    poisoned = [row for row in rows if row["keyword"] == "poisoned page"]
    assert len(poisoned) == 1 and poisoned[0]["poison_risk"] is True
    assert poisoned[0]["search_volume"] is None and poisoned[0]["kd"] is None
    assert set(rows[0]) == set(dfs.PANEL_ROW_FIELDS)


def test_volume_known_at_uses_the_latest_month_already_known_and_keeps_missing_missing():
    assert dfs.VOLUME_PUBLICATION_LAG_DAYS == 45
    info = {"history": {"202504": None, "202505": 40, "202506": 50}}
    # May counts as known from 2025-06-01 + 45 days = 2025-07-16, June from 2025-08-15.
    assert dfs.volume_known_at(info, date(2025, 7, 16)) == 40
    assert dfs.volume_known_at(info, date(2025, 8, 14)) == 40
    assert dfs.volume_known_at(info, date(2025, 8, 15)) == 50
    # Before May is known the latest known month is April, which is missing: None, never May.
    assert dfs.volume_known_at(info, date(2025, 7, 15)) is None
    assert dfs.volume_known_at(info, date(2025, 6, 15)) is None
    assert dfs.volume_known_at(info, date(2025, 5, 2)) is None
    assert dfs.volume_known_at(None, date(2025, 6, 1)) is None


def test_snapshot_parser_drops_a_duplicate_item(sample_root: Path):
    stats = dfs.ParseStats()
    rows = list(dfs.iter_snapshot_rows(sample_root / "google/serp_advanced/US_en.json.gz", stats))
    assert len(rows) == 2 and stats.dropped_duplicate == 1
    assert rows[0]["dataset"] == dfs.DATASET_SNAPSHOT and rows[0]["kd"] == 3.0


def test_labs_response_gives_the_same_rows_as_the_sample():
    stats = dfs.ParseStats()
    rows = list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json", stats, locale="US_en"))
    assert len(stats.failed_tasks) == 1 and stats.failed_tasks[0].startswith("t-bad: 40501")
    assert stats.snapshots == 2 and len(rows) == 3
    alpha = rows[0]
    assert alpha["keyword_id"] == dfs.keyword_id("US_en", "best widget")
    assert alpha["snapshot_time"] == datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
    assert alpha["is_new"] is True and alpha["previous_rank_absolute"] is None
    assert rows[1]["previous_rank_absolute"] == 7 and rows[1]["is_new"] is False
    assert alpha["kd"] is None and alpha["search_volume"] is None
    assert rows[2]["poison_risk"] is True
    sample = next(r for r in dfs.iter_history_rows(_tmp_history(), dfs.ParseStats(), locale="US_en") if r["rank"] == 1)
    for name in ("locale", "keyword", "keyword_id", "rank", "rank_absolute", "domain", "site", "url", "title", "is_new", "main_domain_rank", "page_rank"):
        assert alpha[name] == sample[name], name
    # The sample's etv is computed from its 2026-04 headline volume, so it is null on this 2025-06 page.
    # The Labs etv fails closed the same way until a real response proves it is page dated.
    assert sample["etv"] is None and alpha["etv"] is None
    assert set(alpha) == set(sample)


def _tmp_history() -> Path:
    import tempfile

    folder = Path(tempfile.mkdtemp())
    return _gz(FIX / "history_US_en.jsonl", folder / "US_en.json.gz")


def test_labs_parser_refuses_to_guess_the_locale(tmp_path: Path):
    with pytest.raises(ValueError):
        list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json"))
    csv_path = tmp_path / "locales.csv"
    csv_path.write_text(
        "location_code,location_name,location_code_parent,country_iso_code,location_type,available_sources,language_name,language_code,keywords,serps\n"
        '2840,United States,,US,Country,"google,bing",English,en,1,1\n',
        encoding="utf-8",
    )
    locales = dfs.load_locales(csv_path)
    rows = list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json", locales=locales))
    assert rows[0]["locale"] == "US_en"
    with pytest.raises(ValueError):
        dfs.locale_for(2826, "en", locales)


def test_keyword_rows_plain_and_history(sample_root: Path):
    plain = list(dfs.iter_keyword_rows(sample_root / "google/keywords/US_en.json.gz", "dfs_keywords"))
    assert len(plain) == 1
    row = plain[0]
    assert row["snapshot_month"] == "2026-04" and row["search_volume"] == 90 and row["kd"] == 12.0
    assert json.loads(row["monthly_volume"]) == {"2025-04": None, "2025-05": 40, "2025-06": 50, "2025-07": 60, "2025-08": 70}
    hist = list(dfs.iter_keyword_rows(sample_root / "google_history/keywords/US_en.json.gz", "dfs_keywords_history"))
    assert [r["snapshot_month"] for r in hist] == ["2025-12", "2026-02"]
    assert [r["search_volume"] for r in hist] == [90, 110]


def test_whois_rows_copy_only_whitelisted_fields(sample_root: Path):
    stats = dfs.ParseStats()
    rows = list(dfs.iter_whois_rows(sample_root / "whois/full/data.json.gz", stats))
    assert len(rows) == 1 and stats.dropped_excluded_site == 1
    row = rows[0]
    assert set(row) == set(dfs.WHOIS_FIELDS)
    assert "someone@example.com" not in json.dumps(row, default=str)
    assert row["organic_pos_1"] == 1 and row["organic_etv"] == 146.1 and row["bl_referring_domains"] == 12
    assert row["paid_etv"] is None


def _build(sample_root: Path, tmp_path: Path) -> Path:
    out = tmp_path / "panel"
    files = pn.discover_sample_files(sample_root)
    pn.build_panel(list(files.history) + list(files.snapshot), out, root=sample_root)
    return out


def test_build_and_load_panel_round_trip(sample_root: Path, tmp_path: Path):
    out = _build(sample_root, tmp_path)
    assert (out / "dataset=dfs_history" / "locale=US_en" / "part-US_en.parquet").is_file()
    assert (out / "dataset=dfs_serp" / "locale=US_en" / "part-US_en.parquet").is_file()
    sources = json.loads((out / "SOURCES.json").read_text())
    entry = next(item for item in sources["files"] if "google_history" in item["path"])
    assert entry["url"] == dfs.SAMPLE_BASE_URL + "google_history/serp_advanced/US_en.json.gz"
    assert entry["rows"] == 14 and len(entry["sha256"]) == 64 and entry["fetched_at"].endswith("Z")
    panel = pn.load_panel(panel_dir=out)
    assert tuple(panel.columns) == pn.PANEL_COLUMNS
    assert len(panel) == 16
    assert "smartcrewhq.com" not in set(panel["site"])
    only = pn.load_panel(["keyword_id", "rank"], datasets=["dfs_history"], panel_dir=out)
    assert list(only.columns) == ["keyword_id", "rank"] and len(only) == 14
    assert pn.load_panel(["rank"], locales=["GB_en"], panel_dir=out).empty
    assert panel["previous_rank_absolute"].isna().sum() > 0  # missing stays missing
    with pytest.raises(ValueError):
        pn.load_panel(["nope"], panel_dir=out)


def test_summary_counts_and_gaps(sample_root: Path, tmp_path: Path):
    panel = pn.load_panel(panel_dir=_build(sample_root, tmp_path))
    result = pn.summary(panel)
    hist = result["datasets"]["dfs_history"]["overall"]
    assert hist["keywords"] == 2 and hist["snapshots"] == 5 and hist["rows"] == 14
    assert hist["poison_risk_pages"] == 1 and hist["poison_window_pages"] == 1
    assert hist["date_first"] == "2025-06-01" and hist["date_last"] == "2026-09-25"
    assert hist["gap_bins_consecutive"]["0.5-3"] == 1
    assert hist["gap_bins_consecutive"]["30-60"] == 2
    windows = hist["page_pairs_in_window_any_two_pages"]
    assert windows["30-60 (month 1 stand-in)"]["pairs"] == 3  # 06-01->07-16, 06-02->07-16, 07-16->09-01
    assert windows["75-110 (month 3 stand-in)"]["pairs"] == 2  # 06-01->09-01, 06-02->09-01
    assert hist["depth_bins"]["50-74"] == 1


def test_site_positions_clamps_and_keeps_the_raw_rank(sample_root: Path, tmp_path: Path):
    panel = pn.load_panel(panel_dir=_build(sample_root, tmp_path), datasets=["dfs_history"])
    sp = pn.site_positions(panel)
    assert not sp["poison_risk"].any()
    first = sp[sp["snapshot_time"] == sp["snapshot_time"].min()].set_index("site")
    assert first.loc["alpha.com", "rank100"] == 1 and first.loc["alpha.com", "best_url"] == "https://www.alpha.com/a"
    assert first.loc["deep.net", "rank100"] == 60 and first.loc["deep.net", "position"] == NT50
    assert first.loc["deep.net", "snapshot_depth"] == 60
    assert len(sp[sp["keyword"] == "poisoned page"]) == 0
    assert len(pn.site_positions(panel, drop_poison_window=False)) == len(sp) + 1


def test_snapshots_of_returns_pages_in_time_order(sample_root: Path, tmp_path: Path):
    panel = pn.load_panel(panel_dir=_build(sample_root, tmp_path), datasets=["dfs_history"])
    kid = dfs.keyword_id("US_en", "best widget")
    pages = pn.snapshots_of(panel, kid)
    assert len(pages) == 4
    assert [moment for moment, _ in pages] == sorted(moment for moment, _ in pages)
    assert list(pages[0][1]["rank"]) == [1, 2, 4, 5, 60]


def test_transitions_new_flags_and_absent_as_nt50(sample_root: Path, tmp_path: Path):
    panel = pn.load_panel(panel_dir=_build(sample_root, tmp_path), datasets=["dfs_history"])
    moves = pn.transitions(panel, 30, 60)
    assert moves[["time_a", "time_b"]].drop_duplicates().shape[0] == 3
    first_pair = moves[moves["time_a"] == moves["time_a"].min()]
    assert first_pair["a_is_first_page"].all()
    assert first_pair["new_at_a"].isna().all()  # the first page cannot tell new from old
    newcomer = first_pair.set_index("site").loc["newcomer.io"]
    assert newcomer["position_a"] == NT50 and pd.isna(newcomer["rank100_a"]) and newcomer["position_b"] == 2
    assert bool(newcomer["new_at_b"]) is True
    deep = first_pair.set_index("site").loc["deep.net"]
    assert deep["position_a"] == NT50 and deep["rank100_a"] == 60 and deep["position_b"] == NT50
    later = moves[(moves["site"] == "newcomer.io") & (moves["time_a"] == pd.Timestamp("2025-07-16T10:00:00Z"))].iloc[0]
    assert bool(later["new_at_a"]) is True and later["position_b"] == 1
    gamma_late = moves[(moves["site"] == "gamma.org") & (moves["time_a"] == pd.Timestamp("2025-07-16T10:00:00Z"))].iloc[0]
    assert bool(gamma_late["new_at_a"]) is False and gamma_late["position_b"] == NT50
    assert abs(float(later["gap_days"]) - 47.0) < 1e-9
    assert not moves["depth_ok"].any()  # the fixture pages stop before rank 50
    assert pn.transitions(panel, 200, 300).empty
    with pytest.raises(ValueError):
        pn.transitions(panel, 10, 5)


def test_new_site_report_counts_entries(sample_root: Path, tmp_path: Path):
    panel = pn.load_panel(panel_dir=_build(sample_root, tmp_path), datasets=["dfs_history"])
    report = pn.new_site_report(panel)
    assert report["site_keyword_entries"] == 1 and report["distinct_sites_entering"] == 1
    assert report["entry_band"]["1-10"] == 1
    assert report["entries_where_previous_page_reached_the_entry_rank"] == 1  # the page before reached rank 3
    assert report["month1_30_60_days"]["entries_followed"] == 0  # pages are too short to say absent = NT50


def test_agreement_arithmetic_by_hand():
    pair = ns.RepeatPair(cluster="q1", a={"a.com": 1, "b.com": 2, "c.com": 9, "d.com": 30}, b={"a.com": 2, "b.com": 5, "e.com": 10, "d.com": 31})
    top10 = ns.pair_units(pair, 10)
    assert [(u.site, u.position_a, u.position_b) for u in top10] == [
        ("a.com", 1, 2),
        ("b.com", 2, 5),
        ("c.com", 9, NT50),
        ("e.com", NT50, 10),
    ]
    report = ns.agreement_report([pair, ns.RepeatPair(cluster="q2", a={"a.com": 1}, b={"a.com": 1})], "test")
    assert report["headline_top10_within_2"]["units"] == 5 and report["headline_top10_within_2"]["agree"] == 2
    assert report["top50_within_2"]["units"] == 6 and report["top50_within_2"]["agree"] == 3
    assert report["identical_pairs"] == 1
    assert report["top10_entry_rate"]["count"] == 4 and report["top10_entry_rate"]["rate"] == 0.25
    assert report["top10_exit_rate"]["rate"] == 0.25
    # rule v1, A as forecast of B: (1->2) hit, (2->5) miss, (9->NT50) miss, (NT50->10) miss, (1->1) hit
    assert report["rule_v1_a_as_forecast_of_b_top10_units"]["agree"] == 2
    assert report["top50_by_band_of_a"]["21-50"]["units"] == 1 and report["top50_by_band_of_a"]["NT50"]["units"] == 1
    assert ns.agreement_report([], "none")["pairs"] == 0


def _deep_panel() -> pd.DataFrame:
    """Three pages of one keyword, 50 results each, the second one day after the first."""
    rows = []
    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    for offset, shift in ((0.0, 0), (1.0, 1), (45.0, 0)):
        moment = start + timedelta(days=offset)
        for rank in range(1, 51):
            site_no = rank if shift == 0 or rank > 2 else 3 - rank  # swap ranks 1 and 2 on the second page
            rows.append(
                {"dataset": "dfs_history", "locale": "US_en", "keyword_id": "k1", "snapshot_time": pd.Timestamp(moment), "rank": rank, "site": f"s{site_no}.com", "poison_window": False}
            )
    return pd.DataFrame(rows)


def test_dfs_repeat_pairs_keep_deep_pages_only():
    pairs, counts = ns.dfs_repeat_pairs(_deep_panel())
    assert counts == {"pairs_in_window": 1, "pairs_dropped_short_page": 0, "pairs_kept": 1}
    assert pairs[0].a["s1.com"] == 1 and pairs[0].b["s1.com"] == 2 and abs(pairs[0].gap_days - 1.0) < 1e-9
    report = ns.dfs_noise(_deep_panel())
    assert report["all_locales"]["headline_top10_within_2"]["share"] == 1.0
    assert report["english_locales"]["pairs"] == 1
    short = _deep_panel()
    short = short[~((short["snapshot_time"] == short["snapshot_time"].min()) & (short["rank"] > 40))]
    _, counts_short = ns.dfs_repeat_pairs(short)
    assert counts_short["pairs_dropped_short_page"] == 1 and counts_short["pairs_kept"] == 0


def test_dobbs_reader_and_pairs(tmp_path: Path):
    assert ns.parse_dobbs_name("06-26-22-PM-organic-links.tab") == (date(2022, 6, 26), "PM")
    run = ns.read_dobbs_run(FIX / "06-26-22-AM-organic-links.tab")
    assert run.pages[("abortion law", "GA", "Camilla")] == {"a.gov": 1, "b.com": 2, "c.org": 3}
    for name in ("06-26-22-AM-organic-links.tab", "06-26-22-PM-organic-links.tab", "06-27-22-PM-organic-links.tab"):
        shutil.copy(FIX / name, tmp_path / name)
    report = ns.dobbs_noise(tmp_path)
    am = report["morning_vs_evening_same_day"]
    # q1 Camilla: a 1->2 ok, b 2->1 ok, c 3->51 no, e 51->3 no ; q2 Camilla: a 1->1, d 2->2 ; Andalusia has no PM page
    assert am["pairs"] == 2 and am["headline_top10_within_2"]["units"] == 6 and am["headline_top10_within_2"]["agree"] == 4
    pm = report["evening_vs_next_evening"]
    assert pm["pairs"] == 2 and pm["headline_top10_within_2"]["agree"] == 5
    assert report["limits"] and report["pair_sets"]["am_pm"][0]["pairs"] == 2


def test_download_dobbs_resumes_and_records_sha256(tmp_path: Path):
    body = b"state\tcity\tquery\tdomain\tlink\tposition\n"
    (tmp_path / "06-26-22-AM-organic-links.tab.part").write_bytes(body[:10])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("range"))
        assert request.url.host == "dataverse.harvard.edu"
        return httpx.Response(206, content=body[10:])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ns.download_dobbs(tmp_path, client=client, files=((1, "06-26-22-AM-organic-links.tab", len(body)),))
    assert seen == ["bytes=10-"]
    assert (tmp_path / "06-26-22-AM-organic-links.tab").read_bytes() == body
    sources = json.loads((tmp_path / "SOURCES.json").read_text())
    assert sources["files"][0]["sha256"] == hashlib.sha256(body).hexdigest() and result["files"] == 1
    ns.download_dobbs(tmp_path, client=client, files=((1, "06-26-22-AM-organic-links.tab", len(body)),))
    assert seen == ["bytes=10-"]  # a complete file is not fetched again


def test_keyword_and_whois_tables_round_trip(sample_root: Path, tmp_path: Path):
    files = pn.discover_sample_files(sample_root)
    pairs = [(p, pn.DATASET_KEYWORDS) for p in files.keywords] + [(p, pn.DATASET_KEYWORDS_HISTORY) for p in files.keywords_history]
    pn.build_keyword_table(pairs, tmp_path / "kw", root=sample_root)
    keywords = pn.load_keywords(keywords_dir=tmp_path / "kw")
    assert len(keywords) == 3 and set(keywords["dataset"]) == {"dfs_keywords", "dfs_keywords_history"}
    long = pn.monthly_volume_long(keywords)
    assert long["volume"].isna().sum() == 3 and len(long) == 15
    pn.build_whois_table(files.whois[0], tmp_path / "whois", root=sample_root)
    whois = pn.load_whois(whois_dir=tmp_path / "whois")
    assert list(whois["site"]) == ["geminilaut.com"]
    assert (tmp_path / "whois" / "SOURCES.json").is_file()


def test_cli_build_and_summary(sample_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "panel"
    assert pn.main(["--root", str(sample_root), "--panel-dir", str(out), "build"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["rows"] == 16
    assert pn.main(["--root", str(sample_root), "--panel-dir", str(out), "summary"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["datasets"]["dfs_history"]["overall"]["snapshots"] == 5
    assert "new_sites_history" in payload


# ---------------------------------------------------------------------------
# poison_risk: no end date, the repair option clears it, old parquet still loads


def test_poison_risk_has_no_end_date_and_the_repair_option_clears_it():
    assert dfs.POISON_RISK_START == date(2026, 9, 13) and dfs.POISON_WINDOW_START == dfs.POISON_RISK_START
    assert dfs.poison_risk(date(2026, 9, 12)) is False
    assert dfs.poison_risk(date(2026, 9, 13)) is True
    assert dfs.poison_risk(date(2026, 11, 3)) is True  # a tracker still saw about 30% altered that day
    assert dfs.poison_risk(date(2027, 6, 1)) is True
    assert dfs.poison_risk(date(2026, 11, 3), repaired=True) is False
    assert dfs.in_poison_window(date(2026, 10, 20)) is True  # the old alias, now without the old end date
    assert "poison_risk" in dfs.PANEL_ROW_FIELDS and "poison_window" not in dfs.PANEL_ROW_FIELDS


def _old_panel(folder: Path) -> Path:
    """A panel part written before the rename: the column is poison_window and the old window was 20 Sep to 15 Oct."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    names = [name for name in pn.PANEL_COLUMNS if name != "poison_risk"]
    fields = [pn.PANEL_SCHEMA.field(name) for name in names] + [pa.field("poison_window", pa.bool_(), nullable=False)]
    rows = []
    for day, flag in ((date(2026, 9, 1), False), (date(2026, 9, 15), False), (date(2026, 10, 1), True), (date(2026, 11, 3), False)):
        row = {name: None for name in names}
        row.update(dataset="dfs_history", locale="US_en", keyword="old page", keyword_id="k-old",
                   snapshot_time=datetime(day.year, day.month, day.day, tzinfo=timezone.utc), snapshot_date=day,
                   rank=1, site="alpha.com", poison_window=flag)
        rows.append(row)
    target = folder / "dataset=dfs_history" / "locale=US_en"
    target.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(fields)), target / "part-old.parquet")
    return folder


def test_a_panel_written_with_the_old_column_name_still_loads(tmp_path: Path):
    folder = _old_panel(tmp_path / "panel")
    frame = pn.load_panel(["snapshot_date", "poison_risk"], panel_dir=folder).sort_values("snapshot_date")
    # 15 Sep and 3 Nov were outside the old window but are at risk under the new rule.
    assert list(frame["poison_risk"]) == [False, True, True, True]
    both = pn.load_panel(["keyword_id", "poison_window"], panel_dir=folder)
    assert list(both.columns) == ["keyword_id", "poison_window"] and both["poison_window"].sum() == 3
    full = pn.load_panel(panel_dir=folder)
    assert tuple(full.columns) == pn.PANEL_COLUMNS and full["poison_risk"].dtype == bool
    assert len(pn.site_positions(full)) == 1 and len(pn.site_positions(full, drop_poison_risk=False)) == 4
    assert len(pn.site_positions(full, drop_poison_window=False)) == 4  # the older argument name still works


def test_a_part_with_poison_risk_left_unset_is_filled_from_the_date(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = []
    for day in (date(2026, 9, 1), date(2026, 12, 1)):
        row = {name: None for name in pn.PANEL_COLUMNS}
        row.update(dataset="dfs_history", locale="US_en", keyword="k", keyword_id="k1",
                   snapshot_time=datetime(day.year, day.month, day.day, tzinfo=timezone.utc), snapshot_date=day,
                   rank=1, site="alpha.com", poison_window=False)  # a writer that still sets the old key only
        rows.append(row)
    target = tmp_path / "panel" / "dataset=dfs_history" / "locale=US_en"
    target.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pn.PANEL_SCHEMA), target / "part-new.parquet")
    frame = pn.load_panel(["snapshot_date", "poison_risk"], panel_dir=tmp_path / "panel").sort_values("snapshot_date")
    assert list(frame["poison_risk"]) == [False, True] and not frame["poison_risk"].isna().any()


# ---------------------------------------------------------------------------
# No value later than its page (review fixes)

REAL_SAMPLES = Path(__file__).resolve().parents[1] / "data" / "forecast" / "public" / "dataforseo_samples" / "google_history"
needs_real_samples = pytest.mark.skipif(not (REAL_SAMPLES / "serp_advanced" / "US_en.json.gz").exists(),
                                        reason="the downloaded DataForSEO sample files are not on this machine")


def _one_record(tmp_path: Path, pages: list[str], *, intent_dated: str | None, volume_dated: str | None) -> list[dict]:
    record = {
        "keyword": "dated widget", "location": 2840, "language": "en",
        "keyword_info": {"search_volume": 500, "time_update": volume_dated, "history": {"202501": 10, "202502": 20}},
        "search_intent_info": {"main_intent": "commercial", "last_updated_time": intent_dated},
        "serp_info_history": [
            {"time_update": day, "keyword_difficulty": 5, "items_count": 1, "se_results_count": 10,
             "serp": [{"type": "organic", "rank_group": 1, "rank_absolute": 1, "domain": "a.com", "main_domain": "a.com",
                       "url": "https://a.com/", "etv": 152.0}]}
            for day in pages
        ],
    }
    path = tmp_path / "US_en.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return sorted(dfs.iter_history_rows(path), key=lambda row: row["snapshot_time"])


def test_intent_and_etv_are_null_on_pages_older_than_their_record_date(tmp_path: Path):
    rows = _one_record(tmp_path, ["2025-03-01T00:00:00Z", "2025-06-01T00:00:00Z", "2025-09-01T00:00:00Z"],
                       intent_dated="2025-06-01T00:00:00Z", volume_dated="2025-08-15T00:00:00Z")
    assert [row["intent"] for row in rows] == [None, "commercial", "commercial"]  # on the label's own date it is known
    assert [row["etv"] for row in rows] == [None, None, 152.0]
    assert [row["kd"] for row in rows] == [5.0, 5.0, 5.0]  # the page's own difficulty is untouched
    undated = _one_record(tmp_path, ["2025-09-01T00:00:00Z"], intent_dated=None, volume_dated=None)
    assert undated[0]["intent"] is None and undated[0]["etv"] is None, "an undated record value is refused, not assumed"


def test_page_volume_counts_from_the_publication_lag_after_the_month(tmp_path: Path):
    rows = _one_record(tmp_path, ["2025-03-01T00:00:00Z", "2025-03-17T00:00:00Z", "2025-04-14T00:00:00Z", "2025-04-15T00:00:00Z"],
                       intent_dated=None, volume_dated=None)
    # January counts from 2025-02-01 + 45 days = 2025-03-18, February from 2025-04-15.
    assert [row["search_volume"] for row in rows] == [None, None, 10, 20]


def test_keyword_history_puts_the_current_kd_only_on_the_newest_month(sample_root: Path):
    hist = list(dfs.iter_keyword_rows(sample_root / "google_history/keywords/US_en.json.gz", "dfs_keywords_history"))
    assert [(r["snapshot_month"], r["kd"]) for r in hist] == [("2025-12", None), ("2026-02", 12.0)]
    plain = list(dfs.iter_keyword_rows(sample_root / "google/keywords/US_en.json.gz", "dfs_keywords"))
    assert plain[0]["kd"] == 12.0  # a single-row record keeps its kd


def test_site_positions_without_the_flag_column_fails_closed_on_the_date(tmp_path: Path):
    when = [datetime(2026, 9, 12, tzinfo=timezone.utc), datetime(2026, 10, 1, tzinfo=timezone.utc)]
    rows = pd.DataFrame({"keyword_id": ["k", "k", "k2", "k2"], "snapshot_time": [when[0], when[0], when[1], when[1]],
                         "site": ["a.com", "b.com", "a.com", "b.com"], "rank": [1, 2, 1, 2]})
    with pytest.warns(UserWarning, match="dropped by date alone"):
        kept = pn.site_positions(rows, drop_poison_risk=True)
    assert set(kept["keyword_id"]) == {"k"}
    assert len(pn.site_positions(rows, drop_poison_risk=False)) == 4
    flagged = rows.assign(poison_risk=[False, False, False, False])  # a repaired page: the flag column is believed
    assert len(pn.site_positions(flagged, drop_poison_risk=True)) == 4


@needs_real_samples
def test_real_sample_pages_carry_no_intent_etv_or_volume_dated_after_the_page():
    path = REAL_SAMPLES / "serp_advanced" / "US_en.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        records = {r["keyword"]: r for r in map(json.loads, handle)}
    recorded = {}
    with gzip.open(REAL_SAMPLES / "keywords" / "US_en.json.gz", "rt", encoding="utf-8") as handle:
        for record in map(json.loads, handle):
            for month, info in record["keyword_info_history"].items():
                if info.get("time_update"):
                    recorded[(record["keyword"], month)] = dfs._time(info["time_update"])
    for row in dfs.iter_history_rows(path):
        record = records[row["keyword"]]
        when = row["snapshot_time"]
        if row["intent"] is not None:
            assert dfs._time(record["search_intent_info"]["last_updated_time"]) <= when, row["keyword"]
        if row["etv"] is not None:
            assert dfs._time(record["keyword_info"]["time_update"]) <= when, row["keyword"]
        month = dfs.volume_month_known_at(record["keyword_info"], when.date())
        known = recorded.get((row["keyword"], month)) if month else None
        assert known is None or known <= when, (row["keyword"], when, month, known)


@needs_real_samples
def test_real_keyword_history_rows_older_than_the_record_carry_no_kd():
    path = REAL_SAMPLES / "keywords" / "US_en.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        newest = {r["keyword"]: max(r["keyword_info_history"]) for r in map(json.loads, handle)}
    for row in dfs.iter_keyword_rows(path, "dfs_keywords_history"):
        if row["snapshot_month"].replace("-", "") < newest[row["keyword"]]:
            assert row["kd"] is None, row["keyword"]


# ---------------------------------------------------------------------------
# Follow-ups of the data-layer bug hunt


def test_labs_etv_fails_closed_until_the_switch_is_turned_on(monkeypatch):
    rows = list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json", locale="US_en"))
    assert all(row["etv"] is None for row in rows), "no record date: every etv is held back"
    dated = list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json", locale="US_en", record_date=date(2026, 10, 1)))
    assert [row["etv"] is None for row in dated] == [row["snapshot_date"] < date(2026, 10, 1) for row in dated]
    monkeypatch.setattr(dfs, "LABS_ETV_IS_PAGE_DATED", True)
    trusted = list(dfs.iter_labs_historical_serps(FIX / "labs_historical_serps.json", locale="US_en"))
    assert trusted[0]["etv"] == 1.5


def _write_part(folder: Path, sites: list[str]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [{name: None for name in pn.PANEL_COLUMNS} | {
        "dataset": "dfs_history", "locale": "US_en", "keyword": "k", "keyword_id": "id", "snapshot_time": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "snapshot_date": date(2025, 1, 1), "rank": rank, "site": site, pn.POISON_RISK_COLUMN: False}
        for rank, site in enumerate(sites, start=1)]
    target = folder / "dataset=dfs_history" / "locale=US_en"
    target.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pn.PANEL_SCHEMA), target / "part-x.parquet")


def test_loaders_drop_an_excluded_site_however_a_writer_spelled_it(tmp_path: Path):
    _write_part(tmp_path, ["WWW.SmartCrewHQ.com", "SmartCrewHQ.com", "blog.smartcrewhq.com", "ok.com"])
    panel = pn.load_panel(["keyword_id", "snapshot_time", "site", "rank"], panel_dir=tmp_path)
    assert list(panel["site"]) == ["ok.com"]
    raw = pd.DataFrame({"keyword_id": ["id"] * 2, "snapshot_time": [pd.Timestamp("2025-01-01", tz="UTC")] * 2,
                        "site": ["Www.SmartCrewHQ.com", "ok.com"], "rank": [1, 2], pn.POISON_RISK_COLUMN: [False, False]})
    assert list(pn.site_positions(raw)["site"]) == ["ok.com"]
    assert list(pn.excluded_mask(pd.Series(["SMARTCREWHQ.COM", "x.com", None]))) == [True, False, False]


def test_a_failed_panel_rebuild_leaves_the_old_panel_in_place(tmp_path: Path, sample_root: Path, monkeypatch):
    out = tmp_path / "panel"
    files = pn.select_sample_files(pn.discover_sample_files(sample_root), ["history"])
    pn.build_panel_into(files, out, root=sample_root)
    before = sorted(p.relative_to(out) for p in out.rglob("*"))
    with pytest.raises(pn.PanelExistsError):
        pn.build_panel_into(files, out, root=sample_root)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pn, "build_panel", broken)
    with pytest.raises(RuntimeError):
        pn.build_panel_into(files, out, overwrite=True, root=sample_root)
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == before
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
