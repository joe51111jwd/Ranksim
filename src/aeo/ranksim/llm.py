"""Pluggable chat completion. No key means a stub that does not invent text.

Calls are cached on disk. Cost is an estimate from published list rates and the
token counts the API returns. A cache hit costs nothing. Unknown models are
logged with tokens only, not a made-up dollar amount.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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
DEFAULT_MAX_TOKENS = 800
# Bump when the cache key or stored reply format changes, so old entries are not reused.
CACHE_VERSION = 2
RETRY_BACKOFF_SECONDS = (1.0, 4.0)


class LlmError(Exception):
    """A provider call failed. The message never includes the API key."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _post(url: str, *, headers: dict, payload: dict, timeout: float) -> dict:
    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LlmError(f"LLM request timed out ({type(exc).__name__}).", retryable=True) from None
    except httpx.HTTPError as exc:
        raise LlmError(f"LLM request failed ({type(exc).__name__}).", retryable=True) from None
    if response.status_code == 429 or response.status_code >= 500:
        raise LlmError(f"LLM provider returned HTTP {response.status_code}.", retryable=True)
    if response.status_code >= 400:
        raise LlmError(f"LLM provider returned HTTP {response.status_code}. Check the key and model name.")
    try:
        return response.json()
    except ValueError:
        raise LlmError("LLM provider returned a non-JSON body.") from None


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
    note: str = ""


class LlmClient:
    name = "base"
    available = False
    model = ""

    def complete(
        self, system: str, user: str, validate: Callable[[str], bool] | None = None, max_tokens: int | None = None
    ) -> LlmReply:
        raise RuntimeError("No LLM is configured.")

    def cost_summary(self) -> str:
        return "No LLM client."


class StubLlm(LlmClient):
    name = "stub"
    available = False
    model = ""

    def complete(
        self, system: str, user: str, validate: Callable[[str], bool] | None = None, max_tokens: int | None = None
    ) -> LlmReply:
        return LlmReply(text="", provider="stub", stub=True, model="", estimated_usd=0.0)

    def cost_summary(self) -> str:
        return "LLM stub. Estimated cost $0. No API calls."


class OpenAiLlm(LlmClient):
    name = "openai"
    available = True

    temperature = 0
    max_tokens = DEFAULT_MAX_TOKENS

    def __init__(self, api_key: str, model: str, base_url: str, extra_headers: dict[str, str] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.extra_headers = extra_headers or {}

    def complete(
        self, system: str, user: str, validate: Callable[[str], bool] | None = None, max_tokens: int | None = None
    ) -> LlmReply:
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        payload = _post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            payload={
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": max_tokens or self.max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=60.0,
        )
        try:
            choice = payload["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LlmError("LLM provider returned an unexpected response shape.") from None
        if choice.get("finish_reason") == "length":
            raise LlmError("LLM reply was cut off at the token limit, so it was not used.")
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

    base_url = "https://api.anthropic.com/v1"
    temperature = 0
    max_tokens = DEFAULT_MAX_TOKENS

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(
        self, system: str, user: str, validate: Callable[[str], bool] | None = None, max_tokens: int | None = None
    ) -> LlmReply:
        payload = _post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload={
                "model": self.model,
                "max_tokens": max_tokens or self.max_tokens,
                "temperature": self.temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=60.0,
        )
        if payload.get("stop_reason") == "max_tokens":
            raise LlmError("LLM reply was cut off at the token limit, so it was not used.")
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
    """Disk cache, JSONL cost log, bounded retries and a spend cap around a real client.

    - The cache key covers provider, endpoint, model, sampling settings, a cache version and
      the full prompt. A reply that fails the caller's ``validate`` check is never cached.
    - Retryable failures (timeouts, HTTP 429 and 5xx) are retried at most ``retries`` times
      with backoff. Any remaining failure returns a stub reply with a note; callers already
      fall back to templates on stub replies, so a provider outage never crashes a check.
    - Once ``max_calls`` fresh calls or ``max_usd`` estimated dollars are reached in this
      client, further fresh calls return a stub reply saying the budget was reached.
    """

    def __init__(
        self,
        inner: LlmClient,
        cache_dir: Path,
        cost_log: Path,
        run_id: str,
        *,
        max_calls: int | None = None,
        max_usd: float | None = None,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.name = inner.name
        self.available = inner.available
        self.model = inner.model
        self.cache_dir = cache_dir
        self.cost_log = cost_log
        self.run_id = run_id
        self.max_calls = max_calls
        self.max_usd = max_usd
        self.retries = max(0, retries)
        self.sleep = sleep
        self.calls = 0
        self.attempts = 0
        self.cache_hits = 0
        self.failures = 0
        self.unpriced_seen = False
        self.budget_refusals = 0
        self.estimated_usd = 0.0
        self.unpriced_calls = 0
        # Directories are created on first write, so building a client never touches disk.

    def cache_key(self, system: str, user: str, max_tokens: int | None = None) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "v": CACHE_VERSION,
                    "provider": self.inner.name,
                    "base_url": getattr(self.inner, "base_url", ""),
                    "model": self.model,
                    "temperature": getattr(self.inner, "temperature", None),
                    "max_tokens": max_tokens or getattr(self.inner, "max_tokens", None),
                    "system": system,
                    "user": user,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def complete(
        self, system: str, user: str, validate: Callable[[str], bool] | None = None, max_tokens: int | None = None
    ) -> LlmReply:
        key = self.cache_key(system, user, max_tokens)
        path = self.cache_dir / f"{key}.json"
        saved = None
        if path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None
            if saved is not None and validate is not None and not validate(str(saved.get("text") or "")):
                saved = None
        if saved is not None:
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
        refusal = self._over_budget()
        if refusal:
            self.budget_refusals += 1
            return LlmReply(text="", provider=self.inner.name, stub=True, model=self.model, estimated_usd=0.0, note=refusal)
        reply = None
        extra = {"max_tokens": max_tokens} if max_tokens else {}
        for attempt in range(self.retries + 1):
            if attempt and self._over_budget():
                break
            # Every request sent counts toward the call cap: a failed or timed-out request can be billed too.
            self.attempts += 1
            try:
                reply = self.inner.complete(system, user, **extra)
                break
            except LlmError as exc:
                if exc.retryable and attempt < self.retries:
                    self.sleep(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                self.failures += 1
                return LlmReply(text="", provider=self.inner.name, stub=True, model=self.model, estimated_usd=0.0, note=str(exc))
        if reply is None:
            self.failures += 1
            return LlmReply(text="", provider=self.inner.name, stub=True, model=self.model, estimated_usd=0.0, note=self._over_budget())
        assert reply is not None
        reply.model = reply.model or self.model
        reply.estimated_usd = estimate_cost(reply.model, reply.input_tokens, reply.output_tokens)
        reply.cache_hit = False
        if validate is None or validate(reply.text):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
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
            self.unpriced_seen = True
        else:
            self.estimated_usd += reply.estimated_usd
        self._log(reply)
        return reply

    def _over_budget(self) -> str:
        if self.max_calls is not None and self.attempts >= self.max_calls:
            return f"LLM budget reached: {self.attempts} requests sent (limit {self.max_calls}). No more calls this run."
        if self.max_usd is not None and self.max_usd > 0:
            if self.unpriced_seen:
                return (
                    f"LLM budget stop: model {self.model} has no published rate here, so the ${self.max_usd:.2f} cap "
                    "cannot be enforced. Set RANKSIM_LLM_MAX_USD=0 to rely on the call cap alone."
                )
            if self.estimated_usd >= self.max_usd:
                return f"LLM budget reached: estimated ${self.estimated_usd:.4f} (limit ${self.max_usd:.2f}). No more calls this run."
        return ""

    def cost_summary(self) -> str:
        priced = f"${self.estimated_usd:.6f}"
        extra = ""
        if self.unpriced_calls:
            extra = f" {self.unpriced_calls} calls had no published rate, so their dollars were not estimated."
        if self.failures:
            extra += f" {self.failures} calls failed after retries and fell back to templates."
        if self.budget_refusals:
            extra += f" {self.budget_refusals} calls were refused by the spend limit."
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
        self.cost_log.parent.mkdir(parents=True, exist_ok=True)
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


LLM_PROVIDERS = ("auto", "openrouter", "openai", "anthropic")
OPENAI_PUBLIC_BASE = "https://api.openai.com"


def model_format_problem(provider: str, model: str, *, openai_base_url: str | None = None) -> str | None:
    """Why this model name cannot be meant for this provider, or None. One name is no longer sent to
    whichever provider has a key."""
    if provider == "openrouter" and "/" not in model:
        return "OpenRouter model names look like vendor/model, for example openai/gpt-4o-mini"
    if provider == "openai" and "/" in model and (openai_base_url or OPENAI_PUBLIC_BASE).startswith(OPENAI_PUBLIC_BASE):
        return "OpenAI model names have no vendor prefix, for example gpt-4o-mini (openai/... is an OpenRouter name)"
    if provider == "anthropic" and not model.startswith("claude"):
        return "Anthropic model names start with claude, for example claude-3-5-haiku-20241022"
    return None


def select_llm_provider(settings: Settings) -> tuple[str, str | None]:
    """(provider, key). auto picks OpenRouter, then OpenAI, then Anthropic by which key is set; a
    named provider without its key is a configuration error, not a silent switch."""
    from aeo.errors import ProviderConfigError

    wanted = (getattr(settings, "ranksim_llm_provider", "auto") or "auto").strip().lower()
    if wanted not in LLM_PROVIDERS:
        raise ProviderConfigError(f"RANKSIM_LLM_PROVIDER must be one of {', '.join(LLM_PROVIDERS)}, not {wanted!r}.")
    router = openrouter_key(settings)
    openai_key = settings.openai_api_key if settings.openai_api_key and not settings.openai_api_key.startswith("sk-or-") else None
    keys = {"openrouter": router, "openai": openai_key, "anthropic": settings.anthropic_api_key}
    if wanted == "auto":
        for name in ("openrouter", "openai", "anthropic"):
            if keys[name]:
                return name, keys[name]
        return "none", None
    if not keys[wanted]:
        needed = {"openrouter": "OPENROUTER_API_KEY (or an sk-or- key in OPENAI_API_KEY)", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
        raise ProviderConfigError(f"RANKSIM_LLM_PROVIDER={wanted} needs {needed[wanted]}.")
    return wanted, keys[wanted]


def resolve_llm(settings: Settings, *, cache_dir: Path | None = None, cost_log: Path | None = None, run_id: str = "run") -> LlmClient:
    from aeo.errors import ProviderConfigError

    chosen = (settings.ranksim_llm_model or "").strip()
    provider, key = select_llm_provider(settings)
    if provider == "none":
        return StubLlm()
    model = chosen or {"openrouter": OPENROUTER_DEFAULT, "openai": OPENAI_DEFAULT, "anthropic": ANTHROPIC_DEFAULT}[provider]
    problem = model_format_problem(provider, model, openai_base_url=settings.openai_base_url)
    if problem:
        raise ProviderConfigError(f"RANKSIM_LLM_MODEL={model!r} does not fit the {provider} provider: {problem}.")
    if provider == "openrouter":
        inner: LlmClient = OpenRouterLlm(key, model)
    elif provider == "openai":
        inner = OpenAiLlm(key, model, settings.openai_base_url)
    else:
        inner = AnthropicLlm(key, model)
    return CachingLlm(
        inner,
        cache_dir or Path("data/llm-cache"),
        cost_log or Path("data/llm-cost.jsonl"),
        run_id,
        max_calls=settings.ranksim_llm_max_calls,
        max_usd=settings.ranksim_llm_max_usd,
    )


def _int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
