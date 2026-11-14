"""The test route keeps every existing field and adds the per-search predict block."""

from __future__ import annotations

from aeo.ranksim import workspace as ws
from tests.test_studio import _post, local  # noqa: F401 - the module fixture
from tests.test_workspace import QUERY


def test_route_adds_per_search_predictions_and_keeps_old_fields(local):  # noqa: F811
    client, token = local["client"], local["token"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    row = next(item for item in data["queries"] if item["q"] == QUERY)
    html = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/page", params={"url": row["your_url"]}).json()["html"]
    run = _post(
        client,
        f"/api/v1/projects/{ws.DEMO_SLUG}/test",
        {"q": QUERY, "draft": html, "url": row["your_url"], "original": "current", "original_html": html},
        token,
    ).json()
    for key in ("before", "after", "facts_after", "query_pairwise", "query_pairs", "change", "saved"):
        assert key in run
    block = run["predict"]
    assert block["label"] == "PREDICTED", block
    assert block["searches"] and block["searches"][0]["search"] == QUERY
    first = block["searches"][0]
    assert first["label"] == "PREDICTED"
    assert first["confidence"]["label"] in {"UNSURE", "CLEARER"}
    if first["method"] == "anchored":
        assert first["move"] == 0.0  # unchanged draft stays where it is
    text = str(block).lower().replace("not accuracy", "")
    assert "accuracy" not in text


def test_route_honours_an_explicit_search_list(local):  # noqa: F811
    client, token = local["client"], local["token"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    searches = [item["q"] for item in data["queries"]][:2]
    run = _post(
        client,
        f"/api/v1/projects/{ws.DEMO_SLUG}/test",
        {"q": searches[0], "draft": "<h1>" + " ".join(searches) + "</h1><p>" + " ".join(searches) * 20 + "</p>", "url": "https://dirtcrew.example/new-page/", "searches": searches},
        token,
    ).json()
    assert [row["search"] for row in run["predict"]["searches"]] == searches


def test_verdict_and_every_search_row_agree_and_the_log_records_them(local):  # noqa: F811
    client, token = local["client"], local["token"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    row = next(item for item in data["queries"] if item["q"] == QUERY)
    html = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/page", params={"url": row["your_url"]}).json()["html"]
    longer = html.replace("</body>", "<h2>" + QUERY + " guide</h2><p>" + (QUERY + " ") * 40 + "</p></body>")
    run = _post(
        client,
        f"/api/v1/projects/{ws.DEMO_SLUG}/test",
        {"q": QUERY, "draft": longer, "url": row["your_url"], "original": "current", "original_html": html},
        token,
    ).json()
    first = run["predict"]["searches"][0]
    assert first["search"] == QUERY
    assert run["after"]["position"] == first["slot_with_edit"]
    assert run["after"]["of"] == first["candidates"]
    if first["now"] is not None:
        assert run["before"]["position"] == first["slot_now"]
        assert run.get("change") == first["move_places"]
    if run["after"]["position"] is not None:
        pages = [item for item in run["after"]["order"] if item["page"]]
        assert len(pages) == 1 and pages[0]["predicted"] == run["after"]["position"]
    saved = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/tests/{run['id']}").json()
    assert saved["after"]["position"] == run["after"]["position"]
    assert "content_rerank" in saved


def test_notes_never_name_a_client():
    from aeo.ranksim import predict_service

    assert "smartcrew" not in predict_service.FORWARD_NOTE.lower()
    assert "forward check of 30 searches" in predict_service.FORWARD_NOTE
