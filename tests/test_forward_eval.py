"""Forward scoring from the 2026-11-06 snapshot onto the 2026-11-09 order.

The rules under test are fixed in the module. A later observation's positions
are the labels being scored. They are not training rows, feature choices, or
thresholds.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from aeo.ranksim.forward_eval import (
    BASELINE_DAY,
    BLEND_CONTENT_WEIGHT,
    BLEND_PERSISTENCE_WEIGHT,
    ENTRANT_BASE_POSITION,
    ENTRANT_SCORE,
    FORWARD_DAY,
    HISTORY_CONTENT_SCALE,
    MISSING_CONTENT_SCORE,
    PREDICTION_CREATED_AT,
    agreement_on_overlap,
    blend_scores,
    evaluate_snapshot_pair,
    history_aware_scores,
    persistence_from_positions,
    persistence_scores,
    replay_ledger,
    write_forward_ledger,
)
from aeo.ranksim.ledger import RsOutcome, RsPrediction, score_ledger, verify_ledger
from aeo.ranksim.store import open_session


def _page(title: str) -> str:
    return (
        "<!doctype html><html><head><title>"
        + title
        + "</title></head><body><h1>"
        + title
        + "</h1><p>"
        + title
        + " for a field crew. Scheduling, job costing, and estimates.</p></body></html>"
    )


def _write_snapshot(root: Path, rows: list[tuple[str, int, str]], html: dict[str, str]) -> None:
    root.mkdir(parents=True)
    (root / "html").mkdir()
    with (root / "serp_serper_live.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query", "position", "link"])
        writer.writerows(rows)
    with (root / "html_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["url", "file"])
        for url, body in html.items():
            name = "html/" + str(abs(hash(url))) + ".html"
            (root / name).write_text(body, encoding="utf-8")
            writer.writerow([url, name])


def _pair(tmp_path: Path):
    baseline = tmp_path / "baseline"
    forward = tmp_path / "forward"
    _write_snapshot(
        baseline,
        [
            ("alpha tools", 1, "https://a.example/old"),
            ("alpha tools", 2, "https://b.example/old"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {
            "https://a.example/old": _page("alpha tools guide"),
            "https://b.example/old": _page("alpha tools notes"),
            "https://d.example/one": _page("beta widgets handbook"),
            "https://e.example/two": _page("beta widgets pricing"),
        },
    )
    _write_snapshot(
        forward,
        [
            ("alpha tools", 1, "https://c.example/new"),
            ("alpha tools", 2, "https://a.example/old"),
            ("alpha tools", 3, "https://b.example/old"),
            ("alpha tools", 4, "https://f.example/missing"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {
            "https://c.example/new": _page("alpha tools newcomer"),
            "https://a.example/old": _page("alpha tools guide revised"),
            "https://b.example/old": _page("alpha tools notes revised"),
            "https://d.example/one": _page("beta widgets handbook revised"),
            "https://e.example/two": _page("beta widgets pricing revised"),
        },
    )
    return baseline, forward


def _alpha():
    return [
        {"baseline_position": 2.0, "bm25": 1.0, "html": True},
        {"baseline_position": 1.0, "bm25": 3.0, "html": True},
        {"baseline_position": None, "bm25": 5.0, "html": True},
        {"baseline_position": None, "bm25": None, "html": False},
    ]


def test_persistence_ties_unseen_urls_below_every_page_seen_on_the_baseline():
    scores = persistence_scores(_alpha())
    assert scores[0] == -2.0
    assert scores[1] == -1.0
    assert scores[2] == scores[3] == ENTRANT_SCORE
    assert ENTRANT_SCORE < -10
    assert scores[2] < scores[0] < scores[1]


def test_history_and_blend_use_the_preregistered_constants():
    assert HISTORY_CONTENT_SCALE == 1.0
    assert ENTRANT_BASE_POSITION == 11.0
    assert (BLEND_PERSISTENCE_WEIGHT, BLEND_CONTENT_WEIGHT) == (1.0, 1.0)
    assert PREDICTION_CREATED_AT == datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
    assert BASELINE_DAY == "2026-11-06"
    assert FORWARD_DAY == "2026-11-09"
    rows = _alpha()
    present = [1.0, 3.0, 5.0]
    mean = sum(present) / len(present)
    std = math.sqrt(sum((value - mean) ** 2 for value in present) / len(present))
    z_values = [(1.0 - mean) / std, (3.0 - mean) / std, (5.0 - mean) / std, 0.0]
    history = history_aware_scores(rows)
    assert history[0] == -2.0 + HISTORY_CONTENT_SCALE * z_values[0]
    assert history[1] == -1.0 + HISTORY_CONTENT_SCALE * z_values[1]
    assert history[2] == -ENTRANT_BASE_POSITION + HISTORY_CONTENT_SCALE * z_values[2]
    assert history[3] == -ENTRANT_BASE_POSITION
    bases = [-2.0, -1.0, -ENTRANT_BASE_POSITION, -ENTRANT_BASE_POSITION]
    base_mean = sum(bases) / len(bases)
    base_std = math.sqrt(sum((value - base_mean) ** 2 for value in bases) / len(bases))
    blend = blend_scores(rows)
    assert blend[2] == pytest_blend(bases[2], base_mean, base_std, z_values[2])
    assert blend[3] == pytest_blend(bases[3], base_mean, base_std, 0.0)


def pytest_blend(base: float, mean: float, std: float, content_z: float) -> float:
    return BLEND_PERSISTENCE_WEIGHT * ((base - mean) / std) + BLEND_CONTENT_WEIGHT * content_z


def test_overlap_agreement_ignores_entrants_and_counts_every_ordered_pair():
    baseline = [
        ("alpha tools", "https://a.example/old", 1.0),
        ("alpha tools", "https://b.example/old", 2.0),
        ("alpha tools", "https://gone.example/x", 3.0),
    ]
    forward = [
        ("alpha tools", "https://b.example/old", 1.0),
        ("alpha tools", "https://a.example/old", 2.0),
        ("alpha tools", "https://c.example/new", 3.0),
    ]
    report = agreement_on_overlap(baseline, forward)
    assert report["baseline_rows"] == 3
    assert report["rows_reappeared"] == 2
    assert report["pairs"] == 1
    assert report["agree"] == 0
    assert report["pairwise"] == 0.0
    assert report["mean_spearman"] == -1.0


def test_forward_labels_do_not_enter_training_and_history_is_not_adopted(tmp_path):
    baseline, forward = _pair(tmp_path)
    result = evaluate_snapshot_pair(baseline, forward)
    trained = {(row.query, row.url): row.position for row in result.training_examples}
    assert ("alpha tools", "https://c.example/new") not in trained
    assert ("alpha tools", "https://f.example/missing") not in trained
    assert trained[("alpha tools", "https://a.example/old")] == 1.0
    assert trained[("beta widgets", "https://d.example/one")] == 1.0
    for example in result.training_examples:
        assert "position" not in example.features
        assert float(example.features.get("navboost_prior") or 0.0) == 0.0
    report = result.report
    assert report["scorers"]["history_aware"]["adopted"] is False
    assert "2026-11-09" in report["scorers"]["history_aware"]["adoption_reason"]
    persistence = report["scorers"]["persistence"]
    # alpha: the new page at position 1 loses both pairs against the two returning pages (2 wrong),
    # ties the other entrant, and the two returning pages stay above each other and above the
    # last-place entrant (3 correct). beta: the baseline order still matches (1 correct). 4 of 7.
    assert persistence["pairwise"]["correct"] == 4
    assert persistence["pairwise"]["wrong"] == 2
    assert persistence["pairwise"]["ties"] == 1
    assert persistence["pairwise"]["pairs"] == 7
    entrant = persistence["entrant_vs_returning"]
    assert entrant["correct"] == 2
    assert entrant["pairs"] == 4
    assert persistence["entrant_vs_entrant"]["ties"] == 1
    assert persistence["entrant_vs_entrant"]["correct"] == 0
    assert persistence["draft_vs_returning"] == entrant
    assert persistence["draft_vs_draft"]["ties"] == 1
    missing = [row for row in result.scored_rows if row["url"] == "https://f.example/missing"]
    assert len(missing) == 1
    assert missing[0]["html_source"] == "missing"
    assert missing[0]["content_missing"] == 1
    assert missing[0]["bm25"] > MISSING_CONTENT_SCORE
    assert missing[0]["lambdamart"] > MISSING_CONTENT_SCORE
    assert missing[0]["lambdamart"] == missing[0]["bm25"]
    kept_score = [row for row in result.scored_rows if row["url"] == "https://a.example/old"][0]
    assert kept_score["content_missing"] == 0
    newcomer = [row for row in result.scored_rows if row["url"] == "https://c.example/new"][0]
    assert newcomer["html_source"] == "forward_fetch"
    assert newcomer["entrant"] is True
    kept = [row for row in result.scored_rows if row["url"] == "https://a.example/old"][0]
    assert kept["html_source"] == "baseline"
    assert report["coverage"]["entrant_rows"] == 2
    assert report["label"] == "PREDICTED"
    for name in ("lambdamart", "bm25", "persistence", "blend", "history_aware"):
        block = report["scorers"][name]
        assert set(block["pairwise"]) >= {"correct", "wrong", "ties", "pairs", "pairwise"}
        assert "ndcg_at_10" in block and "spearman" in block


def test_recovered_html_is_score_time_text_and_not_a_training_row(tmp_path):
    baseline, forward = _pair(tmp_path)
    recovered = {
        "https://f.example/missing": (_page("alpha tools recovered page with field crew estimates"), "browser"),
    }
    result = evaluate_snapshot_pair(baseline, forward, recovered=recovered)
    trained = {row.url for row in result.training_examples}
    assert "https://f.example/missing" not in trained
    row = [item for item in result.scored_rows if item["url"] == "https://f.example/missing"][0]
    assert row["html_source"] == "recovered_browser"
    assert row["html"] is True
    assert row["content_missing"] == 0
    assert row["bm25_value"] is not None
    assert result.report["coverage"]["html_recovered"] == 1
    assert result.report["coverage"]["html_missing"] == 0


def test_ledger_writes_predictions_then_outcomes_and_scores_the_later_day(tmp_path):
    baseline, forward = _pair(tmp_path)
    result = evaluate_snapshot_pair(baseline, forward)
    session = open_session(f"sqlite:///{tmp_path / 'ledger.db'}")
    try:
        summary = write_forward_ledger(session, result)
        ok, note = verify_ledger(session)
        assert ok and "chain intact" in note
        predictions = session.query(RsPrediction).all()
        outcomes = session.query(RsOutcome).all()
        assert predictions and outcomes
        assert all(row.created_at == "2026-11-07T04:00:00+00:00" for row in predictions)
        assert all(row.payload["provenance"]["label"] == "PREDICTED" for row in predictions)
        assert all(row.payload["confidence"] == "UNSURE" for row in predictions)
        assert all("not a Google position" in row.payload["rank_meaning"] for row in predictions)
        assert all(row.observed_on == "2026-11-09" for row in outcomes)
        assert all(row.provider == "serper" for row in outcomes)
        for row in predictions:
            for candidate in row.payload["candidates"]:
                if candidate["url"] == "https://c.example/new":
                    assert candidate["labeled_position"] is None
        persistence = score_ledger(session, project_slug="forward-2026-11-09-persistence")
        assert persistence.calls > 0
        assert persistence.no_outcome_yet == 0
        assert persistence.resolved == persistence.calls
        assert persistence.accuracy is not None
        assert summary["persistence"]["resolved"] == persistence.resolved
        assert summary["persistence"]["correct"] == persistence.correct
    finally:
        session.close()


def _position_rows(path: Path) -> list[tuple[str, str, float]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            (row["query"], row["link"], float(row["position"]))
            for row in csv.DictReader(handle)
        ]


def test_published_nov9_file_matches_persistence_and_the_ledger_chain(tmp_path):
    """The committed positions and the ledger chain. HTML is not in git, so content models are not refit here."""
    root = Path(__file__).resolve().parents[1] / "evals" / "forward-2026-11-09"
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    baseline = _position_rows(root / "baseline_serp.csv")
    forward = _position_rows(root / "forward_serp.csv")
    agreement = agreement_on_overlap(baseline, forward)
    stored = report["snapshot_agreement_overlap_only"]
    assert agreement["pairs"] == 667
    assert agreement["agree"] == 594
    assert agreement["rows_reappeared"] == 202
    assert agreement["baseline_rows"] == 294
    assert len(forward) == 299
    assert agreement["pairwise"] == stored["pairwise"]
    assert agreement["mean_spearman"] == stored["mean_spearman"]
    persisted = persistence_from_positions(baseline, forward)
    for key in ("pairwise", "returning_only", "entrant_vs_returning", "entrant_vs_entrant"):
        assert persisted[key] == report["scorers"]["persistence"][key]
    assert persisted["ndcg_at_10"] == report["scorers"]["persistence"]["ndcg_at_10"]
    assert persisted["spearman"] == report["scorers"]["persistence"]["spearman"]
    assert report["scorers"]["history_aware"]["adopted"] is False
    assert report["ranker_change"] == "none"
    assert report["label"] == "PREDICTED"
    assert report["files"]["baseline_serp_sha256"] == "c70ca9907a5243f092fe407d18ca002dc58a1bad301734fc56b6dad50e900a87"
    assert report["files"]["forward_serp_sha256"] == "49a0c8d4b92bd1fd73a2f4fac507394200e724f6c76c611a9c14b9595b3607d0"
    assert report["files"]["html_in_git"] is False
    for name in ("lambdamart", "bm25", "persistence", "blend", "history_aware"):
        block = report["scorers"][name]["pairwise"]
        assert block["pairs"] == 1341
        assert block["correct"] + block["wrong"] + block["ties"] == block["pairs"]
    session = open_session(f"sqlite:///{tmp_path / 'replay.db'}")
    try:
        replay_ledger(session, json.loads((root / "ledger.json").read_text(encoding="utf-8")))
        ok, note = verify_ledger(session)
        assert ok and report["ledger"]["chain"] in note
        scored = score_ledger(session, project_slug="forward-2026-11-09-persistence")
        assert scored.correct == report["ledger"]["persistence"]["correct"]
        assert scored.resolved == report["ledger"]["persistence"]["resolved"]
        assert scored.no_outcome_yet == 0
    finally:
        session.close()
