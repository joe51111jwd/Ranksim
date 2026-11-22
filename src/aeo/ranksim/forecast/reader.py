"""Part 1, the clean reader: pulls, the poisoned-page filter and the clean reading.

What is REAL here: every `Pull` is one provider response as it came back, and a
`CleanReading` is the middle position per site over the pulls that pass the
frozen filter. Nothing here is PREDICTED. Nothing here invents a position: a
site that is not in a pull's top 50 counts as NT50 (51) for that pull, a failed
pull is a failed pull and never an empty result, and a value that is not known
stays None.

The rules follow docs/plan/IMPLEMENTATION_SPEC.md section 3.

Poisoned-page filter (frozen rule, applied in this order, first reason wins):

1. `not_ok`: the provider said the task failed.
2. `partial`: the pull is marked partial and has fewer organic results than
   `partial_min_organic(depth)`. For the spec's depth of 50 that is
   PARTIAL_MIN_ORGANIC (30). For other depths the threshold scales with the
   requested depth: ceil(PARTIAL_MIN_ORGANIC * depth / TOP_DEPTH), at least 1.
   So a top-10 Serper dry run drops a partial pull with fewer than 6 results.
3. `spell_corrected`: Google answered a corrected spelling.
4. `low_overlap`: OUR READING of "fewer than half of its top-20 sites appear in
   the top 50 of any other pull". Take the distinct sites of the pull's results
   ranked 1 to POISON_TOP_N (capped at the requested depth). A site counts when
   it appears in the top 50 of at least one other usable pull of the reading.
   The pull is dropped when the counted share is below POISON_MIN_OVERLAP. It
   is computed leave-one-out over the pulls that survived rules 1 to 3, all at
   once (a drop does not change the test for the others), and only when at
   least 2 other usable pulls exist. A pull with no sites in its top N fails
   when the other pulls have sites. The rule can be switched off with
   `overlap_rule=False` so a report can show it is not what makes agreement.

Known limit of rule 4, stated and tested: pulls that are poisoned the same way
vouch for each other. Two such pulls out of five are both kept, and the median
still ignores their sites because those sites show in only 2 of 5 pulls. Three
or more such pulls out of five win the median.

The middle position: per site, its best organic rank in each kept pull, or 51
when absent. The clean position is the median exactly as
`aeo.ranksim.consensus.median_position` computes it (even count: the mean of
the two middle values rounded half up). A median above 50 is NT50 and the site
is not listed. A reading with fewer than MIN_KEPT_PULLS kept pulls is degraded.
"""

from __future__ import annotations

import gzip
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from aeo.crawl.urls import domain_of
from aeo.ranksim.consensus import median_position
from aeo.ranksim.site_history import site_key

from .asof import parse_utc, utc_iso
from .chain import canonical_json, sha256_text
from .config import (
    DEFAULT_PULLS,
    EXCLUDED_SITES,
    MIN_KEPT_PULLS,
    MIN_PULL_GAP_SECONDS,
    NT50,
    PARTIAL_MIN_ORGANIC,
    POISON_MIN_OVERLAP,
    POISON_TOP_N,
    TOP_DEPTH,
)
from .types import REAL, CleanReading, OrganicHit, Pull, PullSpec, SerpSource, SitePosition

READER_VERSION = "reader-v1"
ID_HEX_CHARS = 24

# Drop reasons, in the order the filter tests them.
DROP_NOT_OK = "not_ok"
DROP_PARTIAL = "partial"
DROP_SPELL = "spell_corrected"
DROP_LOW_OVERLAP = "low_overlap"
DROP_NO_RESULT = "no_result"  # used only when a whole fetch round failed for a spec
DROP_REASONS = (DROP_NOT_OK, DROP_PARTIAL, DROP_SPELL, DROP_LOW_OVERLAP)

# The committed Serper pulls (evals/claude/serp/<day>/<pull>/chunk-NN.json.gz).
# They sent {"gl": "us", "hl": "en", "num": 10} and no location and no device.
# 2840 is the Google geo target id of the United States, a country, not a city.
SERPER_PROVIDER = "serper"
SERPER_COUNTRY_LOCATION_CODE = 2840
SERPER_DEVICE_UNSPECIFIED = "unspecified"
SERPER_CHUNK_ENDPOINT = "https://google.serper.dev/search gl=us num=10 (no pinned city, no device, top 10 only)"


# ---------------------------------------------------------------------------
# Ids


def _spec_payload(spec: PullSpec) -> dict[str, object]:
    return {
        "search": spec.search,
        "location_code": int(spec.location_code),
        "language_code": spec.language_code,
        "device": spec.device,
        "depth": int(spec.depth),
    }


def new_pull_id(provider: str, spec: PullSpec, requested_at: str, *, sequence: int | str = 0) -> str:
    """A deterministic pull id: a sha256 prefix of provider, the request and the requested UTC time.

    `sequence` separates two pulls of the same request made in the same second.
    """
    payload = {"provider": provider, "spec": _spec_payload(spec), "requested_at": requested_at, "sequence": str(sequence)}
    return "pull-" + sha256_text(canonical_json(payload))[:ID_HEX_CHARS]


def new_reading_id(spec: PullSpec, pull_ids: Sequence[str]) -> str:
    """A deterministic reading id from the request and the set of pull ids (their order does not matter)."""
    payload = {"spec": _spec_payload(spec), "pull_ids": sorted(pull_ids), "reader": READER_VERSION}
    return "reading-" + sha256_text(canonical_json(payload))[:ID_HEX_CHARS]


# ---------------------------------------------------------------------------
# Replay source


class ReplaySource:
    """A SerpSource over pulls already on disk or in memory. For tests and dry runs. It never spends.

    Pulls are served in the order given, per exact `PullSpec`. When a spec has
    no pull left, `fetch` returns a failed pull for it (ok False), never an
    empty result and never an exception.
    """

    def __init__(self, pulls: Iterable[Pull], *, name: str = "replay", clock: Callable[[], datetime] | None = None) -> None:
        self.name = name
        self._queues: dict[PullSpec, list[Pull]] = defaultdict(list)
        for pull in pulls:
            self._queues[pull.spec].append(pull)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.served = 0
        self.misses = 0

    def remaining(self, spec: PullSpec) -> int:
        return len(self._queues.get(spec, ()))

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]:
        out: list[Pull] = []
        for spec in specs:
            queue = self._queues.get(spec)
            if queue:
                out.append(queue.pop(0))
                self.served += 1
                continue
            stamp = utc_iso(self._clock())
            self.misses += 1
            out.append(
                Pull(
                    pull_id=new_pull_id(self.name, spec, stamp, sequence=f"miss-{self.misses}"),
                    provider=self.name,
                    endpoint="replay",
                    spec=spec,
                    requested_at=stamp,
                    completed_at=stamp,
                    ok=False,
                    status_message="replay has no more pulls for this request",
                )
            )
        return out

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH) -> float:
        return 0.0


# ---------------------------------------------------------------------------
# Committed Serper chunks


def _serper_item_pull(item: dict, *, path: Path, index: int, label: str, did_you_mean_is_correction: bool) -> Pull:
    query = str(item.get("query") or item.get("q") or "")
    if not query:
        raise ValueError(f"{path} item {index} has no query")
    if not item.get("start"):
        raise ValueError(f"{path} item {index} has no start time, so the pull cannot be dated")
    requested_at = utc_iso(parse_utc(str(item["start"])))
    completed_at = utc_iso(parse_utc(str(item.get("end") or item["start"])))
    raw_text = item.get("raw")
    raw_sha = sha256_text(raw_text) if isinstance(raw_text, str) else None
    status = item.get("status")
    status_code = int(status) if status is not None else None

    payload: dict | None = None
    problem: str | None = None
    if status_code != 200:
        problem = f"HTTP status {status_code}"
    elif not isinstance(raw_text, str):
        problem = "no raw response stored"
    else:
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as error:
            problem = f"raw response is not JSON: {error.msg}"
        if payload is not None and not isinstance(payload, dict):
            problem, payload = "raw response is not a JSON object", None
        if payload is not None and not isinstance(payload.get("organic"), list):
            problem, payload = "raw response has no organic list", None

    params = (payload or {}).get("searchParameters") or {}
    depth = int(params.get("num") or 10)
    spec = PullSpec(
        search=query,
        location_code=SERPER_COUNTRY_LOCATION_CODE,
        language_code=str(params.get("hl") or "en"),
        device=SERPER_DEVICE_UNSPECIFIED,
        depth=depth,
    )
    common = dict(
        pull_id=new_pull_id(SERPER_PROVIDER, spec, requested_at, sequence=label),
        provider=SERPER_PROVIDER,
        endpoint=SERPER_CHUNK_ENDPOINT,
        spec=spec,
        requested_at=requested_at,
        completed_at=completed_at,
        status_code=status_code,
        raw_path=f"{path}#items[{index}]",
        raw_sha256=raw_sha,
    )
    if payload is None:
        return Pull(ok=False, status_message=problem, **common)

    hits: list[OrganicHit] = []
    organic = payload["organic"]
    for entry in organic:
        if not isinstance(entry, dict) or not entry.get("link"):
            continue
        try:
            rank = int(entry["position"])
        except (KeyError, TypeError, ValueError):
            continue  # an organic row without a usable position is not given one
        link = str(entry["link"])
        site = site_key(link)
        if rank < 1 or rank > depth or not site or site in EXCLUDED_SITES:
            continue  # the provider's rank is kept, so a dropped row leaves a gap and moves nobody
        hits.append(OrganicHit(rank=rank, url=link, domain=domain_of(link), site=site, title=entry.get("title")))
    info = payload.get("searchInformation") or {}
    suggestion = info.get("didYouMean") if isinstance(info, dict) else None
    return Pull(
        ok=True,
        status_message=f"didYouMean: {suggestion}" if suggestion else None,
        partial=len(organic) < depth,
        spell_corrected=bool(suggestion) and did_you_mean_is_correction,
        hits=tuple(sorted(hits, key=lambda hit: (hit.rank, hit.url))),
        **common,
    )


def load_serper_chunk_pulls(directory: str | Path, *, did_you_mean_is_correction: bool = False) -> list[Pull]:
    """Pulls from committed Serper chunks: `<directory>/chunk-NN.json.gz`, one Pull per stored item.

    REAL, but weaker than the plan's reader: top 10 only, `gl=us`, no pinned
    city and no device. That is said on every Pull (`endpoint`, the country
    `location_code` 2840 and the device "unspecified"). Hits for EXCLUDED_SITES
    are dropped and the other ranks are left as Google gave them. A stored item
    with a non-200 status or an unreadable body is a failed Pull. `partial` is
    True when fewer organic results came back than were asked for.

    Serper's `didYouMean` is a suggestion. Whether Google answered the corrected
    query instead is not known from the stored response, so by default it is
    kept in `status_message` and does not mark the pull spell corrected.
    """
    folder = Path(directory)
    files = sorted(folder.glob("chunk-*.json.gz"))
    if not files:
        raise FileNotFoundError(f"no chunk-*.json.gz files in {folder}")
    pulls: list[Pull] = []
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            document = json.load(handle)
        label = str(document.get("label") or folder.name)
        for index, item in enumerate(document.get("items") or []):
            pulls.append(
                _serper_item_pull(item, path=path, index=index, label=label, did_you_mean_is_correction=did_you_mean_is_correction)
            )
    return pulls


# ---------------------------------------------------------------------------
# The poisoned-page filter


def partial_min_organic(depth: int) -> int:
    """Fewest organic results a partial pull may have and still be kept, scaled to the requested depth.

    ceil(PARTIAL_MIN_ORGANIC * depth / TOP_DEPTH), at least 1: 30 at depth 50, 6 at depth 10.
    """
    if depth < 1:
        raise ValueError(f"depth {depth} is below 1")
    return max(1, math.ceil(PARTIAL_MIN_ORGANIC * int(depth) / TOP_DEPTH))


def pull_site_positions(pull: Pull) -> dict[str, int]:
    """Best organic rank per site, inside the requested depth and the top 50, excluded sites removed."""
    limit = min(int(pull.spec.depth), TOP_DEPTH)
    return {site: rank for site, rank in pull.site_positions().items() if rank <= limit and site not in EXCLUDED_SITES}


def _top_sites(pull: Pull, top_n: int) -> set[str]:
    limit = min(top_n, int(pull.spec.depth), TOP_DEPTH)
    return {site for site, rank in pull_site_positions(pull).items() if rank <= limit}


def _check_one_request(pulls: Sequence[Pull]) -> PullSpec:
    if not pulls:
        raise ValueError("a reading needs at least one pull")
    first = pulls[0].spec
    for pull in pulls[1:]:
        if pull.spec != first:
            raise ValueError(
                f"pulls of one reading must share one request. {pull.pull_id} asked for {pull.spec}, not {first}"
            )
    ids = [pull.pull_id for pull in pulls]
    if len(set(ids)) != len(ids):
        raise ValueError("the same pull id appears twice in one reading")
    return first


def filter_pulls(pulls: Sequence[Pull], *, overlap_rule: bool = True) -> tuple[list[Pull], list[tuple[str, str]]]:
    """Apply the frozen poisoned-page filter to the pulls of one reading.

    Returns (kept pulls, dropped) where dropped is a list of (pull_id, reason
    text). The reason text starts with one of DROP_REASONS. Order of the input
    is kept. `overlap_rule=False` switches rule 4 off for the ablation.
    """
    spec = _check_one_request(pulls)
    threshold = partial_min_organic(spec.depth)
    dropped: dict[str, str] = {}
    usable: list[Pull] = []
    for pull in pulls:
        if not pull.ok:
            detail = " ".join(str(part) for part in (pull.status_code, pull.status_message) if part is not None)
            dropped[pull.pull_id] = f"{DROP_NOT_OK}: {detail}" if detail else DROP_NOT_OK
        elif pull.partial and len(pull.hits) < threshold:
            dropped[pull.pull_id] = f"{DROP_PARTIAL}: {len(pull.hits)} organic results, fewer than {threshold}"
        elif pull.spell_corrected:
            dropped[pull.pull_id] = DROP_SPELL
        else:
            usable.append(pull)

    if overlap_rule and len(usable) >= 3:
        top50 = {pull.pull_id: set(pull_site_positions(pull)) for pull in usable}
        for pull in usable:
            mine = _top_sites(pull, POISON_TOP_N)
            seen_elsewhere: set[str] = set()
            for other in usable:
                if other.pull_id != pull.pull_id:
                    seen_elsewhere |= top50[other.pull_id]
            if not mine:
                if seen_elsewhere:
                    dropped[pull.pull_id] = f"{DROP_LOW_OVERLAP}: no sites in its top {POISON_TOP_N}, the other pulls have some"
                continue
            share = len(mine & seen_elsewhere) / len(mine)
            if share < POISON_MIN_OVERLAP:
                dropped[pull.pull_id] = (
                    f"{DROP_LOW_OVERLAP}: {len(mine & seen_elsewhere)} of its {len(mine)} top sites "
                    f"appear in another pull's top {TOP_DEPTH}, below {POISON_MIN_OVERLAP:g}"
                )

    kept = [pull for pull in pulls if pull.pull_id not in dropped]
    reasons = [(pull.pull_id, dropped[pull.pull_id]) for pull in pulls if pull.pull_id in dropped]
    return kept, reasons


# ---------------------------------------------------------------------------
# The clean reading


def _url_list(pull: Pull) -> tuple[str, ...]:
    return tuple(hit.url for hit in sorted(pull.hits, key=lambda hit: (hit.rank, hit.url)))


def pulls_look_dependent(first: Pull, second: Pull) -> bool:
    """True when two pulls look like one fetch served twice.

    Same raw sha256, or the same ordered URL list with the same provider result
    time. A missing provider time is not a match: Serper gives none, and two
    honest pulls can return the same list.
    """
    if first.raw_sha256 is not None and first.raw_sha256 == second.raw_sha256:
        return True
    if first.provider_datetime is None or second.provider_datetime is None:
        return False
    return first.provider_datetime == second.provider_datetime and _url_list(first) == _url_list(second)


def _order_key(pull: Pull) -> tuple[str, str]:
    return (utc_iso(parse_utc(pull.completed_at)), pull.pull_id)


def build_clean_reading(
    pulls: Sequence[Pull], *, reading_id: str | None = None, overlap_rule: bool = True
) -> CleanReading:
    """The clean reading of one request from its pulls. A pure function of its inputs.

    Raises ValueError for no pulls or pulls of different requests. The result
    lists only sites whose clean position is 1 to 50, best first.
    """
    spec = _check_one_request(pulls)
    kept, dropped = filter_pulls(pulls, overlap_rule=overlap_rule)
    kept = sorted(kept, key=_order_key)
    per_pull = [pull_site_positions(pull) for pull in kept]
    sites = sorted(set().union(*per_pull)) if per_pull else []

    positions: list[SitePosition] = []
    for site in sites:
        values = tuple(found.get(site, NT50) for found in per_pull)
        middle = median_position(list(values))
        if middle > TOP_DEPTH:
            continue
        best_url = None
        for pull, found in zip(kept, per_pull):
            if found.get(site) == middle:
                best_url = min(
                    (hit for hit in pull.hits if hit.site == site and hit.rank == middle),
                    key=lambda hit: hit.url,
                ).url
                break
        positions.append(SitePosition(site=site, position=middle, pull_positions=values, best_url=best_url))
    positions.sort(key=lambda entry: (entry.position, entry.site))

    dependent = any(
        pulls_look_dependent(kept[i], kept[j]) for i in range(len(kept)) for j in range(i + 1, len(kept))
    )
    stamps = [parse_utc(pull.completed_at) for pull in (kept or list(pulls))]
    return CleanReading(
        reading_id=reading_id or new_reading_id(spec, [pull.pull_id for pull in pulls]),
        search=spec.search,
        location_code=int(spec.location_code),
        device=spec.device,
        taken_at=utc_iso(max(stamps)),
        pulls_total=len(pulls),
        pulls_kept=len(kept),
        dropped=tuple(dropped),
        degraded=len(kept) < MIN_KEPT_PULLS,
        pulls_not_independent=dependent,
        positions=tuple(positions),
        pull_ids=tuple(pull.pull_id for pull in kept),
        label=REAL,
    )


def drop_rate(reading: CleanReading) -> float | None:
    """Share of a reading's pulls the filter dropped. None when the reading had no pulls."""
    if reading.pulls_total <= 0:
        return None
    return (reading.pulls_total - reading.pulls_kept) / reading.pulls_total


# ---------------------------------------------------------------------------
# Reading many searches


@dataclass(frozen=True)
class ReadResult:
    """What one `read_clean` call produced. `failed_rounds` holds (round index, error text)."""

    readings: tuple[CleanReading, ...]
    pulls: tuple[Pull, ...]
    failed_rounds: tuple[tuple[int, str], ...] = ()


def _empty_reading(spec: PullSpec, reasons: Sequence[tuple[str, str]], taken_at: str) -> CleanReading:
    return CleanReading(
        reading_id=new_reading_id(spec, []),
        search=spec.search,
        location_code=int(spec.location_code),
        device=spec.device,
        taken_at=taken_at,
        pulls_total=0,
        pulls_kept=0,
        dropped=tuple(reasons),
        degraded=True,
        pulls_not_independent=False,
        positions=(),
        pull_ids=(),
        label=REAL,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_clean(
    source: SerpSource,
    specs: Sequence[PullSpec],
    *,
    pulls: int = DEFAULT_PULLS,
    gap_seconds: float = MIN_PULL_GAP_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utc_now,
    overlap_rule: bool = True,
) -> ReadResult:
    """Read every spec `pulls` times and build one clean reading per spec.

    One `source.fetch(specs)` per round. Round k+1 starts at least
    `gap_seconds` after round k started (the wait goes through `sleep`, the
    time through `now`). A round that raises, returns the wrong number of pulls
    or pulls for other requests is recorded in `failed_rounds`, and the
    readings are built from the rounds that worked. A spec with no pull at all,
    or whose pulls cannot form a reading (for example one pull id served
    twice), gets an empty degraded reading with the reason, and its pulls stay
    in `pulls`. This function does not check a budget: the
    caller reserves `cost_of_reading(...)` first.
    """
    if pulls < 1:
        raise ValueError("pulls must be at least 1")
    if gap_seconds < 0:
        raise ValueError("gap_seconds cannot be negative")
    if not specs:
        raise ValueError("no specs to read")
    if len(set(specs)) != len(specs):
        raise ValueError("the same spec appears twice. Each spec is one reading.")

    collected: dict[PullSpec, list[Pull]] = {spec: [] for spec in specs}
    every: list[Pull] = []
    failed: list[tuple[int, str]] = []
    round_start: datetime | None = None
    for index in range(pulls):
        if round_start is not None:
            elapsed = (now() - round_start).total_seconds()
            if elapsed < gap_seconds:
                sleep(gap_seconds - elapsed)
        round_start = now()
        try:
            got = list(source.fetch(list(specs)))
        except Exception as error:  # a whole round failed: record it and go on with the others
            failed.append((index, f"{type(error).__name__}: {error}"))
            continue
        if len(got) != len(specs):
            failed.append((index, f"source returned {len(got)} pulls for {len(specs)} specs"))
            continue
        wrong = [i for i, (spec, pull) in enumerate(zip(specs, got)) if pull.spec != spec]
        if wrong:
            failed.append((index, f"source returned pulls out of order or for other requests at positions {wrong}"))
            continue
        for spec, pull in zip(specs, got):
            collected[spec].append(pull)
            every.append(pull)

    round_reasons = [(f"round-{index}", f"{DROP_NO_RESULT}: {text}") for index, text in failed]
    readings: list[CleanReading] = []
    for spec in specs:
        mine = collected[spec]
        if not mine:
            readings.append(_empty_reading(spec, round_reasons, utc_iso(now())))
            continue
        try:
            reading = build_clean_reading(mine, overlap_rule=overlap_rule)
        except ValueError as error:  # the source broke the contract for this spec; keep the other readings
            reasons = round_reasons + [("reading", f"invalid_pulls: {error}")]
            readings.append(_empty_reading(spec, reasons, utc_iso(now())))
            continue
        if round_reasons:
            reading = replace(reading, dropped=reading.dropped + tuple(round_reasons))
        readings.append(reading)
    return ReadResult(readings=tuple(readings), pulls=tuple(every), failed_rounds=tuple(failed))


# ---------------------------------------------------------------------------
# Cost preview


@dataclass(frozen=True)
class CostPreview:
    """What a reading would cost before anything is sent. USD, from the source's own price table."""

    provider: str
    searches: int
    pulls: int
    depth: int
    usd_per_round: float
    usd_total: float
    notes: tuple[str, ...] = field(default_factory=tuple)


def cost_of_reading(source: SerpSource, searches: int | Sequence[object], pulls: int = DEFAULT_PULLS, depth: int = TOP_DEPTH) -> CostPreview:
    """The cost of reading `searches` searches `pulls` times at `depth`: one fetch per round."""
    count = searches if isinstance(searches, int) else len(searches)
    if count < 0 or pulls < 1 or depth < 1:
        raise ValueError("searches must be 0 or more, pulls and depth at least 1")
    per_round = float(source.estimate_cost(count, depth)) if count else 0.0
    if per_round < 0 or math.isnan(per_round):
        raise ValueError(f"{source.name} gave a cost of {per_round}, which is not a price")
    return CostPreview(
        provider=source.name,
        searches=count,
        pulls=pulls,
        depth=depth,
        usd_per_round=per_round,
        usd_total=per_round * pulls,
        notes=(f"{pulls} rounds of {count} searches at depth {depth}",),
    )


def gap_between(first: CleanReading, second: CleanReading) -> timedelta:
    """Absolute time between two readings' `taken_at`."""
    return abs(parse_utc(second.taken_at) - parse_utc(first.taken_at))
