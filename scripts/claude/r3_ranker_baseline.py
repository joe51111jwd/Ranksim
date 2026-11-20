"""Round 3 baseline: the current frozen ranker with no rank history ("its content part").

The brief asks that the round 3 content scorer beat "the current ranker's content
part". The current ranker is evals/preregistered/forward-pair-2026-11-16/model-next.json
(monotone LambdaMART, sha256 checked by t2_counterfactual.FrozenScorer). Round 1
measured that 94% of its gain comes from rank-history features.

Here it scores the pages of one site-restricted list as if nothing had ever
ranked: the history is one empty snapshot, so every history feature is at its
"never seen" value, the Google-written SERP title is blank, and every row gets
the same placeholder position (the position is the label in the ranker's own
pipeline and is never read as a feature here). What is left to order two pages
of one site is the ranker's on-page content, URL and off-page inputs; the
off-page inputs are per domain and so identical inside a list.

  scores(table) -> pd.Series aligned to the usable rows of an r3_dataset table.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from aeo.ranksim.pair_order import Snap, SnapRow, build_transition, load_offpage_cache, parse_html  # noqa: E402
from scripts.claude import r3_pages  # noqa: E402
from scripts.claude.t2_counterfactual import FREEZE, FrozenScorer  # noqa: E402

EMPTY_HISTORY_DAY = "2000-01-01"
LIST_DAY = "2026-11-19"


def scores(table: pd.DataFrame) -> pd.Series:
    usable = table[table["usable"]]
    scorer = FrozenScorer(FREEZE / "model-next.json")
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    parsed = r3_pages.load_pages(set(usable["key"]))
    views: dict[str, object] = {}
    with sqlite3.connect(f"file:{r3_pages.DB}?mode=ro", uri=True, timeout=60) as con:
        for key, page in parsed.items():
            row = con.execute("SELECT html FROM pages WHERE id = ?", (int(page["page_id"]),)).fetchone()
            views[key] = parse_html(page["fetch_url"], (row[0] or "") if row else "")
    history = [Snap(day=EMPTY_HISTORY_DAY, rows=[])]
    out = pd.Series(float("nan"), index=usable.index)
    for _list_id, group in usable.groupby("list_id", sort=True):
        rows = [
            SnapRow(query=record.query, url=record.url, position=1.0, title="", url_key=record.key, site=record.site)
            for record in group.itertuples(index=False)
        ]
        built = build_transition(history, Snap(day=LIST_DAY, rows=rows), views, offpage)
        by_key = dict(zip([row.url_key for row in built], scorer(built), strict=True))
        for index, key in zip(group.index, group["key"], strict=True):
            if key in by_key:
                out.loc[index] = by_key[key]
    return out
