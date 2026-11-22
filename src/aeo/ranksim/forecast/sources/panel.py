"""The result-page panel: one row per organic result per dated Google result page.

What is REAL: every row is a Google organic result that DataForSEO recorded on
the date in `snapshot_time` (the free sample databases now, the paid Historical
SERPs endpoint later; both have the same item layout). Volume, keyword
difficulty, intent, ETV and domain ranks are DataForSEO's published estimates.
What is PREDICTED: nothing. This module stores and reshapes observations.

The pages are COUNTRY level (`location_code` 2840 is the whole United States),
not a pinned city, and each one is a single pull. They are noisier than a clean
reading (docs/plan/PLAN_AUDIT.md B8 and E5).

Layout (docs/plan/IMPLEMENTATION_SPEC.md section 8): parquet under
`data/forecast/cache/panel/dataset=<dataset>/locale=<locale>/part-<source>.parquet`,
with the columns of `PANEL_SCHEMA`. `rank` is the organic rank (`rank_group`).
`site` is `site_history.site_key(main_domain or domain)`. `keyword_id` is the
first 16 hex characters of sha256 of `locale|keyword`. `poison_risk` is True
for a page dated on or after 13 September 2026 that was not collected with the
provider's repair option (Google has served altered pages to scrapers since
then, with no end date yet; spec section 8). Those pages are kept on disk and
left out of training by the helpers here. Parquet written before the rename
carries `poison_window` instead: `load_panel` maps it to `poison_risk` (and
applies the date rule), so no rebuild is needed. A caller that asks for the
old column name gets the same values under that name. Rows for
`config.EXCLUDED_SITES` are dropped when the panel is built and again when it
is loaded.

CLI: python -m aeo.ranksim.forecast.sources.panel build|summary|noise|keywords|whois|dobbs-download
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ...site_history import site_key
from ..asof import utc_iso
from ..config import EXCLUDED_SITES, NT50, TOP_DEPTH, forecast_paths
from . import dfs_samples as dfs


def _excluded_values(values: Iterable[Any]) -> set[str]:
    """The values that name an excluded site once normalised (lower case, site_key), however they are spelled."""
    out: set[str] = set()
    for value in values:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        text = str(value).strip().lower()
        if text and site_key(text) in EXCLUDED_SITES:
            out.add(value)
    return out


def _drop_excluded_table(table: pa.Table) -> pa.Table:
    """Rows whose `site` names an excluded site are dropped. The check normalises each distinct value first."""
    import pyarrow.compute as pc

    if "site" not in table.column_names or not EXCLUDED_SITES or table.num_rows == 0:
        return table
    bad = _excluded_values(pc.unique(table["site"]).to_pylist())
    if not bad:
        return table
    mask = pc.invert(pc.is_in(table["site"], value_set=pa.array(sorted(bad), type=table.schema.field("site").type)))
    return table.filter(pc.fill_null(mask, True))


def excluded_mask(sites: pd.Series) -> pd.Series:
    """True where a site value names an excluded site after normalising (lower case, site_key)."""
    bad = _excluded_values(sites.dropna().unique())
    return sites.isin(bad) if bad else pd.Series(False, index=sites.index)

# ---------------------------------------------------------------------------
# schema

PANEL_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("dataset", pa.string(), nullable=False),  # dfs_history, dfs_serp, dfs_labs_historical
        pa.field("locale", pa.string(), nullable=False),  # CC_lang, e.g. US_en
        pa.field("location_code", pa.int32()),  # DataForSEO / Google geo target id (country level here)
        pa.field("language", pa.string()),
        pa.field("keyword", pa.string(), nullable=False),
        pa.field("keyword_id", pa.string(), nullable=False),  # sha256(locale|keyword)[:16]
        pa.field("snapshot_time", pa.timestamp("us", tz="UTC"), nullable=False),  # when the page was read
        pa.field("snapshot_date", pa.date32(), nullable=False),
        pa.field("rank", pa.int16(), nullable=False),  # organic rank, rank_group
        pa.field("rank_absolute", pa.int16()),  # slot on the page counting every element
        pa.field("domain", pa.string()),  # host as listed
        pa.field("site", pa.string(), nullable=False),  # registrable domain
        pa.field("url", pa.string()),
        pa.field("title", pa.string()),
        pa.field("description", pa.string()),
        pa.field("is_new", pa.bool_()),  # DataForSEO's own flag versus its previous check
        pa.field("previous_rank_absolute", pa.int16()),  # None when DataForSEO gives none or 0
        pa.field("main_domain_rank", pa.int32()),  # DataForSEO domain rank of the site
        pa.field("page_rank", pa.int32()),  # DataForSEO rank of the page
        # DataForSEO estimated traffic of the item. It is computed from the record's headline volume, so
        # it is null on pages dated before keyword_info.time_update (record-dated, not page-dated).
        pa.field("etv", pa.float64()),
        pa.field("kd", pa.float64()),  # keyword difficulty recorded with this page
        pa.field("search_volume", pa.int64()),  # monthly volume of the latest month already known on the page date
        # main intent label; null on pages dated before the label's last_updated_time (or when it has no date)
        pa.field("intent", pa.string()),
        pa.field("items_count", pa.int32()),  # elements on the page, all types
        pa.field("se_results_count", pa.int64()),
        # True: dated on or after dfs.POISON_RISK_START and not repaired. Null only when a writer left it
        # unset; load_panel then fills it from the date rule, so it never comes back null.
        pa.field(dfs.POISON_RISK_COLUMN, pa.bool_()),
    ]
)
POISON_RISK_COLUMN = dfs.POISON_RISK_COLUMN
LEGACY_POISON_COLUMN = dfs.LEGACY_POISON_COLUMN
PANEL_COLUMNS: tuple[str, ...] = tuple(PANEL_SCHEMA.names)
assert PANEL_COLUMNS == dfs.PANEL_ROW_FIELDS, "the parser and the panel schema must list the same columns"

KEYWORD_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("dataset", pa.string(), nullable=False),  # dfs_keywords, dfs_keywords_history, dfs_history, dfs_serp
        pa.field("locale", pa.string(), nullable=False),
        pa.field("location_code", pa.int32()),
        pa.field("language", pa.string()),
        pa.field("keyword", pa.string(), nullable=False),
        pa.field("keyword_id", pa.string(), nullable=False),
        pa.field("snapshot_month", pa.string()),  # YYYY-MM the values describe
        pa.field("time_update", pa.timestamp("us", tz="UTC")),
        pa.field("search_volume", pa.int64()),
        pa.field("cpc", pa.float64()),
        pa.field("competition", pa.float64()),
        pa.field("competition_level", pa.string()),
        pa.field("low_top_of_page_bid", pa.float64()),
        pa.field("high_top_of_page_bid", pa.float64()),
        pa.field("kd", pa.float64()),
        pa.field("intent", pa.string()),
        pa.field("intent_updated", pa.timestamp("us", tz="UTC")),
        pa.field("detected_language", pa.string()),
        pa.field("categories", pa.string()),  # JSON list
        pa.field("monthly_volume", pa.string()),  # JSON {"YYYY-MM": volume or null}
    ]
)
assert tuple(KEYWORD_SCHEMA.names) == dfs.KEYWORD_FIELDS

_WHOIS_TYPES: dict[str, pa.DataType] = {
    "domain": pa.string(),
    "site": pa.string(),
    "tld": pa.string(),
    "registered": pa.bool_(),
    "epp_status_codes": pa.string(),
}
WHOIS_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field(
            name,
            _WHOIS_TYPES.get(name)
            or (pa.timestamp("us", tz="UTC") if name.endswith(("_datetime", "first_seen", "_time_update")) else None)
            or (pa.float64() if name.endswith(("_etv", "_cost")) else pa.int64()),
        )
        for name in dfs.WHOIS_FIELDS
    ]
)

DATASET_KEYWORDS = "dfs_keywords"
DATASET_KEYWORDS_HISTORY = "dfs_keywords_history"

# Snapshot gap windows the backtest uses as stand-ins (build plan step 7): month 1 and month 3.
MONTH1_WINDOW_DAYS = (30.0, 60.0)
MONTH3_WINDOW_DAYS = (75.0, 110.0)
GAP_BINS_DAYS: tuple[tuple[str, float, float], ...] = (
    ("<0.5", 0.0, 0.5),
    ("0.5-3", 0.5, 3.0),
    ("3-30", 3.0, 30.0),
    ("30-60", 30.0, 60.0),
    ("60-75", 60.0, 75.0),
    ("75-110", 75.0, 110.0),
    (">110", 110.0, float("inf")),
)
DEPTH_BINS: tuple[tuple[str, int, int], ...] = (
    ("1-9", 1, 9),
    ("10-29", 10, 29),
    ("30-49", 30, 49),
    ("50-74", 50, 74),
    ("75-89", 75, 89),
    ("90+", 90, 10_000),
)
TOP100 = 100


def default_public_root() -> Path:
    return forecast_paths().root / "public" / "dataforseo_samples"


def default_panel_dir() -> Path:
    return forecast_paths().cache / "panel"


def default_keywords_dir() -> Path:
    return forecast_paths().cache / "keywords"


def default_whois_dir() -> Path:
    return forecast_paths().cache / "whois"


# ---------------------------------------------------------------------------
# discovery and provenance


@dataclass(frozen=True)
class SampleFiles:
    history: tuple[Path, ...]
    snapshot: tuple[Path, ...]
    keywords: tuple[Path, ...]
    keywords_history: tuple[Path, ...]
    whois: tuple[Path, ...]


def discover_sample_files(root: str | Path | None = None) -> SampleFiles:
    """Sample files present under the download root. `.part` files of an unfinished download are left out."""
    base = Path(root) if root else default_public_root()

    def pick(rel: str) -> tuple[Path, ...]:
        folder = base / rel
        return tuple(sorted(folder.glob("*_*.json.gz"))) if folder.is_dir() else ()

    whois = base / "whois" / "full" / "data.json.gz"
    return SampleFiles(
        history=pick("google_history/serp_advanced"),
        snapshot=pick("google/serp_advanced"),
        keywords=pick("google/keywords"),
        keywords_history=pick("google_history/keywords"),
        whois=(whois,) if whois.is_file() else (),
    )


def _provenance(path: Path, root: Path | None, rows: int, stats: Mapping[str, Any]) -> dict[str, Any]:
    try:
        url = dfs.sample_url(path, root) if root else None
    except ValueError:
        url = None
    info = path.stat()
    return {
        "path": str(path),
        "url": url,
        "fetched_at": utc_iso(datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)),
        "fetched_at_source": "file modification time of the download",
        "bytes": info.st_size,
        "sha256": dfs.file_sha256(path),
        "rows": rows,
        "parse": {key: value for key, value in stats.items() if key != "path"},
    }


def write_sources(out_dir: Path, entries: list[dict[str, Any]], note: str) -> Path:
    """SOURCES.json beside a cache: what was read, from where, when, its sha256, and the rows it gave."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "SOURCES.json"
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            existing = {item["path"]: item for item in json.loads(target.read_text(encoding="utf-8")).get("files", [])}
        except (ValueError, KeyError, TypeError):
            existing = {}
    for entry in entries:
        existing[entry["path"]] = entry
    payload = {
        "written_at": utc_iso(datetime.now(timezone.utc)),
        "note": note,
        "files": [existing[key] for key in sorted(existing)],
    }
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    return target


# ---------------------------------------------------------------------------
# writing


def _write_partition(rows: list[dict[str, Any]], schema: pa.Schema, out_dir: Path, dataset: str, locale: str, part: str) -> Path:
    folder = out_dir / f"dataset={dataset}" / f"locale={locale}"
    folder.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema)
    target = folder / f"part-{part}.parquet"
    tmp = folder / f".part-{part}.parquet.tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, target)
    return target


def _source_part(path: Path) -> str:
    return path.name.split(".")[0]


def _kind_of(path: Path) -> str:
    text = path.as_posix()
    if "google_history/serp_advanced/" in text or "google_history/full/" in text:
        return "history"
    if "google/serp_advanced/" in text or "google/full/" in text:
        return "snapshot"
    if text.endswith(".json") and not text.endswith(".json.gz"):
        return "labs"
    raise ValueError(f"cannot tell what kind of result-page file {path} is")


def build_panel(
    source_files: Iterable[str | Path],
    out_dir: str | Path | None = None,
    *,
    root: str | Path | None = None,
    locales: Mapping[str, dfs.LocaleInfo] | None = None,
    labs_locale: str | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Parse result-page files and write the panel. One parquet part per source file.

    History and single-snapshot sample files are told apart by their folder. A
    plain `.json` file is read as a saved Labs historical_serps response and
    needs `labs_locale` or `locales`. Returns per-file counts. SOURCES.json is
    written beside the panel.
    """
    target = Path(out_dir) if out_dir else default_panel_dir()
    base = Path(root) if root else default_public_root()
    report: dict[str, Any] = {"out_dir": str(target), "files": []}
    provenance: list[dict[str, Any]] = []
    for raw in source_files:
        path = Path(raw)
        kind = _kind_of(path)
        stats = dfs.ParseStats()
        if kind == "history":
            rows = list(dfs.iter_history_rows(path, stats))
        elif kind == "snapshot":
            rows = list(dfs.iter_snapshot_rows(path, stats))
        else:
            rows = list(dfs.iter_labs_historical_serps(path, stats, locale=labs_locale, locales=locales))
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault((row["dataset"], row["locale"]), []).append(row)
        for (dataset, locale), chunk in sorted(groups.items()):
            _write_partition(chunk, PANEL_SCHEMA, target, dataset, locale, _source_part(path))
        provenance.append(_provenance(path, base, len(rows), stats.to_dict()))
        report["files"].append({"path": str(path), "kind": kind, "rows": len(rows), **stats.to_dict()})
        if log:
            log(f"{path.name}: {kind}, {stats.records} records, {stats.snapshots} pages, {len(rows)} rows")
        del rows, groups
    write_sources(
        target,
        provenance,
        "DataForSEO result pages (free sample databases or saved Labs historical_serps responses). REAL, country level, single pulls.",
    )
    return report


def build_keyword_table(
    files: Iterable[tuple[str | Path, str]],
    out_dir: str | Path | None = None,
    *,
    root: str | Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Write the keyword table. `files` are (path, dataset) pairs, dataset one of
    dfs_keywords, dfs_keywords_history, dfs_history, dfs_serp."""
    target = Path(out_dir) if out_dir else default_keywords_dir()
    base = Path(root) if root else default_public_root()
    report: dict[str, Any] = {"out_dir": str(target), "files": []}
    provenance = []
    for raw, dataset in files:
        path = Path(raw)
        stats = dfs.ParseStats()
        rows = [row for row in dfs.iter_keyword_rows(path, dataset, stats)]
        if rows:
            _write_partition(rows, KEYWORD_SCHEMA, target, dataset, rows[0]["locale"], _source_part(path))
        provenance.append(_provenance(path, base, len(rows), stats.to_dict()))
        report["files"].append({"path": str(path), "dataset": dataset, "rows": len(rows)})
        if log:
            log(f"{path.name}: {dataset}, {len(rows)} keyword rows")
    write_sources(target, provenance, "DataForSEO keyword data from the free sample databases. Vendor estimates, REAL as published.")
    return report


def build_whois_table(path: str | Path, out_dir: str | Path | None = None, *, root: str | Path | None = None) -> dict[str, Any]:
    """Write the WHOIS plus metrics table (one parquet file)."""
    target = Path(out_dir) if out_dir else default_whois_dir()
    base = Path(root) if root else default_public_root()
    source = Path(path)
    stats = dfs.ParseStats()
    rows = list(dfs.iter_whois_rows(source, stats))
    target.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=WHOIS_SCHEMA)
    pq.write_table(table, target / "whois.parquet", compression="zstd")
    write_sources(
        target,
        [_provenance(source, base, len(rows), stats.to_dict())],
        "DataForSEO WHOIS sample with ranking and backlink counts. Metrics are one 2026-03/04 snapshot, not an as-of history.",
    )
    return {"out_dir": str(target), "rows": len(rows), **stats.to_dict()}


# ---------------------------------------------------------------------------
# loading


def _parts(folder: Path, datasets: Sequence[str] | None, locales: Sequence[str] | None) -> list[Path]:
    out = []
    for part in sorted(folder.glob("dataset=*/locale=*/part-*.parquet")):
        dataset = part.parent.parent.name.split("=", 1)[1]
        locale = part.parent.name.split("=", 1)[1]
        if datasets is not None and dataset not in datasets:
            continue
        if locales is not None and locale not in locales:
            continue
        out.append(part)
    return out


def _load(folder: Path, schema: pa.Schema, columns: Sequence[str] | None, datasets: Sequence[str] | None, locales: Sequence[str] | None) -> pd.DataFrame:
    wanted = list(columns) if columns is not None else list(schema.names)
    unknown = [name for name in wanted if name not in schema.names]
    if unknown:
        raise ValueError(f"unknown columns {unknown}")
    read = list(dict.fromkeys(wanted + (["site"] if "site" in schema.names else [])))
    tables = [pq.read_table(part, columns=read) for part in _parts(folder, datasets, locales)]
    if not tables:
        return schema.empty_table().select(wanted).to_pandas()
    table = _drop_excluded_table(pa.concat_tables(tables))
    frame = table.select(wanted).to_pandas()
    return frame


def _risk_from_dates(table: pa.Table) -> pa.ChunkedArray:
    import pyarrow.compute as pc

    start = pa.scalar(dfs.POISON_RISK_START, type=pa.date32())
    return pc.fill_null(pc.greater_equal(table["snapshot_date"].cast(pa.date32()), start), False)


def _read_panel_part(part: Path, read: Sequence[str]) -> pa.Table:
    """One panel part with the columns `read`, `poison_risk` always filled (old files carry `poison_window`)."""
    import pyarrow.compute as pc

    names = set(pq.read_schema(part).names)
    need_risk = POISON_RISK_COLUMN in read
    from_file = [name for name in read if name != POISON_RISK_COLUMN or name in names]
    extra: list[str] = []
    if need_risk:
        extra.append("snapshot_date")
        if POISON_RISK_COLUMN not in names and LEGACY_POISON_COLUMN in names:
            extra.append(LEGACY_POISON_COLUMN)
    table = pq.read_table(part, columns=list(dict.fromkeys(from_file + extra)))
    if need_risk:
        by_date = _risk_from_dates(table)
        if POISON_RISK_COLUMN in names:
            risk = pc.fill_null(table[POISON_RISK_COLUMN], by_date)  # a value written at build time wins
        elif LEGACY_POISON_COLUMN in names:
            # The old flag had a wrong end date, and old pages never used the repair option,
            # so the risk is the old flag or the date rule.
            risk = pc.or_(pc.fill_null(table[LEGACY_POISON_COLUMN], False), by_date)
        else:
            risk = by_date
        if POISON_RISK_COLUMN in table.column_names:
            table = table.set_column(table.column_names.index(POISON_RISK_COLUMN), POISON_RISK_COLUMN, risk)
        else:
            table = table.append_column(POISON_RISK_COLUMN, risk)
    target = pa.schema([PANEL_SCHEMA.field(name).with_nullable(True) for name in read])
    return table.select(list(read)).cast(target)


def load_panel(
    columns: Sequence[str] | None = None,
    locales: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    panel_dir: str | Path | None = None,
) -> pd.DataFrame:
    """The panel as a DataFrame. Read only the columns you need: the text columns are large.

    Excluded sites are dropped again here. Integer columns with missing values
    come back as float with NaN, never 0. `poison_risk` is always filled: from
    the file, or from the old `poison_window` column and the date rule for a
    part written before the rename. Asking for "poison_window" returns the same
    values under that older name.
    """
    folder = Path(panel_dir) if panel_dir else default_panel_dir()
    asked = list(columns) if columns is not None else list(PANEL_SCHEMA.names)
    wanted = list(dict.fromkeys(POISON_RISK_COLUMN if name == LEGACY_POISON_COLUMN else name for name in asked))
    unknown = [name for name in wanted if name not in PANEL_SCHEMA.names]
    if unknown:
        raise ValueError(f"unknown columns {unknown}")
    read = list(dict.fromkeys(wanted + ["site"]))
    tables = [_read_panel_part(part, read) for part in _parts(folder, datasets, locales)]
    if not tables:
        frame = PANEL_SCHEMA.empty_table().select(wanted).to_pandas()
    else:
        table = _drop_excluded_table(pa.concat_tables(tables))
        frame = table.select(wanted).to_pandas()
    if LEGACY_POISON_COLUMN in asked:
        frame[LEGACY_POISON_COLUMN] = frame[POISON_RISK_COLUMN]
    return frame[list(dict.fromkeys(asked))]


def load_keywords(
    columns: Sequence[str] | None = None,
    locales: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    keywords_dir: str | Path | None = None,
) -> pd.DataFrame:
    return _load(Path(keywords_dir) if keywords_dir else default_keywords_dir(), KEYWORD_SCHEMA, columns, datasets, locales)


def load_whois(columns: Sequence[str] | None = None, *, whois_dir: str | Path | None = None) -> pd.DataFrame:
    target = (Path(whois_dir) if whois_dir else default_whois_dir()) / "whois.parquet"
    if not target.is_file():
        names = list(columns) if columns else list(WHOIS_SCHEMA.names)
        return pd.DataFrame({name: [] for name in names})
    frame = pq.read_table(target, columns=list(columns) if columns else None).to_pandas()
    if "site" in frame.columns:
        frame = frame[~excluded_mask(frame["site"])].reset_index(drop=True)
    return frame


def monthly_volume_long(keywords: pd.DataFrame) -> pd.DataFrame:
    """One row per (dataset, locale, keyword_id, snapshot_month, month) from the JSON monthly volumes."""
    records = []
    for row in keywords[["dataset", "locale", "keyword_id", "snapshot_month", "monthly_volume"]].itertuples(index=False):
        if not isinstance(row.monthly_volume, str):
            continue
        for month, volume in json.loads(row.monthly_volume).items():
            records.append((row.dataset, row.locale, row.keyword_id, row.snapshot_month, month, volume))
    frame = pd.DataFrame(records, columns=["dataset", "locale", "keyword_id", "snapshot_month", "month", "volume"])
    frame["volume"] = frame["volume"].astype("Float64")
    return frame


# ---------------------------------------------------------------------------
# helpers the models call


def _require(panel: pd.DataFrame, names: Sequence[str]) -> None:
    missing = [name for name in names if name not in panel.columns]
    if missing:
        raise ValueError(f"the panel needs columns {missing} for this helper")


def snapshots_of(panel: pd.DataFrame, keyword_id: str) -> list[tuple[pd.Timestamp, pd.DataFrame]]:
    """Every dated page of one keyword, oldest first, each sorted by organic rank."""
    _require(panel, ["keyword_id", "snapshot_time", "rank"])
    rows = panel[panel["keyword_id"] == keyword_id]
    out = []
    for moment, page in sorted(rows.groupby("snapshot_time", sort=True), key=lambda item: item[0]):
        out.append((moment, page.sort_values(["rank", "rank_absolute"] if "rank_absolute" in page else ["rank"]).reset_index(drop=True)))
    return out


def snapshot_table(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per dated page: keyword, time, depth (deepest organic rank), organic rows, index within the keyword (0 = first)."""
    _require(panel, ["keyword_id", "snapshot_time", "rank"])
    keys = ["keyword_id", "snapshot_time"]
    extra = [name for name in ("dataset", "locale", POISON_RISK_COLUMN, LEGACY_POISON_COLUMN) if name in panel.columns]
    grouped = panel.groupby(keys, sort=False)
    table = grouped["rank"].agg(depth="max", organic_rows="size").reset_index()
    if extra:
        table = table.merge(panel[keys + extra].drop_duplicates(keys), on=keys, how="left")
    table = table.sort_values(keys).reset_index(drop=True)
    table["snap_idx"] = table.groupby("keyword_id").cumcount().astype("int32")
    table["snapshots_of_keyword"] = table.groupby("keyword_id")["snap_idx"].transform("size").astype("int32")
    return table


def _poison_flag_column(frame: pd.DataFrame) -> str | None:
    """The column that flags poison-risk pages: poison_risk, or the older poison_window. None when neither is there."""
    for name in (POISON_RISK_COLUMN, LEGACY_POISON_COLUMN):
        if name in frame.columns:
            return name
    return None


def site_positions(
    panel: pd.DataFrame, *, drop_poison_window: bool = True, drop_poison_risk: bool | None = None
) -> pd.DataFrame:
    """One row per (keyword_id, snapshot_time, site) with the site's best organic rank on that page.

    `position` is that rank clamped to 1..50, or NT50 (51) when it is deeper.
    `rank100` is the best rank kept as is. `snapshot_depth` is the deepest
    organic rank on the page: when it is under 50, a site missing from the page
    is not known to be outside the top 50. Pages flagged `poison_risk` are left
    out unless `drop_poison_risk` is False. `drop_poison_window` is its older
    name and is used when `drop_poison_risk` is not given.

    When dropping is asked for and the frame has no flag column, the flag
    cannot be read, so it fails closed: every page dated on or after
    dfs.POISON_RISK_START (by snapshot_time) is dropped, repaired or not, and a
    warning says so. Load the poison_risk column to keep repaired pages.
    """
    _require(panel, ["keyword_id", "snapshot_time", "site", "rank"])
    frame = panel
    drop = drop_poison_window if drop_poison_risk is None else drop_poison_risk
    flag = _poison_flag_column(frame)
    if drop and flag is not None:
        frame = frame[~frame[flag].fillna(False).astype(bool)]
    elif drop:
        start = pd.Timestamp(dfs.POISON_RISK_START, tz="UTC")
        risky = ~(pd.to_datetime(frame["snapshot_time"], utc=True) < start)  # a missing time counts as at risk
        if bool(risky.any()):
            warnings.warn(
                f"site_positions: no {POISON_RISK_COLUMN} column, so {int(risky.sum())} row(s) dated on or after "
                f"{dfs.POISON_RISK_START} were dropped by date alone (repaired pages included). Load {POISON_RISK_COLUMN} to keep repaired pages.",
                stacklevel=2,
            )
        frame = frame[~risky.to_numpy()]
    frame = frame[~excluded_mask(frame["site"])]
    depth = frame.groupby(["keyword_id", "snapshot_time"], sort=False)["rank"].transform("max")
    keep = [
        name for name in ("dataset", "locale", "keyword", "snapshot_date", "url", POISON_RISK_COLUMN, LEGACY_POISON_COLUMN)
        if name in frame.columns
    ]
    work = frame[["keyword_id", "snapshot_time", "site", "rank"] + keep].copy()
    work["snapshot_depth"] = depth.astype("int16")
    work = work.sort_values(["keyword_id", "snapshot_time", "rank"], kind="mergesort")
    work = work.drop_duplicates(["keyword_id", "snapshot_time", "site"], keep="first")
    work = work.rename(columns={"rank": "rank100", "url": "best_url"})
    work["rank100"] = work["rank100"].astype("int16")
    work["position"] = np.where(work["rank100"] <= TOP_DEPTH, work["rank100"], NT50).astype("int16")
    return work.reset_index(drop=True)


def transitions(
    panel: pd.DataFrame,
    min_gap_days: float,
    max_gap_days: float,
    *,
    positions: pd.DataFrame | None = None,
    drop_poison_window: bool = True,
    drop_poison_risk: bool | None = None,
) -> pd.DataFrame:
    """Site moves between two pages of the same keyword whose gap is inside [min_gap_days, max_gap_days].

    For every keyword and every ordered pair of its pages (a earlier, b later)
    in the window, every site in the top 100 of a or of b gets one row:
    `position_a`, `position_b` (1..50, NT50 = 51 when deeper or absent),
    `rank100_a`, `rank100_b` (best rank as given, missing when absent),
    `gap_days` (the real gap), and the depth of both pages (`depth_ok` is True
    when both reach rank 50, so absence means NT50).

    `new_at_b` is True when the site is on page b and on no earlier page of the
    keyword in the data. `new_at_a` is the same for page a, and is missing
    (None) when a is the keyword's first page: the first page cannot tell a new
    site from an old one. "New" means new to this keyword in this data, not a
    new domain.
    """
    if not min_gap_days <= max_gap_days:
        raise ValueError("min_gap_days must not exceed max_gap_days")
    sp = positions if positions is not None else site_positions(
        panel, drop_poison_window=drop_poison_window, drop_poison_risk=drop_poison_risk
    )
    sp = sp[sp["rank100"] <= TOP100]
    snaps = (
        sp[["keyword_id", "snapshot_time", "snapshot_depth"] + [c for c in ("dataset", "locale") if c in sp.columns]]
        .drop_duplicates(["keyword_id", "snapshot_time"])
        .sort_values(["keyword_id", "snapshot_time"])
        .reset_index(drop=True)
    )
    snaps["snap_idx"] = snaps.groupby("keyword_id").cumcount().astype("int32")
    sp = sp.merge(snaps[["keyword_id", "snapshot_time", "snap_idx"]], on=["keyword_id", "snapshot_time"], how="left")
    first = sp.groupby(["keyword_id", "site"], sort=False)["snap_idx"].min().rename("first_idx").reset_index()

    left = snaps.rename(columns={"snapshot_time": "time_a", "snapshot_depth": "depth_a", "snap_idx": "idx_a"})
    right = snaps[["keyword_id", "snapshot_time", "snapshot_depth", "snap_idx"]].rename(
        columns={"snapshot_time": "time_b", "snapshot_depth": "depth_b", "snap_idx": "idx_b"}
    )
    pairs = left.merge(right, on="keyword_id")
    pairs = pairs[pairs["idx_b"] > pairs["idx_a"]]
    pairs["gap_days"] = (pairs["time_b"] - pairs["time_a"]).dt.total_seconds() / 86400.0
    pairs = pairs[(pairs["gap_days"] >= min_gap_days) & (pairs["gap_days"] <= max_gap_days)].reset_index(drop=True)
    columns = [
        "dataset",
        "locale",
        "keyword_id",
        "time_a",
        "time_b",
        "gap_days",
        "site",
        "position_a",
        "position_b",
        "rank100_a",
        "rank100_b",
        "depth_a",
        "depth_b",
        "depth_ok",
        "a_is_first_page",
        "pages_before_a",
        "new_at_a",
        "new_at_b",
    ]
    if pairs.empty:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})

    side = sp[["keyword_id", "snap_idx", "site", "rank100"]]
    at_a = pairs[["keyword_id", "idx_a", "idx_b"]].merge(
        side.rename(columns={"snap_idx": "idx_a", "rank100": "rank100_a"}), on=["keyword_id", "idx_a"]
    )
    at_b = pairs[["keyword_id", "idx_a", "idx_b"]].merge(
        side.rename(columns={"snap_idx": "idx_b", "rank100": "rank100_b"}), on=["keyword_id", "idx_b"]
    )
    moves = at_a.merge(at_b, on=["keyword_id", "idx_a", "idx_b", "site"], how="outer")
    moves = moves.merge(pairs, on=["keyword_id", "idx_a", "idx_b"], how="left")
    moves = moves.merge(first, on=["keyword_id", "site"], how="left")
    moves["rank100_a"] = moves["rank100_a"].astype("Int16")
    moves["rank100_b"] = moves["rank100_b"].astype("Int16")
    for name in ("a", "b"):
        rank = moves[f"rank100_{name}"]
        moves[f"position_{name}"] = rank.where(rank <= TOP_DEPTH, NT50).fillna(NT50).astype("int16")
    moves["depth_ok"] = (moves["depth_a"] >= TOP_DEPTH) & (moves["depth_b"] >= TOP_DEPTH)
    moves["a_is_first_page"] = moves["idx_a"] == 0
    moves["pages_before_a"] = moves["idx_a"].astype("int32")
    present_a = moves["rank100_a"].notna()
    present_b = moves["rank100_b"].notna()
    new_a = pd.Series(present_a & (moves["first_idx"] == moves["idx_a"]), dtype="boolean")
    new_a[moves["a_is_first_page"]] = pd.NA
    moves["new_at_a"] = new_a
    moves["new_at_b"] = pd.Series(present_b & (moves["first_idx"] == moves["idx_b"]), dtype="boolean")
    for name in ("dataset", "locale"):
        if name not in moves.columns:
            moves[name] = None
    out = moves[columns].sort_values(["keyword_id", "time_a", "time_b", "position_a", "position_b", "site"])
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# summary


def _quantiles(values: pd.Series | np.ndarray) -> dict[str, float | None]:
    array = np.asarray(values, dtype=float)
    array = array[~np.isnan(array)]
    if array.size == 0:
        return {"count": 0, "min": None, "p10": None, "p25": None, "median": None, "p75": None, "p90": None, "max": None}
    q = np.quantile(array, [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0])
    return {
        "count": int(array.size),
        "min": float(q[0]),
        "p10": float(q[1]),
        "p25": float(q[2]),
        "median": float(q[3]),
        "p75": float(q[4]),
        "p90": float(q[5]),
        "max": float(q[6]),
    }


def _bin_counts(values: np.ndarray, bins: Sequence[tuple[str, float, float]], *, closed_right: bool = False) -> dict[str, int]:
    out = {}
    for label, low, high in bins:
        if closed_right:
            mask = (values >= low) & (values <= high)
        else:
            mask = (values >= low) & (values < high)
        out[label] = int(mask.sum())
    return out


def _pair_window_counts(snaps: pd.DataFrame, windows: Mapping[str, tuple[float, float]]) -> dict[str, dict[str, int]]:
    """Counts of ordered page pairs (any two pages of one keyword) whose gap falls in each window."""
    small = snaps[["keyword_id", "snapshot_time"]]
    merged = small.merge(small, on="keyword_id", suffixes=("_a", "_b"))
    merged = merged[merged["snapshot_time_b"] > merged["snapshot_time_a"]]
    gaps = (merged["snapshot_time_b"] - merged["snapshot_time_a"]).dt.total_seconds() / 86400.0
    out = {}
    for name, (low, high) in windows.items():
        mask = (gaps >= low) & (gaps <= high)
        out[name] = {"pairs": int(mask.sum()), "keywords": int(merged.loc[mask, "keyword_id"].nunique())}
    return out


def _flag_count(snaps: pd.DataFrame) -> int | None:
    flag = _poison_flag_column(snaps)
    return int(snaps[flag].fillna(False).astype(bool).sum()) if flag is not None else None


def _summary_block(frame: pd.DataFrame) -> dict[str, Any]:
    snaps = snapshot_table(frame)
    ordered = snaps.sort_values(["keyword_id", "snapshot_time"])
    gaps = ordered.groupby("keyword_id")["snapshot_time"].diff().dt.total_seconds().dropna().to_numpy() / 86400.0
    depth = snaps["depth"].to_numpy()
    per_keyword = snaps.groupby("keyword_id").size()
    windows = {"0.5-3": (0.5, 3.0), "30-60 (month 1 stand-in)": MONTH1_WINDOW_DAYS, "75-110 (month 3 stand-in)": MONTH3_WINDOW_DAYS}
    return {
        "keywords": int(frame["keyword_id"].nunique()),
        "snapshots": int(len(snaps)),
        "rows": int(len(frame)),
        "sites": int(frame["site"].nunique()) if "site" in frame.columns else None,
        "date_first": str(frame["snapshot_date"].min()) if "snapshot_date" in frame.columns and len(frame) else None,
        "date_last": str(frame["snapshot_date"].max()) if "snapshot_date" in frame.columns and len(frame) else None,
        "poison_risk_pages": _flag_count(snaps),
        "poison_window_pages": _flag_count(snaps),  # the older key, same count
        "pages_per_keyword": _quantiles(per_keyword.to_numpy()),
        "depth_deepest_rank_per_page": _quantiles(depth),
        "depth_bins": _bin_counts(depth, DEPTH_BINS, closed_right=True),
        "pages_reaching_rank_50": int((depth >= TOP_DEPTH).sum()),
        "gap_days_consecutive": _quantiles(gaps),
        "gap_bins_consecutive": _bin_counts(gaps, GAP_BINS_DAYS),
        "page_pairs_in_window_any_two_pages": _pair_window_counts(snaps, windows),
    }


def summary(panel: pd.DataFrame) -> dict[str, Any]:
    """Counts by locale and overall: keywords, pages, rows, depth, date range, and gaps between pages of a keyword.

    Pass a panel with at least keyword_id, snapshot_time, rank and locale
    (site, snapshot_date and poison_risk add more lines). Each dataset is
    summarized apart, because a history page and a single-snapshot page are not
    the same kind of record.
    """
    _require(panel, ["keyword_id", "snapshot_time", "rank", "locale"])
    datasets = sorted(panel["dataset"].unique()) if "dataset" in panel.columns else [None]
    out: dict[str, Any] = {"label": "REAL DataForSEO result pages, country level, single pulls", "datasets": {}}
    for dataset in datasets:
        frame = panel if dataset is None else panel[panel["dataset"] == dataset]
        block: dict[str, Any] = {"overall": _summary_block(frame), "by_locale": {}}
        english = frame[frame["locale"].str.endswith("_en")]
        block["english_locales"] = _summary_block(english) if len(english) else None
        for locale, chunk in frame.groupby("locale", sort=True):
            block["by_locale"][str(locale)] = _summary_block(chunk)
        out["datasets"][str(dataset)] = block
    return out


LANDING_BANDS: tuple[str, ...] = ("1-10", "11-20", "21-50", "51-100", "absent")


def _landing_band(rank: Any) -> str:
    if rank is None or pd.isna(rank):
        return "absent"
    value = int(rank)
    if value <= 10:
        return "1-10"
    if value <= 20:
        return "11-20"
    if value <= TOP_DEPTH:
        return "21-50"
    return "51-100"


def new_site_report(panel: pd.DataFrame, *, positions: pd.DataFrame | None = None) -> dict[str, Any]:
    """How many sites are new to a keyword at some page, where they enter, and where they are one and three months on.

    Entry: a (keyword, site) whose first page in the data is not the keyword's
    first page, on a page reaching rank 100 or less. "Later" uses the month 1
    (30 to 60 days) and month 3 (75 to 110 days) windows from the entry page;
    when several later pages fall in a window, the nearest to its middle is used.
    Pages flagged poison_risk are left out. Only pairs where both pages reach
    rank 50 are counted for the later bands, so absent means outside the top 50.
    """
    sp = positions if positions is not None else site_positions(panel)
    sp = sp[sp["rank100"] <= TOP100]
    snaps = snapshot_table(sp.rename(columns={"rank100": "rank"})[["keyword_id", "snapshot_time", "rank"]])
    sp = sp.merge(snaps[["keyword_id", "snapshot_time", "snap_idx"]], on=["keyword_id", "snapshot_time"], how="left")
    first = sp.groupby(["keyword_id", "site"], sort=False)["snap_idx"].transform("min")
    entries = sp[(sp["snap_idx"] == first) & (sp["snap_idx"] > 0)]
    keyword_pages = snaps.groupby("keyword_id").size()
    with_later = keyword_pages[keyword_pages > 1]
    result: dict[str, Any] = {
        "label": "REAL DataForSEO pages. New means new to this keyword in this data, not a new domain.",
        "keywords_with_two_or_more_pages": int(len(with_later)),
        "site_keyword_entries": int(len(entries)),
        "distinct_sites_entering": int(entries["site"].nunique()),
        "entry_band": {band: 0 for band in LANDING_BANDS[:-1]},
    }
    for band, count in entries["rank100"].map(_landing_band).value_counts().items():
        result["entry_band"][band] = int(count)
    # An entry right after a short page may be a site that was there but below that page's last result.
    raw_depth = sp.groupby(["keyword_id", "snap_idx"])["snapshot_depth"].first()
    prev_depth = raw_depth.reindex(list(zip(entries["keyword_id"], entries["snap_idx"] - 1))).to_numpy()
    deep_enough = prev_depth >= entries["rank100"].to_numpy()
    result["entries_where_previous_page_reached_the_entry_rank"] = int(np.nansum(deep_enough))
    result["entry_band_where_previous_page_reached_the_entry_rank"] = {band: 0 for band in LANDING_BANDS[:-1]}
    for band, count in entries.loc[deep_enough, "rank100"].map(_landing_band).value_counts().items():
        result["entry_band_where_previous_page_reached_the_entry_rank"][band] = int(count)
    for name, (low, high) in (("month1_30_60_days", MONTH1_WINDOW_DAYS), ("month3_75_110_days", MONTH3_WINDOW_DAYS)):
        moves = transitions(sp, low, high, positions=sp.drop(columns=["snap_idx"]))
        moves = moves[moves["new_at_a"].fillna(False).astype(bool) & moves["depth_ok"].astype(bool)].copy()
        middle = (low + high) / 2.0
        moves["distance"] = (moves["gap_days"] - middle).abs()
        moves = moves.sort_values(["keyword_id", "site", "time_a", "distance"]).drop_duplicates(["keyword_id", "site", "time_a"])
        table = {band: {later: 0 for later in LANDING_BANDS} for band in LANDING_BANDS[:-1]}
        for entry_rank, later_rank in zip(moves["rank100_a"], moves["rank100_b"]):
            table[_landing_band(entry_rank)][_landing_band(later_rank)] += 1
        result[name] = {"entries_followed": int(len(moves)), "entry_band_by_later_band": table}
    return result


# ---------------------------------------------------------------------------
# A whole panel build into one folder, and its summary


class PanelExistsError(FileExistsError):
    """The target panel folder already holds files and overwriting was not asked for."""


def _folder_has_files(folder: Path) -> bool:
    return folder.exists() and any(folder.iterdir())


def select_sample_files(files: SampleFiles, kinds: Iterable[str], locales: Iterable[str] | None = None) -> list[Path]:
    """The result-page sample files of the asked kinds (history, snapshot) and locales (all when None)."""
    wanted_kinds = {kind.strip() for kind in kinds if kind.strip()}
    unknown = wanted_kinds - {"history", "snapshot"}
    if unknown:
        raise ValueError(f"unknown kinds {sorted(unknown)}. Use history, snapshot, or both.")
    chosen: list[Path] = []
    if "history" in wanted_kinds:
        chosen += list(files.history)
    if "snapshot" in wanted_kinds:
        chosen += list(files.snapshot)
    if locales is not None:
        wanted = {item.strip() for item in locales if item.strip()}
        chosen = [path for path in chosen if dfs.locale_from_path(path) in wanted]
    return chosen


def build_panel_into(
    source_files: Sequence[str | Path],
    out_dir: str | Path,
    *,
    overwrite: bool = False,
    root: str | Path | None = None,
    log: Callable[[str], None] | None = None,
    stamp: str | None = None,
) -> dict[str, Any]:
    """Build a whole panel into `out_dir`, safely.

    A missing or empty folder is filled. A folder that already holds files is
    refused (PanelExistsError) unless `overwrite` is True. With `overwrite` the
    new panel is first built in a sibling folder; only when that build has
    finished is the old folder moved aside and the new one put in its place,
    and then the old one is deleted. A failed build leaves the old panel as it
    was. No source file means nothing to build, and that is refused too.
    """
    import shutil

    target = Path(out_dir)
    files = [Path(item) for item in source_files]
    if not files:
        raise ValueError("no result-page sample files were found to build a panel from")
    if _folder_has_files(target) and not overwrite:
        raise PanelExistsError(f"{target} already holds a panel. Pass --overwrite to replace it, or choose a new folder with --out.")
    tag = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    building = target.with_name(f".{target.name}.building-{tag}")
    if building.exists():
        shutil.rmtree(building)
    try:
        report = build_panel(files, building, root=root, log=log)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    old: Path | None = None
    if target.exists():
        old = target.with_name(f".{target.name}.old-{tag}")
        os.replace(target, old)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(building, target)
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
    report["out_dir"] = str(target)
    report["replaced_existing"] = old is not None
    return report


SUMMARY_COLUMNS: tuple[str, ...] = ("dataset", "locale", "keyword_id", "snapshot_time", "snapshot_date", "rank", "site", POISON_RISK_COLUMN)


def panel_datasets(folder: str | Path) -> list[str]:
    return sorted({part.parent.parent.name.split("=", 1)[1] for part in _parts(Path(folder), None, None)})


def panel_summary(folder: str | Path, *, new_sites: bool = True) -> dict[str, Any]:
    """The summary of the panel in `folder`, one dataset at a time (memory stays small). REAL observations."""
    path = Path(folder)
    payload: dict[str, Any] = {"label": "REAL DataForSEO result pages, country level, single pulls", "panel_dir": str(path), "datasets": {}}
    for name in panel_datasets(path):
        panel = load_panel(list(SUMMARY_COLUMNS), datasets=[name], panel_dir=path)
        payload["datasets"].update(summary(panel)["datasets"])
        if new_sites and name == dfs.DATASET_HISTORY:
            payload["new_sites_history"] = new_site_report(panel)
        del panel
    return payload


# ---------------------------------------------------------------------------
# CLI


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    return str(value)


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False, default=_json_default))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.sources.panel", description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=None, help="download root of the DataForSEO samples")
    parser.add_argument("--panel-dir", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="parse the sample result pages and write the panel")
    build.add_argument("--kinds", default="history,snapshot", help="history, snapshot, or both")
    build.add_argument("--locales", default=None, help="comma list of CC_lang to build, default all present")
    sub.add_parser("keywords", help="write the keyword table")
    sub.add_parser("whois", help="write the WHOIS table")
    summ = sub.add_parser("summary", help="counts by locale, gaps, windows, new sites")
    summ.add_argument("--out", default=None, help="write the JSON here too")
    noise = sub.add_parser("noise", help="single-pull agreement on repeated pulls (DataForSEO panel, Wellesley Dobbs)")
    noise.add_argument("--dobbs-dir", default=None)
    noise.add_argument("--out", default=None)
    noise.add_argument("--skip-dobbs", action="store_true")
    dl = sub.add_parser("dobbs-download", help="download the Wellesley Dobbs organic files the noise set uses")
    dl.add_argument("--dobbs-dir", default=None)
    args = parser.parse_args(argv)

    log = lambda text: print(text, file=sys.stderr)  # noqa: E731
    files = discover_sample_files(args.root)
    if args.command == "build":
        kinds = {item.strip() for item in args.kinds.split(",") if item.strip()}
        chosen: list[Path] = []
        if "history" in kinds:
            chosen += list(files.history)
        if "snapshot" in kinds:
            chosen += list(files.snapshot)
        if args.locales:
            wanted = {item.strip() for item in args.locales.split(",")}
            chosen = [path for path in chosen if dfs.locale_from_path(path) in wanted]
        report = build_panel(chosen, args.panel_dir, root=args.root, log=log)
        _print({"files": len(report["files"]), "rows": sum(item["rows"] for item in report["files"]), "out_dir": report["out_dir"]})
        return 0
    if args.command == "keywords":
        pairs = [(p, DATASET_KEYWORDS) for p in files.keywords] + [(p, DATASET_KEYWORDS_HISTORY) for p in files.keywords_history]
        pairs += [(p, dfs.DATASET_HISTORY) for p in files.history] + [(p, dfs.DATASET_SNAPSHOT) for p in files.snapshot]
        report = build_keyword_table(pairs, root=args.root, log=log)
        _print({"files": len(report["files"]), "rows": sum(item["rows"] for item in report["files"])})
        return 0
    if args.command == "whois":
        if not files.whois:
            print("no whois/full/data.json.gz under the download root", file=sys.stderr)
            return 1
        _print(build_whois_table(files.whois[0], root=args.root))
        return 0
    if args.command == "summary":
        folder = Path(args.panel_dir) if args.panel_dir else default_panel_dir()
        payload = panel_summary(folder)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        _print(payload)
        return 0
    if args.command == "noise":
        from . import noise_sets

        payload = noise_sets.run_noise(panel_dir=args.panel_dir, dobbs_dir=None if args.skip_dobbs else (args.dobbs_dir or noise_sets.default_dobbs_dir()))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        _print(payload)
        return 0
    if args.command == "dobbs-download":
        from . import noise_sets

        _print(noise_sets.download_dobbs(args.dobbs_dir))
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
