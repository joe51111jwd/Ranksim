"""The adoption gate is fixed before the recovered Nov 9 scores are read."""

from aeo.ranksim.recovered_eval import GATE_SLICES, PUBLISHED_LAMBDAMART, adoption_gate, published_rate


def _block(correct: int, wrong: int) -> dict[str, tuple[int, int, int]]:
    return {name: (correct, wrong, 0) for name in ("q1", "q2", "q3", "q4")}


def test_slice_counts_keep_a_recovered_page_in_the_text_slice():
    from aeo.ranksim.recovered_eval import slice_counts

    rows = [
        {"query": "alpha", "url": "https://a.example/old", "position": 2, "entrant": False, "html_source": "baseline", "lambdamart": 0.2},
        {"query": "alpha", "url": "https://b.example/new", "position": 1, "entrant": True, "html_source": "recovered_browser", "lambdamart": 0.9},
        {"query": "alpha", "url": "https://c.example/gap", "position": 3, "entrant": True, "html_source": "missing", "lambdamart": 0.0},
    ]
    both = slice_counts(rows, "lambdamart", "draft_vs_draft")
    text = slice_counts(rows, "lambdamart", "draft_vs_draft_with_content")
    assert both["alpha"] == (1, 0, 0)
    assert "alpha" not in text


def test_published_rates_match_the_committed_counts():
    assert published_rate("draft_vs_draft") == 34 / 116
    assert published_rate("draft_vs_draft_with_content") == 12 / 22
    assert published_rate("draft_vs_returning") == 134 / 281
    assert published_rate("draft_vs_returning_with_content") == 87 / 167
    assert set(GATE_SLICES) == set(PUBLISHED_LAMBDAMART)


def test_gate_rejects_a_gain_whose_interval_includes_zero():
    # 20/40 = 0.50. Draft-versus-draft published is 0.293, so the point clears 0.05,
    # but four identical queries make a wide interval. BM25 is lower.
    new = {name: _block(5, 5) for name in GATE_SLICES}
    bm25 = {name: _block(1, 9) for name in GATE_SLICES}
    decision = adoption_gate(new, bm25, draws=200, seed=20261110)
    assert decision["adopt"] is False
    assert decision["label"] == "PREDICTED"


def test_gate_rejects_a_scorer_below_bm25_or_short_of_the_margin():
    new = {name: _block(9, 1) for name in GATE_SLICES}
    bm25 = {name: _block(10, 0) for name in GATE_SLICES}
    below_bm25 = adoption_gate(new, bm25, draws=50, seed=1)
    assert below_bm25["adopt"] is False
    assert "BM25" in below_bm25["reason"]

    short = {name: _block(4, 6) for name in GATE_SLICES}  # 0.40, under published + 0.05 on the ranking slice
    level = {name: _block(1, 9) for name in GATE_SLICES}
    missed = adoption_gate(short, level, draws=50, seed=1)
    assert missed["adopt"] is False


def test_gate_adopts_only_when_every_slice_clears_the_bar():
    new = {name: {f"q{i}": (10, 0, 0) for i in range(12)} for name in GATE_SLICES}
    bm25 = {name: {f"q{i}": (5, 5, 0) for i in range(12)} for name in GATE_SLICES}
    decision = adoption_gate(new, bm25, draws=200, seed=20261110)
    assert decision["adopt"] is True
    assert decision["slices"]["draft_vs_draft"]["delta_vs_published_ci"][0] > 0
