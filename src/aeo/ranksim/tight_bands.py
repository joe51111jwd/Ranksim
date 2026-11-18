"""Tight position bands. Width is 1, 2, or 3. The model may abstain.

Center and width come from four features known before the outcome day: whether
the URL was seen, its last position, how far that position moved across past
pulls, and how far positions moved in the query. Predictions are PREDICTED.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aeo.ranksim.bands import ObservedBand, inclusive_width, score_url
from aeo.ranksim.consensus import ABSENT_RANK

TIGHT_MAX_WIDTH = 3


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def tight_band(center: int, width: int) -> tuple[int, int]:
    """A band of exactly `width` positions that contains center. Width is 1, 2, or 3."""
    if width < 1 or width > TIGHT_MAX_WIDTH:
        raise ValueError("tight width must be 1, 2, or 3")
    center = min(30, max(1, int(center)))
    if width == 1:
        return center, center
    if width == 2:
        low, high = center, center + 1
        if high > 30:
            return 29, 30
        return low, high
    low, high = center - 1, center + 1
    if low < 1:
        return 1, 3
    if high > 30:
        return 28, 30
    return low, high


def persistence_pm1(center: int) -> tuple[int, int]:
    """Yesterday's position ± 1. The edge clip can only make it narrower than 3."""
    center = min(30, max(1, int(center)))
    return max(1, center - 1), min(30, center + 1)


def clip_last_band(low: int, high: int, consensus: int) -> tuple[int, int]:
    """Keep yesterday's band. If it is wider than 3, keep the 3 positions around its consensus."""
    low, high = int(low), int(high)
    if high < low:
        low, high = high, low
    if inclusive_width(low, high) <= TIGHT_MAX_WIDTH:
        return low, high
    start = min(max(low, int(consensus) - 1), high - 2)
    return start, start + 2


@dataclass(frozen=True)
class TightRow:
    query: str
    url_key: str
    seen: float
    position: float
    url_volatility: float
    query_volatility: float
    positions: tuple[int, ...]


def features(row: TightRow) -> list[float]:
    return [1.0, row.seen, row.position, row.url_volatility, row.query_volatility]


def predict_center(row: TightRow, coefficients: list[float]) -> int:
    raw = sum(value * weight for value, weight in zip(features(row), coefficients, strict=True))
    return min(30, max(1, round_half_up(raw)))


def predict_abs_error(row: TightRow, coefficients: list[float]) -> float:
    raw = sum(value * weight for value, weight in zip(features(row), coefficients, strict=True))
    return max(0.0, raw)


def width_from_error(abs_error: float, cut1: float, cut2: float) -> int:
    if cut2 < cut1:
        raise ValueError("width cuts are reversed")
    if abs_error <= cut1:
        return 1
    if abs_error <= cut2:
        return 2
    return 3


def issue_band(
    row: TightRow,
    *,
    center_coefficients: list[float],
    width_coefficients: list[float],
    cut1: float,
    cut2: float,
    abstain_above: float,
) -> tuple[int, int] | None:
    """None means the model abstains. A returned band is at most 3 positions wide."""
    expected = predict_abs_error(row, width_coefficients)
    if expected > abstain_above:
        return None
    center = predict_center(row, center_coefficients)
    width = width_from_error(expected, cut1, cut2)
    low, high = tight_band(center, width)
    if inclusive_width(low, high) > TIGHT_MAX_WIDTH:
        raise ValueError("tight band exceeded width 3")
    return low, high


def baseline_center(row: TightRow) -> int:
    return ABSENT_RANK if row.seen <= 0 else int(round_half_up(row.position))


def summarize(rows: list[tuple[TightRow, tuple[int, int] | None]]) -> dict[str, float]:
    """Coverage and Winkler are over the URLs that received a band. Answered fraction uses every URL."""
    total = len(rows)
    answered = [(row, band) for row, band in rows if band is not None]
    if not answered:
        return {
            "urls": float(total),
            "answered": 0.0,
            "answered_fraction": 0.0,
            "coverage_hits": 0.0,
            "coverage_count": 0.0,
            "coverage": 0.0,
            "contained": 0.0,
            "containment": 0.0,
            "winkler": 0.0,
            "winkler_count": 0.0,
            "mean_winkler": 0.0,
            "width": 0.0,
            "mean_width": 0.0,
        }
    hits = count = contained = winkler_sum = width_sum = 0.0
    for row, band in answered:
        low, high = band
        scored = score_url(
            ObservedBand(row.query, "", row.url_key, min(row.positions), max(row.positions), row.positions, None, None, None),
            low,
            high,
        )
        hits += scored["coverage_hits"]
        count += scored["coverage_count"]
        contained += scored["contained"]
        winkler_sum += scored["winkler"]
        width_sum += scored["width"]
    answered_count = float(len(answered))
    return {
        "urls": float(total),
        "answered": answered_count,
        "answered_fraction": answered_count / total if total else 0.0,
        "coverage_hits": hits,
        "coverage_count": count,
        "coverage": hits / count if count else 0.0,
        "contained": contained,
        "containment": contained / answered_count if answered_count else 0.0,
        "winkler": winkler_sum,
        "winkler_count": count,
        "mean_winkler": winkler_sum / count if count else 0.0,
        "width": width_sum,
        "mean_width": width_sum / answered_count if answered_count else 0.0,
    }


def by_query(rows: list[tuple[TightRow, tuple[int, int] | None]]) -> dict[str, list[tuple[TightRow, tuple[int, int] | None]]]:
    grouped: dict[str, list[tuple[TightRow, tuple[int, int] | None]]] = {}
    for row, band in rows:
        grouped.setdefault(row.query, []).append((row, band))
    return grouped


def choose_width_cuts(
    rows: list[TightRow],
    *,
    center_coefficients: list[float],
    width_coefficients: list[float],
    cuts: list[tuple[float, float]],
) -> dict:
    """Answer every URL. Pick the cuts with the highest coverage, then the lower Winkler score, then the narrower band."""
    if not rows:
        raise ValueError("no rows to choose cuts from")
    best = None
    for cut1, cut2 in cuts:
        issued = [
            (
                row,
                issue_band(
                    row,
                    center_coefficients=center_coefficients,
                    width_coefficients=width_coefficients,
                    cut1=cut1,
                    cut2=cut2,
                    abstain_above=float("inf"),
                ),
            )
            for row in rows
        ]
        totals = summarize(issued)
        key = (totals["coverage"], -totals["mean_winkler"], -totals["mean_width"])
        record = {"cut1": cut1, "cut2": cut2, "abstain_above": None, **totals}
        if best is None or key > best[0]:
            best = (key, record)
    assert best is not None
    return best[1]


def precision_curve(
    rows: list[TightRow],
    *,
    center_coefficients: list[float],
    width_coefficients: list[float],
    cut1: float,
    cut2: float,
    thresholds: list[float],
) -> list[dict[str, float | None]]:
    """Coverage of the issued bands at each answered fraction. The last threshold answers every URL."""
    found = []
    for threshold in thresholds:
        issued = [
            (
                row,
                issue_band(
                    row,
                    center_coefficients=center_coefficients,
                    width_coefficients=width_coefficients,
                    cut1=cut1,
                    cut2=cut2,
                    abstain_above=threshold,
                ),
            )
            for row in rows
        ]
        totals = summarize(issued)
        found.append(
            {
                "abstain_above": None if math.isinf(threshold) else threshold,
                "answered_fraction": totals["answered_fraction"],
                "coverage": totals["coverage"],
                "containment": totals["containment"],
                "mean_winkler": totals["mean_winkler"],
                "mean_width": totals["mean_width"],
                "answered": totals["answered"],
                "urls": totals["urls"],
            }
        )
    return found
