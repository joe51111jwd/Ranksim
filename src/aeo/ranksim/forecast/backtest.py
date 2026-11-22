"""The backtest harness for markers 3 and 4, and the shared forecast entry point.

What is REAL here: positions on dated panel pages (single country-level pulls,
not clean readings), the real days between a forecast date and its outcome
page, and every hit rate computed against those positions. What is PREDICTED:
every forecast point, range, label and distribution. What is SYNTHETIC: every
number from `run_synthetic` (synth.World), which proves the machinery works and
is never accuracy.

Protections of docs/plan/PLAN_AUDIT.md section B, each enforced in code with a test:

- B1 a feature dated after the forecast date: `check_inputs_dated` raises
  asof.LeakError; strength values come through features.strength_for_dates
  (AsOfGuard keyed on publication date); readings after as_of are refused by
  updater.usable_readings.
- B5 and B7 a graded cell from the training side: `check_held_out` refuses any
  graded cell whose search group is a training group, whose site is a training
  site (SplitRule.site_is_test, the sites the trust model was fitted on), or
  whose forecast date is before the time cutoff. `backtest_report` computes its
  held-out counts from a per-cell flag and refuses a cell that is not held out;
  it never certifies cells it was not shown to be held out.
- Brand searches (the search contains the site's brand name, `is_brand_search`)
  are flagged on every graded cell, left out of the headline and shown apart.
- Intervals: the headline (which marker verdicts read) resamples sites, as
  IMPLEMENTATION_SPEC section 2 fixes; the search-clustered version is printed
  apart as `headline_search_clusters`.
- B8 single snapshots: every report says labels are single snapshots, stores
  the real days from forecast date to outcome page, and `refuse_pooling`
  refuses to mix backtest cells with real graded outcomes of any other source.
- An outcome page too shallow to say NT50: `outcome_position` raises
  ShallowOutcome when a site is absent from a page that stops before rank 50.
- Freeze before scoring: a `BacktestSpec` is hashed and written to
  evals/forecast/backtest/<name>/prereg.json (design version 1) or
  prereg-v<k>.json (version k, with the reason recorded inside) before any
  held-out outcome is read (`features.freeze_prereg` refuses to change a frozen
  design). A scored run refuses a dirty forecast source tree and a prereg that
  is not committed and pushed, and a design is scored once: a second scored run
  under the same prereg sha256 is refused. Reports and manifests are versioned
  files (report-v<k>.json) that are never overwritten; each manifest records the
  code revision, whether the source was dirty, and the sha256 of every forecast
  source file and of every model file loaded.

What the panel cannot give: searches a site targets but never ranks for. Every
panel cell exists because the site appeared on the search at least once, so an
upload-day forecast for a brand-new site (mostly NT50) cannot be validated on
it. The entrant test (C) is conditional on the site appearing. The synthetic
world has never-ranking cells with known truth; the paid pulls will supply real
ones in the same table shapes.

`forecast_cells` is the function the command line calls for a real site. The
backtest runs the same core (`forecast_frame`), so it tests the code that ships.

CLI:
  python -m aeo.ranksim.forecast.backtest prereg [--design-version k --reason "..."]   freeze the panel designs (A, B, C)
  python -m aeo.ranksim.forecast.backtest run [--design-version k]   score them once, write versioned reports
  python -m aeo.ranksim.forecast.backtest synthetic [--seeds 20]   SYNTHETIC end to end run and seed spread
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .asof import LeakError, as_date
from .config import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    CONFIRMED_STAGE,
    DIFFICULTY_TERCILES,
    LEAD_FLOOR_DAYS,
    MIN_COUNT_TO_PRINT_RATE,
    NT50,
    SOURCES,
    SPEC_BASELINES,
    STAGE_DAYS,
    TOP_DEPTH,
    forecast_paths,
)
from .features import (
    code_provenance,
    evals_dir,
    freeze_prereg,
    prereg_file,
    require_clean_source,
    require_prereg,
    require_prereg_committed,
    search_group,
    stable_share,
)
from .grading import (
    FINE_BANDS,
    RULE_V1,
    GradedCell,
    band_of,
    band_prior_points,
    fine_band_of,
    fit_band_prior,
    headline,
    summarize,
)
from .placement import PlacementModel, dist_median, fit_placement, point_diagnostics
from .scoreboard import PoolingRefused
from .types import PREDICTED, REAL, SYNTHETIC, Forecast
from .updater import (
    MODEL_FEATURES,
    Reading,
    Updater,
    UpdaterError,
    choose_version,
    fit_model,
    fit_table,
    prior_columns,
    reading_band,
    reading_columns,
)

BACKTEST_VERSION = "backtest-v1"
REPORT_NAME = "backtest"
SEED = 20261122
NUM_THREADS = 3
VALIDATION_SHARE = 0.20  # training search groups held out of every fit, for the updater choice and the range level
TRAIN_CELLS_MAX = 200_000  # training cells per fit, whole searches in sha256(seed|keyword_id) order
PRIOR_FOLDS = 3  # the prior forecast on training cells is cross-fitted, so the updater never learns an in-sample prior
STAND_IN_NOTE = ("These cells are a stand-in. They are sites already seen on a search, read on a single country-level "
                 "page, not brand-new domains at launch. A marker verdict here is about this stand-in only.")
PANEL_LIMIT_NOTE = ("The panel cannot give searches a site targets but never ranks for. Every cell exists because the site "
                    "appeared on the search at least once. An upload-day forecast for a brand-new site, which is mostly "
                    "not in the top 50, cannot be validated on it.")

# Features of the prior placement model on the panel (no reading of the cell's own search).
PANEL_PRIOR_FEATURES: tuple[str, ...] = (
    "own_score", "gap_top10", "gap_top20", "gap_top50", "q_top10", "q_top20", "q_top50", "demand_top10_q25",
    "demand_top10_q50", "demand_top10_q75", "demand_t11_20_q50", "demand_t21_50_q50", "trust_pred", "trust_gap_top10",
    "trust_q_top10", "st_s_cc_hc", "st_s_cc_pr", "st_s_cc_hosts", "st_s_crux", "st_s_tranco", "sd_age_days",
    "sd_left_censored", "sd_age_lower_bound_days", "pg_kd", "pg_log_volume", "pg_depth", "pg_sites_top10",
    "new_to_search", "lead_days",
)
# The model's features for entrants (C): what was knowable before the entrant's page.
ENTRANT_FEATURES: tuple[str, ...] = tuple(f for f in PANEL_PRIOR_FEATURES if f not in ("new_to_search",))


class BacktestRefusal(ValueError):
    """The harness refuses to grade or report something that would make the backtest lie."""


class ShallowOutcome(BacktestRefusal):
    """A site is missing from an outcome page that stops before rank 50: absence does not mean NT50 there."""


# ---------------------------------------------------------------------------
# the frozen spec


@dataclass(frozen=True)
class BacktestSpec:
    """Everything frozen before any held-out outcome is scored. Hashed into prereg.json."""

    name: str
    dataset: str
    split: Mapping[str, Any]
    time_cutoff: str
    windows_days: Mapping[str, Sequence[float]]
    feature_set: Sequence[str]
    model_versions: Mapping[str, str]
    baselines: Sequence[str]
    thresholds: Mapping[str, Any]
    cells: str
    version: str = BACKTEST_VERSION
    notes: Sequence[str] = ()

    def payload(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))

    def sha256(self) -> str:
        from .chain import canonical_json, sha256_text

        return sha256_text(canonical_json(self.payload()))


def backtest_dir(name: str, root: str | Path | None = None) -> Path:
    """evals/forecast/backtest/<name>/ (small evidence files, committed)."""
    base = Path(root) if root else evals_dir(REPORT_NAME)
    return base / name


def spec_prereg_file(spec: BacktestSpec, root: str | Path | None = None, design_version: int = 1) -> Path:
    """prereg.json (design version 1) or prereg-v<k>.json beside it."""
    return prereg_file(backtest_dir(spec.name, root), design_version)


def freeze_spec(spec: BacktestSpec, root: str | Path | None = None, *, design_version: int = 1, code: bool = False) -> str:
    """Write the prereg for the spec (or check an existing one is identical). Returns the sha256."""
    return freeze_prereg(spec_prereg_file(spec, root, design_version), spec.payload(), code=code)


def require_spec(spec: BacktestSpec, root: str | Path | None = None, *, design_version: int = 1) -> str:
    """The sha256 of the frozen spec. Raises when it is missing or differs: nothing is scored then."""
    return require_prereg(spec_prereg_file(spec, root, design_version), spec.payload())


def refuse_scored_twice(name: str, prereg_sha256: str, root: str | Path | None = None) -> None:
    """A design is scored once: refuse when a manifest under the same prereg sha256 exists in its folder."""
    folder = backtest_dir(name, root)
    for path in sorted(folder.glob("manifest-v*.json")):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if old.get("prereg_sha256") == prereg_sha256:
            raise BacktestRefusal(f"{path} already holds a scored run under prereg sha256 {prereg_sha256[:12]}. A test is "
                                  "scored once: freeze a new design version (with the reason) to score again.")


def next_version(folder: Path, stem: str = "report") -> int:
    """The next free run number in a folder: report-v1.json, report-v2.json, ... Old runs stay."""
    taken = [int(p.stem.split("-v")[-1]) for p in folder.glob(f"{stem}-v*.json") if p.stem.split("-v")[-1].isdigit()]
    return max(taken, default=0) + 1


def code_revision() -> str | None:
    """git HEAD, read only. HEAD alone does not say whether the code that ran was committed, so every manifest
    also records features.code_provenance() (dirty flag, changed files, sha256 of every forecast source file)."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def write_run(name: str, report: Mapping[str, Any], *, prereg_sha256: str, extra_manifest: Mapping[str, Any] | None = None,
              root: str | Path | None = None, scored_once: bool = False,
              provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write report-v<k>.json and manifest-v<k>.json for a new run. Never overwrites: a rerun gets the next k.

    With `scored_once` (every scored REAL run), a run under a prereg sha256 that already has a manifest is
    refused. The manifest records the code provenance (HEAD, dirty flag, changed files, source sha256).
    """
    folder = backtest_dir(name, root)
    folder.mkdir(parents=True, exist_ok=True)
    if scored_once:
        refuse_scored_twice(name, prereg_sha256, root)
    version = next_version(folder)
    report_path = folder / f"report-v{version}.json"
    if report_path.exists():
        raise BacktestRefusal(f"{report_path} exists. A run is never overwritten.")
    text = json.dumps(_clean(report), indent=1, sort_keys=True)
    with open(report_path, "x", encoding="utf-8") as handle:
        handle.write(text)
    prov = dict(provenance) if provenance is not None else code_provenance()
    manifest = {
        "name": name, "run_version": version, "report_file": report_path.name,
        "report_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "prereg_sha256": prereg_sha256,
        "code_revision": prov.get("code_revision"), "source_dirty": prov.get("source_dirty"),
        "dirty_files": prov.get("dirty_files"), "source_tree_sha256": prov.get("source_tree_sha256"),
        "source_sha256": prov.get("source_sha256"),
        "written_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "label": report.get("label"),
    }
    manifest.update(dict(extra_manifest or {}))
    with open(folder / f"manifest-v{version}.json", "x", encoding="utf-8") as handle:
        handle.write(json.dumps(_clean(manifest), indent=1, sort_keys=True))
    return manifest


# ---------------------------------------------------------------------------
# refusals


def check_inputs_dated(frame: pd.DataFrame, *, as_of_col: str = "as_of", date_cols: Sequence[str] = ("input_date",),
                       outcome_col: str | None = "outcome_date") -> None:
    """Raise LeakError when any input date is after the row's forecast date, or the outcome is not after it.

    Every column in `date_cols` holds the date of something a feature was built
    from (the input page, an earlier reading, a strength snapshot). A missing
    date is allowed (no such input); a later one is a leak.
    """
    if as_of_col not in frame:
        raise LeakError(f"rows have no {as_of_col}: a forecast without a date cannot be checked")
    as_of = pd.to_datetime(pd.Series([as_date(d) for d in frame[as_of_col]], index=frame.index))
    for col in date_cols:
        if col not in frame:
            continue
        when = pd.to_datetime(pd.Series([as_date(d) if _present(d) else None for d in frame[col]], index=frame.index))
        late = when.notna() & (when > as_of)
        if late.any():
            i = late.idxmax()
            raise LeakError(f"{col} {when[i].date()} is after the forecast date {as_of[i].date()} (row {i})")
    if outcome_col and outcome_col in frame:
        out = pd.to_datetime(pd.Series([as_date(d) for d in frame[outcome_col]], index=frame.index))
        bad = out <= as_of
        if bad.any():
            i = bad.idxmax()
            raise LeakError(f"the outcome {out[i].date()} is not after the forecast date {as_of[i].date()} (row {i})")


def _present(value: Any) -> bool:
    if value is None or value is pd.NaT:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def held_out_flags(frame: pd.DataFrame, split: Any, *, keyword_col: str = "keyword", as_of_col: str = "as_of",
                   site_col: str = "site", require_test_site: bool = True) -> np.ndarray:
    """True per row when the cell is held out on every side: a test search group, a forecast date on or after the
    cutoff and (with `require_test_site`) a test site, i.e. a site the trust model was never fitted on."""
    if len(frame) == 0:
        return np.zeros(0, dtype=bool)
    test_search = np.array([bool(split.search_is_test(str(k))) for k in frame[keyword_col]])
    late = np.array([as_date(d) >= split.cutoff for d in frame[as_of_col]])
    ok = test_search & late
    if require_test_site:
        if site_col not in frame:
            raise BacktestRefusal(f"the cells have no {site_col} column, so the site side of the split cannot be checked")
        ok &= np.array([bool(split.site_is_test(str(s))) for s in frame[site_col]])
    return ok


def check_held_out(frame: pd.DataFrame, split: Any, *, keyword_col: str = "keyword", as_of_col: str = "as_of",
                   site_col: str = "site", require_test_site: bool = True) -> None:
    """Refuse grading any cell whose search is a training search, whose site is a training site, or whose forecast
    date is before the cutoff (IMPLEMENTATION_SPEC section 6: only held-out sites are graded; PLAN_AUDIT B7)."""
    if len(frame) == 0:
        return
    bad = ~held_out_flags(frame, split, keyword_col=keyword_col, as_of_col=as_of_col, site_col=site_col,
                          require_test_site=require_test_site)
    if bad.any():
        raise BacktestRefusal(f"{int(bad.sum())} graded cells are from the training side (training search group, training "
                              f"site, or forecast date before {split.cutoff}). Only held-out cells are graded.")


def outcome_position(rank100: float | int | None, depth: float | int) -> int:
    """The outcome on a single page: 1 to 50, NT50 when deeper than 50 or absent from a page at least 50 deep.

    Raises ShallowOutcome for a site absent from a page that stops before rank 50.
    """
    if rank100 is not None and not (isinstance(rank100, float) and math.isnan(rank100)) and not pd.isna(rank100):
        rank = int(rank100)
        return rank if rank <= TOP_DEPTH else NT50
    if depth is None or pd.isna(depth) or int(depth) < TOP_DEPTH:
        raise ShallowOutcome(f"the site is absent from a page {depth} deep. That page cannot say NT50.")
    return NT50


def check_outcome_depth(frame: pd.DataFrame, *, rank_col: str = "rank100_out", depth_col: str = "depth_outcome") -> None:
    """Refuse cells whose NT50 outcome comes from absence on a page shallower than 50."""
    absent = frame[rank_col].isna() if rank_col in frame else pd.Series(False, index=frame.index)
    shallow = pd.to_numeric(frame[depth_col], errors="coerce").fillna(0) < TOP_DEPTH
    bad = absent & shallow
    if bad.any():
        raise ShallowOutcome(f"{int(bad.sum())} cells say NT50 from a page that stops before rank 50")


def refuse_pooling(cells: Sequence[GradedCell], source: str = "backtest") -> None:
    """Refuse a set of graded cells that mixes sources. Backtest cells are never pooled with real graded outcomes."""
    sources = {c.source for c in cells}
    if sources - {source}:
        raise PoolingRefused(f"these cells mix sources {sorted(str(s) for s in sources)}. "
                             f"Backtest results are reported apart and never pooled with real graded outcomes.")


def pool_with_real(*_args: Any, **_kwargs: Any) -> None:
    """There is no pooled view of backtest cells and real graded outcomes."""
    raise PoolingRefused("Backtest labels are single snapshots. They are never pooled with real graded outcomes.")


# ---------------------------------------------------------------------------
# the report, in the shape of report.stage_report


def _block(hits: Sequence[bool]) -> dict[str, Any]:
    count = len(hits)
    right = int(sum(1 for h in hits if h))
    return {"count": count, "right": right, "rate": right / count if count else None,
            "measurable": count >= MIN_COUNT_TO_PRINT_RATE}


def _group_blocks(cells: Sequence[GradedCell], key: Callable[[GradedCell], str], order: Sequence[str] = ()) -> dict[str, Any]:
    groups: dict[str, list[bool]] = defaultdict(list)
    for c in cells:
        groups[key(c)].append(RULE_V1.is_hit(c.point, c.actual))
    rank = {n: i for i, n in enumerate(order)}
    return {n: _block(groups[n]) for n in sorted(groups, key=lambda n: (rank.get(n, len(rank)), n))}


LEAD_BINS: tuple[tuple[str, int, int], ...] = (("0-27", 0, 27), ("28-44", 28, 44), ("45-59", 45, 59), ("60-74", 60, 74),
                                               ("75-89", 75, 89), ("90-104", 90, 104), ("105-130", 105, 130),
                                               ("131+", 131, 10 ** 9))


def lead_bin(days: int | None) -> str:
    if days is None:
        return "unknown"
    for name, low, high in LEAD_BINS:
        if low <= int(days) <= high:
            return name
    return "unknown"


def _eligibility(source: str, stage: str, graded: int) -> dict[str, dict[str, dict[str, Any]]]:
    table = {s: {st: {"made": 0, "due": 0, "graded": 0, "due_not_graded": 0, "not_yet_due": 0, "next_due_date": None}
                 for st in STAGE_DAYS} for s in SOURCES}
    table[source][stage].update({"made": graded, "due": graded, "graded": graded})
    return table


def _core_update_block(cells: Sequence[GradedCell], outcome_dates: Sequence[date], as_ofs: Sequence[date]) -> dict[str, Any]:
    from .sources import updates as upd

    try:
        calendar = upd.load_updates()
    except (OSError, ValueError):
        calendar = []
    if not cells:
        return {"recorded": [], "outcomes_during_any_update": _block([]), "outcomes_outside_updates": _block([])}
    lo, hi = min(as_ofs), max(outcome_dates)
    chosen = upd.overlaps(lo, hi, calendar, open_until=hi) if calendar else []
    hits = [RULE_V1.is_hit(c.point, c.actual) for c in cells]
    during_any = np.zeros(len(cells), dtype=bool)
    rows = []
    for item in chosen:
        start, end = item.start_date, item.end_date or hi
        inside = np.array([start <= d <= end for d in outcome_dates])
        spans = np.array([a <= end and d >= start for a, d in zip(as_ofs, outcome_dates)])
        during_any |= inside
        rows.append({"name": item.name, "kind": item.kind, "started_on": start.isoformat(),
                     "ended_on": item.end_date.isoformat() if item.end_date else None, "source_url": item.url,
                     "recorded_at": None, "outcomes_inside": _block([h for h, m in zip(hits, inside) if m]),
                     "forecast_spans_overlapping": int(spans.sum())})
    return {"recorded": rows, "outcomes_during_any_update": _block([h for h, m in zip(hits, during_any) if m]),
            "outcomes_outside_updates": _block([h for h, m in zip(hits, during_any) if not m])}


def backtest_report(
    cells: Sequence[GradedCell],
    baselines: Mapping[str, Sequence[int]],
    *,
    stage: str,
    as_of: date,
    model_version: str,
    outcome_dates: Sequence[date],
    as_ofs: Sequence[date],
    band_prior_training: Sequence[GradedCell] | None,
    source: str = "backtest",
    notes: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
    held_out: Sequence[bool] | None = None,
) -> dict[str, Any]:
    """A report in the shape of report.stage_report, for backtest (REAL) or synthetic (SYNTHETIC) cells.

    `held_out` is one flag per cell (held out on the search, date and site sides,
    `held_out_flags`). A cell flagged False is refused. Without flags the report
    does not certify anything: every cell is counted as unchecked (not_test), so
    the marker 3 check "only held out launches are graded" cannot pass.
    `lead_days` on each cell is the real days from the forecast date to the
    outcome page. Cells with a lead under the floor are left out of the headline,
    as stage_report does. Brand cells (`is_brand`) are left out of the headline
    and shown apart. The headline resamples SITES (IMPLEMENTATION_SPEC section 2;
    marker verdicts read it); `headline_search_clusters` resamples searches and is
    printed apart. `headline_site_clusters` is kept as an alias of the headline.
    """
    if source not in ("backtest", "synthetic"):
        raise PoolingRefused("backtest_report writes backtest or synthetic reports only, never real seed or pilot ones")
    refuse_pooling(cells, source)
    if not (len(cells) == len(outcome_dates) == len(as_ofs)):
        raise BacktestRefusal("one outcome date and one forecast date per cell are required")
    if held_out is not None:
        if len(held_out) != len(cells):
            raise BacktestRefusal("one held-out flag per cell is required")
        not_held = [i for i, flag in enumerate(held_out) if not bool(flag)]
        if not_held:
            raise BacktestRefusal(f"{len(not_held)} cells are not held out (training search, training site or a date before "
                                  "the cutoff). Only held-out cells are graded.")
    short = [i for i, c in enumerate(cells) if c.lead_days is None or c.lead_days < LEAD_FLOOR_DAYS]
    keep = [i for i in range(len(cells)) if i not in set(short)]
    main = [cells[i] for i in keep]
    base = {name: [int(points[i]) for i in keep] for name, points in baselines.items()}
    band_prior = None
    if band_prior_training is not None:
        training = list(band_prior_training)
        if source == "synthetic":
            overlap = sorted({c.site for c in training} & {c.site for c in main})
            if overlap:
                raise BacktestRefusal(f"band prior training sites overlap graded sites: {overlap[:3]}")
        band_prior = fit_band_prior(training)
        base["band_prior"] = band_prior_points(band_prior, main)
    head = headline(main, base, cluster_by="site")
    head_search = headline(main, base, cluster_by="search")
    missing = [name for name in SPEC_BASELINES if name not in head["baselines"]]
    persistence = base.get("persistence")
    by_lead: dict[str, Any] = {}
    groups: dict[str, list[int]] = defaultdict(list)
    for j, c in enumerate(main):
        if not c.is_brand:
            groups[lead_bin(c.lead_days)].append(j)
    for name in [b[0] for b in LEAD_BINS] + ["unknown"]:
        if name not in groups:
            continue
        idx = groups[name]
        block = _block([RULE_V1.is_hit(main[j].point, main[j].actual) for j in idx])
        if persistence is not None:
            block["persistence"] = _block([RULE_V1.is_hit(persistence[j], main[j].actual) for j in idx])
        by_lead[name] = block
    leads = [c.lead_days for c in main if c.lead_days is not None]
    label = SYNTHETIC if source == "synthetic" else REAL
    all_notes = list(notes)
    if held_out is None:
        held_block: dict[str, Any] = {"test": 0, "not_test": len(main), "unchecked": len(main)}
        all_notes.append("No per-cell held-out flags were given, so no graded cell is certified as held out.")
    else:
        held_block = {"test": len(main), "not_test": 0}
    brand_count = sum(1 for c in main if c.is_brand)
    all_notes.append(f"{brand_count} brand searches (the search contains the site's brand name) are left out of the "
                     "headline and shown apart.")
    if short:
        all_notes.append(f"{len(short)} graded rows have a lead under {LEAD_FLOOR_DAYS} days and are not called forecasts here ({label}).")
    if source == "backtest":
        all_notes.append("Backtest labels are single snapshots (one country-level page each), not clean readings.")
    else:
        all_notes.append("Synthetic results are a test of the code. They are never accuracy.")
    report: dict[str, Any] = {
        "kind": "stage_report", "source": source, "stage": stage, "confirmed_stage": stage == CONFIRMED_STAGE,
        "as_of": as_date(as_of).isoformat(), "model_version": model_version, "label": label,
        "chain": {"ok": None, "note": "Backtest cells are graded in memory. They are not stored in the scoreboard chain."},
        "grading_rule": RULE_V1.version, "grading_rule_hash": RULE_V1.rule_hash(), "lead_floor_days": LEAD_FLOOR_DAYS,
        "excluded_short_lead": len(short), "excluded_after_stage_day": 0, "baselines_required": list(SPEC_BASELINES),
        "baselines_missing": missing, "band_prior": band_prior, "minimum_count": MIN_COUNT_TO_PRINT_RATE,
        "headline": head, "headline_site_clusters": head, "headline_search_clusters": head_search,
        "eligibility": _eligibility(source, stage, len(main)),
        "by_difficulty": _group_blocks([c for c in main if not c.is_brand], lambda c: c.difficulty or "unknown",
                                       DIFFICULTY_TERCILES),
        "by_niche": _group_blocks([c for c in main if not c.is_brand], lambda c: c.niche or "unknown"),
        "by_lead_days": by_lead,
        "built_by": {source: len(main)},
        "held_out": held_block,
        "target_url": {"count": 0, "right": 0, "rate": None, "measurable": False, "forecasts_with_target_url": 0,
                       "position_unknown": 0},
        "core_updates": _core_update_block(main, [outcome_dates[i] for i in keep], [as_ofs[i] for i in keep]),
        "regrades": {"count": 0, "pairs": [], "note": "backtest cells are graded once"},
        "single_snapshot_labels": source == "backtest",
        "every_graded_cell_held_out": held_out is not None,
        "real_lead_days": {"count": len(leads), "min": min(leads) if leads else None,
                           "median": float(np.median(leads)) if leads else None, "max": max(leads) if leads else None},
        "notes": all_notes,
    }
    report["eligibility_this"] = report["eligibility"][source][stage]
    if extra:
        report.update(dict(extra))
    return report


BRAND_SHORT_LABEL = 3  # a derived site label shorter than this must match a whole word of the search


def _compact(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value).lower())


def site_label(site: str) -> str:
    """The site's own name, as the scoreboard derives it when no brand terms are registered: site.split('.')[0]."""
    return str(site).strip().lower().split(".")[0]


def is_brand_search(site: str, search_text: str, brand_terms: Sequence[str] = ()) -> bool:
    """True when the search contains the site's brand name (IMPLEMENTATION_SPEC section 2), by the scoreboard's rule:
    a registered brand term, or else the site label, compacted (letters and digits only) inside the compacted search.
    A derived label shorter than BRAND_SHORT_LABEL characters must be a whole word of the search ('hp laptop' for
    hp.com), so x.com is not a brand search on 'xbox'."""
    compact = _compact(search_text)
    registered = [t for t in (brand_terms or ()) if _compact(t)]
    if registered:
        return any(_compact(t) in compact for t in registered)
    label = _compact(site_label(site))
    if not label:
        return False
    if len(label) >= BRAND_SHORT_LABEL:
        return label in compact
    return label in re.split(r"[^0-9a-z]+", str(search_text).lower())


def graded_cells_from(frame: pd.DataFrame, points: Sequence[int], *, stage: str, source: str = "backtest",
                      niche_col: str = "locale", search_col: str = "keyword_id", text_col: str = "keyword",
                      brand_terms: Mapping[str, Sequence[str]] | None = None) -> list[GradedCell]:
    """GradedCell per row (site, search, point, REAL actual, real lead days, label, difficulty, niche, brand flag).

    `search_col` is the cluster key of the search; the brand flag is worked out from the search TEXT in `text_col`
    (the search key when there is no text column) and the site's registered brand terms, if any.
    """
    labels = frame["forecast_label"].tolist() if "forecast_label" in frame else [None] * len(frame)
    texts = frame[text_col].tolist() if text_col in frame else frame[search_col].tolist()
    terms = brand_terms or {}
    lows = frame["range_low"].tolist() if "range_low" in frame else [None] * len(frame)
    highs = frame["range_high"].tolist() if "range_high" in frame else [None] * len(frame)
    out = []
    for i, (site, search, actual, lead, diff, niche) in enumerate(zip(
            frame["site"], frame[search_col], frame["position_out"], frame["lead_days"], frame["difficulty"],
            frame[niche_col] if niche_col in frame else [None] * len(frame))):
        out.append(GradedCell(site=str(site), search=str(search), point=int(points[i]), actual=int(actual),
                              is_brand=is_brand_search(str(site), str(texts[i]), terms.get(str(site), ())),
                              difficulty=diff if isinstance(diff, str) else None, label=labels[i], source=source,
                              stage=stage, lead_days=int(lead), range_low=None if lows[i] is None else int(lows[i]),
                              range_high=None if highs[i] is None else int(highs[i]),
                              niche=str(niche) if niche is not None else None))
    return out


# ---------------------------------------------------------------------------
# the forecast entry point shared by the backtest and live forecasting


@dataclass
class ForecastModels:
    """The models a forecast uses. `features` maps (site, search, as_of) to (feature row, input dates).

    The feature provider must return only values knowable on as_of, and the
    dates of the inputs it used; `forecast_frame` checks them.
    """

    placement: PlacementModel
    updater: Updater | None = None
    features: Callable[[str, str, date], tuple[dict[str, Any], dict[str, date | None]]] | None = None


INPUT_DATE_COLS: tuple[str, ...] = ("input_date", "prev_date", "strength_date", "reading_date", "form_date")


def forecast_frame(frame: pd.DataFrame, models: ForecastModels, *, stage: str) -> tuple[np.ndarray, list[Forecast]]:
    """The forecast core. One row per (site, search) with features, reading columns, lead_days and input dates.

    1. Every input date must be on or before the row's as_of (LeakError otherwise).
    2. The prior is the placement model's distribution.
    3. When the stage is not upload and an updater is given, the prior is updated with the readings.
    Returns the 51-outcome distributions and one types.Forecast per row (PREDICTED), with the input dates in notes.
    """
    if stage not in STAGE_DAYS:
        raise BacktestRefusal(f"the stage must be one of {', '.join(STAGE_DAYS)}")
    check_inputs_dated(frame, date_cols=INPUT_DATE_COLS, outcome_col=None)
    work = frame.reset_index(drop=True)
    prior = models.placement.distributions(work)
    dists = prior
    used = models.placement
    if stage != "upload" and models.updater is not None:
        if "reading_position" not in work:
            raise BacktestRefusal("a staged forecast needs the reading columns (updater.reading_columns)")
        dists = models.updater.distributions(work, prior)
    forecasts: list[Forecast] = []
    for i in range(len(work)):
        row = work.iloc[i]
        dates = {col: as_date(row[col]).isoformat() for col in INPUT_DATE_COLS if col in work and _present(row[col])}
        notes = {"input_dates": dates, "prior_model": used.model_version(), "label": PREDICTED}
        if stage != "upload" and models.updater is not None:
            fc = models.updater.forecast(dists[i], site=str(row["site"]), search=str(row["search"]), stage=stage,
                                         as_of=as_date(row["as_of"]), notes=notes)
        else:
            fc = used.forecast(dists[i], site=str(row["site"]), search=str(row["search"]), stage=stage,
                               as_of=as_date(row["as_of"]), notes=notes)
        forecasts.append(fc)
    return dists, forecasts


def form_columns(form: Mapping[str, Any] | None) -> dict[str, float]:
    """fp_* columns from an agency form (agency_form layout: footprint, launch_plan). Missing answers stay NaN."""
    from .features import _f

    form = form or {}
    fp = form.get("footprint") or {}
    plan = form.get("launch_plan") or {}
    return {"fp_referring_domains": _f(fp.get("referring_domains")), "fp_brand_search_volume": _f(fp.get("brand_search_volume")),
            "fp_review_count": _f(fp.get("review_count")), "fp_review_rating": _f(fp.get("review_rating")),
            "fp_links_planned": _f(plan.get("links_planned_count")), "fp_press_planned": _f(plan.get("press_planned")),
            "fp_pages_at_launch": _f(plan.get("pages_at_launch"))}


def forecast_cells(
    site: str,
    searches: Sequence[str],
    as_of: date,
    stage: str,
    readings: Mapping[str, Sequence[Reading]] | None,
    form: Mapping[str, Any] | None,
    models: ForecastModels,
    *,
    outcome_date: date | None = None,
) -> list[Forecast]:
    """Forecasts for one site on its searches as of a date. The command line calls this for a real site.

    Inputs: the feature provider in `models` (page, strength, trust as of the
    date), the readings per search (refused when dated after as_of), the agency
    form, and the outcome date (launch plus 90 days) for the lead time. Every
    input's date is recorded in each forecast's notes. Output is PREDICTED.
    """
    if models.features is None:
        raise BacktestRefusal("forecast_cells needs a feature provider in models.features")
    day = as_date(as_of)
    rows = []
    fp = form_columns(form)
    for search in searches:
        values, dates = models.features(site, search, day)
        row: dict[str, Any] = {**fp, **dict(values), "site": site, "search": search, "as_of": day}
        for col, when in dates.items():
            row[col] = when
        got = list((readings or {}).get(search, ()))
        row.update(reading_columns(got, day))
        known = [r for r in got if r.position is not None]
        row["reading_date"] = max((as_date(r.taken_on) for r in known), default=None)
        lead = (as_date(outcome_date) - day).days if outcome_date is not None else row.get("lead_days")
        row["lead_days"] = float(lead) if lead is not None else math.nan
        rows.append(row)
    frame = pd.DataFrame(rows)
    _, forecasts = forecast_frame(frame, models, stage=stage)
    return forecasts


# ---------------------------------------------------------------------------
# panel data (REAL)


@dataclass(frozen=True)
class PanelDesign:
    """The panel design shared by tests A, B and C."""

    split_version: str = "strength-split-v1"
    windows_days: tuple[tuple[str, float, float], ...] = (("lead_28_74", 28.0, 74.999), ("lead_75_130", 75.0, 130.0))
    entrant_min_gap_days: float = 28.0
    entrant_max_gap_days: float = 130.0
    ceiling_max_gap_days: float = 2.0
    train_cells_max: int = TRAIN_CELLS_MAX
    validation_share: float = VALIDATION_SHARE
    seed: int = SEED


_ACTIVE_SPLIT: list[Any] = []


def _split():
    """The split in force: strength.DEFAULT_SPLIT, or the rehearsal split while a rehearsal runs."""
    from .strength import DEFAULT_SPLIT

    return _ACTIVE_SPLIT[-1] if _ACTIVE_SPLIT else DEFAULT_SPLIT


def rehearsal_pages(sp: pd.DataFrame, pages: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Only the TRAINING side of the real split (training searches, pages before the cutoff), re-split by the
    rehearsal split. Used to debug the run without reading any held-out outcome."""
    from .strength import DEFAULT_SPLIT, REHEARSAL_SPLIT

    keep = pages[(~pages["test_search"]) & (pages["date"] < DEFAULT_SPLIT.cutoff)].copy()
    keep["test_search"] = keep["keyword"].map({k: REHEARSAL_SPLIT.search_is_test(k) for k in set(keep["keyword"])}).astype(bool)
    rows = sp.merge(keep[["keyword_id", "page_idx"]], on=["keyword_id", "page_idx"])
    return rows, keep.reset_index(drop=True)


def panel_cache_dir() -> Path:
    return forecast_paths().cache / "backtest"


def strength_lookup(site_dates: pd.DataFrame, *, log: Callable[[str], None] | None = None) -> pd.DataFrame:
    """Dated strength per (site, as_of): the shared cache read only, the rest through features.strength_for_dates (guarded).

    The lookups this module adds are cached under data/forecast/cache/backtest/, never in the shared file.
    """
    from .features import MEASURES, RAW_STRENGTH_KEYS, default_strength_cache, strength_for_dates

    keep = ["site", "as_of", *RAW_STRENGTH_KEYS, *MEASURES]
    want = site_dates[["site", "as_of"]].drop_duplicates().copy()
    want["as_of"] = [as_date(d) for d in want["as_of"]]
    parts = []
    for path in (default_strength_cache(), panel_cache_dir() / "strength_extra.parquet"):
        if path.is_file():
            have = pd.read_parquet(path)
            have["as_of"] = [as_date(d) for d in have["as_of"]]
            parts.append(have[keep])
    have = pd.concat(parts, ignore_index=True).drop_duplicates(["site", "as_of"]) if parts else pd.DataFrame(columns=keep)
    merged = want.merge(have[["site", "as_of"]].assign(_hit=1), on=["site", "as_of"], how="left")
    missing = merged[merged["_hit"].isna()][["site", "as_of"]]
    if len(missing):
        if log:
            log(f"strength: {len(missing)} (site, date) pairs not cached, looking up")
        fresh = strength_for_dates(missing, log=log)[keep]
        from .features import merge_into_parquet

        merge_into_parquet(panel_cache_dir() / "strength_extra.parquet", fresh, keys=["site", "as_of"], columns=keep)
        have = pd.concat([have, fresh], ignore_index=True)
    return want.merge(have, on=["site", "as_of"], how="left")


def load_panel_pages(*, log: Callable[[str], None] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(site rows, pages) for the whole dfs_history panel. REAL. Poison-risk pages dropped.

    Site rows: one per (keyword_id, page time, site) with rank100, page depth,
    page index within the keyword, first page index of the site on the keyword,
    and the split side. Pages: one row per page with keyword, locale, date, depth, kd, volume, index.
    """
    from .sources import panel as panel_mod

    rows = panel_mod.load_panel(["locale", "keyword", "keyword_id", "snapshot_time", "snapshot_date", "rank", "site", "kd",
                                 "search_volume", panel_mod.POISON_RISK_COLUMN], datasets=["dfs_history"])
    meta = rows.drop_duplicates(["keyword_id", "snapshot_time"])[["keyword_id", "snapshot_time", "kd", "search_volume"]]
    sp = panel_mod.site_positions(rows, drop_poison_risk=True)
    del rows
    pages = sp.drop_duplicates(["keyword_id", "snapshot_time"])[
        ["keyword_id", "keyword", "locale", "snapshot_time", "snapshot_date", "snapshot_depth"]].copy()
    pages = pages.merge(meta, on=["keyword_id", "snapshot_time"], how="left")
    pages = pages.sort_values(["keyword_id", "snapshot_time"]).reset_index(drop=True)
    pages["page_idx"] = pages.groupby("keyword_id").cumcount().astype(int)
    pages["date"] = [as_date(d) for d in pages["snapshot_date"]]
    split = _split()
    sides = {k: split.search_is_test(k) for k in set(pages["keyword"])}
    pages["test_search"] = pages["keyword"].map(sides).astype(bool)
    groups = {k: search_group(k) for k in set(pages["keyword"])}
    pages["search_group"] = pages["keyword"].map(groups)
    sp = sp.merge(pages[["keyword_id", "snapshot_time", "page_idx"]], on=["keyword_id", "snapshot_time"], how="left")
    first = sp.groupby(["keyword_id", "site"])["page_idx"].min().rename("first_idx")
    sp = sp.join(first, on=["keyword_id", "site"])
    sp["date"] = [as_date(d) for d in sp["snapshot_date"]]
    if log:
        log(f"panel: {len(sp)} site rows on {len(pages)} pages, {pages['keyword_id'].nunique()} searches")
    return sp[["keyword_id", "snapshot_time", "date", "site", "rank100", "snapshot_depth", "page_idx", "first_idx"]], pages


def consecutive_gaps(pages: pd.DataFrame) -> dict[str, Any]:
    """The distribution of days between consecutive pages of one search (no outcome is read)."""
    gap = pages.groupby("keyword_id")["snapshot_time"].diff().dt.total_seconds().dropna() / 86400.0
    qs = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]
    bins = [0, 2, 28, 45, 60, 75, 90, 105, 130, 200, 400]
    counts = pd.cut(gap, bins).value_counts().sort_index()
    return {"pairs": int(len(gap)), "quantiles": {str(q): float(np.quantile(gap, q)) for q in qs},
            "bins": {str(k): int(v) for k, v in counts.items()}}


def difficulty_cuts(pages: pd.DataFrame) -> list[float]:
    """Tercile cut points of kd over TRAINING pages (train searches dated before the cutoff). No outcome is read."""
    split = _split()
    train = pages[(~pages["test_search"]) & (pages["date"] < split.cutoff)]
    kd = pd.to_numeric(train["kd"], errors="coerce").dropna()
    return [float(np.quantile(kd, 1 / 3)), float(np.quantile(kd, 2 / 3))] if len(kd) else []


def difficulty_of(kd: Any, cuts: Sequence[float]) -> str | None:
    if not cuts or kd is None or pd.isna(kd):
        return None
    value = float(kd)
    return "easy" if value <= cuts[0] else ("medium" if value <= cuts[1] else "hard")


def outcome_pages(pages: pd.DataFrame, low: float, high: float) -> pd.DataFrame:
    """For each page a, the FIRST later page b of the same search whose gap is inside [low, high] days."""
    left = pages[["keyword_id", "snapshot_time", "page_idx"]]
    pairs = left.merge(left, on="keyword_id", suffixes=("_a", "_b"))
    pairs = pairs[pairs["page_idx_b"] > pairs["page_idx_a"]]
    pairs["gap_days"] = (pairs["snapshot_time_b"] - pairs["snapshot_time_a"]).dt.total_seconds() / 86400.0
    pairs = pairs[(pairs["gap_days"] >= low) & (pairs["gap_days"] <= high)]
    pairs = pairs.sort_values(["keyword_id", "page_idx_a", "page_idx_b"]).drop_duplicates(["keyword_id", "page_idx_a"])
    return pairs[["keyword_id", "page_idx_a", "page_idx_b", "gap_days"]].reset_index(drop=True)


def reading_cells(sp: pd.DataFrame, pages: pd.DataFrame, low: float, high: float) -> tuple[pd.DataFrame, dict[str, int]]:
    """Test A cells: every site in the top 100 of a page (the reading), followed to the first page in the window.

    The cell is chosen from the reading page only (never from the outcome).
    The outcome is the site's position on the outcome page: 1 to 50, NT50 when
    deeper or absent from a page at least 50 deep. A site absent from a shallow
    outcome page is unknown: the cell is dropped and counted.
    """
    pairs = outcome_pages(pages, low, high)
    info = pages.set_index(["keyword_id", "page_idx"])
    at_a = sp.merge(pairs, left_on=["keyword_id", "page_idx"], right_on=["keyword_id", "page_idx_a"])
    at_a = at_a.rename(columns={"rank100": "rank100_in", "snapshot_depth": "depth_input", "date": "input_date"})
    b_rows = sp[["keyword_id", "page_idx", "site", "rank100"]].rename(columns={"page_idx": "page_idx_b", "rank100": "rank100_out"})
    cells = at_a.merge(b_rows, on=["keyword_id", "page_idx_b", "site"], how="left")
    b_info = info[["snapshot_depth", "date", "snapshot_time"]].rename(
        columns={"snapshot_depth": "depth_outcome", "date": "outcome_date", "snapshot_time": "outcome_time"})
    cells = cells.join(b_info, on=["keyword_id", "page_idx_b"])
    absent = cells["rank100_out"].isna()
    shallow = absent & (cells["depth_outcome"] < TOP_DEPTH)
    counts = {"cells_before_depth_rule": int(len(cells)), "dropped_absent_from_shallow_outcome_page": int(shallow.sum())}
    cells = cells[~shallow].copy()
    rank_out = cells["rank100_out"].astype(float)
    cells["position_out"] = np.where(rank_out <= TOP_DEPTH, rank_out, NT50).astype(int)
    cells["position_in"] = np.where(cells["rank100_in"] <= TOP_DEPTH, cells["rank100_in"], NT50).astype(int)
    cells["reading_position"] = cells["position_in"].astype(float)
    cells["new_to_search"] = np.where(cells["page_idx"] == 0, np.nan, (cells["first_idx"] == cells["page_idx"]).astype(float))
    cells["lead_days"] = cells["gap_days"].round().astype(int)
    cells["as_of"] = cells["input_date"]
    cells = _add_previous_reading(cells, sp, pages)
    cells = _add_search_info(cells, pages)
    counts["cells"] = int(len(cells))
    return cells.reset_index(drop=True), counts


def _add_previous_reading(cells: pd.DataFrame, sp: pd.DataFrame, pages: pd.DataFrame) -> pd.DataFrame:
    """The site's position on the page just before the reading page (NT50 when absent from a page 50 deep, NaN when unknown)."""
    prev = sp[["keyword_id", "page_idx", "site", "rank100"]].rename(columns={"rank100": "rank100_prev"})
    prev = prev.assign(page_idx=prev["page_idx"] + 1)  # aligned on the reading page index
    out = cells.merge(prev, on=["keyword_id", "page_idx", "site"], how="left")
    pinfo = pages.set_index(["keyword_id", "page_idx"])[["snapshot_depth", "date"]]
    pinfo = pinfo.rename(columns={"snapshot_depth": "depth_prev", "date": "prev_date"}).reset_index()
    pinfo["page_idx"] = pinfo["page_idx"] + 1
    out = out.merge(pinfo, on=["keyword_id", "page_idx"], how="left")
    rank = out["rank100_prev"].astype(float)
    known_absent = rank.isna() & (out["depth_prev"] >= TOP_DEPTH)
    out["position_prev"] = np.where(rank <= TOP_DEPTH, rank, np.where(rank > TOP_DEPTH, NT50, np.where(known_absent, NT50, np.nan)))
    out["reading_move"] = out["position_prev"] - out["reading_position"]
    out["readings_count"] = np.where(np.isfinite(out["position_prev"]), 2.0, 1.0)
    out["reading_in_top50"] = (out["reading_position"] <= TOP_DEPTH).astype(float)
    return out


def entrant_cells(sp: pd.DataFrame, pages: pd.DataFrame, design: PanelDesign) -> tuple[pd.DataFrame, dict[str, int]]:
    """Test C cells: sites new to a search on a page (no earlier page of the search shows them).

    The input page is the latest page of the search dated at least
    `entrant_min_gap_days` (and at most `entrant_max_gap_days`) before the
    entrant's page. The forecast date is the input page's date. The outcome is
    the entrant's position on its page (1 to 50, NT50 for 51 to 100). These cells
    are CONDITIONAL on the site appearing: cells that never appear are missing.
    """
    ent = sp[(sp["page_idx"] == sp["first_idx"]) & (sp["page_idx"] > 0)].copy()
    left = pages[["keyword_id", "snapshot_time", "page_idx", "date", "snapshot_depth"]]
    pairs = left.merge(left, on="keyword_id", suffixes=("_a", "_b"))
    pairs = pairs[pairs["page_idx_a"] < pairs["page_idx_b"]]
    pairs["gap_days"] = (pairs["snapshot_time_b"] - pairs["snapshot_time_a"]).dt.total_seconds() / 86400.0
    pairs = pairs[(pairs["gap_days"] >= design.entrant_min_gap_days) & (pairs["gap_days"] <= design.entrant_max_gap_days)]
    pairs = pairs.sort_values(["keyword_id", "page_idx_b", "page_idx_a"]).drop_duplicates(["keyword_id", "page_idx_b"], keep="last")
    ent = ent.merge(pairs[["keyword_id", "page_idx_a", "page_idx_b", "gap_days", "date_a", "snapshot_depth_a"]],
                    left_on=["keyword_id", "page_idx"], right_on=["keyword_id", "page_idx_b"])
    counts = {"entrants_with_input_page": int(len(ent))}
    ent = ent.rename(columns={"rank100": "rank100_out", "snapshot_depth": "depth_outcome", "date": "outcome_date",
                              "date_a": "input_date", "snapshot_depth_a": "depth_input"})
    ent["position_out"] = np.where(ent["rank100_out"] <= TOP_DEPTH, ent["rank100_out"], NT50).astype(int)
    ent["as_of"] = ent["input_date"]
    ent["lead_days"] = ent["gap_days"].round().astype(int)
    ent["new_to_search"] = 1.0
    ent["rank100_in"] = np.nan
    ent["reading_position"] = np.nan
    ent = ent.drop(columns=["page_idx"]).rename(columns={"page_idx_a": "page_idx"})
    ent = _add_search_info(ent, pages)
    return ent.reset_index(drop=True), counts


def _add_search_info(cells: pd.DataFrame, pages: pd.DataFrame) -> pd.DataFrame:
    info = pages.drop_duplicates("keyword_id").set_index("keyword_id")[["keyword", "locale", "search_group", "test_search"]]
    out = cells.drop(columns=[c for c in info.columns if c in cells.columns])
    return out.join(info, on="keyword_id")


def ceiling_cells(sp: pd.DataFrame, pages: pd.DataFrame, max_gap: float) -> pd.DataFrame:
    """Two single pulls of one search less than `max_gap` days apart. The first pull's position is the forecast of the second.

    Same kind of cells as test A: sites in the top 100 of the first pull, the
    second pull at least 50 deep when the site is absent. Nothing is fitted.

    Do not quote these numbers for the noise figure, or the reverse. The ceiling
    report-v1 gives 0.898 ("all_searches": sites in the top 100 of the FIRST pull,
    so pull-one positions 51 to 100 forecast NT50 and mostly stay there) and 0.872
    ("top50_of_first_pull": pull-one positions 1 to 50 only). Units are taken from
    the first pull alone, pairs are CONSECUTIVE pages of one search under 2 days
    apart (median 0.2 days, same-day repulls included), the first page may be
    shallow, and clusters are searches. The brief's 0.716 is
    sources/noise_sets.py `rule_v1_a_as_forecast_of_b_top50_units`: units are
    every site in the top 50 of EITHER pull (a site absent from pull A but in
    pull B's top 50 is a unit forecast 51, a sure miss), pairs are ANY two pages
    of one search 0.5 to 3 days apart with BOTH pages at least 50 deep, clusters
    are searches too. The unit set (union of both pulls' top 50s against pull
    one's top 50 or top 100), the gap window and the depth rule all differ; the
    size of each part has not been measured. Neither number stands for the other.
    """
    pairs = pages[["keyword_id", "snapshot_time", "page_idx"]].copy()
    pairs = pairs.merge(pairs, on="keyword_id", suffixes=("_a", "_b"))
    pairs = pairs[pairs["page_idx_b"] == pairs["page_idx_a"] + 1]
    pairs["gap_days"] = (pairs["snapshot_time_b"] - pairs["snapshot_time_a"]).dt.total_seconds() / 86400.0
    pairs = pairs[pairs["gap_days"] < max_gap]
    at_a = sp.merge(pairs[["keyword_id", "page_idx_a", "page_idx_b", "gap_days"]], left_on=["keyword_id", "page_idx"],
                    right_on=["keyword_id", "page_idx_a"])
    b_rows = sp[["keyword_id", "page_idx", "site", "rank100"]].rename(columns={"page_idx": "page_idx_b", "rank100": "rank100_out"})
    cells = at_a.merge(b_rows, on=["keyword_id", "page_idx_b", "site"], how="left")
    depth_b = pages.set_index(["keyword_id", "page_idx"])["snapshot_depth"].rename("depth_outcome")
    cells = cells.join(depth_b, on=["keyword_id", "page_idx_b"])
    cells = cells[~(cells["rank100_out"].isna() & (cells["depth_outcome"] < TOP_DEPTH))].copy()
    cells["position_in"] = np.where(cells["rank100"] <= TOP_DEPTH, cells["rank100"], NT50).astype(int)
    rank_out = cells["rank100_out"].astype(float)
    cells["position_out"] = np.where(rank_out <= TOP_DEPTH, rank_out, NT50).astype(int)
    return cells.reset_index(drop=True)


def add_site_features(cells: pd.DataFrame, sp: pd.DataFrame, pages: pd.DataFrame, *, score: Any, trust_model: Any,
                      evidence: Any | None, cuts: Sequence[float], log: Callable[[str], None] | None = None) -> pd.DataFrame:
    """Strength (as of the input page date, guarded), strength score, gaps and demand on the input page, trust and its gap,
    start-date features, page difficulty and volume. Everything is dated on or before the input page."""
    from .features import MEASURES, RAW_STRENGTH_KEYS, start_features_frame
    from .strength import gap_columns, page_demand_frame

    page_keys = cells[["keyword_id", "page_idx"]].drop_duplicates()
    page_rows = sp.merge(page_keys, on=["keyword_id", "page_idx"])
    # Strength of every site on the input pages, as of that page's date.
    need = pd.concat([page_rows[["site", "date"]], cells[["site", "input_date"]].rename(columns={"input_date": "date"})])
    need = need.rename(columns={"date": "as_of"}).drop_duplicates()
    st = strength_lookup(need, log=log)
    st_cols = [*RAW_STRENGTH_KEYS, *MEASURES]
    st = st.rename(columns={c: f"st_{c}" for c in st_cols})
    page_rows = page_rows.merge(st, left_on=["site", "date"], right_on=["site", "as_of"], how="left").drop(columns=["as_of"])
    out = cells.merge(st, left_on=["site", "input_date"], right_on=["site", "as_of"], how="left", suffixes=("", "_st"))
    out = out.drop(columns=[c for c in out.columns if c.endswith("_st")])
    out["strength_date"] = out["input_date"]
    # Strength score, page demand and the gap.
    plain = {f"st_{m}": m for m in MEASURES}
    page_rows["strength_score"] = score.score(page_rows.rename(columns=plain))
    out["own_score"] = score.score(out.rename(columns=plain))
    page_rows["_page"] = page_rows["keyword_id"].astype(str) + "|" + page_rows["page_idx"].astype(str)
    out["_page"] = out["keyword_id"].astype(str) + "|" + out["page_idx"].astype(str)
    out["cell_rank_in"] = out["rank100_in"]
    gaps = gap_columns(out, page_rows, score_col="strength_score", own_col="own_score")
    out = pd.concat([out.reset_index(drop=True), gaps.reset_index(drop=True)], axis=1)
    demand = page_demand_frame(page_rows)
    out = out.merge(demand, left_on="_page", right_index=True, how="left")
    # Start dates (PREDICTED from REAL evidence), as of the input page date.
    if evidence is not None:
        both = pd.concat([page_rows[["site", "date"]], out[["site", "input_date"]].rename(columns={"input_date": "date"})])
        sd_keys = both.drop_duplicates().rename(columns={"date": "as_of"}).reset_index(drop=True)
        sd = start_features_frame(sd_keys, evidence)
        sd["site"] = sd_keys["site"].to_numpy()
        sd["as_of"] = sd_keys["as_of"].to_numpy()
        page_rows = page_rows.merge(sd, left_on=["site", "date"], right_on=["site", "as_of"], how="left").drop(columns=["as_of"])
        out = out.merge(sd.rename(columns={"as_of": "input_date"}), on=["site", "input_date"], how="left")
    # Trust (PREDICTED) of every site on the page and of the cell's site, and the gap to the page's top 10.
    if trust_model is not None:
        page_rows["trust_pred"] = trust_model.predict(page_rows)
        out["trust_pred"] = trust_model.predict(out)
        tg = gap_columns(out.assign(_own=out["trust_pred"]), page_rows, score_col="trust_pred", own_col="_own")
        out["trust_gap_top10"] = tg["gap_top10"].to_numpy()
        out["trust_q_top10"] = tg["q_top10"].to_numpy()
    # The input page itself: difficulty, volume, depth, how many sites in its top 10.
    pinfo = pages[["keyword_id", "page_idx", "kd", "search_volume", "snapshot_depth", "locale", "keyword", "search_group",
                   "test_search"]].rename(columns={"kd": "pg_kd", "snapshot_depth": "pg_depth"})
    out = out.drop(columns=[c for c in ("locale", "keyword", "search_group", "test_search") if c in out.columns])
    out = out.merge(pinfo, on=["keyword_id", "page_idx"], how="left")
    out["pg_log_volume"] = np.log10(1.0 + pd.to_numeric(out["search_volume"], errors="coerce"))
    top10 = page_rows[page_rows["rank100"] <= 10].groupby("_page")["site"].nunique().rename("pg_sites_top10")
    out = out.join(top10, on="_page")
    out["difficulty"] = [difficulty_of(v, cuts) for v in out["pg_kd"]]
    out["search"] = out["keyword"]
    out["sd_young"] = out["sd_young"] if "sd_young" in out else np.nan
    return out.drop(columns=["_page"]).reset_index(drop=True)


MODEL_DATE_COL = "model_labels_end"


def mark_model_labels_end(frame: pd.DataFrame, labels_end: date) -> pd.DataFrame:
    """Record, per row, the last date of the labels the frozen trust and strength models were fitted on.

    Both models were fitted on training pages dated before DEFAULT_SPLIT's cutoff, so their predictions carry
    facts up to the day before it. A graded (test) row must have its forecast date after that day:
    `check_model_inputs_dated` raises LeakError otherwise. Training and validation rows dated earlier are known
    to see in-sample trust and strength inputs; `in_sample_model_inputs` counts them for the report.
    """
    out = frame.copy()
    out[MODEL_DATE_COL] = labels_end
    return out


def check_model_inputs_dated(test: pd.DataFrame) -> None:
    """LeakError when a graded row's forecast date is not after the frozen models' last label date."""
    if MODEL_DATE_COL in test:
        check_inputs_dated(test, date_cols=(MODEL_DATE_COL,), outcome_col=None)


def in_sample_model_inputs(frame: pd.DataFrame) -> dict[str, Any]:
    """How many rows carry trust and strength inputs from models whose labels end after the row's forecast date."""
    if MODEL_DATE_COL not in frame or len(frame) == 0:
        return {"rows": 0, "of": int(len(frame))}
    late = np.array([as_date(e) > as_date(a) for e, a in zip(frame[MODEL_DATE_COL], frame["as_of"])])
    return {"rows": int(late.sum()), "of": int(len(frame)),
            "note": ("These rows' trust and strength inputs come from the frozen models fitted on training pages up to "
                     "the cutoff, which include later pages of these rows' own searches. They fit the prior and set its "
                     "temperature and 80% range; no graded row is among them. Not fixed: needs time-respecting or "
                     "out-of-fold trust and strength fits for training rows.")}


def sample_training(frame: pd.DataFrame, cap: int, seed: int) -> pd.DataFrame:
    """Whole searches in sha256(seed|keyword_id) order until `cap` rows. Deterministic."""
    if len(frame) <= cap:
        return frame
    sizes = frame.groupby("keyword_id").size()
    order = sorted(sizes.index, key=lambda k: stable_share(str(k), seed))
    chosen, total = [], 0
    for kw in order:
        if total >= cap:
            break
        chosen.append(kw)
        total += int(sizes[kw])
    return frame[frame["keyword_id"].isin(set(chosen))]


def cross_fitted_prior(train: pd.DataFrame, features: Sequence[str], *, label: str, seed: int,
                       group_col: str = "search_group", settings: Mapping[str, Any] | None = None) -> np.ndarray:
    """Out-of-fold prior distributions on training rows (folds by search group), so the updater never sees an in-sample prior.

    `settings` (the tree settings the main fit chose) skips the grid search in each fold.
    """
    from .placement import BAND_GRID, group_folds

    grid = [dict(settings)] if settings else list(BAND_GRID)
    folds = group_folds(train[group_col].astype(str).tolist(), seed + 3, PRIOR_FOLDS)
    out = np.zeros((len(train), 51))
    for k in range(PRIOR_FOLDS):
        fit_rows = folds != k
        model = fit_placement(train[fit_rows], features, training_label=label, seed=seed + k, group_col=group_col,
                              name=f"prior-fold{k}", grid=grid)
        out[~fit_rows] = model.distributions(train[~fit_rows])
    return out


# ---------------------------------------------------------------------------
# panel preregistration and run


def panel_specs(design: PanelDesign, *, gaps: Mapping[str, Any], cuts: Sequence[float], version: int = 1,
                reason: str | None = None) -> dict[str, BacktestSpec]:
    """The three frozen designs (A, B, C) and the ceiling, built from no outcome at all.

    Version 1 returns the designs frozen and scored in prereg.json, unchanged (their sha256 still reproduces).
    Version k >= 2 records what the code now does and the reason for the new version: graded cells are held out
    on the SITE side too (training cells use training sites only), brand searches are left out of the headline,
    and headline intervals resample sites.
    """
    if int(version) >= 2:
        return _panel_specs_v2(design, gaps=gaps, cuts=cuts, version=int(version), reason=reason)
    split = asdict(_split())
    common = {"dataset": "panel dfs_history (DataForSEO sample databases), poison-risk pages dropped", "split": split,
              "time_cutoff": split["time_cutoff"]}
    thresholds = {"grading_rule": RULE_V1.version, "grading_rule_hash": RULE_V1.rule_hash(),
                  "lead_floor_days": LEAD_FLOOR_DAYS, "min_count_to_print": MIN_COUNT_TO_PRINT_RATE,
                  "bootstrap": {"draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED, "clusters": ["search", "site"]},
                  "difficulty_kd_cuts_from_training_pages": list(cuts), "young_site": "sd_young == 1 (start estimate under 730 days)",
                  "placement_point_rule": "median", "range_coverage": 0.80, "label_thresholds": {"locked": 0.80, "out_of_reach": 0.80},
                  "updater_model_replaces_table": "lower bound of model minus table on validation rows above 0",
                  "validation_share_of_training_search_groups": design.validation_share,
                  "train_cells_max": design.train_cells_max, "prior_cross_fit_folds": PRIOR_FOLDS,
                  "consecutive_page_gap_distribution": dict(gaps)}
    models = {"strength_score": "strength-v1 (data/forecast/models/strength/strength-v1-score.json, fitted on training pages)",
              "strength_only_forecaster": "strength-only-forecaster-v1 (fitted on training cells)",
              "trust": "trust-v1-free-lexical-all (fitted on training searches and training sites before the cutoff)",
              "placement": "placement-v1, fitted here on training cells", "updater": "updater-table-v1 and updater-model-v2, fitted here"}
    windows = {name: [low, high] for name, low, high in design.windows_days}
    a = BacktestSpec(
        name="panel_a_reading_to_outcome", windows_days=windows, feature_set=list(PANEL_PRIOR_FEATURES) + list(MODEL_FEATURES),
        model_versions=models, baselines=["always_nt50", "persistence", "band_prior", "site_strength",
                                          "the updater version not in use (table or model)", "placement_prior_no_reading"],
        thresholds=thresholds,
        cells=("Every site in the top 100 of a page (the reading). The outcome is the FIRST later page of the same search whose "
               "gap is inside the window. Outcome position 1 to 50; NT50 when deeper or absent from a page at least 50 deep; "
               "absent from a shallower page is dropped and counted. Train: training search groups, outcome before the cutoff. "
               "Test: held-out search groups, reading on or after the cutoff. Subsets: all, new to the search at the reading "
               "(not on any earlier page; unknown on a search's first page), young sites. Windows chosen from the "
               "consecutive gap distribution (modes at 60 to 75 and 75 to 130 days), not from outcomes."),
        notes=[PANEL_LIMIT_NOTE, STAND_IN_NOTE], **common)
    b = BacktestSpec(
        name="panel_b_two_readings", windows_days={"lead_28_74": windows["lead_28_74"]}, feature_set=list(MODEL_FEATURES),
        model_versions=models, baselines=["always_nt50", "persistence", "band_prior", "site_strength", "mean_of_two_readings"],
        thresholds=thresholds,
        cells=("Test A cells (first window) where the page just before the reading page exists and gives a known position "
               "(present, or absent from a page at least 50 deep). Same model form fitted twice on the same training rows: "
               "with the move between the two readings and the reading count, and without them. Scored on identical test cells."),
        notes=[PANEL_LIMIT_NOTE], **common)
    c = BacktestSpec(
        name="panel_c_entrant_placement",
        windows_days={"input_page_gap": [design.entrant_min_gap_days, design.entrant_max_gap_days]},
        feature_set=list(ENTRANT_FEATURES), model_versions=models,
        baselines=["always_nt50", "persistence", "band_prior", "site_strength", "page_middle", "always_21_50"],
        thresholds={**thresholds, "mae_top30": "mean absolute error in places for entrants landing in 1 to 30: the model's "
                    "median given 1 to 30, and its plain point (NT50 counted as 51), against 15.5 (the middle of the top 30)"},
        cells=("Sites new to a search on a page (on no earlier page of it). Input: the latest page of the search dated 28 to "
               "130 days before. Forecast date: that page's date. Outcome: the entrant's position on its page. CONDITIONAL on "
               "the site appearing: cells that never appear are missing, so this is not the upload-day hit rate."),
        notes=[PANEL_LIMIT_NOTE, "Title match is left out: the entrant's title is only seen on the outcome page."], **common)
    ceiling = BacktestSpec(
        name="panel_ceiling_single_pull", windows_days={"gap": [0.0, design.ceiling_max_gap_days]}, feature_set=[],
        model_versions={}, baselines=["persistence"], thresholds=thresholds,
        cells=("Consecutive pages of one search less than 2 days apart. Sites in the top 100 of the first. The first pull's "
               "position is the forecast of the second. All searches (nothing is fitted). Also shown on held-out searches only."),
        notes=["This is label noise on single pulls, the ceiling for any forecast graded on one pull."], **common)
    return {"A": a, "B": b, "C": c, "ceiling": ceiling}


def _panel_specs_v2(design: PanelDesign, *, gaps: Mapping[str, Any], cuts: Sequence[float], version: int,
                    reason: str | None) -> dict[str, BacktestSpec]:
    if not reason or not str(reason).strip():
        raise ValueError(f"backtest design version {version} needs the reason for the new version, recorded in its prereg")
    base = panel_specs(design, gaps=gaps, cuts=cuts, version=1)
    sides = (" Version {v} sides: test cells are held-out search groups AND held-out sites (SplitRule.site_is_test, sites "
             "the trust model never trained on) with the reading on or after the cutoff; training and validation cells are "
             "training search groups AND training sites with the outcome before the cutoff.").format(v=version)
    rules = {"headline_clusters": "site (2,000 draws, seed 20261122); the search-clustered headline is printed apart",
             "brand_searches": ("flagged when the compacted site label (site.split('.')[0]) or a registered brand term is "
                                "inside the compacted search text (labels under 3 characters must be a whole word); left "
                                "out of the headline and shown apart"),
             "held_out_flags": "computed per graded cell (search, date and site sides); a cell not held out is refused",
             "start_evidence": "Common Crawl first release only; RDAP is left out (no dated history)",
             "models": {"strength": f"strength-v{STRENGTH_MODEL_VERSION}", "trust": TRUST_MODEL_NAME,
                        "missing_model": "refused"}}
    out = {}
    for key, spec in base.items():
        data = asdict(spec)
        data["version"] = f"backtest-v{version}"
        data["cells"] = spec.cells + (sides if key != "ceiling" else "")
        data["thresholds"] = {**dict(spec.thresholds), f"version_{version}_rules": rules}
        data["notes"] = [*spec.notes, f"Design version {version}. Reason for the new version: {str(reason).strip()}"]
        out[key] = BacktestSpec(**data)
    return out


TRUST_MODEL_NAME = "trust-v1-free-lexical-all"  # the trust model every panel prereg names
STRENGTH_MODEL_VERSION = 1  # strength-v1-score.json and strength-v1-forecaster.json


def model_paths(*, strength_version: int = STRENGTH_MODEL_VERSION, trust_name: str = TRUST_MODEL_NAME) -> dict[str, Path]:
    from .strength import model_files
    from .trust import default_models_dir

    files = model_files(strength_version)
    return {"strength_score": files["score"], "strength_forecaster": files["forecaster"],
            "trust": default_models_dir() / f"{trust_name}.json"}


def model_hashes(paths: Mapping[str, Path]) -> dict[str, Any]:
    """The sha256 of every model file a run loads, for its manifest."""
    return {name: {"file": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None}
            for name, path in paths.items()}


def _load_models(log: Callable[[str], None] | None, *, strength_version: int = STRENGTH_MODEL_VERSION,
                 trust_name: str = TRUST_MODEL_NAME):
    """The preregistered models: the strength score, the strength-only forecaster and the trust model.

    A missing preregistered model is refused: scoring the design without its trust input would publish a
    different design under the same prereg sha256.
    """
    from .strength import load_strength_forecaster, load_strength_score
    from .trust import load_trust_model

    paths = model_paths(strength_version=strength_version, trust_name=trust_name)
    missing = [name for name, path in paths.items() if not Path(path).is_file()]
    if missing:
        raise BacktestRefusal(f"the preregistered model files {missing} are missing ({[str(paths[m]) for m in missing]}). "
                              "The design is not scored without them.")
    score = load_strength_score(paths["strength_score"])
    forecaster = load_strength_forecaster(paths["strength_forecaster"])
    trust_model = load_trust_model(paths["trust"])
    if log:
        log(f"models: strength-v{strength_version} score and forecaster, trust {trust_name}")
    return score, forecaster, trust_model


def prereg_panel(design: PanelDesign = PanelDesign(), *, root: str | Path | None = None,
                 log: Callable[[str], None] | None = None, version: int = 1, reason: str | None = None) -> dict[str, str]:
    """Freeze the panel designs. Reads page dates, depths and training-page kd only: no position of any outcome page.

    Version 1 is frozen and scored. Version k >= 2 (with its reason) goes to prereg-v<k>.json beside it, with the
    sha256 of the forecast source so later code changes are refused."""
    _, pages = load_panel_pages(log=log)
    specs = panel_specs(design, gaps=consecutive_gaps(pages), cuts=difficulty_cuts(pages), version=version, reason=reason)
    return {key: freeze_spec(spec, root, design_version=version, code=version > 1) for key, spec in specs.items()}


def _sides(cells: pd.DataFrame, design: PanelDesign) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(train, validation, test) masks. Test: held-out search group, forecast date on or after the cutoff and a
    held-out SITE (one the trust model never trained on). Train and validation: training search group, outcome
    before the cutoff and a training site, so no held-out site's label enters a fit."""
    split = _split()
    cut = split.cutoff
    test_site = np.array([bool(split.site_is_test(str(s))) for s in cells["site"]], dtype=bool)
    test = cells["test_search"].astype(bool) & np.array([as_date(d) >= cut for d in cells["as_of"]]) & test_site
    train_all = ((~cells["test_search"].astype(bool)) & np.array([as_date(d) < cut for d in cells["outcome_date"]])
                 & ~test_site)
    is_val = np.array([stable_share(str(g), design.seed + 21) < design.validation_share for g in cells["search_group"]])
    return pd.Series(train_all & ~is_val, index=cells.index), pd.Series(train_all & is_val, index=cells.index), pd.Series(test, index=cells.index)


def _cells_for(frame: pd.DataFrame, points: Sequence[int], stage: str) -> list[GradedCell]:
    return graded_cells_from(frame, points, stage=stage)


def _rates_by(frame: pd.DataFrame, points: Mapping[str, Sequence[int]], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for value in sorted(set(frame[key].astype(str))):
        mask = (frame[key].astype(str) == value).to_numpy()
        actual = frame["position_out"].to_numpy()[mask]
        out[value] = {"count": int(mask.sum())}
        for name, pts in points.items():
            arr = np.asarray(pts)[mask]
            hits = [RULE_V1.is_hit(int(p), int(a)) for p, a in zip(arr, actual)]
            out[value][name] = sum(hits) / len(hits) if hits else None
    return out


def _subset_block(frame: pd.DataFrame, points: Mapping[str, Sequence[int]], model_name: str, *, stage: str,
                  band_prior_training: Sequence[GradedCell], as_of: date, notes: Sequence[str], extra: Mapping[str, Any]) -> dict[str, Any]:
    if len(frame) == 0:
        return {"count": 0, "note": "no cells"}
    cells = _cells_for(frame, points[model_name], stage)
    base = {k: v for k, v in points.items() if k != model_name and k != "always_nt50"}
    report = backtest_report(cells, base, stage=stage, as_of=as_of, model_version=model_name,
                             outcome_dates=list(frame["outcome_date"]), as_ofs=list(frame["as_of"]),
                             band_prior_training=band_prior_training, notes=notes, extra=extra,
                             held_out=held_out_flags(frame, _split()).tolist())
    by_points = dict(points)
    if report["band_prior"] is not None:
        by_points["band_prior"] = band_prior_points(report["band_prior"], cells)
    if "reading_position" in frame and np.isfinite(pd.to_numeric(frame["reading_position"], errors="coerce")).any():
        report["by_reading_band"] = _rates_by(frame.assign(_rb=[reading_band(v) for v in frame["reading_position"]]),
                                              by_points, "_rb")
    report["by_difficulty_all_forecasters"] = _rates_by(frame.assign(_d=frame["difficulty"].fillna("unknown")), by_points, "_d")
    report["counts"] = {"cells": int(len(frame)), "searches": int(frame["keyword_id"].nunique()),
                        "sites": int(frame["site"].nunique())}
    return report


def run_panel(design: PanelDesign = PanelDesign(), *, root: str | Path | None = None, rehearsal: bool = False,
              log: Callable[[str], None] | None = None, version: int = 1, reason: str | None = None,
              check_git: bool = True) -> dict[str, Any]:
    """Score tests A, B, C and the ceiling ONCE on the REAL panel and write versioned reports. Needs the frozen preregs.

    Design version 1 is scored (report-v1.json), so a version 1 run is refused. A rerun is design version k >= 2:
    `prereg --design-version k --reason ...`, commit and push the prereg-v<k>.json files, then `run --design-version k`.
    Before any held-out outcome is read the run refuses: a prereg that does not match, a design already scored under
    the same prereg sha256, a forecast source tree that differs from HEAD, a prereg that is not committed and pushed,
    and a missing preregistered model file.

    With `rehearsal` the whole run happens inside the training side of the real split (training searches, pages
    before the cutoff), re-split by strength.REHEARSAL_SPLIT, and writes under data/forecast/cache/backtest/. No
    held-out outcome is read, no git check is made, and it can be rerun. Use it to debug before the one scored run.
    """

    sp, pages = load_panel_pages(log=log)
    gaps, cuts = consecutive_gaps(pages), difficulty_cuts(pages)
    if version > 1 and not reason:
        from .features import frozen_reason

        probe = panel_specs(design, gaps=gaps, cuts=cuts)["A"]
        reason = frozen_reason(backtest_dir(probe.name, root), version)
    specs = panel_specs(design, gaps=gaps, cuts=cuts, version=version, reason=reason)
    if rehearsal:
        from .strength import REHEARSAL_SPLIT

        sp, pages = rehearsal_pages(sp, pages)
        _ACTIVE_SPLIT.append(REHEARSAL_SPLIT)
        root = Path(root) if root else panel_cache_dir() / "rehearsal_evals"
        specs = {k: BacktestSpec(**{**asdict(v), "name": "rehearsal_" + v.name}) for k, v in specs.items()}
        shas = {key: freeze_spec(spec, root, design_version=version) for key, spec in specs.items()}
        try:
            return _run_panel_body(sp, pages, design, specs, shas, cuts, root, log, rehearsal=True)
        finally:
            _ACTIVE_SPLIT.pop()
    shas = {key: require_spec(spec, root, design_version=version) for key, spec in specs.items()}
    for key, spec in specs.items():
        refuse_scored_twice(spec.name, shas[key], root)
    provenance = code_provenance()
    git_info: dict[str, Any] = {}
    if check_git:
        require_clean_source(provenance)
        git_info = {key: require_prereg_committed(spec_prereg_file(spec, root, version)) for key, spec in specs.items()}
    return _run_panel_body(sp, pages, design, specs, shas, cuts, root, log, rehearsal=False, provenance=provenance,
                           git_info=git_info, design_version=version)


def _run_panel_body(sp: pd.DataFrame, pages: pd.DataFrame, design: PanelDesign, specs: Mapping[str, BacktestSpec],
                    shas: Mapping[str, str], cuts: Sequence[float], root: str | Path | None,
                    log: Callable[[str], None] | None, *, rehearsal: bool = False,
                    provenance: Mapping[str, Any] | None = None, git_info: Mapping[str, Any] | None = None,
                    design_version: int = 1) -> dict[str, Any]:
    from .features import StartEvidence
    from .markers import evaluate_marker

    score, strength_fc, trust_model = _load_models(log)
    loaded = model_hashes(model_paths())
    evidence = StartEvidence.load(sorted(set(sp["site"])))  # backtest frame: no RDAP (no dated history)
    prov = dict(provenance) if provenance is not None else code_provenance()
    scored = not rehearsal
    from .strength import DEFAULT_SPLIT

    labels_end = DEFAULT_SPLIT.cutoff - timedelta(days=1)  # trust-v1 and strength-v1: training pages before the cutoff

    def _write(key: str, report: Mapping[str, Any]) -> dict[str, Any]:
        extra = {"models_loaded": loaded, "design_version": design_version, "rehearsal": rehearsal,
                 "git": (git_info or {}).get(key), "git_checked": bool(git_info)}
        return write_run(specs[key].name, report, prereg_sha256=shas[key], root=root, scored_once=scored,
                         provenance=prov, extra_manifest=extra)

    results: dict[str, Any] = {}

    # ---- ceiling (nothing fitted)
    ceil = ceiling_cells(sp, pages, design.ceiling_max_gap_days)
    split = _split()
    kw = pages.drop_duplicates("keyword_id").set_index("keyword_id")
    ceil["keyword"] = ceil["keyword_id"].map(kw["keyword"])
    ceil["test_search"] = ceil["keyword_id"].map(kw["test_search"]).astype(bool)
    ceil_cells = [GradedCell(site=s, search=k, point=int(p), actual=int(a), source="backtest") for s, k, p, a in
                  zip(ceil["site"], ceil["keyword_id"], ceil["position_in"], ceil["position_out"])]
    test_mask = ceil["test_search"].to_numpy()
    ceiling = {
        "label": REAL, "pairs_of_pages": int(ceil.groupby(["keyword_id", "page_idx_a"]).ngroups),
        "all_searches": summarize(ceil_cells, cluster_by="search"),
        "all_searches_site_clusters": summarize(ceil_cells, cluster_by="site")["hit"],
        "top50_of_first_pull": summarize([c for c, p in zip(ceil_cells, ceil["position_in"]) if p <= TOP_DEPTH], cluster_by="search")["hit"],
        "held_out_searches": summarize([c for c, m in zip(ceil_cells, test_mask) if m], cluster_by="search")["hit"],
        "gap_days_median": float(ceil["gap_days"].median()) if len(ceil) else None,
        "by_gap": {name: summarize([c for c, g in zip(ceil_cells, ceil["gap_days"]) if lo <= g < hi], cluster_by="search")["hit"]
                   for name, lo, hi in (("under_half_a_day", 0.0, 0.5), ("half_a_day_to_two_days", 0.5, 2.0))},
        "by_band_of_first_pull": {band: summarize([c for c, p in zip(ceil_cells, ceil["position_in"]) if fine_band_of(int(p)) == band],
                                                  cluster_by="search")["hit"] for band in FINE_BANDS},
        "note": "Persistence of one pull as the forecast of the next pull under 2 days later, rule v1. Label noise, not a model.",
    }
    results["ceiling"] = _write("ceiling", {"label": REAL, "ceiling": ceiling})
    results["ceiling"]["numbers"] = ceiling

    # ---- A and B
    a_reports: dict[str, Any] = {}
    b_report: dict[str, Any] | None = None
    for name, low, high in design.windows_days:
        cells, counts = reading_cells(sp, pages, low, high)
        tr_mask, va_mask, te_mask = _sides(cells, design)
        train = sample_training(cells[tr_mask], design.train_cells_max, design.seed)
        val = sample_training(cells[va_mask], design.train_cells_max // 3, design.seed + 1)
        test = cells[te_mask]
        if log:
            log(f"A {name}: train {len(train)}, validation {len(val)}, test {len(test)} cells")
        use = pd.concat([train.assign(_side="train"), val.assign(_side="val"), test.assign(_side="test")], ignore_index=True)
        use = add_site_features(use, sp, pages, score=score, trust_model=trust_model, evidence=evidence, cuts=cuts, log=log)
        use = mark_model_labels_end(use, labels_end)
        _note(log, f"A {name}: features built")
        check_inputs_dated(use, date_cols=INPUT_DATE_COLS)
        train, val, test = (use[use["_side"] == s].reset_index(drop=True) for s in ("train", "val", "test"))
        check_held_out(test, split)
        if not rehearsal:
            check_model_inputs_dated(test)
        check_outcome_depth(test)
        feats = list(PANEL_PRIOR_FEATURES)
        prior_model = fit_placement(train, feats, training_label=REAL, seed=design.seed, name=f"prior-{name}")
        _note(log, f"A {name}: prior placement fitted ({prior_model.cv.get('chosen')})")
        train_prior = cross_fitted_prior(train, feats, label=REAL, seed=design.seed,
                                         settings=(prior_model.cv.get("chosen") or {}).get("params"))
        _note(log, f"A {name}: cross-fitted priors done")
        val_prior = prior_model.distributions(val)
        test_prior = prior_model.distributions(test)
        for frame, prior in ((train, train_prior), (val, val_prior), (test, test_prior)):
            cols = prior_columns(prior)
            for col in cols.columns:
                frame[col] = cols[col].to_numpy()
        table = fit_table(train, training_label=REAL, seed=design.seed)
        try:
            model = fit_model(train, training_label=REAL, seed=design.seed)
        except UpdaterError:
            model = None
        updater = choose_version(table, model, val, val_prior)
        _note(log, f"A {name}: updater in use {updater.in_use}")
        models = ForecastModels(placement=prior_model, updater=updater)
        dists, forecasts = forecast_frame(test, models, stage="d30")
        test["forecast_label"] = [f.label for f in forecasts]
        test["range_low"] = [f.range_low for f in forecasts]
        test["range_high"] = [f.range_high for f in forecasts]
        points = {
            "updater_in_use": [f.point for f in forecasts],
            "always_nt50": [NT50] * len(test),
            "persistence": test["position_in"].astype(int).tolist(),
            "site_strength": [int(p) for p in strength_fc.points(test)],
            "updater_table": [dist_median(d) for d in updater.distributions(test, test_prior, "table")],
            "placement_prior_no_reading": [dist_median(d) for d in test_prior],
        }
        versions = {"updater_table": points["updater_table"]}
        if model is not None:
            versions["updater_model"] = [dist_median(d) for d in updater.distributions(test, test_prior, "model")]
            points["updater_model"] = versions["updater_model"]
        # The version in use is the model being graded. It is never also listed as its own baseline.
        points.pop("updater_" + updater.in_use, None)
        train_cells = _cells_for(train, train["position_out"].astype(int).tolist(), "d30")
        notes = [PANEL_LIMIT_NOTE, STAND_IN_NOTE, f"Updater in use: {updater.in_use}. {updater.reason}"]
        as_of_report = max(as_date(d) for d in test["outcome_date"]) if len(test) else split.cutoff
        extra = {"design": name, "window_days": [low, high], "cell_counts": counts, "prereg_sha256": shas["A"],
                 "updater": {"in_use": updater.in_use, "reason": updater.reason, "comparison": updater.comparison,
                             "table_alpha": table.alpha, "range_level": updater.range_level},
                 "prior_model": {"version": prior_model.model_version(), "temperature": prior_model.temperature,
                                 "range_level": prior_model.range_level, "cv": prior_model.cv,
                                 "calibration_on_its_fold": prior_model.calibration},
                 "point_diagnostics": point_diagnostics(prior_model, dists, test["position_out"].tolist()),
                 "updater_versions_on_test": {k: float(np.mean([RULE_V1.is_hit(int(p), int(a)) for p, a in
                                                                zip(v, test["position_out"])])) if len(test) else None
                                              for k, v in versions.items()},
                 "train_rows": int(len(train)), "validation_rows": int(len(val)),
                 "in_sample_model_inputs": {"train": in_sample_model_inputs(train), "validation": in_sample_model_inputs(val),
                                            "test": in_sample_model_inputs(test)}}
        subsets = {"all": test, "new_to_search": test[test["new_to_search"] == 1.0],
                   "young_sites": test[test["sd_young"] == 1.0]}
        block = {}
        for sub_name, sub in subsets.items():
            idx = sub.index.to_numpy()
            sub_points = {k: [v[i] for i in idx] for k, v in points.items()}
            block[sub_name] = _subset_block(sub.reset_index(drop=True), sub_points, "updater_in_use", stage="d30",
                                            band_prior_training=train_cells, as_of=as_of_report, notes=notes, extra=extra)
        block["all"]["marker_4_on_stand_in"] = evaluate_marker(4, block["all"])
        block["all"]["page_one_counts"] = block["all"]["headline"]["summary"]["page_one"]
        a_reports[name] = block
        if name == design.windows_days[0][0]:
            b_report = _test_b(train, val, test, test_prior, points, train_cells, design, as_of_report, shas["B"])
    results["A"] = _write("A", {"label": REAL, "kind": "panel_a", "windows": a_reports})
    results["A"]["summary"] = _summary_a(a_reports)
    if b_report is not None:
        results["B"] = _write("B", b_report)
        results["B"]["summary"] = b_report.get("summary")

    # ---- C
    c_report = _test_c(sp, pages, design, score=score, strength_fc=strength_fc, trust_model=trust_model, evidence=evidence,
                       cuts=cuts, sha=shas["C"], log=log, labels_end=labels_end, strict_model_dates=not rehearsal)
    results["C"] = _write("C", c_report)
    results["C"]["summary"] = c_report.get("summary")
    return results


def _summary_a(a_reports: Mapping[str, Any]) -> dict[str, Any]:
    out = {}
    for window, block in a_reports.items():
        out[window] = {}
        for sub, rep in block.items():
            if not isinstance(rep, dict) or "headline" not in rep:
                continue
            head = rep["headline"]
            out[window][sub] = {
                "cells": head["forecasts"], "hit": head["summary"]["hit"]["rate"],
                "hit_interval_site": head["summary"]["hit"]["cluster_interval"],
                "hit_interval_search": rep["headline_search_clusters"]["summary"]["hit"]["cluster_interval"],
                "brand_cells_shown_apart": head["brand_forecasts_shown_apart"],
                "baselines": {k: v["baseline_rate"] for k, v in head["baselines"].items()},
                "best_baseline": head["best_baseline"], "skill": head["skill_over_best_baseline"],
                "gain_over_best_interval": head["baselines"][head["best_baseline"]]["difference_interval"] if head["best_baseline"] else None,
                "band_match": head["summary"]["band_match"]["rate"],
                "page_one": {"calls": head["summary"]["page_one"]["calls"], "outcomes": head["summary"]["page_one"]["outcomes"],
                             "precision": head["summary"]["page_one"]["precision"]["rate"],
                             "recall": head["summary"]["page_one"]["recall"]["rate"]},
            }
    return out


def _test_b(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, test_prior: np.ndarray,
            points_a: Mapping[str, Sequence[int]], train_cells: Sequence[GradedCell], design: PanelDesign,
            as_of_report: date, sha: str) -> dict[str, Any]:
    """Does the move between two readings add anything over the latest reading alone? Identical cells, same model form."""
    has_prev = lambda f: np.isfinite(pd.to_numeric(f["position_prev"], errors="coerce").to_numpy(dtype=float))  # noqa: E731
    tr = train[has_prev(train)].reset_index(drop=True)
    te_mask = has_prev(test)
    te = test[te_mask].reset_index(drop=True)
    prior_te = test_prior[te_mask]
    without = [f for f in MODEL_FEATURES if f not in ("reading_move", "readings_count")]
    try:
        m_with = fit_model(tr, training_label=REAL, seed=design.seed)
        m_without = fit_model(tr, training_label=REAL, seed=design.seed, features=without)
    except UpdaterError as error:
        return {"label": REAL, "kind": "panel_b", "error": str(error)}
    table = fit_table(tr, training_label=REAL, seed=design.seed)
    with_upd = Updater(table, m_with, "model", "test B: with the move")
    without_upd = Updater(table, m_without, "model", "test B: latest reading alone")
    p_with = [dist_median(d) for d in with_upd.distributions(te, prior_te)]
    p_without = [dist_median(d) for d in without_upd.distributions(te, prior_te)]
    mean_two = np.round((te["position_prev"].to_numpy(dtype=float) + te["reading_position"].to_numpy(dtype=float)) / 2.0)
    mean_two = np.where(mean_two > TOP_DEPTH, NT50, mean_two).astype(int).tolist()
    idx = np.flatnonzero(te_mask)
    points = {"model_with_move": p_with, "model_latest_reading_alone": p_without, "always_nt50": [NT50] * len(te),
              "persistence": te["position_in"].astype(int).tolist(), "mean_of_two_readings": mean_two,
              "site_strength": [points_a["site_strength"][i] for i in idx]}
    report = _subset_block(te, points, "model_with_move", stage="d30", band_prior_training=train_cells, as_of=as_of_report,
                           notes=[PANEL_LIMIT_NOTE], extra={"prereg_sha256": sha, "train_rows": int(len(tr))})
    cells = _cells_for(te, p_with, "d30")
    from .grading import compare_to_baseline

    keep = [i for i, c in enumerate(cells) if not c.is_brand]  # brand searches are shown apart, never in a gain
    kept_cells, kept_without = [cells[i] for i in keep], [p_without[i] for i in keep]
    gain_s = compare_to_baseline(kept_cells, kept_without, cluster_by="search")
    gain_site = compare_to_baseline(kept_cells, kept_without, cluster_by="site")
    report["kind"] = "stage_report"
    report["move_adds"] = {"with_minus_without_search_clusters": gain_s, "with_minus_without_site_clusters": gain_site}
    report["summary"] = {"cells": len(te), "with_move": gain_s["model_rate"], "latest_alone": gain_s["baseline_rate"],
                         "difference": gain_s["difference"], "interval_search": gain_s["difference_interval"],
                         "interval_site": gain_site["difference_interval"],
                         "persistence": report["headline"]["baselines"]["persistence"]["baseline_rate"]}
    return report


def _test_c(sp: pd.DataFrame, pages: pd.DataFrame, design: PanelDesign, *, score: Any, strength_fc: Any, trust_model: Any,
            evidence: Any, cuts: Sequence[float], sha: str, log: Callable[[str], None] | None,
            labels_end: date | None = None, strict_model_dates: bool = False) -> dict[str, Any]:
    from .markers import evaluate_marker

    cells, counts = entrant_cells(sp, pages, design)
    tr_mask, va_mask, te_mask = _sides(cells, design)
    train = sample_training(cells[tr_mask | va_mask], design.train_cells_max, design.seed)
    test = cells[te_mask]
    if log:
        log(f"C: train {len(train)}, test {len(test)} entrant cells")
    use = pd.concat([train.assign(_side="train"), test.assign(_side="test")], ignore_index=True)
    use = add_site_features(use, sp, pages, score=score, trust_model=trust_model, evidence=evidence, cuts=cuts, log=log)
    if labels_end is not None:
        use = mark_model_labels_end(use, labels_end)
    check_inputs_dated(use, date_cols=INPUT_DATE_COLS)
    train = use[use["_side"] == "train"].reset_index(drop=True)
    test = use[use["_side"] == "test"].reset_index(drop=True)
    check_held_out(test, _split())
    if strict_model_dates:
        check_model_inputs_dated(test)
    model = fit_placement(train, list(ENTRANT_FEATURES), training_label=REAL, seed=design.seed, name="entrant")
    dists, forecasts = forecast_frame(test, ForecastModels(placement=model), stage="upload")
    test["forecast_label"] = [f.label for f in forecasts]
    test["range_low"] = [f.range_low for f in forecasts]
    test["range_high"] = [f.range_high for f in forecasts]
    middle = [min(int(round((float(d) + 1) / 2.0)), NT50) if pd.notna(d) else 25 for d in test["depth_input"]]
    middle = [m if m <= TOP_DEPTH else NT50 for m in middle]
    points = {"placement": [f.point for f in forecasts], "always_nt50": [NT50] * len(test),
              "persistence": [NT50] * len(test), "site_strength": [int(p) for p in strength_fc.points(test)],
              "page_middle": middle, "always_21_50": [35] * len(test)}
    train_cells = _cells_for(train, train["position_out"].astype(int).tolist(), "upload")
    as_of_report = max(as_date(d) for d in test["outcome_date"]) if len(test) else _split().cutoff
    notes = [PANEL_LIMIT_NOTE, "CONDITIONAL on the site appearing on the page. Cells that never appear are missing, so this "
             "is not the upload-day hit rate.", "Persistence equals always NT50 here: an entrant was not on the input page.",
             "Page middle and always 21 to 50 score the same under rule v1 whenever the page middle is 21 to 50."]
    report = _subset_block(test, points, "placement", stage="upload", band_prior_training=train_cells, as_of=as_of_report,
                           notes=notes, extra={"prereg_sha256": sha, "cell_counts": counts, "train_rows": int(len(train)),
                                               "in_sample_model_inputs": {"train": in_sample_model_inputs(train),
                                                                          "test": in_sample_model_inputs(test)},
                                               "model": {"version": model.model_version(), "temperature": model.temperature,
                                                         "range_level": model.range_level, "cv": model.cv,
                                                         "calibration_on_its_fold": model.calibration},
                                               "point_diagnostics": point_diagnostics(model, dists, test["position_out"].tolist())})
    report["marker_3_on_stand_in"] = evaluate_marker(3, report)
    actual = test["position_out"].to_numpy()
    top30 = actual <= 30
    cond = []
    for d in dists[top30]:
        part = d[:30] / d[:30].sum() if d[:30].sum() > 0 else np.full(30, 1 / 30)
        cond.append(int(np.searchsorted(np.cumsum(part), 0.5) + 1))
    plain = np.minimum(np.array(points["placement"])[top30], NT50)
    groups = test["keyword_id"].to_numpy()[top30]
    report["mae_top30"] = {
        "entrants_landing_1_to_30": int(top30.sum()),
        "model_median_given_top30": _mae_block(np.array(cond), actual[top30], groups),
        "model_plain_point_nt50_as_51": _mae_block(plain, actual[top30], groups),
        "middle_of_top30_15_5": _mae_block(np.full(top30.sum(), 15.5), actual[top30], groups),
        "plan_quote": "The plan quotes about 6.5 places against 7.5 for guessing the middle. That figure was a simulation.",
    }
    head = report["headline"]
    report["summary"] = {"cells": head["forecasts"], "hit": head["summary"]["hit"]["rate"],
                         "interval_site": head["summary"]["hit"]["cluster_interval"],
                         "interval_search": report["headline_search_clusters"]["summary"]["hit"]["cluster_interval"],
                         "brand_cells_shown_apart": head["brand_forecasts_shown_apart"],
                         "baselines": {k: v["baseline_rate"] for k, v in head["baselines"].items()},
                         "best_baseline": head["best_baseline"], "skill": head["skill_over_best_baseline"],
                         "mae_top30": {k: v["mae"] for k, v in report["mae_top30"].items() if isinstance(v, dict)}}
    return report


def _mae_block(points: np.ndarray, actual: np.ndarray, groups: np.ndarray) -> dict[str, Any]:
    from .stats import cluster_bootstrap_ratio

    err = np.abs(points.astype(float) - actual.astype(float))
    if len(err) == 0:
        return {"count": 0, "mae": None, "interval_search": [None, None]}
    low, high = cluster_bootstrap_ratio(err.tolist(), [1.0] * len(err), groups.tolist())
    return {"count": int(len(err)), "mae": float(err.mean()), "interval_search": [low, high]}


# ---------------------------------------------------------------------------
# the synthetic pipeline (SYNTHETIC, never accuracy)


SYN_STRENGTH_FEATURES: tuple[str, ...] = (
    "st_s_cc_hc", "cl_s_cc_hc_q_top10", "cl_s_cc_hc_q_top20", "cl_s_cc_hc_q_top50", "cl_s_cc_hc_gap_top10",
    "cl_s_cc_hc_gap_top20", "cl_s_cc_hc_gap_top50", "pg_s_cc_hc_t10_q25", "pg_s_cc_hc_t10_q50", "pg_s_cc_hc_t10_q75",
    "pg_s_cc_hc_t21_50_q50", "pg_kd", "pg_log_volume",
)
SYN_FEATURES: tuple[str, ...] = (*SYN_STRENGTH_FEATURES, "fp_referring_domains", "fp_brand_search_volume",
                                 "fp_review_count", "fp_review_rating", "fp_links_planned", "fp_press_planned",
                                 "fp_pages_at_launch", "trust_pred")
SYN_STAGES: tuple[str, ...] = ("upload", "d7", "d14", "d30", "d60")
SYN_PULLS = 5


@dataclass(frozen=True)
class SyntheticDesign:
    """Plan size: 10 test sites times 20 target searches. Training sites come from the same world."""

    seed: int = 3
    test_sites: int = 10
    train_sites: int = 50
    niches: int = 3
    searches_per_niche: int = 30
    established_sites: int = 360
    test_share_seed: int = SEED


def _syn_world(design: SyntheticDesign):
    from .synth import World

    return World(design.seed, niches=design.niches, searches_per_niche=design.searches_per_niche,
                 established_sites=design.established_sites, new_sites=design.test_sites + design.train_sites)


def synthetic_upload_frame(world: Any) -> pd.DataFrame:
    """Upload-day cells for every new site on its target searches, with features built by features.assemble_frame
    (no historical pairs: only these cells get features). SYNTHETIC truth outcome at launch plus 90 days."""
    from .asof import add_months, month_start
    from .features import assemble_frame, prepare_page_rows, synthetic_footprint, synthetic_panel_rows, synthetic_strength, synthetic_upload_cells

    launches = [info.launch_date for info in world.new_sites.values()]
    first = add_months(month_start(min(launches)), -2)
    last = month_start(max(launches))
    months, month = [], max(first, world.start)
    while month <= last:
        months.append(month)
        month = add_months(month, 1)
    rows = synthetic_panel_rows(world, months)
    site_rows, meta = prepare_page_rows(rows)
    extra = synthetic_upload_cells(world, site_rows, meta)
    frame = assemble_frame(site_rows, meta, window_days=(1e9, 1e9 + 1), strength=synthetic_strength(world), evidence=None,
                           scorer=None, label=SYNTHETIC, extra_cells=extra)
    frame = frame[frame["upload_cell"].fillna(False).astype(bool)].reset_index(drop=True)
    frame = frame.merge(synthetic_footprint(world), on="site", how="left")
    terc = {info.text: info.tercile for info in world.searches}
    niche = {info.text: info.niche for info in world.searches}
    frame["difficulty"] = frame["keyword"].map(terc)
    frame["niche"] = frame["keyword"].map(niche)
    frame["search"] = frame["keyword"]
    frame["launch_date"] = frame["site"].map({s: i.launch_date for s, i in world.new_sites.items()})
    frame["input_date"] = [as_date(t) for t in frame["input_time"]]
    return frame


def synthetic_readings(world: Any, site_searches: Mapping[str, Sequence[str]], stages: Sequence[str] = SYN_STAGES[1:]
                       ) -> dict[tuple[str, str, str], Reading]:
    """Clean readings (reader.read_clean on SyntheticSource, 5 pulls) at each stage day. SYNTHETIC.

    A degraded reading gives an unknown position (None), never NT50.
    """
    from .reader import read_clean
    from .synth import SyntheticSource, fixed_clock
    from .types import PullSpec

    by_day: dict[date, set[str]] = defaultdict(set)
    wanted: list[tuple[str, str, str, date]] = []
    for site, searches in site_searches.items():
        launch = world.new_sites[site].launch_date
        for stage in stages:
            day = launch + timedelta(days=STAGE_DAYS[stage])
            if day >= world.end:
                continue
            for search in searches:
                by_day[day].add(search)
                wanted.append((site, search, stage, day))
    readings_by: dict[tuple[str, date], Any] = {}
    for day in sorted(by_day):
        clock = fixed_clock(datetime(day.year, day.month, day.day, 1, 0, tzinfo=timezone.utc), timedelta(seconds=1))
        source = SyntheticSource(world, clock)
        specs = [PullSpec(search=s, location_code=world.location_code) for s in sorted(by_day[day])]
        result = read_clean(source, specs, pulls=SYN_PULLS, sleep=lambda _s: None, now=clock)
        for reading in result.readings:
            readings_by[(reading.search, day)] = reading
    out: dict[tuple[str, str, str], Reading] = {}
    for site, search, stage, day in wanted:
        reading = readings_by.get((search, day))
        if reading is None:
            continue
        position = None if reading.degraded else reading.position_of(site)
        out[(site, search, stage)] = Reading(position=position, taken_on=day, label=SYNTHETIC, source="clean_reader")
    return out


def _syn_stage_rows(frame: pd.DataFrame, readings: Mapping[tuple[str, str, str], Reading], stage: str) -> pd.DataFrame:
    """Rows for one stage: as_of = launch plus the stage day, the readings up to that stage, lead to day 90."""
    rows = frame.copy()
    day_n = STAGE_DAYS[stage]
    rows["as_of"] = [d + timedelta(days=day_n) for d in rows["launch_date"]]
    rows["outcome_date"] = [d + timedelta(days=90) for d in rows["launch_date"]]
    rows["lead_days"] = 90 - day_n
    order = [s for s in SYN_STAGES[1:] if STAGE_DAYS[s] <= day_n]
    cols = []
    for site, search, as_of in zip(rows["site"], rows["search"], rows["as_of"]):
        got = [readings[(site, search, s)] for s in order if (site, search, s) in readings]
        values = reading_columns(got, as_of)
        known = [r for r in got if r.position is not None]
        values["reading_date"] = max((r.taken_on for r in known), default=None)
        cols.append(values)
    extra = pd.DataFrame(cols, index=rows.index)
    for col in extra.columns:
        rows[col] = extra[col]
    rows["new_to_search"] = 1.0
    rows["prev_date"] = None
    return rows


def run_synthetic(design: SyntheticDesign = SyntheticDesign(), *, log: Callable[[str], None] | None = None,
                  full_report: bool = True) -> dict[str, Any]:
    """The full pipeline on one synthetic world. SYNTHETIC: it proves the machinery works and is never accuracy.

    Trust and placement train on training new sites; upload-day forecasts for
    held-out new sites on their target searches go through `forecast_cells`;
    clean readings at day 7, 14, 30 and 60 update them; each stage is graded
    against the true day-90 position with grading rule v1, beside always NT50,
    persistence, the band prior and a strength-only placement.
    """
    from .markers import evaluate_marker
    from .trust import SYNTHETIC_FEATURES, fit_trust_model, synthetic_trust_frame

    world = _syn_world(design)
    frame = synthetic_upload_frame(world)
    sites = sorted(set(frame["site"]))
    ranked = sorted(sites, key=lambda s: stable_share(f"synthetic-test|{s}", design.test_share_seed + design.seed))
    test_sites = set(ranked[: design.test_sites])
    train_sites = set(sites) - test_sites
    # Trust: fitted on training sites only.
    tframe = synthetic_trust_frame(world)
    t_train = tframe[tframe["site"].isin(train_sites)].reset_index(drop=True)
    small_grid = ({"num_leaves": 4, "min_child_samples": 10}, {"num_leaves": 7, "min_child_samples": 20})
    trust_model = fit_trust_model(t_train, feature_set="synthetic_footprint", features=SYNTHETIC_FEATURES, grid=small_grid,
                                  seed=design.seed) if len(t_train) >= 10 else None
    trust_rows = tframe[["site", "st_s_cc_hc"] + [c for c in tframe.columns if c.startswith("fp_")]]
    trust_pred = dict(zip(trust_rows["site"], trust_model.predict(trust_rows))) if trust_model is not None else {}
    frame["trust_pred"] = frame["site"].map(trust_pred).astype(float)
    frame["search_group"] = frame["site"]  # sites are the split unit here
    train = frame[frame["site"].isin(train_sites)].reset_index(drop=True)
    test = frame[frame["site"].isin(test_sites)].reset_index(drop=True)
    placement = fit_placement(train, list(SYN_FEATURES), training_label=SYNTHETIC, seed=design.seed, group_col="site",
                              name="synthetic-placement")
    strength_only = fit_placement(train, list(SYN_STRENGTH_FEATURES), training_label=SYNTHETIC, seed=design.seed,
                                  group_col="site", name="synthetic-strength-only")
    readings = synthetic_readings(world, {s: world.new_sites[s].targets for s in sites})
    # Updater training rows: training sites at every stage after upload, with a cross-fitted prior.
    train_prior = cross_fitted_prior(train, list(SYN_FEATURES), label=SYNTHETIC, seed=design.seed, group_col="site")
    up_rows, priors = [], []
    for stage in SYN_STAGES[1:]:
        rows = _syn_stage_rows(train, readings, stage)
        rows["stage"] = stage
        up_rows.append(rows)
        priors.append(train_prior)
    up = pd.concat(up_rows, ignore_index=True)
    up_prior = np.vstack(priors)
    cols = prior_columns(up_prior)
    for col in cols.columns:
        up[col] = cols[col].to_numpy()
    is_val = np.array([stable_share(f"syn-val|{s}", design.seed) < VALIDATION_SHARE for s in up["site"]])
    fit_rows, val_rows = up[~is_val].reset_index(drop=True), up[is_val].reset_index(drop=True)
    table = fit_table(fit_rows, training_label=SYNTHETIC, seed=design.seed, group_col="site")
    try:
        umodel = fit_model(fit_rows, training_label=SYNTHETIC, seed=design.seed, group_col="site",
                           features=[f for f in MODEL_FEATURES])
    except UpdaterError:
        umodel = None
    updater = choose_version(table, umodel, val_rows, up_prior[is_val], search_col="search")
    lookup = test.set_index(["site", "search"])

    def provider(site: str, search: str, day: date) -> tuple[dict[str, Any], dict[str, date | None]]:
        row = lookup.loc[(site, search)]
        values = {f: row[f] for f in SYN_FEATURES if f in row.index}
        return values, {"input_date": as_date(row["input_date"]), "strength_date": as_date(row["input_date"])}

    models = ForecastModels(placement=placement, updater=updater, features=provider)
    per_stage: dict[str, Any] = {}
    truth = {(s, q): int(world.true_position(s, q, world.new_sites[s].launch_date + timedelta(days=90)))
             for s, q in zip(test["site"], test["search"])}
    train_cells = [GradedCell(site=s, search=q, point=int(p), actual=int(p), difficulty=d, source="synthetic")
                   for s, q, p, d in zip(train["site"], train["search"], train["position_out"], train["difficulty"])]
    strength_points = [dist_median(d) for d in strength_only.distributions(test)]
    brand_names = {s: tuple((world.agency_form(s) or {}).get("brand_names") or ()) for s in test_sites}
    for stage in SYN_STAGES:
        day_n = STAGE_DAYS[stage]
        forecasts: list[Forecast] = []
        for site in sorted(test_sites):
            info = world.new_sites[site]
            as_of = info.launch_date + timedelta(days=day_n)
            site_reads = {q: [readings[(site, q, s)] for s in SYN_STAGES[1:]
                              if STAGE_DAYS[s] <= day_n and (site, q, s) in readings] for q in info.targets}
            forecasts.extend(forecast_cells(site, list(info.targets), as_of, stage, site_reads,
                                            world.agency_form(site), models,
                                            outcome_date=info.launch_date + timedelta(days=90)))
        by_key = {(f.site, f.search): f for f in forecasts}
        rows = test.copy()
        rows["point"] = [by_key[(s, q)].point for s, q in zip(rows["site"], rows["search"])]
        rows["actual"] = [truth[(s, q)] for s, q in zip(rows["site"], rows["search"])]
        persistence = []
        for s, q in zip(rows["site"], rows["search"]):
            got = [readings[(s, q, st)] for st in SYN_STAGES[1:] if STAGE_DAYS[st] <= day_n and (s, q, st) in readings]
            known = [r for r in got if r.position is not None]
            persistence.append(int(known[-1].position) if known else NT50)
        cells = [GradedCell(site=s, search=q, point=int(p), actual=int(a), difficulty=d, niche=n, source="synthetic",
                            is_brand=is_brand_search(s, q, brand_names.get(s, ())),
                            stage=stage, lead_days=90 - day_n, label=by_key[(s, q)].label,
                            range_low=by_key[(s, q)].range_low, range_high=by_key[(s, q)].range_high)
                 for s, q, p, a, d, n in zip(rows["site"], rows["search"], rows["point"], rows["actual"],
                                             rows["difficulty"], rows["niche"])]
        outcome_dates = [world.new_sites[s].launch_date + timedelta(days=90) for s in rows["site"]]
        as_ofs = [world.new_sites[s].launch_date + timedelta(days=day_n) for s in rows["site"]]
        report = backtest_report(cells, {"persistence": persistence, "site_strength": strength_points}, stage=stage,
                                 as_of=max(outcome_dates), model_version=updater.version() if stage != "upload" else placement.model_version(),
                                 outcome_dates=outcome_dates, as_ofs=as_ofs, band_prior_training=train_cells,
                                 source="synthetic", notes=["SYNTHETIC. Never accuracy."],
                                 held_out=[c.site in test_sites for c in cells])
        if stage == "upload":
            report["marker_3_synthetic"] = evaluate_marker(3, report)
        if stage == "d30":
            report["marker_4_synthetic"] = evaluate_marker(4, report)
        per_stage[stage] = report if full_report else _stage_brief(report)
    summary = {stage: _stage_brief(rep) if full_report else rep for stage, rep in per_stage.items()}
    return {"label": SYNTHETIC, "seed": design.seed, "design": asdict(design), "train_sites": len(train_sites),
            "test_sites": len(test_sites), "test_cells": int(len(test)),
            "updater": {"in_use": updater.in_use, "reason": updater.reason, "comparison": updater.comparison},
            "placement": {"version": placement.model_version(), "temperature": placement.temperature,
                          "range_level": placement.range_level, "calibration": placement.calibration},
            "stages": per_stage if full_report else None, "summary": summary}


def _stage_brief(report: Mapping[str, Any]) -> dict[str, Any]:
    head = report["headline"]
    page = head["summary"]["page_one"]
    return {"cells": head["forecasts"], "hit": head["summary"]["hit"]["rate"],
            "always_nt50": head["baselines"]["always_nt50"]["baseline_rate"],
            "persistence": head["baselines"]["persistence"]["baseline_rate"],
            "band_prior": head["baselines"].get("band_prior", {}).get("baseline_rate"),
            "site_strength": head["baselines"]["site_strength"]["baseline_rate"],
            "page_one_calls": page["calls"], "page_one_outcomes": page["outcomes"],
            "precision": page["precision"]["rate"], "recall": page["recall"]["rate"], "label": SYNTHETIC}


def synthetic_seed_spread(seeds: Sequence[int], base: SyntheticDesign = SyntheticDesign(),
                          log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The day-60 hit rate at plan size (10 sites times 20 searches) across seeds. SYNTHETIC."""
    rows = []
    for seed in seeds:
        design = SyntheticDesign(**{**asdict(base), "seed": int(seed)})
        result = run_synthetic(design, full_report=False, log=log)
        brief = result["summary"]["d60"]
        rows.append({"seed": int(seed), **brief, "updater_in_use": result["updater"]["in_use"]})
        if log:
            log(f"seed {seed}: d60 hit {brief['hit']}, NT50 {brief['always_nt50']}")

    def spread(key: str) -> dict[str, Any]:
        vals = np.array([r[key] for r in rows if r[key] is not None], dtype=float)
        if not len(vals):
            return {"count": 0}
        return {"count": int(len(vals)), "mean": float(vals.mean()), "sd": float(vals.std(ddof=1)) if len(vals) > 1 else None,
                "min": float(vals.min()), "p05": float(np.quantile(vals, 0.05)), "p95": float(np.quantile(vals, 0.95)),
                "max": float(vals.max())}

    diffs = [r["hit"] - r["always_nt50"] for r in rows]
    return {"label": SYNTHETIC, "seeds": list(map(int, seeds)), "plan_size": "10 sites times 20 target searches",
            "per_seed": rows, "d60_hit": spread("hit"), "d60_always_nt50": spread("always_nt50"),
            "d60_persistence": spread("persistence"),
            "d60_minus_always_nt50": {"mean": float(np.mean(diffs)), "min": float(np.min(diffs)), "max": float(np.max(diffs))},
            "share_of_seeds_with_hit_at_least_0_90": float(np.mean([r["hit"] >= 0.90 for r in rows])),
            "share_of_seeds_with_precision_measurable": float(np.mean([(r["page_one_calls"] or 0) >= MIN_COUNT_TO_PRINT_RATE
                                                                       for r in rows])),
            "note": "SYNTHETIC. The spread shows what a marker can and cannot detect at 200 forecasts on 10 sites."}


# ---------------------------------------------------------------------------
# helpers and CLI


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, PlacementModel):
        return obj.model_version()
    return obj


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _note(log: Callable[[str], None] | None, message: str) -> None:
    if log:
        log(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}")


def synthetic_spec(seeds: Sequence[int], base: SyntheticDesign) -> BacktestSpec:
    return BacktestSpec(
        name="synthetic_pipeline", dataset="synth.World (SYNTHETIC)", split={"unit": "new site", "test_sites": base.test_sites,
                                                                             "train_sites": base.train_sites},
        time_cutoff="none: sites are split", windows_days={"stages": [STAGE_DAYS[s] for s in SYN_STAGES], "outcome_day": [90]},
        feature_set=list(SYN_FEATURES), model_versions={"placement": "placement-v1", "updater": "updater-table-v1 / updater-model-v2",
                                                        "trust": "trust-v1 synthetic footprint"},
        baselines=["always_nt50", "persistence", "band_prior", "site_strength"],
        thresholds={"grading_rule": RULE_V1.version, "reader_pulls": SYN_PULLS, "seeds": list(map(int, seeds)),
                    "design": asdict(base)},
        cells="Every held-out new site on its 20 target searches, forecast at upload and updated at day 7, 14, 30 and 60.",
        notes=["SYNTHETIC. It proves the machinery and shows the spread at plan size. Never accuracy."])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.forecast.backtest")
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prereg", help="freeze the panel designs before scoring")
    pre.add_argument("--design-version", type=int, default=1, help="1 is frozen and scored; k >= 2 writes prereg-v<k>.json")
    pre.add_argument("--reason", default=None, help="why a new design version is needed (required for k >= 2)")
    run_cmd = sub.add_parser("run", help="score the panel designs once (REAL), versioned reports")
    run_cmd.add_argument("--rehearsal", action="store_true", help="debug inside the training side only")
    run_cmd.add_argument("--design-version", type=int, default=1, help="the frozen design version to score (once)")
    syn = sub.add_parser("synthetic", help="SYNTHETIC end to end run and the seed spread at plan size")
    syn.add_argument("--seeds", type=int, default=20)
    syn.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    if args.command == "prereg":
        print(json.dumps(prereg_panel(log=_log, version=args.design_version, reason=args.reason), indent=2))
        return 0
    if args.command == "run":
        print(json.dumps(_clean(run_panel(rehearsal=args.rehearsal, log=_log, version=args.design_version)), indent=1,
                         default=str)[:20000])
        return 0
    base = SyntheticDesign(seed=args.seed)
    seeds = list(range(100, 100 + args.seeds))
    spec = synthetic_spec(seeds, base)
    sha = freeze_spec(spec)
    one = run_synthetic(base, log=_log)
    spread = synthetic_seed_spread(seeds, base, log=_log)
    manifest = write_run(spec.name, {"label": SYNTHETIC, "kind": "synthetic_pipeline", "one_world": one, "seed_spread": spread},
                         prereg_sha256=sha)
    print(json.dumps(_clean({"manifest": manifest, "one_world_summary": one["summary"], "updater": one["updater"],
                             "seed_spread": {k: v for k, v in spread.items() if k != "per_seed"}}), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
