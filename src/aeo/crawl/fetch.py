"""Polite raw-HTML crawler. JavaScript is not executed here."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from aeo.crawl import guard

from aeo.crawl.robots import crawl_delay_seconds, parse_robots
from aeo.crawl.sitemap import parse_sitemap
from aeo.crawl.urls import assert_safe_url, normalize_url, same_site
from aeo.errors import ProviderConfigError, UnsafeURL

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
# Whole-response budget. httpx timeouts apply per read, so a server that drips one byte at a
# time could otherwise hold a fetch open far longer than the configured timeout.
TOTAL_DEADLINE_SECONDS = 30.0
# A host that answers 429 or 503 gets no request until its Retry-After (or a doubling backoff from
# BACKOFF_BASE_SECONDS when it gives none) has passed. A wait longer than MAX_BACKOFF_WAIT is not
# slept through: the fetch is reported as deferred so a crawl stays bounded.
BACKOFF_BASE_SECONDS = 2.0
MAX_BACKOFF_WAIT = 120.0
MAX_RETRY_AFTER = 3600.0
READABLE_TYPES = ("html", "xml", "text/plain")


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


class RedirectProblem(Exception):
    """A redirect chain that ends without a page: a loop, too many hops, or no Location."""


class DeadlineExceeded(Exception):
    """No complete response before the request deadline (for example, headers dripped slowly)."""


class HostBackoff(Exception):
    """The host asked crawlers to wait longer than this crawl will wait."""


# yourdomain.com is the example in the refusal message below, so pasting it verbatim is refused too.
PLACEHOLDER_DOMAINS = frozenset({"example.com", "example.org", "example.net", "localhost", "yourdomain.com"})
PLACEHOLDER_SUFFIXES = (".example", ".test", ".invalid", ".localhost", ".example.com", ".example.org", ".example.net")


def user_agent_problem(user_agent: str) -> str | None:
    """Why this user agent may not crawl third-party sites, or None.

    Site owners need to know who is crawling and how to reach them, so the user agent must name the
    crawler and give a contact URL or email that is not a placeholder (example.com and the like).
    """
    import re

    text = (user_agent or "").strip()
    if not text:
        return "the crawler user agent is empty"
    # Punctuation after a URL ("https://example.com; bot@...") is not part of its host.
    hosts = [urlsplit(url).hostname or "" for url in re.findall(r"https?://[^\s)\]<>\"',;]+", text)]
    hosts += [domain for domain in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text)]
    hosts = [host.lower().rstrip(".") for host in hosts]
    real = [
        host
        for host in hosts
        if re.fullmatch(r"[\w-]+(?:\.[\w-]+)+", host)
        and host not in PLACEHOLDER_DOMAINS
        and not host.endswith(PLACEHOLDER_SUFFIXES)
    ]
    if real:
        return None
    if hosts:
        return f"the crawler user agent's contact is a placeholder ({hosts[0]})"
    return "the crawler user agent gives no contact URL or email"


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds from a Retry-After header (delay-seconds or an HTTP date), or None."""
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return min(float(text), MAX_RETRY_AFTER)
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if moment is None:
        return None
    from datetime import datetime, timezone

    current = datetime.now(timezone.utc).timestamp() if now is None else now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return min(max(0.0, moment.timestamp() - current), MAX_RETRY_AFTER)


ROBOTS_UNREACHABLE = object()


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
        # Checked at the first fetch of a real host, so a crawler that only meets unresolvable or
        # synthetic hosts (mock runs) still works.
        self._user_agent_problem = None if allow_private else user_agent_problem(user_agent)
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
        self._backoff: dict[str, tuple[float, int]] = {}  # origin -> (monotonic until, consecutive 429/503)
        # A client passed in (tests, custom transports) is used as given.
        self._guards = guard.install(self.client, pin=not allow_private) if self._owns_client else []

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
        self._require_contact()

        allowed, robots_status = self._allowed(current)
        if not allowed:
            error = (
                "robots.txt did not answer (5xx, 429 or network error), so the site was not crawled"
                if robots_status == "unreachable"
                else "Blocked by robots.txt"
            )
            return Fetched(
                url=current,
                final_url=current,
                blocked=True,
                robots_status=robots_status,
                error=error,
            )

        try:
            self._polite_wait(current)
        except HostBackoff as exc:
            return Fetched(url=current, final_url=current, error=str(exc), robots_status=robots_status)
        try:
            response, final_url = self._get_following_redirects(current, polite=True)
        except UnsafeURL:
            raise
        except HostBackoff as exc:
            return Fetched(url=current, final_url=current, error=str(exc), robots_status=robots_status)
        except httpx.ConnectError as exc:
            if isinstance(exc.__cause__, guard.UnsafeConnect):
                raise UnsafeURL(str(exc.__cause__)) from None
            return Fetched(url=current, final_url=current, error=f"Fetch failed: {exc}", robots_status=robots_status)
        except DeadlineExceeded as exc:
            return Fetched(url=current, final_url=current, error=str(exc), robots_status=robots_status)
        except RedirectProblem as exc:
            return Fetched(url=current, final_url=current, error=str(exc), robots_status=robots_status)
        except (ValueError, httpx.InvalidURL) as exc:
            # A redirect to file:, mailto: or a malformed Location must not crash a whole crawl.
            return Fetched(url=current, final_url=current, error=f"Unsupported redirect target: {exc}", robots_status=robots_status)
        except httpx.HTTPError as exc:
            return Fetched(url=current, final_url=current, error=f"Fetch failed: {exc}", robots_status=robots_status)

        content_type = response.headers.get("content-type", "")
        if response.extensions.get("truncated") == "deadline":
            # A page cut off mid-download is a fragment; storing it would score part of a page.
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                error=f"Download took longer than {TOTAL_DEADLINE_SECONDS:.0f} s; the partial page was not used",
                robots_status=robots_status,
                content_type=content_type,
            )
        body = self._read_capped(response)
        if response.status_code in (429, 503):
            wait = self._note_rate_limit(final_url, response.headers.get("retry-after"))
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                error=f"HTTP {response.status_code}: the host asked crawlers to slow down; no request goes to it for {wait:.0f} s",
                robots_status=robots_status,
                content_type=content_type,
            )
        if response.status_code < 400:
            self._backoff.pop(self._origin(final_url), None)
        if response.status_code >= 400:
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                html="",
                error=f"HTTP {response.status_code}",
                robots_status=robots_status,
                content_type=content_type,
            )
        lowered = content_type.lower()
        if content_type and "html" not in lowered and "xml" not in lowered and "text/plain" not in lowered:
            return Fetched(
                url=current,
                final_url=final_url,
                status_code=response.status_code,
                error=f"Unsupported content type {content_type.split(';')[0]}",
                robots_status=robots_status,
                content_type=content_type,
            )
        return Fetched(
            url=current,
            final_url=final_url,
            status_code=response.status_code,
            html=body,
            robots_status=robots_status,
            content_type=content_type,
        )

    def crawl(self, start_url: str, *, sitemap_url: str | None, max_pages: int) -> CrawlBatch:
        batch = CrawlBatch()
        start = normalize_url(start_url)
        if self._user_agent_problem:
            try:
                assert_safe_url(start, allow_private=self.allow_private, resolve=True)
            except (UnsafeURL, ValueError):
                pass  # an unusable start URL fails below with its own message
            else:
                self._require_contact()
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

    def _allowed(self, url: str) -> tuple[bool, str]:
        origin = self._origin(url)
        parser = self._robots_for(origin)
        if parser is ROBOTS_UNREACHABLE:
            return False, "unreachable"
        if parser is None:
            return True, "unavailable"
        allowed = parser.can_fetch(self.user_agent, url)
        return allowed, "allowed" if allowed else "disallowed"

    def _robots_for(self, origin: str):
        """RFC 9309: 4xx (unavailable) allows everything; 5xx, 429 or a network failure
        (unreachable) means assume complete disallow. A file not read in full before the deadline
        counts as unreachable. The result is cached for this crawler."""
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            safe = assert_safe_url(robots_url, allow_private=self.allow_private)
            response, _final = self._get_following_redirects(safe, any_type=True)
        except RedirectProblem:
            self._robots[origin] = None  # redirect loop: treat as unavailable
            return None
        except (httpx.HTTPError, UnsafeURL, ValueError, DeadlineExceeded):
            self._robots[origin] = ROBOTS_UNREACHABLE
            return ROBOTS_UNREACHABLE
        if response.status_code == 429 or response.status_code >= 500:
            self._robots[origin] = ROBOTS_UNREACHABLE
            return ROBOTS_UNREACHABLE
        if response.extensions.get("truncated") == "deadline":
            # Rules after the cut are unknown, so a partial file is a network failure, not a file.
            self._robots[origin] = ROBOTS_UNREACHABLE
            return ROBOTS_UNREACHABLE
        if response.status_code >= 400:
            parser = parse_robots("")
            self._robots[origin] = parser
            return parser
        parser = parse_robots(self._read_capped(response))
        self._robots[origin] = parser
        return parser

    def _polite_wait(self, url: str) -> None:
        origin = self._origin(url)
        delay = self.delay_seconds
        parser = self._robots.get(origin)
        if parser is not None and parser is not ROBOTS_UNREACHABLE:
            robots_delay = crawl_delay_seconds(parser, self.user_agent)
            if robots_delay is not None:
                delay = max(delay, robots_delay)
        now = time.monotonic()
        wait = delay - (now - self._last_fetch.get(origin, 0.0))
        until, _count = self._backoff.get(origin, (0.0, 0))
        wait = max(wait, until - now)
        if wait > MAX_BACKOFF_WAIT:
            raise HostBackoff(f"The host asked crawlers to wait {wait:.0f} s (HTTP 429/503); not fetched now")
        if wait > 0:
            time.sleep(wait)
        self._last_fetch[origin] = time.monotonic()

    def _note_rate_limit(self, url: str, retry_after: str | None) -> float:
        """Start or extend this host's backoff. Returns the wait in seconds."""
        origin = self._origin(url)
        _until, count = self._backoff.get(origin, (0.0, 0))
        count += 1
        wait = retry_after_seconds(retry_after)
        if wait is None:
            wait = min(MAX_RETRY_AFTER, BACKOFF_BASE_SECONDS * 2 ** (count - 1))
        self._backoff[origin] = (time.monotonic() + wait, count)
        return wait

    def _get_following_redirects(
        self, url: str, *, any_type: bool = False, polite: bool = False
    ) -> tuple[httpx.Response, str]:
        """``polite``: the caller waited for the first URL's origin; a hop to another origin waits
        for that origin too, so a host in 429/503 backoff gets no request through a redirect."""
        current = url
        previous = self._origin(url)
        for _ in range(MAX_REDIRECTS + 1):
            current = assert_safe_url(current, allow_private=self.allow_private, keep_trailing_slash=True)
            if polite and self._origin(current) != previous:
                self._polite_wait(current)
                previous = self._origin(current)
            response = self._get_capped(current, any_type=any_type)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response, current
            location = response.headers.get("location")
            if not location:
                raise RedirectProblem(f"HTTP {response.status_code} redirect without a Location header")
            current = urljoin(current, location)
        raise RedirectProblem(f"Too many redirects (more than {MAX_REDIRECTS})")

    def _get_capped(self, url: str, *, any_type: bool = False) -> httpx.Response:
        """Stream a GET and stop at MAX_BYTES or the total deadline, whichever comes first.

        Bodies of errors, redirects and types we never read (images, PDFs, video) are not
        downloaded at all. Ported from the unmerged claude/bold-einstein-oi5qoj branch, plus the
        overall deadline. Returns a fully read, capped response.
        """
        request = self.client.build_request(
            "GET",
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        clock = getattr(self, "_clock", time.monotonic)
        started = clock()
        for item in self._guards:
            item.clock = clock
            item.deadline = started + TOTAL_DEADLINE_SECONDS
        try:
            response = self.client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            self._clear_deadline()
            if clock() - started >= TOTAL_DEADLINE_SECONDS:
                raise DeadlineExceeded(
                    f"No complete response headers within {TOTAL_DEADLINE_SECONDS:.0f} s; the page was not used"
                ) from exc
            raise
        except BaseException:
            self._clear_deadline()
            raise
        chunks: list[bytes] = []
        truncated = ""
        try:
            content_type = response.headers.get("content-type", "").lower()
            # robots.txt is read whatever its declared type: hosts often serve it as octet-stream.
            readable = response.status_code < 300 and (
                any_type or not content_type or any(kind in content_type for kind in READABLE_TYPES)
            )
            if readable:
                size = 0
                try:
                    for chunk in response.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= MAX_BYTES:
                            truncated = "size"
                            break
                        if clock() - started > TOTAL_DEADLINE_SECONDS:
                            truncated = "deadline"
                            break
                except httpx.TimeoutException:
                    if clock() - started < TOTAL_DEADLINE_SECONDS:
                        raise
                    truncated = "deadline"
        finally:
            response.close()
            self._clear_deadline()
        headers = [
            (name, value)
            for name, value in response.headers.items()
            if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
        ]
        capped = httpx.Response(
            status_code=response.status_code,
            headers=headers,
            content=b"".join(chunks)[:MAX_BYTES],
            request=request,
        )
        capped.extensions["truncated"] = truncated
        return capped

    def _require_contact(self) -> None:
        if self._user_agent_problem:
            raise ProviderConfigError(
                f"Refusing to crawl third-party sites: {self._user_agent_problem}. Set CRAWL_USER_AGENT to your crawler's "
                "name and a contact site owners can reach, for example: "
                "RanksimBot/0.1 (+https://yourdomain.com/bot; you@yourdomain.com)."
            )

    def _clear_deadline(self) -> None:
        for item in self._guards:
            item.deadline = None

    def _read_capped(self, response: httpx.Response) -> str:
        raw = response.content[:MAX_BYTES]
        encoding = response.encoding or "utf-8"
        return raw.decode(encoding, errors="replace")

    @staticmethod
    def _origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"
