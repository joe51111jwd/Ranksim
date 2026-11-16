"""Score one later snapshot with the frozen full-coverage line.

The frozen command always uses 2026-11-11 as the previous snapshot. This module
only changes that input: the caller passes the before file, and it is appended
after the frozen history. Persistence is the negative of the position on that
before file. Coefficients, the gate, and full_coverage.py are not changed.

Scores are PREDICTED. Outcome positions are REAL. They are not features.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timezone
from pathlib import Path

from aeo.ranksim.full_coverage import (
    HISTORY,
    PAIR_MODES,
    PARAMS_REL,
    PREVIOUS_SNAPSHOT_DAY,
    _by_query,
    _coverage,
    _feature_payload,
    _load_named_history,
    _mode_keeps,
    _parse_day,
    _refuse_write,
    append_track_record,
    assert_pinned,
    evaluate_gate,
    file_sha256,
    load_html_dir,
    load_params,
    load_position_csv,
    rank_metrics,
    repo_root,
    score_outcome,
    site_key,
)
from aeo.ranksim.new_page import _percentile

FORWARD_BOOTSTRAP_DRAWS = 2000
FORWARD_BOOTSTRAP_SEED = 20261116
DOMAIN_CONTEXT_HOST = "smartcrewhq.com"


def history_with_before(root: Path, before_csv: Path, before_day: str) -> list[tuple[str, list]]:
    """Frozen Nov 6, Nov 9, and Nov 11 lists, then the before snapshot last."""
    day = _parse_day(before_day)
    if day <= date.fromisoformat(PREVIOUS_SNAPSHOT_DAY):
        raise SystemExit(
            f"The before snapshot must be after {PREVIOUS_SNAPSHOT_DAY}. "
            "The frozen command already uses that day as the previous snapshot."
        )
    if not before_csv.is_file():
        raise SystemExit(f"Missing before snapshot {before_csv}")
    digest = file_sha256(before_csv)
    for item in HISTORY:
        if digest == item["sha256"]:
            raise SystemExit(f"The before snapshot is the frozen {item['day']} history file.")
    history = _load_named_history(root, {item["day"] for item in HISTORY})
    history.append((before_day, load_position_csv(before_csv, before_day)))
    return history


def per_query_pair_counts(queries: dict[str, list[dict]], score_name: str, mode: str) -> dict[str, tuple[int, int, int]]:
    """Correct, wrong, ties for one query. The sums match rank_metrics."""
    found: dict[str, tuple[int, int, int]] = {}
    for query, rows in queries.items():
        correct = wrong = ties = 0
        saw = False
        for left in range(len(rows)):
            for right in range(len(rows)):
                if rows[left]["position"] >= rows[right]["position"]:
                    continue
                if not _mode_keeps(rows[left]["bucket"], rows[right]["bucket"], mode):
                    continue
                saw = True
                left_score = rows[left]["scores"][score_name]
                right_score = rows[right]["scores"][score_name]
                if left_score > right_score:
                    correct += 1
                elif left_score < right_score:
                    wrong += 1
                else:
                    ties += 1
        if saw:
            found[query] = (correct, wrong, ties)
    return found


def pair_coverage(queries: dict[str, list[dict]]) -> dict:
    """Share of REAL ordered pairs that have a finite PREDICTED score on both URLs."""
    outcome_pairs = 0
    predicted_pairs = 0
    for rows in queries.values():
        for left in range(len(rows)):
            for right in range(len(rows)):
                if rows[left]["position"] >= rows[right]["position"]:
                    continue
                outcome_pairs += 1
                left_score = rows[left]["scores"]["full_coverage"]
                right_score = rows[right]["scores"]["full_coverage"]
                if math.isfinite(left_score) and math.isfinite(right_score):
                    predicted_pairs += 1
    return {
        "outcome_pairs": outcome_pairs,
        "predicted_pairs": predicted_pairs,
        "share": None if outcome_pairs == 0 else predicted_pairs / outcome_pairs,
    }


def _rate(triple: tuple[int, int, int]) -> float | None:
    total = sum(triple)
    if total == 0:
        return None
    return triple[0] / total


def pooled_query_bootstrap(
    windows: list[dict[str, dict[str, tuple[int, int, int]]]],
    *,
    draws: int = FORWARD_BOOTSTRAP_DRAWS,
    seed: int = FORWARD_BOOTSTRAP_SEED,
) -> dict:
    """Query bootstrap of pooled pairwise accuracy and its margin over the baseline.

    The resample unit is the query. Both windows of a drawn query stay together.
    The rate pools pairs. It is not the mean of per-query rates.
    """
    if draws < 1:
        raise ValueError("bootstrap draws must be positive")
    if not windows:
        raise ValueError("bootstrap needs at least one window")
    normalized: list[tuple[dict, dict]] = []
    for window in windows:
        candidate = window["candidate"]
        baseline = window["baseline"]
        if set(candidate) != set(baseline):
            raise ValueError("candidate and baseline queries differ inside a window")
        normalized.append((candidate, baseline))
    names = sorted({query for candidate, _baseline in normalized for query in candidate})
    if not names:
        raise ValueError("bootstrap has no queries")

    def accumulate(picked: list[str]) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        left = [0, 0, 0]
        right = [0, 0, 0]
        for query in picked:
            for candidate, baseline in normalized:
                if query not in candidate:
                    continue
                for index, value in enumerate(candidate[query]):
                    left[index] += int(value)
                    right[index] += int(baseline[query][index])
        return (left[0], left[1], left[2]), (right[0], right[1], right[2])

    point_left, point_right = accumulate(names)
    rng = random.Random(seed)
    combined_samples: list[float] = []
    margin_samples: list[float] = []
    for _ in range(draws):
        picked = [names[rng.randrange(len(names))] for _ in names]
        left, right = accumulate(picked)
        left_rate = _rate(left)
        right_rate = _rate(right)
        if left_rate is None or right_rate is None:
            continue
        combined_samples.append(left_rate)
        margin_samples.append(left_rate - right_rate)
    combined_samples.sort()
    margin_samples.sort()
    left_rate = _rate(point_left)
    right_rate = _rate(point_right)
    return {
        "seed": seed,
        "draws": draws,
        "queries": len(names),
        "unit": "query",
        "combined_correct": point_left[0],
        "combined_wrong": point_left[1],
        "combined_ties": point_left[2],
        "combined_pairs": sum(point_left),
        "combined_pairwise": left_rate,
        "baseline_correct": point_right[0],
        "baseline_wrong": point_right[1],
        "baseline_ties": point_right[2],
        "baseline_pairs": sum(point_right),
        "baseline_pairwise": right_rate,
        "margin": None if left_rate is None or right_rate is None else left_rate - right_rate,
        "combined_ci": [None, None] if not combined_samples else [_percentile(combined_samples, 0.025), _percentile(combined_samples, 0.975)],
        "margin_ci": [None, None] if not margin_samples else [_percentile(margin_samples, 0.025), _percentile(margin_samples, 0.975)],
    }


def _score_loaded(
    *,
    root: Path,
    history: list[tuple[str, list]],
    outcome_csv: Path,
    outcome_day: str,
    html_dir: Path | None,
) -> dict:
    if not outcome_csv.is_file():
        raise SystemExit(f"Missing outcome snapshot {outcome_csv}")
    outcome_hash = file_sha256(outcome_csv)
    for item in HISTORY:
        if outcome_hash == item["sha256"]:
            raise SystemExit(f"The outcome CSV is the frozen {item['day']} history file.")
    params = load_params(root / PARAMS_REL)
    html_info = {"html_files": 0, "missing_files": 0, "empty_files": 0, "attached": False}
    html_by_url: dict[str, str] = {}
    if html_dir is not None:
        if not html_dir.is_dir():
            raise SystemExit(f"--html-dir is not a directory: {html_dir}")
        html_by_url, html_info = load_html_dir(html_dir)
        html_info = dict(html_info)
        html_info["attached"] = True
    outcome_rows = load_position_csv(outcome_csv, outcome_day)
    queries = score_outcome(history, outcome_rows, params, html_by_url=html_by_url)
    names = ("full_coverage", "persistence_ranked_last", "bm25")
    metrics = {name: rank_metrics(queries, name) for name in names}
    gate = evaluate_gate(metrics["full_coverage"], metrics["persistence_ranked_last"])
    per_query = {
        name: {mode: per_query_pair_counts(queries, name, mode) for mode in PAIR_MODES}
        for name in ("full_coverage", "persistence_ranked_last")
    }
    manifest = json.loads((root / "evals/preregistered/next-snapshot/manifest.json").read_text(encoding="utf-8"))
    previous_day = history[-1][0]
    return {
        "root": root,
        "history_days": [day for day, _rows in history],
        "before_day": previous_day,
        "outcome_day": outcome_day,
        "snapshot_sha256": outcome_hash,
        "params_sha256": file_sha256(root / PARAMS_REL),
        "scorer_sha256": file_sha256(root / "src/aeo/ranksim/full_coverage.py"),
        "code_commit": manifest.get("code_commit"),
        "html": html_info,
        "queries": queries,
        "outcome_rows": outcome_rows,
        "metrics": metrics,
        "gate": gate,
        "coverage": _coverage(queries),
        "pair_coverage": pair_coverage(queries),
        "per_query": per_query,
        "overlap": _overlap(history[-1][1], outcome_rows),
    }


def _overlap(before_rows: list, outcome_rows: list) -> dict:
    before_keys = {(row.query, row.url_key) for row in before_rows}
    outcome_keys = {(row.query, row.url_key) for row in outcome_rows}
    return {
        "before_rows": len(before_rows),
        "before_keys": len(before_keys),
        "outcome_rows": len(outcome_rows),
        "outcome_keys": len(outcome_keys),
        "before_rows_still_present": sum(1 for row in before_rows if (row.query, row.url_key) in outcome_keys),
        "outcome_rows_seen_before": sum(1 for row in outcome_rows if (row.query, row.url_key) in before_keys),
    }


def score_transition(
    *,
    root: Path | None = None,
    before_csv: Path,
    before_day: str,
    outcome_csv: Path,
    outcome_day: str,
    html_dir: Path | None = None,
) -> dict:
    """Score outcome against the before snapshot. Does not write and does not refit."""
    root = root or repo_root()
    if _parse_day(outcome_day) <= _parse_day(before_day):
        raise SystemExit("The outcome day must be after the before snapshot.")
    assert_pinned(root)
    history = history_with_before(root, before_csv, before_day)
    if file_sha256(outcome_csv) == file_sha256(before_csv):
        raise SystemExit("The outcome CSV is the before snapshot. It is not a new outcome.")
    scored = _score_loaded(
        root=root,
        history=history,
        outcome_csv=outcome_csv,
        outcome_day=outcome_day,
        html_dir=html_dir,
    )
    scored["before_sha256"] = file_sha256(before_csv)
    scored["before_csv"] = str(before_csv)
    scored["outcome_csv"] = str(outcome_csv)
    return scored


def score_frozen_window(
    *,
    root: Path | None = None,
    outcome_csv: Path,
    outcome_day: str,
    html_dir: Path | None = None,
) -> dict:
    """Recompute a window whose previous snapshot is the frozen 2026-11-11 file."""
    root = root or repo_root()
    if _parse_day(outcome_day) <= date.fromisoformat(PREVIOUS_SNAPSHOT_DAY):
        raise SystemExit(f"The frozen window outcome must be after {PREVIOUS_SNAPSHOT_DAY}.")
    assert_pinned(root)
    history = _load_named_history(root, {item["day"] for item in HISTORY})
    return _score_loaded(
        root=root,
        history=history,
        outcome_csv=outcome_csv,
        outcome_day=outcome_day,
        html_dir=html_dir,
    )


def domain_appearances(queries: dict[str, list[dict]], host: str) -> list[dict]:
    found = []
    for query, rows in queries.items():
        for row in rows:
            if site_key(row["url"]) != host:
                continue
            position = row["position"]
            found.append(
                {
                    "query": query,
                    "position": int(position) if float(position).is_integer() else position,
                    "url": row["url"],
                    "bucket": row["bucket"],
                }
            )
    found.sort(key=lambda item: (item["query"], item["position"], item["url"]))
    return found


def _counts_match(block: dict, published: dict) -> bool:
    return (
        int(block["correct"]),
        int(block["wrong"]),
        int(block["ties"]),
        int(block["pairs"]),
    ) == (
        int(published["correct"]),
        int(published["wrong"]),
        int(published["ties"]),
        int(published["pairs"]),
    )


def published_window_matches(scored: dict, published: dict) -> list[str]:
    """Differences between a recomputed window and a published report.json. Empty means a match."""
    mismatches = []
    for scorer in ("full_coverage", "persistence_ranked_last"):
        for mode in PAIR_MODES:
            if not _counts_match(scored["metrics"][scorer][mode], published["metrics"][scorer][mode]):
                mismatches.append(f"{scorer}.{mode}")
        for name in ("ndcg_at_10", "mean_spearman"):
            left = scored["metrics"][scorer][name]
            right = published["metrics"][scorer][name]
            if left != right:
                mismatches.append(f"{scorer}.{name}")
    return mismatches


def _json_counts(per_query: dict) -> dict:
    converted = {}
    for scorer, modes in per_query.items():
        converted[scorer] = {}
        for mode, queries in modes.items():
            converted[scorer][mode] = {query: list(triple) for query, triple in sorted(queries.items())}
    return converted


def _already_recorded(ledger: Path, snapshot_hash: str) -> bool:
    if not ledger.exists():
        return False
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("snapshot_sha256") == snapshot_hash and row.get("kind") in {"run", "metrics"}:
            return True
    return False


def append_transition(ledger: Path, scored: dict, *, created_at: str | None = None) -> str:
    """Append one PREDICTED run, PREDICTED scores, REAL positions, and a SCORE row."""
    root = Path(scored.get("root") or repo_root())
    _refuse_write(Path(ledger), root)
    snapshot_hash = scored["snapshot_sha256"]
    if _already_recorded(Path(ledger), snapshot_hash):
        raise SystemExit(f"Snapshot {snapshot_hash} is already in {ledger}.")
    created_at = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rows: list[dict] = [
        {
            "label": "PREDICTED",
            "kind": "run",
            "confidence": "UNSURE",
            "created_at": created_at,
            "observed_on": scored["outcome_day"],
            "previous_day": scored["before_day"],
            "history_days": list(scored["history_days"]),
            "snapshot_sha256": snapshot_hash,
            "before_sha256": scored.get("before_sha256", ""),
            "params_sha256": scored["params_sha256"],
            "scorer_sha256": scored["scorer_sha256"],
            "code_commit": scored.get("code_commit"),
            "html": scored.get("html") or {},
            "note": (
                "PREDICTED. Outcome positions were not features. The line was not refit. "
                "The before snapshot was supplied as an input path after the frozen history."
            ),
        }
    ]
    for query in sorted(scored["queries"]):
        ordered = sorted(scored["queries"][query], key=lambda item: (item["position"], item["url"]))
        for row in ordered:
            rows.append(
                {
                    "label": "PREDICTED",
                    "kind": "score",
                    "confidence": "UNSURE",
                    "created_at": created_at,
                    "observed_on": scored["outcome_day"],
                    "snapshot_sha256": snapshot_hash,
                    "scorer": "full_coverage",
                    "query": row["query"],
                    "url": row["url"],
                    "bucket": row["bucket"],
                    "score": row["scores"]["full_coverage"],
                    "predicted_position": row["predicted_position"],
                    "persistence_ranked_last": row["scores"]["persistence_ranked_last"],
                    "bm25": row["scores"]["bm25"],
                    "features": _feature_payload(row),
                    "note": "PREDICTED. Not a Google rank.",
                }
            )
            rows.append(
                {
                    "label": "REAL",
                    "kind": "outcome",
                    "observed_on": scored["outcome_day"],
                    "snapshot_sha256": snapshot_hash,
                    "query": row["query"],
                    "url": row["url"],
                    "position": row["position"],
                    "note": "REAL outcome position from the snapshot CSV. It was not a feature.",
                }
            )
    rows.append(
        {
            "label": "SCORE",
            "kind": "metrics",
            "observed_on": scored["outcome_day"],
            "previous_day": scored["before_day"],
            "snapshot_sha256": snapshot_hash,
            "coverage": scored["coverage"],
            "pair_coverage": scored["pair_coverage"],
            "metrics": scored["metrics"],
            "gate": scored["gate"],
            "per_query": _json_counts(scored["per_query"]),
            "lambdamart": "not scored. The frozen booster and the training HTML are not in git, and this command does not refit.",
            "note": "SCORE of PREDICTED full-coverage scores against REAL positions. The gate text was not changed.",
        }
    )
    return append_track_record(Path(ledger), rows)


def append_pooled(ledger: Path, pooled: dict, *, observed_on: str, snapshot_sha256: str, note: str) -> str:
    """Append the pooled summary. It is not a new PREDICTED score and not a REAL observation."""
    row = {
        "label": "SCORE",
        "kind": "pooled",
        "observed_on": observed_on,
        "snapshot_sha256": snapshot_sha256,
        "pooled": {
            key: pooled[key]
            for key in (
                "seed",
                "draws",
                "queries",
                "unit",
                "combined_correct",
                "combined_wrong",
                "combined_ties",
                "combined_pairs",
                "combined_pairwise",
                "baseline_correct",
                "baseline_wrong",
                "baseline_ties",
                "baseline_pairs",
                "baseline_pairwise",
                "margin",
                "combined_ci",
                "margin_ci",
            )
        },
        "note": note,
    }
    return append_track_record(Path(ledger), [row])


def _metric(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"


def _cell(block: dict) -> str:
    if not block or not block.get("pairs"):
        return "0 pairs"
    rate = block.get("pairwise")
    rate_text = "n/a" if rate is None else f"{rate:.6f}"
    text = f"{block['correct']}/{block['pairs']} ({rate_text})"
    wrong = block.get("wrong") or 0
    ties = block.get("ties") or 0
    if wrong or ties:
        text += f", {wrong} wrong, {ties} ties"
    queries = block.get("queries")
    if queries is not None:
        text += f". {queries} queries"
    return text


def _ratio(correct: int, total: int) -> str:
    if total == 0:
        return "0 pairs"
    return f"{correct}/{total} ({correct / total:.6f})"


def render_forward_report(result: dict) -> str:
    gate = result["gate"]
    combined = result["metrics"]["full_coverage"]
    persistence = result["metrics"]["persistence_ranked_last"]
    coverage = result["coverage"]
    pairs = result["pair_coverage"]
    pooled = result["pooled"]
    share = pairs["share"]
    share_text = "n/a" if share is None else f"{share:.6f}"
    lines = [
        f"# {result['outcome_day']} full-coverage score",
        "",
        "Label: PREDICTED for every score. Label: REAL for every outcome position. `ranker_change` is `none`.",
        "",
        "The scorer, coefficients, and gate text are the frozen full-coverage line. "
        "This run did not refit them. The frozen command hardcodes 2026-11-11 as the previous snapshot. "
        f"The input path here appends the {result['before_day']} position file after the frozen Nov 6, Nov 9, and Nov 11 lists. "
        f"Persistence is the negative of the {result['before_day']} position. "
        "Domain history includes that before file because it is history, not the outcome. "
        "That path change is outside `full_coverage.py`.",
        "",
        "## Gate",
        "",
        "The pre-registered rule is unchanged. A pass does not change the product ranker, the fitted parameters, or the frozen files.",
        "",
        f"Gate pass: {str(bool(gate['pass'])).lower()}. ranker_change: {gate['ranker_change']}.",
        "",
        f"| Check | Result |",
        f"| --- | --- |",
        f"| All-pairs pairwise accuracy strictly greater than persistence-ranked-last | {gate['checks']['beats_persistence_ranked_last_on_all_pairs']}. Combined {_cell(combined['all'])}. Persistence-ranked-last {_cell(persistence['all'])}. |",
        f"| New-versus-surviving pairwise accuracy strictly greater than 0.5 | {gate['checks']['beats_chance_on_new_vs_surviving']}. {_cell(combined['new_vs_surviving'])}. |",
        f"| New-versus-surviving denominator at least 100 pairs | {gate['checks']['new_vs_surviving_pairs_at_least_minimum']}. {gate['new_vs_surviving_pairs']} pairs. |",
        "",
        "## Pairwise table",
        "",
        "Pairwise accuracy is correct / (correct + wrong + ties). A score tie is not correct. "
        "Pairs with equal REAL positions are not in the denominator. "
        "NDCG@10 and mean Spearman are means over queries, secondary to the gate. Higher PREDICTED scores are better. "
        "Frozen LambdaMART is not in this command.",
        "",
        "| Pair type | Combined (PREDICTED) | Persistence-ranked-last |",
        "| --- | --- | --- |",
        f"| All | {_cell(combined['all'])}. NDCG@10 {_metric(combined['ndcg_at_10'])}. Spearman {_metric(combined['mean_spearman'])}. | {_cell(persistence['all'])}. NDCG@10 {_metric(persistence['ndcg_at_10'])}. Spearman {_metric(persistence['mean_spearman'])}. |",
        f"| Surviving vs surviving | {_cell(combined['surviving_surviving'])}. | {_cell(persistence['surviving_surviving'])}. |",
        f"| New vs surviving | {_cell(combined['new_vs_surviving'])}. | {_cell(persistence['new_vs_surviving'])}. |",
        f"| New vs new | {_cell(combined['new_vs_new'])}. | {_cell(persistence['new_vs_new'])}. |",
        "",
        "## Coverage",
        "",
        f"REAL rows scored: {coverage['scored_rows']}/{coverage['outcome_rows']}. "
        f"Surviving rows {coverage.get('surviving_rows')}. New rows {coverage.get('new_rows')}. "
        + (
            f"Domain seen on {coverage['new_with_domain_seen']} of the new rows. "
            if "new_with_domain_seen" in coverage
            else ""
        )
        + f"HTML rows {coverage.get('html_rows')}. Queries {coverage.get('queries')}.",
        "",
        f"Outcome pairs with a PREDICTED score: {pairs['predicted_pairs']}/{pairs['outcome_pairs']} ({share_text}). "
        "This is the share of REAL ordered pairs that were scored.",
        "",
    ]
    if "bm25" in result["metrics"]:
        bm25 = result["metrics"]["bm25"]
        lines.extend(
            [
                "BM25F on the supplied HTML is a baseline and not a gate scorer. "
                f"All pairs {_cell(bm25['all'])}. NDCG@10 {_metric(bm25['ndcg_at_10'])}. Spearman {_metric(bm25['mean_spearman'])}. "
                f"Surviving vs surviving {_cell(bm25['surviving_surviving'])}. "
                f"New vs surviving {_cell(bm25['new_vs_surviving'])}. New vs new {_cell(bm25['new_vs_new'])}.",
                "",
            ]
        )
    overlap = result.get("overlap")
    if overlap:
        lines.extend(
            [
                "## Overlap",
                "",
                f"{overlap['before_rows_still_present']} of {overlap['before_rows']} {result['before_day']} rows are in the {result['outcome_day']} top 10 for the same query. "
                f"{overlap['outcome_rows_seen_before']} of {overlap['outcome_rows']} REAL rows were on that before list. "
                f"Before keys {overlap['before_keys']}. Outcome keys {overlap['outcome_keys']}.",
                "",
            ]
        )
    prior = result.get("prior_window")
    if prior:
        lines.extend(
            [
                "## Prior window check",
                "",
                prior["note"],
                "",
            ]
        )
    lines.extend(
        [
            "## Pooled forward tests",
            "",
            "The two windows are Nov 11 to Nov 13 and Nov 13 to Nov 16. "
            "Counts are PREDICTED full-coverage scores against REAL positions. "
            f"The resample unit is the {pooled['unit']}. A draw takes {pooled['queries']} query names with replacement and keeps both windows of each drawn query together. "
            f"Draws: {pooled['draws']}. Seed: {pooled['seed']}. "
            "The rate pools pairs inside the draw. It is not the average of per-query rates. "
            "The 95% interval is the 2.5 and 97.5 percentiles of those draws, with the same linear percentile used by the new-page bootstrap.",
            "",
            f"Combined pairwise accuracy: {_ratio(pooled['combined_correct'], pooled['combined_pairs'])}. "
            f"95% CI {pooled['combined_ci'][0]:.6f} to {pooled['combined_ci'][1]:.6f}.",
            "",
            f"Persistence-ranked-last: {_ratio(pooled['baseline_correct'], pooled['baseline_pairs'])}.",
            "",
            f"Margin, combined minus persistence-ranked-last: {pooled['margin']:.6f}. "
            f"95% CI {pooled['margin_ci'][0]:.6f} to {pooled['margin_ci'][1]:.6f}.",
            "",
        ]
    )
    if pooled["margin_ci"][0] is not None and pooled["margin_ci"][0] > 0:
        lines.append("The margin interval is above zero.")
    elif pooled["margin_ci"][0] is not None:
        lines.append("The margin interval is not above zero.")
    lines.extend(
        [
            "",
            "This interval was not part of the pre-registered gate. The gate above is the point comparison on the Nov 16 window only.",
            "",
        ]
    )
    for window in result.get("window_totals") or []:
        lines.append(
            f"{window['name']}: combined {_ratio(window['combined_correct'], window['combined_pairs'])}; "
            f"persistence-ranked-last {_ratio(window['baseline_correct'], window['baseline_pairs'])}."
        )
    if result.get("window_totals"):
        lines.append("")
    host = result.get("domain_context_host")
    if host:
        lines.extend(
            [
                "## Domain context",
                "",
                f"{host} appears {result.get('domain_context_count', 0)} times in the REAL top 10s. "
                "This is context from the outcome file. It was not used to fit, score, or decide the gate.",
                "",
            ]
        )
        for row in result.get("domain_context") or []:
            lines.append(f"- query `{row['query']}` position {row['position']} {row['url']}")
        if result.get("domain_context"):
            lines.append("")
    provenance = result.get("provenance")
    if provenance:
        lines.extend(
            [
                "## Outcome file",
                "",
                f"REAL Serper organic results. Requests {provenance.get('requests')}. "
                f"Credits {provenance.get('credits')}. Statuses {json.dumps(provenance.get('statuses'), sort_keys=True)}. "
                f"Request window {provenance.get('start')} to {provenance.get('end')}. "
                f"Outcome CSV sha256 `{provenance.get('outcome_sha256')}`. "
                f"requests.jsonl sha256 `{provenance.get('requests_sha256')}`. "
                "The requests file is not committed. No API key is recorded here.",
                "",
            ]
        )
    for caveat in result.get("caveats") or []:
        lines.append(caveat)
        lines.append("")
    ledger = result.get("ledger")
    if ledger:
        lines.extend(
            [
                "## Ledger",
                "",
                f"Appended to `evals/preregistered/next-snapshot/track-record.jsonl`. "
                f"Rows {ledger['rows']}. Chain head `{ledger['head']}`. "
                "Earlier rows were not rewritten.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def public_result(
    scored: dict,
    pooled: dict,
    *,
    domain_rows: list[dict],
    provenance: dict | None,
    caveats: list[str],
    prior_note: str,
    prior_per_query: dict | None = None,
    window_totals: list[dict] | None = None,
) -> dict:
    """JSON-ready view. Drops page text and the in-memory query rows."""
    return {
        "before_day": scored["before_day"],
        "outcome_day": scored["outcome_day"],
        "history_days": list(scored["history_days"]),
        "snapshot_sha256": scored["snapshot_sha256"],
        "before_sha256": scored.get("before_sha256"),
        "params_sha256": scored["params_sha256"],
        "scorer_sha256": scored["scorer_sha256"],
        "code_commit": scored.get("code_commit"),
        "gate": scored["gate"],
        "metrics": scored["metrics"],
        "coverage": scored["coverage"],
        "pair_coverage": scored["pair_coverage"],
        "overlap": scored["overlap"],
        "html": scored["html"],
        "per_query": _json_counts(scored["per_query"]),
        "prior_per_query": None if prior_per_query is None else _json_counts(prior_per_query),
        "window_totals": window_totals or [],
        "pooled": pooled,
        "domain_context_host": DOMAIN_CONTEXT_HOST,
        "domain_context_count": len(domain_rows),
        "domain_context": domain_rows,
        "provenance": provenance,
        "prior_window": {"note": prior_note},
        "caveats": caveats,
        "label": "PREDICTED scores against REAL positions",
    }
