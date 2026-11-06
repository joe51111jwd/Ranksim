"""Coverage of labeled rows against pages we actually stored.

This does not fetch HTML, invent page text, or call a model. Pair counts use the
same position rule as LambdaMART: a pair exists only when one labeled position
is strictly better than the other.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from aeo.crawl.urls import normalize_url
from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import _lookup_page

# Reporting bar only. The older negative AI run had 12 queries and 33 pairs.
# Sixteen queries was the SmartCrew file size those demos quoted. Neither number
# is a statistical test.
BROAD_CLAIM_MIN_QUERIES = 16
BROAD_CLAIM_MIN_PAIRS = 100


@dataclass(frozen=True)
class RowDisposition:
    query: str
    url: str
    position: float
    status: str
    reason: str


@dataclass
class CoverageAudit:
    label_source: str
    page_source: str
    total_rows: int
    stored_rows: int
    missing_rows: int
    singleton_rows: int
    queries_total: int
    queries_with_pairs: int
    pairs: int
    reason_counts: dict[str, int]
    pair_cohort_id: str
    broad_claim: bool
    note: str
    rows: list[RowDisposition] = field(default_factory=list)


def audit_labels(
    labels: list[SerpLabel],
    pages: dict,
    *,
    label_source: str,
    page_source: str,
    snapshot_urls: set[str] | None = None,
) -> CoverageAudit:
    """Join labels to stored pages. Snapshots name local HTML that is not indexed yet."""
    snapshots = _snapshot_keys(snapshot_urls or set())
    stored_by_query: dict[str, list[SerpLabel]] = {}
    preliminary: list[tuple[SerpLabel, str, str]] = []
    for label in labels:
        page = _lookup_page(pages, label.url)
        if page is None:
            if label.url in snapshots:
                reason = "snapshot_not_in_index"
            else:
                reason = "no_stored_html"
            preliminary.append((label, "missing_html", reason))
            continue
        stored_by_query.setdefault(label.query, []).append(label)
        preliminary.append((label, "stored", ""))

    rows: list[RowDisposition] = []
    for label, status, reason in preliminary:
        if status == "stored" and len(stored_by_query.get(label.query, [])) < 2:
            status = "stored_singleton"
            reason = "query_has_fewer_than_two_stored_pages"
        elif status == "stored":
            reason = "usable_for_pairs"
        rows.append(
            RowDisposition(
                query=label.query,
                url=label.url,
                position=label.position,
                status=status,
                reason=reason,
            )
        )

    pair_rows = [row for row in rows if row.status == "stored" and row.reason == "usable_for_pairs"]
    by_query: dict[str, list[RowDisposition]] = {}
    for row in pair_rows:
        by_query.setdefault(row.query, []).append(row)
    pairs = sum(_pair_total(group) for group in by_query.values())
    queries_with_pairs = sum(1 for group in by_query.values() if _pair_total(group) > 0)
    reason_counts: dict[str, int] = {}
    for row in rows:
        reason_counts[row.reason] = reason_counts.get(row.reason, 0) + 1
    stored_rows = sum(1 for row in rows if row.status != "missing_html")
    missing_rows = sum(1 for row in rows if row.status == "missing_html")
    singleton_rows = sum(1 for row in rows if row.status == "stored_singleton")
    broad = queries_with_pairs >= BROAD_CLAIM_MIN_QUERIES and pairs >= BROAD_CLAIM_MIN_PAIRS
    if broad:
        note = (
            f"{pairs} pairs on {queries_with_pairs} queries meet the reporting bar. "
            "PREDICTED agreement on this cohort is still not a live Google rank."
        )
    else:
        note = (
            f"{queries_with_pairs} queries and {pairs} pairs are below the reporting bar "
            f"({BROAD_CLAIM_MIN_QUERIES} queries and {BROAD_CLAIM_MIN_PAIRS} pairs). "
            "Do not treat this cohort as a broad accuracy estimate. "
            "PREDICTED ranks are only among stored candidates."
        )
    return CoverageAudit(
        label_source=label_source,
        page_source=page_source,
        total_rows=len(rows),
        stored_rows=stored_rows,
        missing_rows=missing_rows,
        singleton_rows=singleton_rows,
        queries_total=len({row.query for row in rows}),
        queries_with_pairs=queries_with_pairs,
        pairs=pairs,
        reason_counts=reason_counts,
        pair_cohort_id=_pair_cohort_id(pair_rows),
        broad_claim=broad,
        note=note,
        rows=rows,
    )


def _snapshot_keys(urls: set[str]) -> set[str]:
    keys = set(urls)
    for url in urls:
        try:
            keys.add(normalize_url(url))
        except ValueError:
            continue
    return keys


def same_pair_cohort(left: CoverageAudit, right: CoverageAudit) -> bool:
    return bool(left.pair_cohort_id) and left.pair_cohort_id == right.pair_cohort_id


def llm_relevance_in_default_features() -> bool:
    return "llm_relevance" in FEATURE_NAMES


def ai_comparison_note(
    *,
    baseline: float | None,
    with_relevance: float | None,
    same_cohort: bool,
    broad_claim: bool,
) -> str:
    """Say what an AI-relevance delta means. It does not turn the feature on."""
    if not same_cohort:
        return "Not compared. The two runs do not share the same usable pairs."
    if baseline is None or with_relevance is None:
        return "No AI relevance delta. One side was not measured. llm_relevance is not a default feature."
    delta = with_relevance - baseline
    text = (
        f"Same usable pairs. Baseline pairwise {baseline:.3f}. "
        f"With llm_relevance {with_relevance:.3f}. Delta {delta:+.3f}. "
        "PREDICTED, not a live rank. "
    )
    if delta <= 0:
        text += "This is not evidence that AI relevance helps. It stays out of default scoring."
    elif not broad_claim:
        text += "The cohort is below the reporting bar, so this is not a broad accuracy claim. It stays out of default scoring."
    else:
        text += "A positive delta is still not turned on in default scoring by this report."
    return text


def format_audit(audit: CoverageAudit) -> str:
    lines = [
        f"Label source: {audit.label_source}",
        f"Page source: {audit.page_source}",
        f"Labeled rows: {audit.total_rows}",
        f"Stored rows: {audit.stored_rows}",
        f"Missing HTML: {audit.missing_rows}",
        f"Stored but no pair (fewer than two stored pages on the query): {audit.singleton_rows}",
        f"Queries: {audit.queries_total}",
        f"Queries with pairs: {audit.queries_with_pairs}",
        f"Pairs: {audit.pairs}",
        f"Pair cohort: {audit.pair_cohort_id or 'none'}",
        f"Broad claim: {'no' if not audit.broad_claim else 'reporting bar met'}",
    ]
    for reason in sorted(audit.reason_counts):
        lines.append(f"Reason {reason}: {audit.reason_counts[reason]}")
    lines.append(audit.note)
    return "\n".join(lines) + "\n"


def _pair_total(group: list[RowDisposition]) -> int:
    total = 0
    for left in group:
        for right in group:
            if left.position >= right.position:
                continue
            total += 1
    return total


def _pair_cohort_id(rows: list[RowDisposition]) -> str:
    if not rows:
        return ""
    payload = [
        [row.query, row.url, row.position]
        for row in sorted(rows, key=lambda item: (item.query, item.url, item.position))
    ]
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return digest[:16]
