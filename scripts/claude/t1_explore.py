"""Exploration printouts for T1 (design days only). Descriptive, not a score."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import t1_common as c  # noqa: E402

pd.set_option("display.width", 220)


def main() -> None:
    table = c.design_table()
    emu = c.emulation_table()
    print("design rows", len(table), "emulation rows", len(emu))
    print(table.groupby(["day", "grp", "klass"]).size().unstack(fill_value=0))
    print(emu.groupby(["klass"]).size())
    for klass in ["new", "reentrant"]:
        sub = table[table["klass"] == klass]
        print(f"\n===== {klass} =====")
        for col in ["dp", "dp_n", "any_site_prev", "deep_site", "deep_key", "xq_key_min", "xq_site_min", "is_home", "host_ugc",
                    "host_video", "host_review", "last_top_pos", "any_key_prev", "site_seen_q"]:
            if col not in sub:
                continue
            x = sub.copy()
            if x[col].dtype != bool:
                x[col] = pd.cut(x[col].astype(float), [0, 3, 6, 10, 11, 20, 30, 1000], labels=["1-3", "4-6", "7-10", "11", "12-20", "21-30", "none"])
            t = x.groupby(col, observed=True)["truth"].agg(["size", "mean", "median"])
            t["share_top3"] = x.groupby(col, observed=True)["truth"].apply(lambda v: (v <= 3).mean())
            t["share_8_10"] = x.groupby(col, observed=True)["truth"].apply(lambda v: (v >= 8).mean())
            print(f"-- {col}\n{t.round(2)}")
    print("\n===== emulation (o30 Nov 17, q100+deep history) =====")
    for klass in ["new", "reentrant", "survivor"]:
        sub = emu[emu["klass"] == klass]
        print(klass, len(sub), "truth", sub["truth"].value_counts().sort_index().to_dict())
        if klass != "survivor":
            print(" deep_key", sub["deep_key"].value_counts().sort_index().to_dict())
            print(" deep_site", sub["deep_site"].value_counts().sort_index().to_dict())
            print(" dp", sub["dp"].value_counts().sort_index().to_dict())


if __name__ == "__main__":
    main()
