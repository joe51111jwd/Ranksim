"""The whole-site table and link suggestions score queries with the intents training used (B8),
not a fixed "commercial". Fixtures are synthetic pages, not Google data."""

from datetime import date

import aeo.ranksim.check as check
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.linkgraph import GraphNode, LinkGraph

QUERY = "procore alternative"  # the shared rule makes this a comparison query
OURS = "https://www.smartcrewhq.com/procore-alternative/"


class _FixedModel:
    def feature_name(self):
        return ["bm25f"]

    def predict(self, matrix):
        return [10.0 - 2.0 * index for index in range(len(matrix))]

    def feature_importance(self, importance_type="gain"):
        return [0.0]


def _setup():
    body = "procore alternative " + " ".join(f"word{index}" for index in range(300))
    labels = [
        SerpLabel(QUERY, "https://c1.example/", 1, None, "manual_csv"),
        SerpLabel(QUERY, OURS, 2, None, "manual_csv"),
        SerpLabel(QUERY, "https://c3.example/", 3, None, "manual_csv"),
    ]
    pages = {
        url: PageDoc(url=url, title="Procore alternative", body=body, word_count=300)
        for url in ("https://c1.example/", "https://c3.example/")
    }
    pages[OURS] = PageDoc(url=OURS, title="Procore alternative", body=body, word_count=300, is_ours=True)
    return labels, pages


def _spy(monkeypatch):
    seen = []
    real = check.check_draft

    def spy(**kwargs):
        seen.append(kwargs.get("intent", "<default>"))
        return real(**kwargs)

    monkeypatch.setattr(check, "check_draft", spy)
    return seen


def test_score_site_uses_the_shared_intent_rule(monkeypatch):
    labels, pages = _setup()
    seen = _spy(monkeypatch)
    rows = check.score_site(pages, labels, {QUERY: _FixedModel()}, [QUERY], date(2026, 11, 10))
    assert rows and seen == ["comparison"]  # before: the check_draft default, "commercial"


def test_score_site_takes_the_callers_intents(monkeypatch):
    labels, pages = _setup()
    seen = _spy(monkeypatch)
    check.score_site(pages, labels, {QUERY: _FixedModel()}, [QUERY], date(2026, 11, 10), intents={QUERY: "informational"})
    assert seen == ["informational"]


def test_suggest_links_scores_before_and_after_with_the_shared_intent(monkeypatch):
    labels, pages = _setup()
    graph = LinkGraph(
        nodes={
            "https://www.smartcrewhq.com/": GraphNode(url="https://www.smartcrewhq.com/", depth=0, status="fetched", pagerank=0.9),
            OURS: GraphNode(url=OURS, depth=1, status="fetched", pagerank=0.1),
        }
    )
    seen = _spy(monkeypatch)
    check.suggest_links(graph, pages, labels, {QUERY: _FixedModel()}, today=date(2026, 11, 10))
    assert seen and set(seen) == {"comparison"}
