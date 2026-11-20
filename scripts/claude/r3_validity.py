"""Round 3 study 1: is the order of a site-restricted search a usable label?

  python scripts/claude/r3_validity.py

Question: when Google is asked ``site:<site> <search>``, does it list that site's
pages in the same order as the ordinary search does? If yes, one credit buys up
to 45 same-site ordered pairs with the site held fixed.

Compared (all REAL positions, nothing predicted):
  A. the site-restricted lists pulled on 2026-11-19 (label validity-a) against
     the ordinary Nov 16 deep list (positions 1 to 30) of the same search;
  B. as a yardstick, two ordinary pulls of the same search about three days apart
     (design days only), on same-site pairs, to show how much two ordinary lists
     disagree at that distance;
  C. two site-restricted pulls of the same list on the same evening
     (labels validity-a and noise-a), to measure label noise.

Design days only for the ordinary lists (rsdata.DESIGN_MAX_DATE). Intervals are
95% bootstraps over lists and, separately, over sites (2000 draws, seed 20261119).
Output: evals/claude/r3/validity/results.json and tables.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.claude import metrics as M  # noqa: E402
from scripts.claude import r3_data  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402

OUT = REPO / "evals" / "claude" / "r3" / "validity"


def rate(frame: pd.DataFrame, col: str = "agree") -> dict:
    if frame.empty:
        return {"n": 0}
    by_list = M.bootstrap_rate(frame, col, query_col="q")
    by_site = M.bootstrap_rate(frame, col, query_col="site")
    return {
        "hits": by_list["hits"],
        "n": by_list["n"],
        "rate": by_list["rate"],
        "ci_list": [by_list["lo"], by_list["hi"]],
        "lists": by_list["queries"],
        "ci_site": [by_site["lo"], by_site["hi"]],
        "sites": by_site["queries"],
    }


def fmt(entry: dict) -> str:
    if not entry.get("n"):
        return "0/0"
    return (
        f"{entry['hits']}/{entry['n']} ({entry['rate']:.3f}) by list [{entry['ci_list'][0]:.3f}, {entry['ci_list'][1]:.3f}] "
        f"({entry['lists']} lists), by site [{entry['ci_site'][0]:.3f}, {entry['ci_site'][1]:.3f}] ({entry['sites']} sites)"
    )


def zone(pa: int, pb: int) -> str:
    hi, lo = min(pa, pb), max(pa, pb)
    if lo <= 10:
        return "both in ordinary top 10"
    if hi <= 10:
        return "one in ordinary top 10"
    return "both in ordinary 11 to 30"


def part_a(site_lists: pd.DataFrame, deep: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    pairs = []
    coverage = []
    for q, group in site_lists.groupby("q", sort=True):
        site, query = group["site"].iloc[0], group["query"].iloc[0]
        spos = dict(zip(group["key"], group["position"], strict=True))
        ordinary = deep[(deep["query"] == query) & (deep["site"] == site)].sort_values("deep_pos")
        items = list(zip(ordinary["key"], ordinary["deep_pos"].astype(int), strict=True))
        best_key = items[0][0] if items else None
        coverage.append(
            {
                "q": q,
                "site": site,
                "ordinary_urls": len(items),
                "found_in_site_list": sum(1 for key, _pos in items if key in spos),
                "best_ordinary_rank_in_site_list": spos.get(best_key) if best_key else None,
                "site_list_len": len(group),
            }
        )
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (ka, pa), (kb, pb) = items[i], items[j]
                if pa == pb:
                    continue
                if ka in spos and kb in spos:
                    pairs.append({"q": q, "site": site, "kind": "both_listed", "agree": float(spos[ka] < spos[kb]), "zone": zone(pa, pb)})
                elif ka in spos or kb in spos:
                    # Only one of the two is in the site list: the site list ranks the listed one higher.
                    pairs.append({"q": q, "site": site, "kind": "one_listed", "agree": float(ka in spos), "zone": zone(pa, pb)})
    pairs = pd.DataFrame.from_records(pairs)
    cov = pd.DataFrame.from_records(coverage)
    with_best = cov[cov["best_ordinary_rank_in_site_list"].notna()]
    result = {
        "lists": int(len(cov)),
        "sites": int(cov["site"].nunique()),
        "site_list_len_hist": {str(k): int(v) for k, v in cov["site_list_len"].value_counts().sort_index().items()},
        "ordinary_same_site_urls": int(cov["ordinary_urls"].sum()),
        "found_in_site_top10": int(cov["found_in_site_list"].sum()),
        "best_ordinary_page_rank_in_site_list": {str(int(k)): int(v) for k, v in with_best["best_ordinary_rank_in_site_list"].value_counts().sort_index().items()},
        "best_ordinary_page_not_listed": int(cov["best_ordinary_rank_in_site_list"].isna().sum()),
        "both_listed": rate(pairs[pairs["kind"] == "both_listed"]) if len(pairs) else {"n": 0},
        "both_listed_by_zone": {name: rate(part) for name, part in pairs[pairs["kind"] == "both_listed"].groupby("zone")} if len(pairs) else {},
        "one_listed": rate(pairs[pairs["kind"] == "one_listed"]) if len(pairs) else {"n": 0},
        "one_listed_by_zone": {name: rate(part) for name, part in pairs[pairs["kind"] == "one_listed"].groupby("zone")} if len(pairs) else {},
    }
    return result, pairs


def part_b(rows: pd.DataFrame) -> dict:
    """Two ordinary single pulls of the same search on different design days, same-site pairs in both top 10s."""
    pulls = rs.pulls_by_query_day(rows)
    by_query: dict[str, list] = {}
    for (query, _day), day_pulls in pulls.items():
        by_query.setdefault(query, []).append(day_pulls[0])  # the first pull of each day: one pull a day
    records = []
    for query, day_pulls in by_query.items():
        day_pulls.sort(key=lambda pull: pull.day)
        for i in range(len(day_pulls)):
            for j in range(i + 1, len(day_pulls)):
                a, b = day_pulls[i], day_pulls[j]
                gap = rs.calendar_gap(a.day, b.day)
                shared = sorted(set(a.ranks) & set(b.ranks))
                for x in range(len(shared)):
                    for y in range(x + 1, len(shared)):
                        kx, ky = shared[x], shared[y]
                        same_site = rs.site_key(a.urls[kx]) == rs.site_key(a.urls[ky])
                        if a.ranks[kx] == a.ranks[ky] or b.ranks[kx] == b.ranks[ky]:
                            continue
                        agree = (a.ranks[kx] < a.ranks[ky]) == (b.ranks[kx] < b.ranks[ky])
                        records.append({"q": query, "site": rs.site_key(a.urls[kx]), "gap": gap, "same_site": same_site, "agree": float(agree)})
    frame = pd.DataFrame.from_records(records)
    out = {}
    for label, low, high in [("gap 1 day", 1, 1), ("gap 2 to 3 days", 2, 3), ("gap 4 to 5 days", 4, 5)]:
        part = frame[(frame["gap"] >= low) & (frame["gap"] <= high)]
        out[label] = {"same_site_pairs": rate(part[part["same_site"]]), "all_pairs": rate(part)}
    return out


def part_c(first: pd.DataFrame, second: pd.DataFrame) -> dict:
    """Same site-restricted list pulled twice: pair order agreement and list overlap."""
    records = []
    overlap = []
    for q, group in second.groupby("q", sort=True):
        other = first[first["q"] == q]
        if other.empty:
            continue
        a = dict(zip(other["key"], other["position"], strict=True))
        b = dict(zip(group["key"], group["position"], strict=True))
        shared = sorted(set(a) & set(b))
        overlap.append({"q": q, "site": group["site"].iloc[0], "first": len(a), "second": len(b), "shared": len(shared), "same_top": float(min(a, key=a.get) == min(b, key=b.get))})
        for x in range(len(shared)):
            for y in range(x + 1, len(shared)):
                kx, ky = shared[x], shared[y]
                records.append(
                    {
                        "q": q,
                        "site": group["site"].iloc[0],
                        "agree": float((a[kx] < a[ky]) == (b[kx] < b[ky])),
                        "gap": abs(a[kx] - a[ky]),
                        "top": float(min(a[kx], a[ky]) == 1),
                    }
                )
    frame = pd.DataFrame.from_records(records)
    over = pd.DataFrame.from_records(overlap)
    if frame.empty:
        return {"lists": 0}
    return {
        "lists": int(len(over)),
        "urls_first": int(over["first"].sum()),
        "urls_second": int(over["second"].sum()),
        "urls_shared": int(over["shared"].sum()),
        "same_first_page": [int(over["same_top"].sum()), int(len(over))],
        "pair_agreement": rate(frame),
        "pair_agreement_by_gap": {
            "gap 1": rate(frame[frame["gap"] == 1]),
            "gap 2": rate(frame[frame["gap"] == 2]),
            "gap 3 to 4": rate(frame[(frame["gap"] >= 3) & (frame["gap"] <= 4)]),
            "gap 5 to 9": rate(frame[frame["gap"] >= 5]),
        },
        "pair_agreement_with_first_page": rate(frame[frame["top"] == 1.0]),
    }


def tables(res: dict) -> str:
    a = res["A"]
    lines = [
        "# Round 3 study 1: site-restricted order against ordinary order (scripts/claude/r3_validity.py)",
        "",
        "REAL positions only. Ordinary lists are design days (through 2026-11-17). Site-restricted lists were pulled on 2026-11-19.",
        "",
        "## A. Site-restricted list (Nov 19) against the ordinary Nov 16 deep list",
        "",
        f"- Lists: {a['lists']} on {a['sites']} sites. Results per list: {a['site_list_len_hist']}.",
        f"- Same-site URLs in the ordinary top 30: {a['ordinary_same_site_urls']}. Found in the site-restricted top 10: {a['found_in_site_top10']}.",
        f"- Where the site's best ordinary page sits in the site-restricted list: {a['best_ordinary_page_rank_in_site_list']}. Not listed: {a['best_ordinary_page_not_listed']}.",
        f"- Pairs with both URLs in both lists, same order: {fmt(a['both_listed'])}.",
    ]
    for name, entry in a["both_listed_by_zone"].items():
        lines.append(f"  - {name}: {fmt(entry)}")
    lines.append(f"- Pairs with one URL in the site-restricted top 10 and the other not: the listed one is the higher one in the ordinary list in {fmt(a['one_listed'])}.")
    for name, entry in a["one_listed_by_zone"].items():
        lines.append(f"  - {name}: {fmt(entry)}")
    lines += ["", "## B. Yardstick: two ordinary pulls of one search on different design days", "", "| Gap | Same-site pairs, same order | All pairs, same order |", "| --- | --- | --- |"]
    for name, entry in res["B"].items():
        lines.append(f"| {name} | {fmt(entry['same_site_pairs'])} | {fmt(entry['all_pairs'])} |")
    c = res.get("C") or {}
    lines += ["", "## C. The same site-restricted list pulled twice on one evening", ""]
    if c.get("lists"):
        lines += [
            f"- Lists: {c['lists']}. URLs: {c['urls_first']} and {c['urls_second']}, shared {c['urls_shared']}.",
            f"- Same first page: {c['same_first_page'][0]} of {c['same_first_page'][1]}.",
            f"- Pair order agreement: {fmt(c['pair_agreement'])}.",
        ]
        for name, entry in c["pair_agreement_by_gap"].items():
            lines.append(f"  - {name}: {fmt(entry)}")
        lines.append(f"  - pairs that include the first page: {fmt(c['pair_agreement_with_first_page'])}")
    else:
        lines.append("- Not pulled yet.")
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = rs.load_rows(rs.DESIGN_MAX_DATE)
    assert rows["day"].max() <= rs.DESIGN_MAX_DATE
    deep = rs.deep_table(rows)
    first = r3_data.load_label("validity-a")
    res_a, pairs = part_a(first, deep)
    res = {"A": res_a, "B": part_b(rows)}
    if r3_data.chunk_paths("noise-a"):
        res["C"] = part_c(first, r3_data.load_label("noise-a"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "tables.md").write_text(tables(res), encoding="utf-8")
    pairs.drop(columns=["q"]).to_csv(OUT / "pairs.csv", index=False)
    print(tables(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
