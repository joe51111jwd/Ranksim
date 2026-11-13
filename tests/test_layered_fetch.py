"""HTTP, then a headless browser, then Serper scrape. Serper stays off unless the cap allows it."""

from __future__ import annotations

import httpx

from aeo.crawl.fetch import Fetched
from aeo.crawl.layered import (
    SERPER_SCRAPE_WORST_CREDITS,
    BrowserRender,
    CreditCap,
    LayeredFetcher,
    is_challenge,
)


PAGE = """<!doctype html><html><head>
<title>Dirt work software</title>
<script type="application/ld+json">{"@type":"SoftwareApplication","name":"Dirt"}</script>
</head><body>
<h1>Dirt work software</h1>
<p>Crews track quantities, fuel, and haul roads on one page.</p>
<a href="https://example.com/pricing">Pricing</a>
</body></html>"""

CHALLENGE = "<html><head><title>Just a moment...</title></head><body>cf-challenge challenge-platform</body></html>"


class FakeCrawler:
    def __init__(self, fetched: Fetched, *, browser_allowed: bool = True) -> None:
        self.fetched = fetched
        self.browser_allowed = browser_allowed
        self.closed = False

    def fetch(self, url: str) -> Fetched:
        return self.fetched

    def allowed_for(self, url: str, user_agent: str) -> tuple[bool, str]:
        if user_agent == "*":
            return True, "allowed"
        return self.browser_allowed, "allowed" if self.browser_allowed else "disallowed"

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, rendered: BrowserRender) -> None:
        self.rendered = rendered
        self.urls: list[str] = []

    def render(self, url: str) -> BrowserRender:
        self.urls.append(url)
        return self.rendered

    def close(self) -> None:
        return None


def _fetcher(crawler, browser=None, **kwargs) -> LayeredFetcher:
    return LayeredFetcher(
        user_agent="RankSimTest",
        delay_seconds=0,
        crawler=crawler,
        browser=browser,
        use_browser=True,
        serper_api_key=kwargs.get("serper_api_key"),
        serper_credit_cap=kwargs.get("serper_credit_cap", 0),
        client=kwargs.get("client"),
        sleep=kwargs.get("sleep", lambda _seconds: None),
        clock=kwargs.get("clock", lambda: 0.0),
    )


def test_http_success_does_not_call_the_browser_or_serper():
    browser = FakeBrowser(BrowserRender(html=PAGE, visible_text="unused " * 20, title="x"))
    fetcher = _fetcher(FakeCrawler(Fetched(url="https://example.com/a", final_url="https://example.com/a", status_code=200, html=PAGE)), browser)
    page = fetcher.fetch("https://example.com/a")
    assert page.method == "http"
    assert page.title == "Dirt work software"
    assert "Dirt work software" in page.headings
    assert "https://example.com/pricing" in page.links
    assert "SoftwareApplication" in page.schema_types
    assert "haul roads" in page.visible_text
    assert browser.urls == []
    assert page.attempts[1].reason == "not tried: http succeeded"
    assert page.attempts[2].reason == "not tried: http succeeded"
    assert "key" not in page.attempt_dicts()[2]["reason"]


def test_challenge_html_falls_through_to_the_browser():
    assert is_challenge(CHALLENGE, "Just a moment...")
    browser = FakeBrowser(BrowserRender(html=PAGE, visible_text="Crews track quantities and haul roads here.", title="Dirt work software"))
    fetcher = _fetcher(
        FakeCrawler(Fetched(url="https://example.com/a", final_url="https://example.com/a", status_code=200, html=CHALLENGE)),
        browser,
    )
    page = fetcher.fetch("https://example.com/a")
    assert page.method == "browser"
    assert page.attempts[0].ok is False
    assert page.attempts[0].reason == "challenge_page"
    assert page.attempts[1].ok is True
    assert browser.urls == ["https://example.com/a"]


def test_robots_block_for_the_browser_ua_does_not_launch_it():
    browser = FakeBrowser(BrowserRender(html=PAGE, visible_text="words " * 20))
    fetcher = _fetcher(
        FakeCrawler(
            Fetched(url="https://example.com/a", final_url="https://example.com/a", blocked=True, error="Blocked by robots.txt"),
            browser_allowed=False,
        ),
        browser,
    )
    page = fetcher.fetch("https://example.com/a")
    assert page.method is None
    assert browser.urls == []
    assert "robots" in page.attempts[0].reason.lower()
    assert "robots" in page.attempts[1].reason.lower()


def test_serper_is_off_without_a_key_and_when_the_cap_is_zero():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"text": "should not run", "credits": 2})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    crawler = FakeCrawler(Fetched(url="https://example.com/a", final_url="https://example.com/a", error="SSLError"))
    browser = FakeBrowser(BrowserRender(error="TimeoutError"))
    no_key = _fetcher(crawler, browser, client=client, serper_api_key=None, serper_credit_cap=50)
    page = no_key.fetch("https://example.com/a")
    assert page.method is None
    assert page.attempts[2].reason == "SERPER_API_KEY is not set"
    capped = _fetcher(crawler, browser, client=client, serper_api_key="secret-key", serper_credit_cap=0)
    page = capped.fetch("https://example.com/a")
    assert "credit cap is 0" in page.attempts[2].reason
    assert "secret-key" not in page.attempts[2].reason
    assert calls == []


def test_serper_stops_when_another_call_could_break_the_cap():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("x-api-key") or "")
        return httpx.Response(200, json={"text": "Crews track quantities fuel and haul roads on this page.", "metadata": {"title": "Dirt"}, "credits": 2})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    crawler = FakeCrawler(Fetched(url="https://example.com/a", final_url="https://example.com/a", error="HTTP 403"))
    browser = FakeBrowser(BrowserRender(html=CHALLENGE, visible_text="Just a moment", title="Just a moment..."))
    fetcher = _fetcher(crawler, browser, client=client, serper_api_key="secret-key", serper_credit_cap=SERPER_SCRAPE_WORST_CREDITS)
    first = fetcher.fetch("https://example.com/a")
    second = fetcher.fetch("https://example.com/b")
    assert first.method == "serper"
    assert "haul roads" in first.visible_text
    assert second.method is None
    assert "credit cap" in second.attempts[2].reason
    assert calls == ["secret-key"]
    assert fetcher.credits.spent == 2


def test_credit_cap_rejects_a_worst_case_that_would_pass_the_ceiling():
    cap = CreditCap(SERPER_SCRAPE_WORST_CREDITS - 1)
    assert cap.can_spend(SERPER_SCRAPE_WORST_CREDITS) is False


def test_polite_delay_sleeps_for_the_same_host():
    slept: list[float] = []
    now = {"t": 0.0}

    def clock() -> float:
        return now["t"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now["t"] += seconds

    fetcher = LayeredFetcher(
        user_agent="RankSimTest",
        delay_seconds=1.0,
        crawler=FakeCrawler(Fetched(url="https://example.com/a", final_url="https://example.com/a", status_code=200, html=PAGE)),
        browser=FakeBrowser(BrowserRender()),
        use_browser=False,
        sleep=sleep,
        clock=clock,
    )
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/b")
    assert slept == [1.0]
