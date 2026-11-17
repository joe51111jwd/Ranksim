"""Tight bands stay within width 3. Abstentions stay out of the coverage denominator."""

import hashlib
import json
from pathlib import Path

from aeo.ranksim.tight_bands import (
    TightRow,
    choose_width_cuts,
    clip_last_band,
    issue_band,
    persistence_pm1,
    summarize,
    tight_band,
)


def _row(position: float, outcome: int, seen: float = 1.0, url_vol: float = 0.0, query_vol: float = 0.0) -> TightRow:
    return TightRow("q", "a", seen, position, url_vol, query_vol, (outcome,))


def test_width_is_never_above_three():
    for center in (1, 4, 10, 11, 30):
        for width in (1, 2, 3):
            low, high = tight_band(center, width)
            assert high - low + 1 == width
            assert 1 <= low <= high <= 30


def test_persistence_pm1_is_at_most_three_wide():
    assert persistence_pm1(4) == (3, 5)
    assert persistence_pm1(1) == (1, 2)
    assert persistence_pm1(11) == (10, 12)


def test_last_band_wider_than_three_keeps_the_consensus():
    assert clip_last_band(2, 8, 5) == (4, 6)
    assert clip_last_band(4, 6, 5) == (4, 6)
    assert clip_last_band(7, 7, 7) == (7, 7)


def test_abstention_is_left_out_of_coverage_and_counted_in_the_fraction():
    rows = [
        (_row(4, 4), (4, 4)),
        (_row(8, 2), None),
    ]
    totals = summarize(rows)
    assert totals["answered_fraction"] == 0.5
    assert totals["coverage"] == 1.0
    assert totals["urls"] == 2


def test_issue_band_abstains_when_the_expected_error_is_high():
    row = _row(4, 4, url_vol=5.0)
    # width coefficients put all the weight on url volatility
    band = issue_band(
        row,
        center_coefficients=[4, 0, 0, 0, 0],
        width_coefficients=[0, 0, 0, 1, 0],
        cut1=0.5,
        cut2=1.5,
        abstain_above=2.0,
    )
    assert band is None
    issued = issue_band(
        row,
        center_coefficients=[4, 0, 0, 0, 0],
        width_coefficients=[0, 0, 0, 1, 0],
        cut1=0.5,
        cut2=1.5,
        abstain_above=10.0,
    )
    assert issued is not None
    assert issued[1] - issued[0] + 1 <= 3


def test_frozen_tight_calibration_matches_its_manifest():
    root = Path(__file__).resolve().parents[1] / "evals/preregistered/tight-band-2026-11-18"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["sha256"].items():
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert digest == expected
    assert manifest["max_width"] == 3
    assert manifest["wide_band_gate_unchanged"] is True
    assert "2026-11-16" in manifest["not_used_for_selection"]


def test_cut_choice_uses_only_the_rows_it_is_given():
    easy = [_row(4, 4), _row(5, 5)]
    chosen = choose_width_cuts(
        easy,
        center_coefficients=[0, 0, 1, 0, 0],
        width_coefficients=[0, 0, 0, 0, 0],
        cuts=[(0.5, 1.5), (1.0, 2.0)],
    )
    assert chosen["coverage"] == 1.0
    assert chosen["answered_fraction"] == 1.0
