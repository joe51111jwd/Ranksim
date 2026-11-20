"""Shared design-table builder for the T1 placement work. Design days only (<= rsdata.DESIGN_MAX_DATE)."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import rsdata  # noqa: E402
from scripts.claude import t1_placement as t1  # noqa: E402

DESIGN_DAYS = ["2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16", "2026-11-17"]
EMU_SOURCES = ("smartcrew-q100:2026-11-16", "smartcrew-index-deep:2026-11-16:abspos")


@lru_cache(maxsize=1)
def design_rows() -> pd.DataFrame:
    rows = rsdata.load_rows(rsdata.DESIGN_MAX_DATE)
    assert rows["day"].max() <= rsdata.DESIGN_MAX_DATE
    return rows


@lru_cache(maxsize=1)
def groups() -> dict:
    return rsdata.query_groups(design_rows())


def day_table(rows: pd.DataFrame, day: str) -> pd.DataFrame:
    """Placement rows of ``day`` plus features from rows strictly before ``day``."""
    cons = rsdata.consensus_table(rows)
    target = rsdata.placement_rows(rows, cons, day)
    if target.empty:
        return target
    history = rows[rows["day"] < day]
    feats = t1.features(history, target[["query", "key", "url", "site", "day"]])
    table = pd.concat([target, feats], axis=1)
    table["grp"] = table["query"].map(groups())
    # sanity: features recomputed from history alone must match the label layer
    bad = table[(table["klass_f"] != table["klass"]) | (table["dp"] != table["domain_prior"])]
    if len(bad):
        raise AssertionError(f"feature layer disagrees with placement_rows on {len(bad)} rows for {day}")
    return table


@lru_cache(maxsize=1)
def design_table() -> pd.DataFrame:
    rows = design_rows()
    return pd.concat([day_table(rows, day) for day in DESIGN_DAYS], ignore_index=True)


def emulation_rows() -> pd.DataFrame:
    """a70-style history for o30: only the Nov 16 q100 pull and the Nov 16 deep list, then the Nov 17 outcome."""
    rows = design_rows()
    keep_hist = (rows["day"] == "2026-11-16") & rows["source"].isin(EMU_SOURCES)
    keep_out = rows["day"] == "2026-11-17"
    return rows[keep_hist | keep_out].reset_index(drop=True)


@lru_cache(maxsize=1)
def emulation_table() -> pd.DataFrame:
    table = day_table(emulation_rows(), "2026-11-17")
    return table[table["grp"] == "o30"].reset_index(drop=True)
