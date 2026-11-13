"""Private project: UNSURE on the screens, one database ledger, a later CSV can resolve a call."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import aeo.ranksim.ledger as ledger
from aeo.ranksim.ledger import RsOutcome, RsPrediction, record_outcome, score_ledger
from aeo.ranksim.loop import FetchResult
from aeo.ranksim.project import load_project, pull
from aeo.ranksim.serve import create_app
from aeo.ranksim.store import open_session
from aeo.ranksim.workspace import DATASET_HISTORICAL, DATASET_NONE, DATASET_OBSERVED, DATASET_SYNTHETIC, WorkspaceMeta, record_observed_import
from test_workspace import make_settings, token

QUERY = "vinyl fence installation cost"
OTHER = "cedar fence lifespan years"


def _html(title: str, paragraph: str) -> str:
    body = " ".join([paragraph] * 8)
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"


def test_dataset_gates_for_imported_observations(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    project = type("P", (), {"slug": "acme"})()
    data = b"query,url,position\nfence cost,https://a.example/x,1\nfence cost,https://b.example/y,2\n"
    for dataset in (DATASET_SYNTHETIC, DATASET_HISTORICAL):
        written = record_observed_import(
            database, project, WorkspaceMeta(dataset=dataset), data, "positions.csv", "2099-01-15"
        )
        assert written == 0
    written = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_NONE), data, "positions.csv", "2099-01-15"
    )
    assert written == 1
    again = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_OBSERVED), data, "positions.csv", "2099-01-15"
    )
    assert again == 0
    written = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_OBSERVED), data, "positions.csv", "2099-01-15", public_demo=True
    )
    assert written == 0
    written = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_OBSERVED), data, "positions.csv", "2099-01-15"
    )
    assert written == 0
    session = open_session(database)
    try:
        row = session.query(RsOutcome).one()
    finally:
        session.close()
    assert row.project_slug == "acme"
    assert row.source_file == "positions.csv"
    assert row.source_sha256 == hashlib.sha256(data).hexdigest()
    assert row.uploaded_at
    assert row.provider == "imported_csv"
    assert "Not a live Google pull" in row.notes


def test_older_outcome_table_gains_provenance_columns(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE rs_outcomes (
            id INTEGER PRIMARY KEY,
            recorded_at DATETIME,
            observed_on VARCHAR(10),
            query VARCHAR(500),
            provider VARCHAR(64),
            depth INTEGER,
            positions JSON,
            notes TEXT
        )"""
    )
    connection.execute(
        "INSERT INTO rs_outcomes (observed_on, query, provider, depth, positions, notes) VALUES (?, ?, ?, ?, ?, ?)",
        ("2020-01-02", "fence cost", "imported_csv", 2, "{}", "old row"),
    )
    connection.commit()
    connection.close()
    session = open_session(f"sqlite:///{path}")
    try:
        old = session.query(RsOutcome).one()
        assert not old.project_slug
        assert not old.source_sha256
        record_outcome(
            session,
            query="fence cost",
            observed_on="2099-01-15",
            provider="imported_csv",
            positions={"https://a.example/x": 1},
            depth=1,
            project_slug="acme",
            source_file="later.csv",
            source_sha256="abc",
            uploaded_at="2099-01-15T00:00:00+00:00",
        )
        rows = session.query(RsOutcome).order_by(RsOutcome.id).all()
    finally:
        session.close()
    assert rows[0].notes == "old row"
    assert rows[1].project_slug == "acme"
    assert rows[1].source_file == "later.csv"


def test_older_prediction_table_gains_a_blank_project(tmp_path):
    path = tmp_path / "old-pred.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE rs_predictions (
            id INTEGER PRIMARY KEY,
            created_at VARCHAR(40),
            kind VARCHAR(32),
            query VARCHAR(500),
            subject_url VARCHAR(2000),
            payload JSON,
            prev_hash VARCHAR(64) UNIQUE,
            row_hash VARCHAR(64) UNIQUE
        )"""
    )
    connection.execute(
        "INSERT INTO rs_predictions (created_at, kind, query, subject_url, payload, prev_hash, row_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("2020-01-01T00:00:00+00:00", "workspace_draft", "fence cost", "https://a.example/x", "{}", "0" * 64, "1" * 64),
    )
    connection.commit()
    connection.close()
    session = open_session(f"sqlite:///{path}")
    try:
        old = session.query(RsPrediction).one()
        assert not old.project_slug
    finally:
        session.close()


def test_first_dated_import_on_a_none_project_is_recorded_once(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False))
    csrf = token(client)
    made = client.post(
        "/projects",
        data={"csrf": csrf, "name": "Fresh", "site": "fresh.example", "searches": "fence cost"},
        follow_redirects=False,
    )
    assert made.status_code == 303
    data = b"query,url,position\nfence cost,https://fresh.example/a,1\nfence cost,https://rival.example/a,2\n"
    imported = client.post(
        "/p/fresh/import",
        data={"csrf": csrf, "snapshot_date": "2020-01-03"},
        files={"csv_file": ("first.csv", data, "text/csv")},
        follow_redirects=False,
    )
    assert imported.status_code == 303, imported.text[:300]
    page = client.get("/p/fresh")
    assert "OBSERVED" in page.text
    assert "Not a live Google pull" not in page.text
    session = open_session(settings.ranksim_database_url)
    try:
        row = session.query(RsOutcome).one()
    finally:
        session.close()
    assert row.project_slug == "fresh"
    assert row.provider == "imported_csv"
    assert row.source_file == "first.csv"
    assert row.source_sha256 == hashlib.sha256(data).hexdigest()
    assert row.observed_on == "2020-01-03"
    assert row.uploaded_at
    assert "Not a live Google pull" in row.notes
    again = client.post(
        "/p/fresh/import",
        data={"csrf": csrf, "snapshot_date": "2020-01-03"},
        files={"csv_file": ("first.csv", data, "text/csv")},
        follow_redirects=False,
    )
    assert again.status_code == 303
    session = open_session(settings.ranksim_database_url)
    try:
        assert session.query(RsOutcome).count() == 1
    finally:
        session.close()


def test_historical_import_is_not_scored_as_another_projects_csv(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False))
    csrf = token(client)
    client.post(
        "/projects",
        data={"csrf": csrf, "name": "Real", "site": "real.example", "searches": "fence cost"},
        follow_redirects=False,
    )
    client.post(
        "/projects",
        data={"csrf": csrf, "name": "History", "site": "history.example", "searches": "fence cost"},
        follow_redirects=False,
    )
    from aeo.ranksim.workspace import DATASET_HISTORICAL, load_meta, save_meta

    history = load_project("history", root=tmp_path / "projects")
    meta = load_meta(history)
    meta.dataset = DATASET_HISTORICAL
    save_meta(history, meta)
    data = b"query,url,position\nfence cost,https://real.example/a,1\nfence cost,https://rival.example/a,2\n"
    real = client.post(
        "/p/real/import",
        data={"csrf": csrf, "snapshot_date": "2020-02-02"},
        files={"csv_file": ("real.csv", data, "text/csv")},
        follow_redirects=False,
    )
    assert real.status_code == 303
    demo_import = client.post(
        "/p/history/import",
        data={"csrf": csrf, "snapshot_date": "2020-02-02"},
        files={"csv_file": ("history.csv", data, "text/csv")},
        follow_redirects=False,
    )
    assert demo_import.status_code == 303
    session = open_session(settings.ranksim_database_url)
    try:
        assert session.query(RsOutcome).count() == 1
        assert session.query(RsOutcome).one().project_slug == "real"
        ledger.record_prediction(
            session,
            kind="workspace_draft",
            query="fence cost",
            subject_url="https://history.example/a",
            subject_html="<p>x</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[{"url": "https://rival.example/a", "html_sha256": None, "labeled_position": 2}],
            calls=[ledger.Call("https://rival.example/a", "above", None, "UNSURE", "historical")],
            confidence="UNSURE",
            calibration_note="historical demo",
            provenance={"dataset": "historical", "source": "workspace"},
            project_slug="history",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        score = score_ledger(session)
    finally:
        session.close()
    assert score.resolved == 0
    assert score.calls == 0


def test_private_draft_stays_unsure_and_a_later_csv_resolves_it(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False)
    client = TestClient(app)
    csrf = token(client)
    pages = {
        "https://acme.example/vinyl": _html("Vinyl fence installation cost", "The vinyl fence installation cost covers panels, posts, and labor for a vinyl fence."),
        "https://rival.example/vinyl": _html("Vinyl fence installation cost", "A rival guide to vinyl fence installation cost, panels, posts, and labor."),
        "https://other.example/vinyl": _html("Vinyl fence installation cost", "Another page about vinyl fence installation cost, panels, posts, and labor."),
        "https://acme.example/cedar": _html("Cedar fence lifespan years", "A cedar fence lifespan is measured in years of rot, stain, and weather."),
        "https://rival.example/cedar": _html("Cedar fence lifespan years", "Rival notes on cedar fence lifespan, years, rot, stain, and weather."),
        "https://other.example/cedar": _html("Cedar fence lifespan years", "More notes on cedar fence lifespan, years, rot, stain, and weather."),
    }
    first = (
        "query,url,position\n"
        f"{QUERY},https://acme.example/vinyl,2\n"
        f"{QUERY},https://rival.example/vinyl,1\n"
        f"{QUERY},https://other.example/vinyl,3\n"
        f"{OTHER},https://acme.example/cedar,1\n"
        f"{OTHER},https://rival.example/cedar,2\n"
        f"{OTHER},https://other.example/cedar,3\n"
    ).encode()
    made = client.post(
        "/projects",
        data={"csrf": csrf, "name": "Acme", "site": "acme.example", "competitors": "rival.example", "searches": f"{QUERY}\n{OTHER}"},
        files={"csv_file": ("positions.csv", first, "text/csv")},
        follow_redirects=False,
    )
    assert made.status_code == 303
    session = open_session(settings.ranksim_database_url)
    try:
        assert session.query(RsOutcome).count() == 0
        assert session.query(RsPrediction).count() == 0
    finally:
        session.close()

    def fetch(urls):
        return [
            FetchResult(url=url, html=pages[url], status="fetched", fetch_ms=1.0) if url in pages
            else FetchResult(url=url, html="", status="error", error="not in the fixture")
            for url in urls
        ]

    project = load_project("acme", root=tmp_path / "projects")
    pull(project, settings, skip_google=True, database=settings.ranksim_database_url, fetcher=fetch, workers=2, delay_seconds=0, engines=[])
    app.state.ui.forget("acme")

    compare = client.get("/p/acme/comparisons", params={"q": QUERY})
    assert compare.status_code == 200
    assert "UNSURE" in compare.text
    assert "CLEARER" not in compare.text

    draft_html = pages["https://acme.example/vinyl"]
    stamped = ledger._now
    ledger._now = lambda: datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    try:
        ran = client.post(
            "/p/acme/draft",
            data={"csrf": csrf, "q": QUERY, "which": "all", "original_mode": "none", "url": "https://acme.example/vinyl", "draft_html": draft_html},
            follow_redirects=False,
        )
    finally:
        ledger._now = stamped
    assert ran.status_code == 303, ran.text[:500]
    result = client.get(ran.headers["location"])
    assert result.status_code == 200
    assert "UNSURE" in result.text
    assert "CLEARER" not in result.text
    assert "Saved inputs" in result.text
    again = TestClient(create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False))
    reloaded = again.get(ran.headers["location"])
    assert reloaded.status_code == 200 and "UNSURE" in reloaded.text and "CLEARER" not in reloaded.text

    session = open_session(settings.ranksim_database_url)
    try:
        prediction = session.query(RsPrediction).one()
    finally:
        session.close()
    payload = prediction.payload
    assert prediction.project_slug == "acme"
    assert payload["confidence"] == "UNSURE"
    assert all(call["confidence"] == "UNSURE" for call in payload["calls"])
    assert "words" not in payload["calibration_note"].lower()
    assert "UNSURE" in payload["calibration_note"]
    assert payload["provenance"]["instrument"] == "lambdamart"
    assert payload["provenance"]["held_out"] is False
    assert payload["provenance"]["dataset"] == "observed"
    from aeo.ranksim.ledger import _sha
    from aeo.ranksim.tester import stored_html

    hashes = {row["url"]: row["html_sha256"] for row in payload["candidates"]}
    assert hashes
    for url, digest in hashes.items():
        found = stored_html(settings.ranksim_database_url, url)
        assert found is not None
        assert found[0] == pages[url]
        assert digest == _sha(found[0])
    from aeo.ranksim.workspace import _candidate_html_sha256

    assert _candidate_html_sha256(settings.ranksim_database_url, "https://missing.example/none") is None
    assert prediction.payload["subject_html_sha256"] == hashlib.sha256(draft_html.encode("utf-8")).hexdigest()

    later = (
        "query,url,position\n"
        f"{QUERY},https://acme.example/vinyl,1\n"
        f"{QUERY},https://rival.example/vinyl,2\n"
        f"{QUERY},https://other.example/vinyl,3\n"
    ).encode()
    imported = client.post(
        "/p/acme/import",
        data={"csrf": csrf, "snapshot_date": "2020-01-04", "add_searches": ""},
        files={"csv_file": ("later.csv", later, "text/csv")},
        follow_redirects=False,
    )
    assert imported.status_code == 303, imported.text[:400]
    session = open_session(settings.ranksim_database_url)
    try:
        outcome = session.query(RsOutcome).one()
        score = score_ledger(session)
    finally:
        session.close()
    assert outcome.project_slug == "acme"
    assert outcome.source_file == "later.csv"
    assert outcome.source_sha256 == hashlib.sha256(later).hexdigest()
    assert outcome.observed_on == "2020-01-04"
    assert outcome.uploaded_at
    assert "Not a live Google pull" in outcome.notes
    assert score.predictions == 1
    assert score.resolved > 0
    assert score.accuracy is not None
    def correct_if(positions: dict[str, int]) -> int:
        subject = positions["https://acme.example/vinyl"]
        matched = 0
        for call in payload["calls"]:
            rival = positions[call["competitor_url"]]
            actual = "above" if subject < rival else "below"
            if actual == call["predicted"]:
                matched += 1
        return matched

    acme_positions = {"https://acme.example/vinyl": 1, "https://rival.example/vinyl": 2, "https://other.example/vinyl": 3}
    beta_positions = {"https://acme.example/vinyl": 5, "https://rival.example/vinyl": 1, "https://other.example/vinyl": 2}
    assert score.correct == correct_if(acme_positions)
    other = client.post(
        "/projects",
        data={"csrf": csrf, "name": "Beta", "site": "beta.example", "searches": QUERY},
        follow_redirects=False,
    )
    assert other.status_code == 303
    beta_csv = (
        "query,url,position\n"
        f"{QUERY},https://acme.example/vinyl,5\n"
        f"{QUERY},https://rival.example/vinyl,1\n"
        f"{QUERY},https://other.example/vinyl,2\n"
    ).encode()
    beta = client.post(
        "/p/beta/import",
        data={"csrf": csrf, "snapshot_date": "2020-01-03"},
        files={"csv_file": ("beta.csv", beta_csv, "text/csv")},
        follow_redirects=False,
    )
    assert beta.status_code == 303, beta.text[:300]
    session = open_session(settings.ranksim_database_url)
    try:
        ledger.record_prediction(
            session,
            kind="workspace_draft",
            query=QUERY,
            subject_url="https://acme.example/vinyl",
            subject_html="<p>control</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[{"url": "https://rival.example/vinyl", "html_sha256": None, "labeled_position": 2}],
            calls=[ledger.Call("https://rival.example/vinyl", "above", None, "UNSURE")],
            confidence="UNSURE",
            calibration_note="same-project control",
            provenance={"dataset": "observed", "source": "workspace"},
            project_slug="acme",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        ledger.record_prediction(
            session,
            kind="workspace_draft",
            query=QUERY,
            subject_url="https://acme.example/vinyl",
            subject_html="<p>blank</p>",
            predicted_rank=1,
            predicted_of=2,
            candidates=[{"url": "https://rival.example/vinyl", "html_sha256": None, "labeled_position": 2}],
            calls=[ledger.Call("https://rival.example/vinyl", "above", None, "UNSURE")],
            confidence="UNSURE",
            calibration_note="legacy blank project",
            provenance={"dataset": "observed", "source": "workspace"},
            project_slug="",
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        mixed = score_ledger(session)
        beta_rows = session.query(RsOutcome).filter(RsOutcome.project_slug == "beta").count()
    finally:
        session.close()
    assert beta_rows == 1
    # Beta is dated 2020-01-03, inside the scoring window and before Acme's 2020-01-04 file.
    # The extra Acme call is right only against Acme's positions. A blank project stays unresolved.
    assert mixed.correct == correct_if(acme_positions) + 1
    assert mixed.resolved == score.resolved + 1
