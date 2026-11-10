"""Durable state for a hosted Ranksim (Vercel): projects, stored pages and trained engines in a private Blob store.

A serverless instance only has an empty, temporary /tmp. Ranksim keeps working files there as usual, and this module
mirrors them to a private Vercel Blob store:

- save() packs the data folder (the projects folder plus a consistent copy of the SQLite rank database) into one
  gzipped tar, uploads it under a new immutable name, then rewrites a small pointer blob naming that version.
- refresh() asks for the pointer's metadata (a cheap call that is not served from a cache). When it changed since this
  instance last synced, or every LIST_EVERY_SECONDS anyway, it lists the versions, downloads the newest and swaps it in.
  The pointer write is best effort: a save counts once its immutable version is stored.

Versions are immutable, so a cached download can never be stale. One owner works at a time; two writers at once means
the last save wins, and the losing request is not merged. Nothing here runs unless BLOB_READ_WRITE_TOKEN is set.
"""

from __future__ import annotations

import io
import os
import secrets
import shutil
import sqlite3
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "ranksim/state/"
POINTER = "ranksim/HEAD"
KEEP_VERSIONS = 3
REFRESH_EVERY_SECONDS = 2.0
# The pointer is a hint. Every LIST_EVERY_SECONDS an active instance also lists the versions, so a pointer write that
# failed or was slow can only delay, never hide, a newer save.
LIST_EVERY_SECONDS = 45.0
POINTER_WAIT_SECONDS = 8.0
SKIP_PARTS = {"llm-cache", "__pycache__"}


class CloudError(RuntimeError):
    pass


@dataclass
class CloudState:
    """Mirror of data_dir (holding projects/ and the rank database file) in a private Blob store."""

    data_dir: Path
    db_file: Path
    enabled: bool = False
    on_change: list = field(default_factory=list)
    _seen: str | None = None  # pointer etag this instance last synced to
    _version: str | None = None
    _checked: float = 0.0
    _listed: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _written: list[str] = field(default_factory=list)
    last_error: str = ""

    @classmethod
    def from_env(cls, data_dir: Path, db_file: Path) -> "CloudState":
        return cls(data_dir=Path(data_dir), db_file=Path(db_file), enabled=bool(os.environ.get("BLOB_READ_WRITE_TOKEN")))

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def describe(self) -> dict:
        return {"enabled": self.enabled, "version": self._version, "error": self.last_error}

    # -- reading ---------------------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> bool:
        """Bring this instance up to the newest saved state. Returns True when files were replaced."""
        if not self.enabled:
            return False
        with self._lock:
            now = time.monotonic()
            if not force and now - self._checked < REFRESH_EVERY_SECONDS:
                return False
            from vercel.blob import sync as blob

            try:
                head = _retry(blob.head_blob, POINTER)
                etag = head.etag
            except blob.BlobNotFoundError:
                etag = "none"  # nothing pointed at yet; versions may still exist
            except blob.BlobError:
                etag = None  # the hint is unavailable: fall back to listing
            self._checked = now
            if etag is not None and etag == self._seen and now - self._listed < LIST_EVERY_SECONDS:
                return False
            try:
                versions = sorted((item.pathname for item in _retry(lambda: list(blob.query_blobs(prefix=PREFIX)))), reverse=True)
            except blob.BlobError as exc:
                self.last_error = f"Could not reach storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            self._listed = now
            if not versions or versions[0] == self._version:
                self._seen = etag
                return False
            newest = versions[0]
            try:
                body = _retry(blob.get_blob, newest, access="private").body
            except blob.BlobError as exc:
                self.last_error = f"Could not read storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            self._install(body)
            self._version, self._seen = newest, etag
            self.last_error = ""
            for callback in list(self.on_change):
                callback()
            return True

    def _install(self, body: bytes) -> None:
        staging = self.data_dir.parent / f".{self.data_dir.name}-incoming-{secrets.token_hex(4)}"
        staging.mkdir(parents=True)
        try:
            with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
                archive.extractall(staging, filter="data")
            self.data_dir.mkdir(parents=True, exist_ok=True)
            incoming_db = staging / "rank.db"
            if incoming_db.is_file():
                _dispose_engines(self.db_file)
                os.replace(incoming_db, self.db_file)
            incoming_projects = staging / "projects"
            target = self.data_dir / "projects"
            if incoming_projects.is_dir():
                old = self.data_dir / f".projects-old-{secrets.token_hex(4)}"
                if target.exists():
                    os.replace(target, old)
                os.replace(incoming_projects, target)
                shutil.rmtree(old, ignore_errors=True)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # -- writing ---------------------------------------------------------------------------------

    def save(self) -> str | None:
        """Upload the current files as a new version. Returns the version name, or None when cloud storage is off."""
        if not self.enabled:
            return None
        with self._lock:
            from vercel.blob import sync as blob

            body = self.pack()
            name = f"{PREFIX}{time.time_ns():020d}-{secrets.token_hex(3)}.tgz"
            try:
                _retry(blob.put_blob, name, body, access="private", content_type="application/gzip")
            except blob.BlobError as exc:
                self.last_error = f"Could not save to storage: {type(exc).__name__}."
                raise CloudError(self.last_error) from exc
            previous, self._version = self._version, name
            self._seen = self._write_pointer(name)
            self._checked = self._listed = time.monotonic()
            self.last_error = ""
            self._written.append(name)
            if previous and previous not in self._written:
                self._written.insert(0, previous)
            stale = self._written[:-KEEP_VERSIONS]
            if stale:
                try:
                    blob.delete_blob(stale)
                    self._written = self._written[-KEEP_VERSIONS:]
                except blob.BlobError:
                    pass  # an old version left behind costs storage, not correctness
            return name

    def _write_pointer(self, name: str) -> str | None:
        """Point at the new version, waiting a few seconds at most. Returns the pointer's etag when it was written."""
        from vercel.blob import sync as blob

        box: dict = {}

        def write() -> None:
            try:
                box["etag"] = _retry(
                    blob.put_blob, POINTER, name.encode(), access="private", content_type="text/plain", allow_overwrite=True
                ).etag
            except Exception as exc:  # noqa: BLE001 - the pointer is a hint; listing finds the version anyway
                box["error"] = type(exc).__name__

        worker = threading.Thread(target=write, name="ranksim-pointer", daemon=True)
        worker.start()
        worker.join(POINTER_WAIT_SECONDS)
        if "error" in box or worker.is_alive():
            print(f"ranksim: storage pointer not updated ({box.get('error', 'slow')}); other servers will find the save by listing.", flush=True)
        return box.get("etag")

    def pack(self) -> bytes:
        """A gzipped tar of projects/ plus a consistent copy of the SQLite database."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
            projects = self.data_dir / "projects"
            if projects.is_dir():
                for path in sorted(projects.rglob("*")):
                    if path.is_file() and not (set(path.relative_to(self.data_dir).parts) & SKIP_PARTS):
                        archive.add(path, arcname=str(path.relative_to(self.data_dir)), recursive=False)
            if self.db_file.is_file():
                copy = self.data_dir / f".rank-{secrets.token_hex(4)}.db"
                try:
                    _sqlite_copy(self.db_file, copy)
                    archive.add(copy, arcname="rank.db")
                finally:
                    copy.unlink(missing_ok=True)
        return buffer.getvalue()


def _sqlite_copy(source: Path, target: Path) -> None:
    """sqlite3's online backup: a consistent copy even while another connection is open."""
    src = sqlite3.connect(str(source))
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


def _retry(call, *args, tries: int = 3, **kwargs):
    """Blob calls fail now and then; the SDK does not retry. A missing blob is an answer, not a failure."""
    from vercel.blob import sync as blob

    for attempt in range(tries):
        try:
            return call(*args, **kwargs)
        except blob.BlobNotFoundError:
            raise
        except blob.BlobError:
            if attempt == tries - 1:
                raise
            time.sleep(0.5 * (attempt + 1) ** 2)
    return None
