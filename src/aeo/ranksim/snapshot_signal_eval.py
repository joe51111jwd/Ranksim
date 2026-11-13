"""Family-out checks for page type, Tranco popularity, and Common Crawl link ranks.

The published holdout is not scored here. Pass the private snapshot and the
external files. The external files are not written into the repository.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from aeo.ranksim.features import FEATURE_NAMES
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.lambdamart import RankExample, fit_lambdamart, predict_scores
from aeo.ranksim.snapshot_eval import _corpus, _examples, _saved_html
from aeo.ranksim.snapshot_protocol import (
    assign_families,
    frozen_split,
    pair_counts,
    paired_query_delta,
    signal_decision,
    summarize_queries,
)
from aeo.ranksim.snapshot_signals import (
    CC_FEATURES,
    PAGE_TYPE_FEATURES,
    TRANCO_FEATURES,
    cc_features,
    load_cc_domain_ranks,
    load_tranco,
    page_type_features,
    tranco_features,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_with(examples: list[RankExample], extra) -> list[RankExample]:
    copied = []
    for example in examples:
        features = dict(example.features)
        features.update(extra(example))
        copied.append(RankExample(example.query, example.url, example.position, features, example.intent))
    return copied


def _family_detail(examples: list[RankExample], families: dict[str, str], names: list[str]) -> tuple[dict, dict[str, float]]:
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        grouped[example.query].append(example)
    folds: dict[str, list[str]] = defaultdict(list)
    for query in grouped:
        folds[families[query]].append(query)
    groups = []
    per_query: dict[str, float] = {}
    for test_queries in folds.values():
        banned = set(test_queries)
        train = [row for query, rows in grouped.items() if query not in banned for row in rows]
        if len({row.query for row in train}) < 2:
            continue
        model = fit_lambdamart(train, names)
        for query in test_queries:
            rows = grouped[query]
            predicted = predict_scores(model, rows, names)
            scores = [predicted[id(row)] for row in rows]
            positions = [row.position for row in rows]
            groups.append((positions, scores))
            correct, wrong, ties = pair_counts(positions, scores)
            total = correct + wrong + ties
            if total:
                per_query[query] = correct / total
    return summarize_queries(groups), per_query


def _single(examples: list[RankExample], name: str) -> dict:
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        grouped[example.query].append(example)
    groups = []
    finite = 0
    for rows in grouped.values():
        scores = []
        for row in rows:
            value = row.features.get(name, float("nan"))
            number = float(value)
            if number == number:
                finite += 1
            scores.append(number)
        groups.append(([row.position for row in rows], scores))
    report = summarize_queries(groups)
    report["rows_with_a_number"] = finite
    report["rows"] = len(examples)
    return report


def _coverage(examples: list[RankExample], names: tuple[str, ...], present) -> dict:
    hit = sum(1 for example in examples if present(example))
    return {"rows": len(examples), "rows_with_signal": hit, "share": round(hit / len(examples), 6) if examples else None, "features": list(names)}


def run_signals(snapshot: Path, tranco_path: Path, cc_path: Path) -> dict:
    manifest = [row["query"] for row in csv.DictReader((snapshot / "query_manifest.csv").open(newline="", encoding="utf-8"))]
    train, _holdout = frozen_split(manifest)
    labels = load_serp_file(snapshot / "serp_serper_live.csv")
    html_rows = list(csv.DictReader((snapshot / "html_manifest.csv").open(newline="", encoding="utf-8")))
    html_by_url = _saved_html(html_rows, snapshot)
    train_urls = {label.url for label in labels if label.query in set(train) and label.url in html_by_url}
    pages = _corpus({url: html_by_url[url] for url in train_urls})
    examples, missing = _examples(labels, pages, set(train))
    families = assign_families(train)
    base_names = list(FEATURE_NAMES)
    baseline, base_queries = _family_detail(examples, families, base_names)
    tranco = load_tranco(tranco_path)
    cc_ranks = load_cc_domain_ranks(cc_path)

    def page_extra(example: RankExample) -> dict[str, float]:
        return page_type_features(example.url, html_by_url.get(example.url, ""))

    def tranco_extra(example: RankExample) -> dict[str, float]:
        return tranco_features(example.url, tranco)

    def cc_extra(example: RankExample) -> dict[str, float]:
        return cc_features(example.url, cc_ranks)

    experiments = {}
    for key, names, extra, meaning in (
        (
            "page_type",
            base_names + list(PAGE_TYPE_FEATURES),
            page_extra,
            "URL class and schema.org type. Not fit to a captured position.",
        ),
        (
            "tranco_popularity",
            base_names + list(TRANCO_FEATURES),
            tranco_extra,
            "Tranco top-list popularity. Not a backlink count and not this snapshot's Google order.",
        ),
        (
            "common_crawl_link_graph",
            base_names + list(CC_FEATURES),
            cc_extra,
            "Common Crawl domain PageRank and harmonic centrality from the hyperlink graph. Missing domains stay missing.",
        ),
    ):
        augmented = _copy_with(examples, extra)
        summary, per_query = _family_detail(augmented, families, names)
        delta = paired_query_delta(base_queries, per_query)
        experiments[key] = {
            "meaning": meaning,
            "family_out": summary,
            "versus_current_family_out": delta,
            "decision": signal_decision(delta),
        }
    page_examples = _copy_with(examples, page_extra)
    tranco_examples = _copy_with(examples, tranco_extra)
    cc_examples = _copy_with(examples, cc_extra)
    adopted = [name for name, row in experiments.items() if row["decision"]["adopt"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "holdout_scored": False,
        "train_queries": len(train),
        "stored_examples": len(examples),
        "rows_missing_html": missing,
        "current_family_out": baseline,
        "experiments": experiments,
        "single_feature_on_train_pairs": {
            "tranco_popularity": _single(tranco_examples, "tranco_popularity"),
            "cc_pagerank": _single(cc_examples, "cc_pagerank"),
            "cc_harmonic": _single(cc_examples, "cc_harmonic"),
        },
        "coverage": {
            "page_type_not_other": _coverage(
                page_examples,
                PAGE_TYPE_FEATURES,
                lambda example: any(example.features.get(name) == 1.0 for name in PAGE_TYPE_FEATURES[:6]),
            ),
            "schema_any": _coverage(
                page_examples,
                PAGE_TYPE_FEATURES[6:],
                lambda example: any(example.features.get(name) == 1.0 for name in PAGE_TYPE_FEATURES[6:]),
            ),
            "tranco_listed": _coverage(tranco_examples, TRANCO_FEATURES, lambda example: example.features.get("tranco_listed") == 1.0),
            "common_crawl_matched": _coverage(
                cc_examples,
                CC_FEATURES,
                lambda example: example.features.get("cc_pagerank") == example.features.get("cc_pagerank"),
            ),
        },
        "sources": {
            "tranco": {
                "path_sha256": _sha256(tranco_path),
                "download": "https://tranco-list.eu/top-1m.csv.zip",
                "what_it_is": "Aggregate popularity rank. Inputs include DNS, browser, and one link-based list. The published number is not a backlink count.",
                "license_note": "The default list includes Cloudflare Radar data under CC BY-NC 4.0, so this file is not committed and is not cleared here for commercial redistribution.",
            },
            "common_crawl": {
                "release": "cc-main-2025-oct-nov-dec",
                "file": "https://data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2025-oct-nov-dec/domain/cc-main-2025-oct-nov-dec-domain-ranks.txt.gz",
                "fields": ["pr_val", "harmonicc_val"],
                "join": "registrable domain via site_key",
                "matched_domains": len(cc_ranks),
                "extract_sha256": _sha256(cc_path),
                "what_it_is": "PageRank and harmonic centrality on Common Crawl's domain hyperlink graph for October-December 2025 crawls.",
            },
        },
        "adopted": adopted,
        "ranker_change": "none" if not adopted else "pending_product_wire",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train-only family-out checks for three external or structural signals.")
    parser.add_argument("--snapshot", default=os.environ.get("RANKSIM_SNAPSHOT_DIR", ""))
    parser.add_argument("--tranco", required=True)
    parser.add_argument("--cc-ranks", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    snapshot = Path(args.snapshot)
    if not (snapshot / "query_manifest.csv").is_file():
        raise SystemExit("Pass --snapshot pointing at the private snapshot folder.")
    report = run_signals(snapshot, Path(args.tranco), Path(args.cc_ranks))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "adopted": report["adopted"],
        "current": report["current_family_out"]["pairwise"],
        "page_type": report["experiments"]["page_type"]["family_out"]["pairwise"],
        "tranco": report["experiments"]["tranco_popularity"]["family_out"]["pairwise"],
        "cc": report["experiments"]["common_crawl_link_graph"]["family_out"]["pairwise"],
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
