"""The page facts cache: same facts as page_facts, a new parse when a page changes, a small JSON file. No network."""

from __future__ import annotations

import json
import os

from aeo.ranksim import pagefacts
from aeo.ranksim import workspace as ws
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.project import create_project
from aeo.ranksim.report import build_report
from aeo.ranksim.site import page_facts
from aeo.ranksim.store import open_session

SITE = "www.cachecrew.example"
BODY_MARKER = "zzbodymarkerzz"


def _html(title: str) -> str:
    paragraph = f"<p>Crews log hours on each job {BODY_MARKER} and the office sees the cost by phase every day.</p>" * 12
    return f"<html><head><title>{title}</title><meta name='description' content='About {title}'></head><body><h1>{title}</h1><h2>Costs</h2>{paragraph}<a href='/b/'>b</a></body></html>"


def _setup(tmp_path):
    database = f"sqlite:///{tmp_path / 'rank.db'}"
    project = create_project("Cache Crew", SITE, ["job costing app"], root=tmp_path / "projects", discover_sitemap=False, max_site_pages=0)
    session = open_session(database)
    try:
        store_fetched_page(session, FetchResult(url=f"https://{SITE}/", html=_html("Home page of cache crew"), status="fetched", fetch_ms=50.0))
        store_fetched_page(session, FetchResult(url=f"https://{SITE}/a/", html=_html("Job costing for dirt crews"), status="fetched", fetch_ms=60.0))
        store_fetched_page(session, FetchResult(url=f"https://{SITE}/gone/", html="", status="error", error="HTTP 404"))
    finally:
        session.close()
    return project, database


def _report(project, database):
    return build_report(project, ws.load_meta(project), database)


def test_cached_facts_equal_page_facts_and_cannot_be_changed_by_a_caller():
    pagefacts.clear_memory()
    url, html = f"https://{SITE}/a/", _html("Job costing")
    first = pagefacts.cached_page_facts(url, html, fetch_ms=12.5)
    assert first == page_facts(url, html, fetch_ms=12.5)
    first.links.append("https://evil.example/")
    first.h2s.clear()
    assert pagefacts.cached_page_facts(url, html, fetch_ms=12.5) == page_facts(url, html, fetch_ms=12.5)
    assert pagefacts.cached_page_facts(url, "", "error", "HTTP 500") == page_facts(url, "", "error", "HTTP 500")


def test_report_uses_the_disk_cache_and_reparses_a_changed_page(tmp_path, monkeypatch):
    project, database = _setup(tmp_path)
    pagefacts.clear_memory()
    before = _report(project, database)
    path = tmp_path / "projects" / project.slug / "plugins" / "pagefacts" / "facts.json"
    text = path.read_text(encoding="utf-8")
    assert BODY_MARKER not in text and "<p>" not in text  # facts only, never the stored HTML
    assert len(json.loads(text)["facts"]) == 3

    # a new process: memory is empty, the file answers, nothing is parsed
    pagefacts.clear_memory()
    calls = []
    real = pagefacts.page_facts
    monkeypatch.setattr(pagefacts, "page_facts", lambda *args, **kwargs: calls.append(args[0]) or real(*args, **kwargs))
    stamp = path.stat().st_mtime_ns
    assert _report(project, database) == before and calls == []
    assert path.stat().st_mtime_ns == stamp  # nothing changed, so no write

    # a re-fetch with new HTML gets a new key: parsed again, and the report shows it
    session = open_session(database)
    try:
        store_fetched_page(session, FetchResult(url=f"https://{SITE}/a/", html=_html("Brand new title for the page"), status="fetched", fetch_ms=60.0))
    finally:
        session.close()
    after = _report(project, database)
    assert calls == [f"https://{SITE}/a/"]
    assert "Brand new title for the page" in [page["title"] for page in after["pages"]]
    assert "Job costing for dirt crews" not in [page["title"] for page in after["pages"]]
    assert len(json.loads(path.read_text(encoding="utf-8"))["facts"]) == 3  # the old entry is dropped


def test_a_corrupt_cache_file_is_ignored_and_rebuilt(tmp_path):
    project, database = _setup(tmp_path)
    pagefacts.clear_memory()
    expected = _report(project, database)
    path = tmp_path / "projects" / project.slug / "plugins" / "pagefacts" / "facts.json"
    for junk in ("not json", json.dumps({"version": 1, "facts": []}), json.dumps({"version": 999, "facts": {}})):
        path.write_text(junk, encoding="utf-8")
        pagefacts.clear_memory()
        assert _report(project, database) == expected
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == pagefacts.VERSION
    assert not [name for name in os.listdir(path.parent) if name.endswith(".tmp")]
