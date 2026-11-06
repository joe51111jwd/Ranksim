"""Dummy provider keys in the environment must not enable paid clients."""

from __future__ import annotations

import pytest

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim.llm import resolve_llm
from aeo.ranksim.serp import resolve_serp


def test_dummy_openrouter_env_leaves_the_stub_llm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    client = resolve_llm(Settings(openai_api_key=None, anthropic_api_key=None, openrouter_api_key=None))
    assert client.available is False


def test_dummy_serp_env_still_refuses_to_invent_google(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SERPER_API_KEY", "serper-dummy")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-dummy")
    monkeypatch.setenv("SERPAPI_KEY", "serpapi-dummy")
    settings = Settings(
        brave_api_key=None,
        serpapi_key=None,
        serper_api_key=None,
        dataforseo_login=None,
        dataforseo_password=None,
    )
    with pytest.raises(ProviderConfigError, match="Refusing to invent"):
        resolve_serp(settings, "auto")
