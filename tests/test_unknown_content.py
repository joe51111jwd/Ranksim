"""No HTML is unknown. It is not a shared score under every page that has text."""

from __future__ import annotations

from datetime import date

from aeo.ranksim.features import FEATURE_NAMES, PageDoc, raw_features
from aeo.ranksim.forward_eval import MISSING_CONTENT_SCORE
from aeo.ranksim.unknown_content import score_scale, unknown_content_value


def test_content_missing_is_an_indicator_not_a_lexical_zero():
    stored = PageDoc(url="https://a.example/p", title="alpha tools", body="alpha tools " * 30, word_count=60)
    blank = PageDoc(url="https://b.example/q", content_missing=True)
    rows = raw_features("alpha tools", "commercial", [stored, blank], date(2026, 11, 7))
    assert "content_missing" in FEATURE_NAMES
    assert rows[0]["content_missing"] == 0.0
    assert rows[1]["content_missing"] == 1.0


def test_missing_pages_do_not_share_the_floor_and_can_rank_above_weak_text():
    rows = [
        {"query": "dirt work tools", "url": "https://weak.example/other", "html": True, "bm25": -5.0, "lambdamart": -5.0},
        {"query": "dirt work tools", "url": "https://dirt.example/dirt-work-tools", "html": False},
        {"query": "dirt work tools", "url": "https://zzz.example/unrelated", "html": False},
    ]
    history = [("payroll calendar", "https://cats.example/pay", 1.0)]
    unknown_content_value(rows, history, mean=0.0, std=1.0)
    assert rows[0]["lambdamart"] == -5.0
    assert rows[0]["content_missing"] == 0
    assert rows[1]["content_missing"] == 1
    assert rows[2]["content_missing"] == 1
    assert rows[1]["lambdamart"] != rows[2]["lambdamart"]
    assert rows[1]["lambdamart"] > MISSING_CONTENT_SCORE
    assert rows[2]["lambdamart"] > MISSING_CONTENT_SCORE
    assert rows[1]["lambdamart"] > rows[0]["lambdamart"]


def test_domain_prior_ignores_the_query_family():
    history = [
        ("hcss alternative", "https://weak.example/a", 1.0),
        ("hcss alternatives list", "https://weak.example/b", 1.0),
        ("payroll calendar", "https://weak.example/c", 10.0),
        ("payroll calendar", "https://strong.example/d", 5.0),
    ]
    rows = [
        {"query": "hcss alternative", "url": "https://weak.example/zzz", "html": False},
        {"query": "hcss alternative", "url": "https://strong.example/yyy", "html": False},
    ]
    unknown_content_value(rows, history, mean=0.0, std=1.0)
    assert rows[1]["lambdamart"] > rows[0]["lambdamart"]


def test_a_missing_offpage_part_adds_nothing():
    rows = [
        {"query": "dirt work tools", "url": "https://listed.example/a", "html": False},
        {"query": "dirt work tools", "url": "https://quiet.example/b", "html": False},
    ]
    history = [("payroll calendar", "https://cats.example/pay", 2.0)]
    unknown_content_value(
        rows,
        history,
        signals={"majestic_refsubnets": {"listed.example": 5.0}},
        mean=0.0,
        std=1.0,
    )
    # One listed domain cannot be z-scored, and the unlisted domain is left out.
    # Treating that gap as zero would push the two scores apart.
    assert rows[0]["lambdamart"] == rows[1]["lambdamart"]


def test_score_scale_uses_the_training_scores_only():
    mean, std = score_scale([1.0, 3.0])
    assert mean == 2.0
    assert std > 0
    assert score_scale([]) == (0.0, 1.0)
