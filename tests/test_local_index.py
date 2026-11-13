"""Local page index: candidates, fetch ladder, store, coverage, and BM25F."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from aeo.crawl.layered import BrowserRender
from aeo.ranksim.cli import main
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.localindex.candidates import expand_internal
from aeo.ranksim.localindex.credits import search_credits
from aeo.ranksim.localindex.features import feature_rows, retrieve_bm25f
from aeo.ranksim.localindex.parquet import read_parquet
from aeo.ranksim.localindex.records import Candidate, PageRecord, rank_band
from aeo.ranksim.localindex.build import run_build
from aeo.ranksim.store import open_session

SECRET = "super-secret-serper-key"
DIRT = "https://example.com/dirt"
BLOCKED = "https://example.com/blocked"
FEATURES = "https://example.com/features"
PRICING = "https://example.com/pricing"
GOOD_HTML = """<!doctype html><html><head>
<title>Dirt work software</title>
<meta name="description" content="Scheduling for crews">
<link rel="canonical" href="https://example.com/dirt">
<script type="application/ld+json">{"@type":"Article","datePublished":"2026-01-02"}</script>
</head><body><h1>Dirt work</h1><main>
<p>Heavy civil dirt work software for excavation contractors and crews who need one schedule.</p>
<a href="https://example.com/features">dirt work features</a>
<a href="https://example.com/login">login</a>
<a href="https://other.test/outside">outside</a>
<a href="https://example.com/pricing">dirt work pricing</a>
</main></body></html>"""
FEATURES_HTML = """<!doctype html><html><head><title>Features</title></head><body><main>
<p>Dirt work features for excavation software and crew scheduling across the job site.</p>
<a href="https://example.com/pricing">dirt work pricing</a>
</main></body></html>"""
CHALLENGE_HTML = "<html><head><title>Just a moment</title></head><body>captcha challenge-platform</body></html>"
ARCHIVE_HTML = (
    "<html><head><title>Archived dirt</title></head><body><main>"
    "<p>Archived dirt work software page with enough words for the excavation crews and the schedule.</p>"
    "</main></body></html>"
)
WARC = (
    "WARC/1.0\r\nWARC-Type: response\r\n\r\n"
    "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + ARCHIVE_HTML
)


def test_search_credits_and_rank_bands():
    assert search_credits(10) == 1
    assert search_credits(11) == 2
    assert search_credits(100) == 2
    assert rank_band(1) == "1-10"
    assert rank_band(20) == "11-20"
    assert rank_band(47) == "21-50"
    assert rank_band(80) == "51-100"
    assert rank_band(None) == "unlabeled"


def test_internal_depth_stops_at_the_configured_hop():
    home = PageRecord(url=DIRT, status="full", outlinks=[(FEATURES, "dirt work features"), ("https://example.com/login", "login")])
    features = PageRecord(url=FEATURES, status="full", outlinks=[(PRICING, "dirt work pricing")])
    candidates = [Candidate(DIRT, "dirt work software", 1, "snapshot", "snap")]
    pages = {DIRT: home, FEATURES: features}
    one = expand_internal(candidates, pages, depth=1, max_internal=50)
    assert {item.url for item in one} == {FEATURES}
    two = expand_internal(candidates, pages, depth=2, max_internal=50)
    assert {item.url for item in two} == {FEATURES, PRICING}
    assert expand_internal(candidates, pages, depth=0, max_internal=50) == []


def _write_snapshot(root: Path, *, with_features: bool = False) -> Path:
    snap = root / "snap"
    html = snap / "html"
    html.mkdir(parents=True)
    (html / "dirt.html").write_text(GOOD_HTML, encoding="utf-8")
    rows = [
        "url,final_url,status,file,captured_at,reason",
        "https://example.com/dirt,https://example.com/dirt,200,html/dirt.html,2026-11-13T15:46:00-05:00,",
        "https://example.com/blocked,,,,2026-11-13T15:46:00-05:00,robots_disallow",
    ]
    if with_features:
        (html / "features.html").write_text(FEATURES_HTML, encoding="utf-8")
        rows.append("https://example.com/features,https://example.com/features,200,html/features.html,2026-11-13T15:46:00-05:00,")
    (snap / "html_manifest.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (snap / "serp.csv").write_text(
        "query,url,position\n"
        "dirt work software,https://example.com/dirt,1\n"
        "dirt work software,https://example.com/blocked,2\n",
        encoding="utf-8",
    )
    return snap


def test_offline_seed_coverage_signals_and_parquet(tmp_path: Path):
    snap = _write_snapshot(tmp_path)
    index = tmp_path / "index"
    lines: list[str] = []
    code = run_build(
        index,
        snapshots=[str(snap)],
        num=100,
        depth=1,
        credit_cap=300,
        offline=True,
        allow_private=True,
        delay_seconds=0,
        emit=lines.append,
    )
    assert code == 0
    text = "\n".join(lines)
    assert text.index("Serper credit estimate before spending") < text.index("Top-10 text coverage")
    assert "Credits that will be spent: 0." in text
    assert "2 credits each, 2 credits" in text
    assert "Top-10 text coverage: 1 of 2 (50.0%)" in text
    assert "Rank band 1-10: 1 of 2 (50.0%)" in text
    assert "Query dirt work software: 1 of 2 (50.0%)" in text
    assert f"{BLOCKED}  best position 2  robots_disallow" in text
    assert "https://example.com/login" not in text
    assert "https://other.test/outside" not in text
    assert FEATURES in text and "offline" in text

    report = json.loads((index / "coverage.json").read_text(encoding="utf-8"))
    assert report["top10_meets_target"] is False
    assert report["top10_target"] == 0.95
    pages = read_parquet(index / "pages.parquet")
    dirt = next(row for row in pages if row["url"] == DIRT)
    assert dirt["status"] == "full"
    assert dirt["source"] == "snapshot_html"
    assert dirt["captured_on"] == "2026-11-13"
    assert dirt["canonical"] == DIRT
    assert "Scheduling for crews" in dirt["meta_description"]
    assert "Article" in dirt["schema_types"]
    assert dirt["published"].startswith("2026-01-02")
    assert dirt["word_count"] > 8
    assert len(dirt["content_hash"]) == 64
    assert "dirt work features" in dirt["anchor_text"] or "dirt work features" in json.dumps(pages)
    features = next(row for row in pages if row["url"] == FEATURES)
    assert features["anchor_text"] == "dirt work features"
    assert features["pagerank"] > next(row["pagerank"] for row in pages if row["url"] == BLOCKED)
    stats = read_parquet(index / "domain_stats.parquet")
    example = next(row for row in stats if row["domain"] == "example.com")
    assert example["pages"] >= 2
    edges = read_parquet(index / "edges.parquet")
    assert (DIRT, FEATURES) in {(row["src"], row["dst"]) for row in edges}


def test_bm25f_uses_full_text_and_anchor_text(tmp_path: Path):
    snap = _write_snapshot(tmp_path)
    index = tmp_path / "index"
    run_build(index, snapshots=[str(snap)], offline=True, depth=1, allow_private=True, delay_seconds=0, emit=lambda _line: None)
    ranked = retrieve_bm25f("dirt work software", index)
    scores = dict(ranked)
    assert scores[DIRT] > 0
    assert scores[FEATURES] > 0
    assert scores[DIRT] > scores.get(BLOCKED, 0)
    rows = feature_rows("dirt work software", [DIRT, BLOCKED], index)
    assert rows[0]["bm25f"] > 0
    assert rows[0]["content_missing"] == 0
    assert rows[1]["content_missing"] == 1.0


def test_index_overlays_sql_features(tmp_path: Path):
    snap = _write_snapshot(tmp_path)
    index = tmp_path / "index"
    run_build(index, snapshots=[str(snap)], offline=True, depth=0, allow_private=True, delay_seconds=0, emit=lambda _line: None)
    session = open_session(f"sqlite:///{tmp_path / 'rank.db'}")
    try:
        docs = load_corpus_docs(session, urls=[DIRT, BLOCKED], index_dir=str(index))
    finally:
        session.close()
    assert "dirt work software" in docs[DIRT].body
    assert docs[DIRT].content_missing is False
    assert docs[BLOCKED].content_missing is True


class _Browser:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []

    def render(self, url: str) -> BrowserRender:
        self.urls.append(url)
        return BrowserRender(html=self.html, visible_text="Dirt work software for excavation crews and scheduling.", title="Dirt")

    def close(self) -> None:
        return None


def _client(handler) -> tuple[httpx.Client, list]:
    calls: list[tuple[str, str, bytes]] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content))
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


def _html_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": "text/html"}, text=body)


def test_http_success_stops_the_ladder(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/open":
            return _html_response(GOOD_HTML)
        raise AssertionError(request.url)

    client, calls = _client(handler)
    browser = _Browser(GOOD_HTML)
    index = tmp_path / "index"
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "serp.csv").write_text("query,url,position\ndirt work software,https://example.com/open,1\n", encoding="utf-8")
    run_build(
        index,
        snapshots=[str(snap)],
        offline=False,
        skip_search=True,
        depth=0,
        use_browser=True,
        allow_private=True,
        delay_seconds=0,
        api_key=SECRET,
        client=client,
        browser=browser,
        credit_cap=300,
        emit=lambda _line: None,
    )
    assert browser.urls == []
    assert not any("scrape.serper.dev" in url or "archive.org" in url for _method, url, _body in calls)
    pages = read_parquet(index / "pages.parquet")
    assert pages[0]["source"] == "http"
    assert pages[0]["status"] == "full"
    assert SECRET not in (index / "index.sqlite").read_bytes().decode("latin1")


def test_robots_block_skips_our_crawler_and_uses_serper_scrape(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return _html_response("User-agent: *\nDisallow: /blocked\n")
        if request.url.host == "scrape.serper.dev":
            body = json.loads(request.content.decode())
            assert body == {"url": BLOCKED}
            assert request.headers["X-API-KEY"] == SECRET
            text = "Serper copy of dirt work software for excavation contractors and their crews."
            return httpx.Response(200, json={"text": text, "metadata": {"title": "Blocked", "description": "From serper"}, "credits": 2})
        if "blocked" in request.url.path:
            raise AssertionError("crawler fetched a disallowed URL")
        raise AssertionError(str(request.url))

    client, calls = _client(handler)
    browser = _Browser(GOOD_HTML)
    index = tmp_path / "index"
    snap = tmp_path / "empty"
    snap.mkdir()
    (snap / "serp.csv").write_text(f"query,url,position\ndirt work software,{BLOCKED},1\n", encoding="utf-8")
    lines: list[str] = []
    run_build(
        index,
        snapshots=[str(snap)],
        offline=False,
        skip_search=True,
        depth=0,
        use_browser=True,
        allow_private=True,
        delay_seconds=0,
        api_key=SECRET,
        client=client,
        browser=browser,
        credit_cap=300,
        user_agent="TestIndexBot",
        emit=lines.append,
    )
    assert browser.urls == []
    assert any(host == "scrape.serper.dev" for _method, host, _body in ((m, httpx.URL(u).host, b) for m, u, b in calls))
    page = next(row for row in read_parquet(index / "pages.parquet") if row["url"] == BLOCKED)
    assert page["source"] == "serper_scrape"
    assert page["status"] == "full"
    assert "From serper" in page["meta_description"]
    blob = (index / "index.sqlite").read_bytes().decode("latin1") + (index / "coverage.json").read_text() + "\n".join(lines)
    assert SECRET not in blob


def test_archive_is_last_and_the_newer_capture_wins(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/missing":
            return httpx.Response(403, headers={"content-type": "text/html"}, text="nope")
        if host == "scrape.serper.dev":
            return httpx.Response(500, json={"credits": 2, "message": "failed"})
        if host == "archive.org":
            return httpx.Response(200, json={"archived_snapshots": {"closest": {"available": True, "timestamp": "20261101000000", "status": "200"}}})
        if host == "index.commoncrawl.org" and request.url.path == "/collinfo.json":
            return httpx.Response(200, json=[{"id": "CC-TEST", "cdx-api": "https://index.commoncrawl.org/CC-TEST-index"}])
        if host == "index.commoncrawl.org":
            row = {"timestamp": "20261112000000", "filename": "crawl-data/test.warc.gz", "offset": "0", "length": "400", "status": "200"}
            return httpx.Response(200, text=json.dumps(row) + "\n")
        if host == "data.commoncrawl.org":
            assert request.headers["Range"].startswith("bytes=0-")
            return httpx.Response(206, text=WARC)
        if host == "web.archive.org":
            raise AssertionError("older wayback capture should not be fetched")
        raise AssertionError(str(request.url))

    client, calls = _client(handler)
    index = tmp_path / "index"
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "serp.csv").write_text("query,url,position\ndirt work software,https://example.com/missing,3\n", encoding="utf-8")
    run_build(
        index,
        snapshots=[str(snap)],
        offline=False,
        skip_search=True,
        depth=0,
        use_browser=False,
        allow_private=True,
        delay_seconds=0,
        api_key=SECRET,
        client=client,
        credit_cap=300,
        emit=lambda _line: None,
    )
    hosts = [httpx.URL(url).host for _method, url, _body in calls]
    assert hosts.index("scrape.serper.dev") < hosts.index("archive.org")
    assert hosts.index("archive.org") < hosts.index("data.commoncrawl.org")
    page = read_parquet(index / "pages.parquet")[0]
    assert page["source"] == "common_crawl"
    assert page["captured_on"] == "2026-11-12"
    assert page["status"] == "full"


def test_scrape_success_does_not_call_archives(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/missing":
            return httpx.Response(403)
        if request.url.host == "scrape.serper.dev":
            text = "Scraped dirt work software with enough words for excavation contractors and crews."
            return httpx.Response(200, json={"text": text, "metadata": {"title": "S"}, "credits": 2})
        if request.url.host in {"archive.org", "web.archive.org", "index.commoncrawl.org", "data.commoncrawl.org"}:
            raise AssertionError(request.url.host)
        raise AssertionError(str(request.url))

    client, _calls = _client(handler)
    index = tmp_path / "index"
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "serp.csv").write_text("query,url,position\ndirt work software,https://example.com/missing,1\n", encoding="utf-8")
    run_build(
        index,
        snapshots=[str(snap)],
        skip_search=True,
        depth=0,
        use_browser=False,
        allow_private=True,
        delay_seconds=0,
        api_key=SECRET,
        client=client,
        credit_cap=20,
        emit=lambda _line: None,
    )
    assert read_parquet(index / "pages.parquet")[0]["source"] == "serper_scrape"


def test_deep_search_pages_until_the_credit_cap(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            body = json.loads(request.content.decode())
            assert body["q"] == "dirt work software"
            assert body["gl"] == "us" and body["hl"] == "en"
            if body.get("page"):
                raise AssertionError("second page must stay inside the cap")
            assert body["num"] == 100
            organic = [{"title": f"P{i}", "link": f"https://example.com/p{i}", "position": i, "snippet": "s"} for i in range(1, 11)]
            return httpx.Response(200, json={"organic": organic, "credits": 2})
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(403)

    client, calls = _client(handler)
    index = tmp_path / "index"
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "serp.csv").write_text("query,url,position\ndirt work software,https://example.com/dirt,1\n", encoding="utf-8")
    lines: list[str] = []
    run_build(
        index,
        snapshots=[str(snap)],
        num=100,
        depth=0,
        credit_cap=2,
        use_browser=False,
        allow_private=True,
        delay_seconds=0,
        api_key=SECRET,
        client=client,
        emit=lines.append,
    )
    assert any("before spending" in line for line in lines)
    assert lines[0] == "Serper credit estimate before spending"
    search_at = next(i for i, (_m, url, _b) in enumerate(calls) if "google.serper.dev" in url)
    assert search_at >= 0
    candidates = read_parquet(index / "candidates.parquet")
    positions = {row["url"]: row["position"] for row in candidates if row["source"] == "serper"}
    assert positions["https://example.com/p10"] == 10
    assert "https://example.com/p11" not in positions
    assert not any("scrape.serper.dev" in url for _m, url, _b in calls)


def test_missing_serper_key_still_fetches_over_http(tmp_path: Path, capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {"google.serper.dev", "scrape.serper.dev"}:
            raise AssertionError("paid call without a key")
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/open":
            return _html_response(GOOD_HTML)
        raise AssertionError(str(request.url))

    client, _calls = _client(handler)
    index = tmp_path / "index"
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "serp.csv").write_text("query,url,position\ndirt work software,https://example.com/open,1\n", encoding="utf-8")
    lines: list[str] = []
    code = run_build(
        index,
        snapshots=[str(snap)],
        depth=0,
        use_browser=False,
        allow_private=True,
        delay_seconds=0,
        api_key=None,
        client=client,
        credit_cap=300,
        emit=lines.append,
    )
    assert code == 0
    text = "\n".join(lines)
    assert "SERPER_API_KEY is not set. Serper search and Serper scrape are skipped." in text
    assert "HTTP, the browser, Common Crawl, and Wayback still run." in text
    assert "Credits that will be spent: 0." in text
    assert read_parquet(index / "pages.parquet")[0]["source"] == "http"
    assert SECRET not in capsys.readouterr().out


def test_project_scope_uses_its_queries_and_competitors(tmp_path: Path):
    from aeo.ranksim.localindex.scope import project_index_dir, project_scope
    from aeo.ranksim.project import Project, save_project
    from aeo.ranksim.workspace import WorkspaceMeta, save_meta

    project = Project(
        name="Dirt",
        slug="dirt",
        site="dirt.example",
        searches=["dirt work software"],
        created="2026-11-13",
        root=str(tmp_path),
    )
    save_project(project)
    pull = project.dir / "pulls" / "20261113T000000Z"
    pull.mkdir(parents=True)
    (pull / "google.json").write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "search": "dirt work software",
                        "results": [{"position": 1, "url": "https://example.com/dirt"}],
                    },
                    {
                        "search": "unrelated gardening",
                        "results": [{"position": 1, "url": "https://example.com/garden"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    save_meta(project, WorkspaceMeta(competitors=["rival.example", "dirt.example"]))
    queries, seeds = project_scope(project)
    assert queries == ["dirt work software"]
    by_url = {item.url: item for item in seeds}
    assert by_url["https://example.com/dirt"].position == 1
    assert "https://example.com/garden" not in by_url
    assert by_url["https://rival.example/"].source == "competitor"
    assert by_url["https://dirt.example/"].source == "project_site"
    assert project_index_dir(project) == project.dir / "index"

    with pytest.raises(SystemExit) as built:
        main(
            [
                "index",
                "build",
                "--project",
                "dirt",
                "--projects-dir",
                str(tmp_path),
                "--offline",
                "--depth",
                "0",
                "--num",
                "100",
            ]
        )
    assert built.value.code in {0, None}
    text = (project.dir / "index" / "credit_estimate.txt").read_text(encoding="utf-8")
    assert "SERPER" not in text or "Credits that will be spent: 0." in text
    candidates = read_parquet(project.dir / "index" / "candidates.parquet")
    queries_kept = {row["query"] for row in candidates}
    assert queries_kept == {"dirt work software"}
    assert "https://rival.example/" in {row["url"] for row in candidates}


def test_api_index_job_reports_coverage_without_a_key(tmp_path: Path):
    from fastapi.testclient import TestClient

    from aeo.config import Settings
    from aeo.ranksim.project import Project, save_project
    from aeo.ranksim.serve import create_app
    from aeo.ranksim.workspace import WorkspaceMeta, save_meta

    project = Project(
        name="Dirt",
        slug="dirt",
        site="dirt.example",
        searches=["dirt work software"],
        created="2026-11-13",
        root=str(tmp_path / "projects"),
    )
    save_project(project)
    pull = project.dir / "pulls" / "20261113T000000Z"
    pull.mkdir(parents=True)
    (pull / "google.json").write_text(
        json.dumps({"searches": [{"search": "dirt work software", "results": [{"position": 1, "url": "https://example.com/dirt"}]}]}),
        encoding="utf-8",
    )
    save_meta(project, WorkspaceMeta(competitors=["rival.example"]))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {"google.serper.dev", "scrape.serper.dev"}:
            raise AssertionError("paid call without a key")
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return _html_response(GOOD_HTML)

    client_http, _calls = _client(handler)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'aeo.db'}",
        ranksim_database_url=f"sqlite:///{tmp_path / 'rank.db'}",
        serper_api_key=None,
        crawl_delay_seconds=0,
    )
    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        index_options={"client": client_http, "allow_private": True, "use_browser": False, "delay_seconds": 0},
    )
    web = TestClient(app)
    page = web.get("/p/dirt")
    assert page.status_code == 200
    assert "Build page index" in page.text
    assert "SERPER_API_KEY is not set" in page.text
    token = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    started = web.post("/p/dirt/index", data={"csrf": token}, follow_redirects=False)
    assert started.status_code == 303 and "job=" in started.headers["location"]
    job_id = started.headers["location"].split("job=")[-1]
    job = app.state.ui.jobs.wait(job_id, timeout=30)
    assert job is not None and job.status == "done", job.error if job else "missing job"
    data = app.state.ui.jobs.as_dict(job)
    steps = {item["step"]: item for item in data["steps"]}
    assert "SERPER_API_KEY is not set" in steps["index_estimate"]["message"]
    assert steps["index_coverage"]["done"] >= 1
    assert SECRET not in json.dumps(data)
    coverage = web.get("/p/dirt/index?format=json")
    assert coverage.status_code == 200
    body = coverage.json()
    assert body["slug"] == "dirt"
    assert body["top10_with_text"] >= 1
    assert body["top10_text_pct"] > 0


def test_cli_status_and_offline_build(tmp_path: Path):
    with pytest.raises(SystemExit) as missing:
        main(["index", "status", "--index-dir", str(tmp_path / "missing")])
    assert missing.value.code == 2
    snap = _write_snapshot(tmp_path, with_features=True)
    index = tmp_path / "index"
    with pytest.raises(SystemExit) as built:
        main(["index", "build", "--offline", "--index-dir", str(index), "--snapshot", str(snap), "--depth", "2", "--num", "100"])
    assert built.value.code == 0
    with pytest.raises(SystemExit) as status:
        main(["index", "status", "--index-dir", str(index)])
    assert status.value.code == 0
    urls = {row["url"] for row in read_parquet(index / "candidates.parquet")}
    assert PRICING in urls
    assert "https://example.com/login" not in urls
