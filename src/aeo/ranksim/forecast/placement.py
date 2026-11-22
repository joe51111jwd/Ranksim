"""Part 6: the placement model. Turns features into a position, a range and a label.

What is REAL here: nothing is observed in this module. It is trained on tables
whose outcome column holds REAL positions (the panel, later the paid cohort
cells) or SYNTHETIC ones (synth.World), and the label of the training table is
carried on the model. What is PREDICTED here: every probability, point, range
and label it returns.

The model (docs/plan/IMPLEMENTATION_SPEC.md section 6, part 6):

- Gradient-boosted trees (LightGBM, multiclass) give a probability for each of
  the five bands 1-3, 4-10, 11-20, 21-50 and NT50.
- A position-within-band model gives the shape inside each band: a regressor
  per band (or the training mean when a band has few rows) and a spread from
  out-of-fold residuals, turned into a discretized normal over the band's
  positions.
- Together they give a probability for each of the 51 outcomes (1 to 50, NT50).
- The point is the MEDIAN of that distribution. This rule is frozen in the model
  version (`POINT_RULE`). `expected_hit` and `best_point` show how far the honest
  median is from the point that would maximise grading rule v1. They are a
  diagnostic only and never the point.
- The range is a central interval of the distribution whose nominal level is
  chosen on a calibration fold kept apart from training, so that its coverage
  there reaches config.RANGE_COVERAGE.
- One temperature on the band probabilities is fitted on the calibration fold.
  Reliability (expected calibration error and a table) is reported before and
  after it.
- The label is `locked` when one band other than NT50 holds at least
  config.LOCKED_MIN_PROB, `out_of_reach` when NT50 holds at least
  config.OUT_OF_REACH_MIN_PROB, else `contested`. The thresholds are copied
  into the model at fit time and frozen with it.

It trains on any table with the feature columns and an outcome position: the
synthetic world's upload cells, the panel's reading cells, and later the paid
cohort cells. Missing feature values stay NaN (LightGBM routes them).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .config import LOCKED_MIN_PROB, NT50, OUT_OF_REACH_MIN_PROB, RANGE_COVERAGE, TOP_DEPTH
from .features import check_model_features, stable_share
from .grading import FINE_BANDS, RULE_V1, GradingRule, fine_band_of
from .types import PREDICTED, Forecast

PLACEMENT_VERSION = "placement-v1"
POINT_RULE = "median"
SEED = 20261122
NUM_THREADS = 3
OUTCOMES: tuple[int, ...] = tuple(range(1, TOP_DEPTH + 1)) + (NT50,)
BAND_RANGES: dict[str, tuple[int, int]] = {"1-3": (1, 3), "4-10": (4, 10), "11-20": (11, 20), "21-50": (21, 50),
                                           "NT50": (NT50, NT50)}
CALIBRATION_SHARE = 0.20
CV_FOLDS = 3
MIN_ROWS_FOR_TREES = 60  # below this the band model is the smoothed training share of each band
MIN_ROWS_WITHIN_TREES = 200  # per band, below this the within-band model is the training mean
WITHIN_SD_FLOOR = 0.5
PRIOR_PSEUDO_COUNT = 1.0
TEMPERATURE_GRID: tuple[float, ...] = tuple(np.round(np.arange(0.5, 3.01, 0.05), 2).tolist())
RANGE_LEVEL_GRID: tuple[float, ...] = tuple(np.round(np.arange(0.50, 0.995, 0.01), 2).tolist())
RELIABILITY_BINS = 10
BAND_GRID: tuple[dict[str, Any], ...] = (
    {"num_leaves": 7, "min_data_in_leaf": 50},
    {"num_leaves": 15, "min_data_in_leaf": 100},
    {"num_leaves": 31, "min_data_in_leaf": 200},
)
BAND_FIXED: dict[str, Any] = {"learning_rate": 0.1, "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
                              "lambda_l2": 1.0, "max_rounds": 400, "early_stopping_rounds": 20}
WITHIN_FIXED: dict[str, Any] = {"learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 50, "rounds": 200,
                                "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0}


class PlacementError(ValueError):
    """The placement model was asked to do something it refuses."""


# ---------------------------------------------------------------------------
# outcomes and distributions


def band_index(position: int) -> int:
    """Index of a position's fine band in grading.FINE_BANDS."""
    return FINE_BANDS.index(fine_band_of(int(position)))


def outcome_index(position: int) -> int:
    """Index of a position in OUTCOMES (0 for position 1, 50 for NT50)."""
    value = int(position)
    return TOP_DEPTH if value > TOP_DEPTH else value - 1


def _hit_matrix(rule: GradingRule) -> np.ndarray:
    """H[i, j] = 1 when point OUTCOMES[i] is a hit for real OUTCOMES[j] under `rule` (grading.py decides)."""
    return np.array([[1.0 if rule.is_hit(p, a) else 0.0 for a in OUTCOMES] for p in OUTCOMES])


_HIT_V1 = _hit_matrix(RULE_V1)


def dist_quantile(dist: np.ndarray, q: float) -> int:
    """The smallest outcome whose cumulative probability reaches q. Outcomes are ordered 1..50 then NT50."""
    cum = np.cumsum(np.asarray(dist, dtype=float))
    total = cum[-1] if cum[-1] > 0 else 1.0
    index = int(np.searchsorted(cum / total, q - 1e-12, side="left"))
    return OUTCOMES[min(index, len(OUTCOMES) - 1)]


def dist_median(dist: np.ndarray) -> int:
    """The point rule of PLACEMENT_VERSION: the median of the 51-outcome distribution."""
    return dist_quantile(dist, 0.5)


def expected_hit(dist: np.ndarray, point: int, rule: GradingRule = RULE_V1) -> float:
    """Chance that `point` is a hit under `rule` if the distribution is right. A diagnostic only. PREDICTED."""
    matrix = _HIT_V1 if rule == RULE_V1 else _hit_matrix(rule)
    return float(matrix[outcome_index(point)] @ np.asarray(dist, dtype=float))


def best_point(dist: np.ndarray, rule: GradingRule = RULE_V1) -> tuple[int, float]:
    """The point that maximises the expected hit under `rule`, with that expectation.

    This is the rule-gaming point (it piles onto 15 and 25). It is never issued
    as the forecast. Reports show it beside the median. Ties go to the smaller point.
    """
    matrix = _HIT_V1 if rule == RULE_V1 else _hit_matrix(rule)
    values = matrix @ np.asarray(dist, dtype=float)
    index = int(np.argmax(values))
    return OUTCOMES[index], float(values[index])


def band_mass(dist: np.ndarray) -> np.ndarray:
    """Probability per fine band (1-3, 4-10, 11-20, 21-50, NT50) of a 51-outcome distribution or a matrix of them."""
    arr = np.asarray(dist, dtype=float)
    single = arr.ndim == 1
    arr = arr[None, :] if single else arr
    out = np.column_stack([arr[:, BAND_RANGES[b][0] - 1: min(BAND_RANGES[b][1], NT50)].sum(axis=1) for b in FINE_BANDS])
    return out[0] if single else out


def label_for(band_probs: Sequence[float], locked_min: float = LOCKED_MIN_PROB,
              out_of_reach_min: float = OUT_OF_REACH_MIN_PROB) -> str:
    """locked, contested or out_of_reach from the five band probabilities (thresholds frozen in config)."""
    probs = np.asarray(band_probs, dtype=float)
    if probs[-1] >= out_of_reach_min:
        return "out_of_reach"
    if probs[:-1].max() >= locked_min:
        return "locked"
    return "contested"


def normal_shape(band: str, mean: float, sd: float) -> np.ndarray:
    """A discretized normal over the positions of one band (all mass inside the band). Length 51."""
    low, high = BAND_RANGES[band]
    shape = np.zeros(len(OUTCOMES))
    if band == "NT50":
        shape[-1] = 1.0
        return shape
    positions = np.arange(low, high + 1, dtype=float)
    centre = float(np.clip(mean if math.isfinite(mean) else (low + high) / 2.0, low, high))
    spread = max(float(sd) if math.isfinite(sd) else (high - low + 1) / 2.0, WITHIN_SD_FLOOR)
    weights = np.exp(-0.5 * ((positions - centre) / spread) ** 2)
    weights = weights / weights.sum()
    shape[low - 1: high] = weights
    return shape


def normal_shapes(band: str, means: np.ndarray, sd: float) -> np.ndarray:
    """`normal_shape` for many rows at once: an (n, 51) array, each row summing to 1 inside the band."""
    means = np.asarray(means, dtype=float)
    low, high = BAND_RANGES[band]
    out = np.zeros((len(means), len(OUTCOMES)))
    if band == "NT50":
        out[:, -1] = 1.0
        return out
    positions = np.arange(low, high + 1, dtype=float)
    centre = np.clip(np.where(np.isfinite(means), means, (low + high) / 2.0), low, high)
    spread = max(float(sd) if math.isfinite(sd) else (high - low + 1) / 2.0, WITHIN_SD_FLOOR)
    weights = np.exp(-0.5 * ((positions[None, :] - centre[:, None]) / spread) ** 2)
    out[:, low - 1: high] = weights / weights.sum(axis=1, keepdims=True)
    return out


def combine(band_probs: np.ndarray, shapes: Mapping[str, np.ndarray]) -> np.ndarray:
    """A 51-outcome distribution from five band probabilities and one within-band shape per band (each sums to 1)."""
    out = np.zeros(len(OUTCOMES))
    for k, band in enumerate(FINE_BANDS):
        out += float(band_probs[k]) * np.asarray(shapes[band], dtype=float)
    total = out.sum()
    return out / total if total > 0 else out


def apply_temperature(probs: np.ndarray, temperature: float) -> np.ndarray:
    """Band probabilities sharpened (T < 1) or softened (T > 1): p^(1/T), renormalised. Rows sum to 1."""
    arr = np.clip(np.asarray(probs, dtype=float), 1e-12, 1.0)
    logits = np.log(arr) / float(temperature)
    logits -= logits.max(axis=-1, keepdims=True)
    expo = np.exp(logits)
    return expo / expo.sum(axis=-1, keepdims=True)


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    picked = np.clip(np.asarray(probs)[np.arange(len(y)), y], 1e-12, 1.0)
    return float(-np.mean(np.log(picked)))


def fit_temperature(probs: np.ndarray, y: np.ndarray, grid: Sequence[float] = TEMPERATURE_GRID) -> float:
    """The one temperature (from `grid`) with the lowest log loss on held-out rows. 1.0 when there are none."""
    if len(y) == 0:
        return 1.0
    losses = [(log_loss(apply_temperature(probs, t), y), t) for t in grid]
    return float(min(losses)[1])


def reliability(probs: np.ndarray, y: np.ndarray, bins: int = RELIABILITY_BINS) -> dict[str, Any]:
    """Reliability of band probabilities on held-out rows: per band, a table of predicted against observed share.

    `ece` per band is the count-weighted mean absolute gap between the mean
    predicted probability and the observed share inside equal-width bins.
    `top_label_ece` is the same for the probability of the band called most likely.
    Observed shares are of REAL (or SYNTHETIC) outcomes; probabilities are PREDICTED.
    """
    arr = np.asarray(probs, dtype=float)
    y = np.asarray(y, dtype=int)
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: dict[str, Any] = {"rows": int(len(y)), "bins": bins, "bands": {}}
    if len(y) == 0:
        out["top_label_ece"] = None
        return out

    def table(pred: np.ndarray, hit: np.ndarray) -> tuple[float, list[dict[str, Any]]]:
        which = np.clip(np.digitize(pred, edges[1:-1], right=False), 0, bins - 1)
        rows, ece = [], 0.0
        for b in range(bins):
            mask = which == b
            n = int(mask.sum())
            if n == 0:
                continue
            mean_p = float(pred[mask].mean())
            share = float(hit[mask].mean())
            ece += n * abs(mean_p - share)
            rows.append({"bin": [float(edges[b]), float(edges[b + 1])], "count": n, "mean_predicted": mean_p,
                         "observed_share": share})
        return ece / len(pred), rows

    for k, band in enumerate(FINE_BANDS):
        ece, rows = table(arr[:, k], (y == k).astype(float))
        out["bands"][band] = {"ece": ece, "observed_share": float(np.mean(y == k)),
                              "mean_predicted": float(arr[:, k].mean()), "table": rows}
    top = arr.argmax(axis=1)
    ece, rows = table(arr[np.arange(len(y)), top], (y == top).astype(float))
    out["top_label_ece"] = ece
    out["top_label_table"] = rows
    return out


# ---------------------------------------------------------------------------
# LightGBM helpers


def lgb_params(extra: Mapping[str, Any], seed: int = SEED) -> dict[str, Any]:
    """Deterministic LightGBM settings with a fixed thread count."""
    params = {"verbose": -1, "seed": seed, "bagging_seed": seed, "feature_fraction_seed": seed, "data_random_seed": seed,
              "deterministic": True, "force_row_wise": True, "num_threads": NUM_THREADS}
    params.update({k: v for k, v in extra.items() if k not in ("max_rounds", "early_stopping_rounds", "rounds")})
    return params


def matrix(frame: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    """Feature matrix in a fixed column order. A missing column is all NaN, never 0."""
    cols = []
    for name in features:
        if name in frame:
            cols.append(pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float))
        else:
            cols.append(np.full(len(frame), np.nan))
    return np.column_stack(cols) if cols else np.zeros((len(frame), 0))


def group_folds(groups: Sequence[str], seed: int, folds: int = CV_FOLDS) -> np.ndarray:
    """A fold number per row from sha256(seed|group): one group never sits in two folds."""
    shares = np.array([stable_share(str(g), seed + 7) for g in groups])
    return np.minimum((shares * folds).astype(int), folds - 1)


def _scaled(grid_entry: Mapping[str, Any], rows: int) -> dict[str, Any]:
    entry = dict(grid_entry)
    entry["min_data_in_leaf"] = int(max(5, min(int(entry["min_data_in_leaf"]), rows // 20)))
    return entry


def fit_band_trees(x: np.ndarray, y: np.ndarray, groups: Sequence[str], *, seed: int = SEED,
                   grid: Sequence[Mapping[str, Any]] = BAND_GRID) -> tuple[str, int, dict[str, Any], dict[str, Any]]:
    """Choose settings and rounds by grouped cross-validation inside the rows given, then fit on all of them.

    Returns (booster text, rounds, chosen params, cv record). Every choice is made
    on these rows only (the caller passes training rows).
    """
    import lightgbm as lgb

    folds = group_folds(groups, seed)
    record: dict[str, Any] = {"folds": CV_FOLDS, "grid": []}
    best: tuple[float, int, dict[str, Any]] | None = None
    for entry in grid:
        params = lgb_params({**BAND_FIXED, **_scaled(entry, len(y)), "objective": "multiclass",
                             "num_class": len(FINE_BANDS)}, seed)
        losses, rounds = [], []
        for k in range(CV_FOLDS):
            tr, va = folds != k, folds == k
            if tr.sum() < 10 or va.sum() < 10:
                continue
            train = lgb.Dataset(x[tr], y[tr], free_raw_data=False)
            valid = lgb.Dataset(x[va], y[va], reference=train, free_raw_data=False)
            booster = lgb.train(params, train, num_boost_round=BAND_FIXED["max_rounds"], valid_sets=[valid],
                                callbacks=[lgb.early_stopping(BAND_FIXED["early_stopping_rounds"], verbose=False)])
            iters = max(1, int(booster.best_iteration or BAND_FIXED["max_rounds"]))
            proba = booster.predict(x[va], num_iteration=iters)
            losses.append(log_loss(proba, y[va]))
            rounds.append(iters)
        if not losses:
            continue
        loss = float(np.mean(losses))
        mean_rounds = int(round(float(np.mean(rounds))))
        record["grid"].append({"params": dict(entry), "cv_log_loss": loss, "rounds": mean_rounds})
        if best is None or loss < best[0]:
            best = (loss, mean_rounds, dict(entry))
    if best is None:
        best = (float("nan"), 50, dict(grid[0]))
    _, rounds, chosen = best
    params = lgb_params({**BAND_FIXED, **_scaled(chosen, len(y)), "objective": "multiclass",
                         "num_class": len(FINE_BANDS)}, seed)
    booster = lgb.train(params, lgb.Dataset(x, y), num_boost_round=max(1, rounds))
    record["chosen"] = {"params": chosen, "rounds": rounds}
    return booster.model_to_string(), int(rounds), params, record


# ---------------------------------------------------------------------------
# the model


@dataclass
class WithinBand:
    """The position-within-band model of one band: a regressor (or the training mean) and a spread."""

    band: str
    rows: int
    mean: float
    sd: float
    booster_text: str | None = None
    rounds: int = 0

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.booster_text is None or len(x) == 0:
            return np.full(len(x), self.mean)
        import lightgbm as lgb

        if not hasattr(self, "_cached"):
            object.__setattr__(self, "_cached", lgb.Booster(model_str=self.booster_text))
        return np.asarray(getattr(self, "_cached").predict(x, num_threads=NUM_THREADS), dtype=float)

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_cached", None)
        return data


def fit_within(band: str, x: np.ndarray, pos: np.ndarray, groups: Sequence[str], seed: int = SEED) -> WithinBand:
    """Fit the within-band model of one band on training rows whose outcome is in that band."""
    low, high = BAND_RANGES[band]
    if len(pos) == 0:
        return WithinBand(band, 0, (low + high) / 2.0, (high - low + 1) / 2.0)
    mean = float(np.mean(pos))
    sd = float(np.std(pos)) if len(pos) > 1 else (high - low + 1) / 2.0
    if len(pos) < MIN_ROWS_WITHIN_TREES or band == "NT50":
        return WithinBand(band, int(len(pos)), mean, max(sd, WITHIN_SD_FLOOR))
    import lightgbm as lgb

    params = lgb_params({**WITHIN_FIXED, "objective": "regression"}, seed)
    folds = group_folds(groups, seed)
    oof = np.full(len(pos), np.nan)
    for k in range(CV_FOLDS):
        tr, va = folds != k, folds == k
        if tr.sum() < 20 or va.sum() == 0:
            continue
        booster = lgb.train(params, lgb.Dataset(x[tr], pos[tr]), num_boost_round=WITHIN_FIXED["rounds"])
        oof[va] = booster.predict(x[va])
    resid = pos - np.clip(oof, low, high)
    resid = resid[np.isfinite(resid)]
    spread = float(np.sqrt(np.mean(resid ** 2))) if len(resid) else sd
    booster = lgb.train(params, lgb.Dataset(x, pos), num_boost_round=WITHIN_FIXED["rounds"])
    return WithinBand(band, int(len(pos)), mean, max(spread, WITHIN_SD_FLOOR), booster.model_to_string(),
                      WITHIN_FIXED["rounds"])


@dataclass
class PlacementModel:
    """A fitted placement model. Output is PREDICTED. `training_label` says whether it learned REAL or SYNTHETIC outcomes."""

    features: list[str]
    band_booster_text: str | None
    band_rounds: int
    band_params: dict[str, Any]
    prior_band_probs: list[float]
    within: dict[str, WithinBand]
    temperature: float
    range_level: float
    training_label: str
    training_rows: int
    calibration_rows: int
    version: str = PLACEMENT_VERSION
    point_rule: str = POINT_RULE
    range_coverage_target: float = RANGE_COVERAGE
    locked_min_prob: float = LOCKED_MIN_PROB
    out_of_reach_min_prob: float = OUT_OF_REACH_MIN_PROB
    cv: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    name: str = "placement"
    label: str = PREDICTED

    # -- prediction

    def _booster(self) -> Any:
        import lightgbm as lgb

        if not hasattr(self, "_cached"):
            object.__setattr__(self, "_cached", lgb.Booster(model_str=self.band_booster_text))
        return getattr(self, "_cached")

    def raw_band_probabilities(self, frame: pd.DataFrame) -> np.ndarray:
        """Band probabilities before the temperature."""
        if self.band_booster_text is None:
            return np.tile(np.asarray(self.prior_band_probs, dtype=float), (len(frame), 1))
        x = matrix(frame, self.features)
        if len(x) == 0:
            return np.zeros((0, len(FINE_BANDS)))
        proba = np.asarray(self._booster().predict(x, num_threads=NUM_THREADS), dtype=float)
        return proba.reshape(len(frame), len(FINE_BANDS))

    def band_probabilities(self, frame: pd.DataFrame) -> np.ndarray:
        """Calibrated band probabilities (1-3, 4-10, 11-20, 21-50, NT50). PREDICTED."""
        return apply_temperature(self.raw_band_probabilities(frame), self.temperature)

    def distributions(self, frame: pd.DataFrame) -> np.ndarray:
        """A probability for each of the 51 outcomes per row. PREDICTED."""
        probs = self.band_probabilities(frame)
        x = matrix(frame, self.features)
        out = np.zeros((len(frame), len(OUTCOMES)))
        for k, band in enumerate(FINE_BANDS):
            out += probs[:, k:k + 1] * normal_shapes(band, self.within[band].predict(x), self.within[band].sd)
        total = out.sum(axis=1, keepdims=True)
        return np.divide(out, total, out=np.zeros_like(out), where=total > 0)

    def summarize(self, dist: np.ndarray) -> dict[str, Any]:
        """Point (median), range (calibrated central interval), label and the rule-gaming diagnostic for one distribution."""
        tail = (1.0 - self.range_level) / 2.0
        point = dist_median(dist)
        low = min(dist_quantile(dist, tail), point)
        high = max(dist_quantile(dist, 1.0 - tail), point)
        bands = band_mass(dist)
        gaming, gaming_hit = best_point(dist)
        return {"point": point, "range_low": low, "range_high": high,
                "label": label_for(bands, self.locked_min_prob, self.out_of_reach_min_prob),
                "probs": {band: float(v) for band, v in zip(FINE_BANDS, bands)},
                "expected_hit_of_point": expected_hit(dist, point), "rule_gaming_point": gaming,
                "expected_hit_of_rule_gaming_point": gaming_hit}

    def forecast(self, dist: np.ndarray, *, site: str, search: str, stage: str, as_of: date,
                 notes: Mapping[str, Any] | None = None, target_url: str | None = None) -> Forecast:
        """A types.Forecast from one distribution. PREDICTED."""
        info = self.summarize(dist)
        extra = {"expected_hit_of_point": info["expected_hit_of_point"], "rule_gaming_point": info["rule_gaming_point"],
                 "expected_hit_of_rule_gaming_point": info["expected_hit_of_rule_gaming_point"],
                 "point_rule": self.point_rule, "training_label": self.training_label}
        extra.update(dict(notes or {}))
        return Forecast(site=site, search=search, stage=stage, as_of=as_of, point=int(info["point"]),
                        range_low=int(info["range_low"]), range_high=int(info["range_high"]), label=info["label"],
                        model_version=self.model_version(), probs=info["probs"], target_url=target_url, notes=extra)

    def model_version(self) -> str:
        """The version name plus a short hash of the fitted model, so two fits never share a name."""
        return f"{self.version}:{self.name}:{self.sha256()[:12]}"

    # -- persistence

    def payload(self) -> dict[str, Any]:
        data = {k: v for k, v in asdict(self).items() if k != "within"}
        data.pop("_cached", None)
        data["within"] = {band: w.payload() for band, w in self.within.items()}
        return data

    def sha256(self) -> str:
        text = json.dumps(_json_safe(self.payload()), sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "PlacementModel":
        payload = dict(data)
        payload["within"] = {band: WithinBand(**w) for band, w in payload["within"].items()}
        return cls(**payload)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.floating,)):
        return _json_safe(float(value))
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def save_model(model: PlacementModel, path: str | Path) -> str:
    """Write the model as JSON and return its sha256 (stored beside it in <name>.sha256)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_json_safe(model.payload()), sort_keys=True)
    target.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    target.with_suffix(".sha256").write_text(digest + "\n", encoding="utf-8")
    return digest


def load_model(path: str | Path) -> PlacementModel:
    """Load a saved model. Refuses a file whose sha256 does not match the one written beside it."""
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    stamp = target.with_suffix(".sha256")
    if stamp.is_file() and hashlib.sha256(text.encode("utf-8")).hexdigest() != stamp.read_text().strip():
        raise PlacementError(f"{target} does not match its sha256")
    return PlacementModel.from_payload(json.loads(text))


def _outcomes(frame: pd.DataFrame, outcome_col: str) -> np.ndarray:
    values = pd.to_numeric(frame[outcome_col], errors="coerce")
    if values.isna().any():
        raise PlacementError(f"{int(values.isna().sum())} rows have no outcome in {outcome_col}. "
                             "An unknown outcome is not NT50: drop those rows first.")
    out = values.to_numpy(dtype=int)
    if (out < 1).any():
        raise PlacementError("an outcome below position 1")
    return np.minimum(out, NT50)


def fit_placement(
    frame: pd.DataFrame,
    features: Sequence[str],
    *,
    outcome_col: str = "position_out",
    group_col: str = "search_group",
    training_label: str,
    calibration_share: float = CALIBRATION_SHARE,
    seed: int = SEED,
    grid: Sequence[Mapping[str, Any]] = BAND_GRID,
    name: str = "placement",
) -> PlacementModel:
    """Fit on a training table: band trees and within-band models on the fit fold, temperature and range on the calibration fold.

    The calibration fold is whole groups (`group_col`, the search group by
    default) picked by sha256(seed|group) < `calibration_share`, so no group is
    on both sides. Settings are chosen by grouped cross-validation inside the
    fit fold. `training_label` is REAL or SYNTHETIC and is carried on the model.
    Raises PlacementError on an outcome that is missing (unknown is not NT50).
    """
    feats = check_model_features(features)
    if outcome_col in feats:
        raise PlacementError("the outcome cannot be a feature")
    if len(frame) == 0:
        raise PlacementError("no training rows")
    y_pos = _outcomes(frame, outcome_col)
    y = np.array([band_index(p) for p in y_pos])
    groups = frame[group_col].astype(str).to_numpy() if group_col in frame else np.array([str(i) for i in range(len(frame))])
    is_cal = np.array([stable_share(g, seed + 11) < calibration_share for g in groups])
    if is_cal.all() or (~is_cal).sum() < 2:
        is_cal = np.zeros(len(frame), dtype=bool)
    fit_rows = ~is_cal
    x = matrix(frame, feats)
    counts = np.bincount(y[fit_rows], minlength=len(FINE_BANDS)).astype(float)
    prior = ((counts + PRIOR_PSEUDO_COUNT) / (counts.sum() + PRIOR_PSEUDO_COUNT * len(FINE_BANDS))).tolist()
    booster_text, rounds, params, cv = None, 0, {}, {"note": "too few rows for trees: band model is the smoothed share"}
    if fit_rows.sum() >= MIN_ROWS_FOR_TREES and len(set(y[fit_rows])) >= 2:
        booster_text, rounds, params, cv = fit_band_trees(x[fit_rows], y[fit_rows], groups[fit_rows], seed=seed, grid=grid)
    within = {}
    for k, band in enumerate(FINE_BANDS):
        mask = fit_rows & (y == k)
        within[band] = fit_within(band, x[mask], y_pos[mask].astype(float), groups[mask], seed)
    model = PlacementModel(features=list(feats), band_booster_text=booster_text, band_rounds=rounds, band_params=params,
                           prior_band_probs=prior, within=within, temperature=1.0, range_level=RANGE_COVERAGE,
                           training_label=training_label, training_rows=int(fit_rows.sum()),
                           calibration_rows=int(is_cal.sum()), cv=cv, name=name)
    if is_cal.any():
        cal = frame[is_cal]
        raw = model.raw_band_probabilities(cal)
        model.temperature = fit_temperature(raw, y[is_cal])
        model.calibration = {"before_temperature": reliability(raw, y[is_cal]),
                             "after_temperature": reliability(apply_temperature(raw, model.temperature), y[is_cal]),
                             "temperature": model.temperature}
        dists = model.distributions(cal)
        model.range_level = choose_range_level(dists, y_pos[is_cal])
        model.calibration["range_level"] = model.range_level
        model.calibration["range_coverage_on_calibration"] = range_coverage(dists, y_pos[is_cal], model.range_level)
    else:
        model.calibration = {"note": "no calibration fold: temperature 1 and the nominal range level are used"}
    return model


def central_interval(dist: np.ndarray, level: float) -> tuple[int, int]:
    tail = (1.0 - level) / 2.0
    point = dist_median(dist)
    return min(dist_quantile(dist, tail), point), max(dist_quantile(dist, 1.0 - tail), point)


def range_coverage(dists: np.ndarray, actual: np.ndarray, level: float) -> float | None:
    """Share of rows whose real position lies inside the central interval at `level`."""
    if len(actual) == 0:
        return None
    inside = 0
    for dist, real in zip(dists, actual):
        low, high = central_interval(dist, level)
        inside += int(low <= min(int(real), NT50) <= high)
    return inside / len(actual)


def choose_range_level(dists: np.ndarray, actual: np.ndarray, target: float = RANGE_COVERAGE,
                       grid: Sequence[float] = RANGE_LEVEL_GRID) -> float:
    """The smallest nominal level in `grid` whose coverage on these (calibration) rows reaches `target`."""
    for level in grid:
        cover = range_coverage(dists, actual, level)
        if cover is not None and cover >= target:
            return float(level)
    return float(grid[-1])


def calibration_report(model: PlacementModel, frame: pd.DataFrame, *, outcome_col: str = "position_out") -> dict[str, Any]:
    """Reliability of the band probabilities and range coverage on held-out rows given by the caller."""
    y_pos = _outcomes(frame, outcome_col)
    y = np.array([band_index(p) for p in y_pos])
    probs = model.band_probabilities(frame)
    dists = model.distributions(frame)
    return {"reliability": reliability(probs, y), "range_level": model.range_level,
            "range_coverage": range_coverage(dists, y_pos, model.range_level), "rows": int(len(y))}


def point_diagnostics(model: PlacementModel, dists: np.ndarray, actual: Sequence[int] | None = None) -> dict[str, Any]:
    """How far the honest median sits from the rule-gaming point: mean expected hits, shares on 15 and 25, and real hits.

    Real hits (when `actual` is given) come from grading.py's rule. Everything else is PREDICTED.
    """
    med = [dist_median(d) for d in dists]
    gaming = [best_point(d)[0] for d in dists]
    out: dict[str, Any] = {
        "rows": len(med),
        "expected_hit_of_median": float(np.mean([expected_hit(d, p) for d, p in zip(dists, med)])) if med else None,
        "expected_hit_of_rule_gaming_point": float(np.mean([best_point(d)[1] for d in dists])) if med else None,
        "share_median_equals_gaming_point": float(np.mean([a == b for a, b in zip(med, gaming)])) if med else None,
        "share_gaming_points_on_15_or_25": float(np.mean([g in (15, 25) for g in gaming])) if med else None,
    }
    if actual is not None and med:
        out["real_hit_of_median"] = float(np.mean([RULE_V1.is_hit(p, int(a)) for p, a in zip(med, actual)]))
        out["real_hit_of_rule_gaming_point"] = float(np.mean([RULE_V1.is_hit(p, int(a)) for p, a in zip(gaming, actual)]))
    return out


__all__ = [
    "BAND_RANGES", "OUTCOMES", "PLACEMENT_VERSION", "POINT_RULE", "PlacementError", "PlacementModel", "WithinBand",
    "apply_temperature", "band_mass", "best_point", "calibration_report", "central_interval", "choose_range_level",
    "combine", "dist_median", "normal_shapes", "dist_quantile", "expected_hit", "fit_placement", "fit_temperature", "label_for",
    "load_model", "normal_shape", "point_diagnostics", "range_coverage", "reliability", "save_model",
]
