"""Before/after diff for a question's simulated metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from aeo.sim.citations import QuestionMetrics


@dataclass(frozen=True)
class MetricsDiff:
    citation_share_before: float
    citation_share_after: float
    citation_share_delta: float
    mention_rate_before: float
    mention_rate_after: float
    mention_rate_delta: float
    mean_citation_position_before: float | None
    mean_citation_position_after: float | None
    mean_citation_position_delta: float | None
    diagnosis_before: str
    diagnosis_after: str
    diagnosis_changed: bool
    improved: bool
    improvements: list[str]
    regressions: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def diff_metrics(before: QuestionMetrics, after: QuestionMetrics, *, diagnosis_before: str, diagnosis_after: str) -> MetricsDiff:
    share_delta = round(after.citation_share - before.citation_share, 6)
    mention_delta = round(after.mention_rate - before.mention_rate, 6)
    position_delta = None
    if before.mean_citation_position is not None and after.mean_citation_position is not None:
        position_delta = round(after.mean_citation_position - before.mean_citation_position, 6)

    improvements: list[str] = []
    regressions: list[str] = []
    if share_delta > 1e-9:
        improvements.append(f"Simulated citation share increased by {share_delta:.2f}.")
    elif share_delta < -1e-9:
        regressions.append(f"Simulated citation share decreased by {abs(share_delta):.2f}.")
    if mention_delta > 1e-9:
        improvements.append(f"Simulated mention rate increased by {mention_delta:.2f}.")
    elif mention_delta < -1e-9:
        regressions.append(f"Simulated mention rate decreased by {abs(mention_delta):.2f}.")
    if position_delta is not None:
        if position_delta < -1e-9:
            improvements.append("Mean citation position moved up.")
        elif position_delta > 1e-9:
            regressions.append("Mean citation position moved down.")

    improved = share_delta > 1e-9 or (abs(share_delta) <= 1e-9 and mention_delta > 1e-9)
    return MetricsDiff(
        citation_share_before=before.citation_share,
        citation_share_after=after.citation_share,
        citation_share_delta=share_delta,
        mention_rate_before=before.mention_rate,
        mention_rate_after=after.mention_rate,
        mention_rate_delta=mention_delta,
        mean_citation_position_before=before.mean_citation_position,
        mean_citation_position_after=after.mean_citation_position,
        mean_citation_position_delta=position_delta,
        diagnosis_before=diagnosis_before,
        diagnosis_after=diagnosis_after,
        diagnosis_changed=diagnosis_before != diagnosis_after,
        improved=improved,
        improvements=improvements,
        regressions=regressions,
    )
