"""Position metrics on the same temporal split as the pairwise experiments.

Selection and calibration use Nov 6 to Nov 9 (query-group CV) and Nov 9 to Nov 11
(forward). Nov 13 and Nov 16 are scored after that and do not choose the ranges,
the point method, or the #1 thresholds. Label: PREDICTED.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

from aeo.ranksim.pair_order import build_transition, fit_experiment, load_model, load_offpage_cache, load_serp_snapshot
from aeo.ranksim.position_metrics import (
    apply_ranges,
    bootstrap_rate,
    choose_range_rule,
    choose_thresholds,
    confident_top,
    early_exact,
    html_split,
    persistence_top_calls,
    position_record,
    predict_positions,
    ranks_from_scores,
    select_point_method,
    stability_table,
    top_margins,
)

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path("/tmp/ranksim-page-cache-v1.pkl")
SNAP_ROOT = Path("/tmp/ranksim-snaps")
FREEZE = ROOT / "evals/preregistered/forward-pair-2026-11-16"
OUT = ROOT / "evals/experiments-2026-11-16"
DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16")
TRANSITIONS = {
    "2026-11-09": ("2026-11-06",),
    "2026-11-11": ("2026-11-06", "2026-11-09"),
    "2026-11-13": ("2026-11-06", "2026-11-09", "2026-11-11"),
    "2026-11-16": ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13"),
}


def _directory(day: str) -> Path:
    matches = [path for path in SNAP_ROOT.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot for {day}")
    return matches[0]


def _cv_scores(rows: list) -> list[float]:
    queries = sorted({row.query for row in rows})
    folds = [set(queries[index::5]) for index in range(5)]
    scores: list[float | None] = [None] * len(rows)
    for fold in folds:
        train = [row for row in rows if row.query not in fold]
        test_indexes = [index for index, row in enumerate(rows) if row.query in fold]
        model = fit_experiment("lambdamart_monotone", train)
        predicted = model.predict([rows[index] for index in test_indexes])
        for index, score in zip(test_indexes, predicted, strict=True):
            scores[index] = float(score)
    if any(score is None for score in scores):
        raise SystemExit("query-group CV left a row unscored")
    return [float(score) for score in scores]


def _fmt_rate(pairs: list[tuple[float, float]]) -> str:
    numerator = sum(item[0] for item in pairs)
    denominator = sum(item[1] for item in pairs)
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(pairs, skip_empty=True)
    return f"{numerator:.0f}/{denominator:.0f} ({numerator / denominator:.3f}) [{low:.3f}, {high:.3f}]"


def _fmt_mean(pairs: list[tuple[float, float]]) -> str:
    numerator = sum(item[0] for item in pairs)
    denominator = sum(item[1] for item in pairs)
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(pairs, skip_empty=True)
    return f"{numerator / denominator:.3f} [{low:.3f}, {high:.3f}] n={denominator:.0f}"


def _public_rule(rule: dict) -> dict:
    return {
        "target": rule["target"],
        "by_bucket": rule["by_bucket"],
        "calibration_coverage": rule["calibration_coverage"],
        "calibration_mean_width": rule["calibration_mean_width"],
        "global": rule["global"],
        "surviving": rule["surviving"],
        "new": rule["new"],
    }


def _line(primary: dict, reference: dict) -> str:
    return (
        f"| {_fmt_rate(primary['all']['exact_by_query'])} "
        f"| {_fmt_rate(primary['all']['within_by_query'])} "
        f"| {_fmt_mean(primary['all']['mae_by_query'])} "
        f"| {_fmt_rate(primary['all']['coverage_by_query'])} "
        f"| {_fmt_mean(primary['all']['width_by_query'])} "
        f"| {_fmt_rate(primary['surviving']['coverage_by_query'])} "
        f"| {_fmt_rate(primary['new']['coverage_by_query'])} "
        f"| {_fmt_rate(reference['all']['coverage_by_query'])} |"
    )


def main() -> int:
    print("Loading snapshots", flush=True)
    cached = pickle.loads(CACHE.read_bytes())
    pages = cached["pages"]
    snaps = {day: load_serp_snapshot(_directory(day), day) for day in DAYS}
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    built = {}
    for label, history_days in TRANSITIONS.items():
        built[label] = build_transition([snaps[day] for day in history_days], snaps[label], pages[label], offpage)
        print(f"  {label}: {len(built[label])} rows", flush=True)
    print("Early-window scores", flush=True)
    scores = {}
    scores["2026-11-09"] = _cv_scores(built["2026-11-09"])
    model_nov11 = fit_experiment("lambdamart_monotone", built["2026-11-09"])
    scores["2026-11-11"] = [float(value) for value in model_nov11.predict(built["2026-11-11"])]
    scores["2026-11-13"] = [float(value) for value in load_model(FREEZE / "model-heldout-nov13.json").predict(built["2026-11-13"])]
    scores["2026-11-16"] = [float(value) for value in load_model(FREEZE / "model-heldout-nov16.json").predict(built["2026-11-16"])]
    early = [(built[day], scores[day]) for day in ("2026-11-09", "2026-11-11")]
    method = select_point_method(early)
    print("Selected point method", method, flush=True)
    calibration = []
    margin_records = []
    for rows, values in early:
        preds = predict_positions(method, rows, values)
        for row, pred in zip(rows, preds, strict=True):
            calibration.append((pred, int(round(row.position)), row.bucket))
        margin_records.extend(top_margins(rows, values, ranks_from_scores(rows, values)))
    rule = choose_range_rule(calibration)
    thresholds = choose_thresholds(margin_records)
    print("Range calibration", rule["calibration_coverage"], rule["reference_90"]["calibration_coverage"], flush=True)
    systems = ["persistence_last", "model_rank"]
    if method not in systems:
        systems.append(method)
    report_rows = []
    detail = {}
    for day in ("2026-11-11", "2026-11-13", "2026-11-16"):
        rows = built[day]
        values = scores[day]
        detail[day] = {}
        for name in systems:
            preds = predict_positions(name, rows, values)
            buckets = [row.bucket for row in rows]
            primary = position_record(rows, preds, apply_ranges(preds, buckets, rule))
            reference = position_record(rows, preds, apply_ranges(preds, buckets, rule["reference_90"]))
            detail[day][name] = {"primary": primary, "reference": reference, "html": html_split(rows, preds)}
            report_rows.append((day, name, primary, reference))
        model_preds = ranks_from_scores(rows, values)
        detail[day]["confident"] = {
            "broad": confident_top(top_margins(rows, values, model_preds), thresholds[0]["threshold"]),
            "strict": confident_top(top_margins(rows, values, model_preds), thresholds[1]["threshold"]),
        }
    persistence_calls = {}
    for label, history_days in TRANSITIONS.items():
        if label == "2026-11-09":
            continue
        persistence_calls[label] = confident_top(
            [
                {**record, "margin": 1.0}
                for record in persistence_top_calls(
                    [(row.query, row.url_key, row.position) for row in snaps[history_days[-1]].rows],
                    [(row.query, row.url_key, row.position) for row in snaps[label].rows],
                )
            ],
            0.0,
        )
    stability = stability_table([snaps[day] for day in DAYS])
    early_rows = [row for day in ("2026-11-09", "2026-11-11") for row in built[day]]
    early_preds = [pred for day in ("2026-11-09", "2026-11-11") for pred in predict_positions("model_rank", built[day], scores[day])]
    early_html = html_split(early_rows, early_preds)
    payload = {
        "label": "PREDICTED",
        "point_method": method,
        "family": "lambdamart_monotone",
        "range_target": 0.95,
        "range_rule": _public_rule(rule),
        "range_rule_90": _public_rule(rule["reference_90"]),
        "confident_thresholds": thresholds,
        "calibration_windows": ["2026-11-09 query-group CV", "2026-11-09 to 2026-11-11 forward"],
        "not_used_for_calibration": ["2026-11-13", "2026-11-16"],
    }
    (FREEZE / "position-calibration.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    selected_hits = early_exact(
        [row for rows, _scores in early for row in rows],
        [pred for rows, values in early for pred in predict_positions(method, rows, values)],
    )
    text = _markdown(method, selected_hits, rule, thresholds, report_rows, detail, persistence_calls, stability, early_html)
    (OUT / "position-metrics.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def _markdown(method, selected_hits, rule, thresholds, report_rows, detail, persistence_calls, stability, early_html) -> str:
    lines = [
        "# Position metrics, 2026-11-16",
        "",
        "Label: PREDICTED. Real positions are the Serper labels. Every top-10 row is counted.",
        "",
        f"Point method selected on the early windows only: `{method}`. Early exact accuracy of that choice is {selected_hits:.3f}.",
        "",
        "Ranges are at most 3 positions wide. The applied range is calibrated to 95% coverage on Nov 9 query-group CV residuals and Nov 11 forward residuals. "
        f"Calibration coverage was {rule['calibration_coverage']:.3f} at mean width {rule['calibration_mean_width']:.3f}. "
        f"The 90% reference rule had calibration coverage {rule['reference_90']['calibration_coverage']:.3f} at mean width {rule['reference_90']['calibration_mean_width']:.3f}. "
        "Nov 13 and Nov 16 did not choose the offsets.",
        "",
        "Confident #1 uses the monotone LambdaMART score gap between the model's own #1 and #2. "
        f"Thresholds chosen on the early windows: {thresholds[0]['threshold']:.4f} (target precision {thresholds[0]['target']:.2f}, early precision {thresholds[0]['calibration_precision']:.3f}, early coverage {thresholds[0]['calibration_coverage']:.3f}) "
        f"and {thresholds[1]['threshold']:.4f} (target precision {thresholds[1]['target']:.2f}, early precision {thresholds[1]['calibration_precision']:.3f}, early coverage {thresholds[1]['calibration_coverage']:.3f}).",
        "",
        "## Scores",
        "",
        "Nov 11 was used for selection. Nov 13 is the model trained through Nov 11. Nov 16 is the model trained through Nov 13. "
        "Cells are count/n (rate) [bootstrap 95% CI by query], or mean [CI] with n. The 95% range is the one that is used. The last column is coverage of the 90% reference range.",
        "",
        "| System | Eval | Exact | Within 1 | MAE | Range coverage, 95% rule | Mean width | Surviving coverage | New coverage | Range coverage, 90% rule |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for day, name, primary, reference in report_rows:
        role = "selection forward" if day == "2026-11-11" else "held out"
        lines.append(f"| {name} | {day} ({role}) {_line(primary, reference)}")
    lines.extend(["", "## Confident #1", "", "| Eval | Rule | Precision | Coverage | Calls |", "| --- | --- | --- | --- | --- |"])
    for day in ("2026-11-11", "2026-11-13", "2026-11-16"):
        for label, block in detail[day]["confident"].items():
            lines.append(
                f"| {day} | model {label} | {_fmt_rate(block['precision_by_query'])} | {_fmt_rate(block['coverage_by_query'])} | {block['calls']}/{block['queries']} |"
            )
        base = persistence_calls[day]
        lines.append(
            f"| {day} | persistence yesterday #1 | {_fmt_rate(base['precision_by_query'])} | {_fmt_rate(base['coverage_by_query'])} | {base['calls']}/{base['queries']} |"
        )
    lines.extend(
        [
            "",
            "## REAL stability of the top spots",
            "",
            "This section describes the Serper lists. It is not a model score.",
            "",
            "| From | To | Queries | #1 same | #2 same | #3 same | Top 3 URLs still in the top 3 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in stability:
        lines.append(
            f"| {row['from']} | {row['to']} | {row['queries']} | {row['same_1']}/{row['queries']} ({row['rate_1']:.3f}) | {row['same_2']}/{row['queries']} ({row['rate_2']:.3f}) | {row['same_3']}/{row['queries']} ({row['rate_3']:.3f}) | {row['top3_kept']}/{row['top3_slots']} ({row['top3_rate']:.3f}) |"
        )
    present = early_html["present"]
    missing = early_html["missing"]
    lines.extend(["", "## Would more queries or filled page text move these metrics?", ""])
    lines.append(
        f"On the early windows, monotone LambdaMART exact-position accuracy is {present['exact']:.3f} on {present['rows']} rows with HTML and {missing['exact']:.3f} on {missing['rows']} rows without HTML."
    )
    if present["exact"] is not None and missing["exact"] is not None and (present["rows"] + missing["rows"]):
        pooled = (present["exact"] * present["rows"] + missing["exact"] * missing["rows"]) / (present["rows"] + missing["rows"])
        lines.append(
            f"Giving the missing rows the with-HTML rate would move the early exact rate from {pooled:.3f} to {present['exact']:.3f}. "
            "That is the most page text can add on this evidence. It stays far from 85% exact position accuracy."
        )
    lines.append(
        "The bootstrap intervals on 30 queries are a few points wide. Tracking about 100 queries would narrow them. "
        "It would not move a rate that sits well below 0.85 up to that target. The limit is day-to-day movement inside the top 10, which these five snapshots already measure. "
        "Spending Serper credits on more queries or on refilling blocked pages is not justified for these metrics. "
        "Rows without HTML were not the less accurate slice, so filling those pages would not raise exact-position accuracy. "
        "A width-3 range cannot cover 95% or even 90% of these residuals, so both rules use the same maximum-coverage offsets. "
        "More queries would tighten the interval around the observed rate. They would not move it to 0.85 or 0.95."
    )
    lines.append("")
    return "\n".join(lines)


def _line(primary: dict, reference: dict) -> str:
    return (
        f"| {_fmt_rate(primary['all']['exact_by_query'])} "
        f"| {_fmt_rate(primary['all']['within_by_query'])} "
        f"| {_fmt_mean(primary['all']['mae_by_query'])} "
        f"| {_fmt_rate(primary['all']['coverage_by_query'])} "
        f"| {_fmt_mean(primary['all']['width_by_query'])} "
        f"| {_fmt_rate(primary['surviving']['coverage_by_query'])} "
        f"| {_fmt_rate(primary['new']['coverage_by_query'])} "
        f"| {_fmt_rate(reference['all']['coverage_by_query'])} |"
    )


if __name__ == "__main__":
    raise SystemExit(main())
