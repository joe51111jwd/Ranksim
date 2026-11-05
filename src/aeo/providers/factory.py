"""Resolve the search, LLM, and embedding providers from settings."""

from __future__ import annotations

import contextvars

import httpx

from aeo.config import Settings
from aeo.crawl.fetch import Crawler
from aeo.crawl.jsrender import JsRenderer, build_renderer
from aeo.errors import ProviderConfigError
from aeo.providers.embeddings import Embedder, MockEmbedder, OpenAICompatibleEmbedder
from aeo.providers.llm import LLMClient, MockLLMClient, OpenAICompatibleLLM
from aeo.providers.search import BraveSearchProvider, MockSearchProvider, NullSearchProvider, SearchProvider

http_client_override: contextvars.ContextVar[httpx.Client | None] = contextvars.ContextVar(
    "aeo_http_client",
    default=None,
)
_client_by_settings: dict[int, httpx.Client] = {}


def use_http_client(settings: Settings, client: httpx.Client | None) -> None:
    """Pin an HTTP client for this settings object. Tests use it so crawls do not touch the network."""
    if client is None:
        _client_by_settings.pop(id(settings), None)
    else:
        _client_by_settings[id(settings)] = client


def resolve_provider_names(
    settings: Settings,
    *,
    search: str | None = None,
    llm: str | None = None,
    embedding: str | None = None,
) -> tuple[str, str, str, list[str]]:
    """Return concrete provider names and warnings. ``auto`` never invents a live result."""
    warnings: list[str] = []
    search_name = _resolve_search(settings, search or settings.search_provider, warnings)
    llm_name = _resolve_llm(settings, llm or settings.llm_provider, warnings)
    embedding_name = _resolve_embedding(settings, embedding or settings.embedding_provider, warnings)
    return search_name, llm_name, embedding_name, warnings


def _resolve_search(settings: Settings, choice: str, warnings: list[str]) -> str:
    choice = choice.lower()
    if choice == "auto":
        if not settings.brave_api_key:
            # Never synthetic competitors at runtime: without a key the comparison simply does not run.
            warnings.append("Competitor search not run: needs BRAVE_API_KEY. No competitor pages were invented.")
            return "none"
        choice = "brave"
    if choice == "brave":
        if not settings.brave_api_key:
            raise ProviderConfigError(
                "SEARCH_PROVIDER=brave requires BRAVE_API_KEY. The simulator will not invent web results."
            )
        return "brave"
    if choice == "mock":
        warnings.append(
            "Competitor pages are synthetic documents from the mock search provider, not live web results."
        )
        return "mock"
    if choice == "none":
        warnings.append("Competitor search is off. The site is not compared with live or mock rivals.")
        return "none"
    raise ProviderConfigError(f"Unknown search provider '{choice}'. Use auto, brave, mock, or none.")


def _resolve_llm(settings: Settings, choice: str, warnings: list[str]) -> str:
    choice = choice.lower()
    if choice == "auto":
        if not settings.openai_api_key:
            # Never mock answers at runtime: the simulation does not run without a model.
            raise ProviderConfigError("AI answer simulation not run: needs OPENAI_API_KEY. No answer was invented.")
        choice = "openai"
    if choice == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY. The simulator will not invent model output."
            )
        warnings.append(
            "Answers come from the configured OpenAI-compatible model. "
            "They are sandbox answers, not official ChatGPT, Perplexity, or Google AI Overview results."
        )
        return "openai"
    if choice == "mock":
        warnings.append(
            "Answers are from the deterministic mock LLM, not from ChatGPT, Perplexity, or Google."
        )
        return "mock"
    raise ProviderConfigError(f"Unknown LLM provider '{choice}'. Use auto, openai, or mock.")


def _resolve_embedding(settings: Settings, choice: str, warnings: list[str]) -> str:
    choice = choice.lower()
    if choice == "auto":
        if settings.openai_api_key and settings.embedding_dim >= 256:
            choice = "openai"
        else:
            if settings.openai_api_key and settings.embedding_dim < 256:
                warnings.append(
                    "OPENAI_API_KEY is set, but EMBEDDING_DIM is below 256, so embeddings stay on the "
                    "deterministic mock. Set EMBEDDING_PROVIDER=openai and EMBEDDING_DIM to the model size to use API embeddings."
                )
            choice = "mock"
    if choice == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY.")
        return "openai"
    if choice == "mock":
        warnings.append("Passage vectors are deterministic hashing embeddings, not a neural model.")
        return "mock"
    raise ProviderConfigError(f"Unknown embedding provider '{choice}'. Use auto, openai, or mock.")


def build_search(settings: Settings, name: str) -> SearchProvider:
    if name == "brave":
        if not settings.brave_api_key:
            raise ProviderConfigError("BRAVE_API_KEY is not set.")
        return BraveSearchProvider(settings.brave_api_key, timeout=settings.request_timeout_seconds)
    if name == "mock":
        return MockSearchProvider()
    if name == "none":
        return NullSearchProvider()
    raise ProviderConfigError(f"Unknown search provider '{name}'")


def build_llm(settings: Settings, name: str) -> LLMClient:
    if name == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigError("OPENAI_API_KEY is not set.")
        return OpenAICompatibleLLM(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            timeout=max(settings.request_timeout_seconds, 60.0),
        )
    if name == "mock":
        return MockLLMClient()
    raise ProviderConfigError(f"Unknown LLM provider '{name}'")


def build_embedder(settings: Settings, name: str) -> Embedder:
    if name == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigError("OPENAI_API_KEY is not set.")
        return OpenAICompatibleEmbedder(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_embedding_model,
            dim=settings.embedding_dim,
            timeout=max(settings.request_timeout_seconds, 60.0),
        )
    if name == "mock":
        return MockEmbedder(dim=settings.embedding_dim)
    raise ProviderConfigError(f"Unknown embedding provider '{name}'")


def build_crawler(settings: Settings) -> Crawler:
    override = http_client_override.get() or _client_by_settings.get(id(settings))
    return Crawler(
        user_agent=settings.crawl_user_agent,
        delay_seconds=settings.crawl_delay_seconds,
        allow_private=settings.crawl_allow_private,
        timeout=settings.request_timeout_seconds,
        client=override,
    )


def build_js_renderer(requested: bool) -> JsRenderer:
    return build_renderer(requested)


def synthetic_flags(search_name: str, llm_name: str, embedding_name: str) -> tuple[bool, bool, bool, bool]:
    search_synthetic = search_name == "mock"
    llm_synthetic = llm_name == "mock"
    embedding_synthetic = embedding_name == "mock"
    return search_synthetic, llm_synthetic, embedding_synthetic, search_synthetic or llm_synthetic or embedding_synthetic
