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


def test_async_paid_host_is_blocked_and_mock_transport_is_allowed():
    """No provider module constructs AsyncClient. The guard still blocks it without a live call."""
    import asyncio
    from pathlib import Path

    import httpx

    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src").rglob("*.py"))
    assert "AsyncClient" not in source

    async def blocked():
        async with httpx.AsyncClient() as client:
            request = httpx.Request("GET", "https://openrouter.ai/api/v1/models")
            with pytest.raises(AssertionError, match="blocked paid provider call"):
                await client.send(request)

    async def allowed():
        def handler(request):
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://openrouter.ai/api/v1/models")
            assert response.status_code == 200

    asyncio.run(blocked())
    asyncio.run(allowed())


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
