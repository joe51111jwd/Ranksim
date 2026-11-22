"""DataForSEO client: Google SERP readings (Standard queue) and the Labs endpoints the build plan buys.

REAL: every Pull and every Labs row here is what DataForSEO returned, parsed
from the raw JSON that was saved to disk first. Positions are the provider's
`rank_group` (organic results only). Nothing is PREDICTED here. Nothing is
invented: a failed or missing task is a Pull with ok False and its status, never
an empty result, and a value the provider left out stays None.

What this module does (facts from the DataForSEO API map, read 2026-11-22):

- Auth is HTTP Basic with the API login and API password. Sync httpx only.
- SERP readings use the Standard queue at normal priority by default:
  task_post (at most 100 tasks per call), then tasks_ready and task_get/advanced
  polling under an injected clock, with a timeout. The Live endpoint costs
  about 3.3 times more and runs only when mode="live" is passed by name.
- Each task asks for one city location_code, language en, device desktop,
  depth 50, priority normal, resolve_poisoned_results true, and a tag that
  carries our pull id.
- Status codes follow the map's policy. 20000 is ok. 40106 is ok with
  partial=True. 40102 is ok with no results (Google had none). 40601 and 40602
  mean not ready yet. 40101, 40103 and 50xxx are failed tasks that may be
  resubmitted. Login, balance, pause and cost-limit errors raise ProviderError
  and stop the batch. Every other code is a failed pull.
- The raw response is gzipped to disk BEFORE parsing:
  raw_dir/dataforseo/<UTC date posted>/<task id>.json.gz for queued SERP tasks.
  Labs calls are saved under raw_dir/dataforseo/labs/<function>/<request key>.json.gz
  so a chunked pull can resume: a chunk whose raw file exists is read from disk
  and not paid for again. The sha256 on a Pull is the sha256 of the response
  bytes as received (the uncompressed content of the .gz file).
- Every paid call goes through the SpendLedger: reserve the estimate, send,
  settle with the provider's own `cost` field, release when nothing was sent.
  Without a ledger every paid call is refused. A SERP batch is checked against
  the ledger as a whole before its first task_post, and the estimate follows the
  provider's billing formula, x5 for a keyword holding a search operator. When a
  batch stops after some tasks were paid for, the error carries one Pull per
  spec (`error.pulls`): nothing paid for is thrown away.
- The sandbox (base_url=DFS_SANDBOX) is free and answers with dummy data. Its
  calls skip the ledger, its pulls carry PROVIDER_SANDBOX (readings from them
  are SYNTHETIC, never REAL), its raw files go under their own folder, and no
  $1 sample runs on it.
- A $1 sample is recorded only when it learned something. Otherwise it is
  written as a failed sample that keeps the big-pull gate closed.

Labs data is country level only (no cities). Historical SERPs history depth is
in conflict between two DataForSEO pages (12 months versus since August 2021).
The sample_* helpers measure such facts for $1 or less before a big pull.
"""

from __future__ import annotations

import gzip
import json
import math
import os
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import httpx

from aeo.errors import ProviderConfigError
from aeo.ranksim.site_history import site_key

from .asof import add_months, parse_utc, utc_iso
from .budget import (
    KIND_BULK_TRAFFIC,
    KIND_HISTORICAL_SERPS,
    KIND_HISTORICAL_TRAFFIC,
    KIND_RANKED_KEYWORDS,
    LABS_MAX_TARGETS_PER_TASK,
    SAMPLE_MAX_USD,
    SERP_MODE_KINDS,
    BudgetError,
    ProviderError,
    SpendLedger,
    bulk_traffic_cost,
    historical_serps_estimate,
    historical_traffic_cost,
    ranked_keywords_cost,
    serp_cost,
    to_decimal,
)
from .chain import canonical_json, sha256_bytes, sha256_text
from .config import EXCLUDED_SITES, TOP_DEPTH, forecast_paths
from .types import OrganicHit, Pull, PullSpec

__all__ = [
    "DFS_BASE",
    "DFS_SANDBOX",
    "Balance",
    "BulkTrafficResult",
    "DataForSeoClient",
    "DataForSeoSerpSource",
    "HistoricalOrganic",
    "HistoricalSerpsResult",
    "HistoricalSnapshot",
    "HistoricalTrafficResult",
    "LabsCall",
    "Location",
    "PostedTask",
    "ProviderError",
    "RankedKeywordRow",
    "RankedKeywordsResult",
    "ReadyTask",
    "SampleReport",
    "TrafficMonth",
    "TrafficNow",
    "parse_serp_task",
    "read_raw",
]

PROVIDER = "dataforseo"
# Pulls from the sandbox carry this provider name. The sandbox answers with dummy data (provider map
# section 7), so a reading built from them is SYNTHETIC, never REAL (reader.NOT_REAL_PROVIDERS).
PROVIDER_SANDBOX = "dataforseo-sandbox"
DFS_BASE = "https://api.dataforseo.com/v3"
DFS_SANDBOX = "https://sandbox.dataforseo.com/v3"

PATH_TASK_POST = "serp/google/organic/task_post"
PATH_TASKS_READY = "serp/google/organic/tasks_ready"
PATH_TASK_GET_ADVANCED = "serp/google/organic/task_get/advanced"
PATH_LIVE_ADVANCED = "serp/google/organic/live/advanced"
PATH_HISTORICAL_SERPS = "dataforseo_labs/google/historical_serps/live"
PATH_HISTORICAL_TRAFFIC = "dataforseo_labs/google/historical_bulk_traffic_estimation/live"
PATH_BULK_TRAFFIC = "dataforseo_labs/google/bulk_traffic_estimation/live"
PATH_RANKED_KEYWORDS = "dataforseo_labs/google/ranked_keywords/live"
PATH_USER_DATA = "appendix/user_data"
PATH_SERP_LOCATIONS = "serp/google/locations"

ENDPOINT_STANDARD = "serp/google/organic/task_post+task_get/advanced"
ENDPOINT_LIVE = PATH_LIVE_ADVANCED

MAX_TASKS_PER_POST = 100
MAX_KEYWORD_CHARS = 700
MAX_TAG_CHARS = 255
MAX_DEPTH_STANDARD = 700
MAX_DEPTH_LIVE = 200
MAX_RANKED_KEYWORDS_LIMIT = 1000
DEVICES = ("desktop", "mobile")
STANDARD_MODES = ("standard_normal", "standard_high")
PRIORITY_BY_MODE = {"standard_normal": 1, "standard_high": 2}

DEFAULT_HTTP_TIMEOUT_SECONDS = 60.0
DEFAULT_POLL_SECONDS = 60.0  # tasks_ready allows 20 calls a minute. The docs suggest 5 to 10 minutes for normal priority.
DEFAULT_WAIT_TIMEOUT_SECONDS = 3600.0  # Standard queue target turnaround is 45 minutes.
DIRECT_POLL_EVERY_ROUNDS = 3  # every third round, ask task_get for tasks tasks_ready has not listed
FREE_GET_RETRIES = 3
FREE_GET_BACKOFF_SECONDS = 5.0
THROTTLE_RETRIES = 3
THROTTLE_BACKOFF_SECONDS = 60.0
RESUBMIT_BACKOFF_SECONDS = 60.0
DEFAULT_MAX_RESUBMITS = 1
SAMPLE_HISTORY_MONTHS = 24

STATUS_OK = 20000
STATUS_TASK_CREATED = 20100
STATUS_NO_RESULTS = 40102
STATUS_PARTIAL = 40106
STATUS_INVALID_FIELD = 40501
NOT_READY_CODES = frozenset({40601, 40602})
FATAL_CODES = frozenset({40100, 40104, 40200, 40201, 40203, 40207, 40210})
THROTTLE_CODES = frozenset({40202, 40209})
RESUBMIT_CODES = frozenset({40101, 40103, 50000, 50001, 50301, 50303, 50304, 50401})
UNCHARGED_HTTP_STATUSES = frozenset({401, 402})
OK_SERP_CODES = frozenset({STATUS_OK, STATUS_PARTIAL, STATUS_NO_RESULTS})
# A `spell` block whose type is did_you_mean only suggests a spelling: Google answered the query as typed.
# Every other spell type (showing_results_for, including_results_for, no_results_found_for, or a type
# not in the docs) means Google answered a corrected query, so the pull is marked spell_corrected.
SPELL_TYPES_ANSWERING_ORIGINAL = frozenset({"did_you_mean"})

FATAL_HINTS: dict[int, str] = {
    40100: "Check the DataForSEO API login and API password. The API password is not the account password.",
    40104: "Verify the DataForSEO account before using the API.",
    40200: "The DataForSEO balance needs a top up.",
    40201: "The DataForSEO account is paused. Contact DataForSEO.",
    40203: "The daily cost limit set in the DataForSEO dashboard was reached.",
    40207: "This IP address is not on the DataForSEO whitelist.",
    40210: "The DataForSEO balance is too low. Top up before spending.",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------------------------------------
# Records.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RawFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class PostedTask:
    """One task_post item and what DataForSEO said about it. A task with no task_id was never created."""

    pull_id: str
    spec: PullSpec
    tag: str
    requested_at: str
    task_id: str | None
    status_code: int | None
    status_message: str | None
    cost: float | None
    attempt: int = 0

    @property
    def created(self) -> bool:
        return self.task_id is not None and self.status_code == STATUS_TASK_CREATED


@dataclass(frozen=True)
class ReadyTask:
    task_id: str
    tag: str | None
    date_posted: str | None
    endpoint_advanced: str | None


@dataclass(frozen=True)
class TaskOutcome:
    """A finished task_get answer: the task's status, the task object, and the raw file it was read from."""

    task_id: str
    status_code: int | None
    status_message: str | None
    task: dict[str, Any]
    raw: RawFile
    completed_at: str


@dataclass(frozen=True)
class ParsedSerp:
    hits: tuple[OrganicHit, ...]
    spell_type: str | None
    spell_corrected: bool
    provider_datetime: str | None
    check_url: str | None
    pages_count: int | None
    items_count: int | None
    has_result: bool


@dataclass(frozen=True)
class LabsCall:
    """One Labs call: where its raw JSON is, what it cost, and whether it was read back from disk."""

    function: str
    request_key: str
    raw_path: str
    raw_sha256: str
    from_cache: bool
    cost: float | None
    task_id: str | None
    status_code: int | None
    status_message: str | None

    @property
    def ok(self) -> bool:
        return self.status_code == STATUS_OK


@dataclass(frozen=True)
class HistoricalOrganic:
    rank_group: int
    rank_absolute: int | None
    domain: str | None
    site: str
    url: str | None
    title: str | None
    is_new: bool | None
    previous_rank_absolute: int | None


@dataclass(frozen=True)
class HistoricalSnapshot:
    """One past result page. `datetime` is the provider's UTC time of the snapshot."""

    datetime: str | None
    check_url: str | None
    items_count: int | None
    spell_type: str | None
    organic: tuple[HistoricalOrganic, ...]

    @property
    def snapshot_date(self) -> date | None:
        return parse_utc(self.datetime).date() if self.datetime else None

    @property
    def max_rank_group(self) -> int | None:
        return max((hit.rank_group for hit in self.organic), default=None)


@dataclass(frozen=True)
class HistoricalSerpsResult:
    keyword: str
    location_code: int
    language_code: str
    call: LabsCall
    total_count: int | None
    snapshots: tuple[HistoricalSnapshot, ...]

    @property
    def ok(self) -> bool:
        return self.call.ok


@dataclass(frozen=True)
class TrafficMonth:
    """One month of estimated Google traffic for one target. Missing values stay None."""

    target: str
    item_type: str
    year: int
    month: int
    etv: float | None
    count: int | None


@dataclass(frozen=True)
class HistoricalTrafficResult:
    call: LabsCall
    targets_asked: tuple[str, ...]
    targets_returned: tuple[str, ...]
    rows: tuple[TrafficMonth, ...]

    @property
    def ok(self) -> bool:
        return self.call.ok

    def targets_with_data(self) -> tuple[str, ...]:
        """Targets with at least one month where the traffic estimate or the SERP count is above 0."""
        found = {row.target for row in self.rows if (row.etv or 0) > 0 or (row.count or 0) > 0}
        return tuple(target for target in self.targets_asked if target in found)


@dataclass(frozen=True)
class TrafficNow:
    target: str
    item_type: str
    etv: float | None
    count: int | None


@dataclass(frozen=True)
class BulkTrafficResult:
    call: LabsCall
    targets_asked: tuple[str, ...]
    targets_returned: tuple[str, ...]
    rows: tuple[TrafficNow, ...]

    @property
    def ok(self) -> bool:
        return self.call.ok

    def targets_with_data(self) -> tuple[str, ...]:
        found = {row.target for row in self.rows if (row.etv or 0) > 0 or (row.count or 0) > 0}
        return tuple(target for target in self.targets_asked if target in found)


@dataclass(frozen=True)
class RankedKeywordRow:
    target: str
    keyword: str | None
    search_volume: int | None
    item_type: str | None
    rank_group: int | None
    rank_absolute: int | None
    url: str | None
    domain: str | None
    site: str | None
    check_url: str | None
    is_lost: bool | None
    last_updated_time: str | None


@dataclass(frozen=True)
class RankedKeywordsResult:
    call: LabsCall
    target: str
    total_count: int | None
    rows: tuple[RankedKeywordRow, ...]

    @property
    def ok(self) -> bool:
        return self.call.ok


@dataclass(frozen=True)
class Balance:
    """The account balance in USD as DataForSEO reports it. The login is not kept."""

    balance_usd: Decimal | None
    total_usd: Decimal | None
    raw_path: str
    raw_sha256: str


@dataclass(frozen=True)
class Location:
    location_code: int
    location_name: str | None
    location_code_parent: int | None
    country_iso_code: str | None
    location_type: str | None


@dataclass(frozen=True)
class SampleReport:
    """What a $1 sample found, as recorded in the ledger."""

    kind: str
    findings: dict[str, Any]
    calls: int
    recorded: bool = True  # False: the sample learned nothing, the gate stays closed and it may be run again


# ----------------------------------------------------------------------------------------------
# Small helpers.
# ----------------------------------------------------------------------------------------------


def read_raw(path: str | Path) -> bytes:
    """The response bytes saved in a .json.gz raw file."""
    return gzip.decompress(Path(path).read_bytes())


def write_raw(path: Path, content: bytes) -> RawFile:
    """Gzip `content` to `path` atomically. Same bytes in, same file out (mtime 0)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(gzip.compress(content, mtime=0))
    os.replace(temporary, path)
    return RawFile(str(path), sha256_bytes(content))


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _utc_text(value: Any) -> str | None:
    if not value:
        return None
    try:
        return utc_iso(parse_utc(str(value)))
    except ValueError:
        return None


def _site(domain: Any, url: Any) -> str:
    return site_key(str(domain or url or ""))


def _spell(result: dict[str, Any]) -> tuple[str | None, bool]:
    spell = result.get("spell")
    if not spell:
        return None, False
    spell_type = str(spell.get("type")) if isinstance(spell, dict) and spell.get("type") else "unknown"
    return spell_type, spell_type not in SPELL_TYPES_ANSWERING_ORIGINAL


def parse_serp_task(task: dict[str, Any]) -> ParsedSerp:
    """Organic hits of one SERP task. rank = rank_group. Non-organic items and nested related results are skipped."""
    hits: list[OrganicHit] = []
    spell_type: str | None = None
    spell_corrected = False
    provider_datetime: str | None = None
    check_url: str | None = None
    pages_count: int | None = None
    items_count: int | None = None
    results = task.get("result") or []
    for result in results:
        if not isinstance(result, dict):
            continue
        result_spell, corrected = _spell(result)
        spell_type = spell_type or result_spell
        spell_corrected = spell_corrected or corrected
        provider_datetime = provider_datetime or _utc_text(result.get("datetime"))
        check_url = check_url or result.get("check_url")
        pages_count = pages_count if pages_count is not None else _int_or_none(result.get("pages_count"))
        items_count = items_count if items_count is not None else _int_or_none(result.get("items_count"))
        for item in result.get("items") or []:
            if not isinstance(item, dict) or item.get("type") != "organic":
                continue
            rank = _int_or_none(item.get("rank_group"))
            if rank is None or rank < 1:
                continue
            checks = item.get("checks") or []
            hits.append(
                OrganicHit(
                    rank=rank,
                    url=str(item.get("url") or ""),
                    domain=str(item.get("domain") or ""),
                    site=_site(item.get("domain"), item.get("url")),
                    title=item.get("title"),
                    rank_absolute=_int_or_none(item.get("rank_absolute")),
                    repaired="from_web" in checks,
                )
            )
    hits.sort(key=lambda hit: (hit.rank, hit.rank_absolute if hit.rank_absolute is not None else 10**9, hit.url))
    return ParsedSerp(
        hits=tuple(hits),
        spell_type=spell_type,
        spell_corrected=spell_corrected,
        provider_datetime=provider_datetime,
        check_url=check_url,
        pages_count=pages_count,
        items_count=items_count,
        has_result=bool([result for result in results if isinstance(result, dict)]),
    )


def _validate_spec(spec: PullSpec, *, max_depth: int) -> None:
    if not spec.search or len(spec.search) > MAX_KEYWORD_CHARS:
        raise ValueError(f"keyword must be 1 to {MAX_KEYWORD_CHARS} characters: {spec.search[:40]!r}")
    if int(spec.location_code) <= 0:
        raise ValueError(f"location_code {spec.location_code!r} is not a Google geo target id")
    if not spec.language_code:
        raise ValueError("language_code is required")
    if spec.device not in DEVICES:
        raise ValueError(f"device must be one of {DEVICES}")
    if not 1 <= spec.depth <= max_depth:
        raise ValueError(f"depth must be 1 to {max_depth} for this endpoint, not {spec.depth}")


def _check_target(target: str, *, allow_pages: bool) -> str:
    text = str(target or "").strip()
    if not text:
        raise ValueError("an empty target was given")
    lowered = text.lower()
    if not allow_pages and (lowered.startswith(("http://", "https://")) or "/" in lowered):
        raise ValueError(f"{text!r}: this endpoint takes domains only, without https:// and without a path")
    is_page = lowered.startswith(("http://", "https://")) or "/" in lowered
    if lowered.startswith("www.") and not (allow_pages and is_page):
        raise ValueError(f"{text!r}: DataForSEO wants domains without www.")
    if site_key(text) in EXCLUDED_SITES:
        raise ValueError(f"{text!r} is a client site that is never used to build or test. It is not sent.")
    return text


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def request_key(path: str, task: dict[str, Any]) -> str:
    """A stable key for one Labs request: the same request always maps to the same raw file."""
    return sha256_text(canonical_json({"path": path, "task": task}))[:32]


# ----------------------------------------------------------------------------------------------
# The client.
# ----------------------------------------------------------------------------------------------


class DataForSeoClient:
    """Typed DataForSEO client. Paid calls are metered through `ledger`. Free calls need no ledger."""

    def __init__(
        self,
        login: str,
        password: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = DFS_BASE,
        ledger: SpendLedger | None = None,
        raw_dir: str | Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utcnow,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        if not login or not password:
            raise ProviderConfigError("DataForSEO needs an API login and an API password. Neither may be empty.")
        self._auth = (login, password)
        self._owns = client is None
        self._http = client or httpx.Client(timeout=timeout_seconds)
        self.base_url = base_url.rstrip("/")
        # The sandbox is free and answers with dummy data: its calls skip the ledger, its pulls carry
        # PROVIDER_SANDBOX (so readings from them are SYNTHETIC), its raw files go under their own
        # folder (so no dummy answer is ever read back as a paid one), and it runs no $1 sample.
        self.sandbox = self.base_url == DFS_SANDBOX.rstrip("/")
        self.provider = PROVIDER_SANDBOX if self.sandbox else PROVIDER
        self.ledger = ledger
        self.raw_dir = Path(raw_dir) if raw_dir is not None else forecast_paths().raw
        self._sleep = sleep
        self._now = now
        self.poll_seconds = float(poll_seconds)
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        self._counter = 0

    def __repr__(self) -> str:
        return f"DataForSeoClient(base_url={self.base_url!r}, raw_dir={str(self.raw_dir)!r})"

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def __enter__(self) -> "DataForSeoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _next_stamp(self) -> str:
        self._counter += 1
        return f"{self._now().astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{self._counter:04d}"

    def _require_ledger(self, item: str) -> SpendLedger:
        if self.ledger is None:
            raise BudgetError(
                f"Refused: {item} is a paid call and this client has no spend ledger. "
                "Pass ledger=SpendLedger(path) and approve an amount on it first."
            )
        return self.ledger

    @staticmethod
    def _json_or_none(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    def _raise_for_envelope(
        self, response: httpx.Response, payload: Any, *, what: str, posted: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """The envelope when HTTP is 200 and the top-level status is 20000. Otherwise ProviderError."""
        code = _int_or_none(payload.get("status_code")) if isinstance(payload, dict) else None
        message = payload.get("status_message") if isinstance(payload, dict) else None
        if response.status_code != 200 or code != STATUS_OK:
            hint = FATAL_HINTS.get(code or -1, "")
            shown = f"status {code} ({message})" if code is not None else f"HTTP {response.status_code} with no DataForSEO status"
            raise ProviderError(
                f"DataForSEO refused {what}: {shown}. {hint}".strip(),
                provider=PROVIDER,
                status_code=code if code is not None else response.status_code,
                http_status=response.status_code,
                posted_task_ids=posted,
            )
        return payload

    def _free_get(self, path: str, *, raw_path: Path | None, what: str) -> tuple[dict[str, Any], RawFile | None]:
        """A free GET, retried on a dropped connection or a server error."""
        last_error = ""
        for attempt in range(FREE_GET_RETRIES + 1):
            try:
                response = self._http.get(self._url(path), auth=self._auth)
            except httpx.TransportError as error:
                last_error = f"{type(error).__name__}"
            else:
                raw = write_raw(raw_path, response.content) if raw_path is not None else None
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                else:
                    payload = self._json_or_none(response)
                    return self._raise_for_envelope(response, payload, what=what), raw
            if attempt < FREE_GET_RETRIES:
                self._sleep(FREE_GET_BACKOFF_SECONDS * (attempt + 1))
        raise ProviderError(
            f"DataForSEO did not answer {what} after {FREE_GET_RETRIES + 1} tries ({last_error}).",
            provider=PROVIDER,
        )

    def _paid_post(
        self,
        path: str,
        body: list[dict[str, Any]],
        *,
        item: str,
        kind: str,
        estimate: Decimal,
        raw_path: Path,
        sample: bool = False,
    ) -> tuple[dict[str, Any], RawFile, httpx.Response]:
        """Reserve, POST, save the raw response, settle with the provider's cost. Returns the parsed JSON (not yet checked).

        On the sandbox (free, dummy data) nothing is reserved or settled.
        """
        if self.sandbox:
            try:
                response = self._http.post(self._url(path), json=body, auth=self._auth)
            except httpx.TransportError as error:
                raise ProviderError(f"The DataForSEO sandbox did not answer {item} ({type(error).__name__}).", provider=PROVIDER) from error
            return self._json_or_none(response), write_raw(raw_path, response.content), response
        ledger = self._require_ledger(item)
        with ledger.metered(item, kind, estimate, sample=sample) as meter:
            total = Decimal(0)
            cost_known = True
            answered = False
            attempt = 0
            while True:
                target = raw_path if attempt == 0 else raw_path.with_name(raw_path.name.replace(".json.gz", f".try{attempt}.json.gz"))
                meter.mark_sent()
                try:
                    response = self._http.post(self._url(path), json=body, auth=self._auth)
                except httpx.ConnectError as error:
                    if not answered:
                        meter.mark_unsent()
                    raise ProviderError(
                        f"Could not connect to DataForSEO for {item} ({type(error).__name__}). Nothing was sent.",
                        provider=PROVIDER,
                    ) from error
                except httpx.TransportError as error:
                    raise ProviderError(
                        f"The connection to DataForSEO broke during {item} ({type(error).__name__}). "
                        "The call may have been charged, so the ledger keeps the estimate.",
                        provider=PROVIDER,
                    ) from error
                answered = True
                raw = write_raw(target, response.content)
                payload = self._json_or_none(response)
                cost = payload.get("cost") if isinstance(payload, dict) else None
                if cost is None:
                    # No cost field. A call refused before it ran (bad login, no money) is not charged.
                    # Anything else is charged at the estimate.
                    if response.status_code not in UNCHARGED_HTTP_STATUSES:
                        cost_known = False
                else:
                    total += to_decimal(cost)
                code = _int_or_none(payload.get("status_code")) if isinstance(payload, dict) else None
                if code in THROTTLE_CODES and attempt < THROTTLE_RETRIES:
                    attempt += 1
                    self._sleep(THROTTLE_BACKOFF_SECONDS * attempt)
                    continue
                meter.settle(total if cost_known else None)
                return payload, raw, response

    # -- SERP: Standard queue -----------------------------------------------------------------

    def _serp_task_body(self, spec: PullSpec, tag: str, *, priority: int | None, resolve_poisoned_results: bool) -> dict[str, Any]:
        task: dict[str, Any] = {
            "keyword": spec.search,
            "location_code": int(spec.location_code),
            "language_code": spec.language_code,
            "device": spec.device,
            "depth": int(spec.depth),
            "resolve_poisoned_results": bool(resolve_poisoned_results),
            "tag": tag,
        }
        if priority is not None:
            task["priority"] = priority
        return task

    def new_pull_ids(self, specs: Sequence[PullSpec]) -> list[str]:
        """Our pull ids for these specs: UTC time, a per-client counter, the index, and a hash of the spec."""
        stamp = self._next_stamp()
        return [
            f"dfs-{stamp}-{index:04d}-{sha256_text(canonical_json(spec.__dict__))[:8]}"
            for index, spec in enumerate(specs)
        ]

    def post_serp_tasks(
        self,
        specs: Sequence[PullSpec],
        *,
        mode: str = "standard_normal",
        resolve_poisoned_results: bool = True,
        pull_ids: Sequence[str] | None = None,
        attempt: int = 0,
        sample: bool = False,
    ) -> list[PostedTask]:
        """Post SERP tasks to the Standard queue, at most 100 per call. One PostedTask per spec, in order."""
        if mode not in STANDARD_MODES:
            raise ValueError(f"post_serp_tasks runs the Standard queue only ({STANDARD_MODES}). Use serp_fetch(mode='live') for Live.")
        for spec in specs:
            _validate_spec(spec, max_depth=MAX_DEPTH_STANDARD)
        ids = list(pull_ids) if pull_ids is not None else self.new_pull_ids(specs)
        if len(ids) != len(specs):
            raise ValueError("pull_ids must have one id per spec")
        if not self.sandbox and specs:
            # The whole batch must fit before the first chunk is posted, so a batch the approval
            # cannot cover is refused with nothing spent, not cut off after some chunks were paid.
            total = sum((self._serp_estimate(spec, mode, resolve_poisoned_results) for spec in specs), Decimal(0))
            self._require_ledger("SERP task_post").check(
                f"SERP task_post of {len(specs)} task(s) in {math.ceil(len(specs) / MAX_TASKS_PER_POST)} call(s), {mode}",
                SERP_MODE_KINDS[mode],
                total,
            )
        posted: list[PostedTask] = []
        for start in range(0, len(specs), MAX_TASKS_PER_POST):
            chunk = list(zip(ids[start : start + MAX_TASKS_PER_POST], specs[start : start + MAX_TASKS_PER_POST], strict=True))
            try:
                posted.extend(
                    self._post_chunk(
                        chunk, mode=mode, resolve_poisoned_results=resolve_poisoned_results, attempt=attempt, already=posted, sample=sample
                    )
                )
            except (BudgetError, ProviderError) as error:
                # Chunks already posted were paid for. Hand their records up with the error.
                error.posted_tasks = tuple(posted) + tuple(getattr(error, "posted_tasks", ()) or ())
                raise
        return posted

    @staticmethod
    def _serp_estimate(spec: PullSpec, mode: str, resolve_poisoned_results: bool) -> Decimal:
        """The provider's billing formula for one task, with the x5 for a keyword holding a search operator."""
        return serp_cost(spec.depth, mode=mode, resolve_poisoned_results=resolve_poisoned_results, keyword=spec.search)

    def _post_chunk(
        self,
        chunk: list[tuple[str, PullSpec]],
        *,
        mode: str,
        resolve_poisoned_results: bool,
        attempt: int,
        already: list[PostedTask],
        sample: bool = False,
    ) -> list[PostedTask]:
        requested_at = utc_iso(self._now())
        tags = [f"rs:{pull_id}" if attempt == 0 else f"rs:{pull_id}:a{attempt}" for pull_id, _ in chunk]
        for tag in tags:
            if len(tag) > MAX_TAG_CHARS:
                raise ValueError(f"tag longer than {MAX_TAG_CHARS} characters: {tag[:40]}")
        body = [
            self._serp_task_body(spec, tag, priority=PRIORITY_BY_MODE[mode], resolve_poisoned_results=resolve_poisoned_results)
            for (pull_id, spec), tag in zip(chunk, tags, strict=True)
        ]
        estimate = sum((self._serp_estimate(spec, mode, resolve_poisoned_results) for _, spec in chunk), Decimal(0))
        batch = sha256_text("|".join(tags))[:12]
        raw_path = self.raw_dir / self.provider / requested_at[:10] / f"task_post_{batch}.json.gz"
        previous = tuple(task.task_id for task in already if task.created and task.task_id)
        payload, _raw, response = self._paid_post(
            PATH_TASK_POST,
            body,
            item=f"SERP task_post of {len(body)} task(s), {mode}",
            kind=SERP_MODE_KINDS[mode],
            estimate=estimate,
            raw_path=raw_path,
            sample=sample,
        )
        envelope = self._raise_for_envelope(response, payload, what="the SERP task_post", posted=previous)
        tasks = [task for task in envelope.get("tasks") or [] if isinstance(task, dict)]
        by_tag = {str((task.get("data") or {}).get("tag")): task for task in tasks if (task.get("data") or {}).get("tag")}
        created_ids = tuple(
            str(task["id"]) for task in tasks if task.get("id") and _int_or_none(task.get("status_code")) == STATUS_TASK_CREATED
        )
        result: list[PostedTask] = []
        fatal: tuple[int, Any] | None = None
        for index, ((pull_id, spec), tag) in enumerate(zip(chunk, tags, strict=True)):
            task = by_tag.get(tag)
            if task is None and not by_tag and len(tasks) == len(chunk):
                task = tasks[index]
            if task is None:
                result.append(PostedTask(pull_id, spec, tag, requested_at, None, None, "No task came back for this pull.", None, attempt))
                continue
            code = _int_or_none(task.get("status_code"))
            if code in FATAL_CODES and fatal is None:
                fatal = (code, task.get("status_message"))
            task_id = str(task["id"]) if task.get("id") and code == STATUS_TASK_CREATED else None
            result.append(
                PostedTask(pull_id, spec, tag, requested_at, task_id, code, task.get("status_message"), _float_or_none(task.get("cost")), attempt)
            )
        if fatal is not None:
            code, message = fatal
            error = ProviderError(
                f"DataForSEO refused a SERP task: status {code} ({message}). {FATAL_HINTS.get(code, '')}".strip(),
                provider=PROVIDER,
                status_code=code,
                http_status=response.status_code,
                posted_task_ids=previous + created_ids,
            )
            error.posted_tasks = tuple(result)  # what this call answered; the created ones were paid for
            raise error
        return result

    def tasks_ready(self) -> list[ReadyTask]:
        """Finished SERP tasks not collected yet (free, about 20 calls a minute)."""
        envelope, _ = self._free_get(PATH_TASKS_READY, raw_path=None, what="tasks_ready")
        ready: list[ReadyTask] = []
        for task in envelope.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            for item in task.get("result") or []:
                if isinstance(item, dict) and item.get("id"):
                    ready.append(ReadyTask(str(item["id"]), item.get("tag"), item.get("date_posted"), item.get("endpoint_advanced")))
        return ready

    def get_serp_task(self, task_id: str, *, posted_day: str) -> TaskOutcome | None:
        """task_get/advanced for one task (free). None while the task is still queued. The raw JSON is saved first."""
        raw_path = self.raw_dir / self.provider / posted_day / f"{task_id}.json.gz"
        envelope, raw = self._free_get(f"{PATH_TASK_GET_ADVANCED}/{task_id}", raw_path=raw_path, what=f"task_get for {task_id}")
        assert raw is not None
        tasks = [task for task in envelope.get("tasks") or [] if isinstance(task, dict)]
        task = next((item for item in tasks if str(item.get("id")) == task_id), tasks[0] if len(tasks) == 1 else None)
        completed_at = utc_iso(self._now())
        if task is None:
            return TaskOutcome(task_id, None, "task_get returned no task for this id.", {}, raw, completed_at)
        code = _int_or_none(task.get("status_code"))
        if code in NOT_READY_CODES:
            return None
        if code in FATAL_CODES:
            raise ProviderError(
                f"DataForSEO refused task_get for {task_id}: status {code} ({task.get('status_message')}). {FATAL_HINTS.get(code, '')}".strip(),
                provider=PROVIDER,
                status_code=code,
            )
        return TaskOutcome(task_id, code, task.get("status_message"), task, raw, completed_at)

    def wait_for_tasks(
        self,
        posted: Sequence[PostedTask],
        *,
        timeout_seconds: float | None = None,
        poll_seconds: float | None = None,
        use_tasks_ready: bool = True,
    ) -> dict[str, TaskOutcome]:
        """Collect finished tasks. Tasks still queued at the timeout are left out of the answer."""
        timeout = self.wait_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        poll = self.poll_seconds if poll_seconds is None else float(poll_seconds)
        day_of = {task.task_id: task.requested_at[:10] for task in posted if task.created and task.task_id}
        pending = set(day_of)
        done: dict[str, TaskOutcome] = {}
        start = self._now()
        rounds = 0
        while pending:
            if use_tasks_ready:
                listed = {ready.task_id for ready in self.tasks_ready()}
                asking = pending & listed
                if rounds % DIRECT_POLL_EVERY_ROUNDS == DIRECT_POLL_EVERY_ROUNDS - 1:
                    asking = set(pending)
            else:
                asking = set(pending)
            for task_id in sorted(asking):
                outcome = self.get_serp_task(task_id, posted_day=day_of[task_id])
                if outcome is not None:
                    done[task_id] = outcome
                    pending.discard(task_id)
            if not pending:
                break
            elapsed = (self._now() - start).total_seconds()
            if elapsed >= timeout:
                break
            self._sleep(max(0.0, min(poll, timeout - elapsed)))
            rounds += 1
        return done

    def _pull_from(self, posted: PostedTask, outcome: TaskOutcome | None, *, endpoint: str, timeout: float) -> Pull:
        if not posted.created:
            return Pull(
                pull_id=posted.pull_id,
                provider=self.provider,
                endpoint=endpoint,
                spec=posted.spec,
                requested_at=posted.requested_at,
                completed_at=posted.requested_at,
                ok=False,
                status_code=posted.status_code,
                status_message=posted.status_message or "The task was not created.",
                task_id=posted.task_id,
                cost=posted.cost,
            )
        if outcome is None:
            return Pull(
                pull_id=posted.pull_id,
                provider=self.provider,
                endpoint=endpoint,
                spec=posted.spec,
                requested_at=posted.requested_at,
                completed_at=utc_iso(self._now()),
                ok=False,
                status_code=None,
                status_message=(
                    f"Not ready after {timeout:.0f} seconds. The task was paid for and its result is kept by "
                    "DataForSEO for 30 days. Collect it later with collect()."
                ),
                task_id=posted.task_id,
                cost=posted.cost,
            )
        return _pull_from_task(
            pull_id=posted.pull_id,
            spec=posted.spec,
            endpoint=endpoint,
            requested_at=posted.requested_at,
            completed_at=outcome.completed_at,
            task=outcome.task,
            code=outcome.status_code,
            message=outcome.status_message,
            task_id=posted.task_id,
            cost=posted.cost,
            raw=outcome.raw,
            provider=self.provider,
        )

    def collect(self, posted: Sequence[PostedTask], *, timeout_seconds: float | None = None) -> list[Pull]:
        """Wait for posted tasks and turn each into a Pull, in order. Free: the tasks are already paid for."""
        timeout = self.wait_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        outcomes = self.wait_for_tasks(posted, timeout_seconds=timeout)
        return [
            self._pull_from(task, outcomes.get(task.task_id or ""), endpoint=ENDPOINT_STANDARD, timeout=timeout)
            for task in posted
        ]

    def serp_fetch(
        self,
        specs: Sequence[PullSpec],
        *,
        mode: str = "standard_normal",
        resolve_poisoned_results: bool = True,
        max_resubmits: int = DEFAULT_MAX_RESUBMITS,
        sample: bool = False,
    ) -> list[Pull]:
        """One Pull per spec, in order. Standard queue unless mode='live' is passed by name."""
        if not specs:
            return []
        if mode == "live":
            for spec in specs:
                _validate_spec(spec, max_depth=MAX_DEPTH_LIVE)
            return [self._live_pull(spec, resolve_poisoned_results=resolve_poisoned_results, sample=sample) for spec in specs]
        if mode not in STANDARD_MODES:
            raise ValueError(f"unknown SERP mode {mode!r}. Choose standard_normal, standard_high or live.")
        pull_ids = self.new_pull_ids(specs)
        posted_all: list[PostedTask] = []
        pulls: list[Pull] | None = None
        again: list[int] = []
        try:
            posted = self.post_serp_tasks(
                specs, mode=mode, resolve_poisoned_results=resolve_poisoned_results, pull_ids=pull_ids, sample=sample
            )
            posted_all.extend(posted)
            pulls = self.collect(posted)
            for attempt in range(1, max_resubmits + 1):
                again = [index for index, pull in enumerate(pulls) if not pull.ok and pull.status_code in RESUBMIT_CODES]
                if not again:
                    break
                self._sleep(RESUBMIT_BACKOFF_SECONDS * attempt)
                try:
                    reposted = self.post_serp_tasks(
                        [specs[index] for index in again],
                        mode=mode,
                        resolve_poisoned_results=resolve_poisoned_results,
                        pull_ids=[pull_ids[index] for index in again],
                        attempt=attempt,
                        sample=sample,
                    )
                except BudgetError as refusal:
                    # A resubmission is an extra the approval does not cover: keep every pull already
                    # collected (and any resubmitted task that was paid for), say why the rest were
                    # not tried again, and stop resubmitting. Nothing is thrown away.
                    self._attach_paid_pulls(refusal, specs, pull_ids, pulls, again, tuple(refusal.posted_tasks or ()))
                    note = f" Not resubmitted: {refusal}"
                    return [
                        pull if pull.ok or index not in again else replace(pull, status_message=f"{pull.status_message or ''}{note}".strip())
                        for index, pull in enumerate(refusal.pulls or pulls)
                    ]
                posted_all.extend(reposted)
                for index, pull in zip(again, self.collect(reposted), strict=True):
                    pulls[index] = pull
            return pulls
        except (BudgetError, ProviderError) as error:
            refused = tuple(getattr(error, "posted_tasks", ()) or ())
            if pulls is None and not refused:
                refused = tuple(posted_all)  # everything was posted, then collecting failed
            if isinstance(error, ProviderError) and not error.posted_task_ids:
                error.posted_task_ids = tuple(
                    task.task_id for task in posted_all + list(refused) if task.created and task.task_id
                )
            self._attach_paid_pulls(error, specs, pull_ids, pulls, again, refused)
            raise

    def _attach_paid_pulls(
        self,
        error: BudgetError | ProviderError,
        specs: Sequence[PullSpec],
        pull_ids: Sequence[str],
        pulls: list[Pull] | None,
        again: list[int],
        refused: tuple[PostedTask, ...],
    ) -> None:
        """When a batch stops after some tasks were paid for, put one Pull per spec on the error (`error.pulls`).

        Paid tasks are collected (free). A paid task that cannot be collected now is a failed Pull with
        its task id, so it can be collected later. A spec that was never posted is a failed Pull that
        says why. Nothing is attached when nothing was paid for in this batch.
        """
        created = [task for task in refused if task.created]
        if not created and pulls is None:
            return
        collected: dict[str, Pull] = {}
        if created:
            try:
                for task, pull in zip(created, self.collect(created), strict=True):
                    collected[task.pull_id] = pull
            except ProviderError:
                for task in created:
                    collected[task.pull_id] = self._pull_from(task, None, endpoint=ENDPOINT_STANDARD, timeout=0.0)
        stamp = utc_iso(self._now())
        reason = f"Not sent: the batch stopped before this task was posted. {type(error).__name__}: {error}"
        if pulls is None:  # the first posting stopped part way
            by_id = {task.pull_id: task for task in refused}
            out: list[Pull] = []
            for pull_id, spec in zip(pull_ids, specs, strict=True):
                if pull_id in collected:
                    out.append(collected[pull_id])
                elif pull_id in by_id:
                    out.append(self._pull_from(by_id[pull_id], None, endpoint=ENDPOINT_STANDARD, timeout=0.0))
                else:
                    out.append(Pull(pull_id=pull_id, provider=self.provider, endpoint=ENDPOINT_STANDARD, spec=spec,
                                    requested_at=stamp, completed_at=stamp, ok=False, status_message=reason))
            error.pulls = tuple(out)
            return
        merged = list(pulls)  # a resubmission stopped: keep every collected pull, swap in what the resubmission got
        for index in again:
            if merged[index].pull_id in collected:
                merged[index] = collected[merged[index].pull_id]
        error.pulls = tuple(merged)


    # -- SERP: Live (by name only) -------------------------------------------------------------

    def _live_pull(self, spec: PullSpec, *, resolve_poisoned_results: bool, sample: bool) -> Pull:
        _validate_spec(spec, max_depth=MAX_DEPTH_LIVE)
        pull_id = self.new_pull_ids([spec])[0]
        tag = f"rs:{pull_id}"
        requested_at = utc_iso(self._now())
        body = [self._serp_task_body(spec, tag, priority=None, resolve_poisoned_results=resolve_poisoned_results)]
        raw_path = self.raw_dir / self.provider / requested_at[:10] / f"live_{pull_id}.json.gz"
        payload, raw, response = self._paid_post(
            PATH_LIVE_ADVANCED,
            body,
            item=f"SERP live/advanced for one search ({spec.search[:40]})",
            kind=SERP_MODE_KINDS["live"],
            estimate=self._serp_estimate(spec, "live", resolve_poisoned_results),
            raw_path=raw_path,
            sample=sample,
        )
        envelope = self._raise_for_envelope(response, payload, what="the SERP live call")
        tasks = [task for task in envelope.get("tasks") or [] if isinstance(task, dict)]
        completed_at = utc_iso(self._now())
        if not tasks:
            return Pull(pull_id, self.provider, ENDPOINT_LIVE, spec, requested_at, completed_at, ok=False,
                        status_message="The live call returned no task.", raw_path=raw.path, raw_sha256=raw.sha256)
        task = tasks[0]
        code = _int_or_none(task.get("status_code"))
        if code in FATAL_CODES:
            raise ProviderError(
                f"DataForSEO refused the live task: status {code} ({task.get('status_message')}). {FATAL_HINTS.get(code, '')}".strip(),
                provider=PROVIDER,
                status_code=code,
            )
        return _pull_from_task(
            pull_id=pull_id,
            spec=spec,
            endpoint=ENDPOINT_LIVE,
            requested_at=requested_at,
            completed_at=completed_at,
            task=task,
            code=code,
            message=task.get("status_message"),
            task_id=str(task["id"]) if task.get("id") else None,
            cost=_float_or_none(task.get("cost")),
            raw=raw,
            provider=self.provider,
        )

    # -- Labs ----------------------------------------------------------------------------------

    def _labs_call(
        self,
        path: str,
        task: dict[str, Any],
        *,
        kind: str,
        estimate: Decimal,
        item: str,
        sample: bool = False,
    ) -> tuple[LabsCall, dict[str, Any]]:
        """One Labs call, or its saved answer when the same request was already paid for."""
        function = path.split("/")[-2]
        key = request_key(path, task)
        raw_path = self.raw_dir / self.provider / "labs" / function / f"{key}.json.gz"
        if raw_path.exists():
            content = read_raw(raw_path)
            payload = json.loads(content)
            labs_task = _first_task(payload)
            return self._labs_record(function, key, RawFile(str(raw_path), sha256_bytes(content)), labs_task, from_cache=True), labs_task
        try:
            payload, raw, response = self._paid_post(
                path, [task], item=item, kind=kind, estimate=estimate, raw_path=raw_path, sample=sample
            )
            envelope = self._raise_for_envelope(response, payload, what=f"the Labs {function} call")
            labs_task = _first_task(envelope)
            code = _int_or_none(labs_task.get("status_code"))
            if code in FATAL_CODES:
                raise ProviderError(
                    f"DataForSEO refused the Labs {function} task: status {code} ({labs_task.get('status_message')}). "
                    f"{FATAL_HINTS.get(code, '')}".strip(),
                    provider=PROVIDER,
                    status_code=code,
                )
        except ProviderError:
            self._set_aside(raw_path)
            raise
        record = self._labs_record(function, key, raw, labs_task, from_cache=False)
        if not record.ok:
            # Keep the answer for the audit, but out of the resume path, so a later run asks again.
            moved = self._set_aside(raw_path)
            record = LabsCall(**{**record.__dict__, "raw_path": str(moved) if moved else record.raw_path})
        return record, labs_task

    def _set_aside(self, raw_path: Path) -> Path | None:
        if not raw_path.exists():
            return None
        stamp = self._now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = raw_path.with_name(raw_path.name.replace(".json.gz", f".failed-{stamp}.json.gz"))
        os.replace(raw_path, target)
        return target

    @staticmethod
    def _labs_record(function: str, key: str, raw: RawFile, task: dict[str, Any], *, from_cache: bool) -> LabsCall:
        return LabsCall(
            function=function,
            request_key=key,
            raw_path=raw.path,
            raw_sha256=raw.sha256,
            from_cache=from_cache,
            cost=_float_or_none(task.get("cost")),
            task_id=str(task["id"]) if task.get("id") else None,
            status_code=_int_or_none(task.get("status_code")),
            status_message=task.get("status_message"),
        )

    def historical_serps(
        self,
        keyword: str,
        location_code: int,
        language_code: str,
        date_from: date | None = None,
        date_to: date | None = None,
        *,
        sample: bool = False,
    ) -> HistoricalSerpsResult:
        """Past result pages for one keyword (country level). Billed per snapshot returned."""
        if not keyword or len(keyword) > MAX_KEYWORD_CHARS:
            raise ValueError(f"keyword must be 1 to {MAX_KEYWORD_CHARS} characters")
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValueError(f"date_from {date_from} is after date_to {date_to}")
        task: dict[str, Any] = {"keyword": keyword, "location_code": int(location_code), "language_code": language_code}
        if date_from is not None:
            task["date_from"] = date_from.isoformat()
        if date_to is not None:
            task["date_to"] = date_to.isoformat()
        today = self._now().astimezone(timezone.utc).date()
        start = date_from or (today - timedelta(days=365))
        end = date_to or today
        months = max(1, (end.year - start.year) * 12 + (end.month - start.month) + 1)
        call, labs_task = self._labs_call(
            PATH_HISTORICAL_SERPS,
            task,
            kind=KIND_HISTORICAL_SERPS,
            estimate=historical_serps_estimate(months),
            item=f"Historical SERPs for one keyword over {months} months",
            sample=sample,
        )
        total_count: int | None = None
        snapshots: list[HistoricalSnapshot] = []
        if call.ok:
            for result in labs_task.get("result") or []:
                if not isinstance(result, dict):
                    continue
                total_count = _int_or_none(result.get("total_count"))
                for snap in result.get("items") or []:
                    if isinstance(snap, dict):
                        snapshots.append(_historical_snapshot(snap))
        snapshots.sort(key=lambda snap: snap.datetime or "")
        return HistoricalSerpsResult(keyword, int(location_code), language_code, call, total_count, tuple(snapshots))

    def historical_bulk_traffic_estimation(
        self,
        targets: Sequence[str],
        location_code: int | None = None,
        language_code: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        item_types: Sequence[str] = ("organic",),
        *,
        sample: bool = False,
    ) -> HistoricalTrafficResult:
        """Monthly estimated Google traffic for up to 1,000 domains in one task."""
        asked = tuple(_unique(_check_target(target, allow_pages=False) for target in targets))
        if not asked or len(asked) > LABS_MAX_TARGETS_PER_TASK:
            raise ValueError(f"give 1 to {LABS_MAX_TARGETS_PER_TASK} targets per task, not {len(asked)}")
        task: dict[str, Any] = {"targets": list(asked), "item_types": list(item_types)}
        _put_location(task, location_code, language_code)
        if date_from is not None:
            task["date_from"] = date_from.isoformat()
        if date_to is not None:
            task["date_to"] = date_to.isoformat()
        call, labs_task = self._labs_call(
            PATH_HISTORICAL_TRAFFIC,
            task,
            kind=KIND_HISTORICAL_TRAFFIC,
            estimate=historical_traffic_cost(len(asked)),
            item=f"Historical Bulk Traffic Estimation for {len(asked)} sites",
            sample=sample,
        )
        rows: list[TrafficMonth] = []
        returned: list[str] = []
        if call.ok:
            for item in _result_items(labs_task):
                target = str(item.get("target") or "")
                returned.append(target)
                for item_type, months in sorted((item.get("metrics") or {}).items()):
                    for month in months or []:
                        if not isinstance(month, dict):
                            continue
                        year, number = _int_or_none(month.get("year")), _int_or_none(month.get("month"))
                        if year is None or number is None:
                            continue
                        rows.append(TrafficMonth(target, item_type, year, number, _float_or_none(month.get("etv")), _int_or_none(month.get("count"))))
        rows.sort(key=lambda row: (row.target, row.item_type, row.year, row.month))
        return HistoricalTrafficResult(call, asked, tuple(_unique(returned)), tuple(rows))

    def bulk_traffic_estimation(
        self,
        targets: Sequence[str],
        location_code: int | None = None,
        language_code: str | None = None,
        item_types: Sequence[str] = ("organic",),
        *,
        sample: bool = False,
    ) -> BulkTrafficResult:
        """Current estimated Google traffic for up to 1,000 domains or pages in one task."""
        asked = tuple(_unique(_check_target(target, allow_pages=True) for target in targets))
        if not asked or len(asked) > LABS_MAX_TARGETS_PER_TASK:
            raise ValueError(f"give 1 to {LABS_MAX_TARGETS_PER_TASK} targets per task, not {len(asked)}")
        task: dict[str, Any] = {"targets": list(asked), "item_types": list(item_types)}
        _put_location(task, location_code, language_code)
        call, labs_task = self._labs_call(
            PATH_BULK_TRAFFIC,
            task,
            kind=KIND_BULK_TRAFFIC,
            estimate=bulk_traffic_cost(len(asked)),
            item=f"Bulk Traffic Estimation for {len(asked)} sites",
            sample=sample,
        )
        rows: list[TrafficNow] = []
        returned: list[str] = []
        if call.ok:
            for item in _result_items(labs_task):
                target = str(item.get("target") or "")
                returned.append(target)
                for item_type, metric in sorted((item.get("metrics") or {}).items()):
                    if isinstance(metric, dict):
                        rows.append(TrafficNow(target, item_type, _float_or_none(metric.get("etv")), _int_or_none(metric.get("count"))))
        rows.sort(key=lambda row: (row.target, row.item_type))
        return BulkTrafficResult(call, asked, tuple(_unique(returned)), tuple(rows))

    def ranked_keywords(
        self,
        target: str,
        location_code: int | None = None,
        language_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        item_types: Sequence[str] | None = None,
        load_rank_absolute: bool = False,
        historical_serp_mode: str = "live",
        sample: bool = False,
    ) -> RankedKeywordsResult:
        """Searches a site ranks for and where, at the standard Labs rate (clickstream data is never asked for)."""
        checked = _check_target(target, allow_pages=True)
        if not 1 <= limit <= MAX_RANKED_KEYWORDS_LIMIT:
            raise ValueError(f"limit must be 1 to {MAX_RANKED_KEYWORDS_LIMIT}")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if historical_serp_mode not in ("live", "lost", "all"):
            raise ValueError("historical_serp_mode must be live, lost or all")
        task: dict[str, Any] = {
            "target": checked,
            "limit": int(limit),
            "offset": int(offset),
            "load_rank_absolute": bool(load_rank_absolute),
            "historical_serp_mode": historical_serp_mode,
        }
        if item_types is not None:
            task["item_types"] = list(item_types)
        _put_location(task, location_code, language_code)
        call, labs_task = self._labs_call(
            PATH_RANKED_KEYWORDS,
            task,
            kind=KIND_RANKED_KEYWORDS,
            estimate=ranked_keywords_cost(1, limit),
            item=f"ranked keywords for one site, up to {limit} rows",
            sample=sample,
        )
        total_count: int | None = None
        rows: list[RankedKeywordRow] = []
        if call.ok:
            for result in labs_task.get("result") or []:
                if not isinstance(result, dict):
                    continue
                total_count = _int_or_none(result.get("total_count"))
                for item in result.get("items") or []:
                    if isinstance(item, dict):
                        rows.append(_ranked_row(checked, item))
        return RankedKeywordsResult(call, checked, total_count, tuple(rows))

    # -- resumable chunked helpers ---------------------------------------------------------------

    def historical_traffic_chunks(
        self, targets: Sequence[str], *, chunk_size: int = LABS_MAX_TARGETS_PER_TASK, **options: Any
    ) -> list[HistoricalTrafficResult]:
        """Historical traffic for many sites, in tasks of `chunk_size`. A chunk already on disk is not paid for again."""
        return [self.historical_bulk_traffic_estimation(chunk, **options) for chunk in _chunks(targets, chunk_size)]

    def bulk_traffic_chunks(
        self, targets: Sequence[str], *, chunk_size: int = LABS_MAX_TARGETS_PER_TASK, **options: Any
    ) -> list[BulkTrafficResult]:
        return [self.bulk_traffic_estimation(chunk, **options) for chunk in _chunks(targets, chunk_size)]

    def ranked_keywords_many(self, targets: Sequence[str], **options: Any) -> list[RankedKeywordsResult]:
        return [self.ranked_keywords(target, **options) for target in _unique(targets)]

    def historical_serps_many(self, keywords: Sequence[str], location_code: int, language_code: str, **options: Any) -> list[HistoricalSerpsResult]:
        return [self.historical_serps(keyword, location_code, language_code, **options) for keyword in _unique(keywords)]

    # -- free account calls -----------------------------------------------------------------------

    def user_data(self) -> Balance:
        """The account balance (free). The raw answer is saved, the login is not copied into the record."""
        stamp = self._next_stamp()
        raw_path = self.raw_dir / self.provider / _day(stamp) / f"user_data_{stamp}.json.gz"
        envelope, raw = self._free_get(PATH_USER_DATA, raw_path=raw_path, what="user_data")
        assert raw is not None
        result = next(iter(_first_task(envelope).get("result") or []), None) or {}
        money = result.get("money") or {}
        balance = money.get("balance")
        total = money.get("total")
        return Balance(
            balance_usd=to_decimal(balance) if isinstance(balance, (int, float, str)) and not isinstance(balance, bool) else None,
            total_usd=to_decimal(total) if isinstance(total, (int, float, str)) and not isinstance(total, bool) else None,
            raw_path=raw.path,
            raw_sha256=raw.sha256,
        )

    def locations(self, country_iso_code: str = "us") -> list[Location]:
        """SERP locations for one country (free). Pick a City location_code from here."""
        code = country_iso_code.strip().lower()
        if not code.isalpha() or len(code) != 2:
            raise ValueError("country_iso_code is a two-letter code such as us")
        stamp = self._next_stamp()
        raw_path = self.raw_dir / self.provider / _day(stamp) / f"locations_{code}_{stamp}.json.gz"
        envelope, _ = self._free_get(f"{PATH_SERP_LOCATIONS}/{code}", raw_path=raw_path, what="the locations list")
        found: list[Location] = []
        for item in _first_task(envelope).get("result") or []:
            if isinstance(item, dict) and _int_or_none(item.get("location_code")) is not None:
                found.append(_location(item))
        return found

    # -- the $1 samples ---------------------------------------------------------------------------

    def _sample_ledger(self, kind: str) -> SpendLedger:
        if self.sandbox:
            raise BudgetError(
                f"The {kind} sample cannot run on the DataForSEO sandbox: its answers are dummy data, and a sample "
                "opens the gate for real big pulls."
            )
        ledger = self._require_ledger(f"the {kind} sample")
        if ledger.sample_recorded(kind):
            raise BudgetError(f"The sample for {kind} is already recorded. Read it with ledger.sample_findings({kind!r}).")
        return ledger

    @staticmethod
    def _finish_sample(ledger: SpendLedger, kind: str, findings: dict[str, Any], calls: int, *, learned: bool, why: str) -> SampleReport:
        """Record the sample only when it learned something. Otherwise record a failed attempt that keeps the gate closed."""
        if learned:
            ledger.record_sample(kind, findings)
            return SampleReport(kind, findings, calls)
        findings = {**findings, "not_recorded_because": why}
        ledger.record_failed_sample(kind, findings)
        return SampleReport(kind, findings, calls, recorded=False)

    def sample_serp(self, specs: Sequence[PullSpec], *, mode: str = "standard_normal", cap_usd: Decimal = SAMPLE_MAX_USD) -> SampleReport:
        """A SERP sample capped at `cap_usd`: as many of `specs` as fit, then record what came back."""
        kind = SERP_MODE_KINDS[mode]
        ledger = self._sample_ledger(kind)
        chosen: list[PullSpec] = []
        spend = Decimal(0)
        for spec in specs:
            cost = self._serp_estimate(spec, mode, True)
            if spend + cost > cap_usd:
                break
            chosen.append(spec)
            spend += cost
        if not chosen:
            raise BudgetError(f"Not one SERP pull fits under the {cap_usd} USD sample cap.")
        pulls = self.serp_fetch(chosen, mode=mode, max_resubmits=0, sample=True)
        organic_counts = [len(pull.hits) for pull in pulls if pull.ok]
        findings = {
            "pulls": len(pulls),
            "ok": sum(pull.ok for pull in pulls),
            "failed": [{"pull_id": pull.pull_id, "status_code": pull.status_code} for pull in pulls if not pull.ok],
            "partial": sum(pull.partial for pull in pulls),
            "spell_corrected": sum(pull.spell_corrected for pull in pulls),
            "repaired_pulls": sum(pull.repaired for pull in pulls),
            "fewest_organic": min(organic_counts) if organic_counts else None,
            "most_organic": max(organic_counts) if organic_counts else None,
            "spent_usd": str(ledger.spent_for_kind(kind)),
        }
        return self._finish_sample(ledger, kind, findings, len(pulls), learned=any(pull.ok and pull.hits for pull in pulls),
                                   why="no SERP pull came back ok with organic results")

    def sample_historical_serps(
        self,
        keywords: Sequence[str],
        location_code: int,
        language_code: str,
        *,
        months_back: int = SAMPLE_HISTORY_MONTHS,
        cap_usd: Decimal = SAMPLE_MAX_USD,
    ) -> SampleReport:
        """How deep and how far back past result pages go (plan source 2), for at most `cap_usd`.

        Asks for `months_back` months. When DataForSEO refuses the start date as an
        invalid field, it records that and asks again with the provider's default
        window, so the sample still answers how far back the data goes.
        """
        ledger = self._sample_ledger(KIND_HISTORICAL_SERPS)
        today = self._now().astimezone(timezone.utc).date()
        date_from = add_months(today, -months_back)
        per_call = historical_serps_estimate(months_back + 1)
        planned = Decimal(0)
        results: list[HistoricalSerpsResult] = []
        refusals: list[dict[str, Any]] = []
        for keyword in _unique(keywords):
            if planned + per_call > cap_usd:
                break
            planned += per_call
            result = self.historical_serps(keyword, location_code, language_code, date_from=date_from, sample=True)
            if not result.ok and result.call.status_code == STATUS_INVALID_FIELD and planned + per_call <= cap_usd:
                refusals.append({"keyword": keyword, "date_from": date_from.isoformat(), "status_code": result.call.status_code,
                                 "status_message": result.call.status_message})
                planned += per_call
                result = self.historical_serps(keyword, location_code, language_code, sample=True)
            results.append(result)
        if not results:
            raise BudgetError(f"Not one Historical SERPs call fits under the {cap_usd} USD sample cap.")
        dates = [snap.snapshot_date for result in results for snap in result.snapshots if snap.snapshot_date]
        depths = [snap.max_rank_group for result in results for snap in result.snapshots if snap.max_rank_group is not None]
        months = sorted({(day.year, day.month) for day in dates})
        findings = {
            "keywords_asked": [result.keyword for result in results],
            "keywords_with_snapshots": sum(1 for result in results if result.snapshots),
            "date_from_requested": date_from.isoformat(),
            "refused_start_dates": refusals,
            "failed": [{"keyword": r.keyword, "status_code": r.call.status_code} for r in results if not r.ok],
            "snapshots": len(dates),
            "earliest_snapshot": min(dates).isoformat() if dates else None,
            "latest_snapshot": max(dates).isoformat() if dates else None,
            "distinct_months": len(months),
            "deepest_organic_rank": max(depths) if depths else None,
            "shallowest_organic_rank": min(depths) if depths else None,
            "spent_usd": str(ledger.spent_for_kind(KIND_HISTORICAL_SERPS)),
        }
        return self._finish_sample(ledger, KIND_HISTORICAL_SERPS, findings, len(results) + len(refusals), learned=bool(dates),
                                   why="no call returned a dated snapshot, so how far back the data goes is still unknown")

    def sample_historical_traffic(
        self, small_sites: Sequence[str], *, cap_usd: Decimal = SAMPLE_MAX_USD, **options: Any
    ) -> SampleReport:
        """Do very small new sites show up in the traffic history at all (plan source 4)? At most `cap_usd`."""
        ledger = self._sample_ledger(KIND_HISTORICAL_TRAFFIC)
        sites = _unique(small_sites)
        fit = 0
        while fit < min(len(sites), LABS_MAX_TARGETS_PER_TASK) and historical_traffic_cost(fit + 1) <= cap_usd:
            fit += 1
        if fit == 0:
            raise BudgetError(f"Not one site fits under the {cap_usd} USD sample cap.")
        result = self.historical_bulk_traffic_estimation(sites[:fit], sample=True, **options)
        with_data = result.targets_with_data()
        findings = {
            "sites_asked": len(result.targets_asked),
            "sites_returned": len(result.targets_returned),
            "sites_with_data": len(with_data),
            "share_with_data": round(len(with_data) / len(result.targets_asked), 6) if result.ok else None,
            "status_code": result.call.status_code,
            "months_seen": len({(row.year, row.month) for row in result.rows}),
            "spent_usd": str(ledger.spent_for_kind(KIND_HISTORICAL_TRAFFIC)),
        }
        return self._finish_sample(ledger, KIND_HISTORICAL_TRAFFIC, findings, 1, learned=result.ok,
                                   why=f"the call failed (status {result.call.status_code}), so whether small sites have data is unknown")

    def sample_bulk_traffic(self, small_sites: Sequence[str], *, cap_usd: Decimal = SAMPLE_MAX_USD, **options: Any) -> SampleReport:
        """How many of the given small sites have a current traffic estimate. At most `cap_usd`."""
        ledger = self._sample_ledger(KIND_BULK_TRAFFIC)
        sites = _unique(small_sites)
        fit = 0
        while fit < min(len(sites), LABS_MAX_TARGETS_PER_TASK) and bulk_traffic_cost(fit + 1) <= cap_usd:
            fit += 1
        if fit == 0:
            raise BudgetError(f"Not one site fits under the {cap_usd} USD sample cap.")
        result = self.bulk_traffic_estimation(sites[:fit], sample=True, **options)
        with_data = result.targets_with_data()
        findings = {
            "sites_asked": len(result.targets_asked),
            "sites_returned": len(result.targets_returned),
            "sites_with_data": len(with_data),
            "share_with_data": round(len(with_data) / len(result.targets_asked), 6) if result.ok else None,
            "status_code": result.call.status_code,
            "spent_usd": str(ledger.spent_for_kind(KIND_BULK_TRAFFIC)),
        }
        return self._finish_sample(ledger, KIND_BULK_TRAFFIC, findings, 1, learned=result.ok,
                                   why=f"the call failed (status {result.call.status_code}), so whether small sites have data is unknown")

    def sample_ranked_keywords(
        self, targets: Sequence[str], *, limit: int = 100, cap_usd: Decimal = SAMPLE_MAX_USD, **options: Any
    ) -> SampleReport:
        """How many of the given sites have ranked keyword rows. At most `cap_usd`."""
        ledger = self._sample_ledger(KIND_RANKED_KEYWORDS)
        per_call = ranked_keywords_cost(1, limit)
        results: list[RankedKeywordsResult] = []
        planned = Decimal(0)
        for target in _unique(targets):
            if planned + per_call > cap_usd:
                break
            planned += per_call
            results.append(self.ranked_keywords(target, limit=limit, sample=True, **options))
        if not results:
            raise BudgetError(f"Not one ranked keywords call fits under the {cap_usd} USD sample cap.")
        ranks = [row.rank_group for result in results for row in result.rows if row.rank_group is not None]
        findings = {
            "sites_asked": len(results),
            "sites_with_rows": sum(1 for result in results if result.rows),
            "rows": sum(len(result.rows) for result in results),
            "deepest_rank": max(ranks) if ranks else None,
            "failed": [{"target": r.target, "status_code": r.call.status_code} for r in results if not r.ok],
            "spent_usd": str(ledger.spent_for_kind(KIND_RANKED_KEYWORDS)),
        }
        return self._finish_sample(ledger, KIND_RANKED_KEYWORDS, findings, len(results), learned=any(r.ok for r in results),
                                   why="every ranked keywords call failed")


# ----------------------------------------------------------------------------------------------
# The SerpSource.
# ----------------------------------------------------------------------------------------------


class DataForSeoSerpSource:
    """types.SerpSource over DataForSEO. Standard queue at normal priority unless another mode is named."""

    def __init__(
        self,
        client: DataForSeoClient,
        *,
        mode: str = "standard_normal",
        resolve_poisoned_results: bool = True,
        max_resubmits: int = DEFAULT_MAX_RESUBMITS,
    ) -> None:
        if mode not in SERP_MODE_KINDS:
            raise ValueError(f"unknown SERP mode {mode!r}")
        self.client = client
        self.mode = mode
        self.resolve_poisoned_results = resolve_poisoned_results
        self.max_resubmits = max_resubmits
        provider = getattr(client, "provider", PROVIDER)
        self.name = provider if mode == "standard_normal" else f"{provider}-{mode}"

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]:
        return self.client.serp_fetch(
            specs, mode=self.mode, resolve_poisoned_results=self.resolve_poisoned_results, max_resubmits=self.max_resubmits
        )

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH) -> float:
        """USD for `count` pulls at `depth`, before any resubmission. Exact to the micro-dollar.

        Without the keywords the x5 operator charge cannot be known here; the client's own reservation adds it.
        The sandbox is free.
        """
        if getattr(self.client, "sandbox", False):
            return 0.0
        return float(serp_cost(depth, mode=self.mode, resolve_poisoned_results=self.resolve_poisoned_results, pulls=count))


# ----------------------------------------------------------------------------------------------
# Parsing helpers used above.
# ----------------------------------------------------------------------------------------------


def _pull_from_task(
    *,
    pull_id: str,
    spec: PullSpec,
    endpoint: str,
    requested_at: str,
    completed_at: str,
    task: dict[str, Any],
    code: int | None,
    message: str | None,
    task_id: str | None,
    cost: float | None,
    raw: RawFile,
    provider: str = PROVIDER,
) -> Pull:
    common = dict(
        pull_id=pull_id,
        provider=provider,
        endpoint=endpoint,
        spec=spec,
        requested_at=requested_at,
        completed_at=completed_at,
        status_code=code,
        task_id=task_id,
        cost=cost,
        raw_path=raw.path,
        raw_sha256=raw.sha256,
    )
    if code not in OK_SERP_CODES:
        return Pull(ok=False, status_message=message or "The task failed with no message.", **common)
    parsed = parse_serp_task(task)
    if code == STATUS_OK and not parsed.has_result:
        return Pull(ok=False, status_message="The task said ok but held no result.", **common)
    return Pull(
        ok=True,
        status_message=message,
        provider_datetime=parsed.provider_datetime,
        check_url=parsed.check_url,
        partial=code == STATUS_PARTIAL,
        spell_corrected=parsed.spell_corrected,
        repaired=any(hit.repaired for hit in parsed.hits),
        hits=parsed.hits,
        **common,
    )


def _first_task(envelope: dict[str, Any]) -> dict[str, Any]:
    tasks = [task for task in envelope.get("tasks") or [] if isinstance(task, dict)]
    return tasks[0] if tasks else {}


def _result_items(task: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for result in task.get("result") or []:
        if isinstance(result, dict):
            items.extend(item for item in result.get("items") or [] if isinstance(item, dict))
    return items


def _put_location(task: dict[str, Any], location_code: int | None, language_code: str | None) -> None:
    if location_code is not None:
        task["location_code"] = int(location_code)
    if language_code is not None:
        task["language_code"] = language_code


def _historical_snapshot(snap: dict[str, Any]) -> HistoricalSnapshot:
    spell_type, _ = _spell(snap)
    organic: list[HistoricalOrganic] = []
    for item in snap.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "organic":
            continue
        rank = _int_or_none(item.get("rank_group"))
        if rank is None or rank < 1:
            continue
        changes = item.get("rank_changes") or {}
        is_new = changes.get("is_new") if isinstance(changes.get("is_new"), bool) else None
        organic.append(
            HistoricalOrganic(
                rank_group=rank,
                rank_absolute=_int_or_none(item.get("rank_absolute")),
                domain=item.get("domain"),
                site=_site(item.get("domain"), item.get("url")),
                url=item.get("url"),
                title=item.get("title"),
                is_new=is_new,
                previous_rank_absolute=_int_or_none(changes.get("previous_rank_absolute")),
            )
        )
    organic.sort(key=lambda hit: (hit.rank_group, hit.url or ""))
    return HistoricalSnapshot(
        datetime=_utc_text(snap.get("datetime")),
        check_url=snap.get("check_url"),
        items_count=_int_or_none(snap.get("items_count")),
        spell_type=spell_type,
        organic=tuple(organic),
    )


def _ranked_row(target: str, item: dict[str, Any]) -> RankedKeywordRow:
    keyword_data = item.get("keyword_data") or {}
    info = keyword_data.get("keyword_info") or {}
    element = item.get("ranked_serp_element") or {}
    serp_item = element.get("serp_item") or {}
    domain, url = serp_item.get("domain"), serp_item.get("url")
    return RankedKeywordRow(
        target=target,
        keyword=keyword_data.get("keyword"),
        search_volume=_int_or_none(info.get("search_volume")),
        item_type=serp_item.get("type"),
        rank_group=_int_or_none(serp_item.get("rank_group")),
        rank_absolute=_int_or_none(serp_item.get("rank_absolute")),
        url=url,
        domain=domain,
        site=_site(domain, url) if (domain or url) else None,
        check_url=element.get("check_url"),
        is_lost=element.get("is_lost") if isinstance(element.get("is_lost"), bool) else None,
        last_updated_time=_utc_text(element.get("last_updated_time")),
    )


def _location(item: dict[str, Any]) -> Location:
    return Location(
        location_code=int(item["location_code"]),
        location_name=item.get("location_name"),
        location_code_parent=_int_or_none(item.get("location_code_parent")),
        country_iso_code=item.get("country_iso_code"),
        location_type=item.get("location_type"),
    )


def load_locations_csv(path: str | Path) -> list[Location]:
    """Locations from DataForSEO's public CSV (columns location_code, location_name, location_code_parent,
    country_iso_code, location_type), for example locations_serp_google_2026_11_02.csv."""
    import csv

    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [_location(row) for row in csv.DictReader(handle) if _int_or_none(row.get("location_code")) is not None]


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    if not 1 <= size <= LABS_MAX_TARGETS_PER_TASK:
        raise ValueError(f"chunk_size must be 1 to {LABS_MAX_TARGETS_PER_TASK}")
    ordered = _unique(values)
    return [ordered[start : start + size] for start in range(0, len(ordered), size)]


def _day(stamp: str) -> str:
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
