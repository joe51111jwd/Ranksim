"""Stage reports: the held-out rule for backtest rows, and the same report built from cells in memory.

Everything runs on a temporary SQLite file with an injected clock, or on made-up cells in memory.
No network. The sites are example domains and the positions are made up.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.grading import GradedCell
from aeo.ranksim.forecast.markers import evaluate_marker
from aeo.ranksim.forecast.report import (
    FittedBandPrior,
    MemoryCell,
    held_out_records,
    render_text,
    report_from_cells,
    stage_report,
)
from aeo.ranksim.forecast.scoreboard import PoolingRefused, Scoreboard, ScoreboardRefusal
from aeo.ranksim.forecast.types import CleanReading, Forecast, Pull, PullSpec, SitePosition

LAUNCH = date(2026, 11, 30)
LOC = 1026201
SEARCH = "roof repair austin"


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.moment

    def at_day(self, day: int, hour: int = 12) -> None:
        self.moment = datetime(LAUNCH.year, LAUNCH.month, LAUNCH.day, hour, tzinfo=timezone.utc) + timedelta(days=day)


def stamp(day: int, hour: int) -> str:
    moment = datetime(LAUNCH.year, LAUNCH.month, LAUNCH.day, hour, tzinfo=timezone.utc) + timedelta(days=day)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def outcome_reading(positions: dict[str, int]) -> tuple[list[Pull], CleanReading]:
    spec = PullSpec(search=SEARCH, location_code=LOC)
    pulls = [Pull(pull_id=f"p{i}", provider="dataforseo", endpoint="test", spec=spec, requested_at=stamp(90, 8),
                  completed_at=stamp(90, 9), ok=True) for i in range(5)]
    reading = CleanReading(
        reading_id="r90", search=SEARCH, location_code=LOC, device="desktop", taken_at=stamp(90, 10),
        pulls_total=5, pulls_kept=5, dropped=(), degraded=False, pulls_not_independent=False,
        positions=tuple(SitePosition(site, pos, (pos,) * 5) for site, pos in positions.items()),
        pull_ids=tuple(p.pull_id for p in pulls), label="REAL",
    )
    return pulls, reading


@pytest.fixture()
def split_board(tmp_path: Path):
    """Backtest d30 forecasts on 40 training sites and 40 held-out sites, all graded."""
    clock = Clock()
    board = Scoreboard(f"sqlite:///{tmp_path / 'scoreboard.db'}", clock=clock)
    board.register_model_version("m1", description="test model", artefact_sha256="0" * 64)
    sites = {f"train{i}.example": "train" for i in range(40)} | {f"held{i}.example": "test" for i in range(40)}
    for site in sites:
        board.add_site(site, source="backtest", launch_date=LAUNCH, launch_date_source="estimate", built_by="other")
        board.add_search(site, SEARCH, location_code=LOC, difficulty="easy")
    clock.at_day(30)
    ids = {}
    for index, (site, split) in enumerate(sites.items()):
        point = 5 if index % 4 else 30
        fc = Forecast(site=site, search=SEARCH, stage="d30", as_of=LAUNCH + timedelta(days=30), point=point,
                      range_low=1, range_high=NT50, label="contested", model_version="m1", notes={"split": split})
        ids[site] = board.record_forecast(fc).forecast_id
    pulls, reading = outcome_reading({site: 5 for site in sites})
    board.store_batch(pulls=pulls, readings=[reading])
    clock.at_day(91)
    for fid in ids.values():
        board.grade(fid, "r90")
    yield board, sites
    board.close()


def test_backtest_stage_report_grades_only_the_held_out_split(split_board):
    board, sites = split_board
    records = board.graded_records("backtest", "d30", date(2027, 3, 15), "original")
    training = [r.cell for r in records if r.split == "train"]
    report = stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15), band_prior_training=training)
    assert report["headline"]["forecasts"] == 40
    assert report["excluded_not_held_out"] == 40
    assert report["held_out"] == {"test": 40, "not_test": 0}
    assert report["band_prior"] is not None and "band_prior" in report["headline"]["baselines"]
    assert any("40 graded rows of the training split are left out" in note for note in report["notes"])
    text = render_text(report)
    assert "Training rows left out, only held out rows are graded: 40 (REAL)." in text
    assert "The hash chains verify." in text


def test_a_band_prior_from_a_graded_held_out_site_is_still_refused(split_board):
    board, _ = split_board
    records = board.graded_records("backtest", "d30", date(2027, 3, 15), "original")
    leaky = [r.cell for r in records if r.split == "test"][:3]
    with pytest.raises(ScoreboardRefusal, match="fitted on training data only"):
        stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15), band_prior_training=leaky)


def test_held_out_rule_touches_only_split_sources():
    class Row:
        def __init__(self, split):
            self.split = split

    rows = [Row("test"), Row("train"), Row(None)]
    kept, left = held_out_records(rows, "backtest")
    assert [r.split for r in kept] == ["test"] and len(left) == 2
    for source in ("seed", "pilot", "synthetic"):
        assert held_out_records(rows, source) == (rows, [])


# ---------------------------------------------------------------------------------------------
# the report from cells held in memory


def memory_cells(count: int, *, split: str = "test", source: str = "backtest", stage: str = "upload",
                 lead: int = 90, hit_every: int = 2) -> list[MemoryCell]:
    out = []
    for i in range(count):
        actual = 5 if i % hit_every == 0 else NT50
        cell = GradedCell(site=f"s{i % 25}.example", search=f"q{i}", point=5, actual=actual, difficulty="easy",
                          source=source, stage=stage, lead_days=lead)
        out.append(MemoryCell(cell=cell, split=split, baselines={"persistence": NT50, "site_strength": 5},
                              as_of=LAUNCH, outcome_date=LAUNCH + timedelta(days=lead)))
    return out


def test_report_from_cells_has_the_stage_report_keys_and_markers_run_on_it(split_board):
    board, _ = split_board
    from_board = stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15))
    held = memory_cells(60)
    training = [GradedCell(site=f"t{i}.example", search=f"tq{i}", point=5, actual=NT50, difficulty="easy")
                for i in range(10)]
    report = report_from_cells(held, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                               band_prior_training=training)
    assert set(from_board) <= set(report)
    assert report["label"] == "REAL" and report["chain"]["ok"] is None
    assert report["headline"]["forecasts"] == 60 and report["baselines_missing"] == []
    assert report["headline"]["summary"]["hit"]["rate"] == pytest.approx(0.5)
    verdict = evaluate_marker(3, report)
    assert verdict["passed"] is False  # half right is under 0.75
    text = render_text(report)
    assert "The hash chains verify." not in text and "graded in memory" in text


def test_report_from_cells_counts_training_short_lead_and_brand_rows_apart():
    held = memory_cells(40)
    train = memory_cells(5, split="train")
    short = memory_cells(3, lead=10)
    brand = [MemoryCell(cell=GradedCell(site="b.example", search="brand", point=5, actual=5, is_brand=True,
                                        source="backtest", stage="upload", lead_days=90), split="test",
                        baselines={"persistence": NT50, "site_strength": 5})]
    report = report_from_cells(held + train + short + brand, source="backtest", stage="upload", as_of=date(2026, 12, 21),
                               model_version="m1")
    assert report["excluded_not_held_out"] == 5 and report["excluded_short_lead"] == 3
    assert report["headline"]["forecasts"] == 40 and report["headline"]["brand_forecasts_shown_apart"] == 1
    assert "band_prior" in report["baselines_missing"]


def test_report_from_cells_refuses_pooling_reserved_names_and_uneven_baselines():
    held = memory_cells(30)
    with pytest.raises(PoolingRefused):
        report_from_cells(held + memory_cells(2, source="seed"), source="backtest", stage="upload",
                          as_of=date(2026, 12, 21), model_version="m1")
    with pytest.raises(PoolingRefused):
        report_from_cells(held, source="everything", stage="upload", as_of=date(2026, 12, 21), model_version="m1")
    seed_cells = memory_cells(30, source="seed")
    with pytest.raises(PoolingRefused, match="scoreboard"):  # seed outcomes need the verified chain
        report_from_cells(seed_cells, source="seed", stage="upload", as_of=date(2026, 12, 21), model_version="m1")
    bad = [MemoryCell(cell=held[0].cell, split="test", baselines={"band_prior": 5})]
    with pytest.raises(ScoreboardRefusal, match="reserved"):
        report_from_cells(bad, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1")
    uneven = held[:2] + [MemoryCell(cell=held[2].cell, split="test", baselines={"persistence": NT50})]
    with pytest.raises(ScoreboardRefusal, match="same baselines"):
        report_from_cells(uneven, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1")
    with pytest.raises(ScoreboardRefusal, match="replace"):
        report_from_cells(held, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                          extra={"label": "REAL"})


def test_report_from_cells_checks_the_band_prior_on_the_split_unit():
    held = memory_cells(30)
    same_site = [GradedCell(site="s1.example", search="other", point=5, actual=5, difficulty="easy")]
    with pytest.raises(ScoreboardRefusal, match="site"):
        report_from_cells(held, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                          band_prior_training=same_site)
    by_search = report_from_cells(held, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                                  band_prior_training=same_site, band_prior_unit="search", cluster_by="search")
    assert by_search["band_prior"] is not None and by_search["headline"]["summary"]["cluster_by"] == "search"
    assert by_search["headline_site_clusters"]["summary"]["cluster_by"] == "site"
    same_search = [GradedCell(site="new.example", search="q1", point=5, actual=5, difficulty="easy")]
    with pytest.raises(ScoreboardRefusal, match="search"):
        report_from_cells(held, source="backtest", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                          band_prior_training=same_search, band_prior_unit="search")


def test_an_already_fitted_band_prior_is_used_and_its_source_is_printed_in_one_line(split_board):
    board, _ = split_board
    fitted = FittedBandPrior({"all": NT50, "easy": 5}, frozenset({"panelsite.example"}), frozenset({"g1"}),
                             "The band prior comes from bundle b1, fitted on its own training rows.")
    report = stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15), band_prior_fitted=fitted)
    assert report["band_prior"] == {"all": NT50, "easy": 5}
    assert "band_prior" in report["headline"]["baselines"] and "band_prior" not in report["baselines_missing"]
    assert report["band_prior_source"] == fitted.source
    assert render_text(report).count("comes from bundle b1") == 1


def test_an_already_fitted_band_prior_whose_fitting_site_is_graded_here_is_refused(split_board):
    board, _ = split_board
    fitted = FittedBandPrior({"all": 5}, frozenset({"held3.example"}), frozenset(), "a bundle")
    with pytest.raises(ScoreboardRefusal, match="fitted on training data only"):
        stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15), band_prior_fitted=fitted)


def test_a_fitted_band_prior_and_training_cells_together_are_refused(split_board):
    board, _ = split_board
    fitted = FittedBandPrior({"all": 5}, frozenset(), frozenset(), "a bundle")
    training = [GradedCell(site="t.example", search="q", point=5, actual=5)]
    with pytest.raises(ScoreboardRefusal, match="not both"):
        stage_report(board, source="backtest", stage="d30", as_of=date(2027, 3, 15), band_prior_fitted=fitted,
                     band_prior_training=training)


def test_a_synthetic_report_from_cells_is_labeled_synthetic_and_never_passes():
    cells = memory_cells(60, source="synthetic", hit_every=1)
    training = [GradedCell(site=f"t{i}.example", search=f"tq{i}", point=5, actual=5, difficulty="easy") for i in range(5)]
    report = report_from_cells(cells, source="synthetic", stage="upload", as_of=date(2026, 12, 21), model_version="m1",
                               band_prior_training=training)
    assert report["label"] == "SYNTHETIC"
    verdict = evaluate_marker(3, report)
    assert verdict["passed"] is None and verdict["label"] == "SYNTHETIC"
