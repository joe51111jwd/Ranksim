"""Second forward window: Nov 13 is the previous snapshot. The frozen line is not refit."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from aeo.ranksim.full_coverage import (
    PINNED_PATHS,
    evaluate_gate,
    file_sha256,
    rank_metrics,
    url_key,
    verify_track_record,
)
from aeo.ranksim.full_coverage import repo_root as full_root
from aeo.ranksim.new_page import _percentile

ROOT = full_root()


def _write(path: Path, rows: list[tuple[str, str, int]]) -> None:
    lines = ["query,url,position"]
    lines.extend(f"{query},{url},{position}" for query, url, position in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_before_snapshot_is_appended_after_the_frozen_history(tmp_path: Path):
    from aeo.ranksim.forward_pair import history_with_before

    csv_path = tmp_path / "before.csv"
    _write(csv_path, [("hcss alternative", "https://only-before.example/a", 3)])
    history = history_with_before(ROOT, csv_path, "2026-11-13")
    assert [day for day, _rows in history] == ["2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13"]
    assert history[-1][1][0].position == 3.0
    with pytest.raises(SystemExit):
        history_with_before(ROOT, csv_path, "2026-11-11")


def test_persistence_uses_the_before_position_and_ignores_the_outcome(tmp_path: Path):
    from aeo.ranksim.forward_pair import score_transition

    survivor = "https://only-before.example/kept"
    newcomer = "https://brand-new.example/page"
    before = tmp_path / "before.csv"
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    _write(before, [("alpha tools", survivor, 4), ("alpha tools", "https://other.example/old", 9)])
    _write(left, [("alpha tools", survivor, 1), ("alpha tools", newcomer, 2)])
    _write(right, [("alpha tools", survivor, 9), ("alpha tools", newcomer, 1)])
    before_pins = {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS}
    first = score_transition(
        root=ROOT,
        before_csv=before,
        before_day="2026-11-13",
        outcome_csv=left,
        outcome_day="2026-11-16",
    )
    second = score_transition(
        root=ROOT,
        before_csv=before,
        before_day="2026-11-13",
        outcome_csv=right,
        outcome_day="2026-11-16",
    )
    assert {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS} == before_pins

    def scores(result: dict) -> dict[str, float]:
        return {row["url"]: row["scores"]["full_coverage"] for row in result["queries"]["alpha tools"]}

    assert scores(first) == scores(second)
    kept = next(row for row in first["queries"]["alpha tools"] if row["url"] == survivor)
    fresh = next(row for row in first["queries"]["alpha tools"] if row["url"] == newcomer)
    assert kept["bucket"] == "surviving"
    assert kept["scores"]["full_coverage"] == -4.0
    assert kept["scores"]["persistence_ranked_last"] == -4.0
    assert fresh["bucket"] == "new"
    assert fresh["scores"]["persistence_ranked_last"] == -1000.0
    assert fresh["domain_seen"] is False
    assert "position" not in fresh["scores"]
    assert first["gate"] == evaluate_gate(first["metrics"]["full_coverage"], first["metrics"]["persistence_ranked_last"])
    assert first["gate"]["pass"] is False
    assert first["gate"]["checks"]["new_vs_surviving_pairs_at_least_minimum"] is False
    assert first["gate"]["ranker_change"] == "none"
    counted = first["per_query"]["full_coverage"]["all"]["alpha tools"]
    block = rank_metrics(first["queries"], "full_coverage")["all"]
    assert (block["correct"], block["wrong"], block["ties"]) == counted
    assert first["pair_coverage"]["predicted_pairs"] == first["pair_coverage"]["outcome_pairs"] == block["pairs"]


def test_before_snapshot_counts_in_the_domain_prior(tmp_path: Path):
    from aeo.ranksim.forward_pair import score_transition

    before = tmp_path / "before.csv"
    outcome = tmp_path / "outcome.csv"
    _write(
        before,
        [
            ("alpha tools", "https://only-before.example/old", 1),
            ("alpha tools", "https://constructionbids.ai/blog/hcss-alternative-heavy-construction-software", 2),
        ],
    )
    _write(outcome, [("alpha tools", "https://only-before.example/new", 1), ("alpha tools", "https://never-seen.example/new", 2)])
    scored = score_transition(
        root=ROOT,
        before_csv=before,
        before_day="2026-11-13",
        outcome_csv=outcome,
        outcome_day="2026-11-16",
    )
    rows = {row["url_key"]: row for row in scored["queries"]["alpha tools"]}
    seen = rows[url_key("https://only-before.example/new")]
    unseen = rows[url_key("https://never-seen.example/new")]
    assert seen["bucket"] == "new"
    assert seen["domain_seen"] is True
    assert unseen["domain_seen"] is False
    assert seen["domain_prior"] != unseen["domain_prior"]


def test_transition_rejects_an_outcome_that_is_not_later(tmp_path: Path):
    from aeo.ranksim.forward_pair import score_transition

    csv_path = tmp_path / "serp.csv"
    _write(csv_path, [("alpha tools", "https://a.example/a", 1)])
    with pytest.raises(SystemExit):
        score_transition(
            root=ROOT,
            before_csv=csv_path,
            before_day="2026-11-16",
            outcome_csv=csv_path,
            outcome_day="2026-11-16",
        )


def test_pooled_bootstrap_keeps_both_windows_of_a_query_together():
    from aeo.ranksim.forward_pair import FORWARD_BOOTSTRAP_DRAWS, FORWARD_BOOTSTRAP_SEED, pooled_query_bootstrap

    windows = [
        {"candidate": {"q": (9, 1, 0)}, "baseline": {"q": (5, 5, 0)}},
        {"candidate": {"q": (0, 1, 0)}, "baseline": {"q": (1, 0, 0)}},
    ]
    pooled = pooled_query_bootstrap(windows, draws=50, seed=7)
    assert pooled["queries"] == 1
    assert pooled["unit"] == "query"
    assert (pooled["combined_correct"], pooled["combined_pairs"]) == (9, 11)
    assert (pooled["baseline_correct"], pooled["baseline_pairs"]) == (6, 11)
    assert pooled["combined_pairwise"] == pytest.approx(9 / 11)
    assert pooled["margin"] == pytest.approx(9 / 11 - 6 / 11)
    assert pooled["combined_ci"] == [pytest.approx(9 / 11), pytest.approx(9 / 11)]
    assert pooled["margin_ci"] == [pytest.approx(9 / 11 - 6 / 11), pytest.approx(9 / 11 - 6 / 11)]
    assert FORWARD_BOOTSTRAP_DRAWS == 2000
    assert FORWARD_BOOTSTRAP_SEED == 20261116
    again = pooled_query_bootstrap(windows, draws=50, seed=7)
    assert again == pooled


def test_pooled_bootstrap_resamples_queries_and_matches_the_percentile():
    from aeo.ranksim.forward_pair import pooled_query_bootstrap

    windows = [
        {
            "candidate": {"a": (8, 0, 0), "b": (1, 5, 0), "c": (6, 2, 1), "d": (0, 4, 0)},
            "baseline": {"a": (4, 4, 0), "b": (3, 3, 0), "c": (2, 6, 1), "d": (4, 0, 0)},
        }
    ]
    pooled = pooled_query_bootstrap(windows, draws=40, seed=11)
    assert pooled["queries"] == 4
    assert (pooled["combined_correct"], pooled["combined_wrong"], pooled["combined_ties"], pooled["combined_pairs"]) == (15, 11, 1, 27)
    assert (pooled["baseline_correct"], pooled["baseline_pairs"]) == (13, 27)
    assert pooled["combined_ci"][0] <= pooled["combined_ci"][1]
    assert pooled["margin_ci"][0] <= pooled["margin_ci"][1]
    assert 0.0 <= pooled["combined_ci"][0] <= 1.0
    assert 0.0 <= pooled["combined_ci"][1] <= 1.0

    tables = {
        "a": ((8, 0, 0), (4, 4, 0)),
        "b": ((1, 5, 0), (3, 3, 0)),
        "c": ((6, 2, 1), (2, 6, 1)),
        "d": ((0, 4, 0), (4, 0, 0)),
    }
    names = ["a", "b", "c", "d"]
    rng = random.Random(11)
    combined_samples = []
    margin_samples = []
    for _ in range(40):
        picked = [names[rng.randrange(len(names))] for _ in names]
        left = [0, 0, 0]
        right = [0, 0, 0]
        for name in picked:
            for slot, triple in enumerate(tables[name]):
                target = left if slot == 0 else right
                for index, value in enumerate(triple):
                    target[index] += value
        left_total = sum(left)
        right_total = sum(right)
        combined_samples.append(left[0] / left_total)
        margin_samples.append(left[0] / left_total - right[0] / right_total)
    combined_samples.sort()
    margin_samples.sort()
    assert pooled["combined_ci"][0] == pytest.approx(_percentile(combined_samples, 0.025))
    assert pooled["combined_ci"][1] == pytest.approx(_percentile(combined_samples, 0.975))
    assert pooled["margin_ci"][0] == pytest.approx(_percentile(margin_samples, 0.025))
    assert pooled["margin_ci"][1] == pytest.approx(_percentile(margin_samples, 0.975))
    other = pooled_query_bootstrap(windows, draws=40, seed=12)
    assert other["combined_ci"] != pooled["combined_ci"]


def test_ledger_appends_predicted_and_real_once(tmp_path: Path):
    from aeo.ranksim.forward_pair import append_transition, score_transition

    before = tmp_path / "before.csv"
    outcome = tmp_path / "outcome.csv"
    _write(before, [("alpha tools", "https://only-before.example/kept", 4)])
    _write(outcome, [("alpha tools", "https://only-before.example/kept", 2), ("alpha tools", "https://brand-new.example/page", 1)])
    scored = score_transition(
        root=ROOT,
        before_csv=before,
        before_day="2026-11-13",
        outcome_csv=outcome,
        outcome_day="2026-11-16",
    )
    ledger = tmp_path / "track-record.jsonl"
    before_pins = {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS}
    append_transition(ledger, scored, created_at="2026-11-16T18:00:00+00:00")
    assert {rel: file_sha256(ROOT / rel) for rel in PINNED_PATHS} == before_pins
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["label"] for row in rows] == ["PREDICTED", "PREDICTED", "REAL", "PREDICTED", "REAL", "SCORE"]
    assert rows[0]["kind"] == "run"
    score_rows = [row for row in rows if row["kind"] == "score"]
    real_rows = [row for row in rows if row["kind"] == "outcome"]
    assert all(row["label"] == "PREDICTED" and "position" not in row for row in score_rows)
    assert all(row["label"] == "REAL" and "position" in row for row in real_rows)
    assert rows[-1]["label"] == "SCORE"
    assert rows[-1]["gate"]["ranker_change"] == "none"
    assert verify_track_record(ledger) == 6
    with pytest.raises(SystemExit):
        append_transition(ledger, scored, created_at="2026-11-16T18:00:01+00:00")
    assert verify_track_record(ledger) == 6


def test_frozen_window_stops_at_the_nov11_snapshot(tmp_path: Path):
    from aeo.ranksim.forward_pair import score_frozen_window

    csv_path = tmp_path / "outcome.csv"
    _write(csv_path, [("alpha tools", "https://brand-new.example/page", 1), ("alpha tools", "https://other.example/page", 2)])
    scored = score_frozen_window(root=ROOT, outcome_csv=csv_path, outcome_day="2026-11-13")
    assert scored["history_days"] == ["2026-11-06", "2026-11-09", "2026-11-11"]
    assert scored["before_day"] == "2026-11-11"
    assert all(row["bucket"] == "new" for row in scored["queries"]["alpha tools"])


def test_report_labels_predicted_scores_and_real_positions():
    from aeo.ranksim.forward_pair import render_forward_report

    text = render_forward_report(
        {
            "before_day": "2026-11-13",
            "outcome_day": "2026-11-16",
            "gate": {
                "pass": False,
                "checks": {
                    "beats_persistence_ranked_last_on_all_pairs": False,
                    "beats_chance_on_new_vs_surviving": False,
                    "new_vs_surviving_pairs_at_least_minimum": False,
                },
                "ranker_change": "none",
                "new_vs_surviving_pairs": 1,
            },
            "metrics": {
                "full_coverage": {
                    "all": {"correct": 1, "wrong": 0, "ties": 0, "pairs": 1, "pairwise": 1.0, "queries": 1},
                    "surviving_surviving": {"correct": 0, "wrong": 0, "ties": 0, "pairs": 0, "pairwise": None, "queries": 0},
                    "new_vs_surviving": {"correct": 1, "wrong": 0, "ties": 0, "pairs": 1, "pairwise": 1.0, "queries": 1},
                    "new_vs_new": {"correct": 0, "wrong": 0, "ties": 0, "pairs": 0, "pairwise": None, "queries": 0},
                    "ndcg_at_10": 1.0,
                    "mean_spearman": 1.0,
                    "ndcg_queries": 1,
                    "spearman_queries": 1,
                },
                "persistence_ranked_last": {
                    "all": {"correct": 0, "wrong": 1, "ties": 0, "pairs": 1, "pairwise": 0.0, "queries": 1},
                    "surviving_surviving": {"correct": 0, "wrong": 0, "ties": 0, "pairs": 0, "pairwise": None, "queries": 0},
                    "new_vs_surviving": {"correct": 0, "wrong": 1, "ties": 0, "pairs": 1, "pairwise": 0.0, "queries": 1},
                    "new_vs_new": {"correct": 0, "wrong": 0, "ties": 0, "pairs": 0, "pairwise": None, "queries": 0},
                    "ndcg_at_10": 0.5,
                    "mean_spearman": None,
                    "ndcg_queries": 1,
                    "spearman_queries": 0,
                },
            },
            "coverage": {"outcome_rows": 2, "scored_rows": 2, "surviving_rows": 1, "new_rows": 1, "html_rows": 0, "queries": 1},
            "pair_coverage": {"outcome_pairs": 1, "predicted_pairs": 1, "share": 1.0},
            "pooled": {
                "queries": 2,
                "draws": 2000,
                "seed": 20261116,
                "combined_correct": 4,
                "combined_pairs": 7,
                "combined_pairwise": 4 / 7,
                "baseline_correct": 3,
                "baseline_pairs": 7,
                "baseline_pairwise": 3 / 7,
                "margin": 1 / 7,
                "combined_ci": [0.2, 0.8],
                "margin_ci": [-0.1, 0.4],
                "unit": "query",
            },
            "domain_context": [
                {"query": "hcss alternative", "position": 4, "url": "https://www.smartcrewhq.com/compare/hcss-alternative/"}
            ],
            "domain_context_host": "smartcrewhq.com",
            "domain_context_count": 1,
        }
    )
    assert "PREDICTED" in text
    assert "REAL" in text
    assert "Gate pass: false" in text
    assert "1/1" in text
    assert "4/7" in text
    assert "smartcrewhq.com" in text
    assert "query" in text


def _triples(mapping: dict) -> dict[str, tuple[int, int, int]]:
    return {query: (int(triple[0]), int(triple[1]), int(triple[2])) for query, triple in mapping.items()}


def test_published_nov16_track_record_matches_the_frozen_counts():
    from aeo.ranksim.forward_pair import pooled_query_bootstrap

    report = json.loads((ROOT / "evals/forward-2026-11-16/report.json").read_text(encoding="utf-8"))
    markdown = (ROOT / "evals/forward-2026-11-16/report.md").read_text(encoding="utf-8")
    combined = report["metrics"]["full_coverage"]
    persistence = report["metrics"]["persistence_ranked_last"]
    assert (combined["all"]["correct"], combined["all"]["wrong"], combined["all"]["ties"], combined["all"]["pairs"]) == (1016, 322, 3, 1341)
    assert (persistence["all"]["correct"], persistence["all"]["wrong"], persistence["all"]["ties"], persistence["all"]["pairs"]) == (856, 243, 242, 1341)
    assert (combined["new_vs_surviving"]["correct"], combined["new_vs_surviving"]["pairs"]) == (282, 415)
    assert (combined["new_vs_new"]["correct"], combined["new_vs_new"]["wrong"], combined["new_vs_new"]["ties"], combined["new_vs_new"]["pairs"]) == (139, 100, 3, 242)
    assert (combined["surviving_surviving"]["correct"], combined["surviving_surviving"]["pairs"]) == (595, 684)
    assert combined["ndcg_at_10"] == pytest.approx(0.852882)
    assert combined["mean_spearman"] == pytest.approx(0.627421)
    assert persistence["ndcg_at_10"] == pytest.approx(0.837650)
    assert persistence["mean_spearman"] == pytest.approx(0.544684)
    assert persistence["spearman_queries"] == 29
    assert combined["spearman_queries"] == 30
    assert report["coverage"]["scored_rows"] == report["coverage"]["outcome_rows"] == 299
    assert report["pair_coverage"] == {"outcome_pairs": 1341, "predicted_pairs": 1341, "share": 1.0}
    assert report["gate"]["pass"] is True
    assert report["gate"]["ranker_change"] == "none"
    assert report["gate"]["checks"] == {
        "beats_persistence_ranked_last_on_all_pairs": True,
        "beats_chance_on_new_vs_surviving": True,
        "new_vs_surviving_pairs_at_least_minimum": True,
    }
    windows = [
        {
            "candidate": _triples(report["prior_per_query"]["full_coverage"]["all"]),
            "baseline": _triples(report["prior_per_query"]["persistence_ranked_last"]["all"]),
        },
        {
            "candidate": _triples(report["per_query"]["full_coverage"]["all"]),
            "baseline": _triples(report["per_query"]["persistence_ranked_last"]["all"]),
        },
    ]
    pooled = pooled_query_bootstrap(windows)
    assert (pooled["combined_correct"], pooled["combined_pairs"]) == (2008, 2673)
    assert (pooled["baseline_correct"], pooled["baseline_pairs"]) == (1718, 2673)
    assert pooled["combined_pairwise"] == pytest.approx(report["pooled"]["combined_pairwise"])
    assert pooled["margin"] == pytest.approx(report["pooled"]["margin"])
    assert pooled["combined_ci"] == pytest.approx(report["pooled"]["combined_ci"])
    assert pooled["margin_ci"] == pytest.approx(report["pooled"]["margin_ci"])
    assert pooled["draws"] == 2000
    assert pooled["seed"] == 20261116
    assert pooled["queries"] == 30
    assert report["window_totals"][0]["combined_correct"] == 992
    assert report["window_totals"][0]["combined_pairs"] == 1332
    assert report["window_totals"][1]["combined_correct"] == 1016
    named = {row["query"]: row["position"] for row in report["domain_context"] if row["query"] in {"all in one heavy civil software", "all in one dirt work software"}}
    assert named == {"all in one heavy civil software": 4, "all in one dirt work software": 6}
    hcss = sorted(row["position"] for row in report["domain_context"] if row["query"] == "hcss alternative")
    assert hcss == [4, 8]
    assert report["domain_context_count"] == 29
    assert "1016/1341" in markdown
    assert "2008/2673" in markdown
    assert "Gate pass: true" in markdown
    assert "PREDICTED" in markdown and "REAL" in markdown
    ledger = ROOT / "evals/preregistered/next-snapshot/track-record.jsonl"
    assert verify_track_record(ledger) == report["ledger"]["rows"] == 1199
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["row_hash"] == report["ledger"]["head"]
    assert rows[-1]["kind"] == "pooled"
    assert rows[-1]["label"] == "SCORE"
    assert rows[-2]["kind"] == "metrics"
    assert rows[-2]["metrics"]["full_coverage"]["all"]["correct"] == 1016
    snapshot = rows[-2]["snapshot_sha256"]
    scores = [row for row in rows if row.get("snapshot_sha256") == snapshot and row.get("kind") == "score"]
    outcomes = [row for row in rows if row.get("snapshot_sha256") == snapshot and row.get("kind") == "outcome"]
    assert len(scores) == len(outcomes) == 299
    assert all(row["label"] == "PREDICTED" and "position" not in row for row in scores)
    assert all(row["label"] == "REAL" and "position" in row for row in outcomes)
