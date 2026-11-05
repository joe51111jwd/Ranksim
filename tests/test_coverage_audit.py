"""Denominator checks for labeled rows. No model fit and no network."""

from __future__ import annotations

import csv

from aeo.ranksim.coverage import (
    ai_comparison_note,
    audit_labels,
    format_audit,
    llm_relevance_in_default_features,
    same_pair_cohort,
)
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file


def _page(url: str) -> PageDoc:
    return PageDoc(url=url, title="t", body="body " * 40, word_count=40)


def test_pairs_require_two_stored_pages_and_a_strict_position_order():
    labels = [
        SerpLabel("q", "https://example.com/a", 1, None, "manual_csv"),
        SerpLabel("q", "https://example.com/b", 2, None, "manual_csv"),
        SerpLabel("q", "https://example.com/tie", 2, None, "manual_csv"),
        SerpLabel("solo", "https://example.com/only", 1, None, "manual_csv"),
        SerpLabel("gone", "https://example.com/missing", 1, None, "manual_csv"),
    ]
    pages = {url: _page(url) for url in ("https://example.com/a", "https://example.com/b", "https://example.com/tie", "https://example.com/only")}
    audit = audit_labels(labels, pages, label_source="fixture", page_source="memory", snapshot_urls={"https://example.com/missing"})
    assert audit.total_rows == 5
    assert audit.stored_rows == 4
    assert audit.missing_rows == 1
    assert audit.singleton_rows == 1
    assert audit.queries_with_pairs == 1
    # a beats b and a beats tie. b and tie share position 2, so they are not a pair.
    assert audit.pairs == 2
    assert audit.reason_counts["snapshot_not_in_index"] == 1
    assert audit.reason_counts["query_has_fewer_than_two_stored_pages"] == 1
    assert audit.broad_claim is False
    assert "not treat this cohort as a broad accuracy estimate" in audit.note
    again = audit_labels(labels, pages, label_source="fixture", page_source="memory", snapshot_urls={"https://example.com/missing"})
    assert same_pair_cohort(audit, again)
    shrunk = audit_labels(labels[:2], pages, label_source="fixture", page_source="memory")
    assert not same_pair_cohort(audit, shrunk)
    text = format_audit(audit)
    assert "Missing HTML: 1" in text
    assert "Pairs: 2" in text


def test_ai_note_rejects_a_gain_without_the_same_cohort_or_a_positive_delta():
    assert llm_relevance_in_default_features() is False
    negative = ai_comparison_note(baseline=0.485, with_relevance=0.394, same_cohort=True, broad_claim=False)
    assert negative.startswith("Same usable pairs")
    assert "not evidence" in negative
    assert ai_comparison_note(baseline=0.50, with_relevance=0.60, same_cohort=False, broad_claim=True).startswith("Not compared")
    small_gain = ai_comparison_note(baseline=0.50, with_relevance=0.55, same_cohort=True, broad_claim=False)
    assert "stays out of default scoring" in small_gain
    assert "not measured" in ai_comparison_note(baseline=None, with_relevance=None, same_cohort=True, broad_claim=False)


def test_smartcrew_export_denominator_with_no_index():
    labels = load_serp_file("smartcrew/serp.csv")
    queries = {row.query for row in labels}
    assert len(labels) == 135
    assert len(queries) == 16
    snapshots = set()
    with open("smartcrew/saved_html/manifest.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            snapshots.add((row.get("url") or "").strip())
    audit = audit_labels(labels, {}, label_source="smartcrew/serp.csv", page_source="empty", snapshot_urls=snapshots)
    assert audit.stored_rows == 0
    assert audit.missing_rows == 135
    assert audit.pairs == 0
    assert audit.queries_with_pairs == 0
    assert audit.pair_cohort_id == ""
    assert audit.broad_claim is False
    known = "https://www.reddit.com/r/estimators/comments/11jjop2/estimating_software_options/"
    matched = next(row for row in audit.rows if row.url == known)
    assert matched.reason == "snapshot_not_in_index"
    assert audit.reason_counts["snapshot_not_in_index"] >= 1
    assert audit.reason_counts["no_stored_html"] == 135 - audit.reason_counts["snapshot_not_in_index"]
