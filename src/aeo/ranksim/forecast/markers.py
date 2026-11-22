"""The five markers of the build plan, frozen, and how each is judged.

A marker passes only on measured numbers. The numbers come from a report:
marker 1 from the clean reader's agreement report, marker 2 from the trust
model's pair report, markers 3 to 5 from `report.stage_report`. Graded
outcomes are REAL. Nothing here estimates a number. When a number cannot be
measured yet (zero eligible outcomes, or a count under the minimum) the
verdict is None, never a pass, and the notes say exactly why.

Pass rules (docs/plan/IMPLEMENTATION_SPEC.md section 7):
  1  headline agreement 0.92 or more
  2  0.70 or more of the frozen pairs, and the gain over link count has a lower bound above 0
  3  upload hit rate 0.75 or more on held-out backtest launches, and the gain over the best
     baseline has a lower bound above 0
  4  day-30 hit rate 0.85 or more and page-one precision 0.80 or more (recall printed)
  5  Confirmed hit rate 0.90 or more, precision 0.85 or more, recall 0.80 or more, on REAL
     day-90 outcomes only. Backtest data can never pass it. The site-cluster interval is printed.

Markers 3 to 5 are judged on a stage report that prints every baseline the spec
requires on the same forecasts (config.SPEC_BASELINES: always NT50, persistence,
the band prior by difficulty tercile fitted on training data, site strength
only). While one is missing the verdict is None, never a pass: for marker 3 the
gain over the best baseline is not measurable, since the missing one could be
the best. The notes name what is missing.

A report on synthetic data (its label is SYNTHETIC or its source is
synthetic) gets a SYNTHETIC verdict: `passed` is None, never True, and the
value the bars would give is kept under `synthetic_check_passed`, so the code
can still be tested end to end.

The plan's schedule is checked here too, with date arithmetic only.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from .chain import canonical_json, sha256_text
from .config import CONFIRMED_STAGE, MIN_COUNT_TO_PRINT_RATE, OUTCOME_DAY, SPEC_BASELINES, STAGE_DAYS
from .report import plain_date
from .types import REAL, SYNTHETIC

MARKER_MIN_COUNT = MIN_COUNT_TO_PRINT_RATE
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SCHEDULE_DAYS = (7, 14, 30, 60, 90)

# Dates stated in the build plan (docs/plan/BUILD_PLAN_ROAD_TO_90.md).
PLAN_START = date(2026, 11, 23)  # "Day 1, 23 Nov"; week 1 is 23 to 29 Nov.
PLAN_SEED_LAUNCH = date(2026, 11, 30)  # Gantt: "Seed sites, read weekly | Nov 30 to Feb 28".
PLAN_SEED_DAY_60 = date(2027, 1, 29)  # "The seed sites reach day 60 on 29 January".
PLAN_SEED_DAY_90 = date(2027, 2, 28)  # "and day 90 on 28 February".
PLAN_PILOT_START = date(2026, 12, 28)  # Gantt: "Agency pilots | Dec 28 to Mar 19".


@dataclass(frozen=True)
class MarkerTarget:
    """One bar: the named value must be at least `minimum` (above it when `strict`)."""

    name: str
    minimum: float
    strict: bool = False

    def met(self, value: float) -> bool:
        return value > self.minimum if self.strict else value >= self.minimum


@dataclass(frozen=True)
class Marker:
    number: int
    name: str
    date: date
    stage: str | None
    sources: tuple[str, ...]
    targets: tuple[MarkerTarget, ...]
    pass_rule: str
    also_printed: tuple[str, ...]
    min_count: int = MARKER_MIN_COUNT


MARKERS: tuple[Marker, ...] = (
    Marker(
        1,
        "Clean reader",
        date(2026, 11, 30),
        None,
        (),
        (MarkerTarget("headline agreement", 0.92),),
        "Two clean readings of one search a day apart agree within 2 places for 0.92 or more of the units.",
        ("bands", "raw pull baseline", "interval"),
    ),
    Marker(
        2,
        "Trust model",
        date(2026, 12, 14),
        None,
        ("backtest",),
        (MarkerTarget("pair accuracy", 0.70), MarkerTarget("lower bound of gain over link count", 0.0, strict=True)),
        "On held out sites the trust model orders 0.70 or more of the frozen pairs, and beats link count on the same pairs.",
        ("right", "wrong", "tied", "missing"),
    ),
    Marker(
        3,
        "Forecast at upload",
        date(2026, 12, 21),
        "upload",
        ("backtest",),
        (MarkerTarget("hit rate", 0.75), MarkerTarget("lower bound of gain over best baseline", 0.0, strict=True)),
        "Upload hit rate 0.75 or more on held out backtest launches, above the best baseline with a lower bound above 0.",
        ("per band", "per difficulty", "skill"),
    ),
    Marker(
        4,
        "Forecast at day 30",
        date(2027, 1, 22),
        "d30",
        ("backtest", "seed", "pilot"),
        (MarkerTarget("hit rate", 0.85), MarkerTarget("page one precision", 0.80)),
        "Day 30 hit rate 0.85 or more and page one precision 0.80 or more.",
        ("recall", "seed readings as interim only"),
    ),
    Marker(
        5,
        "Confirmed forecast at day 60",
        date(2027, 3, 19),
        CONFIRMED_STAGE,
        ("seed", "pilot"),
        (MarkerTarget("hit rate", 0.90), MarkerTarget("page one precision", 0.85), MarkerTarget("page one recall", 0.80)),
        "Confirmed hit rate 0.90 or more, precision 0.85 or more and recall 0.80 or more, on REAL day 90 outcomes.",
        ("site cluster interval", "backtest apart"),
    ),
)


def marker(number: int) -> Marker:
    for item in MARKERS:
        if item.number == number:
            return item
    raise ValueError(f"There is no marker {number}. The plan has markers 1 to 5.")


def markers_payload() -> list[dict[str, Any]]:
    """The frozen markers as plain JSON, for hashing and for the scoreboard's freezes table."""
    out = []
    for item in MARKERS:
        entry = asdict(item)
        entry["date"] = item.date.isoformat()
        out.append(entry)
    return out


def markers_hash() -> str:
    return sha256_text(canonical_json(markers_payload()))


def _check(name: str, value: Any, target: Any, ok: bool | None) -> dict[str, Any]:
    return {"name": name, "value": value, "target": target, "ok": ok}


def _rate_check(target: MarkerTarget, block: Mapping[str, Any] | None, minimum: int, notes: list[str]) -> dict[str, Any]:
    """A rate bar. None when the count is under the minimum or the rate is missing."""
    count = int((block or {}).get("count") or 0)
    rate = (block or {}).get("rate")
    if count == 0:
        notes.append(f"{target.name.capitalize()} is not measurable: zero eligible real outcomes.")
        return _check(target.name, rate, target.minimum, None)
    if count < minimum:
        notes.append(f"{target.name.capitalize()} is NOT MEASURABLE ({count} of {minimum} needed).")
        return _check(target.name, rate, target.minimum, None)
    if rate is None:
        notes.append(f"{target.name.capitalize()} has no value.")
        return _check(target.name, None, target.minimum, None)
    return _check(target.name, rate, target.minimum, target.met(float(rate)))


def _bound_check(
    target: MarkerTarget, interval: Sequence[float | None] | None, count: int, minimum: int, notes: list[str]
) -> dict[str, Any]:
    """A lower-bound bar. None under the minimum count: a bound on a tiny sample is neither a pass nor a fail."""
    low = interval[0] if interval else None
    if count < minimum:
        notes.append(f"The {target.name} is NOT MEASURABLE ({count} of {minimum} needed).")
        return _check(target.name, low, target.minimum, None)
    if low is None:
        notes.append(f"The {target.name} cannot be computed yet (fewer than 2 sites or no outcomes).")
        return _check(target.name, None, target.minimum, None)
    return _check(target.name, low, target.minimum, target.met(float(low)))


def _verdict(checks: Sequence[Mapping[str, Any]]) -> bool | None:
    if any(c["ok"] is False for c in checks):
        return False
    if any(c["ok"] is None for c in checks):
        return None
    return True


def is_synthetic_report(report: Mapping[str, Any]) -> bool:
    """True when the report holds synthetic data: its label is SYNTHETIC or its source is synthetic."""
    return report.get("label") == SYNTHETIC or report.get("source") == "synthetic"


def evaluate_marker(number: int, report: Mapping[str, Any]) -> dict[str, Any]:
    """Judge one marker on one report. passed is True, False, or None when it is not measurable yet.

    On a synthetic report the verdict is labeled SYNTHETIC and passed is None.
    The computed value is kept under synthetic_check_passed.
    """
    item = marker(number)
    notes: list[str] = []
    checks: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}

    if number == 1:
        block = report.get("headline") or {}
        checks.append(_rate_check(item.targets[0], block, item.min_count, notes))
        if block.get("cluster_interval"):
            extra["interval"] = list(block["cluster_interval"])
    elif number == 2:
        pairs = report.get("pairs") or {}
        checks.append(_rate_check(item.targets[0], pairs, item.min_count, notes))
        gain = report.get("gain_over_link_count") or {}
        checks.append(
            _bound_check(item.targets[1], gain.get("difference_interval"), int(pairs.get("count") or 0), item.min_count, notes)
        )
        if report.get("counts"):
            extra["counts"] = dict(report["counts"])
    else:
        _stage_marker(item, report, checks, notes, extra)

    passed = _verdict(checks)
    if passed is None and not notes:
        notes.append("Not measurable yet.")
    synthetic = is_synthetic_report(report)
    if synthetic:
        notes.append("Synthetic results test the code. They cannot pass a marker.")
    result = {
        "marker": number,
        "name": item.name,
        "date": item.date.isoformat(),
        "markers_hash": markers_hash(),
        "label": SYNTHETIC if synthetic else REAL,
        "passed": None if synthetic else passed,
        "checks": checks,
        "notes": notes,
    }
    if synthetic:
        result["synthetic_check_passed"] = passed
    result.update(extra)
    return result


def _stage_marker(
    item: Marker,
    report: Mapping[str, Any],
    checks: list[dict[str, Any]],
    notes: list[str],
    extra: dict[str, Any],
) -> None:
    if report.get("kind") != "stage_report":
        raise ValueError(f"Marker {item.number} is judged on a stage_report.")
    if report.get("stage") != item.stage:
        raise ValueError(f"Marker {item.number} is judged on stage {item.stage}. The report is for {report.get('stage')}.")
    source = report.get("source")
    head = report["headline"]
    summary = head["summary"]
    extra["source"] = source
    extra["as_of"] = report.get("as_of")
    extra["graded_forecasts"] = head["forecasts"]

    eligibility = report.get("eligibility") or {}
    for other in sorted(eligibility):
        entry = eligibility[other].get(item.stage, {})
        if entry.get("graded", 0) == 0:
            due = entry.get("next_due_date")
            when = f" The first is due on {due}." if due else ""
            notes.append(f"{other} has no graded {item.stage} outcome yet.{when}")
    if report.get("as_of") != item.date.isoformat():
        notes.append(f"The report is as of {report.get('as_of')}. The marker date is {item.date.isoformat()}.")

    checks.append(_check("source may pass this marker", source, list(item.sources), source in item.sources))
    if item.number == 5 and source == "backtest":
        notes.append("Marker 5 needs REAL day 90 outcomes. Backtest data cannot pass it.")
    if item.number == 4 and source == "seed":
        notes.append("Seed readings count for marker 4 as interim only.")
    if source == "backtest":
        notes.append("Backtest labels are single monthly snapshots.")

    if head["forecasts"] == 0:
        notes.append("Zero eligible real outcomes are graded, so the hit rate cannot be measured.")
    checks.append(_rate_check(item.targets[0], summary["hit"], item.min_count, notes))
    missing = [name for name in SPEC_BASELINES if name not in (head.get("baselines") or {})]
    extra["baselines_missing"] = missing
    if missing:
        notes.append(
            "The baselines the spec requires on the same forecasts are missing: " + ", ".join(missing) + ". "
            f"Marker {item.number} cannot pass without them."
        )

    if item.number == 3:
        held = report.get("held_out") or {}
        not_test = int(held.get("not_test", 0))
        checks.append(_check("only held out launches are graded", not_test, 0, not_test == 0 if head["forecasts"] else None))
        best = head.get("best_baseline")
        interval = head["baselines"][best]["difference_interval"] if best else None
        if missing:
            # The best baseline cannot be known while a required one was never computed.
            checks.append(_check(item.targets[1].name, interval[0] if interval else None, item.targets[1].minimum, None))
        else:
            checks.append(_bound_check(item.targets[1], interval, int(head["forecasts"]), item.min_count, notes))
        extra["best_baseline"] = best
        extra["skill"] = head.get("skill_over_best_baseline")
    if item.number in (4, 5):
        checks.append(_check("spec baselines printed on the same forecasts", len(SPEC_BASELINES) - len(missing),
                             len(SPEC_BASELINES), None if missing else True))
    page_one = summary["page_one"]
    if item.number in (4, 5):
        checks.append(_rate_check(item.targets[1], page_one["precision"], item.min_count, notes))
        extra["recall"] = {
            "count": page_one["recall"]["count"],
            "rate": page_one["recall"]["rate"],
            "measurable": page_one["recall"]["count"] >= item.min_count,
        }
    if item.number == 5:
        checks.append(_rate_check(item.targets[2], page_one["recall"], item.min_count, notes))
        cluster_by = summary.get("cluster_by")
        checks.append(_check("intervals resample sites", cluster_by, "site", cluster_by == "site"))
        extra["site_cluster_interval"] = list(summary["hit"]["cluster_interval"])
        extra["clusters"] = summary.get("clusters")
        low, high = summary["hit"]["cluster_interval"]
        if low is None:
            notes.append("The site cluster interval cannot be computed yet.")
        else:
            notes.append(f"Site cluster interval of the hit rate: {low:.3f} to {high:.3f} over {summary.get('clusters')} sites.")


def render_marker_text(result: Mapping[str, Any]) -> str:
    """A marker verdict in short plain sentences."""
    words = {True: "PASSED", False: "NOT PASSED", None: "NOT MEASURABLE YET"}
    if result.get("label") == SYNTHETIC:
        check = words[result.get("synthetic_check_passed")]
        lines = [
            f"Marker {result['marker']}, {result['name']}: NOT A MARKER RESULT ({SYNTHETIC}). "
            f"Synthetic code check: {check} ({SYNTHETIC})."
        ]
    else:
        lines = [f"Marker {result['marker']}, {result['name']}: {words[result['passed']]} ({result['label']})."]
    for check in result["checks"]:
        state = {True: "met", False: "not met", None: "not measurable"}[check["ok"]]
        lines.append(
            f"Check {check['name']}: {_shown(check['value'])} against {_shown(check['target'])}, {state} ({result['label']})."
        )
    lines.extend(_plain_dates(note) for note in result["notes"])
    return "\n".join(lines).replace("\u2014", ",").replace("\u2013", " to ")


def _shown(value: Any) -> str:
    if value is None:
        return "no value"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{100 * value:.1f}%"
    if isinstance(value, (list, tuple)):
        return " or ".join(str(item) for item in value) if value else "none"
    return _plain_dates(str(value))


def _plain_dates(text: str) -> str:
    """ISO dates written as 28 Feb 2027, so plain text carries no dashes of our own."""
    return _ISO_DATE.sub(lambda match: plain_date(match.group(0)), text)


# ---------------------------------------------------------------------------------------------
# Schedule


def _as_launches(sites: Mapping[str, date | str] | Sequence[tuple[str, date | str]]) -> list[tuple[str, date]]:
    pairs = list(sites.items()) if isinstance(sites, Mapping) else list(sites)
    out = []
    for site, launch in pairs:
        day = launch if isinstance(launch, date) else date.fromisoformat(str(launch)[:10])
        out.append((str(site), day))
    return sorted(out)


def schedule_check(
    sites: Mapping[str, date | str] | Sequence[tuple[str, date | str]],
    today: date,
) -> list[dict[str, Any]]:
    """For each site: its day 7, 14, 30, 60 and 90 dates, and for each marker date whether it can add a graded outcome.

    A graded outcome needs the day 90 reading, so a site can contribute to a marker only when
    launch + 90 days is on or before the marker date.
    """
    out = []
    for site, launch in _as_launches(sites):
        days = {f"day_{n}": (launch + timedelta(days=n)).isoformat() for n in SCHEDULE_DAYS}
        outcome = launch + timedelta(days=OUTCOME_DAY)
        rows = []
        for item in MARKERS:
            day_then = (item.date - launch).days
            reached = [stage for stage, offset in STAGE_DAYS.items() if day_then >= offset]
            can = outcome <= item.date
            if can:
                note = f"{site} reaches day 90 on {outcome.isoformat()}, on or before the marker date."
            elif day_then < 0:
                note = f"{site} has not launched by the marker date."
            else:
                note = f"{site} is at day {day_then} on the marker date. Day 90 is {outcome.isoformat()}."
            rows.append(
                {
                    "marker": item.number,
                    "date": item.date.isoformat(),
                    "day_on_marker_date": day_then,
                    "latest_stage_reached": reached[-1] if reached else None,
                    "can_contribute_graded_outcome": can,
                    "note": note,
                }
            )
        out.append(
            {
                "site": site,
                "launch_date": launch.isoformat(),
                "days_since_launch": (today - launch).days,
                **days,
                "markers": rows,
            }
        )
    return out


def latest_launch_for(marker_date: date) -> date:
    """The last launch date whose day 90 falls on or before the given date."""
    return marker_date - timedelta(days=OUTCOME_DAY)


def plan_schedule_problems() -> list[dict[str, Any]]:
    """The plan's own schedule, checked with date arithmetic. ok False means the plan has a problem there."""
    m4 = marker(4).date
    m5 = marker(5).date
    seed_90 = PLAN_SEED_LAUNCH + timedelta(days=OUTCOME_DAY)
    seed_60 = PLAN_SEED_LAUNCH + timedelta(days=60)
    week1_90 = PLAN_START + timedelta(days=OUTCOME_DAY)
    pilot_90 = PLAN_PILOT_START + timedelta(days=OUTCOME_DAY)
    last_for_m5 = latest_launch_for(m5)
    last_for_m4 = latest_launch_for(m4)
    seed_day_on_m4 = (m4 - PLAN_SEED_LAUNCH).days
    return [
        {
            "name": "seed day 90",
            "value": seed_90.isoformat(),
            "target": PLAN_SEED_DAY_90.isoformat(),
            "ok": seed_90 == PLAN_SEED_DAY_90,
            "note": f"Seed sites launched on {PLAN_SEED_LAUNCH.isoformat()} reach day 90 on {seed_90.isoformat()}.",
        },
        {
            "name": "seed day 60",
            "value": seed_60.isoformat(),
            "target": PLAN_SEED_DAY_60.isoformat(),
            "ok": seed_60 == PLAN_SEED_DAY_60,
            "note": f"Seed sites launched on {PLAN_SEED_LAUNCH.isoformat()} reach day 60 on {seed_60.isoformat()}.",
        },
        {
            "name": "seed launch week",
            "value": week1_90.isoformat(),
            "target": PLAN_SEED_DAY_90.isoformat(),
            "ok": week1_90 == PLAN_SEED_DAY_90,
            "note": (
                f"Step 3 says the seed sites launch in week 1. A launch on {PLAN_START.isoformat()} reaches day 90 on "
                f"{week1_90.isoformat()}, not {PLAN_SEED_DAY_90.isoformat()}. The launch date must be frozen."
            ),
        },
        {
            "name": "pilot launch for marker 5",
            "value": PLAN_PILOT_START.isoformat(),
            "target": last_for_m5.isoformat(),
            "ok": PLAN_PILOT_START <= last_for_m5,
            "note": (
                f"A pilot must launch by {last_for_m5.isoformat()} to reach day 90 by {m5.isoformat()}. "
                f"Pilots start on {PLAN_PILOT_START.isoformat()} and reach day 90 on {pilot_90.isoformat()} at the earliest. "
                "No pilot can add a graded outcome to marker 5."
            ),
        },
        {
            "name": "seed outcome for marker 4",
            "value": seed_90.isoformat(),
            "target": m4.isoformat(),
            "ok": seed_90 <= m4,
            "note": (
                f"Marker 4 on {m4.isoformat()} has no seed day 90 outcome. The seed sites are at day {seed_day_on_m4} "
                f"that day. A launch had to happen by {last_for_m4.isoformat()} to be graded by then."
            ),
        },
    ]
