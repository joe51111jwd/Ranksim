"""Part 3: site strength. What dated free strength lists can and cannot say about Google positions.

What is REAL here: positions on dated panel pages (single country-level pulls),
and strength values copied from dated public lists, each passed through an
`AsOfGuard` keyed on its publication date (features.strength_for_dates). What
is PREDICTED here: the fitted strength score, page demand built from it,
topical strength, and the "site strength only" band forecaster. Nothing here
is SYNTHETIC except in tests.

The questions this module answers on REAL held-out searches at later dates
(docs/plan/IMPLEMENTATION_SPEC.md section 6, part 3):

1. Does strength order two results inside one page's top 10, top 20, top 50?
2. Does it order pairs that straddle position 10 (one in 1 to 10, one in 11 to
   50) and position 50 (one in 1 to 50, one in 51 to 100)?
3. Does the site's strength against the page's demand say "top 10" versus
   "51 to 100"?

Each single measure and the fitted score are scored on the same pairs. A pair
where a measure has no value, or two equal values, is a tie and scores 0.5.
Right, wrong and tied are reported apart.

The split (frozen in evals/forecast/strength/prereg.json before any held-out
number is computed): searches by search group (trainingset.search_group), a
fixed share to test by sha256(seed|group); time by a cutoff date, train pages
dated before it, test pages on or after it. Every model choice is made by
cross-validation inside train, grouped by search group.

Topical strength: for a site and any grouping of searches, a shrunk mean of how
far above or below its strength-expected place the site ranked on that group's
pages before a date, n/(n+k) toward zero with k fitted on training data. The
panel's searches are random long-tail searches with no niches, so the only
natural group here is the locale. The function takes any grouping so the paid
niche data can use it.

CLI:
  python -m aeo.ranksim.forecast.strength prereg   freeze the design (writes prereg.json)
  python -m aeo.ranksim.forecast.strength run      score it once (writes report.json)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .asof import as_date
from .config import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, NT50, TOP_DEPTH, forecast_paths
from .features import (
    LINK_COUNT_MEASURE,
    MEASURE_LABELS,
    MEASURES,
    MODEL_MEASURES,
    SURVIVORSHIP_LIMITED,
    _loo_quantile_and_gap,
    evals_dir,
    freeze_prereg,
    require_prereg,
    search_group,
    stable_share,
)
from .grading import FINE_BANDS, RULE_V1, GradedCell, compare_to_baseline, fine_band_of, summarize
from .stats import cluster_bootstrap_ratio
from .types import PREDICTED, REAL

STRENGTH_VERSION = "strength-v1"
SEED = 20261122
REPORT_NAME = "strength"

PAIR_KINDS: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {
    # kind: ((better site rank range), (worse site rank range)); both inclusive
    "top10": ((1, 10), (1, 10)),
    "top20": ((1, 20), (1, 20)),
    "top50": ((1, 50), (1, 50)),
    "straddle10": ((1, 10), (11, 50)),
    "straddle50": ((1, 50), (51, 100)),
}
MIN_DEPTH_TOP = 50
MIN_DEPTH_STRADDLE50 = 90
FIT_PAIRS_MAX = 300_000
FIT_C_GRID = (0.01, 0.1, 1.0, 10.0)
CV_FOLDS = 3
FORECASTER_TRAIN_ROWS_MAX = 400_000
FORECAST_WINDOW_DAYS = (28.0, 120.0)


@dataclass(frozen=True)
class SplitRule:
    """Searches by search group, then time. Sites are not split here (part 3 scores pages, not unseen sites)."""

    version: str = "strength-split-v1"
    search_test_share: float = 0.30
    site_test_share: float = 0.30  # used by trust.py; kept here so one rule object serves both
    seed: int = SEED
    time_cutoff: str = "2026-01-01"

    @property
    def cutoff(self) -> date:
        return date.fromisoformat(self.time_cutoff)

    def search_is_test(self, keyword: str) -> bool:
        return stable_share(search_group(keyword), self.seed) < self.search_test_share

    def site_is_test(self, site: str) -> bool:
        return stable_share(f"site|{site}", self.seed) < self.site_test_share

    def page_side(self, keyword: str, day: date) -> str:
        """train, test, or excluded (a train search after the cutoff or a test search before it)."""
        test_search = self.search_is_test(keyword)
        later = as_date(day) >= self.cutoff
        if test_search and later:
            return "test"
        if not test_search and not later:
            return "train"
        return "excluded"


DEFAULT_SPLIT = SplitRule()
# The rehearsal split runs the whole pipeline inside the TRAINING pages of DEFAULT_SPLIT only (train searches,
# dated before the cutoff), to debug code without touching any held-out outcome.
REHEARSAL_SPLIT = SplitRule(version="strength-rehearsal-v1", seed=SEED + 1, time_cutoff="2025-10-01")


def restrict_to_training(pos: pd.DataFrame, outer: SplitRule, inner: SplitRule) -> pd.DataFrame:
    """Keep only the outer split's training pages and give them the inner split's sides. For rehearsals."""
    keep = pos[pos["side"] == "train"].copy()
    sides = {}
    for kw, day in keep[["keyword", "snapshot_date"]].drop_duplicates().itertuples(index=False):
        sides[(kw, day)] = inner.page_side(kw, day)
    keep["side"] = [sides[(k, d)] for k, d in zip(keep["keyword"], keep["snapshot_date"])]
    return keep.reset_index(drop=True)


# ---------------------------------------------------------------------------
# pairs


def within_page_pairs(page_codes: np.ndarray, ranks: np.ndarray, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(better, worse) row indices of every pair of one kind inside each page.

    Rows must be sorted by page then rank (one row per site per page). The
    better site is the one with the smaller rank.
    """
    (lo_a, hi_a), (lo_b, hi_b) = PAIR_KINDS[kind]
    codes = np.asarray(page_codes)
    ranks = np.asarray(ranks, dtype=float)
    if len(codes) == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    if np.any(np.diff(codes) < 0):
        raise ValueError("rows must be sorted by page code")
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:], len(codes)]
    cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    out_a: list[np.ndarray] = []
    out_b: list[np.ndarray] = []
    for start, end in zip(starts, ends):
        n = int(end - start)
        if n < 2:
            continue
        if n not in cache:
            cache[n] = np.triu_indices(n, k=1)
        a, b = cache[n]
        ra = ranks[start + a]
        rb = ranks[start + b]
        keep = (ra >= lo_a) & (ra <= hi_a) & (rb >= lo_b) & (rb <= hi_b) & (ra < rb)
        out_a.append(start + a[keep])
        out_b.append(start + b[keep])
    if not out_a:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    return np.concatenate(out_a).astype(np.int64), np.concatenate(out_b).astype(np.int64)


def pair_scores(values: np.ndarray, better: np.ndarray, worse: np.ndarray) -> np.ndarray:
    """1 when the better-ranked site has the higher value, 0 when lower, 0.5 for a tie or a missing value."""
    va = np.asarray(values, dtype=float)[better]
    vb = np.asarray(values, dtype=float)[worse]
    out = np.full(len(better), 0.5)
    known = np.isfinite(va) & np.isfinite(vb)
    out[known & (va > vb)] = 1.0
    out[known & (va < vb)] = 0.0
    return out


def _per_cluster(values: np.ndarray, clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    keys, inverse = np.unique(clusters, return_inverse=True)
    sums = np.bincount(inverse, weights=values, minlength=len(keys))
    counts = np.bincount(inverse, minlength=len(keys)).astype(float)
    return keys, sums, counts


def pair_block(scores: np.ndarray, clusters: np.ndarray) -> dict[str, Any]:
    """Count, right, wrong, tied, rate (ties count half) and a cluster bootstrap interval over `clusters`."""
    count = int(len(scores))
    right = int(np.sum(scores == 1.0))
    wrong = int(np.sum(scores == 0.0))
    tied = count - right - wrong
    rate = float(np.mean(scores)) if count else None
    low = high = None
    if count:
        _, sums, counts = _per_cluster(scores, clusters)
        low, high = cluster_bootstrap_ratio(sums.tolist(), counts.tolist(), list(range(len(sums))))
    decided = right + wrong
    return {"count": count, "right": right, "wrong": wrong, "tied": tied, "rate": rate,
            "rate_decided_only": right / decided if decided else None,
            "cluster_interval": [low, high], "clusters": int(len(np.unique(clusters))) if count else 0}


def diff_block(first: np.ndarray, second: np.ndarray, clusters: np.ndarray) -> dict[str, Any]:
    """Mean of (first - second) on the same pairs, with a cluster bootstrap interval."""
    if not len(first):
        return {"count": 0, "difference": None, "difference_interval": [None, None]}
    gaps = np.asarray(first, dtype=float) - np.asarray(second, dtype=float)
    _, sums, counts = _per_cluster(gaps, clusters)
    low, high = cluster_bootstrap_ratio(sums.tolist(), counts.tolist(), list(range(len(sums))))
    return {"count": int(len(gaps)), "difference": float(np.mean(gaps)), "difference_interval": [low, high]}


def weighted_auc(scores: np.ndarray, labels: np.ndarray, weights: np.ndarray | None = None) -> float | None:
    """Area under the ROC curve with ties counted half, optionally with row weights. None when a class is empty."""
    s = np.asarray(scores, dtype=float)
    y = np.asarray(labels, dtype=bool)
    w = np.ones(len(s)) if weights is None else np.asarray(weights, dtype=float)
    keep = np.isfinite(s)
    s, y, w = s[keep], y[keep], w[keep]
    wp = w[y].sum()
    wn = w[~y].sum()
    if wp <= 0 or wn <= 0:
        return None
    uniq, inv = np.unique(s, return_inverse=True)
    pos = np.bincount(inv, weights=w * y, minlength=len(uniq))
    neg = np.bincount(inv, weights=w * ~y, minlength=len(uniq))
    below = np.cumsum(neg) - neg
    return float(np.sum(pos * (below + 0.5 * neg)) / (wp * wn))


def auc_block(scores: np.ndarray, labels: np.ndarray, clusters: np.ndarray, *, draws: int = BOOTSTRAP_DRAWS,
              seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """AUC with a cluster bootstrap interval (whole clusters resampled). Missing scores count half (as ties)."""
    s = np.asarray(scores, dtype=float).copy()
    fill = np.nanmedian(s) if np.isfinite(s).any() else 0.0
    missing = int(np.sum(~np.isfinite(s)))
    s[~np.isfinite(s)] = fill  # a missing value sits at the middle, so it neither helps nor hurts much
    point = weighted_auc(s, labels)
    keys, inverse = np.unique(clusters, return_inverse=True)
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        picks = rng.integers(0, len(keys), size=len(keys))
        mult = np.bincount(picks, minlength=len(keys)).astype(float)
        value = weighted_auc(s, labels, mult[inverse])
        if value is not None:
            values.append(value)
    low, high = (float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))) if values else (None, None)
    return {"auc": point, "cluster_interval": [low, high], "positives": int(np.sum(labels)),
            "negatives": int(np.sum(~np.asarray(labels, dtype=bool))), "missing_scores": missing, "clusters": int(len(keys))}


# ---------------------------------------------------------------------------
# the fitted strength score


@dataclass
class StrengthScore:
    """A linear site-strength score over the dated measures. PREDICTED. Fitted on training searches only.

    score = sum of coef * standardised measure (missing set to the training mean)
    plus coef * missing flag. Fitted as a pairwise logistic model: for two sites
    on one page, P(first ranks above second) = sigmoid(score_first - score_second).
    """

    measures: tuple[str, ...]
    means: dict[str, float]
    scales: dict[str, float]
    coef: dict[str, float]
    c_value: float
    version: str = STRENGTH_VERSION
    cv: dict[str, Any] = field(default_factory=dict)
    label: str = PREDICTED

    def design(self, frame: pd.DataFrame) -> np.ndarray:
        cols = []
        for m in self.measures:
            raw = pd.to_numeric(frame[m], errors="coerce").to_numpy(dtype=float) if m in frame else np.full(len(frame), np.nan)
            miss = ~np.isfinite(raw)
            z = (np.where(miss, self.means[m], raw) - self.means[m]) / self.scales[m]
            cols.append(z)
            cols.append(miss.astype(float))
        return np.column_stack(cols) if cols else np.zeros((len(frame), 0))

    def names(self) -> list[str]:
        out = []
        for m in self.measures:
            out += [m, f"{m}__missing"]
        return out

    def score(self, frame: pd.DataFrame) -> np.ndarray:
        x = self.design(frame)
        w = np.array([self.coef[n] for n in self.names()])
        return x @ w

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrengthScore":
        payload = dict(data)
        payload["measures"] = tuple(payload["measures"])
        return cls(**payload)


def _pair_design(x: np.ndarray, better: np.ndarray, worse: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    flip = rng.random(len(better)) < 0.5
    first = np.where(flip, worse, better)
    second = np.where(flip, better, worse)
    return x[first] - x[second], (~flip).astype(int)


def fit_strength_score(
    rows: pd.DataFrame,
    *,
    measures: Sequence[str] = MODEL_MEASURES,
    seed: int = SEED,
    max_pairs: int = FIT_PAIRS_MAX,
    c_grid: Sequence[float] = FIT_C_GRID,
) -> StrengthScore:
    """Fit the score on training page rows (page_code, rank100, keyword, measures), sorted by page and rank.

    Pairs: every pair inside each page's top 100, sampled to `max_pairs` (seeded).
    The penalty C is chosen by grouped cross-validation over search groups
    (pair accuracy), inside these training rows only.
    """
    from sklearn.linear_model import LogisticRegression

    for m in measures:
        if m in SURVIVORSHIP_LIMITED:
            raise ValueError(f"{m} is survivorship limited and cannot feed a fitted score")
    means = {m: float(np.nanmean(pd.to_numeric(rows[m], errors="coerce"))) for m in measures}
    scales = {}
    for m in measures:
        sd = float(np.nanstd(pd.to_numeric(rows[m], errors="coerce")))
        scales[m] = sd if sd > 0 else 1.0
    proto = StrengthScore(tuple(measures), means, scales, {}, 1.0)
    x = proto.design(rows)
    better, worse = within_page_pairs(rows["page_code"].to_numpy(), rows["rank100"].to_numpy(), "top50")
    b2, w2 = within_page_pairs(rows["page_code"].to_numpy(), rows["rank100"].to_numpy(), "straddle50")
    better = np.r_[better, b2]
    worse = np.r_[worse, w2]
    rng = np.random.default_rng(seed)
    if len(better) > max_pairs:
        pick = np.sort(rng.choice(len(better), size=max_pairs, replace=False))
        better, worse = better[pick], worse[pick]
    d, y = _pair_design(x, better, worse, seed)
    groups = np.array([stable_share(g, seed) for g in rows["search_group"].to_numpy()[better]])
    fold = np.minimum((groups * CV_FOLDS).astype(int), CV_FOLDS - 1)
    cv: dict[str, Any] = {}
    best_c, best_acc = c_grid[0], -1.0
    for c in c_grid:
        accs = []
        for k in range(CV_FOLDS):
            train, test = fold != k, fold == k
            if train.sum() < 10 or test.sum() < 10:
                continue
            model = LogisticRegression(C=c, fit_intercept=False, max_iter=500)
            model.fit(d[train], y[train])
            accs.append(float(np.mean((d[test] @ model.coef_[0] > 0) == (y[test] == 1))))
        acc = float(np.mean(accs)) if accs else float("nan")
        cv[str(c)] = acc
        if acc > best_acc:
            best_c, best_acc = c, acc
    model = LogisticRegression(C=best_c, fit_intercept=False, max_iter=500)
    model.fit(d, y)
    coef = dict(zip(proto.names(), [float(v) for v in model.coef_[0]]))
    return StrengthScore(tuple(measures), means, scales, coef, float(best_c),
                         cv={"pair_accuracy_by_C": cv, "pairs": int(len(d)), "folds": CV_FOLDS})


# ---------------------------------------------------------------------------
# page demand and the gap


BANDS_DEMAND = (("top10", 1, 10), ("t11_20", 11, 20), ("t21_50", 21, 50))


def page_demand(page: pd.DataFrame, *, score_col: str = "strength_score", exclude_site: str | None = None) -> dict[str, float]:
    """What a page's holders show: quantiles of the strength score in positions 1 to 10, 11 to 20, 21 to 50.

    `page` has site, rank100 and `score_col`. `exclude_site` leaves one site out
    (use it for the site being forecast). PREDICTED (built on the fitted score).
    """
    rows = page if exclude_site is None else page[page["site"] != exclude_site]
    out: dict[str, float] = {}
    for name, low, high in BANDS_DEMAND:
        vals = pd.to_numeric(rows.loc[(rows["rank100"] >= low) & (rows["rank100"] <= high), score_col], errors="coerce").dropna()
        for q in (0.25, 0.5, 0.75):
            out[f"demand_{name}_q{int(q * 100)}"] = float(vals.quantile(q)) if len(vals) else math.nan
    return out


def page_demand_frame(rows: pd.DataFrame, *, score_col: str = "strength_score") -> pd.DataFrame:
    """`page_demand` for many pages at once (no site left out). rows: _page, rank100, score_col. Indexed by _page."""
    out = pd.DataFrame(index=pd.Index(sorted(set(rows["_page"])), name="_page"))
    for name, low, high in BANDS_DEMAND:
        inside = rows[(rows["rank100"] >= low) & (rows["rank100"] <= high)]
        q = inside.groupby("_page")[score_col].quantile([0.25, 0.5, 0.75]).unstack()
        for quant in (0.25, 0.5, 0.75):
            out[f"demand_{name}_q{int(quant * 100)}"] = q[quant].reindex(out.index) if quant in q else np.nan
    return out


def strength_gap(site_score: float, page: pd.DataFrame, site: str | None = None, *, score_col: str = "strength_score") -> dict[str, float]:
    """How far the site sits above (positive) or below the page's holders. The site itself is left out.

    `gap_topK` = site score minus the median score of the other sites in the top
    K. `q_topK` = the share of those sites the site beats (ties half).
    """
    out: dict[str, float] = {}
    for name, depth in (("top10", 10), ("top20", 20), ("top50", 50)):
        mask = page["rank100"] <= depth
        vals = pd.to_numeric(page.loc[mask, score_col], errors="coerce").to_numpy(dtype=float)
        arr = np.sort(vals[np.isfinite(vals)])
        own_rows = page.loc[mask & (page["site"] == site), score_col] if site is not None else pd.Series(dtype=float)
        on_page = pd.to_numeric(own_rows, errors="coerce").to_numpy(dtype=float)
        inside = np.array([len(on_page) > 0 and np.isfinite(on_page[0])])
        page_value = np.array([on_page[0] if len(on_page) else np.nan])
        q, gap = _loo_quantile_and_gap(np.array([site_score], dtype=float), arr, inside, page_value)
        out[f"gap_{name}"] = float(gap[0])
        out[f"q_{name}"] = float(q[0])
    return out


def gap_columns(cells: pd.DataFrame, pages: pd.DataFrame, *, score_col: str = "strength_score", own_col: str = "own_score") -> pd.DataFrame:
    """`strength_gap` for many cells at once. cells: _page, site, cell_rank_in, own_col. pages: _page, site, rank100, score_col."""
    out = pd.DataFrame(index=cells.index)
    cols = {f"{k}_{t}": np.full(len(cells), np.nan) for k in ("gap", "q") for t in ("top10", "top20", "top50")}
    by_page = {k: g for k, g in pages.groupby("_page", sort=False)}
    for key, idx in cells.groupby("_page", sort=False).indices.items():
        page = by_page.get(key)
        if page is None:
            continue
        ranks = page["rank100"].to_numpy(dtype=float)
        vals = page[score_col].to_numpy(dtype=float)
        sub = cells.iloc[idx]
        own = sub[own_col].to_numpy(dtype=float)
        own_rank = pd.to_numeric(sub["cell_rank_in"], errors="coerce").to_numpy(dtype=float)
        first_row: dict[str, int] = {}
        for j, site_name in enumerate(page["site"].to_numpy()):
            first_row.setdefault(site_name, j)
        where = np.array([first_row.get(x, -1) for x in sub["site"].to_numpy()])
        on_page = np.where(where >= 0, vals[np.clip(where, 0, None)], np.nan)
        for name, depth in (("top10", 10), ("top20", 20), ("top50", 50)):
            mask = (ranks <= depth) & np.isfinite(vals)
            arr = np.sort(vals[mask])
            inside = (own_rank <= depth) & (where >= 0) & np.isfinite(on_page)
            q, gap = _loo_quantile_and_gap(own, arr, inside, on_page)
            cols[f"gap_{name}"][idx] = gap
            cols[f"q_{name}"][idx] = q
    for name, values in cols.items():
        out[name] = values
    return out


# ---------------------------------------------------------------------------
# topical strength


def page_residuals(page_codes: np.ndarray, ranks: np.ndarray, scores: np.ndarray) -> np.ndarray:
    """Per row: (expected place from the score minus real place) / sites on the page. Positive = ranks above its strength.

    Places are among the page's sites (1 = best). Rows must be sorted by page code.
    """
    frame = pd.DataFrame({"p": page_codes, "r": ranks, "s": scores})
    real = frame.groupby("p", sort=False)["r"].rank(method="average")
    expected = frame.groupby("p", sort=False)["s"].rank(method="average", ascending=False)
    n = frame.groupby("p", sort=False)["r"].transform("size")
    return ((expected - real) / n).to_numpy(dtype=float)


def topical_strength(
    history: pd.DataFrame,
    queries: pd.DataFrame,
    *,
    k: float,
    group_col: str = "group",
) -> pd.DataFrame:
    """Shrunk mean residual of a site on a group's pages STRICTLY before each query's time, other searches only.

    history: site, `group_col`, keyword_id, time, resid (REAL places, PREDICTED expectation).
    queries: site, `group_col`, keyword_id, time.
    Returns columns topical (n/(n+k) * mean, 0 when n is 0) and topical_n, aligned to queries.
    The group can be anything: a locale here, a niche in the paid data.
    """
    if k < 0:
        raise ValueError("k must be non-negative")
    hist = history[["site", group_col, "keyword_id", "time", "resid"]].dropna(subset=["resid"]).copy()
    hist["time"] = pd.to_datetime(hist["time"], utc=True)
    q = queries[["site", group_col, "keyword_id", "time"]].copy()
    q["time"] = pd.to_datetime(q["time"], utc=True)
    q["_row"] = np.arange(len(q))

    def cumulative(by: list[str]) -> tuple[np.ndarray, np.ndarray]:
        h = hist.sort_values("time").copy()
        # rows with the same time are summed first, so "strictly before" never sees part of a tie
        h = h.groupby(by + ["time"], sort=False)["resid"].agg(["sum", "size"]).reset_index().sort_values("time")
        h["cs"] = h.groupby(by, sort=False)["sum"].cumsum()
        h["cn"] = h.groupby(by, sort=False)["size"].cumsum()
        query = q[by + ["time", "_row"]].sort_values("time")
        merged = pd.merge_asof(query, h[by + ["time", "cs", "cn"]], on="time", by=by, direction="backward",
                               allow_exact_matches=False)
        sums = np.zeros(len(q))
        counts = np.zeros(len(q))
        sums[merged["_row"].to_numpy()] = merged["cs"].fillna(0.0).to_numpy()
        counts[merged["_row"].to_numpy()] = merged["cn"].fillna(0.0).to_numpy()
        return sums, counts

    s_group, n_group = cumulative(["site", group_col])
    s_own, n_own = cumulative(["site", group_col, "keyword_id"])
    total = s_group - s_own
    n = n_group - n_own
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = np.where(n > 0, total / np.maximum(n, 1), 0.0)
    shrunk = np.where(n > 0, n / (n + k) * mean, 0.0) if k > 0 else mean
    return pd.DataFrame({"topical": shrunk, "topical_n": n}, index=queries.index)


def fit_topical_k(history: pd.DataFrame, *, group_col: str = "group") -> dict[str, float]:
    """k = within-site variance / between-site variance of residuals per (site, group). Method of moments on training rows.

    The weight on a site's own mean is n/(n+k). Returns k and both variances.
    """
    h = history.dropna(subset=["resid"])
    g = h.groupby(["site", group_col])["resid"].agg(["mean", "var", "size"])
    total = float(g["size"].sum())
    pooled_within = float(((g["size"] - 1) * g["var"].fillna(0)).sum() / max(total - len(g), 1.0))
    grand = float(h["resid"].mean())
    groups = float(len(g))
    between_ss = float((g["size"] * (g["mean"] - grand) ** 2).sum())
    n0 = (total - float((g["size"] ** 2).sum()) / total) / max(groups - 1, 1.0)
    tau2 = max((between_ss / max(groups - 1, 1.0) - pooled_within) / max(n0, 1e-9), 1e-9)
    return {"k": pooled_within / tau2, "within_variance": pooled_within, "between_variance": tau2,
            "site_groups": int(groups), "rows": int(total)}


# ---------------------------------------------------------------------------
# the site-strength-only forecaster


FORECASTER_FEATURES: tuple[str, ...] = ("own_score", "gap_top10", "gap_top20", "gap_top50", "q_top10", "q_top20", "q_top50",
                                        "demand_top10_q50", "demand_t21_50_q50")
CANDIDATE_POINTS: tuple[int, ...] = (2, 7, 15, 25, NT50)


@dataclass
class StrengthOnlyForecaster:
    """Turns strength against page demand into a band call and a point. PREDICTED. A baseline for later reports.

    A multinomial logistic model gives a probability per fine band (1-3, 4-10,
    11-20, 21-50, NT50). The point is the candidate (2, 7, 15, 25, NT50) with
    the highest expected hit under grading rule v1, where the hit chance of a
    point in each real band is the share measured on the training rows.
    """

    features: tuple[str, ...]
    means: list[float]
    scales: list[float]
    coef: list[list[float]]
    intercept: list[float]
    classes: list[str]
    hit_table: dict[str, dict[str, float]]
    c_value: float
    version: str = "strength-only-forecaster-v1"
    cv: dict[str, Any] = field(default_factory=dict)
    label: str = PREDICTED

    def _x(self, frame: pd.DataFrame) -> np.ndarray:
        cols = []
        for name, mean, scale in zip(self.features, self.means, self.scales):
            raw = pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)
            cols.append((np.where(np.isfinite(raw), raw, mean) - mean) / scale)
        return np.column_stack(cols)

    def probabilities(self, frame: pd.DataFrame) -> np.ndarray:
        logits = self._x(frame) @ np.array(self.coef).T + np.array(self.intercept)
        logits -= logits.max(axis=1, keepdims=True)
        expo = np.exp(logits)
        return expo / expo.sum(axis=1, keepdims=True)

    def points(self, frame: pd.DataFrame) -> np.ndarray:
        probs = self.probabilities(frame)
        table = np.array([[self.hit_table[c].get(str(p), 0.0) for p in CANDIDATE_POINTS] for c in self.classes])
        expected = probs @ table
        return np.array(CANDIDATE_POINTS)[np.argmax(expected, axis=1)]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StrengthOnlyForecaster":
        payload = dict(data)
        payload["features"] = tuple(payload["features"])
        return cls(**payload)


def fit_strength_only(rows: pd.DataFrame, *, seed: int = SEED, c_grid: Sequence[float] = FIT_C_GRID) -> StrengthOnlyForecaster:
    """Fit on training rows with FORECASTER_FEATURES, position_out (REAL) and search_group. C by grouped CV log loss."""
    from sklearn.linear_model import LogisticRegression

    feats = list(FORECASTER_FEATURES)
    raw = np.column_stack([pd.to_numeric(rows[f], errors="coerce").to_numpy(dtype=float) for f in feats])
    means = [float(np.nanmean(raw[:, j])) if np.isfinite(raw[:, j]).any() else 0.0 for j in range(raw.shape[1])]
    scales = [float(np.nanstd(raw[:, j])) or 1.0 for j in range(raw.shape[1])]
    scales = [s if math.isfinite(s) and s > 0 else 1.0 for s in scales]
    x = np.column_stack([(np.where(np.isfinite(raw[:, j]), raw[:, j], means[j]) - means[j]) / scales[j] for j in range(raw.shape[1])])
    positions = rows["position_out"].to_numpy(dtype=int)
    y = np.array([fine_band_of(int(p)) for p in positions])
    classes = [c for c in FINE_BANDS if c in set(y)]
    groups = np.array([stable_share(g, seed) for g in rows["search_group"].to_numpy()])
    fold = np.minimum((groups * CV_FOLDS).astype(int), CV_FOLDS - 1)
    cv: dict[str, float] = {}
    best_c, best_loss = c_grid[0], float("inf")
    for c in c_grid:
        losses = []
        for k in range(CV_FOLDS):
            tr, te = fold != k, fold == k
            if len(set(y[tr])) < 2 or te.sum() == 0:
                continue
            model = LogisticRegression(C=c, max_iter=500)
            model.fit(x[tr], y[tr])
            proba = model.predict_proba(x[te])
            index = {cls: i for i, cls in enumerate(model.classes_)}
            picked = np.array([proba[i, index[v]] if v in index else 1e-6 for i, v in enumerate(y[te])])
            losses.append(float(-np.mean(np.log(np.clip(picked, 1e-9, 1.0)))))
        loss = float(np.mean(losses)) if losses else float("inf")
        cv[str(c)] = loss
        if loss < best_loss:
            best_c, best_loss = c, loss
    model = LogisticRegression(C=best_c, max_iter=500)
    model.fit(x, y)
    order = [list(model.classes_).index(c) for c in classes]
    hit_table: dict[str, dict[str, float]] = {}
    for cls in classes:
        real = positions[y == cls]
        hit_table[cls] = {str(p): float(np.mean([RULE_V1.is_hit(p, int(r)) for r in real])) if len(real) else 0.0
                          for p in CANDIDATE_POINTS}
    return StrengthOnlyForecaster(tuple(feats), means, scales, [list(map(float, model.coef_[i])) for i in order],
                                  [float(model.intercept_[i]) for i in order], classes, hit_table, float(best_c),
                                  cv={"log_loss_by_C": cv, "rows": int(len(y)), "folds": CV_FOLDS})


# ---------------------------------------------------------------------------
# the REAL evaluation


def prereg_payload(split: SplitRule = DEFAULT_SPLIT) -> dict[str, Any]:
    """Everything frozen before any held-out number: split, windows, thresholds, measures, baselines."""
    return {
        "name": "strength", "version": STRENGTH_VERSION, "split": asdict(split),
        "pages": {"dataset": "dfs_history", "poison_risk_pages": "dropped", "min_depth_top_and_straddle10": MIN_DEPTH_TOP,
                  "min_depth_straddle50": MIN_DEPTH_STRADDLE50, "test_pages": "test searches dated on or after the cutoff",
                  "train_pages": "train searches dated before the cutoff"},
        "pair_kinds": {k: list(map(list, v)) for k, v in PAIR_KINDS.items()},
        "ties": "a missing or equal value scores 0.5; right, wrong and tied are reported apart",
        "measures": list(MEASURES), "fitted_measures": list(MODEL_MEASURES),
        "survivorship_limited_not_fitted": list(SURVIVORSHIP_LIMITED),
        "absent_from_searched_list": "floor below the last listed value; not searched: missing",
        "fitted_score": {"form": "pairwise logistic, linear in standardised measures and missing flags",
                         "pairs": "top 50 and straddle 50 pairs of training pages", "max_pairs": FIT_PAIRS_MAX,
                         "C_grid": list(FIT_C_GRID), "cv": f"{CV_FOLDS} folds grouped by search group, inside train"},
        "gate_test": "rows in 1 to 10 (positive) against 51 to 100 (negative) on test pages of depth at least "
                     f"{MIN_DEPTH_STRADDLE50}; score = strength minus the median of the page's other top-10 sites; AUC",
        "topical": {"group": "locale", "history": "pages strictly before the page, other searches, same locale; "
                    "train rows see train searches only", "k": "method of moments on training rows",
                    "combination": "score + beta * topical, beta by pairwise logistic on training pages",
                    "test": "top 50 and straddle 10 pairs on test pages, score alone against score plus topical"},
        "forecaster": {"rows": "site in the top 100 of the input or outcome page, both pages at least 50 deep",
                       "window_days": list(FORECAST_WINDOW_DAYS), "train": "train searches, outcome before the cutoff",
                       "test": "test searches, input page on or after the cutoff", "features": list(FORECASTER_FEATURES),
                       "points": list(CANDIDATE_POINTS), "train_rows_max": FORECASTER_TRAIN_ROWS_MAX,
                       "baselines": ["always NT50", "persistence (position on the input page, NT50 when absent)"]},
        "intervals": {"method": "cluster bootstrap over searches", "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED},
        "grading_rule": RULE_V1.version, "grading_rule_hash": RULE_V1.rule_hash(),
    }


def load_page_rows(split: SplitRule, *, panel_dir: str | Path | None = None, log: Callable[[str], None] | None = None) -> pd.DataFrame:
    """One row per site per panel page (best rank), with the split side and the strength as of the page date. REAL."""
    from .features import cached_strength
    from .sources import panel as panel_mod

    rows = panel_mod.load_panel(["locale", "keyword", "keyword_id", "snapshot_time", "snapshot_date", "rank", "site",
                                 panel_mod.POISON_RISK_COLUMN], datasets=["dfs_history"], panel_dir=panel_dir)
    pos = panel_mod.site_positions(rows, drop_poison_risk=True)
    del rows
    pos = pos.sort_values(["keyword_id", "snapshot_time", "rank100"], kind="mergesort").reset_index(drop=True)
    sides = {}
    for kw, day in pos[["keyword", "snapshot_date"]].drop_duplicates().itertuples(index=False):
        sides[(kw, day)] = split.page_side(kw, day)
    pos["side"] = [sides[(k, d)] for k, d in zip(pos["keyword"], pos["snapshot_date"])]
    groups = {k: search_group(k) for k in set(pos["keyword"])}
    pos["search_group"] = pos["keyword"].map(groups)
    pos["as_of"] = pos["snapshot_date"]
    st = cached_strength(pos[["site", "as_of"]], log=log)
    st["as_of"] = st["as_of"].astype(object)
    pos["as_of"] = [as_date(d) for d in pos["as_of"]]
    pos = pos.merge(st[["site", "as_of", *MEASURES]], on=["site", "as_of"], how="left")
    pos = pos.sort_values(["keyword_id", "snapshot_time", "rank100"], kind="mergesort").reset_index(drop=True)
    pos["page_code"] = pos.groupby(["keyword_id", "snapshot_time"], sort=False).ngroup()
    pos = pos.sort_values(["page_code", "rank100"], kind="mergesort").reset_index(drop=True)
    return pos


def _page_subset(pos: pd.DataFrame, side: str, min_depth: int) -> pd.DataFrame:
    sub = pos[(pos["side"] == side) & (pos["snapshot_depth"] >= min_depth) & (pos["rank100"] <= 100)]
    return sub.reset_index(drop=True)


def pair_tests(test: pd.DataFrame, values: Mapping[str, np.ndarray]) -> dict[str, Any]:
    """Every pair kind on the test pages, each value column on the identical pairs, and each against the fitted score."""
    out: dict[str, Any] = {}
    for kind in PAIR_KINDS:
        min_depth = MIN_DEPTH_STRADDLE50 if kind == "straddle50" else MIN_DEPTH_TOP
        keep = test["snapshot_depth"].to_numpy() >= min_depth
        sub_index = np.flatnonzero(keep)
        better, worse = within_page_pairs(test["page_code"].to_numpy()[keep], test["rank100"].to_numpy()[keep], kind)
        better, worse = sub_index[better], sub_index[worse]
        clusters = test["kw_code"].to_numpy()[better]
        block: dict[str, Any] = {"pages": int(len(np.unique(test["page_code"].to_numpy()[better]))) if len(better) else 0}
        scores = {name: pair_scores(vals, better, worse) for name, vals in values.items()}
        for name, sc in scores.items():
            entry = pair_block(sc, clusters)
            if name != "strength_score" and "strength_score" in scores:
                entry["score_minus_this"] = diff_block(scores["strength_score"], sc, clusters)
            block[name] = entry
        out[kind] = block
    return out


def gate_tests(test: pd.DataFrame, values: Mapping[str, np.ndarray]) -> dict[str, Any]:
    """Test 3: rows in 1 to 10 against rows in 51 to 100, each value minus its page's other top-10 median."""
    keep = (test["snapshot_depth"].to_numpy() >= MIN_DEPTH_STRADDLE50)
    ranks = test["rank100"].to_numpy()
    rows = keep & ((ranks <= 10) | ((ranks >= 51) & (ranks <= 100)))
    out: dict[str, Any] = {}
    sub = test[keep].copy()
    sub["_page"] = sub["page_code"]
    for name, vals in values.items():
        sub["_v"] = np.asarray(vals, dtype=float)[keep]
        cells = sub[["_page", "site", "rank100", "_v"]].rename(columns={"rank100": "cell_rank_in", "_v": "own_score"})
        gaps = gap_columns(cells, sub.rename(columns={"_v": "score"}), score_col="score", own_col="own_score")
        label_rows = (sub["rank100"].to_numpy() <= 10) | ((sub["rank100"].to_numpy() >= 51) & (sub["rank100"].to_numpy() <= 100))
        labels = sub["rank100"].to_numpy()[label_rows] <= 10
        clusters = sub["kw_code"].to_numpy()[label_rows]
        out[name] = {
            "gap_to_page_top10_median": auc_block(gaps["gap_top10"].to_numpy()[label_rows], labels, clusters),
            "raw_value": auc_block(sub["_v"].to_numpy()[label_rows], labels, clusters),
        }
    out["rows"] = int(rows.sum())
    return out


def _split_counts(pos: pd.DataFrame) -> dict[str, Any]:
    pages = pos.drop_duplicates("page_code")
    out = {}
    for side in ("train", "test", "excluded"):
        sub = pages[pages["side"] == side]
        out[side] = {"pages": int(len(sub)), "searches": int(sub["keyword_id"].nunique()),
                     "search_groups": int(sub["search_group"].nunique())}
    train_groups = set(pages.loc[pages["side"] == "train", "search_group"])
    test_groups = set(pages.loc[pages["side"] == "test", "search_group"])
    out["search_groups_on_both_sides"] = len(train_groups & test_groups)
    return out


def topical_test(pos: pd.DataFrame, score: StrengthScore, log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Does locale topical strength add to the fitted score on held-out pages? REAL places, PREDICTED scores."""
    from sklearn.linear_model import LogisticRegression

    work = pos[(pos["snapshot_depth"] >= MIN_DEPTH_TOP) & (pos["rank100"] <= 100)].reset_index(drop=True)
    work["strength_score"] = score.score(work)
    work["resid"] = page_residuals(work["page_code"].to_numpy(), work["rank100"].to_numpy(dtype=float),
                                   work["strength_score"].to_numpy())
    work["time"] = work["snapshot_time"]
    work["group"] = work["locale"]
    train_hist = work[work["side"] == "train"]
    fitted = fit_topical_k(train_hist)
    k = fitted["k"]
    train = work[work["side"] == "train"].reset_index(drop=True)
    train_top = topical_strength(train_hist, train, k=k)
    test = work[work["side"] == "test"].reset_index(drop=True)
    test_top = topical_strength(work, test, k=k)  # every earlier page, other searches, same locale
    train["topical"] = train_top["topical"].to_numpy()
    test["topical"] = test_top["topical"].to_numpy()
    test["topical_n"] = test_top["topical_n"].to_numpy()
    # beta on training pages
    b, w = within_page_pairs(train["page_code"].to_numpy(), train["rank100"].to_numpy(), "top50")
    rng = np.random.default_rng(SEED)
    if len(b) > FIT_PAIRS_MAX:
        pick = np.sort(rng.choice(len(b), size=FIT_PAIRS_MAX, replace=False))
        b, w = b[pick], w[pick]
    x = train[["strength_score", "topical"]].to_numpy(dtype=float)
    d, y = _pair_design(x, b, w, SEED)
    model = LogisticRegression(C=1.0, fit_intercept=False, max_iter=500).fit(d, y)
    beta = float(model.coef_[0][1] / model.coef_[0][0]) if model.coef_[0][0] != 0 else 0.0
    combined = test["strength_score"].to_numpy() + beta * test["topical"].to_numpy()
    out: dict[str, Any] = {"k": fitted, "beta": beta, "coef_train": [float(v) for v in model.coef_[0]],
                           "test_rows_with_history": int(np.sum(test["topical_n"].to_numpy() > 0)),
                           "test_rows": int(len(test))}
    for kind in ("top50", "straddle10", "top10"):
        bb, ww = within_page_pairs(test["page_code"].to_numpy(), test["rank100"].to_numpy(), kind)
        clusters = test.groupby("keyword_id", sort=False).ngroup().to_numpy()[bb]
        alone = pair_scores(test["strength_score"].to_numpy(), bb, ww)
        both = pair_scores(combined, bb, ww)
        only_topical = pair_scores(np.where(test["topical_n"].to_numpy() > 0, test["topical"].to_numpy(), np.nan), bb, ww)
        out[kind] = {"score_alone": pair_block(alone, clusters), "score_plus_topical": pair_block(both, clusters),
                     "topical_alone_where_known": pair_block(only_topical, clusters),
                     "gain": diff_block(both, alone, clusters)}
    return out


def forecaster_test(pos: pd.DataFrame, score: StrengthScore, split: SplitRule,
                    log: Callable[[str], None] | None = None) -> tuple[dict[str, Any], StrengthOnlyForecaster]:
    """Fit the strength-only forecaster on train rows and grade it once on test rows, beside the baselines."""
    from .features import build_cells, cached_strength

    pages = pos.drop_duplicates("page_code")[["keyword_id", "keyword", "locale", "snapshot_time", "snapshot_depth"]]
    meta = pages.rename(columns={"snapshot_depth": "depth"})
    site_rows = pos[["keyword_id", "snapshot_time", "site", "rank100"]]
    cells = build_cells(site_rows, meta, FORECAST_WINDOW_DAYS)
    cells = cells[cells["depth_ok"]].reset_index(drop=True)
    cut = pd.Timestamp(split.cutoff, tz="UTC")
    test_kw = {k: split.search_is_test(k) for k in set(cells["keyword"])}
    is_test = cells["keyword"].map(test_kw).to_numpy(dtype=bool)
    train_mask = (~is_test) & (cells["outcome_time"] < cut).to_numpy()
    test_mask = is_test & (cells["input_time"] >= cut).to_numpy()
    groups = {k: search_group(k) for k in set(cells["keyword"])}
    cells["search_group"] = cells["keyword"].map(groups)
    train = cells[train_mask]
    if len(train) > FORECASTER_TRAIN_ROWS_MAX:
        keep_kw = sorted(set(train["keyword_id"]), key=lambda k: stable_share(k, SEED))
        chosen, total = set(), 0
        sizes = train.groupby("keyword_id").size()
        for kw in keep_kw:
            if total >= FORECASTER_TRAIN_ROWS_MAX:
                break
            chosen.add(kw)
            total += int(sizes[kw])
        train = train[train["keyword_id"].isin(chosen)]
    test = cells[test_mask]
    both = pd.concat([train.assign(_side="train"), test.assign(_side="test")], ignore_index=True)
    st = cached_strength(both[["site", "as_of"]], log=log)
    st["as_of"] = [as_date(d) for d in st["as_of"]]
    both["as_of"] = [as_date(d) for d in both["as_of"]]
    both = both.merge(st[["site", "as_of", *MEASURES]], on=["site", "as_of"], how="left")
    both["own_score"] = score.score(both)
    # input pages, with scores as of their own date
    inp = pos[["keyword_id", "snapshot_time", "site", "rank100", *MEASURES]].copy()
    inp["strength_score"] = score.score(inp)
    inp["_page"] = inp["keyword_id"].astype(str) + "|" + inp["snapshot_time"].astype(str)
    both["_page"] = both["keyword_id"].astype(str) + "|" + both["input_time"].astype(str)
    needed = set(both["_page"])
    inp = inp[inp["_page"].isin(needed)]
    both["cell_rank_in"] = both["rank100_in"]
    gaps = gap_columns(both, inp, score_col="strength_score", own_col="own_score")
    both = pd.concat([both, gaps], axis=1)
    demand = page_demand_frame(inp)
    both = both.merge(demand, left_on="_page", right_index=True, how="left")
    tr = both[both["_side"] == "train"].reset_index(drop=True)
    te = both[both["_side"] == "test"].reset_index(drop=True)
    model = fit_strength_only(tr)
    points = model.points(te)
    cells_graded = [GradedCell(site=s, search=k, point=int(p), actual=int(a))
                    for s, k, p, a in zip(te["site"], te["keyword_id"], points, te["position_out"])]
    persistence = te["position_in"].astype(int).tolist()
    report = {
        "label": REAL, "model_output": PREDICTED,
        "rows": {"train": int(len(tr)), "test": int(len(te)), "test_searches": int(te["keyword_id"].nunique()),
                 "test_sites": int(te["site"].nunique())},
        "real_band_shares_test": te["position_out"].map(lambda p: fine_band_of(int(p))).value_counts(normalize=True).to_dict(),
        "model": summarize(cells_graded, cluster_by="search"),
        "vs_always_nt50": compare_to_baseline(cells_graded, [NT50] * len(cells_graded), cluster_by="search"),
        "vs_persistence": compare_to_baseline(cells_graded, persistence, cluster_by="search"),
        "always_nt50": summarize([GradedCell(c.site, c.search, NT50, c.actual) for c in cells_graded], cluster_by="search")["hit"],
        "persistence": summarize([GradedCell(c.site, c.search, p, c.actual) for c, p in zip(cells_graded, persistence)],
                                 cluster_by="search")["hit"],
        "points_used": {str(k): int(v) for k, v in zip(*np.unique(points, return_counts=True))},
        "note": ("Rows are sites already in the top 100 of the input or outcome page. Every cell exists because the site "
                 "appeared on the search. A brand-new site's upload-day cells are not in this panel."),
    }
    return report, model


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, np.floating):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


def verdict_lines(report: Mapping[str, Any]) -> list[str]:
    """Plain sentences on what strength can and cannot do, read off the report's numbers."""
    lines = []
    pairs = report.get("pairs", {})
    for kind, text in (("top10", "inside one page's top 10"), ("top20", "inside the top 20"), ("top50", "inside the top 50"),
                       ("straddle10", "across position 10 (1 to 10 against 11 to 50)"),
                       ("straddle50", "across position 50 (1 to 50 against 51 to 100)")):
        block = pairs.get(kind, {}).get("strength_score")
        if not block or block.get("rate") is None:
            continue
        low, high = block["cluster_interval"]
        can = low is not None and low > 0.5
        strong = low is not None and low >= 0.6
        word = "orders pairs clearly" if strong else ("orders pairs a little better than chance" if can else "does not order pairs better than chance")
        lines.append(f"Fitted strength {text}: {block['rate']:.3f} of {block['count']} pairs, interval {low:.3f} to {high:.3f}. It {word}.")
    gate = report.get("gate", {}).get("strength_score", {}).get("gap_to_page_top10_median")
    if gate and gate.get("auc") is not None:
        low, high = gate["cluster_interval"]
        lines.append(f"Top 10 against 51 to 100 from strength minus page demand: AUC {gate['auc']:.3f}, interval {low:.3f} to {high:.3f}.")
    return lines


def run(split: SplitRule = DEFAULT_SPLIT, *, out_dir: str | Path | None = None, log: Callable[[str], None] | None = None,
        rehearsal: bool = False) -> dict[str, Any]:
    """Score the frozen design once on REAL held-out searches at later dates. Writes report.json.

    With `rehearsal`, only the training pages are read and split again inside
    (REHEARSAL_SPLIT). No held-out outcome is touched, nothing is written under
    evals/, and the report says REHEARSAL. It is for debugging the code.
    """
    if rehearsal:
        folder = Path(out_dir) if out_dir else forecast_paths().cache / "rehearsal" / REPORT_NAME
        digest = "rehearsal"
        pos = restrict_to_training(load_page_rows(split, log=log), split, REHEARSAL_SPLIT)
        split = REHEARSAL_SPLIT
    else:
        folder = Path(out_dir) if out_dir else evals_dir(REPORT_NAME)
        digest = require_prereg(folder / "prereg.json", prereg_payload(split))
        pos = load_page_rows(split, log=log)
    if log:
        log(f"page rows {len(pos)}")
    train = _page_subset(pos, "train", MIN_DEPTH_TOP)
    test = _page_subset(pos, "test", MIN_DEPTH_TOP)
    score = fit_strength_score(train)
    test = test.copy()
    test["kw_code"] = test.groupby("keyword_id", sort=False).ngroup().to_numpy()
    test["strength_score"] = score.score(test)
    values = {m: test[m].to_numpy(dtype=float) for m in MEASURES}
    values["strength_score"] = test["strength_score"].to_numpy()
    if log:
        log("pair tests")
    pairs = pair_tests(test, values)
    if log:
        log("gate tests")
    gate = gate_tests(test, values)
    if log:
        log("topical")
    topical = topical_test(pos, score, log=log)
    if log:
        log("forecaster")
    forecaster, model = forecaster_test(pos, score, split, log=log)
    report: dict[str, Any] = {
        "label": REAL, "name": REPORT_NAME, "version": STRENGTH_VERSION, "prereg_sha256": digest,
        "rehearsal": bool(rehearsal),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what_is_real": "Positions on dated panel pages (single country-level pulls) and list values as published.",
        "what_is_predicted": "The fitted strength score, page demand, topical strength and the strength-only forecaster.",
        "split": _split_counts(pos), "measure_labels": MEASURE_LABELS, "link_count_measure": LINK_COUNT_MEASURE,
        "fitted_score": score.to_dict(), "pairs": pairs, "gate": gate, "topical": topical, "forecaster": forecaster,
        "forecaster_model": model.to_dict(),
        "limits": [
            "Pages are single country-level pulls. Two pulls a day apart agree within 2 places on about 0.85 of top-10 sites, so labels are noisy.",
            "Open PageRank holds only today's top 10 million domains. Its pairs use only sites that survived, and it is not in the fitted score.",
            "The panel holds only sites that appeared on each search. It cannot test a brand-new site on searches it never ranks for.",
        ],
    }
    report["verdict"] = verdict_lines(report)
    report = _clean(report)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    if rehearsal:
        return report
    models = forecast_paths().models / "strength"
    models.mkdir(parents=True, exist_ok=True)
    (models / f"{STRENGTH_VERSION}-score.json").write_text(json.dumps(score.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    (models / f"{STRENGTH_VERSION}-forecaster.json").write_text(json.dumps(model.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return report


def load_strength_score(path: str | Path | None = None) -> StrengthScore:
    target = Path(path) if path else forecast_paths().models / "strength" / f"{STRENGTH_VERSION}-score.json"
    return StrengthScore.from_dict(json.loads(Path(target).read_text(encoding="utf-8")))


def load_strength_forecaster(path: str | Path | None = None) -> StrengthOnlyForecaster:
    target = Path(path) if path else forecast_paths().models / "strength" / f"{STRENGTH_VERSION}-forecaster.json"
    return StrengthOnlyForecaster.from_dict(json.loads(Path(target).read_text(encoding="utf-8")))


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.strength")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prereg", help="freeze the design before scoring")
    run_cmd = sub.add_parser("run", help="score the frozen design once on REAL held-out data")
    run_cmd.add_argument("--rehearsal", action="store_true", help="training pages only, nested split, nothing written to evals")
    args = parser.parse_args(argv)
    folder = evals_dir(REPORT_NAME)
    if args.command == "prereg":
        digest = freeze_prereg(folder / "prereg.json", prereg_payload())
        print(json.dumps({"prereg": str(folder / "prereg.json"), "sha256": digest}, indent=2))
        return 0
    report = run(log=_log, rehearsal=args.rehearsal)
    print("\n".join(report.get("verdict", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
