"""The clean reader: ids, replay, committed Serper chunks, the poisoned-page filter and the middle position."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aeo.ranksim.forecast.config import NT50
from aeo.ranksim.forecast.reader import (
    DROP_LOW_OVERLAP,
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
from aeo.ranksim.forecast.types import OrganicHit, Pull, PullSpec, SerpSource

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
    kept, dropped = filter_pulls(pulls)
    assert [pid for pid, _ in dropped] == ["pull-poisoned"]
    assert dropped[0][1].startswith(f"{DROP_LOW_OVERLAP}: 0 of its 20 top sites")
    reading = build_clean_reading(pulls)
    clean = build_clean_reading(goods)
    assert reading.positions == clean.positions
    assert reading.pulls_kept == 4 and not reading.degraded
    assert all(not entry.site.startswith("spam") for entry in reading.positions)
    # Switched off, the poisoned pull is kept. Its sites still never reach a median.
    kept_all, dropped_none = filter_pulls(pulls, overlap_rule=False)
    assert len(kept_all) == 5 and dropped_none == []


def test_two_poisoned_pulls_that_agree_are_kept_and_the_median_still_ignores_them():
    """Known limit: poisoned pulls that agree vouch for each other. With 2 of 5 the median still holds."""
    goods = [make_pull(f"g{i}", good_sites()) for i in range(3)]
    spam = [f"spam{i}.com" for i in range(30)]
    bad = [make_pull(f"b{i}", spam) for i in range(2)]
    pulls = goods + bad
    kept, dropped = filter_pulls(pulls)
    assert dropped == [] and len(kept) == 5
    reading = build_clean_reading(pulls)
    pairs = [(e.site, e.position) for e in reading.positions]
    assert pairs == [(e.site, e.position) for e in build_clean_reading(goods).positions]
    assert reading.position_of("spam0.com") == NT50


def test_three_poisoned_pulls_out_of_five_win_the_median():
    """The other side of the same limit, stated: a poisoned majority is read as the truth."""
    spam = [f"spam{i}.com" for i in range(30)]
    pulls = [make_pull(f"g{i}", good_sites()) for i in range(2)] + [make_pull(f"b{i}", spam) for i in range(3)]
    reading = build_clean_reading(pulls)
    assert reading.pulls_kept == 5
    assert reading.position_of("spam0.com") == 1
    assert reading.position_of("good1.com") == NT50


def test_overlap_rule_needs_two_other_usable_pulls():
    pulls = [
        make_pull("a", good_sites()),
        make_pull("b", [f"other{i}.com" for i in range(30)]),
        make_pull("failed", [], ok=False),
    ]
    kept, dropped = filter_pulls(pulls)
    assert [p.pull_id for p in kept] == ["pull-a", "pull-b"]  # a failed pull is not an "other" pull
    assert [pid for pid, _ in dropped] == ["pull-failed"]
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
    result = read_clean(source, [SPEC], pulls=3, gap_seconds=0, sleep=clock.sleep, now=clock.now)
    reading = result.readings[0]
    assert reading.degraded and reading.pulls_total == 0 and reading.positions == ()
    assert len(result.failed_rounds) == 3 and clock.sleeps == []


def test_read_clean_records_a_round_that_breaks_the_source_contract():
    class Short:
        name = "short"

        def fetch(self, specs):
            return []

        def estimate_cost(self, count, depth=50):
            return 0.0

    clock = FakeClock()
    result = read_clean(Short(), [SPEC], pulls=2, gap_seconds=0, sleep=clock.sleep, now=clock.now)
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
        [SPEC], pulls=5, gap_seconds=0, sleep=clock.sleep, now=clock.now,
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
    result = read_clean(Repeating(), [SPEC, other], pulls=3, gap_seconds=0, sleep=clock.sleep, now=clock.now)
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
