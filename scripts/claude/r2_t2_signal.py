"""Round 2, Target 2 research step: do real page edits visibly move rank, and does the SERP show re-indexing?

Measurement only. Nothing here predicts or tunes anything.

  Q1 Excess movement. Rows whose page changed (round 1 dataset, t2_dataset.build) against rows whose
     page did not, stratified by date pair and starting position band. P(moved), mean |change|,
     P(left or entered the top 10). Upper bound on the share of moves on changed pages the edit can
     have caused. Same for "any change" with the size of the diff as a dose.
  Q2 Size of a fair content-versus-position test (closed-form power arithmetic, inputs from Q1).
  Q3 SERP-side change signals: title and date noise floor within a day versus across pulled days,
     SERP title versus our fetched title tag, "re-index events", and movement after them.
  Q4 Lag: do moves follow a change one or more pulled days later rather than in the same window?

Leakage seal
  SERP rows: rsdata.load_rows(DESIGN_MAX_DATE) for consensus positions, plus one local loader
  (load_serp_extra) for the snippet and Serper's per-result date. That loader's SQL has the cutoff
  hard-coded, opens the DB read-only and asserts the maximum day. Page fetches: only through
  t2_dataset.build(DESIGN_MAX_DATE), which uses rsdata.load_page_index and load_page_html. The script
  asserts that the maximum SERP day and the maximum page fetch day loaded are on or before 2026-11-17.
  No snapshot directory, no network, no writes outside evals/claude/r2/t2-signal/.

Intervals: query bootstrap with metrics.BOOT_DRAWS (2000) and metrics.BOOT_SEED (20261119). Plain rates and
paired differences call metrics.bootstrap_rate / bootstrap_difference. Stratified differences, where the
control means must be re-estimated in every draw, use the same resampling scheme (same rng, same draws,
clusters sorted) in strat_effect below.

Run from the repo root with the venv active:  python -m scripts.claude.r2_t2_signal
"""

from __future__ import annotations

import itertools
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aeo.textutil import salient_terms  # noqa: E402
from scripts.claude import metrics as mt  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import t2_dataset as td  # noqa: E402
from scripts.claude.t2_changes import norm, norm_tokens  # noqa: E402
from scripts.claude.t2_common import extracted  # noqa: E402
from scripts.claude.t2_direction import content_score, load_params, predict_direction_position_only  # noqa: E402

CUTOFF = "2026-11-17"
assert rs.DESIGN_MAX_DATE == CUTOFF, "design cutoff changed; this study is sealed at 2026-11-17"
OUT = ROOT / "evals/claude/r2/t2-signal"
ABSENT = rs.ABSENT

# The only direct query in this study. Cutoff hard-coded on purpose.
SEALED_SQL = (
    "SELECT query, snapshot_date AS day, fetched_at_utc AS fetched_at, position, url, title, snippet, raw_json, source "
    "FROM serp_results WHERE snapshot_date <= '2026-11-17' AND url IS NOT NULL AND position IS NOT NULL"
)

BANDS = ((1, 1, "1"), (2, 3, "2-3"), (4, 6, "4-6"), (7, 10, "7-10"), (11, 11, "11"))
DOSE_BINS = ("none", "1-49", "50-249", "250+")
Z_ALPHA = 1.959963984540054  # two-sided 95%: CI lower bound above 0
Z_BETA = 0.8416212335729143  # 80% power


# ----------------------------------------------------------------------------------------------
# Small helpers


def band(pos: int) -> str:
    for low, high, label in BANDS:
        if low <= int(pos) <= high:
            return label
    raise ValueError(pos)


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if (value is None or math.isnan(float(value))) else round(float(value), 6)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def _cluster_weights(n_clusters: int, draws: int = mt.BOOT_DRAWS, seed: int = mt.BOOT_SEED) -> np.ndarray:
    """Multiplicity of each cluster in each draw. Same rng call as metrics.bootstrap_rate."""
    rng = np.random.default_rng(seed)
    index = rng.integers(0, n_clusters, size=(draws, n_clusters))
    weights = np.zeros((draws, n_clusters))
    np.add.at(weights, (np.arange(draws)[:, None], index), 1.0)
    return weights


def strat_effect(frame: pd.DataFrame, treat: str, outcome: str, stratum: str, cluster: str = "query") -> dict:
    """Treated-minus-matched-control mean of ``outcome``, exact stratification on ``stratum``.

    Each treated row is compared with the mean of the control rows in its stratum. Treated rows in a
    stratum with no control row are dropped (counted). Interval: joint cluster bootstrap, so the
    control means are re-estimated in every draw. Also returns the attributable share
    max(0, diff / treated_mean), which is the probability of causation under monotonicity when the
    outcome is a 0/1 "moved" flag.
    """
    use = frame[[cluster, stratum, treat, outcome]].dropna()
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


def matched_base(frame: pd.DataFrame, treat: str, outcome: str, stratum: str) -> pd.DataFrame:
    """Treated rows with column 'base' = control mean of their stratum (controls fixed)."""
    control = frame[~frame[treat].astype(bool)].groupby(stratum)[outcome].mean()
    treated = frame[frame[treat].astype(bool)].copy()
    treated["base"] = treated[stratum].map(control)
    return treated[treated["base"].notna()]


def strat_slope(frame: pd.DataFrame, x: str, y: str, stratum: str, cluster: str = "query") -> dict:
    """Within-stratum least-squares slope of y on x (stratum fixed effects), joint cluster bootstrap."""
    use = frame[[cluster, stratum, x, y]].dropna()
    clusters = sorted(use[cluster].unique())
    strata = sorted(use[stratum].unique())
    c_index = {value: i for i, value in enumerate(clusters)}
    s_index = {value: i for i, value in enumerate(strata)}
    shape = (len(clusters), len(strata))
    n, sx, sy, sxy, sxx = (np.zeros(shape) for _ in range(5))
    for cl, st, xv, yv in zip(use[cluster], use[stratum], use[x], use[y], strict=True):
        i, j = c_index[cl], s_index[st]
        n[i, j] += 1
        sx[i, j] += xv
        sy[i, j] += yv
        sxy[i, j] += xv * yv
        sxx[i, j] += xv * xv

    def calc(weights: np.ndarray) -> np.ndarray:
        nn, ax, ay, axy, axx = (weights @ arr for arr in (n, sx, sy, sxy, sxx))
        safe = np.where(nn > 0, nn, 1.0)
        cxy = np.where(nn > 0, axy - ax * ay / safe, 0).sum(axis=1)
        cxx = np.where(nn > 0, axx - ax * ax / safe, 0).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return cxy / cxx

    point = float(calc(np.ones((1, len(clusters))))[0])
    boot = calc(_cluster_weights(len(clusters)))
    boot = boot[np.isfinite(boot)]
    return {"slope": point, "lo": float(np.percentile(boot, 2.5)), "hi": float(np.percentile(boot, 97.5)),
            "rows": int(len(use)), "clusters": len(clusters)}


def fmt_effect(res: dict, digits: int = 3) -> str:
    if res.get("treated_mean") is None:
        return "n/a"
    return (f"{res['treated_mean']:.{digits}f} vs {res['matched_control_mean']:.{digits}f}, "
            f"diff {res['diff']:+.{digits}f} [{res['lo']:+.{digits}f}, {res['hi']:+.{digits}f}]")


# ----------------------------------------------------------------------------------------------
# Sealed loaders


def _json_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        value = json.loads(raw).get("date")
    except (ValueError, AttributeError):
        return None
    return str(value).strip() if value else None


def load_serp_extra() -> pd.DataFrame:
    """Top-10 SERP rows dated <= 2026-11-17 with title, snippet and Serper's per-result date.

    Same source filters as rsdata.load_rows. Read-only connection. Asserts the maximum day.
    """
    con = sqlite3.connect(f"file:{rs.DB}?mode=ro", uri=True)
    try:
        frame = pd.read_sql_query(SEALED_SQL, con)
    finally:
        con.close()
    assert frame["day"].max() <= CUTOFF, "sealed SERP loader returned a day after 2026-11-17"
    frame = frame[~frame["source"].isin(rs._SKIP_SOURCES)]
    frame = frame[~frame["source"].str.startswith(rs._SKIP_PREFIXES)].copy()
    frame["position"] = frame["position"].astype(int)
    frame["kind"] = frame["source"].map(lambda value: "deep" if value.endswith(rs._DEEP_SUFFIX) else "top10")
    frame = frame[(frame["kind"] == "top10") & (frame["position"] <= 10)].copy()
    frame["key"] = frame["url"].map(rs.url_key)
    frame["site"] = frame["url"].map(rs.site_key)
    frame["serp_date"] = [_json_date(raw) for raw in frame["raw_json"]]
    frame["pull"] = frame["source"] + "|" + frame["fetched_at"]
    return frame.drop(columns="raw_json").reset_index(drop=True)


# ----------------------------------------------------------------------------------------------
# SERP title and date normalization

_ELLIPSIS = re.compile(r"(\s*(\.\.\.|…))+\s*$")
_REL = re.compile(r"^(\d+)\s+(minutes?|mins?|hours?|days?|weeks?|months?|years?)\s+ago$", re.I)


def title_norm(text: str | None) -> str:
    """Lowercase alphanumeric tokens (digits kept, so a year change counts), trailing ellipsis dropped."""
    text = text if isinstance(text, str) else ""
    return " ".join(re.findall(r"[a-z0-9]+", _ELLIPSIS.sub("", text.lower())))


# Segment separators in a displayed title: " | ", " - ", en/em dash, middle dot, " : " and ": ".
# A hyphen inside a word ("All-in-One") is not a separator.
_SEP = re.compile(r"\s+[|\-–—·:]\s+|:\s+|\s*\|\s*")


def _tok(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.lower()))


BRAND_TOKENS_MAX = 3  # a dropped segment of at most 3 tokens is treated as a brand or tag line


def title_parts(text: str | None) -> tuple[tuple[str, ...], bool, tuple]:
    """(full tokens, truncated?, variants) of a displayed title or a title tag.

    Variants are (tokens, dropped) pairs: the full title (dropped 0), the title without its last or
    first separator-delimited segment, and each single segment, where ``dropped`` is the number of
    tokens left out.
    """
    raw = text if isinstance(text, str) else ""
    truncated = bool(_ELLIPSIS.search(raw))
    clean = _ELLIPSIS.sub("", raw)
    full = _tok(clean)
    segments = [seg for seg in (_tok(part) for part in _SEP.split(clean)) if seg]
    variants = {full: 0}
    if len(segments) > 1:
        candidates = [tuple(t for seg in segments[:-1] for t in seg), tuple(t for seg in segments[1:] for t in seg)]
        candidates += segments
        for cand in candidates:
            dropped = len(full) - len(cand)
            if cand not in variants or dropped < variants[cand]:
                variants[cand] = dropped
    return full, truncated, tuple(sorted(variants.items()))


def same_core(a: tuple, b: tuple) -> bool:
    """True when two titles differ only by a brand segment, a segment reorder or truncation.

    Same when: the token sequences are equal; or they are the same multiset (segments reordered);
    or a variant of one equals (or, if either is truncated, is a prefix of) a variant of the other,
    each side dropping at most BRAND_TOKENS_MAX tokens, and the shared part has at least
    min(3, length of the shorter full title) tokens, so a bare brand segment never matches.
    """
    (full_a, trunc_a, var_a), (full_b, trunc_b, var_b) = a, b
    if full_a == full_b or sorted(full_a) == sorted(full_b):
        return True
    need = min(3, len(full_a), len(full_b))
    for x, drop_x in var_a:
        if drop_x > BRAND_TOKENS_MAX:
            continue
        for y, drop_y in var_b:
            if drop_y > BRAND_TOKENS_MAX:
                continue
            if x == y and len(x) >= need and len(x) > 0:
                return True
            if trunc_a or trunc_b:
                short, long = (x, y) if len(x) <= len(y) else (y, x)
                if len(short) >= max(3, need) and long[: len(short)] == short:
                    return True
    return False


def date_interval(text: str | None, day: str) -> tuple[int, int] | None:
    """Interval [lo, hi] of ordinal dates consistent with Serper's date string on SERP day ``day``.

    Relative dates get a generous interval so that plain ageing ("6 days ago" then "8 days ago" two
    days later) is never a change. Absolute dates ("Nov 7, 2026") are exact. None if unparseable.
    """
    if not isinstance(text, str) or not text:
        return None
    today = date.fromisoformat(day).toordinal()
    found = _REL.match(text.strip())
    if found:
        count, unit = int(found.group(1)), found.group(2).lower()
        if unit.startswith(("min", "hour")):
            return (today - 1, today + 1)
        if unit.startswith("day"):
            return (today - count - 1, today - count + 1)
        if unit.startswith("week"):
            return (today - 7 * count - 7, today - 7 * count + 3)
        if unit.startswith("month"):
            return (today - 31 * count - 31, today - 28 * count + 3)
        if unit.startswith("year"):
            return (today - 366 * count - 366, today - 365 * count + 31)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            ordinal = datetime.strptime(text.strip(), fmt).date().toordinal()
            return (ordinal, ordinal)
        except ValueError:
            continue
    return None


def disjoint(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    return a is not None and b is not None and (a[1] < b[0] or b[1] < a[0])


def query_term_count(text: str, query: str) -> int:
    """Query-term count in a title. Same tokens and terms as t2_changes.query_features."""
    terms = list(dict.fromkeys(norm(term) for term in salient_terms(query)))
    counts = Counter(norm_tokens(text))
    return sum(counts[term] for term in terms)


# ----------------------------------------------------------------------------------------------
# Q1, Q2, Q4 inputs: the round 1 change dataset


def load_change_rows(built: dict, cons_index: dict, query_days: dict) -> pd.DataFrame:
    data = built["data"]
    data = data[data["usable"]].copy()
    params = load_params()
    data["pred_position_rule"] = [predict_direction_position_only(row, params) for _, row in data.iterrows()]
    data["content_fires"] = [bool(row["material"]) and content_score(row, params) != 0 for _, row in data.iterrows()]
    data["moved"] = (data["pos_a"] != data["pos_b"]).astype(int)
    data["absd"] = (data["pos_a"] - data["pos_b"]).abs().astype(float)
    data["crossed"] = ((data["pos_a"] <= 10) != (data["pos_b"] <= 10)).astype(int)
    data["band_a"] = data["pos_a"].map(band)
    data["date_pair"] = data["serp_day_a"] + ">" + data["serp_day_b"]
    data["stratum"] = data["date_pair"] + "|" + data["band_a"]
    data["interior"] = data["pos_a"].between(2, 10)
    data["material"] = data["material"].astype(bool)
    data["anychange"] = (data["edit_tokens"] > 0) | data["title_changed"].astype(bool) | data["h1_changed"].astype(bool)
    data["dose_bin"] = pd.cut(data["edit_tokens"], [-1, 0, 49, 249, 10**9], labels=list(DOSE_BINS)).astype(str)
    data["log_edit"] = np.log1p(data["edit_tokens"].astype(float))
    data["pos_right"] = ((data["pred_position_rule"] == data["move"]) & (data["moved"] == 1)).astype(int)

    # Next pulled windows (Q4): B -> C and C -> D for the same (query, key).
    def position(query: str, day: str | None, key: str) -> int | None:
        if day is None:
            return None
        found = cons_index.get((query, day, key))
        return int(found.cons) if found is not None else ABSENT

    def next_day(query: str, day: str) -> str | None:
        later = [value for value in query_days[query] if value > day]
        return later[0] if later else None

    days_c, days_d, pos_c, pos_d = [], [], [], []
    for query, key, day_b in zip(data["query"], data["key"], data["serp_day_b"], strict=True):
        day_c = next_day(query, day_b)
        day_d = next_day(query, day_c) if day_c else None
        days_c.append(day_c)
        days_d.append(day_d)
        pos_c.append(position(query, day_c, key))
        pos_d.append(position(query, day_d, key))
    data["serp_day_c"], data["serp_day_d"] = days_c, days_d
    data["pos_c"], data["pos_d"] = pos_c, pos_d
    for label, start, end, start_day, end_day in (("next", "pos_b", "pos_c", "serp_day_b", "serp_day_c"),
                                                   ("next2", "pos_c", "pos_d", "serp_day_c", "serp_day_d")):
        has = data[end].notna()
        data[f"moved_{label}"] = np.where(has, (data[start] != data[end]).astype(float), np.nan)
        data[f"absd_{label}"] = np.where(has, (data[start] - data[end]).abs(), np.nan)
        data[f"crossed_{label}"] = np.where(has, ((data[start] <= 10) != (data[end] <= 10)).astype(float), np.nan)
        data[f"stratum_{label}"] = np.where(
            has, data[start_day].astype(str) + ">" + data[end_day].astype(str) + "|" + data[start].map(
                lambda value: band(int(value)) if pd.notna(value) else "na"), None)
    # Did the same page change materially again in the next fetch window?
    pairs = built["pairs"]
    material_starts = {(row.key, row.day_a) for row in pairs.itertuples(index=False) if row.usable and row.material}
    data["changed_again_next"] = [(key, day_b) in material_starts for key, day_b in zip(data["key"], data["day_b"], strict=True)]
    return data


def q1_excess(data: pd.DataFrame) -> dict:
    out: dict = {}
    # Reproduce the round 1 counts first.
    mat = data[data["material"]]
    out["round1_check"] = {
        "usable_rows": int(len(data)),
        "material_rows": int(len(mat)),
        "material_moved": int(mat["moved"].sum()),
        "material_up": int((mat["move"] == "up").sum()),
        "material_down": int((mat["move"] == "down").sum()),
        "material_moved_urls": int(mat[mat["moved"] == 1]["key"].nunique()),
        "material_moved_sites": int(mat[mat["moved"] == 1]["site"].nunique()),
        "position_rule_material_moved": mt.bootstrap_rate(mat[mat["moved"] == 1], "pos_right"),
        "position_rule_unchanged_moved": mt.bootstrap_rate(data[(~data["material"]) & (data["moved"] == 1)], "pos_right"),
    }
    # Raw (unstratified) rates.
    raw = {}
    for name, subset in (("material", data[data["material"]]), ("not_material", data[~data["material"]]),
                         ("anychange", data[data["anychange"]]), ("nochange", data[~data["anychange"]])):
        raw[name] = {outcome: (mt.bootstrap_rate(subset, outcome) if outcome != "absd" else
                               {"mean": float(subset[outcome].mean()), "n": int(len(subset))})
                     for outcome in ("moved", "absd", "crossed")}
    out["raw_rates"] = raw

    # Stratified: material vs not material.
    strat = {}
    for outcome in ("moved", "absd", "crossed"):
        joint = strat_effect(data, "material", outcome, "stratum", "query")
        by_url = strat_effect(data, "material", outcome, "stratum", "key")
        helper = mt.bootstrap_difference(matched_base(data, "material", outcome, "stratum"), outcome, "base")
        strat[outcome] = {"joint_query": joint, "joint_url": by_url, "helper_controls_fixed": helper}
    out["material_vs_not"] = strat

    # Same, against rows with no change at all (stricter control).
    strict = data[data["material"] | ~data["anychange"]]
    out["material_vs_nochange"] = {outcome: strat_effect(strict, "material", outcome, "stratum", "query")
                                   for outcome in ("moved", "absd", "crossed")}

    # Interior rows only (start 2..10), where an edit could move either way.
    inner = data[data["interior"]]
    out["material_vs_not_interior"] = {outcome: strat_effect(inner, "material", outcome, "stratum", "query")
                                       for outcome in ("moved", "absd", "crossed")}
    out["material_vs_not_interior_url_cluster"] = {outcome: strat_effect(inner, "material", outcome, "stratum", "key")
                                                   for outcome in ("moved", "absd", "crossed")}
    out["material_vs_not_interior_site_cluster"] = {"moved": strat_effect(inner, "material", "moved", "stratum", "site")}
    inner_mat = inner[inner["material"]]
    out["interior_material_summary"] = {
        "rows": int(len(inner_mat)), "moved": int(inner_mat["moved"].sum()),
        "up": int((inner_mat["move"] == "up").sum()), "down": int((inner_mat["move"] == "down").sum()),
        "urls": int(inner_mat["key"].nunique()), "sites": int(inner_mat["site"].nunique()),
        "client_rows": int((inner_mat["site"] == "smartcrewhq.com").sum()),
        "by_url_up_down": {key: f"{int((g['move'] == 'up').sum())} up, {int((g['move'] == 'down').sum())} down, "
                                f"{int((g['move'] == 'none').sum())} static" for key, g in inner_mat.groupby("key")},
    }
    out["material_summary"] = {
        "rows": int(mat.shape[0]), "urls": int(mat["key"].nunique()), "sites": int(mat["site"].nunique()),
        "url_pairs": int(mat.groupby(["key", "day_a"]).ngroups),
        "client_rows": int((mat["site"] == "smartcrewhq.com").sum()),
    }
    out["interior_material_rows"] = [
        {"query": r.query, "key": r.key, "window": r.date_pair, "pos_a": int(r.pos_a), "pos_b": int(r.pos_b),
         "reasons": r.material_reasons}
        for r in inner[inner["material"]].sort_values(["key", "date_pair"]).itertuples(index=False)]
    # Without the client's own site (deliberate edits during a sitewide trend), treated and control rows alike.
    client = "smartcrewhq.com"
    other = data[data["site"] != client]
    out["material_vs_not_without_client"] = {outcome: strat_effect(other, "material", outcome, "stratum", "query")
                                             for outcome in ("moved", "absd", "crossed")}
    other_inner = other[other["interior"]]
    out["material_vs_not_interior_without_client"] = {outcome: strat_effect(other_inner, "material", outcome, "stratum", "query")
                                                      for outcome in ("moved", "absd")}
    out["client_only_interior"] = {outcome: strat_effect(inner[inner["site"] == client], "material", outcome, "stratum", "query")
                                   for outcome in ("moved", "absd")}

    # Any change (dose).
    out["anychange_vs_none"] = {outcome: strat_effect(data, "anychange", outcome, "stratum", "query")
                                for outcome in ("moved", "absd", "crossed")}
    dose = {}
    for label in DOSE_BINS[1:]:
        sub = data[(data["dose_bin"] == label) | ~data["anychange"]].copy()
        sub["treated"] = sub["dose_bin"] == label
        dose[label] = {outcome: strat_effect(sub, "treated", outcome, "stratum", "query") for outcome in ("moved", "absd")}
    sub = data[(data["title_changed"].astype(bool) | data["h1_changed"].astype(bool)) | ~data["anychange"]].copy()
    sub["treated"] = sub["title_changed"].astype(bool) | sub["h1_changed"].astype(bool)
    dose["title_or_h1"] = {outcome: strat_effect(sub, "treated", outcome, "stratum", "query") for outcome in ("moved", "absd")}
    out["dose_bins_vs_none"] = dose
    out["dose_bin_counts"] = {label: {"rows": int((data["dose_bin"] == label).sum()),
                                      "moved": int(data[data["dose_bin"] == label]["moved"].sum()),
                                      "urls": int(data[data["dose_bin"] == label]["key"].nunique())}
                              for label in DOSE_BINS}
    out["dose_slope_log1p_edit_tokens"] = {outcome: strat_slope(data, "log_edit", outcome, "stratum", "query")
                                           for outcome in ("moved", "absd")}
    # Stratum table for transparency.
    table = (data.groupby(["date_pair", "band_a", "material"])
             .agg(rows=("moved", "size"), moved=("moved", "sum"), absd=("absd", "mean")).reset_index())
    out["stratum_table"] = table.to_dict(orient="records")
    return out


def q2_power(data: pd.DataFrame, q1: dict, built: dict) -> dict:
    mat_moved = data[data["material"] & (data["moved"] == 1)]
    interior = mat_moved[mat_moved["interior"]]
    unchanged_inner = data[(~data["material"]) & (data["moved"] == 1) & data["interior"]]
    a0_inner = mt.bootstrap_rate(unchanged_inner, "pos_right")
    a0_all = q1["round1_check"]["position_rule_unchanged_moved"]
    s = len(interior) / len(mat_moved)
    r = float(interior["content_fires"].mean()) if len(interior) else float("nan")
    pairs_with_move = mat_moved.groupby(["key", "day_a"]).size()
    deff = float(pairs_with_move.mean())
    # f is the share of interior changed-and-moved rows whose move the edit caused (boundary rows give D = 0
    # whatever f is there). Upper bound: the larger of the query-cluster and URL-cluster 97.5th percentiles.
    share_q = q1["material_vs_not_interior"]["moved"]
    share_u = q1["material_vs_not_interior_url_cluster"]["moved"]
    f_point = share_q["attributable_share"]
    f_hi = max(share_q["attributable_share_hi95"], share_u["attributable_share_hi95"])
    f_all_rows = q1["material_vs_not"]["moved"]["joint_query"]

    pairs = built["pairs"]
    use = pairs[pairs["usable"]]
    url_days = float(use["gap_days"].sum())
    urls = int(use["key"].nunique())
    per_url_day = len(mat_moved) / url_days
    weekly = per_url_day * urls * 7.0
    weekly_per_100 = per_url_day * 100 * 7.0

    def n_needed(mu: float, ed2: float) -> float:
        if not (mu > 0):
            return float("inf")
        var = ed2 - mu * mu
        return deff * (Z_ALPHA + Z_BETA) ** 2 * var / (mu * mu)

    a0i = a0_inner["rate"]
    scenarios = []
    f_grid = sorted({round(f_point, 4), round(f_hi, 4), 0.5, 1.0})
    for a_c_label, a_c in (("a_c=0.5 (edit direction independent of the position cue)", 0.5),
                           (f"a_c=a0 interior ({a0i:.3f}, position as good on caused rows)", a0i)):
        for f in f_grid:
            for q in (0.6, 0.7, 0.8):
                gated_mu = s * f * (q - a_c)
                gated_ed2 = s * f * (q * (1 - a_c) + (1 - q) * a_c)
                ungated_mu = s * r * (f * (q - a_c) + (1 - f) * (0.5 - a0i))
                ungated_ed2 = s * r * (f * (q * (1 - a_c) + (1 - q) * a_c) + (1 - f) * 0.5)
                n_g = n_needed(gated_mu, gated_ed2)
                n_u = n_needed(ungated_mu, ungated_ed2)
                scenarios.append({
                    "a_c": a_c_label, "a_c_value": a_c, "f": f, "q": q,
                    "gated_gain": gated_mu, "gated_rows": n_g, "gated_weeks": n_g / weekly,
                    "ungated_gain": ungated_mu, "ungated_rows": n_u, "ungated_weeks": n_u / weekly,
                    "break_even_f_ungated": (a0i - 0.5) / ((q - a_c) + (a0i - 0.5)) if (q - a_c) + (a0i - 0.5) > 0 else None,
                })
    return {
        "formula": ("n = DEFF * (z_0.975 + z_0.80)^2 * (E[D^2] - mu^2) / mu^2, D = 1[content-aware right] - "
                    "1[position-only right] on one changed-and-moved row. Gated (best case, content used only on "
                    "rows where the edit caused the move): mu = s*f*(q - a_c), E[D^2] = s*f*(q*(1-a_c) + (1-q)*a_c). "
                    "Ungated (content used wherever the content step fires, firing rate r independent of causation): "
                    "mu = s*r*(f*(q - a_c) + (1-f)*(0.5 - a0)), E[D^2] = s*r*(f*(q*(1-a_c)+(1-q)*a_c) + (1-f)*0.5). "
                    "Boundary rows (start 1 or 11) give D = 0 because both rules make the forced call."),
        "inputs": {
            "z_alpha_two_sided_95": Z_ALPHA, "z_power_80": Z_BETA,
            "changed_and_moved_rows": int(len(mat_moved)),
            "interior_changed_and_moved_rows": int(len(interior)),
            "s_interior_share": s,
            "r_content_step_fires_interior": r,
            "content_fires_rows": int(interior["content_fires"].sum()),
            "a0_position_rule_unchanged_moved_all": a0_all,
            "a0_position_rule_unchanged_moved_interior": a0_inner,
            "f_point_attributable_share_interior": f_point,
            "f_upper95_interior_max_of_query_and_url_cluster": f_hi,
            "f_upper95_interior_query_cluster": share_q["attributable_share_hi95"],
            "f_upper95_interior_url_cluster": share_u["attributable_share_hi95"],
            "f_all_rows_point_and_upper95": [f_all_rows["attributable_share"], f_all_rows["attributable_share_hi95"]],
            "deff_moved_rows_per_changed_url_pair": deff,
            "yield_url_days": url_days, "yield_urls": urls,
            "changed_and_moved_rows_per_url_day": per_url_day,
            "changed_and_moved_rows_per_week_current_set": weekly,
            "changed_and_moved_rows_per_week_per_100_urls": weekly_per_100,
            "cap_on_accuracy_gain_perfect_content_gated_f_upper": s * f_hi * (1 - 0.5),
            "cap_on_accuracy_gain_perfect_content_gated_f_point": s * f_point * (1 - 0.5),
        },
        "scenarios": scenarios,
    }


# ----------------------------------------------------------------------------------------------
# Q3: SERP-side signals


def obs_table(serp: pd.DataFrame) -> pd.DataFrame:
    """One observation per (query, day, pull, key): the best-positioned row."""
    obs = serp.sort_values(["query", "day", "pull", "position"]).drop_duplicates(["query", "day", "pull", "key"]).copy()
    obs["title_n"] = obs["title"].map(title_norm)
    obs["title"] = obs["title"].where(obs["title"].notna(), "")
    obs["title_p"] = obs["title"].map(title_parts)
    obs["snippet_n"] = obs["snippet"].map(title_norm)
    obs["date_iv"] = [date_interval(text, day) for text, day in zip(obs["serp_date"], obs["day"], strict=True)]
    obs["date_present"] = obs["serp_date"].notna()
    obs["date_parsed"] = obs["date_iv"].notna()
    return obs.reset_index(drop=True)


def _compare(a, b) -> dict:
    return {
        "title_exact_diff": int((a.title or "") != (b.title or "")),
        "title_norm_diff": int(a.title_n != b.title_n),
        "title_core_diff": int(not same_core(a.title_p, b.title_p)),
        "snippet_norm_diff": int(a.snippet_n != b.snippet_n),
        "date_any_present": int(a.date_present or b.date_present),
        "date_presence_diff": int(a.date_present != b.date_present),
        "date_both_parsed": int(a.date_parsed and b.date_parsed),
        "date_value_diff": int(disjoint(a.date_iv, b.date_iv)),
        "date_raw_diff_both_present": int(a.date_present and b.date_present and a.serp_date != b.serp_date),
    }


def q3_noise(obs: pd.DataFrame, pulled_days: dict) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    within, across = [], []
    for (query, key, day), group in obs.groupby(["query", "key", "day"], sort=True):
        if len(group) < 2:
            continue
        rows = list(group.sort_values("fetched_at").itertuples(index=False))
        for a, b in itertools.combinations(rows, 2):
            hours = abs((pd.Timestamp(b.fetched_at) - pd.Timestamp(a.fetched_at)).total_seconds()) / 3600.0
            record = {"query": query, "key": key, "day": day, "hours": hours, **_compare(a, b)}
            within.append(record)
    by_qk = {k: g.sort_values("fetched_at") for k, g in obs.groupby(["query", "key"], sort=True)}
    for (query, key), group in by_qk.items():
        days = pulled_days[query]
        shown = set(group["day"])
        for d1, d2 in zip(days, days[1:]):
            if d1 not in shown or d2 not in shown:
                continue
            a = list(group[group["day"] == d1].itertuples(index=False))[-1]
            b = list(group[group["day"] == d2].itertuples(index=False))[0]
            across.append({"query": query, "key": key, "day_pair": f"{d1}>{d2}", "gap_days": rs.calendar_gap(d1, d2),
                           **_compare(a, b)})
    within_f = pd.DataFrame.from_records(within)
    across_f = pd.DataFrame.from_records(across)
    within_f["gap_bucket"] = pd.cut(within_f["hours"], [-0.01, 1.5, 4.0, 48.0], labels=["<1.5h", "1.5-4h", ">4h"]).astype(str)

    def rates(frame: pd.DataFrame) -> dict:
        res = {"pairs": int(len(frame)), "queries": int(frame["query"].nunique())}
        res["title_exact_diff"] = mt.bootstrap_rate(frame, "title_exact_diff")
        res["title_norm_diff"] = mt.bootstrap_rate(frame, "title_norm_diff")
        res["title_core_diff"] = mt.bootstrap_rate(frame, "title_core_diff")
        res["snippet_norm_diff"] = mt.bootstrap_rate(frame, "snippet_norm_diff")
        present = frame[frame["date_any_present"] == 1]
        res["date_presence_diff_given_any"] = mt.bootstrap_rate(present, "date_presence_diff")
        parsed = frame[frame["date_both_parsed"] == 1]
        res["date_value_diff_given_both"] = mt.bootstrap_rate(parsed, "date_value_diff")
        res["date_raw_string_diff_given_both_present"] = mt.bootstrap_rate(
            frame[(frame["date_any_present"] == 1) & (frame["date_presence_diff"] == 0)], "date_raw_diff_both_present")
        return res

    out = {"within_day_all": rates(within_f)}
    out["within_day_by_day"] = {day: rates(group) for day, group in within_f.groupby("day")}
    out["within_day_by_gap"] = {gap: rates(group) for gap, group in within_f.groupby("gap_bucket")}
    out["across_days_all"] = rates(across_f)
    out["across_by_day_pair"] = {pair: rates(group) for pair, group in across_f.groupby("day_pair")}
    # Paired-in-time contrast for the 16 > 17 window, where both days have several pulls.
    return out, within_f, across_f


def day_summary(group: pd.DataFrame) -> dict:
    titles = Counter(group["title_n"])
    parts = list(group["title_p"])
    core_stable = all(same_core(a, b) for a, b in itertools.combinations(parts, 2))
    modal_norm = titles.most_common(1)[0][0]
    modal_parts = next(p for p, n in zip(group["title_p"], group["title_n"], strict=True) if n == modal_norm)
    present = group[group["date_present"]]
    ivs = [iv for iv in group["date_iv"] if iv is not None]
    date_stable = (group["date_present"].nunique() == 1) and all(
        not disjoint(a, b) for a, b in itertools.combinations(ivs, 2))
    lo = max((iv[0] for iv in ivs), default=None)
    hi = min((iv[1] for iv in ivs), default=None)
    return {
        "n_shown": int(len(group)),
        "title_modal": modal_norm,
        "title_modal_parts": modal_parts,
        "title_raw": group.sort_values("fetched_at")["title"].iloc[-1],
        "title_stable": len(titles) == 1,
        "title_core_stable": core_stable,
        "date_present": bool(len(present) > 0),
        "date_all_present": bool(len(present) == len(group)),
        "date_parsed": bool(len(ivs) > 0),
        "date_stable": bool(date_stable),
        "date_iv": (lo, hi) if ivs and lo is not None and hi is not None and lo <= hi else (tuple(ivs[0]) if ivs else None),
        "date_text": present.sort_values("fetched_at")["serp_date"].iloc[-1] if len(present) else None,
    }


def q3_events(obs: pd.DataFrame, pulled_days: dict, cons_index: dict, first_day: dict) -> pd.DataFrame:
    """Units = (query, key, d1 -> d2) consecutive pulled days of the query, key shown on both days."""
    summaries = {k: day_summary(g) for k, g in obs.groupby(["query", "key", "day"], sort=True)}
    # Every distinct displayed title of a URL, by day, over all queries (for the "seen before" flag).
    key_titles: dict = defaultdict(list)
    for key, day, parts in obs.drop_duplicates(["key", "day", "title_n"])[["key", "day", "title_p"]].itertuples(index=False):
        key_titles[key].append((day, parts))
    shown_by_qk: dict = defaultdict(set)
    for query, key, day in summaries:
        shown_by_qk[(query, key)].add(day)
    params = load_params()
    records = []
    for (query, key), shown in sorted(shown_by_qk.items()):
        days = pulled_days[query]
        for index in range(len(days) - 1):
            d1, d2 = days[index], days[index + 1]
            if d1 not in shown or d2 not in shown:
                continue
            d3 = days[index + 2] if index + 2 < len(days) else None
            s1, s2 = summaries[(query, key, d1)], summaries[(query, key, d2)]
            n_pulls_1 = len(obs[(obs["query"] == query) & (obs["day"] == d1)]["pull"].unique())
            n_pulls_2 = len(obs[(obs["query"] == query) & (obs["day"] == d2)]["pull"].unique())
            # Primary: a substantive title change (not a brand-suffix toggle, segment choice or truncation),
            # with the substantive title stable across every pull of each day.
            title_unstable = not (s1["title_core_stable"] and s2["title_core_stable"])
            title_event = (not title_unstable) and not same_core(s1["title_modal_parts"], s2["title_modal_parts"])
            display_unstable = not (s1["title_stable"] and s2["title_stable"])
            display_event = (not display_unstable) and s1["title_modal"] != s2["title_modal"]
            date_value_event = (s1["date_stable"] and s2["date_stable"] and s1["date_all_present"] and s2["date_all_present"]
                                and disjoint(s1["date_iv"], s2["date_iv"]))
            date_presence_event = (s1["date_stable"] and s2["date_stable"] and s1["date_present"] != s2["date_present"])
            date_newer = bool(date_value_event and s2["date_iv"][0] > s1["date_iv"][1])
            # A title the URL already showed earlier (on any query) points to per-query rewriting or
            # alternation between title sources, not to a fresh crawl.
            seen_before = bool(title_event and any(day < d2 and same_core(s2["title_modal_parts"], parts)
                                                   for day, parts in key_titles[key]))

            def pos(day):
                if day is None:
                    return None
                found = cons_index.get((query, day, key))
                return int(found.cons) if found is not None else ABSENT

            p1, p2, p3 = pos(d1), pos(d2), pos(d3)
            # History up to d1 and up to d2 for the position-only rule (same definition as t2_dataset._history).
            def hist(upto_day):
                # First consensus appearance, but never later than the window start (a key can be shown
                # only in a non-voting pull of that day, in which case its consensus position is 11).
                first = min(first_day.get((query, key), upto_day), upto_day)
                series = [pos(day) for day in days if first <= day <= upto_day]
                before = series[:-1]
                return {"pos_a": series[-1], "has_prev": bool(before),
                        "hist_mean_before": float(np.mean(before)) if before else np.nan}
            pred_same = predict_direction_position_only(hist(d1), params)
            pred_next = predict_direction_position_only(hist(d2), params) if d3 else None
            terms_1 = query_term_count(s1["title_raw"], query)
            terms_2 = query_term_count(s2["title_raw"], query)
            records.append({
                "query": query, "key": key, "site": rs.site_key(key), "d1": d1, "d2": d2, "d3": d3,
                "day_pair": f"{d1}>{d2}", "gap_days": rs.calendar_gap(d1, d2),
                "pulls_d1": n_pulls_1, "pulls_d2": n_pulls_2, "shown_d1": s1["n_shown"], "shown_d2": s2["n_shown"],
                "verified": s1["n_shown"] >= 2 and s2["n_shown"] >= 2,
                "title_unstable": title_unstable, "title_event": title_event,
                "display_unstable": display_unstable, "display_event": display_event,
                "date_value_event": bool(date_value_event), "date_presence_event": bool(date_presence_event),
                "date_newer": date_newer, "date_older": bool(date_value_event and not date_newer),
                "reindex_event": bool(title_event or date_value_event),
                "title_seen_before": seen_before,
                "reindex_event_strict": bool((title_event and not seen_before) or date_value_event),
                "title_d1": s1["title_raw"], "title_d2": s2["title_raw"], "date_d1": s1["date_text"], "date_d2": s2["date_text"],
                "terms_d1": terms_1, "terms_d2": terms_2, "terms_delta": terms_2 - terms_1,
                "pos_d1": p1, "pos_d2": p2, "pos_d3": p3,
                "pred_pos_same": pred_same, "pred_pos_next": pred_next,
            })
    frame = pd.DataFrame.from_records(records)
    frame["moved_same"] = (frame["pos_d1"] != frame["pos_d2"]).astype(int)
    frame["absd_same"] = (frame["pos_d1"] - frame["pos_d2"]).abs().astype(float)
    frame["crossed_same"] = ((frame["pos_d1"] <= 10) != (frame["pos_d2"] <= 10)).astype(int)
    frame["move_same"] = np.where(frame["pos_d2"] < frame["pos_d1"], "up", np.where(frame["pos_d2"] > frame["pos_d1"], "down", "none"))
    has3 = frame["pos_d3"].notna()
    frame["moved_next"] = np.where(has3, (frame["pos_d2"] != frame["pos_d3"]).astype(float), np.nan)
    frame["absd_next"] = np.where(has3, (frame["pos_d2"] - frame["pos_d3"]).abs(), np.nan)
    frame["crossed_next"] = np.where(has3, ((frame["pos_d2"] <= 10) != (frame["pos_d3"] <= 10)).astype(float), np.nan)
    frame["move_next"] = np.where(~has3, "na", np.where(frame["pos_d3"] < frame["pos_d2"], "up",
                                                       np.where(frame["pos_d3"] > frame["pos_d2"], "down", "none")))
    frame["stratum_same"] = frame["day_pair"] + "|" + frame["pos_d1"].map(band)
    frame["stratum_next"] = np.where(has3, frame["d2"] + ">" + frame["d3"].astype(str) + "|" +
                                     frame["pos_d2"].map(band), None)
    return frame


def q3_event_counts(units: pd.DataFrame, within_rate: float, within_rate_display: float) -> dict:
    out = {}
    for pair, group in units.groupby("day_pair", sort=True):
        stable = group[~group["title_unstable"]]
        # Single-pull days cannot show within-day instability, so their noise passes as events.
        unverified = stable[~stable["verified"]]
        ev = group[group["reindex_event"]]
        title_ev = group[group["title_event"]]
        date_ev = group[group["date_value_event"]]
        url_ev = ev.groupby("key").agg(queries=("query", "nunique"), title=("title_event", "sum"),
                                       date=("date_value_event", "sum"))
        confirmed = url_ev[(url_ev["title"] >= 2) | (url_ev["date"] >= 1)]
        out[pair] = {
            "gap_days": int(group["gap_days"].iloc[0]),
            "units_shown_both_days": int(len(group)),
            "units_title_stable_both_days": int(len(stable)),
            "units_verified_multi_pull_both_days": int(group["verified"].sum()),
            "title_unstable_within_day": int(group["title_unstable"].sum()),
            "display_title_events": int(group["display_event"].sum()),
            "title_events": int(len(title_ev)),
            "date_value_events": int(len(date_ev)),
            "date_presence_events": int(group["date_presence_event"].sum()),
            "reindex_events": int(len(ev)),
            "reindex_events_verified": int(ev["verified"].sum()),
            "event_urls": int(ev["key"].nunique()),
            "event_sites": int(ev["site"].nunique()),
            "event_queries": int(ev["query"].nunique()),
            "confirmed_urls_title_on_2plus_queries_or_date": int(len(confirmed)),
            "confirmed_sites": int(len({rs.site_key(key) for key in confirmed.index})),
            "events_per_calendar_day": len(ev) / max(1, int(group["gap_days"].iloc[0])),
            "unverified_stable_units": int(len(unverified)),
            "expected_title_events_from_within_day_noise": within_rate * len(unverified),
            "expected_display_events_from_within_day_noise": within_rate_display * int((~group["display_unstable"] & ~group["verified"]).sum()),
            "title_events_seen_before": int(title_ev["title_seen_before"].sum()),
            "strict_events": int(group["reindex_event_strict"].sum()),
            "strict_event_urls": int(group[group["reindex_event_strict"]]["key"].nunique()),
            "strict_event_sites": int(group[group["reindex_event_strict"]]["site"].nunique()),
            "event_examples": [f"{r.key}: '{r.title_d1}' -> '{r.title_d2}'{' (title shown before)' if r.title_seen_before else ''}"
                               for r in title_ev.drop_duplicates("key").head(10).itertuples()],
        }
    return out


def q3_after_event(units: pd.DataFrame) -> dict:
    base = units[~units["title_unstable"]].copy()
    out = {"units": int(len(base)), "events": int(base["reindex_event"].sum())}
    for window in ("same", "next"):
        frame = base.dropna(subset=[f"moved_{window}"]) if window == "next" else base
        res = {"rows": int(len(frame)), "event_rows": int(frame["reindex_event"].sum())}
        for treat in ("reindex_event", "reindex_event_strict", "title_event", "date_value_event"):
            res[treat] = {outcome: strat_effect(frame, treat, f"{outcome}_{window}", f"stratum_{window}", "query")
                          for outcome in ("moved", "absd", "crossed")}
        res["reindex_event"]["moved_url_cluster"] = strat_effect(frame, "reindex_event", f"moved_{window}", f"stratum_{window}", "key")
        disp = units[~units["display_unstable"]]
        disp = disp.dropna(subset=[f"moved_{window}"]) if window == "next" else disp
        res["display_event"] = {outcome: strat_effect(disp, "display_event", f"{outcome}_{window}", f"stratum_{window}", "query")
                                for outcome in ("moved", "absd", "crossed")}
        res["raw"] = {
            "event": {"rows": int(frame["reindex_event"].sum()),
                      "moved": int(frame[frame["reindex_event"]][f"moved_{window}"].sum())},
            "no_event": {"rows": int((~frame["reindex_event"]).sum()),
                         "moved": int(frame[~frame["reindex_event"]][f"moved_{window}"].sum())},
        }
        out[window] = res
    # Direction: does the move follow the change in the number of query terms in the SERP title?
    direction = {}
    for window in ("same", "next"):
        moved = base[(base["title_event"]) & (base[f"move_{window}"].isin(["up", "down"]))].copy()
        with_delta = moved[moved["terms_delta"] != 0].copy()
        with_delta["call"] = np.where(with_delta["terms_delta"] > 0, "up", "down")
        with_delta["hit"] = (with_delta["call"] == with_delta[f"move_{window}"]).astype(int)
        pred_col = "pred_pos_same" if window == "same" else "pred_pos_next"
        with_delta["pos_hit"] = (with_delta[pred_col] == with_delta[f"move_{window}"]).astype(int)
        direction[window] = {
            "title_event_rows_moved": int(len(moved)),
            "with_term_count_change": int(len(with_delta)),
            "terms_up": int((with_delta["terms_delta"] > 0).sum()),
            "terms_down": int((with_delta["terms_delta"] < 0).sum()),
            "term_rule": mt.bootstrap_rate(with_delta, "hit"),
            "position_rule_same_rows": mt.bootstrap_rate(with_delta, "pos_hit"),
            "term_minus_position": mt.bootstrap_difference(with_delta, "hit", "pos_hit"),
        }
        dated = base[base["date_value_event"] & base[f"move_{window}"].isin(["up", "down"])].copy()
        dated["call"] = np.where(dated["date_newer"], "up", "down")
        dated["hit"] = (dated["call"] == dated[f"move_{window}"]).astype(int)
        direction[window]["date_rule_newer_up"] = mt.bootstrap_rate(dated, "hit")
        direction[window]["date_events_moved"] = int(len(dated))
        direction[window]["date_newer_moved"] = int(dated["date_newer"].sum())
    out["direction"] = direction
    return out


def q3_title_tags(built: dict, obs: pd.DataFrame, units: pd.DataFrame, cons_index: dict, pulled_days: dict) -> tuple[list, pd.DataFrame]:
    """URL pairs where our own fetches show the title tag changed. Did the SERP title follow, and when?"""
    pairs = built["pairs"]
    changed = pairs[pairs["usable"] & pairs["title_changed"].astype(bool)].copy()
    pages = extracted(list(zip(changed["id_a"].astype(int), changed["url"])) + list(zip(changed["id_b"].astype(int), changed["url"])))
    records, timeline = [], []
    for row in changed.sort_values(["key", "day_a"]).itertuples(index=False):
        old_title, new_title = pages[int(row.id_a)]["title"], pages[int(row.id_b)]["title"]
        old_p, new_p = title_parts(old_title), title_parts(new_title)
        sub = obs[obs["key"] == row.key]

        def label(serp_parts: tuple) -> str:
            is_old, is_new = same_core(serp_parts, old_p), same_core(serp_parts, new_p)
            if is_old and is_new:
                return "both"
            if is_new:
                return "new"
            if is_old:
                return "old"
            return "neither"

        per_query = []
        day_labels_all: dict[str, set] = defaultdict(set)
        for query, group in sub.groupby("query", sort=True):
            day_labels = {}
            for day, dgroup in group.groupby("day", sort=True):
                labels = {label(parts) for parts in dgroup["title_p"]}
                day_labels[day] = labels.pop() if len(labels) == 1 else "mixed"
                day_labels_all[day].add(day_labels[day])
                timeline.append({"key": row.key, "change": f"{row.day_a}>{row.day_b}", "old_tag": old_title, "new_tag": new_title,
                                 "query": query, "day": day, "serp_label": day_labels[day],
                                 "serp_title": dgroup.sort_values("fetched_at")["title"].iloc[-1],
                                 "pos": int(cons_index[(query, day, row.key)].cons) if (query, day, row.key) in cons_index else ABSENT})
            seq = "".join({"new": "N", "old": "O", "both": "B", "neither": "x", "mixed": "m"}[day_labels[day]] for day in sorted(day_labels))
            per_query.append({"query": query, "days": sorted(day_labels), "labels": seq,
                              "first_new": next((d for d, lab in sorted(day_labels.items()) if lab == "new"), None)})
        first_new_any = min((item["first_new"] for item in per_query if item["first_new"]), default=None)
        last_serp_day = max(day_labels_all) if day_labels_all else None
        ev = units[(units["key"] == row.key) & units["title_event"]]
        ev_after = ev[ev["d2"] > row.day_a]
        # Overall verdict across queries and days, read from the labels.
        seen = set().union(*day_labels_all.values()) if day_labels_all else set()
        if first_new_any:
            verdict = "SERP took the new tag"
        elif seen <= {"both"}:
            verdict = "SERP title consistent with both tags"
        elif "old" in seen and not any(lab == "old" for d, labs in day_labels_all.items() if d >= row.day_b for lab in labs):
            verdict = "SERP left the old tag, new tag not shown"
        elif "old" in seen:
            verdict = "SERP still shows the old tag"
        else:
            verdict = "SERP shows neither tag"
        records.append({
            "key": row.key, "site": row.site, "fetch_old_day": row.day_a, "fetch_new_day": row.day_b,
            "old_title": old_title, "new_title": new_title,
            "serp_queries": len(per_query),
            "serp_days_after_new_fetch": sorted(d for d in day_labels_all if d >= row.day_b),
            "last_serp_day": last_serp_day,
            "queries_with_new_serp_title": sum(1 for item in per_query if item["first_new"]),
            "first_serp_new_day": first_new_any,
            "lag_days_vs_fetch_new": rs.calendar_gap(row.day_b, first_new_any) if first_new_any else None,
            "serp_title_events_any_time": sorted(set(ev["day_pair"])),
            "serp_title_events_after_old_fetch": sorted(set(ev_after["day_pair"])),
            "verdict": verdict,
            "labels_by_day": {d: sorted(labs) for d, labs in sorted(day_labels_all.items())},
            "per_query": per_query,
        })
    return records, pd.DataFrame.from_records(timeline)


# ----------------------------------------------------------------------------------------------
# Q4: lag


def q4_lag(data: pd.DataFrame, after: dict) -> dict:
    out = {}
    for label in ("next", "next2"):
        frame = data.dropna(subset=[f"moved_{label}"])
        res = {"rows": int(len(frame)), "material_rows": int(frame["material"].sum())}
        for outcome in ("moved", "absd", "crossed"):
            res[outcome] = strat_effect(frame, "material", f"{outcome}_{label}", f"stratum_{label}", "query")
        clean = frame[~frame["changed_again_next"]] if label == "next" else frame
        res["moved_excluding_pages_changed_again"] = strat_effect(clean, "material", f"moved_{label}", f"stratum_{label}", "query")
        res["anychange_moved"] = strat_effect(frame, "anychange", f"moved_{label}", f"stratum_{label}", "query")
        res["absd_url_cluster"] = strat_effect(frame, "material", f"absd_{label}", f"stratum_{label}", "key")
        res["moved_url_cluster"] = strat_effect(frame, "material", f"moved_{label}", f"stratum_{label}", "key")
        other = frame[frame["site"] != "smartcrewhq.com"]
        res["without_client"] = {outcome: strat_effect(other, "material", f"{outcome}_{label}", f"stratum_{label}", "query")
                                 for outcome in ("moved", "absd")}
        client = frame[frame["site"] == "smartcrewhq.com"]
        res["client_only"] = {outcome: strat_effect(client, "material", f"{outcome}_{label}", f"stratum_{label}", "query")
                              for outcome in ("moved", "absd")}
        mat = frame[frame["material"]]
        res["material_detail"] = [
            {"query": r.query, "key": r.key, "fetch_window": f"{r.serp_day_a}>{r.serp_day_b}", "pos_a": int(r.pos_a),
             "pos_b": int(r.pos_b), "pos_c": int(r.pos_c), "pos_d": None if pd.isna(r.pos_d) else int(r.pos_d)}
            for r in mat.sort_values(["key", "serp_day_a"]).itertuples(index=False)]
        out[label] = res
    mat = data[data["material"]].copy()
    mat["window_pattern"] = [
        f"{'M' if same else '-'}{'M' if nxt == 1 else ('-' if nxt == 0 else '?')}{'M' if nxt2 == 1 else ('-' if nxt2 == 0 else '?')}"
        for same, nxt, nxt2 in zip(mat["moved"], mat["moved_next"], mat["moved_next2"], strict=True)
    ]
    out["material_row_patterns_same_next_next2"] = mat["window_pattern"].value_counts().to_dict()
    out["material_rows_by_window"] = {
        "same_moved": int(mat["moved"].sum()), "same_rows": int(len(mat)),
        "next_moved": int(np.nansum(mat["moved_next"])), "next_rows": int(mat["moved_next"].notna().sum()),
        "next2_moved": int(np.nansum(mat["moved_next2"])), "next2_rows": int(mat["moved_next2"].notna().sum()),
        "still_in_place_same_then_moved_next": int(((mat["moved"] == 0) & (mat["moved_next"] == 1)).sum()),
        "rows_static_same_window": int((mat["moved"] == 0).sum()),
        "static_same_window_with_next_day": int(((mat["moved"] == 0) & mat["moved_next"].notna()).sum()),
    }
    out["serp_event_same_vs_next"] = {
        "same_moved": after["same"]["reindex_event"]["moved"],
        "next_moved": after["next"]["reindex_event"]["moved"],
    }
    return out


# ----------------------------------------------------------------------------------------------


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = rs.load_rows(CUTOFF)
    assert rows["day"].max() <= CUTOFF, "SERP rows after the cutoff"
    serp = load_serp_extra()
    top = rows[rows["kind"] == "top10"]
    mine = Counter(zip(serp["query"], serp["day"], serp["fetched_at"], serp["position"], serp["url"], strict=True))
    theirs = Counter(zip(top["query"], top["day"], top["fetched_at"], top["position"], top["url"], strict=True))
    assert mine == theirs, "local SERP loader disagrees with rsdata.load_rows"
    cons = rs.consensus_table(rows)
    pulled_days = rs.query_days(cons)
    cons_index = {(r.query, r.day, r.key): r for r in cons.itertuples(index=False)}
    first_day = cons.groupby(["query", "key"])["day"].min().to_dict()

    print("building round 1 change dataset (sealed at 2026-11-17)", flush=True)
    built = td.build(CUTOFF)
    picked = built["picked"]
    page_index = rs.load_page_index(CUTOFF)
    assert picked["day"].max() <= CUTOFF, "page fetch ET day after the cutoff"
    assert page_index["fetched_at"].max() < "2026-11-18T05:00:00", "page fetch after the cutoff"
    seal = {
        "cutoff": CUTOFF,
        "max_serp_day_rsdata": rows["day"].max(),
        "max_serp_day_local_loader": serp["day"].max(),
        "max_page_fetch_et_day_used": picked["day"].max(),
        "max_page_fetched_at_utc_indexed": page_index["fetched_at"].max(),
        "local_loader_rows_match_rsdata_top10": True,
        "serp_top10_rows": int(len(serp)),
    }
    # t2_common.extracted loads its whole /tmp cache. Confirm (by page id only) that it holds nothing
    # outside the sealed page index, and that every page this study uses is inside it.
    from scripts.claude.t2_common import EXTRACT_CACHE
    import pickle
    sealed_ids = set(page_index["id"].astype(int))
    cache_ids = set(pickle.loads(EXTRACT_CACHE.read_bytes()).keys()) if EXTRACT_CACHE.is_file() else set()
    used_ids = set(picked["id"].astype(int))
    assert used_ids <= sealed_ids, "a page outside the sealed index was used"
    seal["extract_cache_ids"] = len(cache_ids)
    seal["extract_cache_ids_outside_sealed_index"] = len(cache_ids - sealed_ids)
    seal["pages_used_outside_sealed_index"] = len(used_ids - sealed_ids)
    print(json.dumps(seal, indent=2))

    data = load_change_rows(built, cons_index, pulled_days)
    q1 = q1_excess(data)
    q2 = q2_power(data, q1, built)

    obs = obs_table(serp)
    noise, within_f, across_f = q3_noise(obs, pulled_days)
    units = q3_events(obs, pulled_days, cons_index, first_day)
    counts = q3_event_counts(units, noise["within_day_all"]["title_core_diff"]["rate"],
                             noise["within_day_all"]["title_norm_diff"]["rate"])
    after = q3_after_event(units)
    tags, timeline = q3_title_tags(built, obs, units, cons_index, pulled_days)
    # Do material fetch changes coincide with a SERP re-index event in the same window?
    ev_index = {(r.query, r.key, r.d1, r.d2): r for r in units.itertuples(index=False)}
    mat_rows = data[data["material"]]
    coincide = Counter()
    for row in mat_rows.itertuples(index=False):
        unit = ev_index.get((row.query, row.key, row.serp_day_a, row.serp_day_b))
        state = "not shown both days" if unit is None else ("event" if unit.reindex_event else ("title unstable" if unit.title_unstable else "no event"))
        coincide[(state, "moved" if row.moved else "static")] += 1
    q3 = {
        "noise": noise,
        "obs": {"observations": int(len(obs)), "date_present": int(obs["date_present"].sum()),
                "date_parsed": int(obs["date_parsed"].sum()),
                "date_unparsed_values": int((obs["date_present"] & ~obs["date_parsed"]).sum()),
                "pulled_days_by_query_group": {g: sorted({d for q, ds in pulled_days.items() if rs.query_groups(rows).get(q) == g for d in ds})
                                               for g in ("o30", "a70", "p35", "rev", "other")}},
        "event_counts": counts,
        "event_totals": {
            "units": int(len(units)), "title_stable_units": int((~units["title_unstable"]).sum()),
            "reindex_events": int(units["reindex_event"].sum()),
            "title_events": int(units["title_event"].sum()), "date_value_events": int(units["date_value_event"].sum()),
            "display_title_events": int(units["display_event"].sum()),
            "verified_units": int(units["verified"].sum()),
            "reindex_events_verified": int(units[units["reindex_event"]]["verified"].sum()),
            "event_urls": int(units[units["reindex_event"]]["key"].nunique()),
            "event_sites": int(units[units["reindex_event"]]["site"].nunique()),
            "strict_events": int(units["reindex_event_strict"].sum()),
            "strict_event_urls": int(units[units["reindex_event_strict"]]["key"].nunique()),
            "strict_event_sites": int(units[units["reindex_event_strict"]]["site"].nunique()),
            "title_events_seen_before": int(units["title_seen_before"].sum()),
        },
        "after_event": after,
        "title_tag_changes": tags,
        "material_rows_vs_serp_event": {f"{a}|{b}": v for (a, b), v in sorted(coincide.items())},
    }
    q4 = q4_lag(data, after)

    result = {"label": "MEASURED (no predictor). Real Google positions from Serper.", "seal": seal,
              "q1_excess_movement": q1, "q2_test_size": q2, "q3_serp_signals": q3, "q4_lag": q4}
    (OUT / "results.json").write_text(json.dumps(_jsonable(result), indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    units.to_csv(OUT / "serp_units.csv", index=False)
    timeline.to_csv(OUT / "title_tag_timeline.csv", index=False)
    keep = ["query", "key", "site", "serp_day_a", "serp_day_b", "serp_day_c", "pos_a", "pos_b", "pos_c", "pos_d", "move",
            "material", "anychange", "edit_tokens", "title_changed", "h1_changed", "dose_bin", "band_a", "moved",
            "moved_next", "moved_next2", "pred_position_rule", "content_fires", "changed_again_next"]
    data[keep].to_csv(OUT / "change_rows.csv", index=False)
    write_tables(result)
    print_summary(result)
    return 0


def _cell(text) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def write_tables(result: dict) -> None:
    q1, q2, q3, q4 = (result[k] for k in ("q1_excess_movement", "q2_test_size", "q3_serp_signals", "q4_lag"))
    lines = ["# Generated tables (python -m scripts.claude.r2_t2_signal). Do not edit by hand.", ""]
    lines += ["## Q1 stratified (date pair x start band), material vs not material", "",
              "| outcome | changed rows matched | changed | matched unchanged | diff [95% query CI] | URL-cluster CI | controls-fixed CI |",
              "| --- | ---: | ---: | ---: | --- | --- | --- |"]
    for outcome in ("moved", "absd", "crossed"):
        res = q1["material_vs_not"][outcome]
        j, u, h = res["joint_query"], res["joint_url"], res["helper_controls_fixed"]
        lines.append(f"| {outcome} | {j['treated_rows_matched']} of {j['treated_rows']} | {j['treated_mean']:.3f} | "
                     f"{j['matched_control_mean']:.3f} | {j['diff']:+.3f} [{j['lo']:+.3f}, {j['hi']:+.3f}] | "
                     f"[{u['lo']:+.3f}, {u['hi']:+.3f}] | [{h['lo']:+.3f}, {h['hi']:+.3f}] |")
    for name in ("material_vs_nochange", "material_vs_not_interior", "material_vs_not_interior_url_cluster",
                 "material_vs_not_interior_site_cluster", "material_vs_not_without_client",
                 "material_vs_not_interior_without_client", "client_only_interior", "anychange_vs_none"):
        lines += ["", f"## Q1 {name}", "", "| outcome | treated matched | treated | matched control | diff [95% CI] | share caused, point / upper 95 |",
                  "| --- | ---: | ---: | ---: | --- | --- |"]
        for outcome, j in q1[name].items():
            if j.get("treated_mean") is None:
                lines.append(f"| {outcome} | 0 of {j['treated_rows']} | n/a | n/a | n/a | n/a |")
                continue
            lines.append(f"| {outcome} | {j['treated_rows_matched']} of {j['treated_rows']} | {j['treated_mean']:.3f} | "
                         f"{j['matched_control_mean']:.3f} | {j['diff']:+.3f} [{j['lo']:+.3f}, {j['hi']:+.3f}] | "
                         f"{j['attributable_share']:.3f} / {j['attributable_share_hi95']:.3f} |")
    lines += ["", "Interior material rows: " + "; ".join(
        f"{r['key'].split('//')[-1]} [{r['query']}] {r['window']} {r['pos_a']}->{r['pos_b']}" for r in q1["interior_material_rows"])]
    lines += ["", "## Q1 dose bins (edit tokens) vs no change", "",
              "| bin | rows | moved | P(moved) changed vs matched none, diff [CI] | mean abs change, diff [CI] |",
              "| --- | ---: | ---: | --- | --- |"]
    for label, res in q1["dose_bins_vs_none"].items():
        c = q1["dose_bin_counts"].get(label, {"moved": "n/a"})
        lines.append(f"| {label} | {res['moved']['treated_rows']} | {c['moved']} | {fmt_effect(res['moved'])} | {fmt_effect(res['absd'], 2)} |")
    lines.append(f"\nNo-change rows: {q1['dose_bin_counts']['none']}")
    for outcome, res in q1["dose_slope_log1p_edit_tokens"].items():
        lines.append(f"\nSlope of {outcome} on log(1 + edit tokens), within strata: {res['slope']:+.4f} [{res['lo']:+.4f}, {res['hi']:+.4f}] ({res['rows']} rows)")
    lines += ["", "## Q2 inputs", "", "```", json.dumps(_jsonable(q2["inputs"]), indent=1), "```", "",
              "| a_c | f | q | gated gain | gated rows | gated weeks | ungated gain | ungated rows | ungated weeks | ungated break-even f |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]

    def num(value):
        return "never" if value is None or not math.isfinite(value) or value > 1e6 else f"{value:,.0f}"

    def wk(value):
        return "never" if value is None or not math.isfinite(value) or value > 1e5 else f"{value:,.1f}"

    for sc in q2["scenarios"]:
        be = sc["break_even_f_ungated"]
        lines.append(f"| {sc['a_c_value']:.3f} | {sc['f']:.3f} | {sc['q']:.1f} | {sc['gated_gain']:+.4f} | {num(sc['gated_rows'])} | "
                     f"{wk(sc['gated_weeks'])} | {sc['ungated_gain']:+.4f} | {num(sc['ungated_rows'])} | {wk(sc['ungated_weeks'])} | "
                     f"{'n/a' if be is None else f'{be:.3f}'} |")
    noise = q3["noise"]
    lines += ["", "## Q3a/b noise floor: pull pairs", "",
              "| comparison | pull pairs | title differs (exact) | title differs (substantive) | snippet differs | date shown in one only | date value differs (both parsed) |",
              "| --- | ---: | --- | --- | --- | --- | --- |"]

    def noise_line(name, res):
        return (f"| {name} | {res['pairs']} | {mt.fmt_rate(res['title_exact_diff'])} | {mt.fmt_rate(res['title_core_diff'])} | "
                f"{mt.fmt_rate(res['snippet_norm_diff'])} | {mt.fmt_rate(res['date_presence_diff_given_any'])} | "
                f"{mt.fmt_rate(res['date_value_diff_given_both'])} |")

    lines.append(noise_line("same day, all", noise["within_day_all"]))
    for day, res in noise["within_day_by_day"].items():
        lines.append(noise_line(f"same day {day}", res))
    for gap, res in noise["within_day_by_gap"].items():
        lines.append(noise_line(f"same day, gap {gap}", res))
    lines.append(noise_line("consecutive pulled days, all", noise["across_days_all"]))
    for pair, res in noise["across_by_day_pair"].items():
        lines.append(noise_line(pair, res))
    lines += ["", "## Q3d re-index events per day pair (substantive title change or date value change, stable within each day)", "",
              "| day pair | gap days | units shown both days | multi-pull both days | display title changes | substantive title events | date value events | date shown/hidden | re-index events | per calendar day | URLs | sites | URLs confirmed | expected from same-day noise | title shown before | strict events (URLs, sites) |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for pair, c in q3["event_counts"].items():
        lines.append(f"| {pair} | {c['gap_days']} | {c['units_shown_both_days']} | {c['units_verified_multi_pull_both_days']} | "
                     f"{c['display_title_events']} | {c['title_events']} | {c['date_value_events']} | "
                     f"{c['date_presence_events']} | {c['reindex_events']} | {c['events_per_calendar_day']:.1f} | {c['event_urls']} | "
                     f"{c['event_sites']} | {c['confirmed_urls_title_on_2plus_queries_or_date']} | "
                     f"{c['expected_title_events_from_within_day_noise']:.1f} | {c['title_events_seen_before']} | "
                     f"{c['strict_events']} ({c['strict_event_urls']}, {c['strict_event_sites']}) |")
    lines += ["", "Examples of substantive title events:"]
    for pair, c in q3["event_counts"].items():
        for example in c["event_examples"]:
            lines.append(f"- {pair}: {example}")
    lines += ["", "Totals: " + json.dumps(q3["event_totals"])]
    after = q3["after_event"]
    lines += ["", "## Q3e movement after a re-index event (stratified by window and start band)", "",
              "| window | signal | event rows | P(moved) diff [CI] | mean abs change diff [CI] | crossed top 10 diff [CI] |",
              "| --- | --- | ---: | --- | --- | --- |"]
    for window in ("same", "next"):
        for treat in ("reindex_event", "reindex_event_strict", "title_event", "date_value_event", "display_event"):
            res = after[window][treat]
            lines.append(f"| {window} | {treat} | {res['moved']['treated_rows']} | {fmt_effect(res['moved'])} | "
                         f"{fmt_effect(res['absd'], 2)} | {fmt_effect(res['crossed'])} |")
        u = after[window]["reindex_event"]["moved_url_cluster"]
        lines.append(f"| {window} | reindex_event, URL clusters | {u['treated_rows']} | {fmt_effect(u)} | | |")
    lines += ["", "## Q3e direction", ""]
    for window, res in after["direction"].items():
        lines.append(f"- {window}: title-event rows that moved {res['title_event_rows_moved']}, with a query-term change "
                     f"{res['with_term_count_change']} (more terms {res['terms_up']}, fewer {res['terms_down']}). "
                     f"Term rule {mt.fmt_rate(res['term_rule'])}; position rule on same rows {mt.fmt_rate(res['position_rule_same_rows'])}; "
                     f"difference {mt.fmt_diff(res['term_minus_position'])}. Date-newer-goes-up on {res['date_events_moved']} moved "
                     f"date-event rows: {mt.fmt_rate(res['date_rule_newer_up'])}.")
    lines += ["", "## Q3c title tag changes in our fetches vs the SERP title", "",
              "Labels per pulled day: O = SERP title matches the old tag only, N = new tag only, B = both, x = neither, m = mixed within the day.", "",
              "| URL | tag changed between fetches | old tag | new tag | queries | SERP days on or after new fetch | first SERP day with new tag | lag (days) | SERP title events after old fetch | verdict |",
              "| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |"]
    for tag in q3["title_tag_changes"]:
        lines.append(f"| {tag['key']} | {tag['fetch_old_day']} > {tag['fetch_new_day']} | {_cell(tag['old_title'])} | {_cell(tag['new_title'])} | "
                     f"{tag['serp_queries']} | {', '.join(tag['serp_days_after_new_fetch'])} | {tag['first_serp_new_day']} | "
                     f"{tag['lag_days_vs_fetch_new']} | {', '.join(tag['serp_title_events_after_old_fetch'])} | {tag['verdict']} |")
    lines += [""]
    for tag in q3["title_tag_changes"]:
        lines.append(f"- {tag['key']} ({tag['fetch_old_day']} > {tag['fetch_new_day']}): " +
                     "; ".join(f"{d} {'/'.join(labs)}" for d, labs in tag["labels_by_day"].items()))
    lines += ["", "Material rows vs SERP event in the same window: " + json.dumps(q3["material_rows_vs_serp_event"]), ""]
    lines += ["## Q4 lag", "",
              "| window after the fetch pair | rows | material rows | P(moved) diff [CI] | URL-cluster CI | mean abs change diff [CI] | URL-cluster CI | excluding pages changed again | without client site, P(moved) | without client site, mean abs change |",
              "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |"]
    for label in ("next", "next2"):
        res = q4[label]
        mu, au = res["moved_url_cluster"], res["absd_url_cluster"]
        lines.append(f"| {label} | {res['rows']} | {res['material_rows']} | {fmt_effect(res['moved'])} | [{mu['lo']:+.3f}, {mu['hi']:+.3f}] | "
                     f"{fmt_effect(res['absd'], 2)} | [{au['lo']:+.2f}, {au['hi']:+.2f}] | "
                     f"{fmt_effect(res['moved_excluding_pages_changed_again'])} | {fmt_effect(res['without_client']['moved'])} | "
                     f"{fmt_effect(res['without_client']['absd'], 2)} |")
    for label in ("next", "next2"):
        c = q4[label]["client_only"]
        lines.append(f"\nClient site only, {label} window: P(moved) {fmt_effect(c['moved'])} "
                     f"({c['moved']['treated_rows_matched']} of {c['moved']['treated_rows']} changed rows matched); "
                     f"mean abs change {fmt_effect(c['absd'], 2)}")
    lines += ["", "Material rows by window: " + json.dumps(q4["material_rows_by_window"]),
              "", "Patterns (same, next, next2; M moved, - static, ? no later day): " + json.dumps(q4["material_row_patterns_same_next_next2"]), ""]
    for r in q4["next"]["material_detail"]:
        lines.append(f"- {r['key'].split('//')[-1]} [{r['query']}] fetch window {r['fetch_window']}: {r['pos_a']} -> {r['pos_b']} -> {r['pos_c']} -> {r['pos_d']}")
    (OUT / "tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_summary(result: dict) -> None:
    q1 = result["q1_excess_movement"]
    print("\nQ1 material vs not (stratified):")
    for outcome in ("moved", "absd", "crossed"):
        print(f"  {outcome}: {fmt_effect(q1['material_vs_not'][outcome]['joint_query'])}")
    j = q1["material_vs_not"]["moved"]["joint_query"]
    print(f"  share of moves the edit can have caused: point {j['attributable_share']:.3f}, upper 95% {j['attributable_share_hi95']:.3f}")
    print("Q1 any change vs none:")
    for outcome in ("moved", "absd", "crossed"):
        print(f"  {outcome}: {fmt_effect(q1['anychange_vs_none'][outcome])}")
    print("Q3 event totals:", json.dumps(result["q3_serp_signals"]["event_totals"]))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
