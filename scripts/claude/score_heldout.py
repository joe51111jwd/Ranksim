"""Score a frozen predictor on a held-out day. Refuses to run before the freeze is committed and pushed.

  python scripts/claude/score_heldout.py t1 --day 2026-11-18
  python scripts/claude/score_heldout.py t3 --day-a 2026-11-17 --day-b 2026-11-18
  python scripts/claude/score_heldout.py t2 --max-date 2026-11-19
  python scripts/claude/score_heldout.py pooled

Each run verifies the sha256 manifest in evals/preregistered/claude-<target>/,
loads SERP rows through the outcome day only, and writes its rows and summary
under evals/claude/heldout/. Model numbers are PREDICTED. Positions are REAL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import heldout as ho  # noqa: E402
from scripts.claude import metrics as m  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude.freeze import verify_manifest  # noqa: E402

OUT = ROOT / "evals" / "claude" / "heldout"
PREREG = {
    "t1": ROOT / "evals/preregistered/claude-t1-placement",
    "t2": ROOT / "evals/preregistered/claude-t2-edits",
    "t3": ROOT / "evals/preregistered/claude-t3-movers",
}
FIRST_HELDOUT_DAY = "2026-11-18"
# The pass decisions use the 100 SmartCrew searches only: the original 30 and the 70 added on Nov 16.
# Other searches in the raw store (Revorion, ClockShark, the PR 41 baseline list) are reported apart.
DECISION_GROUPS = ("o30", "a70")
# Days pulled in this run have three pulls per search. Nov 18 was pulled earlier with two or three.
FIRST_LEAD_PULLED_DAY = "2026-11-19"
LEAD_PULLS_PER_DAY = 3


def pull_counts(cons: pd.DataFrame, groups: dict[str, str], day: str) -> dict:
    """How many pulls voted per decision-group search on a day."""
    part = cons[(cons["day"] == day) & cons["query"].map(groups).isin(DECISION_GROUPS)]
    per_query = part.groupby("query")["n_pulls"].max()
    return {
        "day": day,
        "searches": int(len(per_query)),
        "min_pulls": int(per_query.min()) if len(per_query) else 0,
        "max_pulls": int(per_query.max()) if len(per_query) else 0,
        "by_count": {str(int(key)): int(value) for key, value in per_query.value_counts().sort_index().items()},
    }


def require_complete(counts: dict, allow_incomplete: bool) -> None:
    if counts["day"] >= FIRST_LEAD_PULLED_DAY and counts["min_pulls"] < LEAD_PULLS_PER_DAY and not allow_incomplete:
        raise SystemExit(f"{counts['day']} has a search with fewer than {LEAD_PULLS_PER_DAY} pulls: {counts['by_count']}. Finish the pulls first.")


def refuse_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"{path.name} already exists. A held-out day is scored once. Use --force only to redo an identical run.")


def require_frozen(target: str) -> dict:
    """The manifest must match, be committed, and be on the pushed branch."""
    directory = PREREG[target]
    manifest = verify_manifest(directory)
    relative = str(directory.relative_to(ROOT))
    tracked = [relative] + list(manifest["sha256"])
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *tracked], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit(f"Frozen files have uncommitted changes:\n{dirty}")
    unpushed = subprocess.run(
        ["git", "log", "--oneline", "@{upstream}..HEAD", "--", *tracked], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    if unpushed:
        raise SystemExit(f"The freeze is committed but not pushed:\n{unpushed}")
    return manifest


def check_ahead_t1(frame: pd.DataFrame, day: str, payload: dict | None = None) -> dict:
    """Compare scored Target 1 predictions with the file written before the day was pulled."""
    from scripts.claude import predict_ahead

    payload = payload if payload is not None else predict_ahead.load(day)
    if payload is None:
        return {"ahead_file": False}
    checked = mismatches = 0
    for row in frame.itertuples(index=False):
        block = payload["t1"].get(row.query)
        if block is None or block["prev_day"] != row.prev_day:
            continue
        if row.klass == "new":
            expected = block["new_by_site"].get(row.site, block["new_by_site"][predict_ahead.UNSEEN_SITE])
        else:
            expected = block["reentrant_by_key"].get(row.key)
            if expected is None:
                continue
        checked += 1
        mismatches += int(int(expected) != int(row.pred))
    if mismatches:
        raise SystemExit(f"{mismatches} Target 1 predictions differ from the file written before the pull.")
    return {"ahead_file": True, "checked": checked, "rows": int(len(frame)), "mismatches": 0}


def check_ahead_t3(frame: pd.DataFrame, scorer: str, day_b: str, payload: dict | None = None) -> dict:
    """Compare scored Target 3 flip calls with the file written before the day was pulled."""
    from scripts.claude import predict_ahead

    payload = payload if payload is not None else predict_ahead.load(day_b)
    if payload is None:
        return {"ahead_file": False}
    checked = mismatches = 0
    lookups: dict[str, dict] = {}
    for row in frame.itertuples(index=False):
        block = payload["t3"].get(row.query)
        if block is None or block["day_a"] != row.day_a:
            continue
        if scorer == "A":
            scores = block["scores_a"]
            if row.key_x not in scores or row.key_y not in scores:
                continue
            expected = scores[row.key_y] < scores[row.key_x]
        else:
            if row.query not in lookups:
                lookups[row.query] = {(x, y): bool(flag) for x, y, flag in block["flips_b"]}
            if (row.key_x, row.key_y) not in lookups[row.query]:
                continue
            expected = lookups[row.query][(row.key_x, row.key_y)]
        checked += 1
        mismatches += int(bool(expected) != bool(row.calls_flip))
    if mismatches:
        raise SystemExit(f"{mismatches} Target 3 calls differ from the file written before the pull.")
    return {"ahead_file": True, "checked": checked, "pairs": int(len(frame)), "mismatches": 0}


def _load(day: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    rows = rs.load_rows(day)
    cons = rs.consensus_table(rows)
    return rows, cons, rs.query_groups(rows)


def _t1_slices(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    main = frame[frame["grp"].isin(DECISION_GROUPS)]
    return {
        "strict_new": main[main["strict_new"]],
        "strict_new_original_30": main[main["strict_new"] & (main["grp"] == "o30")],
        "strict_new_added_70": main[main["strict_new"] & (main["grp"] == "a70")],
        "new_no_deep_history": main[(main["klass"] == "new") & ~main["strict_new"]],
        "reentrant": main[main["klass"] == "reentrant"],
        "all_non_survivors": main,
        "other_search_groups_all_non_survivors": frame[~frame["grp"].isin(DECISION_GROUPS)],
    }


def _print_t1(name: str, summary: dict) -> None:
    print(f"  {name}: n={summary['n']} queries={summary['queries']}")
    for metric in ("band3", "err3"):
        block = summary[metric]
        if not summary["n"]:
            continue
        parts = ", ".join(f"{key} {m.fmt_rate(block[key])}" for key in ("pred",) + ho.T1_BASELINES)
        print(f"    {metric}: {parts}")
        print(f"    {metric}: model minus best baseline ({block['best_baseline']}) {m.fmt_diff(block['diff_vs_best'])} pass={block['pass']}")
        for name in ho.T1_REFERENCES:
            print(f"    {metric}: reference {name} {m.fmt_rate(block[name])}, model minus {name} {m.fmt_diff(block[f'diff_vs_{name}'])}")


def score_t1(day: str, force: bool = False, allow_incomplete: bool = False) -> dict:
    if day < FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    manifest = require_frozen("t1")
    refuse_overwrite(OUT / f"t1-{day}.json", force)
    from scripts.claude import t1_placement

    rows, cons, groups = _load(day)
    counts = pull_counts(cons, groups, day)
    require_complete(counts, allow_incomplete)
    deep = ho.deep_queries(rows, day)
    frame = ho.t1_frame(rows, cons, day, t1_placement.predict_rows, deep)
    if frame.empty:
        raise SystemExit(f"No rows to score on {day}.")
    frame["grp"] = frame["query"].map(groups)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT / f"t1-{day}-rows.csv", index=False)
    report = {"label": "PREDICTED", "target": "t1", "day": day, "manifest_frozen_at": manifest.get("frozen_at_utc"), "pulls": counts, "slices": {}}
    report["ahead_check"] = check_ahead_t1(frame, day)
    print(f"T1 held-out day {day} (PREDICTED)")
    print(f"  pulls: {json.dumps(counts)}")
    print(f"  written-before-the-pull check: {json.dumps(report['ahead_check'])}")
    for name, part in _t1_slices(frame).items():
        report["slices"][name] = ho.t1_summary(part)
        _print_t1(name, report["slices"][name])
    (OUT / f"t1-{day}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _print_t3(name: str, summary: dict) -> None:
    print(f"  {name}: pairs={summary['pairs']} flips={summary['flips']} flip calls={summary['flip_calls']}")
    print(f"    flip accuracy {m.fmt_rate(summary['flip_accuracy'])} goal criterion pass={summary['goal_criterion_pass']}")
    print(f"    non-flip accuracy {m.fmt_rate(summary['nonflip_accuracy'])}")
    print(
        f"    all pairs: model {m.fmt_rate(summary['all_pairs_model'])}, persistence {m.fmt_rate(summary['all_pairs_persistence'])}, "
        f"difference {m.fmt_diff(summary['all_pairs_diff'])} guard pass={summary['guard_pass']}"
    )
    if summary["flip_call_precision"]:
        print(f"    flip call precision {m.fmt_rate(summary['flip_call_precision'])}")


T3_SCORERS = ("A", "B")


def _t3_frame(scorer: str, rows: pd.DataFrame, cons: pd.DataFrame, day_a: str, day_b: str) -> pd.DataFrame:
    if scorer == "A":
        from scripts.claude import t3_movers

        return ho.t3_frame(rows, cons, day_a, day_b, t3_movers.score_urls)
    from scripts.claude import t3_contested

    return ho.t3_frame_pairs(rows, cons, day_a, day_b, t3_contested.call_flips)


def score_t3(day_a: str, day_b: str, force: bool = False, allow_incomplete: bool = False) -> dict:
    """Score both frozen scorers: A (pull-majority order) and B (a contested pair flips)."""
    if day_b < FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    if rs.calendar_gap(day_a, day_b) < 1:
        raise SystemExit("day_a must be before day_b.")
    manifest = require_frozen("t3")
    refuse_overwrite(OUT / f"t3-{day_a}_{day_b}.json", force)
    rows, cons, groups = _load(day_b)
    counts_a = pull_counts(cons, groups, day_a)
    counts_b = pull_counts(cons, groups, day_b)
    require_complete(counts_a, allow_incomplete)
    require_complete(counts_b, allow_incomplete)
    # Both scorers read "yesterday" as the search's last pulled day before day_b. Keep only
    # searches for which that day is day_a, so the pair definition and the scorers agree.
    days = rs.query_days(cons)
    aligned = {query for query, listed in days.items() if rs.previous_day(listed, day_b) == day_a}
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "label": "PREDICTED",
        "target": "t3",
        "day_a": day_a,
        "day_b": day_b,
        "gap_days": rs.calendar_gap(day_a, day_b),
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "pulls_day_a": counts_a,
        "pulls_day_b": counts_b,
        "scorers": {},
    }
    print(f"T3 held-out transition {day_a} -> {day_b} (PREDICTED)")
    print(f"  pulls day_a: {json.dumps(counts_a)}")
    print(f"  pulls day_b: {json.dumps(counts_b)}")
    for scorer in T3_SCORERS:
        frame = _t3_frame(scorer, rows, cons, day_a, day_b)
        if frame.empty:
            raise SystemExit(f"No pairs to score on {day_a} -> {day_b}.")
        dropped = int((~frame["query"].isin(aligned)).sum())
        frame = frame[frame["query"].isin(aligned)].reset_index(drop=True)
        frame["grp"] = frame["query"].map(groups)
        frame["scorer"] = scorer
        frame.to_csv(OUT / f"t3{scorer}-{day_a}_{day_b}-pairs.csv", index=False)
        main = frame[frame["grp"].isin(DECISION_GROUPS)]
        slices = {"smartcrew_100": ho.t3_summary(main)}
        slices["pairs_dropped_other_previous_day"] = dropped
        slices["ahead_check"] = check_ahead_t3(frame, scorer, day_b)
        print(f" scorer {scorer}")
        print(f"  written-before-the-pull check: {json.dumps(slices['ahead_check'])}")
        _print_t3("smartcrew_100 (decision population)", slices["smartcrew_100"])
        for group, part in frame.groupby("grp"):
            slices[group] = ho.t3_summary(part)
            _print_t3(group, slices[group])
        report["scorers"][scorer] = slices
    (OUT / f"t3-{day_a}_{day_b}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


T2_MIN_ROWS = 40
T2_THRESHOLD = 0.55
T2_OWN_SITE = "smartcrewhq.com"
T2_METHODS = ("pred_both_rule", "pred_position_rule", "pred_content_rule", "pred_majority")


def _t2_block(part: pd.DataFrame) -> dict:
    """Accuracy of each frozen direction rule on moved rows, with the two paired differences that decide the test."""
    block: dict = {"n": int(len(part)), "queries": int(part["query"].nunique()) if len(part) else 0}
    if part.empty:
        return block
    scored = part.copy()
    for name in T2_METHODS:
        scored[f"hit_{name}"] = (scored[name] == scored["move"]).astype(int)
        block[name] = m.bootstrap_rate(scored, f"hit_{name}")
    ups = int((scored["move"] == "up").sum())
    block["up"] = ups
    block["down"] = int(len(scored) - ups)
    block["realized_majority_rate"] = float(max(ups, len(scored) - ups) / len(scored))
    block["both_minus_position"] = m.bootstrap_difference(scored, "hit_pred_both_rule", "hit_pred_position_rule")
    block["both_minus_majority"] = m.bootstrap_difference(scored, "hit_pred_both_rule", "hit_pred_majority")
    return block


def _changed_placement(material_rows: pd.DataFrame, cons: pd.DataFrame) -> dict:
    """Target 1's "content changed" rows: pages that ranked yesterday and changed materially.

    The prediction is yesterday's position (persistence). It is compared with the brief's
    three baselines. This table is descriptive and never enters a pass decision.
    """
    days = rs.query_days(cons)
    by_query_day = {key: group for key, group in cons.groupby(["query", "day"])}
    records = []
    for row in material_rows.drop_duplicates(["query", "key", "serp_day_b"]).itertuples(index=False):
        prev = rs.previous_day(days.get(row.query, []), row.serp_day_b)
        if prev is None:
            continue
        before = by_query_day[(row.query, prev)]
        before = before[before["cons"] <= 10]
        prev_pos = dict(zip(before["key"], before["cons"], strict=True))
        if row.key not in prev_pos:
            continue
        today = by_query_day.get((row.query, row.serp_day_b))
        truth = rs.ABSENT
        if today is not None:
            found = today[(today["key"] == row.key) & (today["cons"] <= 10)]
            if len(found):
                truth = int(found["cons"].iloc[0])
        same_site = before[before["site"] == row.site]
        preds = {"persistence": int(prev_pos[row.key]), "domain_prior": int(same_site["cons"].min()), "const7": 7, "const11": 11}
        record = {"query": row.query, "key": row.key, "day": row.serp_day_b, "truth": truth}
        for name, value in preds.items():
            record[f"band3_{name}"] = int(m.band3_hit(value, truth))
            record[f"err3_{name}"] = int(m.err3_hit(value, truth))
        records.append(record)
    frame = pd.DataFrame.from_records(records)
    out: dict = {"n": int(len(frame))}
    if frame.empty:
        return out
    for metric in ("band3", "err3"):
        block = {name: m.bootstrap_rate(frame, f"{metric}_{name}") for name in ("persistence", "domain_prior", "const7", "const11")}
        best = max(("domain_prior", "const7", "const11"), key=lambda name: (block[name]["rate"], name))
        block["best_brief_baseline"] = best
        block["persistence_minus_best_brief_baseline"] = m.bootstrap_difference(frame, f"{metric}_persistence", f"{metric}_{best}")
        out[metric] = block
    return out


def score_t2(max_date: str, force: bool = False) -> dict:
    """Edit direction on change rows whose outcome day is held out. One run covers every pair through max_date."""
    if max_date < FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    manifest = require_frozen("t2")
    refuse_overwrite(OUT / f"t2-{max_date}.json", force)
    from scripts.claude import t2_dataset

    rows = t2_dataset.build_rows(max_date=max_date)
    groups = rs.query_groups(rs.load_rows(max_date))
    rows = rows[(rows["serp_day_b"] >= FIRST_HELDOUT_DAY) & rows["query"].map(groups).isin(DECISION_GROUPS)]
    usable = rows[rows["usable"].astype(bool)]
    moved = usable[usable["move"] != "none"]
    material = moved[moved["material"].astype(bool)]
    control = moved[~moved["material"].astype(bool)]
    OUT.mkdir(parents=True, exist_ok=True)
    keep = [name for name in rows.columns if not name.startswith("headings_")]
    rows[keep].to_csv(OUT / f"t2-{max_date}-rows.csv", index=False)
    report = {
        "label": "PREDICTED",
        "target": "t2",
        "max_date": max_date,
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "counts": {
            "rows": int(len(rows)),
            "usable_rows": int(len(usable)),
            "usable_url_pairs": int(usable.drop_duplicates(["key", "day_a", "day_b"]).shape[0]),
            "material_url_pairs": int(usable[usable["material"].astype(bool)].drop_duplicates(["key", "day_a", "day_b"]).shape[0]),
            "material_rows": int(usable["material"].astype(bool).sum()),
            "material_moved_rows": int(len(material)),
            "material_moved_urls": int(material["key"].nunique()),
            "material_moved_sites": int(material["site"].nunique()),
            "control_moved_rows": int(len(control)),
            "date_pairs": sorted({f"{a}->{b}" for a, b in zip(usable["serp_day_a"], usable["serp_day_b"], strict=True)}),
        },
        "slices": {
            "material_moved": _t2_block(material),
            "material_moved_without_client_site": _t2_block(material[material["site"] != T2_OWN_SITE]),
            "material_moved_top10_at_a": _t2_block(material[material["in_top10_a"].astype(bool)]),
            "control_moved": _t2_block(control),
            "control_moved_top10_at_a": _t2_block(control[control["in_top10_a"].astype(bool)]),
        },
    }
    held_cons = rs.consensus_table(rs.load_rows(max_date))
    report["t1_changed_rows_placement"] = _changed_placement(usable[usable["material"].astype(bool)], held_cons)
    main_block = report["slices"]["material_moved"]
    enough = main_block["n"] >= T2_MIN_ROWS
    if main_block["n"]:
        literal = bool(
            enough
            and main_block["pred_both_rule"]["lo"] > T2_THRESHOLD
            and main_block["pred_both_rule"]["rate"] > main_block["realized_majority_rate"]
        )
        content = bool(enough and main_block["both_minus_position"]["lo"] is not None and main_block["both_minus_position"]["lo"] > 0)
    else:
        literal = content = False
    report["decision"] = {
        "n_at_least_40": bool(enough),
        "l_brief_wording": literal,
        "h_content_beats_position_only": content,
        "target2_pass": bool(literal and content),
    }
    print(f"T2 held-out change rows through {max_date} (PREDICTED)")
    print(f"  counts: {json.dumps(report['counts'])}")
    for name, block in report["slices"].items():
        print(f"  {name}: n={block['n']} queries={block['queries']}")
        if not block["n"]:
            continue
        print(f"    up={block['up']} down={block['down']} realized majority rate {block['realized_majority_rate']:.3f}")
        for method in T2_METHODS:
            print(f"    {method}: {m.fmt_rate(block[method])}")
        print(f"    both minus position-only {m.fmt_diff(block['both_minus_position'])}; both minus majority {m.fmt_diff(block['both_minus_majority'])}")
    changed = report["t1_changed_rows_placement"]
    print(f"  Target 1 changed rows (ranked yesterday, changed materially): n={changed['n']}")
    for metric in ("band3", "err3"):
        if metric in changed:
            block = changed[metric]
            parts = ", ".join(f"{name} {m.fmt_rate(block[name])}" for name in ("persistence", "domain_prior", "const7", "const11"))
            print(f"    {metric}: {parts}; persistence minus {block['best_brief_baseline']} {m.fmt_diff(block['persistence_minus_best_brief_baseline'])}")
    print(f"  decision: {json.dumps(report['decision'])}")
    (OUT / f"t2-{max_date}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


T1_POOLED_MIN_ROWS = 40


def t1_decision(frame: pd.DataFrame) -> dict:
    """The preregistered Target 1 pass rule on strict-new rows, band3."""
    strict = frame[frame["strict_new"] & frame["grp"].isin(DECISION_GROUPS)]
    per_day = {}
    for day, part in strict.groupby("day"):
        summary = ho.t1_summary(part)
        per_day[str(day)] = {"n": summary["n"], "queries": summary["queries"], "big_enough": summary["big_enough"], "band3_pass": summary["band3"]["pass"]}
    days_passing = sum(1 for value in per_day.values() if value["band3_pass"])
    pooled_summary = ho.t1_summary(strict)
    pooled_pass = bool(
        frame["day"].nunique() >= 2
        and pooled_summary["n"] >= T1_POOLED_MIN_ROWS
        and pooled_summary["queries"] >= ho.T1_MIN_QUERIES
        and pooled_summary["band3"]["pass"]
    )
    return {
        "per_day": per_day,
        "days_passing": days_passing,
        "per_day_pass": bool(days_passing >= 2),
        "pooled_n": pooled_summary["n"],
        "pooled_pass": pooled_pass,
        "target1_pass": bool(days_passing >= 2 or pooled_pass),
    }


def pooled() -> dict:
    """Pool every scored held-out file. A query keeps its identity across days in the bootstrap."""
    report: dict = {"label": "PREDICTED"}
    t1_files = sorted(OUT.glob("t1-*-rows.csv"))
    if t1_files:
        frame = pd.concat([pd.read_csv(path) for path in t1_files], ignore_index=True)
        report["t1"] = {"days": sorted(frame["day"].unique().tolist()), "slices": {}}
        print(f"T1 pooled over {report['t1']['days']} (PREDICTED)")
        for name, part in _t1_slices(frame).items():
            report["t1"]["slices"][name] = ho.t1_summary(part)
            _print_t1(name, report["t1"]["slices"][name])
        report["t1"]["decision"] = t1_decision(frame)
        print(f"  decision (strict new, band3): {json.dumps(report['t1']['decision'])}")
    for scorer in T3_SCORERS:
        t3_files = sorted(OUT.glob(f"t3{scorer}-*-pairs.csv"))
        if not t3_files:
            continue
        frame = pd.concat([pd.read_csv(path) for path in t3_files], ignore_index=True)
        frame = frame[frame["grp"].isin(DECISION_GROUPS)]
        consecutive = frame[(pd.to_datetime(frame["day_b"]) - pd.to_datetime(frame["day_a"])).dt.days == 1]
        block = {"transitions": sorted({f"{a}->{b}" for a, b in zip(frame["day_a"], frame["day_b"], strict=True)}), "slices": {}}
        print(f"T3 scorer {scorer} pooled over {block['transitions']} (PREDICTED)")
        for name, part in (("consecutive_days", consecutive), ("all_transitions", frame)):
            if len(part):
                block["slices"][name] = ho.t3_summary(part)
                _print_t3(name, block["slices"][name])
        if "consecutive_days" in block["slices"]:
            pooled_summary = block["slices"]["consecutive_days"]
            block["decision"] = {
                "c1_brief_wording": pooled_summary["goal_criterion_pass"],
                "c2_not_below_persistence": pooled_summary["guard_pass"],
                "target3_pass": bool(pooled_summary["goal_criterion_pass"] and pooled_summary["guard_pass"]),
            }
            print(f"  decision: {json.dumps(block['decision'])}")
        report[f"t3{scorer}"] = block
    decided = [report[f"t3{scorer}"]["decision"]["target3_pass"] for scorer in T3_SCORERS if "decision" in report.get(f"t3{scorer}", {})]
    if decided:
        report["t3_decision"] = {"scorers_tested": len(decided), "target3_pass": bool(any(decided))}
        print(f"T3 decision over both scorers: {json.dumps(report['t3_decision'])}")
    (OUT / "pooled.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t1 = sub.add_parser("t1")
    t1.add_argument("--day", required=True)
    t3 = sub.add_parser("t3")
    t3.add_argument("--day-a", required=True)
    t3.add_argument("--day-b", required=True)
    t2 = sub.add_parser("t2")
    t2.add_argument("--max-date", required=True)
    for each in (t1, t2, t3):
        each.add_argument("--force", action="store_true", help="Overwrite an existing scored file (identical rerun only).")
    for each in (t1, t3):
        each.add_argument("--allow-incomplete", action="store_true", help="Score a lead-pulled day with fewer than three pulls. Not for the record.")
    sub.add_parser("pooled")
    args = parser.parse_args(argv)
    if args.command == "t1":
        score_t1(args.day, force=args.force, allow_incomplete=args.allow_incomplete)
    elif args.command == "t2":
        score_t2(args.max_date, force=args.force)
    elif args.command == "t3":
        score_t3(args.day_a, args.day_b, force=args.force, allow_incomplete=args.allow_incomplete)
    else:
        pooled()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
