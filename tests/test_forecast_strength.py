"""Offline tests for part 3, site strength (forecast/strength.py).

Hand-made frames only. Every outcome here is made up for the test; none is REAL or reported.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import strength as S
from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.features import MODEL_MEASURES, freeze_prereg, require_prereg, search_group


def test_within_page_pairs_kinds():
    codes = np.zeros(12, dtype=int)
    ranks = np.array([1, 2, 5, 9, 10, 11, 15, 30, 50, 51, 70, 100])
    b, w = S.within_page_pairs(codes, ranks, "top10")
    assert len(b) == 10  # five sites in the top 10
    assert np.all(ranks[b] < ranks[w])
    b, w = S.within_page_pairs(codes, ranks, "straddle10")
    assert len(b) == 5 * 4 and set(ranks[w]) == {11, 15, 30, 50}
    b, w = S.within_page_pairs(codes, ranks, "straddle50")
    assert len(b) == 9 * 3
    two = S.within_page_pairs(np.array([0, 0, 1, 1]), np.array([1, 2, 1, 2]), "top10")
    assert len(two[0]) == 2  # pairs never cross pages
    with pytest.raises(ValueError):
        S.within_page_pairs(np.array([1, 0]), np.array([1, 2]), "top10")


def test_pair_scores_count_ties_and_missing_as_half():
    values = np.array([3.0, 1.0, 1.0, np.nan])
    better = np.array([0, 1, 2, 0])
    worse = np.array([1, 0, 1, 3])
    assert S.pair_scores(values, better, worse).tolist() == [1.0, 0.0, 0.5, 0.5]
    block = S.pair_block(np.array([1.0, 0.0, 0.5, 0.5]), np.array([0, 0, 1, 1]))
    assert (block["right"], block["wrong"], block["tied"], block["rate"]) == (1, 1, 2, 0.5)


def test_weighted_auc_matches_brute_force():
    rng = np.random.default_rng(1)
    scores = rng.integers(0, 4, size=40).astype(float)
    labels = rng.random(40) < 0.4
    pos, neg = scores[labels], scores[~labels]
    brute = np.mean([[1.0 if p > n else 0.5 if p == n else 0.0 for n in neg] for p in pos])
    assert S.weighted_auc(scores, labels) == pytest.approx(brute)
    weights = rng.integers(1, 3, size=40).astype(float)
    rep_s = np.repeat(scores, weights.astype(int))
    rep_l = np.repeat(labels, weights.astype(int))
    assert S.weighted_auc(scores, labels, weights) == pytest.approx(S.weighted_auc(rep_s, rep_l))


def _toy_pages(n_pages: int = 60, sites_per_page: int = 30, noise: float = 0.5, seed: int = 0) -> pd.DataFrame:
    """Pages where the real order follows s_cc_hc plus noise. Made-up data for tests."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_pages):
        strength = rng.normal(size=sites_per_page)
        latent = strength + rng.normal(scale=noise, size=sites_per_page)
        order = np.argsort(-latent)
        for rank, i in enumerate(order, start=1):
            rows.append({"page_code": p, "rank100": rank, "site": f"s{p}_{i}.com", "keyword": f"search {p}",
                         "keyword_id": f"k{p}", "search_group": search_group(f"search {p}"),
                         "s_cc_hc": strength[i], "s_cc_pr": rng.normal(), "s_cc_hosts": strength[i] * 0.5 + rng.normal(scale=2),
                         "s_crux": rng.normal(), "s_tranco": rng.normal(), "s_opr": np.nan})
    return pd.DataFrame(rows).sort_values(["page_code", "rank100"]).reset_index(drop=True)


def test_fitted_score_learns_the_informative_measure_and_refuses_opr():
    rows = _toy_pages()
    score = S.fit_strength_score(rows, max_pairs=20_000)
    assert score.coef["s_cc_hc"] > 5 * abs(score.coef["s_cc_pr"])
    values = score.score(rows)
    b, w = S.within_page_pairs(rows["page_code"].to_numpy(), rows["rank100"].to_numpy(), "top20")
    assert float(np.mean(S.pair_scores(values, b, w))) > 0.75
    back = S.StrengthScore.from_dict(score.to_dict())
    assert np.allclose(back.score(rows), values)
    with pytest.raises(ValueError):
        S.fit_strength_score(rows, measures=("s_cc_hc", "s_opr"))


def test_page_demand_and_gap_leave_the_site_out():
    page = pd.DataFrame({"site": [f"s{i}.com" for i in range(1, 31)], "rank100": np.arange(1, 31),
                         "strength_score": np.linspace(3, 0, 30)})
    demand = S.page_demand(page)
    assert demand["demand_top10_q50"] == pytest.approx(float(np.median(np.linspace(3, 0, 30)[:10])))
    gap = S.strength_gap(page["strength_score"][0], page, "s1.com")
    others = np.linspace(3, 0, 30)[1:10]
    assert gap["gap_top10"] == pytest.approx(3.0 - float(np.median(others)))
    assert gap["q_top10"] == 1.0
    outsider = S.strength_gap(-1.0, page, "new.com")
    assert outsider["q_top50"] == 0.0 and outsider["gap_top10"] < 0


def test_topical_strength_uses_only_earlier_pages_of_other_searches_and_shrinks():
    t0 = pd.Timestamp("2025-05-01", tz="UTC")
    history = pd.DataFrame({
        "site": ["a.com"] * 5,
        "group": ["US_en"] * 5,
        "keyword_id": ["k1", "k2", "k3", "kq", "k4"],
        "time": [t0, t0 + timedelta(days=10), t0 + timedelta(days=20), t0 + timedelta(days=5), t0 + timedelta(days=90)],
        "resid": [0.2, 0.4, 0.6, 5.0, -9.0],
    })
    query = pd.DataFrame({"site": ["a.com"], "group": ["US_en"], "keyword_id": ["kq"], "time": [t0 + timedelta(days=30)]})
    out = S.topical_strength(history, query, k=3.0).iloc[0]
    assert out["topical_n"] == 3.0  # kq is the query's own search, k4 is later
    assert out["topical"] == pytest.approx(3 / (3 + 3.0) * 0.4)
    same_time = pd.DataFrame({"site": ["a.com"], "group": ["US_en"], "keyword_id": ["kz"], "time": [t0 + timedelta(days=10)]})
    assert S.topical_strength(history, same_time, k=0.0).iloc[0]["topical_n"] == 2.0  # strictly before
    other_group = query.assign(group="DE_de")
    assert S.topical_strength(history, other_group, k=3.0).iloc[0]["topical"] == 0.0


def test_fit_topical_k_recovers_the_variance_ratio():
    rng = np.random.default_rng(3)
    tau, sigma = 0.5, 1.0
    rows = []
    for s in range(400):
        effect = rng.normal(scale=tau)
        for _ in range(rng.integers(3, 12)):
            rows.append({"site": f"s{s}", "group": "g", "resid": effect + rng.normal(scale=sigma)})
    fitted = S.fit_topical_k(pd.DataFrame(rows))
    assert fitted["k"] == pytest.approx(sigma**2 / tau**2, rel=0.35)


def test_strength_only_forecaster_fits_and_round_trips():
    rng = np.random.default_rng(4)
    n = 3000
    gap = rng.normal(size=n)
    latent = gap + rng.normal(scale=0.7, size=n)
    position = np.where(latent > 1.2, rng.integers(1, 11, n), np.where(latent > 0.3, rng.integers(11, 51, n), NT50))
    rows = pd.DataFrame({f: gap + rng.normal(scale=0.1, size=n) for f in S.FORECASTER_FEATURES})
    rows["position_out"] = position
    rows["search_group"] = [f"g{i % 97}" for i in range(n)]
    model = S.fit_strength_only(rows)
    points = model.points(rows)
    assert set(points) <= set(S.CANDIDATE_POINTS)
    strong = rows["gap_top10"] > 1.5
    weak = rows["gap_top10"] < -1.0
    assert np.mean(points[weak] == NT50) > 0.9
    assert np.mean(points[strong] != NT50) > 0.5
    back = S.StrengthOnlyForecaster.from_dict(model.to_dict())
    assert np.array_equal(back.points(rows), points)
    probs = model.probabilities(rows)
    assert np.allclose(probs.sum(axis=1), 1.0)


def test_split_rule_keeps_search_groups_on_one_side():
    rule = S.SplitRule()
    words = ["plumber near me", "plumbers near me", "best roof repair", "roof repair best", "cheap flights"]
    for a in words:
        for b in words:
            if search_group(a) == search_group(b):
                assert rule.search_is_test(a) == rule.search_is_test(b)
    kw = next(k for k in (f"search {i}" for i in range(200)) if rule.search_is_test(k))
    assert rule.page_side(kw, date(2026, 2, 1)) == "test"
    assert rule.page_side(kw, date(2025, 12, 31)) == "excluded"
    kw_train = next(k for k in (f"search {i}" for i in range(200)) if not rule.search_is_test(k))
    assert rule.page_side(kw_train, date(2025, 12, 31)) == "train"
    assert rule.page_side(kw_train, date(2026, 1, 1)) == "excluded"
    share = np.mean([rule.search_is_test(f"query number {i}") for i in range(4000)])
    assert 0.27 < share < 0.33


def test_restrict_to_training_never_keeps_held_out_pages():
    outer = S.DEFAULT_SPLIT
    pos = pd.DataFrame({"keyword": [f"search {i}" for i in range(300)], "snapshot_date": [date(2025, 11, 1)] * 150 + [date(2026, 2, 1)] * 150})
    pos["side"] = [outer.page_side(k, d) for k, d in zip(pos["keyword"], pos["snapshot_date"])]
    inner = S.restrict_to_training(pos, outer, S.REHEARSAL_SPLIT)
    assert set(inner["keyword"]) <= set(pos.loc[pos["side"] == "train", "keyword"])
    assert (inner["snapshot_date"] < outer.cutoff).all()


def test_prereg_payload_freezes(tmp_path: Path):
    payload = S.prereg_payload()
    digest = freeze_prereg(tmp_path / "prereg.json", payload)
    assert require_prereg(tmp_path / "prereg.json", S.prereg_payload()) == digest
    assert "s_opr" in payload["survivorship_limited_not_fitted"]
    assert "s_opr" not in payload["fitted_measures"]
