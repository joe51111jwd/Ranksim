"""The budget guard: prices, cost arithmetic against the plan's own numbers, the ledger and the sample gate."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aeo.ranksim.forecast import budget
from aeo.ranksim.forecast.budget import (
    PRICES,
    PRICES_READ_ON,
    SAMPLE_MAX_USD,
    BudgetError,
    SpendLedger,
    bulk_traffic_cost,
    historical_serps_cost,
    historical_traffic_cost,
    ranked_keywords_cost,
    serp_cost,
    serp_pages,
    serper_cost,
    to_micros,
)
from aeo.ranksim.forecast.chain import verify_chain


class Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment


def ledger_at(tmp_path, approve: str | None = None) -> SpendLedger:
    ledger = SpendLedger(tmp_path / "spend.jsonl", now=Clock())
    if approve is not None:
        ledger.approve(Decimal(approve), by="James", note="test approval")
    return ledger


# -- prices and arithmetic -----------------------------------------------------------------------


def test_price_table_carries_its_date_and_sources():
    assert PRICES_READ_ON.isoformat() == "2026-11-22"
    assert PRICES["serp_standard_normal_page"].usd == Decimal("0.0006")
    assert PRICES["serp_live_page"].usd == Decimal("0.002")
    for entry in PRICES.values():
        assert entry.source.startswith("https://")
        assert isinstance(entry.usd, Decimal)


def test_serp_pages_round_depth_up_to_the_next_ten():
    assert [serp_pages(depth) for depth in (1, 10, 11, 20, 49, 50, 51)] == [1, 1, 2, 2, 5, 5, 6]
    with pytest.raises(ValueError):
        serp_pages(0)


def test_one_clean_reading_costs_the_plans_figure():
    # Plan, data source 1: 5 pulls of 50 deep at 0.0006 per page of 10 is 0.015 USD.
    assert serp_cost(50, resolve_poisoned_results=False, pulls=5) == Decimal("0.015")
    # With resolve_poisoned_results each task costs one more page: 6 x 0.0006 x 5.
    assert serp_cost(50, resolve_poisoned_results=True, pulls=5) == Decimal("0.018")
    assert serp_cost(50, mode="standard_high", resolve_poisoned_results=False) == Decimal("0.006")
    assert serp_cost(50, mode="live", resolve_poisoned_results=False, pulls=5) == Decimal("0.050")
    with pytest.raises(ValueError):
        serp_cost(50, mode="premium")


def test_labs_costs_match_the_plans_budget_lines():
    assert historical_serps_cost(5000, 24) == Decimal("14.40")
    assert historical_traffic_cost(20_000) == Decimal("26.40")
    assert historical_traffic_cost(1) == Decimal("0.1212")
    assert historical_traffic_cost(1001) == Decimal("0.24") + Decimal("0.0012") * 1001
    assert ranked_keywords_cost(2000, 100) == Decimal("48.00")
    assert ranked_keywords_cost(1, 100) == Decimal("0.024")
    assert bulk_traffic_cost(1000) == Decimal("0.132")
    assert serper_cost(50_000) == Decimal("50.000")


def test_money_never_drifts_like_floats():
    running = 0.0
    for _ in range(10):
        running += 0.1
    assert running != 1.0
    assert sum(to_micros(0.1) for _ in range(10)) == 1_000_000
    assert to_micros(0.0036000000000000003) == 3600
    assert to_micros("0.0000005") == 1  # half up to the micro-dollar
    with pytest.raises(ValueError):
        to_micros(float("nan"))


# -- the ledger --------------------------------------------------------------------------------


def test_fresh_ledger_refuses_every_paid_call_and_says_how_to_approve(tmp_path):
    ledger = ledger_at(tmp_path)
    assert ledger.approved_total == Decimal("0")
    with pytest.raises(BudgetError) as refused:
        ledger.reserve("one SERP pull", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.0036"))
    text = str(refused.value)
    assert ".approve(Decimal(" in text and "by=" in text and str(tmp_path / "spend.jsonl") in text
    assert "python -m aeo.ranksim forecast budget approve --amount <USD> --by <name> --note <why>" in text
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert ledger.spent_total == Decimal("0")


def test_approve_reserve_settle_with_the_providers_cost(tmp_path):
    ledger = ledger_at(tmp_path, approve="5.00")
    first = ledger.reserve("SERP task_post of 5", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.018"))
    assert ledger.spent_total == Decimal("0.018000")  # an open reservation counts at its estimate
    assert ledger.remaining == Decimal("4.982000")
    ledger.settle(first, 0.0175)  # the provider's own cost, as a float from JSON
    assert ledger.spent_total == Decimal("0.017500")
    second = ledger.reserve("never sent", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.01"))
    ledger.release(second)
    third = ledger.reserve("no cost field", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.02"))
    ledger.settle(third, None)  # charged at the estimate
    assert ledger.spent_total == Decimal("0.037500")
    assert ledger.open_reservations() == []
    with pytest.raises(BudgetError, match="already closed"):
        ledger.settle(first, 0.01)
    with pytest.raises(BudgetError, match="does not exist"):
        ledger.release("res-999999")
    ok, head, rows = ledger.verify()
    assert ok and rows == 7 and len(head) == 64


def test_reserve_refuses_more_than_what_remains(tmp_path):
    ledger = ledger_at(tmp_path, approve="0.01")
    ledger.reserve("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.006"))
    with pytest.raises(BudgetError, match="only 0.004000 USD"):
        ledger.reserve("b", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.005"))


def test_approval_rules(tmp_path):
    ledger = ledger_at(tmp_path)
    with pytest.raises(ValueError):
        ledger.approve(0.5, by="James", note="float")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ledger.approve(Decimal("0"), by="James", note="zero")
    with pytest.raises(ValueError):
        ledger.approve(Decimal("1"), by="  ", note="nobody")
    assert ledger.approve("2.50", by="James", note="ok") == Decimal("2.500000")
    with pytest.raises(ValueError):
        ledger.reserve("free call", "x", Decimal("0"))


def test_big_pull_waits_for_its_sample(tmp_path):
    ledger = ledger_at(tmp_path, approve="100")
    with pytest.raises(BudgetError, match="no recorded \\$1 sample"):
        ledger.reserve("20,000 sites", budget.KIND_HISTORICAL_TRAFFIC, Decimal("26.40"))
    sample = ledger.reserve("sample", budget.KIND_HISTORICAL_TRAFFIC, Decimal("0.99"), sample=True)
    ledger.settle(sample, 0.95)
    ledger.record_sample(budget.KIND_HISTORICAL_TRAFFIC, {"sites_asked": 725, "sites_with_data": 12})
    assert ledger.sample_findings(budget.KIND_HISTORICAL_TRAFFIC) == {"sites_asked": 725, "sites_with_data": 12}
    ledger.reserve("20,000 sites", budget.KIND_HISTORICAL_TRAFFIC, Decimal("26.40"))
    # Other kinds still have no sample.
    with pytest.raises(BudgetError):
        ledger.reserve("2,000 sites", budget.KIND_RANKED_KEYWORDS, Decimal("48"))


def test_a_big_pull_split_into_small_calls_still_waits_for_its_sample(tmp_path):
    ledger = ledger_at(tmp_path, approve="100")
    allowed = 0
    with pytest.raises(BudgetError, match="sample cap"):
        for _ in range(100):
            ledger.settle(ledger.reserve("one site", budget.KIND_RANKED_KEYWORDS, Decimal("0.024")), 0.024)
            allowed += 1
    assert allowed == 41  # 41 x 0.024 = 0.984, the 42nd would pass 1.00
    assert ledger.spent_for_kind(budget.KIND_RANKED_KEYWORDS) <= SAMPLE_MAX_USD


def test_released_reservations_do_not_count_toward_the_sample_cap(tmp_path):
    ledger = ledger_at(tmp_path, approve="5")
    for _ in range(3):
        ledger.release(ledger.reserve("unsent", budget.KIND_BULK_TRAFFIC, Decimal("0.9")))
    assert ledger.spent_for_kind(budget.KIND_BULK_TRAFFIC) == Decimal("0")
    ledger.reserve("sent", budget.KIND_BULK_TRAFFIC, Decimal("0.9"))


def test_tampering_with_a_ledger_line_is_detected_and_stops_spending(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    ledger.settle(ledger.reserve("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.5")), 0.5)
    lines = ledger.path.read_text().splitlines()
    record = json.loads(lines[0])
    record["row"]["amount_micros"] = 1_000_000_000  # someone raises the approval by hand
    lines[0] = json.dumps(record)
    ledger.path.write_text("\n".join(lines) + "\n")
    ok, _head, rows = ledger.verify()
    assert ok is False and rows == 0
    with pytest.raises(BudgetError, match="cannot be trusted"):
        ledger.reserve("b", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.1"))
    with pytest.raises(BudgetError):
        _ = ledger.remaining


def test_deleting_a_ledger_line_is_detected(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    reservation = ledger.reserve("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.5"))
    ledger.settle(reservation, 0.5)
    lines = ledger.path.read_text().splitlines()
    ledger.path.write_text("\n".join([lines[0], lines[2]]) + "\n")  # the reservation row is gone
    assert ledger.verify()[0] is False


def test_ledger_rows_chain_with_the_core_row_hash(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    ledger.record_sample("k", {"earliest_snapshot": "2025-12-01", "depth": None})
    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    ok, head, count = verify_chain((row["prev_hash"], row["row_hash"], row["row"]) for row in rows)
    assert ok and count == 2 and head == rows[-1]["row_hash"]
    assert [row["row"]["seq"] for row in rows] == [1, 2]
    assert rows[0]["row"]["at"] == "2026-11-22T12:00:01Z"
    with pytest.raises(ValueError):
        ledger.record_sample("k2", {"share": float("nan")})


def test_two_ledger_objects_on_one_file_agree(tmp_path):
    first = ledger_at(tmp_path, approve="1.00")
    second = SpendLedger(first.path)
    reservation = second.reserve("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.25"))
    assert first.spent_total == Decimal("0.250000")
    first.settle(reservation, 0.2)
    assert second.remaining == Decimal("0.800000")


def test_metered_releases_a_call_that_failed_before_sending(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    with pytest.raises(RuntimeError):
        with ledger.metered("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.1")):
            raise RuntimeError("bad request built")
    assert ledger.spent_total == Decimal("0")
    assert ledger.open_reservations() == []


def test_metered_keeps_the_estimate_when_a_sent_call_broke(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    with pytest.raises(RuntimeError):
        with ledger.metered("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.1")) as meter:
            meter.mark_sent()
            raise RuntimeError("connection reset after sending")
    assert ledger.spent_total == Decimal("0.100000")
    assert ledger.open_reservations() == []


def test_metered_settles_with_the_providers_cost(tmp_path):
    ledger = ledger_at(tmp_path, approve="1.00")
    with ledger.metered("a", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.1")) as meter:
        meter.mark_sent()
        meter.settle(0.003)
    assert ledger.spent_total == Decimal("0.003000")
    with ledger.metered("b", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.1")):
        pass  # nothing sent, nothing settled: released
    assert ledger.spent_total == Decimal("0.003000")


def test_many_small_settlements_sum_exactly(tmp_path):
    ledger = ledger_at(tmp_path, approve="2.00")
    ledger.record_sample(budget.KIND_SERP_STANDARD_NORMAL, {"done": True})
    for _ in range(300):
        ledger.settle(ledger.reserve("p", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.0036")), 0.0036)
    assert ledger.spent_total == Decimal("1.080000")
    assert ledger.remaining == Decimal("0.920000")



def test_past_result_pages_are_reserved_at_one_page_a_month_and_the_sample_rate_is_printed():
    assert budget.HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE == 1
    assert budget.historical_serps_estimate(24, 5000) == Decimal("14.40")  # the plan's own figure, not 43.20
    rate = budget.HISTORICAL_SERPS_PAGES_PER_MONTH_SAMPLES
    assert Decimal("0.37") < rate < Decimal("0.38")  # 53,091 pages / 11,691 searches / 12 months
    measured = budget.historical_serps_sample_rate_estimate(24, 5000)
    assert measured < budget.historical_serps_estimate(24, 5000) and measured == Decimal("5.449423")
    lines = budget.historical_serps_estimate_lines(24, 5000)
    assert "14.400000 USD" in lines[0] and "reserves" in lines[0]
    assert "5.449423 USD" in lines[1] and "53,091 pages" in lines[1]
    assert not any(chr(0x2014) in line or chr(0x2013) in line for line in lines)


def test_serp_cost_applies_the_x5_operator_charge_from_the_billing_formula():
    # Provider map 3.6: base x ceil(depth/10), x5 when the keyword holds a search operator, + base for the repair option.
    assert budget.serp_cost(50, keyword="site:example.com plumber") == Decimal("0.0156")
    assert budget.serp_cost(50, keyword="plumber INTITLE:austin", resolve_poisoned_results=False) == Decimal("0.0150")
    assert budget.serp_cost(50, keyword="plumber austin") == budget.serp_cost(50) == Decimal("0.0036")
    assert budget.serp_cost(50, keyword="plumber -inurl:yelp", pulls=2) == Decimal("0.0312")
    # A colon that is not an operator, or an operator word inside another word, is not charged x5.
    assert not budget.has_search_operator("hours: 24 plumber") and not budget.has_search_operator("website:design")


def test_check_refuses_like_reserve_and_writes_nothing(tmp_path):
    ledger = ledger_at(tmp_path, approve="0.50")
    rows = ledger.state().rows
    ledger.check("fits", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.50"))
    with pytest.raises(BudgetError, match="only 0.500000 USD"):
        ledger.check("too much", budget.KIND_SERP_STANDARD_NORMAL, Decimal("0.54"))
    assert ledger.state().rows == rows


def test_a_failed_sample_is_recorded_but_keeps_the_gate_closed(tmp_path):
    ledger = ledger_at(tmp_path, approve="20.00")
    ledger.record_failed_sample(budget.KIND_HISTORICAL_SERPS, {"snapshots": 0, "earliest_snapshot": None})
    assert not ledger.sample_recorded(budget.KIND_HISTORICAL_SERPS)
    assert ledger.failed_samples(budget.KIND_HISTORICAL_SERPS) == [{"snapshots": 0, "earliest_snapshot": None}]
    with pytest.raises(BudgetError, match="no recorded"):
        ledger.reserve("big pull", budget.KIND_HISTORICAL_SERPS, Decimal("14.40"))
    ledger.record_sample(budget.KIND_HISTORICAL_SERPS, {"snapshots": 12, "earliest_snapshot": "2024-12-01"})
    ledger.reserve("big pull", budget.KIND_HISTORICAL_SERPS, Decimal("14.40"))
    assert ledger.verify()[0]
