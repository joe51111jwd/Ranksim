"""Regression tests for the reviewed snapshot and final-split defects. Synthetic data."""

import json

import pytest

from aeo.ranksim.benchmark import QueryResult, dev_threshold_confidence, feature_scorer
from aeo.ranksim.cli import main
from aeo.ranksim.confidence import PairMargin
from aeo.ranksim.lambdamart import RankExample
from aeo.ranksim.snapshot import final_runs, split_clusters


def _corpus(tmp_path, extra_label_lines=""):
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = ["query,url,position"]
    for q in range(8):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            store_fetched_page(session, FetchResult(url=url, html=f"<html><body><main><p>{words} {'filler ' * 200}</p></main></body></html>", status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}")
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text("\n".join(lines) + "\n" + extra_label_lines)
    return db, serp


def test_a_refrozen_split_does_not_allow_a_second_final_run(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "a"), "--as-of", "2026-11-01"])
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), "--final", "--bootstrap", "10"])
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "b"), "--as-of", "2026-11-01"])
    out = capsys.readouterr().out
    assert "same final split as an earlier snapshot" in out
    with pytest.raises(SystemExit, match="also after re-freezing"):
        main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "b"), "--final", "--bootstrap", "10"])
    assert final_runs(tmp_path / "b") == []
    recorded = final_runs(tmp_path / "a")[0]
    assert recorded["protocols"][0]["n_clusters"] < 10
    assert recorded["protocols"][0]["interval_trustworthy"] is False
    assert all(row["low"] is None for row in recorded["protocols"][0]["scorers"])


def test_snapshot_runs_keep_the_label_import_report(tmp_path, capsys):
    db, serp = _corpus(tmp_path, extra_label_lines="dirt work software 0,not-a-url,4\n")
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "s"), "--as-of", "2026-11-01"])
    manifest = json.loads((tmp_path / "s" / "manifest.json").read_text())
    assert any("dropped" in line for line in manifest["import_report"])
    capsys.readouterr()
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "s"), "--bootstrap", "10", "--json", str(tmp_path / "dev.json")])
    out = capsys.readouterr().out
    assert "dropped" in out
    assert any("dropped" in note for note in json.loads((tmp_path / "dev.json").read_text())["notes"])


def test_a_snapshot_runs_with_its_own_cluster_threshold(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "t"), "--as-of", "2026-11-01", "--cluster-threshold", "0.5"])
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "t"), "--bootstrap", "10", "--json", str(tmp_path / "t.json")])
    assert json.loads((tmp_path / "t.json").read_text())["cluster_threshold"] == 0.5
    with pytest.raises(SystemExit, match="cluster threshold 0.5"):
        main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "t"), "--cluster-threshold", "0.3"])


def test_a_large_cluster_cannot_take_the_final_split():
    clusters = {f"big{index}": 0 for index in range(14)} | {f"small{index}": index + 1 for index in range(6)}
    target = 6  # ceil(0.3 * 20)
    for seed in range(40):
        dev, final = split_clusters(clusters, final_share=0.3, seed=seed)
        assert dev and final and dev.isdisjoint(final)
        assert len(final) <= target
    every_cluster_big = {f"a{index}": 0 for index in range(10)} | {f"b{index}": 1 for index in range(8)}
    dev, final = split_clusters(every_cluster_big, final_share=0.1, seed=0)
    assert len(final) == 8 and len(dev) == 10  # nothing fits the target: the smallest cluster is held out


def _rows(prefix, count, reversed_every_other):
    rows = []
    for q in range(count):
        for position in range(1, 4):
            signal = float(position if (reversed_every_other and q % 2) else 4 - position)
            rows.append(RankExample(f"{prefix}{q}", f"https://{prefix}{q}p{position}.example/", float(position), {"signal": signal, "bm25f": signal}))
    return rows


def _final(right: bool, count: int = 40):
    return [QueryResult(f"f{index}", 100 + index, 2, 0, 0, 1, None, None, [], [PairMargin(1.0, right)]) for index in range(count)]


def test_the_final_threshold_comes_from_dev_pairs_only():
    clusters = {f"d{q}": q for q in range(40)}
    # Dev ordered at chance: no threshold may be chosen, whatever the final pairs look like.
    coin = dev_threshold_confidence(_rows("d", 40, reversed_every_other=True), _final(True), clusters, feature_scorer("signal"), "cluster")
    assert coin.calls == 0 and "No threshold" in coin.note
    # Dev ordered cleanly: a threshold is chosen and is then judged on the final pairs as they are.
    clean = dev_threshold_confidence(_rows("d", 40, reversed_every_other=False), _final(False), clusters, feature_scorer("signal"), "cluster")
    assert clean.calls == 40 and clean.correct == 0
