"""Held-out scoring for pages with no saved history.

The gates in aeo.ranksim.new_page are fixed before the Nov 9 labels are read.
A later edit that moves a gate to fit those labels is a protocol break.
"""

from __future__ import annotations

import json
from pathlib import Path

from aeo.ranksim.new_page import (
    DRAFT_ADOPTION_MARGIN,
    ObservedRow,
    ScoredCandidate,
    adopt_draft_path,
    choose_blend_weight,
    content_query_scores,
    domain_prior_scores,
    draft_failure_counts,
    evaluate_held_out,
    load_forward_rows,
    url_query_overlap,
    write_new_page_ledger,
)
from aeo.ranksim.ledger import verify_ledger
from aeo.ranksim.store import open_session

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "evals" / "forward-2026-11-09" / "ledger.json"
BASELINE_CSV = ROOT / "evals" / "forward-2026-11-09" / "baseline_serp.csv"
FORWARD_CSV = ROOT / "evals" / "forward-2026-11-09" / "forward_serp.csv"


def test_url_overlap_uses_the_query_words_in_the_slug():
    matched = url_query_overlap("dirt work software", "https://crew.example/dirt-work")
    missed = url_query_overlap("dirt work software", "https://crew.example/cake-recipe")
    assert matched > missed
    assert missed == 0.0


def test_missing_html_is_neutral_not_a_sentinel():
    rows = [
        ScoredCandidate("dirt software", "https://a.example/dirt-software", 2, 10.0, "forward_fetch", True),
        ScoredCandidate("dirt software", "https://b.example/dirt-software", 1, 0.0, "forward_fetch", True),
        ScoredCandidate("dirt software", "https://c.example/dirt-software", 3, None, "missing", True),
    ]
    scores = content_query_scores(rows)
    assert scores[2] > -1.0e8
    assert scores[0] > scores[2] > scores[1]


def test_domain_prior_ignores_the_held_out_query_and_later_labels():
    history = [
        ObservedRow("excavation software", "https://good.example/old", 1),
        ObservedRow("excavation software", "https://bad.example/old", 2),
        ObservedRow("grading software", "https://good.example/new", 5),
        ObservedRow("grading software", "https://bad.example/new", 1),
    ]
    candidates = [
        ScoredCandidate("grading software", "https://good.example/new", 9, None, "missing", True),
        ScoredCandidate("grading software", "https://bad.example/new", 1, None, "missing", True),
        ScoredCandidate("grading software", "https://new.example/page", 4, None, "missing", True),
    ]
    scores = domain_prior_scores(candidates, history, holdout_queries={"grading software"})
    assert scores[0] > scores[1]
    scrambled = [
        ScoredCandidate(row.query, row.url, 1 if row.label_position == 9 else 9, row.content, row.html_source, row.entrant)
        for row in candidates
    ]
    assert domain_prior_scores(scrambled, history, holdout_queries={"grading software"}) == scores
    # The unseen host gets the training mean, between the two known hosts.
    assert scores[1] < scores[2] < scores[0]


def test_blend_weight_is_fit_on_nov6_order_only():
    history = [
        ObservedRow("excavation software", "https://match.example/excavation-software", 1),
        ObservedRow("excavation software", "https://nomatch.example/zzzz", 2),
        ObservedRow("grading software", "https://nomatch.example/grading-software", 1),
        ObservedRow("grading software", "https://match.example/zzzz", 2),
    ]
    assert choose_blend_weight(history) == 0.25


def test_held_out_scores_do_not_move_when_later_labels_move():
    history = [
        ObservedRow("excavation software", "https://good.example/excavation", 1),
        ObservedRow("excavation software", "https://bad.example/other", 2),
    ]
    later = [
        ScoredCandidate("grading software", "https://good.example/grading", 2, 1.0, "forward_fetch", True),
        ScoredCandidate("grading software", "https://bad.example/zzzz", 1, 3.0, "forward_fetch", True),
    ]
    flipped = [
        ScoredCandidate(row.query, row.url, 3 - row.label_position, row.content, row.html_source, row.entrant)
        for row in later
    ]
    left = evaluate_held_out(later, history, baselines={}, draws=20, seed=1)
    right = evaluate_held_out(flipped, history, baselines={}, draws=20, seed=1)
    assert left["scores"]["domain_prior"] == right["scores"]["domain_prior"]
    assert left["scores"]["content_query"] == right["scores"]["content_query"]
    assert left["scores"]["calibrated_blend"] == right["scores"]["calibrated_blend"]


def _slice(pairwise: float, low: float) -> dict:
    return {"pairwise": pairwise, "delta_vs_lambdamart_ci": [low, low + 0.2]}


def _metrics(scorer: str, dd: tuple[float, float], dvr: tuple[float, float], *, content: bool) -> dict:
    dd_name = "draft_vs_draft_with_content" if content else "draft_vs_draft"
    dvr_name = "draft_vs_returning_with_content" if content else "draft_vs_returning"
    return {
        "lambdamart": {dd_name: {"pairwise": 0.293 if not content else 0.545}, dvr_name: {"pairwise": 0.477 if not content else 0.521}},
        "bm25": {dd_name: {"pairwise": 0.319 if not content else 0.500}, dvr_name: {"pairwise": 0.420 if not content else 0.425}},
        scorer: {dd_name: _slice(*dd), dvr_name: _slice(*dvr)},
    }


def test_adoption_margin_rejects_a_small_or_uncertain_gain():
    assert DRAFT_ADOPTION_MARGIN == 0.05
    small = _metrics("domain_prior", (0.33, 0.01), (0.55, 0.02), content=False)
    assert adopt_draft_path(small, "domain_prior", content=False)["adopt"] is False
    uncertain = _metrics("domain_prior", (0.40, -0.01), (0.55, 0.02), content=False)
    decision = adopt_draft_path(uncertain, "domain_prior", content=False)
    assert decision["adopt"] is False
    assert "interval" in decision["reason"]


def test_adoption_accepts_only_when_both_slices_clear_the_current_path():
    good = _metrics("content_query", (0.40, 0.02), (0.55, 0.02), content=False)
    assert adopt_draft_path(good, "content_query", content=False)["adopt"] is True
    loses_returning = _metrics("content_query", (0.40, 0.02), (0.50, 0.01), content=False)
    assert adopt_draft_path(loses_returning, "content_query", content=False)["adopt"] is False
    loses_bm25 = _metrics("content_query", (0.30, 0.02), (0.55, 0.02), content=False)
    assert adopt_draft_path(loses_bm25, "content_query", content=False)["adopt"] is False


def test_product_gate_ignores_a_win_that_exists_only_on_missing_html():
    published = _metrics("content_query", (0.40, 0.02), (0.55, 0.02), content=False)
    pasted = _metrics("content_query", (0.56, 0.01), (0.54, -0.02), content=True)
    assert adopt_draft_path(published, "content_query", content=False)["adopt"] is True
    assert adopt_draft_path(pasted, "content_query", content=True)["adopt"] is False


def test_new_page_ledger_is_predicted_and_unsure(tmp_path):
    history = [ObservedRow("excavation software", "https://good.example/excavation", 1)]
    later = [
        ScoredCandidate("grading software", "https://good.example/grading", 1, 1.0, "forward_fetch", True),
        ScoredCandidate("grading software", "https://bad.example/zzzz", 2, None, "missing", True),
    ]
    result = evaluate_held_out(later, history, baselines={}, draws=10, seed=2)
    database = f"sqlite:///{tmp_path / 'ledger.db'}"
    session = open_session(database)
    try:
        summary = write_new_page_ledger(session, result, later)
        ok, note = verify_ledger(session)
    finally:
        session.close()
    assert ok
    assert "intact" in note
    assert summary["content_query"]["predictions"] == 2
    session = open_session(database)
    try:
        from aeo.ranksim.ledger import RsPrediction

        rows = session.query(RsPrediction).all()
        assert rows
        for row in rows:
            assert row.payload["provenance"]["label"] == "PREDICTED"
            assert row.payload["confidence"] == "UNSURE"
            assert row.payload["provenance"]["uncertainty"] == "UNSURE"
    finally:
        session.close()


def test_published_draft_failure_is_the_missing_html_sentinel_not_a_flipped_label():
    rows = load_forward_rows(LEDGER, BASELINE_CSV, FORWARD_CSV)
    counts = draft_failure_counts(rows)
    assert counts["orientation_correct"] > counts["orientation_wrong"]
    draft = counts["draft_vs_draft"]
    assert (draft["correct"], draft["wrong"], draft["ties"], draft["pairs"]) == (34, 54, 28, 116)
    assert counts["both_missing_ties"] == 28
    assert (counts["mixed_correct"], counts["mixed_wrong"]) == (22, 44)
    content = counts["draft_vs_draft_with_content"]
    assert (content["correct"], content["wrong"], content["ties"]) == (12, 10, 0)
    assert counts["largest_query_pairs"] < counts["draft_vs_draft"]["pairs"] / 2
    assert counts["largest_query_loo_pairwise"] < 0.40
    assert counts["forward_csv_mismatches"] == 0
    assert counts["candidates"] == 299
    assert counts["queries"] == 30
    assert counts["score_gap_mismatches"] == 0
    assert counts["missing_drafts"] == 29
    assert counts["fetched_drafts"] == 34
    assert counts["queries_with_at_least_3_pairs_under_half"] == 6
    assert round(counts["missing_draft_mean_position"], 3) == 5.448
    assert round(counts["fetched_draft_mean_position"], 3) == 7.441
    assert counts["drafts_domain_seen_on_another_nov6_query"] == 37
    assert counts["drafts_domain_absent_on_nov6"] == 26
    assert counts["drafts_domain_only_on_this_query"] == 0


def test_committed_new_page_report_matches_a_fresh_held_out_run():
    report_path = ROOT / "evals" / "forward-2026-11-09" / "new_page_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    loaded = load_forward_rows(LEDGER, BASELINE_CSV, FORWARD_CSV)
    fresh = evaluate_held_out(loaded.candidates, loaded.history, loaded.baselines, draws=20, seed=1)
    for scorer in ("lambdamart", "bm25", "domain_prior", "content_query", "calibrated_blend"):
        for name in ("draft_vs_draft", "draft_vs_draft_with_content", "draft_vs_returning", "draft_vs_returning_with_content", "all"):
            stored = report["metrics"][scorer][name]
            got = fresh["metrics"][scorer][name]
            assert (got["correct"], got["wrong"], got["ties"], got["pairs"]) == (
                stored["correct"],
                stored["wrong"],
                stored["ties"],
                stored["pairs"],
            )
    assert report["adopted_scorers"] == []
    assert report["ranker_change"] == "none"
    for decision in report["adoption"].values():
        assert decision["published_draft_slices"]["adopt"] is False
        assert decision["pasted_draft_path"]["adopt"] is False
