"""Scores for a page that has no saved history.

Three scorers are fixed here, before the 2026-11-09 labels are used:

1. domain_prior. Mean Nov 6 relevance of the host on other query families.
   An unseen host gets that training mean.
2. content_query. Within-query z-score of URL overlap with the query, plus the
   z-score of BM25 when the page has text. Missing text adds nothing. It is not
   the missing-page sentinel.
3. calibrated_blend. Same two z-scores, plus a domain weight chosen on Nov 6
   family-out order only. The content weight stays 1. Nov 9 positions are not
   a feature, a label, or a weight.

The adoption gates below are part of the protocol. Do not move them after the
Nov 9 scores have been computed.

A pasted draft always has text. ``adopt_draft_path(..., content=True)`` is the
gate for that path. The gate that still counts pages with no HTML is the
diagnosis of the published 0.293 figure. It does not by itself change the
pasted-draft ranker.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aeo.crawl.urls import domain_of, normalize_url
from aeo.ranksim.features import url_text
from aeo.ranksim.forward_eval import (
    FORWARD_DAY,
    MISSING_CONTENT_SCORE,
    PREDICTION_CREATED_AT,
)
from aeo.ranksim.lambdamart import relevance
from aeo.ranksim.ledger import Call, record_outcome, record_prediction, score_ledger
from aeo.ranksim.metrics import mean_metric, ndcg_at_k, spearman
from aeo.ranksim.snapshot_protocol import CONTENT_STOP, assign_families, content_tokens
from aeo.textutil import tokenize_for_search

DRAFT_ADOPTION_MARGIN = 0.05
BOOTSTRAP_SEED = 20261110
BOOTSTRAP_DRAWS = 2000
BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
NEW_SCORERS = ("domain_prior", "content_query", "calibrated_blend")
SLICE_NAMES = (
    "all",
    "draft_vs_draft",
    "draft_vs_draft_with_content",
    "draft_vs_returning",
    "draft_vs_returning_with_content",
)
GAP_TOLERANCE = 1e-3


@dataclass(frozen=True)
class ObservedRow:
    """One Nov 6 result. ``position`` is that day's rank, never the later one."""

    query: str
    url: str
    position: float


@dataclass(frozen=True)
class ScoredCandidate:
    """One later candidate. ``label_position`` is the outcome and is not a feature."""

    query: str
    url: str
    label_position: float
    content: float | None
    html_source: str
    entrant: bool


@dataclass
class ForwardLoad:
    candidates: list[ScoredCandidate]
    history: list[ObservedRow]
    baselines: dict[str, dict[tuple[str, str], float]]
    gap_mismatches: int
    forward_csv_mismatches: int


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def _query(text: str) -> str:
    return " ".join(text.split())


def is_draft(row: ScoredCandidate) -> bool:
    """No position on this query in the earlier snapshot, and no stored page.

    That is the forward-eval stand-in for a draft. A pasted draft always has
    text, so ``html_source == "missing"`` is the case the product path does not have.
    """
    return bool(row.entrant) and row.html_source != "baseline"


def url_query_overlap(query: str, url: str) -> float:
    """Share of the query's content words that appear in the URL."""
    wanted = content_tokens(query)
    if not wanted:
        return 0.0
    have = {token for token in tokenize_for_search(url_text(url).lower()) if token not in CONTENT_STOP}
    return len(wanted & have) / len(wanted)


def population_z(values: list[float | None]) -> list[float]:
    """Z-score of the present numbers. A missing number adds no push."""
    present = [float(value) for value in values if value is not None]
    if len(present) < 2:
        return [0.0 for _ in values]
    mean = sum(present) / len(present)
    variance = sum((value - mean) ** 2 for value in present) / len(present)
    std = math.sqrt(variance)
    if std == 0.0:
        return [0.0 for _ in values]
    return [0.0 if value is None else (float(value) - mean) / std for value in values]


def _fit_prior(rows: list[ObservedRow]) -> tuple[dict[str, float], float]:
    totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        totals[domain_of(row.url)].append(11.0 - float(row.position))
    if not totals:
        return {}, 0.0
    prior = {domain: sum(values) / len(values) for domain, values in totals.items()}
    flat = [value for values in totals.values() for value in values]
    return prior, sum(flat) / len(flat)


def domain_prior_scores(
    candidates: list[ScoredCandidate],
    history: list[ObservedRow],
    holdout_queries: set[str],
) -> list[float]:
    """Host mean from history outside the holdout. Later labels are not read."""
    train = [row for row in history if row.query not in holdout_queries]
    prior, default = _fit_prior(train)
    return [prior.get(domain_of(row.url), default) for row in candidates]


def content_query_scores(candidates: list[ScoredCandidate]) -> list[float]:
    """URL overlap plus BM25, z-scored inside the query. Missing BM25 adds 0."""
    scores = [0.0] * len(candidates)
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(candidates):
        grouped[row.query].append(index)
    for indexes in grouped.values():
        rows = [candidates[index] for index in indexes]
        overlap = population_z([url_query_overlap(row.query, row.url) for row in rows])
        content = population_z([row.content for row in rows])
        for offset, index in enumerate(indexes):
            scores[index] = overlap[offset] + content[offset]
    return scores


def _history_blend_scores(test_rows: list[ObservedRow], train_rows: list[ObservedRow], weight: float) -> list[float]:
    prior, default = _fit_prior(train_rows)
    scores = [0.0] * len(test_rows)
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(test_rows):
        grouped[row.query].append(index)
    for indexes in grouped.values():
        rows = [test_rows[index] for index in indexes]
        domain_z = population_z([prior.get(domain_of(row.url), default) for row in rows])
        overlap_z = population_z([url_query_overlap(row.query, row.url) for row in rows])
        for offset, index in enumerate(indexes):
            scores[index] = weight * domain_z[offset] + (1.0 - weight) * overlap_z[offset]
    return scores


def _pairs_from_positions(rows: list[ObservedRow], scores: list[float]) -> tuple[int, int, int]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row, score in zip(rows, scores):
        grouped[row.query].append((float(row.position), float(score)))
    correct = wrong = ties = 0
    for pairs in grouped.values():
        for left in range(len(pairs)):
            for right in range(len(pairs)):
                if pairs[left][0] >= pairs[right][0]:
                    continue
                if pairs[left][1] > pairs[right][1]:
                    correct += 1
                elif pairs[left][1] < pairs[right][1]:
                    wrong += 1
                else:
                    ties += 1
    return correct, wrong, ties


def choose_blend_weight(history: list[ObservedRow]) -> float:
    """Pick the domain weight on Nov 6 family-out order. Ties prefer the weight nearer 0.5."""
    queries = sorted({row.query for row in history})
    if len(queries) < 2:
        return 0.5
    families = assign_families(queries)
    grouped: dict[str, list[ObservedRow]] = defaultdict(list)
    for row in history:
        grouped[families[row.query]].append(row)
    if len(grouped) < 2:
        return 0.5
    ranked: list[tuple[float, float, float]] = []
    for weight in BLEND_WEIGHTS:
        correct = wrong = ties = 0
        for family, rows in grouped.items():
            train = [row for other, items in grouped.items() if other != family for row in items]
            scores = _history_blend_scores(rows, train, weight)
            got, bad, tied = _pairs_from_positions(rows, scores)
            correct += got
            wrong += bad
            ties += tied
        total = correct + wrong + ties
        pairwise = -1.0 if total == 0 else correct / total
        ranked.append((pairwise, abs(weight - 0.5), weight))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    return ranked[0][2]


def _candidate_blend_scores(
    candidates: list[ScoredCandidate],
    history: list[ObservedRow],
    holdout_queries: set[str],
    weight: float,
) -> list[float]:
    train = [row for row in history if row.query not in holdout_queries]
    prior, default = _fit_prior(train)
    scores = [0.0] * len(candidates)
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(candidates):
        grouped[row.query].append(index)
    for indexes in grouped.values():
        rows = [candidates[index] for index in indexes]
        domain_z = population_z([prior.get(domain_of(row.url), default) for row in rows])
        overlap_z = population_z([url_query_overlap(row.query, row.url) for row in rows])
        content_z = population_z([row.content for row in rows])
        for offset, index in enumerate(indexes):
            scores[index] = weight * domain_z[offset] + (1.0 - weight) * overlap_z[offset] + content_z[offset]
    return scores


def _family_queries(names: list[str]) -> dict[str, set[str]]:
    if not names:
        return {}
    labels = assign_families(names)
    grouped: dict[str, set[str]] = defaultdict(set)
    for query, family in labels.items():
        grouped[family].add(query)
    return grouped


def _pair_in_slice(left: ScoredCandidate, right: ScoredCandidate, mode: str) -> bool:
    if mode == "all":
        return True
    left_draft = is_draft(left)
    right_draft = is_draft(right)
    left_new = bool(left.entrant)
    right_new = bool(right.entrant)
    if mode == "draft_vs_draft":
        return left_draft and right_draft
    if mode == "draft_vs_draft_with_content":
        return (
            left_draft
            and right_draft
            and left.html_source != "missing"
            and right.html_source != "missing"
        )
    if mode == "draft_vs_returning":
        return (left_draft != right_draft) and ((not left_new) or (not right_new))
    if mode == "draft_vs_returning_with_content":
        drafts = [row for row in (left, right) if is_draft(row)]
        if len(drafts) != 1 or drafts[0].html_source == "missing":
            return False
        return (not left_new) or (not right_new)
    raise ValueError(mode)


def _slice_counts(
    candidates: list[ScoredCandidate],
    score_of: dict[tuple[str, str], float],
    mode: str,
) -> dict[str, tuple[int, int, int]]:
    grouped: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in candidates:
        grouped[row.query].append(row)
    found: dict[str, tuple[int, int, int]] = {}
    for query, rows in grouped.items():
        correct = wrong = ties = 0
        for left in range(len(rows)):
            for right in range(len(rows)):
                if rows[left].label_position >= rows[right].label_position:
                    continue
                if not _pair_in_slice(rows[left], rows[right], mode):
                    continue
                better, worse = rows[left], rows[right]
                left_score = score_of[(query, better.url)]
                right_score = score_of[(query, worse.url)]
                if left_score > right_score:
                    correct += 1
                elif left_score < right_score:
                    wrong += 1
                else:
                    ties += 1
        if correct + wrong + ties:
            found[query] = (correct, wrong, ties)
    return found


def _pooled(per_query: dict[str, tuple[int, int, int]]) -> dict:
    correct = sum(item[0] for item in per_query.values())
    wrong = sum(item[1] for item in per_query.values())
    ties = sum(item[2] for item in per_query.values())
    total = correct + wrong + ties
    return {
        "correct": correct,
        "wrong": wrong,
        "ties": ties,
        "pairs": total,
        "queries": len(per_query),
        "pairwise": None if total == 0 else correct / total,
    }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _bootstrap_rate(per_query: dict[str, tuple[int, int, int]], draws: int, rng: random.Random) -> list[float | None]:
    names = sorted(per_query)
    if not names:
        return [None, None]
    samples: list[float] = []
    for _ in range(draws):
        picked = [names[rng.randrange(len(names))] for _ in names]
        correct = sum(per_query[name][0] for name in picked)
        total = sum(sum(per_query[name]) for name in picked)
        if total:
            samples.append(correct / total)
    if not samples:
        return [None, None]
    samples.sort()
    return [_percentile(samples, 0.025), _percentile(samples, 0.975)]


def _bootstrap_delta(
    candidate: dict[str, tuple[int, int, int]],
    baseline: dict[str, tuple[int, int, int]],
    draws: int,
    rng: random.Random,
) -> list[float | None]:
    names = sorted(set(candidate) & set(baseline))
    if not names:
        return [None, None]
    samples: list[float] = []
    for _ in range(draws):
        picked = [names[rng.randrange(len(names))] for _ in names]
        left_correct = sum(candidate[name][0] for name in picked)
        left_total = sum(sum(candidate[name]) for name in picked)
        right_correct = sum(baseline[name][0] for name in picked)
        right_total = sum(sum(baseline[name]) for name in picked)
        if left_total and right_total:
            samples.append(left_correct / left_total - right_correct / right_total)
    if not samples:
        return [None, None]
    samples.sort()
    return [_percentile(samples, 0.025), _percentile(samples, 0.975)]


def _bootstrap_mean(values: dict[str, float], draws: int, rng: random.Random) -> list[float | None]:
    names = sorted(values)
    if not names:
        return [None, None]
    samples: list[float] = []
    for _ in range(draws):
        picked = [names[rng.randrange(len(names))] for _ in names]
        samples.append(sum(values[name] for name in picked) / len(picked))
    samples.sort()
    return [_percentile(samples, 0.025), _percentile(samples, 0.975)]


def _list_metrics(candidates: list[ScoredCandidate], score_of: dict[tuple[str, str], float]) -> tuple[dict[str, float], dict[str, float]]:
    grouped: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in candidates:
        grouped[row.query].append(row)
    ndcgs: dict[str, float] = {}
    rhos: dict[str, float] = {}
    for query, rows in grouped.items():
        if len(rows) < 2:
            continue
        scores = [score_of[(query, row.url)] for row in rows]
        order = sorted(range(len(rows)), key=lambda index: (-scores[index], rows[index].url))
        gains = {str(index): relevance(rows[index].label_position) for index in range(len(rows))}
        ndcg = ndcg_at_k([str(index) for index in order], gains, k=10)
        rho = spearman(scores, [row.label_position for row in rows])
        if ndcg is not None:
            ndcgs[query] = ndcg
        if rho is not None:
            rhos[query] = rho
    return ndcgs, rhos


def _score_table(candidates: list[ScoredCandidate], scores: list[float]) -> dict[tuple[str, str], float]:
    return {(row.query, row.url): float(score) for row, score in zip(candidates, scores)}


def _metric_block(
    candidates: list[ScoredCandidate],
    score_of: dict[tuple[str, str], float],
    lambdamart: dict[tuple[str, str], float] | None,
    draws: int,
    seed: int,
) -> dict:
    rng = random.Random(seed)
    block: dict = {}
    lambdamart_counts = None
    if lambdamart is not None:
        lambdamart_counts = {mode: _slice_counts(candidates, lambdamart, mode) for mode in SLICE_NAMES}
    for mode in SLICE_NAMES:
        counts = _slice_counts(candidates, score_of, mode)
        pooled = _pooled(counts)
        pooled["ci"] = _bootstrap_rate(counts, draws, rng)
        if lambdamart_counts is None:
            pooled["delta_vs_lambdamart"] = None
            pooled["delta_vs_lambdamart_ci"] = [None, None]
        else:
            base = _pooled(lambdamart_counts[mode])["pairwise"]
            pooled["delta_vs_lambdamart"] = None if pooled["pairwise"] is None or base is None else pooled["pairwise"] - base
            pooled["delta_vs_lambdamart_ci"] = _bootstrap_delta(counts, lambdamart_counts[mode], draws, rng)
        block[mode] = pooled
    ndcgs, rhos = _list_metrics(candidates, score_of)
    ndcg_mean = mean_metric(list(ndcgs.values()))
    rho_mean = mean_metric(list(rhos.values()))
    block["ndcg_at_10"] = None if ndcg_mean is None else ndcg_mean
    block["ndcg_queries"] = len(ndcgs)
    block["ndcg_ci"] = _bootstrap_mean(ndcgs, draws, rng)
    block["spearman"] = None if rho_mean is None else rho_mean
    block["spearman_queries"] = len(rhos)
    block["spearman_ci"] = _bootstrap_mean(rhos, draws, rng)
    return block


def _random_ndcg(candidates: list[ScoredCandidate], draws: int, seed: int) -> dict:
    rng = random.Random(seed)
    grouped: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in candidates:
        grouped[row.query].append(row)
    per_query: list[float] = []
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        gains = {str(index): relevance(row.label_position) for index, row in enumerate(rows)}
        total = 0.0
        seen = 0
        for _ in range(draws):
            order = list(range(len(rows)))
            rng.shuffle(order)
            value = ndcg_at_k([str(index) for index in order], gains, k=10)
            if value is not None:
                total += value
                seen += 1
        if seen:
            per_query.append(total / seen)
    mean = mean_metric(per_query)
    return {
        "pairwise": 0.5,
        "pairwise_note": "A random order with no ties is right on half of the pairs in expectation. A model tie is not counted as correct, so ties lower the model against this 0.5.",
        "ndcg_at_10": None if mean is None else mean,
        "ndcg_queries": len(per_query),
        "spearman": 0.0,
        "spearman_note": "Expected Spearman of a random score with the labeled positions is 0.",
        "draws": draws,
    }


def evaluate_held_out(
    candidates: list[ScoredCandidate],
    history: list[ObservedRow],
    baselines: dict[str, dict[tuple[str, str], float]] | None = None,
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Family-out scores. ``label_position`` is read only while pooling metrics."""
    if draws < 1:
        raise ValueError("draws must be at least 1")
    baselines = baselines or {}
    names = sorted({row.query for row in candidates} | {row.query for row in history})
    families = _family_queries(names)
    scores: dict[str, dict[tuple[str, str], float]] = {name: {} for name in NEW_SCORERS}
    fold_notes = []
    by_query: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in candidates:
        by_query[row.query].append(row)
    for family in sorted(families):
        member_queries = families[family]
        holdout = set(member_queries)
        test_rows = [row for query in sorted(member_queries) for row in by_query.get(query, [])]
        if not test_rows:
            continue
        train_history = [row for row in history if row.query not in holdout]
        weight = choose_blend_weight(train_history)
        domain_scores = domain_prior_scores(test_rows, history, holdout)
        content_scores = content_query_scores(test_rows)
        blend_scores = _candidate_blend_scores(test_rows, history, holdout, weight)
        for table, values in (
            (scores["domain_prior"], domain_scores),
            (scores["content_query"], content_scores),
            (scores["calibrated_blend"], blend_scores),
        ):
            table.update(_score_table(test_rows, values))
        fold_notes.append(
            {
                "family": family,
                "queries": sorted(member_queries),
                "test_queries": sorted({row.query for row in test_rows}),
                "blend_weight": weight,
                "training_rows": len(train_history),
            }
        )
    for name, table in baselines.items():
        scores[name] = dict(table)
    lambdamart = baselines.get("lambdamart")
    metrics = {}
    for offset, (name, table) in enumerate(sorted(scores.items())):
        metrics[name] = _metric_block(candidates, table, lambdamart, draws, seed + offset)
    adoption = {}
    if "lambdamart" in baselines and "bm25" in baselines:
        for name in NEW_SCORERS:
            adoption[name] = {
                "published_draft_slices": adopt_draft_path(metrics, name, content=False),
                "pasted_draft_path": adopt_draft_path(metrics, name, content=True),
            }
    nested: dict[str, dict[str, dict[str, float]]] = {name: {} for name in NEW_SCORERS}
    for name in NEW_SCORERS:
        for (query, url), value in scores[name].items():
            nested[name].setdefault(query, {})[url] = value
    return {
        "protocol": {
            "split": "Query families from assign_families on the sorted query list. Each family is scored by a domain prior and a blend weight fit on the other families' Nov 6 rows only.",
            "labels": "Nov 9 positions are metrics only. They are not features and they are not the blend-weight labels.",
            "content": "BM25 of stored or pasted text. Missing text is left out of the z-score and adds 0. The missing-page sentinel is not used.",
            "blend_weights": list(BLEND_WEIGHTS),
            "content_weight": 1.0,
            "adoption_margin": DRAFT_ADOPTION_MARGIN,
            "bootstrap_draws": draws,
            "bootstrap_seed": seed,
            "uncertainty": "PREDICTED and UNSURE. One interval, 30 queries.",
        },
        "folds": fold_notes,
        "scores": nested,
        "metrics": metrics,
        "random": _random_ndcg(candidates, draws, seed),
        "adoption": adoption,
    }


def _slice_pair(content: bool) -> tuple[str, str]:
    if content:
        return "draft_vs_draft_with_content", "draft_vs_returning_with_content"
    return "draft_vs_draft", "draft_vs_returning"


def adopt_draft_path(metrics: dict, scorer: str, *, content: bool) -> dict:
    """Adopt only when both draft slices clear the current path.

    ``content=True`` is the pasted-draft gate: both pages have text, or the
    draft does. ``content=False`` still counts pages with no HTML.
    """
    draft_name, returning_name = _slice_pair(content)
    reasons: list[str] = []
    adopt = True
    for name in (draft_name, returning_name):
        point = metrics[scorer][name]["pairwise"]
        baseline = metrics["lambdamart"][name]["pairwise"]
        other = metrics["bm25"][name]["pairwise"]
        interval = metrics[scorer][name].get("delta_vs_lambdamart_ci") or [None, None]
        low = interval[0]
        if point is None or baseline is None:
            adopt = False
            reasons.append(f"{name} has no pairs.")
            continue
        bar = baseline + DRAFT_ADOPTION_MARGIN
        if point + 1e-12 < bar:
            adopt = False
            reasons.append(f"{name} {point:.3f} does not clear {bar:.3f}.")
        if other is None or point + 1e-12 < other:
            adopt = False
            shown = "n/a" if other is None else f"{other:.3f}"
            reasons.append(f"{name} {point:.3f} is below BM25 {shown}.")
        if low is None or low <= 0:
            adopt = False
            reasons.append(f"{name} interval includes no gain or a loss.")
    if adopt:
        return {
            "adopt": True,
            "margin": DRAFT_ADOPTION_MARGIN,
            "content_required": content,
            "reason": (
                "Both draft slices clear LambdaMART by 0.05, are at least BM25, "
                "and the bootstrap interval of the gain is above zero."
            ),
        }
    return {
        "adopt": False,
        "margin": DRAFT_ADOPTION_MARGIN,
        "content_required": content,
        "reason": " ".join(reasons),
    }


def _find(parent: dict[str, str], url: str) -> str:
    while parent[url] != url:
        parent[url] = parent[parent[url]]
        url = parent[url]
    return url


def _union(parent: dict[str, str], left: str, right: str) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root != right_root:
        parent[right_root] = left_root


def recover_query_scores(rows: list[dict]) -> tuple[dict[str, float], int]:
    """Rebuild one query's scores from strict pairwise gaps. Equal pages share a score."""
    urls = [row["url"] for row in rows]
    beats = {row["url"]: row["beats"] for row in rows}
    sources = {row["url"]: row["html_source"] for row in rows}
    parent = {url: url for url in urls}
    for left_index, left in enumerate(urls):
        for right in urls[left_index + 1 :]:
            if right not in beats[left] and left not in beats[right]:
                _union(parent, left, right)
    component_score: dict[str, float | None] = {}
    for url in urls:
        root = _find(parent, url)
        component_score.setdefault(root, None)
        if sources[url] == "missing":
            component_score[root] = MISSING_CONTENT_SCORE
    edges: list[tuple[str, str, float]] = []
    for url in urls:
        for other, gap in beats[url].items():
            edges.append((_find(parent, url), _find(parent, other), float(gap)))
    _propagate(component_score, edges)
    unknowns = [root for root, value in component_score.items() if value is None]
    if unknowns:
        component_score[sorted(unknowns)[0]] = 0.0
        _propagate(component_score, edges)
    mismatches = 0
    seen: set[tuple[str, str]] = set()
    for src, dst, gap in edges:
        if (src, dst) in seen:
            continue
        seen.add((src, dst))
        left = component_score[src]
        right = component_score[dst]
        if left is None or right is None or abs((left - right) - gap) > GAP_TOLERANCE:
            mismatches += 1
    recovered = {url: float(component_score[_find(parent, url)]) for url in urls}
    return recovered, mismatches


def _propagate(component_score: dict[str, float | None], edges: list[tuple[str, str, float]]) -> None:
    changed = True
    guard = 0
    while changed and guard < 50:
        guard += 1
        changed = False
        for src, dst, gap in edges:
            if component_score[dst] is not None and component_score[src] is None:
                component_score[src] = component_score[dst] + gap
                changed = True
            elif component_score[src] is not None and component_score[dst] is None:
                component_score[dst] = component_score[src] - gap
                changed = True


def _predictions_by_scorer(payload: dict, slug: str) -> dict[str, dict]:
    grouped: dict[str, dict] = defaultdict(dict)
    for row in payload["predictions"]:
        if row.get("project_slug") != slug:
            continue
        provenance = row["payload"]["provenance"]
        query = _query(row["query"])
        url = _key(row["subject_url"])
        beats = {}
        for call in row["payload"].get("calls") or []:
            beats[_key(call["competitor_url"])] = float(call["score_gap"])
        grouped[query][url] = {
            "url": url,
            "beats": beats,
            "html_source": provenance["html_source"],
            "entrant": bool(provenance["entrant"]),
        }
    return grouped


def _outcome_positions(payload: dict, slug: str) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = {}
    for row in payload["outcomes"]:
        if row.get("project_slug") != slug:
            continue
        found[_query(row["query"])] = {_key(url): int(position) for url, position in row["positions"].items()}
    return found


def _read_positions(path: Path) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            query = _query(row["query"])
            url = _key(row["link"])
            position = float(row["position"])
            current = grouped[query].get(url)
            if current is None or position < current:
                grouped[query][url] = position
    return grouped


def load_forward_rows(ledger_path: Path, baseline_csv: Path, forward_csv: Path) -> ForwardLoad:
    """Nov 6 history from the position file, and Nov 9 rows from the frozen ledger.

    BM25 and LambdaMART numbers are the recorded score gaps. Page HTML is not required.
    """
    payload = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    lambdamart = _predictions_by_scorer(payload, "forward-2026-11-09-lambdamart")
    bm25_rows = _predictions_by_scorer(payload, "forward-2026-11-09-bm25")
    outcomes = _outcome_positions(payload, "forward-2026-11-09-lambdamart")
    forward_positions = _read_positions(Path(forward_csv))
    mismatches = 0
    for query, urls in forward_positions.items():
        recorded = outcomes.get(query, {})
        for url, position in urls.items():
            if recorded.get(url) != int(position):
                mismatches += 1
    if set(forward_positions) != set(outcomes) or sum(len(urls) for urls in forward_positions.values()) != sum(
        len(urls) for urls in outcomes.values()
    ):
        mismatches += 1
    history: list[ObservedRow] = []
    for query, urls in _read_positions(Path(baseline_csv)).items():
        for url, position in sorted(urls.items()):
            history.append(ObservedRow(query, url, position))
    candidates: list[ScoredCandidate] = []
    baselines: dict[str, dict[tuple[str, str], float]] = {"lambdamart": {}, "bm25": {}}
    gap_mismatches = 0
    for query in sorted(lambdamart):
        rows = [lambdamart[query][url] for url in sorted(lambdamart[query])]
        recovered, bad = recover_query_scores(rows)
        gap_mismatches += bad
        bm25_list = [bm25_rows[query][url] for url in sorted(lambdamart[query])]
        recovered_bm25, bad_bm25 = recover_query_scores(bm25_list)
        gap_mismatches += bad_bm25
        for row in rows:
            url = row["url"]
            content = None if row["html_source"] == "missing" else recovered_bm25[url]
            candidates.append(
                ScoredCandidate(
                    query,
                    url,
                    float(outcomes[query][url]),
                    content,
                    row["html_source"],
                    row["entrant"],
                )
            )
            baselines["lambdamart"][(query, url)] = recovered[url]
            baselines["bm25"][(query, url)] = recovered_bm25[url]
    return ForwardLoad(candidates, history, baselines, gap_mismatches, mismatches)


def _count_mode(candidates: list[ScoredCandidate], scores: dict[tuple[str, str], float], mode: str) -> dict:
    return _pooled(_slice_counts(candidates, scores, mode))


def draft_failure_counts(loaded: ForwardLoad) -> dict:
    """Why the published LambdaMART draft pairs sit below chance.

    Counts use the frozen scores. They are not a new model.
    """
    scores = loaded.baselines["lambdamart"]
    draft = _count_mode(loaded.candidates, scores, "draft_vs_draft")
    content = _count_mode(loaded.candidates, scores, "draft_vs_draft_with_content")
    overall = _count_mode(loaded.candidates, scores, "all")
    per_query = _slice_counts(loaded.candidates, scores, "draft_vs_draft")
    both_missing = mixed_correct = mixed_wrong = 0
    grouped: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in loaded.candidates:
        grouped[row.query].append(row)
    for query, rows in grouped.items():
        for left in range(len(rows)):
            for right in range(left + 1, len(rows)):
                if not (is_draft(rows[left]) and is_draft(rows[right])):
                    continue
                if rows[left].label_position == rows[right].label_position:
                    continue
                better, worse = (rows[left], rows[right]) if rows[left].label_position < rows[right].label_position else (rows[right], rows[left])
                sources = {better.html_source, worse.html_source}
                left_score = scores[(query, better.url)]
                right_score = scores[(query, worse.url)]
                if sources == {"missing"} and left_score == right_score:
                    both_missing += 1
                elif "missing" in sources and "forward_fetch" in sources and len(sources) == 2:
                    if left_score > right_score:
                        mixed_correct += 1
                    elif left_score < right_score:
                        mixed_wrong += 1
    largest_name = ""
    largest_pairs = -1
    for query, counts in sorted(per_query.items()):
        total = sum(counts)
        if total > largest_pairs:
            largest_pairs = total
            largest_name = query
    if largest_pairs < 0:
        loo = None
        largest_pairs = 0
    else:
        dropped_correct = draft["correct"] - per_query[largest_name][0]
        dropped_total = draft["pairs"] - sum(per_query[largest_name])
        loo = None if dropped_total == 0 else dropped_correct / dropped_total
    return {
        "label": "diagnosis of the frozen LambdaMART scores, not a new prediction",
        "candidates": len(loaded.candidates),
        "queries": len({row.query for row in loaded.candidates}),
        "orientation_correct": overall["correct"],
        "orientation_wrong": overall["wrong"],
        "orientation_ties": overall["ties"],
        "draft_vs_draft": draft,
        "draft_vs_draft_with_content": content,
        "both_missing_ties": both_missing,
        "mixed_correct": mixed_correct,
        "mixed_wrong": mixed_wrong,
        "largest_query": largest_name,
        "largest_query_pairs": largest_pairs,
        "largest_query_loo_pairwise": loo,
        "forward_csv_mismatches": loaded.forward_csv_mismatches,
        "score_gap_mismatches": loaded.gap_mismatches,
        "per_query": {
            query: {"correct": counts[0], "wrong": counts[1], "ties": counts[2], "pairs": sum(counts)}
            for query, counts in sorted(per_query.items())
        },
    }


def write_new_page_ledger(session, result: dict, candidates: list[ScoredCandidate]) -> dict:
    """Append PREDICTED rows for the three held-out scorers. Does not rewrite an existing chain."""
    by_query: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for row in candidates:
        by_query[row.query].append(row)
    stamp = PREDICTION_CREATED_AT
    for scorer in NEW_SCORERS:
        table = result["scores"][scorer]
        slug = f"new-page-{FORWARD_DAY}-{scorer}"
        for query in sorted(by_query):
            rows = by_query[query]
            order = sorted(rows, key=lambda item: (-table[query][item.url], item.url))
            rank = {item.url: place for place, item in enumerate(order, start=1)}
            listed = [
                {"url": item.url, "html_sha256": "", "labeled_position": None}
                for item in sorted(rows, key=lambda item: item.url)
            ]
            for subject in rows:
                calls = []
                for other in rows:
                    if other.url == subject.url:
                        continue
                    gap = table[query][subject.url] - table[query][other.url]
                    if gap <= 0:
                        continue
                    calls.append(
                        Call(
                            competitor_url=other.url,
                            predicted="above",
                            score_gap=float(gap),
                            confidence="UNSURE",
                            note="PREDICTED. Not a Google rank.",
                        )
                    )
                record_prediction(
                    session,
                    kind="new_page_order",
                    query=query,
                    subject_url=subject.url,
                    subject_html=None,
                    predicted_rank=rank[subject.url],
                    predicted_of=len(rows),
                    candidates=listed,
                    calls=calls,
                    confidence="UNSURE",
                    calibration_note=(
                        "PREDICTED and UNSURE. Family-out scores use Nov 6 rows only. "
                        "The Nov 9 order was not used to train, to pick the blend weight, or to set this label."
                    ),
                    provenance={
                        "dataset": "serper_live",
                        "label": "PREDICTED",
                        "scorer": scorer,
                        "baseline_observed_on": "2026-11-06",
                        "target_observed_on": FORWARD_DAY,
                        "uncertainty": "UNSURE",
                        "html_source": subject.html_source,
                        "entrant": subject.entrant,
                        "content_missing": subject.content is None,
                    },
                    created_at=stamp,
                    project_slug=slug,
                )
    uploaded = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for scorer in NEW_SCORERS:
        slug = f"new-page-{FORWARD_DAY}-{scorer}"
        for query in sorted(by_query):
            rows = by_query[query]
            record_outcome(
                session,
                query=query,
                observed_on=FORWARD_DAY,
                provider="serper",
                positions={row.url: int(row.label_position) for row in rows},
                depth=10,
                notes=(
                    "Serper organic results, gl=us, hl=en, num=10, observed on 2026-11-09. "
                    "This is the later snapshot. It is not a model score."
                ),
                project_slug=slug,
                source_file="forward_serp.csv",
                source_sha256="",
                uploaded_at=uploaded,
            )
    summary = {}
    for scorer in NEW_SCORERS:
        scored = score_ledger(session, project_slug=f"new-page-{FORWARD_DAY}-{scorer}")
        summary[scorer] = {
            "predictions": scored.predictions,
            "calls": scored.calls,
            "resolved": scored.resolved,
            "correct": scored.correct,
            "unresolved": scored.unresolved,
            "accuracy": None if scored.accuracy is None else round(scored.accuracy, 6),
        }
    return summary


def _round_metric(block: dict) -> dict:
    rounded = dict(block)
    for key in ("pairwise", "delta_vs_lambdamart", "ndcg_at_10", "spearman"):
        if key in rounded and isinstance(rounded[key], float):
            rounded[key] = round(rounded[key], 6)
    for key in ("ci", "delta_vs_lambdamart_ci", "ndcg_ci", "spearman_ci"):
        if key in rounded and isinstance(rounded[key], list):
            rounded[key] = [None if value is None else round(value, 6) for value in rounded[key]]
    return rounded


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score new-page rules with query families held out. Does not read Nov 9 as training.")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--baseline-csv", required=True)
    parser.add_argument("--forward-csv", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--ledger-json", default=None)
    parser.add_argument("--ledger-db", default=None)
    parser.add_argument("--draws", type=int, default=BOOTSTRAP_DRAWS)
    args = parser.parse_args(argv)
    loaded = load_forward_rows(Path(args.ledger), Path(args.baseline_csv), Path(args.forward_csv))
    diagnosis = draft_failure_counts(loaded)
    result = evaluate_held_out(loaded.candidates, loaded.history, loaded.baselines, draws=args.draws)
    report = {
        "label": "PREDICTED",
        "diagnosis": _jsonable(diagnosis),
        "protocol": result["protocol"],
        "folds": _jsonable(result["folds"]),
        "random": _jsonable(result["random"]),
        "metrics": {name: {key: _round_metric(value) if isinstance(value, dict) else value for key, value in block.items()} for name, block in result["metrics"].items()},
        "adoption": result["adoption"],
        "ranker_change": "none",
    }
    adopted = [
        name
        for name, decision in result["adoption"].items()
        if decision["pasted_draft_path"]["adopt"]
    ]
    report["adopted_scorer"] = adopted[0] if len(adopted) == 1 else None
    report["adopted_scorers"] = adopted
    if adopted:
        report["ranker_change"] = "not_applied_until_the_pasted_path_is_wired"
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.ledger_json:
        from aeo.ranksim.forward_eval import export_ledger
        from aeo.ranksim.store import open_session

        database = f"sqlite:///{args.ledger_db}" if args.ledger_db else "sqlite://"
        if args.ledger_db:
            Path(args.ledger_db).parent.mkdir(parents=True, exist_ok=True)
        session = open_session(database)
        try:
            summary = write_new_page_ledger(session, result, loaded.candidates)
            exported = export_ledger(session)
            exported["scores"] = summary
            Path(args.ledger_json).write_text(json.dumps(exported, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        finally:
            session.close()
        report["ledger_json"] = args.ledger_json
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0
