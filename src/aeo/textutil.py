"""Token helpers shared by ranking, fan-out, and on-page checks."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }
)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def tokenize_for_search(text: str) -> list[str]:
    return [token for token in tokenize(text) if token not in STOPWORDS and len(token) > 1]


def salient_terms(text: str) -> list[str]:
    seen: list[str] = []
    for token in tokenize_for_search(text):
        if len(token) < 3 or token in seen:
            continue
        seen.append(token)
    return seen


def word_count(text: str) -> int:
    return len(tokenize(text))


def normalize_space(text: str) -> str:
    return " ".join(text.split())
