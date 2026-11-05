"""Stored pages keep their provenance, and a failed refetch never erases a good snapshot."""

import sqlite3
from datetime import date

from aeo.ranksim.benchmark import labeled_coverage
from aeo.ranksim.bootstrap import seed_corpus_if_empty
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.store import RsPage, open_session

HTML = "<html><head><title>Dirt work software</title></head><body><h1>Dirt work</h1><p>{}</p></body></html>"


def test_failed_refetch_keeps_the_last_good_snapshot(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    url = "https://a.example/page"
    store_fetched_page(session, FetchResult(url=url, html=HTML.format("good " * 200), status="fetched"))
    first = session.get(RsPage, url)
    good_sha, good_at = first.html_sha256, first.fetched_at
    assert good_sha and first.source == "crawl"
    store_fetched_page(session, FetchResult(url=url, html="", status="error", error="HTTP 403"))
    row = session.get(RsPage, url)
    assert row.status == "fetched"
    assert "good" in row.raw_html
    assert row.html_sha256 == good_sha and row.fetched_at == good_at
    assert row.last_attempt_status == "error" and row.last_attempt_error == "HTTP 403"
    store_fetched_page(session, FetchResult(url=url, html=HTML.format("newer " * 200), status="fetched"))
    row = session.get(RsPage, url)
    assert "newer" in row.raw_html and row.html_sha256 != good_sha
    assert row.last_attempt_status == "fetched"
    session.close()


def test_failed_first_fetch_is_recorded(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    store_fetched_page(session, FetchResult(url="https://b.example/", html="", status="blocked", error="robots"))
    row = session.get(RsPage, "https://b.example/")
    assert row.status == "blocked" and row.html_sha256 is None and row.last_attempt_status == "blocked"
    session.close()


def test_old_database_gets_new_columns_and_reads_as_unknown_source(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE rs_pages (url VARCHAR(2000) PRIMARY KEY, domain VARCHAR(255), status VARCHAR(32), error TEXT, "
        "raw_html TEXT, title TEXT, h1 TEXT, headings_text TEXT, body TEXT, word_count INTEGER, table_count INTEGER, "
        "has_faq BOOLEAN, link_count INTEGER, fetch_ms FLOAT, updated_label VARCHAR(64), authority FLOAT, "
        "authority_source VARCHAR(64), fetched_at DATETIME)"
    )
    con.execute(
        "INSERT INTO rs_pages VALUES ('https://old.example/', 'old.example', 'fetched', NULL, '<html>x</html>', 'Old', '', '', "
        "'body', 1, 0, 0, 0, NULL, NULL, NULL, 'unavailable', '2026-11-01 00:00:00')"
    )
    con.commit()
    con.close()
    session = open_session(f"sqlite:///{path}")
    pages = load_corpus_docs(session)
    session.close()
    assert pages["https://old.example/"].source == "unknown"
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(rs_pages)")}
    assert {"source", "html_sha256", "last_attempt_at", "last_attempt_status", "last_attempt_error"} <= columns


def test_seeded_drafts_are_kept_for_the_demo_but_never_used_as_examples(tmp_path, monkeypatch):
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo)
    db = f"sqlite:///{tmp_path / 'seed.db'}"
    note = seed_corpus_if_empty(db)
    assert "excluded from accuracy numbers" in note
    session = open_session(db)
    sources = {row.url: row.source for row in session.query(RsPage).all()}
    pages = load_corpus_docs(session)
    labels = load_serp_file("smartcrew/serp.csv")
    coverage = labeled_coverage(labels, session)
    session.close()
    drafts = [url for url, source in sources.items() if source == "draft_bundle"]
    assert len(drafts) == 6 and all("smartcrewhq.com" in url for url in drafts)
    assert set(sources.values()) == {"saved_html", "draft_bundle"}
    examples, uncovered, _ = build_examples(labels, pages, today=date(2026, 10, 29))
    assert not any("smartcrewhq.com" in row.url for row in examples)
    assert len(examples) + uncovered == len(labels)
    assert coverage["draft_only"] == 6
    assert coverage["covered"] == len(examples)


def test_draft_outlinks_do_not_feed_pagerank(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    target = "https://t.example/"
    store_fetched_page(session, FetchResult(url=target, html=HTML.format("t " * 200), status="fetched"))
    link = f'<html><body><a href="{target}">dirt work</a>{"x " * 200}</body></html>'
    store_fetched_page(session, FetchResult(url="https://d.example/", html=link, status="fetched", source="draft_bundle"))
    pages = load_corpus_docs(session)
    assert pages[target].anchors == ""
    store_fetched_page(session, FetchResult(url="https://c.example/", html=link, status="fetched"))
    pages = load_corpus_docs(session)
    session.close()
    assert "dirt work" in pages[target].anchors


def test_old_seeded_drafts_are_found_by_content(tmp_path, monkeypatch):
    import pathlib

    from aeo.ranksim.bootstrap import flag_known_drafts

    repo = pathlib.Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo)
    db = f"sqlite:///{tmp_path / 'old-seed.db'}"
    draft = (repo / "smartcrew/pr8_v3/home.html").read_text(encoding="utf-8", errors="replace")
    session = open_session(db)
    # How the old bootstrap stored it: a plain page under the live URL, no provenance.
    store_fetched_page(session, FetchResult(url="https://www.smartcrewhq.com/", html=draft, status="fetched", source="unknown"))
    store_fetched_page(session, FetchResult(url="https://other.example/", html=HTML.format("x " * 200), status="fetched"))
    session.close()
    assert flag_known_drafts(db) == 1
    session = open_session(db)
    assert session.get(RsPage, "https://www.smartcrewhq.com/").source == "draft_bundle"
    assert session.get(RsPage, "https://other.example/").source == "crawl"
    session.close()
    assert flag_known_drafts(db) == 0


def test_page_lookup_tolerates_trailing_slash_and_normalization(tmp_path):
    from aeo.crawl.urls import normalize_url
    from aeo.ranksim.store import page_by_url

    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    store_fetched_page(session, FetchResult(url="https://x.example/a/", html=HTML.format("a " * 200), status="fetched"))
    store_fetched_page(session, FetchResult(url="https://x.example/b", html=HTML.format("b " * 200), status="fetched"))
    assert page_by_url(session, normalize_url("https://x.example/a/")).url == "https://x.example/a/"
    assert page_by_url(session, "https://X.example/a").url == "https://x.example/a/"
    assert page_by_url(session, "https://x.example/b/").url == "https://x.example/b"
    assert page_by_url(session, "https://x.example/c") is None
    session.close()


def test_reextract_updates_derived_fields_but_not_the_snapshot(tmp_path):
    from aeo.ranksim.loop import reextract_pages

    session = open_session(f"sqlite:///{tmp_path / 'r.db'}")
    store_fetched_page(session, FetchResult(url="https://x.example/", html=HTML.format("w " * 300), status="fetched"))
    row = session.get(RsPage, "https://x.example/")
    sha, at = row.html_sha256, row.fetched_at
    row.word_count = 1  # as if an older extractor had undercounted
    session.commit()
    total, changed = reextract_pages(session)
    row = session.get(RsPage, "https://x.example/")
    assert (total, changed) == (1, 1) and row.word_count > 200
    assert row.html_sha256 == sha and row.fetched_at == at
    session.close()


def test_graph_command_keeps_existing_snapshots(tmp_path, monkeypatch):
    import aeo.ranksim.linkgraph as linkgraph
    from aeo.ranksim.cli import main
    from aeo.ranksim.linkgraph import GraphNode, LinkGraph

    db = f"sqlite:///{tmp_path / 'g.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url="https://x.example/a/", html=HTML.format("original " * 200), status="fetched", fetch_ms=120.0))
    session.close()
    graph = LinkGraph()
    graph.nodes["https://x.example/a"] = GraphNode(url="https://x.example/a", depth=0, status="fetched", html=HTML.format("refetched " * 200))
    graph.nodes["https://x.example/new"] = GraphNode(url="https://x.example/new", depth=1, status="fetched", html=HTML.format("new " * 200))
    monkeypatch.setattr(linkgraph, "collect_graph", lambda *a, **k: graph)
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\nq,https://x.example/a/,1\n")
    main(["--database", db, "graph", "--serp", str(serp), "--out", str(tmp_path / "g.json")])
    session = open_session(db)
    urls = sorted(row.url for row in session.query(RsPage).all())
    kept = session.get(RsPage, "https://x.example/a/")
    session.close()
    assert urls == ["https://x.example/a/", "https://x.example/new"]  # no slash twin
    assert "original" in kept.raw_html and kept.fetch_ms == 120.0 and kept.source == "crawl"
