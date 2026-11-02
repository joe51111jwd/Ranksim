"""Optional authority proxy. Without a backlink key the feature stays unused."""

from __future__ import annotations

import httpx

from aeo.config import Settings

DATAFORSEO_BACKLINKS = "https://api.dataforseo.com/v3/backlinks/summary/live"


def parse_backlink_rank(payload: dict) -> float | None:
    """Scale DataForSEO rank (0 to 1000) to 0..1. Missing values stay None."""
    for task in payload.get("tasks") or []:
        for result in task.get("result") or []:
            rank = result.get("rank")
            if isinstance(rank, (int, float)):
                return max(0.0, min(1.0, float(rank) / 1000.0))
    return None


class DataForSeoAuthority:
    source = "dataforseo"

    def __init__(self, login: str, password: str, client: httpx.Client | None = None) -> None:
        self.login = login
        self.password = password
        self._owns = client is None
        self.client = client or httpx.Client(timeout=30.0)
        self._cache: dict[str, float | None] = {}

    def close(self) -> None:
        if self._owns:
            self.client.close()

    def score(self, domain: str) -> float | None:
        if domain in self._cache:
            return self._cache[domain]
        response = self.client.post(
            DATAFORSEO_BACKLINKS,
            auth=(self.login, self.password),
            json=[{"target": domain}],
        )
        response.raise_for_status()
        value = parse_backlink_rank(response.json())
        self._cache[domain] = value
        return value


def authority_client(settings: Settings) -> DataForSeoAuthority | None:
    mode = (settings.ranksim_authority or "off").strip().lower()
    if mode in {"", "off", "none"}:
        return None
    if mode != "dataforseo":
        raise ValueError("RANKSIM_AUTHORITY must be off or dataforseo.")
    if not settings.dataforseo_login or not settings.dataforseo_password:
        return None
    return DataForSeoAuthority(settings.dataforseo_login, settings.dataforseo_password)
