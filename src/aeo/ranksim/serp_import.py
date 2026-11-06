"""Turn a local SERP capture into query,url,position rows.

Browser JSONL rows often look like {"q", "organic", "ts"}. An organic item is
frequently a 3-part list of breadcrumb, title, and snippet, and many of those
have no http URL. This module writes only URLs that are already http or https.
It does not invent a host, and it does not fetch anything.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

_HTTP_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class SerpRow:
    query: str
    url: str
    position: int


@dataclass
class ImportResult:
    rows: list[SerpRow]
    skipped_no_url: int
    source: str


def import_capture(path: str | Path) -> ImportResult:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    kind = _kind(file_path, text)
    if kind == "csv":
        rows, skipped = _from_csv(text)
        return ImportResult(rows, skipped, "csv")
    if kind == "jsonl":
        rows, skipped = _from_jsonl(text)
        return ImportResult(rows, skipped, "jsonl")
    rows, skipped = _from_json(text)
    return ImportResult(rows, skipped, "json")


def write_serp_csv(path: str | Path, rows: list[SerpRow]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query", "url", "position"])
        writer.writeheader()
        for row in rows:
            writer.writerow({"query": row.query, "url": row.url, "position": row.position})


def _kind(path: Path, text: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".jsonl":
        return "jsonl"
    stripped = text.lstrip()
    if suffix == ".json":
        return "json"
    if stripped.startswith("{") or stripped.startswith("["):
        # A single JSON value, or JSONL that happens to start with an object.
        # Prefer JSONL when more than one non-empty line parses as JSON.
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 1 and all(_is_json_line(line) for line in lines[:5]):
            return "jsonl"
        return "json"
    if "," in text.splitlines()[0]:
        return "csv"
    return "jsonl"


def _is_json_line(line: str) -> bool:
    try:
        json.loads(line)
    except json.JSONDecodeError:
        return False
    return True


def _from_csv(text: str) -> tuple[list[SerpRow], int]:
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")
    fields = {name.strip().lower(): name for name in reader.fieldnames}
    query_key = fields.get("query") or fields.get("q")
    url_key = fields.get("url") or fields.get("link") or fields.get("page")
    position_key = fields.get("position")
    if not query_key or not url_key or not position_key:
        raise ValueError("CSV needs query, url, and position columns")
    rows: list[SerpRow] = []
    skipped = 0
    for raw in reader:
        query = " ".join((raw.get(query_key) or "").split())
        url = _http_url(raw.get(url_key) or "")
        if not query and not (raw.get(url_key) or "").strip():
            continue
        if not query or url is None:
            skipped += 1
            continue
        try:
            position = int(float((raw.get(position_key) or "").strip()))
        except ValueError:
            skipped += 1
            continue
        if position <= 0:
            skipped += 1
            continue
        rows.append(SerpRow(query, url, position))
    return rows, skipped


def _from_jsonl(text: str) -> tuple[list[SerpRow], int]:
    rows: list[SerpRow] = []
    skipped = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL line {line_number} is not JSON") from exc
        found, missed = _rows_from_payload(payload)
        rows.extend(found)
        skipped += missed
    return rows, skipped


def _from_json(text: str) -> tuple[list[SerpRow], int]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("File is not JSON or JSONL") from exc
    if isinstance(payload, list) and payload and all(_is_clean_row(item) for item in payload):
        rows = []
        for item in payload:
            url = _http_url(str(item.get("url") or item.get("link") or ""))
            query = " ".join(str(item.get("query") or item.get("q") or "").split())
            if not query or url is None:
                continue
            position = int(item["position"])
            if position <= 0:
                continue
            rows.append(SerpRow(query, url, position))
        return rows, 0
    if isinstance(payload, list):
        rows = []
        skipped = 0
        for item in payload:
            found, missed = _rows_from_payload(item)
            rows.extend(found)
            skipped += missed
        return rows, skipped
    return _rows_from_payload(payload)


def _is_clean_row(item: object) -> bool:
    return isinstance(item, dict) and ("url" in item or "link" in item) and "position" in item and ("query" in item or "q" in item)


def _rows_from_payload(payload: object) -> tuple[list[SerpRow], int]:
    if not isinstance(payload, dict):
        raise ValueError("Each capture row must be a JSON object")
    query = " ".join(str(payload.get("q") or payload.get("query") or "").split())
    if not query:
        raise ValueError("A capture row needs q or query")
    organic = payload.get("organic")
    if organic is None and _is_clean_row(payload):
        url = _http_url(str(payload.get("url") or payload.get("link") or ""))
        if url is None:
            return [], 1
        return [SerpRow(query, url, int(payload["position"]))], 0
    if not isinstance(organic, list):
        raise ValueError(f"Query {query!r} has no organic list")
    rows: list[SerpRow] = []
    skipped = 0
    for index, item in enumerate(organic, start=1):
        url = _url_from_organic(item)
        if url is None:
            skipped += 1
            continue
        rows.append(SerpRow(query, url, index))
    return rows, skipped


def _url_from_organic(item: object) -> str | None:
    if isinstance(item, dict):
        for key in ("url", "link", "page"):
            if key in item:
                found = _http_url(str(item.get(key) or ""))
                if found:
                    return found
        for value in item.values():
            if isinstance(value, str):
                found = _http_url(value)
                if found:
                    return found
        return None
    if isinstance(item, (list, tuple)):
        for part in item:
            found = _http_url(str(part))
            if found:
                return found
        return None
    if isinstance(item, str):
        return _http_url(item)
    return None


def _http_url(value: str) -> str | None:
    match = _HTTP_URL.search(value.strip())
    if not match:
        return None
    url = match.group(0).rstrip(").,;]")
    lowered = url.lower()
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return url
    return None
