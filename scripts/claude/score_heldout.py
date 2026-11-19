"""Score a frozen predictor on a held-out day. Refuses to run before the freeze is committed and pushed.

  python scripts/claude/score_heldout.py t1 --day 2026-11-18
  python scripts/claude/score_heldout.py t3 --day-a 2026-11-17 --day-b 2026-11-18
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
PREREG = {"t1": ROOT / "evals/preregistered/claude-t1-placement", "t3": ROOT / "evals/preregistered/claude-t3-movers"}
FIRST_HELDOUT_DAY = "2026-11-18"


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


def _load(day: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    rows = rs.load_rows(day)
    cons = rs.consensus_table(rows)
    return rows, cons, rs.query_groups(rows)


def _t1_slices(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "strict_new": frame[frame["strict_new"]],
        "new_no_deep_history": frame[(frame["klass"] == "new") & ~frame["strict_new"]],
        "reentrant": frame[frame["klass"] == "reentrant"],
        "all_non_survivors": frame,
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


def score_t1(day: str) -> dict:
    if day < FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    manifest = require_frozen("t1")
    from scripts.claude import t1_placement

    rows, cons, groups = _load(day)
    deep = ho.deep_queries(rows, day)
    frame = ho.t1_frame(rows, cons, day, t1_placement.predict_rows, deep)
    if frame.empty:
        raise SystemExit(f"No rows to score on {day}.")
    frame["grp"] = frame["query"].map(groups)
    OUT.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT / f"t1-{day}-rows.csv", index=False)
    report = {"label": "PREDICTED", "target": "t1", "day": day, "manifest_frozen_at": manifest.get("frozen_at_utc"), "slices": {}}
    print(f"T1 held-out day {day} (PREDICTED)")
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


def score_t3(day_a: str, day_b: str) -> dict:
    """Score both frozen scorers: A (pull-majority order) and B (a contested pair flips)."""
    if day_b < FIRST_HELDOUT_DAY:
        raise SystemExit("Not a held-out day.")
    manifest = require_frozen("t3")
    rows, cons, groups = _load(day_b)
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "label": "PREDICTED",
        "target": "t3",
        "day_a": day_a,
        "day_b": day_b,
        "gap_days": rs.calendar_gap(day_a, day_b),
        "manifest_frozen_at": manifest.get("frozen_at_utc"),
        "scorers": {},
    }
    print(f"T3 held-out transition {day_a} -> {day_b} (PREDICTED)")
    for scorer in T3_SCORERS:
        frame = _t3_frame(scorer, rows, cons, day_a, day_b)
        if frame.empty:
            raise SystemExit(f"No pairs to score on {day_a} -> {day_b}.")
        frame["grp"] = frame["query"].map(groups)
        frame["scorer"] = scorer
        frame.to_csv(OUT / f"t3{scorer}-{day_a}_{day_b}-pairs.csv", index=False)
        slices = {"all": ho.t3_summary(frame)}
        print(f" scorer {scorer}")
        _print_t3("all", slices["all"])
        for group, part in frame.groupby("grp"):
            slices[group] = ho.t3_summary(part)
            _print_t3(group, slices[group])
        report["scorers"][scorer] = slices
    (OUT / f"t3-{day_a}_{day_b}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


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
    for scorer in T3_SCORERS:
        t3_files = sorted(OUT.glob(f"t3{scorer}-*-pairs.csv"))
        if not t3_files:
            continue
        frame = pd.concat([pd.read_csv(path) for path in t3_files], ignore_index=True)
        consecutive = frame[(pd.to_datetime(frame["day_b"]) - pd.to_datetime(frame["day_a"])).dt.days == 1]
        block = {"transitions": sorted({f"{a}->{b}" for a, b in zip(frame["day_a"], frame["day_b"], strict=True)}), "slices": {}}
        print(f"T3 scorer {scorer} pooled over {block['transitions']} (PREDICTED)")
        for name, part in (("consecutive_days", consecutive), ("all_transitions", frame)):
            if len(part):
                block["slices"][name] = ho.t3_summary(part)
                _print_t3(name, block["slices"][name])
        report[f"t3{scorer}"] = block
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
    sub.add_parser("pooled")
    args = parser.parse_args(argv)
    if args.command == "t1":
        score_t1(args.day)
    elif args.command == "t3":
        score_t3(args.day_a, args.day_b)
    else:
        pooled()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
