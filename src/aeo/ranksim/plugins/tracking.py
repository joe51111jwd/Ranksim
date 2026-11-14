"""Search data tracking: provider health, monthly cost cap, scheduled re-checks, rank history, SERP features.

Plan and storage shapes: docs/plans/track.md. Nothing here invents a position. History is read from the REAL
Google snapshots a project already stores (pulls/<id>/google.json); the cap and the freshness reuse only decide
whether a paid request is made.
"""

from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request

from aeo.errors import ProviderConfigError

CAP_ENV = "RANKSIM_MONTHLY_SEARCH_CAP"
FRESH_ENV = "RANKSIM_SEARCH_FRESH_HOURS"
CRON_SECONDS_ENV = "RANKSIM_CRON_SECONDS"
CRON_SECRET_ENV = "CRON_SECRET"
DEFAULT_CAP = 500
DEFAULT_FRESH_HOURS = 6.0
DEFAULT_CRON_SECONDS = 240.0
MIN_PULL_SECONDS = 30.0
USAGE_DIR = ".tracking"
USAGE_FILE = "usage.json"
PLUGIN_DIR = ("plugins", "tracking")
SCHEDULES = {"off": None, "daily": 24.0, "weekly": 168.0}
DEFAULT_SCHEDULE = "weekly"
SLACK_HOURS = 2.0  # Vercel runs a daily cron within its hour, so "daily" must not miss by a few minutes
RETRY_HOURS = 20.0  # at most one re-check attempt per project per day, even when the attempt failed
TOP_DEPTH = 10
SPARK_POINTS = 12
CRON_PATH = "/api/v1/cron/recheck"
PROVIDER_LABELS = {"serpapi": "SerpAPI", "dataforseo": "DataForSEO", "serper": "Serper"}
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

_FILE_LOCKS: dict[str, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


class SearchCapError(ProviderConfigError):
    """The monthly search cap is reached and there are no earlier results to carry."""


# -- small helpers ----------------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else None


def parse_time(value) -> datetime | None:
    """ISO date or time from a snapshot. Naive values are UTC. Anything unreadable is None."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _env_number(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _file_lock(path: Path) -> threading.Lock:
    key = str(Path(path).resolve())
    with _FILE_LOCKS_GUARD:
        if key not in _FILE_LOCKS:
            _FILE_LOCKS[key] = threading.Lock()
        return _FILE_LOCKS[key]


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def month_key(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def next_month_start(moment: datetime) -> datetime:
    moment = moment.astimezone(timezone.utc)
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(moment.year, moment.month + 1, 1, tzinfo=timezone.utc)


def _reset_label(moment: datetime) -> str:
    start = next_month_start(moment)
    return f"{MONTHS[start.month - 1]} {start.day}"


# -- 1. cost control ------------------------------------------------------------------------------


class Budget:
    """Monthly search counter, cap and freshness reuse for paid Google requests.

    Passed to project.pull(budget=...). Thread safe: the pull asks for searches from several threads. The counter
    lives in <projects root>/.tracking/usage.json, which cloud storage mirrors with the projects folder.
    """

    def __init__(self, root: str | Path, *, cap: int | None = None, fresh_hours: float | None = None, clock=None) -> None:
        self.root = Path(root)
        self._cap = cap
        self._fresh_hours = fresh_hours
        self.clock = clock or _now
        self._lock = _file_lock(self.path)

    # settings are read at call time so a changed env var applies without a restart
    @property
    def cap(self) -> int:
        if self._cap is not None:
            return max(0, int(self._cap))
        return int(_env_number(CAP_ENV, DEFAULT_CAP))

    @property
    def fresh_hours(self) -> float:
        if self._fresh_hours is not None:
            return max(0.0, float(self._fresh_hours))
        return _env_number(FRESH_ENV, DEFAULT_FRESH_HOURS)

    @property
    def fresh_label(self) -> str:
        hours = self.fresh_hours
        return f"{hours:g} hour" if hours == 1 else f"{hours:g} hours"

    @property
    def path(self) -> Path:
        return self.root / USAGE_DIR / USAGE_FILE

    # -- reading

    def usage(self) -> dict:
        data = _read_json(self.path)
        months = data.get("months") if isinstance(data.get("months"), dict) else {}
        data["months"] = months
        return data

    def month(self, data: dict | None = None) -> dict:
        data = data if data is not None else self.usage()
        row = data["months"].get(month_key(self.clock())) or {}
        return {key: int(row.get(key) or 0) for key in ("searches", "reused", "capped", "errors")}

    def used(self) -> int:
        return self.month()["searches"]

    def remaining(self) -> int:
        return max(0, self.cap - self.used())

    def is_fresh(self, earlier) -> bool:
        """True when earlier REAL results were pulled within the freshness window (not in the future)."""
        if earlier is None or not getattr(earlier, "results", None) or getattr(earlier, "error", ""):
            return False
        hours = self.fresh_hours
        if hours <= 0:
            return False
        pulled = parse_time(getattr(earlier, "pulled_at", None))
        if pulled is None:
            return False
        age = self.clock() - pulled
        return timedelta(0) <= age < timedelta(hours=hours)

    def needed(self, searches, by_previous: dict) -> int:
        """How many of these searches would need a paid request now."""
        return sum(1 for search in searches if not self.is_fresh(by_previous.get(search)))

    def cap_message(self) -> str:
        moment = self.clock()
        return (
            f"monthly search cap reached: {self.used()} of {self.cap} searches used in {month_label(month_key(moment))}. "
            f"Raise {CAP_ENV} or wait until {_reset_label(moment)}"
        )

    def refusal(self, searches, by_previous: dict) -> str | None:
        """Why no paid request may start now, or None. Fresh searches never need one."""
        if self.needed(searches, by_previous) and self.remaining() <= 0:
            return self.cap_message()
        return None

    def refuse(self, message: str) -> None:
        text = message[:1].upper() + message[1:]
        raise SearchCapError(f"{text}. No Google results were pulled and nothing was invented.")

    # -- writing

    def _bump(self, **changes) -> None:
        with self._lock:
            data = self.usage()
            key = month_key(self.clock())
            row = data["months"].setdefault(key, {})
            for name, value in changes.items():
                if name in {"searches", "reused", "capped", "errors"}:
                    row[name] = int(row.get(name) or 0) + int(value)
                else:
                    data[name] = value
            # keep a year of months; older rows are history nobody reads
            for old in sorted(data["months"])[:-12]:
                data["months"].pop(old, None)
            _write_json(self.path, data)

    def claim(self, search: str, earlier=None) -> str:
        """'reuse' (fresh earlier results), 'call' (one paid request, counted now), or a refusal sentence."""
        if self.is_fresh(earlier):
            self._bump(reused=1)
            return "reuse"
        with self._lock:
            data = self.usage()
            if self.month(data)["searches"] >= self.cap:
                verdict = self.cap_message()
                self._bump_locked(data, capped=1)
                return verdict
            self._bump_locked(data, searches=1)
            return "call"

    def _bump_locked(self, data: dict, **changes) -> None:
        row = data["months"].setdefault(month_key(self.clock()), {})
        for name, value in changes.items():
            row[name] = int(row.get(name) or 0) + int(value)
        _write_json(self.path, data)

    def record(self, *, ok: bool, error: str = "") -> None:
        """Provider health: the time of the last good answer, or the last error (an HTTP code, never a URL)."""
        stamp = _iso(self.clock())
        if ok:
            self._bump(last_ok_at=stamp)
        else:
            self._bump(errors=1, last_error=str(error)[:120], last_error_at=stamp)


def budget_for(ui) -> Budget:
    existing = (getattr(ui, "pull_options", None) or {}).get("budget")
    if isinstance(existing, Budget):
        return existing
    return Budget(ui.root)


def provider_status(settings, *, public_demo: bool = False) -> dict:
    """The Google provider resolve_serp(settings, 'auto') would pick. Key values never leave the server."""
    key = None
    if not public_demo:
        if getattr(settings, "serpapi_key", None):
            key = "serpapi"
        elif getattr(settings, "dataforseo_login", None) and getattr(settings, "dataforseo_password", None):
            key = "dataforseo"
        elif getattr(settings, "serper_api_key", None):
            key = "serper"
    return {"provider": PROVIDER_LABELS.get(key) if key else None, "provider_key": key, "configured": key is not None}


def tracking_status(ui) -> dict:
    budget = budget_for(ui)
    data = budget.usage()
    month = budget.month(data)
    moment = budget.clock()
    status = provider_status(ui.settings, public_demo=bool(getattr(ui, "public_demo", False)))
    status.update(
        {
            "used": month["searches"],
            "cap": budget.cap,
            "remaining": max(0, budget.cap - month["searches"]),
            "reused": month["reused"],
            "capped": month["capped"],
            "errors": month["errors"],
            "month": month_key(moment),
            "month_label": month_label(month_key(moment)),
            "resets": _iso(next_month_start(moment)),
            "fresh_hours": budget.fresh_hours,
            "last_ok_at": data.get("last_ok_at"),
            "last_error": data.get("last_error") or "",
            "last_error_at": data.get("last_error_at"),
            "healthy": _healthy(data),
            "cron": {"secret_set": bool((os.environ.get(CRON_SECRET_ENV) or "").strip()), "path": CRON_PATH},
            "storage": _storage(ui),
        }
    )
    return status


def _healthy(data: dict) -> bool | None:
    ok = parse_time(data.get("last_ok_at"))
    bad = parse_time(data.get("last_error_at"))
    if ok is None and bad is None:
        return None  # never asked
    if bad is None:
        return True
    return ok is not None and ok >= bad


def _storage(ui) -> str:
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and getattr(cloud, "enabled", False):
        return "cloud"
    return "temporary" if getattr(ui, "public_demo", False) else "disk"


# -- 2. per project settings and state -----------------------------------------------------------


def _plugin_dir(project) -> Path:
    return Path(project.dir).joinpath(*PLUGIN_DIR)


def load_schedule(project) -> str:
    value = str(_read_json(_plugin_dir(project) / "settings.json").get("schedule") or DEFAULT_SCHEDULE)
    return value if value in SCHEDULES else DEFAULT_SCHEDULE


def save_schedule(project, schedule: str) -> str:
    value = str(schedule or "").strip().lower()
    if value not in SCHEDULES:
        raise ValueError("Pick off, daily or weekly.")
    path = _plugin_dir(project) / "settings.json"
    data = _read_json(path)
    data["schedule"] = value
    _write_json(path, data)
    return value


def load_state(project) -> dict:
    return _read_json(_plugin_dir(project) / "state.json")


def save_state(project, state: dict) -> None:
    _write_json(_plugin_dir(project) / "state.json", state)


# -- 3. rank history ------------------------------------------------------------------------------


def stored_snapshots(project) -> list:
    """Every finished Google snapshot that holds its own data (carried copies repeat older pulls and are skipped)."""
    from aeo.ranksim.project import GoogleSnapshot, list_pulls

    found = []
    for info in list_pulls(project):
        if not info.finished:
            continue
        data = _read_json(Path(project.dir) / "pulls" / info.pull_id / "google.json")
        if not data or str(data.get("kind") or "") == "carried":
            continue
        found.append(GoogleSnapshot.from_dict(data))
    return found


def _domain(url_or_domain: str) -> str:
    from aeo.crawl.urls import domain_of

    text = str(url_or_domain or "").strip().lower()
    host = domain_of(text) if "://" in text else text
    return (host or "").removeprefix("www.")


def _matches(domain: str, wanted: str) -> bool:
    return bool(domain) and bool(wanted) and (domain == wanted or domain.endswith("." + wanted))


def _best(results, keep) -> tuple[int | None, str | None]:
    best = None
    for item in results:
        position = int(getattr(item, "position", 0) or 0)
        if not 1 <= position <= TOP_DEPTH or not keep(item):
            continue
        if best is None or position < best[0]:
            best = (position, item.url)
    return best if best else (None, None)


def classify(previous: int | None, current: int | None, *, first: bool = False) -> tuple[str, int | None]:
    """(change, delta). delta > 0 means it moved up (a smaller position number)."""
    if first:
        return "first", None
    if previous is None and current is None:
        return "none", None
    if previous is None:
        return "new", None
    if current is None:
        return "lost", None
    if current < previous:
        return "up", previous - current
    if current > previous:
        return "down", previous - current
    return "same", 0


def _competitors(project, meta, snapshots) -> list[str]:
    chosen = [_domain(item) for item in (getattr(meta, "competitors", None) or []) if _domain(item)]
    if chosen:
        return list(dict.fromkeys(chosen))[:5]
    if not snapshots:
        return []
    try:
        from aeo.ranksim import autopilot

        return [_domain(item) for item in autopilot.pick_competitors(snapshots[-1], project.domain)][:5]
    except Exception:  # noqa: BLE001 - the list of competitors is a nicety, history still works without it
        return []


def build_history(project, meta=None, *, snapshots=None) -> dict:
    """Each search's REAL position over time for our site and the top competitors, and what changed last check."""
    if meta is None:
        try:
            from aeo.ranksim import workspace as ws

            meta = ws.load_meta(project)
        except Exception:  # noqa: BLE001
            meta = None
    snapshots = stored_snapshots(project) if snapshots is None else snapshots
    competitors = _competitors(project, meta, snapshots)
    wanted = list(project.searches)
    per_search: dict[str, dict[str, tuple[datetime, object]]] = {search: {} for search in wanted}
    trend = []
    for snapshot in snapshots:
        written = parse_time(snapshot.written_at)
        ranked: list[int] = []
        counted = 0
        for entry in snapshot.searches:
            if entry.search not in per_search or not entry.results:
                continue
            moment = parse_time(entry.pulled_at) or written
            if moment is None:
                continue
            per_search[entry.search][_iso(moment)] = (moment, entry)  # the same pull date twice is one check
            counted += 1
            position, _url = _best(entry.results, lambda item: project.is_our_url(item.url))
            if position is not None:
                ranked.append(position)
        if counted and written is not None:
            trend.append(
                {
                    "at": _iso(written),
                    "pull_id": snapshot.pull_id,
                    "searches": counted,
                    "ranked": len(ranked),
                    "top3": sum(1 for value in ranked if value <= 3),
                    "average": round(sum(ranked) / len(ranked), 1) if ranked else None,
                }
            )
    trend.sort(key=lambda row: row["at"])
    changes = {key: 0 for key in ("up", "down", "new", "lost", "same", "none", "first")}
    rows = []
    last_check: datetime | None = None
    for search in wanted:
        checks = sorted(per_search[search].values(), key=lambda pair: pair[0])
        points = []
        rivals: dict[str, list[dict]] = {domain: [] for domain in competitors}
        for moment, entry in checks:
            position, url = _best(entry.results, lambda item: project.is_our_url(item.url))
            points.append({"at": _iso(moment), "position": position, "url": url})
            for domain in competitors:
                spot, spot_url = _best(entry.results, lambda item, d=domain: _matches(_domain(item.url), d))
                rivals[domain].append({"at": _iso(moment), "position": spot, "url": spot_url})
            last_check = moment if last_check is None or moment > last_check else last_check
        current = points[-1]["position"] if points else None
        previous = points[-2]["position"] if len(points) >= 2 else None
        if points:
            change, delta = classify(previous, current, first=len(points) == 1)
        else:
            change, delta = "none", None
        changes[change] += 1
        latest_entry = checks[-1][1] if checks else None
        rows.append(
            {
                "search": search,
                "position": current,
                "previous": previous,
                "change": change,
                "delta": delta,
                "url": points[-1]["url"] if points else None,
                "checked_at": points[-1]["at"] if points else None,
                "points": points,
                "competitors": rivals,
                "features": dict(getattr(latest_entry, "features", None) or {}) if latest_entry is not None else {},
            }
        )
    schedule = load_schedule(project)
    next_check = None
    if SCHEDULES.get(schedule) and last_check is not None:
        next_check = _iso(last_check + timedelta(hours=SCHEDULES[schedule]))
    return {
        "site": project.site,
        "competitors": competitors,
        "checks": len(trend),
        "first_at": trend[0]["at"] if trend else None,
        "last_at": trend[-1]["at"] if trend else None,
        "changes": changes,
        "trend": trend,
        "searches": rows,
        "schedule": schedule,
        "last_check_at": _iso(last_check),
        "next_check_at": next_check,
        "note": "REAL Google positions from stored pulls. Empty means not in the top 10 we hold.",
    }


def compact_history(full: dict) -> dict:
    """The small form stored in report["history"]."""
    compact = {key: full.get(key) for key in ("site", "competitors", "checks", "first_at", "last_at", "changes", "trend", "schedule", "last_check_at", "next_check_at", "note")}
    compact["trend"] = (full.get("trend") or [])[-SPARK_POINTS * 2 :]
    compact["searches"] = [
        {
            "search": row["search"],
            "position": row["position"],
            "previous": row["previous"],
            "change": row["change"],
            "delta": row["delta"],
            "spark": [point["position"] for point in row["points"][-SPARK_POINTS:]],
            "features": sorted(row.get("features") or {}),
        }
        for row in full.get("searches") or []
    ]
    return compact


def serp_features(project) -> dict[str, dict]:
    """{search: features} from the latest snapshot. Shape in docs/plans/track.md section 5."""
    from aeo.ranksim.project import latest_google

    snapshot = latest_google(project)
    if snapshot is None:
        return {}
    return {entry.search: dict(entry.features or {}) for entry in snapshot.searches if entry.features}


# -- 4. scheduled re-checks -----------------------------------------------------------------------


@dataclass
class DueProject:
    slug: str
    schedule: str
    last_check_at: datetime | None
    reason: str


def last_check(project) -> datetime | None:
    """Newest REAL pull date the project holds, from any snapshot that has its own data."""
    newest = None
    for snapshot in stored_snapshots(project):
        written = parse_time(snapshot.written_at)
        for entry in snapshot.searches:
            if not entry.results:
                continue
            moment = parse_time(entry.pulled_at) or written
            if moment is not None and (newest is None or moment > newest):
                newest = moment
    return newest


def _skippable_dataset(project) -> str | None:
    try:
        from aeo.ranksim import workspace as ws

        meta = ws.load_meta(project)
    except Exception:  # noqa: BLE001
        return None
    if meta.dataset == ws.DATASET_SYNTHETIC:
        return "synthetic demo data"
    if meta.dataset == ws.DATASET_HISTORICAL:
        return "historical export"
    return None


def due_projects(root: str | Path, *, now: datetime | None = None) -> tuple[list[DueProject], list[dict]]:
    """(due, skipped). Due ones come oldest check first; a project never checked comes first of all."""
    from aeo.ranksim.project import list_projects, load_project

    now = now or _now()
    due: list[DueProject] = []
    skipped: list[dict] = []
    for summary in list_projects(root=root):
        try:
            project = load_project(summary.slug, root=root)
        except Exception:  # noqa: BLE001 - a broken project folder must not stop the others
            skipped.append({"slug": summary.slug, "reason": "could not be read"})
            continue
        schedule = load_schedule(project)
        interval = SCHEDULES.get(schedule)
        if interval is None:
            skipped.append({"slug": project.slug, "reason": "re-checks are off"})
            continue
        dataset = _skippable_dataset(project)
        if dataset:
            skipped.append({"slug": project.slug, "reason": f"{dataset}, never pulled from Google"})
            continue
        attempted = parse_time(load_state(project).get("last_attempt_at"))
        if attempted is not None and now - attempted < timedelta(hours=RETRY_HOURS):
            skipped.append({"slug": project.slug, "reason": "already tried today"})
            continue
        checked = last_check(project)
        if checked is not None and now - checked < timedelta(hours=interval - SLACK_HOURS):
            skipped.append({"slug": project.slug, "reason": "not due yet", "next_check_at": _iso(checked + timedelta(hours=interval))})
            continue
        due.append(DueProject(project.slug, schedule, checked, "never checked" if checked is None else f"{schedule} check due"))
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    due.sort(key=lambda item: (item.last_check_at or oldest, item.slug))
    return due, skipped


def _estimate_seconds(project) -> float:
    from aeo.ranksim.project import load_pull_report

    try:
        report = load_pull_report(project)
    except Exception:  # noqa: BLE001
        report = None
    seconds = float(getattr(report, "total_seconds", 0) or 0) if report is not None else 0.0
    return max(MIN_PULL_SECONDS, seconds)


def _studio_run_pull():
    try:
        from aeo.ranksim.api.sites import _run_pull  # after the P0 split
    except ImportError:
        from aeo.ranksim.studio import _run_pull
    return _run_pull


def _default_run_pull(ui, project) -> dict:
    """The same pull a person starts in the Studio, Google on, AI engines off. Saves to cloud storage itself."""
    return _studio_run_pull()(ui, project, True, queue.Queue())


def recheck(ui, *, now: datetime | None = None, run_pull=None, monotonic=time.monotonic, limit_seconds: float | None = None) -> dict:
    """Re-pull Google for due projects, oldest first, within the monthly cap and the function time limit."""
    from aeo.ranksim.project import latest_google, load_project

    started = monotonic()
    now = now or _now()
    limit = float(limit_seconds) if limit_seconds is not None else _env_number(CRON_SECONDS_ENV, DEFAULT_CRON_SECONDS, minimum=1.0)
    run_pull = run_pull or _default_run_pull
    budget = budget_for(ui)
    status = provider_status(ui.settings, public_demo=bool(getattr(ui, "public_demo", False)))
    result = {"started_at": _iso(now), "provider": status["provider"], "ran": [], "skipped": [], "deferred": []}
    if not status["configured"]:
        result["note"] = "No Google provider key is set (SERPER_API_KEY). Nothing was pulled."
        return result
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and getattr(cloud, "enabled", False):
        cloud.refresh(force=True)
    due, skipped = due_projects(ui.root, now=now)
    result["skipped"] = skipped
    touched = False
    for item in due:
        project = load_project(item.slug, root=ui.root)
        previous = latest_google(project)
        by_previous = previous.by_search() if previous else {}
        needed = budget.needed(project.searches, by_previous)
        remaining = budget.remaining()
        if needed > remaining:
            result["deferred"].append({"slug": item.slug, "reason": f"needs {needed} searches, {remaining} left this month"})
            continue
        estimate = _estimate_seconds(project)
        elapsed = monotonic() - started
        if elapsed + estimate > limit:
            result["deferred"].append({"slug": item.slug, "reason": "out of time for this run, next run picks it up"})
            continue
        lock = ui.jobs.project_lock(item.slug) if getattr(ui, "jobs", None) is not None else None
        if lock is not None and not lock.acquire(blocking=False):
            result["deferred"].append({"slug": item.slug, "reason": "a pull is already running"})
            continue
        state = load_state(project)
        state["last_attempt_at"] = _iso(now)
        save_state(project, state)
        touched = True
        began = monotonic()
        try:
            summary = run_pull(ui, project)
            outcome = {"slug": item.slug, "ok": True, "reason": item.reason, "summary": _short_summary(summary)}
            state.update({"last_result": "ok", "last_error": "", "last_ok_at": _iso(now)})
        except Exception as exc:  # noqa: BLE001 - one project failing must not stop the others
            message = _scrub(ui, f"{type(exc).__name__}: {str(exc)[:300]}")
            outcome = {"slug": item.slug, "ok": False, "reason": item.reason, "error": message}
            state.update({"last_result": "error", "last_error": message})
        finally:
            if lock is not None:
                lock.release()
        outcome["seconds"] = round(monotonic() - began, 1)
        save_state(project, state)
        result["ran"].append(outcome)
    result["used"] = budget.used()
    result["cap"] = budget.cap
    if touched and cloud is not None and getattr(cloud, "enabled", False):
        try:
            cloud.save()
        except Exception as exc:  # noqa: BLE001 - reported, the pulls themselves already saved
            result["storage_error"] = type(exc).__name__
    return result


def _short_summary(summary) -> dict:
    if not isinstance(summary, dict):
        return {}
    keep = ("pull_id", "google_kind", "searches_with_results", "searches_total", "total_seconds")
    return {key: summary[key] for key in keep if key in summary}


def _scrub(ui, text: str) -> str:
    scrub = getattr(ui, "scrub", None)
    return scrub(text) if callable(scrub) else text


def cron_authorized(header: str | None, secret: str | None = None) -> bool:
    """Vercel sends Authorization: Bearer <CRON_SECRET>. No secret set means nobody is authorized."""
    secret = (os.environ.get(CRON_SECRET_ENV) or "") if secret is None else secret
    secret = secret.strip()
    if not secret or not header:
        return False
    return hmac.compare_digest(str(header).strip().encode(), f"Bearer {secret}".encode())


# -- 5. plugin hooks ------------------------------------------------------------------------------


def _common():
    try:
        from aeo.ranksim.api import common  # after the P0 split
    except ImportError:
        from aeo.ranksim import studio as common
    return common


def _open_cron_path() -> None:
    """The owner login must not block Vercel's cron call; the route checks its own bearer secret."""
    for name in ("aeo.ranksim.api.common", "aeo.ranksim.studio"):
        try:
            module = __import__(name, fromlist=["OPEN_EXACT"])
        except ImportError:
            continue
        exact = getattr(module, "OPEN_EXACT", None)
        if isinstance(exact, set):
            exact.add(CRON_PATH)


def install_budget(ui) -> Budget:
    """Every pull started through ui.pull_options (Studio and classic screens) is counted and capped."""
    options = getattr(ui, "pull_options", None)
    if options is None:
        options = {}
        ui.pull_options = options
    budget = options.get("budget")
    if not isinstance(budget, Budget):
        budget = Budget(ui.root)
        options["budget"] = budget
    return budget


def register(app, ui) -> None:
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import JSONResponse

    common = _common()
    install_budget(ui)
    _open_cron_path()

    def error(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    @app.get("/api/v1/tracking/status")
    async def tracking_status_route() -> JSONResponse:
        return JSONResponse(await run_in_threadpool(tracking_status, ui))

    @app.get("/api/v1/projects/{slug}/history")
    async def history_route(slug: str) -> JSONResponse:
        try:
            project = ui.project(slug)
        except LookupError:
            return error(404, "No such project.")
        return JSONResponse(await run_in_threadpool(build_history, project))

    def schedule_view(project) -> dict:
        state = load_state(project)
        schedule = load_schedule(project)
        checked = last_check(project)
        interval = SCHEDULES.get(schedule)
        return {
            "schedule": schedule,
            "schedules": list(SCHEDULES),
            "last_check_at": _iso(checked),
            "next_check_at": _iso(checked + timedelta(hours=interval)) if checked and interval else None,
            "last_attempt_at": state.get("last_attempt_at"),
            "last_result": state.get("last_result"),
            "last_error": state.get("last_error") or "",
        }

    @app.get("/api/v1/projects/{slug}/tracking")
    async def tracking_get(slug: str) -> JSONResponse:
        try:
            project = ui.project(slug)
        except LookupError:
            return error(404, "No such project.")
        return JSONResponse(await run_in_threadpool(schedule_view, project))

    @app.post("/api/v1/projects/{slug}/tracking")
    async def tracking_set(slug: str, request: Request) -> JSONResponse:
        common._check_header_csrf(ui, request)
        blocked = common._not_public(ui)
        if blocked is not None:
            return blocked
        body = await common._json_body(request, 4096)
        try:
            project = ui.project(slug)
        except LookupError:
            return error(404, "No such project.")
        try:
            save_schedule(project, str(body.get("schedule") or ""))
        except ValueError as exc:
            return error(400, str(exc))
        return JSONResponse(await run_in_threadpool(schedule_view, project))

    @app.get(CRON_PATH)
    async def cron_recheck(request: Request) -> JSONResponse:
        if not cron_authorized(request.headers.get("authorization")):
            return error(401, "Unauthorized.")
        if getattr(ui, "public_demo", False):
            return error(403, "Re-checks do not run on the public demo.")
        return JSONResponse(await run_in_threadpool(recheck, ui))


def extend_report(report: dict, *, project, meta=None, database=None, ctx=None, ui=None) -> None:
    report["history"] = compact_history(build_history(project, meta))


def settings_schema() -> dict:
    return {
        "key": "tracking",
        "title": "Re-checks",
        "fields": [{"name": "schedule", "type": "choice", "choices": list(SCHEDULES), "default": DEFAULT_SCHEDULE}],
    }
