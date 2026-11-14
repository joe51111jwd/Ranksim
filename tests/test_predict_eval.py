"""The forward scoring rules for the draft predictor: ties, coverage, and score-once."""

from __future__ import annotations

import pytest

from aeo.ranksim import predict_eval


def row(position, *, entrant=False, scorable=True, pos06=None, predicted=None, never_seen=False):
    return {
        "position": position,
        "entrant": entrant,
        "scorable": scorable,
        "pos06": pos06,
        "predicted": predicted,
        "never_seen": never_seen,
    }


def test_ties_count_as_not_correct_and_unscorable_entrants_leave_entrant_slices():
    rows = [
        row(1, pos06=1),
        row(2, entrant=True, predicted=1.5),
        row(3, entrant=True, predicted=1.5),
        row(4, entrant=True, scorable=False),
    ]
    value = lambda r: -r["pos06"] if not r["entrant"] else (-predict_eval.BOTTOM if not r["scorable"] else -r["predicted"])  # noqa: E731
    by_search = {"q": rows}
    new_new = predict_eval._slice(by_search, value, "entrant_vs_entrant")
    assert new_new["pairs"] == 1 and new_new["ties"] == 1 and new_new["correct"] == 0
    new_old = predict_eval._slice(by_search, value, "entrant_vs_returning")
    assert new_old["pairs"] == 2 and new_old["correct"] == 2
    full = predict_eval._slice(by_search, value, "all")
    assert full["pairs"] == 6  # every pair on the later list, unscorable rows included


def test_score_runs_once(tmp_path):
    (tmp_path / "report.json").write_text("{}")
    with pytest.raises(SystemExit):
        predict_eval.score(tmp_path, tmp_path)


def test_site_signals_leave_out_the_page_and_the_search():
    from aeo.ranksim.predict import site_signals

    rows = [("q1", "a.com", "a.com/x", 1.0), ("q2", "a.com", "a.com/x", 2.0), ("q2", "a.com", "a.com/y", 4.0)]
    got = site_signals("a.com", "a.com/x", "q1", rows)
    assert got["site_best"] == pytest.approx(0.7)  # only a.com/y on q2 counts
    assert site_signals("a.com", "a.com/y", "q2", rows)["site_best"] == pytest.approx(1.0)  # a.com/x on q1
    assert site_signals("b.com", "b.com/z", "q1", rows) == {"site_presence": 0.0, "site_best": 0.0}
