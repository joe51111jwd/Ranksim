"""Freeze a predicted position for a page that is not published yet.

The ledger is the record. A later score reads it and does not recompute the prediction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta


def normalize_query(query: str) -> str:
    return " ".join(query.lower().replace("-", " ").split())


def missing_queries(targets: list[str], tracked: list[str]) -> list[str]:
    known = {normalize_query(query) for query in tracked}
    missing = []
    for query in targets:
        if normalize_query(query) not in known:
            missing.append(query)
    return missing


def text_sha256(title: str, h1: str, headings: str, body: str) -> str:
    payload = json.dumps(
        {"title": title, "h1": h1, "headings": headings, "body": body},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prediction_sha256(
    *,
    source_head: str,
    model_sha256: str,
    url: str,
    query: str,
    position: int,
    low: int,
    high: int,
    text_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "source_head": source_head,
            "model_sha256": model_sha256,
            "url": url,
            "query": query,
            "position": position,
            "low": low,
            "high": high,
            "text_sha256": text_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score_dates(publish: date) -> dict[str, str]:
    return {
        "plus_7": (publish + timedelta(days=7)).isoformat(),
        "plus_14": (publish + timedelta(days=14)).isoformat(),
        "plus_28": (publish + timedelta(days=28)).isoformat(),
    }


CORE_UPDATE_THROUGH = "2026-11-24"
CORE_UPDATE_NOTE = (
    "A Google core update is running through about 2026-11-24. "
    "Any score date on or before that day is confounded by the update. "
    "Score at publish plus 7, 14, and 28 days. Publish is unset because the page is not merged."
)
