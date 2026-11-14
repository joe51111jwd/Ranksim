"""Audit cache warm-up (audit workstream).

After every pull or analysis, build the report once so the audit's results are computed in the pull's background
thread and written to plugins/audit/issues.json under the project. The pull saves the project to cloud storage after
after_pull, so a fresh server instance reads the cached audit instead of parsing every page again on its first report.
A warm-up that fails is logged and skipped: the report then computes the audit itself, as before.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def after_pull(*, project, report, ui) -> None:
    if project is None or ui is None or getattr(ui, "public_demo", False):
        return
    database = getattr(ui, "database", None)
    if not database:
        return
    try:
        from aeo.ranksim import workspace as ws
        from aeo.ranksim.report import build_report

        build_report(project, ws.load_meta(project), database, ui=ui)
    except Exception:  # noqa: BLE001 - a warm-up never breaks a pull
        log.warning("audit cache warm-up failed for %s", getattr(project, "slug", project), exc_info=True)
