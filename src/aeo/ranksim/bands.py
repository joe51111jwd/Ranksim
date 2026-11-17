"""Position bands. The label is the set of positions a URL showed, not one spot.

A pull that omits a URL contributes rank 11, or a deep rank when that rank is 11
or worse. Predictions are PREDICTED. Width is chosen on earlier dates only.
The Winkler score uses inclusive width, so a one-position band that hits scores 1,
and a miss adds (2 / alpha) times the distance outside the band. Alpha is fixed.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeo.ranksim.consensus import ABSENT_RANK, ConsensusUrl, consensus_from_pulls, pull_rank

ALPHA = 0.2
BAND_FLOOR = 1
BAND_CEIL = 30
MAX_HALF_WIDTH = 10


@dataclass(frozen=True)
class ObservedBand:
    query: str
    url: str
    url_key: str
    low: int
    high: int
    positions: tuple[int, ...]
    previous_low: int | None
    previous_high: int | None
    previous_consensus: int | None


def observed_band(row: ConsensusUrl, previous: tuple[int, int, int] | None) -> ObservedBand:
    """`previous` is (low, high, consensus) from the day before, or None when the URL is new."""
    if not row.pull_positions:
        raise ValueError("a band needs at least one pull")
    low = min(row.pull_positions)
    high = max(row.pull_positions)
    if previous is None:
        return ObservedBand(row.query, row.url, row.url_key, low, high, row.pull_positions, None, None, None)
    prev_low, prev_high, consensus = previous
    return ObservedBand(
        row.query, row.url, row.url_key, low, high, row.pull_positions, prev_low, prev_high, consensus
    )


def clip_band(center: int, low_offset: int, high_offset: int) -> tuple[int, int]:
    if high_offset < low_offset:
        raise ValueError("band offsets are reversed")
    low = max(BAND_FLOOR, int(center) + low_offset)
    high = min(BAND_CEIL, int(center) + high_offset)
    if low > high:
        point = min(BAND_CEIL, max(BAND_FLOOR, int(center)))
        return point, point
    return low, high


def inclusive_width(low: int, high: int) -> int:
    return int(high) - int(low) + 1


def winkler(low: int, high: int, position: int, *, alpha: float = ALPHA) -> float:
    """Lower is better. Width is the inclusive count of positions in the band."""
    if alpha <= 0 or alpha >= 1:
        raise ValueError("alpha must sit between 0 and 1")
    width = inclusive_width(low, high)
    if position < low:
        return width + (2.0 / alpha) * (low - position)
    if position > high:
        return width + (2.0 / alpha) * (position - high)
    return float(width)


def uniform_chance(low: int, high: int, *, top: int) -> float:
    """Share of the band that lands in positions 1..top. The mass is uniform on the integers."""
    width = inclusive_width(low, high)
    inside = sum(1 for position in range(low, high + 1) if 1 <= position <= top)
    return inside / width


def _covered(low: int, high: int, position: int) -> bool:
    return low <= position <= high


def score_url(band: ObservedBand, low: int, high: int, *, alpha: float = ALPHA) -> dict[str, float]:
    hits = sum(1 for position in band.positions if _covered(low, high, position))
    count = float(len(band.positions))
    contained = 1.0 if band.low >= low and band.high <= high else 0.0
    score = sum(winkler(low, high, position, alpha=alpha) for position in band.positions)
    number_one_error = 0.0
    top3_error = 0.0
    chance_one = uniform_chance(low, high, top=1)
    chance_top3 = uniform_chance(low, high, top=3)
    for position in band.positions:
        number_one_error += (chance_one - (1.0 if position == 1 else 0.0)) ** 2
        top3_error += (chance_top3 - (1.0 if position <= 3 else 0.0)) ** 2
    return {
        "coverage_hits": float(hits),
        "coverage_count": count,
        "contained": contained,
        "urls": 1.0,
        "width": float(inclusive_width(low, high)),
        "winkler": score,
        "winkler_count": count,
        "brier_one": number_one_error,
        "brier_top3": top3_error,
        "brier_count": count,
        "chance_one": chance_one * count,
        "chance_top3": chance_top3 * count,
        "observed_one": float(sum(1 for position in band.positions if position == 1)),
        "observed_top3": float(sum(1 for position in band.positions if position <= 3)),
    }


def pool_scores(rows: list[dict[str, float]]) -> dict[str, float]:
    totals = {
        "coverage_hits": 0.0,
        "coverage_count": 0.0,
        "contained": 0.0,
        "urls": 0.0,
        "width": 0.0,
        "winkler": 0.0,
        "winkler_count": 0.0,
        "brier_one": 0.0,
        "brier_top3": 0.0,
        "brier_count": 0.0,
        "chance_one": 0.0,
        "chance_top3": 0.0,
        "observed_one": 0.0,
        "observed_top3": 0.0,
    }
    for row in rows:
        for key in totals:
            if key == "urls":
                continue
            totals[key] += row[key]
        totals["urls"] += 1.0
    return totals


def coverage(totals: dict[str, float]) -> float:
    if totals["coverage_count"] == 0:
        return 0.0
    return totals["coverage_hits"] / totals["coverage_count"]


def containment(totals: dict[str, float]) -> float:
    if totals["urls"] == 0:
        return 0.0
    return totals["contained"] / totals["urls"]


def mean_width(totals: dict[str, float]) -> float:
    if totals["urls"] == 0:
        return 0.0
    return totals["width"] / totals["urls"]


def mean_winkler(totals: dict[str, float]) -> float:
    if totals["winkler_count"] == 0:
        return 0.0
    return totals["winkler"] / totals["winkler_count"]


def offset_family() -> list[tuple[int, int]]:
    """A small menu. Symmetric bands, bands that only reach down, and bands that only reach up."""
    found = [(0, 0)]
    for half in range(1, MAX_HALF_WIDTH + 1):
        found.append((-half, half))
        found.append((-half, 0))
        found.append((0, half))
    return found


def _apply(center: int, low_offset: int, high_offset: int) -> tuple[int, int]:
    return clip_band(center, low_offset, high_offset)


def choose_offsets(pairs: list[tuple[int, tuple[int, ...]]], *, alpha: float = ALPHA) -> dict:
    """Offsets with the lowest mean Winkler score. Ties prefer higher coverage, then a narrower band."""
    if not pairs:
        raise ValueError("no calibration pairs")
    best = None
    for low_offset, high_offset in offset_family():
        rows = []
        for center, positions in pairs:
            low, high = _apply(center, low_offset, high_offset)
            dummy = ObservedBand("", "", "", min(positions), max(positions), positions, None, None, None)
            rows.append(score_url(dummy, low, high, alpha=alpha))
        totals = pool_scores(rows)
        key = (
            -mean_winkler(totals),
            coverage(totals),
            -mean_width(totals),
            -(abs(low_offset) + abs(high_offset)),
        )
        record = {
            "low_offset": low_offset,
            "high_offset": high_offset,
            "calibration_winkler": mean_winkler(totals),
            "calibration_coverage": coverage(totals),
            "calibration_mean_width": mean_width(totals),
            "calibration_containment": containment(totals),
        }
        if best is None or key > best[0]:
            best = (key, record)
    assert best is not None
    return best[1]


def choose_k(pairs: list[tuple[int, tuple[int, ...]]], *, alpha: float = ALPHA) -> dict:
    """Symmetric half-width around yesterday's position. Same rule as choose_offsets, restricted to ±k."""
    best = None
    for half in range(0, MAX_HALF_WIDTH + 1):
        rows = []
        for center, positions in pairs:
            low, high = _apply(center, -half, half)
            dummy = ObservedBand("", "", "", min(positions), max(positions), positions, None, None, None)
            rows.append(score_url(dummy, low, high, alpha=alpha))
        totals = pool_scores(rows)
        key = (-mean_winkler(totals), coverage(totals), -mean_width(totals), -half)
        record = {
            "k": half,
            "low_offset": -half,
            "high_offset": half,
            "calibration_winkler": mean_winkler(totals),
            "calibration_coverage": coverage(totals),
            "calibration_mean_width": mean_width(totals),
        }
        if best is None or key > best[0]:
            best = (key, record)
    assert best is not None
    return best[1]


def choose_band_rule(pairs: list[tuple[int, tuple[int, ...], str]], *, alpha: float = ALPHA) -> dict:
    """One offset pair, or separate survivor and new pairs, if the split lowers the calibration score."""
    global_rule = choose_offsets([(center, positions) for center, positions, _bucket in pairs], alpha=alpha)
    by_bucket = {}
    for bucket in ("surviving", "new"):
        subset = [(center, positions) for center, positions, name in pairs if name == bucket]
        by_bucket[bucket] = choose_offsets(subset, alpha=alpha) if subset else dict(global_rule)
    rows = []
    for center, positions, bucket in pairs:
        rule = by_bucket[bucket]
        low, high = _apply(center, rule["low_offset"], rule["high_offset"])
        dummy = ObservedBand("", "", "", min(positions), max(positions), positions, None, None, None)
        rows.append(score_url(dummy, low, high, alpha=alpha))
    totals = pool_scores(rows)
    use_split = mean_winkler(totals) < global_rule["calibration_winkler"] - 1e-12
    return {
        "alpha": alpha,
        "by_bucket": use_split,
        "global": global_rule,
        "surviving": by_bucket["surviving"],
        "new": by_bucket["new"],
        "calibration_winkler": mean_winkler(totals) if use_split else global_rule["calibration_winkler"],
        "calibration_coverage": coverage(totals) if use_split else global_rule["calibration_coverage"],
        "calibration_mean_width": mean_width(totals) if use_split else global_rule["calibration_mean_width"],
    }


def offsets_for(rule: dict, bucket: str) -> tuple[int, int]:
    chosen = rule[bucket] if rule.get("by_bucket") else rule["global"]
    return int(chosen["low_offset"]), int(chosen["high_offset"])


def persistence_band(band: ObservedBand, k: int) -> tuple[int, int]:
    center = ABSENT_RANK if band.previous_consensus is None else int(band.previous_consensus)
    return clip_band(center, -k, k)


def last_observed_band(band: ObservedBand) -> tuple[int, int]:
    if band.previous_low is None or band.previous_high is None:
        return ABSENT_RANK, ABSENT_RANK
    return clip_band(band.previous_low, 0, band.previous_high - band.previous_low)


def model_band(center: int, rule: dict, bucket: str) -> tuple[int, int]:
    low_offset, high_offset = offsets_for(rule, bucket)
    return clip_band(center, low_offset, high_offset)


def collect_bands(
    query: str,
    pulls: list[dict[str, tuple[str, int]]],
    deep: dict[str, int],
    previous: dict[str, tuple[str, int, int, int]],
) -> list[ObservedBand]:
    """Today's union plus yesterday's URLs that dropped out.

    `previous` maps a URL key to (display url, low, high, consensus). A URL missing
    from a pull is 11, or its deep rank when that rank is 11 or worse.
    """
    rows = consensus_from_pulls(query, pulls, deep)
    seen = {row.url_key for row in rows}
    found = []
    for row in rows:
        prior = previous.get(row.url_key)
        previous_band = None if prior is None else (prior[1], prior[2], prior[3])
        found.append(observed_band(row, previous_band))
    for key, (url, low, high, consensus) in previous.items():
        if key in seen:
            continue
        positions = []
        for pull in pulls:
            rank, _shown = pull_rank(key, {item: pos for item, (_url, pos) in pull.items()}, deep)
            positions.append(rank)
        placeholder = ConsensusUrl(query, url, key, consensus, tuple(positions), ())
        found.append(observed_band(placeholder, (low, high, consensus)))
    return found


def previous_records(
    pulls: list[dict[str, dict[str, tuple[str, int]]]],
    deep: dict[str, dict[str, int]],
    queries: list[str],
) -> dict[str, dict[str, tuple[str, int, int, int]]]:
    """Per query, yesterday's URL to (url, low, high, consensus). One pull makes low and high equal."""
    found: dict[str, dict[str, tuple[str, int, int, int]]] = {}
    for query in queries:
        query_pulls = [pull[query] for pull in pulls if query in pull]
        if not query_pulls:
            found[query] = {}
            continue
        rows = consensus_from_pulls(query, query_pulls, deep.get(query, {}))
        found[query] = {row.url_key: (row.url, min(row.pull_positions), max(row.pull_positions), row.consensus) for row in rows}
    return found
