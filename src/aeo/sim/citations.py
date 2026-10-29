"""Parse grounded citations and score whether the submitted domain was used."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MARKER = re.compile(r"\[S(\d+)\]", re.I)


@dataclass(frozen=True)
class SourcePassage:
    marker_num: int
    passage_id: str
    url: str
    domain: str


@dataclass(frozen=True)
class Citation:
    position: int
    marker: str
    passage_id: str
    url: str
    domain: str


@dataclass
class ParseResult:
    citations: list[Citation] = field(default_factory=list)
    unknown_markers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerScore:
    cited: bool
    citation_position: int | None
    mentioned: bool
    citations: list[Citation]
    unknown_markers: list[str]


@dataclass(frozen=True)
class QuestionMetrics:
    citation_share: float
    mention_rate: float
    mean_citation_position: float | None
    n_runs: int
    cited_runs: int
    mentioned_runs: int


def parse_citations(answer: str, sources: list[SourcePassage]) -> ParseResult:
    """Keep markers that match a provided source. Unknown markers are recorded, not invented."""
    by_num = {source.marker_num: source for source in sources}
    result = ParseResult()
    used: set[int] = set()
    for match in _MARKER.finditer(answer or ""):
        number = int(match.group(1))
        marker = f"S{number}"
        if number in used:
            continue
        source = by_num.get(number)
        if source is None:
            if marker not in result.unknown_markers:
                result.unknown_markers.append(marker)
            continue
        used.add(number)
        result.citations.append(
            Citation(
                position=len(result.citations) + 1,
                marker=marker,
                passage_id=source.passage_id,
                url=source.url,
                domain=source.domain,
            )
        )
    return result


def domains_match(left: str, right: str) -> bool:
    return _bare(left) == _bare(right) and _bare(left) != ""


def _bare(domain: str) -> str:
    value = domain.strip().lower()
    if value.startswith("www."):
        return value[4:]
    return value


def mention_aliases(domain: str) -> list[str]:
    bare = _bare(domain)
    if not bare:
        return []
    aliases = [bare]
    label = bare.split(".")[0]
    if len(label) >= 4 and label not in {"mail", "blog", "www"}:
        aliases.append(label)
    return aliases


def domain_mentioned(answer: str, domain: str, citations: list[Citation]) -> bool:
    """True when the domain or its first label appears in prose, outside citations."""
    text = _MARKER.sub(" ", answer or "")
    for citation in citations:
        if citation.url:
            text = text.replace(citation.url, " ")
    haystack = text.lower()
    for alias in mention_aliases(domain):
        if "." in alias:
            if alias in haystack:
                return True
            continue
        if re.search(rf"\b{re.escape(alias)}\b", haystack):
            return True
    return False


def score_answer(
    answer: str,
    sources: list[SourcePassage],
    user_domain: str,
) -> AnswerScore:
    parsed = parse_citations(answer, sources)
    position = None
    for citation in parsed.citations:
        if domains_match(citation.domain, user_domain):
            position = citation.position
            break
    return AnswerScore(
        cited=position is not None,
        citation_position=position,
        mentioned=domain_mentioned(answer, user_domain, parsed.citations),
        citations=parsed.citations,
        unknown_markers=parsed.unknown_markers,
    )


def competitor_citation_share(scores: list[AnswerScore], user_domain: str) -> float:
    if not scores:
        return 0.0
    hits = 0
    for score in scores:
        if any(not domains_match(citation.domain, user_domain) for citation in score.citations):
            hits += 1
    return hits / len(scores)


def aggregate_scores(scores: list[AnswerScore]) -> QuestionMetrics:
    n_runs = len(scores)
    if n_runs == 0:
        return QuestionMetrics(0.0, 0.0, None, 0, 0, 0)
    cited_runs = sum(1 for score in scores if score.cited)
    mentioned_runs = sum(1 for score in scores if score.mentioned)
    positions = [score.citation_position for score in scores if score.cited and score.citation_position is not None]
    mean_position = sum(positions) / len(positions) if positions else None
    return QuestionMetrics(
        citation_share=cited_runs / n_runs,
        mention_rate=mentioned_runs / n_runs,
        mean_citation_position=mean_position,
        n_runs=n_runs,
        cited_runs=cited_runs,
        mentioned_runs=mentioned_runs,
    )
