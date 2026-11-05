"""Ingest real Google positions. This module does not fetch or invent them."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path


ORGANIC_TYPES = {"organic", "organic_result", "web", ""}


@dataclass(frozen=True)
class SerpLabel:
    query: str
    url: str
    position: float
    prior_position: float | None
    source: str
    # Collection metadata, when the file has it. None means the file did not say.
    observed_on: str | None = None
    prior_observed_on: str | None = None
    country: str | None = None
    language: str | None = None
    device: str | None = None
    provider: str | None = None
    # "serp_snapshot": an organic position from one results page.
    # "search_console_avg": Search Console's average position over a period; not a snapshot,
    # and it only covers your own pages.
    kind: str = "serp_snapshot"


@dataclass
class ImportReport:
    """What the loader kept and dropped, so a bad file is never silently half-used."""

    path: str = ""
    kept: int = 0
    kind: str = "serp_snapshot"
    dropped: dict[str, list[int]] = field(default_factory=dict)
    duplicates: list[tuple[str, str, float, float]] = field(default_factory=list)
    tied_positions: int = 0
    metadata_columns: list[str] = field(default_factory=list)

    def drop(self, reason: str, row_number: int) -> None:
        self.dropped.setdefault(reason, []).append(row_number)

    def lines(self) -> list[str]:
        out = [f"Labels: kept {self.kept} rows from {self.path} ({self.kind})."]
        for reason, rows in sorted(self.dropped.items()):
            shown = ", ".join(str(row) for row in rows[:8]) + (" ..." if len(rows) > 8 else "")
            out.append(f"  dropped {len(rows)} rows: {reason} (CSV rows {shown})")
        if self.duplicates:
            out.append(
                f"  {len(self.duplicates)} duplicate query+URL rows: kept the best position for each "
                f"(e.g. {self.duplicates[0][0]!r} {self.duplicates[0][1]} at {self.duplicates[0][2]:g} and {self.duplicates[0][3]:g})"
            )
        if self.tied_positions:
            out.append(f"  {self.tied_positions} queries have two URLs at the same position; those pairs carry no order")
        if self.kind == "search_console_avg":
            out.append(
                "  Search Console average positions are averages over a period for your own pages, not one "
                "results page. Competitor pairwise accuracy from them is not a Google ranking measurement."
            )
        if not self.metadata_columns:
            out.append("  No collection date, country, language, device or provider columns: results cannot be dated.")
        return out


QUERY_COLUMNS = ("query", "top queries", "queries", "keyword")
URL_COLUMNS = ("url", "page", "landing page", "top pages")
POSITION_COLUMNS = ("position", "average position", "rank", "organic position")
META_COLUMNS = {
    "observed_on": ("observed_on", "collected_at", "date", "checked_on"),
    "prior_observed_on": ("prior_observed_on", "prior_date"),
    "country": ("country", "gl", "location"),
    "language": ("language", "hl"),
    "device": ("device",),
    "provider": ("provider", "collected_with"),
    "result_type": ("result_type", "type"),
}


def load_serp_file(path: str | Path, *, report: ImportReport | None = None) -> list[SerpLabel]:
    """Read a labeled results file and validate it row by row.

    Manual columns: query, url, position, optional prior_position, and optional metadata
    (observed_on, prior_observed_on, country, language, device, provider, result_type).
    Search Console columns: Query, Page, Position (or Average Position); marked as averages.

    Rows that cannot be used are dropped and counted in ``report`` with their CSV row numbers:
    blank query or URL, a URL without http(s), a position that is not a finite number >= 1,
    and non-organic rows when a result_type column says so. Queries are whitespace-collapsed
    and case-folded. A repeated query+URL keeps its best position and is reported.
    """
    from aeo.crawl.urls import normalize_url

    report = report if report is not None else ImportReport()
    file_path = Path(path)
    report.path = str(file_path)
    with file_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{file_path} has no header row")
        fields = {name.strip().lower(): name for name in reader.fieldnames if name}
        query_key = _pick(fields, QUERY_COLUMNS)
        url_key = _pick(fields, URL_COLUMNS)
        position_key = _pick(fields, POSITION_COLUMNS)
        prior_key = fields.get("prior_position")
        if not query_key or not url_key or not position_key:
            found = ", ".join(repr(name) for name in reader.fieldnames)
            raise ValueError(
                f"{file_path}: CSV needs query, url, and position columns (comma separated). Found: {found}. "
                "Search Console exports need Query, Page, and Position."
            )
        meta_keys = {name: _pick(fields, aliases) for name, aliases in META_COLUMNS.items()}
        report.metadata_columns = sorted(name for name, key in meta_keys.items() if key and name != "result_type")
        gsc_markers = {"clicks", "impressions", "ctr"}
        is_gsc = (
            position_key == fields.get("average position")
            or bool(gsc_markers & set(fields))
            or (fields.get("page") == url_key and query_key in (fields.get("query"), fields.get("top queries")) and not prior_key)
        )
        kind = "search_console_avg" if is_gsc and not prior_key else "serp_snapshot"
        report.kind = kind
        source = "search_console" if kind == "search_console_avg" else "manual_csv"
        best: dict[tuple[str, str], SerpLabel] = {}
        order: list[tuple[str, str]] = []
        for row_number, raw in enumerate(reader, start=2):
            query = " ".join((raw.get(query_key) or "").split()).casefold()
            url = (raw.get(url_key) or "").strip()
            if not query and not url and not (raw.get(position_key) or "").strip():
                continue
            if not query or not url:
                report.drop("blank query or URL", row_number)
                continue
            try:
                key_url = normalize_url(url)
            except ValueError:
                report.drop("URL is not an absolute http(s) URL", row_number)
                continue
            result_type = _meta(raw, meta_keys["result_type"])
            if result_type is not None and result_type.casefold() not in ORGANIC_TYPES:
                report.drop(f"not an organic result ({result_type})", row_number)
                continue
            position = _float(raw.get(position_key))
            if position is None or not math.isfinite(position) or position < 1:
                report.drop("position is not a finite number >= 1", row_number)
                continue
            prior = _float(raw.get(prior_key)) if prior_key else None
            if prior is not None and (not math.isfinite(prior) or prior < 1):
                prior = None
            label = SerpLabel(
                query=query,
                url=url,
                position=position,
                prior_position=prior,
                source=source,
                observed_on=_meta(raw, meta_keys["observed_on"]),
                prior_observed_on=_meta(raw, meta_keys["prior_observed_on"]),
                country=_meta(raw, meta_keys["country"]),
                language=_meta(raw, meta_keys["language"]),
                device=_meta(raw, meta_keys["device"]),
                provider=_meta(raw, meta_keys["provider"]),
                kind=kind,
            )
            key = (query, key_url)
            if key in best:
                kept = best[key]
                report.duplicates.append((query, url, kept.position, position))
                if position < kept.position:
                    best[key] = label
                continue
            best[key] = label
            order.append(key)
    rows = [best[key] for key in order]
    positions: dict[str, list[float]] = {}
    for row in rows:
        positions.setdefault(row.query, []).append(row.position)
    report.tied_positions = sum(1 for values in positions.values() if len(values) != len(set(values)))
    report.kept = len(rows)
    if not rows:
        raise ValueError(f"{file_path} has headers but no usable rows. " + " ".join(report.lines()[1:]))
    return rows


def navboost_leaks(rows: list[SerpLabel]) -> bool:
    """True when prior_position must be withheld from the model.

    A prior is only safe if it was observed before the label it accompanies. Without dates
    that cannot be checked, and a partial or noisy copy of ``position`` would leak the label
    (a 134-of-135-row copy lifted pairwise accuracy from 0.485 to 0.727 in the audit). So the
    prior is used only when every row that has one also has ``prior_observed_on`` earlier
    than ``observed_on``.
    """
    priors = [row for row in rows if row.prior_position is not None]
    if not priors:
        return False
    for row in priors:
        if not row.observed_on or not row.prior_observed_on:
            return True
        if row.prior_observed_on[:10] >= row.observed_on[:10]:
            return True
    return False


def _meta(raw: dict, key: str | None) -> str | None:
    if not key:
        return None
    value = (raw.get(key) or "").strip()
    return value or None


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
