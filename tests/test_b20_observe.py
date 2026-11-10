"""B20 review fixes for observed results files: ledger observe takes no row dated another day, left
undated, or from another provider; noise FILE FILE pairs like with like; keyword exports with title
rows above the header. Synthetic data."""

import csv
from datetime import datetime, timezone

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.keywords import load_keyword_file
from aeo.ranksim.ledger import Call, RsOutcome, record_prediction
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.store import append_serp, open_session

OURS, RIVAL = "https://ours.example/p", "https://rival.example/"
LATER = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
CLOCK = {"now": LATER}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    import aeo.ranksim.ledger as ledger

    CLOCK["now"] = LATER
    monkeypatch.setattr(ledger, "_now", lambda: CLOCK["now"])


def _at(day, hour=9):
    return datetime(2026, 11, day, hour, tzinfo=timezone.utc)


def _hits(urls, provider="serper"):
    return [SerpHit(position=index, url=url, title="", snippet="", provider=provider, synthetic=False, is_google=True)
            for index, url in enumerate(urls, start=1)]


def _urls(*names):
    return [f"https://{name}.example/" for name in names]


def _predict(db, queries, day):
    CLOCK["now"] = _at(day, 12)
    session = open_session(db)
    try:
        for query in queries:
            record_prediction(
                session, kind="draft_check", query=query, subject_url=OURS, subject_html="x", predicted_rank=1,
                predicted_of=2, candidates=[], calls=[Call(RIVAL, "above", 1.0, "HIGH")], confidence="HIGH",
                calibration_note="", provenance={"model": "t"}, project_slug="default",
            )
    finally:
        session.close()
        CLOCK["now"] = LATER


def _outcomes(db):
    session = open_session(db)
    try:
        return session.query(RsOutcome).count()
    finally:
        session.close()


def _observe(db, path, day, provider="serper"):
    main(["--database", db, "ledger", "observe", str(path), "--observed-on", day, "--provider", provider, "--depth", "10"])


def test_f2_f5_ledger_observe_refuses_an_export_with_lists_from_other_days_or_undated(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    append_serp(session, "dirt work software", _hits([OURS, RIVAL]), country="us", language="en", collected_at=_at(1))
    append_serp(session, "grading software", _hits([OURS, RIVAL], provider="browser_export"), source="label_file")
    session.close()
    _predict(db, ["dirt work software", "grading software"], 10)
    session = open_session(db)
    append_serp(session, "excavation software", _hits([OURS, RIVAL]), country="us", language="en", collected_at=_at(20))
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    days = {row["query"]: row["observed_on"] for row in csv.DictReader(out.open())}
    assert days == {"dirt work software": "2026-11-01", "excavation software": "2026-11-20", "grading software": ""}
    capsys.readouterr()
    # The Nov 1 list and the undated label list must not be recorded as observed on Nov 20,
    # where they would resolve the Nov 10 predictions.
    with pytest.raises(SystemExit) as refused:
        _observe(db, out, "2026-11-20")
    message = str(refused.value)
    assert "Nothing was recorded" in message
    assert "Observed on another day: 1 of 3 queries, for example 'dirt work software' on 2026-11-01." in message
    assert "No observation date in a file that has dates: 1 of 3 queries, for example 'grading software'." in message
    assert "From another provider than serper: 1 of 3 queries, for example 'grading software' from browser_export." in message
    assert "Record each day and provider from its own file with that --observed-on and --provider, or re-collect" in message
    assert "\u2014" not in message and "\u2013" not in message
    assert _outcomes(db) == 0
    assert "Recorded observations" not in capsys.readouterr().out


def test_f5_a_hand_written_file_dated_another_day_is_refused_and_a_matching_one_is_recorded(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    _predict(db, ["dirt work software"], 10)
    nov01 = tmp_path / "nov01.csv"
    nov01.write_text(f"query,url,position,observed_on\ndirt work software,{OURS},1,2026-11-01\ndirt work software,{RIVAL},5,2026-11-01\n")
    with pytest.raises(SystemExit, match="Observed on another day: 1 of 1 queries"):
        _observe(db, nov01, "2026-11-20")
    # A collected_at timestamp is read as its day.
    stamped = tmp_path / "stamped.csv"
    stamped.write_text(f"query,url,position,collected_at\ndirt work software,{OURS},1,2026-11-19T23:00:00+00:00\n")
    with pytest.raises(SystemExit, match="on 2026-11-19"):
        _observe(db, stamped, "2026-11-20")
    browser = tmp_path / "browser.csv"
    browser.write_text(f"query,url,position,observed_on,provider\ndirt work software,{OURS},1,2026-11-20,browser_export\n")
    with pytest.raises(SystemExit, match="From another provider than serper: 1 of 1 queries"):
        _observe(db, browser, "2026-11-20")
    assert _outcomes(db) == 0
    nov20 = tmp_path / "nov20.csv"
    nov20.write_text(f"query,url,position,observed_on,provider\ndirt work software,{OURS},1,2026-11-20,Serper\ndirt work software,{RIVAL},5,2026-11-20,serper\n")
    _observe(db, nov20, "2026-11-20")
    # A plain file with no date or provider column is dated by --observed-on, as before.
    plain = tmp_path / "plain.csv"
    plain.write_text(f"query,url,position\ndirt work software,{OURS},2\ndirt work software,{RIVAL},1\n")
    _observe(db, plain, "2026-11-21", provider="manual_browser")
    assert _outcomes(db) == 2
    assert capsys.readouterr().out.count("Recorded observations for 1 queries") == 2


def test_f2_an_export_of_lists_all_collected_that_day_is_recorded(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    for query in ("q one", "q two"):
        append_serp(session, query, _hits([OURS, RIVAL]), country="us", language="en", collected_at=_at(20))
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    _observe(db, out, "2026-11-20")
    assert "Recorded observations for 2 queries on 2026-11-20 from serper" in capsys.readouterr().out
    assert _outcomes(db) == 2


def _noise(tmp_path, *paths):
    main(["--database", f"sqlite:///{tmp_path / 'noise.db'}", "noise", *map(str, paths)])


def test_f4_noise_files_leave_out_lists_not_recollected_or_undated(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    for query in ("q1", "q2", "q3"):  # an undated label file, as smartcrew/serp.csv is
        append_serp(session, query, _hits(_urls("a", "b", "c", "d"), provider="browser_export"), source="label_file")
    append_serp(session, "q4", _hits(_urls("a", "b", "c", "d")), country="us", language="en", collected_at=_at(1))
    append_serp(session, "q5", _hits(_urls("a", "b", "c", "d")), country="us", language="en", collected_at=_at(1))
    session.close()
    day1 = tmp_path / "day1.csv"
    main(["--database", db, "export-serp", str(day1)])
    session = open_session(db)
    # Only q4 is collected again, and its list is reversed. q5's collect failed, so it keeps its Nov 1 list.
    append_serp(session, "q4", _hits(_urls("d", "c", "b", "a")), country="us", language="en", collected_at=_at(2))
    session.close()
    day2 = tmp_path / "day2.csv"
    main(["--database", db, "export-serp", str(day2)])
    capsys.readouterr()
    _noise(tmp_path, day1, day2)
    printed = capsys.readouterr().out
    assert "Left out 4 of 5 queries that are in both files" in printed
    assert "No observation date in a file that has dates: 3. export-serp leaves it blank for a label file with no date" in printed
    assert "Same collection_id in both files, so the list was not collected again in between: 1." in printed
    assert "Queries compared: 1." in printed
    assert "Top-1 same rate: 0.000." in printed
    # The same comparison from the database agrees.
    main(["--database", db, "noise", "--from-db"])
    from_db = capsys.readouterr().out
    assert "Queries compared: 1." in from_db and "Top-1 same rate: 0.000." in from_db


def test_f4_noise_files_leave_out_other_locales_and_the_wrong_order(tmp_path, capsys):
    header = "query,url,position,observed_on,country,language,device,provider\n"

    def write(name, rows):
        path = tmp_path / name
        path.write_text(header + "".join(
            f"{query},{url},{position},{day},{country},en,{device},{provider}\n"
            for query, day, country, device, provider, urls in rows
            for position, url in enumerate(urls, start=1)
        ))
        return path

    first = write("first.csv", [
        ("q locale", "2026-11-01", "gb", "mobile", "browser_export", _urls("a", "b", "c")),
        ("q same", "2026-11-01", "us", "desktop", "serper", _urls("a", "b", "c")),
    ])
    second = write("second.csv", [
        ("q locale", "2026-11-02", "us", "desktop", "serper", _urls("c", "b", "a")),
        ("q same", "2026-11-02", "us", "desktop", "Serper", _urls("a", "c", "b")),
    ])
    _noise(tmp_path, first, second)
    printed = capsys.readouterr().out
    assert "Left out 1 of 2 queries that are in both files" in printed
    assert "Different provider, country, language or device: 1." in printed
    assert "Queries compared: 1." in printed and "Top-1 same rate: 1.000." in printed
    _noise(tmp_path, second, first)
    printed = capsys.readouterr().out
    assert "Left out 2 of 2 queries that are in both files" in printed
    assert "Dated later in the first file than in the second: 1. Pass the earlier file first." in printed
    assert "Noise ceiling was not computed." in printed and "Queries compared" not in printed


def test_f4_plain_noise_files_without_metadata_are_compared_as_before(tmp_path, capsys):
    first, second = tmp_path / "one.csv", tmp_path / "two.csv"
    first.write_text("query,url,position\n" + "".join(f"q,{url},{i}\n" for i, url in enumerate(_urls("a", "b", "c"), 1)))
    second.write_text("query,url,position\n" + "".join(f"q,{url},{i}\n" for i, url in enumerate(_urls("b", "a", "c"), 1)))
    _noise(tmp_path, first, second)
    printed = capsys.readouterr().out
    assert "Left out" not in printed
    assert "Agreement between two real snapshots" in printed and "Queries compared: 1." in printed


KP_HEADER = "Keyword,Currency,Avg. monthly searches,Three month change,YoY change,Competition,Competition (indexed value)"
KP_ROWS = ["dirt work software,USD,320,0%,0%,Low,12", "hcss alternative,USD,90,0%,0%,Low,8"]
KP_TITLE = ["Keyword Stats 2026-11-10 at 10_00_00", '"November 1, 2025 - October 31, 2026"']


def test_f15_keyword_exports_with_title_rows_are_read_by_their_query_column(tmp_path):
    ragged = tmp_path / "kp_ragged.csv"
    ragged.write_text("\n".join(KP_TITLE + [KP_HEADER] + KP_ROWS) + "\n")
    width = KP_HEADER.count(",")
    padded = tmp_path / "kp_padded.csv"  # a Sheets CSV download pads every row to the sheet width
    padded.write_text("\n".join([KP_TITLE[0] + "," * width, KP_TITLE[1] + "," * width, KP_HEADER] + KP_ROWS) + "\n")
    for path in (ragged, padded):
        assert [row.query for row in load_keyword_file(path)] == ["dirt work software", "hcss alternative"]
    ads = tmp_path / "ads.csv"
    ads.write_text(
        "Search keyword report\nOctober 1, 2026 - October 31, 2026\n"
        "Keyword,Match type,Clicks,Impr.,CTR,Avg. CPC\ndirt work software,Phrase match,12,340,3.53%,1.20\n"
    )
    assert [row.query for row in load_keyword_file(ads)] == ["dirt work software"]


def test_f15_a_metric_table_with_title_rows_and_no_query_column_is_refused(tmp_path):
    terms = tmp_path / "terms.csv"
    terms.write_text(
        "Search terms report\nOctober 1, 2026 - October 31, 2026\n"
        "Search term,Match type,Clicks,Impr.,CTR\ndirt work software,Phrase match,12,340,3.53%\n"
        "hcss alternative,Exact match,3,90,3.33%\n"
    )
    with pytest.raises(ValueError, match="search term, match type, clicks, impr., ctr, but none is a query column"):
        load_keyword_file(terms)


def test_f15_plain_lists_keep_a_later_line_that_names_a_column(tmp_path):
    plain = tmp_path / "plain.txt"
    plain.write_text("dirt work software\nkeyword\ncrm, for contractors\nhcss alternative\n")
    assert [row.query for row in load_keyword_file(plain)] == [
        "dirt work software", "keyword", "crm, for contractors", "hcss alternative"
    ]


def test_f15_track_sends_no_title_header_or_metric_row_as_a_search(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.cli as cli

    sent = []
    resolve = cli.resolve_serp

    def spy(settings, choice):
        provider = resolve(settings, choice)
        search = provider.search
        provider.search = lambda query, *a, **kw: sent.append(query) or search(query, *a, **kw)
        return provider

    monkeypatch.setattr(cli, "resolve_serp", spy)
    ragged = tmp_path / "kp_ragged.csv"
    ragged.write_text("\n".join(KP_TITLE + [KP_HEADER] + KP_ROWS) + "\n")
    cli.main(["--database", f"sqlite:///{tmp_path / 't.db'}", "track", "--mock", "--keywords", str(ragged)])
    assert sent == ["dirt work software", "hcss alternative"]


# Review follow-up (R2 to R6).


def test_r2_observe_names_a_date_it_cannot_read_and_says_how_to_fill_a_blank_one(tmp_path):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    us_date = tmp_path / "us_date.csv"  # an Excel-saved file with a Date column
    us_date.write_text(f"query,url,position,date\nq,{OURS},1,11/10/2026\nq,{RIVAL},2,11/10/2026\n")
    with pytest.raises(SystemExit) as refused:
        _observe(db, us_date, "2026-11-10", provider="manual_browser")
    message = str(refused.value)
    assert "Nothing was recorded" in message
    assert (
        "Date that cannot be read as YYYY-MM-DD: 1 of 1 queries, for example 'q' with 11/10/2026. "
        "Write each date as YYYY-MM-DD, such as 2026-11-10."
    ) in message
    assert "Observed on another day" not in message and "Record each day" not in message
    blank = tmp_path / "blank.csv"  # a hand-written template whose observed_on column was left empty
    blank.write_text(f"query,url,position,observed_on\nq,{OURS},1,\nq,{RIVAL},2,\n")
    with pytest.raises(SystemExit) as refused:
        _observe(db, blank, "2026-11-10", provider="manual_browser")
    message = str(refused.value)
    assert "No observation date in a file that has dates: 1 of 1 queries, for example 'q'." in message
    assert "In a file you wrote, fill in the dates, or remove the date column if it is empty." in message
    assert _outcomes(db) == 0


def test_r3_noise_files_from_the_same_day_are_not_told_to_swap(tmp_path, capsys):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    append_serp(session, "q", _hits(_urls("a", "b", "c")), country="us", language="en", collected_at=_at(10, 8))
    session.close()
    morning = tmp_path / "morning.csv"
    main(["--database", db, "export-serp", str(morning)])
    session = open_session(db)
    append_serp(session, "q", _hits(_urls("c", "b", "a")), country="us", language="en", collected_at=_at(10, 20))
    session.close()
    evening = tmp_path / "evening.csv"
    main(["--database", db, "export-serp", str(evening)])
    capsys.readouterr()
    for pair in ((morning, evening), (evening, morning)):
        _noise(tmp_path, *pair)
        printed = capsys.readouterr().out
        assert "Dated the same day in both files, or on days that overlap: 1. Compare files from different days." in printed
        assert "Pass the earlier file first" not in printed
        assert "Noise ceiling was not computed." in printed


def _queries(path):
    return [row.query for row in load_keyword_file(path)]


def test_r4_titled_exports_without_a_metric_name_are_read_or_refused(tmp_path):
    ads = tmp_path / "ads_keywords.csv"  # a keyword list report: a query column, no metric column
    ads.write_text(
        "Search keyword report\nOctober 1, 2026 - October 31, 2026\nKeyword,Match type,Campaign,Ad group\n"
        "dirt work software,Phrase match,Brand,Core\nhcss alternative,Exact match,Brand,Core\n"
    )
    assert _queries(ads) == ["dirt work software", "hcss alternative"]
    numeric = tmp_path / "numeric_titled.csv"  # no query column, a column of numbers below title rows
    numeric.write_text("Keyword report\nOctober 2026\nTerm,Searches\ndirt work software,320\nhcss alternative,90\n")
    with pytest.raises(ValueError, match="the columns term, searches, but none is a query column"):
        load_keyword_file(numeric)


def test_r5_r6_plain_lists_with_commas_stay_queries(tmp_path):
    cases = {
        # A cell that is a metric word, with no numbers under it (R5).
        "metric_word.txt": "dirt work software\nwebsite, traffic\nseo, tools\n",
        # A cell that names the query column, next to a column no keyword tool exports.
        "keyword_comma.txt": "dirt work software\nhcss alternative\nkeyword, research tools\nseo, tools\n",
        # A query column and a metric name, but the line below has another number of cells.
        "next_width.txt": "dirt work software\nkeyword, clicks\nhcss alternative\n",
        # The same, where only a later line breaks the width.
        "later_width.txt": "dirt work software\nkeyword, clicks\nseo, tools\nhcss alternative\n",
        # The same as the last line, with no row under it.
        "last_line.txt": "dirt work software\nkeyword, clicks\n",
    }
    for name, body in cases.items():
        path = tmp_path / name
        path.write_text(body)
        assert _queries(path) == [line for line in body.splitlines()], name
