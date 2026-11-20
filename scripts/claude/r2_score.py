"""Score a frozen ROUND 2 predictor on a held-out day. PREDICTED.

  python scripts/claude/r2_score.py t1 --prereg evals/preregistered/claude-r2-<name> --day 2026-11-19
  python scripts/claude/r2_score.py t3 --prereg evals/preregistered/claude-r2-<name> --day-a 2026-11-18 --day-b 2026-11-19
  python scripts/claude/r2_score.py pooled --prereg evals/preregistered/claude-r2-<name>

A round 2 preregistration folder holds:
  preregistration.md   the theory's test, written before scoring
  spec.json            {"target": "t1"|"t3", "module": "scripts.claude.<file>", "function": "<name>"}
  manifest.json        sha256 of the predictor and of everything it imports (scripts/claude/freeze.py)

This file does not define any metric. Rows, truth, baselines, intervals and the pass
arithmetic all come from the round 1 harness (scripts/claude/heldout.py, metrics.py,
rsdata.py and the helper functions of score_heldout.py), which is frozen and unchanged.
The run refuses to score when a hash differs or the freeze is not committed and pushed.
Results go to evals/claude/heldout-r2/<folder name>/. A scored file is not overwritten.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.claude import heldout as ho  # noqa: E402
from scripts.claude import rsdata as rs  # noqa: E402
from scripts.claude import score_heldout as sh  # noqa: E402
from scripts.claude.freeze import verify_manifest  # noqa: E402

OUT = ROOT / "evals" / "claude" / "heldout-r2"


def require_frozen_dir(directory: Path) -> dict:
    """The manifest must match, be committed, and be on the pushed branch."""
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


def load_spec(directory: Path, target: str) -> tuple[dict, object]:
    spec = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
    if spec["target"] != target:
        raise SystemExit(f"{directory.name} is a {spec['target']} preregistration, not {target}.")
    function = getattr(importlib.import_module(spec["module"]), spec["function"])
    return spec, function


def out_dir(directory: Path) -> Path:
    path = OUT / directory.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def score_t1(directory: Path, day: str, force: bool, allow_incomplete: bool) -> dict:
    if day < sh.FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    manifest = require_frozen_dir(directory)
    spec, predict_rows = load_spec(directory, "t1")
    out = out_dir(directory)
    sh.refuse_overwrite(out / f"t1-{day}.json", force)
    rows, cons, groups = sh._load(day)
    counts = sh.pull_counts(cons, groups, day)
    sh.require_complete(counts, allow_incomplete)
    deep = ho.deep_queries(rows, day)
    frame = ho.t1_frame(rows, cons, day, predict_rows, deep)
    if frame.empty:
        raise SystemExit(f"No rows to score on {day}.")
    frame["grp"] = frame["query"].map(groups)
    frame.to_csv(out / f"t1-{day}-rows.csv", index=False)
    report = {
        "label": "PREDICTED",
        "target": "t1",
        "prereg": directory.name,
        "predictor": f"{spec['module']}.{spec['function']}",
        "day": day,
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "pulls": counts,
        "slices": {},
    }
    print(f"R2 T1 {directory.name} held-out day {day} (PREDICTED)")
    print(f"  pulls: {json.dumps(counts)}")
    for name, part in sh._t1_slices(frame).items():
        report["slices"][name] = ho.t1_summary(part)
        sh._print_t1(name, report["slices"][name])
    (out / f"t1-{day}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def score_t3(directory: Path, day_a: str, day_b: str, force: bool, allow_incomplete: bool) -> dict:
    if day_b < sh.FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    if rs.calendar_gap(day_a, day_b) < 1:
        raise SystemExit("day_a must be before day_b.")
    manifest = require_frozen_dir(directory)
    spec, call_flips = load_spec(directory, "t3")
    out = out_dir(directory)
    sh.refuse_overwrite(out / f"t3-{day_a}_{day_b}.json", force)
    rows, cons, groups = sh._load(day_b)
    counts_a = sh.pull_counts(cons, groups, day_a)
    counts_b = sh.pull_counts(cons, groups, day_b)
    sh.require_complete(counts_a, allow_incomplete)
    sh.require_complete(counts_b, allow_incomplete)
    days = rs.query_days(cons)
    aligned = {query for query, listed in days.items() if rs.previous_day(listed, day_b) == day_a}
    frame = ho.t3_frame_pairs(rows, cons, day_a, day_b, call_flips)
    if frame.empty:
        raise SystemExit(f"No pairs to score on {day_a} -> {day_b}.")
    dropped = int((~frame["query"].isin(aligned)).sum())
    frame = frame[frame["query"].isin(aligned)].reset_index(drop=True)
    frame["grp"] = frame["query"].map(groups)
    frame.to_csv(out / f"t3-{day_a}_{day_b}-pairs.csv", index=False)
    main = frame[frame["grp"].isin(sh.DECISION_GROUPS)]
    report = {
        "label": "PREDICTED",
        "target": "t3",
        "prereg": directory.name,
        "predictor": f"{spec['module']}.{spec['function']}",
        "day_a": day_a,
        "day_b": day_b,
        "gap_days": rs.calendar_gap(day_a, day_b),
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "pulls_day_a": counts_a,
        "pulls_day_b": counts_b,
        "pairs_dropped_other_previous_day": dropped,
        "slices": {"smartcrew_100": ho.t3_summary(main)},
    }
    print(f"R2 T3 {directory.name} held-out transition {day_a} -> {day_b} (PREDICTED)")
    print(f"  pulls day_a: {json.dumps(counts_a)}")
    print(f"  pulls day_b: {json.dumps(counts_b)}")
    sh._print_t3("smartcrew_100 (decision population)", report["slices"]["smartcrew_100"])
    for group, part in frame.groupby("grp"):
        report["slices"][group] = ho.t3_summary(part)
        sh._print_t3(group, report["slices"][group])
    (out / f"t3-{day_a}_{day_b}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def pooled(directory: Path) -> dict:
    """Pool every scored file of one preregistration. A query keeps its identity across days in the bootstrap."""
    out = out_dir(directory)
    report: dict = {"label": "PREDICTED", "prereg": directory.name}
    t1_files = sorted(out.glob("t1-*-rows.csv"))
    if t1_files:
        frame = pd.concat([pd.read_csv(path) for path in t1_files], ignore_index=True)
        report["t1"] = {"days": sorted(frame["day"].unique().tolist()), "slices": {}}
        print(f"R2 T1 {directory.name} pooled over {report['t1']['days']} (PREDICTED)")
        for name, part in sh._t1_slices(frame).items():
            report["t1"]["slices"][name] = ho.t1_summary(part)
            sh._print_t1(name, report["t1"]["slices"][name])
        report["t1"]["decision"] = sh.t1_decision(frame)
        print(f"  decision (strict new, band3): {json.dumps(report['t1']['decision'])}")
    t3_files = sorted(out.glob("t3-*-pairs.csv"))
    if t3_files:
        frame = pd.concat([pd.read_csv(path) for path in t3_files], ignore_index=True)
        frame = frame[frame["grp"].isin(sh.DECISION_GROUPS)]
        consecutive = frame[(pd.to_datetime(frame["day_b"]) - pd.to_datetime(frame["day_a"])).dt.days == 1]
        block = {"transitions": sorted({f"{a}->{b}" for a, b in zip(frame["day_a"], frame["day_b"], strict=True)}), "slices": {}}
        print(f"R2 T3 {directory.name} pooled over {block['transitions']} (PREDICTED)")
        for name, part in (("consecutive_days", consecutive), ("all_transitions", frame)):
            if len(part):
                block["slices"][name] = ho.t3_summary(part)
                sh._print_t3(name, block["slices"][name])
        if "consecutive_days" in block["slices"]:
            summary = block["slices"]["consecutive_days"]
            block["decision"] = {
                "c1_brief_wording": summary["goal_criterion_pass"],
                "c2_not_below_persistence": summary["guard_pass"],
                "target3_pass": bool(summary["goal_criterion_pass"] and summary["guard_pass"]),
            }
            print(f"  decision: {json.dumps(block['decision'])}")
        report["t3"] = block
    (out / "pooled.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t1 = sub.add_parser("t1")
    t1.add_argument("--day", required=True)
    t3 = sub.add_parser("t3")
    t3.add_argument("--day-a", required=True)
    t3.add_argument("--day-b", required=True)
    both = sub.add_parser("pooled")
    for each in (t1, t3, both):
        each.add_argument("--prereg", required=True, help="Path of the round 2 preregistration folder.")
    for each in (t1, t3):
        each.add_argument("--force", action="store_true", help="Overwrite an existing scored file (identical rerun only).")
        each.add_argument("--allow-incomplete", action="store_true", help="Score a day on which some searches have fewer than three pulls (stated in the preregistration).")
    args = parser.parse_args(argv)
    directory = (ROOT / args.prereg).resolve()
    if args.command == "t1":
        score_t1(directory, args.day, args.force, args.allow_incomplete)
    elif args.command == "t3":
        score_t3(directory, args.day_a, args.day_b, args.force, args.allow_incomplete)
    else:
        pooled(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
