"""SQLite cache for keyword volumes, Semrush pages, and Serper SERPs."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aeo.ranksim.kwresearch.models import VolumeRecord, norm_keyword

VOLUME_TTL_DAYS = 14
BLANK_TTL_DAYS = 1
SEMRUSH_TTL_DAYS = 7


def _parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class KeywordStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS volume (
                  keyword TEXT PRIMARY KEY,
                  display TEXT,
                  volume INTEGER,
                  provider TEXT,
                  sticky INTEGER NOT NULL DEFAULT 0,
                  fetched_at TEXT,
                  payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS serp (
                  keyword TEXT NOT NULL,
                  day TEXT NOT NULL,
                  fetched_at TEXT,
                  payload TEXT NOT NULL,
                  PRIMARY KEY (keyword, day)
                );
                CREATE TABLE IF NOT EXISTS difficulty (
                  keyword TEXT NOT NULL,
                  day TEXT NOT NULL,
                  score INTEGER,
                  coverage REAL,
                  fetched_at TEXT,
                  payload TEXT NOT NULL,
                  PRIMARY KEY (keyword, day)
                );
                CREATE TABLE IF NOT EXISTS semrush_page (
                  domain TEXT PRIMARY KEY,
                  url TEXT,
                  fetched_at TEXT,
                  status TEXT,
                  data_date TEXT,
                  payload TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    def get_volume(self, keyword: str) -> VolumeRecord | None:
        key = norm_keyword(keyword)
        with self._lock:
            row = self._conn.execute("SELECT payload FROM volume WHERE keyword = ?", (key,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload"])
        record = VolumeRecord.from_dict(data)
        record.keyword = record.keyword or keyword
        return record

    def volume_is_fresh(self, record: VolumeRecord, *, now: datetime | None = None) -> bool:
        if record.sticky and record.usable():
            return True
        if record.sticky and record.us_monthly_volume is None:
            return True
        fetched = _parse_time(record.fetched_at)
        if fetched is None:
            return False
        moment = now or datetime.now(timezone.utc)
        ttl = VOLUME_TTL_DAYS if record.usable() else BLANK_TTL_DAYS
        return moment - fetched <= timedelta(days=ttl)

    def put_volume(self, record: VolumeRecord) -> None:
        key = norm_keyword(record.keyword)
        payload = json.dumps(record.to_dict(), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO volume (keyword, display, volume, provider, sticky, fetched_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                  display = excluded.display,
                  volume = excluded.volume,
                  provider = excluded.provider,
                  sticky = excluded.sticky,
                  fetched_at = excluded.fetched_at,
                  payload = excluded.payload
                """,
                (
                    key,
                    record.keyword,
                    record.us_monthly_volume,
                    record.provider,
                    1 if record.sticky else 0,
                    record.fetched_at,
                    payload,
                ),
            )
            self._conn.commit()

    def get_serp(self, keyword: str, day: str) -> dict | None:
        key = norm_keyword(keyword)
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM serp WHERE keyword = ? AND day = ?",
                (key, day),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return payload if isinstance(payload, dict) else None

    def put_serp(self, keyword: str, day: str, payload: dict, fetched_at: str) -> None:
        key = norm_keyword(keyword)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO serp (keyword, day, fetched_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(keyword, day) DO UPDATE SET
                  fetched_at = excluded.fetched_at,
                  payload = excluded.payload
                """,
                (key, day, fetched_at, json.dumps(payload, ensure_ascii=False)),
            )
            self._conn.commit()

    def latest_difficulty(self, keyword: str) -> dict | None:
        key = norm_keyword(keyword)
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM difficulty WHERE keyword = ? ORDER BY day DESC LIMIT 1",
                (key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return payload if isinstance(payload, dict) else None

    def get_difficulty(self, keyword: str, day: str) -> dict | None:
        key = norm_keyword(keyword)
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM difficulty WHERE keyword = ? AND day = ?",
                (key, day),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return payload if isinstance(payload, dict) else None

    def put_difficulty(self, keyword: str, day: str, payload: dict) -> None:
        key = norm_keyword(keyword)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO difficulty (keyword, day, score, coverage, fetched_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(keyword, day) DO UPDATE SET
                  score = excluded.score,
                  coverage = excluded.coverage,
                  fetched_at = excluded.fetched_at,
                  payload = excluded.payload
                """,
                (
                    key,
                    day,
                    payload.get("score"),
                    payload.get("coverage"),
                    payload.get("fetched_at") or "",
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def get_semrush_page(self, domain: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM semrush_page WHERE domain = ?", (domain,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        return payload if isinstance(payload, dict) else None

    def semrush_page_fresh(self, page: dict, *, now: datetime | None = None) -> bool:
        fetched = _parse_time(str(page.get("fetched_at") or ""))
        if fetched is None:
            return False
        moment = now or datetime.now(timezone.utc)
        return moment - fetched <= timedelta(days=SEMRUSH_TTL_DAYS)

    def put_semrush_page(self, domain: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO semrush_page (domain, url, fetched_at, status, data_date, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                  url = excluded.url,
                  fetched_at = excluded.fetched_at,
                  status = excluded.status,
                  data_date = excluded.data_date,
                  payload = excluded.payload
                """,
                (
                    domain,
                    payload.get("url") or "",
                    payload.get("fetched_at") or "",
                    payload.get("status") or "",
                    payload.get("data_date") or "",
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            self._conn.commit()
