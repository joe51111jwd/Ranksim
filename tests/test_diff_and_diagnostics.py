from aeo.sim.calibrate import Observation, calibration_report
from aeo.sim.citations import QuestionMetrics
from aeo.sim.diagnostics import DiagnosticSignals, HtmlChecks, diagnose
from aeo.sim.diff import diff_metrics


def _signals(**overrides) -> DiagnosticSignals:
    base = dict(
        user_pages_indexed=1,
        user_pages_blocked=0,
        user_pages_failed=0,
        thin_shell=False,
        js_dependent=False,
        raw_text_usable=True,
        best_page_found=True,
        best_page_bm25=1.2,
        user_in_top_passages=True,
        best_user_passage_rank=1,
        citation_share=0.0,
        mention_rate=0.0,
        competitor_pages_indexed=3,
        competitor_citation_share=0.0,
        html=HtmlChecks(
            has_h1=True,
            has_title=True,
            has_meta_description=True,
            has_canonical=True,
            text_in_raw_html=True,
            word_count=40,
            word_count_ok=False,
            word_count_target=120,
            has_updated_date=False,
            answer_first_summary=False,
        ),
    )
    base.update(overrides)
    return DiagnosticSignals(**base)


def test_diagnosis_branches():
    assert diagnose(_signals(user_pages_indexed=0, user_pages_blocked=1, best_page_found=False, best_page_bm25=0, user_in_top_passages=False, html=None))[0] == "crawl_index_issue"
    assert diagnose(_signals(user_pages_indexed=0, thin_shell=True, best_page_found=False, best_page_bm25=0, user_in_top_passages=False, html=None))[0] == "crawl_index_issue"
    assert diagnose(_signals(user_pages_indexed=0, best_page_found=False, best_page_bm25=0, user_in_top_passages=False, html=None))[0] == "missing_page"
    assert diagnose(_signals(js_dependent=True, raw_text_usable=False))[0] == "crawl_index_issue"
    assert diagnose(_signals(best_page_bm25=0, user_in_top_passages=False, best_user_passage_rank=None))[0] == "missing_page"
    assert diagnose(_signals(citation_share=0.8))[0] == "likely_cited"
    assert diagnose(_signals(citation_share=0.2, competitor_citation_share=0.8, user_in_top_passages=True))[0] == "third_parties_dominating"
    label, reasons = diagnose(_signals(citation_share=0.0, user_in_top_passages=False, best_user_passage_rank=None, best_page_bm25=0.4))
    assert label == "weak_page"
    assert any("answer-first" in reason for reason in reasons)
    assert any("thin" in reason for reason in reasons)


def test_diff_reports_deltas_and_improvement():
    before = QuestionMetrics(0.2, 0.4, 2.0, 5, 1, 2)
    after = QuestionMetrics(0.6, 0.4, 1.0, 5, 3, 2)
    diff = diff_metrics(before, after, diagnosis_before="weak_page", diagnosis_after="likely_cited")
    assert diff.citation_share_delta == 0.4
    assert diff.mention_rate_delta == 0
    assert diff.mean_citation_position_delta == -1
    assert diff.improved is True
    assert diff.diagnosis_changed is True
    assert diff.improvements
    assert not diff.regressions


def test_diff_regression():
    before = QuestionMetrics(0.8, 0.8, 1.0, 5, 4, 4)
    after = QuestionMetrics(0.2, 0.2, 3.0, 5, 1, 1)
    diff = diff_metrics(before, after, diagnosis_before="likely_cited", diagnosis_after="weak_page")
    assert diff.improved is False
    assert diff.citation_share_delta == -0.6
    assert any("decreased" in item for item in diff.regressions)


def test_calibration_is_empty_until_observations_exist_and_then_is_exact():
    empty = calibration_report([])
    assert empty["observations"] == 0
    assert empty["overall"]["accuracy"] is None
    assert empty["overall"]["brier_score"] is None

    report = calibration_report(
        [
            Observation("chatgpt", True, 1.0),
            Observation("chatgpt", True, 0.0),
            Observation("perplexity", False, None),
        ]
    )
    assert report["observations"] == 3
    assert report["observations_missing_prediction"] == 1
    assert report["overall"]["observations_with_prediction"] == 2
    assert report["overall"]["accuracy"] == 0.5
    assert report["overall"]["precision"] == 1
    assert report["overall"]["recall"] == 0.5
    assert report["overall"]["brier_score"] == 0.5
    assert "perplexity" not in report["by_engine"]
