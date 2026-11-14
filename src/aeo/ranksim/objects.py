"""A tiny object store interface shared by cloud storage, the job registry and login rate limits.

Three backends:

- BlobStore: a private Vercel Blob store (hosted). Writes go to new immutable names wherever correctness matters;
  overwritable names are used only for hints (pointers). Reads of immutable names can never be stale.
- DirStore: a local folder. Used for local runs so jobs and rate limits behave the same way off Vercel.
- MemoryStore: in memory, for tests and for a single process with nothing to persist.

Names are plain strings with "/" separators, like "ranksim/v2/jobs/<id>/00000001.json".
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path


class StoreError(RuntimeError):
    """The store could not be reached or refused the call. Missing objects raise KeyError instead."""


class ObjectStore:
    def put(self, name: str, body: bytes, *, overwrite: bool = False, content_type: str = "application/octet-stream") -> str | None:
        """Store body under name. Returns an etag when the backend has one. Without overwrite an existing name fails."""
        raise NotImplementedError

    def get(self, name: str) -> bytes:
        raise NotImplementedError

    def head(self, name: str) -> str | None:
        """The etag (or another change marker) of name, or None when it does not exist."""
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:
        """Every name under prefix, sorted."""
        raise NotImplementedError

    def delete(self, names: list[str]) -> None:
        raise NotImplementedError


def new_name(prefix: str, suffix: str = "") -> str:
    """A fresh immutable name that sorts by creation time: <prefix><20-digit ns time>-<random><suffix>."""
    return f"{prefix}{time.time_ns():020d}-{secrets.token_hex(3)}{suffix}"


def stamp_of(name: str) -> int:
    """The nanosecond time encoded by new_name, or 0 when name has none."""
    tail = name.rsplit("/", 1)[-1]
    head = tail.split("-", 1)[0]
    return int(head) if head.isdigit() else 0


class MemoryStore(ObjectStore):
    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self._n = 0
        self._lock = threading.Lock()

    def put(self, name, body, *, overwrite=False, content_type="application/octet-stream"):
        with self._lock:
            if name in self.items and not overwrite:
                raise StoreError(f"{name} exists")
            self._n += 1
            self.items[name], self.etags[name] = bytes(body), f"m{self._n}"
            return self.etags[name]

    def get(self, name):
        with self._lock:
            if name not in self.items:
                raise KeyError(name)
            return self.items[name]

    def head(self, name):
        with self._lock:
            return self.etags.get(name)

    def list(self, prefix):
        with self._lock:
            return sorted(name for name in self.items if name.startswith(prefix))

    def delete(self, names):
        with self._lock:
            for name in names:
                self.items.pop(name, None)
                self.etags.pop(name, None)


class DirStore(ObjectStore):
    """Objects as files under root. Writes are atomic (temp file then rename)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        parts = [part for part in name.split("/") if part]
        if not parts or any(part in {".", ".."} or "\\" in part for part in parts):
            raise StoreError(f"Bad object name {name!r}")
        return self.root.joinpath(*parts)

    def put(self, name, body, *, overwrite=False, content_type="application/octet-stream"):
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        temp.write_bytes(bytes(body))
        if overwrite:
            os.replace(temp, path)
        else:
            try:
                os.link(temp, path)  # fails when the name exists: create-only, like the Blob store
            except FileExistsError as exc:
                raise StoreError(f"{name} exists") from exc
            finally:
                temp.unlink(missing_ok=True)
        stat = path.stat()
        return f"{stat.st_mtime_ns}-{stat.st_size}"

    def get(self, name):
        try:
            return self._path(name).read_bytes()
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise KeyError(name) from exc

    def head(self, name):
        try:
            stat = self._path(name).stat()
        except FileNotFoundError:
            return None
        return f"{stat.st_mtime_ns}-{stat.st_size}"

    def list(self, prefix):
        found = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith("."):
                name = path.relative_to(self.root).as_posix()
                if name.startswith(prefix):
                    found.append(name)
        return sorted(found)

    def delete(self, names):
        for name in names:
            try:
                self._path(name).unlink(missing_ok=True)
            except StoreError:
                continue


class BlobStore(ObjectStore):
    """A private Vercel Blob store through vercel.blob.sync (BLOB_READ_WRITE_TOKEN in the environment)."""

    def put(self, name, body, *, overwrite=False, content_type="application/octet-stream"):
        from vercel.blob import sync as blob

        try:
            result = _retry(blob.put_blob, name, bytes(body), access="private", content_type=content_type, allow_overwrite=overwrite)
        except blob.BlobError as exc:
            raise StoreError(f"put failed: {type(exc).__name__}") from exc
        return getattr(result, "etag", None)

    def get(self, name):
        from vercel.blob import sync as blob

        try:
            return bytes(_retry(blob.get_blob, name, access="private").body)
        except blob.BlobNotFoundError as exc:
            raise KeyError(name) from exc
        except blob.BlobError as exc:
            raise StoreError(f"get failed: {type(exc).__name__}") from exc

    def head(self, name):
        from vercel.blob import sync as blob

        try:
            return _retry(blob.head_blob, name).etag
        except blob.BlobNotFoundError:
            return None
        except blob.BlobError as exc:
            raise StoreError(f"head failed: {type(exc).__name__}") from exc

    def list(self, prefix):
        from vercel.blob import sync as blob

        try:
            return sorted(item.pathname for item in _retry(lambda: list(blob.query_blobs(prefix=prefix))))
        except blob.BlobError as exc:
            raise StoreError(f"list failed: {type(exc).__name__}") from exc

    def delete(self, names):
        from vercel.blob import sync as blob

        if not names:
            return
        try:
            _retry(blob.delete_blob, list(names))
        except blob.BlobError as exc:
            raise StoreError(f"delete failed: {type(exc).__name__}") from exc


def _retry(call, *args, tries: int = 3, **kwargs):
    """Blob calls fail now and then and the SDK does not retry. A missing blob is an answer, not a failure."""
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
