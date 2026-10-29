"""Page diagnostics for one question.

Categories describe the sandbox outcome. They are heuristics over the crawl,
the hybrid retrieval, and the simulated citation share. They are not a claim
about what ChatGPT, Perplexity, or Google AI Overviews did.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from aeo.textutil import salient_terms, tokenize

CITED_THRESHOLD = 0.5
WORD_COUNT_TARGET = 120
RAW_TEXT_MIN_WORDS = 40


@dataclass(frozen=True)
class HtmlChecks:
    has_h1: bool
    has_title: bool
    has_meta_description: bool
    has_canonical: bool
    text_in_raw_html: bool
    word_count: int
    word_count_ok: bool
    word_count_target: int
    has_updated_date: bool
    answer_first_summary: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DiagnosticSignals:
    user_pages_indexed: int
    user_pages_blocked: int
    user_pages_failed: int
    thin_shell: bool
    js_dependent: bool
    raw_text_usable: bool
    best_page_found: bool
    best_page_bm25: float
    user_in_top_passages: bool
    best_user_passage_rank: int | None
    citation_share: float
    mention_rate: float
    competitor_pages_indexed: int
    competitor_citation_share: float
    html: HtmlChecks | None = None


def answer_first_summary(main_text: str, question: str) -> bool:
    terms = salient_terms(question)
    if not terms:
        return False
    opening = tokenize(main_text)[:80]
    if not opening:
        return False
    present = set(opening)
    hits = sum(1 for term in terms if term in present)
    return hits / len(terms) >= 0.5


def build_html_checks(
    *,
    title: str | None,
    meta_description: str | None,
    canonical: str | None,
    h1: str | None,
    raw_word_count: int,
    updated: str | None,
    published: str | None,
    main_text: str,
    question: str,
    js_dependent: bool,
) -> HtmlChecks:
    usable = raw_word_count >= RAW_TEXT_MIN_WORDS and not js_dependent
    return HtmlChecks(
        has_h1=bool((h1 or "").strip()),
        has_title=bool((title or "").strip()),
        has_meta_description=bool((meta_description or "").strip()),
        has_canonical=bool((canonical or "").strip()),
        text_in_raw_html=usable if js_dependent else raw_word_count >= RAW_TEXT_MIN_WORDS,
        word_count=raw_word_count,
        word_count_ok=raw_word_count >= WORD_COUNT_TARGET,
        word_count_target=WORD_COUNT_TARGET,
        has_updated_date=bool((updated or "").strip() or (published or "").strip()),
        answer_first_summary=answer_first_summary(main_text, question),
    )


def diagnose(signals: DiagnosticSignals) -> tuple[str, list[str]]:
    """Return ``(label, reasons)``.

    Order: no usable page, JavaScript-only content, no topical page,
    cited often enough, rivals taking the citations, otherwise a weak page.
    """
    reasons: list[str] = []
    if signals.user_pages_indexed == 0:
        if signals.user_pages_blocked or signals.user_pages_failed or signals.thin_shell or signals.js_dependent:
            reasons.append("No usable page text was indexed.")
            if signals.user_pages_blocked:
                reasons.append("robots.txt disallowed one or more URLs.")
            if signals.user_pages_failed:
                reasons.append("One or more fetches failed.")
            if signals.thin_shell or signals.js_dependent:
                reasons.append("Fetched HTML has almost no extractable text. The content may depend on JavaScript.")
            return "crawl_index_issue", reasons
        if signals.best_page_bm25 <= 0 and not signals.user_in_top_passages:
            if signals.best_page_found:
                reasons.append("The page that was indexed does not share salient terms with the question.")
            else:
                reasons.append("The crawl finished but no page on this site matches the question.")
            return "missing_page", reasons
        reasons.append("Only a short page was indexed, and it rarely wins retrieval or citation in this sandbox.")
        return "weak_page", reasons

    if signals.js_dependent and not signals.raw_text_usable:
        reasons.append(
            "Main content is missing from raw HTML, so answer engines that do not run JavaScript may not see it."
        )
        return "crawl_index_issue", reasons

    if signals.best_page_bm25 <= 0 and not signals.user_in_top_passages:
        reasons.append("Indexed pages do not share salient terms with the question.")
        return "missing_page", reasons

    if signals.citation_share >= CITED_THRESHOLD:
        reasons.append(
            f"The domain was cited in {signals.citation_share:.0%} of simulated answers from this sandbox."
        )
        return "likely_cited", reasons

    if (
        signals.user_in_top_passages
        and signals.competitor_citation_share >= CITED_THRESHOLD
        and signals.citation_share < CITED_THRESHOLD
    ):
        reasons.append("Retrieved passages include this site, but simulated answers cite other domains.")
        return "third_parties_dominating", reasons

    reasons.append("A relevant page is indexed but it rarely wins retrieval or citation in this sandbox.")
    if signals.html and not signals.html.answer_first_summary:
        reasons.append("The page does not open with an answer-first summary for this question.")
    if signals.html and not signals.html.word_count_ok:
        reasons.append(
            f"Raw HTML main text is thin ({signals.html.word_count} words; target is {signals.html.word_count_target})."
        )
    if signals.best_user_passage_rank is None:
        reasons.append("No passage from this site reached the fused top passages.")
    return "weak_page", reasons
