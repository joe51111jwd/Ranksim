"""Hash chains for append-only records.

A row's hash covers its own content and the hash of the row before it, so a
stored forecast or grade cannot be edited or removed without breaking every
later hash. This says nothing about whether a forecast is right. It only makes
a later edit visible.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

GENESIS = "0" * 64


def canonical_json(payload: Any) -> str:
    """One stable text form of a JSON value: sorted keys, no spaces, no NaN."""
    _reject_nan(payload)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _reject_nan(value: Any) -> None:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError("NaN and infinity cannot be hashed. Store None for a missing value.")
    if isinstance(value, dict):
        for item in value.values():
            _reject_nan(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nan(item)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """The hash of one row: sha256 of the previous hash, a newline, and the row's canonical JSON."""
    return sha256_text(prev_hash + "\n" + canonical_json(payload))


def verify_chain(rows: Iterable[tuple[str, str, dict[str, Any]]]) -> tuple[bool, str, int]:
    """Check rows given in order as (prev_hash, row_hash, payload).

    Returns (ok, head_hash, rows_checked). On the first broken row it stops and
    returns ok False with the last good head.
    """
    head = GENESIS
    count = 0
    for prev, this, payload in rows:
        if prev != head or row_hash(prev, payload) != this:
            return False, head, count
        head = this
        count += 1
    return True, head, count
