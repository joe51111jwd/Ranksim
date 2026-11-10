"""CLI for the SmartCrew rank simulator.

Examples:
  python -m aeo.ranksim keywords
  python -m aeo.ranksim gaps
  python -m aeo.ranksim collect --mock
  python -m aeo.ranksim calibrate
  python -m aeo.ranksim test page.html --url https://www.smartcrewhq.com/dirtwork-software/
  python -m aeo.ranksim track
  python -m aeo.ranksim report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from aeo.config import get_settings
from aeo.crawl.fetch import Crawler
from aeo.errors import ProviderConfigError, UnsafeURL
from aeo.ranksim.authority import authority_client
from aeo.ranksim.intents import intent_for, query_intents
from aeo.ranksim.whatif import bundle_targets
from aeo.ranksim.keywords import build_keywords, gap_summary, load_keyword_file, repo_root, page_gaps, refresh_sitemap, write_keywords_csv
from aeo.ranksim.loop import FetchResult, attach_authority, collect_corpus, calibrate, page_from_html, run_test, track_ranks
from aeo.ranksim.report import render_report
from aeo.ranksim.index_serp import index_serp_csv
from aeo.ranksim.serp import resolve_serp
from aeo.ranksim.store import open_session


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ranksim", description="SmartCrew Google rank simulator")
    parser.add_argument("--database", default=None, help="SQLAlchemy URL. Default is RANKSIM_DATABASE_URL.")
    sub = parser.add_subparsers(dest="command", required=True)

    keywords = sub.add_parser("keywords", help="Write keywords.csv")
    keywords.add_argument("--refresh-sitemap", action="store_true")

    sub.add_parser("gaps", help="Print the page gap list")

    collect = sub.add_parser("collect", help="Fetch SERPs and crawl the focused corpus")
    collect.add_argument("--provider", default="auto")
    collect.add_argument("--mock", action="store_true", help="Use the synthetic SERP. Not Google.")
    collect.add_argument("--depth", type=int, default=None)
    collect.add_argument("--max-pages", type=int, default=None)
    collect.add_argument("--keywords", default=None, help="Queries from a file (CSV with a query, keyword, queries or top queries column, or one per line) instead of the SmartCrew set")

    sub.add_parser("calibrate", help="Fit weights to the stored SERP order")

    test = sub.add_parser("test", help="Re-score queries after a page change")
    test.add_argument("source", nargs="?", default=None, help="A page URL, a staging URL, or a local HTML file")
    test.add_argument("--url", default=None, help="Canonical URL when source is a file or staging host")
    test.add_argument("--replaces", default=None, help="Stored URL whose HTML should be replaced")
    test.add_argument(
        "--bundle",
        default=None,
        help="Path to a manifest.csv (url,file) of built HTML. Scores with leave-one-query-out LambdaMART.",
    )
    test.add_argument("--serp", default="smartcrew/serp.csv", help="Labeled Google file used with --bundle")
    test.add_argument("--out", default=None, help="Write the --bundle report to this path")
    test.add_argument(
        "--three-way",
        nargs=2,
        metavar=("V1", "V2"),
        default=None,
        help="Two manifest.csv paths. Scores live, v1, and v2 with one leave-one-query-out fit.",
    )
    test.add_argument(
        "--regression",
        nargs=2,
        metavar=("V2", "V3"),
        default=None,
        help="Score live, v2, and v3 with one fit and a same-set check. Does not rerun cross-validation.",
    )

    track = sub.add_parser("track", help="Record today's real ranks for the keyword set")
    track.add_argument("--provider", default="auto")
    track.add_argument("--mock", action="store_true")
    track.add_argument("--depth", type=int, default=None)
    track.add_argument("--keywords", default=None, help="Queries from a file (CSV with a query, keyword, queries or top queries column, or one per line) instead of the SmartCrew set")

    report = sub.add_parser("report", help="Write the HTML report")
    report.add_argument("--out", default=None)

    train = sub.add_parser("train", help="Train LambdaMART on a real Google position file")
    train.add_argument("serp_file", help="CSV with query, url, position")
    train.add_argument("--model-out", default="data/ranksim-lambdamart.txt")
    train.add_argument("--folds", choices=["loo", "kfold"], default="loo")

    evaluate = sub.add_parser("evaluate", help="Cross-validate LambdaMART on a real Google position file")
    evaluate.add_argument("serp_file", help="CSV with query, url, position")
    evaluate.add_argument("--folds", choices=["loo", "kfold"], default="loo")
    evaluate.add_argument("--coverage-csv", default=None, help="Write one row per label: stored HTML or the reason it is missing")

    index_serp = sub.add_parser("index-serp", help="Crawl URLs from a Google position file plus the SmartCrew sitemap")
    index_serp.add_argument("serp_file")
    index_serp.add_argument("--delay", type=float, default=0.4)

    graph = sub.add_parser("graph", help="Crawl a polite link graph from the client site and labeled competitors")
    graph.add_argument("--serp", default="smartcrew/serp.csv")
    graph.add_argument("--out", default="smartcrew/linkgraph.json")
    graph.add_argument("--max-fetches", type=int, default=50)
    graph.add_argument("--internal-depth", type=int, default=2)
    graph.add_argument("--outbound-depth", type=int, default=1)

    graph_eval = sub.add_parser("graph-eval", help="Compare held-out pairwise agreement with and without link-graph features")
    graph_eval.add_argument("--serp", default="smartcrew/serp.csv")
    graph_eval.add_argument("--graph", default="smartcrew/linkgraph.json")

    noise = sub.add_parser("noise", help="Compare two Google snapshots. One file is not enough.")
    noise.add_argument("snapshots", nargs="*")
    noise.add_argument("--from-db", action="store_true", help="Compare one pair of collections per query with the same source, provider and locale, from different days when it has them (dated, Google, not synthetic)")

    export_serp = sub.add_parser(
        "export-serp", help="Write collected results lists as a query,url,position CSV (for evaluate, noise or ledger observe)"
    )
    export_serp.add_argument("out")
    export_serp.add_argument("--all", action="store_true", help="Every collection, not only the latest Google list per query (evaluate, noise and ledger observe refuse such a file)")
    export_serp.add_argument("--include-synthetic", action="store_true", help="Also mock results (marked synthetic; never Google; readers drop them)")

    competitors = sub.add_parser("competitors", help="Import competitor domains")
    comp_sub = competitors.add_subparsers(dest="competitors_command", required=True)
    comp_import = comp_sub.add_parser("import", help="Save a competitor list, optionally from the labeled top 10")
    comp_import.add_argument("domains", nargs="*", help="Domains or URLs")
    comp_import.add_argument("--file", default=None, help="Text or CSV file of domains")
    comp_import.add_argument("--from-serps", action="store_true", help="Take domains that appear in the labeled top 10")
    comp_import.add_argument("--serp", default="smartcrew/serp.csv")
    comp_import.add_argument("--crawl", action="store_true", help="Fetch a homepage when that host is not stored yet")
    comp_import.add_argument("--max-fetches", type=int, default=20)
    comp_import.add_argument("--out", default="smartcrew/competitors.csv")

    space = sub.add_parser("space", help="Print how the client stacks up against imported competitors")
    space.add_argument("--serp", default="smartcrew/serp.csv")
    space.add_argument("--competitors", default="smartcrew/competitors.csv")

    bench = sub.add_parser(
        "benchmark",
        help="Model vs simple baselines on identical query-held-out folds, with bootstrap intervals",
    )
    bench.add_argument("serp_file", nargs="?", default="smartcrew/serp.csv")
    bench.add_argument(
        "--protocol",
        action="append",
        choices=["loqo", "cluster", "cluster-url"],
        help="Repeat to pick several. Default: all three.",
    )
    bench.add_argument("--cluster-threshold", type=float, default=None, help="Default 0.3; with --snapshot, the snapshot's own value")
    bench.add_argument("--bootstrap", type=int, default=2000)
    bench.add_argument("--seed", type=int, default=0)
    bench.add_argument(
        "--as-of",
        default=None,
        help="Date (YYYY-MM-DD) for date-dependent features such as freshness. Default: today. Recorded in the output.",
    )
    bench.add_argument("--graph", default=None, help="Optional link-graph JSON to attach, as graph-eval does")
    bench.add_argument(
        "--with-crawl-artifacts",
        action="store_true",
        help="Add speed, orphan, link_depth and crawl_inlinks back into the model (experiments only)",
    )
    bench.add_argument("--json", dest="json_out", default=None, help="Write the full report as JSON")
    bench.add_argument("--coverage-csv", default=None, help="Write one row per label: stored HTML or the reason it is missing")
    bench.add_argument(
        "--model",
        choices=["default", "regularized"],
        default="default",
        help="LambdaMART configuration for the lambdamart row (and the model id of a --final run)",
    )
    bench.add_argument(
        "--candidate",
        choices=["regularized", "default"],
        default=None,
        help="Also score this configuration on the same folds, report its in-sample vs held-out gap, and apply the fixed adoption rule",
    )
    bench.add_argument(
        "--snapshot",
        default=None,
        help="Run on a frozen snapshot (benchmark-freeze) instead of the live database. Scores the dev split only.",
    )
    bench.add_argument(
        "--final",
        action="store_true",
        help="With --snapshot: train on dev, score the final split once for this model configuration, and append to results.jsonl",
    )

    freeze = sub.add_parser(
        "benchmark-freeze",
        help="Freeze labels, every stored page and a dev/final query-cluster split into a directory (keep it out of git)",
    )
    freeze.add_argument("serp_file", nargs="?", default="smartcrew/serp.csv")
    freeze.add_argument("--out", required=True, help="New directory for the snapshot. An existing snapshot is never overwritten.")
    freeze.add_argument("--as-of", required=True, help="Date (YYYY-MM-DD) the labels describe; freshness features use it")
    freeze.add_argument("--final-share", type=float, default=0.3, help="Share of queries (whole clusters only) held out as the final test set")
    freeze.add_argument("--seed", type=int, default=0)
    freeze.add_argument("--cluster-threshold", type=float, default=0.3)
    freeze.add_argument("--graph", default=None, help="Optional link-graph JSON to freeze with the pages")

    sub.add_parser(
        "reextract",
        help="Re-run text extraction on stored HTML after an extractor change (no network)",
    )
    ledger = sub.add_parser("ledger", help="Prediction track record: verify, list, record observed results, score")
    ledger_sub = ledger.add_subparsers(dest="ledger_command", required=True)
    ledger_verify = ledger_sub.add_parser("verify", help="Check that no recorded prediction was changed, removed, or reordered")
    ledger_verify.add_argument(
        "--expect-head",
        default=None,
        help="A latest hash published earlier; fails if it is no longer in the chain (newest rows removed). "
        "Publish and check the full hash. A prefix of 12 characters or more is accepted as a weaker check.",
    )
    ledger_list = ledger_sub.add_parser("list", help="Show recorded predictions")
    ledger_list.add_argument("--limit", type=int, default=20)
    ledger_list.add_argument("--project", default=None, help="Only this project slug")
    observe = ledger_sub.add_parser("observe", help="Append observed Google positions (query,url,position CSV)")
    observe.add_argument("serp_file")
    observe.add_argument(
        "--observed-on",
        required=True,
        help="Date the results were collected, YYYY-MM-DD. A file with rows dated another day, dates not written as "
        "YYYY-MM-DD, undated rows in a file with dates, or rows from another provider is refused.",
    )
    observe.add_argument("--provider", required=True, help="Where the results came from, e.g. serper, manual_browser")
    observe.add_argument(
        "--depth",
        type=int,
        default=None,
        help="How many organic results were collected per query. Without it, a page missing from the file is unresolved, not 'below'.",
    )
    observe.add_argument(
        "--project",
        default="default",
        help="Project slug the observations belong to. Predictions match outcomes of the same project only. Default: default",
    )
    ledger_score = ledger_sub.add_parser("score", help="Score predictions against the first later observation")
    ledger_score.add_argument("--project", default=None, help="Score only this project slug")

    ai_eval = sub.add_parser("ai-eval", help="LLM relevance CV delta and one AI fix loop. Needs an LLM key.")
    ai_eval.add_argument("--serp", default="smartcrew/serp.csv")
    ai_eval.add_argument("--query", default="dirt work contractor software")
    ai_eval.add_argument(
        "--html",
        default="smartcrew/pr8_v3/blog-best-software-small-dirt-work-contractors.html",
    )
    ai_eval.add_argument(
        "--url",
        default="https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors/",
    )
    ai_eval.add_argument(
        "--allow-small",
        action="store_true",
        help="Run the paid judge even when there are too few labeled pairs to measure anything",
    )
    ai_eval.add_argument("--rounds-dir", default=None, help="Write each AI rewrite round as an HTML file here")

    serve = sub.add_parser("serve", help="Serve the pre-publish rank check demo")
    serve.add_argument("--host", default="127.0.0.1", help="Default is local only. The demo has no authentication.")
    serve.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Extra Host header names to serve (required when --host is not a loopback address)",
    )
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--serp", default="smartcrew/serp.csv")

    args = parser.parse_args(argv)
    settings = get_settings()
    database = args.database or settings.ranksim_database_url
    try:
        if args.command == "keywords":
            _keywords(args.refresh_sitemap)
        elif args.command == "gaps":
            _gaps()
        elif args.command == "collect":
            _collect(settings, database, args)
        elif args.command == "calibrate":
            _calibrate(database)
        elif args.command == "test":
            if args.regression:
                _test_regression(database, args)
            elif args.three_way:
                _test_three_way(database, args)
            elif args.bundle and args.source:
                parser.error("Pass either a source page or --bundle, not both")
            elif args.bundle:
                _test_bundle(database, args)
            elif args.source:
                _test(settings, database, args)
            else:
                parser.error("test needs a source page, --bundle, --three-way, or --regression")
        elif args.command == "track":
            _track(settings, database, args)
        elif args.command == "report":
            _report(database, args.out)
        elif args.command == "train":
            _train(database, args)
        elif args.command == "evaluate":
            _evaluate(database, args)
        elif args.command == "index-serp":
            _index_serp(settings, database, args)
        elif args.command == "graph":
            _graph(settings, database, args)
        elif args.command == "graph-eval":
            _graph_eval(database, args)
        elif args.command == "noise":
            _noise(args, database)
        elif args.command == "export-serp":
            _export_serp(database, args)
        elif args.command == "serve":
            _serve(settings, database, args)
        elif args.command == "benchmark":
            if args.final and not args.snapshot:
                parser.error("--final needs --snapshot: the final split exists only in a frozen snapshot")
            if args.snapshot:
                _benchmark_snapshot(args, database)
            else:
                _benchmark(database, args)
        elif args.command == "benchmark-freeze":
            _benchmark_freeze(database, args)
        elif args.command == "ledger":
            _ledger(database, args)
        elif args.command == "reextract":
            from aeo.ranksim.loop import reextract_pages

            session = open_session(database)
            try:
                total, changed = reextract_pages(session)
            finally:
                session.close()
            print(f"Re-extracted {total} stored pages from their saved HTML. Word count changed on {changed}.")
        elif args.command == "ai-eval":
            _ai_eval(settings, database, args)
        elif args.command == "competitors":
            _competitors(settings, database, args)
        elif args.command == "space":
            _space(database, args)
        else:
            parser.error(f"Unknown command {args.command}")
    except ProviderConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


def _graph(settings, database: str, args) -> None:
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.linkgraph import collect_graph, save_graph
    from aeo.ranksim.loop import store_fetched_page, FetchResult
    from aeo.ranksim.saved_html import load_saved_html
    from aeo.ranksim.store import open_session

    labels = load_serp_file(args.serp)
    saved, missing = load_saved_html()
    if missing:
        print("Saved HTML rows with no file were skipped:")
        for url in missing:
            print(f"missing file {url}")
    graph = collect_graph(
        settings,
        labels,
        saved_html=saved,
        our_internal_depth=args.internal_depth,
        outbound_depth=args.outbound_depth,
        max_fetches=args.max_fetches,
    )
    save_graph(graph, args.out)
    from aeo.ranksim.store import EVALUATION_EXCLUDED_SOURCES, page_by_url

    session = open_session(database)
    try:
        stored = 0
        kept = 0
        for url, node in graph.nodes.items():
            if node.status != "fetched" or not node.html:
                continue
            existing = page_by_url(session, url)
            if (
                existing is not None
                and existing.status == "fetched"
                and (existing.raw_html or "").strip()
                and (existing.source or "unknown") not in EVALUATION_EXCLUDED_SOURCES
            ):
                # Keep the evaluation snapshot (and its URL spelling) that index-serp stored.
                # A graph refetch used to add a trailing-slash twin and overwrite timings, which
                # changed the benchmark without changing coverage.
                kept += 1
                continue
            store_fetched_page(session, FetchResult(url=url, html=node.html, status="fetched", source="graph_crawl"))
            stored += 1
    finally:
        session.close()
    print(f"Link graph: {len(graph.nodes)} pages, {len(graph.edges)} edges, {graph.fetches} fetches, {graph.blocked} robots blocks.")
    print(f"Stored {stored} new pages into the rank database; kept {kept} existing snapshots unchanged.")
    print(f"Wrote {args.out}")
    for note in graph.notes:
        print(note)
    print("In-crawl inlinks are not web-wide backlinks. No backlink API was called.")


def _graph_eval(database: str, args) -> None:
    _flag_old_drafts(database)
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.features import MODEL_FEATURES
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.linkgraph import attach_graph, load_graph
    from pathlib import Path

    labels = load_serp_file(args.serp)
    intents = query_intents(labels).intents
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    base_names = list(MODEL_FEATURES)
    graph_names = base_names + ["orphan", "link_depth", "crawl_inlinks"]
    before_examples, uncovered, _withheld = build_examples(labels, pages, intents)
    before = loo_fit(before_examples, base_names)
    print("Predicted metrics. Not live Google ranks.")
    print(f"Without link-graph features: pairwise {_fmt(before.pairwise)} on {before.n_pairs} pairs, {before.n_queries} queries. Uncovered rows: {uncovered}.")
    path = Path(args.graph)
    if not path.is_file():
        print(f"No graph file at {path}. Link-feature impact was not measured.")
        return
    graph = load_graph(str(path))
    attached = attach_graph(pages, graph)
    after_examples, _uncovered, _withheld = build_examples(labels, pages, intents)
    varied = _graph_feature_varies(after_examples)
    after = loo_fit(after_examples, graph_names)
    print(f"With link-graph features: pairwise {_fmt(after.pairwise)} on {after.n_pairs} pairs. Pages updated from the graph: {attached}.")
    if not varied:
        print("Link features did not vary on the stored pages, so a change in pairwise agreement is not evidence they helped.")
    elif before.pairwise is not None and after.pairwise is not None:
        print(f"Pairwise delta: {after.pairwise - before.pairwise:+.3f}.")
    print("Web-wide backlinks were not included. No DataForSEO key was used.")


def _graph_feature_varies(examples) -> bool:
    """True when a link-graph feature takes more than one value on the labeled examples."""
    for name in ("orphan", "link_depth", "crawl_inlinks"):
        values = {row.features.get(name) for row in examples}
        values = {value for value in values if value is not None and value == value}
        if len(values) > 1:
            return True
    return False


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _noise(args, database: str) -> None:
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.noise import noise_ceiling

    if args.from_db:
        left, right, pairing = _paired_collections(database)
        compared = pairing["compared"]
        unpaired = (
            f"Could not pair {pairing['unpaired']} queries that have more than one collection. Their collections differ "
            "in source, provider, country, language or device, or are undated label files or not Google results."
        )
        unclassified = (
            f"{pairing['unclassified']} queries have lists that an older version stored from a dated file. Such a file "
            "may have been a Search Console export of average positions, so those lists are not compared. "
            "Run index-serp again on each original file to classify it."
        )
        if not compared:
            print("Noise ceiling was not computed. No query has two comparable non-synthetic collections in the database yet.")
            if pairing["unpaired"]:
                print(unpaired)
            if pairing["unclassified"]:
                print(unclassified)
            return
        print(f"Comparing one pair of collections for each of {compared} queries from the database.")
        print(
            "For each query, the newest collection that has a comparable one from an earlier day is paired with the "
            "latest such collection. A pair from the same day is used only when there is none."
        )
        groups = ", ".join(f"{name} ({count} queries)" for name, count in sorted(pairing["groups"].items()))
        print(f"Each pair has the same source, provider, country, language and device: {groups}.")
        if pairing["unpaired"]:
            print(unpaired)
        if pairing["unclassified"]:
            print(unclassified)
        if pairing["same_day"]:
            print(
                f"{pairing['same_day']} of {compared} pairs were collected on the same day. "
                "They show stability within a day, not between days."
            )
        report = noise_ceiling(left, right)
    elif len(args.snapshots) < 2:
        print("Noise ceiling was not computed. Pass two snapshot CSVs of the same queries from different days, or --from-db.")
        return
    else:
        from aeo.ranksim.groundtruth import ImportReport

        left_report, right_report = ImportReport(), ImportReport()
        left = load_serp_file(args.snapshots[0], report=left_report)
        right = load_serp_file(args.snapshots[1], report=right_report)
        left, right, shared, left_out = _comparable_file_lists(left, left_report, right, right_report)
        if left_out:
            print(
                f"Left out {sum(left_out.values())} of {shared} queries that are in both files, because their two "
                "lists are not comparable snapshots from different days."
            )
            for reason, text in _FILE_PAIR_REASONS.items():
                if left_out[reason]:
                    print(text.format(count=left_out[reason]))
            if sum(left_out.values()) == shared:
                print("Noise ceiling was not computed. No query in both files has two comparable lists.")
                return
        report = noise_ceiling(left, right)
    print(report.note)
    if not report.computed:
        print("No pairwise agreement, top-1 rate, or position change is reported.")
        return
    def fmt(value):
        return "n/a" if value is None else f"{value:.3f}"

    print(f"Queries compared: {report.n_queries}.")
    print(f"Pairwise agreement, URLs present both days: {fmt(report.pairwise_agreement)} on {report.n_pairs} pairs.")
    print(f"Pairwise agreement counting URLs that entered or left: {fmt(report.pairwise_with_churn)}.")
    print(f"Mean list overlap: {fmt(report.mean_overlap)}. URLs entered: {report.entered}. URLs dropped: {report.dropped}.")
    print(f"Top-1 same rate: {fmt(report.top1_same_rate)}.")
    print(f"Mean absolute position change (URLs present both days): {fmt(report.mean_abs_position_change)}.")


def _paired_collections(database: str):
    """(older labels, newer labels, pairing summary): one pair of comparable collections per query.

    Two collections are comparable only when they share source, provider, country, language and
    device. Synthetic and non-Google lists and label files with no date are never compared: an
    undated file's stored time is when it was imported, not when it was observed. Nor is a dated
    list an older version stored that may be Search Console averages (store.legacy_unclassified).
    A query's pair is the newest collection that has a comparable one from an earlier UTC day,
    with the latest such collection, so a re-collect later on the same day does not hide the
    day-to-day change. A same-day pair is used only when the query has no pair across days.
    """
    from collections import Counter

    from aeo.ranksim.groundtruth import SerpLabel
    from aeo.ranksim.store import collection_dated, collection_hits, legacy_unclassified, serp_collections

    def key(collection):
        return (collection.source, collection.provider, collection.country, collection.language, collection.device)

    session = open_session(database)
    left: list = []
    right: list = []
    pairing = {"compared": 0, "unpaired": 0, "same_day": 0, "unclassified": 0, "groups": Counter()}
    try:
        by_query: dict[str, list] = {}
        for collection in serp_collections(session):
            by_query.setdefault(collection.query, []).append(collection)
        for query, collections in sorted(by_query.items()):
            pairing["unclassified"] += any(legacy_unclassified(c) for c in collections)
            usable = [c for c in collections if c.is_google and collection_dated(c)]
            pair = same_day = None
            for index in range(len(usable) - 1, 0, -1):
                newer = usable[index]
                earlier = [c for c in usable[:index] if key(c) == key(newer)]
                across = [c for c in earlier if c.collected_at.date() < newer.collected_at.date()]
                if across:
                    pair = (across[-1], newer)
                    break
                if earlier and same_day is None:
                    same_day = (earlier[-1], newer)
            pair = pair or same_day
            if pair is None:
                if len(collections) >= 2:
                    pairing["unpaired"] += 1
                continue
            pairing["compared"] += 1
            older, newer = pair
            if older.collected_at.date() == newer.collected_at.date():
                pairing["same_day"] += 1
            locale = "/".join(value or "default" for value in key(newer)[2:])
            pairing["groups"][f"{newer.source} {newer.provider} {locale}"] += 1
            for target, collection in ((left, older), (right, newer)):
                day = collection.collected_at.date().isoformat()
                target.extend(
                    SerpLabel(query, hit.url, float(hit.position), None, "database", observed_on=day,
                              country=collection.country, language=collection.language, device=collection.device,
                              provider=collection.provider)
                    for hit in collection_hits(session, collection.id)
                )
    finally:
        session.close()
    return left, right, pairing


_LIST_SETTINGS = ("provider", "country", "language", "device")
_FILE_PAIR_REASONS = {
    "undated": "No observation date in a file that has dates (a label file with no date): {count}.",
    "same": "Same collection_id in both files, so the list was not collected again in between: {count}.",
    "settings": "Different provider, country, language or device: {count}.",
    "unreadable": "Date that cannot be read as YYYY-MM-DD: {count}. Write each date as YYYY-MM-DD.",
    "later": "Dated later in the first file than in the second: {count}. Pass the earlier file first.",
    "same_day": "Dated the same day in both files, or on days that overlap: {count}. Compare files from different days.",
}


def _row_day(value: str) -> str | None:
    """The YYYY-MM-DD day of an observed_on or collected_at value, or None when it cannot be read as one."""
    from datetime import date

    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def _file_lists(labels, report) -> dict[str, dict]:
    """What a results file says about each query's list: its days, dates it cannot read as YYYY-MM-DD,
    whether a row is undated in a file that has dates, its collection ids, and its provider, country,
    language and device.

    A file has dates when it has a date column or collection ids. export-serp leaves the date of an
    undated label file blank, because its stored time is when it was imported, not when it was observed.
    """
    dated_file = "observed_on" in report.metadata_columns or any(label.collection_id for label in labels)
    lists: dict[str, dict] = {}
    for label in labels:
        entry = lists.setdefault(
            label.query,
            {
                "days": set(), "unreadable": set(), "undated": False, "collections": set(),
                **{name: set() for name in _LIST_SETTINGS},
            },
        )
        if label.observed_on:
            day = _row_day(label.observed_on)
            if day:
                entry["days"].add(day)
            else:
                entry["unreadable"].add(label.observed_on.strip())
        elif dated_file:
            entry["undated"] = True
        if label.collection_id:
            entry["collections"].add(label.collection_id)
        for name in _LIST_SETTINGS:
            value = getattr(label, name)
            if value:
                entry[name].add(value.casefold())
    return lists


def _comparable_file_lists(left, left_report, right, right_report):
    """(left labels, right labels, queries in both files, Counter of reasons for those left out).

    The like-with-like rule of noise --from-db, for two results files. A query in both files is left
    out when a row is undated in a file that has dates, when its list has the same collection_id in
    both (it was not collected again), when the provider, country, language or device are set in both
    files and differ, when a date in either file is not YYYY-MM-DD (so its order cannot be checked),
    or when its first-file day is not earlier than its second-file day. Files with no such columns
    are compared as they are.
    """
    from collections import Counter

    a_lists, b_lists = _file_lists(left, left_report), _file_lists(right, right_report)
    shared = sorted(set(a_lists) & set(b_lists))
    reasons: dict[str, str] = {}
    for query in shared:
        a, b = a_lists[query], b_lists[query]
        if a["undated"] or b["undated"]:
            reasons[query] = "undated"
        elif a["collections"] & b["collections"]:
            reasons[query] = "same"
        elif any(a[name] and b[name] and a[name] != b[name] for name in _LIST_SETTINGS):
            reasons[query] = "settings"
        elif a["unreadable"] or b["unreadable"]:
            reasons[query] = "unreadable"
        elif a["days"] and b["days"]:
            # YYYY-MM-DD days sort as dates. Swapping the files helps only when every first-file day is later.
            if min(a["days"]) > max(b["days"]):
                reasons[query] = "later"
            elif max(a["days"]) >= min(b["days"]):
                reasons[query] = "same_day"
    if not reasons:
        return left, right, len(shared), Counter()
    return (
        [label for label in left if label.query not in reasons],
        [label for label in right if label.query not in reasons],
        len(shared),
        Counter(reasons.values()),
    )


def _observe_conflicts(labels, report, observed_on: str, provider: str, name: str) -> str | None:
    """Why ledger observe must not record this file with one date and provider, or None.

    Every query is recorded as observed on --observed-on from --provider. A row the file dates on
    another day, a date that is not YYYY-MM-DD, a blank date in a file that has dates (an undated
    label file, or a list that may be Search Console averages, from export-serp; see
    store.legacy_unclassified), or a row from another provider would be recorded with a date or
    provider it does not have.
    """
    lists = _file_lists(labels, report)
    total = len(lists)
    wanted = provider.strip().casefold()
    other_day = sorted(query for query, entry in lists.items() if entry["days"] - {observed_on})
    unreadable = sorted(query for query, entry in lists.items() if entry["unreadable"])
    undated = sorted(query for query, entry in lists.items() if entry["undated"])
    other_provider = sorted(query for query, entry in lists.items() if entry["provider"] - {wanted})
    if not (other_day or unreadable or undated or other_provider):
        return None
    lines = [
        f"Nothing was recorded. ledger observe records one day and one provider per file, and {name} has lists "
        f"it cannot record as observed on {observed_on} from {provider}."
    ]
    if other_day:
        days = ", ".join(sorted(lists[other_day[0]]["days"] - {observed_on}))
        lines.append(f"Observed on another day: {len(other_day)} of {total} queries, for example {other_day[0]!r} on {days}.")
    if unreadable:
        values = ", ".join(sorted(lists[unreadable[0]]["unreadable"]))
        lines.append(
            f"Date that cannot be read as YYYY-MM-DD: {len(unreadable)} of {total} queries, "
            f"for example {unreadable[0]!r} with {values}. Write each date as YYYY-MM-DD, such as {observed_on}."
        )
    if undated:
        lines.append(
            f"No observation date in a file that has dates: {len(undated)} of {total} queries, for example {undated[0]!r}. "
            "export-serp leaves the date blank for a label file with no date. "
            "It also leaves it blank for a list an older version stored from a dated file, because that file may "
            "have been a Search Console export of average positions. Run index-serp again on that original file "
            "to classify it, then export again. "
            "In a file you wrote, fill in the dates, or remove the date column if it is empty."
        )
    if other_provider:
        providers = ", ".join(sorted(lists[other_provider[0]]["provider"] - {wanted}))
        lines.append(
            f"From another provider than {provider}: {len(other_provider)} of {total} queries, "
            f"for example {other_provider[0]!r} from {providers}."
        )
    if other_day or undated or other_provider:
        lines.append(
            "Record each day and provider from its own file with that --observed-on and --provider, "
            "or re-collect those queries and export again."
        )
    return "\n".join(lines)


def _export_serp(database: str, args) -> None:
    import csv

    from aeo.ranksim.store import (
        collection_dated,
        collection_hits,
        legacy_dated_import,
        legacy_unclassified,
        pick_latest,
        serp_collections,
    )

    session = open_session(database)
    written = undated = unclassified = left_out = 0
    not_google: set[str] = set()
    try:
        collections = serp_collections(session, include_synthetic=args.include_synthetic)
        if not args.all:
            # Readers treat every row as a Google results page, so each query gets its latest
            # Google list. A query with no Google list gets its latest list, marked google=no.
            by_query: dict[str, list] = {}
            for collection in collections:
                by_query.setdefault(collection.query, []).append(collection)
            picked = [pick_latest([c for c in group if c.is_google] or group) for group in by_query.values()]
            collections = [c for c in picked if c is not None]
            left_out = len(picked) - len(collections)  # see pick_latest
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "query", "url", "position", "observed_on", "country", "language", "device", "provider", "synthetic",
                "collected_at", "collection_id", "google",
            ])
            for collection in collections:
                # An undated label file was stored at its import time. That is not when the list
                # was observed, so its date columns stay blank.
                dated = collection_dated(collection)
                if legacy_unclassified(collection):
                    unclassified += 1
                elif not dated and not legacy_dated_import(collection):
                    undated += 1
                if not collection.is_google and not collection.synthetic:
                    not_google.add(collection.provider)
                for hit in collection_hits(session, collection.id):
                    writer.writerow([
                        collection.query, hit.url, hit.position, collection.collected_at.date().isoformat() if dated else "",
                        collection.country or "", collection.language or "", collection.device or "", collection.provider,
                        "yes" if collection.synthetic else "", collection.collected_at.isoformat() if dated else "", collection.id,
                        "yes" if collection.is_google else "no",
                    ])
                    written += 1
    finally:
        session.close()
    print(f"Wrote {written} rows from {len(collections)} collections to {args.out}.")
    if undated:
        print(f"{undated} collections come from label files with no date. Their observed_on is blank, not the import date.")
    if unclassified:
        print(
            f"{unclassified} collections come from dated files that an older version stored. Such a file may have been "
            "a Search Console export of average positions, so their observed_on is blank. "
            "Run index-serp again on each original file to classify it."
        )
    if left_out:
        print(
            f"{left_out} queries were left out. Each has a later list that an older version stored from a dated file, "
            "which may have been a Search Console export of average positions. "
            "Run index-serp again on that file to classify it."
        )
    if not_google:
        print(
            f"Some collections are not Google results ({', '.join(sorted(not_google))}). "
            "Their rows say google=no, and readers drop them."
        )
    if args.include_synthetic:
        print("Rows marked synthetic come from the mock provider. They are not Google results. Readers drop them.")
    if args.all:
        print(
            "Every collection is included, so a query can appear more than once. evaluate, noise and ledger observe "
            "refuse a file with more than one collection of a query. Export without --all for them."
        )


def _competitors(settings, database: str, args) -> None:
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.space import crawl_competitor_homepages, domains_from_labels, parse_competitor_text, write_competitors

    domains: list[str] = []
    source_bits = []
    rejected: list[tuple[int, str, str]] = []
    kinds: dict[str, set[str]] = {}
    if args.from_serps:
        found = domains_from_labels(load_serp_file(args.serp), "smartcrewhq.com")
        domains.extend(found)
        for domain in found:
            kinds.setdefault(domain, set()).add("serp")
        source_bits.append("labeled_top10")
    if args.file:
        file_rejected: list[tuple[int, str, str]] = []
        listed = parse_competitor_text(Path(args.file).read_text(encoding="utf-8"), file_rejected)
        domains.extend(listed)
        for domain in listed:
            kinds.setdefault(domain, set()).add("business")
        rejected += [(f"{args.file} line {number}", text, reason) for number, text, reason in file_rejected]
        source_bits.append("file")
    if args.domains:
        argv_rejected: list[tuple[int, str, str]] = []
        listed = parse_competitor_text("\n".join(args.domains), argv_rejected)
        domains.extend(listed)
        for domain in listed:
            kinds.setdefault(domain, set()).add("business")
        rejected += [(f"argument {number}", text, reason) for number, text, reason in argv_rejected]
        source_bits.append("argv")
    for where, text, reason in rejected:
        print(f"Rejected {where}: {text!r}: {reason}.")
    ordered = list(dict.fromkeys(domains))
    if not ordered:
        raise SystemExit("No competitors. Pass domains, --file, or --from-serps. Nothing was invented.")
    kind_names = {domain: ("both" if len(found) > 1 else next(iter(found))) for domain, found in kinds.items()}
    write_competitors(args.out, ordered, "+".join(source_bits) or "import", kind_names)
    print(f"Wrote {len(ordered)} competitors to {args.out}")
    for domain in ordered:
        print(domain)
    if not args.crawl:
        print("No crawl. Pass --crawl to fetch homepages that are not stored yet.")
        return
    fetched, skipped, errors = crawl_competitor_homepages(settings, ordered, database, max_fetches=args.max_fetches)
    print(f"Homepage fetches: {fetched}. Already stored: {skipped}.")
    for error in errors:
        print(f"not stored {error}")


def _space(database: str, args) -> None:
    _flag_old_drafts(database)
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.linkgraph import attach_graph, load_graph
    from aeo.ranksim.space import build_space, domains_from_labels, read_competitor_kinds, read_competitors, render_space_text
    from pathlib import Path

    labels = load_serp_file(args.serp)
    competitors = read_competitors(args.competitors)
    source = args.competitors
    if not competitors:
        competitors = domains_from_labels(labels, "smartcrewhq.com")
        source = "labeled top 10 (no competitors file)"
    intents = query_intents(labels).intents
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    graph_path = repo_root() / "smartcrew" / "linkgraph.json"
    if graph_path.is_file():
        attach_graph(pages, load_graph(str(graph_path)))
    examples, uncovered, _withheld = build_examples(labels, pages, intents)
    fit = loo_fit(examples, keep_models=True)
    if source == args.competitors:
        kinds = read_competitor_kinds(args.competitors)
    else:
        kinds = {domain: "serp" for domain in competitors}  # taken from the labeled results
    report = build_space(labels, pages, fit.models, competitors, intents=intents, kinds=kinds)
    print(f"Competitors from {source}. Uncovered labeled rows: {uncovered}.")
    if fit.pairwise is not None:
        print(f"PREDICTED backtest pairwise {fit.pairwise:.3f} on {fit.n_pairs} pairs.")
    print(render_space_text(report), end="")


def _flag_old_drafts(database: str) -> None:
    """Databases seeded before page provenance hold drafts as ordinary pages; mark them first."""
    from aeo.ranksim.bootstrap import flag_known_drafts

    flagged = flag_known_drafts(database)
    if flagged:
        print(f"{flagged} stored pages match bundled draft HTML and were marked as drafts, not evaluation rows.")
    from aeo.ranksim.bootstrap import backfill_js_shell

    shells = backfill_js_shell(database)
    if shells:
        print(f"{shells} older stored pages are client-rendered shells and were marked; they are not evaluation rows.")


def _bench_corpus(database: str, labels, coverage_csv: str | None = None):
    """Pages, labeled-row coverage and (url, html) of every stored page for the fingerprint."""
    from aeo.ranksim.benchmark import coverage_rows, write_coverage_csv
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.store import RsPage

    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        rows = coverage_rows(labels, session)
        coverage: dict[str, int] = {}
        for row in rows:
            coverage[row["reason"]] = coverage.get(row["reason"], 0) + 1
        from aeo.ranksim.benchmark import coverage_flags

        if coverage_flags(rows):
            print(coverage_flags(rows))
        if coverage_csv:
            write_coverage_csv(rows, coverage_csv)
            print(f"Wrote per-label coverage to {coverage_csv}")
        stored = [
            (url, html or "")
            for url, html in session.query(RsPage.url, RsPage.raw_html).filter(RsPage.status == "fetched").all()
        ]
    finally:
        session.close()
    return pages, coverage, stored


def _attach_graph_file(pages, graph_file, notes: list[str]) -> None:
    from aeo.ranksim.benchmark import file_sha256
    from aeo.ranksim.linkgraph import attach_graph, load_graph

    graph_path = Path(graph_file)
    if not graph_path.is_file():
        raise SystemExit(f"No graph file at {graph_path}.")
    attach_graph(pages, load_graph(str(graph_path)))
    notes.append(f"Link graph attached from {graph_path} (sha256 {file_sha256(graph_path)[:12]}).")


def _benchmark(database: str, args) -> None:
    _flag_old_drafts(database)
    from datetime import date

    if args.cluster_threshold is None:
        args.cluster_threshold = 0.3
    from aeo.ranksim.benchmark import file_sha256
    from aeo.ranksim.groundtruth import ImportReport, load_serp_file

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    import_report = ImportReport()
    labels = load_serp_file(args.serp_file, report=import_report)
    print("\n".join(import_report.lines()))
    intent_map = query_intents(labels)
    intents = intent_map.intents
    pages, coverage, stored = _bench_corpus(database, labels, args.coverage_csv)
    notes = [note for note in [intent_map.note({row.query for row in labels})] if note]
    if args.graph:
        _attach_graph_file(pages, args.graph, notes)
    if not args.as_of:
        notes.append(
            "as_of defaulted to today. Freshness depends on it, so rerunning on another day can change results."
        )
    report = _run_benchmark_report(
        args,
        labels=labels,
        pages=pages,
        coverage=coverage,
        stored=stored,
        intents=intents,
        as_of=as_of,
        labels_sha=file_sha256(args.serp_file),
        notes=notes + [line.strip() for line in import_report.lines()[1:]],
    )
    if report is not None:
        _print_benchmark(report, args)


def _model_feature_names(args) -> list[str]:
    from aeo.ranksim.features import FEATURE_NAMES, MODEL_FEATURES

    return list(FEATURE_NAMES) if args.with_crawl_artifacts else list(MODEL_FEATURES)


def _run_benchmark_report(
    args,
    *,
    labels,
    pages,
    coverage,
    stored,
    intents,
    as_of,
    labels_sha,
    notes,
    final_queries=None,
    cluster_labels=None,
):
    """Build examples, run the protocols, print the report. Returns the report, or None.

    With ``final_queries`` (a snapshot's final split), trains on every other query and scores
    those once (protocols ``final`` and ``final-url``).
    """
    from aeo.ranksim.benchmark import (
        CANDIDATE_PREFIX,
        FINAL_PROTOCOLS,
        PROTOCOLS,
        adoption_decision,
        build_report,
        corpus_fingerprint,
        default_scorers,
        in_sample_pairwise,
        lambdamart_scorer,
        query_clusters,
        run_benchmark,
        run_final,
    )
    from aeo.ranksim.lambdamart import build_examples

    names = _model_feature_names(args)
    examples, uncovered, withheld = build_examples(labels, pages, intents, today=as_of)
    if withheld:
        from aeo.ranksim.groundtruth import navboost_withhold_reason

        notes.append(f"navboost_prior was withheld: {navboost_withhold_reason(labels)}.")
    from aeo.ranksim.benchmark import has_prior_positions

    if withheld:
        notes.append(
            "No persistence row: the labels carry prior_position, but it was withheld for the reason above, so keeping "
            "the previous order is not scored. With valid dates it would be."
        )
    elif not has_prior_positions(examples):
        notes.append(
            "No persistence row: the labels carry no earlier Google position (prior_position), so keeping the previous "
            "order cannot be scored on this file."
        )
    notes.append(
        "site_prior is content blind: it counts in how many training queries the page's site appears. A page edit cannot move it."
    )
    if len({row.query for row in examples}) < 2:
        print("Benchmark not computed. Need at least 2 queries with 2 stored pages each.")
        from aeo.ranksim.benchmark import render_coverage

        print(render_coverage(coverage))
        return None
    cluster_source = cluster_labels if cluster_labels is not None else labels
    model_config = getattr(args, "model", "default")
    candidate = getattr(args, "candidate", None)
    scorers = default_scorers(names, args.seed, examples, config=model_config)
    if candidate:
        if final_queries is not None:
            raise SystemExit("--candidate is for the dev split. A final run scores one configuration (--model).")
        scorers[CANDIDATE_PREFIX + candidate] = lambdamart_scorer(names, config=candidate)
        gaps = []
        for name in (model_config, candidate):
            inside = in_sample_pairwise(examples, lambdamart_scorer(names, config=name))
            gaps.append(f"{name} {inside:.3f}" if inside is not None else f"{name} n/a")
        notes.append(
            "In-sample pairwise (trained and scored on the same queries; compare with the held-out rows above): "
            + ", ".join(gaps) + "."
        )
    if model_config != "default":
        notes.append(f"lambdamart uses the {model_config} configuration.")
    try:
        if final_queries is None:
            clusters, results = run_benchmark(
                examples,
                protocols=tuple(args.protocol or PROTOCOLS),
                cluster_threshold=args.cluster_threshold,
                bootstrap=args.bootstrap,
                seed=args.seed,
                feature_names=names,
                cluster_source=cluster_source,
                scorers=scorers,
            )
            if candidate:
                for result in results:
                    if result.protocol == "cluster-url":
                        notes.append(adoption_decision(result, candidate))
        else:
            all_clusters = query_clusters(cluster_source, args.cluster_threshold)
            used = {row.query for row in examples}
            clusters = {query: cluster for query, cluster in all_clusters.items() if query in used}
            results = run_final(
                examples,
                set(final_queries),
                all_clusters,
                scorers,
                protocols=FINAL_PROTOCOLS,
                bootstrap=args.bootstrap,
                seed=args.seed,
            )
    except ValueError as exc:
        print(f"Benchmark not computed. {exc}")
        return None
    report = build_report(
        examples=examples,
        n_labels=len(labels),
        uncovered=uncovered,
        coverage=coverage,
        clusters=clusters,
        protocols=results,
        as_of=as_of,
        labels_sha=labels_sha,
        corpus_sha=corpus_fingerprint(stored) if stored else None,
        corpus_pages=len(stored),
        cluster_threshold=args.cluster_threshold,
        bootstrap=args.bootstrap,
        seed=args.seed,
        feature_names=names,
        notes=notes,
    )
    return report


def _print_benchmark(report, args) -> None:
    from aeo.ranksim.benchmark import render_report

    print(render_report(report), end="")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.to_json(), encoding="utf-8")
        print(f"Wrote {out}")


def _benchmark_freeze(database: str, args) -> None:
    _flag_old_drafts(database)
    from datetime import date

    from aeo.ranksim.snapshot import SnapshotError, freeze

    try:
        date.fromisoformat(args.as_of)
    except ValueError:
        raise SystemExit(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}.") from None
    if not 0 < args.final_share < 1:
        raise SystemExit("--final-share must be between 0 and 1.")
    if args.graph and not Path(args.graph).is_file():
        raise SystemExit(f"No graph file at {args.graph}.")
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.intents import query_intents

    intent_map = query_intents(load_serp_file(args.serp_file))
    intents = intent_map.intents
    # Read the final-run registry before writing anything: a line that cannot be read stops here.
    _final_registry_rows()
    try:
        manifest = freeze(
            database,
            args.serp_file,
            args.out,
            as_of=args.as_of,
            final_share=args.final_share,
            seed=args.seed,
            cluster_threshold=args.cluster_threshold,
            intents=intents,
            intent_origin=intent_map.origin,
            graph_file=args.graph,
        )
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from None
    split = manifest["split"]
    print(
        f"Froze {manifest['labels_rows']} labels and {manifest['pages']} stored pages into {args.out}. "
        f"{split['clusters']} query clusters: {len(split['dev_queries'])} dev queries, "
        f"{len(split['final_queries'])} final queries (seed {split['seed']})."
    )
    for line in manifest.get("import_report") or []:
        print(line)
    from aeo.ranksim.snapshot import snapshot_final_key

    earlier = _scored_models(database, manifest, snapshot_final_key(args.out, manifest))
    if earlier:
        models = ", ".join(sorted(earlier))
        print(
            f"This is the same final split as an earlier snapshot. Already scored on it: {models}. "
            "Those configurations will not be scored on it again."
        )
    print("The snapshot holds third-party page HTML. Keep it out of git and do not publish it.")
    print(f"Develop with `benchmark --snapshot {args.out}` (dev split only). Score the final split once per model with --final.")


def _benchmark_snapshot(args, registry_database: str) -> None:
    import tempfile
    from datetime import date, datetime, timezone

    from aeo.ranksim.benchmark import git_commit, lightgbm_version
    from aeo.ranksim.groundtruth import ImportReport, load_serp_file
    from aeo.ranksim.lambdamart import model_id
    from aeo.ranksim.snapshot import (
        SnapshotError,
        final_registry_path,
        final_runs,
        graph_path,
        labels_path,
        load_manifest,
        materialize,
        record_final,
        register_final,
        snapshot_final_key,
    )
    from aeo.ranksim.store import close_database

    try:
        manifest = load_manifest(args.snapshot)
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from None
    split_key = snapshot_final_key(args.snapshot, manifest)
    # Runs recorded only in this snapshot's results.jsonl (E12 predates rs_final_runs) protect re-frozen copies too.
    # A line of results.jsonl that cannot be read stops every run here, dev runs included, with the file and line.
    try:
        _backfill_final_runs(registry_database, split_key, args.snapshot)
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from None
    if args.graph:
        raise SystemExit("--graph is not used with --snapshot. Freeze the graph with the pages (benchmark-freeze --graph).")
    split = manifest["split"]
    if args.cluster_threshold is not None and args.cluster_threshold != split["cluster_threshold"]:
        raise SystemExit(
            f"This snapshot's split used cluster threshold {split['cluster_threshold']}; pass that value or omit it."
        )
    args.cluster_threshold = split["cluster_threshold"]
    notes = [
        f"Frozen snapshot {args.snapshot}: created {manifest['created_at']}, split seed {split['seed']}, "
        f"{len(split['dev_queries'])} dev and {len(split['final_queries'])} final queries."
    ]
    if args.as_of and args.as_of != manifest["as_of"]:
        if args.final:
            raise SystemExit(f"A final run uses the snapshot's as_of ({manifest['as_of']}). Omit --as-of.")
        notes.append(f"as_of overridden to {args.as_of}; the snapshot was frozen with {manifest['as_of']}.")
    as_of = date.fromisoformat(args.as_of or manifest["as_of"])
    names = _model_feature_names(args)
    model = model_id(names, config=args.model)
    if args.final:
        if args.protocol:
            raise SystemExit("--protocol does not apply to --final, which runs final and final-url.")
        try:
            previous = [row for row in final_runs(args.snapshot) if row.get("model_id") == model]
        except SnapshotError as exc:
            raise SystemExit(str(exc)) from None
        registered = _scored_models(registry_database, manifest, split_key).get(model)
        if previous or registered:
            when = previous[0].get("created_at") if previous else registered
            raise SystemExit(
                f"Model {model} was already scored on this final split ({when}). See {Path(args.snapshot) / 'results.jsonl'}, "
                f"the rs_final_runs table or {final_registry_path()}. A final split is scored once per model configuration, "
                "also after re-freezing it."
            )
    import_report = ImportReport()
    labels = load_serp_file(labels_path(args.snapshot), report=import_report)
    report_lines = import_report.lines()
    if not args.final:
        # A duplicate example names a query, a URL and two positions. A dev run shows none from the final split.
        dev_queries = set(split["dev_queries"])
        hidden = sum(1 for row in import_report.duplicates if row[0] not in dev_queries)
        import_report.duplicates = [row for row in import_report.duplicates if row[0] in dev_queries]
        report_lines = import_report.lines()
        if hidden:
            report_lines.append(f"  {hidden} duplicate query+URL rows in final-split queries are not shown.")
    print("\n".join(report_lines))
    notes.extend(line.strip() for line in report_lines[1:])
    intents = dict(manifest.get("intents") or {})
    with tempfile.TemporaryDirectory(prefix="ranksim-snapshot-") as tmp:
        database = f"sqlite:///{Path(tmp) / 'snapshot.db'}"
        try:
            materialize(args.snapshot, database)
            if args.final:
                pages, coverage, stored = _bench_corpus(database, labels, args.coverage_csv)
            else:
                dev = set(split["dev_queries"])
                dev_labels = [row for row in labels if row.query in dev]
                pages, coverage, stored = _bench_corpus(database, dev_labels, args.coverage_csv)
        except SnapshotError as exc:
            raise SystemExit(str(exc)) from None
        finally:
            close_database(database)
    graph = graph_path(args.snapshot, manifest)
    if graph is not None:
        _attach_graph_file(pages, graph, notes)
    if args.final:
        notes.append("Final split: trained on every dev query, scored the final queries once.")
        notes.append(
            "This is a same-snapshot holdout, not accuracy. The final queries come from the same frozen label file "
            "as the dev queries. Only a forward check against a later snapshot (the ledger) can measure accuracy."
        )
        report = _run_benchmark_report(
            args,
            labels=labels,
            pages=pages,
            coverage=coverage,
            stored=stored,
            intents=intents,
            as_of=as_of,
            labels_sha=manifest["labels_sha256"],
            notes=notes,
            final_queries=split["final_queries"],
        )
        if report is None:
            return
        entry = {
            "model_id": model,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "lightgbm_version": lightgbm_version(),
            "feature_names": names,
            "as_of": manifest["as_of"],
            "labels_sha256": manifest["labels_sha256"],
            "corpus_sha256": report.corpus_sha,
            "bootstrap": args.bootstrap,
            "seed": args.seed,
            "protocols": [_final_summary(result) for result in report.protocols],
            "notes": list(report.notes),
            "final_key": split_key,
        }
        try:
            _register_final_run(registry_database, entry, args.snapshot)
            record_final(args.snapshot, entry)
        except SnapshotError as exc:
            raise SystemExit(str(exc)) from None
        try:
            registry = register_final({**entry, "snapshot_dir": str(args.snapshot)})
            kept = f" Commit {registry} so a fresh checkout or database also refuses a second run."
        except OSError as exc:
            kept = f" Could not append to {final_registry_path()} ({exc}). Add the run there by hand."
        _print_benchmark(report, args)
        print(f"Recorded in {Path(args.snapshot) / 'results.jsonl'} as {model}.{kept}")
        return
    dev = set(split["dev_queries"])
    notes.append(
        "Dev split only. Final-split labels were not scored or shown. Only their URLs were read, to keep query "
        "clusters whole. Their pages stay in the corpus as unlabeled pages, because in-corpus links and PageRank "
        "use every stored page."
    )
    report = _run_benchmark_report(
        args,
        labels=[row for row in labels if row.query in dev],
        pages=pages,
        coverage=coverage,
        stored=stored,
        intents=intents,
        as_of=as_of,
        labels_sha=manifest["labels_sha256"],
        notes=notes,
        cluster_labels=labels,
    )
    if report is not None:
        _print_benchmark(report, args)


def _scored_models(database: str, manifest: dict, key: str) -> dict[str, str]:
    """Model configurations already scored on this final split, with when: the git-tracked registry
    (smartcrew/final_runs.jsonl) and the main database, under the current or the legacy key. Legacy
    rows are first copied under the current key of the snapshot they name (``_rekey_legacy_final_runs``)."""
    from aeo.ranksim.snapshot import legacy_final_key
    from aeo.ranksim.store import RsFinalRun

    found: dict[str, str] = {}
    for row in _final_registry_rows():
        if row["final_key"] == key:
            found.setdefault(row["model_id"], str(row.get("created_at") or "date not recorded"))
    _rekey_legacy_final_runs(database)
    session = open_session(database)
    try:
        for row in session.query(RsFinalRun).filter(RsFinalRun.final_key.in_([key, legacy_final_key(manifest)])):
            found.setdefault(row.model_id, row.created_at.isoformat(timespec="seconds"))
    finally:
        session.close()
    return found


def _rekey_legacy_final_runs(database: str) -> None:
    """Copy each rs_final_runs row stored under the 87511c0 key (label file bytes, cluster threshold,
    final queries) under the current ``final_key`` of the snapshot folder the row names. A copy of
    the same split re-frozen from LF labels or with another cluster threshold is then refused even
    if the old snapshot is never opened again. Adds rows only: the old row is kept as it was.

    A row is not copied when its folder no longer resolves (moved or deleted, or a relative path from
    another working directory) or its labels.csv no longer matches its manifest. Such a row still
    blocks a re-freeze of byte-identical labels with the same cluster threshold, through its old key."""
    import json

    from aeo.ranksim.benchmark import file_sha256
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.snapshot import LABELS, MANIFEST, final_key, legacy_final_key
    from aeo.ranksim.store import RsFinalRun

    session = open_session(database)
    try:
        rows = session.query(RsFinalRun).all()
        present = {(row.final_key, row.model_id) for row in rows}
        folders: dict[str, list] = {}
        for row in rows:
            if row.snapshot_dir:
                folders.setdefault(row.snapshot_dir, []).append(row)
        added = False
        for folder, folder_rows in folders.items():
            try:
                manifest = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
                old_key = legacy_final_key(manifest)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            legacy = [row for row in folder_rows if row.final_key == old_key]
            if not legacy or file_sha256(Path(folder) / LABELS) != manifest["labels_sha256"]:
                continue
            try:
                new_key = final_key(manifest, load_serp_file(Path(folder) / LABELS))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            for row in legacy:
                if (new_key, row.model_id) in present:
                    continue
                summary = dict(row.summary) if isinstance(row.summary, dict) else {}
                session.add(
                    RsFinalRun(
                        final_key=new_key, model_id=row.model_id, created_at=row.created_at, snapshot_dir=row.snapshot_dir,
                        summary={**summary, "rekeyed_from": old_key},
                    )
                )
                present.add((new_key, row.model_id))
                added = True
        if added:
            session.commit()
    finally:
        session.close()


def _final_registry_rows() -> list[dict]:
    """The git-tracked final-run registry. A line that cannot be read stops the command with its
    file and line number: failing closed, since a skipped line could allow a second final run."""
    from aeo.ranksim.snapshot import SnapshotError, registered_final_runs

    try:
        return registered_final_runs()
    except SnapshotError as exc:
        raise SystemExit(str(exc)) from None


def _backfill_final_runs(database: str, key: str, snapshot_dir) -> None:
    """Copy final runs recorded only in a snapshot's results.jsonl into rs_final_runs. Adds rows,
    never changes or removes one."""
    from datetime import datetime

    from aeo.ranksim.snapshot import final_runs
    from aeo.ranksim.store import RsFinalRun

    runs = [row for row in final_runs(snapshot_dir) if row.get("model_id")]
    if not runs:
        return
    session = open_session(database)
    try:
        known = {row.model_id for row in session.query(RsFinalRun).filter(RsFinalRun.final_key == key)}
        for row in runs:
            if row["model_id"] in known:
                continue
            extra = {}
            try:
                extra["created_at"] = datetime.fromisoformat(str(row.get("created_at")))
            except ValueError:
                pass
            session.add(RsFinalRun(final_key=key, model_id=row["model_id"], snapshot_dir=str(snapshot_dir), summary=row, **extra))
            known.add(row["model_id"])
        session.commit()
    finally:
        session.close()


def _register_final_run(database: str, entry: dict, snapshot_dir: str) -> None:
    from sqlalchemy.exc import IntegrityError

    from aeo.ranksim.snapshot import SnapshotError
    from aeo.ranksim.store import RsFinalRun

    session = open_session(database)
    try:
        session.add(RsFinalRun(final_key=entry["final_key"], model_id=entry["model_id"], snapshot_dir=str(snapshot_dir), summary=entry))
        session.commit()
    except IntegrityError:
        session.rollback()
        raise SnapshotError(f"Model {entry['model_id']} already has a final run on this split in the database.") from None
    finally:
        session.close()


def _final_summary(result) -> dict:
    from aeo.ranksim.benchmark import MIN_VERDICT_CLUSTERS

    band = result.random_band
    trustworthy = result.n_clusters >= MIN_VERDICT_CLUSTERS
    return {
        "protocol": result.protocol,
        "dropped_train_rows": result.dropped_train_rows,
        "n_clusters": result.n_clusters,
        "interval_trustworthy": trustworthy,
        "scorers": [
            {
                "name": row.name,
                "pairwise": row.pairwise.point,
                "low": row.pairwise.low if trustworthy else None,
                "high": row.pairwise.high if trustworthy else None,
                "ndcg": row.ndcg,
                "n_pairs": row.n_pairs,
                "n_queries": row.n_queries,
            }
            for row in result.scorers
        ],
        "random_band": None if band is None else {"mean": band.mean, "low": band.low, "high": band.high},
        "confidence": None if result.confidence is None else result.confidence.note,
    }


def _ledger(database: str, args) -> None:
    from datetime import date
    from collections import defaultdict

    from aeo.ranksim.groundtruth import ImportReport, load_serp_file
    from datetime import datetime, timezone

    from aeo.ranksim.benchmark import file_sha256
    from aeo.ranksim.ledger import LedgerError, RsPrediction, record_outcome, render_score, score_ledger, verify_ledger

    session = open_session(database)
    try:
        if args.ledger_command == "verify":
            ok, message = verify_ledger(session)
            print(message)
            if ok and args.expect_head is not None:
                from aeo.ranksim.ledger import head_is_anchored

                ok, anchored = head_is_anchored(session, args.expect_head)
                print(anchored)
            if not ok:
                raise SystemExit(1)
        elif args.ledger_command == "list":
            query = session.query(RsPrediction)
            if args.project is not None:
                query = query.filter(RsPrediction.project_slug == args.project.strip())
            rows = query.order_by(RsPrediction.id.desc()).limit(args.limit).all()
            if not rows:
                print("No predictions recorded yet.")
            for row in rows:
                payload = row.payload
                print(
                    f"#{row.id} {row.created_at} [{row.project_slug or 'no project'}] {row.query!r} {row.subject_url} PREDICTED "
                    f"{payload['predicted_rank']} of {payload['predicted_of']} supplied pages, {payload['confidence']}"
                )
        elif args.ledger_command == "observe":
            observed_on = date.fromisoformat(args.observed_on).isoformat()
            import_report = ImportReport()
            labels = load_serp_file(args.serp_file, report=import_report)
            if any(label.kind == "search_console_avg" for label in labels):
                raise SystemExit(
                    "Search Console averages are not an observed results page and only cover your own URLs. "
                    "Record a results snapshot (query,url,position) instead."
                )
            conflicts = _observe_conflicts(labels, import_report, observed_on, args.provider, Path(args.serp_file).name)
            if conflicts:
                raise SystemExit(conflicts)
            grouped: dict[str, dict[str, int]] = defaultdict(dict)
            for label in labels:
                grouped[label.query][label.url] = int(round(label.position))
            uploaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            source_sha = file_sha256(args.serp_file) or ""
            try:
                for query, positions in grouped.items():
                    record_outcome(
                        session,
                        query=query,
                        observed_on=observed_on,
                        provider=args.provider,
                        positions=positions,
                        depth=args.depth,
                        project_slug=args.project,
                        source_file=Path(args.serp_file).name,
                        source_sha256=source_sha,
                        uploaded_at=uploaded_at,
                    )
            except LedgerError as exc:
                raise SystemExit(str(exc)) from None
            print(
                f"Recorded observations for {len(grouped)} queries on {observed_on} from {args.provider} "
                f"for project {args.project.strip() or '(none)'}."
            )
        elif args.ledger_command == "score":
            print(render_score(score_ledger(session, project_slug=args.project)), end="")
    finally:
        session.close()


def _ai_eval(settings, database: str, args) -> None:
    from aeo.ranksim.bootstrap import seed_corpus_if_empty
    from aeo.ranksim.check import check_draft
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.judge import collect_judge_scores, relevance_experiment
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.llm import resolve_llm

    print(seed_corpus_if_empty(database))
    labels = load_serp_file(args.serp)
    intents = query_intents(labels).intents
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    examples, uncovered, _withheld = build_examples(labels, pages, intents)
    from aeo.ranksim.benchmark import count_pairs
    from aeo.ranksim.judge import MIN_JUDGE_PAIRS

    n_pairs = count_pairs(examples)
    print(
        f"Coverage: {len(examples)} of {len(labels)} labeled rows have usable stored HTML "
        f"({uncovered} uncovered), {len({row.query for row in examples})} queries, {n_pairs} labeled pairs."
    )
    llm = resolve_llm(settings, run_id="ai-eval")
    print(llm.cost_summary() if not llm.available else f"LLM {llm.name} model {llm.model}.")
    skip_reason = None
    if llm.available and n_pairs == 0:
        skip_reason = "LLM relevance was not measured: no labeled pairs have stored HTML."
        print("Judge not run: no labeled pairs have stored HTML, so nothing could be measured.")
        scores = None
    elif llm.available and n_pairs < MIN_JUDGE_PAIRS and not args.allow_small:
        skip_reason = f"LLM relevance was not measured: {n_pairs} labeled pairs is below {MIN_JUDGE_PAIRS}."
        print(
            f"Judge not run: {n_pairs} pairs is below {MIN_JUDGE_PAIRS}. On a sample this size random scores "
            "move pairwise agreement by several points either way, so the result could not support a decision. "
            f"It would have cost about {len(examples)} judge calls. Build a crawled corpus with index-serp and more "
            "labeled queries, or pass --allow-small to spend anyway."
        )
        scores = None
    else:
        scores = collect_judge_scores(llm, examples, pages)
    if llm.available and scores is None and skip_reason is None:
        skip_reason = "LLM relevance was not measured: the judge did not score every labeled page (see above)."
    experiment = relevance_experiment(examples, scores, skip_reason=skip_reason)
    print("LLM relevance experiment. PREDICTED, not a live rank.")
    print(experiment.note)
    html_path = Path(args.html)
    if not html_path.is_file():
        print(f"Fix loop skipped. HTML file not found: {html_path}")
    else:
        fit = loo_fit(examples, keep_models=True)
        model = fit.models.get(args.query)
        result = check_draft(
            query=args.query,
            html=html_path.read_text(encoding="utf-8", errors="replace"),
            url=args.url,
            labels=labels,
            pages=pages,
            model=model,
            calibration=fit.calibration,
            intent=intent_for(args.query, intents),
            llm=llm,
            run_ai=True,
        )
        print(f"Fix loop. Query: {args.query}")
        print(f"URL: {args.url}")
        print(f"HTML: {html_path}")
        if result.error:
            print(result.error)
        else:
            print(
                f"PREDICTED slot {result.predicted_rank} of {result.predicted_of} supplied pages "
                f"(not a Google position). Confidence: {result.confidence}."
            )
            print(result.scope)
            print(result.fix_note)
            rounds_dir = Path(args.rounds_dir) if args.rounds_dir else None
            for round_ in result.fix_rounds:
                print(f"Round {round_.round_index}: PREDICTED {round_.predicted_rank} of {round_.predicted_of}. {round_.note}")
                if rounds_dir and round_.html:
                    rounds_dir.mkdir(parents=True, exist_ok=True)
                    target = rounds_dir / f"{html_path.stem}.round{round_.round_index}.html"
                    target.write_text(round_.html, encoding="utf-8")
                    print(f"  saved {target}")
        print(f"Uncovered labeled rows: {uncovered}.")
    print(llm.cost_summary())


def _serve(settings, database: str, args) -> None:
    import uvicorn

    from aeo.ranksim.serve import LOOPBACK_HOSTS, DemoState, create_app

    allowed = set(LOOPBACK_HOSTS) | {name.lower() for name in args.allowed_host}
    if args.host not in LOOPBACK_HOSTS:
        if not args.allowed_host:
            raise SystemExit(
                f"Refusing to serve on {args.host} without --allowed-host. The demo has no authentication; "
                "name the host people will use, and put it behind your own access control."
            )
        print(f"Warning: serving on {args.host}. There is no authentication; anyone who can reach it can use it.")
    print("Fitting the leave-one-query-out model. This does not call an LLM or a SERP API.")
    state = DemoState(settings, args.serp, database)
    print(state.accuracy_line())
    print(state.llm_note())
    print(state.relevance_note)
    app = create_app(state, allowed_hosts=allowed)
    print(f"Demo: http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _keywords(refresh: bool) -> None:
    if refresh:
        response = httpx.get("https://www.smartcrewhq.com/sitemap.xml", timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        refresh_sitemap(response.text)
        print("Refreshed sitemap snapshot from https://www.smartcrewhq.com/sitemap.xml")
    path = write_keywords_csv()
    summary = gap_summary()
    print(f"Wrote {path}")
    print(f"{summary['queries']} queries, {summary['with_page']} with a sitemap page, {summary['gaps']} page gaps")


def _gaps() -> None:
    rows = build_keywords()
    summary = gap_summary(rows)
    print(f"{summary['queries']} queries")
    print(f"{summary['with_page']} map to a URL in the 2026-10-29 sitemap")
    print(f"{summary['gaps']} page gaps")
    for row in page_gaps(rows):
        print(f"GAP\t{row.query}\t{row.suggested_target_page}")


def _collect(settings, database: str, args) -> None:
    choice = "mock" if args.mock else args.provider
    provider = resolve_serp(settings, choice)
    keywords = load_keyword_file(args.keywords) if args.keywords else build_keywords()
    depth = args.depth or settings.ranksim_serp_depth
    max_pages = args.max_pages or settings.ranksim_max_pages
    if max_pages > 1000:
        print("Capping max pages at 1000.")
        max_pages = 1000
    fetcher, closer = _live_fetcher(settings)
    session = open_session(database)
    try:
        result = collect_corpus(session, keywords, provider, fetcher, depth=depth, max_pages=max_pages)
    finally:
        session.close()
        closer()
        close = getattr(provider, "close", None)
        if close:
            close()
    print(f"Provider: {result.provider}. Google: {'yes' if result.is_google else 'no'}. Synthetic: {'yes' if result.synthetic else 'no'}.")
    print(f"Queries fetched: {result.queries}. SERP hits: {result.serp_hits}. Pages stored: {result.pages_stored}.")
    for warning in result.warnings:
        print(f"Warning: {warning}")
    authority = authority_client(settings)
    if authority is None:
        print("Authority proxy skipped. It stays unused unless RANKSIM_AUTHORITY=dataforseo and DataForSEO credentials are set.")
        return
    session = open_session(database)
    try:
        scored = attach_authority(session, authority)
    finally:
        session.close()
        authority.close()
    print(f"Authority scores stored for {scored} pages. Source: DataForSEO backlink rank.")


def _calibrate(database: str) -> None:
    _flag_old_drafts(database)
    session = open_session(database)
    try:
        report, label, provider = calibrate(session, build_keywords())
    finally:
        session.close()
    print(f"Label: {label}")
    print(f"Provider: {provider}")
    print(f"Queries: {report.n_queries}. Pairs: {report.n_pairs}.")
    print(f"Mean Spearman (in-sample): {_num(report.spearman)}")
    print(f"Mean NDCG at 10 (in-sample): {_num(report.ndcg_at_10)}")
    print("In-sample: the weights were fit and scored on the same queries. Use `benchmark` for held-out numbers.")
    if label != "calibrated_on_google_serp":
        print("This is not a Google accuracy number.")


def _test(settings, database: str, args) -> None:
    html, url, label = _load_source(settings, args.source, args.url)
    replaces = args.replaces or url
    doc = page_from_html(url, html, is_ours=True)
    session = open_session(database)
    try:
        deltas = run_test(session, build_keywords(), doc, replaces=replaces, source_label=label)
    finally:
        session.close()
    improved = sum(1 for row in deltas if row.delta is not None and row.delta > 0)
    regressed = sum(1 for row in deltas if row.delta is not None and row.delta < 0)
    print(f"Tested {label}")
    print("Positions are predicted on our index. They are not live Google ranks.")
    print(f"Improved: {improved}. Regressed: {regressed}. Queries: {len(deltas)}.")
    print("query\tbefore\tafter\tchange\tcompetitor_gap")
    for row in deltas:
        gap = " | ".join(row.competitor_gaps)
        print(
            f"{row.query}\t{_cell(row.before_predicted_position)}\t{_cell(row.after_predicted_position)}\t{_cell(row.delta)}\t{gap}"
        )
        if row.feature_moves and row.delta not in (None, 0):
            print("  features: " + "; ".join(row.feature_moves))


def _load_bundle(manifest_path: str, pages) -> list:
    from aeo.ranksim.saved_html import load_saved_html
    from aeo.ranksim.whatif import overrides_from_html

    manifest = Path(manifest_path)
    if not manifest.is_file():
        raise SystemExit(f"Bundle manifest not found: {manifest}")
    html_by_url, missing = load_saved_html(manifest.parent)
    if missing:
        raise SystemExit("Bundle manifest rows with no file were not invented: " + ", ".join(missing))
    if not html_by_url:
        raise SystemExit(f"{manifest} has no url,file rows")
    return overrides_from_html(html_by_url, pages)


def _test_regression(database: str, args) -> None:
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.whatif import render_regression, score_many

    labels = load_serp_file(args.serp)
    intents = query_intents(labels).intents
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        reports = score_many(
            labels,
            pages,
            {
                "live": [],
                "v2": _load_bundle(args.regression[0], pages),
                "v3": _load_bundle(args.regression[1], pages),
            },
            intents,
            compute_accuracy=False,
            targets=bundle_targets(args.regression),
        )
    finally:
        session.close()
    text = render_regression(reports["live"], reports["v2"], reports["v3"])
    target = Path(args.out) if args.out else Path("smartcrew") / "pr8-v3-compare.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print(text, end="")
    print(f"Wrote {target}")


def _test_three_way(database: str, args) -> None:
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.saved_html import load_saved_html
    from aeo.ranksim.whatif import overrides_from_html, render_three_way, score_many

    labels = load_serp_file(args.serp)
    intents = query_intents(labels).intents
    bundles = {}
    for name, manifest_path in (("v1", args.three_way[0]), ("v2", args.three_way[1])):
        manifest = Path(manifest_path)
        if not manifest.is_file():
            raise SystemExit(f"Bundle manifest not found: {manifest}")
        html_by_url, missing = load_saved_html(manifest.parent)
        if missing:
            raise SystemExit("Bundle manifest rows with no file were not invented: " + ", ".join(missing))
        if not html_by_url:
            raise SystemExit(f"{manifest} has no url,file rows")
        bundles[name] = html_by_url
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        reports = score_many(
            labels,
            pages,
            {
                "live": [],
                "v1": overrides_from_html(bundles["v1"], pages),
                "v2": overrides_from_html(bundles["v2"], pages),
            },
            intents,
            targets=bundle_targets(args.three_way),
        )
    finally:
        session.close()
    text = render_three_way(reports["live"], reports["v1"], reports["v2"])
    target = Path(args.out) if args.out else Path("smartcrew") / "pr8-v2-compare.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print(text, end="")
    print(f"Wrote {target}")


def _test_bundle(database: str, args) -> None:
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.saved_html import load_saved_html
    from aeo.ranksim.store import save_test_run
    from aeo.ranksim.whatif import overrides_from_html, render_whatif, score_whatif

    manifest = Path(args.bundle)
    if not manifest.is_file():
        raise SystemExit(f"Bundle manifest not found: {manifest}")
    html_by_url, missing = load_saved_html(manifest.parent)
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Bundle manifest rows with no file were not invented: {joined}")
    if not html_by_url:
        raise SystemExit(f"{manifest} has no url,file rows")
    labels = load_serp_file(args.serp)
    intents = query_intents(labels).intents
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        statuses = _page_statuses(session)
        report = score_whatif(labels, pages, overrides_from_html(html_by_url, pages), intents, targets=bundle_targets([manifest]))
        text = render_whatif(report, statuses)
        save_test_run(
            session,
            source_label=f"bundle:{manifest}",
            weight_label="lambdamart_loo",
            synthetic_corpus=False,
            is_google=True,
            summary_json={
                "predicted": True,
                "kind": "bundle",
                "agreement_note": "Held-out agreement with the labeled snapshot (same snapshot), not accuracy on unseen results.",
                "n_pairs": report.n_pairs,
                "pairwise_accuracy": report.pairwise_accuracy,
                "ndcg_at_10": report.ndcg_at_10,
                "spearman": report.spearman,
                "top3_hit_rate": report.top3_hit_rate,
                "queries": [
                    {
                        "query": row.query,
                        "real_position": row.real_position,
                        "before_predicted": row.before_rank,
                        "after_predicted": row.after_rank,
                        "before_url": row.before_url,
                        "after_url": row.after_url,
                        "before_n": row.before_n,
                        "after_n": row.after_n,
                    }
                    for row in report.queries
                ],
            },
        )
    finally:
        session.close()
    target = Path(args.out) if args.out else Path("smartcrew") / "pr8-predicted.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print(text, end="")
    print(f"Wrote {target}")


def _page_statuses(session) -> dict[str, tuple[str, str | None]]:
    from aeo.crawl.urls import normalize_url
    from aeo.ranksim.store import RsPage

    statuses: dict[str, tuple[str, str | None]] = {}
    for row in session.query(RsPage).all():
        statuses[row.url] = (row.status, row.error)
        try:
            statuses[normalize_url(row.url)] = (row.status, row.error)
        except ValueError:
            continue
    return statuses


def _track(settings, database: str, args) -> None:
    choice = "mock" if args.mock else args.provider
    provider = resolve_serp(settings, choice)
    depth = args.depth or settings.ranksim_serp_depth
    session = open_session(database)
    try:
        keywords = load_keyword_file(args.keywords) if args.keywords else build_keywords()
        rows = track_ranks(session, keywords, provider, depth=depth)
    finally:
        session.close()
        close = getattr(provider, "close", None)
        if close:
            close()
    failed = [row for row in rows if row.status == "error"]
    ok = [row for row in rows if row.status != "error"]
    found = sum(1 for row in ok if row.our_position is not None)
    kind = "Google" if rows and rows[0].is_google and not rows[0].synthetic else provider.name
    print(f"Recorded {len(rows)} queries from {kind}: {len(ok)} collected, {len(failed)} failed.")
    if provider.synthetic:
        print("Synthetic mock tracker. Not a Google rank check.")
    print(f"SmartCrew was inside the fetched results for {found} of {len(ok)} collected queries.")
    print("A blank position on a collected query means the domain was outside this fetch, not a made-up rank.")
    for row in failed[:10]:
        print(f"Failed: {row.query!r}: {row.error}. Not recorded as a rank.")


def _train(database: str, args) -> None:
    report = _score_file(database, args.serp_file, fit=True, model_out=args.model_out, folds=args.folds)
    _print_accuracy(report)


def _evaluate(database: str, args) -> None:
    report = _score_file(database, args.serp_file, fit=False, model_out=None, folds=args.folds, coverage_csv=args.coverage_csv)
    _print_accuracy(report)


def _score_file(database: str, path: str, *, fit: bool, model_out: str | None, folds: str = "loo", coverage_csv: str | None = None):
    _flag_old_drafts(database)
    from aeo.ranksim.groundtruth import ImportReport, load_serp_file
    from aeo.ranksim.lambdamart import build_examples, cross_validate, fit_lambdamart, save_model

    import_report = ImportReport()
    labels = load_serp_file(path, report=import_report)
    print("\n".join(import_report.lines()))
    session = open_session(database)
    try:
        from aeo.ranksim.benchmark import coverage_flags, coverage_rows, render_coverage, write_coverage_csv
        from aeo.ranksim.corpus import load_corpus_docs

        pages = load_corpus_docs(session)
        rows = coverage_rows(labels, session)
    finally:
        session.close()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    print(render_coverage(counts))
    if coverage_flags(rows):
        print(coverage_flags(rows))
    if coverage_csv:
        write_coverage_csv(rows, coverage_csv)
        print(f"Wrote per-label coverage to {coverage_csv}")
    intent_map = query_intents(labels)
    if intent_map.note({row.query for row in labels}):
        print(intent_map.note({row.query for row in labels}))
    intents = intent_map.intents
    examples, uncovered, withheld = build_examples(labels, pages, intents)
    real = True
    report = cross_validate(examples, real_google=real, folds_mode=folds)
    report.uncovered_urls = uncovered
    report.navboost_withheld = withheld
    if withheld:
        from aeo.ranksim.groundtruth import navboost_withhold_reason

        report.note += f" The prior position was withheld: {navboost_withhold_reason(labels)}."
    if fit and examples and report.cross_validated and model_out:
        model = fit_lambdamart(examples)
        Path(model_out).parent.mkdir(parents=True, exist_ok=True)
        save_model(model, model_out)
        names = list(model.feature_name())
        gains = model.feature_importance(importance_type="gain")
        ranked = sorted(zip(names, gains), key=lambda item: item[1], reverse=True)
        top = ", ".join(f"{name} ({gain:.1f})" for name, gain in ranked[:8] if gain > 0)
        report.note += (
            f" Saved model to {model_out}. The saved fit uses all labeled queries, so quote the cross-validated numbers, not an in-sample score."
        )
        if top:
            report.note += f" In-sample gain, unstable at this sample size: {top}."
    return report


def _index_serp(settings, database: str, args) -> None:
    summary = index_serp_csv(args.serp_file, database, settings, delay_seconds=args.delay)
    print(
        f"Labels: {summary['labels']}. Queries: {summary['queries']}. "
        f"Attempted: {summary['attempted']}. Fetched: {summary['fetched']}. "
        f"Saved HTML: {summary['saved_html']}. Skipped already stored: {summary['skipped']}. "
        f"Blocked by robots: {summary['blocked']}. Errors: {summary['error']}."
    )
    if summary.get("search_console"):
        print(
            "This is a Search Console file. Its positions are averages for your own pages, not a results page, "
            "so they were not stored as results lists. Only its URLs were crawled."
        )
    if summary.get("search_console_marked"):
        print(
            f"{summary['search_console_marked']} results lists an older version stored from these averages "
            "are now marked as not Google results. They are kept. calibrate and test skip them, and export-serp "
            "writes them with google=no, which readers drop."
        )
    if summary.get("undated_behind_dated"):
        if summary.get("dated_rows"):
            print(
                f"{summary['undated_behind_dated']} queries in this file have no observed_on date and already have a "
                "dated results list, which stays their latest list. Give them an observed_on date if they were observed later."
            )
        else:
            print(
                f"This file has no observed_on dates. {summary['undated_behind_dated']} of its queries already have a dated "
                "results list, which stays their latest list. Add an observed_on column if this file was observed later."
            )
    if summary["saved_missing"]:
        print("Saved HTML manifest rows with no file were not invented:")
        for url in summary["saved_missing"]:
            print(f"missing file {url}")
    print("Blocked and failed URLs were not given invented page text.")
    for url in summary["blocked_urls"]:
        print(f"blocked {url}")
    for url, error in summary["error_urls"]:
        print(f"error {url} {error}")


def _print_accuracy(report) -> None:
    print(report.note)
    print(f"Labeled URLs: {report.n_labeled_urls}. Uncovered URLs: {report.uncovered_urls}. Queries: {report.n_queries}. Pairs: {report.n_pairs}.")
    if not report.cross_validated:
        print("No NDCG, Spearman, top-3 hit rate, or pairwise agreement is reported.")
        return
    print("Predicted metrics. Not live Google ranks.")
    print(f"Pairwise agreement: {_num(report.pairwise_accuracy)}")
    print(f"NDCG at 10: {_num(report.ndcg_at_10)}")
    print(f"Spearman: {_num(report.spearman)}")
    print(f"Top-3 hit rate: {_num(report.top3_hit_rate)}")
    if report.query_scores:
        print("query\tn\tpairwise\tndcg10\tspearman\ttop3\tsmartcrew_real\tsmartcrew_predicted")
        for row in report.query_scores:
            print(
                f"{row.query}\t{row.n_urls}\t{_num(row.pairwise_accuracy)}\t{_num(row.ndcg_at_10)}\t"
                f"{_num(row.spearman)}\t{_num(row.top3_hit)}\t{_cell(row.smartcrew_real_position)}\t"
                f"{_cell(row.smartcrew_predicted_rank)}"
            )
    print(
        "These figures describe the labeled file under query holdout. They are same-snapshot agreement, "
        "not accuracy on unseen results, and not a live Google rank."
    )


def _report(database: str, out: str | None) -> None:
    html = render_report(database)
    target = Path(out) if out else Path("smartcrew") / "report.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html)
    print(f"Wrote {target}")


def _load_source(settings, source: str, url: str | None) -> tuple[str, str, str]:
    path = Path(source)
    if path.exists() and path.is_file():
        if not url:
            raise SystemExit("A local HTML file needs --url, the canonical page you are testing.")
        return path.read_text(), url, f"file:{path}"
    if source.startswith("http://") or source.startswith("https://"):
        fetched = _fetch_one(settings, source)
        if fetched.status != "fetched":
            raise SystemExit(f"Fetch failed for {source}: {fetched.error or fetched.status}")
        canonical = url or source
        return fetched.html, canonical, source
    raise SystemExit(f"Source not found: {source}")


def _fetch_one(settings, url: str) -> FetchResult:
    import time

    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=0,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )
    try:
        started = time.perf_counter()
        try:
            result = crawler.fetch(url)
        except UnsafeURL as exc:
            return FetchResult(url=url, html="", status="blocked", error=str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        if result.blocked or result.error or not result.html:
            return FetchResult(url=url, html="", status="error", error=result.error or result.robots_status, fetch_ms=elapsed)
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed, final_url=result.final_url)
    finally:
        crawler.close()


def _live_fetcher(settings):
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=settings.crawl_delay_seconds,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )

    def fetch(url: str) -> FetchResult:
        import time

        started = time.perf_counter()
        try:
            result = crawler.fetch(url)
        except UnsafeURL as exc:
            return FetchResult(url=url, html="", status="blocked", error=str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        if result.blocked:
            return FetchResult(url=url, html="", status="blocked", error=result.error, fetch_ms=elapsed)
        if result.error or not result.html:
            return FetchResult(url=url, html="", status="error", error=result.error or "empty response", fetch_ms=elapsed)
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed, final_url=result.final_url)

    return fetch, crawler.close


def _num(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _cell(value) -> str:
    if value is None:
        return ""
    return str(value)


if __name__ == "__main__":
    main()
