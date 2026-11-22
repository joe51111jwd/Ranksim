"""Offline tests for part 5, the trust model (forecast/trust.py).

Hand-made frames and the synthetic world only. Synthetic results test the code;
they are SYNTHETIC and never reported as accuracy.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aeo.ranksim.forecast import trust as T
from aeo.ranksim.forecast.content import ContentScorer
from aeo.ranksim.forecast.markers import evaluate_marker
from aeo.ranksim.forecast.types import SYNTHETIC


def _page() -> pd.DataFrame:
    t = pd.Timestamp("2026-02-01", tz="UTC")
    return pd.DataFrame({
        "keyword_id": "k1", "keyword": "emergency plumber", "snapshot_time": t, "snapshot_date": date(2026, 2, 1),
        "site": ["big.com", "match.com", "other.com", "notitle.com"],
        "rank100": [1, 2, 3, 4],
        "title": ["Welcome to Big", "Emergency plumber 24 hours", "Plumber", None],
    })


def test_label_is_content_place_minus_real_place_with_components_stored():
    labels = T.label_table(_page(), ContentScorer("lexical")).set_index("site")
    assert labels.loc["match.com", "content_place"] == 1.0  # best title match
    assert labels.loc["big.com", "real_place"] == 1.0
    assert labels.loc["big.com", "residual"] == labels.loc["big.com", "content_place"] - 1.0 > 0  # ranks above its content
    assert labels.loc["match.com", "residual"] == 1.0 - 2.0
    assert math.isnan(labels.loc["notitle.com", "residual"])  # no title: no label, not zero
    assert labels.loc["other.com", "real_place"] == 3.0 and labels.loc["other.com", "n_page"] == 3
    assert set(labels["content_backend"]) == {"lexical"}
    assert labels["label_table_version"].nunique() == 1
    for col in ("real_place", "content_place", "residual", "content_backend", "content_model"):
        assert col in labels.columns


def test_label_pairs_respect_the_threshold_and_pages():
    frame = pd.DataFrame({"keyword_id": ["a"] * 3 + ["b"] * 2, "snapshot_time": [1, 1, 1, 1, 1],
                          "residual": [10.0, -1.0, 0.0, 30.0, 0.0], "site": list("vwxyz")})
    hi, lo = T.label_pairs(frame, threshold=10.0)
    pairs = {(frame["site"][h], frame["site"][l]) for h, l in zip(hi, lo)}
    # 10.0 against 0.0 differs by exactly the threshold, not more (spec section 6 part 5): no pair
    assert pairs == {("v", "w"), ("y", "z")}
    hi, lo = T.label_pairs(frame, threshold=10.0, strict=False)  # the trust-v1 rule, kept only for that scored version
    assert {(frame["site"][h], frame["site"][l]) for h, l in zip(hi, lo)} == {("v", "w"), ("v", "x"), ("y", "z")}


def test_two_way_interval_brackets_the_mean():
    rng = np.random.default_rng(0)
    values = (rng.random(600) < 0.7).astype(float)
    searches = rng.integers(0, 40, 600)
    a = rng.integers(0, 80, 600)
    b = rng.integers(80, 160, 600)
    low, high = T.two_way_interval(values, searches, a, b, draws=300)
    assert low < values.mean() < high
    assert T.two_way_interval(values[:5], np.zeros(5), a[:5], b[:5]) == (None, None)


def _eval_frame(seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(40):
        for i in range(20):
            trust_value = rng.normal()
            rows.append({"keyword_id": f"k{p}", "snapshot_time": 1, "site": f"s{p % 7}_{i}.com",
                         "residual": 12 * trust_value + rng.normal(scale=6), "st_s_cc_hosts": trust_value + rng.normal(scale=2),
                         "st_s_cc_hc": rng.normal(), "sd_young": float(i < 6), "sd_uncertainty_days": 60.0, "true": trust_value})
    return pd.DataFrame(rows)


def test_evaluate_pairs_scores_everything_on_identical_pairs_and_feeds_marker_2():
    frame = _eval_frame()
    result = T.evaluate_pairs(frame, {"model": frame["true"].to_numpy()}, single_measures=["st_s_cc_hosts", "st_s_cc_hc"])
    n = result["pairs"]
    assert n > 100
    assert result["within_page_shuffle"]["count"] == n
    assert all(block["count"] == n for block in result["single_measures"].values())
    model = result["models"]["model"]
    assert model["pairs"]["count"] == n
    assert model["pairs"]["rate"] > result["single_measures"]["st_s_cc_hosts"]["rate"]
    assert model["both_sites_young"]["pairs"] < n
    report = T.marker2_report(result, "model", label=SYNTHETIC)
    verdict = evaluate_marker(2, report)
    assert verdict["label"] == SYNTHETIC and verdict["passed"] is None  # synthetic data can never pass a marker
    assert verdict["checks"][0]["value"] == pytest.approx(model["pairs"]["rate"])


def test_mdr_before_never_reads_the_page_itself_or_later():
    t = pd.Timestamp("2026-02-01 10:00", tz="UTC")
    rows = pd.DataFrame({"site": ["a.com"] * 4, "snapshot_time": [t - timedelta(days=30), t - timedelta(days=1), t, t + timedelta(days=1)],
                         "main_domain_rank": [100, 0, 999, 555]})
    q = pd.DataFrame({"site": ["a.com", "b.com"], "snapshot_time": [t, t]})
    out = T.mdr_before(rows, q)
    assert out[0] == 100.0  # the 0 a day earlier is missing, the page's own 999 and the later 555 are not used
    assert math.isnan(out[1])


def _fit_frame(n: int = 600, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    return pd.DataFrame({"site": [f"s{i}.com" if i % 3 else f"s{i}.org" for i in range(n)], "st_s_cc_hc": x,
                         "st_s_tranco": rng.normal(size=n), "residual": 5 * x + rng.normal(size=n),
                         "search_group": [f"g{i % 50}" for i in range(n)]})


def test_fit_refuses_survivorship_features_and_round_trips(tmp_path: Path):
    frame = _fit_frame()
    with pytest.raises(ValueError):
        T.fit_trust_model(frame, feature_set="x", features=["st_s_cc_hc", "st_s_opr"])
    grid = ({"num_leaves": 4, "min_child_samples": 10},)
    model = T.fit_trust_model(frame, feature_set="x", features=["st_s_cc_hc", "st_s_tranco", "tld_code", "fp_review_count"], grid=grid)
    assert "fp_review_count" in model.cv["dropped_all_missing"]
    pred = model.predict(frame)
    assert np.corrcoef(pred, frame["residual"])[0, 1] > 0.8
    assert np.corrcoef(model.predict_ridge(frame), frame["residual"])[0, 1] > 0.8
    path, digest = T.save_trust_model(model, {"language": "en", "train_rows": len(frame)}, tmp_path)
    loaded = T.load_trust_model(path)
    assert np.allclose(loaded.predict(frame), pred)
    manifest = json.loads(path.with_name(path.name.replace(".json", ".manifest.json")).read_text())
    assert manifest["sha256"] == digest
    path.write_text(path.read_text().replace('"x"', '"y"', 1))
    with pytest.raises(ValueError):
        T.load_trust_model(path)


def test_fit_is_deterministic():
    frame = _fit_frame(seed=5)
    grid = ({"num_leaves": 4, "min_child_samples": 10},)
    a = T.fit_trust_model(frame, feature_set="x", features=["st_s_cc_hc", "st_s_tranco"], grid=grid).predict(frame)
    b = T.fit_trust_model(frame, feature_set="x", features=["st_s_cc_hc", "st_s_tranco"], grid=grid).predict(frame)
    assert np.array_equal(a, b)


def test_trust_demand_and_gap():
    page = pd.DataFrame({"site": [f"s{i}.com" for i in range(1, 16)], "rank100": np.arange(1, 16), "trust": np.arange(15, 0, -1.0)})
    demand = T.trust_demand(page)
    assert demand["trust_demand_q50"] == pytest.approx(float(np.median(np.arange(15, 5, -1.0))))
    gap = T.trust_gap(20.0, page, "new.com")
    assert gap["trust_q_top10"] == 1.0 and gap["trust_gap_top10"] == pytest.approx(20.0 - 10.5)
    inside = T.trust_gap(15.0, page, "s1.com")
    assert inside["trust_gap_top10"] == pytest.approx(15.0 - float(np.median(np.arange(14, 5, -1.0))))


def test_synthetic_model_orders_planted_ceilings_better_than_link_count_and_only_with_footprint():
    from aeo.ranksim.forecast.synth import World

    world = World(1, niches=3, searches_per_niche=6, established_sites=60, new_sites=1200, months=24)
    result = T.synthetic_order_check(world)
    assert result["label"] == SYNTHETIC
    assert result["test_sites"] > 200
    assert result["with_footprint_gbt"] >= result["link_count_alone"] + 0.05
    assert result["with_footprint_ridge"] > result["link_count_alone"]
    # with the footprint removed, the free list at launch knows nothing about a new site
    assert result["without_footprint_gbt"] <= 0.55
    assert result["without_footprint_gbt"] < result["link_count_alone"]


def test_synthetic_frame_never_feeds_the_planted_ceiling():
    from aeo.ranksim.forecast.synth import World

    world = World(2, niches=2, searches_per_niche=4, established_sites=40, new_sites=30, months=24)
    frame = T.synthetic_trust_frame(world)
    assert (frame["label"] == SYNTHETIC).all()
    assert "planted_ceiling" not in T.SYNTHETIC_FEATURES
    for row in frame.itertuples():
        assert row.as_of == world.new_sites[row.site].launch_date  # features as of launch day, label at day 90


# ---------------------------------------------------------------------------
# marker 2 pairs need labels MORE than the threshold apart; a scored result is never overwritten


REPO = Path(__file__).resolve().parents[1]


def test_marker2_pairs_need_labels_that_differ_by_more_than_the_threshold():
    frame = pd.DataFrame({"keyword_id": ["k", "k"], "snapshot_time": [pd.Timestamp("2026-03-01", tz="UTC")] * 2,
                          "site": ["a.com", "b.com"], "residual": [5.0, 5.0 - T.PAIR_THRESHOLD]})
    hi, _ = T.label_pairs(frame, T.PAIR_THRESHOLD)
    assert len(hi) == 0
    assert T.pairs_strict(2) and not T.pairs_strict(1)  # trust-v1 was scored with "at least"; it is never rescored
    v2 = T.prereg_payload(version=2, reason="pairs as the spec defines them")
    assert ">" in v2["pair_comparison"] and v2["version"] == "trust-v2" and "RDAP" in v2["start_evidence"]


def test_the_version_1_trust_design_still_hashes_to_its_frozen_sha():
    from aeo.ranksim.forecast.chain import canonical_json, sha256_text

    frozen = json.loads((REPO / "evals/forecast/trust/prereg.json").read_text())
    assert sha256_text(canonical_json(json.loads(json.dumps(T.prereg_payload(), default=str)))) == frozen["sha256"]


def _stub_trust_run(monkeypatch) -> dict[str, int]:
    from aeo.ranksim.forecast import markers as M

    calls = {"rows": 0, "scored": 0}

    def fake_rows(*_a, **_k):
        calls["rows"] += 1
        return pd.DataFrame(), None

    def fake_run_one(*_a, version=1, **_k):
        calls["scored"] += 1
        return {"evaluation": {"score": calls["scored"]}, "feature_sets": {}}

    monkeypatch.setattr(T, "load_label_rows", fake_rows)
    monkeypatch.setattr(T, "run_one", fake_run_one)
    monkeypatch.setattr(T, "marker2_report", lambda ev, name: {"score": ev["score"]})
    monkeypatch.setattr(M, "evaluate_marker", lambda marker, rep: {"passed": False, "score": rep["score"]})
    monkeypatch.setattr(M, "render_marker_text", lambda verdict: "stub")
    return calls


def test_trust_run_refuses_to_score_the_held_out_side_a_second_time(tmp_path: Path, monkeypatch):
    from aeo.ranksim.forecast.features import AlreadyScored, freeze_prereg, prereg_file

    calls = _stub_trust_run(monkeypatch)
    freeze_prereg(prereg_file(tmp_path, 1), T.prereg_payload())
    T.run(out_dir=tmp_path, check_git=False, models_dir=tmp_path / "models")
    first = (tmp_path / "report.json").read_text()
    with pytest.raises(AlreadyScored):
        T.run(out_dir=tmp_path, check_git=False, models_dir=tmp_path / "models")
    assert calls == {"rows": 1, "scored": 3}  # the refusal came before any row was read or scored
    assert (tmp_path / "report.json").read_text() == first
    freeze_prereg(prereg_file(tmp_path, 2), T.prereg_payload(version=2, reason="panel rebuilt"), code=True)
    report = T.run(out_dir=tmp_path, check_git=False, models_dir=tmp_path / "models", version=2)
    assert report["version"] == "trust-v2" and report["pair_comparison"] == ">"
    assert (tmp_path / "report.json").read_text() == first and (tmp_path / "report-v2.json").is_file()
    manifest = json.loads((tmp_path / "manifest-v2.json").read_text())
    assert manifest["prereg_file"] == "prereg-v2.json" and "source_tree_sha256" in manifest


def test_trust_run_refuses_an_uncommitted_prereg_before_reading_any_row(tmp_path: Path, monkeypatch):
    from aeo.ranksim.forecast.features import ProvenanceRefusal, freeze_prereg, prereg_file

    calls = _stub_trust_run(monkeypatch)
    freeze_prereg(prereg_file(tmp_path, 1), T.prereg_payload())
    with pytest.raises(ProvenanceRefusal):
        T.run(out_dir=tmp_path, models_dir=tmp_path / "models")
    assert calls["rows"] == 0


def test_a_saved_trust_model_is_never_replaced(tmp_path: Path):
    from aeo.ranksim.forecast.features import AlreadyScored

    model = T.fit_trust_model(_fit_frame(), feature_set="x", features=["st_s_cc_hc", "st_s_tranco"],
                              grid=({"num_leaves": 4, "min_child_samples": 10},))
    path, _ = T.save_trust_model(model, {"language": "en"}, tmp_path)
    before = path.read_text()
    with pytest.raises(AlreadyScored):
        T.save_trust_model(model, {"language": "en", "train_rows": 1}, tmp_path)
    assert path.read_text() == before


# ---------------------------------------------------------------------------
# version 2: marker 2 keys, stand-in, features, out-of-fold models for the backtest


def test_marker2_report_decides_on_the_link_count_gain_and_prints_the_best_single_gain_beside_it():
    frame = _eval_frame()
    result = T.evaluate_pairs(frame, {"model": frame["true"].to_numpy()}, single_measures=["st_s_cc_hosts", "st_s_cc_hc"])
    entry = result["models"]["model"]
    report = T.marker2_report(result, "model")
    assert report["gain_over_link_count"]["difference"] == entry["gain_over_link_count"]["difference"]
    assert report["gain_over_link_count"]["baseline"] == "st_s_cc_hosts"
    assert report["gain_over_best_single_measure"]["difference"] == entry["gain_over_best_single_measure"]["difference"]
    assert report["gain_over_best_single_measure"]["baseline"] == entry["best_single_measure"]
    assert report["stand_in"] is True and "20,000 new sites" in report["stand_in_reason"]
    verdict = evaluate_marker(2, report)
    assert verdict["passed"] is None and verdict["stand_in"] is True
    assert verdict["gain_over_link_count"]["difference"] == report["gain_over_link_count"]["difference"]
    assert verdict["gain_over_best_single_measure"]["difference"] == report["gain_over_best_single_measure"]["difference"]
    plain = T.marker2_report(result, "model", stand_in=False)
    assert "stand_in" not in plain and evaluate_marker(2, plain)["passed"] in (True, False)


def test_trust_version_2_drops_registration_known_and_version_1_keeps_its_features():
    assert "sd_registration_known" in T.feature_sets(1)["free"]
    assert "sd_registration_known" not in T.feature_sets(2)["free"]
    assert "sd_registration_known" not in T.feature_sets(2)["free_plus_mdr"]
    assert T.feature_sets(2)["free_plus_mdr"][-1] == "mdr_before"
    payload = T.prereg_payload(version=2, reason="v2")
    assert "sd_registration_known" not in payload["feature_sets"]["free"] and "stand_in" in payload


def _label_rows(n_pages: int = 90, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_pages):
        for i in range(12):
            x = rng.normal()
            site = (p * 7 + i) % 150
            rows.append({"keyword_id": f"k{p}", "snapshot_time": pd.Timestamp("2025-07-01", tz="UTC"),
                         "snapshot_date": date(2025, 7, 1), "site": f"site{site}.com",
                         "search_group": f"g{p}", "site_test": bool(site % 10 == 0), "residual": 4 * x + rng.normal(),
                         "st_s_cc_hc": x, "st_s_tranco": rng.normal(), "content_backend": "lexical", "content_model": "bm25f"})
    return pd.DataFrame(rows)


def test_trust_fold_models_never_fit_on_their_own_search_fold_or_site_fold():
    labels = _label_rows()
    search_fold = np.array([int(g[1:]) % 3 for g in labels["search_group"]])
    site_fold = np.array([int(s[4:].split(".")[0]) % 3 for s in labels["site"]])
    folds = T.fit_trust_folds(labels, search_fold, site_fold, 3, version=2,
                              grid=({"num_leaves": 4, "min_child_samples": 10},))
    assert set(folds) == {(a, b) for a in range(3) for b in range(3)}
    for (a, b), fold in folds.items():
        fitted = labels[labels["search_group"].isin(fold.search_groups) & labels["site"].isin(fold.sites)]
        assert fold.rows > 0 and not any(int(g[1:]) % 3 == a for g in fold.search_groups)
        assert not any(int(s[4:].split(".")[0]) % 3 == b for s in fold.sites)
        assert not labels.loc[labels["site"].isin(fold.sites), "site_test"].any()  # a held-out site never trains
        assert fold.model.version == f"trust-v2-oof-a{a}-b{b}" and "sd_registration_known" not in fold.model.features
        assert len(fitted) >= fold.rows
    same = T.trust_fit_rows(labels, (search_fold != 1) & (site_fold != 2))
    assert len(same) == folds[(1, 2)].rows


def test_training_labels_read_training_pages_only_and_are_cached(tmp_path: Path, monkeypatch):
    from aeo.ranksim.forecast.strength import DEFAULT_SPLIT

    monkeypatch.setenv("RANKSIM_FORECAST_DIR", str(tmp_path / "data"))
    seen: dict[str, int] = {"loads": 0}
    pages = pd.DataFrame({"keyword_id": ["k1", "k1", "k2", "k2"], "keyword": ["a b", "a b", "c d", "c d"],
                          "snapshot_time": [pd.Timestamp("2025-07-01", tz="UTC")] * 2 + [pd.Timestamp("2026-02-01", tz="UTC")] * 2,
                          "snapshot_date": [date(2025, 7, 1)] * 2 + [date(2026, 2, 1)] * 2, "site": ["x.com", "y.com"] * 2,
                          "rank100": [1, 2, 1, 2], "title": ["a b page", "other"] * 2, "side": ["train", "train", "test", "test"],
                          "site_test": [False, True, False, False], "search_group": ["g1", "g1", "g2", "g2"],
                          "language": ["en"] * 4})

    def load(split, *, sites_split=None, log=None, restrict=None):
        seen["loads"] += 1
        out = restrict(pages) if restrict is not None else pages
        assert set(out["side"]) == {"train"}
        return out.reset_index(drop=True), None

    monkeypatch.setattr(T, "load_label_rows", load)
    labels, meta = T.training_labels(DEFAULT_SPLIT, tag="t")
    assert set(labels["keyword_id"]) == {"k1"} and meta["latest_page_date"] == "2025-07-01"
    assert {"residual", "site_test", "content_backend"} <= set(labels.columns)
    again, meta2 = T.training_labels(DEFAULT_SPLIT, tag="t")
    assert seen["loads"] == 1 and meta2["sha256"] == meta["sha256"] and len(again) == len(labels)
    assert str(tmp_path / "data" / "cache" / "oof") in str(T.label_cache_dir())
