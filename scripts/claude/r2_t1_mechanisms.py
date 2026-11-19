"""Round 2 research for Target 1: where a URL that is new to a search's top 10 lands, and why.

Design days only (<= 2026-11-17). Descriptive mechanism measurements, not a predictor to freeze.
Every number in evals/claude/r2/t1-mechanisms/notes.md comes from this script:

  python scripts/claude/r2_t1_mechanisms.py      # writes results.json and results.md, prints a summary

Data access: SERP rows only through scripts.claude.rsdata with rsdata.DESIGN_MAX_DATE (via
t1_common.design_rows and t1_common.emulation_rows). The maximum loaded day is asserted to be
2026-11-17. The frozen round 1 rule is called read-only through t1_placement.predict_rows.
No network, no Serper, no direct SQLite access, no file outside the output folder is written.

Slices (as in round 1): o30 outcome days Nov 9, 11, 13, 16, 17; the Revorion outcome day Nov 17;
and the "a70 emulation" (o30 searches, history cut to the Nov 16 q100 pull plus the Nov 16 deep
list, outcome Nov 17 three-pull consensus). "Pooled" means the six real design slices; the
emulation reuses the Nov 17 o30 outcome and is always reported on its own.

Every derived position used as a prediction is rounded half up and clipped to [2, 9], the same
clip as the frozen rule. With truth in 1..10 that clip never lowers band3 or err3.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, spearmanr

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import t1_common as c  # noqa: E402
from scripts.claude import t1_placement as t1  # noqa: E402

OUT_DIR = ROOT / "evals" / "claude" / "r2" / "t1-mechanisms"
SEALED_MAX = "2026-11-17"
ABSENT = rs.ABSENT
LO, HI = 2, 9
METRICS = {"band3": m.band3_hit, "err3": m.err3_hit}
REFS = ("c9", "c7", "dp", "frozen")

SLICES = [
    ("o30 Nov 09", "design", "2026-11-09", "o30"),
    ("o30 Nov 11", "design", "2026-11-11", "o30"),
    ("o30 Nov 13", "design", "2026-11-13", "o30"),
    ("o30 Nov 16", "design", "2026-11-16", "o30"),
    ("o30 Nov 17", "design", "2026-11-17", "o30"),
    ("rev Nov 17", "design", "2026-11-17", "rev"),
    ("a70 emulation", "emu", "2026-11-17", "o30"),
]
POOL = [name for name, source, _, _ in SLICES if source == "design"]
EMU = "a70 emulation"

# ---------------------------------------------------------------- pre-stated Q3 signal definitions
# Fixed in code before any Q3 table was printed. Not tuned afterwards.
PLATFORMS = (
    "reddit.com", "youtube.com", "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "tiktok.com", "quora.com", "medium.com", "wikipedia.org", "apple.com", "google.com", "amazon.com",
    "capterra.com", "g2.com", "softwareadvice.com", "getapp.com", "trustradius.com", "softwareconnect.com",
    "selecthub.com",
)
QUERY_STOP = {"a", "an", "the", "and", "or", "for", "of", "in", "to", "with", "vs", "on", "by"}
BRAND_PREFIXES = ("get", "try", "use", "go", "my", "the")
BRAND_SUFFIXES = ("hq", "app", "software", "soft", "inc", "online")


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def query_terms(query: str) -> list[str]:
    out = []
    for tok in re.split(r"[^a-z0-9]+", str(query).lower()):
        if len(tok) < 2 or tok in QUERY_STOP:
            continue
        if len(tok) > 3 and tok.endswith("s"):
            tok = tok[:-1]
        out.append(tok)
    return out


def all_terms_in(query: str, text: str | None) -> bool | None:
    """Every search term (stop words dropped, trailing s stripped) is a substring of the compacted text."""
    if text is None:
        return None
    body = compact(text)
    terms = query_terms(query)
    return bool(terms) and all(term in body for term in terms)


def brand_names(site: str) -> set[str]:
    label = str(site).split(".")[0].lower()
    names = {label}
    for pre in BRAND_PREFIXES:
        if label.startswith(pre) and len(label) - len(pre) >= 3:
            names.add(label[len(pre):])
    for name in list(names):
        for suf in BRAND_SUFFIXES:
            if name.endswith(suf) and len(name) - len(suf) >= 3:
                names.add(name[: -len(suf)])
    return {name for name in names if len(name) >= 3}


def brand_in_query(query: str, site: str) -> bool:
    body = compact(query)
    return any(name in body for name in brand_names(site))


def url_slug(url: str) -> str:
    try:
        return urlsplit(str(url)).path or "/"
    except ValueError:
        return "/"


# ---------------------------------------------------------------- small helpers
def clip(value) -> int | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return int(min(max(int(np.floor(float(value) + 0.5)), LO), HI))


def jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, float) and np.isnan(obj):
        return None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def add_hits(frame: pd.DataFrame, cols) -> pd.DataFrame:
    frame = frame.copy()
    for col in cols:
        for metric, fn in METRICS.items():
            frame[f"{metric}_{col}"] = [int(fn(p, t)) for p, t in zip(frame[col], frame["truth"], strict=True)]
    return frame


def compare(frame: pd.DataFrame, signals, refs=REFS, diffs=("c9", "frozen")) -> dict:
    """Rates with query-bootstrap CIs for each signal and reference, plus paired differences."""
    out: dict = {"n": int(len(frame)), "queries": int(frame["query"].nunique()) if len(frame) else 0}
    if frame.empty:
        return out
    scored = add_hits(frame, list(signals) + list(refs))
    for metric in METRICS:
        block = {}
        for col in list(signals) + list(refs):
            block[col] = m.bootstrap_rate(scored, f"{metric}_{col}")
        for sig in signals:
            for base in diffs:
                if sig != base:
                    block[f"{sig} - {base}"] = m.bootstrap_difference(scored, f"{metric}_{sig}", f"{metric}_{base}")
        out[metric] = block
    return out


def hits_only(frame: pd.DataFrame, cols) -> dict:
    """Plain hit counts (no bootstrap) for per-slice stability tables."""
    out = {"n": int(len(frame)), "queries": int(frame["query"].nunique()) if len(frame) else 0}
    for col in cols:
        for metric, fn in METRICS.items():
            out[f"{metric}_{col}"] = int(sum(int(fn(p, t)) for p, t in zip(frame[col], frame["truth"], strict=True)))
    return out


def landing_profile(truths) -> dict:
    values = [int(v) for v in truths]
    n = len(values)
    if not n:
        return {"n": 0}
    hist = Counter(values)
    return {
        "n": n,
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "top3": int(sum(v <= 3 for v in values)),
        "pos4_7": int(sum(4 <= v <= 7 for v in values)),
        "pos8_10": int(sum(v >= 8 for v in values)),
        "hist": {str(p): int(hist.get(p, 0)) for p in range(1, 11)},
        "band3_c9": int(sum(m.band3_hit(9, v) for v in values)),
        "band3_c7": int(sum(m.band3_hit(7, v) for v in values)),
        "err3_c7": int(sum(m.err3_hit(7, v) for v in values)),
        "err3_c9": int(sum(m.err3_hit(9, v) for v in values)),
    }


# ---------------------------------------------------------------- loading (sealed)
def load() -> tuple[pd.DataFrame, pd.DataFrame, str]:
    assert rs.DESIGN_MAX_DATE == SEALED_MAX, rs.DESIGN_MAX_DATE
    rows = c.design_rows().copy()
    max_day = str(rows["day"].max())
    assert max_day == SEALED_MAX, f"design rows reach {max_day}"
    emu = c.emulation_rows().copy()
    assert str(emu["day"].max()) <= SEALED_MAX
    return rows, emu, max_day


# ---------------------------------------------------------------- sibling index
class SibIndex:
    """Consensus top-10 lists of every search on every history day (history only)."""

    def __init__(self, hcons: pd.DataFrame, groups: dict):
        top = hcons[hcons["cons"] <= 10]
        self.groups = groups
        self.top: dict[tuple[str, str], dict[str, int]] = {}
        self.site_best: dict[tuple[str, str], dict[str, tuple[int, str]]] = {}
        self.days: dict[str, list[str]] = defaultdict(list)
        for (query, day), group in top.groupby(["query", "day"], sort=True):
            ranks = {k: int(v) for k, v in zip(group["key"], group["cons"], strict=True)}
            self.top[(query, day)] = ranks
            best: dict[str, tuple[int, str]] = {}
            for k, s, v in zip(group["key"], group["site"], group["cons"], strict=True):
                if s not in best or (int(v), k) < best[s]:
                    best[s] = (int(v), k)
            self.site_best[(query, day)] = best
            self.days[query].append(day)
        for query in self.days:
            self.days[query].sort()
        self._cache: dict = {}

    def siblings(self, query: str, prev: str, day: str, window: str) -> list[dict]:
        """Other searches with a top-10 list in the window; J = Jaccard of URL keys vs the target's prev-day top 10."""
        cache_key = (query, prev, day, window)
        if cache_key in self._cache:
            return self._cache[cache_key]
        if window == "prev":
            lo = hi = prev
        else:
            lo = (pd.Timestamp(day) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
            hi = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        target = self.top.get((query, prev), {})
        found = []
        for other, days in self.days.items():
            if other == query:
                continue
            inside = [d for d in days if lo <= d <= hi]
            if not inside:
                continue
            d2 = inside[-1]
            ranks = self.top[(other, d2)]
            inter = set(target) & set(ranks)
            union = set(target) | set(ranks)
            found.append(
                {"query": other, "day": d2, "ranks": ranks, "site_best": self.site_best[(other, d2)],
                 "J": len(inter) / len(union) if union else 0.0, "shared": inter, "grp": self.groups.get(other, "other")}
            )
        self._cache[cache_key] = found
        return found


def relative_place(target: dict[str, int], sib_ranks: dict[str, int], anchor_pos: int, shared: set[str]) -> tuple[float, float] | None:
    """Place a URL among the URLs two searches share, by its order in the sibling.

    L = worst target position of shared URLs above it in the sibling (0 if none),
    U = best target position of shared URLs below it (11 if none).
    Returns (mid = (L+U)/2, insert = L+1 if L<U else mid). None if nothing is shared.
    """
    if not shared:
        return None
    above = [target[x] for x in shared if sib_ranks[x] < anchor_pos]
    below = [target[x] for x in shared if sib_ranks[x] > anchor_pos]
    low = max(above, default=0)
    high = min(below, default=ABSENT)
    mid = (low + high) / 2.0
    insert = float(min(low + 1, 10)) if low < high else mid
    return mid, insert


def sibling_signals(sib: SibIndex, query: str, prev: str, day: str, key: str, site: str, window: str) -> dict:
    sibs = sib.siblings(query, prev, day, window)
    target = sib.top.get((query, prev), {})
    url_ev = [s for s in sibs if key in s["ranks"]]
    out: dict = {"url_n": len(url_ev), "url_nJ": sum(1 for s in url_ev if s["J"] > 0)}
    if url_ev:
        pos = [s["ranks"][key] for s in url_ev]
        out["raw_min"] = min(pos)
        out["raw_med"] = float(np.median(pos))
        best_j = max(s["J"] for s in url_ev)
        out["maxJ"] = best_j
        tied = [s for s in url_ev if s["J"] == best_j]
        out["raw_maxJ"] = float(np.median([s["ranks"][key] for s in tied]))
        out["maxJ_grp"] = tied[0]["grp"]
        weight = sum(s["J"] for s in url_ev)
        out["raw_wJ"] = sum(s["J"] * s["ranks"][key] for s in url_ev) / weight if weight > 0 else None
        out["url_top3"] = min(pos) <= 3
        rels = []
        for s in tied:
            rel = relative_place(target, s["ranks"], s["ranks"][key], s["shared"] - {key})
            if rel is not None:
                rels.append(rel)
        out["rel_mid"] = float(np.median([r[0] for r in rels])) if rels else None
        out["rel_ins"] = float(np.median([r[1] for r in rels])) if rels else None
        out["sib_grps"] = sorted({s["grp"] for s in url_ev})
    else:
        out["url_top3"] = False
    site_ev = [s for s in sibs if site in s["site_best"]]
    out["site_n"] = len(site_ev)
    if site_ev:
        pos = [s["site_best"][site][0] for s in site_ev]
        out["site_min"] = min(pos)
        weight = sum(s["J"] for s in site_ev)
        out["site_wJ"] = sum(s["J"] * s["site_best"][site][0] for s in site_ev) / weight if weight > 0 else None
        best_j = max(s["J"] for s in site_ev)
        tied = [s for s in site_ev if s["J"] == best_j]
        out["site_maxJ"] = float(np.median([s["site_best"][site][0] for s in tied]))
        rels = []
        for s in tied:
            pos_s, anchor_key = s["site_best"][site]
            rel = relative_place(target, s["ranks"], pos_s, s["shared"] - {anchor_key, key})
            if rel is not None:
                rels.append(rel)
        out["site_rel_mid"] = float(np.median([r[0] for r in rels])) if rels else None
    return out


# ---------------------------------------------------------------- slices
class Slice:
    def __init__(self, name: str, rows: pd.DataFrame, cons: pd.DataFrame, day: str, grp: str, groups: dict):
        self.name, self.rows, self.cons, self.day, self.grp = name, rows, cons, day, grp
        self.history = rows[rows["day"] < day].copy()
        assert self.history["day"].max() < day
        self.hcons = cons[cons["day"] < day]
        placed = rs.placement_rows(rows, cons, day)
        self.placed = placed[placed["query"].map(groups) == grp].reset_index(drop=True)
        self.hist = t1.History(self.history)
        self.sib = SibIndex(self.hcons, groups)
        self.pulls = rs.pulls_by_query_day(self.history)
        today = cons[(cons["day"] == day) & (cons["cons"] <= 10)]
        self.now_top = {q: dict(zip(g["key"], g["cons"].astype(int), strict=True)) for q, g in today.groupby("query")}
        prevtop = self.hcons[self.hcons["cons"] <= 10]
        self.prev_top = {(q, d): dict(zip(g["key"], g["cons"].astype(int), strict=True)) for (q, d), g in prevtop.groupby(["query", "day"])}
        self.prev_site = {(q, d): dict(zip(g["key"], g["site"], strict=True)) for (q, d), g in prevtop.groupby(["query", "day"])}
        self.prev_cons_all = {(q, d): g for (q, d), g in self.hcons.groupby(["query", "day"])}


def entrant_frame(sl: Slice) -> pd.DataFrame:
    e = sl.placed[sl.placed["klass"] != "survivor"].reset_index(drop=True).copy()
    if e.empty:
        return e
    e["slice"] = sl.name
    targets = e[["query", "key", "url", "site", "day"]].copy()
    e["frozen"] = t1.predict_rows(sl.history, targets, hist=sl.hist)
    feats = t1.features(sl.history, targets, sl.hist)
    for col in ("deep_key", "deep_site", "last_top_pos", "days_since_top", "any_key_prev"):
        e[col] = feats[col].to_numpy()
    e["dp"] = e["domain_prior"].astype(int)
    e["c9"] = 9
    e["c7"] = 7
    return e


# ---------------------------------------------------------------- Q1 slot accounting
def unstable_slots(pulls: list) -> dict | None:
    """Instability of the previous day's consensus-session pulls (needs at least two pulls)."""
    session = rs.consensus_session(pulls)
    if len(session) < 2:
        return None
    by_pos = []
    for pull in session:
        inv = {pos: key for key, pos in pull.ranks.items()}
        by_pos.append(inv)
    unstable = [p for p in range(1, 11) if len({inv.get(p) for inv in by_pos}) > 1]
    return {"n_pulls": len(session), "u_top": min(unstable) if unstable else None, "u_bottom": max(unstable) if unstable else None,
            "n_unstable": len(unstable)}


def q1_rows(sl: Slice, e: pd.DataFrame) -> pd.DataFrame:
    recs = []
    for r in e.itertuples(index=False):
        prev = sl.prev_top.get((r.query, r.prev_day), {})
        sites = sl.prev_site.get((r.query, r.prev_day), {})
        now = sl.now_top.get(r.query, {})
        leavers = {k: p for k, p in prev.items() if k not in now}
        vac = sorted(leavers.values())
        rec = {"n_leavers": len(vac), "vacated": tuple(vac), "vac_dist": min((abs(r.truth - v) for v in vac), default=None)}
        same = [(k, p) for k, p in prev.items() if sites.get(k) == r.site and k != r.key]
        if not same:
            rec["ss_status"] = "none"
        else:
            left = [(k, p) for k, p in same if k not in now]
            if left:
                rec["ss_status"] = "swap"
                rec["swap_slot"] = min(p for _, p in left)
            else:
                rec["ss_status"] = "addition"
                rec["inc_prev"] = min(p for _, p in same)
                rec["inc_now"] = min(now[k] for k, _ in same)
        inst = unstable_slots(sl.pulls.get((r.query, r.prev_day), []))
        if inst is not None:
            rec.update({"u_n_pulls": inst["n_pulls"], "u_top": inst["u_top"], "u_bottom": inst["u_bottom"], "u_n_unstable": inst["n_unstable"]})
        recs.append(rec)
    return pd.concat([e.reset_index(drop=True), pd.DataFrame.from_records(recs)], axis=1)


def perm_null(frame: pd.DataFrame, width: int, rng: np.random.Generator, draws: int = m.BOOT_DRAWS) -> dict:
    """Observed count of entrants within ``width`` of a vacated slot, and a null that permutes landings within slice."""
    sub = frame[frame["n_leavers"] > 0].reset_index(drop=True)
    if sub.empty:
        return {"n": 0}
    mat = np.zeros((len(sub), 10), dtype=bool)
    for i, vac in enumerate(sub["vacated"]):
        for p in range(1, 11):
            mat[i, p - 1] = any(abs(p - v) <= width for v in vac)
    truths = sub["truth"].to_numpy(dtype=int)
    obs = int(mat[np.arange(len(sub)), truths - 1].sum())
    uniform = float(mat.sum(axis=1).sum() / 10.0)
    slices = sub["slice"].to_numpy()
    idx_by_slice = [np.flatnonzero(slices == s) for s in np.unique(slices)]
    counts = np.empty(draws)
    for d in range(draws):
        perm = truths.copy()
        for idx in idx_by_slice:
            perm[idx] = truths[rng.permutation(idx)]
        counts[d] = mat[np.arange(len(sub)), perm - 1].sum()
    return {"n": int(len(sub)), "observed": obs, "null_mean": float(counts.mean()), "null_lo": float(np.percentile(counts, 2.5)),
            "null_hi": float(np.percentile(counts, 97.5)), "p_null_ge_obs": float((counts >= obs).mean()), "uniform_expected": uniform}


def one_in_one_out(sl: Slice, e: pd.DataFrame) -> list[dict]:
    """Search-days with exactly one entrant and one leaver: where did the entrant land relative to the vacated slot?"""
    out = []
    for query, group in e.groupby("query"):
        prev_day = group["prev_day"].iloc[0]
        prev = sl.prev_top.get((query, prev_day), {})
        now = sl.now_top.get(query, {})
        leavers = {k: p for k, p in prev.items() if k not in now}
        if len(group) != 1 or len(leavers) != 1:
            continue
        land = int(group["truth"].iloc[0])
        vac = next(iter(leavers.values()))
        surv = [(prev[k], now[k]) for k in prev if k in now]
        if land < vac:
            between = [(a, b) for a, b in surv if land <= a <= vac - 1]
            expected = [b - a == 1 for a, b in between]
        elif land > vac:
            between = [(a, b) for a, b in surv if vac + 1 <= a <= land]
            expected = [b - a == -1 for a, b in between]
        else:
            between, expected = [], []
        outside = [(a, b) for a, b in surv if (a, b) not in between]
        direction = 1 if land < vac else (-1 if land > vac else 0)
        out.append({"slice": sl.name, "query": query, "klass": group["klass"].iloc[0], "land": land, "vacated": vac,
                    "between_n": len(between), "between_expected_shift": int(sum(expected)),
                    "outside_n": len(outside), "outside_unchanged": int(sum(a == b for a, b in outside)),
                    "outside_same_shift": int(sum(b - a == direction for a, b in outside)) if direction else 0,
                    "outside_n_directional": len(outside) if direction else 0})
    return out


def leaver_profile(sl: Slice) -> list[dict]:
    """Every URL in the previous day's consensus top 10 of each scored search: did it leave, by position and stability."""
    out = []
    queries = sorted(set(sl.placed["query"]))
    for query in queries:
        prev_day = sl.placed.loc[sl.placed["query"] == query, "prev_day"].iloc[0]
        frame = sl.prev_cons_all.get((query, prev_day))
        if frame is None:
            continue
        now = sl.now_top.get(query, {})
        for r in frame[frame["cons"] <= 10].itertuples(index=False):
            if r.n_present < r.n_pulls:
                state = "missing from a pull"
            elif r.pos_min != r.pos_max:
                state = "in every pull, position varied"
            else:
                state = "same position in every pull"
            out.append({"slice": sl.name, "query": query, "prev_pos": int(r.cons), "left": r.key not in now,
                        "multi_pull": int(r.n_pulls) >= 2, "state": state})
    return out


# ---------------------------------------------------------------- Q5 reentrant anchors
def reentrant_anchors(sl: Slice, e: pd.DataFrame) -> pd.DataFrame:
    top = sl.history[sl.history["kind"] == "top10"].sort_values(["fetched_at", "position"], ascending=[False, True])
    last_pull = top.drop_duplicates(["query", "key"])
    last_pull_pos = {(q, k): (int(p), d) for q, k, p, d in zip(last_pull["query"], last_pull["key"], last_pull["position"], last_pull["day"], strict=True)}
    best_ever = top.groupby(["query", "key"])["position"].min().to_dict()
    cons_top = {(q, d, k) for q, d, k, v in zip(sl.hcons["query"], sl.hcons["day"], sl.hcons["key"], sl.hcons["cons"], strict=True) if v <= 10}
    flick = top[[(q, d, k) not in cons_top for q, d, k in zip(top["query"], top["day"], top["key"], strict=True)]]
    flick = flick.drop_duplicates(["query", "key"])
    flick_pos = {(q, k): (int(p), d) for q, k, p, d in zip(flick["query"], flick["key"], flick["position"], flick["day"], strict=True)}
    e = e.copy()
    e["a_deep"] = [int(v) if v <= 30 else None for v in e["deep_key"]]
    e["a_last_top"] = [int(v) if v <= 10 else None for v in e["last_top_pos"]]
    e["a_last_pull"] = [last_pull_pos.get((q, k), (None, None))[0] for q, k in zip(e["query"], e["key"], strict=True)]
    e["last_pull_day"] = [last_pull_pos.get((q, k), (None, None))[1] for q, k in zip(e["query"], e["key"], strict=True)]
    e["a_flicker"] = [flick_pos.get((q, k), (None, None))[0] for q, k in zip(e["query"], e["key"], strict=True)]
    e["a_best_ever"] = [best_ever.get((q, k)) for q, k in zip(e["query"], e["key"], strict=True)]
    return e


# ---------------------------------------------------------------- Q4 gap and truth-type emulations
def cut_case(rows: pd.DataFrame, cons: pd.DataFrame, day: str, cut: str, grp: str, groups: dict, label: str) -> dict:
    keep = (rows["day"] <= cut) | (rows["day"] == day)
    rows_c = rows[keep].reset_index(drop=True)
    cons_c = cons[(cons["day"] <= cut) | (cons["day"] == day)].reset_index(drop=True)
    sl = Slice(label, rows_c, cons_c, day, grp, groups)
    assert sl.history["day"].max() == cut
    e = entrant_frame(sl)
    e = q1_rows(sl, e)
    out = {"label": label, "outcome": day, "cut": cut, "gap": rs.calendar_gap(cut, day),
           "queries": int(sl.placed["query"].nunique()), "has_deep_in_history": bool((sl.history["kind"] == "deep").any())}
    for klass in ("new", "reentrant"):
        sub = e[e["klass"] == klass] if len(e) else e
        prof = landing_profile(sub["truth"]) if len(sub) else {"n": 0}
        if len(sub):
            prof["band3_frozen"] = int(sum(m.band3_hit(p, t) for p, t in zip(sub["frozen"], sub["truth"], strict=True)))
            prof["err3_frozen"] = int(sum(m.err3_hit(p, t) for p, t in zip(sub["frozen"], sub["truth"], strict=True)))
            dp_rows = sub[sub["dp"] <= 10]
            prof["dp_available"] = int(len(dp_rows))
            prof["dp_rows_band3_dp"] = int(sum(m.band3_hit(clip(p), t) for p, t in zip(dp_rows["dp"], dp_rows["truth"], strict=True)))
            prof["dp_rows_band3_c9"] = int(sum(m.band3_hit(9, t) for t in dp_rows["truth"]))
            sw = sub[sub["ss_status"] == "swap"]
            prof["swap_n"] = int(len(sw))
            prof["swap_within1"] = int(((sw["truth"] - sw["swap_slot"]).abs() <= 1).sum()) if len(sw) else 0
            prof["swap_band3_slot"] = int(sum(m.band3_hit(clip(s), t) for s, t in zip(sw["swap_slot"], sw["truth"], strict=True))) if len(sw) else 0
            prof["swap_band3_c9"] = int(sum(m.band3_hit(9, t) for t in sw["truth"])) if len(sw) else 0
        out[klass] = prof
    out["survivor"] = int((sl.placed["klass"] == "survivor").sum())
    return out


def single_pull_truth(rows: pd.DataFrame, day: str, grp: str, groups: dict) -> dict:
    """Same outcome day scored with each voting pull alone as truth, against the three-pull consensus."""
    day_pulls = rs.pulls_by_query_day(rows[(rows["day"] == day)])
    sources = Counter()
    for (query, _), pulls in day_pulls.items():
        if groups.get(query) != grp:
            continue
        for pull in rs.consensus_session(pulls):
            sources[pull.source] += 1
    out = {"day": day, "sources": dict(sources), "by_source": {}}
    for source in sorted(sources):
        keep = (rows["day"] < day) | ((rows["day"] == day) & (rows["source"] == source))
        rows_s = rows[keep].reset_index(drop=True)
        cons_s = rs.consensus_table(rows_s)
        placed = rs.placement_rows(rows_s, cons_s, day)
        placed = placed[placed["query"].map(groups) == grp]
        block = {"n_pulls_truth": sorted(set(placed["n_pulls"].tolist()))}
        for klass in ("new", "reentrant"):
            block[klass] = landing_profile(placed.loc[placed["klass"] == klass, "truth"])
        block["survivor"] = int((placed["klass"] == "survivor").sum())
        out["by_source"][source] = block
    return out


# ---------------------------------------------------------------- markdown helpers
def table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    lines.append("")
    return lines


def fr(block: dict) -> str:
    return m.fmt_rate(block) if block.get("n") else "0/0"


def fd(block: dict) -> str:
    return m.fmt_diff(block)


def two_by_two(frame: pd.DataFrame, signal: str, outcome: str) -> dict:
    sub = frame[frame[signal].notna()]
    sig = sub[signal].astype(bool)
    out_ = sub[outcome].astype(bool)
    a = int((sig & out_).sum())
    b = int((sig & ~out_).sum())
    c_ = int((~sig & out_).sum())
    d = int((~sig & ~out_).sum())
    p = float(fisher_exact([[a, b], [c_, d]])[1]) if (a + b) and (c_ + d) else None
    return {"sig_hit": a, "sig_miss": b, "nosig_hit": c_, "nosig_miss": d, "fisher_p": p,
            "sig_rate": a / (a + b) if a + b else None, "nosig_rate": c_ / (c_ + d) if c_ + d else None}


# ---------------------------------------------------------------- main
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, emu_rows, max_day = load()
    groups = rs.query_groups(rows)
    cons = rs.consensus_table(rows)
    emu_cons = rs.consensus_table(emu_rows)
    rng = np.random.default_rng(m.BOOT_SEED)
    report: dict = {"label": "DESCRIPTIVE (design days only); predictions marked PREDICTED", "max_day_loaded": max_day,
                    "design_max_date": rs.DESIGN_MAX_DATE, "clip": [LO, HI]}
    md: list[str] = ["# R2 T1 mechanisms: generated tables", "",
                     "Generated by `scripts/claude/r2_t1_mechanisms.py`. Design days <= 2026-11-17 only. "
                     f"Maximum day loaded: {max_day}. Intervals: query bootstrap, {m.BOOT_DRAWS} draws, seed {m.BOOT_SEED}.", ""]

    slices: dict[str, Slice] = {}
    frames = []
    for name, source, day, grp in SLICES:
        r_, c_ = (rows, cons) if source == "design" else (emu_rows, emu_cons)
        sl = Slice(name, r_, c_, day, grp, groups)
        slices[name] = sl
        e = entrant_frame(sl)
        e = q1_rows(sl, e)
        e = reentrant_anchors(sl, e)
        frames.append(e)
    ent = pd.concat(frames, ignore_index=True)
    ent["is_pool"] = ent["slice"].isin(POOL)

    # ---- checks
    pool_new = ent[ent["is_pool"] & (ent["klass"] == "new")]
    frozen_hits = int(sum(m.band3_hit(p, t) for p, t in zip(pool_new["frozen"], pool_new["truth"], strict=True)))
    checks = {"max_day_loaded": max_day, "pooled_new_rows": int(len(pool_new)), "pooled_new_frozen_band3": frozen_hits,
              "round1_reported": "148/303"}
    assert (frozen_hits, len(pool_new)) == (148, 303), checks
    emu_hist = slices[EMU].history
    checks["emu_history_sources"] = sorted(emu_hist["source"].unique().tolist())
    report["checks"] = checks

    # ---- slice overview
    overview = []
    for name, _, day, grp in SLICES:
        sl = slices[name]
        p = sl.placed
        overview.append({"slice": name, "outcome": day, "group": grp, "queries": int(p["query"].nunique()),
                         "prev_days": sorted(set(p["prev_day"])), "gap": sorted(set(p["gap_days"].astype(int))),
                         "truth_pulls": sorted(set(p["n_pulls"].astype(int))),
                         "new": int((p["klass"] == "new").sum()), "reentrant": int((p["klass"] == "reentrant").sum()),
                         "survivor": int((p["klass"] == "survivor").sum()),
                         "history_has_deep": bool((sl.history["kind"] == "deep").any())})
    report["slices"] = overview
    md += ["## Slices", ""]
    md += table(["slice", "outcome", "prev day", "gap", "truth pulls", "queries", "new", "reentrant", "survivor", "deep list in history"],
                [[o["slice"], o["outcome"], ",".join(o["prev_days"]), o["gap"], o["truth_pulls"], o["queries"], o["new"], o["reentrant"],
                  o["survivor"], o["history_has_deep"]] for o in overview])
    md += [f"Check: frozen round 1 rule on pooled new rows, band3 {frozen_hits}/{len(pool_new)} (round 1 reported 148/303).", ""]

    # =========================================================== Q1
    q1: dict = {}
    md += ["## Q1 Slot accounting", ""]
    # (a) vacated slots, permutation null
    rows_a = []
    q1["vacated"] = {}
    for klass in ("new", "reentrant", "all"):
        for scope in POOL + [EMU, "pooled"]:
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            if klass != "all":
                sub = sub[sub["klass"] == klass]
            if sub.empty:
                continue
            exact = perm_null(sub, 0, rng)
            within = perm_null(sub, 1, rng)
            res = {"entrants": int(len(sub)), "with_leaver": exact.get("n", 0), "exact": exact, "within1": within}
            q1["vacated"][f"{klass}|{scope}"] = res
            if exact.get("n"):
                rows_a.append([klass, scope, len(sub), exact["n"], exact["observed"], f"{exact['null_mean']:.1f}",
                               within["observed"], f"{within['null_mean']:.1f} [{within['null_lo']:.0f}, {within['null_hi']:.0f}]",
                               f"{within['p_null_ge_obs']:.3f}", f"{within['uniform_expected']:.1f}"])
    md += ["### (a) Landing versus the previous-day positions of the URLs that left", "",
           "Null: landing positions permuted among entrants of the same slice (2000 draws, seed 20261119). Uniform: landing uniform on 1..10.", ""]
    md += table(["class", "slice", "entrants", "with >=1 leaver", "landed on a vacated slot", "null mean", "within 1 of a vacated slot",
                 "null mean [95%]", "P(null >= obs)", "uniform expected"], rows_a)
    # leaver profile and landing histogram
    lp = pd.DataFrame.from_records([rec for name in slices for rec in leaver_profile(slices[name])])
    lp["is_pool"] = lp["slice"].isin(POOL)
    pooled_lp = lp[lp["is_pool"]]
    leave_by_pos = pooled_lp.groupby("prev_pos")["left"].agg(["size", "sum"])
    vac_hist = {str(p): int(leave_by_pos["sum"].get(p, 0)) for p in range(1, 11)}
    pos_hist_new = Counter(pool_new["truth"].astype(int))
    pool_re = ent[ent["is_pool"] & (ent["klass"] == "reentrant")]
    pos_hist_re = Counter(pool_re["truth"].astype(int))
    q1["leave_rate_by_prev_pos_pooled"] = {str(p): [int(leave_by_pos["sum"].get(p, 0)), int(leave_by_pos["size"].get(p, 0))] for p in range(1, 11)}
    q1["landing_hist_new_pooled"] = {str(p): int(pos_hist_new.get(p, 0)) for p in range(1, 11)}
    q1["landing_hist_reentrant_pooled"] = {str(p): int(pos_hist_re.get(p, 0)) for p in range(1, 11)}
    md += ["### Vacated slots versus landing slots (pooled design slices)", ""]
    md += table(["position", "URLs at this position yesterday", "of which left", "new entrants landing here", "reentrants landing here"],
                [[p, int(leave_by_pos["size"].get(p, 0)), vac_hist[str(p)], pos_hist_new.get(p, 0), pos_hist_re.get(p, 0)] for p in range(1, 11)])
    # leave rate by stability (multi-pull previous days only)
    mp = lp[lp["multi_pull"]]
    q1["multi_pull_prev_slices"] = sorted(set(mp["slice"]))
    stab_rows = []
    q1["leave_by_stability"] = {}
    states = ("missing from a pull", "in every pull, position varied", "same position in every pull")
    for scope in sorted(set(mp["slice"])):
        sub = mp[mp["slice"] == scope]
        for band, lo_, hi_ in (("positions 1-7", 1, 7), ("positions 8-10", 8, 10)):
            for state in states:
                s2 = sub[(sub["state"] == state) & (sub["prev_pos"] >= lo_) & (sub["prev_pos"] <= hi_)]
                q1["leave_by_stability"][f"{scope}|{band}|{state}"] = [int(s2["left"].sum()), int(len(s2))]
                stab_rows.append([scope, band, state, int(len(s2)), int(s2["left"].sum())])
    md += ["### Who leaves: stability across yesterday's consensus-session pulls (multi-pull previous days only)", ""]
    md += table(["slice", "yesterday position", "yesterday state", "URLs", "left today"], stab_rows)

    # one-in one-out
    oio = pd.DataFrame.from_records([rec for name in slices for rec in one_in_one_out(slices[name], ent[ent["slice"] == name])])
    q1["one_in_one_out"] = {}
    oio_rows = []
    for scope in POOL + [EMU, "pooled"]:
        sub = oio[oio["slice"].isin(POOL)] if scope == "pooled" else oio[oio["slice"] == scope]
        if sub.empty:
            continue
        res = {"search_days": int(len(sub)), "land_eq_vacated": int((sub["land"] == sub["vacated"]).sum()),
               "land_within1": int(((sub["land"] - sub["vacated"]).abs() <= 1).sum()),
               "land_above": int((sub["land"] < sub["vacated"]).sum()), "land_below": int((sub["land"] > sub["vacated"]).sum()),
               "between_survivors": int(sub["between_n"].sum()), "between_shifted_as_insertion": int(sub["between_expected_shift"].sum()),
               "outside_survivors": int(sub["outside_n"].sum()), "outside_unchanged": int(sub["outside_unchanged"].sum()),
               "outside_same_shift": int(sub["outside_same_shift"].sum()), "outside_n_directional": int(sub["outside_n_directional"].sum()),
               "vacated_hist": {str(p): int((sub["vacated"] == p).sum()) for p in range(1, 11)}}
        q1["one_in_one_out"][scope] = res
        oio_rows.append([scope, res["search_days"], res["land_eq_vacated"], res["land_within1"], res["land_above"], res["land_below"],
                         f"{res['between_shifted_as_insertion']}/{res['between_survivors']}",
                         f"{res['outside_same_shift']}/{res['outside_n_directional']}", f"{res['outside_unchanged']}/{res['outside_survivors']}"])
    md += ["### Search-days with exactly one entrant and one leaver", "",
           "Between: survivors whose old position lies between the landing and the vacated slot. Under pure insertion they shift by one toward the vacated slot. "
           "Background: survivors outside that range that shifted by one in the same direction anyway.", ""]
    md += table(["slice", "search-days", "landed on vacated slot", "within 1", "landed above vacated", "landed below vacated",
                 "between survivors shifted by one", "background: other survivors shifted by one the same way", "other survivors unchanged"], oio_rows)

    # (b) same-site swap versus addition
    md += ["### (b) Same-site URL swap versus addition", "",
           "swap: a same-site URL was in yesterday's top 10 and left today. addition: same-site URLs were there and all stayed. "
           "PREDICTED columns use the swapped slot or the incumbent's position, clipped to [2, 9].", ""]
    q1["same_site"] = {}
    ss_rows = []
    for klass in ("new", "reentrant"):
        for scope in POOL + [EMU, "pooled"]:
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[sub["klass"] == klass]
            if sub.empty:
                continue
            res = {"n": int(len(sub)), "none": int((sub["ss_status"] == "none").sum())}
            sw = sub[sub["ss_status"] == "swap"].copy()
            ad = sub[sub["ss_status"] == "addition"].copy()
            res["swap"] = {"n": int(len(sw)), "exact": int((sw["truth"] == sw["swap_slot"]).sum()),
                           "within1": int(((sw["truth"] - sw["swap_slot"]).abs() <= 1).sum()),
                           "band3_slot": int(sum(m.band3_hit(clip(s), t) for s, t in zip(sw["swap_slot"], sw["truth"], strict=True))),
                           "band3_c9": int(sum(m.band3_hit(9, t) for t in sw["truth"])),
                           "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(sw["frozen"], sw["truth"], strict=True)))}
            if len(ad):
                diff = ad["truth"] - ad["inc_now"]
                res["addition"] = {"n": int(len(ad)), "above_incumbent": int((diff < 0).sum()), "directly_below": int((diff == 1).sum()),
                                   "adjacent": int((diff.abs() == 1).sum()), "below_by_2plus": int((diff >= 2).sum()),
                                   "band3_dp": int(sum(m.band3_hit(clip(s), t) for s, t in zip(ad["inc_prev"], ad["truth"], strict=True))),
                                   "band3_dp_plus1": int(sum(m.band3_hit(clip(s + 1), t) for s, t in zip(ad["inc_prev"], ad["truth"], strict=True))),
                                   "band3_c9": int(sum(m.band3_hit(9, t) for t in ad["truth"])),
                                   "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(ad["frozen"], ad["truth"], strict=True))),
                                   "median_truth_minus_inc_prev": float((ad["truth"] - ad["inc_prev"]).median())}
            else:
                res["addition"] = {"n": 0}
            q1["same_site"][f"{klass}|{scope}"] = res
            a = res["addition"]
            ss_rows.append([klass, scope, res["n"], res["none"], res["swap"]["n"], res["swap"]["exact"], res["swap"]["within1"],
                            f"{res['swap']['band3_slot']} / {res['swap']['band3_c9']} / {res['swap']['band3_frozen']}",
                            a["n"], a.get("above_incumbent", 0), a.get("directly_below", 0), a.get("below_by_2plus", 0),
                            f"{a.get('band3_dp', 0)} / {a.get('band3_dp_plus1', 0)} / {a.get('band3_c9', 0)} / {a.get('band3_frozen', 0)}"])
    md += table(["class", "slice", "entrants", "no same-site URL yesterday", "swap n", "swap: landed on swapped slot", "swap: within 1",
                 "swap band3: slot / c9 / frozen", "addition n", "addition: above incumbent", "addition: directly below", "addition: 2+ below",
                 "addition band3: dp / dp+1 / c9 / frozen"], ss_rows)
    sw_all = ent[(ent["ss_status"] == "swap") & (ent["klass"] == "new") & (ent["is_pool"] | (ent["slice"] == EMU))]
    site_rows = []
    q1["swap_by_site_new"] = {}
    for site_ in sw_all["site"].value_counts().head(6).index:
        s2 = sw_all[sw_all["site"] == site_]
        res = {"n": int(len(s2)), "pooled_n": int(s2["is_pool"].sum()), "within1": int(((s2["truth"] - s2["swap_slot"]).abs() <= 1).sum()),
               "band3_slot": int(sum(m.band3_hit(clip(s), t) for s, t in zip(s2["swap_slot"], s2["truth"], strict=True))),
               "band3_c9": int(sum(m.band3_hit(9, t) for t in s2["truth"]))}
        q1["swap_by_site_new"][site_] = res
        site_rows.append([site_, res["n"], res["pooled_n"], res["within1"], res["band3_slot"], res["band3_c9"]])
    shared_slot = sw_all.groupby(["slice", "query", "swap_slot"]).size()
    q1["swap_rows_sharing_a_slot_new"] = int(shared_slot[shared_slot > 1].sum())
    md += ["Swap rows (new class, pooled design plus emulation) by site, top 6:", ""]
    md += table(["site", "swap rows (incl. emulation)", "of which pooled design", "within 1 of swapped slot", "band3 swapped slot", "band3 c9"], site_rows)
    md += [f"New swap rows where two or more entrants of the same site claim the same swapped slot: {q1['swap_rows_sharing_a_slot_new']}.", ""]

    # (c) unstable slot
    md += ["### (c) Lowest unstable position across yesterday's pulls", "",
           "u_top: the best (smallest) position whose occupant differed across yesterday's consensus-session pulls. Exists only where the previous day had two or more pulls: o30 Nov 17 (prev Nov 16).", ""]
    q1["unstable"] = {}
    un_rows = []
    for klass in ("new", "reentrant", "all"):
        sub = ent[ent["u_top"].notna()] if "u_top" in ent else ent.iloc[0:0]
        if klass != "all":
            sub = sub[sub["klass"] == klass]
        if sub.empty:
            continue
        d = sub["truth"] - sub["u_top"]
        res = {"n": int(len(sub)), "slices": sorted(set(sub["slice"])), "land_eq_utop": int((d == 0).sum()), "within1_utop": int((d.abs() <= 1).sum()),
               "land_at_or_below_utop": int((d >= 0).sum()), "land_above_utop": int((d < 0).sum()),
               "u_top_hist": {str(int(k)): int(v) for k, v in sub["u_top"].value_counts().sort_index().items()},
               "band3_utop": int(sum(m.band3_hit(clip(u), t) for u, t in zip(sub["u_top"], sub["truth"], strict=True))),
               "band3_c9": int(sum(m.band3_hit(9, t) for t in sub["truth"])),
               "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(sub["frozen"], sub["truth"], strict=True)))}
        q1["unstable"][klass] = res
        un_rows.append([klass, res["n"], res["land_eq_utop"], res["within1_utop"], res["land_at_or_below_utop"], res["land_above_utop"],
                        f"{res['band3_utop']} / {res['band3_c9']} / {res['band3_frozen']}"])
    md += table(["class", "n", "landed on u_top", "within 1 of u_top", "landed at or below u_top", "landed above u_top", "band3: u_top / c9 / frozen"], un_rows)
    report["q1"] = q1

    # =========================================================== Q2 sibling transfer
    md += ["## Q2 Sibling-search transfer", ""]
    sig_recs = []
    for r in ent.itertuples(index=False):
        sl = slices[r.slice]
        rec = {}
        for window in ("prev", "3d"):
            s = sibling_signals(sl.sib, r.query, r.prev_day, r.day, r.key, r.site, window)
            rec.update({f"{window}_{k}": v for k, v in s.items()})
        sig_recs.append(rec)
    sig = pd.DataFrame.from_records(sig_recs)
    ent = pd.concat([ent.reset_index(drop=True), sig], axis=1)
    differ = int((ent["prev_url_n"] != ent["3d_url_n"]).sum() + (ent["prev_site_n"] != ent["3d_site_n"]).sum())
    report["checks"]["sibling_windows_differ_rows"] = differ
    W = "3d"
    url_cols = {"raw_min": f"{W}_raw_min", "raw_maxJ": f"{W}_raw_maxJ", "raw_wJ": f"{W}_raw_wJ", "rel_mid": f"{W}_rel_mid", "rel_ins": f"{W}_rel_ins"}
    site_cols = {"site_min": f"{W}_site_min", "site_wJ": f"{W}_site_wJ", "site_rel_mid": f"{W}_site_rel_mid"}
    for name, col in {**url_cols, **site_cols}.items():
        ent[f"p_{name}"] = [clip(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None for v in ent[col]]
    ent["has_url"] = ent[f"{W}_url_n"] > 0
    ent["has_urlJ"] = ent[f"{W}_url_nJ"] > 0
    ent["has_site"] = ent[f"{W}_site_n"] > 0
    ent["site_only"] = ent["has_site"] & ~ent["has_url"]

    q2: dict = {"window_note": "prev and 3d windows give identical sibling sets on every design slice" if differ == 0 else f"windows differ on {differ} row-fields"}
    # coverage
    cov_rows = []
    q2["coverage"] = {}
    for klass in ("new", "reentrant"):
        for scope in POOL + [EMU, "pooled"]:
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[sub["klass"] == klass]
            if sub.empty:
                continue
            res = {"n": int(len(sub)), "url": int(sub["has_url"].sum()), "url_J>0": int(sub["has_urlJ"].sum()),
                   "url_rel": int(sub["p_rel_mid"].notna().sum()), "site_only": int(sub["site_only"].sum()),
                   "site_only_and_dp": int((sub["site_only"] & (sub["dp"] <= 10)).sum()),
                   "none": int((~sub["has_url"] & ~sub["has_site"]).sum())}
            q2["coverage"][f"{klass}|{scope}"] = res
            cov_rows.append([klass, scope, res["n"], res["url"], res["url_J>0"], res["url_rel"], res["site_only"], res["site_only_and_dp"], res["none"]])
    md += [f"Sibling evidence window: {q2['window_note']}. Evidence = the URL (or its site) in another tracked search's consensus top 10 on that search's latest history day in the window.", ""]
    md += ["### Coverage", ""]
    md += table(["class", "slice", "rows", "URL in a sibling top 10", "with overlap J > 0", "relative placement possible",
                 "site only (URL not)", "site only and site already on this search", "no evidence"], cov_rows)

    # scores on rows with URL evidence
    def score_block(sub: pd.DataFrame, signals: list[str]) -> dict:
        return compare(sub, signals)

    sig_names = ["p_raw_min", "p_raw_maxJ", "p_raw_wJ", "p_rel_mid", "p_rel_ins"]
    q2["url_scores"] = {}
    score_rows = []
    for klass in ("new", "reentrant"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[(sub["klass"] == klass) & sub["has_url"]]
            # raw_min on every URL-evidence row; J-based signals on the subset where they exist
            res_all = score_block(sub, ["p_raw_min"]) if len(sub) else {"n": 0}
            subJ = sub[sub["p_rel_mid"].notna() & sub["p_raw_wJ"].notna()]
            res_j = score_block(subJ, ["p_raw_maxJ", "p_raw_wJ", "p_rel_mid", "p_rel_ins"]) if len(subJ) else {"n": 0}
            q2["url_scores"][f"{klass}|{scope}|all_url_rows"] = res_all
            q2["url_scores"][f"{klass}|{scope}|J_rows"] = res_j
            for label, res, sigs in (("URL rows", res_all, ["p_raw_min"]), ("URL rows with J>0", res_j, ["p_raw_maxJ", "p_raw_wJ", "p_rel_mid", "p_rel_ins"])):
                if not res.get("n"):
                    continue
                for metric in METRICS:
                    b = res[metric]
                    for s in sigs:
                        score_rows.append([klass, scope, label, metric, res["n"], s[2:], fr(b[s]), fr(b["c9"]), fr(b["c7"]), fr(b["dp"]), fr(b["frozen"]),
                                           fd(b[f"{s} - c9"]), fd(b[f"{s} - frozen"])])
    md += ["### Scores where the URL has sibling evidence (PREDICTED transfers, clipped to [2, 9])", ""]
    md += table(["class", "slice", "rows", "metric", "n", "signal", "signal", "c9", "c7", "domain prior", "frozen", "signal - c9", "signal - frozen"], score_rows)

    # how well does the sibling position predict landing (raw, unclipped)
    q2["raw_fit"] = {}
    fit_rows = []
    for klass in ("new", "reentrant"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[(sub["klass"] == klass) & sub["has_url"]]
            if len(sub) < 3:
                continue
            for col in ("3d_raw_min", "3d_raw_maxJ"):
                x = sub[col].astype(float)
                rho = spearmanr(x, sub["truth"]).statistic
                res = {"n": int(len(sub)), "spearman": float(rho), "mae": float((x - sub["truth"]).abs().mean()),
                       "exact": int((x.round() == sub["truth"]).sum()), "within1": int(((x - sub["truth"]).abs() <= 1).sum()),
                       "median_truth_minus_sib": float((sub["truth"] - x).median())}
                q2["raw_fit"][f"{klass}|{scope}|{col}"] = res
                fit_rows.append([klass, scope, col[3:], res["n"], f"{res['spearman']:.2f}", f"{res['mae']:.2f}", res["exact"], res["within1"], res["median_truth_minus_sib"]])
    md += ["### Raw fit of the sibling position (unclipped)", ""]
    md += table(["class", "slice", "sibling position", "n", "Spearman", "mean abs error", "exact", "within 1", "median (landing - sibling)"], fit_rows)
    # crosstab
    sub = ent[ent["is_pool"] & ent["has_url"]]
    bucket = lambda v: "1-3" if v <= 3 else ("4-6" if v <= 6 else "7-10")  # noqa: E731
    ct = Counter((bucket(a), bucket(b)) for a, b in zip(sub["3d_raw_maxJ"], sub["truth"], strict=True))
    q2["crosstab_maxJ_vs_landing_pooled_entrants"] = {f"{a}->{b}": int(ct.get((a, b), 0)) for a in ("1-3", "4-6", "7-10") for b in ("1-3", "4-6", "7-10")}
    md += ["### Sibling position (max-overlap sibling) versus landing, pooled design entrants with URL evidence", ""]
    md += table(["sibling position", "landed 1-3", "landed 4-6", "landed 7-10"],
                [[a] + [ct.get((a, b), 0) for b in ("1-3", "4-6", "7-10")] for a in ("1-3", "4-6", "7-10")])
    # J buckets
    q2["by_J"] = {}
    j_rows = []
    sub = ent[ent["has_url"] & (ent["is_pool"] | (ent["slice"] == EMU))]
    for lo_, hi_, lab in ((-0.01, 0.0, "J = 0"), (0.0, 0.25, "0 < J <= 0.25"), (0.25, 0.5, "0.25 < J <= 0.5"), (0.5, 1.01, "J > 0.5")):
        for scope in ("pooled", EMU):
            s2 = sub[sub["is_pool"]] if scope == "pooled" else sub[sub["slice"] == EMU]
            s2 = s2[(s2["3d_maxJ"] > lo_) & (s2["3d_maxJ"] <= hi_)]
            if s2.empty:
                continue
            res = {"n": int(len(s2)), "mae_maxJ": float((s2["3d_raw_maxJ"] - s2["truth"]).abs().mean()),
                   "band3_maxJ": int(sum(m.band3_hit(clip(v), t) for v, t in zip(s2["3d_raw_maxJ"], s2["truth"], strict=True))),
                   "band3_c9": int(sum(m.band3_hit(9, t) for t in s2["truth"])),
                   "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(s2["frozen"], s2["truth"], strict=True)))}
            q2["by_J"][f"{lab}|{scope}"] = res
            j_rows.append([lab, scope, res["n"], f"{res['mae_maxJ']:.2f}", res["band3_maxJ"], res["band3_c9"], res["band3_frozen"]])
    md += ["### Does more list overlap make the transfer better? (entrants, both classes)", ""]
    md += table(["max overlap J", "slice", "n", "mean abs error of sibling position", "band3 sibling", "band3 c9", "band3 frozen"], j_rows)
    # sibling group of the max-J sibling, Nov 17 o30 and emulation
    has = ent[ent["has_url"]]
    grp_ct = Counter(zip(has["slice"], has[f"{W}_maxJ_grp"], strict=True))
    q2["maxJ_sibling_group"] = {f"{a}|{b}": int(v) for (a, b), v in sorted(grp_ct.items())}
    md += ["Group of the max-overlap sibling that carries the URL (entrants with URL evidence): "
           + "; ".join(f"{a}: {b} {v}" for (a, b), v in sorted(grp_ct.items())) + ".", ""]

    # site-only rows
    q2["site_scores"] = {}
    site_rows = []
    for klass in ("new", "reentrant"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[(sub["klass"] == klass) & sub["site_only"]]
            if sub.empty:
                continue
            res = compare(sub, ["p_site_min"])
            q2["site_scores"][f"{klass}|{scope}|site_only"] = res
            subJ = sub[sub["p_site_wJ"].notna() & sub["p_site_rel_mid"].notna()]
            resJ = compare(subJ, ["p_site_wJ", "p_site_rel_mid"]) if len(subJ) else {"n": 0}
            q2["site_scores"][f"{klass}|{scope}|site_only_J"] = resJ
            for label, rr, sigs in (("site-only rows", res, ["p_site_min"]), ("site-only rows with J>0", resJ, ["p_site_wJ", "p_site_rel_mid"])):
                if not rr.get("n"):
                    continue
                for metric in METRICS:
                    b = rr[metric]
                    for s in sigs:
                        site_rows.append([klass, scope, label, metric, rr["n"], s[2:], fr(b[s]), fr(b["c9"]), fr(b["dp"]), fr(b["frozen"]),
                                          fd(b[f"{s} - c9"]), fd(b[f"{s} - frozen"])])
    md += ["### Site-level transfer: the site ranks in sibling searches but this URL does not (PREDICTED)", ""]
    md += table(["class", "slice", "rows", "metric", "n", "signal", "signal", "c9", "domain prior", "frozen", "signal - c9", "signal - frozen"], site_rows)

    # incremental: rows where the frozen rule has no anchor and falls back to its default
    ent["frozen_default"] = np.where(ent["klass"] == "new", (ent["dp"] > 10) & (ent["deep_site"] > 30),
                                     (ent["deep_key"] > 30) & (ent["last_top_pos"] > 10))
    q2["incremental"] = {}
    inc_rows = []
    for klass in ("new", "reentrant"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            sub = sub[(sub["klass"] == klass) & sub["frozen_default"]]
            res = {"default_rows": int(len(sub)), "with_url": int(sub["has_url"].sum()), "with_site_only": int(sub["site_only"].sum())}
            for label, cond, sigs in (("URL evidence", sub["has_url"], ["p_raw_min", "p_raw_maxJ"]), ("site-only evidence", sub["site_only"], ["p_site_min"])):
                s2 = sub[cond]
                if s2.empty:
                    continue
                rr = compare(s2, sigs)
                res[label] = rr
                for metric in METRICS:
                    b = rr[metric]
                    for s in sigs:
                        inc_rows.append([klass, scope, label, metric, rr["n"], s[2:], fr(b[s]), fr(b["frozen"]), fr(b["c9"]), fd(b[f"{s} - frozen"])])
            q2["incremental"][f"{klass}|{scope}"] = res
    md += ["### Incremental test: rows where the frozen rule has no site or deep anchor and falls back to its default (9 new, 7 reentrant)", ""]
    md += [f"Rows on the default: " + "; ".join(f"{k}: {v['default_rows']} (URL evidence {v['with_url']}, site only {v['with_site_only']})" for k, v in q2["incremental"].items()), ""]
    md += table(["class", "slice", "rows", "metric", "n", "signal", "signal", "frozen (its default)", "c9", "signal - frozen"], inc_rows)

    # cold-start supplement: a70 and p35 first pulls on Nov 16, evidence from o30 history (Nov 13)
    cs_rows = rows[rows["day"] <= "2026-11-16"]
    cs_cons = cons[cons["day"] <= "2026-11-16"]
    hist_cons = cs_cons[cs_cons["day"] < "2026-11-16"]
    sib_idx = SibIndex(hist_cons, groups)
    target = cs_cons[(cs_cons["day"] == "2026-11-16") & (cs_cons["cons"] <= 10) & cs_cons["query"].map(groups).isin(["a70", "p35"])]
    cs_recs = []
    for r in target.itertuples(index=False):
        sibs = sib_idx.siblings(r.query, "2026-11-13", "2026-11-16", "3d")
        ev = [s for s in sibs if r.key in s["ranks"]]
        qt = set(query_terms(r.query))
        rec = {"query": r.query, "grp": groups[r.query], "truth": int(r.cons), "url_n": len(ev)}
        if ev:
            pos = [s["ranks"][r.key] for s in ev]
            rec["raw_min"] = min(pos)
            rec["raw_med"] = float(np.median(pos))
            sims = [len(qt & set(query_terms(s["query"]))) / len(qt | set(query_terms(s["query"]))) for s in ev]
            best = max(sims)
            rec["raw_bestq"] = float(np.median([p for p, sm in zip(pos, sims, strict=True) if sm == best]))
        cs_recs.append(rec)
    cs = pd.DataFrame.from_records(cs_recs)
    assert set(hist_cons["day"]) <= {"2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13"}
    q2["cold_start"] = {"targets": int(len(cs)), "with_url_evidence": int((cs["url_n"] > 0).sum())}
    ev = cs[cs["url_n"] > 0].copy()
    for col in ("raw_min", "raw_med", "raw_bestq"):
        ev[f"p_{col}"] = [clip(v) for v in ev[col]]
    ev["c9"] = 9
    ev["c7"] = 7
    ev["c5"] = 5
    res = compare(ev, ["p_raw_min", "p_raw_med", "p_raw_bestq"], refs=("c9", "c7", "c5"), diffs=("c5", "c7"))
    for col in ("raw_min", "raw_med", "raw_bestq"):
        res[f"spearman_{col}"] = float(spearmanr(ev[col], ev["truth"]).statistic)
        res[f"mae_{col}"] = float((ev[col] - ev["truth"]).abs().mean())
    res["mae_c5"] = float((5 - ev["truth"]).abs().mean())
    q2["cold_start"]["scores"] = res
    md += ["### Supplement: cold start on the a70 and p35 searches' first pull (Nov 16), evidence from o30 top 10 on Nov 13", "",
           f"Every top-10 row of the first pull (these are mostly incumbents, not entrants, so constant 5 is the fair flat reference). "
           f"{q2['cold_start']['with_url_evidence']} of {q2['cold_start']['targets']} rows have URL evidence. "
           f"Spearman with the truth: best sibling position {res['spearman_raw_min']:.2f}, median sibling position {res['spearman_raw_med']:.2f}, "
           f"most similar query wording {res['spearman_raw_bestq']:.2f}. Mean abs error: {res['mae_raw_min']:.2f}, {res['mae_raw_med']:.2f}, "
           f"{res['mae_raw_bestq']:.2f}; constant 5: {res['mae_c5']:.2f}.", ""]
    cs_tab = []
    for metric in METRICS:
        b = res[metric]
        for s in ("p_raw_min", "p_raw_med", "p_raw_bestq"):
            cs_tab.append([metric, res["n"], s[2:], fr(b[s]), fr(b["c5"]), fr(b["c7"]), fr(b["c9"]), fd(b[f"{s} - c5"]), fd(b[f"{s} - c7"])])
    md += table(["metric", "n", "signal", "signal", "c5", "c7", "c9", "signal - c5", "signal - c7"], cs_tab)
    report["q2"] = q2

    # =========================================================== Q3 top-3 entrants
    md += ["## Q3 Entrants landing in the top 3 or top 5", "",
           "Signals were fixed in code before any of these tables was printed. Title: the latest stored SERP title of this URL "
           "on any search before the outcome day (history only).", ""]
    titles = {}
    for name, sl in slices.items():
        h = sl.history.sort_values("fetched_at")
        titles[name] = dict(zip(h["key"], h["title"], strict=True))
    ent["sig_brand"] = [brand_in_query(q, s) for q, s in zip(ent["query"], ent["site"], strict=True)]
    ent["sig_site_top3_here"] = ent["dp"] <= 3
    ent["sig_url_top3_sibling"] = ent[f"{W}_url_top3"].fillna(False).astype(bool)
    ent["sig_slug_all_terms"] = [all_terms_in(q, url_slug(u)) for q, u in zip(ent["query"], ent["url"], strict=True)]
    hist_titles = [titles[sl_].get(k) for sl_, k in zip(ent["slice"], ent["key"], strict=True)]
    hist_titles = [t if isinstance(t, str) and t.strip() else None for t in hist_titles]
    ent["title_hist"] = pd.Series(hist_titles, dtype=object)
    ent["sig_title_all_terms"] = pd.Series([all_terms_in(q, t) if isinstance(t, str) else None for q, t in zip(ent["query"], hist_titles, strict=True)], dtype=object)
    assert int(ent["sig_title_all_terms"].notna().sum()) == sum(isinstance(t, str) for t in hist_titles)
    ent["sig_platform"] = ent["site"].isin(PLATFORMS)
    ent["top3"] = ent["truth"] <= 3
    ent["top5"] = ent["truth"] <= 5
    signals = ["sig_brand", "sig_site_top3_here", "sig_url_top3_sibling", "sig_slug_all_terms", "sig_title_all_terms", "sig_platform"]
    q3: dict = {"tables": {}, "per_slice": {}}
    t3_rows = []
    for klass in ("new", "all"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            if klass != "all":
                sub = sub[sub["klass"] == klass]
            base3 = int(sub["top3"].sum())
            base5 = int(sub["top5"].sum())
            q3["tables"][f"{klass}|{scope}|base"] = {"n": int(len(sub)), "top3": base3, "top5": base5}
            for s in signals:
                for outcome in ("top3", "top5"):
                    res = two_by_two(sub, s, outcome)
                    q3["tables"][f"{klass}|{scope}|{s}|{outcome}"] = res
                    n_sig = res["sig_hit"] + res["sig_miss"]
                    n_no = res["nosig_hit"] + res["nosig_miss"]
                    t3_rows.append([klass, scope, s[4:], outcome, f"{res['sig_hit']}/{n_sig}", f"{res['nosig_hit']}/{n_no}",
                                    "n/a" if res["fisher_p"] is None else f"{res['fisher_p']:.3f}"])
    md += table(["class", "slice", "signal", "outcome", "signal yes: hits/n", "signal no: hits/n", "Fisher p"], t3_rows)
    # per slice counts
    ps_rows = []
    for s in signals:
        for scope in POOL + [EMU]:
            sub = ent[ent["slice"] == scope]
            sub = sub[sub[s].notna()]
            yes = sub[sub[s].astype(bool)]
            no = sub[~sub[s].astype(bool)]
            res = {"yes_n": int(len(yes)), "yes_top3": int(yes["top3"].sum()), "no_n": int(len(no)), "no_top3": int(no["top3"].sum())}
            q3["per_slice"][f"{s}|{scope}"] = res
            ps_rows.append([s[4:], scope, f"{res['yes_top3']}/{res['yes_n']}", f"{res['no_top3']}/{res['no_n']}"])
    md += ["### Per slice, all entrants: top-3 landings with and without each signal", ""]
    md += table(["signal", "slice", "signal yes: top3/n", "signal no: top3/n"], ps_rows)
    # descriptive (not pre-stated): how much of the top-3 landings the two site/URL signals cover, and the landing spread on signal rows
    md += ["### Descriptive, added after the pre-stated tables: coverage of top-3 landings and landing spread on signal rows", ""]
    q3["coverage_and_spread"] = {}
    cov3_rows = []
    for klass in ("new", "all"):
        for scope in ("pooled", EMU):
            sub = ent[ent["is_pool"]] if scope == "pooled" else ent[ent["slice"] == scope]
            if klass != "all":
                sub = sub[sub["klass"] == klass]
            either = sub["sig_site_top3_here"] | sub["sig_url_top3_sibling"]
            for label, mask in (("site_top3_here", sub["sig_site_top3_here"]), ("url_top3_sibling", sub["sig_url_top3_sibling"]), ("either", either)):
                s2 = sub[mask]
                res = {"rows": int(len(s2)), "top3_total": int(sub["top3"].sum()), "top3_covered": int(s2["top3"].sum()),
                       "truth1_3": int((s2["truth"] <= 3).sum()), "truth4_7": int(((s2["truth"] >= 4) & (s2["truth"] <= 7)).sum()),
                       "truth8_10": int((s2["truth"] >= 8).sum()),
                       "err3_c4": int(sum(m.err3_hit(4, t) for t in s2["truth"])), "err3_c7": int(sum(m.err3_hit(7, t) for t in s2["truth"])),
                       "band3_c2": int(sum(m.band3_hit(2, t) for t in s2["truth"])), "band3_c9": int(sum(m.band3_hit(9, t) for t in s2["truth"]))}
                q3["coverage_and_spread"][f"{klass}|{scope}|{label}"] = res
                cov3_rows.append([klass, scope, label, res["rows"], f"{res['top3_covered']}/{res['top3_total']}", res["truth1_3"], res["truth4_7"], res["truth8_10"],
                                  f"{res['err3_c4']} / {res['err3_c7']}", f"{res['band3_c2']} / {res['band3_c9']}"])
    md += table(["class", "slice", "signal", "rows with signal", "top-3 landings covered / all top-3 landings", "landed 1-3", "landed 4-7", "landed 8-10",
                 "err3 on signal rows: predict 4 / predict 7", "band3 on signal rows: predict 2 / predict 9"], cov3_rows)
    top3_list = ent[ent["top3"] & (ent["is_pool"] | (ent["slice"] == EMU))][
        ["slice", "klass", "query", "url", "truth", "dp", "frozen", f"{W}_raw_min", "sig_brand", "sig_slug_all_terms", "sig_title_all_terms", "sig_platform"]]
    q3["top3_entrants"] = top3_list.to_dict("records")
    q3["brand_matches"] = sorted({(q, s) for q, s, b in zip(ent["query"], ent["site"], ent["sig_brand"], strict=True) if b})
    q3["title_coverage"] = {"entrants": int(len(ent)), "with_history_title": int(ent["title_hist"].notna().sum())}
    report["q3"] = q3

    # =========================================================== Q4 gap and truth type
    md += ["## Q4 Gap between pulled days and truth type", ""]
    q4: dict = {"natural": {}, "cut": [], "single_vs_three": {}}
    nat_rows = []
    for name, _, day, grp in SLICES:
        sl = slices[name]
        sub = ent[ent["slice"] == name]
        gap = sorted(set(sl.placed["gap_days"].astype(int)))
        pulls = sorted(set(sl.placed["n_pulls"].astype(int)))
        block = {"gap": gap, "truth_pulls": pulls, "queries": int(sl.placed["query"].nunique())}
        for klass in ("new", "reentrant"):
            s2 = sub[sub["klass"] == klass]
            prof = landing_profile(s2["truth"])
            if len(s2):
                prof["band3_frozen"] = int(sum(m.band3_hit(p, t) for p, t in zip(s2["frozen"], s2["truth"], strict=True)))
            block[klass] = prof
        q4["natural"][name] = block
        nb = block["new"]
        rb = block["reentrant"]
        nat_rows.append([name, gap, pulls, block["queries"], nb["n"], f"{nb['n'] / block['queries']:.2f}" if nb["n"] else "0",
                         f"{nb.get('mean', float('nan')):.2f}", nb.get("top3", 0), nb.get("pos4_7", 0), nb.get("pos8_10", 0),
                         nb.get("band3_c9", 0), nb.get("band3_frozen", 0), nb.get("err3_c7", 0), rb["n"], f"{rb.get('mean', float('nan')):.2f}" if rb["n"] else "-"])
    md += ["### Natural design slices (new rows unless marked)", ""]
    md += table(["slice", "gap days", "truth pulls", "queries", "new", "new per search", "new mean landing", "new 1-3", "new 4-7", "new 8-10",
                 "band3 c9", "band3 frozen", "err3 c7", "reentrant", "reentrant mean landing"], nat_rows)
    cut_specs = [
        ("2026-11-17", "2026-11-16"), ("2026-11-17", "2026-11-13"), ("2026-11-17", "2026-11-11"),
        ("2026-11-16", "2026-11-13"), ("2026-11-16", "2026-11-11"), ("2026-11-16", "2026-11-09"),
        ("2026-11-13", "2026-11-11"), ("2026-11-13", "2026-11-09"),
        ("2026-11-11", "2026-11-09"), ("2026-11-11", "2026-11-06"),
    ]
    cut_rows = []
    for day, cut in cut_specs:
        res = cut_case(rows, cons, day, cut, "o30", groups, f"o30 {day} from {cut}")
        q4["cut"].append(res)
        nb, rb = res["new"], res["reentrant"]
        ent_n = nb["n"] + rb["n"]
        cut_rows.append([day, cut, res["gap"], res["queries"], res["has_deep_in_history"], nb["n"], rb["n"], f"{ent_n / res['queries']:.2f}",
                         f"{nb.get('mean', float('nan')):.2f}", nb.get("top3", 0), nb.get("pos8_10", 0), nb.get("band3_c9", 0), nb.get("band3_frozen", 0),
                         nb.get("err3_c7", 0), f"{nb.get('dp_rows_band3_dp', 0)} / {nb.get('dp_rows_band3_c9', 0)} of {nb.get('dp_available', 0)}",
                         f"{nb.get('swap_within1', 0)}/{nb.get('swap_n', 0)}",
                         f"{rb.get('mean', float('nan')):.2f}" if rb["n"] else "-", rb.get("band3_c9", 0), rb.get("band3_frozen", 0)])
    md += ["### Same outcome day, history cut at an earlier day (skip-day emulation, o30)", "",
           "Removes every pulled day between the cut and the outcome, as the a70 searches will have no pulls between Nov 16 and Nov 19.", ""]
    md += table(["outcome", "history cut", "gap", "queries", "deep list in history", "new", "reentrant", "entrants per search", "new mean landing", "new 1-3",
                 "new 8-10", "new band3 c9", "new band3 frozen", "new err3 c7", "new rows with site on page one at cut: band3 dp / c9",
                 "new swap: within 1 of swapped slot", "reentrant mean", "reentrant band3 c9", "reentrant band3 frozen"], cut_rows)
    sv_rows = []
    for day in ("2026-11-16", "2026-11-17"):
        res = single_pull_truth(rows, day, "o30", groups)
        q4["single_vs_three"][day] = res
        three = q4["natural"]["o30 Nov 16" if day == "2026-11-16" else "o30 Nov 17"]
        sv_rows.append([day, "three-pull consensus", three["new"]["n"], f"{three['new'].get('mean', float('nan')):.2f}", three["new"].get("top3", 0),
                        three["new"].get("pos8_10", 0), three["new"].get("band3_c9", 0), three["reentrant"]["n"],
                        f"{three['reentrant'].get('mean', float('nan')):.2f}"])
        for source, block in res["by_source"].items():
            nb, rb = block["new"], block["reentrant"]
            sv_rows.append([day, f"single pull {source}", nb["n"], f"{nb.get('mean', float('nan')):.2f}" if nb["n"] else "-", nb.get("top3", 0),
                            nb.get("pos8_10", 0), nb.get("band3_c9", 0), rb["n"], f"{rb.get('mean', float('nan')):.2f}" if rb["n"] else "-"])
    md += ["### Same day, single-pull truth versus three-pull truth (o30)", ""]
    md += table(["outcome", "truth", "new", "new mean landing", "new 1-3", "new 8-10", "new band3 c9", "reentrant", "reentrant mean landing"], sv_rows)
    report["q4"] = q4

    # =========================================================== Q5 reentrants
    md += ["## Q5 Reentrant anchors", "",
           "Anchors: deep = position in the latest deep list (<= 30); last_top = last consensus top-10 position on this search; "
           "last_pull = position in the most recent top-10 pull that showed it (any source); flicker = most recent pull that showed it on a day "
           "its consensus was outside the top 10; best_ever = best position in any pull. PREDICTED columns clip the anchor to [2, 9].", ""]
    q5: dict = {"anchors": {}, "head_to_head": {}}
    re_all = ent[(ent["klass"] == "reentrant")]
    anchors = ["a_deep", "a_last_top", "a_last_pull", "a_flicker", "a_best_ever"]
    a_rows = []
    for scope in POOL + [EMU, "pooled"]:
        sub = re_all[re_all["is_pool"]] if scope == "pooled" else re_all[re_all["slice"] == scope]
        if sub.empty:
            continue
        for a in anchors:
            s2 = sub[sub[a].notna()]
            if s2.empty:
                continue
            x = s2[a].astype(float)
            res = {"n": int(len(s2)), "of": int(len(sub)), "exact": int((x == s2["truth"]).sum()), "within1": int(((x - s2["truth"]).abs() <= 1).sum()),
                   "band3": int(sum(m.band3_hit(clip(v), t) for v, t in zip(x, s2["truth"], strict=True))),
                   "err3": int(sum(m.err3_hit(clip(v), t) for v, t in zip(x, s2["truth"], strict=True))),
                   "band3_c9": int(sum(m.band3_hit(9, t) for t in s2["truth"])),
                   "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(s2["frozen"], s2["truth"], strict=True))),
                   "mae": float((x - s2["truth"]).abs().mean()), "median_truth_minus_anchor": float((s2["truth"] - x).median())}
            q5["anchors"][f"{scope}|{a}"] = res
            a_rows.append([scope, a[2:], f"{res['n']}/{res['of']}", res["exact"], res["within1"], res["band3"], res["err3"], res["band3_c9"], res["band3_frozen"],
                           f"{res['mae']:.2f}", res["median_truth_minus_anchor"]])
    md += table(["slice", "anchor", "rows with anchor", "exact", "within 1", "band3", "err3", "band3 c9 (same rows)", "band3 frozen (same rows)", "mean abs error",
                 "median (landing - anchor)"], a_rows)
    h_rows = []
    for scope in ("pooled", EMU, "o30 Nov 17"):
        sub = re_all[re_all["is_pool"]] if scope == "pooled" else re_all[re_all["slice"] == scope]
        for pair in (("a_deep", "a_last_top"), ("a_deep", "a_last_pull"), ("a_last_pull", "a_last_top"), ("a_flicker", "a_last_top")):
            s2 = sub[sub[pair[0]].notna() & sub[pair[1]].notna()]
            if s2.empty:
                continue
            e0 = (s2[pair[0]].astype(float) - s2["truth"]).abs()
            e1 = (s2[pair[1]].astype(float) - s2["truth"]).abs()
            res = {"n": int(len(s2)), "first_closer": int((e0 < e1).sum()), "tie": int((e0 == e1).sum()), "second_closer": int((e0 > e1).sum()),
                   "same_value": int((s2[pair[0]].astype(float) == s2[pair[1]].astype(float)).sum())}
            q5["head_to_head"][f"{scope}|{pair[0]}|{pair[1]}"] = res
            h_rows.append([scope, f"{pair[0][2:]} vs {pair[1][2:]}", res["n"], res["first_closer"], res["tie"], res["second_closer"], res["same_value"]])
    md += ["### Head to head on rows where both anchors exist", ""]
    md += table(["slice", "pair", "n", "first closer", "tie", "second closer", "same value"], h_rows)
    # last pull recency
    rec_rows = []
    sub = re_all[re_all["is_pool"] | (re_all["slice"] == EMU)].copy()
    sub["last_pull_gap"] = [rs.calendar_gap(d, day) if d else None for d, day in zip(sub["last_pull_day"], sub["day"], strict=True)]
    for (scope, gap_), s2 in sub.groupby(["slice", "last_pull_gap"]):
        x = s2["a_last_pull"].astype(float)
        rec_rows.append([scope, int(gap_), int(len(s2)), f"{(x - s2['truth']).abs().mean():.2f}", int(sum(m.band3_hit(clip(v), t) for v, t in zip(x, s2["truth"], strict=True)))])
    q5["last_pull_by_recency"] = rec_rows
    md += ["### last_pull anchor by days since that pull", ""]
    md += table(["slice", "days since last pull that showed it", "n", "mean abs error", "band3"], rec_rows)
    report["q5"] = q5

    # =========================================================== Q6 stability
    md += ["## Q6 Per-slice stability of the candidate signals", ""]
    q6: dict = {}
    st_rows = []
    for scope in POOL + [EMU]:
        sub = ent[ent["slice"] == scope]
        new = sub[sub["klass"] == "new"]
        url = new[new["has_url"] & new["p_rel_mid"].notna() & new["p_raw_wJ"].notna()]
        sw = new[new["ss_status"] == "swap"]
        ad = new[new["ss_status"] == "addition"]
        vac = q1["vacated"].get(f"new|{scope}", {}).get("within1", {})
        res = {"new": int(len(new)), "band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(new["frozen"], new["truth"], strict=True))),
               "band3_c9": int(sum(m.band3_hit(9, t) for t in new["truth"])),
               "url_rows": int(new["has_url"].sum()), "urlJ_rows": int(len(url)),
               "url_band3_rel_mid": int(sum(m.band3_hit(p, t) for p, t in zip(url["p_rel_mid"], url["truth"], strict=True))),
               "url_band3_wJ": int(sum(m.band3_hit(p, t) for p, t in zip(url["p_raw_wJ"], url["truth"], strict=True))),
               "url_band3_c9": int(sum(m.band3_hit(9, t) for t in url["truth"])),
               "url_band3_frozen": int(sum(m.band3_hit(p, t) for p, t in zip(url["frozen"], url["truth"], strict=True))),
               "swap": int(len(sw)), "swap_within1": int(((sw["truth"] - sw["swap_slot"]).abs() <= 1).sum()) if len(sw) else 0,
               "addition": int(len(ad)), "addition_below": int(((ad["truth"] - ad["inc_now"]) > 0).sum()) if len(ad) else 0,
               "vac_within1_obs": vac.get("observed"), "vac_within1_null": vac.get("null_mean")}
        dp_rows = new[new["dp"] <= 10]
        res["dp_rows"] = int(len(dp_rows))
        res["dp_rows_band3_dp"] = int(sum(m.band3_hit(clip(p), t) for p, t in zip(dp_rows["dp"], dp_rows["truth"], strict=True)))
        res["dp_rows_band3_c9"] = int(sum(m.band3_hit(9, t) for t in dp_rows["truth"]))
        q6[scope] = res
        st_rows.append([scope, res["new"], f"{res['band3_frozen']} / {res['band3_c9']}",
                        f"{res['dp_rows_band3_dp']} / {res['dp_rows_band3_c9']} of {res['dp_rows']}", res["url_rows"], res["urlJ_rows"],
                        f"{res['url_band3_rel_mid']} / {res['url_band3_wJ']} / {res['url_band3_c9']} / {res['url_band3_frozen']}",
                        f"{res['swap_within1']}/{res['swap']}", f"{res['addition_below']}/{res['addition']}",
                        "-" if res["vac_within1_obs"] is None else f"{res['vac_within1_obs']} vs {res['vac_within1_null']:.1f}"])
    md += ["New rows only. band3 counts.", ""]
    md += table(["slice", "new", "frozen / c9", "site on page one yesterday: dp / c9", "URL sibling rows", "rows with J>0 and relative",
                 "on those: rel_mid / wJ / c9 / frozen",
                 "swap: within 1 of swapped slot", "addition: landed below incumbent", "within 1 of vacated: observed vs null"], st_rows)
    report["q6"] = q6

    # per-entrant table (no titles) for audit
    keep = ["slice", "klass", "query", "url", "site", "truth", "prev_day", "gap_days", "n_pulls", "dp", "frozen", "n_leavers", "vacated", "ss_status",
            "swap_slot", "inc_prev", "inc_now", "u_top", "a_deep", "a_last_top", "a_last_pull", "a_flicker", f"{W}_url_n", f"{W}_raw_min", f"{W}_raw_maxJ",
            f"{W}_maxJ", f"{W}_raw_wJ", f"{W}_rel_mid", f"{W}_site_n", f"{W}_site_min", "sig_brand", "sig_slug_all_terms", "sig_title_all_terms", "sig_platform"]
    keep = [k for k in keep if k in ent.columns]
    report["entrants"] = ent[keep].to_dict("records")

    with open(OUT_DIR / "results.json", "w") as handle:
        json.dump(jsonable(report), handle, indent=1, sort_keys=True, default=str)
    (OUT_DIR / "results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
