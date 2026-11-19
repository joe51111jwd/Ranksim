"""Round 2, Target 2, Instrument B: do edits move rank 14 to 28 days after the change?

Published split tests see edit effects 2 to 6 weeks after the change (evals/claude/theory.md
section 2). This frozen scorer measures rank levels well before and well after each fetch pair. It
is meant to run from 2026-12-03 on, when the first changes seen by the daily re-fetch (fetch day B on
or after 2026-11-19) have 14 days of later pulls. Positions are real Google positions from Serper.
Direction calls come from the frozen round 1 rules and are labelled PREDICTED.

Rows
  scripts.claude.t2_dataset.build_rows(max_date), usable rows of searches in group o30 or a70
  (the decision population of score_heldout), fetch day B (day_b) on or after 2026-11-19. Events are
  the rows with a material change. Controls are the rows with no material change.

Levels (absent from the consensus top 10 counts as 11)
  pre   mean consensus position over the search's pulled days in [A minus 3 days, A], A = fetch day A
  post  mean consensus position over the search's pulled days in [B plus 14 days, B plus 28 days],
        B = fetch day B, and only days on or before max_date (the loaders return nothing later)
  A row needs at least 2 pulled days on each side, otherwise it is dropped and counted.
  moved  |post minus pre| is at least 1.0. Direction is up when post is lower than pre.
  Start band: rsdata bands of pre rounded half up (1, 2 to 3, 4 to 6, 7 to 10, 11).

Report
  1. Excess movement as in Instrument A (scripts/claude/r2_t2_excess.py): each changed row against
     the mean of the unchanged rows in its stratum, stratum = fetch date pair (day_a>day_b) plus the
     start band of rounded pre. P(moved) and mean |post minus pre|. 95% cluster bootstrap, 2000
     draws, seed 20261119, control means re-estimated in every draw; search, URL and site clusters;
     cuts start 2 to 10, without the client site, material against no change, any change against none.
     Sensitivities: band-only strata, clean controls (no material change of the same URL with a
     fetch day B within 28 days of the row's window), complete post windows only.
  2. Direction on changed-and-moved rows for t2_direction.predict_direction (content-aware) and
     predict_direction_position_only (PREDICTED). Feature mapping: the short-window dataset row is
     passed unchanged except pos_a, which is replaced by pre rounded half up (and in_top10_a, which
     follows it). has_prev and hist_mean_before stay the dataset values (tracked days before the SERP
     day matching A). material, q_title_delta and q_h1_delta stay the A to B page diff. The truth
     label is the long-window direction. Accuracy blocks come from score_heldout._t2_block: the two
     rules, the content rule alone, the frozen default class, the larger class share, and the paired
     difference content-aware minus position-only.
  3. The round 1 pass checks (evals/preregistered/claude-t2-edits/preregistration.md):
     L: n at least 40, content-aware rule lower bound above 0.55 and rate above the larger class share.
     H: paired difference content-aware minus position-only with lower bound above zero.

Guards
  A run with max_date before 2026-12-03 is refused unless --dry-run-design is given. A dry run reads
  design days only (max_date at most 2026-11-17), uses a 2 to 6 day post window and no day_b floor,
  and writes a file labelled CODE CHECK ONLY under evals/claude/r2/t2-excess-design/. It is not a
  result. A real run calls require_frozen_dir on --prereg before it loads anything and refuses to
  overwrite an earlier output unless --force is given.

  python scripts/claude/r2_t2_longwindow.py --prereg evals/preregistered/claude-r2-t2-longwindow --max-date YYYY-MM-DD
  python scripts/claude/r2_t2_longwindow.py --dry-run-design
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import metrics as mt  # noqa: E402
from scripts.claude import r2_t2_excess as ex  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import score_heldout as sh  # noqa: E402
from scripts.claude import t2_dataset as td  # noqa: E402
from scripts.claude.r2_score import require_frozen_dir  # noqa: E402
from scripts.claude.t2_direction import (  # noqa: E402
    content_score,
    load_params,
    predict_direction,
    predict_direction_position_only,
)

DESIGN_MAX_DATE = ex.DESIGN_MAX_DATE
FIRST_RUN_DAY = "2026-12-03"
FIRST_EVENT_DAY_B = "2026-11-19"
PRE_WINDOW = (-3, 0)
POST_WINDOW = (14, 28)
DRY_RUN_POST_WINDOW = (2, 6)
MIN_PULLED_DAYS = 2
MOVE_THRESHOLD = 1.0
CLEAN_CONTROL_DAYS = 28
OUTCOMES = ("moved", "absd")
ABSENT = rs.ABSENT
CODE_CHECK_LABEL = ("CODE CHECK ONLY, NOT A RESULT. Design days through 2026-11-17 with a 2 to 6 day post window, "
                    "run only to prove the code path works.")


def round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def shift(day: str, days: int) -> str:
    return (pd.Timestamp(day) + pd.Timedelta(days=days)).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------------------------
# Inputs and levels


def load_inputs(max_date: str) -> tuple[pd.DataFrame, dict[str, str], pd.DataFrame]:
    """Dataset rows, search groups and the consensus table, all sealed at max_date."""
    rows = td.build_rows(max_date=max_date)
    serp = rs.load_rows(max_date)
    return rows, rs.query_groups(serp), rs.consensus_table(serp)


def select_rows(rows: pd.DataFrame, groups: dict[str, str], dry_run: bool) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    keep = rows["usable"].astype(bool) & rows["query"].map(groups).isin(ex.DECISION_GROUPS)
    if dry_run:
        keep &= rows["serp_day_b"] <= DESIGN_MAX_DATE
    else:
        keep &= rows["day_b"] >= FIRST_EVENT_DAY_B
    out = rows[keep].copy()
    out["group"] = out["query"].map(groups)
    return out.reset_index(drop=True)


def window_level(query: str, key: str, anchor: str, window: tuple[int, int], query_days: dict, positions: dict) -> tuple[float | None, list[str]]:
    """Mean consensus position (absent = 11) over the search's pulled days in [anchor + lo, anchor + hi]."""
    start, end = shift(anchor, window[0]), shift(anchor, window[1])
    days = [day for day in query_days.get(query, []) if start <= day <= end]
    if not days:
        return None, []
    values = [positions.get((query, day, key), ABSENT) for day in days]
    return float(sum(values) / len(values)), days


def add_levels(frame: pd.DataFrame, cons: pd.DataFrame, post_window: tuple[int, int], max_date: str) -> pd.DataFrame:
    data = frame.copy()
    if data.empty:
        return data.assign(**{c: pd.Series(dtype=object) for c in (
            "pre", "n_pre", "pre_days", "post", "n_post", "post_days", "post_window_complete", "has_levels")})
    cons = cons[cons["day"] <= max_date] if len(cons) else cons
    query_days = rs.query_days(cons) if len(cons) else {}
    positions = {(q, d, k): int(c) for q, d, k, c in zip(cons["query"], cons["day"], cons["key"], cons["cons"], strict=True)} if len(cons) else {}
    pre, n_pre, pre_days, post, n_post, post_days = [], [], [], [], [], []
    for query, key, day_a, day_b in zip(data["query"], data["key"], data["day_a"], data["day_b"], strict=True):
        level, days = window_level(query, key, day_a, PRE_WINDOW, query_days, positions)
        pre.append(level)
        n_pre.append(len(days))
        pre_days.append(" ".join(days))
        level, days = window_level(query, key, day_b, post_window, query_days, positions)
        post.append(level)
        n_post.append(len(days))
        post_days.append(" ".join(days))
    data["pre"], data["n_pre"], data["pre_days"] = pre, n_pre, pre_days
    data["post"], data["n_post"], data["post_days"] = post, n_post, post_days
    data["post_window_complete"] = [shift(day_b, post_window[1]) <= max_date for day_b in data["day_b"]]
    data["has_levels"] = (data["n_pre"] >= MIN_PULLED_DAYS) & (data["n_post"] >= MIN_PULLED_DAYS)
    return data


def material_days(rows: pd.DataFrame) -> dict[str, list[str]]:
    """Fetch day B of every usable material change pair, by URL key (any search group)."""
    if rows.empty:
        return {}
    mat = rows[rows["usable"].astype(bool) & rows["material"].astype(bool)].drop_duplicates(["key", "day_a", "day_b"])
    found: dict[str, list[str]] = {}
    for key, day_b in zip(mat["key"], mat["day_b"], strict=True):
        found.setdefault(key, []).append(day_b)
    return found


def prepare(frame: pd.DataFrame, changes: dict[str, list[str]]) -> pd.DataFrame:
    """Long-window outcome, stratum and treatment columns on rows that have both levels."""
    data = frame[frame["has_levels"].astype(bool)].copy() if len(frame) else frame.copy()
    if data.empty:
        return data.assign(**{c: pd.Series(dtype=object) for c in (
            "pre_round", "absd", "moved", "move_long", "band_pre", "date_pair", "stratum", "interior", "anychange",
            "clean_control")})
    data["material"] = data["material"].astype(bool)
    data["pre_round"] = data["pre"].map(round_half_up)
    data["absd"] = (data["post"] - data["pre"]).abs().astype(float)
    data["moved"] = (data["absd"] >= MOVE_THRESHOLD).astype(int)
    data["move_long"] = ["up" if b < a else ("down" if b > a else "none") for a, b in zip(data["pre"], data["post"], strict=True)]
    data["band_pre"] = data["pre_round"].map(ex.band)
    data["date_pair"] = data["day_a"].astype(str) + ">" + data["day_b"].astype(str)
    data["stratum"] = data["date_pair"] + "|" + data["band_pre"]
    data["interior"] = data["pre_round"].between(2, 10)
    data["anychange"] = (data["edit_tokens"] > 0) | data["title_changed"].astype(bool) | data["h1_changed"].astype(bool)

    def clean(row) -> bool:
        if row.material:
            return False
        start, end = shift(row.day_a, -CLEAN_CONTROL_DAYS), shift(row.day_b, CLEAN_CONTROL_DAYS)
        return not any(start <= day <= end for day in changes.get(row.key, ()))

    data["clean_control"] = [clean(row) for row in data.itertuples(index=False)]
    return data.reset_index(drop=True)


# ----------------------------------------------------------------------------------------------
# Direction (PREDICTED)


def mapped_features(row: dict) -> dict:
    """The short-window feature row with pos_a replaced by the rounded pre level."""
    mapped = dict(row)
    mapped["pos_a"] = int(row["pre_round"])
    mapped["in_top10_a"] = mapped["pos_a"] <= 10
    return mapped


def content_rule(row: dict, params: dict) -> str:
    """Same as the content rule inside t2_dataset.build_rows (pos_a is not read)."""
    if bool(row.get("material")):
        score = content_score(row, params)
        if score > 0:
            return "up"
        if score < 0:
            return "down"
    return str(params["default"])


def add_predictions(data: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    params = params or load_params()
    out = data.copy()
    out["pos_a_short"] = out["pos_a"]
    out["move_short"] = out["move"] if "move" in out.columns else None
    records = [mapped_features(record) for record in out.to_dict(orient="records")]
    out["pred_both_rule"] = [predict_direction(record, params) for record in records]
    out["pred_position_rule"] = [predict_direction_position_only(record, params) for record in records]
    out["pred_content_rule"] = [content_rule(record, params) for record in records]
    out["pred_majority"] = str(params["default"])
    out["pred_label"] = "PREDICTED"
    out["pos_a"] = [record["pos_a"] for record in records]
    out["in_top10_a"] = [record["in_top10_a"] for record in records]
    out["move"] = out["move_long"]
    return out


def direction_report(data: pd.DataFrame) -> dict:
    moved = data[data["moved"] == 1] if len(data) else data
    changed = moved[moved["material"].astype(bool)] if len(moved) else moved
    control = moved[~moved["material"].astype(bool)] if len(moved) else moved
    slices = {
        "changed_moved": sh._t2_block(changed),
        "changed_moved_without_client_site": sh._t2_block(changed[changed["site"] != ex.CLIENT_SITE] if len(changed) else changed),
        "changed_moved_top10_at_pre": sh._t2_block(changed[changed["in_top10_a"].astype(bool)] if len(changed) else changed),
        "unchanged_moved": sh._t2_block(control),
        "unchanged_moved_top10_at_pre": sh._t2_block(control[control["in_top10_a"].astype(bool)] if len(control) else control),
    }
    main = slices["changed_moved"]
    enough = main["n"] >= sh.T2_MIN_ROWS
    if main["n"]:
        literal = bool(enough and main["pred_both_rule"]["lo"] > sh.T2_THRESHOLD
                       and main["pred_both_rule"]["rate"] > main["realized_majority_rate"])
        content = bool(enough and main["both_minus_position"]["lo"] is not None and main["both_minus_position"]["lo"] > 0)
        position = bool(enough and main["pred_position_rule"]["lo"] > sh.T2_THRESHOLD
                        and main["pred_position_rule"]["rate"] > main["realized_majority_rate"])
    else:
        literal = content = position = False
    decision = {
        "n": int(main["n"]),
        "n_at_least_40": bool(enough),
        "l_brief_wording": literal,
        "h_content_beats_position_only": content,
        "target2_pass": bool(literal and content),
        "info_position_only_rule_meets_l": position,
    }
    return {"label": "PREDICTED", "slices": slices, "decision": decision}


# ----------------------------------------------------------------------------------------------
# Analysis


def sensitivities(data: pd.DataFrame) -> dict:
    out = {}
    if data.empty:
        return out
    band_only = data.assign(stratum=data["band_pre"])
    out["band_only_strata"] = {o: ex.strat_effect(band_only, "material", o, "stratum", "query") for o in OUTCOMES}
    clean = data[data["material"] | data["clean_control"]]
    out["clean_controls"] = {o: ex.strat_effect(clean, "material", o, "stratum", "query") for o in OUTCOMES}
    complete = data[data["post_window_complete"].astype(bool)]
    out["complete_post_window_only"] = {o: ex.strat_effect(complete, "material", o, "stratum", "query") for o in OUTCOMES}
    return out


def counts(selected: pd.DataFrame, data: pd.DataFrame) -> dict:
    changed = data[data["material"]] if len(data) else data
    sel_changed = selected[selected["material"].astype(bool)] if len(selected) else selected
    return {
        "selected_rows": int(len(selected)),
        "selected_changed_rows": int(len(sel_changed)),
        "dropped_pre_under_2_days": int((selected["n_pre"] < MIN_PULLED_DAYS).sum()) if len(selected) else 0,
        "dropped_post_under_2_days": int((selected["n_post"] < MIN_PULLED_DAYS).sum()) if len(selected) else 0,
        "rows_with_levels": int(len(data)),
        "changed_rows": int(len(changed)),
        "changed_rows_moved": int(changed["moved"].sum()) if len(changed) else 0,
        "control_rows": int(len(data) - len(changed)),
        "clean_control_rows": int(data["clean_control"].sum()) if len(data) else 0,
        "changed_urls": int(changed["key"].nunique()) if len(changed) else 0,
        "changed_sites": int(changed["site"].nunique()) if len(changed) else 0,
        "changed_url_pairs": int(changed.drop_duplicates(["key", "day_a", "day_b"]).shape[0]) if len(changed) else 0,
        "changed_rows_client_site": int((changed["site"] == ex.CLIENT_SITE).sum()) if len(changed) else 0,
        "rows_with_complete_post_window": int(data["post_window_complete"].sum()) if len(data) else 0,
        "queries": int(data["query"].nunique()) if len(data) else 0,
        "fetch_day_b_range": [str(data["day_b"].min()), str(data["day_b"].max())] if len(data) else [],
    }


def analyse(rows: pd.DataFrame, groups: dict[str, str], cons: pd.DataFrame, *, dry_run: bool, max_date: str,
            post_window: tuple[int, int] | None = None, params: dict | None = None) -> tuple[dict, pd.DataFrame]:
    post_window = post_window or (DRY_RUN_POST_WINDOW if dry_run else POST_WINDOW)
    selected = add_levels(select_rows(rows, groups, dry_run), cons, post_window, max_date)
    data = prepare(selected, material_days(rows))
    comparisons = ex.compare_all(data, outcomes=OUTCOMES) if len(data) else {}
    if len(data):
        data = add_predictions(data, params)
    report = {
        "label": (CODE_CHECK_LABEL if dry_run else
                  "Positions are real Google positions from Serper. Direction calls are PREDICTED by the frozen round 1 rules."),
        "instrument": "r2_t2_longwindow (Target 2, Instrument B: long-window edit test)",
        "mode": "dry-run-design (code check)" if dry_run else "held-out",
        "max_date": max_date,
        "selection": {"usable": True, "groups": list(ex.DECISION_GROUPS),
                      "fetch_day_b": (f"any, serp_day_b <= {DESIGN_MAX_DATE}" if dry_run else f">= {FIRST_EVENT_DAY_B}")},
        "method": {
            "pre": f"mean consensus position (absent = {ABSENT}) over pulled days in [day_a{PRE_WINDOW[0]:+d}, day_a{PRE_WINDOW[1]:+d}]",
            "post": f"mean consensus position (absent = {ABSENT}) over pulled days in [day_b+{post_window[0]}, day_b+{post_window[1]}], days <= max_date",
            "min_pulled_days_each_side": MIN_PULLED_DAYS,
            "moved": f"|post - pre| >= {MOVE_THRESHOLD}", "direction": "up when post < pre",
            "stratum": "fetch date pair (day_a>day_b) plus start band of pre rounded half up: 1, 2-3, 4-6, 7-10, 11",
            "clean_control": f"unchanged row whose URL has no usable material pair with day_b in [day_a-{CLEAN_CONTROL_DAYS}, day_b+{CLEAN_CONTROL_DAYS}]",
            "feature_mapping": ("short-window dataset row with pos_a := pre rounded half up and in_top10_a := pos_a <= 10; "
                                "has_prev, hist_mean_before, material, q_title_delta, q_h1_delta unchanged"),
            "bootstrap": {"draws": mt.BOOT_DRAWS, "seed": mt.BOOT_SEED},
        },
        "counts": counts(selected, data),
        "excess_movement": comparisons,
        "excess_sensitivities": sensitivities(data),
        "direction": direction_report(data) if len(data) else {"label": "PREDICTED", "slices": {}, "decision": {
            "n": 0, "n_at_least_40": False, "l_brief_wording": False, "h_content_beats_position_only": False,
            "target2_pass": False, "info_position_only_rule_meets_l": False}},
    }
    return report, data


# ----------------------------------------------------------------------------------------------
# Output

TABLE_ROWS = (
    ("material_vs_not", "moved", ("query", "key", "site")),
    ("material_vs_not", "absd", ("query",)),
    ("material_vs_not_start_2_10", "moved", ("query",)),
    ("material_vs_not_without_client", "moved", ("query",)),
    ("material_vs_no_change", "moved", ("query",)),
    ("any_change_vs_none", "moved", ("query",)),
)


def direction_table(direction: dict) -> list[str]:
    lines = ["| Rows | n | up / down | Larger class | Content-aware rule (PREDICTED) | Position-only rule (PREDICTED) | "
             "Frozen default class (PREDICTED) | Content-aware minus position-only |",
             "| --- | ---: | --- | ---: | --- | --- | --- | --- |"]
    for name, block in direction["slices"].items():
        if not block.get("n"):
            lines.append(f"| {name} | 0 | | | | | | |")
            continue
        lines.append(
            f"| {name} | {block['n']} | {block['up']} / {block['down']} | {block['realized_majority_rate']:.3f} | "
            f"{mt.fmt_rate(block['pred_both_rule'])} | {mt.fmt_rate(block['pred_position_rule'])} | "
            f"{mt.fmt_rate(block['pred_majority'])} | {mt.fmt_diff(block['both_minus_position'])} |")
    return lines


def markdown(report: dict) -> str:
    count = report["counts"]
    dry = report["mode"].startswith("dry")
    lines = [f"# Target 2, Instrument B: long-window edit test, {'CODE CHECK ONLY' if dry else 'held-out rows'} through {report['max_date']}", ""]
    if dry:
        lines += [f"**{CODE_CHECK_LABEL}**", ""]
    lines += [
        "Generated by `scripts/claude/r2_t2_longwindow.py`. " + report["method"]["pre"].capitalize() + "; "
        + report["method"]["post"] + ". Moved when |post - pre| >= 1.0.",
        "",
        f"Rows: {count['selected_rows']} selected, {count['rows_with_levels']} with at least 2 pulled days on each side, "
        f"{count['changed_rows']} changed ({count['changed_rows_moved']} moved) on {count['changed_urls']} URLs and "
        f"{count['changed_sites']} sites, {count['control_rows']} unchanged ({count['clean_control_rows']} clean controls), "
        f"{count['rows_with_complete_post_window']} with a complete post window.",
        "",
        "## Excess movement",
        "",
    ]
    if report["excess_movement"]:
        lines += ex.comparison_table(report["excess_movement"], TABLE_ROWS)
        lines += ["", "| Sensitivity | Outcome | Changed rows matched | Difference [95% CI] |", "| --- | --- | ---: | --- |"]
        for name, block in report["excess_sensitivities"].items():
            for outcome, res in block.items():
                lines.append(f"| {name} | {ex.OUTCOME_NAMES[outcome]} | {res['treated_rows_matched']} of {res['treated_rows']} | "
                             f"{ex.effect_cell(res, 2 if outcome == 'absd' else 3)} |")
    else:
        lines.append("No row has both levels yet.")
    decision = report["direction"]["decision"]
    lines += ["", "## Direction on moved rows (PREDICTED calls, real outcomes)", "", *direction_table(report["direction"]), "",
              f"Round 1 pass checks on changed-and-moved rows: n = {decision['n']}, n at least 40: {decision['n_at_least_40']}, "
              f"L: {decision['l_brief_wording']}, H: {decision['h_content_beats_position_only']}, "
              f"Target 2 pass: {decision['target2_pass']}. Position-only rule alone meets L: {decision['info_position_only_rule_meets_l']}."]
    return "\n".join(lines) + "\n"


ROW_COLUMNS = ["query", "group", "key", "site", "day_a", "day_b", "serp_day_a", "serp_day_b", "pos_a_short", "pos_b", "move_short",
               "pre", "n_pre", "pre_days", "post", "n_post", "post_days", "post_window_complete", "pre_round", "band_pre", "stratum",
               "material", "anychange", "clean_control", "absd", "moved", "move_long", "pred_both_rule", "pred_position_rule",
               "pred_content_rule", "pred_majority", "pred_label"]


def output_paths(prereg: Path | None, dry_run: bool, max_date: str) -> dict[str, Path]:
    if dry_run:
        base, stem = ex.OUT_DESIGN, "longwindow-dry-run-code-check"
    else:
        base, stem = ex.OUT_HELDOUT / prereg.name, f"longwindow-{max_date}"
    return {"json": base / f"{stem}.json", "md": base / f"{stem}.md", "rows": base / f"{stem}-rows.csv"}


def run(max_date: str | None, prereg: Path | None, dry_run: bool, force: bool = False) -> dict:
    if dry_run:
        max_date = max_date or DESIGN_MAX_DATE
        if max_date > DESIGN_MAX_DATE:
            raise SystemExit(f"--dry-run-design reads design days only; max_date must be on or before {DESIGN_MAX_DATE}.")
        manifest = None
        paths = output_paths(None, True, max_date)
        ex.use_design_cache()
    else:
        if prereg is None or max_date is None:
            raise SystemExit("A real run needs --prereg and --max-date.")
        if max_date < FIRST_RUN_DAY:
            raise SystemExit(f"max_date {max_date} is before {FIRST_RUN_DAY}: no change seen by the daily re-fetch is 14 days old. "
                             "Use --dry-run-design for a code check on design days.")
        if not (prereg / "manifest.json").is_file():
            raise SystemExit(f"No manifest.json in {prereg}. Freeze the instrument first.")
        manifest = require_frozen_dir(prereg)  # before anything is loaded
        paths = output_paths(prereg, False, max_date)
        ex.refuse_overwrite(paths, force)

    rows, groups, cons = load_inputs(max_date)
    report, data = analyse(rows, groups, cons, dry_run=dry_run, max_date=max_date)
    if dry_run:
        seal = {"cutoff": DESIGN_MAX_DATE, "max_serp_day_loaded": str(cons["day"].max()),
                "max_fetch_day_b_loaded": str(rows["day_b"].max())}
        assert seal["max_serp_day_loaded"] <= DESIGN_MAX_DATE and seal["max_fetch_day_b_loaded"] <= DESIGN_MAX_DATE
        report["seal"] = seal | ex.design_cache_seal(max_date)
    else:
        report["prereg"] = ex.display(prereg)
        report["manifest_frozen_at"] = manifest.get("frozen_at_utc")

    paths["json"].parent.mkdir(parents=True, exist_ok=True)
    paths["json"].write_text(json.dumps(ex.jsonable(report), indent=2) + "\n", encoding="utf-8")
    paths["md"].write_text(markdown(report), encoding="utf-8")
    data[[c for c in ROW_COLUMNS if c in data.columns]].to_csv(paths["rows"], index=False)
    print(markdown(report))
    print(f"wrote {ex.display(paths['json'])}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--prereg", help="Round 2 preregistration folder (required unless --dry-run-design).")
    parser.add_argument("--max-date", help="Last SERP and fetch day to load (YYYY-MM-DD). Must be 2026-12-03 or later for a real run.")
    parser.add_argument("--dry-run-design", action="store_true", help="Code check on design days with a 2 to 6 day post window. Not a result.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output (identical rerun only).")
    args = parser.parse_args(argv)
    prereg = (ROOT / args.prereg).resolve() if args.prereg else None
    run(args.max_date, prereg, args.dry_run_design, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
