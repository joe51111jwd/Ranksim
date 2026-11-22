"""The no-leakage guard: every feature value has a snapshot date.

A forecast made as of a date may use only what was knowable on that date. A
value is wrapped in `Dated` with the date of the snapshot it came from. An
`AsOfGuard` hands the value out only when that date is on or before `as_of`,
and raises `LeakError` otherwise. A source with no dated history is undated:
it is refused in backtests and allowed only for a live forecast made today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any


class LeakError(ValueError):
    """A value from after the forecast date was about to be used."""


def as_date(value: date | datetime | str) -> date:
    """A calendar date from a date, a datetime, or text like 2026-11-22 or 2026-11-22T10:00:00Z."""
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 7:  # a month, 2026-11: its first day
        text += "-01"
    return date.fromisoformat(text[:10])


def parse_utc(text: str) -> datetime:
    """An aware UTC datetime from ISO text. A trailing Z and a missing zone both mean UTC."""
    cleaned = str(text).strip().replace("Z", "+00:00")
    if " " in cleaned and "T" not in cleaned:
        cleaned = cleaned.replace(" ", "T", 1)
    if cleaned.endswith(" +00:00"):
        cleaned = cleaned[:-7] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def utc_iso(moment: datetime) -> str:
    """ISO text in UTC with a trailing Z, to the second."""
    aware = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    """The first day of the month `months` after the month of `day`."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True)
class Dated:
    """A value with where it came from and the date of that snapshot. None means no dated history."""

    value: Any
    source: str
    snapshot_date: date | None


@dataclass
class AsOfGuard:
    """Hands out dated values only when they were knowable on `as_of`."""

    as_of: date
    allow_undated: bool = False
    used: list[tuple[str, str]] = field(default_factory=list)

    def take(self, dated: Dated | None) -> Any:
        """The value, or None when there is none. Raises LeakError for a later or undated snapshot."""
        if dated is None:
            return None
        if dated.snapshot_date is None:
            if not self.allow_undated:
                raise LeakError(
                    f"{dated.source} has no snapshot date, so it cannot be shown to be knowable on {self.as_of}. "
                    "It is allowed only for a live forecast."
                )
            self.used.append((dated.source, "undated"))
            return dated.value
        if dated.snapshot_date > self.as_of:
            raise LeakError(
                f"{dated.source} snapshot {dated.snapshot_date} is after the forecast date {self.as_of}."
            )
        self.used.append((dated.source, dated.snapshot_date.isoformat()))
        return dated.value

    def knowable_table(self) -> list[dict[str, str]]:
        """One row per source used: the newest snapshot taken. For the report's knowable-at-launch table."""
        newest: dict[str, str] = {}
        for source, snapshot in self.used:
            if source not in newest or snapshot > newest[source]:
                newest[source] = snapshot
        return [{"source": source, "newest_snapshot": snapshot} for source, snapshot in sorted(newest.items())]
