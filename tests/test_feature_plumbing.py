"""Features named in FEATURE_NAMES must reach the model with the values the store holds."""

from datetime import date

from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.store import RsPage, open_session


def _page(url, **extra):
    return PageDoc(url=url, title="dirt work software", body="dirt work software " * 80, word_count=240, **extra)


def test_build_examples_keeps_link_graph_fields():
    pages = {
        "https://a.example/": _page("https://a.example/", orphan=True, link_depth=2, crawl_inlinks=0),
        "https://b.example/": _page("https://b.example/", orphan=False, link_depth=1, crawl_inlinks=7),
    }
    labels = [
        SerpLabel("dirt work software", "https://a.example/", 1, None, "manual_csv"),
        SerpLabel("dirt work software", "https://b.example/", 2, None, "manual_csv"),
    ]
    examples, uncovered, _ = build_examples(labels, pages, today=date(2026, 10, 29))
    assert uncovered == 0
    by_url = {row.url: row.features for row in examples}
    assert by_url["https://a.example/"]["orphan"] == 1.0
    assert by_url["https://b.example/"]["orphan"] == 0.0
    assert by_url["https://a.example/"]["link_depth"] == 2.0
    assert by_url["https://b.example/"]["crawl_inlinks"] == 7.0


def test_authority_is_missing_unless_a_provider_stored_it(tmp_path):
    db = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(db)
    html = '<html><head><title>T</title></head><body><a href="https://b.example/">b</a> {}</body></html>'
    store_fetched_page(session, FetchResult(url="https://a.example/", html=html.format("a " * 200), status="fetched"))
    store_fetched_page(session, FetchResult(url="https://b.example/", html=html.format("b " * 200), status="fetched"))
    row = session.get(RsPage, "https://b.example/")
    row.authority = 0.9
    session.commit()
    pages = load_corpus_docs(session)
    session.close()
    assert pages["https://b.example/"].authority == 0.9
    assert pages["https://a.example/"].authority is None
    assert pages["https://b.example/"].pagerank > 0
    labels = [
        SerpLabel("q", "https://a.example/", 1, None, "manual_csv"),
        SerpLabel("q", "https://b.example/", 2, None, "manual_csv"),
    ]
    examples, _, _ = build_examples(labels, pages, today=date(2026, 10, 29))
    by_url = {row.url: row.features for row in examples}
    assert by_url["https://b.example/"]["authority"] == 0.9
    assert by_url["https://a.example/"]["authority"] != by_url["https://a.example/"]["authority"]  # NaN


def test_crawl_artifacts_are_not_model_inputs_and_graph_does_not_overwrite_corpus_signals():
    from aeo.ranksim.features import CRAWL_ARTIFACT_FEATURES, FEATURE_NAMES, MODEL_FEATURES
    from aeo.ranksim.lambdamart import RankExample, fit_lambdamart
    from aeo.ranksim.linkgraph import GraphNode, LinkGraph, attach_graph

    assert not (set(MODEL_FEATURES) & CRAWL_ARTIFACT_FEATURES)
    assert set(MODEL_FEATURES) | CRAWL_ARTIFACT_FEATURES == set(FEATURE_NAMES)
    rows = [
        RankExample(f"q{q}", f"https://s{q}{p}.example/", float(p), {name: float(p % 2) for name in FEATURE_NAMES})
        for q in range(3)
        for p in range(1, 4)
    ]
    assert list(fit_lambdamart(rows).feature_name()) == list(MODEL_FEATURES)

    page = _page("https://a.example/")
    page.pagerank, page.anchors = 0.3, "corpus anchor"
    graph = LinkGraph()
    graph.nodes["https://a.example/"] = GraphNode(url="https://a.example/", depth=1, orphan=False, inlinks=4, pagerank=0.9)
    graph.edges.append(("https://b.example/", "https://a.example/", "nav anchor"))
    attach_graph({"https://a.example/": page}, graph)
    assert page.pagerank == 0.3 and page.anchors == "corpus anchor"
    assert page.graph_pagerank == 0.9 and page.crawl_inlinks == 4
