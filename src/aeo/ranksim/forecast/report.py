"""Stage reports: the numbers the markers are judged on, for ONE source and ONE stage at a time.

What is REAL: graded outcomes (a stored forecast compared with the clean
position about 90 days after launch) and every rate computed from them,
baselines included. What is PREDICTED: the forecasts themselves, counts of
forecasts made or not yet due, and where the points sit (for example how many
sit on 15). A synthetic source is labeled SYNTHETIC and is never accuracy.

Every report verifies the hash chains first and refuses on a break. Seed,
pilot, backtest and synthetic results are never pooled: there is one report
per source and a combined view refuses to exist. Rates on fewer than
config.MIN_COUNT_TO_PRINT_RATE (30) forecasts are printed as NOT MEASURABLE.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from .config import (
    CONFIRMED_STAGE,
    DIFFICULTY_TERCILES,
    LEAD_FLOOR_DAYS,
    MIN_COUNT_TO_PRINT_RATE,
    SOURCES,
    SPEC_BASELINES,
    STAGE_DAYS,
)
from .config import NT50
from .grading import BAND_PRIOR_ALL, RULE_V1, GradedCell, GradingRule, band_prior_points, fit_band_prior, headline
from .scoreboard import GradedRecord, PoolingRefused, Scoreboard, ScoreboardRefusal, mapping_points
from .types import REAL, SYNTHETIC

REPORT_MIN_COUNT = MIN_COUNT_TO_PRINT_RATE
# Computed here and nowhere else. band_prior is fitted here from `band_prior_training`, so it is
# known to come from training data only; a caller cannot hand in a band prior of its own.
RESERVED_BASELINES = ("always_nt50", "persistence", "band_prior")
BaselineSpec = Mapping[str, int] | Callable[[GradedCell], int]


def _rate(numer: int, denom: int) -> float | None:
    return numer / denom if denom else None


def _block(records: Sequence[GradedRecord], hits: Sequence[bool] | None = None) -> dict[str, Any]:
    values = list(hits) if hits is not None else [r.hit for r in records]
    count = len(values)
    right = sum(1 for v in values if v)
    return {"count": count, "right": right, "rate": _rate(right, count), "measurable": count >= REPORT_MIN_COUNT}


def _group(
    records: Sequence[GradedRecord], key: Callable[[GradedRecord], str], order: Sequence[str] = ()
) -> dict[str, Any]:
    """Blocks per group. Names in `order` come first in that order, the rest alphabetically."""
    groups: dict[str, list[GradedRecord]] = defaultdict(list)
    for record in records:
        groups[key(record)].append(record)
    rank = {name: index for index, name in enumerate(order)}
    names = sorted(groups, key=lambda name: (rank.get(name, len(rank)), name))
    return {name: _block(groups[name]) for name in names}


def combined_report(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """There is no combined view. Seed, pilot, backtest and synthetic are reported apart."""
    raise PoolingRefused("A combined report would pool sources. Run stage_report once per source.")


@dataclass(frozen=True)
class FittedBandPrior:
    """A band prior fitted elsewhere on training rows only, with the names of what it was fitted on.

    `table` maps a difficulty tercile (and grading.BAND_PRIOR_ALL) to one point. `sites` and
    `search_groups` are the training sites and search groups. `source` is one sentence saying where
    it came from; the report prints it. A fitting site graded in the same report is refused.
    """

    table: Mapping[str, int]
    sites: frozenset[str]
    search_groups: frozenset[str]
    source: str

    def checked_table(self, cells: Sequence[GradedCell]) -> dict[str, int]:
        table = {str(k): int(v) for k, v in self.table.items()}
        if BAND_PRIOR_ALL not in table or any(not 1 <= v <= NT50 for v in table.values()):
            raise ScoreboardRefusal(f"A fitted band prior needs the key {BAND_PRIOR_ALL!r} and points 1 to {NT50}.")
        overlap = sorted(self.sites & {c.site for c in cells})
        if overlap:
            raise ScoreboardRefusal(
                f"The band prior was fitted on {len(overlap)} site(s) that are graded here ({', '.join(overlap[:3])}). "
                "It is fitted on training data only."
            )
        return table


def _baseline_points(name: str, spec: BaselineSpec, records: Sequence[GradedRecord]) -> list[int]:
    if callable(spec):
        return [int(spec(record.cell)) for record in records]
    return mapping_points(spec, records, name)


def stage_report(
    scoreboard: Scoreboard,
    *,
    source: str,
    stage: str,
    as_of: date,
    extra_baselines: Mapping[str, BaselineSpec] | None = None,
    model_version: str | None = None,
    band_prior_training: Sequence[GradedCell] | None = None,
    band_prior_fitted: FittedBandPrior | None = None,
) -> dict[str, Any]:
    """Every number for one source and one stage as of a date, on the original grades.

    `extra_baselines` maps a name to either forecast id -> point, or a function of the cell.
    The site strength only baseline comes in here as "site_strength".
    always_nt50 and persistence are always added. The band prior is fitted here on
    `band_prior_training`, graded cells of the TRAINING split, or given already fitted as
    `band_prior_fitted` with the names of the sites and search groups it was fitted on (a model
    bundle's own training rows). Either way a fitting site that is in the headline is refused.
    The report lists the spec baselines it lacks under `baselines_missing`, and markers 3 to 5
    are never passed while one is missing.
    """
    _check_source_and_stage(source, stage)
    status = scoreboard.require_verified()

    fetched = scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)
    graded, not_held_out = held_out_records(fetched, source)
    late, short, records = _lead_filter(graded, stage)

    baselines: dict[str, list[int]] = {"persistence": scoreboard.persistence_points(records)}
    for name, spec in sorted((extra_baselines or {}).items()):
        if name in RESERVED_BASELINES:
            raise ScoreboardRefusal(f"The baseline name {name} is reserved. It is always computed here.")
        baselines[name] = _baseline_points(name, spec, records)
    return _assemble(
        records=records,
        short=short,
        late=late,
        not_held_out=not_held_out,
        baselines=baselines,
        band_prior_training=band_prior_training,
        band_prior_unit="site",
        band_prior_fitted=band_prior_fitted,
        rule=scoreboard.rule,
        source=source,
        stage=stage,
        as_of=as_of,
        model_version=records[0].model_version if records else model_version,
        chain=status.to_dict(),
        eligibility=scoreboard.eligibility(as_of),
        core_updates=[u for u in scoreboard.core_updates() if u["recorded_at"][:10] <= as_of.isoformat()],
        regrades=_regrade_block(scoreboard, source, stage, as_of, model_version),
        cluster_by="site",
        lead_key=None,
    )


def _check_source_and_stage(source: str, stage: str) -> None:
    if source not in SOURCES:
        raise PoolingRefused(f"Pick one source of {', '.join(SOURCES)}. A combined view is refused.")
    if stage not in STAGE_DAYS:
        raise ScoreboardRefusal(f"The stage must be one of {', '.join(STAGE_DAYS)}.")


HELD_OUT_SPLIT = "test"
# Sources whose graded rows come from a split: only the held-out rows are graded in a report, so the
# training rows stay free for the band prior and no training row is ever counted as accuracy.
SPLIT_SOURCES = ("backtest",)


def held_out_records(records: Sequence[Any], source: str) -> tuple[list[Any], list[Any]]:
    """The rows a report grades, and the rows it leaves out because they are not held out.

    For a backtest only rows whose split is "test" are graded. A training row, or one with no
    split, is left out and counted. Other sources grade every row. Works on GradedRecord and on
    MemoryCell alike (both carry `split`). pipeline.band_prior_training should take its held-out
    sites from the first list, so a training site is never dropped for being "graded".
    """
    if source not in SPLIT_SOURCES:
        return list(records), []
    kept = [r for r in records if r.split == HELD_OUT_SPLIT]
    left = [r for r in records if r.split != HELD_OUT_SPLIT]
    return kept, left


def _lead_filter(records: Sequence[Any], stage: str) -> tuple[list[Any], list[Any], list[Any]]:
    """(late, short, kept). A forecast dated after its stage day is a shorter lead, never that stage's
    marker (spec section 2); the scoreboard refuses such rows now and this keeps any stored before
    that out. lead_days on the cell is the outcome reading date minus as_of (spec section 2)."""
    late = [r for r in records if _after_stage_day(r, stage)]
    late_ids = {id(r) for r in late}
    short = [
        r for r in records
        if id(r) not in late_ids and (r.cell.lead_days is None or r.cell.lead_days < LEAD_FLOOR_DAYS)
    ]
    short_ids = {id(r) for r in short}
    kept = [r for r in records if id(r) not in short_ids and id(r) not in late_ids]
    return late, short, kept


def _assemble(
    *,
    records: Sequence[Any],
    short: Sequence[Any],
    late: Sequence[Any],
    not_held_out: Sequence[Any],
    baselines: dict[str, list[int]],
    band_prior_training: Sequence[GradedCell] | None,
    band_prior_unit: str,
    rule: GradingRule,
    band_prior_fitted: FittedBandPrior | None = None,
    source: str,
    stage: str,
    as_of: date,
    model_version: str | None,
    chain: Mapping[str, Any],
    eligibility: Mapping[str, Any],
    core_updates: Sequence[Mapping[str, Any]],
    regrades: Mapping[str, Any],
    cluster_by: str,
    lead_key: Callable[[int | None], str] | None,
) -> dict[str, Any]:
    """The report body shared by stage_report (rows from the scoreboard) and report_from_cells (rows in memory)."""
    cells = [r.cell for r in records]
    band_prior: dict[str, int] | None = None
    band_prior_source: str | None = None
    if band_prior_training is not None and band_prior_fitted is not None:
        raise ScoreboardRefusal("Give the band prior training cells or an already fitted band prior, not both.")
    if band_prior_fitted is not None:
        band_prior = band_prior_fitted.checked_table(cells)
        baselines["band_prior"] = band_prior_points(band_prior, cells)
        band_prior_source = band_prior_fitted.source
    elif band_prior_training is not None:
        training = list(band_prior_training)
        overlap = sorted({getattr(c, band_prior_unit) for c in training} & {getattr(c, band_prior_unit) for c in cells})
        if overlap:
            raise ScoreboardRefusal(
                f"The band prior training cells include {len(overlap)} {band_prior_unit}(s) that are graded here "
                f"({', '.join(overlap[:3])}). It is fitted on training data only."
            )
        try:
            band_prior = fit_band_prior(training, rule)
        except ValueError as error:
            raise ScoreboardRefusal(f"The band prior cannot be fitted: {error}.") from error
        baselines["band_prior"] = band_prior_points(band_prior, cells)
        band_prior_source = (f"The band prior was fitted here on {len(training)} training cells from "
                             f"{len({c.site for c in training})} sites, none of them graded here.")
    head = headline(cells, baselines, rule, cluster_by=cluster_by)
    missing_baselines = [name for name in SPEC_BASELINES if name not in head["baselines"]]

    main = [r for r in records if not r.cell.is_brand]
    main_index = [i for i, r in enumerate(records) if not r.cell.is_brand]
    persistence = baselines.get("persistence")
    persistence_main = [persistence[i] for i in main_index] if persistence is not None else None

    by_lead: dict[str, Any] = {}
    lead_groups: dict[str, list[int]] = defaultdict(list)
    lead_order: dict[str, int] = {}
    for position, record in enumerate(main):
        days = record.cell.lead_days
        name = str(int(days or 0)) if lead_key is None else lead_key(days)
        lead_groups[name].append(position)
        lead_order[name] = min(lead_order.get(name, 10**9), int(days if days is not None else 10**9))
    for name in sorted(lead_groups, key=lambda n: (lead_order[n], n)):
        idx = lead_groups[name]
        block = _block([main[i] for i in idx])
        if persistence_main is not None:
            block["persistence"] = _block(
                [main[i] for i in idx], [rule.is_hit(persistence_main[i], main[i].cell.actual) for i in idx]
            )
        by_lead[name] = block

    label = SYNTHETIC if source == "synthetic" else REAL
    notes: list[str] = []
    for other in SOURCES:
        entry = eligibility[other][stage]
        if entry["graded"] == 0:
            when = f" The next outcome is due on {entry['next_due_date']}." if entry["next_due_date"] else ""
            notes.append(f"{other} has no graded {stage} outcome as of {as_of.isoformat()}.{when}")
    if not_held_out:
        notes.append(
            f"{len(not_held_out)} graded rows of the training split are left out. Only held out rows are graded "
            f"here, so the band prior can be fitted on other sites ({label})."
        )
    if short:
        notes.append(
            f"{len(short)} graded rows have a lead under {LEAD_FLOOR_DAYS} days and are not called forecasts here ({label})."
        )
    if late:
        notes.append(
            f"{len(late)} graded rows were dated after day {STAGE_DAYS[stage]} and are not {stage} forecasts. "
            f"They are a shorter lead, never this marker ({label})."
        )
    if source == "backtest":
        notes.append("Backtest labels are single monthly snapshots, not clean readings.")
    if source == "synthetic":
        notes.append("Synthetic results are a test of the code. They are never accuracy.")

    report: dict[str, Any] = {
        "kind": "stage_report",
        "source": source,
        "stage": stage,
        "confirmed_stage": stage == CONFIRMED_STAGE,
        "as_of": as_of.isoformat(),
        "model_version": model_version,
        "label": label,
        "chain": dict(chain),
        "grading_rule": rule.version,
        "grading_rule_hash": rule.rule_hash(),
        "lead_floor_days": LEAD_FLOOR_DAYS,
        "excluded_short_lead": len(short),
        "excluded_after_stage_day": len(late),
        "excluded_not_held_out": len(not_held_out),
        "baselines_required": list(SPEC_BASELINES),
        "baselines_missing": missing_baselines,
        "band_prior": band_prior,
        "band_prior_source": band_prior_source,
        "minimum_count": REPORT_MIN_COUNT,
        "headline": head,
        "eligibility": eligibility,
        "eligibility_this": eligibility[source][stage],
        "by_difficulty": _group(main, lambda r: r.cell.difficulty or "unknown", DIFFICULTY_TERCILES),
        "by_niche": _group(main, lambda r: r.cell.niche or "unknown"),
        "by_lead_days": by_lead,
        "built_by": _count(main, lambda r: r.built_by or "unknown"),
        "held_out": _held_out(main),
        "target_url": _target_url_block(main),
        "core_updates": _core_update_block(core_updates, main, as_of),
        "regrades": dict(regrades),
        "notes": notes,
    }
    return report


@dataclass(frozen=True)
class MemoryCell:
    """One graded cell held in memory, with what the report needs beside it.

    `cell` carries site, search, the PREDICTED point, the REAL position, the brand flag, source,
    stage and lead_days (the real days from the forecast date to the outcome page). `split` is
    "test" for a held-out row. `baselines` maps a baseline name to its point for this cell; every
    cell must name the same baselines. "persistence" may be given here (there is no scoreboard to
    compute it from); "always_nt50" and "band_prior" are reserved, they are computed by the report.
    """

    cell: GradedCell
    split: str | None
    baselines: Mapping[str, int] = field(default_factory=dict)
    as_of: date | None = None
    outcome_date: date | None = None
    launch_date: date | None = None
    built_by: str | None = None
    target_url: str | None = None
    target_url_position: int | None = None


@dataclass(frozen=True)
class _MemoryRow:
    """A MemoryCell in the shape the shared report code reads from a GradedRecord."""

    grade_id: str
    cell: GradedCell
    split: str | None
    as_of: date | None
    outcome_date: date | None
    launch_date: date | None
    built_by: str | None
    target_url: str | None
    target_url_position: int | None
    target_url_hit: bool | None
    hit: bool
    baselines: Mapping[str, int]


MEMORY_RESERVED_BASELINES = ("always_nt50", "band_prior")
MEMORY_SOURCES = ("backtest", "synthetic")
MEMORY_CHAIN_NOTE = "These cells were graded in memory. They are not stored in the scoreboard chain."


def report_from_cells(
    cells: Sequence[MemoryCell],
    *,
    source: str,
    stage: str,
    as_of: date,
    model_version: str | None,
    band_prior_training: Sequence[GradedCell] | None = None,
    band_prior_unit: str = "site",
    rule: GradingRule = RULE_V1,
    cluster_by: str = "site",
    lead_key: Callable[[int | None], str] | None = None,
    core_updates: Sequence[Mapping[str, Any]] = (),
    notes: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The stage report built from graded cells held in memory, with the keys stage_report gives.

    The same rules as stage_report: one source, every cell of that source and stage; for a
    backtest only the held-out split ("test") is graded and the rest is counted under
    `excluded_not_held_out`; rows with a lead under the floor or dated after the stage day are left
    out and counted; brand searches are shown apart; the band prior is fitted here on
    `band_prior_training`, and a training cell that shares a `band_prior_unit` ("site", or
    "search" when the split is by search) with a graded cell is refused. `cluster_by` sets the
    headline intervals; when it is not "site", `headline_site_clusters` resamples sites too.
    `lead_key` names the lead groups (exact days by default). `core_updates` are dicts with name,
    kind, started_on, ended_on, source_url and recorded_at (None for a public calendar date).
    The chain is not verified, because these cells are not in a chain: `chain.ok` is None.
    """
    _check_source_and_stage(source, stage)
    if source not in MEMORY_SOURCES:
        # Seed and pilot outcomes are graded in the scoreboard, whose chain every report verifies.
        # Cells in memory carry no chain, so they never stand in for those reports.
        raise PoolingRefused(f"report_from_cells writes {' or '.join(MEMORY_SOURCES)} reports only. "
                             f"Seed and pilot reports come from the scoreboard (stage_report).")
    if band_prior_unit not in ("site", "search"):
        raise ValueError("band_prior_unit must be 'site' or 'search'")
    items = list(cells)
    sources = {item.cell.source for item in items if item.cell.source is not None}
    stages = {item.cell.stage for item in items if item.cell.stage is not None}
    if sources - {source}:
        raise PoolingRefused(f"These cells mix sources {sorted(sources)}. Each source is reported apart.")
    if stages - {stage}:
        raise ScoreboardRefusal(f"These cells are for stages {sorted(stages)}, not only {stage}.")
    names = {frozenset(item.baselines) for item in items}
    if len(names) > 1:
        raise ScoreboardRefusal("Every cell must name the same baselines, so they are compared on the same forecasts.")
    reserved = sorted(set().union(*names) & set(MEMORY_RESERVED_BASELINES)) if names else []
    if reserved:
        raise ScoreboardRefusal(f"The baseline name {reserved[0]} is reserved. It is always computed here.")

    rows = [
        _MemoryRow(
            grade_id=f"cell-{index}",
            cell=item.cell,
            split=item.split,
            as_of=item.as_of,
            outcome_date=item.outcome_date,
            launch_date=item.launch_date,
            built_by=item.built_by or source,
            target_url=item.target_url,
            target_url_position=item.target_url_position,
            target_url_hit=(rule.is_hit(item.target_url_position, item.cell.actual)
                            if item.target_url and item.target_url_position is not None else None),
            hit=rule.is_hit(item.cell.point, item.cell.actual),
            baselines=dict(item.baselines),
        )
        for index, item in enumerate(items)
    ]
    graded, not_held_out = held_out_records(rows, source)
    late, short, records = _lead_filter(graded, stage)
    baseline_names = sorted(next(iter(names))) if names else []
    baselines = {name: [int(r.baselines[name]) for r in records] for name in baseline_names}
    report = _assemble(
        records=records,
        short=short,
        late=late,
        not_held_out=not_held_out,
        baselines=baselines,
        band_prior_training=band_prior_training,
        band_prior_unit=band_prior_unit,
        rule=rule,
        source=source,
        stage=stage,
        as_of=as_of,
        model_version=model_version,
        chain={"ok": None, "note": MEMORY_CHAIN_NOTE},
        eligibility=_memory_eligibility(source, stage, graded=len(graded)),
        core_updates=[u for u in core_updates if u.get("recorded_at") is None
                      or str(u["recorded_at"])[:10] <= as_of.isoformat()],
        regrades={"count": 0, "pairs": [], "note": "Cells held in memory are graded once."},
        cluster_by=cluster_by,
        lead_key=lead_key,
    )
    if cluster_by != "site":
        # `baselines` now holds the band prior points too: _assemble adds them when it fits the prior.
        report["headline_site_clusters"] = headline([r.cell for r in records], dict(baselines), rule, cluster_by="site")
    leads = [r.cell.lead_days for r in records if r.cell.lead_days is not None]
    report["every_graded_cell_held_out"] = source not in SPLIT_SOURCES or all(r.split == HELD_OUT_SPLIT for r in records)
    report["single_snapshot_labels"] = source == "backtest"
    report["real_lead_days"] = {
        "count": len(leads),
        "min": min(leads) if leads else None,
        "median": float(statistics.median(leads)) if leads else None,
        "max": max(leads) if leads else None,
    }
    report["notes"] = list(notes) + list(report["notes"])
    if extra:
        for key, value in extra.items():
            if key in report:
                raise ScoreboardRefusal(f"extra may not replace the report's own key {key}.")
            report[key] = value
    return report


def _memory_eligibility(source: str, stage: str, *, graded: int) -> dict[str, dict[str, dict[str, Any]]]:
    """Every held-out cell in memory was made, was due and is graded. Other sources have none here."""
    table = {s: {st: {"made": 0, "due": 0, "graded": 0, "due_not_graded": 0, "not_yet_due": 0, "next_due_date": None}
                 for st in STAGE_DAYS} for s in SOURCES}
    table[source][stage].update({"made": graded, "due": graded, "graded": graded})
    return table


def _after_stage_day(record: GradedRecord, stage: str) -> bool:
    """True when a non-upload forecast is dated after launch plus its stage day."""
    if stage == "upload" or record.launch_date is None:
        return False
    return record.as_of > record.launch_date + timedelta(days=STAGE_DAYS[stage])


def _count(records: Sequence[GradedRecord], key: Callable[[GradedRecord], str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[key(record)] += 1
    return {name: counts[name] for name in sorted(counts)}


def _held_out(records: Sequence[GradedRecord]) -> dict[str, int]:
    test = sum(1 for r in records if r.split == "test")
    return {"test": test, "not_test": len(records) - test}


def _target_url_block(records: Sequence[GradedRecord]) -> dict[str, Any]:
    with_target = [r for r in records if r.target_url]
    known = [r for r in with_target if r.target_url_position is not None]
    block = _block(known, [bool(r.target_url_hit) for r in known])
    block["forecasts_with_target_url"] = len(with_target)
    block["position_unknown"] = len(with_target) - len(known)
    return block


def _core_update_block(updates: Sequence[Mapping[str, Any]], records: Sequence[Any], as_of: date) -> dict[str, Any]:
    """Hit rates on outcomes read during and outside the given updates. A row with no outcome date
    is in neither block: it cannot be placed, so it is not counted as outside."""
    rows: list[dict[str, Any]] = []
    inside_any: set[str] = set()
    records = [r for r in records if r.outcome_date is not None]
    for update in updates:
        start = date.fromisoformat(update["started_on"])
        end = date.fromisoformat(update["ended_on"]) if update["ended_on"] else None
        inside = [r for r in records if r.outcome_date >= start and (end is None or r.outcome_date <= end)]
        spans = [r for r in records if r.as_of is not None and r.as_of <= (end or as_of) and r.outcome_date >= start]
        inside_any.update(r.grade_id for r in inside)
        rows.append(
            {
                "name": update["name"],
                "kind": update["kind"],
                "started_on": update["started_on"],
                "ended_on": update["ended_on"],
                "source_url": update.get("source_url"),
                "recorded_at": update.get("recorded_at"),
                "outcomes_inside": _block(inside),
                "forecast_spans_overlapping": len(spans),
            }
        )
    during = [r for r in records if r.grade_id in inside_any]
    outside = [r for r in records if r.grade_id not in inside_any]
    return {"recorded": rows, "outcomes_during_any_update": _block(during), "outcomes_outside_updates": _block(outside)}


def _regrade_block(
    scoreboard: Scoreboard, source: str, stage: str, as_of: date, model_version: str | None
) -> dict[str, Any]:
    def graded(kind: str) -> list[GradedRecord]:
        # The same held-out rule as the headline: a backtest training row is never counted here either.
        return held_out_records(scoreboard.graded_records(source, stage, as_of, kind, model_version=model_version), source)[0]

    originals = {r.forecast_id: r for r in graded("original")}
    regrades = graded("regrade")
    pairs = []
    for regrade in regrades:
        original = originals.get(regrade.forecast_id)
        if original is None:
            continue
        pairs.append(
            {
                "forecast_id": regrade.forecast_id,
                "site": regrade.cell.site,
                "search": regrade.cell.search,
                "is_brand": regrade.cell.is_brand,
                "original": {
                    "position": original.cell.actual,
                    "hit": original.hit,
                    "rule": original.grading_rule_version,
                    "reading_id": original.reading_id,
                },
                "regrade": {
                    "position": regrade.cell.actual,
                    "hit": regrade.hit,
                    "rule": regrade.grading_rule_version,
                    "reading_id": regrade.reading_id,
                    "reason": regrade.reason,
                },
            }
        )
    main = [p for p in pairs if not p["is_brand"]]
    latest = graded("latest")
    main_original = [
        r for r in originals.values()
        if not r.cell.is_brand and (r.cell.lead_days or 0) >= LEAD_FLOOR_DAYS and not _after_stage_day(r, stage)
    ]
    main_latest = [
        r for r in latest
        if not r.cell.is_brand and (r.cell.lead_days or 0) >= LEAD_FLOOR_DAYS and not _after_stage_day(r, stage)
    ]
    return {
        "count": len(pairs),
        "pairs": pairs,
        "original_hit_on_regraded": _block([], [p["original"]["hit"] for p in main]),
        "regrade_hit_on_regraded": _block([], [p["regrade"]["hit"] for p in main]),
        "all_original": _block(main_original),
        "all_with_latest_regrade": _block(main_latest),
    }


# ---------------------------------------------------------------------------------------------
# Plain text


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_BAND_WORDS = {"1-10": "1 to 10", "11-20": "11 to 20", "21-50": "21 to 50", "NT50": "not in top 50"}


def plain_date(value: str | date | None) -> str:
    """22 Nov 2026 style. ISO dates are written without dashes in plain text."""
    if value is None:
        return "no date"
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _pct(value: float | None) -> str:
    return "no value" if value is None else f"{100.0 * value:.1f}%"


def _rate_text(block: Mapping[str, Any], label: str, *, minimum: int = REPORT_MIN_COUNT) -> str:
    count = int(block.get("count") or 0)
    if count < minimum:
        return f"NOT MEASURABLE ({count} of {minimum} needed) ({label})"
    right = int(block.get("right") or 0)
    return f"{_pct(block.get('rate'))}, {right} of {count} ({label})"


def _interval_text(interval: Sequence[float | None] | None) -> str:
    if not interval or interval[0] is None or interval[1] is None:
        return "no interval"
    return f"{_pct(interval[0])} to {_pct(interval[1])}"


def render_text(report: Mapping[str, Any]) -> str:
    """The report in short plain sentences. Every number says REAL, PREDICTED or SYNTHETIC."""
    if report.get("kind") != "stage_report":
        raise ScoreboardRefusal("render_text takes a stage_report.")
    real = SYNTHETIC if report["source"] == "synthetic" else REAL
    head = report["headline"]
    summary = head["summary"]
    lines: list[str] = []
    stage_words = f"{report['stage']}, the Confirmed forecast" if report["confirmed_stage"] else report["stage"]
    lines.append(f"Ranksim stage report. Source {report['source']}. Stage {stage_words}. As of {plain_date(report['as_of'])}.")
    lines.append(f"Model version {report['model_version'] or 'none'}. Grading rule {report['grading_rule']}.")
    chain = report.get("chain") or {}
    if chain.get("ok") is True:
        lines.append("The hash chains verify.")
    else:
        lines.append(str(chain.get("note") or "These rows were not checked against a hash chain."))
    if real == SYNTHETIC:
        lines.append("These are SYNTHETIC results. They test the code and are never accuracy.")

    lines.append(f"Graded forecasts in the headline: {head['forecasts']} ({real}).")
    lines.append(f"Brand searches shown apart: {head['brand_forecasts_shown_apart']} ({real}).")
    if report["excluded_short_lead"]:
        lines.append(
            f"Rows with a lead under {report['lead_floor_days']} days left out: {report['excluded_short_lead']} ({real})."
        )
    if report.get("excluded_after_stage_day"):
        lines.append(f"Rows dated after the stage day left out: {report['excluded_after_stage_day']} ({real}).")
    if report.get("excluded_not_held_out"):
        lines.append(f"Training rows left out, only held out rows are graded: {report['excluded_not_held_out']} ({real}).")
    if report.get("baselines_missing"):
        lines.append(
            "Baselines the spec requires that are missing here: "
            + ", ".join(name.replace("_", " ") for name in report["baselines_missing"])
            + f". Markers 3 to 5 are not passed on this report ({real})."
        )
    if report.get("band_prior_source"):
        lines.append(_plain_note(str(report["band_prior_source"])))
    if head["forecasts"] == 0:
        lines.append(f"There are zero graded outcomes, so no rate is measurable yet ({real}).")
    hit = summary["hit"]
    lines.append(f"Hit rate: {_rate_text(hit, real)}.")
    if hit["count"] >= REPORT_MIN_COUNT:
        lines.append(f"Site cluster interval: {_interval_text(hit['cluster_interval'])} ({real}).")
        lines.append(
            f"Interval if forecasts were independent: {_interval_text(hit['wilson_interval_if_independent'])} ({real})."
        )
    for name in sorted(head["baselines"]):
        base = head["baselines"][name]
        words = "Always saying not in top 50" if name == "always_nt50" else f"Baseline {name.replace('_', ' ')}"
        if base["count"] < REPORT_MIN_COUNT:
            lines.append(f"{words}: NOT MEASURABLE ({base['count']} of {REPORT_MIN_COUNT} needed) ({real}).")
        else:
            lines.append(
                f"{words} scores {_pct(base['baseline_rate'])} on the same forecasts. "
                f"The gain over it is {_pct(base['difference'])}, interval {_interval_text(base['difference_interval'])} ({real})."
            )
    if head["best_baseline"] and hit["count"] >= REPORT_MIN_COUNT:
        skill = head["skill_over_best_baseline"]
        skill_text = "no value" if skill is None else f"{skill:.2f}"
        lines.append(f"Best baseline: {head['best_baseline'].replace('_', ' ')}. Skill over it: {skill_text} ({real}).")
    page_one = summary["page_one"]
    lines.append(f"Page one calls: {page_one['calls']} (PREDICTED). Page one outcomes: {page_one['outcomes']} ({real}).")
    lines.append(f"Page one precision: {_rate_text(page_one['precision'], real)}.")
    lines.append(f"Page one recall: {_rate_text(page_one['recall'], real)}.")
    lines.append(f"Plain band match: {_rate_text(summary['band_match'], real)}.")
    lines.append(f"Hit rate under the forecast side rule: {_rate_text(summary['hit_forecast_side_rule'], real)}.")
    pile = summary["point_pileup"]
    lines.append(f"Share of 11 to 20 points that sit on 15: {_pct(pile['share_of_11_20_points_at_15'])} (PREDICTED).")
    lines.append(f"Share of 21 to 50 points that sit on 25: {_pct(pile['share_of_21_50_points_at_25'])} (PREDICTED).")
    ranged = summary["range"]
    lines.append(f"Range coverage: {_rate_text({'count': ranged['count'], 'right': ranged['covered'], 'rate': ranged['coverage']}, real)}.")
    for band, block in summary["by_real_band"].items():
        lines.append(f"Real band {_BAND_WORDS[band]}: {_rate_text(block, real)}.")
    for title, key in (("Difficulty", "by_difficulty"), ("Niche", "by_niche")):
        for name, block in report[key].items():
            lines.append(f"{title} {name}: {_rate_text(block, real)}.")
    for lead, block in report["by_lead_days"].items():
        lines.append(f"Lead of {lead} days: {_rate_text(block, real)}. Persistence at that lead: {_rate_text(block['persistence'], real)}.")
    for name, count in report["built_by"].items():
        lines.append(f"Graded forecasts on sites built by {name}: {count} ({real}).")
    target = report["target_url"]
    lines.append(
        f"Target URL hit rate: {_rate_text(target, real)}. "
        f"Target URL position unknown for {target['position_unknown']} of {target['forecasts_with_target_url']} ({real})."
    )
    updates = report["core_updates"]
    if not updates["recorded"]:
        lines.append("No core update is recorded.")
    for update in updates["recorded"]:
        end = plain_date(update["ended_on"]) if update["ended_on"] else "an open end"
        lines.append(
            f"Core update {update['name']} ran from {plain_date(update['started_on'])} to {end}. "
            f"We recorded it on {plain_date(update['recorded_at'])}. "
            f"Outcomes read during it: {update['outcomes_inside']['count']} ({real})."
        )
    lines.append(f"Hit rate on outcomes read during an update: {_rate_text(updates['outcomes_during_any_update'], real)}.")
    lines.append(f"Hit rate on outcomes read outside updates: {_rate_text(updates['outcomes_outside_updates'], real)}.")
    regrades = report["regrades"]
    lines.append(f"Regrades: {regrades['count']} ({real}). Originals are never replaced.")
    for pair in regrades["pairs"]:
        lines.append(
            f"Forecast {pair['forecast_id']}: original position {pair['original']['position']}, "
            f"{'hit' if pair['original']['hit'] else 'miss'}. Regrade position {pair['regrade']['position']}, "
            f"{'hit' if pair['regrade']['hit'] else 'miss'}, because {pair['regrade']['reason']} ({real})."
        )
    if regrades["count"]:
        lines.append(f"Hit rate on all originals: {_rate_text(regrades['all_original'], real)}.")
        lines.append(f"Hit rate with the latest regrade in place: {_rate_text(regrades['all_with_latest_regrade'], real)}.")
    for source in SOURCES:
        entry = report["eligibility"][source][report["stage"]]
        lines.append(
            f"Eligibility for {source}: {entry['made']} made, {entry['due']} due, {entry['not_yet_due']} not yet due "
            f"(PREDICTED). {entry['graded']} graded ({SYNTHETIC if source == 'synthetic' else REAL})."
        )
    lines.append(persistence_label())
    for note in report["notes"]:
        lines.append(_plain_note(note))
    text = "\n".join(lines)
    return text.replace("\u2014", ",").replace("\u2013", " to ")


def _plain_note(note: str) -> str:
    """Notes carry ISO dates. Write them the plain way."""
    words = []
    for token in note.split(" "):
        core = token.rstrip(".,")
        tail = token[len(core):]
        if len(core) == 10 and core[4] == "-" and core[7] == "-" and core.replace("-", "").isdigit():
            words.append(plain_date(core) + tail)
        else:
            words.append(token)
    return " ".join(words)


def persistence_label() -> str:
    """How the persistence baseline is defined, for printing beside it."""
    return (
        "Persistence is the site's clean position in the latest reading taken on or before the forecast date. "
        "It counts as not in top 50 when there is none."
    )


__all__ = [
    "MemoryCell",
    "combined_report",
    "held_out_records",
    "persistence_label",
    "plain_date",
    "render_text",
    "report_from_cells",
    "stage_report",
]
