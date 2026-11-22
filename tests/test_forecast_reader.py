"""The clean reader: ids, replay, committed Serper chunks, the poisoned-page filter and the middle position."""

from __future__ import annotations

import gzip
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.reader import (
    DROP_CLUSTER_TIE,
    DROP_LOW_OVERLAP,
    DROP_OTHER_CLUSTER,
    DROP_NOT_OK,
    DROP_PARTIAL,
    DROP_SPELL,
    ReplaySource,
    build_clean_reading,
    cost_of_reading,
    drop_rate,
    filter_pulls,
    load_serper_chunk_pulls,
    new_pull_id,
    new_reading_id,
    partial_min_organic,
    pulls_look_dependent,
    read_clean,
)
from aeo.ranksim.forecast.types import CleanReading, OrganicHit, Pull, PullSpec, SerpSource

REPO = Path(__file__).resolve().parents[1]
SPEC = PullSpec(search="dirt hauling software", location_code=1023191)
FILLER = [f"common{i}.com" for i in range(1, 16)]


def make_pull(
    name: str,
    ranks: dict[str, int] | list[str],
    *,
    spec: PullSpec = SPEC,
    ok: bool = True,
    partial: bool = False,
    spell: bool = False,
    completed: str = "2026-11-23T10:00:00Z",
    provider_datetime: str | None = None,
    raw_sha256: str | None = None,
    status_code: int | None = 20000,
    status_message: str | None = None,
    url_tag: str = "",
) -> Pull:
    """A pull from {site: rank} or from a list of sites in rank order."""
    mapping = {site: index + 1 for index, site in enumerate(ranks)} if isinstance(ranks, list) else dict(ranks)
    hits = tuple(
        OrganicHit(rank=rank, url=f"https://{site}/{url_tag or name}", domain=site, site=site)
        for site, rank in sorted(mapping.items(), key=lambda item: item[1])
    )
    return Pull(
        pull_id=f"pull-{name}",
        provider="test",
        endpoint="test",
        spec=spec,
        requested_at=completed,
        completed_at=completed,
        ok=ok,
        status_code=status_code,
        status_message=status_message,
        provider_datetime=provider_datetime,
        partial=partial,
        spell_corrected=spell,
        hits=hits if ok else (),
        raw_sha256=raw_sha256,
    )


def with_filler(ranks: dict[str, int]) -> dict[str, int]:
    """Add 15 sites shared by every pull at ranks 20 to 34, so the overlap rule keeps the pull."""
    out = dict(ranks)
    for index, site in enumerate(FILLER):
        out[site] = 20 + index
    return out


def good_sites(shift: int = 0) -> list[str]:
    sites = [f"good{i}.com" for i in range(1, 31)]
    return sites[shift:] + sites[:shift]


# ---------------------------------------------------------------------------
# ids


def test_pull_and_reading_ids_are_deterministic():
    first = new_pull_id("dataforseo", SPEC, "2026-11-23T10:00:00Z")
    assert first == new_pull_id("dataforseo", SPEC, "2026-11-23T10:00:00Z")
    assert first.startswith("pull-") and len(first) == 5 + 24
    assert first != new_pull_id("dataforseo", SPEC, "2026-11-23T10:00:01Z")
    assert first != new_pull_id("serper", SPEC, "2026-11-23T10:00:00Z")
    assert first != new_pull_id("dataforseo", SPEC, "2026-11-23T10:00:00Z", sequence=1)
    assert new_reading_id(SPEC, ["b", "a"]) == new_reading_id(SPEC, ["a", "b"])
    assert new_reading_id(SPEC, ["a"]) != new_reading_id(SPEC, ["a", "b"])


def test_reading_is_the_same_whatever_the_input_order():
    pulls = [make_pull(str(i), good_sites(i), completed=f"2026-11-23T10:0{i}:00Z") for i in range(5)]
    assert build_clean_reading(pulls) == build_clean_reading(list(reversed(pulls)))


# ---------------------------------------------------------------------------
# the middle position


def test_median_odd_count():
    pulls = [make_pull(str(i), with_filler({"x.com": r})) for i, r in enumerate((9, 3, 5))]
    reading = build_clean_reading(pulls)
    assert reading.position_of("x.com") == 5
    entry = next(e for e in reading.positions if e.site == "x.com")
    assert sorted(entry.pull_positions) == [3, 5, 9]


def test_median_even_count_rounds_half_up():
    pulls = [make_pull(str(i), with_filler({"x.com": r, "y.com": s})) for i, (r, s) in enumerate(((3, 3), (4, 4), (8, 7), (9, 9)))]
    reading = build_clean_reading(pulls)
    assert reading.position_of("x.com") == 6  # (4 + 8) / 2
    assert reading.position_of("y.com") == 6  # (4 + 7) / 2 = 5.5 rounds up


def test_median_with_absences():
    # x in 1 of 4: median of 2, 51, 51, 51 is 51, so NT50 and not listed.
    # y in 2 of 4: median of 2, 3, 51, 51 is (3 + 51) / 2 = 27. The even rule puts it at 27. Stated here, not hidden.
    ranks = [{"x.com": 2, "y.com": 2}, {"y.com": 3}, {}, {}]
    reading = build_clean_reading([make_pull(str(i), with_filler(r)) for i, r in enumerate(ranks)])
    assert reading.position_of("x.com") == NT50
    assert "x.com" not in [entry.site for entry in reading.positions]
    assert reading.position_of("y.com") == 27


def test_three_of_five_presence_rule():
    ranks = [{"in3.com": 4, "in2.com": 4}, {"in3.com": 5, "in2.com": 5}, {"in3.com": 6}, {}, {}]
    reading = build_clean_reading([make_pull(str(i), with_filler(r)) for i, r in enumerate(ranks)])
    assert reading.position_of("in3.com") == 6
    assert reading.position_of("in2.com") == NT50
    assert all(entry.position <= 50 for entry in reading.positions)
    assert reading.ranked_sites()[0] == "in3.com"


def test_best_url_is_taken_from_a_pull_at_the_median_position():
    pulls = [
        make_pull("a", with_filler({"x.com": 2}), url_tag="first", completed="2026-11-23T10:00:00Z"),
        make_pull("b", with_filler({"x.com": 2}), url_tag="second", completed="2026-11-23T10:01:00Z"),
        make_pull("c", with_filler({"x.com": 5}), url_tag="third", completed="2026-11-23T10:02:00Z"),
    ]
    entry = next(e for e in build_clean_reading(pulls).positions if e.site == "x.com")
    assert entry.best_url == "https://x.com/first"
    even = [make_pull(str(i), with_filler({"y.com": r})) for i, r in enumerate((3, 4, 6, 7))]
    entry = next(e for e in build_clean_reading(even).positions if e.site == "y.com")
    assert entry.position == 5 and entry.best_url is None  # no pull had y.com at 5


def test_excluded_sites_never_get_a_position():
    pulls = [make_pull(str(i), with_filler({"smartcrewhq.com": 1, "x.com": 2})) for i in range(3)]
    reading = build_clean_reading(pulls)
    assert reading.position_of("smartcrewhq.com") == NT50
    assert "smartcrewhq.com" not in reading.ranked_sites()
    assert reading.position_of("x.com") == 2


def test_taken_at_is_the_latest_kept_pull():
    pulls = [
        make_pull("a", good_sites(0), completed="2026-11-23T10:00:00Z"),
        make_pull("b", good_sites(1), completed="2026-11-23T10:05:00+00:00"),
        make_pull("c", good_sites(2), completed="2026-11-23T10:03:00Z"),
        make_pull("late", [], ok=False, completed="2026-11-23T11:00:00Z"),
    ]
    reading = build_clean_reading(pulls)
    assert reading.taken_at == "2026-11-23T10:05:00Z"
    assert reading.pulls_total == 4 and reading.pulls_kept == 3
    assert drop_rate(reading) == pytest.approx(0.25)


def test_reading_refuses_no_pulls_mixed_requests_and_repeated_ids():
    with pytest.raises(ValueError):
        build_clean_reading([])
    other = PullSpec(search="other search", location_code=1023191)
    with pytest.raises(ValueError):
        build_clean_reading([make_pull("a", ["x.com"]), make_pull("b", ["x.com"], spec=other)])
    with pytest.raises(ValueError):
        build_clean_reading([make_pull("a", ["x.com"]), make_pull("a", ["x.com"])])


# ---------------------------------------------------------------------------
# the poisoned-page filter


def test_partial_threshold_scales_with_depth():
    assert partial_min_organic(50) == 30
    assert partial_min_organic(10) == 6
    assert partial_min_organic(20) == 12
    assert partial_min_organic(7) == 5
    assert partial_min_organic(1) == 1
    with pytest.raises(ValueError):
        partial_min_organic(0)


def test_each_drop_reason():
    sites = good_sites()
    pulls = [
        make_pull("ok1", sites),
        make_pull("ok2", sites),
        make_pull("ok3", sites),
        make_pull("failed", [], ok=False, status_code=40501, status_message="Invalid Field"),
        make_pull("partial29", sites[:29], partial=True),
        make_pull("partial30", sites[:30], partial=True),
        make_pull("short_not_partial", sites[:5]),
        make_pull("spell", sites, spell=True),
        make_pull("failed_and_spell", [], ok=False, spell=True, status_code=None),
    ]
    kept, dropped = filter_pulls(pulls)
    reasons = dict(dropped)
    assert reasons["pull-failed"] == f"{DROP_NOT_OK}: 40501 Invalid Field"
    assert reasons["pull-partial29"].startswith(DROP_PARTIAL)
    assert reasons["pull-spell"] == DROP_SPELL
    assert reasons["pull-failed_and_spell"] == DROP_NOT_OK  # first rule wins
    assert [p.pull_id for p in kept] == ["pull-ok1", "pull-ok2", "pull-ok3", "pull-partial30", "pull-short_not_partial"]


def test_partial_rule_at_depth_ten():
    spec10 = PullSpec(search="q", location_code=2840, device="unspecified", depth=10)
    sites = [f"s{i}.com" for i in range(10)]
    pulls = [
        make_pull("a", sites, spec=spec10),
        make_pull("b", sites, spec=spec10),
        make_pull("five", sites[:5], spec=spec10, partial=True),
        make_pull("six", sites[:6], spec=spec10, partial=True),
    ]
    kept, dropped = filter_pulls(pulls)
    assert dict(dropped) == {"pull-five": f"{DROP_PARTIAL}: 5 organic results, fewer than 6"}
    assert len(kept) == 3


def test_one_poisoned_pull_among_five_is_dropped_leave_one_out():
    goods = [make_pull(f"g{i}", good_sites(i % 2), completed=f"2026-11-23T10:0{i}:00Z") for i in range(4)]
    poisoned = make_pull("poisoned", [f"spam{i}.com" for i in range(30)], completed="2026-11-23T10:09:00Z")
    pulls = goods[:2] + [poisoned] + goods[2:]
    kept, dropped = filter_pulls(pulls, version=1)
    assert [pid for pid, _ in dropped] == ["pull-poisoned"]
    assert dropped[0][1].startswith(f"{DROP_LOW_OVERLAP}: 0 of its 20 top sites")
    reading = build_clean_reading(pulls, version=1)
    clean = build_clean_reading(goods, version=1)
    assert reading.positions == clean.positions
    assert reading.pulls_kept == 4 and not reading.degraded
    assert all(not entry.site.startswith("spam") for entry in reading.positions)
    # Version 2 drops the same pull, as the smaller of two clusters with no other signal.
    kept2, dropped2 = filter_pulls(pulls)
    assert [pid for pid, _ in dropped2] == ["pull-poisoned"]
    assert dropped2[0][1].startswith(DROP_OTHER_CLUSTER) and "cluster size" in dropped2[0][1]
    assert build_clean_reading(pulls).positions == clean.positions
    # Switched off, the poisoned pull is kept. Its sites still never reach a median.
    for version in (1, 2):
        kept_all, dropped_none = filter_pulls(pulls, overlap_rule=False, version=version)
        assert len(kept_all) == 5 and dropped_none == []


def test_two_poisoned_pulls_that_agree_are_kept_and_the_median_still_ignores_them():
    """Version 1's known limit: poisoned pulls that agree vouch for each other. With 2 of 5 the median still holds."""
    goods = [make_pull(f"g{i}", good_sites()) for i in range(3)]
    spam = [f"spam{i}.com" for i in range(30)]
    bad = [make_pull(f"b{i}", spam) for i in range(2)]
    pulls = goods + bad
    kept, dropped = filter_pulls(pulls, version=1)
    assert dropped == [] and len(kept) == 5
    reading = build_clean_reading(pulls, version=1)
    pairs = [(e.site, e.position) for e in reading.positions]
    assert pairs == [(e.site, e.position) for e in build_clean_reading(goods, version=1).positions]
    assert reading.position_of("spam0.com") == NT50
    # Version 2 puts the two poisoned pulls in their own cluster and drops them.
    kept2, dropped2 = filter_pulls(pulls)
    assert [p.pull_id for p in kept2] == ["pull-g0", "pull-g1", "pull-g2"]
    assert {pid for pid, _ in dropped2} == {"pull-b0", "pull-b1"}
    assert [(e.site, e.position) for e in build_clean_reading(pulls).positions] == pairs


def test_three_poisoned_pulls_out_of_five_win_the_median():
    """The other side of the same limit, stated: a poisoned majority with no other signal is read as the truth.

    Both versions read it wrong. Version 2 needs history, concentration or ubiquity to do better (tested below).
    """
    spam = [f"spam{i}.com" for i in range(30)]
    pulls = [make_pull(f"g{i}", good_sites()) for i in range(2)] + [make_pull(f"b{i}", spam) for i in range(3)]
    reading = build_clean_reading(pulls, version=1)
    assert reading.pulls_kept == 5
    assert reading.position_of("spam0.com") == 1
    assert reading.position_of("good1.com") == NT50
    second = build_clean_reading(pulls)
    assert second.pulls_kept == 3 and not second.degraded
    assert second.position_of("spam0.com") == 1 and second.position_of("good1.com") == NT50


def test_overlap_rule_needs_two_other_usable_pulls():
    pulls = [
        make_pull("a", good_sites()),
        make_pull("b", [f"other{i}.com" for i in range(30)]),
        make_pull("failed", [], ok=False),
    ]
    kept, dropped = filter_pulls(pulls, version=1)
    assert [p.pull_id for p in kept] == ["pull-a", "pull-b"]  # a failed pull is not an "other" pull
    assert [pid for pid, _ in dropped] == ["pull-failed"]
    assert build_clean_reading(pulls, version=1).degraded
    # Version 2 sees two clusters of one pull and nothing to tell them apart: a tie, never a silent pick.
    kept2, dropped2 = filter_pulls(pulls)
    assert kept2 == []
    reasons = dict(dropped2)
    assert reasons["pull-a"].startswith(DROP_CLUSTER_TIE) and reasons["pull-b"] == reasons["pull-a"]
    assert build_clean_reading(pulls).degraded


def test_overlap_rule_drops_an_empty_pull_when_the_others_have_sites():
    pulls = [make_pull("a", good_sites()), make_pull("b", good_sites()), make_pull("empty", [])]
    _, dropped = filter_pulls(pulls)
    assert [pid for pid, _ in dropped] == ["pull-empty"]
    empties = [make_pull(str(i), []) for i in range(3)]
    kept, dropped = filter_pulls(empties)
    assert len(kept) == 3 and dropped == []


def test_overlap_share_of_exactly_half_is_kept():
    base = good_sites()
    half = base[:10] + [f"new{i}.com" for i in range(10)] + base[20:]
    less = base[:9] + [f"new{i}.com" for i in range(11)] + base[20:]
    _, dropped = filter_pulls([make_pull("a", base), make_pull("b", base), make_pull("half", half)])
    assert dropped == []
    _, dropped = filter_pulls([make_pull("a", base), make_pull("b", base), make_pull("less", less)])
    assert [pid for pid, _ in dropped] == ["pull-less"]


def test_degraded_when_fewer_than_three_pulls_are_kept():
    pulls = [make_pull(str(i), good_sites()) for i in range(2)] + [make_pull(f"f{i}", [], ok=False) for i in range(3)]
    reading = build_clean_reading(pulls)
    assert reading.degraded and reading.pulls_kept == 2 and reading.pulls_total == 5
    assert len(reading.dropped) == 3
    three = [make_pull(str(i), good_sites()) for i in range(3)]
    assert not build_clean_reading(three).degraded


# ---------------------------------------------------------------------------
# independence


def test_independence_flag():
    same_time = "2026-11-23T09:59:00Z"
    twins = [
        make_pull("a", good_sites(), provider_datetime=same_time, url_tag="same"),
        make_pull("b", good_sites(), provider_datetime=same_time, url_tag="same"),
    ]
    third = make_pull("c", good_sites(1), provider_datetime="2026-11-23T10:30:00Z")
    assert build_clean_reading(twins + [third]).pulls_not_independent
    assert pulls_look_dependent(twins[0], twins[1])

    no_time = [make_pull(str(i), good_sites(), url_tag="same") for i in range(3)]
    assert not build_clean_reading(no_time).pulls_not_independent  # same list, no provider time: not a match

    other_time = [make_pull(str(i), good_sites(), provider_datetime=f"2026-11-23T10:0{i}:00Z", url_tag="same") for i in range(3)]
    assert not build_clean_reading(other_time).pulls_not_independent

    same_raw = [make_pull("x", good_sites(), raw_sha256="ab" * 32), make_pull("y", good_sites(1), raw_sha256="ab" * 32), third]
    assert build_clean_reading(same_raw).pulls_not_independent


def test_independence_is_judged_on_kept_pulls_only():
    same_time = "2026-11-23T09:59:00Z"
    pulls = [
        make_pull("a", good_sites(), provider_datetime=same_time, url_tag="same"),
        make_pull("b", good_sites(), provider_datetime=same_time, spell=True, url_tag="same"),
        make_pull("c", good_sites(1)),
        make_pull("d", good_sites(2)),
    ]
    assert not build_clean_reading(pulls).pulls_not_independent


# ---------------------------------------------------------------------------
# replay and read_clean


def test_replay_source_serves_in_order_per_spec_and_then_fails():
    other = PullSpec(search="second search", location_code=1023191)
    a1, a2 = make_pull("a1", ["x.com"]), make_pull("a2", ["y.com"])
    b1 = make_pull("b1", ["z.com"], spec=other)
    source = ReplaySource([a1, b1, a2], clock=lambda: datetime(2026, 11, 23, 12, tzinfo=timezone.utc))
    assert isinstance(source, SerpSource)
    assert source.estimate_cost(100, 50) == 0.0
    assert [p.pull_id for p in source.fetch([SPEC, other])] == ["pull-a1", "pull-b1"]
    second = source.fetch([SPEC, other])
    assert second[0].pull_id == "pull-a2"
    assert second[1].ok is False and second[1].spec == other
    assert second[1].hits == () and "no more pulls" in (second[1].status_message or "")
    assert source.remaining(SPEC) == 0


class FakeClock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.moment += timedelta(seconds=seconds)


class FlakySource:
    """Takes 5 seconds per fetch and raises on chosen rounds."""

    name = "flaky"

    def __init__(self, inner: ReplaySource, clock: FakeClock, fail_on: set[int]) -> None:
        self.inner, self.clock, self.fail_on, self.round = inner, clock, fail_on, 0

    def fetch(self, specs):
        index = self.round
        self.round += 1
        self.clock.moment += timedelta(seconds=5)
        if index in self.fail_on:
            raise RuntimeError("boom")
        return self.inner.fetch(specs)

    def estimate_cost(self, count, depth=50):
        return 0.0


def test_read_clean_goes_on_after_a_failing_round_and_keeps_the_gap():
    other = PullSpec(search="second search", location_code=1023191)
    stored = []
    for i in range(5):
        stored.append(make_pull(f"a{i}", good_sites(i % 2)))
        stored.append(make_pull(f"b{i}", good_sites(i % 3), spec=other))
    clock = FakeClock()
    source = FlakySource(ReplaySource(stored), clock, fail_on={1})
    result = read_clean(source, [SPEC, other], pulls=5, gap_seconds=60, sleep=clock.sleep, now=clock.now)
    assert result.failed_rounds == ((1, "RuntimeError: boom"),)
    assert clock.sleeps == [55.0, 55.0, 55.0, 55.0]  # four gaps between five rounds, none after the last
    assert [r.search for r in result.readings] == [SPEC.search, other.search]
    first = result.readings[0]
    assert first.pulls_total == 4 and first.pulls_kept == 4 and not first.degraded
    assert ("round-1", "no_result: RuntimeError: boom") in first.dropped
    assert len(result.pulls) == 8


def test_read_clean_with_every_round_failing_gives_empty_degraded_readings():
    clock = FakeClock()
    source = FlakySource(ReplaySource([]), clock, fail_on={0, 1, 2})
    result = read_clean(source, [SPEC], pulls=3, gap_seconds=60, sleep=clock.sleep, now=clock.now)
    reading = result.readings[0]
    assert reading.degraded and reading.pulls_total == 0 and reading.positions == ()
    assert len(result.failed_rounds) == 3 and clock.sleeps == [55.0, 55.0]  # failed rounds still keep the gap


def test_read_clean_records_a_round_that_breaks_the_source_contract():
    class Short:
        name = "short"

        def fetch(self, specs):
            return []

        def estimate_cost(self, count, depth=50):
            return 0.0

    clock = FakeClock()
    result = read_clean(Short(), [SPEC], pulls=2, gap_seconds=60, sleep=clock.sleep, now=clock.now)
    assert [index for index, _ in result.failed_rounds] == [0, 1]
    assert "returned 0 pulls for 1 specs" in result.failed_rounds[0][1]


def test_read_clean_refuses_bad_arguments():
    clock = FakeClock()
    source = ReplaySource([])
    with pytest.raises(ValueError):
        read_clean(source, [SPEC, SPEC], sleep=clock.sleep, now=clock.now)
    with pytest.raises(ValueError):
        read_clean(source, [SPEC], pulls=0, sleep=clock.sleep, now=clock.now)
    with pytest.raises(ValueError):
        read_clean(source, [], sleep=clock.sleep, now=clock.now)


def test_cost_preview_uses_the_source_price():
    class Priced:
        name = "priced"

        def fetch(self, specs):
            raise AssertionError("a preview never fetches")

        def estimate_cost(self, count, depth=50):
            return count * (depth // 10) * 0.0006

    preview = cost_of_reading(Priced(), ["a", "b", "c"], pulls=5, depth=50)
    assert preview.searches == 3 and preview.pulls == 5
    assert preview.usd_per_round == pytest.approx(3 * 5 * 0.0006)
    assert preview.usd_total == pytest.approx(5 * 3 * 5 * 0.0006)
    assert cost_of_reading(ReplaySource([]), 300).usd_total == 0.0


# ---------------------------------------------------------------------------
# committed Serper chunks


def write_chunk(folder: Path, items: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    document = {"label": folder.name, "date": "2026-11-19", "source": "test", "chunk": 1, "items": items}
    with gzip.open(folder / "chunk-01.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(document, handle)


def serper_raw(query: str, links: list[tuple[int, str]], *, did_you_mean: str | None = None) -> str:
    payload = {
        "searchParameters": {"q": query, "gl": "us", "hl": "en", "type": "search", "num": 10, "engine": "google"},
        "organic": [{"title": f"t{p}", "link": link, "position": p} for p, link in links],
        "credits": 1,
    }
    if did_you_mean:
        payload["searchInformation"] = {"didYouMean": did_you_mean}
    return json.dumps(payload)


def test_load_serper_chunks(tmp_path):
    links = [(1, "https://www.alpha.com/a"), (2, "https://smartcrewhq.com/x"), (3, "https://learn.beta.com/b")]
    links += [(p, f"https://site{p}.com/") for p in range(4, 10)]
    items = [
        {"query": "q one", "start": "2026-11-19T14:37:55.250463-05:00", "end": "2026-11-19T14:37:56.8-05:00", "status": 200, "raw": serper_raw("q one", links)},
        {"query": "q two", "start": "2026-11-19T14:38:00-05:00", "end": "2026-11-19T14:38:01-05:00", "status": 429, "raw": "{}"},
        {"query": "q three", "start": "2026-11-19T14:39:00-05:00", "end": "2026-11-19T14:39:01-05:00", "status": 200, "raw": "not json"},
        {"query": "q four", "start": "2026-11-19T14:40:00-05:00", "end": "2026-11-19T14:40:01-05:00", "status": 200, "raw": serper_raw("q four", links, did_you_mean="q for")},
    ]
    write_chunk(tmp_path / "pull-a", items)
    pulls = load_serper_chunk_pulls(tmp_path / "pull-a")
    assert [p.ok for p in pulls] == [True, False, False, True]
    first = pulls[0]
    assert first.requested_at == "2026-11-19T19:37:55Z" and first.completed_at == "2026-11-19T19:37:56Z"
    assert first.spec.location_code == 2840 and first.spec.device == "unspecified" and first.spec.depth == 10
    assert "no pinned city" in first.endpoint
    assert [(h.rank, h.site) for h in first.hits][:2] == [(1, "alpha.com"), (3, "beta.com")]  # rank 2 stays a gap
    assert all(h.site != "smartcrewhq.com" for p in pulls for h in p.hits)
    assert first.partial is True  # 9 organic results for num=10
    assert first.raw_sha256 and first.raw_path.endswith("#items[0]")
    assert pulls[1].status_code == 429 and pulls[1].hits == ()
    assert "not JSON" in (pulls[2].status_message or "")
    assert pulls[3].spell_corrected is False and "q for" in (pulls[3].status_message or "")
    strict = load_serper_chunk_pulls(tmp_path / "pull-a", did_you_mean_is_correction=True)
    assert strict[3].spell_corrected is True
    assert load_serper_chunk_pulls(tmp_path / "pull-a") == pulls  # deterministic
    with pytest.raises(FileNotFoundError):
        load_serper_chunk_pulls(tmp_path / "missing")


def test_load_the_committed_serper_pulls():
    pulls = load_serper_chunk_pulls(REPO / "evals/claude/serp/2026-11-19/pull-a")
    assert len(pulls) == 70 and all(p.ok for p in pulls)
    assert len({p.pull_id for p in pulls}) == 70
    assert all(1 <= h.rank <= 10 for p in pulls for h in p.hits)
    assert all(h.site != "smartcrewhq.com" for p in pulls for h in p.hits)
    assert all(p.requested_at.startswith("2026-11-19T19:") for p in pulls)


def test_replay_misses_get_distinct_ids_and_read_clean_survives_them():
    fixed = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    source = ReplaySource([make_pull("only", good_sites())], clock=lambda: fixed)
    first, second = source.fetch([SPEC])[0], source.fetch([SPEC])[0]
    third = source.fetch([SPEC])[0]
    assert first.ok and not second.ok and not third.ok
    assert second.pull_id != third.pull_id
    clock = FakeClock()
    result = read_clean(
        ReplaySource([make_pull("only", good_sites())], clock=lambda: fixed),
        [SPEC], pulls=5, gap_seconds=60, sleep=clock.sleep, now=clock.now,
    )
    reading = result.readings[0]
    assert reading.pulls_total == 5 and reading.pulls_kept == 1 and reading.degraded


def test_read_clean_keeps_other_readings_when_one_spec_gets_a_repeated_pull():
    other = PullSpec(search="second search", location_code=1023191)

    class Repeating:
        name = "repeating"
        calls = 0

        def fetch(self, specs):
            self.calls += 1
            return [make_pull("same", good_sites()), make_pull(f"o{self.calls}", good_sites(), spec=other)]

        def estimate_cost(self, count, depth=50):
            return 0.0

    clock = FakeClock()
    result = read_clean(Repeating(), [SPEC, other], pulls=3, gap_seconds=60, sleep=clock.sleep, now=clock.now)
    broken, fine = result.readings
    assert broken.degraded and broken.pulls_total == 0
    assert broken.dropped[-1][1].startswith("invalid_pulls: the same pull id appears twice")
    assert fine.pulls_kept == 3 and not fine.degraded
    assert len(result.pulls) == 6


def test_serper_rows_without_a_usable_position_are_skipped(tmp_path):
    raw = json.dumps({
        "searchParameters": {"q": "q", "hl": "en", "num": 10},
        "organic": [{"link": "https://a.com/", "position": "x"}, {"link": "https://b.com/"}, {"link": "https://c.com/", "position": 3}],
    })
    write_chunk(tmp_path / "pull-x", [{"query": "q", "start": "2026-11-19T10:00:00Z", "status": 200, "raw": raw}])
    pulls = load_serper_chunk_pulls(tmp_path / "pull-x")
    assert [(h.rank, h.site) for h in pulls[0].hits] == [(3, "c.com")]
    write_chunk(tmp_path / "pull-y", [{"query": "q", "status": 200, "raw": raw}])
    with pytest.raises(ValueError):
        load_serper_chunk_pulls(tmp_path / "pull-y")


# ---------------------------------------------------------------------------
# version 2: clusters chosen by signals that do not depend on cluster size

from aeo.ranksim.forecast.reader import (  # noqa: E402
    CONCENTRATION_MIN_SLOTS,
    DROP_ALTERED_VS_HISTORY,
    READER_VERSION,
    UBIQUITY_MIN_SLOTS,
    RoundFailed,
    RoundSuspects,
    assemble_readings,
    clean_reading_with_verdict,
    cluster_pulls,
    fetch_round,
    off_brand_concentration,
    pulls_link,
    reader_settings,
    round_suspects,
    site_shares_token,
    ubiquity_slots,
)


def top10(search: str) -> PullSpec:
    """A request whose pulls hold 10 results, like the 19 Nov Serper pulls these cases come from.

    The request asks for depth 50: a reading of depth-10 requests is degraded as shallow_depth (see
    test_a_reading_of_shallow_pulls_is_degraded_and_does_not_claim_nt50). With at most 10 hits per
    pull the filter sees exactly the same slots at depth 50 as at depth 10.
    """
    return PullSpec(search=search, location_code=2840, device="unspecified", depth=50)


def slots_pull(
    name: str,
    sites: list[str],
    *,
    spec: PullSpec,
    completed: str = "2026-11-19T19:40:00Z",
    repaired: bool = False,
) -> Pull:
    """A pull from the site of every slot, best first. A site may hold several slots, each with its own URL."""
    hits = tuple(
        OrganicHit(rank=rank, url=f"https://{site}/{name}/{rank}", domain=site, site=site)
        for rank, site in enumerate(sites, start=1)
    )
    return Pull(
        pull_id=f"pull-{name}", provider="test", endpoint="test", spec=spec, requested_at=completed,
        completed_at=completed, ok=True, status_code=200, repaired=repaired, hits=hits,
    )


ASSIGNAR = top10("assignar alternative")
ALTERED_ASSIGNAR = ["slashdot.org"] * 7 + ["softwr.com", "itqlick.com", "gitnux.org"]
CLEAN_ASSIGNAR = ["g2.com", "softwareadvice.com", "cbinsights.com", "trafftrak.com", "owler.com", "capterra.com",
                  "assignar.com", "getapp.com.au", "constructiveos.com", "assignar.com"]
HCSS = top10("hcss pricing")
CLEAN_HCSS = ["hcss.com", "reddit.com", "hcss.com", "trustradius.com", "hcss.com", "hcss.com", "softwarefinder.com",
              "hcss.com", "capterra.com", "g2.com"]


def test_site_tokens_tie_brand_domains_to_their_searches():
    assert site_shares_token("hcss.com", "hcss pricing")
    assert site_shares_token("assignar.com", "assignar alternative")
    assert site_shares_token("earthworkssoftware.com", "earthwork software")
    assert site_shares_token("b2wsoftware.com", "b2w pricing")  # a 3-character token inside the domain name
    assert site_shares_token("getapp.com.au", "getapp reviews")
    assert not site_shares_token("slashdot.org", "assignar alternative")
    assert not site_shares_token("itqlick.com", "hcss pricing")
    assert not site_shares_token("forbes.com", "software for contractors")  # "for" is a stop word
    assert not site_shares_token("", "anything")


def test_off_brand_concentration_counts_slots_and_ignores_brand_sites():
    assert off_brand_concentration(slots_pull("a", ALTERED_ASSIGNAR, spec=ASSIGNAR)) == ("slashdot.org", 7)
    assert off_brand_concentration(slots_pull("h", CLEAN_HCSS, spec=HCSS)) == ("capterra.com", 1)
    assert off_brand_concentration(slots_pull("e", [], spec=HCSS)) == (None, 0)


def test_one_cluster_with_a_legitimately_concentrated_brand_site_is_kept_whole():
    pulls = [slots_pull(f"h{i}", CLEAN_HCSS, spec=HCSS, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    reading, verdict = clean_reading_with_verdict(pulls)
    assert len(verdict.clusters) == 1 and verdict.kept_cluster == 0 and verdict.decided_by is None
    assert reading.pulls_kept == 3 and not reading.degraded and reading.dropped == ()
    assert reading.position_of("hcss.com") == 1
    # The same holds for an off-brand site that really holds many slots on a stable page: one cluster is the truth.
    shuns = top10("shuns")
    stable = ["youtube.com"] * 8 + ["wikipedia.org", "imdb.com"]
    reading = build_clean_reading([slots_pull(f"y{i}", stable, spec=shuns, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)])
    assert reading.pulls_kept == 3 and not reading.degraded and reading.position_of("youtube.com") == 1


def test_two_altered_pulls_against_one_clean_pull_keep_the_clean_one_and_degrade():
    pulls = [
        slots_pull("a", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T19:40:00Z"),
        slots_pull("b", CLEAN_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T20:15:00Z"),
        slots_pull("c", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T20:52:00Z"),
    ]
    reading, verdict = clean_reading_with_verdict(pulls)
    assert verdict.clusters == (("pull-a", "pull-c"), ("pull-b",))
    assert verdict.decided_by == "off-brand concentration" and not verdict.tie
    assert reading.pull_ids == ("pull-b",) and reading.degraded  # fewer than MIN_KEPT_PULLS, so degraded, not wrong
    reasons = dict(reading.dropped)
    assert reasons["pull-a"].startswith(DROP_OTHER_CLUSTER) and "off-brand concentration" in reasons["pull-a"]
    assert reading.position_of("g2.com") == 1 and reading.position_of("slashdot.org") == NT50
    # Version 1 drops the clean pull. That is the bug version 2 fixes.
    first = build_clean_reading(pulls, version=1)
    assert first.pull_ids == ("pull-a", "pull-c") and first.position_of("slashdot.org") == 1


def round_of_altered_directories() -> list[Pull]:
    """Other searches of the same round where two directory sites fill 3 slots each (signal c)."""
    out = []
    for i, search in enumerate(("dirt hauling software", "earthwork takeoff", "crane rental app")):
        sites = ["dirlist.com"] * 3 + ["toolfinder.net"] * 3 + [f"filler{i}{j}.com" for j in range(4)]
        out.append(slots_pull(f"round{i}", sites, spec=top10(search)))
    return out


def test_round_suspects_count_other_unrelated_searches_only():
    round_pulls = round_of_altered_directories()
    suspects = round_suspects(round_pulls)
    assert suspects.searches_by_site["dirlist.com"] == frozenset({"dirt hauling software", "earthwork takeoff", "crane rental app"})
    assert suspects.sites() == frozenset({"dirlist.com", "toolfinder.net"})
    assert suspects.is_suspect("dirlist.com", "some other search")
    assert suspects.is_suspect("dirlist.com", "earthwork takeoff")  # two OTHER searches remain
    two = round_suspects(round_pulls[:2])
    assert not two.is_suspect("dirlist.com", "earthwork takeoff")  # only one other search
    assert two.is_suspect("dirlist.com", "a third search")
    # A site that shares a token with the search does not count that search.
    brand = round_suspects([slots_pull("x", ["dirlist.com"] * 5, spec=top10("dirlist login"))])
    assert brand.searches_by_site == {}
    # Failed and spell-corrected pulls do not count.
    failed = replace(round_pulls[0], ok=False, hits=())
    spelled = replace(round_pulls[1], spell_corrected=True)
    assert round_suspects([failed, spelled]).searches_by_site == {}
    merged = two.merged(round_suspects(round_pulls[2:]))
    assert merged.searches_by_site == suspects.searches_by_site and merged.rounds == 2
    assert round_suspects(list(reversed(round_pulls))) == suspects  # deterministic


def test_round_ubiquity_picks_the_clean_cluster_when_no_single_site_holds_a_run():
    search = top10("raken alternative")
    altered = ["dirlist.com"] * 3 + ["toolfinder.net"] * 2 + ["a1.com", "a2.com", "a3.com", "a4.com", "a5.com"]
    clean = ["softwareadvice.com", "miter.com", "timemark.com", "reddit.com", "workyard.com", "contractorforeman.com",
             "dailybuild.co", "sitestamp.us", "trustradius.com", "g2.com"]
    pulls = [slots_pull("a", altered, spec=search), slots_pull("b", clean, spec=search, completed="2026-11-19T20:10:00Z"),
             slots_pull("c", altered, spec=search, completed="2026-11-19T20:50:00Z")]
    suspects = round_suspects(round_of_altered_directories())
    assert off_brand_concentration(pulls[0])[1] < CONCENTRATION_MIN_SLOTS
    assert ubiquity_slots(pulls[0], suspects) == 5 >= UBIQUITY_MIN_SLOTS
    reading, verdict = clean_reading_with_verdict(pulls, suspects=suspects)
    assert verdict.decided_by == "round ubiquity" and reading.pull_ids == ("pull-b",) and reading.degraded
    # Without the round suspects nothing but size is left, and size picks the altered pair.
    plain, plain_verdict = clean_reading_with_verdict(pulls)
    assert plain_verdict.decided_by == "cluster size" and plain.pull_ids == ("pull-a", "pull-c")
    # Several rounds can be passed: each pull is judged against the round that holds it, never a join of rounds.
    own_round = round_suspects(round_of_altered_directories() + pulls)
    other_round = round_suspects([slots_pull("elsewhere", ["x.com"] * 10, spec=top10("unrelated"))])
    per_round = build_clean_reading(pulls, suspects=[other_round, own_round])
    assert per_round.pull_ids == ("pull-b",)
    # Rounds that do not hold these pulls give them no suspects, so signal (c) cannot fire.
    elsewhere = build_clean_reading(pulls, suspects=[round_suspects(round_of_altered_directories()[:2]),
                                                     round_suspects(round_of_altered_directories()[2:])])
    assert elsewhere.pull_ids == ("pull-a", "pull-c")


def test_five_pulls_with_three_clean_keep_the_three_and_are_not_degraded():
    stamps = [f"2026-11-19T19:4{i}:00Z" for i in range(5)]
    labels = ["clean", "altered", "clean", "altered", "clean"]
    pulls = [slots_pull(f"{label}{i}", ALTERED_ASSIGNAR if label == "altered" else CLEAN_ASSIGNAR, spec=ASSIGNAR,
                        completed=stamps[i]) for i, label in enumerate(labels)]
    reading, verdict = clean_reading_with_verdict(pulls)
    assert reading.pull_ids == ("pull-clean0", "pull-clean2", "pull-clean4")
    assert not reading.degraded and verdict.decided_by == "off-brand concentration"
    assert reading.position_of("g2.com") == 1 and reading.position_of("slashdot.org") == NT50


def history_reading(sites: list[str], spec: PullSpec, taken_at: str = "2026-11-18T19:00:00Z") -> CleanReading:
    pulls = [slots_pull(f"hist{i}", sites, spec=spec, completed=taken_at) for i in range(3)]
    reading = build_clean_reading(pulls)
    assert not reading.degraded and reading.taken_at == taken_at
    return reading


def test_history_chooses_a_minority_clean_cluster():
    search = top10("tenna alternative")
    altered = ["neplych.com", "eliteai.tools", "reddit.com", "dessence.ai", "cotlet.dev", "mindwiki.io", "craft.co",
               "aishortcutlab.com", "vantaige.io", "spotsaas.com"]
    clean = ["g2.com", "softwareadvice.com", "ezo.io", "reddit.com", "cbinsights.com", "trustradius.com", "fandom.com",
             "getlatka.com", "capterra.com", "tenna.com"]
    history = history_reading(clean, search)
    # Four altered pulls that agree and are not concentrated, three clean pulls.
    pulls = [slots_pull(f"alt{i}", altered, spec=search, completed=f"2026-11-19T19:4{i}:00Z") for i in range(4)]
    pulls += [slots_pull(f"ok{i}", clean, spec=search, completed=f"2026-11-19T19:5{i}:00Z") for i in range(3)]
    without = build_clean_reading(pulls)
    assert without.position_of("neplych.com") == 1  # size alone reads the altered majority
    reading, verdict = clean_reading_with_verdict(pulls, history=history)
    assert verdict.decided_by == "history"
    assert reading.pull_ids == ("pull-ok0", "pull-ok1", "pull-ok2") and not reading.degraded
    assert reading.position_of("g2.com") == 1 and reading.position_of("neplych.com") == NT50
    # With only two clean pulls the clean cluster still wins, and the reading is degraded, not wrong.
    few = pulls[:4] + pulls[4:6]
    small = build_clean_reading(few, history=history)
    assert small.pull_ids == ("pull-ok0", "pull-ok1") and small.degraded


def test_history_must_be_an_earlier_non_degraded_reading_of_the_same_request():
    pulls = [slots_pull(f"h{i}", CLEAN_HCSS, spec=HCSS, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    with pytest.raises(ValueError, match="same search"):
        build_clean_reading(pulls, history=history_reading(CLEAN_HCSS, top10("other search")))
    later = history_reading(CLEAN_HCSS, HCSS, taken_at="2026-11-20T00:00:00Z")
    with pytest.raises(ValueError, match="before the pulls"):
        build_clean_reading(pulls, history=later)
    degraded = replace(history_reading(CLEAN_HCSS, HCSS), degraded=True)
    with pytest.raises(ValueError, match="non-degraded"):
        build_clean_reading(pulls, history=degraded)
    # Version 1 ignores history, so it is never checked there.
    assert build_clean_reading(pulls, version=1, history=later).pulls_kept == 3


def test_one_altered_cluster_that_disagrees_with_history_is_degraded():
    pulls = [slots_pull(f"a{i}", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    history = history_reading(CLEAN_ASSIGNAR, ASSIGNAR)
    reading = build_clean_reading(pulls, history=history)
    assert reading.pulls_kept == 0 and reading.degraded and reading.positions == ()
    assert all(reason.startswith(DROP_ALTERED_VS_HISTORY) for _, reason in reading.dropped)
    assert build_clean_reading(pulls).pulls_kept == 3  # without history one cluster is kept whole (stated limit)
    # A concentrated page that agrees with history is kept.
    stable = [slots_pull(f"h{i}", CLEAN_HCSS, spec=HCSS, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    assert build_clean_reading(stable, history=history_reading(CLEAN_HCSS, HCSS)).pulls_kept == 3
    # A page that changed but does not look altered is kept too.
    changed = [slots_pull(f"c{i}", CLEAN_ASSIGNAR, spec=ASSIGNAR, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    other = history_reading([f"old{i}.com" for i in range(10)], ASSIGNAR)
    assert build_clean_reading(changed, history=other).pulls_kept == 3


def test_an_unbreakable_tie_gives_a_degraded_reading_with_its_reason():
    search = top10("site survey app")
    first = [f"x{i}.com" for i in range(10)]
    second = [f"y{i}.com" for i in range(10)]
    pulls = [slots_pull("a", first, spec=search), slots_pull("b", second, spec=search, completed="2026-11-19T19:41:00Z"),
             slots_pull("c", first, spec=search, completed="2026-11-19T19:42:00Z"),
             slots_pull("d", second, spec=search, completed="2026-11-19T19:43:00Z")]
    reading, verdict = clean_reading_with_verdict(pulls)
    assert verdict.tie and verdict.kept_cluster is None
    assert reading.pulls_kept == 0 and reading.degraded and reading.positions == ()
    assert len(reading.dropped) == 4
    assert all(reason.startswith(DROP_CLUSTER_TIE) and "no signal tells" in reason for _, reason in reading.dropped)
    # A concentrated pair is ruled out first, and the two single clean-looking pulls left are still a tie.
    altered = [slots_pull(f"z{i}", ["slashdot.org"] * 6 + first[:4], spec=search, completed=f"2026-11-19T19:5{i}:00Z")
               for i in range(2)]
    mixed, mixed_verdict = clean_reading_with_verdict(pulls[:2] + altered)
    assert len(mixed_verdict.clusters) == 3 and mixed_verdict.tie and mixed.pulls_kept == 0
    assert any(note.startswith("off-brand concentration") for note in mixed_verdict.notes)


def test_a_repaired_pull_decides_between_clusters_and_is_kept():
    pulls = [
        slots_pull("a", CLEAN_ASSIGNAR, spec=ASSIGNAR, repaired=True),
        slots_pull("b", [f"y{i}.com" for i in range(10)], spec=ASSIGNAR, completed="2026-11-19T19:41:00Z"),
        slots_pull("c", [f"y{i}.com" for i in range(10)], spec=ASSIGNAR, completed="2026-11-19T19:42:00Z"),
    ]
    reading, verdict = clean_reading_with_verdict(pulls)
    assert verdict.decided_by == "provider repair" and reading.pull_ids == ("pull-a",)
    assert reading.pulls_kept == 1 and reading.degraded


def test_a_short_page_that_is_the_head_of_a_longer_one_links_to_it():
    sites = good_sites()
    assert pulls_link(make_pull("long", sites), make_pull("short", sites[:5]))
    assert not pulls_link(make_pull("long", sites), make_pull("other", [f"z{i}.com" for i in range(30)]))
    assert pulls_link(make_pull("e1", []), make_pull("e2", []))
    assert not pulls_link(make_pull("e1", []), make_pull("long", sites))
    groups = cluster_pulls([make_pull("b", sites, completed="2026-11-23T10:01:00Z"), make_pull("a", sites)])
    assert [[p.pull_id for p in g] for g in groups] == [["pull-a", "pull-b"]]


def test_version_2_is_deterministic_and_ids_name_the_version():
    pulls = [
        slots_pull("a", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T19:40:00Z"),
        slots_pull("b", CLEAN_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T20:15:00Z"),
        slots_pull("c", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed="2026-11-19T20:52:00Z"),
    ]
    suspects = round_suspects(round_of_altered_directories())
    first = build_clean_reading(pulls, suspects=suspects)
    assert first == build_clean_reading(list(reversed(pulls)), suspects=suspects)
    assert first.reading_id != build_clean_reading(pulls, version=1).reading_id
    assert READER_VERSION == "reader-v2"
    with pytest.raises(ValueError):
        build_clean_reading(pulls, version=3)
    settings = reader_settings()
    assert settings["reader_version"] == "reader-v2" and settings["concentration_min_slots"] == CONCENTRATION_MIN_SLOTS
    assert reader_settings(version=1)["reader_version"] == "reader-v1"
    assert reader_settings(overlap_rule=False)["filter_on"] is False


# ---------------------------------------------------------------------------
# round mode


def test_fetch_round_returns_one_pull_per_spec_and_raises_on_a_broken_round():
    other = PullSpec(search="second search", location_code=1023191)
    source = ReplaySource([make_pull("a", good_sites()), make_pull("b", good_sites(), spec=other)])
    got = fetch_round(source, [SPEC, other], round_index=0, now=FakeClock().now)
    assert [p.pull_id for p in got] == ["pull-a", "pull-b"]

    class Boom:
        name = "boom"

        def fetch(self, specs):
            raise RuntimeError("down")

        def estimate_cost(self, count, depth=50):
            return 0.0

    with pytest.raises(RoundFailed) as caught:
        fetch_round(Boom(), [SPEC], round_index=4, now=FakeClock().now)
    assert caught.value.round_index == 4 and caught.value.message == "RuntimeError: down"
    assert caught.value.started_at == "2026-11-23T10:00:00Z"

    class Swapped:
        name = "swapped"

        def fetch(self, specs):
            return [make_pull("x", good_sites(), spec=other), make_pull("y", good_sites())]

        def estimate_cost(self, count, depth=50):
            return 0.0

    with pytest.raises(RoundFailed, match="out of order"):
        fetch_round(Swapped(), [SPEC, other], now=FakeClock().now)
    with pytest.raises(ValueError):
        fetch_round(source, [], now=FakeClock().now)


def test_assemble_readings_windows_pulls_hours_apart():
    other = PullSpec(search="second search", location_code=1023191)
    morning = [make_pull(f"m{i}", good_sites(i % 2), completed=f"2026-11-23T0{2 * i}:00:00Z") for i in range(3)]
    next_day = [make_pull(f"n{i}", good_sites(1), completed=f"2026-11-24T0{2 * i}:30:00Z") for i in range(3)]
    second = [make_pull(f"o{i}", good_sites(), spec=other, completed=f"2026-11-23T0{i}:10:00Z") for i in range(3)]
    readings = assemble_readings(next_day + second + morning)
    assert [(r.search, r.pulls_total, r.taken_at) for r in readings] == [
        (SPEC.search, 3, "2026-11-23T04:00:00Z"),
        (SPEC.search, 3, "2026-11-24T04:30:00Z"),
        ("second search", 3, "2026-11-23T02:10:00Z"),
    ]
    assert all(not r.degraded for r in readings)
    # A window is measured from its first pull: 4 hours is inside 12, so the morning is one reading.
    assert readings[0].pull_ids == ("pull-m0", "pull-m1", "pull-m2")
    # A narrow window splits them; None puts every pull of a request in one reading.
    narrow = assemble_readings(morning, window_hours=1.0)
    assert [r.pulls_total for r in narrow] == [1, 1, 1] and all(r.degraded for r in narrow)
    whole = assemble_readings(morning + next_day, window_hours=None)
    assert len(whole) == 1 and whole[0].pulls_total == 6
    assert assemble_readings(list(reversed(next_day + second + morning))) == readings  # deterministic
    with pytest.raises(ValueError):
        assemble_readings(morning, window_hours=0)
    deeper = replace(SPEC, depth=20)
    with pytest.raises(ValueError, match="one request"):
        assemble_readings(morning + [make_pull("d", good_sites(), spec=deeper)])


def test_assemble_readings_takes_the_latest_earlier_clean_reading_as_history():
    search = top10("tenna alternative")
    altered = [f"spam{i}.com" for i in range(10)]
    clean = [f"good{i}.com" for i in range(10)]
    old = history_reading([f"gone{i}.com" for i in range(10)], search, taken_at="2026-11-10T00:00:00Z")
    good = history_reading(clean, search, taken_at="2026-11-18T00:00:00Z")
    late = history_reading(altered, search, taken_at="2026-11-30T00:00:00Z")  # after the pulls: never used
    broken = replace(history_reading(altered, search, taken_at="2026-11-18T12:00:00Z"), degraded=True)
    pulls = [slots_pull(f"alt{i}", altered, spec=search, completed=f"2026-11-19T0{i}:00:00Z") for i in range(4)]
    pulls += [slots_pull(f"ok{i}", clean, spec=search, completed=f"2026-11-19T0{4 + i}:00:00Z") for i in range(3)]
    (reading,) = assemble_readings(pulls, history=[late, old, broken, good])
    assert reading.pull_ids == ("pull-ok0", "pull-ok1", "pull-ok2") and not reading.degraded
    (plain,) = assemble_readings(pulls)
    assert plain.position_of("spam0.com") == 1


def test_read_clean_uses_round_suspects():
    search = top10("raken alternative")
    altered = ["dirlist.com"] * 3 + ["toolfinder.net"] * 2 + ["a1.com", "a2.com", "a3.com", "a4.com", "a5.com"]
    clean = [f"good{i}.com" for i in range(10)]
    others = [top10(text) for text in ("dirt hauling software", "earthwork takeoff", "crane rental app")]
    stored: list[Pull] = []
    for index, kind in enumerate(("alt", "ok", "alt")):
        stored.append(slots_pull(f"{kind}{index}", altered if kind == "alt" else clean, spec=search))
        for j, spec in enumerate(others):
            sites = ["dirlist.com"] * 3 + ["toolfinder.net"] * 3 + [f"f{index}{j}{k}.com" for k in range(4)]
            stored.append(slots_pull(f"r{index}{j}", sites, spec=spec))
    clock = FakeClock()
    result = read_clean(ReplaySource(stored), [search] + others, pulls=3, gap_seconds=60, sleep=clock.sleep, now=clock.now)
    first = result.readings[0]
    assert first.pull_ids == ("pull-ok1",) and first.degraded
    old = read_clean(ReplaySource(stored), [search] + others, pulls=3, gap_seconds=60, sleep=clock.sleep, now=clock.now,
                     version=1).readings[0]
    assert old.pull_ids == ("pull-alt0", "pull-alt2")


def test_dropped_reasons_do_not_depend_on_the_input_order_in_either_version():
    """Bug found in review: `dropped` followed the input order, so the same pulls gave two different readings."""
    goods = [make_pull(f"g{i}", good_sites(), completed=f"2026-11-23T10:0{i}:00Z") for i in range(3)]
    bad = make_pull("bad", [f"spam{i}.com" for i in range(30)], completed="2026-11-23T10:05:00Z")
    failed = make_pull("failed", [], ok=False, completed="2026-11-23T10:06:00Z")
    pulls = [failed, goods[0], bad, goods[1], goods[2]]
    for version in (1, 2):
        first = build_clean_reading(pulls, version=version)
        assert first == build_clean_reading(list(reversed(pulls)), version=version)
        assert [pid for pid, _ in first.dropped] == ["pull-bad", "pull-failed"]


def test_read_clean_passes_the_latest_earlier_clean_reading_as_history():
    search = top10("tenna alternative")
    altered = [f"spam{i}.com" for i in range(10)]
    clean = [f"good{i}.com" for i in range(10)]
    stored = [slots_pull(f"alt{i}", altered, spec=search) for i in range(3)]
    stored.insert(1, slots_pull("ok", clean, spec=search))
    clock = FakeClock()
    later = [history_reading(clean, search, taken_at="2026-11-22T10:00:00Z")]  # after the pulls: ignored
    ignored = read_clean(ReplaySource(stored), [search], pulls=4, gap_seconds=60, sleep=clock.sleep, now=clock.now,
                         history=later).readings[0]
    assert ignored.position_of("spam0.com") == 1
    history = [history_reading(clean, search, taken_at="2026-11-18T10:00:00Z")]
    with_history = read_clean(ReplaySource(stored), [search], pulls=4, gap_seconds=60, sleep=clock.sleep, now=clock.now,
                              history=history).readings[0]
    assert with_history.pull_ids == ("pull-ok",) and with_history.degraded
    without = read_clean(ReplaySource(stored), [search], pulls=4, gap_seconds=60, sleep=clock.sleep, now=clock.now).readings[0]
    assert without.position_of("spam0.com") == 1 and not without.degraded


# ---------------------------------------------------------------------------
# Provenance: reader version, degraded reason and the SYNTHETIC label are set by the reader itself

from aeo.ranksim.forecast.reader import (  # noqa: E402
    DEGRADED_NO_PULLS,
    DEGRADED_TOO_FEW_KEPT,
    READER_VERSION_V1,
    READER_VERSION_V2,
    reader_version_name,
    reading_label,
)
from aeo.ranksim.forecast.types import REAL, SYNTHETIC  # noqa: E402


def test_every_reading_names_its_reader_and_a_clean_one_has_no_degraded_reason():
    pulls = [make_pull(str(i), good_sites()) for i in range(3)]
    reading = build_clean_reading(pulls)
    assert reading.reader_version == READER_VERSION_V2 and reading.degraded_reason is None and reading.label == REAL
    assert build_clean_reading(pulls, version=1).reader_version == READER_VERSION_V1
    assert build_clean_reading(pulls, overlap_rule=False).reader_version == READER_VERSION_V2 + "-filter-off"
    assert reader_version_name(2) == READER_VERSION_V2


def test_the_degraded_reason_says_why():
    few = [make_pull(str(i), good_sites()) for i in range(2)] + [make_pull(f"f{i}", [], ok=False) for i in range(3)]
    reading = build_clean_reading(few)
    assert reading.degraded and reading.degraded_reason.startswith(DEGRADED_TOO_FEW_KEPT)
    assert "2 of 5 pulls kept, 3 needed" in reading.degraded_reason and "not_ok 3" in reading.degraded_reason
    search = top10("site survey app")
    first, second = [f"x{i}.com" for i in range(10)], [f"y{i}.com" for i in range(10)]
    tie = build_clean_reading([
        slots_pull("a", first, spec=search), slots_pull("b", second, spec=search, completed="2026-11-19T19:41:00Z"),
        slots_pull("c", first, spec=search, completed="2026-11-19T19:42:00Z"),
        slots_pull("d", second, spec=search, completed="2026-11-19T19:43:00Z"),
    ])
    assert tie.degraded and tie.degraded_reason.startswith(DROP_CLUSTER_TIE)
    altered = [slots_pull(f"a{i}", ALTERED_ASSIGNAR, spec=ASSIGNAR, completed=f"2026-11-19T19:4{i}:00Z") for i in range(3)]
    against = build_clean_reading(altered, history=history_reading(CLEAN_ASSIGNAR, ASSIGNAR))
    assert against.degraded and against.degraded_reason.startswith(DROP_ALTERED_VS_HISTORY)


def test_read_clean_gives_failed_specs_a_no_pulls_reason_and_the_source_label():
    class Broken:
        name = "synthetic"

        def fetch(self, specs):
            raise RuntimeError("down")

        def estimate_cost(self, count, depth=50):
            return 0.0

    result = read_clean(Broken(), [SPEC], pulls=2, gap_seconds=60, sleep=lambda s: None)
    (reading,) = result.readings
    assert reading.degraded and reading.degraded_reason.startswith(DEGRADED_NO_PULLS)
    assert reading.label == SYNTHETIC and reading.reader_version == READER_VERSION_V2


def test_a_reading_from_synthetic_pulls_is_labeled_synthetic_and_history_must_match():
    synthetic = [replace(make_pull(str(i), good_sites()), provider="synthetic") for i in range(3)]
    reading = build_clean_reading(synthetic)
    assert reading.label == SYNTHETIC and reading_label(synthetic) == SYNTHETIC
    mixed = synthetic[:2] + [make_pull("real", good_sites())]
    assert build_clean_reading(mixed).label == SYNTHETIC  # one simulated pull is enough to make it not REAL
    real_history = build_clean_reading([make_pull(f"h{i}", good_sites(), completed="2026-11-20T10:00:00Z") for i in range(3)])
    with pytest.raises(ValueError, match="never judge each other"):
        build_clean_reading(synthetic, history=real_history)


# ---------------------------------------------------------------------------
# Review fixes: depth, round ubiquity per round, reading ids, reading time, paid rounds, sandbox label

from aeo.ranksim.forecast.reader import DEGRADED_SHALLOW, provider_is_real  # noqa: E402
from aeo.ranksim.forecast.scoreboard import Scoreboard  # noqa: E402


def depth10_pull(name: str, sites: list[str], completed: str) -> Pull:
    return slots_pull(name, sites, spec=replace(top10("roof repair austin"), depth=10), completed=completed)


def test_a_reading_of_shallow_pulls_is_degraded_and_does_not_claim_nt50():
    """Spec s3: readings are at depth 50, and NT50 means "not in the top 50". A depth-10 reading cannot say that."""
    page = [f"top{i}.com" for i in range(10)]
    pulls = [depth10_pull(f"p{i}", page, f"2026-11-22T0{i}:00:00Z") for i in range(5)]
    reading = build_clean_reading(pulls)
    assert reading.degraded and reading.degraded_reason.startswith(DEGRADED_SHALLOW)
    # A report that knowingly reads the top 10 asks for it by name; depth 50 readings are unchanged.
    assert not build_clean_reading(pulls, min_depth=10).degraded
    deep = [slots_pull(f"d{i}", page, spec=top10("roof repair austin"), completed=f"2026-11-22T0{i}:00:00Z") for i in range(5)]
    deep_reading = build_clean_reading(deep)
    assert not deep_reading.degraded and deep_reading.position_of("myroofer.com") == NT50


def test_round_ubiquity_uses_the_suspects_of_one_round_not_all_rounds_joined():
    """Spec s3 step 4: "no sites that also fill slots on unrelated searches of the same round".

    zfiller1.com and zfiller2.com fill 3 slots of "alpha tools" in round 0 only and of "beta tools" in round 1
    only, so in no single round are they suspects. For "gamma widgets" they hold 4 slots in 3 pulls; signal (c)
    must not fire, so the larger cluster is kept and the reading is not degraded.
    """
    t0 = datetime(2026, 11, 22, 6, 0, 0, tzinfo=timezone.utc)

    def make(search: str, sites: list[str], r: int) -> Pull:
        stamp = (t0 + timedelta(hours=3 * r)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return slots_pull(f"{search.replace(' ', '-')}-{r}", sites, spec=top10(search), completed=stamp)

    def clean(prefix: str) -> list[str]:
        return [f"{prefix}{i}.com" for i in range(10)]

    filled = lambda prefix: ["zfiller1.com"] * 3 + ["zfiller2.com"] * 3 + [f"{prefix}{i}.com" for i in range(4)]  # noqa: E731
    pulls: list[Pull] = []
    for r in range(5):
        pulls.append(make("alpha tools", filled("alph") if r == 0 else clean("alph"), r))
        pulls.append(make("beta tools", filled("bet") if r == 1 else clean("bet"), r))
        gamma = ["zfiller1.com"] * 2 + ["zfiller2.com"] * 2 + [f"gama{i}.com" for i in range(6)] if r < 3 else clean("gamb")
        pulls.append(make("gamma widgets", gamma, r))
    for r in range(5):
        alone = round_suspects([p for p in pulls if p.pull_id.endswith(f"-{r}")])
        assert not alone.is_suspect("zfiller1.com", "gamma widgets")

    specs = [top10(s) for s in ("alpha tools", "beta tools", "gamma widgets")]
    result = read_clean(ReplaySource(pulls), specs, pulls=5, gap_seconds=60, sleep=lambda s: None, now=lambda: t0)
    gamma = next(r for r in result.readings if r.search == "gamma widgets")
    assert not gamma.degraded and gamma.pulls_kept == 3, gamma.dropped
    # assemble_readings with one RoundSuspects per round judges the same way.
    rounds = [round_suspects([p for p in pulls if p.pull_id.endswith(f"-{r}")]) for r in range(5)]
    (again,) = [r for r in assemble_readings(pulls, suspects=rounds, window_hours=None) if r.search == "gamma widgets"]
    assert again.pulls_kept == 3 and not again.degraded


def test_readings_made_by_different_reader_settings_have_different_ids():
    clean = [f"c{i}.com" for i in range(10)]
    altered = ["slashdot.org"] * 7 + ["x1.com", "x2.com", "x3.com"]
    stamps = [f"2026-11-22T{h:02d}:00:00Z" for h in (6, 9, 12, 15, 18)]
    pulls = [slots_pull(f"p{i}", clean if i < 3 else altered, spec=ASSIGNAR, completed=stamps[i]) for i in range(5)]
    on = build_clean_reading(pulls)
    off = build_clean_reading(pulls, overlap_rule=False)
    assert on.reader_version != off.reader_version and on.positions != off.positions
    assert on.reading_id != off.reading_id
    # A plain reading keeps the id it always had (filter on, no history, no suspect slots).
    assert on.reading_id == new_reading_id(ASSIGNAR, [p.pull_id for p in pulls])
    assert build_clean_reading(list(reversed(pulls))).reading_id == on.reading_id  # order of pulls does not matter
    history = history_reading(CLEAN_ASSIGNAR, ASSIGNAR, taken_at="2026-11-21T06:00:00Z")
    assert build_clean_reading(pulls, history=history).reading_id not in (on.reading_id, off.reading_id)


def test_reading_time_covers_every_pull_that_decided_which_cluster_was_kept():
    """No leakage: three pulls of page A on 23 Nov, two of page B on 24 Nov. The 24 Nov pulls decide the outcome
    (a third one would make a tie), so the reading is dated by them, not by the kept pulls alone."""
    search = top10("roof repair")
    page_a, page_b = [f"a{k}.com" for k in range(10)], [f"b{k}.com" for k in range(10)]

    def pulls(late: int) -> list[Pull]:
        early = [slots_pull(f"a{i}", page_a, spec=search, completed=f"2026-11-23T{20 + i}:00:00Z") for i in range(3)]
        return early + [slots_pull(f"b{i}", page_b, spec=search, completed=f"2026-11-24T0{1 + i}:00:00Z") for i in range(late)]

    two_late, three_late = build_clean_reading(pulls(2)), build_clean_reading(pulls(3))
    assert three_late.degraded and not two_late.degraded and two_late.pull_ids == ("pull-a0", "pull-a1", "pull-a2")
    assert two_late.taken_at == "2026-11-24T02:00:00Z"


def test_two_failed_days_make_two_storable_readings(tmp_path):
    class Broken:
        name = "dataforseo"

        def fetch(self, specs):
            raise RuntimeError("provider down")

        def estimate_cost(self, count, depth=50):
            return 0.0

    spec = PullSpec(search="fence install", location_code=1026201)
    day1 = read_clean(Broken(), [spec], pulls=1, sleep=lambda s: None,
                      now=lambda: datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)).readings[0]
    day2 = read_clean(Broken(), [spec], pulls=1, sleep=lambda s: None,
                      now=lambda: datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)).readings[0]
    assert day1.reading_id != day2.reading_id
    board = Scoreboard(f"sqlite:///{tmp_path / 'sb.db'}")
    board.add_reading(day1)
    board.add_reading(day2)
    board.add_reading(day1)  # the same reading again is still a no-op


def test_a_round_that_stopped_after_paying_keeps_its_pulls():
    """A source that raises after paying for part of a round hands one Pull per spec back on the error."""
    other = top10("second search")
    paid = make_pull("paid", good_sites())
    unsent = make_pull("unsent", [], ok=False, status_message="Not sent: refused")
    unsent = replace(unsent, spec=other)

    class StopsPartWay:
        name = "dataforseo"

        def fetch(self, specs):
            error = RuntimeError("refused part way")
            error.pulls = (paid, unsent)
            raise error

        def estimate_cost(self, count, depth=50):
            return 0.0

    with pytest.raises(RoundFailed) as caught:
        fetch_round(StopsPartWay(), [SPEC, other], now=FakeClock().now)
    assert caught.value.pulls == (paid, unsent)
    result = read_clean(StopsPartWay(), [SPEC, other], pulls=1, sleep=lambda s: None, now=FakeClock().now)
    assert result.pulls == (paid, unsent) and len(result.failed_rounds) == 1
    first = next(r for r in result.readings if r.search == SPEC.search)
    assert first.pulls_total == 1 and first.pull_ids == ("pull-paid",)


def test_sandbox_pulls_are_never_real():
    sandbox = [replace(make_pull(str(i), good_sites()), provider="dataforseo-sandbox") for i in range(3)]
    assert build_clean_reading(sandbox).label == SYNTHETIC
    assert not provider_is_real("dataforseo-sandbox-live") and provider_is_real("dataforseo") and provider_is_real("serper")


# ---------------------------------------------------------------------------
# the gap floor, keyword-aware cost previews, and the one list of synthetic providers


def test_read_clean_refuses_a_gap_under_the_floor_for_a_real_provider():
    from aeo.ranksim.forecast.config import MIN_PULL_GAP_SECONDS

    clock = FakeClock()
    for name in ("dataforseo", "serper", "replay"):
        source = ReplaySource([make_pull(str(i), good_sites()) for i in range(3)], name=name)
        with pytest.raises(ValueError, match="at least 60 seconds apart"):
            read_clean(source, [SPEC], pulls=3, gap_seconds=MIN_PULL_GAP_SECONDS - 1, sleep=clock.sleep, now=clock.now)
    one = read_clean(ReplaySource([make_pull("x", good_sites())]), [SPEC], pulls=1, gap_seconds=0,
                     sleep=clock.sleep, now=clock.now)
    assert len(one.readings) == 1  # one round has no gap to keep


def test_read_clean_lets_synthetic_and_sandbox_sources_read_faster():
    clock = FakeClock()
    for name in ("synthetic", "dataforseo-sandbox", "dataforseo-sandbox-live_normal"):
        pulls = [replace(make_pull(str(i), good_sites()), provider=name) for i in range(3)]
        result = read_clean(ReplaySource(pulls, name=name), [SPEC], pulls=3, gap_seconds=0, sleep=clock.sleep, now=clock.now)
        assert result.readings[0].label == SYNTHETIC and clock.sleeps == []


def test_a_fast_read_through_a_source_that_says_synthetic_is_never_a_real_reading():
    clock = FakeClock()
    real_pulls = [make_pull(str(i), good_sites()) for i in range(3)]  # these pulls claim provider dataforseo
    result = read_clean(ReplaySource(real_pulls, name="synthetic"), [SPEC], pulls=3, gap_seconds=0,
                        sleep=clock.sleep, now=clock.now)
    assert result.readings[0].label == SYNTHETIC


def test_cost_preview_passes_the_search_texts_to_a_source_that_takes_them():
    seen: list[object] = []

    class Texts:
        name = "texts"

        def fetch(self, specs):
            raise AssertionError("a preview never fetches")

        def estimate_cost(self, count, depth=50, *, keywords=None):
            seen.append(keywords)
            return float(sum(5 if "site:" in k else 1 for k in keywords)) if keywords is not None else float(count)

    specs = [SPEC, PullSpec(search="site:example.com hauling", location_code=1023191)]
    assert cost_of_reading(Texts(), specs, pulls=2).usd_per_round == 6.0
    assert seen[-1] == [SPEC.search, "site:example.com hauling"]
    assert cost_of_reading(Texts(), ["plain", "site:x.com y"], pulls=1).usd_total == 6.0
    counted = cost_of_reading(Texts(), 2, pulls=1)
    assert counted.usd_total == 2.0 and seen[-1] is None
    assert any("without the search texts" in note for note in counted.notes)


def test_cost_preview_still_works_with_a_source_that_does_not_take_keywords():
    class Old:
        name = "old"

        def fetch(self, specs):
            raise AssertionError("a preview never fetches")

        def estimate_cost(self, count, depth=50):
            return 0.5 * count

    preview = cost_of_reading(Old(), ["a", "site:b.com c"], pulls=1)
    assert preview.usd_total == 1.0 and any("without the search texts" in note for note in preview.notes)


def test_excluded_sites_are_matched_after_normalising():
    from aeo.ranksim.forecast.reader import pull_site_positions, slot_sites
    from aeo.ranksim.forecast.types import is_excluded_site

    assert is_excluded_site("www.SmartCrewHQ.com") and is_excluded_site("https://blog.smartcrewhq.com/x")
    assert not is_excluded_site("example.com") and not is_excluded_site(None) and not is_excluded_site("")
    hits = (OrganicHit(rank=1, url="https://www.smartcrewhq.com/", domain="www.smartcrewhq.com", site="www.SmartCrewHQ.com"),
            OrganicHit(rank=2, url="https://a.com/", domain="a.com", site="a.com"))
    pull = replace(make_pull("ex", good_sites()), hits=hits)
    assert pull_site_positions(pull) == {"a.com": 2}
    assert slot_sites(pull) == ["a.com"]


def test_the_sandbox_is_in_the_one_list_of_synthetic_providers():
    from aeo.ranksim.forecast.types import SYNTHETIC_PROVIDERS, provider_is_synthetic

    assert "dataforseo-sandbox" in SYNTHETIC_PROVIDERS
    assert provider_is_synthetic("dataforseo-sandbox-live_normal") and provider_is_synthetic("synthetic")
    assert not provider_is_synthetic("dataforseo") and not provider_is_synthetic("dataforseo-live_normal")
    assert not provider_is_synthetic(None)
    for name in ("synthetic", "dataforseo-sandbox", "dataforseo-sandbox-live_normal", "dataforseo", "serper", "replay"):
        assert provider_is_real(name) is not provider_is_synthetic(name)
