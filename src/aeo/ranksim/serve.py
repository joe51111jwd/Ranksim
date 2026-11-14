"""Ranksim web UI: projects, one-button pulls, and page tests. Server-rendered HTML.

Pages
  GET  /                              projects, keys, and a new project form
  POST /projects                      create a project (name, site, searches one per line)
  GET  /p/{slug}                      the project: searches, last pull, coverage, accuracy, Pull now, Test a page
  POST /p/{slug}/pull                 start a pull in a background thread (progress at /api/jobs/{id})
  POST /p/{slug}/test                 test a page: an uploaded .html file, pasted HTML, or a preview URL
  GET  /p/{slug}/jobs/{id}            progress of a pull or a test; forwards to the result when done
  GET  /p/{slug}/results/{rid}        the board: every search, the REAL top 10, your page at its PREDICTED slot
  GET  /p/{slug}/results/{rid}.json   the same board as JSON
  GET  /p/{slug}/versions?url=U       tested versions of one URL
  GET  /p/{slug}/compare?url=U&a=v1&b=v2  two versions rescored now on the same engine
  GET  /p/{slug}/engines              the newest REAL AI engine citations per search
  POST /p/{slug}/searches             replace the key searches
  POST /p/{slug}/import               import REAL Google positions from a query,url,position CSV
  GET  /api/jobs/{id}                 job progress as JSON (polled by /static/app.js)
  GET  /demo, /space, POST /check     the old single-query demo, unchanged (built on first use)

Security: binds 127.0.0.1 by default (see cli.py), checks the Host header,
needs a per-process CSRF token on every POST, caps request bodies and uploads
at 5 MB, escapes every crawled, uploaded or pulled string, sends a strict
Content-Security-Policy (no inline script), fetches preview URLs only through
the robots-respecting, SSRF-safe Crawler, and never puts a key in a page,
a job message or a log line.

Every model number on a page is labeled PREDICTED. Google and AI engine data
is labeled REAL with its source and pull time, or "pull date not recorded".
"""

from __future__ import annotations

import html as _html
import json
import re
import secrets
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from aeo.config import Settings
from aeo.crawl.fetch import Crawler
from aeo.errors import AeoError, UnsafeURL
from aeo.ranksim.check import check_draft, score_site, suggest_links
from aeo.ranksim.confidence import LooFit, loo_fit
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.demo import render_page, render_space_page
from aeo.ranksim.groundtruth import load_serp_file
from aeo.ranksim.judge import relevance_experiment
from aeo.ranksim.keywords import build_keywords
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.linkgraph import LinkGraph, attach_graph, load_graph
from aeo.ranksim.llm import resolve_llm
from aeo.ranksim.space import build_space, domains_from_labels, read_competitors
from aeo.ranksim.store import open_session

SAMPLE_QUERY = "hcss heavyjob alternative"
SAMPLE_URL = "https://www.smartcrewhq.com/compare/hcss-heavyjob-alternative/"
SAMPLE_FILE = Path("smartcrew/pr8_v3/compare-hcss-heavyjob-alternative.html")
GRAPH_PATH = Path("smartcrew/linkgraph.json")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
FORM_OVERHEAD_BYTES = 64 * 1024
MAX_URL_CHARS = 2048
SECRET_ENV = ("RANKSIM_PASSWORD", "RANKSIM_SECRET", "BLOB_READ_WRITE_TOKEN", "GOOGLE_OAUTH_CLIENT_SECRET", "CRON_SECRET")
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})  # GuardMiddleware caps their bodies and refuses them cross-site
MAX_SEARCHES = 200
JOB_KEEP = 200
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
RESULT_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}(?:-[0-9]{1,4})?$")
VERSION_RE = re.compile(r"^v[0-9]{1,6}$")
SECRET_FIELDS = (
    "serpapi_key",
    "serper_api_key",
    "dataforseo_login",
    "dataforseo_password",
    "brave_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "openrouter_api_key",
    "perplexity_api_key",
    "gemini_api_key",
)
STRICT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
LEGACY_CSP = (
    "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
LEGACY_PATHS = ("/demo", "/space", "/check")
# The Studio shell may preview a page in a sandboxed srcdoc frame. That frame inherits this policy, so the shell allows
# inline and remote styles and remote images (never scripts: the frame is sandboxed without allow-scripts).
STUDIO_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https: http:; "
    "font-src 'self' data: https:; connect-src 'self'; frame-src 'self'; form-action 'self'; frame-ancestors 'none'; "
    "base-uri 'self' https:; object-src 'none'"
)
# base-uri allows https so the editable preview (a srcdoc frame, which inherits this policy) can set <base> to the
# page's own address and load its own CSS, images and fonts. Scripts stay 'self' only, so a base element cannot
# redirect one. The Studio builds its own DOM with textContent and never parses outside HTML into the app page.


def csp_for(path: str) -> str:
    if path.startswith(LEGACY_PATHS):
        return LEGACY_CSP
    if path in {"/", "/app", "/start", "/edit"} or path.startswith("/app/"):
        return STUDIO_CSP
    if not path.startswith(("/api/", "/studio/", "/static/")):
        from aeo.ranksim import plugins

        custom = plugins.csp_for(path)  # a plugin page, for example a public share link
        if custom:
            return custom
    return STRICT_CSP
STEP_LABELS = {
    "google": "Google top 10 (REAL)",
    "engines": "AI engines (REAL)",
    "crawl": "Store every result page",
    "train": "Train the mini search engine",
    "fetch": "Fetch the preview URL",
    "load": "Load the mini search engine",
    "score": "Score your page on every search",
    "simulate": "AI answer simulation (PREDICTED)",
    "save": "Save this version",
    "done": "Finished",
}


# ---------------------------------------------------------------------------
# The old single-query demo (unchanged; now at /demo, still answering POST /check)
# ---------------------------------------------------------------------------


class DemoState:
    def __init__(self, settings: Settings, serp_file: str, database: str) -> None:
        self.settings = settings
        self.today = date.today()
        self.labels = load_serp_file(serp_file)
        self.queries = list(dict.fromkeys(row.query for row in self.labels))
        intents = {row.query: row.intent for row in build_keywords()}
        self.intent_for = intents
        from aeo.ranksim.bootstrap import seed_corpus_if_empty

        print(seed_corpus_if_empty(database))
        session = open_session(database)
        try:
            self.pages = load_corpus_docs(session)
        finally:
            session.close()
        self.graph = load_graph(str(GRAPH_PATH)) if GRAPH_PATH.is_file() else LinkGraph(
            notes=["No linkgraph.json yet. Run python -m aeo.ranksim graph."]
        )
        attach_graph(self.pages, self.graph)
        examples, uncovered, _withheld = build_examples(self.labels, self.pages, intents, today=self.today)
        self.uncovered = uncovered
        self.fit: LooFit = loo_fit(examples, keep_models=True)
        self.llm = resolve_llm(settings)
        self.relevance_note = relevance_experiment(examples, None).note
        self.site_rows = score_site(self.pages, self.labels, self.fit.models, self.queries, self.today)
        self.fixes = suggest_links(self.graph, self.pages, self.labels, self.fit.models, today=self.today)
        competitors = read_competitors(Path("smartcrew/competitors.csv"))
        if not competitors:
            competitors = domains_from_labels(self.labels, "smartcrewhq.com")
        self.space = build_space(
            self.labels,
            self.pages,
            self.fit.models,
            competitors,
            our_domain="smartcrewhq.com",
            today=self.today,
        )

    def accuracy_line(self) -> str:
        pairwise = "n/a" if self.fit.pairwise is None else f"{self.fit.pairwise:.3f}"
        ndcg = "n/a" if self.fit.ndcg is None else f"{self.fit.ndcg:.3f}"
        spearman = "n/a" if self.fit.spearman is None else f"{self.fit.spearman:.3f}"
        top3 = "n/a" if self.fit.top3 is None else f"{self.fit.top3:.3f}"
        return (
            f"Backtest on the pages stored now, leave-one-query-out, PREDICTED: pairwise {pairwise}, "
            f"NDCG at 10 {ndcg}, Spearman {spearman}, top-3 hit rate {top3}. "
            f"{self.fit.n_queries} queries, {self.fit.n_pairs} pairs, {self.uncovered} labeled rows had no stored HTML. "
            "Measured now on this data; 0.50 is a coin flip."
        )

    def llm_note(self) -> str:
        if self.llm.available:
            return f"AI layer: {self.llm.name}. Explanations and rewrites are model text about PREDICTED ranks."
        return "AI layer not run: needs OPENROUTER_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY. No explanation or rewrite is shown."

    def page(self, *, selected: str, url_value: str, html_value: str, result=None, error: str = "") -> HTMLResponse:
        html = render_page(
            queries=self.queries,
            selected=selected or (self.queries[0] if self.queries else ""),
            url_value=url_value,
            html_value=html_value,
            result=result,
            accuracy_line=self.accuracy_line(),
            calibration_note=self.fit.calibration.note,
            llm_note=self.llm_note(),
            relevance_note=self.relevance_note,
            site_rows=self.site_rows,
            graph=self.graph,
            fixes=self.fixes,
            error=error,
            csrf=getattr(self, "csrf", ""),
        )
        return HTMLResponse(html)

    def sample_html(self) -> str:
        if SAMPLE_FILE.is_file():
            return SAMPLE_FILE.read_text(encoding="utf-8", errors="replace")
        return "<html><head><title>HCSS alternative for dirtwork contractors</title></head><body><h1>HCSS alternative</h1><p>Draft.</p></body></html>"


def _legacy_check(state: DemoState, query: str, url: str, html: str, ai: str) -> HTMLResponse:
    query = " ".join(query.split())
    url = url.strip()
    if query not in state.fit.models:
        return state.page(selected=query, url_value=url, html_value=html, error="That query is not in the stored Google file. No rank was invented.")
    draft = html.strip()
    if not draft and url:
        draft = _fetch_html(state.settings, url)
        if not draft:
            return state.page(selected=query, url_value=url, html_value=html, error=f"Could not fetch {url}. No page text was invented.")
    if not draft:
        return state.page(selected=query, url_value=url, html_value="", error="Paste HTML or provide a URL.")
    if not url:
        url = "https://draft.local/page"
    result = check_draft(
        query=query,
        html=draft,
        url=url,
        labels=state.labels,
        pages=state.pages,
        model=state.fit.models.get(query),
        calibration=state.fit.calibration,
        today=state.today,
        intent=state.intent_for.get(query, "commercial"),
        llm=state.llm,
        run_ai=ai == "1",
    )
    return state.page(selected=query, url_value=url, html_value="" if url != "https://draft.local/page" else draft, result=result, error=result.error)


def _fetch_html(settings: Settings, url: str) -> str:
    html, _error = fetch_preview(settings, url)
    return html


def fetch_preview(settings: Settings, url: str) -> tuple[str, str]:
    """(html, error) through the robots-respecting, SSRF-safe Crawler. Private addresses are refused."""
    crawler = Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=0,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
    )
    try:
        fetched = crawler.fetch(url)
    except UnsafeURL as exc:
        return "", f"refused: {exc}"
    except Exception as exc:  # noqa: BLE001 - a failed fetch is reported, never invented
        return "", f"fetch failed: {type(exc).__name__}"
    finally:
        crawler.close()
    if fetched.blocked:
        return "", "blocked by robots.txt"
    if fetched.error or not fetched.html:
        return "", fetched.error or "empty response"
    return fetched.html, ""


# ---------------------------------------------------------------------------
# Background jobs (pulls and tests), one at a time per project
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    kind: str  # pull or test
    slug: str
    title: str
    created_at: str
    status: str = "queued"  # queued, running, done, error
    message: str = ""
    error: str = ""
    result_url: str = ""
    started: float = 0.0
    finished: float = 0.0
    created: float = 0.0
    steps: dict[str, dict] = field(default_factory=dict)
    timings: list[dict] = field(default_factory=list)


class JobManager:
    """Runs work in daemon threads. Jobs for the same project wait for each other."""

    def __init__(self, scrub: Callable[[str], str]) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._project_locks: dict[str, threading.Lock] = {}
        self._scrub = scrub

    def project_lock(self, slug: str) -> threading.Lock:
        with self._lock:
            if slug not in self._project_locks:
                self._project_locks[slug] = threading.Lock()
            return self._project_locks[slug]

    def start(self, kind: str, slug: str, title: str, work: Callable[[Job], str]) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            kind=kind,
            slug=slug,
            title=title,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            created=time.perf_counter(),
            message="Waiting for the project's previous job to finish." ,
        )
        with self._lock:
            self._jobs[job.id] = job
            if len(self._jobs) > JOB_KEEP:
                for old in sorted(self._jobs.values(), key=lambda item: item.created)[: len(self._jobs) - JOB_KEEP]:
                    if old.status in {"done", "error"}:
                        self._jobs.pop(old.id, None)
        thread = threading.Thread(target=self._run, args=(job, work), name=f"{kind}-{slug}", daemon=True)
        thread.start()
        return job

    def _run(self, job: Job, work: Callable[[Job], str]) -> None:
        with self.project_lock(job.slug):
            with self._lock:
                job.status = "running"
                job.started = time.perf_counter()
                job.message = "Started."
            try:
                url = work(job)
                with self._lock:
                    job.status = "done"
                    job.result_url = url
                    job.message = job.message or "Done."
            except Exception as exc:  # noqa: BLE001 - every failure is shown, scrubbed of keys
                text = self._scrub(_error_text(exc))
                with self._lock:
                    job.status = "error"
                    job.error = text
                print(f"ranksim job {job.kind} {job.slug} failed: {text}", file=sys.stderr, flush=True)
            finally:
                with self._lock:
                    job.finished = time.perf_counter()

    def event(self, job: Job, step: str, message: str, done: int = 0, total: int = 0, seconds: float | None = None) -> None:
        text = self._scrub(_plain(message))
        with self._lock:
            elapsed = seconds if seconds is not None else (time.perf_counter() - (job.started or job.created))
            current = job.steps.get(step) or {"step": step, "label": STEP_LABELS.get(step, step), "first": round(elapsed, 2)}
            skipped = "not run" in text.lower() and not int(total)
            current.update({"message": text, "done": int(done), "total": int(total), "seconds": round(elapsed, 2), "skipped": skipped})
            job.steps[step] = current
            job.message = text

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active(self, slug: str, kind: str | None = None) -> Job | None:
        with self._lock:
            for job in sorted(self._jobs.values(), key=lambda item: -item.created):
                if job.slug == slug and job.status in {"queued", "running"} and (kind is None or job.kind == kind):
                    return job
        return None

    def as_dict(self, job: Job) -> dict:
        with self._lock:
            end = job.finished or time.perf_counter()
            begin = job.started or job.created
            return {
                "id": job.id,
                "kind": job.kind,
                "slug": job.slug,
                "title": job.title,
                "status": job.status,
                "message": job.message,
                "error": job.error,
                "result_url": job.result_url,
                "created_at": job.created_at,
                "elapsed": round(max(0.0, end - begin), 1),
                "steps": [dict(item) for item in job.steps.values()],
                "timings": [dict(item) for item in job.timings],
            }

    def wait(self, job_id: str, timeout: float = 60.0) -> Job | None:
        """Block until the job ends (tests and scripts)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if job is None or job.status in {"done", "error"}:
                return job
            time.sleep(0.02)
        return self.get(job_id)


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, (AeoError, ValueError)):
        return _plain(str(exc))
    return _plain(f"{type(exc).__name__}: {str(exc)[:300]}")


def _plain(text: str) -> str:
    return str(text or "").replace("\u2014", "-").replace("\u2013", "-")


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------


class UiState:
    def __init__(
        self,
        *,
        settings: Settings,
        database: str,
        projects_root: str | Path | None,
        legacy_state: DemoState | None = None,
        legacy_factory: Callable[[], DemoState] | None = None,
        pull_options: dict | None = None,
        preview_fetch: Callable[[str], tuple[str, str]] | None = None,
        llm_factory: Callable[[], object] | None = None,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        public_demo: bool = False,
        csrf_token: str | None = None,
    ) -> None:
        from aeo.ranksim.project import projects_root as resolve_root

        self.public_demo = bool(public_demo)

        self.settings = settings
        self.database = database
        self.root = Path(resolve_root(projects_root)).resolve()
        # One random token per process. A public multi-instance host passes a token shared by its instances.
        self.csrf = csrf_token or secrets.token_urlsafe(32)
        self.jobs = JobManager(self.scrub)
        self.pull_options = dict(pull_options or {})
        self.preview_fetch = preview_fetch or (lambda url: fetch_preview(settings, url))
        self.llm_factory = llm_factory or (lambda: resolve_llm(settings))
        self.max_upload_bytes = max_upload_bytes
        self._legacy = legacy_state
        self._legacy_factory = legacy_factory
        self._legacy_lock = threading.Lock()
        self._contexts: dict[str, tuple[tuple, object]] = {}
        self._demo_checked = False
        self._context_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._secrets = [str(getattr(settings, name)) for name in SECRET_FIELDS if getattr(settings, name, None)]
        # Hosting secrets that are not provider settings: never echoed in an error, a job message or a log line either.
        import os

        self._secrets += [os.environ[name].strip() for name in SECRET_ENV if os.environ.get(name, "").strip()]

    # secrets never leave the process
    def scrub(self, text: str) -> str:
        value = str(text or "")
        for secret in self._secrets:
            if len(secret) >= 4:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)(api[_-]?key|key|token|password|secret)=([^&\s\"']+)", r"\1=[redacted]", value)
        value = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [redacted]", value)
        return value

    def legacy(self) -> DemoState | None:
        with self._legacy_lock:
            if self._legacy is None and self._legacy_factory is not None:
                self._legacy = self._legacy_factory()
            if self._legacy is not None:
                self._legacy.csrf = self.csrf  # the old form posts with the same per-process token
            return self._legacy

    def project(self, slug: str):
        from aeo.ranksim.project import load_project

        if not SLUG_RE.match(slug or ""):
            raise LookupError("No such project.")
        if not (self.root / slug / "project.json").is_file():
            raise LookupError("No such project.")
        if self.public_demo:
            from aeo.ranksim.workspace import DEMO_SLUG

            if slug != DEMO_SLUG:
                raise LookupError("No such project.")  # a public host serves the synthetic demo and nothing else
        return load_project(slug, root=self.root)

    def context(self, slug: str):
        """The loaded mini engine, cached until a new pull or a change of searches."""
        from aeo.ranksim.project import latest_google, load_project_context

        project = self.project(slug)
        google = latest_google(project)
        key = (google.pull_id if google else None, tuple(project.searches))
        with self._lock:
            lock = self._context_locks.setdefault(slug, threading.Lock())
        with lock:
            cached = self._contexts.get(slug)
            if cached is not None and cached[0] == key:
                return cached[1]
            ctx = load_project_context(project, database=self.database)
            self._contexts[slug] = (key, ctx)
            return ctx

    def forget(self, slug: str) -> None:
        with self._lock:
            self._contexts.pop(slug, None)

    def ensure_demo(self) -> None:
        """Public demo only: build the synthetic demo the first time a request needs it, once per process.

        Not at start, so a cold start that serves the shell, the session or an asset never trains (or imports
        LightGBM). Concurrent first requests wait on the demo's project lock; one builds, the rest find it ready.
        A failed build is logged and not retried automatically (as at start before); the demo button still can."""
        if not self.public_demo or self._demo_checked:
            return
        from aeo.ranksim import workspace as ws

        with self.jobs.project_lock(ws.DEMO_SLUG):
            if self._demo_checked:
                return
            try:
                if not ws.demo_ready(self.root):
                    ws.load_synthetic_demo(self.root, self.settings, self.database)
                    self.forget(ws.DEMO_SLUG)
            except Exception as exc:  # noqa: BLE001 - the home page still offers the button
                print(f"ranksim: synthetic demo not built: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            finally:
                self._demo_checked = True

    def llm_ready(self) -> bool:
        from aeo.ranksim.llm import openrouter_key

        if self.public_demo:
            return False  # a public host never spends a provider key, whatever the environment holds
        settings = self.settings
        return bool(openrouter_key(settings) or settings.openai_api_key or settings.anthropic_api_key)


# ---------------------------------------------------------------------------
# Security middleware: Host check, body cap, headers
# ---------------------------------------------------------------------------


class _TooLarge(Exception):
    pass


class GuardMiddleware:
    def __init__(self, app, *, max_body: int, allowed_hosts: list[str] | None) -> None:
        self.app = app
        self.max_body = max_body
        names = [host.lower() for host in allowed_hosts] if allowed_hosts else None
        # IP_LITERALS lets any numeric address through (a LAN bind): DNS rebinding needs a hostname, so it stays blocked.
        self.ip_literals = bool(names) and IP_LITERALS in names
        self.allowed = {host for host in names if host != IP_LITERALS} if names else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        if self.allowed is not None:
            host = _host_only(headers.get(b"host", b"").decode("latin-1"))
            if host not in self.allowed and not (self.ip_literals and _is_ip(host)):
                await _send_plain(send, 400, "Unknown host. Open this tool at http://127.0.0.1 on the computer running it.")
                return
        path = scope.get("path") or ""
        csp = csp_for(path)
        started = {"value": False}
        secure_request = scope.get("scheme") == "https" or headers.get(b"x-forwarded-proto", b"").decode("latin-1").lower() == "https"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                started["value"] = True
                extra = [
                    (b"content-security-policy", csp.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                ]
                if secure_request:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                own = list(message.get("headers") or [])
                if not any(key.lower() == b"cache-control" for key, _value in own):
                    extra.append((b"cache-control", b"no-store"))  # a route that set its own (Studio assets) keeps it
                message = dict(message)
                message["headers"] = own + extra
            await send(message)

        method = scope.get("method")
        if method in WRITE_METHODS and headers.get(b"sec-fetch-site", b"").strip().lower() == b"cross-site":
            # A browser marks a request another site started. The CSRF token every write checks is the same for every
            # visitor (it is in the shell, which needs no login), so this refuses cross-site forms outright instead
            # of relying on the SameSite cookie alone. Servers (a cron, a webhook) send no Sec-Fetch-Site header.
            await _send_plain(send_with_headers, 403, "Cross-site requests are refused.")
            return
        if method in WRITE_METHODS:
            length = headers.get(b"content-length")
            if length is not None:
                try:
                    if int(length) > self.max_body:
                        await _send_plain(send_with_headers, 413, _too_large_text(self.max_body))
                        return
                except ValueError:
                    await _send_plain(send_with_headers, 400, "Bad Content-Length.")
                    return
            received = {"bytes": 0}

            async def limited():
                message = await receive()
                if message.get("type") == "http.request":
                    received["bytes"] += len(message.get("body") or b"")
                    if received["bytes"] > self.max_body:
                        raise _TooLarge()
                return message

            try:
                await self.app(scope, limited, send_with_headers)
            except _TooLarge:
                if not started["value"]:
                    await _send_plain(send_with_headers, 413, _too_large_text(self.max_body))
            return
        await self.app(scope, receive, send_with_headers)


def _too_large_text(limit: int) -> str:
    return f"Too large. Uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB (request limit {limit} bytes)."


async def _send_plain(send, status: int, text: str) -> None:
    body = text.encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _host_only(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.split(":", 1)[0]


IP_LITERALS = "*ip*"


def _is_ip(host: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def loopback_hosts(port: int | None = None) -> list[str]:
    """Host header names accepted when the server listens on loopback only (blocks DNS rebinding)."""
    return ["127.0.0.1", "localhost", "::1"]


def bound_hosts(bind: str, extra: list[str] | None = None) -> list[str]:
    """Host names accepted on a wider bind (for example 0.0.0.0): loopback names, any IP address, the bind address
    if it is a name, and names given with --allow-host. A rebinding attacker's own domain name is still refused."""
    names = loopback_hosts() + [IP_LITERALS]
    if bind and bind not in {"0.0.0.0", "::"}:
        names.append(_host_only(bind))
    names.extend(_host_only(name) for name in (extra or []) if name)
    return names


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------


def create_app(
    state: DemoState | None = None,
    *,
    settings: Settings | None = None,
    database: str | None = None,
    projects_root: str | Path | None = None,
    legacy_factory: Callable[[], DemoState] | None = None,
    allowed_hosts: list[str] | None = None,
    pull_options: dict | None = None,
    preview_fetch: Callable[[str], tuple[str, str]] | None = None,
    llm_factory: Callable[[], object] | None = None,
    max_upload_bytes: int = MAX_UPLOAD_BYTES,
    public_demo: bool | None = None,
    csrf_token: str | None = None,
    access_password: str | None = None,
    access_secret: str | None = None,
    cloud=None,
) -> FastAPI:
    """The web UI. Passing a DemoState positionally (the old API) serves the old demo at /demo and /check.

    public_demo (or RANKSIM_PUBLIC_DEMO=1) is for an unauthenticated public host: only the synthetic demo,
    no project creation, imports, pulls, competitor edits, historical SmartCrew data, or old /demo pages.
    """
    import os

    settings = settings or (state.settings if state is not None else Settings())
    if public_demo is None:
        public_demo = os.environ.get("RANKSIM_PUBLIC_DEMO", "").strip() in {"1", "true", "yes"}
    ui = UiState(
        settings=settings,
        database=database or settings.ranksim_database_url,
        projects_root=projects_root,
        legacy_state=state,
        legacy_factory=legacy_factory,
        pull_options=pull_options,
        preview_fetch=preview_fetch,
        llm_factory=llm_factory,
        max_upload_bytes=max_upload_bytes,
        public_demo=public_demo,
        csrf_token=csrf_token,
    )
    if public_demo:
        state = None
        ui._legacy = None
        ui._legacy_factory = None
        ui.llm_factory = lambda: None
    from aeo.ranksim.access import Access

    # A password turns on login for every page and API call (a hosted, private Ranksim). The public demo has none.
    ui.access = Access(None if public_demo else access_password, access_secret)
    ui.cloud = cloud
    if cloud is not None:
        cloud.on_change.append(lambda: ui._contexts.clear())
    app = FastAPI(title="Ranksim", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ui = ui
    app.add_middleware(GuardMiddleware, max_body=max_upload_bytes + FORM_OVERHEAD_BYTES, allowed_hosts=allowed_hosts)

    # -- static -------------------------------------------------------------

    @app.get("/static/app.css")
    def css() -> Response:
        return Response(APP_CSS, media_type="text/css")

    @app.get("/static/app.js")
    def js() -> Response:
        return Response(APP_JS, media_type="application/javascript")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "public_demo": ui.public_demo})

    # -- the three-screen workspace: /, /projects, /p/{slug}, comparisons, draft ----------

    from aeo.ranksim import workspace_ui

    if ui.public_demo:
        from aeo.ranksim.studio import NO_STATE_PREFIX, OPEN_EXACT, OPEN_PREFIX

        @app.middleware("http")
        async def _demo_on_first_use(request: Request, call_next):
            # Every public instance builds its own copy of the synthetic demo, on the first request that may read it.
            # The shell, the session and the assets never do, so a cold start answers them without training.
            path = request.url.path
            if not ui._demo_checked and not (path in OPEN_EXACT or path.startswith(OPEN_PREFIX + NO_STATE_PREFIX)):
                await run_in_threadpool(ui.ensure_demo)
            return await call_next(request)

        public_blocked = re.compile(r"^/(api/jobs|p/[^/]+/(advanced|test|results|versions|compare|engines|jobs|searches|import|pull)(/|$|\.))")

        @app.middleware("http")
        async def _public_only_workspace(request: Request, call_next):
            # The older pages store and list anonymous pasted pages and start background jobs. Not on a public host.
            if public_blocked.match(request.url.path):
                return _html_page("Not available", _message_body("Not available on the public demo. Run Ranksim locally for the full tool."), ui, status=404)
            return await call_next(request)

    from aeo.ranksim import studio

    studio.register(app, ui)  # the Studio owns / and /app; the classic screens keep their paths
    workspace_ui.register(app, ui)

    # -- the full project page (pull log, keys, page tester), kept as Advanced ---------

    @app.get("/p/{slug}/advanced", response_class=HTMLResponse)
    def project_page(slug: str, job: str = "", msg: str = "") -> Response:
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        running = ui.jobs.get(job) if job else ui.jobs.active(slug, "pull")
        body = _project_body(ui, project, running, message=msg) + _keys_box(ui)
        return _html_page(project.name, body, ui, crumbs=[(project.name, f"/p/{slug}"), ("Advanced", None)])

    @app.post("/p/{slug}/pull")
    async def start_pull(slug: str, request: Request) -> Response:
        form = await request.form()
        _check_csrf(ui, form)
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        if ui.public_demo:
            return _html_page("Pull", _message_body("This public demo does not fetch pages. Run Ranksim locally to use your own data."), ui, status=403)
        existing = ui.jobs.active(slug, "pull")
        if existing is not None:
            return RedirectResponse(f"/p/{slug}?job={existing.id}", status_code=303)
        job = ui.jobs.start("pull", slug, f"Pull for {project.name}", lambda job: _pull_work(ui, slug, job))
        return RedirectResponse(f"/p/{slug}?job={job.id}", status_code=303)

    @app.post("/p/{slug}/test")
    async def start_test(slug: str, request: Request) -> Response:
        try:
            form = await request.form(max_files=2, max_fields=20, max_part_size=ui.max_upload_bytes + 1024)
        except _TooLarge:
            return _html_page("Test a page", _message_body("Too large. Uploads are limited to 5 MB."), ui, status=413)
        except Exception:  # noqa: BLE001 - a malformed form
            return _html_page("Test a page", _message_body("That form could not be read. Uploads are limited to 5 MB."), ui, status=400)
        _check_csrf(ui, form)
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        try:
            source = await _read_test_source(ui, project, form)
        except _InputError as exc:
            status = 413 if exc.too_large else 400
            return _html_page(project.name, _project_body(ui, project, None, error=str(exc)), ui, crumbs=[(project.name, None)], status=status)
        if ui.public_demo and source.get("kind") == "preview":
            return _html_page(project.name, _message_body("This public demo does not fetch preview URLs."), ui, status=403)
        simulate = bool(form.get("simulate")) and ui.llm_ready()
        job = ui.jobs.start("test", slug, f"Test of {source['url']}", lambda job: _test_work(ui, slug, source, simulate, job))
        return RedirectResponse(f"/p/{slug}/jobs/{job.id}", status_code=303)

    @app.get("/p/{slug}/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(slug: str, job_id: str) -> Response:
        job = ui.jobs.get(job_id)
        if job is None or job.slug != slug:
            return _not_found(ui)
        if job.status == "done" and job.result_url:
            return RedirectResponse(job.result_url, status_code=303)
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        return _html_page(job.title, _job_body(ui, project, job), ui, crumbs=[(project.name, f"/p/{slug}"), ("Working", None)])

    @app.get("/api/jobs/{job_id}")
    def job_api(job_id: str) -> JSONResponse:
        job = ui.jobs.get(job_id)
        if job is None:
            return JSONResponse({"error": "no such job"}, status_code=404)
        return JSONResponse(ui.jobs.as_dict(job))

    @app.get("/p/{slug}/results/{rid}.json")
    def result_json(slug: str, rid: str) -> Response:
        data = _load_result(ui, slug, rid)
        if data is None:
            return JSONResponse({"error": "no such result"}, status_code=404)
        return JSONResponse(data)

    @app.get("/p/{slug}/results/{rid}", response_class=HTMLResponse)
    def result_page(slug: str, rid: str) -> Response:
        data = _load_result(ui, slug, rid)
        if data is None:
            return _not_found(ui)
        project = ui.project(slug)
        body = _board_body(ui, project, data)
        return _html_page(f"Results for {data.get('url', '')}", body, ui, crumbs=[(project.name, f"/p/{slug}"), (f"Result {data.get('version', '')}", None)])

    @app.get("/p/{slug}/versions", response_class=HTMLResponse)
    def versions_page(slug: str, url: str = "") -> Response:
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        return _html_page("Versions", _versions_body(ui, project, url), ui, crumbs=[(project.name, f"/p/{slug}"), ("Versions", None)])

    @app.get("/p/{slug}/compare", response_class=HTMLResponse)
    def compare_page(slug: str, url: str = "", a: str = "", b: str = "") -> Response:
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        body = _compare_body(ui, project, url, a, b)
        return _html_page("Compare versions", body, ui, crumbs=[(project.name, f"/p/{slug}"), ("Versions", f"/p/{slug}/versions?url={quote(url, safe='')}"), ("Compare", None)])

    @app.get("/p/{slug}/engines", response_class=HTMLResponse)
    def engines_page(slug: str) -> Response:
        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        return _html_page("AI engines", _engines_body(ui, project), ui, crumbs=[(project.name, f"/p/{slug}"), ("AI engines", None)])

    @app.post("/p/{slug}/searches")
    async def update_searches_route(slug: str, request: Request) -> Response:
        form = await request.form()
        _check_csrf(ui, form)
        from aeo.ranksim.project import ProjectError, split_searches, update_searches

        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        if ui.public_demo:
            return _html_page(project.name, _message_body("This public demo is read-only."), ui, status=403)
        searches = split_searches(str(form.get("searches") or ""))
        if len(searches) > MAX_SEARCHES:
            return _html_page(project.name, _project_body(ui, project, None, error=f"At most {MAX_SEARCHES} searches."), ui, status=400)
        # Never wait for the project lock on the event loop: a running pull would freeze every page.
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return _busy(ui, project)
        try:
            await run_in_threadpool(update_searches, project, replace=searches)
        except ProjectError as exc:
            return _html_page(project.name, _project_body(ui, project, None, error=str(exc)), ui, status=400)
        finally:
            lock.release()
        ui.forget(slug)
        return RedirectResponse(f"/p/{slug}?msg=searches", status_code=303)

    @app.post("/p/{slug}/import")
    async def import_route(slug: str, request: Request) -> Response:
        try:
            form = await request.form(max_files=1, max_fields=10, max_part_size=64 * 1024)
        except _TooLarge:
            return _html_page("Import", _message_body("Too large. Uploads are limited to 5 MB."), ui, status=413)
        except Exception:  # noqa: BLE001 - a malformed form
            return _html_page("Import", _message_body("That form could not be read. Uploads are limited to 5 MB."), ui, status=400)
        _check_csrf(ui, form)
        from aeo.ranksim import workspace as ws
        from aeo.ranksim import workspace_ui

        try:
            project = ui.project(slug)
        except LookupError:
            return _not_found(ui)
        meta = ws.load_meta(project)

        def setup_error(text: str, status: int, check=None) -> HTMLResponse:
            body = workspace_ui.setup_body(ui, project, meta, error=text, csv_check=check)
            return workspace_ui.shell(ui, f"Setup: {project.name}", body, project=project, meta=meta, tab="setup", status=status)

        if ui.public_demo:
            return setup_error("This public demo does not import files.", 403)
        lock = ui.jobs.project_lock(slug)
        if not lock.acquire(blocking=False):
            return setup_error(BUSY_TEXT, 409)
        try:
            report = await _import_csv(ui, project, form)
        except _CsvRejected as exc:
            return setup_error("", 400, exc.check)
        except _InputError as exc:
            return setup_error(str(exc), 413 if exc.too_large else 400)
        finally:
            lock.release()
        if meta.dataset in {ws.DATASET_NONE, ws.DATASET_OBSERVED}:
            meta.dataset = ws.DATASET_OBSERVED
            meta.dataset_label = report.source
            ws.save_meta(project, meta)
        ui.forget(slug)
        return RedirectResponse(f"/p/{slug}?msg=imported-{report.rows}", status_code=303)

    # -- the old demo -------------------------------------------------------

    @app.get("/demo", response_class=HTMLResponse)
    def legacy_home(demo: int = 0) -> Response:
        if ui.public_demo:
            return _html_page("Old demo", _message_body("Not available on this public demo."), ui, status=404)
        legacy = ui.legacy()
        if legacy is None:
            return _html_page("Old demo", _message_body("The old single-query demo is not configured here. Use a project instead."), ui, status=404)
        if not demo:
            return legacy.page(selected=SAMPLE_QUERY, url_value=SAMPLE_URL, html_value="")
        result = check_draft(
            query=SAMPLE_QUERY,
            html=legacy.sample_html(),
            url=SAMPLE_URL,
            labels=legacy.labels,
            pages=legacy.pages,
            model=legacy.fit.models.get(SAMPLE_QUERY),
            calibration=legacy.fit.calibration,
            today=legacy.today,
            intent=legacy.intent_for.get(SAMPLE_QUERY, "comparison"),
            llm=legacy.llm,
            run_ai=True,
        )
        return legacy.page(selected=SAMPLE_QUERY, url_value=SAMPLE_URL, html_value="", result=result, error=result.error)

    @app.get("/space", response_class=HTMLResponse)
    def legacy_space() -> Response:
        if ui.public_demo:
            return RedirectResponse("/", status_code=303)
        legacy = ui.legacy()
        if legacy is None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(render_space_page(legacy.space, legacy.llm_note()))

    @app.get("/demo/space")
    def legacy_space_alias() -> Response:
        return RedirectResponse("/space", status_code=303)

    @app.get("/check")
    def legacy_check_get() -> Response:
        return RedirectResponse("/demo", status_code=303)

    @app.post("/check", response_class=HTMLResponse)
    def legacy_check(
        query: str = Form(""), url: str = Form(""), html: str = Form(""), ai: str = Form(""), csrf: str = Form("")
    ) -> Response:
        legacy = ui.legacy()
        if legacy is None:
            return RedirectResponse("/", status_code=303)
        # Same CSRF rule as every project form: a cross-site post must not start fetches or paid LLM calls.
        _check_csrf(ui, {"csrf": csrf})
        return _legacy_check(legacy, query, url, html, ai)

    return app


class _InputError(ValueError):
    def __init__(self, text: str, *, too_large: bool = False) -> None:
        super().__init__(text)
        self.too_large = too_large


class _CsvRejected(_InputError):
    """The CSV failed the line-by-line check. Nothing was written or imported."""

    def __init__(self, check) -> None:
        super().__init__("The CSV was not imported. " + " ".join(check.errors[:3]))
        self.check = check


def _check_csrf(ui: UiState, form) -> None:
    from fastapi import HTTPException

    token = str(form.get("csrf") or "")
    if not token or not secrets.compare_digest(token, ui.csrf):
        raise HTTPException(status_code=403, detail="This form has expired. Reload the page and try again.")


async def _read_upload(ui: UiState, upload, what: str) -> bytes:
    data = await upload.read(ui.max_upload_bytes + 1)
    if len(data) > ui.max_upload_bytes:
        raise _InputError(f"That {what} is over 5 MB. Uploads are limited to 5 MB.", too_large=True)
    return data


def _decode_html(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise _InputError("That file is not HTML text.")
    return data.decode("utf-8", errors="replace")


def _clean_url(value: str, *, what: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) > MAX_URL_CHARS:
        raise _InputError(f"The {what} is too long.")
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise _InputError(f"The {what} must start with http:// or https://.")
    return text


async def _read_test_source(ui: UiState, project, form) -> dict:
    """Exactly one of: an uploaded .html file, pasted HTML, a preview URL. Plus the URL the page will live at."""
    from starlette.datastructures import UploadFile

    upload = form.get("html_file")
    pasted = str(form.get("html_text") or "")
    preview = _clean_url(_with_scheme(str(form.get("preview_url") or "")), what="preview URL")
    target = _clean_url(_with_scheme(str(form.get("url") or "")), what="page URL")
    chosen: dict = {}
    if isinstance(upload, UploadFile) and (upload.filename or ""):
        name = Path(upload.filename or "page.html").name
        if not name.lower().endswith((".html", ".htm")):
            raise _InputError("Upload an .html or .htm file.")
        data = await _read_upload(ui, upload, "file")
        if not data.strip():
            raise _InputError("That file is empty.")
        chosen = {"kind": "upload", "html": _decode_html(data), "label": f"upload:{_safe_name(name)}"}
    if pasted.strip():
        if chosen:
            raise _InputError("Give one page: upload a file, paste HTML, or give a preview URL. Not two.")
        if len(pasted.encode("utf-8")) > ui.max_upload_bytes:
            raise _InputError("The pasted HTML is over 5 MB.", too_large=True)
        chosen = {"kind": "paste", "html": pasted, "label": "pasted HTML"}
    if preview:
        if chosen:
            raise _InputError("Give one page: upload a file, paste HTML, or give a preview URL. Not two.")
        chosen = {"kind": "preview", "preview_url": preview, "html": "", "label": preview}
    if not chosen:
        raise _InputError("Give a page to test: upload an .html file, paste HTML, or give a preview URL.")
    url = target or (preview if preview and project.is_our_url(preview) else "")
    note = ""
    if not url and chosen.get("kind") == "preview":
        # A staging preview: test it as the page it previews would be, but say so plainly.
        url = f"https://{project.site}/new-page/"
        note = f"No page URL was given, so the preview was tested as a new page at {url}. Give the real URL to keep its versions together."
    if not url:
        raise _InputError(
            f"Say where the page lives or will live, for example https://{project.site}/your-page/. "
            "The URL decides which REAL result it replaces and which versions it is compared with."
        )
    chosen["url"] = url
    chosen["note"] = note
    return chosen


def _with_scheme(value: str) -> str:
    """'www.example.com/page' becomes 'https://www.example.com/page'. Other schemes are left for _clean_url to refuse."""
    text = (value or "").strip()
    if text and "://" not in text and not text.lower().startswith(("javascript:", "data:", "file:", "mailto:")):
        if re.match(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?(/|$)", text):
            return "https://" + text
    return text


BUSY_TEXT = "A pull or a test is running for this project right now. Try again when it finishes; nothing was changed."


def _busy(ui: UiState, project) -> HTMLResponse:
    running = ui.jobs.active(project.slug)
    return _html_page(project.name, _project_body(ui, project, running, error=BUSY_TEXT), ui, crumbs=[(project.name, None)], status=409)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "page.html"


async def _import_csv(ui: UiState, project, form):
    from starlette.datastructures import UploadFile

    from aeo.ranksim.project import PULL_DATE_NOT_RECORDED, ProjectError, import_serp_csv

    upload = form.get("csv_file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise _InputError("Choose a CSV file with the columns query, url, position.")
    name = _safe_name(Path(upload.filename).name)
    if not name.lower().endswith(".csv"):
        raise _InputError("Upload a .csv file with the columns query, url, position.")
    data = await _read_upload(ui, upload, "CSV")
    from aeo.ranksim.workspace import check_csv

    check = check_csv(data)
    if not check.ok:
        raise _CsvRejected(check)
    snapshot = str(form.get("snapshot_date") or "").strip() or None
    if snapshot and not re.match(r"^\d{4}-\d{2}-\d{2}$", snapshot):
        raise _InputError("The date must be YYYY-MM-DD.")
    folder = project.dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"{stamp}-{name}"
    path.write_bytes(data)
    when = f"Google positions seen {snapshot}" if snapshot else PULL_DATE_NOT_RECORDED
    label = f"uploaded file {name} ({when})"
    # The caller holds the project lock. The import reads and writes files, so it runs off the event loop.
    try:
        report = await run_in_threadpool(import_serp_csv, project, path, snapshot, label, add_searches=bool(form.get("add_searches")))
    except (ProjectError, ValueError, KeyError) as exc:
        raise _InputError(f"Import failed: {exc}") from exc
    from aeo.ranksim import workspace as ws

    # Record only a later dated file on an already-observed project. The dataset
    # flip to observed happens after this returns, so a first file on a none project is not an outcome.
    ws.record_observed_import(ui.database, project, ws.load_meta(project), data, name, snapshot, public_demo=ui.public_demo)
    return report


# ---------------------------------------------------------------------------
# Work done in background threads
# ---------------------------------------------------------------------------


def _pull_work(ui: UiState, slug: str, job: Job) -> str:
    from aeo.ranksim.project import pull

    project = ui.project(slug)

    def progress(event) -> None:
        ui.jobs.event(job, event.step, event.message, event.done, event.total, event.seconds)

    from aeo.errors import ProviderConfigError
    from aeo.ranksim.project import GOOGLE_KEYS

    options = {"engines": None, "engine_runs": 1, **ui.pull_options}
    from aeo.ranksim import workspace as ws

    meta = ws.load_meta(project)
    if meta.dataset == ws.DATASET_SYNTHETIC:
        # A synthetic project never touches the network: its pages come from the fixture.
        options.update({"fetcher": ws.synthetic_fetcher, "skip_google": True, "engines": [], "fresh_days": 0})
    elif meta.dataset == ws.DATASET_HISTORICAL:
        options.update({"fetcher": ws.offline_fetcher, "skip_google": True, "engines": [], "fresh_days": 36500})
    try:
        report = pull(project, ui.settings, database=ui.database, progress=progress, **options)
    except ProviderConfigError as exc:
        if "No REAL Google results" not in str(exc):
            raise
        # The CLI text names a terminal command; on the web, point at the form on this page.
        raise ProviderConfigError(
            f"No REAL Google results for {project.name} yet, and no other project here has results for these searches. "
            f"Asking Google needs {GOOGLE_KEYS} (set before starting the server). Or import a CSV of Google positions "
            "(query, url, position) with 'Import Google positions from a CSV' under Key searches on this page. Nothing was invented."
        ) from exc
    ui.forget(slug)
    from aeo.ranksim import plugins
    from aeo.ranksim.api.common import _pull_summary

    plugins.call_all("after_pull", project=project, report=_pull_summary(report), ui=ui)
    job.timings = [asdict(step) for step in report.all_steps()]
    ui.jobs.event(job, "done", f"Pull {report.pull_id} finished in {report.total_seconds:.1f} s.", 1, 1)
    return f"/p/{slug}?pulled={report.pull_id}"


def _test_work(ui: UiState, slug: str, source: dict, simulate: bool, job: Job) -> str:
    from aeo.ranksim.engines import NO_LLM_STATUS
    from aeo.ranksim.project import test_page
    from aeo.ranksim.project_engines import SIM_OFF_STATUS

    started = time.perf_counter()
    html = source.get("html") or ""
    label = source.get("label") or ""
    if source.get("kind") == "preview":
        ui.jobs.event(job, "fetch", f"Fetching {source['preview_url']} (robots.txt respected, private addresses refused).", 0, 1)
        html, error = ui.preview_fetch(source["preview_url"])
        if not html:
            raise AeoError(f"Could not fetch the preview URL: {error}. No page text was invented.")
        ui.jobs.event(job, "fetch", f"Fetched {len(html):,} characters.", 1, 1)
    ui.jobs.event(job, "load", "Loading the mini search engine.", 0, 1)
    ctx = ui.context(slug)
    ui.jobs.event(job, "load", f"Loaded ({ctx.loaded_from}).", 1, 1)
    total = len(ctx.project.searches)
    ui.jobs.event(job, "score", f"Scoring your page on {total} searches.", 0, total)
    llm = ui.llm_factory() if simulate else None

    def sim_progress(message: str, done: int, total_jobs: int) -> None:
        ui.jobs.event(job, "simulate", message, done, total_jobs)

    sim_off = SIM_OFF_STATUS if ui.llm_ready() else ""
    board = test_page(ctx, html, source["url"], source=label, llm=llm, settings=ui.settings, progress=sim_progress, sim_off=sim_off)
    ui.jobs.event(job, "score", f"Scored {total} searches.", total, total)
    if not simulate:
        ui.jobs.event(job, "simulate", sim_off or NO_LLM_STATUS, 1, 1)
    if source.get("note"):
        board.notes.insert(0, source["note"])
    board.timing["wall_seconds"] = round(time.perf_counter() - started, 2)
    rid = save_result(ctx.project, board)
    ui.jobs.event(job, "save", f"Saved as {board.version}.", 1, 1)
    job.timings = [{"step": key, "seconds": value} for key, value in board.timing.items()]
    return f"/p/{slug}/results/{rid}"


def save_result(project, board) -> str:
    """Write the board JSON under data/projects/<slug>/results/ and index it."""
    folder = project.dir / "results"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}-{(board.content_hash or '0' * 8)[:8]}"
    rid = base
    counter = 2
    while (folder / f"{rid}.json").exists():
        rid = f"{base}-{counter}"
        counter += 1
    data = board.to_dict()
    data["result_id"] = rid
    (folder / f"{rid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    line = {
        "result_id": rid,
        "url": board.url,
        "version": board.version,
        "content_hash": board.content_hash,
        "tested_at": board.tested_at,
        "summary": board.summary.text,
    }
    with (folder / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return rid


def results_index(project) -> list[dict]:
    path = project.dir / "results" / "index.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and RESULT_RE.match(str(item.get("result_id") or "")):
            rows.append(item)
    return rows


def _load_result(ui: UiState, slug: str, rid: str) -> dict | None:
    if not RESULT_RE.match(rid or ""):
        return None
    try:
        project = ui.project(slug)
    except LookupError:
        return None
    path = project.dir / "results" / f"{rid}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def e(value) -> str:
    """Escape anything for HTML text and attribute values."""
    return _html.escape("" if value is None else _plain(str(value)), quote=True)


def ext_link(url: str, text: str | None = None) -> str:
    """A link to a crawled or pulled URL, only for http(s). Anything else is shown as text."""
    label = e(text if text is not None else url)
    try:
        scheme = urlsplit(str(url or "")).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme not in {"http", "https"}:
        return label
    return f'<a href="{e(url)}" rel="noopener noreferrer nofollow" target="_blank">{label}</a>'


def tag(kind: str, text: str | None = None) -> str:
    css = {"REAL": "real", "PREDICTED": "pred"}.get(kind, "plain")
    return f'<span class="tag {css}">{e(text or kind)}</span>'


def _html_page(title: str, body: str, ui: UiState, *, crumbs: list[tuple[str, str | None]] | None = None, status: int = 200) -> HTMLResponse:
    """The older pages (advanced, results, versions, engines, jobs) in the workspace shell, with the project's tabs and data label."""
    from aeo.ranksim import workspace as ws
    from aeo.ranksim import workspace_ui

    project = meta = None
    for _label, href in crumbs or []:
        found = re.match(r"^/p/([a-z0-9][a-z0-9-]{0,59})$", href or "")
        if found:
            try:
                project = ui.project(found.group(1))
                meta = ws.load_meta(project)
            except LookupError:
                project = meta = None
            break
    if meta is not None and meta.synthetic:
        body = synthetic_wording(body)
    return workspace_ui.shell(ui, title, body, project=project, meta=meta, crumbs=crumbs or [], status=status, wide=False)


def synthetic_wording(body: str) -> str:
    """Older pages say REAL for imported positions. In a synthetic project nothing is real, so say SYNTHETIC."""
    body = body.replace('<span class="tag real">', '<span class="tag tag-synth">')
    body = re.sub(r"\bREAL Google\b", "SYNTHETIC (not Google)", body)
    body = re.sub(r"\bREAL\b", "SYNTHETIC", body)
    return body.replace("SYNTHETIC</span> Google results", "SYNTHETIC</span> results").replace("<h2>Google data</h2>", "<h2>Positions (synthetic)</h2>")


def _message_body(text: str) -> str:
    return f'<section class="card"><p>{e(text)}</p><p><a href="/projects">Back to projects</a></p></section>'


def _not_found(ui: UiState) -> HTMLResponse:
    return _html_page("Not found", _message_body("Nothing here. The project or result may have been removed."), ui, status=404)


def _csrf_input(ui: UiState) -> str:
    return f'<input type="hidden" name="csrf" value="{e(ui.csrf)}">'


def _google_ready(settings: Settings) -> bool:
    return bool(settings.serpapi_key or settings.serper_api_key or (settings.dataforseo_login and settings.dataforseo_password))


def _keys_box(ui: UiState) -> str:
    from aeo.ranksim.engines import engine_availability
    from aeo.ranksim.project import NOT_RUN_GOOGLE

    settings = ui.settings
    google = []
    if settings.serpapi_key:
        google.append("SerpAPI")
    if settings.serper_api_key:
        google.append("Serper")
    if settings.dataforseo_login and settings.dataforseo_password:
        google.append("DataForSEO")
    rows = [
        "<li><b>Google results</b>: "
        + (f"ready ({e(', '.join(google))})" if google else f'<span class="muted">{e(NOT_RUN_GOOGLE)}</span>. You can import a CSV of Google positions instead.')
        + "</li>"
    ]
    for row in engine_availability(settings):
        state = "ready" if row.configured else f'<span class="muted">{e(row.status)}</span>'
        rows.append(f"<li><b>{e(row.name)}</b>: {state}</li>")
    rows.append(
        "<li><b>AI answer simulation</b>: "
        + ("ready" if ui.llm_ready() else '<span class="muted">not run: needs OPENROUTER_API_KEY, OPENAI_API_KEY or ANTHROPIC_API_KEY</span>')
        + "</li>"
    )
    return (
        '<section class="card"><h2>Keys on this computer</h2><p class="muted small">Set as environment variables before starting the server. '
        "Their values are never shown.</p><ul class=\"keys\">" + "".join(rows) + "</ul></section>"
    )


def _home_body(ui: UiState, *, error: str = "", values=None) -> str:
    from aeo.ranksim.project import list_projects

    values = values or {}
    parts = [
        '<section class="hero"><h1>Test a page against the REAL Google top 10 before you publish</h1>',
        "<p class=\"lead\">A project is your site plus the searches you want to win. One click stores the REAL Google top 10 for every "
        "search. Then upload a page and see, in seconds, whether it is on topic for each search, where your site would PREDICTED slot in, "
        "what the pages above you have, and what the AI engines cite. Every guess is labeled PREDICTED, with its measured accuracy.</p></section>",
    ]
    if error:
        parts.append(f'<p class="alert bad" role="alert">{e(error)}</p>')
    if not _google_ready(ui.settings):
        parts.append(
            '<p class="alert warn" role="status">No Google key is set on this computer, so a new project cannot ask Google itself. '
            "It can still start: searches that another project here already has REAL results for are reused (with their source and pull date), "
            "and on the project page you can import a CSV of Google positions (query, url, position).</p>"
        )
    rows = list_projects(root=ui.root)
    if rows:
        cards = []
        for row in rows:
            cards.append(
                '<li class="card project-card">'
                f'<h3><a href="/p/{e(row.slug)}">{e(row.name)}</a></h3>'
                f'<p class="muted">{e(row.site)}, {row.searches} key searches</p>'
                + (f"<p class=\"small\">{tag('REAL')} Google results: {e(row.latest_google)}</p>" if row.latest_google else '<p class="small muted">No REAL Google results yet.</p>')
                + f'<p><a class="button" href="/p/{e(row.slug)}">Open</a></p></li>'
            )
        parts.append('<section><h2>Your projects</h2><ul class="cards" role="list">' + "".join(cards) + "</ul></section>")
    else:
        parts.append('<section class="card"><p>No projects yet. Create one below.</p></section>')
    parts.append(
        '<section class="card"><h2>New project</h2>'
        '<form method="post" action="/projects" class="stack">'
        + _csrf_input(ui)
        + f'<label>Name<input name="name" required maxlength="80" value="{e(values.get("name", ""))}" placeholder="SmartCrew"></label>'
        + f'<label>Your site<input name="site" required maxlength="200" value="{e(values.get("site", ""))}" placeholder="www.example.com"></label>'
        + '<label>Key searches, one per line<textarea name="searches" rows="6" required placeholder="all in one dirt work software&#10;hcss alternative">'
        + e(values.get("searches", ""))
        + "</textarea></label>"
        + '<button type="submit" class="primary">Create project</button></form></section>'
    )
    parts.append(_keys_box(ui))
    return "".join(parts)


def _job_panel(ui: UiState, job, *, redirect: bool) -> str:
    data = ui.jobs.as_dict(job)
    steps = "".join(_step_row(item) for item in data["steps"])
    status = {"queued": "Waiting", "running": "Running", "done": "Done", "error": "Failed"}.get(data["status"], data["status"])
    error = f'<p class="alert bad" data-error>{e(data["error"])}</p>' if data["error"] else '<p class="alert bad" data-error hidden></p>'
    return (
        f'<section class="card job" data-job="{e(job.id)}" data-redirect="{"1" if redirect else "0"}" aria-live="polite">'
        f'<h2><span data-status>{e(status)}</span>: {e(job.title)}</h2>'
        f'<p class="muted"><span data-elapsed>{data["elapsed"]:.1f}</span> s. <span data-message>{e(data["message"])}</span></p>'
        f'<div class="steps" data-steps>{steps}</div>{error}'
        '<noscript><p>This page updates itself with JavaScript. Reload it to see progress.</p></noscript>'
        "</section>"
    )


def _step_row(item: dict) -> str:
    total = int(item.get("total") or 0)
    done = int(item.get("done") or 0)
    bar = f'<progress max="{max(total, 1)}" value="{min(done, max(total, 1)) if total else 0}"></progress>'
    count = f"{done} of {total}" if total else ""
    if item.get("skipped"):
        bar = '<p class="small skipped">not run</p>'
        count = ""
    return (
        f'<div class="step" data-step="{e(item.get("step"))}"><div class="step-head"><b>{e(item.get("label"))}</b>'
        f'<span class="muted small">{e(count)} {e(item.get("seconds"))} s</span></div>{bar}'
        f'<p class="small muted step-msg">{e(item.get("message"))}</p></div>'
    )


def _project_body(ui: UiState, project, running, *, error: str = "", message: str = "") -> str:
    from aeo.ranksim.engines import engine_availability
    from aeo.ranksim.project import NOT_RUN_GOOGLE, project_status

    status = project_status(project)
    parts = [f'<section class="hero"><h1>{e(project.name)}</h1><p class="lead">{e(project.site)}, {len(project.searches)} key searches.</p></section>']
    if error:
        parts.append(f'<p class="alert bad" role="alert">{e(error)}</p>')
    if message == "searches":
        parts.append('<p class="alert good">Searches saved. New searches get REAL results on the next pull.</p>')
    elif message.startswith("imported-"):
        parts.append(f'<p class="alert good">Imported {e(message.split("-", 1)[1])} REAL Google results. Press Pull now to store the pages and train.</p>')

    # Pull
    # A pull still running has a folder but nothing written yet: show the newest finished one.
    last = next((item for item in reversed(status.pulls) if item.written_at and item.finished), None)
    asked = next((item for item in reversed(status.pulls) if item.finished and item.google_kind == "api"), None)
    report = status.report
    pull_lines = []
    if last is not None:
        pull_lines.append(f"<p>Last pull: <b>{e(_stamp(last.written_at))}</b> ({e(last.what)}).</p>")
        if asked is None:
            pull_lines.append('<p class="small muted">Google itself has never been asked from this app for this project: no Google key is set.</p>')
        elif asked is not last:
            pull_lines.append(f'<p class="small muted">Google was last asked in pull {e(asked.pull_id)} ({e(_stamp(asked.written_at))}).</p>')
    if status.google is not None:
        entries = [item for item in status.google.searches if item.results]
        sources = sorted({item.source for item in entries if item.source})
        pulled = sorted({item.pulled_label for item in entries})
        pull_lines.append(
            f"<p>{tag('REAL')} Google results: {e(_source_and_date('; '.join(sources) or 'unknown source', ', '.join(pulled)))}.</p>"
        )
    else:
        pull_lines.append(f'<p class="muted">No REAL Google results yet. Google: {e(NOT_RUN_GOOGLE)}. Import a CSV below, or set a key.</p>')
    if report is not None and report.kind == "pull":
        timing = "".join(
            f"<tr><td>{e(STEP_LABELS.get(step.step, step.step))}</td><td class=\"num\">{step.seconds:.1f} s</td></tr>" for step in report.all_steps()
        )
        pull_lines.append(
            f'<details><summary>Step timings of pull {e(report.pull_id)}: {report.total_seconds:.1f} s in total</summary>'
            f'<table class="mini"><tbody>{timing}</tbody></table>'
            f"<p class=\"small muted\">{e(_count_text(report.counts))}</p>"
            + (f'<p class="small muted">{e(report.engines_note)}</p>' if report.engines_note else "")
            + "</details>"
        )
    pull_form = (
        f'<form method="post" action="/p/{e(project.slug)}/pull" class="pull-form" data-busy="Pulling...">{_csrf_input(ui)}'
        '<button type="submit" class="big primary">Pull now</button></form>'
        + (
            '<p class="small muted">Gets the REAL Google top 10 for every search, stores every result page, asks the AI engines that have keys, '
            "and retrains the mini search engine. Usually under a minute when the pages are already stored.</p>"
            if _google_ready(ui.settings)
            else '<p class="small muted">No Google key is set, so Pull now does not ask Google: it reuses the REAL results already saved '
            "(or another project's results for the same searches), refreshes stale pages and retrains only when something changed. "
            "To get fresh Google results, set SERPAPI_KEY, SERPER_API_KEY, or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD, or import a CSV below.</p>"
        )
    )
    job_html = _job_panel(ui, running, redirect=False) if running is not None and running.kind == "pull" and running.slug == project.slug else ""
    parts.append(f'<section class="card"><h2>Google data</h2>{"".join(pull_lines)}{pull_form}{job_html}</section>')

    # Test a page
    parts.append(_test_form(ui, project, enabled=status.google is not None))

    # Accuracy
    accuracy = []
    if status.site_backtest is not None:
        accuracy.append(f"<p>{tag('PREDICTED', 'PREDICTED slot')} {e(status.site_backtest.text)}</p>")
    if report is not None and report.metrics is not None:
        accuracy.append(f'<p class="small muted">Content model (reads the page text, unvalidated): {e(report.metrics.text)}</p>')
    if accuracy:
        parts.append('<section class="card"><h2>How accurate are the predictions?</h2>' + "".join(accuracy) + "</section>")

    # Searches and coverage
    rows = []
    for search in project.searches:
        info = status.coverage.get(search) or {}
        stored = info.get("stored")
        note = info.get("error") or ""
        rows.append(
            "<tr>"
            f'<td>{e(search)}</td><td class="hide-sm">{e(project.intents.get(search, ""))}</td>'
            f'<td class="num">{e(info.get("results", 0))}</td><td class="num">{e("n/a" if stored is None else stored)}</td>'
            f'<td class="hide-sm small">{e(info.get("pulled") or "n/a")}</td><td class="small muted">{e(note)}</td></tr>'
        )
    parts.append(
        '<section class="card"><h2>Key searches</h2>'
        f"<p class=\"small muted\">REAL results = how many Google results we have for the search. Stored = how many of those pages we hold a copy of (the mini search engine can only read stored pages).</p>"
        '<div class="table-scroll"><table class="grid"><thead><tr><th>Search</th><th class="hide-sm">Intent</th><th class="num">REAL results</th><th class="num">Stored</th>'
        '<th class="hide-sm">Pulled</th><th>Note</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
        f'<details><summary>Edit searches</summary><form method="post" action="/p/{e(project.slug)}/searches" class="stack">{_csrf_input(ui)}'
        f'<label>One per line<textarea name="searches" rows="8">{e(chr(10).join(project.searches))}</textarea></label>'
        '<button type="submit">Save searches</button></form></details>'
        f'<details><summary>Import Google positions from a CSV</summary><form method="post" action="/p/{e(project.slug)}/import" enctype="multipart/form-data" class="stack">{_csrf_input(ui)}'
        '<p class="small muted">Columns: query, url, position. Rows are REAL Google data you already have. Without a date they are labeled "pull date not recorded".</p>'
        '<label>CSV file<input type="file" name="csv_file" accept=".csv,text/csv" required></label>'
        '<label>Day the positions were seen (optional)<input type="date" name="snapshot_date"></label>'
        '<label class="check"><input type="checkbox" name="add_searches" value="1"> Add searches in the file that the project does not have</label>'
        '<button type="submit">Import</button></form></details>'
        "</section>"
    )

    # AI engines
    engines = engine_availability(ui.settings)
    lines = "".join(
        f"<li><b>{e(row.name)}</b>: {'ready' if row.configured else '<span class=muted>' + e(row.status) + '</span>'}</li>" for row in engines
    )
    parts.append(
        f'<section class="card"><h2>AI engines</h2><ul class="keys">{lines}<li><b>Bing Copilot</b>: <span class="muted">not covered, no public API returns its citations</span></li></ul>'
        f'<p><a href="/p/{e(project.slug)}/engines">See the newest REAL AI citations per search</a></p></section>'
    )

    # Tested pages
    parts.append(_tested_pages(project))
    return "".join(parts)


def _count_text(counts: dict) -> str:
    names = {
        "stored": "already stored",
        "fetched_now": "fetched now",
        "kept_stored": "kept an older copy",
        "blocked_robots": "blocked by robots.txt",
        "http_error": "HTTP errors",
        "error": "other errors",
        "unsafe": "refused as unsafe",
    }
    total = sum(counts.values())
    parts = [f"{value} {names.get(key, key)}" for key, value in counts.items() if value]
    return f"{total} page URLs: " + ", ".join(parts) + "." if total else ""


def _test_form(ui: UiState, project, *, enabled: bool) -> str:
    from aeo.ranksim.engines import NO_LLM_STATUS

    if not enabled:
        return (
            '<section class="card"><h2>Test a page</h2><p class="muted">This project has no REAL Google results yet, so there is nothing '
            "to compare a page with. Press Pull now (with a Google key, or when another project here already has these searches), "
            "or import a CSV of Google positions under Key searches below.</p></section>"
        )
    sim_ready = ui.llm_ready()
    sim = (
        '<label class="check"><input type="checkbox" name="simulate" value="1"> Also run the AI answer simulation '
        f"(PREDICTED, uses your LLM key). {e(_sim_cost_text(ui, project))}</label>"
        if sim_ready
        else f'<p class="small muted">AI answer simulation: {e(NO_LLM_STATUS)}.</p>'
    )
    return (
        '<section class="card" id="test"><h2>Test a page</h2>'
        "<p class=\"small muted\">Give one page. It is scored on every key search against the stored REAL Google results. Nothing is published.</p>"
        f'<form method="post" action="/p/{e(project.slug)}/test" enctype="multipart/form-data" class="stack" data-busy="Testing...">{_csrf_input(ui)}'
        '<fieldset><legend>Your page (pick one)</legend>'
        '<label>Upload an .html file<input type="file" name="html_file" accept=".html,.htm,text/html"></label>'
        '<label>or paste the HTML<textarea name="html_text" rows="4" placeholder="&lt;html&gt;...&lt;/html&gt;"></textarea></label>'
        '<label>or a preview URL<input type="url" name="preview_url" maxlength="2048" placeholder="https://staging.example.com/new-page/"></label>'
        "</fieldset>"
        f'<label>Where the page lives or will live (required)<input type="text" inputmode="url" name="url" maxlength="2048" '
        f'placeholder="https://{e(project.site)}/your-page/"></label>'
        f'<p class="small muted">The full address, for example https://{e(project.site)}/your-page/ (https:// is added if you leave it out). '
        "It decides which REAL result your page stands in for and keeps its versions together.</p>"
        f"{sim}"
        '<button type="submit" class="big primary">Test this page</button>'
        "<p class=\"small muted\">Upload limit 5 MB. Preview URLs are fetched politely: robots.txt is respected and private network addresses are refused.</p>"
        "</form></section>"
    )


SIM_RUNS = 3
SIM_TOKENS_IN = 3000  # about the size of one simulation prompt: 10 sources x 3 passages
SIM_TOKENS_OUT = 300


def _sim_cost_text(ui: UiState, project) -> str:
    """How many LLM calls a simulation makes, and a labeled cost estimate when the model's list price is known."""
    from aeo.ranksim.llm import ANTHROPIC_DEFAULT, LIST_RATES, OPENAI_DEFAULT, OPENROUTER_DEFAULT, openrouter_key

    calls = len(project.searches) * SIM_RUNS * 2
    settings = ui.settings
    model = OPENROUTER_DEFAULT if openrouter_key(settings) else (OPENAI_DEFAULT if settings.openai_api_key else ANTHROPIC_DEFAULT)
    text = f"About {calls} LLM calls ({len(project.searches)} searches x {SIM_RUNS} runs x with and without your page)."
    rates = LIST_RATES.get(model)
    if rates:
        cost = calls * (SIM_TOKENS_IN * rates[0] + SIM_TOKENS_OUT * rates[1]) / 1_000_000
        text += (
            f" Estimate at {model}'s list price and about {SIM_TOKENS_IN} input and {SIM_TOKENS_OUT} output tokens per call: "
            f"${cost:.2f}. Not a quote; the real cost is logged per call."
        )
    else:
        text += " The cost depends on your model's price; it is logged per call."
    return text


def _tested_pages(project) -> str:
    from aeo.ranksim.tester import TesterHistory

    folder = project.dir / "tests"
    if not folder.is_dir():
        return ""
    latest: dict[str, dict] = {}
    for row in results_index(project):
        latest[row.get("url", "")] = row
    items = []
    for path in sorted(folder.glob("*.jsonl")):
        entries = TesterHistory(path).entries()
        if not entries:
            continue
        url = str(entries[-1].get("url") or "")
        newest = latest.get(url)
        link = f' <a href="/p/{e(project.slug)}/results/{e(newest["result_id"])}">latest result</a>' if newest else ""
        items.append(
            f'<li><span class="url">{e(url)}</span><br><span class="small muted">{len(entries)} versions, newest {e(entries[-1].get("version"))} '
            f'tested {e(_stamp(entries[-1].get("created")))}.</span>{link} '
            f'<a href="/p/{e(project.slug)}/versions?url={e(quote(url, safe=""))}">versions</a></li>'
        )
    if not items:
        return ""
    return '<section class="card"><h2>Tested pages</h2><ul class="plain">' + "".join(items) + "</ul></section>"


def _job_body(ui: UiState, project, job) -> str:
    back = f'<p><a href="/p/{e(project.slug)}">Back to {e(project.name)}</a></p>'
    return _job_panel(ui, job, redirect=True) + back


# Results board ---------------------------------------------------------------


def _pct(value) -> str:
    return "n/a" if value is None else f"{round(100 * float(value))}%"


def _stamp(value) -> str:
    from aeo.ranksim.project import _stamp as stamp

    return stamp(value) if value else "unknown time"


def _board_body(ui: UiState, project, data: dict) -> str:
    from aeo.ranksim.project import plain_lead_from_dict

    summary = data.get("summary") or {}
    total = summary.get("searches", 0)
    pulled = summary.get("real_pulled") or ""
    schema = int(data.get("schema") or 1)
    parts = [
        f'<section class="hero"><p class="eyebrow">{tag("PREDICTED")} {tag("REAL")} Result {e(data.get("version"))}</p>'
        f"<h1>How your page does on {e(total)} key searches</h1>"
        f'<p class="url">{e(data.get("url"))}</p>'
        f'<p class="lead">{e(plain_lead_from_dict(data))}</p></section>'
    ]
    if schema >= 2:
        tiles = [
            ("PREDICTED", "On topic", f"{summary.get('on_topic', 0)} of {total}", "searches where your page covers what the REAL top 10 pages cover"),
            ("PREDICTED", "Top 3", f"{summary.get('predicted_top3', 0)} of {total}", "on-topic searches where your site's track record puts it in the top 3"),
            ("REAL", "This URL", f"{summary.get('real_top10', 0)} of {total}", f"searches where this URL is in the REAL top 10 ({pulled})"),
            ("REAL", "Your site", f"{summary.get('real_site_top10', 0)} of {total}", f"searches where any page of your site is in the REAL top 10 ({pulled})"),
        ]
    else:
        tiles = [
            ("PREDICTED", "Top 3", f"{summary.get('predicted_top3', 0)} of {total}", "older result: site history only, the page was not checked"),
            ("REAL", "This URL", f"{summary.get('real_top10', 0)} of {total}", f"searches where this URL is in the REAL top 10 ({pulled})"),
            ("REAL", "Your site", f"{summary.get('real_site_top10', 0)} of {total}", f"searches where any page of your site is in the REAL top 10 ({pulled})"),
        ]
    parts.append(
        '<section class="tiles">'
        + "".join(
            f'<div class="tile"><div class="tile-head">{tag(kind)} {e(label)}</div><div class="tile-num">{e(value)}</div><div class="small muted">{e(note)}</div></div>'
            for kind, label, value, note in tiles
        )
        + "</section>"
    )
    parts.append(f'<p class="summary-line">{e(summary.get("text"))}</p>')
    if data.get("top10_note"):
        parts.append(f'<p class="small muted">{e(data.get("top10_note"))}</p>')

    # To do
    todo = data.get("todo") or []
    if todo:
        items = []
        for item in todo:
            searches = item.get("searches") or []
            shown = ", ".join(searches[:4]) + (f" and {len(searches) - 4} more" if len(searches) > 4 else "")
            items.append(f"<li>{tag(item.get('label') or 'PREDICTED')} {e(item.get('text'))} <span class=\"small muted\">({e(shown)})</span></li>")
        parts.append(
            '<section class="card"><h2>What to change first</h2><ol class="todo">' + "".join(items) + "</ol>"
            '<p class="small muted">Ranked by how many of your searches each change affects. From the on-topic check and from what the '
            "REAL top 10 pages have in common; none of it is a proven ranking lever.</p></section>"
        )
    elif schema >= 2 and summary.get("on_topic") == total:
        parts.append('<section class="card"><h2>What to change first</h2><p>Nothing from the on-topic check: the page is on topic for every search.</p></section>')

    # How sure
    sure = []
    topic = data.get("topic_backtest") or {}
    if topic.get("text"):
        sure.append(f"<p><b>On-topic check:</b> {e(topic.get('text'))}</p>")
    slot = data.get("slot_backtest") or {}
    if data.get("slot_method") == "site history" and slot:
        sure.append(
            "<p><b>Where an on-topic page lands</b> comes from which sites keep showing up across your searches (site history), "
            "the method that did best in our accuracy tests. Editing the page text does not move it.</p>"
        )
        if data.get("slot_group_text"):
            sure.append(f"<p><b>{e(data.get('slot_group_text'))}</b></p>")
        sure.append(_sure_tiles(slot))
        sure.append(f'<p class="small">{e(slot.get("text"))}</p>')
    details = []
    content = data.get("content_backtest") or {}
    if content.get("text"):
        details.append(f'<p class="small"><b>Content model</b> (reads your page text; it is what moves between versions): {e(content.get("text"))} {e(data.get("content_note"))}</p>')
    backtest = data.get("backtest") or {}
    if backtest.get("text"):
        details.append(f'<p class="small muted">Content model order test: {e(backtest.get("text"))}</p>')
    if details:
        sure.append("<details><summary>Technical details</summary>" + "".join(details) + "</details>")
    parts.append('<section class="card"><h2>How sure is this?</h2>' + "".join(sure) + "</section>")

    # Versions
    version_bits = [f"<p>This is <b>{e(data.get('version'))}</b> of this URL, {e(data.get('word_count'))} words, from {e(data.get('source'))}, tested {e(_stamp(data.get('tested_at')))}.</p>"]
    if data.get("stored_copy"):
        version_bits.append(f'<p class="small">Compared with the {e(data.get("stored_copy"))}.</p>')
    if data.get("previous_version"):
        version_bits.append(f'<p class="small">Previous version: {e(data.get("previous_version"))}, rescored now on the same engine.</p>')
    topic_regressions = data.get("topic_regressions") or []
    if topic_regressions:
        version_bits.append(
            f"<p>{tag('PREDICTED')} <b>No longer on topic</b> on {len(topic_regressions)} searches:</p>"
            '<ul class="bad-list">' + "".join(f"<li>{e(item)}</li>" for item in topic_regressions) + "</ul>"
        )
    elif data.get("baseline") and schema >= 2:
        version_bits.append(f"<p>{tag('PREDICTED')} On-topic check: no search lost against {e(data.get('baseline'))}.</p>")
    if summary.get("baseline"):
        version_bits.append(
            f"<details><summary>{tag('PREDICTED', 'content model, unvalidated')} Against {e(summary.get('baseline'))}: better on {e(summary.get('better'))}, "
            f"worse on {e(summary.get('worse'))}, same on {e(summary.get('same'))} searches</summary>"
            '<p class="small muted">The content model did no better than guessing the middle in backtests, so these moves are a smoke test, '
            "not a forecast, and they do not decide pass or fail.</p>"
            + ('<ul class="small">' + "".join(f"<li>{e(item)}</li>" for item in (data.get("regressions") or [])) + "</ul>" if data.get("regressions") else "")
            + "</details>"
        )
    version_bits.append(
        f'<p><a href="/p/{e(project.slug)}/versions?url={e(quote(str(data.get("url") or ""), safe=""))}">All versions of this URL and compare</a></p>'
    )
    parts.append('<section class="card"><h2>Your versions</h2>' + "".join(version_bits) + "</section>")

    if summary.get("ai_text") or any(row.get("engines") or row.get("simulation") for row in data.get("searches") or []):
        parts.append(_ai_overview(data))

    notes = data.get("notes") or []
    if notes:
        parts.append('<section class="card"><h2>Notes</h2><ul>' + "".join(f"<li>{e(note)}</li>" for note in notes) + "</ul></section>")

    # Every search: a compact table, then one collapsible card per search
    searches = data.get("searches") or []
    rows = "".join(
        f'<tr><td><a href="#s{index}">{e(row.get("search"))}</a></td><td>{e(_topic_word(row))}</td>'
        f'<td class="num">{e(_slot_short(row))}</td><td class="num">{e(_real_short(row))}</td></tr>'
        for index, row in enumerate(searches, start=1)
    )
    parts.append(
        '<section class="card"><h2>Every search</h2><div class="table-scroll"><table class="grid"><thead><tr><th>Search</th>'
        f'<th>On topic? {tag("PREDICTED")}</th><th class="num">Slot {tag("PREDICTED")}</th><th class="num">This URL {tag("REAL")}</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )
    for index, row in enumerate(searches, start=1):
        parts.append(_search_card(project, data, index, row))
    timing = data.get("timing") or {}
    parts.append(
        '<section class="card small muted"><p>'
        f"Engine: pull {e(data.get('pull_id'))}. REAL Google results: {e(data.get('google_source'))} ({e(data.get('google_pulled'))}). "
        f"Load {e(timing.get('load_seconds'))} s, scoring {e(timing.get('score_seconds'))} s, AI {e(timing.get('ai_seconds'))} s, "
        f"total {e(timing.get('wall_seconds', timing.get('total_seconds')))} s.</p>"
        f'<p><a href="/p/{e(project.slug)}/results/{e(data.get("result_id"))}.json">Download this result as JSON</a></p></section>'
    )
    return "".join(parts)


def _ai_overview(data: dict) -> str:
    """One line per AI engine across all searches, engines without a key on one line, then the PREDICTED simulation."""
    from aeo.ranksim.engines import ENGINE_KEYS, ENGINE_SPECS

    searches = data.get("searches") or []
    items = []
    not_run = []
    for engine in ENGINE_KEYS:
        cells = [cell for row in searches for cell in (row.get("engines") or []) if cell.get("engine") == engine]
        if not cells:
            continue
        name = ENGINE_SPECS[engine].name
        answered = [cell for cell in cells if cell.get("status") == "ok"]
        if answered:
            cited = sum(1 for cell in answered if cell.get("our_cited_runs"))
            newest = max(str(cell.get("pulled_at") or "") for cell in answered)
            items.append(
                f"<li><b>{e(name)}</b> {tag('REAL')} cites your site on {cited} of {len(answered)} searches it answered "
                f'<span class="small muted">(newest answer {e(_stamp(newest))})</span></li>'
            )
            continue
        status = sorted({str(cell.get("status")) for cell in cells})[0]
        if status.startswith("not run: needs "):
            not_run.append(f"{name}: {status}")
        else:
            items.append(f'<li><b>{e(name)}</b> <span class="muted">{e(status)}</span></li>')
    if not_run:
        items.append(f'<li><b>No key set</b>: <span class="muted">{e("; ".join(not_run))}. Set the key before starting the server.</span></li>')
    items.append('<li><b>Bing Copilot</b> <span class="muted">not covered: no public API returns its citations</span></li>')
    sims = [row.get("simulation") for row in searches if row.get("simulation")]
    ok = [sim for sim in sims if sim.get("status") == "ok"]
    sim_line = ""
    if ok:
        cited = sum(int(sim.get("your_cited_runs") or 0) for sim in ok)
        runs = sum(int(sim.get("ok_runs") or 0) for sim in ok)
        sim_line = (
            f"{tag('PREDICTED', 'PREDICTED simulation')} our own LLM answering from the Google top 10 cited your page in "
            f"{cited} of {runs} runs over {len(ok)} searches. Not the real engines."
        )
    elif sims:
        sim_line = f"{tag('PREDICTED', 'PREDICTED simulation')} <span class=\"muted\">{e(sorted({str(sim.get('status')) for sim in sims})[0])}</span>"
    flags = sorted({flag for sim in sims for flag in (sim.get("flags") or [])})
    flag_html = ""
    if flags:
        flag_html = (
            '<div class="alert warn"><p><b>Instruction-like text found in pages fed to the simulation.</b> It was fenced and treated as data, '
            "but an LLM may still be swayed by it, so read the simulation's numbers with care. Real engines may distrust or demote such pages.</p>"
            "<ul>" + "".join(f"<li>{e(flag)}</li>" for flag in flags[:10]) + "</ul></div>"
        )
    return (
        '<section class="card"><h2>AI engines (ChatGPT, Claude, Perplexity, Gemini)</h2>'
        f'<ul class="keys">{"".join(items)}</ul>'
        + (f"<p>{sim_line}</p>" if sim_line else "")
        + flag_html
        + f'<p class="small muted">{e(data.get("ai_real_note"))}</p></section>'
    )


def _sure_tiles(slot: dict) -> str:
    if slot.get("within_2") is None:
        return ""
    tiles = [
        ("Within 2 places", _pct(slot.get("within_2")), f"of hidden pages (always guessing the middle: {_pct(slot.get('middle_within_2'))})"),
        ("Off on average", f"{float(slot.get('mae')):.1f} places", f"always guessing the middle: {float(slot.get('middle_mae')):.1f}"),
        ("Exactly right", _pct(slot.get("exact")), f"{slot.get('n')} REAL results, {slot.get('n_searches')} searches"),
    ]
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="tile-head">{tag("PREDICTED", "backtest")} {e(label)}</div><div class="tile-num">{e(value)}</div><div class="small muted">{e(note)}</div></div>'
        for label, value, note in tiles
    ) + "</div>"


def _headline(row: dict) -> int | None:
    return row.get("site_position") if row.get("site_position") is not None else row.get("predicted_position")


def _google_label(row: dict) -> str:
    from aeo.ranksim.project import google_label

    if row.get("site_position") is not None:
        low, high = row.get("site_google_low"), row.get("site_google_high")
        fallback = row.get("site_position")
    else:
        low, high = row.get("google_low"), row.get("google_high")
        fallback = row.get("predicted_position")
    if low is None:  # an older stored result
        return f"#{fallback}" if fallback is not None else "none"
    return google_label(low, high)


def _topic_word(row: dict) -> str:
    topic = row.get("topic") or {}
    if topic.get("status"):
        return topic["status"]
    return "n/a" if row.get("error") else "not checked"


def _slot_short(row: dict) -> str:
    head = _headline(row)
    if head is None:
        topic = row.get("topic") or {}
        return "not placed" if topic.get("on_topic") is not True else "none"
    of = row.get("site_position_of") if row.get("site_position") is not None else row.get("predicted_of")
    return f"{_google_label(row)} of {of}"


def _real_short(row: dict) -> str:
    if row.get("real_position") is not None:
        return f"#{row.get('real_position')}"
    return "not in it" if row.get("real_results") else "n/a"


def _search_card(project, board: dict, index: int, row: dict) -> str:
    head = _headline(row)
    using_site = row.get("site_position") is not None
    topic = row.get("topic") or {}
    summary_bits = f'{e(_topic_word(row))}, {e(_slot_short(row))}'
    parts = [
        f'<details class="card search" id="s{index}"{" open" if index == 1 else ""}><summary class="search-head"><h2>{e(row.get("search"))}</h2>'
        f'<span class="tag plain">{summary_bits}</span></summary>'
    ]
    if topic.get("text"):
        css = "good-text" if topic.get("on_topic") is True else "bad-text"
        parts.append(f'<p class="{css}">{tag("PREDICTED", "on-topic check")} {e(topic.get("text"))}</p>')
    if row.get("topic_regression"):
        parts.append(f'<p class="alert bad">No longer on topic against {e(board.get("baseline"))}.</p>')
    if head is not None:
        of = row.get("site_position_of") if using_site else row.get("predicted_of")
        likely = f"likely #{row.get('site_position_low')} to #{row.get('site_position_high')}" if using_site else ""
        label = _google_label(row)
        parts.append(
            f'<div class="slot"><div class="slot-badge">{tag("PREDICTED")}<span class="slot-num">{e(label)}</span><span class="muted"> of {e(of)}</span></div>'
            f'<div class="slot-text"><b>PREDICTED slot {e(label)}</b>{(" (" + e(likely) + ")") if likely else ""}, from your site\'s track record.'
            f"{_real_line(row)}</div></div>"
        )
    else:
        message = row.get("error") or "No PREDICTED slot."
        if topic.get("text") and message == topic.get("text"):
            message = "No PREDICTED slot: the on-topic check above failed, so the page is PREDICTED not to make the top 10 here."
        parts.append(f'<p class="alert">{e(message)}</p>{_real_line(row)}')
    if row.get("real_results"):
        parts.append(_serp_list(row, board, head))
        parts.append(
            f'<p class="small muted">{tag("REAL")} Google results: '
            f'{e(_source_and_date(row.get("real_source"), row.get("real_pulled")))}. {e(row.get("coverage_text"))}</p>'
        )
    if row.get("site_note"):
        parts.append(f'<p class="small muted">{e(row.get("site_note"))}</p>')
    if row.get("site_check"):
        parts.append(f'<p class="small">{e(row.get("site_check"))}</p>')

    # What the pages above have (site history is what put them there)
    above = row.get("above_slot") or []
    if above:
        first, rest = above[:3], above[3:]
        block = "".join(_comparison_html(comp) for comp in first)
        if rest:
            block += f"<details><summary>{len(rest)} more pages above you</summary>" + "".join(_comparison_html(comp) for comp in rest) + "</details>"
        parts.append(
            f'<details class="above"><summary>Why the {len(above)} pages above your slot are there {tag("REAL", "REAL counts")}</summary>{block}</details>'
        )
    elif head == 1:
        parts.append('<p class="small good-text">Nothing above your slot.</p>')

    # Content model and versions, unvalidated, tucked away
    content_bits = []
    if row.get("predicted_position") is not None:
        content_bits.append(
            f"slot {e(_content_label(row))} of {e(row.get('predicted_of'))}, confidence {e(row.get('confidence'))}"
        )
    change_prev = row.get("change_vs_previous")
    change_stored = row.get("change_vs_stored")
    if change_prev and change_prev != "n/a":
        content_bits.append(f"vs your previous version: {_change_html(change_prev)}")
    if change_stored and change_stored != "n/a":
        content_bits.append(f"vs the stored copy: {_change_html(change_stored)}")
    if row.get("flag") == "WORSE":
        content_bits.append('<span class="tag bad">WORSE</span>')
    extra = []
    if row.get("model_check"):
        extra.append(f'<p class="small muted">{e(row.get("model_check"))}</p>')
    backtest = row.get("backtest") or {}
    if backtest.get("pairwise") is not None:
        extra.append(
            f'<p class="small muted">On this search, held out, it ordered pairs of REAL results right {round(100 * backtest.get("pairwise"))}% of the time '
            f'({e(backtest.get("pairs"))} pairs; 50% is a coin flip).</p>'
        )
    if content_bits or extra:
        parts.append(
            f"<details><summary>{tag('PREDICTED', 'content model, unvalidated')} {'; '.join(content_bits) or 'no position'}</summary>"
            '<p class="small muted">The content model reads the page text but did no better than guessing the middle in backtests.</p>'
            + "".join(extra)
            + "</details>"
        )

    parts.append(_engines_html(row))
    parts.append("</details>")
    return "".join(parts)


def _content_label(row: dict) -> str:
    from aeo.ranksim.project import google_label

    if row.get("google_low") is None:
        return f"#{row.get('predicted_position')}"
    return google_label(row.get("google_low"), row.get("google_high"))


def _real_line(row: dict) -> str:
    pulled = row.get("real_pulled") or ""
    if row.get("real_position") is not None:
        return f" {tag('REAL')} This URL is #{e(row.get('real_position'))} ({e(pulled)})."
    if row.get("real_site_position") is not None:
        return f" {tag('REAL')} This URL is not in the {len(row.get('real_results') or [])} results; your site's best is #{e(row.get('real_site_position'))} ({e(pulled)})."
    if row.get("real_results"):
        return f" {tag('REAL')} No page of your site is in the {len(row.get('real_results') or [])} results ({e(pulled)})."
    return ""


def _source_and_date(source, pulled) -> str:
    from aeo.ranksim.project import source_and_date

    return source_and_date(str(source or ""), str(pulled or ""))


def _change_html(text: str) -> str:
    css = "good-text" if text.startswith("up") else ("bad-text" if text.startswith("down") else "")
    return f'<span class="{css}">{e(text)}</span>'


def _serp_list(row: dict, board: dict, head: int | None) -> str:
    using_site = row.get("site_position") is not None
    below = (row.get("site_below") if using_site else row.get("below")) or {}
    below_url = below.get("url")
    likely = f"likely #{row.get('site_position_low')} to #{row.get('site_position_high')}" if using_site else ""
    you = (
        f'<li class="you"><span class="pos">You</span><div class="res"><div class="title"><b>YOUR PAGE</b> {tag("PREDICTED", f"PREDICTED {_google_label(row)}")}</div>'
        f'<div class="url small">{e(board.get("url"))}</div>'
        + (f'<div class="small muted">{e(likely)}</div>' if likely else "")
        + "</div></li>"
    )
    items = []
    inserted = False
    for result in row.get("real_results") or []:
        if head is not None and not inserted and not result.get("tested_url") and below_url and result.get("url") == below_url:
            items.append(you)
            inserted = True
        items.append(_result_li(result))
    if head is not None and not inserted:
        items.append(you)
    caption = (
        '<p class="small muted caption">The REAL Google results in their REAL order, with your page placed at its PREDICTED slot. '
        "Pages below it would move down one place.</p>"
        if head is not None
        else '<p class="small muted caption">The REAL Google results in their REAL order. Your page is not placed here.</p>'
    )
    return caption + '<ol class="serp" role="list">' + "".join(items) + "</ol>"


def _result_li(result: dict) -> str:
    tags = []
    if result.get("tested_url"):
        tags.append('<span class="tag plain">this URL now</span>')
    elif result.get("our_site"):
        tags.append('<span class="tag plain">your site</span>')
    if result.get("beyond_top10"):
        tags.append('<span class="tag muted-tag">beyond the top 10, not used</span>')
    if not result.get("stored"):
        tags.append('<span class="tag muted-tag">not stored</span>')
    title = result.get("title") or result.get("domain") or result.get("url")
    css = "ours" if result.get("our_site") else ""
    return (
        f'<li class="{css}"><span class="pos">#{e(result.get("position"))}</span><div class="res">'
        f'<div class="title">{e(title)}</div><div class="url small">{ext_link(result.get("url") or "", result.get("domain") or result.get("url"))}</div>'
        f'<div class="small">{" ".join(tags)}</div></div></li>'
    )


def _comparison_html(comp: dict) -> str:
    head = f'<h3 class="h4">REAL #{e(comp.get("real_position"))} {e(comp.get("domain"))}</h3>'
    if comp.get("title"):
        head += f'<p class="small muted">{e(comp.get("title"))}</p>'
    body = []
    if comp.get("note"):
        css = "small warn-text" if comp.get("own_site") else "small"
        body.append(f'<p class="{css}">{e(comp.get("note"))}</p>')
    facts = comp.get("facts") or []
    if facts:
        body.append('<ul class="small">' + "".join(f"<li>{tag('REAL')} {e(fact)}</li>" for fact in facts) + "</ul>")
    if comp.get("page_gaps"):  # an older stored result: content model features
        body.append(
            '<ul class="small">'
            + "".join(f"<li>{e(gap.get('label'))}: yours {e(gap.get('yours_text'))}, theirs {e(gap.get('theirs_text'))}</li>" for gap in comp.get("page_gaps"))
            + "</ul>"
        )
    return f'<div class="comp">{head}{"".join(body)}</div>'


def _engines_html(row: dict) -> str:
    engines = row.get("engines") or []
    sim = row.get("simulation")
    if not engines and not sim:
        return ""
    lines = []
    not_run = [item for item in engines if str(item.get("status") or "").startswith("not run")]
    for item in engines:
        if item in not_run:
            continue
        if item.get("status") == "ok":
            if item.get("our_cited_runs"):
                cited = f"cites your site in {item.get('our_cited_runs')} of {item.get('ok_runs')} answers"
                if item.get("our_mean_position") is not None:
                    cited += f", first at citation #{round(item.get('our_mean_position'))}"
            else:
                cited = f"does not cite your site ({item.get('ok_runs')} answers)"
            top = ", ".join(item.get("top_domains") or []) or "nothing"
            detail = (
                f"{tag('REAL')} {e(cited)}. Most cited: {e(top)}. "
                f'<span class="small muted">Pulled {e(_stamp(item.get("pulled_at")))}, model {e(item.get("model"))}.</span>'
            )
            if item.get("note"):
                detail += f' <span class="small muted">{e(item.get("note"))}</span>'
        else:
            detail = f'<span class="muted">{e(item.get("status"))}</span>'
        lines.append(f'<div class="engine"><b>{e(item.get("name"))}</b><div>{detail}</div></div>')
    if not_run:
        names = ", ".join(str(item.get("name")) for item in not_run)
        lines.append(f'<div class="engine"><b>Not run</b><div><span class="muted">{e(names)}: no key set (see the AI engines section).</span></div></div>')
    sim_html = ""
    if sim:
        if sim.get("status") == "ok":
            share = _pct(sim.get("your_share"))
            sim_html = (
                f"<p>{tag('PREDICTED', 'PREDICTED simulation')} Our own LLM ({e(sim.get('model'))}) answering from the Google top 10 cited your page in "
                f"{e(sim.get('your_cited_runs'))} of {e(sim.get('ok_runs'))} runs ({e(share)}). Your site's share: {e(_pct(sim.get('domain_share_without')))} without your page, "
                f"{e(_pct(sim.get('domain_share_with')))} with it. Not the real engine.</p>"
            )
        else:
            sim_html = f'<p class="small">{tag("PREDICTED", "PREDICTED simulation")} <span class="muted">{e(sim.get("status"))}</span></p>'
        flags = sim.get("flags") or []
        if flags:
            sim_html += '<div class="alert warn small"><b>Instruction-like text in the sources:</b><ul>' + "".join(f"<li>{e(flag)}</li>" for flag in flags) + "</ul></div>"
    return f'<div class="ai"><h3 class="h4">AI answers for this search</h3>{"".join(lines)}{sim_html}</div>'


# Versions, compare, engines ---------------------------------------------------


def _history_for(project, url: str):
    from aeo.ranksim.tester import TesterHistory

    return TesterHistory(TesterHistory.default_path(url, project.dir / "tests"))


def _versions_body(ui: UiState, project, url: str) -> str:
    if not url:
        return _message_body("Pick a tested page from the project page.")
    history = _history_for(project, url)
    entries = history.entries()
    if not entries:
        return f'<section class="card"><p>No tested versions of {e(url)} yet.</p><p><a href="/p/{e(project.slug)}#test">Test a page</a></p></section>'
    by_hash: dict[str, dict] = {}
    for row in results_index(project):
        if row.get("url") == url:
            by_hash[str(row.get("content_hash"))] = row
    rows = []
    for item in entries:
        result = by_hash.get(str(item.get("content_hash")))
        link = f'<a href="/p/{e(project.slug)}/results/{e(result["result_id"])}">result</a>' if result else '<span class="muted">tested from the command line</span>'
        scores = item.get("scores") or []
        top3 = sum(1 for score in scores if score.get("predicted_rank") is not None and score["predicted_rank"] <= 3)
        checked = [score for score in scores if isinstance(score.get("on_topic"), bool)]
        on_topic = f"{sum(1 for score in checked if score['on_topic'])} of {len(scores)}" if checked else "not checked then"
        rows.append(
            f"<tr><td><b>{e(item.get('version'))}</b></td><td class=\"small\">{e(_stamp(item.get('created')))}</td><td class=\"num\">{e(item.get('word_count'))}</td>"
            f'<td class="num">{e(on_topic)}</td><td class="num hide-sm">{top3} of {len(scores)}</td><td>{link}</td></tr>'
        )
    options = "".join(f'<option value="{e(item.get("version"))}">{e(item.get("version"))}</option>' for item in entries)
    last_two = [item.get("version") for item in entries[-2:]]
    form = ""
    if len(entries) >= 2:
        form = (
            f'<form method="get" action="/p/{e(project.slug)}/compare" class="row-form"><input type="hidden" name="url" value="{e(url)}">'
            f'<label>Old<select name="a">{_select(options, last_two[0])}</select></label>'
            f'<label>New<select name="b">{_select(options, last_two[-1])}</select></label>'
            '<button type="submit">Compare</button></form>'
        )
    return (
        f'<section class="hero"><h1>Versions</h1><p class="url">{e(url)}</p></section>'
        '<section class="card"><div class="table-scroll"><table class="grid"><thead><tr><th>Version</th><th>Tested</th><th class="num">Words</th>'
        f'<th class="num">On topic then {tag("PREDICTED")}</th><th class="num hide-sm">Top 3 then {tag("PREDICTED", "PREDICTED, content model, unvalidated")}</th><th></th></tr></thead><tbody>'
        + "".join(rows)
        + f"</tbody></table></div>{form}</section>"
    )


def _select(options: str, chosen) -> str:
    return options.replace(f'value="{e(chosen)}"', f'value="{e(chosen)}" selected', 1)


def _compare_body(ui: UiState, project, url: str, a: str, b: str) -> str:
    from aeo.ranksim.project import _version_html, change_text, test_page

    if not url or not VERSION_RE.match(a or "") or not VERSION_RE.match(b or ""):
        return _message_body("Pick two versions to compare.")
    history = _history_for(project, url)
    entries = {str(item.get("version")): item for item in history.entries()}
    if a not in entries or b not in entries:
        return _message_body("One of those versions does not exist.")
    htmls = {}
    for name in (a, b):
        text = _version_html(history, str(entries[name].get("content_hash")))
        if text is None:
            return _message_body(f"The HTML of {name} was not saved, so it cannot be rescored.")
        htmls[name] = text
    lock = ui.jobs.project_lock(project.slug)
    if not lock.acquire(timeout=2):
        return _message_body("A pull or test is running for this project. Try again when it finishes.")
    try:
        ctx = ui.context(project.slug)
        boards = {name: test_page(ctx, htmls[name], url, save=False, ai=False) for name in (a, b)}
    finally:
        lock.release()
    old_rows = {row.search: row for row in boards[a].searches}
    better = worse = same = 0
    lost = gained = 0
    rows = []
    for row in boards[b].searches:
        old = old_rows.get(row.search)
        before = old.predicted_position if old else None
        old_topic = old.topic.status if old is not None and old.topic is not None else "n/a"
        new_topic = row.topic.status if row.topic is not None else "n/a"
        if old_topic == "on topic" and new_topic != "on topic":
            lost += 1
        elif old_topic != "on topic" and new_topic == "on topic":
            gained += 1
        change = change_text(before, row.predicted_position)
        if change.startswith("up"):
            better += 1
        elif change.startswith("down"):
            worse += 1
        elif change == "same":
            same += 1
        site = row.headline_label() if row.site_position is not None else "n/a"
        rows.append(
            f"<tr><td class=\"searchcell\">{e(row.search)}</td><td>{e(old_topic)}</td><td>{e(new_topic)}</td><td class=\"num hide-sm\">{e(site)}</td>"
            f"<td class=\"num hide-sm\">{e('#' + str(before) if before is not None else 'n/a')}</td>"
            f"<td class=\"num hide-sm\">{e('#' + str(row.predicted_position) if row.predicted_position is not None else 'n/a')}</td>"
            f"<td>{_change_html(change)}</td></tr>"
        )
    return (
        f'<section class="hero"><h1>{e(a)} vs {e(b)}</h1><p class="url">{e(url)}</p>'
        f"<p class=\"lead\">{tag('PREDICTED', 'PREDICTED, on-topic check')} {e(b)} lost {lost} and gained {gained} on-topic searches against {e(a)}. "
        f"{tag('PREDICTED', 'content model, unvalidated')} better on {better}, worse on {worse}, same on {same} searches. "
        f"Both rescored now on the same engine (pull {e(ctx.pull_id)}).</p></section>"
        '<section class="card"><p class="small muted">The site history slot does not change between versions: it comes from where your site appears, not from the page text. '
        "The on-topic check reads the text (losing a search there is what --fail-on-regression catches). The content model reads it too, but in "
        "backtests it did no better than always guessing the middle, so treat its changes as a smoke test.</p>"
        '<div class="table-scroll"><table class="grid compare"><thead><tr><th>Search</th>'
        f'<th>On topic {e(a)}</th><th>On topic {e(b)}</th><th class="num hide-sm">Site history slot</th>'
        f'<th class="num hide-sm">Content model {e(a)}</th><th class="num hide-sm">Content model {e(b)}</th><th>Content model change</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></section>"
    )


def _engines_body(ui: UiState, project) -> str:
    from aeo.ranksim.engines import REAL_NOTE
    from aeo.ranksim.project_engines import BING_NOTE, latest_engines

    latest = latest_engines(project, settings=ui.settings)
    parts = [
        f'<section class="hero"><h1>AI engines</h1><p class="lead">{tag("REAL")} What ChatGPT, Claude, Perplexity and Gemini cite for each key search, newest answers first.</p>'
        f'<p class="small muted">{e(REAL_NOTE)} {e(BING_NOTE)}</p></section>'
    ]
    # An engine with no key and no earlier answer says so once at the top, not once per search.
    never: dict[str, str] = {}
    columns = [latest.rows.get(search, []) for search in project.searches]
    for engine_rows in zip(*columns) if columns else []:
        statuses = {row.status for row in engine_rows}
        if len(statuses) == 1 and next(iter(statuses)).startswith("not run"):
            never[engine_rows[0].engine] = f"{engine_rows[0].name}: {next(iter(statuses))}"
    if never:
        parts.append(
            '<section class="card"><h2>Not run on any search</h2><ul class="keys">'
            + "".join(f'<li class="muted">{e(text)}</li>' for text in never.values())
            + "</ul><p class=\"small muted\">Set the key as an environment variable before starting the server, then press Pull now.</p></section>"
        )
    for search in project.searches:
        rows = []
        for row in latest.rows.get(search, []):
            if row.engine in never:
                continue
            if row.status != "ok":
                rows.append(f'<div class="engine"><b>{e(row.name)}</b><div class="muted">{e(row.status)}</div></div>')
                continue
            cited = "".join(f"<li>#{e(item['position'])} {ext_link(item['url'], item['domain'])}</li>" for item in row.cited[:8])
            ours = f"cites your site in {row.our_cited_runs} of {row.ok_runs} answers" if row.our_cited_runs else f"does not cite your site ({row.ok_runs} answers)"
            rows.append(
                f'<div class="engine"><b>{e(row.name)}</b><div>{tag("REAL")} {e(ours)}. '
                f'<span class="small muted">Pulled {e(_stamp(row.pulled_at))}, model {e(row.model)}, pull {e(row.pull_id)}.</span>'
                f'<ol class="small">{cited}</ol></div></div>'
            )
        if not rows:
            rows.append('<p class="small muted">No AI engine has a key, so nothing was asked for this search.</p>')
        parts.append(f'<section class="card"><h2>{e(search)}</h2>{"".join(rows)}</section>')
    return "".join(parts)


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

APP_CSS = """
:root{color-scheme:light;
--bg:#f5f6f8;--surface:#ffffff;--surface-2:#f9fafb;--ink:#161a22;--ink-2:#394150;--muted:#5c6474;--line:#e3e6eb;--line-2:#cfd4dc;
--accent:#2350c8;--accent-ink:#ffffff;--accent-weak:#edf2fd;--accent-line:#b8c9f2;
--pred:#874a00;--pred-bg:#fff2de;--pred-tint:#fffaf3;--obs:#0c5a45;--obs-bg:#e1f2eb;
--synth:#5f2aa6;--synth-bg:#f3ecfd;--synth-line:#dccbf6;--hist:#6a4b00;--hist-bg:#fbf2d6;--hist-line:#ecdca6;
--you:#fff7dc;--you-line:#c69200;--bad:#b42318;--bad-bg:#fdeceb;--good:#0b6b45;--good-bg:#e3f4ec;--warn:#7a4a00;--warn-bg:#fff5e0;
--chip:#eef0f4;--card:var(--surface);--real:var(--obs);--real-bg:var(--obs-bg);
--radius:10px;--shadow:0 1px 2px rgba(16,24,40,.04)}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;overflow-wrap:break-word}
a{color:var(--accent);text-underline-offset:2px}
a:hover{text-decoration-thickness:2px}
h1,h2,h3,h4{line-height:1.25;color:var(--ink)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip{position:absolute;left:12px;top:-60px;background:var(--surface);color:var(--accent);padding:8px 12px;border:2px solid var(--accent);border-radius:8px;z-index:20}
.skip:focus{top:8px}
.js .js-hide{display:none}
[hidden]{display:none!important}
.nowrap{white-space:nowrap}
.page{max-width:1320px;margin:0 auto;padding:0 24px}
main.page{padding-top:4px;padding-bottom:40px;outline:none}
main.page.narrow{max-width:1040px}
.muted{color:var(--muted)}.small{font-size:13.5px}.strong{font-weight:600}
.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.url{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;color:var(--muted)}
.break{overflow-wrap:anywhere}
.na{color:var(--muted);font-style:italic}
.opt{font-weight:400;color:var(--muted);font-size:12.5px}
.sep{color:var(--line-2);margin:0 4px}
.code{background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:13px;overflow-x:auto}
/* app bar */
.appbar{background:var(--surface);border-bottom:1px solid var(--line)}
.appbar-row{display:flex;align-items:center;gap:16px;min-height:56px;flex-wrap:wrap}
.brand{display:inline-flex;align-items:center;gap:8px;font-weight:700;font-size:16px;color:var(--ink);text-decoration:none;letter-spacing:-.01em}
.brand-mark{width:18px;height:18px;border-radius:5px;background:linear-gradient(var(--accent),var(--accent)) left bottom/5px 9px no-repeat,linear-gradient(var(--accent),var(--accent)) center bottom/5px 14px no-repeat,linear-gradient(var(--pred),var(--pred)) right bottom/5px 18px no-repeat}
.switcher{display:flex;gap:6px;align-items:center}
.switcher select{min-width:200px;max-width:280px;padding:6px 30px 6px 10px;font-size:14px}
.tabs{display:flex;gap:2px;margin-left:auto;align-self:stretch}
.tab{display:inline-flex;align-items:center;gap:8px;padding:0 14px;color:var(--ink-2);text-decoration:none;font-weight:600;font-size:14px;border-bottom:2px solid transparent;margin-bottom:-1px}
.tab:hover{color:var(--ink);background:var(--surface-2)}
.tab[aria-current=page]{color:var(--accent);border-bottom-color:var(--accent)}
.tab-n{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--chip);color:var(--ink-2);font-size:12px}
.tab[aria-current=page] .tab-n{background:var(--accent);color:var(--accent-ink)}
.context{background:var(--surface-2);border-bottom:1px solid var(--line);font-size:13.5px}
.ctx-row{display:flex;gap:6px 22px;align-items:center;flex-wrap:wrap;min-height:38px;padding-top:6px;padding-bottom:6px}
.ctx-k{color:var(--muted);margin-right:4px}
.ctx-q{font-weight:600}
.crumbs a{color:var(--muted)}
.strip{border-bottom:1px solid var(--line);font-size:13.5px;padding:8px 0}
.strip-synth{background:var(--synth-bg);color:#3f1a73;border-color:var(--synth-line)}
.strip-hist{background:var(--hist-bg);color:#4d3600;border-color:var(--hist-line)}
.badge{display:inline-block;font-size:12px;font-weight:700;border-radius:6px;padding:2px 8px;border:1px solid transparent;white-space:nowrap}
.badge-synth{background:var(--synth-bg);color:var(--synth);border-color:var(--synth-line)}
.badge-hist{background:var(--hist-bg);color:var(--hist);border-color:var(--hist-line)}
.badge-obs{background:var(--obs-bg);color:var(--obs)}
.context .badge{margin-left:auto}
/* page head */
.page-head{display:flex;gap:16px;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;margin:22px 0 16px}
.page-head h1{font-size:22px;margin:0 0 4px;font-weight:650;letter-spacing:-.01em}
.page-head .sub{margin:0;color:var(--muted);max-width:76ch}
.head-actions{display:flex;gap:8px;flex-wrap:wrap}
.eyebrow{margin:0 0 2px;font-size:12.5px;color:var(--muted);font-weight:600;letter-spacing:.02em}
/* panels */
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);margin:0 0 16px;box-shadow:var(--shadow);min-width:0}
.panel-head{padding:14px 16px 0;display:flex;flex-direction:column;gap:2px}
.panel-head h2{font-size:15px;margin:0;font-weight:650}
.panel-note{margin:0;font-size:13px;color:var(--muted)}
.panel-body{padding:12px 16px 16px}
.panel-body>p:first-child{margin-top:0}
.panel-body>:last-child{margin-bottom:0}
.panel-quiet{background:var(--surface-2);box-shadow:none}
.panel-accent{border-color:var(--accent-line)}
.panel>.table-wrap{margin-top:10px}
.grid-2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.grid-2>.panel{margin:0}
.grid-top{align-items:start;margin-bottom:16px}
.split{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
.split-main,.split-side,.cmp-main,.cmp-table,.grid-2>*{min-width:0}
.show-sm{display:none}
/* tables */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;position:relative}
.table{width:100%;border-collapse:collapse;font-size:14px}
.table th,.table td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.table thead th{font-size:12.5px;font-weight:600;color:var(--muted);background:var(--surface-2);white-space:nowrap;vertical-align:bottom}
.table tbody th{font-weight:600}
.table tbody tr:last-child>*{border-bottom:0}
.table tbody tr:hover{background:#fbfcfd}
.table .num{text-align:right}
.table td.actions{white-space:nowrap;text-align:right}
.table td.actions a{margin-left:12px}
.table-mini th,.table-mini td{padding:6px 10px;font-size:13.5px}
.table-note{margin:8px 2px 0}
.table-cov tbody th{min-width:12em}
.meter{display:inline-block;min-width:44px;text-align:center;border-radius:6px;padding:1px 6px;font-weight:600;font-size:13px}
.meter-ok{background:var(--good-bg);color:var(--good)}.meter-mid{background:var(--warn-bg);color:var(--warn)}.meter-low{background:var(--bad-bg);color:var(--bad)}
.urls{margin:6px 0 0;padding-left:18px;font-size:12.5px;display:grid;gap:2px}
.urls a{overflow-wrap:anywhere}
details.mini summary{cursor:pointer;font-size:13.5px;color:var(--accent);font-weight:500}
details.mini.pad{padding:0 16px 14px}
/* chips and tags */
.chip{display:inline-flex;align-items:center;font-size:11.5px;font-weight:650;border-radius:999px;padding:1px 8px;border:1px solid var(--line-2);color:var(--ink-2);background:var(--surface);white-space:nowrap;line-height:1.5}
.chip-you{background:var(--you);border-color:var(--you-line);color:#5c4300}
.chip-comp{background:var(--accent-weak);border-color:var(--accent-line);color:#1b3f9e}
.chip-ok{background:var(--good-bg);border-color:#b7dfcc;color:var(--good)}
.chip-bad{background:var(--bad-bg);border-color:#f3c3be;color:var(--bad)}
.chip-na{background:var(--chip);border-color:var(--line);color:var(--muted)}
.chip-unsure{background:var(--warn-bg);border-color:#f0d29a;color:var(--warn)}
.chip-sure{background:var(--good-bg);border-color:#b7dfcc;color:var(--good)}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;border-radius:4px;padding:1px 6px;vertical-align:1px;background:var(--chip);color:var(--ink)}
.tag-pred,.tag.pred{background:var(--pred-bg);color:var(--pred)}
.tag-obs,.tag.real{background:var(--obs-bg);color:var(--obs)}
.tag-synth{background:var(--synth-bg);color:var(--synth)}
.tag.bad{background:var(--bad-bg);color:var(--bad)}
/* buttons and forms */
.btn,button.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;font:inherit;font-size:14px;font-weight:600;line-height:1.2;padding:9px 14px;border-radius:8px;border:1px solid var(--line-2);background:var(--surface);color:var(--ink);text-decoration:none;cursor:pointer;min-height:38px}
.btn:hover{background:var(--surface-2);border-color:#b9c0cb}
.btn-primary,button.btn-primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.btn-primary:hover{background:#1c44ad;border-color:#1c44ad}
.btn-ghost{background:transparent;border-color:transparent;color:var(--accent)}
.btn-sm{font-size:13px;padding:6px 10px;min-height:32px}
.btn[disabled],button[disabled]{opacity:.65;cursor:progress}
.form{display:grid;gap:14px}
.form-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}
.span-2{grid-column:1/-1}
.field{display:grid;gap:5px;min-width:0;margin:0}
.field>label,.field>legend,.qp-select label{font-size:13.5px;font-weight:600;color:var(--ink);padding:0}
fieldset.field{border:0;padding:0;gap:6px}
.form input:not([type=checkbox]):not([type=radio]):not([type=file]),.form select,.form textarea,.switcher select,.qp-select select{font:inherit;font-size:14.5px;color:var(--ink);background:var(--surface);border:1px solid var(--line-2);border-radius:8px;padding:8px 10px;width:100%;max-width:100%}
.form textarea{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;line-height:1.45;resize:vertical}
.form input:focus-visible,.form select:focus-visible,.form textarea:focus-visible,.switcher select:focus-visible,.qp-select select:focus-visible{border-color:var(--accent);outline:2px solid var(--accent);outline-offset:1px}
.form input[type=radio]:focus-visible,.form input[type=checkbox]:focus-visible,.form input[type=file]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.form input[type=file]{font-size:13.5px}
.hint{margin:0;font-size:13px;color:var(--muted)}
label.radio,label.check{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:14px}
label.radio input,label.check input{margin-top:3px;width:auto;flex:none;accent-color:var(--accent)}
.form-actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.inline-form{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.unavailable{border:1px dashed var(--line-2);border-radius:8px;padding:10px 12px;background:var(--surface-2);font-size:13.5px;color:var(--ink-2)}
/* alerts */
.alert{border-radius:8px;padding:10px 14px;margin:0 0 14px;border:1px solid var(--line);background:var(--surface);font-size:14px}
.alert-good,.alert.good{background:var(--good-bg);border-color:#b7dfcc;color:#0a4a31}
.alert-bad,.alert.bad{background:var(--bad-bg);border-color:#f3c3be;color:#7f1d14}
.alert-warn,.alert.warn{background:var(--warn-bg);border-color:#f0d29a;color:#5a3700}
.alert-info{background:var(--accent-weak);border-color:var(--accent-line)}
.tight{margin:6px 0 0;padding-left:20px;display:grid;gap:3px}
/* facts */
.facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:7px 16px;margin:0;font-size:14px}
.facts>div{display:contents}
.facts dt{color:var(--muted)}
.facts dd{margin:0;min-width:0;overflow-wrap:anywhere}
.facts-tight{font-size:13.5px;gap:5px 14px}
.domain-list{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:6px}
.domain-list li{display:flex;justify-content:space-between;gap:8px;align-items:center;font-size:14px;border-bottom:1px solid var(--line);padding-bottom:6px}
/* comparisons */
.cmp-layout{display:grid;grid-template-columns:250px minmax(0,1fr);gap:20px;align-items:start}
.qpicker{position:sticky;top:12px}
.qp-select{display:none}
.qp-title{font-size:12.5px;font-weight:600;color:var(--muted);margin:0 0 6px;padding:0 10px}
.qp-list{list-style:none;margin:0;padding:0;display:grid;gap:2px}
.qp-list a{display:grid;gap:1px;padding:8px 10px;border-radius:8px;text-decoration:none;color:var(--ink);border:1px solid transparent}
.qp-list a:hover{background:var(--surface)}
.qp-list a[aria-current]{background:var(--surface);border-color:var(--accent-line);box-shadow:inset 3px 0 0 var(--accent)}
.qp-q{font-weight:600;font-size:14px;line-height:1.3}
.qp-m{font-size:12.5px;color:var(--muted)}
.cmp-head{display:flex;gap:6px 16px;align-items:baseline;flex-wrap:wrap;margin-bottom:6px}
.cmp-query{font-size:19px;margin:0;font-weight:650}
.cmp-meta{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;color:var(--muted)}
.scope{margin:0 0 14px;font-size:13.5px;color:var(--ink-2);background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--pred);border-radius:6px;padding:8px 12px}
.summary{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:0;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);margin-bottom:14px}
.sum-item{display:flex;flex-direction:column;gap:3px;padding:10px 14px;border-left:1px solid var(--line);align-items:flex-start;min-width:0}
.sum-item:first-child{border-left:0}
.sum-k{font-size:12.5px;color:var(--muted);font-weight:600}
.sum-v{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.cmp-body{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:16px;align-items:start}
.cmp-table{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:10px 0 10px;min-width:0}
.sortbar{display:flex;gap:6px;align-items:center;padding:0 12px 10px;flex-wrap:wrap}
.seg{font-size:13px;font-weight:600;text-decoration:none;color:var(--ink-2);border:1px solid var(--line-2);border-radius:6px;padding:3px 10px}
.seg[aria-current]{background:var(--ink);color:#fff;border-color:var(--ink)}
.table-cmp td.pos{font-size:15px;font-weight:650;width:1%}
.table-cmp .pos-pred,.table-cmp thead th:nth-child(2){background:var(--pred-tint)}
.table-cmp thead th:nth-child(2){color:var(--pred)}
.table-cmp tr.row-you>td{background:var(--you)}
.table-cmp tr.row-you>td:first-child{box-shadow:inset 3px 0 0 var(--you-line)}
.table-cmp tr.row-sel>td{background:var(--accent-weak)}
.table-cmp tr.row-sel.row-you>td{background:#fdf0c4}
.table-cmp tr.row-missing .page-link{color:var(--ink-2)}
.page-cell{min-width:14em}
.page-link{font-weight:600;text-decoration:none;color:var(--ink)}
.page-link:hover{text-decoration:underline;color:var(--accent)}
.page-sub{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:2px}
.shift{font-weight:600}.shift-up{color:var(--good)}.shift-down{color:var(--bad)}
.ok-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--good);margin-right:6px;vertical-align:1px}
.miss{color:var(--bad);font-weight:600}
.detail{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;position:sticky;top:12px;font-size:14px;min-width:0}
.detail-head h3{margin:0 0 2px;font-size:16px}
.detail-head p{margin:0 0 12px}
.detail h4{font-size:13px;margin:16px 0 6px;color:var(--ink-2)}
.ev{list-style:none;margin:0;padding:0;display:grid;font-size:13.5px}
.ev li{display:grid;grid-template-columns:minmax(0,1fr) auto 4.2em;gap:10px;padding:5px 0;border-bottom:1px solid var(--line);align-items:baseline}
.ev li:last-child{border-bottom:0}
.ev-k{color:var(--ink-2)}
.ev-v{font-variant-numeric:tabular-nums;text-align:right;font-weight:600}
.ev-r{font-size:12.5px;color:var(--muted);text-align:right;white-space:nowrap}
.ev-top{color:var(--good);font-weight:600}
.facts-stack{grid-template-columns:minmax(0,1fr);gap:0}
.facts-stack dt{margin-top:8px;font-size:12.5px}
.facts-stack div:first-child dt{margin-top:0}
.run-notes{margin:12px 0 0;color:var(--warn);font-size:14px}
.limits{margin-top:14px;border-top:1px solid var(--line);padding-top:10px}
.limits summary{cursor:pointer;font-weight:600;font-size:13.5px}
.limits li{font-size:13px;color:var(--ink-2)}
/* draft */
.draft-form textarea#d-draft{min-height:220px}
.ba{display:grid;grid-template-columns:auto auto auto minmax(0,1fr);gap:12px 16px;align-items:center}
.ba-col{display:grid;grid-template-columns:auto auto;align-items:baseline;gap:0 8px;border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:130px}
.ba-col .chip{grid-column:1/-1;justify-self:start;margin-top:4px}
.ba-k{grid-column:1/-1;font-size:12.5px;color:var(--muted);font-weight:600}
.ba-v{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1}
.ba-of{color:var(--muted);font-size:13.5px}
.ba-after{border-color:var(--pred);background:var(--pred-tint)}
.ba-arrow{width:28px;height:2px;background:var(--line-2);position:relative}
.ba-arrow::after{content:"";position:absolute;right:-1px;top:-4px;border:5px solid transparent;border-left:7px solid var(--line-2)}
.ba-verdict{margin:0;font-size:15px;font-weight:600}
.verdict-good{color:var(--good)}.verdict-bad{color:var(--bad)}
.caveat{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);font-size:14px;color:var(--ink-2)}
.caveat p{margin:0 0 6px}
.ord-wrap{display:grid;gap:16px}
.ord-two{grid-template-columns:1fr 1fr}
.ord-col h3{font-size:13px;margin:0 0 6px;color:var(--ink-2)}
.ord{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.ord li{display:grid;grid-template-columns:28px minmax(0,1fr);gap:6px;padding:6px 10px;border-top:1px solid var(--line);font-size:13.5px}
.ord li:first-child{border-top:0}
.ord-n{font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums}
.ord-name{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;min-width:0;overflow-wrap:anywhere}
.ord-obs{font-size:12px;color:var(--muted)}
.ord li.ord-you{background:var(--you);box-shadow:inset 3px 0 0 var(--you-line)}
.table-diff tr.row-changed>*{background:#fff8e6}
.table-diff tr.row-changed>th{box-shadow:inset 3px 0 0 var(--pred)}
.topic{padding:4px 0 8px}
.topic+.topic{border-top:1px solid var(--line);padding-top:10px}
.topic h3{font-size:14px;margin:0 0 4px;display:flex;gap:8px;align-items:center}
.topic p{margin:0 0 4px}
.ai-text{white-space:pre-wrap;font-size:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
/* older pages (advanced, results, versions), restyled with the same tokens */
.hero h1{font-size:22px;line-height:1.25;margin:20px 0 6px}
.lead{font-size:15.5px;margin:6px 0;color:var(--ink-2)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;margin:16px 0;box-shadow:var(--shadow)}
.card h2{margin:0 0 8px;font-size:16px}
.h4,h4{font-size:14px;margin:12px 0 4px}
.good-text{color:var(--good);font-weight:600}.bad-text{color:var(--bad);font-weight:600}.bad-list{color:var(--bad)}
.warn-text{color:var(--pred);font-weight:600}
main.narrow button:not(.btn),main.narrow .button{font:inherit;font-size:14px;font-weight:600;border:1px solid var(--line-2);background:var(--surface);color:var(--ink);border-radius:8px;padding:8px 14px;cursor:pointer;text-decoration:none;display:inline-block}
main.narrow button.primary,main.narrow .button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
main.narrow button.big{font-size:16px;padding:11px 20px;width:100%;max-width:360px}
form.stack{display:grid;gap:12px}
form.stack label,.row-form label{display:grid;gap:4px;font-weight:600;font-size:14px}
main.narrow label.check{display:flex}
main.narrow input:not([type=checkbox]):not([type=radio]),main.narrow textarea,main.narrow select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line-2);border-radius:8px;padding:8px;width:100%;max-width:100%}
fieldset{border:1px solid var(--line);border-radius:10px;padding:12px;display:grid;gap:10px;margin:0;min-width:0}
legend{font-weight:650;padding:0 4px}
.row-form{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-top:12px}
.row-form label{min-width:120px}
.cards{list-style:none;padding:0;margin:0;display:grid;gap:12px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.project-card{margin:0}.project-card h3{margin:0}
.keys{margin:0;padding-left:20px}
.plain{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:12px}
.tile-num{font-size:26px;font-weight:700;line-height:1.2;margin:4px 0}
.summary-line{font-size:14px;color:var(--muted)}
.table-scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%}
.grid th,.grid td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top;overflow-wrap:normal;word-break:normal}
.grid th{font-size:12.5px;color:var(--muted);white-space:nowrap}
.grid .num{text-align:right}
.grid td.url,.grid td .url{overflow-wrap:anywhere}
.grid.compare{min-width:640px}
.caption{margin:8px 0 4px}
.mini td{padding:2px 8px}
.jump{margin:0;padding-left:24px;display:grid;gap:4px}
.search-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.search-head h2{margin:0}
.slot{display:flex;gap:16px;align-items:center;margin:12px 0;flex-wrap:wrap}
.slot-badge{display:flex;flex-direction:column;align-items:flex-start;border:2px solid var(--you-line);background:var(--you);border-radius:12px;padding:8px 14px}
.slot-num{font-size:30px;font-weight:800;line-height:1.1}
.slot-text{flex:1 1 240px}
.serp{list-style:none;margin:8px 0;padding:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.serp li{display:grid;grid-template-columns:44px 1fr;gap:8px;padding:8px 10px;border-top:1px solid var(--line)}
.serp li:first-child{border-top:0}
.serp li.ours{background:var(--chip)}
.serp li.you{background:var(--you);border-left:5px solid var(--you-line);padding-left:5px}
.serp .pos{font-weight:700;color:var(--muted)}
.serp li.you .pos{color:var(--pred);text-transform:uppercase;font-size:13px;padding-top:2px}
.serp .title{font-weight:600}
.res{min-width:0}
details{margin:10px 0}
summary{cursor:pointer;font-weight:600}
.comp{border-left:3px solid var(--line);padding:4px 0 4px 12px;margin:10px 0}
.comp ul{margin:4px 0;padding-left:20px}
.ai{border-top:1px solid var(--line);margin-top:12px;padding-top:8px}
.engine{display:grid;grid-template-columns:110px 1fr;gap:8px;padding:6px 0;border-bottom:1px dashed var(--line)}
.engine ol{margin:4px 0;padding-left:20px}
.job .steps{display:grid;gap:10px;margin-top:8px}
.job h2{font-size:15px}
.step-head{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}
.step-msg{margin:2px 0 0}
progress{width:100%;height:10px;accent-color:var(--accent)}
.skipped{margin:2px 0;color:var(--muted);font-style:italic}
.todo{margin:0;padding-left:22px;display:grid;gap:8px}
.searchcell{min-width:9em}
details.card.search>summary{list-style:none;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
details.card.search>summary::-webkit-details-marker{display:none}
details.card.search>summary::before{content:"+";font-weight:700;color:var(--muted);width:1em}
details.card.search[open]>summary::before{content:"-"}
.foot{color:var(--muted);font-size:12.5px;padding:18px 0 28px;border-top:1px solid var(--line);background:var(--surface)}
/* responsive */
@media (max-width:1280px){.cmp-body{grid-template-columns:minmax(0,1fr)}.detail{position:static}}
@media (max-width:1100px){.split{grid-template-columns:minmax(0,1fr)}.hide-md{display:none}}
@media (max-width:960px){
.cmp-layout{grid-template-columns:minmax(0,1fr);gap:12px}
.qpicker{position:static}.qp-title,.qp-list{display:none}
.qp-select{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:10px 12px}
.qp-list-only{display:none}
.qp-select select{flex:1 1 220px;width:auto}
.tabs{margin-left:0;order:3;width:100%;border-top:1px solid var(--line)}
.tab{flex:1 1 0;justify-content:center;padding:10px 6px}
.appbar-row{gap:10px 14px;padding-top:8px}
.switcher{margin-left:auto}
}
@media (max-width:760px){
.page{padding:0 16px}
.grid-2,.form-grid,.ord-two{grid-template-columns:minmax(0,1fr)}
.summary{grid-template-columns:minmax(0,1fr)}
.sum-item{border-left:0;border-top:1px solid var(--line)}
.sum-item:first-child{border-top:0}
.hide-sm{display:none}.show-sm{display:inline}
.strip{padding:6px 0}
.page-head{margin:16px 0 12px}
.table-cov thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
.table-cov,.table-cov tbody,.table-cov tr,.table-cov th,.table-cov td{display:block;width:100%}
.table-cov tr{padding:10px 14px;border-bottom:1px solid var(--line)}
.table-cov tbody tr:last-child{border-bottom:0}
.table-cov th,.table-cov td{border:0;padding:2px 0;text-align:left}
.table-cov tbody th{font-size:15px;margin-bottom:4px}
.table-cov td[data-label]{display:flex;justify-content:space-between;gap:12px}
.table-cov td[data-label]::before{content:attr(data-label);color:var(--muted);font-size:13px}
.table-cov td.hide-sm{display:none}
.table-cov td.cov-action{margin-top:4px}
.table-cov td.cov-cand{display:none}
.table-cov details.mini{text-align:right}
.page-head h1{font-size:20px}
.ba{grid-template-columns:auto auto auto}
.ba-verdict{grid-column:1/-1}
.table th,.table td{padding:8px 9px}
.page-cell{min-width:0}
.switcher select{min-width:0;max-width:52vw}
.engine{grid-template-columns:1fr;gap:2px}
.serp li{grid-template-columns:36px 1fr}
.card{padding:12px}
.context .badge{margin-left:0}
.facts{grid-template-columns:minmax(0,1fr);gap:0}
.facts dt{margin-top:6px}
}
@media (max-width:420px){.tab{font-size:13px;gap:5px}.tab-n{width:18px;height:18px;font-size:11px}.brand{font-size:15px}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
"""

APP_JS = """
(function(){
  'use strict';
  function text(el, value){ if(el){ el.textContent = value == null ? '' : String(value); } }
  function stepRow(item){
    var row = document.createElement('div'); row.className = 'step'; row.setAttribute('data-step', item.step);
    var head = document.createElement('div'); head.className = 'step-head';
    var b = document.createElement('b'); head.appendChild(b);
    var meta = document.createElement('span'); meta.className = 'muted small'; head.appendChild(meta);
    var bar = document.createElement('progress');
    var msg = document.createElement('p'); msg.className = 'small muted step-msg';
    row.appendChild(head); row.appendChild(bar); row.appendChild(msg);
    return row;
  }
  function render(box, data){
    var names = {queued:'Waiting', running:'Running', done:'Done', error:'Failed'};
    text(box.querySelector('[data-status]'), names[data.status] || data.status);
    text(box.querySelector('[data-elapsed]'), Number(data.elapsed || 0).toFixed(1));
    text(box.querySelector('[data-message]'), data.message);
    var holder = box.querySelector('[data-steps]');
    (data.steps || []).forEach(function(item){
      var row = holder.querySelector('[data-step="' + item.step.replace(/[^a-z_]/g, '') + '"]');
      if(!row){ row = stepRow(item); holder.appendChild(row); }
      text(row.querySelector('b'), item.label);
      var count = item.total ? (item.done + ' of ' + item.total + ' ') : '';
      text(row.querySelector('.step-head span'), count + item.seconds + ' s');
      var bar = row.querySelector('progress');
      if(item.skipped){
        if(bar){ var note = document.createElement('p'); note.className = 'small skipped'; note.textContent = 'not run'; bar.replaceWith(note); }
        text(row.querySelector('.step-head span'), item.seconds + ' s');
        text(row.querySelector('.step-msg'), item.message);
        return;
      }
      if(!bar){ return; }
      var total = Math.max(1, item.total || 1);
      bar.max = total; bar.value = item.total ? Math.min(item.done, total) : 0;
      if(!item.total && data.status === 'running'){ bar.removeAttribute('value'); }
      text(row.querySelector('.step-msg'), item.message);
    });
    var err = box.querySelector('[data-error]');
    if(data.error && err){ err.hidden = false; text(err, data.error); }
  }
  function poll(box){
    var id = box.getAttribute('data-job');
    var redirect = box.getAttribute('data-redirect') === '1';
    function tick(){
      fetch('/api/jobs/' + encodeURIComponent(id), {headers:{'Accept':'application/json'}, cache:'no-store'})
        .then(function(r){ return r.json(); })
        .then(function(data){
          render(box, data);
          if(data.status === 'done'){
            if(redirect && data.result_url){ window.location.assign(data.result_url); }
            else { window.location.assign(window.location.pathname); }
            return;
          }
          if(data.status === 'error'){ return; }
          setTimeout(tick, 700);
        })
        .catch(function(){ setTimeout(tick, 2000); });
    }
    tick();
  }
  document.documentElement.classList.add('js');
  document.addEventListener('DOMContentLoaded', function(){
    document.querySelectorAll('form[data-autosubmit] select').forEach(function(select){
      select.addEventListener('change', function(){ if(select.value){ select.form.submit(); } });
    });
    document.querySelectorAll('select[data-navigate]').forEach(function(select){
      select.addEventListener('change', function(){
        var draft = document.getElementById('d-draft');
        var orig = document.getElementById('d-orig');
        var typed = (draft && draft.value.trim()) || (orig && orig.value.trim());
        if(typed){ return; }
        window.location.assign(select.getAttribute('data-navigate') + encodeURIComponent(select.value));
      });
    });
    document.querySelectorAll('[data-show-when]').forEach(function(block){
      var form = block.closest('form');
      var want = block.getAttribute('data-show-when');
      function sync(){
        var picked = form.querySelector('input[name=original_mode]:checked');
        block.hidden = !(picked && picked.value === want);
      }
      form.querySelectorAll('input[name=original_mode]').forEach(function(radio){ radio.addEventListener('change', sync); });
      sync();
    });
    document.querySelectorAll('[data-job]').forEach(function(box){
      var status = box.querySelector('[data-status]');
      if(status && (status.textContent === 'Done' || status.textContent === 'Failed')){ return; }
      poll(box);
    });
    document.querySelectorAll('form[data-busy]').forEach(function(form){
      form.addEventListener('submit', function(){
        var button = form.querySelector('button[type=submit]:not(.js-hide)') || form.querySelector('button[type=submit]');
        if(button){ setTimeout(function(){ button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = form.getAttribute('data-busy'); }, 0); }
      });
    });
  });
})();
"""
