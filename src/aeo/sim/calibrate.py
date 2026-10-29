"""Compare logged real-engine observations with sandbox citation shares.

Accuracy is only computed from observations the caller logged. An empty log
returns null metrics rather than a placeholder score.
"""

from __future__ import annotations

from dataclasses import dataclass

PREDICTION_THRESHOLD = 0.5

ENGINE_ALIASES = {
    "chatgpt": "chatgpt",
    "openai": "chatgpt",
    "perplexity": "perplexity",
    "google_aio": "google_ai_overviews",
    "google_ai_overview": "google_ai_overviews",
    "google_ai_overviews": "google_ai_overviews",
    "aio": "google_ai_overviews",
    "serp": "serp",
    "other": "other",
}


def normalize_engine(value: str) -> str:
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    if key not in ENGINE_ALIASES:
        known = ", ".join(sorted(set(ENGINE_ALIASES.values())))
        raise ValueError(f"Unknown engine '{value}'. Use one of: {known}")
    return ENGINE_ALIASES[key]


@dataclass(frozen=True)
class Observation:
    engine: str
    cited: bool
    predicted_citation_share: float | None


def _classification(pairs: list[tuple[float, int]], threshold: float) -> dict:
    if not pairs:
        return {
            "observations_with_prediction": 0,
            "accuracy": None,
            "precision": None,
            "recall": None,
            "brier_score": None,
        }
    correct = 0
    true_positive = 0
    false_positive = 0
    false_negative = 0
    brier = 0.0
    for predicted, actual in pairs:
        guess = predicted >= threshold
        brier += (predicted - actual) ** 2
        if guess == bool(actual):
            correct += 1
        if guess and actual:
            true_positive += 1
        elif guess and not actual:
            false_positive += 1
        elif not guess and actual:
            false_negative += 1
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else None
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else None
    return {
        "observations_with_prediction": len(pairs),
        "accuracy": correct / len(pairs),
        "precision": precision,
        "recall": recall,
        "brier_score": brier / len(pairs),
    }


def calibration_report(observations: list[Observation], *, threshold: float = PREDICTION_THRESHOLD) -> dict:
    paired = [
        (item.predicted_citation_share, 1 if item.cited else 0)
        for item in observations
        if item.predicted_citation_share is not None
    ]
    by_engine: dict[str, list[tuple[float, int]]] = {}
    for item in observations:
        if item.predicted_citation_share is None:
            continue
        by_engine.setdefault(item.engine, []).append((item.predicted_citation_share, 1 if item.cited else 0))
    return {
        "threshold": threshold,
        "threshold_meaning": (
            "A sandbox citation share at or above this threshold is scored as a predicted citation."
        ),
        "observations": len(observations),
        "observations_missing_prediction": sum(1 for item in observations if item.predicted_citation_share is None),
        "overall": _classification(paired, threshold),
        "by_engine": {engine: _classification(pairs, threshold) for engine, pairs in sorted(by_engine.items())},
        "note": (
            "These figures only describe logged observations. They are empty until you record "
            "what a real answer engine did. The simulator does not invent that log."
        ),
    }
