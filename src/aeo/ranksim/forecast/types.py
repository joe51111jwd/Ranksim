"""Record types shared by the forecast modules.

A `Pull` is one raw provider response for one search: REAL. A `CleanReading` is
the middle position per site over the kept pulls of one reading: REAL. A
`Forecast` is model output: PREDICTED. Positions are integers 1 to 50, or
`NT50` (51) when the site is not in the top 50. A value that is not known is
None. It is never 0 and never NT50.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache
from typing import Any, Protocol, Sequence, runtime_checkable

from aeo.ranksim.site_history import site_key

from .config import EXCLUDED_SITES, NT50, TOP_DEPTH

REAL = "REAL"
PREDICTED = "PREDICTED"
SYNTHETIC = "SYNTHETIC"

# Provider names whose pulls are not observations of Google: the simulated world (synth.PROVIDER)
# and the DataForSEO sandbox, which answers with dummy data (dataforseo.PROVIDER_SANDBOX).
# A reading built from any such pull is SYNTHETIC, never REAL. This is the one list; use
# `provider_is_synthetic` to test a name, since a mode suffix ("dataforseo-sandbox-live") counts too.
SYNTHETIC_PROVIDERS: frozenset[str] = frozenset({"synthetic", "dataforseo-sandbox"})


def provider_is_synthetic(provider: str | None) -> bool:
    """True for a provider whose answers are not observations of Google.

    A name in SYNTHETIC_PROVIDERS counts, and so does one of them followed by a dash and a
    mode (the DataForSEO source names itself "<provider>-<mode>" outside the standard queue).
    """
    name = str(provider or "")
    if name in SYNTHETIC_PROVIDERS:
        return True
    return any(name.startswith(prefix + "-") for prefix in SYNTHETIC_PROVIDERS)


@lru_cache(maxsize=65536)
def _site_of(value: str) -> str:
    return site_key(value)


def is_excluded_site(value: str | None) -> bool:
    """True when a site, domain or URL belongs to config.EXCLUDED_SITES once normalised.

    "www.SmartCrewHQ.com" and "https://blog.smartcrewhq.com/x" are excluded like "smartcrewhq.com".
    A raw string is never compared as it comes.
    """
    if not value:
        return False
    text = str(value)
    return text in EXCLUDED_SITES or _site_of(text) in EXCLUDED_SITES


def clamp_position(value: int | float | None) -> int:
    """A position as stored: 1 to 50, or NT50 for anything deeper. None is not a position."""
    if value is None:
        raise ValueError("a missing position is None, not a position. Decide NT50 where the reading is made.")
    number = int(value)
    if number < 1:
        raise ValueError(f"position {value!r} is below 1")
    return number if number <= TOP_DEPTH else NT50


@dataclass(frozen=True)
class OrganicHit:
    """One organic result of one pull. `rank` counts organic results only."""

    rank: int
    url: str
    domain: str
    site: str
    title: str | None = None
    rank_absolute: int | None = None
    repaired: bool = False


@dataclass(frozen=True)
class PullSpec:
    """What one pull asks the provider for."""

    search: str
    location_code: int
    language_code: str = "en"
    device: str = "desktop"
    depth: int = TOP_DEPTH


@dataclass(frozen=True)
class Pull:
    """One provider response for one search. REAL. `ok` False means the pull failed and holds no result."""

    pull_id: str
    provider: str
    endpoint: str
    spec: PullSpec
    requested_at: str
    completed_at: str
    ok: bool
    status_code: int | None = None
    status_message: str | None = None
    task_id: str | None = None
    provider_datetime: str | None = None
    check_url: str | None = None
    cost: float | None = None
    partial: bool = False
    spell_corrected: bool = False
    repaired: bool = False
    hits: tuple[OrganicHit, ...] = ()
    raw_path: str | None = None
    raw_sha256: str | None = None

    def site_positions(self) -> dict[str, int]:
        """Best organic rank per site in this pull, top 50 only."""
        best: dict[str, int] = {}
        for hit in self.hits:
            if 1 <= hit.rank <= TOP_DEPTH and (hit.site not in best or hit.rank < best[hit.site]):
                best[hit.site] = hit.rank
        return best


@runtime_checkable
class SerpSource(Protocol):
    """Anything that can read Google result lists: DataForSEO, Serper, a replay of saved pulls, the synthetic world.

    `fetch` returns one `Pull` per spec, in the same order. A failed request is a
    `Pull` with ok False. It is never an empty result and never an exception for
    a single bad task. `estimate_cost` is the price in USD of one `fetch` of
    `count` specs at `depth`, before anything is sent. When `keywords` (the
    `count` search texts) are given, a source whose price depends on the text
    (DataForSEO charges five times for a search operator) prices them exactly;
    other sources accept and ignore them.
    """

    name: str

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]: ...

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH, *, keywords: Sequence[str] | None = None) -> float: ...


@dataclass(frozen=True)
class SitePosition:
    """A site's clean position in one reading, with the per-pull positions it came from (NT50 = absent)."""

    site: str
    position: int
    pull_positions: tuple[int, ...]
    best_url: str | None = None


@dataclass(frozen=True)
class CleanReading:
    """The middle position per site over the kept pulls of one reading. REAL (SYNTHETIC from the simulated world).

    Provenance, both None when not known (a reading stored before these fields
    existed, or one built by hand): `reader_version` names the reader rule that
    built it (for example "reader-v2"), and `degraded_reason` says in one line
    why a degraded reading is degraded (a cluster tie, too few kept pulls, the
    only cluster altered against history, no pulls at all). It is None for a
    reading that is not degraded.
    """

    reading_id: str
    search: str
    location_code: int
    device: str
    taken_at: str
    pulls_total: int
    pulls_kept: int
    dropped: tuple[tuple[str, str], ...]
    degraded: bool
    pulls_not_independent: bool
    positions: tuple[SitePosition, ...]
    pull_ids: tuple[str, ...] = ()
    label: str = REAL
    reader_version: str | None = None
    degraded_reason: str | None = None

    def position_of(self, site: str) -> int:
        """The site's clean position, or NT50 when it is not in the reading's top 50."""
        for entry in self.positions:
            if entry.site == site:
                return entry.position
        return NT50

    def ranked_sites(self) -> list[str]:
        """Sites with a clean position in the top 50, best first."""
        inside = [entry for entry in self.positions if entry.position <= TOP_DEPTH]
        return [entry.site for entry in sorted(inside, key=lambda item: (item.position, item.site))]


@dataclass(frozen=True)
class Forecast:
    """One forecast for one (site, search) at one stage. PREDICTED.

    `point` is graded. `range_low`, `range_high` and `label` are shown to the
    customer and reported, and are never graded. `probs` holds the model's
    probability per band (1-3, 4-10, 11-20, 21-50, NT50) when it has one.
    """

    site: str
    search: str
    stage: str
    as_of: date
    point: int
    range_low: int
    range_high: int
    label: str
    model_version: str
    probs: dict[str, float] | None = None
    target_url: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)
    kind: str = PREDICTED

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["as_of"] = self.as_of.isoformat()
        return payload
