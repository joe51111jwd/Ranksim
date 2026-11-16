"""Pairwise order model. Outcome positions are labels, and missing HTML stays in the denominator."""

from __future__ import annotations

from aeo.ranksim.full_coverage import rank_metrics
from aeo.ranksim.pair_order import (
    Offpage,
    PageView,
    Row,
    Snap,
    SnapRow,
    build_transition,
    dump_model,
    evaluate_scores,
    fit_experiment,
    load_model,
    selection_sort_key,
)

def _snap(day: str, triples: list[tuple[str, str, float, str]]) -> Snap:
    rows = []
    for query, url, position, title in triples:
        rows.append(
            SnapRow(query=query, url=url, position=position, title=title, url_key=url, site=url.split("/")[2])
        )
    return Snap(day=day, rows=rows)


def _row(query: str, url: str, position: float, bucket: str, **features: float) -> Row:
    base = {"has_history": 1.0 if bucket == "surviving" else 0.0, "last_relevance": 0.0}
    base.update(features)
    return Row(
        day="2026-11-09",
        query=query,
        url=url,
        url_key=url,
        position=position,
        bucket=bucket,
        group="other",
        features=base,
    )


def test_features_ignore_outcome_position():
    history = _snap(
        "2026-11-06",
        [
            ("q", "https://a.example/old", 1, "Alpha excavation"),
            ("q", "https://b.example/old", 2, "Beta excavation"),
        ],
    )
    first = _snap(
        "2026-11-09",
        [
            ("q", "https://a.example/old", 2, "Alpha excavation"),
            ("q", "https://c.example/new", 1, "Gamma excavation software"),
        ],
    )
    second = _snap(
        "2026-11-09",
        [
            ("q", "https://a.example/old", 1, "Alpha excavation"),
            ("q", "https://c.example/new", 2, "Gamma excavation software"),
        ],
    )
    pages = {
        "https://a.example/old": PageView(title="Alpha", body="excavation software " * 30, word_count=60, present=True),
        "https://c.example/new": PageView(title="Gamma", body="excavation", word_count=1, present=True),
    }
    left = build_transition([history], first, pages, Offpage())
    right = build_transition([history], second, pages, Offpage())
    left_by = {row.url: row.features for row in left}
    right_by = {row.url: row.features for row in right}
    assert left_by == right_by
    assert {row.url: row.position for row in left} != {row.url: row.position for row in right}


def test_missing_html_stays_in_the_denominator():
    history = _snap("2026-11-06", [("q", "https://a.example/old", 1, "Alpha")])
    outcome = _snap(
        "2026-11-09",
        [
            ("q", "https://a.example/old", 1, "Alpha"),
            ("q", "https://b.example/new", 2, "Beta excavation"),
            ("q", "https://c.example/new", 3, "Gamma"),
        ],
    )
    rows = build_transition([history], outcome, {}, Offpage())
    assert len(rows) == 3
    assert sum(1 for row in rows if row.features["content_present"] == 0.0) == 3
    scores = fit_experiment("persistence_ranked_last", rows).predict(rows)
    metrics = evaluate_scores(rows, scores)
    assert metrics["rows"] == 3
    assert metrics["blocks"]["all"]["pairs"] == 3
    assert metrics["scored_rows"] == 3


def test_persistence_ties_new_urls_and_orders_survivors():
    rows = [
        _row("q", "https://a.example", 1, "surviving", last_relevance=10),
        _row("q", "https://b.example", 2, "surviving", last_relevance=9),
        _row("q", "https://c.example", 3, "new", last_relevance=0),
        _row("q", "https://d.example", 4, "new", last_relevance=0),
    ]
    scores = fit_experiment("persistence_ranked_last", rows).predict(rows)
    metrics = evaluate_scores(rows, scores)
    assert metrics["blocks"]["surviving_surviving"]["correct"] == 1
    assert metrics["blocks"]["surviving_surviving"]["pairs"] == 1
    assert metrics["blocks"]["new_vs_new"]["ties"] == 1
    assert metrics["blocks"]["new_vs_new"]["correct"] == 0
    assert metrics["blocks"]["new_vs_surviving"]["correct"] == 4


def test_ridge_learns_a_perfect_feature():
    rows = []
    for day, query in (("2026-11-09", "q1"), ("2026-11-09", "q2")):
        for position, signal in ((1, 3.0), (2, 2.0), (3, 0.0)):
            rows.append(
                Row(
                    day=day,
                    query=query,
                    url=f"https://ex/{query}/{position}",
                    url_key=f"https://ex/{query}/{position}",
                    position=float(position),
                    bucket="surviving",
                    group="other",
                    features={"signal": signal, "noise": 0.2},
                )
            )
    planted = []
    for row in rows:
        planted.append(
            Row(
                day=row.day,
                query=row.query,
                url=row.url,
                url_key=row.url_key,
                position=row.position,
                bucket=row.bucket,
                group=row.group,
                features={"last_relevance": row.features["signal"], "url_overlap": row.features["noise"]},
            )
        )
    model = fit_experiment("ridge_history", planted)
    held = [
        Row(
            day="2026-11-11",
            query="q3",
            url=f"https://ex/q3/{position}",
            url_key=f"https://ex/q3/{position}",
            position=float(position),
            bucket="surviving",
            group="other",
            features={"last_relevance": signal, "url_overlap": 0.2},
        )
        for position, signal in ((1, 3.0), (2, 2.0), (3, 0.0))
    ]
    scores = model.predict(held)
    assert scores[0] > scores[1] > scores[2]


def test_model_roundtrip(tmp_path):
    rows = [
        _row("q", "https://a.example", 1, "surviving", last_relevance=10, url_overlap=0.2),
        _row("q", "https://b.example", 2, "surviving", last_relevance=4, url_overlap=0.8),
        _row("q", "https://c.example", 3, "new", last_relevance=0, url_overlap=0.1, serp_title_coverage=0.9),
    ]
    model = fit_experiment("ridge_history", rows)
    path = tmp_path / "model.json"
    dump_model(model, path, extra={"fit_windows": ["2026-11-06_to_2026-11-09"]})
    loaded = load_model(path)
    assert loaded.predict(rows) == model.predict(rows)


def test_metrics_match_the_frozen_pair_counter():
    rows = [
        _row("q", "https://a.example", 1, "surviving", last_relevance=10),
        _row("q", "https://b.example", 2, "new", last_relevance=0),
        _row("q", "https://c.example", 3, "new", last_relevance=0),
    ]
    scores = [3.0, 1.0, 1.0]
    ours = evaluate_scores(rows, scores)
    theirs = rank_metrics(
        {
            "q": [
                {"position": 1, "bucket": "surviving", "url": "https://a.example", "scores": {"m": 3.0}},
                {"position": 2, "bucket": "new", "url": "https://b.example", "scores": {"m": 1.0}},
                {"position": 3, "bucket": "new", "url": "https://c.example", "scores": {"m": 1.0}},
            ]
        },
        "m",
    )
    assert ours["blocks"]["all"]["correct"] == theirs["all"]["correct"]
    assert ours["blocks"]["all"]["pairs"] == theirs["all"]["pairs"]
    assert ours["blocks"]["all"]["ties"] == theirs["all"]["ties"]
    assert ours["blocks"]["new_vs_new"]["ties"] == theirs["new_vs_new"]["ties"]
    assert abs(ours["ndcg_at_10"] - theirs["ndcg_at_10"]) < 1e-9


def test_future_history_is_rejected():
    history = _snap("2026-11-11", [("q", "https://a.example/a", 1, "A")])
    outcome = _snap("2026-11-09", [("q", "https://a.example/a", 1, "A")])
    try:
        build_transition([history], outcome, {}, Offpage())
    except ValueError as exc:
        assert "before" in str(exc)
    else:
        raise AssertionError("future history was accepted")


def test_score_command_keeps_every_row(tmp_path):
    import subprocess
    import sys

    def write_snap(day: str, rows: list[tuple[str, str, int, str]]) -> None:
        directory = tmp_path / f"snap-{day}"
        directory.mkdir()
        lines = ["query,position,link,title"]
        for query, url, position, title in rows:
            lines.append(f"{query},{position},{url},{title}")
        (directory / "serp_serper_live.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    write_snap("2026-11-06", [("excavation software", "https://a.example/a", 1, "Alpha excavation"), ("excavation software", "https://b.example/b", 2, "Beta excavation")])
    write_snap("2026-11-09", [("excavation software", "https://a.example/a", 2, "Alpha excavation"), ("excavation software", "https://b.example/b", 1, "Beta excavation")])
    model = fit_experiment("persistence_ranked_last", [_row("excavation software", "https://a.example/a", 1, "surviving", last_relevance=10)])
    model_path = tmp_path / "model.json"
    dump_model(model, model_path)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/score_forward_pair.py",
            "--history",
            str(tmp_path / "snap-2026-11-06"),
            "--snapshot",
            str(tmp_path / "snap-2026-11-09"),
            "--model",
            str(model_path),
            "--offpage-cache",
            str(tmp_path / "missing-offpage.tsv"),
        ],
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PREDICTED" in result.stdout
    assert "Rows 2" in result.stdout
    assert "0/1" in result.stdout


def test_locked_model_keeps_survivor_order_and_lambdamart_scores():
    rows = []
    for day, query in (("2026-11-09", "q1"), ("2026-11-09", "q2")):
        rows.append(_row(query, f"https://{query}.example/a", 1, "surviving", last_relevance=10, url_overlap=0.2, domain_mean_relevance=8))
        rows.append(_row(query, f"https://{query}.example/b", 2, "surviving", last_relevance=6, url_overlap=0.4, domain_mean_relevance=5))
        rows.append(_row(query, f"https://{query}.example/c", 3, "new", last_relevance=0, url_overlap=0.9, domain_mean_relevance=9, serp_title_coverage=1))
        rows.append(_row(query, f"https://{query}.example/d", 4, "new", last_relevance=0, url_overlap=0.1, domain_mean_relevance=1, serp_title_coverage=0))
        rows.append(_row(query, f"https://{query}.example/e", 5, "new", last_relevance=0, url_overlap=0.8, domain_mean_relevance=7, serp_title_coverage=0.8))
        rows.append(_row(query, f"https://{query}.example/f", 6, "new", last_relevance=0, url_overlap=0.0, domain_mean_relevance=0, serp_title_coverage=0))
        for row in rows[-6:]:
            row.day = day
    locked = fit_experiment("persistence_locked_full", rows)
    scores = locked.predict(rows[:4])
    assert scores[0] > scores[1]
    assert scores[0] == 10
    assert scores[1] == 6
    boosted = fit_experiment("lambdamart", rows)
    boosted_scores = boosted.predict(rows[:4])
    assert all(score == score for score in boosted_scores)


def test_frozen_pair_model_matches_the_manifest():
    import hashlib
    import json

    root = __import__("pathlib").Path(__file__).resolve().parents[1] / "evals/preregistered/forward-pair-2026-11-16"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["family"] == "lambdamart_monotone"
    for name, expected in manifest["sha256"].items():
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        assert digest == expected
    model = load_model(root / "model-next.json")
    assert model.family == "lambdamart_monotone"


def test_selection_key_prefers_the_worse_window():
    spiky = selection_sort_key(0.95, 0.55, 0.6, 0.6, 10)
    steady = selection_sort_key(0.70, 0.72, 0.6, 0.6, 15)
    assert steady > spiky
