"""Full-coverage new-URL line. Outcome positions are labels, not features."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aeo.ranksim.forward_eval import ENTRANT_SCORE
from aeo.ranksim.full_coverage import (
    BACKTEST_JSON_REL,
    BACKTEST_MD_REL,
    CHANCE_PAIRWISE,
    ENTRANT_SCORE as FULL_ENTRANT_SCORE,
    GATE_TEXT,
    HISTORY,
    MANIFEST_REL,
    MIN_NEW_VS_SURVIVING_PAIRS,
    PARAMS_REL,
    PINNED_PATHS,
    PREREG_REL,
    FittedParams,
    PositionRow,
    append_track_record,
    build_backtest,
    evaluate_gate,
    file_sha256,
    fit_new_url_params,
    fit_ols,
    load_html_dir,
    load_params,
    load_position_csv,
    rank_metrics,
    render_backtest,
    score_command,
    score_outcome,
    site_key,
    verify_track_record,
)

PINNED_SHA256 = {
    "src/aeo/ranksim/full_coverage.py": "78ea55c93de65a364064f191ad9071caea7e8139f6a686ee89b422001af95f88",
    "src/aeo/ranksim/bm25f.py": "8ec9d4219b4ab8f360aa6b849d1e04b2b9d701367c738ad041c6975db2d42606",
    "src/aeo/ranksim/metrics.py": "9b10e100332652bb12c962f8da9cb9d990c853586e35e7d5e8ce9a66274c3a2e",
    "src/aeo/ranksim/snapshot_protocol.py": "4b809a41a41bd9c80157ce4a5d920b6673f7e17cdb47d209123299ffde96236d",
    "src/aeo/crawl/extract.py": "759b58ce5d2f438c89bf9c8e8b0f5f1f258386756a29636dacbfa29ca59c4534",
    "src/aeo/crawl/urls.py": "0d715e4b2b88bf82f4518e296265b51bfb7478c2ce16c9beb37b999615bf42d0",
    "src/aeo/textutil.py": "1d8503f9d2e909253d975bbbbf437061d1b5fd302a14be5ab91fcf619b368a00",
    "scripts/score_next_snapshot.py": "e0c5adf46029e5e231d9a5ebe44025605fb5905a59f393f544b9a1d77382564d",
    "evals/preregistered/next-snapshot/params.json": "caa475734e4dafdd3b929a66ca99f6f5283d29109876be31a016a49f354b0e13",
    "evals/preregistered/next-snapshot/preregistration.md": "a8aa13b210ef5433d380ac1e23b0976b70a50063ea295a392d0036531f5df484",
}
from aeo.ranksim.lambdamart import relevance
from aeo.ranksim.snapshot_protocol import pair_counts

ROOT = Path(__file__).resolve().parents[1]


def _row(query: str, url: str, position: float, day: str = "2026-11-06") -> PositionRow:
    from aeo.ranksim.full_coverage import url_key

    return PositionRow(query=query, url=url, position=position, url_key=url_key(url), day=day)


def _params(**overrides: float) -> FittedParams:
    values = {
        "intercept": 8.0,
        "coef_domain_prior": -0.5,
        "coef_url_overlap": -1.0,
        "coef_content_missing": 0.0,
        "bm25f_z_weight": 1.0,
        "tranco_z_weight": 0.0,
        "majestic_z_weight": 0.0,
        "survivor_history_weight": 0.0,
    }
    values.update(overrides)
    return FittedParams(**values)


def test_ols_recovers_a_known_line():
    features = []
    targets = []
    for x_value in range(5):
        for y_value in range(4):
            features.append([1.0, float(x_value), float(y_value)])
            targets.append(3.0 - x_value + 4.0 * y_value)
    solved = fit_ols(features, targets)
    assert solved is not None
    assert solved == pytest.approx([3.0, -1.0, 4.0])


def test_site_key_folds_www_subdomains_and_keeps_co_uk():
    assert site_key("https://www.hcss.com/x") == "hcss.com"
    assert site_key("https://learn.g2.com/a") == "g2.com"
    assert site_key("https://shop.example.co.uk/a") == "example.co.uk"


def test_relevance_matches_the_existing_gain_on_collected_positions():
    for position in range(1, 12):
        assert relevance(position) == (0.0 if position >= 11 else float(11 - position))


def test_fit_uses_new_url_labels_and_ignores_survivor_labels():
    history = [
        _row("alpha tools", "https://good.example/old", 1),
        _row("alpha tools", "https://bad.example/old", 10),
        _row("beta tools", "https://good.example/other", 1),
        _row("beta tools", "https://bad.example/other", 10),
    ]
    outcome = [
        _row("alpha tools", "https://good.example/old", 1, "2026-11-09"),
        _row("alpha tools", "https://good.example/new", 2, "2026-11-09"),
        _row("beta tools", "https://bad.example/other", 9, "2026-11-09"),
        _row("beta tools", "https://bad.example/new", 8, "2026-11-09"),
        _row("gamma tools", "https://good.example/fresh", 1, "2026-11-09"),
        _row("gamma tools", "https://bad.example/fresh", 10, "2026-11-09"),
    ]
    moved = [
        _row("alpha tools", "https://good.example/old", 10, "2026-11-09"),
        _row("alpha tools", "https://good.example/new", 2, "2026-11-09"),
        _row("beta tools", "https://bad.example/other", 1, "2026-11-09"),
        _row("beta tools", "https://bad.example/new", 8, "2026-11-09"),
        _row("gamma tools", "https://good.example/fresh", 1, "2026-11-09"),
        _row("gamma tools", "https://bad.example/fresh", 10, "2026-11-09"),
    ]
    first, _diag = fit_new_url_params(history, outcome)
    second, _diag = fit_new_url_params(history, moved)
    assert first == second
    relabeled = [
        _row("alpha tools", "https://good.example/old", 1, "2026-11-09"),
        _row("alpha tools", "https://good.example/new", 9, "2026-11-09"),
        _row("beta tools", "https://bad.example/old", 9, "2026-11-09"),
        _row("beta tools", "https://bad.example/new", 2, "2026-11-09"),
        _row("gamma tools", "https://good.example/fresh", 10, "2026-11-09"),
        _row("gamma tools", "https://bad.example/fresh", 1, "2026-11-09"),
    ]
    third, _diag = fit_new_url_params(history, relabeled)
    assert third != first


def test_outcome_position_is_not_a_feature():
    snapshots = [("2026-11-06", [_row("alpha tools", "https://old.example/a", 1)])]
    low = [
        _row("alpha tools", "https://old.example/a", 1, "2026-11-09"),
        _row("alpha tools", "https://new.example/b", 10, "2026-11-09"),
    ]
    high = [
        _row("alpha tools", "https://old.example/a", 10, "2026-11-09"),
        _row("alpha tools", "https://new.example/b", 1, "2026-11-09"),
    ]
    params = _params()
    left = score_outcome(snapshots, low, params)
    right = score_outcome(snapshots, high, params)
    left_row = left["alpha tools"]
    right_row = right["alpha tools"]
    left_scores = {row["url_key"]: row["scores"] for row in left_row}
    right_scores = {row["url_key"]: row["scores"] for row in right_row}
    assert left_scores == right_scores


def test_survivor_score_is_latest_position_unless_the_weight_is_set():
    snapshots = [
        ("2026-11-06", [_row("alpha tools", "https://old.example/a", 5, "2026-11-06")]),
        ("2026-11-09", [_row("alpha tools", "https://old.example/a", 1, "2026-11-09")]),
    ]
    outcome = [_row("alpha tools", "https://old.example/a", 4, "2026-11-11")]
    latest = score_outcome(snapshots, outcome, _params(survivor_history_weight=0.0))
    blended = score_outcome(snapshots, outcome, _params(survivor_history_weight=0.5))
    older = score_outcome(snapshots, outcome, _params(survivor_history_weight=1.0))
    assert latest["alpha tools"][0]["scores"]["full_coverage"] == -1.0
    assert blended["alpha tools"][0]["scores"]["full_coverage"] == pytest.approx(-3.0)
    assert older["alpha tools"][0]["scores"]["full_coverage"] == -5.0


def test_new_url_with_html_outranks_a_weaker_page_and_a_missing_page_is_not_floored():
    snapshots = [("2026-11-06", [_row("alpha tools", "https://old.example/a", 5)])]
    outcome = [
        _row("alpha tools", "https://old.example/a", 1, "2026-11-09"),
        _row("alpha tools", "https://new.example/match", 2, "2026-11-09"),
        _row("alpha tools", "https://new.example/miss", 3, "2026-11-09"),
    ]
    html = {
        "https://new.example/match": "<html><head><title>alpha tools</title></head><body><h1>alpha tools</h1><p>"
        + ("alpha tools " * 40)
        + "</p></body></html>",
        "https://new.example/miss": "<html><head><title>gardening</title></head><body><p>"
        + ("watering plants " * 40)
        + "</p></body></html>",
    }
    from aeo.ranksim.full_coverage import url_key

    keyed = {url_key(url): text for url, text in html.items()}
    scored = score_outcome(snapshots, outcome, _params(), html_by_url=keyed)
    rows = {row["url_key"]: row for row in scored["alpha tools"]}
    match = rows[url_key("https://new.example/match")]
    miss = rows[url_key("https://new.example/miss")]
    assert match["content_missing"] == 0
    assert miss["content_missing"] == 0
    assert match["scores"]["full_coverage"] > miss["scores"]["full_coverage"]
    no_html = score_outcome(snapshots, outcome, _params())
    bare = {row["url_key"]: row for row in no_html["alpha tools"]}
    assert bare[url_key("https://new.example/match")]["content_missing"] == 1
    assert bare[url_key("https://new.example/match")]["bm25f_z"] == 0.0


def test_tranco_weight_zero_does_not_change_the_score(tmp_path: Path):
    snapshots = [("2026-11-06", [_row("alpha tools", "https://old.example/a", 2)])]
    outcome = [
        _row("alpha tools", "https://old.example/a", 1, "2026-11-09"),
        _row("alpha tools", "https://listed.example/new", 2, "2026-11-09"),
        _row("alpha tools", "https://other.example/new", 3, "2026-11-09"),
    ]
    plain = score_outcome(snapshots, outcome, _params())
    ranked = score_outcome(snapshots, outcome, _params(), tranco={"listed.example": 5.0, "other.example": 0.1})
    left = {row["url_key"]: row["scores"]["full_coverage"] for row in plain["alpha tools"]}
    right = {row["url_key"]: row["scores"]["full_coverage"] for row in ranked["alpha tools"]}
    assert left == right
    assert any(row["tranco_popularity"] == 5.0 for row in ranked["alpha tools"])


def test_pair_buckets_and_persistence_ranked_last():
    snapshots = [("2026-11-06", [
        _row("alpha tools", "https://a.example/old", 1),
        _row("alpha tools", "https://b.example/old", 2),
    ])]
    outcome = [
        _row("alpha tools", "https://a.example/old", 1, "2026-11-09"),
        _row("alpha tools", "https://b.example/old", 2, "2026-11-09"),
        _row("alpha tools", "https://c.example/new", 3, "2026-11-09"),
    ]
    # Force the new URL between the two survivors: position_hat 1.5.
    # domain prior and overlap are real, so set coefficients to 0 and intercept 1.5.
    queries = score_outcome(snapshots, outcome, _params(intercept=1.5, coef_domain_prior=0.0, coef_url_overlap=0.0))
    metrics = rank_metrics(queries, "full_coverage")
    assert metrics["all"]["correct"] == 2
    assert metrics["all"]["wrong"] == 1
    assert metrics["all"]["pairs"] == 3
    assert metrics["surviving_surviving"] == {"queries": 1, "pairs": 1, "correct": 1, "wrong": 0, "ties": 0, "pairwise": 1.0}
    assert metrics["new_vs_surviving"]["pairs"] == 2
    assert metrics["new_vs_surviving"]["correct"] == 1
    assert metrics["new_vs_new"]["pairs"] == 0
    persistence = rank_metrics(queries, "persistence_ranked_last")
    assert persistence["all"]["correct"] == 3
    assert persistence["new_vs_surviving"]["correct"] == 2
    assert persistence["new_vs_new"]["ties"] == 0
    assert persistence["new_vs_new"]["pairs"] == 0
    positions = [row["position"] for row in queries["alpha tools"]]
    scores = [row["scores"]["persistence_ranked_last"] for row in queries["alpha tools"]]
    assert pair_counts(positions, scores) == (3, 0, 0)
    assert FULL_ENTRANT_SCORE == ENTRANT_SCORE


def test_every_outcome_url_is_scored_and_new_urls_can_pass_a_survivor():
    snapshots = [("2026-11-06", [_row("alpha tools", "https://weak.example/old", 10)])]
    outcome = [
        _row("alpha tools", "https://weak.example/old", 2, "2026-11-09"),
        _row("alpha tools", "https://strong.example/new", 1, "2026-11-09"),
    ]
    queries = score_outcome(
        snapshots,
        outcome,
        _params(intercept=0.0, coef_domain_prior=-2.0, coef_url_overlap=0.0),
    )
    rows = {row["bucket"]: row for row in queries["alpha tools"]}
    assert rows["new"]["scores"]["full_coverage"] > rows["surviving"]["scores"]["full_coverage"]
    assert rows["surviving"]["scores"]["persistence_ranked_last"] > rows["new"]["scores"]["persistence_ranked_last"]


def test_html_manifest_rejects_paths_that_escape(tmp_path: Path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    outside = tmp_path / "secret.html"
    outside.write_text("<html><title>alpha tools</title><p>alpha tools</p></html>", encoding="utf-8")
    manifest = html_dir / "html_manifest.csv"
    manifest.write_text("url,file\nhttps://new.example/a,../secret.html\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_html_dir(html_dir)


def test_gate_requires_all_three_checks():
    def block(correct: int, pairs: int) -> dict:
        return {"queries": 1, "pairs": pairs, "correct": correct, "wrong": pairs - correct, "ties": 0, "pairwise": correct / pairs}

    combined = {
        "all": block(60, 100),
        "new_vs_surviving": block(51, 100),
        "surviving_surviving": block(1, 1),
        "new_vs_new": block(1, 1),
    }
    persistence = {"all": block(50, 100), "new_vs_surviving": block(40, 100)}
    assert evaluate_gate(combined, persistence)["pass"] is True
    short = dict(combined)
    short["new_vs_surviving"] = block(9, 10)
    assert evaluate_gate(short, persistence)["pass"] is False
    tied = dict(combined)
    tied["all"] = block(50, 100)
    assert evaluate_gate(tied, persistence)["pass"] is False
    assert MIN_NEW_VS_SURVIVING_PAIRS == 100
    assert CHANCE_PAIRWISE == 0.5


def test_track_record_appends_predicted_and_real_without_rewriting(tmp_path: Path):
    ledger = tmp_path / "track-record.jsonl"
    append_track_record(ledger, [{"label": "PREDICTED", "kind": "score", "score": -1.5}])
    append_track_record(ledger, [{"label": "REAL", "kind": "outcome", "position": 2}])
    assert verify_track_record(ledger) == 2
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["label"] == "PREDICTED"
    assert rows[1]["label"] == "REAL"
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    rows[0]["score"] = 99
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verify_track_record(ledger)


def test_command_rejects_nov11_and_frozen_outputs(tmp_path: Path):
    with pytest.raises(SystemExit):
        score_command(snapshot_csv=tmp_path / "missing.csv", observed_on="2026-11-11", ledger=tmp_path / "ledger.jsonl")
    csv_path = tmp_path / "serp.csv"
    csv_path.write_text("query,position,link\nalpha tools,1,https://a.example/old\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        score_command(
            snapshot_csv=csv_path,
            observed_on="2026-11-12",
            ledger=ROOT / "evals/preregistered/next-snapshot/params.json",
        )


def test_history_files_match_the_pinned_hashes():
    for item in HISTORY:
        digest = hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest()
        assert digest == item["sha256"]


def test_preregistration_contains_the_gate_text():
    text = (ROOT / PREREG_REL).read_text(encoding="utf-8")
    assert GATE_TEXT in text
    assert "backtest-nov9-nov11.md" in text
    assert "python scripts/score_next_snapshot.py" in text


def test_frozen_files_match_the_manifest_and_the_literals():
    manifest = json.loads((ROOT / MANIFEST_REL).read_text(encoding="utf-8"))
    assert manifest["sha256"] == PINNED_SHA256
    assert set(PINNED_SHA256) == set(PINNED_PATHS)
    assert manifest["gate"] == GATE_TEXT
    for rel, digest in PINNED_SHA256.items():
        assert file_sha256(ROOT / rel) == digest


def test_params_match_a_refit_that_does_not_read_nov11():
    history = load_position_csv(ROOT / HISTORY[0]["path"], HISTORY[0]["day"])
    labels = load_position_csv(ROOT / HISTORY[1]["path"], HISTORY[1]["day"])
    refit, diagnostics = fit_new_url_params(history, labels)
    loaded = load_params(ROOT / PARAMS_REL)
    assert loaded.intercept == pytest.approx(refit.intercept)
    assert loaded.coef_domain_prior == pytest.approx(refit.coef_domain_prior)
    assert loaded.coef_url_overlap == pytest.approx(refit.coef_url_overlap)
    assert loaded.coef_content_missing == 0.0
    assert loaded.bm25f_z_weight == 1.0
    assert loaded.tranco_z_weight == 0.0
    assert loaded.majestic_z_weight == 0.0
    assert loaded.survivor_history_weight == 0.0
    document = json.loads((ROOT / PARAMS_REL).read_text(encoding="utf-8"))
    assert document["inputs"]["not_read"] == HISTORY[2]["path"]
    assert document["in_sample"]["n_new_urls"] == diagnostics["n_new_urls"] == 97
    assert diagnostics["n_queries"] == 29


def test_backtest_matches_the_committed_report_and_the_published_overlap():
    fresh = build_backtest(ROOT)
    saved = json.loads((ROOT / BACKTEST_JSON_REL).read_text(encoding="utf-8"))
    assert saved == json.loads(json.dumps(fresh, sort_keys=True))
    assert (ROOT / BACKTEST_MD_REL).read_text(encoding="utf-8") == render_backtest(fresh)
    persistence = fresh["scorers"]["persistence_ranked_last"]["surviving_surviving"]
    lambdamart = fresh["scorers"]["lambdamart_ranked_last"]["surviving_surviving"]
    bm25 = fresh["scorers"]["bm25_ranked_last"]["surviving_surviving"]
    assert (persistence["correct"], persistence["wrong"], persistence["ties"], persistence["pairs"]) == (625, 99, 0, 724)
    assert (lambdamart["correct"], lambdamart["wrong"], lambdamart["ties"], lambdamart["pairs"]) == (508, 210, 6, 724)
    assert (bm25["correct"], bm25["pairs"]) == (321, 724)
    assert fresh["coverage"]["outcome_rows"] == fresh["coverage"]["scored_rows"] == 296
    assert fresh["coverage"]["new_rows"] == 88
    assert fresh["coverage"]["surviving_rows"] == 208
    assert fresh["coverage"]["html_rows"] == 0
    assert fresh["baseline_match"] == {"frozen_surviving_unmatched": 0, "persistence_score_disagreements": 0}
    fit = fresh["fit_sample_nov6_to_nov9"]["scorers"]["persistence_ranked_last"]
    assert (fit["all"]["correct"], fit["all"]["pairs"]) == (931, 1341)
    assert (fit["surviving_surviving"]["correct"], fit["surviving_surviving"]["pairs"]) == (594, 667)
    assert fresh["label"] == "BACKTEST"
    assert fresh["gate_if_this_were_the_next_snapshot"]["ranker_change"] == "none"


def _snapshot(path: Path, first: int, second: int) -> None:
    path.write_text(
        "query,position,link\n"
        f"hcss alternative,{first},https://constructionbids.ai/blog/hcss-alternative-heavy-construction-software\n"
        f"hcss alternative,{second},https://brand-new.example/page\n",
        encoding="utf-8",
    )


def _predicted_scores(path: Path) -> dict[str, float]:
    scores = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("kind") == "score":
            scores[row["url"]] = row["score"]
    return scores


def test_score_command_appends_predicted_and_real_and_ignores_positions(tmp_path: Path):
    left_csv = tmp_path / "left.csv"
    right_csv = tmp_path / "right.csv"
    _snapshot(left_csv, 1, 10)
    _snapshot(right_csv, 10, 1)
    left_ledger = tmp_path / "left.jsonl"
    right_ledger = tmp_path / "right.jsonl"
    before = {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS}
    score_command(snapshot_csv=left_csv, observed_on="2026-11-20", ledger=left_ledger)
    score_command(snapshot_csv=right_csv, observed_on="2026-11-20", ledger=right_ledger)
    assert {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS} == before
    assert _predicted_scores(left_ledger) == _predicted_scores(right_ledger)
    rows = [json.loads(line) for line in left_ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["label"] for row in rows] == ["PREDICTED", "PREDICTED", "REAL", "PREDICTED", "REAL", "SCORE"]
    survivor = next(row for row in rows if row.get("bucket") == "surviving")
    newcomer = next(row for row in rows if row.get("bucket") == "new")
    assert survivor["score"] == -1.0
    assert newcomer["score"] != -1.0
    assert "position" not in newcomer
    assert all(row["label"] == "REAL" and "position" in row for row in rows if row["kind"] == "outcome")
    assert rows[-1]["gate"]["ranker_change"] == "none"
    assert verify_track_record(left_ledger) == 6
    with pytest.raises(SystemExit):
        score_command(
            snapshot_csv=ROOT / HISTORY[2]["path"],
            observed_on="2026-11-20",
            ledger=tmp_path / "refused.jsonl",
        )
    assert not (tmp_path / "refused.jsonl").exists()


def test_score_command_uses_html_without_touching_frozen_files(tmp_path: Path):
    csv_path = tmp_path / "serp.csv"
    _snapshot(csv_path, 1, 2)
    plain = tmp_path / "plain.jsonl"
    rich = tmp_path / "rich.jsonl"
    score_command(snapshot_csv=csv_path, observed_on="2026-11-20", ledger=plain)
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "known.html").write_text(
        "<html><head><title>hcss alternative</title></head><body><p>" + ("hcss alternative " * 40) + "</p></body></html>",
        encoding="utf-8",
    )
    (html_dir / "new.html").write_text(
        "<html><head><title>gardening</title></head><body><p>" + ("watering plants " * 40) + "</p></body></html>",
        encoding="utf-8",
    )
    (html_dir / "html_manifest.csv").write_text(
        "url,file\n"
        "https://constructionbids.ai/blog/hcss-alternative-heavy-construction-software,known.html\n"
        "https://brand-new.example/page,new.html\n",
        encoding="utf-8",
    )
    before = {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS}
    score_command(snapshot_csv=csv_path, observed_on="2026-11-20", ledger=rich, html_dir=html_dir)
    assert {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS} == before
    plain_new = next(json.loads(line) for line in plain.read_text(encoding="utf-8").splitlines() if '"bucket":"new"' in line)
    rich_new = next(json.loads(line) for line in rich.read_text(encoding="utf-8").splitlines() if '"bucket":"new"' in line)
    assert plain_new["features"]["content_missing"] == 1
    assert rich_new["features"]["content_missing"] == 0
    assert rich_new["features"]["html_sha256"]
    assert rich_new["score"] != plain_new["score"]
    rich_survivor = next(json.loads(line) for line in rich.read_text(encoding="utf-8").splitlines() if '"bucket":"surviving"' in line)
    assert rich_survivor["score"] == -1.0


def test_cli_score_next_snapshot_reaches_the_frozen_command(tmp_path: Path):
    csv_path = tmp_path / "serp.csv"
    _snapshot(csv_path, 1, 2)
    ledger = tmp_path / "ledger.jsonl"
    from aeo.ranksim.full_coverage import main

    assert (
        main(
            [
                "--snapshot-csv",
                str(csv_path),
                "--observed-on",
                "2026-11-20",
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    assert verify_track_record(ledger) == 6


def test_trailing_slash_and_host_case_count_as_the_same_survivor():
    snapshots = [("2026-11-06", [_row("alpha tools", "https://Old.example/a/", 4)])]
    outcome = [_row("alpha tools", "https://old.example/a", 1, "2026-11-09")]
    scored = score_outcome(snapshots, outcome, _params())
    row = scored["alpha tools"][0]
    assert row["bucket"] == "surviving"
    assert row["scores"]["full_coverage"] == -4.0
