"""PREDICTED ledger scored against a later REAL file. No training."""

from __future__ import annotations

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.track_record import format_score, load_predictions, record_prediction, score_predictions


def test_record_appends_predicted_rows_and_score_uses_overlap_only(tmp_path, capsys):
    ledger = tmp_path / "preds.csv"
    record_prediction(
        query="example software",
        url="https://example.com/a",
        predicted_rank=1,
        predicted_of=2,
        confidence="UNSURE",
        source="run-1",
        ledger=ledger,
        recorded_at="2026-11-06T15:00:00+00:00",
    )
    record_prediction(
        query="example software",
        url="https://example.com/b",
        predicted_rank=2,
        predicted_of=2,
        confidence="UNSURE",
        source="run-1",
        ledger=ledger,
        recorded_at="2026-11-06T15:00:01+00:00",
    )
    rows = load_predictions(ledger)
    assert [row.label for row in rows] == ["PREDICTED", "PREDICTED"]
    assert rows[0].confidence == "UNSURE"
    assert "not a Google position" in rows[0].note

    later = [
        ("example software", "https://example.com/a", 2),
        ("example software", "https://example.com/b", 1),
        ("example software", "https://example.com/unpredicted", 3),
    ]
    score = score_predictions(rows, later)
    assert score.overlapping_rows == 2
    assert score.comparable_queries == 1
    assert score.comparable_pairs == 1
    assert score.pair_correct == 0
    assert score.pairwise == 0.0
    text = format_score(score)
    assert "PREDICTED" in text and "REAL" in text
    assert "not a model fit" in text

    empty = score_predictions(rows[:1], later)
    assert empty.reported is False
    assert empty.pairwise is None
    assert "not reported" in format_score(empty)
    assert "too small" in empty.note

    out = tmp_path / "report.txt"
    real = tmp_path / "later.csv"
    real.write_text(
        "query,url,position\nexample software,https://example.com/a,2\nexample software,https://example.com/b,1\n",
        encoding="utf-8",
    )
    main(["track-record", "score", str(ledger), str(real), "--out", str(out)])
    printed = capsys.readouterr().out
    assert "Pairwise agreement: 0.000" in printed
    assert "Pairwise agreement: 0.000" in out.read_text(encoding="utf-8")


def test_scorer_does_not_train_on_outcome_positions(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("outcome positions were passed to training or features")

    monkeypatch.setattr("aeo.ranksim.lambdamart.fit_lambdamart", boom)
    monkeypatch.setattr("aeo.ranksim.lambdamart.build_examples", boom)
    monkeypatch.setattr("aeo.ranksim.features.raw_features", boom)

    ledger = tmp_path / "preds.csv"
    main(
        [
            "track-record",
            "record",
            "--query",
            "example software",
            "--url",
            "https://example.com/a",
            "--predicted-rank",
            "2",
            "--predicted-of",
            "2",
            "--confidence",
            "UNSURE",
            "--ledger",
            str(ledger),
        ]
    )
    real = tmp_path / "later.csv"
    real.write_text("query,url,position\nexample software,https://example.com/a,1\n", encoding="utf-8")
    main(["track-record", "score", str(ledger), str(real)])
    saved = load_predictions(ledger)
    assert saved[0].label == "PREDICTED"
    assert saved[0].confidence == "UNSURE"


def test_record_rejects_a_url_without_a_scheme(tmp_path):
    with pytest.raises(ValueError, match="http"):
        record_prediction(
            query="example software",
            url="example.com/a",
            predicted_rank=1,
            predicted_of=1,
            ledger=tmp_path / "preds.csv",
        )
