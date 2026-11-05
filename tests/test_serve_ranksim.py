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
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    draft = (REPO / "smartcrew/pr8_v3/blog-best-software-small-dirt-work-contractors.html").read_text(errors="replace")
    response = client.post("/check", data={"query": query, "url": "https://www.smartcrewhq.com/draft-test/", "html": draft, "csrf": state.csrf})
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
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    response = client.post("/check", data={"query": "a query nobody labeled", "url": "", "html": "<html></html>", "csrf": state.csrf})
    assert response.status_code == 200
    assert "No rank was invented" in response.text
    session = open_session(db)
    assert session.query(RsPrediction).count() == 0
    session.close()


def test_result_screen_leads_with_uncertainty_and_coverage(demo):
    state, _db = demo
    query = sorted(state.fit.models)[0]
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    pasted = "<html><head><title>Dirt work software for crews</title></head><body><h1>Dirt work</h1><p>" + "crew schedule " * 200 + "</p></body></html>"
    page = client.post("/check", data={"query": query, "url": "", "html": pasted, "csrf": state.csrf}).text
    hero = page[page.index('id="result"'):]
    assert "UNSURE: no supported call" in hero
    assert "Compared with" in hero and "not a Google position" in hero
    assert "label file has no collection date" in page and "no country or device" in page
    assert hero.index("UNSURE") < hero.index("PREDICTED slot")
    assert "crew schedule" in page[page.index("<textarea"):]  # the pasted draft is kept
    assert 'name="ai" value="1" checked' not in page          # AI off unless asked


def test_drafts_are_tagged_and_whole_site_ranks_match_a_check_of_the_same_html(demo):
    from aeo.ranksim.check import check_draft
    from aeo.ranksim.store import page_by_url

    state, db = demo
    page = TestClient(create_app(state), base_url="http://127.0.0.1").get("/").text
    assert "DRAFT HTML, not the live page" in page
    assert state.site_rows, "the demo corpus should score at least one client page"
    session = open_session(db)
    compared = 0
    for row in state.site_rows:
        stored = page_by_url(session, row.url)
        checked = check_draft(
            query=row.query,
            html=stored.raw_html,
            url=row.url,
            labels=state.labels,
            pages=state.pages,
            model=state.fit.models[row.query],
            calibration=state.fit.calibration,
            today=state.today,
            intent=state.intent_for.get(row.query, "commercial"),
            run_ai=False,
        )
        assert (checked.predicted_rank, checked.predicted_of) == (row.predicted_rank, row.predicted_of)
        compared += 1
    session.close()
    assert compared >= 1


def test_oversized_draft_gets_a_readable_error(demo):
    state, _db = demo
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    response = client.post("/check", data={"query": sorted(state.fit.models)[0], "url": "", "html": "x" * 1_200_000, "csrf": state.csrf})
    assert response.status_code == 413
    assert "500 KB of HTML" in response.text


def test_report_escapes_summary_fields(tmp_path):
    from aeo.ranksim.report import render_report
    from aeo.ranksim.store import RsTestRun

    db = f"sqlite:///{tmp_path / 'r.db'}"
    session = open_session(db)
    session.add(RsTestRun(source_label="x", weight_label="w", summary_json={"improved": "<script>alert(1)</script>", "regressed": '"><img src=x onerror=1>'}))
    session.commit()
    session.close()
    html = render_report(db)
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html


def test_unknown_hosts_and_missing_csrf_are_refused(demo):
    state, db = demo
    app = create_app(state)
    rebinding = TestClient(app, base_url="http://evil.example").get("/")
    assert rebinding.status_code == 400 and "Unknown host" in rebinding.text
    client = TestClient(app, base_url="http://127.0.0.1")
    home = client.get("/")
    assert home.status_code == 200 and state.csrf in home.text
    assert "frame-ancestors 'none'" in home.headers["content-security-policy"]
    forged = client.post("/check", data={"query": sorted(state.fit.models)[0], "url": "", "html": "<html>x</html>"})
    assert forged.status_code == 403
    session = open_session(db)
    assert session.query(RsPrediction).count() == 0
    session.close()


def test_serve_refuses_a_public_bind_without_named_hosts(tmp_path, monkeypatch):
    from aeo.ranksim.cli import main

    monkeypatch.chdir(REPO)
    with pytest.raises(SystemExit, match="allowed-host"):
        main(["--database", f"sqlite:///{tmp_path / 'x.db'}", "serve", "--host", "0.0.0.0", "--port", "8799"])


def test_odd_csrf_values_and_method_errors_are_handled(demo):
    state, _db = demo
    client = TestClient(create_app(state), base_url="http://127.0.0.1")
    odd = client.post("/check", data={"query": "q", "url": "", "html": "<html></html>", "csrf": "\u00e9"})
    assert odd.status_code == 403
    wrong_method = client.get("/check")
    assert wrong_method.status_code == 405 and "POST" in wrong_method.headers.get("allow", "")
