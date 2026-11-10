from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from aeo.config import Settings

# Cleared for every test. A test may set a dummy value again for one case.
PROVIDER_ENV_VARS = (
    "BRAVE_API_KEY",
    "SERPAPI_KEY",
    "SERPER_API_KEY",
    "OPEN_PAGERANK_API_KEY",
    "SERPER_SCRAPE_CREDIT_CAP",
    "DATAFORSEO_LOGIN",
    "DATAFORSEO_PASSWORD",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "PERPLEXITY_API_KEY",
    "GEMINI_API_KEY",
    "RANKSIM_LLM_MODEL",
    "RANKSIM_AUTHORITY",
)

PAID_HOSTS = (
    "openrouter.ai",
    "api.openai.com",
    "api.anthropic.com",
    "google.serper.dev",
    "scrape.serper.dev",
    "openpagerank.com",
    "serpapi.com",
    "api.dataforseo.com",
    "api.search.brave.com",
    "api.perplexity.ai",
    "generativelanguage.googleapis.com",
)


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
        "openrouter_api_key": None,
        "anthropic_api_key": None,
        "serpapi_key": None,
        "serper_api_key": None,
        "serper_scrape_credit_cap": 0,
        "open_pagerank_api_key": None,
        "dataforseo_login": None,
        "dataforseo_password": None,
        "perplexity_api_key": None,
        "gemini_api_key": None,
        "ranksim_llm_model": None,
    }
    values.update(overrides)
    return Settings(**values)


def _drop_dotenv(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
    return init_settings, env_settings, file_secret_settings


def _paid_host(host: str) -> bool:
    name = host.lower().rstrip(".")
    return any(name == suffix or name.endswith("." + suffix) for suffix in PAID_HOSTS)


@pytest.fixture(autouse=True)
def isolate_provider_env(monkeypatch: pytest.MonkeyPatch):
    """Ambient provider keys and a local .env must not reach tests or paid hosts."""
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Settings, "settings_customise_sources", classmethod(_drop_dotenv))
    original = httpx.Client.send

    def guarded(self, request, *args, **kwargs):
        transport = getattr(self, "_transport", None)
        if not isinstance(transport, httpx.MockTransport) and _paid_host(request.url.host):
            raise AssertionError(f"blocked paid provider call during tests: {request.url.host}")
        return original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded)
    original_async = httpx.AsyncClient.send

    async def guarded_async(self, request, *args, **kwargs):
        transport = getattr(self, "_transport", None)
        if not isinstance(transport, httpx.MockTransport) and _paid_host(request.url.host):
            raise AssertionError(f"blocked paid provider call during tests: {request.url.host}")
        return await original_async(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", guarded_async)
