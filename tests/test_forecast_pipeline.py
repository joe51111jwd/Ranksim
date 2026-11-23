"""The measuring pipeline (forecast/pipeline.py). No network, no keys, no real sleeping.

Everything built here is SYNTHETIC or a hand-made fixture. Nothing is accuracy.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from aeo.errors import ProviderConfigError
from aeo.ranksim.forecast import pipeline
from aeo.ranksim.forecast.budget import KIND_SERP_STANDARD_NORMAL, SpendLedger, serp_cost, serper_cost
from aeo.ranksim.forecast.config import NT50, STAGE_DAYS, forecast_paths
from aeo.ranksim.forecast.reader import ReplaySource
from aeo.ranksim.forecast.scoreboard import Scoreboard, ScoreboardRefusal
from aeo.ranksim.forecast.seeds import SeedManifest, SeedSearch, SeedSite
from aeo.ranksim.forecast.synth import World
from aeo.ranksim.forecast.types import SYNTHETIC, Forecast, OrganicHit, Pull, PullSpec

START = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
DASHES = ("\u2014", "\u2013")


@pytest.fixture(autouse=True)
def _no_committed_export(monkeypatch, tmp_path):
    # The committed export of the repo (evals/scoreboard) must never anchor a test scoreboard.
    monkeypatch.setenv("RANKSIM_EXPORT_DIR", str(tmp_path / "committed_export"))


@pytest.fixture(scope="module")
def demo_result(tmp_path_factory):
    folder = tmp_path_factory.mktemp("demo")
    return pipeline.demo(3, workdir=folder / "run", sites=2)


# ---------------------------------------------------------------------------------------------
# The demo


def test_demo_runs_end_to_end_and_every_line_says_synthetic(demo_result):
    result = demo_result
    assert result.label == SYNTHETIC
    assert result.lines and all(line.startswith("SYNTHETIC: ") for line in result.lines)
    assert not any(dash in line for line in result.lines for dash in DASHES)
    assert result.register["sites_added"] == 2 and result.register["searches_added"] == 2 * pipeline.DEMO_TARGETS_PER_SITE
    assert result.forecasts_recorded == len(STAGE_DAYS) * 2 * pipeline.DEMO_TARGETS_PER_SITE
    assert result.grade_summary["counts"]["graded"] > 0
    assert result.grade_summary["counts"]["refused"] == 0
    assert result.chain["ok"] is True
    assert result.export_check["ok"] is True, result.export_check["problems"]
    assert all(run["label"] == SYNTHETIC for run in result.reading_runs)
    assert all(run["spent_usd"] is None for run in result.reading_runs)


def test_demo_report_dicts_have_the_expected_shape(demo_result):
    reports = demo_result.stage_reports
    assert set(reports) == set(STAGE_DAYS)
    for stage, report in reports.items():
        assert report["kind"] == "stage_report" and report["stage"] == stage
        assert report["source"] == "synthetic" and report["label"] == SYNTHETIC
        assert report["model_version"].startswith(f"forecasting-v1:{pipeline.DEMO_BUNDLE}:")
        assert report["headline"]["forecasts"] > 0
        assert report["chain"]["ok"] is True
    evaluations = demo_result.marker_evaluations
    assert set(evaluations) == {"1", "3", "4", "5"}
    for number, evaluation in evaluations.items():
        assert evaluation["marker"] == int(number)
        assert evaluation["passed"] is not True  # synthetic results can never pass a marker
        assert evaluation["label"] == SYNTHETIC
    marker1 = demo_result.marker1
    assert marker1["report"]["label"] == SYNTHETIC
    assert marker1["report"]["built_from_pulls"] is True
    assert marker1["report"]["pairs_used"] == len(marker1["pairs"]) > 0
    assert marker1["report"]["raw_single_pull_baseline"] is not None
    assert "synthetic_check_passed" in marker1["evaluation"]


def test_demo_shows_the_refusals(demo_result):
    attempts = {item["attempt"]: item for item in demo_result.refusals}
    assert len(attempts) == 3 and all(item["refused"] for item in attempts.values())
    late = next(item for name, item in attempts.items() if "late" in name)
    assert "on or before launch" in late["refusal"]
    edit = next(item for name, item in attempts.items() if "edit" in name)
    assert "append-only" in edit["refusal"]
    replace = next(item for name, item in attempts.items() if "replace" in name)
    assert "never replaced" in replace["refusal"]
    assert sum("Refused as it should be" in line for line in demo_result.lines) == 3


def test_demo_is_deterministic_and_refuses_a_used_folder(demo_result, tmp_path):
    with pytest.raises(ValueError, match="fresh folder"):
        pipeline.demo(3, workdir=demo_result.workdir, sites=2)
    again = pipeline.demo(3, workdir=tmp_path / "again", sites=2)
    assert again.manifest_hash == demo_result.manifest_hash
    assert again.grade_summary["counts"] == demo_result.grade_summary["counts"]
    first = demo_result.stage_reports["d60"]["headline"]["summary"]["hit"]
    second = again.stage_reports["d60"]["headline"]["summary"]["hit"]
    assert first == second


# ---------------------------------------------------------------------------------------------
# Clocks, sources and costs


def test_virtual_clock_never_moves_back_and_sleep_returns_at_once():
    clock = pipeline.VirtualClock(START)
    clock.sleep(90)
    assert clock.now() == START + timedelta(seconds=90)
    with pytest.raises(ValueError, match="cannot move back"):
        clock.set(START)
    with pytest.raises(ValueError):
        pipeline.VirtualClock(datetime(2026, 1, 1))


def test_make_source_names_the_missing_environment_variables(tmp_path):
    class Settings:
        dataforseo_login = None
        dataforseo_password = None
        serper_api_key = None

    with pytest.raises(ProviderConfigError) as caught:
        pipeline.make_source("dataforseo", Settings(), None, tmp_path)
    assert "DATAFORSEO_LOGIN" in str(caught.value) and "DATAFORSEO_PASSWORD" in str(caught.value)
    with pytest.raises(ProviderConfigError, match="SERPER_API_KEY"):
        pipeline.make_source("serper", Settings(), None, tmp_path)
    Settings.serper_api_key = "serper-fake-key"
    with pytest.raises(ProviderConfigError, match="serper-location"):
        pipeline.make_source("serper", Settings(), None, tmp_path)
    with pytest.raises(ValueError, match="Unknown provider"):
        pipeline.make_source("bing", Settings(), None, tmp_path)
    with pytest.raises(ValueError, match="World"):
        pipeline.make_source("synthetic", Settings(), None, tmp_path)


def test_make_source_dataforseo_uses_the_standard_queue_with_repair(tmp_path):
    class Settings:
        dataforseo_login = "login-fake"
        dataforseo_password = "password-fake"
        serper_api_key = None

    source = pipeline.make_source("dataforseo", Settings(), None, tmp_path, clock=pipeline.VirtualClock(START))
    assert source.mode == "standard_normal" and source.resolve_poisoned_results is True
    assert "password-fake" not in repr(source.client) and "login-fake" not in repr(source.client)
    source.client.close()


def test_reading_cost_comes_from_budget_and_the_guard_reads_only(tmp_path):
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    plan = pipeline.plan_reading_cost(ledger, "dataforseo", 200, pulls=5, depth=50)
    assert plan.usd == serp_cost(50, pulls=1000) == Decimal("3.6")
    assert plan.calls == 1000 and plan.refusal is not None and "budget approve" in plan.refusal
    assert not ledger.path.exists()  # the plan wrote nothing
    ledger.approve(Decimal("50"), by="test", note="test")
    plan = pipeline.plan_reading_cost(ledger, "dataforseo", 200, pulls=5, depth=50)
    assert plan.refusal is not None and "sample serp" in plan.refusal
    ledger.record_sample(KIND_SERP_STANDARD_NORMAL, {"pulls": 1})
    plan = pipeline.plan_reading_cost(ledger, "dataforseo", 200, pulls=5, depth=50)
    assert plan.allowed and plan.remaining_usd == Decimal("50.000000")
    serper = pipeline.plan_reading_cost(ledger, "serper", 2, pulls=5, depth=50)
    assert serper.usd == serper_cost(50) and serper.calls == 50
    assert pipeline.plan_reading_cost(None, "synthetic", 3).usd == 0


def test_sample_plans_fit_under_the_one_dollar_cap(tmp_path):
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    plan = pipeline.plan_sample_serp(ledger, "dataforseo", 1000)
    assert plan.usd <= Decimal("1") and plan.usd + serp_cost(50) > Decimal("1")
    traffic = pipeline.plan_sample_traffic(ledger, 5000)
    assert traffic.usd <= Decimal("1") and traffic.calls == 1
    ranked = pipeline.plan_sample_ranked_keywords(ledger, 50, limit=100)
    assert ranked.usd <= Decimal("1")
    hist = pipeline.plan_sample_historical_serps(ledger, 100, months_back=24)
    assert hist.usd <= Decimal("1") and hist.calls >= 1
    ledger.record_sample(KIND_SERP_STANDARD_NORMAL, {"pulls": 1})
    again = pipeline.plan_sample_serp(ledger, "dataforseo", 3)
    assert again.refusal is not None and "already recorded" in again.refusal


# ---------------------------------------------------------------------------------------------
# Seed sites


def _manifest(launch: date = date(2026, 3, 1), *, domain: str = "seedone.example", location: int = 1026201) -> SeedManifest:
    searches = tuple(
        SeedSearch(search=text_, tercile=tercile, difficulty_value=value, difficulty_source="kd")
        for text_, tercile, value in (("plumber prices", "easy", 10.0), ("emergency plumber", "medium", 40.0),
                                      ("seedone plumbing", "hard", 70.0))
    )
    site = SeedSite(domain=domain, niche="plumber", launch_date=launch, pages=12, searches=searches)
    return SeedManifest(sites=(site,), location_code=location, difficulty_metric="kd", created_at="2026-02-01T00:00:00Z")


def _board(tmp_path: Path, clock: pipeline.VirtualClock) -> Scoreboard:
    return Scoreboard(f"sqlite:///{tmp_path / 'board.db'}", clock=clock.now)


def test_register_manifest_is_idempotent_and_freezes_the_hash(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    manifest = _manifest()
    first = pipeline.register_manifest(board, manifest)
    assert (first.sites_added, first.searches_added) == (1, 3)
    again = pipeline.register_manifest(board, manifest)
    assert (again.sites_added, again.sites_already, again.searches_added, again.searches_already) == (0, 1, 0, 3)
    freezes = {row["name"]: row for row in board.freezes()}
    assert freezes[first.freeze_name]["payload"]["manifest_hash"] == manifest.manifest_hash()
    with pytest.raises(ScoreboardRefusal, match="different details"):
        pipeline.register_manifest(board, _manifest(launch=date(2026, 3, 9)))
    counts = pipeline.scoreboard_counts(board)
    assert counts["sites"] == 1 and counts["searches"] == 3
    board.close()


def test_manifest_specs_read_a_shared_search_once():
    manifest = _manifest()
    other = SeedSite(domain="seedtwo.example", niche="plumber", launch_date=date(2026, 3, 1), pages=12,
                     searches=(SeedSearch("Plumber  Prices", "easy", 10.0, "kd"),))
    shared = SeedManifest(sites=manifest.sites + (other,), location_code=1026201, difficulty_metric="kd", created_at=manifest.created_at)
    specs = pipeline.manifest_specs(shared)
    assert len(specs) == 3 and all(spec.depth == 50 and spec.location_code == 1026201 for spec in specs)


def test_schedule_costs_come_from_budget_and_list_the_plan_problems():
    manifest = _manifest(launch=date(2026, 11, 30))
    result = pipeline.schedule(manifest, today=date(2026, 11, 22))
    assert result["readings_weekly_only"] == 13 and result["readings_with_milestones"] == 16
    assert Decimal(result["cost_with_milestones_usd"]) == serp_cost(50, pulls=5 * 3 * 16)
    assert any(not item["ok"] for item in result["plan_schedule_problems"])
    assert result["sites"][0]["launch_date"] == "2026-11-30"


# ---------------------------------------------------------------------------------------------
# Readings


def _pull(spec: PullSpec, moment: datetime, sites: list[str], *, ok: bool = True, tag: str = "") -> Pull:
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    hits = tuple(OrganicHit(rank=i, url=f"https://{site}/p", domain=site, site=site) for i, site in enumerate(sites, start=1))
    return Pull(
        pull_id=f"pull-{spec.search.replace(' ', '')}-{stamp}{tag}",
        provider="replay",
        endpoint="replay",
        spec=spec,
        requested_at=stamp,
        completed_at=stamp,
        ok=ok,
        status_code=20000 if ok else 50000,
        hits=hits if ok else (),
    )


def _replay(spec: PullSpec, moment: datetime, sites: list[str], *, pulls: int = 5, failed: int = 0) -> list[Pull]:
    out = []
    for index in range(pulls):
        at = moment + timedelta(minutes=index)
        out.append(_pull(spec, at, sites, ok=index >= failed, tag=f"-{index}"))
    return out


SITES = [f"site{i:02d}.com" for i in range(1, 31)]


def test_take_readings_stores_everything_and_counts_drops(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    good = PullSpec("plumber prices", 1026201)
    bad = PullSpec("emergency plumber", 1026201)
    source = ReplaySource(_replay(good, START, SITES) + _replay(bad, START, SITES, failed=3))
    summary = pipeline.take_readings(board, source, [good, bad], pulls=5, gap_seconds=60, clock=clock)
    assert summary.readings == 2 and summary.degraded == 1
    assert summary.pulls_returned == 10 and summary.pulls_ok == 7 and summary.pulls_dropped == 3
    assert summary.drop_reasons == {"not_ok": 3}
    assert summary.spent_usd is None and summary.label == "REAL"
    assert clock.now() == START + timedelta(seconds=4 * 60)  # four gaps, waited on the virtual clock
    assert pipeline.scoreboard_counts(board)["pulls"] == 10
    stored = board.reading(summary.reading_ids[0])
    assert stored.position_of("site01.com") == 1
    board.close()


def test_take_readings_labels_synthetic_readings(tmp_path):
    world = World(3, niches=1, searches_per_niche=12, established_sites=40, new_sites=1)
    day = world.start + timedelta(days=200)
    clock = pipeline.VirtualClock(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    source = pipeline.make_source("synthetic", None, None, tmp_path, world=world, clock=clock)
    specs = pipeline.search_specs(world.searches_in(world.niches[0])[:3], world.location_code)
    summary = pipeline.take_readings(board, source, specs, pulls=5, gap_seconds=60, clock=clock)
    assert summary.label == SYNTHETIC
    assert all(board.reading(reading_id).label == SYNTHETIC for reading_id in summary.reading_ids)
    board.close()


def test_persistence_placeholder_uses_no_reading_after_as_of(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    manifest = _manifest(launch=date(2026, 1, 1))
    pipeline.register_manifest(board, manifest, today=date(2025, 12, 31))  # registered before launch
    spec = PullSpec("plumber prices", 1026201)
    early = START
    later = START + timedelta(days=3)
    source = ReplaySource(_replay(spec, early, ["seedone.example"] + SITES) + _replay(spec, later, SITES))
    pipeline.take_readings(board, source, [spec], clock=clock)
    clock.set(later)
    pipeline.take_readings(board, source, [spec], clock=clock)
    forecast, inputs = pipeline.persistence_forecast(
        board, site="seedone.example", search="plumber prices", stage="d7", as_of=early.date(),
        location_code=1026201, device="desktop", now=clock.now(),
    )
    assert forecast.point == 1 and len(inputs) == 1 and forecast.model_version == pipeline.PERSISTENCE_MODEL
    latest, _ = pipeline.persistence_forecast(
        board, site="seedone.example", search="plumber prices", stage="d14", as_of=later.date(),
        location_code=1026201, device="desktop", now=clock.now(),
    )
    assert latest.point == NT50 and latest.label == "out_of_reach" and (latest.range_low, latest.range_high) == (NT50, NT50)
    board.close()


# ---------------------------------------------------------------------------------------------
# Marker 1 pairing


def test_marker1_pairs_readings_20_to_28_hours_apart(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    paired = PullSpec("plumber prices", 1026201)
    lonely = PullSpec("emergency plumber", 1026201)
    day_b = START + timedelta(hours=24)
    day_c = START + timedelta(hours=60)
    pulls = _replay(paired, START, SITES) + _replay(lonely, START, SITES) + _replay(paired, day_b, SITES) + _replay(lonely, day_c, SITES)
    source = ReplaySource(pulls)
    pipeline.take_readings(board, source, [paired, lonely], clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, [paired], clock=clock)
    clock.set(day_c)
    pipeline.take_readings(board, source, [lonely], clock=clock)
    result = pipeline.marker1(board, ["plumber prices", "emergency plumber"], location_code=1026201)
    report = result["report"]
    assert report["pairs_used"] == 1 and len(result["pairs"]) == 1
    assert report["searches_unpaired"] == [{"search": "emergency plumber", "reason": "no two usable readings 20 to 28 hours apart"}]
    # 1 of 2 frozen searches gave a pair: below the measured share, so the headline is withheld.
    assert report["headline"]["rate"] is None and report["built_from_pulls"] is True
    assert report["headline"]["rate_on_measured_searches_only"] == 1.0
    assert result["evaluation"]["passed"] is None
    assert any("NOT MEASURABLE" in line or "Marker 1" in line for line in pipeline.marker1_lines(result))
    board.close()


def test_marker1_sample_is_frozen_once(tmp_path):
    board = _board(tmp_path, pipeline.VirtualClock(START))
    sample, _ = pipeline.freeze_marker1_sample(board, [f"search {i}" for i in range(20)], n=5)
    assert pipeline.frozen_marker1_searches(board) == list(sample.searches)
    with pytest.raises(ScoreboardRefusal, match="already frozen"):
        pipeline.freeze_marker1_sample(board, [f"search {i}" for i in range(20)], n=6)
    board.close()


# ---------------------------------------------------------------------------------------------
# verify_against_export


def _forecasting_board(tmp_path: Path) -> tuple[Scoreboard, pipeline.VirtualClock]:
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    pipeline.register_manifest(board, _manifest(launch=date(2026, 2, 1)))
    board.register_model_version("m1", description="test model", frozen_at=clock.now())
    for index, search in enumerate(("plumber prices", "emergency plumber", "seedone plumbing")):
        clock.sleep(60)
        board.record_forecast(Forecast(site="seedone.example", search=search, stage="upload", as_of=date(2026, 1, 5),
                                       point=10 + index, range_low=8, range_high=20, label="contested", model_version="m1"))
    return board, clock


def _drop_triggers_and_run(board: Scoreboard, sql: str) -> None:
    with board.engine.begin() as connection:
        for name in ("forecasts_no_delete", "forecasts_no_update", "pulls_no_delete"):
            connection.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        connection.execute(text(sql))
    board.install_triggers()


def test_verify_against_export_catches_truncation_the_chain_cannot_see(tmp_path):
    board, _ = _forecasting_board(tmp_path)
    export = tmp_path / "export"
    pipeline.export_scoreboard(board, export)
    assert pipeline.verify_against_export(board, export).ok
    _drop_triggers_and_run(board, "DELETE FROM forecasts WHERE seq = 3")
    assert board.verify().ok is True  # a shorter chain still verifies on its own
    comparison = pipeline.verify_against_export(board, export)
    assert not comparison.ok
    assert any("missing from the database" in problem and "sequence 3 to 3" in problem for problem in comparison.problems)
    assert comparison.counts["forecasts"] == {"export": 3, "database": 2}
    board.close()


def test_verify_against_export_allows_new_rows_and_names_an_edit(tmp_path):
    board, clock = _forecasting_board(tmp_path)
    export = tmp_path / "export"
    pipeline.export_scoreboard(board, export)
    clock.sleep(60)
    board.record_forecast(Forecast(site="seedone.example", search="plumber prices", stage="d7", as_of=date(2026, 1, 5),
                                   point=12, range_low=8, range_high=20, label="contested", model_version="m1"))
    assert pipeline.verify_against_export(board, export).ok  # rows added after the export are fine
    _drop_triggers_and_run(board, "UPDATE forecasts SET point = 1 WHERE seq = 2")
    comparison = pipeline.verify_against_export(board, export)
    assert not comparison.ok
    assert any("sequence 2 differs from the export in point" in problem for problem in comparison.problems)
    assert any("chain does not verify" in problem for problem in comparison.problems)
    board.close()


def test_verify_against_export_names_a_changed_export_and_a_lost_pull(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    pipeline.take_readings(board, ReplaySource(_replay(spec, START, SITES)), [spec], clock=clock)
    export = tmp_path / "export"
    pipeline.export_scoreboard(board, export)
    _drop_triggers_and_run(board, "DELETE FROM pulls WHERE pull_id = (SELECT MIN(pull_id) FROM pulls)")
    comparison = pipeline.verify_against_export(board, export)
    assert any("rows of pulls.jsonl are missing" in problem for problem in comparison.problems)
    (export / "freezes.jsonl").write_text('{"name": "forged"}\n', encoding="utf-8")
    comparison = pipeline.verify_against_export(board, export)
    assert any("freezes.jsonl does not match its sha256" in problem for problem in comparison.problems)
    missing = pipeline.verify_against_export(board, tmp_path / "nowhere")
    assert not missing.ok and "HEADS.json is missing" in missing.problems[0]
    board.close()


def test_verify_combines_scoreboard_ledger_and_export(tmp_path):
    board, _ = _forecasting_board(tmp_path)
    ledger = SpendLedger(tmp_path / "spend.jsonl")
    ledger.approve(Decimal("5"), by="test", note="test")
    result = pipeline.verify(board, ledger)
    assert result["ok"] and result["export"] is None
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text(lines[0].replace('"amount_micros":5000000', '"amount_micros":9000000') + "\n", encoding="utf-8")
    assert pipeline.verify(board, ledger)["ok"] is False
    board.close()


# ---------------------------------------------------------------------------------------------
# Imports


FIXTURES = Path(__file__).parent / "fixtures"


def test_import_gsc_copies_the_file_and_freezes_its_hash(tmp_path):
    paths = forecast_paths(tmp_path / "data")
    board = _board(tmp_path, pipeline.VirtualClock(START))
    record = pipeline.import_gsc(paths, board, site="seedsite.example", file=FIXTURES / "forecast_gsc" / "daily_queries.csv",
                                 kind="daily", fetched_at=START)
    assert record.rows > 0 and record.first_date == "2026-12-03"
    stored = paths.root / record.stored_path
    assert stored.read_bytes() == (FIXTURES / "forecast_gsc" / "daily_queries.csv").read_bytes()
    freezes = {row["name"]: row for row in board.freezes()}
    assert freezes[record.freeze_name]["payload"]["sha256"] == record.sha256
    again = pipeline.import_gsc(paths, board, site="seedsite.example", file=FIXTURES / "forecast_gsc" / "daily_queries.csv",
                                kind="daily", fetched_at=START + timedelta(days=1))
    assert again.freeze_name == record.freeze_name  # the same file again changes nothing
    ui = pipeline.import_gsc(paths, board, site="seedsite.example", file=FIXTURES / "forecast_gsc" / "ui_export", kind="ui")
    assert (paths.root / ui.stored_path).is_dir()
    board.close()


def test_import_refuses_smartcrew_data(tmp_path):
    paths = forecast_paths(tmp_path / "data")
    board = _board(tmp_path, pipeline.VirtualClock(START))
    with pytest.raises(ValueError, match="excluded"):
        pipeline.import_gsc(paths, board, site="smartcrewhq.com", file=FIXTURES / "forecast_gsc" / "daily_queries.csv", kind="daily")
    folder = tmp_path / "smartcrew"
    folder.mkdir()
    copy = folder / "q.csv"
    copy.write_bytes((FIXTURES / "forecast_gsc" / "daily_queries.csv").read_bytes())
    with pytest.raises(ValueError, match="SmartCrew"):
        pipeline.import_gsc(paths, board, site="seedsite.example", file=copy, kind="daily")
    assert not (paths.root / pipeline.IMPORTS_DIR).exists()
    board.close()


def test_import_form_freezes_a_valid_form(tmp_path):
    world = World(3, niches=1, searches_per_niche=12, established_sites=40, new_sites=1)
    site = next(iter(world.new_sites))
    form_path = tmp_path / "form.json"
    form_path.write_text(json.dumps({"form_version": 1, "answers": world.agency_form(site)}), encoding="utf-8")
    paths = forecast_paths(tmp_path / "data")
    board = _board(tmp_path, pipeline.VirtualClock(START))
    record = pipeline.import_form(paths, board, file=form_path)
    assert record.site == site and (paths.root / record.stored_path).exists()
    assert record.freeze_name in {row["name"] for row in board.freezes()}
    board.close()


def test_secret_values_skip_short_and_missing_values():
    class Settings:
        dataforseo_login = "abc"
        dataforseo_password = "longer-password"
        serper_api_key = None

    assert pipeline.secret_values(Settings()) == ["longer-password"]
    assert pipeline.credentials_present(Settings()) == {"dataforseo": True, "serper": False}


def test_import_gsc_reads_api_json_and_index_tables_with_their_dates(tmp_path):
    """Bug found in review: the API kind called parse_api_rows without its dimensions, and index rows lost their dates."""
    paths = forecast_paths(tmp_path / "data")
    board = _board(tmp_path, pipeline.VirtualClock(START))
    api = tmp_path / "api.json"
    api.write_text(json.dumps({"rows": [
        {"keys": ["2026-12-03", "emergency plumber"], "clicks": 1, "impressions": 4, "ctr": 0.25, "position": 35.5},
        {"keys": ["2026-12-05", "plumber prices"], "clicks": 0, "impressions": 2, "ctr": 0.0, "position": 29.0},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="dimensions"):
        pipeline.import_gsc(paths, board, site="seedsite.example", file=api, kind="api", fetched_at=START)
    record = pipeline.import_gsc(paths, board, site="seedsite.example", file=api, kind="api", fetched_at=START,
                                 dimensions=["date", "query"])
    assert (record.rows, record.first_date, record.last_date) == (2, "2026-12-03", "2026-12-05")
    index = pipeline.import_gsc(paths, board, site="seedsite.example", file=FIXTURES / "forecast_gsc" / "index_status.csv", kind="index")
    assert index.rows > 0 and index.first_date is not None and index.last_date is not None
    board.close()


# ---------------------------------------------------------------------------------------------
# History, batch writes and round mode


def test_take_readings_passes_recent_history_and_writes_one_transaction(tmp_path):
    from sqlalchemy import event

    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    second = START + timedelta(days=1)
    stale = START + timedelta(days=1 + 22)  # more than HISTORY_MAX_AGE_DAYS after the second reading
    source = ReplaySource(_replay(spec, START, SITES) + _replay(spec, second, SITES) + _replay(spec, stale, SITES))
    commits: list[int] = []
    event.listen(board.engine, "commit", lambda _connection: commits.append(1))
    first = pipeline.take_readings(board, source, [spec], clock=clock)
    assert first.history_used == 0 and first.reader_version == "reader-v2"
    assert len(commits) == 1  # five pulls and one reading in one transaction
    clock.set(second)
    assert pipeline.take_readings(board, source, [spec], clock=clock).history_used == 1
    clock.set(stale)
    assert pipeline.take_readings(board, source, [spec], clock=clock).history_used == 0  # the last one is 22 days old
    assert pipeline.HISTORY_MAX_AGE_DAYS == 21
    assert len(pipeline.history_for(board, [spec], before=stale + timedelta(days=21))) == 1
    assert pipeline.history_for(board, [spec], before=stale + timedelta(days=21, minutes=5)) == []
    assert pipeline.history_for(board, [spec], before=stale + timedelta(days=1), label=SYNTHETIC) == []
    stored = board.reading(first.reading_ids[0])
    assert stored.reader_version == "reader-v2" and stored.degraded_reason is None
    board.close()


def test_the_second_reading_benefits_from_the_first_under_correlated_poisoning(tmp_path):
    """SYNTHETIC. Five pulls a minute apart share an altered spell. Without history such a reading is kept
    whole and wrong. With yesterday's reading passed in by take_readings, it is degraded instead."""
    from dataclasses import replace as dc_replace

    from aeo.ranksim.forecast.synth import DEFAULT_KNOBS, SyntheticSource

    knobs = dc_replace(DEFAULT_KNOBS, poison_spell_minutes=90.0, poison_rate=0.3, poison_spell_share=0.9,
                       fail_rate=0.0, partial_rate=0.0, spell_rate=0.0, repaired_rate=0.0)
    world = World(1, niches=2, searches_per_niche=30, established_sites=200, new_sites=0, months=12, knobs=knobs)
    clean_world = World(1, niches=2, searches_per_niche=30, established_sites=200, new_sites=0, months=12,
                        knobs=dc_replace(knobs, poison_rate=0.0))
    specs = [PullSpec(info.text, world.location_code) for info in world.searches]
    day_one = datetime(2025, 8, 31, 10, tzinfo=timezone.utc)
    day_two = day_one + timedelta(days=1)
    known = {info.site for info in world.sites}

    def wrong(readings) -> int:
        return sum(1 for r in readings if not r.degraded and any(e.site not in known for e in r.positions if e.position <= 10))

    results = {}
    for name in ("with history", "alone"):
        clock = pipeline.VirtualClock(day_one)
        board = Scoreboard(f"sqlite:///{tmp_path / (name.replace(' ', '_') + '.db')}", clock=clock.now)
        if name == "with history":  # yesterday's clean reading of every search
            pipeline.take_readings(board, SyntheticSource(clean_world, clock.now), specs, pulls=5, gap_seconds=60, clock=clock)
        clock.set(day_two)
        summary = pipeline.take_readings(board, SyntheticSource(world, clock.now), specs, pulls=5, gap_seconds=60, clock=clock)
        readings = [board.reading(rid) for rid in summary.reading_ids]
        results[name] = (summary, readings)
        board.close()
    with_history, alone = results["with history"], results["alone"]
    assert with_history[0].history_used == len(specs) and alone[0].history_used == 0
    assert wrong(alone[1]) >= 1, "the synthetic world must give at least one wholly altered reading"
    assert wrong(with_history[1]) == 0 and wrong(with_history[1]) < wrong(alone[1])
    reasons = [r.degraded_reason for r in with_history[1] if r.degraded]
    assert any(reason.startswith("altered_vs_history") for reason in reasons)
    assert all(r.label == SYNTHETIC for r in with_history[1])


def test_read_round_and_assemble_build_readings_from_rounds_hours_apart(tmp_path):
    world = World(3, niches=1, searches_per_niche=12, established_sites=40, new_sites=1)
    day = world.start + timedelta(days=200)
    clock = pipeline.VirtualClock(datetime(day.year, day.month, day.day, 1, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    source = pipeline.make_source("synthetic", None, None, tmp_path, world=world, clock=clock)
    specs = pipeline.search_specs(world.searches_in(world.niches[0])[:4], world.location_code)
    for hour in (1, 5, 9, 13, 17):
        clock.set(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
        summary = pipeline.read_round(board, source, specs, clock=clock)
        assert summary.pulls_returned == 4 and summary.failed is None and summary.label == SYNTHETIC
        assert summary.freeze_name.startswith(pipeline.ROUND_FREEZE_PREFIX)
    assert pipeline.scoreboard_counts(board)["readings"] == 0  # a round builds no reading
    clock.set(datetime(day.year, day.month, day.day, 23, tzinfo=timezone.utc))
    result = pipeline.assemble(board, day)
    assert result.pulls_found == 20 and result.rounds == 5 and result.readings_built == 4 and result.readings_stored == 4
    assert result.labels == {SYNTHETIC: 4} and result.history_used == 0
    for reading_id in result.reading_ids:
        stored = board.reading(reading_id)
        assert stored.pulls_total == 5 and stored.reader_version == "reader-v2"
    again = pipeline.assemble(board, day)
    assert again.readings_stored == 0 and again.readings_already_stored == 4  # running it twice changes nothing
    # The next day's readings get the first day's readings as history.
    next_day = day + timedelta(days=1)
    for hour in (2, 8):
        clock.set(datetime(next_day.year, next_day.month, next_day.day, hour, tzinfo=timezone.utc))
        pipeline.read_round(board, source, specs, clock=clock)
    clock.set(datetime(next_day.year, next_day.month, next_day.day, 23, tzinfo=timezone.utc))
    second = pipeline.assemble(board, next_day, specs=specs[:2])
    assert second.readings_built == 2 and second.history_used == 2 and second.degraded == 2  # two rounds are too few
    assert second.degraded_reasons == {"too_few_kept_pulls": 2}
    assert pipeline.assemble(board, day + timedelta(days=5)).pulls_found == 0
    board.close()


def test_assemble_groups_unrecorded_pulls_into_rounds_by_time(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    pulls = [_pull(spec, START + timedelta(hours=3 * index), SITES, tag=f"-{index}") for index in range(5)]
    board.store_batch(pulls=pulls)
    clock.set(START + timedelta(hours=20))
    result = pipeline.assemble(board, START.date())
    assert result.rounds == 5 and result.readings_built == 1 and result.degraded == 0
    assert result.labels == {"REAL": 1}
    board.close()


def test_marker1_reports_always_name_the_reader(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    day_b = START + timedelta(hours=24)
    # The first pull of each reading fails, so each reading keeps 4 pulls and drops 1.
    source = ReplaySource(_replay(spec, START, SITES, failed=1) + _replay(spec, day_b, SITES, failed=1))
    pipeline.take_readings(board, source, [spec], clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, [spec], clock=clock)
    rebuilt = pipeline.marker1(board, ["plumber prices"], location_code=1026201, reader_version=1)
    assert rebuilt["report"]["built_from_pulls"] is True
    assert rebuilt["report"]["reader"]["reader_version"] == "reader-v1"
    assert rebuilt["report"]["headline_beside_ablation"]["filter_off"] is not None
    # Copied with every pull, the readings are rebuilt from their pulls in the other scoreboard too.
    full = Scoreboard(f"sqlite:///{tmp_path / 'full.db'}", clock=clock.now)
    for reading_id in rebuilt["pairs"][0]:
        reading = board.reading(reading_id)
        names = list(reading.pull_ids) + [pid for pid, _reason in reading.dropped]
        full.store_batch(pulls=[board.pull(pid) for pid in dict.fromkeys(names)], readings=[reading])
    assert pipeline.marker1(full, ["plumber prices"], location_code=1026201)["report"]["built_from_pulls"] is True
    full.close()
    # Copied with their kept pulls only (the dropped pull is not stored there), the report reads the stored
    # readings and still names the reader settings.
    bare = Scoreboard(f"sqlite:///{tmp_path / 'bare.db'}", clock=clock.now)
    for reading_id in rebuilt["pairs"][0]:
        reading = board.reading(reading_id)
        dropped = {pid for pid, _reason in reading.dropped}
        kept = [board.pull(pid) for pid in reading.pull_ids if pid not in dropped]
        assert len(kept) == reading.pulls_kept == 4
        bare.store_batch(pulls=kept, readings=[reading])
    stored = pipeline.marker1(bare, ["plumber prices"], location_code=1026201)
    assert stored["report"]["built_from_pulls"] is False
    assert stored["report"]["reader"]["reader_version"] == "reader-v2"
    assert stored["report"]["reader"]["stored_reader_versions"] == ["reader-v2"]
    assert stored["evaluation"]["label"] == "REAL"
    bare.close()
    board.close()


def test_imports_live_under_the_imports_folder(tmp_path):
    paths = forecast_paths(tmp_path / "data")
    assert paths.imports == tmp_path / "data" / "imports"


# ---------------------------------------------------------------------------------------------
# Brand terms from the agency form


def _form_file(tmp_path: Path, site: str, brands: list[str]) -> Path:
    form = {"form_version": 1, "answers": {"site": site, "niche": "plumber", "brand_names": brands, "filled_by": "agency",
                                           "filled_on": "2026-01-02", "footprint": {}, "launch_plan": {}}}
    path = tmp_path / f"form-{site}.json"
    path.write_text(json.dumps(form), encoding="utf-8")
    return path


def _pilot_manifest(domain: str, searches: list[str], launch: date = date(2026, 3, 1)) -> SeedManifest:
    terciles = ("easy", "medium", "hard")
    items = tuple(SeedSearch(search=text_, tercile=terciles[i % 3], difficulty_value=float(10 * i), difficulty_source="kd")
                  for i, text_ in enumerate(searches))
    site = SeedSite(domain=domain, niche="plumber", launch_date=launch, pages=12, searches=items)
    return SeedManifest(sites=(site,), location_code=1026201, difficulty_metric="kd", created_at="2026-01-03T00:00:00Z")


def _brand_flags(board: Scoreboard) -> dict[str, bool]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from aeo.ranksim.forecast.scoreboard import SearchRow

    with Session(board.engine) as session:
        return {row.search: bool(row.is_brand) for row in session.scalars(select(SearchRow)).all()}


def test_brand_names_on_the_imported_agency_form_flag_brand_searches(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    paths = forecast_paths(tmp_path / "data")
    record = pipeline.import_form(paths, board, file=_form_file(tmp_path, "jpdrains.com", ["Joes Plumbing"]))
    assert any("joes plumbing" in note for note in record.notes)
    pipeline.register_manifest(board, _pilot_manifest("jpdrains.com", ["joes plumbing reviews", "emergency plumber", "jpdrains"]),
                               source="pilot")
    flags = _brand_flags(board)
    board.close()
    assert flags == {"joes plumbing reviews": True, "emergency plumber": False, "jpdrains": True}


def test_a_pilot_without_an_imported_form_is_refused(tmp_path):
    board = _board(tmp_path, pipeline.VirtualClock(START))
    with pytest.raises(ScoreboardRefusal, match="No agency form is imported"):
        pipeline.register_manifest(board, _pilot_manifest("jpdrains.com", ["joes plumbing reviews"]), source="pilot")
    assert pipeline.scoreboard_counts(board)["sites"] == 0
    board.close()


def test_a_form_imported_after_the_site_is_registered_is_refused_when_it_changes_the_brand(tmp_path):
    board = _board(tmp_path, pipeline.VirtualClock(START))
    pipeline.register_manifest(board, _pilot_manifest("jpdrains.com", ["emergency plumber"]), source="seed")
    with pytest.raises(ScoreboardRefusal, match="never changed"):
        pipeline.import_form(forecast_paths(tmp_path / "data"), board, file=_form_file(tmp_path, "jpdrains.com", ["Joes Plumbing"]))
    # A form naming no other brand flags the same searches as the domain name, so it is accepted.
    pipeline.import_form(forecast_paths(tmp_path / "data"), board, file=_form_file(tmp_path, "jpdrains.com", []))
    assert pipeline.register_manifest(board, _pilot_manifest("jpdrains.com", ["emergency plumber"]), source="seed").sites_already == 1
    board.close()


# ---------------------------------------------------------------------------------------------
# Registering after launch


def test_register_refuses_a_site_that_already_launched_unless_a_reason_is_recorded(tmp_path):
    clock = pipeline.VirtualClock(datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    manifest = _manifest(launch=date(2026, 3, 1))
    with pytest.raises(ScoreboardRefusal, match="before launch"):
        pipeline.register_manifest(board, manifest)
    with pytest.raises(ScoreboardRefusal, match="before launch"):
        pipeline.register_manifest(board, manifest, today=date(2026, 3, 1))  # launch day itself is too late
    assert pipeline.scoreboard_counts(board)["sites"] == 0
    backfilled = replace(manifest, after_launch_reason="pilot site already live when the agency joined")
    assert pipeline.register_manifest(board, backfilled).sites_added == 1
    board.close()


def test_re_registering_after_launch_changes_nothing_and_is_allowed(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    manifest = _manifest(launch=date(2026, 3, 1))
    pipeline.register_manifest(board, manifest)
    clock.set(datetime(2026, 4, 1, tzinfo=timezone.utc))
    again = pipeline.register_manifest(board, manifest)
    assert (again.sites_added, again.searches_added) == (0, 0)
    board.close()


# ---------------------------------------------------------------------------------------------
# Marker 1 counts every frozen search


def test_marker1_is_not_passed_when_most_frozen_searches_have_no_clean_pair(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    searches = [f"plumber search {i:03d}" for i in range(300)]
    pipeline.freeze_marker1_sample(board, searches, n=300)
    specs = [PullSpec(s, 1026201) for s in searches]
    day_b = START + timedelta(hours=24)
    recorded: list[Pull] = []
    for moment in (START, day_b):
        for index, spec in enumerate(specs):
            recorded += _replay(spec, moment, SITES, failed=0 if index < 3 else 3)  # 297 come back degraded
    source = ReplaySource(recorded)
    pipeline.take_readings(board, source, specs, clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, specs, clock=clock)
    result = pipeline.marker1(board, pipeline.frozen_marker1_searches(board), location_code=1026201)
    board.close()
    report = result["report"]
    assert report["pairs_used"] == 3 and report["searches_requested"] == 300
    assert report["headline"]["rate"] is None and "NOT MEASURABLE" in report["headline"]["not_measurable"]
    assert result["evaluation"]["passed"] is None
    assert any("NOT MEASURABLE" in line for line in pipeline.marker1_lines(result))


def test_marker1_on_a_list_that_is_not_the_frozen_sample_is_not_a_marker_result(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    specs = [PullSpec(f"plumber search {i:02d}", 1026201) for i in range(4)]
    day_b = START + timedelta(hours=24)
    source = ReplaySource([p for spec in specs for moment in (START, day_b) for p in _replay(spec, moment, SITES)])
    pipeline.take_readings(board, source, specs, clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, specs, clock=clock)
    result = pipeline.marker1(board, [spec.search for spec in specs], location_code=1026201, frozen_list=False)
    board.close()
    assert result["evaluation"]["passed"] is None and result["evaluation"]["not_a_marker_result"] is True
    assert pipeline.marker1_lines(result)[0].startswith("NOT A MARKER RESULT")


def test_marker1_text_names_the_reader_and_its_settings_and_prints_the_number_with_the_filter_off(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    specs = [PullSpec(f"plumber search {i:02d}", 1026201) for i in range(4)]
    day_b = START + timedelta(hours=24)
    source = ReplaySource([p for spec in specs for moment in (START, day_b) for p in _replay(spec, moment, SITES)])
    pipeline.take_readings(board, source, specs, clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, specs, clock=clock)
    result = pipeline.marker1(board, [spec.search for spec in specs], location_code=1026201, frozen_list=False)
    board.close()
    report = result["report"]
    assert report["built_from_pulls"] is True and report["headline_beside_ablation"]["filter_off"] is not None
    lines = pipeline.marker1_lines(result)
    reader = report["reader"]
    named = [line for line in lines if line.startswith(f"Reader {reader['reader_version']}, settings:")]
    assert len(named) == 1 and f"min kept pulls {reader['min_kept_pulls']}" in named[0] and "altered-page filter on" in named[0]
    off = f"{100 * report['headline_beside_ablation']['filter_off']:.1f}%"
    assert any(line.startswith("With the altered-page filter switched off") and off in line for line in lines)
    assert not any(dash in line for line in lines for dash in DASHES)


def test_a_synthetic_report_uses_the_band_prior_stored_in_the_bundle_that_made_its_forecasts(demo_result, tmp_path):
    """report and marker commands give the same band prior the demo prints, read from the live bundle."""
    import shutil

    from aeo.ranksim.forecast.config import ForecastPaths

    workdir = Path(demo_result.workdir)
    shutil.copy(workdir / "scoreboard.db", tmp_path / "scoreboard.db")
    board = Scoreboard(f"sqlite:///{tmp_path / 'scoreboard.db'}")
    as_of = date.fromisoformat(demo_result.stage_reports["upload"]["as_of"])
    result = pipeline.stage_marker(board, source="synthetic", stage="upload", as_of=as_of, paths=ForecastPaths(workdir))
    board.close()
    report = result["report"]
    assert "band_prior" not in report["baselines_missing"]
    assert report["band_prior_source"].startswith(f"The band prior comes from bundle {pipeline.DEMO_BUNDLE}")
    assert not any("band prior by difficulty is missing" in note for note in result["evaluation"]["notes"])


# ---------------------------------------------------------------------------------------------
# Round mode: one reading per search and date, and the pull gap floor


def test_assemble_keeps_one_reading_per_search_and_date(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    day = START.date()

    def round_at(hour: int) -> None:
        clock.set(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour))
        pipeline.read_round(board, ReplaySource([_pull(spec, clock.now(), SITES, tag=f"-r{hour}")]), [spec], clock=clock)

    for hour in (10, 11, 12):
        round_at(hour)
    first = pipeline.assemble(board, day)
    assert first.readings_stored == 1
    for hour in (14, 19):
        round_at(hour)
    second = pipeline.assemble(board, day)
    assert second.readings_stored == 0 and second.newer_pulls_left_out == 1
    assert second.already_assembled[0]["reading_id"] == first.reading_ids[0]
    same_day = [r for r in pipeline.stored_readings(board, "plumber prices", location_code=1026201)
                if r.taken_at.startswith(day.isoformat())]
    assert len(same_day) == 1
    third = pipeline.assemble(board, day)
    assert third.readings_stored == 0 and third.readings_already_stored == 1
    board.close()


def test_assemble_counts_pulls_seconds_apart_as_one_pull(tmp_path):
    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    for second in range(5):
        clock.set(START + timedelta(seconds=second))
        pipeline.read_round(board, ReplaySource([_pull(spec, clock.now(), SITES, tag=f"-s{second}")]), [spec], clock=clock)
    summary = pipeline.assemble(board, START.date())
    assert summary.pulls_too_close == 4
    assert summary.readings_built - summary.degraded == 0  # one real pull is never a clean reading
    board.close()


def test_take_readings_refuses_a_gap_below_the_floor(tmp_path):
    board = _board(tmp_path, pipeline.VirtualClock(START))
    spec = PullSpec("plumber prices", 1026201)
    with pytest.raises(ValueError, match="at least 60 seconds"):
        pipeline.take_readings(board, ReplaySource(_replay(spec, START, SITES)), [spec], gap_seconds=1)
    assert pipeline.scoreboard_counts(board)["pulls"] == 0
    board.close()


# ---------------------------------------------------------------------------------------------
# Costs of searches with a Google operator


def test_reading_cost_with_specs_is_what_the_dataforseo_client_reserves():
    from aeo.ranksim.forecast.dataforseo import DataForSeoClient

    specs = pipeline.search_specs(["plumber site:example.com", "plumber prices"], 1026201)
    plan = pipeline.plan_reading_cost(None, "dataforseo", 2, pulls=5, specs=specs)
    reserved = sum(DataForSeoClient._serp_estimate(spec, "standard_normal", True) for spec in specs) * 5
    assert plan.usd == reserved
    assert any("Google operator" in note for note in plan.notes)
    one_round = pipeline.plan_round_cost(None, "dataforseo", 2, specs=specs)
    assert one_round.usd == reserved / 5
    sample = pipeline.plan_sample_serp(None, "dataforseo", 2, specs=specs)
    assert sample.usd == sum(DataForSeoClient._serp_estimate(spec, "standard_normal", True) for spec in specs)


# ---------------------------------------------------------------------------------------------
# The committed export anchors the scoreboard


def test_open_scoreboard_anchors_to_the_committed_export_and_catches_a_cut_tail(tmp_path, monkeypatch):
    committed = tmp_path / "committed_export"
    monkeypatch.setenv("RANKSIM_EXPORT_DIR", str(committed))
    paths = forecast_paths(tmp_path / "data")
    clock = pipeline.VirtualClock(datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc))
    board = pipeline.open_scoreboard(paths, clock=clock)
    assert board.heads_anchor is None  # no export yet
    pipeline.register_manifest(board, _manifest())
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    for search in ("plumber prices", "emergency plumber"):
        board.record_forecast(Forecast(site="seedone.example", search=search, stage="upload", as_of=clock.now().date(),
                                       point=12, range_low=8, range_high=20, label="contested", model_version="m1"))
    pipeline.export_scoreboard(board, committed)
    board.close()
    board = pipeline.open_scoreboard(paths, clock=clock)
    assert board.heads_anchor == committed and board.verify().ok
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forecasts_no_delete"))
        connection.execute(text("DELETE FROM forecasts WHERE seq = 2"))
    board.install_triggers()
    status = board.verify()
    assert not status.ok and any("Rows were deleted" in problem for problem in status.problems)
    assert pipeline.open_scoreboard(paths, clock=clock, export_dir=None).heads_anchor is None
    board.close()


# ---------------------------------------------------------------------------------------------
# Stage calendar


def test_stage_calendar_lists_stages_due_soon_and_stages_missed(tmp_path):
    clock = pipeline.VirtualClock(datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    pipeline.register_manifest(board, _manifest(launch=date(2026, 11, 30)))
    # 30 November launch: d7 7 Dec, d14 14 Dec, d30 30 Dec, d60 29 Jan (plan audit, dates section).
    soon = pipeline.stage_calendar(board, date(2026, 11, 28))
    assert [(item["stage"], item["stage_date"]) for item in soon["due"]] == [("upload", "2026-11-30")]
    assert soon["missed"] == []
    later = pipeline.stage_calendar(board, date(2026, 12, 28))
    assert [(item["stage"], item["stage_date"]) for item in later["due"]] == [("d30", "2026-12-30")]
    assert [(item["stage"], item["stage_date"]) for item in later["missed"]] == [
        ("upload", "2026-11-30"), ("d7", "2026-12-07"), ("d14", "2026-12-14")]
    board.close()


def _upload(board: Scoreboard, search: str, as_of: date) -> None:
    board.record_forecast(Forecast(site="seedone.example", search=search, stage="upload", as_of=as_of, point=12,
                                   range_low=8, range_high=20, label="contested", model_version="m1"))


def test_a_stage_stays_due_for_the_searches_without_a_forecast_and_is_missed_for_exactly_those(tmp_path):
    clock = pipeline.VirtualClock(datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    pipeline.register_manifest(board, _manifest(launch=date(2026, 11, 30)))
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    # The upload run on 27 November found a clean reading for one of the three searches only.
    _upload(board, "plumber prices", date(2026, 11, 27))
    on_launch = [i for i in pipeline.stage_calendar(board, date(2026, 11, 30))["due"] if i["stage"] == "upload"]
    assert len(on_launch) == 1 and on_launch[0]["searches_open"] == ["emergency plumber", "seedone plumbing"]
    assert on_launch[0]["searches_recorded"] == 1 and on_launch[0]["recorded"] is False
    missed = [i for i in pipeline.stage_calendar(board, date(2026, 12, 5))["missed"] if i["stage"] == "upload"]
    assert len(missed) == 1 and missed[0]["searches_open"] == ["emergency plumber", "seedone plumbing"]
    board.close()


def test_a_stage_with_every_search_forecast_is_neither_due_nor_missed(tmp_path):
    clock = pipeline.VirtualClock(datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    pipeline.register_manifest(board, _manifest(launch=date(2026, 11, 30)))
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    for search in ("plumber prices", "emergency plumber", "seedone plumbing"):
        _upload(board, search, date(2026, 11, 27))
    for day in (date(2026, 11, 30), date(2026, 12, 5)):
        calendar = pipeline.stage_calendar(board, day)
        assert not [i for i in calendar["due"] + calendar["missed"] if i["stage"] == "upload"]
    board.close()


# ---------------------------------------------------------------------------------------------
# Baselines of markers 3 to 5


def test_demo_stage_reports_carry_all_four_spec_baselines_and_say_where_they_came_from(demo_result):
    for stage, report in demo_result.stage_reports.items():
        assert report["baselines_missing"] == [], stage
        assert set(report["headline"]["baselines"]) >= {"always_nt50", "persistence", "band_prior", "site_strength"}
        notes = " ".join(report["baseline_notes"])
        assert "bundle's" in notes and "training cells" in notes and "site strength only baseline is the point" in notes
    evaluation = demo_result.marker_evaluations["4"]
    assert evaluation["passed"] is None and evaluation["label"] == SYNTHETIC  # synthetic never passes or fails
    assert not any("strength.py" in note for note in evaluation["notes"])


def test_demo_forecasts_come_from_the_live_path_and_the_bundle_knows_nothing_later(demo_result):
    training = demo_result.training
    assert training["label"] == SYNTHETIC and training["bundle"]["manifest"]["training_label"] == SYNTHETIC
    cutoff = date.fromisoformat(training["bundle"]["manifest"]["training_cutoff"])
    first_upload = min(date.fromisoformat(run["as_of"]) for run in demo_result.forecast_runs if run["stage"] == "upload")
    assert cutoff < first_upload
    assert {run["stage"] for run in demo_result.forecast_runs} == set(STAGE_DAYS)
    assert demo_result.forecasts_recorded + demo_result.forecasts_skipped == len(STAGE_DAYS) * 2 * pipeline.DEMO_TARGETS_PER_SITE
    assert not any(pipeline.PERSISTENCE_MODEL in line for line in demo_result.lines)
    assert any("through the live path" in line for line in demo_result.lines)


def test_band_prior_training_uses_training_cells_on_other_sites_only(tmp_path):
    launch = date(2026, 1, 6)
    clock = pipeline.VirtualClock(datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    cells, why = pipeline.band_prior_training(board, source="seed", stage="d30", as_of=date(2026, 6, 1))
    assert cells == [] and "no graded backtest d30 forecast of the training split" in why
    spec = PullSpec("plumber prices", 1026201)
    sites = {"seedone.example": "seed", "trainone.example": "backtest", "heldout.example": "backtest"}
    for site, source in sites.items():
        board.add_site(site, source=source, launch_date=launch, launch_date_source="test", built_by="test", niche="plumber")
        board.add_search(site, spec.search, location_code=1026201, difficulty="easy")
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    clock.set(datetime(2026, 2, 5, 9, 0, tzinfo=timezone.utc))  # day 30
    for site, split in (("seedone.example", None), ("trainone.example", "train"), ("heldout.example", "test")):
        board.record_forecast(Forecast(site=site, search=spec.search, stage="d30", as_of=date(2026, 2, 5), point=3,
                                       range_low=1, range_high=5, label="contested", model_version="m1",
                                       notes={"split": split} if split else {}))
    day90 = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    clock.set(day90)
    order = ["trainone.example", "heldout.example", "seedone.example"] + SITES
    pipeline.take_readings(board, ReplaySource(_replay(spec, day90, order)), [spec], clock=clock)
    clock.set(datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc))
    assert pipeline.grade(board, date(2026, 4, 7))["counts"]["graded"] == 3
    cells, why = pipeline.band_prior_training(board, source="seed", stage="d30", as_of=date(2026, 4, 7))
    assert [cell.site for cell in cells] == ["trainone.example"]  # the test split and the seed site are left out
    assert "fitted on 1 graded backtest d30 cells of the training split" in why
    result = pipeline.stage_marker(board, source="seed", stage="d30", as_of=date(2026, 4, 7))
    assert result["report"]["band_prior"] is not None
    assert "band_prior" not in result["report"]["baselines_missing"]
    assert result["report"]["baselines_missing"] == ["site_strength"]
    assert any("1 of 1 graded seed d30 forecasts carry no site strength only point" in note
               for note in result["baseline_notes"])
    board.close()


def _graded_board(tmp_path: Path, rows: list[tuple[str, str, str | None, dict]]) -> tuple[Scoreboard, date]:
    """A board with one d30 forecast per (site, source, split, notes) row, all graded at day 90."""
    launch = date(2026, 1, 6)
    clock = pipeline.VirtualClock(datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc))
    board = _board(tmp_path, clock)
    spec = PullSpec("plumber prices", 1026201)
    for site, source, _split, _notes in rows:
        board.add_site(site, source=source, launch_date=launch, launch_date_source="test", built_by="test", niche="plumber")
        board.add_search(site, spec.search, location_code=1026201, difficulty="easy")
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    clock.set(datetime(2026, 2, 5, 9, 0, tzinfo=timezone.utc))
    for site, _source, split, notes in rows:
        board.record_forecast(Forecast(site=site, search=spec.search, stage="d30", as_of=date(2026, 2, 5), point=3,
                                       range_low=1, range_high=5, label="contested", model_version="m1",
                                       notes={**({"split": split} if split else {}), **notes}))
    day90 = datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc)
    clock.set(day90)
    pipeline.take_readings(board, ReplaySource(_replay(spec, day90, [r[0] for r in rows] + SITES)), [spec], clock=clock)
    clock.set(datetime(2026, 4, 7, 9, 0, tzinfo=timezone.utc))
    assert pipeline.grade(board, date(2026, 4, 7))["counts"]["graded"] == len(rows)
    return board, date(2026, 4, 7)


def test_band_prior_of_a_backtest_report_keeps_training_cells_and_drops_only_held_out_sites(tmp_path):
    board, as_of = _graded_board(tmp_path, [("trainone.example", "backtest", "train", {}),
                                            ("heldout.example", "backtest", "test", {})])
    cells, why = pipeline.band_prior_training(board, source="backtest", stage="d30", as_of=as_of)
    assert [cell.site for cell in cells] == ["trainone.example"], why  # a training site is never taken as graded
    board.close()


def test_stage_marker_takes_the_site_strength_baseline_each_forecast_stored(tmp_path):
    board, as_of = _graded_board(tmp_path, [("seedone.example", "seed", None, {"baselines": {"site_strength": 51}}),
                                            ("seedtwo.example", "seed", None, {"baselines": {"site_strength": 2}})])
    result = pipeline.stage_marker(board, source="seed", stage="d30", as_of=as_of)
    assert "site_strength" not in result["report"]["baselines_missing"]
    assert "site_strength" in result["report"]["headline"]["baselines"]
    assert any("on all 2 graded forecasts" in note for note in result["baseline_notes"])
    board.close()


def test_a_site_strength_point_missing_on_one_forecast_leaves_the_baseline_missing_with_the_count(tmp_path):
    board, as_of = _graded_board(tmp_path, [("seedone.example", "seed", None, {"baselines": {"site_strength": 51}}),
                                            ("seedtwo.example", "seed", None, {})])
    points, why = pipeline.site_strength_points(board, source="seed", stage="d30", as_of=as_of)
    assert points is None and "1 of 2 graded seed d30 forecasts carry no site strength only point" in why
    board.close()


def test_a_dataforseo_sandbox_source_in_any_mode_is_labelled_synthetic():
    class Named:
        def __init__(self, name: str) -> None:
            self.name = name

    assert pipeline.source_label(Named("dataforseo-sandbox")) == SYNTHETIC
    assert pipeline.source_label(Named("dataforseo-sandbox-live")) == SYNTHETIC
    assert pipeline.source_label(Named("synthetic")) == SYNTHETIC
    assert pipeline.source_label(Named("dataforseo")) == "REAL"
    assert pipeline.source_label(Named("dataforseo-live")) == "REAL"


def test_read_round_stores_the_paid_pulls_of_a_round_that_failed_part_way(tmp_path, monkeypatch):
    from aeo.ranksim.forecast.reader import RoundFailed

    clock = pipeline.VirtualClock(START)
    board = _board(tmp_path, clock)
    spec_a, spec_b = PullSpec("plumber prices", 1026201), PullSpec("emergency plumber", 1026201)
    paid = _pull(spec_a, START, SITES)
    unsent = _pull(spec_b, START, SITES, ok=False)

    def failing(source, specs, now):
        raise RoundFailed(0, "the budget guard refused the second task_post.", "2026-01-05T09:00:00Z", pulls=[paid, unsent])

    monkeypatch.setattr(pipeline, "fetch_round", failing)
    summary = pipeline.read_round(board, ReplaySource([]), [spec_a, spec_b], clock=clock)
    assert summary.failed and summary.pulls_returned == 2 and summary.pulls_ok == 1
    assert board.pull(paid.pull_id).ok and not board.pull(unsent.pull_id).ok
    record = next(row for row in board.freezes() if row["name"] == summary.freeze_name)
    assert record["payload"]["failed"].startswith("the budget guard") and sorted(record["payload"]["pull_ids"]) == sorted(
        [paid.pull_id, unsent.pull_id])
    board.close()


# ---------------------------------------------------------------------------------------------
# SmartCrew files are refused by name and by content


def test_a_smartcrew_search_console_export_is_refused_whatever_site_is_typed(tmp_path):
    import zipfile

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    export = downloads / "smartcrewhq.com-Performance-on-Search-2026-11-21.zip"
    with zipfile.ZipFile(export, "w") as archive:
        archive.writestr("Dates.csv", "Date,Clicks,Impressions,CTR,Position\n2026-11-20,3,120,2.5%,14.2\n")
    paths = forecast_paths(tmp_path / "data")
    board = _board(tmp_path, pipeline.VirtualClock(START))
    with pytest.raises(ValueError, match="SmartCrew"):
        pipeline.import_gsc(paths, board, site="one.com", file=export, kind="ui", fetched_at=START)
    other = downloads / "two.com-Performance-on-Search-2026-11-21.zip"
    other.write_bytes(export.read_bytes())
    with pytest.raises(ValueError, match="property two.com, not of one.com"):
        pipeline.import_gsc(paths, board, site="one.com", file=other, kind="ui", fetched_at=START)
    named = downloads / "export.zip"
    with zipfile.ZipFile(named, "w") as archive:
        archive.writestr("Dates.csv", "Date,Clicks,Impressions,CTR,Position\n2026-11-20,3,120,2.5%,14.2\n")
        archive.writestr("Queries.csv", "Top queries,Clicks,Impressions,CTR,Position\nsmartcrew software,4,210,1.9%,3.1\n")
    with pytest.raises(ValueError, match="naming SmartCrew"):
        pipeline.import_gsc(paths, board, site="one.com", file=named, kind="ui", fetched_at=START)
    assert not board.freezes() and not (paths.imports / "gsc").exists()
    board.close()
