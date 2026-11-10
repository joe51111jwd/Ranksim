"""A check says which question it answers: order among stored results, not entry into them (P0-5).
Fixtures are synthetic pages, not Google data."""

from aeo.ranksim.check import check_draft, check_scope
from aeo.ranksim.confidence import Calibration
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel

QUERY = "dirt work software"


class _FixedModel:
    def feature_name(self):
        return ["bm25f"]

    def predict(self, matrix):
        return [10.0 - 2.0 * index for index in range(len(matrix))]

    def feature_importance(self, importance_type="gain"):
        return [0.0]


def _check(draft_url: str):
    body = "dirt work software " + " ".join(f"word{index}" for index in range(400))
    labels, pages = [], {}
    for position in range(1, 4):
        url = f"https://c{position}.example/"
        labels.append(SerpLabel(QUERY, url, position, None, "manual_csv"))
        pages[url] = PageDoc(url=url, title="Dirt work software", body=body, word_count=400)
    draft = PageDoc(url=draft_url, title="Dirt work software", body=body, word_count=400)
    calibration = Calibration(None, None, 0.0, "no threshold")
    return check_draft(
        query=QUERY, html="", url=draft_url, labels=labels, pages=pages, model=_FixedModel(),
        calibration=calibration, run_ai=False, draft_doc=draft,
    )


def test_a_new_page_is_told_the_check_cannot_say_whether_google_shows_it():
    result = _check("https://draft.example/")
    assert result.added_draft
    assert result.scope == check_scope(True, 3)
    assert "among the 3 stored competitor pages" in result.scope
    assert "cannot say whether Google would show your page at all" in result.scope


def test_a_page_already_in_the_results_is_compared_with_the_same_pages():
    result = _check("https://c2.example/")
    assert not result.added_draft
    assert result.scope == check_scope(False, 2)
    assert "already include your page" in result.scope
    assert "cannot say whether" not in result.scope


def test_scope_text_cites_its_evidence_and_uses_no_long_dashes():
    for added in (True, False):
        text = check_scope(added, 1)
        assert "docs/EXPERIMENTS.md" in text
        assert "1 stored competitor page " in text
        assert "\u2014" not in text and "\u2013" not in text
        assert "accuracy" not in text.lower() and "guarantee" not in text.lower()


def test_an_error_result_has_no_scope():
    labels = [SerpLabel(QUERY, "https://c1.example/", 1, None, "manual_csv")]
    result = check_draft(
        query=QUERY, html="", url="https://draft.example/", labels=labels, pages={}, model=None,
        calibration=Calibration(None, None, 0.0, "no threshold"), run_ai=False,
    )
    assert result.error and result.scope == ""
