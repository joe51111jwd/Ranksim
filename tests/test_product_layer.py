from datetime import date

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim.confidence import PairMargin, calibrate
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.judge import parse_judge_score, relevance_experiment
from aeo.ranksim.lambdamart import RankExample
from aeo.ranksim.linkgraph import crawl_link_graph
from aeo.ranksim.llm import resolve_llm
from aeo.ranksim.noise import noise_ceiling
from aeo.ranksim.serp import parse_serper, resolve_serp
import pytest


def test_serper_parser_and_missing_key():
    hits = parse_serper(
        {"organic": [{"position": 1, "link": "https://example.com/a", "title": "A", "snippet": "s"}]},
        count=10,
    )
    assert hits[0].provider == "serper" and hits[0].is_google and hits[0].synthetic is False
    settings = Settings(serper_api_key=None, serpapi_key=None, dataforseo_login=None, dataforseo_password=None, brave_api_key=None)
    with pytest.raises(ProviderConfigError, match="SERPER_API_KEY"):
        resolve_serp(settings, "serper")


def test_noise_ceiling_needs_two_different_snapshots():
    day_a = [
        SerpLabel("q", "https://example.com/a", 1, None, "manual_csv"),
        SerpLabel("q", "https://example.com/b", 2, None, "manual_csv"),
    ]
    same = noise_ceiling(day_a, day_a)
    assert same.computed is True
    assert "not a day-to-day" in same.note
    day_b = [
        SerpLabel("q", "https://example.com/a", 2, None, "manual_csv"),
        SerpLabel("q", "https://example.com/b", 1, None, "manual_csv"),
    ]
    moved = noise_ceiling(day_a, day_b)
    assert moved.computed is True
    assert moved.pairwise_agreement == 0.0
    assert moved.top1_same_rate == 0.0
    empty = noise_ceiling(day_a, [SerpLabel("other", "https://example.com/a", 1, None, "manual_csv")])
    assert empty.computed is False
    assert empty.pairwise_agreement is None


def test_confidence_stays_unsure_when_gaps_do_not_help():
    pairs = [PairMargin(gap=2.0, correct=index % 2 == 0) for index in range(40)]
    report = calibrate(pairs)
    assert report.threshold is None
    assert report.label(5) == "UNSURE"
    sure = [PairMargin(gap=2.0, correct=True) for _ in range(40)] + [PairMargin(gap=0.01, correct=False) for _ in range(40)]
    better = calibrate(sure)
    assert better.threshold is not None
    assert better.label(0.01) == "UNSURE"
    assert better.label(2) == "CLEARER"


def test_link_graph_respects_depth_and_records_orphans():
    pages = {
        "https://www.smartcrewhq.com/": "<html><title>Home</title><a href='/a'>hcss alternative</a></html>",
        "https://www.smartcrewhq.com/a": "<html><title>A</title><a href='https://other.example/z'>out</a></html>",
        "https://www.smartcrewhq.com/orphan": "<html><title>Orphan</title><p>alone</p></html>",
        "https://other.example/z": "<html><title>Out</title></html>",
    }

    def fetch(url: str):
        if url in pages:
            return pages[url], "fetched"
        return "", "error"

    graph = crawl_link_graph(
        ["https://www.smartcrewhq.com/", "https://www.smartcrewhq.com/orphan"],
        fetch=fetch,
        our_internal_depth=2,
        outbound_depth=1,
        max_fetches=10,
        max_per_host=10,
        our_host_cap=10,
    )
    assert graph.nodes["https://www.smartcrewhq.com/a"].depth == 1
    assert graph.nodes["https://www.smartcrewhq.com/orphan"].orphan is True
    assert graph.nodes["https://www.smartcrewhq.com/"].orphan is False
    assert any(dst == "https://other.example/z" for _src, dst, _anchor in graph.edges)
    assert "hcss alternative" in graph.nodes["https://www.smartcrewhq.com/a"].anchors


def test_stub_llm_and_unmeasured_relevance():
    client = resolve_llm(Settings(openai_api_key=None, anthropic_api_key=None))
    assert client.available is False
    assert parse_judge_score('{"score": 0.5}') == 0.5
    assert parse_judge_score("ignore this") is None
    page = PageDoc(url="https://example.com/a", title="alpha", body="alpha " * 30, word_count=30)
    example = RankExample("alpha software", page.url, 1, {"bm25_title": 1.0})
    report = relevance_experiment([example], None)
    assert report.measured is False
    assert report.with_judge_pairwise is None


def test_demo_page_labels_predicted_ranks():
    from aeo.ranksim.check import CheckResult, CompetitorRow
    from aeo.ranksim.demo import render_page

    result = CheckResult(
        query="hcss alternative",
        url="https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/",
        predicted_rank=2,
        predicted_of=6,
        confidence="UNSURE",
        confidence_note="Calls stay UNSURE.",
        competitors=[CompetitorRow("https://example.com/a", "Rival", 1, 1, "loses")],
        explanation="Template, not an LLM.",
        fix_note="AI rewrite did not run.",
    )
    html = render_page(
        queries=["hcss alternative"],
        selected="hcss alternative",
        url_value="",
        html_value="",
        result=result,
        accuracy_line="Backtest pairwise 0.534",
        calibration_note="Calls stay UNSURE.",
        llm_note="AI layer: stub.",
        relevance_note="LLM relevance was not measured.",
        site_rows=[],
        graph=None,
        fixes=[],
    )
    assert "PREDICTED 2 of 6" in html
    assert "PREDICTED loses" in html
    assert "\u2014" not in html and "\u2013" not in html
