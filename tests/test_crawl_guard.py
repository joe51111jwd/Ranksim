"""B3: the crawler connects only to the address it vetted (DNS rebinding), its deadline covers
response headers, and it honours 429/503 backoff per host. Local sockets only."""

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import pytest

import aeo.crawl.fetch as fetch_module
from aeo.crawl import guard
from aeo.crawl.fetch import Crawler, retry_after_seconds
from aeo.crawl.robots import parse_robots
from aeo.errors import UnsafeURL

PUBLIC = "93.184.216.34"


def _allow_all(crawler: Crawler, origin: str) -> None:
    crawler._robots[origin] = parse_robots("User-agent: *\nAllow: /\n")


def test_a_name_that_rebinds_to_a_private_address_is_refused_at_connect(monkeypatch):
    real = socket.getaddrinfo

    def rebinding(host, port, *args, **kwargs):
        if host == "rebind.example":
            # The URL check (no port) sees a public address; the connection (with a port) sees loopback.
            address = PUBLIC if port is None else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    crawler = Crawler(user_agent="ranksim-test", delay_seconds=0)
    _allow_all(crawler, "http://rebind.example")
    try:
        with pytest.raises(UnsafeURL, match="non-public address at connect time"):
            crawler.fetch("http://rebind.example/page")
    finally:
        crawler.close()


def test_the_backend_connects_to_the_vetted_address_and_refuses_mixed_answers(monkeypatch):
    seen = []

    class Inner(httpcore.NetworkBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            seen.append(host)
            raise httpcore.ConnectError("stop here")

    def answers(host, port, *args, **kwargs):
        table = {"good.example": [PUBLIC], "mixed.example": [PUBLIC, "10.0.0.5"]}
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in table[host]]

    monkeypatch.setattr(socket, "getaddrinfo", answers)
    backend = guard.GuardedBackend(Inner(), check_ip=True)
    with pytest.raises(httpcore.ConnectError, match="stop here"):
        backend.connect_tcp("good.example", 443)
    assert seen == [PUBLIC]  # the vetted IP, not the name, so DNS is not asked again
    with pytest.raises(guard.UnsafeConnect):
        backend.connect_tcp("mixed.example", 443)
    with pytest.raises(guard.UnsafeConnect):
        backend.connect_tcp("169.254.169.254", 80)


def test_the_guard_is_installed_on_the_crawlers_own_client():
    crawler = Crawler(user_agent="ranksim-test", allow_private=False)
    try:
        backends = [crawler.client._transport._pool._network_backend]
        assert all(isinstance(item, guard.GuardedBackend) and item.check_ip for item in backends)
    finally:
        crawler.close()


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


def test_the_deadline_covers_headers_that_drip(monkeypatch):
    class Drip(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            line = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Slow: " + b"a" * 200
            for byte in line:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.02)

        def log_message(self, *args):
            pass

    server = _Server(Drip)
    monkeypatch.setattr(fetch_module, "TOTAL_DEADLINE_SECONDS", 1.0)
    crawler = Crawler(user_agent="ranksim-test", delay_seconds=0, allow_private=True, timeout=5.0)
    _allow_all(crawler, server.origin)
    started = time.monotonic()
    try:
        result = crawler.fetch(server.origin + "/slow")
    finally:
        crawler.close()
        server.close()
    assert "No complete response headers within 1 s" in (result.error or "")
    assert time.monotonic() - started < 4.0  # well under the 5 s per-read timeout times the drips


def test_a_429_storm_gets_one_request_per_backoff(monkeypatch):
    hits = []

    class Busy(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(429)
            self.send_header("Retry-After", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = _Server(Busy)
    crawler = Crawler(user_agent="ranksim-test", delay_seconds=0, allow_private=True)
    _allow_all(crawler, server.origin)
    try:
        first = crawler.fetch(server.origin + "/1")
        rest = [crawler.fetch(f"{server.origin}/{index}") for index in range(2, 6)]
    finally:
        crawler.close()
        server.close()
    assert first.status_code == 429 and "600 s" in first.error
    assert hits == ["/1"]  # the host asked for 10 minutes; nothing else was sent
    assert all("not fetched now" in result.error for result in rest)


def test_short_waits_are_slept_and_backoff_doubles_without_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(fetch_module.time, "sleep", lambda seconds: slept.append(round(seconds, 1)))
    hits = []

    class Flaky(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = _Server(Flaky)
    crawler = Crawler(user_agent="ranksim-test", delay_seconds=0, allow_private=True)
    _allow_all(crawler, server.origin)
    try:
        for index in range(3):
            crawler.fetch(f"{server.origin}/{index}")
    finally:
        crawler.close()
        server.close()
    assert len(hits) == 3
    assert slept[:2] == [2.0, 4.0]  # waits before the 2nd and 3rd request: 2 s, then 4 s


def test_retry_after_accepts_seconds_and_dates():
    assert retry_after_seconds("120") == 120.0
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412480.0 - 30) == 30.0
    assert retry_after_seconds("soon") is None and retry_after_seconds(None) is None
    assert retry_after_seconds("999999") == fetch_module.MAX_RETRY_AFTER
