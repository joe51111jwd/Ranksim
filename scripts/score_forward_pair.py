"""Score a later Serper snapshot with the frozen full-coverage line.

The before snapshot is an input. The line is not refit. HTML stays out of git.

    python scripts/score_forward_pair.py \
        --before-csv evals/forward-2026-11-13/outcome_serp.csv \
        --before-day 2026-11-13 \
        --live-csv path/to/serp_serper_live.csv \
        --outcome-csv evals/forward-2026-11-16/outcome_serp.csv \
        --outcome-day 2026-11-16 \
        --html-dir path/to/nov16-snapshot \
        --prior-html-dir path/to/nov13-snapshot \
        --requests path/to/requests.jsonl \
        --ledger evals/preregistered/next-snapshot/track-record.jsonl \
        --report-md evals/forward-2026-11-16/report.md \
        --report-json evals/forward-2026-11-16/report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from aeo.ranksim.forward_pair import (
    DOMAIN_CONTEXT_HOST,
    append_pooled,
    append_transition,
    domain_appearances,
    pooled_query_bootstrap,
    public_result,
    published_window_matches,
    render_forward_report,
    score_frozen_window,
    score_transition,
)
from aeo.ranksim.full_coverage import file_sha256, repo_root, verify_track_record
from aeo.ranksim.metrics import spearman


def write_outcome_csv(live_csv: Path, outcome_csv: Path) -> None:
    with live_csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = {name.strip().lower(): name for name in reader.fieldnames or []}
        query_key = fields.get("query")
        url_key = fields.get("url") or fields.get("link")
        position_key = fields.get("position")
        if not query_key or not url_key or not position_key:
            raise SystemExit(f"{live_csv} needs query, position, and url or link")
        rows = []
        for raw in reader:
            query = " ".join((raw.get(query_key) or "").split())
            url = (raw.get(url_key) or "").strip()
            position = (raw.get(position_key) or "").strip()
            if not query or not url or not position:
                continue
            rows.append((query, url, position))
    if not rows:
        raise SystemExit(f"{live_csv} has no rows")
    outcome_csv.parent.mkdir(parents=True, exist_ok=True)
    with outcome_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["query", "url", "position"])
        writer.writerows(rows)


def read_requests(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    for needle in ("x-api-key", "api_key", "authorization", "bearer "):
        if needle in lowered:
            raise SystemExit(f"{path} contains {needle!r}. Refusing to record it.")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"{path} has no requests")
    return {
        "requests": len(rows),
        "credits": sum(int(row.get("credits") or 0) for row in rows),
        "statuses": dict(Counter(str(row.get("status")) for row in rows)),
        "start": min(row["start"] for row in rows),
        "end": max(row["end"] for row in rows),
        "requests_sha256": file_sha256(path),
        "response_sha256": [row.get("sha256") for row in rows],
        "gl": sorted({(row.get("params") or {}).get("gl") for row in rows}),
        "hl": sorted({(row.get("params") or {}).get("hl") for row in rows}),
        "num": sorted({(row.get("params") or {}).get("num") for row in rows}),
    }


def manifest_notes(html_dir: Path) -> dict:
    manifest = html_dir / "html_manifest.csv"
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    files = [(row.get("file") or "").strip() for row in rows]
    present = [name for name in files if name]
    distinct = {name for name in present if (html_dir / name).is_file()}
    return {
        "manifest_rows": len(rows),
        "manifest_with_file": len(present),
        "distinct_files_on_disk": len(distinct),
        "reasons": dict(
            Counter(
                (row.get("reason") or "").strip() or ("file_present" if (row.get("file") or "").strip() else "no_file")
                for row in rows
            )
        ),
    }


def spearman_gaps(queries: dict) -> list[str]:
    missing = []
    for query, rows in sorted(queries.items()):
        if len(rows) < 2:
            missing.append(query)
            continue
        rho = spearman([row["scores"]["full_coverage"] for row in rows], [row["position"] for row in rows])
        if rho is None:
            missing.append(query)
    return missing


def short_queries(outcome_rows: list) -> list[str]:
    counts = Counter(row.query for row in outcome_rows)
    return [f"{query} has {count} REAL rows" for query, count in sorted(counts.items()) if count != 10]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score one later snapshot with the frozen full-coverage line. Does not refit.")
    parser.add_argument("--before-csv", required=True)
    parser.add_argument("--before-day", required=True)
    parser.add_argument("--live-csv", required=True, help="Serper export. Written to --outcome-csv as query,url,position.")
    parser.add_argument("--outcome-csv", required=True)
    parser.add_argument("--outcome-day", required=True)
    parser.add_argument("--html-dir", required=True)
    parser.add_argument("--prior-html-dir", required=True, help="HTML for the already published before window. Used only to rebuild per-query counts.")
    parser.add_argument("--prior-report", default="evals/forward-2026-11-13/report.json")
    parser.add_argument("--requests", required=True)
    parser.add_argument("--ledger", default="evals/preregistered/next-snapshot/track-record.jsonl")
    parser.add_argument("--report-md", required=True)
    parser.add_argument("--report-json", required=True)
    args = parser.parse_args(argv)
    root = repo_root()

    before_csv = Path(args.before_csv)
    outcome_csv = Path(args.outcome_csv)
    write_outcome_csv(Path(args.live_csv), outcome_csv)
    provenance = read_requests(Path(args.requests))
    provenance["outcome_sha256"] = file_sha256(outcome_csv)
    provenance["before_sha256"] = file_sha256(before_csv)

    published_path = Path(args.prior_report)
    if not published_path.is_absolute():
        published_path = root / published_path
    published = json.loads(published_path.read_text(encoding="utf-8"))
    prior = score_frozen_window(
        root=root,
        outcome_csv=before_csv,
        outcome_day=args.before_day,
        html_dir=Path(args.prior_html_dir),
    )
    mismatches = published_window_matches(prior, published)
    if mismatches:
        raise SystemExit(
            "The recomputed Nov 13 window does not match the published report. "
            f"Refusing to pool. Mismatches: {', '.join(mismatches)}"
        )

    scored = score_transition(
        root=root,
        before_csv=before_csv,
        before_day=args.before_day,
        outcome_csv=outcome_csv,
        outcome_day=args.outcome_day,
        html_dir=Path(args.html_dir),
    )
    windows = [
        {
            "candidate": prior["per_query"]["full_coverage"]["all"],
            "baseline": prior["per_query"]["persistence_ranked_last"]["all"],
        },
        {
            "candidate": scored["per_query"]["full_coverage"]["all"],
            "baseline": scored["per_query"]["persistence_ranked_last"]["all"],
        },
    ]
    pooled = pooled_query_bootstrap(windows)
    domain_rows = domain_appearances(scored["queries"], DOMAIN_CONTEXT_HOST)
    html_notes = manifest_notes(Path(args.html_dir))
    gaps = spearman_gaps(scored["queries"])
    short = short_queries(scored["outcome_rows"])
    caveats = []
    if short:
        caveats.append("Queries that did not return 10 REAL rows: " + "; ".join(short) + ".")
    if gaps:
        caveats.append(
            "Spearman is undefined, and left out of the mean, for: " + ", ".join(gaps) + "."
        )
    else:
        caveats.append(
            f"Spearman is defined on {scored['metrics']['full_coverage']['spearman_queries']} of "
            f"{scored['metrics']['full_coverage']['ndcg_queries']} queries with at least two REAL rows."
        )
    caveats.append(
        "HTML on disk: "
        f"{html_notes['distinct_files_on_disk']} distinct files, "
        f"{html_notes['manifest_with_file']} manifest URLs with a file, "
        f"{html_notes['manifest_rows']} manifest rows. "
        "Manifest rows by recorded reason. An empty reason on a row that has a file is counted as file_present: "
        f"{json.dumps(html_notes['reasons'], sort_keys=True)}. "
        f"The loader attached {scored['html'].get('html_files', 0)} URLs. "
        f"REAL rows with HTML: {scored['coverage'].get('html_rows')}. "
        f"REAL rows with no HTML: {scored['coverage'].get('content_missing_rows')}. "
        "A missing page adds no BM25F push. Its frozen coefficient is 0."
    )
    persistence_rho = scored["metrics"]["persistence_ranked_last"]["spearman_queries"]
    combined_rho = scored["metrics"]["full_coverage"]["spearman_queries"]
    if persistence_rho != combined_rho:
        persistence_defined = []
        for query, rows in sorted(scored["queries"].items()):
            if len(rows) < 2:
                continue
            rho = spearman(
                [row["scores"]["persistence_ranked_last"] for row in rows],
                [row["position"] for row in rows],
            )
            if rho is not None:
                persistence_defined.append(query)
        missing_persistence = sorted(set(scored["queries"]) - set(persistence_defined))
        caveats.append(
            f"Persistence-ranked-last Spearman is defined on {persistence_rho} queries and undefined for: "
            + ", ".join(missing_persistence)
            + f". Full coverage Spearman is defined on {combined_rho} queries."
        )
    if len(scored["outcome_rows"]) != scored["coverage"]["outcome_rows"]:
        caveats.append(
            f"The outcome file has {len(scored['outcome_rows'])} rows and "
            f"{scored['coverage']['outcome_rows']} rows after collapsing a repeated query and URL to its best position."
        )
    prior_queries = set(prior["per_query"]["full_coverage"]["all"])
    current_queries = set(scored["per_query"]["full_coverage"]["all"])
    if prior_queries != current_queries:
        caveats.append(
            "Query names in the pooled windows differ. "
            f"Only in the first window: {sorted(prior_queries - current_queries)}. "
            f"Only in the second window: {sorted(current_queries - prior_queries)}."
        )
    def _sum(mapping: dict) -> tuple[int, int, int]:
        correct = wrong = ties = 0
        for triple in mapping.values():
            correct += int(triple[0])
            wrong += int(triple[1])
            ties += int(triple[2])
        return correct, wrong, ties

    prior_combined = _sum(prior["per_query"]["full_coverage"]["all"])
    prior_base = _sum(prior["per_query"]["persistence_ranked_last"]["all"])
    current_combined = _sum(scored["per_query"]["full_coverage"]["all"])
    current_base = _sum(scored["per_query"]["persistence_ranked_last"]["all"])
    published_combined = published["metrics"]["full_coverage"]["all"]
    published_base = published["metrics"]["persistence_ranked_last"]["all"]
    if (prior_combined[0], sum(prior_combined)) != (published_combined["correct"], published_combined["pairs"]):
        raise SystemExit("Recomputed Nov 13 per-query counts do not add up to the published combined count.")
    if (prior_base[0], sum(prior_base)) != (published_base["correct"], published_base["pairs"]):
        raise SystemExit("Recomputed Nov 13 baseline counts do not add up to the published baseline.")
    if (current_combined[0], sum(current_combined)) != (pooled["combined_correct"] - prior_combined[0], pooled["combined_pairs"] - sum(prior_combined)):
        raise SystemExit("Pooled combined counts are not the sum of the two windows.")
    window_totals = [
        {
            "name": "2026-11-11 to 2026-11-13",
            "combined_correct": prior_combined[0],
            "combined_pairs": sum(prior_combined),
            "baseline_correct": prior_base[0],
            "baseline_pairs": sum(prior_base),
        },
        {
            "name": "2026-11-13 to 2026-11-16",
            "combined_correct": current_combined[0],
            "combined_pairs": sum(current_combined),
            "baseline_correct": current_base[0],
            "baseline_pairs": sum(current_base),
        },
    ]
    prior_note = (
        f"The {args.before_day} window was recomputed with the frozen history ending 2026-11-11 and the snapshot HTML. "
        "Pair counts, NDCG@10, and Spearman match the published report. The line was not refit. "
        "Those recomputed per-query counts are the first window of the pooled bootstrap."
    )
    payload = public_result(
        scored,
        pooled,
        domain_rows=domain_rows,
        provenance=provenance,
        caveats=caveats,
        prior_note=prior_note,
        prior_per_query=prior["per_query"],
        window_totals=window_totals,
    )
    ledger = Path(args.ledger)
    append_transition(ledger, scored)
    head = append_pooled(
        ledger,
        pooled,
        observed_on=args.outcome_day,
        snapshot_sha256=scored["snapshot_sha256"],
        note=(
            "Pooled SCORE of two forward windows, Nov 11 to Nov 13 and Nov 13 to Nov 16. "
            "PREDICTED full-coverage scores against REAL positions. "
            "Not a new prediction and not a REAL observation. "
            f"Query bootstrap, {pooled['draws']} draws, seed {pooled['seed']}."
        ),
    )
    payload["ledger"] = {"rows": verify_track_record(ledger), "head": head}
    report_md = Path(args.report_md)
    report_json = Path(args.report_json)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(render_forward_report(payload), encoding="utf-8")
    report_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(render_forward_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
