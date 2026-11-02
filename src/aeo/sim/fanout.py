"""Query fan-out. The original question is always kept as a sub-query."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from aeo.errors import FanoutParseError
from aeo.textutil import normalize_space, salient_terms

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.I | re.S)
MAX_SUBQUERIES = 6


def mock_subqueries(question: str) -> list[str]:
    cleaned = normalize_space(question)
    terms = salient_terms(cleaned)
    key = " ".join(terms[:6]) if terms else cleaned
    raw = [
        cleaned,
        f"what is {key}",
        f"{key} explained",
        f"how to choose {key}",
        f"{key} comparison",
    ]
    return _dedupe(raw)[:MAX_SUBQUERIES]


def mock_fanout_json(question: str) -> str:
    return json.dumps(
        {
            "provider": "mock",
            "label": "deterministic_mock",
            "subqueries": mock_subqueries(question),
        }
    )


def extract_json_object(payload: str) -> dict:
    text = payload.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise FanoutParseError("Fan-out response was not JSON") from exc
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as inner:
            raise FanoutParseError("Fan-out response was not JSON") from inner
    if not isinstance(data, dict):
        raise FanoutParseError("Fan-out JSON must be an object")
    return data


def parse_subqueries(payload: str | dict, original: str, *, limit: int = MAX_SUBQUERIES) -> list[str]:
    data = payload if isinstance(payload, dict) else extract_json_object(payload)
    items = data.get("subqueries", data.get("queries"))
    if not isinstance(items, list):
        raise FanoutParseError("Fan-out JSON is missing a subqueries list")
    cleaned: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            cleaned.append(normalize_space(item))
    original_clean = normalize_space(original)
    if original_clean and original_clean not in cleaned:
        cleaned.insert(0, original_clean)
    deduped = _dedupe(cleaned)
    if not deduped:
        raise FanoutParseError("Fan-out produced no sub-queries")
    return deduped[:limit]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


@dataclass(frozen=True)
class FanoutResult:
    subqueries: list[str]
    provider: str
    synthetic: bool
    raw: str


def expand_question(question: str, llm) -> FanoutResult:
    raw = llm.fanout(question)
    subqueries = parse_subqueries(raw, question)
    return FanoutResult(
        subqueries=subqueries,
        provider=llm.name,
        synthetic=bool(getattr(llm, "synthetic", False)),
        raw=raw,
    )
