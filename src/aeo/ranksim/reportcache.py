"""A project's report, cached on disk until anything it is built from changes.

Building a report parses stored pages, runs the audit and the competitor profiles, and loads the engine (LightGBM).
That is seconds on a real project, and every new serverless instance would pay it again, because those caches live in
process memory. This cache keeps the finished report as JSON in the project folder
(<project>/plugins/reportcache/report.json), so it travels with the project in cloud storage, and a job precomputes
it at the end of every pull and analysis (api/sites.py), so a GET normally never builds.

The key is a content signature, the same on every instance: sha256 of every file in the project folder and in the
workspace plugin folder (<projects root>/_plugins, e.g. Search Console data), cheap row counts and newest stamps of
the shared rank database tables, whether Google results are connected, which plugins are loaded, and the UTC date (so
nothing in a report can be more than a day older than its data). Anything else changing makes a new key and a fresh
build. The cache's own folder and the page-facts cache are left out of the signature.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
FEATURE = "reportcache"
SKIP_PARTS = {"llm-cache", "__pycache__"}
SKIP_PLUGIN_DIRS = {"reportcache", "pagefacts"}
TABLES = ("rs_pages", "rs_serp", "rs_keywords", "rs_rank_checks", "rs_test_runs", "rs_predictions", "rs_outcomes", "rs_weights")

_hashes: dict[tuple, str] = {}
_lock = threading.Lock()


def _file_hash(path: Path) -> str | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ino)
    with _lock:
        found = _hashes.get(key)
    if found is None:
        try:
            found = hashlib.sha256(path.read_bytes()).hexdigest()
        except (FileNotFoundError, IsADirectoryError):
            return None
        with _lock:
            if len(_hashes) > 50_000:
                _hashes.clear()
            _hashes[key] = found
    return found


def _folder(digest, root: Path, label: str) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root)
        parts = rel.parts
        if set(parts) & SKIP_PARTS or any(part.startswith(".") for part in parts) or rel.name.endswith(".tmp"):
            continue
        if len(parts) > 1 and parts[0] == "plugins" and parts[1] in SKIP_PLUGIN_DIRS:
            continue
        found = _file_hash(path)
        if found is not None:
            digest.update(f"{label}/{rel.as_posix()}={found}\n".encode())


def _database(digest, database: str) -> bool:
    """Counts and newest stamps of the rank database tables. False when the database is not a SQLite file."""
    if not database.startswith("sqlite:///"):
        return False
    path = database.removeprefix("sqlite:///")
    if not path or path == ":memory:" or not Path(path).is_file():
        digest.update(b"db=none\n")
        return True
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        have = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in have:
                continue
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
            stamp = next((c for c in ("fetched_at", "created_at", "recorded_at") if c in columns), None)
            extra = f', max("{stamp}")' if stamp else ""
            row = conn.execute(f'SELECT count(*), max(rowid){extra} FROM "{table}"').fetchone()
            digest.update(f"{table}={row}\n".encode())
    finally:
        conn.close()
    return True


def signature(ui, project, *, google_ready: bool) -> str | None:
    """The report's cache key, or None when it cannot be computed (then the report is simply built)."""
    try:
        digest = hashlib.sha256(f"v{VERSION}|{datetime.now(timezone.utc).date().isoformat()}|google={google_ready}".encode())
        digest.update(f"|public={bool(getattr(ui, 'public_demo', False))}|slug={project.slug}\n".encode())
        from aeo.ranksim import plugins

        digest.update(("plugins=" + ",".join(plugins.names()) + "\n").encode())
        _folder(digest, Path(project.dir), "p")
        _folder(digest, Path(ui.root) / "_plugins", "w")
        if not _database(digest, str(ui.database or "")):
            return None
        return digest.hexdigest()
    except (OSError, sqlite3.Error, ValueError):
        return None


def _path(project) -> Path:
    return Path(project.dir) / "plugins" / FEATURE / "report.json"


def load(project, key: str | None) -> dict | None:
    if not key:
        return None
    try:
        data = json.loads(_path(project).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != key or not isinstance(data.get("report"), dict):
        return None
    return data["report"]


def store(project, key: str | None, report: dict) -> None:
    if not key:
        return
    path = _path(project)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".report-{secrets.token_hex(4)}.json")
        temp.write_text(json.dumps({"key": key, "report": report}, separators=(",", ":")), "utf-8")
        os.replace(temp, path)
    except (OSError, TypeError, ValueError):
        pass  # a cache that cannot be written is only slower
