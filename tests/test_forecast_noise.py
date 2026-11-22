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
