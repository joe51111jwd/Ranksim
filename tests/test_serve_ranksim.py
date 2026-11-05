"""The ranksim demo server, end to end through FastAPI's test client (no network, no keys)."""

import pathlib

import pytest
from fastapi.testclient import TestClient

from aeo.config import Settings
from aeo.ranksim.ledger import RsPrediction, verify_ledger
from aeo.ranksim.serve import DemoState, create_app
from aeo.ranksim.store import open_session

REPO = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    db = f"sqlite:///{tmp_path / 'demo.db'}"
    state = DemoState(Settings(), "smartcrew/serp.csv", db)
    return state, db


def test_check_records_a_prediction_with_provenance(demo):
    state, db = demo
    assert state.fit.models, "the seeded demo corpus should still fit some query models"
    query = sorted(state.fit.models)[0]
    client = TestClient(create_app(state))
    draft = (REPO / "smartcrew/pr8_v3/blog-best-software-small-dirt-work-contractors.html").read_text(errors="replace")
    response = client.post("/check", data={"query": query, "url": "https://www.smartcrewhq.com/draft-test/", "html": draft})
    assert response.status_code == 200
    assert "PREDICTED" in response.text
    session = open_session(db)
    rows = session.query(RsPrediction).all()
    ok, _ = verify_ledger(session)
    session.close()
    assert ok and len(rows) == 1
    payload = rows[0].payload
    assert "not a Google position" in payload["rank_meaning"]
    assert payload["provenance"]["labels_sha256"] and payload["provenance"]["corpus_sha256"]
    assert payload["calls"] and all(call["predicted"] in {"above", "below"} for call in payload["calls"])


def test_rejected_check_records_nothing(demo):
    state, db = demo
    client = TestClient(create_app(state))
    response = client.post("/check", data={"query": "a query nobody labeled", "url": "", "html": "<html></html>"})
    assert response.status_code == 200
    assert "No rank was invented" in response.text
    session = open_session(db)
    assert session.query(RsPrediction).count() == 0
    session.close()
