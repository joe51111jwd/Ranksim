"""Offline tests for part 6, the placement model (forecast/placement.py).

Hand-made frames only. Every number here tests the code. None is accuracy.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import placement as P
from aeo.ranksim.forecast.config import LOCKED_MIN_PROB, NT50, OUT_OF_REACH_MIN_PROB
from aeo.ranksim.forecast.grading import RULE_V1
from aeo.ranksim.forecast.types import PREDICTED, SYNTHETIC


def _table(n: int = 2400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    latent = 30 + 14 * x + rng.normal(scale=8, size=n)
    pos = np.clip(np.round(latent), 1, NT50).astype(int)
    return pd.DataFrame({"x": x, "noise": rng.normal(size=n), "position_out": pos,
                         "search_group": [f"g{i % 240}" for i in range(n)]})


@pytest.fixture(scope="module")
def fitted() -> tuple[P.PlacementModel, pd.DataFrame]:
    frame = _table()
    return P.fit_placement(frame, ["x", "noise"], training_label=SYNTHETIC), frame


def test_distribution_has_51_outcomes_and_sums_to_one(fitted):
    model, frame = fitted
    dists = model.distributions(frame.head(50))
    assert dists.shape == (50, 51)
    assert np.allclose(dists.sum(axis=1), 1.0)
    assert (dists >= 0).all()
    bands = model.band_probabilities(frame.head(50))
    assert np.allclose(P.band_mass(dists), bands, atol=1e-9)


def test_point_is_the_median_and_range_holds_it(fitted):
    model, frame = fitted
    for dist in model.distributions(frame.head(40)):
        info = model.summarize(dist)
        cum = np.cumsum(dist)
        k = P.outcome_index(info["point"])
        assert cum[k] >= 0.5 - 1e-9 and (k == 0 or cum[k - 1] < 0.5)
        assert info["range_low"] <= info["point"] <= info["range_high"]
    assert model.point_rule == "median"


def test_forecast_is_predicted_with_label_and_versioned(fitted):
    model, frame = fitted
    dist = model.distributions(frame.head(1))[0]
    fc = model.forecast(dist, site="a.example", search="s", stage="upload", as_of=date(2026, 1, 1))
    assert fc.kind == PREDICTED
    assert fc.label in ("locked", "contested", "out_of_reach")
    assert fc.model_version.startswith("placement-v1:")
    assert set(fc.probs) == {"1-3", "4-10", "11-20", "21-50", "NT50"}
    assert fc.notes["training_label"] == SYNTHETIC
    assert "rule_gaming_point" in fc.notes


def test_label_thresholds_are_the_frozen_ones():
    assert P.label_for([0, 0, 0, 0.2, 0.8]) == "out_of_reach"
    assert P.label_for([0.0, 0.85, 0.1, 0.05, 0.0]) == "locked"
    assert P.label_for([0.3, 0.3, 0.2, 0.1, 0.1]) == "contested"
    assert P.label_for([0.0, 0.0, 0.0, 0.21, 0.79]) == "contested"
    model = P.fit_placement(_table(200), ["x"], training_label=SYNTHETIC)
    assert model.locked_min_prob == LOCKED_MIN_PROB and model.out_of_reach_min_prob == OUT_OF_REACH_MIN_PROB


def test_temperature_and_reliability_on_the_calibration_fold(fitted):
    model, frame = fitted
    assert model.calibration_rows > 0 and model.training_rows > 0
    assert model.training_rows + model.calibration_rows == len(frame)
    cal = model.calibration
    assert cal["temperature"] == model.temperature
    for key in ("before_temperature", "after_temperature"):
        block = cal[key]
        assert set(block["bands"]) == {"1-3", "4-10", "11-20", "21-50", "NT50"}
        assert 0 <= block["top_label_ece"] <= 1
    assert cal["range_coverage_on_calibration"] >= 0.80  # chosen on the calibration fold to reach RANGE_COVERAGE


def test_calibration_fold_keeps_whole_groups_apart():
    frame = _table(1200)
    from aeo.ranksim.forecast.features import stable_share

    groups = frame["search_group"].astype(str)
    cal = groups.map(lambda g: stable_share(g, P.SEED + 11) < P.CALIBRATION_SHARE)
    assert not set(groups[cal]) & set(groups[~cal])


def test_temperature_recovers_an_overconfident_model():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 5, 2000)
    sharp = np.full((2000, 5), 0.02)
    sharp[np.arange(2000), np.where(rng.random(2000) < 0.4, y, (y + 1) % 5)] = 0.92  # right 40% of the time, says 92%
    t = P.fit_temperature(sharp, y)
    assert t > 1.0
    assert P.reliability(P.apply_temperature(sharp, t), y)["top_label_ece"] < P.reliability(sharp, y)["top_label_ece"]


def test_expected_hit_uses_the_grading_rule_and_gaming_point_differs_from_median():
    dist = np.zeros(51)
    dist[[11, 13, 17, 19]] = 0.25  # positions 12, 14, 18, 20
    assert P.dist_median(dist) == 14
    assert P.expected_hit(dist, 14) == pytest.approx(sum(0.25 for a in (12, 14, 18, 20) if RULE_V1.is_hit(14, a)))
    point, value = P.best_point(dist)
    assert value == pytest.approx(1.0) and point in range(15, 18)  # 15 hits all of 11 to 20 under rule v1
    assert P.expected_hit(dist, point) >= P.expected_hit(dist, 14)


def test_tiny_table_falls_back_to_the_smoothed_share():
    model = P.fit_placement(_table(25), ["x"], training_label=SYNTHETIC)
    assert model.band_booster_text is None
    assert np.isclose(sum(model.prior_band_probs), 1.0)
    dists = model.distributions(_table(3))
    assert np.allclose(dists[0], dists[1])


def test_refuses_missing_outcome_and_outcome_as_feature_and_opr():
    frame = _table(100)
    bad = frame.astype({"position_out": float})
    bad.loc[0, "position_out"] = np.nan
    with pytest.raises(P.PlacementError):
        P.fit_placement(bad, ["x"], training_label=SYNTHETIC)
    with pytest.raises(ValueError):
        P.fit_placement(frame, ["x", "position_out"], training_label=SYNTHETIC)
    with pytest.raises(ValueError):
        P.fit_placement(frame.assign(st_s_opr=1.0), ["x", "st_s_opr"], training_label=SYNTHETIC)


def test_fit_is_deterministic_and_save_load_checks_the_hash(tmp_path, fitted):
    model, frame = fitted
    again = P.fit_placement(frame, ["x", "noise"], training_label=SYNTHETIC)
    assert again.sha256() == model.sha256()
    path = tmp_path / "m.json"
    digest = P.save_model(model, path)
    loaded = P.load_model(path)
    assert loaded.sha256() == model.sha256() == digest or loaded.sha256() == model.sha256()
    assert np.allclose(loaded.distributions(frame.head(5)), model.distributions(frame.head(5)))
    path.write_text(path.read_text().replace('"median"', '"mode"'))
    with pytest.raises(P.PlacementError):
        P.load_model(path)


def test_missing_feature_stays_nan_and_mostly_nt50_table_trains():
    rng = np.random.default_rng(3)
    n = 900
    pos = np.where(rng.random(n) < 0.8, NT50, rng.integers(1, 51, n))
    frame = pd.DataFrame({"x": np.where(rng.random(n) < 0.3, np.nan, rng.normal(size=n)), "position_out": pos,
                          "search_group": [f"s{i % 90}" for i in range(n)]})
    model = P.fit_placement(frame, ["x", "absent_column"], training_label=SYNTHETIC)
    probs = model.band_probabilities(frame)
    assert probs[:, -1].mean() > 0.6
    assert np.isnan(P.matrix(frame, ["absent_column"])).all()


def test_point_diagnostics_reports_median_against_gaming_point(fitted):
    model, frame = fitted
    dists = model.distributions(frame.head(100))
    diag = P.point_diagnostics(model, dists, frame["position_out"].head(100).tolist())
    assert diag["expected_hit_of_rule_gaming_point"] >= diag["expected_hit_of_median"]
    assert 0 <= diag["real_hit_of_median"] <= 1


# ---------------------------------------------------------------------------
# time-aware calibration (version 2)


def test_recent_slice_reads_only_dates_and_falls_back_below_the_minimum():
    end = date(2026, 1, 1)
    dates = [date(2025, 6, 1), date(2025, 10, 3), date(2025, 12, 31), None, date(2026, 1, 1)]
    mask, record = P.recent_slice(dates, end, days=90, min_rows=2)
    assert mask.tolist() == [False, True, True, False, False]  # the end day itself is not before the end
    assert record["used"] == "recent" and record["rows_used"] == 2
    mask, record = P.recent_slice(dates, end, days=90, min_rows=3)
    assert mask.all() and record["used"] == "all" and "under 3" in record["why"]
    mask, record = P.recent_slice(dates, None)
    assert mask.all() and record["used"] == "all"
    base = np.array([True, False, True, True, True])
    mask, _ = P.recent_slice(dates, end, days=90, min_rows=1, base=base)
    assert mask.tolist() == [False, False, True, False, False]


def test_time_aware_calibration_sets_temperature_and_range_on_the_recent_slice_only():
    from aeo.ranksim.forecast.features import stable_share

    frame = _table(3000, seed=3)
    frame["outcome_date"] = [date(2025, 5, 1) + pd.Timedelta(days=int(i % 240)).to_pytimedelta() for i in range(len(frame))]
    end = date(2026, 1, 1)
    model = P.fit_placement(frame, ["x", "noise"], training_label=SYNTHETIC, calibration_date_col="outcome_date",
                            calibration_end=end, recent_days=90, recent_min_rows=50)
    is_cal = np.array([stable_share(g, P.SEED + 11) < P.CALIBRATION_SHARE for g in frame["search_group"]])
    recent = is_cal & np.array([d >= date(2025, 10, 3) for d in frame["outcome_date"]])
    record = model.calibration["slice"]
    assert record["used"] == "recent" and record["rows_used"] == int(recent.sum()) < int(is_cal.sum())
    y = np.array([P.band_index(p) for p in frame.loc[recent, "position_out"]])
    assert model.temperature == P.fit_temperature(model.raw_band_probabilities(frame[recent]), y)
    old = P.fit_placement(frame, ["x", "noise"], training_label=SYNTHETIC)
    assert old.calibration["slice"]["used"] == "all" and old.band_booster_text == model.band_booster_text
    fallback = P.fit_placement(frame, ["x", "noise"], training_label=SYNTHETIC, calibration_date_col="outcome_date",
                               calibration_end=end, recent_days=90, recent_min_rows=10_000)
    assert fallback.calibration["slice"]["used"] == "all" and fallback.temperature == old.temperature
