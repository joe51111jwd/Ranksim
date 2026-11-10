"""Same-snapshot numbers are called agreement, never accuracy (MISSION rule; B17 review item R5).
A number from the labeled file under query holdout is not accuracy on unseen results."""

import re
from types import SimpleNamespace

from aeo.ranksim.confidence import PairMargin, calibrate
from aeo.ranksim.groundtruth import ImportReport


def _accuracy_words(text: str) -> list[str]:
    """Every use of "accuracy" except the negation "not accuracy"."""
    return [
        match.group(0)
        for match in re.finditer(r"(\S+\s+)?accuracy", text, flags=re.IGNORECASE)
        if not (match.group(1) or "").lower().startswith("not")
    ]


def test_evaluate_and_train_print_agreement_not_accuracy(capsys):
    from aeo.ranksim.cli import _print_accuracy

    row = SimpleNamespace(
        query="q", n_urls=3, pairwise_accuracy=0.6, ndcg_at_10=0.7, spearman=0.2, top3_hit=1.0,
        smartcrew_real_position=None, smartcrew_predicted_rank=None,
    )
    report = SimpleNamespace(
        note="Leave-one-query-out.", n_labeled_urls=10, uncovered_urls=0, n_queries=4, n_pairs=12,
        cross_validated=True, pairwise_accuracy=0.6, ndcg_at_10=0.7, spearman=0.2, top3_hit_rate=0.5, query_scores=[row],
    )
    _print_accuracy(report)
    out = capsys.readouterr().out
    assert "Pairwise agreement: 0.600" in out
    assert "not accuracy on unseen results" in out
    assert _accuracy_words(out) == []
    report.cross_validated = False
    _print_accuracy(report)
    assert _accuracy_words(capsys.readouterr().out) == []


def test_calibration_notes_say_how_often_calls_were_right():
    found = calibrate([PairMargin(1.0, True)] * 40, kind="pairs")
    assert found.threshold is not None and "calls were right 1.000 of the time" in found.note
    none = calibrate([PairMargin(1.0, index % 2 == 0) for index in range(40)], kind="pairs")
    assert none.threshold is None and "right at least 65% of the time" in none.note
    for note in (found.note, none.note):
        assert _accuracy_words(note) == []


def test_other_messages_do_not_call_same_snapshot_numbers_accuracy():
    from aeo.ranksim.lambdamart import _need_more

    assert _accuracy_words(_need_more(1)) == []
    gsc = ImportReport(path="gsc.csv", kept=3, kind="search_console_avg")
    text = "\n".join(gsc.lines())
    assert "Search Console" in text and _accuracy_words(text) == []
