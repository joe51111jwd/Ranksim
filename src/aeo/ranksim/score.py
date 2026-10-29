"""Linear scorer. Hand weights are a starting point. Calibrated weights come from SERP pairs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from aeo.ranksim.features import FEATURE_NAMES, PageDoc, normalize_rows, raw_features
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman

# Weights apply to features scaled inside each query. Authority stays at 0
# until a backlink key fills that feature. These are not learned from Google.
HAND_WEIGHTS: dict[str, float] = {
    "bm25_title": 0.22,
    "bm25_h1": 0.16,
    "bm25_headings": 0.08,
    "bm25_body": 0.12,
    "bm25_url": 0.08,
    "embedding": 0.12,
    "intent_match": 0.06,
    "freshness": 0.04,
    "topic_coverage": 0.05,
    "has_table": 0.02,
    "has_faq": 0.02,
    "word_count": 0.02,
    "links": 0.01,
    "speed": 0.00,
    "authority": 0.00,
}


@dataclass(frozen=True)
class ScoredPage:
    url: str
    score: float
    predicted_position: int
    is_ours: bool


@dataclass(frozen=True)
class CalibrationReport:
    spearman: float | None
    ndcg_at_10: float | None
    n_queries: int
    n_pairs: int
    weights: dict[str, float]
    authority_used: bool
    label: str


def dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(weights.get(name, 0.0) * features.get(name, 0.0) for name in FEATURE_NAMES)


def score_pages(
    query: str,
    intent: str,
    pages: list[PageDoc],
    weights: dict[str, float] | None = None,
    today: date | None = None,
) -> list[ScoredPage]:
    if not pages:
        return []
    chosen = weights or HAND_WEIGHTS
    today = today or date.today()
    scaled = normalize_rows(raw_features(query, intent, pages, today))
    scored = [
        (dot(chosen, features), index, pages[index])
        for index, features in enumerate(scaled)
    ]
    scored.sort(key=lambda item: (-item[0], item[1], item[2].url))
    return [
        ScoredPage(
            url=page.url,
            score=score,
            predicted_position=position,
            is_ours=page.is_ours,
        )
        for position, (score, _index, page) in enumerate(scored, start=1)
    ]


def best_our_position(scored: list[ScoredPage]) -> int | None:
    ours = [row.predicted_position for row in scored if row.is_ours]
    return min(ours) if ours else None


def _sigmoid_neg(diff: float) -> float:
    if diff > 30:
        return 0.0
    if diff < -30:
        return -1.0
    return -1.0 / (1.0 + math.exp(diff))


def fit_weights(
    groups: list[tuple[str, str, list[PageDoc], list[int]]],
    *,
    steps: int = 40,
    lr: float = 0.15,
    today: date | None = None,
    authority_used: bool = False,
) -> CalibrationReport:
    """Pairwise fit. groups are (query, intent, pages, actual positions).

    Actual positions are 1-based SERP slots aligned with pages. The fit only
    uses stored results. It does not fill missing SERP rows.
    """
    today = today or date.today()
    weights = dict(HAND_WEIGHTS)
    if not authority_used:
        weights["authority"] = 0.0
    prepared: list[tuple[list[dict[str, float]], list[int]]] = []
    for query, intent, pages, positions in groups:
        if len(pages) != len(positions) or len(pages) < 2:
            continue
        prepared.append((normalize_rows(raw_features(query, intent, pages, today)), positions))
    pair_count = 0
    for features, positions in prepared:
        for left in range(len(positions)):
            for right in range(len(positions)):
                if positions[left] < positions[right]:
                    pair_count += 1
    if pair_count == 0:
        return CalibrationReport(None, None, 0, 0, weights, authority_used, "not_calibrated")

    for _step in range(steps):
        gradient = {name: 0.0 for name in FEATURE_NAMES}
        for features, positions in prepared:
            for left in range(len(positions)):
                for right in range(len(positions)):
                    if positions[left] >= positions[right]:
                        continue
                    diff = dot(weights, features[left]) - dot(weights, features[right])
                    coef = _sigmoid_neg(diff)
                    for name in FEATURE_NAMES:
                        gradient[name] += coef * (features[left][name] - features[right][name])
        for name in FEATURE_NAMES:
            if name == "authority" and not authority_used:
                weights[name] = 0.0
                continue
            weights[name] -= lr * gradient[name] / pair_count

    spearman_values: list[float | None] = []
    ndcg_values: list[float | None] = []
    usable = 0
    for features, positions in prepared:
        scores = [dot(weights, row) for row in features]
        spearman_values.append(spearman(scores, [float(pos) for pos in positions]))
        order = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
        gains = {str(index): float(max(0, 11 - positions[index])) for index in range(len(positions))}
        ranked = [str(index) for index in order]
        ndcg_values.append(ndcg_at_k(ranked, gains, k=10))
        usable += 1
    return CalibrationReport(
        spearman=mean_metric(spearman_values),
        ndcg_at_10=mean_metric(ndcg_values),
        n_queries=usable,
        n_pairs=pair_count,
        weights=weights,
        authority_used=authority_used,
        label="calibrated_on_stored_serp",
    )
