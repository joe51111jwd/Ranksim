"""Records for keyword volume. A missing number stays blank."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm_keyword(keyword: str) -> str:
    return " ".join(str(keyword or "").strip().lower().split())


def display_volume(volume: int) -> str:
    return f"{int(volume):,}"


def parse_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or value != int(value):
            return None
        return int(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def parse_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number >= 0 else None
    text = str(value).strip().replace(",", "").replace("$", "").replace(" ", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def slugify_keyword(keyword: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", norm_keyword(keyword)).strip("-")
    return slug


@dataclass
class Attempt:
    provider: str
    outcome: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VolumeRecord:
    keyword: str
    us_monthly_volume: int | None = None
    volume_display: str = ""
    provider: str = ""
    volume_source: str = ""
    source_url: str = ""
    data_date: str = ""
    fetched_at: str = ""
    cpc_usd: float | None = None
    competition: str = ""
    competition_index: int | None = None
    semrush_us_volume: int | None = None
    semrush_url: str = ""
    semrush_data_date: str = ""
    notes: str = ""
    blank_reason: str = ""
    list_sources: str = ""
    sticky: bool = False
    attempts: list[Attempt] = field(default_factory=list)

    def usable(self) -> bool:
        """A number is usable only with a provider, a source URL, and a data date."""
        return (
            self.us_monthly_volume is not None
            and bool(self.provider.strip())
            and bool(self.volume_source.strip())
            and bool(self.source_url.strip())
            and bool(self.data_date.strip())
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["attempts"] = [item.to_dict() if isinstance(item, Attempt) else item for item in self.attempts]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> VolumeRecord:
        attempts = [Attempt(**item) if isinstance(item, dict) else item for item in data.get("attempts") or []]
        known = {name: data.get(name) for name in (
            "keyword", "us_monthly_volume", "volume_display", "provider", "volume_source", "source_url",
            "data_date", "fetched_at", "cpc_usd", "competition", "competition_index", "semrush_us_volume",
            "semrush_url", "semrush_data_date", "notes", "blank_reason", "list_sources", "sticky",
        )}
        record = cls(**known)
        record.attempts = attempts
        return record


def blank_record(keyword: str, attempts: list[Attempt], *, notes: str = "", list_sources: str = "") -> VolumeRecord:
    reason = "; ".join(f"{item.provider}: {item.detail}" for item in attempts if item.outcome != "hit")
    if not reason:
        reason = "no source returned a monthly volume"
    return VolumeRecord(
        keyword=keyword,
        fetched_at=now_iso(),
        notes=notes,
        blank_reason=reason,
        list_sources=list_sources,
        attempts=attempts,
    )


def provider_id(volume_source: str) -> str:
    text = volume_source.lower()
    if "seodata" in text:
        return "seodata.dev"
    if "semrush" in text:
        return "semrush"
    if "dataforseo" in text:
        return "dataforseo"
    if "google ads" in text or "keyword planner" in text:
        return "google_ads"
    return volume_source.strip()
