"""The budget guard: prices, cost estimates, the spend ledger and the $1 sample gate.

Nothing in this module is REAL Google data and nothing is PREDICTED. The prices
are configuration copied from the providers' published pages on the date in
PRICES_READ_ON. An estimate is arithmetic on those prices. The ledger records
what a person approved and what each provider said it charged (its own `cost`
field), so the amounts it reports as settled are REAL charges as the provider
stated them. A charge the provider did not state is counted at our estimate and
marked as such.

Rules this module enforces (docs/plan/IMPLEMENTATION_SPEC.md section 5):

- The default approved amount is 0. A fresh ledger refuses every paid call.
- Every paid call reserves its estimate first. The reservation is refused when
  the estimate is more than what remains of the approved amount.
- Each paid kind gets a $1 sample first. Until a sample is recorded for a kind,
  that kind may not spend more than SAMPLE_MAX_USD in total. So a big pull
  (anything above SAMPLE_MAX_USD) is refused until its sample is recorded, and
  a big pull split into many small calls is refused too.
- The ledger is an append-only JSONL file. Each line is hash chained with
  chain.row_hash. A broken chain refuses every new reservation.
- Money is kept as integer micro-dollars (1 USD = 1,000,000). Totals never
  drift the way floats do. The public API takes and returns Decimal.

One process writes a ledger file at a time. Two processes appending to the same
file at once are not supported.
"""

from __future__ import annotations

import json
import math
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator

from aeo.errors import AeoError

from .asof import utc_iso
from .chain import GENESIS, canonical_json, row_hash, verify_chain

# ----------------------------------------------------------------------------------------------
# Prices. One table, with the date they were read and where.
# ----------------------------------------------------------------------------------------------

PRICES_READ_ON = date(2026, 11, 22)

SOURCE_SERP_PRICING = "https://dataforseo.com/pricing/google-serp/google-organic-serp-api"
SOURCE_SERP_DEPTH_FAQ = "https://dataforseo.com/help-center/serp-api-pricing-depth-update-faq"
SOURCE_LABS_PRICING = "https://dataforseo.com/pricing/dataforseo-labs/dataforseo-google-api"
SOURCE_SERPER_PRICING = "https://serper.dev"

MICROS_PER_USD = 1_000_000
SAMPLE_MAX_USD = Decimal("1.00")
SERP_PAGE_SIZE = 10
LABS_MAX_TARGETS_PER_TASK = 1000


@dataclass(frozen=True)
class Price:
    """One unit price in USD, with what it is charged per and the page it was read from."""

    key: str
    usd: Decimal
    per: str
    source: str


PRICES: dict[str, Price] = {
    price.key: price
    for price in (
        Price("serp_standard_normal_page", Decimal("0.0006"), "SERP page of 10 results, Standard queue, normal priority", SOURCE_SERP_PRICING),
        Price("serp_standard_high_page", Decimal("0.0012"), "SERP page of 10 results, Standard queue, high priority", SOURCE_SERP_PRICING),
        Price("serp_live_page", Decimal("0.002"), "SERP page of 10 results, Live endpoint", SOURCE_SERP_PRICING),
        Price("labs_historical_serps_snapshot", Decimal("0.00012"), "historical SERP snapshot returned", SOURCE_LABS_PRICING),
        Price("labs_historical_traffic_task", Decimal("0.12"), "Historical Bulk Traffic Estimation task (up to 1,000 domains)", SOURCE_LABS_PRICING),
        Price("labs_historical_traffic_target", Decimal("0.0012"), "domain in a Historical Bulk Traffic Estimation request", SOURCE_LABS_PRICING),
        Price("labs_default_task", Decimal("0.012"), "Labs task at the default rate (ranked keywords, bulk traffic estimation)", SOURCE_LABS_PRICING),
        Price("labs_default_item", Decimal("0.00012"), "item at the Labs default rate (a ranked keyword row, a domain)", SOURCE_LABS_PRICING),
        Price("serper_credit", Decimal("0.001"), "Serper credit on the Starter plan ($50 for 50,000 credits)", SOURCE_SERPER_PRICING),
    )
}

SERP_MODES: dict[str, str] = {
    "standard_normal": "serp_standard_normal_page",
    "standard_high": "serp_standard_high_page",
    "live": "serp_live_page",
}

# Budgeting assumption, not a price: how many Historical SERPs snapshots one month can return.
# The docs example returned 5 snapshots for a range that touched 3 calendar months, so billing is
# per snapshot and can exceed one per month. The $1 sample measures the real count. Settlement
# always uses the provider's own cost field.
HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE = 3

# Ledger kinds. A kind is the unit of the $1 sample gate.
KIND_SERP_STANDARD_NORMAL = "dataforseo.serp.standard_normal"
KIND_SERP_STANDARD_HIGH = "dataforseo.serp.standard_high"
KIND_SERP_LIVE = "dataforseo.serp.live"
KIND_HISTORICAL_SERPS = "dataforseo.labs.historical_serps"
KIND_HISTORICAL_TRAFFIC = "dataforseo.labs.historical_bulk_traffic_estimation"
KIND_BULK_TRAFFIC = "dataforseo.labs.bulk_traffic_estimation"
KIND_RANKED_KEYWORDS = "dataforseo.labs.ranked_keywords"
KIND_SERPER_SEARCH = "serper.search"

SERP_MODE_KINDS: dict[str, str] = {
    "standard_normal": KIND_SERP_STANDARD_NORMAL,
    "standard_high": KIND_SERP_STANDARD_HIGH,
    "live": KIND_SERP_LIVE,
}


class BudgetError(AeoError):
    """A paid call was refused by the budget guard, or the ledger is not usable."""


class ProviderError(AeoError):
    """A provider refused a whole request (bad login, no money, account paused, bad request).

    It stops the batch. `status_code` is the provider's own code when it gave
    one, else the HTTP status. `posted_task_ids` lists queued tasks that were
    already paid for, so their results can still be collected later.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        http_status: int | None = None,
        posted_task_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.http_status = http_status
        self.posted_task_ids = posted_task_ids


# ----------------------------------------------------------------------------------------------
# Money helpers.
# ----------------------------------------------------------------------------------------------


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """A USD amount as Decimal. A float is read through its shortest text form, so 0.1 is 0.1."""
    if isinstance(value, bool):
        raise ValueError("a money amount cannot be a bool")
    try:
        number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, TypeError) as error:
        raise ValueError(f"{value!r} is not a money amount") from error
    if not number.is_finite():
        raise ValueError(f"{value!r} is not a finite money amount")
    return number


def to_micros(value: Decimal | int | float | str) -> int:
    """USD to integer micro-dollars, rounded half up to the micro-dollar."""
    number = to_decimal(value) * MICROS_PER_USD
    return int(number.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_micros(micros: int) -> Decimal:
    """Integer micro-dollars to USD with six decimals."""
    return (Decimal(micros) / MICROS_PER_USD).quantize(Decimal("0.000001"))


def usd_text(value: Decimal | int) -> str:
    """A USD amount as text with six decimals. An int is read as micro-dollars."""
    amount = from_micros(value) if isinstance(value, int) else to_decimal(value).quantize(Decimal("0.000001"))
    return f"{amount} USD"


def price(key: str) -> Decimal:
    return PRICES[key].usd


# ----------------------------------------------------------------------------------------------
# Cost estimates. All return Decimal USD.
# ----------------------------------------------------------------------------------------------


def serp_pages(depth: int) -> int:
    """SERP pages billed for a depth: depth rounded up to the next ten, divided by ten."""
    if depth < 1:
        raise ValueError(f"depth {depth} is below 1")
    return math.ceil(depth / SERP_PAGE_SIZE)


def serp_cost(
    depth: int,
    *,
    mode: str = "standard_normal",
    resolve_poisoned_results: bool = True,
    pulls: int = 1,
) -> Decimal:
    """Price of `pulls` SERP tasks at `depth`. resolve_poisoned_results adds one page price per task."""
    if mode not in SERP_MODES:
        raise ValueError(f"unknown SERP mode {mode!r}. Choose one of {sorted(SERP_MODES)}.")
    if pulls < 0:
        raise ValueError("pulls cannot be negative")
    base = price(SERP_MODES[mode])
    pages = serp_pages(depth) + (1 if resolve_poisoned_results else 0)
    return base * pages * pulls


def historical_serps_cost(searches: int, snapshots_per_search: int) -> Decimal:
    """Price of Historical SERPs: one snapshot price per snapshot returned."""
    _check_counts(searches, snapshots_per_search)
    return price("labs_historical_serps_snapshot") * searches * snapshots_per_search


def historical_serps_estimate(months: int, searches: int = 1) -> Decimal:
    """Upper estimate for Historical SERPs over `months` months, using the snapshots-per-month assumption."""
    _check_counts(months, searches)
    return historical_serps_cost(searches, months * HISTORICAL_SERPS_SNAPSHOTS_PER_MONTH_ESTIMATE)


def historical_traffic_cost(sites: int, *, per_task: int = LABS_MAX_TARGETS_PER_TASK) -> Decimal:
    """Price of Historical Bulk Traffic Estimation: a task price per task of up to 1,000 sites, plus a price per site."""
    _check_counts(sites, per_task)
    if per_task < 1 or per_task > LABS_MAX_TARGETS_PER_TASK:
        raise ValueError(f"per_task must be 1 to {LABS_MAX_TARGETS_PER_TASK}")
    tasks = math.ceil(sites / per_task)
    return price("labs_historical_traffic_task") * tasks + price("labs_historical_traffic_target") * sites


def bulk_traffic_cost(sites: int, *, per_task: int = LABS_MAX_TARGETS_PER_TASK) -> Decimal:
    """Price of Bulk Traffic Estimation (current month) at the Labs default rate."""
    _check_counts(sites, per_task)
    if per_task < 1 or per_task > LABS_MAX_TARGETS_PER_TASK:
        raise ValueError(f"per_task must be 1 to {LABS_MAX_TARGETS_PER_TASK}")
    tasks = math.ceil(sites / per_task)
    return price("labs_default_task") * tasks + price("labs_default_item") * sites


def ranked_keywords_cost(requests: int, rows_per_request: int) -> Decimal:
    """Price of ranked keywords at the standard Labs rate: a task price per request plus a price per row."""
    _check_counts(requests, rows_per_request)
    return price("labs_default_task") * requests + price("labs_default_item") * requests * rows_per_request


def serper_cost(credits: int) -> Decimal:
    """Serper credits to USD on the Starter plan."""
    _check_counts(credits)
    return price("serper_credit") * credits


def _check_counts(*values: int) -> None:
    for value in values:
        if int(value) != value or value < 0:
            raise ValueError(f"count {value!r} must be a whole number of zero or more")


# ----------------------------------------------------------------------------------------------
# The spend ledger.
# ----------------------------------------------------------------------------------------------

EVENTS = ("approve", "reserve", "settle", "release", "sample")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    item: str
    kind: str
    estimate_micros: int
    sample: bool


@dataclass
class LedgerState:
    """Totals rebuilt from the ledger file. Amounts are micro-dollars."""

    ok: bool = True
    head: str = GENESIS
    rows: int = 0
    approved: int = 0
    settled: int = 0
    open: dict[str, Reservation] = field(default_factory=dict)
    closed: set[str] = field(default_factory=set)
    kind_spent: dict[str, int] = field(default_factory=dict)
    samples: dict[str, dict[str, Any]] = field(default_factory=dict)
    problem: str | None = None

    @property
    def open_total(self) -> int:
        return sum(reservation.estimate_micros for reservation in self.open.values())

    @property
    def spent(self) -> int:
        return self.settled + self.open_total

    @property
    def remaining(self) -> int:
        return self.approved - self.spent


class SpendLedger:
    """Append-only, hash-chained record of approvals, reservations, settlements and samples."""

    def __init__(self, path: str | Path, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.path = Path(path)
        self._now = now
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"SpendLedger({str(self.path)!r})"

    # -- reading ---------------------------------------------------------------------------

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def state(self) -> LedgerState:
        """Rebuild every total from the file. A broken chain or a bad line makes ok False."""
        state = LedgerState()
        parsed: list[tuple[str, str, dict[str, Any]]] = []
        for number, line in enumerate(self._lines(), start=1):
            try:
                record = json.loads(line)
                parsed.append((str(record["prev_hash"]), str(record["row_hash"]), dict(record["row"])))
            except (ValueError, KeyError, TypeError):
                state.ok = False
                state.problem = f"line {number} of {self.path} is not a ledger row"
                return state
        ok, head, count = verify_chain(parsed)
        state.head, state.rows = head, count
        if not ok:
            state.ok = False
            state.problem = f"the hash chain of {self.path} breaks at row {count + 1}"
            return state
        for index, (_, _, row) in enumerate(parsed, start=1):
            problem = _apply(state, row, index)
            if problem:
                state.ok = False
                state.problem = problem
                return state
        return state

    def verify(self) -> tuple[bool, str, int]:
        """(ok, head hash, rows checked) for the chain, as chain.verify_chain reports it."""
        state = self.state()
        return state.ok, state.head, state.rows

    @property
    def approved_total(self) -> Decimal:
        return from_micros(self._usable().approved)

    @property
    def spent_total(self) -> Decimal:
        """Settled charges plus every open reservation at its estimate."""
        return from_micros(self._usable().spent)

    @property
    def remaining(self) -> Decimal:
        return from_micros(self._usable().remaining)

    def spent_for_kind(self, kind: str) -> Decimal:
        return from_micros(self._usable().kind_spent.get(kind, 0))

    def sample_recorded(self, kind: str) -> bool:
        return kind in self._usable().samples

    def sample_findings(self, kind: str) -> dict[str, Any] | None:
        return self._usable().samples.get(kind)

    def open_reservations(self) -> list[Reservation]:
        return sorted(self._usable().open.values(), key=lambda item: item.reservation_id)

    def _usable(self) -> LedgerState:
        state = self.state()
        if not state.ok:
            raise BudgetError(f"The spend ledger cannot be trusted: {state.problem}. Nothing can be spent until it is repaired.")
        return state

    # -- writing ---------------------------------------------------------------------------

    def _append(self, state: LedgerState, row: dict[str, Any]) -> dict[str, Any]:
        row = {"seq": state.rows + 1, "at": utc_iso(self._now()), **row}
        this = row_hash(state.head, row)
        record = {"prev_hash": state.head, "row_hash": this, "row": row}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(record) + "\n")
            handle.flush()
        return row

    def approve(self, amount_usd: Decimal | int | str, by: str, note: str) -> Decimal:
        """Record that a person approved spending `amount_usd` more. Returns the new approved total."""
        if isinstance(amount_usd, float):
            raise ValueError("pass the approved amount as Decimal or text, not a float")
        micros = to_micros(amount_usd)
        if micros <= 0:
            raise ValueError("an approval must be more than 0 USD")
        if not str(by or "").strip():
            raise ValueError("an approval needs the name of the person who gave it")
        with self._lock:
            state = self._usable()
            self._append(state, {"event": "approve", "amount_micros": micros, "by": str(by).strip(), "note": str(note or "")})
            return from_micros(state.approved + micros)

    def reserve(self, item: str, kind: str, estimate_usd: Decimal | int | float | str, *, sample: bool = False) -> str:
        """Hold `estimate_usd` for one paid call. Returns the reservation id, or raises BudgetError."""
        micros = to_micros(estimate_usd)
        if micros <= 0:
            raise ValueError("a paid call must have an estimate above 0 USD. Free calls do not go through the ledger.")
        if not str(kind or "").strip():
            raise ValueError("a reservation needs a kind")
        with self._lock:
            state = self._usable()
            if micros > state.remaining:
                raise BudgetError(
                    f"Refused: {item} needs an estimated {usd_text(micros)}, but only {usd_text(state.remaining)} "
                    f"of the {usd_text(state.approved)} approved is left in {self.path}. "
                    f"To approve more, run in Python: SpendLedger(\"{self.path}\").approve(Decimal(\"<amount in USD>\"), "
                    f"by=\"<your name>\", note=\"<what it is for>\")"
                )
            kind_after = state.kind_spent.get(kind, 0) + micros
            if kind not in state.samples and kind_after > to_micros(SAMPLE_MAX_USD):
                raise BudgetError(
                    f"Refused: {kind} has no recorded $1 sample yet, and {item} would bring its spend to "
                    f"{usd_text(kind_after)}, above the {usd_text(SAMPLE_MAX_USD)} sample cap. Run the sample helper "
                    f"for this kind first, or record what a sample found with SpendLedger(\"{self.path}\")"
                    f".record_sample(\"{kind}\", findings)."
                )
            row = self._append(
                state,
                {"event": "reserve", "item": str(item), "kind": str(kind), "estimate_micros": micros, "sample": bool(sample)},
            )
            return _reservation_id(row["seq"])

    def settle(self, reservation_id: str, actual_usd: Decimal | int | float | str | None) -> Decimal:
        """Close a reservation with the provider's own cost. None means the provider gave no cost: the estimate is charged."""
        with self._lock:
            state = self._usable()
            reservation = self._open_reservation(state, reservation_id)
            if actual_usd is None:
                micros, known = reservation.estimate_micros, False
            else:
                micros, known = to_micros(actual_usd), True
                if micros < 0:
                    raise ValueError("a settled cost cannot be negative")
            self._append(
                state,
                {"event": "settle", "reservation_id": reservation_id, "actual_micros": micros, "actual_known": known},
            )
            return from_micros(micros)

    def release(self, reservation_id: str) -> None:
        """Close a reservation for a call that was never sent. Nothing is charged."""
        with self._lock:
            state = self._usable()
            self._open_reservation(state, reservation_id)
            self._append(state, {"event": "release", "reservation_id": reservation_id})

    def record_sample(self, kind: str, findings: dict[str, Any]) -> None:
        """Mark the $1 sample of `kind` as done, with what it found. Big pulls of that kind are then allowed."""
        if not str(kind or "").strip():
            raise ValueError("a sample needs a kind")
        canonical_json(findings)  # refuses NaN and values JSON cannot hold
        with self._lock:
            state = self._usable()
            self._append(state, {"event": "sample", "kind": str(kind), "findings": findings})

    def _open_reservation(self, state: LedgerState, reservation_id: str) -> Reservation:
        if reservation_id in state.closed:
            raise BudgetError(f"reservation {reservation_id} is already closed")
        if reservation_id not in state.open:
            raise BudgetError(f"reservation {reservation_id} does not exist in {self.path}")
        return state.open[reservation_id]

    # -- the helper clients use -------------------------------------------------------------

    @contextmanager
    def metered(self, item: str, kind: str, estimate_usd: Decimal | int | float | str, *, sample: bool = False) -> Iterator["Meter"]:
        """Reserve, then let the client send and settle. See Meter for what happens on failure."""
        reservation_id = self.reserve(item, kind, estimate_usd, sample=sample)
        meter = Meter(self, reservation_id)
        try:
            yield meter
        except BaseException:
            meter.close_unsettled()
            raise
        else:
            meter.close_unsettled()


@dataclass
class Meter:
    """One reserved paid call.

    The client calls `mark_sent()` just before it sends, `mark_unsent()` if the
    connection failed before anything reached the provider, and `settle(cost)`
    with the provider's own cost. If the block ends without a settlement: a call
    that was never sent is released, and a call that may have been sent is
    settled at the estimate, because money may have been spent.
    """

    ledger: SpendLedger
    reservation_id: str
    sent: bool = False
    closed: bool = False
    settled_usd: Decimal | None = None

    def mark_sent(self) -> None:
        self.sent = True

    def mark_unsent(self) -> None:
        self.sent = False

    def settle(self, actual_usd: Decimal | int | float | str | None) -> Decimal:
        if self.closed:
            raise BudgetError(f"reservation {self.reservation_id} is already closed")
        self.settled_usd = self.ledger.settle(self.reservation_id, actual_usd)
        self.closed = True
        return self.settled_usd

    def close_unsettled(self) -> None:
        if self.closed:
            return
        if self.sent:
            self.settled_usd = self.ledger.settle(self.reservation_id, None)
        else:
            self.ledger.release(self.reservation_id)
        self.closed = True


def _reservation_id(seq: int) -> str:
    return f"res-{seq:06d}"


def _apply(state: LedgerState, row: dict[str, Any], expected_seq: int) -> str | None:
    """Fold one verified row into the totals. Returns a problem text when the row makes no sense."""
    event = row.get("event")
    seq = row.get("seq")
    if seq != expected_seq:
        return f"row {expected_seq} carries sequence number {seq!r}"
    try:
        if event == "approve":
            state.approved += int(row["amount_micros"])
        elif event == "reserve":
            reservation_id = _reservation_id(int(seq))
            reservation = Reservation(
                reservation_id, str(row["item"]), str(row["kind"]), int(row["estimate_micros"]), bool(row.get("sample"))
            )
            state.open[reservation_id] = reservation
            state.kind_spent[reservation.kind] = state.kind_spent.get(reservation.kind, 0) + reservation.estimate_micros
        elif event == "settle":
            reservation = state.open.pop(str(row["reservation_id"]))
            actual = int(row["actual_micros"])
            state.closed.add(reservation.reservation_id)
            state.settled += actual
            state.kind_spent[reservation.kind] += actual - reservation.estimate_micros
        elif event == "release":
            reservation = state.open.pop(str(row["reservation_id"]))
            state.closed.add(reservation.reservation_id)
            state.kind_spent[reservation.kind] -= reservation.estimate_micros
        elif event == "sample":
            state.samples[str(row["kind"])] = dict(row["findings"])
        else:
            return f"row {seq} has an unknown event {event!r}"
    except (KeyError, TypeError, ValueError):
        return f"row {seq} ({event}) refers to something that is not in the ledger"
    return None
