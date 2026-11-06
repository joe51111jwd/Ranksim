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
    for dataset in (DATASET_NONE, DATASET_SYNTHETIC, DATASET_HISTORICAL):
        written = record_observed_import(
            database, project, WorkspaceMeta(dataset=dataset), data, "positions.csv", "2099-01-15"
        )
        assert written == 0
    written = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_OBSERVED), data, "positions.csv", "2099-01-15", public_demo=True
    )
    assert written == 0
    written = record_observed_import(
        database, project, WorkspaceMeta(dataset=DATASET_OBSERVED), data, "positions.csv", "2099-01-15"
    )
    assert written == 1
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
    assert payload["confidence"] == "UNSURE"
    assert all(call["confidence"] == "UNSURE" for call in payload["calls"])
    assert "words" not in payload["calibration_note"].lower()
    assert "UNSURE" in payload["calibration_note"]
    assert payload["provenance"]["instrument"] == "lambdamart"
    assert payload["provenance"]["held_out"] is False
    assert payload["provenance"]["dataset"] == "observed"
    hashes = {row["url"]: row["html_sha256"] for row in payload["candidates"]}
    assert hashes
    for url, digest in hashes.items():
        assert digest == hashlib.sha256(pages[url].encode("utf-8")).hexdigest()
    assert prediction.payload["subject_html_sha256"] == hashlib.sha256(draft_html.encode("utf-8")).hexdigest()

    later = (
        "query,url,position\n"
        f"{QUERY},https://acme.example/vinyl,1\n"
        f"{QUERY},https://rival.example/vinyl,2\n"
        f"{QUERY},https://other.example/vinyl,3\n"
    ).encode()
    imported = client.post(
        "/p/acme/import",
        data={"csrf": csrf, "snapshot_date": "2020-01-03", "add_searches": ""},
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
    assert outcome.observed_on == "2020-01-03"
    assert outcome.uploaded_at
    assert "Not a live Google pull" in outcome.notes
    assert score.predictions == 1
    assert score.resolved > 0
    assert score.accuracy is not None
