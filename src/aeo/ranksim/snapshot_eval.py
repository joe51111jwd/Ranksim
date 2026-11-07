"""Offline evaluation of a private Serper snapshot.

Reads HTML and CSV from RANKSIM_SNAPSHOT_DIR or --snapshot. Writes a sanitized
JSON report to --out. Does not call a network API and does not write the pages.

The published holdout is scored only when --reference is passed, and only after
the training-side decision is already fixed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from aeo.crawl.urls import domain_of
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.features import FEATURE_NAMES, normalize_rows
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.lambdamart import RankExample, build_examples, fit_lambdamart, predict_scores
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.score import HAND_WEIGHTS, dot
from aeo.ranksim.store import open_session
from aeo.ranksim.snapshot_protocol import (
    LEXICAL_FEATURES,
    adoption_decision,
    assert_no_rank_leak,
    assign_families,
    frozen_split,
    selective_pairs,
    summarize_queries,
)

TODAY = date(2026, 11, 7)


def _saved_html(html_rows: list[dict[str, str]], root: Path) -> dict[str, str]:
    pages = {}
    for row in html_rows:
        name = (row.get("file") or "").strip()
        if not name:
            continue
        pages[row["url"]] = (root / name).read_bytes().decode("utf-8")
    return pages


def _corpus(html_by_url: dict[str, str]) -> dict:
    """Stored pages plus the link graph the product ranker actually uses."""
    database = f"sqlite:///{tempfile.mkdtemp(prefix='ranksim-snapshot-')}/rank.db"
    session = open_session(database)
    try:
        for url, html in html_by_url.items():
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
        return load_corpus_docs(session)
    finally:
        session.close()


def _examples(labels, pages, queries: set[str]) -> tuple[list[RankExample], int]:
    chosen = [label for label in labels if label.query in queries]
    examples, missing, _withheld = build_examples(chosen, pages, today=TODAY)
    for example in examples:
        assert_no_rank_leak(example.features)
    return examples, missing


def _groups(examples: list[RankExample], score_of) -> list[tuple[list[float], list[float]]]:
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        grouped[example.query].append(example)
    found = []
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        found.append(([row.position for row in rows], score_of(rows)))
    return found


def _feature_report(examples: list[RankExample], name: str) -> dict:
    return summarize_queries(_groups(examples, lambda rows: [float(row.features.get(name) or 0.0) if row.features.get(name) == row.features.get(name) else 0.0 for row in rows]))


def _fit_and_score(train: list[RankExample], test: list[RankExample], names: list[str]) -> list[float]:
    model = fit_lambdamart(train, names)
    predicted = predict_scores(model, test, names)
    return [predicted[id(row)] for row in test]


def _held_out_model(examples: list[RankExample], holdout_of, names: list[str]) -> dict:
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        grouped[example.query].append(example)
    folds: dict[str, list[str]] = defaultdict(list)
    for query in grouped:
        folds[holdout_of(query)].append(query)
    groups = []
    for test_queries in folds.values():
        train = [row for query, rows in grouped.items() if query not in set(test_queries) for row in rows]
        if len({row.query for row in train}) < 2:
            continue
        for query in test_queries:
            rows = grouped[query]
            groups.append(([row.position for row in rows], _fit_and_score(train, rows, names)))
    report = summarize_queries(groups)
    report["folds"] = len(folds)
    return report


def _domain_family_prior(examples: list[RankExample], families: dict[str, str]) -> dict:
    """Score a stored page by its domain's mean relevance on other families. Not a text feature."""
    grouped: dict[str, list[RankExample]] = defaultdict(list)
    for example in examples:
        grouped[example.query].append(example)
    groups = []
    for query, rows in grouped.items():
        banned = {other for other, family in families.items() if family == families[query]}
        totals: dict[str, list[float]] = defaultdict(list)
        for other, other_rows in grouped.items():
            if other in banned:
                continue
            for row in other_rows:
                totals[domain_of(row.url)].append(11.0 - float(row.position))
        prior = {domain: sum(values) / len(values) for domain, values in totals.items()}
        groups.append(([row.position for row in rows], [prior.get(domain_of(row.url), 0.0) for row in rows]))
    report = summarize_queries(groups)
    report["meaning"] = "Domain prior from other query families. Uses their captured positions. Not a deployable text feature."
    return report


def _overlap(labels, train: set[str], holdout: set[str]) -> dict:
    urls: dict[str, set[str]] = defaultdict(set)
    domains: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        urls[label.query].add(label.url)
        domains[label.query].add(domain_of(label.url))
    def pairs(side: set[str], field: dict[str, set[str]]) -> tuple[int, int]:
        names = sorted(side)
        shared = compared = 0
        for index, left in enumerate(names):
            for right in names[index + 1 :]:
                compared += 1
                if field[left] & field[right]:
                    shared += 1
        return shared, compared
    train_url_shared, train_url_pairs = pairs(train, urls)
    train_domain_shared, train_domain_pairs = pairs(train, domains)
    holdout_urls = set().union(*(urls[query] for query in holdout)) if holdout else set()
    train_urls = set().union(*(urls[query] for query in train)) if train else set()
    return {
        "train_query_pairs": train_url_pairs,
        "train_query_pairs_sharing_a_url": train_url_shared,
        "train_query_pairs_sharing_a_domain": train_domain_shared,
        "holdout_urls": len(holdout_urls),
        "holdout_urls_also_in_train": len(holdout_urls & train_urls),
    }


def _reference(train_examples: list[RankExample], holdout_examples: list[RankExample]) -> dict:
    """One look at the published holdout. The model was already frozen."""
    names = list(FEATURE_NAMES)
    model = fit_lambdamart(train_examples, names)
    lambdamart_groups = _groups(
        holdout_examples,
        lambda rows, model=model, names=names: _predict(model, rows, names),
    )
    bm25_groups = _groups(holdout_examples, lambda rows: [float(row.features.get("bm25f") or 0.0) for row in rows])
    hand_groups = _groups(holdout_examples, _hand_scores)
    provider_groups = _groups(holdout_examples, lambda rows: [-float(row.position) for row in rows])
    lambdamart = summarize_queries(lambdamart_groups)
    return {
        "protocol": "Fit on the 20 train queries only, then score the published 10-query holdout once.",
        "lambdamart": lambdamart,
        "lambdamart_selective_largest_half": selective_pairs(lambdamart_groups, keep_fraction=0.5),
        "bm25f": summarize_queries(bm25_groups),
        "frozen_hand_weights": summarize_queries(hand_groups),
        "provider_order_circular": {
            **summarize_queries(provider_groups),
            "meaning": "Score is the negative of the captured position. This restates the label.",
        },
        "not_forward_accuracy": True,
    }


def _predict(model, rows: list[RankExample], names: list[str]) -> list[float]:
    predicted = predict_scores(model, rows, names)
    return [predicted[id(row)] for row in rows]


def _hand_scores(rows: list[RankExample]) -> list[float]:
    scaled = normalize_rows([row.features for row in rows])
    return [dot(HAND_WEIGHTS, row) for row in scaled]


def evaluate(snapshot: Path, *, reference: bool) -> dict:
    manifest = [row["query"] for row in csv.DictReader((snapshot / "query_manifest.csv").open(newline="", encoding="utf-8"))]
    train, holdout = frozen_split(manifest)
    labels = load_serp_file(snapshot / "serp_serper_live.csv")
    html_rows = list(csv.DictReader((snapshot / "html_manifest.csv").open(newline="", encoding="utf-8")))
    html_by_url = _saved_html(html_rows, snapshot)
    train_urls = {label.url for label in labels if label.query in set(train) and label.url in html_by_url}
    # Selection sees only training pages, so holdout links cannot move the features.
    train_examples, train_missing = _examples(labels, _corpus({url: html_by_url[url] for url in train_urls}), set(train))
    families = assign_families(train)
    family_lists = {name: sorted(query for query, got in families.items() if got == name) for name in sorted(set(families.values()))}
    lexical = list(LEXICAL_FEATURES)
    bm25 = _feature_report(train_examples, "bm25f")
    current = _held_out_model(train_examples, lambda query: families[query], list(FEATURE_NAMES))
    lexical_model = _held_out_model(train_examples, lambda query: families[query], lexical)
    decision = adoption_decision(lexical_model["pairwise"], bm25["pairwise"], current["pairwise"])
    file_urls = {row["url"] for row in html_rows if (row.get("file") or "").strip()}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot": str(snapshot),
        "reference_scored": reference,
        "rows": len(labels),
        "queries": len(manifest),
        "saved_html_files": len(file_urls),
        "rows_with_saved_html": sum(1 for label in labels if label.url in file_urls),
        "rows_missing_html": sum(1 for label in labels if label.url not in file_urls),
        "overlap": _overlap(labels, set(train), set(holdout)),
        "families": family_lists,
        "train": {
            "queries": len(train),
            "link_graph": "training pages only",
            "stored_examples": len(train_examples),
            "rows_missing_html": train_missing,
            "bm25f_no_training": bm25,
            "lambdamart_query_loo": _held_out_model(train_examples, lambda query: query, list(FEATURE_NAMES)),
            "lambdamart_family_out": current,
            "lambdamart_family_out_lexical": lexical_model,
            "domain_prior_family_out": _domain_family_prior(train_examples, families),
        },
        "adoption": decision,
        "ranker_change": "none" if not decision["adopt"] else "not_applied_in_this_commit",
    }
    if reference:
        # The published baseline built one link graph over every saved page, including holdout pages.
        published_pages = _corpus(html_by_url)
        published_train, _train_missing = _examples(labels, published_pages, set(train))
        holdout_examples, holdout_missing = _examples(labels, published_pages, set(holdout))
        report["holdout_reference"] = _reference(published_train, holdout_examples)
        report["holdout_reference"]["link_graph"] = "all saved pages, matching the published baseline"
        report["holdout_reference"]["queries"] = len(holdout)
        report["holdout_reference"]["rows_missing_html"] = holdout_missing
        report["holdout_reference"]["stored_examples"] = len(holdout_examples)
    else:
        report["holdout_reference"] = None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train-side snapshot diagnosis, with an optional one-shot holdout reference.")
    parser.add_argument("--snapshot", default=os.environ.get("RANKSIM_SNAPSHOT_DIR", ""))
    parser.add_argument("--out", required=True)
    parser.add_argument("--reference", action="store_true", help="Score the published holdout once. Do not use this while choosing a model.")
    args = parser.parse_args(argv)
    snapshot = Path(args.snapshot)
    if not (snapshot / "query_manifest.csv").is_file():
        raise SystemExit("Pass --snapshot or RANKSIM_SNAPSHOT_DIR pointing at the private snapshot folder.")
    report = evaluate(snapshot, reference=args.reference)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "adopt": report["adoption"]["adopt"],
        "family_out_lambdamart": report["train"]["lambdamart_family_out"]["pairwise"],
        "family_out_lexical": report["train"]["lambdamart_family_out_lexical"]["pairwise"],
        "bm25f": report["train"]["bm25f_no_training"]["pairwise"],
        "reference": None if not report["holdout_reference"] else report["holdout_reference"]["lambdamart"]["pairwise"],
        "out": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
