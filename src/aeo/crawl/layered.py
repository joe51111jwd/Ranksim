"""Fetch a page with fallbacks: HTTP, then a headless browser, then Serper scrape.

Serper scrape stays off unless SERPER_API_KEY is set and the credit cap is above zero.
The cap is a hard ceiling. A call that might cost more than the credits left is not sent.
"""

from __future__ import annotations

import html as html_lib
import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from aeo.crawl.extract import extract_html
from aeo.crawl.fetch import Crawler, Fetched
from aeo.crawl.urls import assert_safe_url
from aeo.errors import UnsafeURL

SERPER_SCRAPE_URL = "https://scrape.serper.dev"
# Serper documents a typical scrape at 2 credits and a hard page at up to 10.
SERPER_SCRAPE_WORST_CREDITS = 10
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_VIEWPORT = {"width": 1366, "height": 768}
CHALLENGE_MARKERS = (
    "captcha",
    "cf-challenge",
    "cf-browser-verification",
    "just a moment",
    "challenge-platform",
)
MIN_WORDS = 8


@dataclass
class Attempt:
    method: str
    ok: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"method": self.method, "ok": self.ok, "reason": self.reason}


@dataclass
class BrowserRender:
    html: str = ""
    visible_text: str = ""
    title: str = ""
    error: str | None = None


@dataclass
class LayeredPage:
    url: str
    html: str = ""
    method: str | None = None
    visible_text: str = ""
    title: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    schema_types: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    fetch_ms: float | None = None

    def attempt_dicts(self) -> list[dict]:
        return [item.as_dict() for item in self.attempts]


class CreditCap:
    """Hard ceiling on Serper scrape credits. Default 0 means the call is off."""

    def __init__(self, cap: int) -> None:
        self.cap = max(0, int(cap))
        self.spent = 0

    def can_spend(self, worst: int) -> bool:
        return self.spent + worst <= self.cap

    def spend(self, amount: int) -> None:
        self.spent += max(0, int(amount))

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.spent)


def is_challenge(html: str, title: str = "") -> bool:
    sample = f"{title}\n{html or ''}"[:20000].lower()
    return any(marker in sample for marker in CHALLENGE_MARKERS)


def usable_text(html: str, visible: str, title: str) -> tuple[bool, str]:
    if is_challenge(html, title):
        return False, "challenge_page"
    words = [part for part in (visible or "").split() if part]
    if len(words) >= MIN_WORDS:
        return True, ""
    return False, "thin_or_empty"


def _schema_types(objects: list) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            for item in kinds:
                if item and str(item) not in found:
                    found.append(str(item))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(objects)
    return found


def describe_page(url: str, html: str, visible: str = "") -> dict:
    extracted = extract_html(html or "", url)
    text = visible.strip()
    if len(text.split()) < extracted.word_count:
        text = extracted.main_text
    headings = [str(item.get("text") or "") for item in extracted.headings if str(item.get("text") or "").strip()]
    if extracted.h1 and extracted.h1 not in headings:
        headings.insert(0, extracted.h1)
    return {
        "title": extracted.title,
        "headings": headings,
        "links": list(extracted.links),
        "schema_types": _schema_types(extracted.schema_objects),
        "visible_text": text,
        "word_count": len(text.split()) if text else extracted.word_count,
    }


def html_from_visible(title: str, visible: str, schema_json: str = "") -> str:
    safe_title = html_lib.escape(title or "")
    safe_body = html_lib.escape(visible or "")
    schema = ""
    if schema_json:
        schema = f'<script type="application/ld+json">{schema_json}</script>'
    return (
        "<!doctype html><html><head><title>"
        + safe_title
        + "</title>"
        + schema
        + "</head><body><main><p>"
        + safe_body
        + "</p></main></body></html>"
    )


def interpret_serper(payload: dict) -> tuple[str, str, str, int]:
    """Return (html, visible text, title, credits) from a Serper scrape body."""
    text = str(payload.get("text") or "")
    markdown = str(payload.get("markdown") or "")
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    title = str(meta.get("title") or meta.get("og:title") or "")
    credits = payload.get("credits")
    charged = int(credits) if isinstance(credits, (int, float)) and not isinstance(credits, bool) else 2
    jsonld = payload.get("jsonld")
    schema_json = json.dumps(jsonld) if jsonld else ""
    if text.lstrip().startswith("<"):
        return text, "", title, charged
    visible = text or markdown
    return html_from_visible(title, visible, schema_json), visible, title, charged


class PlaywrightSession:
    """One headless Chromium. Realistic UA and viewport. Waits for network idle."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    def _ensure(self):
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def render(self, url: str) -> BrowserRender:
        self._ensure()
        context = self._browser.new_context(
            user_agent=BROWSER_UA,
            viewport=BROWSER_VIEWPORT,
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = context.new_page()
        error = None
        try:
            try:
                page.goto(url, wait_until="networkidle", timeout=30000)
            except Exception as exc:  # noqa: BLE001 - a timeout can still leave a rendered page
                error = f"{type(exc).__name__}: {exc}"
            try:
                html = page.content()
                title = page.title()
                visible = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001
                return BrowserRender(error=error or f"{type(exc).__name__}: {exc}")
            if not html:
                return BrowserRender(error=error or "empty browser document")
            return BrowserRender(html=html, visible_text=visible or "", title=title or "", error=None)
        finally:
            context.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None


class LayeredFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        delay_seconds: float = 1.0,
        allow_private: bool = False,
        timeout: float = 20.0,
        serper_api_key: str | None = None,
        serper_credit_cap: int = 0,
        use_browser: bool = True,
        crawler: Crawler | None = None,
        browser=None,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.delay_seconds = delay_seconds
        self.allow_private = allow_private
        self.serper_api_key = (serper_api_key or "").strip() or None
        self.credits = CreditCap(serper_credit_cap)
        self.use_browser = use_browser
        self._owns_crawler = crawler is None
        self.crawler = crawler or Crawler(
            user_agent=user_agent,
            delay_seconds=0,
            allow_private=allow_private,
            timeout=timeout,
        )
        self._browser = browser
        self._owns_browser = False
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout))
        self._sleep = sleep
        self._clock = clock
        self._last: dict[str, float] = {}

    def close(self) -> None:
        if self._owns_crawler:
            self.crawler.close()
        if self._owns_browser and self._browser is not None:
            self._browser.close()
        if self._owns_client:
            self.client.close()

    def fetch(self, url: str) -> LayeredPage:
        started = self._clock()
        try:
            safe = assert_safe_url(url, allow_private=self.allow_private, resolve=True)
        except (UnsafeURL, ValueError) as exc:
            return LayeredPage(url=url, attempts=[Attempt("http", False, str(exc))])

        http_attempt, http_html, http_visible, http_title = self._http(safe)
        if http_attempt.ok:
            page = self._success(safe, "http", http_html, http_visible, http_title, [http_attempt, Attempt("browser", False, "not tried: http succeeded"), Attempt("serper", False, "not tried: http succeeded")])
            page.fetch_ms = (self._clock() - started) * 1000
            return page

        browser_attempt, browser_html, browser_visible, browser_title = self._browser_fetch(safe)
        if browser_attempt.ok:
            page = self._success(safe, "browser", browser_html, browser_visible, browser_title, [http_attempt, browser_attempt, Attempt("serper", False, "not tried: browser succeeded")])
            page.fetch_ms = (self._clock() - started) * 1000
            return page

        serper_attempt, serper_html, serper_visible, serper_title = self._serper(safe)
        attempts = [http_attempt, browser_attempt, serper_attempt]
        if serper_attempt.ok:
            page = self._success(safe, "serper", serper_html, serper_visible, serper_title, attempts)
            page.fetch_ms = (self._clock() - started) * 1000
            return page
        page = LayeredPage(url=safe, attempts=attempts)
        page.fetch_ms = (self._clock() - started) * 1000
        return page

    def _success(self, url: str, method: str, html: str, visible: str, title: str, attempts: list[Attempt]) -> LayeredPage:
        if visible and visible[:80] not in (html or ""):
            extracted = extract_html(html or "", url)
            if len(visible.split()) > extracted.word_count + 20:
                snippet = html_lib.escape(visible)
                if "</body>" in html:
                    html = html.replace("</body>", f"<main>{snippet}</main></body>", 1)
                else:
                    html = (html or "") + f"<main>{snippet}</main>"
        described = describe_page(url, html, visible)
        return LayeredPage(
            url=url,
            html=html,
            method=method,
            visible_text=described["visible_text"],
            title=described["title"] or title,
            headings=described["headings"],
            links=described["links"],
            schema_types=described["schema_types"],
            attempts=attempts,
        )

    def _wait(self, url: str) -> None:
        if self.delay_seconds <= 0:
            return
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._last:
            self._last[host] = self._clock()
            return
        elapsed = self._clock() - self._last[host]
        if elapsed < self.delay_seconds:
            self._sleep(self.delay_seconds - elapsed)
        self._last[host] = self._clock()

    def _http(self, url: str) -> tuple[Attempt, str, str, str]:
        self._wait(url)
        try:
            fetched: Fetched = self.crawler.fetch(url)
        except UnsafeURL as exc:
            return Attempt("http", False, str(exc)), "", "", ""
        except Exception as exc:  # noqa: BLE001 - one host must not abort the batch
            return Attempt("http", False, f"{type(exc).__name__}: {exc}"), "", "", ""
        if fetched.blocked:
            return Attempt("http", False, fetched.error or "Blocked by robots.txt"), "", "", ""
        if fetched.error or not fetched.html:
            return Attempt("http", False, fetched.error or "empty response"), "", "", ""
        described = describe_page(url, fetched.html, "")
        ok, reason = usable_text(fetched.html, described["visible_text"], described["title"])
        if not ok:
            return Attempt("http", False, reason), "", "", ""
        return Attempt("http", True, "fetched"), fetched.html, described["visible_text"], described["title"]

    def _browser_fetch(self, url: str) -> tuple[Attempt, str, str, str]:
        if not self.use_browser:
            return Attempt("browser", False, "browser fallback is off"), "", "", ""
        allowed, status = self.crawler.allowed_for(url, BROWSER_UA)
        if not allowed:
            return Attempt("browser", False, "Blocked by robots.txt"), "", "", ""
        renderer = self._browser_renderer()
        if renderer is None:
            return Attempt("browser", False, "playwright is not installed"), "", "", ""
        self._wait(url)
        try:
            rendered: BrowserRender = renderer.render(url)
        except Exception as exc:  # noqa: BLE001
            return Attempt("browser", False, f"{type(exc).__name__}: {exc}"), "", "", ""
        if rendered.error and not rendered.html:
            return Attempt("browser", False, rendered.error), "", "", ""
        described = describe_page(url, rendered.html, rendered.visible_text)
        ok, reason = usable_text(rendered.html, described["visible_text"], described["title"] or rendered.title)
        if not ok:
            detail = reason if not rendered.error else f"{reason}; {rendered.error}"
            return Attempt("browser", False, detail), "", "", ""
        note = "rendered" if not rendered.error else f"rendered after {rendered.error}"
        return Attempt("browser", True, note), rendered.html, described["visible_text"], described["title"] or rendered.title

    def _browser_renderer(self):
        if self._browser is not None:
            return self._browser
        if not self.use_browser:
            return None
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return None
        self._browser = PlaywrightSession()
        self._owns_browser = True
        return self._browser

    def _serper(self, url: str) -> tuple[Attempt, str, str, str]:
        if not self.serper_api_key:
            return Attempt("serper", False, "SERPER_API_KEY is not set"), "", "", ""
        if self.credits.cap <= 0:
            return Attempt("serper", False, "serper scrape is off because the credit cap is 0"), "", "", ""
        if not self.credits.can_spend(SERPER_SCRAPE_WORST_CREDITS):
            return Attempt("serper", False, f"serper credit cap reached ({self.credits.spent} of {self.credits.cap} spent)"), "", "", ""
        allowed, _status = self.crawler.allowed_for(url, "*")
        if not allowed:
            return Attempt("serper", False, "Blocked by robots.txt"), "", "", ""
        self._wait(url)
        try:
            response = self.client.post(
                SERPER_SCRAPE_URL,
                headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                json={"url": url, "includeMarkdown": False},
            )
        except Exception as exc:  # noqa: BLE001
            return Attempt("serper", False, f"{type(exc).__name__}: {exc}"), "", "", ""
        credits = 1
        payload: dict = {}
        try:
            body = response.json()
            if isinstance(body, dict):
                payload = body
                if isinstance(body.get("credits"), (int, float)) and not isinstance(body.get("credits"), bool):
                    credits = int(body["credits"])
        except Exception:  # noqa: BLE001
            payload = {}
        self.credits.spend(credits)
        if response.status_code >= 400:
            return Attempt("serper", False, f"HTTP {response.status_code}"), "", "", ""
        html, visible, title, _charged = interpret_serper(payload)
        ok, reason = usable_text(html, visible or html, title)
        if not ok:
            return Attempt("serper", False, reason), "", "", ""
        return Attempt("serper", True, f"scraped, {credits} credits"), html, visible, title
