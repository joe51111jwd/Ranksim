"""Crawler safety: address guard, redirects, and robots.txt failure handling. No network."""

import httpx
import pytest

from aeo.crawl.fetch import Crawler
from aeo.crawl.urls import assert_safe_url, normalize_url
from aeo.errors import UnsafeURL


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.1/",                 # carrier-grade NAT
        "http://100.100.100.200/latest/meta-data/",  # metadata address inside 100.64/10
        "http://[::ffff:127.0.0.1]/",         # IPv4-mapped loopback
        "http://[fd00::1]/",                  # IPv6 unique local
        "http://[::1]/",
        "http://192.0.2.10/",                 # documentation range
        "http://169.254.169.254/",
        "http://127.0.0.1/",
    ],
)
def test_non_global_addresses_are_refused(url):
    with pytest.raises(UnsafeURL):
        assert_safe_url(url, resolve=False)


def test_ipv6_literals_keep_their_brackets():
    assert normalize_url("http://[2606:4700::1111]/x") == "http://[2606:4700::1111]/x"
    assert normalize_url("http://[2606:4700::1111]:8080/x") == "http://[2606:4700::1111]:8080/x"
    assert assert_safe_url("http://[2606:4700::1111]/x", resolve=False) == "http://[2606:4700::1111]/x"


def _crawler(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Crawler(user_agent="TestBot/0.1", delay_seconds=0, allow_private=True, client=client)


def test_redirect_to_a_non_http_scheme_is_an_error_not_a_crash():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "file:///etc/passwd"})

    crawler = _crawler(handler)
    result = crawler.fetch("https://site.test/page")
    crawler.close()
    assert result.error and "redirect" in result.error.lower() and not result.html


def test_redirect_loops_and_missing_location_are_errors_not_pages():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"}, text="Moved")
        return httpx.Response(302, text="Moved")

    crawler = _crawler(handler)
    loop = crawler.fetch("https://site.test/loop")
    bare = crawler.fetch("https://site.test/bare")
    crawler.close()
    assert "Too many redirects" in loop.error and loop.html == ""
    assert "without a Location" in bare.error and bare.html == ""


@pytest.mark.parametrize("robots_status", [500, 503, 429])
def test_unreachable_robots_txt_means_do_not_crawl(robots_status):
    fetched = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status, text="User-agent: *\nDisallow: /\n")
        fetched.append(request.url.path)
        return httpx.Response(200, text="<html><title>x</title><body>page</body></html>")

    crawler = _crawler(handler)
    result = crawler.fetch("https://site.test/page")
    crawler.close()
    assert result.blocked and result.robots_status == "unreachable"
    assert fetched == []


def test_missing_robots_txt_allows_crawling_and_status_is_reported():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<html><title>x</title><body>page</body></html>", headers={"content-type": "text/html"})

    crawler = _crawler(handler)
    result = crawler.fetch("https://site.test/page")
    crawler.close()
    assert result.status_code == 200 and result.html and not result.blocked


def test_large_bodies_are_streamed_and_capped_and_unreadable_types_are_not_downloaded():
    from aeo.crawl import fetch as fetch_module

    pulled = {"html": 0, "pdf": 0}

    def body(kind, total):
        def generate():
            chunk = b"<p>" + b"x" * 65_000 + b"</p>"
            sent = 0
            while sent < total:
                pulled[kind] += len(chunk)
                sent += len(chunk)
                yield chunk
        return generate()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/big":
            return httpx.Response(200, headers={"content-type": "text/html"}, content=body("html", 50_000_000))
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=body("pdf", 20_000_000))

    crawler = _crawler(handler)
    big = crawler.fetch("https://site.test/big")
    pdf = crawler.fetch("https://site.test/doc.pdf")
    crawler.close()
    assert len(big.html) <= fetch_module.MAX_BYTES
    assert pulled["html"] < fetch_module.MAX_BYTES + 200_000  # stopped near the cap, not at 50 MB
    assert "Unsupported content type" in pdf.error
    assert pulled["pdf"] == 0


def test_a_slow_drip_stops_at_the_total_deadline(monkeypatch):
    from aeo.crawl import fetch as fetch_module

    ticks = {"t": 0.0}

    def clock():
        ticks["t"] += 5.0  # every read takes 5 "seconds"
        return ticks["t"]

    def drip():
        for _ in range(1000):
            yield b"<p>a</p>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=drip())

    crawler = _crawler(handler)
    crawler._clock = clock
    result = crawler.fetch("https://site.test/slow")
    crawler.close()
    reads = int(ticks["t"] / 5.0)
    assert reads < 20  # stopped after about TOTAL_DEADLINE_SECONDS / 5 reads, not 1000
    assert result.html.count("<p>a</p>") < 20
