"""Serper: the backup SERP provider for the clean reader.

REAL: every Pull is what Serper returned, parsed from the raw JSON that was
saved to disk first. Nothing is PREDICTED here and nothing is invented. A failed
page makes the whole pull a failed Pull (ok False). It is never an empty result.

What Serper can and cannot do (facts from the API map, read 2026-11-22):

- POST https://google.serper.dev/search with the key in the X-API-KEY header.
- Body: q, gl, hl, location, num, page. Search offers 10 results per page, so a
  pull of depth 50 is pages 1 to ceil(depth/10), one request each.
- `location` is Serper's canonical name of a place, such as
  "Austin,Texas,United States". The caller maps each Google geo target id to
  that name (Serper's public list gives both).
- Serper has no documented device switch, so PullSpec.device is not sent.
- Serper CANNOT run resolve_poisoned_results. It has no poisoned-page repair
  and no flag for it, no check_url, and no rank_absolute. Pulls from Serper are
  never marked repaired, and the reader's leave-one-out filter is the only
  defence against altered pages.
- Each successful response says how many `credits` it used. Credits are
  metered through the same SpendLedger as DataForSEO, at the Starter plan price.
  Credits for page 2 and later are not documented, so the ledger settles with
  the credits each response reports.

Positions: rank counts organic results across pages in the order Serper lists
them, so ranks run 1, 2, 3 ... with no gap when a page holds fewer than 10.
A URL that shows again on a later page keeps its first rank and is not counted
twice.

Raw file: raw_dir/serper/<UTC date>/<pull id>.json.gz holds a JSON array whose
elements are the page responses byte for byte, in page order. A page that did
not answer with JSON is stored as {"http_status": ..., "body_text": ...}.
The sha256 on the Pull is the sha256 of that uncompressed array.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from aeo.crawl.urls import domain_of
from aeo.errors import ProviderConfigError
from aeo.ranksim.site_history import site_key

from .asof import utc_iso
from .budget import (
    KIND_SERPER_SEARCH,
    SAMPLE_MAX_USD,
    BudgetError,
    ProviderError,
    SpendLedger,
    serper_cost,
)
from .chain import canonical_json, sha256_text
from .config import TOP_DEPTH, forecast_paths
from .dataforseo import SampleReport, write_raw
from .types import OrganicHit, Pull, PullSpec

PROVIDER = "serper"
SERPER_SEARCH_URL = "https://google.serper.dev/search"
ENDPOINT = "search"
RESULTS_PER_PAGE = 10
CREDITS_PER_PAGE_ESTIMATE = 1  # every stored repo response with num=10 shows credits 1
DEFAULT_GL = "us"
DEFAULT_HL = "en"
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0
MAX_DEPTH = 100
FATAL_HTTP_STATUSES = frozenset({401, 402, 403})
RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
PAGE_RETRIES = 2
PAGE_BACKOFF_SECONDS = 5.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def serper_pages(depth: int) -> int:
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be 1 to {MAX_DEPTH} for Serper, not {depth}")
    return math.ceil(depth / RESULTS_PER_PAGE)


def parse_serper_pages(pages: Sequence[dict[str, Any]], depth: int) -> tuple[OrganicHit, ...]:
    """Organic hits across pages, ranks continuous from 1, duplicates of an earlier URL skipped, cut at `depth`."""
    hits: list[OrganicHit] = []
    seen: set[str] = set()
    for page in pages:
        organic = [item for item in page.get("organic") or [] if isinstance(item, dict)]
        organic.sort(key=lambda item: item.get("position") if isinstance(item.get("position"), int) else 10**9)
        for item in organic:
            url = str(item.get("link") or "")
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            domain = domain_of(url) or ""
            hits.append(
                OrganicHit(
                    rank=len(hits) + 1,
                    url=url,
                    domain=domain,
                    site=site_key(domain or url),
                    title=item.get("title"),
                    rank_absolute=None,
                    repaired=False,
                )
            )
            if len(hits) >= depth:
                return tuple(hits)
    return tuple(hits)


class SerperSerpSource:
    """types.SerpSource over Serper, paged 10 results at a time. Backup provider only."""

    def __init__(
        self,
        api_key: str,
        *,
        locations: Mapping[int, str],
        client: httpx.Client | None = None,
        ledger: SpendLedger | None = None,
        raw_dir: str | Path | None = None,
        gl: str = DEFAULT_GL,
        hl: str = DEFAULT_HL,
        url: str = SERPER_SEARCH_URL,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utcnow,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise ProviderConfigError("Serper needs an API key.")
        self._key = api_key
        self.locations = dict(locations)
        self._owns = client is None
        self._http = client or httpx.Client(timeout=timeout_seconds)
        self.ledger = ledger
        self.raw_dir = Path(raw_dir) if raw_dir is not None else forecast_paths().raw
        self.gl = gl
        self.hl = hl
        self.url = url
        self._sleep = sleep
        self._now = now
        self._counter = 0
        self.name = PROVIDER

    def __repr__(self) -> str:
        return f"SerperSerpSource(url={self.url!r}, raw_dir={str(self.raw_dir)!r})"

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH) -> float:
        """USD for `count` pulls at `depth`, at one credit per page."""
        return float(serper_cost(count * serper_pages(depth) * CREDITS_PER_PAGE_ESTIMATE))

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]:
        """One Pull per spec, in order. A bad key or no credit raises ProviderError and stops the batch."""
        for spec in specs:
            self._check(spec)
        return [self._pull(spec, index) for index, spec in enumerate(specs)]

    def sample(self, specs: Sequence[PullSpec], *, cap_usd: Decimal = SAMPLE_MAX_USD) -> SampleReport:
        """A Serper sample capped at `cap_usd`, recorded in the ledger."""
        ledger = self._require_ledger("the Serper sample")
        if ledger.sample_recorded(KIND_SERPER_SEARCH):
            raise BudgetError(f"The sample for {KIND_SERPER_SEARCH} is already recorded.")
        chosen: list[PullSpec] = []
        planned = Decimal(0)
        for spec in specs:
            cost = serper_cost(serper_pages(spec.depth) * CREDITS_PER_PAGE_ESTIMATE)
            if planned + cost > cap_usd:
                break
            chosen.append(spec)
            planned += cost
        if not chosen:
            raise BudgetError(f"Not one Serper pull fits under the {cap_usd} USD sample cap.")
        for spec in chosen:
            self._check(spec)
        pulls = [self._pull(spec, index, sample=True) for index, spec in enumerate(chosen)]
        counts = [len(pull.hits) for pull in pulls if pull.ok]
        findings = {
            "pulls": len(pulls),
            "ok": sum(pull.ok for pull in pulls),
            "failed": [{"pull_id": pull.pull_id, "status_code": pull.status_code} for pull in pulls if not pull.ok],
            "fewest_organic": min(counts) if counts else None,
            "most_organic": max(counts) if counts else None,
            "spent_usd": str(ledger.spent_for_kind(KIND_SERPER_SEARCH)),
        }
        ledger.record_sample(KIND_SERPER_SEARCH, findings)
        return SampleReport(KIND_SERPER_SEARCH, findings, len(pulls))

    # -- internals -----------------------------------------------------------------------------

    def _require_ledger(self, item: str) -> SpendLedger:
        if self.ledger is None:
            raise BudgetError(
                f"Refused: {item} is a paid call and this client has no spend ledger. "
                "Pass ledger=SpendLedger(path) and approve an amount on it first."
            )
        return self.ledger

    def _check(self, spec: PullSpec) -> None:
        serper_pages(spec.depth)
        if not spec.search:
            raise ValueError("an empty search was given")
        if spec.location_code not in self.locations:
            raise ValueError(
                f"No Serper location name is known for location_code {spec.location_code}. "
                "Pass locations={code: 'City,State,Country'} from Serper's public locations list."
            )

    def _pull(self, spec: PullSpec, index: int, *, sample: bool = False) -> Pull:
        self._counter += 1
        requested = self._now()
        requested_at = utc_iso(requested)
        stamp = requested.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pull_id = f"srp-{stamp}-{self._counter:04d}-{index:04d}-{sha256_text(canonical_json(spec.__dict__))[:8]}"
        pages = serper_pages(spec.depth)
        estimate = serper_cost(pages * CREDITS_PER_PAGE_ESTIMATE)
        ledger = self._require_ledger(f"Serper search for one pull of {pages} page(s)")
        bodies: list[bytes] = []
        statuses: list[int] = []
        credits_total = 0
        credits_known = True
        with ledger.metered(f"Serper search, {pages} page(s)", KIND_SERPER_SEARCH, estimate, sample=sample) as meter:
            try:
                for page in range(1, pages + 1):
                    try:
                        response = self._send_page(spec, page, meter)
                    except ProviderError as error:
                        if error.status_code is None:
                            credits_known = False  # a broken connection may still have been charged
                        raise
                    statuses.append(response.status_code)
                    bodies.append(_storable(response))
                    if response.status_code != 200:
                        break
                    payload = _json(response)
                    credits = payload.get("credits") if isinstance(payload, dict) else None
                    if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0:
                        credits_total += credits
                    else:
                        credits_known = False
            finally:
                raw = None
                if bodies:
                    content = b"[" + b",".join(bodies) + b"]"
                    raw = write_raw(self.raw_dir / PROVIDER / requested_at[:10] / f"{pull_id}.json.gz", content)
                if meter.sent and not meter.closed:
                    meter.settle(serper_cost(credits_total) if credits_known else None)
        completed_at = utc_iso(self._now())
        common: dict[str, Any] = dict(
            pull_id=pull_id,
            provider=PROVIDER,
            endpoint=ENDPOINT,
            spec=spec,
            requested_at=requested_at,
            completed_at=completed_at,
            cost=float(serper_cost(credits_total)) if credits_known else None,
            raw_path=raw.path if raw else None,
            raw_sha256=raw.sha256 if raw else None,
        )
        failed = [status for status in statuses if status != 200]
        if failed:
            return Pull(ok=False, status_code=failed[0], status_message=f"Serper page {len(statuses)} answered HTTP {failed[0]}.", **common)
        parsed: list[dict[str, Any]] = []
        for number, body in enumerate(bodies, start=1):
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            if not isinstance(payload, dict) or "http_status" in payload or not isinstance(payload.get("organic"), list):
                return Pull(
                    ok=False,
                    status_code=200,
                    status_message=f"Serper page {number} held no organic result list, so it is not read as an empty page.",
                    **common,
                )
            parsed.append(payload)
        return Pull(ok=True, status_code=200, hits=parse_serper_pages(parsed, spec.depth), **common)

    def _send_page(self, spec: PullSpec, page: int, meter: Any) -> httpx.Response:
        body = {
            "q": spec.search,
            "gl": self.gl,
            "hl": self.hl,
            "location": self.locations[spec.location_code],
            "num": RESULTS_PER_PAGE,
            "page": page,
        }
        headers = {"X-API-KEY": self._key, "Content-Type": "application/json"}
        for attempt in range(PAGE_RETRIES + 1):
            already_sent = meter.sent
            meter.mark_sent()
            try:
                response = self._http.post(self.url, json=body, headers=headers)
            except httpx.ConnectError as error:
                if not already_sent:
                    meter.mark_unsent()
                raise ProviderError(f"Could not connect to Serper ({type(error).__name__}).", provider=PROVIDER) from error
            except httpx.TransportError as error:
                raise ProviderError(
                    f"The connection to Serper broke ({type(error).__name__}). The page may have been charged.",
                    provider=PROVIDER,
                ) from error
            if response.status_code in FATAL_HTTP_STATUSES:
                raise ProviderError(
                    f"Serper refused the search with HTTP {response.status_code}. Check the Serper API key and its credits.",
                    provider=PROVIDER,
                    status_code=response.status_code,
                    http_status=response.status_code,
                )
            if response.status_code in RETRY_HTTP_STATUSES and attempt < PAGE_RETRIES:
                self._sleep(PAGE_BACKOFF_SECONDS * (attempt + 1))
                continue
            return response
        raise AssertionError("unreachable")


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _storable(response: httpx.Response) -> bytes:
    """The page body as received when it is JSON, else a small JSON note with the HTTP status and text."""
    content = response.content
    try:
        json.loads(content)
        return content
    except ValueError:
        return canonical_json({"http_status": response.status_code, "body_text": response.text[:2000]}).encode("utf-8")
