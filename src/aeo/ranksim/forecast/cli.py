"""The `forecast` commands: python -m aeo.ranksim forecast <command>.

Every manual step of the measuring half of the build plan is one command here.
What is REAL in the output: clean readings, marker 1 agreement, grades and the
rates computed from them, and money the providers said they charged. What is
PREDICTED: forecasts. What is SYNTHETIC: everything `demo` prints. Each line of
output that carries a number says which one it is.

Rules every command follows:

- A command that can cost money is a dry run unless --execute is passed. A dry
  run prints the provider, the count of calls, the exact cost and the remaining
  approved budget, and does nothing else. With --execute it still stops when
  the budget guard refuses.
- Money in the output always comes from budget.py.
- --json prints machine output.
- Exit codes: 0 ok, 1 a check failed (a marker not passed, a chain broken),
  2 usage or configuration error.
- No command prints a key. Credential values are also scrubbed from anything
  written to stdout or stderr.

Each command is one small function `cmd_<name>(ctx, args) -> int`. They share
one `Context` that opens the scoreboard, the spend ledger and a result-page
source. The forecasting commands (train, backtest, upload, update, due) live in
cli_forecasting.py and the data commands in cli_data.py, plugged in the same way.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

import httpx

from aeo.errors import ProviderConfigError

from . import pipeline
from .budget import (
    PRICES,
    PRICES_READ_ON,
    BudgetError,
    ProviderError,
    SpendLedger,
    historical_serps_estimate,
    historical_serps_sample_rate_estimate,
    historical_traffic_cost,
    ranked_keywords_cost,
    serp_cost,
    usd_text,
)
from .config import DEFAULT_DEVICE, DEFAULT_LANGUAGE, DEFAULT_PULLS, MIN_PULL_GAP_SECONDS, SOURCES, STAGE_DAYS, TOP_DEPTH, ForecastPaths, forecast_paths
from .scoreboard import ChainBroken, Scoreboard, ScoreboardError, ScoreboardRefusal
from .seeds import ManifestError, SearchCandidate, SeedManifest, SeedSite, load_frozen, pick_niche_searches
from .asof import utc_iso
from .noise import sample_marker1_searches
from .reader import DEFAULT_RULE_VERSION, READER_VERSIONS
from .types import REAL, PullSpec

PROG = "python -m aeo.ranksim forecast"
DEFAULT_EXPORT_DIR = pipeline.DEFAULT_EXPORT_DIR  # RANKSIM_EXPORT_DIR overrides it, see pipeline.default_export_dir
DEFAULT_SEED_PICK = 20261123
HISTORICAL_LOCATION_DEFAULT = 2840  # United States. Labs data is country level only.
CANDIDATE_COLUMNS = ("search", "difficulty_value", "difficulty_source", "volume")
EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2


# ----------------------------------------------------------------------------------------------
# Output with secrets scrubbed
# ----------------------------------------------------------------------------------------------


class ScrubbedStream:
    """A text stream that replaces every known credential value with [hidden] before writing."""

    def __init__(self, stream: TextIO, secrets: Sequence[str]) -> None:
        self._stream = stream
        self._secrets = [value for value in secrets if value]

    def write(self, text: str) -> int:
        for value in self._secrets:
            text = text.replace(value, "[hidden]")
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()


# ----------------------------------------------------------------------------------------------
# The shared context
# ----------------------------------------------------------------------------------------------


@dataclass
class Context:
    """What every command shares: where the data lives, how to print, and how to open things."""

    paths: ForecastPaths
    as_json: bool
    out: TextIO
    err: TextIO
    clock: pipeline.Clock
    settings_factory: Callable[[], Any]
    http_client: httpx.Client | None = None
    _settings: Any = None
    _scoreboard: Scoreboard | None = None
    _ledger: SpendLedger | None = None
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def settings(self) -> Any:
        if self._settings is None:
            self._settings = self.settings_factory()
        return self._settings

    def scoreboard(self) -> Scoreboard:
        if self._scoreboard is None:
            self._scoreboard = pipeline.open_scoreboard(self.paths, clock=self.clock)
            self._closers.append(self._scoreboard.close)
        return self._scoreboard

    def ledger(self) -> SpendLedger:
        if self._ledger is None:
            self._ledger = pipeline.open_ledger(self.paths, clock=self.clock)
        return self._ledger

    def source(self, provider: str, *, serper_location: str | None = None, location_code: int | None = None) -> Any:
        locations = {int(location_code): serper_location} if serper_location and location_code is not None else None
        made = pipeline.make_source(
            provider,
            self.settings(),
            self.ledger(),
            self.paths.raw,
            clock=self.clock,
            http_client=self.http_client,
            serper_locations=locations,
        )
        client = getattr(made, "client", None)
        closer = getattr(client, "close", None) or getattr(made, "close", None)
        if callable(closer):
            self._closers.append(closer)
        return made

    def dataforseo_client(self) -> Any:
        """A DataForSEO client with the ledger attached, for the samples and the locations list."""
        source = self.source("dataforseo")
        return source.client

    def close(self) -> None:
        while self._closers:
            closer = self._closers.pop()
            try:
                closer()
            except Exception:  # closing must never hide the command's own result
                pass

    # -- printing ------------------------------------------------------------------------------

    def emit(self, payload: Any, lines: Iterable[str]) -> None:
        if self.as_json:
            self.out.write(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n")
        else:
            for line in lines:
                self.out.write(f"{line}\n")

    def fail(self, message: str, code: int = EXIT_USAGE) -> int:
        self.err.write(f"{message}\n")
        return code


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


# ----------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date like 2026-11-30") from error


def _money(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not an amount in USD") from error
    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError("the amount must be more than 0 USD")
    return value


def _today(ctx: Context, given: date | None) -> date:
    return given if given is not None else ctx.clock.now().astimezone(timezone.utc).date()


def _plan_lines(plan: pipeline.CostPlan, *, execute: bool) -> list[str]:
    lines = [
        f"Provider: {plan.provider}.",
        f"Calls: {plan.calls} {plan.call_unit}.",
        f"Exact cost from the price table of {PRICES_READ_ON.isoformat()}: {usd_text(plan.usd)}.",
    ]
    if plan.remaining_usd is not None:
        lines.append(
            f"Approved {usd_text(plan.approved_usd or 0)}. Spent or held {usd_text(plan.spent_usd or 0)}. "
            f"Remaining {usd_text(plan.remaining_usd)}."
        )
    elif plan.kind is not None:
        lines.append("Remaining approved budget: unknown, the ledger cannot be read.")
    lines.extend(plan.notes)
    if plan.refusal:
        lines.append(("Stopped. " if execute else "The budget guard would refuse this now. ") + plan.refusal)
    if not execute:
        lines.append("Dry run. Nothing was sent and nothing was spent. Add --execute to run it.")
    return lines


def _dry_run_or_refuse(ctx: Context, plan: pipeline.CostPlan, execute: bool, extra: dict[str, Any] | None = None) -> int | None:
    """Print the dry run and return 0, or print the refusal and return 2. None means: go on and execute."""
    if not execute:
        payload = {"dry_run": True, "plan": plan.to_dict(), **(extra or {})}
        ctx.emit(payload, _plan_lines(plan, execute=False))
        return EXIT_OK
    if plan.refusal:
        ctx.err.write("\n".join(_plan_lines(plan, execute=True)) + "\n")
        return EXIT_USAGE
    return None


def _searches_from(args: argparse.Namespace) -> list[str]:
    found: list[str] = list(getattr(args, "search", None) or [])
    path = getattr(args, "searches", None)
    if path:
        found.extend(pipeline.read_search_file(path))
    return found


def _specs_from(args: argparse.Namespace) -> tuple[list[PullSpec], str | None]:
    """PullSpecs from --manifest (frozen) or from --searches/--search with --location-code."""
    depth = int(args.depth)
    if getattr(args, "manifest", None):
        manifest = load_frozen(args.manifest)
        return pipeline.manifest_specs(manifest, depth=depth), manifest.manifest_hash()
    searches = _searches_from(args)
    if not searches:
        raise ValueError("Give --manifest, or --searches with a file of searches, or --search.")
    if args.location_code is None:
        raise ValueError("--location-code is needed with a list of searches. Find the city code with: forecast locations --name <city>")
    return pipeline.search_specs(searches, int(args.location_code), language=args.language, device=args.device, depth=depth), None


def _status_word(ok: bool | None) -> str:
    return {True: "PASSED", False: "NOT PASSED", None: "NOT MEASURABLE YET"}[ok]


# ----------------------------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------------------------


def cmd_status(ctx: Context, args: argparse.Namespace) -> int:
    board = ctx.scoreboard()
    counts = pipeline.scoreboard_counts(board)
    chain = board.verify()
    ledger = ctx.ledger()
    state = ledger.state()
    creds = pipeline.credentials_present(ctx.settings())
    problems = [item for item in pipeline.markers_module.plan_schedule_problems() if not item["ok"]]
    export_dir = pipeline.default_export_dir()
    anchored = board.heads_anchor is not None
    calendar = pipeline.stage_calendar(board, _today(ctx, None))
    payload = {
        "data_dir": str(ctx.paths.root),
        "scoreboard_url": ctx.paths.scoreboard_url,
        "counts": counts,
        "chain": chain.to_dict(),
        "ledger": {
            "ok": state.ok,
            "problem": state.problem,
            "approved_usd": str(ledger.approved_total) if state.ok else None,
            "spent_usd": str(ledger.spent_total) if state.ok else None,
            "remaining_usd": str(ledger.remaining) if state.ok else None,
            "samples_recorded": sorted(state.samples) if state.ok else [],
        },
        "credentials_set": creds,
        "plan_schedule_problems": problems,
        "committed_export": {"folder": str(export_dir), "exists": anchored},
        "stages_due": calendar["due"],
        "stages_missed": calendar["missed"],
    }
    lines = [
        f"Forecast data folder: {ctx.paths.root}.",
        "Scoreboard: " + ", ".join(f"{count} {name}" for name, count in counts.items()) + ".",
        "The hash chains verify." if chain.ok else "The hash chains do NOT verify. " + " ".join(chain.problems),
        (f"The committed export in {export_dir} is checked on every report and verify: the database must still hold "
         "every row it names." if anchored else
         f"No committed export exists yet in {export_dir}, so rows cut off the end of the database cannot be caught. "
         f"Run {PROG} export and commit the folder."),
    ]
    if state.ok:
        lines.append(
            f"Budget: approved {usd_text(ledger.approved_total)}, spent or held {usd_text(ledger.spent_total)}, "
            f"remaining {usd_text(ledger.remaining)}."
        )
        lines.append("Samples recorded: " + (", ".join(sorted(state.samples)) if state.samples else "none") + ".")
    else:
        lines.append(f"The spend ledger cannot be trusted: {state.problem}")
    lines.append(f"DataForSEO login set: {'yes' if creds['dataforseo'] else 'no'}. Serper key set: {'yes' if creds['serper'] else 'no'}.")
    lines.append(f"The plan's own schedule has {len(problems)} problems. See: {PROG} seeds schedule.")
    if calendar["due"]:
        lines.append(f"Stage forecasts due in the next {pipeline.STAGE_DUE_WINDOW_DAYS} days. Each must be made on or before "
                     "its stage day:")
        lines.extend(f"  {item['site']} ({item['source']}) {item['stage']} on {item['stage_date']}." for item in calendar["due"])
    else:
        lines.append(f"No stage forecast is due in the next {pipeline.STAGE_DUE_WINDOW_DAYS} days.")
    if calendar["missed"]:
        lines.append("Stages MISSED (no forecast on or before the stage day). A missed stage is never backfilled:")
        lines.extend(f"  {item['site']} ({item['source']}) {item['stage']} was due on {item['stage_date']}." for item in calendar["missed"])
    ctx.emit(payload, lines)
    return EXIT_OK if chain.ok and state.ok else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# budget
# ----------------------------------------------------------------------------------------------


def cmd_budget_show(ctx: Context, args: argparse.Namespace) -> int:
    ledger = ctx.ledger()
    state = ledger.state()
    if not state.ok:
        payload = {"ok": False, "problem": state.problem}
        ctx.emit(payload, [f"The spend ledger cannot be trusted: {state.problem}"])
        return EXIT_CHECK_FAILED
    payload = {
        "ok": True,
        "path": str(ledger.path),
        "approved_usd": str(ledger.approved_total),
        "spent_usd": str(ledger.spent_total),
        "remaining_usd": str(ledger.remaining),
        "open_reservations": [item.__dict__ for item in ledger.open_reservations()],
        "spent_by_kind": {kind: str(ledger.spent_for_kind(kind)) for kind in sorted(state.kind_spent)},
        "samples": state.samples,
        "rows": state.rows,
        "head": state.head,
    }
    lines = [
        f"Spend ledger {ledger.path}: {state.rows} rows, the chain verifies.",
        f"Approved {usd_text(ledger.approved_total)}. Spent or held {usd_text(ledger.spent_total)}. Remaining {usd_text(ledger.remaining)}.",
    ]
    for kind in sorted(state.kind_spent):
        lines.append(f"Spent on {kind}: {usd_text(ledger.spent_for_kind(kind))}.")
    for item in ledger.open_reservations():
        lines.append(f"Open reservation {item.reservation_id} for {item.item}: {usd_text(item.estimate_micros)}.")
    for kind in sorted(state.samples):
        lines.append(f"Sample recorded for {kind}: {json.dumps(state.samples[kind], sort_keys=True)}")
    if not state.samples:
        lines.append("No $1 sample is recorded yet. Each paid kind needs one before a big pull.")
    ctx.emit(payload, lines)
    return EXIT_OK


def cmd_budget_approve(ctx: Context, args: argparse.Namespace) -> int:
    ledger = ctx.ledger()
    total = ledger.approve(args.amount, by=args.by, note=args.note)
    payload = {"approved_now_usd": str(args.amount), "approved_total_usd": str(total), "by": args.by, "note": args.note,
               "remaining_usd": str(ledger.remaining)}
    ctx.emit(payload, [
        f"Recorded an approval of {usd_text(args.amount)} by {args.by}.",
        f"Approved in total: {usd_text(total)}. Remaining: {usd_text(ledger.remaining)}.",
        "This moves no money. It only lets the budget guard allow paid calls up to that amount.",
    ])
    return EXIT_OK


def cmd_budget_prices(ctx: Context, args: argparse.Namespace) -> int:
    reading = serp_cost(TOP_DEPTH, pulls=DEFAULT_PULLS)
    plan_items = {
        "one clean reading (5 pulls, depth 50, Standard queue, repair on)": reading,
        "marker 1 (300 searches read twice)": serp_cost(TOP_DEPTH, pulls=DEFAULT_PULLS * 300 * 2),
        "seed sites (200 searches, 16 readings)": serp_cost(TOP_DEPTH, pulls=DEFAULT_PULLS * 200 * 16),
        "past result pages (5,000 searches, 24 months, plan rate of 1 page a month, reserved)": historical_serps_estimate(24, 5000),
        "past result pages (5,000 searches, 24 months, rate seen in the free history samples)": historical_serps_sample_rate_estimate(24, 5000),
        "traffic history (20,000 sites)": historical_traffic_cost(20000),
        "exact positions (2,000 sites, 100 rows)": ranked_keywords_cost(2000, 100),
    }
    payload = {
        "read_on": PRICES_READ_ON.isoformat(),
        "prices": {key: {"usd": str(item.usd), "per": item.per, "source": item.source} for key, item in PRICES.items()},
        "plan_items": {name: str(value) for name, value in plan_items.items()},
    }
    lines = [f"Prices read on {PRICES_READ_ON.isoformat()}."]
    for key, item in PRICES.items():
        lines.append(f"{key}: {usd_text(item.usd)} per {item.per}. Source {item.source}.")
    for name, value in plan_items.items():
        lines.append(f"Cost of {name}: {usd_text(value)}.")
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# sample
# ----------------------------------------------------------------------------------------------


def _sample_finish(ctx: Context, report: Any, before: Decimal) -> int:
    spent = ctx.ledger().spent_total - before
    recorded = bool(getattr(report, "recorded", True))
    payload = {"kind": report.kind, "calls": report.calls, "findings": report.findings, "spent_usd": str(spent),
               "remaining_usd": str(ctx.ledger().remaining), "label": REAL, "recorded": recorded}
    if recorded:
        lines = [f"Sample recorded for {report.kind} after {report.calls} calls (REAL)."]
    else:
        why = report.findings.get("not_recorded_because", "the sample learned nothing")
        lines = [
            f"The sample was NOT recorded for {report.kind} after {report.calls} calls: {why}.",
            "The gate for big pulls of this kind stays closed.",
        ]
    lines.extend(f"Found {key}: {json.dumps(value, sort_keys=True)} (REAL)." for key, value in sorted(report.findings.items()))
    lines.append(f"Spent {usd_text(spent)}. Remaining {usd_text(ctx.ledger().remaining)}.")
    ctx.emit(payload, lines)
    return EXIT_OK if recorded else EXIT_CHECK_FAILED


def cmd_sample_serp(ctx: Context, args: argparse.Namespace) -> int:
    searches = _searches_from(args)
    if not searches:
        return ctx.fail("Give --searches with a file of searches, or --search.")
    if args.location_code is None:
        return ctx.fail("--location-code is needed. The reader is pinned to one city.")
    specs = pipeline.search_specs(searches, int(args.location_code), depth=int(args.depth))
    plan = pipeline.plan_sample_serp(ctx.ledger(), args.provider, len(specs), depth=int(args.depth), specs=specs)
    stop = _dry_run_or_refuse(ctx, plan, args.execute)
    if stop is not None:
        return stop
    before = ctx.ledger().spent_total
    if args.provider == "dataforseo":
        report = ctx.dataforseo_client().sample_serp(specs)
    else:
        source = ctx.source("serper", serper_location=args.serper_location, location_code=int(args.location_code))
        report = source.sample(specs)
    return _sample_finish(ctx, report, before)


def cmd_sample_historical_serps(ctx: Context, args: argparse.Namespace) -> int:
    keywords = _searches_from(args)
    if not keywords:
        return ctx.fail("Give --searches with a file of searches, or --search.")
    plan = pipeline.plan_sample_historical_serps(ctx.ledger(), len(set(keywords)), months_back=int(args.months_back))
    stop = _dry_run_or_refuse(ctx, plan, args.execute)
    if stop is not None:
        return stop
    before = ctx.ledger().spent_total
    report = ctx.dataforseo_client().sample_historical_serps(
        keywords, int(args.location_code), args.language, months_back=int(args.months_back)
    )
    return _sample_finish(ctx, report, before)


def _lines_of(path: str) -> list[str]:
    return pipeline.read_search_file(path)


def cmd_sample_traffic(ctx: Context, args: argparse.Namespace) -> int:
    sites = list(args.site or []) + (_lines_of(args.sites) if args.sites else [])
    if not sites:
        return ctx.fail("Give --sites with a file of small new sites, one per line, or --site.")
    plan = pipeline.plan_sample_traffic(ctx.ledger(), len(set(sites)))
    stop = _dry_run_or_refuse(ctx, plan, args.execute)
    if stop is not None:
        return stop
    before = ctx.ledger().spent_total
    report = ctx.dataforseo_client().sample_historical_traffic(sites)
    return _sample_finish(ctx, report, before)


def cmd_sample_ranked_keywords(ctx: Context, args: argparse.Namespace) -> int:
    targets = list(args.site or []) + (_lines_of(args.sites) if args.sites else [])
    if not targets:
        return ctx.fail("Give --sites with a file of sites, one per line, or --site.")
    plan = pipeline.plan_sample_ranked_keywords(ctx.ledger(), len(set(targets)), limit=int(args.limit))
    stop = _dry_run_or_refuse(ctx, plan, args.execute)
    if stop is not None:
        return stop
    before = ctx.ledger().spent_total
    report = ctx.dataforseo_client().sample_ranked_keywords(targets, limit=int(args.limit))
    return _sample_finish(ctx, report, before)


# ----------------------------------------------------------------------------------------------
# locations
# ----------------------------------------------------------------------------------------------


def cmd_locations(ctx: Context, args: argparse.Namespace) -> int:
    if args.csv:
        from .dataforseo import load_locations_csv

        found = load_locations_csv(args.csv)
        origin = f"the file {args.csv}"
    else:
        found = ctx.dataforseo_client().locations(args.country)
        origin = "DataForSEO's free locations list"
    needle = args.name.casefold()
    hits = [
        item for item in found
        if needle in (item.location_name or "").casefold()
        and (args.all_types or (item.location_type or "").casefold() == "city")
        and (not args.csv or not args.country or (item.country_iso_code or "").casefold() == args.country.casefold())
    ]
    hits.sort(key=lambda item: (item.location_name or "", item.location_code))
    payload = {"origin": origin, "matches": [item.__dict__ for item in hits[: args.limit]], "total_matches": len(hits)}
    lines = [f"{len(hits)} places match {args.name!r} in {origin}. This costs nothing."]
    for item in hits[: args.limit]:
        lines.append(f"{item.location_code}: {item.location_name} ({item.location_type}, {item.country_iso_code}).")
    if not args.all_types:
        lines.append("Only cities are shown. The reader is pinned to one city. Add --all-types to see the rest.")
    ctx.emit(payload, lines)
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# seeds
# ----------------------------------------------------------------------------------------------


def cmd_seeds_template(ctx: Context, args: argparse.Namespace) -> int:
    folder = Path(args.out)
    folder.mkdir(parents=True, exist_ok=True)
    candidates = folder / "seed_candidates.csv"
    manifest = folder / "seed_manifest_template.json"
    for path in (candidates, manifest):
        if path.exists():
            return ctx.fail(f"{path} already exists. The template never overwrites a file.")
    with candidates.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CANDIDATE_COLUMNS)
        writer.writerow(["emergency plumber", "42", "dataforseo keyword difficulty", "1900"])
    template = {
        "manifest_version": 1,
        "location_code": 0,
        "device": DEFAULT_DEVICE,
        "language": DEFAULT_LANGUAGE,
        "difficulty_metric": "dataforseo keyword difficulty",
        "created_at": "",
        "sites": [],
        "how_to": [
            "Fill seed_candidates.csv with every candidate search of one niche and its difficulty from one named metric.",
            "Then run seeds pick once per niche. It writes the sites and their searches into the manifest.",
            "The example row in the CSV is only an example. Replace it.",
        ],
    }
    manifest.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    ctx.emit({"candidates": str(candidates), "manifest_template": str(manifest)}, [
        f"Wrote {candidates} with the columns {', '.join(CANDIDATE_COLUMNS)}.",
        f"Wrote {manifest}. It is a guide only. seeds pick writes the real manifest.",
    ])
    return EXIT_OK


def _read_candidates(path: str) -> list[SearchCandidate]:
    out: list[SearchCandidate] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in CANDIDATE_COLUMNS[:3] if column not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} has no column {missing[0]}. The columns are {', '.join(CANDIDATE_COLUMNS)}.")
        for number, row in enumerate(reader, start=2):
            try:
                value = float(row["difficulty_value"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path} line {number}: difficulty_value {row['difficulty_value']!r} is not a number") from error
            volume_text = (row.get("volume") or "").strip()
            out.append(SearchCandidate(
                search=row["search"],
                difficulty_value=value,
                difficulty_source=row["difficulty_source"].strip(),
                volume=int(volume_text) if volume_text else None,
            ))
    return out


def cmd_seeds_pick(ctx: Context, args: argparse.Namespace) -> int:
    candidates = _read_candidates(args.candidates)
    metric = candidates[0].difficulty_source
    out = Path(args.out)
    existing: SeedManifest | None = None
    taken_searches: list[str] = []
    if out.exists():
        existing = SeedManifest.load(out)
        if pipeline.seeds_module.hash_path(out).exists():
            return ctx.fail(f"{out} is frozen. A frozen manifest is never changed. Write to a new file.")
        if existing.location_code != args.location_code or existing.difficulty_metric != metric:
            return ctx.fail(f"{out} has another location code or difficulty metric. One manifest has one of each.")
        taken = {pipeline.site_key(site.domain) for site in existing.sites}
        clash = sorted(taken & {pipeline.site_key(domain) for domain in args.domain})
        if clash:
            return ctx.fail(f"{out} already has {clash[0]}.")
        # Searches already given to sites of this niche are not picked again (no search on two sites of one niche).
        taken_searches = [item.search for site in existing.sites if site.niche == args.niche for item in site.searches]
    picked = pick_niche_searches(candidates, args.domain, per_site=args.per_site, seed=args.seed,
                                 allow_shared=args.allow_shared, exclude=() if args.allow_shared else taken_searches)
    new_sites = [
        SeedSite(domain=domain, niche=args.niche, launch_date=args.launch_date, pages=args.pages, searches=tuple(items),
                 built_by=args.built_by)
        for domain, items in sorted(picked.items())
    ]
    if existing is not None:
        manifest = SeedManifest(sites=existing.sites + tuple(new_sites), location_code=existing.location_code,
                                device=existing.device, language=existing.language,
                                difficulty_metric=existing.difficulty_metric, created_at=existing.created_at,
                                allow_shared=existing.allow_shared or bool(args.allow_shared),
                                after_launch_reason=existing.after_launch_reason)
    else:
        manifest = SeedManifest(sites=tuple(new_sites), location_code=args.location_code, difficulty_metric=metric,
                                created_at=utc_iso(ctx.clock.now()), allow_shared=bool(args.allow_shared))
    manifest.save(out)
    check = manifest.validate()
    payload = {"manifest": str(out), "sites": [site.domain for site in manifest.sites], "errors": list(check.errors),
               "warnings": list(check.warnings), "seed": args.seed}
    lines = [f"Picked {args.per_site} searches for each of {len(new_sites)} sites in niche {args.niche} with seed {args.seed}."]
    lines.append(f"Wrote {out}. It now has {len(manifest.sites)} sites. It is not frozen yet.")
    lines.extend(f"Warning: {item}" for item in check.warnings)
    lines.extend(f"Error: {item}" for item in check.errors)
    ctx.emit(payload, lines)
    return EXIT_OK


def cmd_seeds_validate(ctx: Context, args: argparse.Namespace) -> int:
    manifest = SeedManifest.load(args.manifest)
    check = manifest.validate()
    payload = {"ok": check.ok, "errors": list(check.errors), "warnings": list(check.warnings), "hash": manifest.manifest_hash()}
    lines = [f"{args.manifest}: {len(check.errors)} errors, {len(check.warnings)} warnings."]
    lines.extend(f"Error: {item}" for item in check.errors)
    lines.extend(f"Warning: {item}" for item in check.warnings)
    ctx.emit(payload, lines)
    return EXIT_OK if check.ok else EXIT_CHECK_FAILED


def cmd_seeds_freeze(ctx: Context, args: argparse.Namespace) -> int:
    manifest = SeedManifest.load(args.manifest)
    if args.after_launch_reason:
        manifest = replace(manifest, after_launch_reason=args.after_launch_reason)
    digest = manifest.freeze(args.manifest, today=_today(ctx, None))
    ctx.emit({"manifest": args.manifest, "hash": digest}, [
        f"Froze {args.manifest} with hash {digest}.",
        "The hash is written beside it. A frozen manifest is never rewritten. Commit both files.",
        f"Next: {PROG} seeds register --manifest {args.manifest}",
    ])
    return EXIT_OK


def cmd_seeds_schedule(ctx: Context, args: argparse.Namespace) -> int:
    manifest = SeedManifest.load(args.manifest)
    result = pipeline.schedule(manifest, today=_today(ctx, args.today))
    lines = result["text"].splitlines()
    lines.append(
        f"{result['searches']} searches. Readings per search: {result['readings_weekly_only']} on the weekly grid, "
        f"{result['readings_with_milestones']} with days 30, 60 and 90."
    )
    lines.append(
        f"Cost of one clean reading: {usd_text(Decimal(result['cost_per_reading_usd']))}. Weekly grid: "
        f"{usd_text(Decimal(result['cost_weekly_only_usd']))}. With the milestone days: {usd_text(Decimal(result['cost_with_milestones_usd']))}."
    )
    for item in result["plan_schedule_problems"]:
        if not item["ok"]:
            lines.append(f"Plan schedule problem, {item['name']}: {item['note']}")
    ctx.emit(result, lines)
    return EXIT_OK


def cmd_seeds_register(ctx: Context, args: argparse.Namespace) -> int:
    manifest = load_frozen(args.manifest)
    result = pipeline.register_manifest(ctx.scoreboard(), manifest, source=args.source, paths=ctx.paths,
                                        today=_today(ctx, None))
    ctx.emit(result.to_dict(), [
        f"Registered the manifest with hash {result.manifest_hash} as source {result.source}.",
        f"Sites added {result.sites_added}, already there {result.sites_already}. "
        f"Searches added {result.searches_added}, already there {result.searches_already}.",
        f"The manifest is frozen in the scoreboard as {result.freeze_name}.",
    ])
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# read
# ----------------------------------------------------------------------------------------------


def cmd_read(ctx: Context, args: argparse.Namespace) -> int:
    if args.provider == "synthetic":
        return ctx.fail("Synthetic readings run only inside forecast demo, so they never sit beside real readings.")
    if float(args.gap_seconds) < MIN_PULL_GAP_SECONDS:
        return ctx.fail(f"--gap-seconds must be at least {MIN_PULL_GAP_SECONDS}. Pulls taken seconds apart share one "
                        "spell of altered pages, so they are not separate pulls.")
    specs, manifest_hash = _specs_from(args)
    plan = pipeline.plan_reading_cost(ctx.ledger(), args.provider, len(specs), pulls=int(args.pulls), depth=int(args.depth),
                                      specs=specs)
    stop = _dry_run_or_refuse(ctx, plan, args.execute, {"searches": len(specs), "manifest_hash": manifest_hash})
    if stop is not None:
        return stop
    source = ctx.source(args.provider, serper_location=args.serper_location, location_code=specs[0].location_code)
    summary = pipeline.take_readings(ctx.scoreboard(), source, specs, pulls=int(args.pulls),
                                     gap_seconds=float(args.gap_seconds), clock=ctx.clock, ledger=ctx.ledger(),
                                     version=int(args.reader_version))
    label = summary.label
    lines = [
        f"Read {summary.searches} searches with {summary.provider}, {summary.pulls_per_search} pulls each ({label}).",
        f"Pulls returned {summary.pulls_returned}, OK {summary.pulls_ok}, dropped by the filter {summary.pulls_dropped} ({label}).",
        "Drop reasons: " + (", ".join(f"{k} {v}" for k, v in summary.drop_reasons.items()) or "none") + ".",
        f"Readings stored {summary.readings}. Degraded {summary.degraded}. Pulls that look like copies: {summary.not_independent} ({label}).",
        f"Reader {summary.reader_version}. Searches with an earlier reading passed as history: {summary.history_used}.",
        f"Spent {usd_text(summary.spent_usd or 0)}. Remaining {usd_text(ctx.ledger().remaining)}.",
        "All rounds ran in this one process. Altered pages come in spells, so for real readings prefer "
        f"round mode: {PROG} read-round five times across a day, then {PROG} assemble.",
    ]
    for index, message in summary.failed_rounds:
        lines.append(f"Round {index + 1} failed: {message}")
    if summary.budget_refused:
        lines.append("The budget guard refused part of this run. " + pipeline.APPROVE_HINT)
    ctx.emit(summary.to_dict(), lines)
    return EXIT_CHECK_FAILED if summary.failed_rounds else EXIT_OK


def cmd_read_round(ctx: Context, args: argparse.Namespace) -> int:
    if args.provider == "synthetic":
        return ctx.fail("Synthetic readings run only inside forecast demo, so they never sit beside real readings.")
    specs, manifest_hash = _specs_from(args)
    plan = pipeline.plan_round_cost(ctx.ledger(), args.provider, len(specs), depth=int(args.depth), specs=specs)
    stop = _dry_run_or_refuse(ctx, plan, args.execute, {"searches": len(specs), "manifest_hash": manifest_hash})
    if stop is not None:
        return stop
    source = ctx.source(args.provider, serper_location=args.serper_location, location_code=specs[0].location_code)
    summary = pipeline.read_round(ctx.scoreboard(), source, specs, clock=ctx.clock, ledger=ctx.ledger())
    label = summary.label
    lines = [
        f"One round of {summary.searches} searches with {summary.provider} ({label}).",
        f"Pulls stored {summary.pulls_returned}, OK {summary.pulls_ok} ({label}).",
    ]
    if summary.freeze_name:
        lines.append(f"The round is recorded in the scoreboard as {summary.freeze_name}.")
    if summary.failed:
        if summary.pulls_returned:
            lines.append(f"The round failed part way: {summary.failed} The {summary.pulls_returned} pulls it already holds "
                         "were paid for and are stored.")
        else:
            lines.append(f"The round failed and stored nothing: {summary.failed}")
    lines.append(f"Spent {usd_text(summary.spent_usd or 0)}. Remaining {usd_text(ctx.ledger().remaining)}.")
    lines.append(f"No reading is built yet. After the last round of the day run: {PROG} assemble --date <UTC date>")
    ctx.emit(summary.to_dict(), lines)
    return EXIT_CHECK_FAILED if summary.failed else EXIT_OK


def cmd_assemble(ctx: Context, args: argparse.Namespace) -> int:
    has_input = bool(getattr(args, "manifest", None) or _searches_from(args))
    specs = _specs_from(args)[0] if has_input else None
    summary = pipeline.assemble(ctx.scoreboard(), args.date, specs=specs, version=int(args.reader_version))
    labels = ", ".join(f"{count} {name}" for name, count in summary.labels.items()) or "none"
    lines = [
        f"Assembled the readings of {summary.day} (UTC) from {summary.pulls_found} stored pulls in {summary.rounds} rounds.",
        f"Readings built {summary.readings_built}: {labels}. Newly stored {summary.readings_stored}. "
        f"Already stored {summary.readings_already_stored}.",
        f"Degraded {summary.degraded}. Reasons: "
        + (", ".join(f"{k} {v}" for k, v in summary.degraded_reasons.items()) or "none") + ".",
        f"Reader {summary.reader_version}. Searches with an earlier reading passed as history: {summary.history_used}.",
        "This is free. Nothing was sent and nothing was spent.",
    ]
    for item in summary.skipped:
        lines.append(f"Skipped {item['search']}: {item['reason']}")
    if summary.pulls_too_close:
        lines.append(f"{summary.pulls_too_close} pulls came less than {MIN_PULL_GAP_SECONDS} seconds after another pull of "
                     "the same search. Each counts as the same pull and was left out.")
    if summary.already_assembled:
        lines.append(f"{len(summary.already_assembled)} searches already had a reading of {summary.day}. One reading per "
                     "search and date: they were left as they are.")
    if summary.newer_pulls_left_out:
        lines.append(f"{summary.newer_pulls_left_out} of them have pulls stored since that reading was built. Those pulls were "
                     "NOT used. Run assemble only after the last round of the day.")
    if summary.pulls_found == 0:
        lines.append(f"No stored pull completed on {summary.day}. Take the rounds first with: {PROG} read-round")
    ctx.emit(summary.to_dict(), lines)
    failed = summary.skipped or summary.pulls_found == 0 or summary.newer_pulls_left_out
    return EXIT_CHECK_FAILED if failed else EXIT_OK


# ----------------------------------------------------------------------------------------------
# marker 1
# ----------------------------------------------------------------------------------------------


def _read_strata(path: str | None) -> dict[str, str] | None:
    if not path:
        return None
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not {"search", "stratum"} <= set(reader.fieldnames or []):
            raise ValueError(f"{path} needs the columns search and stratum.")
        return {" ".join(row["search"].split()): row["stratum"].strip() for row in reader}


def cmd_marker1_sample(ctx: Context, args: argparse.Namespace) -> int:
    candidates = pipeline.read_search_file(args.candidates)
    strata = _read_strata(args.strata)
    preview = sample_marker1_searches(candidates, args.n, seed=args.seed, strata=strata)
    out = Path(args.out)
    if out.exists() and out.read_text(encoding="utf-8") != "\n".join(preview.searches) + "\n":
        return ctx.fail(f"{out} already exists with other searches. Nothing was frozen and nothing was overwritten.")
    sample, digest = pipeline.freeze_marker1_sample(ctx.scoreboard(), candidates, n=args.n, seed=args.seed,
                                                    strata=strata, name=args.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sample.searches) + "\n", encoding="utf-8")
    payload = {"searches": len(sample.searches), "candidates": sample.candidates, "seed": sample.seed,
               "sample_sha256": sample.sha256, "freeze_name": args.name, "freeze_sha256": digest, "out": str(out)}
    ctx.emit(payload, [
        f"Drew {len(sample.searches)} of {sample.candidates} searches with seed {sample.seed}.",
        f"Frozen in the scoreboard as {args.name} (sha256 {digest}). Wrote {out}.",
        f"Next, on day A: {PROG} read-round --searches {out} --location-code <city> --execute, five rounds hours apart,",
        f"then {PROG} assemble --date <day A>. Do the same on day B, 20 to 28 hours later, then run marker1 report.",
    ])
    return EXIT_OK


def cmd_marker1_report(ctx: Context, args: argparse.Namespace) -> int:
    board = ctx.scoreboard()
    frozen_list = True
    if args.searches:
        searches = pipeline.read_search_file(args.searches)
        try:
            frozen = pipeline.frozen_marker1_searches(board, args.name)
        except ScoreboardRefusal:
            frozen = None
        same = frozen is not None and {pipeline.search_key(item) for item in searches} == {
            pipeline.search_key(item) for item in frozen}
        frozen_list = same
    else:
        searches = pipeline.frozen_marker1_searches(board, args.name)
    result = pipeline.marker1(board, searches, location_code=int(args.location_code), device=args.device,
                              a_date=args.a_date, b_date=args.b_date, reader_version=int(args.reader_version),
                              frozen_list=frozen_list)
    ctx.emit(result, pipeline.marker1_lines(result))
    return EXIT_OK if result["evaluation"]["passed"] is True else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# grade, report, marker
# ----------------------------------------------------------------------------------------------


def cmd_grade(ctx: Context, args: argparse.Namespace) -> int:
    summary = pipeline.grade(ctx.scoreboard(), args.date)
    ctx.emit(summary, pipeline.grade_job.render_summary(summary).splitlines())
    return EXIT_CHECK_FAILED if summary["counts"]["refused"] else EXIT_OK


def cmd_report(ctx: Context, args: argparse.Namespace) -> int:
    result = pipeline.stage_marker(ctx.scoreboard(), source=args.source, stage=args.stage, as_of=_today(ctx, args.as_of),
                                   model_version=args.model_version)
    ctx.emit(result["report"], pipeline.report_module.render_text(result["report"]).splitlines())
    return EXIT_OK


def cmd_marker(ctx: Context, args: argparse.Namespace) -> int:
    if args.number == 1:
        return ctx.fail(f"Marker 1 is judged on two readings a day apart. Run: {PROG} marker1 report --location-code <city>")
    if args.number == 2:
        from .cli_forecasting import marker2

        return marker2(ctx)
    if args.source is None:
        return ctx.fail(f"Marker {args.number} is judged on one source. Add --source seed, pilot, backtest or synthetic.")
    stage = {3: "upload", 4: "d30", 5: "d60"}[args.number]
    result = pipeline.stage_marker(ctx.scoreboard(), source=args.source, stage=stage, as_of=_today(ctx, args.as_of),
                                   model_version=args.model_version)
    evaluation = result["evaluation"]
    ctx.emit(evaluation, pipeline.markers_module.render_marker_text(evaluation).splitlines())
    return EXIT_OK if evaluation["passed"] is True else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# export, import, verify
# ----------------------------------------------------------------------------------------------


def cmd_export(ctx: Context, args: argparse.Namespace) -> int:
    args.out = args.out or str(pipeline.default_export_dir())
    board = ctx.scoreboard()
    if Path(args.out, "HEADS.json").exists():
        # Overwriting an export that is not a prefix of the database would hide rows cut off its end.
        comparison = pipeline.verify_against_export(board, args.out)
        if not comparison.ok:
            ctx.err.write(f"Refused: the export already in {args.out} is NOT a prefix of the database, so it is not "
                          "overwritten. Stop and tell the engineer.\n" + "\n".join(comparison.problems) + "\n")
            return EXIT_CHECK_FAILED
    heads = pipeline.export_scoreboard(board, args.out)
    ctx.emit(heads, [
        f"Exported the scoreboard to {args.out}.",
        f"Forecasts {heads['forecasts']['count']}, head {heads['forecasts']['head']}.",
        f"Grades {heads['grades']['count']}, head {heads['grades']['head']}.",
        "Commit the folder. Git is the outside timestamp.",
    ])
    return EXIT_OK


def cmd_import(ctx: Context, args: argparse.Namespace) -> int:
    url = ctx.paths.scoreboard_url
    args.source_dir = args.source_dir or str(pipeline.default_export_dir())
    status = pipeline.import_scoreboard(url, args.source_dir, clock=ctx.clock)
    ctx.emit(status.to_dict(), [
        f"Rebuilt the scoreboard at {url} from {args.source_dir}.",
        f"Forecasts {status.forecasts_total}, grades {status.grades_total}. The chains verify against HEADS.json.",
    ])
    return EXIT_OK


def cmd_verify(ctx: Context, args: argparse.Namespace) -> int:
    against = args.against
    default_dir = pipeline.default_export_dir()
    if against is None and (default_dir / "HEADS.json").exists() and not args.no_export:
        against = str(default_dir)
    result = pipeline.verify(ctx.scoreboard(), ctx.ledger(), export_dir=against)
    lines = [
        "The scoreboard chains verify." if result["scoreboard"]["ok"] else
        "The scoreboard chains do NOT verify. " + " ".join(result["scoreboard"]["problems"]),
        "The spend ledger chain verifies." if result["ledger"]["ok"] else f"The spend ledger does NOT verify: {result['ledger']['problem']}",
    ]
    if result["export"] is None:
        lines.append("No committed export was compared. Pass --against <folder> to compare one.")
    elif result["export"]["ok"]:
        lines.append(f"The export in {against} is a prefix of the database.")
    else:
        lines.append(f"The export in {against} is NOT a prefix of the database.")
        lines.extend(result["export"]["problems"])
    ctx.emit(result, lines)
    return EXIT_OK if result["ok"] else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# Search Console and the agency form
# ----------------------------------------------------------------------------------------------


def cmd_gsc_import(ctx: Context, args: argparse.Namespace) -> int:
    record = pipeline.import_gsc(ctx.paths, ctx.scoreboard(), site=args.site, file=args.file, kind=args.kind,
                                 period_start=args.period_start, period_end=args.period_end, fetched_at=ctx.clock.now(),
                                 dimensions=[item.strip() for item in args.dimensions.split(",")] if args.dimensions else None)
    ctx.emit(record.to_dict(), [
        f"Imported a Search Console {record.kind} file for {record.site}: {record.rows} rows (REAL), "
        f"{record.first_date or 'no date'} to {record.last_date or 'no date'}.",
        f"Stored at {record.stored_path} under the forecast data folder. sha256 {record.sha256}.",
        f"Frozen in the scoreboard as {record.freeze_name}. Search Console numbers are never graded outcomes.",
    ] + list(record.notes))
    return EXIT_OK


def cmd_form_template(ctx: Context, args: argparse.Namespace) -> int:
    from .agency_form import write_template

    path = write_template(args.out)
    ctx.emit({"template": str(path)}, [f"Wrote the blank agency form to {path}. Send it to the agency. Blank answers stay blank."])
    return EXIT_OK


def cmd_form_validate(ctx: Context, args: argparse.Namespace) -> int:
    from .agency_form import FormError, brand_terms, load_form

    try:
        form = load_form(args.file)
    except FormError as error:
        ctx.emit({"ok": False, "error": str(error)}, [f"The form is not valid: {error}"])
        return EXIT_CHECK_FAILED
    payload = {"ok": True, "site": form.site, "filled_on": form.filled_on.isoformat(), "brand_terms": list(brand_terms(form))}
    ctx.emit(payload, [
        f"The form for {form.site} is valid. Filled in on {form.filled_on.isoformat()} by {form.filled_by}.",
        "Brand terms for the brand search flag: " + (", ".join(brand_terms(form)) or "none") + ".",
    ])
    return EXIT_OK


def cmd_form_import(ctx: Context, args: argparse.Namespace) -> int:
    record = pipeline.import_form(ctx.paths, ctx.scoreboard(), file=args.file)
    ctx.emit(record.to_dict(), [
        f"Imported the agency form for {record.site}. Stored at {record.stored_path}. sha256 {record.sha256}.",
        f"Frozen in the scoreboard as {record.freeze_name}.",
    ] + list(record.notes))
    return EXIT_OK


# ----------------------------------------------------------------------------------------------
# demo
# ----------------------------------------------------------------------------------------------


def cmd_demo(ctx: Context, args: argparse.Namespace) -> int:
    import tempfile

    folder = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="ranksim-demo-"))
    result = pipeline.demo(args.seed, workdir=folder, sites=args.sites)
    ctx.emit(result.to_dict(), result.lines + [f"SYNTHETIC: The demo files are in {folder}."])
    checks_ok = all(item["refused"] for item in result.refusals) and result.chain["ok"] and result.export_check["ok"]
    return EXIT_OK if checks_ok else EXIT_CHECK_FAILED


# ----------------------------------------------------------------------------------------------
# The parser
# ----------------------------------------------------------------------------------------------


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", default=None, help="forecast data folder (default RANKSIM_FORECAST_DIR or data/forecast)")
    common.add_argument("--json", action="store_true", help="print machine output")
    return common


def _add_paid(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execute", action="store_true", help="really send and spend. Without it this is a dry run.")


def _add_search_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--searches", default=None, help="a text file with one search per line")
    parser.add_argument("--search", action="append", default=None, help="one search (repeatable)")


def _register_status(sub: Any, common: argparse.ArgumentParser) -> None:
    sub.add_parser("status", parents=[common], help="where things stand: scoreboard, budget, credentials").set_defaults(handler=cmd_status)


def _register_budget(sub: Any, common: argparse.ArgumentParser) -> None:
    budget = sub.add_parser("budget", help="the spend ledger")
    inner = budget.add_subparsers(dest="budget_command", required=True)
    inner.add_parser("show", parents=[common], help="approved, spent, remaining, samples").set_defaults(handler=cmd_budget_show)
    approve = inner.add_parser("approve", parents=[common], help="record an approval to spend (moves no money)")
    approve.add_argument("--amount", type=_money, required=True, help="USD, for example 50")
    approve.add_argument("--by", required=True, help="the name of the person approving")
    approve.add_argument("--note", required=True, help="what it is for")
    approve.set_defaults(handler=cmd_budget_approve)
    inner.add_parser("prices", parents=[common], help="the price table and what the plan's items cost").set_defaults(handler=cmd_budget_prices)


def _register_sample(sub: Any, common: argparse.ArgumentParser) -> None:
    sample = sub.add_parser("sample", help="the $1 samples before each big pull (paid, dry run by default)")
    inner = sample.add_subparsers(dest="sample_command", required=True)
    serp = inner.add_parser("serp", parents=[common], help="a few clean-reader pulls")
    _add_search_input(serp)
    serp.add_argument("--location-code", type=int, default=None, help="the pinned city code")
    serp.add_argument("--depth", type=int, default=TOP_DEPTH)
    serp.add_argument("--provider", choices=pipeline.PAID_PROVIDERS, default="dataforseo")
    serp.add_argument("--serper-location", default=None, help="Serper place name of the city, for --provider serper")
    _add_paid(serp)
    serp.set_defaults(handler=cmd_sample_serp)
    hist = inner.add_parser("historical-serps", parents=[common], help="how deep and how far back past result pages go")
    _add_search_input(hist)
    hist.add_argument("--location-code", type=int, default=HISTORICAL_LOCATION_DEFAULT, help="a country code (Labs is country level)")
    hist.add_argument("--language", default=DEFAULT_LANGUAGE)
    hist.add_argument("--months-back", type=int, default=24)
    _add_paid(hist)
    hist.set_defaults(handler=cmd_sample_historical_serps)
    traffic = inner.add_parser("traffic", parents=[common], help="do very small new sites show up in the traffic history")
    traffic.add_argument("--sites", default=None, help="a text file with one site per line")
    traffic.add_argument("--site", action="append", default=None)
    _add_paid(traffic)
    traffic.set_defaults(handler=cmd_sample_traffic)
    ranked = inner.add_parser("ranked-keywords", parents=[common], help="exact positions for a few sites")
    ranked.add_argument("--sites", default=None, help="a text file with one site per line")
    ranked.add_argument("--site", action="append", default=None)
    ranked.add_argument("--limit", type=int, default=100, help="rows per site")
    _add_paid(ranked)
    ranked.set_defaults(handler=cmd_sample_ranked_keywords)


def _register_locations(sub: Any, common: argparse.ArgumentParser) -> None:
    loc = sub.add_parser("locations", parents=[common], help="find the city code by name (free)")
    loc.add_argument("--name", required=True, help="part of the city name, for example Austin")
    loc.add_argument("--country", default="us", help="two-letter country code")
    loc.add_argument("--csv", default=None, help="DataForSEO's public locations CSV, to search offline")
    loc.add_argument("--all-types", action="store_true", help="show regions and countries too")
    loc.add_argument("--limit", type=int, default=25)
    loc.set_defaults(handler=cmd_locations)


def _register_seeds(sub: Any, common: argparse.ArgumentParser) -> None:
    seeds = sub.add_parser("seeds", help="the seed-site manifest")
    inner = seeds.add_subparsers(dest="seeds_command", required=True)
    template = inner.add_parser("template", parents=[common], help="write the candidate CSV and a manifest guide")
    template.add_argument("--out", required=True, help="a folder")
    template.set_defaults(handler=cmd_seeds_template)
    pick = inner.add_parser("pick", parents=[common], help="pick a third easy, medium and hard searches per site of one niche")
    pick.add_argument("--candidates", required=True, help="CSV with search, difficulty_value, difficulty_source, volume")
    pick.add_argument("--niche", required=True)
    pick.add_argument("--domain", action="append", required=True, help="a seed domain (repeatable)")
    pick.add_argument("--launch-date", type=_iso_date, required=True, help="the UTC date the sites go live")
    pick.add_argument("--location-code", type=int, required=True, help="the pinned city code")
    pick.add_argument("--pages", type=int, default=12)
    pick.add_argument("--per-site", type=int, default=pipeline.seeds_module.DEFAULT_SEARCHES_PER_SITE)
    pick.add_argument("--seed", type=int, default=DEFAULT_SEED_PICK)
    pick.add_argument("--built-by", default="ranksim")
    pick.add_argument("--allow-shared", action="store_true", help="let two sites of the niche share a search")
    pick.add_argument("--out", required=True, help="the manifest file to write or add to")
    pick.set_defaults(handler=cmd_seeds_pick)
    validate = inner.add_parser("validate", parents=[common], help="check a manifest")
    validate.add_argument("--manifest", required=True)
    validate.set_defaults(handler=cmd_seeds_validate)
    freeze = inner.add_parser("freeze", parents=[common], help="freeze a manifest before launch")
    freeze.add_argument("--manifest", required=True)
    freeze.add_argument("--after-launch-reason", default=None,
                        help="only for a backfilled pilot: why the list is frozen on or after launch (recorded in the manifest)")
    freeze.set_defaults(handler=cmd_seeds_freeze)
    sched = inner.add_parser("schedule", parents=[common], help="reading dates, stage dates, costs and the plan's schedule problems")
    sched.add_argument("--manifest", required=True)
    sched.add_argument("--today", type=_iso_date, default=None)
    sched.set_defaults(handler=cmd_seeds_schedule)
    register = inner.add_parser("register", parents=[common], help="put a frozen manifest's sites and searches on the scoreboard")
    register.add_argument("--manifest", required=True)
    register.add_argument("--source", choices=("seed", "pilot"), default="seed")
    register.set_defaults(handler=cmd_seeds_register)


def _add_request_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", default=None, help="a frozen seed manifest: read all its searches")
    _add_search_input(parser)
    parser.add_argument("--location-code", type=int, default=None, help="the pinned city code, with --searches")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--depth", type=int, default=TOP_DEPTH)


def _add_reader_version(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reader-version", type=int, choices=sorted(READER_VERSIONS), default=DEFAULT_RULE_VERSION,
                        help=f"the clean reader rule (default {DEFAULT_RULE_VERSION})")


def _register_read(sub: Any, common: argparse.ArgumentParser) -> None:
    read = sub.add_parser("read", parents=[common], help="one clean reading run, every round in this process (paid, dry run by default)")
    _add_request_input(read)
    read.add_argument("--pulls", type=int, default=DEFAULT_PULLS)
    read.add_argument("--gap-seconds", type=float, default=float(MIN_PULL_GAP_SECONDS))
    read.add_argument("--provider", choices=pipeline.PROVIDERS, default="dataforseo")
    read.add_argument("--serper-location", default=None, help="Serper place name of the city, for --provider serper")
    _add_reader_version(read)
    _add_paid(read)
    read.set_defaults(handler=cmd_read)
    rnd = sub.add_parser("read-round", parents=[common],
                         help="ONE round of pulls, stored for assemble (paid, dry run by default)")
    _add_request_input(rnd)
    rnd.add_argument("--provider", choices=pipeline.PROVIDERS, default="dataforseo")
    rnd.add_argument("--serper-location", default=None, help="Serper place name of the city, for --provider serper")
    _add_paid(rnd)
    rnd.set_defaults(handler=cmd_read_round)
    asm = sub.add_parser("assemble", parents=[common],
                         help="build and store the readings of one UTC date from its stored rounds (free)")
    asm.add_argument("--date", type=_iso_date, required=True, help="the UTC date whose stored pulls make the readings")
    _add_request_input(asm)
    _add_reader_version(asm)
    asm.set_defaults(handler=cmd_assemble)


def _register_marker1(sub: Any, common: argparse.ArgumentParser) -> None:
    m1 = sub.add_parser("marker1", help="marker 1: the clean reader read twice a day apart")
    inner = m1.add_subparsers(dest="marker1_command", required=True)
    sample = inner.add_parser("sample", parents=[common], help="draw and freeze the search sample (free)")
    sample.add_argument("--candidates", required=True, help="a text file with one candidate search per line")
    sample.add_argument("--strata", default=None, help="optional CSV with columns search and stratum")
    sample.add_argument("--n", type=int, default=pipeline.MARKER1_SEARCHES)
    sample.add_argument("--seed", type=int, default=pipeline.MARKER1_SAMPLE_SEED)
    sample.add_argument("--name", default=pipeline.MARKER1_FREEZE_NAME)
    sample.add_argument("--out", required=True, help="where to write the searches, one per line")
    sample.set_defaults(handler=cmd_marker1_sample)
    rep = inner.add_parser("report", parents=[common], help="pair readings A and B and judge marker 1 (free)")
    rep.add_argument("--searches", default=None, help="default: the frozen sample in the scoreboard")
    rep.add_argument("--name", default=pipeline.MARKER1_FREEZE_NAME)
    rep.add_argument("--location-code", type=int, required=True)
    rep.add_argument("--device", default=DEFAULT_DEVICE)
    rep.add_argument("--a-date", type=_iso_date, default=None)
    rep.add_argument("--b-date", type=_iso_date, default=None)
    _add_reader_version(rep)
    rep.set_defaults(handler=cmd_marker1_report)


def _register_grading(sub: Any, common: argparse.ArgumentParser) -> None:
    grade = sub.add_parser("grade", parents=[common], help="grade forecasts whose day 90 outcome is due")
    grade.add_argument("--date", type=_iso_date, required=True, help="the UTC date to grade as of")
    grade.set_defaults(handler=cmd_grade)
    rep = sub.add_parser("report", parents=[common], help="the stage report for one source and stage")
    rep.add_argument("--source", choices=SOURCES, required=True)
    rep.add_argument("--stage", choices=tuple(STAGE_DAYS), required=True)
    rep.add_argument("--as-of", type=_iso_date, default=None)
    rep.add_argument("--model-version", default=None)
    rep.set_defaults(handler=cmd_report)
    marker = sub.add_parser("marker", parents=[common],
                            help="judge marker 2 on the newest trust report, or marker 3, 4 or 5 on the scoreboard")
    marker.add_argument("--number", type=int, choices=(1, 2, 3, 4, 5), required=True)
    marker.add_argument("--source", choices=SOURCES, default=None, help="needed for markers 3, 4 and 5")
    marker.add_argument("--as-of", type=_iso_date, default=None)
    marker.add_argument("--model-version", default=None)
    marker.set_defaults(handler=cmd_marker)


def _register_chain(sub: Any, common: argparse.ArgumentParser) -> None:
    export = sub.add_parser("export", parents=[common], help="write the scoreboard as JSONL for git")
    export.add_argument("--out", default=None, help=f"default {DEFAULT_EXPORT_DIR}, or RANKSIM_EXPORT_DIR")
    export.set_defaults(handler=cmd_export)
    imp = sub.add_parser("import", parents=[common], help="rebuild an empty scoreboard from an export")
    imp.add_argument("--from", dest="source_dir", default=None, help=f"default {DEFAULT_EXPORT_DIR}, or RANKSIM_EXPORT_DIR")
    imp.set_defaults(handler=cmd_import)
    ver = sub.add_parser("verify", parents=[common], help="check the chains, the ledger and the committed export")
    ver.add_argument("--against", default=None,
                     help=f"an export folder (default {DEFAULT_EXPORT_DIR}, or RANKSIM_EXPORT_DIR, when it exists)")
    ver.add_argument("--no-export", action="store_true", help="do not compare an export")
    ver.set_defaults(handler=cmd_verify)


def _register_imports(sub: Any, common: argparse.ArgumentParser) -> None:
    gsc = sub.add_parser("gsc", help="Search Console exports")
    gsc_inner = gsc.add_subparsers(dest="gsc_command", required=True)
    gimp = gsc_inner.add_parser("import", parents=[common], help="check, store and freeze one export")
    gimp.add_argument("--site", required=True)
    gimp.add_argument("--file", required=True)
    gimp.add_argument("--kind", choices=pipeline.GSC_KINDS, required=True,
                      help="ui (zip or folder), daily, range, api (JSON) or index (indexing table)")
    gimp.add_argument("--period-start", type=_iso_date, default=None)
    gimp.add_argument("--period-end", type=_iso_date, default=None)
    gimp.add_argument("--dimensions", default=None, help="for --kind api: the request's dimensions in order, for example date,query")
    gimp.set_defaults(handler=cmd_gsc_import)
    form = sub.add_parser("form", help="the agency form")
    form_inner = form.add_subparsers(dest="form_command", required=True)
    template = form_inner.add_parser("template", parents=[common], help="write a blank form")
    template.add_argument("--out", required=True)
    template.set_defaults(handler=cmd_form_template)
    validate = form_inner.add_parser("validate", parents=[common], help="check a filled form")
    validate.add_argument("--file", required=True)
    validate.set_defaults(handler=cmd_form_validate)
    fimp = form_inner.add_parser("import", parents=[common], help="check, store and freeze a filled form")
    fimp.add_argument("--file", required=True)
    fimp.set_defaults(handler=cmd_form_import)


def _register_demo(sub: Any, common: argparse.ArgumentParser) -> None:
    demo = sub.add_parser("demo", parents=[common], help="the whole measuring flow on a synthetic world (free, SYNTHETIC)")
    demo.add_argument("--seed", type=int, default=pipeline.DEMO_SEED)
    demo.add_argument("--sites", type=int, default=pipeline.DEMO_SITES)
    demo.add_argument("--workdir", default=None, help="a fresh folder for the demo files (default a new temporary folder)")
    demo.set_defaults(handler=cmd_demo)


REGISTRARS: tuple[Callable[[Any, argparse.ArgumentParser], None], ...] = (
    _register_status,
    _register_budget,
    _register_sample,
    _register_locations,
    _register_seeds,
    _register_read,
    _register_marker1,
    _register_grading,
    _register_chain,
    _register_imports,
    _register_demo,
)


# Command groups that live in their own modules. Each module exports REGISTRARS, a tuple of
# functions with the same signature as the ones above, and imports this module for Context and
# the shared helpers. They are loaded here, when the parser is built, so the import is one way.
PLUGIN_MODULES: tuple[str, ...] = ("cli_forecasting", "cli_data")


def _plugin_registrars() -> tuple[Callable[[Any, argparse.ArgumentParser], None], ...]:
    found: list[Callable[[Any, argparse.ArgumentParser], None]] = []
    for name in PLUGIN_MODULES:
        full = f"{__package__}.{name}"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError as error:
            if error.name != full:  # the module exists and one of its own imports is missing: say so
                raise
            continue
        found.extend(module.REGISTRARS)
    return tuple(found)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="Ranksim forecast: the commands of the build plan.")
    sub = parser.add_subparsers(dest="command", required=True)
    common = _common()
    for register in (*REGISTRARS, *_plugin_registrars()):
        register(sub, common)
    return parser


# ----------------------------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------------------------


def _default_settings() -> Any:
    from aeo.config import get_settings

    return get_settings()


def main(
    argv: list[str] | None = None,
    *,
    http_client: httpx.Client | None = None,
    clock: pipeline.Clock | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    settings_factory: Callable[[], Any] | None = None,
) -> int:
    """Run one forecast command. Returns the exit code: 0 ok, 1 a check failed, 2 usage or configuration."""
    factory = settings_factory or _default_settings
    raw_out = stdout if stdout is not None else sys.stdout
    raw_err = stderr if stderr is not None else sys.stderr
    try:
        secrets = pipeline.secret_values(factory())
    except Exception:  # unreadable settings: nothing to scrub, the command will report the problem
        secrets = []
    out = ScrubbedStream(raw_out, secrets)
    err = ScrubbedStream(raw_err, secrets)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0) if isinstance(stop.code, int) or stop.code is None else EXIT_USAGE
    ctx = Context(
        paths=forecast_paths(getattr(args, "dir", None)),
        as_json=bool(getattr(args, "json", False)),
        out=out,  # type: ignore[arg-type]
        err=err,  # type: ignore[arg-type]
        clock=clock or pipeline.SystemClock(),
        settings_factory=factory,
        http_client=http_client,
    )
    try:
        return int(args.handler(ctx, args))
    except BudgetError as error:
        message = str(error)
        if "budget approve" not in message:
            message += " " + pipeline.APPROVE_HINT
        return ctx.fail(message)
    except ChainBroken as error:
        return ctx.fail(str(error), EXIT_CHECK_FAILED)
    except (ProviderConfigError, ProviderError, ScoreboardRefusal, ScoreboardError, ManifestError, FileNotFoundError,
            FileExistsError, ValueError) as error:
        return ctx.fail(str(error))
    finally:
        ctx.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
