"""Fetch ladder: HTTP, then a browser, then Serper scrape, then an archive.

HTTP and the browser obey robots.txt for the user agent that would fetch.
A disallow stops our crawler. It does not stop the later rungs. Serper scrape
and Common Crawl / Wayback are separate sources, which is how a robots block
or a 403 can still become page text. The API key is never put in a reason,
a record, or a log line.
"""

from __future__ import annotations

import html as html_lib
import json
from dataclasses import dataclass
from datetime import date

import httpx

from aeo.crawl.fetch import Crawler
from aeo.crawl.layered import (
    BROWSER_UA,
    SERPER_SCRAPE_URL,
    BrowserRender,
    interpret_serper,
    usable_text,
)
from aeo.crawl.urls import assert_safe_url
from aeo.errors import UnsafeURL
from aeo.ranksim.localindex.credits import SCRAPE_WORST, CreditSpend, search_credits
from aeo.ranksim.localindex.records import Attempt, PageRecord, page_from_html
from aeo.ranksim.serp import SERPER_ENDPOINT, parse_serper

WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
COMMON_CRAWL_INFO = "https://index.commoncrawl.org/collinfo.json"
COMMON_CRAWL_DATA = "https://data.commoncrawl.org/"


def _date_from_stamp(stamp: str) -> str:
    digits = "".join(ch for ch in stamp if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def _charged(payload: dict, default: int) -> int:
    credits = payload.get("credits") if isinstance(payload, dict) else None
    if isinstance(credits, (int, float)) and not isinstance(credits, bool):
        return max(0, int(credits))
    return default


class IndexFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        credits: CreditSpend,
        api_key: str | None = None,
        delay_seconds: float = 0.0,
        allow_private: bool = False,
        timeout: float = 20.0,
        use_browser: bool = True,
        client: httpx.Client | None = None,
        crawler: Crawler | None = None,
        browser=None,
        today: str | None = None,
        scrape_reserve: int = 0,
    ) -> None:
        self.user_agent = user_agent
        self.credits = credits
        self.scrape_reserve = max(0, int(scrape_reserve))
        self.api_key = (api_key or "").strip() or None
        self.use_browser = use_browser
        self.allow_private = allow_private
        self.today = today or date.today().isoformat()
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout))
        self._owns_crawler = crawler is None
        self.crawler = crawler or Crawler(
            user_agent=user_agent,
            delay_seconds=delay_seconds,
            allow_private=allow_private,
            timeout=timeout,
            client=self.client,
        )
        self._browser = browser
        self._owns_browser = False
        self._cdx: str | None = None
        self._cdx_loaded = False

    def close(self) -> None:
        if self._owns_crawler:
            self.crawler.close()
        if self._owns_browser and self._browser is not None:
            self._browser.close()
        if self._owns_client:
            self.client.close()

    def search(self, query: str, num: int) -> tuple[list, list[Attempt]]:
        """Deep organic results. Pages only while the credit cap allows it."""
        hits: list = []
        attempts: list[Attempt] = []
        if not self.api_key:
            return [], [Attempt("serper_search", False, "SERPER_API_KEY is not set")]
        page = 1
        while len(hits) < num and page <= 10:
            this_num = num if page == 1 else 10
            reserve = search_credits(this_num)
            # The first request is the deep query. Later pages must leave the
            # scrape reserve, or a short first page would spend the whole cap.
            if page > 1 and not self.credits.can_spend(reserve + self.scrape_reserve):
                attempts.append(Attempt("serper_search", False, "stopped paging to keep scrape credits"))
                break
            if not self.credits.can_spend(reserve):
                attempts.append(Attempt("serper_search", False, f"serper credit cap reached ({self.credits.spent} of {self.credits.cap})"))
                break
            body = {"q": query, "num": this_num, "gl": "us", "hl": "en"}
            if page > 1:
                body["page"] = page
            try:
                response = self.client.post(
                    SERPER_ENDPOINT,
                    headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                    json=body,
                )
            except Exception:
                attempts.append(Attempt("serper_search", False, "serper search request failed"))
                break
            payload: dict = {}
            try:
                parsed = response.json()
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
            self.credits.add_search(_charged(payload, reserve))
            if response.status_code >= 400:
                attempts.append(Attempt("serper_search", False, f"HTTP {response.status_code}"))
                break
            batch = parse_serper(payload, this_num)
            if not batch:
                attempts.append(Attempt("serper_search", True, f"page {page} returned no organic results"))
                break
            start = len(hits)
            for offset, hit in enumerate(batch, start=1):
                position = hit.position if page == 1 else start + offset
                if page > 1 and hit.position > start:
                    position = hit.position
                hits.append((position, hit.url, hit.title, hit.snippet))
            attempts.append(Attempt("serper_search", True, f"page {page}, {len(batch)} results"))
            if len(batch) < this_num or len(hits) >= num:
                break
            page += 1
        return hits[:num], attempts

    def fetch(self, url: str) -> PageRecord:
        try:
            safe = assert_safe_url(url, allow_private=self.allow_private, resolve=not self.allow_private)
        except (UnsafeURL, ValueError) as exc:
            return PageRecord(url=url, status="missing", miss_reason=str(exc), attempts=[Attempt("http", False, str(exc))])

        attempts: list[Attempt] = []
        http_ok, http_html, http_final, http_attempt = self._http(safe)
        attempts.append(http_attempt)
        if http_ok:
            attempts.append(Attempt("browser", False, "not tried: http succeeded"))
            attempts.append(Attempt("serper_scrape", False, "not tried: http succeeded"))
            attempts.append(Attempt("archive", False, "not tried: http succeeded"))
            return page_from_html(safe, http_html, source="http", captured_on=self.today, final_url=http_final, attempts=attempts)

        browser_ok, browser_html, browser_attempt = self._browser_fetch(safe)
        attempts.append(browser_attempt)
        if browser_ok:
            attempts.append(Attempt("serper_scrape", False, "not tried: browser succeeded"))
            attempts.append(Attempt("archive", False, "not tried: browser succeeded"))
            return page_from_html(safe, browser_html, source="browser", captured_on=self.today, final_url=safe, attempts=attempts)

        scrape_ok, scrape_html, scrape_attempt = self._scrape(safe)
        attempts.append(scrape_attempt)
        if scrape_ok:
            attempts.append(Attempt("archive", False, "not tried: serper scrape succeeded"))
            return page_from_html(safe, scrape_html, source="serper_scrape", captured_on=self.today, final_url=safe, attempts=attempts)

        archive = self._archive(safe)
        attempts.append(archive.attempt)
        if archive.html:
            return page_from_html(
                safe,
                archive.html,
                source=archive.source,
                captured_on=archive.captured_on or self.today,
                final_url=safe,
                attempts=attempts,
            )
        reason = archive.attempt.reason or scrape_attempt.reason or browser_attempt.reason or http_attempt.reason or "no_text"
        return PageRecord(url=safe, final_url=safe, status="missing", miss_reason=reason, source="", attempts=attempts)

    def _http(self, url: str) -> tuple[bool, str, str, Attempt]:
        try:
            fetched = self.crawler.fetch(url)
        except UnsafeURL as exc:
            return False, "", url, Attempt("http", False, str(exc))
        except Exception:
            return False, "", url, Attempt("http", False, "http request failed")
        if fetched.blocked:
            return False, "", url, Attempt("http", False, fetched.error or "Blocked by robots.txt")
        if fetched.error or not fetched.html:
            return False, "", fetched.final_url or url, Attempt("http", False, fetched.error or "empty response")
        ok, reason = usable_text(fetched.html, fetched.html, "")
        if not ok:
            return False, "", fetched.final_url or url, Attempt("http", False, reason)
        return True, fetched.html, fetched.final_url or url, Attempt("http", True, "fetched")

    def _browser_fetch(self, url: str) -> tuple[bool, str, Attempt]:
        if not self.use_browser:
            return False, "", Attempt("browser", False, "browser fallback is off")
        allowed, _status = self.crawler.allowed_for(url, BROWSER_UA)
        if not allowed:
            return False, "", Attempt("browser", False, "Blocked by robots.txt")
        renderer = self._renderer()
        if renderer is None:
            return False, "", Attempt("browser", False, "playwright is not installed")
        try:
            rendered: BrowserRender = renderer.render(url)
        except Exception:
            return False, "", Attempt("browser", False, "browser request failed")
        if rendered.error and not rendered.html:
            return False, "", Attempt("browser", False, rendered.error)
        html = rendered.html or ""
        visible = rendered.visible_text or ""
        ok, reason = usable_text(html, visible or html, rendered.title or "")
        if not ok:
            return False, "", Attempt("browser", False, reason)
        if visible and visible[:80] not in html:
            html = html + "<main>" + visible + "</main>"
        return True, html, Attempt("browser", True, "rendered")

    def _renderer(self):
        if self._browser is not None:
            return self._browser
        if not self.use_browser:
            return None
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return None
        from aeo.crawl.layered import PlaywrightSession

        self._browser = PlaywrightSession()
        self._owns_browser = True
        return self._browser

    def _scrape(self, url: str) -> tuple[bool, str, Attempt]:
        if not self.api_key:
            return False, "", Attempt("serper_scrape", False, "SERPER_API_KEY is not set")
        if not self.credits.can_spend(SCRAPE_WORST):
            return False, "", Attempt(
                "serper_scrape",
                False,
                f"serper credit cap reached ({self.credits.spent} of {self.credits.cap})",
            )
        try:
            response = self.client.post(
                SERPER_SCRAPE_URL,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={"url": url},
            )
        except Exception:
            return False, "", Attempt("serper_scrape", False, "serper scrape request failed")
        payload: dict = {}
        try:
            body = response.json()
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}
        self.credits.add_scrape(_charged(payload, 2))
        if response.status_code >= 400:
            return False, "", Attempt("serper_scrape", False, f"HTTP {response.status_code}")
        html, visible, title, _charged_again = interpret_serper(payload)
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        description = ""
        for key in ("description", "og:description", "twitter:description"):
            if meta.get(key):
                description = str(meta[key]).strip()
                break
        if description and 'name="description"' not in html:
            tag = '<meta name="description" content="' + html_lib.escape(description, quote=True) + '">'
            if "</head>" in html:
                html = html.replace("</head>", tag + "</head>", 1)
            else:
                html = "<head>" + tag + "</head>" + html
        ok, reason = usable_text(html, visible or html, title)
        if not ok:
            return False, "", Attempt("serper_scrape", False, reason)
        return True, html, Attempt("serper_scrape", True, "scraped")

    def _archive(self, url: str) -> "_Archive":
        wayback = self._wayback_meta(url)
        crawl = self._common_crawl_meta(url)
        choices = [item for item in (wayback, crawl) if item is not None]
        choices.sort(key=lambda item: item.captured_on, reverse=True)
        last = Attempt("archive", False, "no archive capture")
        for choice in choices:
            html, attempt = self._fetch_archive(choice)
            last = attempt
            if html:
                return _Archive(html=html, source=choice.source, captured_on=choice.captured_on, attempt=attempt)
        return _Archive(html="", source="", captured_on="", attempt=last)

    def _wayback_meta(self, url: str) -> "_ArchiveChoice | None":
        try:
            response = self.client.get(WAYBACK_AVAILABLE, params={"url": url})
            payload = response.json()
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        closest = ((payload.get("archived_snapshots") or {}).get("closest")) or {}
        if not isinstance(closest, dict) or not closest.get("available"):
            return None
        stamp = str(closest.get("timestamp") or "")
        captured = _date_from_stamp(stamp)
        if not stamp or not captured:
            return None
        raw = f"https://web.archive.org/web/{stamp}id_/{url}"
        return _ArchiveChoice(source="wayback", captured_on=captured, fetch_url=raw, offset=None, length=None)

    def _common_crawl_meta(self, url: str) -> "_ArchiveChoice | None":
        cdx = self._latest_cdx()
        if not cdx:
            return None
        try:
            response = self.client.get(
                cdx,
                params={"url": url, "output": "json", "limit": "5", "filter": "status:200", "fl": "timestamp,filename,offset,length,status"},
            )
            text = response.text
        except Exception:
            return None
        best: _ArchiveChoice | None = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            stamp = str(row.get("timestamp") or "")
            filename = str(row.get("filename") or "")
            captured = _date_from_stamp(stamp)
            if not filename or not captured:
                continue
            try:
                offset = int(row.get("offset"))
                length = int(row.get("length"))
            except (TypeError, ValueError):
                continue
            choice = _ArchiveChoice(
                source="common_crawl",
                captured_on=captured,
                fetch_url=COMMON_CRAWL_DATA + filename.lstrip("/"),
                offset=offset,
                length=length,
            )
            if best is None or choice.captured_on > best.captured_on:
                best = choice
        return best

    def _latest_cdx(self) -> str | None:
        if self._cdx_loaded:
            return self._cdx
        self._cdx_loaded = True
        try:
            response = self.client.get(COMMON_CRAWL_INFO)
            payload = response.json()
        except Exception:
            self._cdx = None
            return None
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("cdx-api"):
                    self._cdx = str(item["cdx-api"])
                    return self._cdx
        self._cdx = None
        return None

    def _fetch_archive(self, choice: "_ArchiveChoice") -> tuple[str, Attempt]:
        headers = {}
        if choice.offset is not None and choice.length is not None:
            headers["Range"] = f"bytes={choice.offset}-{choice.offset + choice.length - 1}"
        try:
            response = self.client.get(choice.fetch_url, headers=headers)
        except Exception:
            return "", Attempt(choice.source, False, "archive request failed")
        if response.status_code >= 400:
            return "", Attempt(choice.source, False, f"HTTP {response.status_code}")
        body = response.text
        if choice.source == "common_crawl":
            body = _html_from_warc(body)
        if not body:
            return "", Attempt(choice.source, False, "empty archive capture")
        ok, reason = usable_text(body, body, "")
        if not ok:
            return "", Attempt(choice.source, False, reason)
        return body, Attempt(choice.source, True, f"captured {choice.captured_on}")


@dataclass
class _ArchiveChoice:
    source: str
    captured_on: str
    fetch_url: str
    offset: int | None
    length: int | None


@dataclass
class _Archive:
    html: str
    source: str
    captured_on: str
    attempt: Attempt


def _html_from_warc(body: str) -> str:
    marker = "\r\n\r\n"
    first = body.find(marker)
    if first < 0:
        marker = "\n\n"
        first = body.find(marker)
    if first < 0:
        return body
    rest = body[first + len(marker) :]
    second = rest.find(marker)
    if second < 0:
        return rest
    return rest[second + len(marker) :]

