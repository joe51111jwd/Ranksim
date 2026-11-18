"""The model inserts new URLs into yesterday's order and shifts the rest down."""

import hashlib
import json
from pathlib import Path

from aeo.ranksim.hybrid import (
    bootstrap_difference,
    covers_three_wide,
    insert_into_persistence,
    new_url_scores,
    positions_from_order,
    three_wide,
)


def test_a_new_url_ranked_above_every_anchor_takes_position_one():
    order = insert_into_persistence(["a", "b"], ["new"], {"a": 1.0, "b": 0.5, "new": 2.0})
    assert order == ["new", "a", "b"]
    assert positions_from_order(order)["new"] == 1
    assert positions_from_order(order)["a"] == 2


def test_a_new_url_ranked_below_every_anchor_goes_to_the_end():
    order = insert_into_persistence(["a", "b"], ["new"], {"a": 2.0, "b": 1.0, "new": 0.0})
    assert order == ["a", "b", "new"]


def test_two_new_urls_keep_model_order_inside_the_same_slot():
    order = insert_into_persistence(["a"], ["low", "high"], {"a": 1.0, "high": 0.5, "low": 0.2})
    assert order == ["a", "high", "low"]


def test_three_wide_from_one_covers_the_top_three():
    assert three_wide(1) == (1, 3)
    assert covers_three_wide(8, 9)
    assert not covers_three_wide(8, 6)


def test_observed_set_exact_accepts_any_shown_position():
    scored = new_url_scores(4, 6, (4, 6, 11))
    assert scored["exact"] == 0.0
    assert scored["three_wide"] == 0.0
    assert scored["observed"] == 1.0
    assert scored["top3_case"] == 0.0


def test_frozen_hybrid_matches_its_manifest():
    root = Path(__file__).resolve().parents[1] / "evals/preregistered/hybrid-2026-11-19"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert digest == expected
    assert manifest["median_new_position"] == 7
    assert manifest["nov18_gates_unchanged"] is True
    assert "2026-11-17" in manifest["not_used_for_selection"]


def test_difference_interval_is_zero_when_the_two_sides_match():
    point, low, high = bootstrap_difference([(1, 2), (1, 2)], [(1, 2), (1, 2)])
    assert point == 0.0
    assert low == 0.0
    assert high == 0.0
