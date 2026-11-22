"""Offline tests for the backtest harness (forecast/backtest.py).

Tiny hand-made panels and a small synthetic world. Synthetic numbers test the
code; they are SYNTHETIC and never accuracy. No network, no downloaded data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import backtest as B
from aeo.ranksim.forecast.asof import LeakError
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.grading import GradedCell
from aeo.ranksim.forecast.markers import evaluate_marker
from aeo.ranksim.forecast.placement import fit_placement
from aeo.ranksim.forecast.report import render_text
from aeo.ranksim.forecast.scoreboard import PoolingRefused
from aeo.ranksim.forecast.strength import SplitRule
from aeo.ranksim.forecast.types import REAL, SYNTHETIC
from aeo.ranksim.forecast.updater import Reading

STAGE_REPORT_KEYS = {
    "kind", "source", "stage", "confirmed_stage", "as_of", "model_version", "label", "chain", "grading_rule",
    "grading_rule_hash", "lead_floor_days", "excluded_short_lead", "excluded_after_stage_day", "baselines_required",
    "baselines_missing", "band_prior", "minimum_count", "headline", "eligibility", "eligibility_this", "by_difficulty",
    "by_niche", "by_lead_days", "built_by", "held_out", "target_url", "core_updates", "regrades", "notes",
}


def _panel(pages_spec: list[tuple[str, str, int, dict[str, int]]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(site rows, pages) from (keyword, iso day, depth, {site: rank}) tuples, in the shape load_panel_pages returns."""
    rows, pages = [], []
    for keyword, day, depth, ranks in pages_spec:
        t = pd.Timestamp(day, tz="UTC")
        pages.append({"keyword_id": f"id-{keyword}", "keyword": keyword, "locale": "US_en", "snapshot_time": t,
                      "snapshot_date": t.date(), "snapshot_depth": depth, "kd": 30.0, "search_volume": 100})
        for site, rank in ranks.items():
            rows.append({"keyword_id": f"id-{keyword}", "snapshot_time": t, "date": t.date(), "site": site,
                         "rank100": rank, "snapshot_depth": depth})
    pages_df = pd.DataFrame(pages).sort_values(["keyword_id", "snapshot_time"]).reset_index(drop=True)
    pages_df["page_idx"] = pages_df.groupby("keyword_id").cumcount()
    pages_df["date"] = pages_df["snapshot_date"]
    pages_df["test_search"] = False
    pages_df["search_group"] = pages_df["keyword"]
    sp = pd.DataFrame(rows).merge(pages_df[["keyword_id", "snapshot_time", "page_idx"]], on=["keyword_id", "snapshot_time"])
    sp["first_idx"] = sp.groupby(["keyword_id", "site"])["page_idx"].transform("min")
    return sp, pages_df


def _toy_panel():
    return _panel([
        ("plumber", "2026-01-01", 100, {"a.com": 3, "b.com": 20, "c.com": 70}),
        ("plumber", "2026-01-02", 100, {"a.com": 4, "b.com": 22}),          # a next-day pull (ceiling)
        ("plumber", "2026-03-01", 100, {"a.com": 5, "d.com": 9}),           # 59 days after page 0
        ("plumber", "2026-05-10", 100, {"a.com": 2, "b.com": 40, "d.com": 1}),
        ("roofer", "2026-01-05", 100, {"x.com": 1, "y.com": 30}),
        ("roofer", "2026-02-20", 30, {"x.com": 2}),                          # shallow page: y.com absent is unknown
    ])


# ---------------------------------------------------------------------------
# cells


def test_reading_cells_take_the_first_page_in_the_window_and_respect_depth():
    sp, pages = _toy_panel()
    cells, counts = B.reading_cells(sp, pages, 28, 74.999)
    plumber = cells[cells["keyword_id"] == "id-plumber"]
    first = plumber[plumber["page_idx"] == 0].set_index("site")
    assert set(first.index) == {"a.com", "b.com", "c.com"}  # chosen from the reading page only
    assert (first["page_idx_b"] == 2).all()  # the first later page inside 28 to 75 days, not the next-day pull
    assert first.loc["a.com", "position_out"] == 5
    assert first.loc["b.com", "position_out"] == NT50  # absent from a 100-deep page
    assert first.loc["c.com", "position_in"] == NT50 and first.loc["c.com", "reading_position"] == NT50
    assert np.isnan(first.loc["a.com", "new_to_search"])  # a search's first page cannot tell new from old
    roofer = cells[cells["keyword_id"] == "id-roofer"]
    assert list(roofer["site"]) == ["x.com"]  # y.com absent from a 30-deep page: dropped, not NT50
    assert counts["dropped_absent_from_shallow_outcome_page"] == 1
    assert (cells["lead_days"] >= 28).all()


def test_previous_reading_and_move():
    sp, pages = _toy_panel()
    cells, _ = B.reading_cells(sp, pages, 28, 74.999)
    row = cells[(cells["keyword_id"] == "id-plumber") & (cells["page_idx"] == 2) & (cells["site"] == "a.com")].iloc[0]
    assert row["position_prev"] == 4 and row["reading_move"] == -1 and row["readings_count"] == 2
    d = cells[(cells["page_idx"] == 2) & (cells["site"] == "d.com")].iloc[0]
    assert d["position_prev"] == NT50 and d["new_to_search"] == 1.0  # not on the earlier 100-deep pages


def test_entrant_cells_use_an_input_page_at_least_28_days_earlier():
    sp, pages = _toy_panel()
    ent, _ = B.entrant_cells(sp, pages, B.PanelDesign())
    d = ent[ent["site"] == "d.com"].iloc[0]
    assert d["position_out"] == 9 and d["page_idx"] == 1  # input page 2026-01-02, 58 days before
    assert d["as_of"] == date(2026, 1, 2) and d["lead_days"] == 58
    assert np.isnan(d["rank100_in"])


def test_ceiling_cells_pair_pulls_under_two_days_apart():
    sp, pages = _toy_panel()
    ceil = B.ceiling_cells(sp, pages, 2.0)
    assert set(ceil["site"]) == {"a.com", "b.com", "c.com"}
    assert ceil.set_index("site").loc["c.com", "position_out"] == NT50


# ---------------------------------------------------------------------------
# refusals


def test_a_feature_dated_after_the_forecast_date_is_refused():
    frame = pd.DataFrame({"as_of": [date(2026, 1, 10)], "input_date": [date(2026, 1, 11)], "outcome_date": [date(2026, 3, 1)]})
    with pytest.raises(LeakError):
        B.check_inputs_dated(frame)
    ok = frame.assign(input_date=date(2026, 1, 10))
    B.check_inputs_dated(ok)
    with pytest.raises(LeakError):
        B.check_inputs_dated(ok.assign(outcome_date=date(2026, 1, 10)))


def test_a_planted_future_input_through_forecast_cells_is_refused():
    rng = np.random.default_rng(0)
    train = pd.DataFrame({"x": rng.normal(size=200), "position_out": rng.integers(1, 52, 200),
                          "search_group": [f"g{i % 20}" for i in range(200)]})
    model = fit_placement(train, ["x"], training_label=SYNTHETIC)

    def provider(site, search, day):
        return {"x": 0.3}, {"input_date": day, "strength_date": day + timedelta(days=1)}  # planted future value

    models = B.ForecastModels(placement=model, features=provider)
    with pytest.raises(LeakError):
        B.forecast_cells("a.example", ["s1"], date(2026, 1, 1), "upload", {}, None, models)

    def honest(site, search, day):
        return {"x": 0.3}, {"input_date": day - timedelta(days=3), "strength_date": day}

    out = B.forecast_cells("a.example", ["s1", "s2"], date(2026, 1, 1), "upload", {}, None,
                           B.ForecastModels(placement=model, features=honest), outcome_date=date(2026, 4, 1))
    assert [f.search for f in out] == ["s1", "s2"]
    assert out[0].notes["input_dates"]["strength_date"] == "2026-01-01"
    with pytest.raises(LeakError):  # a reading after the forecast date
        B.forecast_cells("a.example", ["s1"], date(2026, 1, 1), "d7", {"s1": [Reading(4, date(2026, 1, 2))]}, None,
                         B.ForecastModels(placement=model, features=honest))


def test_a_graded_cell_from_the_training_side_is_refused():
    split = SplitRule(search_test_share=0.5, site_test_share=0.5, time_cutoff="2026-01-01")
    keywords = [f"search number {i}" for i in range(40)]
    test_kw = next(k for k in keywords if split.search_is_test(k))
    train_kw = next(k for k in keywords if not split.search_is_test(k))
    test_site = next(s for s in (f"site{i}.com" for i in range(200)) if split.site_is_test(s))
    B.check_held_out(pd.DataFrame({"keyword": [test_kw], "site": [test_site], "as_of": [date(2026, 2, 1)]}), split)
    with pytest.raises(B.BacktestRefusal):
        B.check_held_out(pd.DataFrame({"keyword": [train_kw], "site": [test_site], "as_of": [date(2026, 2, 1)]}), split)
    with pytest.raises(B.BacktestRefusal):
        B.check_held_out(pd.DataFrame({"keyword": [test_kw], "site": [test_site], "as_of": [date(2025, 12, 31)]}), split)
    with pytest.raises(B.BacktestRefusal):  # no site column: the site side cannot be checked
        B.check_held_out(pd.DataFrame({"keyword": [test_kw], "as_of": [date(2026, 2, 1)]}), split)


def test_an_outcome_page_too_shallow_to_say_nt50_is_refused():
    assert B.outcome_position(7, 30) == 7
    assert B.outcome_position(None, 60) == NT50
    assert B.outcome_position(77, 100) == NT50
    with pytest.raises(B.ShallowOutcome):
        B.outcome_position(None, 30)
    with pytest.raises(B.ShallowOutcome):
        B.check_outcome_depth(pd.DataFrame({"rank100_out": [np.nan], "depth_outcome": [40]}))
    B.check_outcome_depth(pd.DataFrame({"rank100_out": [np.nan, 12.0], "depth_outcome": [50, 20]}))


def test_pooling_backtest_cells_with_real_graded_outcomes_is_refused():
    cells = [GradedCell("a", "s", 5, 5, source="backtest"), GradedCell("b", "s", 5, 5, source="seed")]
    with pytest.raises(PoolingRefused):
        B.refuse_pooling(cells)
    with pytest.raises(PoolingRefused):
        B.pool_with_real(cells)
    with pytest.raises(PoolingRefused):
        B.backtest_report(cells, {}, stage="d30", as_of=date(2026, 5, 1), model_version="m",
                          outcome_dates=[date(2026, 5, 1)] * 2, as_ofs=[date(2026, 3, 1)] * 2, band_prior_training=None)


# ---------------------------------------------------------------------------
# freezing and versioned runs


def _spec(name: str = "toy", **changes) -> B.BacktestSpec:
    base = dict(name=name, dataset="toy", split={"rule": "toy"}, time_cutoff="2026-01-01",
                windows_days={"lead": [28, 75]}, feature_set=["x"], model_versions={"placement": "placement-v1"},
                baselines=["always_nt50", "persistence"], thresholds={"t": 1}, cells="toy cells")
    base.update(changes)
    return B.BacktestSpec(**base)


def test_spec_is_frozen_before_scoring_and_never_edited(tmp_path):
    spec = _spec()
    with pytest.raises(FileNotFoundError):
        B.require_spec(spec, tmp_path)
    sha = B.freeze_spec(spec, tmp_path)
    assert B.require_spec(spec, tmp_path) == sha == spec.sha256()
    stored = json.loads((tmp_path / "toy" / "prereg.json").read_text())
    assert stored["sha256"] == sha
    with pytest.raises(ValueError):
        B.freeze_spec(_spec(windows_days={"lead": [28, 90]}), tmp_path)
    with pytest.raises(ValueError):
        B.require_spec(_spec(windows_days={"lead": [28, 90]}), tmp_path)


def test_a_rerun_gets_a_new_version_and_the_old_one_stays(tmp_path):
    first = B.write_run("toy", {"label": REAL, "n": 1}, prereg_sha256="abc", root=tmp_path)
    second = B.write_run("toy", {"label": REAL, "n": 2}, prereg_sha256="abc", root=tmp_path)
    assert (first["run_version"], second["run_version"]) == (1, 2)
    assert json.loads((tmp_path / "toy" / "report-v1.json").read_text())["n"] == 1
    assert json.loads((tmp_path / "toy" / "report-v2.json").read_text())["n"] == 2
    manifest = json.loads((tmp_path / "toy" / "manifest-v2.json").read_text())
    assert manifest["prereg_sha256"] == "abc" and len(manifest["report_sha256"]) == 64


# ---------------------------------------------------------------------------
# report shape and markers


def _cells(n: int = 120, source: str = "backtest", stage: str = "d30") -> list[GradedCell]:
    rng = np.random.default_rng(4)
    actual = np.where(rng.random(n) < 0.5, NT50, rng.integers(1, 51, n))
    point = np.where(rng.random(n) < 0.7, actual, NT50)
    return [GradedCell(site=f"site{i % 30}", search=f"q{i % 40}", point=int(p), actual=int(a), source=source, stage=stage,
                       lead_days=60, difficulty=("easy", "medium", "hard")[i % 3], niche="US_en")
            for i, (p, a) in enumerate(zip(point, actual))]


def test_report_has_the_stage_report_shape_and_markers_run_on_it():
    cells = _cells()
    train = [GradedCell(site=f"t{i}", search=f"tq{i}", point=NT50, actual=NT50 if i % 2 else 30,
                        difficulty=("easy", "medium", "hard")[i % 3]) for i in range(60)]
    report = B.backtest_report(cells, {"persistence": [c.point for c in cells], "site_strength": [NT50] * len(cells)},
                               stage="d30", as_of=date(2026, 5, 1), model_version="m", outcome_dates=[date(2026, 4, 1)] * 120,
                               as_ofs=[date(2026, 2, 1)] * 120, band_prior_training=train, held_out=[True] * 120)
    assert STAGE_REPORT_KEYS <= set(report)
    assert report["source"] == "backtest" and report["label"] == REAL
    assert report["held_out"] == {"test": 120, "not_test": 0} and report["every_graded_cell_held_out"]
    assert report["single_snapshot_labels"] is True
    assert report["real_lead_days"]["median"] == 60
    assert report["baselines_missing"] == []
    assert report["headline"]["summary"]["cluster_by"] == "site"
    assert report["headline_site_clusters"]["summary"]["cluster_by"] == "site"
    assert report["headline_search_clusters"]["summary"]["cluster_by"] == "search"
    m4 = evaluate_marker(4, report)
    assert m4["label"] == REAL and m4["passed"] in (True, False, None)
    up = B.backtest_report(_cells(stage="upload"), {"persistence": [NT50] * 120, "site_strength": [NT50] * 120},
                           stage="upload", as_of=date(2026, 5, 1), model_version="m", outcome_dates=[date(2026, 4, 1)] * 120,
                           as_ofs=[date(2026, 2, 1)] * 120, band_prior_training=train, held_out=[True] * 120)
    m3 = evaluate_marker(3, up)
    assert any(c["name"] == "only held out launches are graded" and c["ok"] for c in m3["checks"])
    assert "Ranksim stage report" in render_text(report)


def test_short_lead_rows_are_not_called_forecasts():
    cells = _cells(40)
    cells[0] = GradedCell("s", "q", 5, 5, source="backtest", lead_days=3)
    report = B.backtest_report(cells, {"persistence": [5] * 40}, stage="d30", as_of=date(2026, 5, 1), model_version="m",
                               outcome_dates=[date(2026, 4, 1)] * 40, as_ofs=[date(2026, 2, 1)] * 40, band_prior_training=None)
    assert report["excluded_short_lead"] == 1 and report["headline"]["forecasts"] == 39
    assert "site_strength" in report["baselines_missing"]


# ---------------------------------------------------------------------------
# the synthetic pipeline


@pytest.fixture(scope="module")
def synthetic_run():
    design = B.SyntheticDesign(seed=5, test_sites=3, train_sites=9, niches=1, searches_per_niche=12, established_sites=60)
    return B.run_synthetic(design, full_report=True)


def test_synthetic_pipeline_runs_every_stage_and_is_labelled(synthetic_run):
    run = synthetic_run
    assert run["label"] == SYNTHETIC
    assert set(run["stages"]) == {"upload", "d7", "d14", "d30", "d60"}
    for stage, report in run["stages"].items():
        assert report["label"] == SYNTHETIC and report["source"] == "synthetic"
        assert report["headline"]["forecasts"] == run["test_cells"]
        assert {"always_nt50", "persistence", "band_prior", "site_strength"} <= set(report["headline"]["baselines"])
    upload = run["stages"]["upload"]
    assert upload["marker_3_synthetic"]["passed"] is None and upload["marker_3_synthetic"]["label"] == SYNTHETIC
    assert run["stages"]["d30"]["marker_4_synthetic"]["passed"] is None
    # persistence at upload has no reading: it is always NT50
    assert upload["headline"]["baselines"]["persistence"]["baseline_rate"] == upload["headline"]["baselines"]["always_nt50"]["baseline_rate"]
    assert run["updater"]["in_use"] in ("table", "model") and run["updater"]["reason"]


def test_synthetic_report_refuses_band_prior_from_graded_sites():
    cells = _cells(40, source="synthetic")
    with pytest.raises(B.BacktestRefusal):
        B.backtest_report(cells, {"persistence": [NT50] * 40}, stage="d30", as_of=date(2026, 5, 1), model_version="m",
                          outcome_dates=[date(2026, 4, 1)] * 40, as_ofs=[date(2026, 2, 1)] * 40,
                          band_prior_training=[GradedCell(cells[0].site, "x", NT50, NT50)], source="synthetic")


# ---------------------------------------------------------------------------
# brand searches, site-clustered headline intervals, computed held-out counts


def test_backtest_flags_brand_searches_and_leaves_them_out_of_the_headline():
    frame = pd.DataFrame({"site": ["facebook.com", "plumbco.com", "nike.com"], "keyword_id": ["k1", "k2", "k3"],
                          "keyword": ["facebook login", "emergency plumber", "Nike Running Shoes"],
                          "position_out": [1, 51, 1], "lead_days": [60, 60, 60], "difficulty": ["easy"] * 3,
                          "locale": ["US_en"] * 3})
    cells = B.graded_cells_from(frame, [1, 51, 1], stage="d30")
    assert [c.is_brand for c in cells] == [True, False, True]
    assert cells[0].search == "k1"  # the cluster key stays the search id; the flag reads the search text
    rep = B.backtest_report(cells, {"persistence": [1, 51, 1], "site_strength": [51, 51, 51]}, stage="d30",
                            as_of=date(2026, 4, 1), model_version="m", outcome_dates=[date(2026, 4, 1)] * 3,
                            as_ofs=[date(2026, 2, 1)] * 3, band_prior_training=None, held_out=[True] * 3)
    assert rep["headline"]["brand_forecasts_shown_apart"] == 2 and rep["headline"]["forecasts"] == 1
    assert any("2 brand searches" in n for n in rep["notes"])


def test_brand_rule_follows_the_scoreboard_and_needs_a_whole_word_for_a_short_label():
    assert B.is_brand_search("hp.com", "hp laptop deals")
    assert not B.is_brand_search("x.com", "xbox controller")
    assert B.is_brand_search("joes-plumbing.com", "joes plumbing reviews")
    assert B.is_brand_search("acme.com", "roadrunner traps", brand_terms=["Road Runner"])
    assert not B.is_brand_search("acme.com", "acme anvils", brand_terms=["Road Runner"])  # registered terms replace the label


def test_marker_3_gain_bound_resamples_sites_not_searches():
    cells, persistence, strength = [], [], []
    for i in range(30):  # one site, right on 30 searches where every baseline misses
        cells.append(GradedCell(site="one-lucky-site.com", search=f"s{i}", point=5, actual=5, source="backtest",
                                stage="upload", lead_days=60))
        persistence.append(30)
        strength.append(51)
    for i in range(10):  # ten other sites, model and baselines equal
        cells.append(GradedCell(site=f"other{i}.com", search=f"t{i}", point=51, actual=51, source="backtest",
                                stage="upload", lead_days=60))
        persistence.append(51)
        strength.append(51)
    training = [GradedCell(site=f"train{i}.com", search=f"u{i}", point=51, actual=51) for i in range(20)]
    n = len(cells)
    rep = B.backtest_report(cells, {"persistence": persistence, "site_strength": strength}, stage="upload",
                            as_of=date(2026, 12, 21), model_version="m", outcome_dates=[date(2026, 4, 1)] * n,
                            as_ofs=[date(2026, 2, 1)] * n, band_prior_training=training, held_out=[True] * n)
    assert rep["headline"]["summary"]["cluster_by"] == "site"
    assert rep["headline_search_clusters"]["summary"]["cluster_by"] == "search"
    verdict = evaluate_marker(3, rep)
    assert not verdict["passed"]


def test_backtest_report_never_certifies_cells_it_was_not_shown_to_be_held_out():
    cells = _cells(40, stage="upload")
    args = dict(stage="upload", as_of=date(2026, 4, 1), model_version="m", outcome_dates=[date(2026, 4, 1)] * 40,
                as_ofs=[date(2026, 2, 1)] * 40, band_prior_training=None)
    rep = B.backtest_report(cells, {"persistence": [51] * 40, "site_strength": [51] * 40}, **args)
    assert rep["held_out"]["not_test"] == 40 and rep["every_graded_cell_held_out"] is False
    check = next(c for c in evaluate_marker(3, rep)["checks"] if c["name"] == "only held out launches are graded")
    assert check["ok"] is not True
    with pytest.raises(B.BacktestRefusal):
        B.backtest_report(cells, {"persistence": [51] * 40, "site_strength": [51] * 40},
                          held_out=[True] * 39 + [False], **args)


def test_panel_sides_grade_only_held_out_sites_and_train_only_training_sites():
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    sites = [f"site{i}.com" for i in range(60)]
    test_kw = next(k for k in (f"search {i}" for i in range(200)) if DEFAULT_SPLIT.search_is_test(k))
    train_kw = next(k for k in (f"search {i}" for i in range(200)) if not DEFAULT_SPLIT.search_is_test(k))
    cells = pd.DataFrame({"site": sites * 2, "keyword": [test_kw] * 60 + [train_kw] * 60,
                          "test_search": [True] * 60 + [False] * 60, "search_group": ["g1"] * 60 + ["g2"] * 60,
                          "as_of": [date(2026, 2, 1)] * 60 + [date(2025, 3, 1)] * 60,
                          "outcome_date": [date(2026, 4, 1)] * 60 + [date(2025, 5, 1)] * 60})
    train, val, test = B._sides(cells, B.PanelDesign(validation_share=0.0))
    assert test.any() and train.any()
    assert all(DEFAULT_SPLIT.site_is_test(s) for s in cells.loc[test, "site"])
    assert not any(DEFAULT_SPLIT.site_is_test(s) for s in cells.loc[train | val, "site"])
    B.check_held_out(cells[test], DEFAULT_SPLIT)


# ---------------------------------------------------------------------------
# the trust and strength models the panel loads


def test_backtest_refuses_to_run_without_the_preregistered_trust_model(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import strength as S
    from aeo.ranksim.forecast import trust as T

    monkeypatch.setenv("RANKSIM_FORECAST_DIR", str(tmp_path / "data"))
    files = S.model_files(1)
    files["score"].parent.mkdir(parents=True)
    stub = S.StrengthScore(tuple(S.MODEL_MEASURES), {m: 0.0 for m in S.MODEL_MEASURES}, {m: 1.0 for m in S.MODEL_MEASURES},
                           {n: 0.0 for m in S.MODEL_MEASURES for n in (m, f"{m}__missing")}, 1.0)
    files["score"].write_text(json.dumps(stub.to_dict()))
    files["forecaster"].write_text(json.dumps(S.StrengthOnlyForecaster(
        ("own_score",), [0.0], [1.0], [[0.0]], [0.0], ["NT50"], {"NT50": {"51": 1.0}}, 1.0).to_dict()))
    assert T.default_models_dir() == tmp_path / "data" / "models" / "trust"
    with pytest.raises(B.BacktestRefusal, match="trust"):
        B._load_models(None)
    hashes = B.model_hashes(B.model_paths())
    assert hashes["strength_score"]["sha256"] and hashes["trust"]["sha256"] is None


def test_a_graded_row_dated_before_the_frozen_models_last_label_is_refused():
    rows = pd.DataFrame({"as_of": [date(2026, 2, 1), date(2025, 12, 1)]})
    marked = B.mark_model_labels_end(rows, date(2025, 12, 31))
    with pytest.raises(LeakError):
        B.check_model_inputs_dated(marked)
    B.check_model_inputs_dated(marked.iloc[:1])
    assert B.in_sample_model_inputs(marked)["rows"] == 1


# ---------------------------------------------------------------------------
# scored once, code provenance, design versions


def test_a_scored_design_is_never_scored_twice_and_its_manifest_records_the_code(tmp_path):
    first = B.write_run("toy", {"label": REAL, "n": 1}, prereg_sha256="abc", root=tmp_path, scored_once=True)
    assert {"code_revision", "source_dirty", "dirty_files", "source_tree_sha256", "source_sha256"} <= set(first)
    with pytest.raises(B.BacktestRefusal, match="scored once"):
        B.write_run("toy", {"label": REAL, "n": 2}, prereg_sha256="abc", root=tmp_path, scored_once=True)
    second = B.write_run("toy", {"label": REAL, "n": 3}, prereg_sha256="def", root=tmp_path, scored_once=True)
    assert second["run_version"] == 2
    assert json.loads((tmp_path / "toy" / "report-v1.json").read_text())["n"] == 1


def _frozen_panel_specs(version: int = 1, reason: str | None = None) -> tuple[dict, dict]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evals" / "forecast" / "backtest"
    frozen = {p.parent.name: json.loads(p.read_text()) for p in root.glob("panel_*/prereg.json")}
    th = frozen["panel_a_reading_to_outcome"]["payload"]["thresholds"]
    specs = B.panel_specs(B.PanelDesign(), gaps=th["consecutive_page_gap_distribution"],
                          cuts=th["difficulty_kd_cuts_from_training_pages"], version=version, reason=reason)
    return specs, frozen


def test_version_1_panel_designs_still_hash_to_their_frozen_sha_and_version_2_sits_beside_them(tmp_path):
    specs, frozen = _frozen_panel_specs()
    for spec in specs.values():
        assert spec.sha256() == frozen[spec.name]["sha256"], spec.name
    with pytest.raises(ValueError):
        _frozen_panel_specs(version=2)
    v2, _ = _frozen_panel_specs(version=2, reason="panel rebuilt with the data-layer fixes")
    for key, spec in v2.items():
        assert spec.version == "backtest-v2" and spec.sha256() != specs[key].sha256()
        assert any("panel rebuilt" in n for n in spec.notes)
    assert "held-out sites" in v2["A"].cells
    sha = B.freeze_spec(specs["A"], tmp_path)
    sha2 = B.freeze_spec(v2["A"], tmp_path, design_version=2, code=True)
    assert (tmp_path / v2["A"].name / "prereg.json").is_file() and (tmp_path / v2["A"].name / "prereg-v2.json").is_file()
    assert B.require_spec(specs["A"], tmp_path) == sha and B.require_spec(v2["A"], tmp_path, design_version=2) == sha2
