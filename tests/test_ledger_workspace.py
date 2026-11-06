"""Database ledger for a saved draft and a dated import. Synthetic runs are not recorded."""

from __future__ import annotations

from datetime import datetime, timezone

from aeo.ranksim.ledger import _row_hash, _sha, record_outcome, record_prediction, score_ledger, verify_ledger
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.store import open_session
from aeo.ranksim.tester import stored_html
from aeo.ranksim.workspace import DATASET_SYNTHETIC, _candidate_html_sha256, _record_draft_ledger


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


def test_blank_project_stays_unresolved_against_a_named_outcome(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(database)
    try:
        record_outcome(
            session,
            query="fence cost",
            observed_on="2020-06-01",
            provider="imported_csv",
            positions={"https://example.com/a": 1, "https://example.com/b": 2},
            depth=2,
            project_slug="acme",
        )
        record_prediction(
            session,
            kind="workspace_draft",
            query="fence cost",
            subject_url="https://example.com/a",
            subject_html="<p>x</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[{"url": "https://example.com/b", "html_sha256": None, "labeled_position": 2}],
            calls=[],
            confidence="UNSURE",
            calibration_note="blank project",
            provenance={"dataset": "observed"},
            project_slug="",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        from aeo.ranksim.ledger import Call

        record_prediction(
            session,
            kind="workspace_draft",
            query="fence cost",
            subject_url="https://example.com/a",
            subject_html="<p>x</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[{"url": "https://example.com/b", "html_sha256": None, "labeled_position": 2}],
            calls=[Call("https://example.com/b", "above", None, "UNSURE")],
            confidence="UNSURE",
            calibration_note="named project",
            provenance={"dataset": "observed"},
            project_slug="acme",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        score = score_ledger(session)
    finally:
        session.close()
    assert score.resolved == 1
    assert score.correct == 1


def test_version_two_hash_binds_the_slug_and_legacy_rows_stay_original(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(database)
    try:
        current = record_prediction(
            session,
            kind="workspace_draft",
            query="fence cost",
            subject_url="https://example.com/a",
            subject_html="<p>x</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[],
            calls=[],
            confidence="UNSURE",
            calibration_note="note",
            provenance={"dataset": "observed"},
            project_slug=" acme ",
        )
        assert current.project_slug == "acme"
        assert current.payload["ledger_hash_version"] == 2
        ok, text = verify_ledger(session)
        assert ok and "legacy" not in text
        current.project_slug = "beta"
        session.commit()
        ok, text = verify_ledger(session)
        assert not ok and "contents changed" in text
        current.project_slug = "acme"
        session.commit()
        legacy_payload = {"confidence": "UNSURE", "candidates": []}
        legacy_hash = _row_hash(current.row_hash, "2020-01-01T00:00:00+00:00", "workspace_draft", "fence cost", "https://example.com/a", legacy_payload)
        from aeo.ranksim.ledger import RsPrediction

        session.add(
            RsPrediction(
                created_at="2020-01-01T00:00:00+00:00",
                kind="workspace_draft",
                query="fence cost",
                subject_url="https://example.com/a",
                project_slug="",
                payload=legacy_payload,
                prev_hash=current.row_hash,
                row_hash=legacy_hash,
            )
        )
        session.commit()
        ok, text = verify_ledger(session)
        assert ok and "1 legacy rows" in text
        legacy = session.query(RsPrediction).filter(RsPrediction.row_hash == legacy_hash).one()
        legacy.project_slug = "invented"
        session.commit()
        ok, _text = verify_ledger(session)
        assert ok
        legacy.project_slug = ""
        session.commit()
    finally:
        session.close()


def test_candidate_hash_uses_stored_raw_html(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    html = "<html><body><p>Vinyl  fence\ncost</p></body></html>"
    session = open_session(database)
    try:
        store_fetched_page(session, FetchResult(url="https://acme.example/vinyl", html=html, status="fetched"))
    finally:
        session.close()
    found = stored_html(database, "https://acme.example/vinyl")
    assert found is not None and found[0] == html
    assert _candidate_html_sha256(database, "https://acme.example/vinyl") == _sha(found[0])
    assert _candidate_html_sha256(database, "https://missing.example/none") is None
