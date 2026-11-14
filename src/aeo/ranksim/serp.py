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
SERPER_ENDPOINT = "https://google.serper.dev/search"


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

    def search_with_features(self, query: str, count: int = 10) -> tuple[list[SerpHit], dict]:
        """Organic hits plus what the page showed beyond them (see parse_features). One request, same cost."""
        return self.search(query, count), {}


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

    def _payload(self, query: str, count: int) -> dict:
        response = self.client.get(
            SERPAPI_ENDPOINT,
            params={"engine": "google", "q": query, "num": min(max(count, 1), 20), "api_key": self.api_key},
        )
        response.raise_for_status()
        return response.json()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        return parse_serpapi(self._payload(query, count), count)

    def search_with_features(self, query: str, count: int = 10) -> tuple[list[SerpHit], dict]:
        payload = self._payload(query, count)
        return parse_serpapi(payload, count), parse_features(self.name, payload)


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

    def _payload(self, query: str, count: int) -> dict:
        depth = min(max(count, 1), 20)
        response = self.client.post(
            DATAFORSEO_ENDPOINT,
            auth=(self.login, self.password),
            json=[{"keyword": query, "location_code": 2840, "language_code": "en", "depth": depth}],
        )
        response.raise_for_status()
        return response.json()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        return parse_dataforseo(self._payload(query, count), count)

    def search_with_features(self, query: str, count: int = 10) -> tuple[list[SerpHit], dict]:
        payload = self._payload(query, count)
        return parse_dataforseo(payload, count), parse_features(self.name, payload)


def parse_serper(payload: dict, count: int) -> list[SerpHit]:
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

    def _payload(self, query: str, count: int) -> dict:
        response = self.client.post(
            SERPER_ENDPOINT,
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={"q": query, "num": min(max(count, 1), 20), "gl": "us", "hl": "en"},
        )
        response.raise_for_status()
        return response.json()

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        return parse_serper(self._payload(query, count), count)

    def search_with_features(self, query: str, count: int = 10) -> tuple[list[SerpHit], dict]:
        payload = self._payload(query, count)
        return parse_serper(payload, count), parse_features(self.name, payload)


# SERP features: what a results page shows beyond the organic list. Shape documented in docs/plans/track.md.

FEATURE_LIST_MAX = 10
FEATURE_TEXT_MAX = 300
FEATURE_KEYS = (
    "people_also_ask", "related_searches", "answer_box", "knowledge_panel",
    "top_stories", "videos", "images", "sitelinks",
)


def _txt(value) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())[:FEATURE_TEXT_MAX]


def _link(value) -> str:
    text = _txt(value)
    return text if text.startswith(("http://", "https://")) else ""


def _items(value) -> list:
    return [item for item in value if item] if isinstance(value, list) else []


def _compact(record: dict) -> dict:
    return {key: value for key, value in record.items() if value not in ("", None, [], {})}


def _clip(rows: list) -> list:
    out = [row for row in rows if row]
    return out[:FEATURE_LIST_MAX]


def parse_features(provider: str, payload) -> dict:
    """Normalized SERP features from a raw provider payload. Never raises; unknown shapes give {}."""
    if not isinstance(payload, dict):
        return {}
    try:
        if provider == "serper":
            found = _features_serper(payload)
        elif provider == "serpapi":
            found = _features_serpapi(payload)
        elif provider == "dataforseo":
            found = _features_dataforseo(payload)
        else:
            return {}
    except Exception:  # noqa: BLE001 - extras must never sink a search whose organic results parsed fine
        return {}
    return {key: found[key] for key in FEATURE_KEYS if found.get(key)}


def _sitelinks(position, url, links) -> dict:
    rows = _clip([_compact({"title": _txt(item.get("title")), "url": _link(item.get("link") or item.get("url"))})
                  for item in _items(links) if isinstance(item, dict)])
    return {"position": position, "url": url, "links": rows} if rows and url else {}


def _features_serper(payload: dict) -> dict:
    found: dict = {}
    found["people_also_ask"] = _clip([
        _compact({"question": _txt(item.get("question")), "snippet": _txt(item.get("snippet")), "url": _link(item.get("link"))})
        for item in _items(payload.get("peopleAlsoAsk")) if isinstance(item, dict) and _txt(item.get("question"))
    ])
    found["related_searches"] = _clip([
        _txt(item.get("query") if isinstance(item, dict) else item) for item in _items(payload.get("relatedSearches"))
    ])
    box = payload.get("answerBox")
    if isinstance(box, dict):
        found["answer_box"] = _compact({
            "title": _txt(box.get("title")),
            "snippet": _txt(box.get("snippet") or box.get("answer")),
            "url": _link(box.get("link")),
        })
    graph = payload.get("knowledgeGraph")
    if isinstance(graph, dict):
        found["knowledge_panel"] = _compact({
            "title": _txt(graph.get("title")),
            "type": _txt(graph.get("type")),
            "description": _txt(graph.get("description")),
            "url": _link(graph.get("website") or graph.get("descriptionLink")),
        })
    found["top_stories"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link")), "source": _txt(item.get("source")), "date": _txt(item.get("date"))})
        for item in _items(payload.get("topStories")) if isinstance(item, dict)
    ])
    found["videos"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link")), "source": _txt(item.get("source") or item.get("channel"))})
        for item in _items(payload.get("videos")) if isinstance(item, dict)
    ])
    found["images"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link") or item.get("imageUrl"))})
        for item in _items(payload.get("images")) if isinstance(item, dict)
    ])
    found["sitelinks"] = _clip([
        _sitelinks(int(item.get("position") or index), _link(item.get("link")), item.get("sitelinks"))
        for index, item in enumerate(_items(payload.get("organic")), start=1) if isinstance(item, dict)
    ])
    return found


def _features_serpapi(payload: dict) -> dict:
    found: dict = {}
    found["people_also_ask"] = _clip([
        _compact({"question": _txt(item.get("question")), "snippet": _txt(item.get("snippet")), "url": _link(item.get("link"))})
        for item in _items(payload.get("related_questions")) if isinstance(item, dict) and _txt(item.get("question"))
    ])
    found["related_searches"] = _clip([
        _txt(item.get("query") if isinstance(item, dict) else item) for item in _items(payload.get("related_searches"))
    ])
    box = payload.get("answer_box")
    if isinstance(box, dict):
        found["answer_box"] = _compact({
            "title": _txt(box.get("title")),
            "snippet": _txt(box.get("snippet") or box.get("answer")),
            "url": _link(box.get("link")),
        })
    graph = payload.get("knowledge_graph")
    if isinstance(graph, dict):
        found["knowledge_panel"] = _compact({
            "title": _txt(graph.get("title")),
            "type": _txt(graph.get("type")),
            "description": _txt(graph.get("description")),
            "url": _link(graph.get("website")) or _link((graph.get("source") or {}).get("link") if isinstance(graph.get("source"), dict) else ""),
        })
    found["top_stories"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link")), "source": _txt(item.get("source")), "date": _txt(item.get("date"))})
        for item in _items(payload.get("top_stories")) if isinstance(item, dict)
    ])
    found["videos"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link")), "source": _txt(item.get("platform") or item.get("channel"))})
        for item in _items(payload.get("inline_videos")) if isinstance(item, dict)
    ])
    found["images"] = _clip([
        _compact({"title": _txt(item.get("title")), "url": _link(item.get("link") or item.get("original") or item.get("source"))})
        for item in _items(payload.get("inline_images")) if isinstance(item, dict)
    ])
    rows = []
    for index, item in enumerate(_items(payload.get("organic_results")), start=1):
        if not isinstance(item, dict):
            continue
        links = item.get("sitelinks")
        if isinstance(links, dict):
            links = [*_items(links.get("inline")), *_items(links.get("expanded"))]
        rows.append(_sitelinks(int(item.get("position") or index), _link(item.get("link")), links))
    found["sitelinks"] = _clip(rows)
    return found


def _features_dataforseo(payload: dict) -> dict:
    found: dict = {key: [] for key in ("people_also_ask", "related_searches", "top_stories", "videos", "images", "sitelinks")}
    for task in _items(payload.get("tasks")):
        for result in _items(task.get("result") if isinstance(task, dict) else None):
            for item in _items(result.get("items") if isinstance(result, dict) else None):
                if not isinstance(item, dict):
                    continue
                kind = item.get("type")
                inner = [row for row in _items(item.get("items")) if isinstance(row, dict)]
                if kind == "people_also_ask":
                    for row in inner:
                        expanded = next((part for part in _items(row.get("expanded_element")) if isinstance(part, dict)), {})
                        found["people_also_ask"].append(_compact({
                            "question": _txt(row.get("title")),
                            "snippet": _txt(expanded.get("description")),
                            "url": _link(expanded.get("url")),
                        }) if _txt(row.get("title")) else {})
                elif kind == "related_searches":
                    found["related_searches"].extend(_txt(row) for row in _items(item.get("items")))
                elif kind == "featured_snippet" and "answer_box" not in found:
                    found["answer_box"] = _compact({"title": _txt(item.get("title")), "snippet": _txt(item.get("description")), "url": _link(item.get("url"))})
                elif kind == "knowledge_graph" and "knowledge_panel" not in found:
                    found["knowledge_panel"] = _compact({
                        "title": _txt(item.get("title")), "type": _txt(item.get("sub_title")),
                        "description": _txt(item.get("description")), "url": _link(item.get("url")),
                    })
                elif kind == "top_stories":
                    found["top_stories"].extend(
                        _compact({"title": _txt(row.get("title")), "url": _link(row.get("url")), "source": _txt(row.get("source")), "date": _txt(row.get("date") or row.get("timestamp"))})
                        for row in inner
                    )
                elif kind == "video":
                    found["videos"].extend(
                        _compact({"title": _txt(row.get("title")), "url": _link(row.get("url")), "source": _txt(row.get("source"))}) for row in inner
                    )
                elif kind == "images":
                    found["images"].extend(_compact({"title": _txt(row.get("alt") or row.get("title")), "url": _link(row.get("url") or row.get("image_url"))}) for row in inner)
                elif kind == "organic":
                    found["sitelinks"].append(_sitelinks(int(item.get("rank_group") or 0), _link(item.get("url")), item.get("links")))
    for key in ("people_also_ask", "related_searches", "top_stories", "videos", "images", "sitelinks"):
        found[key] = _clip(found[key])
    return found


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
