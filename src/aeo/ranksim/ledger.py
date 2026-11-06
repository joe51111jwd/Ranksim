"""Append-only prediction ledger, scored later against observed Google results.

Every draft check that produces a ranking is written here with what was predicted, when,
against which candidate set, by which model, on which data. Later SERP observations are
appended as outcomes. Nothing is updated in place:

- Each prediction row stores ``row_hash = sha256(prev_hash + canonical JSON of the row)``.
  ``verify_ledger`` recomputes the chain, so editing, deleting or reordering an old row is
  detectable. Anyone with write access to the database can still rebuild the whole chain;
  publishing the latest ``row_hash`` somewhere public (a commit, a dated post) is what makes
  the track record externally checkable.
- A rerun of the same check is a new row. A newer, better prediction never replaces an
  older, worse one.

Calls are pairwise ("the subject page ranks above competitor X"), because that is what the
product claims. The predicted rank is a slot in the supplied candidate set, never a Google
position. Scoring against an outcome:
- both URLs observed in the collected results: the call is right or wrong;
- subject observed, competitor not: the subject is above it only when the outcome says how deep
  results were collected (``depth``); otherwise unresolved;
- subject not observed: unresolved, because the ledger cannot tell "published and not ranking"
  from "never published at that URL";
- outcomes count only if observed on a calendar date after the latest local date the
  prediction could have been made on (UTC date plus 14 hours), so an earlier observation
  recorded with a local date cannot score a later prediction.
Unresolved calls are counted, never dropped silently. Checks of pasted HTML with no real URL
are not recorded at all: they can never be observed.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import JSON

from aeo.crawl.urls import normalize_url
from aeo.ranksim.store import Base

GENESIS = "0" * 64
PLACEHOLDER_SUBJECTS = frozenset({"https://draft.local/page"})
_APPEND_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RsPrediction(Base):
    __tablename__ = "rs_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(String(500), index=True)
    subject_url: Mapped[str] = mapped_column(String(2000))
    payload: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)  # one successor per row: no forks
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)


class RsOutcome(Base):
    __tablename__ = "rs_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    observed_on: Mapped[str] = mapped_column(String(10), index=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    positions: Mapped[dict] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text, default="")


@dataclass
class Call:
    competitor_url: str
    predicted: str  # "above" or "below": where the subject page is predicted relative to the competitor
    score_gap: float | None
    confidence: str


@dataclass
class LedgerScore:
    predictions: int
    calls: int
    resolved: int
    correct: int
    unresolved: int
    by_confidence: dict[str, dict[str, int]] = field(default_factory=dict)
    no_outcome_yet: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.resolved if self.resolved else None


def record_prediction(
    session: Session,
    *,
    kind: str,
    query: str,
    subject_url: str,
    subject_html: str | None,
    predicted_rank: int,
    predicted_of: int,
    candidates: list[dict],
    calls: list[Call],
    confidence: str,
    calibration_note: str,
    provenance: dict,
    created_at: datetime | None = None,
) -> RsPrediction:
    """Append one prediction. ``candidates`` rows: {url, html_sha256, labeled_position}."""
    stamp = (created_at or _now()).isoformat(timespec="seconds")
    payload = {
        "subject_html_sha256": _sha(subject_html) if subject_html is not None else None,
        "predicted_rank": predicted_rank,
        "predicted_of": predicted_of,
        "rank_meaning": f"slot {predicted_rank} of {predicted_of} supplied candidate pages; not a Google position",
        "candidates": candidates,
        "calls": [call.__dict__ for call in calls],
        "confidence": confidence,
        "calibration_note": calibration_note,
        "provenance": provenance,
    }
    from sqlalchemy.exc import IntegrityError

    # Reading the head and appending must not interleave, or two checks share a parent and the
    # chain forks (verify would then report tampering). A lock covers one process; the unique
    # prev_hash constraint plus a retry covers several processes on one database.
    with _APPEND_LOCK:
        for _attempt in range(5):
            previous = session.query(RsPrediction).order_by(RsPrediction.id.desc()).first()
            prev_hash = previous.row_hash if previous else GENESIS
            row = RsPrediction(
                created_at=stamp,
                kind=kind,
                query=query,
                subject_url=subject_url,
                payload=payload,
                prev_hash=prev_hash,
                row_hash=_row_hash(prev_hash, stamp, kind, query, subject_url, payload),
            )
            session.add(row)
            try:
                session.commit()
                return row
            except IntegrityError:
                session.rollback()
        raise RuntimeError("Could not append to the prediction ledger after 5 attempts.")


def record_outcome(
    session: Session,
    *,
    query: str,
    observed_on: str,
    provider: str,
    positions: dict[str, int],
    depth: int | None,
    notes: str = "",
) -> RsOutcome:
    """Append an observed result list. ``positions`` maps URL to organic position."""
    row = RsOutcome(
        observed_on=observed_on,
        query=" ".join(query.split()),
        provider=provider,
        depth=depth,
        positions={_key(url): int(position) for url, position in positions.items()},
        notes=notes,
    )
    session.add(row)
    session.commit()
    return row


def verify_ledger(session: Session) -> tuple[bool, str]:
    rows = session.query(RsPrediction).order_by(RsPrediction.id).all()
    prev = GENESIS
    for row in rows:
        if row.prev_hash != prev:
            return False, f"Prediction {row.id}: prev_hash does not match prediction before it. A row was removed or reordered."
        expected = _row_hash(row.prev_hash, row.created_at, row.kind, row.query, row.subject_url, row.payload)
        if expected != row.row_hash:
            return False, f"Prediction {row.id}: contents changed after it was recorded."
        prev = row.row_hash
    head = prev if rows else "none"
    return True, f"{len(rows)} predictions, chain intact. Latest hash {head}."


def score_ledger(session: Session) -> LedgerScore:
    """Score every call against the first outcome observed after the prediction was made."""
    predictions = session.query(RsPrediction).order_by(RsPrediction.id).all()
    outcomes = session.query(RsOutcome).order_by(RsOutcome.observed_on, RsOutcome.id).all()
    by_query: dict[str, list[RsOutcome]] = {}
    for outcome in outcomes:
        by_query.setdefault(outcome.query, []).append(outcome)
    score = LedgerScore(predictions=len(predictions), calls=0, resolved=0, correct=0, unresolved=0)
    for prediction in predictions:
        made = datetime.fromisoformat(prediction.created_at)
        latest_local_day = (made + timedelta(hours=14)).date().isoformat()
        later = [row for row in by_query.get(" ".join(prediction.query.split()), []) if row.observed_on > latest_local_day]
        calls = prediction.payload.get("calls", [])
        score.calls += len(calls)
        if not later:
            score.no_outcome_yet += len(calls)
            continue
        outcome = later[0]
        subject = outcome.positions.get(_key(prediction.subject_url))
        for call in calls:
            bucket = score.by_confidence.setdefault(call.get("confidence", "UNSURE"), {"calls": 0, "resolved": 0, "correct": 0})
            bucket["calls"] += 1
            rival = outcome.positions.get(_key(call["competitor_url"]))
            actual = _relative(subject, rival, outcome.depth)
            if actual is None:
                score.unresolved += 1
                continue
            score.resolved += 1
            bucket["resolved"] += 1
            if actual == call["predicted"]:
                score.correct += 1
                bucket["correct"] += 1
    return score


def render_score(score: LedgerScore) -> str:
    lines = [
        "Prediction track record. Calls are 'the checked page ranks above or below this competitor'.",
        f"Predictions {score.predictions}. Calls {score.calls}. Waiting for a later observation: {score.no_outcome_yet}.",
        f"Resolved {score.resolved}, unresolved {score.unresolved} (one or both pages not in the observed results).",
    ]
    if score.resolved:
        lines.append(f"Right {score.correct} of {score.resolved} resolved calls ({score.accuracy:.3f}).")
    else:
        lines.append("No resolved calls yet, so there is no track record to report.")
    for label, bucket in sorted(score.by_confidence.items()):
        rate = f"{bucket['correct'] / bucket['resolved']:.3f}" if bucket["resolved"] else "n/a"
        lines.append(f"  {label}: {bucket['calls']} calls, {bucket['resolved']} resolved, right {bucket['correct']} ({rate})")
    return "\n".join(lines) + "\n"


def _relative(subject: int | None, rival: int | None, depth: int | None) -> str | None:
    if subject is not None and rival is not None:
        return "above" if subject < rival else "below"
    if subject is not None and rival is None and depth is not None:
        return "above"
    return None  # subject not observed: published-and-not-ranking and never-published look the same


def _row_hash(prev_hash: str, created_at: str, kind: str, query: str, subject_url: str, payload: dict) -> str:
    body = json.dumps(
        {"prev": prev_hash, "created_at": created_at, "kind": kind, "query": query, "subject_url": subject_url, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def record_check(session: Session, result, *, html: str, calibration, provenance: dict) -> RsPrediction | None:
    """Append a draft check (check.CheckResult). Errors and empty checks are not predictions."""
    if result.error or result.predicted_rank is None or not result.competitors:
        return None
    if result.url in PLACEHOLDER_SUBJECTS:
        return None  # pasted HTML with no URL can never be observed in Google
    candidates = [
        {
            "url": row.url,
            "html_sha256": result.candidate_hashes.get(row.url),
            "labeled_position": row.stored_position,
        }
        for row in result.competitors
    ]
    calls = [
        Call(
            competitor_url=row.url,
            predicted="above" if row.outcome == "beats" else "below",
            score_gap=None if row.score_gap is None else round(float(row.score_gap), 6),
            confidence=getattr(row, "confidence", None)
            or (calibration.label(abs(row.score_gap)) if row.score_gap is not None else "UNSURE"),
        )
        for row in result.competitors
    ]
    return record_prediction(
        session,
        kind="draft_check",
        query=result.query,
        subject_url=result.url,
        subject_html=html,
        predicted_rank=result.predicted_rank,
        predicted_of=result.predicted_of,
        candidates=candidates,
        calls=calls,
        confidence=result.confidence,
        calibration_note=result.confidence_note,
        provenance=provenance,
    )
