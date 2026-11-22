"""The measuring pipeline (forecast/pipeline.py). No network, no keys, no real sleeping.

Everything built here is SYNTHETIC or a hand-made fixture. Nothing is accuracy.
"""

from __future__ import annotations

import json
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
        assert report["model_version"] == pipeline.PERSISTENCE_MODEL
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
    pipeline.register_manifest(board, manifest)
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
    assert report["headline"]["rate"] == 1.0 and report["built_from_pulls"] is True
    assert result["evaluation"]["passed"] is None  # 10 units is under the minimum count
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
    source = ReplaySource(_replay(spec, START, SITES) + _replay(spec, day_b, SITES))
    pipeline.take_readings(board, source, [spec], clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, [spec], clock=clock)
    rebuilt = pipeline.marker1(board, ["plumber prices"], location_code=1026201, reader_version=1)
    assert rebuilt["report"]["built_from_pulls"] is True
    assert rebuilt["report"]["reader"]["reader_version"] == "reader-v1"
    assert rebuilt["report"]["headline_beside_ablation"]["filter_off"] is not None
    # Without stored pulls the report reads the stored readings and still names the reader settings.
    bare = Scoreboard(f"sqlite:///{tmp_path / 'bare.db'}", clock=clock.now)
    for reading_id in rebuilt["pairs"][0]:
        bare.add_reading(board.reading(reading_id))
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
