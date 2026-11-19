"""Target 2 direction evaluation on design data (days <= 2026-11-17). Outputs are PREDICTED.

Reads evals/claude/t2-edits/dataset.csv (built by t2_dataset from the sealed loaders).
Direction rows: usable page pair AND the consensus position moved (pos_b != pos_a).

Methods
  majority        forward: the majority class of the training rows (else 'down')
  coin            0.5 by definition (reported as the reference line)
  position_rule   t2_direction.predict_direction_position_only (fixed, no fit)
  content_rule    sign of the frozen content score when material and nonzero, else 'down'
  both_rule       t2_direction.predict_direction (the frozen predictor)
  position_logit  logistic regression on position/history features only
  content_logit   logistic regression on content-diff features only
  both_logit      logistic regression on both blocks

Fitted models are strictly forward and grouped: a test row with fetch day A is
predicted by a model fit on rows whose label day B <= A, excluding every row of
the test row's site AND of its query. The fixed rules were designed on all
design rows (see notes.md), so their design numbers are in-sample for those
design choices; only the held-out scoring of the frozen file is a real test.

Test rows are those with day_a >= 2026-11-09 (the earliest day with a prior
labelled transition), so every method is scored on the same rows.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as M  # noqa: E402
from scripts.claude import rsdata  # noqa: E402
from scripts.claude.t2_direction import (  # noqa: E402
    content_score,
    load_params,
    predict_direction,
    predict_direction_position_only,
)

OUT = ROOT / "evals/claude/t2-edits"
FIRST_TEST_DAY = "2026-11-09"
MARGIN = 0.05
C_LOGIT = 0.5
OWN_SITE = "smartcrewhq.com"

POSITION_FEATURES = [
    "pos_a", "reversion", "has_prev", "trend", "spread_a", "n_present_a", "in_top10_a", "top1", "absent_a",
    "gap_days", "days_seen_top10",
]
CONTENT_FEATURES = [
    "material", "title_changed", "h1_changed", "edit_share", "wc_log_ratio", "q_title_delta", "q_h1_delta",
    "q_body_delta", "q_titlecov_delta", "q_h1cov_delta", "q_bodycov_delta", "q_density_delta", "headings_dist",
]
METHODS = ["majority", "position_rule", "content_rule", "both_rule", "position_logit", "content_logit", "both_logit"]


def _prepare(path: Path) -> pd.DataFrame:
    data = pd.read_csv(path)
    for column in ("usable", "material", "title_changed", "h1_changed", "has_prev", "in_top10_a"):
        data[column] = data[column].astype(str).str.lower().isin({"true", "1"})
    frame = data[data["usable"] & (data["move"] != "none")].copy()
    frame["y"] = (frame["move"] == "up").astype(int)
    frame["top1"] = (frame["pos_a"] <= 1).astype(float)
    frame["absent_a"] = (frame["pos_a"] >= 11).astype(float)
    frame["reversion"] = frame["reversion"].fillna(0.0)
    frame["is_material"] = frame["material"].astype(bool)
    frame["is_top10_a"] = frame["in_top10_a"].astype(bool)
    for column in POSITION_FEATURES + CONTENT_FEATURES:
        frame[column] = frame[column].astype(float).fillna(0.0)
    return frame.reset_index(drop=True)


def _fit_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str]) -> np.ndarray:
    if train["y"].nunique() < 2:
        return np.full(len(test), int(train["y"].iloc[0]) if len(train) else 0)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=C_LOGIT, max_iter=2000))
    model.fit(train[features].to_numpy(), train["y"].to_numpy())
    return model.predict(test[features].to_numpy())


def _forward_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    test_mask = frame["day_a"] >= FIRST_TEST_DAY
    out = pd.DataFrame(index=frame.index[test_mask])
    for name in ("majority", "position_logit", "content_logit", "both_logit"):
        out[name] = -1
    out["train_rows"] = 0
    for (day_a, site, query), group in frame[test_mask].groupby(["day_a", "site", "query"], sort=True):
        train = frame[(frame["day_b"] <= day_a) & (frame["site"] != site) & (frame["query"] != query)]
        out.loc[group.index, "train_rows"] = len(train)
        majority = int(train["y"].mean() > 0.5) if len(train) else 0
        out.loc[group.index, "majority"] = majority
        out.loc[group.index, "position_logit"] = _fit_predict(train, group, POSITION_FEATURES)
        out.loc[group.index, "content_logit"] = _fit_predict(train, group, CONTENT_FEATURES)
        out.loc[group.index, "both_logit"] = _fit_predict(train, group, POSITION_FEATURES + CONTENT_FEATURES)
    return out


def _content_rule(row, params: dict) -> int:
    if row["is_material"] and content_score(row, params) != 0:
        return int(content_score(row, params) > 0)
    return 0


def _difference_between_sets(frame: pd.DataFrame, hit: str, left_mask: pd.Series, right_mask: pd.Series) -> dict:
    """acc(left rows) - acc(right rows), queries resampled jointly."""
    queries = sorted(frame["query"].unique())
    index = {query: position for position, query in enumerate(queries)}
    sums = np.zeros((len(queries), 4))
    for row_hit, query, left, right in zip(frame[hit], frame["query"], left_mask, right_mask, strict=True):
        if left:
            sums[index[query], 0] += row_hit
            sums[index[query], 1] += 1
        if right:
            sums[index[query], 2] += row_hit
            sums[index[query], 3] += 1
    rng = np.random.default_rng(M.BOOT_SEED)
    draws = rng.integers(0, len(queries), size=(M.BOOT_DRAWS, len(queries)))
    picked = sums[draws].sum(axis=1)
    ok = (picked[:, 1] > 0) & (picked[:, 3] > 0)
    stats = picked[ok, 0] / picked[ok, 1] - picked[ok, 2] / picked[ok, 3]
    total = sums.sum(axis=0)
    return {
        "diff": float(total[0] / total[1] - total[2] / total[3]),
        "lo": float(np.percentile(stats, 2.5)),
        "hi": float(np.percentile(stats, 97.5)),
        "n_left": int(total[1]),
        "n_right": int(total[3]),
        "valid_draws": int(ok.sum()),
    }


def _wilson_low(hits: int, n: int, z: float = 1.959964) -> float:
    if n == 0:
        return 0.0
    p = hits / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - spread) / (1 + z * z / n)


def _yield_and_power(pairs: pd.DataFrame, data: pd.DataFrame, discordance: float) -> dict:
    usable = pairs[pairs["usable"].astype(str).str.lower() == "true"].copy()
    usable["material"] = usable["material"].astype(str).str.lower() == "true"
    rows = data[(data["usable"].astype(str).str.lower() == "true")].copy()
    rows["material"] = rows["material"].astype(str).str.lower() == "true"
    out: dict = {"margin": MARGIN, "threshold": 0.5 + MARGIN}
    for label, keep_pairs, keep_rows in (
        ("all_sites", usable, rows),
        ("excluding_own_site", usable[usable["site"] != OWN_SITE], rows[rows["site"] != OWN_SITE]),
    ):
        url_days = float(keep_pairs["gap_days"].sum())
        material_pairs = int(keep_pairs["material"].sum())
        mat_rows = keep_rows[keep_rows["material"]]
        moved = mat_rows[mat_rows["move"] != "none"]
        rate = material_pairs / url_days if url_days else 0.0
        rows_per_pair = len(mat_rows) / material_pairs if material_pairs else 0.0
        moved_share = len(moved) / len(mat_rows) if len(mat_rows) else 0.0
        clusters = moved.groupby(["key", "day_a"]).ngroups
        per_100_week = rate * 100 * 7 * rows_per_pair * moved_share
        out[label] = {
            "usable_url_pairs": int(len(keep_pairs)),
            "observed_url_days": url_days,
            "distinct_urls": int(keep_pairs["key"].nunique()),
            "material_url_pairs": material_pairs,
            "material_rate_per_url_day": rate,
            "material_rows": int(len(mat_rows)),
            "material_moved_rows": int(len(moved)),
            "rows_per_material_pair": rows_per_pair,
            "moved_share": moved_share,
            "design_effect_rows_per_changed_page": (len(moved) / clusters) if clusters else 0.0,
            "changed_and_moved_rows_per_week_per_100_urls": per_100_week,
            "changed_and_moved_rows_per_week_current_set": per_100_week * keep_pairs["key"].nunique() / 100.0,
        }
    z_a, z_b = 1.959964, 0.841621
    threshold = 0.5 + MARGIN
    design_effect = max(1.0, out["all_sites"]["design_effect_rows_per_changed_page"])
    weekly_current = out["all_sites"]["changed_and_moved_rows_per_week_current_set"]
    weekly_ext = out["excluding_own_site"]["changed_and_moved_rows_per_week_per_100_urls"]
    table = []
    for p_true in (0.60, 0.65, 0.70, 0.75, 0.80):
        n_eff = ((z_a * math.sqrt(threshold * (1 - threshold)) + z_b * math.sqrt(p_true * (1 - p_true))) / (p_true - threshold)) ** 2
        n_rows = max(40.0, n_eff * design_effect)
        table.append({
            "true_accuracy": p_true,
            "n_rows_80pct_power": math.ceil(n_rows),
            "weeks_current_set": (n_rows / weekly_current) if weekly_current else None,
            "weeks_per_100_non_client_urls": (n_rows / weekly_ext) if weekly_ext else None,
        })
    out["n_for_ci_low_above_threshold"] = table
    out["n40_min_hits_wilson_low_above_0.55"] = next(k for k in range(41) if _wilson_low(k, 40) > threshold)
    out["n40_min_hits_wilson_low_above_0.50"] = next(k for k in range(41) if _wilson_low(k, 40) > 0.5)
    content_table = []
    for q in (0.6, 0.7, 0.8):
        discordant = ((z_a + z_b) / (2 * (q - 0.5))) ** 2
        content_table.append({
            "content_right_on_disagreements": q,
            "disagreement_rows_needed": math.ceil(discordant),
            "changed_moved_rows_needed": math.ceil(discordant / discordance) if discordance else None,
            "weeks_current_set": (discordant / discordance / weekly_current) if discordance and weekly_current else None,
        })
    out["design_disagreement_share_both_vs_position"] = discordance
    out["n_for_content_beating_position"] = content_table
    return out


def main() -> int:
    params = load_params()
    data = pd.read_csv(OUT / "dataset.csv")
    pairs = pd.read_csv(OUT / "fetch_pairs.csv")
    assert data["day_b"].max() <= rsdata.DESIGN_MAX_DATE
    frame = _prepare(OUT / "dataset.csv")
    frame["position_rule"] = [int(predict_direction_position_only(row, params) == "up") for _, row in frame.iterrows()]
    frame["both_rule"] = [int(predict_direction(row, params) == "up") for _, row in frame.iterrows()]
    frame["content_rule"] = [_content_rule(row, params) for _, row in frame.iterrows()]
    frame["content_fired"] = [bool(row["is_material"] and content_score(row, params) != 0) for _, row in frame.iterrows()]
    forward = _forward_predictions(frame)
    test = frame.loc[forward.index].copy()
    for name in ("majority", "position_logit", "content_logit", "both_logit"):
        test[name] = forward[name].astype(int)
    for name in METHODS:
        test[f"hit_{name}"] = (test[name] == test["y"]).astype(int)
    subsets = {
        "material": test["is_material"],
        "control": ~test["is_material"],
        "all": pd.Series(True, index=test.index),
        "material_top10_at_a": test["is_material"] & test["is_top10_a"],
        "control_top10_at_a": ~test["is_material"] & test["is_top10_a"],
    }
    results: dict = {"label": "PREDICTED", "first_test_day": FIRST_TEST_DAY, "design_max_date": rsdata.DESIGN_MAX_DATE,
                     "params": params, "accuracy": {}, "up_share": {}, "paired": {}, "material_vs_control": {}}
    for subset, mask in subsets.items():
        part = test[mask]
        results["up_share"][subset] = {"n": int(len(part)), "up": int(part["y"].sum())}
        results["accuracy"][subset] = {name: M.bootstrap_rate(part, f"hit_{name}") for name in METHODS}
    for subset in ("material", "all", "control"):
        part = test[subsets[subset]]
        results["paired"][subset] = {
            "both_rule_minus_position_rule": M.bootstrap_difference(part, "hit_both_rule", "hit_position_rule"),
            "both_logit_minus_position_logit": M.bootstrap_difference(part, "hit_both_logit", "hit_position_logit"),
            "content_rule_minus_majority": M.bootstrap_difference(part, "hit_content_rule", "hit_majority"),
            "position_rule_minus_majority": M.bootstrap_difference(part, "hit_position_rule", "hit_majority"),
        }
    for name in ("position_rule", "both_rule", "position_logit", "both_logit"):
        results["material_vs_control"][name] = _difference_between_sets(test, f"hit_{name}", test["is_material"], ~test["is_material"])
    fired = test[test["is_material"] & test["content_fired"]]
    results["content_step_on_material"] = {
        "fired": int(len(fired)),
        "right": int((fired["content_rule"] == fired["y"]).sum()),
        "disagrees_with_position": int((fired["both_rule"] != fired["position_rule"]).sum()),
    }
    all_material = frame[frame["is_material"]]
    results["all_design_material_rows_fixed_rules"] = {
        name: M.bootstrap_rate(all_material.assign(hit=(all_material[name] == all_material["y"]).astype(int)), "hit")
        for name in ("position_rule", "both_rule", "content_rule")
    }
    discordance = float((all_material["both_rule"] != all_material["position_rule"]).mean()) if len(all_material) else 0.0
    results["yield_and_power"] = _yield_and_power(pairs, data, discordance)
    keep = ["query", "key", "site", "day_a", "day_b", "pos_a", "pos_b", "move", "material", "material_reasons", "train_rows"]
    predictions = test.join(forward[["train_rows"]], rsuffix="_fw")[keep + METHODS].copy()
    for name in METHODS:
        predictions[name] = np.where(predictions[name] == 1, "up", "down")
    predictions.insert(0, "label", "PREDICTED")
    predictions.to_csv(OUT / "design_predictions.csv", index=False)
    (OUT / "eval.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=float) + "\n", encoding="utf-8")
    lines = ["# Target 2 direction on design data (PREDICTED). Generated by `python -m scripts.claude.t2_eval`.", ""]
    lines.append(f"Test rows: usable pairs with a rank move and day_a >= {FIRST_TEST_DAY}. Query-bootstrap 95% CI.")
    lines.append("")
    lines.append("| subset | n (up) | " + " | ".join(METHODS) + " |")
    lines.append("| --- | --- | " + " | ".join("---" for _ in METHODS) + " |")
    for subset in subsets:
        share = results["up_share"][subset]
        cells = [M.fmt_rate(results["accuracy"][subset][name]) for name in METHODS]
        lines.append(f"| {subset} | {share['n']} ({share['up']}) | " + " | ".join(cells) + " |")
    lines += ["", "Paired differences (same rows):", ""]
    for subset, block in results["paired"].items():
        for name, value in block.items():
            lines.append(f"- {subset}: {name} {M.fmt_diff(value)} (n={value['n']}, queries={value['queries']})")
    lines += ["", "Material minus control accuracy (queries resampled jointly):", ""]
    for name, value in results["material_vs_control"].items():
        lines.append(f"- {name}: {value['diff']:+.3f} [{value['lo']:+.3f}, {value['hi']:+.3f}] (n {value['n_left']} vs {value['n_right']})")
    step = results["content_step_on_material"]
    lines += ["", f"Content step on material test rows: fired {step['fired']}, right {step['right']}, changed the position answer {step['disagrees_with_position']}.", ""]
    lines.append("All design material moved rows (incl. Nov 6->9, fixed rules): " + "; ".join(
        f"{name} {M.fmt_rate(value)}" for name, value in results["all_design_material_rows_fixed_rules"].items()))
    lines += ["", "Yield and power:", "", "```", json.dumps(results["yield_and_power"], indent=2, default=float), "```", ""]
    (OUT / "eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
