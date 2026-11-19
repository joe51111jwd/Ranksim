"""The sealed data layer for the Claude goal experiments. No database, no network."""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from scripts.claude import rsdata as rs


def _db(tmp_path, records):
    path = tmp_path / "raw.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE serp_results (id INTEGER PRIMARY KEY, query TEXT, snapshot_date TEXT, fetched_at_utc TEXT, "
        "gl TEXT, hl TEXT, num INTEGER, position INTEGER, url TEXT, title TEXT, snippet TEXT, raw_json TEXT, source TEXT)"
    )
    con.executemany(
        "INSERT INTO serp_results (query, snapshot_date, fetched_at_utc, position, url, title, source) VALUES (?,?,?,?,?,?,?)",
        records,
    )
    con.commit()
    con.close()
    return path


def _pull(query, day, hour, urls, source="daily"):
    stamp = f"{day}T{hour:02d}:00:00.000Z"
    return [(query, day, stamp, index + 1, f"https://{name}/", name, f"{source}:{day}:{hour}") for index, name in enumerate(urls)]


def test_loader_never_returns_rows_after_the_seal(tmp_path):
    records = _pull("q", "2026-11-17", 14, ["a.com", "b.com"]) + _pull("q", "2026-11-18", 14, ["c.com", "a.com"])
    path = _db(tmp_path, records)
    sealed = rs.load_rows("2026-11-17", db=path)
    assert set(sealed["day"]) == {"2026-11-17"}
    assert "c.com" not in set(sealed["site"])
    opened = rs.load_rows("2026-11-18", db=path)
    assert set(opened["day"]) == {"2026-11-17", "2026-11-18"}
    with pytest.raises(ValueError):
        rs.load_rows("Nov 17", db=path)


def test_consensus_needs_a_strict_majority_of_pulls():
    assert rs.consensus_position([3, 4, 11]) == 4
    assert rs.consensus_position([3, 11, 11]) == 11
    assert rs.consensus_position([1, 11]) == 11
    assert rs.consensus_position([2, 4]) == 3
    assert rs.consensus_position([3, 4, 5, 11]) == 5
    assert rs.consensus_position([7]) == 7
    assert rs.consensus_position([]) == 11


def test_the_larger_same_day_session_votes(tmp_path):
    day = "2026-11-16"
    records = (
        _pull("q", day, 8, ["x.com", "a.com"], source="morning")
        + _pull("q", day, 21, ["a.com", "b.com"], source="e1")
        + _pull("q", day, 22, ["a.com", "b.com"], source="e2")
        + _pull("q", day, 23, ["b.com", "a.com"], source="e3")
    )
    rows = rs.load_rows(day, db=_db(tmp_path, records))
    cons = rs.consensus_table(rows)
    assert set(cons["site"]) == {"a.com", "b.com"}
    assert int(cons["n_pulls"].max()) == 3
    assert dict(zip(cons["site"], cons["cons"], strict=True)) == {"a.com": 1, "b.com": 2}


def test_placement_classes_and_the_domain_prior(tmp_path):
    records = (
        _pull("q", "2026-11-15", 14, ["old.com", "a.com", "b.com"])
        + _pull("q", "2026-11-16", 14, ["a.com", "b.com", "c.com"])
        + [("q", "2026-11-17", "2026-11-17T14:00:00.000Z", 1, "https://a.com/", "a", "d"),
           ("q", "2026-11-17", "2026-11-17T14:00:00.000Z", 2, "https://b.com/fresh", "b2", "d"),
           ("q", "2026-11-17", "2026-11-17T14:00:00.000Z", 3, "https://old.com/", "old", "d"),
           ("q", "2026-11-17", "2026-11-17T14:00:00.000Z", 4, "https://never.com/", "never", "d")]
    )
    rows = rs.load_rows("2026-11-17", db=_db(tmp_path, records))
    cons = rs.consensus_table(rows)
    placed = rs.placement_rows(rows, cons, "2026-11-17").set_index("site")
    by_url = rs.placement_rows(rows, cons, "2026-11-17").set_index("url")
    assert by_url.loc["https://a.com/", "klass"] == "survivor"
    assert by_url.loc["https://b.com/fresh", "klass"] == "new"
    assert by_url.loc["https://b.com/fresh", "domain_prior"] == 2
    assert by_url.loc["https://old.com/", "klass"] == "reentrant"
    assert by_url.loc["https://never.com/", "klass"] == "new"
    assert by_url.loc["https://never.com/", "domain_prior"] == rs.ABSENT
    assert placed.loc["never.com", "prev_day"] == "2026-11-16"


def test_flip_pairs_mark_a_reversed_order(tmp_path):
    records = _pull("q", "2026-11-16", 14, ["a.com", "b.com", "c.com"]) + _pull("q", "2026-11-17", 14, ["b.com", "a.com", "c.com"])
    rows = rs.load_rows("2026-11-17", db=_db(tmp_path, records))
    cons = rs.consensus_table(rows)
    pairs = rs.flip_pairs(cons, "2026-11-16", "2026-11-17")
    assert len(pairs) == 3
    flipped = pairs[pairs["flipped"]]
    assert len(flipped) == 1
    assert flipped.iloc[0]["key_x"].startswith("https://a.com") or "a.com" in flipped.iloc[0]["key_x"]
    assert isinstance(pairs, pd.DataFrame)
