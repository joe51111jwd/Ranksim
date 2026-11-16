"""Turn pairwise scores into positions, and predict position directly.

Ensemble spread sets a 3-wide range without reading the evaluation labels.
A shift of that window, and any score-gap threshold, is chosen on earlier
windows only. Every reported position is PREDICTED.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace

import numpy as np

from aeo.ranksim.pair_order import ALL_FEATURES, MONOTONE_UP, Row, _mean_std, _usable_features, _vector, fit_experiment
from aeo.ranksim.position_metrics import MAX_WIDTH, ranks_from_scores

ENSEMBLE_SIZE = 20


def bootstrap_query_rows(rows: list[Row], rng: random.Random) -> list[Row]:
    """Resample training queries with replacement. Copies stay separate groups."""
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        groups[(row.day, row.query)].append(row)
    keys = list(groups)
    if not keys:
        return []
    chosen = [keys[rng.randrange(len(keys))] for _ in keys]
    copied: list[Row] = []
    for copy_id, key in enumerate(chosen):
        for row in groups[key]:
            copied.append(replace(row, day=f"{row.day}#{copy_id}"))
    return copied


def fit_ensemble(name: str, rows: list[Row], *, size: int = ENSEMBLE_SIZE, mode: str = "seed") -> list:
    """Fit `size` models. Seed mode varies LightGBM's seed. Bootstrap mode resamples queries."""
    models = []
    for index in range(size):
        if mode == "bootstrap":
            sample = bootstrap_query_rows(rows, random.Random(10_000 + index))
            models.append(fit_experiment(name, sample, seed=index + 1))
        else:
            models.append(fit_experiment(name, rows, seed=index + 1))
    return models


def _sigmoid(value: float) -> float:
    if value > 30:
        return 1.0
    if value < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def pairwise_probability_matrix(scores: list[float]) -> list[list[float]]:
    """P(i beats j) = sigmoid(score_i - score_j). Higher score is better."""
    size = len(scores)
    matrix = [[0.0] * size for _ in range(size)]
    for left in range(size):
        for right in range(size):
            if left == right:
                continue
            matrix[left][right] = _sigmoid(scores[left] - scores[right])
    return matrix


def average_win_matrix(member_scores: list[list[float]]) -> list[list[float]]:
    """Fraction of ensemble members in which i outranks j. Ties count half."""
    size = len(member_scores[0])
    matrix = [[0.0] * size for _ in range(size)]
    for scores in member_scores:
        for left in range(size):
            for right in range(size):
                if left == right:
                    continue
                if scores[left] > scores[right]:
                    matrix[left][right] += 1.0
                elif scores[left] == scores[right]:
                    matrix[left][right] += 0.5
    count = float(len(member_scores))
    return [[value / count for value in row] for row in matrix]


def copeland_scores(win_prob: list[list[float]]) -> list[float]:
    """Wins when P>0.5, half a win when P=0.5. Higher is better."""
    scores = []
    for left, row in enumerate(win_prob):
        total = 0.0
        for right, prob in enumerate(row):
            if left == right:
                continue
            if prob > 0.5:
                total += 1.0
            elif prob == 0.5:
                total += 0.5
        scores.append(total)
    return scores


def borda_scores(win_prob: list[list[float]]) -> list[float]:
    """Sum of pairwise win probabilities. Higher is better."""
    return [sum(prob for index, prob in enumerate(row) if index != left) for left, row in enumerate(win_prob)]


def bradley_terry_scores(win_prob: list[list[float]], *, rounds: int = 40) -> list[float]:
    """Positive strengths fit to the win probabilities. Higher is better."""
    size = len(win_prob)
    if size == 0:
        return []
    strength = [1.0] * size
    for _ in range(rounds):
        updated = []
        for left in range(size):
            wins = sum(win_prob[left][right] for right in range(size) if right != left)
            denom = 0.0
            for right in range(size):
                if left == right:
                    continue
                denom += 1.0 / (strength[left] + strength[right])
            updated.append(wins / denom if denom > 0 else strength[left])
        log_mean = sum(math.log(max(value, 1e-12)) for value in updated) / size
        scale = math.exp(log_mean)
        strength = [max(value / scale, 1e-12) for value in updated]
    return strength


def median_rank_scores(rank_samples: list[list[int]]) -> list[float]:
    """Higher is better: negative median rank, with mean rank as a tie break."""
    scores = []
    for samples in rank_samples:
        ordered = sorted(samples)
        median = ordered[len(ordered) // 2]
        mean = sum(samples) / len(samples)
        scores.append(-(median + mean / 1000.0))
    return scores


def max_mass_window(samples: list[int], *, width: int = MAX_WIDTH, ceiling: int = 10) -> tuple[int, int]:
    """The inclusive window of `width` positions that holds the most ensemble ranks."""
    if not samples:
        return 1, 1
    span = max(1, min(width, ceiling))
    best = None
    median = sorted(samples)[len(samples) // 2]
    for start in range(1, ceiling - span + 2):
        end = start + span - 1
        mass = sum(1 for rank in samples if start <= rank <= end)
        center = (start + end) / 2.0
        key = (mass, -abs(center - median))
        if best is None or key > best[0]:
            best = (key, (start, end))
    assert best is not None
    return best[1]


def shift_window(low: int, high: int, shift: int, *, ceiling: int = 10) -> tuple[int, int]:
    """Slide a window and keep it inside 1..ceiling without growing past width 3."""
    span = high - low
    low = low + shift
    high = low + span
    if low < 1:
        high += 1 - low
        low = 1
    if high > ceiling:
        low -= high - ceiling
        high = ceiling
    low = max(1, low)
    high = min(ceiling, high)
    if high - low > MAX_WIDTH - 1:
        high = low + MAX_WIDTH - 1
    return low, high


def aggregate_positions(rows: list[Row], member_scores: list[list[float]]) -> dict[str, dict]:
    """Copeland, Bradley-Terry, Borda, and median-rank positions, plus per-row rank samples."""
    if not member_scores:
        raise ValueError("an ensemble needs at least one model")
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    stored = {
        name: [0.0] * len(rows)
        for name in ("copeland", "bradley_terry", "borda", "median_rank")
    }
    samples = [[] for _ in rows]
    windows = [(1, 1)] * len(rows)
    for indexes in grouped.values():
        aligned = [[scores[index] for index in indexes] for scores in member_scores]
        win_prob = average_win_matrix(aligned)
        rank_samples = [[] for _ in indexes]
        for scores in aligned:
            order = sorted(range(len(indexes)), key=lambda item: (-scores[item], rows[indexes[item]].url))
            for rank, local in enumerate(order, start=1):
                rank_samples[local].append(rank)
        local_scores = {
            "copeland": copeland_scores(win_prob),
            "bradley_terry": bradley_terry_scores(win_prob),
            "borda": borda_scores(win_prob),
            "median_rank": median_rank_scores(rank_samples),
        }
        for local, index in enumerate(indexes):
            for name, values in local_scores.items():
                stored[name][index] = values[local]
            samples[index] = rank_samples[local]
            windows[index] = max_mass_window(rank_samples[local], ceiling=max(len(indexes), max(rank_samples[local])))
    positions = {name: ranks_from_scores(rows, values) for name, values in stored.items()}
    return {"positions": positions, "samples": samples, "windows": windows}


def choose_window_shift(windows: list[tuple[int, int]], reals: list[int], ceilings: list[int]) -> int:
    """Shift in {-1, 0, 1} with the best early coverage. Width stays at most 3."""
    best_shift = 0
    best_hits = -1
    for shift in (-1, 0, 1):
        hits = 0
        for (low, high), real, ceiling in zip(windows, reals, ceilings, strict=True):
            shifted_low, shifted_high = shift_window(low, high, shift, ceiling=ceiling)
            if shifted_low <= real <= shifted_high:
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_shift = shift
    return best_shift


def apply_window_shift(windows: list[tuple[int, int]], shift: int, ceilings: list[int]) -> list[tuple[int, int, int]]:
    ranges = []
    for (low, high), ceiling in zip(windows, ceilings, strict=True):
        shifted_low, shifted_high = shift_window(low, high, shift, ceiling=ceiling)
        ranges.append((shifted_low, shifted_high, shifted_high - shifted_low + 1))
    return ranges


@dataclass
class DirectModel:
    """A model whose raw output is a predicted position. Lower is better."""

    kind: str
    predict_raw: object

    def predicted_positions(self, rows: list[Row]) -> list[int]:
        raw = self.predict_raw(rows)
        # Sort ascending inside the query. Rank 1 is the lowest predicted position.
        return ranks_from_scores(rows, [-float(value) for value in raw])

    def raw(self, rows: list[Row]) -> list[float]:
        return [float(value) for value in self.predict_raw(rows)]


def _design(rows: list[Row]) -> tuple[list[str], list[float], list[float], np.ndarray]:
    names = _usable_features(rows, list(ALL_FEATURES))
    if not names:
        raise ValueError("no varying features")
    means, stds = _mean_std(rows, names)
    matrix = np.asarray([_vector(row, names, means, stds) for row in rows], dtype=np.float64)
    return names, means, stds, matrix


def fit_lgbm_regression(rows: list[Row]) -> DirectModel:
    """Gradient boosting regression of the position. Persistence features are included."""
    import lightgbm as lgb

    names, means, stds, matrix = _design(rows)
    labels = np.asarray([float(row.position) for row in rows], dtype=np.float64)
    params = {
        "objective": "regression",
        "metric": "l2",
        "num_leaves": 7,
        "learning_rate": 0.05,
        "min_data_in_leaf": min(20, max(2, len(rows) // 10)),
        "lambda_l2": 2.0,
        "verbosity": -1,
        "seed": 0,
        "num_threads": 1,
        "deterministic": True,
        "force_col_wise": True,
    }
    booster = lgb.train(params, lgb.Dataset(matrix, label=labels), num_boost_round=80)

    def predict_raw(eval_rows: list[Row]) -> list[float]:
        eval_matrix = np.asarray([_vector(row, names, means, stds) for row in eval_rows], dtype=np.float64)
        return [float(value) for value in booster.predict(eval_matrix)]

    return DirectModel("lgbm_regression", predict_raw)


def fit_lgbm_multiclass(rows: list[Row]) -> DirectModel:
    """Classification over positions 1..10. The sort key is the expected position."""
    import lightgbm as lgb

    names, means, stds, matrix = _design(rows)
    labels = np.asarray([min(9, max(0, int(round(row.position)) - 1)) for row in rows], dtype=np.int32)
    params = {
        "objective": "multiclass",
        "num_class": 10,
        "metric": "multi_logloss",
        "num_leaves": 7,
        "learning_rate": 0.05,
        "min_data_in_leaf": min(10, max(1, len(rows) // 20)),
        "lambda_l2": 2.0,
        "verbosity": -1,
        "seed": 0,
        "num_threads": 1,
        "deterministic": True,
        "force_col_wise": True,
    }
    booster = lgb.train(params, lgb.Dataset(matrix, label=labels), num_boost_round=60)

    def predict_raw(eval_rows: list[Row]) -> list[float]:
        eval_matrix = np.asarray([_vector(row, names, means, stds) for row in eval_rows], dtype=np.float64)
        probs = booster.predict(eval_matrix)
        expected = []
        for row in probs:
            expected.append(sum((klass + 1) * float(prob) for klass, prob in enumerate(row)))
        return expected

    return DirectModel("lgbm_multiclass", predict_raw)


def fit_ordinal_lgbm(rows: list[Row]) -> DirectModel:
    """Cumulative ordinal model: nine binary models P(position <= k), then the expectation."""
    import lightgbm as lgb

    names, means, stds, matrix = _design(rows)
    positions = [int(round(row.position)) for row in rows]
    boosters = []
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 7,
        "learning_rate": 0.05,
        "min_data_in_leaf": min(20, max(2, len(rows) // 10)),
        "lambda_l2": 2.0,
        "verbosity": -1,
        "seed": 0,
        "num_threads": 1,
        "deterministic": True,
        "force_col_wise": True,
    }
    for cutoff in range(1, 10):
        labels = np.asarray([1.0 if position <= cutoff else 0.0 for position in positions], dtype=np.float64)
        if labels.min() == labels.max():
            boosters.append(float(labels[0]))
            continue
        boosters.append(lgb.train(params, lgb.Dataset(matrix, label=labels), num_boost_round=40))

    def predict_raw(eval_rows: list[Row]) -> list[float]:
        eval_matrix = np.asarray([_vector(row, names, means, stds) for row in eval_rows], dtype=np.float64)
        cumulative = []
        for booster in boosters:
            if isinstance(booster, float):
                cumulative.append(np.full(len(eval_rows), booster))
            else:
                cumulative.append(booster.predict(eval_matrix))
        expected = []
        for index in range(len(eval_rows)):
            cdf = []
            running = 0.0
            for probs in cumulative:
                running = max(running, min(1.0, float(probs[index])))
                cdf.append(running)
            # E[Y] = 1 + sum_k (1 - P(Y<=k)) for k=1..9, with P(Y<=10)=1.
            expectation = 1.0 + sum(1.0 - value for value in cdf)
            expected.append(expectation)
        return expected

    return DirectModel("ordinal_lgbm", predict_raw)


def monotone_feature_count() -> int:
    """Persistence and the other monotone features are part of every direct model."""
    return sum(1 for name in ALL_FEATURES if name in MONOTONE_UP)
