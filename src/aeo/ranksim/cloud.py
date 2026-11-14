"""Durable state for a hosted Ranksim (Vercel): projects, stored pages and trained engines in a private Blob store.

A serverless instance only has an empty, temporary /tmp. Ranksim keeps working files there as usual, and this module
mirrors them to a private Vercel Blob store:

  ranksim/v2/HEAD             the manifest (JSON): each project's newest save, the rank database's snapshot and the
                              deltas after it, shared login state (sign out everywhere, a global login lockout)
  ranksim/v2/s/<time>-<rand>  immutable save objects: the folders of the projects that changed, plus the database's
                              row-level delta (or a full snapshot when deltas pile up)

Blob operations are budgeted. Vercel Hobby allows 10,000 simple operations (head, get) and 2,000 advanced ones (put,
list) a month and blocks Blob for 30 days past a cap (deletes are free). So:

- An ordinary request makes no Blob call. An instance checks the manifest (one head) on a cold start, before a write,
  every REFRESH_EVERY_SECONDS, or when the browser's rs_sync cookie (the newest save it has seen) is newer than what
  the instance knows. That gives read-your-writes across instances without polling.
- A save with nothing changed makes no call. Otherwise: one put of the save object and one put of the manifest, with a
  head before each write of the manifest. Nothing lists: objects nobody references are deleted (free), and the
  database goes up as a small delta, not the whole file.

Merging (two writers never clobber each other):

- Projects: three-way merge at file level against the version both sides started from. Files only one side changed
  come from that side; a file both changed keeps the merging side's copy (the newer work). A deleted project is a
  save with no files (a tombstone).
- The rank database is shared by every project (pages and Google results are keyed by URL and search), so it is
  merged row by row (merge_sqlite, make_delta): rows added or removed on one side are applied on the other, a search's
  Google results move as a group (newest pull wins), and the hash-chained prediction ledger stays one valid chain.
- Put has no compare-and-swap, so the manifest is re-checked right before it is written; a save that lands in between
  is merged and the manifest written again.

Older formats. The first check of a store with no manifest imports the phase A layout (ranksim/v2/projects/...) or the
old single tarball (ranksim/state/), once. During the first deploy an old instance may still save an old-format
tarball; for LEGACY_WATCH_SECONDS after the migration every instance notices a newer one (one head per check) and
merges only what the old instance changed since the import (against ranksim/v2/legacy-base.json), so nothing is lost,
newer edits on the new side are kept, and importing the same tarball twice changes nothing. Old objects are never
deleted, so going back to an older deployment still finds them.

Disk. Vercel's /tmp holds 512 MB for everything. The working folder keeps the projects, the rank database and a
compact base (.cloud/base.rows: per row, a hash and the row compressed; about half the database's size), the last
state every instance agreed on: deltas are made against it and merges use it as the common ancestor. No sync or save
writes a second full copy of the database: catch-up reads each save straight from the store's stream in one pass, a
cold sync streams each save it needs to one file that is deleted once used and takes the snapshot as the working
database by a rename, deltas are found by scanning the working database once, a full snapshot for upload is taken in
memory, a compacting save also carries the delta so caught-up instances never download the snapshot, project files
are moved into place rather than copied, and SQLite sorts in memory. All temp files live in .cloud/tmp, are removed
on every failure path, and whatever a killed process left is removed at the next process's first storage call. One
sync or save runs at a time per working folder. A full disk is handled by removing temp files and retrying; if it is
still full, the local copy is dropped and loaded again from storage once. Measured (tests/test_disk.py, 30 MB of
projects): about 135 MB at rest and at every step's peak with a 70 MB database, 208 MB with a 120 MB one.

Nothing here runs unless BLOB_READ_WRITE_TOKEN is set (or a store is passed in, as the tests do).
"""

from __future__ import annotations

import contextlib
import errno
import gzip
import hashlib
import io
import json
import marshal
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from aeo.ranksim.objects import BlobStore, ObjectExists, ObjectStore, StoreError

ROOT_PREFIX = "ranksim/v2/"
CHAIN_PREFIX = ROOT_PREFIX + "c/"
SAVES_PREFIX = CHAIN_PREFIX
PREFIX = CHAIN_PREFIX
POINTER = ROOT_PREFIX + "HEAD"  # a recent chain number (a hint, written every POINTER_EVERY saves)
LEGACY_BASE = ROOT_PREFIX + "legacy-base.json"
LEGACY_PREFIX = "ranksim/state/"
LEGACY_POINTER = "ranksim/HEAD"

# Blob operation budget (Vercel Hobby: 10,000 simple and 2,000 advanced operations a month; Blob is blocked for 30
# days past a cap; deletes are free). Ordinary requests make no Blob call at all.
REFRESH_EVERY_SECONDS = 300.0  # an active instance looks for a newer save (one get) at most this often
HINT_MIN_SECONDS = 5.0
FAILURE_BACKOFF_SECONDS = 15.0  # after a failed look, ordinary requests wait this long before looking again  # a stale-hint cookie can force a look at most this often per instance
VERIFY_AFTER_SECONDS = 600.0  # an instance idle this long also reads the pointer, in case it slept through pruning
# After the migration, watch the old format's pointer for LEGACY_WATCH_SECONDS, one head at most every
# LEGACY_CHECK_SECONDS per instance. Two weeks, because a preview deployment that shares the production Blob store can
# migrate it well before production moves over, while the old production keeps saving in the old format.
LEGACY_WATCH_SECONDS = 14 * 24 * 3600
LEGACY_CHECK_SECONDS = 1800.0
POINTER_EVERY = 8  # the pointer is rewritten on every 8th save (an advanced op), so a cold start probes a few names
KEEP_CHAIN = 64  # saves kept behind the newest (older ones nobody references are deleted, which is free)
# A save is deleted only when it is KEEP_CHAIN behind AND was written more than PRUNE_AFTER_SECONDS ago. An instance
# that last looked longer ago than VERIFY_AFTER_SECONDS (well under this) reads the pointer and reloads from the newest
# manifest, so no instance can be caught between saves it has not applied and saves that are already gone.
PRUNE_AFTER_SECONDS = 3600
COMPACT_DELTAS = 15  # after this many database deltas the next save uploads a full snapshot
COMPACT_RATIO = 0.5  # or when the deltas add up to half the snapshot's size (counted from COMPACT_FLOOR_BYTES up)
COMPACT_FLOOR_BYTES = 4 * 1024 * 1024
CAS_ROUNDS = 6
JOBS_KEPT = 20  # finished or paused job records carried in the manifest
SKIP_PARTS = {"llm-cache", "__pycache__"}
VERSION_FILE = ".ranksim-version.json"
MANIFEST_FILE = "manifest.json"
LOOSE = "_files"  # pseudo project: files sitting directly in projects/
# Project folders, plus "_plugins" (workspace-wide plugin data: plugins.workspace_dir), saved with the same merge
# rules. Dot folders are never saved.
SLUG_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,199}|_plugins)$")


class CloudError(RuntimeError):
    pass


def chain_name(seq: int) -> str:
    return f"{CHAIN_PREFIX}{int(seq):012d}.tgz"


def _seq_of(name: str) -> int:
    tail = name.rsplit("/", 1)[-1].split(".", 1)[0]
    return int(tail) if tail.isdigit() else 0


def _empty_manifest() -> dict:
    return {"v": 3, "seq": 0, "projects": {}, "db": {"snapshot": None, "deltas": [], "sizes": {}}, "auth": {"epoch": 0, "lock_until": 0, "revoked_ms": 0}, "jobs": {}, "legacy": {}, "pruned": 0}


@dataclass
class CloudState:
    """Mirror of data_dir (holding projects/ and the rank database file) in a private object store."""

    data_dir: Path
    db_file: Path
    enabled: bool = False
    on_change: list = field(default_factory=list)
    store: ObjectStore | None = None
    clock: object = None  # monotonic clock, injectable for tests
    _seq: int = 0  # the newest save applied here
    _manifest: dict = field(default_factory=_empty_manifest)
    _version: str | None = None
    _checked: float | None = None
    _hinted: float = -1e18
    _failed_at: float = -1e18  # when the last look at storage failed (non-forced looks back off for a while)
    save_failed_at: float | None = None  # when the last save failed; the app retries it later
    _legacy_checked: float = -1e18
    _legacy_seen: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _installed: dict = field(default_factory=dict)  # slug -> {"seq": save number, "files": manifest, "deleted": bool}
    _db: dict = field(default_factory=lambda: {"snapshot": None, "applied": []})
    _db_clean: tuple | None = None
    _extra: dict = field(default_factory=dict)  # manifest changes waiting for the next save (auth, jobs, legacy)
    _hash_cache: dict = field(default_factory=dict)
    _loaded: bool = False
    last_error: str = ""

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.db_file = Path(self.db_file)
        self._lock = _dir_lock(self.data_dir)  # one sync or save at a time per working folder, across instances
        if self.enabled and self.store is None:
            self.store = BlobStore()
        if self.clock is None:
            self.clock = time.monotonic

    @classmethod
    def from_env(cls, data_dir: Path, db_file: Path) -> "CloudState":
        return cls(data_dir=Path(data_dir), db_file=Path(db_file), enabled=bool(os.environ.get("BLOB_READ_WRITE_TOKEN")))

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def _meta_dir(self) -> Path:
        return self.data_dir / ".cloud"

    @property
    def _base_db(self) -> Path:
        """The compact base (see "the base copy" below): row hashes and compressed rows, not a second database."""
        return self._meta_dir / "base.rows"

    @property
    def _tmp_dir(self) -> Path:
        """Every temp file of a sync or save lives here, so a crash leaves nothing anywhere else."""
        return self._meta_dir / "tmp"

    def _temp(self, label: str, suffix: str = "") -> Path:
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        return self._tmp_dir / f"{label}-{secrets.token_hex(4)}{suffix}"

    def _sweep(self) -> None:
        """Remove temp files a killed or crashed process left behind (and the folders older versions unpacked next to
        the working folder). Temp files are only in use while self._lock is held, by the holder, so this runs under
        the lock: at the first storage call of a process, and when the disk is full."""
        shutil.rmtree(self._tmp_dir, ignore_errors=True)
        spots = [
            (self.data_dir.parent, f".{self.data_dir.name}-incoming-*"),
            (self.data_dir.parent, f".{self.data_dir.name}-legacy-*"),
            (self._meta_dir, ".*"),  # .base.db.<hex>.tmp, .state-*.json, and older .snap-/.minus-/.plus-/.refs- temps
            (self.db_file.parent, f".{self.db_file.name}.*.tmp"),
            (self._meta_dir, "base.db*"),  # the full base copy of earlier versions
        ]
        for folder, pattern in spots:
            if not folder.is_dir():
                continue
            for path in folder.glob(pattern):
                try:
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _wipe(self) -> None:
        """Forget the local copy entirely (the disk is full even after the sweep): the next sync is a cold one."""
        _dispose_engines(self.db_file)
        if self.data_dir.is_dir():
            for path in self.data_dir.iterdir():
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(f"{self.db_file}{suffix}").unlink(missing_ok=True)
        self._sweep()
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._seq, self._version, self._manifest = 0, None, _empty_manifest()
        self._installed, self._db, self._db_clean = {}, {"snapshot": None, "applied": []}, None
        self._hash_cache.clear()

    @property
    def stamp(self) -> int:
        """The newest save this instance knows (its chain number). The app sends it to the browser as a hint."""
        return int(self._seq)

    @property
    def auth(self) -> dict:
        """Shared login state: {"epoch", "lock_until", "revoked_ms"}, including changes not saved yet."""
        merged = dict(self._manifest.get("auth") or {})
        for key, value in (self._extra.get("auth") or {}).items():
            merged[key] = max(merged.get(key) or 0, value)
        return merged

    def describe(self) -> dict:
        return {"enabled": self.enabled, "version": self._version, "error": self.last_error, "projects": len(self._installed)}

    def job_record(self, job_id: str) -> dict | None:
        """A finished or paused job's record carried in the manifest (or waiting for the next save)."""
        return (self._extra.get("jobs") or {}).get(job_id) or (self._manifest.get("jobs") or {}).get(job_id)

    # -- bookkeeping that survives a restart of a local process ---------------------------------

    def _load_meta(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        key = str(self.data_dir.resolve())
        if key not in _SWEPT:  # once per process: what a previous process left when it was killed mid-sync
            _SWEPT.add(key)
            self._sweep()
        try:
            data = json.loads((self._meta_dir / "state.json").read_text("utf-8"))
        except (OSError, ValueError):
            return
        if data.get("v") != 4:
            return  # an earlier layout: start over from the store
        self._installed = dict(data.get("installed") or {})
        self._db = dict(data.get("db") or {"snapshot": None, "applied": []})
        self._seq = int(data.get("seq") or 0)
        self._manifest = dict(data.get("manifest") or _empty_manifest())
        clean = data.get("db_clean")
        self._db_clean = tuple(clean) if clean else None

    def _save_meta(self) -> None:
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        temp = self._meta_dir / f".state-{secrets.token_hex(4)}.json"
        state = {"v": 4, "seq": self._seq, "manifest": self._manifest, "installed": self._installed, "db": self._db, "db_clean": self._db_clean}
        temp.write_text(json.dumps(state), "utf-8")
        os.replace(temp, self._meta_dir / "state.json")

    # -- reading ---------------------------------------------------------------------------------

    def refresh(self, *, force: bool = False, hint: int = 0) -> bool:
        """Bring this instance up to the newest save. Returns True when local files changed.

        Without force there is no Blob call unless the instance has not looked yet (cold start), the last look is
        REFRESH_EVERY_SECONDS old, or hint (the newest save the browser has seen, from its cookie) is newer than this
        instance. A look is one get of the next chain name; each newer save found costs that get.
        """
        if not self.enabled:
            return False
        with self._lock:
            self._load_meta()
            now = self.clock()
            if not force:
                stale_hint = hint > self._seq and now - self._hinted >= HINT_MIN_SECONDS
                due = self._checked is None or now - self._checked >= REFRESH_EVERY_SECONDS
                if not (stale_hint or due):
                    return False
                if stale_hint:
                    self._hinted = now
            if not force and now - self._failed_at < FAILURE_BACKOFF_SECONDS:
                raise CloudError(self.last_error or "Could not read storage.")  # tried a moment ago: no storage call
            try:
                changed = self._sync_recovering(now)
            except (StoreError, KeyError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
                self._failed_at = now
                self.last_error = f"Could not read storage: {type(exc).__name__}."
                print(f"ranksim: {self.last_error} ({str(exc)[:200]})", flush=True)
                raise CloudError(self.last_error) from exc
            self._checked = self.clock()
            self.last_error = ""
            if changed:
                for callback in list(self.on_change):
                    callback()
            return changed

    def _sync_recovering(self, now: float) -> bool:
        """_sync, and when the disk is full: remove temp files and try again; still full: drop the local copy and
        load it again from storage, once. (Unsaved local changes cannot be saved on a full disk either.)"""
        try:
            return self._sync(now)
        except Exception as exc:  # noqa: BLE001 - only a full disk is handled here
            if not _disk_full(exc):
                raise
            print(f"ranksim: the disk is full while reading storage ({str(exc)[:120]}); removing temp files.", flush=True)
        self._sweep()
        try:
            return self._sync(now)
        except Exception as exc:  # noqa: BLE001
            if not _disk_full(exc):
                raise
            print("ranksim: the disk is still full; clearing the local copy and loading it again from storage.", flush=True)
        self._wipe()
        self._sync(now)
        return True

    def _sync(self, now: float) -> bool:
        if self._seq == 0:
            changed = self._cold_sync()
        else:
            long_idle = self._checked is not None and now - self._checked >= VERIFY_AFTER_SECONDS
            changed = self._catch_up(verify=long_idle)
        changed |= self._watch_legacy()
        self._save_meta()
        return changed

    def _catch_up(self, *, verify: bool = False) -> bool:
        """Apply every save after ours, one by one. verify: also read the pointer, in case saves we never saw were
        pruned while this instance slept (then the newest manifest is installed instead)."""
        changed = False
        while True:
            with contextlib.ExitStack() as stack, _Saves(self) as saves:
                try:
                    stream = stack.enter_context(self.store.open(chain_name(self._seq + 1)))
                except KeyError:
                    break
                changed |= self._apply_save(self._seq + 1, stream, saves)
        if verify:
            pointed = self._read_pointer()
            if pointed > self._seq + 1:
                changed |= self._cold_sync()
        return changed

    def _read_pointer(self) -> int:
        try:
            text = self.store.get(POINTER).decode("utf-8", "replace").strip()
        except KeyError:
            return 0
        return int(text) if text.isdigit() else 0

    def _newest(self, start: int) -> int:
        """The newest chain number at or after start that exists, by probing names (heads, no listing)."""
        seq = start
        while self.store.head(chain_name(seq + 1)) is not None:
            seq += 1
        return seq

    def _cold_sync(self) -> bool:
        """Install the newest manifest's state: everything it names that this instance does not have."""
        pointed = self._read_pointer()
        if pointed == 0:
            if self.store.head(chain_name(1)) is None:
                return self._bootstrap()
            pointed = 1
        newest = self._newest(max(pointed, self._seq))
        if newest <= self._seq:
            return False
        changed = False
        with _Saves(self) as saves:
            manifest = _read_manifest(saves.path(newest))
            wanted: dict[int, list[str]] = {}
            for slug, seq in sorted((manifest.get("projects") or {}).items()):
                if SLUG_RE.match(slug) and self._installed.get(slug, {}).get("seq") != seq:
                    wanted.setdefault(int(seq), []).append(slug)
            for seq, slugs in sorted(wanted.items()):
                # One pass over each save object, unpacking only the folders of the projects taken from it.
                prefixes = tuple(f"p/{slug}/" for slug in slugs)
                staging = self._temp("projects")
                try:
                    _unpack(saves.path(seq), staging, lambda name: name.startswith(prefixes))
                    for slug in slugs:
                        changed |= self._merge_project(slug, seq, staging / "p" / slug, saves)
                finally:
                    shutil.rmtree(staging, ignore_errors=True)
            db = manifest.get("db") or {}
            needs_db = db.get("snapshot") and (db["snapshot"] != self._db.get("snapshot") or any(d not in self._db.get("applied", []) for d in db.get("deltas") or []))
            keep = ({int(db["snapshot"])} | {int(d) for d in db.get("deltas") or []}) if needs_db else set()
            for seq in list(saves.paths):
                if seq not in keep:
                    saves.drop(seq)  # projects are done: free the disk before the database is unpacked
            if needs_db:
                self._apply_db(db, saves)
                changed = True
        self._manifest, self._seq = manifest, newest
        self._version = chain_name(newest)
        return changed

    def _apply_save(self, seq: int, stream, saves: "_Saves") -> bool:
        """One save after ours: its projects, its database part, its manifest. The save is read once, straight from
        the store's stream (the object itself is never written to disk): changed project files go to a staging
        folder and are moved into place, the database part goes to its own temp file. Each is deleted once used."""
        changed = False
        manifest = None
        staging = self._temp("projects")
        parts = {"db.full": self._temp("full", ".db"), "db.minus": self._temp("minus", ".db"), "db.plus": self._temp("plus", ".db")}
        found: set[str] = set()
        try:
            with tarfile.open(fileobj=stream, mode="r|gz") as archive:
                for member in archive:
                    if member.name == MANIFEST_FILE and member.isfile():
                        manifest = json.loads(archive.extractfile(member).read().decode("utf-8"))
                    elif member.name.startswith("p/") and (member.isfile() or member.isdir()):
                        archive.extract(member, staging, filter="data")
                    elif member.name in parts and member.isfile():
                        if member.name == "db.full" and {"db.minus", "db.plus"} <= found:
                            continue  # a compacting save: the delta before it is all a caught-up instance needs
                        with open(parts[member.name], "wb") as out:
                            shutil.copyfileobj(archive.extractfile(member), out, 1 << 20)
                        found.add(member.name)
            if manifest is None:
                raise KeyError(f"{chain_name(seq)} has no manifest")
            projects = staging / "p"
            if projects.is_dir():
                for folder in sorted(projects.iterdir()):
                    if folder.is_dir() and SLUG_RE.match(folder.name):
                        changed |= self._merge_project(folder.name, seq, folder, saves)
            shutil.rmtree(staging, ignore_errors=True)
            snapshot_here = int((manifest.get("db") or {}).get("snapshot") or 0) == seq
            if "db.minus" in found and "db.plus" in found:
                self._apply_delta(parts["db.minus"], parts["db.plus"])
                if snapshot_here:
                    self._db = {"snapshot": seq, "applied": []}
                else:
                    self._db.setdefault("applied", []).append(seq)
                changed = True
            elif "db.full" in found:
                self._merge_full(parts["db.full"])
                self._db = {"snapshot": seq, "applied": []}
                changed = True
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            for part in parts.values():
                _unlink_db(part)
        self._manifest, self._seq = manifest, seq
        self._version = chain_name(seq)
        return changed

    def _merge_project(self, slug: str, seq: int, folder: Path, saves: "_Saves") -> bool:
        """Merge one project folder unpacked from save seq. folder is ours to consume: files are moved, not copied."""
        meta_file = folder / VERSION_FILE
        if not meta_file.is_file():
            raise KeyError(f"{slug} missing from save {seq}")
        meta = json.loads(meta_file.read_text("utf-8"))
        files = meta.get("files", {})
        at = {rel: int(meta.get("at", {}).get(rel, seq)) for rel in files}  # which save holds each file's bytes
        base = self._installed.get(slug, {}).get("files", {})
        staged: dict[int, Path] = {}

        def source_of(rel: str) -> Path | None:
            """A file this save did not pack because it was unchanged: read it from the save that holds it (that
            project's folder is unpacked from it once, in one pass)."""
            holder = at.get(rel)
            if holder is None or holder == seq:
                return None
            if holder not in staged:
                staged[holder] = self._temp(f"refs-{holder}")
                _unpack(saves.path(holder), staged[holder], lambda name: name.startswith(f"p/{slug}/"))
            target = staged[holder] / "p" / slug / rel
            return target if target.is_file() else None

        try:
            changed = _merge_files(self._scope(slug), base, folder, files, self._hash_cache, source_of, move=True)
        finally:
            for path in staged.values():
                shutil.rmtree(path, ignore_errors=True)
        self._installed[slug] = {"seq": seq, "files": files, "deleted": bool(meta.get("deleted")), "at": at}
        if meta.get("deleted"):
            _remove_if_empty(self._scope(slug).path)
        return changed

    def _apply_db(self, db: dict, saves: "_Saves") -> None:
        """Snapshot (when it changed), then each delta not applied yet. Each save object is deleted as soon as its
        database part is unpacked, and each unpacked part as soon as it is applied."""
        snapshot = db.get("snapshot")
        if snapshot != self._db.get("snapshot"):
            theirs = self._temp("full", ".db")
            try:
                found = _unpack_files(saves.path(snapshot), {"db.full": theirs})
                saves.drop(snapshot)
                if "db.full" not in found:
                    raise KeyError(f"no database in save {snapshot}")
                self._merge_full(theirs)
            finally:
                _unlink_db(theirs)
            self._db = {"snapshot": snapshot, "applied": []}
        for seq in db.get("deltas") or []:
            if seq in self._db["applied"]:
                continue
            parts = {"db.minus": self._temp("minus", ".db"), "db.plus": self._temp("plus", ".db")}
            try:
                found = _unpack_files(saves.path(seq), parts)
                saves.drop(seq)
                if len(found) == 2:
                    self._apply_delta(parts["db.minus"], parts["db.plus"])
            finally:
                for part in parts.values():
                    _unlink_db(part)
            self._db["applied"].append(seq)

    def _apply_delta(self, minus: Path, plus: Path) -> None:
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        clean = self._db_is_clean()
        if not self.db_file.is_file() or self.db_file.stat().st_size == 0:
            if self._base_db.is_file():
                _dispose_engines(self.db_file)
                _base_restore(self._base_db, self.db_file)
        if self.db_file.is_file():
            _dispose_engines(self.db_file)  # idle pooled connections must not hold a lock while rows are merged
            merge_sqlite(self.db_file, minus, plus)
        if self._base_db.is_file():
            _base_apply(self._base_db, minus, plus)
        self._db_clean = _stat_sig(self.db_file) if clean else None

    def _db_is_clean(self) -> bool:
        return self._db.get("snapshot") is not None and _stat_sig(self.db_file) == self._db_clean

    def _merge_full(self, theirs: Path) -> None:
        """Bring in a full database snapshot (theirs, a temp file of ours). A fresh instance takes it as its working
        database by a rename. Otherwise the snapshot's changes since the base are merged in as a delta (local unsaved
        rows are kept), the base takes the same delta, and theirs is deleted.

        Why a base at all: it is the last state every instance agreed on. Saves upload the row-level difference
        between the working db and the base (a few KB instead of the whole file, which the Blob budget needs), and
        merges use it as the common ancestor, so a row deleted on one side is deleted on the other instead of coming
        back. It is kept compact (row hashes plus compressed rows), not as a second copy of the database."""
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        base_tmp = self._temp("base", ".rows")
        minus, plus = self._temp("minus", ".db"), self._temp("plus", ".db")
        try:
            if not self.db_file.is_file() or self.db_file.stat().st_size == 0 or _no_rows(self.db_file):
                _unlink_db(self._base_db)  # an old base belongs to a database that is gone
                _base_build(theirs, base_tmp)  # before theirs moves: exactly the snapshot, nothing the app adds later
                os.replace(base_tmp, self._base_db)
                _dispose_engines(self.db_file)  # a fresh instance: the snapshot replaces the empty file the app created
                _checkpoint(theirs)
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(f"{self.db_file}{suffix}").unlink(missing_ok=True)
                    Path(f"{theirs}{suffix}").unlink(missing_ok=True)
                os.replace(theirs, self.db_file)
                clean = True
            else:
                clean = self._db_is_clean()
                _dispose_engines(self.db_file)  # idle pooled connections must not hold a lock while rows are merged
                if self._base_db.is_file():  # the snapshot's changes since the base, as a delta; base updated in place
                    make_base_delta(theirs, self._base_db, minus, plus)
                    _unlink_db(theirs)
                    merge_sqlite(self.db_file, minus, plus)
                    _base_apply(self._base_db, minus, plus)
                else:
                    merge_sqlite(self.db_file, None, theirs)
                    _base_build(theirs, base_tmp)
                    _unlink_db(theirs)
                    os.replace(base_tmp, self._base_db)
            self._db_clean = _stat_sig(self.db_file) if clean else None
        finally:
            for path in (base_tmp, minus, plus):
                _unlink_db(path)

    # -- writing ---------------------------------------------------------------------------------

    def save(self) -> str | None:
        """Upload what changed as the next save in the chain: one create-only put (plus the pointer every
        POINTER_EVERY saves). Nothing changed: no Blob call. Another instance took the number first: its save is
        merged in and the next number is tried. Returns the save's name, or None."""
        if not self.enabled:
            return None
        with self._lock:
            self._load_meta()
            try:
                if not self._dirty():
                    self.save_failed_at = None
                    return None
                try:
                    name = self._commit()
                except Exception as exc:  # noqa: BLE001 - only a full disk is retried here
                    if not _disk_full(exc):
                        raise
                    print(f"ranksim: the disk is full while saving ({str(exc)[:120]}); removing temp files and trying again.", flush=True)
                    self._sweep()
                    name = self._commit()
                self.save_failed_at = None
                return name
            except (StoreError, KeyError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
                self.save_failed_at = time.time()
                self.last_error = f"Could not save to storage: {type(exc).__name__}."
                print(f"ranksim: {self.last_error} ({str(exc)[:200]})", flush=True)
                raise CloudError(self.last_error) from exc
            finally:
                self._save_meta()

    def save_project(self, slug: str) -> str | None:
        """Kept for callers of the phase A API: a save covers every changed project anyway."""
        return self.save()

    def update_auth(self, **changes) -> dict:
        """Change the shared login state (revocation, global lockout) and save it now (one put)."""
        auth = self._extra.setdefault("auth", {})
        for key, value in changes.items():
            auth[key] = max(auth.get(key) or 0, value)
        if self.enabled:
            self.save()
        return self.auth

    def record_job(self, record: dict) -> None:
        """Carry a finished or paused job's record in the next save's manifest (the job's own data save)."""
        self._extra.setdefault("jobs", {})[record["id"]] = record

    def _projects_changed(self) -> dict:
        changed: dict[str, tuple[dict, dict]] = {}
        for slug in self._local_slugs():
            files = _manifest(self._scope(slug), self._hash_cache)
            installed = self._installed.get(slug, {})
            if files == installed.get("files") and not installed.get("deleted"):
                continue
            if not files and not installed:
                continue
            changed[slug] = (files, {"base": installed.get("seq"), "files": files, "deleted": False, "slug": slug})
        for slug, info in sorted(self._installed.items()):
            if not info.get("deleted") and slug != LOOSE and not self._scope(slug).path.is_dir():
                changed[slug] = ({}, {"base": info.get("seq"), "files": {}, "deleted": True, "slug": slug})
        return changed

    def _dirty(self) -> bool:
        if self._extra:
            return True
        if self._projects_changed():
            return True
        return self.db_file.is_file() and not self._db_is_clean()

    def _commit(self) -> str | None:
        fresh = self._checked is not None and self._seq and self.clock() - self._checked < 2.0
        if not fresh:
            if self._seq == 0:
                self._cold_sync()  # on an empty store this is the bootstrap, which saves everything as save 1
                if not self._dirty():
                    return self._version
            else:
                self._catch_up()
        for _round in range(CAS_ROUNDS):
            try:
                name = self._write_save(self._seq + 1)
            except _NothingToSave:
                return None
            if name is not None:
                self._checked = self.clock()
                return name
            self._catch_up()  # someone took that number: merge their save, then try the next one
        raise StoreError("storage stayed busy; nothing was lost here, the next save tries again")

    def _write_save(self, seq: int) -> str | None:
        """Pack and create save number seq. Returns its name, or None when the number was taken first."""
        projects = self._projects_changed()
        name = chain_name(seq)
        minus, plus = self._temp("minus", ".db"), self._temp("plus", ".db")
        base_tmp = self._temp("base", ".rows")
        db_kind, sig_before, memory, snap_bytes = None, None, None, b""
        try:
            manifest = json.loads(json.dumps(self._manifest))
            db = manifest.setdefault("db", {"snapshot": None, "deltas": [], "sizes": {}})
            if self.db_file.is_file() and not self._db_is_clean():
                sig_before = _stat_sig(self.db_file)
                has_base = bool(self._db.get("snapshot")) and self._base_db.is_file()
                if not has_base:
                    db_kind = "full"
                elif make_base_delta(self.db_file, self._base_db, minus, plus):
                    # Read from the working db in one read transaction (a consistent view, no full copy on disk).
                    # After the upload the base takes the delta exactly as every reader applies it.
                    delta_size = minus.stat().st_size + plus.stat().st_size
                    sizes = db.get("sizes") or {}
                    total = sum(int(sizes.get(str(d), 0)) for d in db.get("deltas") or []) + delta_size
                    snapshot_size = max(COMPACT_FLOOR_BYTES, int(sizes.get(str(db.get("snapshot")), 0)))
                    db_kind = "compact" if len(db.get("deltas") or []) >= COMPACT_DELTAS or total > COMPACT_RATIO * snapshot_size else "delta"
                if db_kind in ("full", "compact"):
                    # A full snapshot for cold starts, taken in memory. A compacting save also carries the delta
                    # from the same snapshot, so instances that are caught up apply a few rows, not the whole file.
                    memory = _snapshot(self.db_file)
                    if db_kind == "compact":
                        make_base_delta(memory, self._base_db, minus, plus)
                    snap_bytes = memory.serialize()
            if not projects and db_kind is None and not self._extra:
                if sig_before is not None:
                    self._db_clean = sig_before  # same rows as the base: nothing to upload
                raise _NothingToSave()
            manifest["v"], manifest["seq"] = 3, seq
            for slug in projects:
                manifest.setdefault("projects", {})[slug] = seq
            if db_kind in ("full", "compact"):
                db["snapshot"], db["deltas"], db["sizes"] = seq, [], {str(seq): len(snap_bytes)}
            elif db_kind == "delta":
                db["deltas"] = list(db.get("deltas") or []) + [seq]
                db.setdefault("sizes", {})[str(seq)] = minus.stat().st_size + plus.stat().st_size
            auth = manifest.setdefault("auth", {})
            for key, value in (self._extra.get("auth") or {}).items():
                auth[key] = max(auth.get(key) or 0, value)
            jobs = dict(manifest.get("jobs") or {})
            jobs.update(self._extra.get("jobs") or {})
            manifest["jobs"] = dict(sorted(jobs.items(), key=lambda item: float(item[1].get("updated") or 0))[-JOBS_KEPT:])
            if self._extra.get("legacy"):
                manifest["legacy"] = {**(manifest.get("legacy") or {}), **self._extra["legacy"]}
            doomed = self._prunable(manifest, seq)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
                _add_bytes(archive, MANIFEST_FILE, json.dumps(manifest, sort_keys=True).encode("utf-8"))
                for slug, (files, meta) in sorted(projects.items()):
                    # Read each file once and hash exactly the bytes that are packed: another thread may rewrite a
                    # file meanwhile, and a hash of one version stored with the bytes of another would make every
                    # instance treat the file as locally changed for ever. A file that vanished is left out.
                    # A file unchanged since the save every reader already applied is not packed again: the
                    # metadata says which earlier save holds its bytes ("at"), and pruning keeps those saves.
                    before = self._installed.get(slug, {})
                    before_files, before_at = before.get("files") or {}, before.get("at") or {}
                    packed: dict[str, str] = {}
                    contents: dict[str, bytes] = {}
                    at: dict[str, int] = {}
                    for rel in files:
                        try:
                            data = (self._scope(slug).path / rel).read_bytes()
                        except (FileNotFoundError, IsADirectoryError):
                            continue
                        digest = hashlib.sha256(data).hexdigest()
                        packed[rel] = digest
                        if before_files.get(rel) == digest and rel in before_at:
                            at[rel] = int(before_at[rel])
                        else:
                            contents[rel] = data
                            at[rel] = seq
                    files.clear()
                    files.update(packed)
                    meta["files"] = dict(packed)
                    meta["at"] = at
                    manifest.setdefault("refs", {})[slug] = sorted(set(at.values()))
                    _add_bytes(archive, f"p/{slug}/{VERSION_FILE}", json.dumps(meta, sort_keys=True).encode("utf-8"))
                    for rel, data in contents.items():
                        _add_bytes(archive, f"p/{slug}/{rel}", data)
                if db_kind in ("delta", "compact"):  # the delta first: a caught-up reader skips the snapshot after it
                    archive.add(minus, arcname="db.minus", recursive=False)
                    archive.add(plus, arcname="db.plus", recursive=False)
                if db_kind in ("full", "compact"):
                    info = tarfile.TarInfo("db.full")
                    info.size, info.mtime = len(snap_bytes), int(time.time())
                    archive.addfile(info, io.BytesIO(snap_bytes))
                    snap_bytes = b""  # the compressed copy in the buffer is enough
            try:
                self.store.put(name, buffer.getvalue(), content_type="application/gzip")
            except ObjectExists:
                return None
            # Ours now. Record it locally exactly as a reader of this save would.
            for slug, (files, meta) in projects.items():
                self._installed[slug] = {"seq": seq, "files": files, "deleted": meta["deleted"], "at": dict(meta.get("at") or {})}
            if db_kind == "full":
                _base_build(memory, base_tmp)
                os.replace(base_tmp, self._base_db)
            elif db_kind in ("delta", "compact"):
                _base_apply(self._base_db, minus, plus)
            if db_kind is not None:
                self._db_clean = sig_before
                if db_kind in ("full", "compact"):
                    self._db = {"snapshot": seq, "applied": []}
                else:
                    self._db.setdefault("applied", []).append(seq)
            self._manifest, self._seq, self._extra = manifest, seq, {}
            self._version = name
            if seq % POINTER_EVERY == 0:
                try:
                    self.store.put(POINTER, str(seq).encode(), overwrite=True, content_type="text/plain")
                except StoreError:
                    pass  # a hint: cold starts probe a few more names
            if doomed:
                try:
                    self.store.delete([chain_name(n) for n in doomed])
                except StoreError:
                    pass
            return name
        finally:
            if memory is not None:
                memory.close()
            for path in (minus, plus, base_tmp):
                _unlink_db(path)

    def _prunable(self, manifest: dict, seq: int) -> list[int]:
        """Saves old enough to delete: more than KEEP_CHAIN behind and referenced by nobody. Deletes cost nothing,
        so no listing: every number since the last prune is deleted by name (a missing name is fine)."""
        times = {int(k): float(v) for k, v in (manifest.get("times") or {}).items()}
        times[seq] = time.time()  # when each save was written, for the age rule
        start = int(manifest.get("pruned") or 0) + 1
        stop = seq - KEEP_CHAIN
        doomed: list[int] = []
        if stop >= start:
            referenced = set((manifest.get("projects") or {}).values())
            for held in (manifest.get("refs") or {}).values():
                referenced |= {int(n) for n in held}
            db = manifest.get("db") or {}
            referenced |= set(db.get("deltas") or []) | {db.get("snapshot")} | {int(manifest.get("legacy", {}).get("seq") or 0)}
            cutoff = time.time() - PRUNE_AFTER_SECONDS
            last = start - 1
            for n in range(start, stop + 1):
                written = times.get(n)
                if written is not None and written > cutoff:
                    break  # too young; so is everything after it
                last = n
                if n not in referenced:
                    doomed.append(n)
            manifest["pruned"] = last
        floor = int(manifest.get("pruned") or 0)
        manifest["times"] = {str(n): t for n, t in times.items() if n > floor}
        return doomed

    def _local_slugs(self) -> list[str]:
        if not self.projects_dir.is_dir():
            return []
        slugs = [path.name for path in self.projects_dir.iterdir() if path.is_dir() and SLUG_RE.match(path.name) and path.name not in SKIP_PARTS]
        if any(path.is_file() for path in self.projects_dir.iterdir()):
            slugs.append(LOOSE)
        return sorted(slugs)

    def _scope(self, slug: str) -> "_Scope":
        if slug == LOOSE:
            return _Scope(self.projects_dir, recursive=False)
        return _Scope(self.projects_dir / slug, recursive=True)

    # -- first boot, and the old format ----------------------------------------------------------

    def _bootstrap(self) -> bool:
        """An empty chain. Import the old single tarball if the store holds one (a list, once per store), then write
        save number 1, even when there is nothing to save, so nobody lists again."""
        legacy = sorted(self.store.list(LEGACY_PREFIX))
        changed = self._import_legacy(legacy[-1], first=True) if legacy else False
        if legacy:
            self._extra.setdefault("legacy", {}).update({"name": legacy[-1], "etag": self.store.head(LEGACY_POINTER), "since": time.time()})
        self._extra.setdefault("auth", {})
        for _round in range(CAS_ROUNDS):
            try:
                if self._write_save(self._seq + 1) is not None:
                    break
            except _NothingToSave:
                break
            self._catch_up()  # another instance wrote save 1 at the same time: take theirs, then add ours
        if self._seq == 1:
            try:
                self.store.put(POINTER, b"1", overwrite=True, content_type="text/plain")
            except StoreError:
                pass
        return changed

    def _import_legacy(self, name: str, *, first: bool) -> bool:
        """Merge one old-format tarball in. The first import takes everything (local files win); a later one, written
        by an old instance still running during the rollout, is merged against what the first import brought, so
        only what the old instance changed since then is applied. Running it twice changes nothing."""
        bases: dict = {}
        if not first:
            try:
                bases = json.loads(self.store.get(LEGACY_BASE).decode("utf-8"))
            except KeyError:
                bases = {}
        staging = self._temp("legacy")
        staging.mkdir(parents=True)
        download = self._temp("legacy", ".tgz")
        seen: dict = {}
        try:
            self.store.fetch(name, download)
            with tarfile.open(download, mode="r:gz") as archive:
                archive.extractall(staging, filter="data")
            download.unlink(missing_ok=True)
            incoming = staging / "projects"
            if incoming.is_dir():
                self.projects_dir.mkdir(parents=True, exist_ok=True)
                for slug in sorted(path.name for path in incoming.iterdir() if path.is_dir() and SLUG_RE.match(path.name)):
                    theirs = _manifest(_Scope(incoming / slug, recursive=True), {})
                    _merge_files(self._scope(slug), bases.get(slug, {}), incoming / slug, theirs, self._hash_cache)
                    seen[slug] = theirs
                loose = _manifest(_Scope(incoming, recursive=False), {})
                if loose:
                    _merge_files(self._scope(LOOSE), bases.get(LOOSE, {}), incoming, loose, self._hash_cache)
                    seen[LOOSE] = loose
            incoming_db = staging / "rank.db"
            if incoming_db.is_file():
                if self.db_file.is_file() and self.db_file.stat().st_size:
                    merge_sqlite(self.db_file, None, incoming_db)
                else:
                    self.db_file.parent.mkdir(parents=True, exist_ok=True)
                    _dispose_engines(self.db_file)
                    _copy_db(incoming_db, self.db_file)
                self._db_clean = None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            download.unlink(missing_ok=True)
        self.store.put(LEGACY_BASE, json.dumps(seen).encode("utf-8"), overwrite=True, content_type="application/json")
        print(f"ranksim: imported the old storage format ({name.rsplit('/', 1)[-1]}).", flush=True)
        return True

    def _watch_legacy(self) -> bool:
        """For a day after the migration, notice a save an old instance made in the old format and merge it in."""
        legacy = self._manifest.get("legacy") or {}
        if not legacy.get("since") or time.time() - float(legacy["since"]) > LEGACY_WATCH_SECONDS:
            return False
        now = self.clock()
        if now - self._legacy_checked < LEGACY_CHECK_SECONDS:
            return False
        self._legacy_checked = now
        etag = self.store.head(LEGACY_POINTER)
        if etag is None or etag in {legacy.get("etag"), self._legacy_seen}:
            return False
        names = sorted(self.store.list(LEGACY_PREFIX))
        if not names or names[-1] == legacy.get("name"):
            self._legacy_seen = etag  # the pointer moved but nothing new was saved: do not list again for it
            return False
        self._import_legacy(names[-1], first=False)
        self._extra.setdefault("legacy", {}).update({"name": names[-1], "etag": etag})
        self._commit()
        return True


def export_legacy(state: "CloudState") -> str:
    """Write the newest state back in the old format (ranksim/state/<time>-<rand>.tgz plus the ranksim/HEAD pointer),
    so a rollback to a deployment that only knows the old format starts from everything saved since the migration.

    Brings state up to date first. The tarball holds projects/ and a consistent copy of the rank database, exactly as
    the old code packed them. Instances on the new code that still watch the old pointer import it back, which
    changes nothing.
    """
    if not state.enabled:
        raise CloudError("cloud storage is off (BLOB_READ_WRITE_TOKEN is not set)")
    with state.lock:
        state.refresh(force=True)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
            for slug in state._local_slugs():
                scope = state._scope(slug)
                for rel, path in scope.files():
                    arc = f"projects/{rel}" if slug == LOOSE else f"projects/{slug}/{rel}"
                    archive.add(path, arcname=arc, recursive=False)
            if state.db_file.is_file():
                state._meta_dir.mkdir(parents=True, exist_ok=True)
                copy = state._meta_dir / f".export-{secrets.token_hex(4)}.db"
                try:
                    _sqlite_copy(state.db_file, copy)
                    archive.add(copy, arcname="rank.db", recursive=False)
                finally:
                    copy.unlink(missing_ok=True)
        name = f"{LEGACY_PREFIX}{time.time_ns():020d}-{secrets.token_hex(3)}.tgz"
        state.store.put(name, buffer.getvalue(), content_type="application/gzip")
        state.store.put(LEGACY_POINTER, name.encode(), overwrite=True, content_type="text/plain")
        return name


def main(argv: list[str] | None = None) -> int:
    """python -m aeo.ranksim.cloud export-legacy: run with the production BLOB_READ_WRITE_TOKEN before rolling back."""
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(prog="python -m aeo.ranksim.cloud")
    parser.add_argument("command", choices=["export-legacy", "import-legacy", "status"])
    args = parser.parse_args(argv)
    folder = Path(tempfile.mkdtemp(prefix="ranksim-cloud-"))
    state = CloudState.from_env(folder, folder / "rank.db")
    if not state.enabled:
        print("Set BLOB_READ_WRITE_TOKEN (vercel env pull) first. Nothing was done.")
        return 2
    if args.command == "status":
        state.refresh(force=True)
        print(json.dumps({"save": state.stamp, "projects": sorted(state._installed), "legacy": state._manifest.get("legacy")}, indent=2))
        return 0
    if args.command == "import-legacy":
        # After a long rollback (past the watch window), bring what the old deployment saved back into the chain.
        state.refresh(force=True)
        names = sorted(state.store.list(LEGACY_PREFIX))
        if not names:
            print("No old-format save found. Nothing was done.")
            return 1
        state._import_legacy(names[-1], first=False)
        state._extra.setdefault("legacy", {}).update({"name": names[-1], "etag": state.store.head(LEGACY_POINTER), "since": time.time()})
        print(f"Imported {names[-1]}; saved as {state.save()}. Instances watch the old format again for two weeks.")
        return 0
    name = export_legacy(state)
    print(f"Wrote {name} and pointed ranksim/HEAD at it. An old deployment now starts from the newest state.")
    return 0


class _NothingToSave(Exception):
    pass


def _add_bytes(archive, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mtime = len(data), int(time.time())
    archive.addfile(info, io.BytesIO(data))


# -- files: manifests, packing, three-way merge ----------------------------------------------------


@dataclass
class _Scope:
    path: Path
    recursive: bool

    def files(self):
        if not self.path.is_dir():
            return
        items = self.path.rglob("*") if self.recursive else self.path.iterdir()
        for item in items:
            if not item.is_file() or item.is_symlink():
                continue
            rel = item.relative_to(self.path)
            if set(rel.parts) & SKIP_PARTS or any(part.startswith(".") for part in rel.parts) or rel.name.endswith(".tmp"):
                continue  # dot files and folders (our own temp files, version file) and *.tmp are never saved
            yield rel.as_posix(), item


def _manifest(scope: _Scope, cache: dict) -> dict[str, str]:
    """path -> sha256 of every file in scope. Hashes are cached by (size, mtime) so an unchanged file is not reread."""
    found: dict[str, str] = {}
    if len(cache) > 50_000:
        cache.clear()
    for rel, path in scope.files():
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_ctime_ns)
        digest = cache.get(key)
        if digest is None:
            digest = _sha_file(path)
            cache[key] = digest
        found[rel] = digest
    return dict(sorted(found.items()))


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _Saves:
    """Save objects downloaded during one sync, each streamed to a file in the temp folder (never held in memory),
    fetched at most once, deleted as soon as the caller is done with it, and always when the sync ends."""

    def __init__(self, state: "CloudState") -> None:
        self.state = state
        self.paths: dict[int, Path] = {}

    def path(self, seq: int) -> Path:
        """The save's file; KeyError when the store does not have it."""
        seq = int(seq)
        if seq not in self.paths:
            target = self.state._temp(f"save-{seq}", ".tgz")
            try:
                self.state.store.fetch(chain_name(seq), target)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            self.paths[seq] = target
        return self.paths[seq]

    def get(self, seq: int) -> Path | None:
        try:
            return self.path(seq)
        except KeyError:
            return None

    def drop(self, seq: int) -> None:
        path = self.paths.pop(int(seq), None)
        if path is not None:
            path.unlink(missing_ok=True)

    def __enter__(self) -> "_Saves":
        return self

    def __exit__(self, *_exc) -> None:
        for seq in list(self.paths):
            self.drop(seq)


def _read_manifest(archive_path: Path) -> dict:
    """The manifest of a save file (its first member, so this reads only the start of the file)."""
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            if member.name == MANIFEST_FILE and member.isfile():
                handle = archive.extractfile(member)
                if handle is not None:
                    return json.loads(handle.read().decode("utf-8"))
    raise KeyError(f"{archive_path.name} has no manifest")


def _unpack(archive_path: Path, dest: Path, want) -> None:
    """Extract, in one pass, only the members whose name want(name) accepts."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            if (member.isfile() or member.isdir()) and want(member.name):
                archive.extract(member, dest, filter="data")


def _unpack_files(archive_path: Path, targets: dict[str, Path]) -> set[str]:
    """Stream the named members straight to their target files, in one pass. Returns the names found."""
    found: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive:
            target = targets.get(member.name)
            if target is None or not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as out:
                shutil.copyfileobj(handle, out, 1 << 20)
            found.add(member.name)
            if len(found) == len(targets):
                break
    return found


def _unlink_db(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _checkpoint(path: Path) -> None:
    """Fold a database's -wal file back in and truncate it, so a large merge does not leave a large -wal behind."""
    if not Path(f"{path}-wal").exists():
        return
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def _disk_full(exc: BaseException | None) -> bool:
    """True when exc (or what caused it) says the disk is full: ENOSPC, or SQLite's "database or disk is full"."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT):
            return True
        if isinstance(exc, sqlite3.Error) and (getattr(exc, "sqlite_errorcode", None) == 13 or "disk is full" in str(exc).lower()):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


_SWEPT: set[str] = set()  # working folders already swept for leftover temp files by this process
_DIR_LOCKS: dict[str, threading.RLock] = {}
_DIR_LOCKS_GUARD = threading.Lock()


def _dir_lock(data_dir: Path) -> threading.RLock:
    """One lock per working folder for the whole process, however many CloudState objects point at it."""
    key = str(Path(data_dir).resolve())
    with _DIR_LOCKS_GUARD:
        return _DIR_LOCKS.setdefault(key, threading.RLock())


def _merge_files(local: _Scope, base: dict, theirs_dir: Path, theirs: dict, cache: dict, source_of=None, move: bool = False) -> bool:
    """Three-way merge at file level into the local folder. Returns True when a local file changed.

    For each path: if theirs did not change it since base, keep local. If local did not change it since base, take
    theirs (copy, or delete when theirs removed it). If both changed it differently, local wins: it is the newer,
    unsaved work of this instance, and it will be saved next. move: theirs_dir (and what source_of returns) are temp
    files of ours, moved into place instead of copied, so a merge never holds two copies of the incoming files.
    """
    mine = _manifest(local, cache)
    changed = False
    for rel in sorted(set(base) | set(theirs)):
        b, t, m = base.get(rel), theirs.get(rel), mine.get(rel)
        if t == b or m == t:
            continue
        if m != b:
            print(f"ranksim: storage merge kept this server's copy of {local.path.name}/{rel} (both sides changed it).", flush=True)
            continue
        target = local.path / rel
        if t is None:
            target.unlink(missing_ok=True)
        else:
            source = theirs_dir / rel
            if not source.is_file():
                source = source_of(rel) if source_of is not None else None  # bytes kept in an earlier save
                if source is None or not source.is_file():
                    raise KeyError(f"{local.path.name}/{rel} is missing from storage")
            target.parent.mkdir(parents=True, exist_ok=True)
            if move:
                os.utime(source)  # a fresh mtime, not the tar's whole-second one
                os.replace(source, target)
            else:
                temp = target.with_name(f".ranksim-tmp-{secrets.token_hex(4)}")
                shutil.copyfile(source, temp)  # a fresh mtime: copy2 would keep the tar's whole-second one
                os.replace(temp, target)
            try:  # we know this file's hash: later manifests (and the report cache's key) need not read it again
                stat = target.stat()
                cache[(str(target), stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_ctime_ns)] = t
            except OSError:
                pass
        changed = True
    return changed


def _remove_if_empty(folder: Path) -> None:
    if not folder.is_dir():
        return
    for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        folder.rmdir()
    except OSError:
        pass


def _stat_sig(path: Path) -> tuple | None:
    """Changes whenever the database changes. SQLite in WAL mode (aeo.db turns it on for every engine) writes to the
    -wal file first, so that file counts too."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    sig = [stat.st_size, stat.st_mtime_ns]
    for suffix in ("-wal",):
        try:
            extra = Path(f"{path}{suffix}").stat()
            sig += [extra.st_size, extra.st_mtime_ns]
        except FileNotFoundError:
            sig += [0, 0]
    return tuple(sig)


def _copy_db(source: Path, target: Path) -> None:
    """Replace a database file we own, dropping stale -wal and -shm files that belong to the old one."""
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{target}{suffix}").unlink(missing_ok=True)
    temp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    shutil.copyfile(source, temp)
    os.replace(temp, target)


# -- the rank database ---------------------------------------------------------------------------

LEDGER_TABLE = "rs_predictions"
GROUPED = {"rs_serp": "query"}  # Google results are replaced per search as a group
STAMP_COLUMNS = ("fetched_at", "updated_at", "recorded_at", "created_at")


def merge_sqlite(ours: Path, base: Path | None, theirs: Path) -> dict:
    """Apply the changes theirs made since base to ours, in place, in one transaction. Returns counts per table.

    base None means "no common history": every row of theirs counts as added. Row identity:
    - a table with a natural primary key (rs_pages by url, rs_keywords by query): the key. When both sides changed a
      key, the row with the newer timestamp column wins (ours on a tie).
    - a table with an integer id (append-only logs): every column except the id.
    - GROUPED tables: all rows of one group (one search's results) move together; newest timestamp wins a conflict.
    - the prediction ledger: rows are known by row_hash; theirs' new rows are appended, and our rows that theirs does
      not have are re-chained after them, so verify() still sees one unbroken chain.
    """
    conn = sqlite3.connect(str(ours), timeout=10, isolation_level=None)
    conn.execute("PRAGMA temp_store=MEMORY")  # sorts and EXCEPTs in memory: SQLite's temp files would fill /tmp
    counts: dict[str, int] = {}
    try:
        conn.execute("ATTACH DATABASE ? AS t", (str(theirs),))
        if base is not None and Path(base).is_file():
            conn.execute("ATTACH DATABASE ? AS b", (str(base),))
            has_base = True
        else:
            conn.execute("ATTACH DATABASE ':memory:' AS b")
            has_base = False
        conn.execute("BEGIN IMMEDIATE")
        their_tables = _tables(conn, "t")
        our_tables = _tables(conn, "main")
        base_tables = _tables(conn, "b") if has_base else {}
        for table, sql in their_tables.items():
            if table not in our_tables:
                conn.execute(sql)  # a table only theirs has: create it here (the CREATE has no schema prefix)
                our_tables[table] = sql
            if table not in base_tables:
                conn.execute(_in_schema(sql, table, "b"))  # an empty base table keeps every query uniform
            counts[table] = _merge_table(conn, table)
        conn.execute("COMMIT")
        try:
            conn.execute("PRAGMA main.wal_checkpoint(TRUNCATE)")  # a large merge must not leave a large -wal file
        except sqlite3.Error:
            pass
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return counts


def _no_rows(path: Path) -> bool:
    """True when no ordinary table of the database holds a row (a file the app created at start-up)."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return False
    try:
        for table in _tables(conn, "main"):
            if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                return False
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def _tables(conn, schema: str) -> dict[str, str]:
    """Ordinary tables only. A virtual table (FTS5 and the like) and its shadow tables (<name>_data, _idx, _content,
    _docsize, _config) are SQLite's own structures: recreating them breaks ("table already exists"), and merging their
    rows corrupts them. They are derived data and travel only inside full snapshots."""
    rows = conn.execute(f"SELECT name, sql FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    virtual = [name for name, sql in rows if sql and sql.lstrip().upper().startswith("CREATE VIRTUAL")]
    return {
        name: sql for name, sql in rows
        if sql and name not in virtual and not any(name.startswith(f"{v}_") for v in virtual)
    }


def _in_schema(sql: str, table: str, schema: str) -> str:
    pattern = re.compile(r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?(\"?)" + re.escape(table) + r"\2", re.IGNORECASE)
    return pattern.sub(f'CREATE TABLE IF NOT EXISTS {schema}."{table}"', sql, count=1)


def _columns(conn, schema: str, table: str) -> list[tuple[str, str, int]]:
    return [(row[1], (row[2] or "").upper(), int(row[5])) for row in conn.execute(f'PRAGMA {schema}.table_info("{table}")')]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _same(cols: list[str], left: str, right: str) -> str:
    return " AND ".join(f"{left}.{_q(col)} IS {right}.{_q(col)}" for col in cols) or "1"


def _merge_table(conn, table: str) -> int:
    ours_cols = _columns(conn, "main", table)
    their_names = {name for name, _type, _pk in _columns(conn, "t", table)}
    base_names = {name for name, _type, _pk in _columns(conn, "b", table)}
    cols = [name for name, _type, _pk in ours_cols if name in their_names and name in base_names]
    pk = [name for name, _type, flag in sorted(ours_cols, key=lambda item: item[2]) if flag]
    integer_id = len(pk) == 1 and next(kind for name, kind, _f in ours_cols if name == pk[0]) == "INTEGER"
    if not cols:
        return 0
    if table == LEDGER_TABLE and "row_hash" in cols:
        return _merge_ledger(conn, table, [col for col in cols if col != pk[0]] if integer_id else cols)
    if integer_id:
        data = [col for col in cols if col != pk[0]]
        if table in GROUPED and GROUPED[table] in data:
            return _merge_groups(conn, table, data, GROUPED[table])
        return _merge_log(conn, table, data)
    if pk and all(col in cols for col in pk):
        return _merge_keyed(conn, table, cols, pk)
    return _merge_log(conn, table, cols)


def _merge_log(conn, table: str, cols: list[str]) -> int:
    """Rows known by their content: insert theirs' new rows, delete rows theirs removed (when ours still has them)."""
    names = ", ".join(_q(col) for col in cols)
    t, q = _q(table), _q(table)
    added = conn.execute(
        f"INSERT INTO main.{q} ({names}) SELECT {names} FROM (SELECT {names} FROM t.{t} EXCEPT SELECT {names} FROM b.{t} "
        f"EXCEPT SELECT {names} FROM main.{q})"
    ).rowcount
    removed = conn.execute(
        f"DELETE FROM main.{q} WHERE EXISTS (SELECT 1 FROM b.{t} AS y WHERE {_same(cols, 'y', f'main.{q}')}) "
        f"AND NOT EXISTS (SELECT 1 FROM t.{t} AS z WHERE {_same(cols, 'z', f'main.{q}')})"
    ).rowcount
    return max(added, 0) + max(removed, 0)


def _stamp_col(cols: list[str]) -> str | None:
    return next((col for col in STAMP_COLUMNS if col in cols), None)


def _merge_keyed(conn, table: str, cols: list[str], pk: list[str]) -> int:
    names = ", ".join(_q(col) for col in cols)
    keys = ", ".join(_q(col) for col in pk)
    q = _q(table)
    stamp = _stamp_col(cols)
    touched = conn.execute(
        f"SELECT {keys} FROM (SELECT {names} FROM t.{q} EXCEPT SELECT {names} FROM b.{q}) "
        f"UNION SELECT {keys} FROM (SELECT {names} FROM b.{q} EXCEPT SELECT {names} FROM t.{q})"
    ).fetchall()
    where = " AND ".join(f"{_q(col)} IS ?" for col in pk)
    count = 0
    for key in touched:
        theirs = conn.execute(f"SELECT {names} FROM t.{q} WHERE {where}", key).fetchone()
        base = conn.execute(f"SELECT {names} FROM b.{q} WHERE {where}", key).fetchone()
        mine = conn.execute(f"SELECT {names} FROM main.{q} WHERE {where}", key).fetchone()
        if mine == theirs:
            continue
        if mine != base:  # both changed this row
            if theirs is None or mine is None or stamp is None:
                continue
            index = cols.index(stamp)
            if not (theirs[index] is not None and (mine[index] is None or str(theirs[index]) > str(mine[index]))):
                continue
        conn.execute(f"DELETE FROM main.{q} WHERE {where}", key)
        if theirs is not None:
            conn.execute(f"INSERT INTO main.{q} ({names}) VALUES ({', '.join('?' for _ in cols)})", theirs)
        count += 1
    return count


def _merge_groups(conn, table: str, cols: list[str], group: str) -> int:
    names = ", ".join(_q(col) for col in cols)
    q, g = _q(table), _q(group)
    stamp = _stamp_col(cols)

    def changed_groups(left: str, right: str) -> set:
        rows = conn.execute(
            f"SELECT {g} FROM (SELECT {names} FROM {left}.{q} EXCEPT SELECT {names} FROM {right}.{q}) "
            f"UNION SELECT {g} FROM (SELECT {names} FROM {right}.{q} EXCEPT SELECT {names} FROM {left}.{q})"
        ).fetchall()
        return {row[0] for row in rows}

    theirs_changed = changed_groups("t", "b")
    ours_changed = changed_groups("main", "b")
    count = 0
    for value in sorted(theirs_changed, key=lambda item: str(item)):
        if value in ours_changed and stamp is not None:
            mine = conn.execute(f"SELECT max({_q(stamp)}) FROM main.{q} WHERE {g} IS ?", (value,)).fetchone()[0]
            theirs = conn.execute(f"SELECT max({_q(stamp)}) FROM t.{q} WHERE {g} IS ?", (value,)).fetchone()[0]
            if mine is not None and (theirs is None or str(theirs) <= str(mine)):
                continue  # our pull of this search is newer
        elif value in ours_changed:
            continue
        conn.execute(f"DELETE FROM main.{q} WHERE {g} IS ?", (value,))
        conn.execute(f"INSERT INTO main.{q} ({names}) SELECT {names} FROM t.{q} WHERE {g} IS ? ORDER BY rowid", (value,))
        count += 1
    return count


def _merge_ledger(conn, table: str, cols: list[str]) -> int:
    """Keep one hash chain: theirs' new rows go on, then our rows that theirs does not have are re-chained after them.

    The anchor is the prev_hash of theirs' first new row: our rows after that row are ours alone (never saved). This
    works with a full copy of theirs and with a delta that holds only theirs' new rows.
    """
    q = _q(table)
    names = ", ".join(_q(col) for col in cols)
    new_theirs = conn.execute(
        f"SELECT {names} FROM t.{q} WHERE row_hash NOT IN (SELECT row_hash FROM main.{q}) ORDER BY rowid"
    ).fetchall()
    if not new_theirs:
        return 0
    index = {col: i for i, col in enumerate(cols)}
    anchor = new_theirs[0][index["prev_hash"]]
    found = conn.execute(f"SELECT rowid FROM main.{q} WHERE row_hash = ?", (anchor,)).fetchone()
    if found is not None:
        after = f"rowid > {int(found[0])}"
    elif not conn.execute(f"SELECT 1 FROM main.{q} LIMIT 1").fetchone() or anchor == "0" * 64:
        after = "1"
    else:  # theirs builds on rows we never saw: keep every row of ours that neither side's history has
        after = f"row_hash NOT IN (SELECT row_hash FROM b.{q})"
    where = f"{after} AND row_hash NOT IN (SELECT row_hash FROM t.{q})"
    local_only = conn.execute(f"SELECT {names} FROM main.{q} WHERE {where} ORDER BY rowid").fetchall()
    if local_only:
        conn.execute(f"DELETE FROM main.{q} WHERE {where}")
    marks = ", ".join("?" for _ in cols)
    for row in new_theirs:
        conn.execute(f"INSERT INTO main.{q} ({names}) VALUES ({marks})", row)
    if local_only:
        from aeo.ranksim.ledger import _row_hash

        tip = conn.execute(f"SELECT row_hash FROM main.{q} ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        for row in local_only:
            values = list(row)
            payload = values[index["payload"]]
            payload_obj = json.loads(payload) if isinstance(payload, str) else payload
            values[index["prev_hash"]] = tip
            values[index["row_hash"]] = _row_hash(
                tip,
                values[index["created_at"]],
                values[index["kind"]],
                values[index["query"]],
                values[index["subject_url"]],
                payload_obj,
                values[index["project_slug"]] if "project_slug" in index else None,
            )
            conn.execute(f"INSERT INTO main.{q} ({names}) VALUES ({marks})", values)
            tip = values[index["row_hash"]]
    return len(new_theirs) + len(local_only)


def make_delta(new: Path, base: Path | None, minus: Path, plus: Path) -> int:
    """The changes from base to new as two small databases: plus holds rows new has and base lacks, minus the rows base
    has and new lacks (a search's Google results move as a whole group). merge_sqlite(target, minus, plus) applies
    them. Returns how many rows changed (0: nothing to save)."""
    for path in (minus, plus):
        path.unlink(missing_ok=True)
    conn = sqlite3.connect(str(new), timeout=30)
    conn.execute("PRAGMA temp_store=MEMORY")
    total = 0
    try:
        conn.execute("ATTACH DATABASE ? AS m", (str(minus),))
        conn.execute("ATTACH DATABASE ? AS p", (str(plus),))
        if base is not None and Path(base).is_file():
            conn.execute("ATTACH DATABASE ? AS b", (str(base),))
        else:
            conn.execute("ATTACH DATABASE ':memory:' AS b")
        base_tables = _tables(conn, "b")
        for table, sql in _tables(conn, "main").items():
            conn.execute(_in_schema(sql, table, "m"))
            conn.execute(_in_schema(sql, table, "p"))
            if table not in base_tables:
                conn.execute(_in_schema(sql, table, "b"))
            cols = [name for name, _type, _pk in _columns(conn, "main", table)]
            base_names = {name for name, _type, _pk in _columns(conn, "b", table)}
            shared = [col for col in cols if col in base_names]
            names = ", ".join(_q(col) for col in shared)
            q = _q(table)
            group = GROUPED.get(table)
            if group and group in shared:
                g = _q(group)
                changed = (
                    f"SELECT {g} FROM (SELECT {names} FROM main.{q} EXCEPT SELECT {names} FROM b.{q}) "
                    f"UNION SELECT {g} FROM (SELECT {names} FROM b.{q} EXCEPT SELECT {names} FROM main.{q})"
                )
                total += conn.execute(f"INSERT INTO p.{q} ({names}) SELECT {names} FROM main.{q} WHERE {g} IN ({changed}) ORDER BY rowid").rowcount
                total += conn.execute(f"INSERT INTO m.{q} ({names}) SELECT {names} FROM b.{q} WHERE {g} IN ({changed}) ORDER BY rowid").rowcount
                continue
            order = " ORDER BY " + _q("id") if "id" in shared else ""
            total += conn.execute(
                f"INSERT INTO p.{q} ({names}) SELECT * FROM (SELECT {names} FROM main.{q} EXCEPT SELECT {names} FROM b.{q}){order}"
            ).rowcount
            total += conn.execute(
                f"INSERT INTO m.{q} ({names}) SELECT * FROM (SELECT {names} FROM b.{q} EXCEPT SELECT {names} FROM main.{q}){order}"
            ).rowcount
        conn.commit()
    finally:
        conn.close()
    return max(total, 0)


# -- the base copy: the last database state every instance agreed on, kept compact ------------------
#
# A second full copy of the rank database would double its footprint on /tmp. The base keeps, per table, one entry per
# row: a hash of the row's content (to find what changed) and the row compressed (to ship deleted or replaced rows in a
# delta's minus part). That is a fraction of the database's size, and deltas are found by scanning the working database
# once instead of comparing two full copies. Hashes and the encoding (marshal version 2, which never shares references,
# so equal values always encode alike) never leave the instance.

BASE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS {s}.tables (tbl TEXT PRIMARY KEY, sql TEXT, names TEXT)",
    "CREATE TABLE IF NOT EXISTS {s}.rows (tbl TEXT NOT NULL, h BLOB NOT NULL, g, pos INTEGER, body BLOB NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS {s}.rows_h ON rows (tbl, h)",
    "CREATE INDEX IF NOT EXISTS {s}.rows_g ON rows (tbl, g)",
)


def _base_schema(conn, schema: str = "main") -> None:
    for statement in BASE_SCHEMA:
        conn.execute(statement.format(s=schema))


def _row_hash(names, values) -> bytes:
    return hashlib.sha1(marshal.dumps(tuple(sorted(zip(names, values))), 2)).digest()


def _row_pack(names, values) -> bytes:
    return zlib.compress(marshal.dumps((tuple(names), tuple(values)), 2), 3)  # half the time of level 6, 7% bigger


def _row_unpack(body: bytes) -> dict:
    names, values = marshal.loads(zlib.decompress(body))
    return dict(zip(names, values))


def _connect_source(source) -> tuple:
    """(connection, close it after?) for a database path or an open connection."""
    if isinstance(source, sqlite3.Connection):
        return source, False
    return sqlite3.connect(str(source), timeout=30, isolation_level=None), True


def _base_build(source, target: Path) -> None:
    """Write the base of a whole database (a path, or an open connection such as an in-memory snapshot) to target."""
    _unlink_db(target)
    src, close = _connect_source(source)
    out = sqlite3.connect(str(target), isolation_level=None)
    try:
        out.execute("PRAGMA page_size=1024")  # compressed rows end mid-page: small pages waste less of each
        _base_schema(out)
        out.execute("BEGIN")
        pos = 0
        for table, sql in _tables(src, "main").items():
            names = [name for name, _type, _pk in _columns(src, "main", table)]
            if not names:
                continue
            out.execute("INSERT OR REPLACE INTO tables VALUES (?, ?, ?)", (table, sql, json.dumps(names)))
            group = GROUPED.get(table)
            gi = names.index(group) if group in names else None
            rows = []
            for values in src.execute(f"SELECT {', '.join(_q(n) for n in names)} FROM main.{_q(table)}"):
                pos += 1
                rows.append((table, _row_hash(names, values), values[gi] if gi is not None else None, pos, _row_pack(names, values)))
                if len(rows) >= 500:
                    out.executemany("INSERT OR IGNORE INTO rows VALUES (?, ?, ?, ?, ?)", rows)
                    rows.clear()
            out.executemany("INSERT OR IGNORE INTO rows VALUES (?, ?, ?, ?, ?)", rows)
        out.execute("COMMIT")
    except BaseException:
        out.close()
        _unlink_db(target)
        raise
    finally:
        if close:
            src.close()
    out.close()


def _base_apply(base: Path, minus: Path, plus: Path) -> None:
    """Apply a delta to the base: drop the rows of minus, add the rows of plus (exactly what every reader does)."""
    conn = sqlite3.connect(str(base), timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA temp_store=MEMORY")
        _base_schema(conn)
        conn.execute("ATTACH DATABASE ? AS m", (str(minus),))
        conn.execute("ATTACH DATABASE ? AS p", (str(plus),))
        conn.execute("BEGIN IMMEDIATE")
        known = {tbl: json.loads(names) for tbl, names in conn.execute("SELECT tbl, names FROM tables")}

        def layout(schema: str, table: str) -> list[str]:
            # Hash over the columns the base knows for this table (a delta carries only the columns both sides had).
            cols = [name for name, _type, _pk in _columns(conn, schema, table)]
            return [c for c in cols if c in known[table]] if table in known else cols

        for table in _tables(conn, "m"):
            names = layout("m", table)
            if names:
                gone = [(table, _row_hash(names, values)) for values in conn.execute(f"SELECT {', '.join(_q(n) for n in names)} FROM m.{_q(table)}")]
                conn.executemany("DELETE FROM rows WHERE tbl = ? AND h = ?", gone)
        pos = conn.execute("SELECT coalesce(max(pos), 0) FROM rows").fetchone()[0]
        for table, sql in _tables(conn, "p").items():
            names = layout("p", table)
            if not names:
                continue
            if table not in known:
                conn.execute("INSERT OR REPLACE INTO tables VALUES (?, ?, ?)", (table, sql, json.dumps(names)))
            group = GROUPED.get(table)
            gi = names.index(group) if group in names else None
            rows = []
            for values in conn.execute(f"SELECT {', '.join(_q(n) for n in names)} FROM p.{_q(table)}"):
                pos += 1
                rows.append((table, _row_hash(names, values), values[gi] if gi is not None else None, pos, _row_pack(names, values)))
            conn.executemany("INSERT OR IGNORE INTO rows VALUES (?, ?, ?, ?, ?)", rows)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _base_restore(base: Path, target: Path) -> None:
    """Rebuild a database from the base (the working database went missing)."""
    _unlink_db(target)
    conn = sqlite3.connect(str(target), isolation_level=None)
    try:
        conn.execute("ATTACH DATABASE ? AS bs", (str(base),))
        conn.execute("BEGIN")
        for table, sql, _names in conn.execute("SELECT tbl, sql, names FROM bs.tables").fetchall():
            conn.execute(sql)
            for (body,) in conn.execute("SELECT body FROM bs.rows WHERE tbl = ? ORDER BY pos", (table,)).fetchall():
                row = _row_unpack(body)
                conn.execute(f"INSERT INTO main.{_q(table)} ({', '.join(_q(n) for n in row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        conn.execute("COMMIT")
    finally:
        conn.close()


def make_base_delta(new, base: Path | None, minus: Path, plus: Path) -> int:
    """make_delta against the compact base: plus holds the rows new has and the base lacks, minus the rows the base has
    and new lacks, under the same rules (rows compared on the columns both have; a search's Google results move as a
    whole group; tables in the same order). new is a path or an open connection, read in one read transaction (a
    consistent view while the app keeps writing). Returns how many rows changed (0: nothing to save)."""
    for path in (minus, plus):
        _unlink_db(path)
    conn, close = _connect_source(new)
    total = 0
    try:
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("ATTACH DATABASE ? AS m", (str(minus),))
        conn.execute("ATTACH DATABASE ? AS p", (str(plus),))
        has_base = base is not None and Path(base).is_file()
        conn.execute("ATTACH DATABASE ? AS bs", (str(base) if has_base else ":memory:",))
        if not has_base:
            _base_schema(conn, "bs")
        conn.execute("BEGIN")
        known = {tbl: json.loads(names) for tbl, names in conn.execute("SELECT tbl, names FROM bs.tables")}
        for table, sql in _tables(conn, "main").items():
            conn.execute(_in_schema(sql, table, "m"))
            conn.execute(_in_schema(sql, table, "p"))
            cols = [name for name, _type, _pk in _columns(conn, "main", table)]
            base_names = known.get(table)
            shared = [c for c in cols if c in base_names] if base_names is not None else cols
            if not shared:
                continue
            names, q = ", ".join(_q(c) for c in shared), _q(table)
            group = GROUPED.get(table) if GROUPED.get(table) in shared else None
            gi = shared.index(group) if group else None
            order = " ORDER BY rowid" if group else (" ORDER BY " + _q("id") if "id" in shared else "")
            # base rows: hash over the shared columns -> stored hash
            stored: dict[bytes, bytes] = {}
            if base_names is not None and set(base_names) == set(shared):
                for (h,) in conn.execute("SELECT h FROM bs.rows WHERE tbl = ?", (table,)):
                    stored[h] = h
            elif base_names is not None:
                for h, body in conn.execute("SELECT h, body FROM bs.rows WHERE tbl = ?", (table,)):
                    row = _row_unpack(body)
                    stored[_row_hash(shared, [row.get(c) for c in shared])] = h
            seen: set[bytes] = set()
            groups: set = set()
            insert_p = f"INSERT INTO p.{q} ({names}) VALUES ({', '.join('?' * len(shared))})"
            insert_m = f"INSERT INTO m.{q} ({names}) VALUES ({', '.join('?' * len(shared))})"
            added = []
            for values in conn.execute(f"SELECT {names} FROM main.{q}{order}"):
                h = _row_hash(shared, values)
                if h in seen:
                    continue
                seen.add(h)
                if h not in stored:
                    if group:
                        groups.add(values[gi])
                    else:
                        added.append(values)
                        if len(added) >= 500:
                            conn.executemany(insert_p, added)
                            total += len(added)
                            added.clear()
            if added:
                conn.executemany(insert_p, added)
                total += len(added)
            missing = [h for computed, h in stored.items() if computed not in seen]
            removed = []
            for start in range(0, len(missing), 500):
                chunk = missing[start : start + 500]
                marks = ", ".join("?" * len(chunk))
                for pos, body in conn.execute(f"SELECT pos, body FROM bs.rows WHERE tbl = ? AND h IN ({marks})", (table, *chunk)):
                    row = _row_unpack(body)
                    values = tuple(row.get(c) for c in shared)
                    if group:
                        groups.add(values[gi])
                    else:
                        removed.append((pos, values))
            if group:
                if not groups:
                    continue
                rows = [values for values in conn.execute(f"SELECT {names} FROM main.{q} ORDER BY rowid") if values[gi] in groups]
                conn.executemany(insert_p, rows)
                total += len(rows)
                old = []
                for value in groups:
                    for pos, body in conn.execute("SELECT pos, body FROM bs.rows WHERE tbl = ? AND g IS ?", (table, value)):
                        row = _row_unpack(body)
                        old.append((pos, tuple(row.get(c) for c in shared)))
                old.sort(key=lambda item: item[0])
                conn.executemany(insert_m, [values for _pos, values in old])
                total += len(old)
                continue
            if "id" in shared:
                at = shared.index("id")
                removed.sort(key=lambda item: (item[1][at] is not None, item[1][at] if item[1][at] is not None else 0))
            else:
                removed.sort(key=lambda item: item[0])
            conn.executemany(insert_m, [values for _pos, values in removed])
            total += len(removed)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        for schema in ("m", "p", "bs"):
            try:
                conn.execute(f"DETACH DATABASE {schema}")
            except sqlite3.Error:
                pass
        if close:
            conn.close()
    return total


def _snapshot(db_file: Path) -> sqlite3.Connection:
    """A consistent copy of the database in memory (no third full copy on disk)."""
    src = sqlite3.connect(str(db_file), timeout=30)
    mem = sqlite3.connect(":memory:", isolation_level=None)
    try:
        src.backup(mem)
    except BaseException:
        mem.close()
        raise
    finally:
        src.close()
    return mem


def _sqlite_copy(source: Path, target: Path) -> None:
    """sqlite3's online backup: a consistent copy even while another connection is open."""
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _dispose_engines(db_file: Path) -> None:
    """Pooled connections keep the old database file open; drop them before the file is replaced."""
    from aeo.ranksim import store

    for url in list(store._factories):
        if url.startswith("sqlite:///") and Path(url.removeprefix("sqlite:///")).resolve() == db_file.resolve():
            factory = store._factories.pop(url)
            bind = factory.kw.get("bind")
            if bind is not None:
                bind.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
