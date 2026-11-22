"""Marker 1: agreement rows, the report, the gap window, the ablation, the frozen sample and the dry run."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.noise import (
    AGREEMENT_BANDS,
    agreement,
    agreement_report,
    dry_run_on_committed_pulls,
    marker1_report_from_pulls,
    sample_marker1_searches,
    single_pull_agreement,
    summarize_rows,
)
from aeo.ranksim.forecast.types import CleanReading, OrganicHit, Pull, PullSpec, SitePosition

REPO = Path(__file__).resolve().parents[1]
BLOCK_KEYS = {"count", "right", "rate", "cluster_interval", "wilson_interval_if_independent"}


def reading(
    search: str,
    positions: dict[str, int],
    *,
    taken_at: str = "2026-11-23T10:00:00Z",
    degraded: bool = False,
    location: int = 1023191,
    device: str = "desktop",
) -> CleanReading:
    entries = tuple(
        SitePosition(site=site, position=pos, pull_positions=(pos,) * 5)
        for site, pos in sorted(positions.items(), key=lambda item: (item[1], item[0]))
    )
    return CleanReading(
        reading_id=f"r-{search}-{taken_at}",
        search=search,
        location_code=location,
        device=device,
        taken_at=taken_at,
        pulls_total=5,
        pulls_kept=2 if degraded else 5,
        dropped=(),
        degraded=degraded,
        pulls_not_independent=False,
        positions=entries,
    )


def pull(name: str, search: str, sites: list[str], *, completed: str, ok: bool = True) -> Pull:
    spec = PullSpec(search=search, location_code=1023191)
    hits = tuple(OrganicHit(rank=i + 1, url=f"https://{s}/", domain=s, site=s) for i, s in enumerate(sites))
    return Pull(
        pull_id=f"pull-{name}",
        provider="test",
        endpoint="test",
        spec=spec,
        requested_at=completed,
        completed_at=completed,
        ok=ok,
        hits=hits if ok else (),
    )


def test_agreement_edges_at_49_50_and_absent():
    a = reading("q", {"at49.com": 49, "at50.com": 50, "at48.com": 48, "top.com": 10, "near.com": 9})
    b = reading("q", {"near.com": 11})
    rows = {row.site: row for row in agreement(a, b)}
    assert rows["at49.com"].position_b == NT50 and rows["at49.com"].difference == 2 and rows["at49.com"].agree
    assert rows["at50.com"].agree and rows["at50.com"].difference == 1
    assert not rows["at48.com"].agree
    assert not rows["top.com"].agree and rows["top.com"].top10_unit and rows["top.com"].entry_exit_top10
    assert rows["near.com"].agree and rows["near.com"].entry_exit_top10 and not rows["near.com"].entry_exit_top50
    assert not rows["at49.com"].top10_unit and rows["at49.com"].top50_unit and rows["at49.com"].entry_exit_top50
    assert rows["at49.com"].band == "21-50" and rows["near.com"].band == "4-10"
    assert set(rows) == {"at49.com", "at50.com", "at48.com", "top.com", "near.com"}  # absent from both is no unit


def test_agreement_refuses_different_searches_and_drops_excluded_sites():
    with pytest.raises(ValueError):
        agreement(reading("q", {}), reading("other", {}))
    rows = agreement(reading("q", {"smartcrewhq.com": 1, "x.com": 2}), reading("q", {"smartcrewhq.com": 1, "x.com": 2}))
    assert [row.site for row in rows] == ["x.com"]


def test_grading_rule_agreement_counts_both_directions():
    rows = agreement(reading("q", {"s.com": 21}), reading("q", {"s.com": 20}))
    assert rows[0].grading_hit_a_as_forecast is True  # real 20 tolerates 5 places
    assert rows[0].grading_hit_b_as_forecast is False  # real 21 needs a point in 21 to 50
    summary = summarize_rows(rows)
    assert summary["grading_rule_agreement"]["rate"] == pytest.approx(0.5)
    assert summary["grading_rule_agreement"]["right"] == pytest.approx(0.5)


def test_gap_window_and_skip_reasons():
    def pair(search: str, second_time: str, **kw) -> tuple[CleanReading, CleanReading]:
        return reading(search, {"x.com": 3}), reading(search, {"x.com": 4}, taken_at=second_time, **kw)

    pairs = [
        pair("short", "2026-11-24T05:59:24Z"),  # 19.99 hours
        pair("twenty", "2026-11-24T06:00:00Z"),
        pair("twentyeight", "2026-11-24T14:00:00Z"),
        pair("long", "2026-11-24T14:00:36Z"),  # 28.01 hours
        pair("degraded", "2026-11-24T10:00:00Z", degraded=True),
        pair("mobile", "2026-11-24T10:00:00Z", device="mobile"),
        pair("elsewhere", "2026-11-24T10:00:00Z", location=1014221),
    ]
    report = agreement_report(pairs)
    assert report["pairs_used"] == 2
    reasons = {entry["search"]: entry["reason"] for entry in report["pairs_skipped"]}
    assert reasons["short"].startswith("gap outside the window: 19.99 hours")
    assert reasons["long"].startswith("gap outside the window: 28.01 hours")
    assert reasons["degraded"] == "degraded reading"
    assert reasons["mobile"] == reasons["elsewhere"] == "different location or device"
    assert report["pairs_skipped_by_reason"] == {
        "degraded reading": 1, "different location or device": 2, "gap outside the window": 2,
    }
    loose = agreement_report(pairs, enforce_gap=False)
    assert loose["pairs_used"] == 4 and loose["definitions"]["gap_hours"] is None
    with pytest.raises(ValueError):
        agreement_report(pairs, min_gap_hours=30, max_gap_hours=20)


def test_report_shape_and_numbers():
    day2 = "2026-11-24T10:00:00Z"
    pairs = [
        (reading("q1", {"a.com": 1, "b.com": 5, "c.com": 30}), reading("q1", {"a.com": 2, "b.com": 9, "d.com": 45}, taken_at=day2)),
        (reading("q2", {"a.com": 12}), reading("q2", {"a.com": 13, "e.com": 3}, taken_at=day2)),
    ]
    report = agreement_report(pairs)
    for key in ("headline", "top50", "by_band", "entry_exit", "grading_rule_agreement", "raw_single_pull_baseline",
                "ablation_no_overlap_rule", "pairs_used", "pairs_skipped", "searches", "definitions"):
        assert key in report
    assert BLOCK_KEYS <= set(report["headline"]) and BLOCK_KEYS <= set(report["top50"])
    assert set(report["by_band"]) == set(AGREEMENT_BANDS)
    # headline units: q1 a (1,2 agree), b (5,9 no); q2 e (51,3 no) -> 1 of 3
    assert report["headline"]["count"] == 3 and report["headline"]["right"] == 1
    # top-50 units add q1 c (30,51 no), d (51,45 no), q2 a (12,13 agree) -> 2 of 6
    assert report["top50"]["count"] == 6 and report["top50"]["right"] == 2
    assert report["entry_exit"]["top10"] == {"count": 3, "in_one_reading_only": 1, "rate": pytest.approx(1 / 3)}
    assert report["by_band"]["1-3"]["count"] == 2 and report["by_band"]["11-20"]["right"] == 1
    assert report["searches"] == 2 and report["raw_single_pull_baseline"] is None
    assert report["label"] == "REAL"


def test_empty_report_has_no_rate_and_no_interval():
    report = agreement_report([])
    assert report["headline"]["count"] == 0 and report["headline"]["rate"] is None
    assert report["headline"]["cluster_interval"] == [None, None]
    assert report["entry_exit"]["top10"]["rate"] is None


def test_raw_single_pull_baseline_is_measured():
    day1, day2 = "2026-11-23T10:00:00Z", "2026-11-24T10:00:00Z"
    raw = [
        (pull("a", "q", ["x.com", "y.com"], completed=day1), pull("b", "q", ["y.com", "x.com"], completed=day2)),
        (pull("c", "q2", ["x.com"], completed=day1), pull("d", "q2", [], completed=day2, ok=False)),
    ]
    report = agreement_report([], raw_pairs=raw)
    baseline = report["raw_single_pull_baseline"]
    assert baseline["pairs_used"] == 1 and baseline["headline"]["count"] == 2 and baseline["headline"]["right"] == 2
    assert baseline["pairs_skipped_by_reason"] == {"raw pull not ok": 1}
    with pytest.raises(ValueError):
        single_pull_agreement(raw[1][0], raw[1][1])


def five_pulls(prefix: str, search: str, day: str, *, poisoned: bool) -> list[Pull]:
    sites = [f"site{i}.com" for i in range(30)]
    out = []
    for i in range(5):
        order = [sites[1], sites[0]] + sites[2:] if i % 2 else list(sites)
        if poisoned and i == 2:
            order = [f"spam{j}.com" for j in range(30)]
        out.append(pull(f"{prefix}{i}", search, order, completed=f"{day}T10:0{i}:00Z"))
    return out


def test_marker1_report_from_pulls_has_the_ablation_and_the_raw_baseline():
    reading_pulls = [
        (five_pulls("a", "q1", "2026-11-23", poisoned=True), five_pulls("b", "q1", "2026-11-24", poisoned=False)),
        (five_pulls("c", "q2", "2026-11-23", poisoned=False), five_pulls("d", "q2", "2026-11-24", poisoned=True)),
    ]
    report = marker1_report_from_pulls(reading_pulls)
    assert report["pairs_used"] == 2
    assert report["headline"]["rate"] == 1.0
    ablation = report["ablation_no_overlap_rule"]
    assert ablation["pairs_used"] == 2
    # One poisoned pull of five never reaches a median, so the filter is not what makes this agreement.
    assert ablation["headline"]["rate"] == report["headline"]["rate"]
    baseline = report["raw_single_pull_baseline"]
    assert baseline["pairs_used"] == 10
    assert baseline["headline"]["rate"] < 1.0  # the poisoned single pulls disagree


def test_search_sample_is_frozen_and_stratified():
    candidates = [f"search {i}" for i in range(600)]
    strata = {c: ("niche-a" if i < 400 else "niche-b") for i, c in enumerate(candidates)}
    first = sample_marker1_searches(candidates, 300, seed=7, strata=strata)
    shuffled = list(candidates)
    random.Random(1).shuffle(shuffled)
    again = sample_marker1_searches(shuffled + candidates[:5], 300, seed=7, strata=strata)
    assert first == again
    assert len(first.searches) == 300 and len(set(first.searches)) == 300
    assert dict(first.per_stratum) == {"niche-a": 200, "niche-b": 100}
    assert sample_marker1_searches(candidates, 300, seed=8, strata=strata).sha256 != first.sha256
    assert len(first.sha256) == 64


def test_search_sample_edges():
    assert sample_marker1_searches(["b", "a"], 300).searches == ("a", "b")
    three = sample_marker1_searches(["x", "y", "z"], 2, strata={"x": "1", "y": "2", "z": "3"})
    assert sum(count for _, count in three.per_stratum) == 2
    with pytest.raises(ValueError):
        sample_marker1_searches(["x", "y"], 1, strata={"x": "1"})
    with pytest.raises(ValueError):
        sample_marker1_searches([], 5)
    with pytest.raises(ValueError):
        sample_marker1_searches(["x"], 0)


def test_dry_run_on_committed_pulls_is_labelled_and_reproduces_the_map():
    result = dry_run_on_committed_pulls(REPO)
    assert result["is_marker_1"] is False
    assert "NOT MARKER 1" in result["label"] and "No pinned city" in result["label"]
    assert result["inputs"]["pulls_loaded"] == {"pull-a": 70, "pull-b": 70, "pull-c": 70}
    single = result["single_pull_vs_single_pull"]
    assert single["pairs_used"] == 210
    # With absent counted as 11 the map measured 0.644 (union) and 0.868 (both). Smartcrew rows are dropped here.
    assert 0.62 <= single["comparison_only_top10_union_absent_11"]["rate"] <= 0.67
    assert 0.85 <= single["present_in_both_top10"]["rate"] <= 0.90
    assert 0.45 <= single["headline_top10_union_absent_51"]["rate"] <= 0.60
    assert result == dry_run_on_committed_pulls(REPO)  # deterministic


# ---------------------------------------------------------------------------
# reader version and filter settings in the report, version comparison, calibration

import gzip  # noqa: E402
import json  # noqa: E402

from aeo.ranksim.forecast.noise import (  # noqa: E402
    calibrate_on_dataforseo_history,
    compare_versions_on_committed_pulls,
    history_page_pull,
    iter_dataforseo_history_pages,
    main,
)
from aeo.ranksim.forecast.reader import CONCENTRATION_MIN_SLOTS, LINK_MIN_SHARE  # noqa: E402


def test_marker1_report_states_the_reader_version_and_keeps_the_ablation_beside_the_headline():
    reading_pulls = [
        (five_pulls("a", "q1", "2026-11-23", poisoned=True), five_pulls("b", "q1", "2026-11-24", poisoned=False)),
    ]
    report = marker1_report_from_pulls(reading_pulls)
    reader = report["reader"]
    assert reader["reader_version"] == "reader-v2" and reader["filter_on"] is True
    assert reader["concentration_min_slots"] == CONCENTRATION_MIN_SLOTS and reader["link_min_share"] == LINK_MIN_SHARE
    assert reader["history_used"] is False
    assert reader["ablation"]["filter_on"] is False
    beside = report["headline_beside_ablation"]
    assert beside["filter_on"] == report["headline"]["rate"]
    assert beside["filter_off"] == report["ablation_no_overlap_rule"]["headline"]["rate"]
    assert beside["raw_single_pull"] == report["raw_single_pull_baseline"]["headline"]["rate"]
    old = marker1_report_from_pulls(reading_pulls, version=1)
    assert old["reader"]["reader_version"] == "reader-v1" and "poison_min_overlap" in old["reader"]
    assert agreement_report([])["reader"] is None


def test_compare_versions_on_the_committed_pulls():
    result = compare_versions_on_committed_pulls(REPO)
    assert result["is_marker_1"] is False and "NOT MARKER 1" in result["label"]
    first, second = result["version_1"], result["version_2"]
    assert first["reader"]["reader_version"] == "reader-v1" and second["reader"]["reader_version"] == "reader-v2"
    assert first["pulls_dropped"] == 17 and first["readings_degraded"] == 15  # the v1 numbers measured before v2
    assert first["dropped_by_reason"] == {"low_overlap": 17}
    by_search = {entry["search"]: entry for entry in result["splits"]}
    # The two searches named in the evidence: the clean pull is the minority and version 2 keeps it.
    assert by_search["assignar alternative"]["kept_v1"] == ["a", "c"]
    assert by_search["assignar alternative"]["kept_v2"] == ["b"]
    assert by_search["hcss pricing"]["kept_v1"] == ["b", "c"]
    assert by_search["hcss pricing"]["kept_v2"] == ["a"]
    assert by_search["hcss pricing"]["decided_by_v2"] == "off-brand concentration"
    assert by_search["assignar alternative"]["pulls"]["a"][:7] == ["slashdot.org"] * 7
    assert all(entry["degraded_v2"] for entry in result["splits"] if entry["clusters_v2"] and len(entry["clusters_v2"]) > 1)
    assert "slashdot.org" in result["round_suspects"]["pull-a"]
    assert result == compare_versions_on_committed_pulls(REPO)  # deterministic
    dry = dry_run_on_committed_pulls(REPO, version=1)
    assert dry["reader"]["reader_version"] == "reader-v1"
    assert dry["filter_on_three_pull_readings"]["degraded"] == 15


def organic(rank: int, domain: str, path: str = "") -> dict:
    return {"type": "organic", "rank_group": rank, "domain": f"www.{domain}", "main_domain": domain,
            "url": f"https://www.{domain}/{path or rank}"}


def history_line(keyword: str, pages: list[tuple[str | None, list[dict]]]) -> str:
    return json.dumps({
        "keyword": keyword,
        "serp_info_history": [{"time_update": stamp, "serp": items + [{"type": "people_also_ask"}]} for stamp, items in pages],
    })


def write_history(folder: Path, locale: str, lines: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{locale}.json.gz", "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def test_streaming_history_reader_and_calibration_on_a_small_fixture(tmp_path):
    clean = [organic(i + 1, f"site{i}.com") for i in range(10)]
    stable_next = [organic(i + 1, f"site{(i + 1) % 10}.com") for i in range(10)]
    other = [organic(i + 1, f"other{i}.com") for i in range(10)]
    run = [organic(i + 1, "youtube.com", f"v{i}") for i in range(5)] + [organic(i + 6, f"site{i}.com") for i in range(5)]
    brand = [organic(i + 1, "acme.com", f"p{i}") for i in range(6)] + [organic(i + 7, f"site{i}.com") for i in range(4)]
    with_client = [organic(1, "smartcrewhq.com")] + [organic(i + 2, f"site{i}.com") for i in range(9)]
    write_history(tmp_path, "US_en", [
        history_line("plumber near me", [("2025-06-01T10:00:00.1234567Z", clean), ("2025-06-01T14:00:00Z", stable_next),
                                         ("2025-06-01T20:00:00Z", other), ("2025-07-20T10:00:00Z", clean)]),
        history_line("music videos", [("2025-06-02T10:00:00Z", run), (None, clean)]),
        history_line("acme login", [("2025-06-03T10:00:00Z", brand)]),
        history_line("client page", [("2025-06-04T10:00:00Z", with_client)]),
    ])
    write_history(tmp_path, "DE_de", [history_line("klempner", [("2025-06-05T10:00:00Z", run)])])

    pages = list(iter_dataforseo_history_pages(tmp_path))
    assert [page.locale for page in pages] == ["DE_de"] + ["US_en"] * 7  # files in name order, the undated page skipped
    first = pages[1]
    assert first.time_update == "2025-06-01T10:00:00Z" and first.organic[0] == (1, "site0.com", "https://www.site0.com/1")
    assert all(site != "smartcrewhq.com" for page in pages for _, site, _ in page.organic)
    assert [p.locale for p in iter_dataforseo_history_pages(tmp_path, locales=["US_en"])] == ["US_en"] * 7
    pull = history_page_pull(first, depth=10)
    assert pull.spec.depth == 10 and len(pull.hits) == 10 and pull.ok and pull.spec.search == "plumber near me"

    result = calibrate_on_dataforseo_history(tmp_path)
    assert result["label"] == "REAL" and result["locales_read"] == 2
    assert result["pages_with_organic_results"] == {"all": 8, "en": 7}
    flags = result["off_brand_concentration_flag"]
    # youtube.com holds 5 slots on "music videos" and "klempner"; acme.com is on-brand for "acme login".
    assert flags["en"]["5"]["count"] == 1 and flags["all"]["5"]["count"] == 2 and flags["all"]["3"]["count"] == 2
    pairs = result["repulls_under_max_gap"]
    assert pairs["pairs"] == {"all": 2, "en": 2}  # 10:00 to 14:00 and 14:00 to 20:00; the July page is too far
    split = pairs["split_rate_by_link_share"]["depth_50"]["all"]["0.5"]
    assert split["count"] == 1 and split["total"] == 2  # the move to "other" sites splits, the reshuffle does not
    assert result["chosen"]["concentration_min_slots"] == CONCENTRATION_MIN_SLOTS
    assert result == calibrate_on_dataforseo_history(tmp_path)
    with pytest.raises(FileNotFoundError):
        calibrate_on_dataforseo_history(tmp_path / "missing")


def test_command_line_dispatch(tmp_path, capsys):
    write_history(tmp_path, "US_en", [history_line("q", [("2025-06-01T10:00:00Z", [organic(1, "a.com")])])])
    assert main(["calibrate", str(tmp_path), "US_en"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["locales_read"] == 1
