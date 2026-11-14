"""The Studio (the app at /) and its JSON API. No network, no provider keys."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from aeo.ranksim import studio
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
        assert '<script type="module" src="/studio/js/app.js"></script>' in page.text and "<script>" not in page.text
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
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)">', page)
    assert sheets and re.fullmatch(r"/studio/css/base\.css\?v=[0-9a-f]{10}", sheets[0])  # the design tokens load first
    assert all(re.fullmatch(r"/studio/css/[a-z0-9._/-]+\.css\?v=[0-9a-f]{10}", href) for href in sheets)
    modules = re.findall(r'<script type="module" src="([^"]+)"></script>', page)
    preloads = re.findall(r'<link rel="modulepreload" href="([^"]+)">', page)
    assert modules[0] == "/studio/js/app.js" and "/studio/js/core.js" in preloads and "/studio/js/hooks.js" in preloads
    # a module is keyed by its URL: a query string would load a second copy of core.js and its state
    assert not any("?" in src for src in modules + preloads)
    for href in sheets + modules + preloads:
        served = client.get(href)
        assert served.status_code == 200, href
    for name, kind in (("css/base.css", "text/css"), ("js/app.js", "javascript"), ("js/editor/editor.js", "javascript"), ("fonts/sans.woff2", "font/woff2"), ("fonts/mono.woff2", "font/woff2")):
        served = client.get(f"/studio/{name}")
        assert served.status_code == 200 and kind in served.headers["content-type"], name
    assert "/studio/fonts/sans.woff2" in client.get("/studio/css/base.css").text
    version = re.search(r"base\.css\?v=([0-9a-f]+)", page).group(1)
    assert version == studio._asset_version([studio.STATIC / "css" / "base.css"])


def test_assets_revalidate_with_an_etag_and_never_go_stale(local):
    client = local["client"]
    first = client.get("/studio/js/core.js")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-cache" and first.headers["etag"]
    again = client.get("/studio/js/core.js", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == first.headers["etag"]
    assert client.get("/studio/js/core.js", headers={"if-none-match": '"stale"'}).status_code == 200
    assert client.get("/studio/js/hooks.js").headers["etag"] != first.headers["etag"]
    # pages and the API still say no-store
    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/api/v1/projects").headers["cache-control"] == "no-store"


def test_asset_route_refuses_traversal_and_unknown_files(local):
    client = local["client"]
    for bad in (
        "/studio/studio.html",
        "/studio/js/plugins/README.md",
        "/studio/fonts/FONTS.md",
        "/studio/../serve.py",
        "/studio/..%2fserve.py",
        "/studio/%2e%2e/serve.py",
        "/studio/js/../../studio.py",
        "/studio/js/%2e%2e/%2e%2e/studio.py",
        "/studio/css/..%2f..%2fstudio.py",
        "/studio/js/.hidden.js",
        "/studio/js//core.js",
        "/studio/js\\core.js",
        "/studio/%2fetc/passwd",
        "/studio/js/core.js%00.css",
        "/studio/js/core.JS",
        "/studio/js/missing.js",
        "/studio/",
    ):
        refused = client.get(bad)
        assert refused.status_code == 404, bad
        assert "javascript" not in refused.headers.get("content-type", "") and "import " not in refused.text, bad
    assert client.get("/studio/js/core.js").status_code == 200


def test_shell_lists_every_plugin_script_and_stylesheet(local, tmp_path, monkeypatch):
    client = local["client"]
    js, css = tmp_path / "js", tmp_path / "css"
    js.mkdir()
    css.mkdir()
    (js / "zeta.js").write_text("import { on } from '../hooks.js';\n", encoding="utf-8")
    (js / "alpha.js").write_text("export const alpha = 1;\n", encoding="utf-8")
    (js / ".hidden.js").write_text("bad", encoding="utf-8")
    (js / "notes.md").write_text("notes", encoding="utf-8")
    (js / "has space.js").write_text("bad", encoding="utf-8")
    (css / "alpha.css").write_text(".alpha-tile { color: var(--blue); }\n", encoding="utf-8")
    (css / "notes.txt").write_text("notes", encoding="utf-8")
    monkeypatch.setattr(studio, "PLUGIN_JS", js)
    monkeypatch.setattr(studio, "PLUGIN_CSS", css)
    page = client.get("/").text
    modules = re.findall(r'<script type="module" src="([^"]+)"></script>', page)
    assert modules == ["/studio/js/app.js", "/studio/js/plugins/alpha.js", "/studio/js/plugins/zeta.js"]  # app first, then sorted
    sheets = re.findall(r'<link rel="stylesheet" href="([^"?]+)\?v=[0-9a-f]+">', page)
    assert sheets[0] == "/studio/css/base.css" and sheets[-1] == "/studio/css/plugins/alpha.css"
    assert ".hidden.js" not in page and "notes." not in page and "has space" not in page
    served = client.get("/studio/js/plugins/alpha.js")
    assert served.status_code == 200 and served.text == "export const alpha = 1;\n" and "javascript" in served.headers["content-type"]
    assert client.get("/studio/css/plugins/alpha.css").headers["content-type"].startswith("text/css")
    for bad in ("/studio/js/plugins/.hidden.js", "/studio/js/plugins/notes.md", "/studio/css/plugins/notes.txt", "/studio/js/plugins/../zeta.js"):
        assert client.get(bad).status_code == 404, bad
    # a new file shows up on the next page load, with no restart
    (js / "beta.js").write_text("export {};\n", encoding="utf-8")
    assert "/studio/js/plugins/beta.js" in client.get("/").text


def test_shell_works_without_plugin_folders(local, tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "PLUGIN_JS", tmp_path / "none-js")
    monkeypatch.setattr(studio, "PLUGIN_CSS", tmp_path / "none-css")
    page = local["client"].get("/")
    assert page.status_code == 200 and "/studio/js/plugins/" not in page.text and "/studio/css/plugins/" not in page.text


def test_print_stylesheet_applies_only_when_printing(local, tmp_path, monkeypatch):
    import shutil

    static = tmp_path / "static"
    shutil.copytree(studio.STATIC, static)
    (static / "css" / "print.css").write_text(".foot { display: none; }\n", encoding="utf-8")
    (static / "css" / "plugins" / "share.css").write_text("@media print { .share-x { display: none; } }\n", encoding="utf-8")
    monkeypatch.setattr(studio, "STATIC", static)
    monkeypatch.setattr(studio, "PLUGIN_JS", static / "js" / "plugins")
    monkeypatch.setattr(studio, "PLUGIN_CSS", static / "css" / "plugins")
    page = local["client"].get("/").text
    links = re.findall(r"<link rel=\"stylesheet\"[^>]*>", page)
    printed = [link for link in links if "/studio/css/print.css?v=" in link]
    assert printed and all(link.endswith(' media="print">') for link in printed)
    assert all("media=" not in link for link in links if link not in printed)
    assert any("/studio/css/plugins/share.css?v=" in link for link in links)
    assert local["client"].get("/studio/css/print.css").status_code == 200


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
    # One source of truth (predict): the verdict uses the same PREDICTED slots as the Every search row.
    assert run["before"]["position"] == run["after"]["position"] == run["predict"]["searches"][0]["slot_now"]
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
    assert run["after"]["of"] == run["before"]["of"] == run["predict"]["searches"][0]["candidates"]  # one source of truth
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
