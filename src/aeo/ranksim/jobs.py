"""Background jobs for Ranksim: analyses and pulls with ids, progress events, per-site locks and checkpoints.

A hosted Ranksim runs as many short-lived serverless instances (Vercel, 300 s per request at most). Long work is a job:

- start() creates a job record, takes the lock for its key (for example "site:example.com") and runs the work in a
  daemon thread. The browser polls events_since(job_id, cursor) for progress and can reattach after a refresh, or from
  another instance, because every record is persisted to the object store (objects.py).
- Records are never overwritten in place. Each persist writes a new immutable name under
  <prefix>jobs/<id>/ and readers list that folder to find the newest (Blob reads of an overwritten name may be served
  stale from a cache; lists are not cached). The newest KEEP_SNAPSHOTS are kept, older ones deleted.
- Work checks ctx.should_pause() and raises JobPaused at a checkpoint before the request limit. The job is then
  "paused" and resume() runs the work again (possibly on another instance) with the saved checkpoint.
- A running job heartbeats (persists) every HEARTBEAT_SECONDS. A queued or running job whose record is older than
  STALE_SECONDS lost its runner (the instance was frozen or killed) and is reported stalled and resumable.
- Locks across instances are immutable entries <prefix>locks/<sha256(key)[:24]>/<new_name>--<job_id>.<attempt>.
  To acquire: write an own entry, list the folder, and the earliest entry whose job is live wins. Losers delete their
  entry. Entries of dead jobs are deleted when seen.
- A runner whose job was resumed elsewhere (a higher attempt was persisted) notices on its next persist, stops
  writing and asks its work to pause, so a thawed old instance cannot overwrite the new runner's progress.

Nothing here touches the network itself; the store decides where records live (MemoryStore, DirStore, BlobStore).
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
from typing import Callable, Iterator

from aeo.ranksim.objects import DirStore, MemoryStore, ObjectStore, StoreError, stamp_of

STALE_SECONDS = 45.0  # a queued/running record older than this lost its runner
PAUSED_HOLD_SECONDS = 600.0  # a paused job keeps its lock this long, then an abandoned pause stops blocking the key
HEARTBEAT_SECONDS = 10.0
PERSIST_EVERY_SECONDS = 1.5  # plain events are persisted at most this often
SAFETY_SECONDS = 25.0  # should_pause() turns true when less than this is left of the budget
DEFAULT_BUDGET_SECONDS = 240.0
MAX_EVENTS = 500
MAX_ATTEMPTS = 30
KEEP_SNAPSHOTS = 2
PRUNE_AFTER_SECONDS = 7 * 24 * 3600.0
PRUNE_EVERY_SECONDS = 3600.0
ORPHAN_GRACE_SECONDS = 60.0  # a lock entry whose job record is missing counts as live this long (record still landing)
FLUSH_POLL_SECONDS = 0.5  # how often (real time) the flusher thread looks at a running job

LIVE = ("queued", "running")
FINISHED = ("done", "error")
_ID_RE = re.compile(r"^[A-Za-z0-9]{16,64}$")
_ID_ALPHABET = string.ascii_letters + string.digits


class JobPaused(BaseException):
    """Raised by work to stop at a checkpoint. The job becomes "paused" and resume() continues it.

    A BaseException on purpose: crawl code catches Exception per host so one failing host never ends a pull, and a
    pause must pass through that and stop the whole pull (pages fetched so far are already stored)."""


def valid_job_id(job_id: object) -> bool:
    """Job ids are letters and digits only, so they can never smuggle a path, a separator or a lock suffix."""
    return isinstance(job_id, str) and bool(_ID_RE.match(job_id))


def new_job_id() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(20))


def default_error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _scrub_all(value, scrub):
    """Apply scrub to every string inside value (dicts, lists and tuples included)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {(scrub(k) if isinstance(k, str) else k): _scrub_all(v, scrub) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_all(v, scrub) for v in value]
    return value


def _json_copy(value):
    """A deep copy that is guaranteed to be JSON (raises TypeError/ValueError otherwise)."""
    return json.loads(json.dumps(value))


class _Run:
    """One attempt of a job running in this process."""

    def __init__(self, record: dict, started: float, min_stamp: int = 0) -> None:
        self.record = record
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.io_lock = threading.Lock()  # one persist at a time, so snapshot names land in order
        self.written: list[str] = []  # snapshot names this run wrote, oldest first
        self.last_stamp = min_stamp  # every new snapshot name sorts after this
        self.checked: dict[str, int] = {}  # foreign snapshot name -> its attempts (read once)
        self.last_persist = float("-inf")
        self.dirty = False
        self.superseded = False
        self.finished = threading.Event()
        self.started = started


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
        """Store data (default: ctx.checkpoint) as the job's checkpoint and persist right away."""
        if data is None:
            data = self.checkpoint
        clean = _json_copy(data)
        self.checkpoint = data
        with self._run.lock:
            self._run.record["checkpoint"] = clean
        self._registry._persist(self._run, force=True)

    def time_left(self) -> float:
        reg = self._registry
        return reg.budget_seconds - (reg.clock() - self._run.started)

    def should_pause(self) -> bool:
        """True when the work should checkpoint and raise JobPaused now (budget nearly spent, or resumed elsewhere)."""
        return self._run.superseded or self.time_left() < self._registry.safety_seconds


class JobRegistry:
    """Jobs persisted in an object store. One registry per process; several processes may share one store."""

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
        lock_settle_seconds: float | None = None,
        frugal: bool | None = None,
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
        # Frugal mode (a remote store, which on Vercel Hobby has a small monthly operation cap): persist plain events
        # every 30 s, heartbeat every 45 s, call a runner stalled after 150 s, sweep once a day, and skip the lock
        # settle re-list (two starts of one site in the same instant may then both run; nothing is lost).
        if frugal is None:
            frugal = not isinstance(self.store, (MemoryStore, DirStore))
        self.frugal = bool(frugal)
        self.stale_seconds = 150.0 if self.frugal else STALE_SECONDS
        self.heartbeat_seconds = 45.0 if self.frugal else HEARTBEAT_SECONDS
        self.persist_every_seconds = 30.0 if self.frugal else PERSIST_EVERY_SECONDS
        self.prune_every_seconds = 24 * 3600.0 if self.frugal else PRUNE_EVERY_SECONDS
        if lock_settle_seconds is None:
            lock_settle_seconds = 0.0 if self.frugal else 0.02
        self.lock_settle_seconds = float(lock_settle_seconds)
        self.instance = instance or f"{os.getpid()}-{secrets.token_hex(4)}"
        self.last_error = ""
        self._mutex = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._runs: dict[str, _Run] = {}  # job id -> the latest attempt run by this process
        self._lock_names: dict[str, str] = {}  # job id -> the lock entry this process wrote for it
        self._swept = clock()  # first sweep an hour after start, not on the first request

    # -- names -----------------------------------------------------------------------------------

    def _job_dir(self, job_id: str) -> str:
        return f"{self.prefix}jobs/{job_id}/"

    def _lock_dir(self, key: str) -> str:
        return f"{self.prefix}locks/{hashlib.sha256(key.encode()).hexdigest()[:24]}/"

    @staticmethod
    def _parse_lock(name: str) -> tuple[str, int] | None:
        """(job id, attempt) from a lock entry name, or None when it is not one."""
        tail = name.rsplit("/", 1)[-1]
        if "--" not in tail:
            return None
        rest = tail.split("--", 1)[1]
        job_id, _, attempt = rest.rpartition(".")
        if not valid_job_id(job_id) or not attempt.isdigit():
            return None
        return job_id, int(attempt)

    def _age(self, name: str) -> float:
        """Seconds since the time encoded in an object name, measured on this registry's clock."""
        stamp = stamp_of(name)
        return self.clock() - stamp / 1e9 if stamp else float("inf")

    # -- liveness --------------------------------------------------------------------------------

    def _is_live(self, record: dict) -> bool:
        """Does this job still hold its key? Running and fresh, or paused not too long ago."""
        status = record.get("status")
        age = self.clock() - float(record.get("updated") or 0)
        if status in LIVE:
            return age <= self.stale_seconds
        if status == "paused":
            return age <= PAUSED_HOLD_SECONDS
        return False

    def _local(self, job_id: str) -> _Run | None:
        """The run of job_id in this process while it is in flight (not finished, not superseded)."""
        with self._mutex:
            run = self._runs.get(job_id)
        if run is None or run.finished.is_set() or run.superseded:
            return None
        return run

    def _view(self, record: dict, *, local: bool = False) -> dict:
        """A copy of record plus the derived stalled/resumable flags."""
        out = copy.deepcopy(record)
        status = out.get("status")
        stalled = (not local) and status in LIVE and self.clock() - float(out.get("updated") or 0) > self.stale_seconds
        out["stalled"] = bool(stalled)
        out["resumable"] = bool((status == "paused" or stalled) and int(out.get("attempts") or 0) < MAX_ATTEMPTS)
        return out

    # -- reading ---------------------------------------------------------------------------------

    def _stored(self, job_id: str) -> tuple[dict | None, list[str]]:
        """The newest stored snapshot of job_id and the folder listing it came from."""
        names = [n for n in self.store.list(self._job_dir(job_id)) if n.endswith(".json")]
        for name in reversed(names):
            try:
                return json.loads(self.store.get(name)), names
            except KeyError:
                continue  # pruned between list and get: try the one before
            except ValueError:
                continue  # a torn or foreign object; an older snapshot is better than nothing
        return None, names

    def _record(self, job_id: str) -> tuple[dict | None, bool]:
        """(record, is_local): memory while this process runs the job, else the newest stored snapshot."""
        run = self._local(job_id)
        if run is not None:
            with run.lock:
                return copy.deepcopy(run.record), True
        with self._mutex:
            done = self._runs.get(job_id)
        if done is not None and done.finished.is_set() and not done.superseded:
            with done.lock:  # finished here: memory holds the final record, no storage read needed
                return copy.deepcopy(done.record), False
        record, _ = self._stored(job_id)
        if record is None:
            with self._mutex:
                run = self._runs.get(job_id)
            if run is not None:  # the store lost it (or never got it); this process still knows the outcome
                with run.lock:
                    return copy.deepcopy(run.record), False
        return record, False

    def get(self, job_id: str) -> dict | None:
        """The full job record (plus stalled/resumable), or None for an unknown or malformed id."""
        if not valid_job_id(job_id):
            return None
        record, local = self._record(job_id)
        return None if record is None else self._view(record, local=local)

    def events_since(self, job_id: str, cursor: int = 0) -> dict | None:
        """What a poller needs: status, the events after cursor, the new cursor, result and error."""
        if not valid_job_id(job_id):
            return None
        record, local = self._record(job_id)
        if record is None:
            return None
        view = self._view(record, local=local)
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
            "cursor": max([cursor] + [int(e["seq"]) for e in events]),
            "truncated": cursor < first - 1,  # older events were dropped from the capped list
            "result": view.get("result"),
            "error": view.get("error"),
            "attempts": view.get("attempts"),
            "resumable": view["resumable"],
            "stalled": view["stalled"],
            "updated": view.get("updated"),
        }

    def active(self, key: str | None = None, *, include_stalled: bool = False) -> list[dict]:
        """Live jobs (queued, running, or paused recently), optionally only those for key. Newest first."""
        if key is not None:
            names = self.store.list(self._lock_dir(key))
            ids = []
            for name in names:
                parsed = self._parse_lock(name)
                if parsed and parsed[0] not in ids:
                    ids.append(parsed[0])
        else:
            newest: dict[str, str] = {}
            for name in self.store.list(f"{self.prefix}jobs/"):
                parts = name[len(self.prefix) + len("jobs/"):].split("/")
                if len(parts) == 2 and valid_job_id(parts[0]):
                    newest[parts[0]] = max(newest.get(parts[0], ""), name)
            # A live job persisted within the paused hold; skip reading anything older than that.
            ids = [jid for jid, name in newest.items() if self._age(name) <= PAUSED_HOLD_SECONDS + 60]
        found = []
        for jid in ids:
            record, local = self._record(jid)
            if record is None or (key is not None and record.get("key") != key):
                continue
            view = self._view(record, local=local)
            if self._is_live(record) or local or (include_stalled and view["stalled"]):
                found.append({k: view.get(k) for k in ("id", "kind", "key", "title", "status", "updated", "attempts", "stalled", "resumable", "instance")})
        found.sort(key=lambda j: float(j.get("updated") or 0), reverse=True)
        return found

    # -- writing ---------------------------------------------------------------------------------

    def _snapshot_name(self, run: _Run) -> str:
        """A new immutable name that sorts after every snapshot this run (or the attempt it resumed) wrote."""
        stamp = max(time.time_ns(), run.last_stamp + 1)
        run.last_stamp = stamp
        return f"{self._job_dir(run.record['id'])}{stamp:020d}-{secrets.token_hex(3)}.json"

    def _persist(self, run: _Run, *, force: bool = False) -> bool:
        """Write the run's record as a new snapshot. Throttled unless force. Returns True when a snapshot was written."""
        with run.lock:
            if run.superseded:
                return False
            if not force and self.clock() - run.last_persist < self.persist_every_seconds:
                run.dirty = True
                return False
        with run.io_lock:
            with run.lock:
                if run.superseded:
                    return False
                now = self.clock()
                run.record["updated"] = now
                run.last_persist = now
                run.dirty = False
                body = json.dumps(run.record).encode()
                attempts = int(run.record.get("attempts") or 0)
                name = self._snapshot_name(run)
            try:
                self.store.put(name, body, content_type="application/json")
                run.written.append(name)
                self._after_write(run, attempts)
            except StoreError as exc:
                # The job keeps running; the next persist (or heartbeat) tries again.
                self.last_error = f"Could not save job state: {exc}"
                with run.lock:
                    run.dirty = True
                return False
        return True

    def _after_write(self, run: _Run, attempts: int) -> None:
        """List the job's snapshots: step aside if a later attempt runs elsewhere, else prune to KEEP_SNAPSHOTS.

        Frugal mode lists at most once per half stale interval (a takeover only happens after a stall that long) and
        prunes its own older snapshots in between without listing (deletes cost nothing)."""
        if self.frugal:
            now = self.clock()
            if now - getattr(run, "super_checked", -1e18) < self.stale_seconds / 2:
                old = run.written[: max(0, len(run.written) - KEEP_SNAPSHOTS)]
                if old:
                    self.store.delete(old)
                    run.written = run.written[len(old):]
                return
            run.super_checked = now
        names = [n for n in self.store.list(self._job_dir(run.record["id"])) if n.endswith(".json")]
        first = stamp_of(run.written[0]) if run.written else 0
        mine = set(run.written)
        for name in reversed(names):
            if name in mine or stamp_of(name) < first:
                continue
            if name not in run.checked:
                try:
                    run.checked[name] = int(json.loads(self.store.get(name)).get("attempts") or 0)
                except (KeyError, ValueError):
                    run.checked[name] = 0
            if run.checked[name] > attempts:
                with run.cond:
                    run.superseded = True
                    run.cond.notify_all()
                self.store.delete(list(run.written))  # so readers see the new runner's snapshots, not ours
                return
        # Keep our newest snapshot and the KEEP_SNAPSHOTS - 1 before it. Names sorting after ours belong to another
        # runner (a superseding one, or an old one about to notice it was superseded) and are left alone.
        latest = run.written[-1]
        before = [n for n in names if n < latest]
        old = before[: max(0, len(before) - (KEEP_SNAPSHOTS - 1))]
        if old:
            self.store.delete(old)
            gone = set(old)
            run.written = [n for n in run.written if n not in gone]

    def _emit(self, run: _Run, event: dict) -> dict:
        clean = _scrub_all(dict(event), self.scrub)
        with run.cond:
            run.record["seq"] = int(run.record.get("seq") or 0) + 1
            clean["seq"] = run.record["seq"]
            clean.setdefault("time", self.clock())
            try:
                clean = _json_copy(clean)
            except (TypeError, ValueError):
                clean = {"seq": clean["seq"], "time": clean["time"], "type": "event", "text": self.scrub(str(event))[:300]}
            events = run.record.setdefault("events", [])
            events.append(clean)
            if len(events) > MAX_EVENTS:
                del events[: len(events) - MAX_EVENTS]
            run.dirty = True
            run.cond.notify_all()
        return clean

    def _set_status(self, run: _Run, status: str, **fields) -> None:
        """Change status (with a status event) and persist immediately."""
        with run.cond:
            run.record["status"] = status
            run.record.update(fields)
        self._emit(run, {"type": "status", "status": status})
        self._persist(run, force=True)

    # -- locks -----------------------------------------------------------------------------------

    def _key_lock(self, key: str) -> threading.Lock:
        with self._mutex:
            return self._key_locks.setdefault(key, threading.Lock())

    def _acquire(self, key: str, job_id: str, attempt: int) -> str | None:
        """Take the lock for key on behalf of job_id's attempt. Returns None when won, else the holding job's id."""
        folder = self._lock_dir(key)
        mine = f"{folder}{time.time_ns():020d}-{secrets.token_hex(3)}--{job_id}.{attempt}"
        self.store.put(mine, json.dumps({"job": job_id, "attempt": attempt, "instance": self.instance}).encode(), content_type="application/json")
        try:
            names = self.store.list(folder)
        except StoreError:
            self.store.delete([mine])
            raise
        winner, dead = self._scan(names, mine, job_id, attempt)
        if winner is None and self.lock_settle_seconds > 0:
            # An entry named (stamped) before ours may still be landing: its put started earlier but finished later,
            # so our first list missed it. Look once more after the usual write latency; the earliest live entry wins.
            time.sleep(self.lock_settle_seconds)
            try:
                names = self.store.list(folder)
            except StoreError:
                self.store.delete([mine])
                raise
            winner, more = self._scan(names, mine, job_id, attempt)
            dead = sorted(set(dead) | set(more))
        if winner is None:
            with self._mutex:
                self._lock_names[job_id] = mine  # released by name later, without a list
        try:
            if winner is not None:
                self.store.delete([mine])
            if dead:
                self.store.delete(dead)
        except StoreError:
            pass  # a leftover entry of a dead job is skipped (and retried) by the next acquirer
        return winner

    def _scan(self, names: list[str], mine: str, job_id: str, attempt: int) -> tuple[str | None, list[str]]:
        """Walk lock entries oldest first. Returns (holder job id or None when ours is the earliest live, dead entries)."""
        winner: str | None = None
        dead: list[str] = []
        for name in names:
            if name == mine:
                break  # every earlier entry is dead: ours wins
            parsed = self._parse_lock(name)
            if parsed is None:
                continue
            jid, att = parsed
            if jid == job_id:
                if att < attempt:
                    dead.append(name)  # an earlier attempt of the job we are resuming
                    continue
                winner = jid  # another resumer of this same attempt got there first
                break
            try:
                record, _ = self._record(jid)
            except StoreError:
                winner = jid  # cannot tell: do not take a lock that may be held
                break
            if record is None:
                if self._age(name) < ORPHAN_GRACE_SECONDS:
                    winner = jid
                    break
                dead.append(name)
            elif self._is_live(record):
                winner = jid
                break
            else:
                dead.append(name)
        return winner, dead

    def _release(self, key: str, job_id: str) -> None:
        """Delete every lock entry of job_id for key (best effort)."""
        with self._mutex:
            known = self._lock_names.pop(job_id, None)
        try:
            if known is not None and self.frugal:
                self.store.delete([known])  # deletes are free; a list is not
                return
            folder = self._lock_dir(key)
            names = [n for n in self.store.list(folder) if (self._parse_lock(n) or ("",))[0] == job_id]
            if names:
                self.store.delete(names)
        except StoreError:
            pass  # the job is finished, so its entry counts as dead and the next acquirer deletes it

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
        """Start a job for key, or attach to the live job already holding key (then the record has attached=True).

        With background=False the work runs in the calling thread and the finished record is returned.
        """
        self._maybe_sweep()
        with self._key_lock(key):
            with self._mutex:
                local = [r for r in self._runs.values() if r.record.get("key") == key]
            for run in local:
                if not run.finished.is_set() and not run.superseded:
                    with run.lock:
                        return {**self._view(run.record, local=True), "attached": True}
            now = self.clock()
            record = {
                "v": 1,
                "id": new_job_id(),
                "kind": kind,
                "key": key,
                "title": self.scrub(title),
                "params": _json_copy(params or {}),
                "status": "queued",
                "created": now,
                "updated": now,
                "instance": self.instance,
                "attempts": 1,
                "seq": 0,
                "events": [],
                "result": None,
                "error": None,
                "checkpoint": {},
            }
            run = _Run(record, started=now)
            run.super_checked = self.clock()  # a new run was just checked (frugal mode lists later)
            # The record lands before the lock entry, so anyone who sees our entry can also read our record.
            if not self._persist(run, force=True):
                raise StoreError(self.last_error or "could not save job")
            try:
                holder = self._acquire(key, record["id"], 1)
            except StoreError:
                self.store.delete(list(run.written))
                raise
            if holder is not None:
                self.store.delete(list(run.written))
                held, local_flag = self._record(holder)
                if held is None:
                    raise StoreError("lock holder vanished")
                return {**self._view(held, local=local_flag), "attached": True}
            with self._mutex:
                self._runs[record["id"]] = run
            self._set_status(run, "running")
        return self._launch(run, work, error_text, background)

    def resume(
        self,
        job_id: str,
        work: Callable[[JobContext], dict | None],
        *,
        error_text: Callable[[BaseException], str] | None = None,
        background: bool = True,
    ) -> dict | None:
        """Continue a paused or stalled job from its checkpoint. Live jobs come back with attached=True, finished ones unchanged."""
        if not valid_job_id(job_id):
            return None
        local = self._local(job_id)
        if local is not None:
            with local.lock:
                return {**self._view(local.record, local=True), "attached": True}
        record, names = self._stored(job_id)
        if record is None:
            return None
        key = str(record.get("key") or "")
        with self._key_lock(key):
            again = self._local(job_id)
            if again is not None:  # another thread here resumed it meanwhile
                with again.lock:
                    return {**self._view(again.record, local=True), "attached": True}
            view = self._view(record)
            if record.get("status") in FINISHED:
                return view
            if record.get("status") in LIVE and not view["stalled"]:
                return {**view, "attached": True}
            attempt = int(record.get("attempts") or 1) + 1
            if attempt > MAX_ATTEMPTS:
                return view  # resumable is False here; the caller shows the job as stuck
            holder = self._acquire(key, job_id, attempt)
            if holder is not None:
                held, held_local = self._record(holder)
                return {**self._view(held or record, local=held_local), "attached": True}
            now = self.clock()
            record.update({"attempts": attempt, "instance": self.instance, "error": None, "result": None})
            # Our snapshots must sort after every earlier one, even if this machine's clock is behind.
            run = _Run(record, started=now, min_stamp=max([stamp_of(n) for n in names] + [0]))
            run.super_checked = self.clock()  # a new run was just checked (frugal mode lists later)
            with self._mutex:
                self._runs[job_id] = run
            self._emit(run, {"type": "resumed", "attempt": attempt})
            self._set_status(run, "running")
        return self._launch(run, work, error_text, background)

    def _launch(self, run: _Run, work, error_text, background: bool) -> dict:
        flusher = threading.Thread(target=self._flush_loop, args=(run,), name=f"ranksim-job-flush-{run.record['id'][:6]}", daemon=True)
        flusher.start()
        if background:
            thread = threading.Thread(target=self._execute, args=(run, work, error_text), name=f"ranksim-job-{run.record['id'][:6]}", daemon=True)
            thread.start()
            with run.lock:
                return self._view(run.record, local=True)
        self._execute(run, work, error_text)
        with run.lock:
            return self._view(run.record, local=not run.superseded)

    def _execute(self, run: _Run, work, error_text) -> None:
        """Run one attempt of work and record how it ended."""
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
            except Exception:  # noqa: BLE001 - a broken formatter must not hide the failure
                text = default_error_text(exc)
            fields["error"] = self.scrub(str(text))
        try:
            if not run.superseded:  # a superseded run was resumed elsewhere; its new runner owns the record
                if status == "paused":
                    # Keep the newest checkpoint even if the work forgot to save it before raising JobPaused.
                    try:
                        with run.lock:
                            run.record["checkpoint"] = _json_copy(ctx.checkpoint)
                    except (TypeError, ValueError):
                        pass
                self._set_status(run, status, **fields)
                if status in FINISHED and not run.superseded:
                    self._release(str(run.record.get("key") or ""), run.record["id"])
        finally:
            with run.cond:
                run.finished.set()
                run.cond.notify_all()

    def _flush_loop(self, run: _Run) -> None:
        """Persist throttled events and heartbeat while the attempt runs."""
        while not run.finished.is_set() and not run.superseded:
            with run.cond:
                run.cond.wait(FLUSH_POLL_SECONDS)
                if run.finished.is_set() or run.superseded:
                    return
                since = self.clock() - run.last_persist
                due = (run.dirty and since >= self.persist_every_seconds) or since >= self.heartbeat_seconds
            if due:
                self._persist(run, force=True)

    def join(self, job_id: str, timeout: float | None = None) -> bool:
        """Wait (real time) for this process's run of job_id to end. True when it ended (or is not running here)."""
        with self._mutex:
            run = self._runs.get(job_id)
        return True if run is None else run.finished.wait(timeout)

    def follow(self, job_id: str, cursor: int = 0, timeout: float | None = None) -> Iterator[dict]:
        """Yield events after cursor as they arrive, then {"type": "end", "job": <events_since view>}.

        Live streaming works for a job running in this process; for any other job it yields what is stored and ends.
        timeout (real seconds) stops the stream without the end item; the client then polls or follows again.
        """
        run = self._local(job_id) if valid_job_id(job_id) else None
        try:
            cursor = max(0, int(cursor))
        except (TypeError, ValueError):
            cursor = 0
        if run is None:
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
                view = self.events_since(job_id, cursor) or {}
                yield {"type": "end", "job": {k: v for k, v in view.items() if k != "events"}}
                return

    # -- pruning ---------------------------------------------------------------------------------

    def _maybe_sweep(self) -> None:
        if self.clock() - self._swept < self.prune_every_seconds:
            return
        self._swept = self.clock()
        threading.Thread(target=self._sweep_quietly, name="ranksim-job-prune", daemon=True).start()

    def _sweep_quietly(self) -> None:
        try:
            self.prune()
        except StoreError:
            pass  # pruning saves storage; the next sweep tries again

    def prune(self, older_than: float = PRUNE_AFTER_SECONDS) -> int:
        """Delete jobs that are not live and were last written more than older_than seconds ago, plus old lock entries.

        Returns how many jobs were deleted.
        """
        by_job: dict[str, list[str]] = {}
        for name in self.store.list(f"{self.prefix}jobs/"):
            parts = name[len(self.prefix) + len("jobs/"):].split("/")
            if len(parts) == 2 and valid_job_id(parts[0]):
                by_job.setdefault(parts[0], []).append(name)
        removed = 0
        for job_id, names in by_job.items():
            if self._age(max(names)) <= older_than or self._local(job_id) is not None:
                continue
            record, _ = self._stored(job_id)
            if record is not None and self._is_live(record):
                continue
            self.store.delete(names)
            removed += 1
        old_locks = [n for n in self.store.list(f"{self.prefix}locks/") if self._age(n) > older_than]
        if old_locks:
            self.store.delete(old_locks)
        return removed


class ResumableFetch:
    """Fetch URLs one at a time inside a job, pausing before the budget runs out and skipping work done earlier.

    fetch(urls) is any batch fetcher returning FetchResult objects (loop.py); it is called with one URL at a time so a
    pause can come between any two pages. URLs finished in an earlier attempt are listed in the checkpoint under
    namespace; for those, lookup(url) (for example the page already stored in the database) supplies the result and the
    URL is fetched again only when lookup has nothing.
    """

    def __init__(self, ctx: JobContext, fetch: Callable[[list[str]], list], *, lookup: Callable[[str], object] | None = None,
                 namespace: str = "fetch", save_every: int = 10) -> None:
        self.ctx = ctx
        self.fetch = fetch
        self.lookup = lookup
        self.namespace = namespace
        self.save_every = max(1, int(save_every))
        self.fetched = 0  # network fetches made in this attempt
        self.reused = 0  # results taken from lookup instead

    def _state(self) -> dict:
        state = self.ctx.checkpoint.setdefault(self.namespace, {})
        state.setdefault("done", [])
        return state

    def __call__(self, urls: list[str]) -> list:
        state = self._state()
        done = set(state["done"])
        results = []
        unsaved = 0
        for url in urls:
            if url in done and self.lookup is not None:
                cached = self.lookup(url)
                if cached is not None:
                    results.append(cached)
                    self.reused += 1
                    continue
            if self.ctx.should_pause():
                self.ctx.save_checkpoint()
                raise JobPaused(f"paused before {len(urls) - len(results)} of {len(urls)} URLs")
            results.extend(self.fetch([url]))
            self.fetched += 1
            if url not in done:
                done.add(url)
                state["done"].append(url)
            unsaved += 1
            if unsaved >= self.save_every:
                self.ctx.save_checkpoint()
                unsaved = 0
        if unsaved:
            self.ctx.save_checkpoint()
        return results
