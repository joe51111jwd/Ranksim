"""Build step 4 of the plan: the training set (data 2 to 7), as functions the command line calls.

What is REAL here: past result pages, traffic curves and exact positions are
what DataForSEO returned, saved raw before parsing. The cohort comes from
sources/newsites.py (REAL observations plus an ESTIMATED launch date with its
uncertainty). The search universe comes from sources/universe.py (REAL vendor
metrics). Nothing here is PREDICTED. Target searches are chosen by a frozen,
label-blind rule. The split is a frozen rule. Both are code with a hash.
The synthetic path builds the same tables from forecast.synth.World. Those
tables are SYNTHETIC and are never reported as accuracy.

Money rules (docs/plan/IMPLEMENTATION_SPEC.md section 5):

- Every paid function is a dry run unless execute=True. A dry run prints the
  cost from budget.py and calls nothing.
- Every paid call goes through the client's SpendLedger. With no approval the
  ledger refuses. A kind without a recorded $1 sample may not spend more than
  $1 in total.
- A pull is chunked and resumable. Its dates are frozen in a job manifest on
  the first run, so a later run asks the same requests and the client reads
  the saved raw answers instead of paying again.

The audit points handled here (docs/plan/PLAN_AUDIT.md):

- B2: target searches for a past site come only from its launch-era page text,
  through AsOfGuard. Text dated after the launch month raises LeakError. The
  rule never sees rankings, so searches where the site never ranks are in.
- B4: dead and never-crawled sites stay in the cohort table. Missing traffic is
  missing, not zero.
- B5: the split is by launch cohort in time, then by site, and no search group
  lands on both sides.
- B9: sites 24 to 30 months old need 30 months of past result pages. The cost
  table prints both windows and counts the sites each window leaves without a
  launch-month page.
- E4: the history window is in doubt (12 months on one DataForSEO page, back to
  August 2021 on another). The $1 sample asks for 30 months and records the
  earliest month that came back.
- E5: Labs data is country level only.
"""

from __future__ import annotations

import gzip
import hashlib
import inspect
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .asof import AsOfGuard, Dated, add_months, month_start, utc_iso
from .budget import (
    HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE,
    KIND_HISTORICAL_SERPS,
    KIND_HISTORICAL_TRAFFIC,
    KIND_RANKED_KEYWORDS,
    LABS_MAX_TARGETS_PER_TASK,
    SAMPLE_MAX_USD,
    BudgetError,
    SpendLedger,
    historical_serps_cost,
    historical_serps_estimate,
    historical_traffic_cost,
    ranked_keywords_cost,
    usd_text,
)
from .chain import canonical_json, sha256_text
from .config import EXCLUDED_SITES, forecast_paths
from .sources import dfs_samples as dfs
from .sources import panel as panel_mod
from .sources.universe import SearchRow, normalise
from ..site_history import site_key
from .types import SYNTHETIC

__all__ = [
    "CELL_COLUMNS",
    "POSITION_COLUMNS",
    "POSITION_TARGET_COLUMNS",
    "TRAFFIC_MONTH_COLUMNS",
    "TRAFFIC_TARGET_COLUMNS",
    "PullPlan",
    "PullReport",
    "SamplesReport",
    "SplitRule",
    "TargetRule",
    "TargetSelection",
    "assign_split",
    "cohort_cells",
    "plan_pulls",
    "pull_exact_positions",
    "pull_past_result_pages",
    "pull_traffic_curves",
    "run_samples",
    "search_group",
    "select_targets",
    "synthetic_training_set",
    "token_overlap_score",
]

DEFAULT_WINDOWS = (24, 30)
DEFAULT_ROWS_PER_SITE = 100
DEFAULT_LOCATION_CODE = 2840  # United States. Labs data is country level only (PLAN_AUDIT E5).
DEFAULT_LANGUAGE = "en"
DEFAULT_LOCALE = "US_en"
# The earliest month Historical Bulk Traffic Estimation accepts (DataForSEO docs, map section 5.2).
TRAFFIC_MIN_DATE = date(2020, 10, 1)
# Sample sizes the plan's checks need. Each sample is capped at SAMPLE_MAX_USD by budget.py.
SAMPLE_MONTHS_BACK = 30
SAMPLE_RANKED_ROWS = 100
# The two claims about how far past result pages go (PLAN_AUDIT E4).
HISTORY_CLAIM_MONTHS = 12
HISTORY_CLAIM_START = date(2021, 8, 1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_root() -> Path:
    return forecast_paths().cache / "trainingset"


def default_samples_path() -> Path:
    return forecast_paths().root / "samples" / "labs_samples.json"


def _get(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _months_between(earlier: date, later: date) -> int:
    return (later.year * 12 + later.month) - (earlier.year * 12 + earlier.month)


def _site(value: Any) -> str:
    """The registrable site of a domain, host, origin or URL, lower case ("" when empty).

    Every site a pull, a sample or the target selection receives goes through
    this before the EXCLUDED_SITES check and before de-duplicating, so
    "www.SmartCrewHQ.com" or "https://smartcrewhq.com/" are dropped like
    "smartcrewhq.com". An already-normalised site comes back unchanged.
    """
    text = str(value or "").strip().lower()
    return site_key(text) if text else ""


def _wanted_sites(values: Iterable[Any]) -> list[str]:
    """Normalised sites in first-seen order, de-duplicated, with excluded and empty sites removed."""
    out: dict[str, None] = {}
    for value in values:
        site = _site(_get(value, "site") if not isinstance(value, str) else value)
        if site and site not in EXCLUDED_SITES:
            out.setdefault(site, None)
    return list(out)


def _history_window(day: date, months: int) -> tuple[date, date, int]:
    """(date_from, date_to, calendar months covered) of a past-result-pages pull asked on `day` for `months` months.

    The window is `months` calendar months ending with the month of `day`:
    from the first day of the month months-1 back to `day`. The month count
    uses the client's own inclusive formula (dataforseo.historical_serps), so
    the dry run prices exactly what the executed call reserves.
    """
    if months < 1:
        raise ValueError("months must be 1 or more")
    start = add_months(day, -(months - 1))
    covered = (day.year - start.year) * 12 + (day.month - start.month) + 1
    return start, day, covered


def _as_date(value: Any) -> date | None:
    """A date from a date, datetime, pandas or numpy timestamp, or ISO text. None, NaN and NaT give None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ----------------------------------------------------------------------------------------------
# The cost table.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CostLine:
    """One line of the cost table. `usd` comes from budget.py, never typed here."""

    window_months: int | None
    item: str
    kind: str
    units: str
    usd: Decimal
    basis: str


@dataclass
class PullPlan:
    """The cost of plan step 4 for 24 and 30 months of past result pages. A plan, not a charge."""

    searches: int
    sites: int
    rows_per_site: int
    windows: tuple[int, ...]
    lines: list[CostLine]
    totals_upper: dict[int, Decimal]
    totals_plan_reading: dict[int, Decimal]
    sites_with_age: int
    sites_without_launch_page: dict[int, int]
    sites_24_to_30_months: int
    sites_over_30_months: int

    def table(self) -> list[dict[str, Any]]:
        return [{**asdict(line), "usd": str(line.usd)} for line in self.lines]

    def text(self) -> str:
        out = [f"Cost plan for {self.searches} searches, {self.sites} sites, {self.rows_per_site} rows per site."]
        for line in self.lines:
            window = f"{line.window_months} months, " if line.window_months else ""
            out.append(f"{window}{line.item}: {usd_text(line.usd)} ({line.units}; {line.basis}).")
        for window in self.windows:
            out.append(
                f"Total with {window} months: {usd_text(self.totals_upper[window])} at the upper estimate, "
                f"{usd_text(self.totals_plan_reading[window])} at one page per month."
            )
        if self.sites_with_age:
            for window in self.windows:
                out.append(f"With {window} months, {self.sites_without_launch_page[window]} of {self.sites_with_age} dated sites have no launch-month page.")
            out.append(f"Sites 24 to 30 months old: {self.sites_24_to_30_months}. They need the 30-month window (PLAN_AUDIT B9).")
            out.append(f"Sites older than 30 months: {self.sites_over_30_months}. No window here reaches their launch month.")
        return "\n".join(out)


def _count(value: int | Sequence[Any]) -> int:
    return int(value) if isinstance(value, int) else len(value)


def plan_pulls(
    searches: int | Sequence[Any],
    sites: int | Sequence[Any],
    months: Sequence[int] = DEFAULT_WINDOWS,
    rows_per_site: int = DEFAULT_ROWS_PER_SITE,
    *,
    position_sites: int | None = None,
    today: date | None = None,
) -> PullPlan:
    """The cost table for past result pages (each window in `months`), traffic curves, exact positions and the $1 samples.

    `sites` may be a count or cohort rows. With rows that carry launch_date (or
    registered_on when no launch date is known) the plan also counts how many
    sites each window leaves without a launch-month page. `position_sites` is
    how many sites get exact positions (the plan pulls 2,000 of the 20,000);
    it defaults to all sites.
    """
    n_searches = _count(searches)
    n_sites = _count(sites)
    windows = tuple(sorted({int(m) for m in months}))
    if not windows or windows[0] < 1:
        raise ValueError("give at least one window of 1 month or more")
    lines: list[CostLine] = []
    upper: dict[int, Decimal] = {}
    reading: dict[int, Decimal] = {}
    traffic = historical_traffic_cost(n_sites)
    n_position_sites = n_sites if position_sites is None else int(position_sites)
    positions = ranked_keywords_cost(n_position_sites, rows_per_site)
    samples = SAMPLE_MAX_USD * 3
    for window in windows:
        one = historical_serps_cost(n_searches, window)
        high = historical_serps_estimate(window, n_searches)
        lines.append(CostLine(window, "Past result pages, one page per month (the plan's reading)", KIND_HISTORICAL_SERPS,
                              f"{n_searches} searches x {window} pages", one, "budget.historical_serps_cost"))
        if high != one:
            lines.append(CostLine(window, f"Past result pages, upper estimate at {HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE} pages per month",
                                  KIND_HISTORICAL_SERPS, f"{n_searches} searches x {window * HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE} pages",
                                  high, "budget.historical_serps_estimate"))
        upper[window] = high + traffic + positions + samples
        reading[window] = one + traffic + positions + samples
    lines.append(CostLine(None, "Traffic curves", KIND_HISTORICAL_TRAFFIC, f"{n_sites} sites", traffic, "budget.historical_traffic_cost"))
    lines.append(CostLine(None, "Exact positions", KIND_RANKED_KEYWORDS, f"{n_position_sites} sites x {rows_per_site} rows", positions,
                          "budget.ranked_keywords_cost"))
    lines.append(CostLine(None, "The three $1 samples (caps, not estimates)", "samples", "3 samples", samples, "budget.SAMPLE_MAX_USD"))
    day = today or _utcnow().date()
    ages: list[int] = []
    if not isinstance(sites, int):
        for row in sites:
            start = _as_date(_get(row, "launch_date")) or _as_date(_get(row, "registered_on"))
            if start is not None:
                ages.append(_months_between(start, day))
    # A window of `window` months covers the month of `day` and the window-1 months before it
    # (_history_window), so a site whose launch month is `age` months back has a launch-month page iff age < window.
    without = {window: sum(1 for age in ages if age + 1 > window) for window in windows}
    return PullPlan(
        searches=n_searches, sites=n_sites, rows_per_site=rows_per_site, windows=windows, lines=lines, totals_upper=upper,
        totals_plan_reading=reading, sites_with_age=len(ages), sites_without_launch_page=without,
        sites_24_to_30_months=sum(1 for age in ages if 24 <= age < 30), sites_over_30_months=sum(1 for age in ages if age >= 30),
    )


# ----------------------------------------------------------------------------------------------
# The three $1 samples.
# ----------------------------------------------------------------------------------------------


@dataclass
class SampleAnswer:
    """One $1 sample: its question, what it would ask (dry run) or what it found (executed), and the plain answer."""

    kind: str
    question: str
    cap_usd: Decimal
    planned_units: int
    planned_usd: Decimal
    executed: bool
    from_ledger: bool
    findings: dict[str, Any] | None
    answer: str


@dataclass
class SamplesReport:
    dry_run: bool
    answers: list[SampleAnswer]
    json_path: str | None

    def lines(self) -> list[str]:
        out = ["Dry run. Nothing was sent and nothing was spent." if self.dry_run else "Samples run."]
        for item in self.answers:
            out.append(f"{item.kind}: {item.question} {item.answer}")
        return out


def _traffic_sample_fit(cap: Decimal) -> int:
    fit = 0
    while fit < LABS_MAX_TARGETS_PER_TASK and historical_traffic_cost(fit + 1) <= cap:
        fit += 1
    return fit


def _answer_traffic(findings: Mapping[str, Any]) -> str:
    asked, with_data = findings.get("sites_asked"), findings.get("sites_with_data")
    share = findings.get("share_with_data")
    if share is None:
        return f"The call failed with status {findings.get('status_code')}. No answer yet."
    text = f"{with_data} of {asked} small new sites have at least one month of traffic or SERP presence ({share:.1%})."
    if share < 0.5:
        text += " Most show nothing, so past result pages (source 2) become the main way to find new sites."
    else:
        text += " Most show something, so traffic curves can find new sites."
    return text


def _answer_serps(findings: Mapping[str, Any], today: date) -> str:
    earliest = findings.get("earliest_snapshot")
    if not earliest:
        return "No past result page came back. No answer yet."
    first = date.fromisoformat(str(earliest))
    back = _months_between(first, today)
    claims = []
    if back > HISTORY_CLAIM_MONTHS:
        claims.append(f"more than the {HISTORY_CLAIM_MONTHS} months the API page states")
    else:
        claims.append(f"no more than the {HISTORY_CLAIM_MONTHS} months the API page states")
    if first <= add_months(HISTORY_CLAIM_START, 1):
        claims.append(f"consistent with the help article's start of {HISTORY_CLAIM_START}")
    text = (f"The earliest page is {earliest}, {back} months back: {', '.join(claims)}. "
            f"24 months {'are' if back >= 24 else 'are not'} covered. 30 months {'are' if back >= 30 else 'are not'} covered. "
            f"The deepest organic rank seen is {findings.get('deepest_organic_rank')}, over {findings.get('distinct_months')} distinct months.")
    if findings.get("refused_start_dates"):
        text += f" DataForSEO refused the start date for {len(findings['refused_start_dates'])} search(es)."
    return text


def _answer_ranked(findings: Mapping[str, Any]) -> str:
    return (f"{findings.get('sites_with_rows')} of {findings.get('sites_asked')} sites have ranked-keyword rows "
            f"({findings.get('rows')} rows, deepest rank {findings.get('deepest_rank')}).")


def run_samples(
    client: Any | None,
    *,
    small_sites: Sequence[str],
    keywords: Sequence[str],
    ranked_targets: Sequence[str] | None = None,
    location_code: int = DEFAULT_LOCATION_CODE,
    language_code: str = DEFAULT_LANGUAGE,
    months_back: int = SAMPLE_MONTHS_BACK,
    execute: bool = False,
    out_path: Path | None = None,
    today: date | None = None,
) -> SamplesReport:
    """The three $1 samples of the plan's "Checks before spending". Dry run unless execute=True.

    1. Historical traffic for very small new sites: do they show up at all (source 4)?
    2. Historical SERPs asked for `months_back` months: how deep and how far back (source 2)?
    3. Ranked keywords for the same small sites: do they have exact positions (source 5)?

    Executed, each sample runs through the client's sample helper, which spends
    at most $1 through the ledger and records its findings there. A sample
    already recorded in the ledger is read back, not paid again. The answers
    are written to a small JSON under the forecast data directory.
    """
    day = today or _utcnow().date()
    sites = _wanted_sites(small_sites)
    targets = _wanted_sites(ranked_targets) if ranked_targets is not None else list(sites)
    words = list(dict.fromkeys(keywords))
    cap = SAMPLE_MAX_USD
    fit_traffic = min(len(sites), _traffic_sample_fit(cap))
    per_serp = historical_serps_estimate(months_back + 1)
    fit_serps = min(len(words), int(cap // per_serp))
    per_ranked = ranked_keywords_cost(1, SAMPLE_RANKED_ROWS)
    fit_ranked = min(len(targets), int(cap // per_ranked))
    plans = [
        (KIND_HISTORICAL_TRAFFIC, "Do very small new sites show up in the traffic history?", fit_traffic,
         historical_traffic_cost(fit_traffic) if fit_traffic else Decimal(0)),
        (KIND_HISTORICAL_SERPS, f"How deep and how far back do past result pages go (asked for {months_back} months)?", fit_serps,
         per_serp * fit_serps),
        (KIND_RANKED_KEYWORDS, "Do very small new sites have exact positions?", fit_ranked, per_ranked * fit_ranked),
    ]
    answers: list[SampleAnswer] = []
    if not execute:
        for kind, question, units, usd in plans:
            answers.append(SampleAnswer(kind, question, cap, units, usd, False, False, None,
                                        f"Would ask {units} item(s) for up to {usd_text(usd)}. Not sent."))
        return SamplesReport(True, answers, None)
    if client is None or getattr(client, "ledger", None) is None:
        raise BudgetError("Refused: execute=True needs a DataForSEO client with a spend ledger.")
    ledger: SpendLedger = client.ledger
    for kind, question, units, usd in plans:
        recorded = ledger.sample_findings(kind)
        if recorded is not None:
            findings, from_ledger = recorded, True
        else:
            if units == 0:
                raise ValueError(f"the {kind} sample has nothing to ask: give sites or keywords")
            if kind == KIND_HISTORICAL_TRAFFIC:
                findings = client.sample_historical_traffic(sites, location_code=location_code, language_code=language_code).findings
            elif kind == KIND_HISTORICAL_SERPS:
                findings = client.sample_historical_serps(words, location_code, language_code, months_back=months_back).findings
            else:
                findings = client.sample_ranked_keywords(targets, limit=SAMPLE_RANKED_ROWS, location_code=location_code,
                                                         language_code=language_code).findings
            from_ledger = False
        if kind == KIND_HISTORICAL_TRAFFIC:
            answer = _answer_traffic(findings)
        elif kind == KIND_HISTORICAL_SERPS:
            answer = _answer_serps(findings, day)
        else:
            answer = _answer_ranked(findings)
        answers.append(SampleAnswer(kind, question, cap, units, usd, True, from_ledger, dict(findings), answer))
    target = out_path or default_samples_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "written_at": utc_iso(_utcnow()),
        "ledger": str(ledger.path),
        "answers": [{**asdict(a), "cap_usd": str(a.cap_usd), "planned_usd": str(a.planned_usd)} for a in answers],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return SamplesReport(False, answers, str(target))


# ----------------------------------------------------------------------------------------------
# Chunked, resumable pulls.
# ----------------------------------------------------------------------------------------------


@dataclass
class PullReport:
    """What a pull did. A dry run has planned_usd and nothing else. Spent comes from the ledger."""

    kind: str
    job: str
    dry_run: bool
    units: int
    planned_usd: Decimal
    calls: int = 0
    calls_from_cache: int = 0
    spent_usd: Decimal = Decimal(0)
    failures: list[dict[str, Any]] = field(default_factory=list)
    raw_paths: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    rows: int = 0
    manifest: dict[str, Any] = field(default_factory=dict)

    def lines(self) -> list[str]:
        if self.dry_run:
            return [f"Dry run of {self.kind}: {self.units} item(s), planned {usd_text(self.planned_usd)}. Nothing was sent."]
        return [
            f"{self.kind}: {self.calls} call(s), {self.calls_from_cache} read back from saved raw answers, "
            f"{usd_text(self.spent_usd)} spent, {len(self.failures)} failure(s), {self.rows} row(s) normalised."
        ]


def _job_name(kind: str, payload: Mapping[str, Any]) -> str:
    return f"{kind}-{sha256_text(canonical_json(payload))[:12]}"


def _manifest(root: Path, job: str, kind: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The frozen parameters of a job. Written on the first run and read back after, so dates never move."""
    path = root / "jobs" / f"{job}.json"
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("kind") != kind:
            raise ValueError(f"job {job} is a {stored.get('kind')} job, not {kind}")
        return stored
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = {"job": job, "kind": kind, "created_at": utc_iso(_utcnow()), **params}
    path.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
    return stored


def _require_client(client: Any | None) -> SpendLedger:
    if client is None or getattr(client, "ledger", None) is None:
        raise BudgetError("Refused: execute=True needs a DataForSEO client with a spend ledger. Nothing was sent.")
    return client.ledger


def _write_parquet(frame: pd.DataFrame, path: Path, schema: pa.Schema) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)
    return str(path)


def pull_past_result_pages(
    client: Any | None,
    searches: Sequence[Any],
    *,
    months: int = 24,
    location_code: int = DEFAULT_LOCATION_CODE,
    language_code: str = DEFAULT_LANGUAGE,
    locale: str = DEFAULT_LOCALE,
    execute: bool = False,
    job: str | None = None,
    root: Path | None = None,
    panel_dir: Path | None = None,
    today: date | None = None,
) -> PullReport:
    """Past result pages (Historical SERPs) for each search, then into the panel through the existing parser.

    Raw answers are saved by the client before parsing. Normalising decompresses
    each saved answer to a .json file and hands it to panel.build_panel, the
    same parser the free samples use.

    `months` is the number of calendar months asked for, the month of today
    included (_history_window). The planned cost is priced from the same dates
    the request sends, so the dry run equals what the client reserves. The
    keywords are de-duplicated and sorted before the job is named, so the same
    searches in another order resume the same job and pay nothing again. A job
    whose manifest already exists keeps its frozen dates, and its cost is
    priced from them.
    """
    keywords = sorted({str(k) for k in (_get(s, "keyword") if not isinstance(s, str) else s for s in searches) if k})
    name = job or _job_name("historical_serps", {"keywords": keywords, "months": months, "location": location_code, "language": language_code})
    base = Path(root) if root else default_root()
    day = today or _utcnow().date()
    date_from, date_to, covered = _history_window(day, months)
    stored_path = base / "jobs" / f"{name}.json"
    if stored_path.is_file():  # read only: a dry run never writes, but it prices the frozen dates a resumed run will ask
        stored = json.loads(stored_path.read_text(encoding="utf-8"))
        if stored.get("kind") == KIND_HISTORICAL_SERPS and stored.get("date_from") and stored.get("date_to"):
            frozen_from, frozen_to = date.fromisoformat(stored["date_from"]), date.fromisoformat(stored["date_to"])
            covered = max(1, (frozen_to.year - frozen_from.year) * 12 + (frozen_to.month - frozen_from.month) + 1)
            keywords = list(stored.get("keywords", keywords))
    planned = historical_serps_estimate(covered, len(keywords)) if keywords else Decimal(0)
    if not execute:
        return PullReport(KIND_HISTORICAL_SERPS, name, True, len(keywords), planned)
    ledger = _require_client(client)
    manifest = _manifest(base, name, KIND_HISTORICAL_SERPS, {
        "keywords": keywords, "location_code": location_code, "language_code": language_code, "locale": locale,
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "months": months,
    })
    report = PullReport(KIND_HISTORICAL_SERPS, name, False, len(manifest["keywords"]), planned, manifest=manifest)
    spent_before = ledger.spent_for_kind(KIND_HISTORICAL_SERPS)
    json_paths: list[Path] = []
    for keyword in manifest["keywords"]:
        result = client.historical_serps(keyword, manifest["location_code"], manifest["language_code"],
                                         date.fromisoformat(manifest["date_from"]), date.fromisoformat(manifest["date_to"]))
        report.calls += 1
        report.calls_from_cache += int(result.call.from_cache)
        report.raw_paths.append(result.call.raw_path)
        if not result.ok:
            report.failures.append({"keyword": keyword, "status_code": result.call.status_code, "status_message": result.call.status_message})
            continue
        target = base / name / "labs_json" / f"{result.call.request_key}.json"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.decompress(Path(result.call.raw_path).read_bytes()))
        json_paths.append(target)
    report.spent_usd = ledger.spent_for_kind(KIND_HISTORICAL_SERPS) - spent_before
    if json_paths:
        built = panel_mod.build_panel(json_paths, out_dir=panel_dir, labs_locale=manifest["locale"])
        report.rows = sum(item["rows"] for item in built["files"])
        report.outputs["panel"] = built["out_dir"]
    return report


# Traffic tables. Every column is documented here. REAL: what DataForSEO estimated. Missing stays null.
TRAFFIC_MONTH_COLUMNS: dict[str, str] = {
    "site": "the domain asked for (registrable domain)",
    "item_type": "organic, paid, featured_snippet or local_pack",
    "month": "first day of the month the estimate is for (UTC)",
    "etv": "DataForSEO estimated traffic volume for the month; null when the vendor gave none",
    "serp_count": "number of result pages that held the site that month; null when the vendor gave none",
    "known_no_earlier_than": "first day of the next month: an estimate for a month cannot be known before it ends",
    "location_code": "Labs location (country level only)",
    "language_code": "Labs language",
    "request_key": "key of the saved raw answer",
    "raw_sha256": "sha256 of the raw answer bytes",
    "label": "REAL for vendor data, SYNTHETIC for the synthetic path",
}
TRAFFIC_TARGET_COLUMNS: dict[str, str] = {
    "site": "the domain asked for",
    "returned": "True when the answer listed the site at all; False means missing, not zero",
    "months_returned": "months with a row for the site (any item type)",
    "months_with_traffic": "months where etv or serp_count is above 0",
    "first_month_with_traffic": "first such month; null when none",
    "status_code": "DataForSEO task status of the chunk",
    "request_key": "key of the saved raw answer",
    "raw_sha256": "sha256 of the raw answer bytes",
    "from_cache": "True when the chunk was read back from disk and not paid again",
    "label": "REAL or SYNTHETIC",
}
TRAFFIC_MONTH_SCHEMA = pa.schema([
    ("site", pa.string()), ("item_type", pa.string()), ("month", pa.date32()), ("etv", pa.float64()), ("serp_count", pa.int64()),
    ("known_no_earlier_than", pa.date32()), ("location_code", pa.int32()), ("language_code", pa.string()),
    ("request_key", pa.string()), ("raw_sha256", pa.string()), ("label", pa.string()),
])
TRAFFIC_TARGET_SCHEMA = pa.schema([
    ("site", pa.string()), ("returned", pa.bool_()), ("months_returned", pa.int32()), ("months_with_traffic", pa.int32()),
    ("first_month_with_traffic", pa.date32()), ("status_code", pa.int32()), ("request_key", pa.string()),
    ("raw_sha256", pa.string()), ("from_cache", pa.bool_()), ("label", pa.string()),
])
assert tuple(TRAFFIC_MONTH_SCHEMA.names) == tuple(TRAFFIC_MONTH_COLUMNS)
assert tuple(TRAFFIC_TARGET_SCHEMA.names) == tuple(TRAFFIC_TARGET_COLUMNS)


def traffic_tables(
    months: Iterable[Mapping[str, Any]], asked: Mapping[str, Mapping[str, Any]], *, label: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the two traffic tables from month rows (site, item_type, year, month, etv, count, ...) and the chunk info per site asked."""
    records = []
    for row in months:
        first = date(int(row["year"]), int(row["month"]), 1)
        records.append({
            "site": row["site"], "item_type": row["item_type"], "month": first, "etv": row.get("etv"), "serp_count": row.get("count"),
            "known_no_earlier_than": add_months(first, 1), "location_code": row.get("location_code"),
            "language_code": row.get("language_code"), "request_key": row.get("request_key"), "raw_sha256": row.get("raw_sha256"),
            "label": label,
        })
    month_frame = pd.DataFrame.from_records(records, columns=list(TRAFFIC_MONTH_COLUMNS))
    month_frame = month_frame.sort_values(["site", "item_type", "month"], kind="mergesort").reset_index(drop=True)
    targets = []
    for site in sorted(asked):
        info = asked[site]
        mine = month_frame[month_frame["site"] == site]
        positive = mine[(mine["etv"].fillna(0) > 0) | (mine["serp_count"].fillna(0) > 0)]
        targets.append({
            "site": site, "returned": bool(info.get("returned")), "months_returned": int(mine["month"].nunique()),
            "months_with_traffic": int(positive["month"].nunique()),
            "first_month_with_traffic": positive["month"].min() if len(positive) else None,
            "status_code": info.get("status_code"), "request_key": info.get("request_key"), "raw_sha256": info.get("raw_sha256"),
            "from_cache": bool(info.get("from_cache")), "label": label,
        })
    return month_frame, pd.DataFrame.from_records(targets, columns=list(TRAFFIC_TARGET_COLUMNS))


def pull_traffic_curves(
    client: Any | None,
    sites: Sequence[Any],
    *,
    months: int = 30,
    location_code: int = DEFAULT_LOCATION_CODE,
    language_code: str = DEFAULT_LANGUAGE,
    chunk_size: int = LABS_MAX_TARGETS_PER_TASK,
    execute: bool = False,
    job: str | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> PullReport:
    """Monthly traffic curves (Historical Bulk Traffic Estimation) in chunks of up to 1,000 sites, then two parquet tables."""
    names = sorted(_wanted_sites(sites))
    chunks = [names[i : i + chunk_size] for i in range(0, len(names), chunk_size)]
    planned = sum((historical_traffic_cost(len(chunk)) for chunk in chunks), Decimal(0))
    name = job or _job_name("traffic", {"sites": names, "months": months, "location": location_code, "chunk": chunk_size})
    if not execute:
        return PullReport(KIND_HISTORICAL_TRAFFIC, name, True, len(names), planned)
    ledger = _require_client(client)
    base = Path(root) if root else default_root()
    day = today or _utcnow().date()
    start = max(TRAFFIC_MIN_DATE, add_months(day, -months))
    manifest = _manifest(base, name, KIND_HISTORICAL_TRAFFIC, {
        "sites": names, "chunk_size": chunk_size, "location_code": location_code, "language_code": language_code,
        "date_from": start.isoformat(), "date_to": day.isoformat(), "months": months,
    })
    report = PullReport(KIND_HISTORICAL_TRAFFIC, name, False, len(manifest["sites"]), planned, manifest=manifest)
    spent_before = ledger.spent_for_kind(KIND_HISTORICAL_TRAFFIC)
    month_rows: list[dict[str, Any]] = []
    asked: dict[str, dict[str, Any]] = {}
    stored = manifest["sites"]
    for offset in range(0, len(stored), manifest["chunk_size"]):
        chunk = stored[offset : offset + manifest["chunk_size"]]
        result = client.historical_bulk_traffic_estimation(
            chunk, manifest["location_code"], manifest["language_code"],
            date.fromisoformat(manifest["date_from"]), date.fromisoformat(manifest["date_to"]),
        )
        report.calls += 1
        report.calls_from_cache += int(result.call.from_cache)
        report.raw_paths.append(result.call.raw_path)
        if not result.ok:
            report.failures.append({"chunk_start": offset, "status_code": result.call.status_code, "status_message": result.call.status_message})
        returned = set(result.targets_returned)
        meta = {"request_key": result.call.request_key, "raw_sha256": result.call.raw_sha256}
        for site in chunk:
            asked[site] = {"returned": site in returned, "status_code": result.call.status_code, "from_cache": result.call.from_cache, **meta}
        for row in result.rows:
            month_rows.append({"site": row.target, "item_type": row.item_type, "year": row.year, "month": row.month, "etv": row.etv,
                               "count": row.count, "location_code": manifest["location_code"], "language_code": manifest["language_code"], **meta})
    report.spent_usd = ledger.spent_for_kind(KIND_HISTORICAL_TRAFFIC) - spent_before
    month_frame, target_frame = traffic_tables(month_rows, asked, label="REAL")
    out = base / name
    report.outputs["traffic_months"] = _write_parquet(month_frame, out / "traffic_months.parquet", TRAFFIC_MONTH_SCHEMA)
    report.outputs["traffic_targets"] = _write_parquet(target_frame, out / "traffic_targets.parquet", TRAFFIC_TARGET_SCHEMA)
    report.rows = len(month_frame)
    return report


# Exact positions. REAL, and CURRENT: the ranked-keywords answer describes the pull date, not the launch month.
POSITION_COLUMNS: dict[str, str] = {
    "site": "the domain asked for",
    "keyword": "the search, as DataForSEO gives it",
    "keyword_id": "sha256(locale|keyword)[:16], the panel's key",
    "locale": "CC_lang",
    "search_volume": "vendor monthly volume; null when not given",
    "item_type": "organic, paid, featured_snippet, local_pack",
    "rank_group": "organic rank (rank_group); null when not given",
    "rank_absolute": "slot on the page counting every element",
    "url": "the ranking page",
    "result_site": "registrable domain of the ranking page",
    "check_url": "the Google URL DataForSEO checked",
    "is_lost": "True when DataForSEO marks the ranking as lost",
    "last_updated_time": "when DataForSEO last saw this ranking (UTC)",
    "request_key": "key of the saved raw answer",
    "raw_sha256": "sha256 of the raw answer bytes",
    "label": "REAL or SYNTHETIC",
}
POSITION_TARGET_COLUMNS: dict[str, str] = {
    "site": "the domain asked for",
    "total_count": "rankings DataForSEO holds for the site (all pages); null when not given",
    "rows": "rows returned in this pull",
    "status_code": "DataForSEO task status",
    "request_key": "key of the saved raw answer",
    "raw_sha256": "sha256 of the raw answer bytes",
    "from_cache": "True when read back from disk and not paid again",
    "label": "REAL or SYNTHETIC",
}
POSITION_SCHEMA = pa.schema([
    ("site", pa.string()), ("keyword", pa.string()), ("keyword_id", pa.string()), ("locale", pa.string()),
    ("search_volume", pa.int64()), ("item_type", pa.string()), ("rank_group", pa.int32()), ("rank_absolute", pa.int32()),
    ("url", pa.string()), ("result_site", pa.string()), ("check_url", pa.string()), ("is_lost", pa.bool_()),
    ("last_updated_time", pa.timestamp("s", tz="UTC")), ("request_key", pa.string()), ("raw_sha256", pa.string()), ("label", pa.string()),
])
POSITION_TARGET_SCHEMA = pa.schema([
    ("site", pa.string()), ("total_count", pa.int64()), ("rows", pa.int32()), ("status_code", pa.int32()),
    ("request_key", pa.string()), ("raw_sha256", pa.string()), ("from_cache", pa.bool_()), ("label", pa.string()),
])
assert tuple(POSITION_SCHEMA.names) == tuple(POSITION_COLUMNS)
assert tuple(POSITION_TARGET_SCHEMA.names) == tuple(POSITION_TARGET_COLUMNS)


def position_tables(rows: Iterable[Mapping[str, Any]], targets: Iterable[Mapping[str, Any]], *, label: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    records = []
    for row in rows:
        moment = row.get("last_updated_time")
        records.append({**{name: row.get(name) for name in POSITION_COLUMNS},
                        "last_updated_time": pd.Timestamp(moment) if moment else None, "label": label})
    frame = pd.DataFrame.from_records(records, columns=list(POSITION_COLUMNS))
    frame = frame.sort_values(["site", "keyword_id", "item_type"], kind="mergesort", na_position="last").reset_index(drop=True)
    target_frame = pd.DataFrame.from_records([{**{n: t.get(n) for n in POSITION_TARGET_COLUMNS}, "label": label} for t in targets],
                                             columns=list(POSITION_TARGET_COLUMNS))
    return frame, target_frame.sort_values("site", kind="mergesort").reset_index(drop=True)


def pull_exact_positions(
    client: Any | None,
    sites: Sequence[Any],
    *,
    rows_per_site: int = DEFAULT_ROWS_PER_SITE,
    location_code: int = DEFAULT_LOCATION_CODE,
    language_code: str = DEFAULT_LANGUAGE,
    locale: str = DEFAULT_LOCALE,
    execute: bool = False,
    job: str | None = None,
    root: Path | None = None,
) -> PullReport:
    """Exact positions (ranked keywords, standard Labs rate) for each site, then two parquet tables."""
    names = sorted(_wanted_sites(sites))
    planned = ranked_keywords_cost(len(names), rows_per_site)
    name = job or _job_name("positions", {"sites": names, "rows": rows_per_site, "location": location_code})
    if not execute:
        return PullReport(KIND_RANKED_KEYWORDS, name, True, len(names), planned)
    ledger = _require_client(client)
    base = Path(root) if root else default_root()
    manifest = _manifest(base, name, KIND_RANKED_KEYWORDS, {
        "sites": names, "rows_per_site": rows_per_site, "location_code": location_code, "language_code": language_code, "locale": locale,
    })
    report = PullReport(KIND_RANKED_KEYWORDS, name, False, len(manifest["sites"]), planned, manifest=manifest)
    spent_before = ledger.spent_for_kind(KIND_RANKED_KEYWORDS)
    rows: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for site in manifest["sites"]:
        result = client.ranked_keywords(site, manifest["location_code"], manifest["language_code"], limit=manifest["rows_per_site"])
        report.calls += 1
        report.calls_from_cache += int(result.call.from_cache)
        report.raw_paths.append(result.call.raw_path)
        meta = {"request_key": result.call.request_key, "raw_sha256": result.call.raw_sha256}
        if not result.ok:
            report.failures.append({"site": site, "status_code": result.call.status_code, "status_message": result.call.status_message})
        targets.append({"site": site, "total_count": result.total_count, "rows": len(result.rows), "status_code": result.call.status_code,
                        "from_cache": result.call.from_cache, **meta})
        for row in result.rows:
            rows.append({
                "site": site, "keyword": row.keyword, "keyword_id": dfs.keyword_id(manifest["locale"], row.keyword) if row.keyword else None,
                "locale": manifest["locale"], "search_volume": row.search_volume, "item_type": row.item_type, "rank_group": row.rank_group,
                "rank_absolute": row.rank_absolute, "url": row.url, "result_site": row.site, "check_url": row.check_url,
                "is_lost": row.is_lost, "last_updated_time": row.last_updated_time, **meta,
            })
    report.spent_usd = ledger.spent_for_kind(KIND_RANKED_KEYWORDS) - spent_before
    frame, target_frame = position_tables(rows, targets, label="REAL")
    out = base / name
    report.outputs["positions"] = _write_parquet(frame, out / "positions.parquet", POSITION_SCHEMA)
    report.outputs["position_targets"] = _write_parquet(target_frame, out / "position_targets.parquet", POSITION_TARGET_SCHEMA)
    report.rows = len(frame)
    return report


# ----------------------------------------------------------------------------------------------
# Label-blind target searches.
# ----------------------------------------------------------------------------------------------


TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset({"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "at", "by", "from", "or", "is", "are",
                       "my", "your", "our", "me", "near", "best", "top", "how", "what", "do", "does", "i"})


def _tokens(text: str) -> list[str]:
    out = []
    for token in TOKEN_RE.findall(normalise(text)):
        if token in STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.append(token)
    return out


def token_overlap_score(search: str, text: str) -> float:
    """A SIMPLE stand-in scorer: the share of the search's content words found in the text. Not the content model.

    It exists so the selection rule and the synthetic path can run before the
    real content scorer (part 4) is finished. select_targets takes any callable.
    """
    words = set(_tokens(search))
    if not words:
        return 0.0
    found = set(_tokens(text))
    return len(words & found) / len(words)


@dataclass(frozen=True)
class TargetRule:
    """The frozen, label-blind rule that picks target searches for a past site. It never sees rankings.

    `k` matches above `threshold` per site, plus `controls_per_site` searches of
    the same niche picked by sha256 order among the rest. The hash covers these
    values, the scorer's name and the source text of the selection code.
    """

    version: str = "target-rule-v1"
    k: int = 20
    threshold: float = 0.5
    controls_per_site: int = 4
    seed: int = 20261122
    scorer_id: str = ""

    def with_scorer(self, score_fn: Callable[[str, str], float]) -> "TargetRule":
        if self.scorer_id:
            return self
        name = f"{getattr(score_fn, '__module__', '?')}.{getattr(score_fn, '__qualname__', type(score_fn).__name__)}"
        return TargetRule(self.version, self.k, self.threshold, self.controls_per_site, self.seed, name)

    def rule_hash(self) -> str:
        payload = {**asdict(self), "code": inspect.getsource(_select_for_site)}
        return sha256_text(canonical_json(payload))


DEFAULT_TARGET_RULE = TargetRule()


@dataclass(frozen=True)
class TargetRow:
    site: str
    keyword: str
    keyword_id: str
    locale: str
    niche: str | None
    score: float
    selection: str  # match or control
    rank_in_site: int
    launch_date: date
    as_of: date


@dataclass
class TargetSelection:
    rows: list[TargetRow]
    rule: TargetRule
    rule_hash: str
    sites_without_text: list[str]
    sites_without_launch_date: list[str]
    counts: dict[str, Any]
    sites_whose_niche_has_no_searches: list[str] = field(default_factory=list)

    def frame(self) -> pd.DataFrame:
        frame = pd.DataFrame.from_records([asdict(r) for r in self.rows], columns=list(TargetRow.__dataclass_fields__))
        frame["target_rule_hash"] = self.rule_hash
        return frame


def last_day_of_month(day: date) -> date:
    return add_months(day, 1) - timedelta(days=1)


def _select_for_site(site: str, text: str, searches: Sequence[Any], score_fn: Callable[[str, str], float],
                     rule: TargetRule) -> list[tuple[Any, float, str]]:
    """Top k searches by score at or above the threshold, then label-blind controls by sha256 order. Ties break on keyword_id."""
    scored = []
    for search in searches:
        value = float(score_fn(str(_get(search, "keyword")), text))
        if not math.isfinite(value):
            raise ValueError(f"the scorer returned {value} for {site} and {_get(search, 'keyword')!r}")
        scored.append((search, value))
    ranked = sorted(scored, key=lambda pair: (-pair[1], str(_get(pair[0], "keyword_id"))))
    matches = [(s, v, "match") for s, v in ranked if v >= rule.threshold][: rule.k]
    taken = {str(_get(s, "keyword_id")) for s, _, _ in matches}
    rest = [(s, v) for s, v in scored if str(_get(s, "keyword_id")) not in taken]
    rest.sort(key=lambda pair: hashlib.sha256(f"{rule.seed}|{site}|{_get(pair[0], 'keyword_id')}".encode()).hexdigest())
    controls = [(s, v, "control") for s, v in rest[: rule.controls_per_site]]
    return matches + controls


def select_targets(
    cohort: Sequence[Any],
    universe: Sequence[Any],
    page_text: Mapping[str, Sequence[Dated]],
    score_fn: Callable[[str, str], float],
    *,
    rule: TargetRule = DEFAULT_TARGET_RULE,
) -> TargetSelection:
    """The label-blind choice of target searches for past sites (PLAN_AUDIT B2).

    For each site with a launch date, its launch-era page text is taken through
    AsOfGuard with as_of = the last day of the launch month. A text dated after
    that, or undated, raises LeakError: it cannot be passed in. Each search of
    the site's niche (all searches when no niche is given) is scored with
    score_fn(search, text). A site whose niche has no searches gets none (it is
    listed, never given another niche's searches). The rule never receives rankings, so searches where
    the site never ranks are kept like any other.

    Sites are normalised (_site) before anything else: an excluded site is
    dropped however it is spelled, and two spellings of one site make one
    entry (the first in sorted order). page_text is matched on the normalised
    site too.
    """
    rule = rule.with_scorer(score_fn)
    rule_hash = rule.rule_hash()
    rows: list[TargetRow] = []
    without_text: list[str] = []
    without_launch: list[str] = []
    without_pool: list[str] = []
    by_niche: dict[Any, list[Any]] = {}
    for search in universe:
        by_niche.setdefault(_get(search, "niche"), []).append(search)
    all_searches = list(universe)
    texts_by_site: dict[str, list[Dated]] = {}
    for key, items in page_text.items():
        texts_by_site.setdefault(_site(key), []).extend(items)
    seen: set[str] = set()
    for entry in sorted(cohort, key=lambda r: str(_get(r, "site"))):
        site = _site(_get(entry, "site"))
        if not site or site in EXCLUDED_SITES or site in seen:
            continue
        seen.add(site)
        launch = _as_date(_get(entry, "launch_date"))
        if launch is None:
            without_launch.append(site)
            continue
        as_of = last_day_of_month(launch)
        guard = AsOfGuard(as_of)
        texts = [guard.take(item) for item in texts_by_site.get(site, ())]
        text = "\n".join(str(t) for t in texts if t)
        if not text.strip():
            without_text.append(site)
            continue
        niche = _get(entry, "niche")
        pool = by_niche.get(niche, []) if niche is not None else all_searches
        if not pool:
            without_pool.append(site)
            continue
        for rank, (search, value, selection) in enumerate(_select_for_site(site, text, pool, score_fn, rule), start=1):
            rows.append(TargetRow(site=site, keyword=str(_get(search, "keyword")), keyword_id=str(_get(search, "keyword_id")),
                                  locale=str(_get(search, "locale")), niche=_get(search, "niche"), score=round(value, 6),
                                  selection=selection, rank_in_site=rank, launch_date=launch, as_of=as_of))
    counts = {
        "sites_with_targets": len({row.site for row in rows}),
        "matches": sum(row.selection == "match" for row in rows),
        "controls": sum(row.selection == "control" for row in rows),
        "sites_without_text": len(without_text),
        "sites_without_launch_date": len(without_launch),
        "sites_whose_niche_has_no_searches": len(without_pool),
    }
    return TargetSelection(rows, rule, rule_hash, without_text, without_launch, counts, without_pool)


# ----------------------------------------------------------------------------------------------
# The split and the cells the backtest reads.
# ----------------------------------------------------------------------------------------------

CELL_COLUMNS: tuple[str, ...] = (
    "site", "keyword", "keyword_id", "locale", "launch_date", "launch_date_source", "launch_uncertainty_days",
    "target_rule_hash", "niche", "split",
)
SPLITS = ("train", "test", "excluded")


def search_group(keyword: str) -> str:
    """The search group of a search: its content words, plural s removed, sorted and deduplicated, hashed.

    "plumber near me" and "plumbers near me" share a group. A search group never
    lands on both sides of the split.
    """
    words = sorted(set(_tokens(keyword))) or [normalise(keyword)]
    return sha256_text(" ".join(words))[:16]


@dataclass(frozen=True)
class SplitRule:
    """The frozen split rule of IMPLEMENTATION_SPEC section 6: by launch cohort in time, then by site, no search group on both sides."""

    version: str = "split-rule-v2"  # v2: floor of the share (v1 rounded up), sites normalised, excluded sites excluded
    test_share: float = 0.2
    seed: int = 20261122

    def rule_hash(self) -> str:
        payload = {**asdict(self), "code": inspect.getsource(assign_split) + inspect.getsource(search_group)}
        return sha256_text(canonical_json(payload))


DEFAULT_SPLIT_RULE = SplitRule()


def assign_split(cells: Sequence[Mapping[str, Any]], rule: SplitRule = DEFAULT_SPLIT_RULE) -> list[str]:
    """One split label per cell (train, test or excluded).

    1. Sites are ordered by launch month. Whole months from the latest back go to
       test while the test share stays at or under rule.test_share of sites
       (floor of test_share x sites, but at least one test site when there are
       two or more sites, so both sides are non-empty). The boundary month is
       split by site, in sha256(seed | site) order, to reach the share. So test
       sites launched after every train site, except inside the boundary month.
    2. Every cell of a site takes the site's side.
    3. Every search group used by a test site belongs to test. A train cell in
       such a group is excluded. So no search group is on both sides.
    4. A cell whose site has no launch date is excluded, and so is a cell of an
       excluded site (EXCLUDED_SITES), however the site is spelled.
    """
    if not 0 < rule.test_share < 1:
        raise ValueError("test_share must be between 0 and 1")
    launch: dict[str, date] = {}
    for cell in cells:
        day = _as_date(cell.get("launch_date"))
        site = _site(cell["site"])
        if day is not None and site and site not in EXCLUDED_SITES:
            launch.setdefault(site, day)
    sites = sorted(launch)
    # Decimal, not float: 0.29 * 100 is 28.999... in floating point.
    target = max(1, math.floor(Decimal(str(rule.test_share)) * len(sites))) if len(sites) > 1 else 0
    by_month: dict[date, list[str]] = {}
    for site in sites:
        by_month.setdefault(month_start(launch[site]), []).append(site)
    test_sites: set[str] = set()
    for month in sorted(by_month, reverse=True):
        members = sorted(by_month[month], key=lambda s: hashlib.sha256(f"{rule.seed}|{s}".encode()).hexdigest())
        room = target - len(test_sites)
        if room <= 0:
            break
        test_sites.update(members[:room])
    test_groups = {search_group(str(c["keyword"])) for c in cells if _site(c["site"]) in test_sites}
    out = []
    for cell in cells:
        site = _site(cell["site"])
        if site not in launch:
            out.append("excluded")
        elif site in test_sites:
            out.append("test")
        elif search_group(str(cell["keyword"])) in test_groups:
            out.append("excluded")
        else:
            out.append("train")
    return out


def cohort_cells(
    cohort: Sequence[Any],
    targets: TargetSelection,
    *,
    split_rule: SplitRule = DEFAULT_SPLIT_RULE,
) -> pd.DataFrame:
    """The table the backtest reads, with exactly CELL_COLUMNS. One row per (site, target search).

    launch_date, launch_date_source and launch_uncertainty_days come from the
    cohort. target_rule_hash is the selection rule's hash. split is assigned by
    the frozen split rule; its hash is in frame.attrs["split_rule_hash"].
    """
    info: dict[str, Any] = {}
    for row in cohort:
        info[_site(_get(row, "site"))] = row
    records: list[dict[str, Any]] = []
    for target in targets.rows:
        row = info.get(_site(target.site))
        uncertainty = _get(row, "launch_uncertainty_days") if row is not None else None
        records.append({
            "site": target.site, "keyword": target.keyword, "keyword_id": target.keyword_id, "locale": target.locale,
            "launch_date": target.launch_date,
            "launch_date_source": _get(row, "launch_date_source") if row is not None else None,
            "launch_uncertainty_days": None if uncertainty is None or pd.isna(uncertainty) else int(uncertainty),
            "target_rule_hash": targets.rule_hash, "niche": target.niche,
        })
    splits = assign_split(records, split_rule)
    for record, split in zip(records, splits, strict=True):
        record["split"] = split
    frame = pd.DataFrame.from_records(records, columns=list(CELL_COLUMNS))
    frame["launch_uncertainty_days"] = frame["launch_uncertainty_days"].astype("Int64")
    frame = frame.sort_values(["site", "keyword_id"], kind="mergesort").reset_index(drop=True)
    frame.attrs["split_rule_hash"] = split_rule.rule_hash()
    frame.attrs["target_rule_hash"] = targets.rule_hash
    return frame


def check_split(frame: pd.DataFrame) -> dict[str, int]:
    """Counts of sites and search groups on both sides. Both must be 0."""
    train = frame[frame["split"] == "train"]
    test = frame[frame["split"] == "test"]
    groups_train = {search_group(k) for k in train["keyword"]}
    groups_test = {search_group(k) for k in test["keyword"]}
    return {"sites_on_both_sides": len(set(train["site"]) & set(test["site"])), "groups_on_both_sides": len(groups_train & groups_test)}


# ----------------------------------------------------------------------------------------------
# The synthetic end-to-end path.
# ----------------------------------------------------------------------------------------------

SYNTHETIC_LOCALE = "SYN_en"
SYNTHETIC_DATASET = "synthetic_historical"


@dataclass
class SyntheticTrainingSet:
    """Every training-set table built from forecast.synth.World. SYNTHETIC. Never reported as accuracy."""

    label: str
    cohort: pd.DataFrame
    universe: list[SearchRow]
    targets: TargetSelection
    cells: pd.DataFrame
    panel_rows: int
    traffic_months: pd.DataFrame
    traffic_targets: pd.DataFrame
    positions: pd.DataFrame
    position_targets: pd.DataFrame
    cost_plan: PullPlan
    paths: dict[str, str]


def synthetic_training_set(
    world: Any,
    *,
    out_dir: Path,
    rule: TargetRule = DEFAULT_TARGET_RULE,
    split_rule: SplitRule = DEFAULT_SPLIT_RULE,
    score_fn: Callable[[str, str], float] = token_overlap_score,
) -> SyntheticTrainingSet:
    """Build the cohort, universe, targets, cells, panel, traffic and position tables from a synthetic World.

    No money and no network. The synthetic world plants each new site's launch
    date, so launch_date_source is "synthetic_truth" with 0 days of uncertainty.
    Dead sites stay in the cohort with no_content_evidence set. Decoy
    registrations from the synthetic new-domain list stay in too.
    """
    from .sources.newsites import CohortRow, rows_to_frame

    out = Path(out_dir)
    cohort_rows: list[CohortRow] = []
    seen: set[str] = set()
    for site, info in sorted(world.new_sites.items()):
        alive = bool(info.alive)
        cohort_rows.append(CohortRow(
            site=site, registered_on=info.registered, registered_on_source="synthetic", listed_on=info.registered,
            source="synthetic-nrd", tld="example", rdap_status=None, rdap_registered_on=None, nameservers=(), ns_parking=None,
            dns_resolves=None, current_state_checked_at=None, cc_crawls_checked=(), cc_pre_registration_captures=None,
            cc_first_seen=info.launch_date if alive else None, cc_first_content=info.launch_date if alive else None,
            cc_pages_first_content_crawl=info.pages_at_launch if alive else None, home_url=None, home_title=None,
            launch_date=info.launch_date if alive else None, launch_date_source="synthetic_truth" if alive else None,
            launch_uncertainty_days=0 if alive else None, parked=not alive, parked_now=False, possibly_re_registered=False,
            possible_rebrand=False, no_content_evidence=not alive, redirects_offsite=False, re_registered_after_listing=False,
            registration_older_than_list=False, not_registered_now=False, resolvable_or_crawled=alive, content_evidence=alive, kept=alive,
        ))
        seen.add(site)
    registration_days = sorted({info.registered for info in world.new_sites.values()})
    for day in registration_days:
        for item in world.newly_registered(day):
            if item.value in seen:
                continue
            seen.add(item.value)
            cohort_rows.append(CohortRow(
                site=item.value, registered_on=day, registered_on_source="synthetic", listed_on=day, source="synthetic-nrd",
                tld="example", rdap_status=None, rdap_registered_on=None, nameservers=(), ns_parking=None, dns_resolves=None,
                current_state_checked_at=None, cc_crawls_checked=(), cc_pre_registration_captures=None, cc_first_seen=None,
                cc_first_content=None, cc_pages_first_content_crawl=None, home_url=None, home_title=None, launch_date=None,
                launch_date_source=None, launch_uncertainty_days=None, parked=False, parked_now=False, possibly_re_registered=False,
                possible_rebrand=False, no_content_evidence=True, redirects_offsite=False, re_registered_after_listing=False,
                registration_older_than_list=False, not_registered_now=False, resolvable_or_crawled=False, content_evidence=False,
                kept=False,
            ))
    cohort_rows.sort(key=lambda r: r.site)
    cohort_frame = rows_to_frame(cohort_rows)
    cohort_frame["label"] = SYNTHETIC
    niche_of = {site: info.niche for site, info in world.new_sites.items()}

    universe = [
        SearchRow(keyword=s.text, keyword_id=dfs.keyword_id(SYNTHETIC_LOCALE, s.text), locale=SYNTHETIC_LOCALE, country="SYN",
                  niche=s.niche, seed_term="", search_volume=int(s.volume), competition=None, cpc=None, kd=None, intent=None,
                  sources="synthetic", difficulty_proxy=float(s.difficulty), difficulty_proxy_source="synthetic_difficulty", stratum=s.tercile)
        for s in world.searches
    ]
    pages: dict[str, list[Dated]] = {}
    for site, info in world.new_sites.items():
        if not info.alive:
            continue
        brand = world.sites[world.site_index(site)].brand
        text = " | ".join(f"{search.title()} | {brand}" for search in info.targets)
        pages[site] = [Dated(text, f"synthetic-page:{site}", info.launch_date)]
    entries = [{"site": r.site, "launch_date": r.launch_date, "niche": niche_of.get(r.site)} for r in cohort_rows]
    targets = select_targets(entries, universe, pages, score_fn, rule=rule)
    cells = cohort_cells(cohort_rows, targets, split_rule=split_rule)

    panel_dir = out / "panel"
    panel_rows = 0
    month = world.start
    last_pulls: list[Any] = []
    while month < world.end:
        records = []
        pulls = world.monthly_snapshots(month)
        for pull in pulls:
            moment = datetime.fromisoformat(pull.provider_datetime.replace("Z", "+00:00"))
            for hit in pull.hits:
                records.append({
                    "dataset": SYNTHETIC_DATASET, "locale": SYNTHETIC_LOCALE, "location_code": pull.spec.location_code,
                    "language": pull.spec.language_code, "keyword": pull.spec.search,
                    "keyword_id": dfs.keyword_id(SYNTHETIC_LOCALE, pull.spec.search), "snapshot_time": moment,
                    "snapshot_date": moment.date(), "rank": hit.rank, "rank_absolute": hit.rank_absolute, "domain": hit.domain,
                    "site": hit.site, "url": hit.url, "title": hit.title, "description": None, "is_new": None,
                    "previous_rank_absolute": None, "main_domain_rank": None, "page_rank": None, "etv": None, "kd": None,
                    "search_volume": None, "intent": None, "items_count": None, "se_results_count": None, "poison_risk": False,
                })
        folder = panel_dir / f"dataset={SYNTHETIC_DATASET}" / f"locale={SYNTHETIC_LOCALE}"
        folder.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(records, schema=panel_mod.PANEL_SCHEMA), folder / f"part-{month:%Y-%m}.parquet", compression="zstd")
        panel_rows += len(records)
        last_pulls = pulls
        month = add_months(month, 1)

    month_rows: list[dict[str, Any]] = []
    asked: dict[str, dict[str, Any]] = {}
    for site in sorted(world.new_sites):
        curve = world.traffic_curve(site)
        asked[site] = {"returned": bool(curve), "status_code": 20000, "request_key": None, "raw_sha256": None, "from_cache": False}
        for item in curve:
            year, number = (int(x) for x in item.value["month"].split("-"))
            month_rows.append({"site": site, "item_type": "organic", "year": year, "month": number, "etv": item.value["etv"],
                               "count": None, "location_code": None, "language_code": None})
    traffic_months, traffic_targets = traffic_tables(month_rows, asked, label=SYNTHETIC)

    position_rows: list[dict[str, Any]] = []
    position_targets: list[dict[str, Any]] = []
    for site in sorted(world.new_sites):
        found = 0
        for pull in last_pulls:
            for hit in pull.hits:
                if hit.site == site:
                    found += 1
                    position_rows.append({
                        "site": site, "keyword": pull.spec.search, "keyword_id": dfs.keyword_id(SYNTHETIC_LOCALE, pull.spec.search),
                        "locale": SYNTHETIC_LOCALE, "search_volume": None, "item_type": "organic", "rank_group": hit.rank,
                        "rank_absolute": hit.rank_absolute, "url": hit.url, "result_site": hit.site, "check_url": None, "is_lost": None,
                        "last_updated_time": pull.provider_datetime,
                    })
        position_targets.append({"site": site, "total_count": found, "rows": found, "status_code": 20000, "from_cache": False})
    positions, position_target_frame = position_tables(position_rows, position_targets, label=SYNTHETIC)

    plan = plan_pulls(universe, cohort_rows, today=world.end - timedelta(days=1))
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "cohort": str(out / "cohort.parquet"), "cells": str(out / "cells.parquet"), "targets": str(out / "targets.parquet"),
        "panel": str(panel_dir),
        "traffic_months": _write_parquet(traffic_months, out / "traffic_months.parquet", TRAFFIC_MONTH_SCHEMA),
        "traffic_targets": _write_parquet(traffic_targets, out / "traffic_targets.parquet", TRAFFIC_TARGET_SCHEMA),
        "positions": _write_parquet(positions, out / "positions.parquet", POSITION_SCHEMA),
        "position_targets": _write_parquet(position_target_frame, out / "position_targets.parquet", POSITION_TARGET_SCHEMA),
    }
    cohort_frame.to_parquet(paths["cohort"], index=False)
    cells_out = cells.copy()
    cells_out["label"] = SYNTHETIC
    cells_out.to_parquet(paths["cells"], index=False)
    target_frame = targets.frame()
    target_frame["label"] = SYNTHETIC
    target_frame.to_parquet(paths["targets"], index=False)
    meta = {
        "label": SYNTHETIC, "world_seed": world.seed, "target_rule_hash": targets.rule_hash,
        "split_rule_hash": split_rule.rule_hash(), "counts": targets.counts, "split": check_split(cells),
        "split_counts": cells["split"].value_counts().to_dict(), "panel_rows": panel_rows,
    }
    (out / "SYNTHETIC.json").write_text(json.dumps(meta, indent=2, sort_keys=True, default=str), encoding="utf-8")
    paths["meta"] = str(out / "SYNTHETIC.json")
    return SyntheticTrainingSet(SYNTHETIC, cohort_frame, universe, targets, cells, panel_rows, traffic_months, traffic_targets,
                                positions, position_target_frame, plan, paths)
