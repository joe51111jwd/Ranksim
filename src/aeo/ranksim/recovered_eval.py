"""Gate for the recovered-content forward look. Fixed before those scores are read.

The candidate is LambdaMART on the original Nov 6 HTML, with recovered page
text used only at score time, and with no-HTML pages scored by the
unknown-content rule. It is not trained on the later day.

Promote it only when every draft slice below beats the published LambdaMART
rate by at least 0.05, is at least the BM25 rate on that same new slice, and
the query bootstrap of (new rate minus the published rate) has its lower end
above zero. The published rate is the committed 2026-11-09 figure. It is not
resampled. Text-only slices are part of the gate because a pasted draft has text.

Published LambdaMART, from evals/forward-2026-11-09/new_page.md:
- draft vs draft: 34/116
- draft vs draft, both have text: 12/22
- draft vs already ranking: 134/281
- draft vs already ranking, draft has text: 87/167

Bootstrap: 2000 draws, seed 20261110, a query's pairs stay together.
Every row is PREDICTED. Clicks and behavior data are not available.
"""

from __future__ import annotations

import random

from aeo.ranksim.new_page import BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, _bootstrap_rate, _percentile

DRAFT_ADOPTION_MARGIN = 0.05
GATE_SLICES = (
    "draft_vs_draft",
    "draft_vs_draft_with_content",
    "draft_vs_returning",
    "draft_vs_returning_with_content",
)
PUBLISHED_LAMBDAMART = {
    "draft_vs_draft": {"correct": 34, "pairs": 116},
    "draft_vs_draft_with_content": {"correct": 12, "pairs": 22},
    "draft_vs_returning": {"correct": 134, "pairs": 281},
    "draft_vs_returning_with_content": {"correct": 87, "pairs": 167},
}


def published_rate(name: str) -> float:
    row = PUBLISHED_LAMBDAMART[name]
    return row["correct"] / row["pairs"]


def adoption_gate(
    new_by_slice: dict[str, dict[str, tuple[int, int, int]]],
    bm25_by_slice: dict[str, dict[str, tuple[int, int, int]]],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Decide whether the recovered LambdaMART replaces the published one.

    ``new_by_slice[slice]`` maps a query to (correct, wrong, ties).
    """
    reasons: list[str] = []
    adopt = True
    details = {}
    for offset, name in enumerate(GATE_SLICES):
        per_query = new_by_slice.get(name) or {}
        correct = sum(item[0] for item in per_query.values())
        total = sum(sum(item) for item in per_query.values())
        point = None if total == 0 else correct / total
        bm25_rows = bm25_by_slice.get(name) or {}
        bm25_correct = sum(item[0] for item in bm25_rows.values())
        bm25_total = sum(sum(item) for item in bm25_rows.values())
        bm25 = None if bm25_total == 0 else bm25_correct / bm25_total
        reference = published_rate(name)
        rng = random.Random(seed + offset)
        interval = _bootstrap_rate(per_query, draws, rng)
        low = None if interval[0] is None else interval[0] - reference
        high = None if interval[1] is None else interval[1] - reference
        bar = reference + DRAFT_ADOPTION_MARGIN
        slice_ok = True
        notes: list[str] = []
        if point is None:
            slice_ok = False
            notes.append(f"{name} has no pairs.")
        else:
            if point + 1e-12 < bar:
                slice_ok = False
                notes.append(f"{name} {point:.3f} does not clear {bar:.3f}.")
            if bm25 is None or point + 1e-12 < bm25:
                slice_ok = False
                shown = "n/a" if bm25 is None else f"{bm25:.3f}"
                notes.append(f"{name} {point:.3f} is below BM25 {shown}.")
            if low is None or low <= 0:
                slice_ok = False
                notes.append(f"{name} interval includes no gain or a loss.")
        if not slice_ok:
            adopt = False
            reasons.extend(notes)
        details[name] = {
            "pairwise": point,
            "pairs": total,
            "correct": correct,
            "published": reference,
            "bm25": bm25,
            "delta_vs_published_ci": [low, high],
            "rate_ci": interval,
        }
    if adopt:
        reason = (
            "Every draft slice clears the published LambdaMART rate by 0.05, "
            "is at least BM25, and the bootstrap interval of the gain is above zero."
        )
    else:
        reason = " ".join(reasons)
    return {"adopt": adopt, "margin": DRAFT_ADOPTION_MARGIN, "reason": reason, "slices": details, "label": "PREDICTED"}


def rate_interval(per_query: dict[str, tuple[int, int, int]], *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> list[float | None]:
    rng = random.Random(seed)
    return _bootstrap_rate(per_query, draws, rng)


def percentile(values: list[float], fraction: float) -> float:
    return _percentile(sorted(values), fraction)


def _is_draft(row: dict) -> bool:
    return bool(row.get("entrant")) and row.get("html_source") != "baseline"


def _has_content(row: dict) -> bool:
    return row.get("html_source") != "missing"


def _in_slice(left: dict, right: dict, mode: str) -> bool:
    if mode == "all":
        return True
    left_draft = _is_draft(left)
    right_draft = _is_draft(right)
    left_new = bool(left.get("entrant"))
    right_new = bool(right.get("entrant"))
    if mode == "draft_vs_draft":
        return left_draft and right_draft
    if mode == "draft_vs_draft_with_content":
        return left_draft and right_draft and _has_content(left) and _has_content(right)
    if mode == "draft_vs_returning":
        return (left_draft != right_draft) and ((not left_new) or (not right_new))
    if mode == "draft_vs_returning_with_content":
        drafts = [row for row in (left, right) if _is_draft(row)]
        if len(drafts) != 1 or not _has_content(drafts[0]):
            return False
        return (not left_new) or (not right_new)
    raise ValueError(mode)


def slice_counts(rows: list[dict], score_key: str, mode: str) -> dict[str, tuple[int, int, int]]:
    """Per query (correct, wrong, ties). A tie is in the denominator and is not correct."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["query"], []).append(row)
    found: dict[str, tuple[int, int, int]] = {}
    for query, group in grouped.items():
        correct = wrong = ties = 0
        for left in range(len(group)):
            for right in range(len(group)):
                if group[left]["position"] >= group[right]["position"]:
                    continue
                if not _in_slice(group[left], group[right], mode):
                    continue
                better = group[left][score_key]
                worse = group[right][score_key]
                if better > worse:
                    correct += 1
                elif better < worse:
                    wrong += 1
                else:
                    ties += 1
        if correct + wrong + ties:
            found[query] = (correct, wrong, ties)
    return found
