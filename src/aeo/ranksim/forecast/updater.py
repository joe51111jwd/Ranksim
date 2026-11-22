"""Part 7: the early-reading updater. Moves a forecast as Google reacts.

What is REAL here: readings of a site's position taken after launch (clean
readings, or single panel pages in the backtest) when the caller passes REAL
ones; SYNTHETIC ones from synth.World in tests. What is PREDICTED here: every
updated probability, point, range and label.

Version 1, the transition table (works with a handful of rows). Cells are
keyed by the band of the latest reading (1-3, 4-10, 11-20, 21-50, NT50), the
prior forecast's band, the lead time bucket (days from the reading to the
outcome) and whether the site is new to the search. For each key the table
holds the chance of each outcome band, with Dirichlet smoothing toward the
pooled table one level up (key without new, then without the prior band, then
the reading band alone, then all rows). The smoothing weight is chosen by
grouped cross-validation on the training rows.

How it combines with the prior (OUR READING of "Bayes' rule"). The table's
chance of outcome band o given the reading r, divided by the chance of o given
the prior band alone (same lead and newness), is the likelihood ratio of the
reading. The updated band probabilities are prior(o) times that ratio,
renormalised. When the prior is calibrated this equals the table's own
answer, and a sharper prior moves the result as Bayes' rule says it should.
Inside the band that holds the reading position, the position is a
discretized normal around the reading (spread fitted on training rows where
reading and outcome share a band). Other bands keep the prior's shape.

Version 2, the model. Gradient-boosted trees over the reading (position, in or
out of the top 50, the move between the last two readings when there are two,
how many readings), the prior distribution, the lead time, page demand,
strength and trust gaps, and Search Console early signals when present
(gsc.early_signals; NaN on the panel). It replaces the table only when it beats
it on validation rows held out of both fits by a margin whose cluster interval
over searches lies above zero. `Updater.in_use` and `Updater.reason` record the
choice.

Search Console positions are never outcomes. A reading dated after the forecast
date is refused (asof.LeakError).

Version 2 additions (both chosen on training-side rows only, never on held-out rows):

- Time-aware calibration. Given each row's outcome date and the end of the
  training period, the model's temperature and the updater's range level are
  set on the most recent slice of their rows (placement.recent_slice: the last
  90 days, at least 2,000 rows, else every row).
- A rule for one segment: sites new to the search at the reading
  (`choose_segment`). The idea of a separate rule comes from a version 1
  held-out result (on those sites the updater scored 0.820 against 0.867 for
  always NOT IN TOP 50). Which rule is used is decided on validation rows only,
  by a frozen rule: the version in use stays unless a candidate (always NT50,
  the table, the model) beats it on the segment's validation rows with a cluster
  interval over searches whose lower bound is above zero; among those the
  largest gain wins. `Updater.segment_rules` records the winner and
  `Updater.segment_comparison` the validation numbers.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .asof import LeakError, as_date
from .config import NT50, RANGE_COVERAGE, TOP_DEPTH
from .features import check_model_features
from .grading import FINE_BANDS, RULE_V1, GradedCell, compare_to_baseline, fine_band_of
from .placement import (
    BAND_RANGES,
    CALIBRATION_RECENT_DAYS,
    CALIBRATION_RECENT_MIN_ROWS,
    OUTCOMES,
    apply_temperature,
    band_index,
    band_mass,
    choose_range_level,
    dist_median,
    dist_quantile,
    fit_band_trees,
    fit_temperature,
    group_folds,
    label_for,
    log_loss,
    matrix,
    normal_shapes,
    recent_slice,
    reliability,
)
from .types import PREDICTED, REAL, SYNTHETIC, Forecast

UPDATER_TABLE_VERSION = "updater-table-v1"
UPDATER_MODEL_VERSION = "updater-model-v2"
SEED = 20261122
NO_READING = "none"
READING_BANDS: tuple[str, ...] = (*FINE_BANDS, NO_READING)
LEAD_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("0-27", 0, 27), ("28-45", 28, 45), ("46-75", 46, 75), ("76-130", 76, 130), ("131+", 131, 10 ** 9))
NEW_VALUES: tuple[str, ...] = ("new", "not_new", "unknown")
ALPHA_GRID: tuple[float, ...] = (1.0, 3.0, 10.0, 30.0, 100.0)
CV_FOLDS = 3
SD_FLOOR = 0.5
MIN_ROWS_FOR_MODEL = 200
VALIDATION_MARGIN = 0.0  # the model must beat the table with a lower interval bound above this
# Segments with their own rule, and the column that marks a row as inside the segment (value 1).
SEGMENT_COLUMNS: dict[str, str] = {"new_to_search": "new_to_search"}
SEGMENT_CANDIDATES: tuple[str, ...] = ("always_nt50", "table", "model")
SEGMENT_RULE = ("on the segment's validation rows, the version in use stays unless a candidate (always NT50, the table, "
                "the model) beats it with a cluster interval over searches whose lower bound is above zero; among those "
                "the largest gain wins")
SEGMENT_ORIGIN = ("The idea of a separate rule for sites new to the search comes from the version 1 held-out result: on "
                  "those sites the updater scored 0.820 against 0.867 for always NOT IN TOP 50. The rule itself is chosen "
                  "on validation rows only.")

# Columns of the version 2 model. Missing columns are NaN. GSC columns are NaN on the panel.
READING_FEATURES: tuple[str, ...] = ("reading_position", "reading_in_top50", "reading_move", "readings_count")
PRIOR_FEATURES: tuple[str, ...] = tuple(f"prior_p_{b}" for b in FINE_BANDS) + ("prior_point",)
CONTEXT_FEATURES: tuple[str, ...] = ("lead_days", "new_to_search", "own_score", "gap_top10", "gap_top20", "gap_top50",
                                     "q_top10", "q_top50", "demand_top10_q50", "demand_t21_50_q50", "trust_pred",
                                     "trust_gap_top10", "pg_kd")
GSC_FEATURES: tuple[str, ...] = ("sc_impressions_total", "sc_clicks_total", "sc_query_present_target",
                                 "sc_avg_position_target", "sc_impressions_slope")
MODEL_FEATURES: tuple[str, ...] = (*READING_FEATURES, *PRIOR_FEATURES, *CONTEXT_FEATURES, *GSC_FEATURES)


class UpdaterError(ValueError):
    """The updater was asked to do something it refuses."""


# ---------------------------------------------------------------------------
# readings


@dataclass(frozen=True)
class Reading:
    """One reading of a site on a search. REAL (clean reader or a panel page) or SYNTHETIC.

    `position` is 1 to 50, NT50 when the reading reaches rank 50 and the site is
    not in it, or None when the reading cannot say (a page shorter than 50).
    """

    position: int | None
    taken_on: date
    label: str = REAL
    source: str = "clean_reader"

    def __post_init__(self) -> None:
        if self.position is not None and not 1 <= int(self.position) <= NT50:
            raise UpdaterError(f"a reading position must be 1 to 50 or NT50, not {self.position}")
        if self.label not in (REAL, SYNTHETIC):
            raise UpdaterError("a reading is REAL or SYNTHETIC. Search Console positions are never readings here.")


def usable_readings(readings: Sequence[Reading], as_of: date) -> list[Reading]:
    """Readings known on `as_of`, oldest first. A reading after `as_of` raises LeakError. Unknown positions are dropped."""
    day = as_date(as_of)
    late = [r for r in readings if as_date(r.taken_on) > day]
    if late:
        raise LeakError(f"a reading taken on {late[0].taken_on} is after the forecast date {day}")
    return sorted((r for r in readings if r.position is not None), key=lambda r: as_date(r.taken_on))


def reading_band(position: float | int | None) -> str:
    """The fine band of a reading, or `none` when there is no usable reading."""
    if position is None or (isinstance(position, float) and not math.isfinite(position)):
        return NO_READING
    return fine_band_of(int(position))


def lead_bucket(days: float | int | None) -> str:
    """The lead bucket of a lead in days (reading to outcome)."""
    if days is None or (isinstance(days, float) and not math.isfinite(days)):
        raise UpdaterError("the lead time is required")
    value = int(round(float(days)))
    for name, low, high in LEAD_BUCKETS:
        if low <= value <= high:
            return name
    raise UpdaterError(f"lead {days} is negative: the outcome must come after the reading")


def new_value(flag: Any) -> str:
    if flag is None or (isinstance(flag, float) and not math.isfinite(flag)) or flag is pd.NA:
        return "unknown"
    return "new" if bool(flag) else "not_new"


def reading_columns(readings: Sequence[Reading], as_of: date) -> dict[str, float]:
    """The reading features of version 2: latest position, in the top 50, the move between the last two, count."""
    usable = usable_readings(readings, as_of)
    if not usable:
        return {"reading_position": math.nan, "reading_in_top50": math.nan, "reading_move": math.nan,
                "readings_count": 0.0}
    last = float(usable[-1].position)
    move = float(usable[-2].position) - last if len(usable) >= 2 else math.nan
    return {"reading_position": last, "reading_in_top50": 1.0 if last <= TOP_DEPTH else 0.0, "reading_move": move,
            "readings_count": float(len(usable))}


# ---------------------------------------------------------------------------
# within-band shaping shared by both versions


def shape_with_reading(band_probs: np.ndarray, prior_dists: np.ndarray, reading_pos: np.ndarray,
                       spreads: Mapping[str, float]) -> np.ndarray:
    """51-outcome distributions from updated band probabilities.

    Inside the band holding the reading position the shape is a discretized
    normal around the reading. Other bands keep the prior's shape (uniform when
    the prior had no mass there).
    """
    band_probs = np.asarray(band_probs, dtype=float)
    prior = np.asarray(prior_dists, dtype=float)
    n = len(band_probs)
    pos = np.asarray(reading_pos, dtype=float)
    out = np.zeros((n, len(OUTCOMES)))
    for k, band in enumerate(FINE_BANDS):
        low, high = BAND_RANGES[band]
        lo, hi = low - 1, min(high, NT50)
        prior_part = prior[:, lo:hi]
        mass = prior_part.sum(axis=1, keepdims=True)
        width = hi - lo
        shape = np.where(mass > 0, prior_part / np.where(mass > 0, mass, 1.0), 1.0 / width)
        full = np.zeros((n, len(OUTCOMES)))
        full[:, lo:hi] = shape
        if band != "NT50":
            inside = np.isfinite(pos) & (pos >= low) & (pos <= high)
            if inside.any():
                full[inside] = normal_shapes(band, pos[inside], spreads.get(band, SD_FLOOR))
        out += band_probs[:, k:k + 1] * full
    total = out.sum(axis=1, keepdims=True)
    return np.divide(out, total, out=np.zeros_like(out), where=total > 0)


def fit_spreads(reading_pos: np.ndarray, outcome: np.ndarray) -> dict[str, float]:
    """Per band, the root mean square of (outcome minus reading) on rows where both sit in that band."""
    out: dict[str, float] = {}
    for band in FINE_BANDS[:-1]:
        low, high = BAND_RANGES[band]
        mask = (reading_pos >= low) & (reading_pos <= high) & (outcome >= low) & (outcome <= high)
        diffs = (outcome[mask] - reading_pos[mask]).astype(float)
        out[band] = max(float(np.sqrt(np.mean(diffs ** 2))) if len(diffs) else (high - low + 1) / 2.0, SD_FLOOR)
    return out


# ---------------------------------------------------------------------------
# version 1: the transition table


def _key(*parts: str) -> str:
    return "|".join(parts)


@dataclass
class TransitionTable:
    """Version 1. Counts of outcome bands per key, smoothed toward the pooled table. PREDICTED output."""

    counts: dict[str, list[float]]
    base_counts: dict[str, list[float]]
    alpha: float
    spreads: dict[str, float]
    rows: int
    training_label: str
    version: str = UPDATER_TABLE_VERSION
    cv: dict[str, Any] = field(default_factory=dict)
    label: str = PREDICTED

    def _chain(self, table: Mapping[str, list[float]], keys: Sequence[str]) -> np.ndarray:
        n_bands = len(FINE_BANDS)
        glob = np.asarray(table.get(_key("*"), [0.0] * n_bands), dtype=float)
        probs = (glob + 1.0) / (glob.sum() + n_bands)
        for key in reversed(keys):
            c = np.asarray(table.get(key, [0.0] * n_bands), dtype=float)
            probs = (c + self.alpha * probs) / (c.sum() + self.alpha)
        return probs

    def outcome_probs(self, rb: str, pb: str, lb: str, new: str) -> np.ndarray:
        """Chance of each outcome band given the reading band, prior band, lead bucket and newness (smoothed)."""
        keys = [_key(rb, pb, lb, new), _key(rb, pb, lb), _key(rb, lb), _key(rb)]
        return self._chain(self.counts, keys)

    def base_probs(self, pb: str, lb: str, new: str) -> np.ndarray:
        """Chance of each outcome band given the prior band, lead bucket and newness alone (smoothed)."""
        keys = [_key(pb, lb, new), _key(pb, lb), _key(pb)]
        return self._chain(self.base_counts, keys)

    def update_bands(self, prior_band_probs: np.ndarray, rb: str, pb: str, lb: str, new: str) -> np.ndarray:
        """Bayes' rule: prior(o) times the likelihood ratio table(o | r, ...) / table(o | prior band, ...)."""
        prior = np.asarray(prior_band_probs, dtype=float)
        if rb == NO_READING:
            return prior / prior.sum()
        ratio = self.outcome_probs(rb, pb, lb, new) / self.base_probs(pb, lb, new)
        post = prior * ratio
        total = post.sum()
        return post / total if total > 0 else self.outcome_probs(rb, pb, lb, new)

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _count_tables(rb: Sequence[str], pb: Sequence[str], lb: Sequence[str], nv: Sequence[str],
                  y: np.ndarray) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    n_bands = len(FINE_BANDS)
    counts: dict[str, np.ndarray] = {}
    base: dict[str, np.ndarray] = {}

    def add(table: dict[str, np.ndarray], key: str, k: int) -> None:
        table.setdefault(key, np.zeros(n_bands))[k] += 1.0

    for r, p, l, v, k in zip(rb, pb, lb, nv, y):
        for key in (_key(r, p, l, v), _key(r, p, l), _key(r, l), _key(r), _key("*")):
            add(counts, key, int(k))
        for key in (_key(p, l, v), _key(p, l), _key(p), _key("*")):
            add(base, key, int(k))
    return ({k: v.tolist() for k, v in counts.items()}, {k: v.tolist() for k, v in base.items()})


def _table_keys(frame: pd.DataFrame) -> tuple[list[str], list[str], list[str], list[str]]:
    rb = [reading_band(v) for v in pd.to_numeric(frame["reading_position"], errors="coerce")]
    pb = [str(v) for v in frame["prior_band"]]
    lb = [lead_bucket(v) for v in pd.to_numeric(frame["lead_days"], errors="coerce")]
    nv = [new_value(v) for v in (frame["new_to_search"] if "new_to_search" in frame else [None] * len(frame))]
    return rb, pb, lb, nv


def _outcome_bands(frame: pd.DataFrame, outcome_col: str) -> tuple[np.ndarray, np.ndarray]:
    values = pd.to_numeric(frame[outcome_col], errors="coerce")
    if values.isna().any():
        raise UpdaterError("an outcome is missing. An unknown outcome is not NT50: drop the row first.")
    pos = np.minimum(values.to_numpy(dtype=int), NT50)
    return pos, np.array([band_index(p) for p in pos])


def fit_table(frame: pd.DataFrame, *, outcome_col: str = "position_out", group_col: str = "search_group",
              training_label: str, seed: int = SEED, alpha_grid: Sequence[float] = ALPHA_GRID) -> TransitionTable:
    """Fit version 1 on training rows: reading_position (NaN for none), prior_band, lead_days, new_to_search, outcome.

    The smoothing weight is chosen by grouped cross-validation (log loss of the
    table's own outcome probabilities) inside these rows.
    """
    if len(frame) == 0:
        raise UpdaterError("no training rows")
    pos, y = _outcome_bands(frame, outcome_col)
    rb, pb, lb, nv = _table_keys(frame)
    reading = pd.to_numeric(frame["reading_position"], errors="coerce").to_numpy(dtype=float)
    groups = frame[group_col].astype(str).to_numpy() if group_col in frame else np.array([str(i) for i in range(len(frame))])
    folds = group_folds(groups, seed)
    cv: dict[str, Any] = {"log_loss_by_alpha": {}}
    best_alpha, best_loss = alpha_grid[0], float("inf")
    if len(set(groups)) >= CV_FOLDS and len(frame) >= 3 * CV_FOLDS:
        for alpha in alpha_grid:
            losses = []
            for k in range(CV_FOLDS):
                tr, va = folds != k, folds == k
                if tr.sum() == 0 or va.sum() == 0:
                    continue
                idx = np.flatnonzero(tr)
                counts, base = _count_tables([rb[i] for i in idx], [pb[i] for i in idx], [lb[i] for i in idx],
                                             [nv[i] for i in idx], y[tr])
                table = TransitionTable(counts, base, alpha, {}, int(tr.sum()), training_label)
                probs = np.array([table.outcome_probs(rb[i], pb[i], lb[i], nv[i]) for i in np.flatnonzero(va)])
                losses.append(log_loss(probs, y[va]))
            loss = float(np.mean(losses)) if losses else float("inf")
            cv["log_loss_by_alpha"][str(alpha)] = loss
            if loss < best_loss:
                best_alpha, best_loss = alpha, loss
    else:
        best_alpha = 10.0
        cv["note"] = "too few groups for cross-validation: alpha 10"
    counts, base = _count_tables(rb, pb, lb, nv, y)
    return TransitionTable(counts, base, float(best_alpha), fit_spreads(reading, pos.astype(float)), int(len(frame)),
                           training_label, cv=cv)


# ---------------------------------------------------------------------------
# version 2: the model


@dataclass
class UpdaterModel:
    """Version 2. Trees over the reading, the prior and the context. PREDICTED output."""

    features: list[str]
    booster_text: str
    rounds: int
    params: dict[str, Any]
    temperature: float
    spreads: dict[str, float]
    rows: int
    training_label: str
    version: str = UPDATER_MODEL_VERSION
    cv: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    label: str = PREDICTED

    def band_probabilities(self, frame: pd.DataFrame) -> np.ndarray:
        import lightgbm as lgb

        if not hasattr(self, "_cached"):
            object.__setattr__(self, "_cached", lgb.Booster(model_str=self.booster_text))
        x = matrix(frame, self.features)
        raw = np.asarray(getattr(self, "_cached").predict(x, num_threads=3), dtype=float).reshape(len(frame), len(FINE_BANDS))
        return apply_temperature(raw, self.temperature)

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_cached", None)
        return data


def fit_model(frame: pd.DataFrame, *, outcome_col: str = "position_out", group_col: str = "search_group",
              training_label: str, features: Sequence[str] = MODEL_FEATURES, seed: int = SEED,
              calibration_share: float = 0.2, calibration_date_col: str | None = None,
              calibration_end: date | None = None, recent_days: float | None = CALIBRATION_RECENT_DAYS,
              recent_min_rows: int = CALIBRATION_RECENT_MIN_ROWS) -> UpdaterModel:
    """Fit version 2 on training rows (settings by grouped CV inside them; one temperature on a group-held calibration fold).

    With `calibration_date_col` and `calibration_end` the temperature is set on the recent slice of the calibration
    fold (placement.recent_slice); the trees are fitted on the same rows either way."""
    from .features import stable_share

    feats = check_model_features(features)
    pos, y = _outcome_bands(frame, outcome_col)
    if len(frame) < MIN_ROWS_FOR_MODEL or len(set(y)) < 2:
        raise UpdaterError(f"version 2 needs at least {MIN_ROWS_FOR_MODEL} rows with two outcome bands")
    groups = frame[group_col].astype(str).to_numpy() if group_col in frame else np.array([str(i) for i in range(len(frame))])
    is_cal = np.array([stable_share(g, seed + 13) < calibration_share for g in groups])
    if is_cal.all() or (~is_cal).sum() < MIN_ROWS_FOR_MODEL:
        is_cal = np.zeros(len(frame), dtype=bool)
    x = matrix(frame, feats)
    text, rounds, params, cv = fit_band_trees(x[~is_cal], y[~is_cal], groups[~is_cal], seed=seed)
    reading = pd.to_numeric(frame.get("reading_position"), errors="coerce").to_numpy(dtype=float) \
        if "reading_position" in frame else np.full(len(frame), np.nan)
    model = UpdaterModel(list(feats), text, rounds, params, 1.0, fit_spreads(reading[~is_cal], pos[~is_cal].astype(float)),
                         int((~is_cal).sum()), training_label, cv=cv)
    if is_cal.any():
        dates = frame[calibration_date_col].tolist() if calibration_date_col and calibration_date_col in frame else None
        use, slice_record = recent_slice(dates, calibration_end, days=recent_days, min_rows=recent_min_rows, base=is_cal)
        raw = model.band_probabilities(frame[use])  # temperature is still 1.0 here, so these are the raw probabilities
        model.temperature = fit_temperature(raw, y[use])
        model.calibration = {"temperature": model.temperature, "slice": slice_record,
                             "after_temperature": reliability(apply_temperature(raw, model.temperature), y[use])}
    return model


# ---------------------------------------------------------------------------
# the updater in use


def segment_mask(frame: pd.DataFrame, segment: str) -> np.ndarray:
    """True per row inside the segment (its column equals 1). A missing value is outside: unknown is not new."""
    col = SEGMENT_COLUMNS.get(segment, segment)
    if col not in frame:
        return np.zeros(len(frame), dtype=bool)
    return (pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float) == 1.0)


def segment_flags(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """segment_mask for every known segment."""
    return {name: segment_mask(frame, name) for name in SEGMENT_COLUMNS}


def prior_columns(prior_dists: np.ndarray) -> pd.DataFrame:
    """prior_p_<band>, prior_point and prior_band from 51-outcome prior distributions."""
    bands = band_mass(prior_dists)
    out = pd.DataFrame({f"prior_p_{b}": bands[:, k] for k, b in enumerate(FINE_BANDS)})
    points = [dist_median(d) for d in prior_dists]
    out["prior_point"] = np.array(points, dtype=float)
    out["prior_band"] = [fine_band_of(p) for p in points]
    return out


@dataclass
class Updater:
    """The updater in use: the table, the model when there is one, which one is used and why."""

    table: TransitionTable
    model: UpdaterModel | None
    in_use: str
    reason: str
    range_level: float = RANGE_COVERAGE
    comparison: dict[str, Any] = field(default_factory=dict)
    segment_rules: dict[str, str] = field(default_factory=dict)  # segment -> always_nt50, table or model
    segment_comparison: dict[str, Any] = field(default_factory=dict)
    range_calibration: dict[str, Any] = field(default_factory=dict)

    def version(self) -> str:
        chosen = self.table.version if self.in_use == "table" else self.model.version  # type: ignore[union-attr]
        return f"{chosen}:{self.sha256()[:12]}"

    def band_probabilities(self, frame: pd.DataFrame, prior_dists: np.ndarray, which: str | None = None) -> np.ndarray:
        """Updated band probabilities per row. `frame` holds the reading and context columns."""
        use = which or self.in_use
        if use == "model":
            if self.model is None:
                raise UpdaterError("no version 2 model is fitted")
            cols = prior_columns(prior_dists)
            work = frame.reset_index(drop=True).copy()
            for name in cols.columns:
                work[name] = cols[name].to_numpy()
            probs = self.model.band_probabilities(work)
            no_reading = ~np.isfinite(pd.to_numeric(work["reading_position"], errors="coerce").to_numpy(dtype=float))
            probs[no_reading] = band_mass(prior_dists[no_reading])
            return probs
        prior_bands = band_mass(prior_dists)
        cols = prior_columns(prior_dists)
        work = frame.reset_index(drop=True).copy()
        work["prior_band"] = cols["prior_band"].to_numpy()
        rb, pb, lb, nv = _table_keys(work)
        return np.array([self.table.update_bands(prior_bands[i], rb[i], pb[i], lb[i], nv[i]) for i in range(len(work))])

    def _version_distributions(self, frame: pd.DataFrame, prior_dists: np.ndarray, use: str) -> np.ndarray:
        probs = self.band_probabilities(frame, prior_dists, use)
        spreads = self.table.spreads if use == "table" else self.model.spreads  # type: ignore[union-attr]
        reading = pd.to_numeric(frame["reading_position"], errors="coerce").to_numpy(dtype=float)
        return shape_with_reading(probs, prior_dists, reading, spreads)

    def distributions(self, frame: pd.DataFrame, prior_dists: np.ndarray, which: str | None = None) -> np.ndarray:
        """Updated 51-outcome distributions. PREDICTED.

        `which` ("table" or "model") gives that version alone. Without it the version in use is applied, and on rows
        inside a segment whose rule names the other version (`segment_rules`) that version is used there instead.
        """
        if which is not None:
            return self._version_distributions(frame, prior_dists, which)
        out = self._version_distributions(frame, prior_dists, self.in_use)
        for segment, winner in self.segment_rules.items():
            if winner in ("table", "model") and winner != self.in_use:
                mask = segment_mask(frame, segment)
                if mask.any():
                    out[mask] = self._version_distributions(frame[mask].reset_index(drop=True), prior_dists[mask], winner)
        return out

    def point_rule(self, segments: Mapping[str, bool] | None = None) -> str | None:
        """The fixed point a segment rule imposes ("always_nt50"), or None when the distribution's median is the point."""
        for segment, inside in (segments or {}).items():
            if inside and self.segment_rules.get(segment) == "always_nt50":
                return "always_nt50"
        return None

    def forecast(self, dist: np.ndarray, *, site: str, search: str, stage: str, as_of: date,
                 notes: Mapping[str, Any] | None = None, segments: Mapping[str, bool] | None = None) -> Forecast:
        """A types.Forecast from one updated distribution. PREDICTED.

        `segments` says which segments the row is in (for example {"new_to_search": True}). When the rule for one of
        them is always NT50, the point is NT50; the range is the distribution's central interval widened to hold the
        point, and the label still comes from the distribution.
        """
        tail = (1.0 - self.range_level) / 2.0
        point = dist_median(dist)
        rule = self.point_rule(segments)
        if rule == "always_nt50":
            point = NT50
        bands = band_mass(dist)
        extra = {"updater_in_use": self.in_use, "updater_reason": self.reason}
        if rule:
            extra["segment_rule"] = rule
        extra.update(dict(notes or {}))
        return Forecast(site=site, search=search, stage=stage, as_of=as_of, point=int(point),
                        range_low=int(min(dist_quantile(dist, tail), point)),
                        range_high=int(max(dist_quantile(dist, 1 - tail), point)), label=label_for(bands),
                        model_version=self.version(), probs={b: float(v) for b, v in zip(FINE_BANDS, bands)}, notes=extra)

    def points(self, frame: pd.DataFrame, prior_dists: np.ndarray) -> list[int]:
        """The points of the updater in use, segment rules included, one per row."""
        dists = self.distributions(frame, prior_dists)
        flags = segment_flags(frame)
        out = []
        for i, dist in enumerate(dists):
            rule = self.point_rule({k: bool(v[i]) for k, v in flags.items()})
            out.append(NT50 if rule == "always_nt50" else dist_median(dist))
        return out

    def payload(self) -> dict[str, Any]:
        return {"table": self.table.payload(), "model": self.model.payload() if self.model else None,
                "in_use": self.in_use, "reason": self.reason, "range_level": self.range_level,
                "comparison": self.comparison, "segment_rules": dict(self.segment_rules),
                "segment_comparison": self.segment_comparison, "range_calibration": self.range_calibration}

    @classmethod
    def from_payload(cls, data: Mapping[str, Any]) -> "Updater":
        """The inverse of `payload`. Files written before version 2 have no segment rules and load without them."""
        table = dict(data["table"])
        model = dict(data["model"]) if data.get("model") else None
        for item in (table, model):
            if item is not None:
                item.pop("_cached", None)
        return cls(table=TransitionTable(**table), model=UpdaterModel(**model) if model else None,
                   in_use=str(data["in_use"]), reason=str(data["reason"]), range_level=float(data["range_level"]),
                   comparison=dict(data.get("comparison") or {}), segment_rules=dict(data.get("segment_rules") or {}),
                   segment_comparison=dict(data.get("segment_comparison") or {}),
                   range_calibration=dict(data.get("range_calibration") or {}))

    def sha256(self) -> str:
        text = json.dumps(_json_safe(self.payload()), sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.floating):
        return _json_safe(float(value))
    if isinstance(value, np.integer):
        return int(value)
    return value


def choose_version(table: TransitionTable, model: UpdaterModel | None, validation: pd.DataFrame,
                   prior_dists: np.ndarray, *, outcome_col: str = "position_out", search_col: str = "keyword_id",
                   site_col: str = "site", margin: float = VALIDATION_MARGIN, date_col: str | None = None,
                   end: date | None = None, recent_days: float | None = CALIBRATION_RECENT_DAYS,
                   recent_min_rows: int = CALIBRATION_RECENT_MIN_ROWS) -> Updater:
    """Decide which version is used, on validation rows held out of both fits, before any test row is scored.

    The model is used only when its hit rate under grading rule v1 minus the
    table's, on the same validation rows, has a cluster interval (over searches)
    whose lower bound is above `margin`. The range level is set on validation rows
    for the version chosen: all of them, or with `date_col` and `end` the recent
    slice only (placement.recent_slice, dates only). The comparison is kept on the updater.
    """
    updater = Updater(table, model, "table", "")
    if len(validation) == 0:
        updater.reason = "no validation rows: the table is used"
        return updater
    actual, _ = _outcome_bands(validation, outcome_col)
    dates = validation[date_col].tolist() if date_col and date_col in validation else None
    recent, slice_record = recent_slice(dates, end, days=recent_days, min_rows=recent_min_rows, rows=len(validation))
    updater.range_calibration = {"slice": slice_record}
    table_dists = updater.distributions(validation, prior_dists, "table")
    table_points = [dist_median(d) for d in table_dists]
    if model is None:
        updater.reason = "no version 2 model could be fitted: the table is used"
        updater.range_level = choose_range_level(table_dists[recent], actual[recent])
        return updater
    model_dists = updater.distributions(validation, prior_dists, "model")
    model_points = [dist_median(d) for d in model_dists]
    sites = validation[site_col].astype(str).tolist() if site_col in validation else [""] * len(validation)
    searches = validation[search_col].astype(str).tolist()
    cells = [GradedCell(site=s, search=k, point=int(p), actual=int(a))
             for s, k, p, a in zip(sites, searches, model_points, actual)]
    comparison = compare_to_baseline(cells, table_points, RULE_V1, cluster_by="search")
    updater.comparison = {"rows": int(len(validation)), "model_minus_table": comparison,
                          "rule": "model used only when the lower bound of model minus table is above "
                                  f"{margin} on validation rows"}
    low = comparison["difference_interval"][0]
    if low is not None and low > margin:
        updater.in_use = "model"
        updater.reason = (f"version 2 beat the table on {len(validation)} validation rows: "
                          f"{comparison['difference']:+.4f}, interval lower bound {low:+.4f}")
        updater.range_level = choose_range_level(model_dists[recent], actual[recent])
    else:
        updater.reason = ("version 2 did not beat the table on validation rows by a margin whose interval is above "
                          f"zero (difference {comparison['difference']}, lower bound {low}): the table is used")
        updater.range_level = choose_range_level(table_dists[recent], actual[recent])
    return updater


def choose_segment(updater: Updater, validation: pd.DataFrame, prior_dists: np.ndarray, *, segment: str = "new_to_search",
                   outcome_col: str = "position_out", search_col: str = "keyword_id", site_col: str = "site",
                   margin: float = VALIDATION_MARGIN) -> dict[str, Any]:
    """Choose the rule for one segment on VALIDATION rows only (SEGMENT_RULE), and record it on the updater.

    Candidates: always NT50, the table and the model (when fitted). The default is the version in use. A candidate
    replaces it on the segment only when its hit rate under grading rule v1 minus the default's, on the segment's
    validation rows, has a cluster interval over searches whose lower bound is above `margin`; among those the
    largest gain wins. Returns the record, which is also stored in `updater.segment_comparison[segment]`.
    """
    default = updater.in_use
    mask = segment_mask(validation, segment)
    record: dict[str, Any] = {"segment": segment, "rule": SEGMENT_RULE, "origin": SEGMENT_ORIGIN, "default": default,
                              "validation_rows": int(mask.sum()), "validation_hit": {}, "gain_over_default": {}}
    winner = default
    if mask.any():
        rows = validation[mask].reset_index(drop=True)
        prior = np.asarray(prior_dists)[mask]
        actual, _ = _outcome_bands(rows, outcome_col)
        points: dict[str, list[int]] = {"always_nt50": [NT50] * len(rows)}
        for version in ("table", "model"):
            if version == "model" and updater.model is None:
                continue
            points[version] = [dist_median(d) for d in updater.distributions(rows, prior, version)]
        sites = rows[site_col].astype(str).tolist() if site_col in rows else [""] * len(rows)
        searches = rows[search_col].astype(str).tolist()
        for name, pts in points.items():
            record["validation_hit"][name] = float(np.mean([RULE_V1.is_hit(int(p), int(a)) for p, a in zip(pts, actual)]))
        best_gain = None
        for name in SEGMENT_CANDIDATES:
            if name == default or name not in points:
                continue
            cells = [GradedCell(site=s, search=k, point=int(p), actual=int(a))
                     for s, k, p, a in zip(sites, searches, points[name], actual)]
            comp = compare_to_baseline(cells, points[default], RULE_V1, cluster_by="search")
            record["gain_over_default"][name] = comp
            low = comp["difference_interval"][0]
            if low is not None and low > margin and (best_gain is None or comp["difference"] > best_gain):
                winner, best_gain = name, comp["difference"]
        record["why"] = (f"{winner} beat the version in use ({default}) on {len(rows)} validation rows of the segment "
                         f"with a lower bound above {margin}" if winner != default else
                         f"no candidate beat the version in use ({default}) with a lower bound above {margin}")
    else:
        record["why"] = "no validation rows in the segment: the version in use stays"
    record["winner"] = winner
    updater.segment_rules[segment] = winner
    updater.segment_comparison[segment] = record
    return record


__all__ = [
    "LEAD_BUCKETS", "MODEL_FEATURES", "NO_READING", "Reading", "TransitionTable", "UPDATER_MODEL_VERSION",
    "UPDATER_TABLE_VERSION", "Updater", "UpdaterError", "UpdaterModel", "choose_segment", "choose_version", "fit_model",
    "fit_spreads", "fit_table", "lead_bucket", "prior_columns", "reading_band", "reading_columns", "segment_flags",
    "segment_mask", "shape_with_reading", "usable_readings", "SEGMENT_COLUMNS", "SEGMENT_RULE", "SEGMENT_ORIGIN",
]
