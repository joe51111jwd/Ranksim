"""How much Google's own order moves between two snapshots.

One snapshot cannot estimate that ceiling. This module does not invent a second day.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from aeo.crawl.urls import normalize_url
from aeo.ranksim.groundtruth import SerpLabel


@dataclass
class NoiseReport:
    computed: bool
    n_queries: int
    pairwise_agreement: float | None
    top1_same_rate: float | None
    mean_abs_position_change: float | None
    note: str
    pairwise_with_churn: float | None = None
    mean_overlap: float | None = None
    entered: int = 0
    dropped: int = 0
    n_pairs: int = 0


def noise_ceiling(left: list[SerpLabel], right: list[SerpLabel]) -> NoiseReport:
    """Agreement between two snapshots of the same queries.

    - pairwise_agreement: pairs of URLs present on both days that keep their order.
    - pairwise_with_churn: also counts pairs where one URL left or entered the list, treating
      the missing URL as below every URL that is present (it was not in the collected results).
      Pairs where neither URL is present on a day are skipped.
    - mean_abs_position_change: once per URL present on both days.
    - mean_overlap: shared URLs / larger list size, per query, averaged. entered/dropped count
      URLs present on only one day.
    The snapshots are "identical" only when URL sets and positions all match.
    """
    left_map = _by_query(left)
    right_map = _by_query(right)
    shared = sorted(set(left_map) & set(right_map))
    if not shared:
        return NoiseReport(False, 0, None, None, None, "No query appears in both snapshots. The noise ceiling was not computed.")
    pair_correct = pair_total = 0
    churn_correct = churn_total = 0
    top1_hits = top1_total = 0
    abs_changes: list[float] = []
    overlaps: list[float] = []
    entered = dropped = 0
    usable = 0
    identical = True
    for query in shared:
        a = left_map[query]
        b = right_map[query]
        both = sorted(set(a) & set(b))
        union = sorted(set(a) | set(b))
        entered += len(set(b) - set(a))
        dropped += len(set(a) - set(b))
        overlaps.append(len(both) / max(len(a), len(b)))
        if a != b:
            identical = False
        for url in both:
            abs_changes.append(abs(a[url] - b[url]))
        top1_total += 1
        if min(a, key=a.get) == min(b, key=b.get):
            top1_hits += 1
        if len(both) >= 2:
            usable += 1
        for i in union:
            for j in union:
                if i == j:
                    continue
                order_a = _above(a, i, j)
                if order_a is not True:
                    continue
                order_b = _above(b, i, j)
                if order_b is None:
                    continue
                churn_total += 1
                churn_correct += 1 if order_b else 0
                if i in a and j in a and i in b and j in b:
                    pair_total += 1
                    pair_correct += 1 if order_b else 0
    if churn_total == 0:
        return NoiseReport(
            False,
            0,
            None,
            None,
            None,
            "The snapshots share queries but not enough URLs to compare order. The noise ceiling was not computed.",
        )
    note = (
        "Snapshots match exactly, so this is not a day-to-day noise estimate. Collect the same queries on another day."
        if identical
        else (
            "Agreement between two real snapshots of the same queries. It shows how stable the target is; "
            "it is not model accuracy, and it does not prove a hard ceiling on what a model can reach."
        )
    )
    return NoiseReport(
        True,
        len(shared),
        pair_correct / pair_total if pair_total else None,
        top1_hits / top1_total if top1_total else None,
        sum(abs_changes) / len(abs_changes) if abs_changes else None,
        note,
        pairwise_with_churn=churn_correct / churn_total,
        mean_overlap=sum(overlaps) / len(overlaps),
        entered=entered,
        dropped=dropped,
        n_pairs=pair_total,
    )


def _above(positions: dict[str, float], i: str, j: str) -> bool | None:
    """True if i is above j on this day, False if below, None if neither is listed or they tie."""
    pi, pj = positions.get(i), positions.get(j)
    if pi is None and pj is None:
        return None
    if pi is None:
        return False
    if pj is None:
        return True
    if pi == pj:
        return None
    return pi < pj


def _by_query(rows: list[SerpLabel]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        try:
            url = normalize_url(row.url)
        except ValueError:
            url = row.url
        current = grouped[row.query].get(url)
        if current is None or row.position < current:
            grouped[row.query][url] = row.position
    return grouped
