"""Confidence is calibrated on the statistic the check labels, from the model's own scores, without
pages the 150-word rule placed. Fixtures are synthetic, not Google data."""

from aeo.ranksim.confidence import Calibration, PairMargin, adjacent_margins, calibrate, pair_margins
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import RankExample, gate_thin_pages, gated_rows

QUERY = "dirt work software"


def _row(url, position, thin=False):
    return RankExample(QUERY, url, float(position), {"thin_content": 1.0 if thin else 0.0})


def test_gated_rows_are_thin_pages_in_a_query_with_a_fuller_page():
    solid, thin = _row("https://a.example/", 1), _row("https://b.example/", 2, thin=True)
    assert gated_rows([solid, thin]) == {id(thin)}
    only_thin = [_row("https://c.example/", 1, thin=True), _row("https://d.example/", 2, thin=True)]
    assert gated_rows(only_thin) == set()  # the rule moves nothing when every page is thin


def test_margins_leave_out_pairs_the_150_word_rule_decided():
    rows = [_row("https://a.example/", 1), _row("https://b.example/", 2), _row("https://c.example/", 3, thin=True)]
    raw = {id(rows[0]): 3.0, id(rows[1]): 2.9, id(rows[2]): 5.0}
    gated_scores = gate_thin_pages(rows, raw)
    scores = [gated_scores[id(row)] for row in rows]
    assert scores[2] == 2.9 - 1  # the rule's synthetic offset
    gated = gated_rows(rows)
    pairs = pair_margins(rows, scores, gated)
    assert len(pairs) == 1 and abs(pairs[0].gap - 0.1) < 1e-9  # only a vs b; no synthetic gap of 1.0
    adjacent = adjacent_margins(rows, scores, gated)
    assert len(adjacent) == 2 and all(abs(case.gap - 0.1) < 1e-9 for case in adjacent)
    assert pair_margins(rows, scores) != pairs  # without the exclusion the gated pairs come back


def test_adjacent_case_needs_both_neighbour_orders_right():
    rows = [_row(f"https://p{index}.example/", index + 1) for index in range(4)]
    # Predicted order p0, p2, p1, p3. p2 sits between p0 (right) and p1 (wrong).
    scores = [4.0, 2.0, 3.0, 0.5]
    cases = adjacent_margins(rows, scores)
    by_gap = sorted((round(case.gap, 3), case.correct) for case in cases)
    # p0 (right vs p2), p2 (wrong vs p1), p1 (wrong vs p2), p3 (right vs p1, gap 1.5)
    assert by_gap == [(1.0, False), (1.0, False), (1.0, True), (1.5, True)]
    clean = adjacent_margins(rows, [4.0, 3.0, 2.0, 0.5])
    assert all(case.correct for case in clean) and len(clean) == 4


def test_calibrate_wording_names_the_statistic():
    cases = [PairMargin(1.0, True) for _ in range(40)]
    assert "nearest page in the predicted order" in calibrate(cases, kind="adjacent").note
    assert "between two pages" in calibrate(cases, kind="pairs").note


class _FixedModel:
    """Scores the n-th example 10, 8, 6, ... whatever its features: the check's own logic is under test."""

    def feature_name(self):
        return ["bm25f"]

    def predict(self, matrix):
        return [10.0 - 2.0 * index for index in range(len(matrix))]

    def feature_importance(self, importance_type="gain"):
        return [0.0]


def _check(draft_words: int, calibration: Calibration):
    from aeo.ranksim.check import check_draft

    filler = " ".join(f"word{index}" for index in range(400))
    labels, pages = [], {}
    for position in range(1, 4):
        url = f"https://c{position}.example/"
        labels.append(SerpLabel(QUERY, url, position, None, "manual_csv"))
        pages[url] = PageDoc(url=url, title="Dirt work software", body=f"dirt work software {filler}", word_count=400)
    draft = PageDoc(
        url="https://draft.example/",
        title="Dirt work software",
        body=" ".join(["dirt"] * draft_words),
        word_count=draft_words,
    )
    return check_draft(
        query=QUERY, html="", url=draft.url, labels=labels, pages=pages, model=_FixedModel(),
        calibration=calibration, run_ai=False, draft_doc=draft,
    )


def test_a_thin_draft_is_never_clearer_from_the_rules_offset():
    # Before: the gated draft sat 1.0 below the last full page, and a 0.5 threshold called that CLEARER.
    result = _check(100, Calibration(0.5, 0.9, 0.5, "pairs", adjacent_threshold=0.5, adjacent_note="adjacent"))
    assert result.predicted_rank == 4
    assert result.confidence == "UNSURE"
    assert "150 words" in result.confidence_note
    assert {row.confidence for row in result.competitors} == {"UNSURE"}


def test_the_predicted_rank_uses_the_adjacent_threshold_not_the_head_to_head_one():
    # Head-to-head gaps of 2.0 or more clear 0.5; no adjacent threshold passed, so the rank stays UNSURE.
    result = _check(400, Calibration(0.5, 0.9, 0.5, "pairs", adjacent_threshold=None, adjacent_note="no adjacent threshold"))
    assert result.confidence == "UNSURE"
    assert result.confidence_note == "no adjacent threshold"
    assert {row.confidence for row in result.competitors} == {"CLEARER"}
    both = _check(400, Calibration(0.5, 0.9, 0.5, "pairs", adjacent_threshold=0.5, adjacent_note="adjacent"))
    assert both.confidence == "CLEARER"
