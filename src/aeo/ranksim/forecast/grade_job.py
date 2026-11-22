"""The nightly grading job: grade every forecast whose day-90 outcome is due.

The forecasts are PREDICTED. The readings they are graded on are REAL clean
readings. This job only chooses which stored reading grades which stored
forecast. It never makes a reading up and never reads the wall clock: the
date it grades as of is an argument. (The grade rows are stamped by the
scoreboard's own injected clock.)

Choice of reading: among the non-degraded readings of the forecast's search,
location and device taken on days 87 to 93 after launch, taken after the
forecast was made, and taken on or before `today`, the one closest to day 90.
Ties go to the earlier reading.

OUR READING of "closest": a reading chosen too early could be beaten by a
closer one still to come, and an original grade can never be replaced. So a
reading `d` days from day 90 is used only once `today` is on or after day
90 + d, when no closer reading can still arrive. Until then the forecast is
listed as waiting for a closer reading. The job is idempotent: a forecast
with an original grade is never graded again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any

from .asof import parse_utc
from .config import OUTCOME_DAY
from .scoreboard import WINDOW_LAST_DAY, Scoreboard, ScoreboardRefusal, WindowReading
from .types import REAL

SUMMARY_KEYS = (
    "graded",
    "already_graded",
    "not_yet_due",
    "waiting_for_closer_reading",
    "due_no_reading_in_window",
    "due_only_degraded_readings",
    "due_only_readings_before_forecast",
    "window_closed_no_reading",
    "refused",
)


def choose_reading(readings: list[WindowReading]) -> WindowReading | None:
    """The usable reading closest to day 90. Ties go to the earlier one."""
    usable = [r for r in readings if not r.degraded and not r.before_forecast]
    if not usable:
        return None
    return min(usable, key=lambda r: (abs(r.day_after_launch - OUTCOME_DAY), parse_utc(r.taken_at), r.reading_id))


def run(scoreboard: Scoreboard, today: date) -> dict[str, Any]:
    """Grade what is due as of `today`. Returns the ids in each outcome, sorted, and the counts."""
    if not isinstance(today, date) or isinstance(today, datetime):
        raise TypeError("today must be a datetime.date, not a datetime")
    scoreboard.require_verified()
    out: dict[str, Any] = {key: [] for key in SUMMARY_KEYS}
    for record in scoreboard.forecasts():
        fid = record.forecast_id
        grades = scoreboard.grades_for(fid)
        if any(g.kind == "original" for g in grades):
            out["already_graded"].append(fid)
            continue
        if record.outcome_due_date > today:
            out["not_yet_due"].append(fid)
            continue
        window = scoreboard.window_readings(fid, taken_on_or_before=today)
        if record.source != "synthetic":
            window = [r for r in window if r.label == REAL]
        window_closed = today > record.launch_date + timedelta(days=WINDOW_LAST_DAY)
        if not window:
            out["due_no_reading_in_window"].append(fid)
            if window_closed:
                out["window_closed_no_reading"].append(fid)
            continue
        if all(r.degraded for r in window):
            out["due_only_degraded_readings"].append(fid)
            continue
        chosen = choose_reading(window)
        if chosen is None:
            out["due_only_readings_before_forecast"].append(fid)
            continue
        distance = abs(chosen.day_after_launch - OUTCOME_DAY)
        if today < record.launch_date + timedelta(days=OUTCOME_DAY + distance):
            out["waiting_for_closer_reading"].append(fid)
            continue
        try:
            scoreboard.grade(fid, chosen.reading_id, kind="original")
        except ScoreboardRefusal as error:
            out["refused"].append({"forecast_id": fid, "reason": str(error)})
            continue
        out["graded"].append(fid)
    for key in SUMMARY_KEYS:
        if key == "refused":
            out[key].sort(key=lambda item: item["forecast_id"])
        else:
            out[key].sort()
    out["counts"] = {key: len(out[key]) for key in SUMMARY_KEYS}
    out["today"] = today.isoformat()
    return out


def render_summary(summary: dict[str, Any]) -> str:
    """The job's summary in short plain sentences."""
    words = {
        "graded": "graded now",
        "already_graded": "already graded",
        "not_yet_due": "not yet due",
        "waiting_for_closer_reading": "due, waiting in case a reading closer to day 90 arrives",
        "due_no_reading_in_window": "due with no reading in days 87 to 93",
        "due_only_degraded_readings": "due with only degraded readings in the window",
        "due_only_readings_before_forecast": "due with only readings taken before the forecast was made",
        "window_closed_no_reading": "past day 93 with no reading, so they stay ungraded",
        "refused": "refused by the scoreboard",
    }
    lines = [f"Grading job as of {summary['today']}."]
    for key in SUMMARY_KEYS:
        lines.append(f"Forecasts {words[key]}: {summary['counts'][key]}.")
    for item in summary["refused"]:
        lines.append(f"Refused {item['forecast_id']}: {item['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forecast grade", description="Grade forecasts whose outcome is due.")
    parser.add_argument("--date", required=True, help="the UTC date to grade as of, for example 2027-03-01")
    parser.add_argument("--url", default=None, help="scoreboard database URL")
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(args.date)
    except ValueError:
        print(f"Not an ISO date: {args.date}", file=sys.stderr)
        return 2
    board = Scoreboard(args.url)
    try:
        summary = run(board, today)
    except ScoreboardRefusal as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        board.close()
    print(render_summary(summary))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
