"""B17 (identity): what-if, the space view and coverage treat one page under any spelling as one
page, and leave client-rendered shells out the way training and check_draft do. The pr8 bundles'
new pages join their target queries. Synthetic pages, except the bundled manifests and label file."""

import csv
from datetime import date
from pathlib import Path

import pytest

from aeo.ranksim.benchmark import coverage_rows
from aeo.ranksim.confidence import loo_fit
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.intents import normalize_query
from aeo.ranksim.lambdamart import _lookup_page, build_examples
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.space import build_space, render_space_text
from aeo.ranksim.store import open_session
from aeo.ranksim.urlkey import match_key
from aeo.ranksim.whatif import (
    PageMove,
    QueryWhatIf,
    WhatIfReport,
    bundle_targets,
    overrides_from_html,
    render_three_way,
    render_whatif,
    score_whatif,
)

REPO = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 10)
STORED = "https://www.smartcrewhq.com/compare/x/"
QUERY = "paving case 0"


def _html(title: str, words: int) -> str:
    return f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1><p>{(title + ' crews ') * words}</p></main></body></html>"


def _corpus(label_url: str = STORED, extra_label: str | None = None):
    """Four queries of four labeled pages; the SmartCrew page is position 3 on the first query."""
    labels, pages = [], {}
    for index in range(4):
        query = f"paving case {index}"
        for position in range(1, 5):
            url = STORED if (index == 0 and position == 3) else f"https://rival{position}.example/q{index}"
            labels.append(SerpLabel(query, label_url if url == STORED else url, float(position), None, "manual_csv"))
            body = (query + " paving crews schedule ") * (40 + 10 * (5 - position))
            pages[url] = PageDoc(url=url, title=query, h1=query, body=body, word_count=len(body.split()))
    if extra_label:
        labels.append(SerpLabel(QUERY, extra_label, 6.0, None, "manual_csv"))
    return labels, pages


def _row(report: WhatIfReport, query: str = QUERY) -> QueryWhatIf:
    return next(row for row in report.queries if row.query == query)


# C14 and C7 (what-if half): a bundle page replaces the stored copy under any spelling.


@pytest.mark.parametrize(
    "label_url, bundle_url, targeted",
    [
        (STORED, "https://smartcrewhq.com/compare/x", True),  # C14: manifest without www, crawl with www
        ("https://smartcrewhq.com/compare/x", "https://smartcrewhq.com/compare/x", False),  # C7: label and draft without www
        ("https://smartcrewhq.com/compare/x", "https://smartcrewhq.com/compare/x", True),
        (STORED, "http://smartcrewhq.com/compare/x/?utm_source=pr", True),
    ],
)
def test_a_bundle_page_under_another_spelling_replaces_the_stored_copy(label_url, bundle_url, targeted):
    labels, pages = _corpus(label_url)
    overrides = overrides_from_html({bundle_url: _html(QUERY, 300)}, pages)
    targets = {QUERY: {match_key(bundle_url)}} if targeted else None
    row = _row(score_whatif(labels, pages, overrides, today=TODAY, targets=targets))
    assert row.before_n == row.swap_n == row.after_n == 4  # the page is not in the set twice
    assert [page.role for page in row.pages] == ["labeled, HTML replaced"]
    assert row.pages[0].words_after > row.pages[0].words_before


def test_one_page_labeled_under_two_spellings_is_scored_once():
    # As in check_draft: a second label for a page already in the set is not a second competitor.
    labels, pages = _corpus(extra_label="https://smartcrewhq.com/compare/x?utm_source=x")
    row = _row(score_whatif(labels, pages, [], today=TODAY))
    assert row.before_n == row.after_n == 4
    assert sum(1 for _rank, url, _ours in row.order if match_key(url) == match_key(STORED)) == 1


# X1: client-rendered shells are not competitors in what-if or the space view.


SHELL_HTML = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'


def _shell_corpus():
    labels, pages = [], {}
    for index in range(4):
        query = f"paving case {index}"
        for position in range(1, 6):
            url = f"https://site{position}.example/q{index}"
            if position == 1:
                pages[url] = PageDoc(url=url, title="App", body="", word_count=0, js_shell=True)
            else:
                body = (query + " paving crews schedule ") * (40 + 10 * (6 - position))
                pages[url] = PageDoc(url=url, title=query, h1=query, body=body, word_count=len(body.split()))
            labels.append(SerpLabel(query, url, float(position), None, "manual_csv"))
    ours = "https://www.smartcrewhq.com/app"  # our own shell, which the bundle replaces
    pages[ours] = PageDoc(url=ours, title="App", body="", word_count=0, js_shell=True)
    labels.append(SerpLabel(QUERY, ours, 6.0, None, "manual_csv"))
    return labels, pages, ours


def test_what_if_leaves_competitor_shells_out_and_says_so():
    labels, pages, ours = _shell_corpus()
    examples, uncovered, _withheld = build_examples(labels, pages, {}, today=TODAY)
    assert uncovered == 5  # training leaves every shell out
    overrides = overrides_from_html({ours: _html(QUERY, 300)}, pages)
    report = score_whatif(labels, pages, overrides, today=TODAY)
    for row in report.queries:
        shell = f"https://site1.example/q{row.query.rsplit(' ', 1)[1]}"
        assert shell not in [url for _rank, url, _ours in row.order]
        assert row.shells == [shell]
    first = _row(report)
    assert first.before_n == 5  # four stored competitors plus our page, whose shell the bundle replaces
    assert [page.role for page in first.pages] == ["labeled, HTML replaced"]
    assert all(row.before_n == 4 for row in report.queries if row.query != QUERY)
    text = render_whatif(report)
    assert "Labeled rows left out as client-rendered shells: 4." in text
    assert "https://site1.example/q0 | a client-rendered shell, left out of scoring." in text
    assert "\u2014" not in text and "\u2013" not in text


def test_the_space_view_leaves_shells_out_of_predicted_ranks_and_counts_them():
    labels, pages, _ours = _shell_corpus()
    labels = [label for label in labels if "smartcrewhq" not in label.url]
    examples, _uncovered, _withheld = build_examples(labels, pages, {}, today=TODAY)
    models = loo_fit(examples, keep_models=True).models
    report = build_space(labels, pages, models, ["site1.example"], our_domain="site2.example", today=TODAY)
    assert [board.scored_pages for board in report.boards] == [4, 4, 4, 4]
    assert [board.shells for board in report.boards] == [1, 1, 1, 1]
    share = next(row for row in report.shares if row.domain == "site1.example")
    assert share.real_top3 == 4 and share.predicted_queries == 0  # REAL kept, no PREDICTED rank for a shell
    text = render_space_text(report)
    assert "(4 scored pages, 1 client-rendered shell left out)" in text
    assert "Labeled pages left out of the PREDICTED ranks as client-rendered shells: 4." in text


# C1: the bundled pr8 manifests' pages join their target queries, and an unscored target says so.


@pytest.mark.parametrize("folder", ["pr8", "pr8_v2", "pr8_v3"])
def test_every_bundled_pr8_page_targets_a_labeled_query(folder):
    labeled = {normalize_query(row.query) for row in load_serp_file(REPO / "smartcrew" / "serp.csv")}
    manifest = REPO / "smartcrew" / folder / "manifest.csv"
    targets = bundle_targets([manifest])  # the real keywords.csv
    with manifest.open(newline="", encoding="utf-8") as handle:
        urls = [row["url"] for row in csv.DictReader(handle)]
    assert any("all-in-one-heavy-civil-software" in url for url in urls)
    missing = [url for url in urls if not any(match_key(url) in targets.get(query, set()) for query in labeled)]
    assert missing == []


def _query(name: str, url: str | None, rank: int | None, total: int) -> QueryWhatIf:
    pages = []
    if url and rank:
        pages = [PageMove(url, "labeled", rank, rank, rank, "", "", "", 200, 200, 0, 0)]
    return QueryWhatIf(name, total, total, total, None, None, False, rank, url, rank, rank, url, 200, rank, url, pages, [], [])


def test_a_target_query_with_no_score_is_not_rendered_as_checked():
    query = "all in one heavy civil software"  # a PAGE_TARGETS query for the new heavy civil page
    reports = [WhatIfReport("n", 0.5, 0.7, 0.1, 0.5, 1, 1, 1, 0, [_query(query, None, None, 8)], []) for _ in range(3)]
    text = render_three_way(*reports)
    lines = [line for line in text.splitlines() if line.startswith("/all-in-one-heavy-civil-software\t")]
    assert lines == ["/all-in-one-heavy-civil-software\tall in one heavy civil software\ttarget, not scored\tn/a\tn/a\tn/a\t"]
    assert "Not scored: " in text and "The ship check did not cover them." in text
    assert "\u2014" not in text and "\u2013" not in text


def test_a_bundle_page_that_joins_no_query_is_named():
    labels, pages = _corpus()
    orphan = "https://www.smartcrewhq.com/orphan-page"
    overrides = overrides_from_html({orphan: _html("orphan", 300), STORED: _html(QUERY, 300)}, pages)
    report = score_whatif(labels, pages, overrides, today=TODAY)
    assert f"Bundle page {orphan} joined no labeled query, so it was not scored." in render_whatif(report)
    assert report.unjoined_urls == [orphan]
    three = render_three_way(report, report, report)
    assert f"v2 bundle page {orphan} joined no labeled query" in three


# C10: coverage describes the stored row that training uses.


def _good(words: int = 300) -> str:
    body = " ".join(f"word{index}" for index in range(words))
    return f"<html><head><title>T</title></head><body><main><p>{body}</p></main></body></html>"


@pytest.mark.parametrize(
    "stored, label_url, expected",
    [
        # The live page under one spelling, a failed attempt under another (claim A and B).
        (
            [FetchResult("https://www.example.com/pricing", _good(), "fetched"), FetchResult("https://example.com/pricing", "", "error", error="HTTP 403")],
            "https://www.example.com/pricing/",
            ("covered", "https://www.example.com/pricing"),
        ),
        (
            [FetchResult("https://www.example.com/pricing", _good(), "fetched"), FetchResult("http://example.com/pricing", "", "error", error="timeout")],
            "https://example.com/pricing/",
            ("covered", "https://www.example.com/pricing"),
        ),
        # Training picks a draft by normalize_url: coverage must not call the label covered (claim C).
        (
            [
                FetchResult("https://www.example.com/pricing", _good(), "fetched", source="draft_bundle"),
                FetchResult("https://example.com/pricing", _good(), "fetched"),
            ],
            "https://www.example.com/pricing/",
            ("draft_only", "https://www.example.com/pricing"),
        ),
    ],
)
def test_coverage_describes_the_row_training_uses(tmp_path, stored, label_url, expected):
    session = open_session(f"sqlite:///{tmp_path / 'c.db'}")
    try:
        for result in stored:
            store_fetched_page(session, result)
        others = [f"https://other{index}.example/p" for index in range(2)]
        for url in others:
            store_fetched_page(session, FetchResult(url, _good(), "fetched"))
        labels = [SerpLabel("q", label_url, 1.0, None, "manual_csv")]
        labels += [SerpLabel("q", url, float(index + 2), None, "manual_csv") for index, url in enumerate(others)]
        rows = coverage_rows(labels, session)
        pages = load_corpus_docs(session)
    finally:
        session.close()
    examples, uncovered, _withheld = build_examples(labels, pages, {}, today=TODAY)
    assert (rows[0]["reason"], rows[0]["stored_url"]) == expected
    assert rows[0]["stored_url"] == _lookup_page(pages, label_url).url
    assert sum(1 for row in rows if row["reason"] in ("covered", "thin")) == len(examples)
    assert sum(1 for row in rows if row["reason"] not in ("covered", "thin")) == uncovered
