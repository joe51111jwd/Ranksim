"""Target 1: new-URL placement predictor. Output label: PREDICTED.

Interface
---------
predict_rows(history_rows, targets, params=None) -> list[int]

history_rows  SERP rows from rsdata.load_rows, already cut to days strictly
              before every target day (asserted here).
targets       DataFrame with columns query, key, url, site, day.

Every feature is computed from history_rows plus the identity of the target
row (query string, URL string). Each target row is predicted on its own: no
feature reads another target row, the outcome SERP, or anything dated on or
after the target day.

Fitted parameters live in evals/claude/t1-placement/params.json so the rule
can be frozen by sha256.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import rsdata  # noqa: E402

ABSENT = rsdata.ABSENT
PARAMS_PATH = ROOT / "evals" / "claude" / "t1-placement" / "params.json"

_STOP = {"the", "and", "for", "with", "best", "software", "app", "apps", "tool", "tools", "free", "how", "what", "top", "vs"}
_UGC_HOSTS = ("reddit.com", "quora.com", "contractortalk.com", "facebook.com", "linkedin.com", "medium.com")
_VIDEO_HOSTS = ("youtube.com",)
_REVIEW_HOSTS = ("capterra.com", "g2.com", "softwareadvice.com", "getapp.com", "trustradius.com", "softwareconnect.com", "selecthub.com")


def _tokens(text: str) -> set[str]:
    return {tok for tok in re.split(r"[^a-z0-9]+", str(text).lower()) if len(tok) >= 3 and tok not in _STOP}


def _path(url: str) -> str:
    try:
        return urlsplit(str(url)).path or "/"
    except ValueError:
        return "/"


class History:
    """Indexes built once from history rows. Nothing here sees a target day."""

    def __init__(self, history_rows: pd.DataFrame):
        self.rows = history_rows
        self.max_day = str(history_rows["day"].max()) if len(history_rows) else ""
        cons = rsdata.consensus_table(history_rows)
        self.cons = cons
        self.deep = rsdata.deep_table(history_rows)
        top = history_rows[history_rows["kind"] == "top10"]
        self.query_days = {q: sorted(set(g["day"])) for q, g in top.groupby("query")}
        # (query, day) -> consensus frame for that day
        self.cons_qd = {k: g for k, g in cons.groupby(["query", "day"])} if len(cons) else {}
        # every (query, key) and (query, site) seen in any stored list
        self.seen_key = set(zip(history_rows["query"], history_rows["key"]))
        self.seen_site = set(zip(history_rows["query"], history_rows["site"]))
        # any-pull best positions per (query, day)
        self.any_key = top.groupby(["query", "day", "key"])["position"].min().to_dict()
        self.any_site = top.groupby(["query", "day", "site"])["position"].min().to_dict()
        # latest deep list per query
        self.deep_key: dict[tuple[str, str], int] = {}
        self.deep_site: dict[tuple[str, str], int] = {}
        if len(self.deep):
            last_deep = self.deep.groupby("query")["day"].max().to_dict()
            latest = self.deep[self.deep["day"] == self.deep["query"].map(last_deep)]
            self.deep_key = {(q, k): int(p) for q, k, p in zip(latest["query"], latest["key"], latest["deep_pos"], strict=True)}
            site_best = latest.groupby(["query", "site"])["deep_pos"].min()
            self.deep_site = {k: int(v) for k, v in site_best.items()}
        # cross-query: latest consensus top-10 position of each key / site on each query
        top_cons = cons[cons["cons"] <= ABSENT - 1] if len(cons) else cons
        self.xq_key: dict[str, dict[str, int]] = defaultdict(dict)
        self.xq_site: dict[str, dict[str, int]] = defaultdict(dict)
        if len(top_cons):
            last_day = {q: days[-1] for q, days in self.query_days.items()}
            recent = top_cons[top_cons["day"] == top_cons["query"].map(last_day)]
            for q, k, p in zip(recent["query"], recent["key"], recent["cons"], strict=True):
                self.xq_key[k][q] = min(int(p), self.xq_key[k].get(q, ABSENT))
            site_best = recent.groupby(["site", "query"])["cons"].min()
            for (s, q), p in site_best.items():
                self.xq_site[s][q] = int(p)
        # key history on its own query: consensus positions on every pulled day
        self.key_cons_days: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
        for q, d, k, c in zip(cons["query"], cons["day"], cons["key"], cons["cons"], strict=True):
            self.key_cons_days[(q, k)].append((d, int(c)))
        # per-query entrant landing: truth of URLs absent the day before, from history only
        self.query_entrant_mean: dict[str, float] = {}
        for q, days in self.query_days.items():
            vals = []
            for a, b in zip(days[:-1], days[1:], strict=True):
                pa = self.cons_qd.get((q, a))
                pb = self.cons_qd.get((q, b))
                if pa is None or pb is None:
                    continue
                prev_top = set(pa.loc[pa["cons"] <= 10, "key"])
                now = pb[(pb["cons"] <= 10) & (~pb["key"].isin(prev_top))]
                vals.extend(int(v) for v in now["cons"])
            if vals:
                self.query_entrant_mean[q] = float(np.mean(vals))


def features(history_rows: pd.DataFrame, targets: pd.DataFrame, hist: History | None = None) -> pd.DataFrame:
    """One feature row per target, computed from history only."""
    _assert_sealed(history_rows, targets)
    hist = hist or History(history_rows)
    out = []
    for t in targets.itertuples(index=False):
        q, k, url, s = t.query, t.key, t.url, t.site
        days = hist.query_days.get(q, [])
        prev = days[-1] if days else None
        prev_cons = hist.cons_qd.get((q, prev)) if prev else None
        if prev_cons is not None:
            top = prev_cons[prev_cons["cons"] <= 10]
            key_prev = dict(zip(top["key"], top["cons"], strict=True))
            site_rows = top[top["site"] == s]
            dp = int(site_rows["cons"].min()) if len(site_rows) else ABSENT
            dp_n = int(len(site_rows))
            n_prev_pulls = int(prev_cons["n_pulls"].iloc[0])
        else:
            key_prev, dp, dp_n, n_prev_pulls = {}, ABSENT, 0, 0
        seen = (q, k) in hist.seen_key
        if k in key_prev:
            klass = "survivor"
        elif not seen:
            klass = "new"
        else:
            klass = "reentrant"
        # flicker positions on the previous day (any pull, any session)
        any_key_prev = hist.any_key.get((q, prev, k), ABSENT) if prev else ABSENT
        any_site_prev = hist.any_site.get((q, prev, s), ABSENT) if prev else ABSENT
        # last consensus record of this key on this query
        kd = sorted(hist.key_cons_days.get((q, k), []))
        last_cons = kd[-1][1] if kd else ABSENT
        best_cons = min((c for _, c in kd), default=ABSENT)
        last_top_day = max((d for d, c in kd if c <= 10), default=None)
        last_top_pos = next((c for d, c in reversed(kd) if c <= 10), ABSENT)
        days_since_top = rsdata.calendar_gap(last_top_day, t.day) if last_top_day else 99
        # cross-query priors, other queries only
        xk = {qq: p for qq, p in hist.xq_key.get(k, {}).items() if qq != q}
        xs = {qq: p for qq, p in hist.xq_site.get(s, {}).items() if qq != q}
        qtok = _tokens(q)
        ptok = _tokens(_path(url).replace("-", " ").replace("_", " "))
        path = _path(url)
        out.append(
            {
                "klass_f": klass,
                "prev_day_f": prev,
                "n_prev_pulls": n_prev_pulls,
                "dp": dp,
                "dp_n": dp_n,
                "any_key_prev": any_key_prev,
                "any_site_prev": any_site_prev,
                "deep_key": hist.deep_key.get((q, k), 99),
                "deep_site": hist.deep_site.get((q, s), 99),
                "site_seen_q": (q, s) in hist.seen_site,
                "last_cons": last_cons,
                "best_cons": best_cons,
                "last_top_pos": last_top_pos,
                "days_since_top": days_since_top,
                "xq_key_n": len(xk),
                "xq_key_min": min(xk.values(), default=ABSENT),
                "xq_key_med": float(np.median(list(xk.values()))) if xk else float(ABSENT),
                "xq_site_n": len(xs),
                "xq_site_min": min(xs.values(), default=ABSENT),
                "xq_site_med": float(np.median(list(xs.values()))) if xs else float(ABSENT),
                "is_home": path.strip("/") == "",
                "path_depth": len([p for p in path.split("/") if p]),
                "host_ugc": s in _UGC_HOSTS,
                "host_video": s in _VIDEO_HOSTS,
                "host_review": s in _REVIEW_HOSTS,
                "tok_overlap": (len(qtok & ptok) / len(qtok)) if qtok else 0.0,
                "q_entrant_mean": hist.query_entrant_mean.get(q, np.nan),
            }
        )
    return pd.DataFrame.from_records(out, index=targets.index)


def _assert_sealed(history_rows: pd.DataFrame, targets: pd.DataFrame) -> None:
    if len(targets) == 0 or len(history_rows) == 0:
        return
    first_target = str(targets["day"].min())
    last_hist = str(history_rows["day"].max())
    if not last_hist < first_target:
        raise AssertionError(f"history reaches {last_hist}, target day {first_target}: history must be strictly before")


def load_params(path: Path | str = PARAMS_PATH) -> dict:
    with open(path) as handle:
        return json.load(handle)


def _clip(value: float) -> int:
    return int(min(max(int(round(value)), 1), ABSENT))


def _clip_to(value: float, lo: int, hi: int) -> int:
    return int(min(max(int(np.floor(float(value) + 0.5)), lo), hi))


def rule_predict(f: dict, params: dict) -> int:
    """Anchor cascade. ``f`` is one feature row (a dict), ``params`` one rule spec.

    For the row's class ('new', 'reentrant', 'survivor') the spec lists anchors
    in priority order. The first anchor whose feature value is <= its ``max``
    gives the prediction value + offset. ``combine: median`` instead takes the
    median of all available anchors (each plus its offset). With no anchor the
    class default is used. Every prediction is clipped to [lo, hi]. Evaluated
    rows always have truth 1-10, so 1 is dominated by 2 and 10 or 11 by 9 on
    both metrics; the frozen clip is [2, 9].
    """
    spec = params[f["klass_f"]]
    lo, hi = spec.get("clip", params.get("clip", [1, ABSENT]))
    found = []
    for anchor in spec.get("anchors", []):
        value = f[anchor["feature"]]
        if value is not None and not (isinstance(value, float) and np.isnan(value)) and float(value) <= anchor.get("max", 10):
            found.append(float(anchor["value"]) if "value" in anchor else float(value) + anchor.get("offset", 0))
            if spec.get("combine", "first") == "first":
                break
    if found:
        value = found[0] if spec.get("combine", "first") == "first" else float(np.median(found))
    else:
        value = spec["default"]
    if f.get("host_ugc") and spec.get("ugc_shift"):
        value += spec["ugc_shift"]
    return _clip_to(value, lo, hi)


def predict_rows(
    history_rows: pd.DataFrame, targets: pd.DataFrame, params: dict | None = None, hist: History | None = None
) -> list[int]:
    """PREDICTED consensus position (1-11) for each target row, history strictly before the target day.

    ``hist`` is an optional cache of History(history_rows); it is only a speed-up and must be built
    from the same history_rows.
    """
    _assert_sealed(history_rows, targets)
    params = params if params is not None else load_params()
    if hist is not None and hist.rows is not history_rows:
        raise ValueError("hist must be built from the same history_rows")
    feats = features(history_rows, targets, hist)
    return [rule_predict(record, params) for record in feats.to_dict("records")]
