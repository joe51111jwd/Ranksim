"""The scoreboard (part 2): every forecast, every reading, and every grade, in one SQLite file.

What is REAL here: pulls and clean readings (observed Google positions) and
grades (a forecast compared with the clean position read about 90 days after
launch). What is PREDICTED: forecasts. A forecast row carries the label text
PREDICTED and a grade row carries REAL. Nothing in this module makes a
position up: a site that is absent from a reading is NT50, and a target URL
position that cannot be worked out from the stored pulls stays None.

Forecasts and grades are append-only. Each row is hash-chained with
`chain.row_hash` over its canonical payload, the chain cannot fork because
`prev_hash` is unique, and SQLite triggers made with the tables refuse any
UPDATE or DELETE. `verify()` recomputes both chains from the stored columns,
so an edit made with the triggers dropped is still caught. Every report calls
`require_verified()` first.

The refusals of docs/plan/IMPLEMENTATION_SPEC.md section 4 are enforced at
write time and each raises `ScoreboardRefusal` with a plain sentence.
All timestamps are UTC ISO text (second precision, trailing Z). All dates are ISO dates.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from aeo.crawl.urls import normalize_url
from aeo.ranksim.site_history import site_key

from .asof import as_date, parse_utc, utc_iso
from .chain import GENESIS, canonical_json, row_hash, sha256_text, verify_chain
from .config import (
    DEFAULT_DEVICE,
    DIFFICULTY_TERCILES,
    EXCLUDED_SITES,
    LABELS,
    MIN_KEPT_PULLS,
    NT50,
    OUTCOME_DAY,
    OUTCOME_WINDOW_DAYS,
    SOURCES,
    STAGE_DAYS,
    TOP_DEPTH,
    forecast_paths,
)
from .grading import RULE_V1, GradedCell, GradingRule
from .types import PREDICTED, REAL, SYNTHETIC, CleanReading, Forecast, OrganicHit, Pull, PullSpec, SitePosition

GRADE_KINDS = ("original", "regrade")
RECORD_KINDS = ("original", "regrade", "latest")
# Columns added to `readings` after the first databases were made. Opening an
# older database adds them (empty). Readings are not hash chained, so this
# changes no forecast or grade hash.
READING_PROVENANCE_COLUMNS: dict[str, str] = {"reader_version": "VARCHAR(64)", "degraded_reason": "TEXT"}
WINDOW_FIRST_DAY = OUTCOME_DAY - OUTCOME_WINDOW_DAYS  # 87
WINDOW_LAST_DAY = OUTCOME_DAY + OUTCOME_WINDOW_DAYS  # 93
APPEND_RETRIES = 5
EXPORT_FILES = (
    "sites.jsonl",
    "searches.jsonl",
    "model_versions.jsonl",
    "pulls.jsonl",
    "readings.jsonl",
    "forecasts.jsonl",
    "grades.jsonl",
    "core_updates.jsonl",
    "freezes.jsonl",
)


class ScoreboardError(RuntimeError):
    """The scoreboard cannot do what was asked (bad database, bad export)."""


class ScoreboardRefusal(ValueError):
    """A write or a report the rules forbid. The message says which rule, in a plain sentence."""


class ChainBroken(ScoreboardRefusal):
    """The hash chain does not verify, so no report may be printed."""


class PoolingRefused(ScoreboardRefusal):
    """Seed, pilot, backtest and synthetic results are never pooled into one number."""


def search_key(search: str) -> str:
    """The matching form of a search: lower case, single spaces."""
    return " ".join(str(search).lower().split())


def _compact(value: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value).lower())


def _iso_date(value: date | datetime | str) -> str:
    return as_date(value).isoformat()


def _json_safe(payload: Any) -> Any:
    """The payload exactly as it will come back from the database (JSON round trip, NaN refused)."""
    try:
        return json.loads(canonical_json(payload))
    except (TypeError, ValueError) as error:
        raise ScoreboardRefusal(f"The record cannot be stored as plain JSON: {error}") from error


def _check_position(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScoreboardRefusal(f"The {name} must be an integer from 1 to 50, or NT50 ({NT50}). Got {value!r}.")
    if not 1 <= value <= NT50:
        raise ScoreboardRefusal(f"The {name} must be from 1 to 50, or NT50 ({NT50}). Got {value}.")
    return value


def _norm_url(url: str) -> str | None:
    try:
        return normalize_url(url)
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------
# Tables


class ScoreboardBase(DeclarativeBase):
    """The scoreboard's own metadata. It is not aeo.ranksim.store.Base."""


class SiteRow(ScoreboardBase):
    __tablename__ = "sites"

    site: Mapped[str] = mapped_column(String(253), primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    niche: Mapped[str | None] = mapped_column(String(200), nullable=True)
    launch_date: Mapped[str] = mapped_column(String(10))
    launch_date_source: Mapped[str] = mapped_column(String(500))
    built_by: Mapped[str] = mapped_column(String(100))
    brand_terms: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(20))


class SearchRow(ScoreboardBase):
    __tablename__ = "searches"
    __table_args__ = (UniqueConstraint("site", "search_key", name="uq_searches_site_search"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site: Mapped[str] = mapped_column(ForeignKey("sites.site"))
    search: Mapped[str] = mapped_column(String(500))
    search_key: Mapped[str] = mapped_column(String(500), index=True)
    search_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    difficulty_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_brand: Mapped[bool] = mapped_column(Boolean)
    target_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    location_code: Mapped[int] = mapped_column(Integer)
    device: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[str] = mapped_column(String(20))


class PullRow(ScoreboardBase):
    __tablename__ = "pulls"

    pull_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(500))
    search: Mapped[str] = mapped_column(String(500))
    search_key: Mapped[str] = mapped_column(String(500), index=True)
    location_code: Mapped[int] = mapped_column(Integer)
    language_code: Mapped[str] = mapped_column(String(16))
    device: Mapped[str] = mapped_column(String(16))
    depth: Mapped[int] = mapped_column(Integer)
    requested_at: Mapped[str] = mapped_column(String(40))
    completed_at: Mapped[str] = mapped_column(String(40))
    ok: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_datetime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    check_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean)
    spell_corrected: Mapped[bool] = mapped_column(Boolean)
    repaired: Mapped[bool] = mapped_column(Boolean)
    hits: Mapped[list] = mapped_column(JSON)
    raw_path: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    excluded_hits_dropped: Mapped[int] = mapped_column(Integer)
    stored_at: Mapped[str] = mapped_column(String(20))


class ReadingRow(ScoreboardBase):
    __tablename__ = "readings"

    reading_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    search: Mapped[str] = mapped_column(String(500))
    search_key: Mapped[str] = mapped_column(String(500), index=True)
    location_code: Mapped[int] = mapped_column(Integer)
    device: Mapped[str] = mapped_column(String(16))
    taken_at: Mapped[str] = mapped_column(String(20), index=True)
    pulls_total: Mapped[int] = mapped_column(Integer)
    pulls_kept: Mapped[int] = mapped_column(Integer)
    dropped: Mapped[list] = mapped_column(JSON)
    degraded: Mapped[bool] = mapped_column(Boolean)
    pulls_not_independent: Mapped[bool] = mapped_column(Boolean)
    pull_ids: Mapped[list] = mapped_column(JSON)
    label: Mapped[str] = mapped_column(String(16))
    excluded_positions_dropped: Mapped[int] = mapped_column(Integer)
    stored_at: Mapped[str] = mapped_column(String(20))
    reader_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    degraded_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ReadingPositionRow(ScoreboardBase):
    __tablename__ = "reading_positions"
    __table_args__ = (UniqueConstraint("reading_id", "site", name="uq_reading_positions_site"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reading_id: Mapped[str] = mapped_column(ForeignKey("readings.reading_id"), index=True)
    site: Mapped[str] = mapped_column(String(253))
    position: Mapped[int] = mapped_column(Integer)
    pull_positions: Mapped[list] = mapped_column(JSON)
    best_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class ModelVersionRow(ScoreboardBase):
    __tablename__ = "model_versions"

    version: Mapped[str] = mapped_column(String(200), primary_key=True)
    frozen_at: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    artefact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ForecastRow(ScoreboardBase):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("site", "search_key", "stage", "model_version", name="uq_forecasts_one_per_stage_model"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    forecast_id: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(16))
    site: Mapped[str] = mapped_column(ForeignKey("sites.site"))
    search: Mapped[str] = mapped_column(String(500))
    search_key: Mapped[str] = mapped_column(String(500), index=True)
    search_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_brand: Mapped[bool] = mapped_column(Boolean)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    niche: Mapped[str | None] = mapped_column(String(200), nullable=True)
    built_by: Mapped[str] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[str] = mapped_column(String(10))
    point: Mapped[int] = mapped_column(Integer)
    range_low: Mapped[int] = mapped_column(Integer)
    range_high: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(ForeignKey("model_versions.version"))
    probs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    notes: Mapped[dict] = mapped_column(JSON)
    kind: Mapped[str] = mapped_column(String(16))
    label_text: Mapped[str] = mapped_column(String(16))
    created_at_utc: Mapped[str] = mapped_column(String(20))
    launch_date: Mapped[str] = mapped_column(String(10))
    outcome_due_date: Mapped[str] = mapped_column(String(10))
    lead_days: Mapped[int] = mapped_column(Integer)
    grading_rule_version: Mapped[str] = mapped_column(String(32))
    grading_rule_hash: Mapped[str] = mapped_column(String(64))
    code_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_reading_ids: Mapped[list] = mapped_column(JSON)
    location_code: Mapped[int] = mapped_column(Integer)
    device: Mapped[str] = mapped_column(String(16))
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)


class GradeRow(ScoreboardBase):
    __tablename__ = "grades"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grade_id: Mapped[str] = mapped_column(String(64), unique=True)
    forecast_id: Mapped[str] = mapped_column(ForeignKey("forecasts.forecast_id"), index=True)
    forecast_row_hash: Mapped[str] = mapped_column(String(64))
    reading_id: Mapped[str] = mapped_column(ForeignKey("readings.reading_id"))
    reading_taken_at: Mapped[str] = mapped_column(String(20))
    days_after_launch: Mapped[int] = mapped_column(Integer)
    site: Mapped[str] = mapped_column(String(253))
    search: Mapped[str] = mapped_column(String(500))
    outcome_position: Mapped[int] = mapped_column(Integer)
    hit: Mapped[bool] = mapped_column(Boolean)
    target_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    target_url_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_url_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    grading_rule_version: Mapped[str] = mapped_column(String(32))
    grading_rule_hash: Mapped[str] = mapped_column(String(64))
    graded_at_utc: Mapped[str] = mapped_column(String(20))
    label_text: Mapped[str] = mapped_column(String(16))
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)


class CoreUpdateRow(ScoreboardBase):
    __tablename__ = "core_updates"
    __table_args__ = (UniqueConstraint("name", "started_on", name="uq_core_updates_name_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(64))
    started_on: Mapped[str] = mapped_column(String(10))
    ended_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2000))
    recorded_at: Mapped[str] = mapped_column(String(20))
    ended_recorded_at: Mapped[str | None] = mapped_column(String(20), nullable=True)


class FreezeRow(ScoreboardBase):
    __tablename__ = "freezes"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[Any] = mapped_column(JSON)
    frozen_at: Mapped[str] = mapped_column(String(20))


APPEND_ONLY_TABLES = ("forecasts", "grades", "pulls", "readings", "reading_positions", "freezes", "model_versions")

_CORE_UPDATE_TRIGGERS = {
    "core_updates_no_delete": (
        "CREATE TRIGGER IF NOT EXISTS core_updates_no_delete BEFORE DELETE ON core_updates "
        "BEGIN SELECT RAISE(ABORT, 'core_updates rows cannot be deleted'); END"
    ),
    "core_updates_only_end": (
        "CREATE TRIGGER IF NOT EXISTS core_updates_only_end BEFORE UPDATE ON core_updates "
        "WHEN OLD.ended_on IS NOT NULL OR NEW.ended_on IS NULL OR NEW.id IS NOT OLD.id "
        "OR NEW.name IS NOT OLD.name OR NEW.kind IS NOT OLD.kind OR NEW.started_on IS NOT OLD.started_on "
        "OR NEW.source_url IS NOT OLD.source_url OR NEW.recorded_at IS NOT OLD.recorded_at "
        "OR OLD.ended_recorded_at IS NOT NULL "
        "BEGIN SELECT RAISE(ABORT, 'core_updates rows only accept a first end date'); END"
    ),
}


def _trigger_sql() -> dict[str, tuple[str, str]]:
    """Trigger name -> (table, CREATE statement)."""
    out: dict[str, tuple[str, str]] = {}
    for table in APPEND_ONLY_TABLES:
        for action in ("UPDATE", "DELETE"):
            name = f"{table}_no_{action.lower()}"
            out[name] = (
                table,
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {action} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only: {action} refused'); END",
            )
    for name, sql in _CORE_UPDATE_TRIGGERS.items():
        out[name] = ("core_updates", sql)
    return out


TRIGGERS = _trigger_sql()

for _name, (_table, _sql) in TRIGGERS.items():
    event.listen(ScoreboardBase.metadata.tables[_table], "after_create", DDL(_sql))


# ---------------------------------------------------------------------------------------------
# Records handed back to callers


@dataclass(frozen=True)
class ForecastRecord:
    """One stored forecast. PREDICTED."""

    forecast_id: str
    seq: int
    source: str
    site: str
    search: str
    stage: str
    as_of: date
    point: int
    range_low: int
    range_high: int
    label: str
    model_version: str
    target_url: str | None
    is_brand: bool
    difficulty: str | None
    niche: str | None
    created_at_utc: str
    launch_date: date
    outcome_due_date: date
    lead_days: int
    location_code: int
    device: str
    input_reading_ids: tuple[str, ...]
    row_hash: str
    label_text: str = PREDICTED


@dataclass(frozen=True)
class GradeRecord:
    """One stored grade. REAL."""

    grade_id: str
    seq: int
    forecast_id: str
    reading_id: str
    reading_taken_at: str
    days_after_launch: int
    outcome_position: int
    hit: bool
    target_url_position: int | None
    target_url_hit: bool | None
    kind: str
    reason: str | None
    grading_rule_version: str
    graded_at_utc: str
    row_hash: str
    label_text: str = REAL


@dataclass(frozen=True)
class WindowReading:
    """A reading of the forecast's search, location and device that falls inside days 87 to 93."""

    reading_id: str
    taken_at: str
    day_after_launch: int
    degraded: bool
    label: str
    before_forecast: bool


@dataclass(frozen=True)
class GradedRecord:
    """A graded cell with what the reports need beside it."""

    forecast_id: str
    grade_id: str
    kind: str
    reason: str | None
    cell: GradedCell
    as_of: date
    outcome_date: date
    reading_id: str
    hit: bool
    target_url: str | None
    target_url_position: int | None
    target_url_hit: bool | None
    grading_rule_version: str
    model_version: str
    built_by: str
    split: str | None


@dataclass(frozen=True)
class ChainStatus:
    """The result of recomputing both chains."""

    ok: bool
    forecasts_ok: bool
    grades_ok: bool
    links_ok: bool
    triggers_ok: bool
    forecasts_head: str
    grades_head: str
    forecasts_checked: int
    grades_checked: int
    forecasts_total: int
    grades_total: int
    problems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "forecasts_ok": self.forecasts_ok,
            "grades_ok": self.grades_ok,
            "links_ok": self.links_ok,
            "triggers_ok": self.triggers_ok,
            "forecasts_head": self.forecasts_head,
            "grades_head": self.grades_head,
            "forecasts_total": self.forecasts_total,
            "grades_total": self.grades_total,
            "problems": list(self.problems),
        }


def _hash_payload(row: ForecastRow | GradeRow) -> dict[str, Any]:
    skip = {"seq", "prev_hash", "row_hash"}
    return {column.name: getattr(row, column.name) for column in row.__table__.columns if column.name not in skip}


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _forecast_record(row: ForecastRow) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=row.forecast_id,
        seq=row.seq,
        source=row.source,
        site=row.site,
        search=row.search,
        stage=row.stage,
        as_of=date.fromisoformat(row.as_of),
        point=row.point,
        range_low=row.range_low,
        range_high=row.range_high,
        label=row.label,
        model_version=row.model_version,
        target_url=row.target_url,
        is_brand=bool(row.is_brand),
        difficulty=row.difficulty,
        niche=row.niche,
        created_at_utc=row.created_at_utc,
        launch_date=date.fromisoformat(row.launch_date),
        outcome_due_date=date.fromisoformat(row.outcome_due_date),
        lead_days=row.lead_days,
        location_code=row.location_code,
        device=row.device,
        input_reading_ids=tuple(row.input_reading_ids or ()),
        row_hash=row.row_hash,
        label_text=row.label_text,
    )


def _grade_record(row: GradeRow) -> GradeRecord:
    return GradeRecord(
        grade_id=row.grade_id,
        seq=row.seq,
        forecast_id=row.forecast_id,
        reading_id=row.reading_id,
        reading_taken_at=row.reading_taken_at,
        days_after_launch=row.days_after_launch,
        outcome_position=row.outcome_position,
        hit=bool(row.hit),
        target_url_position=row.target_url_position,
        target_url_hit=row.target_url_hit,
        kind=row.kind,
        reason=row.reason,
        grading_rule_version=row.grading_rule_version,
        graded_at_utc=row.graded_at_utc,
        row_hash=row.row_hash,
        label_text=row.label_text,
    )


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ScoreboardError("The scoreboard runs on SQLite only.")
    database = parsed.database or ""
    memory = database in ("", ":memory:")
    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if memory:
        kwargs["poolclass"] = StaticPool
    else:
        Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        if not memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


# ---------------------------------------------------------------------------------------------


class Scoreboard:
    """The forecast scoreboard. Construct with a SQLAlchemy SQLite URL, or none for config.forecast_paths()."""

    def __init__(
        self,
        url: str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        rule: GradingRule = RULE_V1,
        code_commit: str | None = None,
    ) -> None:
        self.url = url or forecast_paths().scoreboard_url
        self.engine = _make_engine(self.url)
        self._clock = clock or _default_clock
        self.rule = rule
        self.code_commit = code_commit
        self._lock = threading.RLock()
        ScoreboardBase.metadata.create_all(self.engine)
        self.add_missing_columns()
        self.install_triggers()
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    # -- plumbing ------------------------------------------------------------------------------

    def close(self) -> None:
        self.engine.dispose()

    def install_triggers(self) -> None:
        """Create any missing append-only trigger. The tables get them when they are created; this repairs a dropped one."""
        with self.engine.begin() as connection:
            for _table, sql in TRIGGERS.values():
                connection.execute(text(sql))

    def add_missing_columns(self) -> list[str]:
        """Add the reading provenance columns to a database made before they existed. Returns the columns added.

        The new columns start empty (None) for the readings already stored.
        Readings are not hash chained, so no chain changes.
        """
        added: list[str] = []
        with self.engine.begin() as connection:
            present = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(readings)")).all()}
            for name, kind in READING_PROVENANCE_COLUMNS.items():
                if name not in present:
                    connection.execute(text(f"ALTER TABLE readings ADD COLUMN {name} {kind}"))
                    added.append(name)
        return added

    def triggers_present(self) -> list[str]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).all()
        return sorted(str(row[0]) for row in rows)

    def _now(self) -> datetime:
        moment = self._clock()
        if moment.tzinfo is None:
            raise ScoreboardError("The scoreboard clock must return an aware UTC datetime.")
        return parse_utc(utc_iso(moment))

    def _session(self) -> Session:
        return self._sessions()

    # -- reference rows ------------------------------------------------------------------------

    def add_site(
        self,
        site: str,
        *,
        source: str,
        launch_date: date | str,
        launch_date_source: str,
        built_by: str,
        niche: str | None = None,
        brand_terms: Sequence[str] = (),
    ) -> None:
        """Register a site (a registrable domain) with its launch date and where that date came from."""
        name = str(site).strip().lower()
        if not name or site_key(name) != name:
            raise ScoreboardRefusal(f"The site must be a registrable domain such as example.com. Got {site!r}.")
        if name in EXCLUDED_SITES:
            raise ScoreboardRefusal(f"{name} is excluded. Its rows are never used to build or test.")
        if source not in SOURCES:
            raise ScoreboardRefusal(f"The source must be one of {', '.join(SOURCES)}. Got {source!r}.")
        if not str(launch_date_source).strip():
            raise ScoreboardRefusal("The launch date needs a source that says where it came from.")
        if not str(built_by).strip():
            raise ScoreboardRefusal("built_by must say who built the site.")
        terms = sorted({str(term).strip().lower() for term in brand_terms if str(term).strip()})
        values = {
            "site": name,
            "source": source,
            "niche": niche,
            "launch_date": _iso_date(launch_date),
            "launch_date_source": str(launch_date_source),
            "built_by": str(built_by),
            "brand_terms": terms,
        }
        with self._lock, self._session() as session, session.begin():
            existing = session.get(SiteRow, name)
            if existing is not None:
                same = all(getattr(existing, key) == value for key, value in values.items())
                if same:
                    return
                raise ScoreboardRefusal(f"The site {name} is already registered with different details.")
            session.add(SiteRow(created_at=utc_iso(self._now()), **values))

    def add_search(
        self,
        site: str,
        search: str,
        *,
        location_code: int,
        device: str = DEFAULT_DEVICE,
        search_group: str | None = None,
        difficulty: str | None = None,
        difficulty_value: float | None = None,
        is_brand: bool | None = None,
        target_url: str | None = None,
    ) -> None:
        """Register one target search of a site. is_brand None means: work it out from the site's brand terms."""
        key = search_key(search)
        if not key:
            raise ScoreboardRefusal("The search text is empty.")
        if difficulty is not None and difficulty not in DIFFICULTY_TERCILES:
            raise ScoreboardRefusal(f"difficulty must be one of {', '.join(DIFFICULTY_TERCILES)}, or None.")
        if isinstance(location_code, bool) or not isinstance(location_code, int):
            raise ScoreboardRefusal("location_code must be the integer code of the pinned city.")
        value = None if difficulty_value is None or difficulty_value != difficulty_value else float(difficulty_value)
        if target_url is not None and _norm_url(target_url) is None:
            raise ScoreboardRefusal(f"The target URL {target_url!r} is not an http or https URL.")
        with self._lock, self._session() as session, session.begin():
            site_row = session.get(SiteRow, str(site).strip().lower())
            if site_row is None:
                raise ScoreboardRefusal(f"The site {site!r} is not registered.")
            if is_brand is None:
                terms = list(site_row.brand_terms or []) or [site_row.site.split(".")[0]]
                compact = _compact(key)
                brand = any(_compact(term) and _compact(term) in compact for term in terms)
            else:
                brand = bool(is_brand)
            dup = session.scalar(select(SearchRow).where(SearchRow.site == site_row.site, SearchRow.search_key == key))
            if dup is not None:
                raise ScoreboardRefusal(f"The search {search!r} is already registered for {site_row.site}.")
            session.add(
                SearchRow(
                    site=site_row.site,
                    search=" ".join(str(search).split()),
                    search_key=key,
                    search_group=search_group,
                    difficulty=difficulty,
                    difficulty_value=value,
                    is_brand=brand,
                    target_url=target_url,
                    location_code=location_code,
                    device=device,
                    created_at=utc_iso(self._now()),
                )
            )

    def register_model_version(
        self,
        version: str,
        *,
        description: str,
        artefact_sha256: str | None = None,
        frozen_at: datetime | None = None,
    ) -> None:
        """Freeze a model version. A forecast may only name a version frozen at or before the forecast is made."""
        if not str(version).strip():
            raise ScoreboardRefusal("The model version is empty.")
        stamp = utc_iso(frozen_at) if frozen_at is not None else utc_iso(self._now())
        with self._lock, self._session() as session, session.begin():
            existing = session.get(ModelVersionRow, version)
            if existing is not None:
                if existing.description == description and existing.artefact_sha256 == artefact_sha256:
                    return
                raise ScoreboardRefusal(f"Model version {version} is already frozen with a different artefact.")
            session.add(
                ModelVersionRow(version=version, frozen_at=stamp, description=description, artefact_sha256=artefact_sha256)
            )

    # -- readings ------------------------------------------------------------------------------

    def add_pull(self, pull: Pull) -> None:
        """Store one pull. REAL. Hits for excluded sites are dropped and counted."""
        self.store_batch(pulls=(pull,))

    def add_reading(self, reading: CleanReading) -> None:
        """Store one clean reading and its positions. REAL (or SYNTHETIC when its label says so)."""
        self.store_batch(readings=(reading,))

    def store_batch(self, pulls: Sequence[Pull] = (), readings: Sequence[CleanReading] = ()) -> None:
        """Store many pulls and readings in ONE transaction: all of them, or none when one is refused.

        Each row is checked exactly as add_pull and add_reading check it. A row
        already stored with the same content is skipped. Pulls are written
        before readings.
        """
        pull_values = [self._pull_values(pull) for pull in pulls]
        reading_values = [self._reading_values(reading) for reading in readings]
        with self._lock, self._session() as session, session.begin():
            stamp = utc_iso(self._now())
            for values in pull_values:
                self._put_pull(session, values, stamp)
                session.flush()  # a pull id given twice in one batch is then checked like any stored pull
            for values, positions in reading_values:
                self._put_reading(session, values, positions, stamp)

    @staticmethod
    def _pull_values(pull: Pull) -> dict[str, Any]:
        hits = [hit for hit in pull.hits if site_key(hit.site or hit.domain) not in EXCLUDED_SITES]
        values = {
            "pull_id": pull.pull_id,
            "provider": pull.provider,
            "endpoint": pull.endpoint,
            "search": pull.spec.search,
            "search_key": search_key(pull.spec.search),
            "location_code": pull.spec.location_code,
            "language_code": pull.spec.language_code,
            "device": pull.spec.device,
            "depth": pull.spec.depth,
            "requested_at": pull.requested_at,
            "completed_at": pull.completed_at,
            "ok": bool(pull.ok),
            "status_code": pull.status_code,
            "status_message": pull.status_message,
            "task_id": pull.task_id,
            "provider_datetime": pull.provider_datetime,
            "check_url": pull.check_url,
            "cost": pull.cost,
            "partial": bool(pull.partial),
            "spell_corrected": bool(pull.spell_corrected),
            "repaired": bool(pull.repaired),
            "hits": _json_safe([hit.__dict__ for hit in hits]),
            "raw_path": pull.raw_path,
            "raw_sha256": pull.raw_sha256,
            "excluded_hits_dropped": len(pull.hits) - len(hits),
        }
        return values

    @staticmethod
    def _put_pull(session: Session, values: dict[str, Any], stamp: str) -> None:
        existing = session.get(PullRow, values["pull_id"])
        if existing is not None:
            if all(getattr(existing, key) == value for key, value in values.items()):
                return
            raise ScoreboardRefusal(f"Pull {values['pull_id']} is already stored with different content.")
        session.add(PullRow(stored_at=stamp, **values))

    @staticmethod
    def _reading_values(reading: CleanReading) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        try:
            taken = utc_iso(parse_utc(reading.taken_at))
        except ValueError as error:
            raise ScoreboardRefusal(f"The reading time {reading.taken_at!r} is not ISO text.") from error
        if not reading.degraded and reading.pulls_kept < MIN_KEPT_PULLS:
            raise ScoreboardRefusal(
                f"Reading {reading.reading_id} kept {reading.pulls_kept} pulls but is not marked degraded. "
                f"Fewer than {MIN_KEPT_PULLS} kept pulls is degraded."
            )
        if reading.label not in (REAL, SYNTHETIC):
            raise ScoreboardRefusal(f"A reading is labeled REAL or SYNTHETIC. Got {reading.label!r}.")
        positions = [entry for entry in reading.positions if site_key(entry.site) not in EXCLUDED_SITES]
        for entry in positions:
            _check_position(entry.position, f"position of {entry.site}")
        sites = [entry.site for entry in positions]
        if len(set(sites)) != len(sites):
            raise ScoreboardRefusal(f"Reading {reading.reading_id} lists a site twice.")
        values = {
            "reading_id": reading.reading_id,
            "search": reading.search,
            "search_key": search_key(reading.search),
            "location_code": reading.location_code,
            "device": reading.device,
            "taken_at": taken,
            "pulls_total": reading.pulls_total,
            "pulls_kept": reading.pulls_kept,
            "dropped": _json_safe([list(item) for item in reading.dropped]),
            "degraded": bool(reading.degraded),
            "pulls_not_independent": bool(reading.pulls_not_independent),
            "pull_ids": list(reading.pull_ids),
            "label": reading.label,
            "excluded_positions_dropped": len(reading.positions) - len(positions),
            "reader_version": reading.reader_version,
            "degraded_reason": reading.degraded_reason,
        }
        position_values = sorted(
            (
                {
                    "site": entry.site,
                    "position": entry.position,
                    "pull_positions": list(entry.pull_positions),
                    "best_url": entry.best_url,
                }
                for entry in positions
            ),
            key=lambda item: (item["position"], item["site"]),
        )
        return values, position_values

    def _put_reading(self, session: Session, values: dict[str, Any], position_values: list[dict[str, Any]], stamp: str) -> None:
        reading_id = values["reading_id"]
        existing = session.get(ReadingRow, reading_id)
        if existing is not None:
            stored = self._positions(session, reading_id)
            same_positions = [
                {"site": p.site, "position": p.position, "pull_positions": list(p.pull_positions), "best_url": p.best_url}
                for p in stored
            ] == position_values
            if same_positions and all(getattr(existing, key) == value for key, value in values.items()):
                return
            raise ScoreboardRefusal(f"Reading {reading_id} is already stored with different content.")
        session.add(ReadingRow(stored_at=stamp, **values))
        session.flush()
        for item in position_values:
            session.add(ReadingPositionRow(reading_id=reading_id, **item))

    @staticmethod
    def _positions(session: Session, reading_id: str) -> list[SitePosition]:
        rows = session.scalars(
            select(ReadingPositionRow)
            .where(ReadingPositionRow.reading_id == reading_id)
            .order_by(ReadingPositionRow.position, ReadingPositionRow.site)
        ).all()
        return [SitePosition(r.site, r.position, tuple(r.pull_positions or ()), r.best_url) for r in rows]

    def reading(self, reading_id: str) -> CleanReading:
        with self._session() as session:
            row = session.get(ReadingRow, reading_id)
            if row is None:
                raise ScoreboardRefusal(f"Reading {reading_id} is not stored.")
            return self._reading_from_row(session, row)

    def _reading_from_row(self, session: Session, row: ReadingRow) -> CleanReading:
        return CleanReading(
            reading_id=row.reading_id,
            search=row.search,
            location_code=row.location_code,
            device=row.device,
            taken_at=row.taken_at,
            pulls_total=row.pulls_total,
            pulls_kept=row.pulls_kept,
            dropped=tuple((str(a), str(b)) for a, b in (row.dropped or [])),
            degraded=bool(row.degraded),
            pulls_not_independent=bool(row.pulls_not_independent),
            positions=tuple(self._positions(session, row.reading_id)),
            pull_ids=tuple(row.pull_ids or ()),
            label=row.label,
            reader_version=row.reader_version,
            degraded_reason=row.degraded_reason,
        )

    def readings_for(
        self,
        search: str,
        location_code: int,
        device: str,
        *,
        before: datetime | None = None,
        on_or_before: date | datetime | None = None,
        non_degraded_only: bool = True,
        limit: int | None = None,
        label: str | None = None,
        not_before: datetime | None = None,
    ) -> list[CleanReading]:
        """Stored readings of one search, location and device, newest first (ties by reading id, last first).

        `before`: taken strictly before this UTC moment. `on_or_before`: a date
        keeps readings taken on or before that UTC day, a datetime keeps those
        taken at or before it. `not_before`: taken at or after this moment.
        `non_degraded_only` (the default) leaves degraded readings out. `label`
        keeps only REAL or only SYNTHETIC readings. `limit` caps the count.
        """
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        with self._session() as session:
            query = select(ReadingRow).where(
                ReadingRow.search_key == search_key(search),
                ReadingRow.location_code == int(location_code),
                ReadingRow.device == device,
            )
            if non_degraded_only:
                query = query.where(ReadingRow.degraded.is_(False))
            if label is not None:
                query = query.where(ReadingRow.label == label)
            rows = session.scalars(query.order_by(ReadingRow.taken_at.desc(), ReadingRow.reading_id.desc())).all()
            out: list[CleanReading] = []
            for row in rows:
                taken = parse_utc(row.taken_at)
                if before is not None and not taken < parse_utc(utc_iso(before)):
                    continue
                if not_before is not None and taken < parse_utc(utc_iso(not_before)):
                    continue
                if isinstance(on_or_before, datetime):
                    if taken > parse_utc(utc_iso(on_or_before)):
                        continue
                elif isinstance(on_or_before, date) and taken.date() > on_or_before:
                    continue
                out.append(self._reading_from_row(session, row))
                if limit is not None and len(out) >= limit:
                    break
            return out

    def pull(self, pull_id: str) -> Pull:
        with self._session() as session:
            row = session.get(PullRow, pull_id)
            if row is None:
                raise ScoreboardRefusal(f"Pull {pull_id} is not stored.")
            return _pull_from_row(row)

    def pulls_completed_between(
        self, start: datetime, end: datetime, *, search_keys: Sequence[str] | None = None
    ) -> list[Pull]:
        """Stored pulls whose completed time is at or after `start` and before `end` (UTC), oldest first.

        `search_keys` (matching forms, see search_key) keeps only those searches.
        """
        low, high = parse_utc(utc_iso(start)), parse_utc(utc_iso(end))
        with self._session() as session:
            query = select(PullRow)
            if search_keys is not None:
                query = query.where(PullRow.search_key.in_(sorted({search_key(key) for key in search_keys})))
            out = []
            for row in session.scalars(query).all():
                moment = parse_utc(row.completed_at)
                if low <= moment < high:
                    out.append((moment, row.pull_id, _pull_from_row(row)))
        return [pull for _moment, _id, pull in sorted(out, key=lambda item: (item[0], item[1]))]

    # -- forecasts -----------------------------------------------------------------------------

    def record_forecast(
        self,
        forecast: Forecast,
        *,
        input_reading_ids: Sequence[str] = (),
        code_commit: str | None = None,
    ) -> ForecastRecord:
        """Append one forecast to the chain, or refuse it with a plain sentence."""
        if not isinstance(forecast, Forecast):
            raise TypeError("record_forecast takes a types.Forecast")
        if forecast.stage not in STAGE_DAYS:
            raise ScoreboardRefusal(f"The stage must be one of {', '.join(STAGE_DAYS)}. Got {forecast.stage!r}.")
        point = _check_position(forecast.point, "point")
        low = _check_position(forecast.range_low, "range low")
        high = _check_position(forecast.range_high, "range high")
        if low > high:
            raise ScoreboardRefusal(f"The range low {low} is above the range high {high}.")
        if forecast.label not in LABELS:
            raise ScoreboardRefusal(f"The label must be one of {', '.join(LABELS)}. Got {forecast.label!r}.")
        if forecast.kind != PREDICTED:
            raise ScoreboardRefusal("A forecast is PREDICTED. Its kind says otherwise.")
        if not str(forecast.model_version).strip():
            raise ScoreboardRefusal("The forecast names no model version.")
        if forecast.target_url is not None and _norm_url(forecast.target_url) is None:
            raise ScoreboardRefusal(f"The target URL {forecast.target_url!r} is not an http or https URL.")
        as_of = as_date(forecast.as_of)
        reading_ids = sorted({str(item) for item in input_reading_ids})
        key = search_key(forecast.search)
        site = str(forecast.site).strip().lower()
        forecast_id = "fc_" + sha256_text(canonical_json([site, key, forecast.stage, forecast.model_version]))[:40]

        for _attempt in range(APPEND_RETRIES):
            with self._lock, self._session() as session:
                try:
                    with session.begin():
                        payload = self._forecast_payload(
                            session, forecast, site, key, as_of, point, low, high, reading_ids, forecast_id, code_commit
                        )
                        head = session.scalars(select(ForecastRow).order_by(ForecastRow.seq.desc()).limit(1)).first()
                        prev = head.row_hash if head is not None else GENESIS
                        if head is not None and parse_utc(head.created_at_utc) > parse_utc(payload["created_at_utc"]):
                            raise ScoreboardRefusal(
                                "The clock is behind the newest forecast on the chain. Refusing to write a backdated row."
                            )
                        row = ForecastRow(prev_hash=prev, row_hash=row_hash(prev, payload), **payload)
                        session.add(row)
                        session.flush()
                        return _forecast_record(row)
                except IntegrityError:
                    continue
        raise ScoreboardError("The forecast chain kept changing under this write. Nothing was stored.")

    def _forecast_payload(
        self,
        session: Session,
        forecast: Forecast,
        site: str,
        key: str,
        as_of: date,
        point: int,
        low: int,
        high: int,
        reading_ids: list[str],
        forecast_id: str,
        code_commit: str | None,
    ) -> dict[str, Any]:
        site_row = session.get(SiteRow, site)
        if site_row is None:
            raise ScoreboardRefusal(f"The site {forecast.site!r} is not registered. Register it before forecasting.")
        search_row = session.scalar(select(SearchRow).where(SearchRow.site == site, SearchRow.search_key == key))
        if search_row is None:
            raise ScoreboardRefusal(f"The search {forecast.search!r} is not registered for {site}.")
        created = self._now()
        model = session.get(ModelVersionRow, forecast.model_version)
        if model is None:
            raise ScoreboardRefusal(f"Model version {forecast.model_version} is not frozen. Register it first.")
        if parse_utc(model.frozen_at) > created:
            raise ScoreboardRefusal(f"Model version {forecast.model_version} was frozen after this forecast is made.")
        if as_of > created.date():
            raise ScoreboardRefusal(f"The forecast date {as_of} is later than the day it is made, {created.date()}.")
        launch = date.fromisoformat(site_row.launch_date)
        if forecast.stage == "upload" and as_of > launch:
            raise ScoreboardRefusal(f"An upload forecast must be made on or before launch ({launch}). Its date is {as_of}.")
        for reading_id in reading_ids:
            reading = session.get(ReadingRow, reading_id)
            if reading is None:
                raise ScoreboardRefusal(f"Input reading {reading_id} is not stored, so its date cannot be checked.")
            taken = parse_utc(reading.taken_at)
            if taken.date() > as_of:
                raise ScoreboardRefusal(
                    f"Input reading {reading_id} was taken on {taken.date()}, after the forecast date {as_of}."
                )
            if taken > created:
                raise ScoreboardRefusal(f"Input reading {reading_id} was taken after this forecast is made.")
        duplicate = session.scalar(
            select(ForecastRow.forecast_id).where(
                ForecastRow.site == site,
                ForecastRow.search_key == key,
                ForecastRow.stage == forecast.stage,
                ForecastRow.model_version == forecast.model_version,
            )
        )
        if duplicate is not None:
            raise ScoreboardRefusal(
                f"A {forecast.stage} forecast for {site} and {forecast.search!r} from model {forecast.model_version} "
                "already exists. A forecast is never replaced."
            )
        due = launch + timedelta(days=OUTCOME_DAY)
        payload = {
            "forecast_id": forecast_id,
            "source": site_row.source,
            "site": site,
            "search": search_row.search,
            "search_key": key,
            "search_group": search_row.search_group,
            "is_brand": bool(search_row.is_brand),
            "difficulty": search_row.difficulty,
            "niche": site_row.niche,
            "built_by": site_row.built_by,
            "stage": forecast.stage,
            "as_of": as_of.isoformat(),
            "point": point,
            "range_low": low,
            "range_high": high,
            "label": forecast.label,
            "model_version": forecast.model_version,
            "probs": forecast.probs,
            "target_url": forecast.target_url or search_row.target_url,
            "notes": dict(forecast.notes or {}),
            "kind": forecast.kind,
            "label_text": PREDICTED,
            "created_at_utc": utc_iso(created),
            "launch_date": launch.isoformat(),
            "outcome_due_date": due.isoformat(),
            "lead_days": (due - as_of).days,
            "grading_rule_version": self.rule.version,
            "grading_rule_hash": self.rule.rule_hash(),
            "code_commit": code_commit if code_commit is not None else self.code_commit,
            "input_reading_ids": reading_ids,
            "location_code": search_row.location_code,
            "device": search_row.device,
        }
        return _json_safe(payload)

    def forecasts(self, *, source: str | None = None, stage: str | None = None) -> list[ForecastRecord]:
        """Stored forecasts in chain order."""
        with self._session() as session:
            query = select(ForecastRow).order_by(ForecastRow.seq)
            if source is not None:
                query = query.where(ForecastRow.source == source)
            if stage is not None:
                query = query.where(ForecastRow.stage == stage)
            return [_forecast_record(row) for row in session.scalars(query).all()]

    def forecast(self, forecast_id: str) -> ForecastRecord:
        with self._session() as session:
            row = session.scalar(select(ForecastRow).where(ForecastRow.forecast_id == forecast_id))
            if row is None:
                raise ScoreboardRefusal(f"Forecast {forecast_id} is not stored.")
            return _forecast_record(row)

    # -- grading -------------------------------------------------------------------------------

    def window_readings(self, forecast_id: str, *, taken_on_or_before: date | None = None) -> list[WindowReading]:
        """Readings of the forecast's search, location and device taken on days 87 to 93 after launch."""
        with self._session() as session:
            row = session.scalar(select(ForecastRow).where(ForecastRow.forecast_id == forecast_id))
            if row is None:
                raise ScoreboardRefusal(f"Forecast {forecast_id} is not stored.")
            launch = date.fromisoformat(row.launch_date)
            created = parse_utc(row.created_at_utc)
            readings = session.scalars(
                select(ReadingRow)
                .where(
                    ReadingRow.search_key == row.search_key,
                    ReadingRow.location_code == row.location_code,
                    ReadingRow.device == row.device,
                )
                .order_by(ReadingRow.taken_at, ReadingRow.reading_id)
            ).all()
            out: list[WindowReading] = []
            for reading in readings:
                taken = parse_utc(reading.taken_at)
                day = (taken.date() - launch).days
                if not WINDOW_FIRST_DAY <= day <= WINDOW_LAST_DAY:
                    continue
                if taken_on_or_before is not None and taken.date() > taken_on_or_before:
                    continue
                out.append(
                    WindowReading(
                        reading_id=reading.reading_id,
                        taken_at=reading.taken_at,
                        day_after_launch=day,
                        degraded=bool(reading.degraded),
                        label=reading.label,
                        before_forecast=taken <= created,
                    )
                )
            return out

    def grades_for(self, forecast_id: str) -> list[GradeRecord]:
        with self._session() as session:
            rows = session.scalars(select(GradeRow).where(GradeRow.forecast_id == forecast_id).order_by(GradeRow.seq)).all()
            return [_grade_record(row) for row in rows]

    def grade(
        self,
        forecast_id: str,
        reading_id: str,
        *,
        kind: str = "original",
        reason: str | None = None,
        rule: GradingRule | None = None,
    ) -> GradeRecord:
        """Grade one forecast against one clean reading. Appends a REAL grade row or refuses."""
        if kind not in GRADE_KINDS:
            raise ScoreboardRefusal(f"kind must be original or regrade. Got {kind!r}.")
        chosen = rule or self.rule
        for _attempt in range(APPEND_RETRIES):
            with self._lock, self._session() as session:
                try:
                    with session.begin():
                        payload = self._grade_payload(session, forecast_id, reading_id, kind, reason, chosen)
                        head = session.scalars(select(GradeRow).order_by(GradeRow.seq.desc()).limit(1)).first()
                        prev = head.row_hash if head is not None else GENESIS
                        row = GradeRow(prev_hash=prev, row_hash=row_hash(prev, payload), **payload)
                        session.add(row)
                        session.flush()
                        return _grade_record(row)
                except IntegrityError:
                    continue
        raise ScoreboardError("The grade chain kept changing under this write. Nothing was stored.")

    def _grade_payload(
        self,
        session: Session,
        forecast_id: str,
        reading_id: str,
        kind: str,
        reason: str | None,
        rule: GradingRule,
    ) -> dict[str, Any]:
        forecast = session.scalar(select(ForecastRow).where(ForecastRow.forecast_id == forecast_id))
        if forecast is None:
            raise ScoreboardRefusal(f"Forecast {forecast_id} is not stored.")
        if row_hash(forecast.prev_hash, _hash_payload(forecast)) != forecast.row_hash:
            raise ChainBroken(f"Forecast {forecast_id} does not match its own hash. It will not be graded.")
        reading = session.get(ReadingRow, reading_id)
        if reading is None:
            raise ScoreboardRefusal(f"Reading {reading_id} is not stored.")
        if reading.degraded:
            raise ScoreboardRefusal(
                f"Reading {reading_id} is degraded (fewer than 3 kept pulls). A degraded reading is never an outcome."
            )
        if reading.label != REAL and forecast.source != "synthetic":
            raise ScoreboardRefusal(f"Reading {reading_id} is {reading.label}. Only a REAL reading grades a {forecast.source} forecast.")
        if reading.search_key != forecast.search_key:
            raise ScoreboardRefusal(f"Reading {reading_id} is for the search {reading.search!r}, not {forecast.search!r}.")
        if reading.location_code != forecast.location_code or reading.device != forecast.device:
            raise ScoreboardRefusal(
                f"Reading {reading_id} was read at location {reading.location_code} on {reading.device}. "
                f"The forecast is for location {forecast.location_code} on {forecast.device}."
            )
        taken = parse_utc(reading.taken_at)
        launch = date.fromisoformat(forecast.launch_date)
        day = (taken.date() - launch).days
        if not WINDOW_FIRST_DAY <= day <= WINDOW_LAST_DAY:
            raise ScoreboardRefusal(
                f"Reading {reading_id} was taken on day {day} after launch. The outcome window is day "
                f"{WINDOW_FIRST_DAY} to day {WINDOW_LAST_DAY}."
            )
        if parse_utc(forecast.created_at_utc) >= taken:
            raise ScoreboardRefusal(
                f"Forecast {forecast_id} was made at or after reading {reading_id} was taken. It cannot be graded on it."
            )
        existing = session.scalars(select(GradeRow).where(GradeRow.forecast_id == forecast_id)).all()
        if kind == "original":
            if any(row.kind == "original" for row in existing):
                raise ScoreboardRefusal(f"Forecast {forecast_id} already has its original grade. Use a regrade with a reason.")
            if rule.rule_hash() != forecast.grading_rule_hash:
                raise ScoreboardRefusal("The original grade must use the grading rule frozen on the forecast.")
        else:
            if not reason or not str(reason).strip():
                raise ScoreboardRefusal("A regrade needs a reason.")
            if not any(row.kind == "original" for row in existing):
                raise ScoreboardRefusal(f"Forecast {forecast_id} has no original grade to sit beside a regrade.")
            if any(
                row.kind == "regrade" and row.reading_id == reading_id and row.grading_rule_hash == rule.rule_hash()
                for row in existing
            ):
                raise ScoreboardRefusal("This regrade (same reading, same rule) is already stored.")
        position_row = session.scalar(
            select(ReadingPositionRow).where(
                ReadingPositionRow.reading_id == reading_id, ReadingPositionRow.site == forecast.site
            )
        )
        outcome = position_row.position if position_row is not None else NT50
        target_position = (
            self._target_url_position(session, reading, forecast.target_url) if forecast.target_url else None
        )
        grade_id = "gr_" + sha256_text(
            canonical_json([forecast_id, reading_id, kind, rule.rule_hash(), len(existing)])
        )[:40]
        payload = {
            "grade_id": grade_id,
            "forecast_id": forecast_id,
            "forecast_row_hash": forecast.row_hash,
            "reading_id": reading_id,
            "reading_taken_at": reading.taken_at,
            "days_after_launch": day,
            "site": forecast.site,
            "search": forecast.search,
            "outcome_position": outcome,
            "hit": rule.is_hit(forecast.point, outcome),
            "target_url": forecast.target_url,
            "target_url_position": target_position,
            "target_url_hit": rule.is_hit(forecast.point, target_position) if target_position is not None else None,
            "kind": kind,
            "reason": str(reason).strip() if reason else None,
            "grading_rule_version": rule.version,
            "grading_rule_hash": rule.rule_hash(),
            "graded_at_utc": utc_iso(self._now()),
            "label_text": REAL,
        }
        return _json_safe(payload)

    def _target_url_position(self, session: Session, reading: ReadingRow, target_url: str) -> int | None:
        """Best rank of the exact target URL over the reading's kept pulls. None when the pulls cannot show it."""
        target = _norm_url(target_url)
        if target is None:
            return None
        dropped = {str(item[0]) for item in (reading.dropped or [])}
        kept = [pull_id for pull_id in (reading.pull_ids or []) if pull_id not in dropped]
        if not kept:
            return None
        best: int | None = None
        for pull_id in kept:
            pull = session.get(PullRow, pull_id)
            if pull is None or not pull.ok:
                return None
            for hit in pull.hits or []:
                rank = int(hit.get("rank", 0))
                if 1 <= rank <= TOP_DEPTH and _norm_url(str(hit.get("url", ""))) == target:
                    best = rank if best is None else min(best, rank)
        return best if best is not None else NT50

    # -- core updates and freezes --------------------------------------------------------------

    def record_core_update(
        self,
        name: str,
        *,
        kind: str,
        started_on: date | str,
        source_url: str,
        ended_on: date | str | None = None,
    ) -> None:
        """Record a Google update from the status dashboard, with the time we recorded it."""
        start = _iso_date(started_on)
        end = _iso_date(ended_on) if ended_on is not None else None
        if end is not None and end < start:
            raise ScoreboardRefusal("A core update cannot end before it starts.")
        if not str(source_url).strip():
            raise ScoreboardRefusal("A core update needs the source URL it was read from.")
        with self._lock, self._session() as session, session.begin():
            dup = session.scalar(select(CoreUpdateRow).where(CoreUpdateRow.name == name, CoreUpdateRow.started_on == start))
            if dup is not None:
                raise ScoreboardRefusal(f"The core update {name!r} starting {start} is already recorded.")
            now = utc_iso(self._now())
            session.add(
                CoreUpdateRow(
                    name=name,
                    kind=kind,
                    started_on=start,
                    ended_on=end,
                    source_url=source_url,
                    recorded_at=now,
                    ended_recorded_at=now if end is not None else None,
                )
            )

    def end_core_update(self, name: str, *, started_on: date | str, ended_on: date | str) -> None:
        """Record the end of an update that was recorded while it was still rolling out."""
        start = _iso_date(started_on)
        end = _iso_date(ended_on)
        if end < start:
            raise ScoreboardRefusal("A core update cannot end before it starts.")
        with self._lock, self._session() as session, session.begin():
            row = session.scalar(select(CoreUpdateRow).where(CoreUpdateRow.name == name, CoreUpdateRow.started_on == start))
            if row is None:
                raise ScoreboardRefusal(f"No core update {name!r} starting {start} is recorded.")
            if row.ended_on is not None:
                raise ScoreboardRefusal(f"The core update {name!r} already has an end date.")
            row.ended_on = end
            row.ended_recorded_at = utc_iso(self._now())

    def core_updates(self) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.scalars(select(CoreUpdateRow).order_by(CoreUpdateRow.started_on, CoreUpdateRow.id)).all()
            return [_row_dict(row) for row in rows]

    def freeze(self, name: str, payload: Any) -> str:
        """Freeze a payload under a name. The same payload again is a no-op. A different one is refused."""
        safe = _json_safe(payload)
        digest = sha256_text(canonical_json(safe))
        with self._lock, self._session() as session, session.begin():
            existing = session.get(FreezeRow, name)
            if existing is not None:
                if existing.sha256 == digest:
                    return digest
                raise ScoreboardRefusal(f"{name} is already frozen with a different payload. A change is a new name.")
            session.add(FreezeRow(name=name, sha256=digest, payload=safe, frozen_at=utc_iso(self._now())))
        return digest

    def freezes(self) -> list[dict[str, Any]]:
        with self._session() as session:
            return [_row_dict(row) for row in session.scalars(select(FreezeRow).order_by(FreezeRow.name)).all()]

    # -- verification --------------------------------------------------------------------------

    def verify(self) -> ChainStatus:
        """Recompute both chains from the stored columns, check grade links, and check the triggers."""
        problems: list[str] = []
        with self._session() as session:
            forecasts = session.scalars(select(ForecastRow).order_by(ForecastRow.seq)).all()
            grades = session.scalars(select(GradeRow).order_by(GradeRow.seq)).all()
            f_ok, f_head, f_count = verify_chain((r.prev_hash, r.row_hash, _hash_payload(r)) for r in forecasts)
            g_ok, g_head, g_count = verify_chain((r.prev_hash, r.row_hash, _hash_payload(r)) for r in grades)
            if not f_ok:
                problems.append(f"The forecast chain breaks after {f_count} of {len(forecasts)} rows.")
            if not g_ok:
                problems.append(f"The grade chain breaks after {g_count} of {len(grades)} rows.")
            by_id = {r.forecast_id: r.row_hash for r in forecasts}
            links_ok = True
            for grade in grades:
                if by_id.get(grade.forecast_id) != grade.forecast_row_hash:
                    links_ok = False
                    problems.append(f"Grade {grade.grade_id} points at a forecast row that is missing or changed.")
                    break
        present = set(self.triggers_present())
        missing = sorted(set(TRIGGERS) - present)
        if missing:
            problems.append("Append-only triggers are missing: " + ", ".join(missing) + ".")
        triggers_ok = not missing
        return ChainStatus(
            ok=f_ok and g_ok and links_ok and triggers_ok,
            forecasts_ok=f_ok,
            grades_ok=g_ok,
            links_ok=links_ok,
            triggers_ok=triggers_ok,
            forecasts_head=f_head,
            grades_head=g_head,
            forecasts_checked=f_count,
            grades_checked=g_count,
            forecasts_total=len(forecasts),
            grades_total=len(grades),
            problems=tuple(problems),
        )

    def require_verified(self) -> ChainStatus:
        status = self.verify()
        if not status.ok:
            raise ChainBroken("The scoreboard does not verify, so nothing is reported. " + " ".join(status.problems))
        return status

    # -- queries for the reports ---------------------------------------------------------------

    def graded_records(
        self,
        source: str,
        stage: str,
        as_of: date | None = None,
        kind: str = "original",
        *,
        model_version: str | None = None,
    ) -> list[GradedRecord]:
        """Graded forecasts of ONE source and ONE stage, sorted by site, search and forecast id.

        kind "original" gives each forecast's original grade. "regrade" gives the latest
        regrade of forecasts that have one. "latest" gives the latest regrade where one exists
        and the original otherwise. With as_of, only outcomes due and read on or before it count.
        """
        if source not in SOURCES:
            raise PoolingRefused(f"Pick one source of {', '.join(SOURCES)}. Sources are never pooled.")
        if stage not in STAGE_DAYS:
            raise ScoreboardRefusal(f"The stage must be one of {', '.join(STAGE_DAYS)}.")
        if kind not in RECORD_KINDS:
            raise ScoreboardRefusal(f"kind must be one of {', '.join(RECORD_KINDS)}.")
        with self._session() as session:
            query = select(ForecastRow).where(ForecastRow.source == source, ForecastRow.stage == stage)
            if model_version is not None:
                query = query.where(ForecastRow.model_version == model_version)
            forecasts = session.scalars(query.order_by(ForecastRow.seq)).all()
            versions = sorted({row.model_version for row in forecasts})
            if model_version is None and len(versions) > 1:
                raise ScoreboardRefusal(
                    f"{source} {stage} holds forecasts from {len(versions)} model versions ({', '.join(versions)}). "
                    "Name one. Versions are reported apart."
                )
            ids = [row.forecast_id for row in forecasts]
            grades_by: dict[str, list[GradeRow]] = {}
            if ids:
                for grade in session.scalars(
                    select(GradeRow).where(GradeRow.forecast_id.in_(ids)).order_by(GradeRow.seq)
                ).all():
                    grades_by.setdefault(grade.forecast_id, []).append(grade)
            out: list[GradedRecord] = []
            for row in forecasts:
                if as_of is not None and date.fromisoformat(row.outcome_due_date) > as_of:
                    continue
                candidates = [
                    g
                    for g in grades_by.get(row.forecast_id, [])
                    if as_of is None or parse_utc(g.reading_taken_at).date() <= as_of
                ]
                chosen = _choose_grade(candidates, kind)
                if chosen is None:
                    continue
                outcome_date = parse_utc(chosen.reading_taken_at).date()
                notes = row.notes or {}
                split = notes.get("split") if isinstance(notes, dict) else None
                cell = GradedCell(
                    site=row.site,
                    search=row.search,
                    point=row.point,
                    actual=chosen.outcome_position,
                    is_brand=bool(row.is_brand),
                    difficulty=row.difficulty,
                    label=row.label,
                    source=row.source,
                    stage=row.stage,
                    lead_days=row.lead_days,
                    range_low=row.range_low,
                    range_high=row.range_high,
                    niche=row.niche,
                )
                out.append(
                    GradedRecord(
                        forecast_id=row.forecast_id,
                        grade_id=chosen.grade_id,
                        kind=chosen.kind,
                        reason=chosen.reason,
                        cell=cell,
                        as_of=date.fromisoformat(row.as_of),
                        outcome_date=outcome_date,
                        reading_id=chosen.reading_id,
                        hit=bool(chosen.hit),
                        target_url=chosen.target_url,
                        target_url_position=chosen.target_url_position,
                        target_url_hit=chosen.target_url_hit,
                        grading_rule_version=chosen.grading_rule_version,
                        model_version=row.model_version,
                        built_by=row.built_by,
                        split=str(split) if split is not None else None,
                    )
                )
        out.sort(key=lambda item: (item.cell.site, search_key(item.cell.search), item.forecast_id))
        return out

    def graded_cells(
        self,
        source: str,
        stage: str,
        as_of: date | None = None,
        kind: str = "original",
        *,
        model_version: str | None = None,
    ) -> list[GradedCell]:
        """GradedCell list for grading.headline: one source, one stage, never pooled."""
        return [record.cell for record in self.graded_records(source, stage, as_of, kind, model_version=model_version)]

    def persistence_points(self, items: Sequence[str | GradedRecord | GradedCell]) -> list[int]:
        """The persistence baseline per forecast: the site's clean position in the latest non-degraded
        reading of the same search, location and device taken on or before the forecast's as_of. NT50 when none.

        Only REAL readings count for a forecast whose source is not synthetic, and
        only SYNTHETIC readings for a synthetic forecast, as the grading job does
        for outcomes."""
        with self._session() as session:
            out: list[int] = []
            for item in items:
                row = self._forecast_for_item(session, item)
                as_of = date.fromisoformat(row.as_of)
                wanted = SYNTHETIC if row.source == "synthetic" else REAL
                readings = session.scalars(
                    select(ReadingRow)
                    .where(
                        ReadingRow.search_key == row.search_key,
                        ReadingRow.location_code == row.location_code,
                        ReadingRow.device == row.device,
                        ReadingRow.degraded.is_(False),
                        ReadingRow.label == wanted,
                    )
                    .order_by(ReadingRow.taken_at.desc(), ReadingRow.reading_id.desc())
                ).all()
                chosen = next((r for r in readings if parse_utc(r.taken_at).date() <= as_of), None)
                if chosen is None:
                    out.append(NT50)
                    continue
                position = session.scalar(
                    select(ReadingPositionRow.position).where(
                        ReadingPositionRow.reading_id == chosen.reading_id, ReadingPositionRow.site == row.site
                    )
                )
                out.append(int(position) if position is not None else NT50)
            return out

    @staticmethod
    def _forecast_for_item(session: Session, item: str | GradedRecord | GradedCell) -> ForecastRow:
        if isinstance(item, GradedRecord):
            item = item.forecast_id
        if isinstance(item, str):
            row = session.scalar(select(ForecastRow).where(ForecastRow.forecast_id == item))
            if row is None:
                raise ScoreboardRefusal(f"Forecast {item} is not stored.")
            return row
        if isinstance(item, GradedCell):
            query = select(ForecastRow).where(
                ForecastRow.site == item.site, ForecastRow.search_key == search_key(item.search)
            )
            if item.stage is not None:
                query = query.where(ForecastRow.stage == item.stage)
            if item.source is not None:
                query = query.where(ForecastRow.source == item.source)
            rows = session.scalars(query).all()
            if len(rows) != 1:
                raise ScoreboardRefusal(
                    f"The cell for {item.site} and {item.search!r} matches {len(rows)} forecasts. Pass forecast ids instead."
                )
            return rows[0]
        raise TypeError("persistence_points takes forecast ids, GradedRecord or GradedCell items")

    def eligibility(self, as_of: date) -> dict[str, dict[str, dict[str, Any]]]:
        """Counts by source and stage: made, due, graded, due but not graded, not yet due, next due date."""
        table: dict[str, dict[str, dict[str, Any]]] = {
            source: {
                stage: {"made": 0, "due": 0, "graded": 0, "due_not_graded": 0, "not_yet_due": 0, "next_due_date": None}
                for stage in STAGE_DAYS
            }
            for source in SOURCES
        }
        with self._session() as session:
            originals: dict[str, str] = {}
            for grade in session.scalars(select(GradeRow).where(GradeRow.kind == "original")).all():
                originals[grade.forecast_id] = grade.reading_taken_at
            for row in session.scalars(select(ForecastRow).order_by(ForecastRow.seq)).all():
                if parse_utc(row.created_at_utc).date() > as_of:
                    continue
                entry = table.setdefault(row.source, {}).setdefault(
                    row.stage,
                    {"made": 0, "due": 0, "graded": 0, "due_not_graded": 0, "not_yet_due": 0, "next_due_date": None},
                )
                entry["made"] += 1
                due = date.fromisoformat(row.outcome_due_date)
                if due <= as_of:
                    entry["due"] += 1
                    taken = originals.get(row.forecast_id)
                    if taken is not None and parse_utc(taken).date() <= as_of:
                        entry["graded"] += 1
                    else:
                        entry["due_not_graded"] += 1
                else:
                    entry["not_yet_due"] += 1
                    if entry["next_due_date"] is None or due.isoformat() < entry["next_due_date"]:
                        entry["next_due_date"] = due.isoformat()
        return table

    # -- export and import ---------------------------------------------------------------------

    def export(self, directory: str | Path) -> dict[str, Any]:
        """Write the scoreboard as sorted JSONL plus HEADS.json. The same database always gives the same bytes."""
        status = self.require_verified()
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        with self._session() as session:
            tables: dict[str, list[dict[str, Any]]] = {
                "sites.jsonl": [_row_dict(r) for r in session.scalars(select(SiteRow).order_by(SiteRow.site)).all()],
                "searches.jsonl": [
                    _row_dict(r) for r in session.scalars(select(SearchRow).order_by(SearchRow.site, SearchRow.search_key)).all()
                ],
                "model_versions.jsonl": [
                    _row_dict(r) for r in session.scalars(select(ModelVersionRow).order_by(ModelVersionRow.version)).all()
                ],
                "pulls.jsonl": [_row_dict(r) for r in session.scalars(select(PullRow).order_by(PullRow.pull_id)).all()],
                "readings.jsonl": [],
                "forecasts.jsonl": [
                    _row_dict(r) for r in session.scalars(select(ForecastRow).order_by(ForecastRow.seq)).all()
                ],
                "grades.jsonl": [_row_dict(r) for r in session.scalars(select(GradeRow).order_by(GradeRow.seq)).all()],
                "core_updates.jsonl": [
                    _row_dict(r) for r in session.scalars(select(CoreUpdateRow).order_by(CoreUpdateRow.id)).all()
                ],
                "freezes.jsonl": [_row_dict(r) for r in session.scalars(select(FreezeRow).order_by(FreezeRow.name)).all()],
            }
            for reading in session.scalars(select(ReadingRow).order_by(ReadingRow.reading_id)).all():
                entry = _row_dict(reading)
                entry["positions"] = [
                    {"site": p.site, "position": p.position, "pull_positions": list(p.pull_positions), "best_url": p.best_url}
                    for p in self._positions(session, reading.reading_id)
                ]
                tables["readings.jsonl"].append(entry)
        files: dict[str, str] = {}
        for name in EXPORT_FILES:
            body = "".join(canonical_json(row) + "\n" for row in tables[name])
            (folder / name).write_text(body, encoding="utf-8")
            files[name] = sha256_text(body)
        heads = {
            "forecasts": {"count": status.forecasts_total, "head": status.forecasts_head},
            "grades": {"count": status.grades_total, "head": status.grades_head},
            "grading_rule": {"version": self.rule.version, "hash": self.rule.rule_hash()},
            "files": files,
        }
        (folder / "HEADS.json").write_text(json.dumps(heads, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return heads

    def import_export(self, directory: str | Path) -> ChainStatus:
        """Rebuild an EMPTY scoreboard from an export, then verify the chains and the head hashes."""
        folder = Path(directory)
        heads_path = folder / "HEADS.json"
        if not heads_path.exists():
            raise ScoreboardError(f"{heads_path} is missing.")
        heads = json.loads(heads_path.read_text(encoding="utf-8"))
        rows: dict[str, list[dict[str, Any]]] = {}
        for name in EXPORT_FILES:
            path = folder / name
            if not path.exists():
                raise ScoreboardError(f"{path} is missing.")
            body = path.read_text(encoding="utf-8")
            if sha256_text(body) != heads.get("files", {}).get(name):
                raise ChainBroken(f"{name} does not match the sha256 in HEADS.json.")
            rows[name] = [json.loads(line) for line in body.splitlines() if line.strip()]
        models = (
            (SiteRow, "sites.jsonl"),
            (SearchRow, "searches.jsonl"),
            (ModelVersionRow, "model_versions.jsonl"),
            (PullRow, "pulls.jsonl"),
            (ForecastRow, "forecasts.jsonl"),
            (GradeRow, "grades.jsonl"),
            (CoreUpdateRow, "core_updates.jsonl"),
            (FreezeRow, "freezes.jsonl"),
        )
        with self._lock, self._session() as session:
            try:
                with session.begin():
                    for model in (SiteRow, SearchRow, ModelVersionRow, PullRow, ReadingRow, ForecastRow, GradeRow, CoreUpdateRow, FreezeRow):
                        if session.scalar(select(func.count()).select_from(model)):
                            raise ScoreboardRefusal("import_export needs an empty scoreboard. This one already holds rows.")
                    for model, name in models[:4]:
                        for item in rows[name]:
                            session.add(model(**item))
                        session.flush()
                    for item in rows["readings.jsonl"]:
                        positions = item.pop("positions", [])
                        session.add(ReadingRow(**item))
                        session.flush()
                        for position in positions:
                            session.add(ReadingPositionRow(reading_id=item["reading_id"], **position))
                    session.flush()
                    for model, name in models[4:]:
                        for item in rows[name]:
                            session.add(model(**item))
                        session.flush()
                    forecasts = session.scalars(select(ForecastRow).order_by(ForecastRow.seq)).all()
                    grades = session.scalars(select(GradeRow).order_by(GradeRow.seq)).all()
                    f_ok, f_head, _ = verify_chain((r.prev_hash, r.row_hash, _hash_payload(r)) for r in forecasts)
                    g_ok, g_head, _ = verify_chain((r.prev_hash, r.row_hash, _hash_payload(r)) for r in grades)
                    expected = (
                        heads["forecasts"]["head"],
                        heads["forecasts"]["count"],
                        heads["grades"]["head"],
                        heads["grades"]["count"],
                    )
                    if not (f_ok and g_ok) or (f_head, len(forecasts), g_head, len(grades)) != expected:
                        raise ChainBroken("The imported chains do not verify against HEADS.json. Nothing was imported.")
            except (TypeError, IntegrityError) as error:
                raise ScoreboardError(f"The export could not be loaded: {error}") from error
        return self.require_verified()


def _choose_grade(grades: list[GradeRow], kind: str) -> GradeRow | None:
    originals = [g for g in grades if g.kind == "original"]
    regrades = [g for g in grades if g.kind == "regrade"]
    if kind == "original":
        return originals[0] if originals else None
    if kind == "regrade":
        return regrades[-1] if regrades else None
    if regrades:
        return regrades[-1]
    return originals[0] if originals else None


def _pull_from_row(row: PullRow) -> Pull:
    return Pull(
        pull_id=row.pull_id,
        provider=row.provider,
        endpoint=row.endpoint,
        spec=PullSpec(
            search=row.search,
            location_code=row.location_code,
            language_code=row.language_code,
            device=row.device,
            depth=row.depth,
        ),
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        ok=bool(row.ok),
        status_code=row.status_code,
        status_message=row.status_message,
        task_id=row.task_id,
        provider_datetime=row.provider_datetime,
        check_url=row.check_url,
        cost=row.cost,
        partial=bool(row.partial),
        spell_corrected=bool(row.spell_corrected),
        repaired=bool(row.repaired),
        hits=tuple(OrganicHit(**hit) for hit in (row.hits or [])),
        raw_path=row.raw_path,
        raw_sha256=row.raw_sha256,
    )


def detect_code_commit(repo: str | Path = ".") -> str | None:
    """The commit checked out in `repo`, read from .git without running git. None when it cannot be read."""
    git = Path(repo) / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            path = git / ref
            if path.exists():
                return path.read_text(encoding="utf-8").strip() or None
            packed = git / "packed-refs"
            if packed.exists():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
            return None
        return head or None
    except OSError:
        return None


def stage_due_date(launch: date, stage: str) -> date:
    """The day a stage's forecast is issued: launch plus the stage's day count."""
    if stage not in STAGE_DAYS:
        raise ScoreboardRefusal(f"The stage must be one of {', '.join(STAGE_DAYS)}.")
    return launch + timedelta(days=STAGE_DAYS[stage])


def mapping_points(points: Mapping[str, int], records: Sequence[GradedRecord], name: str) -> list[int]:
    """Baseline points aligned with records from a forecast id -> point mapping. Every record needs one."""
    out: list[int] = []
    for record in records:
        if record.forecast_id not in points:
            raise ScoreboardRefusal(f"Baseline {name} has no point for forecast {record.forecast_id}.")
        out.append(_check_position(points[record.forecast_id], f"{name} point"))
    return out
