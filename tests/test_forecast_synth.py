"""The synthetic world: determinism, valid truth, tuned outcome mix, noise shape, dated lists, labels.

Everything checked here is SYNTHETIC. No number in this file is accuracy.
"""

from __future__ import annotations

import statistics
import time as clock_time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from aeo.ranksim.forecast.asof import AsOfGuard, LeakError, add_months
from aeo.ranksim.forecast.config import NT50, TOP_DEPTH
from aeo.ranksim.forecast.stats import design_effect
from aeo.ranksim.forecast.synth import (
    COUNTRY_LOCATION_CODE,
    DEFAULT_KNOBS,
    SyntheticSource,
    World,
    day90_nt50_share,
    fixed_clock,
)
from aeo.ranksim.forecast.types import SYNTHETIC, Pull, PullSpec, SerpSource

PULL_DAY = datetime(2025, 9, 1, 10, 0, tzinfo=timezone.utc)


def small_world(seed: int = 11, **overrides) -> World:
    settings = dict(niches=1, searches_per_niche=9, established_sites=120, new_sites=6, months=12)
    settings.update(overrides)
    return World(seed, **settings)


def spec_for(world: World, search: str) -> PullSpec:
    return PullSpec(search=search, location_code=world.location_code)


def band_of(position: int) -> str:
    return "1-3" if position <= 3 else "4-10" if position <= 10 else "11-20" if position <= 20 else "21-50"


def test_world_is_deterministic_for_a_seed_and_differs_across_seeds():
    first, second, other = small_world(5), small_world(5), small_world(6)
    assert first.new_sites == second.new_sites
    assert first.searches == second.searches
    assert first.day90_truth() == second.day90_truth()
    day = date(2025, 8, 3)
    assert [first.true_ranking(s.text, day) for s in first.searches] == [
        second.true_ranking(s.text, day) for s in second.searches
    ]
    pulls_a = SyntheticSource(first, fixed_clock(PULL_DAY)).fetch([spec_for(first, s.text) for s in first.searches])
    pulls_b = SyntheticSource(second, fixed_clock(PULL_DAY)).fetch([spec_for(second, s.text) for s in second.searches])
    assert pulls_a == pulls_b
    assert first.trust_ceilings() != other.trust_ceilings()
    assert [first.true_ranking(s.text, day) for s in first.searches] != [
        other.true_ranking(s.text, day) for s in other.searches
    ]


def test_true_positions_are_a_valid_ranking():
    world = small_world()
    for day in (date(2025, 1, 1), date(2025, 7, 15), date(2025, 12, 31)):
        for info in world.searches:
            ranking = world.true_ranking(info.text, day)
            assert len(ranking) == len(set(ranking))
            positions = [world.true_position(site.site, info.text, day) for site in world.sites]
            inside = sorted(p for p in positions if p != NT50)
            assert inside == list(range(1, min(TOP_DEPTH, len(ranking)) + 1))
            for rank, site in enumerate(ranking[:TOP_DEPTH], start=1):
                assert world.true_position(site, info.text, day) == rank
            for site in ranking[TOP_DEPTH:]:
                assert world.true_position(site, info.text, day) == NT50


def test_day90_nt50_share_is_near_the_plan_figure_for_default_knobs():
    """Stated band: the mean over seeds 0 to 9 is in 0.55 to 0.71, each seed in 0.40 to 0.80 (plan figure about 0.63)."""
    shares = [day90_nt50_share(World(seed)) for seed in range(10)]
    assert all(share is not None and 0.40 <= share <= 0.80 for share in shares), shares
    assert 0.55 <= statistics.mean(shares) <= 0.71, shares
    by_tercile: dict[str, list[bool]] = {"easy": [], "medium": [], "hard": []}
    page_one = []
    for seed in range(5):
        for cell in World(seed).day90_truth():
            by_tercile[cell.tercile].append(cell.position == NT50)
            page_one.append(cell.position <= 10)
            assert cell.label == SYNTHETIC and cell.is_target
    easy, medium, hard = (statistics.mean(by_tercile[name]) for name in ("easy", "medium", "hard"))
    assert easy < medium < hard
    assert 0.03 <= statistics.mean(page_one) <= 0.25
    counts = {name: len(values) for name, values in by_tercile.items()}
    assert counts["easy"] >= counts["hard"] and counts["hard"] * 4 >= sum(counts.values())


def test_trust_and_positions_improve_over_time_and_dead_sites_never_rank():
    world = World(3, new_sites=60)
    live = [info for info in world.new_sites.values() if info.alive]
    dead = [info for info in world.new_sites.values() if not info.alive]
    assert live and dead
    trust = {day: [] for day in (14, 45, 90, 99)}
    positions = {day: [] for day in (14, 45, 90)}
    for info in live:
        for day in trust:
            trust[day].append(world.true_trust(info.site, info.launch_date + timedelta(days=day)))
        for day in positions:
            when = info.launch_date + timedelta(days=day)
            positions[day].extend(world.true_position(info.site, search, when) for search in info.targets)
        assert world.true_trust(info.site, info.launch_date - timedelta(days=1)) == 0.0
        assert world.true_trust(info.site, info.launch_date + timedelta(days=90)) < info.trust_ceiling
    means = [statistics.mean(trust[day]) for day in sorted(trust)]
    assert means == sorted(means) and means[0] < means[-1]
    assert statistics.mean(positions[14]) > statistics.mean(positions[45]) > statistics.mean(positions[90])
    source = SyntheticSource(world, fixed_clock(datetime(2025, 1, 1, tzinfo=timezone.utc), timedelta(days=3)))
    for info in dead:
        assert info.index_date is None
        for day in range(0, world.n_days, 17):
            when = world.start + timedelta(days=day)
            assert all(world.true_position(info.site, s, when) == NT50 for s in world.searches_in(info.niche))
            assert world.true_trust(info.site, when) == 0.0
        assert world.search_console_rows(info.site, world.end - timedelta(days=1)) == []
        assert all(point.value["etv"] is None for point in world.traffic_curve(info.site))
    dead_sites = {info.site for info in dead}
    for pull in source.fetch([spec_for(world, s) for s in world.searches_in(dead[0].niche)] * 8):
        assert not dead_sites & {hit.site for hit in pull.hits}
    assert world.true_trust(world.sites[0].site, world.start) is None


def test_failure_modes_appear_at_about_the_configured_rates():
    knobs = replace(DEFAULT_KNOBS, poison_rate=0.10, partial_rate=0.06, fail_rate=0.05, spell_rate=0.04,
                    repaired_rate=0.08)
    world = small_world(knobs=knobs)
    source = SyntheticSource(world, fixed_clock(PULL_DAY, timedelta(seconds=61)))
    specs = [spec_for(world, info.text) for info in world.searches] * 400
    pulls = source.fetch(specs)
    events = source.events
    assert len(pulls) == len(events) == len(specs)
    n = len(events)
    served = [event for event in events if not event.failed]

    def close(count: int, total: int, rate: float) -> bool:
        spread = 4.5 * (total * rate * (1 - rate)) ** 0.5
        return abs(count - total * rate) <= spread

    assert close(sum(event.failed for event in events), n, knobs.fail_rate)
    assert close(sum(event.poisoned for event in served), len(served), knobs.poison_rate)
    assert close(sum(event.partial for event in served), len(served), knobs.partial_rate)
    assert close(sum(event.spell_corrected for event in served), len(served), knobs.spell_rate)
    assert close(sum(event.repaired for event in served), len(served), knobs.repaired_rate * (1 - knobs.poison_rate))
    for pull, event in zip(pulls, events):
        assert pull.pull_id == event.pull_id
        assert pull.ok is not event.failed
        if event.failed:
            assert pull.hits == () and pull.status_code == 50000
            continue
        assert pull.partial is event.partial and pull.spell_corrected is event.spell_corrected
        assert pull.status_code == (40106 if event.partial else 20000)
        if event.partial:
            assert len(pull.hits) < 0.8 * TOP_DEPTH
        if event.poisoned and not event.partial and not event.spell_corrected:
            truth = set(world.true_ranking(pull.spec.search, PULL_DAY.date())[:TOP_DEPTH])
            top = [hit.site for hit in pull.hits[:20]]
            assert sum(site in truth for site in top) / len(top) < 0.5
        if event.repaired:
            assert pull.repaired and all(hit.repaired for hit in pull.hits)
        ranks = [hit.rank for hit in pull.hits]
        assert ranks == list(range(1, len(ranks) + 1))
        assert len({hit.site for hit in pull.hits}) == len(pull.hits)


def test_source_is_a_serp_source_and_returns_one_pull_per_spec_in_order():
    world = small_world()
    source = SyntheticSource(world, fixed_clock(PULL_DAY, timedelta(minutes=2)))
    assert isinstance(source, SerpSource)
    assert source.name == "synthetic"
    assert source.estimate_cost(500) == 0.0 and source.estimate_cost(5, depth=100) == 0.0
    texts = [info.text for info in world.searches]
    specs = [
        spec_for(world, texts[2]),
        spec_for(world, "no such search"),
        PullSpec(search=texts[0], location_code=COUNTRY_LOCATION_CODE),
        spec_for(world, texts[1]),
        PullSpec(search=texts[3], location_code=world.location_code, depth=10),
    ]
    pulls = source.fetch(specs)
    assert len(pulls) == len(specs)
    assert [pull.spec for pull in pulls] == specs
    assert all(isinstance(pull, Pull) and pull.provider == "synthetic" and pull.cost == 0.0 for pull in pulls)
    assert pulls[1].ok is False and pulls[1].hits == () and pulls[1].status_code == 40501
    assert pulls[2].ok is False and "location" in pulls[2].status_message
    assert all("SYNTHETIC" in (pull.status_message or "") for pull in pulls)
    stamps = [pull.requested_at for pull in pulls]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    good = [pull for pull in (pulls[0], pulls[3], pulls[4]) if pull.ok]
    for pull in good:
        for hit in pull.hits:
            assert hit.url.startswith(f"https://www.{hit.site}/")
            assert hit.site.endswith(".example") and "smartcrew" not in hit.site
    if pulls[4].ok:
        assert len(pulls[4].hits) <= 10
    outside = SyntheticSource(world, fixed_clock(datetime(2030, 1, 1, tzinfo=timezone.utc))).fetch([specs[0]])
    assert outside[0].ok is False and outside[0].status_code == 40501
    naive = SyntheticSource(world, fixed_clock(PULL_DAY.replace(tzinfo=None))).fetch([specs[0]])
    assert naive[0].requested_at == "2025-09-01T10:00:00Z"
    assert source.fetch([]) == []


def test_pulls_are_deterministic_for_search_time_and_call_count():
    world = small_world()
    spec = spec_for(world, world.searches[0].text)
    first = SyntheticSource(world, fixed_clock(PULL_DAY)).fetch([spec, spec])
    again = SyntheticSource(world, fixed_clock(PULL_DAY)).fetch([spec, spec])
    assert first == again
    assert first[0].pull_id != first[1].pull_id
    later = SyntheticSource(world, fixed_clock(PULL_DAY + timedelta(seconds=1))).fetch([spec])
    assert later[0].pull_id != first[0].pull_id


def test_pull_day_comes_from_the_utc_instant_not_the_local_date():
    world = small_world()
    spec = spec_for(world, world.searches[0].text)
    local = datetime(2025, 9, 1, 22, 0, tzinfo=timezone(timedelta(hours=-5)))
    from_local = SyntheticSource(world, fixed_clock(local)).fetch([spec])[0]
    from_utc = SyntheticSource(world, fixed_clock(datetime(2025, 9, 2, 3, 0, tzinfo=timezone.utc))).fetch([spec])[0]
    assert from_local == from_utc
    assert from_local.requested_at == "2025-09-02T03:00:00Z"
    source = SyntheticSource(world, fixed_clock(local))
    source.fetch([spec])
    assert source.events[0].day == date(2025, 9, 2)


def test_monthly_snapshots_are_country_level_single_pages_100_deep_inside_the_month():
    world = World(4)
    month = date(2025, 6, 1)
    pulls = world.monthly_snapshots(month)
    assert [pull.spec.search for pull in pulls] == [info.text for info in world.searches]
    assert pulls == world.monthly_snapshots("2025-06")
    for pull in pulls:
        assert pull.ok and pull.spec.location_code == COUNTRY_LOCATION_CODE and pull.spec.depth == 100
        assert "SYNTHETIC" in pull.status_message and "not a clean reading" in pull.status_message
        taken = datetime.fromisoformat(pull.completed_at.replace("Z", "+00:00"))
        assert taken.tzinfo is not None and month <= taken.date() < add_months(month, 1)
        indexed = len(world.true_ranking(pull.spec.search, taken.date()))
        assert len(pull.hits) == min(100, indexed) == 100
        assert [hit.rank for hit in pull.hits] == list(range(1, len(pull.hits) + 1))
    days = {pull.completed_at[:10] for pull in pulls}
    assert len(days) > 1
    with pytest.raises(ValueError):
        world.monthly_snapshots(add_months(world.end, 1))


def test_every_dated_list_is_dated_no_later_than_its_publication():
    world = World(2)
    month = date(2025, 9, 1)
    listed = world.strength_list(month)
    assert listed
    for site, point in listed.items():
        assert point.source.startswith("synthetic-")
        assert month <= point.snapshot_date < add_months(month, 1)
        assert point.value is not None
    published = next(iter(listed.values())).snapshot_date
    AsOfGuard(published).take(next(iter(listed.values())))
    with pytest.raises(LeakError):
        AsOfGuard(month).take(next(iter(listed.values())))
    for info in world.new_sites.values():
        early = world.strength_list(add_months(info.launch_date.replace(day=1), 1))
        assert info.site not in early  # a brand-new site is absent from top lists, never listed as 0
    with pytest.raises(ValueError):
        world.strength_list(world.start)  # its data month is before the window

    some_new = next(info for info in world.new_sites.values() if info.alive)
    for site in (world.sites[0].site, some_new.site):
        curve = world.traffic_curve(site)
        assert curve
        for point in curve:
            data_month = date.fromisoformat(point.value["month"] + "-01")
            assert point.snapshot_date >= add_months(data_month, 1)
            assert point.snapshot_date <= add_months(data_month, 2)
            assert point.value["etv"] is None or point.value["etv"] > 0
    first_new = world.traffic_curve(some_new.site)[0].value["month"]
    assert first_new == f"{some_new.launch_date:%Y-%m}"

    registered = some_new.registered
    nrd = world.newly_registered(registered)
    assert some_new.site in [point.value for point in nrd]
    assert all(point.snapshot_date == registered + timedelta(days=1) for point in nrd)
    assert [point.value for point in nrd] == sorted(point.value for point in nrd)
    assert world.newly_registered(registered) == nrd


def test_search_console_rows_exist_only_after_indexing_and_hide_rare_queries():
    world = World(2)
    live = [item for item in world.new_sites.values() if item.alive]
    info = min(live, key=lambda item: min(
        world.true_position(item.site, search, item.launch_date + timedelta(days=50)) for search in item.targets
    ))
    assert world.search_console_rows(info.site, info.launch_date) == []
    as_of = info.launch_date + timedelta(days=60)
    rows = world.search_console_rows(info.site, as_of)
    assert rows, "a live new site gets some impressions by day 60"
    volumes = {search.text: search.volume for search in world.searches}
    for row in rows:
        assert set(row) == {"data_date", "query", "page", "clicks", "impressions", "position"}
        day = date.fromisoformat(row["data_date"])
        assert info.index_date <= day <= as_of - timedelta(days=2)
        assert volumes[row["query"]] >= DEFAULT_KNOBS.gsc_anon_monthly_volume
        assert 0 <= row["clicks"] <= row["impressions"] and row["impressions"] > 0
        assert row["position"] >= 1.0
        assert row["page"].startswith(f"https://www.{info.site}/")
    keys = [(row["data_date"], row["query"]) for row in rows]
    assert keys == sorted(keys) and len(keys) == len(set(keys))
    assert rows == world.search_console_rows(info.site, as_of)
    rare = [search for search in world.searches if search.volume < DEFAULT_KNOBS.gsc_anon_monthly_volume]
    established = world.sites[0].site
    long_rows = world.search_console_rows(established, world.start + timedelta(days=40))
    assert not {row["query"] for row in long_rows} & {search.text for search in rare}


def test_agency_form_is_a_plain_dict_the_form_loader_accepts():
    world = World(2)
    agency_form = pytest.importorskip("aeo.ranksim.forecast.agency_form")
    for name, info in world.new_sites.items():
        form = world.agency_form(name)
        assert isinstance(form, dict) and form["site"] == name
        assert form["launch_plan"]["planned_launch_date"] == info.launch_date.isoformat()
        assert "SYNTHETIC" in form["filled_by"]
        loaded = agency_form.load_form(form)
        assert loaded.site == name
        assert loaded.filled_on < info.launch_date
    with pytest.raises(KeyError):
        world.agency_form(world.sites[0].site)


def test_synthetic_label_is_on_every_output():
    world = small_world()
    pulls = SyntheticSource(world, fixed_clock(PULL_DAY)).fetch([spec_for(world, s.text) for s in world.searches])
    assert all(pull.provider == "synthetic" and "SYNTHETIC" in pull.status_message for pull in pulls)
    assert all(cell.label == SYNTHETIC for cell in world.day90_truth())
    assert all(point.source.startswith("synthetic-") for point in world.strength_list(date(2025, 6, 1)).values())
    assert all("smartcrew" not in site.site for site in world.sites)


def test_median_of_five_pulls_is_closer_to_truth_than_one_pull():
    """Computed with core types only: per site, the median of its position over 5 pulls (absent = 51)."""
    world = small_world(seed=21, searches_per_niche=12)
    source = SyntheticSource(world, fixed_clock(PULL_DAY, timedelta(minutes=3)))
    single_errors: list[int] = []
    median_errors: list[float] = []
    for info in world.searches:
        pulls = [pull for pull in source.fetch([spec_for(world, info.text)] * 7)
                 if pull.ok and not pull.partial and not pull.spell_corrected][:5]
        assert len(pulls) >= 3
        positions = [pull.site_positions() for pull in pulls]
        for site in world.true_ranking(info.text, PULL_DAY.date())[:30]:
            truth = world.true_position(site, info.text, PULL_DAY.date())
            seen = [pos.get(site, NT50) for pos in positions]
            single_errors.append(abs(seen[0] - truth))
            median_errors.append(abs(statistics.median(seen) - truth))
    single = statistics.mean(single_errors)
    median = statistics.mean(median_errors)
    assert median < 0.85 * single, (median, single)


def test_noise_is_roughly_calibrated_to_the_measured_figures():
    """Loose bands around the map figures, stated here.

    Same-day pair agreement in top 10 (measured 0.93): 0.85 to 0.96.
    Next-day pair agreement (measured 0.877 to 0.90): 0.82 to 0.94.
    Next-day within 2 places at 4 to 10 (measured 0.82): 0.70 to 0.90.
    Mean next-day move grows with depth (SEOmonitor 0.22, 1.00, 2.41, 4.83).
    """
    knobs = replace(DEFAULT_KNOBS, poison_rate=0.0, partial_rate=0.0, fail_rate=0.0, spell_rate=0.0, repaired_rate=0.0)
    world = World(3, niches=1, searches_per_niche=30, established_sites=130, new_sites=0, months=12, knobs=knobs)

    def one(search: str, moment: datetime) -> dict[str, int]:
        return SyntheticSource(world, fixed_clock(moment)).fetch([spec_for(world, search)])[0].site_positions()

    def agree(a: dict[str, int], b: dict[str, int]) -> tuple[int, int]:
        sites = [site for site, pos in a.items() if pos <= 10 and site in b]
        good = total = 0
        for i, x in enumerate(sites):
            for y in sites[i + 1:]:
                total += 1
                good += (a[x] < a[y]) == (b[x] < b[y])
        return good, total

    same = [0, 0]
    next_day = [0, 0]
    moves: dict[str, list[int]] = {"1-3": [], "4-10": [], "11-20": [], "21-50": []}
    within_two: list[bool] = []
    for info in world.searches:
        for offset in range(0, 60, 12):
            start = PULL_DAY + timedelta(days=offset)
            a = one(info.text, start)
            b = one(info.text, start + timedelta(minutes=40))
            c = one(info.text, start + timedelta(days=1, hours=1))
            for store, other in ((same, b), (next_day, c)):
                good, total = agree(a, other)
                store[0] += good
                store[1] += total
            for site, pos in a.items():
                if site in c:
                    moves[band_of(pos)].append(abs(c[site] - pos))
                if 4 <= pos <= 10:
                    within_two.append(abs(c.get(site, NT50) - pos) <= 2)
    assert 0.85 <= same[0] / same[1] <= 0.96
    assert 0.82 <= next_day[0] / next_day[1] <= 0.94
    assert 0.70 <= statistics.mean(within_two) <= 0.90
    means = [statistics.mean(moves[band]) for band in ("1-3", "4-10", "11-20", "21-50")]
    assert means == sorted(means)
    assert means[0] < 1.0 and 2.0 < means[-1] < 8.0


def test_trust_ceiling_is_estimable_from_the_footprint_but_not_computable():
    world = World(9, new_sites=240)
    infos = list(world.new_sites.values())
    ceilings = np.array([info.trust_ceiling for info in infos])
    features = np.column_stack([
        np.ones(len(infos)),
        np.log1p([info.referring_domains for info in infos]),
        np.log1p([info.brand_search_volume for info in infos]),
        np.log1p([info.review_count for info in infos]),
        np.log1p([info.links_planned for info in infos]),
        [float(info.press_planned) for info in infos],
        [info.content_quality for info in infos],
    ])
    coef, *_ = np.linalg.lstsq(features, ceilings, rcond=None)
    fitted = features @ coef
    r_squared = 1 - np.sum((ceilings - fitted) ** 2) / np.sum((ceilings - ceilings.mean()) ** 2)
    assert 0.25 <= r_squared <= 0.90, r_squared
    assert world.trust_ceilings() == {info.site: info.trust_ceiling for info in sorted(infos, key=lambda i: i.site)}
    assert all(value > 0 for value in ceilings)


def test_outcomes_cluster_within_a_site():
    """plan-audit-math section 3: forecasts of one site share its fate. The world must show that."""
    world = World(1, new_sites=60)
    cells = world.day90_truth()
    effect = design_effect([float(cell.position == NT50) for cell in cells], [cell.site for cell in cells])
    assert effect is not None and effect > 1.5


def test_bad_inputs_are_refused():
    world = small_world()
    with pytest.raises(ValueError):
        world.true_position(world.sites[0].site, world.searches[0].text, world.end)
    with pytest.raises(KeyError):
        world.true_position("nobody.example", world.searches[0].text, world.start)
    with pytest.raises(KeyError):
        world.trust_ceiling(world.sites[0].site)
    with pytest.raises(ValueError):
        World(1, months=3)
    with pytest.raises(ValueError):
        World(1, searches_per_niche=2)
    assert World(1, months=3, new_sites=0).new_sites == {}


def test_worlds_build_fast():
    started = clock_time.perf_counter()
    small_world(31)
    small_seconds = clock_time.perf_counter() - started
    started = clock_time.perf_counter()
    World(31)
    default_seconds = clock_time.perf_counter() - started
    assert small_seconds < 1.0
    assert default_seconds < 5.0
