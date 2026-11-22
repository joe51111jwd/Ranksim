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
# Fewest forecasts (or units) a rate needs before it is printed as a number or
# can pass a marker. Below it the reports print NOT MEASURABLE with the count.
MIN_COUNT_TO_PRINT_RATE = 30
MIN_COUNT_PRECISION_RECALL = MIN_COUNT_TO_PRINT_RATE  # the older name, kept so every import works

# Search difficulty terciles, easiest first. Seed searches, the synthetic world
# and the scoreboard all use these three names.
DIFFICULTY_TERCILES: tuple[str, ...] = ("easy", "medium", "hard")

# Stages of a forecast and the day after launch each one is issued on.
STAGE_DAYS: dict[str, int] = {"upload": 0, "d7": 7, "d14": 14, "d30": 30, "d60": 60}
CONFIRMED_STAGE = "d60"
# A seed or pilot forecast must be written within this many days of its as_of
# date. A row written later could have been shaped by readings after as_of that
# its input list does not name. Backtest is exempt: its as_of is the launch
# month by design. OUR READING of "frozen at day 60" (plan-audit-math M5).
FORECAST_FROZEN_GAP_DAYS = 1

# Baselines every stage report prints on the same forecasts (spec section 2):
# always NT50; persistence; band prior by search difficulty tercile, fitted on
# training data only; site strength only. Markers 3 to 5 are never passed on a
# report that lacks one of them.
SPEC_BASELINES: tuple[str, ...] = ("always_nt50", "persistence", "band_prior", "site_strength")

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
# How old an earlier clean reading may be and still be passed to reader version
# 2 as history. OUR READING, not calibrated. Seed searches are read weekly, so
# 21 days still finds a history after two missed weekly readings. Altered-page
# spells mostly end within 3 days, so a reading that old is very unlikely to be
# altered the same way as today's pulls. Older readings are left out because the
# usual set of sites on a page drifts over weeks, and a stale history could make
# a clean page look altered (history overlap below one half).
HISTORY_MAX_AGE_DAYS = 21
# Pulls of one search requested less than this far apart belong to one round
# when no round record says otherwise (`forecast assemble`). Rounds are hours
# apart; one round of a few hundred searches is posted within minutes.
ROUND_GAP_MINUTES = 60

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

    @property
    def imports(self) -> Path:
        """Copies of imported files (Search Console exports, agency forms), each frozen by sha256 in the scoreboard."""
        return self.root / "imports"


def forecast_paths(root: str | Path | None = None) -> ForecastPaths:
    """Paths under RANKSIM_FORECAST_DIR, or ./data/forecast when it is not set."""
    chosen = root or os.environ.get("RANKSIM_FORECAST_DIR") or "data/forecast"
    return ForecastPaths(root=Path(chosen))
