"""Load pages the crawler was not allowed to fetch.

A manifest maps a URL to a file under smartcrew/saved_html/. Missing files are
reported and skipped. Nothing is synthesized in their place.
"""

from __future__ import annotations

import csv
from pathlib import Path


def saved_html_dir(root: Path | None = None) -> Path:
    """``smartcrew/saved_html`` under the repo root (``RANKSIM_ROOT`` overrides), not the cwd."""
    if root is not None:
        return root
    from aeo.ranksim.keywords import repo_root

    return repo_root() / "smartcrew" / "saved_html"


def load_saved_html(directory: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """Return (url to html, urls whose files are missing)."""
    folder = directory or saved_html_dir()
    manifest = folder / "manifest.csv"
    if not manifest.is_file():
        return {}, []
    found: dict[str, str] = {}
    missing: list[str] = []
    with manifest.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}, []
        fields = {name.strip().lower(): name for name in reader.fieldnames}
        url_key = fields.get("url")
        file_key = fields.get("file")
        if not url_key or not file_key:
            raise ValueError("saved_html/manifest.csv needs url and file columns")
        for raw in reader:
            url = (raw.get(url_key) or "").strip()
            name = (raw.get(file_key) or "").strip()
            if not url and not name:
                continue
            if not url or not name:
                raise ValueError("Each saved HTML row needs both url and file")
            path = _safe_file(folder, name)
            if not path.is_file():
                missing.append(url)
                continue
            found[url] = path.read_text(encoding="utf-8", errors="replace")
    return found, missing


def _safe_file(folder: Path, name: str) -> Path:
    if name.startswith("/") or ".." in Path(name).parts:
        raise ValueError(f"Saved HTML path must stay inside the directory: {name}")
    path = (folder / name).resolve()
    if folder.resolve() not in path.parents and path != folder.resolve():
        raise ValueError(f"Saved HTML path must stay inside the directory: {name}")
    return path
