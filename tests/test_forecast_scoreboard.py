"""The scoreboard, the nightly grading job and the stage report.

Everything here runs on a temporary SQLite file with an injected clock. No
network, no keys, no wall clock. The positions are made up for the tests and
the sites are example domains.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aeo.ranksim.forecast import grade_job
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.grading import GradingRule
from aeo.ranksim.forecast.markers import evaluate_marker
from aeo.ranksim.forecast.report import combined_report, render_text, stage_report
from aeo.ranksim.forecast.scoreboard import (
    TRIGGERS,
    ChainBroken,
    PoolingRefused,
    Scoreboard,
    ScoreboardRefusal,
)
from aeo.ranksim.forecast.types import CleanReading, Forecast, OrganicHit, Pull, PullSpec, SitePosition

LAUNCH = date(2026, 11, 30)
LOC = 1026201  # an arbitrary pinned city code for the tests
SEARCH = "roof repair austin"


class Clock:
    """An injected UTC clock."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def at_day(self, day: int, hour: int = 12) -> None:
        self.moment = datetime(LAUNCH.year, LAUNCH.month, LAUNCH.day, hour, tzinfo=timezone.utc) + timedelta(days=day)


def utc_day(day: int, hour: int = 10) -> str:
    moment = datetime(LAUNCH.year, LAUNCH.month, LAUNCH.day, hour, tzinfo=timezone.utc) + timedelta(days=day)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def reading(
    rid: str,
    day: int,
    positions: dict[str, int],
    *,
    search: str = SEARCH,
    degraded: bool = False,
    location: int = LOC,
    device: str = "desktop",
    pull_ids: tuple[str, ...] = (),
    dropped: tuple[tuple[str, str], ...] = (),
    label: str = "REAL",
    hour: int = 10,
) -> CleanReading:
    return CleanReading(
        reading_id=rid,
        search=search,
        location_code=location,
        device=device,
        taken_at=utc_day(day, hour),
        pulls_total=5,
        pulls_kept=2 if degraded else 5,
        dropped=dropped,
        degraded=degraded,
        pulls_not_independent=False,
        positions=tuple(SitePosition(site, pos, (pos,) * 5) for site, pos in positions.items()),
        pull_ids=pull_ids,
        label=label,
    )


def forecast(stage: str = "d60", day: int = 60, point: int = 5, *, site: str = "alpha.com", search: str = SEARCH,
             model: str = "m1", label: str = "contested", notes: dict | None = None) -> Forecast:
    return Forecast(
        site=site,
        search=search,
        stage=stage,
        as_of=LAUNCH + timedelta(days=day),
        point=point,
        range_low=max(1, point - 3) if point != NT50 else 40,
        range_high=min(NT50, point + 3),
        label=label,
        model_version=model,
        notes=notes or {},
    )


@pytest.fixture()
def clock() -> Clock:
    return Clock(datetime(2026, 11, 20, 9, tzinfo=timezone.utc))


@pytest.fixture()
def board(tmp_path: Path, clock: Clock) -> Scoreboard:
    sb = Scoreboard(f"sqlite:///{tmp_path / 'scoreboard.db'}", clock=clock)
    sb.add_site(
        "alpha.com",
        source="seed",
        launch_date=LAUNCH,
        launch_date_source="seed manifest",
        built_by="ranksim",
        niche="roofing",
        brand_terms=("Alpha Roofing",),
    )
    sb.add_search("alpha.com", SEARCH, location_code=LOC, difficulty="easy", target_url="https://alpha.com/roof-repair")
    sb.add_search("alpha.com", "alpha roofing reviews", location_code=LOC, difficulty="easy")
    sb.register_model_version("m1", description="test model", artefact_sha256="0" * 64)
    yield sb
    sb.close()


def made_at(board: Scoreboard, clock: Clock, fc: Forecast, day: int, **kwargs) -> str:
    clock.at_day(day)
    return board.record_forecast(fc, **kwargs).forecast_id


# ---------------------------------------------------------------------------------------------
# storage


def test_database_uses_wal_and_has_every_table(board: Scoreboard):
    with board.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        names = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"sites", "searches", "pulls", "readings", "reading_positions", "forecasts", "grades",
            "core_updates", "freezes", "model_versions"} <= names
    assert set(TRIGGERS) <= set(board.triggers_present())


def test_url_comes_from_config_when_not_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock):
    monkeypatch.setenv("RANKSIM_SCOREBOARD_URL", f"sqlite:///{tmp_path / 'env.db'}")
    sb = Scoreboard(clock=clock)
    assert sb.url.endswith("env.db") and (tmp_path / "env.db").exists()
    sb.close()


def test_brand_flag_comes_from_brand_terms(board: Scoreboard, clock: Clock):
    brand = made_at(board, clock, forecast(search="alpha roofing reviews", point=1), 60)
    plain = made_at(board, clock, forecast(), 60)
    assert board.forecast(brand).is_brand is True
    assert board.forecast(plain).is_brand is False


def test_excluded_client_site_is_refused_and_its_positions_dropped(board: Scoreboard):
    with pytest.raises(ScoreboardRefusal, match="excluded"):
        board.add_site("smartcrewhq.com", source="pilot", launch_date=LAUNCH, launch_date_source="x", built_by="agency")
    board.add_reading(reading("r-x", 10, {"smartcrewhq.com": 3, "beta.com": 4}))
    stored = board.reading("r-x")
    assert [p.site for p in stored.positions] == ["beta.com"]


def test_site_must_be_registrable_domain(board: Scoreboard):
    with pytest.raises(ScoreboardRefusal, match="registrable"):
        board.add_site("www.gamma.com", source="seed", launch_date=LAUNCH, launch_date_source="x", built_by="ranksim")
    with pytest.raises(ScoreboardRefusal, match="source"):
        board.add_site("gamma.com", source="everything", launch_date=LAUNCH, launch_date_source="x", built_by="ranksim")


# ---------------------------------------------------------------------------------------------
# forecast refusals


def test_forecast_for_unknown_site_or_search_is_refused(board: Scoreboard, clock: Clock):
    clock.at_day(60)
    with pytest.raises(ScoreboardRefusal, match="site 'nobody.com' is not registered"):
        board.record_forecast(forecast(site="nobody.com"))
    with pytest.raises(ScoreboardRefusal, match="search 'gutter repair' is not registered"):
        board.record_forecast(forecast(search="gutter repair"))


def test_forecast_with_unknown_stage_is_refused(board: Scoreboard, clock: Clock):
    clock.at_day(45)
    with pytest.raises(ScoreboardRefusal, match="stage"):
        board.record_forecast(forecast(stage="d45", day=45))


def test_forecast_dated_after_the_day_it_is_made_is_refused(board: Scoreboard, clock: Clock):
    clock.at_day(59, hour=23)
    with pytest.raises(ScoreboardRefusal, match="later than the day it is made"):
        board.record_forecast(forecast(day=60))
    clock.at_day(60, hour=0)
    assert board.record_forecast(forecast(day=60)).as_of == LAUNCH + timedelta(days=60)


def test_forecast_using_a_reading_taken_after_its_date_is_refused(board: Scoreboard, clock: Clock):
    board.add_reading(reading("r59", 59, {"alpha.com": 9}))
    board.add_reading(reading("r61", 61, {"alpha.com": 7}))
    clock.at_day(62)
    with pytest.raises(ScoreboardRefusal, match="after the forecast date"):
        board.record_forecast(forecast(day=60), input_reading_ids=["r59", "r61"])
    with pytest.raises(ScoreboardRefusal, match="not stored"):
        board.record_forecast(forecast(day=60), input_reading_ids=["missing"])
    record = board.record_forecast(forecast(day=60), input_reading_ids=["r59"])
    assert record.input_reading_ids == ("r59",)


def test_reading_on_the_forecast_date_but_after_it_was_made_is_refused(board: Scoreboard, clock: Clock):
    board.add_reading(reading("r60-late", 60, {"alpha.com": 9}, hour=18))
    clock.at_day(60, hour=12)
    with pytest.raises(ScoreboardRefusal, match="taken after this forecast is made"):
        board.record_forecast(forecast(day=60), input_reading_ids=["r60-late"])


def test_second_forecast_for_same_cell_stage_and_model_is_refused(board: Scoreboard, clock: Clock):
    made_at(board, clock, forecast(point=5), 60)
    with pytest.raises(ScoreboardRefusal, match="already exists"):
        made_at(board, clock, forecast(point=8), 60)
    board.register_model_version("m2", description="second model")
    assert made_at(board, clock, forecast(point=8, model="m2"), 60)


@pytest.mark.parametrize("point", [0, 52, -3, True, "5", 5.0])
def test_point_outside_1_to_50_or_nt50_is_refused(board: Scoreboard, clock: Clock, point):
    clock.at_day(60)
    bad = Forecast("alpha.com", SEARCH, "d60", LAUNCH + timedelta(days=60), point, 1, NT50, "contested", "m1")
    with pytest.raises(ScoreboardRefusal, match="point"):
        board.record_forecast(bad)


def test_nt50_and_edge_points_are_accepted(board: Scoreboard, clock: Clock):
    board.register_model_version("m2", description="x")
    board.register_model_version("m3", description="x")
    assert made_at(board, clock, forecast(point=NT50), 60)
    assert made_at(board, clock, forecast(point=1, model="m2"), 60)
    assert made_at(board, clock, forecast(point=50, model="m3"), 60)


def test_upload_forecast_dated_after_launch_is_refused(board: Scoreboard, clock: Clock):
    clock.at_day(1)
    with pytest.raises(ScoreboardRefusal, match="on or before launch"):
        board.record_forecast(forecast(stage="upload", day=1))
    record = board.record_forecast(forecast(stage="upload", day=0))
    assert record.lead_days == 90 and record.outcome_due_date == LAUNCH + timedelta(days=90)


def test_unfrozen_or_late_frozen_model_is_refused(board: Scoreboard, clock: Clock):
    clock.at_day(60)
    with pytest.raises(ScoreboardRefusal, match="not frozen"):
        board.record_forecast(forecast(model="never"))
    board.register_model_version("late", description="x", frozen_at=datetime(2027, 3, 1, tzinfo=timezone.utc))
    with pytest.raises(ScoreboardRefusal, match="frozen after"):
        board.record_forecast(forecast(model="late"))


def test_backdated_clock_cannot_extend_the_chain(board: Scoreboard, clock: Clock):
    board.register_model_version("m2", description="x", frozen_at=datetime(2026, 11, 1, tzinfo=timezone.utc))
    made_at(board, clock, forecast(), 60)
    clock.at_day(60, hour=1)
    with pytest.raises(ScoreboardRefusal, match="clock is behind"):
        board.record_forecast(forecast(model="m2"))


def test_forecast_row_stores_the_contract_fields(board: Scoreboard, clock: Clock):
    board.add_reading(reading("r55", 55, {"alpha.com": 12}))
    fid = made_at(board, clock, forecast(), 60, input_reading_ids=["r55"], code_commit="abc123")
    with board.engine.connect() as connection:
        row = connection.execute(text("SELECT * FROM forecasts WHERE forecast_id = :f"), {"f": fid}).mappings().one()
    assert row["label_text"] == "PREDICTED" and row["kind"] == "PREDICTED"
    assert row["source"] == "seed" and row["launch_date"] == "2026-11-30"
    assert row["outcome_due_date"] == "2027-02-28" and row["lead_days"] == 30
    assert row["grading_rule_version"] == "v1" and len(row["grading_rule_hash"]) == 64
    assert row["code_commit"] == "abc123" and row["created_at_utc"] == "2027-01-29T12:00:00Z"
    assert row["target_url"] == "https://alpha.com/roof-repair"
    assert row["location_code"] == LOC and row["device"] == "desktop"


# ---------------------------------------------------------------------------------------------
# triggers and the chain


def _graded_board(board: Scoreboard, clock: Clock) -> str:
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 6}))
    clock.at_day(91)
    board.grade(fid, "r90")
    return fid


def test_update_and_delete_of_forecasts_and_grades_fail(board: Scoreboard, clock: Clock):
    _graded_board(board, clock)
    board.freeze("markers", {"m": 1})
    for sql in (
        "UPDATE forecasts SET point = 9",
        "DELETE FROM forecasts",
        "UPDATE grades SET hit = 0",
        "DELETE FROM grades",
        "UPDATE readings SET degraded = 1",
        "DELETE FROM reading_positions",
        "UPDATE freezes SET sha256 = 'x'",
        "DELETE FROM model_versions",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            with board.engine.begin() as connection:
                connection.execute(text(sql))
    assert board.verify().ok


def test_core_update_rows_only_accept_one_end_date(board: Scoreboard, clock: Clock):
    board.record_core_update("March core update", kind="core", started_on="2027-02-20",
                             source_url="https://status.search.google.com/incidents/x")
    board.end_core_update("March core update", started_on="2027-02-20", ended_on="2027-03-05")
    with pytest.raises(ScoreboardRefusal, match="already has an end"):
        board.end_core_update("March core update", started_on="2027-02-20", ended_on="2027-03-06")
    with pytest.raises(DBAPIError):
        with board.engine.begin() as connection:
            connection.execute(text("UPDATE core_updates SET ended_on = '2027-04-01'"))
    with pytest.raises(DBAPIError):
        with board.engine.begin() as connection:
            connection.execute(text("DELETE FROM core_updates"))


def test_tampered_forecast_breaks_verify_and_every_report_refuses(board: Scoreboard, clock: Clock, tmp_path: Path):
    _graded_board(board, clock)
    assert board.verify().ok
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forecasts_no_update"))
        connection.execute(text("UPDATE forecasts SET point = 7 WHERE seq = 1"))
    board.install_triggers()
    status = board.verify()
    assert status.ok is False and status.forecasts_ok is False and status.triggers_ok is True
    with pytest.raises(ChainBroken):
        stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19))
    with pytest.raises(ChainBroken):
        grade_job.run(board, date(2027, 3, 19))
    with pytest.raises(ChainBroken):
        board.export(tmp_path / "export")


def test_tampered_grade_breaks_verify(board: Scoreboard, clock: Clock):
    _graded_board(board, clock)
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER grades_no_update"))
        connection.execute(text("UPDATE grades SET outcome_position = 3"))
    status = board.verify()
    assert status.grades_ok is False and status.forecasts_ok is True
    assert status.triggers_ok is False  # the dropped trigger is reported too
    with pytest.raises(ChainBroken):
        stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19))


def test_dropped_trigger_alone_fails_verify(board: Scoreboard):
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER grades_no_delete"))
    status = board.verify()
    assert status.ok is False and "grades_no_delete" in " ".join(status.problems)


def test_tampered_forecast_is_not_graded(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 6}))
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forecasts_no_update"))
        connection.execute(text("UPDATE forecasts SET point = 6"))
    clock.at_day(91)
    with pytest.raises(ChainBroken):
        board.grade(fid, "r90")


def test_export_then_import_round_trip_keeps_heads_and_bytes(board: Scoreboard, clock: Clock, tmp_path: Path):
    _graded_board(board, clock)
    board.add_pull(_pull("p1", [("https://alpha.com/roof-repair", 4)]))
    board.record_core_update("u1", kind="core", started_on="2027-02-20", source_url="https://status.search.google.com/u1")
    board.freeze("grading_rule_v1", {"version": "v1"})
    heads = board.export(tmp_path / "a")
    again = board.export(tmp_path / "b")
    assert heads == again
    for name in heads["files"]:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert (tmp_path / "a" / "HEADS.json").read_bytes() == (tmp_path / "b" / "HEADS.json").read_bytes()

    rebuilt = Scoreboard(f"sqlite:///{tmp_path / 'rebuilt.db'}", clock=clock)
    status = rebuilt.import_export(tmp_path / "a")
    assert status.ok
    assert status.forecasts_head == heads["forecasts"]["head"] and status.grades_head == heads["grades"]["head"]
    assert rebuilt.export(tmp_path / "c") == heads
    with pytest.raises(ScoreboardRefusal, match="empty"):
        rebuilt.import_export(tmp_path / "a")
    rebuilt.close()


def test_import_refuses_an_edited_export(board: Scoreboard, clock: Clock, tmp_path: Path):
    _graded_board(board, clock)
    board.export(tmp_path / "a")
    path = tmp_path / "a" / "forecasts.jsonl"
    path.write_text(path.read_text().replace('"point":5', '"point":6'))
    fresh = Scoreboard(f"sqlite:///{tmp_path / 'fresh.db'}", clock=clock)
    with pytest.raises(ChainBroken):
        fresh.import_export(tmp_path / "a")
    assert fresh.forecasts() == []
    fresh.close()


def test_freeze_is_idempotent_and_refuses_a_change(board: Scoreboard):
    first = board.freeze("markers", {"m": 1})
    assert board.freeze("markers", {"m": 1}) == first
    with pytest.raises(ScoreboardRefusal, match="different payload"):
        board.freeze("markers", {"m": 2})


# ---------------------------------------------------------------------------------------------
# grading


def test_grade_refuses_a_degraded_reading(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("bad", 90, {"alpha.com": 6}, degraded=True))
    clock.at_day(91)
    with pytest.raises(ScoreboardRefusal, match="degraded"):
        board.grade(fid, "bad")


@pytest.mark.parametrize("day, allowed", [(86, False), (87, True), (93, True), (94, False)])
def test_grading_window_edges(board: Scoreboard, clock: Clock, day: int, allowed: bool):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading(f"r{day}", day, {"alpha.com": 6}))
    clock.at_day(95)
    if allowed:
        grade = board.grade(fid, f"r{day}")
        assert grade.days_after_launch == day and grade.hit is True
    else:
        with pytest.raises(ScoreboardRefusal, match="outcome window"):
            board.grade(fid, f"r{day}")


def test_grade_refuses_another_search_location_or_device(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("other-search", 90, {"alpha.com": 6}, search="gutter repair"))
    board.add_reading(reading("other-city", 90, {"alpha.com": 6}, location=2840))
    board.add_reading(reading("mobile", 90, {"alpha.com": 6}, device="mobile"))
    clock.at_day(91)
    with pytest.raises(ScoreboardRefusal, match="for the search"):
        board.grade(fid, "other-search")
    with pytest.raises(ScoreboardRefusal, match="location"):
        board.grade(fid, "other-city")
    with pytest.raises(ScoreboardRefusal, match="location"):
        board.grade(fid, "mobile")


def test_search_match_ignores_case_and_spacing(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("caps", 90, {"alpha.com": 6}, search="  Roof   Repair AUSTIN "))
    clock.at_day(91)
    assert board.grade(fid, "caps").outcome_position == 6


def test_grade_refuses_a_forecast_made_at_or_after_the_reading(board: Scoreboard, clock: Clock):
    board.add_reading(reading("early", 88, {"alpha.com": 6}, hour=10))
    board.add_reading(reading("same-second", 89, {"alpha.com": 6}, hour=12))
    clock.at_day(88, hour=11)
    late = board.record_forecast(forecast(day=88))
    clock.at_day(89, hour=12)
    board.register_model_version("m2", description="x")
    same = board.record_forecast(forecast(day=89, model="m2"))
    clock.at_day(95)
    with pytest.raises(ScoreboardRefusal, match="at or after"):
        board.grade(late.forecast_id, "early")
    with pytest.raises(ScoreboardRefusal, match="at or after"):
        board.grade(same.forecast_id, "same-second")


def test_synthetic_reading_cannot_grade_a_seed_forecast(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("synth", 90, {"alpha.com": 6}, label="SYNTHETIC"))
    clock.at_day(91)
    with pytest.raises(ScoreboardRefusal, match="SYNTHETIC"):
        board.grade(fid, "synth")


def test_absent_site_is_nt50_and_second_original_is_refused(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=NT50), 60)
    board.add_reading(reading("r90", 90, {"beta.com": 1}))
    clock.at_day(91)
    grade = board.grade(fid, "r90")
    assert grade.outcome_position == NT50 and grade.hit is True and grade.label_text == "REAL"
    with pytest.raises(ScoreboardRefusal, match="already has its original"):
        board.grade(fid, "r90")


def _pull(pid: str, hits: list[tuple[str, int]], *, ok: bool = True) -> Pull:
    return Pull(
        pull_id=pid,
        provider="dataforseo",
        endpoint="task_get/advanced",
        spec=PullSpec(search=SEARCH, location_code=LOC),
        requested_at=utc_day(90, 8),
        completed_at=utc_day(90, 9),
        ok=ok,
        status_code=20000 if ok else 40501,
        hits=tuple(OrganicHit(rank=rank, url=url, domain=url.split("/")[2], site=url.split("/")[2]) for url, rank in hits),
    )


def test_target_url_position_is_its_best_rank_over_kept_pulls(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=4), 60)
    board.add_pull(_pull("p1", [("https://alpha.com/", 2), ("https://alpha.com/roof-repair/", 6)]))
    board.add_pull(_pull("p2", [("https://alpha.com/roof-repair", 4)]))
    board.add_pull(_pull("p3", [("https://alpha.com/roof-repair", 1)]))
    board.add_reading(reading("r90", 90, {"alpha.com": 2}, pull_ids=("p1", "p2", "p3"), dropped=(("p3", "poisoned"),)))
    clock.at_day(91)
    grade = board.grade(fid, "r90")
    assert grade.outcome_position == 2  # the site's clean position grades the forecast
    assert grade.target_url_position == 4  # p3 was dropped, so its rank 1 does not count
    assert grade.target_url_hit is True


def test_target_url_position_is_none_when_pulls_are_missing(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=4), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 2}, pull_ids=("not-stored",)))
    clock.at_day(91)
    grade = board.grade(fid, "r90")
    assert grade.target_url_position is None and grade.target_url_hit is None


def test_target_url_absent_from_every_kept_pull_is_nt50(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=4), 60)
    board.add_pull(_pull("p1", [("https://alpha.com/", 2)]))
    board.add_reading(reading("r90", 90, {"alpha.com": 2}, pull_ids=("p1",)))
    clock.at_day(91)
    assert board.grade(fid, "r90").target_url_position == NT50


def test_regrade_needs_a_reason_and_sits_beside_the_original(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=10), 60)
    other = board.record_forecast(forecast(stage="d30", day=30)).forecast_id
    board.add_reading(reading("r89", 89, {"alpha.com": 16}))
    board.add_reading(reading("r92", 92, {"alpha.com": 11}))
    clock.at_day(94)
    with pytest.raises(ScoreboardRefusal, match="no original"):
        board.grade(fid, "r92", kind="regrade", reason="core update")
    original = board.grade(fid, "r89")
    assert original.hit is False  # real 16 allows 5 places and 10 is 6 away
    with pytest.raises(ScoreboardRefusal, match="needs a reason"):
        board.grade(fid, "r92", kind="regrade")
    wider = GradingRule(version="v2", tolerance_1_10=3)
    regrade = board.grade(fid, "r92", kind="regrade", reason="regraded after two calm weeks", rule=wider)
    assert regrade.kind == "regrade" and regrade.grading_rule_version == "v2"
    with pytest.raises(ScoreboardRefusal, match="already stored"):
        board.grade(fid, "r92", kind="regrade", reason="again", rule=wider)
    kinds = [g.kind for g in board.grades_for(fid)]
    assert kinds == ["original", "regrade"]
    originals = board.graded_records("seed", "d60", kind="original")
    latest = board.graded_records("seed", "d60", kind="latest")
    assert originals[0].cell.actual == 16 and latest[0].cell.actual == 11 and latest[0].hit is True
    with pytest.raises(ScoreboardRefusal, match="original grade must use"):
        board.grade(other, "r92", rule=wider)


def test_graded_cells_fill_every_report_field(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=7, label="locked"), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 8}))
    clock.at_day(91)
    board.grade(fid, "r90")
    (cell,) = board.graded_cells("seed", "d60")
    assert (cell.site, cell.point, cell.actual, cell.is_brand) == ("alpha.com", 7, 8, False)
    assert (cell.difficulty, cell.label, cell.lead_days, cell.niche) == ("easy", "locked", 30, "roofing")
    assert (cell.range_low, cell.range_high, cell.source, cell.stage) == (4, 10, "seed", "d60")
    assert board.graded_cells("seed", "d60", as_of=date(2027, 2, 27)) == []
    with pytest.raises(PoolingRefused):
        board.graded_cells("all", "d60")


def test_graded_cells_refuse_mixed_model_versions(board: Scoreboard, clock: Clock):
    made_at(board, clock, forecast(), 60)
    board.register_model_version("m2", description="x")
    made_at(board, clock, forecast(model="m2"), 60)
    with pytest.raises(ScoreboardRefusal, match="model versions"):
        board.graded_cells("seed", "d60")
    assert board.graded_cells("seed", "d60", model_version="m2") == []


# ---------------------------------------------------------------------------------------------
# persistence and eligibility


def test_persistence_uses_the_latest_good_reading_on_or_before_as_of(board: Scoreboard, clock: Clock):
    board.add_reading(reading("r40", 40, {"alpha.com": 30}))
    board.add_reading(reading("r55", 55, {"alpha.com": 14}))
    board.add_reading(reading("r58-bad", 58, {"alpha.com": 2}, degraded=True))
    board.add_reading(reading("r60", 60, {"alpha.com": 9}, hour=23))  # same day as as_of: allowed
    board.add_reading(reading("r61", 61, {"alpha.com": 1}))  # after as_of: never used
    board.add_reading(reading("r59-city", 59, {"alpha.com": 3}, location=2840))
    fid = made_at(board, clock, forecast(), 62)
    assert board.persistence_points([fid]) == [9]


def test_persistence_is_nt50_without_an_earlier_reading_or_when_absent(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(stage="upload", day=0), 0)
    assert board.persistence_points([fid]) == [NT50]
    board.add_reading(reading("r10", 10, {"beta.com": 1}))
    other = made_at(board, clock, forecast(stage="d14", day=14), 14)
    assert board.persistence_points([other]) == [NT50]


def test_eligibility_counts_by_source_and_stage(board: Scoreboard, clock: Clock):
    made_at(board, clock, forecast(stage="upload", day=0), 0)
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 6}))
    clock.at_day(91)
    board.grade(fid, "r90")
    table = board.eligibility(LAUNCH + timedelta(days=91))
    assert table["seed"]["d60"] == {"made": 1, "due": 1, "graded": 1, "due_not_graded": 0, "not_yet_due": 0,
                                    "next_due_date": None}
    assert table["seed"]["upload"]["due"] == 1 and table["seed"]["upload"]["due_not_graded"] == 1
    early = board.eligibility(LAUNCH + timedelta(days=61))
    assert early["seed"]["d60"]["not_yet_due"] == 1 and early["seed"]["d60"]["next_due_date"] == "2027-02-28"
    assert early["pilot"]["d60"]["made"] == 0 and early["backtest"]["d30"]["graded"] == 0


# ---------------------------------------------------------------------------------------------
# the nightly job


def test_job_picks_the_reading_closest_to_day_90(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r88", 88, {"alpha.com": 20}))
    board.add_reading(reading("r91", 91, {"alpha.com": 6}))
    board.add_reading(reading("r87", 87, {"alpha.com": 30}))
    clock.at_day(95)
    summary = grade_job.run(board, LAUNCH + timedelta(days=95))
    assert summary["graded"] == [fid]
    assert board.grades_for(fid)[0].reading_id == "r91"


def test_job_tie_goes_to_the_earlier_reading(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r91", 91, {"alpha.com": 6}))
    board.add_reading(reading("r89", 89, {"alpha.com": 7}))
    clock.at_day(95)
    grade_job.run(board, LAUNCH + timedelta(days=95))
    assert board.grades_for(fid)[0].reading_id == "r89"


def test_job_waits_while_a_closer_reading_can_still_arrive(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r88", 88, {"alpha.com": 6}))
    clock.at_day(91)
    first = grade_job.run(board, LAUNCH + timedelta(days=91))
    assert first["waiting_for_closer_reading"] == [fid] and first["graded"] == []
    board.add_reading(reading("r91", 91, {"alpha.com": 9}))
    second = grade_job.run(board, LAUNCH + timedelta(days=91))
    assert second["graded"] == [fid]
    assert board.grades_for(fid)[0].reading_id == "r91"


def test_job_grades_a_day_88_reading_once_no_closer_one_can_come(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r88", 88, {"alpha.com": 6}))
    clock.at_day(92)
    assert grade_job.run(board, LAUNCH + timedelta(days=92))["graded"] == [fid]


def test_job_ignores_readings_dated_after_today(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(), 60)
    board.add_reading(reading("r91", 91, {"alpha.com": 6}))
    clock.at_day(89)
    assert grade_job.run(board, LAUNCH + timedelta(days=89))["not_yet_due"] == [fid]
    summary = grade_job.run(board, LAUNCH + timedelta(days=90))
    assert summary["due_no_reading_in_window"] == [fid] and summary["window_closed_no_reading"] == []
    assert grade_job.run(board, LAUNCH + timedelta(days=91))["graded"] == [fid]


def test_job_is_idempotent_and_reports_every_reason(board: Scoreboard, clock: Clock, tmp_path: Path):
    board.add_search("alpha.com", "gutter repair", location_code=LOC)
    board.add_search("alpha.com", "siding repair", location_code=LOC)
    board.add_search("alpha.com", "storm damage", location_code=LOC)
    graded = made_at(board, clock, forecast(), 60)
    degraded = made_at(board, clock, forecast(search="gutter repair"), 60)
    empty = made_at(board, clock, forecast(search="siding repair"), 60)
    no_window = made_at(board, clock, forecast(stage="d30", day=30, search="storm damage"), 60)
    board.add_reading(reading("r90", 90, {"alpha.com": 6}))
    board.add_reading(reading("g90", 90, {"alpha.com": 6}, search="gutter repair", degraded=True))
    today = LAUNCH + timedelta(days=95)
    clock.at_day(95)
    first = grade_job.run(board, today)
    assert first["graded"] == [graded]
    assert first["due_only_degraded_readings"] == [degraded]
    assert first["due_no_reading_in_window"] == sorted([empty, no_window])
    assert first["window_closed_no_reading"] == sorted([empty, no_window])
    heads = board.export(tmp_path / "one")
    second = grade_job.run(board, today)
    assert second["graded"] == [] and second["already_graded"] == [graded]
    assert board.export(tmp_path / "two") == heads
    assert "Forecasts graded now: 0." in grade_job.render_summary(second)


def test_job_skips_readings_taken_before_the_forecast(board: Scoreboard, clock: Clock):
    board.add_reading(reading("r88", 88, {"alpha.com": 6}, hour=1))
    fid = made_at(board, clock, forecast(day=88), 88)
    clock.at_day(95)
    summary = grade_job.run(board, LAUNCH + timedelta(days=95))
    assert summary["due_only_readings_before_forecast"] == [fid]


def test_job_main_needs_an_explicit_date(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    with pytest.raises(SystemExit):
        grade_job.main(["--url", url])
    assert grade_job.main(["--url", url, "--date", "2027-03-01"]) == 0
    assert "Grading job as of 2027-03-01." in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------
# the stage report


def _many(board: Scoreboard, clock: Clock, *, sites: int = 8, searches: int = 5, brand: bool = True) -> None:
    """sites x searches seed cells at d60, graded at day 90. Most land beyond 50, like new domains."""
    clock.at_day(-5)
    for s in range(sites):
        site = f"site{s}.example"
        board.add_site(site, source="seed", launch_date=LAUNCH, launch_date_source="seed manifest",
                       built_by="ranksim", niche="roofing" if s % 2 else "plumbing")
        for q in range(searches):
            board.add_search(site, f"query {q} for {s}", location_code=LOC, difficulty=("easy", "medium", "hard")[q % 3])
        if brand:
            board.add_search(site, f"site{s} example login", location_code=LOC)
    clock.at_day(60)
    for s in range(sites):
        site = f"site{s}.example"
        for q in range(searches):
            point = 5 if q == 0 else NT50
            board.record_forecast(forecast(point=point, site=site, search=f"query {q} for {s}"))
        if brand:
            board.record_forecast(forecast(point=1, site=site, search=f"site{s} example login"))
    for s in range(sites):
        site = f"site{s}.example"
        for q in range(searches):
            actual = (6 if s < 6 else 30) if q == 0 else (NT50 if q != 4 or s % 3 else 40)
            board.add_reading(reading(f"r{s}-{q}", 90, {site: actual}, search=f"query {q} for {s}"))
        if brand:
            board.add_reading(reading(f"b{s}", 90, {site: 1}, search=f"site{s} example login"))
    clock.at_day(91)
    grade_job.run(board, LAUNCH + timedelta(days=91))


def test_stage_report_numbers_and_brand_split(board: Scoreboard, clock: Clock):
    _many(board, clock)
    rep = stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19))
    head = rep["headline"]
    assert head["forecasts"] == 40 and head["brand_forecasts_shown_apart"] == 8
    assert head["brand"]["hit"]["rate"] == 1.0
    # 6 page one hits, and 29 of 32 deep calls right (sites 0, 3 and 6 land at 40 on query 4)
    assert head["summary"]["hit"]["right"] == 6 + 29
    assert set(head["baselines"]) == {"always_nt50", "persistence"}
    assert rep["eligibility_this"]["graded"] == 48
    assert rep["by_difficulty"]["easy"]["count"] == 16
    assert list(rep["by_difficulty"]) == ["easy", "medium", "hard"]  # tercile order, not alphabetical
    assert set(rep["by_niche"]) == {"plumbing", "roofing"}
    assert rep["by_lead_days"]["30"]["count"] == 40
    assert rep["built_by"] == {"ranksim": 40}
    assert rep["label"] == "REAL" and rep["chain"]["ok"] is True


def test_stage_report_accepts_extra_baselines_and_refuses_reserved_names(board: Scoreboard, clock: Clock):
    _many(board, clock, sites=4, searches=2, brand=False)
    rep = stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19),
                       extra_baselines={"band_prior": lambda cell: NT50})
    assert "band_prior" in rep["headline"]["baselines"]
    with pytest.raises(ScoreboardRefusal, match="reserved"):
        stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19),
                     extra_baselines={"persistence": lambda cell: 1})
    with pytest.raises(ScoreboardRefusal, match="no point"):
        stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19), extra_baselines={"strength": {}})


def test_sources_are_never_pooled():
    with pytest.raises(PoolingRefused):
        combined_report()


def test_stage_report_refuses_a_pooled_source(board: Scoreboard):
    for source in ("all", "combined", None):
        with pytest.raises(PoolingRefused):
            stage_report(board, source=source, stage="d60", as_of=date(2027, 3, 19))


def test_render_text_is_plain_and_labels_every_number(board: Scoreboard, clock: Clock):
    _many(board, clock)
    text_out = render_text(stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19)))
    assert "\u2014" not in text_out and "\u2013" not in text_out
    assert " - " not in text_out and "2027-" not in text_out
    assert "Hit rate: 87.5%, 35 of 40 (REAL)." in text_out
    assert "Page one precision: NOT MEASURABLE (8 of 30 needed) (REAL)." in text_out
    header = ("Ranksim stage report.", "Model version", "Persistence is", "pilot has", "backtest has", "synthetic has")
    for line in text_out.splitlines():
        if any(ch.isdigit() for ch in line) and not line.startswith(header):
            assert "(REAL)" in line or "(PREDICTED)" in line or "(SYNTHETIC)" in line, line


def test_report_with_zero_outcomes_says_not_measurable(board: Scoreboard, clock: Clock):
    made_at(board, clock, forecast(), 60)
    rep = stage_report(board, source="seed", stage="d60", as_of=date(2027, 2, 1))
    assert rep["headline"]["forecasts"] == 0 and rep["eligibility_this"]["not_yet_due"] == 1
    text_out = render_text(rep)
    assert "NOT MEASURABLE (0 of 30 needed)" in text_out and "zero graded outcomes" in text_out
    verdict = evaluate_marker(5, rep)
    assert verdict["passed"] is None
    assert any("zero eligible real outcomes" in note for note in verdict["notes"])


def test_report_keeps_short_lead_rows_out_of_the_headline(board: Scoreboard, clock: Clock):
    clock.at_day(70)
    fid = board.record_forecast(forecast(day=70)).forecast_id  # a d60 forecast made late: lead 20 days
    board.add_reading(reading("r90", 90, {"alpha.com": 5}))
    clock.at_day(91)
    board.grade(fid, "r90")
    rep = stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19))
    assert rep["excluded_short_lead"] == 1 and rep["headline"]["forecasts"] == 0


def test_report_shows_regrades_beside_originals_and_core_updates(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=10), 60)
    board.add_reading(reading("r89", 89, {"alpha.com": 16}))
    board.add_reading(reading("r92", 92, {"alpha.com": 11}))
    clock.at_day(94)
    board.grade(fid, "r89")
    board.grade(fid, "r92", kind="regrade", reason="core update during the window")
    board.record_core_update("February core update", kind="core", started_on="2027-02-20", ended_on="2027-02-27",
                             source_url="https://status.search.google.com/incidents/y")
    rep = stage_report(board, source="seed", stage="d60", as_of=date(2027, 3, 19))
    pair = rep["regrades"]["pairs"][0]
    assert pair["original"]["position"] == 16 and pair["original"]["hit"] is False
    assert pair["regrade"]["position"] == 11 and pair["regrade"]["hit"] is True
    assert rep["headline"]["summary"]["hit"]["right"] == 0  # the headline stays on the original
    (update,) = rep["core_updates"]["recorded"]
    assert update["outcomes_inside"]["count"] == 1 and update["forecast_spans_overlapping"] == 1
    assert rep["core_updates"]["outcomes_outside_updates"]["count"] == 0
    assert "because core update during the window" in render_text(rep)


# ---------------------------------------------------------------------------------------------
# bugs found in review


def test_latest_view_as_of_falls_back_to_the_original_before_a_later_regrade(board: Scoreboard, clock: Clock):
    fid = made_at(board, clock, forecast(point=10), 60)
    board.add_reading(reading("r89", 89, {"alpha.com": 16}))
    board.add_reading(reading("r93", 93, {"alpha.com": 11}))
    clock.at_day(94)
    board.grade(fid, "r89")
    board.grade(fid, "r93", kind="regrade", reason="calm week")
    before = board.graded_records("seed", "d60", as_of=LAUNCH + timedelta(days=91), kind="latest")
    assert [r.cell.actual for r in before] == [16]  # the regrade's reading is after as_of
    after = board.graded_records("seed", "d60", as_of=LAUNCH + timedelta(days=93), kind="latest")
    assert [r.cell.actual for r in after] == [11]
    assert board.graded_records("seed", "d60", as_of=LAUNCH + timedelta(days=91), kind="regrade") == []


def test_reading_with_too_few_kept_pulls_must_be_degraded(board: Scoreboard):
    bad = dataclasses.replace(reading("thin", 90, {"alpha.com": 6}), pulls_kept=2, degraded=False)
    with pytest.raises(ScoreboardRefusal, match="not marked degraded"):
        board.add_reading(bad)


def test_job_refuses_a_datetime_for_today(board: Scoreboard):
    with pytest.raises(TypeError):
        grade_job.run(board, datetime(2027, 3, 1, tzinfo=timezone.utc))


def test_reading_stored_twice_is_idempotent_but_a_changed_one_is_refused(board: Scoreboard):
    board.add_reading(reading("r10", 10, {"alpha.com": 6}))
    board.add_reading(reading("r10", 10, {"alpha.com": 6}))
    with pytest.raises(ScoreboardRefusal, match="different content"):
        board.add_reading(reading("r10", 10, {"alpha.com": 7}))


# ---------------------------------------------------------------------------------------------
# reading provenance, reading access and batch writes


def test_reading_provenance_is_stored_returned_and_exported(board: Scoreboard, clock: Clock, tmp_path: Path):
    made = dataclasses.replace(reading("rp", 5, {"alpha.com": 4}, degraded=True), reader_version="reader-v2",
                               degraded_reason="too_few_kept_pulls: 2 of 5 pulls kept, 3 needed")
    board.add_reading(made)
    back = board.reading("rp")
    assert back == made and back.reader_version == "reader-v2" and back.degraded_reason.startswith("too_few")
    board.add_reading(made)  # the same content again is a no-op
    with pytest.raises(ScoreboardRefusal, match="different content"):
        board.add_reading(dataclasses.replace(made, degraded_reason="cluster_tie: other"))
    board.add_reading(reading("plain", 6, {"alpha.com": 4}))
    assert board.reading("plain").reader_version is None and board.reading("plain").degraded_reason is None
    board.export(tmp_path / "export")
    rows = [__import__("json").loads(line) for line in (tmp_path / "export" / "readings.jsonl").read_text().splitlines()]
    by_id = {row["reading_id"]: row for row in rows}
    assert by_id["rp"]["reader_version"] == "reader-v2" and by_id["rp"]["degraded_reason"].startswith("too_few")
    assert by_id["plain"]["reader_version"] is None


def test_an_older_database_without_the_provenance_columns_still_opens(tmp_path: Path, clock: Clock):
    path = tmp_path / "old.db"
    first = Scoreboard(f"sqlite:///{path}", clock=clock)
    first.add_reading(reading("before", 3, {"alpha.com": 2}))
    first.close()
    import sqlite3

    with sqlite3.connect(path) as connection:  # make it look like a database from before the columns existed
        connection.execute("DROP TRIGGER readings_no_update")
        connection.execute("ALTER TABLE readings DROP COLUMN reader_version")
        connection.execute("ALTER TABLE readings DROP COLUMN degraded_reason")
        names = {row[1] for row in connection.execute("PRAGMA table_info(readings)")}
        assert "reader_version" not in names
    again = Scoreboard(f"sqlite:///{path}", clock=clock)
    try:
        assert again.reading("before").reader_version is None
        again.add_reading(dataclasses.replace(reading("after", 4, {"alpha.com": 2}), reader_version="reader-v2"))
        assert again.reading("after").reader_version == "reader-v2"
        assert again.add_missing_columns() == []
        assert again.verify().ok  # the dropped trigger was put back on open
    finally:
        again.close()


def test_an_export_made_before_the_provenance_columns_still_compares_and_imports(board: Scoreboard, clock: Clock, tmp_path: Path):
    import json

    from aeo.ranksim.forecast.chain import sha256_text
    from aeo.ranksim.forecast.pipeline import verify_against_export

    board.add_reading(reading("r1", 3, {"alpha.com": 2}))
    folder = tmp_path / "export"
    board.export(folder)
    path = folder / "readings.jsonl"
    old_rows = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        row.pop("reader_version")
        row.pop("degraded_reason")
        old_rows.append(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    body = "".join(item + "\n" for item in old_rows)
    path.write_text(body, encoding="utf-8")
    heads = json.loads((folder / "HEADS.json").read_text())
    heads["files"]["readings.jsonl"] = sha256_text(body)
    (folder / "HEADS.json").write_text(json.dumps(heads, sort_keys=True, indent=2) + "\n")
    comparison = verify_against_export(board, folder)
    assert comparison.ok, comparison.problems
    fresh = Scoreboard(f"sqlite:///{tmp_path / 'fresh.db'}", clock=clock)
    try:
        assert fresh.import_export(folder).ok
        assert fresh.reading("r1").reader_version is None
    finally:
        fresh.close()


def test_readings_for_returns_newest_first_with_its_filters(board: Scoreboard, clock: Clock):
    board.add_reading(reading("d1", 1, {"alpha.com": 3}))
    board.add_reading(reading("d2", 2, {"alpha.com": 4}, degraded=True))
    board.add_reading(reading("d3", 3, {"alpha.com": 5}))
    board.add_reading(reading("d3s", 3, {"alpha.com": 6}, label="SYNTHETIC", hour=11))
    board.add_reading(reading("other", 3, {"alpha.com": 6}, location=2840))
    ids = lambda found: [item.reading_id for item in found]  # noqa: E731
    assert ids(board.readings_for(SEARCH, LOC, "desktop")) == ["d3s", "d3", "d1"]
    assert ids(board.readings_for(SEARCH.upper(), LOC, "desktop", non_degraded_only=False)) == ["d3s", "d3", "d2", "d1"]
    assert ids(board.readings_for(SEARCH, LOC, "desktop", label="REAL", limit=1)) == ["d3"]
    moment = datetime.fromisoformat(utc_day(3).replace("Z", "+00:00"))
    assert ids(board.readings_for(SEARCH, LOC, "desktop", before=moment)) == ["d1"]
    assert ids(board.readings_for(SEARCH, LOC, "desktop", on_or_before=moment)) == ["d3", "d1"]
    assert ids(board.readings_for(SEARCH, LOC, "desktop", on_or_before=moment.date())) == ["d3s", "d3", "d1"]
    assert ids(board.readings_for(SEARCH, LOC, "desktop", not_before=moment)) == ["d3s", "d3"]
    assert board.readings_for(SEARCH, LOC, "mobile") == []


def test_persistence_uses_only_readings_of_the_forecast_label(board: Scoreboard, clock: Clock):
    board.add_reading(reading("real", 50, {"alpha.com": 9}))
    board.add_reading(reading("synth", 55, {"alpha.com": 2}, label="SYNTHETIC"))
    fid = made_at(board, clock, forecast(), 62)
    assert board.persistence_points([fid]) == [9]  # a seed forecast never copies a synthetic reading
    board.add_site("synthsite.example", source="synthetic", launch_date=LAUNCH, launch_date_source="test", built_by="test")
    board.add_search("synthsite.example", SEARCH, location_code=LOC)
    synthetic = forecast(site="synthsite.example")
    sid = made_at(board, clock, synthetic, 62)
    board.add_reading(reading("synth2", 56, {"synthsite.example": 7}, label="SYNTHETIC"))
    assert board.persistence_points([sid]) == [7]


def test_store_batch_writes_everything_in_one_transaction_or_nothing(board: Scoreboard, clock: Clock):
    pulls = [_pull(f"bp{i}", [("https://alpha.com/x", 1)]) for i in range(3)]
    good = reading("batch-ok", 7, {"alpha.com": 1}, pull_ids=tuple(p.pull_id for p in pulls))
    board.store_batch(pulls=pulls, readings=[good])
    assert board.pull("bp0").pull_id == "bp0" and board.reading("batch-ok") == good
    board.store_batch(pulls=pulls, readings=[good])  # the same rows again change nothing
    bad = dataclasses.replace(reading("batch-bad", 8, {"alpha.com": 1}), pulls_kept=1)  # not degraded with 1 kept
    with pytest.raises(ScoreboardRefusal):
        board.store_batch(pulls=[_pull("bp-new", [("https://alpha.com/x", 2)])], readings=[bad])
    with pytest.raises(ScoreboardRefusal):
        board.pull("bp-new")  # the pull of the refused batch was not stored either
    changed = dataclasses.replace(good, positions=())
    with pytest.raises(ScoreboardRefusal, match="different content"):
        board.store_batch(pulls=[_pull("bp-late", [("https://alpha.com/x", 2)])], readings=[changed])
    with pytest.raises(ScoreboardRefusal):
        board.pull("bp-late")


def test_pulls_completed_between_filters_by_time_and_search(board: Scoreboard):
    early = dataclasses.replace(_pull("t1", [("https://alpha.com/x", 1)]), completed_at="2026-12-01T01:00:00Z")
    late = dataclasses.replace(_pull("t2", [("https://alpha.com/x", 1)]), completed_at="2026-12-01T23:00:00Z")
    next_day = dataclasses.replace(_pull("t3", [("https://alpha.com/x", 1)]), completed_at="2026-12-02T00:00:00Z")
    board.store_batch(pulls=[late, next_day, early])
    start = datetime(2026, 12, 1, tzinfo=timezone.utc)
    found = board.pulls_completed_between(start, start + timedelta(days=1))
    assert [pull.pull_id for pull in found] == ["t1", "t2"]
    assert board.pulls_completed_between(start, start + timedelta(days=1), search_keys=["no such search"]) == []
