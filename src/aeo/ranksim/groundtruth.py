"""Ingest real Google positions. This module does not fetch or invent them."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SerpLabel:
    query: str
    url: str
    position: float
    prior_position: float | None
    source: str


def load_serp_file(path: str | Path) -> list[SerpLabel]:
    """Read a manual CSV or a Search Console export.

    Manual columns: query, url, position, and optional prior_position.
    Search Console columns: Query, Page, Position (or Average Position).
    """
    file_path = Path(path)
    with file_path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{file_path} has no header row")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        query_key = _pick(fields, ("query", "top queries", "queries"))
        url_key = _pick(fields, ("url", "page", "landing page"))
        position_key = _pick(fields, ("position", "average position"))
        prior_key = fields.get("prior_position")
        if not query_key or not url_key or not position_key:
            raise ValueError(
                "CSV needs query, url, and position columns. "
                "Search Console exports need Query, Page, and Position."
            )
        source = "search_console" if "page" in fields and "query" in fields else "manual_csv"
        if prior_key:
            source = "manual_csv"
        rows: list[SerpLabel] = []
        for raw in reader:
            query = " ".join((raw.get(query_key) or "").split())
            url = (raw.get(url_key) or "").strip()
            if not query or not url:
                continue
            position = _float(raw.get(position_key))
            if position is None or position <= 0:
                continue
            prior = _float(raw.get(prior_key)) if prior_key else None
            rows.append(SerpLabel(query=query, url=url, position=position, prior_position=prior, source=source))
    if not rows:
        raise ValueError(f"{file_path} has headers but no usable rows")
    return rows


def navboost_leaks(rows: list[SerpLabel]) -> bool:
    """True when the only 'prior' is a copy of the label we are trying to predict."""
    priors = [row for row in rows if row.prior_position is not None]
    if not priors:
        return False
    return all(abs((row.prior_position or 0) - row.position) < 1e-6 for row in priors)


def _pick(fields: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in fields:
            return fields[name]
    return None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
