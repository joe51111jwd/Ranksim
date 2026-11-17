"""Score a date's pulls against consensus truth. Does not refit. Label: PREDICTED.

    python scripts/score_consensus.py \
        --pull path/to/pull-a/serp_serper_live.csv \
        --pull path/to/pull-b/serp_serper_live.csv \
        --pull path/to/pull-c/serp_serper_live.csv \
        --deep path/to/serp_serper_deep.csv \
        --previous path/to/previous-day/serp_serper_live.csv \
        --history path/to/2026-11-06 \
        --history path/to/2026-11-09 \
        --history path/to/2026-11-11 \
        --history path/to/2026-11-13 \
        --html-dir path/to/html-snapshot \
        --model evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json

The ceiling is how well one real pull predicts the consensus of the other pulls.
Persistence is the previous day's position, with 11 for a URL that was absent.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

from aeo.ranksim.consensus import (
    consensus_from_pulls,
    pool,
    predict_from_pull,
    score_query,
)
from aeo.ranksim.pair_order import (
    Snap,
    SnapRow,
    build_transition,
    load_model,
    load_offpage_cache,
    load_pages,
    load_serp_snapshot,
)
from aeo.ranksim.position_metrics import bootstrap_rate, ranks_from_scores
from aeo.ranksim.site_history import url_key

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "evals/preregistered/forward-pair-2026-11-16/model-heldout-nov16.json"
DEFAULT_CACHE = ROOT / "evals/preregistered/forward-pair-2026-11-16/offpage.tsv"


def _read_serp(path: Path) -> dict[str, dict[str, tuple[str, int]]]:
    grouped: dict[str, dict[str, tuple[str, int]]] = defaultdict(dict)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = {name.strip().lower(): name for name in (reader.fieldnames or [])}
        query_key = fields.get("query")
        url_column = fields.get("link") or fields.get("url")
        position_key = fields.get("position")
        if not query_key or not url_column or not position_key:
            raise SystemExit(f"{path} needs query, link, and position")
        for raw in reader:
            query = " ".join((raw.get(query_key) or "").split())
            url = (raw.get(url_column) or "").strip()
            if not query or not url:
                continue
            try:
                position = int(float(raw.get(position_key) or ""))
            except ValueError:
                continue
            if position <= 0:
                continue
            try:
                key = url_key(url)
            except ValueError:
                key = url
            current = grouped[query].get(key)
            if current is None or position < current[1]:
                grouped[query][key] = (url, position)
    return grouped


def _deep_ranks(path: Path | None) -> dict[str, dict[str, int]]:
    if path is None:
        return {}
    found: dict[str, dict[str, int]] = defaultdict(dict)
    table = _read_serp(path)
    for query, rows in table.items():
        for key, (_url, position) in rows.items():
            if position >= 11:
                found[query][key] = position
    return found


def _fmt(pair: tuple[float, float], by_query: list[tuple[float, float]]) -> str:
    numerator, denominator = pair
    if denominator == 0:
        return "n/a"
    low, high = bootstrap_rate(by_query, skip_empty=True)
    return f"{numerator:.0f}/{denominator:.0f} ({numerator / denominator:.3f}) [{low:.3f}, {high:.3f}]"


def _query_pairs(scores, name: str) -> list[tuple[float, float]]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(lambda: [0.0, 0.0])
    for score in scores:
        numerator, denominator = getattr(score, name)
        grouped[score.query][0] += numerator
        grouped[score.query][1] += denominator
    return [(item[0], item[1]) for _query, item in sorted(grouped.items())]


def _line(title: str, scores) -> None:
    pooled = pool(scores)
    print(title)
    for name, label in (
        ("exact", "Exact consensus"),
        ("within_range", "3-wide coverage"),
        ("observed", "Observed-set exact"),
        ("top3_precision", "Top-3 precision"),
        ("top3_recall", "Top-3 recall"),
        ("number_one", "Number-one accuracy"),
    ):
        print(f"  {label}: {_fmt(pooled[name], _query_pairs(scores, name))}")


def _persistence_predictions(rows, previous: dict[str, tuple[str, int]], deep: dict[str, int]) -> list[int]:
    from aeo.ranksim.consensus import pull_rank

    return [pull_rank(row.url_key, {key: pos for key, (_url, pos) in previous.items()}, deep)[0] for row in rows]


def _model_predictions(grouped_rows: dict[str, list], history_dirs: list[Path], html_dir: Path | None, model_path: Path, offpage_cache: Path) -> dict[str, list[int]]:
    from aeo.ranksim.pair_order import site_key

    history = [load_serp_snapshot(path) for path in history_dirs]
    outcome_rows = []
    for query, rows in grouped_rows.items():
        for row in rows:
            outcome_rows.append(
                SnapRow(
                    query=query,
                    url=row.url,
                    position=1.0,
                    title="",
                    url_key=row.url_key,
                    site=site_key(row.url),
                )
            )
    outcome = Snap(day="2099-01-01", rows=outcome_rows)
    pages = load_pages(html_dir) if html_dir is not None else {}
    if offpage_cache.is_file():
        offpage = load_offpage_cache(offpage_cache)
    else:
        from aeo.ranksim.pair_order import Offpage

        offpage = Offpage()
    built = build_transition(history, outcome, pages, offpage)
    model = load_model(model_path)
    scores = model.predict(built)
    predicted = ranks_from_scores(built, scores)
    by_query: dict[str, list[int]] = {}
    for query, rows in grouped_rows.items():
        by_query[query] = []
        for row in rows:
            matches = [index for index, item in enumerate(built) if item.query == query and item.url_key == row.url_key]
            if len(matches) != 1:
                raise SystemExit(f"Model row missing for {query} {row.url}")
            by_query[query].append(predicted[matches[0]])
    return by_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PREDICTED consensus scorecard. Does not refit.")
    parser.add_argument("--pull", action="append", required=True, type=Path)
    parser.add_argument("--deep", type=Path)
    parser.add_argument("--previous", type=Path, help="Previous day's SERP. Persistence uses these positions.")
    parser.add_argument("--history", action="append", type=Path, default=[])
    parser.add_argument("--html-dir", type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--offpage-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--label", default="PREDICTED", choices=("PREDICTED", "DIAGNOSTIC"))
    parser.add_argument("--expect-sha256", default="", help="If set, the model file must match this hash.")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    pulls = [_read_serp(path) for path in args.pull]
    deep = _deep_ranks(args.deep)
    queries = sorted(set.intersection(*(set(pull) for pull in pulls)))
    if not queries:
        raise SystemExit("No query is in every pull")
    print(f"Label: {args.label}. Consensus of {len(args.pull)} pulls, {len(queries)} queries in every pull.")
    if args.model.is_file():
        digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
        print(f"Model: {args.model}")
        print(f"Model sha256: {digest}")
        if args.expect_sha256 and digest != args.expect_sha256:
            raise SystemExit(f"Model hash {digest} does not match {args.expect_sha256}")
    ceiling_scores = []
    persistence_scores = []
    previous = _read_serp(args.previous) if args.previous else {}
    for query in queries:
        query_pulls = [pull[query] for pull in pulls]
        query_deep = deep.get(query, {})
        for index, pull in enumerate(query_pulls):
            others = query_pulls[:index] + query_pulls[index + 1 :]
            rows = consensus_from_pulls(query, others, query_deep)
            if not rows:
                continue
            ceiling_scores.append(score_query(rows, predict_from_pull(rows, pull, query_deep)))
            if args.previous:
                persistence_scores.append(
                    score_query(rows, _persistence_predictions(rows, previous.get(query, {}), query_deep))
                )
    _line("Inter-pull ceiling (one pull predicts the consensus of the other pulls)", ceiling_scores)
    if persistence_scores:
        _line("Persistence on the same leave-one-out consensus", persistence_scores)
    if args.model.is_file() and args.history:
        print("Scoring the frozen model", flush=True)
        # Score each query's full three-pull URL set once, then reuse ranks for every leave-one-out subset.
        full_rows = {query: consensus_from_pulls(query, [pull[query] for pull in pulls], deep.get(query, {})) for query in queries}
        full_predicted = _model_predictions(full_rows, args.history, args.html_dir, args.model, args.offpage_cache)
        model_scores = []
        for query in queries:
            query_pulls = [pull[query] for pull in pulls]
            query_deep = deep.get(query, {})
            rank_by_url = {row.url_key: full_predicted[query][index] for index, row in enumerate(full_rows[query])}
            for index, _pull in enumerate(query_pulls):
                others = query_pulls[:index] + query_pulls[index + 1 :]
                rows = consensus_from_pulls(query, others, query_deep)
                # Re-rank within the leave-one-out set so the position is the model's order of those URLs.
                order = sorted(range(len(rows)), key=lambda item: (rank_by_url.get(rows[item].url_key, 10_000), rows[item].url_key))
                predicted = [0] * len(rows)
                for rank, item in enumerate(order, start=1):
                    predicted[item] = rank
                model_scores.append(score_query(rows, predicted))
        _line("Frozen model on the same leave-one-out consensus", model_scores)
    full_rows = {query: consensus_from_pulls(query, [pull[query] for pull in pulls], deep.get(query, {})) for query in queries}
    full_persistence = []
    for query, rows in full_rows.items():
        if args.previous:
            full_persistence.append(score_query(rows, _persistence_predictions(rows, previous.get(query, {}), deep.get(query, {}))))
    if full_persistence:
        _line("Persistence on the full-day consensus", full_persistence)
    if args.model.is_file() and args.history:
        if "full_predicted" not in locals():
            print("Scoring the frozen model on the full-day consensus", flush=True)
            full_predicted = _model_predictions(full_rows, args.history, args.html_dir, args.model, args.offpage_cache)
        full_model = [score_query(rows, full_predicted[query]) for query, rows in full_rows.items()]
        _line("Frozen model on the full-day consensus", full_model)
    if args.json_out:
        payload = {
            "label": args.label,
            "queries": len(queries),
            "pulls": [str(path) for path in args.pull],
            "ceiling": {name: pool(ceiling_scores)[name] for name in ("exact", "within_range", "observed", "top3_precision", "top3_recall", "number_one")},
        }
        args.json_out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
