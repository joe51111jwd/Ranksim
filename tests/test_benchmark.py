"""Deterministic tests for the leakage-aware benchmark. Fixtures are synthetic, not Google data."""

import json

from aeo.ranksim.benchmark import (
    corpus_fingerprint,
    cross_fitted_confidence,
    feature_scorer,
    make_folds,
    query_clusters,
    random_band,
    run_benchmark,
    run_protocol,
    split_fold,
    QueryResult,
    _queries_with_pairs_map,
)
from aeo.ranksim.cli import main
from aeo.ranksim.confidence import PairMargin
from aeo.ranksim.lambdamart import RankExample


def _row(query, url, position, signal):
    return RankExample(query, url, float(position), {"signal": float(signal), "bm25f": float(signal)})


def _family(prefix, n_urls, shared=None):
    """A query whose labeled URLs are prefix-unique plus optional shared URLs."""
    urls = [f"https://{prefix}.example/{index}" for index in range(n_urls)] + list(shared or [])
    return urls


def test_clusters_join_queries_with_overlapping_urls_only():
    shared = ["https://shared.example/a", "https://shared.example/b", "https://shared.example/c"]
    examples = []
    for query, urls in (
        ("hcss alternative", _family("x", 2, shared)),
        ("hcss heavyjob alternative", _family("y", 2, shared)),
        ("dirt work software", _family("z", 5)),
    ):
        examples += [_row(query, url, index + 1, 0) for index, url in enumerate(urls)]
    clusters = query_clusters(examples, threshold=0.3)
    assert clusters["hcss alternative"] == clusters["hcss heavyjob alternative"]
    assert clusters["dirt work software"] != clusters["hcss alternative"]


def test_cluster_url_protocol_drops_training_rows_that_share_a_held_out_url():
    examples = [
        _row("q1", "https://a.example/1", 1, 1),
        _row("q1", "https://shared.example/x", 2, 0),
        _row("q2", "https://b.example/1", 1, 1),
        _row("q2", "https://shared.example/x/", 2, 0),  # same page, trailing slash
        _row("q2", "https://b.example/2", 3, 0),
    ]
    usable = _queries_with_pairs_map(examples)
    clusters = {"q1": 0, "q2": 1}
    fold = make_folds(sorted(usable), clusters, "cluster-url")[0]
    train, test, dropped = split_fold(usable, fold, "cluster-url")
    assert {row.query for row in test} == {"q1"}
    assert dropped == 1
    assert all("shared.example" not in row.url for row in train)
    _train, _test, kept = split_fold(usable, fold, "cluster")
    assert kept == 0


def test_perfect_feature_scores_one_and_random_band_centres_on_half():
    examples = []
    for q in range(6):
        for position in range(1, 6):
            examples.append(_row(f"query {q}", f"https://site{q}.example/{position}", position, 10 - position))
    clusters = query_clusters(examples)
    result = run_protocol(examples, "loqo", clusters, {"oracle": feature_scorer("signal")}, bootstrap=200)
    oracle = result.scorers[0]
    assert oracle.pairwise.point == 1.0
    assert oracle.n_pairs == 6 * 10
    assert oracle.pairwise.low == 1.0 and oracle.pairwise.high == 1.0
    band = random_band(examples, seeds=400, seed=1)
    assert 0.45 < band.mean < 0.55
    assert band.low < 0.5 < band.high


def test_score_ties_are_counted_and_scored_as_wrong():
    examples = [_row("q", f"https://t.example/{index}", index + 1, 0) for index in range(3)]
    examples += [_row("r", f"https://u.example/{index}", index + 1, 3 - index) for index in range(3)]
    result = run_protocol(examples, "loqo", {"q": 0, "r": 1}, {"flat": feature_scorer("signal")}, bootstrap=0)
    row = result.scorers[0]
    assert row.n_ties == 3
    assert row.n_pairs == 6
    assert row.pairwise.point == 0.5


def test_cross_fitted_confidence_never_uses_a_folds_own_pairs():
    # Fold 0 has 40 large-gap pairs that are all wrong. Fold 1 has 40 large-gap pairs that are all right.
    wrong = QueryResult("a", 0, 0, 0, 0, 40, None, None, [PairMargin(5.0, False) for _ in range(40)])
    right = QueryResult("b", 1, 0, 40, 0, 40, None, None, [PairMargin(5.0, True) for _ in range(40)])
    check = cross_fitted_confidence([wrong, right], {"a": 0, "b": 1}, 2)
    # Fold 0 borrows fold 1's threshold (fold 1 is accurate) and is then scored on its own wrong pairs.
    # Fold 1 borrows fold 0's pairs, which never reach 0.65, so fold 1 gets no threshold.
    assert check.folds_with_threshold == 1
    assert check.calls == 40
    assert check.correct == 0
    assert check.accuracy == 0.0


def test_corpus_fingerprint_changes_when_any_stored_page_changes():
    base = [("https://a.example/", "<html>a</html>"), ("https://b.example/", "<html>b</html>")]
    assert corpus_fingerprint(base) == corpus_fingerprint(list(reversed(base)))
    assert corpus_fingerprint(base) != corpus_fingerprint(base + [("https://c.example/", "<html>c</html>")])
    assert corpus_fingerprint(base) != corpus_fingerprint([base[0], ("https://b.example/", "<html>B</html>")])


def test_run_benchmark_reports_every_protocol_on_identical_pairs():
    examples = []
    for q in range(5):
        for position in range(1, 5):
            examples.append(_row(f"q{q}", f"https://s{q}.example/{position}", position, 5 - position))
    clusters, results = run_benchmark(examples, bootstrap=50, scorers={"oracle": feature_scorer("signal")})
    assert [row.protocol for row in results] == ["loqo", "cluster", "cluster-url"]
    assert {row.scorers[0].n_pairs for row in results} == {5 * 6}
    assert all(row.random_band is not None for row in results)
    assert len(clusters) == 5


def test_benchmark_cli_refuses_without_two_covered_queries(tmp_path, capsys):
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\nq1,https://a.example/,1\nq1,https://b.example/,2\n")
    db = f"sqlite:///{tmp_path / 'rank.db'}"
    main(["--database", db, "benchmark", str(serp), "--bootstrap", "10", "--as-of", "2026-10-29"])
    out = capsys.readouterr().out
    assert "Benchmark not computed" in out
    assert "not_attempted 2" in out


def test_benchmark_cli_writes_json_with_provenance(tmp_path, capsys):
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'rank.db'}"
    session = open_session(db)
    lines = ["query,url,position"]
    for q in range(3):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = f"<html><head><title>dirt work software {position}</title></head><body><h1>Dirt work</h1><p>{words} {'filler ' * 200}</p></body></html>"
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}")
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text("\n".join(lines) + "\n")
    out_json = tmp_path / "bench.json"
    main(["--database", db, "benchmark", str(serp), "--bootstrap", "20", "--as-of", "2026-10-29", "--json", str(out_json)])
    out = capsys.readouterr().out
    assert "Not live Google ranks" in out
    assert "[cluster-url]" in out
    payload = json.loads(out_json.read_text())
    assert payload["as_of"] == "2026-10-29"
    assert payload["coverage"] == {"covered": 9}
    assert payload["labels_sha"] and payload["corpus_sha"]
    assert {p["protocol"] for p in payload["protocols"]} == {"loqo", "cluster", "cluster-url"}
    assert "margins" not in json.dumps(payload)


def test_clusters_come_from_the_label_file_not_crawl_coverage():
    from aeo.ranksim.groundtruth import SerpLabel

    shared = [f"https://shared.example/{index}" for index in range(4)]
    labels = []
    for query, own in (("a", "x"), ("b", "y")):
        for index, url in enumerate([f"https://{own}.example/1", f"https://{own}.example/2"] + shared):
            labels.append(SerpLabel(query, url, index + 1, None, "manual_csv"))
    # Only the non-shared pages were crawled, so covered rows alone show no overlap.
    examples = [_row(label.query, label.url, label.position, 0) for label in labels if "shared" not in label.url]
    clusters, _ = run_benchmark(examples, bootstrap=0, scorers={"f": feature_scorer("signal")}, cluster_source=labels)
    assert clusters["a"] == clusters["b"]
    alone, _ = run_benchmark(examples, bootstrap=0, scorers={"f": feature_scorer("signal")})
    assert alone["a"] != alone["b"]


def _synthetic(signal_tracks_label: bool, queries: int = 14, per_query: int = 5):
    import random as _random

    rng = _random.Random(7)
    examples = []
    for q in range(queries):
        for position in range(1, per_query + 1):
            signal = (per_query - position) * 3.0 if signal_tracks_label else rng.random()
            examples.append(
                RankExample(
                    f"query {q}",
                    f"https://q{q}p{position}.example/page",
                    float(position),
                    {"bm25_title": signal, "bm25f": signal},
                )
            )
    return examples


def test_validated_calibration_drops_a_threshold_that_fails_held_out():
    from aeo.ranksim.confidence import Calibration, validate_calibration

    in_sample = Calibration(0.05, 0.9, 0.5, "in-sample")
    checked = validate_calibration(in_sample, _synthetic(False), ["bm25_title"])
    assert checked.threshold is None
    assert checked.label(100.0) == "UNSURE"
    assert "chosen and scored on the same pairs" in checked.note
    assert "stays UNSURE" in checked.note


def test_validated_calibration_keeps_a_threshold_that_holds_held_out():
    from aeo.ranksim.confidence import loo_fit

    fit = loo_fit(_synthetic(True), ["bm25_title"])
    assert fit.calibration.threshold is not None
    assert fit.calibration.confident_pairwise >= 0.65
    assert "Held out by query cluster and URL" in fit.calibration.note
    unvalidated = loo_fit(_synthetic(True), ["bm25_title"], validate=False)
    assert unvalidated.calibration.threshold == fit.calibration.threshold


def test_ndcg_and_top3_do_not_leak_label_order_through_ties():
    from aeo.ranksim.lambdamart import _ndcg, _top3_hit

    rows = [RankExample("q", f"https://t.example/{i}", float(i + 1), {}) for i in range(5)]
    flat = [0.0] * 5
    forward = (_ndcg(rows, flat), _top3_hit(rows, flat))
    backward = (_ndcg(list(reversed(rows)), flat), _top3_hit(list(reversed(rows)), flat))
    assert forward == backward
    assert forward[0] < 1.0
    assert abs(forward[1] - 3 / 5) < 1e-9
    perfect = [5.0, 4.0, 3.0, 2.0, 1.0]
    assert _ndcg(rows, perfect) == 1.0
    assert _top3_hit(rows, perfect) == 1.0
