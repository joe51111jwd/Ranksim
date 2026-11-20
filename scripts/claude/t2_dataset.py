"""Target 2 design dataset: real page-content changes joined to consensus rank moves.

Design data only: SERP rows from rsdata.load_rows(DESIGN_MAX_DATE), page fetches
from rsdata.load_page_index(DESIGN_MAX_DATE) + load_page_html. Nothing after
2026-11-17 is read.

One row per (query, URL key, fetch pair A -> B), where A and B are consecutive
days on which the URL has a usable fetch with the same fetch_method, and the
query has a top-10 pull on both A and B.

Alignment (frozen, see t2_common): a fetch's day is its America/New_York date,
the same convention as the SERP snapshot_date. The SERP day matching fetch day A
is the query's latest pulled day in [A - 1, A]; for fetch day B it is the query's
earliest pulled day in [B, B + 1]. A (query, pair) with no such day is dropped.
On design data every o30 fetch day has a pull on the same day, so both SERP days
equal the fetch days (the tolerance only matters for later held-out fetches). Positions are rsdata consensus positions (the day's voting session;
single-pull days use that pull; absent or flicker = 11).

Row universe (uses only information up to A): the URL appeared in the query's
consensus table on some day <= A. pos_b and move are LABELS. Features use page
content at A and at B (the edit) and SERP history up to and including A.

Outputs in evals/claude/t2-edits/: fetch_pairs.csv, noise_pairs.csv,
dataset.csv, counts.json, rulecheck.md.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import rsdata  # noqa: E402
from scripts.claude.t2_changes import MATERIAL_RULE, RULE_VERSION, diff_extracted, query_features  # noqa: E402
from scripts.claude.t2_common import OUT_DIR, extracted, fetch_table, one_per_day, same_day_pairs  # noqa: E402

ABSENT = rsdata.ABSENT
PAIR_COLUMNS = [
    "usable", "material", "material_reasons", "wc_a", "wc_b", "wc_delta", "wc_log_ratio", "title_changed",
    "title_token_dist", "h1_changed", "headings_a", "headings_b", "headings_added", "headings_removed",
    "headings_dist", "text_shingle_dist", "text_token_dist", "added_tokens", "removed_tokens", "edit_tokens",
    "edit_share", "added_share", "removed_share", "added_blocks", "removed_blocks", "meta_changed",
    "schema_changed", "canonical_changed", "internal_links_a", "internal_links_b", "internal_links_delta",
    "external_links_delta", "updated_changed", "published_changed",
]


def _history(positions: list[tuple[str, int]], day_a: str) -> dict:
    """SERP history features of one (query, key) using days <= day_a only.

    positions: (day, consensus position or 11) for every pulled day of the query from
    the key's first appearance on, in day order.
    """
    upto = [(day, pos) for day, pos in positions if day <= day_a]
    before = [pos for day, pos in upto if day < day_a]
    pos_a = upto[-1][1]
    prev = before[-1] if before else np.nan
    mean_before = float(np.mean(before)) if before else np.nan
    return {
        "pos_a": pos_a,
        "in_top10_a": pos_a <= 10,
        "prev_pos": prev,
        "has_prev": bool(before),
        "hist_mean_before": mean_before,
        "hist_mean_upto": float(np.mean([pos for _day, pos in upto])),
        "hist_best": min(pos for _day, pos in upto),
        "hist_worst": max(pos for _day, pos in upto),
        "days_seen_top10": sum(1 for _day, pos in upto if pos <= 10),
        "days_tracked": len(upto),
        "reversion": (pos_a - mean_before) if before else 0.0,
        "trend": (pos_a - prev) if before else 0.0,
    }


def build_rows(max_date: str = rsdata.DESIGN_MAX_DATE, min_day_a: str | None = None) -> pd.DataFrame:
    """Mechanical entry point for held-out scoring of Target 2.

    Returns one row per (query, URL key, same-method fetch pair A -> B) with the design
    dataset columns (diff features, material flag, history up to A, pos_b/delta/move
    LABELS) plus the frozen PREDICTED columns from t2_direction and params.json:
      pred_position_rule  predict_direction_position_only (no content)
      pred_content_rule   sign of the content score when material and nonzero, else the default class
      pred_both_rule      predict_direction (the frozen predictor)
      pred_majority       the frozen default class ('down')
    Everything dated after max_date is excluded by the sealed loaders (SERP rows and
    page fetches). min_day_a keeps only pairs whose fetch day A is >= min_day_a.
    Score only rows with usable == True and move != 'none'.
    """
    from scripts.claude.t2_direction import content_score, load_params, predict_direction, predict_direction_position_only

    params = load_params()
    data = build(max_date)["data"]
    if min_day_a is not None and len(data):
        data = data[data["day_a"] >= min_day_a].reset_index(drop=True)
    default = str(params["default"])

    def content_rule(row) -> str:
        if bool(row["material"]):
            score = content_score(row, params)
            if score > 0:
                return "up"
            if score < 0:
                return "down"
        return default

    data["pred_position_rule"] = [predict_direction_position_only(row, params) for _, row in data.iterrows()]
    data["pred_content_rule"] = [content_rule(row) for _, row in data.iterrows()]
    data["pred_both_rule"] = [predict_direction(row, params) for _, row in data.iterrows()]
    data["pred_majority"] = default
    data["pred_label"] = "PREDICTED"
    return data


def _quantiles(frame: pd.DataFrame, column: str) -> str:
    qs = [0.5, 0.75, 0.9, 0.95, 0.99, 1.0]
    values = frame[column].astype(float)
    return " | ".join(f"{value:.3g}" for value in values.quantile(qs).tolist())


def _serp_day(days: list[str], fetch_day: str, side: str) -> str | None:
    """Frozen fetch-day -> SERP-day alignment (same day, else 1 day tolerance)."""
    stamp = pd.Timestamp(fetch_day)
    if side == "a":
        window = [day for day in days if stamp - pd.Timedelta(days=1) <= pd.Timestamp(day) <= stamp]
        return window[-1] if window else None
    window = [day for day in days if stamp <= pd.Timestamp(day) <= stamp + pd.Timedelta(days=1)]
    return window[0] if window else None


def build(max_date: str = rsdata.DESIGN_MAX_DATE) -> dict:
    """Design work calls this with the default. A held-out scorer may pass a later
    max_date only after its preregistration is committed."""
    rows = rsdata.load_rows(max_date)
    assert rows["day"].max() <= max_date
    cons = rsdata.consensus_table(rows)
    groups = rsdata.query_groups(rows)
    query_days = rsdata.query_days(cons)
    cons_index = {
        (record.query, record.day, record.key): record
        for record in cons.itertuples(index=False)
    }
    keys_by_query: dict[str, set[str]] = defaultdict(set)
    first_day: dict[tuple[str, str], str] = {}
    for record in cons.itertuples(index=False):
        keys_by_query[record.query].add(record.key)
        pair = (record.query, record.key)
        if pair not in first_day or record.day < first_day[pair]:
            first_day[pair] = record.day
    queries_by_key: dict[str, set[str]] = defaultdict(set)
    for query, keys in keys_by_query.items():
        for key in keys:
            queries_by_key[key].add(query)

    fetches = fetch_table(max_date)
    assert fetches["day"].max() <= max_date
    fetches = fetches[fetches["key"].isin(set(cons["key"]))]
    timed = fetches[~fetches["date_only"]]
    pages = extracted(list(zip(timed["id"].astype(int), timed["url"])))
    words = {pid: page["word_count"] for pid, page in pages.items()}
    picked = one_per_day(timed, words)

    pair_records = []
    for (key, method), group in picked.groupby(["key", "fetch_method"], sort=True):
        group = group.sort_values("day")
        for index in range(len(group) - 1):
            a, b = group.iloc[index], group.iloc[index + 1]
            diff = diff_extracted(pages[int(a["id"])], pages[int(b["id"])])
            record = {"key": key, "url": b["url"], "site": rsdata.site_key(b["url"]), "method": method,
                      "day_a": a["day"], "day_b": b["day"], "id_a": int(a["id"]), "id_b": int(b["id"]),
                      "gap_days": rsdata.calendar_gap(a["day"], b["day"])}
            record.update({column: diff[column] for column in PAIR_COLUMNS})
            pair_records.append(record)
    pairs = pd.DataFrame.from_records(pair_records)

    noise = same_day_pairs(timed)
    noise_records = []
    for item in noise.itertuples(index=False):
        diff = diff_extracted(pages[item.id_a], pages[item.id_b])
        record = item._asdict()
        record.update({column: diff[column] for column in PAIR_COLUMNS})
        noise_records.append(record)
    noise_frame = pd.DataFrame.from_records(noise_records)

    data_records = []
    for pair in pairs.itertuples(index=False):
        for query in sorted(queries_by_key.get(pair.key, ())):
            days = query_days[query]
            serp_a = _serp_day(days, pair.day_a, "a")
            serp_b = _serp_day(days, pair.day_b, "b")
            if serp_a is None or serp_b is None or serp_b <= serp_a:
                continue
            first = first_day[(query, pair.key)]
            if first > serp_a:
                continue
            positions = []
            for day in days:
                if day < first or day > serp_a:
                    continue
                found = cons_index.get((query, day, pair.key))
                positions.append((day, int(found.cons) if found is not None else ABSENT))
            history = _history(positions, serp_a)
            on_a = cons_index.get((query, serp_a, pair.key))
            on_b = cons_index.get((query, serp_b, pair.key))
            pos_b = int(on_b.cons) if on_b is not None else ABSENT
            serp_days_between = [day for day in days if serp_a < day < serp_b]
            row = {
                "query": query,
                "group": groups.get(query, "other"),
                "key": pair.key,
                "url": pair.url,
                "site": pair.site,
                "method": pair.method,
                "day_a": pair.day_a,
                "day_b": pair.day_b,
                "serp_day_a": serp_a,
                "serp_day_b": serp_b,
                "gap_days": pair.gap_days,
                "serp_days_skipped": len(serp_days_between),
                "n_pulls_a": int(on_a.n_pulls) if on_a is not None else 0,
                "n_present_a": int(on_a.n_present) if on_a is not None else 0,
                "spread_a": (int(on_a.pos_max) - int(on_a.pos_min)) if on_a is not None else 0,
                **history,
                **{column: getattr(pair, column) for column in PAIR_COLUMNS},
            }
            row.update(query_features(pages[pair.id_a], pages[pair.id_b], query))
            # LABELS below this line. Never features.
            row["pos_b"] = pos_b
            row["delta"] = history["pos_a"] - pos_b
            row["move"] = "up" if pos_b < history["pos_a"] else ("down" if pos_b > history["pos_a"] else "none")
            data_records.append(row)
    data = pd.DataFrame.from_records(data_records)

    # Selection check: does a fetch on B exist independently of the B outcome?
    sel_records = []
    usable_a = picked[picked["id"].map(lambda pid: words.get(int(pid), 0) >= 50)]
    fetched = {(row.key, row.fetch_method, row.day) for row in picked.itertuples(index=False)}
    fetch_days = sorted(set(picked["day"]))
    for row in usable_a.itertuples(index=False):
        for query in sorted(queries_by_key.get(row.key, ())):
            days = query_days[query]
            if row.day not in days:
                continue
            on_a = cons_index.get((query, row.day, row.key))
            if on_a is None or int(on_a.cons) > 10:
                continue
            later = [day for day in days if day > row.day and day in fetch_days]
            if not later:
                continue
            nxt = later[0]
            on_b = cons_index.get((query, nxt, row.key))
            pos_b = int(on_b.cons) if on_b is not None else ABSENT
            sel_records.append({"query": query, "key": row.key, "day_a": row.day, "day_b": nxt,
                                "pos_a": int(on_a.cons), "pos_b": pos_b,
                                "fetched_b": (row.key, row.fetch_method, nxt) in fetched})
    selection = pd.DataFrame.from_records(sel_records)
    return {"pairs": pairs, "noise": noise_frame, "data": data, "selection": selection, "picked": picked}


def _counts(built: dict) -> dict:
    pairs, data, noise, selection = built["pairs"], built["data"], built["noise"], built["selection"]
    out: dict = {"rule_version": RULE_VERSION, "material_rule": MATERIAL_RULE, "design_max_date": rsdata.DESIGN_MAX_DATE}
    out["fetch_pairs"] = {
        "total": int(len(pairs)),
        "usable": int(pairs["usable"].sum()),
        "material": int(pairs["material"].sum()),
        "by_method": pairs.groupby("method").size().to_dict(),
    }
    per_pair = []
    for (day_a, day_b), group in pairs.groupby(["day_a", "day_b"], sort=True):
        rows = data[(data["day_a"] == day_a) & (data["day_b"] == day_b)]
        mat = rows[rows["usable"] & rows["material"]]
        ctl = rows[rows["usable"] & ~rows["material"]]
        per_pair.append({
            "day_a": day_a, "day_b": day_b,
            "url_pairs": int(len(group)), "usable": int(group["usable"].sum()), "material": int(group["material"].sum()),
            "rows": int(len(rows)), "rows_material": int(len(mat)),
            "rows_material_up": int((mat["move"] == "up").sum()), "rows_material_down": int((mat["move"] == "down").sum()),
            "rows_material_none": int((mat["move"] == "none").sum()),
            "rows_control": int(len(ctl)), "rows_control_up": int((ctl["move"] == "up").sum()),
            "rows_control_down": int((ctl["move"] == "down").sum()), "rows_control_none": int((ctl["move"] == "none").sum()),
        })
    out["per_date_pair"] = per_pair
    usable = data[data["usable"]]
    mat = usable[usable["material"]]
    out["rows"] = {
        "total": int(len(data)),
        "usable": int(len(usable)),
        "material": int(len(mat)),
        "material_moved": int((mat["move"] != "none").sum()),
        "material_up": int((mat["move"] == "up").sum()),
        "material_down": int((mat["move"] == "down").sum()),
        "material_queries": int(mat["query"].nunique()),
        "material_urls": int(mat["key"].nunique()),
        "material_sites": int(mat["site"].nunique()),
        "material_moved_urls": int(mat[mat["move"] != "none"]["key"].nunique()),
        "material_moved_sites": int(mat[mat["move"] != "none"]["site"].nunique()),
        "control": int((~usable["material"]).sum()),
        "control_moved": int(((~usable["material"]) & (usable["move"] != "none")).sum()),
        "groups": data.groupby("group").size().to_dict(),
    }
    out["noise_reference"] = {
        "pairs": int(len(noise)),
        "usable": int(noise["usable"].sum()) if len(noise) else 0,
        "material": int((noise["usable"] & noise["material"]).sum()) if len(noise) else 0,
    }
    if len(selection):
        sel = selection.assign(dropped=selection["pos_b"] > 10)
        out["selection_check"] = {
            "rows_top10_at_a_with_usable_fetch": int(len(sel)),
            "fetched_on_next_day": int(sel["fetched_b"].sum()),
            "fetched_when_still_top10": f"{int(sel[~sel['dropped']]['fetched_b'].sum())}/{int((~sel['dropped']).sum())}",
            "fetched_when_dropped_out": f"{int(sel[sel['dropped']]['fetched_b'].sum())}/{int(sel['dropped'].sum())}",
        }
    return out


def _rulecheck_md(built: dict, counts: dict) -> str:
    pairs, noise = built["pairs"], built["noise"]
    lines = [
        "# Target 2 material-change rule check (design data, days <= 2026-11-17)",
        "",
        f"Rule {RULE_VERSION}: {MATERIAL_RULE}",
        "",
        "Generated by `python -m scripts.claude.t2_dataset`. Pairs are same URL, same fetch_method.",
        "Noise reference = earliest vs latest timed fetch of the same URL on the same ET day, >= 1 hour apart.",
        "",
        "## Flag rates",
        "",
        "| set | pairs | usable | material (usable) | title changed (usable) | H1 changed (usable) | headings changed (usable) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, frame in (("noise reference (same day)", noise), ("consecutive fetch days", pairs)):
        use = frame[frame["usable"]]
        heads = ((use["headings_added"] + use["headings_removed"]) > 0).sum()
        lines.append(
            f"| {name} | {len(frame)} | {len(use)} | {int(use['material'].sum())} | {int(use['title_changed'].sum())} | "
            f"{int(use['h1_changed'].sum())} | {int(heads)} |"
        )
    lines += [
        "",
        "## Distribution on usable pairs (quantiles 0.5 | 0.75 | 0.9 | 0.95 | 0.99 | max)",
        "",
        "| feature | noise reference | consecutive |",
        "| --- | --- | --- |",
    ]
    for column in ("edit_tokens", "edit_share", "text_shingle_dist", "headings_dist", "wc_log_ratio"):
        lines.append(f"| {column} | {_quantiles(noise[noise['usable']], column)} | {_quantiles(pairs[pairs['usable']], column)} |")
    lines += [
        "",
        "## Every usable pair with any text, title or H1 difference (both sides of the threshold)",
        "",
        "| set | url | days | words a->b | edit tokens | edit share | headings +/- | title | H1 | material |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for name, frame in (("noise", noise), ("consecutive", pairs)):
        use = frame[frame["usable"] & ((frame["edit_tokens"] > 0) | frame["title_changed"] | frame["h1_changed"])]
        for row in use.sort_values("edit_tokens").itertuples(index=False):
            lines.append(
                f"| {name} | {row.key} | {row.day_a}->{row.day_b} | {row.wc_a}->{row.wc_b} | {row.edit_tokens} | "
                f"{row.edit_share:.3f} | +{row.headings_added}/-{row.headings_removed} | {'changed' if row.title_changed else ''} | "
                f"{'changed' if row.h1_changed else ''} | {row.material_reasons or 'no'} |"
            )
    unusable = pairs[~pairs["usable"]]
    lines += [
        "",
        f"## Unusable consecutive pairs: {len(unusable)} (one side < 50 main-text words; never material)",
        "",
        "Sites: " + ", ".join(f"{site} ({count})" for site, count in unusable["site"].value_counts().items()),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    built = build()
    counts = _counts(built)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    built["pairs"].to_csv(OUT_DIR / "fetch_pairs.csv", index=False)
    built["noise"].to_csv(OUT_DIR / "noise_pairs.csv", index=False)
    built["data"].to_csv(OUT_DIR / "dataset.csv", index=False)
    built["selection"].to_csv(OUT_DIR / "selection_check.csv", index=False)
    (OUT_DIR / "counts.json").write_text(json.dumps(counts, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (OUT_DIR / "rulecheck.md").write_text(_rulecheck_md(built, counts) + "\n", encoding="utf-8")
    print(json.dumps(counts, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
