"""Polite HTTP for public keyword pages. Retries, a per-host delay, and a named user agent."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

import httpx

USER_AGENT = "RanksimKeywordBot/1.0 (+https://github.com/joe51111jwd/Ranksim)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class PoliteHttp:
    def __init__(self, client: httpx.Client, *, delay: float = 1.0, retries: int = 3) -> None:
        self.client = client
        self.delay = max(0.0, float(delay))
        self.retries = max(1, int(retries))
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).netloc.lower()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
        extra = kwargs.pop("headers", None) or {}
        headers.update(extra)
        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(self.retries):
            self._wait(host)
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                self._backoff(attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt + 1 < self.retries:
                self._backoff(attempt)
                continue
            return response
        if response is not None:
            return response
        assert last_error is not None
        raise last_error

    def _wait(self, host: str) -> None:
        if self.delay <= 0 or not host:
            return
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            wait = self.delay - (now - last) if last else 0.0
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._last[host] = now

    def _backoff(self, attempt: int) -> None:
        pause = self.delay if self.delay > 0 else 0.0
        if pause <= 0:
            return
        time.sleep(pause * (2**attempt))


def scrub(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        token = (secret or "").strip()
        if len(token) >= 4:
            cleaned = cleaned.replace(token, "[redacted]")
    return cleaned
