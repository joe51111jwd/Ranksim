"""Projects: the list, create, delete, settings (searches and competitors), CSV import, setup, overview, the report,
and the synthetic demo."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import workspace as ws
from aeo.ranksim.api.common import (
    _check_header_csrf,
    _engine_error,
    _error,
    _join,
    _json_body,
    _not_public,
    _pull_summary,
    _serve,
    _split,
    _text,
    google_status,
    storage_kind,
)


def project_list(ui) -> list[dict]:
    from aeo.ranksim.project import list_projects

    try:
        rows = list_projects(root=ui.root)
    except Exception:  # noqa: BLE001 - a broken project folder must not take the app down
        rows = []
    if ui.public_demo:
        rows = [row for row in rows if row.slug == ws.DEMO_SLUG]
    out = []
    for row in rows:
        try:
            meta = ws.load_meta(ui.project(row.slug))
        except LookupError:
            continue
        out.append(
            {
                "slug": row.slug,
                "name": row.name,
                "site": row.site,
                "queries": row.searches,
                "built": bool(row.has_engine),
                "dataset": meta.dataset,
                "dataset_label": meta.dataset_label,
            }
        )
    return out


def _dataset_info(meta: ws.WorkspaceMeta) -> dict:
    labels = {
        ws.DATASET_SYNTHETIC: ("Demo data", "Invented .example sites and positions. Not Google results."),
        ws.DATASET_HISTORICAL: ("Historical export", meta.dataset_label or ws.HISTORICAL_SOURCE),
        ws.DATASET_OBSERVED: ("Observed positions", meta.dataset_label or "Positions from an imported file or provider."),
        ws.DATASET_NONE: ("No positions yet", "Import positions on the classic Setup screen."),
    }
    short, long = labels.get(meta.dataset, labels[ws.DATASET_NONE])
    return {"kind": meta.dataset, "short": short, "long": long}


def project_setup(ui, slug: str) -> dict:
    """Everything the Data screen shows: per-query coverage, the pulls so far, and whether the engine is built."""
    from aeo.ranksim.project import list_pulls, load_pull_report

    project = ui.project(slug)
    meta = ws.load_meta(project)
    ready, reason = ws.engine_state(project)
    rows = []
    for item in ws.coverage(project, ui.database, meta):
        rows.append(
            {
                "q": item.search,
                "candidates": item.candidates,
                "stored": item.stored,
                "missing": item.missing[:12],
                "yours": item.yours,
                "competitors": item.competitors,
                "source": item.source,
                "pulled": item.pulled,
                "error": item.error,
            }
        )
    covered = {row["q"] for row in rows}
    rows.extend({"q": q, "candidates": 0, "stored": 0, "missing": [], "yours": None, "competitors": [], "source": "", "pulled": "", "error": "no results yet"} for q in project.searches if q not in covered)
    pulls = [
        {
            "id": info.pull_id,
            "when": info.written_at,
            "kind": info.google_kind or info.kind,
            "with_results": info.searches_with_results,
            "engine": info.has_engine,
            "pairwise": info.pairwise,
            "seconds": info.total_seconds,
        }
        for info in list_pulls(project)
        if info.finished
    ][-10:]
    last = load_pull_report(project)
    return {
        "slug": project.slug,
        "name": project.name,
        "site": project.site,
        "queries": rows,
        "competitors": list(meta.competitors),
        "data": _dataset_info(meta),
        "engine": {"ready": ready, "reason": reason},
        "pulls": list(reversed(pulls)),
        "last_pull": _pull_summary(last) if last is not None else None,
        "google": google_status(ui),
        "storage": storage_kind(ui),
        "read_only": ui.public_demo,
    }


def project_overview(ui, slug: str) -> dict:
    project = ui.project(slug)
    meta = ws.load_meta(project)
    ready, reason = ws.engine_state(project)
    data = {
        "slug": project.slug,
        "name": project.name,
        "site": project.site,
        "domain": project.domain,
        "data": _dataset_info(meta),
        "competitors": list(meta.competitors),
        "public_demo": ui.public_demo,
        "ai": ui.llm_ready(),
        "engine": {"ready": ready, "reason": reason},
        "model": None,
        "queries": [{"q": search, "intent": project.intents.get(search, "")} for search in project.searches],
        "error": "",
    }
    if not ready:
        return data
    from aeo.ranksim.project import ProjectError

    try:
        ctx = ui.context(slug)
    except ProjectError as exc:
        data["error"] = str(exc)
        return data
    metrics = ctx.metrics
    data["model"] = {
        "pairwise": metrics.pairwise,
        "ndcg": metrics.ndcg,
        "queries": metrics.n_searches,
        "pairs": metrics.n_pairs,
        "calibration": ctx.fit.calibration.note,
        "kind": "LambdaMART, each query held out",
    }
    rows = []
    for search in project.searches:
        view = ws.compare_search(ctx, search, meta)
        you = next((cand for cand in view.candidates if cand.url == view.your_url), None)
        rows.append(
            {
                "q": search,
                "intent": view.intent,
                "candidates": view.total,
                "scored": view.scored,
                "your_url": view.your_url or ws.your_url(ctx, search, meta),
                "your_title": you.title if you else "",
                "observed": view.your_observed,
                "predicted": view.your_predicted,
                "of": view.scored,
                "confidence": view.confidence,
                "pairwise": view.query_pairwise,
                "pairs": view.query_pairs,
                "error": view.error,
            }
        )
    data["queries"] = rows
    return data


REPORT_WAIT_SECONDS = 25.0
PERSIST_IF_SLOWER_SECONDS = 5.0  # a build this slow is saved to cloud storage at once, so other instances reuse it


def cached_report(ui, slug: str) -> dict | None:
    """The cached report when it matches the project's data. On a miss, look for a newer save once (another instance
    may have built it a moment ago; one storage read, rate limited), then answer None."""
    from aeo.ranksim import reportcache

    project = ui.project(slug)
    google_ready = google_status(ui)["ready"]
    hit = reportcache.load(project, reportcache.signature(ui, project, google_ready=google_ready))
    cloud = getattr(ui, "cloud", None)
    if hit is None and cloud is not None and getattr(cloud, "enabled", False) and hasattr(cloud, "stamp"):
        if cloud.refresh(hint=cloud.stamp + 1):
            ui.forget(slug)
            project = ui.project(slug)
            hit = reportcache.load(project, reportcache.signature(ui, project, google_ready=google_ready))
    return hit


def start_report_job(ui, slug: str) -> dict:
    """Start building the report as a job, or join the build already running for this project."""
    from aeo.ranksim.api.sites import local_registry

    return local_registry(ui).start("report", f"report:{slug}", f"Report for {slug}", report_work(ui, slug), params={"slug": slug})


def report_work(ui, slug: str):
    def work(ctx) -> dict:
        import time

        ctx.emit({"type": "step", "step": "report", "state": "active", "message": "Building the report."})
        began = time.monotonic()
        project_report(ui, slug, cached=False)  # writes the cache file into the project folder
        cloud = getattr(ui, "cloud", None)
        if time.monotonic() - began > PERSIST_IF_SLOWER_SECONDS and cloud is not None and getattr(cloud, "enabled", False):
            cloud.save()  # one put: every other instance now reads it instead of building for as long
        return {"slug": slug, "ready": True}

    return work


def wait_for_report_job(ui, record: dict) -> dict | None:
    """Wait up to REPORT_WAIT_SECONDS when the job runs in this process. The finished record, or None."""
    from aeo.ranksim.api.sites import local_registry

    reg = local_registry(ui)
    if reg._local(record["id"]) is None:
        return record if record.get("status") in {"done", "error"} else None  # running on another instance
    if not reg.join(record["id"], REPORT_WAIT_SECONDS):
        return None
    return reg.get(record["id"])


def project_report(ui, slug: str, *, cached: bool = True) -> dict:
    """The report, from the on-disk cache when nothing it is built from changed (reportcache.py), else built."""
    from aeo.ranksim import reportcache

    project = ui.project(slug)
    google_ready = google_status(ui)["ready"]
    key = reportcache.signature(ui, project, google_ready=google_ready)
    if cached:
        hit = reportcache.load(project, key)
        if hit is not None:
            return hit
    data = _build_project_report(ui, project, google_ready)
    # Building writes other caches into the project folder (page facts, competitor profiles, discovery), so the key is
    # taken again: it describes the folder as the next request will find it.
    reportcache.store(project, reportcache.signature(ui, project, google_ready=google_ready), data)
    return data


def _build_project_report(ui, project, google_ready: bool) -> dict:
    from aeo.ranksim.report import build_report

    slug = project.slug
    meta = ws.load_meta(project)
    ready, _reason = ws.engine_state(project)
    ctx = None
    if ready:
        try:
            ctx = ui.context(slug)
        except Exception:  # noqa: BLE001 - the report shows what it can without the engine
            ctx = None
    data = build_report(project, meta, ui.database, ctx=ctx, google_ready=google_ready, ui=ui)
    data["slug"] = project.slug
    data["data"] = _dataset_info(meta)
    data["engine"] = {"ready": ready}
    return data


def register(app: FastAPI, ui) -> None:
    serve = _serve()

    @app.get("/api/v1/projects")
    def api_projects() -> JSONResponse:
        return JSONResponse({"public_demo": ui.public_demo, "projects": project_list(ui), "demo_slug": ws.DEMO_SLUG})

    @app.get("/api/v1/projects/{slug}")
    async def api_project(slug: str) -> JSONResponse:
        try:
            data = await run_in_threadpool(project_overview, ui, slug)
        except LookupError:
            return _error(404, "No such project.")
        except Exception as exc:  # noqa: BLE001 - shown, scrubbed
            return _engine_error(ui, exc)
        return JSONResponse(data)

    @app.post("/api/v1/demo")
    async def api_demo(request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        slug = ws.DEMO_SLUG
        if ws.demo_ready(ui.root):
            return JSONResponse({"slug": slug, "built": False})
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return _error(409, "The demo is being built right now. Try again in a few seconds.")
        try:
            await run_in_threadpool(ws.load_synthetic_demo, ui.root, ui.settings, ui.database)
        except Exception as exc:  # noqa: BLE001
            return _error(500, ui.scrub(f"The demo could not be built: {type(exc).__name__}: {str(exc)[:200]}"))
        finally:
            lock.release()
        ui.forget(slug)
        return JSONResponse({"slug": slug, "built": True})

    @app.post("/api/v1/projects")
    async def api_create(request: Request) -> JSONResponse:
        from aeo.ranksim.project import ProjectError, create_project

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 256 * 1024)
        searches = _split(body.get("queries"))
        if not searches:
            return _error(400, "Add at least one search query.")
        if len(searches) > serve.MAX_SEARCHES:
            return _error(400, f"At most {serve.MAX_SEARCHES} queries per project.")
        try:
            made = create_project(_text(body.get("name"), 200), _text(body.get("site"), 300), searches, root=ui.root)
        except (ProjectError, ValueError) as exc:
            message = str(exc).replace(f" in {ui.root}", "").replace(str(ui.root), "the projects folder")
            return _error(400, message)
        meta = ws.WorkspaceMeta()
        meta.competitors, problems = ws.parse_competitors(_text(_join(body.get("competitors")), 20000), made.domain)
        meta.dataset = ws.DATASET_NONE
        ws.save_meta(made, meta)
        return JSONResponse({"slug": made.slug, "problems": problems})

    @app.get("/api/v1/projects/{slug}/setup")
    async def api_setup(slug: str) -> JSONResponse:
        try:
            return JSONResponse(await run_in_threadpool(project_setup, ui, slug))
        except LookupError:
            return _error(404, "No such project.")

    @app.post("/api/v1/projects/{slug}/settings")
    async def api_settings(slug: str, request: Request) -> JSONResponse:
        from aeo.ranksim.project import ProjectError, update_searches

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 256 * 1024)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        problems: list[str] = []
        if "queries" in body:
            searches = _split(body.get("queries"))
            if len(searches) > serve.MAX_SEARCHES:
                return _error(400, f"At most {serve.MAX_SEARCHES} queries per project.")
            try:
                project = update_searches(project, replace=searches)
            except ProjectError as exc:
                return _error(400, str(exc))
        if "competitors" in body:
            meta = ws.load_meta(project)
            meta.competitors, problems = ws.parse_competitors(_text(_join(body.get("competitors")), 20000), project.domain)
            ws.save_meta(project, meta)
        ui.forget(slug)
        return JSONResponse({"ok": True, "problems": problems, "queries": project.searches})

    @app.post("/api/v1/projects/{slug}/import")
    async def api_import(slug: str, request: Request) -> JSONResponse:
        import re

        from aeo.ranksim.studio import DATE_RE, MAX_CSV_BYTES
        from aeo.ranksim.workspace_ui import _import_bytes

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, MAX_CSV_BYTES + 64 * 1024)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        data = _text(body.get("csv"), MAX_CSV_BYTES + 1).encode("utf-8")
        if not data.strip():
            return _error(400, "That file is empty.")
        if len(data) > MAX_CSV_BYTES:
            return _error(413, "That CSV is over 5 MB.")
        seen = _text(body.get("date"), 20).strip()
        if seen and not re.match(DATE_RE, seen):
            return _error(400, "Give the date the positions were seen as YYYY-MM-DD, or leave it blank.")
        check = ws.check_csv(data)
        if not check.ok:
            return JSONResponse({"error": "The CSV was not imported.", "problems": check.errors[:8]}, status_code=400)
        report = await run_in_threadpool(_import_bytes, ui, project, data, seen, bool(body.get("add_queries", True)))
        if isinstance(report, str):
            return _error(400, report)
        ui.forget(slug)
        return JSONResponse(
            {
                "rows": report.rows,
                "imported": report.searches_imported,
                "added": report.searches_added,
                "missing": report.searches_missing,
                "warnings": check.warnings[:8],
            }
        )

    @app.post("/api/v1/projects/{slug}/delete")
    async def api_delete(slug: str, request: Request) -> JSONResponse:
        import shutil

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 4096)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        if _text(body.get("confirm"), 100) != project.slug:
            return _error(400, "Type the project's short name to confirm.")
        folder = Path(project.dir).resolve()
        if folder.parent != ui.root:
            return _error(400, "That project folder is outside the projects folder. Nothing was deleted.")
        await run_in_threadpool(shutil.rmtree, folder)
        ui.forget(slug)
        return JSONResponse({"ok": True})

    @app.get("/api/v1/projects/{slug}/report")
    async def api_report(slug: str) -> JSONResponse:
        """The cached report at once. Without one, the build runs as a job (any instance joins it rather than starting
        another); this request waits up to REPORT_WAIT_SECONDS for it, else answers 202 {"building": true, "job"}:
        ask again (or poll GET /api/v1/jobs/{job}). Never a request held for minutes."""
        try:
            hit = await run_in_threadpool(cached_report, ui, slug)
        except LookupError:
            return _error(404, "No such project.")
        if hit is not None:
            return JSONResponse(hit)
        from aeo.ranksim.objects import StoreError

        try:
            record = await run_in_threadpool(start_report_job, ui, slug)
        except StoreError as exc:
            return _error(503, f"Could not reach storage ({type(exc).__name__}). Try again in a moment.")
        finished = await run_in_threadpool(wait_for_report_job, ui, record)
        if finished is not None and finished.get("status") == "error":
            return _error(500, finished.get("error") or "The report could not be built.")
        hit = await run_in_threadpool(cached_report, ui, slug) if finished is not None else None
        if hit is not None:
            return JSONResponse(hit)
        return JSONResponse({"building": True, "job": record["id"], "retry_after": 3}, status_code=202, headers={"retry-after": "3"})
