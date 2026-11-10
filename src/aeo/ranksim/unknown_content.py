"""Unknown page text is not a shared floor.

Fixed before the recovered 2026-11-09 scores are read.

Holdout: query families from assign_families. The domain prior for a query is
the mean Nov 6 relevance (11 minus position) of that host on the other
families only. An unseen host gets the training mean. Nov 9 positions are not
features, weights, or thresholds.

A page with HTML keeps its LambdaMART and BM25 scores.
A page with no HTML gets the mean of the Nov 6 LambdaMART scores, shifted by
the average within-query z-score of the off-page parts that are present.
A missing part adds nothing. It is not a fitted weight and it is not the
missing-page sentinel. Two pages with different off-page evidence do not share
a score. Clicks and on-site behavior are not in this data and are not used.
"""

from __future__ import annotations

import math

from aeo.crawl.urls import domain_of
from aeo.ranksim.features import url_text
from aeo.ranksim.site_history import site_key
from aeo.ranksim.snapshot_protocol import CONTENT_STOP, assign_families, content_tokens
from aeo.textutil import tokenize_for_search

OFFPAGE_PARTS = (
    "domain_prior",
    "url_overlap",
    "majestic_refsubnets",
    "tranco_popularity",
    "cc_pagerank",
    "open_pagerank",
)


def url_overlap(query: str, url: str) -> float:
    wanted = content_tokens(query)
    if not wanted:
        return 0.0
    have = {token for token in tokenize_for_search(url_text(url).lower()) if token not in CONTENT_STOP}
    return len(wanted & have) / len(wanted)


def score_scale(scores: list[float]) -> tuple[float, float]:
    """Mean and population standard deviation of the frozen model's training scores."""
    present = [float(value) for value in scores if value is not None and math.isfinite(float(value))]
    if not present:
        return 0.0, 1.0
    mean = sum(present) / len(present)
    if len(present) < 2:
        return mean, 1.0
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    std = math.sqrt(variance)
    return mean, std if std > 0 else 1.0


def _z(values: list[float | None]) -> list[float | None]:
    present = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(present) < 2:
        return [None if value is None else 0.0 for value in values]
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    std = math.sqrt(variance)
    if std == 0.0:
        return [None if value is None else 0.0 for value in values]
    return [None if value is None else (float(value) - mean) / std for value in values]


def _prior(history: list[tuple[str, str, float]]) -> tuple[dict[str, float], float]:
    totals: dict[str, list[float]] = {}
    for _query, url, position in history:
        totals.setdefault(site_key(url), []).append(11.0 - float(position))
    if not totals:
        return {}, 0.0
    prior = {host: sum(values) / len(values) for host, values in totals.items()}
    flat = [value for values in totals.values() for value in values]
    return prior, sum(flat) / len(flat)


def unknown_content_value(
    rows: list[dict],
    history: list[tuple[str, str, float]],
    *,
    signals: dict[str, dict[str, float | None]] | None = None,
    mean: float = 0.0,
    std: float = 1.0,
) -> None:
    """Fill ordering scores for rows with no HTML. Rows with HTML are left as they are.

    ``rows`` are one query. Each row needs ``query``, ``url``, and ``html``.
    ``history`` is Nov 6 only: (query, url, position). Later positions are not accepted here.
    """
    if not rows:
        return
    families = assign_families(sorted({query for query, _url, _position in history} | {row["query"] for row in rows}))
    tables = signals or {}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["query"], []).append(row)
    for query, group in grouped.items():
        holdout = families.get(query)
        train = [item for item in history if families.get(item[0]) != holdout]
        prior, default = _prior(train)
        parts: dict[str, list[float | None]] = {
            "domain_prior": [prior.get(site_key(row["url"]), default) for row in group],
            "url_overlap": [url_overlap(query, row["url"]) for row in group],
        }
        for name in OFFPAGE_PARTS:
            if name in {"domain_prior", "url_overlap"}:
                continue
            table = tables.get(name) or {}
            parts[name] = []
            for row in group:
                host = site_key(row["url"])
                bare = domain_of(row["url"])
                value = table.get(host)
                if value is None:
                    value = table.get(bare)
                parts[name].append(None if value is None else float(value))
        zipped = {name: _z(values) for name, values in parts.items()}
        for index, row in enumerate(group):
            row["content_missing"] = 0 if row.get("html") else 1
            if row.get("html"):
                continue
            zs = [zipped[name][index] for name in zipped if zipped[name][index] is not None]
            average = 0.0 if not zs else sum(zs) / len(zs)
            mapped = float(mean) + average * float(std)
            row["bm25"] = mapped
            row["lambdamart"] = mapped
            row["unknown_content"] = True
            row["offpage_z"] = average
