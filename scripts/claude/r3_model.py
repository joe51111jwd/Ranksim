"""Round 3 scorers for ordering the pages of one site for one search, and how they are evaluated.

Every scorer gives one number per (search, page). Inside a list the page with the
larger number is PREDICTED to rank higher. Pair accuracy counts a tie as half.

Scorers:
  bm25f      BM25F alone (the baseline named in the brief)
  vote       equal-weight vote of fixed feature directions, nothing fitted
  logit      logistic regression on feature differences of page pairs (no intercept, L2)
  gbm        LightGBM LambdaMART on the lists (fixed small settings)
  blend      mean of the within-list z-scores of logit and gbm

Cross-validation always holds out whole sites (and, for the stricter variant,
whole search groups too). Intervals are 95% bootstraps over lists and over sites.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import metrics as M  # noqa: E402
from scripts.claude import r3_features as F  # noqa: E402

SEED = 20261119
LOGIT_C = 1.0
GBM_PARAMS = {
    "objective": "lambdarank",
    "n_estimators": 300,
    "learning_rate": 0.04,
    "num_leaves": 7,
    "min_child_samples": 15,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.7,
    "reg_lambda": 5.0,
    "lambdarank_truncation_level": 10,
    "verbose": -1,
    "random_state": SEED,
    "n_jobs": 4,
}

LEXICAL = ["bm25f_norm", "bm25_body_norm"] + [name for name in F.TEXT_LEXICAL if name not in ("bm25f", "bm25_body")]
TIERS = {
    "lexical": LEXICAL,
    "semantic": F.TEXT_SEMANTIC,
    "url": F.URL_FEATURES,
    "shape": F.SHAPE_FEATURES,
    "site": ["in_list_link_share", "home_linked", "anchor_cov", "has_anchor"],
    "query": F.QUERY_FEATURES,
}
# Fixed directions for the unfitted vote (+1 larger is better, -1 smaller is better). Text and URL match only.
VOTE = {
    "bm25f_norm": 1, "title_cov": 1, "h1_cov": 1, "meta_cov": 1, "slug_cov": 1, "title_phrase": 1,
    "title_focus": 1, "slug_focus": 1, "first100_cov": 1, "body_density": 1,
}


def feature_set(tiers: list[str], available: set[str]) -> list[str]:
    names: list[str] = []
    for tier in tiers:
        names += [name for name in TIERS[tier] if name in available and name not in names]
    return names


# ---------------------------------------------------------------------------------------------
# Pair bookkeeping


def list_pairs_index(table: pd.DataFrame) -> pd.DataFrame:
    """Index pairs (row positions) of usable pages inside each list, higher-ranked page first."""
    usable = table[table["usable"]]
    out = []
    for list_id, group in usable.groupby("list_id", sort=True):
        group = group.sort_values("rank")
        index = group.index.to_numpy()
        ranks = group["rank"].to_numpy()
        for i in range(len(index)):
            for j in range(i + 1, len(index)):
                out.append((list_id, group["site"].iloc[0], group["query"].iloc[0], index[i], index[j], int(ranks[i]), int(ranks[j])))
    return pd.DataFrame(out, columns=["list_id", "site", "query", "hi", "lo", "rank_hi", "rank_lo"])


def pair_hits(pairs: pd.DataFrame, scores: pd.Series) -> np.ndarray:
    hi = scores.loc[pairs["hi"]].to_numpy(dtype=float)
    lo = scores.loc[pairs["lo"]].to_numpy(dtype=float)
    diff = hi - lo
    return np.where(diff > 1e-12, 1.0, np.where(diff < -1e-12, 0.0, 0.5))


def accuracy(pairs: pd.DataFrame, hits: np.ndarray) -> dict:
    frame = pairs.assign(hit=hits)
    if frame.empty:
        return {"n": 0}
    by_list = M.bootstrap_rate(frame, "hit", query_col="list_id")
    by_site = M.bootstrap_rate(frame, "hit", query_col="site")
    by_query = M.bootstrap_rate(frame, "hit", query_col="query")
    return {
        "n": int(len(frame)),
        "hits": float(frame["hit"].sum()),
        "rate": float(frame["hit"].mean()),
        "ci_list": [by_list["lo"], by_list["hi"]],
        "lists": by_list["queries"],
        "ci_site": [by_site["lo"], by_site["hi"]],
        "sites": by_site["queries"],
        "ci_query": [by_query["lo"], by_query["hi"]],
        "queries": by_query["queries"],
        "macro_site": float(frame.groupby("site")["hit"].mean().mean()),
    }


def difference(pairs: pd.DataFrame, hits: np.ndarray, base: np.ndarray) -> dict:
    frame = pairs.assign(hit=hits, base=base)
    by_list = M.bootstrap_difference(frame, "hit", "base", query_col="list_id")
    by_site = M.bootstrap_difference(frame, "hit", "base", query_col="site")
    by_query = M.bootstrap_difference(frame, "hit", "base", query_col="query")
    return {"diff": by_list["diff"], "ci_list": [by_list["lo"], by_list["hi"]], "ci_site": [by_site["lo"], by_site["hi"]], "ci_query": [by_query["lo"], by_query["hi"]]}


def fmt(entry: dict) -> str:
    if not entry.get("n"):
        return "0/0"
    return (
        f"{entry['hits']:.1f}/{entry['n']} ({entry['rate']:.3f}) list [{entry['ci_list'][0]:.3f}, {entry['ci_list'][1]:.3f}] "
        f"site [{entry['ci_site'][0]:.3f}, {entry['ci_site'][1]:.3f}] query [{entry['ci_query'][0]:.3f}, {entry['ci_query'][1]:.3f}]"
    )


def fmt_diff(entry: dict) -> str:
    return (
        f"{entry['diff']:+.3f} list [{entry['ci_list'][0]:+.3f}, {entry['ci_list'][1]:+.3f}] site [{entry['ci_site'][0]:+.3f}, {entry['ci_site'][1]:+.3f}] "
        f"query [{entry['ci_query'][0]:+.3f}, {entry['ci_query'][1]:+.3f}]"
    )


# ---------------------------------------------------------------------------------------------
# Scorers


def _filled(table: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Missing values take the mean of their list (so they add nothing to a pair difference), else 0."""
    values = table[features].astype(float)
    list_mean = values.groupby(table["list_id"]).transform("mean")
    return values.fillna(list_mean).fillna(0.0)


def vote_scores(table: pd.DataFrame) -> pd.Series:
    """Sum over features of the within-list rank share, in the fixed direction. Nothing is fitted."""
    usable = table[table["usable"]]
    total = pd.Series(0.0, index=usable.index)
    for name, direction in VOTE.items():
        if name in usable:
            total += direction * usable.groupby("list_id")[name].rank(method="average", pct=True).fillna(0.5)
    return total


class LogitModel:
    def __init__(self, features: list[str], c: float = LOGIT_C):
        self.features = list(features)
        self.c = c
        self.scale: np.ndarray | None = None
        self.coef: np.ndarray | None = None

    def fit(self, table: pd.DataFrame) -> "LogitModel":
        from sklearn.linear_model import LogisticRegression

        usable = table[table["usable"]]
        values = _filled(usable, self.features)
        pairs = list_pairs_index(usable)
        diff = values.loc[pairs["hi"]].to_numpy() - values.loc[pairs["lo"]].to_numpy()
        self.scale = np.where(diff.std(axis=0) > 1e-9, diff.std(axis=0), 1.0)
        x = diff / self.scale
        # Each list gets the same total weight, so a site with long lists does not dominate.
        weight = 1.0 / pairs.groupby("list_id")["hi"].transform("size").to_numpy()
        model = LogisticRegression(fit_intercept=False, C=self.c, max_iter=2000)
        model.fit(np.vstack([x, -x]), np.concatenate([np.ones(len(x)), np.zeros(len(x))]), sample_weight=np.concatenate([weight, weight]))
        self.coef = model.coef_[0]
        return self

    def score(self, table: pd.DataFrame) -> pd.Series:
        usable = table[table["usable"]]
        values = _filled(usable, self.features).to_numpy() / self.scale
        return pd.Series(values @ self.coef, index=usable.index)

    def to_dict(self) -> dict:
        return {"kind": "logit", "features": self.features, "c": self.c, "scale": self.scale.tolist(), "coef": self.coef.tolist()}

    @classmethod
    def from_dict(cls, data: dict) -> "LogitModel":
        model = cls(data["features"], data["c"])
        model.scale = np.asarray(data["scale"], dtype=float)
        model.coef = np.asarray(data["coef"], dtype=float)
        return model


class GbmModel:
    def __init__(self, features: list[str], params: dict | None = None):
        self.features = list(features)
        self.params = dict(GBM_PARAMS if params is None else params)
        self.model = None

    def fit(self, table: pd.DataFrame) -> "GbmModel":
        import lightgbm as lgb

        usable = table[table["usable"]].sort_values(["list_id", "rank"])
        sizes = usable.groupby("list_id", sort=True).size()
        keep = usable[usable["list_id"].isin(sizes[sizes >= 2].index)]
        group = keep.groupby("list_id", sort=True).size().to_numpy()
        # Relevance grade: 10 for the first page of the list down to 1 for the tenth.
        grade = (11 - keep["rank"].clip(upper=10)).astype(int).to_numpy()
        self.model = lgb.LGBMRanker(**self.params)
        self.model.fit(keep[self.features].astype(float), grade, group=group)
        return self

    def score(self, table: pd.DataFrame) -> pd.Series:
        usable = table[table["usable"]]
        return pd.Series(self.model.predict(usable[self.features].astype(float)), index=usable.index)


def zscore_in_list(table: pd.DataFrame, scores: pd.Series) -> pd.Series:
    lists = table.loc[scores.index, "list_id"]
    mean = scores.groupby(lists).transform("mean")
    std = scores.groupby(lists).transform("std").replace(0.0, np.nan)
    return ((scores - mean) / std).fillna(0.0)


def fit_predict(kind: str, features: list[str], train: pd.DataFrame, test: pd.DataFrame) -> pd.Series:
    if kind == "logit":
        return LogitModel(features).fit(train).score(test)
    if kind == "gbm":
        return GbmModel(features).fit(train).score(test)
    if kind == "blend":
        linear = [name for name in features if name not in TIERS["query"]]
        a = zscore_in_list(test, LogitModel(linear).fit(train).score(test))
        b = zscore_in_list(test, GbmModel(features).fit(train).score(test))
        return (a + b) / 2.0
    raise ValueError(kind)


def site_folds(table: pd.DataFrame, n_splits: int = 5, seed: int = SEED) -> dict[str, int]:
    """Whole sites per fold, sites dealt in a seeded order by descending size so folds are even."""
    sizes = table[table["usable"]].groupby("site").size().sort_values(ascending=False)
    rng = np.random.default_rng(seed)
    order = list(sizes.index)
    # Shuffle inside blocks of n_splits so the deal is random but balanced.
    folds: dict[str, int] = {}
    for start in range(0, len(order), n_splits):
        block = order[start : start + n_splits]
        for site, fold in zip(block, rng.permutation(n_splits)[: len(block)], strict=True):
            folds[site] = int(fold)
    return folds


def cross_validate(table: pd.DataFrame, kind: str, features: list[str], n_splits: int = 5, seed: int = SEED, also_hold_out_queries: bool = False, group_of: dict[str, int] | None = None) -> pd.Series:
    """Out-of-fold scores: each site's lists are scored by a model that never saw that site.

    With ``also_hold_out_queries`` the training side also drops every list whose
    search group appears in the held-out fold.
    """
    folds = site_folds(table, n_splits, seed)
    fold_of_row = table["site"].map(folds)
    out = pd.Series(np.nan, index=table.index)
    for fold in range(n_splits):
        test = table[fold_of_row == fold]
        train = table[fold_of_row != fold]
        if also_hold_out_queries and group_of is not None:
            held = set(test["query"].map(group_of))
            train = train[~train["query"].map(group_of).isin(held)]
        if test[test["usable"]].empty or train[train["usable"]].empty:
            continue
        scores = fit_predict(kind, features, train, test)
        out.loc[scores.index] = scores
    return out


# ---------------------------------------------------------------------------------------------
# Frozen scorer: fit once on the training lists, saved as plain files, loaded for every later use.


class FrozenScorer:
    """A fitted scorer of kind logit, gbm or blend, saved as JSON plus a LightGBM text model.

    ``score(table)`` returns one number per usable row. For the blend the two parts
    are z-scored inside each list before they are averaged, so a blend score only
    compares pages of the same list. ``score_parts`` gives the raw parts, which are
    comparable across lists (used for before-and-after scoring of one page).
    """

    def __init__(self, spec: dict, logit: LogitModel | None, gbm: GbmModel | None):
        self.spec = spec
        self.logit = logit
        self.gbm = gbm

    @classmethod
    def fit(cls, table: pd.DataFrame, kind: str, features: list[str]) -> "FrozenScorer":
        linear = [name for name in features if name not in TIERS["query"]]
        logit = LogitModel(linear).fit(table) if kind in ("logit", "blend") else None
        gbm = GbmModel(features).fit(table) if kind in ("gbm", "blend") else None
        return cls({"kind": kind, "features": list(features), "linear_features": linear, "gbm_params": GBM_PARAMS, "logit_c": LOGIT_C}, logit, gbm)

    def score_parts(self, table: pd.DataFrame) -> dict[str, pd.Series]:
        parts = {}
        if self.logit is not None:
            parts["logit"] = self.logit.score(table)
        if self.gbm is not None:
            parts["gbm"] = self.gbm.score(table)
        return parts

    def score(self, table: pd.DataFrame) -> pd.Series:
        parts = self.score_parts(table)
        if self.spec["kind"] == "blend":
            return (zscore_in_list(table, parts["logit"]) + zscore_in_list(table, parts["gbm"])) / 2.0
        return parts[self.spec["kind"]]

    def save(self, directory: Path) -> None:
        import json

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "spec.json").write_text(json.dumps(self.spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if self.logit is not None:
            (directory / "logit.json").write_text(json.dumps(self.logit.to_dict(), indent=2) + "\n", encoding="utf-8")
        if self.gbm is not None:
            (directory / "gbm.txt").write_text(self.gbm.model.booster_.model_to_string(), encoding="utf-8")

    @classmethod
    def load(cls, directory: Path) -> "FrozenScorer":
        import json

        import lightgbm as lgb

        directory = Path(directory)
        spec = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
        logit = LogitModel.from_dict(json.loads((directory / "logit.json").read_text(encoding="utf-8"))) if (directory / "logit.json").exists() else None
        gbm = None
        if (directory / "gbm.txt").exists():
            gbm = GbmModel(spec["features"], spec["gbm_params"])
            gbm.model = _BoosterWrap(lgb.Booster(model_str=(directory / "gbm.txt").read_text(encoding="utf-8")))
        return cls(spec, logit, gbm)


class _BoosterWrap:
    """Gives a loaded LightGBM booster the predict() call GbmModel.score uses."""

    def __init__(self, booster):
        self.booster_ = booster

    def predict(self, frame):
        return self.booster_.predict(np.asarray(frame, dtype=float))
