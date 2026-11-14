"""The Studio (the app at /) and its JSON API. No network, no provider keys."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from aeo.ranksim import workspace as ws
from aeo.ranksim.serve import create_app
from tests.test_workspace import QUERY, make_settings

DASHES = ("\u2014", "\u2013")


def _csrf(client: TestClient) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text)
    assert found, "the Studio shell carries the CSRF token"
    return found.group(1)


def _post(client: TestClient, path: str, body: dict, token: str | None):
    headers = {"x-ranksim-csrf": token} if token else {}
    return client.post(path, json=body, headers=headers)


@pytest.fixture(scope="module")
def local(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("studio")
    settings = make_settings(tmp)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp / "projects", public_demo=False)
    client = TestClient(app)
    token = _csrf(client)
    assert client.get("/api/v1/projects").json()["projects"] == []
    built = _post(client, "/api/v1/demo", {}, token)
    assert built.status_code == 200 and built.json()["slug"] == ws.DEMO_SLUG
    return {"client": client, "token": token, "app": app}


def test_shell_is_served_at_root_and_app_paths_with_its_own_policy(local):
    client = local["client"]
    for path in ("/", "/app", f"/app/{ws.DEMO_SLUG}", f"/app/{ws.DEMO_SLUG}/studio", f"/app/{ws.DEMO_SLUG}/rankings"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert '<script src="/studio/studio.js?v=' in page.text and "<script>" not in page.text
        assert not any(dash in page.text for dash in DASHES)
        csp = page.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert page.headers["x-frame-options"] == "DENY"
    assert client.get(f"/app/{ws.DEMO_SLUG}/history").status_code == 200  # a plugin's screen key survives a reload
    assert client.get(f"/app/{ws.DEMO_SLUG}/No.Such").status_code == 404
    # the classic screens keep the strict policy
    assert "'unsafe-inline'" not in client.get("/projects").headers["content-security-policy"]


def test_assets_are_served_and_versioned(local):
    client = local["client"]
    page = client.get("/").text
    version = re.search(r"studio\.css\?v=([0-9a-f]+)", page).group(1)
    assert re.search(rf"studio\.js\?v={version}", page)
    css = client.get("/studio/studio.css")
    js = client.get("/studio/studio.js")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/studio/studio.html").status_code == 404
    assert client.get("/studio/../serve.py").status_code == 404


def test_classic_home_moved_to_projects_and_old_demo_link_still_redirects(local):
    client = local["client"]
    home = client.get("/projects")
    assert home.status_code == 200 and "Northwind Fencing" in home.text
    assert client.get("/?demo=1", follow_redirects=False).headers["location"] == "/demo?demo=1"
    assert client.get("/go", follow_redirects=False).headers["location"] == "/projects"


def test_overview_matches_the_comparison_screen(local):
    client = local["client"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    assert data["engine"]["ready"] and data["data"]["kind"] == ws.DATASET_SYNTHETIC and data["data"]["short"] == "Demo data"
    assert data["model"]["queries"] >= 2 and 0 <= data["model"]["pairwise"] <= 1
    assert data["ai"] is False and data["public_demo"] is False
    row = next(item for item in data["queries"] if item["q"] == QUERY)
    compare = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/compare", params={"q": QUERY}).json()
    you = next(cand for cand in compare["candidates"] if cand["role"] == "you")
    assert row["observed"] == you["observed"] and row["predicted"] == you["predicted"] and row["of"] == compare["scored"]
    assert all(cand["predicted"] is None for cand in compare["candidates"] if not cand["stored"])
    assert client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/compare", params={"q": "not a query"}).status_code == 404
    assert client.get("/api/v1/projects/no-such-project").status_code == 404


def test_page_endpoint_serves_only_pages_the_project_lists(local):
    client = local["client"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    url = next(item for item in data["queries"] if item["q"] == QUERY)["your_url"]
    page = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/page", params={"url": url})
    assert page.status_code == 200 and "<h1>" in page.json()["html"]
    for bad in ("https://elsewhere.example/page/", "file:///etc/passwd", "javascript:alert(1)", ""):
        assert client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/page", params={"url": bad}).status_code == 404, bad


def test_draft_test_needs_the_header_token_and_json(local):
    client, token = local["client"], local["token"]
    path = f"/api/v1/projects/{ws.DEMO_SLUG}/test"
    body = {"q": QUERY, "draft": "<h1>Vinyl fence installation cost</h1><p>Posts and panels.</p>"}
    refused = _post(client, path, body, None)
    assert refused.status_code == 403 and "expired" in refused.json()["error"]
    assert _post(client, path, body, "wrong").status_code == 403
    form = client.post(path, data=body, headers={"x-ranksim-csrf": token})
    assert form.status_code == 415
    assert _post(client, path, {"q": "not a query", "draft": "x"}, token).status_code == 400
    empty = _post(client, path, {"q": QUERY, "draft": "   "}, token)
    assert empty.status_code == 400 and "Nothing was run" in empty.json()["error"]


def test_unchanged_draft_scores_like_the_stored_page_and_is_saved_locally(local):
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
    assert run["before"]["position"] == run["after"]["position"] == row["predicted"]
    assert run["change"] == 0 and run["has_original"] and "current page" in run["original_label"]
    assert run["saved"] is True and ws.DRAFT_ID_RE.match(run["id"])
    assert "pages Google ranks" not in (run["topic_after"] or {}).get("text", "")


def test_a_longer_draft_is_scored_against_the_same_candidates(local):
    client, token = local["client"], local["token"]
    data = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    row = next(item for item in data["queries"] if item["q"] == QUERY)
    html = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/page", params={"url": row["your_url"]}).json()["html"]
    longer = html.replace(
        "</article>",
        "<h2>Vinyl fence installation cost by height</h2><p>Vinyl fence installation cost depends on height, posts, panels and labor.</p>"
        "<table><tr><th>Height</th><th>Cost</th></tr><tr><td>6 ft</td><td>Example</td></tr></table></article>",
    )
    run = _post(
        client,
        f"/api/v1/projects/{ws.DEMO_SLUG}/test",
        {"q": QUERY, "draft": longer, "url": row["your_url"], "original": "current", "original_html": html},
        token,
    ).json()
    assert run["after"]["of"] == run["before"]["of"] == run["candidates"] + 1
    assert run["facts_after"]["word_count"] > run["facts_before"]["word_count"]
    assert run["facts_after"]["table_count"] == run["facts_before"]["table_count"] + 1
    assert sum(1 for item in run["after"]["order"] if item["page"]) == 1
    assert run["change"] == run["before"]["position"] - run["after"]["position"]


@pytest.fixture()
def public(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the public demo saved a draft")

    monkeypatch.setattr("aeo.ranksim.workspace.save_draft_run", boom)
    settings = make_settings(tmp_path, openai_api_key="sk-test-not-used")
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=True)
    client = TestClient(app)
    return {"client": client, "token": _csrf(client), "tmp": tmp_path}


def test_public_demo_lists_only_the_demo_and_never_saves(public):
    client, token = public["client"], public["token"]
    listing = client.get("/api/v1/projects").json()
    assert listing["public_demo"] is True and [row["slug"] for row in listing["projects"]] == [ws.DEMO_SLUG]
    assert 'data-mode="public"' in client.get("/").text
    overview = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}").json()
    assert overview["ai"] is False  # a key in the environment is never spent on a public host
    run = _post(
        client,
        f"/api/v1/projects/{ws.DEMO_SLUG}/test",
        {"q": QUERY, "draft": "Vinyl fence installation cost\n\nPosts, panels, labor and the vinyl fence installation cost.", "ai": True},
        token,
    )
    assert run.status_code == 200
    body = run.json()
    assert body["saved"] is False and body["id"] == "" and body["ai"]["status"] == "off"
    again = _post(client, "/api/v1/demo", {}, token)
    assert again.status_code == 200 and again.json()["built"] is False  # visitors cannot make the host rebuild


def test_public_demo_refuses_other_projects(public):
    from aeo.ranksim.project import create_project

    create_project("Private", "private.example", ["private query"], root=public["tmp"] / "projects")
    client = public["client"]
    assert client.get("/api/v1/projects/private").status_code == 404
    assert client.get("/api/v1/projects/private/compare", params={"q": "private query"}).status_code == 404
    assert _post(client, "/api/v1/projects/private/test", {"q": "private query", "draft": "x"}, public["token"]).status_code == 404
    assert [row["slug"] for row in client.get("/api/v1/projects").json()["projects"]] == [ws.DEMO_SLUG]


def test_any_page_opens_for_editing_and_names_its_project(tmp_path):
    asked = []

    def preview(url: str):
        asked.append(url)
        if "broken" in url:
            return "", "HTTP 500"
        return "<html><head><title>Their page</title></head><body><h1>Hello</h1></body></html>", ""

    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False, preview_fetch=preview)
    client = TestClient(app)
    token = _csrf(client)
    edit = client.get("/edit?url=https://example.com/pricing")
    assert edit.status_code == 200 and "'unsafe-inline'" in edit.headers["content-security-policy"]
    assert _post(client, "/api/v1/fetch", {"url": "example.com/pricing"}, None).status_code == 403
    got = _post(client, "/api/v1/fetch", {"url": "example.com/pricing"}, token)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["url"] == "https://example.com/pricing" and "<h1>Hello</h1>" in body["html"] and body["project"] is None
    assert asked == ["https://example.com/pricing"]
    failed = _post(client, "/api/v1/fetch", {"url": "https://example.com/broken"}, token)
    assert failed.status_code == 400 and "HTTP 500" in failed.json()["error"]
