"""Frozen 2026-11-11 predictions. The outcome is not an input to the freeze."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.forward_freeze import (
    ADOPTION_MARGIN,
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    EXPECTED_HYPERPARAMETERS,
    WINDOW_NOV6,
    WINDOW_NOV9,
    _decision,
    freeze_predictions,
    render_preregistration,
    score_predictions,
    write_freeze_dir,
    write_score_dir,
)
from aeo.ranksim.lambdamart import RankExample, fit_lambdamart

ROOT = Path(__file__).resolve().parents[1]
NOV9_LEDGER = ROOT / "evals" / "forward-2026-11-09" / "ledger.json"
NOV9_LEDGER_SHA256 = "c38d0d8505e50835438046aaa066abcb2070e3ec244b9ed4cdacd008157c0f05"


def _page(title: str) -> str:
    return (
        "<!doctype html><html><head><title>"
        + title
        + "</title></head><body><h1>"
        + title
        + "</h1><p>"
        + (title + " ") * 40
        + "for a field crew. Scheduling, job costing, and estimates.</p></body></html>"
    )


def _write_snapshot(root: Path, day: str, rows: list[tuple[str, int, str]], html: dict[str, str]) -> None:
    root.mkdir(parents=True)
    (root / "html").mkdir()
    with (root / "serp_serper_live.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query", "position", "link", "request_start", "request_end"])
        for query, position, url in rows:
            writer.writerow([query, position, url, f"{day}T12:00:00-05:00", f"{day}T12:01:00-05:00"])
    with (root / "html_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["url", "file"])
        for url, body in html.items():
            name = "html/" + hashlib.sha256(url.encode()).hexdigest()[:12] + ".html"
            (root / name).write_text(body, encoding="utf-8")
            writer.writerow([url, name])


def _pair(tmp_path: Path):
    nov6 = tmp_path / "nov6"
    nov9 = tmp_path / "nov9"
    _write_snapshot(
        nov6,
        "2026-11-06",
        [
            ("alpha tools", 1, "https://a.example/old"),
            ("alpha tools", 2, "https://b.example/old"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {
            "https://a.example/old": _page("alpha tools guide"),
            "https://b.example/old": _page("alpha tools notes"),
            "https://d.example/one": _page("beta widgets guide"),
            "https://e.example/two": _page("beta widgets notes"),
        },
    )
    _write_snapshot(
        nov9,
        "2026-11-09",
        [
            ("alpha tools", 1, "https://a.example/old"),
            ("alpha tools", 2, "https://c.example/new"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {
            "https://c.example/new": _page("alpha tools newcomer"),
        },
    )
    return nov6, nov9


def test_freeze_keeps_each_day_in_its_own_training_set(tmp_path: Path):
    nov6, nov9 = _pair(tmp_path)
    payload = freeze_predictions(nov6, nov9)
    assert payload["label"] == "PREDICTED"
    assert payload["outcome_opened"] is False
    assert payload["ranker_change"] == "none"
    assert payload["hyperparameters"]["num_leaves"] == 15
    assert payload["hyperparameters"]["learning_rate"] == 0.08
    assert payload["hyperparameters"]["num_boost_round"] == 40
    nov6_window = payload["windows"][WINDOW_NOV6]
    nov9_window = payload["windows"][WINDOW_NOV9]
    assert "https://c.example/new" not in nov6_window["training_urls"]["2026-11-06"]
    assert "https://c.example/new" in nov9_window["training_urls"]["2026-11-09"]
    assert set(nov6_window["training_urls"]) == {"2026-11-06"}
    later_only = [row for row in nov6_window["rows"] if row["url"] == "https://c.example/new"]
    assert later_only == []
    kept = [row for row in nov6_window["rows"] if row["url"] == "https://a.example/old"]
    assert kept and kept[0]["html"] is True
    newcomer = [row for row in nov9_window["rows"] if row["url"] == "https://c.example/new"]
    assert newcomer and newcomer[0]["html_source"] == "2026-11-09"
    for window in (nov6_window, nov9_window):
        by_query: dict[str, list] = {}
        for row in window["rows"]:
            by_query.setdefault(row["query"], []).append(row)
            assert row["scores"]["persistence"] == -row["candidate_position"]
            assert "outcome_position" not in row
        for group in by_query.values():
            for scorer in ("lambdamart", "bm25", "persistence", "blend", "history_aware"):
                ranks = sorted(row["ranks"][scorer] for row in group)
                assert ranks == list(range(1, len(group) + 1))
    text = render_preregistration(payload)
    assert "PREDICTED" in text
    assert "0.05" in text
    assert "unpredicted" in text
    assert str(ADOPTION_MARGIN) in text or "0.05" in text
    assert str(BOOTSTRAP_SEED) in text
    assert str(BOOTSTRAP_DRAWS) in text


def test_score_counts_new_outcome_urls_as_unpredicted(tmp_path: Path, monkeypatch):
    nov6, nov9 = _pair(tmp_path)
    out = tmp_path / "freeze"
    write_freeze_dir(nov6, nov9, out)
    outcome = tmp_path / "outcome"
    _write_snapshot(
        outcome,
        "2026-11-11",
        [
            ("alpha tools", 1, "https://b.example/old"),
            ("alpha tools", 2, "https://a.example/old"),
            ("alpha tools", 3, "https://z.example/entrant"),
            ("beta widgets", 1, "https://e.example/two"),
            ("beta widgets", 2, "https://d.example/one"),
        ],
        {},
    )

    def _boom(*_args, **_kwargs):
        raise AssertionError("scoring must not fit LambdaMART")

    monkeypatch.setattr("aeo.ranksim.forward_freeze.fit_lambdamart", _boom)
    payload = json.loads((out / "predictions.json").read_text(encoding="utf-8"))
    report = score_predictions(payload, outcome / "serp_serper_live.csv")
    nov6_report = report["windows"][WINDOW_NOV6]
    assert nov6_report["coverage"]["unpredicted_outcome_rows"] == 1
    assert nov6_report["coverage"]["candidate_rows_in_outcome"] == 4
    assert nov6_report["coverage"]["scored_pairs"] == 2
    assert nov6_report["coverage"]["outcome_pairs"] == 4
    assert nov6_report["churn_baseline"]["rows_reappeared"] == 4
    assert nov6_report["churn_baseline"]["agree"] == 0
    assert nov6_report["churn_baseline"]["pairs"] == 2
    assert nov6_report["scorers"]["persistence"]["pairwise"]["pairs"] == 2
    assert nov6_report["scorers"]["persistence"]["pairwise"]["correct"] == 0
    assert nov6_report["ranker_change"] == "none"
    assert report["label"] == "PREDICTED"
    ledger_before = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    hashes = [row["row_hash"] for row in ledger_before["predictions"]]
    write_score_dir(out / "predictions.json", outcome, out / "ledger.json", out / "report.json")
    ledger_after = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    assert [row["row_hash"] for row in ledger_after["predictions"]] == hashes
    assert ledger_after["outcomes"]
    assert all(row["observed_on"] == "2026-11-11" for row in ledger_after["outcomes"])
    assert all(row["depth"] is None for row in ledger_after["outcomes"])
    scored = ledger_after["scores"]
    slug = f"forward-2026-11-11-{WINDOW_NOV6}-persistence"
    assert scored[slug]["resolved"] == 2
    assert scored[slug]["correct"] == 0
    assert scored[slug]["no_outcome_yet"] == 0


def test_promotion_bar_is_the_pre_registered_margin():
    def block(correct: int, wrong: int) -> dict[str, tuple[int, int, int]]:
        return {f"q{index}": (correct, wrong, 0) for index in range(8)}

    per_query = {
        "persistence": block(5, 5),
        "bm25": block(5, 5),
        "lambdamart": block(10, 0),
        "blend": block(5, 5),
        "history_aware": block(10, 0),
    }
    decision = _decision(per_query)
    assert decision["ranker_change"] == "none"
    assert decision["scorers"]["lambdamart"]["clears_bar"] is True
    assert decision["scorers"]["lambdamart"]["applied"] is False
    assert decision["scorers"]["bm25"]["clears_bar"] is False
    assert decision["scorers"]["blend"]["clears_bar"] is False
    assert decision["scorers"]["history_aware"]["eligible"] is False
    assert decision["scorers"]["history_aware"]["clears_bar"] is False
    assert decision["any_scorer_clears_bar"] is True


def test_fit_lambdamart_hyperparameters_match_the_freeze():
    examples = []
    for query, url, position, bm25 in (
        ("alpha", "https://a.example", 1, 2.0),
        ("alpha", "https://b.example", 2, 1.0),
        ("beta", "https://c.example", 1, 2.0),
        ("beta", "https://d.example", 2, 0.5),
    ):
        features = {name: 0.0 for name in FEATURE_NAMES}
        features["bm25f"] = bm25
        examples.append(RankExample(query, url, position, features))
    model = fit_lambdamart(examples)
    assert int(model.params["num_iterations"]) == EXPECTED_HYPERPARAMETERS["num_boost_round"]
    assert float(model.params["learning_rate"]) == EXPECTED_HYPERPARAMETERS["learning_rate"]
    assert int(model.params["num_leaves"]) == EXPECTED_HYPERPARAMETERS["num_leaves"]


def test_nov9_ledger_file_is_not_rewritten():
    digest = hashlib.sha256(NOV9_LEDGER.read_bytes()).hexdigest()
    assert digest == NOV9_LEDGER_SHA256


def test_published_freeze_is_still_closed():
    path = ROOT / "evals" / "forward-2026-11-11" / "predictions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["label"] == "PREDICTED"
    assert payload["outcome_opened"] is False
    assert payload["ranker_change"] == "none"
    nov9_rows = payload["windows"][WINDOW_NOV9]["rows"]
    nov6_rows = payload["windows"][WINDOW_NOV6]["rows"]
    assert len(nov9_rows) == 299
    assert len(nov6_rows) == 294
    for row in nov9_rows + nov6_rows:
        assert "outcome_position" not in row
        assert row["scores"]["persistence"] == -row["candidate_position"]
    text = (ROOT / "evals" / "forward-2026-11-11" / "preregistration.md").read_text(encoding="utf-8")
    assert "PREDICTED" in text and "0.05" in text
