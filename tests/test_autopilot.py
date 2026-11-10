"""Autopilot: a site in, a report out. Fakes for the crawler and Google; no network."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient
from test_project import OUR_URL, SITE, TODAY, FakeCrawler, _pages, page_html

from aeo.ranksim import autopilot
from aeo.ranksim.serve import create_app
from tests.test_hosted import FakeGoogle
from tests.test_serve_project import make_settings

HOME = f"https://{SITE}/"
SITEMAP = f"https://{SITE}/sitemap.xml"
APP_URL = f"https://{SITE}/excavation-crew-app/"
ABOUT = f"https://{SITE}/about/"
LONG_TITLE = "Excavation crew app for dispatch, timecards, daily logs and job costing | Dirt Crew"


def _site_pages() -> dict[str, str]:
    pages = dict(_pages())
    pages[HOME] = (
        "<html><head><title>Dirt work software | Dirt Crew</title></head><body>"
        "<h1>Dirt work software</h1><p>Short homepage.</p></body></html>"
    )
    pages[SITEMAP] = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{OUR_URL}</loc></url><url><loc>{APP_URL}</loc></url><url><loc>{ABOUT}</loc></url></urlset>"
    )
    pages[APP_URL] = (
        f"<html><head><title>{LONG_TITLE}</title><meta name=\"description\" content=\"Crew app.\"></head><body>"
        "<h1>Excavation crew app</h1><h2>Grading job costing</h2>"
        + "<p>" + " ".join(["Crews log hours on each excavation job and the office sees cost by phase."] * 40) + "</p></body></html>"
    )
    pages[ABOUT] = "<html><head><title>About us | Dirt Crew</title></head><body><h1>About us</h1><p>We build software.</p></body></html>"
    return pages


def _token(client: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def _lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_searches_come_from_titles_and_headings_without_the_brand():
    pages = [autopilot.page_facts(url, html) for url, html in _site_pages().items() if url.startswith(f"https://{SITE}/") and not url.endswith(".xml")]
    brand = autopilot.brand_of(pages, SITE)
    assert brand == "Dirt Crew"
    found = autopilot.discover_searches(pages, brand)
    assert "dirt work software" in found and "excavation crew app" in found
    assert "about us" not in found and not any("dirt crew" in phrase for phrase in found)
    assert len(found) == len(set(found))


def test_audit_names_the_page_the_numbers_and_the_fix():
    pages = [autopilot.page_facts(url, html) for url, html in _site_pages().items() if url in {HOME, APP_URL, ABOUT}]
    pages.append(autopilot.SitePage(url=f"https://{SITE}/broken/", status="error", error="HTTP 500"))
    issues = autopilot.audit(None, pages, [], {})
    kinds = {(issue["kind"], issue["page"]) for issue in issues}
    assert ("title-long", APP_URL) in kinds and ("thin", HOME) in kinds and ("meta-missing", HOME) in kinds
    assert ("unreadable", f"https://{SITE}/broken/") in kinds
    long = next(issue for issue in issues if issue["kind"] == "title-long")
    assert str(len(LONG_TITLE)) in long["title"] and "/excavation-crew-app/" in long["comment"] and long["fix"]
    assert [issue["severity"] for issue in issues] == sorted((issue["severity"] for issue in issues), key=["high", "medium", "low"].index)


def test_a_site_url_is_all_it_takes(tmp_path):
    settings = make_settings(tmp_path, serpapi_key="sk-test-autopilot")
    crawler = FakeCrawler(_site_pages())
    app = create_app(
        settings=settings,
        projects_root=tmp_path / "projects",
        public_demo=False,
        pull_options={"serp_provider": FakeGoogle(), "fetcher": crawler, "today": TODAY, "workers": 4},
    )
    client = TestClient(app)
    token = _token(client)
    response = client.post("/api/v1/sites", json={"site": f"https://{SITE}/some/page"}, headers={"x-ranksim-csrf": token})
    assert response.status_code == 200
    lines = _lines(response)
    steps = [line.get("step") for line in lines if line["type"] == "step"]
    assert {"site", "searches", "google", "pages", "model", "competitors", "report"} <= set(steps), steps
    done = lines[-1]
    assert done["type"] == "done", lines[-3:]
    slug = done["slug"]
    assert "dirt work software" in done["searches"]
    assert HOME in crawler.asked and SITEMAP in crawler.asked

    report = client.get(f"/api/v1/projects/{slug}/report").json()
    assert report["search_data"] and 2 <= report["kpis"]["checked"] <= report["kpis"]["searches"]  # the fake knows some searches
    row = next(item for item in report["rankings"] if item["q"] == "dirt work software")
    assert row["checked"] and row["top"] and all(1 <= item["position"] <= 10 for item in row["top"])
    assert report["competitors"] and all(domain.endswith(".example") for domain in report["competitors"])
    assert not any(domain.endswith(SITE.removeprefix("www.")) for domain in report["competitors"])
    assert report["share"][0]["slots"] >= report["share"][-1]["slots"]
    assert report["issues"] and {issue["severity"] for issue in report["issues"]} <= {"high", "medium", "low"}
    assert any(issue["kind"] == "title-long" for issue in report["issues"])
    assert report["pages_read"] >= 3

    again = client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token})
    assert _lines(again)[-1]["slug"] == slug  # the same site opens the same project


def test_without_a_google_key_the_pages_are_still_checked(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=False, pull_options={"fetcher": FakeCrawler(_site_pages()), "today": TODAY})
    client = TestClient(app)
    token = _token(client)
    lines = _lines(client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": token}))
    skipped = [line for line in lines if line.get("step") == "google"]
    assert skipped and skipped[0]["state"] == "skipped"
    report = client.get(f"/api/v1/projects/{lines[-1]['slug']}/report").json()
    assert report["search_data"] is False and report["google_ready"] is False
    assert report["issues"] and all(issue["kind"] not in {"not-ranking", "no-page", "behind"} for issue in report["issues"])


def test_an_unreadable_site_says_so(tmp_path):
    app = create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", public_demo=False, pull_options={"fetcher": FakeCrawler({})})
    client = TestClient(app)
    lines = _lines(client.post("/api/v1/sites", json={"site": "nothing.example"}, headers={"x-ranksim-csrf": _token(client)}))
    assert lines[-1]["type"] == "error" and "couldn't read nothing.example" in lines[-1]["error"]


def test_public_demo_cannot_analyze(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", public_demo=True))
    assert client.post("/api/v1/sites", json={"site": SITE}, headers={"x-ranksim-csrf": _token(client)}).status_code == 403


def test_page_html_fixture_still_reads():
    facts = autopilot.page_facts(OUR_URL, page_html("Dirt work software", "Dirt Work Software"))
    assert facts.status == "fetched" and facts.title and facts.words > 0
