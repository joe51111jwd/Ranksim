"""Calibrate position bands on the early windows and score later days. Does not refit the frozen model.

Nov 9 (query-group CV) and Nov 9 to Nov 11 choose the persistence half-width and the
model offsets. Nov 13 and Nov 16 are forward checks. Nov 16 is the only day with
three pulls. Label: PREDICTED.
"""

from __future__ import annotations

import json
import pickle
from collections import defaultdict
from pathlib import Path

from aeo.ranksim.bands import (
    ALPHA,
    choose_band_rule,
    choose_k,
    last_observed_band,
    mean_width,
    mean_winkler,
    model_band,
    observed_band,
    persistence_band,
    pool_scores,
    score_url,
    uniform_chance,
)
from aeo.ranksim.consensus import ABSENT_RANK, consensus_from_pulls, pull_rank
from aeo.ranksim.pair_order import (
    Snap,
    SnapRow,
    build_transition,
    fit_experiment,
    load_model,
    load_offpage_cache,
    load_serp_snapshot,
)
from aeo.ranksim.position_metrics import bootstrap_rate, ranks_from_scores
from aeo.ranksim.site_history import site_key

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.score_consensus import _deep_ranks, _read_serp  # noqa: E402
CACHE = Path("/tmp/ranksim-page-cache-v1.pkl")
SNAP_ROOT = Path("/tmp/ranksim-snaps")
PROOF = Path("/tmp/ranksim-proof")
FREEZE = ROOT / "evals/preregistered/forward-pair-2026-11-16"
OUT = ROOT / "evals/experiments-2026-11-16/band-metrics.md"
CALIBRATION = ROOT / "evals/preregistered/band-2026-11-18/band-calibration.json"
DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16")


def _directory(day: str) -> Path:
    matches = [path for path in SNAP_ROOT.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot for {day}")
    return matches[0]


def _index(snap: Snap) -> dict[str, dict[str, SnapRow]]:
    found: dict[str, dict[str, SnapRow]] = defaultdict(dict)
    for row in snap.rows:
        current = found[row.query].get(row.url_key)
        if current is None or row.position < current.position:
            found[row.query][row.url_key] = row
    return found


def _union(day: str, today: dict[str, dict[str, SnapRow]], previous: dict[str, dict[str, SnapRow]]) -> Snap:
    rows = []
    for query in sorted(set(today) | set(previous)):
        seen = set()
        for source in (today.get(query, {}), previous.get(query, {})):
            for key, row in source.items():
                if key in seen:
                    continue
                seen.add(key)
                position = row.position if key in today.get(query, {}) else float(ABSENT_RANK)
                rows.append(
                    SnapRow(
                        query=query,
                        url=row.url,
                        position=position,
                        title=row.title,
                        url_key=key,
                        site=row.site,
                        snippet=row.snippet,
                        serp_date=row.serp_date,
                    )
                )
    return Snap(day=day, rows=rows)


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


def _bands_for_single(today: dict[str, dict[str, SnapRow]], previous: dict[str, dict[str, SnapRow]] | None):
    found = []
    queries = set(today) if previous is None else set(today) | set(previous)
    for query in sorted(queries):
        current = today.get(query, {})
        prior = {} if previous is None else previous.get(query, {})
        for key in sorted(set(current) | set(prior)):
            if key in current:
                position = int(round(current[key].position))
                url = current[key].url
            else:
                position = ABSENT_RANK
                url = prior[key].url
            if key in prior:
                prev = int(round(prior[key].position))
                previous_band = (prev, prev, prev)
            else:
                previous_band = None
            row_positions = (position,)
            from aeo.ranksim.consensus import ConsensusUrl

            consensus_row = ConsensusUrl(query, url, key, position, row_positions, () if position >= ABSENT_RANK else (position,))
            found.append(observed_band(consensus_row, previous_band))
    return found


def _nov16_bands(previous: dict[str, dict[str, SnapRow]]):
    pulls = [
        _read_serp(PROOF / "2026-11-16-q100/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-a/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-b/serp_serper_live.csv"),
    ]
    deep = _deep_ranks(PROOF / "2026-11-16-deep/serp_serper_deep.csv")
    queries = sorted(set.intersection(*(set(pull) for pull in pulls)))
    found = []
    display_rows = []
    for query in queries:
        query_pulls = [pull[query] for pull in pulls]
        query_deep = deep.get(query, {})
        rows = consensus_from_pulls(query, query_pulls, query_deep)
        seen = {row.url_key for row in rows}
        prior = previous.get(query, {})
        for row in rows:
            previous_band = None
            if row.url_key in prior:
                prev = int(round(prior[row.url_key].position))
                previous_band = (prev, prev, prev)
            found.append(observed_band(row, previous_band))
            display_rows.append(
                SnapRow(
                    query=query,
                    url=row.url,
                    position=float(row.consensus),
                    title=prior[row.url_key].title if row.url_key in prior else "",
                    url_key=row.url_key,
                    site=site_key(row.url),
                    snippet=prior[row.url_key].snippet if row.url_key in prior else "",
                    serp_date=prior[row.url_key].serp_date if row.url_key in prior else "",
                )
            )
        for key, prior_row in prior.items():
            if key in seen:
                continue
            positions = []
            for pull in query_pulls:
                rank, _shown = pull_rank(key, {item: pos for item, (_url, pos) in pull.items()}, query_deep)
                positions.append(rank)
            from aeo.ranksim.consensus import ConsensusUrl

            prev = int(round(prior_row.position))
            consensus_row = ConsensusUrl(query, prior_row.url, key, prev, tuple(positions), ())
            found.append(observed_band(consensus_row, (prev, prev, prev)))
            display_rows.append(
                SnapRow(
                    query=query,
                    url=prior_row.url,
                    position=float(ABSENT_RANK),
                    title=prior_row.title,
                    url_key=key,
                    site=prior_row.site,
                    snippet=prior_row.snippet,
                    serp_date=prior_row.serp_date,
                )
            )
    return found, Snap(day="2026-11-16", rows=display_rows)


def _centers(bands, ranks: dict[tuple[str, str], int]) -> list[int]:
    return [ranks[(band.query, band.url_key)] for band in bands]


def _rank_map(rows, scores) -> dict[tuple[str, str], int]:
    predicted = ranks_from_scores(rows, scores)
    return {(row.query, row.url_key): predicted[index] for index, row in enumerate(rows)}


def _bucket(band) -> str:
    return "new" if band.previous_consensus is None else "surviving"


def _eval(bands, ranks: dict[tuple[str, str], int], k: int, rule: dict) -> dict[str, dict]:
    systems = {
        "persistence": [],
        "last_band": [],
        "model": [],
    }
    called = {name: {} for name in systems}
    for band in bands:
        specs = {
            "persistence": persistence_band(band, k),
            "last_band": last_observed_band(band),
            "model": model_band(ranks[(band.query, band.url_key)], rule, _bucket(band)),
        }
        for name, (low, high) in specs.items():
            systems[name].append((band.query, score_url(band, low, high)))
            if name == "model":
                center = ranks[(band.query, band.url_key)]
            else:
                center = ABSENT_RANK if band.previous_consensus is None else int(band.previous_consensus)
            current = called[name].get(band.query)
            if current is None or (center, band.url_key) < current[0]:
                called[name][band.query] = ((center, band.url_key), band, low, high)
    report = {}
    for name, rows in systems.items():
        grouped: dict[str, list] = defaultdict(list)
        for query, row in rows:
            grouped[query].append(row)
        pooled_rows = [row for _query, row in rows]
        totals = pool_scores(pooled_rows)

        def pairs(key_num: str, key_den: str) -> list[tuple[float, float]]:
            return [(sum(item[key_num] for item in grouped[query]), sum(item[key_den] for item in grouped[query])) for query in sorted(grouped)]

        one_hits = []
        one_chance = []
        top_hits = []
        top_chance = []
        for query, (_key, band, low, high) in sorted(called[name].items()):
            pulls = len(band.positions) or 1
            one_hits.append((sum(1 for position in band.positions if position == 1), pulls))
            top_hits.append((sum(1 for position in band.positions if position <= 3), pulls))
            one_chance.append(uniform_chance(low, high, top=1))
            top_chance.append(uniform_chance(low, high, top=3))
        report[name] = {
            "totals": totals,
            "coverage": pairs("coverage_hits", "coverage_count"),
            "containment": pairs("contained", "urls"),
            "width": pairs("width", "urls"),
            "winkler": pairs("winkler", "winkler_count"),
            "brier_one": pairs("brier_one", "brier_count"),
            "brier_top3": pairs("brier_top3", "brier_count"),
            "chance_one": pairs("chance_one", "brier_count"),
            "observed_one": pairs("observed_one", "brier_count"),
            "chance_top3": pairs("chance_top3", "brier_count"),
            "observed_top3": pairs("observed_top3", "brier_count"),
            "called_one_rate": one_hits,
            "called_one_chance": sum(one_chance) / len(one_chance) if one_chance else 0.0,
            "called_top_rate": top_hits,
            "called_top_chance": sum(top_chance) / len(top_chance) if top_chance else 0.0,
        }
    return report


def _fmt(pairs: list[tuple[float, float]], *, digits: int = 3) -> str:
    numerator = sum(item[0] for item in pairs)
    denominator = sum(item[1] for item in pairs)
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(pairs)
    return f"{numerator / denominator:.{digits}f} [{low:.{digits}f}, {high:.{digits}f}] n={denominator:.0f}"


def _line(name: str, report: dict) -> str:
    return (
        f"| {name} | {_fmt(report['winkler'])} | {_fmt(report['coverage'])} | {_fmt(report['containment'])} "
        f"| {_fmt(report['width'])} | {_fmt(report['brier_one'])} | {_fmt(report['brier_top3'])} |"
    )


def main() -> int:
    print("Loading snapshots", flush=True)
    cached = pickle.loads(CACHE.read_bytes())
    pages = cached["pages"]
    snaps = {day: load_serp_snapshot(_directory(day), day) for day in DAYS}
    indexed = {day: _index(snaps[day]) for day in DAYS}
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    order = list(DAYS)
    unions = {}
    built = {}
    for index, day in enumerate(order):
        if index == 0:
            continue
        previous = order[index - 1]
        unions[day] = _union(day, indexed[day], indexed[previous])
        history = [snaps[name] for name in order[:index]]
        built[day] = build_transition(history, unions[day], pages[day], offpage)
        print(f"  {day}: {len(built[day])} rows", flush=True)
    print("Scoring early windows", flush=True)
    scores = {}
    scores["2026-11-09"] = _cv_scores(built["2026-11-09"])
    model_nov11 = fit_experiment("lambdamart_monotone", built["2026-11-09"])
    scores["2026-11-11"] = [float(value) for value in model_nov11.predict(built["2026-11-11"])]
    scores["2026-11-13"] = [float(value) for value in load_model(FREEZE / "model-heldout-nov13.json").predict(built["2026-11-13"])]
    print("Nov 16 three-pull bands", flush=True)
    nov16_bands, nov16_snap = _nov16_bands(indexed["2026-11-13"])
    history = [snaps[day] for day in order[:-1]]
    nov16_rows = build_transition(history, nov16_snap, pages["2026-11-16"], offpage)
    nov16_scores = [float(value) for value in load_model(FREEZE / "model-heldout-nov16.json").predict(nov16_rows)]
    print(f"  nov16 union rows {len(nov16_rows)} observed urls {len(nov16_bands)}", flush=True)
    early_pairs_k = []
    early_pairs_model = []
    early_bands = {}
    for day, previous in (("2026-11-09", "2026-11-06"), ("2026-11-11", "2026-11-09")):
        bands = _bands_for_single(indexed[day], indexed[previous])
        ranks = _rank_map(built[day], scores[day])
        early_bands[day] = (bands, ranks)
        for band in bands:
            center = ABSENT_RANK if band.previous_consensus is None else int(band.previous_consensus)
            early_pairs_k.append((center, band.positions))
            early_pairs_model.append((ranks[(band.query, band.url_key)], band.positions, "new" if band.previous_consensus is None else "surviving"))
    k_rule = choose_k(early_pairs_k)
    model_rule = choose_band_rule(early_pairs_model)
    print("Chosen k", k_rule, flush=True)
    print("Chosen model offsets", model_rule["by_bucket"], model_rule["global"], flush=True)
    reports = {}
    for day, previous in (("2026-11-13", "2026-11-11"), ("2026-11-09", "2026-11-06"), ("2026-11-11", "2026-11-09")):
        bands = early_bands[day][0] if day in early_bands else _bands_for_single(indexed[day], indexed[previous])
        ranks = early_bands[day][1] if day in early_bands else _rank_map(built[day], scores[day])
        reports[day] = _eval(bands, ranks, k_rule["k"], model_rule)
    reports["2026-11-16"] = _eval(nov16_bands, _rank_map(nov16_rows, nov16_scores), k_rule["k"], model_rule)
    payload = {
        "label": "PREDICTED",
        "alpha": ALPHA,
        "winkler": "inclusive width plus (2/alpha) times the distance outside the band, averaged over pull positions including imputed 11s",
        "persistence_k": k_rule["k"],
        "persistence": k_rule,
        "model_rule": {
            "by_bucket": model_rule["by_bucket"],
            "global": model_rule["global"],
            "surviving": model_rule["surviving"],
            "new": model_rule["new"],
            "calibration_winkler": model_rule["calibration_winkler"],
            "calibration_coverage": model_rule["calibration_coverage"],
            "calibration_mean_width": model_rule["calibration_mean_width"],
        },
        "family": "lambdamart_monotone",
        "model": "evals/preregistered/forward-pair-2026-11-16/model-next.json",
        "model_sha256": "bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7",
        "diagnostic_model_sha256": "c4619100d0f9db5d4dbeb563e416284631f76589cd1b4f428a60e8fb88de30f3",
        "calibration_windows": ["2026-11-09 query-group CV", "2026-11-09 to 2026-11-11 forward"],
        "not_used_for_calibration": ["2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"],
        "nov17_gate_unchanged": True,
    }
    CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Position bands",
        "",
        "Label: PREDICTED. A URL's truth on a day is the min, the max, and the full list of its pull positions. A pull that omits it contributes 11, or a deep rank of 11 or worse when that rank is known.",
        "",
        f"Alpha for the Winkler score is {ALPHA}, fixed before these forward days. The score is the inclusive width, plus {2 / ALPHA:.0f} times the distance outside the band. Lower is better.",
        "",
        f"Persistence half-width k={k_rule['k']} and the model offsets were chosen on Nov 9 and Nov 11 only, by the lowest Winkler score. Nov 13 and Nov 16 did not choose them.",
        "",
        f"Model offsets, by bucket: surviving {model_rule['surviving']['low_offset']} to {model_rule['surviving']['high_offset']}, new URLs {model_rule['new']['low_offset']} to {model_rule['new']['high_offset']}. Calibration Winkler {model_rule['calibration_winkler']:.3f}, coverage {model_rule['calibration_coverage']:.3f}, mean width {model_rule['calibration_mean_width']:.3f}.",
        f"Persistence calibration Winkler {k_rule['calibration_winkler']:.3f}, coverage {k_rule['calibration_coverage']:.3f}, mean width {k_rule['calibration_mean_width']:.3f}.",
        "",
        "The model band is centered on the model's rank inside that day's URL set, then shifted by the frozen offsets. It is not a raw Google position. The Nov 16 model is `model-heldout-nov16`, fit through Nov 13.",
        "",
        "There is one 3-pull day, Nov 16. The cross-day ceiling, how well one day's band predicts the next day's band, waits for a second 3-pull day.",
        "",
        "## Forward checks",
        "",
        "| Day | System | Winkler | Coverage | Full containment | Mean width | #1 Brier | Top-3 Brier |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for day in ("2026-11-13", "2026-11-16"):
        for name, label in (("persistence", "Persistence ± k"), ("last_band", "Last observed band"), ("model", "Model band")):
            lines.append(f"| {day} " + _line(label, reports[day][name]))
    lines.extend(
        [
            "",
            "Nov 13 is one pull, so each observed band is a single position. Nov 16 is three pulls plus deep ranks for absences.",
            "",
            "On Nov 16 the model band's Winkler score is 27.043 against persistence 17.770, and its coverage is 0.699 against persistence 0.834. That forward check would not pass the Nov 18 gate. It was not used to change k or the offsets.",
            "",
        ]
    )
    lines.append("## Called URL versus how often it lands there")
    lines.append("")
    lines.append("The called URL is the one with the best center in the query. Its predicted chance is the share of its band that covers the spot. The observed rate is the share of pulls that put it there.")
    lines.append("")
    lines.append("| Day | System | Predicted #1 chance | Observed #1 rate | Predicted top-3 chance | Observed top-3 rate |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for day in ("2026-11-13", "2026-11-16"):
        for name, label in (("persistence", "Persistence ± k"), ("last_band", "Last observed band"), ("model", "Model band")):
            item = reports[day][name]
            lines.append(
                f"| {day} | {label} | {item['called_one_chance']:.3f} | {_fmt(item['called_one_rate'])} | {item['called_top_chance']:.3f} | {_fmt(item['called_top_rate'])} |"
            )
    lines.extend(
        [
            "",
            "## Added diagnostic for 2026-11-17",
            "",
            "The 2026-11-17 consensus gate is unchanged. These pulls are not in this workspace, so there is no Nov 17 band number yet.",
            "When the three pulls exist, `scripts/score_consensus.py --band-calibration evals/preregistered/band-2026-11-18/band-calibration.json` prints this same table and labels it ADDED DIAGNOSTIC. That print is not part of the Nov 17 gate.",
            "",
        ]
    )
    # Silence unused locals from the early reports' winkler helper if a day was calibration-only.
    _ = (mean_winkler, mean_width, reports["2026-11-09"])
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
