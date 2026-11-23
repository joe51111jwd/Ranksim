"""The seed-site manifest: our own small sites, their target searches, and when they are read.

Build plan step 3 and data source 11. Ten small sites we build ourselves, each with
20 target searches split a third easy, a third medium and a third hard. The clean
reader reads every search on a fixed schedule until day 90.

What is REAL here: nothing is measured. The difficulty values are inputs from a
named metric (for example DataForSEO keyword difficulty), copied in as given.
What is PREDICTED here: nothing. This module only picks searches, freezes the
list before launch, and computes dates and reading counts.

Rules this module enforces (audit finding F24):

- The difficulty metric is one named metric for the whole pool. Mixed metrics are refused.
- Terciles come from the whole candidate pool, so easy means the same thing for every site.
- The pick is seeded and deterministic. The same pool and seed give the same searches.
- No search goes to two sites of the same niche unless `allow_shared=True` is passed and
  recorded in the manifest. `validate` makes a shared search an error otherwise.
- A list is frozen before launch: `freeze` refuses a manifest with a site that launches on or
  before the day the manifest was created or frozen, unless `after_launch_reason` is recorded
  (a backfilled pilot).
- A frozen manifest has its hash written beside it. `load_frozen` refuses a manifest
  whose content no longer matches that hash.

Dates are UTC calendar dates. Day N is `launch_date + N days`. Costs are in USD.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TextIO

from ..site_history import site_key
from .asof import as_date, parse_utc, utc_iso
from .chain import canonical_json, sha256_text
from .config import (
    DEFAULT_DEVICE,
    DEFAULT_LANGUAGE,
    EXCLUDED_SITES,
    OUTCOME_DAY,
    STAGE_DAYS,
)

TERCILES: tuple[str, ...] = ("easy", "medium", "hard")
DEFAULT_SEARCHES_PER_SITE = 20
MIN_PAGES_PER_SITE = 10
MAX_PAGES_PER_SITE = 15
READING_INTERVAL_DAYS = 7
MILESTONE_DAYS: tuple[int, ...] = (7, 14, 30, 60, 90)
VALID_DEVICES: tuple[str, ...] = ("desktop", "mobile")
MANIFEST_VERSION = 1
HASH_SUFFIX = ".sha256"

# The plan's marker dates (docs/plan/BUILD_PLAN_ROAD_TO_90.md, Markers table).
PLAN_MARKER_DATES: dict[str, date] = {
    "marker1": date(2026, 11, 30),
    "marker2": date(2026, 12, 14),
    "marker3": date(2026, 12, 21),
    "marker4": date(2027, 1, 22),
    "marker5": date(2027, 3, 19),
}

# Which seed-site dates a marker needs to have happened by its own date.
# Marker 4 uses day-30 readings, and grading a day-30 forecast needs the day-90 outcome.
# Marker 5 grades the Confirmed (day 60) forecast against the day-90 outcome.
MARKER_NEEDS: dict[str, tuple[str, ...]] = {
    "marker4": ("d30", "outcome"),
    "marker5": ("d60", "outcome"),
}


class ManifestError(ValueError):
    """A seed manifest is invalid, or a frozen manifest does not match its hash."""


def normalize_search(text: str) -> str:
    """The comparable form of a search: lower case, single spaces, no outer spaces."""
    return " ".join(str(text).casefold().split())


# --------------------------------------------------------------------------- picking


@dataclass(frozen=True)
class SearchCandidate:
    """One possible target search with its difficulty from one named metric."""

    search: str
    difficulty_value: float
    difficulty_source: str
    volume: int | None = None


@dataclass(frozen=True)
class SeedSearch:
    """One picked target search for one seed site."""

    search: str
    tercile: str
    difficulty_value: float
    difficulty_source: str
    volume: int | None = None
    target_url: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "search": self.search,
            "tercile": self.tercile,
            "difficulty_value": self.difficulty_value,
            "difficulty_source": self.difficulty_source,
            "volume": self.volume,
            "target_url": self.target_url,
        }


def tercile_quota(count: int) -> dict[str, int]:
    """How many searches each tercile gets when `count` are picked.

    The rule: each tercile gets count // 3. The leftover (0, 1 or 2) goes one each
    to easy first, then medium. So 20 gives easy 7, medium 7, hard 6, and 10 gives 4, 3, 3.
    """
    if count < 0:
        raise ValueError(f"cannot pick {count} searches")
    base, extra = divmod(count, 3)
    return {name: base + (1 if index < extra else 0) for index, name in enumerate(TERCILES)}


def _check_pool(candidates: Sequence[SearchCandidate]) -> list[SearchCandidate]:
    if not candidates:
        raise ValueError("the candidate pool is empty")
    sources = {candidate.difficulty_source for candidate in candidates}
    if len(sources) != 1:
        raise ValueError(f"the pool mixes difficulty metrics {sorted(sources)}. Use one named metric.")
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_search(candidate.search)
        if not key:
            raise ValueError("a candidate search is empty")
        if key in seen:
            raise ValueError(f"the search {candidate.search!r} is in the pool twice")
        seen.add(key)
        value = candidate.difficulty_value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"the search {candidate.search!r} has no usable difficulty value ({value!r})")
    return sorted(candidates, key=lambda item: (float(item.difficulty_value), normalize_search(item.search)))


def assign_terciles(candidates: Sequence[SearchCandidate]) -> dict[str, str]:
    """Map each normalized search to easy, medium or hard by rank in the whole pool.

    The pool is sorted by difficulty value (ties by search text). The candidate at
    rank i of n falls in tercile floor(3 * i / n). Equal values never straddle a
    cut: a group of equal values takes the tercile of its first member.
    """
    ordered = _check_pool(candidates)
    total = len(ordered)
    result: dict[str, str] = {}
    group_value: float | None = None
    group_tercile = TERCILES[0]
    for index, candidate in enumerate(ordered):
        value = float(candidate.difficulty_value)
        if group_value is None or value != group_value:
            group_value = value
            group_tercile = TERCILES[min(2, (3 * index) // total)]
        result[normalize_search(candidate.search)] = group_tercile
    return result


def _site_seed(seed: int, label: str) -> int:
    return int(hashlib.sha256(f"{seed}:{label}".encode()).hexdigest()[:16], 16)


def pick_target_searches(
    candidates: Sequence[SearchCandidate],
    *,
    per_site: int = DEFAULT_SEARCHES_PER_SITE,
    seed: int,
    exclude: Iterable[str] = (),
) -> list[SeedSearch]:
    """The target searches for one site, as close to a third per tercile as `per_site` allows.

    Terciles come from the whole pool (`assign_terciles`). Searches in `exclude`
    (for example the ones already given to another site of the same niche) are not
    picked. Inside each tercile the pick is a seeded random sample of the sorted
    candidates, so the same pool and seed always give the same list. Raises
    ValueError when a tercile has too few candidates left. It never quietly moves
    the shortfall to another tercile.
    """
    terciles = assign_terciles(candidates)
    blocked = {normalize_search(text) for text in exclude}
    quota = tercile_quota(per_site)
    rng = random.Random(seed)
    ordered = _check_pool(candidates)
    picked: list[SeedSearch] = []
    for name in TERCILES:
        pool = [c for c in ordered if terciles[normalize_search(c.search)] == name and normalize_search(c.search) not in blocked]
        need = quota[name]
        if len(pool) < need:
            raise ValueError(f"the {name} tercile has {len(pool)} free candidates, and {need} are needed")
        chosen = rng.sample(pool, need)
        chosen.sort(key=lambda item: (float(item.difficulty_value), normalize_search(item.search)))
        picked.extend(
            SeedSearch(
                search=item.search,
                tercile=name,
                difficulty_value=float(item.difficulty_value),
                difficulty_source=item.difficulty_source,
                volume=item.volume,
            )
            for item in chosen
        )
    return picked


def pick_niche_searches(
    candidates: Sequence[SearchCandidate],
    domains: Sequence[str],
    *,
    per_site: int = DEFAULT_SEARCHES_PER_SITE,
    seed: int,
    allow_shared: bool = False,
    exclude: Iterable[str] = (),
) -> dict[str, list[SeedSearch]]:
    """Target searches for every site of one niche, with no search on two sites unless `allow_shared`.

    Sites are handled in sorted domain order so the result does not depend on the
    order the domains were given in. Each site gets its own seed derived from `seed`
    and its domain. `exclude` holds searches already given to other sites of the niche
    (for example in the manifest being added to); none of them is picked again unless
    `allow_shared`.
    """
    blocked = set() if allow_shared else {normalize_search(text) for text in exclude}
    keys = [site_key(domain) for domain in domains]
    if len(set(keys)) != len(keys):
        raise ValueError("a domain is listed twice")
    if not allow_shared:
        sizes = {name: 0 for name in TERCILES}
        for search, name in assign_terciles(candidates).items():
            if search not in blocked:
                sizes[name] += 1
        quota = tercile_quota(per_site)
        for name in TERCILES:
            needed = quota[name] * len(keys)
            if sizes[name] < needed:
                raise ValueError(
                    f"{len(keys)} sites need {needed} {name} searches with none shared, "
                    f"and the pool has {sizes[name]}"
                    + (" not already used by the niche" if blocked else "")
                    + ". Add candidates or pass allow_shared=True."
                )
    result: dict[str, list[SeedSearch]] = {}
    used: set[str] = set(blocked)
    for domain in sorted(keys):
        picked = pick_target_searches(
            candidates, per_site=per_site, seed=_site_seed(seed, domain), exclude=() if allow_shared else used
        )
        used.update(normalize_search(item.search) for item in picked)
        result[domain] = picked
    return result


# --------------------------------------------------------------------------- manifest


@dataclass(frozen=True)
class SeedSite:
    """One seed site. `launch_date` is the UTC date it first served its real pages."""

    domain: str
    niche: str
    launch_date: date | None
    pages: int
    searches: tuple[SeedSearch, ...]
    built_by: str = "ranksim"

    def to_payload(self) -> dict[str, Any]:
        order = {name: index for index, name in enumerate(TERCILES)}
        searches = sorted(
            self.searches, key=lambda item: (order.get(item.tercile, len(order)), normalize_search(item.search))
        )
        return {
            "domain": self.domain,
            "niche": self.niche,
            "launch_date": self.launch_date.isoformat() if self.launch_date else None,
            "pages": self.pages,
            "built_by": self.built_by,
            "searches": [item.to_payload() for item in searches],
        }


@dataclass(frozen=True)
class ManifestCheck:
    """The result of `SeedManifest.validate`. Errors block a freeze. Warnings are printed."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class SeedManifest:
    """The frozen description of every seed site: what is read, where, and on what metric."""

    sites: tuple[SeedSite, ...]
    location_code: int
    device: str = DEFAULT_DEVICE
    language: str = DEFAULT_LANGUAGE
    difficulty_metric: str = ""
    created_at: str = field(default_factory=lambda: utc_iso(datetime.now(UTC)))
    # Recorded only when set, so manifests that do not use them keep their hash.
    allow_shared: bool = False  # two sites of one niche may share a search (seeds pick --allow-shared)
    after_launch_reason: str | None = None  # why this list is frozen on or after a launch date (a backfilled pilot)

    def validate(self) -> ManifestCheck:
        errors: list[str] = []
        warnings: list[str] = []
        if isinstance(self.location_code, bool) or not isinstance(self.location_code, int) or self.location_code <= 0:
            errors.append(f"location_code must be a positive whole number, not {self.location_code!r}")
        if self.device not in VALID_DEVICES:
            errors.append(f"device must be one of {list(VALID_DEVICES)}, not {self.device!r}")
        if not str(self.language).strip():
            errors.append("language is empty")
        if not str(self.difficulty_metric).strip():
            errors.append("difficulty_metric is empty. Name the metric the terciles came from.")
        try:
            parse_utc(self.created_at)
        except (TypeError, ValueError):
            errors.append(f"created_at {self.created_at!r} is not a UTC time")
        if not self.sites:
            errors.append("the manifest has no sites")
        seen_domains: dict[str, str] = {}
        by_niche: dict[str, dict[str, list[str]]] = {}
        for site in self.sites:
            key = site_key(site.domain)
            label = site.domain or "(empty domain)"
            if not key:
                errors.append("a site has an empty domain")
                continue
            if key in EXCLUDED_SITES:
                errors.append(f"{label} is an excluded site and cannot be a seed site")
            if key in seen_domains:
                errors.append(f"{label} is listed twice (same site as {seen_domains[key]})")
            seen_domains[key] = label
            if site.launch_date is None:
                errors.append(f"{label} has no launch date")
            if not str(site.niche).strip():
                errors.append(f"{label} has no niche")
            if isinstance(site.pages, bool) or not isinstance(site.pages, int) or site.pages < 1:
                errors.append(f"{label} pages must be a whole number of at least 1, not {site.pages!r}")
            elif not MIN_PAGES_PER_SITE <= site.pages <= MAX_PAGES_PER_SITE:
                warnings.append(
                    f"{label} has {site.pages} pages. The plan says {MIN_PAGES_PER_SITE} to {MAX_PAGES_PER_SITE}."
                )
            if not site.searches:
                errors.append(f"{label} has no target searches")
            counts = {name: 0 for name in TERCILES}
            seen_searches: set[str] = set()
            for item in site.searches:
                norm = normalize_search(item.search)
                if not norm:
                    errors.append(f"{label} has an empty search")
                    continue
                if norm in seen_searches:
                    errors.append(f"{label} lists the search {item.search!r} twice")
                seen_searches.add(norm)
                if item.tercile not in TERCILES:
                    errors.append(f"{label} search {item.search!r} has tercile {item.tercile!r}")
                else:
                    counts[item.tercile] += 1
                if self.difficulty_metric and item.difficulty_source != self.difficulty_metric:
                    errors.append(
                        f"{label} search {item.search!r} uses metric {item.difficulty_source!r}, "
                        f"not the manifest metric {self.difficulty_metric!r}"
                    )
                by_niche.setdefault(site.niche, {}).setdefault(norm, []).append(label)
            expected = tercile_quota(len(site.searches))
            if site.searches and counts != expected:
                warnings.append(f"{label} tercile counts are {counts}. A third each would be {expected}.")
        for niche in sorted(by_niche):
            for norm in sorted(by_niche[niche]):
                owners = sorted(set(by_niche[niche][norm]))
                if len(owners) > 1:
                    message = f"the search {norm!r} is on {len(owners)} sites of niche {niche!r}: {owners}"
                    if self.allow_shared:
                        warnings.append(message + " (allow_shared is recorded)")
                    else:
                        errors.append(message + ". No search goes to two sites of one niche unless allow_shared is recorded.")
        if self.after_launch_reason is not None and not str(self.after_launch_reason).strip():
            errors.append("after_launch_reason is empty. Say why the list is frozen after launch, or leave it out.")
        return ManifestCheck(errors=tuple(errors), warnings=tuple(warnings))

    def to_payload(self) -> dict[str, Any]:
        sites = sorted(self.sites, key=lambda site: site_key(site.domain))
        payload: dict[str, Any] = {
            "manifest_version": MANIFEST_VERSION,
            "location_code": self.location_code,
            "device": self.device,
            "language": self.language,
            "difficulty_metric": self.difficulty_metric,
            "created_at": self.created_at,
            "sites": [site.to_payload() for site in sites],
        }
        if self.allow_shared:
            payload["allow_shared"] = True
        if self.after_launch_reason is not None:
            payload["after_launch_reason"] = self.after_launch_reason
        return payload

    def manifest_hash(self) -> str:
        """sha256 of the canonical JSON of the manifest. Site and search order do not change it."""
        return sha256_text(canonical_json(self.to_payload()))

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SeedManifest:
        try:
            version = payload.get("manifest_version", MANIFEST_VERSION)
            if version != MANIFEST_VERSION:
                raise ManifestError(f"manifest version {version!r} is not {MANIFEST_VERSION}")
            sites = []
            for raw in payload["sites"]:
                launch = raw.get("launch_date")
                searches = tuple(
                    SeedSearch(
                        search=str(item["search"]),
                        tercile=str(item["tercile"]),
                        difficulty_value=float(item["difficulty_value"]),
                        difficulty_source=str(item["difficulty_source"]),
                        volume=None if item.get("volume") is None else int(item["volume"]),
                        target_url=item.get("target_url"),
                    )
                    for item in raw["searches"]
                )
                sites.append(
                    SeedSite(
                        domain=str(raw["domain"]),
                        niche=str(raw["niche"]),
                        launch_date=None if launch in (None, "") else as_date(launch),
                        pages=raw["pages"],
                        searches=searches,
                        built_by=str(raw.get("built_by", "ranksim")),
                    )
                )
            return cls(
                sites=tuple(sites),
                location_code=payload["location_code"],
                device=str(payload["device"]),
                language=str(payload["language"]),
                difficulty_metric=str(payload["difficulty_metric"]),
                created_at=str(payload["created_at"]),
                allow_shared=bool(payload.get("allow_shared", False)),
                after_launch_reason=None if payload.get("after_launch_reason") is None else str(payload["after_launch_reason"]),
            )
        except ManifestError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestError(f"the manifest is malformed: {error!r}") from error

    @classmethod
    def load(cls, path: str | Path) -> SeedManifest:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ManifestError(f"{path} is not valid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ManifestError(f"{path} does not hold a JSON object")
        return cls.from_payload(payload)

    def launched_by(self, day: date) -> list[SeedSite]:
        """The sites whose launch date is on or before `day`."""
        return [site for site in self.sites if site.launch_date is not None and site.launch_date <= day]

    def freeze(self, path: str | Path, *, today: date | None = None) -> str:
        """Validate, save, and write the hash beside the manifest. Returns the hash.

        Refuses an invalid manifest. Refuses to replace a frozen manifest with a
        different one: a frozen list is a pre-registration and is never rewritten.

        Refuses a manifest with a site whose launch date is on or before the date it was
        created (created_at) or on or before `today` (the UTC date of the freeze, from the
        caller's clock): a list picked after launch can be chosen from early rankings, so
        it is no pre-registration. The only way past is `after_launch_reason`, recorded in
        the manifest and its hash, for a backfilled pilot.
        """
        check = self.validate()
        if not check.ok:
            raise ManifestError("the manifest has errors and cannot be frozen: " + "; ".join(check.errors))
        if self.after_launch_reason is None:
            created = parse_utc(self.created_at).date()
            for label, day in (("it was created", created), ("it is frozen", today)):
                if day is None:
                    continue
                late = self.launched_by(day)
                if late:
                    first = min(late, key=lambda item: (item.launch_date or day, site_key(item.domain)))
                    raise ManifestError(
                        f"{first.domain} launches on {first.launch_date}, on or before the day {label} "
                        f"({day.isoformat()}). Searches are frozen before launch, or they could be chosen from early "
                        "rankings. For a pilot that was already live, run: python -m aeo.ranksim forecast seeds freeze "
                        f"--manifest {path} --after-launch-reason \"<why>\""
                    )
        target = Path(path)
        sidecar = hash_path(target)
        digest = self.manifest_hash()
        if sidecar.exists():
            existing = sidecar.read_text(encoding="utf-8").strip()
            if existing != digest:
                raise ManifestError(
                    f"{target} is already frozen with hash {existing}. A different manifest cannot replace it."
                )
        self.save(target)
        sidecar.write_text(digest + "\n", encoding="utf-8")
        return digest


def hash_path(path: str | Path) -> Path:
    """Where the frozen hash of a manifest file is kept: the same name plus .sha256."""
    target = Path(path)
    return target.with_name(target.name + HASH_SUFFIX)


def load_frozen(path: str | Path) -> SeedManifest:
    """Load a frozen manifest. Refuses it when the hash file is missing or no longer matches."""
    target = Path(path)
    sidecar = hash_path(target)
    if not sidecar.exists():
        raise ManifestError(f"{target} has no frozen hash at {sidecar}. It was never frozen.")
    expected = sidecar.read_text(encoding="utf-8").strip()
    manifest = SeedManifest.load(target)
    actual = manifest.manifest_hash()
    if actual != expected:
        raise ManifestError(f"{target} was changed after it was frozen. Hash {actual} does not match {expected}.")
    return manifest


# --------------------------------------------------------------------------- schedule


def reading_days(*, weekly: bool = True, until_day: int = OUTCOME_DAY, milestones: bool = True) -> list[int]:
    """Days after launch on which the clean reader reads every search.

    Day 0, then every 7 days up to `until_day` when `weekly`, plus the milestone
    days 7, 14, 30, 60 and 90 that are not after `until_day`, even when they fall
    off the weekly grid. With launch on day 0 and until_day 90 the weekly grid is
    days 0 to 84 (13 readings). Days 30, 60 and 90 are off that grid, so with the
    milestones there are 16.
    """
    if until_day < 0:
        raise ValueError(f"until_day {until_day} is before launch")
    days = {0}
    if weekly:
        days.update(range(0, until_day + 1, READING_INTERVAL_DAYS))
    if milestones:
        days.update(day for day in MILESTONE_DAYS if day <= until_day)
    return sorted(days)


def reading_schedule(
    launch_date: date | datetime | str, *, weekly: bool = True, until_day: int = OUTCOME_DAY, milestones: bool = True
) -> list[date]:
    """The UTC dates to read, from `reading_days` added to the launch date."""
    start = as_date(launch_date)
    return [start + timedelta(days=day) for day in reading_days(weekly=weekly, until_day=until_day, milestones=milestones)]


def reading_count(*, weekly: bool = True, until_day: int = OUTCOME_DAY, milestones: bool = True) -> int:
    """How many readings each search gets. 13 on the weekly grid alone, 16 with the milestones."""
    return len(reading_days(weekly=weekly, until_day=until_day, milestones=milestones))


@dataclass(frozen=True)
class ScheduleCost:
    """Reading cost for a manifest in USD, on the weekly grid alone and with the milestone days."""

    searches: int
    cost_per_reading_usd: float
    readings_weekly_only: int
    readings_with_milestones: int
    cost_weekly_only_usd: float
    cost_with_milestones_usd: float

    @property
    def extra_usd(self) -> float:
        return round(self.cost_with_milestones_usd - self.cost_weekly_only_usd, 6)


def cost(manifest: SeedManifest, cost_per_reading: float, *, until_day: int = OUTCOME_DAY) -> ScheduleCost:
    """What reading every search of the manifest costs, both ways.

    `cost_per_reading` is the USD price of one clean reading of one search (for the
    plan's reader, 5 pulls of 5 pages at $0.0006, that is $0.015). 200 searches give
    13 readings for $39.00 on the weekly grid and 16 for $48.00 with the milestones.
    """
    if isinstance(cost_per_reading, bool) or not math.isfinite(float(cost_per_reading)) or cost_per_reading < 0:
        raise ValueError(f"cost_per_reading must be a price of 0 or more, not {cost_per_reading!r}")
    searches = sum(len(site.searches) for site in manifest.sites)
    weekly_only = reading_count(until_day=until_day, milestones=False)
    with_milestones = reading_count(until_day=until_day, milestones=True)
    return ScheduleCost(
        searches=searches,
        cost_per_reading_usd=float(cost_per_reading),
        readings_weekly_only=weekly_only,
        readings_with_milestones=with_milestones,
        cost_weekly_only_usd=round(searches * weekly_only * float(cost_per_reading), 6),
        cost_with_milestones_usd=round(searches * with_milestones * float(cost_per_reading), 6),
    )


def stage_dates(launch_date: date | datetime | str) -> dict[str, date]:
    """The UTC date of each forecast stage and of the outcome reading, from config.STAGE_DAYS and OUTCOME_DAY."""
    start = as_date(launch_date)
    dates = {stage: start + timedelta(days=day) for stage, day in STAGE_DAYS.items()}
    dates["outcome"] = start + timedelta(days=OUTCOME_DAY)
    return dates


@dataclass(frozen=True)
class LateDate:
    """A seed-site date a marker needs that falls after the marker's own date."""

    domain: str
    stage: str
    stage_date: date
    marker: str
    marker_date: date


def late_dates(manifest: SeedManifest, markers: Mapping[str, date] = PLAN_MARKER_DATES) -> list[LateDate]:
    """Every (site, stage) a marker needs that is not done by the marker's date."""
    found: list[LateDate] = []
    for site in sorted(manifest.sites, key=lambda item: site_key(item.domain)):
        if site.launch_date is None:
            continue
        dates = stage_dates(site.launch_date)
        for marker in sorted(MARKER_NEEDS):
            if marker not in markers:
                continue
            for stage in MARKER_NEEDS[marker]:
                if dates[stage] > markers[marker]:
                    found.append(LateDate(site.domain, stage, dates[stage], marker, markers[marker]))
    return found


def schedule_report(manifest: SeedManifest, markers: Mapping[str, date] = PLAN_MARKER_DATES) -> str:
    """Plain text: each site's stage dates, the markers each date falls after, and what is too late."""
    lines: list[str] = []
    marker_items = sorted(markers.items(), key=lambda item: (item[1], item[0]))
    lines.append("Plan marker dates: " + ", ".join(f"{name} {day.isoformat()}" for name, day in marker_items) + ".")
    for site in sorted(manifest.sites, key=lambda item: site_key(item.domain)):
        if site.launch_date is None:
            lines.append(f"{site.domain}: no launch date, so no stage dates.")
            continue
        lines.append(f"{site.domain} (niche {site.niche}), launch {site.launch_date.isoformat()}:")
        for stage, day in stage_dates(site.launch_date).items():
            after = [name for name, marker_day in marker_items if day > marker_day]
            note = f"after {', '.join(after)}" if after else "before every marker"
            lines.append(f"  {stage} {day.isoformat()}: {note}.")
    late = late_dates(manifest, markers)
    if late:
        lines.append("Too late for a marker:")
        for item in late:
            lines.append(
                f"  {item.domain} {item.stage} is {item.stage_date.isoformat()}, "
                f"after {item.marker} on {item.marker_date.isoformat()}."
            )
    else:
        lines.append("Every date a marker needs comes before that marker.")
    return "\n".join(lines)


def print_schedule(
    manifest: SeedManifest, markers: Mapping[str, date] = PLAN_MARKER_DATES, *, file: TextIO | None = None
) -> None:
    """Print `schedule_report` for the manifest."""
    print(schedule_report(manifest, markers), file=file if file is not None else sys.stdout)
