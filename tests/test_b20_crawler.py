"""B20 crawler fixes: robots.txt is checked on every redirect hop (F1), a host httpx cannot encode
does not crash a fetch (F8), robots.txt is read as UTF-8 whatever the declared charset (F9), dot
segments are removed before the robots check (F10), and robots.txt requests respect 429/503
backoff (F11). No network: mock transports only."""

import socket
import time

import httpx
import pytest

import aeo.crawl.fetch as fetch_module
from aeo.crawl.fetch import ROBOTS_UNREACHABLE, Crawler
from aeo.crawl.urls import normalize_url

UA = "RanksimTest/1.0 (+https://ci.ranksim-tests.org/bot)"
PUBLIC = "93.184.216.34"
HTML = {"content-type": "text/html"}
TEXT = {"content-type": "text/plain"}


def _page(path: str) -> httpx.Response:
    return httpx.Response(200, text=f"<html><title>{path}</title><body>{path}</body></html>", headers=HTML)


def _crawler(handler, **kwargs) -> Crawler:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    options = {"user_agent": UA, "delay_seconds": 0, "allow_private": True, "client": client}
    options.update(kwargs)
    return Crawler(**options)


# F1


def _two_sites(b_robots: httpx.Response):
    """site.test disallows /private; /go redirects into it, /out to other.test/secret and /moved
    to other.test/ok. other.test answers robots.txt with ``b_robots``."""
    hits = []

    def handler(request):
        host, path = request.url.host, request.url.path
        hits.append((host, path))
        if host == "site.test":
            if path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", headers=TEXT)
            if path == "/go":
                return httpx.Response(302, headers={"location": "/private/page"})
            if path == "/out":
                return httpx.Response(302, headers={"location": "https://other.test/secret"})
            if path == "/moved":
                return httpx.Response(301, headers={"location": "https://other.test/ok"})
            return _page(path)
        if path == "/robots.txt":
            return b_robots
        return _page(path)

    return hits, handler


def test_a_same_origin_redirect_into_a_disallowed_path_gets_no_request():
    hits, handler = _two_sites(httpx.Response(404))
    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/go")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed" and not result.html
    assert result.url == "https://site.test/go"
    assert result.final_url == "https://site.test/private/page"
    assert "https://site.test/private/page" in result.error  # the message names the hop
    assert ("site.test", "/private/page") not in hits


def test_a_cross_origin_redirect_is_checked_against_the_robots_txt_of_the_target():
    hits, handler = _two_sites(httpx.Response(200, text="User-agent: *\nDisallow: /\n", headers=TEXT))
    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/out")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert result.final_url == "https://other.test/secret"
    assert [path for host, path in hits if host == "other.test"] == ["/robots.txt"]  # robots.txt first, then nothing


def test_a_redirect_target_whose_robots_txt_does_not_answer_is_blocked():
    hits, handler = _two_sites(httpx.Response(503))
    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/out")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "unreachable"
    assert "https://other.test/secret" in result.error and "did not answer" in result.error
    assert [path for host, path in hits if host == "other.test"] == ["/robots.txt"]


def test_an_allowed_cross_origin_redirect_is_fetched_after_its_robots_txt_is_read_once():
    hits, handler = _two_sites(httpx.Response(404))  # other.test has no robots.txt: everything allowed
    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/moved")
        again = crawler.fetch("https://site.test/moved")
    finally:
        crawler.close()
    assert result.status_code == 200 and "/ok" in result.html and not result.blocked
    assert result.final_url == "https://other.test/ok" and result.robots_status == "allowed"
    assert again.status_code == 200
    assert [path for host, path in hits if host == "other.test"] == ["/robots.txt", "/ok", "/ok"]  # read once, first


def test_a_crawl_does_not_store_a_page_reached_through_a_disallowed_redirect():
    hits, handler = _two_sites(httpx.Response(404))

    def with_links(request):
        if request.url.host == "site.test" and request.url.path == "/":
            hits.append(("site.test", "/"))
            return httpx.Response(200, text='<html><body><a href="https://site.test/go">go</a></body></html>', headers=HTML)
        return handler(request)

    crawler = _crawler(with_links)
    try:
        batch = crawler.crawl("https://site.test/", sitemap_url=None, max_pages=5)
    finally:
        crawler.close()
    go = [page for page in batch.pages if page.url == "https://site.test/go"]
    assert go and go[0].blocked and not go[0].html
    assert ("site.test", "/private/page") not in hits


# F8


def test_a_host_httpx_cannot_encode_returns_an_error_and_is_cached(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port or 0))]
    )
    sent = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(404)

    host = "i❤.ws"  # valid under IDNA 2003 (the stdlib resolver), refused by httpx (IDNA 2008)
    crawler = _crawler(handler, allow_private=False)
    try:
        first = crawler.fetch(f"http://{host}/page")
        second = crawler.fetch(f"http://{host}/other")
        batch = crawler.crawl(f"http://{host}/", sitemap_url=None, max_pages=2)
    finally:
        crawler.close()
    for result in (first, second, *batch.pages):
        assert result.blocked and result.error and not result.html
    assert crawler._robots[f"http://{host}"] is ROBOTS_UNREACHABLE  # cached: the origin is not retried
    assert sent == []


# F9


@pytest.mark.parametrize("charset", ["ISO-8859-1", "windows-1252"])
def test_utf8_robots_txt_without_a_byte_order_mark_is_read_as_utf8_under_a_legacy_charset(charset):
    hits = []

    def handler(request):
        hits.append(request.url.path)
        if request.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /café\n".encode("utf-8")
            return httpx.Response(200, content=body, headers={"content-type": f"text/plain; charset={charset}"})
        return _page(request.url.path)

    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/café/menu")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


def test_utf8_robots_txt_cut_inside_a_character_by_the_size_cap_is_still_utf8(monkeypatch):
    head = "User-agent: *\nDisallow: /café\n# ".encode("utf-8")
    body = head + "é".encode("utf-8") * 50
    monkeypatch.setattr(fetch_module, "MAX_BYTES", len(head) + 2 * 10 + 1)  # the cap splits an 'é'
    hits = []

    def handler(request):
        hits.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=body, headers={"content-type": "text/plain; charset=ISO-8859-1"})
        return _page(request.url.path)

    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test/café/menu")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


# F10


def test_dot_segments_are_removed_as_httpx_removes_them():
    cases = {
        "http://h.test/public/../private/x": "http://h.test/private/x",
        "http://h.test/./private/x": "http://h.test/private/x",
        "http://h.test/../private": "http://h.test/private",
        "http://h.test/a/b/..": "http://h.test/a",
        "http://h.test/a/./": "http://h.test/a",
        "http://h.test/public/%2E%2E/private/x": "http://h.test/private/x",
        "http://h.test/public/%2e%2e/private/x": "http://h.test/private/x",
        "http://h.test/public/.%2E/private/x?q=1": "http://h.test/private/x?q=1",
        "http://h.test/%2E/x": "http://h.test/x",
    }
    for raw, expected in cases.items():
        assert normalize_url(raw) == expected, raw
    with httpx.Client() as client:
        for raw in cases:
            if "%" not in raw:  # httpx leaves an escaped dot alone; normalize_url resolves it first
                sent = client.build_request("GET", raw).url
                assert normalize_url(str(sent)) == normalize_url(raw), raw
    assert normalize_url("http://h.test/a/./", keep_trailing_slash=True) == "http://h.test/a/"


def test_ordinary_urls_keep_their_identity():
    for url in (
        "https://shop.test/",
        "https://shop.test/v1.2/docs",
        "https://shop.test/.well-known/security.txt",
        "https://shop.test/a/.hidden/b",
        "https://shop.test/a..b/c",
        "https://shop.test/file.tar.gz",
        "https://shop.test/a%2Eb",
        "https://shop.test/a/%2E%2Ex",
        "https://shop.test/search?path=../x&v=1.0",
        "https://shop.test/caf%C3%A9",
    ):
        assert normalize_url(url) == url, url
    assert normalize_url("https://Shop.test:443/a/") == "https://shop.test/a"


@pytest.mark.parametrize(
    "path",
    ["/public/../private/x", "/./private/x", "/public/%2E%2E/private/x", "/public/%2e%2e/private/x"],
)
def test_a_dotted_path_cannot_pass_a_disallow_rule(path):
    hits = []

    def handler(request):
        hits.append(request.url.raw_path.decode())
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n", headers=TEXT)
        return _page(request.url.path)

    crawler = _crawler(handler)
    try:
        result = crawler.fetch("https://site.test" + path)
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert result.url == "https://site.test/private/x"  # checked, stored and requested as one path
    assert hits == ["/robots.txt"]


# F11


def _apex_and_canonical(log):
    """https://shop-site.org (and http://) 301s everything to https://www.shop-site.org."""

    def handler(request):
        host, path = request.url.host, request.url.path
        log.append((time.monotonic(), host, path))
        if host == "shop-site.org":
            return httpx.Response(301, headers={"location": f"https://www.shop-site.org{path}"})
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers=TEXT)
        return _page(path)

    return handler


def test_robots_txt_is_not_requested_from_a_host_in_long_backoff():
    log = []
    crawler = _crawler(_apex_and_canonical(log))
    crawler._backoff["https://www.shop-site.org"] = (time.monotonic() + 600, 1)
    try:
        deferred = crawler.fetch("https://www.shop-site.org/p1")
        assert "https://www.shop-site.org" not in crawler._robots  # not cached as unreachable
        crawler._backoff.clear()  # the backoff has passed
        later = crawler.fetch("https://www.shop-site.org/p1")
    finally:
        crawler.close()
    assert not deferred.blocked and deferred.robots_status == "deferred"
    assert "not fetched now" in deferred.error
    assert later.status_code == 200 and later.robots_status == "allowed"
    assert [path for _t, _host, path in log] == ["/robots.txt", "/p1"]  # nothing during the backoff


def test_a_robots_txt_redirect_into_a_host_in_backoff_is_deferred_not_blocked():
    log = []
    crawler = _crawler(_apex_and_canonical(log))
    crawler._backoff["https://www.shop-site.org"] = (time.monotonic() + 600, 1)
    try:
        result = crawler.fetch("http://shop-site.org/x")  # its robots.txt redirects to the busy host
    finally:
        crawler.close()
    assert not result.blocked and result.robots_status == "deferred" and "not fetched now" in result.error
    assert "http://shop-site.org" not in crawler._robots
    assert [host for _t, host, _path in log] == ["shop-site.org"]  # the busy host got no request


def test_a_short_backoff_on_the_robots_txt_redirect_target_is_waited_out():
    log = []
    crawler = _crawler(_apex_and_canonical(log))
    until = time.monotonic() + 1.0
    crawler._backoff["https://www.shop-site.org"] = (until, 1)
    try:
        result = crawler.fetch("http://shop-site.org/x")
    finally:
        crawler.close()
    assert result.status_code == 200 and "/x" in result.html
    busy = [(moment, path) for moment, host, path in log if host == "www.shop-site.org"]
    assert busy and all(moment >= until for moment, _path in busy), busy


def test_a_redirected_crawl_sleeps_through_a_short_backoff_instead_of_losing_the_host():
    log = []
    state = {"limited_until": 0.0, "tripped": False}

    def handler(request):
        host, path = request.url.host, request.url.path
        now = time.monotonic()
        if host == "shop-site.org":
            log.append((host, path, 301))
            return httpx.Response(301, headers={"location": f"https://www.shop-site.org{path}"})
        if now < state["limited_until"]:  # a per-client limiter: everything in the window gets 429
            log.append((host, path, 429))
            return httpx.Response(429, headers={"Retry-After": "1"})
        if path == "/" and not state["tripped"]:
            state["tripped"] = True
            state["limited_until"] = now + 1.0
            log.append((host, path, 429))
            return httpx.Response(429, headers={"Retry-After": "1"})
        log.append((host, path, 200))
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers=TEXT)
        if path == "/sitemap.xml":
            locs = "".join(f"<url><loc>https://www.shop-site.org/p{i}</loc></url>" for i in (1, 2, 3))
            body = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{locs}</urlset>'
            return httpx.Response(200, text=body, headers={"content-type": "application/xml"})
        return _page(path)

    crawler = _crawler(handler)
    try:
        batch = crawler.crawl("https://shop-site.org/", sitemap_url=None, max_pages=10)
    finally:
        crawler.close()
    pages = {page.url: page for page in batch.pages}
    for index in (1, 2, 3):
        page = pages[f"https://www.shop-site.org/p{index}"]
        assert page.status_code == 200 and page.html and not page.blocked, page
    assert [entry for entry in log if entry[2] == 429] == [("www.shop-site.org", "/", 429)]  # only the trigger
