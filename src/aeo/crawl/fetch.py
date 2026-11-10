"""Polite raw-HTML crawler. JavaScript is not executed here."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from aeo.crawl.robots import crawl_delay_seconds, parse_robots
from aeo.crawl.sitemap import parse_sitemap
from aeo.crawl.urls import assert_safe_url, normalize_url, same_site
from aeo.errors import UnsafeURL

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5


@dataclass
class Fetched:
    url: str
    final_url: str
    status_code: int = 0
    html: str = ""
    error: str | None = None
    blocked: bool = False
    robots_status: str = "unknown"
    content_type: str = ""


@dataclass
class CrawlBatch:
    pages: list[Fetched] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Crawler:
    def __init__(
        self,
        *,
        user_agent: str,
        delay_seconds: float = 0.25,
        allow_private: bool = False,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.allow_private = allow_private
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._robots: dict[str, object] = {}
        self._last_fetch: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def fetch(self, url: str) -> Fetched:
        try:
            current = assert_safe_url(url, allow_private=self.allow_private, resolve=True)
        except UnsafeURL as exc:
            if "Could not resolve" in str(exc):
                return Fetched(url=url, final_url=url, error=str(exc), robots_status="skipped")
            raise
        except ValueError as exc:
            return Fetched(url=url, final_url=url, error=str(exc), robots_status="skipped")

        allowed, robots_status = self._allowed(current)
        if not allowed:
            return Fetched(
                url=current,
                final_url=current,
                blocked=True,
                robots_status=robots_status,
                error="Blocked by robots.txt",
            )

        self._polite_wait(current)
        try:
            response, final_url = self._get_following_redirects(current)
        except UnsafeURL:
            raise
        except httpx.HTTPError as exc:
            return Fetched(url=current, final_url=current, error=f"Fetch failed: {exc}", robots_status="allowed")

        content_type = response.headers.get("content-type", "")
        body = self._read_capped(response)
        if response.status_code >= 400:
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                html="",
                error=f"HTTP {response.status_code}",
                robots_status="allowed",
                content_type=content_type,
            )
        if content_type and "html" not in content_type and "xml" not in content_type and "text/plain" not in content_type:
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                error=f"Unsupported content type {content_type.split(';')[0]}",
                robots_status="allowed",
                content_type=content_type,
            )
        return Fetched(
            url=current,
            final_url=final_url,
            status_code=response.status_code,
            html=body,
            robots_status="allowed",
            content_type=content_type,
        )

    def crawl(self, start_url: str, *, sitemap_url: str | None, max_pages: int) -> CrawlBatch:
        batch = CrawlBatch()
        start = normalize_url(start_url)
        seeds = self._seed_urls(start, sitemap_url, max_pages, batch.warnings)
        queue = list(seeds)
        seen: set[str] = set()
        while queue and len(batch.pages) < max_pages:
            candidate = queue.pop(0)
            try:
                candidate = normalize_url(candidate)
            except ValueError:
                continue
            if candidate in seen or not same_site(candidate, start):
                continue
            seen.add(candidate)
            try:
                fetched = self.fetch(candidate)
            except UnsafeURL as exc:
                batch.warnings.append(str(exc))
                continue
            batch.pages.append(fetched)
            if fetched.blocked or fetched.error or not fetched.html:
                continue
            from aeo.crawl.extract import extract_html

            extracted = extract_html(fetched.html, fetched.final_url or candidate)
            for link in extracted.links:
                if link not in seen and same_site(link, start):
                    queue.append(link)
        return batch

    def _seed_urls(self, start: str, sitemap_url: str | None, max_pages: int, warnings: list[str]) -> list[str]:
        urls: list[str] = []
        sitemap_targets = []
        if sitemap_url:
            sitemap_targets.append(sitemap_url)
        else:
            origin = urlsplit(start)
            sitemap_targets.append(f"{origin.scheme}://{origin.netloc}/sitemap.xml")
        for target in sitemap_targets:
            try:
                urls.extend(self._read_sitemap(target, max_pages, depth=0))
            except Exception as exc:  # noqa: BLE001 — a bad sitemap should not abort the crawl
                if sitemap_url:
                    warnings.append(f"Sitemap was not used: {exc}")
        if start not in urls:
            urls.insert(0, start)
        return urls

    def _read_sitemap(self, url: str, max_pages: int, *, depth: int) -> list[str]:
        fetched = self.fetch(url)
        if fetched.blocked or fetched.error or not fetched.html:
            raise RuntimeError(fetched.error or "empty sitemap response")
        kind, locs = parse_sitemap(fetched.html)
        if kind == "urlset":
            return locs[:max_pages]
        if depth >= 1:
            return []
        pages: list[str] = []
        for child in locs:
            if len(pages) >= max_pages:
                break
            try:
                pages.extend(self._read_sitemap(child, max_pages - len(pages), depth=depth + 1))
            except Exception:
                continue
        return pages[:max_pages]

    def allowed_for(self, url: str, user_agent: str | None = None) -> tuple[bool, str]:
        """Whether this user agent may fetch the URL. A missing robots file is allowed."""
        origin = self._origin(url)
        parser = self._robots_for(origin)
        if parser is None:
            return True, "unavailable"
        agent = user_agent or self.user_agent
        allowed = parser.can_fetch(agent, url)
        return allowed, "allowed" if allowed else "disallowed"

    def _allowed(self, url: str) -> tuple[bool, str]:
        return self.allowed_for(url, self.user_agent)

    def _robots_for(self, origin: str):
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            safe = assert_safe_url(robots_url, allow_private=self.allow_private)
            response, _final = self._get_following_redirects(safe)
        except (httpx.HTTPError, UnsafeURL, ValueError):
            self._robots[origin] = None
            return None
        if response.status_code == 404:
            parser = parse_robots("")
            self._robots[origin] = parser
            return parser
        if response.status_code >= 400:
            self._robots[origin] = None
            return None
        parser = parse_robots(self._read_capped(response))
        self._robots[origin] = parser
        return parser

    def _polite_wait(self, url: str) -> None:
        origin = self._origin(url)
        delay = self.delay_seconds
        parser = self._robots.get(origin)
        if parser is not None:
            robots_delay = crawl_delay_seconds(parser, self.user_agent)
            if robots_delay is not None:
                delay = max(delay, robots_delay)
        elapsed = time.monotonic() - self._last_fetch.get(origin, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_fetch[origin] = time.monotonic()

    def _get_following_redirects(self, url: str) -> tuple[httpx.Response, str]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            current = assert_safe_url(current, allow_private=self.allow_private, keep_trailing_slash=True)
            response = self._get_capped(current)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, current
            location = response.headers.get("location")
            if not location:
                return response, current
            current = urljoin(current, location)
        return response, current

    def _get_capped(self, url: str) -> httpx.Response:
        """GET that stops reading at MAX_BYTES, so a huge body never sits in memory.

        The body of an error, a redirect or a type we do not read (images, PDFs, video) is not read at all.
        """
        request = self.client.build_request(
            "GET",
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        response = self.client.send(request, stream=True)
        chunks: list[bytes] = []
        try:
            content_type = response.headers.get("content-type", "")
            readable = response.status_code < 300 and (
                not content_type or any(kind in content_type for kind in ("html", "xml", "text/plain"))
            )
            if readable:
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES:
                        break
        finally:
            response.close()
        headers = [(name, value) for name, value in response.headers.items() if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}]
        return httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=b"".join(chunks)[:MAX_BYTES],
            request=request,
        )

    def _read_capped(self, response: httpx.Response) -> str:
        raw = response.content[:MAX_BYTES]
        encoding = response.encoding or "utf-8"
        return raw.decode(encoding, errors="replace")

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"
