from aeo.providers.llm import choose_mock_indices, mock_include_passage
from aeo.sim.citations import (
    SourcePassage,
    aggregate_scores,
    score_answer,
)


SOURCES = [
    SourcePassage(1, "p1", "https://northwind.example/trail-mix", "northwind.example"),
    SourcePassage(2, "p2", "https://rival-outdoor.example/roundup", "rival-outdoor.example"),
]


def test_citation_position_mention_and_unknown_markers():
    answer = (
        "Northwind is a reasonable pick for long days [S1]. "
        "A roundup disagrees [S2]. See also [S1] and [S9]. "
        "https://evil.example/not-a-source is not a retrieved page."
    )
    score = score_answer(answer, SOURCES, "www.northwind.example")
    assert score.cited is True
    assert score.citation_position == 1
    assert score.mentioned is True
    assert [item.domain for item in score.citations] == ["northwind.example", "rival-outdoor.example"]
    assert score.unknown_markers == ["S9"]
    assert all(item.url != "https://evil.example/not-a-source" for item in score.citations)


def test_mentioned_without_being_cited():
    answer = "Northwind publishes a guide, but this answer only cites the roundup [S2]."
    score = score_answer(answer, SOURCES, "northwind.example")
    assert score.cited is False
    assert score.citation_position is None
    assert score.mentioned is True


def test_aggregate_share_and_mean_position():
    scores = [
        score_answer("Cited first [S1].", SOURCES, "northwind.example"),
        score_answer("Only the rival [S2].", SOURCES, "northwind.example"),
        score_answer("Northwind is named, rival is cited [S2].", SOURCES, "northwind.example"),
        score_answer("The rival is first [S2]. Northwind follows [S1].", SOURCES, "northwind.example"),
    ]
    metrics = aggregate_scores(scores)
    assert metrics.n_runs == 4
    assert metrics.cited_runs == 2
    assert metrics.citation_share == 0.5
    assert metrics.mentioned_runs == 2
    assert metrics.mention_rate == 0.5
    assert metrics.mean_citation_position == 1.5


def test_mock_variation_drops_the_top_passage_once_per_five_runs():
    included = [mock_include_passage(0, run_index) for run_index in range(5)]
    assert included == [True, True, True, True, False]
    assert choose_mock_indices(3, 4) == [1]
    assert choose_mock_indices(1, 4) == [0]
