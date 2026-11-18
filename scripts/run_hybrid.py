"""Hybrid new-URL placement. Selection uses dates through 2026-11-16. Nov 17 is diagnostic.

Seen URLs keep yesterday's position. New URLs, and URLs whose page text changed,
are inserted where the model ranks them against the surviving URLs. Label: PREDICTED.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections import defaultdict
from pathlib import Path

from aeo.ranksim.consensus import ABSENT_RANK, consensus_from_pulls, median_position
from aeo.ranksim.hybrid import (
    bootstrap_difference,
    covers_three_wide,
    insert_into_persistence,
    new_url_scores,
    pool_new,
    positions_from_order,
    rate,
)
from aeo.ranksim.pair_order import Snap, SnapRow, build_transition, fit_experiment, load_model, load_offpage_cache, load_serp_snapshot
from aeo.ranksim.position_metrics import bootstrap_rate
from aeo.ranksim.site_history import site_key

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.score_consensus import _deep_ranks, _read_serp  # noqa: E402

SNAP_ROOT = Path("/tmp/ranksim-snaps")
PROOF = Path("/tmp/ranksim-proof")
FREEZE = ROOT / "evals/preregistered/forward-pair-2026-11-16"
CACHE = Path("/tmp/ranksim-page-cache-v1.pkl")
OUT = ROOT / "evals/experiments-2026-11-16/hybrid.md"
CALIBRATION = ROOT / "evals/preregistered/hybrid-2026-11-19/hybrid.json"
DAYS = ("2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16")


def _directory(day: str) -> Path:
    matches = [path for path in SNAP_ROOT.iterdir() if path.is_dir() and day in path.name]
    if len(matches) != 1:
        raise SystemExit(f"Expected one snapshot for {day}")
    return matches[0]


def _index(snap: Snap) -> dict[str, dict[str, tuple[str, int, str]]]:
    found: dict[str, dict[str, tuple[str, int, str]]] = defaultdict(dict)
    for row in snap.rows:
        position = int(round(row.position))
        current = found[row.query].get(row.url_key)
        if current is None or position < current[1]:
            found[row.query][row.url_key] = (row.url, position, row.site or site_key(row.url))
    return found


def _page_hash(page) -> str:
    if page is None or not getattr(page, "present", False):
        return ""
    text = f"{page.title}\n{page.h1}\n{page.body}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _changed(previous_pages, today_pages, key: str) -> bool:
    before = _page_hash(previous_pages.get(key) if previous_pages else None)
    after = _page_hash(today_pages.get(key) if today_pages else None)
    return bool(before and after and before != after)


def _median_int(values: list[int]) -> int:
    return median_position(values)


def _query_rows(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        grouped[row["query"]].append(row)
    return grouped


def _pairs(records: list[dict], key: str) -> list[tuple[float, float]]:
    grouped = _query_rows(records)
    pairs = []
    for query in sorted(grouped):
        if key == "top3":
            cases = [row for row in grouped[query] if row["top3_case"]]
            if not cases:
                continue
            pairs.append((sum(row["top3_hit"] for row in cases), float(len(cases))))
        else:
            pairs.append((sum(row[key] for row in grouped[query]), float(len(grouped[query]))))
    return pairs


def _fmt(pairs: list[tuple[float, float]]) -> str:
    if not pairs:
        return "n/a"
    numerator = sum(item[0] for item in pairs)
    denominator = sum(item[1] for item in pairs)
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(pairs, skip_empty=True)
    return f"{numerator:.0f}/{denominator:.0f} ({numerator / denominator:.3f}) [{low:.3f}, {high:.3f}]"


def _place(query_rows: list[dict], scores: dict[str, float]) -> None:
    anchors = [row["url_key"] for row in sorted(
        (row for row in query_rows if row["seen"] and not row["changed"]),
        key=lambda row: (row["previous"], row["url_key"]),
    )]
    placed = [row["url_key"] for row in query_rows if row["new"] or row["changed"]]
    order = insert_into_persistence(anchors, placed, scores)
    positions = positions_from_order(order)
    for row in query_rows:
        if row["url_key"] in positions:
            row["hybrid"] = positions[row["url_key"]]


def _score_new(rows: list[dict], prediction: str, median_slot: int) -> list[dict]:
    scored = []
    for row in rows:
        # A median of 11 means the URL did not stick in the top 10. Those rows are not placements.
        if not row["new"] or int(row["truth"]) >= ABSENT_RANK:
            continue
        if prediction == "hybrid":
            pred = int(row["hybrid"])
        elif prediction == "median":
            pred = median_slot
        elif prediction == "absent":
            pred = ABSENT_RANK
        elif prediction == "domain":
            pred = int(row["domain_prior"])
        else:
            raise ValueError(prediction)
        item = new_url_scores(pred, row["truth"], tuple(row["shown"]))
        item["query"] = row["query"]
        scored.append(item)
    return scored


def _coverage(scored: list[dict]) -> float:
    totals = pool_new(scored)
    return rate(totals["three_wide"], totals["count"])


def main() -> int:
    print("Loading history through Nov 16", flush=True)
    cached = pickle.loads(CACHE.read_bytes())
    pages = cached["pages"]
    snaps = {day: load_serp_snapshot(_directory(day), day) for day in DAYS}
    indexed = {day: _index(snaps[day]) for day in DAYS}
    offpage = load_offpage_cache(FREEZE / "offpage.tsv")
    pulls = [
        _read_serp(PROOF / "2026-11-16-q100/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-a/serp_serper_live.csv"),
        _read_serp(PROOF / "2026-11-16-noise-b/serp_serper_live.csv"),
    ]
    deep = _deep_ranks(PROOF / "2026-11-16-deep/serp_serper_deep.csv")
    nov16_today: dict[str, dict[str, tuple[str, int, str, tuple[int, ...]]]] = defaultdict(dict)
    for query in sorted(set.intersection(*(set(pull) for pull in pulls))):
        rows = consensus_from_pulls(query, [pull[query] for pull in pulls], deep.get(query, {}))
        for row in rows:
            nov16_today[query][row.url_key] = (row.url, row.consensus, site_key(row.url), tuple(position for position in row.shown_positions))
    # Single-pull days use that day's top 10. Nov 16 uses the three-pull consensus.
    outcomes = {day: indexed[day] for day in DAYS if day != "2026-11-16"}
    outcomes["2026-11-16"] = {
        query: {key: (url, position, site) for key, (url, position, site, _shown) in urls.items()}
        for query, urls in nov16_today.items()
    }
    shown = {
        "2026-11-16": {query: {key: shown for key, (_url, _pos, _site, shown) in urls.items()} for query, urls in nov16_today.items()}
    }
    order = list(DAYS)
    built_rows = {}
    for index, day in enumerate(order):
        if index == 0:
            continue
        history = [snaps[name] for name in order[:index]]
        outcome_rows = []
        for query, urls in outcomes[day].items():
            for key, (url, position, site) in urls.items():
                outcome_rows.append(SnapRow(query=query, url=url, position=float(position), title="", url_key=key, site=site))
        outcome = Snap(day=day, rows=outcome_rows)
        page_map = pages.get(day, {})
        built_rows[day] = build_transition(history, outcome, page_map, offpage)
        print(f"  {day}: {len(built_rows[day])} rows", flush=True)
    print("Fitting the Nov 11 model on Nov 9", flush=True)
    scores = {}
    model_nov11 = fit_experiment("lambdamart_monotone", built_rows["2026-11-09"])
    scores["2026-11-11"] = {
        (row.query, row.url_key): float(score)
        for row, score in zip(built_rows["2026-11-11"], model_nov11.predict(built_rows["2026-11-11"]), strict=True)
    }
    scores["2026-11-13"] = {
        (row.query, row.url_key): float(score)
        for row, score in zip(
            built_rows["2026-11-13"],
            load_model(FREEZE / "model-heldout-nov13.json").predict(built_rows["2026-11-13"]),
            strict=True,
        )
    }
    scores["2026-11-16"] = {
        (row.query, row.url_key): float(score)
        for row, score in zip(
            built_rows["2026-11-16"],
            load_model(FREEZE / "model-heldout-nov16.json").predict(built_rows["2026-11-16"]),
            strict=True,
        )
    }
    day_rows = {}
    for day, previous in (("2026-11-11", "2026-11-09"), ("2026-11-13", "2026-11-11"), ("2026-11-16", "2026-11-13")):
        records = []
        prev_pages = pages.get(previous, {})
        today_pages = pages.get(day, {})
        for query, urls in outcomes[day].items():
            prior = outcomes[previous].get(query, {})
            for key, (url, truth, site) in urls.items():
                seen = key in prior
                changed = seen and _changed(prev_pages, today_pages, key)
                domain_prior = ABSENT_RANK
                for other, (_other_url, position, other_site) in prior.items():
                    if other_site == site and position < domain_prior:
                        domain_prior = position
                if day == "2026-11-16":
                    observed = shown["2026-11-16"].get(query, {}).get(key, ())
                else:
                    observed = () if truth >= ABSENT_RANK else (truth,)
                records.append(
                    {
                        "query": query,
                        "url_key": key,
                        "seen": seen,
                        "new": not seen,
                        "changed": changed,
                        "previous": prior[key][1] if seen else ABSENT_RANK,
                        "truth": truth,
                        "shown": observed,
                        "domain_prior": domain_prior,
                        "hybrid": truth,
                    }
                )
            _place(
                [row for row in records if row["query"] == query],
                {key: scores[day].get((query, key), float("-inf")) for key in urls},
            )
        day_rows[day] = records
        print(f"  {day} new {sum(row['new'] for row in records)} changed {sum(row['changed'] for row in records)}", flush=True)
    early_truths = [
        row["truth"] for day in ("2026-11-11", "2026-11-13") for row in day_rows[day] if row["new"] and row["truth"] < ABSENT_RANK
    ]
    through_16 = early_truths + [
        row["truth"] for row in day_rows["2026-11-16"] if row["new"] and row["truth"] < ABSENT_RANK
    ]
    median_early = _median_int(early_truths)
    median_frozen = _median_int(through_16)
    print("Median new position through Nov 13", median_early, "through Nov 16", median_frozen, flush=True)

    def evaluate(day: str, median_slot: int) -> dict[str, list[dict]]:
        return {name: _score_new(day_rows[day], name, median_slot) for name in ("hybrid", "median", "absent", "domain")}

    reports = {
        "2026-11-11": evaluate("2026-11-11", median_early),
        "2026-11-13": evaluate("2026-11-13", median_early),
        "2026-11-16": evaluate("2026-11-16", median_early),
    }
    # The frozen median includes Nov 16. Nov 16's own table above uses the earlier median.
    frozen_reports_16 = evaluate("2026-11-16", median_frozen)

    def best_name(report: dict[str, list[dict]]) -> str:
        return max(("median", "absent", "domain"), key=lambda name: _coverage(report[name]))

    lines = [
        "# Hybrid new-URL placement",
        "",
        "Label: PREDICTED. Seen URLs keep yesterday's position. New URLs, and URLs whose crawled text changed, are inserted by the monotone LambdaMART score against those survivors. The Nov 18 gates were not changed.",
        "",
        "A new-URL row is a URL that was absent yesterday and whose consensus position is in the top 10. A URL that shows in one pull and is absent from the others has a median of 11 and is not a placement.",
        "",
        f"The median new-URL position on Nov 11 and Nov 13 is {median_early}. Including Nov 16 it is {median_frozen}. The Nov 19 gate uses {median_frozen}. The Nov 16 row below uses {median_early} so that day does not grade a baseline fit on itself.",
        "",
        "Nov 11 uses a model fit on Nov 9. Nov 13 uses `model-heldout-nov13`. Nov 16 uses `model-heldout-nov16`, which has not seen Nov 16 labels.",
        "",
        "| Day | System | Exact | 3-wide | Top-3 entry | Observed-set exact |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for day, report in reports.items():
        for name, label in (
            ("hybrid", "Hybrid"),
            ("median", "Median new position"),
            ("absent", "Position 11"),
            ("domain", "Domain prior"),
        ):
            lines.append(
                f"| {day} | {label} | {_fmt(_pairs(report[name], 'exact'))} | {_fmt(_pairs(report[name], 'three_wide'))} "
                f"| {_fmt(_pairs(report[name], 'top3'))} | {_fmt(_pairs(report[name], 'observed'))} |"
            )
        winner = best_name(report)
        point, low, high = bootstrap_difference(_pairs(report["hybrid"], "three_wide"), _pairs(report[winner], "three_wide"))
        lines.append(f"| {day} | Hybrid minus {winner} |  | {point:.3f} [{low:.3f}, {high:.3f}] |  |  |")
    def _vol(history_days: list[str], query: str, key: str) -> float:
        series = []
        seen_before = False
        for name in history_days:
            item = indexed[name].get(query, {}).get(key)
            if item is not None:
                series.append(item[1])
                seen_before = True
            elif seen_before:
                series.append(ABSENT_RANK)
        if len(series) < 2:
            return 0.0
        return sum(abs(series[i] - series[i - 1]) for i in range(1, len(series))) / (len(series) - 1)

    best_cap = None
    for cap in (0, 1, 2):
        hits = total = 0
        for day, previous in (("2026-11-11", "2026-11-09"), ("2026-11-13", "2026-11-11"), ("2026-11-16", "2026-11-13")):
            history_days = [name for name in DAYS if name < day]
            for row in day_rows[day]:
                if not row["seen"] or row["changed"]:
                    continue
                vol = _vol(history_days, row["query"], row["url_key"])
                half = min(cap, int(round(vol)))
                pred = int(row["previous"])
                low, high = max(1, pred - half), pred + half
                hits += int(low <= row["truth"] <= high)
                total += 1
        record = (hits / total if total else 0.0, -cap, cap, hits, total)
        if best_cap is None or record > best_cap:
            best_cap = record
    print("Seen-URL volatility cap", best_cap, flush=True)
    payload = {
        "label": "PREDICTED",
        "model": "evals/preregistered/forward-pair-2026-11-16/model-next.json",
        "model_sha256": "bc4de5022a44c91bdb481c46f28c5374ee5477d1990d55931a21a4ce0e2a8ea7",
        "selection_model_nov16": "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json",
        "median_new_position": median_frozen,
        "seen_band_half_width_cap": best_cap[2],
        "seen_band_rule": "half width is min(cap, round(mean absolute move across earlier days))",
        "median_without_nov16": median_early,
        "insertion": "count of surviving URLs with a higher model score; ties among new URLs break by model score then url key",
        "content_change": "page text hash differs on both days",
        "selection_through": "2026-11-16",
        "not_used_for_selection": ["2026-11-17", "2026-11-19"],
        "nov16_best_baseline": best_name(reports["2026-11-16"]),
        "nov16_difference_using_early_median": list(bootstrap_difference(
            _pairs(reports["2026-11-16"]["hybrid"], "three_wide"),
            _pairs(reports["2026-11-16"][best_name(reports["2026-11-16"])], "three_wide"),
        )),
    }
    CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("Scoring Nov 17 after the file was written", flush=True)
    nov17_pulls = [
        _read_serp(Path("/home/ubuntu/.cursor/projects/agent-repos-Ranksim/uploads/serp_2026-11-17-pull-a_f06e.csv")),
        _read_serp(Path("/home/ubuntu/.cursor/projects/agent-repos-Ranksim/uploads/serp_2026-11-17-pull-b_1130.csv")),
        _read_serp(Path("/home/ubuntu/.cursor/projects/agent-repos-Ranksim/uploads/serp_2026-11-17-pull-c_0d82.csv")),
    ]
    nov17_today = defaultdict(dict)
    for query in sorted(set.intersection(*(set(pull) for pull in nov17_pulls))):
        for row in consensus_from_pulls(query, [pull[query] for pull in nov17_pulls], {}):
            nov17_today[query][row.url_key] = (row.url, row.consensus, site_key(row.url), tuple(row.shown_positions))
    outcome_rows = []
    for query, urls in nov17_today.items():
        for key, (url, position, site, _shown) in urls.items():
            outcome_rows.append(SnapRow(query=query, url=url, position=float(position), title="", url_key=key, site=site))
    history = [snaps[day] for day in DAYS]
    nov17_built = build_transition(history, Snap(day="2026-11-17", rows=outcome_rows), pages.get("2026-11-16", {}), offpage)
    nov17_scores = {
        (row.query, row.url_key): float(score)
        for row, score in zip(nov17_built, load_model(FREEZE / "model-next.json").predict(nov17_built), strict=True)
    }
    records = []
    previous = outcomes["2026-11-16"]
    for query, urls in nov17_today.items():
        prior = previous.get(query, {})
        query_records = []
        for key, (url, truth, site, observed) in urls.items():
            seen = key in prior
            domain_prior = ABSENT_RANK
            for _other, (_u, position, other_site) in prior.items():
                if other_site == site and position < domain_prior:
                    domain_prior = position
            query_records.append(
                {
                    "query": query,
                    "url_key": key,
                    "seen": seen,
                    "new": not seen,
                    "changed": False,
                    "previous": prior[key][1] if seen else ABSENT_RANK,
                    "truth": truth,
                    "shown": observed,
                    "domain_prior": domain_prior,
                    "hybrid": truth,
                }
            )
        _place(query_records, {key: nov17_scores.get((query, key), float("-inf")) for key in urls})
        records.extend(query_records)
    nov17 = {name: _score_new(records, name, median_frozen) for name in ("hybrid", "median", "absent", "domain")}
    winner = best_name(nov17)
    point, low, high = bootstrap_difference(_pairs(nov17["hybrid"], "three_wide"), _pairs(nov17[winner], "three_wide"))
    lines.extend(
        [
            "",
            "## Nov 17 diagnostic, not used to choose",
            "",
            f"Model is `model-next`, which has not seen Nov 17. Median new position is the frozen value {median_frozen}. No Nov 17 HTML, so no page is marked content-changed.",
            "",
            "| System | Exact | 3-wide | Top-3 entry | Observed-set exact |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for name, label in (
        ("hybrid", "Hybrid"),
        ("median", "Median new position"),
        ("absent", "Position 11"),
        ("domain", "Domain prior"),
    ):
        lines.append(
            f"| {label} | {_fmt(_pairs(nov17[name], 'exact'))} | {_fmt(_pairs(nov17[name], 'three_wide'))} "
            f"| {_fmt(_pairs(nov17[name], 'top3'))} | {_fmt(_pairs(nov17[name], 'observed'))} |"
        )
    lines.append(f"| Hybrid minus {winner} |  | {point:.3f} [{low:.3f}, {high:.3f}] |  |  |")
    lines.append("")
    lines.append(
        "The Nov 19 gate passes only when this difference, on that day's new URLs, has a query-bootstrap lower bound above 0 against the best of the three baselines."
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUT.read_text())
    _ = frozen_reports_16
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
