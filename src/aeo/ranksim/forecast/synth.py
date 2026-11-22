"""A synthetic world with known truth, for end-to-end tests and power analysis only.

Everything this module produces is SYNTHETIC. None of it is REAL and none of it
is PREDICTED. It is never reported as accuracy, never mixed with seed, pilot or
backtest results, and never used as training data for a model that is graded
on real outcomes. Every pull says SYNTHETIC in `status_message`, every truth row
carries the label SYNTHETIC, and every dated list names a `synthetic-` source.
No SmartCrew data is read or imitated. Sites live under the reserved `.example`
top-level domain, so no synthetic site can be a real one.

The model (the latent score of docs/plan and the master plan section 2.1):

    u(site, search, day) = KAPPA * m(site, search)      content match times a weight
                         + S(site, day)                 global site strength, drifts slowly
                         + F(site, niche)               topical focus of the site in the niche
                         + T(site, day)                 trust (new sites only, 0 for established sites)
                         + r(site, search, day)         a slow random walk per page

The true ranking of a search on a day is the sort of u over the pages that are
indexed that day. `World.true_position` gives 1 to 50, or NT50.

Variance shares are set from the master plan map: site part about 75% of the
score variance among candidates (R about 3, a DERIVED figure with a 45% to 95%
range), content about 10% to 12%, the page's unseen part the rest. Searches get
a difficulty from who competes: on high-value searches the strong sites also
have well-matched pages, so the score a page needs to reach page one is higher.
Difficulty is measured as the true score at position 10 on the first day, then
cut into terciles inside each niche (easy, medium, hard).

New sites: a launch date, a footprint (links, brand demand, reviews), a launch
plan, an indexing delay, and trust that starts at 0 on the day the site is
indexed and grows toward a site-specific ceiling at a site-specific speed:

    T(day) = ceiling * (1 - exp(-(day - index_day) / tau))

The ceiling is a function of footprint and content quality plus noise of its
own (`trust_noise_sd`), so trust can be estimated from the footprint but not
computed from it, as the build plan says. A share of new sites is parked or
dead: they are never indexed and never rank. The defaults put about 63% of the
day-90 cells of a site's chosen target searches beyond position 50 when the
targets are a third easy, a third medium and a third hard. That is the share the
build plan assumes for "always say not in top 50". It is a tuning target here,
not a measurement.

Observation noise on top of the truth (all knobs in `SynthKnobs`):

- day movement: one score shock per (site, search, UTC day), shared by every
  pull of that day. It is Google moving, not the reader.
- per-pull jitter: one score shock per (site, search, pull). Score noise of a
  fixed size moves deep pages more places than top pages because scores are
  packed tighter deeper in the list, so jitter grows with depth.
- jumps: with probability `jump_rate` per pull a page at place p moves a
  uniform random distance of up to max(`jump_span_min`, `jump_span_share` * p)
  places either way, so top pages jump less than deep ones (after the jump-mixture model of
  r4-statistical-design.md sim 5, which fitted q = 0.10 with a uniform place
  inside top-10 lists; uniform over a 100-deep list moved top pages far more
  than the SEOmonitor daily moves allow, so the jump is local here).
- poisoned pulls (most organic results replaced by unrelated sites), repaired
  pulls (clean list marked repaired), partial pulls (fewer results, status
  40106), failed pulls (status 50000, no hits), spell-corrected pulls (the list
  of a different search). Status codes copy DataForSEO's documented codes.

Figures the noise is roughly calibrated to (from the maps, none re-measured here):

- pull-to-pull disagreement on pairs: 7.0% same session, 10.1% 25 hours apart,
  20.3% 10 to 11 days apart (evals/claude/r2/t3-noise, theory.md line 139).
- two pulls agree on 0.877 of pairs (RESULTS-claude.md 5.1).
- unedited page next day within 2 places: 0.82 at positions 4 to 10, within 1
  place 0.86 at 1 to 3 (r4-edit-simulator-design.md).
- SEOmonitor mean absolute daily move: 0.22 (1 to 3), 1.00 (4 to 10), 2.41
  (11 to 20), 4.83 (21 to 50) (ranking-mechanics.md 2.3).
- poisoned pages: 20% to 30% of scraped pages from 26 September 2026, fewer
  after 10 October, 2% to 3% left after retries (DataForSEO, dataforseo-api map
  section 4). The plan quotes 27.6%. The default rate here is a knob.
- Search Console: data after 2 to 3 days, rare queries anonymized, pages a user
  did not view are not recorded (r4-free-label-sources.md 3).

The CTR curve, the Search Console view chances and the traffic detection floor
are SYNTHETIC modelling choices, not measured numbers. They exist so the dated
lists have a plausible shape. `tests/test_forecast_synth.py` checks the noise
against loose bands around the figures above, and states the bands.

Randomness: one seeded numpy Generator per purpose, keyed by the world seed and
integer keys through `numpy.random.SeedSequence`. There is no global random
state. Times are UTC. A naive datetime is read as UTC.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Sequence

import numpy as np

from .asof import Dated, add_months, as_date, month_start, utc_iso
from .config import NT50, TOP_DEPTH
from .types import SYNTHETIC, OrganicHit, Pull, PullSpec

PROVIDER = "synthetic"
ENDPOINT_LIVE = "synthetic/serp/google/organic"
ENDPOINT_HISTORICAL = "synthetic/dataforseo_labs/google/historical_serps"
STATUS_TEXT = "SYNTHETIC pull from a simulated world. Not a real Google result."

# DataForSEO task status codes (dataforseo-api map section 2), copied so the shape matches.
STATUS_OK = 20000
STATUS_PARTIAL = 40106
STATUS_INVALID = 40501
STATUS_FAILED = 50000

DEFAULT_SEED = 20261122
DEFAULT_CITY_LOCATION_CODE = 1026201  # Austin, Texas, United States (City), per the DataForSEO locations CSV.
COUNTRY_LOCATION_CODE = 2840  # United States. Labs history is country level only.
DEFAULT_START_MONTH = date(2025, 1, 1)
SNAPSHOT_DEPTH = 100
TLD = "example"

NICHE_NAMES = ("plumber", "dentist", "roofer", "electrician", "landscaper", "locksmith", "accountant", "veterinarian")
SEARCH_TEMPLATES = (
    "{n}", "{n} near me", "best {n}", "emergency {n}", "affordable {n}", "{n} prices", "local {n}",
    "{n} reviews", "top rated {n}", "cheap {n}", "24 hour {n}", "{n} open now", "certified {n}",
    "{n} quote", "{n} services", "family {n}", "how to choose a {n}", "{n} cost estimate",
    "licensed {n}", "{n} company", "same day {n}", "{n} free estimate", "residential {n}",
    "commercial {n}", "{n} downtown", "independent {n}", "{n} appointment", "trusted {n}",
    "{n} for small jobs", "experienced {n}",
)
NAME_PARTS = ("alpha", "bright", "cedar", "delta", "ember", "fox", "granite", "harbor", "iris", "juniper",
              "kestrel", "lumen", "maple", "north", "oak", "pine", "quarry", "river", "summit", "timber")

# Random-stream salts. Each purpose has its own stream so adding draws to one never shifts another.
_SALT_BUILD = 1
_SALT_DAY = 2
_SALT_PULL = 3
_SALT_SNAPSHOT = 4
_SALT_NRD = 5
_SALT_GSC = 6
_SALT_LIST = 7
_SALT_FORM = 8

TERCILES = ("easy", "medium", "hard")


@dataclass(frozen=True)
class SynthKnobs:
    """Every tunable number of the synthetic world. Score units are latent score SDs."""

    # Score model (master plan 2.1 shares: site about 0.75, content about 0.11, page part about 0.14).
    kappa: float = 0.33
    strength_sd: float = 0.80
    focus_sd: float = 0.33
    page_sd: float = 0.37
    page_walk_sd_per_month: float = 0.07
    strength_drift_sd_per_month: float = 0.04
    difficulty_coupling: float = 0.8  # how much strong sites' content tracks a search's value
    value_content_shift: float = 1.5  # every incumbent's content rises with a search's value
    participation: float = 0.9  # chance a niche site has a page on a search of its niche
    generalist_share: float = 0.08  # directories that compete on every niche
    generalist_boost: float = 0.6
    generalist_content: float = -0.5
    # New sites.
    new_base: float = -2.2  # site part of an untrusted new site
    new_focus_mean: float = 0.25
    new_focus_sd: float = 0.15
    target_content_mean: float = 1.0
    target_content_sd: float = 0.8
    nontarget_page_share: float = 0.5
    nontarget_content_mean: float = -0.3
    trust_mean: float = 2.0
    trust_noise_sd: float = 0.45
    trust_tau_median_days: float = 40.0
    trust_tau_log_sd: float = 0.5
    dead_share: float = 0.10
    index_delay_median_days: float = 4.0
    index_delay_log_sd: float = 0.8
    page_index_extra_mean_days: float = 5.0
    targets_per_site: int = 20
    # Observation noise.
    day_sd: float = 0.06
    pull_sd: float = 0.05
    jump_rate: float = 0.10
    jump_span_share: float = 0.5  # a jumped page at place p moves uniformly up to this share of p either way
    jump_span_min: float = 1.0
    country_sd: float = 0.08
    poison_rate: float = 0.05
    poison_replace_low: float = 0.6
    poison_replace_high: float = 0.9
    repaired_rate: float = 0.10
    partial_rate: float = 0.03
    fail_rate: float = 0.03
    spell_rate: float = 0.01
    # Dated lists (SYNTHETIC modelling choices).
    strength_publish_day: int = 10
    strength_data_lag_months: int = 1
    strength_list_noise_sd: float = 0.25
    strength_list_quantile: float = 0.4  # sites weaker than this share of established sites are not listed
    traffic_publication_lag_days: int = 14
    traffic_floor_visits: float = 5.0
    gsc_lag_days: int = 2
    gsc_anon_monthly_volume: int = 20
    nrd_decoys_per_day: float = 3.0
    form_missing_share: float = 0.15


DEFAULT_KNOBS = SynthKnobs()


@dataclass(frozen=True)
class SearchInfo:
    """One search of the world. `difficulty` is the true score at position 10 on the first day."""

    index: int
    text: str
    niche: str
    volume: int  # monthly searches, SYNTHETIC
    value: float
    difficulty: float
    tercile: str


@dataclass(frozen=True)
class SiteInfo:
    """One site of the world. `kind` is established, generalist or new."""

    index: int
    site: str
    kind: str
    niche: str | None
    registered: date
    brand: str


@dataclass(frozen=True)
class NewSiteInfo:
    """The planted facts of one new site. SYNTHETIC truth, for scoring only."""

    site: str
    niche: str
    registered: date
    launch_date: date
    index_date: date | None  # None for a dead site
    alive: bool
    trust_ceiling: float
    trust_tau_days: float
    content_quality: float
    referring_domains: int
    brand_search_volume: int
    review_count: int
    review_rating: float | None
    links_planned: int
    press_planned: bool
    pages_at_launch: int
    targets: tuple[str, ...]


@dataclass(frozen=True)
class TruthCell:
    """The SYNTHETIC day-90 position of one (new site, search)."""

    site: str
    search: str
    niche: str
    tercile: str
    is_target: bool
    alive: bool
    launch_date: date
    outcome_date: date
    position: int
    label: str = SYNTHETIC


@dataclass(frozen=True)
class PullEvent:
    """What the synthetic source did to one pull. Kept by the source, never put in the pull."""

    pull_id: str
    search: str
    day: date
    failed: bool
    partial: bool
    poisoned: bool
    repaired: bool
    spell_corrected: bool
    served_search: str | None


def _string_key(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def ctr_at(position: float) -> float:
    """Click share at a position. A SYNTHETIC curve (0.30 / position on page one, smaller after), not a measurement."""
    if position <= 10:
        return 0.30 / position
    if position <= 20:
        return 0.01
    if position <= TOP_DEPTH:
        return 0.002
    return 0.0


def view_chance(position: float) -> float:
    """Chance a searcher scrolls far enough to see a result. SYNTHETIC. Search Console records only viewed results."""
    if position <= 10:
        return 1.0
    if position <= 20:
        return 0.25
    if position <= 30:
        return 0.08
    if position <= TOP_DEPTH:
        return 0.03
    return 0.0


class World:
    """A deterministic synthetic search world. SYNTHETIC truth and SYNTHETIC observations.

    `established_sites` and `new_sites` are totals across niches. Established
    sites get a home niche round robin. A share of them (`generalist_share`)
    are directories that compete on every niche.
    """

    def __init__(
        self,
        seed: int = DEFAULT_SEED,
        *,
        niches: int = 3,
        searches_per_niche: int = 30,
        established_sites: int = 360,
        new_sites: int = 30,
        start_month: date = DEFAULT_START_MONTH,
        months: int = 24,
        location_code: int = DEFAULT_CITY_LOCATION_CODE,
        knobs: SynthKnobs | None = None,
    ) -> None:
        if not 1 <= niches <= len(NICHE_NAMES):
            raise ValueError(f"niches must be 1 to {len(NICHE_NAMES)}")
        if searches_per_niche < 3:
            raise ValueError("searches_per_niche must be at least 3 so there is an easy, medium and hard tercile")
        if established_sites < niches * 11:
            raise ValueError("established_sites is too small: each niche needs more than 10 competitors")
        if new_sites < 0 or months < 1 or seed < 0:
            raise ValueError("new_sites, months and seed must be non-negative and months at least 1")
        self.seed = int(seed)
        self.knobs = knobs or DEFAULT_KNOBS
        self.location_code = int(location_code)
        self.start = month_start(as_date(start_month))
        self.months = int(months)
        self.end = add_months(self.start, self.months)  # first day after the window
        self.n_days = (self.end - self.start).days
        self.niches: tuple[str, ...] = NICHE_NAMES[:niches]
        self._ranking_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        self._day_cache: dict[tuple[int, int], np.ndarray] = {}
        self._build(searches_per_niche, established_sites, new_sites)

    # ------------------------------------------------------------------ randomness

    def _rng(self, *keys: int) -> np.random.Generator:
        return np.random.default_rng(np.random.SeedSequence([self.seed, *[int(k) for k in keys]]))

    # ------------------------------------------------------------------ build

    def _build(self, searches_per_niche: int, established: int, new: int) -> None:
        k = self.knobs
        rng = self._rng(_SALT_BUILD)
        n_niches = len(self.niches)
        knots = self.months + 1

        # Searches.
        searches: list[dict[str, Any]] = []
        for niche_index, niche in enumerate(self.niches):
            for j in range(searches_per_niche):
                template = SEARCH_TEMPLATES[j % len(SEARCH_TEMPLATES)]
                text = template.format(n=niche)
                if j >= len(SEARCH_TEMPLATES):
                    text = f"{text} {j // len(SEARCH_TEMPLATES) + 1}"
                searches.append({"text": text, "niche": niche, "niche_index": niche_index})
        n_searches = len(searches)
        volumes = np.maximum(1, np.round(np.exp(rng.normal(math.log(90.0), 1.2, size=n_searches)))).astype(int)
        values = rng.normal(0.0, 1.0, size=n_searches)
        search_niche = np.array([item["niche_index"] for item in searches])

        # Sites.
        n_general = int(round(established * k.generalist_share))
        n_niche_sites = established - n_general
        n_sites = established + new
        kinds: list[str] = []
        homes: list[int] = []  # -1 for generalists
        for i in range(n_niche_sites):
            kinds.append("established")
            homes.append(i % n_niches)
        for _ in range(n_general):
            kinds.append("generalist")
            homes.append(-1)
        for i in range(new):
            kinds.append("new")
            homes.append(i % n_niches)
        home = np.array(homes)
        is_new = np.array([kind == "new" for kind in kinds])
        is_general = np.array([kind == "generalist" for kind in kinds])
        self._kinds = kinds
        self._home = home
        self._is_new = is_new

        names = self._site_names(kinds, homes, rng)
        start_day = self.start

        # Global strength knots: established drift slowly, new sites sit at the untrusted base.
        base = rng.normal(0.0, k.strength_sd, size=n_sites)
        base = np.where(is_general, base + k.generalist_boost, base)
        steps = rng.normal(0.0, k.strength_drift_sd_per_month, size=(n_sites, knots))
        steps[:, 0] = 0.0
        s_knots = base[:, None] + np.cumsum(steps, axis=1)
        s_knots[is_new] = k.new_base
        self._s_knots = s_knots

        # Topical focus in the home niche (generalists 0).
        focus = rng.normal(0.0, k.focus_sd, size=n_sites)
        new_focus = rng.normal(k.new_focus_mean, k.new_focus_sd, size=n_sites)
        focus = np.where(is_new, new_focus, focus)
        focus = np.where(is_general, 0.0, focus)
        self._focus = focus

        # Content match and participation. NaN means the site has no page on the search.
        z = (base - base[~is_new].mean()) / max(base[~is_new].std(), 1e-9)
        raw_m = rng.normal(0.0, 1.0, size=(n_sites, n_searches))
        part_draw = rng.random(size=(n_sites, n_searches))
        m = raw_m + k.difficulty_coupling * values[None, :] * z[:, None] + k.value_content_shift * values[None, :]
        same_niche = home[:, None] == search_niche[None, :]
        established_part = (~is_new & ~is_general)[:, None] & same_niche & (part_draw < k.participation)
        general_part = is_general[:, None] & np.ones((1, n_searches), dtype=bool)
        m = np.where(is_general[:, None], raw_m + k.generalist_content + k.value_content_shift * values[None, :], m)
        page_day = np.full((n_sites, n_searches), np.inf)
        page_day[established_part | general_part] = -1.0

        # Page random walk knots.
        walk = rng.normal(0.0, k.page_walk_sd_per_month, size=(n_sites, n_searches, knots))
        walk[:, :, 0] = rng.normal(0.0, k.page_sd, size=(n_sites, n_searches))
        self._r_knots = np.cumsum(walk, axis=2)

        # Day grid: knot index and fraction for linear interpolation.
        day_k = np.empty(self.n_days, dtype=int)
        day_frac = np.empty(self.n_days)
        for t in range(self.n_days):
            day = start_day + timedelta(days=t)
            first = month_start(day)
            length = (add_months(first, 1) - first).days
            day_k[t] = (first.year - start_day.year) * 12 + first.month - start_day.month
            day_frac[t] = (day.day - 1) / length
        self._day_k = day_k
        self._day_frac = day_frac

        self._m = m
        self._page_day = page_day
        self._search_niche = search_niche

        self._trust_c = np.zeros(n_sites)
        self._trust_tau = np.ones(n_sites)
        self._index_day = np.full(n_sites, np.inf)

        # Difficulty from established competitors on day 0, terciles per niche.
        difficulty = np.empty(n_searches)
        for q in range(n_searches):
            cand = np.flatnonzero(page_day[:, q] < 0)
            scores = np.sort(self._score(cand, q, 0))[::-1]
            difficulty[q] = scores[min(9, len(scores) - 1)]
        terciles = [""] * n_searches
        for niche_index in range(n_niches):
            idx = np.flatnonzero(search_niche == niche_index)
            order = idx[np.lexsort((idx, difficulty[idx]))]
            for rank, q in enumerate(order):
                terciles[q] = TERCILES[min(2, rank * 3 // len(order))]
        self.searches: tuple[SearchInfo, ...] = tuple(
            SearchInfo(q, searches[q]["text"], searches[q]["niche"], int(volumes[q]), float(values[q]),
                       float(difficulty[q]), terciles[q])
            for q in range(n_searches)
        )
        self._search_by_text = {info.text: info.index for info in self.searches}

        # New sites: launch, footprint, trust, pages, targets.
        registered: list[date] = []
        for i in range(n_sites):
            if is_new[i]:
                registered.append(start_day)  # replaced below
            else:
                registered.append(start_day - timedelta(days=int(rng.integers(2 * 365, 20 * 365))))
        new_info: dict[str, NewSiteInfo] = {}
        new_indices = np.flatnonzero(is_new)
        if len(new_indices):
            earliest = min(180, self.n_days // 3)
            latest = self.n_days - 100
            if latest < earliest:
                raise ValueError(
                    f"months={self.months} is too short. New sites need a launch date and a day 90 inside the window."
                )
        for i in new_indices:
            niche_index = int(home[i])
            launch_t = int(rng.integers(earliest, latest + 1))
            launch = start_day + timedelta(days=launch_t)
            reg = launch - timedelta(days=int(rng.integers(3, 121)))
            registered[i] = reg
            alive = bool(rng.random() >= k.dead_share)
            quality = float(rng.normal())
            links = int(round(math.exp(rng.normal(math.log(15.0), 1.2))))
            brand = int(round(math.exp(rng.normal(math.log(40.0), 1.3))))
            reviews = 0 if rng.random() < 0.2 else int(round(math.exp(rng.normal(math.log(12.0), 1.4))))
            rating = None if reviews == 0 else float(np.clip(np.round(rng.normal(4.4, 0.4), 1), 1.0, 5.0))
            planned = int(round(math.exp(rng.normal(math.log(10.0), 0.8))))
            press = bool(rng.random() < 0.3)
            pages = int(rng.integers(8, 41))
            z_links = (math.log1p(links) - math.log1p(15.0)) / 1.2
            z_brand = (math.log1p(brand) - math.log1p(40.0)) / 1.3
            z_reviews = (math.log1p(reviews) - 2.0) / 1.4
            z_plan = (math.log1p(planned) - math.log1p(10.0)) / 0.8
            linear = (k.trust_mean + 0.35 * z_links + 0.25 * z_brand + 0.20 * z_reviews + 0.25 * quality
                      + 0.15 * z_plan + 0.10 * press + rng.normal(0.0, k.trust_noise_sd))
            ceiling = float(math.log1p(math.exp(linear)))  # softplus keeps the ceiling positive
            tau = float(k.trust_tau_median_days * math.exp(-0.15 * z_plan + rng.normal(0.0, k.trust_tau_log_sd)))
            delay = 1 + int(round(math.exp(rng.normal(math.log(k.index_delay_median_days), k.index_delay_log_sd))))
            delay = min(delay, 60)
            index_t = launch_t + delay
            # Targets: a third easy, a third medium, a third hard (remainder to easy, then medium).
            niche_qs = np.flatnonzero(search_niche == niche_index)
            want = min(k.targets_per_site, len(niche_qs))
            per = [want // 3 + (1 if r < want % 3 else 0) for r in range(3)]
            targets: list[int] = []
            for tercile, count in zip(TERCILES, per):
                pool = [q for q in niche_qs if terciles[q] == tercile]
                chosen = rng.choice(pool, size=min(count, len(pool)), replace=False) if pool else []
                targets.extend(int(q) for q in chosen)
            target_set = set(targets)
            for q in niche_qs:
                q = int(q)
                if q in target_set:
                    m[i, q] = rng.normal(k.target_content_mean + 0.5 * quality, k.target_content_sd)
                    extra = rng.geometric(1.0 / k.page_index_extra_mean_days) - 1
                elif rng.random() < k.nontarget_page_share:
                    m[i, q] = rng.normal(k.nontarget_content_mean + 0.3 * quality, 1.0)
                    extra = rng.geometric(1.0 / (3 * k.page_index_extra_mean_days)) - 1
                else:
                    continue
                if alive:
                    page_day[i, q] = index_t + extra
            self._trust_c[i] = ceiling
            self._trust_tau[i] = tau
            if alive:
                self._index_day[i] = index_t
            new_info[names[i]] = NewSiteInfo(
                site=names[i], niche=self.niches[niche_index], registered=reg, launch_date=launch,
                index_date=start_day + timedelta(days=index_t) if alive else None, alive=alive,
                trust_ceiling=ceiling, trust_tau_days=tau, content_quality=quality, referring_domains=links,
                brand_search_volume=brand, review_count=reviews, review_rating=rating, links_planned=planned,
                press_planned=press, pages_at_launch=pages,
                targets=tuple(self.searches[q].text for q in sorted(targets)),
            )
        self.sites: tuple[SiteInfo, ...] = tuple(
            SiteInfo(i, names[i], kinds[i], self.niches[homes[i]] if homes[i] >= 0 else None, registered[i],
                     names[i].split(".")[0].replace("-", " ").title())
            for i in range(n_sites)
        )
        self._site_by_name = {info.site: info.index for info in self.sites}
        self.new_sites: dict[str, NewSiteInfo] = new_info
        self._candidates = [np.flatnonzero(np.isfinite(page_day[:, q])) for q in range(n_searches)]
        established_strength = s_knots[~is_new, 0]
        self._list_threshold = float(np.quantile(established_strength, k.strength_list_quantile))
        widest = max(len(c) for c in self._candidates)
        self._unrelated = tuple(f"offtopic-{j:04d}.{TLD}" for j in range(max(3 * SNAPSHOT_DEPTH, widest)))

    def _site_names(self, kinds: Sequence[str], homes: Sequence[int], rng: np.random.Generator) -> list[str]:
        names: list[str] = []
        used: set[str] = set()
        for i, kind in enumerate(kinds):
            if kind == "generalist":
                stem = f"{NAME_PARTS[i % len(NAME_PARTS)]}-listings-{i:03d}"
            else:
                niche = self.niches[homes[i]]
                part = NAME_PARTS[int(rng.integers(len(NAME_PARTS)))]
                prefix = "new-" if kind == "new" else ""
                stem = f"{prefix}{part}-{niche}-{i:03d}"
            name = f"{stem}.{TLD}"
            if name in used:  # cannot happen with the index suffix, kept as a guard
                raise RuntimeError(f"duplicate synthetic site name {name}")
            used.add(name)
            names.append(name)
        return names

    # ------------------------------------------------------------------ lookups

    def day_index(self, day: date | datetime | str) -> int:
        """Days since the start of the window. Raises ValueError outside the window."""
        value = as_date(day)
        t = (value - self.start).days
        if not 0 <= t < self.n_days:
            raise ValueError(f"{value} is outside the synthetic window {self.start} to {self.end - timedelta(days=1)}")
        return t

    def search_index(self, search: str) -> int:
        try:
            return self._search_by_text[search]
        except KeyError:
            raise KeyError(f"the synthetic world has no search {search!r}") from None

    def site_index(self, site: str) -> int:
        try:
            return self._site_by_name[site]
        except KeyError:
            raise KeyError(f"the synthetic world has no site {site!r}") from None

    def searches_in(self, niche: str) -> list[str]:
        return [info.text for info in self.searches if info.niche == niche]

    def target_searches(self, site: str) -> tuple[str, ...]:
        return self._new(site).targets

    def _new(self, site: str) -> NewSiteInfo:
        if site not in self.new_sites:
            raise KeyError(f"{site!r} is not a new site of this world")
        return self.new_sites[site]

    def url_of(self, site: str, search: str) -> str:
        """The synthetic URL of the site's page for the search."""
        info = self.sites[self.site_index(site)]
        slug = "-".join(search.split())
        if info.kind == "generalist":
            niche = self.searches[self.search_index(search)].niche
            return f"https://www.{site}/{niche}/{slug}"
        return f"https://www.{site}/services/{slug}"

    # ------------------------------------------------------------------ scores and truth

    def _strength(self, idx: np.ndarray, t: int) -> np.ndarray:
        k0 = self._day_k[t]
        f = self._day_frac[t]
        upper = min(k0 + 1, self._s_knots.shape[1] - 1)
        return self._s_knots[idx, k0] * (1 - f) + self._s_knots[idx, upper] * f

    def _trust(self, idx: np.ndarray, t: int) -> np.ndarray:
        elapsed = t - self._index_day[idx]
        grown = self._trust_c[idx] * (1.0 - np.exp(-np.maximum(elapsed, 0.0) / self._trust_tau[idx]))
        return np.where(np.isfinite(elapsed) & (elapsed >= 0), grown, 0.0)

    def _score(self, idx: np.ndarray, q: int, t: int) -> np.ndarray:
        k0 = self._day_k[t]
        f = self._day_frac[t]
        upper = min(k0 + 1, self._r_knots.shape[2] - 1)
        walk = self._r_knots[idx, q, k0] * (1 - f) + self._r_knots[idx, q, upper] * f
        return (self.knobs.kappa * self._m[idx, q] + self._strength(idx, t) + self._focus[idx]
                + self._trust(idx, t) + walk)

    def _ranking(self, q: int, t: int) -> tuple[np.ndarray, np.ndarray]:
        """(site indices best first, their true scores) over pages indexed on day t."""
        key = (q, t)
        cached = self._ranking_cache.get(key)
        if cached is not None:
            return cached
        cand = self._candidates[q]
        cand = cand[self._page_day[cand, q] <= t]
        scores = self._score(cand, q, t)
        order = np.lexsort((cand, -scores))
        result = (cand[order], scores[order])
        if len(self._ranking_cache) > 200_000:
            self._ranking_cache.clear()
        self._ranking_cache[key] = result
        return result

    def true_ranking(self, search: str, day: date | datetime | str) -> tuple[str, ...]:
        """Every indexed page's site for the search on the day, best first. SYNTHETIC truth."""
        order, _ = self._ranking(self.search_index(search), self.day_index(day))
        return tuple(self.sites[i].site for i in order)

    def true_position(self, site: str, search: str, day: date | datetime | str) -> int:
        """SYNTHETIC ground truth: 1 to 50, or NT50 when beyond 50, not indexed or without a page."""
        s = self.site_index(site)
        order, _ = self._ranking(self.search_index(search), self.day_index(day))
        hits = np.flatnonzero(order == s)
        if not len(hits):
            return NT50
        rank = int(hits[0]) + 1
        return rank if rank <= TOP_DEPTH else NT50

    def true_trust(self, site: str, day: date | datetime | str) -> float | None:
        """A new site's SYNTHETIC trust on the day. None for an established site, which has no trust gap."""
        s = self.site_index(site)
        if not self._is_new[s]:
            return None
        return float(self._trust(np.array([s]), self.day_index(day))[0])

    def trust_ceiling(self, site: str) -> float:
        """The planted trust ceiling of a new site. SYNTHETIC truth, for checking a model recovers it."""
        return self._new(site).trust_ceiling

    def trust_ceilings(self) -> dict[str, float]:
        return {name: info.trust_ceiling for name, info in sorted(self.new_sites.items())}

    def day90_truth(
        self, sites: Sequence[str] | None = None, searches: Sequence[str] | None = None
    ) -> list[TruthCell]:
        """SYNTHETIC day-90 positions. Default: every new site on its own target searches."""
        chosen = sorted(self.new_sites) if sites is None else list(sites)
        rows: list[TruthCell] = []
        for name in chosen:
            info = self._new(name)
            outcome = info.launch_date + timedelta(days=90)
            for text in (info.targets if searches is None else searches):
                q = self.search_index(text)
                rows.append(TruthCell(
                    site=name, search=text, niche=self.searches[q].niche, tercile=self.searches[q].tercile,
                    is_target=text in info.targets, alive=info.alive, launch_date=info.launch_date,
                    outcome_date=outcome, position=self.true_position(name, text, outcome),
                ))
        return rows

    # ------------------------------------------------------------------ observations

    def _day_shock(self, q: int, t: int) -> np.ndarray:
        """One score shock per site for the search on the UTC day, shared by every pull of that day."""
        key = (q, t)
        shock = self._day_cache.get(key)
        if shock is None:
            shock = self._rng(_SALT_DAY, q, t).normal(0.0, self.knobs.day_sd, size=len(self.sites))
            if len(self._day_cache) > 200_000:
                self._day_cache.clear()
            self._day_cache[key] = shock
        return shock

    def observed_order(self, q: int, t: int, rng: np.random.Generator | None, *, country: bool = False) -> np.ndarray:
        """Site indices best first as one pull would see them. rng None gives the day's list with no pull noise."""
        order, scores = self._ranking(q, t)
        noisy = scores + self._day_shock(q, t)[order]
        if country:
            noisy = noisy + self._rng(_SALT_SNAPSHOT, 0, q).normal(0.0, self.knobs.country_sd, size=len(self.sites))[order]
        if rng is None:
            return order[np.lexsort((order, -noisy))]
        noisy = noisy + rng.normal(0.0, self.knobs.pull_sd, size=len(order))
        first = order[np.lexsort((order, -noisy))]
        n = len(first)
        keys = np.arange(n, dtype=float)
        jumps = rng.random(n) < self.knobs.jump_rate
        span = np.maximum(self.knobs.jump_span_min, self.knobs.jump_span_share * (keys[jumps] + 1))
        keys[jumps] = keys[jumps] + rng.uniform(-1.0, 1.0, size=int(jumps.sum())) * span
        return first[np.lexsort((np.arange(n), keys))]

    def _hits(self, sites: Sequence[str], search: str, repaired: bool) -> tuple[OrganicHit, ...]:
        hits: list[OrganicHit] = []
        for rank, site in enumerate(sites, start=1):
            if site in self._site_by_name:
                url = self.url_of(site, search)
                title = f"{search.title()} | {self.sites[self._site_by_name[site]].brand}"
            else:
                url = f"https://www.{site}/{'-'.join(search.split())}-deals"
                title = f"{search.title()} deals and more"
            hits.append(OrganicHit(rank=rank, url=url, domain=f"www.{site}", site=site, title=title,
                                   rank_absolute=rank, repaired=repaired))
        return tuple(hits)

    def _poison(self, sites: list[str], rng: np.random.Generator) -> list[str]:
        """Replace most slots with unrelated sites, keeping the rest of the true list in order."""
        share = rng.uniform(self.knobs.poison_replace_low, self.knobs.poison_replace_high)
        replace_slot = rng.random(len(sites)) < share
        strangers = list(rng.choice(len(self._unrelated), size=len(sites), replace=False))
        kept = iter(sites)
        out: list[str] = []
        for slot in replace_slot:
            out.append(self._unrelated[strangers.pop()] if slot else next(kept))
        return out

    def monthly_snapshots(self, month: date | str) -> list[Pull]:
        """One country-level historical page per search for the month, up to 100 deep. SYNTHETIC.

        Shaped like the Labs historical pages the plan buys: one snapshot, one
        pull's noise, no clean reading. The snapshot day is inside the month.
        Poisoned pages occur at `poison_rate` and are not repaired, as in Labs.
        """
        first = month_start(as_date(month))
        if not self.start <= first < self.end:
            raise ValueError(f"{first} is outside the synthetic window")
        length = (add_months(first, 1) - first).days
        month_key = first.year * 12 + first.month
        pulls: list[Pull] = []
        for info in self.searches:
            rng = self._rng(_SALT_SNAPSHOT, 1, info.index, month_key)
            day = first + timedelta(days=int(rng.integers(0, length)))
            moment = datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
            t = self.day_index(day)
            order = self.observed_order(info.index, t, rng, country=True)
            sites = [self.sites[i].site for i in order[:SNAPSHOT_DEPTH]]
            poisoned = bool(rng.random() < self.knobs.poison_rate)
            if poisoned:
                sites = self._poison(sites, rng)
            spec = PullSpec(search=info.text, location_code=COUNTRY_LOCATION_CODE, depth=SNAPSHOT_DEPTH)
            stamp = utc_iso(moment)
            pull_id = "synthetic-hist-" + hashlib.sha256(f"{self.seed}|{info.index}|{month_key}".encode()).hexdigest()[:20]
            pulls.append(Pull(
                pull_id=pull_id, provider=PROVIDER, endpoint=ENDPOINT_HISTORICAL, spec=spec, requested_at=stamp,
                completed_at=stamp, ok=True, status_code=STATUS_OK,
                status_message=STATUS_TEXT + " Single monthly snapshot, not a clean reading.",
                task_id=pull_id, provider_datetime=stamp, cost=0.0, hits=self._hits(sites, info.text, False),
            ))
        return pulls

    # ------------------------------------------------------------------ dated lists

    def strength_list(self, month: date | str) -> dict[str, Dated]:
        """Per-site strength scores as published in the month. SYNTHETIC.

        Published on `strength_publish_day` of the month, from data
        `strength_data_lag_months` earlier (the 15th of that month). The
        snapshot date is the publication date, so the list is knowable from
        then on. Sites weaker than the listing threshold are absent, which is
        how brand-new sites look in real top lists. Absent is missing, never 0.
        """
        k = self.knobs
        first = month_start(as_date(month))
        data_day = add_months(first, -k.strength_data_lag_months) + timedelta(days=14)
        published = first + timedelta(days=k.strength_publish_day - 1)
        if not self.start <= data_day or not first < self.end:
            raise ValueError(f"no synthetic strength list is published in {first:%Y-%m}")
        t = self.day_index(data_day)
        idx = np.arange(len(self.sites))
        strength = self._strength(idx, t) + self._trust(idx, t)
        noise = self._rng(_SALT_LIST, first.year * 12 + first.month).normal(0.0, k.strength_list_noise_sd, size=len(idx))
        out: dict[str, Dated] = {}
        source = f"synthetic-strength-list:{first:%Y-%m}"
        for i in idx:
            if strength[i] >= self._list_threshold:
                out[self.sites[i].site] = Dated(round(float(strength[i] + noise[i]), 3), source, published)
        return dict(sorted(out.items()))

    def newly_registered(self, day: date | str) -> list[Dated]:
        """Domains registered on the day, as a daily NRD list published the next day. SYNTHETIC.

        Holds the world's new sites registered that day and unrelated decoy
        registrations that never launch.
        """
        value = as_date(day)
        published = value + timedelta(days=1)
        names = [info.site for info in self.new_sites.values() if info.registered == value]
        key = (value - date(1970, 1, 1)).days
        decoys = int(self._rng(_SALT_NRD, key).poisson(self.knobs.nrd_decoys_per_day))
        names += [f"parked-{key}-{j}.{TLD}" for j in range(decoys)]
        source = f"synthetic-nrd:{value.isoformat()}"
        return [Dated(name, source, published) for name in sorted(names)]

    def traffic_curve(self, site: str) -> list[Dated]:
        """Monthly estimated Google visits of a site. SYNTHETIC.

        Value: {"month": "YYYY-MM", "etv": float or None}. A month below the
        detection floor is None (missing, not zero), the way cheap traffic data
        can be blind to tiny sites. Month M is published `traffic_publication_lag_days`
        after it ends. New sites start in their launch month.
        """
        s = self.site_index(site)
        k = self.knobs
        first = month_start(self._new(site).launch_date) if self._is_new[s] else self.start
        out: list[Dated] = []
        month = first
        while month < self.end:
            t = self.day_index(month + timedelta(days=14))
            visits = 0.0
            for info in self.searches:
                if not np.isfinite(self._page_day[s, info.index]):
                    continue
                order, _ = self._ranking(info.index, t)
                where = np.flatnonzero(order == s)
                if len(where):
                    visits += info.volume * ctr_at(int(where[0]) + 1)
            etv = round(visits, 2) if visits >= k.traffic_floor_visits else None
            published = add_months(month, 1) + timedelta(days=k.traffic_publication_lag_days)
            out.append(Dated({"month": f"{month:%Y-%m}", "etv": etv}, f"synthetic-traffic:{site}", published))
            month = add_months(month, 1)
        return out

    def search_console_rows(self, site: str, as_of: date | str) -> list[dict[str, Any]]:
        """Daily Search Console rows of a site known on `as_of`. SYNTHETIC.

        Rows exist only from the day a page is indexed, only up to as_of minus
        `gsc_lag_days`, only for impressions above 0, and never for queries
        whose monthly volume is under `gsc_anon_monthly_volume` (anonymized).
        Positions are averages of the day's observed position, with a decimal.
        """
        s = self.site_index(site)
        k = self.knobs
        limit = as_date(as_of) - timedelta(days=k.gsc_lag_days)
        last_t = min((limit - self.start).days, self.n_days - 1)
        rows: list[dict[str, Any]] = []
        for info in self.searches:
            q = info.index
            first_t = self._page_day[s, q]
            if not np.isfinite(first_t) or info.volume < k.gsc_anon_monthly_volume:
                continue
            page = self.url_of(site, info.text)
            for t in range(max(0, int(first_t)), last_t + 1):
                observed = self.observed_order(q, t, None)
                where = np.flatnonzero(observed == s)
                if not len(where):
                    continue
                position = int(where[0]) + 1
                chance = view_chance(position)
                if chance <= 0:
                    continue
                rng = self._rng(_SALT_GSC, s, q, t)
                impressions = int(rng.poisson(info.volume / 30.0 * chance))
                if impressions == 0:
                    continue
                clicks = int(rng.binomial(impressions, ctr_at(position)))
                shown = max(1.0, round(position + rng.normal(0.0, 0.3), 1))
                rows.append({
                    "data_date": (self.start + timedelta(days=t)).isoformat(), "query": info.text, "page": page,
                    "clicks": clicks, "impressions": impressions, "position": shown,
                })
        rows.sort(key=lambda row: (row["data_date"], row["query"]))
        return rows

    def agency_form(self, site: str) -> dict[str, Any]:
        """The agency form for a new site as a plain dict (the answers layout of agency_form.load_form). SYNTHETIC.

        Filled in 7 days before launch. Some optional answers are missing (None),
        the way an agency does not always know them.
        """
        info = self._new(site)
        s = self.site_index(site)
        rng = self._rng(_SALT_FORM, s)
        filled = info.launch_date - timedelta(days=7)

        def maybe(value: Any) -> Any:
            return None if rng.random() < self.knobs.form_missing_share else value

        reviews = maybe(info.review_count)
        brand = maybe(info.brand_search_volume)
        links = maybe(info.referring_domains)
        footprint = {
            "review_count": reviews,
            "review_rating": info.review_rating if reviews else None,
            "review_platform": "synthetic reviews" if reviews is not None else None,
            "reviews_date": filled.isoformat() if reviews is not None else None,
            "brand_search_volume": brand,
            "brand_search_volume_source": "synthetic" if brand is not None else None,
            "brand_search_volume_date": filled.isoformat() if brand is not None else None,
            "referring_domains": links,
            "referring_domains_source": "synthetic" if links is not None else None,
            "referring_domains_date": filled.isoformat() if links is not None else None,
            "has_old_site": False,
            "business_listing": maybe(bool(info.review_count > 0)),
        }
        plan = {
            "planned_launch_date": info.launch_date.isoformat(),
            "links_planned_count": maybe(info.links_planned),
            "press_planned": maybe(info.press_planned),
            "pages_at_launch": info.pages_at_launch,
        }
        return {
            "site": site,
            "niche": info.niche,
            "brand_names": [self.sites[s].brand],
            "filled_by": "SYNTHETIC world generator",
            "filled_on": filled.isoformat(),
            "footprint": footprint,
            "launch_plan": plan,
        }


@dataclass
class SyntheticSource:
    """A `types.SerpSource` over a `World`. Every pull is SYNTHETIC and costs nothing.

    `fetch` reads `clock()` once per spec. A pull is deterministic for (search,
    UTC second, how many pulls of that search this source served before). A
    spec the world cannot answer (unknown search, other location, day outside
    the window) comes back as a failed pull, never as an exception.
    """

    world: World
    clock: Callable[[], datetime]
    name: str = PROVIDER
    events: list[PullEvent] = field(default_factory=list, init=False)
    _counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def estimate_cost(self, count: int, depth: int = TOP_DEPTH) -> float:
        return 0.0

    def fetch(self, specs: Sequence[PullSpec]) -> list[Pull]:
        return [self._one(spec) for spec in specs]

    def _failed(self, spec: PullSpec, stamp: str, pull_id: str, code: int, message: str) -> Pull:
        return Pull(pull_id=pull_id, provider=PROVIDER, endpoint=ENDPOINT_LIVE, spec=spec, requested_at=stamp,
                    completed_at=stamp, ok=False, status_code=code, status_message=f"{STATUS_TEXT} {message}",
                    task_id=pull_id, provider_datetime=stamp, cost=0.0)

    def _one(self, spec: PullSpec) -> Pull:
        world = self.world
        moment = _utc(self.clock())
        stamp = utc_iso(moment)
        count = self._counts.get(spec.search, 0)
        self._counts[spec.search] = count + 1
        epoch = int(moment.timestamp())
        pull_id = "synthetic-" + hashlib.sha256(
            f"{world.seed}|{spec.search}|{spec.location_code}|{epoch}|{count}".encode()
        ).hexdigest()[:24]
        day = moment.date()
        if spec.search not in world._search_by_text:
            return self._failed(spec, stamp, pull_id, STATUS_INVALID, "Unknown search for this world.")
        if spec.location_code != world.location_code:
            return self._failed(spec, stamp, pull_id, STATUS_INVALID,
                                f"This world serves location {world.location_code} only.")
        if spec.depth < 1:
            return self._failed(spec, stamp, pull_id, STATUS_INVALID, "Depth must be at least 1.")
        try:
            t = world.day_index(day)
        except ValueError:
            return self._failed(spec, stamp, pull_id, STATUS_INVALID, "The day is outside the simulated window.")
        q = world.search_index(spec.search)
        rng = world._rng(_SALT_PULL, q, epoch, count, _string_key(spec.search))
        u_fail, u_spell, u_poison, u_repair, u_partial = rng.random(5)
        k = world.knobs
        failed = bool(u_fail < k.fail_rate)
        spell = (not failed) and bool(u_spell < k.spell_rate)
        poisoned = (not failed) and bool(u_poison < k.poison_rate)
        repaired = (not failed) and (not poisoned) and bool(u_repair < k.repaired_rate)
        partial = (not failed) and bool(u_partial < k.partial_rate)
        served = q
        if spell:
            peers = [info.index for info in world.searches if info.niche == world.searches[q].niche and info.index != q]
            served = int(rng.choice(peers)) if peers else q
        self.events.append(PullEvent(pull_id, spec.search, day, failed, partial, poisoned, repaired, spell,
                                     None if failed else world.searches[served].text))
        if failed:
            return self._failed(spec, stamp, pull_id, STATUS_FAILED, "Simulated task failure. No results.")
        order = world.observed_order(served, t, rng)
        length = min(spec.depth, len(order))
        sites = [world.sites[i].site for i in order[:length]]
        if poisoned:
            sites = world._poison(sites, rng)
        if partial:
            sites = sites[: int(rng.integers(min(5, length), max(min(5, length) + 1, int(0.8 * length))))]
        message = STATUS_TEXT
        if spell:
            message += " Google answered a corrected spelling."
        if partial:
            message += " Partial results."
        return Pull(
            pull_id=pull_id, provider=PROVIDER, endpoint=ENDPOINT_LIVE, spec=spec, requested_at=stamp,
            completed_at=stamp, ok=True, status_code=STATUS_PARTIAL if partial else STATUS_OK,
            status_message=message, task_id=pull_id, provider_datetime=stamp, cost=0.0, partial=partial,
            spell_corrected=spell, repaired=repaired, hits=world._hits(sites, world.searches[served].text, repaired),
        )


def fixed_clock(moment: datetime, step: timedelta = timedelta(0)) -> Callable[[], datetime]:
    """A clock for tests: returns `moment`, then moves forward by `step` on every call. No real time is read."""
    state = {"now": _utc(moment)}

    def clock() -> datetime:
        current = state["now"]
        state["now"] = current + step
        return current

    return clock


def day90_nt50_share(world: World, *, alive_only: bool = False) -> float | None:
    """Share of SYNTHETIC day-90 target cells beyond position 50. None when there are no cells."""
    cells = [cell for cell in world.day90_truth() if cell.alive or not alive_only]
    if not cells:
        return None
    return sum(cell.position == NT50 for cell in cells) / len(cells)


__all__ = [
    "COUNTRY_LOCATION_CODE", "DEFAULT_CITY_LOCATION_CODE", "DEFAULT_KNOBS", "NewSiteInfo", "PullEvent",
    "SearchInfo", "SiteInfo", "SynthKnobs", "SyntheticSource", "TruthCell", "World", "ctr_at",
    "day90_nt50_share", "fixed_clock", "view_chance",
]
