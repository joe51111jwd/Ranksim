"""Frozen benchmark snapshots: deterministic split, tamper checks, reproducible dev runs, one final run
per model configuration. Fixtures are synthetic pages and labels, not Google data."""

import gzip
import json

import pytest

from aeo.ranksim.benchmark import feature_scorer, run_final
from aeo.ranksim.cli import main
from aeo.ranksim.lambdamart import RankExample, model_id
from aeo.ranksim.snapshot import SnapshotError, final_runs, freeze, load_manifest, materialize, record_final, split_clusters

QUERIES = 8


def _corpus(tmp_path):
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = ["query,url,position"]
    for q in range(QUERIES):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = (
                f"<html><head><title>dirt work software {position}</title></head><body><h1>Dirt work</h1>"
                f"<p>{words} {'filler ' * 200}</p></body></html>"
            )
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}")
    store_fetched_page(session, FetchResult(url="https://down.example/", html="", status="error", error="HTTP 404"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text("\n".join(lines) + "\n")
    return db, serp


def test_split_is_deterministic_disjoint_and_keeps_clusters_whole():
    clusters = {f"q{index}": index // 2 for index in range(12)}  # 6 clusters of 2 queries
    dev, final = split_clusters(clusters, final_share=0.3, seed=0)
    assert (dev, final) == split_clusters(clusters, final_share=0.3, seed=0)
    assert dev.isdisjoint(final) and dev | final == set(clusters)
    assert len(final) == 4  # ceil(0.3 * 12) queries, whole clusters only
    for query in final:
        partner = f"q{clusters[query] * 2 + (1 - int(query[1:]) % 2)}"
        assert partner in final
    other_seed = [split_clusters(clusters, final_share=0.3, seed=seed)[1] for seed in range(1, 6)]
    assert any(choice != final for choice in other_seed)


def test_a_large_cluster_cannot_take_most_queries():
    clusters = {f"big{index}": 0 for index in range(8)} | {f"small{index}": index + 1 for index in range(8)}
    for seed in range(10):
        dev, final = split_clusters(clusters, final_share=0.3, seed=seed)
        assert dev and final
        assert len(final) <= 8


def test_a_single_cluster_cannot_be_split():
    with pytest.raises(SnapshotError, match="2 query clusters"):
        split_clusters({"a": 0, "b": 0}, final_share=0.3, seed=0)


def test_freeze_writes_manifest_labels_and_pages_and_never_overwrites(tmp_path):
    db, serp = _corpus(tmp_path)
    out = tmp_path / "snap"
    manifest = freeze(db, serp, out, as_of="2026-11-01", intents={"dirt work software 0": "commercial"})
    assert (out / "labels.csv").read_bytes() == serp.read_bytes()
    assert manifest["pages"] == QUERIES * 3 + 1
    assert manifest["intents"]["dirt work software 0"] == "commercial"
    assert manifest["intents"]["dirt work software 1"] == "unknown"
    assert "Keep out of git" in manifest["notice"]
    with gzip.open(out / "pages.jsonl.gz", "rt") as handle:
        records = [json.loads(line) for line in handle]
    assert {row["status"] for row in records} == {"fetched", "error"}
    assert all(row["source"] for row in records)
    split = manifest["split"]
    assert set(split["dev_queries"]).isdisjoint(split["final_queries"])
    with pytest.raises(SnapshotError, match="not overwritten"):
        freeze(db, serp, out, as_of="2026-11-01")


def test_a_changed_snapshot_is_refused(tmp_path):
    db, serp = _corpus(tmp_path)
    out = tmp_path / "snap"
    freeze(db, serp, out, as_of="2026-11-01")
    load_manifest(out)
    labels = out / "labels.csv"
    labels.write_text(labels.read_text().replace(",1\n", ",2\n", 1))
    with pytest.raises(SnapshotError, match="labels.csv"):
        load_manifest(out)


def test_materialize_keeps_html_source_and_fetch_time(tmp_path):
    from aeo.ranksim.store import RsPage, open_session

    db, serp = _corpus(tmp_path)
    out = tmp_path / "snap"
    freeze(db, serp, out, as_of="2026-11-01")
    copy = f"sqlite:///{tmp_path / 'copy.db'}"
    materialize(out, copy)
    live, frozen = open_session(db), open_session(copy)
    try:
        for row in live.query(RsPage).all():
            twin = frozen.get(RsPage, row.url)
            assert twin.raw_html == row.raw_html
            assert twin.status == row.status
            assert twin.source == row.source
            assert twin.word_count == row.word_count
            if row.status == "fetched":
                assert twin.fetched_at.replace(tzinfo=None) == row.fetched_at.replace(tzinfo=None)
        with pytest.raises(SnapshotError, match="empty database"):
            materialize(out, copy)
    finally:
        live.close()
        frozen.close()


def test_record_final_refuses_a_second_run_for_the_same_model(tmp_path):
    record_final(tmp_path, {"model_id": "lgbm-a", "value": 1})
    record_final(tmp_path, {"model_id": "lgbm-b", "value": 2})
    with pytest.raises(SnapshotError, match="already has a final-split result"):
        record_final(tmp_path, {"model_id": "lgbm-a", "value": 3})
    assert [row["value"] for row in final_runs(tmp_path)] == [1, 2]


def test_model_id_changes_with_features_or_monotone_set():
    base = model_id(["bm25f", "word_count"])
    assert base == model_id(["bm25f", "word_count"])
    assert base != model_id(["bm25f", "word_count", "tables"])
    assert base != model_id(["bm25f", "word_count"], frozenset({"bm25f"}))


def _row(query, url, position, signal):
    return RankExample(query, url, float(position), {"signal": float(signal), "bm25f": float(signal)})


def test_run_final_trains_on_dev_and_scores_only_final_queries():
    examples = []
    for q in range(6):
        for position in range(1, 4):
            examples.append(_row(f"q{q}", f"https://s{q}.example/{position}", position, 4 - position))
    clusters = {f"q{q}": q for q in range(6)}
    seen_train = []

    def spy(train, test):
        seen_train.append({row.query for row in train})
        return feature_scorer("signal")(train, test)

    results = run_final(examples, {"q4", "q5"}, clusters, {"signal": spy}, bootstrap=50)
    assert [row.protocol for row in results] == ["final", "final-url"]
    for result in results:
        scorer = result.scorers[0]
        assert {row.query for row in scorer.queries} == {"q4", "q5"}
        assert scorer.n_pairs == 2 * 3
        assert scorer.pairwise.point == 1.0
        assert result.random_band is not None
    assert all(train == {"q0", "q1", "q2", "q3"} for train in seen_train)
    with pytest.raises(ValueError, match="nothing to score"):
        run_final(examples, {"missing"}, clusters, {"signal": spy})


def test_snapshot_dev_run_is_reproducible_and_excludes_final_queries(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    snap = tmp_path / "snap"
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(snap), "--as-of", "2026-11-01"])
    capsys.readouterr()
    manifest = load_manifest(snap)
    outputs = []
    for name in ("a.json", "b.json"):
        main(["benchmark", "--snapshot", str(snap), "--bootstrap", "20", "--json", str(tmp_path / name)])
        capsys.readouterr()
        payload = json.loads((tmp_path / name).read_text())
        payload.pop("created_at")
        outputs.append(payload)
    assert outputs[0] == outputs[1]
    assert outputs[0]["as_of"] == "2026-11-01"
    assert set(outputs[0]["clusters"]) == set(manifest["split"]["dev_queries"])
    assert not set(outputs[0]["clusters"]) & set(manifest["split"]["final_queries"])


def test_final_runs_once_per_model_and_appends(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    snap = tmp_path / "snap"
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(snap), "--as-of", "2026-11-01"])
    capsys.readouterr()
    main(["benchmark", "--snapshot", str(snap), "--final", "--bootstrap", "20"])
    out = capsys.readouterr().out
    assert "[final]" in out and "[final-url]" in out
    assert "chosen on held-out dev pairs" in out
    runs = final_runs(snap)
    assert len(runs) == 1 and runs[0]["model_id"].startswith("lgbm-")
    assert {row["protocol"] for row in runs[0]["protocols"]} == {"final", "final-url"}
    with pytest.raises(SystemExit, match="already scored on this final split"):
        main(["benchmark", "--snapshot", str(snap), "--final", "--bootstrap", "20"])
    assert len(final_runs(snap)) == 1
    # A different model configuration gets its own single run.
    main(["benchmark", "--snapshot", str(snap), "--final", "--bootstrap", "20", "--with-crawl-artifacts"])
    capsys.readouterr()
    assert len(final_runs(snap)) == 2


def test_final_needs_a_snapshot(tmp_path):
    with pytest.raises(SystemExit):
        main(["--database", f"sqlite:///{tmp_path / 'x.db'}", "benchmark", "--final"])


def test_no_verdict_from_a_bootstrap_over_few_clusters():
    from aeo.ranksim.benchmark import MIN_VERDICT_CLUSTERS, Interval, _verdict

    clear_win = Interval(0.10, 0.08, 0.13)
    assert "beats" in _verdict("lambdamart", "bm25f_only", clear_win, MIN_VERDICT_CLUSTERS)
    few = _verdict("lambdamart", "bm25f_only", clear_win, 3)
    assert "beats" not in few and "No verdict" in few
