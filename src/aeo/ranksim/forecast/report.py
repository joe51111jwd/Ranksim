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

from collections import defaultdict
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from .config import CONFIRMED_STAGE, DIFFICULTY_TERCILES, LEAD_FLOOR_DAYS, MIN_COUNT_TO_PRINT_RATE, SOURCES, STAGE_DAYS
from .grading import GradedCell, headline
from .scoreboard import GradedRecord, PoolingRefused, Scoreboard, ScoreboardRefusal, mapping_points
from .types import REAL, SYNTHETIC

REPORT_MIN_COUNT = MIN_COUNT_TO_PRINT_RATE
RESERVED_BASELINES = ("always_nt50", "persistence")
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
) -> dict[str, Any]:
    """Every number for one source and one stage as of a date, on the original grades.

    `extra_baselines` maps a name to either forecast id -> point, or a function of the cell.
    always_nt50 and persistence are always added.
    """
    if source not in SOURCES:
        raise PoolingRefused(f"Pick one source of {', '.join(SOURCES)}. A combined view is refused.")
    if stage not in STAGE_DAYS:
        raise ScoreboardRefusal(f"The stage must be one of {', '.join(STAGE_DAYS)}.")
    status = scoreboard.require_verified()

    all_records = scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)
    short = [r for r in all_records if r.cell.lead_days is None or r.cell.lead_days < LEAD_FLOOR_DAYS]
    short_ids = {r.grade_id for r in short}
    records = [r for r in all_records if r.grade_id not in short_ids]
    cells = [r.cell for r in records]

    baselines: dict[str, list[int]] = {"persistence": scoreboard.persistence_points(records)}
    for name, spec in sorted((extra_baselines or {}).items()):
        if name in RESERVED_BASELINES:
            raise ScoreboardRefusal(f"The baseline name {name} is reserved. It is always computed here.")
        baselines[name] = _baseline_points(name, spec, records)
    head = headline(cells, baselines, scoreboard.rule)

    main = [r for r in records if not r.cell.is_brand]
    main_index = [i for i, r in enumerate(records) if not r.cell.is_brand]
    persistence_main = [baselines["persistence"][i] for i in main_index]

    by_lead: dict[str, Any] = {}
    lead_groups: dict[int, list[int]] = defaultdict(list)
    for position, record in enumerate(main):
        lead_groups[int(record.cell.lead_days or 0)].append(position)
    for lead in sorted(lead_groups):
        idx = lead_groups[lead]
        block = _block([main[i] for i in idx])
        block["persistence"] = _block(
            [main[i] for i in idx], [scoreboard.rule.is_hit(persistence_main[i], main[i].cell.actual) for i in idx]
        )
        by_lead[str(lead)] = block

    eligibility = scoreboard.eligibility(as_of)
    notes: list[str] = []
    for other in SOURCES:
        entry = eligibility[other][stage]
        if entry["graded"] == 0:
            when = f" The next outcome is due on {entry['next_due_date']}." if entry["next_due_date"] else ""
            notes.append(f"{other} has no graded {stage} outcome as of {as_of.isoformat()}.{when}")
    if short:
        notes.append(
            f"{len(short)} graded rows have a lead under {LEAD_FLOOR_DAYS} days and are not called forecasts here (REAL)."
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
        "model_version": records[0].model_version if records else model_version,
        "label": SYNTHETIC if source == "synthetic" else REAL,
        "chain": status.to_dict(),
        "grading_rule": scoreboard.rule.version,
        "grading_rule_hash": scoreboard.rule.rule_hash(),
        "lead_floor_days": LEAD_FLOOR_DAYS,
        "excluded_short_lead": len(short),
        "minimum_count": REPORT_MIN_COUNT,
        "headline": head,
        "eligibility": eligibility,
        "eligibility_this": eligibility[source][stage],
        "by_difficulty": _group(main, lambda r: r.cell.difficulty or "unknown", DIFFICULTY_TERCILES),
        "by_niche": _group(main, lambda r: r.cell.niche or "unknown"),
        "by_lead_days": by_lead,
        "built_by": _count(main, lambda r: r.built_by),
        "held_out": _held_out(main),
        "target_url": _target_url_block(main),
        "core_updates": _core_update_block(scoreboard, main, as_of),
        "regrades": _regrade_block(scoreboard, source, stage, as_of, model_version),
        "notes": notes,
    }
    return report


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


def _core_update_block(scoreboard: Scoreboard, records: Sequence[GradedRecord], as_of: date) -> dict[str, Any]:
    updates = [u for u in scoreboard.core_updates() if u["recorded_at"][:10] <= as_of.isoformat()]
    rows: list[dict[str, Any]] = []
    inside_any: set[str] = set()
    for update in updates:
        start = date.fromisoformat(update["started_on"])
        end = date.fromisoformat(update["ended_on"]) if update["ended_on"] else None
        inside = [r for r in records if r.outcome_date >= start and (end is None or r.outcome_date <= end)]
        spans = [r for r in records if r.as_of <= (end or as_of) and r.outcome_date >= start]
        inside_any.update(r.grade_id for r in inside)
        rows.append(
            {
                "name": update["name"],
                "kind": update["kind"],
                "started_on": update["started_on"],
                "ended_on": update["ended_on"],
                "source_url": update["source_url"],
                "recorded_at": update["recorded_at"],
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
    originals = {r.forecast_id: r for r in scoreboard.graded_records(source, stage, as_of, "original", model_version=model_version)}
    regrades = scoreboard.graded_records(source, stage, as_of, "regrade", model_version=model_version)
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
    latest = scoreboard.graded_records(source, stage, as_of, "latest", model_version=model_version)
    main_original = [r for r in originals.values() if not r.cell.is_brand and (r.cell.lead_days or 0) >= LEAD_FLOOR_DAYS]
    main_latest = [r for r in latest if not r.cell.is_brand and (r.cell.lead_days or 0) >= LEAD_FLOOR_DAYS]
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
    lines.append("The hash chains verify.")
    if real == SYNTHETIC:
        lines.append("These are SYNTHETIC results. They test the code and are never accuracy.")

    lines.append(f"Graded forecasts in the headline: {head['forecasts']} ({real}).")
    lines.append(f"Brand searches shown apart: {head['brand_forecasts_shown_apart']} ({real}).")
    if report["excluded_short_lead"]:
        lines.append(
            f"Rows with a lead under {report['lead_floor_days']} days left out: {report['excluded_short_lead']} ({real})."
        )
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
    "combined_report",
    "persistence_label",
    "plain_date",
    "render_text",
    "stage_report",
]
