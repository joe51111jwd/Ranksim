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


def noise_ceiling(left: list[SerpLabel], right: list[SerpLabel]) -> NoiseReport:
    left_map = _by_query(left)
    right_map = _by_query(right)
    shared = sorted(set(left_map) & set(right_map))
    if not shared:
        return NoiseReport(False, 0, None, None, None, "No query appears in both snapshots. The noise ceiling was not computed.")
    pair_correct = 0
    pair_total = 0
    top1_hits = 0
    top1_total = 0
    abs_changes: list[float] = []
    usable = 0
    for query in shared:
        a = left_map[query]
        b = right_map[query]
        urls = sorted(set(a) & set(b))
        if len(urls) < 2:
            continue
        usable += 1
        for i in range(len(urls)):
            for j in range(len(urls)):
                if a[urls[i]] >= a[urls[j]]:
                    continue
                pair_total += 1
                if b[urls[i]] < b[urls[j]]:
                    pair_correct += 1
                abs_changes.append(abs(a[urls[i]] - b[urls[i]]))
        top1_total += 1
        best_a = min(a, key=a.get)
        best_b = min(b, key=b.get)
        if best_a == best_b:
            top1_hits += 1
    if usable == 0 or pair_total == 0:
        return NoiseReport(
            False,
            0,
            None,
            None,
            None,
            "The snapshots share queries but not enough URLs to compare order. The noise ceiling was not computed.",
        )
    identical = pair_correct == pair_total and top1_hits == top1_total and all(change == 0 for change in abs_changes)
    note = (
        "Snapshots match exactly, so this is not a day-to-day noise estimate. Collect the same queries on another day."
        if identical
        else "Agreement between two real snapshots of the same queries. This is a ceiling, not model accuracy."
    )
    return NoiseReport(
        True,
        usable,
        pair_correct / pair_total,
        top1_hits / top1_total if top1_total else None,
        sum(abs_changes) / len(abs_changes) if abs_changes else None,
        note,
    )


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
