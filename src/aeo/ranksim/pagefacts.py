"""A cache of site.page_facts, so a report GET does not re-parse every stored page.

page_facts is a pure function of (url, html, status, error, fetch_ms). Each answer is cached under the sha256 of
those inputs, so a page that is fetched again (new HTML, new status) gets a new key and is parsed again: nothing
has to be invalidated by hand.

Two layers:
- in memory, per process, shared by every caller (bounded, oldest dropped first);
- on disk per project, <project>/plugins/pagefacts/facts.json, so a new serverless instance does not parse
  either. It holds the facts only (no raw HTML) and only the keys the last report used, so it stays small and
  is saved with the project like any other project file. It is rewritten only when its keys change.

A missing, unreadable or corrupt file is ignored and rebuilt; the cache never changes what the report shows.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from dataclasses import asdict, fields
from pathlib import Path

from aeo.ranksim.site import SitePage, page_facts

VERSION = 1
FEATURE = "pagefacts"
FILE = "facts.json"
MEMORY_LIMIT = 4000

_memory: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()
_FIELDS = {item.name for item in fields(SitePage)}


def fact_key(url: str, html: str, status: str = "fetched", error: str = "", fetch_ms: float | None = None) -> str:
    digest = hashlib.sha256()
    for part in (str(VERSION), url or "", status or "", error or "", repr(fetch_ms)):
        digest.update(part.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    digest.update((html or "").encode("utf-8", "surrogatepass"))
    return digest.hexdigest()


def _page(data: dict) -> SitePage:
    values = {key: value for key, value in data.items() if key in _FIELDS}
    values["h2s"] = list(values.get("h2s") or [])
    values["links"] = list(values.get("links") or [])
    return SitePage(**values)  # a fresh object each time, so a caller that edits one cannot change the cache


def _remember(key: str, data: dict) -> None:
    with _lock:
        _memory[key] = data
        _memory.move_to_end(key)
        while len(_memory) > MEMORY_LIMIT:
            _memory.popitem(last=False)


def cached_page_facts(url: str, html: str, status: str = "fetched", error: str = "", fetch_ms: float | None = None, *, cache: "ProjectFacts | None" = None) -> SitePage:
    """page_facts(url, html, status, error, fetch_ms), from the cache when the same inputs were parsed before."""
    key = fact_key(url, html, status, error, fetch_ms)
    if cache is not None:
        cache.used.add(key)
        data = cache.disk.get(key)
        if data is not None:
            _remember(key, data)
            return _page(data)
    with _lock:
        data = _memory.get(key)
    if data is None:
        data = asdict(page_facts(url, html, status, error, fetch_ms))
        _remember(key, data)
    if cache is not None:
        cache.new[key] = data
    return _page(data)


class ProjectFacts:
    """The on-disk layer for one project. Read on creation; save() writes back only the keys used since."""

    def __init__(self, project) -> None:
        self.project = project
        self.path = Path(project.dir) / "plugins" / FEATURE / FILE
        self.disk: dict[str, dict] = {}
        self.used: set[str] = set()
        self.new: dict[str, dict] = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == VERSION and isinstance(raw.get("facts"), dict):
                self.disk = {key: value for key, value in raw["facts"].items() if isinstance(value, dict)}
        except (OSError, ValueError):
            self.disk = {}

    def save(self) -> None:
        if not self.used:
            return
        if self.used == set(self.disk) and not set(self.new) - set(self.disk):
            return  # nothing changed: no write, so a steady report GET never touches the project folder
        merged = {**self.disk, **self.new}
        keep = {key: merged[key] for key in sorted(self.used) if key in merged}
        try:
            from aeo.ranksim.plugins import data_dir

            folder = data_dir(self.project, FEATURE)
            temp = folder / f".{FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
            temp.write_text(json.dumps({"version": VERSION, "facts": keep}, separators=(",", ":")), encoding="utf-8")
            os.replace(temp, folder / FILE)
            self.disk = keep
            self.new = {}
        except (OSError, ValueError, TypeError):
            pass  # a read-only or full disk only costs speed


def clear_memory() -> None:
    with _lock:
        _memory.clear()
