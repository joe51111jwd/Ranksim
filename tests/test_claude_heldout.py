"""Held-out scoring and freezing for the Claude goal experiments. Offline."""

from __future__ import annotations

import json

import pandas as pd
import pytest

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
