"""Pluggable chat completion. No key means a stub that does not invent text."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aeo.config import Settings


@dataclass
class LlmReply:
    text: str
    provider: str
    stub: bool


class LlmClient:
    name = "base"
    available = False

    def complete(self, system: str, user: str) -> LlmReply:
        raise RuntimeError("No LLM is configured.")


class StubLlm(LlmClient):
    name = "stub"
    available = False

    def complete(self, system: str, user: str) -> LlmReply:
        return LlmReply(
            text="",
            provider="stub",
            stub=True,
        )


class OpenAiLlm(LlmClient):
    name = "openai"
    available = True

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str) -> LlmReply:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return LlmReply(text=str(text or ""), provider="openai", stub=False)


class AnthropicLlm(LlmClient):
    name = "anthropic"
    available = True

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest") -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> LlmReply:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1200,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        parts = response.json().get("content") or []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        return LlmReply(text=text, provider="anthropic", stub=False)


def resolve_llm(settings: Settings) -> LlmClient:
    if settings.openai_api_key:
        return OpenAiLlm(settings.openai_api_key, settings.openai_model, settings.openai_base_url)
    if settings.anthropic_api_key:
        return AnthropicLlm(settings.anthropic_api_key)
    return StubLlm()
