import httpx

import pytest

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.providers.embeddings import OpenAICompatibleEmbedder
from aeo.providers.factory import resolve_provider_names
from aeo.providers.llm import OpenAICompatibleLLM
from aeo.providers.search import BRAVE_ENDPOINT, BraveSearchProvider, MockSearchProvider


def test_auto_without_keys_is_mock_and_labeled():
    settings = Settings(
        database_url="sqlite:///:memory:",
        search_provider="auto",
        llm_provider="auto",
        embedding_provider="auto",
        brave_api_key=None,
        openai_api_key=None,
    )
    search, llm, embedding, warnings = resolve_provider_names(settings)
    assert (search, llm, embedding) == ("mock", "mock", "mock")
    assert warnings
    assert any("mock" in warning.lower() or "synthetic" in warning.lower() or "deterministic" in warning.lower() for warning in warnings)


def test_brave_without_a_key_refuses_to_invent_results():
    settings = Settings(
        database_url="sqlite:///:memory:",
        search_provider="brave",
        brave_api_key=None,
        openai_api_key=None,
    )
    with pytest.raises(ProviderConfigError, match="BRAVE_API_KEY"):
        resolve_provider_names(settings)


def test_mock_search_hits_are_synthetic_and_carry_html():
    hits = MockSearchProvider().search("best trail mix", count=2)
    assert len(hits) == 2
    assert all(hit.synthetic and hit.inline_html and hit.provider == "mock" for hit in hits)


def test_brave_adapter_parses_live_result_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(BRAVE_ENDPOINT)
        assert request.headers["X-Subscription-Token"] == "test-key"
        assert request.url.params["q"] == "trail mix"
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "Real", "url": "https://example.com/a", "description": "desc"}]}},
        )

    provider = BraveSearchProvider("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    hits = provider.search("trail mix", count=3)
    assert hits[0].url == "https://example.com/a"
    assert hits[0].synthetic is False
    assert hits[0].inline_html is None
    assert hits[0].provider == "brave"


def test_openai_compatible_clients_return_the_payload_unchanged():
    def chat(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert b"trail mix" in body
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"subqueries":["trail mix"]}'}}]})

    llm = OpenAICompatibleLLM(
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="mock-model",
        client=httpx.Client(transport=httpx.MockTransport(chat)),
    )
    assert "trail mix" in llm.fanout("trail mix")

    def embed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.25, 0.75]}]})

    embedder = OpenAICompatibleEmbedder(
        api_key="sk-test",
        base_url="https://example.invalid/v1",
        model="embed",
        dim=2,
        client=httpx.Client(transport=httpx.MockTransport(embed)),
    )
    assert embedder.embed("hello") == [0.25, 0.75]
