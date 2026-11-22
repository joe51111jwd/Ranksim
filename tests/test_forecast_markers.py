"""The frozen markers, their verdicts, and the plan's schedule checks.

The stage reports here are built with grading.headline on made-up graded
cells, in the shape report.stage_report returns. No database, no network.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from aeo.ranksim.forecast.config import NT50, SOURCES, STAGE_DAYS
from aeo.ranksim.forecast.grading import GradedCell, headline
from aeo.ranksim.forecast.markers import (
    MARKERS,
    evaluate_marker,
    latest_launch_for,
    marker,
    markers_hash,
    markers_payload,
    plan_schedule_problems,
    render_marker_text,
    schedule_check,
)


def cells(sites: int, per_site: int, *, hit_share: float, page_one_every: int = 0) -> list[GradedCell]:
    """sites x per_site cells with exactly round(total * (1 - hit_share)) misses, spread evenly.

    Every page_one_every-th cell is a page one call (point 4, real 5). A miss is a page that
    really landed at 30, so the point (4 or NT50) is wrong.
    """
    total = sites * per_site
    misses = round(total * (1 - hit_share))
    miss_at = {round(j * total / misses) + 1 for j in range(misses)} if misses else set()
    out = []
    for index in range(total):
        site, q = f"site{index // per_site}.example", index % per_site
        point, actual = (4, 5) if page_one_every and index % page_one_every == 0 else (NT50, NT50)
        if index in miss_at:
            actual = 30
        out.append(GradedCell(site, f"q{q}", point, actual))
    assert sum(1 for i in range(total) if i in miss_at) == misses
    return out


def stage_report_like(
    graded: list[GradedCell],
    *,
    source: str,
    stage: str,
    as_of: str,
    not_test: int = 0,
    baselines: dict[str, list[int]] | None = None,
    spec_baselines: bool = True,
) -> dict:
    """With spec_baselines, persistence, band_prior and site_strength are present (all NT50 points)."""
    named = {name: [NT50] * len(graded) for name in ("persistence", "band_prior", "site_strength")} if spec_baselines else {}
    named.update(baselines or {})
    eligibility = {
        name: {s: {"made": 0, "due": 0, "graded": 0, "due_not_graded": 0, "not_yet_due": 0, "next_due_date": None}
               for s in STAGE_DAYS}
        for name in SOURCES
    }
    eligibility[source][stage]["graded"] = len(graded)
    return {
        "kind": "stage_report",
        "source": source,
        "stage": stage,
        "as_of": as_of,
        "headline": headline(graded, named),
        "eligibility": eligibility,
        "held_out": {"test": len(graded) - not_test, "not_test": not_test},
    }


def test_markers_are_frozen_and_hashed():
    assert [m.number for m in MARKERS] == [1, 2, 3, 4, 5]
    assert [m.date for m in MARKERS] == [
        date(2026, 11, 30), date(2026, 12, 14), date(2026, 12, 21), date(2027, 1, 22), date(2027, 3, 19),
    ]
    with pytest.raises(dataclasses.FrozenInstanceError):
        MARKERS[0].date = date(2026, 12, 1)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        MARKERS[4].targets[0].minimum = 0.5  # type: ignore[misc]
    assert markers_hash() == markers_hash() and len(markers_hash()) == 64
    payload = markers_payload()
    assert payload[4]["targets"][0] == {"name": "hit rate", "minimum": 0.9, "strict": False}
    assert [t.minimum for t in marker(5).targets] == [0.90, 0.85, 0.80]
    assert [t.minimum for t in marker(4).targets] == [0.85, 0.80]
    assert marker(3).targets[1].strict is True
    with pytest.raises(ValueError):
        marker(6)


def test_marker_1_pass_fail_and_not_measurable():
    passed = evaluate_marker(1, {"headline": {"count": 300, "right": 285, "rate": 0.95, "cluster_interval": [0.92, 0.97]}})
    assert passed["passed"] is True and passed["checks"][0]["target"] == 0.92
    assert passed["interval"] == [0.92, 0.97]
    edge = evaluate_marker(1, {"headline": {"count": 300, "right": 276, "rate": 0.92, "cluster_interval": [0.89, 0.95]}})
    assert edge["passed"] is True
    failed = evaluate_marker(1, {"headline": {"count": 300, "right": 270, "rate": 0.90, "cluster_interval": [0.86, 0.93]}})
    assert failed["passed"] is False and failed["checks"][0]["ok"] is False
    empty = evaluate_marker(1, {"headline": {"count": 0, "right": 0, "rate": None, "cluster_interval": [None, None]}})
    assert empty["passed"] is None and "zero eligible real outcomes" in empty["notes"][0]
    small = evaluate_marker(1, {"headline": {"count": 12, "right": 12, "rate": 1.0, "cluster_interval": [None, None]}})
    assert small["passed"] is None and "NOT MEASURABLE (12 of 30 needed)" in small["notes"][0]
    assert evaluate_marker(1, {})["passed"] is None


def test_marker_2_needs_the_gain_over_link_count():
    good = {"pairs": {"count": 400, "right": 300, "rate": 0.75}, "gain_over_link_count": {"difference_interval": [0.02, 0.2]},
            "counts": {"right": 300, "wrong": 90, "tied": 10, "missing": 0}}
    assert evaluate_marker(2, good)["passed"] is True
    assert evaluate_marker(2, good)["counts"]["tied"] == 10
    no_gain = dict(good, gain_over_link_count={"difference_interval": [-0.01, 0.2]})
    assert evaluate_marker(2, no_gain)["passed"] is False
    unknown = dict(good, gain_over_link_count={"difference_interval": [None, None]})
    assert evaluate_marker(2, unknown)["passed"] is None


def test_marker_3_passes_only_above_the_best_baseline_on_held_out_launches():
    graded = cells(20, 5, hit_share=0.85, page_one_every=3)
    report = stage_report_like(graded, source="backtest", stage="upload", as_of="2026-12-21")
    result = evaluate_marker(3, report)
    hit_rate = report["headline"]["summary"]["hit"]["rate"]
    assert hit_rate >= 0.75
    assert result["passed"] is True, result
    assert result["best_baseline"] == "always_nt50"
    # Same numbers, but a baseline that is as good as the model: no gain, so no pass.
    same = stage_report_like(graded, source="backtest", stage="upload", as_of="2026-12-21",
                             baselines={"oracle": [c.point for c in graded]})
    verdict = evaluate_marker(3, same)
    assert verdict["passed"] is False
    assert any(c["name"] == "lower bound of gain over best baseline" and c["ok"] is False for c in verdict["checks"])
    # A training site among the graded rows refuses the pass.
    leaky = stage_report_like(graded, source="backtest", stage="upload", as_of="2026-12-21", not_test=3)
    assert evaluate_marker(3, leaky)["passed"] is False
    # Seed data cannot pass marker 3.
    seed = stage_report_like(graded, source="seed", stage="upload", as_of="2026-12-21")
    assert evaluate_marker(3, seed)["passed"] is False


def test_marker_3_is_not_passed_without_the_band_prior_and_site_strength_baselines():
    """Spec section 2: the four baselines are printed on the same forecasts every time."""
    graded = cells(20, 5, hit_share=0.85, page_one_every=3)
    bare = stage_report_like(graded, source="backtest", stage="upload", as_of="2026-12-21", spec_baselines=False,
                             baselines={"persistence": [NT50] * len(graded)})
    verdict = evaluate_marker(3, bare)
    assert verdict["passed"] is None
    assert verdict["baselines_missing"] == ["band_prior", "site_strength"]
    gain = next(c for c in verdict["checks"] if c["name"] == "lower bound of gain over best baseline")
    assert gain["ok"] is None
    assert any("band_prior, site_strength" in note for note in verdict["notes"])
    assert "NOT MEASURABLE YET" in render_marker_text(verdict)


def test_marker_3_judges_a_tercile_constant_tool_against_the_band_prior():
    """A tool that only knows search difficulty (easy 5, medium 15, hard NT50) is the band prior itself."""
    terciles = {"easy": (5, 5), "medium": (15, 15), "hard": (NT50, NT50)}
    graded = [
        GradedCell(f"site{i % 8}.example", f"q{i}", terciles[name][0], terciles[name][1], difficulty=name)
        for i, name in enumerate(["easy", "medium", "hard"] * 12)
    ]
    band_prior = [terciles[c.difficulty][0] for c in graded]
    report = stage_report_like(graded, source="backtest", stage="upload", as_of="2026-12-21",
                               baselines={"band_prior": band_prior})
    verdict = evaluate_marker(3, report)
    assert verdict["best_baseline"] == "band_prior"
    assert verdict["passed"] is False  # no gain over the band prior


def test_markers_4_and_5_are_not_passed_without_the_spec_baselines():
    graded = cells(20, 10, hit_share=0.95, page_one_every=3)
    for number, stage, source in ((4, "d30", "backtest"), (5, "d60", "seed")):
        bare = stage_report_like(graded, source=source, stage=stage, as_of="2027-03-19", spec_baselines=False)
        verdict = evaluate_marker(number, bare)
        assert verdict["passed"] is None, verdict
        assert verdict["baselines_missing"] == ["persistence", "band_prior", "site_strength"]


def test_marker_3_fails_under_75_and_is_not_measurable_with_few_outcomes():
    low = stage_report_like(cells(20, 5, hit_share=0.70), source="backtest", stage="upload", as_of="2026-12-21")
    assert evaluate_marker(3, low)["passed"] is False
    few = stage_report_like(cells(4, 5, hit_share=1.0), source="backtest", stage="upload", as_of="2026-12-21")
    verdict = evaluate_marker(3, few)
    assert verdict["passed"] is None
    assert any("NOT MEASURABLE (20 of 30 needed)" in note for note in verdict["notes"])


def test_marker_4_needs_measurable_precision():
    graded = cells(20, 5, hit_share=0.90, page_one_every=10)  # 10 page one calls: precision not measurable
    report = stage_report_like(graded, source="backtest", stage="d30", as_of="2027-01-22")
    verdict = evaluate_marker(4, report)
    assert verdict["passed"] is None
    assert any("Page one precision is NOT MEASURABLE (10 of 30 needed)" in n for n in verdict["notes"])
    assert verdict["recall"]["measurable"] is False
    many = stage_report_like(cells(20, 5, hit_share=0.90, page_one_every=3), source="backtest", stage="d30",
                             as_of="2027-01-22")
    assert evaluate_marker(4, many)["passed"] is True
    bad = stage_report_like(cells(20, 5, hit_share=0.80, page_one_every=3), source="backtest", stage="d30",
                            as_of="2027-01-22")
    assert evaluate_marker(4, bad)["passed"] is False
    with pytest.raises(ValueError):
        evaluate_marker(4, stage_report_like(graded, source="backtest", stage="d60", as_of="2027-01-22"))


def test_marker_5_refuses_backtest_data_and_prints_the_site_cluster_interval():
    graded = cells(20, 10, hit_share=0.95, page_one_every=3)
    real = stage_report_like(graded, source="seed", stage="d60", as_of="2027-03-19")
    verdict = evaluate_marker(5, real)
    assert verdict["passed"] is True, verdict
    low, high = verdict["site_cluster_interval"]
    assert low is not None and low < real["headline"]["summary"]["hit"]["rate"] <= high
    assert verdict["clusters"] == 20
    assert any("Site cluster interval" in note for note in verdict["notes"])
    backtest = stage_report_like(graded, source="backtest", stage="d60", as_of="2027-03-19")
    refused = evaluate_marker(5, backtest)
    assert refused["passed"] is False
    assert any("Backtest data cannot pass it" in note for note in refused["notes"])
    synthetic = stage_report_like(graded, source="synthetic", stage="d60", as_of="2027-03-19")
    synthetic_verdict = evaluate_marker(5, synthetic)
    assert synthetic_verdict["passed"] is None and synthetic_verdict["label"] == "SYNTHETIC"
    assert synthetic_verdict["synthetic_check_passed"] is False  # the source bar fails, kept apart from passed


def test_marker_5_with_zero_outcomes_is_not_measurable_and_names_sources():
    report = stage_report_like([], source="seed", stage="d60", as_of="2027-01-22")
    report["eligibility"]["seed"]["d60"]["next_due_date"] = "2027-02-28"
    verdict = evaluate_marker(5, report)
    assert verdict["passed"] is None
    assert any("zero eligible real outcomes" in n for n in verdict["notes"])
    assert any(n.startswith("seed has no graded d60 outcome yet. The first is due on 2027-02-28") for n in verdict["notes"])
    assert any(n.startswith("pilot has no graded d60 outcome yet") for n in verdict["notes"])
    text_out = render_marker_text(verdict)
    assert "NOT MEASURABLE YET" in text_out and "28 Feb 2027" in text_out
    assert "\u2014" not in text_out and "\u2013" not in text_out and "2027-" not in text_out


def test_schedule_check_against_the_plan_dates():
    rows = schedule_check(
        {"seed.example": date(2026, 11, 30), "last.example": "2026-12-19", "late.example": date(2026, 12, 20),
         "pilot.example": date(2026, 12, 28)},
        today=date(2026, 11, 22),
    )
    by_site = {row["site"]: row for row in rows}
    assert [row["site"] for row in rows] == sorted(by_site)
    seed = by_site["seed.example"]
    assert (seed["day_7"], seed["day_14"], seed["day_30"], seed["day_60"], seed["day_90"]) == (
        "2026-12-07", "2026-12-14", "2026-12-30", "2027-01-29", "2027-02-28",
    )
    assert seed["days_since_launch"] == -8
    can = {m["marker"]: m["can_contribute_graded_outcome"] for m in seed["markers"]}
    assert can == {1: False, 2: False, 3: False, 4: False, 5: True}
    marker4 = next(m for m in seed["markers"] if m["marker"] == 4)
    assert marker4["day_on_marker_date"] == 53 and marker4["latest_stage_reached"] == "d30"
    assert next(m for m in by_site["last.example"]["markers"] if m["marker"] == 5)["can_contribute_graded_outcome"]
    assert not next(m for m in by_site["late.example"]["markers"] if m["marker"] == 5)["can_contribute_graded_outcome"]
    pilot5 = next(m for m in by_site["pilot.example"]["markers"] if m["marker"] == 5)
    assert pilot5["can_contribute_graded_outcome"] is False and pilot5["day_on_marker_date"] == 81
    pilot1 = next(m for m in by_site["pilot.example"]["markers"] if m["marker"] == 1)
    assert pilot1["latest_stage_reached"] is None and "not launched" in pilot1["note"]


def test_plan_schedule_problems_are_stated_as_checks():
    checks = {c["name"]: c for c in plan_schedule_problems()}
    assert checks["seed day 90"]["value"] == "2027-02-28" and checks["seed day 90"]["ok"] is True
    assert checks["seed day 60"]["value"] == "2027-01-29" and checks["seed day 60"]["ok"] is True
    pilot = checks["pilot launch for marker 5"]
    assert pilot["target"] == "2026-12-19" and pilot["ok"] is False
    assert latest_launch_for(date(2027, 3, 19)) == date(2026, 12, 19)
    m4 = checks["seed outcome for marker 4"]
    assert m4["ok"] is False and "day 53" in m4["note"] and "2026-10-24" in m4["note"]
    week1 = checks["seed launch week"]
    assert week1["value"] == "2027-02-21" and week1["ok"] is False


def test_a_synthetic_report_gets_a_synthetic_verdict_that_never_passes():
    block = {"count": 300, "right": 290, "rate": 0.97, "cluster_interval": [0.95, 0.98]}
    verdict = evaluate_marker(1, {"label": "SYNTHETIC", "headline": block})
    assert verdict["label"] == "SYNTHETIC" and verdict["passed"] is None
    assert verdict["synthetic_check_passed"] is True  # the bars were met, kept apart from passed
    assert any("cannot pass a marker" in note for note in verdict["notes"])
    text = render_marker_text(verdict)
    assert "NOT A MARKER RESULT (SYNTHETIC)" in text and "Synthetic code check: PASSED (SYNTHETIC)" in text
    assert "—" not in text and "–" not in text
    real = evaluate_marker(1, {"label": "REAL", "headline": block})
    assert real["label"] == "REAL" and real["passed"] is True and "synthetic_check_passed" not in real


def test_shared_constants_live_in_config():
    from aeo.ranksim.forecast import config, markers, report, scoreboard, synth

    assert config.DIFFICULTY_TERCILES == ("easy", "medium", "hard")
    assert scoreboard.DIFFICULTY_TERCILES is config.DIFFICULTY_TERCILES and synth.TERCILES is config.DIFFICULTY_TERCILES
    assert report.REPORT_MIN_COUNT == markers.MARKER_MIN_COUNT == config.MIN_COUNT_TO_PRINT_RATE == 30
    assert config.MIN_COUNT_PRECISION_RECALL == config.MIN_COUNT_TO_PRINT_RATE
