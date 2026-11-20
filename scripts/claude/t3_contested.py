"""Target 3, scorer B: "a contested pair flips". Written by the lead. PREDICTED.

Rule, with no fitted parameter: for a pair ordered x above y in day_a's
consensus, call a flip when at least one top-10 pull of day_a (voting or not)
showed y strictly above x. A URL missing from a pull counts as 11, and a pull
where both are missing shows no order.

This scorer aims at the brief's literal criterion (more than half of the pairs
that flipped are called). It calls many pairs that do not flip, so it is
expected to be WORSE than persistence on all pairs. It is frozen next to
scorer A (scripts/claude/t3_movers.py) so that both readings are tested.
"""

from __future__ import annotations

import pandas as pd

from scripts.claude import rsdata as rs

NAME = "t3-contested-pairs-v1"


def call_flips(history_rows: pd.DataFrame, query: str, pairs: list[tuple[str, str]], day_b: str) -> list[bool]:
    """One bool per (key_x, key_y) pair, x being the better URL on day_a. History must end before day_b."""
    if len(history_rows) and history_rows["day"].max() >= day_b:
        raise AssertionError("history contains day_b or later")
    mine = history_rows[(history_rows["query"] == query) & (history_rows["kind"] == "top10")]
    if mine.empty:
        return [False] * len(pairs)
    day_a = mine["day"].max()
    pulls = rs.pulls_by_query_day(mine[mine["day"] == day_a]).get((query, day_a), [])
    calls = []
    for key_x, key_y in pairs:
        contested = False
        for pull in pulls:
            rank_x = pull.ranks.get(key_x, rs.ABSENT)
            rank_y = pull.ranks.get(key_y, rs.ABSENT)
            if rank_y < rank_x:
                contested = True
                break
        calls.append(contested)
    return calls
