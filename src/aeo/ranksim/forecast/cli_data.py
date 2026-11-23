"""The data commands of plan step 4: python -m aeo.ranksim forecast pull|cohort|universe|panel|content|updates.

These commands wrap what trainingset.py and sources/*.py already do. They add
no new data logic beyond the spam and parking filter in sources/newsites.py.

What is REAL in the output: past result pages, traffic curves and exact
positions as DataForSEO returned them, the cohort evidence (registration
lists, RDAP, DNS, Common Crawl), the search universe metrics, the panel and
Google's update calendar. What is ESTIMATED: a cohort site's launch date,
always with its uncertainty. What is PREDICTED: a content score. Nothing here
is SYNTHETIC.

Money rules (docs/plan/IMPLEMENTATION_SPEC.md section 5):

- pull past-pages, pull traffic and pull positions are dry runs unless
  --execute is passed. The dry run prints the cost budget.py gives, how many
  calls already have a saved raw answer, what is still to pay, and what the
  budget guard would say. It sends nothing and writes nothing.
- With --execute the budget guard checks what is still to pay before the
  first call. Without an approval, or without the $1 sample of that kind
  when the pull costs more than $1, it refuses and sends nothing.
- A pull is a job with a frozen manifest. A rerun reads the saved raw
  answers and pays nothing twice.

Every other command is free. cohort build, universe build --download and
updates sync --fetch read public services that need no key.

Commands plug into cli.py through REGISTRARS. cli.py imports this module when
it builds the parser; this module imports cli.py, never the other way round
at import time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from ..site_history import site_key
from . import content as content_mod
from . import pipeline
from . import trainingset as ts
from .budget import (
    KIND_HISTORICAL_SERPS,
    KIND_HISTORICAL_TRAFFIC,
    KIND_RANKED_KEYWORDS,
    PRICES_READ_ON,
    usd_text,
)
from .cli import (
    EXIT_CHECK_FAILED,
    EXIT_OK,
    HISTORICAL_LOCATION_DEFAULT,
    PROG,
    Context,
    _add_paid,
    _add_search_input,
    _dry_run_or_refuse,
    _iso_date,
    _searches_from,
    _today,
)
from .config import DEFAULT_LANGUAGE, EXCLUDED_SITES
from .types import PREDICTED, REAL

PLAN_SEARCHES = 5000  # build plan step 4: past result pages for 5,000 searches
PLAN_SITES = 20000  # 20,000 sites that started 12 to 30 months ago
PLAN_POSITION_SITES = 2000  # exact positions for 2,000 of them
PLAN_WINDOWS = ts.DEFAULT_WINDOWS
POSITION_PICK_SEED = 20261122  # the label-blind order that picks the sites for exact positions
SAMPLE_KINDS: tuple[tuple[str, str], ...] = (
    (KIND_HISTORICAL_SERPS, "past result pages"),
    (KIND_HISTORICAL_TRAFFIC, "traffic history"),
    (KIND_RANKED_KEYWORDS, "exact positions"),
)
DEFAULT_LABS_PANEL = "panel_labs"  # past result pages bought from DataForSEO go here, beside the free sample panel


# ----------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------


def _jsonable(payload: Any) -> Any:
    """A payload with numpy numbers turned into plain numbers, so --json prints numbers, not text."""
    from .sources.panel import _json_default

    return json.loads(json.dumps(payload, default=_json_default))


def _lines_of(path: str | Path) -> list[str]:
    return pipeline.read_search_file(path)


def _cohort_sites(path: str | Path, which: str) -> list[dict[str, Any]]:
    """Rows of a cohort parquet written by cohort build: every site (all) or only the kept ones (kept).

    kept means kept_after_filter when the spam filter ran, else kept. Both are
    decided from launch-era evidence only, so the choice is label blind.
    """
    import pandas as pd

    frame = pd.read_parquet(path)
    if "site" not in frame.columns:
        raise ValueError(f"{path} has no site column. Give a cohort table written by: {PROG} cohort build")
    if which == "kept":
        column = "kept_after_filter" if "kept_after_filter" in frame.columns else "kept"
        if column not in frame.columns:
            raise ValueError(f"{path} has no kept column. Pass --which all to use every site.")
        frame = frame[frame[column].fillna(False).astype(bool)]
    columns = [name for name in ("site", "launch_date", "registered_on") if name in frame.columns]
    return frame[columns].to_dict("records")


TABLE_BUILDERS: dict[str, str] = {
    "universe": f"{PROG} universe build",
    "cohort": f"{PROG} cohort build",
}
FILE_FLAGS: dict[str, str] = {
    "searches": "a text file with one search per line",
    "sites": "a text file with one site per line",
}


def _refuse_inputs(ctx: Context, args: argparse.Namespace) -> int | None:
    """Refuse a missing input file or a SYNTHETIC table before anything is priced. None when the inputs are fine.

    A paid pull never runs on SYNTHETIC names, dry runs included: the price would be for made-up sites.
    """
    for flag, text in FILE_FLAGS.items():
        given = getattr(args, flag, None)
        if given and not Path(given).is_file():
            return ctx.fail(f"{given} does not exist. Give --{flag} {text}.")
    for flag, builder in TABLE_BUILDERS.items():
        given = getattr(args, flag, None)
        if not given:
            continue
        if not Path(given).is_file():
            return ctx.fail(f"The {flag} table {given} does not exist. Build it with: {builder}")
        why = ts.synthetic_table_reason(given)
        if why is not None:
            return ctx.fail(f"The {flag} table {given} is SYNTHETIC ({why}), and a paid pull never runs on SYNTHETIC names.")
    return None


def _sites_from(args: argparse.Namespace) -> list[Any]:
    found: list[Any] = list(getattr(args, "site", None) or [])
    if getattr(args, "sites", None):
        found.extend(_lines_of(args.sites))
    if getattr(args, "cohort", None):
        found.extend(_cohort_sites(args.cohort, args.which))
    return found


def _keywords_from(args: argparse.Namespace) -> list[str]:
    found = _searches_from(args)
    if getattr(args, "universe", None):
        from .sources.universe import load_universe

        found.extend(row.keyword for row in load_universe(args.universe))
    return found


def _unique_site_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """One row per normalised site, first seen kept, empty and excluded sites removed (the same rule the pulls use)."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = dict(row) if isinstance(row, dict) else {"site": row}
        names = ts._wanted_sites([record.get("site")])
        if names and names[0] not in out:
            out[names[0]] = {**record, "site": names[0]}
    return list(out.values())


def _position_pick(sites: Sequence[Any], limit: int | None) -> list[str]:
    """At most `limit` sites in a frozen label-blind order (sha256 of a seed and the site). None keeps all."""
    names = ts._wanted_sites(sites)
    if limit is None or limit >= len(names):
        return names
    return sorted(names, key=lambda site: hashlib.sha256(f"{POSITION_PICK_SEED}|{site}".encode()).hexdigest())[:limit]


def _cost_plan(ctx: Context, kind: str, item: str, calls: int, usd: Decimal, notes: Sequence[str]) -> pipeline.CostPlan:
    """What the budget guard would say now about `usd` more of `kind`. Writes nothing."""
    ledger = ctx.ledger()
    state = ledger.state()
    refusal = pipeline.guard_refusal(ledger, kind, usd, item) if usd > 0 or not state.ok else None
    return pipeline.CostPlan(
        provider="dataforseo", item=item, kind=kind, calls=calls, call_unit="Labs calls still to pay", usd=usd,
        approved_usd=ledger.approved_total if state.ok else None, spent_usd=ledger.spent_total if state.ok else None,
        remaining_usd=ledger.remaining if state.ok else None, sample_recorded=(kind in state.samples) if state.ok else None,
        refusal=refusal, notes=tuple(notes),
    )


def _report_payload(report: ts.PullReport) -> dict[str, Any]:
    return {
        "kind": report.kind, "job": report.job, "dry_run": report.dry_run, "units": report.units,
        "planned_usd": str(report.planned_usd), "calls_planned": report.calls_planned, "calls_saved": report.calls_saved,
        "unpaid_usd": None if report.unpaid_usd is None else str(report.unpaid_usd), "calls": report.calls,
        "calls_from_cache": report.calls_from_cache, "spent_usd": str(report.spent_usd), "failures": report.failures,
        "outputs": report.outputs, "rows": report.rows, "label": REAL,
    }


def _run_pull(
    ctx: Context,
    args: argparse.Namespace,
    *,
    kind: str,
    what: str,
    pull: Callable[..., ts.PullReport],
    inputs: Sequence[Any],
    options: dict[str, Any],
) -> int:
    """The shared dry run, guard check and executed run of the three paid pulls."""
    root = ctx.paths.cache / "trainingset"
    dry = pull(None, inputs, execute=False, root=root, raw_dir=ctx.paths.raw, **options)
    if dry.units == 0:
        return ctx.fail(f"Nothing to pull: no {what} left after removing empty and excluded entries.")
    saved = dry.calls_saved or 0
    unpaid = dry.unpaid_usd if dry.unpaid_usd is not None else dry.planned_usd
    notes = [
        f"Job {dry.job}: {dry.units} {what} in {dry.calls_planned} calls. The whole job costs {usd_text(dry.planned_usd)} "
        f"from the price table of {PRICES_READ_ON.isoformat()}.",
        f"{saved} of {dry.calls_planned} calls already have a saved answer and are never paid again.",
        "The provider's own cost field settles each call in the ledger.",
    ]
    if kind == KIND_HISTORICAL_SERPS:
        from .budget import historical_serps_estimate_lines

        months = int(options.get("months", 24))
        notes.extend(historical_serps_estimate_lines(months, dry.units))
    label = dict(SAMPLE_KINDS)[kind]
    plan = _cost_plan(ctx, kind, f"the {label} pull of {dry.units} {what}", dry.calls_planned - saved, unpaid, notes)
    stop = _dry_run_or_refuse(ctx, plan, args.execute, {"report": _report_payload(dry)})
    if stop is not None:
        return stop
    client = ctx.dataforseo_client()
    before = ctx.ledger().spent_total
    report = pull(client, inputs, execute=True, root=root, **options)
    spent = ctx.ledger().spent_total - before
    payload = {"dry_run": False, "report": _report_payload(report), "spent_usd": str(spent), "remaining_usd": str(ctx.ledger().remaining)}
    lines = [f"{line} (REAL)" for line in report.lines()]
    lines += [f"Wrote {name}: {path}." for name, path in sorted(report.outputs.items())]
    lines.append(f"Spent {usd_text(spent)} in this run. Remaining {usd_text(ctx.ledger().remaining)}.")
    if report.failures:
        lines.append(f"{len(report.failures)} call(s) failed. Run the same command again: saved answers cost nothing and the "
                     "failed calls are asked again.")
    ctx.emit(payload, lines)
    return EXIT_CHECK_FAILED if report.failures else EXIT_OK


# ----------------------------------------------------------------------------------------------
# pull
# ----------------------------------------------------------------------------------------------


def cmd_pull_plan(ctx: Context, args: argparse.Namespace) -> int:
    refused = _refuse_inputs(ctx, args)
    if refused is not None:
        return refused
    keywords = _keywords_from(args)
    sites = _unique_site_rows(_sites_from(args))
    searches: int | list[str] = sorted(set(keywords)) if keywords else PLAN_SEARCHES
    site_input: int | list[Any] = sites if sites else PLAN_SITES
    position_sites = args.position_sites if args.position_sites is not None else (None if sites else PLAN_POSITION_SITES)
    windows = tuple(args.months) if args.months else PLAN_WINDOWS
    plan = ts.plan_pulls(searches, site_input, windows, args.rows_per_site, position_sites=position_sites, today=_today(ctx, args.today))
    ledger = ctx.ledger()
    state = ledger.state()
    samples = {kind: (kind in state.samples) if state.ok else None for kind, _ in SAMPLE_KINDS}
    missing = [kind for kind, recorded in samples.items() if recorded is not True]
    payload = {
        "plan": {"searches": plan.searches, "sites": plan.sites, "rows_per_site": plan.rows_per_site, "windows": list(plan.windows),
                 "lines": plan.table(), "totals_upper": {str(k): str(v) for k, v in plan.totals_upper.items()},
                 "totals_plan_reading": {str(k): str(v) for k, v in plan.totals_plan_reading.items()},
                 "totals_sample_rate": {str(k): str(v) for k, v in plan.totals_sample_rate.items()},
                 "sites_with_age": plan.sites_with_age,
                 "sites_without_launch_page": {str(k): v for k, v in plan.sites_without_launch_page.items()},
                 "sites_24_to_30_months": plan.sites_24_to_30_months, "sites_over_30_months": plan.sites_over_30_months},
        "inputs": {"searches_from": "files" if keywords else "the plan's count", "sites_from": "files" if sites else "the plan's count"},
        "ledger": {"ok": state.ok, "problem": state.problem,
                   "approved_usd": str(ledger.approved_total) if state.ok else None,
                   "spent_usd": str(ledger.spent_total) if state.ok else None,
                   "remaining_usd": str(ledger.remaining) if state.ok else None},
        "samples_recorded": samples,
        "samples_missing": [{"kind": kind, "command": pipeline.SAMPLE_COMMANDS.get(kind)} for kind in missing],
        "prices_read_on": PRICES_READ_ON.isoformat(),
    }
    lines = ["The big pulls of build step 4. A plan, not a charge. Every amount comes from budget.py."]
    if not keywords:
        lines.append(f"Searches: the plan's {PLAN_SEARCHES}. Give --searches or --universe to price your own list.")
    if not sites:
        lines.append(f"Sites: the plan's {PLAN_SITES}, exact positions for {PLAN_POSITION_SITES}. Give --sites or --cohort to price your own.")
    lines.extend(plan.text().splitlines())
    if state.ok:
        lines.append(f"Approved {usd_text(ledger.approved_total)}. Spent or held {usd_text(ledger.spent_total)}. "
                     f"Remaining {usd_text(ledger.remaining)}.")
    else:
        lines.append(f"The spend ledger cannot be trusted: {state.problem}")
    for kind, name in SAMPLE_KINDS:
        if samples[kind]:
            lines.append(f"The $1 sample for {name} is recorded.")
        else:
            lines.append(f"The $1 sample for {name} is MISSING. Run: {pipeline.SAMPLE_COMMANDS.get(kind)}")
    lines.append(f"Then run {PROG} pull past-pages, pull traffic and pull positions. Each is a dry run until you add --execute.")
    ctx.emit(payload, lines)
    return EXIT_OK


def cmd_pull_past_pages(ctx: Context, args: argparse.Namespace) -> int:
    refused = _refuse_inputs(ctx, args)
    if refused is not None:
        return refused
    keywords = _keywords_from(args)
    if not keywords:
        return ctx.fail(f"Give --searches with a file of searches, --search, or --universe with a table from: {PROG} universe build")
    if args.months < 1:
        return ctx.fail("--months must be 1 or more.")
    panel_dir = Path(args.panel_dir) if args.panel_dir else ctx.paths.cache / DEFAULT_LABS_PANEL
    options = {"months": args.months, "location_code": args.location_code, "language_code": args.language, "locale": args.locale,
               "job": args.job, "panel_dir": panel_dir, "today": _today(ctx, None)}
    return _run_pull(ctx, args, kind=KIND_HISTORICAL_SERPS, what="searches", pull=ts.pull_past_result_pages, inputs=keywords,
                     options=options)


def cmd_pull_traffic(ctx: Context, args: argparse.Namespace) -> int:
    refused = _refuse_inputs(ctx, args)
    if refused is not None:
        return refused
    sites = _sites_from(args)
    if not sites:
        return ctx.fail(f"Give --sites with a file of sites, --site, or --cohort with a table from: {PROG} cohort build")
    if args.months < 1:
        return ctx.fail("--months must be 1 or more.")
    options = {"months": args.months, "location_code": args.location_code, "language_code": args.language, "job": args.job,
               "today": _today(ctx, None)}
    return _run_pull(ctx, args, kind=KIND_HISTORICAL_TRAFFIC, what="sites", pull=ts.pull_traffic_curves, inputs=sites, options=options)


def cmd_pull_positions(ctx: Context, args: argparse.Namespace) -> int:
    refused = _refuse_inputs(ctx, args)
    if refused is not None:
        return refused
    sites = _sites_from(args)
    if not sites:
        return ctx.fail(f"Give --sites with a file of sites, --site, or --cohort with a table from: {PROG} cohort build")
    if args.max_sites is not None and args.max_sites < 1:
        return ctx.fail("--max-sites must be 1 or more.")
    chosen = _position_pick(sites, args.max_sites)
    options = {"rows_per_site": args.rows_per_site, "location_code": args.location_code, "language_code": args.language,
               "locale": args.locale, "job": args.job}
    return _run_pull(ctx, args, kind=KIND_RANKED_KEYWORDS, what="sites", pull=ts.pull_exact_positions, inputs=chosen, options=options)


# ----------------------------------------------------------------------------------------------
# cohort build
# ----------------------------------------------------------------------------------------------


def _read_registrations(path: str | Path) -> list[Any]:
    """Registrations from a CSV with the columns site and listed_on (and optionally source). Sites are normalised."""
    from .sources import newsites as ns

    out = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "site" not in reader.fieldnames or "listed_on" not in reader.fieldnames:
            raise ValueError(f"{path} needs a header with the columns site and listed_on (and optionally source).")
        for number, row in enumerate(reader, start=2):
            site = site_key(str(row.get("site") or "").strip().lower())
            if not site or "." not in site:
                continue
            try:
                listed = date.fromisoformat(str(row.get("listed_on") or "").strip())
            except ValueError as error:
                raise ValueError(f"{path} line {number}: listed_on must be a date like 2025-10-27") from error
            source = str(row.get("source") or "").strip() or f"file:{Path(path).name}"
            out.append(ns.Registration(site=site, listed_on=listed, source=source, tld=site.rsplit(".", 1)[-1]))
    return out


def cmd_cohort_build(ctx: Context, args: argparse.Namespace) -> int:
    try:
        return _cohort_build(ctx, args)
    except (ConnectionError, httpx.HTTPError, subprocess.CalledProcessError) as error:
        return ctx.fail(f"A public source could not be read: {type(error).__name__}: {error}. Run the command again: "
                        "what was already fetched is cached and is not fetched twice.")


def _cohort_build(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import newsites as ns

    use_cc = not args.no_cc
    if use_cc and not args.after:
        return ctx.fail("Give at least one --after crawl id (a Common Crawl crawl after the registrations), or pass --no-cc.")
    if not args.registrations and not (args.start and args.end):
        return ctx.fail("Give --registrations with a CSV of site and listed_on, or --start and --end to read a public list.")
    own = ctx.http_client is None
    http = ctx.http_client or ns.make_client()
    try:
        notes: list[str] = []
        if args.registrations:
            regs = _read_registrations(args.registrations)
            notes.append(f"Registrations read from {args.registrations}: {len(regs)}.")
        else:
            repo = ns.REPOS[args.repo]
            fetcher = ns.NrdFetcher(repo, root=ctx.paths.root / "public" / "nrd" / repo.key, http=http, mode=args.mode,
                                    sleep=ctx.clock.sleep)
            daily, skipped = fetcher.daily_lists(args.start, args.end)
            regs = ns.registrations_from(daily, tlds=tuple(args.tld or ns.DEFAULT_TLDS), cap_per_day=args.cap_per_day)
            notes.append(f"Daily lists from {repo.repo}: {len(daily)} days, {len(skipped)} days skipped. "
                         f"Registrations after the TLD filter and the cap of {args.cap_per_day} a day: {len(regs)}.")
        before = len(regs)
        regs = [reg for reg in regs if site_key(str(reg.site).strip().lower()) not in EXCLUDED_SITES]
        if before != len(regs):
            notes.append(f"Excluded client sites dropped: {before - len(regs)}.")
        if not regs:
            return ctx.fail("No registrations to build a cohort from.")
        out_dir = Path(args.out) if args.out else ctx.paths.cache / "newsites"
        plan = ns.CrawlPlan(tuple(args.before or ()), tuple(args.after or ())) if use_cc else ns.CrawlPlan((), ())
        result = ns.cohort(regs, http=http, plan=plan, crawls=None if use_cc else {},
                           cache_root=ctx.paths.cache / "newsites", public_root=ctx.paths.root / "public",
                           use_rdap=not args.no_rdap, use_dns=not args.no_dns, use_cc=use_cc, fetch_pages=not args.no_pages,
                           sleep=ctx.clock.sleep, now=ctx.clock.now)
    finally:
        if own:
            http.close()
    spam = ns.spam_filter(result.rows, result.pages)
    paths = ns.write_cohort(result, out_dir, args.name, spam)
    payload = {"label": REAL, "launch_date_label": "ESTIMATED", "name": args.name, "paths": paths, "funnel": result.report.get("funnel"),
               "spam_filter": result.report.get("spam_filter"), "notes": notes,
               "evidence_used": {"rdap": not args.no_rdap, "dns": not args.no_dns, "commoncrawl": use_cc, "pages": use_cc and not args.no_pages}}
    if not use_cc:
        notes.append("Common Crawl was skipped, so no site has content evidence and no site is kept.")
    lines = [*notes, "The cohort (REAL evidence; launch_date is an ESTIMATE with its uncertainty in days):"]
    lines.extend(result.funnel.lines())
    lines.extend(spam.lines())
    lines.append("Every registration stays in the table with its flags and its spam_rule. Nothing was deleted.")
    lines.extend(f"Wrote {name}: {path}." for name, path in sorted(paths.items()))
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# universe build
# ----------------------------------------------------------------------------------------------


def cmd_universe_build(ctx: Context, args: argparse.Namespace) -> int:
    try:
        return _universe_build(ctx, args)
    except (ConnectionError, httpx.HTTPError) as error:
        return ctx.fail(f"The public keyword shard could not be downloaded: {type(error).__name__}: {error}.")


def _universe_build(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import universe as uv

    country = args.country.upper()
    if country not in uv.COUNTRY_LOCALE:
        return ctx.fail(f"No locale is set for country {country}. Choose one of {', '.join(sorted(uv.COUNTRY_LOCALE))}.")
    locale = uv.COUNTRY_LOCALE[country]
    notes: list[str] = []
    kaggle: list[Path] = [Path(p) for p in args.kaggle or ()]
    if not args.kaggle:
        folder = ctx.paths.root / "public" / "kaggle_google_keywords"
        for name in uv.KAGGLE_SHARDS.get(country, ()):
            local = folder / name
            if local.is_file():
                kaggle.append(local)
            elif args.download:
                kaggle.append(uv.download_kaggle_shard(name, http=ctx.http_client, root=folder))
                notes.append(f"Downloaded the public Kaggle shard {name} (free, CC BY 4.0).")
            else:
                notes.append(f"The Kaggle shard {name} is not in {folder}. Add --download to fetch it (free).")
    dfs_files: list[Path] = [Path(p) for p in args.dfs_keywords or ()]
    if not args.dfs_keywords:
        base = ctx.paths.root / "public" / "dataforseo_samples"
        for rel in (f"google/keywords/{locale}.json.gz", f"google_history/keywords/{locale}.json.gz"):
            if (base / rel).is_file():
                dfs_files.append(base / rel)
    if not kaggle and not dfs_files:
        return ctx.fail("No keyword source file was found. Give --kaggle or --dfs-keywords, or add --download.")
    universe = uv.build_universe(args.seed_term, country=country, n=args.n, niche=args.niche, kaggle_files=kaggle,
                                 dfs_keyword_files=dfs_files, seed=args.seed, brand_terms=tuple(args.brand or ()),
                                 allow_partial_coverage=args.allow_partial_coverage)
    paths = uv.write_universe(universe, Path(args.out) if args.out else ctx.paths.cache / "universe")
    counts = universe.counts
    payload = {"label": REAL, "niche": universe.niche, "country": country, "locale": locale, "paths": paths, "sha256": universe.sha256,
               "rule_hash": universe.rule_hash, "counts": counts, "notes": notes,
               "sources": [str(p) for p in kaggle] + [str(p) for p in dfs_files]}
    lines = [*notes,
             f"Search universe for {universe.niche} ({country}): {counts['selected']} searches selected of {counts['kept']} kept "
             f"candidates (REAL vendor metrics, newest month).",
             "By third, selected: " + ", ".join(f"{k} {v}" for k, v in counts["selected_by_stratum"].items()) + ".",
             "Dropped: " + (", ".join(f"{k} {v}" for k, v in counts["dropped"].items()) or "none") + "."]
    if counts["shortfall_by_stratum"]:
        lines.append("Short of the quota: " + ", ".join(f"{k} {v}" for k, v in counts["shortfall_by_stratum"].items())
                     + ". A short third is not filled from the others.")
    lines.append("These metrics describe searches as of 2026. They pick which searches to study and are never launch-month features.")
    lines.append(f"sha256 {universe.sha256}. Rule hash {universe.rule_hash}.")
    lines.extend(f"Wrote {name}: {path}." for name, path in sorted(paths.items()))
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# panel build and panel summary
# ----------------------------------------------------------------------------------------------


def cmd_panel_build(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import panel as panel_mod

    root = Path(args.root) if args.root else ctx.paths.root / "public" / "dataforseo_samples"
    out = Path(args.out) if args.out else ctx.paths.cache / "panel"
    files = panel_mod.select_sample_files(panel_mod.discover_sample_files(root), args.kinds.split(","),
                                          args.locales.split(",") if args.locales else None)
    if not files:
        return ctx.fail(f"No result-page sample files under {root} for kinds {args.kinds}"
                        + (f" and locales {args.locales}" if args.locales else "") + ".")
    try:
        report = panel_mod.build_panel_into(files, out, overwrite=args.overwrite, root=root)
    except panel_mod.PanelExistsError as error:
        return ctx.fail(str(error))
    rows = sum(item["rows"] for item in report["files"])
    excluded = sum(item.get("dropped_excluded_site", 0) for item in report["files"])
    payload = {"label": REAL, "out_dir": report["out_dir"], "files": report["files"], "rows": rows,
               "replaced_existing": report["replaced_existing"], "rows_dropped_for_excluded_sites": excluded}
    lines = [f"Built the panel in {report['out_dir']} from {len(files)} sample files: {rows} rows (REAL, country level, single pulls)."]
    for item in report["files"]:
        lines.append(f"  {Path(item['path']).name} ({item['kind']}): {item['rows']} rows from {item['snapshots']} pages.")
    lines.append(f"Rows of excluded client sites dropped: {excluded}.")
    lines.append("The old panel in that folder was replaced." if report["replaced_existing"] else "The folder was new or empty.")
    lines.append(f"See the summary with: {PROG} panel summary" + (f" --panel-dir {out}" if args.out else ""))
    ctx.emit(payload, lines)
    return EXIT_OK


def cmd_panel_summary(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import panel as panel_mod

    folder = Path(args.panel_dir) if args.panel_dir else ctx.paths.cache / "panel"
    if not panel_mod.panel_datasets(folder):
        return ctx.fail(f"No panel in {folder}. Build it with: {PROG} panel build")
    payload = _jsonable(panel_mod.panel_summary(folder, new_sites=not args.no_new_sites))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [f"Panel in {folder}. {payload['label']}."]
    for name, block in payload["datasets"].items():
        overall = block["overall"]
        lines.append(f"{name}: {overall['keywords']} searches, {overall['snapshots']} pages, {overall['rows']} rows, "
                     f"{overall['sites']} sites, dated {overall['date_first']} to {overall['date_last']}, "
                     f"{overall['poison_risk_pages']} pages at risk of being altered (left out of training), "
                     f"{len(block['by_locale'])} locales.")
        depth = overall["pages_reaching_rank_50"]
        lines.append(f"  {depth} of {overall['snapshots']} pages reach organic rank 50.")
    if args.out:
        lines.append(f"Wrote the full summary to {args.out}.")
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# content score
# ----------------------------------------------------------------------------------------------


def _looks_like_html(path: Path, text: str) -> bool:
    return path.suffix.lower() in (".html", ".htm") or text.lstrip()[:1] == "<"


def cmd_content_score(ctx: Context, args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        return ctx.fail(f"{path} is not a file.")
    text = path.read_text(encoding="utf-8", errors="replace")
    is_html = args.format == "html" or (args.format == "auto" and _looks_like_html(path, text))
    if is_html:
        page = content_mod.page_text_from_html(text, args.url or "")
    else:
        page = content_mod.page_text_from_fields(title=None, h1=None, meta_description=None, body=text, url=args.url or "")
    value_from = args.value_from or (content_mod.VALUE_HEAD if is_html else content_mod.VALUE_PAGE)
    models_dir = None if os.environ.get(content_mod.ENV_MODELS_DIR) else ctx.paths.models
    scorer = content_mod.ContentScorer(args.backend, models_dir=models_dir, allow_download=bool(args.allow_download), value_from=value_from)
    score = scorer.score(args.search, page)
    backend = "the pinned local model" if score.mode == content_mod.MODE_CROSS else "the lexical fallback"
    payload = {**score.to_dict(), "search": args.search, "file": str(path), "read_as": "html" if is_html else "text", "backend": backend}
    lines = [
        f"Content score for {args.search!r} on {path.name} (read as {'HTML' if is_html else 'plain text'}): "
        + (f"{score.value:.6f}" if score.value is not None else "no score") + f" ({PREDICTED}).",
        f"Backend: {backend} ({score.mode}, {score.model}). Value from: {score.value_from}.",
        "Parts: " + ", ".join(f"{k} {'none' if v is None else f'{v:.6f}'}" for k, v in score.parts.items()) + ".",
        "Scores of the model and of the lexical fallback are on different scales. Never compare them.",
    ]
    if score.fallback_reason:
        lines.append(f"Why the fallback ran: {score.fallback_reason}")
    if score.value is None:
        lines.append("The page gave the scorer nothing to read for this part. Try --value-from page.")
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# updates show and updates sync
# ----------------------------------------------------------------------------------------------


def cmd_updates_show(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import updates as up

    payload, path = up.load_calendar(root=ctx.paths.root)
    items = up._from_payload(payload)
    if args.kind:
        items = [item for item in items if item.kind in set(args.kind)]
    origin = "the packaged copy" if path.resolve() == up.PACKAGED_PATH.resolve() else "the cache"
    kinds = Counter(item.kind for item in items)
    shown = items[-args.limit:] if args.limit else items
    out = {"label": REAL, "file": str(path), "origin": origin, "fetched_at": payload.get("fetched_at"),
           "coverage_start": payload.get("coverage_start"), "count": len(items), "kinds": dict(sorted(kinds.items())),
           "updates": [item.__dict__ for item in shown]}
    lines = [f"Google ranking updates from {origin} ({path}), fetched {payload.get('fetched_at')}. REAL, as Google lists them.",
             f"Coverage starts {payload.get('coverage_start')}. Earlier updates are not on the dashboard.",
             f"{len(items)} updates. Kinds: " + (", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) or "none") + "."]
    for item in shown:
        end = item.end_date.isoformat() if item.end_date else "still open"
        lines.append(f"  {item.start_date.isoformat()} to {end}: {item.name} ({item.kind}).")
    ctx.emit(out, lines)
    return EXIT_OK


def cmd_updates_sync(ctx: Context, args: argparse.Namespace) -> int:
    from .sources import updates as up

    target = up.cache_path(ctx.paths.root)
    if not args.fetch:
        ctx.emit({"fetched": False, "cache": str(target)}, [
            "Nothing was fetched. Add --fetch to read Google's public status dashboard (free, no key) into "
            f"{target}. The packaged copy that ships with the code is never changed.",
        ])
        return EXIT_OK
    try:
        previous = {item.incident_id for item in up.load_updates(root=ctx.paths.root)}
    except FileNotFoundError:
        previous = set()
    try:
        payload = up.fetch_updates(ctx.http_client, root=ctx.paths.root, read_incident_pages=not args.history_only, sleep=ctx.clock.sleep)
    except httpx.HTTPError as error:
        return ctx.fail(f"Google's status dashboard could not be read: {type(error).__name__}. Nothing was written.")
    new = sorted(item["incident_id"] for item in payload["updates"] if item["incident_id"] not in previous)
    ctx.emit({"fetched": True, "cache": str(target), "count": payload["count"], "fetched_at": payload["fetched_at"], "new_incidents": new,
              "label": REAL}, [
        f"Read {payload['count']} ranking updates from Google's status dashboard (REAL) at {payload['fetched_at']}.",
        f"{len(new)} of them were not in the calendar before.",
        f"Wrote {target}. The packaged copy was not changed.",
    ])
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# The parser
# ----------------------------------------------------------------------------------------------


def _add_site_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sites", default=None, help="a text file with one site per line")
    parser.add_argument("--site", action="append", default=None, help="one site (repeatable)")
    parser.add_argument("--cohort", default=None, help="a cohort table written by cohort build")
    parser.add_argument("--which", choices=("kept", "all"), default="kept",
                        help="with --cohort: the kept sites (default) or every registered site")


def _add_labs_location(parser: argparse.ArgumentParser, *, locale: bool = True) -> None:
    parser.add_argument("--location-code", type=int, default=HISTORICAL_LOCATION_DEFAULT, help="a country code (Labs is country level)")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    if locale:
        parser.add_argument("--locale", default=ts.DEFAULT_LOCALE, help="CC_lang of the location, for the keyword ids")
    parser.add_argument("--job", default=None, help="a job name (default: named from the request)")


def _register_pull(sub: Any, common: argparse.ArgumentParser) -> None:
    pull = sub.add_parser("pull", help="the big paid pulls of step 4 (dry run by default) and their plan")
    inner = pull.add_subparsers(dest="pull_command", required=True)
    plan = inner.add_parser("plan", parents=[common], help="counts, costs, approval and missing $1 samples (free)")
    _add_search_input(plan)
    plan.add_argument("--universe", default=None, help="a universe table written by universe build")
    _add_site_input(plan)
    plan.add_argument("--months", type=int, action="append", default=None, help="a past-pages window in months (repeatable, default 24 and 30)")
    plan.add_argument("--rows-per-site", type=int, default=ts.DEFAULT_ROWS_PER_SITE)
    plan.add_argument("--position-sites", type=int, default=None, help="sites that get exact positions (default the plan's 2,000)")
    plan.add_argument("--today", type=_iso_date, default=None, help="the day the site ages are counted from")
    plan.set_defaults(handler=cmd_pull_plan)
    past = inner.add_parser("past-pages", parents=[common], help="past result pages for each search (paid)")
    _add_search_input(past)
    past.add_argument("--universe", default=None, help="a universe table written by universe build")
    past.add_argument("--months", type=int, default=24, help="calendar months, this month included")
    past.add_argument("--panel-dir", default=None, help=f"where the pages go (default cache/{DEFAULT_LABS_PANEL})")
    _add_labs_location(past)
    _add_paid(past)
    past.set_defaults(handler=cmd_pull_past_pages)
    traffic = inner.add_parser("traffic", parents=[common], help="monthly traffic history of each site (paid)")
    _add_site_input(traffic)
    traffic.add_argument("--months", type=int, default=30, help="calendar months, this month included")
    _add_labs_location(traffic, locale=False)
    _add_paid(traffic)
    traffic.set_defaults(handler=cmd_pull_traffic)
    positions = inner.add_parser("positions", parents=[common], help="exact positions of each site (paid)")
    _add_site_input(positions)
    positions.add_argument("--rows-per-site", type=int, default=ts.DEFAULT_ROWS_PER_SITE)
    positions.add_argument("--max-sites", type=int, default=None, help="at most this many sites, picked in a frozen label-blind order")
    _add_labs_location(positions)
    _add_paid(positions)
    positions.set_defaults(handler=cmd_pull_positions)


def _register_cohort(sub: Any, common: argparse.ArgumentParser) -> None:
    from .sources import newsites as ns

    cohort = sub.add_parser("cohort", help="the new-site cohort")
    inner = cohort.add_subparsers(dest="cohort_command", required=True)
    build = inner.add_parser("build", parents=[common], help="collect evidence, apply the frozen rules and the spam filter (free)")
    build.add_argument("--name", required=True, help="a name for the output files")
    build.add_argument("--registrations", default=None, help="a CSV with the columns site and listed_on")
    build.add_argument("--repo", choices=sorted(ns.REPOS), default="cenk", help="the public list, with --start and --end")
    build.add_argument("--start", type=_iso_date, default=None, help="first list day, like 2025-10-27")
    build.add_argument("--end", type=_iso_date, default=None, help="last list day")
    build.add_argument("--mode", choices=("raw", "git"), default="raw")
    build.add_argument("--tld", action="append", default=None)
    build.add_argument("--cap-per-day", type=int, default=ns.DEFAULT_CAP_PER_DAY)
    build.add_argument("--before", action="append", default=None, help="a crawl id that ended before the registrations")
    build.add_argument("--after", action="append", default=None, help="a crawl id after the registrations")
    build.add_argument("--no-rdap", action="store_true")
    build.add_argument("--no-dns", action="store_true")
    build.add_argument("--no-cc", action="store_true", help="skip Common Crawl (then no site has content evidence)")
    build.add_argument("--no-pages", action="store_true")
    build.add_argument("--out", default=None, help="default cache/newsites")
    build.set_defaults(handler=cmd_cohort_build)


def _register_universe(sub: Any, common: argparse.ArgumentParser) -> None:
    from .sources import universe as uv

    universe = sub.add_parser("universe", help="the search universe")
    inner = universe.add_subparsers(dest="universe_command", required=True)
    build = inner.add_parser("build", parents=[common], help="candidate searches of one niche in difficulty thirds (free)")
    build.add_argument("--niche", required=True)
    build.add_argument("--seed-term", action="append", required=True, help="a seed term of the niche (repeatable)")
    build.add_argument("--country", default="US")
    build.add_argument("--n", type=int, default=300)
    build.add_argument("--seed", type=int, default=uv.DEFAULT_SEED)
    build.add_argument("--kaggle", action="append", default=None, help="a Kaggle keyword shard (default the shards under public/)")
    build.add_argument("--dfs-keywords", action="append", default=None, help="a DataForSEO keyword sample file")
    build.add_argument("--brand", action="append", default=None, help="a niche brand to drop (repeatable)")
    build.add_argument("--download", action="store_true", help="fetch missing public Kaggle shards (free)")
    build.add_argument("--allow-partial-coverage", action="store_true")
    build.add_argument("--out", default=None, help="default cache/universe")
    build.set_defaults(handler=cmd_universe_build)


def _register_panel(sub: Any, common: argparse.ArgumentParser) -> None:
    panel = sub.add_parser("panel", help="the result-page panel")
    inner = panel.add_subparsers(dest="panel_command", required=True)
    build = inner.add_parser("build", parents=[common], help="build the panel from the public sample files (free)")
    build.add_argument("--root", default=None, help="the sample download folder (default public/dataforseo_samples)")
    build.add_argument("--out", default=None, help="the panel folder (default cache/panel)")
    build.add_argument("--kinds", default="history,snapshot", help="history, snapshot, or both")
    build.add_argument("--locales", default=None, help="comma list of CC_lang (default all present)")
    build.add_argument("--overwrite", action="store_true", help="replace a panel folder that already holds files")
    build.set_defaults(handler=cmd_panel_build)
    summ = inner.add_parser("summary", parents=[common], help="counts, dates, depth and gaps of a panel (free)")
    summ.add_argument("--panel-dir", default=None, help="default cache/panel")
    summ.add_argument("--out", default=None, help="also write the full JSON here")
    summ.add_argument("--no-new-sites", action="store_true", help="skip the new-site report (faster)")
    summ.set_defaults(handler=cmd_panel_summary)


def _register_content(sub: Any, common: argparse.ArgumentParser) -> None:
    content = sub.add_parser("content", help="the content match scorer")
    inner = content.add_subparsers(dest="content_command", required=True)
    score = inner.add_parser("score", parents=[common], help="score one page file against one search (free)")
    score.add_argument("--search", required=True)
    score.add_argument("--file", required=True, help="the page: saved HTML or plain text")
    score.add_argument("--format", choices=("auto", "html", "text"), default="auto")
    score.add_argument("--url", default=None)
    score.add_argument("--backend", choices=("auto", content_mod.MODE_CROSS, content_mod.MODE_LEXICAL), default="auto")
    score.add_argument("--value-from", choices=content_mod.VALUE_FROM, default=None,
                       help="which part is the score (default head for HTML, page for text)")
    score.add_argument("--allow-download", action="store_true", help="fetch the pinned model if it is missing (free)")
    score.set_defaults(handler=cmd_content_score)


def _register_updates(sub: Any, common: argparse.ArgumentParser) -> None:
    updates = sub.add_parser("updates", help="Google's ranking update calendar")
    inner = updates.add_subparsers(dest="updates_command", required=True)
    show = inner.add_parser("show", parents=[common], help="show the calendar (free, no network)")
    show.add_argument("--kind", action="append", default=None, help="only this kind, like core or spam (repeatable)")
    show.add_argument("--limit", type=int, default=15, help="show the newest this many (0 for all)")
    show.set_defaults(handler=cmd_updates_show)
    sync = inner.add_parser("sync", parents=[common], help="refresh the cached calendar from Google's dashboard (free)")
    sync.add_argument("--fetch", action="store_true", help="really read the dashboard. Without it nothing is fetched.")
    sync.add_argument("--history-only", action="store_true", help="skip the incident pages (dates then come from the list)")
    sync.set_defaults(handler=cmd_updates_sync)


REGISTRARS: tuple[Callable[[Any, argparse.ArgumentParser], None], ...] = (
    _register_pull,
    _register_cohort,
    _register_universe,
    _register_panel,
    _register_content,
    _register_updates,
)
