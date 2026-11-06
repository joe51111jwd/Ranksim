"""Join labeled rows to local HTML. No network and no invented pages."""

from __future__ import annotations

from pathlib import Path

from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.snapshot_join import join_snapshots, load_snapshot_dir


def _label(query: str, url: str, position: float) -> SerpLabel:
    return SerpLabel(query, url, position, None, "manual_csv")


def test_trailing_slash_recovers_a_row_exact_match_misses(tmp_path: Path):
    folder = tmp_path / "shots"
    folder.mkdir()
    (folder / "a.html").write_text("<html><title>Alpha</title><body><p>alpha software body</p></body></html>", encoding="utf-8")
    (folder / "b.html").write_text("<html><title>Beta</title><body><p>beta software body</p></body></html>", encoding="utf-8")
    (folder / "empty.html").write_text("   ", encoding="utf-8")
    (folder / "manifest.csv").write_text(
        "url,file\n"
        "https://example.com/a/,a.html\n"
        "https://example.com/b,b.html\n"
        "https://example.com/empty,empty.html\n",
        encoding="utf-8",
    )
    snapshots = load_snapshot_dir(folder, source="fixture")
    labels = [
        _label("example software", "https://example.com/a", 1),
        _label("example software", "https://example.com/b/", 2),
        _label("example software", "https://example.com/empty", 3),
        _label("example software", "https://www.example.com/a", 4),
        _label("example software", "http://example.com/b", 5),
        _label("other", "https://example.com/missing", 1),
    ]
    report = join_snapshots(labels, snapshots, label_source="fixture")
    by_url = {row.url: row for row in report.rows}
    assert by_url["https://example.com/a"].reason == "matched_normalized"
    assert by_url["https://example.com/b/"].reason == "matched_normalized"
    assert by_url["https://example.com/empty"].reason == "empty_html"
    assert by_url["https://www.example.com/a"].reason == "no_local_snapshot"
    assert by_url["http://example.com/b"].reason == "no_local_snapshot"
    assert by_url["https://example.com/missing"].reason == "no_local_snapshot"
    assert report.exact_rows == 0
    assert report.normalized_extra_rows == 2
    assert report.usable_rows == 2
    assert report.queries_with_pairs == 1
    assert report.pairs == 1
    assert report.broad_claim is False
    assert report.dataset_id
    again = join_snapshots(labels, snapshots, label_source="fixture")
    assert again.dataset_id == report.dataset_id


def test_exact_match_is_not_counted_as_recovered(tmp_path: Path):
    folder = tmp_path / "shots"
    folder.mkdir()
    (folder / "a.html").write_text("<html><title>A</title><body><p>words</p></body></html>", encoding="utf-8")
    (folder / "manifest.csv").write_text("url,file\nhttps://example.com/a,a.html\n", encoding="utf-8")
    labels = [
        _label("q", "https://example.com/a", 1),
        _label("q", "https://example.com/b", 2),
    ]
    report = join_snapshots(labels, load_snapshot_dir(folder, source="fixture"), label_source="fixture")
    assert report.exact_rows == 1
    assert report.normalized_extra_rows == 0
    assert report.usable_rows == 1
    assert report.pairs == 0


def test_later_snapshot_replaces_the_same_url_and_mock_scores_stay_on_that_cohort(tmp_path: Path):
    folder_a = tmp_path / "first"
    folder_b = tmp_path / "second"
    for folder, body in ((folder_a, "alpha"), (folder_b, "beta")):
        folder.mkdir()
        (folder / "a.html").write_text(f"<html><title>{body}</title><body><p>{body} software text</p></body></html>", encoding="utf-8")
        (folder / "b.html").write_text(f"<html><title>{body} b</title><body><p>{body} other software text</p></body></html>", encoding="utf-8")
        (folder / "manifest.csv").write_text("url,file\nhttps://example.com/a,a.html\nhttps://example.com/b,b.html\n", encoding="utf-8")
    first = load_snapshot_dir(folder_a, source="first")
    second = load_snapshot_dir(folder_b, source="second")
    merged = dict(first)
    merged.update(second)
    labels = [
        _label("one", "https://example.com/a", 1),
        _label("one", "https://example.com/b", 2),
        _label("two", "https://example.com/a", 1),
        _label("two", "https://example.com/b", 2),
    ]
    report = join_snapshots(labels, merged, label_source="fixture")
    assert all(row.source == "second" for row in report.rows)
    from aeo.ranksim.snapshot_join import cohort_metrics

    scores = {(row.query, row.url): 0.1 if row.url.endswith("/a") else 0.9 for row in report.rows}
    metrics = cohort_metrics(report, judge_scores=scores)
    assert metrics.baseline_pairwise is not None
    assert metrics.ai_measured is True
    assert metrics.ai_pairwise is not None
    assert metrics.same_cohort is True
    assert metrics.queries == 2
    assert metrics.pairs == 2


def test_committed_snapshots_do_not_invent_rows_and_ai_stays_off_by_default():
    from aeo.ranksim.snapshot_join import audit_repo_snapshots, cohort_metrics

    report = audit_repo_snapshots(Path("smartcrew"))
    assert report.total_rows == 135
    assert report.exact_rows == 35
    assert report.normalized_extra_rows == 0
    assert report.usable_rows == 35
    assert report.missing_rows == 100
    assert report.queries_with_pairs == 12
    assert report.pairs == 33
    assert report.broad_claim is False
    metrics = cohort_metrics(report)
    assert metrics.baseline_pairwise is not None
    assert round(metrics.baseline_pairwise, 3) == 0.545
    assert metrics.queries == 12
    assert metrics.pairs == 33
    assert metrics.ai_measured is False
    assert metrics.ai_pairwise is None
    assert "llm_relevance" not in FEATURE_NAMES
    assert metrics.same_cohort is True
    text = report.format()
    assert "normalized extra rows: 0" in text
    assert "no local snapshot: 100" in text
    assert "not a broad accuracy estimate" in text
