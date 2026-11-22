"""Tests for the seed-site manifest: tercile picking, freezing, and the reading schedule."""

from __future__ import annotations

import io
import json
import math
from datetime import date

import pytest

from aeo.ranksim.forecast import seeds
from aeo.ranksim.forecast.seeds import (
    ManifestError,
    SearchCandidate,
    SeedManifest,
    SeedSearch,
    SeedSite,
    assign_terciles,
    cost,
    hash_path,
    late_dates,
    load_frozen,
    pick_niche_searches,
    pick_target_searches,
    reading_count,
    reading_days,
    reading_schedule,
    schedule_report,
    stage_dates,
    tercile_quota,
)

METRIC = "dataforseo_keyword_difficulty"


def pool(count: int = 30, metric: str = METRIC) -> list[SearchCandidate]:
    return [SearchCandidate(f"search {index:02d}", float(index), metric, volume=10 * index) for index in range(count)]


def site(domain: str, *, launch: date | None = date(2026, 11, 30), pages: int = 12, searches=None, niche="plumbing"):
    if searches is None:
        searches = tuple(pick_target_searches(pool(), seed=7))
    return SeedSite(domain=domain, niche=niche, launch_date=launch, pages=pages, searches=tuple(searches))


def manifest(*sites: SeedSite) -> SeedManifest:
    return SeedManifest(
        sites=tuple(sites),
        location_code=1006886,
        device="desktop",
        language="en",
        difficulty_metric=METRIC,
        created_at="2026-11-29T10:00:00Z",
    )


# --------------------------------------------------------------------------- terciles


def test_tercile_quota_rule_twenty_gives_seven_seven_six():
    assert tercile_quota(20) == {"easy": 7, "medium": 7, "hard": 6}
    assert tercile_quota(10) == {"easy": 4, "medium": 3, "hard": 3}
    assert tercile_quota(21) == {"easy": 7, "medium": 7, "hard": 7}
    assert tercile_quota(2) == {"easy": 1, "medium": 1, "hard": 0}
    with pytest.raises(ValueError):
        tercile_quota(-1)


def test_terciles_come_from_rank_in_the_whole_pool():
    terciles = assign_terciles(pool(30))
    assert [terciles[f"search {i:02d}"] for i in (0, 9, 10, 19, 20, 29)] == [
        "easy", "easy", "medium", "medium", "hard", "hard"
    ]
    assert list(terciles.values()).count("easy") == 10


def test_equal_difficulty_values_never_straddle_a_cut():
    candidates = [SearchCandidate(f"s{i}", 1.0 if i < 2 else 5.0, METRIC) for i in range(6)]
    # Ranks 2 to 5 share one value. Rank 2 is in the easy third (3*2//6 = 1 is medium).
    terciles = assign_terciles(candidates)
    assert terciles["s0"] == terciles["s1"] == "easy"
    assert {terciles[f"s{i}"] for i in range(2, 6)} == {"medium"}


def test_pick_is_seeded_deterministic_and_balanced():
    first = pick_target_searches(pool(60), per_site=20, seed=11)
    again = pick_target_searches(list(reversed(pool(60))), per_site=20, seed=11)
    other = pick_target_searches(pool(60), per_site=20, seed=12)
    assert [item.search for item in first] == [item.search for item in again]
    assert [item.search for item in first] != [item.search for item in other]
    counts = {name: sum(1 for item in first if item.tercile == name) for name in seeds.TERCILES}
    assert counts == {"easy": 7, "medium": 7, "hard": 6}
    terciles = assign_terciles(pool(60))
    assert all(terciles[item.search] == item.tercile for item in first)
    assert len({item.search for item in first}) == 20


def test_pick_refuses_bad_pools():
    with pytest.raises(ValueError, match="mixes difficulty metrics"):
        pick_target_searches(pool(30) + [SearchCandidate("x", 1.0, "other_metric")], seed=1)
    with pytest.raises(ValueError, match="twice"):
        pick_target_searches(pool(30) + [SearchCandidate("Search  00", 3.0, METRIC)], seed=1)
    with pytest.raises(ValueError, match="no usable difficulty"):
        pick_target_searches(pool(30) + [SearchCandidate("nan one", math.nan, METRIC)], seed=1)
    with pytest.raises(ValueError, match="empty"):
        pick_target_searches([], seed=1)
    with pytest.raises(ValueError, match="easy tercile has 3 free candidates, and 4 are needed"):
        pick_target_searches(pool(9), per_site=12, seed=1)


def test_niche_pick_gives_no_search_to_two_sites_unless_asked():
    # Easy gets the leftover first, so 3 sites of 20 need 21 easy searches: a pool of 63.
    with pytest.raises(ValueError, match="3 sites need 21 easy searches with none shared, and the pool has 20"):
        pick_niche_searches(pool(60), ["a.example", "b.example", "c.example"], per_site=20, seed=3)
    candidates = pool(63)
    picks = pick_niche_searches(candidates, ["b.example", "a.example", "c.example"], per_site=20, seed=3)
    assert list(picks) == ["a.example", "b.example", "c.example"]
    all_searches = [item.search for items in picks.values() for item in items]
    assert len(all_searches) == len(set(all_searches)) == 60
    same = pick_niche_searches(candidates, ["c.example", "a.example", "b.example"], per_site=20, seed=3)
    assert {k: [i.search for i in v] for k, v in same.items()} == {k: [i.search for i in v] for k, v in picks.items()}
    with pytest.raises(ValueError):
        pick_niche_searches(pool(30), ["a.example", "b.example"], per_site=20, seed=3)
    shared = pick_niche_searches(pool(30), ["a.example", "b.example"], per_site=20, seed=3, allow_shared=True)
    assert all(len(items) == 20 for items in shared.values())
    with pytest.raises(ValueError, match="twice"):
        pick_niche_searches(candidates, ["a.example", "www.a.example"], seed=3)


# --------------------------------------------------------------------------- manifest


def test_validate_errors_and_warnings():
    good = site("a.example")
    dup = site("www.a.example")
    no_launch = site("b.example", launch=None)
    repeated = site("c.example", searches=(SeedSearch("x", "easy", 1.0, METRIC), SeedSearch("X ", "easy", 1.0, METRIC)))
    small = site("d.example", pages=9)
    check = manifest(good, dup, no_launch, repeated, small).validate()
    assert not check.ok
    text = " | ".join(check.errors)
    assert "www.a.example is listed twice" in text
    assert "b.example has no launch date" in text
    assert "c.example lists the search 'X ' twice" in text
    assert not any("d.example" in error for error in check.errors)
    assert any("d.example has 9 pages" in warning for warning in check.warnings)
    excluded = manifest(site("smartcrewhq.com")).validate()
    assert any("excluded" in error for error in excluded.errors)
    wrong_metric = manifest(site("e.example", searches=(SeedSearch("x", "easy", 1.0, "other"),))).validate()
    assert any("not the manifest metric" in error for error in wrong_metric.errors)


def test_shared_search_in_one_niche_is_a_warning():
    check = manifest(site("a.example"), site("b.example")).validate()
    assert check.ok
    assert any("is on 2 sites of niche 'plumbing'" in warning for warning in check.warnings)


def test_hash_ignores_site_and_search_order():
    a, b = site("a.example"), site("b.example", searches=tuple(pick_target_searches(pool(), seed=8)))
    reversed_b = SeedSite(b.domain, b.niche, b.launch_date, b.pages, tuple(reversed(b.searches)))
    assert manifest(a, b).manifest_hash() == manifest(reversed_b, a).manifest_hash()
    assert manifest(a).manifest_hash() != manifest(b).manifest_hash()


def test_freeze_load_frozen_and_tamper_refusal(tmp_path):
    path = tmp_path / "seed_manifest.json"
    original = manifest(site("a.example"), site("b.example", searches=tuple(pick_target_searches(pool(), seed=9))))
    digest = original.freeze(path)
    assert hash_path(path).read_text().strip() == digest
    loaded = load_frozen(path)
    assert loaded == SeedManifest.from_payload(json.loads(path.read_text()))
    assert loaded.manifest_hash() == digest
    assert loaded.sites[0].launch_date == date(2026, 11, 30)

    # Reformatting the file does not change the content, so it still loads.
    path.write_text(json.dumps(json.loads(path.read_text()), indent=0, sort_keys=False))
    assert load_frozen(path).manifest_hash() == digest

    # Freezing the same manifest again is allowed. A different one is refused.
    assert original.freeze(path) == digest
    with pytest.raises(ManifestError, match="already frozen"):
        manifest(site("z.example")).freeze(path)

    payload = json.loads(path.read_text())
    payload["sites"][0]["searches"][0]["search"] = "a winnable search"
    path.write_text(json.dumps(payload))
    with pytest.raises(ManifestError, match="changed after it was frozen"):
        load_frozen(path)


def test_freeze_refuses_invalid_and_load_frozen_needs_the_hash(tmp_path):
    with pytest.raises(ManifestError, match="no launch date"):
        manifest(site("a.example", launch=None)).freeze(tmp_path / "m.json")
    assert not (tmp_path / "m.json").exists()
    manifest(site("a.example")).save(tmp_path / "plain.json")
    with pytest.raises(ManifestError, match="never frozen"):
        load_frozen(tmp_path / "plain.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ManifestError, match="not valid JSON"):
        SeedManifest.load(tmp_path / "bad.json")
    with pytest.raises(ManifestError, match="malformed"):
        SeedManifest.from_payload({"sites": []})


# --------------------------------------------------------------------------- schedule


def test_schedule_dates_from_the_audit():
    dates = stage_dates(date(2026, 11, 30))
    assert dates == {
        "upload": date(2026, 11, 30),
        "d7": date(2026, 12, 7),
        "d14": date(2026, 12, 14),
        "d30": date(2026, 12, 30),
        "d60": date(2027, 1, 29),
        "outcome": date(2027, 2, 28),
    }
    assert stage_dates("2026-11-23")["outcome"] == date(2027, 2, 21)
    assert stage_dates(date(2026, 11, 23))["d60"] == date(2027, 1, 22)


def test_thirteen_weekly_readings_versus_sixteen_with_milestones():
    weekly = reading_schedule(date(2026, 11, 30), milestones=False)
    assert len(weekly) == 13 == reading_count(milestones=False)
    assert weekly[0] == date(2026, 11, 30) and weekly[-1] == date(2027, 2, 22)
    full = reading_schedule(date(2026, 11, 30))
    assert len(full) == 16 == reading_count()
    assert set(full) - set(weekly) == {date(2026, 12, 30), date(2027, 1, 29), date(2027, 2, 28)}
    assert full == sorted(full)
    assert reading_days(weekly=False) == [0, 7, 14, 30, 60, 90]
    assert reading_days(until_day=30) == [0, 7, 14, 21, 28, 30]
    with pytest.raises(ValueError):
        reading_days(until_day=-1)


def test_cost_shows_the_difference():
    ten = manifest(*[site(f"s{i}.example") for i in range(10)])
    estimate = cost(ten, 0.015)
    assert estimate.searches == 200
    assert (estimate.readings_weekly_only, estimate.readings_with_milestones) == (13, 16)
    assert estimate.cost_weekly_only_usd == pytest.approx(39.0)
    assert estimate.cost_with_milestones_usd == pytest.approx(48.0)
    assert estimate.extra_usd == pytest.approx(9.0)
    with pytest.raises(ValueError):
        cost(ten, -0.01)


def test_late_dates_and_printed_schedule():
    on_time = site("a.example", launch=date(2026, 11, 30))
    late = site("b.example", launch=date(2026, 12, 21))
    found = late_dates(manifest(on_time, late))
    pairs = {(item.domain, item.stage, item.marker) for item in found}
    # Day 90 of any November launch is after marker 4 on 22 January.
    assert ("a.example", "outcome", "marker4") in pairs
    assert ("a.example", "d30", "marker4") not in pairs
    assert ("a.example", "outcome", "marker5") not in pairs
    # A launch on 21 December has day 90 on 21 March, after marker 5 on 19 March.
    assert ("b.example", "outcome", "marker5") in pairs
    report = schedule_report(manifest(on_time, late))
    assert "outcome 2027-02-28: after marker1, marker2, marker3, marker4." in report
    assert "b.example outcome is 2027-03-21, after marker5 on 2027-03-19." in report
    assert chr(0x2013) not in report and chr(0x2014) not in report
    buffer = io.StringIO()
    seeds.print_schedule(manifest(on_time), file=buffer)
    assert buffer.getvalue().startswith("Plan marker dates:")
