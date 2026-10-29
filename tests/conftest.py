from __future__ import annotations

from pathlib import Path

from aeo.config import Settings


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite:///{tmp_path / 'aeo.db'}",
        "search_provider": "mock",
        "llm_provider": "mock",
        "embedding_provider": "mock",
        "process_jobs_inline": True,
        "crawl_delay_seconds": 0,
        "crawl_allow_private": False,
        "embedding_dim": 64,
        "brave_api_key": None,
        "openai_api_key": None,
    }
    values.update(overrides)
    return Settings(**values)
