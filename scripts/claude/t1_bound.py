"""In-sample upper bound for T1 rules on a fixed signal partition (design days only). Diagnostic, optimistic by construction.

For each cell of a partition of the rows by history-only signals, pick the integer that maximizes
hits on the cell's own outcome rows (this is fitting on the test rows, so it is an UPPER BOUND, not a
predictor). If even this cannot beat constant 7 on err3, no rule on these signals can.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import t1_common as c  # noqa: E402


def bucket(value: float) -> str:
    value = float(value)
    return "1-3" if value <= 3 else "4-6" if value <= 6 else "7-10" if value <= 10 else "none"


def cells(frame: pd.DataFrame, klass: str) -> pd.Series:
    if klass == "new":
        return frame["dp"].map(bucket) + "|" + frame["xq_key_min"].map(bucket) + "|ugc=" + frame["host_ugc"].astype(str)
    anchor = frame["deep_key"].where(frame["deep_key"] <= 30, frame["last_top_pos"])
    return anchor.map(lambda v: str(int(v)) if v <= 10 else ("11-30" if v <= 30 else "none")) + "|" + frame["dp"].map(bucket)


def main() -> None:
    design = c.design_table()
    emu = c.emulation_table()
    emu = emu.assign(day="emu")
    for name, table in (("design o30+rev", design), ("emu", emu)):
        for klass in ("new", "reentrant"):
            sub = table[table["klass"] == klass].copy()
            if sub.empty:
                continue
            sub["cell"] = cells(sub, klass)
            out = []
            for metric, fn in (("err3", m.err3_hit), ("band3", m.band3_hit)):
                c7 = sum(fn(7, t) for t in sub["truth"])
                dp = sum(fn(p, t) for p, t in zip(sub["domain_prior"], sub["truth"], strict=True))
                best = 0
                for _, grp in sub.groupby("cell"):
                    best += max(sum(fn(p, t) for t in grp["truth"]) for p in range(1, 12))
                out.append(f"{metric}: in-sample cell optimum {best}/{len(sub)} ({best/len(sub):.3f}) vs c7 {c7} ({c7/len(sub):.3f}) vs dp {dp}")
            print(f"{name} {klass} cells={sub['cell'].nunique()} | " + " | ".join(out))
            # per-day err3 upper bound gain, cell optimum fitted on that day's own rows
            for day, grp_day in sub.groupby("day"):
                grp_day = grp_day.copy()
                choice = {}
                for cell, g in grp_day.groupby("cell"):
                    scores = {p: sum(m.err3_hit(p, t) for t in g["truth"]) for p in range(1, 12)}
                    choice[cell] = max(sorted(scores, key=lambda p: abs(p - 7)), key=lambda p: scores[p])
                grp_day["ub"] = [int(m.err3_hit(choice[cl], t)) for cl, t in zip(grp_day["cell"], grp_day["truth"], strict=True)]
                grp_day["c7"] = [int(m.err3_hit(7, t)) for t in grp_day["truth"]]
                diff = m.bootstrap_difference(grp_day, "ub", "c7")
                print(f"   {day} n={len(grp_day)} err3 in-sample optimum {grp_day['ub'].sum()} vs c7 {grp_day['c7'].sum()} diff {m.fmt_diff(diff)}")


if __name__ == "__main__":
    main()
