"""Grading rule v1, the as-of guard, the hash chain and the interval helpers."""

from __future__ import annotations

from datetime import date

import pytest

from aeo.ranksim.forecast import chain
from aeo.ranksim.forecast.asof import AsOfGuard, Dated, LeakError, add_months, as_date, parse_utc, utc_iso
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.grading import (
    RULE_V1,
    GradedCell,
    GradingRule,
    always_nt50,
    band_of,
    band_prior_points,
    fit_band_prior,
    compare_to_baseline,
    fine_band_of,
    headline,
    skill,
    summarize,
)
from aeo.ranksim.forecast.stats import cluster_bootstrap_ratio, design_effect, wilson
from aeo.ranksim.forecast.types import clamp_position


def test_bands_cover_every_position():
    assert [band_of(p) for p in (1, 10, 11, 20, 21, 50, 51, 400)] == [
        "1-10", "1-10", "11-20", "11-20", "21-50", "21-50", "NT50", "NT50",
    ]
    assert [fine_band_of(p) for p in (1, 3, 4, 10, 11)] == ["1-3", "1-3", "4-10", "4-10", "11-20"]
    assert clamp_position(75) == NT50
    with pytest.raises(ValueError):
        clamp_position(0)
    with pytest.raises(ValueError):
        clamp_position(None)


@pytest.mark.parametrize(
    "point, actual, expected",
    [
        (12, 10, True),   # real 10, within 2
        (13, 10, False),
        (10, 12, True),   # real 12, within 5
        (7, 12, True),
        (12, 7, False),   # real 7 only allows 2 places
        (21, 20, True),   # real 20, within 5
        (25, 20, True),
        (26, 20, False),
        (20, 21, False),  # real 21 needs a point in 21 to 50
        (35, 30, True),
        (50, 21, True),
        (NT50, 50, False),
        (50, NT50, False),
        (NT50, NT50, True),
        (NT50, 5, False),
        (11, 9, True),
        (6, 11, True),
        (5, 11, False),
        (30, 60, False),  # anything beyond 50 is NT50
        (99, 60, True),
    ],
)
def test_rule_v1_reads_the_plan_table_literally(point, actual, expected):
    assert RULE_V1.is_hit(point, actual) is expected


def test_forecast_side_rule_mirrors_the_tolerance():
    assert RULE_V1.is_hit(21, 20) and not RULE_V1.is_hit_forecast_side(21, 20)
    # Mirrored: forecast 20 for real 21 is a miss under v1 and a hit under the forecast-side rule.
    assert not RULE_V1.is_hit(20, 21) and RULE_V1.is_hit_forecast_side(20, 21) is True
    assert RULE_V1.is_hit_forecast_side(12, 7) is True  # forecast 12 allows 5 places
    assert RULE_V1.is_hit_forecast_side(NT50, NT50) is True


def test_a_changed_rule_has_a_different_hash():
    assert RULE_V1.rule_hash() == GradingRule().rule_hash()
    assert RULE_V1.rule_hash() != GradingRule(version="v2", tolerance_1_10=3).rule_hash()


def _cells():
    rows = []
    # 10 sites, 6 searches each. Most land outside the top 50, like a new domain.
    for s in range(10):
        site = f"site{s}.example"
        rows.append(GradedCell(site, "q-page-one", point=5, actual=6 if s < 7 else NT50, label="locked"))
        rows.append(GradedCell(site, "q-page-two", point=15, actual=14 if s < 5 else 30, label="contested"))
        rows.append(GradedCell(site, "q-deep", point=30, actual=40, label="contested"))
        for j in range(3):
            rows.append(GradedCell(site, f"q-out-{j}", point=NT50, actual=NT50, label="out_of_reach",
                                   range_low=40, range_high=NT50))
    return rows


def test_summary_counts_hits_bands_and_page_one():
    cells = _cells()
    report = summarize(cells)
    # hits: 7 page-one, 5 page-two, 10 deep, 30 out = 52 of 60
    assert report["hit"]["count"] == 60 and report["hit"]["right"] == 52
    assert report["by_real_band"]["NT50"]["count"] == 33  # 30 out plus 3 missed page-one calls
    assert report["page_one"]["calls"] == 10 and report["page_one"]["outcomes"] == 7
    assert report["page_one"]["precision"]["rate"] == pytest.approx(0.7)
    assert report["page_one"]["recall"]["rate"] == pytest.approx(1.0)
    assert report["page_one"]["precision"]["measurable"] is False  # 10 calls is under the minimum of 30
    assert report["point_pileup"]["share_of_11_20_points_at_15"] == 1.0
    assert report["confusion_forecast_by_real"]["1-10"]["NT50"] == 3
    assert report["range"]["count"] == 30 and report["range"]["coverage"] == 1.0
    assert report["coverage_not_contested"] == pytest.approx(40 / 60)
    low, high = report["hit"]["cluster_interval"]
    assert 0.0 <= low <= report["hit"]["rate"] <= high <= 1.0


def test_headline_always_includes_the_empty_tool_and_splits_brand():
    cells = _cells() + [GradedCell("site0.example", "site0 brand", point=1, actual=1, is_brand=True)]
    report = headline(cells, {"persistence": [NT50] * len(cells)})
    assert report["forecasts"] == 60 and report["brand_forecasts_shown_apart"] == 1
    empty = report["baselines"]["always_nt50"]
    assert empty["baseline_rate"] == pytest.approx(33 / 60)
    assert empty["difference"] == pytest.approx(52 / 60 - 33 / 60)
    assert report["best_baseline"] in ("always_nt50", "persistence")
    assert report["skill_over_best_baseline"] == pytest.approx(skill(52 / 60, 33 / 60))
    assert report["brand"]["hit"]["count"] == 1


def test_baseline_needs_one_point_per_cell():
    with pytest.raises(ValueError):
        compare_to_baseline(_cells(), always_nt50(_cells())[:-1])


def test_the_empty_tool_name_is_reserved_and_ties_are_stable():
    cells = _cells()
    with pytest.raises(ValueError):
        headline(cells, {"always_nt50": [NT50] * len(cells)})
    # A baseline identical to the empty tool ties with it. The name that sorts first wins.
    report = headline(cells, {"zz_same_as_empty": [NT50] * len(cells)})
    assert report["best_baseline"] == "always_nt50"


def test_wilson_matches_the_plan_example():
    low, high = wilson(180, 200)
    assert low == pytest.approx(0.851, abs=0.002) and high == pytest.approx(0.934, abs=0.002)
    assert wilson(0, 0) == (None, None)


def test_cluster_interval_is_wider_when_forecasts_move_together():
    # 10 sites of 20 forecasts. In the clustered case each site is all right or all wrong.
    clustered = [1.0 if site < 9 else 0.0 for site in range(10) for _ in range(20)]
    spread = [0.0 if i % 10 == 0 else 1.0 for i in range(200)]
    sites = [site for site in range(10) for _ in range(20)]
    c_low, c_high = cluster_bootstrap_ratio(clustered, [1.0] * 200, sites)
    s_low, s_high = cluster_bootstrap_ratio(spread, [1.0] * 200, sites)
    assert (c_high - c_low) > (s_high - s_low)
    assert design_effect(clustered, sites) > 5
    assert cluster_bootstrap_ratio([1.0], [1.0], ["only"]) == (None, None)


def test_as_of_guard_refuses_later_and_undated_values():
    guard = AsOfGuard(date(2025, 3, 1))
    assert guard.take(Dated(7, "tranco", date(2025, 2, 28))) == 7
    assert guard.take(None) is None
    with pytest.raises(LeakError):
        guard.take(Dated(9, "tranco", date(2025, 3, 2)))
    with pytest.raises(LeakError):
        guard.take(Dated(1, "majestic_today", None))
    live = AsOfGuard(date(2026, 11, 22), allow_undated=True)
    assert live.take(Dated(1, "majestic_today", None)) == 1
    assert guard.knowable_table() == [{"source": "tranco", "newest_snapshot": "2025-02-28"}]


def test_date_helpers():
    assert as_date("2026-11") == date(2026, 11, 1)
    assert as_date("2026-11-22T10:00:00Z") == date(2026, 11, 22)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 1)
    assert utc_iso(parse_utc("2026-11-22 10:00:00 +00:00")) == "2026-11-22T10:00:00Z"


def test_hash_chain_breaks_on_any_edit():
    rows = []
    head = chain.GENESIS
    for i in range(3):
        payload = {"n": i, "point": 5}
        this = chain.row_hash(head, payload)
        rows.append((head, this, payload))
        head = this
    assert chain.verify_chain(rows) == (True, head, 3)
    rows[1] = (rows[1][0], rows[1][1], {"n": 1, "point": 6})
    ok, _, checked = chain.verify_chain(rows)
    assert ok is False and checked == 1
    with pytest.raises(ValueError):
        chain.canonical_json({"x": float("nan")})


def test_as_date_of_text_with_an_offset_is_the_utc_date():
    from datetime import datetime, timedelta, timezone

    moment = datetime(2026, 11, 22, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert as_date(moment) == date(2026, 11, 23)
    assert as_date("2026-11-22T23:30:00-05:00") == date(2026, 11, 23)
    assert as_date("2026-11-23T01:00:00+05:00") == date(2026, 11, 22)
    assert as_date("2026-11-22T10:00:00Z") == date(2026, 11, 22)
    assert as_date("2026-11-22 23:59:59") == date(2026, 11, 22)  # naive text means UTC
    assert as_date("2026-11-22") == date(2026, 11, 22) and as_date("2026-11") == date(2026, 11, 1)


def test_band_prior_is_fitted_per_tercile_on_training_cells_only():
    training = (
        [GradedCell(f"t{i}.example", "q", 1, 5, difficulty="easy") for i in range(4)]
        + [GradedCell(f"t{i}.example", "q2", 1, 14, difficulty="medium") for i in range(4)]
        + [GradedCell(f"t{i}.example", "q3", 1, NT50, difficulty="hard") for i in range(4)]
        + [GradedCell("brand.example", "brand", 1, 1, difficulty="hard", is_brand=True)]
    )
    prior = fit_band_prior(training)
    assert RULE_V1.is_hit(prior["easy"], 5) and RULE_V1.is_hit(prior["medium"], 14) and prior["hard"] == NT50
    assert prior["easy"] == 3 and prior["medium"] == 9  # the smallest point that hits every training cell
    held_out = [GradedCell("h.example", "x", 9, 9, difficulty="hard"), GradedCell("h.example", "y", 9, 9)]
    assert band_prior_points(prior, held_out) == [NT50, prior["all"]]
    with pytest.raises(ValueError):
        fit_band_prior([])
