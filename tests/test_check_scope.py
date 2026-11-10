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


def _spelled(draft_url: str, own_label: str = "https://www.mysite.example/p/", own_stored: bool = True, extra_labels=()):
    body = "dirt work software " + " ".join(f"word{index}" for index in range(400))
    labels = [
        SerpLabel(QUERY, "https://c1.example/", 1, None, "manual_csv"),
        SerpLabel(QUERY, own_label, 2, None, "manual_csv"),
        SerpLabel(QUERY, "https://c3.example/", 3, None, "manual_csv"),
        *extra_labels,
    ]
    pages = {
        url: PageDoc(url=url, title="Dirt work software", body=body, word_count=400)
        for url in ("https://c1.example/", "https://c3.example/")
    }
    if own_stored:
        pages[own_label] = PageDoc(url=own_label, title="Dirt work software", body=body, word_count=400)
    draft = PageDoc(url=draft_url, title="Dirt work software", body=body, word_count=400)
    return check_draft(
        query=QUERY, html="", url=draft_url, labels=labels, pages=pages, model=_FixedModel(),
        calibration=Calibration(None, None, 0.0, "no threshold"), run_ai=False, draft_doc=draft,
    )


def test_the_users_page_typed_another_way_replaces_its_stored_copy():
    # Before: the stored www copy stayed in the set as a competitor of its own draft, and the scope
    # told a page Google already ranks that the check cannot say whether Google would show it.
    for typed in ("https://mysite.example/p", "http://www.mysite.example/p", "https://mysite.example/p/?utm_source=x"):
        result = _spelled(typed)
        assert not result.added_draft, typed
        assert result.predicted_of == 3
        assert sorted(row.url for row in result.competitors) == ["https://c1.example/", "https://c3.example/"]
        assert "already include your page" in result.scope
        assert "among the 2 stored competitor pages" in result.scope


def test_a_labeled_page_without_stored_html_is_not_called_new_or_a_missing_competitor():
    result = _spelled("https://mysite.example/p", own_stored=False)
    assert result.added_draft  # its text comes from the draft only
    assert "already include your page" in result.scope
    assert "cannot say whether Google would show" not in result.scope
    assert result.missing_urls == []  # the user's own page is not a competitor without HTML


def test_one_competitor_labeled_under_two_spellings_is_scored_once():
    extra = (SerpLabel(QUERY, "http://www.c1.example", 4, None, "manual_csv"),)
    result = _spelled("https://www.mysite.example/p/", extra_labels=extra)
    assert [row.url for row in result.competitors].count("https://c1.example/") == 1
    assert result.predicted_of == 3


def test_each_size_in_the_scope_is_the_size_of_the_experiment_it_cites():
    import re
    from pathlib import Path

    experiments = (Path(__file__).resolve().parents[1] / "docs" / "EXPERIMENTS.md").read_text(encoding="utf-8")
    e16 = experiments.split("E16.", 1)[1].split("\n## ", 1)[0]
    dev_queries = re.search(r"Frozen dev split \((\d+) queries", e16).group(1)
    e18 = experiments.split("E18.", 1)[1].split("\n## ", 1)[0]
    assert "same 30 queries" in e18
    new_page = check_scope(True, 3)
    assert "E16" in new_page and f"({dev_queries} labeled queries" in new_page
    assert "E18" in new_page and "30 queries checked again from Nov 6 to Nov 9, 2026" in new_page
    e14 = experiments.split("E14.", 1)[1].split("\n## ", 1)[0]
    assert "30 queries" in e14 and "E14" in check_scope(False, 3)
