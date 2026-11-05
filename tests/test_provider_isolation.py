"""Ordinary tests must not call paid providers.

Ambient keys and a local .env are stripped for the suite. A dummy key can still
build a client, and that client is blocked before any socket opens.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from aeo.config import Settings
from aeo.ranksim.llm import resolve_llm


def test_partial_settings_stay_stub_when_no_provider_env():
    client = resolve_llm(Settings(openai_api_key=None, anthropic_api_key=None))
    assert client.available is False
    assert client.name == "stub"


def test_dotenv_provider_key_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-from-dotenv\nOPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.openrouter_api_key is None
    assert settings.openai_api_key is None
    assert resolve_llm(settings).available is False


def test_dummy_provider_env_cannot_call_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy")
    monkeypatch.setenv("SERPER_API_KEY", "serper-dummy")
    monkeypatch.setenv("BRAVE_API_KEY", "brave-dummy")

    offline = Settings(
        openai_api_key=None,
        anthropic_api_key=None,
        openrouter_api_key=None,
        serper_api_key=None,
        serpapi_key=None,
        brave_api_key=None,
        dataforseo_login=None,
        dataforseo_password=None,
    )
    assert resolve_llm(offline).available is False

    leaked = Settings(openai_api_key=None, anthropic_api_key=None)
    assert leaked.openrouter_api_key == "sk-or-dummy"
    client = resolve_llm(
        leaked,
        cache_dir=tmp_path / "cache",
        cost_log=tmp_path / "cost.jsonl",
    )
    assert client.available is True
    with pytest.raises(AssertionError, match="blocked paid provider"):
        client.complete("system", "score this page")


def test_mock_transport_can_still_post_to_a_paid_host():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "host": request.url.host})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = client.post("https://api.openai.com/v1/chat/completions", json={"ping": True})
    assert response.status_code == 200
    assert response.json()["host"] == "api.openai.com"
