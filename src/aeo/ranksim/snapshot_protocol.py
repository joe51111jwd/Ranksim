"""Frozen query-split protocol for the 2026-11-06 snapshot.

The published 10-query holdout is a reference set. Model choices are made on
the other 20 queries, with near-duplicate queries held out together. Nothing
here reads a captured position into a feature.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

from aeo.textutil import tokenize_for_search

FROZEN_SEED = 20261106
HOLDOUT_COUNT = 10
# A training-side gain smaller than this is not adopted. Five points is about
# 23 pairs on the 469 stored training pairs, larger than the query-level noise.
ADOPTION_MARGIN = 0.05
FAMILY_JACCARD = 0.5
CONTENT_STOP = frozenset(
    {"a", "an", "the", "for", "with", "and", "in", "of", "to", "best", "software", "all", "one", "1"}
)
LEXICAL_FEATURES = (
    "bm25f",
    "bm25_title",
    "bm25_h1",
    "bm25_body",
    "bm25_url",
    "url_exact",
    "topic_coverage",
)
RANK_FEATURE_NAMES = frozenset({"position", "prior_position", "provider_order"})


def frozen_split(queries: list[str]) -> tuple[list[str], list[str]]:
    """Return (train, holdout), each sorted.

    Listed order, shuffled with random.Random(20261106), first 10 held out.
    """
    if len(queries) < HOLDOUT_COUNT + 2:
        raise ValueError(f"Need at least {HOLDOUT_COUNT + 2} queries to freeze this split.")
    shuffled = list(queries)
    random.Random(FROZEN_SEED).shuffle(shuffled)
    holdout = sorted(shuffled[:HOLDOUT_COUNT])
    train = sorted(shuffled[HOLDOUT_COUNT:])
    return train, holdout


def content_tokens(query: str) -> set[str]:
    text = query.lower().replace("-", " ").replace("dirtworks", "dirt work").replace("dirtwork", "dirt work")
    return {token for token in tokenize_for_search(text) if token not in CONTENT_STOP}


def assign_families(queries: list[str]) -> dict[str, str]:
    """Group near-duplicate queries.

    Two queries join when their content-token Jaccard is at least 0.5.
    Every query containing "alternative" is one family, so an alternatives
    page is not validated against a reworded alternatives query.
    """
    parent = {query: query for query in queries}

    def find(query: str) -> str:
        while parent[query] != query:
            parent[query] = parent[parent[query]]
            query = parent[query]
        return query

    def union(left: str, right: str) -> None:
        parent[find(left)] = find(right)

    tokens = {query: content_tokens(query) for query in queries}
    ordered = list(queries)
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            both = tokens[left] | tokens[right]
            if both and len(tokens[left] & tokens[right]) / len(both) >= FAMILY_JACCARD:
                union(left, right)
    alternatives = [query for query in ordered if "alternative" in query.lower()]
    for query in alternatives[1:]:
        union(alternatives[0], query)
    groups: dict[str, list[str]] = defaultdict(list)
    for query in ordered:
        groups[find(query)].append(query)
    names: dict[str, str] = {}
    for index, members in enumerate(sorted(groups.values(), key=lambda rows: rows[0]), start=1):
        label = f"family-{index}"
        for query in members:
            names[query] = label
    return names


def pair_counts(positions: list[float], scores: list[float]) -> tuple[int, int, int]:
    """(correct, wrong, ties) over pairs whose positions differ.

    A tie in the score is not counted as a correct order.
    """
    correct = wrong = ties = 0
    for left in range(len(positions)):
        for right in range(len(positions)):
            if positions[left] >= positions[right]:
                continue
            if scores[left] > scores[right]:
                correct += 1
            elif scores[left] < scores[right]:
                wrong += 1
            else:
                ties += 1
    return correct, wrong, ties


def _query_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"query_mean": None, "query_std": None, "query_se": None, "query_min": None, "query_max": None}
    mean = sum(values) / len(values)
    if len(values) == 1:
        var = 0.0
    else:
        var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = math.sqrt(var)
    return {
        "query_mean": round(mean, 6),
        "query_std": round(std, 6),
        "query_se": round(std / math.sqrt(len(values)), 6),
        "query_min": round(min(values), 6),
        "query_max": round(max(values), 6),
    }


def summarize_queries(groups: list[tuple[list[float], list[float]]]) -> dict:
    """groups are (positions, scores) for one query each. Every differing pair is in the denominator."""
    correct = wrong = ties = 0
    per_query: list[float] = []
    usable = 0
    for positions, scores in groups:
        got, bad, tied = pair_counts(positions, scores)
        total = got + bad + tied
        if total == 0:
            continue
        usable += 1
        correct += got
        wrong += bad
        ties += tied
        per_query.append(got / total)
    total = correct + wrong + ties
    return {
        "queries": usable,
        "pairs": total,
        "correct": correct,
        "wrong": wrong,
        "ties": ties,
        "pairwise": None if total == 0 else round(correct / total, 6),
        **_query_stats(per_query),
        "meaning": "Unconditional order of every stored pair. A score tie is not correct.",
    }


def selective_pairs(groups: list[tuple[list[float], list[float]]], *, keep_fraction: float = 0.5) -> dict:
    """Accuracy on the largest score gaps, with the share of pairs that remain.

    keep_fraction is fixed in advance. It is not chosen to reach a target accuracy.
    """
    if not 0 < keep_fraction <= 1:
        raise ValueError("keep_fraction must be in (0, 1].")
    pool: list[tuple[float, bool]] = []
    for positions, scores in groups:
        for left in range(len(positions)):
            for right in range(len(positions)):
                if positions[left] >= positions[right]:
                    continue
                gap = abs(scores[left] - scores[right])
                pool.append((gap, scores[left] > scores[right]))
    if not pool:
        return {"pairs": 0, "kept": 0, "coverage": None, "pairwise": None, "keep_fraction": keep_fraction}
    pool.sort(key=lambda item: item[0], reverse=True)
    kept_n = max(1, math.ceil(len(pool) * keep_fraction))
    kept = pool[:kept_n]
    correct = sum(1 for _gap, good in kept if good)
    return {
        "pairs": len(pool),
        "kept": kept_n,
        "coverage": round(kept_n / len(pool), 6),
        "pairwise": round(correct / kept_n, 6),
        "keep_fraction": keep_fraction,
        "meaning": "Selective accuracy. Not the accuracy of the tool on every eligible pair.",
    }


def paired_query_delta(baseline: dict[str, float], candidate: dict[str, float]) -> dict:
    """Query-level difference, candidate minus baseline, on the queries both scored."""
    shared = sorted(set(baseline) & set(candidate))
    diffs = [candidate[query] - baseline[query] for query in shared]
    if not diffs:
        return {"queries": 0, "mean": None, "std": None, "se": None, "improved": 0, "worse": 0, "unchanged": 0}
    mean = sum(diffs) / len(diffs)
    if len(diffs) == 1:
        var = 0.0
    else:
        var = sum((value - mean) ** 2 for value in diffs) / (len(diffs) - 1)
    std = math.sqrt(var)
    improved = sum(1 for value in diffs if value > 1e-12)
    worse = sum(1 for value in diffs if value < -1e-12)
    return {
        "queries": len(diffs),
        "mean": round(mean, 6),
        "std": round(std, 6),
        "se": round(std / math.sqrt(len(diffs)), 6),
        "improved": improved,
        "worse": worse,
        "unchanged": len(diffs) - improved - worse,
    }


def signal_decision(delta: dict) -> dict:
    """Promote a signal only when the query-level gain is larger than its own noise.

    A gain inside two standard errors is reported and not adopted. This is not a
    five-point gate: a smaller gain is kept when it is consistent across queries.
    """
    mean = delta.get("mean")
    se = delta.get("se")
    if mean is None or se is None:
        return {"adopt": False, "reason": "No paired query comparison was available."}
    if mean <= 0:
        return {"adopt": False, "reason": f"Query-mean change is {mean:.4f}. The signal is not higher."}
    if not (delta.get("improved", 0) >= delta.get("worse", 0)):
        return {"adopt": False, "reason": "More queries got worse than got better."}
    if mean <= 2 * se:
        return {
            "adopt": False,
            "reason": (
                f"Query-mean gain {mean:.4f} is inside two standard errors ({se:.4f}). "
                "The point estimate is higher and is not distinguishable from noise on 20 queries."
            ),
        }
    return {
        "adopt": True,
        "reason": (
            f"Query-mean gain {mean:.4f} exceeds two standard errors ({se:.4f}), "
            f"on {delta.get('improved')} improved and {delta.get('worse')} worse queries."
        ),
    }


def adoption_decision(
    candidate: float | None,
    lexical_baseline: float | None,
    current_model: float | None,
    *,
    margin: float = ADOPTION_MARGIN,
) -> dict:
    """Adopt a candidate only when family-out pairwise clears both baselines by the margin."""
    if candidate is None or lexical_baseline is None or current_model is None:
        return {"adopt": False, "reason": "A baseline or the candidate has no pairs."}
    bar = max(lexical_baseline, current_model) + margin
    if candidate + 1e-12 < bar:
        return {
            "adopt": False,
            "reason": (
                f"Family-out pairwise {candidate:.3f} does not clear {bar:.3f} "
                f"(the better of BM25 and the current model, plus {margin:.2f})."
            ),
            "bar": round(bar, 6),
        }
    return {"adopt": True, "reason": f"Family-out pairwise {candidate:.3f} clears {bar:.3f}.", "bar": round(bar, 6)}


def assert_no_rank_leak(features: dict[str, float]) -> None:
    """Captured position is not a feature. An unset navboost prior stays at zero."""
    leaked = RANK_FEATURE_NAMES.intersection(features)
    if leaked:
        raise ValueError(f"Rank fields cannot be features: {sorted(leaked)}")
    prior = features.get("navboost_prior", 0.0)
    if prior == prior and float(prior) != 0.0:
        raise ValueError("navboost_prior carries a previous rank and is not allowed on this snapshot.")
