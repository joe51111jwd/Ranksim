"""Poll the job table until stopped.

Run one worker with SQLite. Postgres can run several; claims use row locks.
"""

from __future__ import annotations

import logging
import signal
import time

from aeo.config import get_settings
from aeo.db import session_factory
from aeo.jobs import claim_next, execute_job

logger = logging.getLogger(__name__)
_stop = False


def _handle_stop(_signum, _frame) -> None:
    global _stop
    _stop = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    settings = get_settings()
    if settings.process_jobs_inline:
        logger.warning("PROCESS_JOBS_INLINE is true. The API will also run jobs; leave this worker off.")
    factory = session_factory(settings)
    logger.info("AEO worker started")
    while not _stop:
        session = factory()
        try:
            job = claim_next(session)
            if job is None:
                time.sleep(settings.worker_poll_seconds)
                continue
            logger.info("Running job %s (%s)", job.id, job.kind)
            execute_job(session, settings, job)
        finally:
            session.close()
    logger.info("AEO worker stopped")


if __name__ == "__main__":
    main()
