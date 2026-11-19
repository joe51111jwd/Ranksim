"""Target 3 (pairwise order of movers): a URL scorer built from history only.

Convention: LOWER score = predicted BETTER rank on day_b. The induced order of
two URLs is the order of their scores. Every output is PREDICTED.

The scorer is "yesterday's consensus position plus a bounded correction":

    score(k) = cons_a(k) + lam * (target(k) - cons_a(k)) + 1e-6 * cons_a(k)

cons_a   consensus position on the query's last pulled day before day_b
         (rsdata.consensus_table on history only; 11 if not in that top 10).
target   a smoothed position from history (see TARGETS below).
lam      0 is pure persistence. For a pair x above y on day_a the scorer
         overrides yesterday's order exactly when
             target(x) - target(y) > (1 - lam) / lam * (cons_a(y) - cons_a(x)),
         so lam is the override-confidence knob of the trade-off curve.
The 1e-6 term breaks exact ties toward yesterday's order.

The score of a key depends only on history and that key (and, for the cross
query target, on other queries' history). It never depends on which other keys
are requested, so the requested key set (chosen by the evaluator from day_b)
cannot leak day_b information into a score.

Fitted parameters live in evals/claude/t3-movers/params.json and are loaded
from there by default so the file can be frozen by sha256.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:  # run as package (python -m scripts.claude.x) or with the repo root on sys.path
    from scripts.claude import rsdata
except ImportError:  # pragma: no cover
    import rsdata  # type: ignore

ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = ROOT / "evals" / "claude" / "t3-movers" / "params.json"
ABSENT = rsdata.ABSENT
TIE_EPS = 1e-6

# target names understood by score_from_features
TARGETS = (
    "vote_mean_a",  # mean of day_a's voting pulls (absent = 11)
    "all_mean_a",  # mean of every top-10 pull on day_a (voting or not, plus the deep list if flagged)
    "ew_mean",  # exponentially weighted mean of every history pull, half life in days
    "prev_mean",  # mean of every pull on days before day_a (mean reversion to the longer past)
    "trend",  # cons_a plus slope * gap from a least-squares line through daily means
    "deep",  # latest deep-list position before day_b
    "xq_mean",  # mean consensus position of the same URL on the other queries on day_a
)


def load_params(path: Path | str = PARAMS_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _day_index(day: str) -> int:
    return int(pd.Timestamp(day).value // (86400 * 10**9))


def _assert_history(history_rows: pd.DataFrame, day_b: str) -> None:
    if len(history_rows) and str(history_rows["day"].max()) >= str(day_b):
        raise AssertionError(f"history contains day {history_rows['day'].max()} >= day_b {day_b}")


class HistoryContext:
    """Everything the scorer may read for one prediction day. Built from rows dated before day_b."""

    def __init__(self, history_rows: pd.DataFrame, day_b: str, *, deep_as_pull: bool = False):
        _assert_history(history_rows, day_b)
        self.day_b = str(day_b)
        self.rows = history_rows
        self.pulls = rsdata.pulls_by_query_day(history_rows)
        self.deep = rsdata.deep_table(history_rows)
        self.deep_as_pull = deep_as_pull
        self.days: dict[str, list[str]] = defaultdict(list)
        for query, day in sorted(self.pulls):
            self.days[query].append(day)
        self._cons_cache: dict[tuple[str, str], dict[str, int]] = {}
        self._feat_cache: dict[str, pd.DataFrame] = {}
        self._groups: dict[str, str] | None = None

    def groups(self) -> dict[str, str]:
        """Query group labels from history sources only."""
        if self._groups is None:
            self._groups = rsdata.query_groups(self.rows)
        return self._groups

    def day_a(self, query: str) -> str | None:
        days = self.days.get(query)
        return days[-1] if days else None

    def cons_on(self, query: str, day: str) -> dict[str, int]:
        cached = self._cons_cache.get((query, day))
        if cached is not None:
            return cached
        pulls = self.pulls.get((query, day), [])
        vote = rsdata.consensus_session(pulls)
        keys: list[str] = []
        for pull in vote:
            for key in pull.ranks:
                if key not in keys:
                    keys.append(key)
        out = {key: rsdata.consensus_position([pull.ranks.get(key, ABSENT) for pull in vote]) for key in keys}
        self._cons_cache[(query, day)] = out
        return out

    def _deep_pull(self, query: str, day: str) -> dict[str, int] | None:
        if not self.deep_as_pull or self.deep.empty:
            return None
        part = self.deep[(self.deep["query"] == query) & (self.deep["day"] == day)]
        if part.empty:
            return None
        return {key: int(pos) for key, pos in zip(part["key"], part["deep_pos"], strict=True)}

    def features(self, query: str) -> pd.DataFrame:
        """One row per key seen for the query in history. Index = key."""
        cached = self._feat_cache.get(query)
        if cached is not None:
            return cached
        day_a = self.day_a(query)
        if day_a is None:
            frame = pd.DataFrame(columns=["cons_a"])
            self._feat_cache[query] = frame
            return frame
        b_index = _day_index(self.day_b)
        cons_a = self.cons_on(query, day_a)
        vote_a = rsdata.consensus_session(self.pulls[(query, day_a)])
        vote_ids = {(pull.source, pull.fetched_at) for pull in vote_a}

        # every history pull of this query: (day, age_days, is_day_a, is_vote, ranks)
        pull_list = []
        for day in self.days[query]:
            for pull in self.pulls[(query, day)]:
                pull_list.append((day, b_index - _day_index(day), day == day_a, (pull.source, pull.fetched_at) in vote_ids and day == day_a, pull.ranks))
            deep_pull = self._deep_pull(query, day)
            if deep_pull is not None:
                capped = {key: min(pos, ABSENT) for key, pos in deep_pull.items() if pos <= 10}
                pull_list.append((day, b_index - _day_index(day), day == day_a, False, capped))

        keys: list[str] = []
        for _, _, _, _, ranks in pull_list:
            for key in ranks:
                if key not in keys:
                    keys.append(key)

        deep_best: dict[str, int] = {}
        if not self.deep.empty:
            part = self.deep[self.deep["query"] == query]
            if not part.empty:
                latest = part["day"].max()
                part = part[part["day"] == latest]
                deep_best = {key: int(pos) for key, pos in zip(part["key"], part["deep_pos"], strict=True)}

        records = []
        for key in keys:
            vote_pos = [ranks.get(key, ABSENT) for _, _, is_a, is_vote, ranks in pull_list if is_vote]
            all_a = [ranks.get(key, ABSENT) for _, _, is_a, _, ranks in pull_list if is_a]
            prev = [ranks.get(key, ABSENT) for _, _, is_a, _, ranks in pull_list if not is_a]
            ages = np.array([age for _, age, _, _, _ in pull_list], dtype=float)
            pos = np.array([ranks.get(key, ABSENT) for _, _, _, _, ranks in pull_list], dtype=float)
            # daily means for the trend line
            daily: dict[str, list[int]] = defaultdict(list)
            for day, _, _, _, ranks in pull_list:
                daily[day].append(ranks.get(key, ABSENT))
            records.append(
                {
                    "key": key,
                    "day_a": day_a,
                    "gap_b": b_index - _day_index(day_a),
                    "cons_a": int(cons_a.get(key, ABSENT)),
                    "vote_mean_a": float(np.mean(vote_pos)) if vote_pos else float(ABSENT),
                    "all_mean_a": float(np.mean(all_a)) if all_a else float(ABSENT),
                    "n_vote_a": len(vote_pos),
                    "n_all_a": len(all_a),
                    "prev_mean": float(np.mean(prev)) if prev else math.nan,
                    "n_prev": len(prev),
                    "_ages": ages,
                    "_pos": pos,
                    "_daily": {day: float(np.mean(values)) for day, values in daily.items()},
                    "deep": float(min(deep_best.get(key, 31), 31)) if deep_best else math.nan,
                }
            )
        frame = pd.DataFrame.from_records(records).set_index("key")
        self._feat_cache[query] = frame
        return frame

    def cross_query(self, query: str, keys: list[str], same_group: dict[str, str] | None) -> dict[str, float]:
        """Mean consensus position of each key on other queries (same group if labels given), each on its own last day."""
        out: dict[str, list[int]] = defaultdict(list)
        group = same_group.get(query) if same_group else None
        wanted = set(keys)
        for other in self.days:
            if other == query:
                continue
            if same_group is not None and same_group.get(other) != group:
                continue
            cons = self.cons_on(other, self.days[other][-1])
            for key, pos in cons.items():
                if key in wanted and pos <= 10:
                    out[key].append(pos)
        return {key: float(np.mean(values)) for key, values in out.items()}


PAIR_FEATURES = (
    "inv_gap",  # 1 / (cons_a(y) - cons_a(x))
    "top",  # cons_a(x)
    "rev_vote",  # share of day_a voting pulls with y strictly above x (ties 0.5)
    "rev_a",  # same over every day_a top-10 pull (voting or not)
    "n_a",  # number of day_a top-10 pulls
    "rev_prev",  # share of pulls on days before day_a with y above x (ties 0.5), 0.5 if none
    "n_prev",  # number of pulls before day_a
    "rev_last",  # +1 if y above x on the latest earlier day (daily mean), -1 if below, 0 tie or none
    "d_vote",  # vote_mean_a(y) - vote_mean_a(x)
    "d_ew2",  # ew_mean(h=2)(y) - ew_mean(h=2)(x)
    "d_prev",  # prev_mean(y) - prev_mean(x), 0 if no earlier pulls
    "d_deep",  # deep(y) - deep(x), 0 if no deep list
    "d_xq",  # xq_mean(y) - xq_mean(x) (falls back to cons_a)
    "gap_days",  # calendar days from day_a to day_b
)


def _rev_share(pos_x: np.ndarray, pos_y: np.ndarray) -> float:
    if len(pos_x) == 0:
        return 0.5
    return float(np.mean(np.where(pos_y < pos_x, 1.0, np.where(pos_y == pos_x, 0.5, 0.0))))


def pair_features(ctx: HistoryContext, query: str, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Diagnostic pair features for (x, y) with x above y in day_a's consensus. History only.

    Used by the design eval to test whether any pair-level evidence isolates
    flips. Not used by score_urls unless params name a linear URL model.
    """
    feat = ctx.features(query)
    day_a = ctx.day_a(query)
    ew2 = {key: _ew_mean(a, p, 2.0) for key, a, p in zip(feat.index, feat["_ages"], feat["_pos"], strict=True)}
    xq = ctx.cross_query(query, list(feat.index), ctx.groups())
    vote_ids = {(pull.source, pull.fetched_at) for pull in rsdata.consensus_session(ctx.pulls[(query, day_a)])}
    a_pulls = ctx.pulls[(query, day_a)]
    vote_pulls = [pull for pull in a_pulls if (pull.source, pull.fetched_at) in vote_ids]
    prev_days = [day for day in ctx.days[query] if day < day_a]
    prev_pulls = [pull for day in prev_days for pull in ctx.pulls[(query, day)]]
    last_pulls = ctx.pulls[(query, prev_days[-1])] if prev_days else []
    out = []
    for x, y in pairs:
        cx, cy = int(feat.loc[x, "cons_a"]), int(feat.loc[y, "cons_a"])

        def arr(pulls, key):
            return np.array([pull.ranks.get(key, ABSENT) for pull in pulls], dtype=float)

        last_x, last_y = arr(last_pulls, x), arr(last_pulls, y)
        if len(last_x):
            diff = float(np.mean(last_x) - np.mean(last_y))
            rev_last = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
        else:
            rev_last = 0.0
        dx, dy = feat.loc[x, "deep"], feat.loc[y, "deep"]
        px, py = feat.loc[x, "prev_mean"], feat.loc[y, "prev_mean"]
        out.append(
            {
                "key_x": x,
                "key_y": y,
                "inv_gap": 1.0 / max(cy - cx, 1),
                "top": float(cx),
                "rev_vote": _rev_share(arr(vote_pulls, x), arr(vote_pulls, y)),
                "rev_a": _rev_share(arr(a_pulls, x), arr(a_pulls, y)),
                "n_a": float(len(a_pulls)),
                "rev_prev": _rev_share(arr(prev_pulls, x), arr(prev_pulls, y)),
                "n_prev": float(len(prev_pulls)),
                "rev_last": rev_last,
                "d_vote": float(feat.loc[y, "vote_mean_a"] - feat.loc[x, "vote_mean_a"]),
                "d_ew2": float(ew2[y] - ew2[x]),
                "d_prev": 0.0 if (math.isnan(px) or math.isnan(py)) else float(py - px),
                "d_deep": 0.0 if (math.isnan(dx) or math.isnan(dy)) else float(dy - dx),
                "d_xq": float(xq.get(y, cy) - xq.get(x, cx)),
                "gap_days": float(feat.loc[x, "gap_b"]),
            }
        )
    return pd.DataFrame.from_records(out)


def _ew_mean(ages: np.ndarray, pos: np.ndarray, half_life: float) -> float:
    if len(pos) == 0:
        return float(ABSENT)
    if half_life is None or not math.isfinite(half_life):
        return float(np.mean(pos))
    weights = np.power(0.5, ages / float(half_life))
    return float(np.sum(weights * pos) / np.sum(weights))


def _trend_target(row: pd.Series, window: int) -> float:
    daily = row["_daily"]
    days = sorted(daily)[-window:]
    if len(days) < 2:
        return float(row["cons_a"])
    x = np.array([_day_index(day) for day in days], dtype=float)
    y = np.array([daily[day] for day in days], dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    return float(row["cons_a"]) + slope * float(row["gap_b"])


def target_values(ctx: HistoryContext, query: str, feat: pd.DataFrame, params: dict) -> pd.Series:
    name = params["target"]
    if name in ("vote_mean_a", "all_mean_a"):
        return feat[name].astype(float)
    if name == "ew_mean":
        half = params.get("half_life", math.inf)
        return pd.Series([_ew_mean(a, p, half) for a, p in zip(feat["_ages"], feat["_pos"], strict=True)], index=feat.index)
    if name == "prev_mean":
        return feat["prev_mean"].astype(float).fillna(feat["cons_a"].astype(float))
    if name == "trend":
        window = int(params.get("window", 3))
        return pd.Series([_trend_target(row, window) for _, row in feat.iterrows()], index=feat.index)
    if name == "deep":
        return feat["deep"].astype(float).fillna(feat["cons_a"].astype(float))
    if name == "xq_mean":
        groups = ctx.groups() if params.get("xq_same_group", True) else None
        xq = ctx.cross_query(query, list(feat.index), groups)
        return pd.Series([xq.get(key, float(cons)) for key, cons in zip(feat.index, feat["cons_a"], strict=True)], index=feat.index)
    raise ValueError(f"unknown target {name!r}")


def _kemeny_order(items: list[str], pref: np.ndarray) -> list[str]:
    """Order maximizing sum of pref[i, j] over i placed above j. Exact DP over subsets (n <= 14)."""
    n = len(items)
    if n > 14:
        raise ValueError("kemeny DP limited to 14 items")
    full = (1 << n) - 1
    best = np.full(1 << n, -np.inf)
    choice = np.full(1 << n, -1, dtype=int)
    best[0] = 0.0
    # gain of putting item i next (below everything already placed in mask, above the rest)
    for mask in range(1 << n):
        if best[mask] == -np.inf:
            continue
        rest = [j for j in range(n) if not mask & (1 << j)]
        for i in rest:
            gain = sum(pref[i, j] for j in rest if j != i)
            new = mask | (1 << i)
            value = best[mask] + gain
            if value > best[new] + 1e-12:
                best[new] = value
                choice[new] = i
    order = []
    mask = full
    while mask:
        i = int(choice[mask])
        order.append(i)
        mask &= ~(1 << i)
    order.reverse()
    return [items[i] for i in order]


def pull_shares(ctx: HistoryContext, query: str, params: dict) -> tuple[list[str], np.ndarray]:
    """Day_a consensus top-10 keys (sorted by cons_a) and share[i, j] = weighted share of pulls with i above j.

    Weight 1 for every top-10 pull on day_a (voting or not; the deep list too if
    deep_as_pull). Pulls on earlier days get prev_weight * 0.5 ** ((age - age_a) / prev_half_life).
    Ties (both absent) count one half. History only.
    """
    feat = ctx.features(query)
    top = feat[feat["cons_a"] <= 10]
    items = sorted(top.index, key=lambda key: (int(top.loc[key, "cons_a"]), key))
    day_a = ctx.day_a(query)
    prev_weight = float(params.get("prev_weight", 0.0))
    half = float(params.get("prev_half_life", math.inf))
    b_index = _day_index(ctx.day_b)
    age_a = b_index - _day_index(day_a)
    weighted: list[tuple[float, dict]] = []  # day_a pulls, weight 1
    earlier: list[tuple[float, dict]] = []  # pulls on days before day_a
    for day in ctx.days[query]:
        if day == day_a:
            bucket, weight = weighted, 1.0
        elif prev_weight > 0:
            age = b_index - _day_index(day)
            bucket = earlier
            weight = prev_weight * (0.5 ** ((age - age_a) / half) if math.isfinite(half) else 1.0)
        else:
            continue
        for pull in ctx.pulls[(query, day)]:
            bucket.append((weight, pull.ranks))
        deep_pull = ctx._deep_pull(query, day)
        if deep_pull is not None:
            bucket.append((weight, {key: pos for key, pos in deep_pull.items() if pos <= 10}))
    # Optional cap (params["prev_cap"] < 1). Each day_a pull moves a pair's weighted margin by 0.5 or 0,
    # an earlier pull by at most 0.5 * its weight. With the earlier total capped below 1, earlier days
    # can only break exact day_a ties and never outvote a strict day_a majority.
    cap = params.get("prev_cap")
    earlier_total = sum(weight for weight, _ in earlier)
    if cap is not None and earlier_total > float(cap):
        earlier = [(weight * float(cap) / earlier_total, ranks) for weight, ranks in earlier]
    weighted = weighted + earlier
    n = len(items)
    share = np.full((n, n), 0.5)
    if n < 2 or not weighted:
        return items, share
    pos = np.array([[ranks.get(key, ABSENT) for key in items] for _, ranks in weighted], dtype=float)
    w = np.array([weight for weight, _ in weighted], dtype=float)
    for i in range(n):
        for j in range(n):
            if i != j:
                above = np.where(pos[:, i] < pos[:, j], 1.0, np.where(pos[:, i] == pos[:, j], 0.5, 0.0))
                share[i, j] = float(np.sum(w * above) / np.sum(w))
    return items, share


def kemeny_positions(ctx: HistoryContext, query: str, params: dict) -> dict[str, float]:
    """Day_a consensus top 10 re-ordered by weighted pairwise pull majorities (Kemeny). History only.

    A pair's preference is share - 0.5 when |share - 0.5| > margin, else 0. The
    consensus order adds a tie-break of 1e-3 per pair, so with no strict weighted
    majority against it the result is persistence.
    """
    items, share = pull_shares(ctx, query, params)
    feat = ctx.features(query)
    if len(items) < 2:
        return {key: float(feat.loc[key, "cons_a"]) for key in items}
    margin = float(params.get("margin", 0.0))
    n = len(items)
    pref = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            m = share[i, j] - 0.5
            pref[i, j] = m if abs(m) > margin else 0.0
            pref[i, j] += 1e-3 if i < j else 0.0  # consensus tie-break (items sorted by cons_a)
    order = _kemeny_order(items, pref)
    return {key: float(rank + 1) for rank, key in enumerate(order)}


def score_with_context(ctx: HistoryContext, query: str, keys: list[str], params: dict) -> dict[str, float]:
    """Scores for the requested keys. Keys never seen in history get 11 + 1 (worse than any seen key)."""
    feat = ctx.features(query)
    if feat.empty:
        return {key: float(ABSENT + 1) for key in keys}
    if params.get("target") == "kemeny":
        ranks = kemeny_positions(ctx, query, params)
        out = {}
        for key in keys:
            if key in ranks:
                out[key] = ranks[key]
            elif key in feat.index:
                out[key] = float(feat.loc[key, "cons_a"]) + 0.5  # outside day_a top 10: below it
            else:
                out[key] = float(ABSENT + 1)
        return out
    lam = float(params.get("lam", 0.0))
    cons = feat["cons_a"].astype(float)
    if lam == 0.0:
        score = cons.copy()
    else:
        target = target_values(ctx, query, feat, params)
        target = target.clip(upper=float(params.get("target_cap", 31.0)))
        score = cons + lam * (target - cons)
    score = score + TIE_EPS * cons
    out = {}
    for key in keys:
        out[key] = float(score[key]) if key in score.index else float(ABSENT + 1)
    return out


def score_urls(
    history_rows: pd.DataFrame, query: str, keys: list[str], day_b: str, params: dict | None = None
) -> dict[str, float]:
    """PREDICTED day_b scores (lower = better) for ``keys`` on ``query`` from rows strictly before day_b.

    ``params`` defaults to evals/claude/t3-movers/params.json.
    """
    _assert_history(history_rows, day_b)
    if params is None:
        params = load_params()
    ctx = HistoryContext(history_rows, day_b, deep_as_pull=bool(params.get("deep_as_pull", False)))
    return score_with_context(ctx, query, keys, params)
