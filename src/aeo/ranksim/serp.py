"""SERP adapters. Google results come only from SerpAPI or DataForSEO.

Brave is a fallback web search, not a Google ranking. The mock is synthetic
and must be requested explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from aeo.config import Settings
from aeo.errors import ProviderConfigError

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DATAFORSEO_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"


@dataclass(frozen=True)
class SerpHit:
    position: int
    url: str
    title: str
    snippet: str
    provider: str
    synthetic: bool
    is_google: bool


class SerpProvider:
    name = "base"
    synthetic = False
    is_google = False

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        raise NotImplementedError


def parse_serpapi(payload: dict, count: int) -> list[SerpHit]:
    hits: list[SerpHit] = []
    for item in payload.get("organic_results") or []:
        url = item.get("link") or item.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        position = int(item.get("position") or len(hits) + 1)
        hits.append(
            SerpHit(
                position=position,
                url=url,
                title=str(item.get("title") or ""),
                snippet=str(item.get("snippet") or ""),
                provider="serpapi",
                synthetic=False,
                is_google=True,
            )
        )
        if len(hits) >= count:
            break
    hits.sort(key=lambda hit: hit.position)
    return [_reposition(hit, index) for index, hit in enumerate(hits, start=1)]


def parse_dataforseo(payload: dict, count: int) -> list[SerpHit]:
    hits: list[SerpHit] = []
    tasks = payload.get("tasks") or []
    for task in tasks:
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                if item.get("type") != "organic":
                    continue
                url = item.get("url")
                if not isinstance(url, str) or not url.startswith("http"):
                    continue
                position = int(item.get("rank_group") or item.get("rank_absolute") or len(hits) + 1)
                hits.append(
                    SerpHit(
                        position=position,
                        url=url,
                        title=str(item.get("title") or ""),
                        snippet=str(item.get("description") or ""),
                        provider="dataforseo",
                        synthetic=False,
                        is_google=True,
                    )
                )
    hits.sort(key=lambda hit: hit.position)
    trimmed = hits[:count]
    return [_reposition(hit, index) for index, hit in enumerate(trimmed, start=1)]


def _reposition(hit: SerpHit, position: int) -> SerpHit:
    return SerpHit(
        position=position,
        url=hit.url,
        title=hit.title,
        snippet=hit.snippet,
        provider=hit.provider,
        synthetic=hit.synthetic,
        is_google=hit.is_google,
    )


class SerpApiProvider(SerpProvider):
    name = "serpapi"
    synthetic = False
    is_google = True

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._owns = client is None
        self.client = client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        response = self.client.get(
            SERPAPI_ENDPOINT,
            params={"engine": "google", "q": query, "num": min(max(count, 1), 20), "api_key": self.api_key},
        )
        response.raise_for_status()
        return parse_serpapi(response.json(), count)


class DataForSeoProvider(SerpProvider):
    name = "dataforseo"
    synthetic = False
    is_google = True

    def __init__(self, login: str, password: str, client: httpx.Client | None = None) -> None:
        self.login = login
        self.password = password
        self._owns = client is None
        self.client = client or httpx.Client(timeout=60.0)

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        depth = min(max(count, 1), 20)
        response = self.client.post(
            DATAFORSEO_ENDPOINT,
            auth=(self.login, self.password),
            json=[{"keyword": query, "location_code": 2840, "language_code": "en", "depth": depth}],
        )
        response.raise_for_status()
        return parse_dataforseo(response.json(), count)


class BraveFallbackProvider(SerpProvider):
    """Brave web search. Positions are Brave's order, not Google's."""

    name = "brave"
    synthetic = False
    is_google = False

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._owns = client is None
        self.client = client or httpx.Client(timeout=30.0)

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        response = self.client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(max(count, 1), 20)},
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
        )
        response.raise_for_status()
        results = ((response.json().get("web") or {}).get("results")) or []
        hits: list[SerpHit] = []
        for item in results:
            url = item.get("url")
            if not isinstance(url, str):
                continue
            hits.append(
                SerpHit(
                    position=len(hits) + 1,
                    url=url,
                    title=str(item.get("title") or ""),
                    snippet=str(item.get("description") or ""),
                    provider="brave",
                    synthetic=False,
                    is_google=False,
                )
            )
            if len(hits) >= count:
                break
        return hits


class MockSerpProvider(SerpProvider):
    """Deterministic synthetic results for tests. Not a Google SERP."""

    name = "mock"
    synthetic = True
    is_google = False

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        hits: list[SerpHit] = []
        for index in range(1, min(count, 5) + 1):
            hits.append(
                SerpHit(
                    position=index,
                    url=f"https://mock-rival-{index}.example/{_slug(query)}",
                    title=f"Mock result {index} for {query}",
                    snippet=f"Synthetic page. Not a live Google result. Query: {query}",
                    provider="mock",
                    synthetic=True,
                    is_google=False,
                )
            )
        return hits


def _slug(query: str) -> str:
    return "-".join(query.lower().split())[:80] or "query"


def resolve_serp(settings: Settings, choice: str | None) -> SerpProvider:
    selected = (choice or "auto").strip().lower()
    if selected == "auto":
        if settings.serpapi_key:
            selected = "serpapi"
        elif settings.dataforseo_login and settings.dataforseo_password:
            selected = "dataforseo"
        elif settings.brave_api_key:
            selected = "brave"
        else:
            raise ProviderConfigError(
                "No SERP key is set. Refusing to invent Google results. "
                "Set SERPAPI_KEY or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD. "
                "BRAVE_API_KEY is a non-Google fallback. Use --mock only for tests."
            )
    if selected == "serpapi":
        if not settings.serpapi_key:
            raise ProviderConfigError("SERPAPI_KEY is not set. Refusing to invent Google results.")
        return SerpApiProvider(settings.serpapi_key)
    if selected == "dataforseo":
        if not settings.dataforseo_login or not settings.dataforseo_password:
            raise ProviderConfigError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required.")
        return DataForSeoProvider(settings.dataforseo_login, settings.dataforseo_password)
    if selected == "brave":
        if not settings.brave_api_key:
            raise ProviderConfigError("BRAVE_API_KEY is not set.")
        return BraveFallbackProvider(settings.brave_api_key)
    if selected == "mock":
        return MockSerpProvider()
    raise ProviderConfigError("SERP provider must be auto, serpapi, dataforseo, brave, or mock.")
