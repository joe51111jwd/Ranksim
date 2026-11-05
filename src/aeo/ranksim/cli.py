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
from aeo.ranksim.keywords import build_keywords, gap_summary, page_gaps, refresh_sitemap, write_keywords_csv
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

    report = sub.add_parser("report", help="Write the HTML report")
    report.add_argument("--out", default=None)

    train = sub.add_parser("train", help="Train LambdaMART on a real Google position file")
    train.add_argument("serp_file", help="CSV with query, url, position")
    train.add_argument("--model-out", default="data/ranksim-lambdamart.txt")
    train.add_argument("--folds", choices=["loo", "kfold"], default="loo")

    evaluate = sub.add_parser("evaluate", help="Cross-validate LambdaMART on a real Google position file")
    evaluate.add_argument("serp_file", help="CSV with query, url, position")
    evaluate.add_argument("--folds", choices=["loo", "kfold"], default="loo")

    index_serp = sub.add_parser("index-serp", help="Crawl URLs from a Google position file plus the SmartCrew sitemap")
    index_serp.add_argument("serp_file")
    index_serp.add_argument("--delay", type=float, default=0.4)

    graph = sub.add_parser("graph", help="Crawl a polite link graph from the client site and labeled competitors")
    graph.add_argument("--serp", default="smartcrew/serp.csv")
    graph.add_argument("--out", default="smartcrew/linkgraph.json")
    graph.add_argument("--max-fetches", type=int, default=50)
    graph.add_argument("--internal-depth", type=int, default=2)
    graph.add_argument("--outbound-depth", type=int, default=1)

    graph_eval = sub.add_parser("graph-eval", help="Compare held-out pairwise accuracy with and without link-graph features")
    graph_eval.add_argument("--serp", default="smartcrew/serp.csv")
    graph_eval.add_argument("--graph", default="smartcrew/linkgraph.json")

    noise = sub.add_parser("noise", help="Compare two Google snapshots. One file is not enough.")
    noise.add_argument("snapshots", nargs="+")

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
    bench.add_argument("--cluster-threshold", type=float, default=0.3)
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

    sub.add_parser(
        "reextract",
        help="Re-run text extraction on stored HTML after an extractor change (no network)",
    )
    ledger = sub.add_parser("ledger", help="Prediction track record: verify, list, record observed results, score")
    ledger_sub = ledger.add_subparsers(dest="ledger_command", required=True)
    ledger_sub.add_parser("verify", help="Check that no recorded prediction was changed, removed, or reordered")
    ledger_list = ledger_sub.add_parser("list", help="Show recorded predictions")
    ledger_list.add_argument("--limit", type=int, default=20)
    observe = ledger_sub.add_parser("observe", help="Append observed Google positions (query,url,position CSV)")
    observe.add_argument("serp_file")
    observe.add_argument("--observed-on", required=True, help="Date the results were collected, YYYY-MM-DD")
    observe.add_argument("--provider", required=True, help="Where the results came from, e.g. serper, manual_browser")
    observe.add_argument(
        "--depth",
        type=int,
        default=None,
        help="How many organic results were collected per query. Without it, a page missing from the file is unresolved, not 'below'.",
    )
    ledger_sub.add_parser("score", help="Score predictions against the first later observation")

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
            _noise(args)
        elif args.command == "serve":
            _serve(settings, database, args)
        elif args.command == "benchmark":
            _benchmark(database, args)
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
    intents = {row.query: row.intent for row in build_keywords()}
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
        print("Link features did not vary on the stored pages, so a change in pairwise accuracy is not evidence they helped.")
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


def _noise(args) -> None:
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.noise import noise_ceiling

    if len(args.snapshots) < 2:
        print("Noise ceiling was not computed. Pass two snapshot CSVs of the same queries from different days.")
        return
    report = noise_ceiling(load_serp_file(args.snapshots[0]), load_serp_file(args.snapshots[1]))
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


def _competitors(settings, database: str, args) -> None:
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.space import crawl_competitor_homepages, domains_from_labels, parse_competitor_text, write_competitors

    domains: list[str] = []
    source_bits = []
    if args.from_serps:
        domains.extend(domains_from_labels(load_serp_file(args.serp), "smartcrewhq.com"))
        source_bits.append("labeled_top10")
    if args.file:
        domains.extend(parse_competitor_text(Path(args.file).read_text(encoding="utf-8")))
        source_bits.append("file")
    if args.domains:
        domains.extend(parse_competitor_text("\n".join(args.domains)))
        source_bits.append("argv")
    ordered = list(dict.fromkeys(domains))
    if not ordered:
        raise SystemExit("No competitors. Pass domains, --file, or --from-serps. Nothing was invented.")
    write_competitors(args.out, ordered, "+".join(source_bits) or "import")
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
    from aeo.ranksim.space import build_space, domains_from_labels, read_competitors, render_space_text
    from pathlib import Path

    labels = load_serp_file(args.serp)
    competitors = read_competitors(args.competitors)
    source = args.competitors
    if not competitors:
        competitors = domains_from_labels(labels, "smartcrewhq.com")
        source = "labeled top 10 (no competitors file)"
    intents = {row.query: row.intent for row in build_keywords()}
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    graph_path = Path("smartcrew/linkgraph.json")
    if graph_path.is_file():
        attach_graph(pages, load_graph(str(graph_path)))
    examples, uncovered, _withheld = build_examples(labels, pages, intents)
    fit = loo_fit(examples, keep_models=True)
    report = build_space(labels, pages, fit.models, competitors)
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


def _benchmark(database: str, args) -> None:
    _flag_old_drafts(database)
    from datetime import date

    from aeo.ranksim.benchmark import (
        PROTOCOLS,
        build_report,
        corpus_fingerprint,
        file_sha256,
        labeled_coverage,
        render_report,
        run_benchmark,
    )
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.features import FEATURE_NAMES, MODEL_FEATURES
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.linkgraph import attach_graph, load_graph
    from aeo.ranksim.store import RsPage

    from aeo.ranksim.groundtruth import ImportReport

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    import_report = ImportReport()
    labels = load_serp_file(args.serp_file, report=import_report)
    print("\n".join(import_report.lines()))
    intents = {row.query: row.intent for row in build_keywords()}
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        coverage = labeled_coverage(labels, session)
        stored = [
            (url, html or "")
            for url, html in session.query(RsPage.url, RsPage.raw_html).filter(RsPage.status == "fetched").all()
        ]
    finally:
        session.close()
    notes = []
    if args.graph:
        graph_path = Path(args.graph)
        if not graph_path.is_file():
            raise SystemExit(f"No graph file at {graph_path}.")
        attach_graph(pages, load_graph(str(graph_path)))
        notes.append(f"Link graph attached from {graph_path} (sha256 {file_sha256(graph_path)[:12]}).")
    names = list(FEATURE_NAMES) if args.with_crawl_artifacts else list(MODEL_FEATURES)
    examples, uncovered, withheld = build_examples(labels, pages, intents, today=as_of)
    if withheld:
        from aeo.ranksim.groundtruth import navboost_withhold_reason

        notes.append(f"navboost_prior was withheld: {navboost_withhold_reason(labels)}.")
    if not args.as_of:
        notes.append(
            "as_of defaulted to today. Freshness depends on it, so rerunning on another day can change results."
        )
    protocols = tuple(args.protocol or PROTOCOLS)
    if len({row.query for row in examples}) < 2:
        print("Benchmark not computed. Need at least 2 queries with 2 stored pages each.")
        print("Coverage of labeled rows: " + ", ".join(f"{k} {v}" for k, v in sorted(coverage.items())))
        return
    try:
        clusters, results = run_benchmark(
            examples,
            protocols=protocols,
            cluster_threshold=args.cluster_threshold,
            bootstrap=args.bootstrap,
            seed=args.seed,
            feature_names=names,
            cluster_source=labels,
        )
    except ValueError as exc:
        print(f"Benchmark not computed. {exc}")
        return
    report = build_report(
        examples=examples,
        n_labels=len(labels),
        uncovered=uncovered,
        coverage=coverage,
        clusters=clusters,
        protocols=results,
        as_of=as_of,
        labels_sha=file_sha256(args.serp_file),
        corpus_sha=corpus_fingerprint(stored) if stored else None,
        corpus_pages=len(stored),
        cluster_threshold=args.cluster_threshold,
        bootstrap=args.bootstrap,
        seed=args.seed,
        feature_names=names,
        notes=notes + [line.strip() for line in import_report.lines()[1:]],
    )
    if len({row.query for row in examples}) < 2:
        print("Benchmark not computed. Need at least 2 queries with 2 stored pages each.")
        print("Coverage of labeled rows: " + ", ".join(f"{k} {v}" for k, v in sorted(coverage.items())))
        return
    print(render_report(report), end="")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.to_json(), encoding="utf-8")
        print(f"Wrote {out}")


def _ledger(database: str, args) -> None:
    from datetime import date
    from collections import defaultdict

    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.ledger import RsPrediction, record_outcome, render_score, score_ledger, verify_ledger

    session = open_session(database)
    try:
        if args.ledger_command == "verify":
            ok, message = verify_ledger(session)
            print(message)
            if not ok:
                raise SystemExit(1)
        elif args.ledger_command == "list":
            rows = session.query(RsPrediction).order_by(RsPrediction.id.desc()).limit(args.limit).all()
            if not rows:
                print("No predictions recorded yet.")
            for row in rows:
                payload = row.payload
                print(
                    f"#{row.id} {row.created_at} {row.query!r} {row.subject_url} PREDICTED "
                    f"{payload['predicted_rank']} of {payload['predicted_of']} supplied pages, {payload['confidence']}"
                )
        elif args.ledger_command == "observe":
            observed_on = date.fromisoformat(args.observed_on).isoformat()
            labels = load_serp_file(args.serp_file)
            if any(label.kind == "search_console_avg" for label in labels):
                raise SystemExit(
                    "Search Console averages are not an observed results page and only cover your own URLs. "
                    "Record a results snapshot (query,url,position) instead."
                )
            grouped: dict[str, dict[str, int]] = defaultdict(dict)
            for label in labels:
                grouped[label.query][label.url] = int(round(label.position))
            for query, positions in grouped.items():
                record_outcome(
                    session,
                    query=query,
                    observed_on=observed_on,
                    provider=args.provider,
                    positions=positions,
                    depth=args.depth,
                )
            print(f"Recorded observations for {len(grouped)} queries on {observed_on} from {args.provider}.")
        elif args.ledger_command == "score":
            print(render_score(score_ledger(session)), end="")
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
    intents = {row.query: row.intent for row in build_keywords()}
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
            "move pairwise accuracy by several points either way, so the result could not support a decision. "
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
            intent=intents.get(args.query, "commercial"),
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
    keywords = build_keywords()
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
    intents = {row.query: row.intent for row in build_keywords()}
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
    intents = {row.query: row.intent for row in build_keywords()}
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
    intents = {row.query: row.intent for row in build_keywords()}
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
        statuses = _page_statuses(session)
        report = score_whatif(labels, pages, overrides_from_html(html_by_url, pages), intents)
        text = render_whatif(report, statuses)
        save_test_run(
            session,
            source_label=f"bundle:{manifest}",
            weight_label="lambdamart_loo",
            synthetic_corpus=False,
            is_google=True,
            summary_json={
                "predicted": True,
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
        rows = track_ranks(session, build_keywords(), provider, depth=depth)
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
    report = _score_file(database, args.serp_file, fit=False, model_out=None, folds=args.folds)
    _print_accuracy(report)


def _score_file(database: str, path: str, *, fit: bool, model_out: str | None, folds: str = "loo"):
    _flag_old_drafts(database)
    from aeo.ranksim.groundtruth import ImportReport, load_serp_file
    from aeo.ranksim.lambdamart import build_examples, cross_validate, fit_lambdamart, save_model

    import_report = ImportReport()
    labels = load_serp_file(path, report=import_report)
    print("\n".join(import_report.lines()))
    session = open_session(database)
    try:
        from aeo.ranksim.corpus import load_corpus_docs

        pages = load_corpus_docs(session)
    finally:
        session.close()
    intents = {row.query: row.intent for row in build_keywords()}
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
        print("No NDCG, Spearman, top-3 hit rate, or pairwise accuracy is reported.")
        return
    print("Predicted metrics. Not live Google ranks.")
    print(f"Pairwise accuracy: {_num(report.pairwise_accuracy)}")
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
    print("These figures describe the labeled file under query holdout. They are not a live Google rank.")


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
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed)
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
        return FetchResult(url=url, html=result.html, status="fetched", fetch_ms=elapsed)

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
