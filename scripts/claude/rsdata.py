"""Sealed data layer for the Claude goal experiments (GOAL.md, Nov 18 2026).

Every loader takes ``max_date`` and never returns a SERP row or a page fetch
dated after it. Dates are the America/New_York ``snapshot_date`` already stored
in ``ranksim_raw.sqlite``. Design work passes the design cutoff. A held-out day
is only loaded by a scoring script after its preregistration is committed.

Positions are REAL Google positions from Serper. Nothing here predicts.
"""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aeo.ranksim.consensus import median_position
from aeo.ranksim.site_history import site_key, url_key

DB = Path(os.environ.get("RANKSIM_RAW_DB", "/workspace/ranksim-data/ranksim_raw.sqlite"))
ABSENT = 11
# The last day any design choice may read. Held-out days start the day after.
DESIGN_MAX_DATE = "2026-11-17"

# Page-relative copy of the deep pull (positions restart at 1 on each page). Never used.
_SKIP_SOURCES = ("smartcrew-index-deep:2026-11-16",)
_DEEP_SUFFIX = ":abspos"
# A different query list pulled once for keyword research. Not part of any day series.
_SKIP_PREFIXES = ("keyword-sample:",)


def connect(db: Path | str = DB) -> sqlite3.Connection:
    """Read-only connection. The raw store is append-only and is never written from here."""
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _check_date(max_date: str) -> str:
    text = str(max_date)
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ValueError(f"max_date must be YYYY-MM-DD, got {max_date!r}")
    return text


def load_rows(max_date: str, db: Path | str = DB) -> pd.DataFrame:
    """All SERP rows with snapshot_date <= max_date.

    Columns: query, day, fetched_at, position, url, key, site, source, kind, title.
    kind is 'top10' for an ordinary top-10 pull and 'deep' for the paged deep
    list (absolute positions 1-30). A pull is one (query, source, fetched_at).
    """
    max_date = _check_date(max_date)
    with connect(db) as con:
        frame = pd.read_sql_query(
            "SELECT query, snapshot_date AS day, fetched_at_utc AS fetched_at, position, url, title, source "
            "FROM serp_results WHERE snapshot_date <= ? AND url IS NOT NULL AND position IS NOT NULL",
            con,
            params=(max_date,),
        )
    frame = frame[~frame["source"].isin(_SKIP_SOURCES)]
    frame = frame[~frame["source"].str.startswith(_SKIP_PREFIXES)]
    frame = frame.copy()
    frame["position"] = frame["position"].astype(int)
    frame["kind"] = frame["source"].map(lambda value: "deep" if value.endswith(_DEEP_SUFFIX) else "top10")
    frame["key"] = frame["url"].map(url_key)
    frame["site"] = frame["url"].map(site_key)
    frame = frame[(frame["kind"] == "deep") | (frame["position"] <= 10)]
    if len(frame) and frame["day"].max() > max_date:
        raise AssertionError("sealed loader returned a row after max_date")
    return frame.sort_values(["day", "query", "fetched_at", "position"]).reset_index(drop=True)


@dataclass(frozen=True)
class Pull:
    query: str
    day: str
    source: str
    fetched_at: str
    ranks: dict  # key -> position (best position if a key repeats)
    urls: dict  # key -> display url


def pulls_by_query_day(rows: pd.DataFrame) -> dict[tuple[str, str], list[Pull]]:
    """Top-10 pulls for each (query, day), oldest first."""
    found: dict[tuple[str, str], list[Pull]] = defaultdict(list)
    top = rows[rows["kind"] == "top10"]
    for (query, day, source, fetched_at), group in top.groupby(["query", "day", "source", "fetched_at"], sort=True):
        ranks: dict[str, int] = {}
        urls: dict[str, str] = {}
        for key, url, position in zip(group["key"], group["url"], group["position"], strict=True):
            if key not in ranks or position < ranks[key]:
                ranks[key] = int(position)
                urls[key] = url
        found[(query, day)].append(Pull(query, day, source, fetched_at, ranks, urls))
    for pulls in found.values():
        pulls.sort(key=lambda pull: (pull.fetched_at, pull.source))
    return dict(found)


def consensus_position(ranks_with_absent: list[int]) -> int:
    """Median over a day's pulls, absent counted as 11. Top-10 needs a strict majority of pulls.

    For three pulls this equals the existing consensus rule (median, absent = 11)
    whenever the result is 10 or better. A URL shown in half or fewer of the
    day's pulls is a flicker and gets 11.
    """
    count = len(ranks_with_absent)
    present = sum(1 for value in ranks_with_absent if value < ABSENT)
    if count == 0 or present * 2 <= count:
        return ABSENT
    return min(median_position(list(ranks_with_absent)), ABSENT)


SESSION_HOURS = 3.0


def consensus_session(pulls: list[Pull]) -> list[Pull]:
    """The pulls that vote in a day's consensus.

    A session is a run of pulls that start within SESSION_HOURS of the run's
    first pull. The session with the most pulls votes. A tie goes to the later
    session. On Nov 16 this picks the three evening pulls (16:26, 16:57, 17:29 ET)
    over the single morning pull, which matches the earlier consensus work.
    """
    if not pulls:
        return []
    ordered = sorted(pulls, key=lambda pull: (pull.fetched_at, pull.source))
    sessions: list[list[Pull]] = []
    for pull in ordered:
        start = pd.Timestamp(sessions[-1][0].fetched_at) if sessions else None
        if start is not None and (pd.Timestamp(pull.fetched_at) - start).total_seconds() <= SESSION_HOURS * 3600:
            sessions[-1].append(pull)
        else:
            sessions.append([pull])
    best = sessions[0]
    for session in sessions[1:]:
        if len(session) >= len(best):
            best = session
    return best


def consensus_table(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (query, day, key) seen in a voting top-10 pull that day.

    Columns: query, day, key, url, site, cons (1-10, or 11 for a flicker),
    n_pulls, n_present, pos_min, pos_max, positions (tuple, absent = 11).
    Only the day's consensus session votes (see consensus_session).
    """
    records = []
    for (query, day), day_pulls in pulls_by_query_day(rows).items():
        pulls = consensus_session(day_pulls)
        order: list[str] = []
        display: dict[str, str] = {}
        for pull in pulls:
            for key, url in pull.urls.items():
                if key not in display:
                    display[key] = url
                    order.append(key)
        for key in order:
            ranks = [pull.ranks.get(key, ABSENT) for pull in pulls]
            shown = [value for value in ranks if value < ABSENT]
            records.append(
                {
                    "query": query,
                    "day": day,
                    "key": key,
                    "url": display[key],
                    "site": site_key(display[key]),
                    "cons": consensus_position(ranks),
                    "n_pulls": len(pulls),
                    "n_present": len(shown),
                    "pos_min": min(shown),
                    "pos_max": max(shown),
                    "positions": tuple(ranks),
                }
            )
    return pd.DataFrame.from_records(records)


def deep_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Deep list rows: query, day, key, site, deep_pos (absolute, best if repeated)."""
    deep = rows[rows["kind"] == "deep"]
    if deep.empty:
        return pd.DataFrame(columns=["query", "day", "key", "site", "deep_pos"])
    best = deep.groupby(["query", "day", "key"], as_index=False).agg(deep_pos=("position", "min"), site=("site", "first"))
    return best[["query", "day", "key", "site", "deep_pos"]]


def query_days(cons: pd.DataFrame) -> dict[str, list[str]]:
    """Sorted days on which each query has at least one top-10 pull."""
    found: dict[str, set[str]] = defaultdict(set)
    for query, day in zip(cons["query"], cons["day"], strict=True):
        found[query].add(day)
    return {query: sorted(days) for query, days in found.items()}


def previous_day(days: list[str], day: str) -> str | None:
    earlier = [value for value in days if value < day]
    return earlier[-1] if earlier else None


def calendar_gap(day_a: str, day_b: str) -> int:
    return (pd.Timestamp(day_b) - pd.Timestamp(day_a)).days


def first_seen(rows: pd.DataFrame) -> dict[tuple[str, str], str]:
    """First day each (query, key) appeared in any stored list, top-10 or deep."""
    return rows.groupby(["query", "key"])["day"].min().to_dict()


def site_first_seen(rows: pd.DataFrame) -> dict[tuple[str, str], str]:
    return rows.groupby(["query", "site"])["day"].min().to_dict()


def placement_rows(rows: pd.DataFrame, cons: pd.DataFrame, outcome_day: str) -> pd.DataFrame:
    """Top-10 consensus rows of ``outcome_day`` with their class and the three fixed baselines.

    klass:
      survivor   consensus top 10 on the query's previous pulled day
      new        (query, URL) never seen in any stored list before outcome_day
      reentrant  seen before, but not in the previous day's consensus top 10

    domain_prior is the best consensus position of the same site on the same
    query on the previous pulled day, 11 if the site was not in that top 10.
    This is the baseline definition frozen in evals/preregistered/hybrid-2026-11-19.

    ``rows`` and ``cons`` may contain the outcome day. Only the truth column
    ``cons`` and the class label read it. Every feature a model uses must come
    from days strictly before outcome_day.
    """
    days = query_days(cons)
    seen = first_seen(rows)
    today = cons[(cons["day"] == outcome_day) & (cons["cons"] <= 10)]
    by_query_day = {key: group for key, group in cons.groupby(["query", "day"])}
    out = []
    for record in today.itertuples(index=False):
        prev = previous_day(days[record.query], outcome_day)
        if prev is None:
            continue
        before = by_query_day[(record.query, prev)]
        before_top = before[before["cons"] <= 10]
        prev_pos = dict(zip(before_top["key"], before_top["cons"], strict=True))
        same_site = before_top[before_top["site"] == record.site]
        domain_prior = int(same_site["cons"].min()) if len(same_site) else ABSENT
        first = seen.get((record.query, record.key))
        if record.key in prev_pos:
            klass = "survivor"
        elif first is None or first >= outcome_day:
            klass = "new"
        else:
            klass = "reentrant"
        out.append(
            {
                "query": record.query,
                "day": outcome_day,
                "prev_day": prev,
                "gap_days": calendar_gap(prev, outcome_day),
                "key": record.key,
                "url": record.url,
                "site": record.site,
                "truth": int(record.cons),
                "klass": klass,
                "prev_pos": prev_pos.get(record.key, ABSENT),
                "domain_prior": domain_prior,
                "n_pulls": int(record.n_pulls),
            }
        )
    return pd.DataFrame.from_records(out)


def flip_pairs(cons: pd.DataFrame, day_a: str, day_b: str) -> pd.DataFrame:
    """URL pairs in the consensus top 10 on both days, strictly ordered on both days.

    Columns: query, key_x, key_y, a_x, a_y, b_x, b_y, flipped.
    key_x is the URL ranked better on day_a. flipped is True when day_b reverses that order.
    Pairs tied on either day are left out. Only day_b positions and ``flipped`` are outcomes.
    """
    a = cons[(cons["day"] == day_a) & (cons["cons"] <= 10)]
    b = cons[(cons["day"] == day_b) & (cons["cons"] <= 10)]
    b_pos = {(query, key): int(value) for query, key, value in zip(b["query"], b["key"], b["cons"], strict=True)}
    out = []
    for query, group in a.groupby("query"):
        items = [(key, int(value)) for key, value in zip(group["key"], group["cons"], strict=True) if (query, key) in b_pos]
        items.sort(key=lambda item: (item[1], item[0]))
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (key_x, a_x), (key_y, a_y) = items[i], items[j]
                if a_x == a_y:
                    continue
                b_x, b_y = b_pos[(query, key_x)], b_pos[(query, key_y)]
                if b_x == b_y:
                    continue
                out.append(
                    {
                        "query": query,
                        "day_a": day_a,
                        "day_b": day_b,
                        "key_x": key_x,
                        "key_y": key_y,
                        "a_x": a_x,
                        "a_y": a_y,
                        "b_x": b_x,
                        "b_y": b_y,
                        "flipped": b_x > b_y,
                    }
                )
    return pd.DataFrame.from_records(out)


def load_page_index(max_date: str, db: Path | str = DB) -> pd.DataFrame:
    """Page fetch records (no HTML) with a fetch date <= max_date.

    Columns: id, url, key, fetched_at, fetch_day, fetch_method, http_status, sha256, html_len.
    fetch_day is the UTC date of the fetch. A fetch late in the US evening can
    carry the next UTC date, so the seal compares the UTC date to max_date + 1 day.
    """
    max_date = _check_date(max_date)
    limit = (pd.Timestamp(max_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d") + "T05:00:00"
    with connect(db) as con:
        frame = pd.read_sql_query(
            "SELECT id, url, fetched_at_utc AS fetched_at, fetch_method, http_status, sha256, "
            "length(html) AS html_len FROM pages WHERE fetched_at_utc < ? AND fetch_method NOT LIKE 'wayback%'",
            con,
            params=(limit,),
        )
    frame["key"] = frame["url"].map(url_key)
    frame["fetch_day"] = frame["fetched_at"].str.slice(0, 10)
    return frame


def load_page_html(page_id: int, db: Path | str = DB) -> str:
    with connect(db) as con:
        row = con.execute("SELECT html FROM pages WHERE id = ?", (int(page_id),)).fetchone()
    return (row[0] or "") if row else ""


def query_groups(rows: pd.DataFrame) -> dict[str, str]:
    """Query set label from the source that first pulled the query.

    o30   the original 30 SmartCrew searches (pulled since Nov 6)
    a70   the 70 searches added on Nov 16
    p35   the PR 41 baseline searches (one pull on Nov 16)
    rev   the Revorion searches (a different niche)
    other anything else (for example the ClockShark searches)
    """
    labels: dict[str, str] = {}
    sources: dict[str, set[str]] = defaultdict(set)
    for query, source in zip(rows["query"], rows["source"], strict=True):
        sources[query].add(source)
    for query, found in sources.items():
        if any(value.startswith("revorion-snapshot:") for value in found):
            labels[query] = "rev"
        elif any(value.startswith("smartcrew-snapshot:") or value.startswith("smartcrew-noise-") for value in found):
            labels[query] = "o30"
        elif any(value.startswith("smartcrew-q100:") for value in found):
            labels[query] = "a70"
        elif "daily:2026-11-16" in found:
            labels[query] = "p35"
        else:
            labels[query] = "other"
    return labels
