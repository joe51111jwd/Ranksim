"""The ledger reads and writes the same rows as the integration branch (PR #5) and dates each
prediction by when it was really written. Fixtures are synthetic."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from aeo.ranksim.cli import main
from aeo.ranksim.ledger import (
    DEFAULT_PROJECT_SLUG,
    Call,
    LedgerError,
    RsOutcome,
    RsPrediction,
    _row_hash,
    record_check,
    record_outcome,
    record_prediction,
    render_score,
    score_ledger,
    verify_ledger,
)
from aeo.ranksim.store import open_session

QUERY = "dirt work software"
SUBJECT = "https://ours.example/draft"
NOW = {"now": datetime(2026, 11, 30, 12, tzinfo=timezone.utc)}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    import aeo.ranksim.ledger as ledger

    NOW["now"] = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ledger, "_now", lambda: NOW["now"])


def _predict(session, *, written, stated=None, slug=DEFAULT_PROJECT_SLUG, dataset=None, rival="https://a.example/"):
    NOW["now"] = written
    provenance = {"model": "test"} if dataset is None else {"model": "test", "dataset": dataset}
    return record_prediction(
        session,
        kind="draft_check",
        query=QUERY,
        subject_url=SUBJECT,
        subject_html="<html>draft</html>",
        predicted_rank=1,
        predicted_of=2,
        candidates=[{"url": rival, "html_sha256": None, "labeled_position": None}],
        calls=[Call(rival, "above", 1.0, "UNSURE")],
        confidence="UNSURE",
        calibration_note="test",
        provenance=provenance,
        created_at=stated,
        project_slug=slug,
    )


def _observe(session, day, *, slug=DEFAULT_PROJECT_SLUG):
    NOW["now"] = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
    record_outcome(session, query=QUERY, observed_on=day, provider="t",
                   positions={SUBJECT: 1, "https://a.example/": 2}, depth=10, project_slug=slug)


def test_version_two_hash_matches_the_integration_branch_formula():
    # Expected values were computed by running PR #5's ledger._row_hash (ad8020f) on these inputs.
    payload = {
        "calls": [{"competitor_url": "https://rival.example/", "predicted": "above", "score_gap": 0.4, "confidence": "UNSURE", "note": ""}],
        "ledger_hash_version": 2,
        "recorded_at": "2026-11-05T00:00:00+00:00",
        "predicted_rank": 1,
    }
    args = ("0" * 64, "2026-11-05T00:00:00+00:00", "draft_check", QUERY, SUBJECT)
    assert _row_hash(*args, payload, "default") == "e426cea40827549c1529cc6e014e1e714eb13838e498e862c3aad001f942bddd"
    assert _row_hash(*args, payload, "") == "38ca53911bb0098130bc028b158e8f011ce755ad7d02b724cd996eca8b0714ff"
    legacy = {key: value for key, value in payload.items() if key != "ledger_hash_version"}
    assert _row_hash(*args, legacy, "default") == "6119c54cb8e6573b24a68887fc3a87b52e2728eef430bf13ccf14b17786e09d0"


def test_the_slug_is_bound_and_legacy_rows_still_verify(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc))
    # A legacy (version 1) row written by older code: no version key, no slug in the hash.
    previous = session.query(RsPrediction).one()
    payload = {"predicted_rank": 1, "calls": []}
    stamp = "2026-11-06T00:00:00+00:00"
    session.add(RsPrediction(created_at=stamp, kind="draft_check", query=QUERY, subject_url=SUBJECT, payload=payload,
                             prev_hash=previous.row_hash, row_hash=_row_hash(previous.row_hash, stamp, "draft_check", QUERY, SUBJECT, payload)))
    session.commit()
    ok, message = verify_ledger(session)
    assert ok and "1 legacy rows" in message
    previous.project_slug = "someone-else"
    session.commit()
    ok, message = verify_ledger(session)
    assert not ok and "Prediction 1" in message
    session.close()


def test_outcomes_resolve_only_within_one_named_project(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc), slug="")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc), slug="other")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc))
    _observe(session, "2026-11-12", slug="")
    _observe(session, "2026-11-12")
    score = score_ledger(session)
    # The default-project call resolves; the other project's waits; the blank one is legacy, not waiting.
    assert (score.calls, score.resolved, score.no_outcome_yet, score.legacy) == (2, 1, 1, 1)
    assert score_ledger(session, project_slug="other").resolved == 0
    session.close()


def test_a_draft_check_and_a_cli_observation_share_the_default_project(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    NOW["now"] = datetime(2026, 11, 5, tzinfo=timezone.utc)
    rival = SimpleNamespace(url="https://a.example/", outcome="beats", score_gap=0.5, confidence="UNSURE", stored_position=2)
    result = SimpleNamespace(error=None, predicted_rank=1, predicted_of=2, competitors=[rival], url=SUBJECT, query=QUERY,
                             candidate_hashes={}, confidence="UNSURE", confidence_note="note")
    row = record_check(session, result, html="<html>draft</html>", calibration=None, provenance={"model": "test"})
    assert row.project_slug == DEFAULT_PROJECT_SLUG
    session.close()
    NOW["now"] = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
    serp = tmp_path / "later.csv"
    serp.write_text(f"query,url,position\n{QUERY},{SUBJECT},1\n{QUERY},https://a.example/,3\n")
    main(["--database", db, "ledger", "observe", str(serp), "--observed-on", "2026-11-20", "--provider", "manual"])
    main(["--database", db, "ledger", "score"])
    out = capsys.readouterr().out
    assert "for project default" in out and "Right 1 of 1 resolved calls" in out
    session = open_session(db)
    outcome = session.query(RsOutcome).one()
    assert (outcome.source_file, len(outcome.source_sha256), outcome.project_slug) == ("later.csv", 64, "default")
    session.close()


def test_a_backdated_prediction_is_dated_from_when_it_was_written(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    # Stated as Nov 7 (a protocol time) but written on Nov 9 after the Nov 9 results were known.
    _predict(session, written=datetime(2026, 11, 9, 15, tzinfo=timezone.utc), stated=datetime(2026, 11, 7, 4, tzinfo=timezone.utc))
    _observe(session, "2026-11-09")
    score = score_ledger(session)
    assert score.backdated == 1 and score.resolved == 0 and score.no_outcome_yet == 1
    assert "written more than an hour after their stated time" in render_score(score)
    _observe(session, "2026-11-11")
    assert score_ledger(session).resolved == 1
    # The write time is inside the hashed payload, so it cannot be moved back unnoticed.
    row = session.query(RsPrediction).one()
    payload = dict(row.payload)
    payload["recorded_at"] = "2026-11-07T04:00:00+00:00"
    row.payload = payload
    session.commit()
    assert not verify_ledger(session)[0]
    session.close()


def test_synthetic_predictions_are_counted_but_not_scored(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc), dataset="synthetic")
    _observe(session, "2026-11-12")
    score = score_ledger(session)
    assert (score.unscored, score.calls, score.resolved) == (1, 0, 0)
    session.close()


def test_an_outcome_cannot_be_observed_in_the_future(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    NOW["now"] = datetime(2026, 11, 10, tzinfo=timezone.utc)
    with pytest.raises(LedgerError, match="in the future"):
        record_outcome(session, query=QUERY, observed_on="2026-11-20", provider="t", positions={SUBJECT: 1}, depth=10)
    session.close()


def test_a_not_null_failure_is_reported_not_retried_as_a_fork(tmp_path):
    db = tmp_path / "l.db"
    import sqlite3

    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE rs_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at VARCHAR(40) NOT NULL, "
        "kind VARCHAR(32) NOT NULL, query VARCHAR(500) NOT NULL, subject_url VARCHAR(2000) NOT NULL, "
        "project_slug VARCHAR(200) NOT NULL, payload JSON NOT NULL, prev_hash VARCHAR(64) NOT NULL UNIQUE, "
        "row_hash VARCHAR(64) NOT NULL UNIQUE, reviewer TEXT NOT NULL)"
    )
    connection.commit()
    connection.close()
    session = open_session(f"sqlite:///{db}")
    with pytest.raises(LedgerError, match="NOT NULL") as raised:
        _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert "5 times" not in str(raised.value)
    session.close()


def test_a_database_made_by_the_integration_branch_accepts_these_writes(tmp_path):
    """PR #5 declares project_slug NOT NULL with no server default. This writer always sets it."""
    db = tmp_path / "l.db"
    import sqlite3

    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE rs_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at VARCHAR(40) NOT NULL, "
        "kind VARCHAR(32) NOT NULL, query VARCHAR(500) NOT NULL, subject_url VARCHAR(2000) NOT NULL, "
        "project_slug VARCHAR(200) NOT NULL, payload JSON NOT NULL, prev_hash VARCHAR(64) NOT NULL UNIQUE, "
        "row_hash VARCHAR(64) NOT NULL UNIQUE)"
    )
    connection.commit()
    connection.close()
    session = open_session(f"sqlite:///{db}")
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert verify_ledger(session)[0]
    session.close()


def test_an_older_ledger_database_gains_the_new_columns(tmp_path):
    db = tmp_path / "l.db"
    import sqlite3

    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE rs_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at DATETIME, observed_on VARCHAR(10) NOT NULL, "
        "query VARCHAR(500) NOT NULL, provider VARCHAR(64) NOT NULL, depth INTEGER, positions JSON NOT NULL, notes TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO rs_outcomes (observed_on, query, provider, depth, positions, notes) VALUES ('2026-11-01', 'q', 't', 10, '{}', '')"
    )
    connection.commit()
    connection.close()
    session = open_session(f"sqlite:///{db}")
    old = session.query(RsOutcome).one()
    assert (old.project_slug, old.source_file, old.source_sha256, old.uploaded_at) == ("", "", "", "")
    columns = {row[1] for row in session.execute(text("PRAGMA table_info(rs_outcomes)"))}
    assert {"project_slug", "source_file", "source_sha256", "uploaded_at"} <= columns
    session.close()


def test_an_outcome_dated_after_every_time_zone_is_refused_at_the_boundary(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    NOW["now"] = datetime(2026, 11, 10, 0, 30, tzinfo=timezone.utc)  # 14:30 on Nov 10 in UTC+14, nowhere Nov 11 yet
    with pytest.raises(LedgerError, match="in the future"):
        record_outcome(session, query=QUERY, observed_on="2026-11-11", provider="t", positions={SUBJECT: 1}, depth=10)
    NOW["now"] = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)  # now Nov 11 in UTC+14
    record_outcome(session, query=QUERY, observed_on="2026-11-11", provider="t", positions={SUBJECT: 1}, depth=10)
    session.close()


def test_an_outcome_recorded_before_the_prediction_never_resolves_it(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    # Inserted directly (bypassing the date check): dated later, but already in the ledger.
    session.add(RsOutcome(recorded_at=datetime(2026, 11, 4, tzinfo=timezone.utc), observed_on="2026-11-12", query=QUERY,
                          provider="t", depth=10, positions={SUBJECT: 1, "https://a.example/": 2}, notes="", project_slug=DEFAULT_PROJECT_SLUG))
    session.commit()
    _predict(session, written=datetime(2026, 11, 5, tzinfo=timezone.utc))
    assert score_ledger(session).resolved == 0
    session.close()


def test_a_published_head_catches_removal_of_the_newest_rows(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    for day in (5, 6, 7):
        _predict(session, written=datetime(2026, 11, day, tzinfo=timezone.utc))
    head = session.query(RsPrediction).order_by(RsPrediction.id.desc()).first().row_hash
    session.execute(text("DELETE FROM rs_predictions WHERE id = 3"))
    session.commit()
    assert verify_ledger(session)[0]  # the remaining chain is intact on its own
    session.close()
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "verify", "--expect-head", head])
    assert "not in this ledger" in capsys.readouterr().out


def test_rows_from_another_writer_without_a_write_time_are_not_scored(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    payload = {"calls": [{"competitor_url": "https://a.example/", "predicted": "above", "score_gap": 1.0, "confidence": "UNSURE"}],
               "ledger_hash_version": 2, "provenance": {}}
    stamp = "2026-11-07T04:00:00+00:00"
    session.add(RsPrediction(created_at=stamp, kind="forward", query=QUERY, subject_url=SUBJECT, project_slug=DEFAULT_PROJECT_SLUG,
                             payload=payload, prev_hash="0" * 64,
                             row_hash=_row_hash("0" * 64, stamp, "forward", QUERY, SUBJECT, payload, DEFAULT_PROJECT_SLUG)))
    session.commit()
    _observe(session, "2026-11-09")
    score = score_ledger(session)
    assert (score.undated, score.resolved, score.calls) == (1, 0, 0)
    assert "carry no write time" in render_score(score)
    session.close()


def test_only_a_unique_violation_counts_as_a_fork_race():
    from aeo.ranksim.ledger import _is_fork_race

    class Orig(Exception):
        def __init__(self, message, sqlstate=None):
            super().__init__(message)
            self.sqlstate = sqlstate

    class Wrapped(Exception):
        def __init__(self, orig):
            self.orig = orig

    not_null = Orig('null value in column "x" violates not-null constraint\nDETAIL: Failing row contains (duplicate content checker, unique).', "23502")
    assert not _is_fork_race(Wrapped(not_null))
    assert _is_fork_race(Wrapped(Orig('duplicate key value violates unique constraint "rs_predictions_prev_hash_key"', "23505")))
    assert _is_fork_race(Wrapped(Orig("UNIQUE constraint failed: rs_predictions.prev_hash")))
    assert not _is_fork_race(Wrapped(Orig("NOT NULL constraint failed: rs_predictions.reviewer\nduplicate")))

