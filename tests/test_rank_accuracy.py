from datetime import date

import pytest

from aeo.ranksim.attribution import feature_moves
from aeo.ranksim.bm25f import bm25f_scores
from aeo.ranksim.features import PageDoc
from aeo.ranksim.graph import pagerank
from aeo.ranksim.groundtruth import load_serp_file, navboost_leaks
from aeo.ranksim.lambdamart import build_examples, cross_validate
from aeo.ranksim.groundtruth import SerpLabel


def test_bm25f_weights_title_above_a_single_body_mention():
    query = "dirtwork software"
    scores = bm25f_scores(
        query,
        [
            {"title": "dirtwork software", "h1": "", "headings": "", "url": "", "anchor": "", "body": "hello"},
            {"title": "hello", "h1": "", "headings": "", "url": "", "anchor": "", "body": "dirtwork software"},
        ],
    )
    assert scores[0] > scores[1]


def test_pagerank_sends_mass_to_the_linked_page():
    ranks = pagerank([("https://a.example/", "https://b.example/")])
    assert ranks["https://b.example/"] > ranks["https://a.example/"]


def test_manual_csv_and_search_console_headers(tmp_path):
    manual = tmp_path / "manual.csv"
    manual.write_text("query,url,position,prior_position\nhcss alternative,https://example.com/a,1,4\n")
    rows = load_serp_file(manual)
    assert rows[0].position == 1
    assert rows[0].prior_position == 4
    assert navboost_leaks(rows) is False
    leaked = [SerpLabel("q", "https://example.com/a", 2, 2, "manual_csv")]
    assert navboost_leaks(leaked) is True

    gsc = tmp_path / "gsc.csv"
    gsc.write_text("Query,Page,Position\nbest dirtwork software,https://example.com/b,3.5\n")
    parsed = load_serp_file(gsc)
    assert parsed[0].source == "search_console"
    assert parsed[0].position == 3.5
    assert parsed[0].prior_position is None


def test_too_few_labels_do_not_invent_accuracy():
    page = PageDoc(url="https://example.com/a", title="dirtwork software", body="dirtwork software " * 40, word_count=80)
    labels = [SerpLabel("only one", page.url, 1, None, "manual_csv")]
    examples, uncovered, withheld = build_examples(labels, {page.url: page})
    report = cross_validate(examples, real_google=True)
    assert uncovered == 0
    assert withheld is False
    assert report.cross_validated is False
    assert report.ndcg_at_10 is None
    assert report.pairwise_accuracy is None
    assert "not computed" in report.note


def test_lambdamart_recovers_an_obvious_order_on_a_fixture():
    pages = {}
    labels = []
    for query_index in range(4):
        query = f"dirtwork software case {query_index}"
        specs = [
            (1, f"https://example.com/{query_index}/best", query, query + " " + (query + " ") * 30),
            (2, f"https://example.com/{query_index}/mid", "notes", query),
            (3, f"https://example.com/{query_index}/off", "ferns", "lobby plants and water"),
        ]
        for position, url, title, body in specs:
            pages[url] = PageDoc(url=url, title=title, h1=title, body=body, word_count=len(body.split()))
            labels.append(SerpLabel(query, url, position, None, "manual_csv"))
    examples, uncovered, withheld = build_examples(labels, pages, today=date(2026, 10, 29))
    assert uncovered == 0 and withheld is False
    report = cross_validate(examples, real_google=False)
    assert report.cross_validated is True
    assert report.real_google is False
    assert report.pairwise_accuracy is not None and report.pairwise_accuracy >= 0.9
    assert report.ndcg_at_10 is not None and report.ndcg_at_10 >= 0.9
    assert report.top3_hit_rate == 1


def test_feature_moves_name_the_title_change():
    before = PageDoc(url="https://www.smartcrewhq.com/notes", title="Hello", body="Hello there.", word_count=2)
    after = PageDoc(
        url="https://www.smartcrewhq.com/notes",
        title="Best dirtwork software",
        h1="Best dirtwork software",
        body="Best dirtwork software for excavation crews and haul tickets. " * 20,
        word_count=200,
        table_count=1,
    )
    moves = feature_moves("best dirtwork software", "commercial", before, after, today=date(2026, 10, 29))
    assert moves
    assert any("title match" in line for line in moves)
    blob = " ".join(moves)
    assert "\u2014" not in blob and "\u2013" not in blob
