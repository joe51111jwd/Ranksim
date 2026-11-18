"""Consensus position from several pulls. Imputed absences are not 'shown'."""

from aeo.ranksim.consensus import (
    ABSENT_RANK,
    consensus_from_pulls,
    leave_one_out_ceiling,
    median_position,
    pool,
    predict_from_pull,
    score_query,
)


def test_median_uses_half_up_and_absence_is_not_a_shown_position():
    assert median_position([1, 1, 11]) == 1
    assert median_position([2, 8]) == 5
    assert median_position([3, 4]) == 4
    pulls = [
        {"a": ("https://a.example", 1), "b": ("https://b.example", 2)},
        {"a": ("https://a.example", 1)},
        {"a": ("https://a.example", 2), "b": ("https://b.example", 4)},
    ]
    rows = consensus_from_pulls("q", pulls, {"b": 15})
    by_key = {row.url_key: row for row in rows}
    assert by_key["a"].consensus == 1
    assert by_key["a"].shown_positions == (1, 1, 2)
    # Second pull missed b. Deep rank 15 is shown. The other pulls showed 2 and 4.
    assert 15 in by_key["b"].pull_positions
    assert ABSENT_RANK not in by_key["b"].shown_positions or 15 in by_key["b"].shown_positions
    assert by_key["b"].shown_positions == (2, 15, 4)


def test_leave_one_out_ceiling_uses_only_the_other_pulls():
    pulls = [
        {"a": ("https://a.example", 1), "b": ("https://b.example", 2)},
        {"a": ("https://a.example", 1), "b": ("https://b.example", 2)},
        {"a": ("https://a.example", 1), "b": ("https://b.example", 3)},
    ]
    scores = leave_one_out_ceiling("q", pulls, {})
    assert len(scores) == 3
    pooled = pool(scores)
    assert pooled["number_one"] == (3.0, 3.0)
    # Holding out the third pull, consensus of the first two puts b at 2, and the third pull says 3.
    third = scores[2]
    assert third.exact[0] == 1  # a matches, b does not


def test_observed_set_exact_ignores_the_imputed_absence():
    pulls = [
        {"a": ("https://a.example", 4)},
        {"b": ("https://b.example", 1)},
    ]
    rows = consensus_from_pulls("q", pulls, {})
    # Predict 11 for both. 11 was not shown, so observed-set exact is 0.
    scored = score_query(rows, predict_from_pull(rows, {}, {}))
    assert scored.observed == (0, 2)
    assert scored.exact[1] == 2
