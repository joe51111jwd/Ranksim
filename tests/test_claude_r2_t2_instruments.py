"""Round 2, Target 2 test instruments: excess movement (A) and the long-window edit test (B).

Offline and fast, on synthetic frames and the committed design rows. The two tests marked needs_db
read the raw store through the sealed loaders at max_date 2026-11-17 and skip when it is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from scripts.claude import r2_t2_excess as ex  # noqa: E402
from scripts.claude import r2_t2_longwindow as lw  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.t2_direction import DEFAULT_PARAMS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DESIGN_ROWS = ROOT / "evals/claude/r2/t2-signal/change_rows.csv"
DESIGN_RESULTS = ROOT / "evals/claude/r2/t2-signal/results.json"
needs_db = pytest.mark.skipif(not Path(rs.DB).exists(), reason="raw SQLite store not present on this machine")


def _random_frame(seed: int = 7, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({
        "query": rng.choice([f"q{i:02d}" for i in range(12)], n),
        "key": rng.choice([f"u{i:02d}" for i in range(40)], n),
        "stratum": rng.choice(["a|1", "a|2-3", "b|4-6", "b|7-10", "c|11"], n),
        "material": rng.random(n) < 0.15,
        "moved": (rng.random(n) < 0.5).astype(int),
        "absd": rng.integers(0, 6, n).astype(float),
    })
    frame["site"] = frame["key"].str[:2]
    return frame


def _comparisons(diff, lo, hi):
    main = {"diff": diff, "lo": lo, "hi": hi, "treated_mean": None if diff is None else 0.5 + diff,
            "matched_control_mean": None if diff is None else 0.5}
    return {"material_vs_not": {"moved": {"query": main}}}


COUNTS = {"changed_rows": 30, "changed_rows_matched": 28, "changed_urls": 12, "changed_sites": 6,
          "control_rows_in_matched_strata": 400, "queries": 50}


# ------------------------------------------------------------------ Instrument A: statistics


def test_strat_effect_is_the_design_study_code():
    from scripts.claude import r2_t2_signal as sig

    frame = _random_frame()
    for outcome in ("moved", "absd"):
        for cluster in ("query", "key", "site"):
            assert ex.strat_effect(frame, "material", outcome, "stratum", cluster) == sig.strat_effect(
                frame, "material", outcome, "stratum", cluster)
    assert ex.band(1) == "1" and ex.band(3) == "2-3" and ex.band(6) == "4-6" and ex.band(10) == "7-10" and ex.band(11) == "11"


def test_strat_effect_compares_each_changed_row_with_its_own_stratum():
    records = [
        ("q1", "s1", False, 1), ("q2", "s1", False, 0), ("q1", "s1", True, 1),
        ("q1", "s2", False, 0), ("q2", "s2", False, 0), ("q3", "s2", False, 0), ("q3", "s2", False, 1),
        ("q2", "s2", True, 1), ("q3", "s2", True, 0),
        ("q3", "s3", True, 1),  # no unchanged row in s3: dropped
    ]
    frame = pd.DataFrame(records, columns=["query", "stratum", "material", "moved"])
    res = ex.strat_effect(frame, "material", "moved", "stratum", "query")
    assert res["treated_rows"] == 4 and res["treated_rows_matched"] == 3 and res["treated_rows_dropped_no_control"] == 1
    assert res["control_rows"] == 6 and res["control_rows_in_matched_strata"] == 6
    assert res["treated_mean"] == pytest.approx(2 / 3)
    assert res["matched_control_mean"] == pytest.approx((0.5 + 2 * 0.25) / 3)
    assert res["diff"] == pytest.approx(1 / 3)
    assert res["lo"] <= res["diff"] <= res["hi"]


def test_strat_effect_handles_no_rows_and_no_changed_rows():
    frame = _random_frame()
    empty = ex.strat_effect(frame.iloc[0:0], "material", "moved", "stratum", "query")
    assert empty["treated_mean"] is None and empty["lo"] is None and empty["valid_draws"] == 0
    none = ex.strat_effect(frame.assign(material=False), "material", "moved", "stratum", "query")
    assert none["treated_rows"] == 0 and none["treated_mean"] is None and none["lo"] is None


# ------------------------------------------------------------------ Instrument A: rows and outcomes


def _dataset_rows() -> pd.DataFrame:
    base = {"key": "k", "site": "s.com", "day_a": "2026-11-17", "day_b": "2026-11-18", "serp_day_a": "2026-11-17",
            "edit_tokens": 0, "title_changed": False, "h1_changed": False, "material": False, "usable": True}
    records = [
        {**base, "query": "o", "serp_day_b": "2026-11-16"},
        {**base, "query": "o", "serp_day_b": "2026-11-18"},
        {**base, "query": "a", "serp_day_b": "2026-11-19"},
        {**base, "query": "r", "serp_day_b": "2026-11-19"},
        {**base, "query": "o", "serp_day_b": "2026-11-19", "usable": False},
    ]
    frame = pd.DataFrame(records)
    frame["pos_a"], frame["pos_b"] = 3, 4
    return frame


def test_select_rows_matches_score_t2_and_the_design_split():
    groups = {"o": "o30", "a": "a70", "r": "rev"}
    rows = _dataset_rows()
    held = ex.select_rows(rows, groups, design=False)
    assert sorted(zip(held["query"], held["serp_day_b"])) == [("a", "2026-11-19"), ("o", "2026-11-18")]
    design = ex.select_rows(rows, groups, design=True)
    assert list(zip(design["query"], design["serp_day_b"])) == [("o", "2026-11-16")]
    assert set(held["group"]) == {"o30", "a70"}


def test_prepare_outcomes_bands_and_strata():
    rows = pd.DataFrame({
        "query": ["q"] * 4, "key": list("abcd"), "site": ["s"] * 4, "serp_day_a": ["2026-11-18"] * 4,
        "serp_day_b": ["2026-11-19"] * 4, "pos_a": [1, 5, 11, 7], "pos_b": [1, 11, 9, 7],
        "edit_tokens": [0, 3, 0, 0], "title_changed": [False, False, True, False], "h1_changed": [False] * 4,
        "material": [False, True, True, False],
    })
    data = ex.prepare(rows)
    assert list(data["moved"]) == [0, 1, 1, 0]
    assert list(data["absd"]) == [0.0, 6.0, 2.0, 0.0]
    assert list(data["crossed"]) == [0, 1, 1, 0]
    assert list(data["band_a"]) == ["1", "4-6", "11", "7-10"]
    assert list(data["interior"]) == [False, True, False, True]
    assert list(data["anychange"]) == [False, True, True, False]
    assert data["stratum"].iloc[1] == "2026-11-18>2026-11-19|4-6"
    assert ex.prepare(rows.iloc[0:0]).empty


@pytest.mark.parametrize("diff, lo, hi, held, falsified", [
    (0.083, -0.082, 0.267, True, False),
    (0.150, 0.020, 0.300, False, False),  # interval above zero
    (0.250, 0.050, 0.400, False, True),  # the theory's falsifier
    (0.250, -0.050, 0.500, False, False),  # above +0.20, interval includes zero
    (-0.100, -0.300, 0.050, True, False),
])
def test_prediction_check(diff, lo, hi, held, falsified):
    check = ex.prediction_check(_comparisons(diff, lo, hi), COUNTS)
    assert check["label"] == "PREDICTED" and check["evaluable"]
    assert check["prediction_held"] is held and check["falsifier_met"] is falsified
    assert check["observed_diff"] == diff and check["ci_lo"] == lo and check["ci_hi"] == hi
    assert check["counts"] == {"changed_rows": 30, "matched_rows": 28, "changed_urls": 12, "sites": 6,
                               "control_rows_in_matched_strata": 400, "queries": 50}


def test_prediction_check_without_matched_rows():
    check = ex.prediction_check(_comparisons(None, None, None), COUNTS)
    assert check["evaluable"] is False and check["prediction_held"] is None and check["falsifier_met"] is False


@pytest.mark.skipif(not DESIGN_ROWS.exists(), reason="committed design rows missing")
def test_design_reproduction_from_committed_design_rows():
    """The design study's own rows (days <= 2026-11-17) give the numbers in notes.md Q1."""
    rows = pd.read_csv(DESIGN_ROWS)
    rows["usable"] = True
    rows["day_a"], rows["day_b"] = rows["serp_day_a"], rows["serp_day_b"]  # equal on design data (t2_dataset docstring)
    report, data = ex.analyse(rows, {q: "o30" for q in rows["query"]}, design=True, max_date=ex.DESIGN_MAX_DATE)
    repro = report["design_reproduction"]
    assert repro["ok"], [c for c in repro["checks"] if c["mismatch"]]
    assert len(repro["checks"]) == len(ex.DESIGN_EXPECTED)
    check = report["prediction_check"]
    assert check["prediction_held"] is True and check["falsifier_met"] is False
    assert (check["counts"]["changed_rows"], check["counts"]["matched_rows"], check["counts"]["changed_urls"],
            check["counts"]["sites"]) == (28, 25, 11, 5)
    assert check["observed_diff"] == pytest.approx(0.083, abs=0.0005)
    interior = report["comparisons"]["material_vs_not_start_2_10"]["moved"]["query"]
    assert (round(interior["diff"], 3), round(interior["lo"], 3), round(interior["hi"], 3)) == (0.215, 0.033, 0.400)


def test_design_reproduction_flags_a_mismatch():
    rows = pd.read_csv(DESIGN_ROWS) if DESIGN_ROWS.exists() else pytest.skip("committed design rows missing")
    rows["usable"] = True
    rows["day_a"], rows["day_b"] = rows["serp_day_a"], rows["serp_day_b"]
    rows = rows[rows["site"] != "agtek.com"]  # change the data: the check must fail
    report, _ = ex.analyse(rows, {q: "o30" for q in rows["query"]}, design=True, max_date=ex.DESIGN_MAX_DATE)
    assert report["design_reproduction"]["ok"] is False


# ------------------------------------------------------------------ Instrument A: guards


def _prereg(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "manifest.json").write_text("{}", encoding="utf-8")
    return folder


def _synthetic_short_rows() -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(3)
    records = []
    for q in range(12):
        for k in range(10):
            pos_a = int(rng.integers(1, 12))
            records.append({
                "query": f"q{q}", "key": f"u{k}", "site": f"s{k % 4}.com", "day_a": "2026-11-17", "day_b": "2026-11-19",
                "serp_day_a": "2026-11-17", "serp_day_b": "2026-11-19", "usable": True, "material": k < 2,
                "edit_tokens": 300 if k < 3 else 0, "title_changed": k == 0, "h1_changed": False,
                "pos_a": pos_a, "pos_b": int(np.clip(pos_a + rng.integers(-2, 3), 1, 11)),
            })
    rows = pd.DataFrame(records)
    return rows, {f"q{q}": ("o30" if q % 2 else "a70") for q in range(12)}


def test_excess_heldout_refuses_design_days_and_checks_the_freeze_before_loading(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ex, "load_inputs", lambda max_date: calls.append("load") or pytest.fail("loaded data"))
    with pytest.raises(SystemExit, match="Not a held-out day"):
        ex.run("2026-11-17", _prereg(tmp_path, "claude-r2-t2-excess"), design=False)
    with pytest.raises(SystemExit, match="manifest"):
        ex.run("2026-11-19", tmp_path / "missing", design=False)

    def not_frozen(directory):
        calls.append("freeze")
        raise SystemExit("not frozen")

    monkeypatch.setattr(ex, "require_frozen_dir", not_frozen)
    with pytest.raises(SystemExit, match="not frozen"):
        ex.run("2026-11-19", tmp_path / "claude-r2-t2-excess", design=False)
    assert calls == ["freeze"]
    with pytest.raises(SystemExit, match="on or before 2026-11-17"):
        ex.run("2026-11-18", None, design=True)
    assert calls == ["freeze"]


def test_excess_heldout_writes_once_and_reports_the_prediction(tmp_path, monkeypatch):
    prereg = _prereg(tmp_path, "claude-r2-t2-excess")
    calls = []
    rows, groups = _synthetic_short_rows()
    monkeypatch.setattr(ex, "OUT_HELDOUT", tmp_path / "out")
    monkeypatch.setattr(ex, "require_frozen_dir", lambda d: calls.append("freeze") or {"frozen_at_utc": "T"})
    monkeypatch.setattr(ex, "load_inputs", lambda max_date: calls.append("load") or (rows, groups))
    report = ex.run("2026-11-19", prereg, design=False)
    assert calls == ["freeze", "load"]
    folder = tmp_path / "out" / "claude-r2-t2-excess"
    saved = json.loads((folder / "excess-2026-11-19.json").read_text(encoding="utf-8"))
    assert (folder / "excess-2026-11-19.md").is_file() and (folder / "excess-2026-11-19-rows.csv").is_file()
    check = saved["prediction_check"]
    for field in ("observed_diff", "ci_lo", "ci_hi", "below_plus_0_20", "interval_includes_zero", "prediction_held",
                  "falsifier_met", "statement", "counts"):
        assert field in check
    assert check["counts"]["changed_rows"] == 24 and check["counts"]["changed_urls"] == 2 and check["counts"]["sites"] == 2
    assert saved["mode"] == "held-out" and saved["manifest_frozen_at"] == "T" and "design_reproduction" not in saved
    assert set(saved["comparisons"]) == set(ex.comparison_sets(ex.prepare(rows)).keys())
    assert report["counts"]["by_group"] == {"a70": 60, "o30": 60}
    with pytest.raises(SystemExit, match="already exists"):
        ex.run("2026-11-19", prereg, design=False)
    assert calls == ["freeze", "load", "freeze"]
    ex.run("2026-11-19", prereg, design=False, force=True)
    assert calls[-1] == "load"


# ------------------------------------------------------------------ Instrument B: levels


def test_round_half_up():
    assert [lw.round_half_up(v) for v in (1.0, 2.5, 3.49, 3.5, 10.5, 11.0)] == [1, 3, 3, 4, 11, 11]


def _cons(records) -> pd.DataFrame:
    return pd.DataFrame(records, columns=["query", "day", "key", "cons"])


def test_window_levels_use_pulled_days_absent_as_11_and_need_two_days():
    cons = _cons([
        ("q", "2026-11-15", "x", 2),  # before the pre window
        ("q", "2026-11-16", "x", 3), ("q", "2026-11-18", "y", 1), ("q", "2026-11-19", "x", 5),
        ("q", "2026-12-04", "x", 2), ("q", "2026-12-05", "y", 4), ("q", "2026-12-20", "x", 1),  # B+28 is 12-19
    ])
    rows = pd.DataFrame({"query": ["q", "q"], "key": ["x", "y"], "day_a": ["2026-11-19"] * 2, "day_b": ["2026-11-21"] * 2})
    data = lw.add_levels(rows, cons, lw.POST_WINDOW, max_date="2026-12-31")
    x, y = data.iloc[0], data.iloc[1]
    assert x["pre_days"] == "2026-11-16 2026-11-18 2026-11-19" and x["pre"] == pytest.approx((3 + 11 + 5) / 3)
    assert x["post_days"] == "2026-12-05" and x["n_post"] == 1 and not x["has_levels"]  # 12-04 is B+13
    assert y["pre"] == pytest.approx((11 + 1 + 11) / 3) and y["post"] == 4.0
    assert bool(x["post_window_complete"])
    early = lw.add_levels(rows, cons, lw.POST_WINDOW, max_date="2026-12-10")
    assert not early["post_window_complete"].any()
    wide = lw.add_levels(rows, cons, (13, 28), max_date="2026-12-31")
    assert wide.iloc[0]["n_post"] == 2 and bool(wide.iloc[0]["has_levels"]) and wide.iloc[0]["post"] == pytest.approx((2 + 11) / 2)


def _long_rows(pre_post: list[tuple[float, float]], material: list[bool], keys: list[str] | None = None) -> pd.DataFrame:
    n = len(pre_post)
    return pd.DataFrame({
        "query": [f"q{i % 3}" for i in range(n)], "key": keys or [f"u{i}" for i in range(n)], "site": ["s.com"] * n,
        "day_a": ["2026-11-19"] * n, "day_b": ["2026-11-20"] * n, "pre": [p for p, _ in pre_post],
        "post": [b for _, b in pre_post], "has_levels": [True] * n, "post_window_complete": [True] * n,
        "material": material, "edit_tokens": [0] * n, "title_changed": [False] * n, "h1_changed": [False] * n,
    })


def test_long_window_outcomes_strata_and_clean_controls():
    frame = _long_rows([(3.0, 3.9), (3.0, 4.0), (3.0, 2.0), (10.6, 9.0), (2.5, 6.0)],
                       [True, False, False, False, False], keys=["a", "b", "c", "a", "d"])
    changes = {"a": ["2026-11-20"], "b": ["2026-12-30"], "c": ["2026-12-19"]}
    data = lw.prepare(frame, changes)
    assert list(data["moved"]) == [0, 1, 1, 1, 1]
    assert list(data["move_long"]) == ["down", "down", "up", "up", "down"]
    assert list(data["pre_round"]) == [3, 3, 3, 11, 3]
    assert list(data["band_pre"]) == ["2-3", "2-3", "2-3", "11", "2-3"]
    assert data["stratum"].iloc[3] == "2026-11-19>2026-11-20|11"
    assert list(data["interior"]) == [True, True, True, False, True]
    # The window for a clean control is [day_a - 28, day_b + 28] = [2026-10-22, 2026-12-18]. b and c change after it
    # (clean), a changes inside it (not clean), d never changes (clean). The changed row is never a control.
    assert list(data["clean_control"]) == [False, True, True, False, True]
    assert lw.prepare(frame.assign(has_levels=False), changes).empty


def test_feature_mapping_replaces_pos_a_with_the_rounded_pre_level():
    frame = _long_rows([(11.0, 6.0), (1.2, 4.0), (5.0, 3.0), (5.0, 7.0)], [False, False, True, False])
    frame["pos_a"], frame["move"] = [3, 3, 9, 5], ["none"] * 4
    frame["has_prev"] = [False, False, False, True]
    frame["hist_mean_before"] = [np.nan, np.nan, np.nan, 2.0]
    frame["q_title_delta"], frame["q_h1_delta"] = [0, 0, 1, 0], [0, 0, 0, 0]
    out = lw.add_predictions(lw.prepare(frame, {}), dict(DEFAULT_PARAMS))
    assert list(out["pos_a"]) == [11, 1, 5, 5] and list(out["pos_a_short"]) == [3, 3, 9, 5]
    assert list(out["in_top10_a"]) == [False, True, True, True]
    assert list(out["pred_both_rule"]) == ["up", "down", "up", "up"]  # row 3: content step; row 4: 5 - 2.0 > 0.5 gives up
    assert list(out["pred_position_rule"]) == ["up", "down", "down", "up"]  # row 3 falls to the default class
    assert list(out["pred_content_rule"]) == ["down", "down", "up", "down"]
    assert set(out["pred_majority"]) == {"down"} and set(out["pred_label"]) == {"PREDICTED"}
    assert list(out["move"]) == ["up", "down", "up", "down"] and list(out["move_short"]) == ["none"] * 4


# ------------------------------------------------------------------ Instrument B: guards and a full synthetic run


def _synthetic_long_inputs(seed: int = 5):
    """60 searches, 8 URLs each; URL u0 and u1 change on the 2026-11-19 to 2026-11-20 fetch pair."""
    rng = np.random.default_rng(seed)
    pre_days = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]
    post_days = ["2026-12-04", "2026-12-05", "2026-12-06"]
    rows, cons, groups = [], [], {}
    for q in range(60):
        query = f"q{q:02d}"
        groups[query] = "o30" if q % 2 else "a70"
        for k in range(8):
            key = f"u{k}"
            level = int(rng.integers(1, 12))
            for day in pre_days:
                if level <= 10:
                    cons.append((query, day, key, level))
            after = int(np.clip(level + rng.integers(-3, 4), 1, 11))
            for day in post_days:
                if after <= 10:
                    cons.append((query, day, key, after))
            rows.append({
                "query": query, "key": key, "site": f"s{k % 3}.com", "day_a": "2026-11-19", "day_b": "2026-11-20",
                "serp_day_a": "2026-11-19", "serp_day_b": "2026-11-20", "usable": True, "material": k < 2,
                "edit_tokens": 400 if k < 2 else 0, "title_changed": k == 0, "h1_changed": False,
                "pos_a": level, "pos_b": level, "move": "none", "has_prev": True, "hist_mean_before": float(level),
                "q_title_delta": 1 if k == 0 else 0, "q_h1_delta": 0,
            })
    # A design-day row (fetch day B before 2026-11-19) is not an event.
    rows.append({**rows[0], "day_a": "2026-11-16", "day_b": "2026-11-17", "serp_day_b": "2026-11-17"})
    return pd.DataFrame(rows), groups, _cons(cons)


def test_longwindow_refuses_early_dates_and_checks_the_freeze_first(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lw, "load_inputs", lambda max_date: calls.append("load") or pytest.fail("loaded data"))

    def not_frozen(directory):
        calls.append("freeze")
        raise SystemExit("not frozen")

    monkeypatch.setattr(lw, "require_frozen_dir", not_frozen)
    prereg = _prereg(tmp_path, "claude-r2-t2-longwindow")
    with pytest.raises(SystemExit, match="2026-12-03"):
        lw.run("2026-12-02", prereg, dry_run=False)
    with pytest.raises(SystemExit, match="on or before 2026-11-17"):
        lw.run("2026-11-18", None, dry_run=True)
    assert calls == []
    with pytest.raises(SystemExit, match="not frozen"):
        lw.run("2026-12-04", prereg, dry_run=False)
    assert calls == ["freeze"]


def test_longwindow_full_synthetic_run(tmp_path, monkeypatch):
    prereg = _prereg(tmp_path, "claude-r2-t2-longwindow")
    rows, groups, cons = _synthetic_long_inputs()
    calls = []
    monkeypatch.setattr(ex, "OUT_HELDOUT", tmp_path / "out")
    monkeypatch.setattr(lw, "require_frozen_dir", lambda d: calls.append("freeze") or {"frozen_at_utc": "T"})
    monkeypatch.setattr(lw, "load_inputs", lambda max_date: calls.append("load") or (rows, groups, cons))
    lw.run("2026-12-06", prereg, dry_run=False)
    assert calls == ["freeze", "load"]
    folder = tmp_path / "out" / "claude-r2-t2-longwindow"
    saved = json.loads((folder / "longwindow-2026-12-06.json").read_text(encoding="utf-8"))
    assert "PREDICTED" in saved["label"] and saved["direction"]["label"] == "PREDICTED"
    count = saved["counts"]
    assert count["selected_rows"] == 480 and count["rows_with_levels"] == 480 and count["changed_rows"] == 120
    assert count["rows_with_complete_post_window"] == 0  # B + 28 is after 2026-12-06
    assert count["clean_control_rows"] == 360
    main = saved["excess_movement"]["material_vs_not"]["moved"]["query"]
    assert main["treated_rows"] == 120 and main["lo"] <= main["diff"] <= main["hi"]
    decision = saved["direction"]["decision"]
    assert set(decision) == {"n", "n_at_least_40", "l_brief_wording", "h_content_beats_position_only", "target2_pass",
                             "info_position_only_rule_meets_l"}
    block = saved["direction"]["slices"]["changed_moved"]
    assert decision["n"] == block["n"] and decision["n_at_least_40"] == (block["n"] >= 40)
    assert decision["target2_pass"] == (decision["l_brief_wording"] and decision["h_content_beats_position_only"])
    frame = pd.read_csv(folder / "longwindow-2026-12-06-rows.csv")
    moved = frame[(frame["moved"] == 1) & frame["material"]]
    assert block["n"] == len(moved)
    assert block["pred_both_rule"]["hits"] == int((moved["pred_both_rule"] == moved["move_long"]).sum())
    with pytest.raises(SystemExit, match="already exists"):
        lw.run("2026-12-06", prereg, dry_run=False)


def test_longwindow_with_no_rows_yet(tmp_path, monkeypatch):
    rows, groups, cons = _synthetic_long_inputs()
    report, data = lw.analyse(rows, groups, cons[cons["day"] < "2026-12-01"], dry_run=False, max_date="2026-12-03")
    assert data.empty and report["counts"]["rows_with_levels"] == 0 and report["direction"]["decision"]["n"] == 0
    assert "No row has both levels yet." in lw.markdown(report)


# ------------------------------------------------------------------ Real store, design days only


@pytest.fixture(scope="module")
def design_inputs():
    if not Path(rs.DB).exists():
        pytest.skip("raw SQLite store not present on this machine")
    from scripts.claude import t2_common

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(t2_common, "EXTRACT_CACHE", ex.DESIGN_CACHE)
        rows, groups, cons = lw.load_inputs(ex.DESIGN_MAX_DATE)
    assert cons["day"].max() <= ex.DESIGN_MAX_DATE and rows["day_b"].max() <= ex.DESIGN_MAX_DATE
    return rows, groups, cons


@needs_db
def test_design_reproduction_through_the_sealed_loaders(design_inputs):
    rows, groups, _cons_table = design_inputs
    report, _ = ex.analyse(rows, groups, design=True, max_date=ex.DESIGN_MAX_DATE)
    assert report["design_reproduction"]["ok"], [c for c in report["design_reproduction"]["checks"] if c["mismatch"]]
    stored = json.loads(DESIGN_RESULTS.read_text(encoding="utf-8"))["q1_excess_movement"]
    pairs = (("material_vs_not", stored["material_vs_not"]["moved"]["joint_query"]),
             ("material_vs_not_start_2_10", stored["material_vs_not_interior"]["moved"]),
             ("any_change_vs_none", stored["anychange_vs_none"]["moved"]))
    for name, want in pairs:
        got = report["comparisons"][name]["moved"]["query"]
        for field in ("treated_mean", "matched_control_mean", "diff", "lo", "hi"):
            assert got[field] == pytest.approx(want[field], abs=1e-6), (name, field)


@needs_db
def test_longwindow_dry_run_code_path_on_design_days(design_inputs):
    rows, groups, cons = design_inputs
    report, data = lw.analyse(rows, groups, cons, dry_run=True, max_date=ex.DESIGN_MAX_DATE)
    assert report["label"].startswith("CODE CHECK ONLY") and report["mode"].startswith("dry-run")
    assert report["counts"]["rows_with_levels"] > 0 and report["counts"]["changed_rows"] > 0
    assert data["post_days"].map(lambda days: all(day <= ex.DESIGN_MAX_DATE for day in days.split())).all()
    assert "CODE CHECK ONLY" in lw.markdown(report)
