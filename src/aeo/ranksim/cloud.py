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

Nothing here runs unless BLOB_READ_WRITE_TOKEN is set (or a store is passed in, as the tests do).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import threading
import time
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
HINT_MIN_SECONDS = 5.0  # a stale-hint cookie can force a look at most this often per instance
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
        return self._meta_dir / "base.db"

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
        try:
            data = json.loads((self._meta_dir / "state.json").read_text("utf-8"))
        except (OSError, ValueError):
            return
        if data.get("v") != 3:
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
        state = {"v": 3, "seq": self._seq, "manifest": self._manifest, "installed": self._installed, "db": self._db, "db_clean": self._db_clean}
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
            try:
                changed = self._sync(now)
            except (StoreError, KeyError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not read storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            self._checked = self.clock()
            self.last_error = ""
            if changed:
                for callback in list(self.on_change):
                    callback()
            return changed

    def _sync(self, now: float) -> bool:
        if self._seq == 0:
            changed = self._cold_sync()
        else:
            long_idle = self._checked is not None and now - self._checked >= VERIFY_AFTER_SECONDS
            changed = self._catch_up(verify=long_idle)
        changed |= self._watch_legacy()
        self._save_meta()
        return changed

    def _get_save(self, seq: int) -> bytes | None:
        try:
            return self.store.get(chain_name(seq))
        except KeyError:
            return None

    def _catch_up(self, *, verify: bool = False) -> bool:
        """Apply every save after ours, one by one. verify: also read the pointer, in case saves we never saw were
        pruned while this instance slept (then the newest manifest is installed instead)."""
        changed = False
        while True:
            body = self._get_save(self._seq + 1)
            if body is None:
                break
            changed |= self._apply_save(self._seq + 1, body)
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
        body = self._get_save(newest)
        if body is None:
            raise KeyError(chain_name(newest))
        with _Unpacked(self.data_dir, body) as folder:
            manifest = json.loads((folder / MANIFEST_FILE).read_text("utf-8"))
        bodies = {newest: body}
        changed = False
        for slug, seq in sorted((manifest.get("projects") or {}).items()):
            if SLUG_RE.match(slug) and self._installed.get(slug, {}).get("seq") != seq:
                if seq not in bodies:  # fetch each save object once (setdefault would fetch every time)
                    bodies[seq] = self._get_save(seq)
                if bodies[seq] is None:
                    raise KeyError(chain_name(seq))
                changed |= self._install_project(slug, seq, bodies[seq])
        db = manifest.get("db") or {}
        if db.get("snapshot") and (db["snapshot"] != self._db.get("snapshot") or any(d not in self._db.get("applied", []) for d in db.get("deltas") or [])):
            self._apply_db(db, bodies)
            changed = True
        self._manifest, self._seq = manifest, newest
        self._version = chain_name(newest)
        return changed

    def _apply_save(self, seq: int, body: bytes) -> bool:
        """One save after ours: its projects, its database part, its manifest."""
        changed = False
        with _Unpacked(self.data_dir, body) as folder:
            manifest = json.loads((folder / MANIFEST_FILE).read_text("utf-8"))
            projects = folder / "p"
            if projects.is_dir():
                for path in sorted(projects.iterdir()):
                    if path.is_dir() and SLUG_RE.match(path.name):
                        changed |= self._merge_project(path.name, seq, path)
            full, minus, plus = folder / "db.full", folder / "db.minus", folder / "db.plus"
            if full.is_file():
                self._merge_full(full)
                self._db = {"snapshot": seq, "applied": []}
                changed = True
            elif minus.is_file() and plus.is_file():
                self._apply_delta(minus, plus)
                self._db.setdefault("applied", []).append(seq)
                changed = True
        self._manifest, self._seq = manifest, seq
        self._version = chain_name(seq)
        return changed

    def _install_project(self, slug: str, seq: int, body: bytes) -> bool:
        with _Unpacked(self.data_dir, body) as folder:
            return self._merge_project(slug, seq, folder / "p" / slug)

    def _merge_project(self, slug: str, seq: int, folder: Path) -> bool:
        meta_file = folder / VERSION_FILE
        if not meta_file.is_file():
            raise KeyError(f"{slug} missing from save {seq}")
        meta = json.loads(meta_file.read_text("utf-8"))
        base = self._installed.get(slug, {}).get("files", {})
        changed = _merge_files(self._scope(slug), base, folder, meta.get("files", {}), self._hash_cache)
        self._installed[slug] = {"seq": seq, "files": meta.get("files", {}), "deleted": bool(meta.get("deleted"))}
        if meta.get("deleted"):
            _remove_if_empty(self._scope(slug).path)
        return changed

    def _apply_db(self, db: dict, bodies: dict) -> None:
        """Snapshot (when it changed), then each delta not applied yet."""
        snapshot = db.get("snapshot")
        if snapshot != self._db.get("snapshot"):
            if snapshot not in bodies:  # fetch each save object once (setdefault would fetch every time)
                bodies[snapshot] = self._get_save(snapshot)
            if bodies[snapshot] is None:
                raise KeyError(chain_name(snapshot))
            with _Unpacked(self.data_dir, bodies[snapshot]) as folder:
                if not (folder / "db.full").is_file():
                    raise KeyError(f"no database in save {snapshot}")
                self._merge_full(folder / "db.full")
            self._db = {"snapshot": snapshot, "applied": []}
        for seq in db.get("deltas") or []:
            if seq in self._db["applied"]:
                continue
            if seq not in bodies:  # fetch each save object once (setdefault would fetch every time)
                bodies[seq] = self._get_save(seq)
            if bodies[seq] is None:
                raise KeyError(chain_name(seq))
            with _Unpacked(self.data_dir, bodies[seq]) as folder:
                if (folder / "db.plus").is_file() and (folder / "db.minus").is_file():
                    self._apply_delta(folder / "db.minus", folder / "db.plus")
            self._db["applied"].append(seq)

    def _apply_delta(self, minus: Path, plus: Path) -> None:
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        clean = self._db_is_clean()
        if not self.db_file.is_file() or self.db_file.stat().st_size == 0:
            if self._base_db.is_file():
                _copy_db(self._base_db, self.db_file)
        if self.db_file.is_file():
            merge_sqlite(self.db_file, minus, plus)
        if self._base_db.is_file():
            merge_sqlite(self._base_db, minus, plus)
        self._db_clean = _stat_sig(self.db_file) if clean else None

    def _db_is_clean(self) -> bool:
        return self._db.get("snapshot") is not None and _stat_sig(self.db_file) == self._db_clean

    def _merge_full(self, theirs: Path) -> None:
        """Three-way merge of a full database snapshot into the local db (local unsaved rows are kept)."""
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_file.is_file() or self.db_file.stat().st_size == 0:
            _dispose_engines(self.db_file)
            _copy_db(theirs, self.db_file)
            clean = True
        else:
            clean = self._db_is_clean()
            base = self._base_db if self._base_db.is_file() else None
            merge_sqlite(self.db_file, base, theirs)
        _copy_db(theirs, self._base_db)
        self._db_clean = _stat_sig(self.db_file) if clean else None

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
                    return None
                return self._commit()
            except (StoreError, KeyError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not save to storage: {type(exc).__name__}."
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
                self._cold_sync()
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
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        snap = self._meta_dir / f".snap-{secrets.token_hex(4)}.db"
        minus = self._meta_dir / f".minus-{secrets.token_hex(4)}.db"
        plus = self._meta_dir / f".plus-{secrets.token_hex(4)}.db"
        db_kind, sig_before = None, None
        try:
            manifest = json.loads(json.dumps(self._manifest))
            db = manifest.setdefault("db", {"snapshot": None, "deltas": [], "sizes": {}})
            if self.db_file.is_file() and not self._db_is_clean():
                sig_before = _stat_sig(self.db_file)
                _sqlite_copy(self.db_file, snap)
                if not self._db.get("snapshot") or not self._base_db.is_file():
                    db_kind = "full"
                elif make_delta(snap, self._base_db, minus, plus):
                    delta_size = minus.stat().st_size + plus.stat().st_size
                    sizes = db.get("sizes") or {}
                    total = sum(int(sizes.get(str(d), 0)) for d in db.get("deltas") or []) + delta_size
                    snapshot_size = max(COMPACT_FLOOR_BYTES, int(sizes.get(str(db.get("snapshot")), 0)))
                    db_kind = "full" if len(db.get("deltas") or []) >= COMPACT_DELTAS or total > COMPACT_RATIO * snapshot_size else "delta"
            if not projects and db_kind is None and not self._extra:
                if sig_before is not None:
                    self._db_clean = sig_before  # same rows as the base: nothing to upload
                raise _NothingToSave()
            manifest["v"], manifest["seq"] = 3, seq
            for slug in projects:
                manifest.setdefault("projects", {})[slug] = seq
            if db_kind == "full":
                db["snapshot"], db["deltas"], db["sizes"] = seq, [], {str(seq): snap.stat().st_size}
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
                    packed: dict[str, str] = {}
                    contents: dict[str, bytes] = {}
                    for rel in files:
                        try:
                            data = (self._scope(slug).path / rel).read_bytes()
                        except (FileNotFoundError, IsADirectoryError):
                            continue
                        contents[rel] = data
                        packed[rel] = hashlib.sha256(data).hexdigest()
                    files.clear()
                    files.update(packed)
                    meta["files"] = dict(packed)
                    _add_bytes(archive, f"p/{slug}/{VERSION_FILE}", json.dumps(meta, sort_keys=True).encode("utf-8"))
                    for rel, data in contents.items():
                        _add_bytes(archive, f"p/{slug}/{rel}", data)
                if db_kind == "full":
                    archive.add(snap, arcname="db.full", recursive=False)
                elif db_kind == "delta":
                    archive.add(minus, arcname="db.minus", recursive=False)
                    archive.add(plus, arcname="db.plus", recursive=False)
            try:
                self.store.put(name, buffer.getvalue(), content_type="application/gzip")
            except ObjectExists:
                return None
            # Ours now. Record it locally exactly as a reader of this save would.
            for slug, (files, meta) in projects.items():
                self._installed[slug] = {"seq": seq, "files": files, "deleted": meta["deleted"]}
            if db_kind is not None:
                _copy_db(snap, self._base_db)
                self._db_clean = sig_before
                if db_kind == "full":
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
            for path in (snap, minus, plus):
                path.unlink(missing_ok=True)

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
        body = self.store.get(name)
        bases: dict = {}
        if not first:
            try:
                bases = json.loads(self.store.get(LEGACY_BASE).decode("utf-8"))
            except KeyError:
                bases = {}
        staging = self.data_dir.parent / f".{self.data_dir.name}-legacy-{secrets.token_hex(4)}"
        staging.mkdir(parents=True)
        seen: dict = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
                archive.extractall(staging, filter="data")
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


class _Unpacked:
    """Extract an object to a private staging folder; yields the folder and cleans up."""

    def __init__(self, data_dir: Path, body: bytes) -> None:
        self.data_dir, self.body = data_dir, body
        self.folder: Path | None = None

    def __enter__(self) -> Path:
        self.folder = self.data_dir.parent / f".{self.data_dir.name}-incoming-{secrets.token_hex(4)}"
        self.folder.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(self.body), mode="r:gz") as archive:
            archive.extractall(self.folder, filter="data")
        return self.folder

    def __exit__(self, *_exc):
        shutil.rmtree(self.folder, ignore_errors=True)


def _merge_files(local: _Scope, base: dict, theirs_dir: Path, theirs: dict, cache: dict) -> bool:
    """Three-way merge at file level into the local folder. Returns True when a local file changed.

    For each path: if theirs did not change it since base, keep local. If local did not change it since base, take
    theirs (copy, or delete when theirs removed it). If both changed it differently, local wins: it is the newer,
    unsaved work of this instance, and it will be saved next.
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
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".ranksim-tmp-{secrets.token_hex(4)}")
            shutil.copyfile(source, temp)  # a fresh mtime: copy2 would keep the tar's whole-second one
            os.replace(temp, target)
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
    conn = sqlite3.connect(str(ours), timeout=30, isolation_level=None)
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
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return counts


def _tables(conn, schema: str) -> dict[str, str]:
    rows = conn.execute(f"SELECT name, sql FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return {name: sql for name, sql in rows if sql}


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
