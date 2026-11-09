"""CLI for the SmartCrew rank simulator.

Examples:
  python -m aeo.ranksim keywords
  python -m aeo.ranksim gaps
  python -m aeo.ranksim collect --mock
  python -m aeo.ranksim calibrate
  python -m aeo.ranksim test page.html --url https://www.smartcrewhq.com/dirtwork-software/
  python -m aeo.ranksim track
  python -m aeo.ranksim report
  python -m aeo.ranksim tester page.html --url https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/ --watch
  python -m aeo.ranksim optimize page.html --url https://www.smartcrewhq.com/all-in-one-heavy-civil-software/ --rewriter rules --goal 1
  python -m aeo.ranksim project create SmartCrew --site www.smartcrewhq.com --searches "dirt work software; hcss alternative"
  python -m aeo.ranksim project import-serp smartcrew smartcrew/serp.csv
  python -m aeo.ranksim project pull smartcrew --skip-google
  python -m aeo.ranksim project test smartcrew page.html --url https://www.smartcrewhq.com/all-in-one-dirt-work-software/
  python -m aeo.ranksim project engines smartcrew
  python -m aeo.ranksim serve            (the web UI on http://127.0.0.1:8765/)
"""

from __future__ import annotations

import argparse
import os
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

    forward_eval = sub.add_parser(
        "forward-eval",
        help="Freeze on the 2026-11-06 snapshot and score the 2026-11-09 order once. Does not train on the later day.",
    )
    forward_eval.add_argument("--baseline", required=True, help="2026-11-06 snapshot directory. HTML stays out of git.")
    forward_eval.add_argument("--forward", required=True, help="2026-11-09 snapshot directory.")
    forward_eval.add_argument("--out", required=True, help="JSON report path.")
    forward_eval.add_argument("--ledger-json", default=None, help="Export the prediction chain and the later outcomes.")
    forward_eval.add_argument("--ledger-db", default=None, help="SQLite file for the ledger. Default is in memory.")

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

    tester_cmd = sub.add_parser(
        "tester",
        help="Sandbox tester. Score a page version in seconds on the stored competitor set. PREDICTED, not live Google.",
    )
    tester_cmd.add_argument("source", help="A local HTML file (needs --url) or an http(s) URL: preview, staging, or live")
    tester_cmd.add_argument("--url", default=None, help="Canonical URL of the page. Required for a local file.")
    tester_cmd.add_argument(
        "--queries",
        default=None,
        help='Comma-separated target queries, e.g. "q1,q2". Default picks up to --top labeled queries for this page.',
    )
    tester_cmd.add_argument("--top", type=int, default=5, help="How many target queries to pick when --queries is not given")
    tester_cmd.add_argument("--serp", default="smartcrew/serp.csv", help="Labeled Google file")
    tester_cmd.add_argument("--watch", action="store_true", help="Poll the source and print a new board when the HTML changes")
    tester_cmd.add_argument("--interval", type=float, default=15.0, help="Seconds between polls with --watch")
    tester_cmd.add_argument("--max-checks", type=int, default=0, help="Stop --watch after this many polls. 0 polls until Ctrl+C.")
    tester_cmd.add_argument("--history", default=None, help="Version history JSONL. Default data/tester/<url hash>.jsonl")
    tester_cmd.add_argument("--json", action="store_true", help="Print the board as JSON")
    tester_cmd.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 when a target query is PREDICTED worse than live, or than v1 when no live copy is stored",
    )

    optimize_cmd = sub.add_parser(
        "optimize",
        help="Rapid iteration. Rewrite and rescore until PREDICTED top N on every target query. Sandbox only.",
    )
    optimize_cmd.add_argument("source", help="A local HTML file (needs --url) or an http(s) URL")
    optimize_cmd.add_argument("--url", default=None, help="Canonical URL of the page. Required for a local file.")
    optimize_cmd.add_argument("--queries", default=None, help='Comma-separated target queries, e.g. "q1,q2"')
    optimize_cmd.add_argument("--top", type=int, default=5, help="How many target queries to pick when --queries is not given")
    optimize_cmd.add_argument("--goal", type=int, default=3, help="Stop when every target query is PREDICTED at this rank or better")
    optimize_cmd.add_argument("--max-rounds", type=int, default=8)
    optimize_cmd.add_argument("--patience", type=int, default=3, help="Stop after this many rejected rounds in a row")
    optimize_cmd.add_argument("--budget-usd", type=float, default=None, help="Stop the LLM rewriter once its estimated cost reaches this")
    optimize_cmd.add_argument(
        "--rewriter",
        choices=["auto", "llm", "rules"],
        default="auto",
        help="auto and llm need an LLM key. rules runs offline and is not an LLM.",
    )
    optimize_cmd.add_argument("--out-dir", default=None, help="Default data/optimize/<url slug>")
    optimize_cmd.add_argument("--serp", default="smartcrew/serp.csv", help="Labeled Google file")

    _add_project_parser(sub)

    serve = sub.add_parser("serve", help="Serve the web UI: projects, Pull now, Test a page. The old demo is at /demo.")
    serve.add_argument("--host", default="127.0.0.1", help="Default 127.0.0.1 (this computer only). 0.0.0.0 exposes it with no login.")
    serve.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="With a wider --host: a host name browsers may use besides IP addresses and localhost (repeatable)",
    )
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--serp", default="smartcrew/serp.csv", help="Labeled Google file for the old demo at /demo")
    serve.add_argument("--projects-dir", default=None, help="Default data/projects, or RANKSIM_PROJECTS_DIR")

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
        elif args.command == "forward-eval":
            from aeo.ranksim.forward_eval import main as forward_main

            forward_args = ["--baseline", args.baseline, "--forward", args.forward, "--out", args.out]
            if args.ledger_json:
                forward_args.extend(["--ledger-json", args.ledger_json])
            if args.ledger_db:
                forward_args.extend(["--ledger-db", args.ledger_db])
            raise SystemExit(forward_main(forward_args))
        elif args.command == "serve":
            _serve(settings, database, args)
        elif args.command == "ai-eval":
            _ai_eval(settings, database, args)
        elif args.command == "competitors":
            _competitors(settings, database, args)
        elif args.command == "space":
            _space(database, args)
        elif args.command == "tester":
            code = _tester(settings, database, args)
            if code:
                raise SystemExit(code)
        elif args.command == "optimize":
            code = _optimize(settings, database, args)
            if code:
                raise SystemExit(code)
        elif args.command == "project":
            from aeo.ranksim.project import ProjectError

            try:
                code = _project(settings, database, args)
            except ProjectError as exc:
                print(str(exc), file=sys.stderr)
                raise SystemExit(2) from exc
            if code:
                raise SystemExit(code)
        else:
            parser.error(f"Unknown command {args.command}")
    except ProviderConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


def _add_project_parser(sub) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--projects-dir", default=None, help="Default data/projects, or RANKSIM_PROJECTS_DIR")
    project = sub.add_parser(
        "project",
        help="Projects: your key searches, the REAL Google top 10 for each, and a mini search engine to test pages on",
    )
    psub = project.add_subparsers(dest="project_command", required=True)

    create = psub.add_parser("create", parents=[common], help="Create a project: a site and its key searches")
    create.add_argument("name")
    create.add_argument("--site", required=True, help="Your site, e.g. www.example.com")
    create.add_argument("--searches", default=None, help="Key searches separated by ; e.g. 'dirt work software; hcss alternative'")
    create.add_argument("--searches-file", default=None, help="Text file, one search per line")
    create.add_argument("--slug", default=None)
    create.add_argument("--sitemap-file", default=None, help="Local list of your site's URLs, one per line. Default reads /sitemap.xml at pull time.")
    create.add_argument("--link-graph", default=None, help="Optional link graph JSON (python -m aeo.ranksim graph)")
    create.add_argument("--max-site-pages", type=int, default=60)

    searches = psub.add_parser("searches", parents=[common], help="Add or remove key searches")
    searches.add_argument("name")
    searches.add_argument("--add", default=None, help="Searches separated by ;")
    searches.add_argument("--remove", default=None, help="Searches separated by ;")

    imp = psub.add_parser("import-serp", parents=[common], help="Seed REAL Google results from a query,url,position CSV")
    imp.add_argument("name")
    imp.add_argument("file")
    imp.add_argument("--snapshot-date", default=None, help="YYYY-MM-DD the positions were seen. Default: pull date not recorded.")
    imp.add_argument("--source", default=None, help="Source label. Default names the file and says when it was pulled.")
    imp.add_argument("--add-searches", action="store_true", help="Add searches in the file that the project does not have")

    pull_cmd = psub.add_parser("pull", parents=[common], help="Pull REAL Google results, crawl the pages, train the mini engine")
    pull_cmd.add_argument("name")
    pull_cmd.add_argument("--skip-google", action="store_true", help="Reuse the REAL results already saved")
    pull_cmd.add_argument("--depth", type=int, default=10)
    pull_cmd.add_argument("--fresh-days", type=float, default=7.0, help="Refetch stored pages older than this")
    pull_cmd.add_argument("--workers", type=int, default=8)
    pull_cmd.add_argument("--delay", type=float, default=0.4, help="Seconds between requests to one host")
    pull_cmd.add_argument(
        "--engines",
        default="chatgpt,claude,perplexity,gemini",
        help="AI engines to ask for REAL citations, comma separated, or none. An engine without its key is recorded as not run.",
    )
    pull_cmd.add_argument("--runs", type=int, default=1, help="Times to ask each engine each search (1 to 20). Answers vary run to run.")
    pull_cmd.add_argument("--brand", default=None, help="Brand name to look for in AI answers. Default: the project name.")
    pull_cmd.add_argument("--fresh-engines", action="store_true", help="Do not reuse AI answers cached earlier the same UTC day")
    pull_cmd.add_argument("--json", action="store_true")

    show = psub.add_parser("show", parents=[common], help="Show a project, its REAL results, and its pulls")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")

    test = psub.add_parser("test", parents=[common], help="Test a page on every key search. PREDICTED slot among the REAL top 10.")
    test.add_argument("name")
    test.add_argument("source", help="A local HTML file (needs --url) or an http(s) URL")
    test.add_argument("--url", default=None, help="The URL the page lives or will live at")
    test.add_argument("--json", action="store_true")
    test.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 when the page is no longer on topic for a search where the stored copy (or the previous version) was",
    )
    test.add_argument(
        "--fail-on-content-regression",
        action="store_true",
        help="Also exit 1 when the content model (unvalidated) PREDICTS a search worse than the stored copy or previous version",
    )
    test.add_argument("--no-save", action="store_true", help="Do not record this version")
    test.add_argument("--details", type=int, default=3, help="Pages above you to explain per search in the text output (-1 for all)")
    test.add_argument("--sim-runs", type=int, default=3, help="PREDICTED AI answer simulation runs per search (needs an LLM key)")
    test.add_argument("--no-sim", action="store_true", help="Skip the PREDICTED AI answer simulation (no LLM calls)")

    engines = psub.add_parser("engines", parents=[common], help="Show the latest REAL AI engine citations per search")
    engines.add_argument("name")
    engines.add_argument("--json", action="store_true")

    lst = psub.add_parser("list", parents=[common], help="List projects")
    lst.add_argument("--json", action="store_true")


def _project(settings, database: str, args) -> int:
    """Project commands. REAL is Google data with its source and date. PREDICTED is the model."""
    import json
    import time
    from dataclasses import asdict

    from aeo.ranksim import project as proj

    root = args.projects_dir
    command = args.project_command
    if command == "create":
        searches = proj.split_searches(args.searches)
        if args.searches_file:
            searches += proj.split_searches(Path(args.searches_file).read_text(encoding="utf-8"))
        made = proj.create_project(
            args.name,
            args.site,
            searches,
            root=root,
            slug=args.slug,
            sitemap_file=args.sitemap_file,
            link_graph=args.link_graph,
            max_site_pages=args.max_site_pages,
        )
        print(f"Created project {made.name} (slug {made.slug}) for {made.site} with {len(made.searches)} key searches in {made.dir}.")
        print(f"Next: python -m aeo.ranksim project pull {made.slug} (needs {proj.GOOGLE_KEYS}),")
        print(f"or import REAL results you already have: python -m aeo.ranksim project import-serp {made.slug} FILE.csv")
        return 0
    if command == "list":
        rows = proj.list_projects(root=root)
        if args.json:
            print(json.dumps(proj.clean([asdict(row) for row in rows]), indent=2))
        else:
            print(proj.render_list_text(rows), end="")
        return 0
    project = proj.load_project(args.name, root=root)
    if command == "searches":
        updated = proj.update_searches(project, add=proj.split_searches(args.add), remove=proj.split_searches(args.remove))
        print(f"{updated.name} has {len(updated.searches)} key searches:")
        for search in updated.searches:
            print(f"  {search} ({updated.intents.get(search, '')})")
        print("New searches get REAL results on the next project pull.")
        return 0
    if command == "import-serp":
        report = proj.import_serp_csv(project, args.file, args.snapshot_date, args.source, add_searches=args.add_searches)
        print(f"Imported {report.rows} REAL Google results for {len(report.searches_imported)} searches into pull {report.pull_id}.")
        print(f"Source: {report.source}.")
        if report.searches_added:
            print("Added searches from the file: " + ", ".join(report.searches_added) + ".")
        for search in report.searches_missing:
            print(f"Not in the file: {search}. {proj.NOT_RUN_GOOGLE}.")
        if report.searches_ignored:
            print("In the file but not in the project, ignored: " + ", ".join(report.searches_ignored) + ". Pass --add-searches to add them.")
        print(f"Next: python -m aeo.ranksim project pull {project.slug} --skip-google to crawl the pages and train the mini engine.")
        return 0
    if command == "pull":
        last = {"step": ""}

        def progress(event) -> None:
            if args.json:
                return
            if event.step != last["step"] or event.done == event.total or (event.step == "crawl" and event.done % 25 == 0):
                count = f" [{event.done}/{event.total}]" if event.total else ""
                print(f"{event.seconds:6.1f} s  {event.step}{count}: {event.message}", flush=True)
                last["step"] = event.step

        report = proj.pull(
            project,
            settings,
            depth=args.depth,
            progress=progress,
            skip_google=args.skip_google,
            database=database,
            fresh_days=args.fresh_days,
            workers=args.workers,
            delay_seconds=args.delay,
            engines=args.engines,
            engine_runs=args.runs,
            brand=args.brand,
            engine_use_cache=not args.fresh_engines,
        )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(proj.render_pull_text(report), end="")
        return 0
    if command == "engines":
        from aeo.ranksim.project_engines import latest_engines, latest_engines_dict, render_engines_text

        latest = latest_engines(project, settings=settings)
        if args.json:
            print(json.dumps(latest_engines_dict(latest), indent=2))
        else:
            print(render_engines_text(project, latest), end="")
        return 0
    if command == "show":
        status = proj.project_status(project)
        if args.json:
            print(json.dumps(status.to_dict(), indent=2))
        else:
            print(proj.render_status_text(status), end="")
        return 0
    if command == "test":
        html, url, label = _load_source(settings, args.source, args.url)
        started = time.perf_counter()
        ctx = proj.load_project_context(project, database=database)
        llm = None
        if not args.no_sim:
            from aeo.ranksim.llm import resolve_llm

            llm = resolve_llm(settings)
        board = proj.test_page(
            ctx,
            html,
            url,
            source=label,
            save=not args.no_save,
            llm=llm,
            sim_runs=args.sim_runs,
            settings=settings,
            sim_off="not run: switched off with --no-sim" if args.no_sim else "",
        )
        board.timing["wall_seconds"] = round(time.perf_counter() - started, 2)
        if args.json:
            print(json.dumps(board.to_dict(), indent=2))
        else:
            print(proj.render_board_text(board, detail_limit=args.details), end="")
        if args.fail_on_regression and board.topic_regressions:
            print(f"Failing: {len(board.topic_regressions)} searches are no longer on topic against {board.baseline}.", file=sys.stderr)
            return 1
        if args.fail_on_content_regression and board.regressions:
            print(
                f"Failing: {len(board.regressions)} searches are PREDICTED worse than {board.baseline} by the content model (unvalidated).",
                file=sys.stderr,
            )
            return 1
        return 0
    raise SystemExit(f"Unknown project command {command}")


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
    session = open_session(database)
    try:
        stored = 0
        for url, node in graph.nodes.items():
            if node.status != "fetched" or not node.html:
                continue
            store_fetched_page(session, FetchResult(url=url, html=node.html, status="fetched"))
            stored += 1
    finally:
        session.close()
    print(f"Link graph: {len(graph.nodes)} pages, {len(graph.edges)} edges, {graph.fetches} fetches, {graph.blocked} robots blocks.")
    print(f"Stored {stored} fetched pages into the rank database.")
    print(f"Wrote {args.out}")
    for note in graph.notes:
        print(note)
    print("In-crawl inlinks are not web-wide backlinks. No backlink API was called.")


def _graph_eval(database: str, args) -> None:
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.features import FEATURE_NAMES
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
    base_names = [name for name in FEATURE_NAMES if name not in {"orphan", "link_depth", "crawl_inlinks"}]
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
    varied = _graph_feature_varies(pages)
    after_examples, _uncovered, _withheld = build_examples(labels, pages, intents)
    after = loo_fit(after_examples, FEATURE_NAMES)
    print(f"With link-graph features: pairwise {_fmt(after.pairwise)} on {after.n_pairs} pairs. Pages updated from the graph: {attached}.")
    if not varied:
        print("Link features did not vary on the stored pages, so a change in pairwise accuracy is not evidence they helped.")
    elif before.pairwise is not None and after.pairwise is not None:
        print(f"Pairwise delta: {after.pairwise - before.pairwise:+.3f}.")
    print("Web-wide backlinks were not included. No DataForSEO key was used.")


def _graph_feature_varies(pages: dict) -> bool:
    seen = set()
    depths = set()
    for url, doc in pages.items():
        if url != getattr(doc, "url", url):
            continue
        if doc.orphan is not None:
            seen.add(doc.orphan)
        if doc.link_depth is not None:
            depths.add(doc.link_depth)
    return len(seen) > 1 or len(depths) > 1


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
    print(f"Queries compared: {report.n_queries}.")
    print(f"Pairwise agreement: {report.pairwise_agreement:.3f}.")
    print(f"Top-1 same rate: {report.top1_same_rate:.3f}.")
    print(f"Mean absolute position change: {report.mean_abs_position_change:.3f}.")


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


def _ai_eval(settings, database: str, args) -> None:
    from aeo.ranksim.bootstrap import seed_corpus_if_empty
    from aeo.ranksim.check import check_draft
    from aeo.ranksim.confidence import loo_fit
    from aeo.ranksim.corpus import load_corpus_docs
    from aeo.ranksim.judge import collect_judge_scores, relevance_experiment
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.lambdamart import build_examples
    from aeo.ranksim.llm import resolve_llm

    print(seed_corpus_if_empty(database))
    from aeo.ranksim.tester import _labels_for_scoring

    labels, label_synthetic, label_note = _labels_for_scoring(args.serp)
    if label_note:
        print(label_note)
    intents = {row.query: row.intent for row in build_keywords()}
    session = open_session(database)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()
    examples, uncovered, _withheld = build_examples(labels, pages, intents)
    llm = resolve_llm(settings, run_id="ai-eval")
    print(llm.cost_summary() if not llm.available else f"LLM {llm.name} model {llm.model}.")
    scores = collect_judge_scores(llm, examples, pages)
    experiment = relevance_experiment(examples, scores)
    print("LLM relevance, leave-one-query-out. PREDICTED, not a live rank.")
    print(experiment.note)
    if experiment.measured:
        print(f"Baseline pairwise: {experiment.baseline_pairwise:.3f}")
        print(f"With llm_relevance: {experiment.with_judge_pairwise:.3f}")
        if experiment.baseline_pairwise is not None and experiment.with_judge_pairwise is not None:
            print(f"Delta: {experiment.with_judge_pairwise - experiment.baseline_pairwise:+.3f}")
    else:
        print("No pairwise delta. The judge did not score every labeled page.")
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
            synthetic=label_synthetic,
        )
        print(f"Fix loop. Query: {args.query}")
        print(f"URL: {args.url}")
        print(f"HTML: {html_path}")
        if result.error:
            print(result.error)
        else:
            print(f"PREDICTED rank {result.predicted_rank} of {result.predicted_of}. Confidence: {result.confidence}.")
            print(result.fix_note)
            for round_ in result.fix_rounds:
                print(f"Round {round_.round_index}: PREDICTED {round_.predicted_rank} of {round_.predicted_of}. {round_.note}")
        print(f"Uncovered labeled rows: {uncovered}.")
    print(llm.cost_summary())


def _tester(settings, database: str, args) -> int:
    """Score a page version in the sandbox, record it, and print the board."""
    import json
    import time

    from aeo.ranksim import tester as sandbox

    html, url, label = _load_source(settings, args.source, args.url)
    kind = "live fetch" if _same_page(args.source, url) else "draft"
    started = time.perf_counter()
    ctx = sandbox.load_sim_context(database, args.serp)
    load_s = time.perf_counter() - started
    for note in ctx.notes:
        print(note, file=sys.stderr if args.json else sys.stdout)
    history = sandbox.TesterHistory(args.history or sandbox.TesterHistory.default_path(url))
    queries = _split_queries(args.queries)
    pinned: list[str] = []
    if not queries:
        # Keep the queries v1 and the stored copy were scored on, so an edit
        # that drops query words from the title cannot drop them from the check.
        first_saved = history.first()
        if first_saved is not None:
            pinned.extend(row.query for row in first_saved.scores)
        stored = sandbox.stored_html(ctx.database, url)
        if stored is not None:
            pinned.extend(sandbox.pick_targets(ctx, url, stored[0], top=args.top)[0])
    targets, skipped, kept = sandbox.stable_targets(ctx, url, html, queries=queries, top=args.top, pinned=pinned)
    targets_note = _targets_note(sandbox, ctx, url, html, args.top, kept)
    skipped = sandbox.skip_notes(ctx, skipped)
    live = sandbox.live_score(ctx, url, targets, created=_now())
    state: dict = {"regressions": [], "baseline": ""}

    def run(page_html: str) -> str:
        began = time.perf_counter()
        current = sandbox.score_page(ctx, url, page_html, targets, label=kind, source=label, created=_now())
        score_ms = (time.perf_counter() - began) * 1000
        saved = history.append(current)
        previous = history.previous()
        first = history.first()
        baseline, name = sandbox.baseline_for(current, live, first)
        state["regressions"] = sandbox.regressions(current, baseline, name)
        state["baseline"] = name
        extra = {"skipped": skipped, "first": first, "serp_file": ctx.serp_file, "targets_note": targets_note}
        if args.json:
            data = sandbox.board_dict(current, previous, live, ctx.accuracy_line(), **extra)
            data["history"] = str(history.path)
            data["new_version_saved"] = saved
            data["timing"] = {"model_load_seconds": round(load_s, 2), "scoring_ms": round(score_ms, 1)}
            if args.watch:
                return json.dumps(data) + "\n"  # one JSON document per line, so the stream is JSONL
            return json.dumps(data, indent=2) + "\n"
        text = sandbox.render_board(current, previous, live, ctx.accuracy_line(), **extra)
        if saved:
            text += f"Saved as {current.version} in {history.path}."
        else:
            text += f"Same HTML as {current.version}, so no new version was saved in {history.path}."
        return text + f" Model load {load_s:.1f} s. Scoring {score_ms:.0f} ms.\n"

    if not args.watch:
        print(run(html), end="")
    else:
        pending = [html]

        def load() -> str:
            if pending:
                return pending.pop()
            try:
                fresh, _url, _label = _load_source(settings, args.source, args.url)
            except SystemExit as exc:
                raise RuntimeError(str(exc)) from None
            return fresh

        print(
            f"Watching {args.source} every {args.interval:g} s. A new board prints only when the HTML changes. Ctrl+C stops.",
            file=sys.stderr if args.json else sys.stdout,
        )
        try:
            sandbox.watch(load, run, args.interval, args.max_checks, on_change=lambda text: print(str(text).rstrip("\n"), flush=True))
        except KeyboardInterrupt:
            print("Stopped.", file=sys.stderr if args.json else sys.stdout)
    if args.fail_on_regression and state["regressions"]:
        count = sandbox.count_of(len(state["regressions"]), "target query is", "target queries are")
        print(f"Failing: {count} PREDICTED worse than {state['baseline']}.", file=sys.stderr)
        return 1
    return 0


def _same_page(source: str, url: str) -> bool:
    """True when the source is the page's own URL, so the HTML is a live fetch, not a draft."""
    from aeo.crawl.urls import normalize_url

    if not source.startswith(("http://", "https://")):
        return False
    try:
        return normalize_url(source) == normalize_url(url)
    except ValueError:
        return source == url


def _targets_note(sandbox, ctx, url: str, html: str, top: int, kept: list[str]) -> str:
    if not kept:
        return ""
    picked, _skipped = sandbox.pick_targets(ctx, url, html, top=top)
    held = [query for query in kept if query not in picked]
    if not held:
        return ""
    return (
        f"Kept as targets from v1 or the stored copy, though this HTML alone would not pick them: {', '.join(held)}. "
        "An edit cannot drop a query from the check."
    )


def _optimize(settings, database: str, args) -> int:
    """Rewrite and rescore in the sandbox until PREDICTED top N on every target query."""
    import time

    from aeo.ranksim import tester as sandbox
    from aeo.ranksim.llm import resolve_llm
    from aeo.ranksim.optimize import Guards, LlmRewriter, RulesRewriter, optimize, render_optimize, slug_for

    llm = None
    if args.rewriter in ("auto", "llm"):
        llm = resolve_llm(settings, run_id="optimize")
        if not llm.available:
            print(
                "No LLM key is set, so the LLM rewriter cannot run. Set OPENROUTER_API_KEY, OPENAI_API_KEY, "
                "or ANTHROPIC_API_KEY. Or pass --rewriter rules to run offline with the rules rewriter, which is not an LLM.",
                file=sys.stderr,
            )
            return 2
    html, url, label = _load_source(settings, args.source, args.url)
    started = time.perf_counter()
    ctx = sandbox.load_sim_context(database, args.serp)
    load_s = time.perf_counter() - started
    for note in ctx.notes:
        print(note)
    targets, skipped = sandbox.pick_targets(ctx, url, html, _split_queries(args.queries), top=args.top)
    notes = sandbox.skip_notes(ctx, skipped)
    for query, note in notes.items():
        print(f"Skipped {query}: {note}.")
    if not targets:
        if notes and all(note == sandbox.SKIP_NOTE_UNSTORED for note in notes.values()):
            print(
                f"Every query asked for is labeled but has too few stored pages, so nothing was optimized. {sandbox.UNSTORED_HINT}",
                file=sys.stderr,
            )
        else:
            print(
                "No query from the labeled Google file matches this page, so nothing was optimized. "
                "Pass --queries with queries from that file.",
                file=sys.stderr,
            )
        return 2
    guards = Guards()
    rewriter = (
        LlmRewriter(llm)
        if llm is not None
        else RulesRewriter(ctx.today, max_title_chars=guards.max_title_chars, max_h1_chars=guards.max_h1_chars)
    )
    out_dir = Path(args.out_dir) if args.out_dir else Path("data/optimize") / slug_for(url)
    print(f"Source: {label}.")
    began = time.perf_counter()
    result = optimize(
        ctx,
        url,
        html,
        targets,
        goal_rank=args.goal,
        rewriter=rewriter,
        max_rounds=args.max_rounds,
        patience=args.patience,
        budget_usd=args.budget_usd,
        out_dir=out_dir,
        now_label=_now(),
        guards=guards,
    )
    run_s = time.perf_counter() - began
    print(render_optimize(result, ctx.accuracy_line()), end="")
    rounds = sandbox.count_of(len(result.rounds), "round file", "round files")
    print(f"Wrote best.html, {rounds}, log.json, and report.txt to {out_dir}.")
    if llm is not None:
        print(llm.cost_summary())
    print(f"Model load {load_s:.1f} s. Optimize {run_s:.1f} s for {len(result.rounds)} rounds.")
    return 0


def _split_queries(text: str | None) -> list[str] | None:
    if not text:
        return None
    found = [" ".join(part.split()) for part in text.split(",") if part.strip()]
    return found or None


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _serve(settings, database: str, args) -> None:
    import uvicorn

    from aeo.ranksim.serve import DemoState, bound_hosts, create_app, loopback_hosts

    def legacy():
        print("Fitting the leave-one-query-out model for the old demo. This does not call an LLM or a SERP API.", flush=True)
        return DemoState(settings, args.serp, database)

    loopback = args.host in {"127.0.0.1", "localhost", "::1"}
    app = create_app(
        settings=settings,
        database=database,
        projects_root=args.projects_dir,
        legacy_factory=legacy,
        allowed_hosts=loopback_hosts(args.port) if loopback else bound_hosts(args.host, getattr(args, "allow_host", [])),
    )
    if not loopback:
        print(
            f"Warning: listening on {args.host}. Anyone who can reach this address can use the tool; there is no login. "
            "Browsers must use an IP address, localhost, or a name given with --allow-host (other names are refused, which blocks DNS rebinding)."
        )
    shown = "127.0.0.1" if loopback else args.host
    print(f"Ranksim: http://{shown}:{args.port}/  (old demo at /demo)", flush=True)
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


SYNTHETIC_ENV = "RANKSIM_ALLOW_SYNTHETIC"


def _serp_choice(args) -> str:
    """--mock (or --provider mock) writes synthetic SERPs. That is for tests only, so it needs an explicit opt-in."""
    choice = "mock" if args.mock else args.provider
    if str(choice).strip().lower() == "mock" and os.environ.get(SYNTHETIC_ENV) != "1":
        raise _usage_error(
            f"--mock writes synthetic SERPs, not Google results, and is for tests only. Set {SYNTHETIC_ENV}=1 to use it anyway. "
            "For REAL results set SERPAPI_KEY, SERPER_API_KEY, or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD."
        )
    return choice


def _collect(settings, database: str, args) -> None:
    choice = _serp_choice(args)
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
    session = open_session(database)
    try:
        report, label, provider = calibrate(session, build_keywords())
    finally:
        session.close()
    print(f"Label: {label}")
    print(f"Provider: {provider}")
    print(f"Queries: {report.n_queries}. Pairs: {report.n_pairs}.")
    print(f"Mean Spearman: {_num(report.spearman)}")
    print(f"Mean NDCG at 10: {_num(report.ndcg_at_10)}")
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
    choice = _serp_choice(args)
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
    found = sum(1 for row in rows if row.our_position is not None)
    kind = "Google" if rows and rows[0].is_google and not rows[0].synthetic else provider.name
    print(f"Recorded {len(rows)} queries from {kind}.")
    if provider.synthetic:
        print("Synthetic mock tracker. Not a Google rank check.")
    print(f"SmartCrew was inside the fetched results for {found} queries.")
    print("A blank position means the domain was outside this fetch, not a made-up rank.")


def _train(database: str, args) -> None:
    report = _score_file(database, args.serp_file, fit=True, model_out=args.model_out, folds=args.folds)
    _print_accuracy(report)


def _evaluate(database: str, args) -> None:
    report = _score_file(database, args.serp_file, fit=False, model_out=None, folds=args.folds)
    _print_accuracy(report)


def _score_file(database: str, path: str, *, fit: bool, model_out: str | None, folds: str = "loo"):
    from aeo.ranksim.groundtruth import load_serp_file
    from aeo.ranksim.lambdamart import build_examples, cross_validate, fit_lambdamart, save_model

    labels = load_serp_file(path)
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
        report.note += " Navboost prior was withheld because it copied the label position."
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


def _usage_error(message: str) -> SystemExit:
    """A usage or input error exits 2, so a script can tell it from a regression (exit 1)."""
    print(message, file=sys.stderr)
    return SystemExit(2)


def _load_source(settings, source: str, url: str | None) -> tuple[str, str, str]:
    path = Path(source)
    if url and "://" not in url:
        url = "https://" + url.lstrip("/")
    if path.exists() and path.is_file():
        if not url:
            raise _usage_error("A local HTML file needs --url, the canonical page you are testing, for example --url https://www.example.com/page/.")
        return path.read_text(), url, f"file:{path}"
    if source.startswith("http://") or source.startswith("https://"):
        fetched = _fetch_one(settings, source)
        if fetched.status != "fetched":
            raise _usage_error(f"Fetch failed for {source}: {fetched.error or fetched.status}")
        canonical = url or source
        return fetched.html, canonical, source
    raise _usage_error(f"Source not found: {source}")


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
