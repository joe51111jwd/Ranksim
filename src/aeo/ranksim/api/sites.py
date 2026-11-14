"""Long jobs that stream progress: a site in, a report out (POST /api/v1/sites), and a project's pull.

Both answer with one JSON object per line: a start line, progress lines, then a done or error line. The work runs
in a thread, finishes and is saved even if the browser stops reading. Every plugin's after_pull hook runs when a
pull or an analysis finished.
"""

from __future__ import annotations

import copy
import json
import queue
import threading

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import plugins
from aeo.ranksim import workspace as ws
from aeo.ranksim.api.common import _check_header_csrf, _error, _json_body, _not_public, _pull_summary, _split, _text, google_status


def _stream(first: dict, events: queue.Queue) -> StreamingResponse:
    """The first line, then every event put on the queue until None."""

    async def lines():
        yield json.dumps(first) + "\n"
        while True:
            item = await run_in_threadpool(events.get)
            if item is None:
                break
            yield json.dumps(item, default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"cache-control": "no-store", "x-accel-buffering": "no"})


def register(app: FastAPI, ui) -> None:
    @app.post("/api/v1/projects/{slug}/pull")
    async def api_pull(slug: str, request: Request) -> Response:
        """Ask Google (when a key is set), fetch every candidate page that is missing or stale, and train the engine.

        The answer streams one JSON object per line: progress events, then a done or error line. The work finishes
        and is saved even if the browser stops reading.
        """
        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        try:
            project = ui.project(slug)
        except LookupError:
            return _error(404, "No such project.")
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return _error(409, "A pull is already running for this project.")
        events: queue.Queue = queue.Queue()
        use_google = bool(body.get("google", True)) and google_status(ui)["ready"]

        def work() -> None:
            try:
                events.put({"type": "done", "report": _run_pull(ui, project, use_google, events)})
            except Exception as exc:  # noqa: BLE001 - shown to the owner, scrubbed of keys
                events.put({"type": "error", "error": _pull_error(ui, project, exc)})
            finally:
                lock.release()
                events.put(None)

        threading.Thread(target=work, name=f"pull-{slug}", daemon=True).start()
        return _stream({"type": "start", "google": use_google}, events)

    @app.post("/api/v1/sites")
    async def api_analyze(request: Request) -> Response:
        """Read the site, find its searches, ask Google, fetch the ranking pages, train, pick competitors.

        Streams one JSON object per line: step events, then done (with the project slug) or error.
        """
        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        site = _text(body.get("site"), 300).strip()
        if not site:
            return _error(400, "Type your site's address, for example yoursite.com.")
        lock = ui.jobs.project_lock("__analyze__")
        if not lock.acquire(blocking=False):
            return _error(409, "Another site is being analyzed right now. Try again in a minute.")
        events: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                events.put({"type": "done", **_analyze(ui, site, events, body.get("searches"))})
            except Exception as exc:  # noqa: BLE001 - shown to the owner, scrubbed of keys
                events.put({"type": "error", "error": _analyze_error(ui, exc)})
            finally:
                lock.release()
                events.put(None)

        threading.Thread(target=work, name="analyze", daemon=True).start()
        return _stream({"type": "start", "google": google_status(ui)["ready"]}, events)


def _after_pull(ui, project, summary: dict | None) -> None:
    """Every plugin's after_pull hook. Each gets its own copy of the summary, so the stream's answer stays as built."""
    plugins.call_all("after_pull", project=project, report=copy.deepcopy(summary), ui=ui)


def _run_pull(ui, project, use_google: bool, events, *, notify: bool = True) -> dict:
    """One pull, the same call the classic screens make, with hosted defaults: no AI engines, Google only with a key.

    notify=False leaves the after_pull hook to the caller (an analysis calls it once, when it has finished).
    """
    from datetime import date

    from aeo.ranksim.project import pull

    def progress(event) -> None:
        events.put(
            {
                "type": "progress",
                "step": event.step,
                "message": ui.scrub(event.message),
                "done": event.done,
                "total": event.total,
                "seconds": round(float(getattr(event, "seconds", 0.0) or 0.0), 1),
            }
        )

    options = {"engines": [], "engine_runs": 1, **ui.pull_options}
    meta = ws.load_meta(project)
    if meta.dataset == ws.DATASET_SYNTHETIC:
        options.update({"fetcher": ws.synthetic_fetcher, "skip_google": True, "fresh_days": 0})
    elif meta.dataset == ws.DATASET_HISTORICAL:
        options.update({"fetcher": ws.offline_fetcher, "skip_google": True, "fresh_days": 36500})
    else:
        options.setdefault("skip_google", not use_google)
    report = pull(project, ui.settings, database=ui.database, progress=progress, **options)
    ui.forget(project.slug)
    if report.searches_with_results:
        from aeo.ranksim.competitors import pick_competitors
        from aeo.ranksim.project import latest_google

        fresh = ws.load_meta(project)
        if not fresh.competitors:  # competitors are picked for you; editing them in Settings sticks
            fresh.competitors = pick_competitors(latest_google(project), project.domain)
            ws.save_meta(project, fresh)
    if report.google_kind == "api" and meta.dataset in {ws.DATASET_NONE, ws.DATASET_OBSERVED}:
        meta = ws.load_meta(project)
        meta.dataset = ws.DATASET_OBSERVED
        meta.dataset_label = f"Google results via {report.google_source or 'the configured provider'}, pulled {date.today().isoformat()}"
        ws.save_meta(project, meta)
    summary = _pull_summary(report)
    if notify:
        _after_pull(ui, project, summary)  # before the cloud save, so what plugins store is kept
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        events.put({"type": "progress", "step": "store", "message": "Saving the project, pages and engine.", "done": 0, "total": 1, "seconds": 0})
        cloud.save()
    return summary


def _pull_error(ui, project, exc: Exception) -> str:
    from aeo.errors import ProviderConfigError

    if isinstance(exc, ProviderConfigError) and "No REAL Google results" in str(exc):
        return (
            f"No Google results for {project.name} yet. Add a Google results key (SERPER_API_KEY) to this server, "
            "or import a CSV of positions (query, url, position). Nothing was invented."
        )
    return ui.scrub(f"The pull failed: {type(exc).__name__}: {str(exc)[:300]}")


def _analyze(ui, site: str, events, searches=None) -> dict:
    """The whole autopilot for one site. Every step reports progress on events."""
    from aeo.ranksim import discovery
    from aeo.ranksim.competitors import pick_competitors
    from aeo.ranksim.project import ProjectError, clean_site, create_project, latest_google, list_projects, load_project, slugify, update_searches
    from aeo.ranksim.site import read_site

    def step(name: str, message: str, done: int = 0, total: int = 0, state: str = "active", **extra) -> None:
        events.put({"type": "step", "step": name, "state": state, "message": ui.scrub(message), "done": done, "total": total, **extra})

    host = clean_site(site)
    want_domain = host.removeprefix("www.")
    project = None
    for row in list_projects(root=ui.root):
        try:
            found = load_project(row.slug, root=ui.root)
        except (ProjectError, FileNotFoundError, OSError, ValueError):
            continue
        if found.domain.removeprefix("www.") == want_domain:  # one project per site, with or without www
            project = found
            host = clean_site(found.site)  # read the host this project already uses
            break
    fetcher = ui.pull_options.get("fetcher")
    step("site", f"Reading {host}.")
    pages = read_site(host, ui.settings, ui.database, fetcher=fetcher, progress=lambda message, done, total: step("site", message, done, total))
    readable = [page for page in pages if page.status == "fetched"]
    if not readable:
        first = pages[0] if pages else None
        reason = (first.error or first.status) if first else "no answer"
        raise ProjectError(f"We couldn't read {host} ({reason}). Check the address, or that the site allows crawlers.")
    step("site", f"Read {len(readable)} of {len(pages)} pages.", len(pages), len(pages), state="done")

    brand = discovery.brand_of(pages, host)
    chosen = _split(searches) if searches else discovery.discover_with_plugins(pages, brand, host=host, project=project, ui=ui)
    if not chosen:
        chosen = [brand.lower()]
    step("searches", f"Found {len(chosen)} searches your pages are written for.", len(chosen), len(chosen), state="done", searches=chosen)

    google = google_status(ui)
    if project is None:
        name = brand
        try:
            load_project(slugify(name), root=ui.root)
            name = f"{brand} {want_domain}"
        except (ProjectError, FileNotFoundError, OSError, ValueError):
            pass
        project = create_project(name, host, chosen, root=ui.root)
    else:
        snapshot = latest_google(project)
        has_results = snapshot is not None and any(entry.results for entry in snapshot.searches)
        if not has_results:
            project = update_searches(project, replace=chosen)
        elif google["ready"]:
            project = update_searches(project, replace=project.searches + [q for q in chosen if q not in project.searches][:5])
        chosen = list(project.searches)  # searches with real Google results stay; guesses never replace them
    events.put({"type": "project", "slug": project.slug, "name": project.name, "site": project.site})
    meta = ws.load_meta(project)
    ws.save_meta(project, meta)

    previous = latest_google(project)
    can_rank = google["ready"] or (previous is not None and any(entry.results for entry in previous.searches))
    if can_rank:
        pull_events: list = []

        class Relay:
            def put(self, item):
                if item.get("type") == "progress":
                    name = {"google": "google", "crawl": "pages", "train": "model", "store": "save"}.get(item.get("step"))
                    if name:
                        events.put({"type": "step", "step": name, "state": "active", "message": item.get("message", ""), "done": item.get("done", 0), "total": item.get("total", 0)})
                pull_events.append(item)

        if not google["ready"]:
            step("google", "Using the Google results already saved for this site.", 1, 1, state="done")
        summary = _run_pull(ui, project, google["ready"], Relay(), notify=False)
        meta = ws.load_meta(project)
        picked = meta.competitors or pick_competitors(latest_google(project), project.domain)
        if picked and not meta.competitors:
            meta.competitors = picked
            ws.save_meta(project, meta)
        step("competitors", f"Found {len(picked)} competitors.", len(picked), len(picked), state="done", competitors=picked)
    else:
        summary = None
        step("google", "Search data isn't connected on this server yet, so rankings are skipped. Your pages were still checked.", state="skipped")
    _after_pull(ui, project, summary)  # before the cloud save, so what plugins store is kept
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        cloud.save()
    ui.forget(project.slug)
    step("report", "Report ready.", 1, 1, state="done")
    return {"slug": project.slug, "searches": chosen, "pull": summary}


def _analyze_error(ui, exc: Exception) -> str:
    from aeo.ranksim.project import ProjectError

    if isinstance(exc, ProjectError):
        return ui.scrub(str(exc).replace(str(ui.root), "the projects folder"))
    return ui.scrub(f"The analysis stopped: {type(exc).__name__}: {str(exc)[:300]}")
