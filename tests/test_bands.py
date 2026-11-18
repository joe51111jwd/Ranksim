"""Bands cover a set of positions. Width is chosen by the Winkler score on the pairs it is given."""

import hashlib
import json
from pathlib import Path

from aeo.ranksim.bands import (
    choose_k,
    choose_offsets,
    clip_band,
    collect_bands,
    containment,
    coverage,
    last_observed_band,
    mean_winkler,
    observed_band,
    persistence_band,
    pool_scores,
    score_url,
    uniform_chance,
    winkler,
)
from aeo.ranksim.consensus import ConsensusUrl


def _band(positions: tuple[int, ...], previous: tuple[int, int, int] | None = None):
    row = ConsensusUrl("q", "https://a.example", "a", 1, positions, tuple(position for position in positions if position != 11))
    return observed_band(row, previous)


def test_absence_widens_the_observed_band_to_eleven():
    band = _band((1, 11, 2))
    assert (band.low, band.high, band.positions) == (1, 11, (1, 11, 2))


def test_winkler_charges_width_and_a_miss():
    assert winkler(2, 4, 3) == 3
    assert winkler(2, 4, 1) == 3 + 10 * 1
    assert winkler(2, 4, 6) == 3 + 10 * 2


def test_coverage_and_containment_are_different_aggregates():
    partial = score_url(_band((2, 4, 11)), 2, 4)
    full = score_url(_band((2, 3, 4)), 2, 4)
    totals = pool_scores([partial, full])
    assert coverage(totals) == 5 / 6
    assert containment(totals) == 0.5
    assert mean_winkler(totals) > 0


def test_k_stays_narrow_when_the_point_already_hits():
    chosen = choose_k([(4, (4,)), (4, (4,)), (7, (7,))])
    assert chosen["k"] == 0


def test_k_widens_when_the_point_is_two_away():
    chosen = choose_k([(4, (6,)), (4, (6,)), (4, (6,))])
    assert chosen["k"] == 2


def test_offset_menu_can_prefer_a_downward_band():
    # The center is always two above the observation. A downward band costs less than a symmetric one.
    chosen = choose_offsets([(6, (4,)), (6, (4,)), (8, (6,))])
    assert chosen["low_offset"] < 0
    assert chosen["high_offset"] <= 0 or chosen["low_offset"] == -chosen["high_offset"]


def test_persistence_and_last_band_use_yesterday_or_eleven():
    new = _band((5,))
    assert persistence_band(new, 1) == (10, 12) or clip_band(11, -1, 1) == persistence_band(new, 1)
    assert persistence_band(new, 1) == clip_band(11, -1, 1)
    assert last_observed_band(new) == (11, 11)
    old = _band((5, 7), (2, 4, 3))
    assert last_observed_band(old) == (2, 4)
    assert persistence_band(old, 1) == (2, 4)


def test_a_url_missing_from_every_pull_is_eleven():
    pulls = [{"a": ("https://a.example", 1)}]
    bands = collect_bands("q", pulls, {}, {"b": ("https://b.example", 4, 4, 4)})
    by_key = {band.url_key: band for band in bands}
    assert by_key["b"].positions == (11,)
    assert by_key["b"].previous_consensus == 4
    assert by_key["a"].previous_consensus is None


def test_frozen_band_calibration_matches_its_manifest():
    root = Path(__file__).resolve().parents[1] / "evals/preregistered/band-2026-11-18"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert digest == expected
    assert manifest["persistence_k"] == 6
    assert manifest["nov17_gate_unchanged"] is True


def test_uniform_chance_counts_only_positions_inside_the_band():
    assert uniform_chance(1, 1, top=1) == 1
    assert uniform_chance(2, 4, top=1) == 0
    assert uniform_chance(2, 4, top=3) == 2 / 3
