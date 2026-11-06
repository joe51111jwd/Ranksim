"""Database ledger for a saved draft and a dated import. Synthetic runs are not recorded."""

from __future__ import annotations

from aeo.ranksim.ledger import record_outcome, record_prediction, score_ledger
from aeo.ranksim.store import open_session
from aeo.ranksim.workspace import DATASET_SYNTHETIC, _record_draft_ledger


def test_dated_import_can_score_a_later_prediction_and_synthetic_is_skipped(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(database)
    try:
        record_outcome(
            session,
            query="fence cost",
            observed_on="2020-01-02",
            provider="imported_csv",
            positions={"https://example.com/a": 2, "https://example.com/b": 1},
            depth=2,
            notes="Imported file. Not a live Google pull.",
        )
    finally:
        session.close()
    _record_draft_ledger(
        database,
        {
            "dataset": DATASET_SYNTHETIC,
            "search": "fence cost",
            "url": "https://example.com/a",
            "after": {"position": 1, "of": 2, "order": [{"page": False, "url": "https://example.com/b", "predicted": 2, "observed": 1}]},
        },
        "<p>synthetic</p>",
    )
    session = open_session(database)
    try:
        from aeo.ranksim.ledger import RsPrediction

        assert session.query(RsPrediction).count() == 0
    finally:
        session.close()
    _record_draft_ledger(
        database,
        {
            "dataset": "observed",
            "search": "fence cost",
            "url": "https://example.com/a",
            "after": {
                "position": 1,
                "of": 2,
                "confidence": "UNSURE",
                "confidence_note": "Held-out check failed. Every call stays UNSURE.",
                "order": [
                    {"page": True, "predicted": 1},
                    {"page": False, "url": "https://example.com/b", "predicted": 2, "observed": 1},
                ],
            },
            "notes": ["The draft has 10 words."],
            "calibration_note": "Held-out check failed. Every call stays UNSURE.",
            "calibration_held_out": False,
            "model_kind": "held-out model: trained on the other searches, never saw this one",
        },
        "<p>vinyl fence</p>",
    )
    session = open_session(database)
    try:
        from aeo.ranksim.ledger import RsPrediction

        row = session.query(RsPrediction).one()
        score = score_ledger(session)
    finally:
        session.close()
    assert score.predictions == 1
    assert score.resolved == 0
    assert score.accuracy is None
    assert row.payload["confidence"] == "UNSURE"
    assert row.payload["calls"][0]["confidence"] == "UNSURE"
    assert row.payload["calibration_note"] == "Held-out check failed. Every call stays UNSURE."
    assert "10 words" not in row.payload["calibration_note"]
    assert row.payload["candidates"][0]["html_sha256"] is None
    assert row.payload["provenance"]["instrument"] == "lambdamart"
    assert row.payload["provenance"]["held_out"] is False
    import aeo.ranksim.ledger as ledger

    assert not hasattr(ledger, "record_check")
