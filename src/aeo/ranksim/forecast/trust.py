"""Part 5: the trust model. How far above or below its content a site really ranks.

What is REAL here: positions on dated panel pages (single country-level
pulls), result titles as Google showed them, and dated strength list values
(each through an `AsOfGuard`). What is PREDICTED here: the content score of
each title (content.py, untrained, frozen), the content-expected place built
from it, and every trust model output. Rows from `synth.World` are SYNTHETIC.

The label, exactly as the plan defines it. For each page, every site's best
result title is scored against the search with ONE content backend (the
backend and model revision are stored on every row; two backends are never
mixed inside one comparison). The content-expected place is the site's place
when the page's sites are sorted by that score. The residual is the
content-expected place minus the real place among the same sites. A positive
residual means the site ranks above what its content alone earns. The real
place, the content-expected place, the residual and the backend are stored
apart. The content model is untrained and frozen (no fold can see a label),
so every content score is out of fold by construction (audit B3); its version
is hashed into the label table.

The model: gradient-boosted trees, with a ridge baseline, predicting the
residual from features knowable at the page date and NOT derived from
rankings: dated strength measures, start-date evidence, site size, top-level
domain, and agency-form footprint fields when present (None on the panel). Two
feature sets are reported apart: free lists only, and free lists plus the
provider's main_domain_rank (taken from the site's newest panel row dated
strictly before the page, never from the page itself).

Marker 2, on the panel. Held-out sites AND held-out searches (no site and no
search on both sides), later dates than training. Pairs are two held-out
sites on the same held-out page whose labels differ by MORE than the frozen
threshold (trust-v1 was frozen and scored with "at least"; every later design
version uses "more than", as the spec says, and records it in its prereg). The model is right when it puts the site with the higher label
first; a tie scores 0.5. Each single strength measure and a within-page
shuffle are scored on the identical pairs.

What this is and is not. It is the plan's marker 2 definition applied to sites
that already rank somewhere in a public sample, with result titles standing in
for page content. It is not yet the plan's 20,000 new sites with traffic
curves. Those need the paid pulls.

CLI:
  python -m aeo.ranksim.forecast.trust prereg [--design-version k --reason "..."]
  python -m aeo.ranksim.forecast.trust run [--rehearsal] [--design-version k]
A design version is scored once: version 1 (report.json) is scored, so a rerun
is version k >= 2 (prereg-v<k>.json committed and pushed first, then report-v<k>.json).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from .asof import as_date
from .chain import canonical_json, sha256_text
from .config import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, forecast_paths
from .features import (
    LINK_COUNT_MEASURE,
    MEASURES,
    MODEL_MEASURES,
    START_COLUMNS,
    SYNTHETIC_FOOTPRINT_COLUMNS,
    _loo_quantile_and_gap,
    check_model_features,
    AlreadyScored,
    begin_scored_run,
    code_provenance,
    evals_dir,
    excluded_site_mask,
    finish_scored_run,
    freeze_prereg,
    frozen_reason,
    prereg_file,
    search_group,
    stable_share,
    versioned_payload,
)
from .strength import DEFAULT_SPLIT, SplitRule, pair_block, pair_scores
from .types import PREDICTED, REAL, SYNTHETIC

TRUST_VERSION = "trust-v1"
SEED = 20261122
REPORT_NAME = "trust"
LABEL_TOP = 50  # the label is built among the sites in a page's top 50
LABEL_MIN_DEPTH = 50
PAIR_THRESHOLD = 10.0  # places of residual between the two sites of a pair
PRIMARY = {"backend": "cross_encoder", "language": "en"}
SECONDARY = ({"backend": "lexical", "language": "en"}, {"backend": "lexical", "language": None})
NUM_THREADS = 3
CV_FOLDS = 3
GBT_GRID: tuple[dict[str, Any], ...] = (
    {"num_leaves": 7, "min_child_samples": 200},
    {"num_leaves": 15, "min_child_samples": 200},
    {"num_leaves": 31, "min_child_samples": 400},
    {"num_leaves": 15, "min_child_samples": 50},
)
GBT_FIXED: dict[str, Any] = {"learning_rate": 0.05, "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8,
                             "reg_lambda": 1.0, "max_rounds": 600, "early_stopping_rounds": 30}
RIDGE_ALPHAS: tuple[float, ...] = (1.0, 10.0, 100.0, 1000.0)
TOP_TLDS = 40
TRAIN_ROWS_MAX = 400_000  # training label rows per run, whole pages kept in sha256(seed|page) order

FREE_FEATURES: tuple[str, ...] = (
    *(f"st_{m}" for m in MODEL_MEASURES), "st_cc_present", "st_crux_present", "st_tranco_present",
    "sd_age_days", "sd_uncertainty_days", "sd_left_censored", "sd_age_lower_bound_days", "sd_no_evidence",
    "sd_registration_known", "sd_possibly_reregistered", "tld_code", *SYNTHETIC_FOOTPRINT_COLUMNS,
)
MDR_FEATURES: tuple[str, ...] = (*FREE_FEATURES, "mdr_before")
FEATURE_SETS: dict[str, tuple[str, ...]] = {"free": FREE_FEATURES, "free_plus_mdr": MDR_FEATURES}
CATEGORICAL: tuple[str, ...] = ("tld_code",)
SINGLE_MEASURES: tuple[str, ...] = tuple(f"st_{m}" for m in MEASURES)

LABEL_COLUMNS: tuple[str, ...] = ("keyword_id", "keyword", "locale", "snapshot_time", "snapshot_date", "site", "rank100",
                                  "real_place", "content_score", "content_place", "residual", "n_page", "content_backend",
                                  "content_model", "label_table_version")


# ---------------------------------------------------------------------------
# the label


def tld_of(site: str) -> str:
    """The last label of the registrable domain (co.uk gives uk)."""
    text = str(site or "").strip(".").lower()
    return text.rsplit(".", 1)[-1] if "." in text else text


def label_table(page_rows: pd.DataFrame, scorer: Any, *, top: int = LABEL_TOP) -> pd.DataFrame:
    """The trust label per (site, page). REAL places, PREDICTED content scores.

    `page_rows`: one row per site per page (keyword_id, keyword, snapshot_time,
    site, rank100 = best organic rank, title = that result's title). Only sites
    with rank100 <= top count. A site whose title is missing gets no content
    place and no residual (NaN), and does not shift other sites' content
    places: the content order is over the sites that have a title, and the real
    place is over the same sites.
    """
    from .features import title_content_scores

    rows = page_rows[page_rows["rank100"] <= top].copy()
    rows = rows.sort_values(["keyword_id", "snapshot_time", "rank100"], kind="mergesort").reset_index(drop=True)
    scores, mode, model = title_content_scores(rows["keyword"].astype(str).tolist(), rows["title"].tolist(), scorer)
    rows["content_score"] = scores
    has = np.isfinite(scores)
    keys = ["keyword_id", "snapshot_time"]
    scored = rows[has].copy()
    scored["real_place"] = scored.groupby(keys, sort=False)["rank100"].rank(method="first")
    scored["content_place"] = scored.groupby(keys, sort=False)["content_score"].rank(method="average", ascending=False)
    scored["n_page"] = scored.groupby(keys, sort=False)["rank100"].transform("size")
    rows = rows.join(scored[["real_place", "content_place", "n_page"]])
    rows["residual"] = rows["content_place"] - rows["real_place"]
    rows["content_backend"] = mode
    rows["content_model"] = model
    rows["label_table_version"] = sha256_text(canonical_json({"version": TRUST_VERSION, "top": top, "backend": mode,
                                                               "model": model, "input": "title only"}))[:16]
    keep = [c for c in LABEL_COLUMNS if c in rows.columns]
    return rows[keep + [c for c in rows.columns if c not in keep and c != "title"]]


# ---------------------------------------------------------------------------
# the model


@dataclass
class TrustModel:
    """A fitted trust model: LightGBM trees and a ridge baseline over one feature set. PREDICTED output.

    `predict` gives the trees' residual estimate, `predict_ridge` the ridge one.
    Higher means the site is expected to rank further above its content.
    """

    version: str
    feature_set: str
    features: list[str]
    categorical: list[str]
    booster_text: str
    rounds: int
    gbt_params: dict[str, Any]
    ridge: dict[str, Any]
    label_backend: str
    label_model: str
    tld_levels: list[str]
    cv: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    label: str = PREDICTED

    def _booster(self) -> Any:
        import lightgbm as lgb

        if not hasattr(self, "_cached"):
            object.__setattr__(self, "_cached", lgb.Booster(model_str=self.booster_text))
        return getattr(self, "_cached")

    def matrix(self, frame: pd.DataFrame) -> pd.DataFrame:
        return design_matrix(frame, self.features, self.tld_levels)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = self.matrix(frame)
        return np.asarray(self._booster().predict(x, num_threads=NUM_THREADS), dtype=float)

    def predict_ridge(self, frame: pd.DataFrame) -> np.ndarray:
        return ridge_predict(self.ridge, self.matrix(frame))

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("_cached", None)
        return data


def tld_levels_from(sites: Sequence[str], top: int = TOP_TLDS) -> list[str]:
    counts = pd.Series([tld_of(s) for s in sites]).value_counts()
    return sorted(counts.index[:top].tolist())


def design_matrix(frame: pd.DataFrame, features: Sequence[str], tld_levels: Sequence[str]) -> pd.DataFrame:
    """Model inputs in a fixed column order. Missing stays NaN. tld_code is the TLD's index in the frozen levels (other = len(levels))."""
    out = pd.DataFrame(index=frame.index)
    level = {name: i for i, name in enumerate(tld_levels)}
    for name in features:
        if name == "tld_code":
            sites = frame["site"].astype(str) if "site" in frame else pd.Series([""] * len(frame), index=frame.index)
            out[name] = [float(level.get(tld_of(s), len(tld_levels))) for s in sites]  # other TLDs share the last code
        elif name in frame:
            out[name] = pd.to_numeric(frame[name], errors="coerce").astype(float)
        else:
            out[name] = np.nan
    return out


def ridge_fit(x: pd.DataFrame, y: np.ndarray, alpha: float, categorical: Sequence[str], tld_levels: Sequence[str]) -> dict[str, Any]:
    """Ridge on standardised numeric columns, missing flags, and one-hot TLD. Missing set to the training mean."""
    from sklearn.linear_model import Ridge

    design, spec = _ridge_design(x, None, categorical, tld_levels)
    model = Ridge(alpha=alpha).fit(design, y)
    spec.update({"coef": [float(v) for v in model.coef_], "intercept": float(model.intercept_), "alpha": float(alpha)})
    return spec


def _ridge_design(x: pd.DataFrame, spec: Mapping[str, Any] | None, categorical: Sequence[str],
                  tld_levels: Sequence[str]) -> tuple[np.ndarray, dict[str, Any]]:
    numeric = [c for c in x.columns if c not in categorical]
    if spec is None:
        means = {c: float(np.nanmean(x[c])) if np.isfinite(x[c]).any() else 0.0 for c in numeric}
        scales = {c: (float(np.nanstd(x[c])) or 1.0) if np.isfinite(x[c]).any() else 1.0 for c in numeric}
        scales = {c: v if math.isfinite(v) and v > 0 else 1.0 for c, v in scales.items()}
        spec = {"numeric": numeric, "means": means, "scales": scales, "categorical": list(categorical),
                "tld_levels": list(tld_levels)}
    cols = []
    for c in spec["numeric"]:
        raw = x[c].to_numpy(dtype=float)
        miss = ~np.isfinite(raw)
        cols.append((np.where(miss, spec["means"][c], raw) - spec["means"][c]) / spec["scales"][c])
        cols.append(miss.astype(float))
    for c in spec["categorical"]:
        codes = x[c].to_numpy(dtype=float)
        for i in range(len(spec["tld_levels"])):
            cols.append((codes == i).astype(float))
    return (np.column_stack(cols) if cols else np.zeros((len(x), 0))), dict(spec)


def ridge_predict(spec: Mapping[str, Any], x: pd.DataFrame) -> np.ndarray:
    design, _ = _ridge_design(x, spec, spec["categorical"], spec["tld_levels"])
    return design @ np.array(spec["coef"]) + float(spec["intercept"])


def _folds(groups: Sequence[str], seed: int, folds: int = CV_FOLDS) -> np.ndarray:
    share = np.array([stable_share(str(g), seed) for g in groups])
    return np.minimum((share * folds).astype(int), folds - 1)


def _lgb_params(extra: Mapping[str, Any], seed: int, rows: int) -> dict[str, Any]:
    params = {
        "objective": "regression", "verbosity": -1, "num_threads": NUM_THREADS, "deterministic": True,
        "force_row_wise": True, "seed": seed, "bagging_seed": seed, "feature_fraction_seed": seed, "data_random_seed": seed,
        "learning_rate": GBT_FIXED["learning_rate"], "bagging_fraction": GBT_FIXED["subsample"],
        "bagging_freq": GBT_FIXED["subsample_freq"], "feature_fraction": GBT_FIXED["colsample_bytree"],
        "lambda_l2": GBT_FIXED["reg_lambda"], "num_leaves": int(extra["num_leaves"]),
        "min_data_in_leaf": int(min(extra["min_child_samples"], max(5, rows // 20))),
    }
    return params


def fit_trust_model(
    frame: pd.DataFrame,
    *,
    feature_set: str,
    features: Sequence[str] | None = None,
    label_col: str = "residual",
    group_col: str = "search_group",
    seed: int = SEED,
    label_backend: str = "",
    label_model: str = "",
    grid: Sequence[Mapping[str, Any]] = GBT_GRID,
    alphas: Sequence[float] = RIDGE_ALPHAS,
) -> TrustModel:
    """Fit trees and ridge on training rows only. Settings by grouped cross-validation inside these rows.

    Columns that may not feed a model (Open PageRank, outcomes, identifiers) are refused.
    A feature column that is missing everywhere (the footprint on the panel) is dropped
    and the drop is recorded in `cv["dropped_all_missing"]`.
    """
    import lightgbm as lgb

    chosen = list(features) if features is not None else list(FEATURE_SETS[feature_set])
    check_model_features(chosen)
    data = frame[np.isfinite(pd.to_numeric(frame[label_col], errors="coerce"))].reset_index(drop=True)
    levels = tld_levels_from(data["site"].astype(str).tolist()) if "tld_code" in chosen else []
    x_all = design_matrix(data, chosen, levels)
    dropped = [c for c in chosen if c != "tld_code" and not np.isfinite(x_all[c].to_numpy()).any()]
    kept = [c for c in chosen if c not in dropped]
    x = x_all[kept]
    y = data[label_col].to_numpy(dtype=float)
    fold = _folds(data[group_col].tolist(), seed)
    cats = [c for c in CATEGORICAL if c in kept]
    cv: dict[str, Any] = {"gbt": {}, "ridge": {}, "dropped_all_missing": dropped, "rows": int(len(y))}
    best, best_loss, best_rounds = None, float("inf"), 50
    for setting in grid:
        params = _lgb_params(setting, seed, len(y))
        losses, rounds = [], []
        for k in range(CV_FOLDS):
            tr, te = fold != k, fold == k
            if tr.sum() < 20 or te.sum() < 5:
                continue
            dtrain = lgb.Dataset(x[tr], y[tr], categorical_feature=cats or "auto", free_raw_data=False)
            dvalid = lgb.Dataset(x[te], y[te], categorical_feature=cats or "auto", reference=dtrain, free_raw_data=False)
            booster = lgb.train(params, dtrain, num_boost_round=GBT_FIXED["max_rounds"], valid_sets=[dvalid],
                                callbacks=[lgb.early_stopping(GBT_FIXED["early_stopping_rounds"], verbose=False)])
            pred = booster.predict(x[te], num_iteration=booster.best_iteration)
            losses.append(float(np.mean((pred - y[te]) ** 2)))
            rounds.append(int(booster.best_iteration or GBT_FIXED["max_rounds"]))
        loss = float(np.mean(losses)) if losses else float("inf")
        cv["gbt"][json.dumps(dict(setting), sort_keys=True)] = {"mse": loss, "rounds": rounds}
        if loss < best_loss:
            best, best_loss, best_rounds = dict(setting), loss, max(10, int(np.mean(rounds)) if rounds else 50)
    assert best is not None
    params = _lgb_params(best, seed, len(y))
    booster = lgb.train(params, lgb.Dataset(x, y, categorical_feature=cats or "auto"), num_boost_round=best_rounds)
    best_alpha, best_ridge = alphas[0], float("inf")
    for alpha in alphas:
        losses = []
        for k in range(CV_FOLDS):
            tr, te = fold != k, fold == k
            if tr.sum() < 20 or te.sum() < 5:
                continue
            spec = ridge_fit(x[tr], y[tr], alpha, cats, levels)
            losses.append(float(np.mean((ridge_predict(spec, x[te]) - y[te]) ** 2)))
        loss = float(np.mean(losses)) if losses else float("inf")
        cv["ridge"][str(alpha)] = loss
        if loss < best_ridge:
            best_alpha, best_ridge = alpha, loss
    ridge = ridge_fit(x, y, best_alpha, cats, levels)
    cv["label_variance"] = float(np.var(y))
    return TrustModel(version=TRUST_VERSION, feature_set=feature_set, features=kept, categorical=cats,
                      booster_text=booster.model_to_string(), rounds=int(best_rounds), gbt_params=params, ridge=ridge,
                      label_backend=label_backend, label_model=label_model, tld_levels=levels, cv=cv)


def code_revision() -> str | None:
    """The repository HEAD commit, read only. None when git is not available.

    HEAD alone does not say whether the code that ran was committed: manifests written by `run` also carry
    features.code_provenance() (dirty flag, changed files, sha256 of every forecast source file)."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, capture_output=True,
                             text=True, timeout=10, check=False)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def default_models_dir() -> Path:
    return forecast_paths().models / "trust"


def trust_model_name(model: TrustModel, manifest: Mapping[str, Any]) -> str:
    name = f"{model.version}-{model.feature_set}-{model.label_backend or 'unknown'}"
    if manifest.get("language"):
        name += f"-{manifest['language']}"
    elif "language" in manifest:
        name += "-all"
    return name


def save_trust_model(model: TrustModel, manifest: Mapping[str, Any], models_dir: str | Path | None = None, *,
                     overwrite: bool = False) -> tuple[Path, str]:
    """Write the model JSON (never committed: it lives under data/) and a manifest with its sha256. Returns (path, sha256).

    An existing model file is never replaced (the backtest loads it by name): a new fit gets a new version name.
    """
    folder = Path(models_dir) if models_dir else default_models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = trust_model_name(model, manifest)
    payload = model.payload()
    payload["manifest"] = dict(manifest)
    text = json.dumps(payload, sort_keys=True, default=str)
    path = folder / f"{name}.json"
    manifest_path = folder / f"{name}.manifest.json"
    if not overwrite and (path.exists() or manifest_path.exists()):
        raise AlreadyScored(f"{path} exists. A model file is never replaced: fit it under a new version name.")
    with open(path, "w" if overwrite else "x", encoding="utf-8") as handle:
        handle.write(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with open(manifest_path, "w" if overwrite else "x", encoding="utf-8") as handle:
        handle.write(json.dumps({**manifest, "model_file": path.name, "sha256": digest}, indent=2, sort_keys=True, default=str))
    return path, digest


def load_trust_model(path: str | Path) -> TrustModel:
    """Load a saved model and check its sha256 against the manifest beside it."""
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    manifest_path = target.with_name(target.name.replace(".json", ".manifest.json"))
    if manifest_path.is_file():
        expected = json.loads(manifest_path.read_text(encoding="utf-8")).get("sha256")
        if expected and hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            raise ValueError(f"{target} does not match the sha256 in its manifest")
    data = json.loads(text)
    data.pop("_cached", None)
    return TrustModel(**data)


# ---------------------------------------------------------------------------
# demand and gap


def trust_demand(page: pd.DataFrame, *, trust_col: str = "trust", exclude_site: str | None = None) -> dict[str, float]:
    """The PREDICTED trust of the sites holding the page's top 10: 25th, 50th and 75th percentile."""
    rows = page if exclude_site is None else page[page["site"] != exclude_site]
    vals = pd.to_numeric(rows.loc[rows["rank100"] <= 10, trust_col], errors="coerce").dropna()
    return {f"trust_demand_q{int(q * 100)}": float(vals.quantile(q)) if len(vals) else math.nan for q in (0.25, 0.5, 0.75)}


def trust_gap(site_trust: float, page: pd.DataFrame, site: str | None = None, *, trust_col: str = "trust") -> dict[str, float]:
    """Site trust minus the median trust of the page's other top-10 sites, and the share of them it beats."""
    mask = page["rank100"] <= 10
    vals = pd.to_numeric(page.loc[mask, trust_col], errors="coerce").to_numpy(dtype=float)
    arr = np.sort(vals[np.isfinite(vals)])
    own_rows = page.loc[mask & (page["site"] == site), trust_col] if site is not None else pd.Series(dtype=float)
    on_page = pd.to_numeric(own_rows, errors="coerce").to_numpy(dtype=float)
    inside = np.array([len(on_page) > 0 and np.isfinite(on_page[0])])
    page_value = np.array([on_page[0] if len(on_page) else np.nan])
    q, gap = _loo_quantile_and_gap(np.array([site_trust], dtype=float), arr, inside, page_value)
    return {"trust_gap_top10": float(gap[0]), "trust_q_top10": float(q[0])}


# ---------------------------------------------------------------------------
# pairs and intervals


def label_pairs(frame: pd.DataFrame, threshold: float = PAIR_THRESHOLD, *, strict: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """(higher-label, lower-label) row indices: two rows on the same page whose residuals differ by MORE than threshold.

    IMPLEMENTATION_SPEC section 6 part 5: labels that "differ by more than a frozen threshold". Residuals are whole
    and half places, so a gap of exactly the threshold is common and is left out. `strict=False` reproduces the
    trust-v1 pair set (at least the threshold), which was scored once under that rule and is kept only for it.
    """
    data = frame.reset_index(drop=True)
    codes = data.groupby(["keyword_id", "snapshot_time"], sort=False).ngroup().to_numpy()
    res = data["residual"].to_numpy(dtype=float)
    order = np.argsort(codes, kind="mergesort")
    codes_sorted = codes[order]
    starts = np.flatnonzero(np.r_[True, codes_sorted[1:] != codes_sorted[:-1]])
    ends = np.r_[starts[1:], len(codes_sorted)]
    hi_list, lo_list = [], []
    for s, e in zip(starts, ends):
        idx = order[s:e]
        if len(idx) < 2:
            continue
        a, b = np.triu_indices(len(idx), k=1)
        ra, rb = res[idx[a]], res[idx[b]]
        gap = np.abs(ra - rb)
        ok = np.isfinite(ra) & np.isfinite(rb) & ((gap > threshold) if strict else (gap >= threshold))
        a, b, ra, rb = a[ok], b[ok], ra[ok], rb[ok]
        hi = np.where(ra > rb, idx[a], idx[b])
        lo = np.where(ra > rb, idx[b], idx[a])
        hi_list.append(hi)
        lo_list.append(lo)
    if not hi_list:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    return np.concatenate(hi_list).astype(np.int64), np.concatenate(lo_list).astype(np.int64)


def two_way_interval(
    values: np.ndarray,
    searches: np.ndarray,
    site_a: np.ndarray,
    site_b: np.ndarray,
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
    chunk: int = 50,
) -> tuple[float | None, float | None]:
    """95% interval of the mean of `values` under a two-way (pigeonhole) bootstrap over searches and sites.

    Each draw gives every search and every site a Poisson(1) weight; a pair's
    weight is its search weight times the weights of its two sites (Owen 2007).
    This is mildly conservative. None when there are fewer than 2 searches or 2 sites.
    """
    v = np.asarray(values, dtype=float)
    if len(v) == 0:
        return None, None
    s_keys, s_idx = np.unique(searches, return_inverse=True)
    site_keys, site_idx = np.unique(np.r_[site_a, site_b], return_inverse=True)
    a_idx, b_idx = site_idx[: len(v)], site_idx[len(v):]
    if len(s_keys) < 2 or len(site_keys) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    stats = []
    for start in range(0, draws, chunk):
        n = min(chunk, draws - start)
        ws = rng.poisson(1.0, size=(n, len(s_keys))).astype(float)
        wt = rng.poisson(1.0, size=(n, len(site_keys))).astype(float)
        w = ws[:, s_idx] * wt[:, a_idx] * wt[:, b_idx]
        tot = w.sum(axis=1)
        good = tot > 0
        stats.extend(((w[good] @ v) / tot[good]).tolist())
    low, high = np.quantile(stats, [0.025, 0.975])
    return float(low), float(high)


def marker_block(scores: np.ndarray, searches: np.ndarray, site_a: np.ndarray, site_b: np.ndarray) -> dict[str, Any]:
    """Counts and rate with intervals by search, by site (first site of the pair) and two-way."""
    block = pair_block(scores, searches)
    block["search_interval"] = block.pop("cluster_interval")
    block["site_interval"] = pair_block(scores, site_a)["cluster_interval"]
    block["two_way_interval"] = list(two_way_interval(scores, searches, site_a, site_b))
    block["sites"] = int(len(np.unique(np.r_[site_a, site_b]))) if len(scores) else 0
    return block


def gain_block(model: np.ndarray, base: np.ndarray, searches: np.ndarray, site_a: np.ndarray, site_b: np.ndarray) -> dict[str, Any]:
    from .strength import diff_block

    gaps = np.asarray(model, dtype=float) - np.asarray(base, dtype=float)
    out = diff_block(model, base, searches)
    out["search_interval"] = out.pop("difference_interval")
    out["difference_interval"] = list(two_way_interval(gaps, searches, site_a, site_b))
    out["interval_method"] = "two-way bootstrap over searches and sites"
    return out


def evaluate_pairs(
    frame: pd.DataFrame,
    model_values: Mapping[str, np.ndarray],
    *,
    threshold: float = PAIR_THRESHOLD,
    single_measures: Sequence[str] = SINGLE_MEASURES,
    young_col: str | None = "sd_young",
    seed: int = SEED,
    strict: bool = True,
) -> dict[str, Any]:
    """Marker 2 pairs on the held-out frame: each model, each single measure and a shuffle on the IDENTICAL pairs.

    A pair needs labels that differ by more than `threshold` (`strict=False`: at least, the trust-v1 rule)."""
    data = frame.reset_index(drop=True)
    hi, lo = label_pairs(data, threshold, strict=strict)
    searches = data["keyword_id"].to_numpy()[hi]
    sa = data["site"].to_numpy()[hi]
    sb = data["site"].to_numpy()[lo]
    rng = np.random.default_rng(seed)
    shuffle = rng.random(len(data))
    out: dict[str, Any] = {"threshold_places": threshold, "pair_comparison": ">" if strict else ">=", "pairs": int(len(hi)),
                           "pages": int(data.iloc[hi].groupby(["keyword_id", "snapshot_time"]).ngroups) if len(hi) else 0}
    singles: dict[str, Any] = {}
    single_scores: dict[str, np.ndarray] = {}
    for name in single_measures:
        if name not in data:
            continue
        sc = pair_scores(data[name].to_numpy(dtype=float), hi, lo)
        single_scores[name] = sc
        singles[name] = marker_block(sc, searches, sa, sb)
    shuffle_scores = pair_scores(shuffle, hi, lo)
    out["single_measures"] = singles
    out["within_page_shuffle"] = marker_block(shuffle_scores, searches, sa, sb)
    out["models"] = {}
    for name, values in model_values.items():
        sc = pair_scores(np.asarray(values, dtype=float), hi, lo)
        free_best = max((m for m in single_scores if "mdr" not in m and "opr" not in m),
                        key=lambda m: float(np.mean(single_scores[m])) if len(single_scores[m]) else -1, default=None)
        entry: dict[str, Any] = {"pairs": marker_block(sc, searches, sa, sb)}
        if free_best is not None:
            entry["best_single_measure"] = free_best
            entry["gain_over_best_single_measure"] = gain_block(sc, single_scores[free_best], searches, sa, sb)
        if f"st_{LINK_COUNT_MEASURE}" in single_scores:
            entry["gain_over_link_count"] = gain_block(sc, single_scores[f"st_{LINK_COUNT_MEASURE}"], searches, sa, sb)
        if young_col and young_col in data:
            young = (data[young_col].to_numpy(dtype=float)[hi] == 1.0) & (data[young_col].to_numpy(dtype=float)[lo] == 1.0)
            unc = np.r_[data["sd_uncertainty_days"].to_numpy(dtype=float)[hi][young],
                        data["sd_uncertainty_days"].to_numpy(dtype=float)[lo][young]] if "sd_uncertainty_days" in data else np.array([])
            young_entry: dict[str, Any] = {"pairs": int(young.sum()),
                                           "median_start_uncertainty_days": float(np.median(unc)) if len(unc) else None}
            if young.sum():
                young_entry["model"] = marker_block(sc[young], searches[young], sa[young], sb[young])
                if free_best is not None:
                    young_entry["best_single_measure"] = marker_block(single_scores[free_best][young], searches[young], sa[young], sb[young])
                    young_entry["gain_over_best_single_measure"] = gain_block(sc[young], single_scores[free_best][young],
                                                                              searches[young], sa[young], sb[young])
            entry["both_sites_young"] = young_entry
        out["models"][name] = entry
    return out


def marker2_report(evaluation: Mapping[str, Any], model_name: str, *, label: str = REAL) -> dict[str, Any]:
    """The dict shape markers.evaluate_marker(2, report) reads, for one model."""
    entry = evaluation["models"][model_name]
    pairs = entry["pairs"]
    gain = entry.get("gain_over_best_single_measure") or {}
    return {
        "label": label,
        "pairs": {"count": pairs["count"], "rate": pairs["rate"], "two_way_interval": pairs["two_way_interval"]},
        "gain_over_link_count": {"difference": gain.get("difference"), "difference_interval": gain.get("difference_interval"),
                                 "baseline": entry.get("best_single_measure"),
                                 "note": "the best single strength measure on the same pairs, at least as strict as link count"},
        "counts": {"right": pairs["right"], "wrong": pairs["wrong"], "tied": pairs["tied"], "missing": 0},
    }


# ---------------------------------------------------------------------------
# the REAL data


def prereg_payload(split: SplitRule = DEFAULT_SPLIT, *, version: int = 1, reason: str | None = None) -> dict[str, Any]:
    """The frozen marker 2 design. Version 1 is the file already frozen (unchanged, its sha256 still reproduces).
    Version k >= 2 also records its version name, the reason for the new version, the pair comparison
    (labels differ by MORE than the threshold), and that RDAP start evidence is left out (no dated history)."""
    payload = versioned_payload(_prereg_payload_v1(split), name="trust", version=version, reason=reason)
    if int(version) >= 2:
        payload["pair_comparison"] = "labels differ by more than the threshold (>)"
        payload["start_evidence"] = ("Common Crawl first release only; RDAP is left out (today-only, no dated history, "
                                     "its presence encodes survival to the fetch day)")
    return payload


def pairs_strict(version: int) -> bool:
    """trust-v1 was frozen and scored with pairs at least the threshold apart; every later version uses more than."""
    return int(version) >= 2


def _prereg_payload_v1(split: SplitRule) -> dict[str, Any]:
    return {
        "name": "trust", "version": TRUST_VERSION, "split": asdict(split),
        "split_rule": ("searches by search group (share to test by sha256), sites by sha256(seed|site|name) share to test, "
                       "train pages dated before the cutoff on train searches with train sites only; test pages on or after "
                       "the cutoff on test searches; pairs only between two test sites"),
        "label": {"definition": "content-expected place minus real place among the page's top 50 sites with a title",
                  "content_input": "the site's best result title only", "top": LABEL_TOP, "min_page_depth": LABEL_MIN_DEPTH},
        "primary": PRIMARY, "secondary": list(SECONDARY),
        "pair_threshold_places": PAIR_THRESHOLD, "ties": "0.5",
        "train_rows_max": TRAIN_ROWS_MAX, "train_row_sampling": "whole pages in sha256(seed|page) order",
        "feature_sets": {k: list(v) for k, v in FEATURE_SETS.items()},
        "mdr_rule": "main_domain_rank from the site's newest panel row dated strictly before the page; 0 is missing; higher is stronger",
        "models": {"primary": "gradient-boosted trees (LightGBM)", "baseline": "ridge",
                   "gbt_grid": [dict(g) for g in GBT_GRID], "gbt_fixed": GBT_FIXED, "ridge_alphas": list(RIDGE_ALPHAS),
                   "cv": f"{CV_FOLDS} folds grouped by search group inside train"},
        "baselines_on_identical_pairs": [*SINGLE_MEASURES, "within-page shuffle"],
        "marker_gain": "model minus the best free single measure on the same pairs (chosen on the test pairs, which favours the baseline)",
        "young": "both sites have a start estimate (not left censored) under 730 days before the page date",
        "intervals": {"two_way": "Poisson pigeonhole bootstrap over searches and sites", "one_way": "cluster bootstrap by search, and by site",
                      "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED},
    }


def mdr_before(rows: pd.DataFrame, queries: pd.DataFrame) -> np.ndarray:
    """The site's main_domain_rank from its newest panel row dated STRICTLY before each query time. 0 counts as missing."""
    hist = rows[["site", "snapshot_time", "main_domain_rank"]].copy()
    hist["main_domain_rank"] = pd.to_numeric(hist["main_domain_rank"], errors="coerce")
    hist.loc[hist["main_domain_rank"] <= 0, "main_domain_rank"] = np.nan
    hist = hist.dropna().groupby(["site", "snapshot_time"], sort=False)["main_domain_rank"].median().reset_index()
    hist["snapshot_time"] = pd.to_datetime(hist["snapshot_time"], utc=True)
    hist = hist.sort_values("snapshot_time")
    q = queries[["site", "snapshot_time"]].copy()
    q["snapshot_time"] = pd.to_datetime(q["snapshot_time"], utc=True)
    q["_row"] = np.arange(len(q))
    merged = pd.merge_asof(q.sort_values("snapshot_time"), hist, on="snapshot_time", by="site", direction="backward",
                           allow_exact_matches=False)
    out = np.full(len(q), np.nan)
    out[merged["_row"].to_numpy()] = merged["main_domain_rank"].to_numpy(dtype=float)
    return out


def load_label_rows(split: SplitRule, *, sites_split: SplitRule | None = None, log: Callable[[str], None] | None = None,
                    restrict: Callable[[pd.DataFrame], pd.DataFrame] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Best row per site per page on train and test pages, with titles, strength, start dates and mdr_before. REAL.

    Returns (page rows, all panel rows used for mdr). `restrict` (rehearsal) may cut the pages first.
    """
    from .features import StartEvidence, cached_strength, start_features_frame
    from .sources import panel as panel_mod

    rows = panel_mod.load_panel(["locale", "language", "keyword", "keyword_id", "snapshot_time", "snapshot_date", "rank", "site",
                                 "title", "main_domain_rank", panel_mod.POISON_RISK_COLUMN], datasets=["dfs_history"])
    rows = rows[~rows[panel_mod.POISON_RISK_COLUMN].fillna(False).astype(bool)].drop(columns=[panel_mod.POISON_RISK_COLUMN])
    rows = rows[~excluded_site_mask(rows["site"])]
    mdr_rows = rows[["site", "snapshot_time", "main_domain_rank"]]
    depth = rows.groupby(["keyword_id", "snapshot_time"], sort=False)["rank"].transform("max")
    rows = rows.assign(depth=depth)
    rows = rows.sort_values(["keyword_id", "snapshot_time", "rank"], kind="mergesort")
    best = rows.drop_duplicates(["keyword_id", "snapshot_time", "site"], keep="first").rename(columns={"rank": "rank100"})
    del rows
    sides = {}
    for kw, day in best[["keyword", "snapshot_date"]].drop_duplicates().itertuples(index=False):
        sides[(kw, day)] = split.page_side(kw, day)
    best["side"] = [sides[(k, d)] for k, d in zip(best["keyword"], best["snapshot_date"])]
    best = best[best["side"] != "excluded"]
    if restrict is not None:
        best = restrict(best)
    best = best[(best["depth"] >= LABEL_MIN_DEPTH)].reset_index(drop=True)
    who = sites_split or split
    test_site = {s: who.site_is_test(s) for s in set(best["site"])}
    best["site_test"] = best["site"].map(test_site).astype(bool)
    groups = {k: search_group(k) for k in set(best["keyword"])}
    best["search_group"] = best["keyword"].map(groups)
    best["as_of"] = [as_date(d) for d in best["snapshot_date"]]
    if log:
        log(f"label rows before strength: {len(best)}")
    st = cached_strength(best[["site", "as_of"]], log=log)
    st["as_of"] = [as_date(d) for d in st["as_of"]]
    st = st.rename(columns={c: f"st_{c}" for c in st.columns if c not in ("site", "as_of")})
    best = best.merge(st, on=["site", "as_of"], how="left")
    evidence = StartEvidence.load(sorted(set(best["site"])))
    site_dates = best[["site", "as_of"]].drop_duplicates().reset_index(drop=True)
    sd = start_features_frame(site_dates, evidence)
    sd["site"] = site_dates["site"].to_numpy()
    sd["as_of"] = site_dates["as_of"].to_numpy()
    best = best.merge(sd, on=["site", "as_of"], how="left")
    best["mdr_before"] = mdr_before(mdr_rows, best[["site", "snapshot_time"]])
    for col in SYNTHETIC_FOOTPRINT_COLUMNS:
        best[col] = np.nan  # the agency form does not exist for panel sites
    return best.reset_index(drop=True), mdr_rows


def sample_pages(frame: pd.DataFrame, max_rows: int, seed: int = SEED) -> pd.DataFrame:
    """Whole pages in sha256(seed|keyword_id|time) order until `max_rows` rows. Deterministic."""
    if len(frame) <= max_rows:
        return frame.reset_index(drop=True)
    keys = frame["keyword_id"].astype(str) + "|" + frame["snapshot_time"].astype(str)
    sizes = keys.value_counts()
    order = sorted(sizes.index, key=lambda k: stable_share(k, seed))
    chosen, total = set(), 0
    for key in order:
        if total >= max_rows:
            break
        chosen.add(key)
        total += int(sizes[key])
    return frame[keys.isin(chosen).to_numpy()].reset_index(drop=True)


def _scorer(backend: str) -> Any:
    from .content import ContentScorer

    return ContentScorer(backend)


def run_one(best: pd.DataFrame, config: Mapping[str, Any], *, models_dir: Path | None, manifest_base: Mapping[str, Any],
            log: Callable[[str], None] | None = None, save: bool = True, version: int = 1) -> dict[str, Any]:
    """One label backend and language: labels, two feature sets, trees and ridge, pairs on held-out sites and searches."""
    language = config.get("language")
    rows = best if language is None else best[best["language"] == language]
    train_pages = rows[(rows["side"] == "train")]
    test_pages = rows[(rows["side"] == "test")]
    scorer = _scorer(config["backend"])
    if log:
        log(f"labels {config}: {len(train_pages)} train rows, {len(test_pages)} test rows")
    train_labels = label_table(train_pages, scorer)
    test_labels = label_table(test_pages, scorer)
    train = sample_pages(train_labels[~train_labels["site_test"]], TRAIN_ROWS_MAX)
    test = test_labels[test_labels["site_test"]].reset_index(drop=True)
    train_ok = train[np.isfinite(train["residual"])]
    overlap_sites = len(set(train_ok["site"]) & set(test["site"]))
    overlap_groups = len(set(train_ok["search_group"]) & set(test["search_group"]))
    if overlap_sites or overlap_groups:
        raise AssertionError(f"leak: {overlap_sites} sites and {overlap_groups} search groups on both sides")
    result: dict[str, Any] = {
        "config": dict(config), "content_backend": scorer.mode, "content_model": scorer.model_name,
        "label_table_version": str(train_labels["label_table_version"].iloc[0]) if len(train_labels) else None,
        "counts": {"train_rows": int(len(train_ok)), "train_sites": int(train_ok["site"].nunique()),
                   "train_searches": int(train_ok["keyword_id"].nunique()),
                   "train_pages": int(train_ok.groupby(["keyword_id", "snapshot_time"]).ngroups),
                   "test_rows": int(np.isfinite(test["residual"]).sum()), "test_sites": int(test["site"].nunique()),
                   "test_searches": int(test["keyword_id"].nunique()),
                   "sites_on_both_sides": overlap_sites, "search_groups_on_both_sides": overlap_groups,
                   "train_rows_missing_title": int((~np.isfinite(train["residual"])).sum())},
        "label_summary": {"train_residual_sd": float(np.nanstd(train["residual"])), "test_residual_sd": float(np.nanstd(test["residual"]))},
        "feature_sets": {},
    }
    model_values: dict[str, np.ndarray] = {}
    for name in FEATURE_SETS:
        model = fit_trust_model(train_ok, feature_set=name, label_backend=scorer.mode, label_model=scorer.model_name)
        model.version = f"trust-v{int(version)}"
        model_values[f"{name}_gbt"] = model.predict(test)
        model_values[f"{name}_ridge"] = model.predict_ridge(test)
        info: dict[str, Any] = {"features_used": model.features, "cv": model.cv, "rounds": model.rounds}
        if save and models_dir is not None:
            manifest = {**manifest_base, "feature_set": name, "language": language, "content_backend": scorer.mode,
                        "content_model": scorer.model_name, "train_rows": int(len(train_ok)), "features": model.features,
                        "label_table_version": result["label_table_version"]}
            path, digest = save_trust_model(model, manifest, models_dir)
            info["model_file"] = str(path)
            info["model_sha256"] = digest
        result["feature_sets"][name] = info
    test_eval = test[np.isfinite(test["residual"])].reset_index(drop=True)
    keep_index = np.flatnonzero(np.isfinite(test["residual"].to_numpy()))
    model_values = {k: v[keep_index] for k, v in model_values.items()}
    test_eval["st_mdr_before"] = test_eval["mdr_before"]  # DataForSEO domain rank: 0 to 1000, higher is stronger
    singles = [*SINGLE_MEASURES, "st_mdr_before"]
    result["evaluation"] = evaluate_pairs(test_eval, model_values, single_measures=singles, strict=pairs_strict(version))
    return result


def run(split: SplitRule = DEFAULT_SPLIT, *, out_dir: str | Path | None = None, log: Callable[[str], None] | None = None,
        rehearsal: bool = False, version: int = 1, reason: str | None = None, check_git: bool = True,
        models_dir: str | Path | None = None) -> dict[str, Any]:
    """Score the frozen marker 2 design ONCE on REAL held-out sites and searches.

    Design version 1 is already scored (evals/forecast/trust/report.json), so a version 1 run is refused. A rerun
    needs a new design version k: `prereg --design-version k --reason ...` writes prereg-v<k>.json, commit and push
    it, then `run --design-version k` writes report-v<k>.json, manifest-v<k>.json (code revision, dirty flag, source
    sha256) and trust-v<k>-*.json models, each by exclusive create. Every refusal happens before any held-out row is read.

    With `rehearsal`, only DEFAULT_SPLIT's training pages are read (train searches before the cutoff, train sites only),
    split again inside, nothing is written under evals/, and it can be rerun.
    """
    from .markers import evaluate_marker, render_marker_text
    from .strength import REHEARSAL_SPLIT

    restrict = None
    sites_split = None
    if rehearsal:
        folder = Path(out_dir) if out_dir else forecast_paths().cache / "rehearsal" / REPORT_NAME
        digest = "rehearsal"
        outer = split

        def restrict(frame: pd.DataFrame) -> pd.DataFrame:
            keep = frame[(frame["side"] == "train") & ~frame["site"].map(outer.site_is_test).astype(bool)].copy()
            sides = {(k, d): REHEARSAL_SPLIT.page_side(k, d) for k, d in keep[["keyword", "snapshot_date"]].drop_duplicates().itertuples(index=False)}
            keep["side"] = [sides[(k, d)] for k, d in zip(keep["keyword"], keep["snapshot_date"])]
            return keep[keep["side"] != "excluded"]

        sites_split = REHEARSAL_SPLIT
        models_dir = None
    context: dict[str, Any] | None = None
    if not rehearsal:
        folder = Path(out_dir) if out_dir else evals_dir(REPORT_NAME)
        models_dir = Path(models_dir) if models_dir else default_models_dir()
        reason = reason or frozen_reason(folder, version)
        existing = sorted(models_dir.glob(f"trust-v{int(version)}-*.json")) if models_dir.is_dir() else []
        context = begin_scored_run(folder, version, prereg_payload(split, version=version, reason=reason),
                                   outputs=existing[:1], check_git=check_git)
        digest = context["prereg_sha256"]
    best, _ = load_label_rows(split, sites_split=sites_split, log=log, restrict=restrict)
    provenance = context["provenance"] if context else code_provenance()
    manifest_base = {"version": f"trust-v{int(version)}", "design_version": int(version),
                     "data_sources": ["panel dfs_history (DataForSEO sample databases)",
                                      "Common Crawl domain ranks", "Chrome UX Report top lists", "Tranco",
                                      "Common Crawl first release (start dates; RDAP left out, no dated history)"],
                     "split_hash": sha256_text(canonical_json(asdict(split))), "prereg_sha256": digest,
                     "code_revision": provenance["code_revision"], "source_dirty": provenance["source_dirty"],
                     "dirty_files": provenance["dirty_files"], "source_tree_sha256": provenance["source_tree_sha256"],
                     "label": PREDICTED}
    runs = {}
    for config in (PRIMARY, *SECONDARY):
        key = f"{config['backend']}_{config.get('language') or 'all'}"
        runs[key] = run_one(best, config, models_dir=models_dir, manifest_base=manifest_base, log=log, save=not rehearsal,
                            version=version)
    primary_key = f"{PRIMARY['backend']}_{PRIMARY['language']}"
    primary_eval = runs[primary_key]["evaluation"]
    marker_input = marker2_report(primary_eval, "free_gbt")
    verdict = evaluate_marker(2, marker_input)
    marker_input_mdr = marker2_report(primary_eval, "free_plus_mdr_gbt")
    verdict_mdr = evaluate_marker(2, marker_input_mdr)
    report = {
        "label": REAL, "name": REPORT_NAME, "version": f"trust-v{int(version)}", "design_version": int(version),
        "reason_for_new_version": reason if int(version) > 1 else None,
        "pair_comparison": ">" if pairs_strict(version) else ">=", "prereg_sha256": digest, "rehearsal": bool(rehearsal),
        "computed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what_this_is": ("The plan's marker 2 definition applied to sites that already rank somewhere in a public sample, "
                         "with result titles standing in for page content. It is not yet the plan's 20,000 new sites with "
                         "traffic curves. Those need the paid pulls."),
        "what_is_real": "Positions, titles and list values as recorded on their dates.",
        "what_is_predicted": "Content scores, the content-expected place, every trust model output.",
        "runs": runs, "primary": primary_key,
        "marker2_input_free": marker_input, "marker2_verdict_free": verdict,
        "marker2_input_free_plus_mdr": marker_input_mdr, "marker2_verdict_free_plus_mdr": verdict_mdr,
        "marker2_text_free": render_marker_text(verdict),
    }
    from .strength import _clean, _json_default

    report = _clean(report)
    if context is None:  # rehearsal: rerunnable, under the cache only
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
        return report
    models = {key: {name: {"file": info.get("model_file"), "sha256": info.get("model_sha256")}
                    for name, info in (runs[key].get("feature_sets") or {}).items()} for key in runs}
    finish_scored_run(context, report, default=_json_default, extra={"name": REPORT_NAME, "models": models})
    return report


# ---------------------------------------------------------------------------
# the synthetic world


SYNTHETIC_FEATURES: tuple[str, ...] = (*SYNTHETIC_FOOTPRINT_COLUMNS, "st_s_cc_hc")
SYNTHETIC_NO_FOOTPRINT: tuple[str, ...] = ("st_s_cc_hc",)


def synthetic_trust_frame(world: Any, *, as_of_offset_days: int = 0, label_day: int = 90) -> pd.DataFrame:
    """One row per alive new site: footprint, the synthetic strength list as of launch, and a SYNTHETIC label.

    Synthetic pages have no text, so the label here is the planted trust on
    launch day plus `label_day` (World.true_trust). The planted ceiling is kept in
    `planted_ceiling` for checking only and never feeds the model.
    """
    from datetime import timedelta

    from .features import synthetic_footprint, synthetic_strength

    footprint = synthetic_footprint(world)
    strength = synthetic_strength(world)
    rows = []
    for site, info in sorted(world.new_sites.items()):
        if not info.alive:
            continue
        label_date = info.launch_date + timedelta(days=label_day)
        if label_date >= world.end:
            continue
        rows.append({"site": site, "as_of": info.launch_date + timedelta(days=as_of_offset_days), "niche": info.niche,
                     "search_group": f"synthetic-site|{site}", "residual": world.true_trust(site, label_date),
                     "planted_ceiling": info.trust_ceiling})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    st = strength(frame[["site", "as_of"]])
    frame["st_s_cc_hc"] = st["s_cc_hc"].to_numpy()
    frame = frame.merge(footprint, on="site", how="left")
    frame["label"] = SYNTHETIC
    return frame


def synthetic_order_check(world: Any, *, test_share: float = 0.3, seed: int = SEED) -> dict[str, Any]:
    """Fit on synthetic train sites, order held-out sites by planted ceiling. SYNTHETIC, never accuracy.

    Returns pair accuracy against the planted ceiling for: the model with the
    footprint, the model without the footprint, and the single link-count field
    (referring domains) alone.
    """
    frame = synthetic_trust_frame(world)
    is_test = np.array([stable_share(f"synthetic|{s}", seed) < test_share for s in frame["site"]])
    train, test = frame[~is_test].reset_index(drop=True), frame[is_test].reset_index(drop=True)
    small_grid = ({"num_leaves": 4, "min_child_samples": 10}, {"num_leaves": 7, "min_child_samples": 20})
    with_fp = fit_trust_model(train, feature_set="synthetic_footprint", features=SYNTHETIC_FEATURES, grid=small_grid, seed=seed)
    without = fit_trust_model(train, feature_set="synthetic_no_footprint", features=SYNTHETIC_NO_FOOTPRINT, grid=small_grid, seed=seed)

    def order(values: np.ndarray) -> float:
        truth = test["planted_ceiling"].to_numpy(dtype=float)
        a, b = np.triu_indices(len(truth), k=1)
        hi = np.where(truth[a] > truth[b], a, b)
        lo = np.where(truth[a] > truth[b], b, a)
        return float(np.mean(pair_scores(values, hi, lo)))

    return {
        "label": SYNTHETIC, "train_sites": int(len(train)), "test_sites": int(len(test)),
        "with_footprint_gbt": order(with_fp.predict(test)), "with_footprint_ridge": order(with_fp.predict_ridge(test)),
        "without_footprint_gbt": order(without.predict(test)), "without_footprint_ridge": order(without.predict_ridge(test)),
        "link_count_alone": order(test["fp_referring_domains"].to_numpy(dtype=float)),
        "models": {"with": with_fp, "without": without},
    }


# ---------------------------------------------------------------------------
# CLI


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.trust")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prereg", help="freeze the marker 2 design before scoring")
    pre.add_argument("--design-version", type=int, default=1, help="1 is frozen and scored; k >= 2 writes prereg-v<k>.json")
    pre.add_argument("--reason", default=None, help="why a new design version is needed (required for k >= 2)")
    run_cmd = sub.add_parser("run", help="score the frozen design once on REAL held-out sites and searches")
    run_cmd.add_argument("--rehearsal", action="store_true", help="training pages only, nested split, nothing written to evals")
    run_cmd.add_argument("--design-version", type=int, default=1, help="the frozen design version to score (once)")
    args = parser.parse_args(argv)
    folder = evals_dir(REPORT_NAME)
    if args.command == "prereg":
        path = prereg_file(folder, args.design_version)
        digest = freeze_prereg(path, prereg_payload(version=args.design_version, reason=args.reason),
                               code=args.design_version > 1)
        print(json.dumps({"prereg": str(path), "sha256": digest}, indent=2))
        return 0
    report = run(log=_log, rehearsal=args.rehearsal, version=args.design_version)
    print(report.get("marker2_text_free", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
