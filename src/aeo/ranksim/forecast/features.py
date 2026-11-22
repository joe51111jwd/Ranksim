"""The shared feature frame: one place that turns dated observations into model inputs.

What is REAL here: positions, titles, search difficulty, volume and intent are
copied from dated Google result pages in the panel (sources/panel.py). Site
strength values are copied from dated public lists (sources/strength_lists.py)
and every one passes an `AsOfGuard` keyed on its PUBLICATION date. What is
PREDICTED here: the site start-date estimate (sources/domain_age.py, a rule
over REAL evidence) and every content match score (content.py, model output).
Rows built from `synth.World` are SYNTHETIC and say so in the `label` column.

The leakage rules this module enforces:

- A row has an `as_of` date. Every input it uses is dated on or before it:
  strength lists through the guard (a later snapshot raises `LeakError`),
  panel history only from pages dated on or before `as_of`, the start-date
  estimate only from evidence published on or before `as_of`, and nothing
  from the outcome page.
- Open PageRank holds only domains in today's top 10 million, so whether a site
  has a value there carries future information. Its value is kept in the frame
  for reports (`st_s_opr`) and it is listed in `SURVIVORSHIP_LIMITED`.
  `check_model_features` refuses it in any model feature list. The start-date
  estimate here does not use Open PageRank evidence either.
- A left-censored start date ("started on or before U") is never turned into an
  age. The frame carries the lower bound on age and the censoring flag apart.
- A site with no result on a page has no title there. Its content score is
  missing (NaN), never zero.

What the panel cannot give. Every (site, search) cell in the panel exists
because the site appeared on that search at least once. A brand-new site's
upload-day forecast is mostly "not in the top 50" on searches it never ranks
for, and no such cell is in the panel. Frames built from the panel cannot
validate that case. `build_synthetic_frame` has such cells with known truth
(SYNTHETIC), and the paid pulls (trainingset.py) will supply real ones later
in the same column layout.

Three entry points build features for one item (`page_features`,
`site_features`, `cell_features`); the frame builders do the same work in bulk.

CLI:
  python -m aeo.ranksim.forecast.features build-panel --searches 2000 --seed 20261122
  python -m aeo.ranksim.forecast.features build-synthetic --seed 3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .asof import AsOfGuard, Dated, add_months, as_date, month_start
from .chain import canonical_json, sha256_text
from .config import EXCLUDED_SITES, NT50, TOP_DEPTH, forecast_paths
from .types import REAL, SYNTHETIC

FEATURES_VERSION = "features-v1"
DEFAULT_SEED = 20261122

# Strength measures, as "higher is stronger" values. Absent from a list that was
# searched for the site means weaker than every listed site: it gets a floor
# below the list's last value. Not searched means unknown: NaN.
CC_ABSENT_POS_LOG10 = 9.5  # log10 of a position past every Common Crawl domain node (about 1.8e8)
TOPLIST_ABSENT_LOG10 = 6.5  # past the last bucket or rank of a top 1 million list
MEASURES: tuple[str, ...] = ("s_cc_hc", "s_cc_pr", "s_cc_hosts", "s_crux", "s_tranco", "s_opr")
MEASURE_LABELS: dict[str, str] = {
    "s_cc_hc": "Common Crawl harmonic centrality position (log, inverted)",
    "s_cc_pr": "Common Crawl PageRank position (log, inverted)",
    "s_cc_hosts": "Common Crawl linked host count (log)",
    "s_crux": "Chrome popularity bucket (log, inverted)",
    "s_tranco": "Tranco rank (log, inverted)",
    "s_opr": "Open PageRank score (survivorship limited)",
}
SURVIVORSHIP_LIMITED: tuple[str, ...] = ("s_opr",)
MODEL_MEASURES: tuple[str, ...] = tuple(m for m in MEASURES if m not in SURVIVORSHIP_LIMITED)
# The link count stand-in for "a plain link count" baselines: linked hosts in the Common Crawl graph.
LINK_COUNT_MEASURE = "s_cc_hosts"

PAGE_BANDS: tuple[tuple[str, int, int], ...] = (("t10", 1, 10), ("t11_20", 11, 20), ("t21_50", 21, 50))
CELL_TOPS: tuple[tuple[str, int], ...] = (("top10", 10), ("top20", 20), ("top50", 50))
QUANTILES: tuple[float, ...] = (0.25, 0.5, 0.75)
INTENTS: tuple[str, ...] = ("informational", "navigational", "commercial", "transactional")
YOUNG_AGE_DAYS = 730  # "young" means an estimated start under two years before as_of

# Frame windows: the gap between the input page (as_of) and the outcome page.
DEFAULT_WINDOW_DAYS = (28.0, 120.0)

START_SOURCES_EXCLUDED = ("openpagerank_first_month",)  # survivorship: OPR holds only today's top 10 million


# ---------------------------------------------------------------------------
# model feature hygiene


def check_model_features(columns: Iterable[str]) -> list[str]:
    """The list back when every column may feed a fitted model. Raises ValueError otherwise.

    Refused: any Open PageRank column (presence carries future information),
    snapshot bookkeeping columns, identifiers, outcomes and the persistence reading
    of the outcome page.
    """
    out = list(columns)
    bad = []
    for name in out:
        low = name.lower()
        if "opr" in low or low.endswith("snapshot_age_days") or "newer_release_not_materialised" in low:
            bad.append(name)
        elif name in ("position_out", "rank100_out", "outcome_time", "outcome_date", "site", "keyword", "keyword_id"):
            bad.append(name)
    if bad:
        raise ValueError(f"these columns may not feed a model: {bad}")
    return out


def search_group(keyword: str) -> str:
    """The search group used by every split here (the trainingset rule: content words, sorted, hashed)."""
    from .trainingset import search_group as _group

    return _group(str(keyword))


def stable_share(key: str, seed: int) -> float:
    """A number in [0, 1) from sha256(seed|key). The same key always lands on the same side."""
    digest = hashlib.sha256(f"{seed}|{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


# ---------------------------------------------------------------------------
# strength values


def _f(value: Any) -> float:
    if value is None:
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


def strength_values(raw: Mapping[str, Any]) -> dict[str, float]:
    """Higher-is-stronger values from one site's `strength_lists.strength_features` row.

    REAL values transformed, nothing fitted. A list that was searched and does not
    hold the site gives the floor (weaker than any listed site). A list that was
    not searched gives NaN. Open PageRank missing stays NaN: its absence is not
    knowable at the date (survivorship), so it is never a floor.
    """
    out: dict[str, float] = {}
    present = _f(raw.get("cc_present"))
    for name, key in (("s_cc_hc", "cc_hc_pos"), ("s_cc_pr", "cc_pr_pos")):
        pos = _f(raw.get(key))
        if present == 1.0 and pos > 0:
            out[name] = -math.log10(pos)
        elif present == 0.0:
            out[name] = -CC_ABSENT_POS_LOG10
        else:
            out[name] = math.nan
    hosts = _f(raw.get("cc_n_hosts"))
    out["s_cc_hosts"] = math.log10(1.0 + hosts) if present == 1.0 and hosts >= 0 else (0.0 if present == 0.0 else math.nan)
    for name, prefix, key in (("s_crux", "crux", "crux_bucket"), ("s_tranco", "tranco", "tranco_rank")):
        here = _f(raw.get(f"{prefix}_present"))
        value = _f(raw.get(key))
        if here == 1.0 and value > 0:
            out[name] = -math.log10(value)
        elif here == 0.0:
            out[name] = -TOPLIST_ABSENT_LOG10
        else:
            out[name] = math.nan
    out["s_opr"] = _f(raw.get("opr_opr"))
    return out


RAW_STRENGTH_KEYS: tuple[str, ...] = (
    "cc_present", "cc_hc_pos", "cc_pr_pos", "cc_n_hosts", "cc_snapshot_age_days",
    "crux_present", "crux_bucket", "crux_snapshot_age_days",
    "tranco_present", "tranco_rank", "tranco_snapshot_age_days",
    "opr_opr", "opr_snapshot_age_days",
)


def _snapshot_key(sources: Sequence[Any], day: date) -> tuple[str, ...]:
    from .sources.strength_lists import pick_snapshot

    key = []
    for source in sources:
        snap = pick_snapshot(source.snapshots(), day)
        key.append(snap.snapshot_id if snap is not None else "-")
    return tuple(key)


def strength_for_dates(
    site_dates: pd.DataFrame,
    *,
    sources: Sequence[Any] | None = None,
    root: str | Path | None = None,
    log: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Dated strength per (site, as_of) row, every value through an AsOfGuard. REAL.

    `site_dates` has columns `site` and `as_of` (dates). Dates are grouped by the
    set of snapshots each source would pick. Inside a group the picked snapshots
    are the same, so `strength_lists.strength_features` runs once at the group's
    EARLIEST date with a guard for that date (every value is then dated on or
    before every date in the group), and the snapshot ages are moved to each
    row's own date. A snapshot dated after its as_of raises `asof.LeakError`.
    Returns the input columns plus `RAW_STRENGTH_KEYS` and the `MEASURES`.
    """
    from .sources import strength_lists as sl

    chosen = list(sources) if sources is not None else sl.default_sources(root)
    frame = site_dates[["site", "as_of"]].copy()
    frame["as_of"] = [as_date(d) for d in frame["as_of"]]
    unique_days = sorted(set(frame["as_of"]))
    key_of = {day: _snapshot_key(chosen, day) for day in unique_days}
    group_start: dict[tuple[str, ...], date] = {}
    for day in unique_days:
        group_start.setdefault(key_of[day], day)
    frame["_key"] = [key_of[d] for d in frame["as_of"]]
    results: list[pd.DataFrame] = []
    for key, start in sorted(group_start.items(), key=lambda kv: kv[1]):
        part = frame[frame["_key"] == key]
        sites = sorted(set(part["site"]))
        guard = AsOfGuard(start)
        found = sl.strength_features(sites, start, guard, sources=chosen)
        records = []
        for site in sites:
            norm = sl.normalise_site(site)
            row = found.get(norm) if norm else None
            records.append({"site": site, **{k: (row or {}).get(k) for k in RAW_STRENGTH_KEYS}})
        table = pd.DataFrame.from_records(records, columns=["site", *RAW_STRENGTH_KEYS])
        merged = part.merge(table, on="site", how="left")
        shift = np.array([(d - start).days for d in merged["as_of"]], dtype=float)
        for col in RAW_STRENGTH_KEYS:
            if col.endswith("snapshot_age_days"):
                merged[col] = pd.to_numeric(merged[col], errors="coerce") + shift
        results.append(merged)
        if log:
            log(f"strength group from {start}: {len(sites)} sites, {len(part)} rows")
    if not results:
        out = frame.drop(columns="_key")
        for col in (*RAW_STRENGTH_KEYS, *MEASURES):
            out[col] = np.nan
        return out
    out = pd.concat(results, ignore_index=True).drop(columns="_key")
    for col in RAW_STRENGTH_KEYS:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
    values = add_strength_values(out)
    return values


def add_strength_values(frame: pd.DataFrame) -> pd.DataFrame:
    """Vectorised `strength_values` over a frame with the raw strength columns. Returns a copy with MEASURES added."""
    out = frame.copy()

    def col(name: str) -> np.ndarray:
        return pd.to_numeric(out[name], errors="coerce").to_numpy(dtype=float) if name in out else np.full(len(out), np.nan)

    present = col("cc_present")
    with np.errstate(divide="ignore", invalid="ignore"):
        for name, key in (("s_cc_hc", "cc_hc_pos"), ("s_cc_pr", "cc_pr_pos")):
            pos = col(key)
            value = np.where((present == 1.0) & (pos > 0), -np.log10(pos), np.nan)
            out[name] = np.where(present == 0.0, -CC_ABSENT_POS_LOG10, value)
        hosts = col("cc_n_hosts")
        value = np.where((present == 1.0) & (hosts >= 0), np.log10(1.0 + hosts), np.nan)
        out["s_cc_hosts"] = np.where(present == 0.0, 0.0, value)
        for name, prefix, key in (("s_crux", "crux", "crux_bucket"), ("s_tranco", "tranco", "tranco_rank")):
            here = col(f"{prefix}_present")
            raw = col(key)
            value = np.where((here == 1.0) & (raw > 0), -np.log10(raw), np.nan)
            out[name] = np.where(here == 0.0, -TOPLIST_ABSENT_LOG10, value)
    out["s_opr"] = col("opr_opr")
    return out


# ---------------------------------------------------------------------------
# start dates


@dataclass
class StartEvidence:
    """REAL first-seen and registration evidence per site, without Open PageRank (survivorship)."""

    by_site: dict[str, list[Dated]] = field(default_factory=dict)

    @classmethod
    def load(cls, sites: Iterable[str], root: str | Path | None = None) -> "StartEvidence":
        """Common Crawl first release and cached RDAP registrations. No network."""
        from .sources import domain_age
        from .sources.strength_lists import default_root

        base = default_root(root)
        site_set = sorted(set(sites))
        table: dict[str, list[Dated]] = {site: [] for site in site_set}
        for source in (domain_age.cc_first_release(site_set, base), domain_age.RdapLookup(base).registration_dates(site_set)):
            for site, dated in source.items():
                if dated.source in START_SOURCES_EXCLUDED:
                    continue
                table.setdefault(site, []).append(dated)
        return cls(table)


START_COLUMNS: tuple[str, ...] = (
    "sd_age_days", "sd_uncertainty_days", "sd_left_censored", "sd_age_lower_bound_days", "sd_no_evidence",
    "sd_possibly_reregistered", "sd_registration_known", "sd_young",
)


def start_features(site: str, as_of: date, evidence: Sequence[Dated]) -> dict[str, float]:
    """Start-date features for one site as of a date. The estimate is PREDICTED from REAL evidence.

    Evidence published after `as_of` is dropped (domain_age.best_start_date does
    that). `sd_age_days` is NaN when the estimate is left censored or missing; the
    lower bound on age (as_of minus the earliest first-seen date) is apart.
    `sd_young` is 1 when the estimate exists and the age is under YOUNG_AGE_DAYS,
    0 when it exists and is older or when the site is left censored before that
    many days, NaN when unknown.
    """
    from .sources.domain_age import best_start_date

    if any(item.source in START_SOURCES_EXCLUDED for item in evidence):
        raise ValueError("Open PageRank evidence is survivorship limited and is not used for start dates here")
    day = as_date(as_of)
    est = best_start_date(site, list(evidence), as_of=day)
    age = float((day - est.estimate).days) if est.estimate is not None else math.nan
    lower = float((day - est.upper).days) if est.upper is not None else math.nan
    censored = 1.0 if "left_censored" in est.flags else 0.0
    if math.isfinite(age):
        young = 1.0 if age < YOUNG_AGE_DAYS else 0.0
    elif censored and math.isfinite(lower) and lower >= YOUNG_AGE_DAYS:
        young = 0.0
    else:
        young = math.nan
    return {
        "sd_age_days": age,
        "sd_uncertainty_days": float(est.uncertainty_days) if est.uncertainty_days is not None else math.nan,
        "sd_left_censored": censored,
        "sd_age_lower_bound_days": lower,
        "sd_no_evidence": 1.0 if est.method == "no_evidence" else 0.0,
        "sd_possibly_reregistered": 1.0 if "possibly_reregistered" in est.flags else 0.0,
        "sd_registration_known": 1.0 if est.lower is not None else 0.0,
        "sd_young": young,
    }


def start_features_frame(site_dates: pd.DataFrame, evidence: StartEvidence) -> pd.DataFrame:
    """`start_features` for every (site, as_of) row. Cached per (site, evidence visible on the date)."""
    cache: dict[tuple[str, tuple[date, ...], date], dict[str, float]] = {}
    rows = []
    for site, day in zip(site_dates["site"], site_dates["as_of"]):
        when = as_date(day)
        items = evidence.by_site.get(site, [])
        visible = tuple(sorted(i.snapshot_date for i in items if i.snapshot_date is not None and i.snapshot_date <= when))
        # age moves with the date, so the cache key keeps the date but only within the site's visible evidence
        key = (site, visible, when)
        if key not in cache:
            cache[key] = start_features(site, when, items)
        rows.append(cache[key])
    return pd.DataFrame.from_records(rows, columns=list(START_COLUMNS), index=site_dates.index)


# ---------------------------------------------------------------------------
# panel history


HIST_COLUMNS: tuple[str, ...] = ("hist_searches_locale", "hist_top10_locale", "hist_top50_locale", "hist_share_top10_locale",
                                 "hist_searches_all", "hist_pages_this_search", "hist_last_rank_this_search")


def site_positions_frame(rows: pd.DataFrame) -> pd.DataFrame:
    """Best rank per (keyword_id, snapshot_time, site) from panel-shaped rows, with the page depth. REAL."""
    from .sources.panel import site_positions

    return site_positions(rows, drop_poison_risk=True)


def history_features(cells: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Panel history per cell, from pages dated on or before the cell's `as_of` only. REAL counts.

    `cells` has site, locale, keyword_id, as_of. `positions` has keyword_id,
    snapshot_time, snapshot_date, site, rank100, locale. Counts are over OTHER
    searches of the same locale (the cell's own search is left out), so the
    feature never restates the search's own past. The own search is reported
    apart (`hist_pages_this_search`, `hist_last_rank_this_search`), for the
    persistence-style models that may use it.
    """
    pos = positions[["locale", "keyword_id", "site", "snapshot_date", "rank100"]].copy()
    pos["snapshot_date"] = [as_date(d) for d in pos["snapshot_date"]]
    first = pos.groupby(["locale", "site", "keyword_id"], sort=False)["snapshot_date"].min().rename("first_seen").reset_index()
    top10 = pos[pos["rank100"] <= 10].groupby(["locale", "site", "keyword_id"], sort=False)["snapshot_date"].min().rename("first_top10")
    top50 = pos[pos["rank100"] <= TOP_DEPTH].groupby(["locale", "site", "keyword_id"], sort=False)["snapshot_date"].min().rename("first_top50")
    first = first.join(top10, on=["locale", "site", "keyword_id"]).join(top50, on=["locale", "site", "keyword_id"])

    out = pd.DataFrame(index=cells.index)
    work = cells[["site", "locale", "keyword_id", "as_of"]].copy()
    work["as_of"] = [as_date(d) for d in work["as_of"]]
    work["_row"] = np.arange(len(work))

    def count_before(event_col: str, by: list[str]) -> np.ndarray:
        events = first.dropna(subset=[event_col])[by + [event_col]].copy()
        events[event_col] = pd.to_datetime(events[event_col])
        events = events.sort_values(event_col)
        events["_n"] = events.groupby(by, sort=False).cumcount() + 1
        query = work[by + ["as_of", "_row"]].copy()
        query["as_of"] = pd.to_datetime(query["as_of"])
        query = query.sort_values("as_of")
        merged = pd.merge_asof(query, events.rename(columns={event_col: "as_of"}), on="as_of", by=by, direction="backward")
        result = np.zeros(len(work))
        result[merged["_row"].to_numpy()] = merged["_n"].fillna(0).to_numpy()
        return result

    def own_before(event_col: str) -> np.ndarray:
        own = work.merge(first[["locale", "site", "keyword_id", event_col]], on=["locale", "site", "keyword_id"], how="left")
        own = own.sort_values("_row")
        seen = own[event_col].to_numpy()
        when = own["as_of"].to_numpy()
        return np.array([1.0 if isinstance(s, date) and s <= w else 0.0 for s, w in zip(seen, when)])

    n_loc = count_before("first_seen", ["locale", "site"]) - own_before("first_seen")
    n10 = count_before("first_top10", ["locale", "site"]) - own_before("first_top10")
    n50 = count_before("first_top50", ["locale", "site"]) - own_before("first_top50")
    first_all = first.groupby(["site", "keyword_id"], sort=False)["first_seen"].min().rename("first_any").reset_index()
    events = first_all.copy()
    events["first_any"] = pd.to_datetime(events["first_any"])
    events = events.sort_values("first_any")
    events["_n"] = events.groupby("site", sort=False).cumcount() + 1
    query = work[["site", "as_of", "_row"]].copy()
    query["as_of"] = pd.to_datetime(query["as_of"])
    merged = pd.merge_asof(query.sort_values("as_of"), events.rename(columns={"first_any": "as_of"})[["site", "as_of", "_n"]],
                           on="as_of", by="site", direction="backward")
    n_all = np.zeros(len(work))
    n_all[merged["_row"].to_numpy()] = merged["_n"].fillna(0).to_numpy()
    n_all = n_all - own_before("first_seen")

    own_pages = pos.merge(work[["site", "keyword_id", "as_of", "_row"]], on=["site", "keyword_id"])
    own_pages = own_pages[[s <= a for s, a in zip(own_pages["snapshot_date"], own_pages["as_of"])]]
    pages_n = np.zeros(len(work))
    last_rank = np.full(len(work), np.nan)
    if len(own_pages):
        own_pages = own_pages.sort_values(["_row", "snapshot_date"])
        counts = own_pages.groupby("_row").size()
        pages_n[counts.index.to_numpy()] = counts.to_numpy()
        last = own_pages.groupby("_row")["rank100"].last()
        last_rank[last.index.to_numpy()] = last.to_numpy(dtype=float)

    out["hist_searches_locale"] = n_loc
    out["hist_top10_locale"] = n10
    out["hist_top50_locale"] = n50
    with np.errstate(divide="ignore", invalid="ignore"):
        out["hist_share_top10_locale"] = np.where(n_loc > 0, n10 / np.maximum(n_loc, 1), np.nan)
    out["hist_searches_all"] = n_all
    out["hist_pages_this_search"] = pages_n
    out["hist_last_rank_this_search"] = last_rank
    return out


# ---------------------------------------------------------------------------
# page features


def _page_key(frame: pd.DataFrame) -> pd.Series:
    return frame["keyword_id"].astype(str) + "|" + pd.to_datetime(frame["snapshot_time"], utc=True).astype(str)


def page_feature_frame(
    page_rows: pd.DataFrame,
    page_meta: pd.DataFrame | None = None,
    *,
    measures: Sequence[str] = MODEL_MEASURES,
) -> pd.DataFrame:
    """What each page demands. One row per (keyword_id, snapshot_time). REAL inputs, nothing fitted.

    `page_rows` holds one row per site per page with keyword_id, snapshot_time,
    site, rank100 (best organic rank) and the strength `measures`, for EVERY page
    of each keyword that should count as history (churn and new entrants look at
    earlier pages of the same keyword in this frame only). `page_meta` may give
    kd, search_volume, intent and depth per page (the values recorded with that
    page, so knowable on its date).

    Columns: for each measure and band (t10 = positions 1 to 10, t11_20, t21_50)
    the 25th, 50th and 75th percentile (`pg_<measure>_<band>_q50`); distinct
    sites in the top 10 and top 50; the largest share of top-10 slots one site
    holds; the Herfindahl index of top-50 slots by site; difficulty, log volume,
    intent flags; the gap to the previous page of the keyword, the share of the
    previous top 10 and top 50 that left, and how many top-50 sites are new to
    the keyword (never on an earlier page of it in the frame). A first page has
    churn NaN, not 0.
    """
    rows = page_rows.copy()
    rows["_page"] = _page_key(rows)
    rows["snapshot_time"] = pd.to_datetime(rows["snapshot_time"], utc=True)
    pages = rows.groupby("_page", sort=False).agg(keyword_id=("keyword_id", "first"), snapshot_time=("snapshot_time", "first"))
    out = pd.DataFrame(index=pages.index)
    out["keyword_id"] = pages["keyword_id"]
    out["snapshot_time"] = pages["snapshot_time"]
    for band, low, high in PAGE_BANDS:
        inside = rows[(rows["rank100"] >= low) & (rows["rank100"] <= high)]
        grouped = inside.groupby("_page", sort=False)
        for measure in measures:
            if measure not in inside:
                continue
            q = grouped[measure].quantile(list(QUANTILES)).unstack()
            for quant in QUANTILES:
                name = f"pg_{measure}_{band}_q{int(quant * 100)}"
                out[name] = q[quant].reindex(out.index) if quant in q else np.nan
            out[f"pg_{measure}_{band}_known"] = grouped[measure].count().reindex(out.index).fillna(0) / max(1, high - low + 1)
    top10 = rows[rows["rank100"] <= 10]
    top50 = rows[rows["rank100"] <= TOP_DEPTH]
    out["pg_sites_top10"] = top10.groupby("_page")["site"].nunique().reindex(out.index).fillna(0)
    out["pg_sites_top50"] = top50.groupby("_page")["site"].nunique().reindex(out.index).fillna(0)
    if "slots" in rows:
        slots10 = top10.groupby(["_page", "site"])["slots"].sum()
    else:
        slots10 = top10.groupby(["_page", "site"]).size()
    out["pg_top10_max_site_share"] = (slots10.groupby(level=0).max() / slots10.groupby(level=0).sum()).reindex(out.index)
    slots50 = top50.groupby(["_page", "site"]).size()
    share50 = slots50 / slots50.groupby(level=0).transform("sum")
    out["pg_top50_hhi"] = (share50 ** 2).groupby(level=0).sum().reindex(out.index)
    out["pg_depth"] = rows.groupby("_page")["rank100"].max().reindex(out.index)

    if page_meta is not None and len(page_meta):
        meta = page_meta.copy()
        meta["_page"] = _page_key(meta)
        meta = meta.drop_duplicates("_page").set_index("_page")
        out["pg_kd"] = pd.to_numeric(meta.get("kd"), errors="coerce").reindex(out.index) if "kd" in meta else np.nan
        volume = pd.to_numeric(meta.get("search_volume"), errors="coerce").reindex(out.index) if "search_volume" in meta else np.nan
        out["pg_log_volume"] = np.log10(1.0 + volume) if not isinstance(volume, float) else np.nan
        intent = meta["intent"].reindex(out.index) if "intent" in meta else pd.Series(index=out.index, dtype=object)
        for name in INTENTS:
            out[f"pg_intent_{name}"] = np.where(intent.isna(), np.nan, (intent == name).astype(float))
        if "depth" in meta:
            out["pg_depth"] = pd.to_numeric(meta["depth"], errors="coerce").reindex(out.index).fillna(out["pg_depth"])
    else:
        out["pg_kd"] = np.nan
        out["pg_log_volume"] = np.nan
        for name in INTENTS:
            out[f"pg_intent_{name}"] = np.nan

    # Churn against the previous page of the same keyword.
    order = out[["keyword_id", "snapshot_time"]].sort_values(["keyword_id", "snapshot_time"])
    order["prev_page"] = order.groupby("keyword_id")["snapshot_time"].shift(1)
    prev_key = order["keyword_id"].astype(str) + "|" + order["prev_page"].astype(str)
    prev_key[order["prev_page"].isna()] = None
    out["_prev"] = prev_key.reindex(out.index)
    gap = (order["snapshot_time"] - order["prev_page"]).dt.total_seconds() / 86400.0
    out["pg_gap_from_previous_days"] = gap.reindex(out.index)
    sets10 = top10.groupby("_page")["site"].agg(lambda s: frozenset(s))
    sets50 = top50.groupby("_page")["site"].agg(lambda s: frozenset(s))
    churn10, churn50 = [], []
    for page, prev in zip(out.index, out["_prev"]):
        if prev is None or not isinstance(prev, str) or prev not in sets50.index:
            churn10.append(np.nan)
            churn50.append(np.nan)
            continue
        old10 = sets10.get(prev, frozenset())
        old50 = sets50.get(prev, frozenset())
        new10 = sets10.get(page, frozenset())
        new50 = sets50.get(page, frozenset())
        churn10.append(len(old10 - new10) / len(old10) if old10 else np.nan)
        churn50.append(len(old50 - new50) / len(old50) if old50 else np.nan)
    out["pg_churn_top10"] = churn10
    out["pg_churn_top50"] = churn50
    seen_first = rows.groupby(["keyword_id", "site"])["snapshot_time"].transform("min")
    is_new = (rows["snapshot_time"] == seen_first) & (rows["rank100"] <= TOP_DEPTH)
    first_page = rows.groupby("keyword_id")["snapshot_time"].transform("min") == rows["snapshot_time"]
    entrants = rows[is_new & ~first_page].groupby("_page").size()
    out["pg_new_entrants_top50"] = entrants.reindex(out.index).fillna(0.0)
    out.loc[out["_prev"].isna(), "pg_new_entrants_top50"] = np.nan
    return out.drop(columns="_prev")


def page_features(
    page: pd.DataFrame,
    *,
    earlier_pages: pd.DataFrame | None = None,
    meta: Mapping[str, Any] | None = None,
    measures: Sequence[str] = MODEL_MEASURES,
) -> dict[str, Any]:
    """`page_feature_frame` for one page. `earlier_pages` are pages of the same keyword dated before it.

    `page` has site, rank100 (or rank), keyword_id, snapshot_time and the measures.
    Raises ValueError when an "earlier" page is not earlier: a later page must
    never shape a page's features.
    """
    current = page.copy()
    if "rank100" not in current and "rank" in current:
        current = current.rename(columns={"rank": "rank100"})
    moment = pd.to_datetime(current["snapshot_time"], utc=True).iloc[0]
    parts = [current]
    if earlier_pages is not None and len(earlier_pages):
        earlier = earlier_pages.copy()
        if "rank100" not in earlier and "rank" in earlier:
            earlier = earlier.rename(columns={"rank": "rank100"})
        if (pd.to_datetime(earlier["snapshot_time"], utc=True) >= moment).any():
            raise ValueError("earlier_pages holds a page dated on or after the page itself")
        parts.append(earlier)
    rows = pd.concat(parts, ignore_index=True)
    meta_frame = None
    if meta:
        meta_frame = pd.DataFrame([{**meta, "keyword_id": current["keyword_id"].iloc[0], "snapshot_time": moment}])
    table = page_feature_frame(rows, meta_frame, measures=measures)
    key = f"{current['keyword_id'].iloc[0]}|{moment}"
    return table.loc[key].to_dict()


# ---------------------------------------------------------------------------
# site and cell features


def site_features(
    sites: Sequence[str],
    as_of: date | str,
    *,
    sources: Sequence[Any] | None = None,
    evidence: StartEvidence | None = None,
    positions: pd.DataFrame | None = None,
    locale: str | None = None,
    root: str | Path | None = None,
) -> pd.DataFrame:
    """Per site, as of one date: dated strength (REAL, guarded), start date (PREDICTED), panel history (REAL).

    Panel history needs `positions` (site_positions rows) and `locale`; it uses
    pages dated on or before `as_of` only. Returns one row per site, indexed by site.
    """
    day = as_date(as_of)
    frame = pd.DataFrame({"site": list(sites), "as_of": [day] * len(sites)})
    strength = strength_for_dates(frame, sources=sources, root=root).set_index("site")
    out = strength.drop(columns=["as_of"]).add_prefix("st_")
    ev = evidence if evidence is not None else StartEvidence.load(sites, root)
    start = start_features_frame(frame, ev)
    start.index = frame["site"]
    out = out.join(start)
    if positions is not None and locale is not None:
        cells = frame.assign(locale=locale, keyword_id="__none__")
        hist = history_features(cells, positions)
        hist.index = frame["site"]
        out = out.join(hist[["hist_searches_locale", "hist_top10_locale", "hist_top50_locale",
                             "hist_share_top10_locale", "hist_searches_all"]])
    return out


def leave_one_out_quantile(values: np.ndarray, others_sorted: np.ndarray, own_inside: bool) -> float:
    """Share of the page's other sites weaker than `values` (ties count half), with the site itself left out."""
    value = float(values)
    if not math.isfinite(value):
        return math.nan
    arr = others_sorted
    less = float(np.searchsorted(arr, value, side="left"))
    leq = float(np.searchsorted(arr, value, side="right"))
    ties = leq - less
    total = float(len(arr))
    if own_inside:
        ties -= 1.0
        total -= 1.0
    if total <= 0:
        return math.nan
    return (less + 0.5 * max(ties, 0.0)) / total


def cell_strength_quantiles(
    cells: pd.DataFrame,
    page_rows: pd.DataFrame,
    *,
    measures: Sequence[str] = MODEL_MEASURES,
    value_cols: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """For each cell, the site's strength as a quantile of the page's top 10, 20 and 50, site left out.

    `cells` has `_page` (keyword_id|snapshot_time of the INPUT page), site,
    `cell_rank_in` (the site's best rank on that page or NaN) and one column per
    measure with the site's own value (named by `value_cols[measure]`, default the
    measure name). `page_rows` holds the page's rows with `_page`, site, rank100
    and the measures. A missing own value gives NaN. Columns:
    `cl_<measure>_q_<top>` and `cl_<measure>_gap_<top>` (own value minus the
    median of the other sites in that top).
    """
    names = dict(value_cols or {})
    out = pd.DataFrame(index=cells.index)
    columns: dict[str, np.ndarray] = {}
    for measure in measures:
        for top, _ in CELL_TOPS:
            columns[f"cl_{measure}_q_{top}"] = np.full(len(cells), np.nan)
            columns[f"cl_{measure}_gap_{top}"] = np.full(len(cells), np.nan)
    by_page = {key: grp for key, grp in page_rows.groupby("_page", sort=False)}
    positions = {key: idx for key, idx in cells.groupby("_page", sort=False).indices.items()}
    for key, idx in positions.items():
        page = by_page.get(key)
        if page is None:
            continue
        ranks = page["rank100"].to_numpy(dtype=float)
        page_sites = page["site"].to_numpy()
        sub = cells.iloc[idx]
        own_rank = pd.to_numeric(sub["cell_rank_in"], errors="coerce").to_numpy(dtype=float)
        sub_sites = sub["site"].to_numpy()
        for measure in measures:
            vals = page[measure].to_numpy(dtype=float)
            own_vals = pd.to_numeric(sub[names.get(measure, measure)], errors="coerce").to_numpy(dtype=float)
            for top, depth in CELL_TOPS:
                mask = (ranks <= depth) & np.isfinite(vals)
                arr = np.sort(vals[mask])
                in_sites = set(page_sites[mask])
                qcol = columns[f"cl_{measure}_q_{top}"]
                gcol = columns[f"cl_{measure}_gap_{top}"]
                for j, row in enumerate(idx):
                    inside = bool(own_rank[j] <= depth) and sub_sites[j] in in_sites
                    value = own_vals[j]
                    qcol[row] = leave_one_out_quantile(value, arr, inside)
                    if math.isfinite(value):
                        others = arr
                        if inside:
                            pos = int(np.searchsorted(arr, value, side="left"))
                            others = np.delete(arr, min(pos, len(arr) - 1)) if len(arr) else arr
                        gcol[row] = value - float(np.median(others)) if len(others) else math.nan
    for name, values in columns.items():
        out[name] = values
    return out


def title_content_scores(
    searches: Sequence[str],
    titles: Sequence[Any],
    scorer: Any,
) -> tuple[np.ndarray, str, str]:
    """Content match of each title against its search, title only. PREDICTED. Missing title gives NaN.

    The description is left out on purpose: Google writes it for the search, so it
    is not knowable at upload. Returns (scores, backend mode, model name).
    """
    from .content import PageText, as_text

    values = np.full(len(titles), np.nan)
    items: list[tuple[str, Any]] = []
    where: list[int] = []
    for i, (search, title) in enumerate(zip(searches, titles)):
        text = as_text(title).strip()
        if text:
            items.append((str(search), PageText(title=" ".join(text.split()))))
            where.append(i)
    if items:
        scores = scorer.score_many(items)
        for i, score in zip(where, scores):
            values[i] = np.nan if score.value is None else float(score.value)
    return values, str(scorer.mode), str(scorer.model_name)


def cell_features(
    site: str,
    search: str,
    as_of: date | str,
    page: pd.DataFrame,
    *,
    site_values: Mapping[str, float],
    scorer: Any | None = None,
    measures: Sequence[str] = MODEL_MEASURES,
) -> dict[str, Any]:
    """Site against page, for one cell. The page must be dated on or before `as_of`.

    `page` holds the input page's rows (site, rank100, title, snapshot_time and the
    measures). `site_values` holds the site's own measures as of `as_of`. The
    title content score uses the site's best result title on the page (missing
    when the site has no result there) and names the backend that ran.
    """
    day = as_date(as_of)
    moment = pd.to_datetime(page["snapshot_time"], utc=True)
    if (moment.dt.date > day).any():
        raise ValueError(f"the input page is dated after the forecast date {day}")
    rows = page.copy()
    if "rank100" not in rows and "rank" in rows:
        rows = rows.rename(columns={"rank": "rank100"})
    rows["_page"] = "p"
    own = rows[rows["site"] == site].sort_values("rank100")
    rank_in = float(own["rank100"].iloc[0]) if len(own) else math.nan
    cell = pd.DataFrame([{"_page": "p", "site": site, "cell_rank_in": rank_in, **{m: site_values.get(m, math.nan) for m in measures}}])
    table = cell_strength_quantiles(cell, rows, measures=measures).iloc[0].to_dict()
    table["cell_rank_in"] = rank_in
    if scorer is not None:
        title = own["title"].iloc[0] if len(own) and "title" in own else None
        values, mode, model = title_content_scores([search], [title], scorer)
        table["cl_title_content"] = float(values[0])
        table["content_backend"] = mode
        table["content_model"] = model
    return table


# ---------------------------------------------------------------------------
# frame builders


@dataclass(frozen=True)
class PanelFrameSpec:
    """What the panel frame builder samples. Every field goes into the cache key and the manifest."""

    searches: int = 2000
    seed: int = DEFAULT_SEED
    window_days: tuple[float, float] = DEFAULT_WINDOW_DAYS
    datasets: tuple[str, ...] = ("dfs_history",)
    locales: tuple[str, ...] | None = None
    content_backend: str = "lexical"
    version: str = FEATURES_VERSION

    def key(self) -> str:
        return sha256_text(canonical_json(asdict(self)))[:16]


PANEL_ROW_COLUMNS: tuple[str, ...] = ("dataset", "locale", "keyword", "keyword_id", "snapshot_time", "snapshot_date", "rank",
                                      "site", "title", "kd", "search_volume", "intent", "main_domain_rank")


def sample_keywords(keywords: pd.DataFrame, count: int, seed: int) -> list[str]:
    """A seeded sample of keyword_ids, by sha256(seed|keyword_id) order. Deterministic for a given set."""
    ids = sorted(set(keywords["keyword_id"]))
    ranked = sorted(ids, key=lambda k: stable_share(k, seed))
    return sorted(ranked[: max(0, int(count))]) if count < len(ids) else ids


def prepare_page_rows(panel_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(site rows, page meta) from panel-shaped rows. Site rows: best rank and its title per site per page."""
    rows = panel_rows.copy()
    rows["snapshot_time"] = pd.to_datetime(rows["snapshot_time"], utc=True)
    rows = rows[~rows["site"].isin(EXCLUDED_SITES)]
    depth = rows.groupby(["keyword_id", "snapshot_time"], sort=False)["rank"].transform("max")
    rows = rows.assign(depth=depth.astype(int))
    rows = rows.sort_values(["keyword_id", "snapshot_time", "rank"], kind="mergesort")
    slots = rows.groupby(["keyword_id", "snapshot_time", "site"], sort=False).size().rename("slots")
    best = rows.drop_duplicates(["keyword_id", "snapshot_time", "site"], keep="first").rename(columns={"rank": "rank100"})
    best = best.join(slots, on=["keyword_id", "snapshot_time", "site"])
    meta_cols = [c for c in ("dataset", "locale", "keyword", "keyword_id", "snapshot_time", "snapshot_date", "kd",
                             "search_volume", "intent", "depth") if c in best.columns]
    meta = best[meta_cols].drop_duplicates(["keyword_id", "snapshot_time"]).reset_index(drop=True)
    if "snapshot_date" not in best:
        best["snapshot_date"] = best["snapshot_time"].dt.date
        meta["snapshot_date"] = meta["snapshot_time"].dt.date
    return best.reset_index(drop=True), meta


def page_pairs(meta: pd.DataFrame, window_days: tuple[float, float]) -> pd.DataFrame:
    """Every (input page a, outcome page b) of one keyword with a before b and the gap inside the window."""
    pages = meta[["keyword_id", "snapshot_time", "depth"]].sort_values(["keyword_id", "snapshot_time"])
    left = pages.rename(columns={"snapshot_time": "input_time", "depth": "depth_input"})
    right = pages.rename(columns={"snapshot_time": "outcome_time", "depth": "depth_outcome"})
    pairs = left.merge(right, on="keyword_id")
    pairs["gap_days"] = (pairs["outcome_time"] - pairs["input_time"]).dt.total_seconds() / 86400.0
    low, high = window_days
    return pairs[(pairs["gap_days"] >= low) & (pairs["gap_days"] <= high)].reset_index(drop=True)


def build_cells(site_rows: pd.DataFrame, meta: pd.DataFrame, window_days: tuple[float, float]) -> pd.DataFrame:
    """One row per (site, input page, outcome page): every site in the top 100 of either page. REAL positions.

    `position_out` and `position_in` are 1 to 50 or NT50 (51, also when absent);
    `rank100_*` is the best rank as given (NaN when absent). `depth_ok` is True
    only when both pages reach rank 50, so absence really means NT50.
    """
    pairs = page_pairs(meta, window_days)
    if pairs.empty:
        return pd.DataFrame()
    side = site_rows[["keyword_id", "snapshot_time", "site", "rank100"]]
    at_in = pairs.merge(side.rename(columns={"snapshot_time": "input_time", "rank100": "rank100_in"}), on=["keyword_id", "input_time"])
    at_out = pairs.merge(side.rename(columns={"snapshot_time": "outcome_time", "rank100": "rank100_out"}), on=["keyword_id", "outcome_time"])
    keys = ["keyword_id", "input_time", "outcome_time", "gap_days", "depth_input", "depth_outcome", "site"]
    cells = at_in[keys + ["rank100_in"]].merge(at_out[keys + ["rank100_out"]], on=keys, how="outer")
    for name in ("in", "out"):
        rank = cells[f"rank100_{name}"].astype(float)
        cells[f"position_{name}"] = np.where(rank <= TOP_DEPTH, rank, NT50).astype(int)
    cells["depth_ok"] = (cells["depth_input"] >= TOP_DEPTH) & (cells["depth_outcome"] >= TOP_DEPTH)
    info = meta.drop_duplicates("keyword_id").set_index("keyword_id")
    for col in ("dataset", "locale", "keyword"):
        if col in info:
            cells[col] = cells["keyword_id"].map(info[col])
    cells["as_of"] = cells["input_time"].dt.date
    cells["outcome_date"] = cells["outcome_time"].dt.date
    return cells.sort_values(["keyword_id", "input_time", "outcome_time", "site"]).reset_index(drop=True)


def assemble_frame(
    site_rows: pd.DataFrame,
    meta: pd.DataFrame,
    *,
    window_days: tuple[float, float] = DEFAULT_WINDOW_DAYS,
    strength: Callable[[pd.DataFrame], pd.DataFrame],
    evidence: StartEvidence | None,
    scorer: Any | None,
    label: str,
    extra_cells: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The shared frame from page rows: cells, page features of the input page, site and cell features.

    `strength` maps a (site, as_of) frame to the same frame with the MEASURES added
    (the real builder passes `strength_for_dates`, the synthetic one its own
    lists). Page features use the strength as of each page's own date. Site
    features use the strength as of the cell's `as_of` (the input page's date).
    `extra_cells` (synthetic only) adds cells for sites with no result on either page.
    """
    cells = build_cells(site_rows, meta, window_days)
    if extra_cells is not None and len(extra_cells):
        cells = pd.concat([cells, extra_cells], ignore_index=True)
    if cells.empty:
        return cells
    rows = site_rows.copy()
    rows["as_of"] = rows["snapshot_time"].dt.date
    page_strength = strength(rows[["site", "as_of"]].drop_duplicates())
    rows = rows.merge(page_strength[["site", "as_of", *MEASURES]], on=["site", "as_of"], how="left")
    pages = page_feature_frame(rows, meta)
    cells["_page"] = cells["keyword_id"].astype(str) + "|" + cells["input_time"].astype(str)
    rows["_page"] = _page_key(rows)
    frame = cells.merge(pages.drop(columns=["keyword_id", "snapshot_time"]), left_on="_page", right_index=True, how="left")

    site_dates = frame[["site", "as_of"]].drop_duplicates().reset_index(drop=True)
    st = strength(site_dates)
    st_cols = [c for c in st.columns if c not in ("site", "as_of")]
    st = st.rename(columns={c: f"st_{c}" for c in st_cols})
    frame = frame.merge(st, on=["site", "as_of"], how="left")
    if evidence is not None:
        sd = start_features_frame(site_dates, evidence)
        sd["site"] = site_dates["site"].to_numpy()
        sd["as_of"] = site_dates["as_of"].to_numpy()
        frame = frame.merge(sd, on=["site", "as_of"], how="left")
    else:
        for col in START_COLUMNS:
            frame[col] = np.nan

    positions = rows[["locale", "keyword_id", "site", "snapshot_date", "rank100"]] if "locale" in rows else None
    if positions is not None:
        hist = history_features(frame[["site", "locale", "keyword_id", "as_of"]], positions)
        frame = pd.concat([frame, hist], axis=1)

    frame["cell_rank_in"] = frame["rank100_in"]
    quant = cell_strength_quantiles(frame, rows, value_cols={m: f"st_{m}" for m in MODEL_MEASURES})
    frame = pd.concat([frame, quant], axis=1)

    titles = rows.set_index(["_page", "site"])["title"] if "title" in rows else None
    if scorer is not None and titles is not None:
        keyed = list(zip(frame["_page"], frame["site"]))
        title_map = titles[~titles.index.duplicated()].to_dict()
        frame_titles = [title_map.get(k) for k in keyed]
        values, mode, model = title_content_scores(frame["keyword"].astype(str).tolist(), frame_titles, scorer)
        frame["cl_title_content"] = values
        frame["content_backend"] = mode
        frame["content_model"] = model
    else:
        frame["cl_title_content"] = np.nan
        frame["content_backend"] = None
        frame["content_model"] = None
    frame["search_group"] = [search_group(k) for k in frame["keyword"].astype(str)]
    frame["label"] = label
    frame["features_version"] = FEATURES_VERSION
    return frame.drop(columns=["_page"]).reset_index(drop=True)


def default_frame_dir() -> Path:
    return forecast_paths().cache / "features"


def build_panel_frame(
    spec: PanelFrameSpec = PanelFrameSpec(),
    *,
    panel_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    sources: Sequence[Any] | None = None,
    root: str | Path | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Materialise the shared frame for a seeded sample of panel searches to parquet. REAL rows.

    Every row carries `as_of` (the input page's date). Writes
    `<cache>/features/panel_frame_<key>.parquet` and a manifest JSON beside it,
    and returns the parquet path. The sample is `spec.searches` keyword_ids by
    sha256(seed|keyword_id) order.
    """
    from .content import ContentScorer
    from .sources import panel as panel_mod

    keys = panel_mod.load_panel(["keyword_id"], locales=spec.locales, datasets=list(spec.datasets), panel_dir=panel_dir)
    chosen = sample_keywords(keys.drop_duplicates(), spec.searches, spec.seed)
    del keys
    rows = panel_mod.load_panel(list(PANEL_ROW_COLUMNS) + [panel_mod.POISON_RISK_COLUMN], locales=spec.locales,
                                datasets=list(spec.datasets), panel_dir=panel_dir)
    rows = rows[rows["keyword_id"].isin(set(chosen))]
    rows = rows[~rows[panel_mod.POISON_RISK_COLUMN].fillna(False).astype(bool)]
    if log:
        log(f"{len(chosen)} searches, {len(rows)} rows")
    site_rows, meta = prepare_page_rows(rows)
    evidence = StartEvidence.load(sorted(set(site_rows["site"])), root)
    scorer = ContentScorer(spec.content_backend) if spec.content_backend else None

    def strength(site_dates: pd.DataFrame) -> pd.DataFrame:
        return strength_for_dates(site_dates, sources=sources, root=root, log=log)

    frame = assemble_frame(site_rows, meta, window_days=spec.window_days, strength=strength, evidence=evidence,
                           scorer=scorer, label=REAL)
    out = Path(out_dir) if out_dir else default_frame_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"panel_frame_{spec.key()}.parquet"
    frame.to_parquet(path, index=False)
    manifest = {
        "label": REAL, "spec": asdict(spec), "key": spec.key(), "rows": len(frame), "searches": len(chosen),
        "columns": list(frame.columns), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "note": ("Every cell exists because the site appeared on the search at least once. Upload-day cells for "
                 "searches a site never ranks for are not in the panel."),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.with_suffix(".json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# synthetic frame


SYNTHETIC_FOOTPRINT_COLUMNS: tuple[str, ...] = ("fp_referring_domains", "fp_brand_search_volume", "fp_review_count",
                                                "fp_review_rating", "fp_links_planned", "fp_press_planned", "fp_pages_at_launch")


def synthetic_panel_rows(world: Any, months: Sequence[date] | None = None) -> pd.DataFrame:
    """Panel-shaped rows from `World.monthly_snapshots`. SYNTHETIC."""
    from .sources import dfs_samples as dfs

    locale = "SYN_en"
    records = []
    month = world.start if months is None else None
    todo = list(months) if months is not None else []
    if months is None:
        while month < world.end:
            todo.append(month)
            month = add_months(month, 1)
    for month in todo:
        for pull in world.monthly_snapshots(month):
            moment = datetime.fromisoformat(pull.provider_datetime.replace("Z", "+00:00"))
            info = world.searches[world.search_index(pull.spec.search)]
            for hit in pull.hits:
                records.append({
                    "dataset": "synthetic_historical", "locale": locale, "keyword": pull.spec.search,
                    "keyword_id": dfs.keyword_id(locale, pull.spec.search), "snapshot_time": moment,
                    "snapshot_date": moment.date(), "rank": hit.rank, "site": hit.site, "title": hit.title,
                    "kd": float(info.difficulty), "search_volume": float(info.volume), "intent": None,
                    "main_domain_rank": np.nan,
                })
    return pd.DataFrame.from_records(records, columns=list(PANEL_ROW_COLUMNS))


def synthetic_strength(world: Any) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """A strength function over the world's dated strength lists. SYNTHETIC.

    The list value goes into every CC-shaped measure (one synthetic link measure,
    so the columns match the real frame); sites missing from a published list get
    the floor, as real absent sites do. The list picked is the newest PUBLISHED on
    or before as_of, through an AsOfGuard.
    """
    published: dict[date, dict[str, Dated]] = {}
    month = add_months(world.start, world.knobs.strength_data_lag_months)
    while month < world.end:
        try:
            lists = world.strength_list(month)
        except ValueError:
            month = add_months(month, 1)
            continue
        day = month + timedelta(days=world.knobs.strength_publish_day - 1)
        published[day] = lists
        month = add_months(month, 1)
    days = sorted(published)

    def strength(site_dates: pd.DataFrame) -> pd.DataFrame:
        out = site_dates[["site", "as_of"]].copy()
        values = np.full(len(out), np.nan)
        for i, (site, when) in enumerate(zip(out["site"], out["as_of"])):
            day = as_date(when)
            eligible = [d for d in days if d <= day]
            if not eligible:
                continue
            lists = published[eligible[-1]]
            guard = AsOfGuard(day)
            dated = lists.get(site)
            if dated is None:
                values[i] = -CC_ABSENT_POS_LOG10
            else:
                values[i] = float(guard.take(dated))
        for col in RAW_STRENGTH_KEYS:
            out[col] = np.nan
        for name in MEASURES:
            out[name] = np.nan
        out["s_cc_hc"] = values
        out["s_cc_pr"] = values
        out["s_cc_hosts"] = values
        return out

    return strength


def synthetic_upload_cells(world: Any, site_rows: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """Upload-day cells: every new site on each target search, as of its launch date. SYNTHETIC truth outcome.

    The input page is the newest synthetic page of the search dated on or before
    launch. The outcome is `World.true_position` on launch day plus 90.
    """
    from .sources import dfs_samples as dfs

    records = []
    page_times = meta.groupby("keyword_id")["snapshot_time"].apply(lambda s: np.sort(s.to_numpy()))
    rank_lookup = site_rows.set_index(["keyword_id", "snapshot_time", "site"])["rank100"]
    rank_lookup = rank_lookup[~rank_lookup.index.duplicated()].to_dict()
    for site, info in sorted(world.new_sites.items()):
        outcome_day = info.launch_date + timedelta(days=90)
        if outcome_day >= world.end:
            continue
        for search in info.targets:
            kid = dfs.keyword_id("SYN_en", search)
            times = page_times.get(kid)
            if times is None:
                continue
            launch_moment = pd.Timestamp(datetime.combine(info.launch_date, datetime.max.time()), tz="UTC")
            before = [t for t in times if pd.Timestamp(t) <= launch_moment]
            if not before:
                continue
            input_time = pd.Timestamp(before[-1])
            rank_in = rank_lookup.get((kid, input_time, site))
            pos = world.true_position(site, search, outcome_day)
            records.append({
                "keyword_id": kid, "input_time": input_time, "outcome_time": pd.Timestamp(datetime.combine(outcome_day, datetime.min.time()), tz="UTC"),
                "gap_days": float((outcome_day - input_time.date()).days), "depth_input": 100, "depth_outcome": 100,
                "site": site, "rank100_in": rank_in if rank_in is not None else np.nan, "rank100_out": float(pos) if pos <= TOP_DEPTH else np.nan,
                "position_in": int(rank_in) if rank_in is not None and rank_in <= TOP_DEPTH else NT50, "position_out": int(pos),
                "depth_ok": True, "dataset": "synthetic_truth", "locale": "SYN_en", "keyword": search,
                "as_of": input_time.date(), "outcome_date": outcome_day, "upload_cell": True,
            })
    return pd.DataFrame.from_records(records)


def build_synthetic_frame(
    world: Any,
    *,
    window_days: tuple[float, float] = DEFAULT_WINDOW_DAYS,
    scorer: Any | None = None,
    upload_cells: bool = True,
) -> pd.DataFrame:
    """The same columns as the panel frame, from a synthetic world. SYNTHETIC, never reported as accuracy.

    Adds the cells the panel cannot have: each new site on its target searches as
    of launch day, with the true day-90 position (`upload_cell` True), and the
    agency-form footprint columns (`fp_*`) for new sites (None elsewhere).
    Start-date columns are NaN: synthetic sites have no Common Crawl evidence.
    """
    rows = synthetic_panel_rows(world)
    site_rows, meta = prepare_page_rows(rows)
    extra = synthetic_upload_cells(world, site_rows, meta) if upload_cells else None
    frame = assemble_frame(site_rows, meta, window_days=window_days, strength=synthetic_strength(world), evidence=None,
                           scorer=scorer, label=SYNTHETIC, extra_cells=extra)
    if "upload_cell" not in frame:
        frame["upload_cell"] = False
    frame["upload_cell"] = frame["upload_cell"].fillna(False).astype(bool)
    footprint = synthetic_footprint(world)
    frame = frame.merge(footprint, on="site", how="left")
    return frame


def synthetic_footprint(world: Any) -> pd.DataFrame:
    """Agency-form footprint per new site, as the form gives it (missing answers stay None). SYNTHETIC."""
    records = []
    for site in sorted(world.new_sites):
        form = world.agency_form(site)
        fp = form["footprint"]
        plan = form["launch_plan"]
        records.append({
            "site": site,
            "fp_referring_domains": _f(fp.get("referring_domains")),
            "fp_brand_search_volume": _f(fp.get("brand_search_volume")),
            "fp_review_count": _f(fp.get("review_count")),
            "fp_review_rating": _f(fp.get("review_rating")),
            "fp_links_planned": _f(plan.get("links_planned_count")),
            "fp_press_planned": _f(plan.get("press_planned")),
            "fp_pages_at_launch": _f(plan.get("pages_at_launch")),
        })
    return pd.DataFrame.from_records(records, columns=["site", *SYNTHETIC_FOOTPRINT_COLUMNS])


# ---------------------------------------------------------------------------
# CLI


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.features")
    sub = parser.add_subparsers(dest="command", required=True)
    panel_cmd = sub.add_parser("build-panel", help="materialise the shared frame for a seeded sample of panel searches")
    panel_cmd.add_argument("--searches", type=int, default=2000)
    panel_cmd.add_argument("--seed", type=int, default=DEFAULT_SEED)
    panel_cmd.add_argument("--window", type=float, nargs=2, default=list(DEFAULT_WINDOW_DAYS))
    panel_cmd.add_argument("--content", choices=["lexical", "cross_encoder", "none"], default="lexical")
    panel_cmd.add_argument("--locales", nargs="*", default=None)
    synth_cmd = sub.add_parser("build-synthetic", help="the same columns from a synthetic world (SYNTHETIC)")
    synth_cmd.add_argument("--seed", type=int, default=3)
    synth_cmd.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.command == "build-panel":
        spec = PanelFrameSpec(searches=args.searches, seed=args.seed, window_days=(float(args.window[0]), float(args.window[1])),
                              locales=tuple(args.locales) if args.locales else None,
                              content_backend="" if args.content == "none" else args.content)
        path = build_panel_frame(spec, log=_log)
        print(json.dumps({"path": str(path), "label": REAL}, indent=2))
        return 0
    from .synth import World

    world = World(args.seed)
    frame = build_synthetic_frame(world)
    out = Path(args.out) if args.out else default_frame_dir() / f"synthetic_frame_seed{args.seed}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    print(json.dumps({"path": str(out), "rows": len(frame), "label": SYNTHETIC}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
