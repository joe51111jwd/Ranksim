"""Crawler hardening: SSRF (incl. DNS rebinding), timeouts, retries, redirects, sitemaps, script-drawn pages.

Every network call is faked: httpx.MockTransport, a monkeypatched socket.getaddrinfo and a socket connect stub.
"""

from __future__ import annotations

import gzip
import socket

import httpcore
import httpx
import pytest

from aeo.crawl import guard
from aeo.crawl.fetch import (
    DEFAULT_USER_AGENT,
    MAX_REDIRECTS,
    RETRY_AFTER_CAP,
    Crawler,
    Fetched,
)
from aeo.crawl.guard import GuardedBackend, guarded_transport
from aeo.crawl.sitemap import parse_sitemap
from aeo.crawl.thin import script_drawn
from aeo.crawl.urls import assert_safe_url
from aeo.errors import UnsafeURL

PUBLIC_V4 = "93.184.216.34"


def _info(ip: str, port: int | None = 80):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, port or 0, 0, 0) if family == socket.AF_INET6 else (ip, port or 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


def fake_dns(monkeypatch, table):
    """Route socket.getaddrinfo through ``table``: host -> ip, list of ips, or callable(host) -> ip(s)."""
    calls: list[str] = []

    def getaddrinfo(host, port, *args, **kwargs):
        calls.append(host)
        answer = table.get(host)
        if callable(answer):
            answer = answer(host)
        if answer is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        ips = [answer] if isinstance(answer, str) else answer
        return [_info(ip, port) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return calls


def mock_crawler(handler, **kwargs) -> Crawler:
    kwargs.setdefault("delay_seconds", 0)
    kwargs.setdefault("sleep", lambda seconds: None)
    return Crawler(user_agent=kwargs.pop("user_agent", "t"), client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


# -- SSRF: URL checks ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://172.16.3.4/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://240.0.0.1/",
        "http://[::1]/",
        "http://[::]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[2002:7f00:1::]/",
        "http://[ff02::1]/",
        "http://localhost/",
        "http://localhost./",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://db.internal/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://127.1/",
        "http://017700000001/",
    ],
)
def test_private_and_trick_hosts_are_refused_without_dns(url, monkeypatch):
    calls = fake_dns(monkeypatch, {})
    with pytest.raises(UnsafeURL):
        assert_safe_url(url, resolve=True)
    assert calls == []  # refused by name or literal, before any lookup


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://example.com/", "javascript:alert(1)", "http://user:pw@example.com/", "http://user@example.com/"])
def test_non_http_and_credential_urls_are_refused(url):
    with pytest.raises(ValueError):
        assert_safe_url(url, resolve=False)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.100.100.200", "::1", "fe80::1", "::ffff:127.0.0.1", "fd00::1"])
def test_names_that_resolve_private_are_refused(ip, monkeypatch):
    fake_dns(monkeypatch, {"evil.example": [PUBLIC_V4, ip]})
    with pytest.raises(UnsafeURL):
        assert_safe_url("http://evil.example/", resolve=True)


def test_public_name_and_unusual_port_are_allowed(monkeypatch):
    fake_dns(monkeypatch, {"ok.example": [PUBLIC_V4, "2606:4700::1111"]})
    assert assert_safe_url("http://ok.example:8443/a") == "http://ok.example:8443/a"


def test_crawler_refuses_numeric_trick_before_any_request():
    def handler(request):
        raise AssertionError(f"request sent to {request.url}")

    crawler = mock_crawler(handler)
    for url in ("http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://127.1/"):
        with pytest.raises(UnsafeURL):
            crawler.fetch(url)


@pytest.mark.parametrize(
    "location", ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8080/admin", "http://[::ffff:10.0.0.1]/", "http://0x7f000001/", "http://internal.example/", "file:///etc/passwd"]
)
def test_redirect_to_private_target_is_refused(location, monkeypatch):
    fake_dns(monkeypatch, {"internal.example": "10.0.0.7"})
    requested: list[str] = []

    def handler(request):
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": location})

    crawler = mock_crawler(handler)
    with pytest.raises((UnsafeURL, ValueError)):
        result = crawler.fetch(f"http://{PUBLIC_V4}/go")
        if result.error:  # a non-http scheme ends as an error, never as a request
            raise ValueError(result.error)
    assert all("169.254" not in url and "127.0.0.1" not in url and "internal" not in url for url in requested)


def test_unresolvable_host_is_an_error_not_a_refusal(monkeypatch):
    fake_dns(monkeypatch, {})
    crawler = mock_crawler(lambda request: httpx.Response(200))
    result = crawler.fetch("http://nowhere.example/")
    assert result.error and "Could not resolve" in result.error


# -- SSRF: connect-time guard (DNS rebinding) ---------------------------------------------------------------------


def test_rebinding_dns_is_refused_at_connect_time(monkeypatch):
    """First lookup public (passes assert_safe_url), second lookup private: the backend must refuse."""
    answers = iter([PUBLIC_V4, "169.254.169.254"])
    calls = fake_dns(monkeypatch, {"rebind.example": lambda host: next(answers)})
    connects: list[tuple] = []
    monkeypatch.setattr(guard, "_connect", lambda *args: connects.append(args) or pytest.fail("socket connected"))

    assert assert_safe_url("http://rebind.example/") == "http://rebind.example/"  # first answer is public
    with pytest.raises(UnsafeURL):
        GuardedBackend().connect_tcp("rebind.example", 80, timeout=1.0)
    assert calls == ["rebind.example", "rebind.example"] and connects == []


def test_rebinding_through_the_default_transport(monkeypatch):
    """The Crawler's own client (no injected client) refuses the rebinding answer during the real request."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    lookups: list[object] = []

    def getaddrinfo(host, port, *args, **kwargs):
        # Every pre-request check (port None) sees a public answer; the connect-time lookup (with a port)
        # gets the rebinding answer.
        lookups.append(port)
        return [_info(PUBLIC_V4 if port is None else "127.0.0.1", port)]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(guard, "_connect", lambda *args: pytest.fail("socket connected"))
    crawler = Crawler(user_agent="t", delay_seconds=0, sleep=lambda s: None)
    try:
        with pytest.raises(UnsafeURL, match="non-public"):
            crawler.fetch("http://rebind.example/")
    finally:
        crawler.close()
    assert None in lookups and 80 in lookups  # the pre-check passed; the connect-time lookup refused


def test_guarded_backend_connects_only_to_the_vetted_address(monkeypatch):
    """Positive path: the socket is opened to the vetted numeric address, never to the name."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    calls = fake_dns(monkeypatch, {"site.example": PUBLIC_V4})
    seen: list[tuple] = []

    def connect(family, socktype, proto, sockaddr, timeout, source):
        seen.append(sockaddr)
        ours, theirs = socket.socketpair()
        body = b"<html><body><p>hello from the vetted address</p></body></html>"
        theirs.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        connect.keep.append(theirs)
        return ours

    connect.keep = []
    monkeypatch.setattr(guard, "_connect", connect)
    client = httpx.Client(transport=guarded_transport())
    try:
        response = client.get("http://site.example/page")
    finally:
        client.close()
        for sock in connect.keep:
            sock.close()
    assert response.status_code == 200 and b"vetted address" in response.content
    assert seen == [(PUBLIC_V4, 80)]
    assert calls == ["site.example"]


def test_guarded_backend_refuses_ip_literals_and_tricks(monkeypatch):
    fake_dns(monkeypatch, {"127.0.0.1": "127.0.0.1"})
    monkeypatch.setattr(guard, "_connect", lambda *args: pytest.fail("socket connected"))
    backend = GuardedBackend()
    for host in ("127.0.0.1", "::1", "[::1]", "169.254.169.254", "0x7f000001", "2130706433", "localhost", "metadata.google.internal"):
        with pytest.raises(UnsafeURL):
            backend.connect_tcp(host, 80)


def test_guarded_backend_dns_failure_is_a_connect_error(monkeypatch):
    fake_dns(monkeypatch, {})
    with pytest.raises(httpcore.ConnectError):
        GuardedBackend().connect_tcp("gone.example", 443)


def test_injected_client_and_allow_private_are_left_alone():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert Crawler(user_agent="t", client=client).client is client
    lab = Crawler(user_agent="t", allow_private=True)
    try:
        backend = getattr(lab.client._transport, "_pool", None)
        assert not isinstance(getattr(backend, "_network_backend", None), GuardedBackend)
    finally:
        lab.close()
    guarded = Crawler(user_agent="t")
    try:
        assert isinstance(guarded.client._transport._pool._network_backend, GuardedBackend)
    finally:
        guarded.close()


# -- retries, backoff, timeouts -----------------------------------------------------------------------------------


def test_503_then_200_is_retried_with_backoff():
    hits = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body><p>ok</p></body></html>")

    crawler = mock_crawler(handler, sleep=sleeps.append)
    result = crawler.fetch(f"http://{PUBLIC_V4}/page")
    assert result.status_code == 200 and result.error is None and "ok" in result.html
    assert hits["n"] == 2 and len(sleeps) == 1 and 0.25 <= sleeps[0] <= 0.5


def test_backoff_grows_and_gives_up_after_max_retries():
    sleeps: list[float] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(502)

    crawler = mock_crawler(handler, sleep=sleeps.append, max_retries=3)
    result = crawler.fetch(f"http://{PUBLIC_V4}/down")
    assert result.status_code == 502 and result.error == "HTTP 502"
    assert len(sleeps) == 3 and sleeps[0] <= 0.5 and 1.0 <= sleeps[2] <= 2.0


def test_retry_after_is_honoured_and_capped():
    sleeps: list[float] = []
    plan = [httpx.Response(429, headers={"retry-after": "2"}), httpx.Response(503, headers={"retry-after": "120"}), httpx.Response(200, text="<p>fine</p>")]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return plan.pop(0)

    crawler = mock_crawler(handler, sleep=sleeps.append, timeout=60)
    result = crawler.fetch(f"http://{PUBLIC_V4}/busy")
    assert result.status_code == 200
    assert sleeps == [2.0, RETRY_AFTER_CAP]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_other_4xx_are_not_retried(status):
    hits = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        return httpx.Response(status)

    sleeps: list[float] = []
    result = mock_crawler(handler, sleep=sleeps.append).fetch(f"http://{PUBLIC_V4}/missing")
    assert result.error == f"HTTP {status}" and hits["n"] == 1 and sleeps == []


def test_connect_errors_and_read_timeouts_are_retried():
    plan: list = [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.Response(200, text="<p>up</p>")]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        item = plan.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    sleeps: list[float] = []
    result = mock_crawler(handler, sleep=sleeps.append).fetch(f"http://{PUBLIC_V4}/flaky")
    assert result.status_code == 200 and len(sleeps) == 2


def test_unsafe_url_is_never_retried():
    hits = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        return httpx.Response(307, headers={"location": "http://169.254.169.254/"})

    sleeps: list[float] = []
    with pytest.raises(UnsafeURL):
        mock_crawler(handler, sleep=sleeps.append).fetch(f"http://{PUBLIC_V4}/x")
    assert hits["n"] == 1 and sleeps == []


def test_wall_clock_cap_ends_a_slow_drip_body():
    now = {"t": 0.0}

    def clock():
        return now["t"]

    def body():
        for _ in range(1000):
            now["t"] += 1.0  # every chunk takes a second
            yield b"<p>drip</p>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body())

    result = mock_crawler(handler, clock=clock, timeout=5).fetch(f"http://{PUBLIC_V4}/slow")
    assert result.error and "timed out" in result.error and result.html == ""
    assert now["t"] <= 7


def test_retries_stop_at_the_wall_clock_cap():
    sleeps: list[float] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(503, headers={"retry-after": "8"})

    result = mock_crawler(handler, sleep=sleeps.append, timeout=5).fetch(f"http://{PUBLIC_V4}/x")
    assert result.status_code == 503 and sleeps == []  # an 8 s wait would pass the 5 s cap, so it gives up


def test_default_client_has_separate_connect_and_read_timeouts():
    crawler = Crawler(timeout=20, connect_timeout=4, read_timeout=12)
    try:
        timeout = crawler.client.timeout
        assert (timeout.connect, timeout.read) == (4, 12)
        assert crawler.user_agent == DEFAULT_USER_AGENT
    finally:
        crawler.close()


# -- redirects ----------------------------------------------------------------------------------------------------


def test_redirect_chain_is_recorded():
    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        if path == "/b":
            return httpx.Response(302, headers={"location": f"https://{PUBLIC_V4}/c"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>end</p>")

    result = mock_crawler(handler).fetch(f"http://{PUBLIC_V4}/a")
    assert result.redirects == [f"http://{PUBLIC_V4}/b", f"https://{PUBLIC_V4}/c"]
    assert result.final_url == f"https://{PUBLIC_V4}/c" and result.status_code == 200


def test_no_redirect_means_empty_chain():
    assert Fetched(url="u", final_url="u").redirects == []
    result = mock_crawler(lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="<p>x</p>")).fetch(f"http://{PUBLIC_V4}/")
    assert result.redirects == []


def test_redirect_loop_is_detected():
    hits = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        return httpx.Response(302, headers={"location": "/b" if request.url.path == "/a" else "/a"})

    result = mock_crawler(handler).fetch(f"http://{PUBLIC_V4}/a")
    assert result.error and "loop" in result.error.lower()
    assert result.redirects == [f"http://{PUBLIC_V4}/b", f"http://{PUBLIC_V4}/a"] and hits["n"] == 2


def test_too_many_redirects():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        n = int(request.url.path.strip("/") or 0)
        return httpx.Response(302, headers={"location": f"/{n + 1}"})

    result = mock_crawler(handler).fetch(f"http://{PUBLIC_V4}/0")
    assert result.error and "Too many redirects" in result.error and len(result.redirects) == MAX_REDIRECTS


# -- sitemaps -----------------------------------------------------------------------------------------------------

URLSET = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'
INDEX = '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</sitemapindex>'


def _locs(tag, urls):
    return "".join(f"<{tag}><loc>{url}</loc></{tag}>" for url in urls)


def test_parse_sitemap_is_backward_compatible_and_reads_gzip_bytes():
    xml = URLSET.format(_locs("url", ["https://site.example/a"]))
    assert parse_sitemap(xml) == ("urlset", ["https://site.example/a"])
    assert parse_sitemap(gzip.compress(xml.encode())) == ("urlset", ["https://site.example/a"])
    with pytest.raises(ValueError):
        parse_sitemap("<not xml")
    with pytest.raises(ValueError):
        parse_sitemap('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>')


def _site_handler(files: dict[str, tuple[str, bytes | str]], robots: str = ""):
    fetched: list[str] = []

    def handler(request):
        path = request.url.path
        fetched.append(path)
        if path == "/robots.txt":
            return httpx.Response(200, text=robots) if robots else httpx.Response(404)
        if path in files:
            content_type, body = files[path]
            return httpx.Response(200, headers={"content-type": content_type}, content=body if isinstance(body, bytes) else body.encode())
        if path.startswith("/p"):
            return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><body><p>page {path}</p></body></html>")
        return httpx.Response(404)

    return handler, fetched


def test_nested_sitemap_index_and_gzip_child():
    base = "https://site.example"
    files = {
        "/sitemap.xml": ("application/xml", INDEX.format(_locs("sitemap", [f"{base}/index-2.xml", f"{base}/plain.xml"]))),
        "/index-2.xml": ("application/xml", INDEX.format(_locs("sitemap", [f"{base}/deep.xml.gz"]))),
        "/deep.xml.gz": ("application/x-gzip", gzip.compress(URLSET.format(_locs("url", [f"{base}/p1", f"{base}/p2"])).encode())),
        "/plain.xml": ("application/xml", URLSET.format(_locs("url", [f"{base}/p3"]))),
    }
    handler, fetched = _site_handler(files)
    crawler = mock_crawler(handler, allow_private=True)
    urls = crawler._seed_urls(f"{base}/", None, 50, [])
    assert urls == [f"{base}/", f"{base}/p1", f"{base}/p2", f"{base}/p3"]
    assert "/deep.xml.gz" in fetched


def test_sitemap_index_deeper_than_two_levels_is_ignored():
    base = "https://site.example"
    files = {
        "/sitemap.xml": ("application/xml", INDEX.format(_locs("sitemap", [f"{base}/l1.xml"]))),
        "/l1.xml": ("application/xml", INDEX.format(_locs("sitemap", [f"{base}/l2.xml"]))),
        "/l2.xml": ("application/xml", INDEX.format(_locs("sitemap", [f"{base}/l3.xml"]))),
        "/l3.xml": ("application/xml", URLSET.format(_locs("url", [f"{base}/p9"]))),
    }
    handler, fetched = _site_handler(files)
    urls = mock_crawler(handler, allow_private=True)._seed_urls(f"{base}/", None, 50, [])
    assert urls == [f"{base}/"] and "/l3.xml" not in fetched


def test_gzip_sitemap_by_content_and_robots_sitemap_seed():
    base = "https://site.example"
    # Served as text/xml with no .gz name and no Content-Encoding: only the gzip magic bytes give it away.
    files = {"/maps/site.xml": ("text/xml", gzip.compress(URLSET.format(_locs("url", [f"{base}/p7"])).encode()))}
    handler, fetched = _site_handler(files, robots=f"User-agent: *\nAllow: /\nSitemap: {base}/maps/site.xml\n")
    urls = mock_crawler(handler, allow_private=True)._seed_urls(f"{base}/", None, 50, [])
    assert urls == [f"{base}/", f"{base}/p7"]
    assert "/sitemap.xml" not in fetched  # robots named the sitemap, so the default path is not guessed


def test_sitemap_caps_on_urls_and_children(monkeypatch):
    import aeo.crawl.fetch as fetch_mod

    base = "https://site.example"
    monkeypatch.setattr(fetch_mod, "MAX_SITEMAP_CHILDREN", 3)
    children = [f"{base}/c{i}.xml" for i in range(10)]
    files = {"/sitemap.xml": ("application/xml", INDEX.format(_locs("sitemap", children)))}
    for i in range(10):
        files[f"/c{i}.xml"] = ("application/xml", URLSET.format(_locs("url", [f"{base}/p{i}-{j}" for j in range(2)])))
    handler, fetched = _site_handler(files)
    urls = mock_crawler(handler, allow_private=True)._seed_urls(f"{base}/", None, 100, [])
    assert len([path for path in fetched if path.startswith("/c")]) == 3 and len(urls) == 7
    handler, fetched = _site_handler(files)
    urls = mock_crawler(handler, allow_private=True)._seed_urls(f"{base}/", None, 3, [])
    assert len(urls) == 4  # start page + 3 sitemap URLs (max_pages)


def test_gzip_bomb_is_capped(monkeypatch):
    import aeo.crawl.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "MAX_GUNZIP_BYTES", 1000)
    bomb = gzip.compress(b"<urlset>" + b" " * 5_000_000 + b"</urlset>")
    handler, _ = _site_handler({"/big.xml.gz": ("application/gzip", bomb)})
    result = mock_crawler(handler, allow_private=True).fetch("https://site.example/big.xml.gz")
    assert len(result.html) <= 1000


# -- script-drawn pages -------------------------------------------------------------------------------------------

SPA_SHELL = """<!doctype html><html><head><title>Paving App</title>
<script defer src="/static/js/main.4f2a.js"></script><link rel="stylesheet" href="/static/css/main.css"></head>
<body><noscript>You need to enable JavaScript to run this app.</noscript><div id="root"></div></body></html>"""

ARTICLE = (
    "<!doctype html><html><head><title>How to schedule a paving crew</title><script src='/analytics.js'></script></head><body>"
    "<header><nav><a href='/'>Home</a></nav></header><main><article><h1>How to schedule a paving crew</h1>"
    + "<p>Paving crews plan work around asphalt plant hours, weather windows and truck availability. " * 8
    + "</p></article></main></body></html>"
)


@pytest.mark.parametrize(
    "html",
    [
        SPA_SHELL,
        '<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">' + '{"props":{}}' * 300 + "</script></body></html>",
        "<html><body><app-root></app-root><script src='runtime.js'></script><script src='main.js'></script></body></html>",
        "<html><body><div id='app'><!-- mounted by Vue --></div><script src='/app.js'></script></body></html>",
        "<html><body><div>Loading</div><script>" + "window.__STATE__ = 1;" * 400 + "</script></body></html>",
    ],
)
def test_script_drawn_shells_are_flagged(html):
    finding = script_drawn(html)
    assert finding is not None
    assert finding["visible_words"] < 10 and finding["reason"]
    assert set(finding) >= {"visible_words", "script_bytes", "reason"}


@pytest.mark.parametrize(
    "html",
    [
        ARTICLE,
        '<html><body><div id="__next"><h1>Server rendered</h1><p>' + "real words here " * 30 + "</p></div><script src='/_next/main.js'></script></body></html>",
        "<html><body><p>Page not found.</p></body></html>",
        "",
    ],
)
def test_normal_pages_are_not_flagged(html):
    assert script_drawn(html) is None


def test_fetch_sets_script_drawn_on_html_only():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/app":
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=SPA_SHELL)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=ARTICLE)

    crawler = mock_crawler(handler)
    assert crawler.fetch(f"http://{PUBLIC_V4}/app").script_drawn is True
    assert crawler.fetch(f"http://{PUBLIC_V4}/article").script_drawn is False


# -- robots -------------------------------------------------------------------------------------------------------


def test_robots_disallow_is_still_honoured_for_the_bot_user_agent():
    requested: list[str] = []

    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: RanksimBot\nDisallow: /private\n\nUser-agent: *\nDisallow:\n")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    crawler = mock_crawler(handler, user_agent=DEFAULT_USER_AGENT)
    blocked = crawler.fetch(f"http://{PUBLIC_V4}/private/page")
    assert blocked.blocked and blocked.robots_status == "disallowed" and "/private/page" not in requested
    assert crawler.fetch(f"http://{PUBLIC_V4}/public").robots_status == "allowed"
    assert requested.count("/robots.txt") == 1


def test_robots_5xx_keeps_todays_behaviour():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(500)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    result = mock_crawler(handler).fetch(f"http://{PUBLIC_V4}/x")
    assert result.error is None and result.robots_status == "allowed" and result.html
