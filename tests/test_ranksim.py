import csv
from datetime import date

import pytest

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim.cli import main
from aeo.ranksim.features import PageDoc
from aeo.ranksim.keywords import build_keywords, load_sitemap_urls, page_gaps, write_keywords_csv
from aeo.ranksim.loop import FetchResult, collect_corpus, calibrate, run_test
from aeo.ranksim.metrics import ndcg_at_k, spearman
from aeo.ranksim.report import render_report
from aeo.ranksim.score import HAND_WEIGHTS, fit_weights, score_pages
from aeo.ranksim.serp import MockSerpProvider, parse_dataforseo, parse_serpapi, resolve_serp
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.store import open_session


REQUIRED = [
    "hcss alternative",
    "what is the best all-in-one dirtworks software",
    "best all-in-one heavy construction software",
    "best heavy civil construction software",
]


def test_keyword_set_is_150_and_gaps_are_outside_the_sitemap():
    sitemap = load_sitemap_urls()
    rows = build_keywords(sitemap)
    assert len(rows) == 150
    assert len({row.query for row in rows}) == 150
    for phrase in REQUIRED:
        assert phrase in {row.query for row in rows}
    for row in rows:
        assert "\u2014" not in row.query + row.notes
        assert "\u2013" not in row.query + row.notes
        if row.page_exists:
            assert row.suggested_target_page in sitemap
            assert row.cluster != "gap"
        else:
            assert row.suggested_target_page not in sitemap
            assert row.notes
    gaps = page_gaps(rows)
    assert len(gaps) == 37
    assert any(row.query == "hcss heavybid alternative" for row in gaps)
    assert any(row.query == "smartcrew pricing" and row.page_exists for row in rows)


def test_spearman_and_ndcg_known_values():
    assert spearman([3, 2, 1], [1, 2, 3]) == pytest.approx(1)
    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(-1)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    perfect = ndcg_at_k(["a", "b", "c"], {"a": 3, "b": 2, "c": 1}, k=10)
    worst = ndcg_at_k(["c", "b", "a"], {"a": 3, "b": 2, "c": 1}, k=10)
    assert perfect == pytest.approx(1)
    assert worst < perfect


def test_title_match_outranks_unrelated_page_with_hand_weights():
    pages = [
        PageDoc(url="https://example.com/tax", title="Quarterly tax filing", body="Bakery invoices and payroll forms."),
        PageDoc(
            url="https://www.smartcrewhq.com/dirtwork-software",
            title="Best dirtwork software",
            h1="Best dirtwork software",
            body="Best dirtwork software for excavation crews, haul tickets, and job cost.",
            word_count=180,
            is_ours=True,
        ),
    ]
    scored = score_pages("best dirtwork software", "commercial", pages, HAND_WEIGHTS, date(2026, 10, 29))
    assert scored[0].url.endswith("/dirtwork-software")
    assert scored[0].predicted_position == 1


def test_calibration_recovers_a_labeled_order_and_is_not_called_google():
    query = "best dirtwork software"
    pages = [
        PageDoc(url="https://rival.example/best", title="Best dirtwork software", h1="Best dirtwork software", body="dirtwork software " * 40, word_count=200),
        PageDoc(url="https://rival.example/mid", title="Dirtwork notes", body="A short note about dirtwork.", word_count=40),
        PageDoc(url="https://rival.example/off", title="Office plants", body="Ferns in the lobby.", word_count=20),
    ]
    report = fit_weights([(query, "commercial", pages, [1, 2, 3])], today=date(2026, 10, 29))
    assert report.n_pairs == 3
    assert report.spearman == pytest.approx(1)
    assert report.ndcg_at_10 == pytest.approx(1)
    assert report.label == "calibrated_on_stored_serp"


def test_serpapi_and_dataforseo_parsers_keep_only_organic_links():
    serp = parse_serpapi(
        {"organic_results": [{"position": 2, "link": "https://example.com/b", "title": "B", "snippet": "s"}, {"position": 1, "link": "https://example.com/a", "title": "A"}]},
        count=10,
    )
    assert [hit.url for hit in serp] == ["https://example.com/a", "https://example.com/b"]
    assert all(hit.is_google and not hit.synthetic for hit in serp)
    parsed = parse_dataforseo(
        {"tasks": [{"result": [{"items": [{"type": "organic", "rank_group": 1, "url": "https://example.com/a", "title": "A", "description": "d"}, {"type": "people_also_ask", "url": "https://example.com/skip"}]}]}]},
        count=10,
    )
    assert len(parsed) == 1
    assert parsed[0].provider == "dataforseo"


def test_backlink_rank_parser_scales_and_ignores_empty_payloads():
    from aeo.ranksim.authority import parse_backlink_rank

    assert parse_backlink_rank({"tasks": [{"result": [{"rank": 250}]}]}) == pytest.approx(0.25)
    assert parse_backlink_rank({"tasks": []}) is None


def test_auto_serp_refuses_to_invent_results_without_keys():
    settings = Settings(
        brave_api_key=None, serpapi_key=None, serper_api_key=None, dataforseo_login=None, dataforseo_password=None
    )
    with pytest.raises(ProviderConfigError, match="Refusing to invent"):
        resolve_serp(settings, "auto")
    mock = resolve_serp(settings, "mock")
    assert isinstance(mock, MockSerpProvider)
    hit = mock.search("hcss alternative", count=2)[0]
    assert hit.synthetic and hit.is_google is False


def test_fast_loop_moves_predicted_position_when_the_page_gets_stronger(tmp_path):
    url = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(url)
    query = "best dirtwork software"
    our = "https://www.smartcrewhq.com/notes"
    hits = [
        SerpHit(1, "https://rival.example/plants", "Office plants", "ferns", "fixture", True, False),
        SerpHit(2, our, "Hello", "hello", "fixture", True, False),
        SerpHit(3, "https://rival.example/tax", "Tax notes", "tax", "fixture", True, False),
    ]

    class Fixture:
        name = "fixture"
        synthetic = True
        is_google = False

        def search(self, _query: str, count: int = 10):
            return hits[:count]

    def fetcher(page_url: str) -> FetchResult:
        html = {
            "https://rival.example/plants": "<html><title>Office plants</title><body><h1>Ferns</h1><p>Lobby ferns.</p></body></html>",
            our: "<html><title>Hello</title><body><p>Hello there.</p></body></html>",
            # One rival matches a query word, so the weak page is strictly behind it before the edit.
            # (Without it, all three pages tie and float rounding, which differs by Python version, set the order.)
            "https://rival.example/tax": "<html><title>Tax notes</title><body><p>Quarterly tax filing software.</p></body></html>",
        }[page_url]
        return FetchResult(url=page_url, html=html, status="fetched", fetch_ms=100)

    from aeo.ranksim.keywords import Keyword

    keywords = [Keyword(query, "commercial", "best", our, True, "")]
    result = collect_corpus(session, keywords, Fixture(), fetcher, depth=3, max_pages=1000)
    assert result.synthetic and result.is_google is False
    strong = """<html><head><title>Best dirtwork software</title></head>
    <body><h1>Best dirtwork software</h1>
    <table><tr><td>haul tickets</td></tr></table>
    <h2>FAQ</h2>
    <p>Best dirtwork software for excavation crews covers haul tickets, job cost, and equipment hours.</p>
    </body></html>"""
    from aeo.ranksim.loop import page_from_html

    doc = page_from_html(our, strong, is_ours=True)
    deltas = run_test(session, keywords, doc, replaces=our, today=date(2026, 10, 29), source_label="strong.html")
    session.close()
    assert len(deltas) == 1
    row = deltas[0]
    assert row.before_predicted_position is not None
    assert row.after_predicted_position is not None
    assert row.after_predicted_position < row.before_predicted_position
    assert row.delta > 0
    assert "Not a live Google rank" in row.note
    _report, label, _provider = calibrate(session, keywords, today=date(2026, 10, 29))
    assert label == "mock_calibration_not_google"


def test_report_and_cli_do_not_invent_google_accuracy(tmp_path, capsys):
    html = render_report(None)
    assert "Google calibration has not been run" in html
    assert "Spearman" not in html or "no Spearman" in html
    assert "\u2014" not in html and "\u2013" not in html
    assert "hcss heavybid alternative" in html

    database = f"sqlite:///{tmp_path / 'empty.db'}"
    out = tmp_path / "report.html"
    main(["--database", database, "report", "--out", str(out)])
    written = out.read_text()
    assert "Google calibration has not been run" in written
    assert "\u2014" not in written and "\u2013" not in written

    csv_path = write_keywords_csv(tmp_path / "keywords.csv")
    with csv_path.open() as handle:
        found = list(csv.DictReader(handle))
    assert len(found) == 150
    assert found[0]["page_exists"] in {"yes", "no"}
