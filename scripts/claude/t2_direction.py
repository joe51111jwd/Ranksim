"""Target 2 frozen direction predictor. Output is PREDICTED, never observed.

predict_direction(features_row) -> 'up' | 'down'

features_row is one row of the t2_dataset schema (a dict or a pandas Series).
It reads only:
  pos_a              consensus position on the SERP day matching fetch A (11 = absent)
  has_prev           the URL has a tracked SERP day for this query before A
  hist_mean_before   mean consensus position over the tracked days before A (11 = absent)
  material           t2_changes material flag for the A -> B page pair
  q_title_delta, q_h1_delta   change in query-term counts in the title / H1 (B minus A)
It never reads pos_b, move, delta or anything from day B's SERP.

Logic (fixed before any held-out data; parameters in evals/claude/t2-edits/params.json):
  1. pos_a >= 11 -> 'up' (a URL outside the top 10 can only move up or stay).
  2. pos_a <= 1 -> 'down' (a #1 can only move down or stay).
  3. Content step, only when the page changed materially: s = w_title * q_title_delta
     + w_h1 * q_h1_delta. s > 0 -> 'up', s < 0 -> 'down'. The sign rule is an a-priori
     SEO prior (more query terms in title/H1 helps). It was NOT fit: there are 17
     changed-and-moved design rows, too few to fit anything.
  4. Mean reversion: r = pos_a - hist_mean_before. r > eps -> 'up', r < -eps -> 'down'.
  5. Otherwise the default class ('down', the design majority among moved rows).

predict_direction_position_only() is the same rule without step 3 (the no-content
baseline that the content step must beat).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARAMS = ROOT / "evals/claude/t2-edits/params.json"

DEFAULT_PARAMS = {
    "version": "t2-direction-v1",
    "label": "PREDICTED",
    "absent": 11,
    "reversion_eps": 0.5,
    "default": "down",
    "content_weights": {"q_title_delta": 1.0, "q_h1_delta": 1.0},
    "content_only_if_material": True,
}


@lru_cache(maxsize=4)
def load_params(path: str = str(PARAMS)) -> dict:
    file = Path(path)
    if not file.is_file():
        return dict(DEFAULT_PARAMS)
    return json.loads(file.read_text(encoding="utf-8"))


def _get(row, name: str, default=None):
    try:
        value = row[name]
    except (KeyError, IndexError, TypeError):
        return default
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    return value


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _position_step(row, params: dict) -> str | None:
    pos_a = int(_get(row, "pos_a", params["absent"]))
    if pos_a >= int(params["absent"]):
        return "up"
    if pos_a <= 1:
        return "down"
    return None


def _reversion_step(row, params: dict) -> str | None:
    if not _truthy(_get(row, "has_prev", False)):
        return None
    mean_before = _get(row, "hist_mean_before")
    if mean_before is None:
        return None
    gap = float(_get(row, "pos_a")) - float(mean_before)
    eps = float(params["reversion_eps"])
    if gap > eps:
        return "up"
    if gap < -eps:
        return "down"
    return None


def content_score(row, params: dict | None = None) -> float:
    params = params or load_params()
    return sum(float(weight) * float(_get(row, name, 0.0) or 0.0) for name, weight in params["content_weights"].items())


def _content_step(row, params: dict) -> str | None:
    if params.get("content_only_if_material", True) and not _truthy(_get(row, "material", False)):
        return None
    score = content_score(row, params)
    if score > 0:
        return "up"
    if score < 0:
        return "down"
    return None


def predict_direction(features_row, params: dict | None = None) -> str:
    """PREDICTED 'up' or 'down' for one (query, URL, A -> B) row."""
    params = params or load_params()
    for step in (_position_step, _content_step, _reversion_step):
        found = step(features_row, params)
        if found is not None:
            return found
    return str(params["default"])


def predict_direction_position_only(features_row, params: dict | None = None) -> str:
    """Same rule without the content step: the no-content baseline."""
    params = params or load_params()
    for step in (_position_step, _reversion_step):
        found = step(features_row, params)
        if found is not None:
            return found
    return str(params["default"])


def write_params(path: Path = PARAMS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULT_PARAMS, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_params()
    print(f"wrote {PARAMS}")
