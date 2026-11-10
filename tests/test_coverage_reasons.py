"""Per-label coverage: every labeled row gets one reason, the table can be written as CSV, and
bundled folders are found from the repo root whatever the working directory. Synthetic fixtures
except the bundled draft, which is read from the repo."""

import csv
from pathlib import Path

from aeo.ranksim.benchmark import COVERAGE_COLUMNS, coverage_rows, labeled_coverage, write_coverage_csv
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.store import RsPage, open_session

REPO = Path(__file__).resolve().parents[1]
QUERY = "dirt work software"


def _html(words: int) -> str:
    body = " ".join(f"word{index}" for index in range(words))
    return f"<html><head><title>Dirt work</title></head><body><main><h1>Dirt work</h1><p>{body}</p></main></body></html>"


def _db(tmp_path):
    db = f"sqlite:///{tmp_path / 'c.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url="https://full.example/", html=_html(400), status="fetched"))
    store_fetched_page(session, FetchResult(url="https://thin.example/", html=_html(40), status="fetched"))
    store_fetched_page(session, FetchResult(url="https://empty.example/", html="", status="fetched"))
    store_fetched_page(session, FetchResult(url="https://robots.example/", html="", status="blocked", error="robots.txt"))
    store_fetched_page(session, FetchResult(url="https://gone.example/", html="", status="error", error="HTTP 404"))
    store_fetched_page(session, FetchResult(url="https://draft.example/", html=_html(400), status="fetched", source="draft_bundle"))
    session.close()
    labels = [
        SerpLabel(QUERY, url, position, None, "manual_csv")
        for position, url in enumerate(
            [
                "https://full.example",  # slash variant still matches
                "https://thin.example/",
                "https://empty.example/",
                "https://robots.example/",
                "https://gone.example/",
                "https://draft.example/",
                "https://never.example/",
            ],
            start=1,
        )
    ]
    return db, labels


def test_every_label_gets_one_reason_and_thin_is_its_own(tmp_path):
    db, labels = _db(tmp_path)
    session = open_session(db)
    try:
        rows = coverage_rows(labels, session)
        counts = labeled_coverage(labels, session)
    finally:
        session.close()
    assert [row["reason"] for row in rows] == ["covered", "thin", "empty", "blocked", "error", "draft_only", "not_attempted"]
    assert counts == {"covered": 1, "thin": 1, "empty": 1, "blocked": 1, "error": 1, "draft_only": 1, "not_attempted": 1}
    assert rows[0]["stored_url"] == "https://full.example/" and rows[4]["error"] == "HTTP 404"
    out = tmp_path / "coverage.csv"
    write_coverage_csv(rows, out)
    with out.open(newline="") as handle:
        written = list(csv.DictReader(handle))
    assert list(written[0]) == list(COVERAGE_COLUMNS) and len(written) == len(labels)


def test_evaluate_prints_reasons_and_writes_the_table_from_any_directory(tmp_path, monkeypatch, capsys):
    from aeo.ranksim.cli import main

    db, labels = _db(tmp_path)
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\n" + "".join(f"{row.query},{row.url},{int(row.position)}\n" for row in labels))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    main(["--database", db, "evaluate", str(serp), "--coverage-csv", str(tmp_path / "cov.csv")])
    out = capsys.readouterr().out
    assert "Coverage of labeled rows: covered 1, thin 1, empty 1, draft_only 1, blocked 1, error 1, not_attempted 1." in out
    assert (tmp_path / "cov.csv").is_file()


def test_bundled_folders_are_found_from_the_repo_root(tmp_path, monkeypatch):
    from aeo.ranksim.bootstrap import draft_folders, flag_known_drafts
    from aeo.ranksim.saved_html import saved_html_dir

    manifest = REPO / "smartcrew" / "pr8_v3" / "manifest.csv"
    with manifest.open(newline="") as handle:
        first = next(csv.DictReader(handle))
    draft_html = (manifest.parent / first["file"]).read_text(encoding="utf-8")
    db = f"sqlite:///{tmp_path / 'd.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url=first["url"], html=draft_html, status="fetched", source="unknown"))
    session.close()
    monkeypatch.chdir(tmp_path)  # before: Path("smartcrew/pr8_v3") was read relative to the cwd
    assert saved_html_dir() == REPO / "smartcrew" / "saved_html"
    assert all(folder.is_absolute() and folder.is_dir() for folder in draft_folders())
    assert flag_known_drafts(db) >= 1
    session = open_session(db)
    try:
        assert session.query(RsPage).filter(RsPage.url == first["url"]).one().source == "draft_bundle"
    finally:
        session.close()
