"""The live forecasting path (forecast/forecasting.py and forecast/cli_forecasting.py).

No network, no keys, no spending. REAL-labelled bundles and readings here are hand-made test
fixtures built to exercise the REAL code paths (the gate, the refusals); they are not results.
The synthetic bundle is SYNTHETIC and never accuracy.
"""

from __future__ import annotations

import io
import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import cli, forecasting, pipeline
from aeo.ranksim.forecast.asof import AsOfGuard, Dated, LeakError
from aeo.ranksim.forecast.config import NT50, forecast_paths
from aeo.ranksim.forecast.features import MEASURES, RAW_STRENGTH_KEYS
from aeo.ranksim.forecast.reader import ReplaySource
from aeo.ranksim.forecast.scoreboard import Scoreboard
from aeo.ranksim.forecast.types import REAL, SYNTHETIC, Forecast, OrganicHit, Pull, PullSpec

LAUNCH = date(2026, 12, 1)
LOCATION = 1026201
SITE = "seedone.example"
SEARCHES = ("plumber prices", "emergency plumber", "drain repair")
RIVALS = [f"rival{i:02d}.com" for i in range(1, 41)]
DASHES = ("—", "–")


@pytest.fixture(autouse=True)
def _no_committed_export(monkeypatch, tmp_path):
    monkeypatch.setenv("RANKSIM_EXPORT_DIR", str(tmp_path / "committed_export"))
    monkeypatch.delenv("RANKSIM_SCOREBOARD_URL", raising=False)
    monkeypatch.delenv("RANKSIM_FORECAST_DIR", raising=False)


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)


def fake_strength(site_dates: pd.DataFrame) -> pd.DataFrame:
    """Hand-made dated strength, every value through an AsOfGuard like the real lists."""
    out = site_dates[["site", "as_of"]].copy().reset_index(drop=True)
    for col in (*RAW_STRENGTH_KEYS, *MEASURES):
        out[col] = np.nan
    for i, (site, day) in enumerate(zip(out["site"], out["as_of"])):
        guard = AsOfGuard(day)
        value = guard.take(Dated(float(-1.0 - (sum(map(ord, site)) % 50) / 10.0), "fixture", day - timedelta(days=5)))
        for m in ("s_cc_hc", "s_cc_pr", "s_cc_hosts"):
            out.loc[i, m] = value
    return out


def fixture_rows(n: int = 60, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = pd.DataFrame({
        "site": [f"train{i % 15:02d}.com" for i in range(n)],
        "search": [f"search {i % 12}" for i in range(n)],
        "keyword_id": [f"k{i % 12}" for i in range(n)],
        "search_group": [f"g{i % 12}" for i in range(n)],
        "difficulty": [("easy", "medium", "hard")[i % 3] for i in range(n)],
        "position_out": rng.choice([3, 8, 15, 30, NT50, NT50], size=n),
        "st_s_cc_hc": rng.normal(-3, 1, n), "st_s_cc_pr": rng.normal(-3, 1, n),
        "cl_s_cc_hc_q_top10": rng.random(n), "pg_sites_top10": rng.integers(5, 10, n).astype(float),
    })
    rows["reading_position"] = np.where(rng.random(n) < 0.5, rows["position_out"], NT50).astype(float)
    rows["lead_days"] = 60.0
    rows["new_to_search"] = np.nan
    return rows


@pytest.fixture(scope="module")
def real_bundle_paths(tmp_path_factory):
    paths = forecast_paths(tmp_path_factory.mktemp("bundles"))
    rows = fixture_rows()
    forecasting.fit_bundle(paths, "cond-real", rows, rows, label=REAL, source="panel", includes_never_ranked=False,
                           training_cutoff=date(2025, 12, 31), group_col="search_group", trust=None,
                           difficulty_metric="kd", split_note="test fixture")
    forecasting.fit_bundle(paths, "syn-fixture", rows, None, label=SYNTHETIC, source="synthetic",
                           includes_never_ranked=True, training_cutoff=date(2025, 12, 31), group_col="search_group",
                           trust=None, difficulty_metric="kd", split_note="test fixture")
    forecasting.fit_bundle(paths, "late-real", rows, None, label=REAL, source="panel", includes_never_ranked=False,
                           training_cutoff=date(2027, 6, 1), group_col="search_group", trust=None,
                           difficulty_metric="kd", split_note="test fixture")
    return paths


def _pulls(spec: PullSpec, moment: datetime, sites: list[str], *, ok: bool = True) -> list[Pull]:
    out = []
    for index in range(5):
        at = moment + timedelta(minutes=2 * index)
        stamp = at.strftime("%Y-%m-%dT%H:%M:%SZ")
        hits = tuple(OrganicHit(rank=r, url=f"https://{s}/p", domain=s, site=s) for r, s in enumerate(sites, start=1))
        out.append(Pull(pull_id=f"p-{spec.search.replace(' ', '')}-{stamp}", provider="replay", endpoint="replay",
                        spec=spec, requested_at=stamp, completed_at=stamp, ok=ok, status_code=20000 if ok else 50000,
                        hits=hits if ok else ()))
    return out


def seed_board(tmp_path: Path, clock: pipeline.VirtualClock) -> Scoreboard:
    board = Scoreboard(f"sqlite:///{tmp_path / 'board.db'}", clock=clock.now)
    board.add_site(SITE, source="seed", launch_date=LAUNCH, launch_date_source="test", built_by="test", niche="plumber")
    for text_ in SEARCHES:
        board.add_search(SITE, text_, location_code=LOCATION, difficulty="easy")
    return board


def read(board: Scoreboard, clock: pipeline.VirtualClock, moment: datetime, search: str, sites: list[str], *, ok: bool = True):
    clock.set(moment)
    spec = PullSpec(search, LOCATION)
    summary = pipeline.take_readings(board, ReplaySource(_pulls(spec, moment, sites, ok=ok)), [spec], clock=clock)
    clock.set(moment + timedelta(minutes=30))  # the pulls finish within 10 minutes
    return summary


# ---------------------------------------------------------------------------------------------
# The appearance gate


def test_the_gate_prior_is_the_arithmetic_written_in_the_docstring():
    by_day_90 = 0.4082 + 0.5918 * 2 / 11
    assert round(0.0611 * by_day_90 * 5, 2) == forecasting.GATE_PRIOR
    assert "OUR READING" in forecasting.appearance_gate.__doc__ and "not fitted" in forecasting.GATE_PRIOR_SOURCE


def test_apply_gate_puts_the_rest_of_the_mass_on_not_in_top_50_and_keeps_the_shape():
    dist = np.zeros(51)
    dist[2], dist[9], dist[50] = 0.5, 0.25, 0.25
    gated = forecasting.apply_gate(dist, 0.16)[0]
    assert math.isclose(gated.sum(), 1.0)
    assert math.isclose(gated[50], 0.84)
    assert math.isclose(gated[2] / gated[9], 2.0)
    with pytest.raises(forecasting.ForecastingRefusal):
        forecasting.apply_gate(dist, 1.5)


def test_a_conditional_bundle_can_never_forecast_without_the_gate(real_bundle_paths):
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    assert bundle.conditional
    with pytest.raises(forecasting.ForecastingRefusal, match="needs the appearance gate"):
        forecasting.live_models(bundle, forecasting.NO_GATE)


def test_the_gate_uses_graded_outcomes_only_when_there_are_enough_and_never_later_ones(tmp_path):
    clock = pipeline.VirtualClock(_at(date(2026, 1, 5), 9))
    board = Scoreboard(f"sqlite:///{tmp_path / 'g.db'}", clock=clock.now)
    launch = date(2026, 1, 6)
    spec = PullSpec("plumber prices", LOCATION)
    sites = {"inone.example": "seed", "intwo.example": "pilot", "synth.example": "synthetic"}
    for site, source in sites.items():
        board.add_site(site, source=source, launch_date=launch, launch_date_source="t", built_by="t", niche="plumber")
        board.add_search(site, spec.search, location_code=LOCATION, difficulty="easy")
    board.register_model_version("m1", description="t", frozen_at=clock.now())
    for site in sites:
        board.record_forecast(Forecast(site=site, search=spec.search, stage="upload", as_of=clock.now().date(), point=NT50,
                                       range_low=NT50, range_high=NT50, label="out_of_reach", model_version="m1"))
    day90 = _at(launch + timedelta(days=90), 12)
    read(board, clock, day90, spec.search, ["inone.example", "synth.example"] + RIVALS)
    clock.set(day90 + timedelta(days=1))
    pipeline.grade(board, (day90 + timedelta(days=1)).date())
    before = forecasting.appearance_gate(board, label=REAL, as_of=launch + timedelta(days=30), min_cells=2, min_sites=2)
    assert before.source == "prior" and before.cells == 0  # the outcome was not known yet
    after = forecasting.appearance_gate(board, label=REAL, as_of=day90.date(), min_cells=2, min_sites=2)
    assert after.source == "scoreboard" and after.cells == 2 and after.value == 0.5  # seed and pilot only
    synthetic = forecasting.appearance_gate(board, label=SYNTHETIC, as_of=day90.date(), min_cells=1, min_sites=1)
    assert synthetic.source == "prior" and synthetic.cells == 0  # REAL outcomes never feed a synthetic gate
    strict = forecasting.appearance_gate(board, label=REAL, as_of=day90.date())
    assert strict.source == "prior" and strict.value == forecasting.GATE_PRIOR
    board.close()


# ---------------------------------------------------------------------------------------------
# Bundles on disk


def test_a_bundle_whose_manifest_or_file_changed_is_refused(real_bundle_paths, tmp_path):
    import shutil

    paths = forecast_paths(tmp_path / "copy")
    shutil.copytree(real_bundle_paths.models / "cond-real", paths.models / "cond-real")
    manifest_path = paths.models / "cond-real" / forecasting.BUNDLE_MANIFEST
    data = json.loads(manifest_path.read_text())
    assert data["includes_never_ranked"] is False and data["training_label"] == REAL
    data["includes_never_ranked"] = True  # pretend the panel had never-ranked cells: skips the gate
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(forecasting.ForecastingRefusal, match="does not match its own sha256"):
        forecasting.load_bundle(paths, "cond-real")
    shutil.rmtree(paths.models / "cond-real")
    shutil.copytree(real_bundle_paths.models / "cond-real", paths.models / "cond-real")
    (paths.models / "cond-real" / "placement.json").write_text("{}")
    with pytest.raises(forecasting.ForecastingRefusal, match="does not match the sha256"):
        forecasting.load_bundle(paths, "cond-real")


def test_a_bundle_is_never_replaced_and_reserved_names_are_refused(real_bundle_paths):
    rows = fixture_rows()
    with pytest.raises(forecasting.ForecastingRefusal, match="never replaced"):
        forecasting.fit_bundle(real_bundle_paths, "cond-real", rows, None, label=REAL, source="panel",
                               includes_never_ranked=False, training_cutoff=date(2025, 1, 1), group_col="search_group",
                               trust=None, difficulty_metric=None, split_note="t")
    with pytest.raises(forecasting.ForecastingRefusal, match="another part"):
        forecasting.check_bundle_name("trust")


def test_panel_training_rows_never_include_a_held_out_row():
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    keywords = [f"kw {i}" for i in range(60)]
    sites = [f"s{i}.com" for i in range(60)]
    frame = pd.DataFrame({"keyword": keywords, "site": sites, "outcome_date": [date(2025, 6, 1)] * 30 + [date(2026, 2, 1)] * 30,
                          "depth_ok": True, "label": REAL})
    rows = forecasting.panel_training_rows(frame)
    assert len(rows) > 0
    for kw, site, day in zip(rows["keyword"], rows["site"], rows["outcome_date"]):
        assert not DEFAULT_SPLIT.search_is_test(kw) and not DEFAULT_SPLIT.site_is_test(site) and day < DEFAULT_SPLIT.cutoff


def test_panel_training_writes_a_conditional_real_bundle_from_training_rows_only(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import trust
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    monkeypatch.setattr(trust, "default_models_dir", lambda: tmp_path / "no-trust")
    monkeypatch.setattr(forecasting, "newest_trust_report", lambda root=None: None)
    rng = np.random.default_rng(4)
    keywords = [k for k in (f"plumbing kw {i}" for i in range(400)) if not DEFAULT_SPLIT.search_is_test(k)][:30]
    sites = [s for s in (f"site{i}.com" for i in range(400)) if not DEFAULT_SPLIT.site_is_test(s)][:40]
    held_kw = next(k for k in (f"plumbing kw {i}" for i in range(400)) if DEFAULT_SPLIT.search_is_test(k))
    rows = []
    for i in range(240):
        rows.append({"keyword": keywords[i % 30], "keyword_id": f"k{i % 30}", "search_group": f"g{i % 30}",
                     "site": sites[i % 40], "outcome_date": date(2025, 9, 1), "depth_ok": True, "label": REAL,
                     "rank100_in": np.nan if i % 2 else float(1 + i % 40), "depth_input": 100, "gap_days": 60.0,
                     "position_out": int(rng.choice([4, 12, 30, NT50])), "st_s_cc_hc": float(rng.normal(-3, 1)),
                     "cl_s_cc_hc_q_top10": float(rng.random())})
    rows.append({**rows[0], "keyword": held_kw, "position_out": 1})  # a held-out search: never read
    rows.append({**rows[1], "outcome_date": date(2026, 2, 1), "position_out": 1})  # after the cutoff: never read
    path = tmp_path / "panel_frame_test.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    result = forecasting.train(forecast_paths(tmp_path / "data"), data="panel", name="p1", frame_path=path)
    assert result.refusal is None and result.label == REAL
    manifest = result.bundle.manifest
    assert manifest["includes_never_ranked"] is False and result.bundle.conditional
    assert manifest["counts"]["upload_rows"] == 120  # only cells absent from the input page: the entrant analog
    assert manifest["counts"]["updater"]["rows"] == 240
    assert date.fromisoformat(manifest["training_cutoff"]) < DEFAULT_SPLIT.cutoff
    assert any("CONDITIONAL" in note for note in result.notes) and any("trust_pred is missing" in n for n in result.notes)


def test_paid_training_names_the_pull_commands_when_its_files_are_missing(tmp_path):
    result = forecasting.train(forecast_paths(tmp_path), data="paid", name="paid1")
    assert result.bundle is None and "pull past-pages" in result.refusal and "pull traffic" in result.refusal
    assert not (tmp_path / "models" / "paid1").exists()


# ---------------------------------------------------------------------------------------------
# The live path


def test_an_upload_forecast_for_a_real_site_goes_through_the_gate_and_skips_cells_without_a_reading(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    good = read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    read(board, clock, _at(date(2026, 11, 29), 11), SEARCHES[2], RIVALS, ok=False)  # degraded: never used
    clock.set(_at(date(2026, 11, 30), 10))
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    run = forecasting.plan_forecasts(board, real_bundle_paths, bundle, site=SITE, stage="upload", day=None, now=clock.now(),
                                     strength=fake_strength, evidence=None)
    assert run.as_of == date(2026, 11, 30) and run.label == REAL and run.gate["source"] == "prior"
    assert [f.search for f in run.forecasts] == [SEARCHES[0]]
    assert sorted(item["search"] for item in run.skipped) == sorted([SEARCHES[1], SEARCHES[2]])
    forecast = run.forecasts[0]
    assert forecast.point == NT50 and forecast.label == "out_of_reach"  # 0.84 on NOT IN TOP 50 at least
    assert "gate-prior-0.1600" in forecast.model_version and forecast.notes["gate"]["source"] == "prior"
    assert forecast.notes["baselines"]["site_strength"] == NT50
    assert run.inputs[SEARCHES[0]] == [good.reading_ids[0]]
    lines = forecasting.run_lines(run, record=False)
    assert any("honest answer until the paid training data exists" in line for line in lines)
    assert any("Dry run" in line for line in lines) and not any(d in " ".join(lines) for d in DASHES)
    assert pipeline.scoreboard_counts(board)["forecasts"] == 0
    board.close()


def test_a_reading_taken_after_the_forecast_date_is_never_the_page(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    first = read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    read(board, clock, _at(date(2026, 11, 30), 9), SEARCHES[0], [SITE] + RIVALS)
    clock.set(_at(date(2026, 11, 30), 12))
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    run = forecasting.plan_forecasts(board, real_bundle_paths, bundle, site=SITE, stage="upload", day=date(2026, 11, 29),
                                     now=clock.now(), strength=fake_strength)
    assert run.inputs[SEARCHES[0]] == [first.reading_ids[0]]
    board.close()


def test_a_synthetic_bundle_never_forecasts_a_seed_site_and_a_late_bundle_is_a_leak(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    synthetic = forecasting.load_bundle(real_bundle_paths, "syn-fixture")
    with pytest.raises(forecasting.ForecastingRefusal, match="SYNTHETIC rows"):
        forecasting.plan_forecasts(board, real_bundle_paths, synthetic, site=SITE, stage="upload", day=None,
                                   now=clock.now(), strength=fake_strength)
    late = forecasting.load_bundle(real_bundle_paths, "late-real")
    with pytest.raises(LeakError, match="learned outcomes up to 2027-06-01"):
        forecasting.plan_forecasts(board, real_bundle_paths, late, site=SITE, stage="upload", day=None, now=clock.now(),
                                   strength=fake_strength)
    board.close()


def test_a_strength_value_published_after_the_date_raises_a_leak(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)

    def leaky(site_dates: pd.DataFrame) -> pd.DataFrame:
        day = site_dates["as_of"].iloc[0]
        AsOfGuard(day).take(Dated(1.0, "fixture", day + timedelta(days=3)))
        return fake_strength(site_dates)

    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    with pytest.raises(LeakError):
        forecasting.plan_forecasts(board, real_bundle_paths, bundle, site=SITE, stage="upload", day=None, now=clock.now(),
                                   strength=leaky)
    board.close()


def test_record_freezes_once_and_a_second_run_never_replaces(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    run = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="upload", day=None,
                                       now=clock.now(), record=True, strength=fake_strength)
    assert len(run.recorded) == 1 and run.refused == []
    stored = board.forecasts()[0]
    assert stored.model_version == run.forecasts[0].model_version and stored.input_reading_ids == tuple(run.inputs[SEARCHES[0]])
    again = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="upload", day=None,
                                         now=clock.now(), record=True, strength=fake_strength)
    assert again.recorded == [] and [item["search"] for item in again.already] == [SEARCHES[0]]
    assert pipeline.scoreboard_counts(board)["forecasts"] == 1
    board.close()


def test_a_date_the_scoreboard_would_refuse_blocks_the_run_and_a_passed_stage_is_missed(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    clock.set(_at(date(2026, 11, 29), 9))
    old = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="upload",
                                       day=date(2026, 11, 26), now=clock.now(), record=True, strength=fake_strength)
    assert "more than 1 day" in old.blocked
    clock.set(_at(LAUNCH + timedelta(days=1), 9))
    run = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="upload",
                                       day=LAUNCH + timedelta(days=1), now=clock.now(), record=True, strength=fake_strength)
    assert "on or before launch" in run.blocked and run.recorded == []
    grace = forecasting.plan_forecasts(board, real_bundle_paths, bundle, site=SITE, stage="upload", day=None,
                                       now=clock.now(), strength=fake_strength)
    assert grace.missed is None and grace.blocked is None and grace.as_of == LAUNCH  # one day of grace, dated launch
    clock.set(_at(LAUNCH + timedelta(days=9), 9))
    missed = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="d7", day=None,
                                          now=clock.now(), record=True, strength=fake_strength)
    assert missed.missed.startswith("MISSED") and "never backfilled" in missed.missed
    assert pipeline.scoreboard_counts(board)["forecasts"] == 0
    board.close()


def test_a_stage_update_uses_the_two_latest_readings_and_stores_the_strength_baseline(tmp_path, real_bundle_paths):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    first = read(board, clock, _at(LAUNCH, 9), SEARCHES[0], RIVALS)
    second = read(board, clock, _at(LAUNCH + timedelta(days=7), 9), SEARCHES[0], RIVALS[:4] + [SITE] + RIVALS[4:])
    clock.set(_at(LAUNCH + timedelta(days=7), 12))
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    run = forecasting.upload_or_update(board, real_bundle_paths, bundle=bundle, site=SITE, stage="d7", day=None,
                                       now=clock.now(), record=True, strength=fake_strength)
    assert len(run.recorded) == 1
    assert run.inputs[SEARCHES[0]] == sorted([first.reading_ids[0], second.reading_ids[0]])
    forecast = run.forecasts[0]
    assert forecast.stage == "d7" and forecast.model_version.startswith(bundle.version)
    assert "gate-prior" in forecast.model_version and "updater-table-v1" in forecast.model_version
    assert forecast.notes["search_console"].startswith("no Search Console export")
    assert isinstance(forecast.notes["baselines"]["site_strength"], int)
    board.close()


# ---------------------------------------------------------------------------------------------
# Marker 2 and the backtest files


def _trust_report(folder: Path, *, name: str = "report.json", rehearsal: bool = False) -> Path:
    pairs = {"count": 400, "rate": 0.74, "right": 296, "wrong": 104, "tied": 0, "two_way_interval": [0.70, 0.78]}
    entry = {"pairs": pairs, "best_single_measure": "st_s_cc_pr",
             "gain_over_link_count": {"difference": 0.06, "difference_interval": [0.02, 0.10]},
             "gain_over_best_single_measure": {"difference": 0.01, "difference_interval": [-0.02, 0.04]}}
    report = {"label": REAL, "primary": "lex", "version": "trust-v1", "rehearsal": rehearsal,
              "runs": {"lex": {"evaluation": {"models": {"free_gbt": entry}}}}}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(report))
    return path


def test_marker_2_on_the_free_panel_is_a_stand_in_that_never_passes_and_prints_both_gains(tmp_path):
    path = _trust_report(tmp_path / "trust")
    result = forecasting.marker2_from_trust_report(path)
    verdict = result["verdict"]
    assert verdict["passed"] is None and verdict["stand_in"] is True and verdict["stand_in_passed"] is True
    assert verdict["gain_over_link_count"]["difference"] == 0.06
    assert verdict["gain_over_best_single_measure"]["difference"] == 0.01
    assert forecasting.newest_trust_report(tmp_path / "trust") == path
    newer = _trust_report(tmp_path / "trust", name="report-v2.json")
    assert forecasting.newest_trust_report(tmp_path / "trust") == newer
    with pytest.raises(forecasting.ForecastingRefusal, match="rehearsal"):
        forecasting.marker2_from_trust_report(_trust_report(tmp_path / "r", rehearsal=True))


def _design(root: Path, name: str, *, preregs: tuple[str, ...], manifests: tuple[int, ...]) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    for prereg in preregs:
        (folder / prereg).write_text("{}")
    for version in manifests:
        (folder / f"manifest-v{version}.json").write_text(json.dumps({"design_version": version}))
        (folder / f"report-v{version}.json").write_text(json.dumps({"label": REAL, "summary": {"hit": 0.5, "cells": 10}}))


def test_backtest_listing_scores_nothing_and_the_rerun_version_is_the_next_or_the_frozen_unscored_one(tmp_path):
    _design(tmp_path, "panel_c_entrant_placement", preregs=("prereg.json",), manifests=(1,))
    items = forecasting.list_backtests(tmp_path)
    assert items[0]["stand_in"] and items[0]["runs"][0]["label"] == REAL
    assert "hit 50.0% on 10 cells" in items[0]["runs"][0]["headline"]
    assert forecasting.backtest_rerun_version(tmp_path) == (2, False)
    (tmp_path / "panel_c_entrant_placement" / "prereg-v2.json").write_text("{}")
    assert forecasting.backtest_rerun_version(tmp_path) == (2, True)


def test_due_lists_stages_due_and_missed_with_the_command_to_run(tmp_path):
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    result = forecasting.due(board, date(2026, 11, 29))
    assert [(i["stage"], i["stage_date"]) for i in result["due"]] == [("upload", "2026-12-01")]
    grace = forecasting.due(board, date(2026, 12, 2))  # the day after launch: last day to write a forecast dated launch
    assert [(i["stage"], i["last_day_to_record"]) for i in grace["due"]][0] == ("upload", "2026-12-02")
    assert grace["missed"] == []
    later = forecasting.due(board, date(2026, 12, 12))
    assert [i["stage"] for i in later["missed"]] == ["upload", "d7"]
    lines = forecasting.due_lines(later)
    assert any("never backfilled" in line for line in lines)
    assert any("forecast update --stage d14 --site seedone.example --record" in line for line in lines)
    board.close()


# ---------------------------------------------------------------------------------------------
# The commands


def run_cli(argv: list[str], data: Path, *, clock: pipeline.VirtualClock | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv + ["--dir", str(data)], clock=clock or pipeline.VirtualClock(_at(date(2026, 11, 22), 9)),
                    stdout=out, stderr=err, settings_factory=lambda: None)
    return code, out.getvalue(), err.getvalue()


def test_upload_command_is_a_dry_run_until_record_and_says_why_every_point_is_not_in_top_50(tmp_path, real_bundle_paths, monkeypatch):
    import shutil

    data = tmp_path / "data"
    shutil.copytree(real_bundle_paths.models / "cond-real", data / "models" / "cond-real")
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    board.add_site(SITE, source="seed", launch_date=LAUNCH, launch_date_source="test", built_by="test", niche="plumber")
    board.add_search(SITE, SEARCHES[0], location_code=LOCATION, difficulty="easy")
    read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    board.close()
    monkeypatch.setattr(forecasting, "real_strength_fn", lambda paths: fake_strength)
    code, out, _ = run_cli(["upload", "--site", SITE, "--bundle", "cond-real"], data, clock=clock)
    assert code == 0 and "Dry run" in out and "NOT IN TOP 50" in out and "honest answer" in out
    code, out, _ = run_cli(["upload", "--site", SITE, "--bundle", "cond-real", "--record", "--json"], data, clock=clock)
    payload = json.loads(out)
    assert code == 0 and len(payload["runs"][0]["recorded"]) == 1 and payload["training_label"] == REAL
    code, out, _ = run_cli(["upload", "--site", SITE, "--record"], data, clock=clock)
    assert code == 0 and "Already recorded" in out


def test_marker_2_command_reads_the_newest_trust_report_and_says_it_is_a_stand_in(tmp_path, monkeypatch):
    path = _trust_report(tmp_path / "trust")
    monkeypatch.setattr(forecasting, "newest_trust_report", lambda root=None: path)
    code, out, _ = run_cli(["marker", "--number", "2"], tmp_path / "data")
    assert code == 1  # a stand-in never passes
    assert "NOT A MARKER RESULT (STAND-IN)" in out and "Gain over link count alone" in out
    assert "Gain over the best single measure" in out and "stand-in for the plan's 20,000 new sites" in out
    code, _, err = run_cli(["marker", "--number", "3"], tmp_path / "data")
    assert code == 2 and "--source" in err


def test_backtest_run_warns_first_and_only_freezes_the_next_design(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import backtest

    root = tmp_path / "evals"
    _design(root, "panel_a_reading_to_outcome", preregs=("prereg.json",), manifests=(1,))
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(backtest, "prereg_panel", lambda **kw: calls.append(("prereg", kw["version"])) or {"A": "sha"})
    monkeypatch.setattr(backtest, "run_panel", lambda **kw: calls.append(("run", kw["version"])) or {})
    code, out, err = run_cli(["backtest", "run", "--reason", "a second look after the fix", "--root", str(root)], tmp_path / "d")
    assert err.startswith(forecasting.RERUN_WARNING) and calls == [("prereg", 2)] and code == 1
    assert "Nothing was scored" in out
    (root / "panel_a_reading_to_outcome" / "prereg-v2.json").write_text("{}")
    code, out, err = run_cli(["backtest", "run", "--reason", "a second look after the fix", "--root", str(root)], tmp_path / "d")
    assert calls[-1] == ("run", 2) and code == 0
    code, out, _ = run_cli(["backtest", "--root", str(root)], tmp_path / "d")
    assert code == 0 and "Nothing was scored" in out


def test_train_command_refuses_paid_without_files_and_due_reports_missed(tmp_path):
    code, out, _ = run_cli(["train", "--data", "paid"], tmp_path / "data")
    assert code == 1 and "pull positions" in out
    code, out, _ = run_cli(["due", "--json"], tmp_path / "data")
    assert code == 0 and json.loads(out)["due"] == []


# ---------------------------------------------------------------------------------------------
# Stage forecasts: the gate, the stage day, the updater and the frozen Search Console rows


def _panel_like_frame(path: Path) -> Path:
    """build_cells shape: a cell exists only when the site is in the top 100 of the input or the outcome page.

    So a row whose site is absent from the input page has a site that was on the outcome page: the
    updater it trains learns that absence goes with a top 50 outcome (hand-made fixture, not a result).
    """
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    rng = np.random.default_rng(7)
    keywords = [k for k in (f"plumbing kw {i}" for i in range(600)) if not DEFAULT_SPLIT.search_is_test(k)][:40]
    sites = [s for s in (f"site{i}.com" for i in range(600)) if not DEFAULT_SPLIT.site_is_test(s)][:60]
    rows = []
    for i in range(600):
        if i % 2:
            rank_in, out = np.nan, int(rng.choice([5, 12, 25, 40, NT50], p=[0.2, 0.2, 0.25, 0.25, 0.1]))
        else:
            rank_in = float(rng.integers(1, 51))
            out = int(rng.choice([int(rank_in), NT50], p=[0.7, 0.3]))
        rows.append({"keyword": keywords[i % 40], "keyword_id": f"k{i % 40}", "search_group": f"g{i % 40}",
                     "site": sites[i % 60], "outcome_date": date(2025, 9, 1), "depth_ok": True, "label": REAL,
                     "rank100_in": rank_in, "depth_input": 100, "gap_days": 60.0, "position_out": out,
                     "st_s_cc_hc": float(rng.normal(-3, 1)), "st_s_cc_pr": float(rng.normal(-3, 1)),
                     "cl_s_cc_hc_q_top10": float(rng.random())})
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _spy_distributions(monkeypatch) -> list[tuple[str, np.ndarray]]:
    from aeo.ranksim.forecast import backtest

    seen: list[tuple[str, np.ndarray]] = []
    real = backtest.forecast_frame

    def spy(frame, models, *, stage):
        dists, out = real(frame, models, stage=stage)
        seen.append((stage, dists))
        return dists, out

    monkeypatch.setattr(backtest, "forecast_frame", spy)
    return seen


def test_a_stage_forecast_of_a_site_that_has_not_appeared_stays_inside_the_gate(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import trust

    monkeypatch.setattr(trust, "default_models_dir", lambda: tmp_path / "no-trust")
    monkeypatch.setattr(forecasting, "newest_trust_report", lambda root=None: None)
    seen = _spy_distributions(monkeypatch)
    paths = forecast_paths(tmp_path / "data")
    bundle = forecasting.train(paths, data="panel", name="p1", frame_path=_panel_like_frame(tmp_path / "f.parquet")).bundle
    assert bundle.conditional
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    read(board, clock, _at(LAUNCH, 9), SEARCHES[0], RIVALS)  # absent on launch day
    read(board, clock, _at(LAUNCH + timedelta(days=7), 9), SEARCHES[0], RIVALS)  # still absent on day 7
    clock.set(_at(LAUNCH + timedelta(days=7), 12))
    d7 = forecasting.plan_forecasts(board, paths, bundle, site=SITE, stage="d7", day=None, now=clock.now(),
                                    strength=fake_strength)
    assert float(seen[-1][1][0][:forecasting.TOP_DEPTH].sum()) <= forecasting.GATE_PRIOR + 1e-9
    forecast = d7.forecasts[0]
    assert "stage-cap-gate-prior-0.1600" in forecast.model_version
    assert forecast.notes["stage_gate_cap"]["applied"] is True
    # Once a real reading shows the site inside the top 50, the cap is lifted and that reading is an input.
    shown = read(board, clock, _at(LAUNCH + timedelta(days=14), 9), SEARCHES[0], RIVALS[:2] + [SITE] + RIVALS[2:])
    clock.set(_at(LAUNCH + timedelta(days=14), 12))
    d14 = forecasting.plan_forecasts(board, paths, bundle, site=SITE, stage="d14", day=None, now=clock.now(),
                                     strength=fake_strength)
    assert d14.forecasts[0].notes["stage_gate_cap"]["applied"] is False
    assert shown.reading_ids[0] in d14.inputs[SEARCHES[0]]
    board.close()


def test_the_gated_updater_caps_only_cells_where_the_site_has_not_appeared():
    gate = forecasting.GateSpec("prior", 0.16)
    dists = np.zeros((2, NT50))
    dists[:, 2] = 0.6
    dists[:, NT50 - 1] = 0.4

    class Fixed:
        in_use = "table"

        def distributions(self, frame, prior, which=None):
            return dists.copy()

    gated = forecasting.GatedUpdater(Fixed(), gate, {"seen": True})
    out = gated.distributions(pd.DataFrame({"search": ["seen", "unseen"]}), dists)
    assert out[0, :forecasting.TOP_DEPTH].sum() == pytest.approx(0.6)
    assert out[1, :forecasting.TOP_DEPTH].sum() == pytest.approx(0.16) and out[1].sum() == pytest.approx(1.0)
    low = forecasting.cap_at_gate(np.r_[np.full(10, 0.01), np.zeros(40), [0.9]], 0.16)  # below the gate: unchanged
    assert low[0, :forecasting.TOP_DEPTH].sum() == pytest.approx(0.10)


def test_a_stage_forecast_needs_a_bundle_with_an_updater(tmp_path):
    paths = forecast_paths(tmp_path / "bundles")
    bundle = forecasting.fit_bundle(paths, "no-updater", fixture_rows(), None, label=REAL, source="panel",
                                    includes_never_ranked=False, training_cutoff=date(2025, 12, 31),
                                    group_col="search_group", trust=None, difficulty_metric="kd", split_note="fixture")
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    read(board, clock, _at(LAUNCH + timedelta(days=7), 9), SEARCHES[0], RIVALS[:2] + [SITE] + RIVALS[2:])
    clock.set(_at(LAUNCH + timedelta(days=7), 12))
    with pytest.raises(forecasting.ForecastingRefusal, match="no early-reading updater"):
        forecasting.upload_or_update(board, paths, bundle=bundle, site=SITE, stage="d7", day=None, now=clock.now(),
                                     record=True, strength=fake_strength)
    assert board.forecasts() == []
    board.close()


def _cli_data(tmp_path: Path, real_bundle_paths, clock, launches: dict[str, date]) -> Path:
    import shutil

    data = tmp_path / "data"
    shutil.copytree(real_bundle_paths.models / "cond-real", data / "models" / "cond-real")
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    for index, (site, launch) in enumerate(launches.items()):
        board.add_site(site, source="seed", launch_date=launch, launch_date_source="test", built_by="test", niche="plumber")
        board.add_search(site, SEARCHES[index], location_code=LOCATION, difficulty="easy")
    board.close()
    return data


def test_all_due_records_a_stage_forecast_only_on_its_stage_day(tmp_path, real_bundle_paths, monkeypatch):
    monkeypatch.setattr(forecasting, "real_strength_fn", lambda paths: fake_strength)
    other = "seedtwo.example"
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    data = _cli_data(tmp_path, real_bundle_paths, clock, {SITE: LAUNCH, other: LAUNCH + timedelta(days=3)})
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    day7 = LAUNCH + timedelta(days=7)
    read(board, clock, _at(day7, 9), SEARCHES[0], RIVALS)
    read(board, clock, _at(day7, 10), SEARCHES[1], RIVALS)
    board.close()
    clock.set(_at(day7, 12))
    code, out, _ = run_cli(["update", "--all-due", "--stage", "d7", "--record", "--json"], data, clock=clock)
    payload = json.loads(out)
    assert code == 0
    assert [(run["site"], run["as_of"], len(run["recorded"])) for run in payload["runs"]] == [(SITE, day7.isoformat(), 1)]
    assert [item["site"] for item in payload["not_yet_due"]] == [other]
    # The other site's d7 is made on its own day 7, from that day's reading, and is not blocked by an early one.
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    its_day7 = read(board, clock, _at(day7 + timedelta(days=3), 9), SEARCHES[1], RIVALS[:3] + [other] + RIVALS[3:])
    board.close()
    clock.set(_at(day7 + timedelta(days=3), 12))
    code, out, _ = run_cli(["update", "--all-due", "--stage", "d7", "--record"], data, clock=clock)
    assert code == 0
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    stored = {f.site: f for f in board.forecasts() if f.stage == "d7"}
    board.close()
    assert stored[other].as_of == day7 + timedelta(days=3)
    assert its_day7.reading_ids[0] in stored[other].input_reading_ids


def test_all_due_reports_a_missed_stage_and_exits_1(tmp_path, real_bundle_paths, monkeypatch):
    monkeypatch.setattr(forecasting, "real_strength_fn", lambda paths: fake_strength)
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    data = _cli_data(tmp_path, real_bundle_paths, clock, {SITE: LAUNCH})
    clock.set(_at(LAUNCH + timedelta(days=35), 10))  # day 30 and its grace day have passed
    code, out, _ = run_cli(["update", "--all-due", "--stage", "d30", "--record"], data, clock=clock)
    assert code == 1 and "MISSED" in out and "never backfilled" in out
    assert "in the next 3 days" not in out


def test_due_prints_a_run_now_command_only_for_stages_due_today():
    result = {"today": "2026-12-08", "window_days": 3, "missed": [], "due": [
        {"site": "a.example", "source": "seed", "stage": "d7", "stage_date": "2026-12-08"},
        {"site": "b.example", "source": "seed", "stage": "d7", "stage_date": "2026-12-11"}]}
    lines = forecasting.due_lines(result)
    assert any(line.startswith("  a.example") and "Run: " in line for line in lines)
    later = next(line for line in lines if line.startswith("  b.example"))
    assert "Run it on 2026-12-11" in later and "Run: " not in later


def test_a_search_console_folder_changed_after_its_freeze_is_not_read(tmp_path):
    def dates_csv(days: int, impressions: int) -> str:
        rows = [f"{(LAUNCH + timedelta(days=k)).isoformat()},0,{impressions},0%,40.0" for k in range(days)]
        return "\n".join(["Date,Clicks,Impressions,CTR,Position", *rows]) + "\n"

    paths = forecast_paths(tmp_path / "data")
    clock = pipeline.VirtualClock(_at(LAUNCH - timedelta(days=2), 9))
    board = Scoreboard(f"sqlite:///{paths.root / 'scoreboard.db'}", clock=clock.now)
    board.add_site(SITE, source="seed", launch_date=LAUNCH, launch_date_source="test", built_by="test", niche="plumber")
    export = tmp_path / "seedone.example-Performance-on-Search-2026-12-03"
    export.mkdir()
    (export / "Dates.csv").write_text(dates_csv(2, 0), encoding="utf-8")
    clock.set(_at(LAUNCH + timedelta(days=2), 9))
    record = pipeline.import_gsc(paths, board, site=SITE, file=export, kind="ui", fetched_at=clock.now())
    clock.set(_at(LAUNCH + timedelta(days=7), 12))
    inputs = forecasting.LiveInputs(board, paths, fake_strength, REAL, clock.now())
    frozen, note = forecasting.gsc_columns(inputs, SITE, SEARCHES[0], LAUNCH, LAUNCH + timedelta(days=7))
    assert note == "1 Search Console exports"
    (paths.root / record.stored_path / "Dates.csv").write_text(dates_csv(6, 500), encoding="utf-8")
    after, note = forecasting.gsc_columns(inputs, SITE, SEARCHES[0], LAUNCH, LAUNCH + timedelta(days=7))
    board.close()
    assert all(math.isnan(v) for v in after.values())
    assert "1 export skipped: stored copy changed since its freeze" in note


# ---------------------------------------------------------------------------------------------
# strength sync: list lookups for brand-new sites (fake list files served by httpx.MockTransport)

SYNC_DAY = date(2026, 11, 30)  # Common Crawl cc-main-2026-aug-sep-oct, CrUX 202610 and the Tranco list of 2026-11-01
LISTED = {"rival01.com": 1200, "rival02.com": 98000}  # in every fake list; the other rivals and the new site are not


def _cc_file(listed: dict[str, int]) -> bytes:
    import gzip

    lines = "#harmonicc_pos\t#harmonicc_val\t#pr_pos\t#pr_val\t#host_rev\t#n_hosts\n"
    lines += "".join(f"{pos}\t1.5e7\t{pos}\t1e-6\t{'.'.join(reversed(site.split('.')))}\t4\n" for site, pos in listed.items())
    lines += "".join(f"{900000 + i}\t1.0\t{900000 + i}\t1e-9\tcom.filler{i}\t1\n" for i in range(20))
    return gzip.compress(lines.encode("utf-8"))


class _Chunks(httpx.SyncByteStream):
    """A response body streamed in small chunks, like a real large download."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):
        for start in range(0, len(self.data), 64):
            yield self.data[start:start + 64]


def list_server(seen: list[str], *, cc_cut: bool = False, crux_status: int = 200, tranco_rows: int = 3):
    """A handler serving fake list files in each provider's exact format. `seen` gets every URL asked for."""
    import gzip

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith("-domain-ranks.txt.gz"):
            data = _cc_file(LISTED)
            if cc_cut:  # no length header and the gzip member stops half way: a broken stream
                return httpx.Response(200, stream=_Chunks(data[: len(data) // 2]))
            return httpx.Response(200, stream=_Chunks(data), headers={"content-length": str(len(data))})
        if "crux-top-lists" in url:
            if crux_status != 200:
                return httpx.Response(crux_status)
            body = "origin,rank\n" + "".join(f"https://www.{s},{1000 if p < 5000 else 100000}\n" for s, p in LISTED.items())
            return httpx.Response(200, content=gzip.compress(body.encode("utf-8")))
        if "/api/lists/date/" in url:
            day = url.rsplit("/", 1)[1]
            return httpx.Response(200, json={"available": True, "list_id": "FAKE1", "created_on": f"{day}T22:00:01",
                                             "download": "https://tranco-list.eu/download/FAKE1/3",
                                             "configuration": {"startDate": day, "endDate": day, "providers": ["x"]}})
        if "/download/FAKE1/" in url:
            rows = ["rival01.com", "rival02.com", "big.example"][:tranco_rows]
            return httpx.Response(200, content="".join(f"{i},{s}\n" for i, s in enumerate(rows, start=1)).encode())
        return httpx.Response(404)

    return handler


def sync_sources(paths, client):
    from aeo.ranksim.forecast.sources import strength_lists as sl

    return [sl.CommonCrawlDomainRanks(paths.root, client=client), sl.OpenPageRankHistory(paths.root, client=client),
            sl.CruxTopLists(paths.root, client=client), sl.TrancoByDate(paths.root, client=client, limiter=sl.RateLimiter(0.0))]


def _sync_board(tmp_path: Path):
    paths = forecast_paths(tmp_path / "data")
    paths.root.mkdir(parents=True, exist_ok=True)
    clock = pipeline.VirtualClock(_at(SYNC_DAY - timedelta(days=2), 9))
    board = Scoreboard(f"sqlite:///{paths.root / 'scoreboard.db'}", clock=clock.now)
    board.add_site(SITE, source="seed", launch_date=LAUNCH, launch_date_source="test", built_by="test", niche="plumber")
    board.add_search(SITE, SEARCHES[0], location_code=LOCATION, difficulty="easy")
    read(board, clock, _at(SYNC_DAY - timedelta(days=1), 9), SEARCHES[0], RIVALS[:6])
    clock.set(_at(SYNC_DAY, 10))
    return paths, clock, board


def test_a_sync_dry_run_names_the_release_on_or_before_the_date_and_downloads_nothing(tmp_path):
    import httpx

    paths, clock, board = _sync_board(tmp_path)
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(list_server(seen)))
    result = forecasting.strength_sync(board, paths, [(SITE, SYNC_DAY)], now=clock.now(), fetch=False,
                                       sources=sync_sources(paths, client))
    board.close()
    assert seen == []  # a dry run sends nothing
    releases = {item.source: item.release for item in result.items}
    assert releases == {"commoncrawl_domain_ranks": "cc-main-2026-aug-sep-oct", "crux_top_lists": "crux-202610",
                        "tranco": "tranco-for-2026-11-01"}
    assert all(len(item.missing) == 7 for item in result.items)  # the new site and the six sites on its page
    text = "\n".join(forecasting.strength_sync_lines(result))
    assert "2.5 GB, large" in text and "about 9 MB" in text and "Dry run. Nothing was downloaded" in text
    assert "Open PageRank is not looked up" in text and not any(d in text for d in DASHES)


def test_a_release_published_after_the_date_is_never_chosen():
    paths = forecast_paths(Path("/nonexistent-ranksim-root"))
    day = date(2026, 10, 21)  # cc-main-2026-aug-sep-oct came out on 2026-10-22; CrUX 202609 on 2026-10-15
    items = forecasting.plan_strength_sync(paths, {("newsite.example", day)}, sources=sync_sources(paths, None))
    by_source = {item.source: item for item in items}
    assert by_source["commoncrawl_domain_ranks"].release == "cc-main-2026-jul-aug-sep"
    assert by_source["commoncrawl_domain_ranks"].published <= day
    assert by_source["crux_top_lists"].release == "crux-202609"
    assert by_source["tranco"].release == "tranco-for-2026-10-01"
    assert all(item.published <= day for item in items)


def test_a_searched_absent_site_gets_the_floor_and_an_unsearched_site_stays_nan(tmp_path):
    import httpx

    from aeo.ranksim.forecast.features import CC_ABSENT_POS_LOG10, TOPLIST_ABSENT_LOG10

    paths, clock, board = _sync_board(tmp_path)
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(list_server(seen)))
    result = forecasting.strength_sync(board, paths, [(SITE, SYNC_DAY)], now=clock.now(), fetch=True,
                                       sources=sync_sources(paths, client))
    board.close()
    assert not result.failed and [item.status for item in result.items] == ["done", "done", "done"]
    assert {item.source: item.found for item in result.items} == {"commoncrawl_domain_ranks": 2, "crux_top_lists": 2,
                                                                   "tranco": 2}
    strength = forecasting.RealStrength(paths)
    page_day = SYNC_DAY - timedelta(days=1)
    got = strength(pd.DataFrame({"site": [SITE, "rival01.com", "rival03.com", "neverlooked.example"],
                                 "as_of": [SYNC_DAY, page_day, page_day, SYNC_DAY]})).set_index("site")
    assert got.loc[SITE, "s_cc_hc"] == -CC_ABSENT_POS_LOG10 and got.loc[SITE, "s_cc_hosts"] == 0.0
    assert got.loc[SITE, "s_crux"] == -TOPLIST_ABSENT_LOG10 and got.loc[SITE, "s_tranco"] == -TOPLIST_ABSENT_LOG10
    assert got.loc["rival01.com", "s_cc_hc"] == pytest.approx(-math.log10(1200))
    assert got.loc["rival03.com", "s_cc_hc"] == -CC_ABSENT_POS_LOG10  # on the page, searched, not listed
    assert all(math.isnan(got.loc["neverlooked.example", m]) for m in ("s_cc_hc", "s_crux", "s_tranco"))
    assert strength.never_searched("neverlooked.example", SYNC_DAY) == list(forecasting.SYNC_SOURCES)
    assert strength.never_searched(SITE, SYNC_DAY) == []


def test_a_failed_or_cut_download_marks_nothing_searched(tmp_path):
    import httpx

    from aeo.ranksim.forecast.sources import strength_lists as sl

    paths, clock, board = _sync_board(tmp_path)
    seen: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(list_server(seen, cc_cut=True, crux_status=404, tranco_rows=2)))
    sources = sync_sources(paths, client)
    result = forecasting.strength_sync(board, paths, [(SITE, SYNC_DAY)], now=clock.now(), fetch=True, sources=sources)
    board.close()
    assert result.failed and [item.status for item in result.items] == ["failed", "failed", "failed"]
    crux_asked = [url.rsplit("/", 1)[1] for url in seen if "crux-top-lists" in url]
    assert crux_asked == ["202610.csv.gz", "202609.csv.gz", "202608.csv.gz", "202607.csv.gz"]  # never a later month
    assert sources[0].searched("cc-main-2026-aug-sep-oct") == set()
    assert not list((paths.cache / "strength").rglob("*.searched.txt.gz"))
    assert not list(sources[3].public_dir.glob("tranco_*.csv"))  # the cut list file was removed, never read
    got = forecasting.RealStrength(paths)(pd.DataFrame({"site": [SITE], "as_of": [SYNC_DAY]}))
    assert math.isnan(got.loc[0, "s_cc_hc"])  # absence after a failed read is never the floor
    lines = forecasting.strength_sync_lines(result)
    assert any("FAILED" in line and "never a floor" in line for line in lines)
    with pytest.raises(sl.IncompleteRead):
        list(sl.stream_gzip_lines_strict([_cc_file(LISTED)[:40]]))


def test_a_strength_row_cached_as_missing_never_hides_a_later_lookup(tmp_path):
    import httpx

    from aeo.ranksim.forecast.features import CC_ABSENT_POS_LOG10
    from aeo.ranksim.forecast.sources import strength_lists as sl

    paths, clock, board = _sync_board(tmp_path)
    board.close()
    before = forecasting.RealStrength(paths)(pd.DataFrame({"site": [SITE], "as_of": [SYNC_DAY]}))
    assert math.isnan(before.loc[0, "s_cc_hc"])
    cached = pd.read_parquet(forecasting.strength_cache_path(paths))
    assert len(cached) == 1 and math.isnan(cached.loc[0, "cc_present"])  # stored as missing by cached_strength
    client = httpx.Client(transport=httpx.MockTransport(list_server([])))
    sl.materialise_cc_release_strict(sl.CommonCrawlDomainRanks(paths.root, client=client), [SITE], "cc-main-2026-aug-sep-oct")
    after = forecasting.RealStrength(paths)(pd.DataFrame({"site": [SITE], "as_of": [SYNC_DAY]}))
    assert after.loc[0, "s_cc_hc"] == -CC_ABSENT_POS_LOG10
    assert forecasting.purge_missing_strength_rows(forecasting.strength_cache_path(paths), [SITE]) == 1


def test_an_upload_names_the_sync_command_and_after_the_sync_far_fewer_features_are_missing(tmp_path, real_bundle_paths):
    import httpx

    paths, clock, board = _sync_board(tmp_path)
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    first = forecasting.plan_forecasts(board, paths, bundle, site=SITE, stage="upload", day=SYNC_DAY, now=clock.now())
    missing_before = first.forecasts[0].notes["features_missing"]
    command = f"strength sync --site {SITE} --date {SYNC_DAY} --fetch --dir {paths.root}"
    assert any("were never looked up" in note and command in note for note in first.notes)
    assert "st_s_cc_hc" in missing_before
    client = httpx.Client(transport=httpx.MockTransport(list_server([])))
    forecasting.strength_sync(board, paths, [(SITE, SYNC_DAY)], now=clock.now(), fetch=True,
                              sources=sync_sources(paths, client))
    second = forecasting.plan_forecasts(board, paths, bundle, site=SITE, stage="upload", day=SYNC_DAY, now=clock.now())
    board.close()
    missing_after = second.forecasts[0].notes["features_missing"]
    assert len(missing_after) < len(missing_before) and "st_s_cc_hc" not in missing_after
    assert not any("were never looked up" in note for note in second.notes)
    age = next(note for note in second.notes if note.startswith("Strength lists for"))
    assert "Common Crawl domain ranks: published 39 days before the date" in age
    assert "Chrome UX Report top list: published 15 days before the date" in age
    lines = forecasting.run_lines(second, record=False)
    assert any("model features are missing" in line for line in lines) or not missing_after


def test_strength_sync_command_is_a_dry_run_without_fetch(tmp_path):
    paths, clock, board = _sync_board(tmp_path)
    board.close()
    code, out, err = run_cli(["strength", "sync", "--site", SITE, "--date", SYNC_DAY.isoformat()], paths.root, clock=clock)
    assert code == 0 and "cc-main-2026-aug-sep-oct" in out and "Dry run. Nothing was downloaded" in out
    code, out, _ = run_cli(["strength", "sync", "--all-due", "--date", SYNC_DAY.isoformat(), "--json"], paths.root,
                           clock=clock)
    payload = json.loads(out)
    assert code == 0 and payload["targets"] == [{"site": SITE, "date": SYNC_DAY.isoformat()}] and not payload["fetch"]


# ---------------------------------------------------------------------------------------------
# the band prior stored in every bundle


def test_a_bundle_stores_a_band_prior_fitted_on_its_own_training_rows_only(real_bundle_paths):
    from aeo.ranksim.forecast.grading import GradedCell, fit_band_prior

    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    prior = bundle.band_prior
    rows = fixture_rows()
    cells = [GradedCell(site=s, search=q, point=int(p), actual=int(p), difficulty=d)
             for s, q, p, d in zip(rows["site"], rows["search"], rows["position_out"], rows["difficulty"])]
    assert prior["table"] == {k: int(v) for k, v in fit_band_prior(cells).items()}
    assert prior["rows"] == 60 and prior["grading_rule"] == "v1" and prior["conditional_on_appearing"] is True
    assert prior["fitted_sites"] == sorted(set(rows["site"]))
    assert "conditional on appearing" in prior["from"] and "own 60 training rows" in prior["from"]
    summary = bundle.manifest["band_prior"]
    assert summary["table"] == prior["table"] and "fitted_sites" not in summary
    assert "band_prior" in bundle.manifest["files"]


def test_panel_like_rows_get_band_prior_terciles_cut_on_their_own_searches():
    rows = pd.DataFrame({"site": [f"s{i}.com" for i in range(9)], "search": [f"q{i}" for i in range(9)],
                         "pg_kd": [5, 10, 15, 40, 45, 50, 80, 85, 90], "position_out": [3, 3, 3, 30, 30, 30, NT50, NT50, NT50]})
    prior = forecasting.fit_bundle_band_prior(rows, includes_never_ranked=False, source="panel", label=REAL,
                                              difficulty_metric="DataForSEO keyword difficulty", split_note="train only")
    assert prior["tercile_cuts"] == pytest.approx([float(np.quantile(rows["pg_kd"], 1 / 3)), float(np.quantile(rows["pg_kd"], 2 / 3))])
    assert prior["table"]["easy"] == 1 and prior["table"]["medium"] == 21 and prior["table"]["hard"] == NT50


def _graded_seed_board(tmp_path: Path, bundle_paths, site: str):
    """One seed upload forecast made by the cond-real bundle, then graded on its day 90 reading."""
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = Scoreboard(f"sqlite:///{tmp_path / 'board.db'}", clock=clock.now)
    board.add_site(site, source="seed", launch_date=LAUNCH, launch_date_source="test", built_by="test", niche="plumber")
    board.add_search(site, SEARCHES[0], location_code=LOCATION, difficulty="easy")
    read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    clock.set(_at(date(2026, 11, 30), 10))
    bundle = forecasting.load_bundle(bundle_paths, "cond-real")
    run = forecasting.upload_or_update(board, bundle_paths, bundle=bundle, site=site, stage="upload", day=None,
                                       now=clock.now(), record=True, strength=fake_strength)
    assert len(run.recorded) == 1
    day90 = LAUNCH + timedelta(days=90)
    read(board, clock, _at(day90, 9), SEARCHES[0], RIVALS)
    clock.set(_at(day90 + timedelta(days=1), 9))
    assert pipeline.grade(board, day90 + timedelta(days=1))["counts"]["graded"] == 1
    return board, day90 + timedelta(days=1), bundle


def test_a_seed_report_uses_the_band_prior_of_the_bundle_that_made_its_forecasts(tmp_path, real_bundle_paths):
    board, day, bundle = _graded_seed_board(tmp_path, real_bundle_paths, SITE)
    result = pipeline.stage_marker(board, source="seed", stage="upload", as_of=day, paths=real_bundle_paths)
    board.close()
    report = result["report"]
    assert report["band_prior"] == bundle.band_prior["table"] and "band_prior" not in report["baselines_missing"]
    assert report["band_prior_source"].startswith("The band prior comes from bundle cond-real")
    text = pipeline.report_module.render_text(report)
    assert text.count("The band prior comes from bundle cond-real") == 1 and "conditional on appearing" in text


def test_a_seed_report_refuses_a_band_prior_fitted_on_a_site_it_grades(tmp_path, real_bundle_paths):
    board, day, _ = _graded_seed_board(tmp_path, real_bundle_paths, "train03.com")  # a training site of the bundle
    with pytest.raises(pipeline.ScoreboardRefusal, match="fitted on training data only"):
        pipeline.stage_marker(board, source="seed", stage="upload", as_of=day, paths=real_bundle_paths)
    board.close()


def test_a_report_never_mixes_band_priors_of_two_bundles_and_an_old_bundle_has_none(tmp_path, real_bundle_paths):
    from types import SimpleNamespace

    a = forecasting.load_bundle(real_bundle_paths, "cond-real")
    b = forecasting.load_bundle(real_bundle_paths, "late-real")
    mixed = [SimpleNamespace(model_version=f"{a.version}|x"), SimpleNamespace(model_version=f"{b.version}|y")]
    found = forecasting.report_band_prior(mixed, real_bundle_paths)
    assert found["table"] is None and "from 2 bundles" in found["note"] and "none is used" in found["note"]
    paths = forecast_paths(tmp_path / "data")
    old = forecasting.save_bundle(paths, "old-one", placement=a.placement, strength_only=a.strength_only, updater=None,
                                  trust=None, manifest={"training_label": REAL, "includes_never_ranked": False,
                                                        "training_cutoff": "2025-12-31"})
    found = forecasting.report_band_prior([SimpleNamespace(model_version=f"{old.version}|z")], paths)
    assert found["table"] is None and "before bundles stored a band prior" in found["note"]
    found = forecasting.report_band_prior([SimpleNamespace(model_version="m1")], paths)
    assert found["table"] is None and "not made by a live model bundle" in found["note"]


# ---------------------------------------------------------------------------------------------
# the content match score as a model input


def test_the_live_content_score_is_a_model_input_only_from_the_backend_the_bundle_trained_on():
    scored = {"value": 2.5, "backend": "lexical"}
    assert forecasting.content_feature(scored, "lexical") == (2.5, pytest.approx(
        "model input: the live lexical score, the backend the bundle was trained on"))
    value, note = forecasting.content_feature(scored, "cross_encoder")
    assert math.isnan(value) and "ran lexical" in note and "cross_encoder" in note
    value, note = forecasting.content_feature(scored, None)
    assert math.isnan(value) and "does not name" in note
    value, note = forecasting.content_feature({"value": math.nan, "backend": None}, "lexical")
    assert math.isnan(value) and "no page text" in note


def test_a_bundle_with_content_match_gets_the_live_score_only_when_the_backends_match(tmp_path):
    from aeo.ranksim.forecast.content import ContentScorer

    paths = forecast_paths(tmp_path / "data")
    rows = fixture_rows()
    rows["content_match"] = np.linspace(-2, 4, len(rows))
    for name, backend in (("cm-lexical", "lexical"), ("cm-cross", "cross_encoder")):
        forecasting.fit_bundle(paths, name, rows, rows, label=REAL, source="paid", includes_never_ranked=True,
                               training_cutoff=date(2025, 12, 31), group_col="search_group", trust=None,
                               difficulty_metric="kd", split_note="test fixture",
                               extra_manifest={"content_match_backend": backend})
    page = paths.imports / "pages" / SITE
    page.mkdir(parents=True)
    (page / "home.html").write_text("<html><head><title>Plumber prices in Austin</title></head><body><h1>Plumber prices"
                                    "</h1><p>Our plumber prices for every job.</p></body></html>", encoding="utf-8")
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    board = seed_board(tmp_path, clock)
    read(board, clock, _at(date(2026, 11, 29), 9), SEARCHES[0], RIVALS)
    clock.set(_at(date(2026, 11, 30), 10))
    made = {}
    for name in ("cm-lexical", "cm-cross"):
        bundle = forecasting.load_bundle(paths, name)
        assert "content_match" in bundle.placement.features
        run = forecasting.plan_forecasts(board, paths, bundle, site=SITE, stage="upload", day=None, now=clock.now(),
                                         strength=fake_strength, content_scorer=ContentScorer("lexical"))
        made[name] = run.forecasts[0].notes
    board.close()
    assert "content_match" not in made["cm-lexical"]["features_missing"]
    assert "the backend the bundle was trained on" in made["cm-lexical"]["content_match"]["note"]
    assert "content_match" in made["cm-cross"]["features_missing"]
    assert "ran lexical" in made["cm-cross"]["content_match"]["note"]


def test_a_bundle_without_content_match_never_feeds_the_score(real_bundle_paths):
    bundle = forecasting.load_bundle(real_bundle_paths, "cond-real")
    assert "content_match" not in bundle.placement.features
    assert "content_match" in bundle.manifest["features_dropped_no_value"]


# ---------------------------------------------------------------------------------------------
# --all-due with an explicit --date, and network errors


def test_all_due_with_an_explicit_date_on_the_grace_day_dates_the_forecast_on_the_stage_day(tmp_path, real_bundle_paths,
                                                                                              monkeypatch):
    monkeypatch.setattr(forecasting, "real_strength_fn", lambda paths: fake_strength)
    clock = pipeline.VirtualClock(_at(date(2026, 11, 28), 9))
    data = _cli_data(tmp_path, real_bundle_paths, clock, {SITE: LAUNCH})
    day7 = LAUNCH + timedelta(days=7)
    board = Scoreboard(f"sqlite:///{data / 'scoreboard.db'}", clock=clock.now)
    read(board, clock, _at(day7, 9), SEARCHES[0], RIVALS)
    board.close()
    grace = day7 + timedelta(days=1)
    clock.set(_at(grace, 12))
    code, out, _ = run_cli(["update", "--all-due", "--stage", "d7", "--date", grace.isoformat(), "--record", "--json"], data,
                           clock=clock)
    payload = json.loads(out)
    assert code == 0
    assert [(run["site"], run["as_of"], len(run["recorded"]), run["blocked"]) for run in payload["runs"]] == \
        [(SITE, day7.isoformat(), 1, None)]


def test_a_network_error_in_a_forecast_command_ends_in_one_sentence_and_exit_1(tmp_path, monkeypatch):
    import httpx

    def broken(*_args, **_kwargs):
        raise httpx.ConnectError("could not reach the host")

    monkeypatch.setattr(forecasting, "due", broken)
    code, out, err = run_cli(["due"], tmp_path / "data")
    assert code == 1 and "Traceback" not in err and err.count("\n") == 1
    assert err.startswith("A network request failed (ConnectError: could not reach the host).")
    monkeypatch.setattr(forecasting, "due", lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("reset")))
    code, _, err = run_cli(["due"], tmp_path / "data")
    assert code == 1 and "ConnectionError: reset" in err
