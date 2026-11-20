"""Design-only evaluation for Target 3 (pairwise order of movers).

Reads SERP rows only through rsdata.load_rows(rsdata.DESIGN_MAX_DATE) (days <= 2026-11-17).
Every prediction for day_b is made from rows with day < day_b. Every number in
evals/claude/t3-movers/notes.md comes from this script:

    python -m scripts.claude.t3_design_eval            # writes evals/claude/t3-movers/design_results.json

All model outputs are PREDICTED. Truth is the real Serper consensus.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics, rsdata  # noqa: E402
from scripts.claude import t3_movers as tm  # noqa: E402

OUT_DIR = ROOT / "evals" / "claude" / "t3-movers"

# (group, day_a, day_b). Only consecutive pulled days of the group.
TRANSITIONS = [
    ("o30", "2026-11-06", "2026-11-09"),
    ("o30", "2026-11-09", "2026-11-11"),
    ("o30", "2026-11-11", "2026-11-13"),
    ("o30", "2026-11-13", "2026-11-16"),
    ("o30", "2026-11-16", "2026-11-17"),
    ("rev", "2026-11-16", "2026-11-17"),
]
# The two transitions that look like the held-out ones (next calendar day).
CONSECUTIVE = [("o30", "2026-11-16", "2026-11-17"), ("rev", "2026-11-16", "2026-11-17")]


def tname(transition: tuple[str, str, str]) -> str:
    group, day_a, day_b = transition
    return f"{group} {day_a[5:]}->{day_b[5:]}"


class Design:
    """Loads design rows once and caches truth pairs and history contexts."""

    def __init__(self):
        self.rows = rsdata.load_rows(rsdata.DESIGN_MAX_DATE)
        assert self.rows["day"].max() <= rsdata.DESIGN_MAX_DATE
        self.groups = rsdata.query_groups(self.rows)
        self.cons = rsdata.consensus_table(self.rows)
        self.cons["grp"] = self.cons["query"].map(self.groups)
        self._pairs: dict = {}
        self._ctx: dict = {}

    def pairs(self, transition) -> pd.DataFrame:
        if transition not in self._pairs:
            group, day_a, day_b = transition
            part = self.cons[self.cons["grp"] == group]
            frame = rsdata.flip_pairs(part, day_a, day_b)
            frame["transition"] = tname(transition)
            frame["gap"] = frame["a_y"] - frame["a_x"]
            self._pairs[transition] = frame
        return self._pairs[transition]

    def context(self, day_b: str, deep_as_pull: bool) -> tm.HistoryContext:
        key = (day_b, deep_as_pull)
        if key not in self._ctx:
            history = self.rows[self.rows["day"] < day_b]
            self._ctx[key] = tm.HistoryContext(history, day_b, deep_as_pull=deep_as_pull)
        return self._ctx[key]


def predict_pairs(design: Design, transition, params: dict) -> pd.DataFrame:
    """Truth pairs with the scorer's PREDICTED flip call (score_x > score_y)."""
    group, day_a, day_b = transition
    pairs = design.pairs(transition).copy()
    ctx = design.context(day_b, bool(params.get("deep_as_pull", False)))
    pred = []
    for query, part in pairs.groupby("query", sort=False):
        if ctx.day_a(query) != day_a:
            raise AssertionError(f"{query}: scorer day_a {ctx.day_a(query)} != transition day_a {day_a}")
        keys = sorted(set(part["key_x"]) | set(part["key_y"]))
        scores = tm.score_with_context(ctx, query, keys, params)
        feat = ctx.features(query)
        for record in part.itertuples(index=False):
            # sanity: the scorer's own day_a consensus equals the truth table's day_a positions
            assert int(feat.loc[record.key_x, "cons_a"]) == record.a_x
            assert int(feat.loc[record.key_y, "cons_a"]) == record.a_y
            pred.append((record.Index if hasattr(record, "Index") else None, scores[record.key_x] > scores[record.key_y]))
    pairs["pred_flip"] = [flag for _, flag in pred]
    return pairs


def summarize(pairs: pd.DataFrame) -> dict:
    frame = pairs.copy()
    frame["hit"] = (frame["pred_flip"] == frame["flipped"]).astype(int)
    frame["persist_hit"] = (~frame["flipped"]).astype(int)
    frame["flipped_i"] = frame["flipped"].astype(int)
    called = frame[frame["pred_flip"]]
    return {
        "pairs": int(len(frame)),
        "flips": int(frame["flipped"].sum()),
        "calls": int(len(called)),
        "flip_acc": metrics.bootstrap_rate(frame[frame["flipped"]], "hit"),
        "nonflip_acc": metrics.bootstrap_rate(frame[~frame["flipped"]], "hit"),
        "all_acc": metrics.bootstrap_rate(frame, "hit"),
        "persist_all_acc": metrics.bootstrap_rate(frame, "persist_hit"),
        "all_minus_persist": metrics.bootstrap_difference(frame, "hit", "persist_hit"),
        "precision": metrics.bootstrap_rate(called, "flipped_i"),
    }


def row_text(label: str, summary: dict) -> str:
    def rate(result):
        if not result.get("n"):
            return "n/a"
        return f"{result['hits']}/{result['n']} {result['rate']:.3f} [{result['lo']:.3f},{result['hi']:.3f}]"

    return (
        f"| {label} | {rate(summary['flip_acc'])} | {rate(summary['nonflip_acc'])} | "
        f"{summary['all_acc']['rate']:.3f} vs {summary['persist_all_acc']['rate']:.3f} | "
        f"{metrics.fmt_diff(summary['all_minus_persist'])} | {rate(summary['precision'])} |"
    )


HEADER = (
    "| scorer | flip acc (recall) | non-flip acc | all-pairs vs persist | all - persist | precision of calls |\n"
    "|---|---|---|---|---|---|"
)


def heuristic_grid() -> list[tuple[str, dict]]:
    grid: list[tuple[str, dict]] = [("persistence", {"lam": 0.0})]
    for lam in (0.5, 1.0):
        grid.append((f"vote_mean_a lam={lam}", {"target": "vote_mean_a", "lam": lam}))
        grid.append((f"all_mean_a lam={lam}", {"target": "all_mean_a", "lam": lam}))
        grid.append((f"all_mean_a+deep lam={lam}", {"target": "all_mean_a", "lam": lam, "deep_as_pull": True}))
    for half in (0.5, 1.0, 2.0, 4.0, math.inf):
        for lam in (0.25, 0.5, 0.75, 1.0):
            grid.append((f"ew_mean h={half} lam={lam}", {"target": "ew_mean", "half_life": half, "lam": lam}))
    for label, extra in (
        ("kemeny day_a", {}),
        ("kemeny day_a+deep", {"deep_as_pull": True}),
        ("kemeny day_a+prev w=0.25", {"prev_weight": 0.25}),
        ("kemeny day_a+prev w=0.5 h=2", {"prev_weight": 0.5, "prev_half_life": 2.0}),
        ("kemeny day_a+prev w=1", {"prev_weight": 1.0}),
        ("kemeny day_a+prev w=1 h=2", {"prev_weight": 1.0, "prev_half_life": 2.0}),
        ("kemeny day_a margin=0.2", {"margin": 0.2}),
    ):
        grid.append((label, {"target": "kemeny", **extra}))
    for lam in (0.25, 0.5, 1.0):
        grid.append((f"prev_mean lam={lam}", {"target": "prev_mean", "lam": lam}))
        grid.append((f"trend w=3 lam={lam}", {"target": "trend", "window": 3, "lam": lam}))
        grid.append((f"deep lam={lam}", {"target": "deep", "lam": lam}))
        grid.append((f"xq_mean lam={lam}", {"target": "xq_mean", "lam": lam}))
    return grid


def run_grid(design: Design) -> dict:
    results = {}
    for label, params in heuristic_grid():
        per = {}
        frames = []
        for transition in TRANSITIONS:
            pairs = predict_pairs(design, transition, params)
            per[tname(transition)] = summarize(pairs)
            frames.append(pairs)
        pooled_all = pd.concat(frames, ignore_index=True)
        consecutive = pd.concat([f for f, t in zip(frames, TRANSITIONS, strict=True) if t in CONSECUTIVE], ignore_index=True)
        per["pooled o30 multi-day (6->16)"] = summarize(pd.concat(frames[:4], ignore_index=True))
        per["pooled consecutive (o30+rev 16->17)"] = summarize(consecutive)
        per["pooled all"] = summarize(pooled_all)
        results[label] = {"params": params, "results": per}
    return results


def _vote_ranks(design: Design, query: str, day: str) -> list[dict]:
    pulls = rsdata.pulls_by_query_day(design.rows[(design.rows["query"] == query) & (design.rows["day"] == day)])[(query, day)]
    return [pull.ranks for pull in rsdata.consensus_session(pulls)]


def time_reversed(design: Design) -> dict:
    """Diagnostic only. Orient pairs by the Nov 17 consensus and ask whether a strict majority of
    Nov 17's voting pulls against that consensus predicts the Nov 16 consensus order. Same-day pull noise
    is time symmetric, so this re-tests the rev_vote mechanism on independent pull draws. It uses a later
    day to 'predict' an earlier one and is never a forecast."""
    out = {}
    for group in ("o30", "rev"):
        part = design.cons[design.cons["grp"] == group]
        pairs = rsdata.flip_pairs(part, "2026-11-17", "2026-11-16")
        calls = []
        for record in pairs.itertuples(index=False):
            ranks = _vote_ranks(design, record.query, "2026-11-17")
            share = np.mean([1.0 if r.get(record.key_y, 11) < r.get(record.key_x, 11) else (0.5 if r.get(record.key_y, 11) == r.get(record.key_x, 11) else 0.0) for r in ranks])
            calls.append(share > 0.5)
        pairs["pred_flip"] = calls
        out[f"{group} 17->16 strict pull-majority override"] = summarize(pairs)
    return out


def flip_strength(design: Design) -> dict:
    """For each design consecutive-day flip, the share of day_b voting pulls that show the new order."""
    out = {}
    for transition in CONSECUTIVE:
        pairs = design.pairs(transition)
        counts: dict = {}
        for record in pairs[pairs["flipped"]].itertuples(index=False):
            ranks = _vote_ranks(design, record.query, transition[2])
            k = sum(1 for r in ranks if r.get(record.key_y, 11) < r.get(record.key_x, 11))
            label = f"{k}/{len(ranks)}"
            counts[label] = counts.get(label, 0) + 1
        out[tname(transition)] = counts
    return out


# ---------------------------------------------------------------- learned pairwise P(flip)

MODEL_FEATURES = {
    "LR-hist": ["inv_gap", "rev_prev", "rev_last", "d_ew2"],
    "LR-day": ["inv_gap", "rev_vote", "rev_a"],
    "LR-day+hist": ["inv_gap", "rev_vote", "rev_a", "rev_prev", "rev_last"],
    "LR-all": ["inv_gap", "top", "rev_vote", "rev_a", "rev_prev", "rev_last", "d_vote", "d_ew2", "d_prev", "d_deep", "d_xq"],
    "GBM-all": ["inv_gap", "top", "rev_vote", "rev_a", "rev_prev", "rev_last", "d_vote", "d_ew2", "d_prev", "d_deep", "d_xq", "n_a", "gap_days"],
}


def pair_dataset(design: Design) -> pd.DataFrame:
    """Truth pairs of every design transition with history-only pair features."""
    frames = []
    for transition in TRANSITIONS:
        pairs = design.pairs(transition)
        ctx = design.context(transition[2], False)
        for query, part in pairs.groupby("query", sort=False):
            feats = tm.pair_features(ctx, query, list(zip(part["key_x"], part["key_y"], strict=True)))
            assert list(feats["key_x"]) == list(part["key_x"]) and list(feats["key_y"]) == list(part["key_y"])
            feats.index = part.index
            frames.append(pd.concat([part, feats.drop(columns=["key_x", "key_y"])], axis=1))
    return pd.concat(frames, ignore_index=True)


def _fit_predict(name: str, train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    feats = MODEL_FEATURES[name]
    y = train["flipped"].astype(int).to_numpy()
    if name.startswith("GBM"):
        import lightgbm as lgb

        model = lgb.LGBMClassifier(
            n_estimators=150, learning_rate=0.05, max_depth=2, num_leaves=4, min_child_samples=20,
            subsample=1.0, colsample_bytree=1.0, reg_lambda=1.0, random_state=0, verbose=-1, deterministic=True,
        )
        model.fit(train[feats].to_numpy(), y)
        return model.predict_proba(test[feats].to_numpy())[:, 1]
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=5000))
    model.fit(train[feats].to_numpy(), y)
    return model.predict_proba(test[feats].to_numpy())[:, 1]


def forward_chain(data: pd.DataFrame, name: str) -> pd.DataFrame:
    """Each test transition is predicted by a model trained only on o30 transitions that END on or before its day_a."""
    out = []
    for transition in TRANSITIONS[2:]:
        group, day_a, day_b = transition
        train_t = [tname(t) for t in TRANSITIONS if t[0] == "o30" and t[2] <= day_a]
        train = data[data["transition"].isin(train_t)]
        test = data[data["transition"] == tname(transition)].copy()
        test["p_flip"] = _fit_predict(name, train, test)
        test["scheme"] = f"forward ({'+'.join(t[6:] for t in train_t)})"
        out.append(test)
    return pd.concat(out, ignore_index=True)


def query_cv(data: pd.DataFrame, name: str, transition_label: str, *, folds: int = 5, seed: int = 0, extra_train: list[str] | None = None) -> pd.DataFrame:
    """Out-of-fold P(flip) on one transition, folds split by query. NOT forward in time (same day)."""
    test_all = data[data["transition"] == transition_label].copy()
    queries = sorted(test_all["query"].unique())
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(queries))
    fold_of = {queries[i]: k % folds for k, i in enumerate(order)}
    test_all["fold"] = test_all["query"].map(fold_of)
    extra = data[data["transition"].isin(extra_train or [])]
    preds = pd.Series(np.nan, index=test_all.index)
    for k in range(folds):
        train = pd.concat([extra, test_all[test_all["fold"] != k]])
        test = test_all[test_all["fold"] == k]
        preds.loc[test.index] = _fit_predict(name, train, test)
    test_all["p_flip"] = preds
    return test_all


def curve(frame: pd.DataFrame, score_col: str = "p_flip", steps: tuple = (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1, 0.05)) -> list[dict]:
    """Recall / precision / all-pairs trade-off when flips are called for score >= threshold."""
    rows = []
    for thr in steps:
        work = frame.copy()
        work["pred_flip"] = work[score_col] >= thr
        summary = summarize(work)
        rows.append({"threshold": thr, **summary})
    return rows


def best_recall_at_precision(frame: pd.DataFrame, score_col: str = "p_flip", floor: float = 0.5) -> dict:
    """Largest recall among top-k cutoffs whose precision >= floor. Post hoc and optimistic by construction."""
    order = frame.sort_values(score_col, ascending=False)
    y = order["flipped"].astype(int).to_numpy()
    tp = np.cumsum(y)
    k = np.arange(1, len(y) + 1)
    ok = tp / k >= floor
    if not ok.any():
        return {"recall": 0.0, "calls": 0, "tp": 0, "flips": int(y.sum())}
    best = int(np.max(np.where(ok)[0]))
    return {"recall": float(tp[best] / y.sum()), "calls": int(k[best]), "tp": int(tp[best]), "flips": int(y.sum())}


def auc(frame: pd.DataFrame, score_col: str = "p_flip") -> float:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(frame["flipped"].astype(int), frame[score_col]))


def run_models(design: Design) -> dict:
    data = pair_dataset(design)
    t5 = tname(("o30", "2026-11-16", "2026-11-17"))
    rev = tname(("rev", "2026-11-16", "2026-11-17"))
    multi = [tname(t) for t in TRANSITIONS[:4]]
    results: dict = {"univariate_16_17": {}}
    part = data[data["transition"] == t5]
    for col in ("rev_vote", "rev_a", "rev_last"):
        table = part.groupby(col)["flipped"].agg(["sum", "size"])
        results["univariate_16_17"][col] = {f"{idx:.3f}": [int(r["sum"]), int(r["size"])] for idx, r in table.iterrows()}
    bins = pd.cut(part["rev_prev"], [-0.01, 0.2, 0.4, 0.6, 0.8, 1.0])
    table = part.groupby(bins, observed=False)["flipped"].agg(["sum", "size"])
    results["univariate_16_17"]["rev_prev"] = {str(idx): [int(r["sum"]), int(r["size"])] for idx, r in table.iterrows()}

    for name in MODEL_FEATURES:
        entry: dict = {"features": MODEL_FEATURES[name]}
        # A. forward chaining (only history features vary in training: day_a is a single pull before Nov 16)
        fc = forward_chain(data, name)
        entry["forward"] = {}
        for label, sub in fc.groupby("transition", sort=False):
            sub = sub.copy()
            sub["pred_flip"] = sub["p_flip"] > 0.5
            entry["forward"][label] = {
                "scheme": sub["scheme"].iloc[0],
                "auc": auc(sub),
                "at_p_gt_0.5": summarize(sub),
                "best_recall_at_precision_0.5_posthoc": best_recall_at_precision(sub),
            }
        # B. query-grouped CV on o30 16->17 (5 seeds), with and without earlier transitions in training
        for tag, extra in (("qcv_16_17", None), ("qcv_16_17+multi", multi)):
            pooled = []
            for seed in range(5):
                oof = query_cv(data, name, t5, seed=seed, extra_train=extra)
                oof["seed"] = seed
                pooled.append(oof)
            seeds = pd.concat(pooled, ignore_index=True)
            per_seed = []
            for seed, sub in seeds.groupby("seed"):
                sub = sub.copy()
                sub["pred_flip"] = sub["p_flip"] > 0.5
                s = summarize(sub)
                per_seed.append(
                    {
                        "seed": int(seed),
                        "auc": auc(sub),
                        "flip_acc": s["flip_acc"]["rate"],
                        "precision": s["precision"]["rate"],
                        "calls": s["calls"],
                        "all_minus_persist": s["all_minus_persist"]["diff"],
                        "best_recall_at_precision_0.5_posthoc": best_recall_at_precision(sub)["recall"],
                    }
                )
            first = seeds[seeds["seed"] == 0].copy()
            first["pred_flip"] = first["p_flip"] > 0.5
            entry[tag] = {"per_seed": per_seed, "seed0_at_p_gt_0.5": summarize(first), "seed0_curve": curve(first)}
        # C. in-sample on o30 16->17 (fit and score on the same pairs): optimistic ceiling, not a result
        insample = part.copy()
        insample["p_flip"] = _fit_predict(name, part, part)
        insample["pred_flip"] = insample["p_flip"] > 0.5
        entry["insample_16_17"] = {
            "auc": auc(insample),
            "at_p_gt_0.5": summarize(insample),
            "best_recall_at_precision_0.5": best_recall_at_precision(insample),
        }
        results[name] = entry
    results["_rev_note"] = f"{rev} is scored in the forward block with o30 models trained on 6->16."
    return results


def print_models(results: dict) -> None:
    print("\n## Learned pairwise P(flip) models")
    for name, entry in results.items():
        if name.startswith("_") or name == "univariate_16_17":
            continue
        print(f"\n### {name} {entry['features']}")
        print("forward chaining, call flip when P>0.5:")
        print(HEADER)
        for label, item in entry["forward"].items():
            print(row_text(f"{label} auc={item['auc']:.3f} posthoc-best-recall@prec0.5={item['best_recall_at_precision_0.5_posthoc']['recall']:.3f}", item["at_p_gt_0.5"]))
        for tag in ("qcv_16_17", "qcv_16_17+multi"):
            print(f"{tag} per seed: " + "; ".join(
                f"s{p['seed']} auc={p['auc']:.3f} rec={p['flip_acc']:.3f} prec={'n/a' if p['precision'] is None else f'{p['precision']:.3f}'} calls={p['calls']} d={p['all_minus_persist']:+.3f} best@0.5={p['best_recall_at_precision_0.5_posthoc']:.3f}"
                for p in entry[tag]["per_seed"]
            ))
        ins = entry["insample_16_17"]
        print(f"in-sample 16->17 (optimistic): auc={ins['auc']:.3f} best recall@prec>=0.5={ins['best_recall_at_precision_0.5']}")


# ---------------------------------------------------------------- frozen spec checks and trade-off curves

def kemeny_sweep(design: Design) -> dict:
    """URL-level trade-off: weight of earlier days' pulls against day_a's pulls (h = 2 days)."""
    out = {}
    for weight in (0.0, 0.001, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
        params = {"target": "kemeny", "prev_weight": weight, "prev_half_life": 2.0}
        frames = {tname(t): predict_pairs(design, t, params) for t in TRANSITIONS}
        consecutive = pd.concat([frames[tname(t)] for t in CONSECUTIVE], ignore_index=True)
        out[f"w={weight}"] = {
            "o30 16->17": summarize(frames[tname(CONSECUTIVE[0])]),
            "pooled consecutive": summarize(consecutive),
            "pooled o30 multi-day": summarize(pd.concat([frames[tname(t)] for t in TRANSITIONS[:4]], ignore_index=True)),
        }
    return out


def share_curve(design: Design, params: dict) -> dict:
    """Pair-level trade-off: call a flip when the weighted share of pulls with y above x exceeds theta.
    theta = 0.5 is the strict-majority rule (the Kemeny scorer may differ on a few pairs)."""
    out = {}
    for transition in CONSECUTIVE + TRANSITIONS[:4]:
        pairs = design.pairs(transition).copy()
        ctx = design.context(transition[2], False)
        shares = []
        for query, part in pairs.groupby("query", sort=False):
            items, share = tm.pull_shares(ctx, query, params)
            index = {key: i for i, key in enumerate(items)}
            for record in part.itertuples(index=False):
                shares.append(share[index[record.key_y], index[record.key_x]])
        pairs["share_y"] = shares
        rows = {}
        for theta in (0.7, 0.6, 0.5, 0.45, 0.4, 0.35, 0.3, 0.25, 0.2, 0.1, 0.0):
            work = pairs.copy()
            work["pred_flip"] = work["share_y"] > theta
            rows[f"{theta}"] = summarize(work)
        out[tname(transition)] = rows
    return out


def power(summary: dict) -> dict:
    """Design effect of the query bootstrap and flips needed for a CI lower bound above 0.5."""
    flip = summary["flip_acc"]
    r, n = flip["rate"], flip["n"]
    boot_se = (flip["hi"] - flip["lo"]) / 3.92
    binom_se = math.sqrt(max(r * (1 - r), 1e-9) / n)
    deff = (boot_se / binom_se) ** 2 if binom_se > 0 else math.nan
    from statistics import NormalDist

    norm = NormalDist()

    def needed(rate: float) -> float:
        return math.inf if rate <= 0.5 else deff * 1.96**2 * rate * (1 - rate) / (rate - 0.5) ** 2

    def p_clear(rate: float, flips: int) -> float:
        se = math.sqrt(deff * rate * (1 - rate) / flips)
        # observed rate must exceed 0.5 + 1.96 * se (se evaluated near the observed rate, approximated at rate)
        return 1 - norm.cdf((0.5 + 1.96 * se - rate) / se)

    return {
        "design_recall": r,
        "design_recall_ci": [flip["lo"], flip["hi"]],
        "design_flips": n,
        "deff_query_bootstrap": deff,
        "flips_needed_if_true_recall": {f"{rate}": needed(rate) for rate in (0.55, 0.6, 0.65, 0.7, 0.8)},
        "p_clear_at_design_recall": {f"n={k}": p_clear(r, k) for k in (45, 90, 135, 150, 300)},
        "p_clear_at_design_upper_ci": {f"n={k}": p_clear(flip["hi"], k) for k in (45, 90, 135, 150, 300)},
    }


def frozen_check(design: Design) -> dict:
    """Score the frozen params.json through the public score_urls API, one call per (query, day_b)."""
    params = tm.load_params()
    out = {"params": params}
    frames = []
    for transition in TRANSITIONS:
        _, day_a, day_b = transition
        history = design.rows[design.rows["day"] < day_b]
        pairs = design.pairs(transition).copy()
        flags = []
        for query, part in pairs.groupby("query", sort=False):
            keys = sorted(set(part["key_x"]) | set(part["key_y"]))
            scores = tm.score_urls(history, query, keys, day_b)
            # the score of a key must not depend on which other keys are requested
            for key in keys[:3]:
                alone = tm.score_urls(history, query, [key], day_b)
                assert alone[key] == scores[key], (query, key)
            flags.extend((scores[x] > scores[y]) for x, y in zip(part["key_x"], part["key_y"], strict=True))
        pairs["pred_flip"] = flags
        out[tname(transition)] = summarize(pairs)
        frames.append(pairs)
    out["pooled consecutive (o30+rev 16->17)"] = summarize(pd.concat(frames[4:], ignore_index=True))
    out["pooled o30 multi-day (6->16)"] = summarize(pd.concat(frames[:4], ignore_index=True))
    # leakage guard: history containing day_b must be refused
    try:
        tm.score_urls(design.rows[design.rows["day"] <= "2026-11-17"], "x", ["k"], "2026-11-17")
        out["assert_guard"] = "FAILED: no error"
    except AssertionError:
        out["assert_guard"] = "ok: history with day >= day_b raises AssertionError"
    out["power_o30_16_17"] = power(out[tname(CONSECUTIVE[0])])
    out["power_pooled_consecutive"] = power(out["pooled consecutive (o30+rev 16->17)"])
    return out


def main() -> None:
    design = Design()
    out = {"design_max_date": rsdata.DESIGN_MAX_DATE, "transitions": [tname(t) for t in TRANSITIONS]}
    grid = run_grid(design)
    out["grid"] = grid
    for view in [tname(t) for t in TRANSITIONS] + ["pooled o30 multi-day (6->16)", "pooled consecutive (o30+rev 16->17)"]:
        print(f"\n### {view}\n{HEADER}")
        for label, item in grid.items():
            print(row_text(label, item["results"][view]))
    out["time_reversed"] = time_reversed(design)
    print("\n## Time-reversed diagnostic (Nov 17 pulls vs Nov 16 consensus order; NOT a prediction)")
    print(HEADER)
    for label, item in out["time_reversed"].items():
        print(row_text(label, item))
    out["flip_strength"] = flip_strength(design)
    print("\n## How many day_b pulls reversed each true flip (outcome-side diagnostic)")
    print(json.dumps(out["flip_strength"], indent=1))
    out["kemeny_sweep"] = kemeny_sweep(design)
    print("\n## Kemeny trade-off: weight of earlier days' pulls (h=2)")
    for view in ("o30 16->17", "pooled consecutive", "pooled o30 multi-day"):
        print(f"\n### {view}\n{HEADER}")
        for label, item in out["kemeny_sweep"].items():
            print(row_text(label, item[view]))
    out["share_curve"] = share_curve(design, tm.load_params())
    print("\n## Pair-level threshold curve on weighted pull share (frozen weights)")
    for view, rows in out["share_curve"].items():
        print(f"\n### {view}\n{HEADER}")
        for theta, item in rows.items():
            print(row_text(f"share_y > {theta}", item))
    out["frozen"] = frozen_check(design)
    print("\n## Frozen params.json through score_urls")
    print(HEADER)
    for label, item in out["frozen"].items():
        if isinstance(item, dict) and "flip_acc" in item:
            print(row_text(label, item))
    print(out["frozen"]["assert_guard"])
    print(json.dumps({k: out["frozen"][k] for k in ("power_o30_16_17", "power_pooled_consecutive")}, indent=1, default=float))
    if "--skip-models" not in sys.argv:
        models = run_models(design)
        out["models"] = models
        print_models(models)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "design_results.json", "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1, default=float)


if __name__ == "__main__":
    main()
