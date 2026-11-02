"""Pluggable chat completion. No key means a stub that does not invent text.

Calls are cached on disk. Cost is an estimate from published list rates and the
token counts the API returns. A cache hit costs nothing. Unknown models are
logged with tokens only, not a made-up dollar amount.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aeo.config import Settings

# USD per 1,000,000 tokens: (input, output). Published list rates, not an invoice.
LIST_RATES = {
    "gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
}

OPENAI_DEFAULT = "gpt-4o-mini"
ANTHROPIC_DEFAULT = "claude-3-5-haiku-20241022"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT = "openai/gpt-4o-mini"


@dataclass
class LlmReply:
    text: str
    provider: str
    stub: bool
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_usd: float | None = None
    cache_hit: bool = False


class LlmClient:
    name = "base"
    available = False
    model = ""

    def complete(self, system: str, user: str) -> LlmReply:
        raise RuntimeError("No LLM is configured.")

    def cost_summary(self) -> str:
        return "No LLM client."


class StubLlm(LlmClient):
    name = "stub"
    available = False
    model = ""

    def complete(self, system: str, user: str) -> LlmReply:
        return LlmReply(text="", provider="stub", stub=True, model="", estimated_usd=0.0)

    def cost_summary(self) -> str:
        return "LLM stub. Estimated cost $0. No API calls."


class OpenAiLlm(LlmClient):
    name = "openai"
    available = True

    def __init__(self, api_key: str, model: str, base_url: str, extra_headers: dict[str, str] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.extra_headers = extra_headers or {}

    def complete(self, system: str, user: str) -> LlmReply:
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
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
        payload = response.json()
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage") or {}
        return LlmReply(
            text=str(text or ""),
            provider=self.name,
            stub=False,
            model=self.model,
            input_tokens=_int(usage.get("prompt_tokens")),
            output_tokens=_int(usage.get("completion_tokens")),
        )


class OpenRouterLlm(OpenAiLlm):
    """OpenRouter's OpenAI-compatible chat API."""

    name = "openrouter"

    def __init__(self, api_key: str, model: str) -> None:
        super().__init__(
            api_key,
            model,
            OPENROUTER_BASE,
            {
                "HTTP-Referer": "https://www.smartcrewhq.com",
                "X-Title": "SmartCrew rank check",
            },
        )


class AnthropicLlm(LlmClient):
    name = "anthropic"
    available = True

    def __init__(self, api_key: str, model: str) -> None:
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
        payload = response.json()
        parts = payload.get("content") or []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        usage = payload.get("usage") or {}
        return LlmReply(
            text=text,
            provider="anthropic",
            stub=False,
            model=self.model,
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
        )


class CachingLlm(LlmClient):
    """Disk cache and a JSONL cost log around a real client."""

    def __init__(self, inner: LlmClient, cache_dir: Path, cost_log: Path, run_id: str) -> None:
        self.inner = inner
        self.name = inner.name
        self.available = inner.available
        self.model = inner.model
        self.cache_dir = cache_dir
        self.cost_log = cost_log
        self.run_id = run_id
        self.calls = 0
        self.cache_hits = 0
        self.estimated_usd = 0.0
        self.unpriced_calls = 0
        cache_dir.mkdir(parents=True, exist_ok=True)
        cost_log.parent.mkdir(parents=True, exist_ok=True)

    def complete(self, system: str, user: str) -> LlmReply:
        key = hashlib.sha256(
            json.dumps(
                {"provider": self.inner.name, "model": self.model, "system": system, "user": user},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            reply = LlmReply(
                text=str(saved.get("text") or ""),
                provider=self.inner.name,
                stub=False,
                model=self.model,
                input_tokens=saved.get("input_tokens"),
                output_tokens=saved.get("output_tokens"),
                estimated_usd=0.0,
                cache_hit=True,
            )
            self.cache_hits += 1
            self._log(reply)
            return reply
        reply = self.inner.complete(system, user)
        reply.model = reply.model or self.model
        reply.estimated_usd = estimate_cost(reply.model, reply.input_tokens, reply.output_tokens)
        reply.cache_hit = False
        path.write_text(
            json.dumps(
                {
                    "text": reply.text,
                    "model": reply.model,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                }
            ),
            encoding="utf-8",
        )
        self.calls += 1
        if reply.estimated_usd is None:
            self.unpriced_calls += 1
        else:
            self.estimated_usd += reply.estimated_usd
        self._log(reply)
        return reply

    def cost_summary(self) -> str:
        priced = f"${self.estimated_usd:.6f}"
        extra = ""
        if self.unpriced_calls:
            extra = f" {self.unpriced_calls} calls had no published rate, so their dollars were not estimated."
        return (
            f"LLM {self.name} {self.model}. Fresh calls {self.calls}, cache hits {self.cache_hits}. "
            f"Estimated cost this run {priced}.{extra} Log: {self.cost_log}."
        )

    def _log(self, reply: LlmReply) -> None:
        row = {
            "at": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "provider": reply.provider,
            "model": reply.model,
            "cache_hit": reply.cache_hit,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "estimated_usd": reply.estimated_usd,
        }
        with self.cost_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    rates = LIST_RATES.get(model)
    if rates is None or input_tokens is None or output_tokens is None:
        return None
    in_rate, out_rate = rates
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def openrouter_key(settings: Settings) -> str | None:
    if settings.openrouter_api_key:
        return settings.openrouter_api_key
    key = settings.openai_api_key or ""
    if key.startswith("sk-or-"):
        return key
    return None


def resolve_llm(settings: Settings, *, cache_dir: Path | None = None, cost_log: Path | None = None, run_id: str = "run") -> LlmClient:
    chosen = (settings.ranksim_llm_model or "").strip()
    router = openrouter_key(settings)
    if router:
        inner: LlmClient = OpenRouterLlm(router, chosen or OPENROUTER_DEFAULT)
    elif settings.openai_api_key:
        inner = OpenAiLlm(settings.openai_api_key, chosen or OPENAI_DEFAULT, settings.openai_base_url)
    elif settings.anthropic_api_key:
        inner = AnthropicLlm(settings.anthropic_api_key, chosen or ANTHROPIC_DEFAULT)
    else:
        return StubLlm()
    return CachingLlm(
        inner,
        cache_dir or Path("data/llm-cache"),
        cost_log or Path("data/llm-cost.jsonl"),
        run_id,
    )


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
