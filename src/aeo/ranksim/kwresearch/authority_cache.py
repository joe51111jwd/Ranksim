"""Free Tranco and Majestic lists for the difficulty score.

When RANKSIM_TRANCO_PATH and RANKSIM_MAJESTIC_PATH are unset, the lists are
downloaded into a cache directory and reused until they are older than 30 days.
A failed download does not invent ranks. If no cached file exists, that list is
omitted and the authority factor can drop out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from aeo.ranksim.kwresearch.difficulty import load_majestic_ranks, load_tranco_ranks
from aeo.ranksim.kwresearch.http import USER_AGENT
from aeo.ranksim.kwresearch.models import now_iso

TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
MAJESTIC_URL = "https://downloads.majestic.com/majestic_million.csv"
REFRESH_DAYS = 30
TRANCO_NAME = "tranco-top-1m.csv.zip"
MAJESTIC_NAME = "majestic_million.csv"
MANIFEST_NAME = "manifest.json"


@dataclass
class RankSource:
    ranks: dict[str, int] | None
    list_date: str
    note: str
    source_url: str


def default_cache_dir() -> Path:
    import os

    override = os.environ.get("RANKSIM_AUTHORITY_CACHE", "").strip()
    if override:
        return Path(override)
    return Path("data/keyword-authority")


def load_authority_sources(
    *,
    cache_dir: str | Path | None = None,
    tranco_path: str | None = None,
    majestic_path: str | None = None,
    fetch: bool = True,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> tuple[RankSource, RankSource]:
    """Load Tranco and Majestic. Explicit paths skip the download for that list."""
    moment = now or datetime.now(timezone.utc)
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    owns = client is None and fetch and (not tranco_path or not majestic_path)
    http = client
    if owns:
        http = httpx.Client(timeout=120.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        tranco = _one(
            name="tranco",
            explicit=tranco_path,
            url=TRANCO_URL,
            filename=TRANCO_NAME,
            loader=load_tranco_ranks,
            cache=cache,
            fetch=fetch,
            client=http,
            now=moment,
        )
        majestic = _one(
            name="majestic",
            explicit=majestic_path,
            url=MAJESTIC_URL,
            filename=MAJESTIC_NAME,
            loader=load_majestic_ranks,
            cache=cache,
            fetch=fetch,
            client=http,
            now=moment,
        )
        return tranco, majestic
    finally:
        if owns and http is not None:
            http.close()


def _one(
    *,
    name: str,
    explicit: str | None,
    url: str,
    filename: str,
    loader,
    cache: Path,
    fetch: bool,
    client: httpx.Client | None,
    now: datetime,
) -> RankSource:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            return RankSource(None, "", f"{name} file is missing", "")
        try:
            return RankSource(loader(path), "", "", "")
        except (OSError, ValueError, zipfile_error()) as exc:
            return RankSource(None, "", f"{name} file could not be read ({type(exc).__name__})", "")
    if not fetch:
        return RankSource(None, "", "", url)
    manifest = _read_manifest(cache)
    entry = manifest.get(name) if isinstance(manifest.get(name), dict) else {}
    cached = cache / filename
    fresh = cached.is_file() and _is_fresh(str(entry.get("fetched_at") or ""), now)
    if fresh:
        loaded = _load_file(loader, cached)
        if loaded is not None:
            return RankSource(loaded, str(entry.get("list_date") or ""), "", url)
    list_date = ""
    error = ""
    if client is not None:
        try:
            list_date = _download(client, url, cached)
        except (httpx.HTTPError, OSError) as exc:
            error = f"download failed ({type(exc).__name__})"
    else:
        error = "download failed (no HTTP client)"
    if cached.is_file():
        loaded = _load_file(loader, cached)
        if loaded is not None:
            kept_date = list_date or str(entry.get("list_date") or "")
            note = ""
            if error:
                note = "refresh failed, using the cached list"
            elif not list_date:
                note = "the file did not publish a list date"
            if not error:
                _write_entry(cache, name, list_date=kept_date, source_url=url)
            return RankSource(loaded, kept_date, note, url)
    return RankSource(None, "", error or "download failed", url)


def _download(client: httpx.Client, url: str, dest: Path) -> str:
    response = client.get(url, headers={"User-Agent": USER_AGENT})
    if response.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {response.status_code}",
            request=response.request,
            response=response,
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    temporary.write_bytes(response.content)
    temporary.replace(dest)
    return _header_date(response.headers.get("last-modified"))


def _load_file(loader, path: Path):
    try:
        return loader(path)
    except (OSError, ValueError, zipfile_error()):
        return None


def zipfile_error():
    import zipfile

    return (zipfile.BadZipFile, KeyError)


def _is_fresh(fetched_at: str, now: datetime) -> bool:
    parsed = _parse_time(fetched_at)
    if parsed is None:
        return False
    return now - parsed <= timedelta(days=REFRESH_DAYS)


def _parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _header_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


def _read_manifest(cache: Path) -> dict:
    path = cache / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_entry(cache: Path, name: str, *, list_date: str, source_url: str) -> None:
    manifest = _read_manifest(cache)
    manifest[name] = {
        "fetched_at": now_iso(),
        "list_date": list_date,
        "source_url": source_url,
    }
    cache.mkdir(parents=True, exist_ok=True)
    (cache / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
