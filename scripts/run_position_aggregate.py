"""Position metrics from pairwise aggregation and direct position models.

Choose ranges and #1 thresholds on Nov 9 query-group CV and the Nov 11 forward
test only. Score Nov 13 (trained through Nov 11) and Nov 16 (trained through
Nov 13). Every top-10 row is counted. Label: PREDICTED.
"""

from __future__ import annotations

import csv
import pickle
from pathlib import Path

from aeo.ranksim.pair_order import build_transition, fit_experiment, load_offpage_cache, load_serp_snapshot
from aeo.ranksim.position_aggregate import (
    ENSEMBLE_SIZE,
    aggregate_positions,
    apply_window_shift,
    choose_window_shift,
    fit_ensemble,
    fit_lgbm_multiclass,
    fit_lgbm_regression,
    fit_ordinal_lgbm,
)
from aeo.ranksim.position_metrics import (
    apply_ranges,
    bootstrap_rate,
    choose_offsets,
    choose_range_rule,
    choose_thresholds,
    confident_top,
    persistence_points,
    persistence_top_calls,
    position_record,
    ranks_from_scores,
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
TRAIN_KEYS = ("fold", "nov9", "through11", "through13")
SINGLE_MODELS = (
    "lambdamart_monotone",
    "ridge_history",
    "ridge_content",
    "ridge_offpage",
    "ensemble_ridge_lambdamart",
    "ridge_pointwise",
)
ENSEMBLES = (
    ("lambdamart_monotone", "seed"),
    ("ridge_offpage", "bootstrap"),
    ("ridge_content", "bootstrap"),
    ("ensemble_ridge_lambdamart", "seed"),
)
AGGREGATES = ("copeland", "bradley_terry", "borda", "median_rank")
DIRECT = ("lgbm_regression", "lgbm_multiclass", "ordinal_lgbm")


def _directory(day: str) -> Path:
    matches = [path for path in SNAP_ROOT.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot for {day}")
    return matches[0]


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


def _folds(rows: list) -> list[set[str]]:
    queries = sorted({row.query for row in rows})
    return [set(queries[index::5]) for index in range(5)]


def _train_rows(built: dict, key: str, fold: set[str] | None = None) -> list:
    if key == "fold":
        return [row for row in built["2026-11-09"] if row.query not in (fold or set())]
    if key == "nov9":
        return built["2026-11-09"]
    if key == "through11":
        return built["2026-11-09"] + built["2026-11-11"]
    if key == "through13":
        return built["2026-11-09"] + built["2026-11-11"] + built["2026-11-13"]
    raise KeyError(key)


def _ceilings(rows: list) -> list[int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.query] = counts.get(row.query, 0) + 1
    return [counts[row.query] for row in rows]


def _oracle_coverage(preds: list[int], reals: list[int]) -> float:
    rule = choose_offsets(list(zip(preds, reals, strict=True)), target=0.95)
    return float(rule["calibration_coverage"])


def _pack(rows, preds, ranges) -> dict:
    record = position_record(rows, preds, ranges)
    oracle = _oracle_coverage(preds, [int(round(row.position)) for row in rows])
    record["oracle_coverage"] = oracle
    record["oracle_hits"] = round(oracle * len(rows))
    record["oracle_rows"] = len(rows)
    return record


def main() -> int:
    print("Loading snapshots", flush=True)
    pages = pickle.loads(CACHE.read_bytes())["pages"]
    snaps = {day: load_serp_snapshot(_directory(day), day) for day in DAYS}
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    built = {}
    for label, history in TRANSITIONS.items():
        built[label] = build_transition([snaps[day] for day in history], snaps[label], pages[label], offpage)
        print(f"  {label}: {len(built[label])} rows", flush=True)
    fold_sets = _folds(built["2026-11-09"])
    print(f"Fitting ensembles of {ENSEMBLE_SIZE}. This is the slow step.", flush=True)
    ensembles: dict[tuple, list] = {}
    singles: dict[tuple, object] = {}
    directs: dict[tuple, object] = {}
    for family, mode in ENSEMBLES:
        for key in ("nov9", "through11", "through13"):
            print(f"  ensemble {family} {mode} {key}", flush=True)
            ensembles[(family, mode, key)] = fit_ensemble(family, _train_rows(built, key), size=ENSEMBLE_SIZE, mode=mode)
        for fold_id, fold in enumerate(fold_sets):
            print(f"  ensemble {family} {mode} fold {fold_id}", flush=True)
            ensembles[(family, mode, f"fold{fold_id}")] = fit_ensemble(
                family, _train_rows(built, "fold", fold), size=ENSEMBLE_SIZE, mode=mode
            )
    for family in SINGLE_MODELS:
        for key in ("nov9", "through11", "through13"):
            singles[(family, key)] = fit_experiment(family, _train_rows(built, key))
        for fold_id, fold in enumerate(fold_sets):
            singles[(family, f"fold{fold_id}")] = fit_experiment(family, _train_rows(built, "fold", fold))
    for key in ("nov9", "through11", "through13"):
        rows = _train_rows(built, key)
        directs[("lgbm_regression", key)] = fit_lgbm_regression(rows)
        directs[("lgbm_multiclass", key)] = fit_lgbm_multiclass(rows)
        directs[("ordinal_lgbm", key)] = fit_ordinal_lgbm(rows)
    for fold_id, fold in enumerate(fold_sets):
        rows = _train_rows(built, "fold", fold)
        directs[("lgbm_regression", f"fold{fold_id}")] = fit_lgbm_regression(rows)
        directs[("lgbm_multiclass", f"fold{fold_id}")] = fit_lgbm_multiclass(rows)
        directs[("ordinal_lgbm", f"fold{fold_id}")] = fit_ordinal_lgbm(rows)

    def single_scores(family: str, day: str) -> list[float]:
        rows = built[day]
        if day == "2026-11-09":
            scores = [0.0] * len(rows)
            for fold_id, fold in enumerate(fold_sets):
                model = singles[(family, f"fold{fold_id}")]
                indexes = [index for index, row in enumerate(rows) if row.query in fold]
                predicted = model.predict([rows[index] for index in indexes])
                for index, value in zip(indexes, predicted, strict=True):
                    scores[index] = float(value)
            return scores
        key = {"2026-11-11": "nov9", "2026-11-13": "through11", "2026-11-16": "through13"}[day]
        return [float(value) for value in singles[(family, key)].predict(rows)]

    def ensemble_scores(family: str, mode: str, day: str) -> list[list[float]]:
        rows = built[day]
        if day == "2026-11-09":
            columns = [[0.0] * len(rows) for _ in range(ENSEMBLE_SIZE)]
            for fold_id, fold in enumerate(fold_sets):
                models = ensembles[(family, mode, f"fold{fold_id}")]
                indexes = [index for index, row in enumerate(rows) if row.query in fold]
                subset = [rows[index] for index in indexes]
                for member, model in enumerate(models):
                    predicted = model.predict(subset)
                    for index, value in zip(indexes, predicted, strict=True):
                        columns[member][index] = float(value)
            return columns
        key = {"2026-11-11": "nov9", "2026-11-13": "through11", "2026-11-16": "through13"}[day]
        return [[float(value) for value in model.predict(rows)] for model in ensembles[(family, mode, key)]]

    def direct_positions(kind: str, day: str) -> list[int]:
        rows = built[day]
        if day == "2026-11-09":
            raw = [0.0] * len(rows)
            for fold_id, fold in enumerate(fold_sets):
                model = directs[(kind, f"fold{fold_id}")]
                indexes = [index for index, row in enumerate(rows) if row.query in fold]
                predicted = model.raw([rows[index] for index in indexes])
                for index, value in zip(indexes, predicted, strict=True):
                    raw[index] = float(value)
            return ranks_from_scores(rows, [-value for value in raw])
        key = {"2026-11-11": "nov9", "2026-11-13": "through11", "2026-11-16": "through13"}[day]
        return directs[(kind, key)].predicted_positions(rows)

    print("Scoring", flush=True)
    # Predictions for every reported method on every scored day.
    # method name -> day -> positions, and optional ensemble windows
    predictions: dict[str, dict[str, list[int]]] = {}
    windows: dict[str, dict[str, list[tuple[int, int]]]] = {}
    score_bank: dict[str, dict[str, list[float]]] = {}
    member_bank: dict[str, dict[str, list[list[float]]]] = {}
    for family in SINGLE_MODELS:
        predictions[family] = {}
        score_bank[family] = {}
        for day in ("2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16"):
            values = single_scores(family, day)
            score_bank[family][day] = values
            predictions[family][day] = ranks_from_scores(built[day], values)
    for family, mode in ENSEMBLES:
        label = f"{family}_{mode}"
        member_bank[label] = {}
        for aggregate in AGGREGATES:
            predictions[f"{label}_{aggregate}"] = {}
            windows[f"{label}_{aggregate}"] = {}
        for day in ("2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16"):
            members = ensemble_scores(family, mode, day)
            member_bank[label][day] = members
            aggregated = aggregate_positions(built[day], members)
            for aggregate in AGGREGATES:
                name = f"{label}_{aggregate}"
                predictions[name][day] = aggregated["positions"][aggregate]
                windows[name][day] = aggregated["windows"]
    for kind in DIRECT:
        predictions[kind] = {}
        for day in ("2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16"):
            predictions[kind][day] = direct_positions(kind, day)
    predictions["persistence_last"] = {day: persistence_points(built[day]) for day in predictions["lambdamart_monotone"]}

    early_days = ("2026-11-09", "2026-11-11")
    holdout_days = ("2026-11-13", "2026-11-16")
    method_names = ["persistence_last", *SINGLE_MODELS, *DIRECT]
    for family, mode in ENSEMBLES:
        for aggregate in AGGREGATES:
            method_names.append(f"{family}_{mode}_{aggregate}")

    records = []
    for name in method_names:
        use_windows = name in windows
        if use_windows:
            early_windows = [window for day in early_days for window in windows[name][day]]
            early_reals = [int(round(row.position)) for day in early_days for row in built[day]]
            early_ceilings = [ceiling for day in early_days for ceiling in _ceilings(built[day])]
            shift = choose_window_shift(early_windows, early_reals, early_ceilings)
            range_note = f"ensemble max-mass window, early shift {shift}"
        else:
            pairs = []
            for day in early_days:
                for row, pred in zip(built[day], predictions[name][day], strict=True):
                    pairs.append((pred, int(round(row.position)), row.bucket))
            rule = choose_range_rule(pairs)
            shift = None
            range_note = (
                f"offset rule target 0.95, early coverage {rule['calibration_coverage']:.3f}, "
                f"by_bucket {rule['by_bucket']}"
            )
        if name in member_bank:
            early_calls = []
            for day in early_days:
                early_calls.extend(_ensemble_calls(built[day], member_bank[name][day]))
            thresholds = choose_thresholds(early_calls, (0.80, 0.90))
        elif name in score_bank:
            early_calls = []
            for day in early_days:
                early_calls.extend(top_margins(built[day], score_bank[name][day], predictions[name][day]))
            thresholds = choose_thresholds(early_calls, (0.80, 0.90))
        else:
            thresholds = []
        for day in holdout_days:
            rows = built[day]
            preds = predictions[name][day]
            if use_windows:
                ranges = apply_window_shift(windows[name][day], shift, _ceilings(rows))
            else:
                ranges = apply_ranges(preds, [row.bucket for row in rows], rule)
            packed = _pack(rows, preds, ranges)
            confident = []
            if name in member_bank:
                calls = _ensemble_calls(rows, member_bank[name][day])
                confident = [confident_top(calls, float(item["threshold"])) for item in thresholds]
            elif name in score_bank:
                calls = top_margins(rows, score_bank[name][day], preds)
                confident = [confident_top(calls, float(item["threshold"])) for item in thresholds]
            records.append(
                {
                    "name": name,
                    "day": day,
                    "record": packed,
                    "confident": confident,
                    "thresholds": thresholds,
                    "range_note": range_note,
                }
            )
            exact = packed["all"]["exact"]
            cover = packed["all"]["range_coverage"]
            print(f"  {name} {day} exact {exact:.3f} range {cover:.3f} oracle {packed['oracle_coverage']:.3f}", flush=True)

    persistence_calls = {}
    for label, history_days in TRANSITIONS.items():
        if label not in ("2026-11-13", "2026-11-16"):
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
    text = _markdown(method_names, records, persistence_calls)
    (OUT / "position-aggregate.md").write_text(text, encoding="utf-8")
    _csv(records, OUT / "position-aggregate.csv")
    print(text)
    print("Wrote", OUT / "position-aggregate.md", flush=True)
    return 0


def _ensemble_calls(rows, member_scores: list[list[float]]) -> list[dict]:
    """Confidence is the fraction of ensemble members that rank the called URL #1."""
    from collections import defaultdict

    grouped: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        grouped[row.query].append(index)
    calls = []
    members = len(member_scores)
    for query in sorted(grouped):
        indexes = grouped[query]
        votes = {index: 0 for index in indexes}
        for scores in member_scores:
            top = max(indexes, key=lambda index: (float(scores[index]), -ord(rows[index].url[:1] or " ")))
            # URL tie-break matches ranks_from_scores, which sorts by -score, url.
            top = sorted(indexes, key=lambda index: (-float(scores[index]), rows[index].url))[0]
            votes[top] += 1
        called = max(indexes, key=lambda index: (votes[index], -len(rows[index].url)))
        called = sorted(indexes, key=lambda index: (-votes[index], rows[index].url))[0]
        calls.append(
            {
                "query": query,
                "margin": votes[called] / members,
                "correct": 1.0 if int(round(rows[called].position)) == 1 else 0.0,
            }
        )
    return calls


def _markdown(method_names: list[str], records: list[dict], persistence_calls: dict) -> str:
    lines = [
        "# Position aggregation and direct position models",
        "",
        "Label: PREDICTED. Every top-10 row is counted. Ranges and #1 thresholds use Nov 9 query-group CV and the Nov 11 forward test only.",
        "",
        f"Methods tried: {len(method_names)}. Ensemble size: {ENSEMBLE_SIZE}, fixed before the holdout. "
        "Seed ensembles vary LightGBM's seed. Bootstrap ensembles resample training queries.",
        "",
        "The 95% column is the frozen early-window range. Oracle coverage is the best fixed 3-wide offset on that same eval window. It is not used to choose a method.",
        "",
        "| Method | Eval | Exact | Within 1 | MAE | 95% range coverage | Mean width | Oracle best 3-wide | Beats persistence exact | Beats persistence range |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    by_key = {(row["name"], row["day"]): row for row in records}
    for day in ("2026-11-13", "2026-11-16"):
        base = by_key[("persistence_last", day)]["record"]
        for name in method_names:
            item = by_key[(name, day)]
            block = item["record"]
            beats_exact = "yes" if block["all"]["exact"] > base["all"]["exact"] + 1e-12 else "no"
            beats_range = "yes" if block["all"]["range_coverage"] > base["all"]["range_coverage"] + 1e-12 else "no"
            if name == "persistence_last":
                beats_exact = "baseline"
                beats_range = "baseline"
            lines.append(
                f"| {name} | {day} | {_fmt_rate(block['all']['exact_by_query'])} | {_fmt_rate(block['all']['within_by_query'])} "
                f"| {_fmt_mean(block['all']['mae_by_query'])} | {_fmt_rate(block['all']['coverage_by_query'])} "
                f"| {_fmt_mean(block['all']['width_by_query'])} | {block['oracle_hits']}/{block['oracle_rows']} ({block['oracle_coverage']:.3f}) "
                f"| {beats_exact} | {beats_range} |"
            )
    lines.extend(["", "## Confident #1", "", "| Method | Eval | Threshold | Precision | Coverage |", "| --- | --- | --- | --- | --- |"])
    for day in ("2026-11-13", "2026-11-16"):
        for name in method_names:
            item = by_key[(name, day)]
            if not item["confident"]:
                continue
            for threshold, block in zip(item["thresholds"], item["confident"], strict=True):
                lines.append(
                    f"| {name} | {day} | {threshold['threshold']:.4f} (target {threshold['target']:.2f}) "
                    f"| {_fmt_rate(block['precision_by_query'])} | {_fmt_rate(block['coverage_by_query'])} |"
                )
        base = persistence_calls[day]
        lines.append(
            f"| persistence yesterday #1 | {day} | always | {_fmt_rate(base['precision_by_query'])} | {_fmt_rate(base['coverage_by_query'])} |"
        )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            "Exact-position target is 85%. The 3-wide range target is 95% coverage. "
            "A method beats persistence only when its held-out rate is higher. The oracle column is an upper bound for a fixed offset, not a model that was selected.",
            "",
        ]
    )
    return "\n".join(lines)


def _csv(records: list[dict], path: Path) -> None:
    fields = [
        "name",
        "day",
        "exact",
        "exact_n",
        "within_1",
        "mae",
        "range_coverage",
        "range_n",
        "mean_width",
        "oracle_coverage",
        "range_note",
        "label",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in records:
            block = item["record"]["all"]
            writer.writerow(
                {
                    "name": item["name"],
                    "day": item["day"],
                    "exact": block["exact"],
                    "exact_n": block["rows"],
                    "within_1": block["within_1"],
                    "mae": block["mae"],
                    "range_coverage": block["range_coverage"],
                    "range_n": block["rows"],
                    "mean_width": block["mean_width"],
                    "oracle_coverage": item["record"]["oracle_coverage"],
                    "range_note": item["range_note"],
                    "label": "PREDICTED",
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
