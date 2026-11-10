"""SERP adapters. Google results come from SerpAPI, Serper, or DataForSEO.

Brave is a fallback web search, not a Google ranking. The mock is synthetic
and must be requested explicitly.

A failed collection is never an empty result: an HTTP error, a provider error payload
(HTTP 200 with an error body), or a response with no organic block raises SerpCollectError,
so callers do not record "not ranking" or overwrite a stored SERP with nothing. Timeouts,
429 and 5xx are retried with backoff first.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import httpx

from aeo.config import Settings
from aeo.errors import ProviderConfigError

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DATAFORSEO_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
SERPER_ENDPOINT = "https://google.serper.dev/search"


SERP_RETRY_BACKOFF = (2.0, 8.0)


class SerpCollectError(Exception):
    """A SERP request failed. Never treat this as 'no results'. Messages never include keys."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class SerpHit:
    position: int
    url: str
    title: str
    snippet: str
    provider: str
    synthetic: bool
    is_google: bool
    raw_position: int | None = None  # the provider's own position before organic renumbering


def _send(request: Callable[[], httpx.Response], provider: str, *, retries: int = 2, sleep: Callable[[float], None] | None = None) -> dict:
    """Run a provider request with bounded retries; return the JSON body or raise SerpCollectError."""
    sleep = sleep or time.sleep
    for attempt in range(retries + 1):
        try:
            response = request()
        except httpx.TimeoutException:
            error = SerpCollectError(f"{provider} request timed out.", retryable=True)
        except httpx.HTTPError as exc:
            error = SerpCollectError(f"{provider} request failed ({type(exc).__name__}).", retryable=True)
        else:
            if response.status_code == 429 or response.status_code >= 500:
                error = SerpCollectError(f"{provider} returned HTTP {response.status_code}.", retryable=True)
            elif response.status_code >= 400:
                raise SerpCollectError(f"{provider} returned HTTP {response.status_code}. Check the key and plan.")
            else:
                try:
                    return response.json()
                except ValueError:
                    error = SerpCollectError(f"{provider} returned a non-JSON body.", retryable=True)
        if attempt < retries:
            sleep(SERP_RETRY_BACKOFF[min(attempt, len(SERP_RETRY_BACKOFF) - 1)])
            continue
        raise error
    raise SerpCollectError(f"{provider} request failed.")  # pragma: no cover


class SerpProvider:
    name = "base"
    synthetic = False
    is_google = False
    # What the request asks for. None: not sent, so the provider's default applied (not recorded as US).
    country: str | None = None
    language: str | None = None
    device: str | None = None

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        raise NotImplementedError


SERPAPI_EMPTY = "hasn't returned any results"


def parse_serpapi(payload: dict, count: int) -> list[SerpHit]:
    error = payload.get("error")
    if error:
        if SERPAPI_EMPTY in str(error):
            return []
        raise SerpCollectError(f"serpapi error: {str(error)[:200]}")
    if "organic_results" not in payload:
        raise SerpCollectError("serpapi response has no organic_results block.")
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
    status = payload.get("status_code")
    if status is not None and status != 20000:
        raise SerpCollectError(f"dataforseo status {status}: {str(payload.get('status_message') or '')[:200]}")
    hits: list[SerpHit] = []
    tasks = payload.get("tasks") or []
    if not tasks:
        raise SerpCollectError("dataforseo response has no tasks.")
    for task in tasks:
        task_status = task.get("status_code")
        if task_status is not None and task_status != 20000:
            raise SerpCollectError(f"dataforseo task status {task_status}: {str(task.get('status_message') or '')[:200]}")
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
        raw_position=hit.raw_position if hit.raw_position is not None else hit.position,
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
        payload = _send(
            lambda: self.client.get(
                SERPAPI_ENDPOINT,
                params={"engine": "google", "q": query, "num": min(max(count, 1), 20), "api_key": self.api_key},
            ),
            self.name,
        )
        return parse_serpapi(payload, count)


class DataForSeoProvider(SerpProvider):
    country = "us"
    language = "en"
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
        payload = _send(
            lambda: self.client.post(
                DATAFORSEO_ENDPOINT,
                auth=(self.login, self.password),
                json=[{"keyword": query, "location_code": 2840, "language_code": "en", "depth": depth}],
            ),
            self.name,
        )
        return parse_dataforseo(payload, count)


def parse_serper(payload: dict, count: int) -> list[SerpHit]:
    if "organic" not in payload:
        message = payload.get("message") or payload.get("error") or "no organic block"
        raise SerpCollectError(f"serper response has no organic results: {str(message)[:200]}")
    hits: list[SerpHit] = []
    for item in payload.get("organic") or []:
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
                provider="serper",
                synthetic=False,
                is_google=True,
            )
        )
        if len(hits) >= count:
            break
    hits.sort(key=lambda hit: hit.position)
    return [_reposition(hit, index) for index, hit in enumerate(hits, start=1)]


class SerperProvider(SerpProvider):
    """Google organic results via Serper. Requires SERPER_API_KEY."""
    country = "us"
    language = "en"

    name = "serper"
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
        payload = _send(
            lambda: self.client.post(
                SERPER_ENDPOINT,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"q": query, "num": min(max(count, 1), 20), "gl": "us", "hl": "en"},
            ),
            self.name,
        )
        return parse_serper(payload, count)


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
        payload = _send(
            lambda: self.client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(max(count, 1), 20)},
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            ),
            self.name,
        )
        if payload.get("type") == "ErrorResponse" or "web" not in payload:
            raise SerpCollectError(f"brave response has no web results: {str(payload.get('error') or payload.get('type') or '')[:200]}")
        results = (payload.get("web") or {}).get("results") or []
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
        elif settings.serper_api_key:
            selected = "serper"
        elif settings.brave_api_key:
            selected = "brave"
        else:
            raise ProviderConfigError(
                "No SERP key is set. Refusing to invent Google results. "
                "Set SERPAPI_KEY, SERPER_API_KEY, or DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD. "
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
    if selected == "serper":
        if not settings.serper_api_key:
            raise ProviderConfigError("SERPER_API_KEY is not set. Refusing to invent Google results.")
        return SerperProvider(settings.serper_api_key)
    if selected == "brave":
        if not settings.brave_api_key:
            raise ProviderConfigError("BRAVE_API_KEY is not set.")
        return BraveFallbackProvider(settings.brave_api_key)
    if selected == "mock":
        return MockSerpProvider()
    raise ProviderConfigError("SERP provider must be auto, serpapi, dataforseo, serper, brave, or mock.")
