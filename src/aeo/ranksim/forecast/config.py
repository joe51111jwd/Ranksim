"""Frozen constants and paths shared by every forecast module.

Every threshold and seed the reports depend on is named here once, so a change
is one visible diff. Nothing in this file is measured. It is configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Grading (docs/plan/IMPLEMENTATION_SPEC.md section 2).
TOP_DEPTH = 50
NT50 = 51  # "not in the top 50": not ranked, not indexed, or beyond position 50.
OUTCOME_DAY = 90
OUTCOME_WINDOW_DAYS = 3
LEAD_FLOOR_DAYS = 28
MIN_COUNT_PRECISION_RECALL = 30

# Stages of a forecast and the day after launch each one is issued on.
STAGE_DAYS: dict[str, int] = {"upload": 0, "d7": 7, "d14": 14, "d30": 30, "d60": 60}
CONFIRMED_STAGE = "d60"

# Intervals.
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261122

# Clean reader (section 3).
DEFAULT_PULLS = 5
MIN_KEPT_PULLS = 3
MIN_PULL_GAP_SECONDS = 60
PARTIAL_MIN_ORGANIC = 30
POISON_TOP_N = 20
POISON_MIN_OVERLAP = 0.5
DEFAULT_LANGUAGE = "en"
DEFAULT_DEVICE = "desktop"

# Placement labels (section 6).
LOCKED_MIN_PROB = 0.80
OUT_OF_REACH_MIN_PROB = 0.80
RANGE_COVERAGE = 0.80

# Sites whose rows never enter training or tests (the client rule in the build plan).
EXCLUDED_SITES: frozenset[str] = frozenset({"smartcrewhq.com"})

SOURCES = ("seed", "pilot", "backtest", "synthetic")
LABELS = ("locked", "contested", "out_of_reach")


@dataclass(frozen=True)
class ForecastPaths:
    """Where forecast data lives. Everything under `root` is local and ignored by git."""

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def spend(self) -> Path:
        return self.root / "spend.jsonl"

    @property
    def scoreboard_url(self) -> str:
        return os.environ.get("RANKSIM_SCOREBOARD_URL") or f"sqlite:///{self.root / 'scoreboard.db'}"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def models(self) -> Path:
        return self.root / "models"


def forecast_paths(root: str | Path | None = None) -> ForecastPaths:
    """Paths under RANKSIM_FORECAST_DIR, or ./data/forecast when it is not set."""
    chosen = root or os.environ.get("RANKSIM_FORECAST_DIR") or "data/forecast"
    return ForecastPaths(root=Path(chosen))
