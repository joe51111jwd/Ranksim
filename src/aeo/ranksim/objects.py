"""A tiny object store interface shared by cloud storage, the job registry and login rate limits.

Three backends:

- BlobStore: a private Vercel Blob store (hosted). Writes go to new immutable names wherever correctness matters;
  overwritable names are used only for hints (pointers). Reads of immutable names can never be stale.
- DirStore: a local folder. Used for local runs so jobs and rate limits behave the same way off Vercel.
- MemoryStore: in memory, for tests and for a single process with nothing to persist.

Names are plain strings with "/" separators, like "ranksim/v2/jobs/<id>/00000001.json".
"""

from __future__ import annotations

import contextlib
import io
import os
import secrets
import threading
import time
from pathlib import Path


class StoreError(RuntimeError):
    """The store could not be reached or refused the call. Missing objects raise KeyError instead."""


class ObjectExists(StoreError):
    """A create-only put (overwrite=False) found the name taken by different content. Writers use this as a
    compare-and-swap: whoever creates the next name in a sequence first wins, the other one merges and tries again."""


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

    @contextlib.contextmanager
    def open(self, name: str):
        """name's bytes as a readable binary stream, read once front to back (KeyError when name does not exist).
        Same cost as get. Backends that can stream do, so the object is neither held whole nor written to disk."""
        yield io.BytesIO(self.get(name))

    def fetch(self, name: str, path: Path) -> None:
        """Write name's bytes to the file path (KeyError when name does not exist). Same cost as get. Backends that
        can stream do, so a large object never sits in memory whole; a failed fetch leaves no file behind."""
        try:
            data = self.get(name)
            with open(path, "wb") as handle:
                handle.write(data)
        except BaseException:
            Path(path).unlink(missing_ok=True)
            raise

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
                if self.items[name] == bytes(body):
                    return self.etags[name]  # the same write again (a retry): it already landed
                raise ObjectExists(f"{name} exists")
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
                if path.read_bytes() == bytes(body):
                    return f"{path.stat().st_mtime_ns}-{path.stat().st_size}"
                raise ObjectExists(f"{name} exists") from exc
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

        if overwrite:
            try:
                result = _retry(blob.put_blob, name, bytes(body), access="private", content_type=content_type, allow_overwrite=True)
            except blob.BlobError as exc:
                raise StoreError(f"put failed: {type(exc).__name__}") from exc
            return getattr(result, "etag", None)
        # Create-only. Vercel Blob refuses a put to an existing pathname unless allowOverwrite is set, but the error's
        # type is not documented, so after any failure the name is checked: taken by other bytes means ObjectExists;
        # taken by our own bytes means an earlier try landed; free means a real failure (tried again once).
        for attempt in range(2):
            try:
                result = blob.put_blob(name, bytes(body), access="private", content_type=content_type, allow_overwrite=False)
                return getattr(result, "etag", None)
            except blob.BlobError as exc:
                failure = exc
            try:
                if self.head(name) is not None:
                    if self.get(name) == bytes(body):
                        return self.head(name)
                    raise ObjectExists(f"{name} exists") from failure
            except KeyError:
                pass
            if attempt == 0:
                time.sleep(0.5)
        raise StoreError(f"put failed: {type(failure).__name__}") from failure


    @contextlib.contextmanager
    def open(self, name):
        from vercel.blob import sync as blob

        stream = getattr(blob, "stream_blob", None)
        if stream is None:
            with super().open(name) as handle:
                yield handle
            return
        with contextlib.ExitStack() as stack:
            try:
                download = _retry(lambda: stack.enter_context(stream(name, access="private")))
            except blob.BlobNotFoundError as exc:
                raise KeyError(name) from exc
            except blob.BlobError as exc:
                raise StoreError(f"get failed: {type(exc).__name__}") from exc
            yield io.BufferedReader(_ChunkReader(iter(download), blob.BlobError), 1 << 20)

    def fetch(self, name, path):
        from vercel.blob import sync as blob

        stream = getattr(blob, "stream_blob", None)
        if stream is None:
            return super().fetch(name, path)

        def once():
            with stream(name, access="private") as download, open(path, "wb") as handle:
                for chunk in download:
                    handle.write(chunk)

        try:
            _retry(once)
        except blob.BlobNotFoundError as exc:
            Path(path).unlink(missing_ok=True)
            raise KeyError(name) from exc
        except blob.BlobError as exc:
            Path(path).unlink(missing_ok=True)
            raise StoreError(f"get failed: {type(exc).__name__}") from exc
        except OSError:
            Path(path).unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - a dropped stream from the HTTP layer
            Path(path).unlink(missing_ok=True)
            raise StoreError(f"get failed: {type(exc).__name__}") from exc
        except BaseException:
            Path(path).unlink(missing_ok=True)
            raise

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


class _ChunkReader(io.RawIOBase):
    """A streamed download's chunks as a readable file. A dropped stream surfaces as StoreError."""

    def __init__(self, chunks, failure) -> None:
        self._chunks, self._failure, self._left = chunks, failure, b""

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while not self._left:
            try:
                self._left = next(self._chunks)
            except StopIteration:
                return 0
            except self._failure as exc:
                raise StoreError(f"get failed: {type(exc).__name__}") from exc
            except OSError:
                raise
            except Exception as exc:  # noqa: BLE001 - the HTTP layer's own errors
                raise StoreError(f"get failed: {type(exc).__name__}") from exc
        size = min(len(buffer), len(self._left))
        buffer[:size] = self._left[:size]
        self._left = self._left[size:]
        return size


def _retry(call, *args, tries: int = 3, **kwargs):
    """Blob calls fail now and then and the SDK does not retry. A missing blob is an answer, not a failure."""
    from vercel.blob import sync as blob

    for attempt in range(tries):
        try:
            return call(*args, **kwargs)
        except blob.BlobNotFoundError:
            raise
        except blob.BlobError as exc:
            if attempt == tries - 1 or _exists_error(exc):
                raise
            time.sleep(0.5 * (attempt + 1) ** 2)
    return None


def _exists_error(exc: BaseException) -> bool:
    """Vercel Blob refuses a put to an existing pathname unless allowOverwrite is set."""
    text = str(exc).lower()
    return "already exists" in text or getattr(exc, "status_code", None) == 409
