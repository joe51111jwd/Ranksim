"""Offline tests for scripts/claude/refetch_html.py. No network, no keys, temp SQLite only."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

DATA_LIB = Path("/workspace/ranksim-data/lib/db.py")
if not DATA_LIB.exists():  # the raw-store helpers live outside git (CI has no copy)
    pytest.skip("ranksim-data lib not present on this machine", allow_module_level=True)

from scripts.claude import refetch_html as rf  # noqa: E402
from lib.db import init_db  # noqa: E402


def _serp(con, rows):
    con.executemany(
        "INSERT INTO serp_results (query, snapshot_date, fetched_at_utc, position, url, source) VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "raw.sqlite"
    con = init_db(path)
    _serp(
        con,
        [
            ("q", "2026-11-13", "t", 1, "https://old.example.com/", "daily:2026-11-13"),
            ("q", "2026-11-14", "t", 1, "https://a.example.com/x", "daily:2026-11-14"),
            ("q", "2026-11-14", "t", 2, "https://a.example.com/y", "daily:2026-11-14"),
            ("q", "2026-11-16", "t", 2, "https://a.example.com/x", "daily:2026-11-16"),
            ("q", "2026-11-16", "t", 11, "https://deep11.example.com/", "daily:2026-11-16"),
            ("q", "2026-11-16", "t", 3, "https://kw.example.com/", "keyword-sample:2026-11-16"),
            ("q", "2026-11-16", "t", 4, "https://pagerel.example.com/", "smartcrew-index-deep:2026-11-16"),
            ("q", "2026-11-16", "t", 5, "https://abspos.example.com/", "smartcrew-index-deep:2026-11-16:abspos"),
            ("q", "2026-11-16", "t", 25, "https://abspos25.example.com/", "smartcrew-index-deep:2026-11-16:abspos"),
            ("q", "2026-11-19", "t", 1, "https://b.example.org/", "daily:2026-11-19"),
            ("q", "2026-11-19", "t", None, "https://nopos.example.org/", "daily:2026-11-19"),
        ],
    )
    con.close()
    return path


def test_cutoff_is_new_york_today_minus_days():
    assert rf.cutoff_date(5, date(2026, 11, 19)) == "2026-11-14"
    assert rf.cutoff_date(0, date(2026, 11, 19)) == "2026-11-19"


def test_select_urls_applies_window_top10_and_source_exclusions(db):
    con = rf.connect(db)
    urls = rf.select_urls(con, "2026-11-14")
    assert urls == sorted(
        [
            "https://a.example.com/x",
            "https://a.example.com/y",
            "https://abspos.example.com/",
            "https://b.example.org/",
        ]
    )
    assert rf.select_urls(con, "2026-11-14", limit=2) == urls[:2]
    assert "https://old.example.com/" in rf.select_urls(con, "2026-11-13")
    con.close()


def _ok(url, body=b"<html><body>hi</body></html>"):
    return {
        "url": url,
        "status": 200,
        "mime": "text/html; charset=utf-8",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "captured_at": "2026-11-19T03:00:00.123456-05:00",
        "reason": None,
        "body": body,
    }


def _fail(url, reason, status=None):
    return {"url": url, "status": status, "mime": None, "bytes": 0, "sha256": None,
            "captured_at": None, "reason": reason, "body": None}


def test_page_row_matches_collector_convention():
    ok = rf.page_row(_ok("https://a.example.com/x"))
    assert ok["fetch_method"] == "http"
    assert ok["html"] == "<html><body>hi</body></html>"
    assert ok["fetched_at_utc"] == "2026-11-19T08:00:00.123Z"
    blocked = rf.page_row(_fail("https://a.example.com/y", "robots_disallow"))
    assert blocked["fetch_method"] == "http:robots_disallow"
    assert blocked["html"] is None
    # a 403 body is kept out of html but its status and hash stay, as in collect_snapshot
    rec = _ok("https://a.example.com/z")
    rec.update(status=403, reason="http_403")
    denied = rf.page_row(rec)
    assert denied["fetch_method"] == "http:http_403" and denied["html"] is None and denied["http_status"] == 403


def test_run_pass_appends_rows_every_pass_and_sidecar_hides_urls(db, tmp_path):
    con = rf.connect(db)
    urls = rf.select_urls(con, "2026-11-14")
    calls = []

    def fetch(url):
        calls.append(url)
        if "abspos" in url:
            return _fail(url, "robots_disallow")
        if "b.example.org" in url:
            raise ConnectionError("boom")
        return _ok(url)

    side1 = tmp_path / "side" / "refetch-1.jsonl"
    s1 = rf.run_pass(con, urls, fetch, side1, workers=3, batch_size=2, progress_every=0)
    assert sorted(calls) == urls  # one attempt per URL per pass
    assert s1["urls_selected"] == 4 and s1["rows_inserted"] == 4
    assert s1["fetched_ok"] == 2 and s1["failed"] == 2
    assert s1["failure_reasons"] == {"robots_disallow": 1, "error:ConnectionError": 1}
    assert s1["ok_no_prior_http_fetch"] == 2

    rows = con.execute("SELECT url, fetch_method, html FROM pages ORDER BY id").fetchall()
    assert len(rows) == 4
    assert {r[1] for r in rows} == {"http", "http:robots_disallow", "http:error:ConnectionError"}
    assert all((r[2] is None) == (r[1] != "http") for r in rows)

    text = side1.read_text()
    for url in urls:
        assert url not in text
    lines = [json.loads(line) for line in text.splitlines()]
    assert {line["url_sha1"] for line in lines} == {rf.url_sha1(u) for u in urls}
    assert all(line["page_id"] is not None for line in lines)

    # A second pass on unchanged pages still inserts dated rows (different fetched_at_utc).
    def fetch_later(url):
        rec = _ok(url)
        rec["captured_at"] = "2026-11-20T03:00:00.000000-05:00"
        return rec

    s2 = rf.run_pass(con, urls[:2], fetch_later, tmp_path / "side" / "refetch-2.jsonl", workers=2, progress_every=0)
    assert s2["rows_inserted"] == 2
    assert s2["ok_bytes_unchanged_vs_prior"] == 2
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 6
    con.close()


def test_write_batch_retries_when_locked(db):
    con = rf.connect(db)
    other = sqlite3.connect(db, timeout=0)
    other.execute("BEGIN IMMEDIATE")  # hold the write lock
    con.execute("PRAGMA busy_timeout = 0")
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            other.rollback()  # the other writer finishes

    ids = rf.write_batch(con, [rf.page_row(_ok("https://a.example.com/x"))], sleep=fake_sleep)
    assert len(sleeps) == 2 and ids[0] is not None
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 1
    other.close()
    con.close()


def test_pages_table_stays_append_only(db):
    con = rf.connect(db)
    rf.write_batch(con, [rf.page_row(_ok("https://a.example.com/x"))])
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM pages")
    con.close()
