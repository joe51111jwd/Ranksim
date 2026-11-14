"""Durable state for a hosted Ranksim (Vercel): projects, stored pages and trained engines in a private Blob store.

A serverless instance only has an empty, temporary /tmp. Ranksim keeps working files there as usual, and this module
mirrors them to a private Vercel Blob store, one object per project plus one for the shared rank database:

  ranksim/v2/projects/<slug>/v/<time>-<rand>.tgz   immutable versions of one project folder
  ranksim/v2/projects/<slug>/HEAD                  pointer to that project's newest version (a hint)
  ranksim/v2/db/v/<time>-<rand>.db.gz              immutable versions of the SQLite rank database
  ranksim/v2/HEAD                                  changes on every save anywhere (a hint)
  ranksim/v2/migrated                              written once the old single tarball was imported

Design rules:

- Versions are immutable, so a cached download can never be stale. The pointers are hints: an instance heads the
  global pointer (a cheap call that is not served from a cache) and lists the versions when it changed, or every
  LIST_EVERY_SECONDS anyway, so a pointer write that failed or was slow can only delay, never hide, a newer save.
- Each project version records the version it was based on and a manifest (path -> sha256) of its files. Saving and
  loading are three-way merges at file level against that base: files only one side changed are taken from that side,
  and when both sides changed one file the side doing the merge keeps its own (it is the newer work). So two people
  working on different projects never clobber each other, and two writers on one project only collide on a file both
  changed. After writing, the writer lists again; a version that appeared meanwhile is merged and written once more.
- The rank database is shared by every project (pages and Google results are keyed by URL and search), so it cannot
  simply be split. It is merged row by row against the last synced copy (see merge_sqlite): rows added or removed on
  the other side are applied here, a search's Google results are replaced as a group (newest pull wins), and the
  hash-chained prediction ledger stays one valid chain (local rows never saved are re-chained after the other side's).
- A project deleted on one instance is saved as a tombstone version with no files, so the deletion reaches the others.
- The old format (one tarball of everything under ranksim/state/) is imported automatically on the first refresh that
  finds no new-format data, then saved in the new format. The old tarballs are left in place (nothing is deleted), so
  going back to an older deployment still finds them.

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
PROJECTS_PREFIX = ROOT_PREFIX + "projects/"
DB_PREFIX = ROOT_PREFIX + "db/v/"
DB_POINTER = ROOT_PREFIX + "db/HEAD"
POINTER = ROOT_PREFIX + "HEAD"
MIGRATED = ROOT_PREFIX + "migrated"
LEGACY_PREFIX = "ranksim/state/"
LEGACY_POINTER = "ranksim/HEAD"
# Kept for code that imported the old names.
PREFIX = PROJECTS_PREFIX

KEEP_VERSIONS = 3
PRUNE_AFTER_SECONDS = 600  # an old version is deleted only when it is this old, so a reader mid-download still finds it
REFRESH_EVERY_SECONDS = 2.0
LIST_EVERY_SECONDS = 45.0
POINTER_WAIT_SECONDS = 8.0
CONFIRM_ROUNDS = 3
SKIP_PARTS = {"llm-cache", "__pycache__"}
VERSION_FILE = ".ranksim-version.json"
LOOSE = "_files"  # pseudo project: files sitting directly in projects/
# Project folders, plus "_plugins" (workspace-wide plugin data: plugins.workspace_dir), saved as its own object with
# the same merge rules. Dot folders are never saved.
SLUG_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,199}|_plugins)$")


class CloudError(RuntimeError):
    pass


@dataclass
class CloudState:
    """Mirror of data_dir (holding projects/ and the rank database file) in a private object store."""

    data_dir: Path
    db_file: Path
    enabled: bool = False
    on_change: list = field(default_factory=list)
    store: ObjectStore | None = None
    _seen: str | None = None  # global pointer etag this instance last synced to
    _version: str | None = None  # newest version name this instance wrote or installed (for describe)
    _checked: float = 0.0
    _listed: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _installed: dict = field(default_factory=dict)  # slug -> {"version": name, "files": manifest, "deleted": bool}
    _db_version: str | None = None
    _db_clean: tuple | None = None  # (size, mtime_ns) of the local db right after the last sync
    _hash_cache: dict = field(default_factory=dict)
    _loaded: bool = False
    last_error: str = ""

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.db_file = Path(self.db_file)
        if self.enabled and self.store is None:
            self.store = BlobStore()

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
        self._installed = dict(data.get("installed") or {})
        self._db_version = data.get("db_version")
        clean = data.get("db_clean")
        self._db_clean = tuple(clean) if clean else None

    def _save_meta(self) -> None:
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        temp = self._meta_dir / f".state-{secrets.token_hex(4)}.json"
        temp.write_text(json.dumps({"installed": self._installed, "db_version": self._db_version, "db_clean": self._db_clean}), "utf-8")
        os.replace(temp, self._meta_dir / "state.json")

    # -- reading ---------------------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> bool:
        """Bring this instance up to the newest saved state. Returns True when local files changed."""
        if not self.enabled:
            return False
        with self._lock:
            self._load_meta()
            now = time.monotonic()
            if not force and now - self._checked < REFRESH_EVERY_SECONDS:
                return False
            try:
                etag = self.store.head(POINTER) or "none"
            except StoreError:
                etag = None  # the hint is unavailable: fall back to listing
            self._checked = now
            if etag is not None and etag == self._seen and now - self._listed < LIST_EVERY_SECONDS:
                return False
            try:
                changed = self._sync_all()
            except (StoreError, KeyError, OSError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not read storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            self._listed = time.monotonic()
            self._seen = etag
            self.last_error = ""
            if changed:
                for callback in list(self.on_change):
                    callback()
            return changed

    def _sync_all(self) -> bool:
        projects = self._remote_projects()
        db_versions = self.store.list(DB_PREFIX)
        changed = False
        if not projects and not db_versions and self.store.head(MIGRATED) is None:
            changed = self._migrate_legacy()
            projects = self._remote_projects()
            db_versions = self.store.list(DB_PREFIX)
        for slug, versions in sorted(projects.items()):
            newest = versions[-1]
            if self._installed.get(slug, {}).get("version") != newest:
                changed |= self._pull_project(slug, newest)
        if db_versions and db_versions[-1] != self._db_version:
            changed |= self._pull_db(db_versions[-1])
        self._save_meta()
        return changed

    def _remote_projects(self) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for name in self.store.list(PROJECTS_PREFIX):
            rest = name[len(PROJECTS_PREFIX):]
            slug, _sep, tail = rest.partition("/v/")
            if tail and SLUG_RE.match(slug) and tail.endswith(".tgz"):
                found.setdefault(slug, []).append(name)
        return {slug: sorted(names, key=lambda item: (stamp_of(item), item)) for slug, names in found.items()}

    def _pull_project(self, slug: str, version: str) -> bool:
        """Merge one stored project version into the local folder (local unsaved changes win conflicts)."""
        body = self.store.get(version)
        with _Unpacked(self.data_dir, body) as (folder, meta):
            base = self._installed.get(slug, {}).get("files", {})
            changed = _merge_files(self._scope(slug), base, folder, meta.get("files", {}), self._hash_cache)
        self._installed[slug] = {"version": version, "files": meta.get("files", {}), "deleted": bool(meta.get("deleted"))}
        self._version = version
        if meta.get("deleted"):
            _remove_if_empty(self._scope(slug).path)
        return changed

    def _pull_db(self, version: str) -> bool:
        body = gzip.decompress(self.store.get(version))
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        theirs = self._meta_dir / f".incoming-{secrets.token_hex(4)}.db"
        theirs.write_bytes(body)
        try:
            self._merge_db_from(theirs)
        finally:
            theirs.unlink(missing_ok=True)
        self._db_version = version
        return True

    def _merge_db_from(self, theirs: Path) -> None:
        """Apply what changed in theirs since the base copy to the local database, then make theirs the new base."""
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_file.is_file() or self.db_file.stat().st_size == 0:
            _dispose_engines(self.db_file)
            _copy_db(theirs, self.db_file)
            clean = True
        else:
            # Local rows not saved yet stay "dirty" after the merge, so the next save uploads them.
            clean = self._db_version is not None and _stat_sig(self.db_file) == self._db_clean
            base = self._base_db if self._base_db.is_file() else None
            merge_sqlite(self.db_file, base, theirs)
        _copy_db(theirs, self._base_db)
        self._db_clean = _stat_sig(self.db_file) if clean else None

    # -- writing ---------------------------------------------------------------------------------

    def save(self) -> str | None:
        """Upload every project and the database that changed since the last sync. Returns the last version written,
        or None when cloud storage is off or nothing changed."""
        if not self.enabled:
            return None
        with self._lock:
            self._load_meta()
            written: list[str] = []
            try:
                for slug in self._local_slugs():
                    name = self._save_project(slug)
                    if name:
                        written.append(name)
                for slug, info in sorted(self._installed.items()):
                    if not info.get("deleted") and slug != LOOSE and not self._scope(slug).path.is_dir():
                        written.append(self._write_tombstone(slug))
                name = self._save_db()
                if name:
                    written.append(name)
            except (StoreError, KeyError, OSError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not save to storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            finally:
                self._save_meta()
            if written:
                self._version = written[-1]
                self._seen = self._write_pointers(written)
                self._checked = self._listed = time.monotonic()
            self.last_error = ""
            return written[-1] if written else None

    def save_project(self, slug: str) -> str | None:
        """Save one project now (a job's checkpoint), without touching the others."""
        if not self.enabled:
            return None
        with self._lock:
            self._load_meta()
            try:
                name = self._save_project(slug) if self._scope(slug).path.is_dir() else None
                db = self._save_db()
            except (StoreError, KeyError, OSError, tarfile.TarError, sqlite3.Error) as exc:
                self.last_error = f"Could not save to storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            finally:
                self._save_meta()
            written = [item for item in (name, db) if item]
            if written:
                self._seen = self._write_pointers(written)
            return name

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

    def _save_project(self, slug: str) -> str | None:
        scope = self._scope(slug)
        prefix = f"{PROJECTS_PREFIX}{slug}/v/"
        ours: str | None = None
        for _round in range(CONFIRM_ROUNDS):
            remote = sorted(self.store.list(prefix), key=lambda item: (stamp_of(item), item))
            newest = remote[-1] if remote else None
            installed = self._installed.get(slug, {})
            if newest and newest != installed.get("version") and newest != ours:
                self._pull_project(slug, newest)  # someone else saved this project: merge their version first
                installed = self._installed[slug]
            files = _manifest(scope, self._hash_cache)
            if ours is not None and newest == ours:
                break  # nothing newer appeared after our write
            if files == installed.get("files") and not installed.get("deleted"):
                break
            if not files and not installed:
                break
            ours = new_name(prefix, ".tgz")
            meta = {"base": installed.get("version"), "files": files, "deleted": False, "slug": slug}
            self.store.put(ours, _pack(scope, files, meta), content_type="application/gzip")
            self._installed[slug] = {"version": ours, "files": files, "deleted": False}
        if len(remote) >= KEEP_VERSIONS:
            self._prune(prefix, keep=self._installed.get(slug, {}).get("version"))
        return ours

    def _write_tombstone(self, slug: str) -> str:
        prefix = f"{PROJECTS_PREFIX}{slug}/v/"
        installed = self._installed.get(slug, {})
        name = new_name(prefix, ".tgz")
        meta = {"base": installed.get("version"), "files": {}, "deleted": True, "slug": slug}
        self.store.put(name, _pack(self._scope(slug), {}, meta), content_type="application/gzip")
        self._installed[slug] = {"version": name, "files": {}, "deleted": True}
        self._prune(prefix, keep=name)
        return name

    def _save_db(self) -> str | None:
        if not self.db_file.is_file():
            return None
        ours: str | None = None
        for _round in range(CONFIRM_ROUNDS):
            remote = self.store.list(DB_PREFIX)
            newest = remote[-1] if remote else None
            if newest and newest != self._db_version and newest != ours:
                self._pull_db(newest)
            if ours is not None and newest == ours:
                break
            if self._db_version is not None and _stat_sig(self.db_file) == self._db_clean:
                break  # unchanged since the last sync
            self._meta_dir.mkdir(parents=True, exist_ok=True)
            copy = self._meta_dir / f".outgoing-{secrets.token_hex(4)}.db"
            try:
                _sqlite_copy(self.db_file, copy)
                ours = new_name(DB_PREFIX, ".db.gz")
                self.store.put(ours, gzip.compress(copy.read_bytes(), compresslevel=6), content_type="application/gzip")
                _copy_db(copy, self._base_db)
            finally:
                copy.unlink(missing_ok=True)
            self._db_version = ours
            # Taken after the copy, because closing the copy's connection may checkpoint the WAL into the main file.
            # A write landing in the instant between is not lost: it is uploaded with the next change.
            self._db_clean = _stat_sig(self.db_file)
        if len(remote) >= KEEP_VERSIONS:
            self._prune(DB_PREFIX, keep=self._db_version)
        return ours

    def _prune(self, prefix: str, keep: str | None) -> None:
        """Delete versions beyond the newest KEEP_VERSIONS once they are old enough. Costs storage when it fails."""
        try:
            names = sorted(self.store.list(prefix), key=lambda item: (stamp_of(item), item))
            cutoff = time.time_ns() - int(PRUNE_AFTER_SECONDS * 1e9)
            stale = [name for name in names[:-KEEP_VERSIONS] if name != keep and stamp_of(name) < cutoff]
            if stale:
                self.store.delete(stale)
        except StoreError:
            pass

    def _write_pointers(self, written: list[str]) -> str | None:
        """Update the hints, waiting a few seconds at most. Returns the global pointer's etag when it was written."""
        box: dict = {}
        latest = written[-1]
        slugs = {name[len(PROJECTS_PREFIX):].split("/v/")[0] for name in written if name.startswith(PROJECTS_PREFIX)}

        def write() -> None:
            try:
                for slug in sorted(slugs):
                    newest = self._installed.get(slug, {}).get("version")
                    if newest:
                        self.store.put(f"{PROJECTS_PREFIX}{slug}/HEAD", newest.encode(), overwrite=True, content_type="text/plain")
                if any(name.startswith(DB_PREFIX) for name in written) and self._db_version:
                    self.store.put(DB_POINTER, self._db_version.encode(), overwrite=True, content_type="text/plain")
                box["etag"] = self.store.put(POINTER, latest.encode(), overwrite=True, content_type="text/plain")
            except Exception as exc:  # noqa: BLE001 - the pointers are hints; listing finds the versions anyway
                box["error"] = type(exc).__name__

        worker = threading.Thread(target=write, name="ranksim-pointer", daemon=True)
        worker.start()
        worker.join(POINTER_WAIT_SECONDS)
        if "error" in box or worker.is_alive():
            print(f"ranksim: storage pointer not updated ({box.get('error', 'slow')}); other servers will find the save by listing.", flush=True)
        return box.get("etag")

    def project_changed_remotely(self, slug: str) -> bool:
        """True when another instance saved this project since we synced it (one cheap head call)."""
        if not self.enabled:
            return False
        try:
            body_tag = self.store.head(f"{PROJECTS_PREFIX}{slug}/HEAD")
        except StoreError:
            return True
        if body_tag is None:
            return False
        try:
            pointed = self.store.get(f"{PROJECTS_PREFIX}{slug}/HEAD").decode("utf-8", "replace").strip()
        except (StoreError, KeyError):
            return True
        return pointed != self._installed.get(slug, {}).get("version")

    # -- the old single tarball ------------------------------------------------------------------

    def _migrate_legacy(self) -> bool:
        """Import the newest old-format tarball, then save it in the new format. The old tarballs are kept."""
        legacy = sorted(self.store.list(LEGACY_PREFIX))
        if not legacy:
            return False
        newest = legacy[-1]
        body = self.store.get(newest)
        staging = self.data_dir.parent / f".{self.data_dir.name}-legacy-{secrets.token_hex(4)}"
        staging.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
                archive.extractall(staging, filter="data")
            incoming = staging / "projects"
            if incoming.is_dir():
                self.projects_dir.mkdir(parents=True, exist_ok=True)
                for slug in sorted(path.name for path in incoming.iterdir() if path.is_dir()):
                    theirs = _Scope(incoming / slug, recursive=True)
                    # local files win; a fresh instance has none, so everything is taken
                    _merge_files(self._scope(slug), {}, theirs.path, _manifest(theirs, {}), self._hash_cache)
                loose = _Scope(incoming, recursive=False)
                loose_files = _manifest(loose, {})
                if loose_files:
                    _merge_files(self._scope(LOOSE), {}, incoming, loose_files, self._hash_cache)
            incoming_db = staging / "rank.db"
            if incoming_db.is_file():
                if self.db_file.is_file() and self.db_file.stat().st_size:
                    merge_sqlite(self.db_file, None, incoming_db)
                else:
                    self.db_file.parent.mkdir(parents=True, exist_ok=True)
                    _dispose_engines(self.db_file)
                    _copy_db(incoming_db, self.db_file)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for slug in self._local_slugs():
            self._save_project(slug)
        self._save_db()
        self.store.put(MIGRATED, newest.encode(), overwrite=True, content_type="text/plain")
        print(f"ranksim: imported the old storage format ({newest.rsplit('/', 1)[-1]}) into per-project storage.", flush=True)
        return True


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


def _pack(scope: _Scope, files: dict[str, str], meta: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
        encoded = json.dumps(meta, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(VERSION_FILE)
        info.size, info.mtime = len(encoded), int(time.time())
        archive.addfile(info, io.BytesIO(encoded))
        for rel in files:
            archive.add(scope.path / rel, arcname=rel, recursive=False)
    return buffer.getvalue()


class _Unpacked:
    """Extract a project version to a private staging folder; yields (folder, meta) and cleans up."""

    def __init__(self, data_dir: Path, body: bytes) -> None:
        self.data_dir, self.body = data_dir, body
        self.folder: Path | None = None

    def __enter__(self):
        self.folder = self.data_dir.parent / f".{self.data_dir.name}-incoming-{secrets.token_hex(4)}"
        self.folder.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(self.body), mode="r:gz") as archive:
            archive.extractall(self.folder, filter="data")
        meta_file = self.folder / VERSION_FILE
        meta = json.loads(meta_file.read_text("utf-8")) if meta_file.is_file() else {}
        if "files" not in meta:  # a version without a manifest: describe what it holds
            meta["files"] = _manifest(_Scope(self.folder, recursive=True), {})
        return self.folder, meta

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
    """Keep one hash chain: theirs' rows first, then our unsaved rows re-chained on top of theirs."""
    q = _q(table)
    names = ", ".join(_q(col) for col in cols)
    new_theirs = conn.execute(
        f"SELECT {names} FROM t.{q} WHERE row_hash NOT IN (SELECT row_hash FROM main.{q}) ORDER BY rowid"
    ).fetchall()
    if not new_theirs:
        return 0
    local_only = conn.execute(
        f"SELECT {names} FROM main.{q} WHERE row_hash NOT IN (SELECT row_hash FROM t.{q}) "
        f"AND row_hash NOT IN (SELECT row_hash FROM b.{q}) ORDER BY rowid"
    ).fetchall()
    if local_only:
        conn.execute(f"DELETE FROM main.{q} WHERE row_hash NOT IN (SELECT row_hash FROM t.{q}) AND row_hash NOT IN (SELECT row_hash FROM b.{q})")
    marks = ", ".join("?" for _ in cols)
    for row in new_theirs:
        conn.execute(f"INSERT INTO main.{q} ({names}) VALUES ({marks})", row)
    if local_only:
        from aeo.ranksim.ledger import _row_hash

        index = {col: i for i, col in enumerate(cols)}
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
