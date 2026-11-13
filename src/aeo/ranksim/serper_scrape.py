"""Read a Serper scrape folder as page text. No API key and no network call.

A folder has manifest.csv (url, raw_file, and the other capture columns) and
raw JSON files with text and metadata. Usable text is longer than 500
characters. Shorter text is kept on the record and is not page content.
The capture date is the day the scrape was saved, which can be later than
the SERP day.
"""

from __future__ import annotations

import csv
import html
import json
from dataclasses import dataclass
from pathlib import Path

SOURCE = "serper_scrape"
USABLE_MIN_CHARS = 500
DEFAULT_CAPTURED_ON = "2026-11-10"


@dataclass(frozen=True)
class ScrapedPage:
    url: str
    text: str
    title: str
    description: str
    source: str
    captured_on: str
    chars: int
    usable: bool
    error: str = ""
    status: str = ""

    def as_html(self) -> str:
        return html_from_scrape(self)


def _inside(root: Path, name: str) -> Path:
    if not name or name.startswith("/") or ".." in Path(name).parts:
        raise ValueError(f"Scrape path must stay inside the directory: {name}")
    path = (root / name).resolve()
    if root.resolve() not in path.parents and path != root.resolve():
        raise ValueError(f"Scrape path must stay inside the directory: {name}")
    return path


def _meta(payload: dict) -> tuple[str, str]:
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    title = ""
    for key in ("title", "og:title", "application-title", "twitter:title"):
        value = str(meta.get(key) or "").strip()
        if value:
            title = value
            break
    description = ""
    for key in ("description", "og:description", "twitter:description"):
        value = str(meta.get(key) or "").strip()
        if value:
            description = value
            break
    return title, description


def load_scrape_dir(root: str | Path, *, captured_on: str = DEFAULT_CAPTURED_ON) -> list[ScrapedPage]:
    """One row per manifest URL. Does not read an API key and does not call Serper."""
    folder = Path(root)
    manifest = folder / "manifest.csv"
    pages: list[ScrapedPage] = []
    with manifest.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            url = (row.get("url") or "").strip()
            if not url:
                continue
            error = (row.get("error") or "").strip()
            status = (row.get("status") or "").strip()
            raw_name = (row.get("raw_file") or "").strip()
            text = ""
            title = ""
            description = ""
            if raw_name:
                path = _inside(folder, raw_name)
                if path.is_file():
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        text = str(payload.get("text") or "")
                        title, description = _meta(payload)
                        if not error and payload.get("message") and not text:
                            error = str(payload.get("message"))
            chars = len(text)
            pages.append(
                ScrapedPage(
                    url=url,
                    text=text,
                    title=title,
                    description=description,
                    source=SOURCE,
                    captured_on=captured_on,
                    chars=chars,
                    usable=chars > USABLE_MIN_CHARS,
                    error=error,
                    status=status,
                )
            )
    return pages


def html_from_scrape(page: ScrapedPage) -> str:
    """Minimal HTML so the existing extractor can read title, description, and text."""
    title = html.escape(page.title or "")
    description = html.escape(page.description or "")
    body = page.text or ""
    if page.description and page.description not in body:
        body = page.description + "\n" + body
    paragraphs = []
    for block in body.split("\n"):
        block = block.strip()
        if block:
            paragraphs.append(f"<p>{html.escape(block)}</p>")
    return (
        "<!doctype html><html><head><title>"
        + title
        + '</title><meta name="description" content="'
        + description
        + '"></head><body><h1>'
        + title
        + "</h1><main>"
        + "".join(paragraphs)
        + "</main></body></html>"
    )


def scrape_content_map(pages: list[ScrapedPage]) -> dict[str, tuple[str, str, str]]:
    """Usable pages only: url to (html, source, captured_on). Short text is left out."""
    found: dict[str, tuple[str, str, str]] = {}
    for page in pages:
        if not page.usable:
            continue
        found[page.url] = (page.as_html(), page.source, page.captured_on)
    return found
