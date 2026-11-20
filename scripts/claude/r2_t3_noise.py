"""Round 2 research for Target 3: why do URL pairs swap order between days.

MEASUREMENT ONLY. Nothing here is a predictor that will be frozen or scored. Every
"oracle" below is a bound or a diagnostic, and the ones that read day two say so.

Design data only. Rows come from ``rsdata.load_rows(rsdata.DESIGN_MAX_DATE)`` and the
script asserts that the latest day loaded is 2026-11-17. The SQLite file and the
snapshot folders are never opened directly. No network.

Transition studied: Nov 16 (day one: one morning pull plus the three voting evening
pulls) to Nov 17 (three pulls), original 30 searches. Truth and the pair rule are the
ones in ``rsdata`` (median over the voting session, absent = 11, strict majority for
the top 10; a pair is scored when both URLs are in both top 10s and strictly ordered
on both days).

Intervals: query bootstrap with the draw count and seed of ``scripts/claude/metrics.py``
(2000 draws, seed 20261119, the same index matrix as ``metrics.bootstrap_rate``). Rates
on subsets resample ALL queries of the transition, not only the queries in the subset,
because the subset-only resampling in ``metrics.bootstrap_rate`` is anti-conservative
(see evals/claude/t3-movers/notes.md). The full-table flip rate is checked to equal
``metrics.bootstrap_rate`` exactly.

Run:  python -m scripts.claude.r2_t3_noise
Out:  evals/claude/r2/t3-noise/results.json and stdout.txt
"""

from __future__ import annotations

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")  # many tiny matrix products; threads only add overhead

import itertools  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass
from pathlib import Path

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import optimize, special, stats  # noqa: E402

from scripts.claude import metrics as mt  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402

ABSENT = rs.ABSENT
DAY1 = "2026-11-16"
DAY2 = "2026-11-17"
EARLIER = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13")
OUT_DIR = Path(__file__).resolve().parents[2] / "evals" / "claude" / "r2" / "t3-noise"
MC_DRAWS = 100_000  # Monte Carlo draws of the permutation null (exact per-search enumeration inside)
GRID = 100
Q = (np.arange(GRID) + 0.5) / GRID  # per-pull order probability grid; symmetric, never exactly 0.5
LO = Q < 0.5
N_PULLS = (1, 3, 5, 7, 9, 15, 25, 51)
HISTORY_EXTRA = (0, 1, 3, 5, 6, 9, 12, 15, 18)
COMBOS = list(itertools.combinations(range(6), 3))
OBS_COMBO = COMBOS.index((0, 1, 2))
LINES: list[str] = []


T0 = time.time()


def say(text: object = "") -> None:
    print(text, flush=True)
    LINES.append(str(text))


def tick(label: str) -> None:
    """Progress timing on stderr only (kept out of stdout.txt)."""
    import sys

    print(f"[{time.time() - T0:7.1f} s] {label}", file=sys.stderr, flush=True)


def f3(value, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


# --------------------------------------------------------------------------- data


@dataclass
class Search:
    query: str
    morning: dict
    eve: list
    d2: list
    earlier: dict
    timeline: list  # (label, fetched_at, ranks), time order, every top-10 pull through DAY2


def load():
    if rs.DESIGN_MAX_DATE != "2026-11-17":
        raise AssertionError("design cutoff changed")
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    max_day = str(rows["day"].max())
    if max_day != "2026-11-17":
        raise AssertionError(f"latest day loaded is {max_day}, expected 2026-11-17")
    groups = rs.query_groups(rows)
    pulls = rs.pulls_by_query_day(rows)
    cons = rs.consensus_table(rows)
    if str(cons["day"].max()) > "2026-11-17":
        raise AssertionError("consensus table holds a sealed day")
    site_of = dict(zip(rows["key"], rows["site"], strict=True))
    searches: dict[str, Search] = {}
    for query in sorted(q for q, g in groups.items() if g == "o30"):
        if (query, DAY1) not in pulls or (query, DAY2) not in pulls:
            continue
        day1 = pulls[(query, DAY1)]
        vote1 = rs.consensus_session(day1)
        if len(day1) != 4 or vote1 != day1[1:]:
            raise AssertionError(f"unexpected Nov 16 pulls for {query}")
        day2 = pulls[(query, DAY2)]
        if len(day2) != 3 or rs.consensus_session(day2) != day2:
            raise AssertionError(f"unexpected Nov 17 pulls for {query}")
        earlier, timeline = {}, []
        for day in EARLIER:
            found = pulls.get((query, day), [])
            if len(found) != 1:
                raise AssertionError(f"{query} {day}: {len(found)} pulls")
            earlier[day] = found[0].ranks
            timeline.append((day[5:], found[0].fetched_at, found[0].ranks))
        for label, pull in zip(("11-16m", "11-16e1", "11-16e2", "11-16e3"), day1, strict=True):
            timeline.append((label, pull.fetched_at, pull.ranks))
        for label, pull in zip(("11-17a", "11-17b", "11-17c"), day2, strict=True):
            timeline.append((label, pull.fetched_at, pull.ranks))
        searches[query] = Search(query, day1[0].ranks, [p.ranks for p in vote1], [p.ranks for p in day2], earlier, timeline)
    return rows, cons, site_of, searches


# --------------------------------------------------------------------------- core rules


def cons_of(pulls: list[dict]) -> dict[str, int]:
    keys: set[str] = set()
    for pull in pulls:
        keys.update(pull)
    return {key: rs.consensus_position([pull.get(key, ABSENT) for pull in pulls]) for key in keys}


def scored_pairs(cons_a: dict, cons_b: dict) -> list[tuple]:
    """Same rule as rsdata.flip_pairs: (key_x, key_y, a_x, a_y, b_x, b_y, flipped)."""
    items = sorted(((k, v) for k, v in cons_a.items() if v <= 10 and cons_b.get(k, ABSENT) <= 10), key=lambda t: (t[1], t[0]))
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (kx, ax), (ky, ay) = items[i], items[j]
            if ax == ay:
                continue
            bx, by = cons_b[kx], cons_b[ky]
            if bx == by:
                continue
            out.append((kx, ky, ax, ay, bx, by, bx > by))
    return out


def vote(ranks: dict, kx: str, ky: str) -> int:
    """+1 if x above y in this pull, -1 if y above x, 0 if both absent."""
    rx, ry = ranks.get(kx, ABSENT), ranks.get(ky, ABSENT)
    if rx == ry:
        return 0
    return 1 if rx < ry else -1


def pull_disagreement(a: dict, b: dict) -> tuple[int, int]:
    """URL pairs present in both pulls: (pairs ordered differently, pairs compared)."""
    keys = sorted(set(a) & set(b))
    dis = cmp = 0
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            cmp += 1
            if (a[keys[i]] < a[keys[j]]) != (b[keys[i]] < b[keys[j]]):
                dis += 1
    return dis, cmp


# --------------------------------------------------------------------------- bootstrap


def qboot(sums: pd.DataFrame, stat) -> dict:
    """Query bootstrap of any statistic of per-query column sums (metrics.py draws and seed)."""
    vals = sums.to_numpy(dtype=float)
    size = len(vals)
    rng = np.random.default_rng(mt.BOOT_SEED)
    index = rng.integers(0, size, size=(mt.BOOT_DRAWS, size))
    drawn = vals[index].sum(axis=1)
    names = list(sums.columns)
    with np.errstate(divide="ignore", invalid="ignore"):
        point = float(stat({n: vals[:, i].sum() for i, n in enumerate(names)}))
        arr = np.asarray(stat({n: drawn[:, i] for i, n in enumerate(names)}), dtype=float)
    ok = np.isfinite(arr)
    out = {"value": point if np.isfinite(point) else None, "lo": None, "hi": None, "undefined_draws": int((~ok).sum()), "queries": size}
    if ok.any():
        out["lo"] = float(np.percentile(arr[ok], 2.5))
        out["hi"] = float(np.percentile(arr[ok], 97.5))
    return out


def per_query(frame: pd.DataFrame, cols: list[str], universe: list[str]) -> pd.DataFrame:
    return frame.groupby("query")[cols].sum().reindex(universe, fill_value=0)


def rate_ci(frame: pd.DataFrame, num: str, den: str, universe: list[str]) -> dict:
    sums = per_query(frame, [num, den], universe)
    out = qboot(sums, lambda d: d[num] / d[den])
    out["num"] = int(sums[num].sum())
    out["den"] = int(sums[den].sum())
    return out


def fr(res: dict, digits: int = 3) -> str:
    """'num/den (rate) [lo, hi]'."""
    if res.get("value") is None:
        return f"{res.get('num', 0)}/{res.get('den', 0)} (n/a)"
    lo = "n/a" if res["lo"] is None else f"{res['lo']:.{digits}f}"
    hi = "n/a" if res["hi"] is None else f"{res['hi']:.{digits}f}"
    head = f"{res['num']}/{res['den']} " if "num" in res else ""
    return f"{head}({res['value']:.{digits}f}) [{lo}, {hi}]"


def fv(res: dict, digits: int = 3) -> str:
    if res.get("value") is None:
        return "n/a"
    lo = "n/a" if res["lo"] is None else f"{res['lo']:.{digits}f}"
    hi = "n/a" if res["hi"] is None else f"{res['hi']:.{digits}f}"
    return f"{res['value']:.{digits}f} [{lo}, {hi}]"


# --------------------------------------------------------------------------- Q1


def q1_permutation(searches: dict[str, Search]) -> tuple[dict, pd.DataFrame]:
    queries = sorted(searches)
    nq = len(queries)
    F = np.zeros((nq, 20))
    P = np.zeros((nq, 20))
    WD, WC, CD, CC = (np.zeros((nq, 20)) for _ in range(4))
    for qi, query in enumerate(queries):
        s = searches[query]
        six = s.eve + s.d2
        dis = np.zeros((6, 6))
        cmp = np.zeros((6, 6))
        for i, j in itertools.combinations(range(6), 2):
            d, c = pull_disagreement(six[i], six[j])
            dis[i, j] = dis[j, i] = d
            cmp[i, j] = cmp[j, i] = c
        for si, combo in enumerate(COMBOS):
            rest = tuple(i for i in range(6) if i not in combo)
            pairs = scored_pairs(cons_of([six[i] for i in combo]), cons_of([six[i] for i in rest]))
            P[qi, si] = len(pairs)
            F[qi, si] = sum(1 for p in pairs if p[6])
            within = [pair for group in (combo, rest) for pair in itertools.combinations(group, 2)]
            cross = [(i, j) for i in combo for j in rest]
            WD[qi, si] = sum(dis[i, j] for i, j in within)
            WC[qi, si] = sum(cmp[i, j] for i, j in within)
            CD[qi, si] = sum(dis[i, j] for i, j in cross)
            CC[qi, si] = sum(cmp[i, j] for i, j in cross)
    for si, combo in enumerate(COMBOS):  # swapping the two pseudo-days must not change anything
        sj = COMBOS.index(tuple(i for i in range(6) if i not in combo))
        if not (np.array_equal(F[:, si], F[:, sj]) and np.array_equal(P[:, si], P[:, sj])):
            raise AssertionError("permutation statistic is not symmetric under swapping the pseudo-days")
    obs_f, obs_p = F[:, OBS_COMBO].sum(), P[:, OBS_COMBO].sum()
    if (obs_f, obs_p) != (44, 767):
        raise AssertionError(f"observed split gives {obs_f}/{obs_p}, expected 44/767")

    rng = np.random.default_rng(mt.BOOT_SEED)
    pick = rng.integers(0, 20, size=(MC_DRAWS, nq))
    rows_idx = np.arange(nq)[None, :]
    f_null = F[rows_idx, pick].sum(axis=1)
    p_null = P[rows_idx, pick].sum(axis=1)
    rate_null = f_null / p_null
    pull_obs = CD[:, OBS_COMBO].sum() / CC[:, OBS_COMBO].sum() - WD[:, OBS_COMBO].sum() / WC[:, OBS_COMBO].sum()
    pull_null = CD[rows_idx, pick].sum(1) / CC[rows_idx, pick].sum(1) - WD[rows_idx, pick].sum(1) / WC[rows_idx, pick].sum(1)

    pq = pd.DataFrame(
        {
            "query": queries,
            "obs_f": F[:, OBS_COMBO],
            "obs_p": P[:, OBS_COMBO],
            "null_f": F.mean(axis=1),
            "null_p": P.mean(axis=1),
            "wd": WD[:, OBS_COMBO],
            "wc": WC[:, OBS_COMBO],
            "cd": CD[:, OBS_COMBO],
            "cc": CC[:, OBS_COMBO],
        }
    ).set_index("query")
    out = {
        "searches": nq,
        "splits_per_search": 20,
        "distinct_splits_per_search": 10,
        "observed": {"flips": int(obs_f), "pairs": int(obs_p), "rate": obs_f / obs_p},
        "null_expected": {
            "flips": float(F.mean(axis=1).sum()),
            "pairs": float(P.mean(axis=1).sum()),
            "rate_pooled": float(F.mean(axis=1).sum() / P.mean(axis=1).sum()),
            "rate_mean_over_draws": float(rate_null.mean()),
        },
        "null_distribution_mc": {
            "draws": MC_DRAWS,
            "flips_2.5_97.5": [float(np.percentile(f_null, 2.5)), float(np.percentile(f_null, 97.5))],
            "rate_2.5_97.5": [float(np.percentile(rate_null, 2.5)), float(np.percentile(rate_null, 97.5))],
            "p_null_flips_ge_observed": float((f_null >= obs_f).mean()),
            "p_null_flips_le_observed": float((f_null <= obs_f).mean()),
            "p_null_rate_ge_observed": float((rate_null >= obs_f / obs_p).mean()),
        },
        "query_bootstrap": {
            "observed_rate": qboot(pq[["obs_f", "obs_p"]], lambda d: d["obs_f"] / d["obs_p"]),
            "null_rate": qboot(pq[["null_f", "null_p"]], lambda d: d["null_f"] / d["null_p"]),
            "noise_share_of_flip_count": qboot(pq[["null_f", "obs_f"]], lambda d: d["null_f"] / d["obs_f"]),
            "noise_share_of_flip_rate": qboot(pq[["null_f", "null_p", "obs_f", "obs_p"]], lambda d: (d["null_f"] / d["null_p"]) / (d["obs_f"] / d["obs_p"])),
            "excess_flips": qboot(pq[["null_f", "obs_f"]], lambda d: d["obs_f"] - d["null_f"]),
            "excess_rate": qboot(pq[["null_f", "null_p", "obs_f", "obs_p"]], lambda d: d["obs_f"] / d["obs_p"] - d["null_f"] / d["null_p"]),
        },
        "pull_level": {
            "within_day_disagreement": qboot(pq[["wd", "wc"]], lambda d: d["wd"] / d["wc"]),
            "cross_day_disagreement": qboot(pq[["cd", "cc"]], lambda d: d["cd"] / d["cc"]),
            "cross_minus_within": qboot(pq[["wd", "wc", "cd", "cc"]], lambda d: d["cd"] / d["cc"] - d["wd"] / d["wc"]),
            "observed_cross_minus_within": float(pull_obs),
            "null_cross_minus_within_2.5_97.5": [float(np.percentile(pull_null, 2.5)), float(np.percentile(pull_null, 97.5))],
            "p_null_ge_observed": float((pull_null >= pull_obs).mean()),
            "counts": {k: int(pq[k].sum()) for k in ("wd", "wc", "cd", "cc")},
        },
    }
    return out, pq


def q1_nov16(searches: dict[str, Search]) -> dict:
    """Morning pull versus the evening session, against each pull held out in turn."""
    queries = sorted(searches)
    nq = len(queries)
    F = np.zeros((nq, 4))
    P = np.zeros((nq, 4))
    CD, CC, WD, WC = (np.zeros((nq, 4)) for _ in range(4))
    for qi, query in enumerate(queries):
        s = searches[query]
        four = [s.morning] + s.eve
        dis = np.zeros((4, 4))
        cmp = np.zeros((4, 4))
        for i, j in itertools.combinations(range(4), 2):
            d, c = pull_disagreement(four[i], four[j])
            dis[i, j] = dis[j, i] = d
            cmp[i, j] = cmp[j, i] = c
        for h in range(4):
            others = [i for i in range(4) if i != h]
            pairs = scored_pairs(cons_of([four[h]]), cons_of([four[i] for i in others]))
            P[qi, h] = len(pairs)
            F[qi, h] = sum(1 for p in pairs if p[6])
            CD[qi, h] = sum(dis[h, j] for j in others)
            CC[qi, h] = sum(cmp[h, j] for j in others)
            WD[qi, h] = sum(dis[i, j] for i, j in itertools.combinations(others, 2))
            WC[qi, h] = sum(cmp[i, j] for i, j in itertools.combinations(others, 2))
    rng = np.random.default_rng(mt.BOOT_SEED)
    pick = rng.integers(0, 4, size=(MC_DRAWS, nq))
    rows_idx = np.arange(nq)[None, :]
    f_null = F[rows_idx, pick].sum(1)
    p_null = P[rows_idx, pick].sum(1)
    obs_rate = F[:, 0].sum() / P[:, 0].sum()
    pq = pd.DataFrame(
        {
            "query": queries,
            "obs_f": F[:, 0],
            "obs_p": P[:, 0],
            "eve_f": F[:, 1:].mean(1),
            "eve_p": P[:, 1:].mean(1),
            "null_f": F.mean(1),
            "null_p": P.mean(1),
            "m_dis": CD[:, 0],
            "m_cmp": CC[:, 0],
            "e_dis": WD[:, 0],
            "e_cmp": WC[:, 0],
        }
    ).set_index("query")
    return {
        "observed_morning_vs_evening_consensus": {"flips": int(F[:, 0].sum()), "pairs": int(P[:, 0].sum()), "rate": float(obs_rate)},
        "evening_pull_vs_rest_mean": {"flips": float(F[:, 1:].mean(1).sum()), "pairs": float(P[:, 1:].mean(1).sum())},
        "null_expected_any_pull_held_out": {"flips": float(F.mean(1).sum()), "pairs": float(P.mean(1).sum()), "rate": float(F.mean(1).sum() / P.mean(1).sum())},
        "p_null_flips_ge_observed": float((f_null >= F[:, 0].sum()).mean()),
        "p_null_rate_ge_observed": float((f_null / p_null >= obs_rate).mean()),
        "observed_rate_ci": qboot(pq[["obs_f", "obs_p"]], lambda d: d["obs_f"] / d["obs_p"]),
        "evening_heldout_rate_ci": qboot(pq[["eve_f", "eve_p"]], lambda d: d["eve_f"] / d["eve_p"]),
        "morning_minus_evening_rate": qboot(pq[["obs_f", "obs_p", "eve_f", "eve_p"]], lambda d: d["obs_f"] / d["obs_p"] - d["eve_f"] / d["eve_p"]),
        "noise_share": qboot(pq[["obs_f", "obs_p", "eve_f", "eve_p"]], lambda d: (d["eve_f"] / d["eve_p"]) / (d["obs_f"] / d["obs_p"])),
        "pull_level_morning_vs_evening": qboot(pq[["m_dis", "m_cmp"]], lambda d: d["m_dis"] / d["m_cmp"]),
        "pull_level_evening_vs_evening": qboot(pq[["e_dis", "e_cmp"]], lambda d: d["e_dis"] / d["e_cmp"]),
        "pull_level_difference": qboot(pq[["m_dis", "m_cmp", "e_dis", "e_cmp"]], lambda d: d["m_dis"] / d["m_cmp"] - d["e_dis"] / d["e_cmp"]),
    }


# --------------------------------------------------------------------------- pair features (Q2, Q5)


def entered(s: Search, key: str) -> str:
    if key not in s.earlier["2026-11-13"]:
        return "day1"
    if key not in s.earlier["2026-11-11"]:
        return "nov13"
    return "established"


def base_features(s: Search, A: list[dict], kx: str, ky: str, ax: int, ay: int, site_of: dict) -> dict:
    """Categories that depend only on day one (or a pseudo-day one) and earlier days."""
    dist = ay - ax
    rev = sum(1 for p in A if vote(p, kx, ky) < 0)
    agree = sum(1 for d in EARLIER if vote(s.earlier[d], kx, ky) > 0)
    revd = sum(1 for d in EARLIER if vote(s.earlier[d], kx, ky) < 0)
    ex, ey = entered(s, kx), entered(s, ky)
    if "day1" in (ex, ey):
        newc = "either URL new on day one"
    elif "nov13" in (ex, ey):
        newc = "either URL new on Nov 13 (none on day one)"
    else:
        newc = "both in the Nov 11 and Nov 13 top 10"
    return {
        "dist": "1" if dist == 1 else ("2" if dist == 2 else "3+"),
        "band": "both in top 3" if ay <= 3 else ("both in 4-10" if ax >= 4 else "mixed"),
        "site": "same site" if site_of[kx] == site_of[ky] else "different sites",
        "contested": f"{rev} of 3" if rev < 2 else "2+ of 3 (pull majority reverses the median)",
        "history": "earlier days mostly agree with day one" if agree > revd else ("earlier days mostly reversed" if revd > agree else "earlier days tied or silent"),
        "newcomer": newc,
    }


FEATURE_ORDER = {
    "dist": ["1", "2", "3+"],
    "band": ["both in top 3", "both in 4-10", "mixed"],
    "site": ["same site", "different sites"],
    "contested": ["0 of 3", "1 of 3", "2+ of 3 (pull majority reverses the median)"],
    "history": ["earlier days mostly agree with day one", "earlier days mostly reversed", "earlier days tied or silent"],
    "newcomer": ["either URL new on day one", "either URL new on Nov 13 (none on day one)", "both in the Nov 11 and Nov 13 top 10"],
}
FEATURE_TITLE = {
    "dist": "Rank distance on day one",
    "band": "Position band on day one",
    "site": "Same site",
    "contested": "Voting pulls of day one that show the reverse order",
    "history": "Order in the four earlier single pulls (Nov 6, 9, 11, 13)",
    "newcomer": "Newcomer status (top 10 of the Nov 11 and Nov 13 pulls)",
}


def site_best(cons: pd.DataFrame, queries: list[str], days: tuple[str, ...]) -> dict:
    sub = cons[cons["query"].isin(queries) & cons["day"].isin(days) & (cons["cons"] <= 10)]
    return sub.groupby(["query", "site", "day"])["cons"].min().astype(int).to_dict()


def site_momentum(best: dict, queries: list[str], site: str, query: str, d_from: str, d_to: str) -> tuple[float, int]:
    deltas = []
    for other in queries:
        if other == query:
            continue
        a = best.get((other, site, d_from), ABSENT)
        b = best.get((other, site, d_to), ABSENT)
        if a == ABSENT and b == ABSENT:
            continue
        deltas.append(b - a)
    return (float(np.mean(deltas)) if deltas else float("nan"), len(deltas))


def observed_pairs(searches: dict[str, Search], cons: pd.DataFrame, site_of: dict) -> pd.DataFrame:
    queries = sorted(searches)
    best = site_best(cons, queries, ("2026-11-13", DAY1, DAY2))
    rec = []
    for query in queries:
        s = searches[query]
        for kx, ky, ax, ay, bx, by, flipped in scored_pairs(cons_of(s.eve), cons_of(s.d2)):
            row = {"query": query, "kx": kx, "ky": ky, "ax": ax, "ay": ay, "bx": bx, "by": by, "flip": int(flipped), "one": 1}
            row.update(base_features(s, s.eve, kx, ky, ax, ay, site_of))
            votes_eve = [vote(p, kx, ky) for p in s.eve]
            v_m = vote(s.morning, kx, ky)
            votes_d2 = [vote(p, kx, ky) for p in s.d2]
            votes_earl = [vote(s.earlier[d], kx, ky) for d in EARLIER]
            row["eve_rev"] = sum(v < 0 for v in votes_eve)
            row["eve_inf"] = sum(v != 0 for v in votes_eve)
            row["m_rev"] = int(v_m < 0)
            row["m_inf"] = int(v_m != 0)
            row["d2_rev"] = sum(v < 0 for v in votes_d2)
            row["d2_inf"] = sum(v != 0 for v in votes_d2)
            row["earl_rev"] = sum(v < 0 for v in votes_earl)
            row["earl_inf"] = sum(v != 0 for v in votes_earl)
            n_before = row["earl_rev"] + row["m_rev"] + row["eve_rev"]
            row["rev_before"] = n_before
            row["novel"] = "never (0 of 8)" if n_before == 0 else ("once (1 of 8)" if n_before == 1 else "2 or more of 8")
            day1_rev = [v_m < 0] + [v < 0 for v in votes_eve]  # time order: morning, e1, e2, e3
            if sum(day1_rev) == 0:
                pattern = "no day-one pull reversed"
            elif sum(day1_rev) >= 2:
                pattern = "2+ day-one pulls reversed"
            else:
                pattern = ["only the morning pull reversed", "only evening pull 1 reversed", "only evening pull 2 reversed", "only the last pull (evening 3) reversed"][day1_rev.index(True)]
            row["last_pattern"] = pattern
            row["x_entered"] = entered(s, kx)
            row["y_entered"] = entered(s, ky)
            for tag, k in (("x", kx), ("y", ky)):
                m, n = site_momentum(best, queries, site_of[k], query, "2026-11-13", DAY1)
                row[f"mom_{tag}"] = m
                row[f"mom_{tag}_n"] = n
                m2, n2 = site_momentum(best, queries, site_of[k], query, DAY1, DAY2)
                row[f"mom2_{tag}"] = m2
                row[f"mom2_{tag}_n"] = n2
            row["can_a"], row["can_b"] = sorted((kx, ky))
            rec.append(row)
    frame = pd.DataFrame.from_records(rec)
    # canonical orientation for the noise model: b is the alphabetically later key
    x_is_a = frame["kx"] == frame["can_a"]
    for src_rev, src_inf, dst in (("eve_rev", "eve_inf", "r1"), ("d2_rev", "d2_inf", "r2"), ("m_rev", "m_inf", "rm"), ("earl_rev", "earl_inf", "re")):
        # votes for "b above a": if x is a, reversed votes (y above x) are b-above-a votes
        frame[dst] = np.where(x_is_a, frame[src_rev], frame[src_inf] - frame[src_rev])
    frame["n1"], frame["n2"], frame["nm"], frame["ne"] = frame["eve_inf"], frame["d2_inf"], frame["m_inf"], frame["earl_inf"]
    return frame


def null_by_category(searches: dict[str, Search], site_of: dict) -> dict:
    """Expected pairs and flips per category under exchangeable pseudo-days (features from pseudo-day one)."""
    acc: dict[tuple[str, str], list[float]] = {}
    for query in sorted(searches):
        s = searches[query]
        six = s.eve + s.d2
        for combo in COMBOS:
            A = [six[i] for i in combo]
            B = [six[i] for i in range(6) if i not in combo]
            for kx, ky, ax, ay, _bx, _by, flipped in scored_pairs(cons_of(A), cons_of(B)):
                for feat, cat in base_features(s, A, kx, ky, ax, ay, site_of).items():
                    slot = acc.setdefault((feat, cat), [0.0, 0.0])
                    slot[0] += 1 / 20
                    slot[1] += flipped / 20
    return {f"{feat}|{cat}": {"pairs": v[0], "flips": v[1]} for (feat, cat), v in acc.items()}


# --------------------------------------------------------------------------- noise models (Q3, Q4)


def binom_lik(r, n) -> np.ndarray:
    return stats.binom.pmf(np.asarray(r)[:, None], np.asarray(n)[:, None], Q[None, :])


def bmaj(q: np.ndarray, n: int | float) -> np.ndarray:
    """P(a strict majority of n pulls shows the order that one pull shows with probability q). n odd or inf."""
    if n == float("inf"):
        return (q > 0.5).astype(float)
    return stats.binom.sf(n // 2, n, q)


def sym(w: np.ndarray) -> np.ndarray:
    w = 0.5 * (w + w[::-1])
    return w / w.sum()


def fit_stationary(L: np.ndarray, weights: np.ndarray, w0=None, max_iter: int = 20000, tol: float = 1e-11) -> dict:
    w = np.full(GRID, 1 / GRID) if w0 is None else w0.copy()
    prev = -np.inf
    for _ in range(max_iter):
        li = L @ w
        ll = float(weights @ np.log(li))
        w = sym(w * (L.T @ (weights / li)))
        if ll - prev < tol * abs(ll):
            break
        prev = ll
    return {"kind": "stationary", "w": w, "pi": 0.0, "loglik": float(weights @ np.log(L @ w))}


def fit_fresh(L1, L2, weights, w0=None, pi0=0.2, max_iter=20000, tol=1e-11, fix_pi: float | None = None) -> dict:
    """Day two keeps day one's order probability, or with probability pi draws a fresh one from G."""
    w = np.full(GRID, 1 / GRID) if w0 is None else w0.copy()
    pi = pi0 if fix_pi is None else fix_pi
    A = L1 * L2
    prev = -np.inf
    for _ in range(max_iter):
        s0, m1, m2 = A @ w, L1 @ w, L2 @ w
        li = (1 - pi) * s0 + pi * m1 * m2
        ll = float(weights @ np.log(li))
        r = weights / li
        counts = (1 - pi) * w * (A.T @ r) + pi * w * (L1.T @ (r * m2)) + pi * w * (L2.T @ (r * m1))
        if fix_pi is None:
            pi = float((r * pi * m1 * m2).sum() / weights.sum())
        w = sym(counts)
        if ll - prev < tol * abs(ll):
            break
        prev = ll
    li = (1 - pi) * ((L1 * L2) @ w) + pi * (L1 @ w) * (L2 @ w)
    return {"kind": "fresh", "w": w, "pi": pi, "loglik": float(weights @ np.log(li))}


def fit_swap(L1, L2, weights, w0=None, pi0=0.1, max_iter=20000, tol=1e-11, fix_pi: float | None = None) -> dict:
    """Day two keeps day one's order probability q, or with probability pi swaps to 1 - q."""
    w = np.full(GRID, 1 / GRID) if w0 is None else w0.copy()
    pi = pi0 if fix_pi is None else fix_pi
    A0 = L1 * L2
    A1 = L1 * L2[:, ::-1]
    prev = -np.inf
    for _ in range(max_iter):
        s0, s1 = A0 @ w, A1 @ w
        li = (1 - pi) * s0 + pi * s1
        ll = float(weights @ np.log(li))
        r = weights / li
        counts = (1 - pi) * w * (A0.T @ r) + pi * w * (A1.T @ r)
        if fix_pi is None:
            pi = float((r * pi * s1).sum() / weights.sum())
        w = sym(counts)
        if ll - prev < tol * abs(ll):
            break
        prev = ll
    li = (1 - pi) * (A0 @ w) + pi * (A1 @ w)
    return {"kind": "swap", "w": w, "pi": pi, "loglik": float(weights @ np.log(li))}


def beta_mirror(a: float, b: float) -> np.ndarray:
    d = stats.beta.pdf(Q, a, b) + stats.beta.pdf(Q, b, a)
    return d / d.sum()


def g_family(family: str, theta) -> np.ndarray:
    """Smooth symmetric G on the grid. beta: mirrored Beta(a, b). lnorm: mirrored logit-normal(mu, sd)."""
    if family == "beta":
        return beta_mirror(np.exp(theta[0]), np.exp(theta[1]))
    z = special.logit(Q)
    sd = np.exp(theta[1])
    d = (stats.norm.pdf(z, theta[0], sd) + stats.norm.pdf(z, -theta[0], sd)) / (Q * (1 - Q))
    total = d.sum()
    if not np.isfinite(total) or total <= 0:
        return np.full(GRID, np.nan)
    return d / total


FAMILY_START = {"beta": [np.log(0.5), np.log(7.0)], "lnorm": [-3.0, np.log(1.5)]}
FAMILY_BOUNDS = {"beta": [(-5.0, 3.0), (-3.0, 6.0)], "lnorm": [(-12.0, 0.0), (-3.0, 2.5)]}


def fit_param(L1, L2, weights, family: str, kind: str, fix_pi: float | None = None, x0=None) -> dict:
    """kind 'stationary' or 'fresh'. pi is fitted on the logit scale unless fix_pi is given."""
    A = L1 * L2
    free_pi = kind == "fresh" and fix_pi is None

    def nll(theta):
        w = g_family(family, theta[:2])
        if not np.all(np.isfinite(w)):
            return 1e12
        if kind == "stationary":
            li = A @ w
        else:
            pi = special.expit(theta[2]) if free_pi else fix_pi
            li = (1 - pi) * (A @ w) + pi * (L1 @ w) * (L2 @ w)
        return -float(weights @ np.log(np.maximum(li, 1e-300)))

    start = list(FAMILY_START[family]) + ([special.logit(0.05)] if free_pi else [])
    if x0 is not None:
        start = list(x0[: len(start)])
    bounds = list(FAMILY_BOUNDS[family]) + ([(-14.0, 3.0)] if free_pi else [])
    res = optimize.minimize(nll, start, method="L-BFGS-B", bounds=bounds)
    pi = 0.0 if kind == "stationary" else (float(special.expit(res.x[2])) if free_pi else float(fix_pi))
    return {"kind": "stationary" if kind == "stationary" else "fresh", "family": family, "w": g_family(family, res.x[:2]), "pi": pi, "loglik": -float(res.fun), "theta": [float(v) for v in res.x]}


def profile_pi(fitter, grid: list[float]) -> list[dict]:
    out = []
    for value in grid:
        f = fitter(value)
        out.append({"pi": value, "loglik": f["loglik"]})
    return out


def upper_bound(profile: list[dict], crit: float = 2.706) -> float | None:
    """Largest pi whose profile log-likelihood is within crit/2 of the maximum (one-sided 95%)."""
    lls = np.array([p["loglik"] for p in profile])
    pis = np.array([p["pi"] for p in profile])
    best = lls.max()
    inside = 2 * (best - lls) <= crit
    last = int(np.max(np.nonzero(inside)[0]))
    if last == len(pis) - 1:
        return None
    a, b = 2 * (best - lls[last]), 2 * (best - lls[last + 1])
    return float(pis[last] + (crit - a) / (b - a) * (pis[last + 1] - pis[last]))


def drift_kernel(tau: float) -> np.ndarray:
    """Gradual change: logit(q2) = logit(q1) + N(0, tau^2), discretised on the grid."""
    if tau <= 1e-9:
        return np.eye(GRID)
    z = special.logit(Q)
    K = stats.norm.pdf(z[None, :] - z[:, None], scale=tau)
    return K / K.sum(axis=1, keepdims=True)


def fit_drift(L1, L2, weights, family: str = "beta", fix_tau: float | None = None, x0=None) -> dict:
    def nll(theta):
        w = g_family(family, theta[:2])
        if not np.all(np.isfinite(w)):
            return 1e12
        tau = fix_tau if fix_tau is not None else float(np.exp(theta[2]))
        li = (L1 * (L2 @ drift_kernel(tau).T)) @ w
        return -float(weights @ np.log(np.maximum(li, 1e-300)))

    start = list(FAMILY_START[family]) + ([np.log(0.3)] if fix_tau is None else [])
    if x0 is not None:
        start = list(x0[: len(start)])
    bounds = list(FAMILY_BOUNDS[family]) + ([(np.log(1e-3), np.log(10.0))] if fix_tau is None else [])
    res = optimize.minimize(nll, start, method="L-BFGS-B", bounds=bounds)
    tau = float(np.exp(res.x[2])) if fix_tau is None else float(fix_tau)
    return {"kind": "drift", "family": family, "w": g_family(family, res.x[:2]), "pi": 0.0, "tau": tau, "loglik": -float(res.fun), "theta": [float(v) for v in res.x]}


def kernel(fit: dict, pi: float | None = None) -> np.ndarray:
    """Day-one to day-two transition matrix over the q grid (rows: day-one q)."""
    eye = np.eye(GRID)
    if fit["kind"] == "drift":
        return drift_kernel(fit["tau"])
    pi = fit["pi"] if pi is None else pi
    if fit["kind"] == "swap":
        return (1 - pi) * eye + pi * eye[::-1]
    return (1 - pi) * eye + pi * np.outer(np.ones(GRID), fit["w"])


OPP = np.outer(LO, ~LO) | np.outer(~LO, LO)


def posterior_real(fit: dict, L1, L2) -> tuple[np.ndarray, np.ndarray]:
    """Per pair: P(the true pull-majority order changed between the days | votes), and the likelihood."""
    w, K = fit["w"], kernel(fit)
    li = (L1 * (L2 @ K.T)) @ w
    real = (L1 * (L2 @ (K * OPP).T)) @ w / li
    return real, li


def project(fit: dict, n: int | float, pi: float | None = None) -> dict:
    """Per scored pair, n pulls per day, majority consensus: flip rate split into real and noise."""
    w, K = fit["w"], kernel(fit, pi)
    B = bmaj(Q, n)
    F = np.outer(B, 1 - B)
    F = F + F.T
    WK = w[:, None] * K
    total = float(np.sum(WK * F))
    real = float(np.sum(WK * F * OPP))
    true_change = float(np.sum(WK * OPP))
    return {
        "pulls": n if n != float("inf") else "inf",
        "flip_rate": total,
        "real_flip_rate": real,
        "noise_flip_rate": total - real,
        "noise_share": (total - real) / total if total else None,
        "true_order_change_rate": true_change,
        "share_of_true_changes_seen_as_flips": real / true_change if true_change > 1e-12 else None,
    }


def greedy_budget(units: list[tuple[float, float]], floor: float = 0.5, fractional: bool = True) -> dict:
    """units = (flip probability, mass). Call in order of flip probability while expected precision >= floor."""
    total = sum(f * m for f, m in units)
    mass_all = sum(m for _, m in units)
    units = sorted(units, key=lambda u: -u[0])
    tp = calls = 0.0
    for f, m in units:
        if m <= 0:
            continue
        if (tp + f * m) / (calls + m) >= floor:
            tp += f * m
            calls += m
            continue
        if fractional and f < floor and calls > 0:
            t = max(0.0, min(1.0, (tp - floor * calls) / (m * (floor - f))))
            tp += t * f * m
            calls += t * m
        break
    return {"recall": tp / total if total else None, "precision": tp / calls if calls else None, "call_share": calls / mass_all, "flip_rate": total / mass_all}


def true_q_oracle(fit: dict, pi: float | None = None, n_day: int = 3, n_next: int = 3, floor: float = 0.5) -> dict:
    """Oracle that knows each pair's day-one q (and the change kernel)."""
    w, K = fit["w"], kernel(fit, pi)
    B1 = bmaj(Q, n_day)
    KB = K @ bmaj(Q, n_next)  # P(day-two consensus shows b above a | day-one q)
    units = []
    for g in range(GRID):
        units.append((KB[g], w[g] * (1 - B1[g])))  # day one shows a above b
        units.append((1 - KB[g], w[g] * B1[g]))  # day one shows b above a
    return greedy_budget(units, floor=floor)


def history_oracle(fit: dict, k_extra: int, pi: float | None = None, n_day: int = 3, n_next: int = 3, floor: float = 0.5) -> dict:
    """Bayes oracle that knows G and the kernel and sees k_extra stationary pulls before day one plus day one's pulls."""
    w, K = fit["w"], kernel(fit, pi)
    KB = K @ bmaj(Q, n_next)
    units = []
    for r1 in range(n_day + 1):
        state_b = r1 > n_day / 2
        for h in range(k_extra + 1):
            like = w * stats.binom.pmf(r1, n_day, Q) * stats.binom.pmf(h, k_extra, Q)
            mass = float(like.sum())
            if mass <= 0:
                continue
            p_b = float((like / mass) @ KB)
            units.append(((1 - p_b) if state_b else p_b, mass))
    return greedy_budget(units, floor=floor)


# --------------------------------------------------------------------------- empirical oracles (Q3)


def oracle_scores(frame: pd.DataFrame, sets: list[str], w: np.ndarray, pi: float = 0.0) -> np.ndarray:
    """P(flip) for each observed pair from the votes in the named sets (x = day-one winner)."""
    s = sum(frame[f"{name}_rev"].to_numpy() for name in sets)
    n = sum(frame[f"{name}_inf"].to_numpy() for name in sets)
    post = binom_lik(s, n) * w
    post /= post.sum(axis=1, keepdims=True)
    B3 = bmaj(Q, 3)
    return (1 - pi) * (post @ B3) + pi * float(w @ B3)


def realized_budget(frame: pd.DataFrame, score: np.ndarray, universe: list[str], floor: float = 0.5) -> dict:
    """Call whole tie groups in order of score while the mean score stays >= floor; then score on real flips."""
    df = pd.DataFrame({"query": frame["query"].to_numpy(), "flip": frame["flip"].to_numpy(), "score": np.round(score, 12)})
    groups = df.groupby("score").agg(n=("flip", "size"), tp=("flip", "sum")).sort_index(ascending=False)
    groups["exp"] = groups.index.to_numpy() * groups["n"]
    cut = None
    cum_n = cum_exp = 0.0
    for value, row in groups.iterrows():
        if (cum_exp + row["exp"]) / (cum_n + row["n"]) < floor:
            break
        cum_n += row["n"]
        cum_exp += row["exp"]
        cut = value
    called = df["score"] >= cut if cut is not None else pd.Series(False, index=df.index)
    df["call"] = called.astype(int)
    df["tp"] = (called & (df["flip"] == 1)).astype(int)
    # post-hoc realized frontier on the same ordering
    best = {"recall": 0.0, "precision": None, "calls": 0, "threshold": None}
    ctp = cn = 0
    for value, row in groups.iterrows():
        ctp += row["tp"]
        cn += row["n"]
        if cn and ctp / cn >= floor and ctp / df["flip"].sum() > best["recall"]:
            best = {"recall": ctp / df["flip"].sum(), "precision": ctp / cn, "calls": int(cn), "right": int(ctp), "threshold": float(value)}
    return {
        "calls": int(df["call"].sum()),
        "right": int(df["tp"].sum()),
        "recall": rate_ci(df, "tp", "flip", universe),
        "precision": rate_ci(df, "tp", "call", universe),
        "expected_recall": float(cum_exp / df["score"].sum()),
        "expected_precision": float(cum_exp / cum_n) if cum_n else None,
        "score_cut": None if cut is None else float(cut),
        "posthoc_best_realized": best,
    }


# --------------------------------------------------------------------------- variogram (Q4)

BUCKETS = [0, 2, 12, 21, 30, 60, 84, 132, 180, 228, 300]
BUCKET_LABELS = ["<2 h (same session)", "2-12 h (Nov 16 morning vs evening)", "12-21 h (Nov 16 evening vs Nov 17)", "21-30 h (Nov 16 morning vs Nov 17)", "30-60 h (about 2 days)", "60-84 h (about 3 days)", "84-132 h (4-5 days)", "132-180 h (6-7 days)", "180-228 h (8-9 days)", "228-300 h (10-11 days)"]


def variogram(searches: dict[str, Search]) -> pd.DataFrame:
    rec = []
    for query, s in searches.items():
        tl = s.timeline
        for i in range(len(tl)):
            for j in range(i + 1, len(tl)):
                (la, ta, ra), (lb, tb, rb) = tl[i], tl[j]
                gap = abs((pd.Timestamp(tb) - pd.Timestamp(ta)).total_seconds()) / 3600
                d, c = pull_disagreement(ra, rb)
                rec.append({"query": query, "a": la, "b": lb, "gap_h": gap, "dis": d, "cmp": c})
    frame = pd.DataFrame.from_records(rec)
    frame["bucket"] = pd.cut(frame["gap_h"], BUCKETS, right=False, labels=BUCKET_LABELS)
    return frame


# --------------------------------------------------------------------------- main


def table(header: list[str], rows: list[list]) -> None:
    say("| " + " | ".join(header) + " |")
    say("| " + " | ".join("---" for _ in header) + " |")
    for row in rows:
        say("| " + " | ".join(str(c) for c in row) + " |")


def to_jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [to_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    return obj


def main() -> None:
    start = time.time()
    results: dict = {}
    rows, cons, site_of, searches = load()
    queries = sorted(searches)
    say("# Round 2, Target 3 noise study (design days only). Measurement, not a predictor.")
    say(f"Latest day loaded: {rows['day'].max()} (asserted == 2026-11-17). Searches with Nov 16 and Nov 17 pulls: {len(queries)}.")
    results["data"] = {"max_day_loaded": str(rows["day"].max()), "searches": len(queries)}

    # ---- the pair table, checked against rsdata.flip_pairs
    obs = observed_pairs(searches, cons, site_of)
    groups = rs.query_groups(rows)
    ref = rs.flip_pairs(cons, DAY1, DAY2)
    ref = ref[ref["query"].map(groups) == "o30"]
    mine = set(zip(obs["query"], obs["kx"], obs["ky"], obs["flip"].astype(bool), strict=True))
    theirs = set(zip(ref["query"], ref["key_x"], ref["key_y"], ref["flipped"].astype(bool), strict=True))
    if mine != theirs or len(obs) != 767 or int(obs["flip"].sum()) != 44:
        raise AssertionError("pair table differs from rsdata.flip_pairs")
    U = sorted(obs["query"].unique())
    check = mt.bootstrap_rate(obs, "flip")
    mine_rate = rate_ci(obs, "flip", "one", U)
    if abs(check["lo"] - mine_rate["lo"]) > 1e-12 or abs(check["hi"] - mine_rate["hi"]) > 1e-12:
        raise AssertionError("qboot does not reproduce metrics.bootstrap_rate")
    say(f"Pair table equals rsdata.flip_pairs: 767 pairs, 44 flips, {len(U)} searches with pairs. Flip rate {fr(mine_rate)} (same as metrics.bootstrap_rate).")
    results["observed_flip_rate"] = mine_rate

    tick("loaded")
    # ================================================================ Q1
    say("\n## Q1. Noise share of flips: permutation of the six voting pulls")
    q1, pq = q1_permutation(searches)
    results["q1_nov16_to_nov17"] = q1
    nb = q1["query_bootstrap"]
    say(f"Observed: {q1['observed']['flips']} flips / {q1['observed']['pairs']} pairs = {q1['observed']['rate']:.4f}")
    say(f"Exchangeable (all 20 splits of each search's six pulls, exact per search): expected flips {q1['null_expected']['flips']:.2f}, expected pairs {q1['null_expected']['pairs']:.1f}, pooled rate {q1['null_expected']['rate_pooled']:.4f}")
    say(f"Null spread over random splits ({MC_DRAWS} Monte Carlo draws of one split per search): flips {q1['null_distribution_mc']['flips_2.5_97.5']}, rate {[round(v, 4) for v in q1['null_distribution_mc']['rate_2.5_97.5']]}; P(null flips >= 44) = {q1['null_distribution_mc']['p_null_flips_ge_observed']:.4f}; P(null rate >= observed) = {q1['null_distribution_mc']['p_null_rate_ge_observed']:.4f}")
    say(f"Query bootstrap: null rate {fv(nb['null_rate'], 4)}; observed rate {fv(nb['observed_rate'], 4)}; noise share of the flip count {fv(nb['noise_share_of_flip_count'])}; of the flip rate {fv(nb['noise_share_of_flip_rate'])}; excess flips {fv(nb['excess_flips'], 2)}; excess rate {fv(nb['excess_rate'], 4)}")
    pl = q1["pull_level"]
    say(f"Pull level (URL pairs present in both pulls): within-day disagreement {fv(pl['within_day_disagreement'], 4)}, cross-day {fv(pl['cross_day_disagreement'], 4)}, cross minus within {fv(pl['cross_minus_within'], 4)}; null 95% range {[round(v, 4) for v in pl['null_cross_minus_within_2.5_97.5']]}, P(null >= observed) = {pl['p_null_ge_observed']:.4f}. Counts {pl['counts']}")

    say("\n### Q1b. Nov 16 alone: morning pull versus the evening session")
    q1b = q1_nov16(searches)
    results["q1_nov16_morning_vs_evening"] = q1b
    o = q1b["observed_morning_vs_evening_consensus"]
    say(f"Morning pull vs evening consensus: {o['flips']} flips / {o['pairs']} pairs = {fv(q1b['observed_rate_ci'], 4)}")
    say(f"Each evening pull vs the consensus of the other three (mean): {q1b['evening_pull_vs_rest_mean']['flips']:.2f} / {q1b['evening_pull_vs_rest_mean']['pairs']:.1f} = {fv(q1b['evening_heldout_rate_ci'], 4)}")
    say(f"Any of the four held out (exchangeable null): rate {q1b['null_expected_any_pull_held_out']['rate']:.4f}; P(null flips >= observed) = {q1b['p_null_flips_ge_observed']:.4f}; P(null rate >= observed) = {q1b['p_null_rate_ge_observed']:.4f}")
    say(f"Morning minus evening flip rate {fv(q1b['morning_minus_evening_rate'], 4)}; evening-held-out rate as a share of the morning rate {fv(q1b['noise_share'])}")
    say(f"Pull level: morning vs evening pulls {fv(q1b['pull_level_morning_vs_evening'], 4)}, evening vs evening {fv(q1b['pull_level_evening_vs_evening'], 4)}, difference {fv(q1b['pull_level_difference'], 4)}")

    tick("Q1 done")
    say("\n### Q1c. Are the pulls exchangeable? Pull-by-pull disagreement on Nov 16 and Nov 17")
    labels7 = ["16m", "16e1", "16e2", "16e3", "17a", "17b", "17c"]
    rec7 = []
    for query in queries:
        s = searches[query]
        seven = [s.morning] + s.eve + s.d2
        for i, j in itertools.combinations(range(7), 2):
            d, c = pull_disagreement(seven[i], seven[j])
            same = sorted(seven[i].items(), key=lambda t: t[1]) == sorted(seven[j].items(), key=lambda t: t[1])
            rec7.append({"query": query, "i": i, "j": j, "dis": d, "cmp": c, "identical": int(same)})
    rec7 = pd.DataFrame(rec7)
    mat_rows = []
    pair_stats = {}
    for i in range(7):
        row = [labels7[i]]
        for j in range(7):
            if i == j:
                row.append("-")
                continue
            a, b = min(i, j), max(i, j)
            sub = rec7[(rec7["i"] == a) & (rec7["j"] == b)]
            row.append(f"{sub['dis'].sum() / sub['cmp'].sum():.3f} ({int(sub['identical'].sum())})")
            pair_stats[f"{labels7[a]}~{labels7[b]}"] = {"disagreement": float(sub["dis"].sum() / sub["cmp"].sum()), "identical_top10_searches": int(sub["identical"].sum())}
        mat_rows.append(row)
    say("Pooled share of URL pairs ordered differently (in brackets: searches out of 30 whose two top-10 lists are identical in order):")
    table(["pull"] + labels7, mat_rows)
    to17 = {}
    for i in range(4):
        sel = (rec7["i"] == i) & (rec7["j"] >= 4)
        frame = pd.DataFrame({"query": rec7["query"], "dis": np.where(sel, rec7["dis"], 0), "cmp": np.where(sel, rec7["cmp"], 0)})
        to17[labels7[i]] = qboot(per_query(frame, ["dis", "cmp"], queries), lambda d: d["dis"] / d["cmp"])
    e3 = pd.DataFrame({"query": rec7["query"], "d3": np.where((rec7["i"] == 3) & (rec7["j"] >= 4), rec7["dis"], 0), "c3": np.where((rec7["i"] == 3) & (rec7["j"] >= 4), rec7["cmp"], 0), "d2": np.where((rec7["i"] == 2) & (rec7["j"] >= 4), rec7["dis"], 0), "c2": np.where((rec7["i"] == 2) & (rec7["j"] >= 4), rec7["cmp"], 0)})
    diff32 = qboot(per_query(e3, ["d3", "c3", "d2", "c2"], queries), lambda d: d["d3"] / d["c3"] - d["d2"] / d["c2"])
    say("Disagreement of each Nov 16 pull with the three Nov 17 pulls: " + "; ".join(f"{k} {fv(v, 4)}" for k, v in to17.items()) + f". Evening 3 minus evening 2: {fv(diff32, 4)}")
    ident_same = int(rec7[((rec7["i"] >= 1) & (rec7["j"] <= 3)) | (rec7["i"] >= 4)]["identical"].sum())
    ident_n = int((((rec7["i"] >= 1) & (rec7["j"] <= 3)) | (rec7["i"] >= 4)).sum())
    ident_cross = int(rec7[(rec7["i"] >= 1) & (rec7["i"] <= 3) & (rec7["j"] >= 4)]["identical"].sum())
    ident_cross_n = int(((rec7["i"] >= 1) & (rec7["i"] <= 3) & (rec7["j"] >= 4)).sum())
    say(f"Identical top-10 lists: same-session pull pairs {ident_same}/{ident_n}; Nov 16 evening vs Nov 17 pull pairs {ident_cross}/{ident_cross_n}")
    idf = pd.DataFrame({"query": rec7["query"], "same": np.where(((rec7["i"] >= 1) & (rec7["j"] <= 3)) | (rec7["i"] >= 4), rec7["identical"], 0), "same_n": (((rec7["i"] >= 1) & (rec7["j"] <= 3)) | (rec7["i"] >= 4)).astype(int), "cross": np.where((rec7["i"] >= 1) & (rec7["i"] <= 3) & (rec7["j"] >= 4), rec7["identical"], 0), "cross_n": ((rec7["i"] >= 1) & (rec7["i"] <= 3) & (rec7["j"] >= 4)).astype(int)})
    ident_diff = qboot(per_query(idf, ["same", "same_n", "cross", "cross_n"], queries), lambda d: d["same"] / d["same_n"] - d["cross"] / d["cross_n"])
    say(f"Identical-list rate, same session minus cross day: {fv(ident_diff)}")
    results["q1_pull_similarity"] = {"identical_rate_difference": ident_diff, "pairs": pair_stats, "nov16_pull_vs_nov17": to17, "evening3_minus_evening2_vs_nov17": diff32, "identical_same_session": [ident_same, ident_n], "identical_cross_day": [ident_cross, ident_cross_n]}

    # ================================================================ noise model (needed by Q2, Q3, Q4)
    say("\n## Noise model fits (canonical orientation, 3 voting pulls on Nov 16 and 3 pulls on Nov 17 per scored pair)")
    L1 = binom_lik(obs["r1"], obs["n1"])
    L2 = binom_lik(obs["r2"], obs["n2"])
    ones = np.ones(len(obs))
    flips_mask = obs["flip"] == 1
    fits = {
        "npmle_stationary": fit_stationary(L1 * L2, ones),
        "npmle_fresh": fit_fresh(L1, L2, ones),
        "npmle_swap": fit_swap(L1, L2, ones),
        "beta_stationary": fit_param(L1, L2, ones, "beta", "stationary"),
        "beta_fresh": fit_param(L1, L2, ones, "beta", "fresh"),
        "lnorm_stationary": fit_param(L1, L2, ones, "lnorm", "stationary"),
        "lnorm_fresh": fit_param(L1, L2, ones, "lnorm", "fresh"),
        "beta_drift": fit_drift(L1, L2, ones, "beta"),
    }
    fit_rows = []
    for name, f in fits.items():
        D0 = float(np.sum(f["w"] * 2 * Q * (1 - Q)))
        change = f"tau {f['tau']:.4f}" if f["kind"] == "drift" else f"pi {f['pi']:.4f}"
        fit_rows.append([name, f"{f['loglik']:.2f}", change, f"{D0:.4f}", f"{project(f, 3)['flip_rate']:.4f}", f"{float(f['w'][(Q >= 0.35) & (Q <= 0.65)].sum()):.3f}"])
    table(["model (G family, change kind)", "log-lik", "fitted change parameter", "single-pull disagreement under G", "model flip rate at 3 pulls", "G mass with pull-level q in [0.35, 0.65]"], fit_rows)
    say(f"Parametric G: beta-mirror theta {fits['beta_stationary']['theta']}, logit-normal-mirror theta {fits['lnorm_stationary']['theta']} (stationary fits).")
    results["model_fits"] = {name: {k: v for k, v in f.items() if k != "w"} | {"G_mass_q_0.35_0.65": float(f["w"][(Q >= 0.35) & (Q <= 0.65)].sum()), "G_mass_q_below_0.1_or_above_0.9": float(f["w"][(Q < 0.1) | (Q > 0.9)].sum())} for name, f in fits.items()}

    # profile likelihood of pi (share of pairs whose order probability changed overnight)
    PI_GRID = [0.0, 0.0025, 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3]
    profiles = {
        "npmle_fresh": profile_pi(lambda v: fit_fresh(L1, L2, ones, fix_pi=v), PI_GRID),
        "npmle_swap": profile_pi(lambda v: fit_swap(L1, L2, ones, fix_pi=v), PI_GRID),
        "beta_fresh": profile_pi(lambda v: fit_param(L1, L2, ones, "beta", "fresh", fix_pi=v), PI_GRID),
        "lnorm_fresh": profile_pi(lambda v: fit_param(L1, L2, ones, "lnorm", "fresh", fix_pi=v), PI_GRID),
    }
    ub = {name: upper_bound(p) for name, p in profiles.items()}
    say("\nProfile log-likelihood of pi, shown as 2*(max - LL). One-sided 95% upper bound where it crosses 2.706. Pairs are treated as independent here, so these bounds are too narrow if pairs in one search move together.")
    table(["model"] + [str(v) for v in PI_GRID] + ["95% upper bound on pi"], [[name] + [f"{2 * (max(x['loglik'] for x in p) - x['loglik']):.2f}" for x in p] + [f"{ub[name]:.4f}" if ub[name] is not None else "> 0.3"] for name, p in profiles.items()])
    TAU_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
    profiles["beta_drift"] = profile_pi(lambda v: fit_drift(L1, L2, ones, "beta", fix_tau=v), TAU_GRID)
    ub["beta_drift"] = upper_bound(profiles["beta_drift"])
    say("Profile of the drift model's tau (logit units per day), 2*(max - LL):")
    table(["model"] + [str(v) for v in TAU_GRID] + ["95% upper bound on tau"], [["beta_drift"] + [f"{2 * (max(x['loglik'] for x in profiles['beta_drift']) - x['loglik']):.2f}" for x in profiles["beta_drift"]] + [f"{ub['beta_drift']:.4f}" if ub["beta_drift"] is not None else "> 3"]])
    results["profiles"] = {"pi": {k: v for k, v in profiles.items() if k != "beta_drift"}, "tau": profiles["beta_drift"], "upper_bounds": ub}
    fits_ub = {
        "npmle_fresh": fit_fresh(L1, L2, ones, fix_pi=ub["npmle_fresh"]),
        "npmle_swap": fit_swap(L1, L2, ones, fix_pi=ub["npmle_swap"]),
        "beta_fresh": fit_param(L1, L2, ones, "beta", "fresh", fix_pi=ub["beta_fresh"]),
        "lnorm_fresh": fit_param(L1, L2, ones, "lnorm", "fresh", fix_pi=ub["lnorm_fresh"]),
        "beta_drift": fit_drift(L1, L2, ones, "beta", fix_tau=ub["beta_drift"]),
    }
    decomposition = {}
    dec_rows = []
    for name, f in fits_ub.items():
        real, _ = posterior_real(f, L1, L2)
        mle_real, _ = posterior_real(fits[name], L1, L2)
        p3 = project(f, 3)
        mle_par = fits[name]["tau"] if f["kind"] == "drift" else fits[name]["pi"]
        decomposition[name] = {
            "change_parameter": "tau" if f["kind"] == "drift" else "pi",
            "mle": mle_par,
            "upper95_profile": ub[name],
            "real_flips_among_44_at_mle": float(mle_real[flips_mask].sum()),
            "real_flips_among_44_at_upper": float(real[flips_mask].sum()),
            "real_share_of_44_at_upper": float(real[flips_mask].sum() / 44),
            "real_changes_missed_by_consensus_at_upper": float(real[~flips_mask].sum()),
            "true_order_change_rate_at_upper": p3["true_order_change_rate"],
            "model_real_share_of_flips_at_upper_3_pulls": 1 - p3["noise_share"],
        }
        dec_rows.append([name, decomposition[name]["change_parameter"], f"{mle_par:.4f}", f"{float(mle_real[flips_mask].sum()):.2f}", f"{ub[name]:.4f}", f"{p3['true_order_change_rate']:.4f}", f"{float(real[flips_mask].sum()):.1f} ({float(real[flips_mask].sum()) / 44:.2f})", f"{float(real[~flips_mask].sum()):.1f}"])
    say("\nHow many of the 44 flips are real order changes (the pull-majority order differs between the days)?")
    table(["model", "change parameter", "MLE", "real flips among 44 at MLE", "95% upper bound", "true order changes per scored pair at the bound", "real flips among 44 at the bound (share)", "real changes the consensus missed at the bound"], dec_rows)

    tick("fits and profiles done")
    # query bootstrap of the parametric change model (refit per draw)
    say("\nQuery bootstrap of the beta-mirror fresh-change model (2000 draws, refit per draw):")
    q_index = {q: i for i, q in enumerate(U)}
    pair_q = obs["query"].map(q_index).to_numpy()
    rng = np.random.default_rng(mt.BOOT_SEED)
    index = rng.integers(0, len(U), size=(mt.BOOT_DRAWS, len(U)))
    fl_all = obs["flip"].to_numpy()
    boot = []
    x0 = list(fits["beta_stationary"]["theta"]) + [special.logit(0.02)]
    for row in index:
        mult = np.bincount(row, minlength=len(U)).astype(float)
        weights = mult[pair_q]
        keep = weights > 0
        f = fit_param(L1[keep], L2[keep], weights[keep], "beta", "fresh", x0=x0)
        rf, _ = posterior_real(f, L1[keep], L2[keep])
        f["real_among_flips"] = float((weights[keep] * rf * fl_all[keep]).sum())
        f["flips"] = float((weights[keep] * fl_all[keep]).sum())
        f["real_among_nonflips"] = float((weights[keep] * rf * (1 - fl_all[keep])).sum())
        boot.append(f)

    def bci(values) -> list[float]:
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        return [float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))]

    boot_summary = {
        "pi": bci([f["pi"] for f in boot]),
        "pi_share_of_draws_below_0.001": float(np.mean([f["pi"] < 0.001 for f in boot])),
        "real_share_of_flips": bci([f["real_among_flips"] / f["flips"] for f in boot]),
        "real_changes_missed": bci([f["real_among_nonflips"] for f in boot]),
        "true_order_change_rate": bci([project(f, 3)["true_order_change_rate"] for f in boot]),
    }
    decomposition["beta_fresh_bootstrap"] = boot_summary
    say(f"pi {fits['beta_fresh']['pi']:.4f} {boot_summary['pi']} (share of draws with pi < 0.001: {boot_summary['pi_share_of_draws_below_0.001']:.3f}); real share of flips {boot_summary['real_share_of_flips']}; true order changes per scored pair {boot_summary['true_order_change_rate']}; real changes missed by consensus {boot_summary['real_changes_missed']}")
    results["model_decomposition_of_44_flips"] = decomposition
    pi_hi = ub["beta_fresh"]
    fit_hi = fits_ub["beta_fresh"]

    tick("bootstrap done")
    # model check: folded (r1, r2) table for pairs with 3 informative votes on both days
    full = (obs["n1"] == 3) & (obs["n2"] == 3)
    n_full = int(full.sum())
    cells = {}
    for r1 in range(4):
        for r2 in range(4):
            key = min((r1, r2), (3 - r1, 3 - r2))
            cells.setdefault(key, []).append((r1, r2))
    say(f"\nModel check on the {n_full} pairs with 3 informative votes on both days (folded: (r1, r2) is pooled with (3-r1, 3-r2); r = votes for the alphabetically later URL):")
    check_rows = []
    for key in sorted(cells):
        observed = int(sum(((obs.loc[full, "r1"] == a) & (obs.loc[full, "r2"] == b)).sum() for a, b in cells[key]))
        exp = []
        for f in (fits["npmle_stationary"], fits["beta_stationary"], fits["lnorm_stationary"], fit_hi):
            tot = 0.0
            for a, b in cells[key]:
                la = stats.binom.pmf(a, 3, Q)
                lb = stats.binom.pmf(b, 3, Q)
                tot += float(np.sum(f["w"] * la * (kernel(f) @ lb)))
            exp.append(f"{n_full * tot:.1f}")
        check_rows.append([f"{cells[key]}", observed] + exp)
    table(["cells (r1, r2)", "observed", "NPMLE stationary", "beta stationary", "logit-normal stationary", f"beta fresh at pi = {pi_hi:.3f}"], check_rows)
    results["model_check"] = check_rows
    a_cnt = int(sum(((obs.loc[full, "r1"] == a) & (obs.loc[full, "r2"] == b)).sum() for a, b in cells[(1, 0)]))
    b_cnt = int(sum(((obs.loc[full, "r1"] == a) & (obs.loc[full, "r2"] == b)).sum() for a, b in cells[(0, 1)]))
    asym = stats.binomtest(a_cnt, a_cnt + b_cnt, 0.5)
    say(f"Day asymmetry: one dissenting vote on Nov 16 and none on Nov 17: {a_cnt}; the reverse: {b_cnt}. Two-sided binomial p = {asym.pvalue:.4f}. Every model here is symmetric in the two days, so none can fit this.")
    sess = []
    for query in queries:
        s = searches[query]
        e = [pull_disagreement(a, b) for a, b in itertools.combinations(s.eve, 2)]
        d = [pull_disagreement(a, b) for a, b in itertools.combinations(s.d2, 2)]
        sess.append({"query": query, "e_dis": sum(x[0] for x in e), "e_cmp": sum(x[1] for x in e), "d_dis": sum(x[0] for x in d), "d_cmp": sum(x[1] for x in d)})
    sess = pd.DataFrame(sess).set_index("query")
    sess_res = {
        "nov16_evening": qboot(sess[["e_dis", "e_cmp"]], lambda d: d["e_dis"] / d["e_cmp"]),
        "nov17": qboot(sess[["d_dis", "d_cmp"]], lambda d: d["d_dis"] / d["d_cmp"]),
        "difference": qboot(sess[["e_dis", "e_cmp", "d_dis", "d_cmp"]], lambda d: d["e_dis"] / d["e_cmp"] - d["d_dis"] / d["d_cmp"]),
        "day_asymmetry_counts": [a_cnt, b_cnt],
        "day_asymmetry_p": float(asym.pvalue),
    }
    say(f"Same-session single-pull disagreement: Nov 16 evening {fv(sess_res['nov16_evening'], 4)}, Nov 17 {fv(sess_res['nov17'], 4)}, difference {fv(sess_res['difference'], 4)}")
    results["session_noise"] = sess_res

    tick("model check done")
    # ================================================================ Q2
    say("\n## Q2. Structure of flips (observed Nov 16 to Nov 17), with the exchangeable-null expectation per category")
    null_cat = null_by_category(searches, site_of)
    q2 = {}
    for feat, cats in FEATURE_ORDER.items():
        say(f"\n### {FEATURE_TITLE[feat]}")
        trs = []
        q2[feat] = {}
        for cat in cats:
            sub = obs[obs[feat] == cat]
            flag = obs.assign(f=(obs[feat] == cat).astype(int))
            flag["ff"] = flag["f"] * flag["flip"]
            res = rate_ci(flag, "ff", "f", U)
            nl = null_cat.get(f"{feat}|{cat}", {"pairs": 0.0, "flips": 0.0})
            q2[feat][cat] = {"flips": int(sub["flip"].sum()), "non_flips": int((sub["flip"] == 0).sum()), "pairs": len(sub), "flip_rate": res, "null_pairs": nl["pairs"], "null_flips": nl["flips"]}
            trs.append([cat, int(sub["flip"].sum()), int((sub["flip"] == 0).sum()), fr(res), f"{nl['flips']:.1f} / {nl['pairs']:.1f}", f"{int(sub['flip'].sum()) - nl['flips']:+.1f}"])
        table(["category", "flips", "non-flips", "flip rate [95% CI]", "exchangeable null: flips / pairs", "observed minus null flips"], trs)

    # reversion vs departure, worded for flips
    rev_rows = []
    for cat, name in (("earlier days mostly reversed", "reversion (day two returns to the earlier order)"), ("earlier days mostly agree with day one", "departure (day two leaves the earlier order)"), ("earlier days tied or silent", "no earlier majority")):
        rev_rows.append([name, int(obs.loc[(obs["history"] == cat), "flip"].sum())])
    say("\nThe 44 flips by direction relative to the earlier days:")
    table(["flip type", "flips"], rev_rows)
    q2["reversion_vs_departure"] = rev_rows

    # strength on day two, and day one morning
    say("\nDay-two strength of the 44 flips (Nov 17 pulls showing the new order):")
    strength = obs[flips_mask].groupby("d2_rev").size().to_dict()
    table(["Nov 17 pulls showing the new order", "flips"], [[k, v] for k, v in sorted(strength.items(), reverse=True)])
    q2["day_two_strength"] = strength
    say("\nDay-one votes over all four Nov 16 pulls (morning + 3 evening) showing the reverse order:")
    rows4 = []
    for k in range(5):
        sub = obs[(obs["eve_rev"] + obs["m_rev"]) == k]
        if len(sub):
            rows4.append([f"{k} of 4", int(sub["flip"].sum()), len(sub), f"{sub['flip'].mean():.3f}"])
    table(["reverse votes on Nov 16", "flips", "pairs", "flip rate"], rows4)
    q2["four_pull_votes"] = rows4

    # movers: which URL moved in each flip
    fl = obs[flips_mask].copy()
    fl["dx"] = fl["bx"] - fl["ax"]
    fl["dy"] = fl["by"] - fl["ay"]
    drivers = []
    for r in fl.itertuples(index=False):
        if abs(r.dx) > abs(r.dy):
            drivers.append((r.query, r.kx, abs(r.dx)))
        elif abs(r.dy) > abs(r.dx):
            drivers.append((r.query, r.ky, abs(r.dy)))
        else:
            drivers.append((r.query, "tie", abs(r.dx)))
    fl["driver_move"] = [d[2] for d in drivers]
    fl["driver"] = [f"{d[0]}|{d[1]}" for d in drivers]
    adjacent = int(((fl["ay"] - fl["ax"] == 1) & (fl["bx"] - fl["by"] == 1)).sum())
    distinct = fl.loc[~fl["driver"].str.endswith("|tie"), "driver"].nunique()
    per_driver = fl.loc[~fl["driver"].str.endswith("|tie")].groupby("driver").size().sort_values(ascending=False)
    url_moves = []
    for query in queries:
        s = searches[query]
        ca, cb = cons_of(s.eve), cons_of(s.d2)
        for k, v in ca.items():
            if v <= 10 and cb.get(k, ABSENT) <= 10:
                url_moves.append(abs(cb[k] - v))
    url_moves = np.asarray(url_moves)
    movers = {
        "adjacent_swaps": adjacent,
        "flips_with_equal_moves": int(fl["driver"].str.endswith("|tie").sum()),
        "distinct_driver_urls": int(distinct),
        "flips_per_driver_top5": per_driver.head(5).tolist(),
        "flips_by_driver_move": fl.groupby("driver_move").size().to_dict(),
        "flips_whose_driver_moved_3plus": int((fl["driver_move"] >= 3).sum()),
        "urls_in_both_top10": int(len(url_moves)),
        "url_move_counts": {str(k): int((url_moves == k).sum()) for k in range(0, 4)} | {"4+": int((url_moves >= 4).sum())},
    }
    say(f"\nMovers: adjacent swaps (distance 1 on both days) {adjacent} of 44; flips where both URLs moved equally {movers['flips_with_equal_moves']}; distinct URLs that did most of the moving {distinct}; flips per driver (top 5) {movers['flips_per_driver_top5']}; flips by the driver's move size {movers['flips_by_driver_move']}")
    say(f"URL moves Nov 16 to Nov 17 for the {len(url_moves)} URLs in both top 10s: {movers['url_move_counts']}")
    big = []
    for drv in per_driver.index:
        query, key = drv.split("|", 1)
        s = searches[query]
        d1 = tuple(p.get(key, ABSENT) for p in s.eve)
        d2 = tuple(p.get(key, ABSENT) for p in s.d2)
        mrn = s.morning.get(key, ABSENT)
        earl = tuple(s.earlier[d].get(key, ABSENT) for d in EARLIER)
        move = abs(cons_of(s.d2)[key] - cons_of(s.eve)[key])
        big.append({"flips": int(per_driver[drv]), "move": int(move), "nov16_evening_pulls": d1, "nov16_morning": mrn, "nov17_pulls": d2, "nov6_9_11_13": earl, "day1_spread": max(d1) - min(d1), "day2_spread": max(d2) - min(d2)})
    big.sort(key=lambda r: -r["move"])
    say("Pull positions of the URLs that drove the flips (11 = absent; URL names left out), largest consensus move first:")
    table(["flips driven", "consensus move", "Nov 16 evening pulls", "Nov 16 morning", "Nov 17 pulls", "Nov 6, 9, 11, 13 pulls"], [[r["flips"], r["move"], r["nov16_evening_pulls"], r["nov16_morning"], r["nov17_pulls"], r["nov6_9_11_13"]] for r in big])
    movers["driver_positions"] = big
    bimodal = [r for r in big if r["move"] >= 3]
    movers["drivers_moving_3plus"] = len(bimodal)
    movers["drivers_moving_3plus_with_day1_or_day2_spread_ge_4"] = sum(1 for r in bimodal if max(r["day1_spread"], r["day2_spread"]) >= 4)
    say(f"Drivers that moved 3+ places: {len(bimodal)}; of them, already spread over 4+ places across one day's pulls: {movers['drivers_moving_3plus_with_day1_or_day2_spread_ge_4']}")
    q2["movers"] = movers

    # concentration by search
    per_q = obs.groupby("query")["flip"].agg(["sum", "size"])
    per_q = per_q.reindex(queries, fill_value=0)
    prev = rs.flip_pairs(cons, "2026-11-13", DAY1)
    prev = prev[prev["query"].isin(queries)].groupby("query")["flipped"].mean().reindex(queries)
    null_q = pq["null_f"].reindex(queries)
    rho_null = stats.spearmanr(per_q["sum"], null_q)
    ok = prev.notna()
    rho_prev = stats.spearmanr((per_q["sum"] / per_q["size"].replace(0, np.nan))[ok], prev[ok], nan_policy="omit")
    top5 = per_q["sum"].sort_values(ascending=False).head(5)
    conc = {
        "searches_with_a_flip": int((per_q["sum"] > 0).sum()),
        "flips_in_top5_searches": int(top5.sum()),
        "top5_flip_counts": top5.tolist(),
        "spearman_obs_flips_vs_null_expected": [float(rho_null.statistic), float(rho_null.pvalue)],
        "spearman_flip_rate_vs_nov13_16_flip_rate": [float(rho_prev.statistic), float(rho_prev.pvalue)],
    }
    say(f"Concentration: {conc['searches_with_a_flip']} of {len(queries)} searches have a flip; the top 5 searches hold {conc['flips_in_top5_searches']} of 44 ({conc['top5_flip_counts']}). Spearman of per-search flips with the per-search null expectation: {conc['spearman_obs_flips_vs_null_expected'][0]:.3f} (p {conc['spearman_obs_flips_vs_null_expected'][1]:.4f}); per-search flip rate vs the Nov 13 to 16 flip rate: {conc['spearman_flip_rate_vs_nov13_16_flip_rate'][0]:.3f} (p {conc['spearman_flip_rate_vs_nov13_16_flip_rate'][1]:.4f})")
    q2["concentration"] = conc
    results["q2_structure"] = q2

    tick("Q2 done")
    # ================================================================ Q3
    say("\n## Q3. Ceiling: the precision-budget oracle (call in order of P(flip | state, q) while expected precision >= 0.5)")
    # (a) distribution of q
    B3 = bmaj(Q, 3)
    qc = np.maximum(B3, 1 - B3)  # P(a 3-pull consensus shows the pair's pull-majority order)
    bins = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0001]
    bin_labels = ["0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-0.95", "0.95-0.99", "0.99-1"]
    say("\n(a) Distribution of q (P that a 3-pull consensus shows the pair's majority order) under each stationary G. 'pairs' = share of scored pairs; 'flips' = share of expected flips (weight 2q(1-q)).")
    dist_rows = []
    q3a = {}
    G_NAMES = ("npmle_stationary", "beta_stationary", "lnorm_stationary")
    for name in G_NAMES:
        w = fits[name]["w"]
        idx = np.digitize(qc, bins) - 1
        mass = [float(w[idx == i].sum()) for i in range(len(bin_labels))]
        fw = w * 2 * qc * (1 - qc)
        fmass = [float(fw[idx == i].sum() / fw.sum()) for i in range(len(bin_labels))]
        q3a[name] = {"pairs": dict(zip(bin_labels, mass, strict=True)), "flips": dict(zip(bin_labels, fmass, strict=True)), "flip_weighted_mean_q": float((fw * qc).sum() / fw.sum()), "lead_formula_mean_of_1_over_2q_flip_weighted": float((fw / (2 * qc)).sum() / fw.sum())}
        dist_rows.append([f"{name}, pairs"] + [f"{m:.3f}" for m in mass] + [f"{q3a[name]['flip_weighted_mean_q']:.3f}", f"{q3a[name]['lead_formula_mean_of_1_over_2q_flip_weighted']:.3f}"])
        dist_rows.append([f"{name}, flips"] + [f"{m:.3f}" for m in fmass] + ["", ""])
    table(["G"] + bin_labels + ["flip-weighted mean q", "flip-weighted mean of 1/(2q)"], dist_rows)
    say("Per-pair posterior mean of q from all six votes, counts of pairs (the six votes include the outcome day, so this is descriptive only):")
    post_rows = []
    q3a["posterior_q_counts"] = {}
    for name in ("npmle_stationary", "beta_stationary"):
        post6 = binom_lik(obs["eve_rev"] + obs["d2_rev"], obs["eve_inf"] + obs["d2_inf"]) * fits[name]["w"]
        post6 /= post6.sum(1, keepdims=True)
        qpost = post6 @ qc
        hf = np.histogram(qpost[flips_mask.to_numpy()], bins=bins)[0]
        hn = np.histogram(qpost[~flips_mask.to_numpy()], bins=bins)[0]
        q3a["posterior_q_counts"][name] = {"flips": dict(zip(bin_labels, hf.tolist(), strict=True)), "non_flips": dict(zip(bin_labels, hn.tolist(), strict=True))}
        post_rows.append([f"{name}: 44 flips"] + hf.tolist())
        post_rows.append([f"{name}: 723 non-flips"] + hn.tolist())
    table(["prior and pairs"] + bin_labels, post_rows)

    # true-q oracle bound under each G (stationary world, and the change world at the profile upper bound)
    say("\nModel bound: oracle that knows each pair's true q (and pi), 3-pull consensus on both days. Recall at expected precision >= 0.5:")
    bound_rows = []
    q3_bound = {}
    for fam in ("npmle", "beta", "lnorm"):
        st = true_q_oracle(fits[f"{fam}_stationary"], pi=0.0)
        ch = true_q_oracle(fits_ub[f"{fam}_fresh"])
        q3_bound[fam] = {"stationary_world": st, "change_world_at_upper_pi": ch, "upper_pi": ub[f"{fam}_fresh"]}
        bound_rows.append([fam, f"{st['recall']:.3f}", f"{st['precision']:.3f}", f"{st['flip_rate']:.4f}", f"{ub[f'{fam}_fresh']:.4f}", f"{ch['recall']:.3f}", f"{ch['flip_rate']:.4f}"])
    sw = true_q_oracle(fits_ub["npmle_swap"])
    q3_bound["npmle_swap_at_upper_pi"] = sw
    bound_rows.append(["npmle, swap change", "-", "-", "-", f"{ub['npmle_swap']:.4f}", f"{sw['recall']:.3f}", f"{sw['flip_rate']:.4f}"])
    dr = true_q_oracle(fits_ub["beta_drift"])
    q3_bound["beta_drift_at_upper_tau"] = dr
    bound_rows.append(["beta, drift change", "-", "-", "-", f"tau {ub['beta_drift']:.3f}", f"{dr['recall']:.3f}", f"{dr['flip_rate']:.4f}"])
    table(["G family", "stationary world: recall", "precision", "flip rate", "upper-bound pi", "recall with change at that pi", "flip rate"], bound_rows)

    # (b), (c) empirical oracles on the 767 pairs
    say("\n(b) and (c) Empirical precision-budget oracles on the 767 real pairs. P(flip) = posterior mean of B3(p_y) given the votes; whole tie groups are called while the mean P stays >= 0.5; then scored on the real flips.")
    w_np6 = fits["npmle_stationary"]["w"]
    w_beta6 = fits["beta_stationary"]["w"]
    honest_fits = {}
    for label, sets in (("day1_3", ["eve"]), ("day1_4", ["eve", "m"]), ("all_8", ["eve", "m", "earl"])):
        s_ = sum(obs[f"{n}_rev"].to_numpy() for n in sets)
        n_ = sum(obs[f"{n}_inf"].to_numpy() for n in sets)
        honest_fits[label] = fit_stationary(binom_lik(s_, n_), ones)["w"]
    jeff = beta_mirror(0.5, 0.5)
    unif = np.full(GRID, 1 / GRID)
    pi_aware = ("beta G, pi at upper bound (reads day two)", fit_hi["w"], pi_hi)
    variants = [
        ("cheat: Nov 16 voting + Nov 17 (6 votes)", ["eve", "d2"], [("NPMLE G (6 votes)", w_np6, 0.0), ("beta G (6 votes)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0), ("uniform", unif, 0.0)]),
        ("cheat: all Nov 16 + Nov 17 (7 votes)", ["eve", "m", "d2"], [("NPMLE G (6 votes)", w_np6, 0.0), ("beta G (6 votes)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0)]),
        ("cheat: all 11 pulls Nov 6 to Nov 17", ["eve", "m", "d2", "earl"], [("NPMLE G (6 votes)", w_np6, 0.0), ("beta G (6 votes)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0)]),
        ("honest: Nov 16 voting pulls (3 votes)", ["eve"], [("NPMLE G fit on these votes", honest_fits["day1_3"], 0.0), ("beta G (prior reads day two)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0)]),
        ("honest: all Nov 16 pulls (4 votes)", ["eve", "m"], [("NPMLE G fit on these votes", honest_fits["day1_4"], 0.0), ("beta G (prior reads day two)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0), pi_aware]),
        ("honest: all 8 pulls up to day one", ["eve", "m", "earl"], [("NPMLE G fit on these votes", honest_fits["all_8"], 0.0), ("beta G (prior reads day two)", w_beta6, 0.0), ("Jeffreys", jeff, 0.0), pi_aware]),
    ]
    q3_emp = {}
    emp_rows = []
    for label, sets, priors in variants:
        for pname, w, pi in priors:
            score = oracle_scores(obs, sets, w, pi)
            res = realized_budget(obs, score, U)
            q3_emp[f"{label} | {pname}"] = res
            ph = res["posthoc_best_realized"]
            emp_rows.append([label, pname, res["calls"], res["right"], fr(res["recall"]), fr(res["precision"]), f"{res['expected_recall']:.3f}", f"{ph['recall']:.3f} ({ph.get('right', 0)}/{ph['calls']})"])
    table(["votes the oracle sees", "prior", "calls", "right", "realized recall [CI]", "realized precision [CI]", "oracle's own expected recall", "post-hoc best realized recall at precision >= 0.5 (right/calls)"], emp_rows)
    say("\nPlain strict-majority rule (call when more of the listed votes show y above x than x above y):")
    maj_rows = []
    q3_maj = {}
    for label, sets in (("cheat 6", ["eve", "d2"]), ("cheat 7", ["eve", "m", "d2"]), ("cheat 11", ["eve", "m", "d2", "earl"]), ("honest 3", ["eve"]), ("honest 4", ["eve", "m"]), ("honest 8", ["eve", "m", "earl"])):
        s_ = sum(obs[f"{n}_rev"] for n in sets)
        n_ = sum(obs[f"{n}_inf"] for n in sets)
        df = obs[["query", "flip"]].copy()
        df["call"] = (2 * s_ > n_).astype(int)
        df["tie"] = (2 * s_ == n_).astype(int)
        df["tp"] = df["call"] * df["flip"]
        rec = rate_ci(df, "tp", "flip", U)
        prec = rate_ci(df, "tp", "call", U)
        q3_maj[label] = {"recall": rec, "precision": prec, "ties": int(df["tie"].sum()), "flips_on_ties": int((df["tie"] * df["flip"]).sum())}
        maj_rows.append([label, int(df["call"].sum()), fr(rec), fr(prec), f"{int(df['tie'].sum())} ({int((df['tie'] * df['flip']).sum())} flips)"])
    table(["votes", "calls", "recall", "precision", "exact ties (flips among them)"], maj_rows)

    # (d) more stationary history, exact under the model
    say("\n(d) Model: Bayes precision-budget oracle that knows G (and pi) and sees k extra stationary pulls before day one plus day one's 3 pulls. Exact sums over vote outcomes. Change world = pi at the profile upper bound with G refit at that pi.")
    hist_rows = []
    q3_hist = {}
    hist_models = [("npmle", fits["npmle_stationary"], fits_ub["npmle_fresh"]), ("beta", fits["beta_stationary"], fits_ub["beta_fresh"]), ("lnorm", fits["lnorm_stationary"], fits_ub["lnorm_fresh"]), ("beta, drift change", fits["beta_stationary"], fits_ub["beta_drift"])]
    for fam, f_st, f_ch in hist_models:
        q3_hist[fam] = {}
        for k in HISTORY_EXTRA:
            st = history_oracle(f_st, k, pi=0.0)
            ch = history_oracle(f_ch, k)
            q3_hist[fam][k] = {"stationary_world": st, "change_world": ch}
            hist_rows.append([fam, k, k + 3, f3(st["recall"]), f3(st["precision"]), f3(st["call_share"], 4), f3(ch["recall"]), f3(ch["precision"])])
        st_inf, ch_inf = true_q_oracle(f_st, pi=0.0), true_q_oracle(f_ch)
        q3_hist[fam]["inf"] = {"stationary_world": st_inf, "change_world": ch_inf}
        hist_rows.append([fam, "inf", "inf", f3(st_inf["recall"]), f3(st_inf["precision"]), f3(st_inf["call_share"], 4), f3(ch_inf["recall"]), f3(ch_inf["precision"])])
    table(["G family", "extra pulls before day one", "total pulls seen", "stationary world: recall", "precision", "share of pairs called", "change world (upper bound): recall", "precision"], hist_rows)
    mc = _mc_history_check(fit_hi, 6)
    exact = history_oracle(fit_hi, 6)
    say(f"Monte Carlo cross-check (beta G at upper pi, k=6, 400000 simulated pairs, whole-pair cut): recall {f3(mc['recall'])}, precision {f3(mc['precision'])}; exact: recall {f3(exact['recall'])}, precision {f3(exact['precision'])}")
    dec = {}
    for fam in q3_hist:
        h = q3_hist[fam]
        dec[fam] = {
            "true_q_stationary": h["inf"]["stationary_world"]["recall"],
            "k0_stationary": h[0]["stationary_world"]["recall"],
            "k1_stationary": h[1]["stationary_world"]["recall"],
            "k5_stationary": h[5]["stationary_world"]["recall"],
            "k0_change": h[0]["change_world"]["recall"],
            "k5_change": h[5]["change_world"]["recall"],
            "true_q_change": h["inf"]["change_world"]["recall"],
        }
    say("Gap decomposition, recall at precision >= 0.5 (k = extra stationary pulls before day one):")
    table(["G family"] + list(dec["beta"].keys()), [[fam] + [f3(v) for v in d.values()] for fam, d in dec.items()])
    floor_rows = []
    q3_floor = {}
    for floor in (0.5, 0.55, 0.6, 0.7):
        q3_floor[floor] = {}
        row = [floor]
        for k in (1, 6, 12, 18, "inf"):
            r = true_q_oracle(fits["beta_stationary"], pi=0.0, floor=floor) if k == "inf" else history_oracle(fits["beta_stationary"], k, pi=0.0, floor=floor)
            q3_floor[floor][k] = r
            row.append(f3(r["recall"]))
        floor_rows.append(row)
    say("Recall when the oracle keeps a stricter expected precision (beta G, stationary world), by extra pulls before day one:")
    table(["precision floor", "k=1", "k=6", "k=12", "k=18", "true q"], floor_rows)
    results["q3_precision_floors"] = q3_floor
    results["q3_ceiling"] = {"a_q_distribution": q3a, "true_q_bound": q3_bound, "empirical_budget_oracles": q3_emp, "strict_majority": q3_maj, "d_history": q3_hist, "gap_decomposition": dec, "mc_check": {"mc": mc, "exact": exact}}

    tick("Q3 done")
    # ================================================================ Q4
    say("\n## Q4. More pulls or a longer horizon")
    proj_rows = []
    q4_proj = {}
    proj_models = [("npmle_stationary", fits["npmle_stationary"]), ("beta_stationary", fits["beta_stationary"]), ("lnorm_stationary", fits["lnorm_stationary"]), (f"beta_fresh at upper pi {pi_hi:.4f}", fit_hi), (f"npmle_swap at upper pi {ub['npmle_swap']:.4f}", fits_ub["npmle_swap"]), (f"beta_drift at upper tau {ub['beta_drift']:.3f}", fits_ub["beta_drift"])]
    for name, f in proj_models:
        q4_proj[name] = []
        for n in list(N_PULLS) + [float("inf")]:
            p = project(f, n)
            q4_proj[name].append(p)
            seen = p["share_of_true_changes_seen_as_flips"]
            proj_rows.append([name, p["pulls"], f"{p['flip_rate']:.4f}", f"{p['real_flip_rate']:.4f}", f"{p['noise_flip_rate']:.4f}", "n/a" if p["noise_share"] is None else f"{p['noise_share']:.3f}", "n/a" if seen is None else f"{seen:.3f}"])
    table(["model", "pulls per day", "flip rate per scored pair", "real flips", "noise flips", "noise share of flips", "true changes seen as flips"], proj_rows)
    proj_ci = {}
    for n in (3, 5, 7, 9, float("inf")):
        vals = [project(f, n) for f in boot]
        proj_ci[str(n)] = {
            "noise_share": bci([v["noise_share"] if v["noise_share"] is not None else np.nan for v in vals]),
            "flip_rate": bci([v["flip_rate"] for v in vals]),
            "real_flip_rate": bci([v["real_flip_rate"] for v in vals]),
            "noise_flip_rate": bci([v["noise_flip_rate"] for v in vals]),
        }
    say("Query-bootstrap 95% intervals, beta fresh-change model refit per draw:")
    table(["pulls per day", "flip rate", "noise flip rate", "real flip rate", "noise share"], [[k] + [f"[{v[c][0]:.4f}, {v[c][1]:.4f}]" for c in ("flip_rate", "noise_flip_rate", "real_flip_rate", "noise_share")] for k, v in proj_ci.items()])
    # variogram
    vg = variogram(searches)
    floor_label = BUCKET_LABELS[0]
    vg_rows = []
    q4_vg = {}
    for label in BUCKET_LABELS:
        sub = vg[vg["bucket"] == label]
        if sub.empty:
            continue
        both = pd.DataFrame({"query": vg["query"], "dis_b": np.where(vg["bucket"] == label, vg["dis"], 0), "cmp_b": np.where(vg["bucket"] == label, vg["cmp"], 0), "dis_0": np.where(vg["bucket"] == floor_label, vg["dis"], 0), "cmp_0": np.where(vg["bucket"] == floor_label, vg["cmp"], 0)})
        sums = per_query(both, ["dis_b", "cmp_b", "dis_0", "cmp_0"], queries)
        D = qboot(sums, lambda d: d["dis_b"] / d["cmp_b"])
        exc = qboot(sums, lambda d: d["dis_b"] / d["cmp_b"] - d["dis_0"] / d["cmp_0"])
        share = qboot(sums, lambda d: 1 - (d["dis_0"] / d["cmp_0"]) / (d["dis_b"] / d["cmp_b"]))
        piv = qboot(sums, lambda d: (d["dis_b"] / d["cmp_b"] - d["dis_0"] / d["cmp_0"]) / (0.5 - d["dis_0"] / d["cmp_0"]))
        q4_vg[label] = {"pull_pairs": len(sub), "mean_gap_h": float(sub["gap_h"].mean()), "url_pairs_compared": int(sub["cmp"].sum()), "disagreement": D, "excess_over_same_session": exc, "real_change_share": share, "implied_pi": piv, "pull_pair_types": sorted({f"{a}~{b}" for a, b in zip(sub["a"], sub["b"], strict=True)})}
        vg_rows.append([label, len(sub), f"{sub['gap_h'].mean():.1f}", int(sub["cmp"].sum()), fv(D, 4), fv(exc, 4), fv(share), fv(piv)])
    say("\nSingle-pull pair disagreement by time gap (URL pairs present in both pulls; floor = same-session pulls on Nov 16 evening and Nov 17):")
    table(["gap", "pull pairs", "mean gap h", "URL pairs compared", "disagreement [CI]", "minus same-session floor", "real-change share of disagreement", "implied pi (fresh model)"], vg_rows)
    say("\nConsecutive single-pull days (one pull each; Nov 16 uses the morning pull):")
    cons_rows = []
    q4_consec = {}
    for a, b in (("11-06", "11-09"), ("11-09", "11-11"), ("11-11", "11-13"), ("11-13", "11-16m")):
        sel = (vg["a"] == a) & (vg["b"] == b)
        sub = vg[sel]
        both = pd.DataFrame({"query": vg["query"], "dis_b": np.where(sel, vg["dis"], 0), "cmp_b": np.where(sel, vg["cmp"], 0), "dis_0": np.where(vg["bucket"] == floor_label, vg["dis"], 0), "cmp_0": np.where(vg["bucket"] == floor_label, vg["cmp"], 0)})
        sums = per_query(both, ["dis_b", "cmp_b", "dis_0", "cmp_0"], queries)
        D = qboot(sums, lambda d: d["dis_b"] / d["cmp_b"])
        share = qboot(sums, lambda d: 1 - (d["dis_0"] / d["cmp_0"]) / (d["dis_b"] / d["cmp_b"]))
        piv = qboot(sums, lambda d: (d["dis_b"] / d["cmp_b"] - d["dis_0"] / d["cmp_0"]) / (0.5 - d["dis_0"] / d["cmp_0"]))
        q4_consec[f"{a}->{b}"] = {"gap_h": float(sub["gap_h"].mean()), "url_pairs": int(sub["cmp"].sum()), "disagreement": D, "real_change_share": share, "implied_pi": piv}
        cons_rows.append([f"{a} to {b}", f"{sub['gap_h'].mean():.1f}", int(sub["cmp"].sum()), fv(D, 4), fv(share), fv(piv)])
    table(["transition", "gap h", "URL pairs", "disagreement", "real-change share", "implied pi"], cons_rows)
    gap_rows = []
    q4_gap = {}
    for label in BUCKET_LABELS[1:]:
        if label not in q4_vg:
            continue
        pi_g = q4_vg[label]["implied_pi"]["value"]
        pi_g = min(1.0, max(0.0, pi_g))
        cells_ = {str(n): project(fits["beta_stationary"], n, pi=pi_g) for n in (1, 3, 5, 9, float("inf"))}
        q4_gap[label] = {"pi_from_variogram": pi_g, "projections": cells_}
        gap_rows.append([label, f"{pi_g:.3f}"] + [f"{cells_[str(n)]['flip_rate']:.3f} / {cells_[str(n)]['noise_share']:.2f}" if cells_[str(n)]["noise_share"] is not None else f"{cells_[str(n)]['flip_rate']:.3f} / n/a" for n in (1, 3, 5, 9)] + [f"{cells_['inf']['flip_rate']:.3f}"])
    say("\nProjected consensus flip rate / noise share of flips by gap (beta G, pi = the variogram's implied pi for that gap; rough, the variogram's pair population differs from scored pairs):")
    table(["gap", "pi", "1 pull", "3 pulls", "5 pulls", "9 pulls", "infinite pulls (all real)"], gap_rows)
    ref_rows = []
    q4_ref = {}
    for a, b in (("2026-11-06", "2026-11-09"), ("2026-11-09", "2026-11-11"), ("2026-11-11", "2026-11-13"), ("2026-11-13", DAY1), (DAY1, DAY2)):
        fp = rs.flip_pairs(cons, a, b)
        fp = fp[fp["query"].isin(queries)].copy()
        fp["flip"] = fp["flipped"].astype(int)
        fp["one"] = 1
        res = rate_ci(fp, "flip", "one", sorted(fp["query"].unique()))
        q4_ref[f"{a}->{b}"] = res
        ref_rows.append([f"{a[5:]} to {b[5:]}", fr(res, 4)])
    say("\nObserved consensus flip rates (rsdata rule; Nov 6 to 13 are single pulls, Nov 16 and 17 are 3-pull sessions):")
    table(["transition", "flips/pairs (rate) [CI]"], ref_rows)
    results["q4"] = {"projections": q4_proj, "projection_ci_beta_fresh_bootstrap": proj_ci, "variogram": q4_vg, "consecutive_single_pulls": q4_consec, "gap_projection": q4_gap, "observed_transitions": q4_ref}

    tick("Q4 done")
    # ================================================================ Q5
    say("\n## Q5. Pre-specified leads (no thresholds tuned). Lift = flip rate when flagged / flip rate when not flagged.")
    leads = {
        "(a) higher URL entered the top 10 on day one": obs["x_entered"] == "day1",
        "(a') lower URL entered the top 10 on day one (contrast)": obs["y_entered"] == "day1",
        "(b) lower URL's site rose on other searches, Nov 13 to Nov 16": obs["mom_y"] < 0,
        "(b') higher URL's site fell on other searches, Nov 13 to Nov 16": obs["mom_x"] > 0,
        "(b'') diagnostic, reads day two: lower URL's site rose on other searches, Nov 16 to Nov 17": obs["mom2_y"] < 0,
        "(b''') diagnostic, reads day two: higher URL's site fell on other searches, Nov 16 to Nov 17": obs["mom2_x"] > 0,
        "(c) only the last day-one pull shows the reverse order": obs["last_pattern"] == "only the last pull (evening 3) reversed",
        "(c') only the morning pull shows the reverse order": obs["last_pattern"] == "only the morning pull reversed",
        "(c'') only evening pull 1 shows the reverse order": obs["last_pattern"] == "only evening pull 1 reversed",
        "(c''') only evening pull 2 shows the reverse order": obs["last_pattern"] == "only evening pull 2 reversed",
        "(d) habitual: reverse order seen in 2+ of the 8 pulls up to day one": obs["novel"] == "2 or more of 8",
        "(d') once: reverse order seen in exactly 1 of 8": obs["novel"] == "once (1 of 8)",
        "(d'') novel: reverse order never seen in the 8 pulls up to day one": obs["novel"] == "never (0 of 8)",
    }
    lead_rows = []
    q5 = {}
    for name, mask in leads.items():
        df = obs[["query", "flip"]].copy()
        df["f"] = mask.fillna(False).astype(int).to_numpy()
        df["ff"] = df["f"] * df["flip"]
        df["u"] = 1 - df["f"]
        df["uf"] = df["u"] * df["flip"]
        prec = rate_ci(df, "ff", "f", U)
        base = rate_ci(df, "uf", "u", U)
        lift = qboot(per_query(df, ["ff", "f", "uf", "u"], U), lambda d: (d["ff"] / d["f"]) / (d["uf"] / d["u"]))
        recall = rate_ci(df, "ff", "flip", U)
        q5[name] = {"flagged_pairs": int(df["f"].sum()), "flagged_flips": int(df["ff"].sum()), "precision": prec, "unflagged_rate": base, "lift": lift, "recall": recall}
        lead_rows.append([name, int(df["f"].sum()), int(df["ff"].sum()), fr(prec), fr(base), fv(lift, 2), fr(recall)])
    table(["lead", "flagged pairs", "flagged flips", "precision (flip rate if flagged)", "flip rate if not flagged", "lift", "recall"], lead_rows)
    single = obs[obs["last_pattern"].str.startswith("only")]
    ctab = pd.crosstab(single["last_pattern"], single["flip"])
    chi = stats.chi2_contingency(ctab.to_numpy())
    evening = single[single["last_pattern"] != "only the morning pull reversed"]
    chi_e = stats.chi2_contingency(pd.crosstab(evening["last_pattern"], evening["flip"]).to_numpy())
    cdf = obs[["query", "flip"]].copy()
    cdf["e3"] = (obs["last_pattern"] == "only the last pull (evening 3) reversed").astype(int)
    cdf["e2"] = (obs["last_pattern"] == "only evening pull 2 reversed").astype(int)
    cdf["e3f"] = cdf["e3"] * cdf["flip"]
    cdf["e2f"] = cdf["e2"] * cdf["flip"]
    d32 = qboot(per_query(cdf, ["e3f", "e3", "e2f", "e2"], U), lambda d: d["e3f"] / d["e3"] - d["e2f"] / d["e2"])
    say(f"Pairs with exactly one reversed day-one pull: homogeneity across the four pulls chi2 p = {chi.pvalue:.4f}; across the three evening pulls only p = {chi_e.pvalue:.4f} (pairs treated as independent). Flip rate when only evening 3 dissents minus when only evening 2 dissents: {fv(d32)} (query bootstrap). Evening 2 sits between evening 1 and evening 3 in time, so recency does not explain the gap.")
    q5["single_dissent_homogeneity"] = {"chi2_p_four_pulls": float(chi.pvalue), "chi2_p_evening_pulls": float(chi_e.pvalue), "e3_minus_e2": d32}
    undefined_b = int(obs["mom_y"].isna().sum())
    say(f"Pairs whose lower URL's site appears on no other search (lead b undefined, counted as not flagged): {undefined_b}")
    q5["undefined_site_momentum_pairs"] = undefined_b
    results["q5_leads"] = q5

    results["runtime_seconds"] = time.time() - start
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(to_jsonable(results), indent=1) + "\n")
    say(f"\nWrote {OUT_DIR / 'results.json'} in {time.time() - start:.0f} s")
    (OUT_DIR / "stdout.txt").write_text("\n".join(LINES) + "\n")


def _mc_history_check(fit: dict, k_extra: int, n_pairs: int = 400_000) -> dict:
    """Simulate pairs from the fitted model and apply the exact unit table, to check history_oracle."""
    rng = np.random.default_rng(mt.BOOT_SEED)
    w, K = fit["w"], kernel(fit)
    g = rng.choice(GRID, size=n_pairs, p=w)
    q1 = Q[g]
    r1 = rng.binomial(3, q1)
    h = rng.binomial(k_extra, q1)
    cum = np.cumsum(K, axis=1)
    g2 = np.minimum((cum[g] < rng.random(n_pairs)[:, None]).sum(axis=1), GRID - 1)
    r2 = rng.binomial(3, Q[g2])
    state_b = r1 >= 2
    flip = np.where(state_b, r2 <= 1, r2 >= 2)
    KB = K @ bmaj(Q, 3)
    table_f = {}
    for a in range(4):
        for b in range(k_extra + 1):
            like = w * stats.binom.pmf(a, 3, Q) * stats.binom.pmf(b, k_extra, Q)
            p_b = float((like / like.sum()) @ KB)
            table_f[(a, b)] = (1 - p_b) if a >= 2 else p_b
    score = np.array([table_f[(a, b)] for a, b in zip(r1, h, strict=True)])
    order = np.argsort(-score, kind="stable")
    cum_score = np.cumsum(score[order]) / np.arange(1, n_pairs + 1)
    keep = order[: int(np.searchsorted(-cum_score, -0.5, side="right"))]
    return {"recall": float(flip[keep].sum() / flip.sum()), "precision": float(flip[keep].mean()) if len(keep) else None}


if __name__ == "__main__":
    main()
