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

from aeo.ranksim.objects import BlobStore, ObjectStore, StoreError, new_name, stamp_of

ROOT_PREFIX = "ranksim/v2/"
SAVES_PREFIX = ROOT_PREFIX + "s/"
POINTER = ROOT_PREFIX + "HEAD"  # the manifest
LEGACY_BASE = ROOT_PREFIX + "legacy-base.json"
LEGACY_PREFIX = "ranksim/state/"
LEGACY_POINTER = "ranksim/HEAD"
# Names of the first per-project layout (phase A), read once by the bootstrap if a store still holds them.
PROJECTS_PREFIX = ROOT_PREFIX + "projects/"
DB_PREFIX = ROOT_PREFIX + "db/v/"
PREFIX = SAVES_PREFIX

# Blob operation budget (Vercel Hobby: 10,000 simple and 2,000 advanced operations a month, and Blob is blocked for
# 30 days once a cap is passed). Ordinary requests make no Blob call at all:
REFRESH_EVERY_SECONDS = 300.0  # an active instance checks the manifest (one head) at most this often
HINT_MIN_SECONDS = 5.0  # a stale-hint cookie can force a check at most this often per instance
LEGACY_WATCH_SECONDS = 24 * 3600  # after the migration, watch the old pointer (one head per check) for a day
KEEP_RECENT = 12  # save objects remembered for pruning; unreferenced ones beyond this are deleted (deletes are free)
PRUNE_AFTER_SECONDS = 600  # never delete an object younger than this: a reader mid-download still finds it
COMPACT_DELTAS = 15  # after this many database deltas the next save uploads a full snapshot
COMPACT_RATIO = 0.5  # or when the deltas add up to half the snapshot's size (counted from COMPACT_FLOOR_BYTES up)
COMPACT_FLOOR_BYTES = 4 * 1024 * 1024
POINTER_WAIT_SECONDS = 8.0
CONFIRM_ROUNDS = 3
SKIP_PARTS = {"llm-cache", "__pycache__"}
VERSION_FILE = ".ranksim-version.json"
LOOSE = "_files"  # pseudo project: files sitting directly in projects/
# Project folders, plus "_plugins" (workspace-wide plugin data: plugins.workspace_dir), saved with the same merge
# rules. Dot folders are never saved.
SLUG_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,199}|_plugins)$")


class CloudError(RuntimeError):
    pass


def _empty_manifest() -> dict:
    return {"v": 2, "stamp": 0, "projects": {}, "db": {"snapshot": None, "deltas": [], "sizes": {}}, "auth": {"epoch": 0, "lock_until": 0}, "recent": [], "legacy": {}}


@dataclass
class CloudState:
    """Mirror of data_dir (holding projects/ and the rank database file) in a private object store."""

    data_dir: Path
    db_file: Path
    enabled: bool = False
    on_change: list = field(default_factory=list)
    store: ObjectStore | None = None
    clock: object = None  # monotonic clock, injectable for tests
    _seen: str | None = None  # manifest etag this instance last read
    _manifest: dict = field(default_factory=_empty_manifest)
    _version: str | None = None
    _checked: float | None = None
    _hinted: float = -1e18
    _legacy_checked: float = -1e18
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _installed: dict = field(default_factory=dict)  # slug -> {"version": save name, "files": manifest, "deleted": bool}
    _db: dict = field(default_factory=lambda: {"snapshot": None, "applied": []})
    _db_clean: tuple | None = None
    _pending_db: list = field(default_factory=list)  # database saves uploaded but not in the manifest yet
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
        """The newest save this instance knows (nanoseconds). The app sends it to the browser as a hint."""
        return int(self._manifest.get("stamp") or 0)

    @property
    def auth(self) -> dict:
        """Shared login state from the manifest: {"epoch": int, "lock_until": float}."""
        return dict(self._manifest.get("auth") or {"epoch": 0, "lock_until": 0})

    def describe(self) -> dict:
        return {"enabled": self.enabled, "version": self._version, "error": self.last_error, "projects": len(self._installed)}

    # -- bookkeeping that survives a restart of a local process ---------------------------------

    def _load_meta(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads((self._meta_dir / "state.json").read_text("utf-8"))
        except (OSError, ValueError):
            return
        if data.get("v") != 2:
            return  # the phase A layout: start over (the bootstrap reads it from the store)
        self._installed = dict(data.get("installed") or {})
        self._db = dict(data.get("db") or {"snapshot": None, "applied": []})
        clean = data.get("db_clean")
        self._db_clean = tuple(clean) if clean else None

    def _save_meta(self) -> None:
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        temp = self._meta_dir / f".state-{secrets.token_hex(4)}.json"
        temp.write_text(json.dumps({"v": 2, "installed": self._installed, "db": self._db, "db_clean": self._db_clean}), "utf-8")
        os.replace(temp, self._meta_dir / "state.json")

    # -- reading ---------------------------------------------------------------------------------

    def refresh(self, *, force: bool = False, hint: int = 0) -> bool:
        """Bring this instance up to the newest saved state. Returns True when local files changed.

        Without force, this makes no Blob call unless the instance has not checked yet (cold start), the last check is
        REFRESH_EVERY_SECONDS old, or hint (the newest save the browser has seen, from its cookie) is newer than what
        this instance knows. A check is one head of the manifest; the manifest and changed saves are read only when
        it changed.
        """
        if not self.enabled:
            return False
        with self._lock:
            self._load_meta()
            now = self.clock()
            if not force:
                stale_hint = hint > self.stamp and now - self._hinted >= HINT_MIN_SECONDS
                due = self._checked is None or now - self._checked >= REFRESH_EVERY_SECONDS
                if not (stale_hint or due):
                    return False
                if stale_hint:
                    self._hinted = now
            try:
                changed = self._sync()
            except (StoreError, KeyError, OSError, ValueError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not read storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            self._checked = self.clock()
            self.last_error = ""
            if changed:
                for callback in list(self.on_change):
                    callback()
            return changed

    def _sync(self) -> bool:
        etag = self.store.head(POINTER)
        if etag is None:
            changed = self._bootstrap()
        elif etag == self._seen:
            changed = False
        else:
            changed = self._apply(self._read_manifest(etag), etag)
        changed |= self._watch_legacy()
        self._save_meta()
        return changed

    def _read_manifest(self, etag: str | None) -> dict:
        data = json.loads(self.store.get(POINTER).decode("utf-8"))
        if not isinstance(data, dict) or data.get("v") != 2:
            raise ValueError("bad manifest")
        base = _empty_manifest()
        base.update(data)
        return base

    def _apply(self, manifest: dict, etag: str | None) -> bool:
        """Install every project and database change the manifest names that this instance does not have yet."""
        changed = False
        bodies: dict[str, bytes] = {}
        for slug, name in sorted((manifest.get("projects") or {}).items()):
            mine = self._installed.get(slug, {})
            if mine.get("base") == name and mine.get("version") != name:
                continue  # our newer save of this project is on its way into the manifest
            if SLUG_RE.match(slug) and mine.get("version") != name:
                if name not in bodies:
                    bodies[name] = self.store.get(name)
                changed |= self._install_project(slug, name, bodies[name])
        db = manifest.get("db") or {}
        if db.get("snapshot") and (db["snapshot"] != self._db.get("snapshot") or any(d not in self._db.get("applied", []) for d in db.get("deltas") or [])):
            self._apply_db(db, bodies)
            changed = True
        self._manifest, self._seen = manifest, etag
        return changed

    def _install_project(self, slug: str, name: str, body: bytes) -> bool:
        with _Unpacked(self.data_dir, body) as folder:
            meta_file = folder / "p" / slug / VERSION_FILE
            if not meta_file.is_file():
                raise KeyError(f"{slug} missing from {name}")
            meta = json.loads(meta_file.read_text("utf-8"))
            mine = self._installed.get(slug, {})
            # While our own save is not listed yet, the common ancestor is the version it was based on.
            base = mine.get("base_files", mine.get("files", {})) if "base" in mine else mine.get("files", {})
            changed = _merge_files(self._scope(slug), base, folder / "p" / slug, meta.get("files", {}), self._hash_cache)
        self._installed[slug] = {"version": name, "files": meta.get("files", {}), "deleted": bool(meta.get("deleted"))}
        self._version = name
        if meta.get("deleted"):
            _remove_if_empty(self._scope(slug).path)
        return changed

    def _apply_db(self, db: dict, bodies: dict) -> None:
        """Snapshot (when it changed), then each delta not applied yet, merged into the local db and the base copy."""
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        snapshot = db.get("snapshot")
        if snapshot != self._db.get("snapshot"):
            body = bodies.get(snapshot) or self.store.get(snapshot)
            bodies[snapshot] = body
            with _Unpacked(self.data_dir, body) as folder:
                full = folder / "db.full"
                if not full.is_file():
                    raise KeyError(f"no database in {snapshot}")
                self._merge_full(full)
            self._db = {"snapshot": snapshot, "applied": []}
        for name in db.get("deltas") or []:
            if name in self._db["applied"]:
                continue
            body = bodies.get(name) or self.store.get(name)
            bodies[name] = body
            with _Unpacked(self.data_dir, body) as folder:
                minus, plus = folder / "db.minus", folder / "db.plus"
                if plus.is_file() and minus.is_file():
                    clean = self._db_is_clean()
                    if not self.db_file.is_file() or self.db_file.stat().st_size == 0:
                        _copy_db(self._base_db, self.db_file)
                    merge_sqlite(self.db_file, minus, plus)
                    merge_sqlite(self._base_db, minus, plus)
                    self._db_clean = _stat_sig(self.db_file) if clean else None
            self._db["applied"].append(name)

    def _db_is_clean(self) -> bool:
        return self._db.get("snapshot") is not None and _stat_sig(self.db_file) == self._db_clean

    def _merge_full(self, theirs: Path) -> None:
        """Three-way merge of a full database snapshot into the local db (local unsaved rows are kept)."""
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
        """Upload what changed since the last sync as one save object, then point the manifest at it.

        Nothing changed: no Blob call. Otherwise: one head (plus reads only when another instance saved meanwhile),
        one put of the save object, one head and one put of the manifest. Returns the save's name, or None.
        """
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
        """Kept for callers of the phase A API: a save covers every changed project in one object anyway."""
        return self.save()

    def _dirty(self) -> bool:
        for slug in self._local_slugs():
            if _manifest(self._scope(slug), self._hash_cache) != self._installed.get(slug, {}).get("files") or self._installed.get(slug, {}).get("deleted"):
                if _manifest(self._scope(slug), self._hash_cache) or self._installed.get(slug):
                    return True
        if any(not info.get("deleted") and slug != LOOSE and not self._scope(slug).path.is_dir() for slug, info in self._installed.items()):
            return True
        return self.db_file.is_file() and not self._db_is_clean()

    def _commit(self) -> str | None:
        fresh = self._checked is not None and self._seen is not None and self.clock() - self._checked < 2.0
        etag = self._seen if fresh else self.store.head(POINTER)  # the write's own check a moment ago is enough
        if etag is None and self._seen is None and not self._manifest.get("stamp"):
            self._bootstrap()  # a first save on an empty store still checks for older formats first
            if not self._dirty():
                return self._version  # the bootstrap saved everything
            etag = self.store.head(POINTER)
        if etag is not None and etag != self._seen:
            self._apply(self._read_manifest(etag), etag)  # merge what another instance saved first
        ours = self._write_save()
        if ours is None:
            return None
        for _round in range(CONFIRM_ROUNDS):
            if self._put_manifest(ours):
                return ours
            # Another save landed between our upload and the manifest write: merge it, and if that changed a project
            # we had saved (both sides edited it), upload the merged result as a new save.
            etag = self.store.head(POINTER)
            self._apply(self._read_manifest(etag), etag)
            if self._dirty():
                ours = self._write_save() or ours
        print("ranksim: storage was busy; this save will be listed by the next one.", flush=True)
        return ours

    def _manifest_has(self, name: str) -> bool:
        m = self._manifest
        return name in (m.get("projects") or {}).values() or name in (m.get("db") or {}).get("deltas", []) or name == (m.get("db") or {}).get("snapshot")

    def _write_save(self) -> str | None:
        """Pack every changed project and the database delta (or a full snapshot) into one immutable object."""
        projects: dict[str, tuple[dict, dict]] = {}
        for slug in self._local_slugs():
            files = _manifest(self._scope(slug), self._hash_cache)
            installed = self._installed.get(slug, {})
            if files == installed.get("files") and not installed.get("deleted"):
                continue
            if not files and not installed:
                continue
            projects[slug] = (files, {"base": installed.get("version"), "files": files, "deleted": False, "slug": slug})
        for slug, info in sorted(self._installed.items()):
            if not info.get("deleted") and slug != LOOSE and not self._scope(slug).path.is_dir():
                projects[slug] = ({}, {"base": info.get("version"), "files": {}, "deleted": True, "slug": slug})
        name = new_name(SAVES_PREFIX, ".tgz")
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        snap = self._meta_dir / f".snap-{secrets.token_hex(4)}.db"
        minus = self._meta_dir / f".minus-{secrets.token_hex(4)}.db"
        plus = self._meta_dir / f".plus-{secrets.token_hex(4)}.db"
        db_kind = None
        try:
            if self.db_file.is_file() and not self._db_is_clean():
                sig_before = _stat_sig(self.db_file)
                _sqlite_copy(self.db_file, snap)
                db = self._manifest.get("db") or {}
                sizes = db.get("sizes") or {}
                if not self._db.get("snapshot") or not self._base_db.is_file():
                    db_kind = "full"
                else:
                    changed_rows = make_delta(snap, self._base_db, minus, plus)
                    if changed_rows:
                        delta_size = minus.stat().st_size + plus.stat().st_size
                        total = sum(int(v) for k, v in sizes.items() if k in (db.get("deltas") or [])) + delta_size
                        too_many = len(db.get("deltas") or []) >= COMPACT_DELTAS
                        snapshot_size = max(COMPACT_FLOOR_BYTES, int(sizes.get(db.get("snapshot"), 0)))
                        db_kind = "full" if too_many or total > COMPACT_RATIO * snapshot_size else "delta"
                    else:
                        self._db_clean = sig_before  # same rows as the base: nothing to upload
            if not projects and db_kind is None:
                return None
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
                for slug, (files, meta) in sorted(projects.items()):
                    _add_bytes(archive, f"p/{slug}/{VERSION_FILE}", json.dumps(meta, sort_keys=True).encode("utf-8"))
                    for rel in files:
                        archive.add(self._scope(slug).path / rel, arcname=f"p/{slug}/{rel}", recursive=False)
                if db_kind == "full":
                    archive.add(snap, arcname="db.full", recursive=False)
                elif db_kind == "delta":
                    archive.add(minus, arcname="db.minus", recursive=False)
                    archive.add(plus, arcname="db.plus", recursive=False)
            body = buffer.getvalue()
            self.store.put(name, body, content_type="application/gzip")
            for slug, (files, meta) in projects.items():
                before = self._installed.get(slug, {})
                ancestor = before.get("base_files", before.get("files", {})) if "base" in before else before.get("files", {})
                self._installed[slug] = {
                    "version": name, "files": files, "deleted": meta["deleted"],
                    "base": before.get("base", meta["base"]) if "base" in before else meta["base"],
                    "base_files": ancestor,  # what both sides started from, for a merge before the manifest lists us
                }
            if db_kind is not None:
                _copy_db(snap, self._base_db)
                self._db_clean = sig_before
                if db_kind == "full":
                    self._db = {"snapshot": name, "applied": []}
                else:
                    self._db["applied"].append(name)
            if db_kind is not None:
                size = snap.stat().st_size if db_kind == "full" else minus.stat().st_size + plus.stat().st_size
                self._pending_db.append({"name": name, "kind": db_kind, "size": size})
            self._version = name
            return name
        finally:
            for path in (snap, minus, plus):
                path.unlink(missing_ok=True)

    def _put_manifest(self, ours: str) -> bool:
        """Re-check the manifest, add our save, write it. False when another save landed in between (merge again)."""
        etag = self.store.head(POINTER)
        if etag is not None and etag != self._seen:
            return False
        manifest = json.loads(json.dumps(self._manifest))
        written = set()
        for slug, info in self._installed.items():
            if "base" in info and info["base"] == manifest["projects"].get(slug) and info["version"] != manifest["projects"].get(slug):
                manifest["projects"][slug] = info["version"]  # our save of this project, not listed yet
                written.add(info["version"])
        db = manifest.setdefault("db", {"snapshot": None, "deltas": [], "sizes": {}})
        for item in self._pending_db:
            if item["kind"] == "full":
                db["snapshot"], db["deltas"], db["sizes"] = item["name"], [], {item["name"]: item["size"]}
            else:
                db["deltas"] = list(db.get("deltas") or []) + [item["name"]]
                db.setdefault("sizes", {})[item["name"]] = item["size"]
            written.add(item["name"])
        written.add(ours)
        manifest["stamp"] = max([int(manifest.get("stamp") or 0)] + [stamp_of(name) for name in written])
        manifest["recent"] = list(manifest.get("recent") or []) + sorted(name for name in written if name not in (manifest.get("recent") or []))
        self._prune(manifest)
        new_etag = self._write_pointer(manifest)
        self._manifest, self._seen = manifest, new_etag
        self._pending_db = []
        for info in self._installed.values():
            info.pop("base", None)
            info.pop("base_files", None)
        self._checked = self.clock()
        return True

    def _prune(self, manifest: dict) -> None:
        """Delete save objects nothing references any more. Deletes are free; no listing is needed."""
        referenced = set((manifest.get("projects") or {}).values())
        db = manifest.get("db") or {}
        referenced |= set(db.get("deltas") or []) | {db.get("snapshot")}
        recent = list(manifest.get("recent") or [])
        cutoff = time.time_ns() - int(PRUNE_AFTER_SECONDS * 1e9)
        old = [name for name in recent[:-KEEP_RECENT] if name not in referenced]
        doomed = [name for name in old if stamp_of(name) < cutoff]
        manifest["recent"] = [name for name in recent if name not in doomed]
        db["sizes"] = {k: v for k, v in (db.get("sizes") or {}).items() if k in referenced}
        if doomed:
            try:
                self.store.delete(doomed)
            except StoreError:
                manifest["recent"] = doomed + manifest["recent"]  # try again next time

    def _write_pointer(self, manifest: dict) -> str | None:
        return self.store.put(POINTER, json.dumps(manifest, sort_keys=True).encode("utf-8"), overwrite=True, content_type="application/json")

    def update_auth(self, **changes) -> dict:
        """Change the shared login state (revocation epoch, lockout) in the manifest: one head and one put."""
        if not self.enabled:
            return self.auth
        with self._lock:
            self._load_meta()
            for _round in range(CONFIRM_ROUNDS):
                etag = self.store.head(POINTER)
                if etag is not None and etag != self._seen:
                    self._apply(self._read_manifest(etag), etag)
                manifest = json.loads(json.dumps(self._manifest))
                auth = manifest.setdefault("auth", {"epoch": 0, "lock_until": 0})
                for key, value in changes.items():
                    auth[key] = max(auth.get(key) or 0, value) if key in {"epoch", "lock_until", "revoked_ms"} else value
                if self.store.head(POINTER) != etag:
                    continue
                self._seen = self._write_pointer(manifest)
                self._manifest = manifest
                break
            self._save_meta()
            return self.auth

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

    # -- first boot, and the older formats --------------------------------------------------------

    def _bootstrap(self) -> bool:
        """No manifest yet. Import the phase A layout or the old single tarball if a store holds one, then save
        everything as the first manifest. Costs a few lists once per store, never again."""
        changed = False
        legacy = sorted(self.store.list(LEGACY_PREFIX))
        phase_a = self.store.list(PROJECTS_PREFIX) + self.store.list(DB_PREFIX)
        if phase_a:
            changed |= self._import_phase_a(phase_a)
        elif legacy:
            changed |= self._import_legacy(legacy[-1], first=True)
        self._seen = None
        if self._dirty() or legacy or phase_a:
            name = self._write_save()
            manifest = _empty_manifest()
            if legacy:
                manifest["legacy"] = {"name": legacy[-1], "etag": self.store.head(LEGACY_POINTER), "since": time.time()}
            self._manifest = manifest
            if name:
                self._put_manifest(name)
            else:
                self._seen = self._write_pointer(manifest)
        else:
            self._manifest = _empty_manifest()
            self._seen = self._write_pointer(self._manifest)  # an empty store: written once, so nobody lists again
        return changed

    def _import_phase_a(self, names: list[str]) -> bool:
        newest: dict[str, str] = {}
        for name in names:
            if name.startswith(PROJECTS_PREFIX) and name.endswith(".tgz") and "/v/" in name:
                slug = name[len(PROJECTS_PREFIX):].split("/v/")[0]
                if SLUG_RE.match(slug) and (slug not in newest or stamp_of(name) > stamp_of(newest[slug])):
                    newest[slug] = name
        for slug, name in sorted(newest.items()):
            with _Unpacked(self.data_dir, self.store.get(name)) as folder:
                meta_file = folder / VERSION_FILE
                meta = json.loads(meta_file.read_text("utf-8")) if meta_file.is_file() else {}
                if meta.get("deleted"):
                    continue
                theirs = meta.get("files") or _manifest(_Scope(folder, recursive=True), {})
                _merge_files(self._scope(slug), {}, folder, theirs, self._hash_cache)
        dbs = sorted(name for name in names if name.startswith(DB_PREFIX))
        if dbs:
            self._meta_dir.mkdir(parents=True, exist_ok=True)
            incoming = self._meta_dir / f".incoming-{secrets.token_hex(4)}.db"
            incoming.write_bytes(gzip.decompress(self.store.get(dbs[-1])))
            try:
                self._merge_full(incoming)
                self._db_clean = None
            finally:
                incoming.unlink(missing_ok=True)
        return True

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
        if now - self._legacy_checked < REFRESH_EVERY_SECONDS:
            return False
        self._legacy_checked = now
        etag = self.store.head(LEGACY_POINTER)
        if etag is None or etag == legacy.get("etag"):
            return False
        names = sorted(self.store.list(LEGACY_PREFIX))
        if not names or names[-1] == legacy.get("name"):
            return False
        self._import_legacy(names[-1], first=False)
        self._manifest.setdefault("legacy", {}).update({"name": names[-1], "etag": etag})
        if self._dirty():
            self._commit()
        else:
            self.update_auth()  # writes the manifest with the new legacy marker
        return True


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
            if set(rel.parts) & SKIP_PARTS or rel.name == VERSION_FILE or rel.name.startswith(".ranksim-tmp-"):
                continue
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
        key = (str(path), stat.st_size, stat.st_mtime_ns)
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
            shutil.copy2(source, temp)
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
