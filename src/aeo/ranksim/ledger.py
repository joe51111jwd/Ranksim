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
  recorded with a local date cannot score a later prediction;
- a prediction is dated by the later of its stated time (``created_at``, which a caller may set
  to a protocol time) and the wall clock when it was written (``payload.recorded_at``, set here
  and covered by the hash). A row written after its stated time is "backdated": only outcomes
  observed after it was really written can resolve it. A row without a write time from this
  ledger (another writer, whatever its hash version) is "undated": counted, not scored;
- predictions and outcomes match only within one named project (``project_slug``). A blank slug
  is legacy and matches nothing. Single-project use passes ``DEFAULT_PROJECT_SLUG``.
Unresolved calls are counted, never dropped silently. Checks of pasted HTML with no real URL
are not recorded at all: they can never be observed.

Hash versions (``payload.ledger_hash_version``): 1 = the original formula; 2 = also binds the
stored ``project_slug`` (the same formula as the integration branch, PR #5 ad8020f). Version 1
rows keep verifying with their original formula and are reported as legacy.
"""

from __future__ import annotations

import hashlib
import json
import re
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
LEDGER_HASH_VERSION = 2
# The project of the single-project database used by the CLI and the local demo.
DEFAULT_PROJECT_SLUG = "default"
# A row whose stated time is this much earlier than when it was written is reported as backdated.
BACKDATE_TOLERANCE = timedelta(hours=1)
# Predictions recorded for these datasets are not scored as a track record.
UNSCORED_DATASETS = frozenset({"synthetic", "historical", "none"})
_APPEND_LOCK = threading.Lock()
# The shortest head-hash prefix `ledger verify --expect-head` accepts.
MIN_HEAD_PREFIX = 12


class LedgerError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RsPrediction(Base):
    __tablename__ = "rs_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))
    query: Mapped[str] = mapped_column(String(500), index=True)
    subject_url: Mapped[str] = mapped_column(String(2000))
    # Empty on rows written before this column existed. Never filled in after the fact.
    project_slug: Mapped[str] = mapped_column(String(200), default="", server_default="")
    payload: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)  # one successor per row: no forks
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)


class RsOutcome(Base):
    __tablename__ = "rs_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: _now())
    observed_on: Mapped[str] = mapped_column(String(10), index=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    positions: Mapped[dict] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Provenance of an imported observation file. Empty on older rows. Describes the file, not a
    # claim that it is a live Google pull.
    project_slug: Mapped[str] = mapped_column(String(200), default="", server_default="")
    source_file: Mapped[str] = mapped_column(String(500), default="", server_default="")
    source_sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")
    uploaded_at: Mapped[str] = mapped_column(String(40), default="", server_default="")


@dataclass
class Call:
    competitor_url: str
    predicted: str  # "above" or "below": where the subject page is predicted relative to the competitor
    score_gap: float | None
    confidence: str
    note: str = ""


@dataclass
class LedgerScore:
    predictions: int
    calls: int
    resolved: int
    correct: int
    unresolved: int
    by_confidence: dict[str, dict[str, int]] = field(default_factory=dict)
    no_outcome_yet: int = 0
    backdated: int = 0  # predictions written later than their stated time
    unscored: int = 0  # predictions on synthetic, historical or no data
    legacy: int = 0  # predictions with no project (written before projects existed): kept, not scored
    undated: int = 0  # rows with no write time from this ledger (another writer): stated time unverifiable, not scored

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
    project_slug: str = "",
) -> RsPrediction:
    """Append one prediction. ``candidates`` rows: {url, html_sha256, labeled_position}.

    ``created_at`` may be a protocol time; the wall clock at write time is always stored as
    ``payload.recorded_at`` and scoring uses the later of the two.
    """
    written = _now()
    stamp = (created_at or written).isoformat(timespec="seconds")
    slug = _project_slug(project_slug)
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
        "recorded_at": written.isoformat(timespec="seconds"),
        "ledger_hash_version": LEDGER_HASH_VERSION,
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
                project_slug=slug,
                payload=payload,
                prev_hash=prev_hash,
                row_hash=_row_hash(prev_hash, stamp, kind, query, subject_url, payload, slug),
            )
            session.add(row)
            try:
                session.commit()
                return row
            except IntegrityError as exc:
                session.rollback()
                if not _is_fork_race(exc):
                    raise LedgerError(f"Could not append to the prediction ledger: {exc.orig}") from exc
        raise LedgerError("Could not append to the prediction ledger: another writer won the same slot 5 times.")


def record_outcome(
    session: Session,
    *,
    query: str,
    observed_on: str,
    provider: str,
    positions: dict[str, int],
    depth: int | None,
    notes: str = "",
    project_slug: str = "",
    source_file: str = "",
    source_sha256: str = "",
    uploaded_at: str = "",
) -> RsOutcome:
    """Append an observed result list. ``positions`` maps URL to organic position.

    ``observed_on`` is the date the caller supplied; a date after the latest local date anywhere
    (UTC plus 14 hours, the same rule scoring uses) is refused, so an outcome can never be dated
    after a prediction written later. The file fields describe an imported file, not a live
    Google pull.
    """
    if observed_on > (_now().astimezone(timezone.utc) + timedelta(hours=14)).date().isoformat():
        raise LedgerError(f"observed_on {observed_on} is in the future; an outcome cannot be observed before it happens.")
    row = RsOutcome(
        observed_on=observed_on,
        query=" ".join(query.split()),
        provider=provider,
        depth=depth,
        positions={_key(url): int(position) for url, position in positions.items()},
        notes=notes,
        project_slug=_project_slug(project_slug),
        source_file=source_file,
        source_sha256=source_sha256,
        uploaded_at=uploaded_at,
    )
    session.add(row)
    session.commit()
    return row


def verify_ledger(session: Session) -> tuple[bool, str]:
    rows = session.query(RsPrediction).order_by(RsPrediction.id).all()
    prev = GENESIS
    legacy = 0
    for row in rows:
        if row.prev_hash != prev:
            return False, f"Prediction {row.id}: prev_hash does not match prediction before it. A row was removed or reordered."
        if _hash_version(row.payload) < 2:
            legacy += 1
        expected = _row_hash(row.prev_hash, row.created_at, row.kind, row.query, row.subject_url, row.payload, row.project_slug)
        if expected != row.row_hash:
            return False, f"Prediction {row.id}: contents changed after it was recorded."
        prev = row.row_hash
    head = prev if rows else "none"
    note = (
        f"{len(rows)} predictions, chain intact. Latest hash {head}. Publish this hash (a commit or a dated post) and "
        "check it later with `ledger verify --expect-head`: deleting the newest rows passes this check on its own."
    )
    if legacy:
        note += f" {legacy} legacy rows use the original hash and do not bind project_slug."
    return True, note


def head_is_anchored(session: Session, expected_head: str | None) -> tuple[bool, str]:
    """Whether a head hash published earlier is still a row of the chain.

    ``verify_ledger`` only checks the rows that remain, so deleting the newest rows (or rebuilding
    the whole chain) passes it. Comparing against a head recorded somewhere else catches both.
    The full hash or a unique prefix of at least ``MIN_HEAD_PREFIX`` hex characters is accepted,
    in any case. An empty value is refused, never skipped.
    """
    wanted = (expected_head or "").strip().lower()
    if not wanted:
        return False, f"The published head is empty. Give the hash that verify printed (at least {MIN_HEAD_PREFIX} characters)."
    if not re.fullmatch(f"[0-9a-f]{{{MIN_HEAD_PREFIX},64}}", wanted):
        return False, (
            f"The published head {wanted[:16]!r} is not a hash. Give the hash that verify printed, "
            f"in full or its first {MIN_HEAD_PREFIX} characters or more."
        )
    matches = session.query(RsPrediction).filter(RsPrediction.row_hash.startswith(wanted)).order_by(RsPrediction.id).limit(2).all()
    if len(matches) > 1:
        return False, f"The published head {wanted}... matches more than one row. Give more characters of the hash."
    row = matches[0] if matches else None
    if row is None:
        return False, f"The published head {wanted[:16]}... is not in this ledger: rows were removed or the chain was rebuilt."
    later = session.query(RsPrediction).filter(RsPrediction.id > row.id).count()
    return True, f"The published head is prediction {row.id}; {later} predictions were added after it."


def _same_project(prediction: RsPrediction, outcome: RsOutcome) -> bool:
    """Both rows name the same project. A blank slug is legacy and matches nothing."""
    left = _project_slug(prediction.project_slug)
    right = _project_slug(outcome.project_slug)
    return bool(left) and left == right


def prediction_time(prediction: RsPrediction) -> tuple[datetime | None, bool]:
    """(the time the prediction is dated by, whether it was backdated).

    The later of the stated ``created_at`` and the wall-clock ``payload.recorded_at``: a row
    cannot be resolved by an outcome that existed before the row did. Only this ledger's own
    write time counts, and it writes ``recorded_at`` only in version 2 rows, whose hash also binds
    the project. Any other row (another writer that does not stamp the write time, for example a
    replay at a stated protocol time, whatever its hash version) cannot have its time checked, so
    it is ``None`` (undated).
    """
    stated = _aware(datetime.fromisoformat(prediction.created_at))
    written_text = (prediction.payload or {}).get("recorded_at")
    if not written_text or _hash_version(prediction.payload) < 2:
        return None, False
    written = _aware(datetime.fromisoformat(written_text))
    return max(stated, written), written - stated > BACKDATE_TOLERANCE


def score_ledger(session: Session, *, project_slug: str | None = None) -> LedgerScore:
    """Score every call against the first later outcome for the same project and query.

    ``project_slug`` limits the score to one project. Predictions on synthetic, historical or no
    data are counted as unscored, not scored.
    """
    predictions = session.query(RsPrediction).order_by(RsPrediction.id).all()
    outcomes = session.query(RsOutcome).order_by(RsOutcome.observed_on, RsOutcome.id).all()
    if project_slug is not None:
        wanted = _project_slug(project_slug)
        predictions = [row for row in predictions if _project_slug(row.project_slug) == wanted]
        outcomes = [row for row in outcomes if _project_slug(row.project_slug) == wanted]
    by_query: dict[str, list[RsOutcome]] = {}
    for outcome in outcomes:
        by_query.setdefault(outcome.query, []).append(outcome)
    score = LedgerScore(predictions=len(predictions), calls=0, resolved=0, correct=0, unresolved=0)
    for prediction in predictions:
        dataset = str(((prediction.payload or {}).get("provenance") or {}).get("dataset") or "")
        if dataset in UNSCORED_DATASETS:
            score.unscored += 1
            continue
        if not _project_slug(prediction.project_slug):
            score.legacy += 1  # no project: matches no outcome, and is not "waiting" for one
            continue
        made, backdated = prediction_time(prediction)
        if made is None:
            score.undated += 1
            continue
        score.backdated += int(backdated)
        # In UTC: the stated time may carry any offset, and its own calendar date is not the rule.
        latest_local_day = (made.astimezone(timezone.utc) + timedelta(hours=14)).date().isoformat()
        later = [
            row
            for row in by_query.get(" ".join(prediction.query.split()), [])
            if row.observed_on > latest_local_day
            and _same_project(prediction, row)
            # An outcome already in the ledger when the prediction was written cannot resolve it.
            and (row.recorded_at is None or _aware(row.recorded_at) > made)
        ]
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
    if score.backdated:
        lines.append(
            f"{score.backdated} predictions were written more than an hour after their stated time. They are dated "
            "from when they were written, so observations made before that cannot resolve them."
        )
    if score.unscored:
        lines.append(f"{score.unscored} predictions on synthetic, historical or no data are not scored.")
    if score.legacy:
        lines.append(
            f"{score.legacy} predictions were written before projects existed (no project). They are kept and verified "
            "but not scored: their hash does not bind a project, so no outcome can be matched to them safely."
        )
    if score.undated:
        lines.append(
            f"{score.undated} predictions carry no write time from this ledger (written by another tool). Their stated time cannot be "
            "checked, so they are not evidence that the prediction came before the result, and are not scored."
        )
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


def _project_slug(value: str | None) -> str:
    """The slug as stored: stripped, or empty. Never a guessed project."""
    return (value or "").strip()


def _hash_version(payload: dict | None) -> int:
    return int((payload or {}).get("ledger_hash_version") or 1)


def _row_hash(
    prev_hash: str,
    created_at: str,
    kind: str,
    query: str,
    subject_url: str,
    payload: dict,
    project_slug: str | None = None,
) -> str:
    """Version 1 (no ``ledger_hash_version``): the original formula, without the slug. Version 2:
    also binds the stored slug, including an empty one."""
    body = {
        "prev": prev_hash,
        "created_at": created_at,
        "kind": kind,
        "query": query,
        "subject_url": subject_url,
        "payload": payload,
    }
    if _hash_version(payload) >= 2:
        body["project_slug"] = _project_slug(project_slug)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _is_fork_race(exc) -> bool:
    """True when another writer took the same prev_hash slot (a unique violation), the one
    IntegrityError worth retrying. A NOT NULL or other failure is reported, not retried.

    The driver's error code decides when there is one (PostgreSQL 23505); otherwise only the
    first line of the message is read, because a DETAIL line can quote the row's own values."""
    orig = getattr(exc, "orig", exc)
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if state:
        return str(state) == "23505"
    first = (str(orig).splitlines() or [""])[0].lower()
    return first.startswith("unique constraint failed") or "duplicate key" in first or "duplicate entry" in first


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _key(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url.strip()


def record_check(
    session: Session,
    result,
    *,
    html: str,
    calibration,
    provenance: dict,
    project_slug: str = DEFAULT_PROJECT_SLUG,
) -> RsPrediction | None:
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
        project_slug=project_slug,
    )
