"""The paid cohort path: past new sites joined into upload cells, a bundle trained on them, and the cohort backtest.

This is the plan's route to marker 3 (build step 6): an upload-day forecast for a brand-new site,
trained and tested on past new sites, including searches where the site never ranked.

What is REAL here: past result pages (single country-level pages, as DataForSEO returned them, or
the free sample pages), the positions read from them, dated strength list values, start-date
evidence and the launch-era page text of each site. The launch date of a past site is an ESTIMATE
with its uncertainty in days (sources/newsites.py). What is PREDICTED: every forecast, every
content match score and every probability. What is SYNTHETIC: every table built by
trainingset.synthetic_training_set, every bundle trained on them and every number scored on them.
A SYNTHETIC number is never accuracy and never a marker result.

The join (`join_upload_cells`), one row per (site, target search):

- The launch date D is an estimate: the day of the first Common Crawl capture of real content, the
  true launch up to the uncertainty earlier. The result page, strength and start-date evidence are
  dated on or before the EARLIEST plausible launch date, D minus the launch uncertainty, so the
  site's own later pages cannot be in them. That date is the row's `inputs_as_of`.
- OUR READING: the site's own launch page text counts as known on the day it was captured, never
  after D (it is the page being uploaded; text captured after D raises LeakError). Its capture day
  is stored per row as `content_date`. The row's `as_of` is the newest date of any input it used:
  max(inputs_as_of, content_date). So every input is dated on or before `as_of`, as spec section 2
  asks, and `lead_days` (outcome date minus `as_of`) is never overstated by the launch uncertainty.
  A row with no text used (or a join without features) has as_of = inputs_as_of.
- The input page is the latest stored page of the search dated on or before `inputs_as_of`, at most
  JoinRule.input_max_age_days old, not at poison risk, and read to depth 50. A cell with no such
  page is dropped and counted by reason. It is never guessed.
- The outcome is the site's position on the page of the search closest to D + 90 days inside
  JoinRule.outcome_window_days, not at poison risk and read to depth 50. The page is chosen without
  looking at the site, so whether a cell is kept never depends on its outcome: a cell whose window
  holds no clean page at least 50 deep is dropped and counted (outcome_pages_shallow), whether or
  not the site is on a shorter page. A site absent from the chosen page is NOT IN TOP 50, a REAL
  observation. Never-ranked cells are kept: they are the point of this data.
- A cell whose launch uncertainty is unknown or larger than JoinRule.max_launch_uncertainty_days
  is dropped and counted.
- Features are the live provider's own (forecasting.feature_row) on a page built from the input
  page, so a bundle trained here runs unchanged on the live path. Strength goes through
  features.cached_strength (AsOfGuard inside), start-date evidence through features.start_features,
  the page itself through an AsOfGuard. Difficulty and volume come only from the input page's own
  record (spec section 8: the parser keeps kd dated by its page and volume known 45 days after its
  month). The universe's metrics describe searches today and are never features.
- content_match is the launch-era page text of the site scored against the search with the
  scorer class the live path records (content.ContentScorer), NaN when no text is on file. The
  bundle manifest names the backend under content_match_backend. It is a model input when
  forecasting.PLACEMENT_FEATURES lists it and the training rows hold a value. The live path then uses
  a live score only from that same backend (forecasting.content_feature), and the cohort backtest
  refuses to score with another backend: scores of two backends are on different scales.
- Brand searches (backtest.is_brand_search) are flagged, left out of training and of the headline.
  Excluded sites are refused. A cell table that repeats a (site, search), puts a site or a search
  group on both sides of the split, or whose split the frozen split rule does not reproduce, is refused.

Training (`train_paid`) reads the 'train' split only, and only rows whose outcome page is dated
before the time cutoff: the earliest `as_of` of any 'test' row. So the bundle never learned an
outcome dated after a forecast it is scored on. The 'test' split is never joined by training.

The cohort backtest (`freeze_design`, `score_design`) follows the scored-run protocol of spec
section 6 through features.freeze_prereg, begin_scored_run and finish_scored_run. Each graded cell's
held-out flag is computed (a site and a search group not on the train side, a forecast date on or
after the cutoff), never assumed. The held-out test rows of one training set are scored once,
whatever the design name: a later look at the same rows (same test-row keys, or the same cell
table) needs design version 2 or later with its reason, and is reported as a SECOND LOOK, PROVISIONAL,
with no marker verdict. Its report is
built by backtest.backtest_report, which calls report.report_from_cells. On REAL tables the
report's source is backtest and markers.evaluate_marker(3, report) decides. On SYNTHETIC tables the
source is synthetic, every line says SYNTHETIC and the marker result is NOT A MARKER RESULT.

Traffic curves and exact positions are not read here. The traffic curves are the label source of
the trust model (part 5), and exact positions describe the pull date, not the launch month, so
neither is a placement input or a day 90 outcome.

Money: nothing here spends or calls a network service.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from . import trainingset as ts
from .asof import AsOfGuard, Dated, LeakError, as_date, utc_iso
from .chain import canonical_json, sha256_text
from .config import NT50, OUTCOME_DAY, SPEC_BASELINES, TOP_DEPTH, ForecastPaths
from .types import PREDICTED, REAL, SYNTHETIC, CleanReading, SitePosition, is_excluded_site

PAIDSET_VERSION = "paidset-v1"
SYNTHETIC_DATASETS: frozenset[str] = frozenset({ts.SYNTHETIC_DATASET})
DEFAULT_SET_NAME = "cells"  # cache/trainingset/cells: the REAL training-set folder `paid cells` writes
DEFAULT_SYNTHETIC_SET_NAME = "synthetic"
DEFAULT_LABS_PANEL = "panel_labs"
DESIGN_NAME = "cohort-upload"
STAGE = "upload"
CONTENT_COLUMN = "content_match"
CONTENT_BACKEND_KEY = "content_match_backend"
SEED = 20261122

# Drop reasons, in the order they are checked. Each dropped cell gets exactly one.
DROP_REASONS: tuple[str, ...] = (
    "split_excluded",
    "launch_date_missing",
    "launch_uncertainty_unknown",
    "launch_uncertainty_too_large",
    "search_has_no_pages",
    "no_page_on_or_before_launch",
    "input_page_too_old",
    "input_pages_poison_risk",
    "input_pages_shallow",
    "no_outcome_page_in_window",
    "outcome_pages_poison_risk",
    "outcome_pages_shallow",
    "outcome_unknown_shallow_page",
    "outcome_on_or_after_test_cutoff",
)
DROP_TEXT: dict[str, str] = {
    "split_excluded": "the frozen split rule left the cell out (its search group is used by a test site)",
    "launch_date_missing": "the site has no estimated launch date",
    "launch_uncertainty_unknown": "the launch date has no stated uncertainty",
    "launch_uncertainty_too_large": "the launch date is more uncertain than the frozen limit",
    "search_has_no_pages": "the search has no stored result page",
    "no_page_on_or_before_launch": "no page of the search is dated on or before the earliest plausible launch date",
    "input_page_too_old": "every earlier page is older than the frozen limit",
    "input_pages_poison_risk": "every recent earlier page is at poison risk",
    "input_pages_shallow": "every usable earlier page stops before rank 50",
    "no_outcome_page_in_window": "no page of the search falls in the outcome window",
    "outcome_pages_poison_risk": "every page in the outcome window is at poison risk",
    "outcome_pages_shallow": "every clean page in the outcome window stops before rank 50, so the outcome is unknown",
    "outcome_unknown_shallow_page": "the site is absent from an outcome page that stops before rank 50, so the outcome is unknown",
    "outcome_on_or_after_test_cutoff": "a training outcome dated on or after the earliest test forecast date",
}


class PaidSetRefusal(ValueError):
    """The paid cohort path refuses something that would leak, mix labels or fake a result. The message says why."""


# ---------------------------------------------------------------------------
# the frozen join rule


@dataclass(frozen=True)
class JoinRule:
    """The frozen rule that turns cells and past result pages into upload cells. Hashed with its code.

    OUR READING of the plan, each value frozen here and written into every bundle and design:
    input_max_age_days 62 (about two monthly pages; a page five months old is not what a forecast
    would have seen), max_launch_uncertainty_days 45 (the Common Crawl gap that still keeps day 90
    inside the plan's month 3), the outcome window of days 60 to 120 after the launch date (the
    plan's month 3 stand-in for day 90: a month either side, because past pages come about once a
    month or less; closest to day 90 wins among clean pages at least 50 deep, ties go to the earlier
    page, the real day is stored), and the month 1 reading for the updater on days 14 to 45 (closest
    to day 30). v2: the outcome page must be 50 deep before it is chosen (v1 dropped only the sites
    absent from a short page, so the kept set depended on the outcome), and the row's as_of covers
    the launch page text it used.
    """

    version: str = "join-rule-v2"
    input_max_age_days: int = 62
    max_launch_uncertainty_days: int = 45
    outcome_day: int = OUTCOME_DAY
    outcome_window_days: tuple[int, int] = (60, 120)
    reading_day: int = 30
    reading_window_days: tuple[int, int] = (14, 45)
    min_depth: int = TOP_DEPTH

    def payload(self) -> dict[str, Any]:
        out = asdict(self)
        out["outcome_window_days"] = list(self.outcome_window_days)
        out["reading_window_days"] = list(self.reading_window_days)
        return out

    def rule_hash(self) -> str:
        code = "".join(inspect.getsource(fn) for fn in _RULE_FUNCTIONS)
        return sha256_text(canonical_json({**self.payload(), "code": code}))


DEFAULT_JOIN_RULE = JoinRule()


# ---------------------------------------------------------------------------
# the training-set folder


@dataclass
class TrainingSetFiles:
    """One training-set folder as read: its label, cells, universe, page text and the panel it is joined with."""

    folder: Path
    label: str
    cells: pd.DataFrame
    universe: pd.DataFrame | None
    page_texts: dict[str, list[dict[str, Any]]]
    panel_dir: Path
    meta: dict[str, Any]
    cells_sha256: str


def _file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_set_dir(paths: ForecastPaths, *, synthetic: bool = False) -> Path:
    return paths.cache / "trainingset" / (DEFAULT_SYNTHETIC_SET_NAME if synthetic else DEFAULT_SET_NAME)


def default_labs_panel(paths: ForecastPaths) -> Path:
    return paths.cache / DEFAULT_LABS_PANEL


def _labels_of(frame: pd.DataFrame | None) -> set[str]:
    if frame is None or "label" not in frame.columns:
        return set()
    return {str(v) for v in frame["label"].dropna().unique()}


def check_cell_table(cells: pd.DataFrame, meta: Mapping[str, Any], where: str | Path = "the cell table") -> None:
    """Refuse a cell table the frozen rules did not write: one row per (site, search), split train, test or excluded,
    no site and no search group on both sides, the split rule recorded in the meta file the frozen one, and the split
    column exactly what that rule gives on these cells (trainingset.assign_split). The split is never trusted as written."""
    repeated = cells[cells.duplicated(["site", "keyword_id"], keep=False)]
    if len(repeated):
        first = repeated.iloc[0]
        raise PaidSetRefusal(f"{where}: {len(repeated)} rows repeat a (site, search), for example {first['site']} and "
                             f"{first['keyword']!r}. A cell is one (site, search): it is never trained or graded twice.")
    unknown = sorted({str(v) for v in cells["split"].unique()} - set(ts.SPLITS))
    if unknown:
        raise PaidSetRefusal(f"{where}: unknown split values {unknown}. A split is one of {', '.join(ts.SPLITS)}.")
    both = ts.check_split(cells)
    if both["sites_on_both_sides"] or both["groups_on_both_sides"]:
        raise PaidSetRefusal(f"{where}: {both['sites_on_both_sides']} sites and {both['groups_on_both_sides']} search groups "
                             "are on both sides of the split. A held-out cell is a held-out site on a held-out search group.")
    expected = ts.DEFAULT_SPLIT_RULE.rule_hash()
    if meta.get("split_rule_hash") != expected:
        raise PaidSetRefusal(f"{where}: the meta file records split rule {str(meta.get('split_rule_hash'))[:12]}, not the frozen "
                             f"one ({expected[:12]}). Write the folder again with the frozen rule.")
    again = ts.assign_split(cells.to_dict("records"), ts.DEFAULT_SPLIT_RULE)
    differ = sum(a != str(b) for a, b in zip(again, cells["split"]))
    if differ:
        raise PaidSetRefusal(f"{where}: the frozen split rule gives another split for {differ} cells than the table holds. "
                             "The split column was edited or written by another rule. The folder is refused.")


def open_training_set(folder: str | Path, *, panel_dir: str | Path | None = None,
                      paths: ForecastPaths | None = None) -> TrainingSetFiles:
    """Read a training-set folder (trainingset.write_cells_set or synthetic_training_set) and check its labels.

    The label is REAL when REAL.json is there and SYNTHETIC when SYNTHETIC.json is there. Both, or a
    cells, universe, traffic or positions table whose label column says otherwise, is refused: labels
    are never mixed. A cell of an excluded site is refused, and so is a cell table check_cell_table refuses
    (a repeated (site, search), a site or search group on both sides, a split the frozen rule does not
    reproduce). The panel is `panel_dir`, else the folder's
    own panel/ subfolder, else (REAL only) cache/panel_labs.
    """
    base = Path(folder)
    cells_path = base / ts.CELLS_FILE
    if not cells_path.is_file():
        raise PaidSetRefusal(f"{base} holds no {ts.CELLS_FILE}. Write it with: python -m aeo.ranksim forecast paid cells "
                             "(REAL) or paid synthetic (SYNTHETIC).")
    real = (base / ts.REAL_META_FILE).is_file()
    synthetic = (base / ts.SYNTHETIC_META_FILE).is_file()
    if real and synthetic:
        raise PaidSetRefusal(f"{base} holds both {ts.REAL_META_FILE} and {ts.SYNTHETIC_META_FILE}. Labels are never mixed.")
    if not real and not synthetic:
        raise PaidSetRefusal(f"{base} has no {ts.REAL_META_FILE} or {ts.SYNTHETIC_META_FILE}, so its label is unknown. "
                             "It is refused.")
    label = REAL if real else SYNTHETIC
    meta = json.loads((base / (ts.REAL_META_FILE if real else ts.SYNTHETIC_META_FILE)).read_text(encoding="utf-8"))
    if meta.get("label") not in (None, label):
        raise PaidSetRefusal(f"{base}: the meta file says {meta.get('label')} but its name says {label}.")
    cells = pd.read_parquet(cells_path)
    missing = [c for c in ts.CELL_COLUMNS if c not in cells.columns]
    if missing:
        raise PaidSetRefusal(f"{cells_path} lacks the columns {missing}.")
    universe = pd.read_parquet(base / ts.UNIVERSE_FILE) if (base / ts.UNIVERSE_FILE).is_file() else None
    for name, frame in (("cells", cells), ("universe", universe)):
        found = _labels_of(frame)
        if found - {label}:
            raise PaidSetRefusal(f"The {name} table of {base} is labelled {sorted(found)} in a {label} folder. "
                                 "Labels are never mixed.")
    for extra in ("traffic_months.parquet", "traffic_targets.parquet", "positions.parquet", "position_targets.parquet"):
        if (base / extra).is_file():
            found = _labels_of(pd.read_parquet(base / extra, columns=["label"]))
            if found - {label}:
                raise PaidSetRefusal(f"{base / extra} is labelled {sorted(found)} in a {label} folder. Labels are never mixed.")
    bad = sorted({str(s) for s in cells["site"] if is_excluded_site(str(s))})
    if bad:
        raise PaidSetRefusal(f"The cells hold an excluded site ({len(bad)} of them). Excluded sites are never used to "
                             "train or test. The folder is refused.")
    check_cell_table(cells, meta, base)
    if panel_dir is not None:
        panel = Path(panel_dir)
    elif (base / "panel").is_dir():
        panel = base / "panel"
    elif label == REAL and paths is not None:
        panel = default_labs_panel(paths)
    else:
        raise PaidSetRefusal(f"No panel of past result pages is given for {base}. Pass the panel folder.")
    return TrainingSetFiles(base, label, cells, universe, ts.load_page_texts(base / ts.PAGE_TEXT_FILE), panel, meta,
                            _file_sha(cells_path))


# ---------------------------------------------------------------------------
# past result pages


@dataclass(frozen=True)
class Page:
    """One stored result page of one search. REAL (SYNTHETIC from the simulated world). `ranks` is the best
    organic rank of each site on the page, at any depth."""

    keyword_id: str
    time: pd.Timestamp
    day: date
    depth: int
    poison: bool
    dataset: str
    ranks: Mapping[str, int]
    kd: float | None
    volume: float | None

    def rank_of(self, site: str) -> int | None:
        return self.ranks.get(site)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_pages(panel_dir: str | Path, keyword_ids: Sequence[str], *, label: str) -> dict[str, list[Page]]:
    """Every stored page of the given searches, oldest first. A panel whose datasets do not match the label is refused."""
    from .sources.panel import load_panel

    folder = Path(panel_dir)
    if not folder.is_dir():
        raise PaidSetRefusal(f"The panel folder {folder} does not exist.")
    columns = ["dataset", "keyword_id", "snapshot_time", "snapshot_date", "rank", "site", "kd", "search_volume", "poison_risk"]
    frame = load_panel(columns, panel_dir=folder)
    datasets = {str(d) for d in frame["dataset"].dropna().unique()}
    if label == SYNTHETIC and datasets - SYNTHETIC_DATASETS:
        raise PaidSetRefusal(f"SYNTHETIC cells cannot be joined with REAL pages ({sorted(datasets - SYNTHETIC_DATASETS)}). "
                             "Labels are never mixed.")
    if label == REAL and datasets & SYNTHETIC_DATASETS:
        raise PaidSetRefusal(f"REAL cells cannot be joined with SYNTHETIC pages ({sorted(datasets & SYNTHETIC_DATASETS)}). "
                             "Labels are never mixed.")
    wanted = set(str(k) for k in keyword_ids)
    frame = frame[frame["keyword_id"].astype(str).isin(wanted)]
    out: dict[str, list[Page]] = {}
    if frame.empty:
        return out
    frame = frame.assign(snapshot_time=pd.to_datetime(frame["snapshot_time"], utc=True))
    for (kid, moment), group in frame.groupby(["keyword_id", "snapshot_time"], sort=True):
        ranks: dict[str, int] = {}
        for site, rank in zip(group["site"].astype(str), pd.to_numeric(group["rank"], errors="coerce")):
            if not math.isfinite(rank):
                continue
            if site not in ranks or int(rank) < ranks[site]:
                ranks[site] = int(rank)
        depth = int(pd.to_numeric(group["rank"], errors="coerce").max()) if len(group) else 0
        kd = next((v for v in (_num(x) for x in group["kd"]) if v is not None), None)
        volume = next((v for v in (_num(x) for x in group["search_volume"]) if v is not None), None)
        out.setdefault(str(kid), []).append(Page(
            keyword_id=str(kid), time=pd.Timestamp(moment), day=pd.Timestamp(moment).date(), depth=depth,
            poison=bool(group["poison_risk"].fillna(False).astype(bool).any()), dataset=str(group["dataset"].iloc[0]),
            ranks=ranks, kd=kd, volume=volume))
    return out


def _choose_input(pages: Sequence[Page], as_of: date, rule: JoinRule) -> tuple[Page | None, str | None]:
    """The latest page on or before as_of, at most input_max_age_days old, not at poison risk, at least 50 deep."""
    earlier = [p for p in pages if p.day <= as_of]
    if not earlier:
        return None, "no_page_on_or_before_launch"
    recent = [p for p in earlier if (as_of - p.day).days <= rule.input_max_age_days]
    if not recent:
        return None, "input_page_too_old"
    clean = [p for p in recent if not p.poison]
    if not clean:
        return None, "input_pages_poison_risk"
    deep = [p for p in clean if p.depth >= rule.min_depth]
    if not deep:
        return None, "input_pages_shallow"
    return max(deep, key=lambda p: p.time), None


def _choose_outcome(pages: Sequence[Page], launch: date, rule: JoinRule) -> tuple[Page | None, str | None]:
    """The page closest to launch + outcome_day inside the outcome window, not at poison risk, at least 50 deep.
    Ties: the earlier page. It never looks at any site on the page, so the keep or drop decision is outcome-blind."""
    low, high = rule.outcome_window_days
    inside = [p for p in pages if low <= (p.day - launch).days <= high]
    if not inside:
        return None, "no_outcome_page_in_window"
    clean = [p for p in inside if not p.poison]
    if not clean:
        return None, "outcome_pages_poison_risk"
    deep = [p for p in clean if p.depth >= rule.min_depth]
    if not deep:
        return None, "outcome_pages_shallow"
    return min(deep, key=lambda p: (abs((p.day - launch).days - rule.outcome_day), p.time)), None


def _choose_reading(pages: Sequence[Page], launch: date, before: date, rule: JoinRule) -> Page | None:
    """The month 1 page for the updater: closest to launch + reading_day inside the reading window, before the outcome."""
    low, high = rule.reading_window_days
    inside = [p for p in pages if low <= (p.day - launch).days <= high and p.day < before and not p.poison]
    if not inside:
        return None
    return min(inside, key=lambda p: (abs((p.day - launch).days - rule.reading_day), p.time))


def _page_reading(page: Page, label: str) -> CleanReading:
    """The input page as the reading the live provider reads (top 50 only, excluded sites dropped there)."""
    positions = tuple(SitePosition(site=site, position=int(rank), pull_positions=(int(rank),))
                      for site, rank in sorted(page.ranks.items(), key=lambda kv: (kv[1], kv[0])) if rank <= TOP_DEPTH)
    return CleanReading(reading_id=f"page:{page.keyword_id}:{utc_iso(page.time.to_pydatetime())}", search=page.keyword_id,
                        location_code=0, device="desktop", taken_at=utc_iso(page.time.to_pydatetime()), pulls_total=1,
                        pulls_kept=1, dropped=(), degraded=False, pulls_not_independent=False, positions=positions,
                        label=label, reader_version="single-past-page")


# ---------------------------------------------------------------------------
# strength, start evidence and the content scorer


class PrefetchedStrength:
    """A strength function that looks every (site, date) up once, through the wrapped function (whose own
    AsOfGuard checks each value), and serves later calls from that table."""

    def __init__(self, inner: Callable[[pd.DataFrame], pd.DataFrame]) -> None:
        self.inner = inner
        self.rows: dict[tuple[str, date], dict[str, Any]] = {}
        self.failure: str | None = None

    def prefetch(self, site_dates: pd.DataFrame) -> None:
        """Look the missing pairs up once. A LeakError always propagates. Any other read failure is kept: every
        later call then raises it at once, and forecasting.feature_row turns it into missing (NaN) values with a note."""
        if self.failure is not None:
            raise RuntimeError(self.failure)
        try:
            self._prefetch(site_dates)
        except LeakError:
            raise
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            self.failure = f"{type(error).__name__}: {error}"
            raise RuntimeError(self.failure) from error

    def _prefetch(self, site_dates: pd.DataFrame) -> None:
        want = site_dates[["site", "as_of"]].copy()
        want["as_of"] = [as_date(d) for d in want["as_of"]]
        want = want.drop_duplicates()
        missing = want[[(s, d) not in self.rows for s, d in zip(want["site"], want["as_of"])]]
        if missing.empty:
            return
        found = self.inner(missing.reset_index(drop=True))
        for record in found.to_dict("records"):
            key = (str(record["site"]), as_date(record["as_of"]))
            self.rows.setdefault(key, record)
        for site, day in zip(missing["site"], missing["as_of"]):
            self.rows.setdefault((str(site), day), {"site": site, "as_of": day})

    def __call__(self, site_dates: pd.DataFrame) -> pd.DataFrame:
        self.prefetch(site_dates)
        records = [dict(self.rows[(str(s), as_date(d))]) for s, d in zip(site_dates["site"], site_dates["as_of"])]
        out = pd.DataFrame.from_records(records)
        out["as_of"] = [as_date(d) for d in out["as_of"]]
        return out


def synthetic_world(meta: Mapping[str, Any]) -> Any:
    """The synthetic world a SYNTHETIC folder was built from, rebuilt from its world_params. SYNTHETIC."""
    from .synth import World

    params = meta.get("world_params") or {}
    if not params:
        raise PaidSetRefusal("The SYNTHETIC folder records no world_params, so its world cannot be rebuilt. Build it "
                             "again with: python -m aeo.ranksim forecast paid synthetic")
    if not params.get("knobs_default"):
        raise PaidSetRefusal("The SYNTHETIC folder was built with changed world knobs, which are not recorded. Pass a "
                             "strength function for it.")
    return World(int(params["seed"]), niches=int(params["niches"]), searches_per_niche=int(params["searches_per_niche"]),
                 established_sites=int(params["established_sites"]), new_sites=int(params["new_sites"]),
                 start_month=date.fromisoformat(str(params["start_month"])), months=int(params["months"]),
                 location_code=int(params["location_code"]))


def default_strength(files: TrainingSetFiles, paths: ForecastPaths | None) -> Callable[[pd.DataFrame], pd.DataFrame]:
    """REAL: features.cached_strength under the data folder. SYNTHETIC: the rebuilt world's dated lists."""
    if files.label == SYNTHETIC:
        from .features import synthetic_strength

        world = synthetic_world(files.meta)
        unknown = sorted(set(files.cells["site"].astype(str)) - set(world.new_sites))
        if unknown:
            raise PaidSetRefusal(f"The rebuilt synthetic world does not hold {len(unknown)} cell site(s), for example "
                                 f"{unknown[0]}. The folder and its world_params disagree.")
        return synthetic_strength(world)
    if paths is None:
        raise PaidSetRefusal("REAL strength needs the forecast data folder.")
    from .forecasting import real_strength_fn

    return real_strength_fn(paths)


def _content_page(row: Mapping[str, Any]) -> Any:
    from .content import page_text_from_fields

    return page_text_from_fields(title=row.get("title"), h1=row.get("h1"), meta_description=row.get("meta_description"),
                                 body=row.get("body"), url=str(row.get("url") or ""))


# ---------------------------------------------------------------------------
# the join


UPLOAD_ID_COLUMNS: tuple[str, ...] = (
    "site", "keyword", "search", "keyword_id", "locale", "niche", "split", "label", "search_group", "difficulty",
    "is_brand", "launch_date", "launch_uncertainty_days", "as_of", "inputs_as_of", "content_date", "input_date", "input_time", "input_depth",
    "input_page_age_days", "strength_date", "rank_in", "position_in", "outcome_date", "outcome_days_since_launch",
    "outcome_depth", "rank_out", "position_out", "lead_days", "never_ranked_to_outcome", "content_match",
    "content_match_backend", "reading_date", "reading_rank", "reading_depth",
)


@dataclass
class JoinResult:
    """Upload cells of one training-set folder, and every cell that was dropped, by reason. REAL or SYNTHETIC."""

    label: str
    cells: pd.DataFrame
    dropped_by_split: dict[str, dict[str, int]]
    asked_by_split: dict[str, int]
    rule: JoinRule
    content_match_backend: str | None
    content_match_model: str | None
    folder: str
    panel_dir: str
    cells_sha256: str
    panel_fingerprint: str
    time_cutoff: date | None
    notes: list[str] = field(default_factory=list)
    # Train cells with a month 1 page: (site, keyword_id) and the live provider's features from that page on its day.
    reading_features: pd.DataFrame | None = None

    @property
    def dropped(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for counts in self.dropped_by_split.values():
            for reason, count in counts.items():
                total[reason] = total.get(reason, 0) + count
        return {r: total[r] for r in DROP_REASONS if total.get(r)}

    def never_ranked(self) -> dict[str, Any]:
        rows = self.cells
        n = int(len(rows))
        never = int(rows["never_ranked_to_outcome"].sum()) if n else 0
        nt50 = int((rows["position_out"] == NT50).sum()) if n else 0
        return {"cells": n, "never_ranked": never, "never_ranked_share": never / n if n else None,
                "outcome_not_in_top_50": nt50, "outcome_not_in_top_50_share": nt50 / n if n else None}

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "cells": int(len(self.cells)), "asked_by_split": self.asked_by_split,
                "dropped": self.dropped, "dropped_by_split": self.dropped_by_split, "rule": self.rule.payload(),
                "rule_hash": self.rule.rule_hash(), "content_match_backend": self.content_match_backend,
                "content_match_model": self.content_match_model, "folder": self.folder, "panel_dir": self.panel_dir,
                "cells_sha256": self.cells_sha256, "panel_fingerprint": self.panel_fingerprint,
                "time_cutoff": self.time_cutoff.isoformat() if self.time_cutoff else None,
                "never_ranked": self.never_ranked(),
                "brand_cells": int(self.cells["is_brand"].sum()) if len(self.cells) else 0, "notes": self.notes}

    def lines(self) -> list[str]:
        tag = f"({self.label})"
        asked = ", ".join(f"{k} {v}" for k, v in sorted(self.asked_by_split.items())) or "none"
        out = [f"Upload cells joined from {self.folder} with the pages in {self.panel_dir} {tag}.",
               f"Cells asked by split: {asked}. Kept: {len(self.cells)} {tag}."]
        for reason, count in self.dropped.items():
            out.append(f"Dropped {count}: {DROP_TEXT[reason]} {tag}.")
        nr = self.never_ranked()
        if nr["cells"]:
            out.append(f"{nr['never_ranked']} of {nr['cells']} kept cells are searches where the site never ranked in the "
                       f"top 50 up to the outcome page ({nr['never_ranked_share']:.1%}) {tag}.")
            out.append(f"{nr['outcome_not_in_top_50']} kept cells have NOT IN TOP 50 as the outcome, read on a page at least "
                       f"50 deep ({nr['outcome_not_in_top_50_share']:.1%}) {tag}.")
            out.append(f"Brand searches flagged and left out of training and of the headline: "
                       f"{int(self.cells['is_brand'].sum())} {tag}.")
        out.append(f"Content match backend: {self.content_match_backend or 'none ran'} (PREDICTED scores, recorded per cell).")
        if self.time_cutoff:
            out.append(f"Time cutoff: training outcomes must be dated before {self.time_cutoff}, the earliest test "
                       f"forecast date {tag}.")
        out.extend(f"{note} {tag}" for note in self.notes)
        return out


def time_cutoff(cells: pd.DataFrame, rule: JoinRule = DEFAULT_JOIN_RULE) -> date | None:
    """The earliest forecast date (as_of) of any 'test' cell with a usable launch date. Reads launch dates, never outcomes."""
    found: list[date] = []
    for split, launch, unc in zip(cells["split"], cells["launch_date"], cells["launch_uncertainty_days"]):
        if split != "test" or launch is None or pd.isna(launch) or unc is None or pd.isna(unc):
            continue
        if int(unc) > rule.max_launch_uncertainty_days:
            continue
        found.append(as_date(launch) - timedelta(days=int(unc)))
    return min(found) if found else None


def _difficulty_map(universe: pd.DataFrame | None) -> dict[str, str | None]:
    if universe is None or "keyword_id" not in universe.columns or "stratum" not in universe.columns:
        return {}
    out: dict[str, str | None] = {}
    for kid, stratum in zip(universe["keyword_id"].astype(str), universe["stratum"]):
        out.setdefault(kid, str(stratum) if isinstance(stratum, str) and stratum in ("easy", "medium", "hard") else None)
    return out


def _join_one(cell: Mapping[str, Any], pages: Sequence[Page], rule: JoinRule, *, want_reading: bool) -> tuple[dict[str, Any] | None, str | None]:
    """Choose the input page, the outcome and the month 1 page of one cell. Returns (row, None) or (None, drop reason)."""
    from .backtest import ShallowOutcome, outcome_position

    launch = cell.get("launch_date")
    if launch is None or pd.isna(launch):
        return None, "launch_date_missing"
    unc = cell.get("launch_uncertainty_days")
    if unc is None or pd.isna(unc):
        return None, "launch_uncertainty_unknown"
    if int(unc) > rule.max_launch_uncertainty_days:
        return None, "launch_uncertainty_too_large"
    if not pages:
        return None, "search_has_no_pages"
    launch_day = as_date(launch)
    as_of = launch_day - timedelta(days=int(unc))  # inputs_as_of; join_upload_cells moves as_of to the text date it uses
    page_in, reason = _choose_input(pages, as_of, rule)
    if page_in is None:
        return None, reason
    page_out, reason = _choose_outcome(pages, launch_day, rule)
    if page_out is None:
        return None, reason
    site = str(cell["site"])
    rank_out = page_out.rank_of(site)
    try:
        position_out = outcome_position(rank_out, page_out.depth)
    except ShallowOutcome:  # unreachable: _choose_outcome takes pages at least 50 deep only
        return None, "outcome_unknown_shallow_page"
    rank_in = page_in.rank_of(site)
    position_in = rank_in if rank_in is not None and rank_in <= TOP_DEPTH else NT50  # the input page is at least 50 deep
    never = all((p.rank_of(site) is None or p.rank_of(site) > TOP_DEPTH) for p in pages if p.day <= page_out.day)
    row: dict[str, Any] = {
        "as_of": as_of, "inputs_as_of": as_of, "content_date": None, "input_date": page_in.day, "input_time": page_in.time, "input_depth": page_in.depth,
        "input_page_age_days": (as_of - page_in.day).days, "strength_date": as_of,
        "rank_in": float(rank_in) if rank_in is not None else math.nan, "position_in": int(position_in),
        "outcome_date": page_out.day, "outcome_days_since_launch": (page_out.day - launch_day).days,
        "outcome_depth": page_out.depth, "rank_out": float(rank_out) if rank_out is not None else math.nan,
        "position_out": int(position_out), "lead_days": (page_out.day - as_of).days, "never_ranked_to_outcome": bool(never),
        "reading_date": None, "reading_rank": math.nan, "reading_depth": math.nan,
        "_input_page": page_in,
    }
    if want_reading:
        page_r = _choose_reading(pages, launch_day, page_out.day, rule)
        if page_r is not None:
            rank_r = page_r.rank_of(site)
            row.update({"reading_date": page_r.day, "reading_rank": float(rank_r) if rank_r is not None else math.nan,
                        "reading_depth": page_r.depth, "_reading_page": page_r})
    return row, None


# The functions whose source the join rule hash covers, bound once here so a later rebinding cannot change the hash.
_RULE_FUNCTIONS = (_choose_input, _choose_outcome, _choose_reading, _join_one)


def join_upload_cells(
    folder: str | Path,
    *,
    splits: Sequence[str] = ("train", "test"),
    panel_dir: str | Path | None = None,
    paths: ForecastPaths | None = None,
    strength: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    evidence: Any | None = None,
    content_scorer: Any | None = None,
    rule: JoinRule = DEFAULT_JOIN_RULE,
    build_features: bool = True,
    want_readings: bool = False,
    log: Callable[[str], None] | None = None,
) -> JoinResult:
    """Join the cells of the asked splits with their past result pages into upload cells (module docstring).

    Only cells of `splits` are read. A cell of another split is never joined, so its outcome page is
    never opened. Training cells whose outcome page is dated on or after the time cutoff (the
    earliest test forecast date) are dropped and counted. Never-ranked cells are kept.
    """
    from .backtest import is_brand_search
    from .features import StartEvidence, panel_fingerprint, search_group
    from .forecasting import PANEL_DIFFICULTY_METRIC, LiveInputs, feature_row

    for split in splits:
        if split not in ("train", "test"):
            raise PaidSetRefusal(f"a split to join is train or test, not {split!r}")
    files = open_training_set(folder, panel_dir=panel_dir, paths=paths)
    label = files.label
    cells = files.cells.copy()
    cutoff = time_cutoff(cells, rule)
    dropped: dict[str, dict[str, int]] = {s: {} for s in splits}
    asked: dict[str, int] = {s: 0 for s in splits}

    def drop(split: str, reason: str) -> None:
        dropped[split][reason] = dropped[split].get(reason, 0) + 1

    excluded = cells[cells["split"] == "excluded"]
    if len(excluded) and "train" in splits:
        dropped["train"]["split_excluded"] = int(len(excluded))
    chosen = cells[cells["split"].isin(list(splits))].reset_index(drop=True)
    for split in splits:
        asked[split] = int((chosen["split"] == split).sum())
    pages = load_pages(files.panel_dir, sorted(set(chosen["keyword_id"].astype(str))), label=label)
    difficulty = _difficulty_map(files.universe)
    joined: list[dict[str, Any]] = []
    for cell in chosen.to_dict("records"):
        split = str(cell["split"])
        row, reason = _join_one(cell, pages.get(str(cell["keyword_id"]), []), rule,
                                want_reading=want_readings and split == "train")
        if row is None:
            drop(split, str(reason))
            continue
        if split == "train" and cutoff is not None and row["outcome_date"] >= cutoff:
            drop(split, "outcome_on_or_after_test_cutoff")
            continue
        site, keyword = str(cell["site"]), str(cell["keyword"])
        row.update({
            "site": site, "keyword": keyword, "search": keyword, "keyword_id": str(cell["keyword_id"]),
            "locale": cell.get("locale"), "niche": cell.get("niche"), "split": split, "label": label,
            "search_group": search_group(keyword), "difficulty": difficulty.get(str(cell["keyword_id"])),
            "is_brand": bool(is_brand_search(site, keyword)), "launch_date": as_date(cell["launch_date"]),
            "launch_uncertainty_days": int(cell["launch_uncertainty_days"]),
        })
        joined.append(row)
    notes: list[str] = []
    backend = model = None
    readings: list[dict[str, Any]] = []
    if build_features and joined:
        inner = strength or default_strength(files, paths)
        prefetched = PrefetchedStrength(inner)
        pairs = []
        for row in joined:
            for page, day in ((row["_input_page"], row["inputs_as_of"]), (row.get("_reading_page"), row.get("reading_date"))):
                if page is None:
                    continue
                pairs.extend((s, page.day) for s, r in page.ranks.items() if r <= TOP_DEPTH and not is_excluded_site(s))
                pairs.append((row["site"], day))
        try:
            prefetched.prefetch(pd.DataFrame(pairs, columns=["site", "as_of"]))
        except RuntimeError:
            pass  # kept in prefetched.failure; feature_row reports it and leaves strength missing (NaN), never zero
        if evidence is None and label == REAL:
            try:
                evidence = StartEvidence.load(sorted({r["site"] for r in joined}), paths.root if paths else None)
            except (OSError, ValueError, KeyError) as error:
                notes.append(f"Start-date evidence could not be read ({type(error).__name__}). Start features are missing (NaN).")
        scorer = content_scorer
        inputs = LiveInputs(None, paths, prefetched, label, datetime.now(timezone.utc), evidence=evidence)  # type: ignore[arg-type]
        def features_on(page: Page, site: str, keyword: str, day: date) -> dict[str, Any]:
            """The live provider's features for one cell as of `day`, from one stored page dated on or before it."""
            reading = AsOfGuard(day).take(Dated(_page_reading(page, label), f"past-page:{page.dataset}", page.day))
            known = page.kd is not None or page.volume is not None
            meta = {"difficulty_value": page.kd, "volume": page.volume, "difficulty_source": PANEL_DIFFICULTY_METRIC} if known else None
            return feature_row(inputs, site=site, search=keyword, day=day, page=reading, trust=None, meta=meta,
                               difficulty_metric=PANEL_DIFFICULTY_METRIC, form_answers=None)

        late_text = 0
        for number, row in enumerate(joined, start=1):
            page = row.pop("_input_page")
            row.update(features_on(page, row["site"], row["keyword"], row["inputs_as_of"]))
            page_r = row.pop("_reading_page", None)
            if page_r is not None:  # what a stage forecast on the month 1 day would see: that page, on that day
                readings.append({"site": row["site"], "keyword_id": row["keyword_id"],
                                 **features_on(page_r, row["site"], row["keyword"], as_date(row["reading_date"]))})
            texts = files.page_texts.get(row["site"], [])
            guard = AsOfGuard(row["launch_date"])  # the site's own launch page is known when captured, never after D
            taken = [guard.take(Dated(t, str(t.get("source") or "page_text"), t["captured_on"])) for t in texts]
            row["content_match"] = math.nan
            row["content_match_backend"] = None
            if taken:
                if scorer is None:
                    from .content import ContentScorer

                    scorer = ContentScorer("auto")
                latest = max(taken, key=lambda t: t["captured_on"])
                score = scorer.score(row["keyword"], _content_page(latest))
                row["content_match"] = math.nan if score.value is None else float(score.value)
                row["content_match_backend"] = score.mode
                backend, model = score.mode, score.model
                if score.value is not None:
                    # The text is an input of this row: as_of is the newest input date, and lead_days runs from it.
                    text_day = as_date(latest["captured_on"])
                    row["content_date"] = text_day
                    if text_day > row["as_of"]:
                        late_text += 1
                        row["as_of"] = text_day
                        row["lead_days"] = (as_date(row["outcome_date"]) - text_day).days
            if log and number % 500 == 0:
                log(f"features for {number} of {len(joined)} cells")
        notes.extend(sorted(set(inputs.notes)))
        if late_text:
            notes.append(f"{late_text} cells use launch page text captured after the earliest plausible launch date. Their "
                         "as_of is the text's capture day and lead_days runs from it; the result page, strength and "
                         "start evidence stay dated on or before inputs_as_of.")
    else:
        for row in joined:
            row.pop("_input_page", None)
            row.pop("_reading_page", None)
            row.setdefault("content_match", math.nan)
            row.setdefault("content_match_backend", None)
    frame = pd.DataFrame.from_records(joined)
    if frame.empty:
        frame = pd.DataFrame(columns=list(UPLOAD_ID_COLUMNS))
    else:
        first = [c for c in UPLOAD_ID_COLUMNS if c in frame.columns]
        frame = frame[first + [c for c in frame.columns if c not in first]]
        frame = frame.sort_values(["split", "site", "keyword_id"], kind="mergesort").reset_index(drop=True)
    return JoinResult(label, frame, dropped, asked, rule, backend, model, str(files.folder), str(files.panel_dir),
                      files.cells_sha256, panel_fingerprint(files.panel_dir), cutoff, notes,
                      reading_features=pd.DataFrame.from_records(readings) if readings else None)


# ---------------------------------------------------------------------------
# training


def training_rows(join: JoinResult) -> pd.DataFrame:
    """The rows a bundle is fitted on: the 'train' split, brand searches left out. A 'test' row is refused."""
    rows = join.cells
    if len(rows) and (rows["split"] != "train").any():
        raise PaidSetRefusal("training rows may come from the train split only. A test row was handed to training.")
    if len(rows) == 0:
        return rows
    return rows[~rows["is_brand"].astype(bool)].reset_index(drop=True)


def updater_rows(rows: pd.DataFrame, label: str, reading_features: pd.DataFrame | None) -> pd.DataFrame | None:
    """Rows for the early-reading updater: training cells with a month 1 page, as of that page's day.

    The features are the live provider's from the month 1 page on its own day, as a live stage forecast
    builds them from its stage-day reading. The reading is the site's position on that page (NOT IN TOP 50
    when absent from a page at least 50 deep, unknown when absent from a shallower one). lead_days runs
    from that page to the outcome page.
    """
    from .updater import Reading, reading_columns

    if len(rows) == 0 or "reading_date" not in rows or reading_features is None or reading_features.empty:
        return None
    later = {(str(r["site"]), str(r["keyword_id"])): r for r in reading_features.to_dict("records")}
    have = rows[rows["reading_date"].notna()].reset_index(drop=True)
    if have.empty:
        return None
    records = []
    for row in have.to_dict("records"):
        found = later.get((str(row["site"]), str(row["keyword_id"])))
        if found is None:
            continue
        day = as_date(row["reading_date"])
        row = {**row, **{k: v for k, v in found.items() if k not in ("site", "keyword_id")},
               "input_date": day, "strength_date": day}
        rank = row["reading_rank"]
        if rank is not None and not pd.isna(rank):
            position: int | None = int(rank) if int(rank) <= TOP_DEPTH else NT50
        elif row["reading_depth"] is not None and not pd.isna(row["reading_depth"]) and int(row["reading_depth"]) >= TOP_DEPTH:
            position = NT50
        else:
            position = None
        cols = reading_columns([Reading(position=position, taken_on=day, label=label, source="past_page")], day)
        records.append({**row, **cols, "as_of": day, "lead_days": float((as_date(row["outcome_date"]) - day).days),
                        "new_to_search": math.nan})
    return pd.DataFrame.from_records(records)


def graded_training_cells(rows: pd.DataFrame, *, source: str) -> list[Any]:
    """GradedCell per training row (point = REAL actual): what the band prior baseline is fitted on."""
    from .grading import GradedCell

    return [GradedCell(site=str(r["site"]), search=str(r["keyword_id"]), point=int(r["position_out"]),
                       actual=int(r["position_out"]), is_brand=bool(r["is_brand"]),
                       difficulty=r["difficulty"] if isinstance(r["difficulty"], str) else None, source=source,
                       stage=STAGE, niche=str(r["niche"]) if r.get("niche") is not None else None)
            for r in rows.to_dict("records")]


def report_source(label: str) -> str:
    """backtest for REAL tables, synthetic for SYNTHETIC ones (so the report and the marker say SYNTHETIC)."""
    return "synthetic" if label == SYNTHETIC else "backtest"


def train_paid(
    paths: ForecastPaths,
    name: str,
    *,
    folder: str | Path,
    panel_dir: str | Path | None = None,
    strength: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    evidence: Any | None = None,
    content_scorer: Any | None = None,
    rule: JoinRule = DEFAULT_JOIN_RULE,
    log: Callable[[str], None] | None = None,
) -> Any:
    """Fit a bundle on the 'train' split of a training-set folder through forecasting.fit_bundle.

    The bundle is trained only when its kept training rows include searches where the site never
    ranked (includes_never_ranked True, counted from those rows), so the live path uses it with no
    appearance gate. With none kept, nothing is trained and the refusal says why. Calibration and validation rows come from the training side by search
    group, as the panel bundle does it. The 'test' split is never joined. A bundle from SYNTHETIC tables
    is labelled SYNTHETIC, which the live path refuses for a seed or pilot site.
    """
    from .forecasting import (PANEL_DIFFICULTY_METRIC, PLACEMENT_FEATURES, TrainResult, check_bundle_name, fit_bundle,
                              refuse_existing_bundle)
    from .placement import PlacementError
    from .updater import UpdaterError

    name = check_bundle_name(name)
    refuse_existing_bundle(paths, name)  # refused before the join and the training run, not after them
    join = join_upload_cells(folder, splits=("train",), panel_dir=panel_dir, paths=paths, strength=strength,
                             evidence=evidence, content_scorer=content_scorer, rule=rule, want_readings=True, log=log)
    rows = training_rows(join)
    if rows.empty:
        return TrainResult(None, "paid", join.label, counts={"join": join.to_dict()}, refusal=(
            f"No training cell survived the join of {join.folder}. Dropped: "
            + (", ".join(f"{k} {v}" for k, v in join.dropped.items()) or "none") + "."))
    never_kept = int(rows["never_ranked_to_outcome"].astype(bool).sum())
    if never_kept == 0:
        return TrainResult(None, "paid", join.label, counts={"join": join.to_dict()}, refusal=(
            f"None of the {len(rows)} kept training cells is a search where the site never ranked in the top 50. A bundle "
            "from them would skip the appearance gate without having learned never-ranked searches. Nothing was trained."))
    cutoff = max(as_date(d) for d in rows["outcome_date"])
    if join.time_cutoff is not None and cutoff >= join.time_cutoff:
        raise LeakError(f"a training outcome on {cutoff} is on or after the earliest test forecast date {join.time_cutoff}")
    kd_known = bool(pd.to_numeric(rows.get("pg_kd"), errors="coerce").notna().any()) if "pg_kd" in rows else False
    source = "paid" if join.label == REAL else "paid-synthetic"
    extra = {
        "paidset_version": PAIDSET_VERSION, "training_set": join.folder, "cells_sha256": join.cells_sha256,
        "panel_dir": join.panel_dir, "panel_fingerprint": join.panel_fingerprint,
        "join_rule": join.rule.payload(), "join_rule_hash": join.rule.rule_hash(),
        "split_rule_hash": (join.cells.attrs or {}).get("split_rule_hash") or _meta_value(join.folder, "split_rule_hash"),
        "target_rule_hash": _meta_value(join.folder, "target_rule_hash"),
        "time_cutoff": join.time_cutoff.isoformat() if join.time_cutoff else None,
        "rows_read_by_split": {"train": int(join.asked_by_split.get("train", 0)), "test": 0},
        "dropped": join.dropped, "brand_cells_left_out": int(join.cells["is_brand"].sum()),
        "never_ranked": join.never_ranked(), "never_ranked_training_rows": never_kept,
        "content_match_backend": join.content_match_backend, "content_match_model": join.content_match_model,
        # The same test fit_bundle applies to every listed feature: listed, and at least one finite value.
        "content_match_is_model_input": bool(CONTENT_COLUMN in PLACEMENT_FEATURES and CONTENT_COLUMN in rows
                                             and pd.to_numeric(rows[CONTENT_COLUMN], errors="coerce").notna().any()),
    }
    split_note = (f"the train split of the frozen split rule only (by launch cohort in time, then by site, no search group "
                  f"on both sides); outcomes before {join.time_cutoff or 'no test cutoff'}; no test row is read")
    updater = updater_rows(rows, join.label, join.reading_features)
    common = dict(label=join.label, source=source, includes_never_ranked=never_kept > 0, training_cutoff=cutoff, group_col="search_group",
                  trust=None, difficulty_metric=PANEL_DIFFICULTY_METRIC if kd_known else None, split_note=split_note,
                  extra_manifest=extra)
    notes: list[str] = []
    try:
        bundle = fit_bundle(paths, name, rows, updater, **common)
    except (UpdaterError, PlacementError) as error:
        if updater is None:
            return TrainResult(None, "paid", join.label, counts={"join": join.to_dict()},
                               refusal=f"The placement model could not be fitted on {len(rows)} training rows: {error}.")
        notes.append(f"The early-reading updater could not be fitted ({error}). The bundle has no updater, so it makes "
                     "upload forecasts only.")
        try:
            bundle = fit_bundle(paths, name, rows, None, **common)
        except PlacementError as again:
            return TrainResult(None, "paid", join.label, counts={"join": join.to_dict()},
                               refusal=f"The placement model could not be fitted on {len(rows)} training rows: {again}.")
    result = TrainResult(bundle, "paid", join.label, counts=dict(bundle.manifest["counts"]),
                         training_cells=graded_training_cells(rows, source=report_source(join.label)))
    result.counts["join"] = join.to_dict()
    result.notes.extend(join.lines())
    result.notes.extend(notes)
    result.notes.append("This bundle includes searches where a site never ranked, so it forecasts without the appearance gate.")
    result.notes.append("No trust model is in this bundle: trust from traffic curves is not built yet (part 5). trust_pred is "
                        "missing (NaN), never zero.")
    if join.label == SYNTHETIC:
        result.notes.append("SYNTHETIC. This bundle is never used for a seed or pilot site and nothing it scores is accuracy.")
    return result


def _meta_value(folder: str | Path, key: str) -> Any:
    for name in (ts.REAL_META_FILE, ts.SYNTHETIC_META_FILE):
        path = Path(folder) / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")).get(key)
    return None


PAID_INPUT_COMMANDS: dict[str, str] = {
    "cohort": "python -m aeo.ranksim forecast cohort build --name <name> ...",
    "cells": "python -m aeo.ranksim forecast paid cells --cohort <cohort parquet> --pages <pages jsonl> --universe <universe parquet>",
}


def train_paid_from_cache(paths: ForecastPaths, *, name: str, folder: str | Path | None = None,
                          log: Callable[[str], None] | None = None) -> Any:
    """What `forecast train --data paid` runs: the REAL training-set folder under the cache (or `folder`) and cache/panel_labs.

    Missing inputs are named with the command that makes each. Traffic curves and exact positions are
    not needed by the placement model; they are named so the plan's step 4 is complete.
    """
    from .forecasting import PAID_PULL_COMMANDS, TrainResult

    base = Path(folder) if folder else default_set_dir(paths)
    has_panel_here = (base / "panel").is_dir()
    labs = default_labs_panel(paths)
    pages_ok = has_panel_here or (labs.is_dir() and any(labs.rglob("*.parquet")))
    cells_ok = (base / ts.CELLS_FILE).is_file()
    if not (pages_ok and cells_ok):
        parts = []
        if not pages_ok:
            parts.append(f"past result pages: {PAID_PULL_COMMANDS['past result pages']}")
        if not cells_ok:
            parts.append(f"the cell table in {base}: {PAID_INPUT_COMMANDS['cells']}")
        others = "; ".join(f"{k}: {PAID_PULL_COMMANDS[k]}" for k in ("traffic curves", "exact positions"))
        return TrainResult(None, "paid", REAL, refusal=(
            "The paid training set is not on disk. Missing: " + "; ".join(parts) + ". Each pull is a dry run without "
            "--execute and needs an approval and its $1 sample first. The other two paid pulls of step 4 feed the trust "
            f"model, not the placement model: {others}."))
    return train_paid(paths, name, folder=base, panel_dir=None if has_panel_here else labs, log=log)


# ---------------------------------------------------------------------------
# the frozen cohort backtest (marker 3 on past new sites)


def _keys_sha(cells: pd.DataFrame) -> str:
    keys = sorted(f"{s}|{k}" for s, k in zip(cells["site"].astype(str), cells["keyword_id"].astype(str)))
    return sha256_text("\n".join(keys))


def _training_digest(cells: Sequence[Any]) -> str:
    rows = sorted([c.site, c.search, int(c.actual), c.difficulty or "", bool(c.is_brand)] for c in cells)
    return sha256_text(canonical_json(rows))


def design_root(root: str | Path | None, name: str) -> Path:
    from .backtest import backtest_dir

    return backtest_dir(name, root)


def _check_bundle_for(bundle: Any, join_label: str, cells_sha256: str) -> None:
    manifest = bundle.manifest
    if not manifest.get("includes_never_ranked"):
        raise PaidSetRefusal(f"Bundle {bundle.name} does not include never-ranked cells. The cohort backtest scores a bundle "
                             "trained by paid train only.")
    if bundle.training_label != join_label:
        raise PaidSetRefusal(f"Bundle {bundle.name} is {bundle.training_label} and the tables are {join_label}. Labels are "
                             "never mixed.")
    if manifest.get("cells_sha256") != cells_sha256:
        raise PaidSetRefusal(f"Bundle {bundle.name} was trained on another cell table (sha256 "
                             f"{str(manifest.get('cells_sha256'))[:12]}, now {cells_sha256[:12]}). Train it again on this one.")


def design_payload(
    paths: ForecastPaths,
    *,
    folder: str | Path,
    bundle_name: str,
    name: str = DESIGN_NAME,
    panel_dir: str | Path | None = None,
    strength: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    evidence: Any | None = None,
    rule: JoinRule = DEFAULT_JOIN_RULE,
) -> tuple[dict[str, Any], list[Any]]:
    """The cohort backtest design and the training cells of its band prior. Reads no 'test' outcome.

    The design names the test rows (their keys, from the cell table), the split and target rule
    hashes, the panel fingerprint, the join rule, the bundle hash and features, the band prior fitted
    on training cells, the grading rule, the four baselines and the markers hash.
    """
    from .features import panel_fingerprint
    from .forecasting import load_bundle
    from .grading import RULE_V1, fit_band_prior
    from .markers import markers_hash

    files = open_training_set(folder, panel_dir=panel_dir, paths=paths)
    bundle = load_bundle(paths, bundle_name)
    _check_bundle_for(bundle, files.label, files.cells_sha256)
    test = files.cells[files.cells["split"] == "test"]
    if test.empty:
        raise PaidSetRefusal(f"{files.folder} has no test cells, so there is nothing to score.")
    train_join = join_upload_cells(folder, splits=("train",), panel_dir=panel_dir, paths=paths, strength=strength,
                                   evidence=evidence, rule=rule, build_features=False)
    train_cells = graded_training_cells(training_rows(train_join), source=report_source(files.label))
    if not train_cells:
        raise PaidSetRefusal("No training cell survived the join, so the band prior cannot be fitted.")
    manifest = bundle.manifest
    payload = {
        "kind": "cohort_upload_backtest", "design": name, "paidset_version": PAIDSET_VERSION, "plan_marker": 3,
        "label": files.label, "report_source": report_source(files.label), "stage": STAGE,
        "training_set": {"cells_sha256": files.cells_sha256, "split_rule_hash": files.meta.get("split_rule_hash"),
                         "target_rule_hash": files.meta.get("target_rule_hash")},
        "test_rows": {"split": "test", "count": int(len(test)), "sites": int(test["site"].nunique()),
                      "searches": int(test["keyword_id"].nunique()), "keys_sha256": _keys_sha(test)},
        "time_cutoff": train_join.time_cutoff.isoformat() if train_join.time_cutoff else None,
        "panel": {"folder_name": Path(files.panel_dir).name, "fingerprint": panel_fingerprint(files.panel_dir)},
        "join_rule": rule.payload(), "join_rule_hash": rule.rule_hash(),
        "bundle": {"name": bundle.name, "sha256": bundle.sha256, "version": bundle.version,
                   "training_label": bundle.training_label, "includes_never_ranked": True,
                   "features": list(manifest.get("features") or []),
                   "strength_only_features": list(manifest.get("strength_only_features") or []),
                   "training_cutoff": manifest.get("training_cutoff"),
                   "content_match_backend": manifest.get("content_match_backend")},
        "band_prior": {"training_cells": len(train_cells), "training_cells_sha256": _training_digest(train_cells),
                       "points": fit_band_prior(train_cells, RULE_V1)},
        "metric": {"grading_rule": RULE_V1.version, "grading_rule_hash": RULE_V1.rule_hash(),
                   "headline": "hit rate of the forecast point on held-out test cells, brand searches left out, lead 28 days or more",
                   "interval": "cluster bootstrap over sites", "page_one": "precision and recall of points 1 to 10",
                   "range": "share of real positions inside the issued range"},
        "baselines": {"always_nt50": "always NOT IN TOP 50",
                      "persistence": "the site's position on the input page, NOT IN TOP 50 when it is absent from it",
                      "band_prior": "the constant point per difficulty third fitted on training cells",
                      "site_strength": "the bundle's site strength only placement model on the same rows"},
        "spec_baselines": list(SPEC_BASELINES),
        "markers_hash": markers_hash(),
    }
    return payload, train_cells


LOOKS_FILE = "scored_looks.jsonl"  # in the training-set folder: every scored look at its test rows, whatever the root


def _same_rows(record: Mapping[str, Any], keys_sha: str, cells_sha: str) -> bool:
    keys = record.get("test_rows_keys_sha256")
    if keys:
        return keys == keys_sha
    return bool(record.get("cells_sha256")) and record.get("cells_sha256") == cells_sha


def earlier_looks(folder: str | Path, root: str | Path | None, name: str, *, keys_sha: str, cells_sha: str) -> list[dict[str, Any]]:
    """Every scored run that already read these held-out test rows, under any design name.

    Looked for in every manifest-v*.json of every design folder under the backtest root (the given one and the
    default one) and in the training-set folder's own record of scored looks (LOOKS_FILE). A run matches when it
    names the same test-row keys, or (older manifests without them) the same cell table.
    """
    from .backtest import backtest_dir

    found: dict[str, dict[str, Any]] = {}
    bases = {design_root(root, name).parent, backtest_dir(name, None).parent}
    for base in sorted(bases):
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*/manifest-v*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and _same_rows(record, keys_sha, cells_sha):
                report = path.parent / str(record.get("report_file") or "")
                found[str(report.resolve())] = {"design": path.parent.name, "design_version": record.get("design_version"),
                                                "report_file": str(report), "written_at": record.get("written_at")}
    ledger = Path(folder) / LOOKS_FILE
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and _same_rows(record, keys_sha, cells_sha):
                report = str(record.get("report_file") or "")
                found.setdefault(str(Path(report).resolve()) if report else line, {
                    "design": record.get("design"), "design_version": record.get("design_version"),
                    "report_file": report, "written_at": record.get("written_at")})
    return sorted(found.values(), key=lambda r: (str(r.get("written_at") or ""), str(r["report_file"])))


def _record_look(folder: str | Path, entry: Mapping[str, Any]) -> None:
    with (Path(folder) / LOOKS_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), sort_keys=True, default=str) + "\n")


def freeze_design(
    paths: ForecastPaths,
    *,
    folder: str | Path,
    bundle_name: str,
    root: str | Path | None = None,
    name: str = DESIGN_NAME,
    version: int = 1,
    reason: str | None = None,
    panel_dir: str | Path | None = None,
    strength: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    evidence: Any | None = None,
    rule: JoinRule = DEFAULT_JOIN_RULE,
) -> dict[str, Any]:
    """Freeze the cohort backtest design (features.freeze_prereg with code=True). Scores nothing.

    A SYNTHETIC design is written only under an explicit `root`, never into the committed evals folder.
    """
    from .features import freeze_prereg, prereg_file, versioned_payload

    payload, _ = design_payload(paths, folder=folder, bundle_name=bundle_name, name=name, panel_dir=panel_dir,
                                strength=strength, evidence=evidence, rule=rule)
    if payload["label"] == SYNTHETIC and root is None:
        raise PaidSetRefusal("A SYNTHETIC design is written only under an explicit root folder, never beside REAL designs.")
    body = versioned_payload(payload, name=name, version=version, reason=reason)
    target = prereg_file(design_root(root, name), version)
    already_frozen = target.is_file()  # freeze_prereg then only checks that the stored design is the same
    digest = freeze_prereg(target, body, code=True)
    looks = earlier_looks(folder, root, name, keys_sha=payload["test_rows"]["keys_sha256"],
                          cells_sha=payload["training_set"]["cells_sha256"])
    return {"prereg_file": str(target), "sha256": digest, "payload": body, "label": payload["label"], "version": int(version),
            "already_frozen": already_frozen,
            "earlier_looks": looks}


def scorer_for_bundle(bundle: Any, given: Any | None) -> Any | None:
    """The content scorer for test cells: the backend the bundle's content_match column was trained with.

    A given scorer that runs another backend is refused: its scores are on another scale, and the model
    would read them as if they were not. With no backend in the manifest the given scorer (or none) is used.
    """
    trained = bundle.manifest.get(CONTENT_BACKEND_KEY)
    if not trained:
        return given
    if given is None:
        from .content import ContentScorer

        given = ContentScorer(str(trained))  # raises when that backend cannot run here: nothing is scored then
    if getattr(given, "mode", None) != trained:
        raise PaidSetRefusal(f"bundle {bundle.name} learned content_match from {trained} scores and the scorer runs "
                             f"{getattr(given, 'mode', None)}. Pass --content-backend {trained}.")
    return given


def score_design(
    paths: ForecastPaths,
    *,
    folder: str | Path,
    bundle_name: str,
    root: str | Path | None = None,
    name: str = DESIGN_NAME,
    version: int = 1,
    reason: str | None = None,
    panel_dir: str | Path | None = None,
    strength: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    evidence: Any | None = None,
    content_scorer: Any | None = None,
    rule: JoinRule = DEFAULT_JOIN_RULE,
    check_git: bool = True,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Score the 'test' split once under the frozen design and write the versioned report and manifest.

    Every refusal comes before any test outcome is read (features.begin_scored_run): the design must be
    frozen and unchanged, the forecast source must match the freeze, this version must not be scored,
    and with `check_git` the source tree is exactly HEAD and the design file is committed and pushed.
    `check_git` False is refused for REAL tables. Held-out flags are computed per graded cell. When these
    test rows were already scored (earlier_looks, any design name), version 1 is refused; a later version
    (with its frozen reason) is scored and reported as a SECOND LOOK: PROVISIONAL, no marker verdict.
    """
    from .backtest import backtest_report, forecast_frame
    from .features import PROVISIONAL, begin_scored_run, finish_scored_run, mark_provisional, versioned_payload
    from .forecasting import NO_GATE, live_models, load_bundle
    from .markers import evaluate_marker
    from .placement import dist_median

    files = open_training_set(folder, panel_dir=panel_dir, paths=paths)
    if not check_git and files.label == REAL:
        raise PaidSetRefusal("A scored run on REAL tables always checks git: the design must be committed and pushed and "
                             "the source tree clean.")
    payload, train_cells = design_payload(paths, folder=folder, bundle_name=bundle_name, name=name, panel_dir=panel_dir,
                                          strength=strength, evidence=evidence, rule=rule)
    label = payload["label"]
    body = versioned_payload(payload, name=name, version=version, reason=reason)
    bundle = load_bundle(paths, bundle_name)
    content_scorer = scorer_for_bundle(bundle, content_scorer)
    context = begin_scored_run(design_root(root, name), version, body, check_git=check_git)
    keys_sha, cells_sha = payload["test_rows"]["keys_sha256"], payload["training_set"]["cells_sha256"]
    looks = earlier_looks(folder, root, name, keys_sha=keys_sha, cells_sha=cells_sha)
    if looks and int(version) < 2:
        where = ", ".join(f"{look['design']} version {look['design_version']}" for look in looks)
        raise PaidSetRefusal(f"These held-out test rows were already scored ({where}). A test is scored once, whatever the "
                             "design name. A second look needs --design-version 2 or later with --reason, and is reported as "
                             "a second look.")
    second_look = None
    if looks:
        second_look = {"label": PROVISIONAL, "earlier_looks": looks,
                       "reason_for_new_version": body.get("reason_for_new_version"),
                       "reason": (f"SECOND LOOK: these held-out test rows were already scored {len(looks)} time(s) (first in "
                                  f"{looks[0]['report_file']}). Its numbers are PROVISIONAL, not fresh held-out accuracy, and "
                                  "carry no marker verdict.")}
    train_split = files.cells[files.cells["split"] == "train"]
    train_sites = {str(s) for s in train_split["site"]}
    train_groups = {ts.search_group(str(k)) for k in train_split["keyword"]}
    cutoff_text = payload.get("time_cutoff")
    held_cutoff = date.fromisoformat(cutoff_text) if cutoff_text else None
    # From here on the test split is read, once.
    join = join_upload_cells(folder, splits=("test",), panel_dir=panel_dir, paths=paths, strength=strength, evidence=evidence,
                             content_scorer=content_scorer, rule=rule, log=log)
    rows = join.cells
    if rows.empty:
        raise PaidSetRefusal("No test cell survived the join. Dropped: "
                             + (", ".join(f"{k} {v}" for k, v in join.dropped.items()) or "none") + ".")
    if (rows["split"] != "test").any():
        raise PaidSetRefusal("a scored run grades test rows only")
    trained = bundle.manifest.get(CONTENT_BACKEND_KEY)
    if join.content_match_backend is not None and trained and join.content_match_backend != trained:
        raise PaidSetRefusal(f"the test cells were scored with {join.content_match_backend} and the bundle with {trained}. "
                             "Scores of two backends are on different scales.")
    early = min(as_date(d) for d in rows["as_of"])
    if bundle.training_cutoff >= early:
        raise LeakError(f"bundle {bundle.name} learned outcomes up to {bundle.training_cutoff}, on or after the earliest test "
                        f"forecast date {early}")
    models, strength_only = live_models(bundle, NO_GATE)
    dists, forecasts = forecast_frame(rows, models, stage=STAGE)
    strength_points = [int(dist_median(d)) for d in strength_only.distributions(rows)]
    source = report_source(label)
    from .grading import GradedCell

    cells = [GradedCell(site=str(r["site"]), search=str(r["keyword_id"]), point=int(f.point), actual=int(r["position_out"]),
                        is_brand=bool(r["is_brand"]), difficulty=r["difficulty"] if isinstance(r["difficulty"], str) else None,
                        label=f.label, source=source, stage=STAGE, lead_days=int(r["lead_days"]),
                        range_low=int(f.range_low), range_high=int(f.range_high),
                        niche=str(r["niche"]) if r.get("niche") is not None else None)
             for r, f in zip(rows.to_dict("records"), forecasts)]
    actual = rows["position_out"].astype(int).to_numpy()
    lows = np.array([f.range_low for f in forecasts])
    highs = np.array([f.range_high for f in forecasts])
    inside = (lows <= actual) & (actual <= highs)
    outcome_dates = [as_date(d) for d in rows["outcome_date"]]
    extra = {
        "plan_marker": 3, "design": name, "design_version": int(version), "paidset_version": PAIDSET_VERSION,
        "label_text": f"Model output is {PREDICTED}. Positions are {label}.",
        "what_this_is": ("Upload-day forecasts for past new sites on target searches picked by a frozen label-blind rule, "
                         "including searches where the site never ranked, graded on the page closest to day 90."
                         if label == REAL else "SYNTHETIC tables. A test of the code. Never accuracy and never a marker result."),
        "test_cells_asked": int(join.asked_by_split.get("test", 0)), "dropped_cells": join.dropped,
        "never_ranked": join.never_ranked(),
        "range_coverage": {"rows": int(len(rows)), "coverage": float(inside.mean()) if len(rows) else None},
        "join_rule": rule.payload(), "join_rule_hash": rule.rule_hash(),
        "bundle": {"name": bundle.name, "sha256": bundle.sha256, "version": bundle.version},
        "content_match_backend": join.content_match_backend,
        "outcome_days_since_launch": {"min": int(rows["outcome_days_since_launch"].min()),
                                      "max": int(rows["outcome_days_since_launch"].max())},
        "reason_for_new_version": body.get("reason_for_new_version"),
    }
    # Held out on every side, computed per cell: a site and a search group not on the train side, dated on or after the cutoff.
    held_out = [str(r["site"]) not in train_sites and ts.search_group(str(r["keyword"])) not in train_groups
                and (held_cutoff is None or as_date(r["as_of"]) >= held_cutoff) for r in rows.to_dict("records")]
    report = backtest_report(cells, {"persistence": [int(p) for p in rows["position_in"]], "site_strength": strength_points},
                             stage=STAGE, as_of=max(outcome_dates), model_version=bundle.version, outcome_dates=outcome_dates,
                             as_ofs=[as_date(d) for d in rows["as_of"]], band_prior_training=train_cells, source=source,
                             notes=[f"Every number here is {label}. Forecasts are {PREDICTED}."], extra=extra,
                             held_out=held_out, stand_in_reason=None)
    report["marker_3"] = evaluate_marker(3, report)
    report["test_rows_keys_sha256"] = keys_sha
    report["second_look"] = second_look
    if second_look is not None:
        report = mark_provisional(report, second_look)
    marker = report["marker_3"]
    files = {role: dict(item) for role, item in (bundle.manifest.get("files") or {}).items()}
    manifest = finish_scored_run(context, report, extra={
        "bundle": bundle.name, "bundle_sha256": bundle.sha256, "model_files": files, "cells_sha256": join.cells_sha256,
        "panel_fingerprint": join.panel_fingerprint, "label": label, "design": name, "test_rows_keys_sha256": keys_sha,
        "second_look": second_look is not None})
    try:
        _record_look(folder, {"design": name, "design_version": int(version), "report_file": str(context["report_file"]),
                              "test_rows_keys_sha256": keys_sha, "cells_sha256": cells_sha, "label": label,
                              "written_at": manifest.get("written_at")})
    except OSError:
        pass  # the manifest written above still names these rows, and earlier_looks reads every manifest
    return {"report": report, "manifest": manifest, "marker": marker, "report_file": str(context["report_file"]),
            "manifest_file": str(context["manifest_file"]), "label": label}


def score_lines(result: Mapping[str, Any]) -> list[str]:
    """The scored report in short plain sentences. Every line names its label."""
    from .markers import render_marker_text

    report = result["report"]
    label = result["label"]
    tag = f"({label})"
    head = report["headline"]
    summary = head["summary"]
    hit = summary["hit"]

    def pct(value: Any) -> str:
        return "not measurable" if value is None else f"{float(value):.1%}"

    interval = hit.get("cluster_interval") or [None, None]
    lines = [
        f"Cohort backtest {report['design']} version {report['design_version']}, upload stage, scored once {tag}.",
        f"Graded cells in the headline: {head['forecasts']} (brand searches and leads under 28 days left out) {tag}.",
        f"Hit rate: {pct(hit.get('rate'))}, site cluster interval {pct(interval[0])} to {pct(interval[1])} {tag}.",
    ]
    for name, block in sorted((head.get("baselines") or {}).items()):
        lines.append(f"Baseline {name}: {pct(block.get('baseline_rate'))} {tag}.")
    if head.get("best_baseline"):
        skill = head.get("skill_over_best_baseline")
        shown = "not measurable" if skill is None else f"{float(skill):.3f}"
        lines.append(f"Best baseline: {head['best_baseline']}. Skill over it: {shown} {tag}.")
    page = summary.get("page_one") or {}
    for key in ("precision", "recall"):
        block = page.get(key) or {}
        count = int(block.get("count") or 0)
        shown = pct(block.get("rate")) if count >= 30 else f"NOT MEASURABLE ({count} cases)"
        lines.append(f"Page one {key}: {shown} {tag}.")
    cover = report.get("range_coverage") or {}
    lines.append(f"Range coverage: {pct(cover.get('coverage'))} of {cover.get('rows')} rows {tag}.")
    nr = report.get("never_ranked") or {}
    if nr.get("cells"):
        lines.append(f"Never-ranked cells: {nr['never_ranked']} of {nr['cells']} ({pct(nr.get('never_ranked_share'))}) {tag}.")
    for reason, count in (report.get("dropped_cells") or {}).items():
        lines.append(f"Dropped test cells, {DROP_TEXT.get(reason, reason)}: {count} {tag}.")
    if report.get("second_look"):
        lines.append(f"{report['second_look']['reason']} Reason for this version: "
                     f"{report['second_look'].get('reason_for_new_version')} {tag}.")
    lines.extend(render_marker_text(result["marker"]).splitlines())
    lines.append(f"Wrote {result['report_file']} and {result['manifest_file']}. They are never overwritten.")
    if label == SYNTHETIC:
        lines = [line if SYNTHETIC in line else f"{line} {tag}" for line in lines]
    return lines
