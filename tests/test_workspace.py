"""The three-screen workspace: CSV checks, comparisons, draft tests, the synthetic demo, and the web flow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aeo.config import Settings
from aeo.ranksim import workspace as ws
from aeo.ranksim.project import load_project_context
from aeo.ranksim.serve import create_app

DASHES = ("—", "–")
QUERY = "vinyl fence installation cost"


def make_settings(tmp_path: Path, **keys) -> Settings:
    values = {
        "database_url": f"sqlite:///{tmp_path / 'aeo.db'}",
        "ranksim_database_url": f"sqlite:///{tmp_path / 'rank.db'}",
        "serpapi_key": None,
        "serper_api_key": None,
        "dataforseo_login": None,
        "dataforseo_password": None,
        "brave_api_key": None,
        "openai_api_key": None,
        "anthropic_api_key": None,
        "openrouter_api_key": None,
        "perplexity_api_key": None,
        "gemini_api_key": None,
        "crawl_delay_seconds": 0,
        "ranksim_llm_model": None,
    }
    values.update(keys)
    return Settings(_env_file=None, **values)


def token(client: TestClient, path: str = "/") -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert found, "every form carries a CSRF token"
    return found.group(1)


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ws")
    settings = make_settings(tmp)
    project, report = ws.load_synthetic_demo(tmp / "projects", settings, settings.ranksim_database_url)
    ctx = load_project_context(project, database=settings.ranksim_database_url)
    return {"tmp": tmp, "settings": settings, "project": project, "report": report, "ctx": ctx}


# -- CSV checks ----------------------------------------------------------------


def test_csv_check_accepts_a_good_file_and_lists_queries():
    check = ws.check_csv(b"query,url,position\nfence cost,https://a.example/x,1\nfence cost,https://b.example/y,2\nfence height,https://a.example/z,3\n")
    assert check.ok and check.rows == 3 and check.queries == ["fence cost", "fence height"]


def test_csv_check_reports_problems_by_line():
    data = (
        "query,url,position\n"
        "fence cost,https://a.example/x,1\n"
        ",https://a.example/y,2\n"
        "fence cost,not a url,3\n"
        "fence cost,https://c.example/,zero\n"
        "fence cost,https://d.example/,0\n"
    ).encode()
    check = ws.check_csv(data)
    assert not check.ok
    text = " ".join(check.errors)
    assert "Line 3: the query is empty" in text
    assert "Line 4: 'not a url' is not a full http(s) URL" in text
    assert "Line 5: position 'zero' is not a number" in text
    assert "Line 6: position 0 must be 1 or more" in text


@pytest.mark.parametrize(
    "data, expected",
    [
        (b"", "empty"),
        (b"keyword,page\nx,https://a.example/\n", "Missing columns: query, position"),
        (b"query,url,position\n", "no data rows"),
        ("query,url,position\nfé,https://a.example/,1\n".encode("latin-1"), "not UTF-8"),
        (b"\x00\x01binary", "not a text CSV"),
    ],
)
def test_csv_check_rejects_unusable_files(data, expected):
    check = ws.check_csv(data)
    assert not check.ok and expected in " ".join(check.errors)


def test_competitors_are_parsed_to_domains_and_your_site_is_refused():
    found, problems = ws.parse_competitors("https://www.rival.com/page\nrival.com\nother.example, bad entry!\nmysite.com", "mysite.com")
    assert found == ["rival.com", "other.example"]
    assert any("your own site" in item for item in problems) and any("bad entry" in item for item in problems)


# -- engine-level behavior on the synthetic fixture ------------------------------


def test_synthetic_demo_builds_without_network_and_records_missing_html(demo):
    report = demo["report"]
    assert report.counts.get("fetched_now", 0) > 30
    assert report.counts.get("blocked_robots", 0) > 0 and report.counts.get("http_error", 0) > 0
    meta = ws.load_meta(demo["project"])
    assert meta.dataset == ws.DATASET_SYNTHETIC and "Not Google results" in meta.dataset_label
    rows = ws.coverage(demo["project"], demo["settings"].ranksim_database_url, meta)
    assert len(rows) == 6 and all(row.stored < row.candidates for row in rows)
    assert all(".example" in url for row in rows for url in row.missing)


def test_comparison_predicts_only_stored_candidates_and_never_invents(demo):
    view = ws.compare_search(demo["ctx"], QUERY)
    assert not view.error
    stored = [cand for cand in view.candidates if cand.stored]
    missing = [cand for cand in view.candidates if not cand.stored]
    assert missing and all(cand.predicted is None and cand.score is None and not cand.evidence for cand in missing)
    assert sorted(cand.predicted for cand in stored) == list(range(1, len(stored) + 1))
    assert view.scored == len(stored) and view.total == len(view.candidates)
    assert view.your_observed is not None and view.your_predicted is not None
    assert view.confidence in {"UNSURE", "CLEARER"} or view.confidence
    assert any("no stored HTML" in note for note in view.notes)


def test_comparison_without_your_page_says_so(demo):
    view = ws.compare_search(demo["ctx"], "chain link fence vs vinyl")
    assert view.your_url is None and view.your_predicted is None


def test_draft_run_scores_original_and_draft_against_the_same_candidates(demo):
    ctx = demo["ctx"]
    view = ws.compare_search(ctx, QUERY)
    original = ws.synthetic_page(view.your_url)
    draft = original.replace("</article>", "<h2>FAQ</h2><h3>What does it cost?</h3><p>Vinyl fence installation cost depends on panels, posts and labor.</p></article>")
    run = ws.run_draft(ctx, QUERY, draft, view.your_url, original_html=original, original_label="pasted original")
    assert run["before"]["of"] == run["after"]["of"] == run["candidates"] + 1
    before_urls = {item["url"] for item in run["before"]["order"] if not item["page"]}
    after_urls = {item["url"] for item in run["after"]["order"] if not item["page"]}
    assert before_urls == after_urls and view.your_url not in after_urls
    assert run["facts_before"]["has_faq"] == "no" and run["facts_after"]["has_faq"] == "yes"
    assert "change" in run and run["ai"]["status"] == "off"


def test_draft_run_competitors_only_and_errors(demo):
    ctx = demo["ctx"]
    run = ws.run_draft(ctx, QUERY, "<h1>Vinyl fence installation cost</h1><p>Short.</p>", "https://www.northwind-fencing.example/new/", which="competitors")
    assert run["set_label"] == "named competitors only"
    assert all(ws.role_of(ctx.project, ws.load_meta(ctx.project), item["url"]) == "competitor" for item in run["after"]["order"] if not item["page"])
    assert run["before"] is None and any("words" in note for note in run["notes"])
    with pytest.raises(ws.WorkspaceError):
        ws.run_draft(ctx, QUERY, "   ", "https://www.northwind-fencing.example/new/")
    with pytest.raises(ws.WorkspaceError):
        ws.run_draft(ctx, "not a project query", "<p>x</p>", "https://www.northwind-fencing.example/new/")


def test_plain_text_draft_becomes_a_page():
    html = ws._as_html("My title line\n\nFirst paragraph here.\n\nSecond one.")
    assert "<title>My title line</title>" in html and "<p>Second one.</p>" in html
    kept = ws._as_html("Title\nsecond line of the intro\n\nBody")
    assert "second line of the intro" in kept and "<p>Body</p>" in kept


def test_an_off_topic_or_stuffed_draft_gets_no_position(demo):
    ctx = demo["ctx"]
    stuffed = "<h1>Vinyl fence installation cost</h1>" + "<p>vinyl fence installation cost vinyl fence installation cost.</p>" * 120
    run = ws.run_draft(ctx, QUERY, stuffed, "https://www.northwind-fencing.example/new/")
    assert run["after"]["position"] is None and run["after"]["unplaced"] and "change" not in run
    assert "Google ranks" not in run["after"]["unplaced"]


def test_synthetic_positions_are_never_borrowed_as_real(demo):
    from aeo.ranksim.project import borrow_real_results, create_project

    other = create_project("Copycat", "copycat.example", [QUERY], root=demo["tmp"] / "projects")
    assert borrow_real_results(other) == {}


# -- the web flow ---------------------------------------------------------------


@pytest.fixture()
def web(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False)
    client = TestClient(app)
    return {"app": app, "client": client, "tmp": tmp_path}


def _clean_page(text: str) -> None:
    assert "<script>" not in text and not any(dash in text for dash in DASHES)


def test_empty_home_offers_the_demo_and_a_create_form(web):
    page = web["client"].get("/")
    assert page.status_code == 200
    assert "Load synthetic demo" in page.text and "New project" in page.text and "No projects yet" in page.text
    _clean_page(page.text)


def test_full_flow_demo_compare_draft_reload(web):
    client = web["client"]
    csrf = token(client)
    made = client.post("/demo/synthetic", data={"csrf": csrf}, follow_redirects=False)
    assert made.status_code == 303 and made.headers["location"].startswith("/p/synthetic-demo")
    setup = client.get(made.headers["location"])
    assert setup.status_code == 200 and "SYNTHETIC example data" in setup.text and "Coverage by query" in setup.text
    _clean_page(setup.text)
    compare = client.get("/p/synthetic-demo/comparisons", params={"q": QUERY})
    assert compare.status_code == 200
    text = compare.text
    assert "PREDICTED" in text and "n/a" in text and "not a Google rank" in text and "SYNTHETIC example data" in text
    assert 'class="row-you' in text and "Limitations" in text
    _clean_page(text)
    picked = client.get("/p/synthetic-demo/comparisons", params={"q": QUERY, "sel": "0", "sort": "predicted"})
    assert picked.status_code == 200 and "Evidence the model reads" in picked.text
    form = client.get("/p/synthetic-demo/draft", params={"q": QUERY})
    assert "AI notes unavailable" in form.text and 'name="ai_notes"' not in form.text
    bad = client.post("/p/synthetic-demo/draft", data={"csrf": csrf, "q": QUERY, "draft_html": "   ", "original_mode": "none"})
    assert bad.status_code == 400 and "Nothing was run" in bad.text
    empty_original = client.post("/p/synthetic-demo/draft", data={"csrf": csrf, "q": QUERY, "draft_html": "<p>x</p>", "original_mode": "paste", "original_html": ""})
    assert empty_original.status_code == 400 and "original box is empty" in empty_original.text
    ran = client.post(
        "/p/synthetic-demo/draft",
        data={"csrf": csrf, "q": QUERY, "which": "all", "original_mode": "stored", "draft_html": "<html><head><title>Vinyl fence installation cost</title></head><body><h1>Vinyl fence installation cost</h1><p>Panels, posts and labor.</p></body></html>"},
        follow_redirects=False,
    )
    assert ran.status_code == 303 and "/p/synthetic-demo/draft/" in ran.headers["location"]
    result = client.get(ran.headers["location"])
    assert result.status_code == 200
    assert "Original" in result.text and "PREDICTED" in result.text and "does not show that Google will rank" in result.text
    assert "AI notes unavailable" in result.text
    _clean_page(result.text)
    # persistence: a new app on the same folders still has the project, the competitors and the saved run
    again = TestClient(create_app(settings=make_settings(web["tmp"]), projects_root=web["tmp"] / "projects", public_demo=False))
    assert again.get(ran.headers["location"]).status_code == 200
    assert "fenceguide.example" in again.get("/p/synthetic-demo").text
    assert again.get(ran.headers["location"] + "/draft.txt").text.startswith("<html>")
    assert again.get("/p/synthetic-demo/draft/nope").status_code == 404
    assert again.get("/p/synthetic-demo/draft/20260101T000000Z-abcdef/../../project.txt").status_code == 404


def test_draft_with_a_configured_provider_adds_ai_notes(tmp_path):
    class FakeLlm:
        available = True
        name = "fake"

        def __init__(self):
            self.prompts = []

        def complete(self, system, user):
            self.prompts.append(user)

            class Reply:
                text = "- The draft adds an FAQ.\n- PREDICTED among these candidates only."

            return Reply()

    llm = FakeLlm()
    settings = make_settings(tmp_path, openai_api_key="sk-test-not-real")
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", llm_factory=lambda: llm, public_demo=False)
    client = TestClient(app)
    csrf = token(client)
    client.post("/demo/synthetic", data={"csrf": csrf})
    form = client.get("/p/synthetic-demo/draft", params={"q": QUERY}).text
    assert 'name="ai_notes"' in form and "sk-test-not-real" not in form
    ran = client.post(
        "/p/synthetic-demo/draft",
        data={"csrf": csrf, "q": QUERY, "original_mode": "none", "ai_notes": "1", "draft_html": "<h1>Vinyl fence installation cost</h1><p>Text.</p>"},
    )
    assert ran.status_code == 200 and "The draft adds an FAQ" in ran.text and "sk-test-not-real" not in ran.text
    assert llm.prompts and "<h1>" not in llm.prompts[0]  # measured facts only, never the page


def test_create_project_from_csv_and_reject_a_bad_import(web):
    client = web["client"]
    csrf = token(client)
    good = b"query,url,position\nfence cost,https://a.example/x,1\nfence cost,https://b.example/y,2\n"
    made = client.post(
        "/projects",
        data={"csrf": csrf, "name": "Acme", "site": "acme.example", "competitors": "b.example\nacme.example", "searches": ""},
        files={"csv_file": ("positions.csv", good, "text/csv")},
        follow_redirects=False,
    )
    assert made.status_code == 303 and "imported-2" in made.headers["location"]
    page = client.get(made.headers["location"])
    assert "Imported 2 observed positions" in page.text and "fence cost" in page.text and "b.example" in page.text
    assert "Observed data" in page.text and "SYNTHETIC" not in page.text
    bad = b"query,url,position\nfence cost,https://a.example/x,first\n"
    rejected = client.post("/p/acme/import", data={"csrf": csrf}, files={"csv_file": ("bad.csv", bad, "text/csv")})
    assert rejected.status_code == 400 and "The CSV was not imported" in rejected.text and "Line 2" in rejected.text
    nothing = client.post("/projects", data={"csrf": csrf, "name": "Empty", "site": "e.example", "searches": ""})
    assert nothing.status_code == 400 and "Add at least one query" in nothing.text
    comparisons = client.get("/p/acme/comparisons")
    assert comparisons.status_code == 200 and "Comparisons" in comparisons.text
    saved = client.post("/p/acme/competitors", data={"csrf": csrf, "competitors": "c.example\nnot valid!"})
    assert saved.status_code == 200 and "not a domain" in saved.text


def test_public_demo_mode_only_serves_the_synthetic_demo(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=True))
    home = client.get("/")
    csrf = token(client)
    assert "Load historical SmartCrew export" not in home.text and "public demo" in home.text
    assert client.post("/demo/historical", data={"csrf": csrf}).status_code == 404
    assert client.post("/projects", data={"csrf": csrf, "name": "X", "site": "x.example", "searches": "a"}).status_code == 403
    assert client.get("/demo").status_code == 404
    assert client.post("/demo/synthetic", data={"csrf": csrf}, follow_redirects=False).status_code == 303
    assert client.post("/p/synthetic-demo/pull", data={"csrf": csrf}).status_code == 404
    assert client.post("/p/synthetic-demo/import", data={"csrf": csrf}, files={"csv_file": ("a.csv", b"query,url,position\n", "text/csv")}).status_code == 404
    assert client.post("/p/synthetic-demo/competitors", data={"csrf": csrf, "competitors": "a.example"}).status_code == 403
    assert client.get("/healthz").json()["public_demo"] is True
    for path in ("/p/synthetic-demo/advanced", "/p/synthetic-demo/versions", "/p/synthetic-demo/engines", "/api/jobs/x"):
        assert client.get(path).status_code == 404, path
    assert client.post("/p/synthetic-demo/test", data={"csrf": csrf, "html_text": "<p>x</p>"}).status_code == 404
    # anonymous drafts are rendered once, never saved, listed or served back
    ran = client.post(
        "/p/synthetic-demo/draft",
        data={"csrf": csrf, "q": QUERY, "original_mode": "stored", "draft_html": "<h1>PRIVATE-MARKER vinyl fence installation cost</h1><p>panels posts labor</p>"},
    )
    assert ran.status_code == 200 and "is not saved" in ran.text
    form = client.get("/p/synthetic-demo/draft", params={"q": QUERY}).text
    assert "PRIVATE-MARKER" not in form and "Saved tests" not in form
    assert not (tmp_path / "projects" / "synthetic-demo" / "drafts").exists()
    # a public host serves the demo project only, even when other projects sit in its folder
    from aeo.ranksim.project import create_project

    create_project("Private", "private.example", ["secret query"], root=tmp_path / "projects")
    assert client.get("/p/private").status_code == 404 and client.get("/p/private/comparisons").status_code == 404


def test_public_mode_from_the_environment_never_uses_a_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RANKSIM_PUBLIC_DEMO", "1")
    settings = make_settings(tmp_path, openai_api_key="sk-test-not-real")
    app = create_app(settings=settings, projects_root=tmp_path / "projects", llm_factory=lambda: (_ for _ in ()).throw(AssertionError("called")))
    assert app.state.ui.public_demo and not app.state.ui.llm_ready()
    client = TestClient(app)
    form = client.get("/p/synthetic-demo/draft", params={"q": QUERY}).text
    assert 'name="ai_notes"' not in form and "AI notes unavailable" in form


def test_posts_need_the_csrf_token(web):
    client = web["client"]
    assert client.post("/demo/synthetic", data={}).status_code == 403
    assert client.post("/p/synthetic-demo/draft", data={"q": QUERY, "draft_html": "x"}).status_code == 403
