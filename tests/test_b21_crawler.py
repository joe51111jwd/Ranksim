"""B21 crawler fixes. G1: a host that rebinds to a non-public address is an unsafe URL whichever DNS
lookup first sees the private answer, also for a Unicode (IDN) host name; it is never cached as a
robots.txt that did not answer, and no blocked page is stored. G2: robots.txt is decoded line by
line, so one non-UTF-8 byte does not turn the UTF-8 rules on other lines into latin-1. No external
network: a local server behind stubbed DNS for the guarded client, and mock transports."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import httpx
import pytest

import aeo.crawl.fetch as fetch_module
from aeo.crawl.fetch import ROBOTS_UNREACHABLE, Crawler
from aeo.errors import UnsafeURL

UA = "RanksimTest/1.0 (+https://ci.ranksim-tests.org/bot)"
PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.5"
TEXT = {"content-type": "text/plain"}


# G1


def _ascii_host(name: str) -> str:
    return httpx.URL(f"http://{name}/").raw_host.decode("ascii")  # punycode, as httpx sends it


class _FlipWorld:
    """A real guarded crawler (allow_private=False). Every .test host resolves to a public address
    that a local server answers. ``rebinding`` is the exception: ``private(count, port)`` says
    whether its lookup number ``count`` (1-based, URL checks and connection guard alike) answers
    10.0.0.5 instead. site.test/go redirects to the rebinding host, and the robots.txt of
    redirecting.test redirects to it."""

    def __init__(self, monkeypatch, rebinding: str, private):
        for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            monkeypatch.delenv(name, raising=False)  # plain http goes direct, through the pinned pool
        self.rebinding = _ascii_host(rebinding)
        self.private = private
        self.lookups = 0
        self.connects = []
        self.hits = []
        world = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                host = self.headers.get("Host", "")
                world.hits.append((host, self.path))
                if host == "redirecting.test" and self.path == "/robots.txt":
                    return self.redirect(f"http://{world.rebinding}/robots.txt")
                if host == "site.test" and self.path == "/go":
                    return self.redirect(f"http://{world.rebinding}/secret")
                if self.path == "/robots.txt":
                    body, kind = b"User-agent: *\nAllow: /\n", "text/plain"
                else:
                    body, kind = b"<html><title>page</title><body>page</body></html>", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def redirect(self, location):
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        port = self.httpd.server_port
        real = socket.getaddrinfo

        def resolve(host, wanted_port, *args, **kwargs):
            name = host.decode() if isinstance(host, bytes) else str(host)
            if not name.endswith(".test"):
                return real(host, wanted_port, *args, **kwargs)  # the local server itself
            address = PUBLIC
            if _ascii_host(name) == world.rebinding:
                world.lookups += 1
                outcome = world.private(world.lookups, wanted_port)
                if isinstance(outcome, BaseException):
                    raise outcome
                address = PRIVATE if outcome else PUBLIC
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, wanted_port or 0))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)

        class Local(httpcore.NetworkBackend):
            def __init__(self, inner):
                self.inner = inner

            def connect_tcp(self, host, wanted_port, timeout=None, local_address=None, socket_options=None):
                world.connects.append(host)
                if host != PUBLIC:
                    raise httpcore.ConnectError("only the public test address is served here")
                return self.inner.connect_tcp("127.0.0.1", port, timeout=timeout)

            def sleep(self, seconds):
                self.inner.sleep(seconds)

        self._local = Local
        self.crawlers = []

    def crawler(self) -> Crawler:
        self.lookups = 0  # each crawler meets the rebinding host fresh
        crawler = Crawler(user_agent=UA, delay_seconds=0)
        assert crawler._guards and all(item.check_ip for item in crawler._guards)
        for item in crawler._guards:
            item._inner = self._local(item._inner)
        self.crawlers.append(crawler)
        return crawler

    def rebinding_hits(self):
        return [path for host, path in self.hits if host == self.rebinding]

    def close(self):
        for crawler in self.crawlers:
            crawler.close()
        self.httpd.shutdown()
        self.httpd.server_close()


def _first_lookup_public(count, _port):
    """The usual TTL-0 rebinding: the first answer is public, every later one private. The DNS
    server cannot tell a URL check from a connection."""
    return count > 1


def _connect_private(_count, port):
    """Lookups without a port (URL checks) answer public; the connection guard's answer is private."""
    return port is not None


@pytest.mark.parametrize("start", ["http://flip.test/page", "http://site.test/go"], ids=["start-host", "redirect-hop"])
def test_a_rebinding_answer_seen_by_the_robots_txt_url_check_is_an_unsafe_url(monkeypatch, start):
    world = _FlipWorld(monkeypatch, "flip.test", _first_lookup_public)
    try:
        crawler = world.crawler()
        with pytest.raises(UnsafeURL, match="flip.test resolves to a non-public address"):
            crawler.fetch(start)
        assert "http://flip.test" not in crawler._robots  # not cached as a robots.txt that did not answer
        batch = world.crawler().crawl(start, sitemap_url=None, max_pages=3)
    finally:
        world.close()
    assert [page for page in batch.pages if "flip.test" in page.final_url] == []  # no blocked page named after it
    assert not any(page.blocked for page in batch.pages)
    assert any("flip.test resolves to a non-public address" in item for item in batch.warnings)
    assert set(world.connects) <= {PUBLIC}  # the private address got no connection
    assert world.rebinding_hits() == []


@pytest.mark.parametrize("public_lookups", range(7))
@pytest.mark.parametrize("start", ["http://flip.test/page", "http://site.test/go"], ids=["start-host", "redirect-hop"])
def test_a_rebinding_host_is_never_read_as_a_robots_txt_that_did_not_answer(monkeypatch, start, public_lookups):
    """Whichever lookup first sees the private answer, the fetch is an unsafe URL or (when no lookup
    before the page request sees it) the page itself. It is never a blocked page."""
    world = _FlipWorld(monkeypatch, "flip.test", lambda count, _port: count > public_lookups)
    try:
        crawler = world.crawler()
        try:
            result = crawler.fetch(start)
        except UnsafeURL as exc:
            assert "flip.test" in str(exc)
            assert public_lookups < 6  # with six public answers, nothing sees the private one
            # A robots.txt read before the private answer stays cached; "did not answer" never is.
            assert crawler._robots.get("http://flip.test") is not ROBOTS_UNREACHABLE
        else:
            assert not result.blocked and result.status_code == 200 and "page" in result.html, result
            assert result.final_url.startswith("http://flip.test/")
            parser = crawler._robots["http://flip.test"]
            assert parser is not None and parser is not ROBOTS_UNREACHABLE
    finally:
        world.close()
    assert set(world.connects) <= {PUBLIC}


@pytest.mark.parametrize("host", ["rébind.test", "xn--rbind-bsa.test"], ids=["unicode", "punycode"])
def test_an_idn_host_refused_by_the_connection_guard_is_an_unsafe_url(monkeypatch, host):
    world = _FlipWorld(monkeypatch, host, _connect_private)
    origin = f"http://{host}"
    try:
        crawler = world.crawler()
        with pytest.raises(UnsafeURL, match="xn--rbind-bsa.test resolves to a non-public address at connect time"):
            crawler.fetch(f"{origin}/page")
        assert origin not in crawler._robots
        batch = world.crawler().crawl(f"{origin}/page", sitemap_url=None, max_pages=3)
    finally:
        world.close()
    assert batch.pages == []  # no blocked page named after the rebinding host
    assert any("resolves to a non-public address at connect time" in item for item in batch.warnings)
    assert set(world.connects) <= {PUBLIC}
    assert world.rebinding_hits() == []


def test_a_robots_txt_that_redirects_to_a_rebinding_host_stays_fail_closed(monkeypatch):
    world = _FlipWorld(monkeypatch, "flip.test", _first_lookup_public)
    try:
        crawler = world.crawler()
        result = crawler.fetch("http://redirecting.test/page")
    finally:
        world.close()
    assert result.blocked and result.robots_status == "unreachable" and not result.html
    assert crawler._robots["http://redirecting.test"] is ROBOTS_UNREACHABLE
    assert ("redirecting.test", "/page") not in world.hits
    assert set(world.connects) <= {PUBLIC}


@pytest.mark.parametrize(
    "failure", [socket.gaierror(socket.EAI_NONAME, "not known"), UnicodeError("label too long")], ids=["dns", "malformed"]
)
def test_a_robots_txt_url_check_that_cannot_resolve_stays_unreachable(monkeypatch, failure):
    world = _FlipWorld(monkeypatch, "flip.test", lambda count, _port: failure if count > 1 else False)
    try:
        crawler = world.crawler()
        result = crawler.fetch("http://flip.test/page")
    finally:
        world.close()
    assert result.blocked and result.robots_status == "unreachable" and "did not answer" in result.error
    assert crawler._robots["http://flip.test"] is ROBOTS_UNREACHABLE
    assert world.rebinding_hits() == []


# G2


def _robots_crawler(body: bytes, content_type: str, hits: list) -> Crawler:
    def handler(request):
        hits.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=body, headers={"content-type": content_type})
        return httpx.Response(200, text="<html><body>page</body></html>", headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return Crawler(user_agent=UA, delay_seconds=0, allow_private=True, client=client)


RULES = "User-agent: *\nDisallow: /café\n".encode("utf-8")


@pytest.mark.parametrize(
    ("body", "charset"),
    [
        (RULES + "# © Shop\n".encode("latin-1"), "ISO-8859-1"),
        ("# © Shop\n".encode("latin-1") + RULES, "ISO-8859-1"),
        (RULES + b"# Shop\x92s rules\n", "windows-1252"),  # a smart quote pasted into a comment
    ],
    ids=["latin-1-after", "latin-1-before", "cp1252-quote"],
)
def test_utf8_rules_survive_one_non_utf8_line_under_a_legacy_charset(body, charset):
    hits = []
    crawler = _robots_crawler(body, f"text/plain; charset={charset}", hits)
    try:
        result = crawler.fetch("https://site.test/café/menu")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


def test_utf8_and_latin1_rule_lines_in_one_file_are_each_read_in_their_own_charset():
    body = "User-agent: *\nDisallow: /café\n".encode("latin-1") + "Disallow: /naïve\n".encode("utf-8")
    hits = []
    crawler = _robots_crawler(body, "text/plain; charset=ISO-8859-1", hits)
    try:
        cafe = crawler.fetch("https://site.test/café/menu")
        naive = crawler.fetch("https://site.test/naïve/menu")
        other = crawler.fetch("https://site.test/other")
    finally:
        crawler.close()
    assert cafe.blocked and cafe.robots_status == "disallowed"
    assert naive.blocked and naive.robots_status == "disallowed"
    assert not other.blocked and other.robots_status == "allowed"
    assert hits == ["/robots.txt", "/other"]


def test_a_character_cut_by_the_size_cap_on_the_last_line_is_dropped_not_a_fallback(monkeypatch):
    head = "# © Shop\n".encode("latin-1") + "User-agent: *\nDisallow: /naïve".encode("utf-8")
    body = head + "é more text\n".encode("utf-8")
    monkeypatch.setattr(fetch_module, "MAX_BYTES", len(head) + 1)  # the cap splits the 'é' after the rule
    hits = []
    crawler = _robots_crawler(body, "text/plain; charset=ISO-8859-1", hits)
    try:
        result = crawler.fetch("https://site.test/naïve/menu")
    finally:
        crawler.close()
    assert result.blocked and result.robots_status == "disallowed"
    assert hits == ["/robots.txt"]


def test_a_utf16_robots_txt_with_its_charset_declared_is_still_read_whole():
    body = "User-agent: *\nDisallow: /private\nDisallow: /café\n".encode("utf-16")
    hits = []
    crawler = _robots_crawler(body, "text/plain; charset=utf-16", hits)
    try:
        private = crawler.fetch("https://site.test/private/page")
        cafe = crawler.fetch("https://site.test/café/menu")
    finally:
        crawler.close()
    assert private.blocked and private.robots_status == "disallowed"
    assert cafe.blocked and cafe.robots_status == "disallowed"
    assert hits == ["/robots.txt"]
