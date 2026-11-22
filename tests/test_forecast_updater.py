"""Offline tests for part 7, the early-reading updater (forecast/updater.py).

Hand-made frames only. Every number here tests the code. None is accuracy.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import updater as U
from aeo.ranksim.forecast.asof import LeakError
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.placement import band_mass, dist_median
from aeo.ranksim.forecast.types import REAL, SYNTHETIC


def _prior(band_probs: list[float]) -> np.ndarray:
    dist = np.zeros(51)
    for (low, high), p in zip(((1, 3), (4, 10), (11, 20), (21, 50), (51, 51)), band_probs):
        dist[low - 1:high] += p / (high - low + 1)
    return dist


def _rows(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    out = np.where(rng.random(n) < 0.6, NT50, rng.integers(1, 51, n))
    read = np.where(rng.random(n) < 0.7, out, rng.integers(1, 52, n)).astype(float)
    read = np.minimum(read, NT50)
    return pd.DataFrame({"reading_position": read, "prior_band": "NT50", "lead_days": 60, "new_to_search": True,
                         "position_out": out, "search_group": [f"g{i % 60}" for i in range(n)],
                         "keyword_id": [f"g{i % 60}" for i in range(n)], "site": [f"s{i}" for i in range(n)]})


def test_reading_after_the_forecast_date_is_refused():
    readings = [U.Reading(position=5, taken_on=date(2026, 1, 10))]
    with pytest.raises(LeakError):
        U.usable_readings(readings, date(2026, 1, 9))
    with pytest.raises(LeakError):
        U.reading_columns(readings, date(2026, 1, 9))


def test_search_console_positions_are_never_readings():
    with pytest.raises(U.UpdaterError):
        U.Reading(position=4, taken_on=date(2026, 1, 1), label="GSC")
    with pytest.raises(U.UpdaterError):
        U.Reading(position=0, taken_on=date(2026, 1, 1))


def test_reading_columns_give_the_move_between_the_last_two():
    got = U.reading_columns([U.Reading(30, date(2026, 1, 1)), U.Reading(None, date(2026, 1, 5)),
                             U.Reading(12, date(2026, 1, 8))], date(2026, 1, 9))
    assert got == {"reading_position": 12.0, "reading_in_top50": 1.0, "reading_move": 18.0, "readings_count": 2.0}
    one = U.reading_columns([U.Reading(NT50, date(2026, 1, 1))], date(2026, 1, 9))
    assert one["reading_in_top50"] == 0.0 and np.isnan(one["reading_move"])
    none = U.reading_columns([], date(2026, 1, 9))
    assert np.isnan(none["reading_position"]) and none["readings_count"] == 0.0


def test_lead_buckets_and_negative_lead():
    assert U.lead_bucket(30) == "28-45" and U.lead_bucket(83) == "76-130"
    with pytest.raises(U.UpdaterError):
        U.lead_bucket(-1)


def test_table_works_with_a_handful_of_rows_and_smooths_to_the_pool():
    rows = _rows(8)
    table = U.fit_table(rows, training_label=SYNTHETIC)
    probs = table.outcome_probs("4-10", "NT50", "46-75", "new")
    assert np.isclose(probs.sum(), 1.0) and (probs > 0).all()
    unseen = table.outcome_probs("1-3", "1-3", "131+", "unknown")  # a key never seen falls back to the pooled table
    assert np.isclose(unseen.sum(), 1.0)


def test_no_reading_returns_the_prior_and_a_reading_moves_it_by_bayes():
    rows = _rows(2000, seed=1)
    table = U.fit_table(rows, training_label=SYNTHETIC)
    prior = np.array([0.05, 0.1, 0.1, 0.15, 0.6])
    assert np.allclose(table.update_bands(prior, U.NO_READING, "NT50", "46-75", "new"), prior)
    post = table.update_bands(prior, "4-10", "NT50", "46-75", "new")
    assert post[1] > prior[1] and np.isclose(post.sum(), 1.0)
    # With the prior equal to the table's own base rate, Bayes' rule returns the table's answer.
    base = table.base_probs("NT50", "46-75", "new")
    assert np.allclose(table.update_bands(base, "4-10", "NT50", "46-75", "new"),
                       table.outcome_probs("4-10", "NT50", "46-75", "new"))


def test_updated_distribution_centres_on_the_reading_inside_its_band():
    rows = _rows(2000, seed=2)
    table = U.fit_table(rows, training_label=SYNTHETIC)
    updater = U.Updater(table, None, "table", "test")
    prior = np.vstack([_prior([0.05, 0.1, 0.1, 0.15, 0.6])] * 2)
    frame = pd.DataFrame({"reading_position": [7.0, np.nan], "lead_days": [60, 60], "new_to_search": [True, True]})
    dists = updater.distributions(frame, prior)
    assert np.allclose(dists.sum(axis=1), 1.0)
    assert dists[0][6] == dists[0][3:10].max()  # most mass at position 7 inside 4 to 10
    assert np.allclose(dists[1], prior[1])  # no reading: the prior stands
    fc = updater.forecast(dists[0], site="a", search="s", stage="d30", as_of=date(2026, 1, 1))
    assert fc.notes["updater_in_use"] == "table" and fc.range_low <= fc.point <= fc.range_high


def test_choose_version_keeps_the_table_unless_the_model_wins_with_an_interval_above_zero():
    rows = _rows(1500, seed=3)
    prior = np.vstack([_prior([0.05, 0.1, 0.1, 0.15, 0.6])] * len(rows))
    cols = U.prior_columns(prior)
    for col in cols.columns:
        rows[col] = cols[col].to_numpy()
    rows["prior_band"] = cols["prior_band"]
    table = U.fit_table(rows, training_label=SYNTHETIC)
    chosen = U.choose_version(table, None, rows, prior)
    assert chosen.in_use == "table" and "no version 2" in chosen.reason
    model = U.fit_model(rows, training_label=SYNTHETIC)
    chosen = U.choose_version(table, model, rows, prior)
    low = chosen.comparison["model_minus_table"]["difference_interval"][0]
    assert (chosen.in_use == "model") == (low is not None and low > 0)
    assert chosen.reason


def test_model_needs_enough_rows_and_refuses_opr_features():
    rows = _rows(50)
    with pytest.raises(U.UpdaterError):
        U.fit_model(rows, training_label=SYNTHETIC)
    with pytest.raises(ValueError):
        U.fit_model(_rows(400).assign(st_s_opr=1.0), training_label=REAL, features=["reading_position", "st_s_opr"])


def test_missing_outcome_is_refused():
    rows = _rows(20).astype({"position_out": float})
    rows.loc[0, "position_out"] = np.nan
    with pytest.raises(U.UpdaterError):
        U.fit_table(rows, training_label=SYNTHETIC)


def test_prior_columns_follow_the_median():
    prior = np.vstack([_prior([0.0, 0.0, 0.0, 0.3, 0.7]), _prior([0.6, 0.4, 0, 0, 0])])
    cols = U.prior_columns(prior)
    assert list(cols["prior_band"]) == ["NT50", "1-3"]
    assert cols["prior_point"].tolist() == [float(dist_median(prior[0])), float(dist_median(prior[1]))]
    assert np.allclose(band_mass(prior)[:, -1], cols["prior_p_NT50"])


# ---------------------------------------------------------------------------
# version 2: the rule for sites new to the search, time-aware calibration


def _persistence_rows(n: int, seed: int, *, new: float, outcome_nt50: bool = False, prefix: str = "q") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    read = rng.integers(1, 51, n).astype(float)
    out = np.full(n, NT50) if outcome_nt50 else read.astype(int)
    return pd.DataFrame({"reading_position": read, "prior_band": "NT50", "lead_days": 60, "new_to_search": new,
                         "position_out": out, "search_group": [f"{prefix}{i % 80}" for i in range(n)],
                         "keyword_id": [f"{prefix}{i % 80}" for i in range(n)], "site": [f"{prefix}s{i}" for i in range(n)]})


def _flat_prior(n: int) -> np.ndarray:
    return np.tile(_prior([0.2, 0.2, 0.2, 0.2, 0.2]), (n, 1))


def test_new_site_rule_switches_to_always_nt50_only_when_it_beats_the_version_in_use_on_validation():
    train = pd.concat([_persistence_rows(800, 1, new=0.0), _persistence_rows(800, 2, new=1.0, prefix="r")], ignore_index=True)
    table = U.fit_table(train, training_label=REAL)
    val = pd.concat([_persistence_rows(400, 3, new=0.0, prefix="v"),
                     _persistence_rows(400, 4, new=1.0, outcome_nt50=True, prefix="w")], ignore_index=True)
    updater = U.choose_version(table, None, val, _flat_prior(len(val)))
    record = U.choose_segment(updater, val, _flat_prior(len(val)))
    assert record["winner"] == "always_nt50" and updater.segment_rules == {"new_to_search": "always_nt50"}
    assert record["validation_rows"] == 400 and record["validation_hit"]["always_nt50"] == 1.0
    assert record["gain_over_default"]["always_nt50"]["difference_interval"][0] > 0
    assert "0.820 against 0.867" in record["origin"]
    dist = updater.distributions(val.iloc[:1], _flat_prior(1))[0]
    new = updater.forecast(dist, site="a", search="q", stage="d30", as_of=date(2025, 9, 1), segments={"new_to_search": True})
    old = updater.forecast(dist, site="a", search="q", stage="d30", as_of=date(2025, 9, 1), segments={"new_to_search": False})
    assert new.point == NT50 and new.range_high == NT50 and new.notes["segment_rule"] == "always_nt50"
    assert old.point == dist_median(dist) and "segment_rule" not in old.notes
    points = updater.points(val, _flat_prior(len(val)))
    assert all(p == NT50 for p in points[400:]) and points[:400] != [NT50] * 400


def test_new_site_rule_keeps_the_version_in_use_when_no_candidate_beats_it():
    train = _persistence_rows(1200, 5, new=1.0)
    table = U.fit_table(train, training_label=REAL)
    val = _persistence_rows(400, 6, new=1.0, prefix="v")
    updater = U.choose_version(table, None, val, _flat_prior(len(val)))
    record = U.choose_segment(updater, val, _flat_prior(len(val)))
    assert record["winner"] == "table" == record["default"]
    assert updater.points(val, _flat_prior(len(val))) == [dist_median(d) for d in updater.distributions(val, _flat_prior(len(val)), "table")]
    empty = U.choose_segment(updater, val.assign(new_to_search=np.nan), _flat_prior(len(val)))
    assert empty["winner"] == "table" and empty["validation_rows"] == 0  # unknown newness is not new


def test_updater_range_level_is_set_on_recent_validation_rows():
    train = _persistence_rows(1200, 7, new=0.0)
    table = U.fit_table(train, training_label=REAL)
    old = _persistence_rows(600, 8, new=0.0, prefix="o")
    noisy = _persistence_rows(600, 9, new=0.0, prefix="n")
    noisy["position_out"] = np.random.default_rng(9).integers(1, 52, len(noisy))
    val = pd.concat([old.assign(outcome_date=date(2025, 6, 1)), noisy.assign(outcome_date=date(2025, 12, 1))], ignore_index=True)
    plain = U.choose_version(table, None, val, _flat_prior(len(val)))
    recent = U.choose_version(table, None, val, _flat_prior(len(val)), date_col="outcome_date", end=date(2026, 1, 1),
                              recent_days=90, recent_min_rows=100)
    assert recent.range_calibration["slice"]["used"] == "recent" and recent.range_calibration["slice"]["rows_used"] == 600
    assert plain.range_calibration["slice"]["used"] == "all" and plain.range_calibration["slice"]["rows_used"] == 1200
    from aeo.ranksim.forecast.placement import choose_range_level

    dists = plain.distributions(val, _flat_prior(len(val)), "table")
    actual = val["position_out"].to_numpy()
    assert recent.range_level == choose_range_level(dists[600:], actual[600:])
    assert plain.range_level == choose_range_level(dists, actual)


def test_updater_model_temperature_uses_the_recent_calibration_slice():
    rows = _rows(1500, seed=11)
    rows["prior_p_NT50"] = 0.5
    rows["outcome_date"] = [date(2025, 6, 1) if i % 2 else date(2025, 12, 1) for i in range(len(rows))]
    model = U.fit_model(rows, training_label=REAL, calibration_date_col="outcome_date", calibration_end=date(2026, 1, 1),
                        recent_days=90, recent_min_rows=20)
    assert model.calibration["slice"]["used"] == "recent"
    assert model.calibration["slice"]["rows_used"] < model.calibration["slice"]["candidate_rows"]


def test_updater_payload_round_trips_with_its_segment_rules():
    train = _persistence_rows(800, 12, new=1.0)
    table = U.fit_table(train, training_label=REAL)
    val = _persistence_rows(300, 13, new=1.0, outcome_nt50=True, prefix="v")
    updater = U.choose_version(table, None, val, _flat_prior(len(val)))
    U.choose_segment(updater, val, _flat_prior(len(val)))
    again = U.Updater.from_payload(updater.payload())
    assert again.segment_rules == updater.segment_rules and again.sha256() == updater.sha256()
    legacy = {k: v for k, v in updater.payload().items() if k not in ("segment_rules", "segment_comparison", "range_calibration")}
    assert U.Updater.from_payload(legacy).segment_rules == {}
