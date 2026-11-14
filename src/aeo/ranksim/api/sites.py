"""Long jobs that stream progress: a site in, a report out (POST /api/v1/sites), and a project's pull.

Both are jobs (aeo.ranksim.jobs): each has an id, a per-site lock, progress events persisted to storage, and a
checkpoint. The answer is still one JSON object per line: a start line (now with the job id), progress lines, then a
done or error line, so the browser's stream code is unchanged. Underneath:

- Two different sites run at the same time. Starting a site (analysis or pull) that is already running anywhere
  attaches to that job: the stream replays its events from the start and follows it, live on the instance running it,
  by polling storage on any other instance. A refresh mid-analysis therefore reattaches by posting again.
- Long work pauses before the request's time limit (jobs.JobRegistry budget, 240 of Vercel's 300 seconds): pages
  fetched so far are already stored, the project and database are saved, and the stream ends with
  {"type": "paused", "job": id}. Posting again (or POST /api/v1/jobs/{id}/resume) continues from the checkpoint on any
  instance, without reading the site again and without asking Google again.
- GET /api/v1/jobs/{id}?cursor=N returns events after N and the status, for polling. GET /api/v1/jobs lists live jobs.

Every plugin's after_pull hook runs when a pull or an analysis finished, before the cloud save.
"""

from __future__ import annotations

import copy
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from aeo.ranksim import plugins
from aeo.ranksim import workspace as ws
from aeo.ranksim.api.common import _check_header_csrf, _error, _json_body, _not_public, _pull_summary, _split, _text, google_status
from aeo.ranksim.jobs import JobPaused, JobRegistry, valid_job_id

STREAM_SECONDS = 270.0  # a stream that only watches a job elsewhere ends before Vercel's 300 s limit; the browser posts again
POLL_SECONDS = 1.0
TERMINAL = {"done", "error"}


def registry(ui) -> JobRegistry:
    """The app's job registry: on the cloud store when there is one, so every instance sees every job."""
    reg = getattr(ui, "job_registry", None)
    if reg is None:
        cloud = getattr(ui, "cloud", None)
        hosted = cloud is not None and getattr(cloud, "enabled", False)
        # frugal on cloud storage: Vercel Blob operations are capped per month (see jobs.JobRegistry)
        reg = JobRegistry(cloud.store if hosted else None, scrub=ui.scrub, frugal=hosted)
        ui.job_registry = reg
    return reg


class _Events:
    """The queue-like object the analysis and pull code put events on, writing them to the job instead."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def put(self, item) -> None:
        if item is not None:
            self.ctx.emit(item)


def _site_key(domain: str) -> str:
    return "site:" + (domain or "").lower().removeprefix("www.")


def _terminal_line(job: dict) -> dict:
    status = job.get("status")
    if status == "done":
        return {"type": "done", **(job.get("result") or {})}
    if status == "error":
        return {"type": "error", "error": job.get("error") or "The job stopped."}
    return {"type": "paused", "job": job.get("id"), "message": "Still working. Continuing in a new request."}


def _job_stream(ui, record: dict, first: dict, resume_work) -> StreamingResponse:
    """Replay a job's events from the start, then follow it until it ends, pauses, or this request's time is up.

    A job running in this process is followed live. A job running on another instance is polled from storage; if its
    runner died (stalled), this request takes it over with resume_work.
    """
    reg = registry(ui)
    job_id = record["id"]

    def lines_sync():
        yield {**first, "job": job_id, "attached": bool(record.get("attached"))}
        cursor = 0
        began = time.monotonic()
        while True:
            end = None
            for item in reg.follow(job_id, cursor):
                if item.get("type") == "end":
                    end = item.get("job") or {}
                    break
                cursor = max(cursor, int(item.get("seq") or 0))
                yield item
            if end is None:
                yield {"type": "error", "error": "That job is gone."}
                return
            if end.get("status") in TERMINAL or end.get("status") == "paused":
                line = _terminal_line({**end, "id": job_id})
                cloud = getattr(ui, "cloud", None)
                if cloud is not None and getattr(cloud, "enabled", False):
                    line["sync"] = str(cloud.stamp)  # the browser keeps it in rs_sync, so the next request sees this save
                yield line
                return
            if end.get("stalled") and resume_work is not None:
                taken = reg.resume(job_id, resume_work)
                if taken is not None and not taken.get("attached"):
                    continue  # running here now: follow it live
            if time.monotonic() - began > STREAM_SECONDS:
                yield _terminal_line({"status": "paused", "id": job_id})
                return
            time.sleep(POLL_SECONDS * (10 if reg.frugal else 1))  # running on another instance: poll storage (a list each)

    async def lines():
        gen = lines_sync()
        while True:
            item = await run_in_threadpool(next, gen, None)
            if item is None:
                break
            yield json.dumps(item, default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson", headers={"cache-control": "no-store", "x-accel-buffering": "no"})


def _start_or_resume(ui, kind: str, key: str, title: str, work, params: dict, error_text) -> dict:
    """Start the job, or attach to the live one for this site; a paused or stalled one is resumed here."""
    reg = registry(ui)
    record = reg.start(kind, key, title, work, params=params, error_text=error_text)
    if record.get("attached") and (record.get("status") == "paused" or record.get("stalled")):
        resumed = reg.resume(record["id"], work, error_text=error_text)
        if resumed is not None:
            record = {**resumed, "attached": True}
    return record


def _store_error(exc: Exception) -> JSONResponse:
    return _error(503, f"Could not reach storage ({type(exc).__name__}). Nothing was started. Try again in a moment.")


def register(app: FastAPI, ui) -> None:
    from aeo.ranksim.objects import StoreError

    def pull_work(slug: str, use_google: bool):
        def work(ctx) -> dict:
            project = ui.project(slug)
            return {"report": _run_pull(ui, project, use_google, _Events(ctx), ctx=ctx)}

        return work

    def analyze_work(site: str, searches):
        def work(ctx) -> dict:
            return _analyze(ui, site, _Events(ctx), searches, ctx=ctx)

        return work

    @app.post("/api/v1/projects/{slug}/pull")
    async def api_pull(slug: str, request: Request) -> Response:
        """Ask Google (when a key is set), fetch every candidate page that is missing or stale, and train the engine.

        The answer streams one JSON object per line: progress events, then a done, error or paused line. The work
        finishes and is saved even if the browser stops reading. A pull already running for this site is joined.
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
        use_google = bool(body.get("google", True)) and google_status(ui)["ready"]
        work = pull_work(project.slug, use_google)
        try:
            record = await run_in_threadpool(
                _start_or_resume, ui, "pull", _site_key(project.domain), f"Pull for {project.name}", work,
                {"slug": project.slug, "google": use_google}, lambda exc: _pull_error(ui, project, exc),
            )
        except StoreError as exc:
            return _store_error(exc)
        if record.get("attached") and record.get("kind") != "pull":
            return _error(409, "This site is being analyzed right now. Its pull is part of that, so wait for it to finish.")
        return _job_stream(ui, record, {"type": "start", "google": use_google}, work)

    @app.post("/api/v1/sites")
    async def api_analyze(request: Request) -> Response:
        """Read the site, find its searches, ask Google, fetch the ranking pages, train, pick competitors.

        Streams one JSON object per line: step events, then done (with the project slug), error, or paused (post
        again to continue). Several sites run at once; posting a site that is already being analyzed reattaches.
        """
        from aeo.ranksim.project import ProjectError, clean_site

        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        body = await _json_body(request, 8192)
        site = _text(body.get("site"), 300).strip()
        if not site:
            return _error(400, "Type your site's address, for example yoursite.com.")
        try:
            host = clean_site(site)
        except (ProjectError, ValueError) as exc:
            return _error(400, ui.scrub(str(exc)))
        searches = body.get("searches")
        work = analyze_work(site, searches)
        try:
            record = await run_in_threadpool(
                _start_or_resume, ui, "analyze", _site_key(host), f"Analysis of {host}", work,
                {"site": site, "searches": searches}, lambda exc: _analyze_error(ui, exc),
            )
        except StoreError as exc:
            return _store_error(exc)
        return _job_stream(ui, record, {"type": "start", "google": google_status(ui)["ready"]}, work)

    def _work_for(record: dict):
        params = record.get("params") or {}
        if record.get("kind") == "analyze":
            return analyze_work(str(params.get("site") or ""), params.get("searches"))
        if record.get("kind") == "pull":
            return pull_work(str(params.get("slug") or ""), bool(params.get("google")))
        return None

    @app.get("/api/v1/jobs")
    def api_jobs() -> JSONResponse:
        """Live jobs (running or paused), newest first, so the app can show and reattach to work in progress."""
        try:
            rows = registry(ui).active(include_stalled=True)
        except StoreError as exc:
            return _store_error(exc)
        keep = ("id", "kind", "key", "title", "status", "updated", "stalled", "resumable")
        return JSONResponse({"jobs": [{name: row.get(name) for name in keep} for row in rows]})

    @app.get("/api/v1/jobs/{job_id}")
    def api_job(job_id: str, cursor: int = 0) -> JSONResponse:
        """Events after cursor plus the job's status. Poll with the returned cursor."""
        if not valid_job_id(job_id):
            return _error(404, "No such job.")
        try:
            view = registry(ui).events_since(job_id, cursor)
        except StoreError as exc:
            return _store_error(exc)
        if view is None:
            return _error(404, "No such job.")
        view.pop("params", None)
        view.pop("checkpoint", None)
        return JSONResponse(json.loads(json.dumps(view, default=str)))

    @app.post("/api/v1/jobs/{job_id}/resume")
    async def api_job_resume(job_id: str, request: Request) -> Response:
        """Continue a paused or stalled job here (any instance), streaming like the request that started it."""
        _check_header_csrf(ui, request)
        blocked = _not_public(ui)
        if blocked is not None:
            return blocked
        if not valid_job_id(job_id):
            return _error(404, "No such job.")
        reg = registry(ui)
        try:
            stored = await run_in_threadpool(reg.get, job_id)
            work = _work_for(stored) if stored else None
            if stored is None or work is None:
                return _error(404, "No such job.")
            record = await run_in_threadpool(reg.resume, job_id, work)
        except StoreError as exc:
            return _store_error(exc)
        if record is None:
            return _error(404, "No such job.")
        return _job_stream(ui, record, {"type": "start", "google": google_status(ui)["ready"]}, work)


class _CheckpointedSerp:
    """A Google provider that keeps every answer in the job's checkpoint and replays it on a resumed attempt."""

    def __init__(self, inner, ctx) -> None:
        import threading

        self.inner, self.ctx = inner, ctx
        self.name = inner.name
        self.is_google = getattr(inner, "is_google", True)
        self.synthetic = getattr(inner, "synthetic", False)
        self._lock = threading.Lock()

    def search(self, query: str, count: int = 10) -> list:
        from dataclasses import asdict

        from aeo.ranksim.serp import SerpHit

        key = f"{count}|{query}"
        with self._lock:
            kept = (self.ctx.checkpoint.get("serp") or {}).get(key)
        if kept is not None:
            return [SerpHit(**row) for row in kept]
        hits = self.inner.search(query, count)
        with self._lock:
            kept = self.ctx.checkpoint.setdefault("serp", {})
            kept[key] = [asdict(hit) for hit in hits]
            if len(kept) % 5 == 0:
                self.ctx.save_checkpoint()  # every few answers (each save is a storage write); a pause keeps them all
        return hits

    def close(self) -> None:
        closer = getattr(self.inner, "close", None)
        if callable(closer):
            closer()


def _save_now(ui) -> None:
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        cloud.save()


def _pausing_fetcher(ui, ctx, base, delay_seconds: float):
    """A batch fetcher that checks the job's time budget before every page and raises JobPaused when it is low.

    Each page the pull fetches is stored as it arrives, so a resumed pull skips them (they are fresh). With no
    injected fetcher, one robots-respecting Crawler serves a whole host batch, as project._crawler_batch does.
    """
    def pause(done: list) -> None:
        """Keep the pages this batch already fetched (the pull stores a batch only when it returns), then pause."""
        fetched = [item for item in done if getattr(item, "status", "") == "fetched" and getattr(item, "html", "")]
        if fetched:
            from aeo.ranksim.loop import store_fetched_page
            from aeo.ranksim.store import open_session

            session = open_session(ui.database)
            try:
                for item in fetched:
                    store_fetched_page(session, item)
                session.commit()
            finally:
                session.close()
        raise JobPaused("time budget")

    if base is not None:

        def fetch_injected(urls: list[str]) -> list:
            out = []
            for url in urls:
                if ctx.should_pause():
                    pause(out)
                out.extend(base([url]))
            return out

        return fetch_injected

    from aeo.crawl.fetch import Crawler
    from aeo.errors import UnsafeURL
    from aeo.ranksim.loop import FetchResult

    settings = ui.settings

    def fetch(urls: list[str]) -> list:
        crawler = Crawler(
            user_agent=settings.crawl_user_agent,
            delay_seconds=delay_seconds,
            allow_private=settings.crawl_allow_private,
            timeout=min(settings.request_timeout_seconds, 20.0),
        )
        out: list = []
        try:
            for url in urls:
                if ctx.should_pause():
                    pause(out)
                began = time.perf_counter()
                try:
                    got = crawler.fetch(url)
                except UnsafeURL as exc:
                    out.append(FetchResult(url=url, html="", status="blocked", error=str(exc)))
                    continue
                elapsed = (time.perf_counter() - began) * 1000
                if got.blocked:
                    out.append(FetchResult(url=url, html="", status="blocked", error=got.error or "Blocked by robots.txt", fetch_ms=elapsed))
                elif got.error or not got.html:
                    out.append(FetchResult(url=url, html="", status="error", error=got.error or "empty response", fetch_ms=elapsed))
                else:
                    out.append(FetchResult(url=url, html=got.html, status="fetched", fetch_ms=elapsed))
        finally:
            crawler.close()
        return out

    return fetch


def _after_pull(ui, project, summary: dict | None) -> None:
    """Every plugin's after_pull hook. Each gets its own copy of the summary, so the stream's answer stays as built."""
    plugins.call_all("after_pull", project=project, report=copy.deepcopy(summary), ui=ui)


def _run_pull(ui, project, use_google: bool, events, *, notify: bool = True, ctx=None) -> dict:
    """One pull, the same call the classic screens make, with hosted defaults: no AI engines, Google only with a key.

    notify=False leaves the after_pull hook to the caller (an analysis calls it once, when it has finished).
    ctx (a running job) makes the crawl pause before the time budget runs out (JobPaused, after saving). Google's
    answers are kept in the job's checkpoint as they arrive, so a resumed pull replays them and never spends credits
    twice for one search (a pull writes its google.json only when it finishes).
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
    if ctx is not None:
        if not options.get("skip_google"):
            from aeo.ranksim.project import _google_provider

            provider, _reason = _google_provider(ui.settings, options.get("serp_provider"))
            if provider is not None:
                options["serp_provider"] = _CheckpointedSerp(provider, ctx)
        options["fetcher"] = _pausing_fetcher(ui, ctx, options.get("fetcher"), float(options.get("delay_seconds", 0.4)))
    try:
        report = pull(project, ui.settings, database=ui.database, progress=progress, **options)
    except JobPaused:
        events.put({"type": "progress", "step": "store", "message": "Saving progress. It continues in a moment.", "done": 0, "total": 1, "seconds": 0})
        ui.forget(project.slug)
        _save_now(ui)
        raise
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


def _analyze(ui, site: str, events, searches=None, *, ctx=None) -> dict:
    """The whole autopilot for one site. Every step reports progress on events.

    ctx (a running job): once the project exists its slug and searches go in the checkpoint, so a resumed analysis
    goes straight to the pull without reading the site again.
    """
    from aeo.ranksim import discovery
    from aeo.ranksim.competitors import pick_competitors
    from aeo.ranksim.project import ProjectError, clean_site, create_project, latest_google, list_projects, load_project, slugify, update_searches
    from aeo.ranksim.site import read_site

    def step(name: str, message: str, done: int = 0, total: int = 0, state: str = "active", **extra) -> None:
        events.put({"type": "step", "step": name, "state": state, "message": ui.scrub(message), "done": done, "total": total, **extra})

    host = clean_site(site)
    want_domain = host.removeprefix("www.")
    project = None
    checkpoint = ctx.checkpoint if ctx is not None else {}
    if checkpoint.get("slug"):
        try:
            project = load_project(checkpoint["slug"], root=ui.root)
        except (ProjectError, FileNotFoundError, OSError, ValueError):
            project = None
        if project is not None:
            return _analyze_rest(ui, project, list(checkpoint.get("searches") or project.searches), events, step, ctx, resumed=True)
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
    if ctx is not None:
        ctx.checkpoint.update({"slug": project.slug, "searches": list(chosen)})
        ctx.save_checkpoint()
    return _analyze_rest(ui, project, chosen, events, step, ctx)


def _analyze_rest(ui, project, chosen: list, events, step, ctx, *, resumed: bool = False) -> dict:
    """The analysis after the project exists: the pull (when there is search data), competitors, after_pull, save."""
    from aeo.ranksim.competitors import pick_competitors
    from aeo.ranksim.project import latest_google

    if resumed:
        step("site", "Picked up where it paused.", 1, 1, state="done")
        step("searches", f"Using {len(chosen)} searches.", len(chosen), len(chosen), state="done", searches=chosen)
        events.put({"type": "project", "slug": project.slug, "name": project.name, "site": project.site})
    google = google_status(ui)
    if ctx is not None and ctx.should_pause():
        _save_now(ui)
        raise JobPaused("time budget")
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
        summary = _run_pull(ui, project, google["ready"], Relay(), notify=False, ctx=ctx)
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
