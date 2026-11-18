"""Position hits, ranges, and confident #1 calls. Outcome positions are labels."""

from aeo.ranksim.pair_order import Row
from aeo.ranksim.position_metrics import (
    RANGE_REFERENCE,
    RANGE_TARGET,
    apply_ranges,
    choose_range_rule,
    clip_range,
    persistence_points,
    pin_previous,
    position_record,
    predict_positions,
    ranks_from_scores,
    select_point_method,
    stability_table,
)


def _row(query: str, url: str, position: float, bucket: str, last: int | None = None) -> Row:
    return Row(
        day="2026-11-09",
        query=query,
        url=url,
        url_key=url,
        position=position,
        bucket=bucket,
        group="other",
        features={
            "has_history": 0.0 if last is None else 1.0,
            "last_relevance": 0.0 if last is None else float(11 - last),
            "content_present": 1.0,
        },
    )


class _Snap:
    def __init__(self, day, triples):
        self.day = day
        self.rows = [
            type("R", (), {"query": query, "url_key": url, "position": position})()
            for query, url, position in triples
        ]


def test_range_target_is_95_and_keeps_90_for_reference():
    assert RANGE_TARGET == 0.95
    assert RANGE_REFERENCE == 0.90
    pairs = [(1, 1, "surviving")] * 19 + [(1, 6, "surviving")]
    rule = choose_range_rule(pairs)
    assert rule["target"] == 0.95
    assert rule["reference_90"]["target"] == 0.90
    # 19/20 are exact. A point range covers 0.95, so the 95% rule stays a single position.
    assert (rule["global"]["low_offset"], rule["global"]["high_offset"]) == (0, 0)


def test_ranges_stay_at_most_three_wide_and_count_every_row():
    rows = [
        _row("q", "https://a.example", 1, "surviving", 1),
        _row("q", "https://b.example", 2, "new"),
        _row("q", "https://c.example", 4, "new"),
    ]
    preds = [1, 10, 10]
    ranges = apply_ranges(preds, [row.bucket for row in rows], choose_range_rule([(pred, int(row.position), row.bucket) for pred, row in zip(preds, rows)]))
    assert all(span <= 3 and low <= high for low, high, span in ranges)
    record = position_record(rows, preds, ranges)
    assert record["all"]["rows"] == 3
    assert record["all"]["exact"] == 1 / 3
    assert record["new"]["rows"] == 2


def test_persistence_points_use_last_position_and_put_new_urls_at_10():
    rows = [
        _row("q", "https://a.example", 3, "surviving", 2),
        _row("q", "https://b.example", 1, "new"),
    ]
    assert persistence_points(rows) == [2, 10]


def test_pin_previous_number_one_keeps_that_url_at_rank_1():
    rows = [
        _row("q", "https://old.example", 2, "surviving", 1),
        _row("q", "https://new.example", 1, "new"),
    ]
    # The model prefers the new URL. Pinning yesterday's #1 still leaves it at rank 1.
    assert pin_previous(rows, [0.1, 5.0], top=1) == [1, 2]
    assert ranks_from_scores(rows, [0.1, 5.0]) == [2, 1]


def test_edge_clip_does_not_widen_the_range():
    low, high, span = clip_range(1, -1, 1)
    assert (low, high, span) == (1, 2, 2)
    low, high, span = clip_range(10, 0, 2)
    assert span <= 3
    assert high <= 10


def test_selection_uses_exact_hits_and_prefers_the_simpler_tie():
    rows = [
        _row("q", "https://a.example", 1, "surviving", 1),
        _row("q", "https://b.example", 2, "surviving", 2),
    ]
    # Both persistence and a model that copies it are exact. Persistence is simpler and wins a pure tie
    # only against later methods; model_rank is first and also exact when scores match the order.
    chosen = select_point_method([(rows, [2.0, 1.0])])
    assert chosen == "model_rank"
    chosen = select_point_method([(rows, [0.0, 5.0])])
    assert chosen == "persistence_last"


def test_stability_counts_the_same_url_at_a_rank():
    first = _Snap("2026-11-06", [("q", "https://a.example", 1), ("q", "https://b.example", 2), ("q", "https://c.example", 3)])
    second = _Snap("2026-11-09", [("q", "https://a.example", 1), ("q", "https://c.example", 2), ("q", "https://d.example", 3)])
    table = stability_table([first, second])
    assert table[0]["same_1"] == 1
    assert table[0]["same_2"] == 0
    assert table[0]["top3_kept"] == 2
    assert table[0]["top3_slots"] == 3


def test_frozen_position_calibration_targets_95_percent():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evals/preregistered/forward-pair-2026-11-16"
    payload = json.loads((root / "position-calibration.json").read_text(encoding="utf-8"))
    assert payload["label"] == "PREDICTED"
    assert payload["range_target"] == 0.95
    assert payload["range_rule"]["target"] == 0.95
    assert payload["range_rule_90"]["target"] == 0.9
    assert payload["not_used_for_calibration"] == ["2026-11-13", "2026-11-16"]


def test_predict_positions_rejects_an_unknown_method():
    rows = [_row("q", "https://a.example", 1, "new")]
    try:
        predict_positions("not-a-method", rows, [1.0])
    except ValueError:
        return
    raise AssertionError("unknown method was accepted")
