"""B20 review fixes for stored results lists: Search Console lists and label files an older version
stored, re-imports of an earlier undated file, unreadable observed_on dates and draft recrawls.
Synthetic data."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.store import (
    RsPage,
    RsSerp,
    RsSerpCollection,
    append_serp,
    collection_dated,
    collection_hits,
    latest_collection,
    open_session,
    serp_collections,
    serp_for_query,
)

QUERY = "dirt work software"


def _hits(urls, provider="serper", is_google=True):
    return [SerpHit(position=index, url=url, title="", snippet="", provider=provider, synthetic=False, is_google=is_google)
            for index, url in enumerate(urls, start=1)]


def _at(day, hour=12, month=11):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


def _midnight(month, day):
    return datetime(2026, month, day, tzinfo=timezone.utc)


def _urls(*names):
    return [f"https://{name}.example/" for name in names]


def _legacy(session, query, urls, collected_at, recorded_at, positions=None):
    """A label_file collection as 87511c0 stored it: no dated flag (the migration adds NULL), signed
    with the day of collected_at. That version stored a dated file at UTC midnight of its observed
    day and an undated file at its import time."""
    pairs = list(zip(positions or range(1, len(urls) + 1), urls))
    body = "|".join(f"{position}:{url}" for position, url in pairs)
    collection_id = uuid.uuid4().hex
    session.add(RsSerpCollection(
        id=collection_id, query=query, provider="browser_export", synthetic=False, is_google=True, source="label_file",
        collected_at=collected_at, recorded_at=recorded_at, hits=len(pairs), dated=None,
        signature=hashlib.sha256(f"label_file|browser_export|{collected_at.date().isoformat()}|{body}".encode()).hexdigest(),
    ))
    for position, url in pairs:
        session.add(RsSerp(query=query, position=position, url=url, title="", snippet="", provider="browser_export",
                           synthetic=False, is_google=True, fetched_at=collected_at, collection_id=collection_id))
    session.commit()
    return collection_id


def _index(path, db, tmp_path, monkeypatch, fetched=None):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    attempted = []
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: attempted.extend(urls) or [(u, fetched(u)) for u in urls if fetched])
    summary = index_serp.index_serp_csv(path, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")
    return summary, attempted


def _cli_index(path, db, tmp_path, monkeypatch):
    import aeo.ranksim.cli as cli
    import aeo.ranksim.index_serp as index_serp

    real = index_serp.index_serp_csv
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: [])
    monkeypatch.setattr(cli, "index_serp_csv", lambda p, d, s, **_k: real(p, d, s, include_sitemap=False, saved_dir=tmp_path / "none"))
    cli.main(["--database", db, "index-serp", str(path)])


def _write(path, rows, observed_on=None):
    header = "query,url,position" + (",observed_on" if observed_on is not None else "")
    lines = [header] + [f"{q},{url},{i}" + (f",{observed_on(q)}" if observed_on is not None else "") for q, url, i in rows]
    path.write_text("\n".join(lines) + "\n")


def _list(query, urls):
    return [(query, url, i) for i, url in enumerate(urls, 1)]


def test_f3_search_console_lists_an_older_version_stored_are_marked_not_google(tmp_path, monkeypatch, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    a, b = "https://www.client.example/a", "https://www.client.example/b"
    imported = datetime(2026, 11, 1, 15, 4, 5, 123456, tzinfo=timezone.utc)
    session = open_session(db)
    # 87511c0 stored this Search Console file (positions 4.6 and 7.2) as an undated results list.
    _legacy(session, QUERY, [a, b], imported, imported + timedelta(milliseconds=8), positions=[5, 7])
    # The same list for another query is not from this file and stays a Google list.
    _legacy(session, "other query", [a, b], imported, imported + timedelta(milliseconds=9), positions=[5, 7])
    session.close()
    gsc = tmp_path / "gsc.csv"
    gsc.write_text(
        "Query,Page,Clicks,Impressions,CTR,Position\n"
        f"{QUERY},{a},3,100,3%,4.6\n"
        f"{QUERY},{b},1,80,1%,7.2\n"
    )
    _cli_index(gsc, db, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    session = open_session(db)
    [kept] = serp_collections(session, QUERY)  # kept, not deleted
    assert kept.is_google is False
    assert [hit.is_google for hit in collection_hits(session, kept.id)] == [False, False]
    assert serp_for_query(session, QUERY) == []  # calibrate and test skip it
    assert [c.is_google for c in serp_collections(session, "other query")] == [True]
    session.close()
    assert "1 results lists an older version stored from these averages are now marked as not Google results" in printed
    assert "calibrate and test skip them, and export-serp writes them with google=no, which readers drop." in printed
    out = tmp_path / "export.csv"
    main(["--database", db, "export-serp", str(out)])
    rows = [line.split(",") for line in out.read_text().splitlines()[1:]]
    assert {row[-1] for row in rows if row[0] == QUERY} == {"no"}
    only_gsc = tmp_path / "only-gsc.csv"
    only_gsc.write_text("".join(line + "\n" for line in out.read_text().splitlines() if "other query" not in line))
    with pytest.raises(ValueError, match="no usable rows"):
        load_serp_file(only_gsc)
    capsys.readouterr()
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "observe", str(only_gsc), "--observed-on", "2026-11-10", "--provider", "manual_browser"])
    assert "Recorded observations" not in capsys.readouterr().out
    # Indexing the file again finds nothing more to mark.
    summary, _ = _index(gsc, db, tmp_path, monkeypatch)
    assert summary["search_console_marked"] == 0


def _page_html(title, words=300):
    body = " ".join(f"w{i}" for i in range(words))
    return f"<html><head><title>{title}</title></head><body><main><p>{body} {title}</p></main></body></html>"


def test_f3_calibrate_and_test_never_read_a_marked_search_console_list(tmp_path, monkeypatch):
    from aeo.ranksim.keywords import Keyword
    from aeo.ranksim.loop import FetchResult, calibrate, page_from_html, run_test, store_fetched_page

    db = f"sqlite:///{tmp_path / 's.db'}"
    a, b, c = (f"https://www.client.example/{name}" for name in "abc")
    x, y = "https://x.example/", "https://y.example/"
    imported = datetime(2026, 11, 1, 15, 4, 5, 123456, tzinfo=timezone.utc)
    session = open_session(db)
    for url in (a, b, c, x, y):
        store_fetched_page(session, FetchResult(url=url, html=_page_html(url.rsplit("/", 2)[-2]), status="fetched"))
    # 87511c0 stored this Search Console file as results lists. "crew app" also has an earlier
    # undated browser export, so before the mark the Search Console list was its latest list.
    _legacy(session, QUERY, [a, b, c], imported, imported + timedelta(milliseconds=8), positions=[5, 7, 9])
    _legacy(session, "crew app", [x, y], imported - timedelta(days=1), imported - timedelta(days=1))
    _legacy(session, "crew app", [a, b], imported, imported + timedelta(milliseconds=9), positions=[2, 3])
    session.close()
    gsc = tmp_path / "gsc.csv"
    gsc.write_text(
        "Query,Page,Clicks,Impressions,CTR,Position\n"
        f"{QUERY},{a},3,100,3%,4.6\n{QUERY},{b},1,80,1%,7.2\n{QUERY},{c},1,80,1%,9.1\n"
        f"crew app,{a},5,90,5%,2.4\ncrew app,{b},2,60,3%,3.0\n"
    )
    summary, _ = _index(gsc, db, tmp_path, monkeypatch)
    assert summary["search_console_marked"] == 2
    session = open_session(db)
    assert len(serp_collections(session)) == 3  # nothing deleted
    assert serp_for_query(session, QUERY) == []
    assert [hit.url for hit in serp_for_query(session, "crew app")] == [x, y]
    report, label, provider = calibrate(session, [])
    assert (label, provider, report.n_queries, report.n_pairs) == ("calibrated_on_google_serp", "browser_export", 1, 1)
    keyword = Keyword(QUERY, "commercial", "", "", True, "")
    [delta] = run_test(session, [keyword], page_from_html(a, _page_html("a"), is_ours=True), replaces=a, source_label="t")
    assert delta.note == "No stored SERP for this query. No position was invented."
    session.close()


def test_f6_a_dated_label_file_stored_before_the_dated_column_keeps_its_date(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    older, newer = _urls("a", "b", "c", "d", "e"), _urls("b", "a", "c", "e", "d")
    imported = datetime(2026, 11, 2, 9, 30, 5, 123456, tzinfo=timezone.utc)
    session = open_session(db)
    # Two dated label files that 87511c0 stored: observed 2026-10-01 and 2026-10-15, imported later.
    _legacy(session, "q1", older, _midnight(10, 1), imported)
    _legacy(session, "q1", newer, _midnight(10, 15), imported + timedelta(seconds=1))
    # A live collect on 2026-10-05, then a dated label file observed 2026-10-15.
    append_serp(session, "q2", _hits(older), country="us", language="en", collected_at=_at(5, month=10))
    _legacy(session, "q2", newer, _midnight(10, 15), imported)
    # An undated file that 87511c0 stored at its import time stays undated.
    _legacy(session, "q3", older, imported, imported + timedelta(milliseconds=5))
    assert [collection_dated(c) for c in serp_collections(session, "q1")] == [True, True]
    assert [collection_dated(c) for c in serp_collections(session, "q3")] == [False]
    latest = latest_collection(session, "q2")
    assert latest.source == "label_file" and latest.collected_at.date().isoformat() == "2026-10-15"
    # Re-importing the older dated file does not make it the latest list.
    older_hits = _hits(older, provider="browser_export")
    assert append_serp(session, "q1", older_hits, provider="browser_export", collected_at=_midnight(10, 1), source="label_file") is None
    assert [hit.url for hit in serp_for_query(session, "q1")] == newer
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    printed = capsys.readouterr().out
    observed = {(row.query, row.url): row.observed_on for row in load_serp_file(out)}
    assert observed[("q1", newer[0])] == "2026-10-15" and observed[("q2", newer[0])] == "2026-10-15"
    assert observed[("q3", older[0])] is None
    assert "1 collections come from label files with no date" in printed
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "Noise ceiling was not computed" not in report and "one pair of collections for each of 1 queries" in report


def test_f7_f14_an_undated_import_never_marks_a_legacy_dated_list_undated(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.store as store
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    urls = _urls("a", "b", "c")
    session = open_session(db)
    legacy = _legacy(session, "q1", urls, _midnight(10, 15), datetime(2026, 10, 20, 9, 0, 3, 5, tzinfo=timezone.utc))
    session.close()
    undated = tmp_path / "serp.csv"
    _write(undated, _list("q1", urls))
    monkeypatch.setattr(store, "utcnow", lambda: _at(2))
    _index(undated, db, tmp_path, monkeypatch)
    session = open_session(db)
    collections = serp_collections(session, "q1")
    # The undated file is its own list, as on a fresh database, and the dated one keeps its date.
    assert [(c.id == legacy, c.dated, collection_dated(c)) for c in collections] == [(True, None, True), (False, False, False)]
    session.close()
    dated = tmp_path / "serp-2026-10-15.csv"
    _write(dated, _list("q1", urls), observed_on=lambda _q: "2026-10-15")
    monkeypatch.setattr(store, "utcnow", lambda: _at(3))
    _index(dated, db, tmp_path, monkeypatch)
    session = open_session(db)
    collections = serp_collections(session, "q1")
    assert [(c.id == legacy, c.dated) for c in collections] == [(True, True), (False, False)]
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    assert {row.observed_on for row in load_serp_file(out)} == {"2026-10-15"}
    assert "no date" not in capsys.readouterr().out


def test_an_undated_list_from_before_the_dated_column_stays_one_row_over_several_imports(tmp_path, monkeypatch):
    import aeo.ranksim.store as store

    db = f"sqlite:///{tmp_path / 's.db'}"
    urls = _urls("a", "b", "c")
    imported = datetime(2026, 11, 1, 15, 4, 5, 123456, tzinfo=timezone.utc)
    session = open_session(db)
    legacy = _legacy(session, "q1", urls, imported, imported + timedelta(milliseconds=5))
    session.close()
    serp = tmp_path / "serp.csv"
    _write(serp, _list("q1", urls))
    for day in (2, 3, 4):
        monkeypatch.setattr(store, "utcnow", lambda day=day: _at(day))
        _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [(c.id, c.dated) for c in serp_collections(session, "q1")] == [(legacy, False)]
    session.close()


def test_f12_reimporting_an_earlier_undated_file_makes_its_list_the_latest_again(tmp_path, monkeypatch):
    import aeo.ranksim.store as store

    db = f"sqlite:///{tmp_path / 's.db'}"
    serp = tmp_path / "serp.csv"
    for day, order in ((1, "abc"), (2, "bac"), (3, "abc")):  # edit the file, then revert the edit
        _write(serp, _list("q1", _urls(*order)))
        monkeypatch.setattr(store, "utcnow", lambda day=day: _at(day))
        _index(serp, db, tmp_path, monkeypatch)
        session = open_session(db)
        assert [hit.url for hit in serp_for_query(session, "q1")] == _urls(*order)
        session.close()
    session = open_session(db)
    stored = {c.id: c.collected_at for c in serp_collections(session, "q1")}
    session.close()
    assert len(stored) == 2  # one row per list
    # Running the latest file again changes nothing.
    monkeypatch.setattr(store, "utcnow", lambda: _at(4))
    _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert {c.id: c.collected_at for c in serp_collections(session, "q1")} == stored
    # A live collect still beats an undated file, even one imported after it.
    append_serp(session, "q1", _hits(_urls("c", "a", "b")), country="us", language="en", collected_at=_at(5))
    session.close()
    _write(serp, _list("q1", _urls(*"bac")))
    monkeypatch.setattr(store, "utcnow", lambda: _at(6))
    _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert len(serp_collections(session, "q1")) == 3
    assert latest_collection(session, "q1").source == "live"
    assert [hit.url for hit in serp_for_query(session, "q1")] == _urls("c", "a", "b")
    session.close()


def test_f13_a_date_that_is_not_yyyy_mm_dd_refuses_the_file_before_anything_is_stored(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, "q1", _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(1))
    session.close()
    serp = tmp_path / "serp-nov5.csv"  # a spreadsheet re-saved 2026-11-05 as 11/5/2026
    _write(serp, _list("q1", _urls("c", "b", "a")), observed_on=lambda _q: "11/5/2026")
    with pytest.raises(ValueError, match=r"'11/5/2026'.*YYYY-MM-DD"):
        _index(serp, db, tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        _cli_index(serp, db, tmp_path, monkeypatch)
    err = capsys.readouterr().err
    assert "11/5/2026" in err and "YYYY-MM-DD" in err
    session = open_session(db)
    assert [c.source for c in serp_collections(session, "q1")] == ["live"]
    assert session.query(RsPage).count() == 0  # nothing was crawled or stored
    session.close()


def test_f13_the_no_dates_message_is_only_for_a_file_with_no_observed_on_dates(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    for query in ("q1", "q2"):
        append_serp(session, query, _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(1))
    session.close()
    mixed = tmp_path / "mixed.csv"  # q1 has a date, q2 has none
    _write(mixed, _list("q1", _urls("c", "b", "a")) + _list("q2", _urls("c", "b", "a")),
           observed_on=lambda q: "2026-11-05" if q == "q1" else "")
    _cli_index(mixed, db, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    assert "This file has no" not in printed
    assert "1 queries in this file have no observed_on date and already have a dated results list" in printed
    undated = tmp_path / "undated.csv"
    _write(undated, _list("q2", _urls("b", "c", "a")))
    _cli_index(undated, db, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    assert "This file has no observed_on dates. 1 of its queries already have a dated results list" in printed


@pytest.mark.parametrize(
    ("label", "draft", "live", "recrawled"),
    [
        # The draft is at the slash twin of the label and the live page under a third spelling.
        ("https://www.client.example/compare/x/", "https://www.client.example/compare/x", "https://client.example/compare/x/", True),
        ("https://www.client.example/compare/x", "https://www.client.example/compare/x/", "https://client.example/compare/x/", True),
        ("http://client.example/compare/x", "https://client.example/compare/x/", "https://www.client.example/compare/x/", True),
        # The live page is at the labeled URL itself, which the join finds first: no recrawl.
        ("https://www.client.example/compare/x/", "https://www.client.example/compare/x", "https://www.client.example/compare/x/", False),
    ],
)
def test_f18_a_draft_the_training_join_reaches_does_not_block_the_recrawl(tmp_path, monkeypatch, label, draft, live, recrawled):
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.loop import FetchResult, store_fetched_page

    body = " ".join(f"w{i}" for i in range(300))
    html = f"<html><head><title>T</title></head><body><main><p>{body}</p></main></body></html>"
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url=draft, html=html.replace("T<", "DRAFT<"), status="fetched", source="draft_bundle"))
    store_fetched_page(session, FetchResult(url=live, html=html, status="fetched"))
    for i in range(2):
        store_fetched_page(session, FetchResult(url=f"https://o{i}.example/", html=html, status="fetched"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text(f"query,url,position\nq,{label},1\nq,https://o0.example/,2\nq,https://o1.example/,3\n")
    summary, attempted = _index(serp, db, tmp_path, monkeypatch, fetched=lambda url: FetchResult(url=url, html=html, status="fetched"))
    assert attempted == ([label] if recrawled else []) and summary["skipped"] == (2 if recrawled else 3)
    session = open_session(db)
    pages = load_corpus_docs(session)
    session.close()
    _, uncovered, _ = build_examples(load_serp_file(serp), pages, {})
    assert uncovered == 0


@pytest.mark.parametrize("order", ["live first", "draft first"])
def test_f18_a_draft_at_a_twin_spelling_never_takes_the_key_of_the_labeled_page(tmp_path, monkeypatch, order):
    """The live page is stored at exactly the labeled URL and a draft at its slash twin. Training,
    coverage and index-serp all reach the live page, whichever row was stored first, so the label
    is covered and index-serp does not fetch it again on every run."""
    from aeo.ranksim.benchmark import coverage_rows
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.loop import FetchResult, store_fetched_page

    label, draft = "https://www.client.example/compare/x", "https://www.client.example/compare/x/"
    html = _page_html("T")
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    rows = [
        FetchResult(url=label, html=html, status="fetched"),
        FetchResult(url=draft, html=_page_html("DRAFT"), status="fetched", source="draft_bundle"),
    ]
    for result in rows if order == "live first" else rows[::-1]:
        store_fetched_page(session, result)
    for i in range(2):
        store_fetched_page(session, FetchResult(url=f"https://o{i}.example/", html=html, status="fetched"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text(f"query,url,position\nq,{label},1\nq,https://o0.example/,2\nq,https://o1.example/,3\n")
    for _run in range(2):
        summary, attempted = _index(serp, db, tmp_path, monkeypatch, fetched=lambda url: FetchResult(url=url, html=html, status="fetched"))
        assert attempted == [] and summary["skipped"] == 3
    labels = load_serp_file(serp)
    session = open_session(db)
    pages = load_corpus_docs(session)
    coverage = coverage_rows(labels, session)
    session.close()
    assert pages[label].title == "T" and pages[draft].title == "DRAFT"
    assert (coverage[0]["reason"], coverage[0]["stored_url"]) == ("covered", label)
    _, uncovered, _ = build_examples(labels, pages, {})
    assert uncovered == 0
