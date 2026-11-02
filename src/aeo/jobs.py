"""Database-backed job queue.

Postgres claims with ``FOR UPDATE SKIP LOCKED``, so several workers can share
one database. SQLite is safe with a single worker. Set ``PROCESS_JOBS_INLINE``
to run jobs inside the API process instead; do not also run a worker then.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from aeo.config import Settings
from aeo.models import ABTest, Job, Simulation, Site, utcnow
from aeo.pipeline import run_ab, run_index_site, run_simulation

logger = logging.getLogger(__name__)


def enqueue(session: Session, kind: str, payload: dict) -> Job:
    job = Job(id=str(uuid4()), kind=kind, payload_json=payload, status="queued")
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session) -> Job | None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        row = session.execute(
            text(
                """
                WITH next_job AS (
                    SELECT id FROM jobs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE jobs
                SET status = 'running',
                    started_at = CURRENT_TIMESTAMP,
                    attempts = attempts + 1
                WHERE id IN (SELECT id FROM next_job)
                RETURNING id
                """
            )
        ).first()
        session.commit()
        if row is None:
            return None
        return session.get(Job, row[0])

    job = session.query(Job).filter(Job.status == "queued").order_by(Job.created_at, Job.id).first()
    if job is None:
        return None
    job.status = "running"
    job.started_at = utcnow()
    job.attempts += 1
    session.commit()
    return job


def execute_job(session: Session, settings: Settings, job: Job) -> None:
    job_id = job.id
    kind = job.kind
    payload = dict(job.payload_json or {})
    try:
        if kind == "simulation":
            result = run_simulation(session, settings, payload["simulation_id"])
        elif kind == "index_site":
            result = run_index_site(session, settings, payload["site_id"])
        elif kind == "ab_test":
            result = run_ab(session, settings, payload["ab_test_id"])
        else:
            raise RuntimeError(f"Unknown job kind {kind}")
        stored = session.get(Job, job_id)
        if stored is None:
            return
        stored.status = "succeeded"
        stored.result_json = result
        stored.finished_at = utcnow()
        stored.error = None
        session.commit()
    except Exception as exc:
        session.rollback()
        stored = session.get(Job, job_id)
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("Job %s failed", job_id)
        if stored is None:
            return
        stored.status = "failed"
        stored.error = message
        stored.finished_at = utcnow()
        _mark_failed(session, payload, message)
        session.commit()


def drain(session: Session, settings: Settings, *, limit: int = 25) -> int:
    processed = 0
    while processed < limit:
        job = claim_next(session)
        if job is None:
            return processed
        execute_job(session, settings, job)
        processed += 1
    return processed


def _mark_failed(session: Session, payload: dict, message: str) -> None:
    simulation_id = payload.get("simulation_id")
    if simulation_id:
        sim = session.get(Simulation, simulation_id)
        if sim is not None:
            sim.status = "failed"
            sim.error = message
            sim.finished_at = utcnow()
            site = session.get(Site, sim.site_id)
            if site is not None and site.status != "ready":
                site.status = "failed"
                site.error = message
    site_id = payload.get("site_id")
    if site_id and not simulation_id:
        site = session.get(Site, site_id)
        if site is not None:
            site.status = "failed"
            site.error = message
            site.finished_at = utcnow()
    ab_test_id = payload.get("ab_test_id")
    if ab_test_id:
        ab = session.get(ABTest, ab_test_id)
        if ab is not None:
            ab.status = "failed"
            ab.error = message
            ab.finished_at = utcnow()
