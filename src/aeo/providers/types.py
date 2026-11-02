"""Shared provider records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    provider: str
    synthetic: bool
    inline_html: str | None = None


@dataclass(frozen=True)
class ResolvedProviders:
    search_name: str
    llm_name: str
    embedding_name: str
    synthetic: bool
    warnings: list[str]
