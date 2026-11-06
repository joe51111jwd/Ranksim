"""File ledger of PREDICTED ranks, scored later against REAL positions.

Scoring compares order only. The later REAL file is not a feature and this
module does not train a model.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LEDGER = Path("data/track-record.csv")
PREDICTED_NOTE = "PREDICTED rank among stored candidates, not a Google position."
COLUMNS = (
    "recorded_at",
    "query",
    "url",
    "predicted_rank",
    "predicted_of",
    "confidence",
    "label",
    "source",
    "note",
)


@dataclass(frozen=True)
class Prediction:
    recorded_at: str
    query: str
    url: str
    predicted_rank: int
    predicted_of: int
    confidence: str
    source: str
    note: str
    label: str = "PREDICTED"


@dataclass
class TrackScore:
    overlapping_rows: int
    comparable_queries: int
    comparable_pairs: int
    pair_correct: int
    pairwise: float | None
    reported: bool
    note: str


def record_prediction(
    *,
    query: str,
    url: str,
    predicted_rank: int,
    predicted_of: int,
    confidence: str = "UNSURE",
    source: str = "",
    note: str = PREDICTED_NOTE,
    ledger: str | Path | None = None,
    recorded_at: str | None = None,
) -> Path:
    query = " ".join(query.split())
    url = url.strip()
    if not query:
        raise ValueError("query is required")
    if not (url.lower().startswith("http://") or url.lower().startswith("https://")):
        raise ValueError("url must already be http or https. Nothing was invented.")
    if predicted_rank <= 0 or predicted_of <= 0:
        raise ValueError("predicted rank and predicted of must be positive")
    if predicted_rank > predicted_of:
        raise ValueError("predicted rank cannot be past predicted of")
    confidence = (confidence or "UNSURE").strip() or "UNSURE"
    row = Prediction(
        recorded_at=recorded_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        query=query,
        url=url,
        predicted_rank=int(predicted_rank),
        predicted_of=int(predicted_of),
        confidence=confidence,
        source=source.strip(),
        note=note.strip() or PREDICTED_NOTE,
    )
    path = Path(ledger) if ledger else DEFAULT_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "recorded_at": row.recorded_at,
                "query": row.query,
                "url": row.url,
                "predicted_rank": row.predicted_rank,
                "predicted_of": row.predicted_of,
                "confidence": row.confidence,
                "label": "PREDICTED",
                "source": row.source,
                "note": row.note,
            }
        )
    return path


def load_predictions(path: str | Path) -> list[Prediction]:
    file_path = Path(path)
    with file_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{file_path} has no header row")
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        required = ("query", "url", "predicted_rank")
        if any(name not in fields for name in required):
            raise ValueError("predictions CSV needs query, url, and predicted_rank")
        rows: list[Prediction] = []
        for raw in reader:
            query = " ".join((raw.get(fields["query"]) or "").split())
            url = (raw.get(fields["url"]) or "").strip()
            if not query or not url:
                continue
            rank = int(float(raw.get(fields["predicted_rank"]) or "0"))
            of_key = fields.get("predicted_of")
            predicted_of = int(float(raw.get(of_key) or "0")) if of_key else 0
            confidence = (raw.get(fields["confidence"]) if "confidence" in fields else None) or "UNSURE"
            source = (raw.get(fields["source"]) if "source" in fields else None) or ""
            note = (raw.get(fields["note"]) if "note" in fields else None) or PREDICTED_NOTE
            label = (raw.get(fields["label"]) if "label" in fields else None) or "PREDICTED"
            rows.append(
                Prediction(
                    recorded_at=(raw.get(fields["recorded_at"]) if "recorded_at" in fields else None) or "",
                    query=query,
                    url=url,
                    predicted_rank=rank,
                    predicted_of=predicted_of,
                    confidence=str(confidence).strip() or "UNSURE",
                    source=str(source).strip(),
                    note=str(note).strip() or PREDICTED_NOTE,
                    label=str(label).strip() or "PREDICTED",
                )
            )
    return rows


def score_predictions(predictions: list[Prediction], observations: list[tuple[str, str, float]]) -> TrackScore:
    """Agreement of PREDICTED order with later REAL positions.

    ``observations`` are (query, url, real_position). Positions are used only
    to decide which URL was better. They are not features.
    """
    predicted: dict[str, dict[str, Prediction]] = {}
    for row in predictions:
        predicted.setdefault(row.query, {}).setdefault(row.url, row)
    observed: dict[str, dict[str, float]] = {}
    for query, url, position in observations:
        query = " ".join(query.split())
        url = url.strip()
        if not query or not url:
            continue
        observed.setdefault(query, {}).setdefault(url, float(position))

    overlapping = 0
    queries = 0
    correct = 0
    total = 0
    for query, preds in predicted.items():
        reals = observed.get(query, {})
        shared = [url for url in preds if url in reals]
        overlapping += len(shared)
        if len(shared) < 2:
            continue
        query_pairs = 0
        for index, left in enumerate(shared):
            for right in shared[index + 1 :]:
                real_left = reals[left]
                real_right = reals[right]
                pred_left = preds[left].predicted_rank
                pred_right = preds[right].predicted_rank
                if real_left == real_right or pred_left == pred_right:
                    continue
                query_pairs += 1
                total += 1
                if (real_left < real_right) == (pred_left < pred_right):
                    correct += 1
        if query_pairs:
            queries += 1

    if total == 0:
        return TrackScore(
            overlapping_rows=overlapping,
            comparable_queries=0,
            comparable_pairs=0,
            pair_correct=0,
            pairwise=None,
            reported=False,
            note=(
                "No pairwise accuracy is reported. Overlap is too small "
                f"({overlapping} shared query and url rows, 0 pairs). "
                "REAL positions were not used as model features."
            ),
        )
    return TrackScore(
        overlapping_rows=overlapping,
        comparable_queries=queries,
        comparable_pairs=total,
        pair_correct=correct,
        pairwise=correct / total,
        reported=True,
        note=(
            f"PREDICTED order agreed with later REAL positions on {correct} of {total} pairs. "
            "This is outcome evaluation on the overlap only, not a live Google rank and not a model fit. "
            "REAL positions were not used as features."
        ),
    )


def format_score(score: TrackScore) -> str:
    lines = [
        "Track record. PREDICTED ranks compared with a later REAL file.",
        f"Overlapping query and url rows: {score.overlapping_rows}.",
        f"Comparable queries: {score.comparable_queries}.",
        f"Comparable pairs: {score.comparable_pairs}.",
    ]
    if score.reported and score.pairwise is not None:
        lines.append(f"Pairwise agreement: {score.pairwise:.3f}.")
    else:
        lines.append("Pairwise agreement: not reported.")
    lines.append(score.note)
    return "\n".join(lines) + "\n"
