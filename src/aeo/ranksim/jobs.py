"""Jobs: long work (an analysis, a pull) with an id, progress events, a per-key lock, pause and resume.

A job runs in a thread of the instance that started it. Its events live in memory there, so the request that started
it (or any request on that instance) can follow it live. Other instances see it through storage, which is budgeted
(Vercel Hobby caps Blob operations per month), so storage holds as little as possible:

- The lock. ranksim/v2/locks/<hash of key>.json is created with a create-only put (one advanced operation). If the
  name is taken, its holder is read: a live holder means the caller attaches to that job instead of starting a second
  one. The lock records a deadline (start + budget + STALL_GRACE_SECONDS); a holder past its deadline with no final
  record lost its instance and is reported stalled and resumable.
- The final or paused record. on_finish(record) gets it; the app hands it to cloud storage, which carries it in the
  manifest of the job's own data save, so it costs nothing extra. Without on_finish (or when it declines) the record
  is written to ranksim/v2/jobs/<id>.json.
- Nothing else: no heartbeats, no progress snapshots. A browser that reattaches on another instance sees the job as
  running and gets its events once it finishes or pauses.

A job id starts with 12 characters of its key's hash, so any instance can find the job's lock from the id alone.
Long work checks ctx.should_pause() and raises JobPaused before the request's time limit; the record keeps
ctx.checkpoint, and resume() continues it in a new request on any instance. A paused job keeps its lock for
PAUSED_HOLD_SECONDS, so posting the same site again resumes it instead of starting over.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import string
import threading
import time
from collections.abc import Callable, Iterator

from aeo.ranksim.objects import DirStore, MemoryStore, ObjectExists, ObjectStore, StoreError

SAFETY_SECONDS = 25.0  # should_pause() turns true when less than this is left of the budget
DEFAULT_BUDGET_SECONDS = 240.0
STALL_GRACE_SECONDS = 60.0  # a running job past start + budget + this lost its instance
PAUSED_HOLD_SECONDS = 600.0  # a paused job keeps its key this long, then an abandoned pause stops blocking it
MAX_EVENTS = 500  # kept in memory
STORED_EVENTS = 60  # kept in a stored record (the manifest carries a few records)
MAX_ATTEMPTS = 30
LIVE = ("queued", "running")
FINISHED = ("done", "error")
_ID_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
_ID_ALPHABET = string.ascii_letters + string.digits


class JobPaused(BaseException):
    """Raised by work to stop at a checkpoint. The job becomes "paused" and resume() continues it.

    A BaseException on purpose: crawl code catches Exception per host so one failing host never ends a pull, and a
    pause must pass through that and stop the whole pull (pages fetched so far are already stored)."""


def valid_job_id(job_id: object) -> bool:
    return isinstance(job_id, str) and bool(_ID_RE.match(job_id))


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def new_job_id(key: str = "") -> str:
    """12 hex characters of the key's hash (any instance finds the job's lock from its id) and 12 random ones."""
    return _key_hash(key)[:12] + "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))


def default_error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _scrub_all(value, scrub):
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {(_scrub_all(k, scrub) if isinstance(k, str) else k): _scrub_all(v, scrub) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_all(v, scrub) for v in value]
    return value


def _json_copy(value):
    return json.loads(json.dumps(value))


class _Run:
    def __init__(self, record: dict, started: float) -> None:
        self.record = record
        self.started = started
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.finished = threading.Event()
        self.superseded = False


class JobContext:
    """What work(ctx) gets: identity, inputs, progress events, checkpoints and the time budget."""

    def __init__(self, registry: "JobRegistry", run: _Run) -> None:
        self._registry = registry
        self._run = run
        self.job_id: str = run.record["id"]
        self.params: dict = copy.deepcopy(run.record.get("params") or {})
        self.checkpoint: dict = copy.deepcopy(run.record.get("checkpoint") or {})
        self.attempt: int = int(run.record.get("attempts") or 1)

    def emit(self, event: dict) -> dict:
        """Add a progress event. Every string in it is scrubbed. Returns the stored event (with its seq)."""
        return self._registry._emit(self._run, event)

    def save_checkpoint(self, data: dict | None = None) -> None:
        """Keep data (default: ctx.checkpoint) as the checkpoint. It is stored when the job pauses or ends."""
        if data is not None:
            self.checkpoint = data
        with self._run.lock:
            self._run.record["checkpoint"] = _json_copy(self.checkpoint)

    def time_left(self) -> float:
        reg = self._registry
        return reg.budget_seconds - (reg.clock() - self._run.started)

    def should_pause(self) -> bool:
        """True when the work should raise JobPaused now (the budget is nearly spent)."""
        return self._run.superseded or self.time_left() < self._registry.safety_seconds


class JobRegistry:
    """Jobs of this process, with locks and final records in an object store shared by every instance."""

    def __init__(
        self,
        store: ObjectStore | None = None,
        *,
        prefix: str = "ranksim/v2/",
        scrub: Callable[[str], str] = lambda s: s,
        budget_seconds: float | None = None,
        clock: Callable[[], float] = time.time,
        instance: str | None = None,
        safety_seconds: float = SAFETY_SECONDS,
        frugal: bool | None = None,
        on_finish: Callable[[dict], bool] | None = None,
        recall: Callable[[str, bool], dict | None] | None = None,
        **_ignored,
    ) -> None:
        self.store = store if store is not None else MemoryStore()
        self.prefix = prefix
        self.scrub = scrub
        if budget_seconds is None:
            try:
                budget_seconds = float(os.environ.get("RANKSIM_JOB_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS)
            except ValueError:
                budget_seconds = DEFAULT_BUDGET_SECONDS
        self.budget_seconds = float(budget_seconds)
        self.safety_seconds = float(safety_seconds)
        self.clock = clock
        self.frugal = (not isinstance(self.store, (MemoryStore, DirStore))) if frugal is None else bool(frugal)
        self.on_finish = on_finish
        self.recall = recall
        self.instance = instance or f"{os.getpid()}-{secrets.token_hex(4)}"
        self.last_error = ""
        self._mutex = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._runs: dict[str, _Run] = {}

    # -- names and records -------------------------------------------------------------------------

    def _lock_name(self, key_hash: str) -> str:
        return f"{self.prefix}locks/{key_hash[:12]}.json"

    def _record_name(self, job_id: str) -> str:
        return f"{self.prefix}jobs/{job_id}.json"

    def _key_lock(self, key: str) -> threading.Lock:
        with self._mutex:
            return self._key_locks.setdefault(key, threading.Lock())

    def _local(self, job_id: str) -> _Run | None:
        with self._mutex:
            return self._runs.get(job_id)

    def _view(self, record: dict, *, local: bool) -> dict:
        out = copy.deepcopy(record)
        status = out.get("status")
        stalled = (not local) and status in LIVE and self.clock() > float(out.get("deadline") or 0)
        out["stalled"] = stalled
        out["resumable"] = bool((status == "paused" or stalled) and int(out.get("attempts") or 0) < MAX_ATTEMPTS)
        return out

    def _read(self, name: str) -> dict | None:
        try:
            data = json.loads(self.store.get(name).decode("utf-8"))
        except (KeyError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _final(self, job_id: str, *, fresh: bool = False) -> dict | None:
        """A finished or paused record: this process, the app's recall (cloud manifest), or the record object."""
        run = self._local(job_id)
        if run is not None and run.finished.is_set() and not run.superseded:
            with run.lock:
                return copy.deepcopy(run.record)
        if self.recall is not None:
            found = self.recall(job_id, False)
            if found is not None:
                return found
        found = self._read(self._record_name(job_id))
        if found is None and fresh and self.recall is not None:
            found = self.recall(job_id, True)  # another instance may have saved it a moment ago
        return found

    def _record(self, job_id: str) -> tuple[dict | None, bool]:
        """(record, is_local): memory while this process runs the job, else storage."""
        run = self._local(job_id)
        if run is not None and not run.finished.is_set() and not run.superseded:
            with run.lock:
                return copy.deepcopy(run.record), True
        final = self._final(job_id)
        if final is not None:
            return final, False
        holder = self._read(self._lock_name(job_id))
        if holder is not None and holder.get("id") == job_id:
            return holder, False
        return self._final(job_id, fresh=True), False

    # -- reading ---------------------------------------------------------------------------------

    def get(self, job_id: str) -> dict | None:
        """The full job record (plus stalled/resumable), or None for an unknown or malformed id."""
        if not valid_job_id(job_id):
            return None
        record, local = self._record(job_id)
        return None if record is None else self._view(record, local=local)

    def events_since(self, job_id: str, cursor: int = 0) -> dict | None:
        """What a poller needs: status, the events after cursor, the new cursor, result and error."""
        view = self.get(job_id)
        if view is None:
            return None
        try:
            cursor = max(0, int(cursor))
        except (TypeError, ValueError):
            cursor = 0
        kept = view.get("events") or []
        events = [e for e in kept if int(e.get("seq", 0)) > cursor]
        first = int(kept[0].get("seq", 1)) if kept else int(view.get("seq") or 0) + 1
        return {
            "id": view["id"],
            "kind": view.get("kind"),
            "key": view.get("key"),
            "title": view.get("title"),
            "status": view.get("status"),
            "events": events,
            "cursor": max([cursor] + [int(e.get("seq", 0)) for e in events]),
            "truncated": cursor + 1 < first,
            "result": view.get("result"),
            "error": view.get("error"),
            "attempts": view.get("attempts"),
            "resumable": view.get("resumable"),
            "stalled": view.get("stalled"),
            "updated": view.get("updated"),
        }

    def active(self, key: str | None = None, *, include_stalled: bool = False) -> list[dict]:
        """Live jobs this process runs (another instance's job is found from its id or its key's lock)."""
        with self._mutex:
            runs = list(self._runs.values())
        rows = []
        for run in runs:
            if run.finished.is_set() or run.superseded:
                continue
            with run.lock:
                if key is None or run.record.get("key") == key:
                    rows.append(self._view(run.record, local=True))
        return sorted(rows, key=lambda row: -float(row.get("created") or 0))

    def join(self, job_id: str, timeout: float | None = None) -> bool:
        run = self._local(job_id)
        return True if run is None else run.finished.wait(timeout)

    def follow(self, job_id: str, cursor: int = 0, timeout: float | None = None) -> Iterator[dict]:
        """Yield events after cursor as they arrive, then {"type": "end", "job": <summary>}.

        Live for a job running in this process; for any other job it yields what is stored and ends.
        timeout (real seconds) stops the stream without the end item; the client then polls or follows again.
        """
        run = self._local(job_id) if valid_job_id(job_id) else None
        try:
            cursor = max(0, int(cursor))
        except (TypeError, ValueError):
            cursor = 0
        if run is None or run.superseded:
            view = self.events_since(job_id, cursor)
            if view is None:
                return
            yield from view["events"]
            yield {"type": "end", "job": {k: v for k, v in view.items() if k != "events"}}
            return
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with run.cond:
                new = [copy.deepcopy(e) for e in run.record.get("events") or [] if int(e["seq"]) > cursor]
                ended = run.finished.is_set() or run.superseded
                if not new and not ended:
                    wait = 1.0 if deadline is None else min(1.0, deadline - time.monotonic())
                    if wait <= 0:
                        return
                    run.cond.wait(wait)
                    continue
            for event in new:
                cursor = int(event["seq"])
                yield event
            if ended:
                with run.lock:
                    view = self._view(run.record, local=not run.superseded)
                summary = {k: view.get(k) for k in ("id", "kind", "key", "title", "status", "result", "error", "attempts", "resumable", "stalled", "updated")}
                summary["cursor"] = cursor
                yield {"type": "end", "job": summary}
                return

    def prune(self, older_than: float = 0) -> int:
        """Nothing to prune: locks are deleted when jobs end, and records ride on cloud saves."""
        return 0

    # -- locks -----------------------------------------------------------------------------------

    def _lock_body(self, record: dict) -> bytes:
        keep = ("v", "id", "kind", "key", "title", "params", "status", "created", "updated", "deadline", "instance", "attempts")
        return json.dumps({name: record.get(name) for name in keep}).encode("utf-8")

    def _holder_view(self, holder: dict) -> dict | None:
        """The lock holder as a view while it holds the key (running, stalled and not taken over yet, or paused
        recently), else None (the lock is a leftover and may be taken)."""
        final = self._final(str(holder.get("id") or ""))
        if final is not None and int(final.get("attempts") or 0) >= int(holder.get("attempts") or 0):
            if final.get("status") == "paused" and self.clock() - float(final.get("updated") or 0) < PAUSED_HOLD_SECONDS:
                return self._view(final, local=False)
            return None
        return self._view(holder, local=False)

    def _take(self, record: dict, *, replace_id: str | None = None) -> dict | None:
        """Create the lock for record's key. None when taken; else the holder's view (the caller attaches).
        replace_id: the job being resumed, whose own older lock may be replaced."""
        name = self._lock_name(record["id"])
        body = self._lock_body(record)
        for _round in range(4):
            try:
                self.store.put(name, body, content_type="application/json")
                return None
            except ObjectExists:
                pass
            holder = self._read(name)
            if holder is None:
                continue  # deleted meanwhile: try again
            if replace_id is not None and holder.get("id") == replace_id:
                if int(holder.get("attempts") or 0) >= int(record["attempts"]):
                    return self._view(holder, local=False)  # another instance resumed it first
                self.store.delete([name])
                continue
            view = self._holder_view(holder)
            if view is not None:
                return view
            self.store.delete([name])  # a leftover lock of a finished or abandoned job
        raise StoreError("could not take the lock")

    def _release(self, record: dict) -> None:
        name = self._lock_name(record["id"])
        try:
            holder = self._read(name)
            if holder is not None and holder.get("id") == record["id"] and int(holder.get("attempts") or 0) <= int(record.get("attempts") or 0):
                self.store.delete([name])
        except StoreError:
            pass  # a leftover lock is taken over once its job reads as finished

    # -- running ---------------------------------------------------------------------------------

    def start(
        self,
        kind: str,
        key: str,
        title: str,
        work: Callable[[JobContext], dict | None],
        *,
        params: dict | None = None,
        error_text: Callable[[BaseException], str] | None = None,
        background: bool = True,
    ) -> dict:
        """Start a job for key, or attach to the live job already holding key (then the record has attached=True)."""
        with self._key_lock(key):
            with self._mutex:
                local = [r for r in self._runs.values() if r.record.get("key") == key]
            for run in local:
                if not run.finished.is_set() and not run.superseded:
                    with run.lock:
                        return {**self._view(run.record, local=True), "attached": True}
            now = self.clock()
            record = {
                "v": 2,
                "id": new_job_id(key),
                "kind": kind,
                "key": key,
                "title": self.scrub(title),
                "params": _json_copy(params or {}),
                "status": "running",
                "created": now,
                "updated": now,
                "deadline": now + self.budget_seconds + STALL_GRACE_SECONDS,
                "instance": self.instance,
                "attempts": 1,
                "seq": 0,
                "events": [],
                "result": None,
                "error": None,
                "checkpoint": {},
            }
            holder = self._take(record)
            if holder is not None:
                return {**holder, "attached": True}
            run = _Run(record, started=now)
            with self._mutex:
                self._runs[record["id"]] = run
        return self._launch(run, work, error_text, background)

    def resume(
        self,
        job_id: str,
        work: Callable[[JobContext], dict | None],
        *,
        error_text: Callable[[BaseException], str] | None = None,
        background: bool = True,
    ) -> dict | None:
        """Continue a paused or stalled job. Live jobs come back with attached=True, finished ones unchanged."""
        if not valid_job_id(job_id):
            return None
        run = self._local(job_id)
        if run is not None and not run.finished.is_set() and not run.superseded:
            with run.lock:
                return {**self._view(run.record, local=True), "attached": True}
        record, local = self._record(job_id)
        if record is None:
            return None
        view = self._view(record, local=local)
        if record.get("status") in FINISHED:
            return view
        if record.get("status") in LIVE and not view["stalled"]:
            return {**view, "attached": True}
        if not view["resumable"]:
            return view
        key = str(record.get("key") or "")
        with self._key_lock(key):
            now = self.clock()
            fresh = copy.deepcopy(record)
            fresh.update({
                "status": "running",
                "updated": now,
                "deadline": now + self.budget_seconds + STALL_GRACE_SECONDS,
                "instance": self.instance,
                "attempts": int(record.get("attempts") or 1) + 1,
                "result": None,
                "error": None,
            })
            if record.get("status") != "paused":
                fresh["checkpoint"] = {}  # a stalled runner never stored one
            fresh.setdefault("events", [])
            fresh.setdefault("seq", max([0] + [int(e.get("seq", 0)) for e in fresh["events"]]))
            holder = self._take(fresh, replace_id=job_id)
            if holder is not None:
                return {**holder, "attached": True}
            run = _Run(fresh, started=now)
            with self._mutex:
                self._runs[job_id] = run
        return self._launch(run, work, error_text, background)

    def _launch(self, run: _Run, work, error_text, background: bool) -> dict:
        if background:
            threading.Thread(target=self._execute, args=(run, work, error_text), name=f"job-{run.record['id']}", daemon=True).start()
        else:
            self._execute(run, work, error_text)
        with run.lock:
            return self._view(run.record, local=True)

    def _emit(self, run: _Run, event: dict) -> dict:
        clean = _scrub_all(dict(event), self.scrub)
        with run.cond:
            run.record["seq"] = int(run.record.get("seq") or 0) + 1
            clean["seq"] = run.record["seq"]
            clean.setdefault("time", self.clock())
            try:
                clean = _json_copy(clean)
            except (TypeError, ValueError):
                clean = {"type": "note", "message": self.scrub(str(event))[:500], "seq": clean["seq"]}
            events = run.record.setdefault("events", [])
            events.append(clean)
            if len(events) > MAX_EVENTS:
                del events[: len(events) - MAX_EVENTS]
            run.record["updated"] = self.clock()
            run.cond.notify_all()
        return clean

    def _execute(self, run: _Run, work, error_text) -> None:
        ctx = JobContext(self, run)
        status, fields = "done", {}
        try:
            result = work(ctx)
            if result is None:
                result = {}
            elif not isinstance(result, dict):
                result = {"value": result}
            fields["result"] = _json_copy(result)
        except JobPaused:
            status = "paused"
        except Exception as exc:  # noqa: BLE001 - any failure of the work becomes the job's error
            status = "error"
            try:
                text = (error_text or default_error_text)(exc)
            except Exception:  # noqa: BLE001
                text = default_error_text(exc)
            fields["error"] = self.scrub(str(text))
        with run.cond:
            if status == "paused":
                try:
                    run.record["checkpoint"] = _json_copy(ctx.checkpoint)
                except (TypeError, ValueError):
                    pass
            run.record.update(fields)
            run.record["status"] = status
            run.record["updated"] = self.clock()
            stored = copy.deepcopy(run.record)
        stored["events"] = stored.get("events", [])[-STORED_EVENTS:]
        try:
            self._store_final(run, stored)
        finally:
            with run.cond:
                run.finished.set()
                run.cond.notify_all()

    def _store_final(self, run: _Run, record: dict) -> None:
        """Make the outcome visible to every instance, then release the lock (a paused job keeps it)."""
        holder = self._read(self._lock_name(record["id"]))
        if holder is not None and holder.get("id") == record["id"] and int(holder.get("attempts") or 0) > int(record.get("attempts") or 0):
            run.superseded = True  # resumed elsewhere after we looked stalled: that runner owns the record now
            return
        kept = False
        if self.on_finish is not None:
            try:
                kept = bool(self.on_finish(record))
            except Exception as exc:  # noqa: BLE001 - fall back to the record object below
                self.last_error = f"Could not save the job with the project: {type(exc).__name__}"
        if not kept:
            try:
                self.store.put(self._record_name(record["id"]), json.dumps(record).encode("utf-8"), overwrite=True, content_type="application/json")
            except StoreError as exc:
                self.last_error = f"Could not save job state: {exc}"
        if record.get("status") in FINISHED:
            self._release(record)
