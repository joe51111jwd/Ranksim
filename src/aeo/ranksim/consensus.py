"""Consensus truth from several same-day pulls. Positions are REAL. Predictions are not.

A URL missing from a pull counts as rank 11, or as its deeper rank when a deep
list has it at 11 or worse. The consensus position is the median of those
ranks, rounded half up. The set of positions Google actually showed does not
include the imputed 11.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

ABSENT_RANK = 11


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def median_position(values: list[int]) -> int:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of no positions")
    if count % 2 == 1:
        return ordered[count // 2]
    return round_half_up((ordered[count // 2 - 1] + ordered[count // 2]) / 2)


@dataclass(frozen=True)
class ConsensusUrl:
    query: str
    url: str
    url_key: str
    consensus: int
    pull_positions: tuple[int, ...]
    shown_positions: tuple[int, ...]


def pull_rank(url_key: str, pull: dict[str, int], deep: dict[str, int]) -> tuple[int, int | None]:
    """Return (rank used in the median, position Google showed or None)."""
    if url_key in pull:
        position = int(pull[url_key])
        return position, position
    deep_rank = deep.get(url_key)
    if deep_rank is not None and int(deep_rank) >= ABSENT_RANK:
        return int(deep_rank), int(deep_rank)
    return ABSENT_RANK, None


def _as_rank_map(pull: dict[str, tuple[str, int]]) -> dict[str, int]:
    return {key: position for key, (_url, position) in pull.items()}


def consensus_from_pulls(
    query: str,
    pulls: list[dict[str, tuple[str, int]]],
    deep: dict[str, int],
) -> list[ConsensusUrl]:
    order: list[str] = []
    display: dict[str, str] = {}
    for pull in pulls:
        for key, (url, _position) in pull.items():
            if key not in display:
                order.append(key)
                display[key] = url
    rows = []
    for key in order:
        positions = []
        shown = []
        for pull in pulls:
            rank, observed = pull_rank(key, _as_rank_map(pull), deep)
            positions.append(rank)
            if observed is not None:
                shown.append(observed)
        rows.append(
            ConsensusUrl(
                query=query,
                url=display[key],
                url_key=key,
                consensus=median_position(positions),
                pull_positions=tuple(positions),
                shown_positions=tuple(shown),
            )
        )
    return rows


def predict_from_pull(
    rows: list[ConsensusUrl],
    pull: dict[str, tuple[str, int]],
    deep: dict[str, int],
) -> list[int]:
    """A real pull's position for each consensus URL. Missing becomes 11 or the deep rank."""
    return [pull_rank(row.url_key, _as_rank_map(pull), deep)[0] for row in rows]


def top_keys(rows: list[ConsensusUrl], positions: list[int], k: int = 3) -> list[str]:
    order = sorted(range(len(rows)), key=lambda index: (positions[index], rows[index].url_key))
    return [rows[index].url_key for index in order[:k]]


def consensus_top_keys(rows: list[ConsensusUrl], k: int = 3) -> list[str]:
    chosen = [row.url_key for row in rows if row.consensus <= k]
    if chosen:
        return chosen
    return top_keys(rows, [row.consensus for row in rows], k)


def consensus_number_one(rows: list[ConsensusUrl]) -> set[str]:
    best = min(row.consensus for row in rows)
    return {row.url_key for row in rows if row.consensus == best}


@dataclass
class QueryScore:
    query: str
    exact: tuple[float, float]
    within_range: tuple[float, float]
    observed: tuple[float, float]
    top3_precision: tuple[float, float]
    top3_recall: tuple[float, float]
    number_one: tuple[float, float]


def score_query(rows: list[ConsensusUrl], predicted: list[int], *, width: int = 3) -> QueryScore:
    if len(rows) != len(predicted):
        raise ValueError("predictions and consensus rows differ in length")
    if not rows:
        raise ValueError("empty query")
    exact = within = observed = 0
    half = width // 2
    for row, pred in zip(rows, predicted, strict=True):
        if pred == row.consensus:
            exact += 1
        low = max(1, pred - half)
        high = low + width - 1
        if low <= row.consensus <= high:
            within += 1
        if pred in row.shown_positions:
            observed += 1
    count = float(len(rows))
    predicted_top = set(top_keys(rows, predicted, 3))
    truth_top = set(consensus_top_keys(rows, 3))
    overlap = len(predicted_top & truth_top)
    truth_one = consensus_number_one(rows)
    called = top_keys(rows, predicted, 1)[0]
    return QueryScore(
        query=rows[0].query,
        exact=(exact, count),
        within_range=(within, count),
        observed=(observed, count),
        top3_precision=(overlap, float(len(predicted_top))),
        top3_recall=(overlap, float(len(truth_top))),
        number_one=(1.0 if called in truth_one else 0.0, 1.0),
    )


def pool(scores: list[QueryScore]) -> dict[str, tuple[float, float]]:
    names = ("exact", "within_range", "observed", "top3_precision", "top3_recall", "number_one")
    totals = {name: [0.0, 0.0] for name in names}
    for score in scores:
        for name in names:
            numerator, denominator = getattr(score, name)
            totals[name][0] += numerator
            totals[name][1] += denominator
    return {name: (numerator, denominator) for name, (numerator, denominator) in totals.items()}


def leave_one_out_ceiling(
    query: str,
    pulls: list[dict[str, tuple[str, int]]],
    deep: dict[str, int],
) -> list[QueryScore]:
    """How well each real pull predicts the consensus of the other pulls."""
    found = []
    for index, pull in enumerate(pulls):
        others = pulls[:index] + pulls[index + 1 :]
        rows = consensus_from_pulls(query, others, deep)
        if not rows:
            continue
        predicted = predict_from_pull(rows, pull, deep)
        found.append(score_query(rows, predicted))
    return found
