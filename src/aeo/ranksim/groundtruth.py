"""Ingest real Google positions. This module does not fetch or invent them."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SerpLabel:
    query: str
    url: str
    position: float
    prior_position: float | None
    source: str


@dataclass(frozen=True)
class LabelSet:
    """One labeled result file. ``source`` is the file's own provenance, not a guess from the URL host."""

    source: str
    pulled_at: str | None
    rows: list[SerpLabel]


def write_label_set(path: str | Path, source: str, pulled_at: str | None, rows: list[SerpLabel]) -> None:
    """Write label.json. ``source`` is stored as given: synthetic, google_auto, or imported_serp."""
    payload = {
        "source": source,
        "pulled_at": pulled_at,
        "rows": [{"query": row.query, "url": row.url, "position": row.position} for row in rows],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_labels(path: str | Path) -> LabelSet:
    """Read label.json. A missing source or a broken file is an error, not an observed Google set."""
    file_path = Path(path)
    if file_path.is_dir():
        file_path = file_path / "label.json"
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{file_path} could not be read ({exc}). No confidence call was made.") from exc
    if not isinstance(data, dict) or not str(data.get("source") or "").strip():
        raise ValueError(
            f"{file_path} has no source. It is not treated as synthetic or as observed Google. No confidence call was made."
        )
    source = str(data["source"]).strip()
    pulled = data.get("pulled_at")
    pulled_at = str(pulled).strip() if isinstance(pulled, str) and pulled.strip() else None
    rows: list[SerpLabel] = []
    for item in data.get("rows") or []:
        if not isinstance(item, dict):
            continue
        query = " ".join(str(item.get("query") or "").split())
        url = str(item.get("url") or "").strip()
        position = _float(str(item.get("position") if item.get("position") is not None else ""))
        if not query or not url or position is None or position <= 0:
            continue
        rows.append(SerpLabel(query=query, url=url, position=position, prior_position=None, source=source))
    if not rows:
        raise ValueError(f"{file_path} has no usable rows. No confidence call was made.")
    return LabelSet(source=source, pulled_at=pulled_at, rows=rows)


def label_json_beside(path: str | Path) -> Path | None:
    """The label.json the caller named.

    An explicit CSV is a different artifact. A parent or sibling label.json, including
    project/label.json written by a later import, does not replace that CSV's rows.
    """
    candidate = Path(path)
    if candidate.is_dir():
        found = candidate / "label.json"
        return found if found.is_file() else None
    if candidate.name == "label.json":
        return candidate if candidate.is_file() else None
    return None


def synthetic_source(source: str) -> bool:
    """True only for the synthetic demo's own source value."""
    return source == "synthetic"


def live_google_source(source: str) -> bool:
    """True only for a recorded Google pull. An import is not a live pull."""
    return source == "google_auto"


def load_serp_file(path: str | Path) -> list[SerpLabel]:
    """Read a manual CSV or a Search Console export.

    Manual columns: query, url, position, and optional prior_position.
    Search Console columns: Query, Page, Position (or Average Position).
    """
    file_path = Path(path)
    # utf-8-sig: Excel's "CSV UTF-8" starts with a byte order mark that would otherwise hide the first column name.
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
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
