"""The measuring pipeline: plain functions the `forecast` commands call.

Each function here is usable from Python with an injected clock and an injected
result-page source, so the whole flow runs in tests with no network, no keys
and no real sleeping.

What is REAL here: pulls and clean readings from a paid provider (DataForSEO
or Serper), the marker 1 agreement numbers computed from them, and grades of
stored forecasts against later clean readings. What is PREDICTED here:
forecasts. `demo` makes them through forecasting.py, the same live path the
upload and update commands use; `persistence_forecast` (model version
"persistence-v0") is kept as a placeholder helper and is no model. What is
SYNTHETIC: everything `demo` produces, and
every reading taken with the synthetic source. The reader labels a reading
built from synthetic pulls SYNTHETIC, and such readings are never accuracy.

Readings come two ways: `take_readings` runs every round of pulls in one
process, and round mode spreads them over a day (`read_round` once per round,
then `assemble` for the UTC date). Both pass each search's latest earlier clean
reading as history and read the round suspects per round.

Money: every amount is computed by `budget.py` from its price table, and every
paid call goes through the spend ledger. The `plan_*` functions only read the
ledger. They never write to it and never send anything.

Missing values stay None. Times are UTC.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import time as time_module
import zipfile
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

import httpx
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from aeo.errors import ProviderConfigError
from aeo.ranksim.site_history import site_key

from . import grade_job
from . import markers as markers_module
from . import report as report_module
from . import seeds as seeds_module
from .agency_form import load_form
from .asof import parse_utc, utc_iso
from .budget import (
    KIND_BULK_TRAFFIC,
    KIND_HISTORICAL_SERPS,
    KIND_HISTORICAL_TRAFFIC,
    KIND_RANKED_KEYWORDS,
    KIND_SERP_STANDARD_NORMAL,
    KIND_SERPER_SEARCH,
    LABS_MAX_TARGETS_PER_TASK,
    SAMPLE_MAX_USD,
    SpendLedger,
    historical_serps_estimate,
    historical_serps_estimate_lines,
    historical_traffic_cost,
    ranked_keywords_cost,
    serp_cost,
    serper_cost,
    to_micros,
    usd_text,
)
from .chain import canonical_json, sha256_bytes, sha256_text
from .config import (
    DEFAULT_DEVICE,
    DEFAULT_LANGUAGE,
    DEFAULT_PULLS,
    EXCLUDED_SITES,
    HISTORY_MAX_AGE_DAYS,
    MIN_PULL_GAP_SECONDS,
    NT50,
    ROUND_GAP_MINUTES,
    STAGE_DAYS,
    TOP_DEPTH,
    ForecastPaths,
    forecast_paths,
)
from .noise import (
    MARKER1_SAMPLE_SEED,
    MARKER1_SEARCHES,
    MAX_GAP_HOURS,
    MIN_GAP_HOURS,
    SearchSample,
    agreement_report,
    marker1_report_from_pulls,
    sample_marker1_searches,
)
from .reader import (
    DEFAULT_RULE_VERSION,
    RoundFailed,
    RoundSuspects,
    assemble_readings,
    fetch_round,
    read_clean,
    reader_settings,
    reader_version_name,
    reading_label,
    round_suspects,
)
from .scoreboard import (
    EXPORT_FILES,
    READING_PROVENANCE_COLUMNS,
    ChainStatus,
    CoreUpdateRow,
    ForecastRow,
    FreezeRow,
    GradeRow,
    ModelVersionRow,
    PullRow,
    ReadingPositionRow,
    ReadingRow,
    Scoreboard,
    ScoreboardRefusal,
    SearchRow,
    SiteRow,
    detect_code_commit,
    search_key,
)
from .seeds import SeedManifest, SeedSearch, SeedSite
from .types import REAL, SYNTHETIC, CleanReading, Forecast, Pull, PullSpec, SerpSource, provider_is_synthetic

PROVIDERS = ("dataforseo", "serper", "synthetic")
PAID_PROVIDERS = ("dataforseo", "serper")
DATAFORSEO_ENV = ("DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD")
SERPER_ENV = "SERPER_API_KEY"
DATAFORSEO_MODE = "standard_normal"
DATAFORSEO_POSTS_PER_CALL = 100  # dataforseo.MAX_TASKS_PER_POST
MARKER1_FREEZE_NAME = "marker1.sample"
STAGE_MARKERS: dict[str, int] = {"upload": 3, "d30": 4, "d60": 5}
IMPORTS_DIR = "imports"  # kept for older imports; the folder is ForecastPaths.imports
ROUND_FREEZE_PREFIX = "read_round."
APPROVE_HINT = (
    "To approve an amount, run: python -m aeo.ranksim forecast budget approve "
    "--amount <USD> --by <your name> --note <what it is for>"
)
SAMPLE_COMMANDS: dict[str, str] = {
    KIND_SERP_STANDARD_NORMAL: "python -m aeo.ranksim forecast sample serp",
    KIND_SERPER_SEARCH: "python -m aeo.ranksim forecast sample serp --provider serper",
    KIND_HISTORICAL_SERPS: "python -m aeo.ranksim forecast sample historical-serps",
    KIND_HISTORICAL_TRAFFIC: "python -m aeo.ranksim forecast sample traffic",
    KIND_RANKED_KEYWORDS: "python -m aeo.ranksim forecast sample ranked-keywords",
    KIND_BULK_TRAFFIC: "python -m aeo.ranksim forecast sample traffic",
}

# The persistence placeholder of the demo. PREDICTED, and only a placeholder.
PERSISTENCE_MODEL = "persistence-v0"
PERSISTENCE_DESCRIPTION = (
    "Persistence placeholder: the point is the site's position in the latest clean reading on or before as_of, "
    "NT50 when there is none. Not a model. It exists so the scoreboard path runs before the real models exist."
)
PERSISTENCE_RANGE = 2

# The demo world. SYNTHETIC. Small so the whole flow runs in a few seconds.
DEMO_SEED = 3
DEMO_SITES = 3
DEMO_SEARCHES_PER_NICHE = 12
DEMO_ESTABLISHED_SITES = 40
DEMO_TARGETS_PER_SITE = 6
DEMO_READING_DAYS = (0, 7, 14, 30, 60, 90)
DEMO_READ_HOUR = 12
DEMO_FORECAST_HOUR = 13
DEMO_MARKER1_DAYS_BEFORE_LAUNCH = 7
DEMO_DIFFICULTY_METRIC = "synthetic difficulty (true score at position 10 on the first day)"
DEMO_TRAIN_POOL = 37  # more new sites in the same world; those whose day 90 is before the first demo launch train the bundle
DEMO_BUNDLE = "demo-synthetic"


# ----------------------------------------------------------------------------------------------
# Clocks
# ----------------------------------------------------------------------------------------------


class Clock(Protocol):
    """What the pipeline reads time from. `sleep` is the only way it waits."""

    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """The wall clock in UTC and real sleeping. Used only by real runs."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time_module.sleep(seconds)


class VirtualClock:
    """A clock that only moves when told to. `sleep` moves it forward and returns at once."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("a clock time must be an aware UTC datetime")
        self._now = start.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot sleep a negative time")
        self._now = self._now + timedelta(seconds=seconds)

    def set(self, moment: datetime) -> None:
        """Move to a later moment. Moving back is refused: the scoreboard refuses backdated rows."""
        if moment.tzinfo is None:
            raise ValueError("a clock time must be an aware UTC datetime")
        moment = moment.astimezone(timezone.utc)
        if moment < self._now:
            raise ValueError(f"the clock cannot move back from {utc_iso(self._now)} to {utc_iso(moment)}")
        self._now = moment


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=timezone.utc)


# ----------------------------------------------------------------------------------------------
# Opening the scoreboard, the ledger and a source
# ----------------------------------------------------------------------------------------------


DEFAULT_EXPORT_DIR = "evals/scoreboard"
EXPORT_DIR_ENV = "RANKSIM_EXPORT_DIR"
_DEFAULT = object()  # "use the default export folder"


def default_export_dir() -> Path:
    """The committed export folder: RANKSIM_EXPORT_DIR when set, else evals/scoreboard (relative to the working folder)."""
    return Path(os.environ.get(EXPORT_DIR_ENV) or DEFAULT_EXPORT_DIR)


def heads_anchor_for(export_dir: str | Path | None | object = _DEFAULT) -> Path | None:
    """The export folder to anchor the scoreboard to, or None when there is no committed export there yet.

    An export folder counts only when it holds HEADS.json. `export_dir=None` means: no anchor.
    """
    if export_dir is None:
        return None
    folder = default_export_dir() if export_dir is _DEFAULT else Path(export_dir)  # type: ignore[arg-type]
    return folder if (folder / "HEADS.json").exists() else None


def open_scoreboard(
    paths: ForecastPaths, *, clock: Clock | None = None, export_dir: str | Path | None | object = _DEFAULT
) -> Scoreboard:
    """The scoreboard at paths.scoreboard_url (RANKSIM_SCOREBOARD_URL wins), stamped with the checked-out commit.

    When the committed export folder (`export_dir`, default `default_export_dir()`) holds HEADS.json,
    it is passed as the scoreboard's heads_anchor: every verify, report and export then checks that the
    committed head hashes are still a prefix of the database, which catches rows cut off its end.
    Pass export_dir=None to open without an anchor.
    """
    chosen = clock or SystemClock()
    return Scoreboard(paths.scoreboard_url, clock=chosen.now, code_commit=detect_code_commit(),
                      heads_anchor=heads_anchor_for(export_dir))


def open_ledger(paths: ForecastPaths, *, clock: Clock | None = None) -> SpendLedger:
    """The spend ledger at paths.spend. Opening it writes nothing."""
    chosen = clock or SystemClock()
    return SpendLedger(paths.spend, now=chosen.now)


def make_source(
    provider: str,
    settings: Any,
    ledger: SpendLedger | None,
    raw_dir: str | Path,
    *,
    world: Any = None,
    clock: Clock | None = None,
    http_client: httpx.Client | None = None,
    serper_locations: Mapping[int, str] | None = None,
) -> SerpSource:
    """A result-page source.

    dataforseo: Standard queue at normal priority, the repair option on (resolve_poisoned_results),
    depth and city come from each PullSpec. serper: the backup, with `serper_locations` mapping each
    location code to Serper's place name. synthetic: the simulated world, free. Credentials are read
    from `settings` (aeo.config.get_settings()) and are never printed or stored by this module.
    """
    chosen = clock or SystemClock()
    if provider == "dataforseo":
        from .dataforseo import DataForSeoClient, DataForSeoSerpSource

        login = getattr(settings, "dataforseo_login", None)
        password = getattr(settings, "dataforseo_password", None)
        if not login or not password:
            raise ProviderConfigError(
                f"DataForSEO needs a login. Set {DATAFORSEO_ENV[0]} and {DATAFORSEO_ENV[1]} in the environment, "
                "then run the command again. Their values are never printed."
            )
        client = DataForSeoClient(
            login,
            password,
            client=http_client,
            ledger=ledger,
            raw_dir=raw_dir,
            sleep=chosen.sleep,
            now=chosen.now,
        )
        return DataForSeoSerpSource(client, mode=DATAFORSEO_MODE, resolve_poisoned_results=True)
    if provider == "serper":
        from .serper import SerperSerpSource

        key = getattr(settings, "serper_api_key", None)
        if not key:
            raise ProviderConfigError(f"Serper needs a key. Set {SERPER_ENV} in the environment. Its value is never printed.")
        if not serper_locations:
            raise ProviderConfigError(
                "Serper needs the place name of the pinned city, for example \"Austin,Texas,United States\". "
                "Pass it with --serper-location."
            )
        return SerperSerpSource(
            key,
            locations=dict(serper_locations),
            client=http_client,
            ledger=ledger,
            raw_dir=raw_dir,
            sleep=chosen.sleep,
            now=chosen.now,
        )
    if provider == "synthetic":
        from .synth import SyntheticSource

        if world is None:
            raise ValueError("The synthetic source needs a synthetic World.")
        return SyntheticSource(world, chosen.now)
    raise ValueError(f"Unknown provider {provider!r}. Choose one of {', '.join(PROVIDERS)}.")


def credentials_present(settings: Any) -> dict[str, bool]:
    """Which provider credentials are set. Only yes or no, never a value."""
    return {
        "dataforseo": bool(getattr(settings, "dataforseo_login", None) and getattr(settings, "dataforseo_password", None)),
        "serper": bool(getattr(settings, "serper_api_key", None)),
    }


def secret_values(settings: Any) -> list[str]:
    """Credential values to scrub from any text before it is printed."""
    out = []
    for name in ("dataforseo_login", "dataforseo_password", "serper_api_key"):
        value = getattr(settings, name, None)
        if isinstance(value, str) and len(value.strip()) >= 4:
            out.append(value.strip())
    return sorted(set(out), key=len, reverse=True)


# ----------------------------------------------------------------------------------------------
# Costs and the budget guard (read only)
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CostPlan:
    """What a paid command would cost, from budget.py, and what the budget guard would say now."""

    provider: str
    item: str
    kind: str | None
    calls: int
    call_unit: str
    usd: Decimal
    approved_usd: Decimal | None
    spent_usd: Decimal | None
    remaining_usd: Decimal | None
    sample_recorded: bool | None
    refusal: str | None
    notes: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.refusal is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "item": self.item,
            "kind": self.kind,
            "calls": self.calls,
            "call_unit": self.call_unit,
            "usd": str(self.usd),
            "usd_text": usd_text(self.usd),
            "approved_usd": None if self.approved_usd is None else str(self.approved_usd),
            "spent_usd": None if self.spent_usd is None else str(self.spent_usd),
            "remaining_usd": None if self.remaining_usd is None else str(self.remaining_usd),
            "sample_recorded": self.sample_recorded,
            "guard_would_allow": self.allowed,
            "refusal": self.refusal,
            "notes": list(self.notes),
        }


def guard_refusal(ledger: SpendLedger, kind: str, usd: Decimal, item: str) -> str | None:
    """What SpendLedger.reserve would refuse for `usd` of `kind` right now, or None. Writes nothing."""
    state = ledger.state()
    if not state.ok:
        return f"The spend ledger cannot be trusted: {state.problem}. Nothing can be spent until it is repaired."
    micros = to_micros(usd)
    if micros > state.remaining:
        return (
            f"Refused by the budget guard: {item} needs {usd_text(micros)}, but only {usd_text(state.remaining)} "
            f"of the {usd_text(state.approved)} approved is left. {APPROVE_HINT}"
        )
    kind_after = state.kind_spent.get(kind, 0) + micros
    if kind not in state.samples and kind_after > to_micros(SAMPLE_MAX_USD):
        command = SAMPLE_COMMANDS.get(kind, "the $1 sample for this kind")
        return (
            f"Refused by the budget guard: {kind} has no recorded $1 sample yet, and {item} would bring its spend to "
            f"{usd_text(kind_after)}, above the {usd_text(SAMPLE_MAX_USD)} sample cap. Run {command} first."
        )
    return None


def _plan(
    ledger: SpendLedger | None,
    *,
    provider: str,
    item: str,
    kind: str | None,
    calls: int,
    call_unit: str,
    usd: Decimal,
    notes: Sequence[str] = (),
    sample: bool = False,
) -> CostPlan:
    if kind is None or usd <= 0:
        return CostPlan(provider, item, kind, calls, call_unit, Decimal(0) if usd <= 0 else usd, None, None, None, None, None, tuple(notes))
    if ledger is None:
        return CostPlan(provider, item, kind, calls, call_unit, usd, None, None, None, None,
                        "No spend ledger is open. Nothing can be spent.", tuple(notes))
    state = ledger.state()
    if not state.ok:
        refusal = f"The spend ledger cannot be trusted: {state.problem}. Nothing can be spent until it is repaired."
        return CostPlan(provider, item, kind, calls, call_unit, usd, None, None, None, None, refusal, tuple(notes))
    recorded = kind in state.samples
    refusal: str | None
    if sample and recorded:
        refusal = f"The $1 sample for {kind} is already recorded. Read it with: python -m aeo.ranksim forecast budget show"
    else:
        refusal = guard_refusal(ledger, kind, usd, item)
    return CostPlan(
        provider=provider,
        item=item,
        kind=kind,
        calls=calls,
        call_unit=call_unit,
        usd=usd,
        approved_usd=ledger.approved_total,
        spent_usd=ledger.spent_total,
        remaining_usd=ledger.remaining,
        sample_recorded=recorded,
        refusal=refusal,
        notes=tuple(notes),
    )


def plan_reading_cost(
    ledger: SpendLedger | None,
    provider: str,
    searches: int,
    *,
    pulls: int = DEFAULT_PULLS,
    depth: int = TOP_DEPTH,
    item: str | None = None,
    specs: Sequence[PullSpec] | None = None,
) -> CostPlan:
    """The cost of one reading run: `searches` searches read `pulls` times at `depth`.

    With `specs` (the PullSpecs that will be sent; their count must equal `searches`) the DataForSEO
    figure is the client's own estimate per spec: each spec's depth, and five times the pages for a
    search holding a Google operator (site:, intitle: ...). That is the amount --execute checks and
    reserves. Without specs the operator charge cannot be known and is not added.
    """
    if searches < 0 or pulls < 1 or depth < 1:
        raise ValueError("searches must be 0 or more, pulls and depth at least 1")
    if specs is not None and len(specs) != searches:
        raise ValueError(f"{len(specs)} specs were given for {searches} searches")
    item = item or f"a reading of {searches} searches, {pulls} pulls each, depth {depth}"
    if provider == "dataforseo":
        tasks = searches * pulls
        posts = pulls * math.ceil(searches / DATAFORSEO_POSTS_PER_CALL) if searches else 0
        if specs is not None:
            usd = sum((_dataforseo_task_cost(spec) * pulls for spec in specs), Decimal(0))
        else:
            usd = serp_cost(depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True, pulls=tasks)
        operators = sum(1 for spec in specs or () if _dataforseo_task_cost(spec) != serp_cost(
            spec.depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True))
        notes = (
            f"Standard queue, normal priority, the repair option on. {posts} task_post calls carry the {tasks} tasks.",
            "The provider's own cost field settles each call in the ledger.",
            *((f"{operators} searches hold a Google operator, so they are charged five times the pages.",) if operators else ()),
        )
        return _plan(ledger, provider=provider, item=item, kind=KIND_SERP_STANDARD_NORMAL, calls=tasks,
                     call_unit="SERP tasks", usd=usd, notes=notes)
    if provider == "serper":
        from .serper import CREDITS_PER_PAGE_ESTIMATE, serper_pages

        pages = serper_pages(depth)
        if specs is not None:
            requests = pulls * sum(serper_pages(spec.depth) for spec in specs)
        else:
            requests = searches * pulls * pages
        usd = serper_cost(requests * CREDITS_PER_PAGE_ESTIMATE)
        notes = (f"{pages} pages of 10 per pull. Serper has no repair option for poisoned pages.",)
        return _plan(ledger, provider=provider, item=item, kind=KIND_SERPER_SEARCH, calls=requests,
                     call_unit="Serper requests", usd=usd, notes=notes)
    if provider == "synthetic":
        return _plan(None, provider=provider, item=item, kind=None, calls=searches * pulls, call_unit="synthetic pulls",
                     usd=Decimal(0), notes=("SYNTHETIC. Free.",))
    raise ValueError(f"Unknown provider {provider!r}. Choose one of {', '.join(PROVIDERS)}.")


def _dataforseo_task_cost(spec: PullSpec) -> Decimal:
    """One DataForSEO task for `spec`, as DataForSeoClient estimates, checks and reserves it (x5 for an operator)."""
    return serp_cost(spec.depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True, keyword=spec.search)


def plan_round_cost(
    ledger: SpendLedger | None,
    provider: str,
    searches: int,
    *,
    depth: int = TOP_DEPTH,
    specs: Sequence[PullSpec] | None = None,
) -> CostPlan:
    """The cost of ONE round of pulls (round mode): one pull of each of `searches` searches at `depth`."""
    return plan_reading_cost(ledger, provider, searches, pulls=1, depth=depth, specs=specs,
                             item=f"one round of {searches} searches, one pull each, depth {depth}")


def plan_sample_serp(
    ledger: SpendLedger | None,
    provider: str,
    searches: int,
    *,
    depth: int = TOP_DEPTH,
    specs: Sequence[PullSpec] | None = None,
) -> CostPlan:
    """The $1 SERP sample: as many single pulls as fit under the cap, the way the client picks them.

    With `specs`, the pulls are taken in order and each costs what the client estimates for it (five
    times the pages for a search holding a Google operator), stopping at the first that does not fit,
    exactly as DataForSeoClient.sample_serp does.
    """
    if provider == "dataforseo":
        each = serp_cost(depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True)
        costs = [_dataforseo_task_cost(spec) for spec in specs] if specs is not None else None
        kind = KIND_SERP_STANDARD_NORMAL
        unit = "SERP tasks"
    elif provider == "serper":
        from .serper import CREDITS_PER_PAGE_ESTIMATE, serper_pages

        each = serper_cost(serper_pages(depth) * CREDITS_PER_PAGE_ESTIMATE)
        costs = [serper_cost(serper_pages(spec.depth) * CREDITS_PER_PAGE_ESTIMATE) for spec in specs] if specs is not None else None
        kind = KIND_SERPER_SEARCH
        unit = "Serper pulls"
    else:
        raise ValueError("The SERP sample runs on dataforseo or serper.")
    if costs is not None:
        searches = len(costs)
        fit, usd = 0, Decimal(0)
        for cost in costs:
            if usd + cost > SAMPLE_MAX_USD:
                break
            usd += cost
            fit += 1
    else:
        fit = _fit_under_cap(searches, each)
        usd = each * fit
    return _plan(ledger, provider=provider, item=f"the SERP sample ({fit} pulls at depth {depth})", kind=kind,
                 calls=fit, call_unit=unit, usd=usd, sample=True,
                 notes=(f"Searches beyond the first {fit} do not fit under the {usd_text(SAMPLE_MAX_USD)} cap.",) if fit < searches else ())


def plan_sample_historical_serps(ledger: SpendLedger | None, keywords: int, *, months_back: int) -> CostPlan:
    """The $1 Historical SERPs sample, at the plan rate of budget.py (1 page per search per month). The notes give both rates."""
    per_call = historical_serps_estimate(months_back + 1)
    fit = _fit_under_cap(keywords, per_call)
    notes = (
        "Billing is per page (snapshot) returned. The provider's cost field settles it.",
        *(f"This sample at the {line[0].lower()}{line[1:]}" if index < 2 else line
          for index, line in enumerate(historical_serps_estimate_lines(months_back + 1, fit))),
        f"A refused start date is retried once with the default window, still under the {usd_text(SAMPLE_MAX_USD)} cap.",
    )
    return _plan(ledger, provider="dataforseo", item=f"the Historical SERPs sample ({fit} keywords, {months_back} months)",
                 kind=KIND_HISTORICAL_SERPS, calls=fit, call_unit="Labs calls", usd=per_call * fit, notes=notes, sample=True)


def plan_sample_traffic(ledger: SpendLedger | None, sites: int) -> CostPlan:
    """The $1 Historical Bulk Traffic Estimation sample: as many sites as fit under the cap in one task."""
    fit = 0
    while fit < min(sites, LABS_MAX_TARGETS_PER_TASK) and historical_traffic_cost(fit + 1) <= SAMPLE_MAX_USD:
        fit += 1
    usd = historical_traffic_cost(fit) if fit else Decimal(0)
    return _plan(ledger, provider="dataforseo", item=f"the traffic history sample ({fit} sites)", kind=KIND_HISTORICAL_TRAFFIC,
                 calls=1 if fit else 0, call_unit="Labs calls", usd=usd, sample=True)


def plan_sample_ranked_keywords(ledger: SpendLedger | None, targets: int, *, limit: int) -> CostPlan:
    """The $1 ranked keywords sample at `limit` rows per site."""
    per_call = ranked_keywords_cost(1, limit)
    fit = _fit_under_cap(targets, per_call)
    return _plan(ledger, provider="dataforseo", item=f"the ranked keywords sample ({fit} sites, up to {limit} rows each)",
                 kind=KIND_RANKED_KEYWORDS, calls=fit, call_unit="Labs calls", usd=per_call * fit, sample=True,
                 notes=("The estimate charges every row of the limit. Fewer rows cost less.",))


def _fit_under_cap(count: int, each: Decimal) -> int:
    fit = 0
    total = Decimal(0)
    while fit < count and total + each <= SAMPLE_MAX_USD:
        total += each
        fit += 1
    return fit


# ----------------------------------------------------------------------------------------------
# Seed sites
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterResult:
    manifest_hash: str
    freeze_name: str
    sites_added: int
    sites_already: int
    searches_added: int
    searches_already: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def manifest_specs(manifest: SeedManifest, *, depth: int = TOP_DEPTH) -> list[PullSpec]:
    """One PullSpec per distinct search of the manifest (searches shared by sites are read once)."""
    seen: set[str] = set()
    specs: list[PullSpec] = []
    for site in sorted(manifest.sites, key=lambda item: site_key(item.domain)):
        for item in site.searches:
            key = search_key(item.search)
            if key in seen:
                continue
            seen.add(key)
            specs.append(PullSpec(search=" ".join(item.search.split()), location_code=int(manifest.location_code),
                                  language_code=manifest.language, device=manifest.device, depth=depth))
    return specs


def search_specs(
    searches: Iterable[str],
    location_code: int,
    *,
    language: str = DEFAULT_LANGUAGE,
    device: str = DEFAULT_DEVICE,
    depth: int = TOP_DEPTH,
) -> list[PullSpec]:
    """PullSpecs for a list of searches, one per distinct search."""
    if isinstance(location_code, bool) or not isinstance(location_code, int) or location_code <= 0:
        raise ValueError("location_code must be the positive integer code of the pinned city")
    seen: set[str] = set()
    out: list[PullSpec] = []
    for raw in searches:
        textual = " ".join(str(raw).split())
        key = search_key(textual)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(PullSpec(search=textual, location_code=location_code, language_code=language, device=device, depth=depth))
    if not out:
        raise ValueError("no searches were given")
    return out


def read_search_file(path: str | Path) -> list[str]:
    """Searches from a text file, one per line. Blank lines and lines starting with # are skipped."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


FORM_FREEZE_PREFIX = "form."


def form_brand_terms(scoreboard: Scoreboard, site: str, *, paths: ForecastPaths | None = None) -> tuple[str, ...] | None:
    """The brand terms of every agency form imported for `site`, joined. None when no form was imported.

    The terms are read from the form's freeze record, or, for a form frozen before the record held
    them, from the stored copy under `paths` (its sha256 must still match the frozen one).
    """
    from .agency_form import brand_terms as form_terms

    name = site_key(site)
    found = False
    terms: set[str] = set()
    for row in scoreboard.freezes():
        if not str(row["name"]).startswith(f"{FORM_FREEZE_PREFIX}{name}."):
            continue
        payload = row["payload"] or {}
        if payload.get("kind") != "agency_form" or payload.get("site") != name:
            continue
        found = True
        if isinstance(payload.get("brand_terms"), list):
            terms.update(str(term) for term in payload["brand_terms"])
            continue
        if paths is None:
            raise ScoreboardRefusal(
                f"The agency form {row['name']} for {name} was frozen without its brand terms, and no data folder was "
                "given to read the stored copy from."
            )
        stored = paths.root / str(payload["stored_path"])
        if _file_digest(stored) != payload.get("sha256"):
            raise ScoreboardRefusal(f"The stored copy {stored} of agency form {row['name']} no longer matches its frozen sha256.")
        terms.update(form_terms(load_form(stored)))
    return tuple(sorted(terms)) if found else None


def register_manifest(
    scoreboard: Scoreboard,
    manifest: SeedManifest,
    *,
    source: str = "seed",
    paths: ForecastPaths | None = None,
    today: date | None = None,
) -> RegisterResult:
    """Add the manifest's sites and searches to the scoreboard and freeze the manifest hash there.

    Sites get their launch date with the manifest hash as its source. Searches get their tercile,
    difficulty value and target URL. Brand flags: a site with an imported agency form gets the form's
    brand terms (agency_form.brand_terms), so a search naming the brand is flagged and kept out of the
    headline. A pilot site must have its form imported first (`form import`), because a pilot's brand
    usually differs from its domain; a seed site without a form falls back to its domain name.

    A site that is not on the scoreboard yet is refused when `today` (default: the scoreboard clock's
    UTC date) is on or after its launch date, unless the manifest records an after-launch reason
    (SeedManifest.after_launch_reason, for a backfilled pilot): a list picked after launch can be
    chosen from early rankings. Running it again with the same manifest adds nothing. A different
    manifest for a site that is already registered is refused.
    """
    check = manifest.validate()
    if not check.ok:
        raise ScoreboardRefusal("The manifest has errors and cannot be registered: " + "; ".join(check.errors))
    digest = manifest.manifest_hash()
    freeze_name = f"seed_manifest.{digest[:16]}"
    existing_keys = _registered_search_keys(scoreboard)
    sites_added = sites_already = searches_added = searches_already = 0
    known_sites = _registered_sites(scoreboard)
    day = today if today is not None else scoreboard._now().date()
    reason = getattr(manifest, "after_launch_reason", None)
    terms_by_site: dict[str, tuple[str, ...]] = {}
    for site in manifest.sites:
        name = site_key(site.domain)
        if site.launch_date is None:
            raise ScoreboardRefusal(f"{site.domain} has no launch date.")
        if name not in known_sites and day >= site.launch_date and not reason:
            raise ScoreboardRefusal(
                f"{name} launched on {site.launch_date.isoformat()} and today is {day.isoformat()}. Its searches must be "
                "frozen and registered before launch, or they could be chosen from early rankings. For a backfilled pilot, "
                "freeze the manifest with an after-launch reason."
            )
        terms = form_brand_terms(scoreboard, name, paths=paths)
        if terms is None and source == "pilot":
            raise ScoreboardRefusal(
                f"No agency form is imported for the pilot {name}. Its brand names decide which searches are brand "
                "searches, and those are kept out of the headline. Run: python -m aeo.ranksim forecast form import "
                "--file <the filled form>, then register again."
            )
        terms_by_site[name] = terms or ()
        if name in known_sites:
            stored_terms = tuple(sorted(_registered_brand_terms(scoreboard, name)))
            wanted = tuple(sorted({str(term).strip().lower() for term in terms_by_site[name] if str(term).strip()}))
            if not _same_brand_terms(name, stored_terms, wanted):
                raise ScoreboardRefusal(
                    f"{name} is already on the scoreboard with the brand terms {list(stored_terms)}, but its imported "
                    f"agency forms give {list(wanted)}. A registered site is never changed, so its brand flags cannot be "
                    "repaired here. Tell the engineer."
                )
            terms_by_site[name] = stored_terms  # the same flags; the stored row is never rewritten
    for site in sorted(manifest.sites, key=lambda item: site_key(item.domain)):
        name = site_key(site.domain)
        assert site.launch_date is not None
        scoreboard.add_site(
            name,
            source=source,
            launch_date=site.launch_date,
            launch_date_source=f"frozen seed manifest {digest}",
            built_by=site.built_by,
            niche=site.niche,
            brand_terms=terms_by_site[name],
        )
        if name in known_sites:
            sites_already += 1
        else:
            sites_added += 1
        for item in site.searches:
            key = (name, search_key(item.search))
            if key in existing_keys:
                searches_already += 1
                continue
            scoreboard.add_search(
                name,
                item.search,
                location_code=int(manifest.location_code),
                device=manifest.device,
                search_group=site.niche,
                difficulty=item.tercile,
                difficulty_value=item.difficulty_value,
                is_brand=None,
                target_url=item.target_url,
            )
            existing_keys.add(key)
            searches_added += 1
    scoreboard.freeze(freeze_name, {"manifest_hash": digest, "source": source, "manifest": manifest.to_payload()})
    return RegisterResult(digest, freeze_name, sites_added, sites_already, searches_added, searches_already, source)


def _registered_sites(scoreboard: Scoreboard) -> set[str]:
    with Session(scoreboard.engine) as session:
        return set(session.scalars(select(SiteRow.site)).all())


def _same_brand_terms(site: str, stored: Sequence[str], wanted: Sequence[str]) -> bool:
    """True when two lists of brand terms flag the same searches. No terms means the domain name (the scoreboard's fallback)."""

    def compact(terms: Sequence[str]) -> set[str]:
        return {"".join(ch for ch in str(term).casefold() if ch.isalnum()) for term in terms} - {""}

    label = [site.split(".")[0]]
    return compact(stored or label) == compact(wanted or label)


def _registered_brand_terms(scoreboard: Scoreboard, site: str) -> list[str]:
    with Session(scoreboard.engine) as session:
        row = session.get(SiteRow, site)
        return list(row.brand_terms or []) if row is not None else []


def _registered_search_keys(scoreboard: Scoreboard) -> set[tuple[str, str]]:
    with Session(scoreboard.engine) as session:
        return {(row.site, row.search_key) for row in session.scalars(select(SearchRow)).all()}


def schedule(
    manifest: SeedManifest, *, today: date | None = None, pulls: int = DEFAULT_PULLS, depth: int = TOP_DEPTH
) -> dict[str, Any]:
    """Each site's reading dates and stage dates, what they cost, and the plan's own schedule problems.

    `today` (a UTC date) only sets the days since launch shown per site. It defaults to the UTC date now.
    """
    today = today if today is not None else datetime.now(timezone.utc).date()
    searches = sum(len(site.searches) for site in manifest.sites)
    weekly = seeds_module.reading_count(milestones=False)
    with_milestones = seeds_module.reading_count(milestones=True)
    per_reading = serp_cost(depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True, pulls=pulls)
    weekly_usd = serp_cost(depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True, pulls=pulls * searches * weekly)
    milestones_usd = serp_cost(depth, mode=DATAFORSEO_MODE, resolve_poisoned_results=True, pulls=pulls * searches * with_milestones)
    launches = {site_key(site.domain): site.launch_date for site in manifest.sites if site.launch_date is not None}
    return {
        "manifest_hash": manifest.manifest_hash(),
        "today": today.isoformat(),
        "searches": searches,
        "reading_days": seeds_module.reading_days(milestones=True),
        "readings_weekly_only": weekly,
        "readings_with_milestones": with_milestones,
        "cost_per_reading_usd": str(per_reading),
        "cost_weekly_only_usd": str(weekly_usd),
        "cost_with_milestones_usd": str(milestones_usd),
        "cost_note": f"DataForSEO Standard queue with the repair option, {pulls} pulls at depth {depth}, prices from budget.py.",
        "sites": markers_module.schedule_check(launches, today) if launches else [],
        "late_dates": [
            {"domain": item.domain, "stage": item.stage, "stage_date": item.stage_date.isoformat(), "marker": item.marker,
             "marker_date": item.marker_date.isoformat()}
            for item in seeds_module.late_dates(manifest)
        ],
        "plan_schedule_problems": markers_module.plan_schedule_problems(),
        "text": seeds_module.schedule_report(manifest),
    }


# ----------------------------------------------------------------------------------------------
# Readings
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadingRunSummary:
    """What one reading run stored. Positions are REAL, or SYNTHETIC for the synthetic source."""

    provider: str
    label: str
    searches: int
    pulls_per_search: int
    pulls_returned: int
    pulls_ok: int
    pulls_dropped: int
    drop_reasons: dict[str, int]
    readings: int
    degraded: int
    not_independent: int
    failed_rounds: tuple[tuple[int, str], ...]
    reading_ids: tuple[str, ...]
    spent_usd: Decimal | None
    budget_refused: bool
    started_at: str
    finished_at: str
    history_used: int = 0  # searches given an earlier clean reading as history
    reader_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["failed_rounds"] = [list(item) for item in self.failed_rounds]
        out["reading_ids"] = list(self.reading_ids)
        out["spent_usd"] = None if self.spent_usd is None else str(self.spent_usd)
        return out


def source_label(source: SerpSource) -> str:
    """SYNTHETIC for a source of the simulated world or the DataForSEO sandbox (in any mode), REAL for Google results.

    types.provider_is_synthetic is the one list: "dataforseo-sandbox-live" counts as the sandbox too.
    """
    return SYNTHETIC if provider_is_synthetic(getattr(source, "name", None)) else REAL


def history_for(
    scoreboard: Scoreboard,
    specs: Sequence[PullSpec],
    *,
    before: datetime,
    label: str = REAL,
    max_age_days: int = HISTORY_MAX_AGE_DAYS,
) -> list[CleanReading]:
    """For each spec, the latest non-degraded stored reading of the same search, location and device.

    It must be taken before `before` and no more than `max_age_days` days
    earlier (config.HISTORY_MAX_AGE_DAYS), and carry `label`: real readings
    are judged only by real history, synthetic only by synthetic. Specs with
    no such reading are left out. REAL (or SYNTHETIC) stored readings.
    """
    floor = before - timedelta(days=max_age_days)
    out: list[CleanReading] = []
    seen: set[tuple[str, int, str]] = set()
    for spec in specs:
        key = (search_key(spec.search), int(spec.location_code), spec.device)
        if key in seen:
            continue
        seen.add(key)
        out.extend(scoreboard.readings_for(spec.search, spec.location_code, spec.device, before=before,
                                           not_before=floor, non_degraded_only=True, limit=1, label=label))
    return out


def take_readings(
    scoreboard: Scoreboard,
    source: SerpSource,
    specs: Sequence[PullSpec],
    *,
    pulls: int = DEFAULT_PULLS,
    gap_seconds: float = MIN_PULL_GAP_SECONDS,
    clock: Clock | None = None,
    ledger: SpendLedger | None = None,
    overlap_rule: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    use_history: bool = True,
) -> ReadingRunSummary:
    """Run the clean reader once over `specs`, store every pull and every reading, and summarise.

    Every round of pulls runs in this one process (round mode spreads them over a day instead, see
    `read_round` and `assemble`). For each search the latest earlier non-degraded reading of the same
    search, location and device, at most HISTORY_MAX_AGE_DAYS old and with the same label, is passed
    to the reader as history, and the round suspects are read from each round. All pulls and readings
    are stored in one transaction. The reader labels the readings (SYNTHETIC for the synthetic source).

    The money spent is the ledger's spent total after the run minus before it (settled charges
    plus open reservations, as budget.py counts them). It is None when no ledger is given.
    """
    if gap_seconds < MIN_PULL_GAP_SECONDS:
        raise ValueError(
            f"The pulls of one reading must be at least {MIN_PULL_GAP_SECONDS} seconds apart. Got {gap_seconds:g}. "
            "Pulls taken seconds apart share one spell of altered pages."
        )
    chosen = clock or SystemClock()
    before = ledger.spent_total if ledger is not None else None
    started_at = chosen.now()
    started = utc_iso(started_at)
    label = source_label(source)
    past = history_for(scoreboard, specs, before=started_at, label=label) if use_history and overlap_rule else []
    result = read_clean(source, list(specs), pulls=pulls, gap_seconds=gap_seconds, sleep=chosen.sleep, now=chosen.now,
                        overlap_rule=overlap_rule, version=version, history=past)
    readings = list(result.readings)
    scoreboard.store_batch(pulls=result.pulls, readings=readings)
    synthetic = label == SYNTHETIC or any(reading.label == SYNTHETIC for reading in readings)
    first_pull: dict[tuple[str, int, str], datetime] = {}
    for pull in result.pulls:
        key = (pull.spec.search, int(pull.spec.location_code), pull.spec.device)
        moment = parse_utc(pull.completed_at)
        first_pull[key] = min(moment, first_pull.get(key, moment))
    offered = sum(  # history the reader could use: same request, taken before that request's first pull
        1 for item in past
        if (key := (item.search, int(item.location_code), item.device)) in first_pull
        and parse_utc(item.taken_at) < first_pull[key]
    )
    reasons: Counter[str] = Counter()
    for reading in readings:
        for _pull_id, reason in reading.dropped:
            reasons[str(reason).split(":")[0]] += 1
    spent = (ledger.spent_total - before) if ledger is not None and before is not None else None
    return ReadingRunSummary(
        provider=getattr(source, "name", "unknown"),
        label=SYNTHETIC if synthetic else REAL,
        searches=len(specs),
        pulls_per_search=pulls,
        pulls_returned=len(result.pulls),
        pulls_ok=sum(1 for pull in result.pulls if pull.ok),
        pulls_dropped=sum(reading.pulls_total - reading.pulls_kept for reading in readings),
        drop_reasons=dict(sorted(reasons.items())),
        readings=len(readings),
        degraded=sum(1 for reading in readings if reading.degraded),
        not_independent=sum(1 for reading in readings if reading.pulls_not_independent),
        failed_rounds=tuple(result.failed_rounds),
        reading_ids=tuple(reading.reading_id for reading in readings),
        spent_usd=spent,
        budget_refused=any("BudgetError" in message for _index, message in result.failed_rounds),
        started_at=started,
        finished_at=utc_iso(chosen.now()),
        history_used=offered,
        reader_version=reader_version_name(version, overlap_rule=overlap_rule),
    )


# ----------------------------------------------------------------------------------------------
# Round mode: one round of pulls at a time, readings assembled later from the stored pulls
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundRunSummary:
    """What one round of pulls stored. The pulls are REAL, or SYNTHETIC for the synthetic source."""

    provider: str
    label: str
    searches: int
    pulls_returned: int
    pulls_ok: int
    failed: str | None
    freeze_name: str | None
    spent_usd: Decimal | None
    started_at: str
    finished_at: str

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["spent_usd"] = None if self.spent_usd is None else str(self.spent_usd)
        return out


def read_round(
    scoreboard: Scoreboard,
    source: SerpSource,
    specs: Sequence[PullSpec],
    *,
    clock: Clock | None = None,
    ledger: SpendLedger | None = None,
) -> RoundRunSummary:
    """Take ONE round of pulls (one pull per spec) and store them with a record of the round.

    No reading is built here: `assemble` builds the readings of a UTC date from the stored pulls of
    its rounds. The record is a scoreboard freeze named read_round.<start time>.<hash> that lists the
    round's pull ids, so `assemble` reads the round suspects of each round from exactly its pulls.
    A round that fails as a whole says why. When the source had already paid for part of the round
    (RoundFailed.pulls is not empty), those pulls are stored and recorded with the failure: they were
    paid for, and the unsent ones are failed pulls, never empty results.
    """
    chosen = clock or SystemClock()
    before = ledger.spent_total if ledger is not None else None
    started = utc_iso(chosen.now())
    label = source_label(source)
    failed: str | None = None
    got: list[Pull] = []
    freeze_name: str | None = None
    try:
        got = fetch_round(source, list(specs), now=chosen.now)
    except RoundFailed as error:
        failed = error.message
        got = list(error.pulls)
    if got:
        scoreboard.store_batch(pulls=got)
        ids = sorted(pull.pull_id for pull in got)
        stamp = started.replace("-", "").replace(":", "")
        freeze_name = f"{ROUND_FREEZE_PREFIX}{stamp}.{sha256_text(canonical_json(ids))[:12]}"
        scoreboard.freeze(freeze_name, {
            "kind": "read_round",
            "provider": getattr(source, "name", "unknown"),
            "label": label,
            "started_at": started,
            "pull_ids": ids,
            "searches": len(specs),
            **({"failed": failed} if failed else {}),
        })
    spent = (ledger.spent_total - before) if ledger is not None and before is not None else None
    return RoundRunSummary(
        provider=getattr(source, "name", "unknown"),
        label=label,
        searches=len(specs),
        pulls_returned=len(got),
        pulls_ok=sum(1 for pull in got if pull.ok),
        failed=failed,
        freeze_name=freeze_name,
        spent_usd=spent,
        started_at=started,
        finished_at=utc_iso(chosen.now()),
    )


def _round_groups(scoreboard: Scoreboard, pulls: Sequence[Pull]) -> list[list[Pull]]:
    """The rounds the pulls belong to, each with ALL of its stored pulls (other searches too).

    A pull listed in a read_round record belongs to that round. Other pulls are put in rounds by
    their requested time: a gap of more than ROUND_GAP_MINUTES starts a new round.
    """
    wanted = {pull.pull_id for pull in pulls}
    groups: list[list[Pull]] = []
    placed: set[str] = set()
    for row in scoreboard.freezes():
        if not str(row["name"]).startswith(ROUND_FREEZE_PREFIX):
            continue
        ids = [str(item) for item in (row["payload"] or {}).get("pull_ids", [])]
        if not wanted.intersection(ids):
            continue
        members: list[Pull] = []
        for pull_id in ids:
            try:
                members.append(scoreboard.pull(pull_id))
            except ScoreboardRefusal:
                continue  # a pull of the record that is not stored adds nothing
        placed.update(ids)
        groups.append(members)
    loose = sorted((pull for pull in pulls if pull.pull_id not in placed), key=lambda p: (parse_utc(p.requested_at), p.pull_id))
    gap = timedelta(minutes=ROUND_GAP_MINUTES)
    current: list[Pull] = []
    last: datetime | None = None
    for pull in loose:
        moment = parse_utc(pull.requested_at)
        if last is not None and moment - last > gap:
            groups.append(current)
            current = []
        current.append(pull)
        last = moment
    if current:
        groups.append(current)
    return groups


@dataclass(frozen=True)
class AssembleSummary:
    """What `assemble` built and stored for one UTC date. REAL readings (SYNTHETIC from the simulated world)."""

    day: str
    pulls_found: int
    rounds: int
    readings_built: int
    readings_stored: int
    readings_already_stored: int
    degraded: int
    history_used: int
    reading_ids: tuple[str, ...]
    degraded_reasons: dict[str, int]
    skipped: tuple[dict[str, str], ...]
    labels: dict[str, int]
    reader_version: str
    pulls_too_close: int = 0  # ok pulls left out because they came less than MIN_PULL_GAP_SECONDS after a kept pull
    already_assembled: tuple[dict[str, str], ...] = ()  # searches that already had a reading of this date

    @property
    def newer_pulls_left_out(self) -> int:
        """Searches already assembled for this date whose stored pulls have changed since: those pulls were not used."""
        return sum(1 for item in self.already_assembled if item.get("newer_pulls") == "yes")

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["reading_ids"] = list(self.reading_ids)
        out["skipped"] = [dict(item) for item in self.skipped]
        out["already_assembled"] = [dict(item) for item in self.already_assembled]
        out["newer_pulls_left_out"] = self.newer_pulls_left_out
        return out


def _spaced_pulls(pulls: Sequence[Pull]) -> tuple[list[Pull], int]:
    """The pulls of one request with the ok pulls at least MIN_PULL_GAP_SECONDS apart, and how many were left out.

    Ok pulls are taken in order of their requested time. One that was requested less than
    MIN_PULL_GAP_SECONDS after the last kept ok pull shares its spell of altered pages, so it counts
    as the same pull and is left out. Failed pulls are kept: the reader drops them anyway.
    """
    ordered = sorted(pulls, key=lambda p: (parse_utc(p.requested_at), p.pull_id))
    kept: list[Pull] = []
    last: datetime | None = None
    left_out = 0
    floor = timedelta(seconds=MIN_PULL_GAP_SECONDS)
    for pull in ordered:
        if pull.ok:
            moment = parse_utc(pull.requested_at)
            if last is not None and moment - last < floor:
                left_out += 1
                continue
            last = moment
        kept.append(pull)
    return kept, left_out


def assemble(
    scoreboard: Scoreboard,
    day: date,
    *,
    specs: Sequence[PullSpec] | None = None,
    version: int = DEFAULT_RULE_VERSION,
    overlap_rule: bool = True,
    use_history: bool = True,
) -> AssembleSummary:
    """Build and store one clean reading per search for the UTC date `day` from its stored pulls.

    The pulls used are those completed on that UTC day (with `specs`, only pulls of exactly those
    requests). Ok pulls of one request requested less than MIN_PULL_GAP_SECONDS apart count as one
    pull (`pulls_too_close`). Each reading gets the latest earlier non-degraded reading of its
    search, location and device as history (at most HISTORY_MAX_AGE_DAYS old, same label) and the
    round suspects of every round its pulls came from.

    One reading per search, location, device and UTC date: when one is already stored for that date
    (from an earlier assemble or read), the search is left as it is and listed in
    `already_assembled`, with newer_pulls "yes" when its stored pulls have changed since (those
    pulls are then not used). Running it twice on the same pulls changes nothing. Free: nothing is
    sent and nothing is spent.
    """
    start = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    wanted = set(specs) if specs is not None else None
    found = scoreboard.pulls_completed_between(start, end, search_keys=[spec.search for spec in specs] if specs else None)
    if wanted is not None:
        found = [pull for pull in found if pull.spec in wanted]
    rounds = _round_groups(scoreboard, found)
    suspects: list[RoundSuspects] = [round_suspects(group) for group in rounds if group]
    groups: dict[tuple[str, int, str], list[Pull]] = {}
    for pull in found:
        groups.setdefault((search_key(pull.spec.search), int(pull.spec.location_code), pull.spec.device), []).append(pull)
    built: list[CleanReading] = []
    skipped: list[dict[str, str]] = []
    already_assembled: list[dict[str, str]] = []
    history_used = 0
    too_close = 0
    for key in sorted(groups):
        mine, left_out = _spaced_pulls(groups[key])
        spec0 = mine[0].spec
        stored_today = [
            r for r in scoreboard.readings_for(spec0.search, spec0.location_code, spec0.device, on_or_before=day,
                                               not_before=start, non_degraded_only=False)
        ]
        if stored_today:
            used = {pid for r in stored_today for pid in r.pull_ids} | {
                str(pid) for r in stored_today for pid, _reason in r.dropped}
            newer = any(pull.pull_id not in used for pull in mine)
            already_assembled.append({
                "search": spec0.search,
                "reading_id": stored_today[0].reading_id,
                "taken_at": stored_today[0].taken_at,
                "newer_pulls": "yes" if newer else "no",
            })
            continue
        too_close += left_out
        first = min(parse_utc(pull.completed_at) for pull in mine)
        label = reading_label(mine)
        past: list[CleanReading] = []
        if use_history and overlap_rule:
            spec = mine[0].spec
            past = history_for(scoreboard, [spec], before=first, label=label)
            past = [item for item in past if item.search == spec.search]  # the reader matches the exact search text
        try:
            made = assemble_readings(mine, history=past, suspects=suspects or None, window_hours=None,
                                     overlap_rule=overlap_rule, version=version)
        except ValueError as error:
            skipped.append({"search": mine[0].spec.search, "reason": str(error)})
            continue
        history_used += 1 if past else 0
        built.extend(made)
    new: list[CleanReading] = []
    already = len(already_assembled)
    for reading in built:
        try:
            scoreboard.reading(reading.reading_id)
        except ScoreboardRefusal:
            new.append(reading)
        else:
            already += 1
    scoreboard.store_batch(readings=new)
    reasons: Counter[str] = Counter(str(r.degraded_reason).split(":")[0] for r in built if r.degraded)
    return AssembleSummary(
        day=day.isoformat(),
        pulls_found=len(found),
        rounds=len(suspects),
        readings_built=len(built),
        readings_stored=len(new),
        readings_already_stored=already,
        degraded=sum(1 for reading in built if reading.degraded),
        history_used=history_used,
        reading_ids=tuple(reading.reading_id for reading in built),
        degraded_reasons=dict(sorted(reasons.items())),
        skipped=tuple(skipped),
        labels=dict(sorted(Counter(reading.label for reading in built).items())),
        reader_version=reader_version_name(version, overlap_rule=overlap_rule),
        pulls_too_close=too_close,
        already_assembled=tuple(already_assembled),
    )


@dataclass(frozen=True)
class StoredReading:
    reading_id: str
    taken_at: str
    degraded: bool
    label: str


def stored_readings(scoreboard: Scoreboard, search: str, *, location_code: int, device: str = DEFAULT_DEVICE) -> list[StoredReading]:
    """Every stored reading of one search at one location and device, oldest first."""
    found = scoreboard.readings_for(search, location_code, device, non_degraded_only=False)
    return [StoredReading(r.reading_id, r.taken_at, bool(r.degraded), r.label) for r in reversed(found)]


def latest_reading(
    scoreboard: Scoreboard,
    search: str,
    *,
    location_code: int,
    device: str,
    as_of: date,
    not_after: datetime,
    label: str | None = None,
) -> CleanReading | None:
    """The latest non-degraded reading taken on or before `as_of` and not after `not_after`. None when there is none.

    `label` keeps only REAL or only SYNTHETIC readings.
    """
    for reading in scoreboard.readings_for(search, location_code, device, on_or_before=as_of, label=label):
        if parse_utc(reading.taken_at) <= parse_utc(utc_iso(not_after)):
            return reading
    return None


# ----------------------------------------------------------------------------------------------
# Marker 1
# ----------------------------------------------------------------------------------------------


def freeze_marker1_sample(
    scoreboard: Scoreboard,
    candidates: Sequence[str],
    *,
    n: int = MARKER1_SEARCHES,
    seed: int = MARKER1_SAMPLE_SEED,
    strata: Mapping[str, str] | None = None,
    name: str = MARKER1_FREEZE_NAME,
) -> tuple[SearchSample, str]:
    """Draw the frozen marker 1 search sample and freeze it in the scoreboard. A second, different sample under the same name is refused."""
    sample = sample_marker1_searches(candidates, n, seed=seed, strata=strata)
    payload = {
        "searches": list(sample.searches),
        "strata": [list(item) for item in sample.strata],
        "seed": sample.seed,
        "requested": sample.requested,
        "candidates": sample.candidates,
        "per_stratum": [list(item) for item in sample.per_stratum],
        "sample_sha256": sample.sha256,
    }
    digest = scoreboard.freeze(name, payload)
    return sample, digest


def frozen_marker1_searches(scoreboard: Scoreboard, name: str = MARKER1_FREEZE_NAME) -> list[str]:
    for row in scoreboard.freezes():
        if row["name"] == name:
            return [str(item) for item in row["payload"]["searches"]]
    raise ScoreboardRefusal(f"No marker 1 sample is frozen under {name}. Run: python -m aeo.ranksim forecast marker1 sample")


def pair_marker1_readings(
    scoreboard: Scoreboard,
    searches: Sequence[str],
    *,
    location_code: int,
    device: str = DEFAULT_DEVICE,
    a_date: date | None = None,
    b_date: date | None = None,
) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    """(reading A id, reading B id) per search with B 20 to 28 hours after A, closest to 24 hours.

    Degraded readings are not paired. Returns the pairs and the searches left unpaired with the reason.
    """
    pairs: list[tuple[str, str]] = []
    unpaired: list[dict[str, str]] = []
    for search in searches:
        usable = [r for r in stored_readings(scoreboard, search, location_code=location_code, device=device) if not r.degraded]
        best: tuple[float, str, str, str] | None = None
        for first in usable:
            first_at = parse_utc(first.taken_at)
            if a_date is not None and first_at.date() != a_date:
                continue
            for second in usable:
                second_at = parse_utc(second.taken_at)
                if b_date is not None and second_at.date() != b_date:
                    continue
                hours = (second_at - first_at).total_seconds() / 3600.0
                if not MIN_GAP_HOURS <= hours <= MAX_GAP_HOURS:
                    continue
                key = (abs(hours - 24.0), first.taken_at, first.reading_id, second.reading_id)
                if best is None or key < best:
                    best = key
        if best is None:
            reason = "fewer than two usable readings" if len(usable) < 2 else "no two usable readings 20 to 28 hours apart"
            unpaired.append({"search": search, "reason": reason})
        else:
            pairs.append((best[2], best[3]))
    return pairs, unpaired


def _reading_pulls(scoreboard: Scoreboard, reading: CleanReading) -> list[Pull] | None:
    """Every stored pull of a reading, kept and dropped. None when one of them is not stored."""
    dropped = [pull_id for pull_id, _reason in reading.dropped if not _pseudo_pull_id(pull_id)]
    pulls: list[Pull] = []
    for pull_id in dict.fromkeys(list(reading.pull_ids) + dropped):
        try:
            pulls.append(scoreboard.pull(pull_id))
        except ScoreboardRefusal:
            return None
    return pulls or None


def _pseudo_pull_id(pull_id: str) -> bool:
    """The reader records a failed round as ("round-<k>", reason) and a broken spec as ("reading", reason)."""
    return pull_id == "reading" or pull_id.startswith("round-")


def marker1(
    scoreboard: Scoreboard,
    searches: Sequence[str],
    *,
    location_code: int,
    device: str = DEFAULT_DEVICE,
    a_date: date | None = None,
    b_date: date | None = None,
    reader_version: int = DEFAULT_RULE_VERSION,
    frozen_list: bool = True,
) -> dict[str, Any]:
    """The marker 1 report and its evaluation, from readings already in the scoreboard.

    Every search of `searches` counts in the denominator: a search with no usable pair (degraded
    readings, no reading B 20 to 28 hours after A) is not dropped from the marker. When fewer than
    noise.MARKER1_MIN_MEASURED_SHARE of the searches give a pair, the headline is withheld and the
    marker is not measurable. Marker 1 is judged only on the frozen sample: with
    frozen_list=False (a list that is not the frozen marker1.sample) the verdict is NOT A MARKER
    RESULT and passed is None; the computed value is kept under list_check_passed.

    When every pull of every paired reading is stored, the report rebuilds the readings from their
    pulls with reader `reader_version` (so it also carries the raw single-pull baseline and the
    filter ablation). Each search's history is the latest earlier non-degraded reading taken before
    reading A's first pull (at most HISTORY_MAX_AGE_DAYS old), passed to both A and B, so reading A
    is never the history of reading B. Otherwise it reports on the stored clean readings alone and
    says so. The report always names the reader settings. The marker module labels the verdict.
    """
    if not searches:
        raise ValueError("marker 1 needs the frozen list of searches")
    settings = reader_settings(version=reader_version)
    pair_ids, unpaired = pair_marker1_readings(
        scoreboard, searches, location_code=location_code, device=device, a_date=a_date, b_date=b_date
    )
    readings = [(scoreboard.reading(a), scoreboard.reading(b)) for a, b in pair_ids]
    synthetic = any(r.label == SYNTHETIC for pair in readings for r in pair)
    pulls: list[tuple[list[Pull], list[Pull]]] = []
    from_pulls = bool(readings)
    for first, second in readings:
        first_pulls = _reading_pulls(scoreboard, first)
        second_pulls = _reading_pulls(scoreboard, second)
        if first_pulls is None or second_pulls is None:
            from_pulls = False
            break
        pulls.append((first_pulls, second_pulls))
    if from_pulls:
        history: dict[str, CleanReading] = {}
        for (first, _second), (first_pulls, _) in zip(readings, pulls):
            earliest = min(parse_utc(pull.completed_at) for pull in first_pulls)
            spec = first_pulls[0].spec
            found = history_for(scoreboard, [spec], before=earliest, label=reading_label(first_pulls))
            found = [item for item in found if item.search == spec.search]
            if found:
                history[spec.search] = found[0]
        report = marker1_report_from_pulls(pulls, version=reader_version, history=history,
                                           searches_requested=len(searches))
    else:
        stored = sorted({str(r.reader_version or "unknown") for pair in readings for r in pair})
        report = agreement_report(readings, reader={**settings, "history_used": None, "stored_reader_versions": stored},
                                  searches_requested=len(searches))
    notes = [] if from_pulls else ["The report uses the stored clean readings. Their pulls are not all stored, so there is no raw pull baseline."]
    if synthetic:
        report["label"] = SYNTHETIC
    report["searches_requested"] = len(searches)
    report["searches_unpaired"] = unpaired
    report["built_from_pulls"] = from_pulls
    report["frozen_list"] = bool(frozen_list)
    evaluation = markers_module.evaluate_marker(1, report)
    if not frozen_list:
        evaluation["list_check_passed"] = evaluation["passed"]
        evaluation["passed"] = None
        evaluation["not_a_marker_result"] = True
        evaluation["notes"] = list(evaluation["notes"]) + [
            "NOT A MARKER RESULT: the searches are not the frozen marker 1 sample, so this cannot pass marker 1."
        ]
    if unpaired:
        notes.append(f"{len(unpaired)} of {len(searches)} searches have no pair of readings 20 to 28 hours apart. "
                     "They count against the marker, not out of it.")
    report["pipeline_notes"] = notes
    return {"report": report, "evaluation": evaluation, "pairs": [list(item) for item in pair_ids]}


def marker1_lines(result: Mapping[str, Any]) -> list[str]:
    """Marker 1 in short plain sentences."""
    report = result["report"]
    label = report.get("label", REAL)
    head = report.get("headline") or {}
    lines = []
    if result["evaluation"].get("not_a_marker_result"):
        lines.append("NOT A MARKER RESULT: these searches are not the frozen marker 1 sample. Diagnostics only.")
    lines += [
        f"Marker 1, the clean reader. Pairs used: {report.get('pairs_used')} of {report.get('searches_requested')} searches ({label}).",
        f"Headline agreement: {_pct(head.get('rate'))} of {head.get('count')} top 10 units ({label}).",
    ]
    if head.get("not_measurable"):
        lines.append(f"{head['not_measurable']} ({label}).")
        lines.append(f"On the measured searches alone it would be {_pct(head.get('rate_on_measured_searches_only'))}. "
                     f"That is not the headline ({label}).")
    interval = head.get("cluster_interval") or [None, None]
    if interval[0] is not None:
        lines.append(f"Interval over searches: {_pct(interval[0])} to {_pct(interval[1])} ({label}).")
    raw = report.get("raw_single_pull_baseline")
    if raw:
        lines.append(f"Single raw pulls on the same searches agree {_pct(raw['headline'].get('rate'))} of the time ({label}).")
    beside = report.get("headline_beside_ablation") or {}
    if beside.get("filter_off") is not None:
        lines.append(f"With the altered-page filter switched off, the same readings agree {_pct(beside['filter_off'])} "
                     f"({label}). The headline keeps the filter on.")
    else:
        lines.append("The number with the altered-page filter switched off is missing: it needs every pull of every "
                     "paired reading to be stored.")
    lines.append(reader_line(report.get("reader")))
    lines.extend(str(note) for note in report.get("pipeline_notes", []))
    lines.append(markers_module.render_marker_text(result["evaluation"]))
    return lines


def reader_line(reader: Mapping[str, Any] | None) -> str:
    """The reader version and its settings, in one plain sentence (the same values --json shows under reader)."""
    if not reader:
        return "The reader version and its settings are not recorded in this report."
    version = reader.get("reader_version") or "unknown"
    rest = {k: v for k, v in reader.items() if k != "reader_version" and v is not None}
    names = {"filter_on": "altered-page filter"}
    shown = "; ".join(f"{names.get(k, k.replace('_', ' '))} {_setting(v)}" for k, v in rest.items())
    return f"Reader {version}, settings: {shown or 'none recorded'}."


def _setting(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(_setting(item) for item in value) or "none"
    if isinstance(value, Mapping):
        return ", ".join(f"{k} {_setting(v)}" for k, v in value.items()) or "none"
    return str(value).replace(chr(0x2014), ",").replace(chr(0x2013), " to ")  # no dashes in plain text


def _pct(value: Any) -> str:
    return "no value" if value is None else f"{100 * float(value):.1f}%"


# ----------------------------------------------------------------------------------------------
# Grading, reports, markers 3 to 5
# ----------------------------------------------------------------------------------------------


def grade(scoreboard: Scoreboard, today: date) -> dict[str, Any]:
    """The nightly grading job as of `today` (an explicit UTC date)."""
    return grade_job.run(scoreboard, today)


TRAINING_SPLIT = "train"
TRAINING_SOURCE = "backtest"  # training cells come from the backtest's training split
SITE_STRENGTH_NOTE_KEY = "baselines"  # forecast notes: {"baselines": {"site_strength": point}}, made at forecast time
SITE_STRENGTH_MISSING = (
    "The site strength only baseline is missing: {why} The forecasting commands (upload and update) store it with "
    "every forecast they make, from the bundle's site strength only model on the same date. Markers 3 to 5 cannot "
    "pass while it is missing."
)


def site_strength_points(
    scoreboard: Scoreboard, *, source: str, stage: str, as_of: date, model_version: str | None = None
) -> tuple[dict[str, int] | None, str]:
    """The site strength only baseline point of every graded forecast (forecast id -> point), and a plain sentence.

    The point is the one the forecast stored when it was made (notes["baselines"]["site_strength"]): the
    bundle's strength-only placement model on the same feature row and date, through the same appearance
    gate, so it uses nothing later than the forecast. When any graded forecast lacks it the baseline is
    missing as a whole (never filled in for part of the rows) and the sentence says how many lack it.
    """
    records = scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)
    if not records:
        return None, SITE_STRENGTH_MISSING.format(why=f"there is no graded {source} {stage} forecast as of {as_of}.")
    ids = [record.forecast_id for record in records]
    with Session(scoreboard.engine) as session:
        notes = dict(session.execute(select(ForecastRow.forecast_id, ForecastRow.notes)
                                     .where(ForecastRow.forecast_id.in_(ids))).all())
    points: dict[str, int] = {}
    for forecast_id in ids:
        value = ((notes.get(forecast_id) or {}).get(SITE_STRENGTH_NOTE_KEY) or {}).get("site_strength")
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= NT50:
            points[forecast_id] = value
    if len(points) < len(ids):
        lacking = len(ids) - len(points)
        return None, SITE_STRENGTH_MISSING.format(
            why=f"{lacking} of {len(ids)} graded {source} {stage} forecasts carry no site strength only point "
                "(made by a placeholder or before forecasting stored it).")
    return points, (f"The site strength only baseline is the point each forecast stored when it was made, from the "
                    f"bundle's strength-only model, on all {len(ids)} graded forecasts.")


def band_prior_training(
    scoreboard: Scoreboard, *, source: str, stage: str, as_of: date, model_version: str | None = None
) -> tuple[list[Any], str]:
    """Graded cells of the TRAINING split for the band prior baseline, and a plain sentence saying where they came from.

    Training cells are graded forecasts of the same stage whose notes carry split "train", from the
    backtest (from synthetic forecasts for a synthetic report, so sources are never mixed with
    synthetic data). Every cell on a site that is graded in the report (the held-out sites) is left
    out, so the band prior never sees a held-out site. The list is empty when none remain; the
    sentence then says why.
    """
    fetched = scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)
    held_out = {record.cell.site for record in report_module.held_out_records(fetched, source)[0]}
    training_source = "synthetic" if source == "synthetic" else TRAINING_SOURCE
    with Session(scoreboard.engine) as session:
        versions = sorted(set(session.scalars(
            select(ForecastRow.model_version).where(ForecastRow.source == training_source, ForecastRow.stage == stage)
        ).all()))
    cells: list[Any] = []
    dropped_sites: set[str] = set()
    for version in versions:
        for record in scoreboard.graded_records(training_source, stage, as_of, "original", model_version=version):
            if record.split != TRAINING_SPLIT:
                continue
            if record.cell.site in held_out:
                dropped_sites.add(record.cell.site)
                continue
            cells.append(record.cell)
    if cells:
        sites = len({cell.site for cell in cells})
        why = (f"The band prior by difficulty is fitted on {len(cells)} graded {training_source} {stage} cells of the "
               f"training split from {sites} sites, none of them graded in this report.")
    elif dropped_sites:
        why = (f"The band prior by difficulty is missing: every graded training cell of {training_source} {stage} is on a "
               f"site graded in this report ({len(dropped_sites)} sites), and the prior is fitted on other sites only.")
    else:
        why = (f"The band prior by difficulty is missing: there is no graded {training_source} {stage} forecast of the "
               f"training split as of {as_of.isoformat()} to fit it on.")
    return cells, why


def scoreboard_paths(scoreboard: Scoreboard) -> ForecastPaths:
    """The data folder of a scoreboard kept at <folder>/scoreboard.db (the default layout and --dir), else the default.

    The model bundles live in that folder's models/. A scoreboard elsewhere (RANKSIM_SCOREBOARD_URL)
    falls back to forecast_paths(), which honours RANKSIM_FORECAST_DIR.
    """
    url = str(getattr(scoreboard, "url", "") or "")
    if url.startswith("sqlite:///"):
        file = Path(url.removeprefix("sqlite:///"))
        if file.name == "scoreboard.db":
            return ForecastPaths(file.parent)
    return forecast_paths()


# Sources whose forecasts can come from a live model bundle; their band prior is the one stored in that bundle.
BUNDLE_BAND_PRIOR_SOURCES: tuple[str, ...] = ("seed", "pilot", "synthetic")


def stage_marker(
    scoreboard: Scoreboard,
    *,
    source: str,
    stage: str,
    as_of: date,
    model_version: str | None = None,
    training_cells: Sequence[Any] | None = None,
    training_note: str | None = None,
    paths: ForecastPaths | None = None,
) -> dict[str, Any]:
    """The stage report for one source and stage, and the marker judged on it when the stage has one (3, 4 or 5).

    All four spec baselines are handed to report.stage_report when they can be built:
    always NT50 and persistence (always), the band prior by difficulty, and the site strength only
    points each forecast stored when it was made (`site_strength_points`).

    The band prior. Given `training_cells` (with `training_note`), it is fitted on those. For a seed,
    pilot or synthetic report whose forecasts were made by a live model bundle it is the band prior
    stored in that bundle (forecasting.report_band_prior, found from the forecasts' model version
    under `paths`, default the folder that holds the scoreboard): fitted at training time on that
    bundle's own training rows. Forecasts from more than one bundle, or an old bundle without one,
    leave it missing. Otherwise (backtest reports, and forecasts not made by a live bundle) it is
    fitted on graded training-split cells (`band_prior_training`).
    In every case a fitting site graded in the report is refused. `baseline_notes` says in plain
    sentences where each baseline came from or why it is missing; the report and the marker never
    pass while one is missing.
    """
    from .forecasting import bundle_ref, report_band_prior

    scoreboard.require_verified()
    training: list[Any] = []
    fitted = None
    records = (scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)
               if training_cells is None and source in BUNDLE_BAND_PRIOR_SOURCES else [])
    if training_cells is not None:
        training = list(training_cells)
        band_note = training_note or (f"The band prior by difficulty is fitted on {len(training)} training cells given "
                                      "by the caller.")
    elif any(bundle_ref(r.model_version) for r in records):
        found = report_band_prior(records, paths or scoreboard_paths(scoreboard))
        band_note = found["note"]
        if found["table"] is not None:
            fitted = report_module.FittedBandPrior(found["table"], frozenset(found["sites"]),
                                                   frozenset(found["search_groups"]), band_note)
    else:
        training, band_note = band_prior_training(scoreboard, source=source, stage=stage, as_of=as_of,
                                                  model_version=model_version)
    strength, strength_note = site_strength_points(scoreboard, source=source, stage=stage, as_of=as_of,
                                                   model_version=model_version)
    report = report_module.stage_report(scoreboard, source=source, stage=stage, as_of=as_of, model_version=model_version,
                                        band_prior_training=training or None, band_prior_fitted=fitted,
                                        extra_baselines={"site_strength": strength} if strength is not None else None)
    notes = [band_note, strength_note]
    report["baseline_notes"] = notes
    if report.get("band_prior") is not None:
        report["band_prior_source"] = band_note  # printed once, as the band prior's own line
        report["notes"] = list(report.get("notes") or []) + [strength_note]
    else:
        report["band_prior_source"] = None
        report["notes"] = list(report.get("notes") or []) + notes
    number = STAGE_MARKERS.get(stage)
    evaluation = markers_module.evaluate_marker(number, report) if number is not None else None
    if evaluation is not None:
        evaluation["notes"] = list(evaluation["notes"]) + notes
    return {"report": report, "evaluation": evaluation, "marker": number, "baseline_notes": notes}


# ----------------------------------------------------------------------------------------------
# Stage calendar: which stage forecasts are due soon and which were missed
# ----------------------------------------------------------------------------------------------

STAGE_DUE_WINDOW_DAYS = 3


def stage_calendar(scoreboard: Scoreboard, today: date, *, window_days: int = STAGE_DUE_WINDOW_DAYS) -> dict[str, list[dict[str, Any]]]:
    """Seed and pilot stages due from `today` to `today + window_days`, and stages that were missed.

    A stage's forecast is dated on or before its stage day (launch + config.STAGE_DAYS), and the
    scoreboard refuses one dated later or written more than a day after its date. So the stage job
    must run on or before the stage day, or at the latest FORECAST_FROZEN_GAP_DAYS after it with the
    forecast dated on the stage day: such a stage is still listed as due, with `last_day_to_record`.

    One forecast per site, search and stage (spec section 9): a stage is recorded for a site only when
    every registered search of the site has a forecast at that stage. Until then it stays due, and
    `searches_open` names the searches still without one (a run that skipped some searches leaves the
    others alone; re-running the site fills only the open ones). A stage whose last day to record has
    passed with open searches is MISSED for exactly those searches: it is reported, and it is never
    backfilled (a later forecast would be a shorter lead, never that stage). A site with no registered
    search counts as recorded once it has any forecast at the stage.
    """
    from .config import FORECAST_FROZEN_GAP_DAYS

    with Session(scoreboard.engine) as session:
        sites = session.scalars(select(SiteRow).where(SiteRow.source.in_(("seed", "pilot"))).order_by(SiteRow.site)).all()
        made: dict[tuple[str, str], set[str]] = {}
        for row in session.scalars(select(ForecastRow)).all():
            made.setdefault((row.site, row.stage), set()).add(row.search_key)
        registered: dict[str, dict[str, str]] = {}
        for row in session.scalars(select(SearchRow)).all():
            registered.setdefault(row.site, {})[row.search_key] = row.search
        site_rows = [(row.site, row.source, date.fromisoformat(row.launch_date)) for row in sites]
    due: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for site, source, launch in site_rows:
        searches = registered.get(site, {})
        for stage, offset in STAGE_DAYS.items():
            day = launch + timedelta(days=offset)
            last = day + timedelta(days=FORECAST_FROZEN_GAP_DAYS)
            done = made.get((site, stage), set())
            open_searches = sorted(searches[key] for key in searches if key not in done)
            recorded = not open_searches if searches else bool(done)
            entry = {"site": site, "source": source, "stage": stage, "stage_date": day.isoformat(),
                     "last_day_to_record": last.isoformat(), "recorded": recorded,
                     "searches_open": open_searches, "searches_recorded": len(set(searches) & done),
                     "searches_registered": len(searches)}
            if recorded:
                continue
            if last < today:
                missed.append(entry)
            elif day <= today + timedelta(days=window_days):
                due.append(entry)
    def key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (item["stage_date"], item["site"], item["stage"])

    return {"due": sorted(due, key=key), "missed": sorted(missed, key=key)}


# ----------------------------------------------------------------------------------------------
# Export, import, verify
# ----------------------------------------------------------------------------------------------


def export_scoreboard(scoreboard: Scoreboard, directory: str | Path) -> dict[str, Any]:
    """Write the scoreboard as JSONL plus HEADS.json for git. Refuses when the chains do not verify."""
    return scoreboard.export(directory)


def import_scoreboard(url: str, directory: str | Path, *, clock: Clock | None = None) -> ChainStatus:
    """Rebuild an empty scoreboard at `url` from an export and verify it."""
    chosen = clock or SystemClock()
    board = Scoreboard(url, clock=chosen.now)
    try:
        return board.import_export(directory)
    finally:
        board.close()


@dataclass(frozen=True)
class ExportComparison:
    """Whether a committed export is a prefix of the database. `problems` says exactly what differs."""

    ok: bool
    problems: tuple[str, ...]
    counts: dict[str, dict[str, int]]
    chain: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": list(self.problems), "counts": self.counts, "chain": self.chain}


_KEYED_TABLES: tuple[tuple[str, Any, Callable[[dict[str, Any]], str]], ...] = (
    ("sites.jsonl", SiteRow, lambda row: str(row["site"])),
    ("searches.jsonl", SearchRow, lambda row: f"{row['site']} | {row['search_key']}"),
    ("model_versions.jsonl", ModelVersionRow, lambda row: str(row["version"])),
    ("pulls.jsonl", PullRow, lambda row: str(row["pull_id"])),
    ("freezes.jsonl", FreezeRow, lambda row: str(row["name"])),
)
_CHAIN_TABLES: tuple[tuple[str, str, Any], ...] = (
    ("forecasts.jsonl", "forecasts", ForecastRow),
    ("grades.jsonl", "grades", GradeRow),
)


def _row_text(row: Any) -> str:
    """A database row as the export writes it: the canonical JSON of every column."""
    return canonical_json({column.name: getattr(row, column.name) for column in row.__table__.columns})


def _changed_fields(exported: str, stored: str) -> str:
    first, second = json.loads(exported), json.loads(stored)
    return ", ".join(sorted(key for key in set(first) | set(second) if first.get(key) != second.get(key)))


def verify_against_export(scoreboard: Scoreboard, directory: str | Path) -> ExportComparison:
    """Check that a committed export is a prefix of the database.

    The hash chain alone cannot see rows cut off the end of the database: a shorter chain still
    verifies. So the export's head hashes and counts are compared with the database: every exported
    forecast and grade must sit in the database at the same sequence number with the same content,
    and every exported site, search, model version, pull, reading and freeze must still be there
    unchanged. Rows added after the export are fine. Core updates may only have gained an end date.
    """
    folder = Path(directory)
    problems: list[str] = []
    counts: dict[str, dict[str, int]] = {}
    status = scoreboard.verify()
    if not status.ok:
        problems.extend(f"The database chain does not verify: {problem}" for problem in status.problems)
    heads_path = folder / "HEADS.json"
    if not heads_path.exists():
        problems.append(f"{heads_path} is missing, so there is nothing to compare against.")
        return ExportComparison(False, tuple(problems), counts, status.to_dict())
    heads = json.loads(heads_path.read_text(encoding="utf-8"))
    exported: dict[str, list[str]] = {}
    for name in EXPORT_FILES:
        path = folder / name
        if not path.exists():
            problems.append(f"The export has no {name}.")
            exported[name] = []
            continue
        body = path.read_text(encoding="utf-8")
        if sha256_text(body) != (heads.get("files") or {}).get(name):
            problems.append(f"The exported {name} does not match its sha256 in HEADS.json. The export was changed after it was written.")
        exported[name] = [line for line in body.splitlines() if line.strip()]

    with Session(scoreboard.engine) as session:
        for name, head_key, model in _CHAIN_TABLES:
            lines = exported[name]
            rows = [json.loads(line) for line in lines]
            head = heads.get(head_key) or {}
            noun = head_key[:-1]
            if int(head.get("count", -1)) != len(rows):
                problems.append(f"HEADS.json says {head.get('count')} {head_key} but {name} holds {len(rows)}.")
            if rows and head.get("head") != rows[-1].get("row_hash"):
                problems.append(f"HEADS.json names {head.get('head')} as the {noun} head, but the last exported row is {rows[-1].get('row_hash')}.")
            database = session.scalars(select(model).order_by(model.seq)).all()
            counts[head_key] = {"export": len(rows), "database": len(database)}
            by_seq = {row.seq: row for row in database}
            missing = [row["seq"] for row in rows if row["seq"] not in by_seq]
            if missing:
                problems.append(
                    f"{len(missing)} exported {head_key} are missing from the database (sequence {missing[0]} to {missing[-1]}). "
                    f"The database holds {len(database)} and the export {len(rows)}. Recent rows were cut off."
                )
            for line, row in zip(lines, rows, strict=True):
                stored = by_seq.get(row["seq"])
                if stored is None:
                    continue
                stored_text = _row_text(stored)
                if stored_text != line:
                    problems.append(
                        f"The {noun} at sequence {row['seq']} differs from the export in {_changed_fields(line, stored_text)} "
                        f"(export hash {row.get('row_hash')}, database hash {stored.row_hash})."
                    )
                    break
            if rows and len(database) >= len(rows) and database[len(rows) - 1].row_hash != head.get("head"):
                problems.append(f"The database {noun} at position {len(rows)} is not the exported head {head.get('head')}.")

        for name, model, key_of in _KEYED_TABLES:
            lines = exported[name]
            stored_rows = session.scalars(select(model)).all()
            database = {}
            for item in stored_rows:
                item_text = _row_text(item)
                database[key_of(json.loads(item_text))] = item_text
            counts[name.split(".")[0]] = {"export": len(lines), "database": len(database)}
            gone: list[str] = []
            differs: str | None = None
            for line in lines:
                key = key_of(json.loads(line))
                stored_text = database.get(key)
                if stored_text is None:
                    gone.append(key)
                elif stored_text != line and differs is None:
                    differs = f"The row {key} of {name} differs from the export in {_changed_fields(line, stored_text)}."
            if gone:
                problems.append(f"{len(gone)} exported rows of {name} are missing from the database, for example {gone[0]}.")
            if differs:
                problems.append(differs)

        reading_lines = exported["readings.jsonl"]
        stored_readings_by_id: dict[str, str] = {}
        for row in session.scalars(select(ReadingRow)).all():
            entry = {column.name: getattr(row, column.name) for column in row.__table__.columns}
            positions = session.scalars(
                select(ReadingPositionRow)
                .where(ReadingPositionRow.reading_id == row.reading_id)
                .order_by(ReadingPositionRow.position, ReadingPositionRow.site)
            ).all()
            entry["positions"] = [
                {"site": p.site, "position": p.position, "pull_positions": list(p.pull_positions or ()), "best_url": p.best_url}
                for p in positions
            ]
            stored_readings_by_id[row.reading_id] = entry
        counts["readings"] = {"export": len(reading_lines), "database": len(stored_readings_by_id)}
        gone_readings: list[str] = []
        reading_differs: str | None = None
        for line in reading_lines:
            exported_row = json.loads(line)
            reading_id = str(exported_row["reading_id"])
            stored_entry = stored_readings_by_id.get(reading_id)
            if stored_entry is None:
                gone_readings.append(reading_id)
                continue
            # An export written before the provenance columns existed has no such keys. The database
            # added them empty on open, so an empty value there matches a missing key.
            trimmed = {key: value for key, value in stored_entry.items()
                       if not (key in READING_PROVENANCE_COLUMNS and key not in exported_row and value is None)}
            stored_text = canonical_json(trimmed)
            if stored_text != line and reading_differs is None:
                reading_differs = f"The reading {reading_id} differs from the export in {_changed_fields(line, stored_text)}."
        if gone_readings:
            problems.append(f"{len(gone_readings)} exported readings are missing from the database, for example {gone_readings[0]}.")
        if reading_differs:
            problems.append(reading_differs)

        updates = {row.id: json.loads(_row_text(row)) for row in session.scalars(select(CoreUpdateRow)).all()}
        counts["core_updates"] = {"export": len(exported["core_updates.jsonl"]), "database": len(updates)}
        for line in exported["core_updates.jsonl"]:
            row = json.loads(line)
            stored = updates.get(row["id"])
            if stored is None:
                problems.append(f"The exported core update {row.get('name')} is missing from the database.")
                continue
            for key, value in row.items():
                if stored.get(key) == value:
                    continue
                if key in ("ended_on", "ended_recorded_at") and value is None:
                    continue  # an end date recorded after the export is allowed
                problems.append(f"The core update {row.get('name')} differs from the export in {key}.")
                break

    return ExportComparison(not problems, tuple(problems), counts, status.to_dict())


def verify(scoreboard: Scoreboard, ledger: SpendLedger, *, export_dir: str | Path | None = None) -> dict[str, Any]:
    """The scoreboard chains, the spend ledger chain, and, when given, the committed export."""
    status = scoreboard.verify()
    ledger_ok, ledger_head, ledger_rows = ledger.verify()
    ledger_problem = ledger.state().problem
    comparison = verify_against_export(scoreboard, export_dir) if export_dir is not None else None
    ok = status.ok and ledger_ok and (comparison is None or comparison.ok)
    return {
        "ok": ok,
        "scoreboard": status.to_dict(),
        "ledger": {"ok": ledger_ok, "head": ledger_head, "rows": ledger_rows, "problem": ledger_problem},
        "export": comparison.to_dict() if comparison is not None else None,
    }


# ----------------------------------------------------------------------------------------------
# Imports: Search Console exports and agency forms
# ----------------------------------------------------------------------------------------------

GSC_KINDS = ("ui", "daily", "range", "api", "index")


@dataclass(frozen=True)
class ImportRecord:
    """A file copied under the forecast data directory, with its sha256 frozen in the scoreboard."""

    kind: str
    site: str
    sha256: str
    stored_path: str
    freeze_name: str
    rows: int
    first_date: str | None
    last_date: str | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["notes"] = list(self.notes)
        return out


def _refuse_client_data(site: str, path: Path) -> str:
    name = site_key(site)
    if not name or name != str(site).strip().lower():
        raise ValueError(f"The site must be a registrable domain such as example.com. Got {site!r}.")
    if name in EXCLUDED_SITES:
        raise ValueError(f"{name} is excluded. Its data is never used to build or test.")
    parts = {part.casefold() for part in path.resolve().parts}
    if "smartcrew" in parts:
        raise ValueError("Files under smartcrew/ are SmartCrew's private data and are never imported.")
    names = [path.name]
    if path.is_dir():
        names.extend(child.name for child in path.rglob("*"))
    elif path.suffix.casefold() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names.extend(archive.namelist())
        except zipfile.BadZipFile:
            pass  # the parser reports a broken zip
    for item in names:
        if _names_excluded(item):
            raise ValueError(
                f"{item!r} names SmartCrew ({', '.join(sorted(EXCLUDED_SITES))}). SmartCrew's Search Console exports and "
                "files are private data and are never imported, whatever --site says."
            )
    prop = _export_property(path.name)
    if prop is not None and prop != name:
        raise ValueError(
            f"{path.name} is a Search Console export of the property {prop}, not of {name}. Nothing was imported. "
            "Check --site and the file."
        )
    return name


def _names_excluded(text: str | None) -> bool:
    """True when a file name, query or page names SmartCrew or an excluded site (any case, any sc-domain_ form)."""
    if not text:
        return False
    folded = str(text).casefold()
    if "smartcrew" in folded:
        return True
    return any(site in folded or site.split(".")[0] in folded for site in EXCLUDED_SITES)


def _export_property(file_name: str) -> str | None:
    """The property a Search Console UI export is named after ("example.com-Performance-on-Search-..."), or None."""
    match = re.match(r"^(?:sc-domain[_:])?(?:https?[_:]+(?:www\.)?)?([a-z0-9.-]+\.[a-z]{2,})[_/]?-performance-on-search",
                     file_name.casefold())
    if match is None:
        return None
    return site_key(match.group(1)) or None


def _file_digest(path: Path) -> str:
    if path.is_dir():
        listing = {
            str(child.relative_to(path)).replace("\\", "/"): sha256_bytes(child.read_bytes())
            for child in sorted(path.rglob("*"))
            if child.is_file()
        }
        return sha256_text(canonical_json(listing))
    return sha256_bytes(path.read_bytes())


def _store_copy(paths: ForecastPaths, section: str, site: str, source: Path, digest: str) -> Path:
    folder = paths.imports / section / site
    folder.mkdir(parents=True, exist_ok=True)
    suffix = "" if source.is_dir() else "".join(source.suffixes[-2:]) or ".dat"
    target = folder / f"{digest[:16]}{suffix}"
    if source.is_dir():
        if not target.exists():
            shutil.copytree(source, target)
    elif not target.exists():
        shutil.copyfile(source, target)
    if _file_digest(target) != digest:
        raise ValueError(f"{target} already exists with other content. Nothing was imported.")
    return target


def import_gsc(
    paths: ForecastPaths,
    scoreboard: Scoreboard,
    *,
    site: str,
    file: str | Path,
    kind: str,
    period_start: date | None = None,
    period_end: date | None = None,
    fetched_at: datetime | None = None,
    dimensions: Sequence[str] | None = None,
) -> ImportRecord:
    """Parse a Search Console export to check it, copy it under the data directory, and freeze its sha256.

    The numbers are REAL Search Console numbers. They are stored for the early-reading updater and
    are never used as graded outcomes. Kinds: ui (the UI export, zip or folder), daily (a table with
    a date column), range (Queries.csv or Pages.csv, with the period), api (searchanalytics.query JSON,
    with its `dimensions` in order) and index (indexed page counts).
    """
    from . import gsc

    source = Path(file)
    if not source.exists():
        raise FileNotFoundError(f"{source} does not exist")
    name = _refuse_client_data(site, source)
    if kind not in GSC_KINDS:
        raise ValueError(f"kind must be one of {', '.join(GSC_KINDS)}")
    rows: list[Any]
    notes: list[str] = []
    if kind == "ui":
        export = gsc.parse_ui_export(source, period_start=period_start, period_end=period_end, fetched_at=fetched_at)
        rows = export.all_rows()
    elif kind == "daily":
        table = gsc.parse_daily_table(source, fetched_at=fetched_at)
        rows, notes = list(table.rows), list(table.notes)
    elif kind == "range":
        if period_start is None or period_end is None:
            raise ValueError("A range table needs --period-start and --period-end.")
        table = gsc.parse_range_table(source, period_start=period_start, period_end=period_end, fetched_at=fetched_at)
        rows, notes = list(table.rows), list(table.notes)
    elif kind == "api":
        if not dimensions:
            raise ValueError("An API export needs its dimensions in order, for example --dimensions date,query.")
        table = gsc.parse_api_rows(json.loads(source.read_text(encoding="utf-8")), dimensions=list(dimensions),
                                   fetched_at=fetched_at, start_date=period_start, end_date=period_end)
        rows, notes = list(table.rows), list(table.notes)
    else:
        rows = list(gsc.parse_index_status(source))
    for row in rows:
        for cell in (getattr(row, "query", None), getattr(row, "page", None)):
            if _names_excluded(cell):
                raise ValueError(
                    f"The export holds a row naming SmartCrew ({cell!r}). It looks like SmartCrew's Search Console data, "
                    "which is never imported. Nothing was imported."
                )
    dates = sorted(day.isoformat() for day in (_row_day(row) for row in rows) if day is not None)
    digest = _file_digest(source)
    target = _store_copy(paths, "gsc", name, source, digest)
    freeze_name = f"gsc.{name}.{digest[:16]}"
    relative = str(target.relative_to(paths.root)).replace("\\", "/")
    payload = {"kind": kind, "site": name, "sha256": digest, "stored_path": relative, "rows": len(rows),
               "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
               "original_name": source.name}
    scoreboard.freeze(freeze_name, payload)
    return ImportRecord(kind, name, digest, relative, freeze_name, len(rows), payload["first_date"], payload["last_date"], tuple(notes))


def _row_day(row: Any) -> date | None:
    """The date of a Search Console row (data_date) or an indexing row (day)."""
    for name in ("data_date", "day"):
        value = getattr(row, name, None)
        if isinstance(value, date):
            return value
    return None


def import_form(paths: ForecastPaths, scoreboard: Scoreboard, *, file: str | Path) -> ImportRecord:
    """Check an agency form, copy it under the data directory, and freeze its sha256 in the scoreboard."""
    source = Path(file)
    if not source.exists():
        raise FileNotFoundError(f"{source} does not exist")
    from .agency_form import brand_terms as form_terms

    form = load_form(source)
    name = _refuse_client_data(form.site, source)
    terms = list(form_terms(form))
    notes: list[str] = []
    if name in _registered_sites(scoreboard):
        stored = sorted(_registered_brand_terms(scoreboard, name))
        if not _same_brand_terms(name, stored, terms):
            raise ScoreboardRefusal(
                f"{name} is already on the scoreboard with the brand terms {stored}, and this form gives {terms}. "
                "A registered site is never changed. Import the form before seeds register. Nothing was imported."
            )
    digest = _file_digest(source)
    target = _store_copy(paths, "forms", name, source, digest)
    freeze_name = f"{FORM_FREEZE_PREFIX}{name}.{digest[:16]}"
    relative = str(target.relative_to(paths.root)).replace("\\", "/")
    scoreboard.freeze(
        freeze_name,
        {"kind": "agency_form", "site": name, "sha256": digest, "stored_path": relative,
         "filled_on": form.filled_on.isoformat(), "filled_by": form.filled_by, "original_name": source.name,
         "brand_terms": terms},
    )
    notes.append("Brand terms for the brand search flag: " + (", ".join(terms) or "none") + ".")
    return ImportRecord("agency_form", name, digest, relative, freeze_name, 1, form.filled_on.isoformat(),
                        form.filled_on.isoformat(), tuple(notes))


# ----------------------------------------------------------------------------------------------
# Counts for status
# ----------------------------------------------------------------------------------------------


def scoreboard_counts(scoreboard: Scoreboard) -> dict[str, int]:
    tables = {"sites": SiteRow, "searches": SearchRow, "pulls": PullRow, "readings": ReadingRow,
              "forecasts": ForecastRow, "grades": GradeRow, "freezes": FreezeRow, "model_versions": ModelVersionRow}
    with Session(scoreboard.engine) as session:
        return {name: int(session.scalar(select(func.count()).select_from(model)) or 0) for name, model in tables.items()}


# ----------------------------------------------------------------------------------------------
# The demo: the whole measuring flow on the synthetic world
# ----------------------------------------------------------------------------------------------


@dataclass
class DemoResult:
    """Everything the demo produced. All of it is SYNTHETIC."""

    workdir: str
    seed: int
    manifest_hash: str
    register: dict[str, Any]
    reading_runs: list[dict[str, Any]]
    marker1: dict[str, Any]
    forecasts_recorded: int
    refusals: list[dict[str, Any]]
    grade_summary: dict[str, Any]
    stage_reports: dict[str, dict[str, Any]]
    marker_evaluations: dict[str, dict[str, Any]]
    chain: dict[str, Any]
    export_check: dict[str, Any]
    lines: list[str] = field(default_factory=list)
    label: str = SYNTHETIC
    training: dict[str, Any] = field(default_factory=dict)
    forecast_runs: list[dict[str, Any]] = field(default_factory=list)
    forecasts_skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "training": self.training,
            "forecast_runs": self.forecast_runs,
            "forecasts_skipped": self.forecasts_skipped,
            "workdir": self.workdir,
            "seed": self.seed,
            "manifest_hash": self.manifest_hash,
            "register": self.register,
            "reading_runs": self.reading_runs,
            "marker1": self.marker1,
            "forecasts_recorded": self.forecasts_recorded,
            "refusals": self.refusals,
            "grade_summary": self.grade_summary,
            "stage_reports": self.stage_reports,
            "marker_evaluations": self.marker_evaluations,
            "chain": self.chain,
            "export_check": self.export_check,
            "lines": self.lines,
        }


def persistence_forecast(
    scoreboard: Scoreboard,
    *,
    site: str,
    search: str,
    stage: str,
    as_of: date,
    location_code: int,
    device: str,
    now: datetime,
    target_url: str | None = None,
) -> tuple[Forecast, list[str]]:
    """The persistence placeholder for one (site, search, stage). PREDICTED, model version persistence-v0.

    The point is the site's clean position in the latest usable reading taken on or before `as_of`
    (NT50 when there is none, or when the site is not in it). The range is the point plus or minus
    PERSISTENCE_RANGE places, or NT50 alone. The label is out_of_reach for NT50 and contested
    otherwise: a placeholder has no probabilities to earn locked.
    """
    reading = latest_reading(scoreboard, search, location_code=location_code, device=device, as_of=as_of, not_after=now)
    point = reading.position_of(site) if reading is not None else NT50
    if point >= NT50:
        low = high = NT50
        label = "out_of_reach"
    else:
        low, high = max(1, point - PERSISTENCE_RANGE), min(NT50, point + PERSISTENCE_RANGE)
        label = "contested"
    forecast = Forecast(
        site=site,
        search=search,
        stage=stage,
        as_of=as_of,
        point=point,
        range_low=low,
        range_high=high,
        label=label,
        model_version=PERSISTENCE_MODEL,
        target_url=target_url,
        notes={"placeholder": "persistence", "reading_id": reading.reading_id if reading is not None else None},
    )
    return forecast, [reading.reading_id] if reading is not None else []


def demo(
    seed: int = DEMO_SEED,
    *,
    workdir: str | Path,
    sites: int = DEMO_SITES,
    pulls: int = DEFAULT_PULLS,
) -> DemoResult:
    """Run the whole flow on a small synthetic world. Every number it prints is SYNTHETIC.

    Steps: build a world; train a SYNTHETIC bundle (trust, placement, updater) on new sites whose day 90
    comes before the first demo launch, so the bundle knows nothing later than any forecast date; write
    and freeze a seed manifest from the `sites` latest-launching new sites; import their agency forms and
    register them; freeze a marker 1 sample and read it on two days 24 hours apart; read every site's
    searches on days 0, 7, 14, 30, 60 and 90 after its launch; make the upload forecast on launch day
    and update it on each stage day through the live path (forecasting.upload_or_update, features from
    the stored readings); grade at day 90; show that a late forecast, a replacement and an edit are
    refused; verify, export and compare; print the stage reports with all four baselines and the marker
    evaluations, which can never pass because the data is synthetic.
    """
    from . import forecasting
    from .features import synthetic_strength
    from .synth import DEFAULT_KNOBS, World

    folder = Path(workdir)
    folder.mkdir(parents=True, exist_ok=True)
    database = folder / "scoreboard.db"
    if database.exists():
        raise ValueError(f"{database} already exists. The demo runs on a fresh folder.")
    paths = ForecastPaths(root=folder)

    world = World(
        seed,
        niches=1,
        searches_per_niche=DEMO_SEARCHES_PER_NICHE,
        established_sites=DEMO_ESTABLISHED_SITES,
        new_sites=sites + DEMO_TRAIN_POOL,
        knobs=replace(DEFAULT_KNOBS, targets_per_site=DEMO_TARGETS_PER_SITE),
    )
    reachable = sorted((info for info in world.new_sites.values() if info.launch_date + timedelta(days=90) < world.end),
                       key=lambda info: (info.launch_date, info.site))
    if len(reachable) < sites or sites < 1:
        raise ValueError("The demo needs at least one new site that reaches day 90 inside the world.")
    new_sites = reachable[-sites:]
    earliest = new_sites[0].launch_date
    train_sites = [info.site for info in reachable if info.launch_date + timedelta(days=90) < earliest]
    trained = forecasting.train_synthetic(paths, world, name=DEMO_BUNDLE, sites=train_sites)
    bundle = trained.bundle
    assert bundle is not None
    strength = synthetic_strength(world)
    marker1_a = max(world.start, earliest - timedelta(days=DEMO_MARKER1_DAYS_BEFORE_LAUNCH))
    marker1_b = marker1_a + timedelta(days=1)
    clock = VirtualClock(_at(marker1_a - timedelta(days=3), 9))

    info_by_text = {info.text: info for info in world.searches}
    manifest = SeedManifest(
        sites=tuple(
            SeedSite(
                domain=info.site,
                niche=info.niche,
                launch_date=info.launch_date,
                pages=int(info.pages_at_launch),
                searches=tuple(
                    SeedSearch(
                        search=text_,
                        tercile=info_by_text[text_].tercile,
                        difficulty_value=float(info_by_text[text_].difficulty),
                        difficulty_source=DEMO_DIFFICULTY_METRIC,
                        volume=int(info_by_text[text_].volume),
                        target_url=world.url_of(info.site, text_),
                    )
                    for text_ in info.targets
                ),
                built_by="SYNTHETIC world generator",
            )
            for info in new_sites
        ),
        location_code=world.location_code,
        difficulty_metric=DEMO_DIFFICULTY_METRIC,
        created_at=utc_iso(clock.now()),
        allow_shared=True,  # the synthetic world gives sites of one niche overlapping targets, and says so
    )
    manifest_path = folder / "seed_manifest.json"
    digest = manifest.freeze(manifest_path)
    seeds_module.load_frozen(manifest_path)

    board = Scoreboard(f"sqlite:///{database}", clock=clock.now)
    _skip_fsync(board)
    lines: list[str] = []

    def say(*texts: str) -> None:
        for chunk in texts:
            for line in str(chunk).splitlines():
                if line.strip():
                    lines.append(f"SYNTHETIC: {line.strip()}")

    try:
        say(f"Demo on a simulated world, seed {seed}. Nothing here is a real Google result and nothing is accuracy.")
        say(f"Trained bundle {bundle.version} on {len(train_sites)} other new sites of the same world, whose day 90 "
            f"outcomes all come before the first demo launch. Training cutoff {bundle.training_cutoff}.")
        say(f"Placement rows {trained.counts.get('upload_rows')}. Updater in use: "
            f"{(trained.counts.get('updater') or {}).get('in_use')}.")
        if trained.marker2 and trained.marker2.get("verdict"):
            say(markers_module.render_marker_text(trained.marker2["verdict"]))
        elif trained.marker2:
            say(f"Marker 2 on held-out training sites: not measured, {trained.marker2.get('note')}.")
        say(f"Seed manifest frozen with hash {digest} at {manifest_path}.")
        forms_dir = folder / "forms"
        forms_dir.mkdir(parents=True, exist_ok=True)
        for info in new_sites:
            form_path = forms_dir / f"{info.site}.json"
            form_path.write_text(json.dumps(world.agency_form(info.site), indent=2, sort_keys=True), encoding="utf-8")
            import_form(paths, board, file=form_path)
        say(f"Imported {len(new_sites)} agency forms before registering, so their brand names flag brand searches.")
        registered = register_manifest(board, manifest, source="synthetic", paths=paths)
        say(f"Registered {registered.sites_added} sites and {registered.searches_added} searches. Manifest hash frozen as {registered.freeze_name}.")

        specs_all = manifest_specs(manifest)
        sample, _ = freeze_marker1_sample(board, [spec.search for spec in specs_all], n=len(specs_all))
        say(f"Marker 1 sample of {len(sample.searches)} searches frozen with sha256 {sample.sha256}.")
        source = make_source("synthetic", None, None, folder / "raw", world=world, clock=clock)

        spec_by_key = {search_key(spec.search): spec for spec in specs_all}
        reads: dict[datetime, set[str]] = {}
        for day in (marker1_a, marker1_b):
            reads.setdefault(_at(day, DEMO_READ_HOUR), set()).update(search_key(s) for s in sample.searches)
        forecast_events: dict[datetime, list[tuple[SeedSite, str]]] = {}
        for site in manifest.sites:
            assert site.launch_date is not None
            for offset in DEMO_READING_DAYS:
                reads.setdefault(_at(site.launch_date + timedelta(days=offset), DEMO_READ_HOUR), set()).update(
                    search_key(item.search) for item in site.searches
                )
            for stage, offset in STAGE_DAYS.items():
                forecast_events.setdefault(_at(site.launch_date + timedelta(days=offset), DEMO_FORECAST_HOUR), []).append((site, stage))

        runs: list[dict[str, Any]] = []
        forecast_runs: list[dict[str, Any]] = []
        recorded = 0
        skipped = 0
        versions: dict[str, str] = {}
        for moment in sorted(set(reads) | set(forecast_events)):
            clock.set(moment)
            if moment in reads:
                specs = [spec_by_key[key] for key in sorted(reads[moment])]
                summary = take_readings(board, source, specs, pulls=pulls, gap_seconds=MIN_PULL_GAP_SECONDS, clock=clock)
                runs.append(summary.to_dict())
            for site, stage in sorted(forecast_events.get(moment, []), key=lambda item: (item[0].domain, item[1])):
                assert site.launch_date is not None
                as_of = site.launch_date + timedelta(days=STAGE_DAYS[stage])
                run = forecasting.upload_or_update(board, paths, bundle=bundle, site=site.domain, stage=stage, day=as_of,
                                                   now=clock.now(), record=True, strength=strength)
                if run.blocked or run.missed or run.refused:
                    raise RuntimeError(f"The demo forecast run was stopped: {run.blocked or run.missed or run.refused}")
                recorded += len(run.recorded)
                skipped += len(run.skipped)
                for forecast in run.forecasts:
                    versions.setdefault(stage, forecast.model_version)
                forecast_runs.append({"site": run.site, "stage": stage, "as_of": as_of.isoformat(),
                                      "recorded": len(run.recorded), "skipped": len(run.skipped), "gate": run.gate["tag"]})
        say(
            f"Took {len(runs)} reading runs: {sum(r['readings'] for r in runs)} readings from {sum(r['pulls_returned'] for r in runs)} pulls. "
            f"{sum(r['pulls_dropped'] for r in runs)} pulls were dropped by the filter and {sum(r['degraded'] for r in runs)} readings are degraded."
        )
        say(f"Recorded {recorded} forecasts through the live path (PREDICTED, bundle {bundle.version}): the upload forecast on "
            "launch day and one update on each stage day, each built from the readings stored on or before its date.")
        if skipped:
            say(f"Skipped {skipped} cells with no clean reading on or before their date. They are not called NOT IN TOP 50.")

        m1 = marker1(board, list(sample.searches), location_code=manifest.location_code, device=manifest.device,
                     a_date=marker1_a, b_date=marker1_b)
        say(*marker1_lines(m1))

        last_launch = max(site.launch_date for site in manifest.sites if site.launch_date is not None)
        today = last_launch + timedelta(days=90)
        clock.set(_at(today, 18))
        graded = grade(board, today)
        say(grade_job.render_summary(graded))

        refusals = _demo_refusals(board, manifest, versions)
        for item in refusals:
            say(f"Refused as it should be, {item['attempt']}: {item['refusal']}")

        status = board.verify()
        say("The hash chains verify." if status.ok else "The hash chains do NOT verify: " + " ".join(status.problems))
        export_dir = folder / "export"
        export_scoreboard(board, export_dir)
        comparison = verify_against_export(board, export_dir)
        say("The export is a prefix of the database." if comparison.ok else "The export check failed: " + " ".join(comparison.problems))

        reports: dict[str, dict[str, Any]] = {}
        evaluations: dict[str, dict[str, Any]] = {}
        training_note = (f"The band prior by difficulty is fitted on the bundle's {len(trained.training_cells)} training "
                         f"cells from {len(train_sites)} training sites, none of them graded here.")
        for stage in STAGE_DAYS:
            result = stage_marker(board, source="synthetic", stage=stage, as_of=today,
                                  training_cells=trained.training_cells, training_note=training_note)
            reports[stage] = result["report"]
            say(report_module.render_text(result["report"]))
            if result["evaluation"] is not None:
                evaluations[str(result["marker"])] = result["evaluation"]
                say(markers_module.render_marker_text(result["evaluation"]))
        evaluations["1"] = m1["evaluation"]
        say("End of the demo. Every number above is SYNTHETIC and tests the code only.")
        return DemoResult(
            workdir=str(folder),
            seed=seed,
            manifest_hash=digest,
            register=registered.to_dict(),
            reading_runs=runs,
            marker1=m1,
            forecasts_recorded=recorded,
            refusals=refusals,
            grade_summary=graded,
            stage_reports=reports,
            marker_evaluations=evaluations,
            chain=status.to_dict(),
            export_check=comparison.to_dict(),
            lines=lines,
            training=trained.to_dict(),
            forecast_runs=forecast_runs,
            forecasts_skipped=skipped,
        )
    finally:
        board.close()


def _skip_fsync(board: Scoreboard) -> None:
    """The demo database is throwaway SYNTHETIC data, so it skips the disk flush on every commit. Real scoreboards never do."""

    @event.listens_for(board.engine, "connect")
    def _no_sync(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()

    board.engine.dispose()  # open connections are replaced by ones that carry the pragma


def _demo_refusals(board: Scoreboard, manifest: SeedManifest, versions: Mapping[str, str]) -> list[dict[str, Any]]:
    """Try a late forecast, a replacement and an edit. Each must be refused. Returns what each refusal said.

    `versions` maps a stage to the model version the demo recorded there, so the replacement really
    names the same (site, search, stage, model version) as a stored forecast.
    """
    site = sorted(manifest.sites, key=lambda item: site_key(item.domain))[0]
    item = site.searches[0]
    assert site.launch_date is not None
    out: list[dict[str, Any]] = []
    late = Forecast(site=site.domain, search=item.search, stage="upload", as_of=site.launch_date + timedelta(days=30),
                    point=NT50, range_low=NT50, range_high=NT50, label="out_of_reach", model_version=versions["upload"])
    out.append(_expect_refusal("a late upload forecast dated 30 days after launch", lambda: board.record_forecast(late)))
    again = Forecast(site=site.domain, search=item.search, stage="d30", as_of=site.launch_date + timedelta(days=30),
                     point=1, range_low=1, range_high=3, label="contested", model_version=versions["d30"])
    out.append(_expect_refusal("a second d30 forecast that would replace the first", lambda: board.record_forecast(again)))

    def edit() -> None:
        with board.engine.begin() as connection:
            connection.execute(text("UPDATE forecasts SET point = 1 WHERE seq = 1"))

    out.append(_expect_refusal("an edit of the first stored forecast", edit))
    return out


def _expect_refusal(attempt: str, action: Callable[[], Any]) -> dict[str, Any]:
    try:
        action()
    except ScoreboardRefusal as error:
        return {"attempt": attempt, "refused": True, "refusal": str(error)}
    except DBAPIError as error:
        message = str(getattr(error, "orig", error)) or type(error).__name__
        return {"attempt": attempt, "refused": True, "refusal": f"the database refused it ({message})."}
    raise RuntimeError(f"The scoreboard accepted {attempt}. It must refuse it.")
