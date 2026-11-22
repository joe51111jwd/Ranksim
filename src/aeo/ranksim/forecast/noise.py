"""Marker 1: how much two clean readings of one search, a day apart, agree.

What is REAL here: the readings and pulls compared are observed Google result
lists. The agreement numbers are statistics of those REAL readings. Nothing
here is PREDICTED, and nothing here is a forecast. Agreement between two
readings from the same city and device measures how repeatable the reader is.
It does not show the reader is right (the two readings share any bias).

Definitions (docs/plan/IMPLEMENTATION_SPEC.md section 3, OUR READING):

- Unit: one (search, site). The headline set holds the sites in the top 10 of
  either reading. The top-50 set holds the sites in the top 50 of either
  reading. A unit absent from one reading is counted there at 51 (NT50), so a
  site that enters or leaves is a disagreement unless the other position is 49
  or 50. That edge is part of the frozen rule and is tested.
- Agree: the two positions differ by 2 or less.
- Bands use the better of the two positions: 1-3, 4-10, 11-20, 21-50.
- Entry and exit: the share of top-10 units present in only one reading's top
  10 (and the same for the top 50).
- Grading rule agreement: reading A's position taken as the forecast of
  reading B under grading.RULE_V1, over top-50 units (spec s3: one direction,
  A as the forecast of B; rule v1 is not symmetric). The reverse direction and
  the average of both are printed beside it under their own names.
- Each (search, location, device) is one search of the frozen list and is
  counted once: a second pair for it is refused.
- Measured share (OUR READING): the headline counts only when at least
  MARKER1_MIN_MEASURED_SHARE of the requested searches gave a usable pair. A
  pair is skipped when a reading is degraded, and version 2 degrades exactly
  the searches whose pulls disagree (a cluster tie), so without this bar the
  headline could be measured on the few searches the reader agrees with
  itself on. Below the bar the headline rate is withheld (None, NOT
  MEASURABLE) and the rate on the measured searches is kept beside it.
- Pairs whose readings are degraded, more than 28 or less than 20 hours apart,
  or from a different location or device are skipped and listed with the reason.
- Intervals: bootstrap over searches (whole searches resampled). The Wilson
  interval is printed beside it and is right only if units were independent.

The raw single-pull baseline is measured on single pulls with the same
definitions. It is never a quoted number.

Every marker 1 report states the reader version and every filter threshold it
used (`reader.reader_settings`) and keeps the ablation (the same readings with
the filter after the structural drops switched off) beside the headline.
Marker 1 never passes reading A as the history of reading B: that would choose
B's pulls by their agreement with A and so inflate the agreement it measures.

Calibration (REAL data, clean era): `calibrate_on_dataforseo_history` reads
the DataForSEO history samples under
data/forecast/public/dataforseo_samples/google_history/serp_advanced with its
own small streaming reader and measures the false-alarm rates of the version 2
reader on result pages from May 2025 to April 2026, before Google served
altered pages to scrapers. `compare_versions_on_committed_pulls` puts reader
versions 1 and 2 side by side on the committed 19 Nov 2026 Serper pulls, with
the site lists of every search whose pulls split, so a person can check which
cluster each version kept.
"""

from __future__ import annotations

import gzip
import itertools
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from aeo.ranksim.site_history import site_key

from .asof import parse_utc, utc_iso
from .chain import canonical_json, sha256_text
from .config import EXCLUDED_SITES, NT50, TOP_DEPTH
from .grading import RULE_V1, fine_band_of
from .reader import (
    CONCENTRATION_MIN_SLOTS,
    DEFAULT_RULE_VERSION,
    LINK_MIN_SHARE,
    UBIQUITY_MIN_SLOTS,
    RoundSuspects,
    build_clean_reading,
    clean_reading_with_verdict,
    link_shares,
    load_serper_chunk_pulls,
    off_brand_concentration,
    pull_site_positions,
    reader_settings,
    round_suspects,
    ubiquity_slots,
)
from .stats import cluster_bootstrap_ratio, wilson
from .types import REAL, SYNTHETIC, CleanReading, OrganicHit, Pull, PullSpec, is_excluded_site

AGREE_WITHIN = 2
HEADLINE_DEPTH = 10
MARKER1_SEARCHES = 300
MARKER1_SAMPLE_SEED = 20261130
# OUR READING, not in the spec: the share of the frozen list that must give a usable pair before the
# headline counts. It equals the headline bar (0.92): a reader that cannot read more than 8% of the
# frozen searches twice has failed repeatability at the level of searches by the same margin.
MARKER1_MIN_MEASURED_SHARE = 0.92
MIN_GAP_HOURS = 20.0
MAX_GAP_HOURS = 28.0
AGREEMENT_BANDS = ("1-3", "4-10", "11-20", "21-50")

SKIP_DEGRADED = "degraded reading"
SKIP_GAP = "gap outside the window"
SKIP_PLACE = "different location or device"
SKIP_SEARCH = "different search"
SKIP_NOT_OK = "raw pull not ok"

COMMITTED_DAY = "2026-11-19"
COMMITTED_LABELS = ("pull-a", "pull-b", "pull-c")
# The committed Serper pulls asked for the top 10. The dry run reads them at that depth on purpose
# (its label says "Top 10 only"); the reader would otherwise mark every such reading shallow_depth.
COMMITTED_DEPTH = HEADLINE_DEPTH
DRY_RUN_LABEL = (
    "DRY RUN ON COMMITTED PULLS. NOT MARKER 1. Top 10 only. No pinned city and no device. "
    "Same day, three Serper pulls about 35 to 40 minutes apart, not a day apart. "
    "The searches were chosen for a client. Rows for excluded sites are dropped."
)


@dataclass(frozen=True)
class AgreementRow:
    """One (search, site) unit compared between two readings. Absent is NT50 (51)."""

    search: str
    site: str
    position_a: int
    position_b: int
    difference: int
    agree: bool
    top10_unit: bool
    top50_unit: bool
    entry_exit_top10: bool
    entry_exit_top50: bool
    band: str
    grading_hit_a_as_forecast: bool
    grading_hit_b_as_forecast: bool


def _reading_positions(reading: CleanReading) -> dict[str, int]:
    return {
        entry.site: entry.position
        for entry in reading.positions
        if entry.position <= TOP_DEPTH and not is_excluded_site(entry.site)
    }


def _rows(search: str, first: Mapping[str, int], second: Mapping[str, int]) -> list[AgreementRow]:
    rows: list[AgreementRow] = []
    for site in sorted(set(first) | set(second)):
        if is_excluded_site(site):
            continue
        a = int(first.get(site, NT50))
        b = int(second.get(site, NT50))
        if a > TOP_DEPTH and b > TOP_DEPTH:
            continue
        a_top10, b_top10 = a <= HEADLINE_DEPTH, b <= HEADLINE_DEPTH
        a_top50, b_top50 = a <= TOP_DEPTH, b <= TOP_DEPTH
        rows.append(
            AgreementRow(
                search=search,
                site=site,
                position_a=a,
                position_b=b,
                difference=abs(a - b),
                agree=abs(a - b) <= AGREE_WITHIN,
                top10_unit=a_top10 or b_top10,
                top50_unit=True,
                entry_exit_top10=a_top10 != b_top10,
                entry_exit_top50=a_top50 != b_top50,
                band=fine_band_of(min(a, b)),
                grading_hit_a_as_forecast=RULE_V1.is_hit(a, b),
                grading_hit_b_as_forecast=RULE_V1.is_hit(b, a),
            )
        )
    return rows


def agreement(a: CleanReading, b: CleanReading) -> list[AgreementRow]:
    """Per-unit rows for one search: every site in the top 50 of either reading.

    `top10_unit` marks the headline set. Raises ValueError when the readings are of different searches.
    """
    if a.search != b.search:
        raise ValueError(f"readings of different searches: {a.search!r} and {b.search!r}")
    return _rows(a.search, _reading_positions(a), _reading_positions(b))


def single_pull_agreement(a: Pull, b: Pull) -> list[AgreementRow]:
    """The same rows for two single raw pulls (the measured baseline)."""
    if a.spec.search != b.spec.search:
        raise ValueError(f"pulls of different searches: {a.spec.search!r} and {b.spec.search!r}")
    if not (a.ok and b.ok):
        raise ValueError("a failed pull has no positions to compare")
    return _rows(a.spec.search, pull_site_positions(a), pull_site_positions(b))


# ---------------------------------------------------------------------------
# Summaries


def _ratio_block(values: Sequence[float], clusters: Sequence[str]) -> dict[str, Any]:
    total = len(values)
    got = float(sum(values))
    low, high = cluster_bootstrap_ratio(list(values), [1.0] * total, list(clusters))
    w_low, w_high = wilson(got, total)
    whole = all(value in (0.0, 1.0) for value in values)
    return {
        "count": total,
        "right": int(got) if whole else got,
        "rate": got / total if total else None,
        "cluster_interval": [low, high],
        "wilson_interval_if_independent": [w_low, w_high],
    }


def _agree_block(rows: Sequence[AgreementRow]) -> dict[str, Any]:
    return _ratio_block([1.0 if row.agree else 0.0 for row in rows], [row.search for row in rows])


def summarize_rows(rows: Sequence[AgreementRow]) -> dict[str, Any]:
    """Headline, top 50, bands, entry and exit, and grading-rule agreement for a set of rows."""
    top10 = [row for row in rows if row.top10_unit]
    top50 = [row for row in rows if row.top50_unit]
    by_band = {band: _agree_block([row for row in top50 if row.band == band]) for band in AGREEMENT_BANDS}
    moved10 = sum(1 for row in top10 if row.entry_exit_top10)
    moved50 = sum(1 for row in top50 if row.entry_exit_top50)
    clusters50 = [row.search for row in top50]
    grading = [1.0 if row.grading_hit_a_as_forecast else 0.0 for row in top50]
    reverse = [1.0 if row.grading_hit_b_as_forecast else 0.0 for row in top50]
    both = [(a + b) / 2.0 for a, b in zip(grading, reverse)]
    return {
        "headline": _agree_block(top10),
        "top50": _agree_block(top50),
        "by_band": by_band,
        "entry_exit": {
            "top10": {"count": len(top10), "in_one_reading_only": moved10, "rate": moved10 / len(top10) if top10 else None},
            "top50": {"count": len(top50), "in_one_reading_only": moved50, "rate": moved50 / len(top50) if top50 else None},
        },
        "grading_rule_agreement": {
            "rule": RULE_V1.version,
            "rule_hash": RULE_V1.rule_hash(),
            "direction": "reading A taken as the forecast of reading B",
            **_ratio_block(grading, clusters50),
        },
        "grading_rule_agreement_b_as_forecast_of_a": {
            "rule": RULE_V1.version,
            "direction": "reading B taken as the forecast of reading A (extra, not the spec's figure)",
            **_ratio_block(reverse, clusters50),
        },
        "grading_rule_agreement_both_directions_averaged": {
            "rule": RULE_V1.version,
            "direction": "mean of both directions (extra, not the spec's figure)",
            **_ratio_block(both, clusters50),
        },
        "present_in_both_top10": _agree_block([row for row in top10 if not row.entry_exit_top10]),
        "searches": len({row.search for row in rows}),
    }


def _gap_hours(first: str, second: str) -> float:
    return abs((parse_utc(second) - parse_utc(first)).total_seconds()) / 3600.0


def _pair_problem(
    search_a: str,
    search_b: str,
    place_a: tuple[int, str],
    place_b: tuple[int, str],
    gap_hours: float,
    *,
    min_gap_hours: float,
    max_gap_hours: float,
    enforce_gap: bool,
) -> str | None:
    if search_a != search_b:
        return SKIP_SEARCH
    if place_a != place_b:
        return SKIP_PLACE
    if enforce_gap and not (min_gap_hours <= gap_hours <= max_gap_hours):
        return f"{SKIP_GAP}: {gap_hours:.2f} hours, window {min_gap_hours:g} to {max_gap_hours:g}"
    return None


def _skip_entry(search: str, reason: str) -> dict[str, str]:
    return {"search": search, "reason": reason}


def _reading_pairs_rows(
    pairs: Sequence[tuple[CleanReading, CleanReading]], **window: Any
) -> tuple[list[AgreementRow], int, list[dict[str, str]]]:
    rows: list[AgreementRow] = []
    used = 0
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for a, b in pairs:
        key = (a.search, int(a.location_code), a.device)
        if key in seen:
            raise ValueError(
                f"a second pair of readings for {a.search!r} ({a.location_code}, {a.device}). Each search of the frozen "
                "list is counted once: a unit is one (search, site)."
            )
        seen.add(key)
        if a.degraded or b.degraded:
            skipped.append(_skip_entry(a.search, SKIP_DEGRADED))
            continue
        problem = _pair_problem(
            a.search, b.search, (a.location_code, a.device), (b.location_code, b.device),
            _gap_hours(a.taken_at, b.taken_at), **window,
        )
        if problem:
            skipped.append(_skip_entry(a.search, problem))
            continue
        rows.extend(agreement(a, b))
        used += 1
    return rows, used, skipped


def _raw_pairs_rows(pairs: Sequence[tuple[Pull, Pull]], **window: Any) -> tuple[list[AgreementRow], int, list[dict[str, str]]]:
    rows: list[AgreementRow] = []
    used = 0
    skipped: list[dict[str, str]] = []
    for a, b in pairs:
        if not (a.ok and b.ok):
            skipped.append(_skip_entry(a.spec.search, SKIP_NOT_OK))
            continue
        problem = _pair_problem(
            a.spec.search, b.spec.search, (a.spec.location_code, a.spec.device), (b.spec.location_code, b.spec.device),
            _gap_hours(a.completed_at, b.completed_at), **window,
        )
        if problem:
            skipped.append(_skip_entry(a.spec.search, problem))
            continue
        rows.extend(single_pull_agreement(a, b))
        used += 1
    return rows, used, skipped


def _skip_counts(skipped: Sequence[dict[str, str]]) -> dict[str, int]:
    return dict(sorted(Counter(entry["reason"].split(":")[0] for entry in skipped).items()))


def agreement_report(
    pairs: Sequence[tuple[CleanReading, CleanReading]],
    *,
    raw_pairs: Sequence[tuple[Pull, Pull]] | None = None,
    no_overlap_pairs: Sequence[tuple[CleanReading, CleanReading]] | None = None,
    min_gap_hours: float = MIN_GAP_HOURS,
    max_gap_hours: float = MAX_GAP_HOURS,
    enforce_gap: bool = True,
    reader: Mapping[str, Any] | None = None,
    searches_requested: int | None = None,
) -> dict[str, Any]:
    """The marker 1 report for pairs of clean readings (A, B) of the same searches.

    `searches_requested` is the size of the frozen list (300 for marker 1); by
    default the number of pairs given. A caller whose frozen list holds searches
    with no pair at all must pass it, so they count as not measured. When fewer
    than MARKER1_MIN_MEASURED_SHARE of them gave a usable pair, `headline.rate`
    is None (NOT MEASURABLE) and the rate on the measured searches is kept as
    `headline.rate_on_measured_searches_only`. A second pair for one search
    raises ValueError.

    `raw_pairs` are single raw pulls of the same searches: the measured
    baseline. `no_overlap_pairs` are the same readings rebuilt with the
    filter after the structural drops switched off: the ablation. `reader`
    names the reader version and filter settings the readings were built with
    (`reader.reader_settings`); the report prints it as given. Degraded
    readings are never used.
    """
    if min_gap_hours > max_gap_hours:
        raise ValueError("min_gap_hours is above max_gap_hours")
    window = {"min_gap_hours": min_gap_hours, "max_gap_hours": max_gap_hours, "enforce_gap": enforce_gap}
    rows, used, skipped = _reading_pairs_rows(pairs, **window)
    summary = summarize_rows(rows)
    requested = len(pairs) if searches_requested is None else int(searches_requested)
    if requested < len(pairs):
        raise ValueError(f"searches_requested {requested} is below the {len(pairs)} pairs given")
    measured_share = used / requested if requested else None
    headline = dict(summary["headline"])
    withheld: str | None = None
    if measured_share is None or measured_share < MARKER1_MIN_MEASURED_SHARE:
        withheld = (
            f"NOT MEASURABLE: {used} of {requested} searches gave a usable pair, below the "
            f"{MARKER1_MIN_MEASURED_SHARE:g} share needed. Skipped searches (degraded readings, gaps, no pair) "
            "are the ones the reader could not read twice, so a rate on the rest would flatter it."
        )
        headline["rate_on_measured_searches_only"] = headline["rate"]
        headline["rate"] = None
        headline["not_measurable"] = withheld

    raw_block: dict[str, Any] | None = None
    if raw_pairs is not None:
        raw_rows, raw_used, raw_skipped = _raw_pairs_rows(raw_pairs, **window)
        raw_summary = summarize_rows(raw_rows)
        raw_block = {
            "headline": raw_summary["headline"],
            "top50": raw_summary["top50"],
            "by_band": raw_summary["by_band"],
            "entry_exit": raw_summary["entry_exit"],
            "present_in_both_top10": raw_summary["present_in_both_top10"],
            "pairs_used": raw_used,
            "pairs_skipped": raw_skipped,
            "pairs_skipped_by_reason": _skip_counts(raw_skipped),
            "searches": raw_summary["searches"],
        }

    ablation: dict[str, Any] | None = None
    if no_overlap_pairs is not None:
        ab_rows, ab_used, ab_skipped = _reading_pairs_rows(no_overlap_pairs, **window)
        ab_summary = summarize_rows(ab_rows)
        ablation = {
            "headline": ab_summary["headline"],
            "top50": ab_summary["top50"],
            "pairs_used": ab_used,
            "pairs_skipped_by_reason": _skip_counts(ab_skipped),
            "searches": ab_summary["searches"],
            "note": "Same readings with the filter after the structural drops switched off.",
        }

    synthetic = any(reading.label == SYNTHETIC for pair in pairs for reading in pair)
    return {
        "label": SYNTHETIC if synthetic else REAL,
        "what": "Agreement of two clean readings of each search. Repeatability of the reader, not its accuracy.",
        "headline": headline,
        "top50": summary["top50"],
        "by_band": summary["by_band"],
        "entry_exit": summary["entry_exit"],
        "grading_rule_agreement": summary["grading_rule_agreement"],
        "grading_rule_agreement_b_as_forecast_of_a": summary["grading_rule_agreement_b_as_forecast_of_a"],
        "grading_rule_agreement_both_directions_averaged": summary["grading_rule_agreement_both_directions_averaged"],
        "present_in_both_top10": summary["present_in_both_top10"],
        "raw_single_pull_baseline": raw_block,
        "ablation_no_overlap_rule": ablation,
        "headline_beside_ablation": {
            "filter_on": headline["rate"],
            "filter_off": ablation["headline"]["rate"] if ablation else None,
            "raw_single_pull": raw_block["headline"]["rate"] if raw_block else None,
        },
        "reader": dict(reader) if reader is not None else None,
        "pairs_used": used,
        "pairs_skipped": skipped,
        "pairs_skipped_by_reason": _skip_counts(skipped),
        "searches": summary["searches"],
        "searches_measured": used,
        "searches_requested_for_share": requested,
        "measured_share": measured_share,
        "min_measured_share": MARKER1_MIN_MEASURED_SHARE,
        "measured_line": f"measured {used} of {requested} searches",
        "definitions": {
            "unit": "(search, site) in the top 10 of either reading; also reported over the top 50",
            "absent": NT50,
            "agree_within": AGREE_WITHIN,
            "gap_hours": [min_gap_hours, max_gap_hours] if enforce_gap else None,
            "interval": "bootstrap over searches; Wilson only if units were independent",
            "bands_from": "the better of the two positions",
        },
    }


def _ok_in_order(pulls: Sequence[Pull]) -> list[Pull]:
    return sorted((pull for pull in pulls if pull.ok), key=lambda pull: (parse_utc(pull.requested_at), pull.pull_id))


def marker1_report_from_pulls(
    reading_pulls: Sequence[tuple[Sequence[Pull], Sequence[Pull]]],
    *,
    min_gap_hours: float = MIN_GAP_HOURS,
    max_gap_hours: float = MAX_GAP_HOURS,
    enforce_gap: bool = True,
    version: int = DEFAULT_RULE_VERSION,
    suspects_a: RoundSuspects | Iterable[RoundSuspects] | None = None,
    suspects_b: RoundSuspects | Iterable[RoundSuspects] | None = None,
    history: Mapping[str, CleanReading] | None = None,
    searches_requested: int | None = None,
) -> dict[str, Any]:
    """Build both readings of each search from their pulls, with and without the filter, and report.

    `version` is the reader version. `suspects_a` and `suspects_b` are the
    round suspects of the rounds of reading A and of reading B (version 2,
    signal c), one RoundSuspects per round. When they are None they are read
    from the pulls given, per round: a reading's k-th pull (in requested
    order) of every search was taken in round k, as `read_clean` takes them,
    and each pull is judged against its own round only (spec s3, "the same
    round"). `searches_requested` is passed to `agreement_report`. `history` maps a search to an earlier clean reading taken
    before reading A's first pull. The same history is passed to both A and B,
    so reading A is never the history of reading B. The raw baseline pairs the
    i-th OK pull of reading A with the i-th OK pull of reading B (in requested
    order), so every single pull is used once.
    """
    if suspects_a is None:
        suspects_a = _suspects_by_round([first for first, _ in reading_pulls])
    if suspects_b is None:
        suspects_b = _suspects_by_round([second for _, second in reading_pulls])
    pairs: list[tuple[CleanReading, CleanReading]] = []
    plain: list[tuple[CleanReading, CleanReading]] = []
    raw: list[tuple[Pull, Pull]] = []
    used_history = 0
    for first, second in reading_pulls:
        earlier = (history or {}).get(first[0].spec.search) if first and int(version) != 1 else None
        used_history += earlier is not None
        pairs.append((
            build_clean_reading(first, version=version, suspects=suspects_a, history=earlier),
            build_clean_reading(second, version=version, suspects=suspects_b, history=earlier),
        ))
        plain.append((
            build_clean_reading(first, overlap_rule=False, version=version),
            build_clean_reading(second, overlap_rule=False, version=version),
        ))
        raw.extend(zip(_ok_in_order(first), _ok_in_order(second)))
    settings = reader_settings(version=version)
    settings["history_used"] = used_history > 0
    settings["searches_with_history"] = used_history
    settings["ablation"] = reader_settings(version=version, overlap_rule=False)
    return agreement_report(
        pairs,
        raw_pairs=raw,
        no_overlap_pairs=plain,
        min_gap_hours=min_gap_hours,
        max_gap_hours=max_gap_hours,
        enforce_gap=enforce_gap,
        reader=settings,
        searches_requested=searches_requested,
    )


def _suspects_by_round(readings_pulls: Sequence[Sequence[Pull]]) -> list[RoundSuspects]:
    """Round suspects per round, where a reading's k-th pull (requested order) belongs to round k."""
    rounds: dict[int, list[Pull]] = defaultdict(list)
    for pulls in readings_pulls:
        ordered = sorted(pulls, key=lambda pull: (parse_utc(pull.requested_at), pull.pull_id))
        for index, pull in enumerate(ordered):
            rounds[index].append(pull)
    return [round_suspects(rounds[index]) for index in sorted(rounds)]


# ---------------------------------------------------------------------------
# The frozen search sample


@dataclass(frozen=True)
class SearchSample:
    """A frozen, seeded, stratified sample of searches. `sha256` covers the seed, n, strata and the list."""

    searches: tuple[str, ...]
    strata: tuple[tuple[str, str], ...]
    seed: int
    requested: int
    candidates: int
    per_stratum: tuple[tuple[str, int], ...]
    sha256: str


def sample_marker1_searches(
    candidates: Sequence[str],
    n: int = MARKER1_SEARCHES,
    *,
    seed: int = MARKER1_SAMPLE_SEED,
    strata: Mapping[str, str] | None = None,
) -> SearchSample:
    """Draw `n` searches, stratified by `strata` (search -> stratum), proportional with largest remainders.

    The result does not depend on the order of `candidates`. Duplicates are
    folded. Every candidate must have a stratum when `strata` is given. When
    there are n or fewer candidates, all are taken.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    unique = sorted({str(text).strip() for text in candidates if str(text).strip()})
    if not unique:
        raise ValueError("no candidate searches")
    if strata is None:
        assignment = {search: "all" for search in unique}
    else:
        missing = [search for search in unique if search not in strata]
        if missing:
            raise ValueError(f"{len(missing)} candidates have no stratum, for example {missing[0]!r}")
        assignment = {search: str(strata[search]) for search in unique}
    groups: dict[str, list[str]] = {}
    for search in unique:
        groups.setdefault(assignment[search], []).append(search)
    names = sorted(groups)
    take = min(n, len(unique))
    exact = {name: take * len(groups[name]) / len(unique) for name in names}
    quota = {name: int(exact[name]) for name in names}
    left = take - sum(quota.values())
    for name in sorted(names, key=lambda item: (-(exact[item] - quota[item]), item))[:left]:
        quota[name] += 1

    rng = random.Random(seed)
    chosen: list[tuple[str, str]] = []
    for name in names:
        picked = rng.sample(groups[name], quota[name])
        chosen.extend((name, search) for search in sorted(picked))
    searches = tuple(search for _, search in chosen)
    payload = {
        "seed": seed,
        "requested": n,
        "candidates": unique,
        "strata": {search: assignment[search] for search in unique},
        "searches": list(searches),
    }
    return SearchSample(
        searches=searches,
        strata=tuple(chosen),
        seed=seed,
        requested=n,
        candidates=len(unique),
        per_stratum=tuple((name, quota[name]) for name in names),
        sha256=sha256_text(canonical_json(payload)),
    )


# ---------------------------------------------------------------------------
# Dry run on the committed Serper pulls


def _by_search(pulls: Sequence[Pull]) -> dict[str, Pull]:
    out: dict[str, Pull] = {}
    for pull in pulls:
        if pull.spec.search in out:
            raise ValueError(f"search {pull.spec.search!r} appears twice in one pull set")
        out[pull.spec.search] = pull
    return out


def _brief(block: dict[str, Any]) -> dict[str, Any]:
    return {key: block[key] for key in ("count", "right", "rate", "cluster_interval")}


def _absent_as_11_block(rows: Sequence[AgreementRow]) -> dict[str, Any]:
    """Top-10 union agreement with absent counted as 11 (the old top-10 repo rule). Only for comparison."""
    top10 = [row for row in rows if row.top10_unit]
    values = [
        1.0 if abs(min(row.position_a, HEADLINE_DEPTH + 1) - min(row.position_b, HEADLINE_DEPTH + 1)) <= AGREE_WITHIN else 0.0
        for row in top10
    ]
    return _brief(_ratio_block(values, [row.search for row in top10]))


def _load_committed(root: str | Path, day: str, labels: Sequence[str]) -> tuple[Path, dict[str, list[Pull]], dict[str, dict[str, Pull]], list[str]]:
    base = Path(root) / "evals" / "claude" / "serp" / day
    sets = {label: load_serper_chunk_pulls(base / label) for label in labels}
    indexed = {label: _by_search(pulls) for label, pulls in sets.items()}
    searches = sorted(set.intersection(*(set(found) for found in indexed.values())))
    return base, sets, indexed, searches


def dry_run_on_committed_pulls(
    root: str | Path = ".",
    *,
    day: str = COMMITTED_DAY,
    labels: Sequence[str] = COMMITTED_LABELS,
    version: int = DEFAULT_RULE_VERSION,
) -> dict[str, Any]:
    """What the committed pulls can honestly show. It is NOT marker 1.

    Loads `<root>/evals/claude/serp/<day>/<label>/chunk-NN.json.gz` for each
    label, then measures with this module's definitions:

    - single pull against single pull, every pair of labels, pooled;
    - leave one out: each pull against the clean reading of the other two
      (a 2-pull reading is degraded under the spec and is used here only as a
      stability check);
    - the 3-pull clean reading against each 2-pull reading;
    - how many pulls the poisoned-page filter dropped.

    Readings are built with reader `version`. Each label is one pull round, so
    for version 2 the round suspects are read per label, and each pull is
    judged against the suspects of its own label.
    """
    base, sets, indexed, searches = _load_committed(root, day, labels)
    failed = {label: sum(1 for pull in pulls if not pull.ok) for label, pulls in sets.items()}
    suspects = [round_suspects(sets[label]) for label in labels]

    window = {"min_gap_hours": 0.0, "max_gap_hours": 0.0, "enforce_gap": False}
    pair_results: dict[str, Any] = {}
    pooled_raw: list[tuple[Pull, Pull]] = []
    identical: dict[str, int] = {}
    for x, y in itertools.combinations(labels, 2):
        chosen = [(indexed[x][s], indexed[y][s]) for s in searches]
        pooled_raw.extend(chosen)
        rows, _, _ = _raw_pairs_rows(chosen, **window)
        summary = summarize_rows(rows)
        pair_results[f"{x} vs {y}"] = {
            "headline": _brief(summary["headline"]),
            "present_in_both_top10": _brief(summary["present_in_both_top10"]),
        }
        identical[f"{x} vs {y}"] = sum(
            1 for a, b in chosen if a.ok and b.ok and [h.url for h in a.hits] == [h.url for h in b.hits]
        )
    raw_rows, raw_used, raw_skipped = _raw_pairs_rows(pooled_raw, **window)
    raw_summary = summarize_rows(raw_rows)

    loo_rows: list[AgreementRow] = []
    loo_kept_rows: list[AgreementRow] = []
    three_two_rows: list[AgreementRow] = []
    drops: Counter[str] = Counter()
    degraded_three = 0
    empty_rest = 0
    dropped_plain: Counter[str] = Counter()
    for search in searches:
        group = [indexed[label][search] for label in labels]
        full = build_clean_reading(group, version=version, suspects=suspects, min_depth=COMMITTED_DEPTH)
        degraded_three += int(full.degraded)
        drops.update(reason.split(":")[0] for _, reason in full.dropped)
        plain = build_clean_reading(group, overlap_rule=False, version=version, min_depth=COMMITTED_DEPTH)
        dropped_plain.update(reason.split(":")[0] for _, reason in plain.dropped)
        for held in range(len(group)):
            if not group[held].ok:
                continue
            rest = build_clean_reading([pull for i, pull in enumerate(group) if i != held], version=version, suspects=suspects,
                                       min_depth=COMMITTED_DEPTH)
            empty_rest += int(rest.pulls_kept == 0)
            held_rows = _rows(search, pull_site_positions(group[held]), _reading_positions(rest))
            loo_rows.extend(held_rows)
            if rest.pulls_kept > 0:
                loo_kept_rows.extend(held_rows)
            three_two_rows.extend(_rows(search, _reading_positions(full), _reading_positions(rest)))
    loo = summarize_rows(loo_rows)
    loo_kept = summarize_rows(loo_kept_rows)
    three_two = summarize_rows(three_two_rows)

    return {
        "label": DRY_RUN_LABEL,
        "is_marker_1": False,
        "reader": reader_settings(version=version),
        "inputs": {
            "directory": str(base),
            "labels": list(labels),
            "pulls_loaded": {label: len(pulls) for label, pulls in sets.items()},
            "pulls_failed": failed,
            "searches_in_all_pulls": len(searches),
            "excluded_sites_dropped": sorted(EXCLUDED_SITES),
            "identical_ordered_url_lists": identical,
        },
        "single_pull_vs_single_pull": {
            "pairs_used": raw_used,
            "pairs_skipped_by_reason": _skip_counts(raw_skipped),
            "headline_top10_union_absent_51": raw_summary["headline"],
            "present_in_both_top10": raw_summary["present_in_both_top10"],
            "comparison_only_top10_union_absent_11": _absent_as_11_block(raw_rows),
            "entry_exit_top10": raw_summary["entry_exit"]["top10"],
            "by_band": {band: _brief(raw_summary["by_band"][band]) for band in ("1-3", "4-10")},
            "per_pair": pair_results,
        },
        "leave_one_out_pull_vs_other_two": {
            "headline_top10_union_absent_51": loo["headline"],
            "present_in_both_top10": loo["present_in_both_top10"],
            "comparison_only_top10_union_absent_11": _absent_as_11_block(loo_rows),
            "two_pull_readings_with_no_kept_pull": empty_rest,
            "headline_only_where_the_two_pull_reading_kept_a_pull": loo_kept["headline"],
            "note": "A two-pull reading with no kept pull (a tie) has no positions, so every held-out site counts as a disagreement in the first block.",
        },
        "three_pull_reading_vs_two_pull_reading": {
            "headline_top10_union_absent_51": three_two["headline"],
            "present_in_both_top10": three_two["present_in_both_top10"],
            "note": "The two readings share pulls, so this is an upper bound on stability, not a test.",
        },
        "filter_on_three_pull_readings": {
            "readings": len(searches),
            "degraded": degraded_three,
            "dropped_by_reason": dict(sorted(drops.items())),
            "dropped_by_reason_without_overlap_rule": dict(sorted(dropped_plain.items())),
        },
    }


def _short_label(label: str) -> str:
    return label.rsplit("-", 1)[-1]


def compare_versions_on_committed_pulls(
    root: str | Path = ".", *, day: str = COMMITTED_DAY, labels: Sequence[str] = COMMITTED_LABELS
) -> dict[str, Any]:
    """Reader versions 1 and 2 side by side on the committed pulls. It is NOT marker 1.

    For both versions: pulls dropped, readings degraded, the leave-one-out and
    single-pull numbers of `dry_run_on_committed_pulls`. For every search whose
    pulls split into more than one version 2 cluster, or where version 1
    dropped a pull: the site list of every pull, the clusters, which pulls each
    version kept, and the signal that decided. Whether the kept pulls are the
    clean ones is left to the person reading the lists. The code does not
    decide it.
    """
    _, sets, indexed, searches = _load_committed(root, day, labels)
    suspects = [round_suspects(sets[label]) for label in labels]
    names = {pull.pull_id: _short_label(label) for label in labels for pull in sets[label]}
    splits: list[dict[str, Any]] = []
    same_choice = 0
    for search in searches:
        group = [indexed[label][search] for label in labels]
        first = build_clean_reading(group, version=1, min_depth=COMMITTED_DEPTH)
        second, verdict = clean_reading_with_verdict(group, version=2, suspects=suspects, min_depth=COMMITTED_DEPTH)
        if (verdict is None or len(verdict.clusters) < 2) and first.pulls_kept == first.pulls_total:
            continue
        kept_v1 = [names[pid] for pid in first.pull_ids]
        kept_v2 = [names[pid] for pid in second.pull_ids]
        same_choice += int(kept_v1 == kept_v2)
        splits.append({
            "search": search,
            "pulls": {names[pull.pull_id]: [hit.site for hit in sorted(pull.hits, key=lambda h: h.rank)] for pull in group},
            "clusters_v2": [[names[pid] for pid in cluster] for cluster in (verdict.clusters if verdict else ())],
            "kept_v1": kept_v1,
            "kept_v2": kept_v2,
            "decided_by_v2": verdict.decided_by if verdict else None,
            "tie_v2": bool(verdict.tie) if verdict else False,
            "notes_v2": list(verdict.notes) if verdict else [],
            "degraded_v1": first.degraded,
            "degraded_v2": second.degraded,
        })

    def summary(version: int) -> dict[str, Any]:
        run = dry_run_on_committed_pulls(root, day=day, labels=labels, version=version)
        filtered = run["filter_on_three_pull_readings"]
        return {
            "reader": run["reader"],
            "pulls_dropped": sum(filtered["dropped_by_reason"].values()),
            "dropped_by_reason": filtered["dropped_by_reason"],
            "readings_degraded": filtered["degraded"],
            "readings": filtered["readings"],
            "leave_one_out": {
                "headline_top10_union_absent_51": _brief(run["leave_one_out_pull_vs_other_two"]["headline_top10_union_absent_51"]),
                "present_in_both_top10": _brief(run["leave_one_out_pull_vs_other_two"]["present_in_both_top10"]),
                "two_pull_readings_with_no_kept_pull": run["leave_one_out_pull_vs_other_two"]["two_pull_readings_with_no_kept_pull"],
                "headline_only_where_the_two_pull_reading_kept_a_pull": _brief(
                    run["leave_one_out_pull_vs_other_two"]["headline_only_where_the_two_pull_reading_kept_a_pull"]
                ),
            },
            "single_pull_vs_single_pull": {
                "headline_top10_union_absent_51": _brief(run["single_pull_vs_single_pull"]["headline_top10_union_absent_51"]),
                "present_in_both_top10": _brief(run["single_pull_vs_single_pull"]["present_in_both_top10"]),
            },
        }

    return {
        "label": DRY_RUN_LABEL,
        "is_marker_1": False,
        "round_suspects": {label: sorted(item.sites()) for label, item in zip(labels, suspects)},
        "version_1": summary(1),
        "version_2": summary(2),
        "searches_with_a_split_or_a_v1_drop": len(splits),
        "same_pulls_kept_by_both": same_choice,
        "splits": splits,
    }


# ---------------------------------------------------------------------------
# Calibration on the clean-era DataForSEO history samples

DATAFORSEO_HISTORY_DIR = "data/forecast/public/dataforseo_samples/google_history/serp_advanced"
CALIBRATION_CONCENTRATION_SLOTS = (3, 4, 5)
CALIBRATION_UBIQUITY_SLOTS = (3, 4, 5)
CALIBRATION_LINK_SHARES = (0.3, 0.4, 0.5, 0.6)
CALIBRATION_MAX_GAP_HOURS = 24.0
CALIBRATION_DEPTHS = (TOP_DEPTH, HEADLINE_DEPTH)
HISTORY_PROVIDER = "dataforseo-history-sample"


@dataclass(frozen=True)
class HistoryPage:
    """One dated clean-era result page from a DataForSEO history sample. REAL.

    `organic` holds (rank_group, site, url) for organic items only, best first,
    with excluded sites dropped. `time_update` is the provider's UTC time.
    """

    locale: str
    keyword: str
    time_update: str
    organic: tuple[tuple[int, str, str], ...]


def iter_dataforseo_history_pages(
    directory: str | Path = DATAFORSEO_HISTORY_DIR, *, locales: Sequence[str] | None = None
) -> Iterator[HistoryPage]:
    """Stream every dated result page of the history samples, one file and one line at a time.

    Each file is `<CC>_<lang>.json.gz`, one keyword per line with
    `serp_info_history`, a list of dated pages whose items of type "organic"
    carry rank_group, domain, main_domain and url. The site is the registrable
    domain of the url (main_domain when the url has none). Rows for excluded
    sites are dropped. A page without a time is skipped, because it cannot be
    dated. Files are read in name order, so the stream is deterministic.
    """
    folder = Path(directory)
    files = sorted(folder.glob("*.json.gz"))
    if not files:
        raise FileNotFoundError(f"no *.json.gz files in {folder}")
    wanted = set(locales) if locales is not None else None
    for path in files:
        locale = path.name[: -len(".json.gz")]
        if wanted is not None and locale not in wanted:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                keyword = str(record.get("keyword") or "")
                for page in record.get("serp_info_history") or []:
                    stamp = page.get("time_update")
                    if not keyword or not stamp:
                        continue
                    organic: list[tuple[int, str, str]] = []
                    for item in page.get("serp") or []:
                        if not isinstance(item, dict) or item.get("type") != "organic":
                            continue
                        try:
                            rank = int(item.get("rank_group"))
                        except (TypeError, ValueError):
                            continue
                        url = str(item.get("url") or "")
                        site = site_key(url) if url else ""
                        site = site or str(item.get("main_domain") or item.get("domain") or "").lower()
                        if rank < 1 or not site or is_excluded_site(site):
                            continue
                        organic.append((rank, site, url))
                    organic.sort(key=lambda entry: (entry[0], entry[2]))
                    yield HistoryPage(locale=locale, keyword=keyword, time_update=utc_iso(parse_utc(str(stamp))),
                                      organic=tuple(organic))


def history_page_pull(page: HistoryPage, *, depth: int = TOP_DEPTH) -> Pull:
    """A clean-era page as a `Pull`, cut to `depth`, so the reader's own functions judge it."""
    spec = PullSpec(search=page.keyword, location_code=0, language_code=page.locale.split("_")[-1], device="desktop",
                    depth=int(depth))
    hits = tuple(
        OrganicHit(rank=rank, url=url, domain=site, site=site) for rank, site, url in page.organic if rank <= depth
    )
    pull_id = "history-" + sha256_text(canonical_json([page.locale, page.keyword, page.time_update, depth]))[:24]
    return Pull(pull_id=pull_id, provider=HISTORY_PROVIDER, endpoint="dataforseo history sample", spec=spec,
                requested_at=page.time_update, completed_at=page.time_update, ok=True, hits=hits)


def _rate(count: int, total: int) -> dict[str, Any]:
    return {"count": count, "total": total, "rate": count / total if total else None}


def calibrate_on_dataforseo_history(
    directory: str | Path = DATAFORSEO_HISTORY_DIR,
    *,
    locales: Sequence[str] | None = None,
    concentration_slots: Sequence[int] = CALIBRATION_CONCENTRATION_SLOTS,
    ubiquity_slots_grid: Sequence[int] = CALIBRATION_UBIQUITY_SLOTS,
    link_shares_grid: Sequence[float] = CALIBRATION_LINK_SHARES,
    max_gap_hours: float = CALIBRATION_MAX_GAP_HOURS,
) -> dict[str, Any]:
    """False-alarm rates of the version 2 reader on clean-era pages (May 2025 to April 2026). REAL data.

    - Signal (b) alone: the share of pages where one off-brand site holds at
      least k of the top 10 slots, for each k, for English locales (`*_en`)
      and for all locales.
    - Splits: for re-pulls of one keyword in one locale less than
      `max_gap_hours` apart (consecutive pages), the share that would land in
      different clusters at each link share, at depth 50 (top 20 slots against
      the top 50) and at depth 10 (top 10 against top 10, like the Serper pulls).
    - Signal (c) alone: pages grouped into stand-in rounds of one locale and one
      calendar month (the samples have no real pull rounds; a month holds more
      searches than a round, so this overstates the suspects). The share of
      pages with at least k top-10 slots held by unrelated round suspects.

    Each locale is read and dropped before the next, so memory stays small.
    """
    concentration = {"en": Counter(), "all": Counter()}
    pages_total = {"en": 0, "all": 0}
    ubiquity = {"en": Counter(), "all": Counter()}
    split_counts: dict[int, Counter[float]] = {depth: Counter() for depth in CALIBRATION_DEPTHS}
    pair_total = {"en": 0, "all": 0}
    split_en: dict[int, Counter[float]] = {depth: Counter() for depth in CALIBRATION_DEPTHS}
    gaps: list[float] = []
    locales_read: list[str] = []
    first_time: str | None = None
    last_time: str | None = None

    current: str | None = None
    bucket: list[HistoryPage] = []

    def flush(pages: list[HistoryPage]) -> None:
        nonlocal first_time, last_time
        if not pages:
            return
        locale = pages[0].locale
        locales_read.append(locale)
        groups = ("all", "en") if locale.endswith("_en") else ("all",)
        top = [history_page_pull(page) for page in pages]
        for page, pull in zip(pages, top):
            first_time = page.time_update if first_time is None or page.time_update < first_time else first_time
            last_time = page.time_update if last_time is None or page.time_update > last_time else last_time
            if not pull.hits:
                continue
            slots = off_brand_concentration(pull)[1]
            for group in groups:
                pages_total[group] += 1
                for k in concentration_slots:
                    concentration[group][k] += int(slots >= k)
        by_month: dict[str, list[Pull]] = defaultdict(list)
        for page, pull in zip(pages, top):
            if pull.hits:
                by_month[page.time_update[:7]].append(pull)
        for month_pulls in by_month.values():
            suspects = round_suspects(month_pulls)
            for pull in month_pulls:
                held = ubiquity_slots(pull, suspects)
                for group in groups:
                    for k in ubiquity_slots_grid:
                        ubiquity[group][k] += int(held >= k)
        by_keyword: dict[str, list[HistoryPage]] = defaultdict(list)
        for page in pages:
            if page.organic:
                by_keyword[page.keyword].append(page)
        for keyword_pages in by_keyword.values():
            keyword_pages.sort(key=lambda page: page.time_update)
            for older, newer in zip(keyword_pages, keyword_pages[1:]):
                hours = (parse_utc(newer.time_update) - parse_utc(older.time_update)).total_seconds() / 3600.0
                if hours >= max_gap_hours:
                    continue
                gaps.append(hours)
                for group in groups:
                    pair_total[group] += 1
                for depth in CALIBRATION_DEPTHS:
                    a, b = history_page_pull(older, depth=depth), history_page_pull(newer, depth=depth)
                    share_a, share_b = link_shares(a, b)
                    low = min(share_a or 0.0, share_b or 0.0)
                    for threshold in link_shares_grid:
                        split = low < threshold
                        split_counts[depth][threshold] += int(split)
                        if "en" in groups:
                            split_en[depth][threshold] += int(split)

    for page in iter_dataforseo_history_pages(directory, locales=locales):
        if page.locale != current:
            flush(bucket)
            bucket = []
            current = page.locale
        bucket.append(page)
    flush(bucket)

    gaps.sort()
    return {
        "label": REAL,
        "what": "False-alarm rates of reader version 2 on clean-era result pages. Not accuracy.",
        "directory": str(directory),
        "locales_read": len(locales_read),
        "pages_dated_from": first_time,
        "pages_dated_to": last_time,
        "pages_with_organic_results": dict(pages_total),
        "off_brand_concentration_flag": {
            group: {str(k): _rate(concentration[group][k], pages_total[group]) for k in concentration_slots}
            for group in ("en", "all")
        },
        "round_ubiquity_flag_month_rounds": {
            group: {str(k): _rate(ubiquity[group][k], pages_total[group]) for k in ubiquity_slots_grid}
            for group in ("en", "all")
        },
        "repulls_under_max_gap": {
            "max_gap_hours": max_gap_hours,
            "pairs": dict(pair_total),
            "median_gap_hours": gaps[len(gaps) // 2] if gaps else None,
            "split_rate_by_link_share": {
                f"depth_{depth}": {
                    "all": {str(t): _rate(split_counts[depth][t], pair_total["all"]) for t in link_shares_grid},
                    "en": {str(t): _rate(split_en[depth][t], pair_total["en"]) for t in link_shares_grid},
                }
                for depth in CALIBRATION_DEPTHS
            },
        },
        "chosen": {
            "link_min_share": LINK_MIN_SHARE,
            "concentration_min_slots": CONCENTRATION_MIN_SLOTS,
            "ubiquity_min_slots": UBIQUITY_MIN_SLOTS,
        },
    }


def _print_json(result: Any) -> None:
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


def main(argv: Sequence[str]) -> int:
    """`noise [root]` prints the dry run. `noise compare [root]` puts reader versions side by side.
    `noise calibrate [directory] [locale ...]` measures the false-alarm rates on the clean-era samples.
    """
    args = list(argv)
    if args and args[0] == "compare":
        _print_json(compare_versions_on_committed_pulls(args[1] if len(args) > 1 else "."))
    elif args and args[0] == "calibrate":
        directory = args[1] if len(args) > 1 else DATAFORSEO_HISTORY_DIR
        _print_json(calibrate_on_dataforseo_history(directory, locales=args[2:] or None))
    else:
        _print_json(dry_run_on_committed_pulls(args[0] if args else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
