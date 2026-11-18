"""Fit a width-3 band on dates through 2026-11-13. Later days do not choose the rule.

Center and width are ordinary least squares on seen, last position, the URL's
past movement, and the query's past movement. The fit uses Nov 6 to Nov 9 and
Nov 9 to Nov 11. The width cuts are chosen on Nov 11 to Nov 13. The frozen
point answers every URL, because persistence ± 1 does too and the gate requires
an answered fraction at least that high. Label: PREDICTED.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from aeo.ranksim.consensus import ABSENT_RANK, pull_rank
from aeo.ranksim.pair_order import Snap, load_serp_snapshot
from aeo.ranksim.position_metrics import bootstrap_rate
from aeo.ranksim.tight_bands import (
    TightRow,
    baseline_center,
    choose_width_cuts,
    clip_last_band,
    issue_band,
    persistence_pm1,
    precision_curve,
    predict_abs_error,
    summarize,
)

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.score_consensus import _deep_ranks, _read_serp  # noqa: E402

SNAP_ROOT = Path("/tmp/ranksim-snaps")
PROOF = Path("/tmp/ranksim-proof")
OUT = ROOT / "evals/experiments-2026-11-16/tight-bands.md"
CALIBRATION = ROOT / "evals/preregistered/tight-band-2026-11-18/tight-calibration.json"
DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16")
QUANTILES = [(0.2, 0.5), (0.25, 0.5), (0.33, 0.67), (0.4, 0.7), (0.5, 0.75)]
CURVE_QUANTILES = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]


def _directory(day: str) -> Path:
    matches = [path for path in SNAP_ROOT.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot for {day}")
    return matches[0]


def _index(snap: Snap) -> dict[str, dict[str, int]]:
    found: dict[str, dict[str, int]] = defaultdict(dict)
    for row in snap.rows:
        position = int(round(row.position))
        current = found[row.query].get(row.url_key)
        if current is None or position < current:
            found[row.query][row.url_key] = position
    return found


def _series(history: list[dict[str, dict[str, int]]], query: str, key: str) -> list[int]:
    series = []
    seen = False
    for day in history:
        position = day.get(query, {}).get(key)
        if position is not None and position < ABSENT_RANK:
            series.append(int(position))
            seen = True
        elif seen:
            series.append(ABSENT_RANK)
    return series


def _volatility(series: list[int]) -> float:
    if len(series) < 2:
        return 0.0
    return sum(abs(series[index] - series[index - 1]) for index in range(1, len(series))) / (len(series) - 1)


def examples(history: list[dict[str, dict[str, int]]], today: dict[str, dict[str, int]]) -> list[TightRow]:
    rows = []
    queries = set(today) | set().union(*(set(day) for day in history))
    for query in sorted(queries):
        keys = set(today.get(query, {}))
        for day in history:
            keys |= set(day.get(query, {}))
        series_by_key = {key: _series(history, query, key) for key in keys}
        seen_vols = [_volatility(series) for series in series_by_key.values() if series]
        query_vol = sum(seen_vols) / len(seen_vols) if seen_vols else 0.0
        for key in sorted(keys):
            series = series_by_key[key]
            seen = 1.0 if any(position < ABSENT_RANK for position in series) else 0.0
            position = float(series[-1]) if series else float(ABSENT_RANK)
            url_vol = _volatility(series) if series else query_vol
            outcome = today.get(query, {}).get(key, ABSENT_RANK)
            rows.append(TightRow(query, key, seen, position, url_vol, query_vol, (int(outcome),)))
    return rows


def multi_examples(history: list[dict[str, dict[str, int]]], pulls: list[dict], deep: dict[str, dict[str, int]]) -> list[TightRow]:
    """Features from history only. The outcome is every pull's position, with 11 or a deep rank for an absence."""
    queries = set.intersection(*(set(pull) for pull in pulls))
    rows = []
    for query in sorted(queries):
        query_pulls = [pull[query] for pull in pulls]
        query_deep = deep.get(query, {})
        keys = set()
        display = {}
        for pull in query_pulls:
            for key, (url, _position) in pull.items():
                keys.add(key)
                display.setdefault(key, url)
        for day in history:
            keys |= set(day.get(query, {}))
        series_by_key = {key: _series(history, query, key) for key in keys}
        seen_vols = [_volatility(series) for series in series_by_key.values() if series]
        query_vol = sum(seen_vols) / len(seen_vols) if seen_vols else 0.0
        for key in sorted(keys):
            series = series_by_key[key]
            seen = 1.0 if any(position < ABSENT_RANK for position in series) else 0.0
            position = float(series[-1]) if series else float(ABSENT_RANK)
            url_vol = _volatility(series) if series else query_vol
            positions = []
            for pull in query_pulls:
                rank, _shown = pull_rank(key, {item: pos for item, (_url, pos) in pull.items()}, query_deep)
                positions.append(rank)
            rows.append(TightRow(query, key, seen, position, url_vol, query_vol, tuple(positions)))
    return rows


def _fit(rows: list[TightRow]) -> tuple[list[float], list[float]]:
    from aeo.ranksim.tight_bands import features, predict_center

    matrix = np.asarray([features(row) for row in rows], dtype=float)
    target = np.asarray([row.positions[0] for row in rows], dtype=float)
    center, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    center_coefficients = [float(value) for value in center]
    residual = np.asarray([abs(row.positions[0] - predict_center(row, center_coefficients)) for row in rows], dtype=float)
    width, *_ = np.linalg.lstsq(matrix, residual, rcond=None)
    return center_coefficients, [float(value) for value in width]


def _issued(rows, center, width, cut1, cut2, threshold):
    return [
        (
            row,
            issue_band(
                row,
                center_coefficients=center,
                width_coefficients=width,
                cut1=cut1,
                cut2=cut2,
                abstain_above=threshold,
            ),
        )
        for row in rows
    ]


def _baselines(rows: list[TightRow]) -> dict[str, list]:
    persistence = []
    last = []
    for row in rows:
        center = baseline_center(row)
        persistence.append((row, persistence_pm1(center)))
        if row.seen <= 0:
            last.append((row, (ABSENT_RANK, ABSENT_RANK)))
        else:
            # A single past pull makes yesterday's band a point. The clip keeps it a point.
            point = int(round(row.position))
            last.append((row, clip_last_band(point, point, point)))
    return {"persistence_pm1": persistence, "last_band_width3": last}


def _query_pairs(rows: list[tuple[TightRow, tuple[int, int] | None]], key_num: str, key_den: str) -> list[tuple[float, float]]:
    grouped: dict[str, list] = defaultdict(list)
    for row, band in rows:
        grouped[row.query].append((row, band))
    pairs = []
    for query in sorted(grouped):
        totals = summarize(grouped[query])
        pairs.append((totals[key_num], totals[key_den] if totals[key_den] else (1.0 if key_den == "urls" else 0.0)))
    return pairs


def _fmt(rows, key_num, key_den) -> str:
    pairs = _query_pairs(rows, key_num, key_den)
    numerator = sum(item[0] for item in pairs)
    denominator = sum(item[1] for item in pairs)
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(pairs)
    return f"{numerator / denominator:.3f} [{low:.3f}, {high:.3f}] n={denominator:.0f}"


def _report_line(name: str, rows) -> str:
    totals = summarize(rows)
    return (
        f"| {name} | {_fmt(rows, 'coverage_hits', 'coverage_count')} | {totals['answered_fraction']:.3f} "
        f"({totals['answered']:.0f}/{totals['urls']:.0f}) | {_fmt(rows, 'winkler', 'winkler_count')} | {totals['mean_width']:.3f} |"
    )


def main() -> int:
    print("Loading snapshots through Nov 13", flush=True)
    indexed = {day: _index(load_serp_snapshot(_directory(day), day)) for day in DAYS[:4]}
    order = DAYS[:4]
    built = {}
    for index, day in enumerate(order):
        if index == 0:
            continue
        built[day] = examples([indexed[name] for name in order[:index]], indexed[day])
        print(f"  {day}: {len(built[day])} urls", flush=True)
    train = built["2026-11-09"] + built["2026-11-11"]
    center, width = _fit(train)
    print("Center coefficients", [round(value, 4) for value in center], flush=True)
    print("Width coefficients", [round(value, 4) for value in width], flush=True)
    train_errors = sorted(predict_abs_error(row, width) for row in train)

    def _quantile(fraction: float) -> float:
        if fraction >= 1:
            return math.inf
        index = min(len(train_errors) - 1, max(0, int(round(fraction * (len(train_errors) - 1)))))
        return float(train_errors[index])

    cuts = [(_quantile(low), _quantile(high)) for low, high in QUANTILES]
    chosen = choose_width_cuts(built["2026-11-13"], center_coefficients=center, width_coefficients=width, cuts=cuts)
    print("Chosen cuts", chosen["cut1"], chosen["cut2"], "coverage", round(chosen["coverage"], 3), flush=True)
    selection = _issued(built["2026-11-13"], center, width, chosen["cut1"], chosen["cut2"], math.inf)
    baselines = _baselines(built["2026-11-13"])
    persistence_coverage = summarize(baselines["persistence_pm1"])["coverage"]
    curve = precision_curve(
        built["2026-11-13"],
        center_coefficients=center,
        width_coefficients=width,
        cut1=chosen["cut1"],
        cut2=chosen["cut2"],
        thresholds=[_quantile(fraction) for fraction in CURVE_QUANTILES],
    )
    payload = {
        "label": "PREDICTED",
        "max_width": 3,
        "center_coefficients": center,
        "width_coefficients": width,
        "feature_order": ["intercept", "seen", "position", "url_volatility", "query_volatility"],
        "cut1": chosen["cut1"],
        "cut2": chosen["cut2"],
        "abstain_above": None,
        "fit_windows": ["2026-11-06 to 2026-11-09", "2026-11-09 to 2026-11-11"],
        "cut_window": "2026-11-11 to 2026-11-13",
        "not_used_for_selection": ["2026-11-16", "2026-11-17", "2026-11-18"],
        "cuts_tried": len(QUANTILES),
        "cut_quantiles": QUANTILES,
        "selection_coverage": chosen["coverage"],
        "selection_persistence_pm1_coverage": persistence_coverage,
        "selection_gap": chosen["coverage"] - persistence_coverage,
        "curve": curve,
    }
    CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Scoring Nov 16 after the rule was written", flush=True)
    pulls = [
        _read_serp(PROOF / "2026-11-16-q100/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-a/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-b/serp_serper_live.csv"),
    ]
    deep = _deep_ranks(PROOF / "2026-11-16-deep/serp_serper_deep.csv")
    forward_rows = multi_examples([indexed[day] for day in DAYS[:4]], pulls, deep)
    forward = _issued(forward_rows, center, width, chosen["cut1"], chosen["cut2"], math.inf)
    forward_base = _baselines(forward_rows)
    lines = [
        "# Tight bands, width at most 3",
        "",
        "Label: PREDICTED. The ±6 band gate in `evals/preregistered/band-2026-11-18/` is unchanged.",
        "",
        "The center is a least-squares prediction of today's position from four features: seen or new, last position, the URL's mean absolute move across past pulls, and that same mean for the query. Width is 1, 2, or 3 from a second least-squares prediction of the absolute error. The fit uses Nov 6 to Nov 11. The two width cuts were chosen on Nov 11 to Nov 13, among "
        f"{len(QUANTILES)} answer-every-URL settings. Each setting is a pair of quantiles of the fit-window expected error. The winner is the highest coverage, then the lower Winkler score, then the narrower band.",
        "",
        f"Chosen cuts: width 1 when the expected absolute error is at most {chosen['cut1']}, width 2 up to {chosen['cut2']}, otherwise width 3. The frozen point does not abstain. Persistence ± 1 answers every URL, and the gate requires an answered fraction at least that high.",
        "",
        f"On the Nov 13 selection window the model coverage is {chosen['coverage']:.3f} and persistence ± 1 is {persistence_coverage:.3f}. The gap is {chosen['coverage'] - persistence_coverage:+.3f}. The gate needs +0.05 on a later day. This window chose the cuts, so it is not the gate.",
        "",
        "## Nov 13 selection window",
        "",
        "| System | Coverage of issued bands | Answered | Winkler | Mean width |",
        "| --- | --- | --- | --- | --- |",
        _report_line("Model, answer all", selection),
        _report_line("Persistence ± 1", baselines["persistence_pm1"]),
        _report_line("Last band clipped to width 3", baselines["last_band_width3"]),
        "",
        "## Precision at answered fraction, Nov 13",
        "",
        "Precision here is the share of observed positions that fall inside the bands that were issued. Answered fraction is how many URLs received a band. Rows that abstain are not in the Winkler score. The answer-all row is the frozen point. The other rows were not eligible for the gate, because they answer fewer URLs than persistence ± 1.",
        "",
        "| Abstain above | Answered fraction | Coverage | Full containment | Winkler | Mean width |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for point in curve:
        limit = "none" if point["abstain_above"] is None else f"{point['abstain_above']}"
        lines.append(
            f"| {limit} | {point['answered_fraction']:.3f} | {point['coverage']:.3f} | {point['containment']:.3f} | {point['mean_winkler']:.3f} | {point['mean_width']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Nov 16 forward check, not used to choose",
            "",
            "Nov 16 here is the three pulls plus deep ranks for absences. Features still stop at Nov 13. It was scored after the calibration file was written.",
            "",
            "| System | Coverage of issued bands | Answered | Winkler | Mean width |",
            "| --- | --- | --- | --- | --- |",
            _report_line("Model, answer all", forward),
            _report_line("Persistence ± 1", forward_base["persistence_pm1"]),
            _report_line("Last band clipped to width 3", forward_base["last_band_width3"]),
            "",
            "## Nov 17",
            "",
            "The Nov 17 pulls are not in this workspace. When they arrive, `scripts/score_consensus.py` runs the existing Nov 17 consensus gate, and `--band-calibration` plus `--tight-calibration` print the two band diagnostics. Those diagnostics are not part of the Nov 17 gate. Label: PREDICTED.",
            "",
        ]
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
