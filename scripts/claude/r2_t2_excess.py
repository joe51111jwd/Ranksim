"""Round 2, Target 2, Instrument A: do changed pages move more than matched unchanged pages?

This is a frozen test instrument for claim 2 of evals/claude/theory.md section 2. It measures. It
predicts nothing, so every number it writes is a real Google position from Serper, except the
prediction statement in the prediction_check block, which is labelled PREDICTED.

Rows
  scripts.claude.t2_dataset.build_rows(max_date): one row per (search, URL, fetch pair A to B).
  Held-out run: exactly the selection of score_heldout.score_t2: serp_day_b on or after 2026-11-18,
  search group o30 or a70 (rsdata.query_groups over rsdata.load_rows(max_date)), usable true.
  Design run (--design): the same, except serp_day_b on or before 2026-11-17 and max_date at most
  2026-11-17. On design days every usable row is in group o30, so the group filter changes nothing.

Method (the same as Q1 of scripts/claude/r2_t2_signal.py)
  A row is changed when the round 1 material flag is set. Each changed row is compared with the mean
  of the unchanged rows in its stratum. A stratum is the SERP date pair plus the start band of pos_a
  (1, 2 to 3, 4 to 6, 7 to 10, 11 for absent). A changed row in a stratum without an unchanged row
  is dropped and counted. Outcomes: moved (pos_a differs from pos_b), mean absolute change in
  consensus position, crossed (the row left or entered the top 10). Interval: 95% cluster bootstrap,
  2000 draws, seed 20261119, clusters sorted, control means re-estimated inside every draw. Clusters:
  search (primary), URL and site. Cuts: all rows, start 2 to 10, both without the client site
  (smartcrewhq.com, treated and control rows alike), material against rows with no change at all,
  and any change against none.

Prediction checked (theory.md, stated before scoring, PREDICTED): the difference in P(moved),
material versus matched unchanged, all rows, search clusters, is below +0.20 and its 95% interval
includes zero. The theory's falsifier (difference above +0.20 with the interval above zero) is
reported next to it.

Seal
  A held-out run calls require_frozen_dir on the --prereg folder before it loads anything and
  refuses to overwrite an earlier output unless --force is given. A design run refuses a max_date
  after 2026-11-17, asserts that no SERP day or fetch day after the cutoff was loaded, and uses its
  own page extraction cache (DESIGN_CACHE) so that it never unpickles extractions a held-out run
  may have added to the shared cache.

Run from the repo root with the venv active:
  python scripts/claude/r2_t2_excess.py --prereg evals/preregistered/claude-r2-t2-excess --max-date YYYY-MM-DD
  python scripts/claude/r2_t2_excess.py --design
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Imported at module level on purpose: scripts/claude/freeze.py closure then records every file the
# instrument depends on, including the lazily imported t2_direction that build_rows uses.
from scripts.claude import metrics as mt  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import score_heldout as sh  # noqa: E402
from scripts.claude import t2_common  # noqa: E402
from scripts.claude import t2_dataset as td  # noqa: E402
from scripts.claude import t2_direction  # noqa: E402,F401
from scripts.claude.r2_score import require_frozen_dir  # noqa: E402

DESIGN_MAX_DATE = "2026-11-17"
assert rs.DESIGN_MAX_DATE == DESIGN_MAX_DATE, "design cutoff changed"
FIRST_HELDOUT_DAY = sh.FIRST_HELDOUT_DAY  # "2026-11-18"
DECISION_GROUPS = sh.DECISION_GROUPS  # ("o30", "a70")
CLIENT_SITE = sh.T2_OWN_SITE  # "smartcrewhq.com"
ABSENT = rs.ABSENT

OUT_HELDOUT = ROOT / "evals" / "claude" / "heldout-r2"
OUT_DESIGN = ROOT / "evals" / "claude" / "r2" / "t2-excess-design"
DESIGN_CACHE = Path("/tmp/t2-extract-cache-r2-design.pkl")

BANDS = ((1, 1, "1"), (2, 3, "2-3"), (4, 6, "4-6"), (7, 10, "7-10"), (11, 11, "11"))
OUTCOMES = ("moved", "absd", "crossed")
CLUSTERS = ("query", "key", "site")
CLUSTER_NAMES = {"query": "search", "key": "URL", "site": "site"}

PREDICTION_LIMIT = 0.20
PREDICTION_TEXT = (
    "PREDICTED (evals/claude/theory.md section 2, stated before scoring): the difference in P(moved), "
    "material versus matched unchanged, all rows, is below +0.20 and its 95% interval includes zero."
)
FALSIFIER_TEXT = (
    "theory.md 'What would prove this wrong': changed pages move clearly more than matched unchanged "
    "pages, difference above +0.20 with the 95% interval above zero."
)

# Design numbers from evals/claude/r2/t2-signal/notes.md, Q1 (3 decimals as printed there).
# (comparison, outcome, cluster, changed mean, matched unchanged mean, diff, lo, hi, matched, changed)
DESIGN_EXPECTED = (
    ("material_vs_not", "moved", "query", 0.640, 0.557, 0.083, -0.082, 0.267, 25, 28),
    ("material_vs_not", "absd", "query", 1.720, 1.936, -0.216, -1.049, 0.593, 25, 28),
    ("material_vs_not", "crossed", "query", 0.160, 0.281, -0.121, -0.258, 0.083, 25, 28),
    ("material_vs_not", "moved", "key", 0.640, 0.557, 0.083, -0.113, 0.348, 25, 28),
    ("material_vs_no_change", "moved", "query", 0.640, 0.558, 0.082, -0.093, 0.281, 25, 28),
    ("material_vs_not_start_2_10", "moved", "query", 0.875, 0.660, 0.215, 0.033, 0.400, 16, 16),
    ("material_vs_not_start_2_10", "absd", "query", 2.125, 1.995, 0.130, -0.785, 0.960, 16, 16),
    ("material_vs_not_start_2_10", "crossed", "query", 0.125, 0.228, -0.103, -0.325, 0.154, 16, 16),
    ("material_vs_not_start_2_10", "moved", "key", 0.875, 0.660, 0.215, 0.032, 0.389, 16, 16),
    ("material_vs_not_start_2_10", "moved", "site", 0.875, 0.660, 0.215, -0.108, 0.384, 16, 16),
    ("material_vs_not_without_client", "moved", "query", 0.800, 0.543, 0.257, -0.003, 0.496, 10, 10),
    ("material_vs_not_without_client", "absd", "query", 2.000, 1.420, 0.580, -0.700, 1.918, 10, 10),
    ("material_vs_not_start_2_10_without_client", "moved", "query", 0.875, 0.622, 0.253, -0.010, 0.500, 8, 8),
    ("any_change_vs_none", "moved", "query", 0.536, 0.540, -0.004, -0.121, 0.114, 97, 101),
    ("any_change_vs_none", "absd", "query", 1.237, 1.793, -0.556, -1.116, 0.091, 97, 101),
    ("any_change_vs_none", "crossed", "query", 0.144, 0.241, -0.096, -0.182, 0.003, 97, 101),
)
DESIGN_TOLERANCE = 0.0005 + 1e-9


# ----------------------------------------------------------------------------------------------
# Statistics (copied from scripts/claude/r2_t2_signal.py; a unit test checks they agree)


def band(pos: int) -> str:
    for low, high, label in BANDS:
        if low <= int(pos) <= high:
            return label
    raise ValueError(pos)


def _cluster_weights(n_clusters: int, draws: int = mt.BOOT_DRAWS, seed: int = mt.BOOT_SEED) -> np.ndarray:
    """Multiplicity of each cluster in each draw. Same rng call as metrics.bootstrap_rate."""
    rng = np.random.default_rng(seed)
    index = rng.integers(0, n_clusters, size=(draws, n_clusters))
    weights = np.zeros((draws, n_clusters))
    np.add.at(weights, (np.arange(draws)[:, None], index), 1.0)
    return weights


def _empty_effect(frame: pd.DataFrame, treat: str, cluster: str) -> dict:
    treated = int(frame[treat].astype(bool).sum()) if len(frame) else 0
    return {
        "treated_rows": treated, "treated_rows_matched": 0, "treated_rows_dropped_no_control": treated,
        "control_rows": int(len(frame) - treated), "control_rows_in_matched_strata": 0, "clusters": 0,
        "cluster": cluster, "treated_mean": None, "matched_control_mean": None, "diff": None, "lo": None,
        "hi": None, "attributable_share": 0.0, "attributable_share_hi95": None, "attributable_share_hi90": None,
        "valid_draws": 0,
    }


def strat_effect(frame: pd.DataFrame, treat: str, outcome: str, stratum: str, cluster: str = "query") -> dict:
    """Treated-minus-matched-control mean of ``outcome``, exact stratification on ``stratum``.

    Each treated row is compared with the mean of the control rows in its stratum. Treated rows in a
    stratum with no control row are dropped and counted. Interval: joint cluster bootstrap, so the
    control means are re-estimated in every draw. Same code as r2_t2_signal.strat_effect, plus a
    guard that returns empty fields when there is no row at all.
    """
    use = frame[[cluster, stratum, treat, outcome]].dropna()
    if use.empty:
        return _empty_effect(use, treat, cluster)
    clusters = sorted(use[cluster].unique())
    strata = sorted(use[stratum].unique())
    c_index = {value: i for i, value in enumerate(clusters)}
    s_index = {value: i for i, value in enumerate(strata)}
    shape = (len(clusters), len(strata))
    t_y, t_n, c_y, c_n = (np.zeros(shape) for _ in range(4))
    for cl, st, tr, y in zip(use[cluster], use[stratum], use[treat], use[outcome], strict=True):
        i, j = c_index[cl], s_index[st]
        if bool(tr):
            t_y[i, j] += float(y)
            t_n[i, j] += 1
        else:
            c_y[i, j] += float(y)
            c_n[i, j] += 1

    def calc(weights: np.ndarray):
        ty, tn, cy, cn = weights @ t_y, weights @ t_n, weights @ c_y, weights @ c_n
        ok = (tn > 0) & (cn > 0)
        cmean = np.divide(cy, cn, out=np.zeros_like(cy), where=cn > 0)
        den = np.where(ok, tn, 0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            tmean = np.where(ok, ty, 0).sum(axis=1) / den
            cexp = np.where(ok, tn * cmean, 0).sum(axis=1) / den
        return tmean, cexp, den

    tmean, cexp, den = calc(np.ones((1, len(clusters))))
    boot_t, boot_c, boot_den = calc(_cluster_weights(len(clusters)))
    valid = (boot_den > 0) & np.isfinite(boot_t) & np.isfinite(boot_c)
    diff = boot_t[valid] - boot_c[valid]
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(boot_t[valid] > 0, np.clip(diff / boot_t[valid], 0.0, None), 0.0)
    matched_strata = (t_n.sum(axis=0) > 0) & (c_n.sum(axis=0) > 0)
    point_diff = float(tmean[0] - cexp[0]) if den[0] > 0 else float("nan")
    point_share = max(0.0, point_diff / float(tmean[0])) if den[0] > 0 and tmean[0] > 0 else 0.0
    return {
        "treated_rows": int(t_n.sum()),
        "treated_rows_matched": int(den[0]),
        "treated_rows_dropped_no_control": int(t_n.sum() - den[0]),
        "control_rows": int(c_n.sum()),
        "control_rows_in_matched_strata": int(c_n[:, matched_strata].sum()),
        "clusters": len(clusters),
        "cluster": cluster,
        "treated_mean": float(tmean[0]) if den[0] > 0 else None,
        "matched_control_mean": float(cexp[0]) if den[0] > 0 else None,
        "diff": point_diff,
        "lo": float(np.percentile(diff, 2.5)) if len(diff) else None,
        "hi": float(np.percentile(diff, 97.5)) if len(diff) else None,
        "attributable_share": point_share,
        "attributable_share_hi95": float(np.percentile(share, 97.5)) if len(share) else None,
        "attributable_share_hi90": float(np.percentile(share, 95.0)) if len(share) else None,
        "valid_draws": int(valid.sum()),
    }


def jsonable(value):
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(float(value)) else round(float(value), 6)
    return value


# ----------------------------------------------------------------------------------------------
# Rows


def use_design_cache() -> None:
    """Point the page extraction cache at a file only design runs write (sealed page ids only)."""
    t2_common.EXTRACT_CACHE = DESIGN_CACHE


def design_cache_seal(max_date: str) -> dict:
    """Every page id in the design extraction cache must be in the sealed page index."""
    sealed = set(rs.load_page_index(max_date)["id"].astype(int))
    cached = set(pickle.loads(DESIGN_CACHE.read_bytes()).keys()) if DESIGN_CACHE.is_file() else set()
    outside = len(cached - sealed)
    assert outside == 0, "the design extraction cache holds a page outside the sealed index"
    return {"extract_cache": str(DESIGN_CACHE), "cache_ids": len(cached), "cache_ids_outside_sealed_index": outside}


def load_inputs(max_date: str) -> tuple[pd.DataFrame, dict[str, str]]:
    """The dataset rows and the search groups, both sealed at max_date."""
    rows = td.build_rows(max_date=max_date)
    groups = rs.query_groups(rs.load_rows(max_date))
    return rows, groups


def select_rows(rows: pd.DataFrame, groups: dict[str, str], design: bool) -> pd.DataFrame:
    """score_heldout.score_t2's held-out selection, or the design days with --design."""
    if rows.empty:
        return rows.copy()
    if design:
        keep = rows["serp_day_b"] <= DESIGN_MAX_DATE
    else:
        keep = rows["serp_day_b"] >= FIRST_HELDOUT_DAY
    keep &= rows["query"].map(groups).isin(DECISION_GROUPS)
    keep &= rows["usable"].astype(bool)
    out = rows[keep].copy()
    out["group"] = out["query"].map(groups)
    return out.reset_index(drop=True)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Outcome, stratum and treatment columns. pos_b is the outcome; nothing else reads it."""
    data = frame.copy()
    if data.empty:
        text = ("query", "group", "key", "site", "day_a", "day_b", "serp_day_a", "serp_day_b", "band_a", "date_pair", "stratum")
        numbers = ("pos_a", "pos_b", "edit_tokens", "moved", "absd", "crossed")
        flags = ("material", "anychange", "interior", "title_changed", "h1_changed")
        columns = {c: pd.Series(dtype=object) for c in text} | {c: pd.Series(dtype=float) for c in numbers}
        return pd.DataFrame(columns | {c: pd.Series(dtype=bool) for c in flags})
    data["material"] = data["material"].astype(bool)
    data["moved"] = (data["pos_a"] != data["pos_b"]).astype(int)
    data["absd"] = (data["pos_a"] - data["pos_b"]).abs().astype(float)
    data["crossed"] = ((data["pos_a"] <= 10) != (data["pos_b"] <= 10)).astype(int)
    data["band_a"] = data["pos_a"].map(band)
    data["date_pair"] = data["serp_day_a"].astype(str) + ">" + data["serp_day_b"].astype(str)
    data["stratum"] = data["date_pair"] + "|" + data["band_a"]
    data["interior"] = data["pos_a"].between(2, 10)
    data["anychange"] = (data["edit_tokens"] > 0) | data["title_changed"].astype(bool) | data["h1_changed"].astype(bool)
    return data


# ----------------------------------------------------------------------------------------------
# Comparisons


def comparison_sets(data: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, str, str]]:
    """name -> (rows, treatment column, description). Without the client site drops its control rows too."""
    other = data[data["site"] != CLIENT_SITE]
    return {
        "material_vs_not": (data, "material", "Material vs not, all rows"),
        "material_vs_not_start_2_10": (data[data["interior"]], "material", "Material vs not, start 2 to 10"),
        "material_vs_not_without_client": (other, "material", "Material vs not, without the client site"),
        "material_vs_not_start_2_10_without_client": (other[other["interior"]], "material",
                                                      "Material vs not, start 2 to 10, without the client site"),
        "material_vs_no_change": (data[data["material"] | ~data["anychange"]], "material",
                                  "Material vs no change at all"),
        "any_change_vs_none": (data, "anychange", "Any change vs none"),
    }


def compare_all(data: pd.DataFrame, outcomes: tuple[str, ...] = OUTCOMES, clusters: tuple[str, ...] = CLUSTERS) -> dict:
    out = {}
    for name, (rows, treat, label) in comparison_sets(data).items():
        block = {"label": label, "treatment": treat}
        for outcome in outcomes:
            block[outcome] = {cluster: strat_effect(rows, treat, outcome, "stratum", cluster) for cluster in clusters}
        out[name] = block
    return out


def counts(data: pd.DataFrame, comparisons: dict) -> dict:
    changed = data[data["material"]] if len(data) else data
    main = comparisons["material_vs_not"]["moved"]["query"]
    return {
        "usable_rows": int(len(data)),
        "changed_rows": int(len(changed)),
        "changed_rows_matched": int(main["treated_rows_matched"]),
        "changed_rows_dropped_no_control": int(main["treated_rows_dropped_no_control"]),
        "control_rows": int(len(data) - len(changed)),
        "control_rows_in_matched_strata": int(main["control_rows_in_matched_strata"]),
        "changed_urls": int(changed["key"].nunique()) if len(changed) else 0,
        "changed_sites": int(changed["site"].nunique()) if len(changed) else 0,
        "changed_url_pairs": int(changed.drop_duplicates(["key", "day_a", "day_b"]).shape[0]) if len(changed) else 0,
        "changed_rows_client_site": int((changed["site"] == CLIENT_SITE).sum()) if len(changed) else 0,
        "changed_rows_start_2_10": int(changed["interior"].sum()) if len(changed) else 0,
        "changed_rows_moved": int(changed["moved"].sum()) if len(changed) else 0,
        "any_change_rows": int(data["anychange"].sum()) if len(data) else 0,
        "queries": int(data["query"].nunique()) if len(data) else 0,
        "queries_with_changed_rows": int(changed["query"].nunique()) if len(changed) else 0,
        "by_group": {str(k): int(v) for k, v in data.groupby("group").size().items()} if len(data) and "group" in data else {},
        "date_pairs": sorted(data["date_pair"].unique().tolist()) if len(data) else [],
    }


def prediction_check(comparisons: dict, count: dict) -> dict:
    main = comparisons["material_vs_not"]["moved"]["query"]
    diff, lo, hi = main["diff"], main["lo"], main["hi"]
    evaluable = main["treated_mean"] is not None and lo is not None and hi is not None
    below = bool(evaluable and diff < PREDICTION_LIMIT)
    includes_zero = bool(evaluable and lo <= 0.0 <= hi)
    return {
        "label": "PREDICTED",
        "statement": PREDICTION_TEXT,
        "comparison": "material_vs_not, outcome moved, search clusters",
        "evaluable": bool(evaluable),
        "changed_mean": main["treated_mean"],
        "matched_unchanged_mean": main["matched_control_mean"],
        "observed_diff": diff if evaluable else None,
        "ci_lo": lo,
        "ci_hi": hi,
        "below_plus_0_20": below,
        "interval_includes_zero": includes_zero,
        "prediction_held": bool(below and includes_zero) if evaluable else None,
        "falsifier": FALSIFIER_TEXT,
        "falsifier_met": bool(evaluable and diff > PREDICTION_LIMIT and lo > 0.0),
        "counts": {
            "changed_rows": count["changed_rows"],
            "matched_rows": count["changed_rows_matched"],
            "changed_urls": count["changed_urls"],
            "sites": count["changed_sites"],
            "control_rows_in_matched_strata": count["control_rows_in_matched_strata"],
            "queries": count["queries"],
        },
    }


def design_reproduction(comparisons: dict) -> dict:
    """Compare a design run with the numbers printed in evals/claude/r2/t2-signal/notes.md, Q1."""
    checks, ok = [], True
    for name, outcome, cluster, t_mean, c_mean, diff, lo, hi, matched, treated in DESIGN_EXPECTED:
        got = comparisons[name][outcome][cluster]
        pairs = {"treated_mean": t_mean, "matched_control_mean": c_mean, "diff": diff, "lo": lo, "hi": hi}
        bad = [field for field, want in pairs.items()
               if got[field] is None or abs(float(got[field]) - want) > DESIGN_TOLERANCE]
        if got["treated_rows_matched"] != matched or got["treated_rows"] != treated:
            bad.append("counts")
        ok &= not bad
        checks.append({"comparison": name, "outcome": outcome, "cluster": cluster, "expected": {**pairs, "matched": matched, "changed": treated},
                       "observed": {field: got[field] for field in pairs} | {"matched": got["treated_rows_matched"], "changed": got["treated_rows"]},
                       "mismatch": bad})
    return {"source": "evals/claude/r2/t2-signal/notes.md, Q1", "tolerance": DESIGN_TOLERANCE, "ok": bool(ok), "checks": checks}


def analyse(rows: pd.DataFrame, groups: dict[str, str], *, design: bool, max_date: str) -> tuple[dict, pd.DataFrame]:
    data = prepare(select_rows(rows, groups, design))
    comparisons = compare_all(data)
    count = counts(data, comparisons)
    report = {
        "label": ("MEASURED. Real Google positions from Serper. The only PREDICTED item is the statement in "
                  "prediction_check, written before scoring."),
        "instrument": "r2_t2_excess (Target 2, Instrument A: excess movement of changed pages)",
        "mode": "design" if design else "held-out",
        "max_date": max_date,
        "selection": ({"serp_day_b": f"<= {DESIGN_MAX_DATE}"} if design else {"serp_day_b": f">= {FIRST_HELDOUT_DAY}"})
        | {"groups": list(DECISION_GROUPS), "usable": True},
        "method": {
            "stratum": "SERP date pair (serp_day_a>serp_day_b) plus start band of pos_a: 1, 2-3, 4-6, 7-10, 11",
            "outcomes": {"moved": "pos_b != pos_a", "absd": "|pos_b - pos_a|", "crossed": "pos_a <= 10 != pos_b <= 10"},
            "bootstrap": {"draws": mt.BOOT_DRAWS, "seed": mt.BOOT_SEED, "clusters": [CLUSTER_NAMES[c] for c in CLUSTERS],
                          "control_means": "re-estimated inside every draw"},
            "client_site": CLIENT_SITE,
        },
        "counts": count,
        "prediction_check": prediction_check(comparisons, count),
        "comparisons": comparisons,
    }
    if design:
        report["design_reproduction"] = design_reproduction(comparisons)
    return report, data


# ----------------------------------------------------------------------------------------------
# Output


def fmt_num(value, digits: int = 3, sign: bool = False) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def effect_cell(res: dict, digits: int = 3) -> str:
    if res.get("treated_mean") is None:
        return "n/a"
    return f"{fmt_num(res['diff'], digits, True)} [{fmt_num(res['lo'], digits, True)}, {fmt_num(res['hi'], digits, True)}]"


TABLE_ROWS = (
    ("material_vs_not", "moved", ("query", "key", "site")),
    ("material_vs_not", "absd", ("query",)),
    ("material_vs_not", "crossed", ("query",)),
    ("material_vs_not_start_2_10", "moved", ("query", "key", "site")),
    ("material_vs_not_start_2_10", "absd", ("query",)),
    ("material_vs_not_without_client", "moved", ("query",)),
    ("material_vs_not_without_client", "absd", ("query",)),
    ("material_vs_not_start_2_10_without_client", "moved", ("query",)),
    ("material_vs_no_change", "moved", ("query",)),
    ("any_change_vs_none", "moved", ("query", "key")),
    ("any_change_vs_none", "absd", ("query",)),
)
OUTCOME_NAMES = {"moved": "P(moved)", "absd": "mean abs change", "crossed": "P(crossed top 10)"}


def comparison_table(comparisons: dict, rows=TABLE_ROWS) -> list[str]:
    lines = ["| Comparison | Clusters | Changed rows matched | Outcome | Changed | Matched unchanged | Difference [95% CI] |",
             "| --- | --- | ---: | --- | ---: | ---: | --- |"]
    for name, outcome, clusters in rows:
        for cluster in clusters:
            res = comparisons[name][outcome][cluster]
            digits = 2 if outcome == "absd" else 3
            lines.append(
                f"| {comparisons[name]['label']} | {CLUSTER_NAMES[cluster]} | {res['treated_rows_matched']} of {res['treated_rows']} | "
                f"{OUTCOME_NAMES[outcome]} | {fmt_num(res['treated_mean'], digits)} | {fmt_num(res['matched_control_mean'], digits)} | "
                f"{effect_cell(res, digits)} |")
    return lines


def markdown(report: dict) -> str:
    pc, count = report["prediction_check"], report["counts"]
    title = ("Design run (days through 2026-11-17), not a test" if report["mode"] == "design"
             else f"Held-out rows through {report['max_date']}")
    lines = [
        f"# Target 2, Instrument A: excess movement. {title}",
        "",
        "Generated by `scripts/claude/r2_t2_excess.py`. Positions are real Google positions from Serper. "
        "Each changed (material) row is compared with the mean of the unchanged rows in its stratum "
        "(SERP date pair plus start band). 95% cluster bootstrap, 2000 draws, seed 20261119.",
        "",
        f"Rows: {count['usable_rows']} usable, {count['changed_rows']} changed ({count['changed_rows_matched']} matched), "
        f"{count['changed_urls']} changed URLs on {count['changed_sites']} sites, {count['changed_rows_client_site']} changed rows on "
        f"the client site, {count['queries']} searches.",
        "",
        *comparison_table(report["comparisons"]),
        "",
        "## Prediction check",
        "",
        pc["statement"],
        "",
    ]
    if pc["evaluable"]:
        lines.append(
            f"Observed: {fmt_num(pc['changed_mean'])} against {fmt_num(pc['matched_unchanged_mean'])}, difference "
            f"{fmt_num(pc['observed_diff'], 3, True)} [{fmt_num(pc['ci_lo'], 3, True)}, {fmt_num(pc['ci_hi'], 3, True)}]. "
            f"Below +0.20: {pc['below_plus_0_20']}. Interval includes zero: {pc['interval_includes_zero']}. "
            f"Prediction held: {pc['prediction_held']}. Falsifier met: {pc['falsifier_met']}.")
    else:
        lines.append("Not evaluable: no changed row has a matched unchanged row.")
    if "design_reproduction" in report:
        lines += ["", f"Design reproduction of notes.md Q1 ({len(report['design_reproduction']['checks'])} numbers): "
                  f"{'ok' if report['design_reproduction']['ok'] else 'MISMATCH'}."]
    return "\n".join(lines) + "\n"


ROW_COLUMNS = ["query", "group", "key", "site", "day_a", "day_b", "serp_day_a", "serp_day_b", "pos_a", "pos_b", "band_a",
               "stratum", "material", "anychange", "edit_tokens", "title_changed", "h1_changed", "moved", "absd", "crossed"]


def display(path: Path) -> str:
    """Repo-relative path when the path is inside the repo, else the path itself."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def output_paths(prereg: Path | None, design: bool, max_date: str) -> dict[str, Path]:
    if design:
        base, stem = OUT_DESIGN, "excess-design"
    else:
        base, stem = OUT_HELDOUT / prereg.name, f"excess-{max_date}"
    return {"json": base / f"{stem}.json", "md": base / f"{stem}.md", "rows": base / f"{stem}-rows.csv"}


def refuse_overwrite(paths: dict[str, Path], force: bool) -> None:
    for path in paths.values():
        if path.exists() and not force:
            raise SystemExit(f"{path} already exists. A held-out run is written once. Use --force only to redo an identical run.")


def run(max_date: str | None, prereg: Path | None, design: bool, force: bool = False) -> dict:
    if design:
        max_date = max_date or DESIGN_MAX_DATE
        if max_date > DESIGN_MAX_DATE:
            raise SystemExit(f"--design reads design days only; max_date must be on or before {DESIGN_MAX_DATE}.")
        manifest = None
        paths = output_paths(None, True, max_date)
        use_design_cache()
    else:
        if prereg is None or max_date is None:
            raise SystemExit("A held-out run needs --prereg and --max-date.")
        if max_date < FIRST_HELDOUT_DAY:
            raise SystemExit(f"Not a held-out day: max_date must be on or after {FIRST_HELDOUT_DAY}.")
        if not (prereg / "manifest.json").is_file():
            raise SystemExit(f"No manifest.json in {prereg}. Freeze the instrument first.")
        manifest = require_frozen_dir(prereg)  # before anything is loaded
        paths = output_paths(prereg, False, max_date)
        refuse_overwrite(paths, force)

    rows, groups = load_inputs(max_date)
    report, data = analyse(rows, groups, design=design, max_date=max_date)
    if design:
        seal = {"cutoff": DESIGN_MAX_DATE,
                "max_serp_day_b_loaded": str(rows["serp_day_b"].max()), "max_fetch_day_b_loaded": str(rows["day_b"].max())}
        assert seal["max_serp_day_b_loaded"] <= DESIGN_MAX_DATE and seal["max_fetch_day_b_loaded"] <= DESIGN_MAX_DATE
        report["seal"] = seal | design_cache_seal(max_date)
    else:
        report["prereg"] = display(prereg)
        report["manifest_frozen_at"] = manifest.get("frozen_at_utc")

    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    paths["json"].write_text(json.dumps(jsonable(report), indent=2) + "\n", encoding="utf-8")
    paths["md"].write_text(markdown(report), encoding="utf-8")
    data[[c for c in ROW_COLUMNS if c in data.columns]].to_csv(paths["rows"], index=False)

    pc = report["prediction_check"]
    print(f"Instrument A, {report['mode']} run through {max_date}")
    print(f"  counts: {json.dumps(pc['counts'])}")
    for line in comparison_table(report["comparisons"]):
        print("  " + line)
    print(f"  prediction held: {pc['prediction_held']} (diff {fmt_num(pc['observed_diff'], 3, True)} "
          f"[{fmt_num(pc['ci_lo'], 3, True)}, {fmt_num(pc['ci_hi'], 3, True)}]); falsifier met: {pc['falsifier_met']}")
    print(f"  wrote {display(paths['json'])}")
    if design and not report["design_reproduction"]["ok"]:
        bad = [c for c in report["design_reproduction"]["checks"] if c["mismatch"]]
        raise SystemExit(f"Design reproduction failed on {len(bad)} numbers: {json.dumps(jsonable(bad))}")
    if design:
        print("  design reproduction of notes.md Q1: ok")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prereg", help="Round 2 preregistration folder (required unless --design).")
    parser.add_argument("--max-date", help="Last SERP and fetch day to load (YYYY-MM-DD).")
    parser.add_argument("--design", action="store_true", help="Design days only (serp_day_b <= 2026-11-17). No freeze needed.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing held-out output (identical rerun only).")
    args = parser.parse_args(argv)
    prereg = (ROOT / args.prereg).resolve() if args.prereg else None
    run(args.max_date, prereg, args.design, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
