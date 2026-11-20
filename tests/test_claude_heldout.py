"""Held-out scoring and freezing for the Claude goal experiments. Offline."""

from __future__ import annotations

import json

import pytest

pd = pytest.importorskip("pandas")

from scripts.claude import freeze
from scripts.claude import heldout as ho
from scripts.claude import metrics as m


def _rows(records):
    frame = pd.DataFrame(records, columns=["query", "day", "fetched_at", "position", "url", "source"])
    frame["kind"] = "top10"
    frame["title"] = ""
    frame["key"] = frame["url"]
    frame["site"] = frame["url"].str.replace("https://", "").str.split("/").str[0]
    return frame


def _pull(query, day, order):
    return [(query, day, f"{day}T14:00:00.000Z", index + 1, f"https://{name}/", "s") for index, name in enumerate(order)]


def test_band3_and_err3_follow_their_definitions():
    assert m.band3_hit(1, 3) and not m.band3_hit(1, 4)
    assert m.band3_hit(7, 6) and m.band3_hit(7, 8) and not m.band3_hit(7, 9)
    assert m.band3_hit(11, 10) and not m.band3_hit(11, 9)
    assert m.err3_hit(7, 10) and m.err3_hit(7, 4) and not m.err3_hit(7, 3)


def test_t1_frame_gives_the_predictor_only_earlier_days():
    from scripts.claude import rsdata as rs

    records = _pull("q", "2026-11-16", ["a.com", "b.com", "c.com"]) + _pull("q", "2026-11-17", ["a.com", "new.com", "b.com"])
    rows = _rows(records)
    cons = rs.consensus_table(rows)
    seen = {}

    def predictor(history, targets):
        seen["max_day"] = history["day"].max()
        seen["columns"] = list(targets.columns)
        return [2] * len(targets)

    frame = ho.t1_frame(rows, cons, "2026-11-17", predictor, deep_queries=set())
    assert seen["max_day"] == "2026-11-16"
    assert "truth" not in seen["columns"]
    assert list(frame["url"]) == ["https://new.com/"]
    assert frame.iloc[0]["klass"] == "new" and not bool(frame.iloc[0]["strict_new"])
    assert frame.iloc[0]["truth"] == 2 and frame.iloc[0]["band3_pred"] == 1 and frame.iloc[0]["band3_const7"] == 0
    summary = ho.t1_summary(frame)
    assert summary["band3"]["pred"]["hits"] == 1
    assert summary["big_enough"] is False and summary["band3"]["pass"] is False


def test_t3_flip_accuracy_and_the_persistence_guard():
    from scripts.claude import rsdata as rs

    records = _pull("q", "2026-11-16", ["a.com", "b.com", "c.com"]) + _pull("q", "2026-11-17", ["b.com", "a.com", "c.com"])
    rows = _rows(records)
    cons = rs.consensus_table(rows)

    def persistence(history, query, keys, day_b):
        assert history["day"].max() < day_b
        last = history[history["day"] == history["day"].max()]
        return {key: float(dict(zip(last["key"], last["position"], strict=True)).get(key, 11)) for key in keys}

    def reverse(history, query, keys, day_b):
        return {key: -value for key, value in persistence(history, query, keys, day_b).items()}

    kept = ho.t3_summary(ho.t3_frame(rows, cons, "2026-11-16", "2026-11-17", persistence))
    assert kept["flips"] == 1 and kept["flip_accuracy"]["hits"] == 0 and kept["guard_pass"] is True
    flipped = ho.t3_summary(ho.t3_frame(rows, cons, "2026-11-16", "2026-11-17", reverse))
    assert flipped["flip_accuracy"]["hits"] == 1
    assert flipped["all_pairs_model"]["hits"] == 1 and flipped["all_pairs_persistence"]["hits"] == 2
    assert flipped["guard_pass"] is False


def test_manifest_detects_a_changed_file(tmp_path, monkeypatch):
    monkeypatch.setattr(freeze, "ROOT", tmp_path)
    target = tmp_path / "model.json"
    target.write_text(json.dumps({"weight": 1}), encoding="utf-8")
    freeze.write_manifest(tmp_path / "prereg", ["model.json"])
    assert freeze.verify_manifest(tmp_path / "prereg")["sha256"]["model.json"] == freeze.sha256_file(target)
    target.write_text(json.dumps({"weight": 2}), encoding="utf-8")
    with pytest.raises(SystemExit):
        freeze.verify_manifest(tmp_path / "prereg")


def test_scorer_b_calls_a_flip_only_when_a_pull_showed_the_reverse_order():
    from scripts.claude import t3_contested

    day = "2026-11-16"
    records = (
        [("q", day, f"{day}T08:00:00.000Z", 1, "https://b.com/", "m"), ("q", day, f"{day}T08:00:00.000Z", 2, "https://a.com/", "m")]
        + [("q", day, f"{day}T21:00:00.000Z", 1, "https://a.com/", "e"), ("q", day, f"{day}T21:00:00.000Z", 2, "https://b.com/", "e"),
           ("q", day, f"{day}T21:00:00.000Z", 3, "https://c.com/", "e")]
    )
    rows = _rows(records)
    a, b, c, gone = "https://a.com/", "https://b.com/", "https://c.com/", "https://never.com/"
    calls = t3_contested.call_flips(rows, "q", [(a, b), (a, c), (b, c), (c, gone), (gone, a)], "2026-11-17")
    # the morning pull (another session) showed b above a; c was absent there (11) and a present, so a stays above c
    # b above c in both pulls; c present vs never-seen url: no pull shows the reverse; never-seen vs a: a is above it in every pull
    assert calls == [True, False, False, False, True]
    with pytest.raises(AssertionError):
        t3_contested.call_flips(rows, "q", [(a, b)], day)


def test_query_bootstrap_matches_a_brute_force_reference():
    import numpy as np

    frame = pd.DataFrame({"query": list("aaabbcdddd"), "h": [1, 0, 1, 1, 1, 0, 0, 1, 0, 0], "b": [0, 0, 1, 1, 0, 0, 1, 0, 0, 0]})
    got = m.bootstrap_rate(frame, "h", draws=500, seed=7)
    diff = m.bootstrap_difference(frame, "h", "b", draws=500, seed=7)
    groups = [part for _, part in frame.groupby("query", sort=True)]
    rng = np.random.default_rng(7)
    index = rng.integers(0, len(groups), size=(500, len(groups)))
    rates, diffs = [], []
    for draw in index:
        picked = pd.concat([groups[i] for i in draw])
        rates.append(picked["h"].mean())
        diffs.append(picked["h"].mean() - picked["b"].mean())
    assert got["rate"] == pytest.approx(0.5) and got["n"] == 10 and got["queries"] == 4
    assert got["lo"] == pytest.approx(np.percentile(rates, 2.5)) and got["hi"] == pytest.approx(np.percentile(rates, 97.5))
    assert diff["diff"] == pytest.approx(0.2)
    assert diff["lo"] == pytest.approx(np.percentile(diffs, 2.5)) and diff["hi"] == pytest.approx(np.percentile(diffs, 97.5))
    assert m.bootstrap_rate(frame.iloc[0:0], "h")["n"] == 0
    assert m.bootstrap_difference(frame.iloc[0:0], "h", "b")["diff"] is None


def test_pooled_keeps_the_decision_searches_and_consecutive_days(tmp_path, monkeypatch):
    from scripts.claude import score_heldout as sh

    monkeypatch.setattr(sh, "OUT", tmp_path)

    def pairs(day_a, day_b, group, query, flipped, calls):
        return pd.DataFrame(
            {
                "query": query, "day_a": day_a, "day_b": day_b, "key_x": [f"x{i}" for i in range(len(flipped))],
                "key_y": [f"y{i}" for i in range(len(flipped))], "flipped": flipped, "calls_flip": calls,
                "correct": [int(f == c) for f, c in zip(flipped, calls, strict=True)],
                "persist_correct": [int(not f) for f in flipped], "grp": group, "scorer": "B",
            }
        )

    pairs("2026-11-18", "2026-11-19", "o30", "q1", [True, False, False], [True, False, True]).to_csv(tmp_path / "t3B-2026-11-18_2026-11-19-pairs.csv", index=False)
    pairs("2026-11-18", "2026-11-19", "rev", "r1", [True, True], [False, False]).to_csv(tmp_path / "t3B-2026-11-18_2026-11-19b-pairs.csv", index=False)
    pairs("2026-11-16", "2026-11-19", "a70", "q2", [True, True], [True, True]).to_csv(tmp_path / "t3B-2026-11-16_2026-11-19-pairs.csv", index=False)
    report = sh.pooled()
    consecutive = report["t3B"]["slices"]["consecutive_days"]
    assert consecutive["pairs"] == 3 and consecutive["flips"] == 1 and consecutive["flip_accuracy"]["hits"] == 1
    assert report["t3B"]["slices"]["all_transitions"]["pairs"] == 5
    assert report["t3B"]["decision"]["c1_brief_wording"] is False
    assert report["t3_decision"] == {"scorers_tested": 1, "target3_pass": False}
