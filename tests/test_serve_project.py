"""The web UI: projects, Pull now, Test a page, results, versions, AI engines, and its guards.

FastAPI TestClient with fakes: the fake SERP and crawler from test_project, a
fake preview fetcher, a fake LLM. Jobs run in real background threads and the
tests wait on them. No network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_engines import SourceReadingLlm, cite_by_domain
from test_project import OUR_URL, SEARCHES, SITE, TODAY, FakeCrawler, _results, page_html

from aeo.config import Settings
from aeo.ranksim import cli
from aeo.ranksim import project as proj
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.serve import MAX_UPLOAD_BYTES, create_app, loopback_hosts

DASHES = ("\u2014", "\u2013")
SECRET = "sk-live-SECRET-0123456789"
HOSTILE_TITLE = '<img src=x onerror=alert(1)> Dirt "work" software'


class HostileSerp:
    """A Google provider whose titles carry markup, to prove every crawled string is escaped."""

    name = "fakeserp"
    is_google = True
    synthetic = False

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        urls = _results().get(query, [])[:count]
        return [
            SerpHit(position=index, url=url, title=f"{HOSTILE_TITLE} {index}", snippet="", provider=self.name, synthetic=False, is_google=True)
            for index, url in enumerate(urls, start=1)
        ]


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


def csrf(client: TestClient, path: str = "/") -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert found, "every form carries a CSRF token"
    return found.group(1)


def wait(app, location: str) -> dict:
    job_id = location.split("job=")[-1] if "job=" in location else location.rstrip("/").split("/")[-1]
    ui = app.state.ui
    job = ui.jobs.wait(job_id, timeout=120)
    assert job is not None
    return ui.jobs.as_dict(job)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """One app over a temp projects folder: created in the UI, imported, pulled with fakes."""
    tmp = tmp_path_factory.mktemp("serve")
    settings = make_settings(tmp, serpapi_key=SECRET)
    previews: list[str] = []

    def preview_fetch(url: str) -> tuple[str, str]:
        previews.append(url)
        if "missing" in url:
            return "", "HTTP 404"
        return page_html("Preview of dirt work software", "Dirt Work Software", extra="dirt work software"), ""

    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp / "projects",
        pull_options={"serp_provider": HostileSerp(), "fetcher": FakeCrawler(), "today": TODAY, "workers": 4},
        preview_fetch=preview_fetch,
    )
    client = TestClient(app)
    token = csrf(client)
    made = client.post(
        "/projects",
        data={"csrf": token, "name": "Dirt Crew", "site": SITE, "searches": "\n".join(SEARCHES)},
        follow_redirects=False,
    )
    assert made.status_code == 303 and made.headers["location"] == "/p/dirt-crew"
    pulled = client.post("/p/dirt-crew/pull", data={"csrf": token}, follow_redirects=False)
    assert pulled.status_code == 303 and "job=" in pulled.headers["location"]
    job = wait(app, pulled.headers["location"])
    return {"app": app, "client": client, "token": token, "tmp": tmp, "settings": settings, "job": job, "previews": previews}


def test_home_lists_projects_keys_and_never_shows_a_key(site):
    page = site["client"].get("/")
    assert page.status_code == 200
    assert "Dirt Crew" in page.text and "/p/dirt-crew" in page.text
    assert "not run: needs OPENAI_API_KEY" in page.text and "ready (SerpAPI)" in page.text
    assert SECRET not in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in page.text  # the only script is /static/app.js
    assert not any(dash in page.text for dash in DASHES)


def test_post_without_csrf_is_refused(site):
    refused = site["client"].post("/projects", data={"name": "X", "site": "x.com", "searches": "a"}, follow_redirects=False)
    assert refused.status_code == 403
    assert site["client"].post("/p/dirt-crew/pull", data={"csrf": "wrong"}).status_code == 403


def test_pull_job_progress_and_project_page(site):
    job = site["job"]
    assert job["status"] == "done", job["error"]
    steps = {item["step"]: item for item in job["steps"]}
    for name in ("google", "engines", "crawl", "train", "done"):
        assert name in steps
    assert steps["crawl"]["done"] == steps["crawl"]["total"] > 0
    assert [item["step"] for item in job["timings"]] == ["google", "engines", "crawl", "train", "save"]
    page = site["client"].get("/p/dirt-crew")
    assert page.status_code == 200
    assert "Pull now" in page.text and "Test this page" in page.text
    assert "Step timings of pull" in page.text
    assert "In backtests this predictor put a hidden page within 2 places" in page.text
    assert "not run: needs ANTHROPIC_API_KEY" in page.text
    api = site["client"].get(f"/api/jobs/{job['id']}").json()
    assert api["status"] == "done" and api["result_url"].startswith("/p/dirt-crew")
    assert site["client"].get("/api/jobs/nope").status_code == 404
    # A pull still running has an empty folder: the page keeps showing the newest finished pull.
    running = site["tmp"] / "projects" / "dirt-crew" / "pulls" / "29991231T000000Z"
    running.mkdir()
    try:
        page = site["client"].get("/p/dirt-crew").text
        assert "Last pull: <b>unknown time" not in page and "Last pull: <b>20" in page
    finally:
        running.rmdir()


def test_upload_test_shows_the_board_with_labels_and_escaping(site):
    client, app = site["client"], site["app"]
    html = page_html("Dirt Work Software", "Dirt Work Software", extra="dirt work software excavation crew")
    started = client.post(
        "/p/dirt-crew/test",
        data={"csrf": site["token"], "url": OUR_URL},
        files={"html_file": ("dirt.html", html.encode(), "text/html")},
        follow_redirects=False,
    )
    assert started.status_code == 303 and "/jobs/" in started.headers["location"]
    job = wait(app, started.headers["location"])
    assert job["status"] == "done", job["error"]
    assert {"load", "score", "simulate", "save"} <= {item["step"] for item in job["steps"]}
    forwarded = client.get(started.headers["location"], follow_redirects=False)
    assert forwarded.status_code == 303 and forwarded.headers["location"] == job["result_url"]
    page = client.get(job["result_url"])
    assert page.status_code == 200
    text = page.text
    assert "PREDICTED top 3 on" in text and "REAL (" in text and "REAL today" not in text
    assert 'class="you"' in text and "YOUR PAGE" in text
    assert "In backtests this predictor put a hidden page within 2 places of its REAL Google position" in text
    assert "On-topic check" in text and "On topic (PREDICTED)" in text
    # Pages above the slot are explained by what sets the slot (site history counts), not by content-model advice.
    assert "Why the" in text and "Site history: its site is in the REAL top 10" in text and "Page one" not in text
    assert "not run: needs OPENAI_API_KEY" in text and "not run: needs OPENROUTER_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY" in text
    assert "Bing Copilot" in text
    assert "<img src=x onerror" not in text and "&lt;img src=x onerror=alert(1)&gt;" in text
    assert not any(dash in text for dash in DASHES)
    assert SECRET not in text
    for search in SEARCHES:
        assert search in text
    data = client.get(job["result_url"] + ".json").json()
    assert data["version"] == "v1" and data["slot_method"] == "site history"
    assert data["searches"][0]["site_position"] is not None


def test_paste_and_preview_and_versions_and_compare(site):
    client, app = site["client"], site["app"]
    weak = page_html("Hi", "Hi", paragraphs=3)
    started = client.post("/p/dirt-crew/test", data={"csrf": site["token"], "url": OUR_URL, "html_text": weak}, follow_redirects=False)
    job = wait(app, started.headers["location"])
    assert job["status"] == "done", job["error"]
    board = client.get(job["result_url"] + ".json").json()
    assert board["version"] == "v2" and board["previous_version"] == "v1"
    assert any(row["change_vs_previous"] != "n/a" for row in board["searches"])

    started = client.post(
        "/p/dirt-crew/test",
        data={"csrf": site["token"], "preview_url": "https://staging.dirtcrew.example/new/", "url": "https://www.dirtcrew.example/new/"},
        follow_redirects=False,
    )
    job = wait(app, started.headers["location"])
    assert job["status"] == "done", job["error"]
    assert site["previews"][-1] == "https://staging.dirtcrew.example/new/"
    assert "fetch" in {item["step"] for item in job["steps"]}

    failed = client.post("/p/dirt-crew/test", data={"csrf": site["token"], "preview_url": "https://staging.dirtcrew.example/missing/"}, follow_redirects=False)
    job = wait(app, failed.headers["location"])
    assert job["status"] == "error" and "HTTP 404" in job["error"] and "invented" in job["error"]

    versions = client.get("/p/dirt-crew/versions", params={"url": OUR_URL})
    assert versions.status_code == 200 and "v1" in versions.text and "v2" in versions.text and "Compare" in versions.text
    compare = client.get("/p/dirt-crew/compare", params={"url": OUR_URL, "a": "v1", "b": "v2"})
    assert compare.status_code == 200
    assert "content model" in compare.text and "Site history slot" in compare.text
    assert all(search in compare.text for search in SEARCHES)
    assert client.get("/p/dirt-crew/compare", params={"url": OUR_URL, "a": "v1", "b": "../../etc"}).status_code == 200
    home = client.get("/p/dirt-crew")
    assert "Tested pages" in home.text and "latest result" in home.text


def test_bad_inputs_are_refused(site):
    client, token = site["client"], site["token"]
    nothing = client.post("/p/dirt-crew/test", data={"csrf": token, "url": OUR_URL})
    assert nothing.status_code == 400 and "Give a page to test" in nothing.text
    two = client.post("/p/dirt-crew/test", data={"csrf": token, "html_text": "<p>x</p>", "preview_url": "https://a.example/"})
    assert two.status_code == 400 and "Not two" in two.text
    local = client.post("/p/dirt-crew/test", data={"csrf": token, "preview_url": "file:///etc/passwd"})
    assert local.status_code == 400 and "must start with http" in local.text
    wrong = client.post("/p/dirt-crew/test", data={"csrf": token}, files={"html_file": ("notes.txt", b"hello", "text/plain")})
    assert wrong.status_code == 400 and ".html" in wrong.text
    big = b"<html>" + b"a" * (MAX_UPLOAD_BYTES + 10)
    too_big = client.post("/p/dirt-crew/test", data={"csrf": token, "url": OUR_URL}, files={"html_file": ("big.html", big, "text/html")})
    assert too_big.status_code == 413
    slightly = b"<html>" + b"a" * (MAX_UPLOAD_BYTES - 6 + 100)
    over = client.post("/p/dirt-crew/test", data={"csrf": token, "url": OUR_URL}, files={"html_file": ("big.html", slightly, "text/html")})
    assert over.status_code == 413
    huge = b"<html>" + b"a" * (MAX_UPLOAD_BYTES + 200_000)  # over the request cap: refused before the form is read
    capped = client.post("/p/dirt-crew/test", data={"csrf": token, "url": OUR_URL}, files={"html_file": ("big.html", huge, "text/html")})
    assert capped.status_code == 413 and "5 MB" in capped.text
    pasted = client.post("/p/dirt-crew/searches", content=b"csrf=x&searches=" + b"a" * (MAX_UPLOAD_BYTES + 200_000),
                         headers={"content-type": "application/x-www-form-urlencoded"})
    assert pasted.status_code == 413
    assert client.get("/p/..%2F..%2Fetc").status_code == 404
    assert client.get("/p/dirt-crew/results/../../project").status_code == 404
    assert client.get("/p/dirt-crew/results/nope").status_code == 404


def test_hostile_target_url_is_escaped(site):
    client, app = site["client"], site["app"]
    target = 'https://www.dirtcrew.example/x"><script>alert(1)</script>'
    started = client.post("/p/dirt-crew/test", data={"csrf": site["token"], "url": target, "html_text": page_html("A", "A")}, follow_redirects=False)
    job = wait(app, started.headers["location"])
    assert job["status"] == "done", job["error"]
    page = client.get(job["result_url"]).text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_engines_page_and_searches_edit_and_import(site):
    client, token = site["client"], site["token"]
    engines = client.get("/p/dirt-crew/engines")
    assert engines.status_code == 200 and "not run: needs GEMINI_API_KEY" in engines.text and "Bing Copilot" in engines.text
    saved = client.post("/p/dirt-crew/searches", data={"csrf": token, "searches": "\n".join([*SEARCHES, "trench box rental"])}, follow_redirects=False)
    assert saved.status_code == 303
    page = client.get("/p/dirt-crew")
    assert "trench box rental" in page.text and "not run: needs SERPAPI_KEY" in page.text
    client.post("/p/dirt-crew/searches", data={"csrf": token, "searches": "\n".join(SEARCHES)})
    rows = ["query,url,position"] + [f"{search},{url},{index}" for search, urls in _results().items() for index, url in enumerate(urls, start=1)]
    imported = client.post(
        "/p/dirt-crew/import",
        data={"csrf": token},
        files={"csv_file": ("serp.csv", ("\n".join(rows) + "\n").encode(), "text/csv")},
        follow_redirects=False,
    )
    assert imported.status_code == 303 and "imported-24" in imported.headers["location"]
    page = client.get(imported.headers["location"])
    assert "Imported 24 REAL Google results" in page.text and "pull date not recorded" in page.text
    bad = client.post("/p/dirt-crew/import", data={"csrf": token}, files={"csv_file": ("x.txt", b"a,b", "text/plain")})
    assert bad.status_code == 400


def test_new_project_without_google_shows_not_run_and_a_failed_pull_names_the_keys(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects")
    client = TestClient(app)
    token = csrf(client)
    hostile = client.post("/projects", data={"csrf": token, "name": "<script>alert(1)</script>Crew", "site": "www.example.com", "searches": "a search"}, follow_redirects=False)
    slug = hostile.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/p/{slug}")
    assert "<script>alert(1)</script>" not in page.text and "&lt;script&gt;" in page.text
    assert "not run: needs SERPAPI_KEY, SERPER_API_KEY, or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD" in page.text
    assert "This project has no REAL Google results yet" in page.text and "import a CSV" in page.text
    started = client.post(f"/p/{slug}/pull", data={"csrf": token}, follow_redirects=False)
    job = wait(app, started.headers["location"])
    assert job["status"] == "error" and "SERPAPI_KEY" in job["error"] and "Nothing was invented" in job["error"]
    empty = client.post("/projects", data={"csrf": token, "name": "", "site": "x.com", "searches": "a"})
    assert empty.status_code == 400 and "needs a name" in empty.text


def test_simulation_runs_with_an_llm_and_the_key_stays_hidden(site, tmp_path):
    settings = make_settings(tmp_path, openai_api_key=SECRET)
    app = create_app(
        settings=settings,
        database=site["settings"].ranksim_database_url,
        projects_root=site["tmp"] / "projects",
        llm_factory=lambda: SourceReadingLlm(cite_by_domain(["dirtcrew"])),
    )
    client = TestClient(app)
    token = csrf(client, "/p/dirt-crew")
    form = client.get("/p/dirt-crew").text
    assert 'name="simulate"' in form
    started = client.post(
        "/p/dirt-crew/test",
        data={"csrf": token, "url": OUR_URL, "html_text": page_html("Dirt Work Software", "Dirt Work Software"), "simulate": "1"},
        follow_redirects=False,
    )
    job = wait(app, started.headers["location"])
    assert job["status"] == "done", job["error"]
    page = client.get(job["result_url"]).text
    assert "PREDICTED simulation" in page and "cited your page in" in page and "Not the real engine" in page
    assert "not pulled yet: the key is set now, press Pull now" in page  # ChatGPT has a key but no pull asked it
    assert SECRET not in page and SECRET not in client.get(job["result_url"] + ".json").text
    off = client.post("/p/dirt-crew/test", data={"csrf": token, "url": OUR_URL, "html_text": page_html("B", "B")}, follow_redirects=False)
    job = wait(app, off.headers["location"])
    assert "not run: switched off for this test" in client.get(job["result_url"]).text


def test_job_errors_are_scrubbed_of_keys(tmp_path):
    settings = make_settings(tmp_path, openai_api_key=SECRET)
    app = create_app(settings=settings, projects_root=tmp_path / "projects")
    ui = app.state.ui

    def boom(_job):
        raise RuntimeError(f"upstream said no for key {SECRET} at https://api.example/x?api_key=abc123")

    job = ui.jobs.start("test", "x", "boom", boom)
    done = ui.jobs.wait(job.id)
    assert done.status == "error" and SECRET not in done.error and "abc123" not in done.error and "[redacted]" in done.error


def test_host_check_blocks_dns_rebinding(tmp_path):
    app = create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", allowed_hosts=loopback_hosts(8765))
    client = TestClient(app)
    assert client.get("/").status_code == 400
    assert client.get("/", headers={"host": "127.0.0.1:8765"}).status_code == 200
    assert client.get("/", headers={"host": "localhost:8765"}).status_code == 200
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


def test_legacy_routes_redirect_sensibly(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects"))
    assert client.get("/check", follow_redirects=False).headers["location"] == "/demo"
    assert client.post("/check", data={"query": "x"}, follow_redirects=False).headers["location"] == "/"
    assert client.get("/demo").status_code == 404
    assert client.get("/?demo=1", follow_redirects=False).headers["location"] == "/demo?demo=1"
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200


def test_cli_serve_binds_loopback_by_default(monkeypatch, tmp_path):
    seen = {}

    def fake_run(app, host, port, log_level):
        seen.update(app=app, host=host, port=port)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    cli.main(["serve", "--projects-dir", str(tmp_path / "projects")])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8765
    client = TestClient(seen["app"])
    assert client.get("/").status_code == 400  # Host checked when bound to loopback
    assert client.get("/", headers={"host": "127.0.0.1:8765"}).status_code == 200
