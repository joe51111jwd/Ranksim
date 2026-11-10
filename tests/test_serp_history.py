"""SERP history: collections are appended with what was asked for, the latest feeds the model,
and collected lists reach evaluate, noise and the ledger through export-serp. Synthetic data."""

from datetime import datetime, timezone

from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.keywords import load_keyword_file
from aeo.ranksim.serp import SerperProvider, SerpHit
from aeo.ranksim.store import RsSerp, append_serp, open_session, serp_collections, serp_for_query

QUERY = "dirt work software"


def _hits(urls, provider="serper", synthetic=False):
    return [SerpHit(position=index, url=url, title="", snippet="", provider=provider, synthetic=synthetic, is_google=not synthetic)
            for index, url in enumerate(urls, start=1)]


def _day(day):
    return datetime(2026, 11, day, 12, tzinfo=timezone.utc)


def test_two_collections_are_kept_and_the_latest_is_served(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 's.db'}")
    first = append_serp(session, QUERY, _hits(["https://a.example/", "https://b.example/"]), country="us", language="en", collected_at=_day(6))
    second = append_serp(session, QUERY, _hits(["https://b.example/", "https://a.example/"]), country="us", language="en", collected_at=_day(9))
    assert first and second and first != second
    assert [row.url for row in serp_for_query(session, QUERY)] == ["https://b.example/", "https://a.example/"]
    collections = serp_collections(session, QUERY)
    assert [c.collected_at.day for c in collections] == [6, 9] and collections[0].country == "us"
    # Importing the same list again adds nothing.
    assert append_serp(session, QUERY, _hits(["https://b.example/", "https://a.example/"]), collected_at=_day(9)) is None
    assert len(serp_collections(session, QUERY)) == 2
    session.close()


def test_rows_from_before_collections_are_still_served(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 's.db'}")
    session.add(RsSerp(query=QUERY, position=1, url="https://old.example/", title="", snippet="", provider="serper", synthetic=False, is_google=True))
    session.commit()
    assert [row.url for row in serp_for_query(session, QUERY)] == ["https://old.example/"]
    session.close()


def test_providers_record_only_what_they_ask_for():
    assert (SerperProvider.country, SerperProvider.language) == ("us", "en")
    from aeo.ranksim.serp import BraveFallbackProvider, MockSerpProvider, SerpApiProvider

    assert SerpApiProvider.country is None and BraveFallbackProvider.country is None and MockSerpProvider.country is None


def test_export_feeds_evaluate_and_noise_from_the_database(tmp_path, capsys):
    from aeo.ranksim.cli import main

    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    for day, order in ((6, ["a", "b", "c"]), (9, ["b", "a", "c"])):
        append_serp(session, QUERY, _hits([f"https://{name}.example/" for name in order]), country="us", language="en", collected_at=_day(day))
    append_serp(session, "mock query", _hits(["https://m.example/"], provider="mock", synthetic=True), collected_at=_day(9))
    session.close()
    out = tmp_path / "latest.csv"
    main(["--database", db, "export-serp", str(out)])
    labels = load_serp_file(out)
    assert [(row.url, row.position, row.observed_on, row.country) for row in labels] == [
        ("https://b.example/", 1, "2026-11-09", "us"), ("https://a.example/", 2, "2026-11-09", "us"), ("https://c.example/", 3, "2026-11-09", "us")
    ]
    every = tmp_path / "all.csv"
    main(["--database", db, "export-serp", str(every), "--all", "--include-synthetic"])
    assert len(every.read_text().splitlines()) == 1 + 3 + 3 + 1
    capsys.readouterr()
    main(["--database", db, "noise", "--from-db"])
    report = capsys.readouterr().out
    assert "one pair of collections for each of 1 queries" in report and "Pairwise agreement" in report


def test_keyword_files_in_csv_or_plain_lines(tmp_path):
    csv_file = tmp_path / "k.csv"
    csv_file.write_text("query,intent\nHCSS  Alternative,\nbest paving software,informational\n")
    rows = load_keyword_file(csv_file)
    assert [(row.query, row.intent) for row in rows] == [("hcss alternative", "comparison"), ("best paving software", "informational")]
    plain = tmp_path / "k.txt"
    plain.write_text("# comment\ndirt work software\ndirt work software\n")
    assert [row.query for row in load_keyword_file(plain)] == ["dirt work software"]


def test_index_serp_records_the_label_files_date_and_locale(tmp_path, monkeypatch):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    db = f"sqlite:///{tmp_path / 's.db'}"
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position,observed_on,country,language\nq,https://a.example/,1,2026-11-06,us,en\nq,https://b.example/,2,2026-11-06,us,en\n")
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_a: [])
    index_serp.index_serp_csv(serp, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")
    index_serp.index_serp_csv(serp, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")
    session = open_session(db)
    collections = serp_collections(session, "q")
    session.close()
    assert len(collections) == 1  # the second import of the same file added nothing
    assert (collections[0].source, collections[0].collected_at.date().isoformat(), collections[0].country) == ("label_file", "2026-11-06", "us")
