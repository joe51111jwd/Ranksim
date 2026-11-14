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


def test_issues_without_a_suggestion_get_a_stand_in_or_a_plain_reason():
    predict_service._CACHE.clear()
    ctx = make_ctx()
    h1 = {"id": "h", "kind": "h1-search", "page": "https://ours.example/main", "search": "hcss alternative", "element": {"selector": "h1"}}
    got = pi.estimate(ctx, h1, OURS, [])
    assert got["status"] in {"estimate", "no_change"} and "Assumes the H1" in got["assumption"]
    schema = {"id": "s", "kind": "schema-gap", "page": "https://ours.example/main", "search": "hcss alternative", "element": {"selector": "head"}}
    with_schema = OURS.replace("</head>", '<script type="application/ld+json">{}</script></head>')
    assert "already has some" in pi.estimate(ctx, schema, with_schema, [])["reason"]
    added = pi.estimate(ctx, schema, OURS, [])
    assert added.get("assumption") == "Assumes structured data is added to the page." or "same" in added.get("reason", "")
    for kind in ("not-ranking", "thin-vs-top", "script-built", "few-links"):
        out = pi.estimate(ctx, {"id": kind, "kind": kind, "page": "https://ours.example/main", "search": "hcss alternative", "element": {"selector": "main"}}, OURS, [])
        if out["status"] == "no_estimate":
            assert out["reason"] and "today" not in out
        else:
            assert "Assumes three links" in out["assumption"]


def test_sent_issues_skip_the_report_and_never_fill_the_id_cache(local, monkeypatch):  # noqa: F811
    client, token = local["client"], local["token"]
    path = f"/api/v1/projects/{ws.DEMO_SLUG}/issues/impact"
    report = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/report").json()
    issues = report.get("issues", [])[:3]
    assert issues

    def no_report(*_args, **_kwargs):
        raise AssertionError("the report was rebuilt although the issues were sent")

    import aeo.ranksim.api.projects as projects

    monkeypatch.setattr(projects, "project_report", no_report)
    body = _post(client, path, {"ids": [item["id"] for item in issues], "issues": issues}, token)
    assert body.status_code == 200, body.text
    assert [row["id"] for row in body.json()["impacts"]] == [item["id"] for item in issues]
    forged = dict(issues[0], suggestion="<h1>forged</h1>", action="append", element={"selector": "body"})
    _post(client, path, {"ids": [forged["id"]], "issues": [forged]}, token)
    monkeypatch.undo()
    honest = _post(client, path, {"ids": [issues[0]["id"]]}, token).json()["impacts"][0]
    assert "forged" not in str(honest)
