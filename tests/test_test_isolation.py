"""The suite must give the same result on a laptop with provider keys as in CI."""

import socket

import pytest

from aeo.config import Settings
from aeo.ranksim.llm import StubLlm, resolve_llm
from aeo.ranksim.serp import resolve_serp
from aeo.errors import ProviderConfigError


def test_dotenv_keys_are_not_loaded_in_tests(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=sk-or-from-dotenv\nSERPER_API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.openrouter_api_key is None
    assert settings.serper_api_key is None
    assert isinstance(resolve_llm(settings), StubLlm)
    with pytest.raises(ProviderConfigError, match="Refusing to invent"):
        resolve_serp(settings, "auto")


def test_default_settings_have_no_provider_keys():
    settings = Settings()
    for name in (
        "openai_api_key",
        "openrouter_api_key",
        "anthropic_api_key",
        "serper_api_key",
        "serpapi_key",
        "brave_api_key",
        "dataforseo_login",
        "dataforseo_password",
    ):
        assert getattr(settings, name) is None, name


def test_external_network_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(RuntimeError, match="network connection"):
            sock.connect(("192.0.2.1", 443))
    finally:
        sock.close()
