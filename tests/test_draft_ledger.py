"""Draft ledger hashing and the list of searches that still need a baseline."""

from aeo.ranksim.draft_ledger import missing_queries, prediction_sha256, score_dates, text_sha256
from datetime import date


def test_missing_queries_ignores_hyphens_and_keeps_the_original_wording():
    tracked = ["workyard alternative", "all in one dirt work software"]
    missing = missing_queries(
        ["workyard alternative", "Workyard", "all-in-one dirt work software", "construction management software"],
        tracked,
    )
    assert missing == ["Workyard", "construction management software"]


def test_prediction_hash_changes_when_the_position_changes():
    left = prediction_sha256(
        source_head="cbb50da",
        model_sha256="abc",
        url="https://www.smartcrewhq.com/compare/workyard-alternative/",
        query="workyard alternative",
        position=4,
        low=2,
        high=4,
        text_sha256="text",
    )
    right = prediction_sha256(
        source_head="cbb50da",
        model_sha256="abc",
        url="https://www.smartcrewhq.com/compare/workyard-alternative/",
        query="workyard alternative",
        position=5,
        low=3,
        high=5,
        text_sha256="text",
    )
    assert left != right
    assert text_sha256("T", "H", "heads", "body") == text_sha256("T", "H", "heads", "body")


def test_score_dates_are_seven_fourteen_and_twenty_eight_days_after_publish():
    found = score_dates(date(2026, 11, 20))
    assert found == {"plus_7": "2026-11-27", "plus_14": "2026-12-04", "plus_28": "2026-12-18"}
