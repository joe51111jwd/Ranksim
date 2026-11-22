"""The live forecasting path: model bundles on disk, the appearance gate, live feature rows, upload and update.

What is REAL here: the clean readings read back from the scoreboard (the result page a
forecast is built on, and a site's own positions at the stage days), dated strength list
values, start-date evidence, the agency form answers and Search Console rows as they were
frozen. What is PREDICTED: every forecast, every trust value and every probability. What is
SYNTHETIC: a bundle trained on synth.World, and every forecast made for a synthetic site.
The three are never pooled: a SYNTHETIC bundle never forecasts a seed or pilot site, and a
gate fitted on graded outcomes reads REAL seed and pilot outcomes only for a REAL site and
SYNTHETIC outcomes only for a synthetic one.

No leakage. A forecast dated D uses only what was knowable on D:

- the page is the latest stored, non-degraded clean reading of the search taken on or before
  D (and not after the moment the forecast is made). A search with no such reading is
  skipped and reported by name. It never becomes a silent NOT IN TOP 50;
- strength values come through features.strength_for_dates or cached_strength (every value
  through asof.AsOfGuard), or synth.World's dated lists for a synthetic site;
- start-date evidence goes through features.start_features (evidence published after D is
  dropped there);
- the agency form used is the newest frozen copy filled in on or before D;
- Search Console rows fetched after D are ignored by gsc.early_signals;
- a bundle whose training outcomes end after D is refused (asof.LeakError);
- backtest.forecast_cells checks every input date again.

The appearance gate (task A3 of the build). The free panel has cells only for sites that
appeared on a search at least once, so a placement model trained on it answers "where does a
site land, given that it appears". A brand-new site on launch day has appeared nowhere. So a
placement model is used directly for a REAL site only when its bundle manifest says it was
trained on REAL upload cells that include searches a site never ranked for. Otherwise every
forecast goes through `GatedPlacement`: P(in top 50 on day 90) times the landing distribution
inside the top 50, with the rest of the mass on NOT IN TOP 50. The updater of such a bundle
learned from the same appeared-only cells, so for a stage forecast it goes through
`GatedUpdater`: on a search where no clean reading on or before the date has the site inside
the top 50, P(in top 50) stays at or under the gate. `live_models` is the only way this module
builds the models a forecast uses, and it always wraps a conditional bundle's placement model
and updater, so the live path cannot skip the gate.

Money: nothing here spends. Training and forecasting are free.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from aeo.ranksim.site_history import site_key

from .asof import LeakError, as_date, parse_utc, utc_iso
from .chain import canonical_json, sha256_text
from .config import NT50, OUTCOME_DAY, STAGE_DAYS, TOP_DEPTH, ForecastPaths, forecast_paths
from .features import MODEL_MEASURES, START_COLUMNS, SYNTHETIC_FOOTPRINT_COLUMNS
from .placement import PlacementModel, dist_median, load_model, save_model
from .scoreboard import ForecastRow, GradeRow, Scoreboard, ScoreboardRefusal, SearchRow, SiteRow, search_key
from .types import PREDICTED, REAL, SYNTHETIC, CleanReading, Forecast, is_excluded_site
from .updater import GSC_FEATURES, Reading, TransitionTable, Updater, UpdaterModel

FORECASTING_VERSION = "forecasting-v1"
BUNDLE_MANIFEST = "bundle.json"
TRAINING_SOURCES: tuple[str, ...] = ("panel", "synthetic", "paid")
RESERVED_MODEL_NAMES: tuple[str, ...] = ("trust", "strength", "minilm-l6")  # folders other parts own under models/
LIVE_SOURCES: tuple[str, ...] = ("seed", "pilot", "synthetic")
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SEED = 20261122
STAGE_READINGS_USED = 2
TRUST_MIN_SITES = 30  # trust.fit_trust_model's grouped CV needs about 20 rows in each training fold
SYNTHETIC_TRAIN_NEW_SITES = 60  # train --data synthetic: enough new sites for a trust model and a held-out marker 2  # the latest clean reading and the one before it, as the panel's updater rows have

# Training cells.
CELLS_UPLOAD_WITH_NEVER_RANKED = "upload_cells_with_never_ranked_searches"
CELLS_APPEARED_ONLY = "cells_of_sites_that_appeared"

# ---------------------------------------------------------------------------
# features the live path builds from one result page (the features.assemble_frame family)

LIVE_MEASURES: tuple[str, ...] = MODEL_MEASURES
STRENGTH_FEATURES: tuple[str, ...] = (
    *(f"st_{m}" for m in LIVE_MEASURES),
    *(f"cl_{m}_{kind}_{top}" for m in LIVE_MEASURES for top in ("top10", "top20", "top50") for kind in ("q", "gap")),
    *(f"pg_{m}_t10_q{q}" for m in LIVE_MEASURES for q in (25, 50, 75)),
    *(f"pg_{m}_t11_20_q50" for m in LIVE_MEASURES),
    *(f"pg_{m}_t21_50_q50" for m in LIVE_MEASURES),
)
PAGE_FEATURES: tuple[str, ...] = ("pg_sites_top10", "pg_sites_top50", "pg_top10_max_site_share", "pg_top50_hhi",
                                  "pg_kd", "pg_log_volume")
START_FEATURES: tuple[str, ...] = tuple(c for c in START_COLUMNS if c != "sd_young")
FORM_FEATURES: tuple[str, ...] = SYNTHETIC_FOOTPRINT_COLUMNS
TRUST_FEATURE = "trust_pred"
PLACEMENT_FEATURES: tuple[str, ...] = (*STRENGTH_FEATURES, *PAGE_FEATURES, *START_FEATURES, *FORM_FEATURES, TRUST_FEATURE)
STRENGTH_ONLY_FEATURES: tuple[str, ...] = STRENGTH_FEATURES  # the site strength only baseline sees nothing else

# ---------------------------------------------------------------------------
# the appearance gate

GATE_MIN_CELLS = 100
GATE_MIN_SITES = 10
GATE_PRIOR = 0.16
GATE_PRIOR_SOURCE = "prior, not fitted (OUR READING of published numbers, docs/plan/PUBLIC_DATA_CATALOG.md)"
GATE_PRIOR_ARITHMETIC = (
    "Ahrefs 2025: 6.11% of English pages with real content reach the top 10 within a year, and 40.82% of those get "
    "there in the first month. If the other 59.18% arrive evenly over months 2 to 12, months 2 and 3 add "
    "59.18% x 2/11 = 10.76%, so 40.82% + 10.76% = 51.58% of the year's top 10 arrivals are in by day 90. "
    "0.0611 x 0.5158 = 0.0315 is the chance of a top 10 place by day 90. Places 11 to 50 are four times as many "
    "slots as 1 to 10. We assume a new page's chance of 11 to 50 is at most four times its chance of 1 to 10, so "
    "0.0315 x 5 = 0.158, rounded to 0.16."
)


class ForecastingRefusal(ValueError):
    """The live path refuses something that would leak, pool, spend or fake a result. The message says why."""


@dataclass(frozen=True)
class GateSpec:
    """Where P(in top 50 on day 90) came from. source is none, scoreboard or prior."""

    source: str
    value: float | None
    cells: int = 0
    sites: int = 0
    note: str = ""

    @property
    def applied(self) -> bool:
        return self.source != "none"

    def tag(self) -> str:
        if not self.applied:
            return "gate-none"
        return f"gate-{self.source}-{float(self.value):.4f}"

    def sentence(self) -> str:
        if not self.applied:
            return "No appearance gate: the placement model was trained on upload cells that include never-ranked searches."
        if self.source == "prior":
            return (f"Appearance gate {float(self.value):.2f} from the {GATE_PRIOR_SOURCE}. "
                    f"Graded seed and pilot outcomes known on the date: {self.cells} cells from {self.sites} sites, "
                    f"fewer than the {GATE_MIN_CELLS} cells from {GATE_MIN_SITES} sites a fitted gate needs.")
        return (f"Appearance gate {float(self.value):.4f} fitted on {self.cells} graded outcomes from {self.sites} sites "
                "known on the date.")

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "value": self.value, "cells": self.cells, "sites": self.sites, "tag": self.tag(),
                "note": self.note, "sentence": self.sentence()}


NO_GATE = GateSpec("none", None, note="the bundle's placement model includes never-ranked upload cells")


def appearance_gate(scoreboard: Scoreboard, *, label: str, as_of: date,
                    min_cells: int = GATE_MIN_CELLS, min_sites: int = GATE_MIN_SITES) -> GateSpec:
    """P(in top 50 on day 90) known on `as_of`, from graded outcomes when there are enough, else the frozen prior.

    GATE_PRIOR derivation (OUR READING, a prior that was not fitted). Published numbers from
    docs/plan/PUBLIC_DATA_CATALOG.md, "Published numbers used as starting points":

    - Ahrefs 2025: 6.11% of English pages with real content reach the top 10 within a year.
    - Of the pages that get there, 40.82% do so in the first month.
    - Assume the other 59.18% arrive evenly over months 2 to 12. Months 2 and 3 add
      59.18% x 2 / 11 = 10.76%. So 40.82% + 10.76% = 51.58% of the year's arrivals are in by day 90.
    - P(top 10 by day 90) = 0.0611 x 0.5158 = 0.0315.
    - Places 11 to 50 are four times as many slots as 1 to 10. Assume a new page's chance of 11 to 50
      is at most four times its chance of 1 to 10: P(top 50 by day 90) <= 0.0315 x 5 = 0.158.
    - Rounded: GATE_PRIOR = 0.16.

    The Ahrefs figure counts a page that reaches the top 10 for any search, which is if anything higher
    than for one named target search, so the prior errs toward calling top 50 more often, never toward
    a better looking NOT IN TOP 50 rate. PLAN_AUDIT section D: none of these numbers is a Ranksim result.

    Fitted gate. The share of graded (site, search) cells whose day 90 outcome is in the top 50,
    over REAL seed and pilot forecasts for a REAL site (SYNTHETIC forecasts for a synthetic site),
    brand searches left out, only grades whose outcome reading was taken on or before `as_of`.
    Used only with at least GATE_MIN_CELLS cells from at least GATE_MIN_SITES sites. Frozen choice:
    the plan's seed sites are 10 sites of 20 searches; a share from fewer sites rests on one or two
    sites whose searches rise and fall together (PLAN_AUDIT A3), and 100 cells from 10 sites is half
    of the seed design, the first point where a site-clustered share is more than a handful of sites.
    """
    sources = ("synthetic",) if label == SYNTHETIC else ("seed", "pilot")
    day = as_date(as_of)
    with Session(scoreboard.engine) as session:
        rows = session.execute(
            select(ForecastRow.site, ForecastRow.search_key, ForecastRow.is_brand, GradeRow.outcome_position,
                   GradeRow.reading_taken_at, GradeRow.kind)
            .join(GradeRow, GradeRow.forecast_id == ForecastRow.forecast_id)
            .where(ForecastRow.source.in_(sources))
        ).all()
    outcomes: dict[tuple[str, str], int] = {}
    for site, key, is_brand, position, taken_at, kind in rows:
        if is_brand or kind != "original" or parse_utc(taken_at).date() > day:
            continue
        outcomes.setdefault((site, key), int(position))
    cells = len(outcomes)
    sites = len({site for site, _ in outcomes})
    if cells >= min_cells and sites >= min_sites:
        share = sum(1 for p in outcomes.values() if p <= TOP_DEPTH) / cells
        return GateSpec("scoreboard", float(share), cells, sites,
                        note=f"share of graded {'/'.join(sources)} cells in the top 50 on day 90, known on {day}")
    return GateSpec("prior", GATE_PRIOR, cells, sites, note=GATE_PRIOR_ARITHMETIC)


def apply_gate(dists: np.ndarray, gate: float) -> np.ndarray:
    """gate x (the landing distribution inside the top 50, renormalised), the rest on NT50. Rows sum to 1."""
    arr = np.atleast_2d(np.asarray(dists, dtype=float))
    if not 0.0 <= float(gate) <= 1.0:
        raise ForecastingRefusal(f"an appearance gate is a probability, not {gate}")
    inside = arr[:, :TOP_DEPTH]
    mass = inside.sum(axis=1, keepdims=True)
    out = np.zeros_like(arr)
    shape = np.divide(inside, mass, out=np.zeros_like(inside), where=mass > 0)
    out[:, :TOP_DEPTH] = float(gate) * shape
    out[:, TOP_DEPTH] = 1.0 - out[:, :TOP_DEPTH].sum(axis=1)
    return out


@dataclass
class GatedPlacement:
    """A placement model whose distributions pass through the appearance gate. Duck-types PlacementModel."""

    base: PlacementModel
    gate: GateSpec
    bundle_version: str

    @property
    def training_label(self) -> str:
        return self.base.training_label

    def distributions(self, frame: pd.DataFrame) -> np.ndarray:
        dists = self.base.distributions(frame)
        return apply_gate(dists, float(self.gate.value)) if self.gate.applied else dists

    def model_version(self) -> str:
        return f"{self.bundle_version}|{self.base.model_version()}|{self.gate.tag()}"

    def forecast(self, dist: np.ndarray, *, site: str, search: str, stage: str, as_of: date,
                 notes: Mapping[str, Any] | None = None, target_url: str | None = None) -> Forecast:
        made = self.base.forecast(dist, site=site, search=search, stage=stage, as_of=as_of, notes=notes, target_url=target_url)
        return replace(made, model_version=self.model_version(), notes={**made.notes, "gate": self.gate.to_dict()})


def cap_at_gate(dists: np.ndarray, gate: float) -> np.ndarray:
    """Rows whose top 50 mass is above `gate` are scaled down to it (apply_gate); rows at or below it are unchanged."""
    arr = np.atleast_2d(np.asarray(dists, dtype=float)).copy()
    over = arr[:, :TOP_DEPTH].sum(axis=1) > float(gate) + 1e-12
    if over.any():
        arr[over] = apply_gate(arr[over], float(gate))
    return arr


@dataclass
class GatedUpdater:
    """The updater of a conditional bundle, capped by the appearance gate while the site has not appeared.

    A conditional bundle's updater learned from cells of sites that appeared on the search (on the
    input page or the outcome page), so a reading where the site is NOT IN TOP 50 comes, in training,
    from a site that was on the outcome page. Applied to a site that has appeared nowhere, that
    evidence of absence would push the forecast INTO the top 50, past the gate. So for a cell where
    no clean reading on or before the date has the site inside the top 50 (`appeared` False or not
    known), P(in top 50) is capped at the gate value. Once a real reading shows the site inside the
    top 50, the updater's answer is used as it is. Duck-types updater.Updater for backtest.forecast_frame.
    """

    base: Updater
    gate: GateSpec
    appeared: dict[str, bool] = field(default_factory=dict)  # search -> a clean reading has the site in the top 50

    @property
    def in_use(self) -> str:
        return self.base.in_use

    def version(self) -> str:
        return f"{self.base.version()}|stage-cap-{self.gate.tag()}"

    def _capped_rows(self, frame: pd.DataFrame) -> np.ndarray:
        searches = frame["search"].astype(str).tolist() if "search" in frame else [""] * len(frame)
        return np.array([not self.appeared.get(s, False) for s in searches], dtype=bool)

    def distributions(self, frame: pd.DataFrame, prior_dists: np.ndarray, which: str | None = None) -> np.ndarray:
        out = np.asarray(self.base.distributions(frame, prior_dists, which), dtype=float).copy()
        if not self.gate.applied:
            return out
        capped = self._capped_rows(frame.reset_index(drop=True))
        if capped.any():
            out[capped] = cap_at_gate(out[capped], float(self.gate.value))
        return out

    def forecast(self, dist: np.ndarray, *, site: str, search: str, stage: str, as_of: date,
                 notes: Mapping[str, Any] | None = None, segments: Mapping[str, bool] | None = None) -> Forecast:
        made = self.base.forecast(dist, site=site, search=search, stage=stage, as_of=as_of, notes=notes, segments=segments)
        appeared = bool(self.appeared.get(search, False))
        cap = {"applied": self.gate.applied and not appeared, "site_appeared_in_a_reading": appeared,
               "gate": self.gate.tag(),
               "rule": "P(top 50) capped at the gate until a clean reading shows the site inside the top 50"}
        return replace(made, model_version=self.version(), notes={**made.notes, "stage_gate_cap": cap})


# ---------------------------------------------------------------------------
# bundles on disk


@dataclass
class ModelBundle:
    """A trained bundle: placement, the site strength only placement, the updater and trust. PREDICTED output."""

    name: str
    folder: Path
    manifest: dict[str, Any]
    placement: PlacementModel
    strength_only: PlacementModel
    updater: Updater | None
    trust: Any | None

    @property
    def sha256(self) -> str:
        return str(self.manifest["bundle_sha256"])

    @property
    def version(self) -> str:
        return f"{FORECASTING_VERSION}:{self.name}:{self.sha256[:12]}"

    @property
    def training_label(self) -> str:
        return str(self.manifest["training_label"])

    @property
    def conditional(self) -> bool:
        """True when the placement model only knows sites that appeared: forecasts then pass the gate."""
        return not (bool(self.manifest.get("includes_never_ranked")))

    @property
    def training_cutoff(self) -> date:
        return date.fromisoformat(str(self.manifest["training_cutoff"]))


def models_root(paths: ForecastPaths) -> Path:
    return paths.models


def check_bundle_name(name: str) -> str:
    text = str(name or "").strip()
    if not NAME_PATTERN.match(text):
        raise ForecastingRefusal(f"{name!r} is not a bundle name. Use lower case letters, digits, dots, dashes, at most 64.")
    if text in RESERVED_MODEL_NAMES:
        raise ForecastingRefusal(f"{text} is the folder of another part under the models folder. Pick another name.")
    return text


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.floating):
        return _json_safe(float(value))
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_updater(updater: Updater, path: Path) -> str:
    """Write the updater (table, model, choice) as JSON. updater.py has no file format of its own."""
    text = json.dumps(_json_safe(updater.payload()), sort_keys=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_updater(path: Path) -> Updater:
    data = json.loads(path.read_text(encoding="utf-8"))
    table = dict(data["table"])
    model = dict(data["model"]) if data.get("model") else None
    for item in (table, model):
        if item is not None:
            item.pop("_cached", None)
    return Updater(table=TransitionTable(**table), model=UpdaterModel(**model) if model else None,
                   in_use=str(data["in_use"]), reason=str(data["reason"]), range_level=float(data["range_level"]),
                   comparison=dict(data.get("comparison") or {}))


def bundle_hash(manifest: Mapping[str, Any]) -> str:
    """sha256 over the manifest body, which holds the sha256 of every file. A changed flag or file breaks it."""
    body = {k: v for k, v in manifest.items() if k != "bundle_sha256"}
    return sha256_text(canonical_json(_json_safe(body)))


def save_bundle(paths: ForecastPaths, name: str, *, placement: PlacementModel, strength_only: PlacementModel,
                updater: Updater | None, trust: Any | None, manifest: Mapping[str, Any]) -> ModelBundle:
    """Write a new bundle under data/forecast/models/<name>/. An existing bundle is never replaced."""
    from .trust import save_trust_model

    name = check_bundle_name(name)
    folder = models_root(paths) / name
    if (folder / BUNDLE_MANIFEST).exists():
        raise ForecastingRefusal(f"{folder} already holds a bundle. A bundle is never replaced: train under a new --name.")
    folder.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, str]] = {}
    save_model(placement, folder / "placement.json")
    save_model(strength_only, folder / "strength_only.json")
    files["placement"] = {"file": "placement.json", "sha256": _file_sha(folder / "placement.json")}
    files["strength_only"] = {"file": "strength_only.json", "sha256": _file_sha(folder / "strength_only.json")}
    if updater is not None:
        save_updater(updater, folder / "updater.json")
        files["updater"] = {"file": "updater.json", "sha256": _file_sha(folder / "updater.json")}
    if trust is not None:
        trust_dir = folder / "trust"
        path, _digest = save_trust_model(trust, {"bundle": name, "label": PREDICTED}, trust_dir)
        files["trust"] = {"file": str(path.relative_to(folder)), "sha256": _file_sha(path)}
    body = {**_json_safe(dict(manifest)), "bundle_format": FORECASTING_VERSION, "name": name, "files": files,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    body["bundle_sha256"] = bundle_hash(body)
    with open(folder / BUNDLE_MANIFEST, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(body, indent=2, sort_keys=True))
    return load_bundle(paths, name)


def load_bundle(paths: ForecastPaths, name: str) -> ModelBundle:
    """Load a bundle and check every hash. A file or a manifest flag that changed is refused."""
    from .trust import load_trust_model

    name = check_bundle_name(name)
    folder = models_root(paths) / name
    manifest_path = folder / BUNDLE_MANIFEST
    if not manifest_path.is_file():
        found = ", ".join(list_bundles(paths)) or "none"
        raise ForecastingRefusal(f"No bundle named {name} under {models_root(paths)}. Bundles there: {found}. "
                                 "Train one with: python -m aeo.ranksim forecast train --data synthetic|panel")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if bundle_hash(manifest) != manifest.get("bundle_sha256"):
        raise ForecastingRefusal(f"{manifest_path} does not match its own sha256. The bundle was changed after training.")
    files = manifest.get("files") or {}
    for role, item in files.items():
        target = folder / str(item["file"])
        if not target.is_file() or _file_sha(target) != item["sha256"]:
            raise ForecastingRefusal(f"The {role} file {target} is missing or does not match the sha256 in {manifest_path}.")
    placement = load_model(folder / files["placement"]["file"])
    strength_only = load_model(folder / files["strength_only"]["file"])
    updater = load_updater(folder / files["updater"]["file"]) if "updater" in files else None
    trust = load_trust_model(folder / files["trust"]["file"]) if "trust" in files else None
    if placement.training_label != manifest.get("training_label"):
        raise ForecastingRefusal(f"The placement model says {placement.training_label} and the manifest says "
                                 f"{manifest.get('training_label')}. The bundle is refused.")
    return ModelBundle(name, folder, manifest, placement, strength_only, updater, trust)


def list_bundles(paths: ForecastPaths) -> list[str]:
    root = models_root(paths)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / BUNDLE_MANIFEST).is_file())


def newest_bundle(paths: ForecastPaths, *, label: str | None = None) -> str | None:
    """The bundle written last (by its manifest's created_at), or None. `label` keeps REAL or SYNTHETIC bundles only."""
    best: tuple[str, str] | None = None
    for name in list_bundles(paths):
        data = json.loads((models_root(paths) / name / BUNDLE_MANIFEST).read_text(encoding="utf-8"))
        if label is not None and data.get("training_label") != label:
            continue
        key = (str(data.get("created_at") or ""), name)
        if best is None or key > best:
            best = key
    return best[1] if best else None


# ---------------------------------------------------------------------------
# training


@dataclass
class TrainResult:
    """What train() wrote. The bundle's outputs are PREDICTED; its training label is REAL or SYNTHETIC."""

    bundle: ModelBundle | None
    data: str
    label: str | None
    counts: dict[str, Any] = field(default_factory=dict)
    marker2: dict[str, Any] | None = None
    refusal: str | None = None
    notes: list[str] = field(default_factory=list)
    training_cells: list[Any] = field(default_factory=list)  # GradedCell per training upload cell (band prior)

    def to_dict(self) -> dict[str, Any]:
        out = {"data": self.data, "label": self.label, "counts": self.counts, "marker2": self.marker2,
               "refusal": self.refusal, "notes": list(self.notes)}
        if self.bundle is not None:
            out["bundle"] = {"name": self.bundle.name, "folder": str(self.bundle.folder), "version": self.bundle.version,
                             "sha256": self.bundle.sha256, "manifest": self.bundle.manifest}
        return out


def _usable_features(frame: pd.DataFrame, wanted: Sequence[str]) -> tuple[list[str], list[str]]:
    """(features with at least one value in the training rows, features dropped because they never have one)."""
    kept, dropped = [], []
    for name in wanted:
        if name in frame and np.isfinite(pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)).any():
            kept.append(name)
        else:
            dropped.append(name)
    return kept, dropped


def fit_bundle(
    paths: ForecastPaths,
    name: str,
    upload_rows: pd.DataFrame,
    updater_rows: pd.DataFrame | None,
    *,
    label: str,
    source: str,
    includes_never_ranked: bool,
    training_cutoff: date,
    group_col: str,
    trust: Any | None,
    difficulty_metric: str | None,
    split_note: str,
    seed: int = SEED,
    extra_manifest: Mapping[str, Any] | None = None,
) -> ModelBundle:
    """Fit placement, the site strength only placement and the updater on training rows, then write the bundle.

    `upload_rows`: one row per training cell with the features and `position_out` (REAL or SYNTHETIC).
    `updater_rows`: reading rows (reading_position, lead_days, new_to_search, position_out and the
    features) or None. The updater's prior is cross-fitted by group so it never learns an in-sample prior.
    """
    from .backtest import VALIDATION_SHARE, cross_fitted_prior, sample_training
    from .features import stable_share
    from .placement import fit_placement
    from .updater import UpdaterError, choose_version, fit_model, fit_table, prior_columns

    if label not in (REAL, SYNTHETIC):
        raise ForecastingRefusal("a training label is REAL or SYNTHETIC")
    if len(upload_rows) == 0:
        raise ForecastingRefusal("there are no training rows")
    feats, dropped = _usable_features(upload_rows, PLACEMENT_FEATURES)
    strength_feats, _ = _usable_features(upload_rows, STRENGTH_ONLY_FEATURES)
    if not feats or not strength_feats:
        raise ForecastingRefusal("no feature has a value in the training rows")
    placement = fit_placement(upload_rows, feats, training_label=label, seed=seed, group_col=group_col, name=f"{name}-placement")
    strength_only = fit_placement(upload_rows, strength_feats, training_label=label, seed=seed, group_col=group_col,
                                  name=f"{name}-strength-only")
    updater = None
    updater_counts: dict[str, Any] = {"rows": 0}
    if updater_rows is not None and len(updater_rows):
        rows = updater_rows.reset_index(drop=True)
        if "keyword_id" in rows:
            rows = sample_training(rows, 200_000, seed).reset_index(drop=True)
        prior = cross_fitted_prior(rows, feats, label=label, seed=seed, group_col=group_col,
                                   settings=(placement.cv.get("chosen") or {}).get("params"))
        cols = prior_columns(prior)
        for col in cols.columns:
            rows[col] = cols[col].to_numpy()
        is_val = np.array([stable_share(f"bundle-val|{g}", seed) < VALIDATION_SHARE for g in rows[group_col].astype(str)])
        fit_rows, val_rows = rows[~is_val].reset_index(drop=True), rows[is_val].reset_index(drop=True)
        if len(fit_rows) == 0:
            fit_rows, val_rows, is_val = rows, rows.iloc[0:0], np.zeros(len(rows), dtype=bool)
        table = fit_table(fit_rows, training_label=label, seed=seed, group_col=group_col)
        try:
            model = fit_model(fit_rows, training_label=label, seed=seed, group_col=group_col)
        except UpdaterError:
            model = None
        search_col = "search" if "search" in val_rows else ("keyword" if "keyword" in val_rows else group_col)
        updater = choose_version(table, model, val_rows, prior[is_val], search_col=search_col)
        updater_counts = {"rows": int(len(rows)), "fit_rows": int(len(fit_rows)), "validation_rows": int(len(val_rows)),
                          "in_use": updater.in_use, "reason": updater.reason}
    counts = {"upload_rows": int(len(upload_rows)),
              "sites": int(upload_rows["site"].nunique()) if "site" in upload_rows else None,
              "searches": int(upload_rows["search"].nunique()) if "search" in upload_rows else None,
              "updater": updater_counts}
    manifest = {
        "training_source": source, "training_label": label,
        "training_cells": CELLS_UPLOAD_WITH_NEVER_RANKED if includes_never_ranked else CELLS_APPEARED_ONLY,
        "includes_never_ranked": bool(includes_never_ranked), "training_cutoff": as_date(training_cutoff).isoformat(),
        "features": feats, "features_dropped_no_value": dropped, "strength_only_features": strength_feats,
        "updater_features": list(updater.model.features) if updater is not None and updater.model is not None else [],
        "difficulty_metric": difficulty_metric, "split": split_note, "counts": counts, "group_col": group_col,
        "placement_version": placement.model_version(), "strength_only_version": strength_only.model_version(),
        "updater_version": updater.version() if updater is not None else None,
        "trust": None if trust is None else {"version": getattr(trust, "version", None),
                                             "feature_set": getattr(trust, "feature_set", None),
                                             "features": list(getattr(trust, "features", []))},
        **dict(extra_manifest or {}),
    }
    return save_bundle(paths, name, placement=placement, strength_only=strength_only, updater=updater, trust=trust,
                       manifest=manifest)


def _graded_training_cells(rows: pd.DataFrame, *, source: str, stage: str = "upload") -> list[Any]:
    """GradedCell per training row (point = actual): what the band prior baseline is fitted on. Training data only."""
    from .grading import GradedCell

    out = []
    for site, search, actual, diff in zip(rows["site"], rows["search"], rows["position_out"],
                                          rows["difficulty"] if "difficulty" in rows else [None] * len(rows)):
        out.append(GradedCell(site=str(site), search=str(search), point=int(actual), actual=int(actual),
                              difficulty=diff if isinstance(diff, str) else None, source=source, stage=stage))
    return out


def synthetic_training_frames(world: Any, sites: Sequence[str]) -> tuple[pd.DataFrame, pd.DataFrame, Any]:
    """(upload rows, updater rows, trust model) for training sites of a synthetic world. SYNTHETIC.

    Upload rows are features.assemble_frame cells of each training site on its target searches as of its
    launch, with the true day 90 position (never-ranked searches included). Updater rows are the same
    cells at day 7, 14, 30 and 60 with clean readings (reader.read_clean on synth.SyntheticSource).
    Trust is fitted on the training sites only.
    """
    from .backtest import SYN_STAGES, _syn_stage_rows, synthetic_readings, synthetic_upload_frame
    from .trust import SYNTHETIC_FEATURES, fit_trust_model, synthetic_trust_frame

    keep = set(sites)
    frame = synthetic_upload_frame(world)
    frame = frame[frame["site"].isin(keep)].reset_index(drop=True)
    if frame.empty:
        raise ForecastingRefusal("the synthetic world has no upload cells for the training sites")
    tframe = synthetic_trust_frame(world)
    t_train = tframe[tframe["site"].isin(keep)].reset_index(drop=True) if len(tframe) else tframe
    trust = None
    if len(t_train) >= TRUST_MIN_SITES:
        small_grid = ({"num_leaves": 4, "min_child_samples": 10}, {"num_leaves": 7, "min_child_samples": 20})
        trust = fit_trust_model(t_train, feature_set="synthetic_footprint", features=SYNTHETIC_FEATURES, grid=small_grid,
                                seed=world.seed)
    frame = frame.copy()
    frame["trust_pred"] = trust.predict(frame) if trust is not None else np.nan
    readings = synthetic_readings(world, {s: world.new_sites[s].targets for s in sorted(keep) if s in world.new_sites})
    parts = []
    for stage in SYN_STAGES[1:]:
        part = _syn_stage_rows(frame, readings, stage)
        part["stage"] = stage
        parts.append(part)
    return frame, pd.concat(parts, ignore_index=True), trust


def train_synthetic(paths: ForecastPaths, world: Any, *, name: str, sites: Sequence[str] | None = None,
                    holdout_share: float = 0.3) -> TrainResult:
    """Train a SYNTHETIC bundle on a synthetic world's new sites (all of them, or `sites`).

    The training cutoff is the last day 90 outcome of a training site, so a forecast dated earlier is
    refused. Marker 2 is printed on held-out training sites, labeled SYNTHETIC, and can never pass.
    """
    chosen = sorted(sites) if sites is not None else sorted(world.new_sites)
    usable = [s for s in chosen if world.new_sites[s].launch_date + timedelta(days=OUTCOME_DAY) < world.end]
    if not usable:
        raise ForecastingRefusal("no training site of the synthetic world reaches day 90 inside the world")
    upload_rows, updater_rows, trust = synthetic_training_frames(world, usable)
    cutoff = max(world.new_sites[s].launch_date for s in usable) + timedelta(days=OUTCOME_DAY)
    bundle = fit_bundle(paths, name, upload_rows, updater_rows, label=SYNTHETIC, source="synthetic",
                        includes_never_ranked=True, training_cutoff=cutoff, group_col="site", trust=trust,
                        difficulty_metric=SYNTHETIC_DIFFICULTY_METRIC,
                        split_note="every training site of the synthetic world; the forecast sites are other sites",
                        extra_manifest={"world_seed": int(world.seed), "training_sites": usable})
    result = TrainResult(bundle, "synthetic", SYNTHETIC, counts=dict(bundle.manifest["counts"]),
                         training_cells=_graded_training_cells(upload_rows, source="synthetic"))
    result.marker2 = synthetic_marker2(world, usable, holdout_share=holdout_share)
    result.notes.append("SYNTHETIC. This bundle forecasts synthetic sites only. It never forecasts a seed or pilot site.")
    return result


SYNTHETIC_DIFFICULTY_METRIC = "synthetic difficulty (true score at position 10 on the first day)"
PANEL_DIFFICULTY_METRIC = "DataForSEO keyword difficulty"


def synthetic_marker2(world: Any, sites: Sequence[str], *, holdout_share: float = 0.3) -> dict[str, Any]:
    """Marker 2 on held-out synthetic training sites. SYNTHETIC: the verdict can never pass."""
    from .features import stable_share
    from .markers import evaluate_marker
    from .trust import SYNTHETIC_FEATURES, evaluate_pairs, fit_trust_model, synthetic_trust_frame

    frame = synthetic_trust_frame(world)
    frame = frame[frame["site"].isin(set(sites))].reset_index(drop=True) if len(frame) else frame
    held = np.array([stable_share(f"marker2|{s}", SEED) < holdout_share for s in frame["site"]]) if len(frame) else np.array([])
    train, test = frame[~held].reset_index(drop=True), frame[held].reset_index(drop=True)
    if len(train) < TRUST_MIN_SITES or len(test) < 2:
        return {"label": SYNTHETIC, "verdict": None,
                "note": f"too few synthetic sites for marker 2 ({len(train)} to fit, {len(test)} held out)"}
    small_grid = ({"num_leaves": 4, "min_child_samples": 10}, {"num_leaves": 7, "min_child_samples": 20})
    model = fit_trust_model(train, feature_set="synthetic_footprint", features=SYNTHETIC_FEATURES, grid=small_grid,
                            seed=world.seed)
    test = test.assign(keyword_id=test["niche"].astype(str), snapshot_time="synthetic", st_s_cc_hosts=test["st_s_cc_hc"])
    evaluation = evaluate_pairs(test, {"model": model.predict(test)}, threshold=0.0,
                                single_measures=("st_s_cc_hc", "st_s_cc_hosts"), young_col=None)
    report = marker2_input(evaluation, "model", label=SYNTHETIC, stand_in_reason=None)
    verdict = evaluate_marker(2, report)
    return {"label": SYNTHETIC, "input": report, "verdict": verdict,
            "note": "Pairs are held-out synthetic sites of one niche whose planted trust differs at all (threshold 0)."}


def marker2_input(evaluation: Mapping[str, Any], model_name: str, *, label: str, stand_in_reason: str | None) -> dict[str, Any]:
    """The dict markers.evaluate_marker(2, ...) reads, with BOTH gains: over link count (the verdict) and over
    the best single measure (stricter, printed beside it)."""
    from .features import LINK_COUNT_MEASURE

    entry = evaluation["models"][model_name]
    pairs = entry["pairs"]
    link = entry.get("gain_over_link_count") or {}
    best = entry.get("gain_over_best_single_measure") or {}
    out: dict[str, Any] = {
        "label": label,
        "pairs": {"count": pairs["count"], "rate": pairs["rate"], "two_way_interval": pairs.get("two_way_interval")},
        "gain_over_link_count": {"difference": link.get("difference"), "difference_interval": link.get("difference_interval"),
                                 "baseline": f"st_{LINK_COUNT_MEASURE}" if link else None},
        "gain_over_best_single_measure": {"difference": best.get("difference"),
                                          "difference_interval": best.get("difference_interval"),
                                          "baseline": entry.get("best_single_measure")},
        "counts": {"right": pairs.get("right"), "wrong": pairs.get("wrong"), "tied": pairs.get("tied"), "missing": 0},
    }
    if stand_in_reason:
        out["stand_in"] = True
        out["stand_in_reason"] = stand_in_reason
    return out


TRUST_STAND_IN_REASON = ("it is judged on sites that already rank in the free panel, with result titles for content, "
                         "not on the plan's 20,000 new sites with traffic curves")


def newest_trust_report(root: str | Path | None = None) -> Path | None:
    """The newest scored trust report under evals/forecast/trust (report-v<k>.json beats report.json)."""
    from .features import evals_dir

    folder = Path(root) if root else evals_dir("trust")
    if not folder.is_dir():
        return None
    best: tuple[int, Path] | None = None
    for path in folder.glob("report*.json"):
        stem = path.stem
        if stem == "report":
            version = 1
        elif stem.startswith("report-v") and stem[8:].isdigit():
            version = int(stem[8:])
        else:
            continue
        if best is None or version > best[0]:
            best = (version, path)
    return best[1] if best else None


def marker2_from_trust_report(path: str | Path) -> dict[str, Any]:
    """Judge marker 2 on a scored trust report, read only. The free panel version is a stand-in: never a pass or fail."""
    from .markers import evaluate_marker

    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("rehearsal"):
        raise ForecastingRefusal(f"{path} is a rehearsal report. Marker 2 is judged on a scored report only.")
    primary = report["primary"]
    evaluation = report["runs"][primary]["evaluation"]
    label = SYNTHETIC if report.get("label") == SYNTHETIC else REAL
    marker_input = marker2_input(evaluation, "free_gbt", label=label, stand_in_reason=TRUST_STAND_IN_REASON)
    verdict = evaluate_marker(2, marker_input)
    return {"report_file": str(path), "version": report.get("version"), "label": label, "input": marker_input,
            "verdict": verdict, "what_this_is": report.get("what_this_is")}


def panel_frame_path(paths: ForecastPaths, given: str | Path | None = None) -> Path | None:
    """The panel frame to train on: `given`, else the newest panel_frame_*.parquet under the cache."""
    if given:
        return Path(given)
    folder = paths.cache / "features"
    found = sorted(folder.glob("panel_frame_*.parquet"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return found[-1] if found else None


def panel_training_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """The TRAINING side of the frozen split only: training search group, training site, outcome before the cutoff.

    The split object is strength.DEFAULT_SPLIT, the one backtest.py and trust.py use. A held-out row is
    never returned, and a frame whose rows are all held out gives an empty frame. Rows whose outcome page
    is shallower than 50 are dropped (absence there is not NT50).
    """
    from .strength import DEFAULT_SPLIT

    if frame.empty:
        return frame
    cut = DEFAULT_SPLIT.cutoff
    outcome = [as_date(d) for d in frame["outcome_date"]]
    test_search = np.array([bool(DEFAULT_SPLIT.search_is_test(str(k))) for k in frame["keyword"]])
    test_site = np.array([bool(DEFAULT_SPLIT.site_is_test(str(s))) for s in frame["site"]])
    before = np.array([d < cut for d in outcome])
    keep = ~test_search & ~test_site & before
    if "depth_ok" in frame:
        keep &= frame["depth_ok"].fillna(False).astype(bool).to_numpy()
    if "label" in frame:
        keep &= (frame["label"].astype(str) == REAL).to_numpy()
    out = frame[keep].reset_index(drop=True)
    out["search"] = out["keyword"]
    return out


def train_panel(paths: ForecastPaths, *, name: str, frame_path: str | Path | None = None,
                log: Callable[[str], None] | None = None) -> TrainResult:
    """Train a REAL bundle on the training side of the free panel frame. It never reads a held-out outcome.

    Placement rows are cells where the site is absent from the input page (the entrant analog), so the
    model is conditional on the site appearing on the outcome page: every live forecast with it passes the
    appearance gate. Updater rows are every training cell, with the input page as the reading. Trust is the
    frozen trust model the backtest names (fitted on training searches and training sites only).
    """
    from .backtest import TRAIN_CELLS_MAX, TRUST_MODEL_NAME, sample_training
    from .strength import DEFAULT_SPLIT
    from .trust import default_models_dir, load_trust_model

    path = panel_frame_path(paths, frame_path)
    if path is None or not path.is_file():
        return TrainResult(None, "panel", REAL, refusal=(
            "No panel frame is built. Build it with: python -m aeo.ranksim.forecast.features build-panel "
            "(after: python -m aeo.ranksim forecast panel build). Then train again."))
    if log:
        log(f"reading {path}")
    frame = pd.read_parquet(path)
    rows = panel_training_rows(frame)
    del frame
    if rows.empty:
        return TrainResult(None, "panel", REAL, refusal=f"{path} has no training rows on the frozen split.")
    rows = sample_training(rows, TRAIN_CELLS_MAX, SEED).reset_index(drop=True)
    trust_path = default_models_dir() / f"{TRUST_MODEL_NAME}.json"
    trust = load_trust_model(trust_path) if trust_path.is_file() else None
    rows["trust_pred"] = trust.predict(rows) if trust is not None else np.nan
    rank_in = pd.to_numeric(rows["rank100_in"], errors="coerce")
    deep_in = pd.to_numeric(rows["depth_input"], errors="coerce") >= TOP_DEPTH
    upload_rows = rows[rank_in.isna() & deep_in].reset_index(drop=True)
    updater_rows = rows.copy()
    updater_rows["reading_position"] = np.where(rank_in.notna(), np.minimum(rank_in.fillna(NT50), NT50),
                                                np.where(deep_in, NT50, np.nan))
    updater_rows["lead_days"] = pd.to_numeric(updater_rows["gap_days"], errors="coerce").round()
    updater_rows["new_to_search"] = np.nan
    updater_rows["readings_count"] = np.where(np.isfinite(updater_rows["reading_position"]), 1.0, 0.0)
    updater_rows["reading_in_top50"] = np.where(np.isfinite(updater_rows["reading_position"]),
                                                (updater_rows["reading_position"] <= TOP_DEPTH).astype(float), np.nan)
    cutoff = max(as_date(d) for d in rows["outcome_date"])
    if cutoff >= DEFAULT_SPLIT.cutoff:
        raise LeakError(f"a training outcome on {cutoff} is on or after the split cutoff {DEFAULT_SPLIT.cutoff}")
    bundle = fit_bundle(paths, name, upload_rows, updater_rows, label=REAL, source="panel", includes_never_ranked=False,
                        training_cutoff=cutoff, group_col="search_group", trust=trust,
                        difficulty_metric=PANEL_DIFFICULTY_METRIC,
                        split_note=(f"{DEFAULT_SPLIT.version}: training search groups and training sites, outcomes before "
                                    f"{DEFAULT_SPLIT.cutoff}; no held-out row is read"),
                        extra_manifest={"panel_frame": str(path), "panel_frame_sha256": _file_sha(path),
                                        "trust_file": str(trust_path) if trust is not None else None,
                                        "trust_file_sha256": _file_sha(trust_path) if trust is not None else None})
    result = TrainResult(bundle, "panel", REAL, counts=dict(bundle.manifest["counts"]))
    if trust is None:
        result.notes.append(f"The frozen trust model {trust_path} is missing, so trust_pred is missing (NaN) in every row.")
    report_path = newest_trust_report()
    result.marker2 = marker2_from_trust_report(report_path) if report_path else None
    result.notes.append("CONDITIONAL: the panel only has cells of sites that appeared on a search. Every upload forecast "
                        "with this bundle passes the appearance gate.")
    return result


PAID_PULL_COMMANDS: dict[str, str] = {
    "past result pages": "python -m aeo.ranksim forecast pull past-pages --universe <universe parquet> --months 24 --execute",
    "traffic curves": "python -m aeo.ranksim forecast pull traffic --cohort <cohort parquet> --execute",
    "exact positions": "python -m aeo.ranksim forecast pull positions --cohort <cohort parquet> --max-sites 2000 --execute",
}


def paid_files_status(paths: ForecastPaths) -> dict[str, bool]:
    """Which outputs of the three paid pulls exist under the cache (trainingset.py writes them)."""
    base = paths.cache / "trainingset"
    labs = paths.cache / "panel_labs"
    return {
        "past result pages": labs.is_dir() and any(labs.rglob("*.parquet")),
        "traffic curves": base.is_dir() and any(base.glob("*/traffic_months.parquet")),
        "exact positions": base.is_dir() and any(base.glob("*/positions.parquet")),
    }


def train(paths: ForecastPaths, *, data: str, name: str | None = None, world: Any | None = None,
          sites: Sequence[str] | None = None, frame_path: str | Path | None = None,
          log: Callable[[str], None] | None = None) -> TrainResult:
    """Fit trust, placement and updater on ONE training source and write the bundle. Never scores held-out rows."""
    if data not in TRAINING_SOURCES:
        raise ForecastingRefusal(f"--data must be one of {', '.join(TRAINING_SOURCES)}")
    chosen = check_bundle_name(name or f"{data}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
    if data == "synthetic":
        from .synth import World

        made = world if world is not None else World(SEED % 1000, new_sites=SYNTHETIC_TRAIN_NEW_SITES)
        return train_synthetic(paths, made, name=chosen, sites=sites)
    if data == "panel":
        return train_panel(paths, name=chosen, frame_path=frame_path, log=log)
    status = paid_files_status(paths)
    missing = [k for k, ok in status.items() if not ok]
    if missing:
        commands = "; ".join(f"{k}: {PAID_PULL_COMMANDS[k]}" for k in missing)
        return TrainResult(None, "paid", REAL, refusal=(
            f"The paid training set is not on disk. Missing: {', '.join(missing)}. Make it with: {commands}. "
            "Each pull is a dry run without --execute and needs an approval and its $1 sample first."))
    return TrainResult(None, "paid", REAL, refusal=(
        "The paid pull files exist, but the step that joins them into upload cells with never-ranked searches is not "
        "built yet. Nothing was trained. Tell the engineer."))


# ---------------------------------------------------------------------------
# the live feature provider


StrengthFn = Callable[[pd.DataFrame], pd.DataFrame]


def real_strength_fn(paths: ForecastPaths) -> StrengthFn:
    """Dated REAL strength through features.cached_strength (every value through an AsOfGuard)."""
    from .features import cached_strength

    def strength(site_dates: pd.DataFrame) -> pd.DataFrame:
        out = cached_strength(site_dates, cache_path=paths.cache / "features" / "strength_by_site_date.parquet",
                              root=paths.root)
        out["as_of"] = [as_date(d) for d in out["as_of"]]
        return out

    return strength


@dataclass
class LiveInputs:
    """Where the live provider reads from. Each part may be injected (tests, the demo)."""

    scoreboard: Scoreboard
    paths: ForecastPaths
    strength: StrengthFn
    label: str
    now: datetime
    evidence: Any | None = None
    content_scorer: Any | None = None
    notes: list[str] = field(default_factory=list)


def latest_page(inputs: LiveInputs, search: str, location_code: int, device: str, day: date) -> CleanReading | None:
    """The latest stored non-degraded clean reading of the search on or before `day`, with the site's label."""
    for reading in inputs.scoreboard.readings_for(search, location_code, device, on_or_before=day, label=inputs.label,
                                                  non_degraded_only=True):
        if parse_utc(reading.taken_at) <= parse_utc(utc_iso(inputs.now)):
            return reading
    return None


def _page_frame(reading: CleanReading) -> pd.DataFrame:
    rows = [{"site": p.site, "rank100": int(p.position)} for p in reading.positions
            if p.position <= TOP_DEPTH and not is_excluded_site(p.site)]
    frame = pd.DataFrame(rows, columns=["site", "rank100"])
    frame["keyword_id"] = "live"
    frame["snapshot_time"] = pd.Timestamp(parse_utc(reading.taken_at))
    return frame


def _strength_rows(inputs: LiveInputs, site_dates: pd.DataFrame) -> pd.DataFrame:
    from .features import MEASURES, RAW_STRENGTH_KEYS

    try:
        out = inputs.strength(site_dates)
    except LeakError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        inputs.notes.append(f"Strength lists could not be read ({type(error).__name__}: {error}). Strength features are "
                            "missing (NaN), never zero.")
        out = site_dates[["site", "as_of"]].copy()
        for col in (*RAW_STRENGTH_KEYS, *MEASURES):
            out[col] = np.nan
    return out


def search_meta(scoreboard: Scoreboard) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (site, search key): difficulty source and volume from the frozen seed manifests on the scoreboard."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in scoreboard.freezes():
        if not str(row["name"]).startswith("seed_manifest."):
            continue
        manifest = (row["payload"] or {}).get("manifest") or {}
        metric = manifest.get("difficulty_metric")
        for site in manifest.get("sites") or []:
            name = site_key(str(site.get("domain")))
            for item in site.get("searches") or []:
                out[(name, search_key(str(item.get("search"))))] = {
                    "difficulty_value": item.get("difficulty_value"), "volume": item.get("volume"),
                    "difficulty_source": item.get("difficulty_source") or metric}
    return out


def form_for(inputs: LiveInputs, site: str, day: date) -> tuple[dict[str, Any] | None, date | None, str | None]:
    """The newest frozen agency form of the site filled in on or before `day`: (answers, filled_on, freeze name)."""
    from .agency_form import load_form

    best: tuple[str, str, Path] | None = None
    for row in inputs.scoreboard.freezes():
        payload = row["payload"] or {}
        if payload.get("kind") != "agency_form" or payload.get("site") != site_key(site):
            continue
        filled = str(payload.get("filled_on") or "")
        if not filled or date.fromisoformat(filled) > day or parse_utc(row["frozen_at"]).date() > day:
            continue
        key = (filled, str(row["name"]), inputs.paths.root / str(payload["stored_path"]))
        if best is None or key[:2] > best[:2]:
            best = key
    if best is None:
        return None, None, None
    stored = best[2]
    form = load_form(stored)
    return dict(form.to_payload()["answers"]), form.filled_on, best[1]


def gsc_columns(inputs: LiveInputs, site: str, search: str, launch: date, day: date) -> tuple[dict[str, float], str]:
    """updater.GSC_FEATURES for one cell from the frozen Search Console exports (rows fetched after `day` ignored)."""
    from . import gsc
    from .pipeline import _file_digest

    empty = {name: math.nan for name in GSC_FEATURES}
    rows: list[Any] = []
    used = 0
    changed = 0
    for row in inputs.scoreboard.freezes():
        payload = row["payload"] or {}
        if not str(row["name"]).startswith(f"gsc.{site_key(site)}.") or payload.get("kind") not in ("ui", "daily"):
            continue
        fetched = parse_utc(row["frozen_at"])
        if fetched.date() > day:
            continue
        path = inputs.paths.root / str(payload["stored_path"])
        if not path.exists():
            continue
        # A file, zip or folder export is read only when its stored copy still has the digest frozen at import
        # (pipeline._file_digest, the same hash import_gsc froze): a copy changed since could hold later rows.
        if _file_digest(path) != payload.get("sha256"):
            changed += 1
            continue
        if payload["kind"] == "ui":
            rows.extend(gsc.parse_ui_export(path, fetched_at=fetched).all_rows())
        else:
            rows.extend(gsc.parse_daily_table(path, fetched_at=fetched).rows)
        used += 1
    skipped = (f"; {changed} export{'s' if changed != 1 else ''} skipped: stored copy changed since its freeze"
               if changed else "")
    if not rows:
        return empty, f"no Search Console export frozen on or before the date{skipped}"
    signals = gsc.early_signals(rows, launch_date=launch, as_of=day, target_searches=[search])

    def num(value: Any) -> float:
        return float(value) if value is not None and math.isfinite(float(value)) else math.nan

    return {
        "sc_impressions_total": num(signals.get("sc_impressions_total")),
        "sc_clicks_total": num(signals.get("sc_clicks_total")),
        "sc_query_present_target": num(signals.get(gsc.signal_key("sc_query_present", search))),
        "sc_avg_position_target": num(signals.get(gsc.signal_key("sc_avg_position", search))),
        "sc_impressions_slope": num(signals.get("sc_impressions_weekly_slope")),
    }, f"{used} Search Console exports{skipped}"


def content_match(inputs: LiveInputs, site: str, search: str, day: date) -> dict[str, Any]:
    """The content match score of the site's own page against the search, when page text is on file.

    Page text lives under <data>/imports/pages/<site>/ as .html files. A file changed after `day` is
    not used. The score is recorded with the backend that ran. NaN when no page text is on file.
    """
    folder = inputs.paths.imports / "pages" / site_key(site)
    end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc).timestamp()
    files = sorted((p for p in folder.glob("*.html") if p.stat().st_mtime <= end), key=lambda p: p.stat().st_mtime) \
        if folder.is_dir() else []
    if not files:
        return {"value": math.nan, "backend": None, "note": "no page text on file"}
    from .content import ContentScorer, page_text_from_html

    scorer = inputs.content_scorer or ContentScorer("auto")
    inputs.content_scorer = scorer
    page = page_text_from_html(files[-1].read_text(encoding="utf-8", errors="replace"), "")
    score = scorer.score(search, page)
    value = math.nan if score.value is None else float(score.value)
    return {"value": value, "backend": score.mode, "model": score.model, "file": files[-1].name,
            "sha256": _file_sha(files[-1]), "note": "recorded, not a model input (no training set has a page-text score)"}


def feature_row(inputs: LiveInputs, *, site: str, search: str, day: date, page: CleanReading,
                trust: Any | None, meta: Mapping[str, Any] | None, difficulty_metric: str | None,
                form_answers: Mapping[str, Any] | None) -> dict[str, Any]:
    """The features.assemble_frame family for one (site, search) as of `day`, from one stored page."""
    from .backtest import form_columns
    from .features import MEASURES, cell_features, page_features, start_features

    page_rows = _page_frame(page)
    page_day = parse_utc(page.taken_at).date()
    if page_day > day:
        raise LeakError(f"the page of {search!r} was taken on {page_day}, after the forecast date {day}")
    wanted = pd.DataFrame({"site": [*page_rows["site"], site], "as_of": [page_day] * len(page_rows) + [day]})
    st = _strength_rows(inputs, wanted.drop_duplicates().reset_index(drop=True))
    st_page = st[[as_date(d) == page_day for d in st["as_of"]]].drop_duplicates("site").set_index("site")
    st_own = st[(st["site"] == site) & np.array([as_date(d) == day for d in st["as_of"]])]
    for m in MEASURES:
        page_rows[m] = page_rows["site"].map(st_page[m] if m in st_page else {}).astype(float)
    own = st_own.iloc[0].to_dict() if len(st_own) else {}
    metric_ok = bool(meta) and difficulty_metric is not None and meta.get("difficulty_source") == difficulty_metric
    # Difficulty and volume feed the model only when the manifest names the metric the bundle was trained on.
    page_meta = {"kd": float(meta["difficulty_value"]) if metric_ok and meta.get("difficulty_value") is not None else None,
                 "search_volume": float(meta["volume"]) if metric_ok and meta.get("volume") is not None else None}
    row: dict[str, Any] = {}
    if len(page_rows):
        row.update({k: v for k, v in page_features(page_rows, meta=page_meta).items() if str(k).startswith("pg_")})
        row.update(cell_features(site, search, day, page_rows, site_values={m: own.get(m, math.nan) for m in MODEL_MEASURES}))
    for key, value in own.items():
        if key not in ("site", "as_of"):
            row[f"st_{key}"] = value
    if inputs.evidence is not None:
        row.update(start_features(site, day, inputs.evidence.by_site.get(site, [])))
    else:
        row.update({c: math.nan for c in START_COLUMNS})
    row.update(form_columns(form_answers))
    row["site"] = site
    if trust is not None:
        row[TRUST_FEATURE] = float(trust.predict(pd.DataFrame([row]))[0])
    else:
        row[TRUST_FEATURE] = math.nan
    row.pop("site", None)
    return {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) and not isinstance(v, bool) else v)
            for k, v in row.items()}


def stage_readings(inputs: LiveInputs, site: str, search: str, location_code: int, device: str,
                   day: date) -> tuple[list[Reading], list[str]]:
    """The site's own position in the latest clean reading on or before `day` and the one before it."""
    found = []
    for reading in inputs.scoreboard.readings_for(search, location_code, device, on_or_before=day, label=inputs.label,
                                                  non_degraded_only=True):
        if parse_utc(reading.taken_at) > parse_utc(utc_iso(inputs.now)):
            continue
        found.append(reading)
        if len(found) >= STAGE_READINGS_USED:
            break
    found.reverse()
    out = [Reading(position=int(r.position_of(site)), taken_on=parse_utc(r.taken_at).date(),
                   label=SYNTHETIC if r.label == SYNTHETIC else REAL) for r in found]
    return out, [r.reading_id for r in found]


# ---------------------------------------------------------------------------
# a forecast run (upload or one stage) for one site


@dataclass
class ForecastRun:
    """The forecasts of one site at one stage as of one date, and everything that stopped a cell. PREDICTED."""

    site: str
    source: str
    stage: str
    as_of: date
    label: str
    bundle: str
    bundle_version: str
    gate: dict[str, Any]
    forecasts: list[Forecast] = field(default_factory=list)
    inputs: dict[str, list[str]] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    already: list[dict[str, str]] = field(default_factory=list)
    blocked: str | None = None
    missed: str | None = None
    recorded: list[str] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stage_day: date | None = None

    @property
    def all_nt50(self) -> bool:
        return bool(self.forecasts) and all(f.point >= NT50 for f in self.forecasts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site, "source": self.source, "stage": self.stage, "as_of": self.as_of.isoformat(),
            "stage_day": self.stage_day.isoformat() if self.stage_day else None, "label": self.label,
            "bundle": self.bundle, "bundle_version": self.bundle_version, "gate": self.gate,
            "forecasts": [_json_safe({**f.to_payload(), "label_text": PREDICTED}) for f in self.forecasts],
            "inputs": self.inputs, "skipped": self.skipped, "already_recorded": self.already, "blocked": self.blocked,
            "missed": self.missed, "recorded": self.recorded, "refused": self.refused, "notes": self.notes,
        }


def _site_row(scoreboard: Scoreboard, site: str) -> tuple[str, str, date]:
    name = site_key(site)
    with Session(scoreboard.engine) as session:
        row = session.get(SiteRow, name)
        if row is None:
            raise ForecastingRefusal(f"{name} is not on the scoreboard. Register it first: python -m aeo.ranksim forecast "
                                     "seeds register --manifest <frozen manifest>")
        return row.site, row.source, date.fromisoformat(row.launch_date)


def _searches(scoreboard: Scoreboard, site: str) -> list[tuple[str, str, int, str, str | None]]:
    with Session(scoreboard.engine) as session:
        rows = session.scalars(select(SearchRow).where(SearchRow.site == site).order_by(SearchRow.search_key)).all()
        return [(r.search, r.search_key, int(r.location_code), r.device, r.target_url) for r in rows]


def _already(scoreboard: Scoreboard, site: str, stage: str) -> dict[str, str]:
    with Session(scoreboard.engine) as session:
        rows = session.execute(select(ForecastRow.search_key, ForecastRow.model_version)
                               .where(ForecastRow.site == site, ForecastRow.stage == stage)).all()
    return {key: version for key, version in rows}


def live_models(bundle: ModelBundle, gate: GateSpec, appeared: Mapping[str, bool] | None = None):
    """The models a live forecast uses. A conditional bundle's placement model AND updater are ALWAYS gated.

    `appeared` maps each search to whether a clean reading on or before the date has the site inside
    the top 50. The updater of a conditional bundle caps P(top 50) at the gate on every search not
    marked True (GatedUpdater).
    """
    from .backtest import ForecastModels

    if bundle.conditional and not gate.applied:
        raise ForecastingRefusal(f"bundle {bundle.name} is conditional on the site appearing, so it needs the appearance "
                                 "gate. A forecast without it is refused.")
    placement = GatedPlacement(bundle.placement, gate if bundle.conditional else NO_GATE, bundle.version)
    strength_only = GatedPlacement(bundle.strength_only, gate if bundle.conditional else NO_GATE, bundle.version)
    updater: Any = bundle.updater
    if bundle.conditional and updater is not None:
        updater = GatedUpdater(updater, gate, dict(appeared or {}))
    return ForecastModels(placement=placement, updater=updater), strength_only


def site_appeared(inputs: "LiveInputs", site: str, search: str, location_code: int, device: str,
                  day: date) -> str | None:
    """The newest clean, non-degraded reading of the search on or before `day` with the site inside the top 50
    (its reading id), or None when no such reading exists: the site has not appeared on the search."""
    moment = parse_utc(utc_iso(inputs.now))
    for reading in inputs.scoreboard.readings_for(search, location_code, device, on_or_before=day, label=inputs.label,
                                                  non_degraded_only=True):
        if parse_utc(reading.taken_at) > moment:
            continue
        if int(reading.position_of(site)) <= TOP_DEPTH:
            return reading.reading_id
    return None


def plan_forecasts(
    scoreboard: Scoreboard,
    paths: ForecastPaths,
    bundle: ModelBundle,
    *,
    site: str,
    stage: str,
    day: date | None,
    now: datetime,
    strength: StrengthFn | None = None,
    evidence: Any | None = None,
    content_scorer: Any | None = None,
) -> ForecastRun:
    """Build (never record) the forecasts of one registered site at one stage as of `day` (default: the stage day).

    Refused before anything is built: an unregistered site, a site that is not seed, pilot or synthetic,
    a SYNTHETIC bundle for a seed or pilot site, and a bundle whose training outcomes end after the date.
    Reported, not refused: a stage whose day has passed is MISSED (never backfilled); a date the
    scoreboard would refuse (an upload after launch, a stage forecast after its day, a seed or pilot
    forecast more than one day old, a date in the future) blocks the run with that sentence; a search
    with no clean reading on or before the date is skipped by name; a cell that already has a forecast
    at this stage is listed and left alone.
    """
    from .backtest import forecast_cells
    from .config import FORECAST_FROZEN_GAP_DAYS

    if stage not in STAGE_DAYS:
        raise ForecastingRefusal(f"the stage must be one of {', '.join(STAGE_DAYS)}")
    name, source, launch = _site_row(scoreboard, site)
    if source not in LIVE_SOURCES:
        raise ForecastingRefusal(f"{name} is a {source} site. Live forecasts are for seed, pilot and synthetic sites.")
    if bundle.training_label == SYNTHETIC and source in ("seed", "pilot"):
        raise ForecastingRefusal(f"Bundle {bundle.name} was trained on SYNTHETIC rows. A synthetic model never forecasts "
                                 f"a seed or pilot site like {name}. Train on REAL data: forecast train --data panel.")
    if stage != "upload" and bundle.updater is None:
        raise ForecastingRefusal(f"Bundle {bundle.name} has no early-reading updater, so a {stage} forecast would be the "
                                 "upload model's answer with the site's readings ignored. Train a bundle with an updater "
                                 "(forecast train) and pass it with --bundle.")
    label = SYNTHETIC if source == "synthetic" else REAL
    stage_day = launch + timedelta(days=STAGE_DAYS[stage])
    today = now.astimezone(timezone.utc).date()
    as_of = as_date(day) if day is not None else min(stage_day, today)
    gate = appearance_gate(scoreboard, label=label, as_of=as_of) if bundle.conditional else NO_GATE
    run = ForecastRun(name, source, stage, as_of, label, bundle.name, bundle.version, gate.to_dict(), stage_day=stage_day)
    if bundle.training_cutoff > as_of:
        raise LeakError(f"bundle {bundle.name} learned outcomes up to {bundle.training_cutoff}, after the forecast date "
                        f"{as_of}. A forecast may only use what was knowable on its date.")
    if (today - stage_day).days > FORECAST_FROZEN_GAP_DAYS:
        run.missed = (f"MISSED: the {stage} stage day of {name} was {stage_day}, and a forecast dated then may be written "
                      f"at most {FORECAST_FROZEN_GAP_DAYS} day later. A missed stage is never backfilled. A forecast made "
                      "now would be a shorter lead, never this stage.")
        return run
    if as_of > today:
        run.blocked = f"The forecast date {as_of} is later than today, {today}."
    elif stage == "upload" and as_of > launch:
        run.blocked = (f"An upload forecast must be made on or before launch ({launch}). The date is {as_of}. "
                       "The scoreboard refuses it.")
    elif stage != "upload" and as_of > stage_day:
        run.blocked = (f"A {stage} forecast must be dated on or before day {STAGE_DAYS[stage]} ({stage_day}). "
                       f"The date is {as_of}. The scoreboard refuses it.")
    elif source in ("seed", "pilot") and (today - as_of).days > FORECAST_FROZEN_GAP_DAYS:
        run.blocked = (f"A {source} forecast dated {as_of} written on {today} is more than {FORECAST_FROZEN_GAP_DAYS} day "
                       "late. Later readings could have shaped it. The scoreboard refuses it.")
    if run.blocked:
        return run
    if source == "synthetic" and strength is None:
        raise ForecastingRefusal("Synthetic sites are forecast only inside forecast demo, which holds their world.")
    inputs = LiveInputs(scoreboard, paths, strength or real_strength_fn(paths), label, now, evidence=evidence,
                        content_scorer=content_scorer)
    if evidence is None and label == REAL:
        try:
            from .features import StartEvidence

            inputs.evidence = StartEvidence.load([name], paths.root)
        except (OSError, ValueError, KeyError) as error:
            run.notes.append(f"Start-date evidence could not be read ({type(error).__name__}). Start features are missing.")
    meta = search_meta(scoreboard)
    form, filled_on, form_name = form_for(inputs, name, as_of)
    already = _already(scoreboard, name, stage)
    models, strength_only = live_models(bundle, gate)
    appeared: dict[str, bool] = {}
    rows: dict[str, dict[str, Any]] = {}
    dates: dict[str, dict[str, date | None]] = {}
    readings: dict[str, list[Reading]] = {}
    extras: dict[str, dict[str, Any]] = {}
    for search, key, location, device, target_url in _searches(scoreboard, name):
        if key in already:
            run.already.append({"search": search, "model_version": already[key]})
            continue
        page = latest_page(inputs, search, location, device, as_of)
        if page is None:
            run.skipped.append({"search": search, "reason": f"no stored clean reading on or before {as_of} that is not "
                                                            "degraded. The cell is skipped, not called NOT IN TOP 50."})
            continue
        values = feature_row(inputs, site=name, search=search, day=as_of, page=page, trust=bundle.trust,
                             meta=meta.get((name, key)), difficulty_metric=bundle.manifest.get("difficulty_metric"),
                             form_answers=form)
        ids = [page.reading_id]
        got: list[Reading] = []
        if stage != "upload":
            got, stage_ids = stage_readings(inputs, name, search, location, device, as_of)
            ids.extend(i for i in stage_ids if i not in ids)
            seen_in = site_appeared(inputs, name, search, location, device, as_of)
            appeared[search] = seen_in is not None
            if seen_in is not None and seen_in not in ids:
                ids.append(seen_in)  # the reading that lifted the gate cap is an input of the forecast
            sc, sc_note = gsc_columns(inputs, name, search, launch, as_of)
            values.update(sc)
            extras.setdefault(search, {})["search_console"] = sc_note
        extras.setdefault(search, {})["content_match"] = content_match(inputs, name, search, as_of)
        extras[search]["target_url"] = target_url
        rows[search] = values
        dates[search] = {"input_date": parse_utc(page.taken_at).date(), "strength_date": as_of,
                         "form_date": filled_on}
        readings[search] = got
        run.inputs[search] = sorted(ids)
    run.notes.extend(inputs.notes)
    if form_name is None:
        run.notes.append("No agency form filled in on or before the date is frozen. Footprint features are missing.")
    if not rows:
        return run

    def provider(site_: str, search_: str, day_: date) -> tuple[dict[str, Any], dict[str, date | None]]:
        if site_ != name or day_ != as_of:
            raise ForecastingRefusal("the live provider serves only the site and date it was built for")
        return dict(rows[search_]), dict(dates[search_])

    models.features = provider
    if isinstance(models.updater, GatedUpdater):
        models.updater.appeared = appeared
    searches = list(rows)
    made = forecast_cells(name, searches, as_of, stage, readings, None, models,
                          outcome_date=launch + timedelta(days=OUTCOME_DAY))
    frame = pd.DataFrame([rows[s] for s in searches])
    strength_points = [dist_median(d) for d in strength_only.distributions(frame)]
    features_used = list(bundle.placement.features)
    for forecast, base_point, search in zip(made, strength_points, searches):
        missing = sorted(f for f in features_used if not _finite(rows[search].get(f)))
        version = forecast.model_version
        if stage != "upload":
            version = f"{bundle.version}|{forecast.model_version}|prior {models.placement.model_version()}"
        notes = {**forecast.notes, "bundle": bundle.name, "bundle_sha256": bundle.sha256, "bundle_version": bundle.version,
                 "training_label": bundle.training_label, "gate": gate.to_dict(), "features_missing": missing,
                 "features_used": len(features_used),
                 "baselines": {"site_strength": int(base_point)}, "forecast_label": label, **extras.get(search, {})}
        target = extras.get(search, {}).get("target_url")
        run.forecasts.append(replace(forecast, model_version=version, notes=_json_safe(notes), target_url=target))
    return run


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def record_run(scoreboard: Scoreboard, run: ForecastRun, bundle: ModelBundle) -> ForecastRun:
    """Freeze the run's forecasts on the scoreboard. Each refusal is kept as a plain message. Nothing is worked around."""
    if run.blocked or run.missed:
        return run
    for forecast in run.forecasts:
        # The description depends on the version string's parts only, never on counts that grow over time:
        # the scoreboard refuses a known version frozen again with another description.
        description = (f"{FORECASTING_VERSION} bundle {bundle.name} (sha256 {bundle.sha256}), trained on "
                       f"{bundle.training_label} {bundle.manifest.get('training_source')} data. Appearance gate "
                       f"{run.gate['tag']}.")
        try:
            scoreboard.register_model_version(forecast.model_version, description=description, artefact_sha256=bundle.sha256)
            record = scoreboard.record_forecast(forecast, input_reading_ids=run.inputs.get(forecast.search, ()))
        except ScoreboardRefusal as error:
            run.refused.append({"search": forecast.search, "refusal": str(error)})
            continue
        run.recorded.append(record.forecast_id)
    return run


def upload_or_update(
    scoreboard: Scoreboard,
    paths: ForecastPaths,
    *,
    bundle: ModelBundle,
    site: str,
    stage: str,
    day: date | None,
    now: datetime,
    record: bool,
    strength: StrengthFn | None = None,
    evidence: Any | None = None,
) -> ForecastRun:
    """Build the forecasts of one site and, with `record`, freeze them on the scoreboard."""
    run = plan_forecasts(scoreboard, paths, bundle, site=site, stage=stage, day=day, now=now, strength=strength,
                         evidence=evidence)
    return record_run(scoreboard, run, bundle) if record else run


def run_lines(run: ForecastRun, *, record: bool) -> list[str]:
    """A run in short plain sentences. Every number says PREDICTED or names its label."""
    label = run.label
    lines = [f"{run.stage} forecasts for {run.site} ({run.source} site, {label}) as of {run.as_of}, bundle {run.bundle_version}."]
    if run.missed:
        return lines + [run.missed]
    if run.blocked:
        return lines + [f"Nothing was built: {run.blocked}"]
    lines.append(GateSpec(**{k: run.gate[k] for k in ("source", "value", "cells", "sites")}).sentence())
    for f in run.forecasts:
        point = "NOT IN TOP 50" if f.point >= NT50 else str(f.point)
        low = "NT50" if f.range_low >= NT50 else str(f.range_low)
        high = "NT50" if f.range_high >= NT50 else str(f.range_high)
        lines.append(f"  {f.search}: point {point}, range {low} to {high}, {f.label} (PREDICTED, model {f.model_version}).")
        missing = f.notes.get("features_missing") or []
        if missing:
            lines.append(f"    {len(missing)} of {f.notes.get('features_used', '?')} model features are missing (NaN, never "
                         "zero) for this search. The list is in the forecast's notes.")
    if run.forecasts and run.all_nt50 and run.gate.get("source") == "prior":
        lines.append("Every point is NOT IN TOP 50 because the gate is a low prior: that is the honest answer until the paid "
                     "training data exists.")
    for item in run.skipped:
        lines.append(f"Skipped {item['search']}: {item['reason']}")
    for item in run.already:
        lines.append(f"Already recorded at {run.stage}: {item['search']} (model {item['model_version']}). A forecast is never "
                     "replaced.")
    lines.extend(run.notes)
    if record:
        lines.append(f"Recorded {len(run.recorded)} forecasts on the scoreboard. Refused {len(run.refused)}.")
        for item in run.refused:
            lines.append(f"Refused by the scoreboard, {item['search']}: {item['refusal']}")
    else:
        lines.append(f"Dry run. {len(run.forecasts)} forecasts would be recorded. Nothing was recorded. Add --record to "
                     "freeze them on the scoreboard.")
    return lines


# ---------------------------------------------------------------------------
# what is due


def due(scoreboard: Scoreboard, today: date, *, window_days: int = 3) -> dict[str, Any]:
    """Seed and pilot stages due from today to today + window_days, and the stages missed (pipeline.stage_calendar)."""
    from .pipeline import stage_calendar

    calendar = stage_calendar(scoreboard, today, window_days=window_days)
    return {"today": today.isoformat(), "window_days": window_days, **calendar}


def due_lines(result: Mapping[str, Any]) -> list[str]:
    lines = [f"As of {result['today']}, stage forecasts due in the next {result['window_days']} days:"]
    if not result["due"]:
        lines.append("  none.")
    today = date.fromisoformat(str(result["today"]))
    for item in result["due"]:
        command = "upload" if item["stage"] == "upload" else f"update --stage {item['stage']}"
        if item["stage"] != "upload" and date.fromisoformat(item["stage_date"]) > today:
            # A stage forecast is made on its stage day, from that day's reading. One recorded earlier is never replaced.
            lines.append(f"  {item['site']} ({item['source']}) {item['stage']} on {item['stage_date']}. Run it on "
                         f"{item['stage_date']}, after that day's clean reading: python -m aeo.ranksim forecast {command} "
                         f"--site {item['site']} --record")
            continue
        lines.append(f"  {item['site']} ({item['source']}) {item['stage']} on {item['stage_date']}. "
                     f"Run: python -m aeo.ranksim forecast {command} --site {item['site']} --record")
    if result["missed"]:
        lines.append("Stages MISSED (no forecast, and the last day to record one dated the stage day has passed). "
                     "A missed stage is never backfilled:")
        lines.extend(f"  {item['site']} ({item['source']}) {item['stage']} was due on {item['stage_date']}."
                     for item in result["missed"])
    else:
        lines.append("No stage was missed.")
    return lines


# ---------------------------------------------------------------------------
# backtest evidence files (read only) and the rerun


BACKTEST_STAND_IN = "panel"  # designs whose name starts with this are stand-ins: sites already seen on a search


def _versioned(folder: Path, stem: str) -> list[tuple[int, Path]]:
    out = []
    for path in folder.glob(f"{stem}*.json"):
        tail = path.stem[len(stem):]
        if tail == "":
            out.append((1, path))
        elif tail.startswith("-v") and tail[2:].isdigit():
            out.append((int(tail[2:]), path))
    return sorted(out)


def _headline(report: Mapping[str, Any]) -> str:
    if "windows" in report:
        parts = []
        for window, block in report["windows"].items():
            head = block["all"]["headline"]
            parts.append(f"{window}: hit {_pct(head['summary']['hit']['rate'])} on {head['forecasts']} cells, best baseline "
                         f"{head.get('best_baseline')}")
        return "; ".join(parts)
    if "ceiling" in report:
        return f"single pull persistence hit {_pct(report['ceiling']['all_searches']['hit']['rate'])}"
    if "one_world" in report:
        d60 = report["one_world"]["summary"].get("d60") or {}
        return f"d60 hit {_pct(d60.get('hit'))} on {d60.get('cells')} cells, always NT50 {_pct(d60.get('always_nt50'))}"
    summary = report.get("summary") or {}
    if "hit" in summary:
        return f"hit {_pct(summary['hit'])} on {summary.get('cells')} cells, best baseline {summary.get('best_baseline')}"
    if "with_move" in summary:
        return (f"with the move {_pct(summary['with_move'])}, latest reading alone {_pct(summary['latest_alone'])} on "
                f"{summary.get('cells')} cells")
    return "no headline in this file"


def _pct(value: Any) -> str:
    return "no value" if value is None else f"{100 * float(value):.1f}%"


def list_backtests(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Every frozen backtest design under evals/forecast/backtest with its scored files. Scores nothing."""
    from .features import evals_dir

    base = Path(root) if root else evals_dir("backtest")
    out = []
    if not base.is_dir():
        return out
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        designs = _versioned(folder, "prereg")
        runs = []
        for version, path in _versioned(folder, "report"):
            report = json.loads(path.read_text(encoding="utf-8"))
            runs.append({"file": path.name, "run_version": version, "label": report.get("label"),
                         "headline": _headline(report)})
        out.append({"name": folder.name, "design_versions": [v for v, _ in designs], "runs": runs,
                    "stand_in": folder.name.startswith(BACKTEST_STAND_IN)})
    return out


def backtest_lines(items: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = ["Frozen backtest designs and their evidence files. Nothing was scored."]
    if not items:
        lines.append("No backtest design is frozen yet.")
    for item in items:
        tag = " STAND-IN: sites already seen on a search, not new launches." if item["stand_in"] else ""
        lines.append(f"{item['name']}: design versions {', '.join(map(str, item['design_versions'])) or 'none'}.{tag}")
        for run in item["runs"]:
            lines.append(f"  {run['file']} ({run['label']}): {run['headline']}.")
    return lines


RERUN_WARNING = ("This is another look at the same held-out rows. It will be reported as a second look, beside the "
                 "first, never in place of it.")


def backtest_rerun_version(root: str | Path | None = None) -> tuple[int, bool]:
    """(design version to look at next, whether its prereg is already frozen).

    A design version counts as scored when any panel design folder holds a manifest naming it
    (manifest-v<k>.json with design_version, version 1 when the field is absent). The newest frozen
    version that is not scored yet is reused, so a frozen and pushed design is scored by the second
    call. Otherwise the next version is one past the newest frozen one.
    """
    from .features import evals_dir

    base = Path(root) if root else evals_dir("backtest")
    frozen: set[int] = set()
    scored: set[int] = set()
    for item in list_backtests(root):
        if not item["stand_in"]:
            continue
        frozen.update(int(v) for v in item["design_versions"])
        for _version, path in _versioned(base / item["name"], "manifest"):
            data = json.loads(path.read_text(encoding="utf-8"))
            scored.add(int(data.get("design_version") or 1))
    latest = max(frozen, default=1)
    if latest >= 2 and latest not in scored:
        return latest, True
    return latest + 1, False


__all__ = [
    "FORECASTING_VERSION", "GATE_MIN_CELLS", "GATE_MIN_SITES", "GATE_PRIOR", "GateSpec", "GatedPlacement", "GatedUpdater",
    "ForecastRun", "ForecastingRefusal", "ModelBundle", "PLACEMENT_FEATURES", "TrainResult", "appearance_gate",
    "apply_gate", "cap_at_gate", "due", "list_backtests", "live_models", "load_bundle", "marker2_from_trust_report",
    "plan_forecasts", "record_run", "save_bundle", "train", "upload_or_update",
]
