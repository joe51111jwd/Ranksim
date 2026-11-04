"""Site history: the PREDICTED slot that won the accuracy lab, and its measured backtest.

Toy REAL result lists built here, plus one check on the committed SmartCrew
Google file (smartcrew/serp.csv). No network.
"""

from __future__ import annotations

from pathlib import Path

from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.site_history import (
    RealList,
    SiteIndex,
    SlotErrors,
    fit_site_history,
    lists_from_pairs,
    site_history_backtest,
    site_history_position,
    site_key,
)

DASHES = ("\u2014", "\u2013")


def toy_lists() -> list[RealList]:
    """4 searches. leader.com is #1 everywhere, steady.com #2, then a few one-off sites."""
    rows = []
    for index, search in enumerate(["dirt work software", "excavation app", "grading costs", "paving crews"]):
        rows.append(
            (
                search,
                [
                    (1, f"https://www.leader.com/{index}"),
                    (2, f"https://steady.com/{index}"),
                    (3, f"https://oneoff{index}.com/a"),
                    (4, f"https://other{index}.com/b"),
                    (5, f"https://late{index}.org/c"),
                ],
            )
        )
    return lists_from_pairs(rows)


def test_site_key_folds_www_and_subdomains():
    assert site_key("https://www.hcss.com/x") == "hcss.com"
    assert site_key("https://learn.g2.com/a") == "g2.com"
    assert site_key("https://shop.example.co.uk/") == "example.co.uk"
    assert site_key("www.smartcrewhq.com") == "smartcrewhq.com"


def test_counts_respect_excluded_searches_and_urls():
    index = SiteIndex(toy_lists())
    assert index.count("leader.com") == 4
    assert index.count("leader.com", frozenset({"excavation app"})) == 3
    # leader.com appears in "dirt work software" only through /0, so excluding that URL drops that search.
    assert index.count("leader.com", frozenset(), "https://www.leader.com/0") == 3
    assert index.count("leader.com", frozenset({"dirt work software"}), "https://www.leader.com/0") == 3
    assert index.count("nobody.com") == 0


def test_fit_needs_two_searches_and_learns_that_recurring_sites_rank_higher():
    lists = toy_lists()
    assert fit_site_history(SiteIndex(lists[:1])) is None
    model = fit_site_history(SiteIndex(lists))
    assert model is not None and model.weight > 0
    assert model.n_searches == 4 and model.n_pairs > 0
    assert model.p_above(3, 0) > 0.5 > model.p_above(0, 3)


def test_position_places_a_new_site_below_a_recurring_one_and_never_invents():
    lists = toy_lists()
    index = SiteIndex(lists)
    model = fit_site_history(index)
    incumbents = [url for _pos, url in sorted(lists[0].results)]
    newcomer = site_history_position(model, index, "https://brand-new.example/page", "dirt work software", incumbents)
    recurring = site_history_position(model, index, "https://steady.com/new", "dirt work software", incumbents)
    assert newcomer is not None and recurring is not None
    assert 1 <= recurring.position < newcomer.position <= newcomer.of == len(incumbents) + 1
    assert newcomer.low <= newcomer.position <= newcomer.high
    assert newcomer.your_site_searches == 0 and recurring.your_site_searches == 3
    assert len(newcomer.p_above) == len(incumbents)
    assert site_history_position(None, index, "https://x.com/", "dirt work software", incumbents) is None
    assert site_history_position(model, index, "https://x.com/", "dirt work software", []) is None


def test_backtest_is_measured_and_plain():
    result = site_history_backtest(SiteIndex(toy_lists()))
    assert result.mae is not None and result.middle_mae is not None
    assert result.n == 20 and result.n_searches == 4
    assert 0.0 <= result.within_2 <= 1.0 and result.band >= 1
    assert result.text.startswith("In backtests this predictor put a hidden page within 2 places of its REAL Google position")
    assert not any(dash in result.text for dash in DASHES)
    empty = site_history_backtest(SiteIndex(toy_lists()[:1]))
    assert empty.mae is None and empty.text.startswith("not run: needs REAL results")


def test_slot_errors_summary():
    found = SlotErrors()
    found.searches = 1
    found.add(1, 1, 4)
    found.add(5, 2, 4, new_site=True)
    summary = found.summary("content model", population="stored REAL results")
    assert summary.mae == 1.5 and summary.exact == 0.5 and summary.within_2 == 0.5
    assert summary.new_site_n == 1 and summary.new_site_mae == 3.0
    assert "the content model put a hidden page within 2 places" in summary.text


def test_beats_the_middle_on_the_committed_smartcrew_file():
    """The lab's finding, re-measured on REAL data: site history beats always guessing the middle."""
    path = Path(__file__).resolve().parents[1] / "smartcrew" / "serp.csv"  # works from any folder
    labels = load_serp_file(path)
    grouped: dict[str, list[tuple[int, str]]] = {}
    for row in labels:
        grouped.setdefault(row.query, []).append((int(round(row.position)), row.url))
    result = site_history_backtest(SiteIndex(lists_from_pairs(grouped.items())))
    assert result.n_searches == 16 and result.n >= 120
    assert result.mae < result.middle_mae
