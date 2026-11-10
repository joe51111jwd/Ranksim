"""B17 review fixes for SERP history: Search Console averages, undated label files, export-serp
--all and synthetic rows, noise --from-db pairing, draft recrawls and keyword files. Synthetic data."""

from datetime import datetime, timezone

import pytest

from aeo.ranksim.groundtruth import ImportReport, load_serp_file
from aeo.ranksim.keywords import load_keyword_file
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.store import append_serp, open_session, serp_collections

QUERY = "dirt work software"


def _hits(urls, provider="serper", is_google=True, synthetic=False):
    return [SerpHit(position=index, url=url, title="", snippet="", provider=provider, synthetic=synthetic, is_google=is_google)
            for index, url in enumerate(urls, start=1)]


def _at(day, hour=12):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)


def _urls(*names):
    return [f"https://{name}.example/" for name in names]


def _index(path, db, tmp_path, monkeypatch, fetched=None):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    attempted = []
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: attempted.extend(urls) or [(u, fetched(u)) for u in urls if fetched])
    summary = index_serp.index_serp_csv(path, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")
    return summary, attempted


def test_c0_search_console_averages_never_become_a_dated_snapshot(tmp_path, monkeypatch, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    gsc = tmp_path / "gsc.csv"
    gsc.write_text(
        "Query,Page,Clicks,Impressions,CTR,Position\n"
        f"{QUERY},https://www.client.example/a,3,100,3%,4.6\n"
        f"{QUERY},https://www.client.example/b,1,80,1%,7.2\n"
    )
    summary, _ = _index(gsc, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert serp_collections(session, QUERY) == []
    session.close()
    out = tmp_path / "export.csv"
    main(["--database", db, "export-serp", str(out)])
    assert out.read_text().splitlines()[1:] == []
    # The original file and anything exported are both kept out of the ledger.
    for path in (gsc, out):
        with pytest.raises(SystemExit):
            main(["--database", db, "ledger", "observe", str(path), "--observed-on", "2026-11-10", "--provider", "serper"])
    assert "Recorded observations" not in capsys.readouterr().out
    assert summary["search_console"] is True


def test_c8_an_undated_label_file_is_stored_once_and_never_dated_by_its_import(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.store as store
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\n" + "".join(f"q{n},{url},{i}\n" for n in (1, 2) for i, url in enumerate(_urls("a", "b", "c"), 1)))
    for day in (1, 2):  # re-run on a later day, e.g. to retry failed crawls
        monkeypatch.setattr(store, "utcnow", lambda day=day: _at(day))
        _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [len(serp_collections(session, q)) for q in ("q1", "q2")] == [1, 1]
    # A real, dated re-collection of q1 does not pair with the undated file.
    append_serp(session, "q1", _hits(_urls("c", "b", "a")), provider="browser_export", collected_at=_at(3), source="label_file")
    session.close()
    out = tmp_path / "export.csv"
    main(["--database", db, "export-serp", str(out), "--all"])
    rows = [line.split(",") for line in out.read_text().splitlines()[1:]]
    undated = [row for row in rows if row[0] == "q2"]
    assert undated and all(row[3] == "" and row[9] == "" for row in undated)
    capsys.readouterr()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "Noise ceiling was not computed" in report and "Could not pair 1 queries" in report


def test_c9_an_export_with_several_collections_of_a_query_is_refused_by_readers(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, QUERY, _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(3))
    append_serp(session, QUERY, _hits(_urls("c", "a", "b")), country="us", language="en", collected_at=_at(8))
    session.close()
    every = tmp_path / "all.csv"
    main(["--database", db, "export-serp", str(every), "--all"])
    with pytest.raises(ValueError, match="collections of the query"):
        load_serp_file(every)
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "observe", str(every), "--observed-on", "2026-11-08", "--provider", "serper"])
    assert "Recorded observations" not in capsys.readouterr().out
    # The latest export still loads as one list per query.
    latest = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(latest)])
    assert [(row.url, row.position) for row in load_serp_file(latest)] == [(url, float(i)) for i, url in enumerate(_urls("c", "a", "b"), 1)]


def test_c9_synthetic_rows_never_load_as_google_results(tmp_path):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, QUERY, _hits(_urls("a", "b")), collected_at=_at(3))
    append_serp(session, "mock query", _hits(_urls("m", "n"), provider="mock", is_google=False, synthetic=True), collected_at=_at(3))
    session.close()
    out = tmp_path / "with-mock.csv"
    main(["--database", db, "export-serp", str(out), "--include-synthetic"])
    report = ImportReport()
    labels = load_serp_file(out, report=report)
    assert {row.query for row in labels} == {QUERY}
    assert report.dropped == {"synthetic (mock) result, not Google": [4, 5]}


def test_c11_a_draft_at_the_labeled_url_is_recrawled_when_the_live_page_has_another_spelling(tmp_path, monkeypatch):
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import RsPage

    body = " ".join(f"w{i}" for i in range(300))
    html = f"<html><head><title>T</title></head><body><main><p>{body}</p></main></body></html>"
    labeled = "https://www.client.example/compare/x/"
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url=labeled, html=html.replace("T<", "DRAFT<"), status="fetched", source="draft_bundle"))
    store_fetched_page(session, FetchResult(url=labeled.rstrip("/"), html=html, status="fetched"))  # sitemap spelling
    for i in range(2):
        store_fetched_page(session, FetchResult(url=f"https://o{i}.example/", html=html, status="fetched"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text(f"query,url,position\nq,{labeled},1\nq,https://o0.example/,2\nq,https://o1.example/,3\n")
    summary, attempted = _index(serp, db, tmp_path, monkeypatch, fetched=lambda url: FetchResult(url=url, html=html, status="fetched"))
    assert attempted == [labeled] and summary["skipped"] == 2
    session = open_session(db)
    assert session.get(RsPage, labeled).source == "crawl"
    pages = load_corpus_docs(session)
    session.close()
    _, uncovered, _ = build_examples(load_serp_file(serp), pages, {})
    assert uncovered == 0


def test_c17_keyword_tool_exports_are_read_by_their_query_column(tmp_path):
    semrush = tmp_path / "semrush.csv"
    semrush.write_text('Keyword,Intent,Volume,Keyword Difficulty\nhcss alternative,commercial,320,12\n"crm, for contractors",informational,90,8\n')
    assert [(row.query, row.intent) for row in load_keyword_file(semrush)] == [
        ("hcss alternative", "commercial"), ("crm, for contractors", "informational")
    ]
    gsc = tmp_path / "gsc.csv"
    gsc.write_text("Top queries,Clicks,Impressions,CTR,Position\ndirt work software,12,340,3.5%,8.2\n")
    assert [row.query for row in load_keyword_file(gsc)] == ["dirt work software"]
    nameless = tmp_path / "nameless.csv"
    nameless.write_text("Term,Volume\npaving software,90\nasphalt estimating,40\n")
    with pytest.raises(ValueError, match="none is a query column"):
        load_keyword_file(nameless)
    plain = tmp_path / "plain.txt"
    plain.write_text("crm, for contractors\n")
    assert [row.query for row in load_keyword_file(plain)] == ["crm, for contractors"]


def test_c18_noise_from_db_pairs_only_the_same_source_provider_and_locale(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    # Google then Brave: not comparable.
    append_serp(session, "q brave", _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(1))
    append_serp(session, "q brave", _hits(_urls("c", "b", "a"), provider="brave", is_google=False), collected_at=_at(2))
    # A gb mobile browser export then Serper us/en: not comparable.
    append_serp(session, "q locale", _hits(_urls("a", "b", "c"), provider="browser_export"), country="gb", device="mobile",
                collected_at=_at(1), source="label_file")
    append_serp(session, "q locale", _hits(_urls("c", "b", "a")), country="us", language="en", collected_at=_at(2))
    # Two Serper us/en collections with a Brave one after them: the two Serper lists pair.
    append_serp(session, "q pair", _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(1))
    append_serp(session, "q pair", _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(2))
    append_serp(session, "q pair", _hits(_urls("c", "b", "a"), provider="brave", is_google=False), collected_at=_at(3))
    session.close()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "one pair of collections for each of 1 queries" in report
    assert "Could not pair 2 queries" in report and "live serper us/en/default (1 queries)" in report
    assert "Pairwise agreement, URLs present both days: 1.000" in report and "Top-1 same rate: 1.000" in report


def test_u2_a_same_day_recollect_that_found_the_same_list_is_kept(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    stable = _urls(*"abcdefgh")
    for hour in (1, 17):
        for query in ("q1", "q2", "q3"):
            append_serp(session, query, _hits(stable), collected_at=_at(10, hour))
        moved = stable if hour == 1 else [stable[1], stable[0], *stable[2:]]
        append_serp(session, "q4", _hits(moved), collected_at=_at(10, hour))
    assert [len(serp_collections(session, q)) for q in ("q1", "q2", "q3", "q4")] == [2, 2, 2, 2]
    # Storing the very same collection again still adds nothing.
    assert append_serp(session, "q1", _hits(stable), collected_at=_at(10, 17)) is None
    session.close()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "one pair of collections for each of 4 queries" in report and "Top-1 same rate: 0.750" in report
    assert "4 of 4 pairs were collected on the same day" in report


# Review follow-up of the fixes above.


def _cli_index(path, db, tmp_path, monkeypatch):
    import aeo.ranksim.cli as cli
    import aeo.ranksim.index_serp as index_serp

    real = index_serp.index_serp_csv
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: [])
    monkeypatch.setattr(cli, "index_serp_csv", lambda p, d, s, **_k: real(p, d, s, include_sitemap=False, saved_dir=tmp_path / "none"))
    cli.main(["--database", db, "index-serp", str(path)])


def test_noise_pairs_across_days_before_a_same_day_retry(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    stable = _urls(*"abcdefgh")
    swapped = [stable[1], stable[0], *stable[2:]]
    for query in ("q1", "q2", "q3", "q4"):
        append_serp(session, query, _hits(stable), country="us", language="en", collected_at=_at(1, 12))
        day2 = stable if query in ("q1", "q2") else swapped
        append_serp(session, query, _hits(day2), country="us", language="en", collected_at=_at(2, 9))
        # A retry later on day 2 found the same list. It must not replace the day 1 vs day 2 pair.
        assert append_serp(session, query, _hits(day2), country="us", language="en", collected_at=_at(2, 10))
    # A query collected twice on one day only still gets its same-day pair.
    for hour in (9, 10):
        append_serp(session, "q5", _hits(stable), country="us", language="en", collected_at=_at(2, hour))
    session.close()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "Snapshots match exactly" not in report and "Top-1 same rate: 0.600" in report
    assert "1 of 5 pairs were collected on the same day" in report
    assert "one pair of collections for each of 5 queries" in report


def test_noise_never_pairs_lists_that_are_not_google(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    for day, order in ((1, "abc"), (2, "cba")):
        append_serp(session, "q brave", _hits(_urls(*order), provider="brave", is_google=False), collected_at=_at(day))
    session.close()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "Noise ceiling was not computed" in report and "Could not pair 1 queries" in report


def test_reimporting_an_undated_file_after_a_live_collect_adds_nothing(tmp_path, monkeypatch):
    import aeo.ranksim.store as store
    from aeo.ranksim.store import latest_collection

    db = f"sqlite:///{tmp_path / 's.db'}"
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\n" + "".join(f"q1,{url},{i}\n" for i, url in enumerate(_urls("a", "b", "c"), 1)))
    monkeypatch.setattr(store, "utcnow", lambda: _at(1))
    _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    append_serp(session, "q1", _hits(_urls("c", "a", "b")), country="us", language="en", collected_at=_at(2))
    session.close()
    monkeypatch.setattr(store, "utcnow", lambda: _at(3))  # re-run to retry failed crawls
    _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [c.source for c in serp_collections(session, "q1")] == ["label_file", "live"]
    assert latest_collection(session, "q1").source == "live"
    session.close()


def test_an_undated_file_imported_after_a_dated_list_does_not_become_the_latest(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.store as store
    from aeo.ranksim.cli import main
    from aeo.ranksim.store import latest_collection, serp_for_query

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, "q1", _hits(_urls("c", "a", "b")), country="us", language="en", collected_at=_at(2))
    session.close()
    serp = tmp_path / "serp.csv"  # observed earlier, but the file does not say when
    serp.write_text("query,url,position\n" + "".join(f"{q},{url},{i}\n" for q in ("q1", "q2") for i, url in enumerate(_urls("a", "b", "c"), 1)))
    monkeypatch.setattr(store, "utcnow", lambda: _at(3))
    _cli_index(serp, db, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    session = open_session(db)
    assert latest_collection(session, "q1").source == "live"
    assert [row.url for row in serp_for_query(session, "q1")] == _urls("c", "a", "b")
    assert latest_collection(session, "q2").source == "label_file"  # nothing dated to prefer
    session.close()
    assert "1 of its queries already have a dated results list, which stays their latest list" in printed
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    rows = {(row.query, row.url): (row.position, row.observed_on) for row in load_serp_file(out)}
    assert rows[("q1", _urls("c")[0])] == (1.0, "2026-11-02") and rows[("q2", _urls("a")[0])] == (1.0, None)


def test_an_undated_collection_from_before_the_dated_column_is_matched_on_reimport(tmp_path, monkeypatch):
    import hashlib

    import aeo.ranksim.store as store
    from aeo.ranksim.store import RsSerpCollection

    db = f"sqlite:///{tmp_path / 's.db'}"
    serp = tmp_path / "serp.csv"
    urls = _urls("a", "b", "c")
    serp.write_text("query,url,position\n" + "".join(f"q1,{url},{i}\n" for i, url in enumerate(urls, 1)))
    monkeypatch.setattr(store, "utcnow", lambda: _at(1, 15))
    _index(serp, db, tmp_path, monkeypatch)
    # Make the row look like one written before the dated column: no flag, and signed with its import day.
    session = open_session(db)
    row = session.query(RsSerpCollection).one()
    body = "|".join(f"{i}:{url}" for i, url in enumerate(urls, 1))
    row.dated = None
    row.signature = hashlib.sha256(f"label_file|browser_export|2026-11-01|{body}".encode()).hexdigest()
    session.commit()
    session.close()
    monkeypatch.setattr(store, "utcnow", lambda: _at(5))
    _index(serp, db, tmp_path, monkeypatch)
    session = open_session(db)
    collections = serp_collections(session, "q1")
    assert len(collections) == 1 and collections[0].dated is False
    session.close()


def test_keyword_lists_with_commas_and_semicolon_or_tab_exports(tmp_path):
    two_comma = tmp_path / "two_comma.txt"
    two_comma.write_text("crm, for contractors\npaving software, free\n")
    assert [row.query for row in load_keyword_file(two_comma)] == ["crm, for contractors", "paving software, free"]
    one_column = tmp_path / "one_column.csv"
    one_column.write_text('query\ncrm, for contractors\n"paving software, free"\n')
    assert [row.query for row in load_keyword_file(one_column)] == ["crm, for contractors", "paving software, free"]
    semi = tmp_path / "semi.csv"
    semi.write_text("Keyword;Intent;Volume\nhcss alternative;commercial;320\n")
    assert [(row.query, row.intent) for row in load_keyword_file(semi)] == [("hcss alternative", "commercial")]
    tab = tmp_path / "tab.tsv"
    tab.write_text("Keyword\tVolume\nhcss alternative\t320\n")
    assert [row.query for row in load_keyword_file(tab)] == ["hcss alternative"]
    german = tmp_path / "german.csv"
    german.write_text("Suchbegriff;Suchvolumen\nbaggersoftware;90\nbausoftware;1.200\n")
    with pytest.raises(ValueError, match="none is a query column"):
        load_keyword_file(german)


def test_export_serp_marks_lists_that_are_not_google_and_readers_drop_them(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, "q both", _hits(_urls("a", "b")), country="us", language="en", collected_at=_at(1))
    append_serp(session, "q both", _hits(_urls("b", "a"), provider="brave", is_google=False), collected_at=_at(2))
    append_serp(session, "q brave", _hits(_urls("c", "d"), provider="brave", is_google=False), collected_at=_at(2))
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    printed = capsys.readouterr().out
    report = ImportReport()
    labels = load_serp_file(out, report=report)
    # The later Brave list does not hide the Google one, and a Brave-only query is not read as Google.
    assert [(row.query, row.url, row.position) for row in labels] == [("q both", _urls("a")[0], 1.0), ("q both", _urls("b")[0], 2.0)]
    assert report.dropped == {"not a Google result (brave)": [4, 5]}
    assert "not Google results (brave)" in printed
    brave_only = tmp_path / "brave.csv"
    brave_only.write_text("".join(line + "\n" for line in out.read_text().splitlines() if "q both" not in line))
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "observe", str(brave_only), "--observed-on", "2026-11-02", "--provider", "serper"])
    assert "Recorded observations" not in capsys.readouterr().out
