"""The prediction ledger is append-only, tamper-evident, and scored honestly."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from aeo.ranksim.cli import main
from aeo.ranksim.ledger import Call, RsPrediction, record_outcome, record_prediction, score_ledger, verify_ledger
from aeo.ranksim.store import open_session


def _predict(session, when, calls, query="dirt work software", subject="https://ours.example/draft"):
    return record_prediction(
        session,
        kind="draft_check",
        query=query,
        subject_url=subject,
        subject_html="<html>draft</html>",
        predicted_rank=1,
        predicted_of=1 + len(calls),
        candidates=[{"url": call.competitor_url, "html_sha256": None, "labeled_position": None} for call in calls],
        calls=calls,
        confidence="UNSURE",
        calibration_note="test",
        provenance={"model": "test"},
        created_at=datetime(*when, tzinfo=timezone.utc),
    )


def test_chain_detects_edits_and_deletions(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    for day in (1, 2, 3):
        _predict(session, (2026, 11, day), [Call("https://rival.example/", "above", 0.4, "UNSURE")])
    ok, message = verify_ledger(session)
    assert ok and "3 predictions" in message
    row = session.get(RsPrediction, 2)
    payload = dict(row.payload)
    payload["predicted_rank"] = 2
    row.payload = payload
    session.commit()
    ok, message = verify_ledger(session)
    assert not ok and "Prediction 2" in message
    session.close()

    other = open_session(f"sqlite:///{tmp_path / 'm.db'}")
    for day in (1, 2, 3):
        _predict(other, (2026, 11, day), [Call("https://rival.example/", "above", 0.4, "UNSURE")])
    other.execute(text("DELETE FROM rs_predictions WHERE id = 2"))
    other.commit()
    ok, message = verify_ledger(other)
    assert not ok and "removed or reordered" in message
    other.close()


def test_scoring_uses_only_later_observations_and_counts_unresolved(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(
        session,
        (2026, 11, 5),
        [
            Call("https://a.example/", "above", 1.0, "UNSURE"),  # will be right
            Call("https://b.example/", "above", 0.2, "UNSURE"),  # will be wrong
            Call("https://c.example/", "below", 0.3, "UNSURE"),  # c not observed
        ],
    )
    # Same-day observation must not count: it is not after the prediction.
    record_outcome(session, query="dirt work software", observed_on="2026-11-05", provider="t",
                   positions={"https://ours.example/draft": 9, "https://a.example/": 1}, depth=10)
    score = score_ledger(session)
    assert score.resolved == 0 and score.no_outcome_yet == 3
    record_outcome(session, query="dirt work software", observed_on="2026-11-12", provider="t",
                   positions={"https://ours.example/draft": 3, "https://a.example/": 5, "https://b.example/": 2}, depth=None)
    score = score_ledger(session)
    assert (score.calls, score.resolved, score.correct, score.unresolved) == (3, 2, 1, 1)
    assert score.accuracy == 0.5
    session.close()


def test_depth_resolves_a_page_outside_the_collected_results(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(session, (2026, 11, 5), [Call("https://c.example/", "above", 0.3, "UNSURE")])
    record_outcome(session, query="dirt work software", observed_on="2026-11-12", provider="t",
                   positions={"https://ours.example/draft": 4}, depth=10)
    score = score_ledger(session)
    assert (score.resolved, score.correct) == (1, 1)
    session.close()


def test_ledger_cli_observe_requires_a_date_and_scores(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    _predict(session, (2026, 11, 5), [Call("https://a.example/", "above", 1.0, "UNSURE")])
    session.close()
    serp = tmp_path / "later.csv"
    serp.write_text("query,url,position\ndirt work software,https://ours.example/draft,2\ndirt work software,https://a.example/,4\n")
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "observe", str(serp), "--provider", "manual"])
    main(["--database", db, "ledger", "observe", str(serp), "--observed-on", "2026-11-20", "--provider", "manual"])
    main(["--database", db, "ledger", "score"])
    main(["--database", db, "ledger", "verify"])
    out = capsys.readouterr().out
    assert "Right 1 of 1 resolved calls" in out
    assert "chain intact" in out
