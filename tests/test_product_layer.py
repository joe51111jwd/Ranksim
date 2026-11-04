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


def test_cheap_model_defaults_and_disk_cache(tmp_path, monkeypatch):
    from aeo.ranksim.llm import CachingLlm, LlmClient, LlmReply, estimate_cost, resolve_llm

    monkeypatch.chdir(tmp_path)  # resolve_llm's default cache folder is relative: keep it out of the caller's folder

    openai = resolve_llm(Settings(openai_api_key="sk-test", anthropic_api_key=None, openrouter_api_key=None, ranksim_llm_model=None))
    assert openai.model == "gpt-4o-mini"
    assert openai.name == "openai"
    router = resolve_llm(Settings(openrouter_api_key="sk-or-router", openai_api_key=None, anthropic_api_key=None, ranksim_llm_model=None))
    assert router.name == "openrouter"
    assert router.model == "openai/gpt-4o-mini"
    assert router.inner.base_url == "https://openrouter.ai/api/v1"
    routed = resolve_llm(Settings(openai_api_key="sk-or-from-openai", openrouter_api_key=None, anthropic_api_key=None, ranksim_llm_model=None))
    assert routed.name == "openrouter"
    anthropic = resolve_llm(Settings(openai_api_key=None, anthropic_api_key="sk-ant", openrouter_api_key=None, ranksim_llm_model=None))
    assert anthropic.model == "claude-3-5-haiku-20241022"
    override = resolve_llm(Settings(openai_api_key="sk-test", ranksim_llm_model="gpt-4o-mini"))
    assert override.model == "gpt-4o-mini"
    assert estimate_cost("not-a-priced-model", 10, 10) is None

    class Fake(LlmClient):
        name = "openai"
        available = True
        model = "gpt-4o-mini"
        calls = 0

        def complete(self, system: str, user: str) -> LlmReply:
            self.calls += 1
            return LlmReply(text="ok", provider="openai", stub=False, model=self.model, input_tokens=1000, output_tokens=100)

    fake = Fake()
    cached = CachingLlm(fake, tmp_path / "cache", tmp_path / "cost.jsonl", "test-run")
    first = cached.complete("sys", "user")
    second = cached.complete("sys", "user")
    assert fake.calls == 1
    assert first.cache_hit is False and second.cache_hit is True
    assert second.estimated_usd == 0.0
    assert first.estimated_usd == pytest.approx((1000 * 0.15 + 100 * 0.60) / 1_000_000)
    log = (tmp_path / "cost.jsonl").read_text()
    assert "test-run" in log and "cache_hit" in log


def test_space_does_not_invent_a_rank_for_missing_html():
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.lambdamart import RankExample
    from aeo.ranksim.space import build_space

    examples = []
    for index in range(2):
        query = f"dirtwork software {index}"
        for position, url, title in (
            (1, f"https://rival.example/{index}", query),
            (2, f"https://www.smartcrewhq.com/{index}", "notes"),
        ):
            page = PageDoc(url=url, title=title, h1=title, body=(title + " ") * 40, word_count=80, is_ours="smartcrewhq" in url)
            examples.append(RankExample(query, url, position, {"bm25_title": 1.0 if position == 1 else 0.0}))
    fit = loo_fit(examples, feature_names=["bm25_title"], keep_models=True)
    labels = [
        SerpLabel("dirtwork software 0", "https://rival.example/0", 1, None, "manual_csv"),
        SerpLabel("dirtwork software 0", "https://www.smartcrewhq.com/0", 2, None, "manual_csv"),
        SerpLabel("dirtwork software 0", "https://missing.example/hidden", 3, None, "manual_csv"),
        SerpLabel("dirtwork software 1", "https://rival.example/1", 1, None, "manual_csv"),
        SerpLabel("dirtwork software 1", "https://www.smartcrewhq.com/1", 2, None, "manual_csv"),
    ]
    pages = {}
    for row in examples:
        pages[row.url] = PageDoc(url=row.url, title=row.url, body="x " * 40, word_count=40, is_ours="smartcrewhq" in row.url)
    report = build_space(labels, pages, fit.models, ["rival.example", "missing.example"])
    board = next(item for item in report.boards if item.query == "dirtwork software 0")
    missing = next(player for player in board.players if player.domain == "missing.example")
    assert missing.real_position == 3
    assert missing.predicted_rank is None
    share = next(row for row in report.shares if row.domain == "missing.example")
    assert share.real_top10 == 1
    assert share.predicted_queries == 0
    text = __import__("aeo.ranksim.space", fromlist=["render_space_text"]).render_space_text(report)
    assert "PREDICTED n/a" in text
    assert "REAL 3" in text


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
