"""Pairwise aggregation and direct position models. Scores are PREDICTED."""

from aeo.ranksim.pair_order import Row
from aeo.ranksim.position_aggregate import (
    ENSEMBLE_SIZE,
    average_win_matrix,
    borda_scores,
    bootstrap_query_rows,
    bradley_terry_scores,
    copeland_scores,
    max_mass_window,
    median_rank_scores,
    shift_window,
)


def _row(query: str, url: str, position: float) -> Row:
    return Row(
        day="2026-11-09",
        query=query,
        url=url,
        url_key=url,
        position=position,
        bucket="new",
        group="other",
        features={"has_history": 0.0, "last_relevance": 0.0},
    )


def test_ensemble_size_is_fixed_before_holdout():
    assert ENSEMBLE_SIZE == 20


def test_copeland_borda_and_bradley_terry_keep_a_transitive_order():
    # Three models agree that A beats B beats C.
    members = [[3.0, 2.0, 1.0], [3.0, 2.0, 1.0], [2.5, 2.0, 0.5]]
    win_prob = average_win_matrix(members)
    copeland = copeland_scores(win_prob)
    borda = borda_scores(win_prob)
    strength = bradley_terry_scores(win_prob)
    assert copeland[0] > copeland[1] > copeland[2]
    assert borda[0] > borda[1] > borda[2]
    assert strength[0] > strength[1] > strength[2]


def test_median_rank_prefers_the_usually_better_url():
    scores = median_rank_scores([[1, 1, 2], [3, 3, 2]])
    assert scores[0] > scores[1]


def test_max_mass_window_is_three_wide_and_covers_the_mode():
    low, high = max_mass_window([4, 4, 4, 5, 9])
    assert high - low == 2
    assert low <= 4 <= high


def test_shift_does_not_widen_the_window():
    low, high = shift_window(1, 3, -1, ceiling=10)
    assert (low, high) == (1, 3)
    low, high = shift_window(8, 10, 1, ceiling=10)
    assert high - low <= 2
    assert high <= 10


def test_bootstrap_copies_do_not_collapse_into_one_query_group():
    rows = [_row("q", "https://a.example", 1), _row("q", "https://b.example", 2)]
    copied = bootstrap_query_rows(rows, __import__("random").Random(0))
    assert len(copied) == len(rows)
    assert len({row.day for row in copied}) == 1
    assert all("#" in row.day for row in copied)
