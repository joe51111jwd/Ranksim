"""B21 review fixes for results lists that an older version (87511c0) stored from label files.

That version stored a dated label file and a Search Console file with a date column the same way:
is_google, at UTC midnight of the date, with no dated flag. A list on one site may therefore hold
Search Console averages. It is never read as a dated Google list (G0), and index-serp on a Search
Console file marks only the lists that version could have stored from that file (G3). Synthetic data.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.store import (
    RsSerp,
    RsSerpCollection,
    append_serp,
    collection_dated,
    collection_hits,
    latest_collection,
    open_session,
    pick_latest,
    serp_collections,
    serp_for_query,
)

QUERY = "crew scheduling app"
# A Search Console domain property: its pages can sit on subdomains of one site.
A, B, C = "https://www.client.example/a", "https://blog.client.example/b", "https://client.example/c"
IMPORTED = datetime(2026, 11, 2, 9, 30, 5, 123456, tzinfo=timezone.utc)
GSC_HEADER = "Date,Query,Page,Clicks,Impressions,CTR,Position\n"


def _midnight(month, day):
    return datetime(2026, month, day, tzinfo=timezone.utc)


def _legacy(session, query, pairs, collected_at, recorded_at=IMPORTED, is_google=True):
    """A list exactly as 87511c0's index-serp stored it, from (position, url) pairs sorted by
    position. A dated file (a label file, or a Search Console file with a date column) was stored at
    UTC midnight of its date and recorded at the import time; a file with no date at its import time.
    That version had no dated column, so the migration adds NULL. is_google=False is a list that
    bc813d6's index-serp marked as Search Console averages."""
    body = "|".join(f"{position}:{url}" for position, url in pairs)
    collection_id = uuid.uuid4().hex
    session.add(RsSerpCollection(
        id=collection_id, query=query, provider="browser_export", synthetic=False, is_google=is_google,
        source="label_file", collected_at=collected_at, recorded_at=recorded_at, hits=len(pairs), dated=None,
        signature=hashlib.sha256(f"label_file|browser_export|{collected_at.date().isoformat()}|{body}".encode()).hexdigest(),
    ))
    for position, url in pairs:
        session.add(RsSerp(query=query, position=position, url=url, title="", snippet="", provider="browser_export",
                           synthetic=False, is_google=is_google, fetched_at=collected_at, collection_id=collection_id))
    session.commit()
    return collection_id


def _live(session, query, urls, collected_at):
    hits = [SerpHit(position=i, url=url, title="", snippet="", provider="serper", synthetic=False, is_google=True)
            for i, url in enumerate(urls, 1)]
    return append_serp(session, query, hits, country="us", language="en", collected_at=collected_at)


def _index(path, db, tmp_path, monkeypatch):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: [])
    return index_serp.index_serp_csv(path, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")


def _cli_index(path, db, tmp_path, monkeypatch):
    import aeo.ranksim.cli as cli
    import aeo.ranksim.index_serp as index_serp

    real = index_serp.index_serp_csv
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: [])
    monkeypatch.setattr(cli, "index_serp_csv", lambda p, d, s, **_k: real(p, d, s, include_sitemap=False, saved_dir=tmp_path / "none"))
    cli.main(["--database", db, "index-serp", str(path)])


def _export(db, path, capsys):
    from aeo.ranksim.cli import main

    capsys.readouterr()
    main(["--database", db, "export-serp", str(path)])
    return capsys.readouterr().out


def _noise(db, capsys):
    from aeo.ranksim.cli import main

    capsys.readouterr()
    main(["--database", db, "noise", "--from-db"])
    return capsys.readouterr().out


def _hits(session, query):
    return [(hit.position, hit.url) for hit in serp_for_query(session, query)]


def test_g0_search_console_averages_stored_with_a_date_are_never_a_dated_google_list(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    # 87511c0 ran index-serp on two Search Console exports with a Date column.
    _legacy(session, QUERY, [(2, A), (5, B), (9, C)], _midnight(10, 1))
    _legacy(session, QUERY, [(2, B), (6, A), (9, C)], _midnight(10, 15), IMPORTED + timedelta(seconds=2))
    stored = serp_collections(session, QUERY)
    assert [collection_dated(c) for c in stored] == [False, False]
    session.close()
    report = _noise(db, capsys)
    assert "Noise ceiling was not computed" in report and "Agreement between two real snapshots" not in report
    assert (
        "1 queries have lists that an older version stored from a dated file. Such a file may have been a Search "
        "Console export of average positions, so those lists are not compared. "
        "Run index-serp again on each original file to classify it."
    ) in report
    out = tmp_path / "export.csv"
    printed = _export(db, out, capsys)
    assert {row.observed_on for row in load_serp_file(out)} == {None}
    assert ",2026-10-15," not in out.read_text()
    assert (
        "1 collections come from dated files that an older version stored. Such a file may have been a Search Console "
        "export of average positions, so their observed_on is blank. Run index-serp again on each original file to classify it."
    ) in printed
    for day in ("2026-10-15", "2026-11-10"):
        with pytest.raises(SystemExit):
            main(["--database", db, "ledger", "observe", str(out), "--observed-on", day, "--provider", "browser_export"])
        assert "Recorded observations" not in capsys.readouterr().out
    # A live collect on 2026-10-10 beats both lists, though one is stored at a later date.
    session = open_session(db)
    live_urls = ["https://rival.example/x", A, "https://other.example/y"]
    live = _live(session, QUERY, live_urls, datetime(2026, 10, 10, 14, tzinfo=timezone.utc))
    assert pick_latest(serp_collections(session, QUERY)).id == live
    assert latest_collection(session, QUERY).id == live
    assert _hits(session, QUERY) == list(enumerate(live_urls, 1))
    session.close()
    _export(db, out, capsys)
    assert {(row.url, row.observed_on, row.provider) for row in load_serp_file(out)} == {
        (url, "2026-10-10", "serper") for url in live_urls
    }


def test_g0_a_later_list_that_may_be_averages_keeps_an_older_dated_list_from_being_the_latest(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    snapshot = [(1, "https://rival.example/x"), (2, A), (3, "https://other.example/y")]
    session = open_session(db)
    # 87511c0 stored a dated results page (its URLs span several sites, so it is dated), then a
    # Search Console export with a Date column for the same query.
    _legacy(session, QUERY, snapshot, _midnight(10, 1))
    averages = _legacy(session, QUERY, [(2, B), (6, A), (9, C)], _midnight(10, 15))
    assert [collection_dated(c) for c in serp_collections(session, QUERY)] == [True, False]
    # The averages are not served, and the older page is not served as the latest one either.
    assert latest_collection(session, QUERY) is None and _hits(session, QUERY) == []
    session.close()
    out = tmp_path / "export.csv"
    printed = _export(db, out, capsys)
    assert out.read_text().splitlines()[1:] == []
    assert (
        "1 queries were left out. Each has a later list that an older version stored from a dated file, which may have "
        "been a Search Console export of average positions. Run index-serp again on that file to classify it."
    ) in printed
    # Running index-serp again on the Search Console file marks its list, and the page is the latest again.
    gsc = tmp_path / "gsc-2026-10-15.csv"
    gsc.write_text(GSC_HEADER + "".join(f"2026-10-15,{QUERY},{url},2,50,4%,{p}\n" for url, p in ((A, 6.2), (B, 1.8), (C, 8.9))))
    capsys.readouterr()
    _cli_index(gsc, db, tmp_path, monkeypatch)
    assert "1 results lists an older version stored from these averages are now marked as not Google results" in capsys.readouterr().out
    session = open_session(db)
    assert session.get(RsSerpCollection, averages).is_google is False
    assert _hits(session, QUERY) == [(position, url) for position, url in snapshot]
    session.close()
    printed = _export(db, out, capsys)
    assert {(row.url, row.observed_on) for row in load_serp_file(out)} == {(url, "2026-10-01") for _, url in snapshot}
    assert "left out" not in printed


def test_g0_reimporting_only_the_older_of_two_one_site_dated_files_does_not_make_it_the_latest(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    # 87511c0 imported two dated files that track one own page. A list on one site could also be
    # Search Console averages, so neither is read as dated until its file is imported again.
    _legacy(session, QUERY, [(3, A)], _midnight(10, 15))
    _legacy(session, QUERY, [(2, A)], _midnight(10, 29), IMPORTED + timedelta(days=1))
    assert _hits(session, QUERY) == [(2, A)]
    session.close()
    out = tmp_path / "export.csv"
    _export(db, out, capsys)
    assert [(row.position, row.observed_on) for row in load_serp_file(out)] == [(2.0, None)]
    files = {}
    for day, position in (("2026-10-15", 3), ("2026-10-29", 2)):
        files[day] = tmp_path / f"tracked-{day}.csv"
        files[day].write_text(f"query,url,position,observed_on\n{QUERY},{A},{position},{day}\n")
    _index(files["2026-10-15"], db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [c.dated for c in serp_collections(session, QUERY)] == [True, None]
    assert _hits(session, QUERY) == []
    session.close()
    printed = _export(db, out, capsys)
    assert out.read_text().splitlines()[1:] == [] and "1 queries were left out" in printed
    # Importing the later file too classifies it, and both lists are in full use.
    _index(files["2026-10-29"], db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [c.dated for c in serp_collections(session, QUERY)] == [True, True]
    assert _hits(session, QUERY) == [(2, A)]
    session.close()
    _export(db, out, capsys)
    assert [(row.position, row.observed_on) for row in load_serp_file(out)] == [(2.0, "2026-10-29")]
    report = _noise(db, capsys)
    assert "Comparing one pair of collections for each of 1 queries" in report and "older version" not in report


def test_g0_g3_a_search_console_file_with_dates_marks_only_the_list_of_its_own_day(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    same = [(2, B), (6, A), (9, C)]
    session = open_session(db)
    tracked = _legacy(session, QUERY, same, _midnight(10, 1))  # a dated label file with the same rounded list
    averages = _legacy(session, QUERY, same, _midnight(10, 15))  # the Search Console export below
    session.close()
    gsc = tmp_path / "gsc-2026-10-15.csv"
    gsc.write_text(GSC_HEADER + "".join(f"2026-10-15,{QUERY},{url},2,50,4%,{p}\n" for url, p in ((A, 6.2), (B, 1.8), (C, 8.9))))
    _cli_index(gsc, db, tmp_path, monkeypatch)
    assert "1 results lists an older version stored from these averages are now marked as not Google results" in capsys.readouterr().out
    session = open_session(db)
    assert session.get(RsSerpCollection, averages).is_google is False
    kept = session.get(RsSerpCollection, tracked)
    assert kept.is_google is True and [hit.is_google for hit in collection_hits(session, tracked)] == [True] * 3
    assert not collection_dated(kept)  # not a dated Google list until its own file is imported again
    assert _hits(session, QUERY) == same
    session.close()
    assert _index(gsc, db, tmp_path, monkeypatch)["search_console_marked"] == 0
    session = open_session(db)
    assert len(serp_collections(session, QUERY)) == 2  # nothing deleted
    session.close()


def test_g3_a_search_console_file_with_no_dates_never_marks_a_dated_list_an_older_version_stored(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    # 87511c0 imported a dated file of own-page positions ...
    dirt = _legacy(session, "dirt work software", [(1, A)], _midnight(10, 15))
    crew = _legacy(session, QUERY, [(3, B)], _midnight(10, 15))
    # ... and a Search Console file with no date for another query, stored at its import time.
    other = _legacy(session, "other query", [(5, A)], IMPORTED, IMPORTED + timedelta(milliseconds=8))
    session.close()
    gsc = tmp_path / "gsc.csv"  # averages that round to the tracked positions
    gsc.write_text(
        "Query,Page,Clicks,Impressions,CTR,Position\n"
        f"dirt work software,{A},30,400,7.5%,1.2\n{QUERY},{B},4,90,4.4%,3.4\nother query,{A},3,100,3%,4.6\n"
    )
    _cli_index(gsc, db, tmp_path, monkeypatch)
    printed = capsys.readouterr().out
    assert "1 results lists an older version stored from these averages are now marked as not Google results" in printed
    session = open_session(db)
    assert [session.get(RsSerpCollection, cid).is_google for cid in (dirt, crew, other)] == [True, True, False]
    assert _hits(session, "dirt work software") == [(1, A)] and _hits(session, QUERY) == [(3, B)]
    assert _hits(session, "other query") == []
    session.close()
    # Importing the dated file again dates both lists, and they stay Google results.
    tracked = tmp_path / "tracked-2026-10-15.csv"
    tracked.write_text(f"query,url,position,observed_on\ndirt work software,{A},1,2026-10-15\n{QUERY},{B},3,2026-10-15\n")
    _index(tracked, db, tmp_path, monkeypatch)
    session = open_session(db)
    assert [(c.dated, c.is_google) for c in (session.get(RsSerpCollection, cid) for cid in (dirt, crew))] == [(True, True)] * 2
    session.close()
    out = tmp_path / "export.csv"
    _export(db, out, capsys)
    observed = {(row.query, row.url): row.observed_on for row in load_serp_file(out)}
    assert observed == {("dirt work software", A): "2026-10-15", (QUERY, B): "2026-10-15"}


def test_g3_importing_the_dated_file_again_takes_off_a_mark_an_earlier_index_serp_put_on_it(tmp_path, monkeypatch, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    # A dated list of own-page positions that 87511c0 stored and bc813d6's index-serp then marked
    # as Search Console averages, because an undated Search Console file had the same rounded list.
    crew = _legacy(session, QUERY, [(3, B)], _midnight(10, 15), is_google=False)
    assert _hits(session, QUERY) == []
    session.close()
    tracked = tmp_path / "tracked-2026-10-15.csv"
    tracked.write_text(f"query,url,position,observed_on\n{QUERY},{B},3,2026-10-15\n")
    _index(tracked, db, tmp_path, monkeypatch)
    session = open_session(db)
    row = session.get(RsSerpCollection, crew)
    assert (row.dated, row.is_google) == (True, True)
    assert [hit.is_google for hit in collection_hits(session, crew)] == [True]
    assert _hits(session, QUERY) == [(3, B)] and len(serp_collections(session, QUERY)) == 1
    session.close()
    out = tmp_path / "export.csv"
    _export(db, out, capsys)
    assert [(row.url, row.observed_on) for row in load_serp_file(out)] == [(B, "2026-10-15")]
    assert ",no\n" not in out.read_text()
