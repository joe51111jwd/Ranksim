"""PREDICTED impact per audit issue: applying suggestions, honest no-estimate answers, the route and its cache."""

from __future__ import annotations

from aeo.ranksim import predict_service
from aeo.ranksim import workspace as ws
from aeo.ranksim.plugins import predict_impact as pi
from tests.test_predict import OURS, SEARCHES, make_ctx
from tests.test_studio import _post, local  # noqa: F401 - the module fixture

PAGE = "<html><head><title>Old title</title><meta name='description' content='x'></head><body><main><h1 id='h'>Welcome</h1><p class='p'>Body</p></main></body></html>"


def issue(action, suggestion, selector, **extra):
    return {"id": "i1", "page": "https://ours.example/main", "action": action, "suggestion": suggestion, "element": {"selector": selector, **extra}}


def test_every_action_applies_to_the_stored_html():
    assert "<h1 id=\"h\">New heading</h1>" in pi.apply_suggestion(PAGE, issue("text", "New heading", "#h"))
    assert 'content="Better"' in pi.apply_suggestion(PAGE, issue("attr", "Better", "meta[name=description]", attr="content"))
    assert "<h2 id=\"h\">" in pi.apply_suggestion(PAGE, issue("retag", "h2", "#h"))
    appended = pi.apply_suggestion(PAGE, issue("append", "<h2>More</h2>", "main"))
    assert appended.index("<h2>More</h2>") > appended.index("Body")
    prepended = pi.apply_suggestion(PAGE, issue("prepend", "<p>First</p>", "main"))
    assert prepended.index("First") < prepended.index("Welcome")
    after = pi.apply_suggestion(PAGE, issue("after", "<p>Next</p>", "#h"))
    assert after.index("Welcome") < after.index("Next") < after.index("Body")
    assert pi.apply_suggestion(PAGE, issue("copy", "x", "#h")) is None
    assert pi.apply_suggestion(PAGE, issue("text", "x", "#nope")) is None
    assert pi.apply_suggestion(PAGE, issue("retag", "h2 onclick", "#h")) is None


def test_estimate_is_predicted_and_honest_about_no_estimate():
    predict_service._CACHE.clear()
    ctx = make_ctx()
    meta = {"id": "m", "page": "https://ours.example/main", "action": "append", "suggestion": '<meta name="twitter:card" content="summary">', "element": {"selector": "head"}}
    out = pi.estimate(ctx, meta, OURS, [])
    assert out["status"] == "no_estimate" and "same" in out["reason"] and out["label"] == "PREDICTED"
    assert pi.estimate(ctx, dict(meta, action="copy"), OURS, [])["status"] == "no_estimate"
    assert pi.estimate(ctx, meta, None, [])["status"] == "no_estimate"
    assert pi.estimate(ctx, dict(meta, page=None), OURS, [])["status"] == "no_estimate"
    title = {"id": "t", "page": "https://ours.example/main", "search": "hcss alternative", "action": "text", "suggestion": "hcss alternative", "element": {"selector": "title"}}
    got = pi.estimate(ctx, title, OURS, [])
    assert got["status"] in {"estimate", "no_change"}
    assert got["headline"].startswith("PREDICTED")
    assert got["searches"][0]["search"] == "hcss alternative"
    for row in got["searches"]:
        assert row["label"] == "PREDICTED"
        assert row["confidence"]["label"] in {"UNSURE", "CLEARER"}
        assert "backtest_pairs" in row["confidence"]
    assert "not a guarantee" in got["note"]
    assert set(row["search"] for row in got["searches"]) <= set(SEARCHES)


def test_route_answers_per_id_and_caches(local):  # noqa: F811
    client, token = local["client"], local["token"]
    path = f"/api/v1/projects/{ws.DEMO_SLUG}/issues/impact"
    assert _post(client, path, {"ids": []}, token).status_code == 400
    assert _post(client, path, {"ids": ["x"]}, None).status_code == 403
    report = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/report").json()
    ids = [item["id"] for item in report.get("issues", [])][:6] + ["no-such-issue"]
    first = _post(client, path, {"ids": ids}, token)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["label"] == "PREDICTED"
    assert [row["id"] for row in body["impacts"]] == ids
    assert body["impacts"][-1]["status"] == "unknown"
    for row in body["impacts"]:
        assert row["status"] in {"estimate", "no_change", "no_estimate", "unknown"}
        if row["status"] in {"estimate", "no_change"}:
            assert row["headline"].startswith("PREDICTED")
    again = _post(client, path, {"ids": ids[:-1]}, token).json()
    assert again["impacts"] == body["impacts"][:-1]
    text = str(body).lower().replace("not accuracy", "")
    assert "accuracy" not in text and "guarantee" in text
    assert "—" not in str(body) and "–" not in str(body)
