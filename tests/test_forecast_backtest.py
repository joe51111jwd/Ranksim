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
    files = S.model_files(B.STRENGTH_MODEL_VERSION)
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


# ---------------------------------------------------------------------------
# version 2: out-of-fold model inputs, stand-in reports, reports written at the end, v2 wiring


class _ConstScore:
    def __init__(self, value: float):
        self.value = value

    def score(self, frame):
        return np.full(len(frame), self.value, dtype=float)


class _ConstTrust(_ConstScore):
    def predict(self, frame):
        return np.full(len(frame), self.value, dtype=float)


def _oof_inputs(sp, pages, cells, *, leak_site: str | None = None, strength_sites: frozenset | None = frozenset(),
                main_strength_sites: frozenset | None = frozenset()) -> B.ModelInputs:
    from aeo.ranksim.forecast.trust import FoldTrust

    groups = set(pages["search_group"].astype(str))
    sites = set(sp["site"].astype(str))
    folds = {}
    for a in range(3):
        for b in range(3):
            fit_groups = frozenset(g for g in groups if B.oof_search_fold(g) != a)
            fit_sites = frozenset(s for s in sites if B.oof_site_fold(s) != b)
            if leak_site is not None:
                fit_sites = fit_sites | {leak_site}
            folds[(a, b)] = FoldTrust(_ConstTrust(100 * a + 10 * b + 1), a, b, fit_groups, fit_sites, 10, date(2025, 12, 31))
    score_groups = {k: frozenset(g for g in groups if B.oof_search_fold(g) != k) for k in range(3)}
    return B.ModelInputs(main_score=_ConstScore(-5.0), main_trust=_ConstTrust(-1.0),
                         main_excludes_search=lambda kw: kw == "roofer", main_excludes_site=lambda s: True,
                         fold_scores={k: _ConstScore(float(k)) for k in range(3)}, fold_score_groups=score_groups,
                         fold_trust=folds,
                         fold_score_sites=None if strength_sites is None else {k: strength_sites for k in range(3)},
                         main_score_sites=main_strength_sites)


def _stub_strength(monkeypatch):
    from aeo.ranksim.forecast.features import MEASURES, RAW_STRENGTH_KEYS

    def lookup(site_dates, log=None):
        out = site_dates[["site", "as_of"]].drop_duplicates().copy()
        out["as_of"] = [B.as_date(d) for d in out["as_of"]]
        for col in (*RAW_STRENGTH_KEYS, *MEASURES):
            out[col] = np.nan
        return out

    monkeypatch.setattr(B, "strength_lookup", lookup)


def test_training_rows_take_trust_and_strength_out_of_fold_and_test_rows_the_main_models(monkeypatch):
    _stub_strength(monkeypatch)
    sp, pages = _toy_panel()
    cells, _ = B.reading_cells(sp, pages, 28, 130)
    cells[B.INPUT_MODE_COL] = np.where(cells["keyword"] == "roofer", "main", "fold")
    inputs = _oof_inputs(sp, pages, cells)
    out = B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[], inputs=inputs)
    fold = out[out[B.INPUT_MODE_COL] == "fold"]
    main = out[out[B.INPUT_MODE_COL] == "main"]
    assert len(fold) and len(main)
    for _, row in fold.iterrows():
        a, b = B.oof_search_fold(row["search_group"]), B.oof_site_fold(row["site"])
        assert row["trust_pred"] == 100 * a + 10 * b + 1 and row["own_score"] == float(a)
        assert row[B.INPUTS_MODEL_COL] == f"fold-a{a}-b{b}"
    assert (main["trust_pred"] == -1.0).all() and (main["own_score"] == -5.0).all()
    assert B.in_sample_model_inputs(fold)["rows"] == 0 and B.in_sample_model_inputs(main)["rows"] == 0
    # The check reads what each model was fitted on: a fold model that saw a row's own site is caught.
    leaky = _oof_inputs(sp, pages, cells, leak_site="a.com")
    out2 = B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[], inputs=leaky)
    caught = B.in_sample_model_inputs(out2[out2[B.INPUT_MODE_COL] == "fold"])
    assert caught["own_site_in_fit"] == int((out2.loc[out2[B.INPUT_MODE_COL] == "fold", "site"] == "a.com").sum()) > 0


def test_a_page_with_both_held_out_and_training_rows_is_refused(monkeypatch):
    _stub_strength(monkeypatch)
    sp, pages = _toy_panel()
    cells, _ = B.reading_cells(sp, pages, 28, 130)
    cells[B.INPUT_MODE_COL] = ["main"] + ["fold"] * (len(cells) - 1)
    with pytest.raises(B.BacktestRefusal, match="both held-out and training"):
        B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[],
                            inputs=_oof_inputs(sp, pages, cells))


def test_panel_reports_are_stand_ins_so_markers_never_pass_or_fail_on_them():
    cells = _cells(stage="upload")
    train = [GradedCell(site=f"t{i}", search=f"tq{i}", point=NT50, actual=NT50) for i in range(40)]
    args = dict(stage="upload", as_of=date(2026, 5, 1), model_version="m", outcome_dates=[date(2026, 4, 1)] * 120,
                as_ofs=[date(2026, 2, 1)] * 120, band_prior_training=train, held_out=[True] * 120)
    base = {"persistence": [NT50] * 120, "site_strength": [NT50] * 120}
    report = B.backtest_report(cells, base, stand_in_reason=B.STAND_IN_REASON, **args)
    assert report["stand_in"] is True and report["stand_in_reason"] == B.STAND_IN_REASON
    verdict = evaluate_marker(3, report)
    assert verdict["passed"] is None and verdict["stand_in"] is True and "stand_in_passed" in verdict
    d30 = B.backtest_report(_cells(), base, stand_in_reason=B.STAND_IN_REASON, **{**args, "stage": "d30"})
    verdict4 = evaluate_marker(4, d30)
    assert verdict4["passed"] is None and verdict4["stand_in"] is True
    plain = B.backtest_report(cells, base, **args)
    assert "stand_in" not in plain and evaluate_marker(3, plain)["passed"] in (True, False)


def test_backtest_report_is_built_by_the_shared_report_code_and_keeps_the_version_1_keys():
    from pathlib import Path

    from aeo.ranksim.forecast.grading import headline

    v1 = json.loads((Path(__file__).resolve().parents[1] / "evals" / "forecast" / "backtest" / "panel_a_reading_to_outcome"
                     / "report-v1.json").read_text())["windows"]["lead_28_74"]["all"]
    cells = _cells()
    base = {"persistence": [c.point for c in cells], "site_strength": [NT50] * len(cells)}
    train = [GradedCell(site=f"t{i}", search=f"tq{i}", point=NT50, actual=NT50 if i % 2 else 30) for i in range(60)]
    report = B.backtest_report(cells, base, stage="d30", as_of=date(2026, 5, 1), model_version="m",
                               outcome_dates=[date(2026, 4, 1)] * 120, as_ofs=[date(2026, 2, 1)] * 120,
                               band_prior_training=train, held_out=[True] * 120, stand_in_reason=B.STAND_IN_REASON)
    extras = {"by_difficulty_all_forecasters", "by_reading_band", "cell_counts", "counts", "design", "marker_4_on_stand_in",
              "page_one_counts", "point_diagnostics", "prereg_sha256", "prior_model", "train_rows", "updater",
              "updater_versions_on_test", "validation_rows", "window_days"}  # written by the panel run, not the report
    assert set(v1) - extras <= set(report)
    assert report["excluded_not_held_out"] == 0 and report["chain"]["ok"] is None
    assert report["headline"]["summary"]["cluster_by"] == "site"
    same = headline(cells, {**base, "band_prior": B.band_prior_points(report["band_prior"], cells)}, cluster_by="site")
    assert same["summary"]["hit"] == report["headline"]["summary"]["hit"]
    assert same["baselines"]["band_prior"] == report["headline"]["baselines"]["band_prior"]
    assert report["by_lead_days"] and set(report["by_lead_days"]) <= {b[0] for b in B.LEAD_BINS} | {"unknown"}


def test_a_run_that_crashes_part_way_writes_no_report(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import features as F

    monkeypatch.setenv("RANKSIM_FORECAST_DIR", str(tmp_path / "data"))
    forecaster = type("F", (), {"points": lambda self, frame: np.full(len(frame), NT50)})()
    monkeypatch.setattr(B, "_load_models", lambda log, **kw: (_ConstScore(0.0), forecaster, None))
    monkeypatch.setattr(F.StartEvidence, "load", classmethod(lambda cls, sites, root=None, live=False: cls({})))

    def boom(*args, **kwargs):
        raise RuntimeError("crash in test A")

    monkeypatch.setattr(B, "reading_cells", boom)
    sp, pages = _toy_panel()
    specs = B.panel_specs(B.PanelDesign(), gaps={}, cuts=[])
    shas = {key: "sha-" + key for key in specs}
    with pytest.raises(RuntimeError, match="crash in test A"):
        B._run_panel_body(sp, pages, B.PanelDesign(), specs, shas, [], tmp_path / "evals", None, rehearsal=True)
    assert not list((tmp_path / "evals").rglob("report-v*.json")) and not list((tmp_path / "evals").rglob("manifest-v*.json"))


def test_version_2_designs_load_the_version_2_models_and_record_the_new_rules():
    assert B.models_for_design(1) == {"strength_version": 1, "trust_name": "trust-v1-free-lexical-all"}
    assert B.models_for_design(2) == {"strength_version": 2, "trust_name": "trust-v2-free-lexical-all"}
    paths = B.model_paths(**B.models_for_design(2))
    assert paths["strength_score"].name == "strength-v2-score.json" and paths["trust"].name == "trust-v2-free-lexical-all.json"
    v2, _ = _frozen_panel_specs(version=2, reason="second look")
    rules = v2["A"].thresholds["version_2_rules"]
    assert rules["models"] == {"strength": "strength-v2", "trust": "trust-v2-free-lexical-all", "missing_model": "refused"}
    assert rules["out_of_fold_inputs"]["folds"] == B.OOF_FOLDS
    assert "0.820 against 0.867" in rules["new_to_search_rule"]["origin"]
    assert "90" in rules["calibration"]["rule"] and "stand_in" in rules["report_code"]


def test_rehearsal_sides_leave_out_the_real_splits_held_out_sites():
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT, REHEARSAL_SPLIT

    sites = [f"site{i}.com" for i in range(200)]
    test_kw = next(k for k in (f"search {i}" for i in range(400)) if REHEARSAL_SPLIT.search_is_test(k))
    train_kw = next(k for k in (f"search {i}" for i in range(400)) if not REHEARSAL_SPLIT.search_is_test(k))
    cells = pd.DataFrame({"site": sites * 2, "keyword": [test_kw] * 200 + [train_kw] * 200,
                          "test_search": [True] * 200 + [False] * 200, "search_group": ["g1"] * 200 + ["g2"] * 200,
                          "as_of": [date(2025, 11, 1)] * 200 + [date(2025, 6, 1)] * 200,
                          "outcome_date": [date(2025, 12, 20)] * 200 + [date(2025, 8, 1)] * 200})
    B._ACTIVE_SPLIT.append(REHEARSAL_SPLIT)
    try:
        train, val, test = B._sides(cells, B.PanelDesign(validation_share=0.0))
    finally:
        B._ACTIVE_SPLIT.pop()
    used = cells.loc[train | val | test, "site"]
    assert len(used) and not any(DEFAULT_SPLIT.site_is_test(s) for s in used)
    assert test.any() and all(REHEARSAL_SPLIT.site_is_test(s) for s in cells.loc[test, "site"])


def test_the_forecast_core_applies_the_new_site_rule_of_the_updater():
    from aeo.ranksim.forecast.updater import choose_segment, choose_version, fit_table

    rng = np.random.default_rng(3)
    n = 900
    read = rng.integers(1, 51, n).astype(float)
    frame = pd.DataFrame({"x": rng.normal(size=n), "reading_position": read, "lead_days": 60.0,
                          "new_to_search": np.where(np.arange(n) % 2 == 0, 1.0, 0.0), "site": [f"s{i}" for i in range(n)],
                          "search": [f"q{i % 90}" for i in range(n)], "keyword_id": [f"q{i % 90}" for i in range(n)],
                          "search_group": [f"q{i % 90}" for i in range(n)], "as_of": date(2025, 6, 1)})
    frame["position_out"] = np.where(frame["new_to_search"] == 1.0, NT50, read.astype(int))
    placement = fit_placement(frame.assign(position_out=NT50 - 1), ["x"], training_label=REAL)
    prior = placement.distributions(frame)
    from aeo.ranksim.forecast.updater import prior_columns

    work = pd.concat([frame, prior_columns(prior)], axis=1)
    table = fit_table(work.assign(new_to_search=0.0, position_out=read.astype(int)), training_label=REAL)
    updater = choose_version(table, None, work, prior)
    assert choose_segment(updater, work, prior)["winner"] == "always_nt50"
    _, forecasts = B.forecast_frame(frame, B.ForecastModels(placement=placement, updater=updater), stage="d30")
    points = np.array([f.point for f in forecasts])
    assert (points[frame["new_to_search"].to_numpy() == 1.0] == NT50).all()
    assert (points[frame["new_to_search"].to_numpy() == 0.0] != NT50).any()


# ---------------------------------------------------------------------------
# version 2 after the second bug hunt: honest in-sample counts, design files that name what is run, PROVISIONAL


def test_in_sample_check_counts_rows_whose_strength_score_was_fitted_on_their_own_site(monkeypatch):
    """Strength is split by search only, so a training row's strength fold score (and the main score serving test
    rows) can be fitted on the row's own site. The count must show it, apart from trust, which must stay 0."""
    _stub_strength(monkeypatch)
    sp, pages = _toy_panel()
    cells, _ = B.reading_cells(sp, pages, 28, 130)
    cells[B.INPUT_MODE_COL] = np.where(cells["keyword"] == "roofer", "main", "fold")
    every_site = frozenset(sp["site"].astype(str))
    inputs = _oof_inputs(sp, pages, cells, strength_sites=every_site, main_strength_sites=every_site)
    out = B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[], inputs=inputs)
    for side in ("fold", "main"):
        rows = out[out[B.INPUT_MODE_COL] == side]
        got = B.in_sample_model_inputs(rows)
        assert len(rows) > 0
        assert got["strength_own_site_in_fit"] == len(rows) and got["trust_own_site_in_fit"] == 0
        assert got["own_site_in_fit"] == len(rows) and got["rows"] == len(rows)
        assert "split by search only" in got["note"]
    # strength sites not recorded: the rows cannot be certified, so they count, and are shown as unknown
    unknown = _oof_inputs(sp, pages, cells, strength_sites=None, main_strength_sites=None)
    out2 = B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[], inputs=unknown)
    got2 = B.in_sample_model_inputs(out2)
    assert got2["strength_own_site_unknown"] == len(out2) == got2["rows"]


def test_a_training_row_with_no_trust_fold_model_is_labelled_by_its_strength_fold_not_main(monkeypatch):
    _stub_strength(monkeypatch)
    sp, pages = _toy_panel()
    cells, _ = B.reading_cells(sp, pages, 28, 130)
    cells[B.INPUT_MODE_COL] = "fold"
    inputs = _oof_inputs(sp, pages, cells)
    inputs = B.ModelInputs(**{**inputs.__dict__, "fold_trust": {}})
    out = B.add_site_features(cells, sp, pages, score=None, trust_model=None, evidence=None, cuts=[], inputs=inputs)
    for _, row in out.iterrows():
        assert row[B.INPUTS_MODEL_COL] == f"strength-fold-a{B.oof_search_fold(row['search_group'])}"


def test_build_model_inputs_records_the_sites_each_strength_score_was_fitted_on(monkeypatch):
    from aeo.ranksim.forecast import strength as S
    from aeo.ranksim.forecast import trust as T

    _stub_strength(monkeypatch)
    sp, pages = _panel([
        ("plumber", "2025-03-01", 100, {"s0.com": 3, "s1.com": 8}),
        ("painter", "2025-03-02", 100, {"s0.com": 5, "s2.com": 2}),
        ("roofer", "2025-03-03", 100, {"s1.com": 4, "s2.com": 6}),
    ])
    pages["test_search"] = False
    from types import SimpleNamespace

    monkeypatch.setattr(S, "fit_fold_scores", lambda rows, folds, count, **kw: {k: SimpleNamespace(cv={}) for k in range(count)})
    monkeypatch.setattr(T, "training_labels", lambda *a, **kw: (pd.DataFrame({"snapshot_date": []}), {}))
    monkeypatch.setattr(B, "trust_fold_models", lambda labels, meta, **kw: ({}, {}))
    inputs = B.build_model_inputs(sp, pages, score=_ConstScore(0.0), trust_model=None, rehearsal=False)
    rows = B.strength_training_rows(sp, pages)
    folds = np.array([B.oof_search_fold(g) for g in rows["search_group"].astype(str)])
    for k in range(B.OOF_FOLDS):
        assert inputs.fold_score_sites[k] == frozenset(rows.loc[folds != k, "site"].astype(str))
    assert inputs.main_score_sites == frozenset(rows["site"].astype(str))


def test_version_2_designs_name_the_models_version_2_loads_and_a_mismatch_is_refused():
    loaded = B.models_for_design(2)
    v2, _ = _frozen_panel_specs(version=2, reason="out-of-fold inputs")
    for key in ("A", "B", "C"):
        mv = v2[key].model_versions
        assert loaded["trust_name"] in mv["trust"] and "strength-v2" in mv["strength_score"]
        assert "strength-v1" not in json.dumps(mv) and "trust-v1" not in json.dumps(mv)
    assert v2["ceiling"].model_versions == {}
    B.check_models_match_specs(v2, 2)
    v1, _ = _frozen_panel_specs()
    with pytest.raises(B.BacktestRefusal, match="the run loads strength-v2"):
        B.check_models_match_specs(v1, 2)


def test_version_2_designs_say_their_held_out_rows_were_already_read_by_version_1():
    v2, _ = _frozen_panel_specs(version=2, reason="out-of-fold inputs")
    for spec in v2.values():
        record = spec.thresholds["version_2_rules"]["held_out_rows"]
        assert record["label"] == "PROVISIONAL" and record["already_read_by"] == "design version 1"
        assert any(n.startswith("PROVISIONAL: held-out rows already read by version 1") for n in spec.notes)
    rules = v2["A"].thresholds["version_2_rules"]["out_of_fold_inputs"]
    assert "strength_own_site_in_fit is NOT 0" in rules["report"]


def test_a_split_after_the_dates_version_1_graded_is_fresh_and_version_1_is_never_provisional():
    from aeo.ranksim.forecast.features import held_out_already_read
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    assert held_out_already_read(DEFAULT_SPLIT, 1) is None
    assert held_out_already_read(DEFAULT_SPLIT, 2)["label"] == "PROVISIONAL"
    assert held_out_already_read(SplitRule(seed=7), 2) is not None  # another seed still regrades rows v1 read
    assert held_out_already_read(SplitRule(version="later", time_cutoff="2026-05-01"), 2) is None


def test_provisional_reports_pass_or_fail_no_marker_and_say_why():
    from aeo.ranksim.forecast.features import held_out_already_read, mark_provisional
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    record = held_out_already_read(DEFAULT_SPLIT, 2)
    report = {"label": REAL, "windows": {"w": {"all": {"headline": {"x": 1}, "marker_4_on_stand_in": {"passed": True}}}}}
    out = mark_provisional(report, record)
    assert out["provisional"] is True and "already read by version 1" in out["provisional_reason"]
    inner = out["windows"]["w"]["all"]
    assert inner["provisional"] is True
    assert inner["marker_4_on_stand_in"]["passed"] is None and inner["marker_4_on_stand_in"]["provisional_passed"] is True
    assert report["windows"]["w"]["all"]["marker_4_on_stand_in"]["passed"] is True  # the input is not changed
    assert mark_provisional(report, None) is report


def test_range_without_the_new_site_rule_is_each_distributions_own_80pct_interval():
    from aeo.ranksim.forecast.updater import nt50_mass

    dist = np.zeros(51)
    dist[3:9] = 0.95 / 6  # positions 4 to 9
    dist[-1] = 0.05
    block = B._range_block_from_dists(np.array([dist, nt50_mass()]), [12, NT50], 0.8)
    assert block == {"rows": 2, "coverage": 0.5, "mean_width": 3.5}  # [4, 9] misses 12; [51, 51] holds NT50
