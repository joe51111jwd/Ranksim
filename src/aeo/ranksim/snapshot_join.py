"""Join labeled SERP rows to HTML that is already on disk.

Normalization follows ``normalize_url`` (scheme, host, and trailing slash).
``www`` is not removed, and ``http`` is not rewritten to ``https``. A label
with no file stays missing. Empty files are not treated as pages.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from aeo.crawl.urls import normalize_url
from aeo.ranksim.coverage import BROAD_CLAIM_MIN_PAIRS, BROAD_CLAIM_MIN_QUERIES
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.saved_html import load_saved_html

SNAPSHOT_DIRS = ("saved_html", "pr8", "pr8_v2", "pr8_v3")


@dataclass(frozen=True)
class SnapshotPage:
    url: str
    html: str
    source: str


@dataclass(frozen=True)
class JoinRow:
    query: str
    url: str
    position: float
    reason: str
    snapshot_url: str | None
    source: str | None


@dataclass
class JoinReport:
    label_source: str
    dataset_id: str
    total_rows: int
    exact_rows: int
    normalized_extra_rows: int
    usable_rows: int
    missing_rows: int
    empty_rows: int
    queries_total: int
    queries_with_pairs: int
    pairs: int
    broad_claim: bool
    reason_counts: dict[str, int]
    rows: list[JoinRow] = field(default_factory=list)
    snapshots: dict[str, SnapshotPage] = field(default_factory=dict)

    def format(self) -> str:
        lines = [
            f"Label source: {self.label_source}",
            f"Dataset: {self.dataset_id}",
            f"Labeled rows: {self.total_rows}",
            f"Exact rows: {self.exact_rows}",
            f"normalized extra rows: {self.normalized_extra_rows}",
            f"Usable rows: {self.usable_rows}",
            f"Empty HTML rows: {self.empty_rows}",
            f"no local snapshot: {self.missing_rows}",
            f"Queries: {self.queries_total}",
            f"Queries with pairs: {self.queries_with_pairs}",
            f"Pairs: {self.pairs}",
            f"Broad claim: {'no' if not self.broad_claim else 'reporting bar met'}",
        ]
        for reason in sorted(self.reason_counts):
            lines.append(f"{reason.replace('_', ' ')}: {self.reason_counts[reason]}")
        if self.normalized_extra_rows == 0:
            lines.append("Normalization did not recover any additional labeled rows.")
        lines.append(
            "PREDICTED ranks on this join are only among stored pages. "
            "This is not a broad accuracy estimate."
            if not self.broad_claim
            else "The reporting bar is met. PREDICTED ranks are still not live Google ranks."
        )
        return "\n".join(lines) + "\n"


@dataclass
class CohortMetrics:
    baseline_pairwise: float | None
    queries: int
    pairs: int
    ai_measured: bool
    ai_pairwise: float | None
    same_cohort: bool
    note: str


def load_snapshot_dir(directory: str | Path, *, source: str) -> dict[str, SnapshotPage]:
    found, _missing = load_saved_html(Path(directory))
    pages: dict[str, SnapshotPage] = {}
    for url, html in found.items():
        pages[url] = SnapshotPage(url=url, html=html, source=source)
    return pages


def audit_repo_snapshots(root: str | Path, *, serp_name: str = "serp.csv") -> JoinReport:
    base = Path(root)
    snapshots: dict[str, SnapshotPage] = {}
    for name in SNAPSHOT_DIRS:
        folder = base / name
        if not (folder / "manifest.csv").is_file():
            continue
        for url, page in load_snapshot_dir(folder, source=name).items():
            # Later directories replace the same URL. saved_html is first.
            # pr8_v3 replaces pr8 and pr8_v2 when they share a URL.
            snapshots[url] = page
    labels = load_serp_file(base / serp_name)
    return join_snapshots(labels, snapshots, label_source=str(base / serp_name))


def join_snapshots(
    labels: list[SerpLabel],
    snapshots: dict[str, SnapshotPage],
    *,
    label_source: str,
) -> JoinReport:
    by_norm: dict[str, SnapshotPage] = {}
    for page in snapshots.values():
        try:
            by_norm.setdefault(normalize_url(page.url), page)
        except ValueError:
            continue
    rows: list[JoinRow] = []
    exact_rows = 0
    extra_rows = 0
    empty_rows = 0
    for label in labels:
        exact = snapshots.get(label.url)
        norm_key = _norm(label.url)
        normalized = by_norm.get(norm_key) if norm_key else None
        chosen = exact or normalized
        if chosen is None or not (chosen.html or "").strip():
            if chosen is not None:
                reason = "empty_html"
                empty_rows += 1
                rows.append(JoinRow(label.query, label.url, label.position, reason, chosen.url, chosen.source))
            else:
                rows.append(JoinRow(label.query, label.url, label.position, "no_local_snapshot", None, None))
            continue
        if exact is not None and exact.url == chosen.url:
            reason = "matched_exact"
            exact_rows += 1
        else:
            reason = "matched_normalized"
            extra_rows += 1
        rows.append(JoinRow(label.query, label.url, label.position, reason, chosen.url, chosen.source))

    usable = [row for row in rows if row.reason in {"matched_exact", "matched_normalized"}]
    pairs, queries_with_pairs = _pairs(usable)
    reason_counts: dict[str, int] = {}
    for row in rows:
        reason_counts[row.reason] = reason_counts.get(row.reason, 0) + 1
    missing_rows = reason_counts.get("no_local_snapshot", 0)
    broad = queries_with_pairs >= BROAD_CLAIM_MIN_QUERIES and pairs >= BROAD_CLAIM_MIN_PAIRS
    return JoinReport(
        label_source=label_source,
        dataset_id=_dataset_id(label_source, labels, snapshots),
        total_rows=len(rows),
        exact_rows=exact_rows,
        normalized_extra_rows=extra_rows,
        usable_rows=len(usable),
        missing_rows=missing_rows,
        empty_rows=empty_rows,
        queries_total=len({row.query for row in rows}),
        queries_with_pairs=queries_with_pairs,
        pairs=pairs,
        broad_claim=broad,
        reason_counts=reason_counts,
        rows=rows,
        snapshots=snapshots,
    )


def cohort_metrics(report: JoinReport, *, judge_scores: dict[tuple[str, str], float] | None = None) -> CohortMetrics:
    """Baseline on the joined rows. Judge scores are optional and never fetched."""
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.groundtruth import SerpLabel
    from aeo.ranksim.judge import relevance_experiment
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.loop import page_from_html

    pages = {}
    labels: list[SerpLabel] = []
    for row in report.rows:
        if row.reason not in {"matched_exact", "matched_normalized"} or not row.snapshot_url:
            continue
        snap = report.snapshots.get(row.snapshot_url)
        if snap is None or not snap.html.strip():
            continue
        doc = page_from_html(row.url, snap.html)
        pages[row.url] = doc
        key = _norm(row.url)
        if key:
            pages[key] = doc
        labels.append(SerpLabel(row.query, row.url, row.position, None, "snapshot_join"))
    examples, _uncovered, _withheld = build_examples(labels, pages)
    fit = loo_fit(examples)
    ai_measured = False
    ai_pairwise = None
    same = True
    if judge_scores is not None:
        experiment = relevance_experiment(examples, judge_scores)
        ai_measured = experiment.measured
        ai_pairwise = experiment.with_judge_pairwise
        same = experiment.measured
    note = (
        "Baseline is leave-one-query-out on the joined pages only. "
        "REAL positions are the labels. They are not copied into a feature. "
    )
    if not ai_measured:
        note += "AI relevance was not measured. No judge scores were supplied, and none were invented."
    else:
        note += "AI relevance used only the supplied scores on this same row set. It is not a default feature."
    return CohortMetrics(
        baseline_pairwise=fit.pairwise,
        queries=fit.n_queries,
        pairs=fit.n_pairs,
        ai_measured=ai_measured,
        ai_pairwise=ai_pairwise,
        same_cohort=same,
        note=note,
    )


def _pairs(rows: list[JoinRow]) -> tuple[int, int]:
    grouped: dict[str, list[JoinRow]] = {}
    for row in rows:
        grouped.setdefault(row.query, []).append(row)
    total = 0
    queries = 0
    for group in grouped.values():
        query_pairs = 0
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if left.position == right.position:
                    continue
                query_pairs += 1
        if query_pairs:
            queries += 1
            total += query_pairs
    return total, queries


def _dataset_id(label_source: str, labels: list[SerpLabel], snapshots: dict[str, SnapshotPage]) -> str:
    label_rows = [[row.query, row.url, row.position] for row in sorted(labels, key=lambda item: (item.query, item.url, item.position))]
    files = []
    for url in sorted(snapshots):
        page = snapshots[url]
        digest = hashlib.sha256(page.html.encode("utf-8", errors="replace")).hexdigest()
        files.append([page.source, url, digest])
    payload = {"labels": label_source, "rows": label_rows, "files": files}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()[:16]


def _norm(url: str) -> str | None:
    try:
        return normalize_url(url)
    except ValueError:
        return None
