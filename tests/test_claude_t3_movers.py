"""Target 3 scorer A (pull-majority order). Offline, synthetic pulls only."""

from __future__ import annotations

import itertools

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from scripts.claude import t3_movers


def _rows(pulls):
    records = []
    for day, hour, order in pulls:
        for index, name in enumerate(order):
            records.append(("q", day, f"{day}T{hour:02d}:00:00.000Z", index + 1, f"https://{name}/", f"s{hour}"))
    frame = pd.DataFrame(records, columns=["query", "day", "fetched_at", "position", "url", "source"])
    frame["kind"] = "top10"
    frame["title"] = ""
    frame["key"] = frame["url"]
    frame["site"] = frame["url"].str.replace("https://", "").str.split("/").str[0]
    return frame


def _key(name):
    return f"https://{name}/"


def test_history_that_reaches_day_b_is_refused():
    rows = _rows([("2026-11-16", 14, ["a.com", "b.com"]), ("2026-11-17", 14, ["b.com", "a.com"])])
    with pytest.raises(AssertionError):
        t3_movers.score_urls(rows, "q", [_key("a.com")], "2026-11-17")


def test_a_score_does_not_depend_on_which_keys_are_requested():
    pulls = [("2026-11-15", 14, ["a.com", "b.com", "c.com", "d.com"])] + [
        ("2026-11-16", hour, order)
        for hour, order in ((14, ["a.com", "b.com", "c.com", "d.com"]), (15, ["b.com", "a.com", "c.com", "d.com"]), (16, ["b.com", "a.com", "d.com", "c.com"]))
    ]
    rows = _rows(pulls)
    keys = [_key(name) for name in ("a.com", "b.com", "c.com", "d.com", "never.com")]
    together = t3_movers.score_urls(rows, "q", keys, "2026-11-17")
    for key in keys:
        assert t3_movers.score_urls(rows, "q", [key], "2026-11-17")[key] == together[key]
    assert t3_movers.score_urls(rows, "q", list(reversed(keys)), "2026-11-17") == together
    # two of three pulls put b above a, so the pull-majority order overrides the median order only if they disagree
    assert together[_key("never.com")] == 12.0


def test_one_pull_days_are_pure_persistence():
    rows = _rows([("2026-11-15", 14, ["c.com", "b.com", "a.com"]), ("2026-11-16", 14, ["a.com", "b.com", "c.com"])])
    scores = t3_movers.score_urls(rows, "q", [_key("a.com"), _key("b.com"), _key("c.com")], "2026-11-17")
    assert scores[_key("a.com")] < scores[_key("b.com")] < scores[_key("c.com")]


def test_kemeny_search_matches_brute_force_and_keeps_order_past_14_items():
    rng = np.random.default_rng(3)
    for size in (2, 4, 6):
        pref = rng.normal(size=(size, size))
        np.fill_diagonal(pref, 0.0)
        items = [f"k{i}" for i in range(size)]
        best = max(itertools.permutations(range(size)), key=lambda order: sum(pref[order[i], order[j]] for i in range(size) for j in range(i + 1, size)))
        assert t3_movers._kemeny_order(items, pref) == [items[i] for i in best]
    many = [f"k{i}" for i in range(15)]
    assert t3_movers._kemeny_order(many, rng.normal(size=(15, 15))) == many
