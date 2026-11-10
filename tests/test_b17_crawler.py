"""B17 crawler fixes: linear robots.txt matching, robots.txt that cannot be read in full fails
closed once per origin, BOM and percent-encoding per RFC 9309, backoff through redirects, address
fallback under DNS pinning, and the placeholder user agent check. Local sockets only."""

import random
import signal
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpcore
import pytest

import aeo.crawl.fetch as fetch_module
import aeo.crawl.urls as urls_module
from aeo.crawl import guard
from aeo.crawl.fetch import Crawler, user_agent_problem
from aeo.crawl.robots import _matches, parse_robots
from aeo.errors import ProviderConfigError

UA = "RanksimTest/1.0 (+https://ci.ranksim-tests.org/bot)"
PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"
ROOT = Path(__file__).resolve().parents[1]


def _allow_all(crawler: Crawler, origin: str) -> None:
    crawler._robots[origin] = parse_robots("User-agent: *\nAllow: /\n")


class _Server:
    def __init__(self, handler):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    def page(self, body: bytes = b"<html><body>page</body></html>", status: int = 200, kind: str = "text/html"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextmanager
def _time_limit(seconds: float):
    """Interrupt a runaway regex instead of letting the test run for hours (POSIX only)."""
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum, frame):
        raise TimeoutError(f"robots.txt matching took longer than {seconds} s")

    previous = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _reference_match(pattern: str, target: str) -> bool:
    """Slow but obviously correct: the set of target positions reachable after each pattern char."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    reach = {0}
    for char in body:
        if not reach:
            return False
        if char == "*":
            reach = set(range(min(reach), len(target) + 1))
        else:
            reach = {pos + 1 for pos in reach if pos < len(target) and target[pos] == char}
    return bool(reach) and (len(target) in reach if anchored else True)


# C2


def test_wildcard_rules_match_in_linear_time():
    hostile = parse_robots("User-agent: *\nDisallow: /" + "*a" * 20 + "*b\n")
    faceted = parse_robots("User-agent: *\nDisallow: /*?*&*&*&*&*sort=\n")
    query = "&".join(f"f{index}=v" for index in range(600))
    started = time.monotonic()
    with _time_limit(3.0):
        assert hostile.can_fetch(UA, "https://site.example/" + "a" * 5000)  # no 'b', so allowed
        assert not hostile.can_fetch(UA, "https://site.example/" + "a" * 5000 + "b")
        assert faceted.can_fetch(UA, "https://site.example/search?" + query)
        assert not faceted.can_fetch(UA, "https://site.example/search?" + query + "&sort=price")
    assert time.monotonic() - started < 1.0  # the regex matcher took minutes on the first case


def test_the_wildcard_matcher_agrees_with_a_reference():
    rng = random.Random(9309)
    for _ in range(20000):
        pattern = "".join(rng.choice("ab*$/") for _ in range(rng.randint(0, 8)))
        target = "".join(rng.choice("ab/$") for _ in range(rng.randint(0, 10)))
        assert _matches(pattern, target) == _reference_match(pattern, target), (pattern, target)


# C3


def test_a_robots_txt_past_the_deadline_blocks_the_origin_once_and_does_not_raise(monkeypatch):
    hits = []

    class DripRobots(_Handler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path != "/robots.txt":
                return self.page()
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n")
                for _ in range(200):
                    self.wfile.write(b"X-Slow: a\r\n")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

    server = _Server(DripRobots)
    monkeypatch.setattr(fetch_module, "TOTAL_DEADLINE_SECONDS", 1.0)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True, timeout=5.0)
    started = time.monotonic()
    try:
        first = crawler.fetch(server.origin + "/a")
        second = crawler.fetch(server.origin + "/b")
        batch = crawler.crawl(server.origin + "/", sitemap_url=None, max_pages=3)
    finally:
        crawler.close()
        server.close()
    for result in (first, second, *batch.pages):
        assert result.blocked and result.robots_status == "unreachable"
    assert batch.pages
    assert hits == ["/robots.txt"]  # fetched once for the whole crawl; no page was requested
    assert time.monotonic() - started < 4.0  # one deadline, not one per URL


# C4


def test_a_byte_order_mark_does_not_hide_the_first_group():
    assert not parse_robots("﻿User-agent: *\nDisallow: /private\n").can_fetch(UA, "https://h.example/private/x")
    hits = []

    class BomRobots(_Handler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path == "/robots.txt":
                return self.page(b"\xef\xbb\xbfUser-agent: *\nDisallow: /private\n", kind="text/plain")
            return self.page(b"<html><body>secret</body></html>")

    server = _Server(BomRobots)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True)
    try:
        result = crawler.fetch(server.origin + "/private/page")
    finally:
        crawler.close()
        server.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


# C5


def test_a_robots_txt_cut_off_by_the_deadline_fails_closed(monkeypatch):
    hits = []
    head = b"User-agent: *\nAllow: /\n"
    tail = b"User-agent: RanksimTest\nDisallow: /\n"

    class StallRobots(_Handler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path != "/robots.txt":
                return self.page(b"<html><body>secret</body></html>")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(head) + len(tail)))
            self.end_headers()
            self.wfile.write(head)
            self.wfile.flush()
            time.sleep(2.5)  # the rule for this crawler comes after the deadline
            try:
                self.wfile.write(tail)
            except OSError:
                pass

    server = _Server(StallRobots)
    monkeypatch.setattr(fetch_module, "TOTAL_DEADLINE_SECONDS", 1.0)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True, timeout=5.0)
    try:
        first = crawler.fetch(server.origin + "/private")
        second = crawler.fetch(server.origin + "/other")
    finally:
        crawler.close()
        server.close()
    for result in (first, second):
        assert result.blocked and result.robots_status == "unreachable"
    assert hits == ["/robots.txt"]


# C6


def test_backoff_applies_to_a_busy_host_reached_through_a_redirect():
    busy_hits = []

    class Busy(_Handler):
        def do_GET(self):  # noqa: N802
            busy_hits.append(self.path)
            self.send_response(429)
            self.send_header("Retry-After", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

    busy = _Server(Busy)

    class Redirect(_Handler):
        def do_GET(self):  # noqa: N802
            self.send_response(301)
            self.send_header("Location", busy.origin + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

    front = _Server(Redirect)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True)
    mixed = Crawler(user_agent=UA, delay_seconds=0, allow_private=True)
    for item in (crawler, mixed):
        _allow_all(item, busy.origin)
        _allow_all(item, front.origin)
    try:
        results = [crawler.fetch(f"{front.origin}/r{index}") for index in range(5)]
        via_redirect = len(busy_hits)
        busy_hits.clear()
        direct = mixed.fetch(busy.origin + "/first")
        after = mixed.fetch(front.origin + "/after")
    finally:
        crawler.close()
        mixed.close()
        front.close()
        busy.close()
    assert via_redirect == 1  # the host asked for 10 minutes; the redirect does not get around it
    assert results[0].status_code == 429
    assert all("not fetched now" in (result.error or "") for result in results[1:])
    assert direct.status_code == 429 and "not fetched now" in (after.error or "")
    assert busy_hits == ["/first"]


# C19


class _Stream(httpcore.NetworkStream):
    def read(self, max_bytes, timeout=None):
        return b""

    def write(self, buffer, timeout=None):
        pass

    def close(self):
        pass


def test_the_backend_tries_each_vetted_address_in_order(monkeypatch):
    seen = []

    class Inner(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen.append(host)
            if host == PUBLIC_V6:
                raise httpcore.ConnectError("[Errno 101] Network is unreachable")
            return _Stream()

    def answers(host, port, *args, **kwargs):
        table = {
            "dual.example": [(socket.AF_INET6, (PUBLIC_V6, port, 0, 0)), (socket.AF_INET, (PUBLIC, port))],
            "mixed.example": [(socket.AF_INET, (PUBLIC, port)), (socket.AF_INET, ("10.0.0.5", port))],
        }
        return [(family, socket.SOCK_STREAM, 6, "", address) for family, address in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", answers)
    backend = guard.GuardedBackend(Inner(), check_ip=True)
    stream = backend.connect_tcp("dual.example", 443)
    assert isinstance(stream, guard.DeadlineStream)
    assert seen == [PUBLIC_V6, PUBLIC]  # the first address is dead; the second vetted one answers
    seen.clear()
    with pytest.raises(guard.UnsafeConnect):
        backend.connect_tcp("mixed.example", 443)
    assert seen == []  # every address is vetted before any connection


def test_address_fallback_stops_at_the_deadline(monkeypatch):
    now = [100.0]
    seen = []

    class Inner(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen.append((host, timeout))
            now[0] = 111.0
            raise httpcore.ConnectTimeout("timed out")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.35", port)),
        ],
    )
    backend = guard.GuardedBackend(Inner(), check_ip=True)
    backend.clock = lambda: now[0]
    backend.deadline = 110.0
    with pytest.raises(httpcore.ConnectTimeout):
        backend.connect_tcp("slow.example", 443, timeout=20.0)
    assert seen == [(PUBLIC, 10.0)]  # capped at the time left; no second try after the deadline


def test_a_crawl_reaches_a_host_whose_first_address_does_not_answer(monkeypatch):
    class Ok(_Handler):
        def do_GET(self):  # noqa: N802
            self.page(b"<html><body>ok</body></html>")

    server = _Server(Ok)
    real = socket.getaddrinfo

    def dual(host, port, *args, **kwargs):
        if host == "dual.test":
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port or 0, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port or 0)),
            ]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", dual)
    # Loopback stands in for a public host so the pinned path runs; nothing listens on ::1.
    monkeypatch.setattr(guard, "_is_public_ip", lambda ip: True)
    monkeypatch.setattr(urls_module, "_is_public_ip", lambda ip: True)
    origin = f"http://dual.test:{server.httpd.server_port}"
    crawler = Crawler(user_agent=UA, delay_seconds=0)
    assert all(item.check_ip for item in crawler._guards)
    _allow_all(crawler, origin)
    try:
        result = crawler.fetch(origin + "/page")
    finally:
        crawler.close()
        server.close()
    assert result.status_code == 200 and result.error is None and "ok" in result.html


# U0


def test_percent_encoding_is_normalised_before_matching():
    def allowed(robots: str, path: str) -> bool:
        return parse_robots("User-agent: *\n" + robots).can_fetch(UA, "https://site.example" + path)

    assert not allowed("Disallow: /~joe\n", "/%7Ejoe/x")  # an escaped unreserved octet is decoded
    assert not allowed("Disallow: /%7Ejoe\n", "/~joe/x")
    assert not allowed("Disallow: /caf%c3%a9\n", "/café/menu")  # hex case does not matter
    assert not allowed("Disallow: /caf%C3%A9\n", "/caf%c3%a9/menu")
    assert not allowed("Disallow: /caf%C3%A9\n", "/café/menu")
    assert allowed("Disallow: /a%2fb\n", "/a/b/c")  # a reserved character stays encoded
    assert not allowed("Disallow: /a%2fb\n", "/a%2Fb/c")
    assert allowed("Disallow: /%2A\n", "/anything")  # an escaped '*' is a literal, not a wildcard


# U1


def test_punctuation_after_a_placeholder_contact_does_not_make_it_real():
    for agent in (
        "MyBot/1.0 (+https://example.com; bot@example.com)",
        "MyBot/1.0 (+https://example.com.)",
        "MyBot/1.0 (+https://example.com, https://bot.example)",
        "MyBot/1.0 <https://example.com>",
        "RanksimBot/0.1 (+https://yourdomain.com/bot; you@yourdomain.com)",  # the refusal message's example
    ):
        assert user_agent_problem(agent) is not None, agent
    assert user_agent_problem("MyBot/1.0 (+https://ranksim-crawler.org; bot@ranksim-crawler.org)") is None
    assert user_agent_problem("MyBot/1.0 (+https://ranksim-crawler.org.)") is None


# U13


def test_the_user_agent_check_and_its_exemption_are_documented(monkeypatch):
    placeholder = "AEOSimulatorBot/0.1 (+https://example.com/aeo-simulator)"
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port or 0))]
    )
    own_site = Crawler(user_agent=placeholder, delay_seconds=0)
    try:
        with pytest.raises(ProviderConfigError, match="CRAWL_USER_AGENT"):
            own_site.crawl("https://my-own-site.org/", sitemap_url=None, max_pages=1)  # the main app's own crawl
    finally:
        own_site.close()
    assert Crawler(user_agent=placeholder, allow_private=True)._user_agent_problem is None  # exempt
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    paragraphs = [block for block in readme.split("\n\n") if "CRAWL_USER_AGENT" in block]
    assert any("site you submit" in block for block in paragraphs)
    assert paragraphs and all("CRAWL_ALLOW_PRIVATE=true" in block for block in paragraphs)


# Review follow-up


@pytest.mark.parametrize("charset", ["ISO-8859-1", "windows-1252"])
def test_a_byte_order_mark_wins_over_a_declared_single_byte_charset(charset):
    hits = []

    class BomRobots(_Handler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path == "/robots.txt":
                body = "﻿User-agent: *\nDisallow: /private\nDisallow: /café\n".encode("utf-8")
                return self.page(body, kind=f"text/plain; charset={charset}")
            return self.page(b"<html><body>secret</body></html>")

    server = _Server(BomRobots)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True)
    try:
        private = crawler.fetch(server.origin + "/private/page")
        cafe = crawler.fetch(server.origin + "/café/menu")  # the rest of the file is read as UTF-8
    finally:
        crawler.close()
        server.close()
    assert private.blocked and private.robots_status == "disallowed"
    assert cafe.blocked and cafe.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


def test_a_declared_charset_still_applies_without_a_byte_order_mark():
    class Latin1Robots(_Handler):
        def do_GET(self):  # noqa: N802
            if self.path == "/robots.txt":
                return self.page("User-agent: *\nDisallow: /café\n".encode("latin-1"), kind="text/plain; charset=ISO-8859-1")
            return self.page(b"<html><body>page</body></html>")

    server = _Server(Latin1Robots)
    crawler = Crawler(user_agent=UA, delay_seconds=0, allow_private=True)
    try:
        result = crawler.fetch(server.origin + "/café/menu")
    finally:
        crawler.close()
        server.close()
    assert result.blocked and result.robots_status == "disallowed"


def test_an_ipv6_literal_contact_is_read_not_raised():
    public = "MyBot/1.0 (+https://[2606:4700::6810:85e5]/bot)"
    assert user_agent_problem(public) is None
    assert user_agent_problem("MyBot/1.0 (+https://[2606:4700::6810:85e5]/bot; bot@ranksim-crawler.org)") is None
    assert user_agent_problem("MyBot/1.0 (+https://[2606:4700::6810:85e5]:8443/bot)") is None
    Crawler(user_agent=public).close()  # the constructor checks the user agent and must not raise
    for agent in (
        "MyBot/1.0 (+https://[not-an-address]/bot)",
        "MyBot/1.0 (+https://[2606:4700::1/bot)",
        "MyBot/1.0 (+https://[::1]/bot)",  # loopback, like localhost
        "MyBot/1.0 (+https://[2001:db8::1]/bot)",  # documentation range, like example.com
        "MyBot/1.0 (+http://127.0.0.1/bot)",
    ):
        assert user_agent_problem(agent) is not None, agent
    assert user_agent_problem("MyBot/1.0 (+https://[::1]/bot; bot@ranksim-crawler.org)") is None


def test_yourdomain_subdomains_are_placeholders_too():
    for agent in (
        "MyBot/1.0 (+https://www.yourdomain.com/bot)",
        "MyBot/1.0 (bot@mail.yourdomain.com)",
    ):
        assert "placeholder" in user_agent_problem(agent), agent
    assert user_agent_problem("MyBot/1.0 (+https://www.notyourdomain.com/bot)") is None
