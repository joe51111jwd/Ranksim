"""The grading rule: when a forecast counts as a hit, and the numbers reported beside the hit rate.

The outcome is a REAL clean-reader position. The forecast point is PREDICTED.
This module only compares the two. Rule v1 is the table in the build plan read
literally (docs/plan/IMPLEMENTATION_SPEC.md section 2):

    real position 1 to 10   hit when the point is a position within 2 places
    real position 11 to 20  hit when the point is a position within 5 places
    real position 21 to 50  hit when the point is in 21 to 50
    not in the top 50       hit when the point is NT50

The rule is not symmetric and a point of 15 or 25 covers a whole band, so every
summary also gives the plain band match, a variant with the tolerance taken from
the forecast side, and how many points sit on 15 and 25. A changed rule is a new
version with a new hash. It never regrades old rows in place.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .chain import canonical_json, sha256_text
from .config import MIN_COUNT_PRECISION_RECALL, NT50, TOP_DEPTH
from .stats import cluster_bootstrap_diff, cluster_bootstrap_ratio, design_effect, wilson
from .types import clamp_position

BANDS = ("1-10", "11-20", "21-50", "NT50")
FINE_BANDS = ("1-3", "4-10", "11-20", "21-50", "NT50")
PAGE_ONE_MAX = 10


def band_of(position: int) -> str:
    """The grading band of a position: 1-10, 11-20, 21-50 or NT50."""
    value = clamp_position(position)
    if value <= 10:
        return "1-10"
    if value <= 20:
        return "11-20"
    if value <= TOP_DEPTH:
        return "21-50"
    return "NT50"


def fine_band_of(position: int) -> str:
    """The five bands the placement model predicts: 1-3, 4-10, 11-20, 21-50 or NT50."""
    value = clamp_position(position)
    if value <= 3:
        return "1-3"
    if value <= 10:
        return "4-10"
    return band_of(value)


@dataclass(frozen=True)
class GradingRule:
    """One frozen grading rule. `rule_hash` goes on every grade made with it."""

    version: str = "v1"
    depth: int = TOP_DEPTH
    tolerance_1_10: int = 2
    tolerance_11_20: int = 5

    def rule_hash(self) -> str:
        return sha256_text(canonical_json(asdict(self)))

    def _tolerance(self, position: int) -> int | None:
        if position <= 10:
            return self.tolerance_1_10
        if position <= 20:
            return self.tolerance_11_20
        return None

    def is_hit(self, point: int, actual: int) -> bool:
        """Rule v1: the tolerance comes from the REAL position."""
        forecast = clamp_position(point)
        real = clamp_position(actual)
        if real == NT50:
            return forecast == NT50
        if real > 20:
            return 21 <= forecast <= self.depth
        if forecast == NT50:
            return False
        return abs(forecast - real) <= int(self._tolerance(real))

    def is_hit_forecast_side(self, point: int, actual: int) -> bool:
        """The mirrored rule: the tolerance comes from the forecast. A diagnostic, never the headline."""
        forecast = clamp_position(point)
        real = clamp_position(actual)
        if forecast == NT50:
            return real == NT50
        if forecast > 20:
            return 21 <= real <= self.depth
        if real == NT50:
            return False
        return abs(forecast - real) <= int(self._tolerance(forecast))


RULE_V1 = GradingRule()


@dataclass(frozen=True)
class GradedCell:
    """One graded (site, search): the PREDICTED point and the REAL position."""

    site: str
    search: str
    point: int
    actual: int
    is_brand: bool = False
    difficulty: str | None = None
    label: str | None = None
    source: str | None = None
    stage: str | None = None
    lead_days: int | None = None
    range_low: int | None = None
    range_high: int | None = None
    niche: str | None = None


def _rate(numer: float, denom: float) -> float | None:
    return numer / denom if denom else None


def _ratio_block(numer: Sequence[float], denom: Sequence[float], clusters: Sequence[str]) -> dict[str, Any]:
    total = float(sum(denom))
    got = float(sum(numer))
    low, high = cluster_bootstrap_ratio(numer, denom, clusters)
    w_low, w_high = wilson(got, total)
    return {
        "count": int(total),
        "right": int(got),
        "rate": _rate(got, total),
        "cluster_interval": [low, high],
        "wilson_interval_if_independent": [w_low, w_high],
    }


def summarize(cells: Sequence[GradedCell], rule: GradingRule = RULE_V1, *, cluster_by: str = "site") -> dict[str, Any]:
    """Every number reported for one set of graded forecasts. Brand cells are the caller's to split off."""
    if cluster_by not in ("site", "search"):
        raise ValueError("cluster_by must be 'site' or 'search'")
    clusters = [getattr(cell, cluster_by) for cell in cells]
    ones = [1.0] * len(cells)
    hits = [1.0 if rule.is_hit(c.point, c.actual) else 0.0 for c in cells]
    mirrored = [1.0 if rule.is_hit_forecast_side(c.point, c.actual) else 0.0 for c in cells]
    both = [a * b for a, b in zip(hits, mirrored)]
    band_match = [1.0 if band_of(c.point) == band_of(c.actual) else 0.0 for c in cells]

    out: dict[str, Any] = {
        "grading_rule": rule.version,
        "grading_rule_hash": rule.rule_hash(),
        "cluster_by": cluster_by,
        "clusters": len(set(clusters)),
        "hit": _ratio_block(hits, ones, clusters),
        "design_effect": design_effect(hits, clusters),
        "band_match": _ratio_block(band_match, ones, clusters),
        "hit_forecast_side_rule": _ratio_block(mirrored, ones, clusters),
        "hit_under_both_rules": _ratio_block(both, ones, clusters),
    }

    by_band: dict[str, Any] = {}
    for band in BANDS:
        index = [i for i, c in enumerate(cells) if band_of(c.actual) == band]
        got = sum(hits[i] for i in index)
        by_band[band] = {
            "count": len(index),
            "right": int(got),
            "rate": _rate(got, len(index)),
            "share_of_outcomes": _rate(len(index), len(cells)),
        }
    out["by_real_band"] = by_band

    confusion = {forecast: {real: 0 for real in BANDS} for forecast in BANDS}
    for cell in cells:
        confusion[band_of(cell.point)][band_of(cell.actual)] += 1
    out["confusion_forecast_by_real"] = confusion

    called = [1.0 if clamp_position(c.point) <= PAGE_ONE_MAX else 0.0 for c in cells]
    landed = [1.0 if clamp_position(c.actual) <= PAGE_ONE_MAX else 0.0 for c in cells]
    true_pos = [a * b for a, b in zip(called, landed)]
    precision = _ratio_block(true_pos, called, clusters)
    recall = _ratio_block(true_pos, landed, clusters)
    precision["measurable"] = precision["count"] >= MIN_COUNT_PRECISION_RECALL
    recall["measurable"] = recall["count"] >= MIN_COUNT_PRECISION_RECALL
    out["page_one"] = {
        "calls": int(sum(called)),
        "outcomes": int(sum(landed)),
        "precision": precision,
        "recall": recall,
        "minimum_count": MIN_COUNT_PRECISION_RECALL,
    }

    in_11_20 = [c for c in cells if band_of(c.point) == "11-20"]
    in_21_50 = [c for c in cells if band_of(c.point) == "21-50"]
    out["point_pileup"] = {
        "share_of_11_20_points_at_15": _rate(sum(1 for c in in_11_20 if c.point == 15), len(in_11_20)),
        "share_of_21_50_points_at_25": _rate(sum(1 for c in in_21_50 if c.point == 25), len(in_21_50)),
    }

    ranged = [c for c in cells if c.range_low is not None and c.range_high is not None]
    inside = sum(1 for c in ranged if int(c.range_low) <= clamp_position(c.actual) <= int(c.range_high))
    out["range"] = {"count": len(ranged), "covered": inside, "coverage": _rate(inside, len(ranged))}

    labelled = [c for c in cells if c.label]
    if labelled:
        per_label: dict[str, Any] = {}
        for name in sorted({c.label for c in labelled}):
            index = [i for i, c in enumerate(cells) if c.label == name]
            got = sum(hits[i] for i in index)
            per_label[str(name)] = {"count": len(index), "right": int(got), "rate": _rate(got, len(index))}
        not_contested = sum(1 for c in labelled if c.label != "contested")
        out["by_label"] = per_label
        out["coverage_not_contested"] = _rate(not_contested, len(labelled))
    return out


def always_nt50(cells: Sequence[GradedCell]) -> list[int]:
    """The empty tool: it says "not in the top 50" every time."""
    return [NT50] * len(cells)


def compare_to_baseline(
    cells: Sequence[GradedCell],
    baseline_points: Sequence[int],
    rule: GradingRule = RULE_V1,
    *,
    cluster_by: str = "site",
) -> dict[str, Any]:
    """Model hit rate minus a baseline's hit rate on the SAME forecasts, with a cluster interval."""
    if len(cells) != len(baseline_points):
        raise ValueError("one baseline point per cell is required")
    clusters = [getattr(cell, cluster_by) for cell in cells]
    model = [1.0 if rule.is_hit(c.point, c.actual) else 0.0 for c in cells]
    base = [1.0 if rule.is_hit(p, c.actual) else 0.0 for p, c in zip(baseline_points, cells)]
    diff, low, high = cluster_bootstrap_diff(model, base, clusters)
    model_rate = _rate(sum(model), len(model))
    base_rate = _rate(sum(base), len(base))
    return {
        "count": len(cells),
        "model_rate": model_rate,
        "baseline_rate": base_rate,
        "difference": diff,
        "difference_interval": [low, high],
        "skill": skill(model_rate, base_rate),
    }


def skill(model_rate: float | None, baseline_rate: float | None) -> float | None:
    """(model - baseline) / (1 - baseline): the share of the baseline's misses the model removes."""
    if model_rate is None or baseline_rate is None or baseline_rate >= 1.0:
        return None
    return (model_rate - baseline_rate) / (1.0 - baseline_rate)


def headline(
    cells: Sequence[GradedCell],
    baselines: dict[str, Sequence[int]] | None = None,
    rule: GradingRule = RULE_V1,
    *,
    cluster_by: str = "site",
) -> dict[str, Any]:
    """The full report for one set of graded forecasts.

    Brand searches are left out of the headline and summarized apart. Each
    baseline is a list of points aligned with `cells`. "always_nt50" is always
    added. The best baseline is the one with the highest hit rate on these cells.
    """
    keep = [i for i, c in enumerate(cells) if not c.is_brand]
    main = [cells[i] for i in keep]
    brand = [c for c in cells if c.is_brand]
    table: dict[str, Any] = {}
    named = dict(baselines or {})
    if "always_nt50" in named:
        raise ValueError("the baseline name 'always_nt50' is reserved: it is always computed here")
    named["always_nt50"] = always_nt50(cells)
    for name, points in named.items():
        if len(points) != len(cells):
            raise ValueError(f"baseline {name!r} must have one point per cell")
        table[name] = compare_to_baseline(main, [points[i] for i in keep], rule, cluster_by=cluster_by)
    # Ties go to the name that sorts first, so the choice does not depend on argument order.
    best = min(table, key=lambda name: (-(table[name]["baseline_rate"] or 0.0), name)) if main else None
    return {
        "forecasts": len(main),
        "brand_forecasts_shown_apart": len(brand),
        "summary": summarize(main, rule, cluster_by=cluster_by),
        "baselines": table,
        "best_baseline": best,
        "skill_over_best_baseline": table[best]["skill"] if best else None,
        "brand": summarize(brand, rule, cluster_by=cluster_by) if brand else None,
    }
