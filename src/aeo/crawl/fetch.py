"""Polite raw-HTML crawler. JavaScript is not executed here.

Safety and robustness, in the order a fetch meets them:

* ``assert_safe_url`` refuses non-http schemes, credentials, local names, numeric host tricks and any host
  that resolves to a non-public address. Unusual ports are allowed on purpose (public sites use them); the
  address checks are what keep the crawler off internal services.
* robots.txt is read once per origin and checked for every page.
* The default client connects through ``aeo.crawl.guard.GuardedBackend``, which resolves again at connect
  time and connects only to an address it vetted, so DNS rebinding cannot reach 169.254.169.254. Behind an
  environment proxy the proxy resolves instead (see that module's note). An injected ``client`` is used as is.
* Each redirect hop is re-validated, recorded on ``Fetched.redirects``, checked for loops and capped.
* Separate connect and read timeouts, plus a wall clock cap on the whole fetch (``timeout``).
* Connect errors, read timeouts, 429, 502, 503 and 504 are retried with capped exponential backoff and
  jitter; ``Retry-After`` is honoured up to ``RETRY_AFTER_CAP``. Other 4xx and refused URLs never retry.
* Bodies are capped at ``MAX_BYTES``; gzip sitemaps are inflated with a cap.
"""

from __future__ import annotations

import email.utils
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from aeo.crawl.robots import crawl_delay_seconds, parse_robots
from aeo.crawl.sitemap import gunzip_capped, is_gzip, parse_sitemap
from aeo.crawl.thin import script_drawn
from aeo.crawl.urls import UnresolvableHost, assert_safe_url, normalize_url, same_site
from aeo.errors import UnsafeURL

DEFAULT_USER_AGENT = "RanksimBot/1.0 (+https://ranksim.vercel.app/bot; SEO rank simulator)"

MAX_BYTES = 2_000_000
MAX_GUNZIP_BYTES = 10_000_000
MAX_REDIRECTS = 5
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 15.0

MAX_RETRIES = 2
RETRY_STATUSES = frozenset({429, 502, 503, 504})
BACKOFF_BASE = 0.5
BACKOFF_CAP = 4.0
RETRY_AFTER_CAP = 10.0

MAX_SITEMAP_DEPTH = 2  # top sitemap index -> child index -> urlset; deeper indexes are ignored
MAX_SITEMAP_CHILDREN = 50  # child sitemaps read per crawl, across every index
MAX_SITEMAP_URLS = 50_000
MAX_ROBOTS_SITEMAPS = 5

_READABLE = ("html", "xml", "text/plain")
_GZIP_TYPES = ("application/gzip", "application/x-gzip")


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
    redirects: list[str] = field(default_factory=list)  # each hop followed, in order; empty without redirects
    script_drawn: bool = False  # raw HTML is nearly empty because scripts draw the page (see aeo.crawl.thin)


@dataclass
class CrawlBatch:
    pages: list[Fetched] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _FetchProblem(Exception):
    """A fetch that ended without a usable response: redirect loop, too many redirects, wall clock."""

    def __init__(self, message: str, chain: list[str] | None = None) -> None:
        super().__init__(message)
        self.chain = list(chain or [])


def _readable(content_type: str) -> bool:
    content_type = content_type.lower()
    return not content_type or any(kind in content_type for kind in _READABLE + _GZIP_TYPES)


class Crawler:
    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        delay_seconds: float = 0.25,
        allow_private: bool = False,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        """``timeout`` caps one whole fetch (redirects and retries included) in wall clock seconds.

        ``connect_timeout`` and ``read_timeout`` bound each socket connect and each read, never above
        ``timeout``. ``sleep``, ``clock`` and ``rng`` are injectable for tests.
        """
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.allow_private = allow_private
        self.timeout = float(timeout)
        self.connect_timeout = min(float(connect_timeout), self.timeout)
        self.read_timeout = min(float(read_timeout), self.timeout)
        self.max_retries = max(0, int(max_retries))
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._owns_client = client is None
        if client is None:
            transport = None
            if not allow_private:
                from aeo.crawl.guard import guarded_transport

                # Direct connections are vetted at connect time. Requests httpx routes through an environment
                # proxy use httpx's own proxy transport, and the proxy resolves the host (see aeo.crawl.guard).
                transport = guarded_transport()
            client = httpx.Client(
                timeout=self._timeouts(self.timeout),
                follow_redirects=False,
                headers={"User-Agent": user_agent},
                transport=transport,
            )
        self.client = client
        self._robots: dict[str, object] = {}
        self._last_fetch: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    # -- single page ------------------------------------------------------------------------------------------

    def fetch(self, url: str) -> Fetched:
        try:
            current = assert_safe_url(url, allow_private=self.allow_private, resolve=True)
        except UnresolvableHost as exc:
            return Fetched(url=url, final_url=url, error=str(exc), robots_status="skipped")
        except UnsafeURL:
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
        deadline = self._clock() + self.timeout
        try:
            response, final_url, chain = self._get_following_redirects(current, deadline=deadline)
        except UnsafeURL:
            raise
        except _FetchProblem as exc:
            final = exc.chain[-1] if exc.chain else current
            return Fetched(url=current, final_url=final, error=str(exc), robots_status="allowed", redirects=exc.chain)
        except httpx.HTTPError as exc:
            return Fetched(url=current, final_url=current, error=f"Fetch failed: {exc}", robots_status="allowed")

        content_type = response.headers.get("content-type", "")
        gzipped = any(kind in content_type.lower() for kind in _GZIP_TYPES) or is_gzip(response.content[:2])
        if response.status_code >= 400:
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                html="",
                error=f"HTTP {response.status_code}",
                robots_status="allowed",
                content_type=content_type,
                redirects=chain,
            )
        if not _readable(content_type):
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                error=f"Unsupported content type {content_type.split(';')[0]}",
                robots_status="allowed",
                content_type=content_type,
                redirects=chain,
            )
        if gzipped:
            try:
                body = gunzip_capped(response.content, limit=MAX_GUNZIP_BYTES).decode("utf-8", errors="replace")
            except ValueError as exc:
                return Fetched(
                    url=current,
                    final_url=final_url,
                    status_code=response.status_code,
                    error=str(exc),
                    robots_status="allowed",
                    content_type=content_type,
                    redirects=chain,
                )
        else:
            body = self._read_capped(response)
        looks_html = "html" in content_type.lower() or (not content_type and "<html" in body[:4096].lower())
        return Fetched(
            url=current,
            final_url=final_url,
            status_code=response.status_code,
            html=body,
            robots_status="allowed",
            content_type=content_type,
            redirects=chain,
            script_drawn=bool(looks_html and not gzipped and script_drawn(body)),
        )

    # -- site crawl -------------------------------------------------------------------------------------------

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
        if sitemap_url:
            sitemap_targets = [sitemap_url]
        else:
            sitemap_targets = self.robots_sitemaps(start) or [f"{self._origin(start)}/sitemap.xml"]
        limit = min(max_pages, MAX_SITEMAP_URLS)
        budget = {"children": 0}
        for target in sitemap_targets:
            if len(urls) >= limit:
                break
            try:
                urls.extend(self._read_sitemap(target, limit - len(urls), depth=0, budget=budget))
            except Exception as exc:  # noqa: BLE001 — a bad sitemap should not abort the crawl
                if sitemap_url:
                    warnings.append(f"Sitemap was not used: {exc}")
        if start not in urls:
            urls.insert(0, start)
        return urls

    def robots_sitemaps(self, url: str) -> list[str]:
        """``Sitemap:`` lines from the robots.txt of ``url``'s origin, first ``MAX_ROBOTS_SITEMAPS``, deduped."""
        parser = self._robots_for(self._origin(url))
        if parser is None:
            return []
        listed = parser.site_maps() or []
        return list(dict.fromkeys(item.strip() for item in listed if item.strip()))[:MAX_ROBOTS_SITEMAPS]

    def _read_sitemap(self, url: str, max_pages: int, *, depth: int, budget: dict | None = None) -> list[str]:
        """Page URLs from a sitemap or sitemap index, gzip or plain, nested up to ``MAX_SITEMAP_DEPTH``."""
        budget = budget if budget is not None else {"children": 0}
        fetched = self.fetch(url)
        if fetched.blocked or fetched.error or not fetched.html:
            raise RuntimeError(fetched.error or "empty sitemap response")
        kind, locs = parse_sitemap(fetched.html)
        if kind == "urlset":
            return locs[:max_pages]
        if depth >= MAX_SITEMAP_DEPTH:
            return []
        pages: list[str] = []
        for child in locs:
            if len(pages) >= max_pages or budget["children"] >= MAX_SITEMAP_CHILDREN:
                break
            budget["children"] += 1
            try:
                pages.extend(self._read_sitemap(child, max_pages - len(pages), depth=depth + 1, budget=budget))
            except Exception:  # noqa: BLE001, S112 - one bad child sitemap does not end the others
                continue
        return pages[:max_pages]

    # -- robots -----------------------------------------------------------------------------------------------

    def _allowed(self, url: str) -> tuple[bool, str]:
        origin = self._origin(url)
        parser = self._robots_for(origin)
        if parser is None:
            return True, "unavailable"
        allowed = parser.can_fetch(self.user_agent, url)
        return allowed, "allowed" if allowed else "disallowed"

    def _robots_for(self, origin: str):
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            safe = assert_safe_url(robots_url, allow_private=self.allow_private)
            response, _final, _chain = self._get_following_redirects(safe, deadline=self._clock() + self.timeout)
        except (httpx.HTTPError, UnsafeURL, ValueError, _FetchProblem):
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
        elapsed = self._clock() - self._last_fetch.get(origin, 0.0)
        if elapsed < delay:
            self._sleep(delay - elapsed)
        self._last_fetch[origin] = self._clock()

    # -- transport --------------------------------------------------------------------------------------------

    def _get_following_redirects(self, url: str, *, deadline: float | None = None) -> tuple[httpx.Response, str, list[str]]:
        """Follow redirects by hand so every hop is re-validated. Returns (response, final URL, hops)."""
        if deadline is None:
            deadline = self._clock() + self.timeout
        current = assert_safe_url(url, allow_private=self.allow_private, keep_trailing_slash=True)
        seen = {current}
        chain: list[str] = []
        while True:
            response = self._send_with_retries(current, deadline, chain)
            if response.status_code not in REDIRECT_STATUSES:
                return response, current, chain
            location = response.headers.get("location")
            if not location:
                return response, current, chain
            if len(chain) >= MAX_REDIRECTS:
                raise _FetchProblem(f"Too many redirects (more than {MAX_REDIRECTS})", chain)
            try:
                target = assert_safe_url(urljoin(current, location.strip()), allow_private=self.allow_private, keep_trailing_slash=True)
            except UnresolvableHost as exc:
                raise _FetchProblem(f"Redirect target failed: {exc}", chain + [urljoin(current, location.strip())]) from exc
            except UnsafeURL:
                raise
            except ValueError as exc:
                raise _FetchProblem(f"Redirect to an unusable URL: {exc}", chain) from exc
            chain.append(target)
            if target in seen:
                raise _FetchProblem(f"Redirect loop at {target}", chain)
            seen.add(target)
            current = target

    def _send_with_retries(self, url: str, deadline: float, chain: list[str] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            self._check_deadline(deadline, chain)
            try:
                response = self._get_capped(url, deadline=deadline, chain=chain)
            except UnsafeURL:
                raise  # a refused target is final
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                if attempt >= self.max_retries:
                    raise
                wait = self._backoff(attempt)
                if self._clock() + wait >= deadline:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                    return response
                wait = self._retry_wait(response, attempt)
                if self._clock() + wait >= deadline:
                    return response
            self._sleep(wait)
            attempt += 1

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff with jitter: between half and all of base * 2**attempt, capped."""
        ceiling = min(BACKOFF_CAP, BACKOFF_BASE * (2**attempt))
        return self._rng.uniform(ceiling / 2, ceiling)

    def _retry_wait(self, response: httpx.Response, attempt: int) -> float:
        header = (response.headers.get("retry-after") or "").strip()
        if header:
            seconds: float | None = None
            try:
                seconds = float(header)
            except ValueError:
                try:
                    when = email.utils.parsedate_to_datetime(header)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    seconds = (when - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, IndexError, OverflowError):
                    seconds = None
            if seconds is not None and not math.isnan(seconds):
                return max(0.0, min(seconds, RETRY_AFTER_CAP))
        return self._backoff(attempt)

    def _check_deadline(self, deadline: float, chain: list[str] | None = None) -> None:
        if self._clock() >= deadline:
            raise _FetchProblem(f"Fetch timed out after {self.timeout:g} s", chain)

    def _timeouts(self, remaining: float) -> httpx.Timeout:
        remaining = max(0.01, remaining)
        return httpx.Timeout(
            connect=min(self.connect_timeout, remaining),
            read=min(self.read_timeout, remaining),
            write=min(self.read_timeout, remaining),
            pool=min(self.connect_timeout, remaining),
        )

    def _get_capped(self, url: str, *, deadline: float | None = None, chain: list[str] | None = None) -> httpx.Response:
        """GET that stops reading at MAX_BYTES, so a huge body never sits in memory.

        The body of an error, a redirect or a type we do not read (images, PDFs, video) is not read at all.
        Reading stops with a timeout error once the fetch's wall clock deadline passes.
        """
        extra = {}
        if self._owns_client and deadline is not None:
            extra["timeout"] = self._timeouts(deadline - self._clock())
        request = self.client.build_request(
            "GET",
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            **extra,
        )
        response = self.client.send(request, stream=True)
        chunks: list[bytes] = []
        try:
            content_type = response.headers.get("content-type", "")
            if response.status_code < 300 and _readable(content_type):
                size = 0
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= MAX_BYTES:
                        break
                    if deadline is not None and self._clock() >= deadline:
                        raise _FetchProblem(f"Fetch timed out after {self.timeout:g} s", chain)
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
