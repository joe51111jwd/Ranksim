"""Connect-time SSRF guard for the crawler's own httpx transport.

``assert_safe_url`` resolves a host before the request is sent, then httpx would resolve it again when it
opens the socket. A rebinding DNS server can answer with a public address the first time and with
169.254.169.254 the second. This backend closes that gap: it resolves once, refuses the connection unless
every returned address is public, and connects the socket to one of those vetted addresses. It never hands a
host name to the socket layer, so there is no second lookup.

TLS is unchanged: httpcore wraps the returned stream with ``start_tls(server_hostname=<URL host>)``, so SNI
and certificate hostname checks still use the name from the URL, not the IP we connected to.

Limit: when an HTTP(S) proxy from the environment is used (``HTTPS_PROXY`` and friends, honoured by httpx's
``trust_env``), httpx sends those requests through its own proxy transport and the proxy resolves the target
host. This guard does not see those connections, so behind a proxy the pre-request ``assert_safe_url`` check
is the only DNS check and rebinding protection depends on the proxy. Hosts listed in ``NO_PROXY`` go direct
and are guarded here.
"""

from __future__ import annotations

import socket
import typing

import httpcore
import httpx
from httpcore._backends.sync import SyncStream

from aeo.crawl.urls import UnresolvableHost, check_host, resolve_public
from aeo.errors import UnsafeURL


def _connect(family: int, socktype: int, proto: int, sockaddr: tuple, timeout: float | None, source_address: tuple | None) -> socket.socket:
    """Open a TCP socket to an already vetted numeric address. Tests replace this with a stub."""
    sock = socket.socket(family, socktype, proto)
    try:
        sock.settimeout(timeout)
        if source_address is not None:
            sock.bind(source_address)
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


class GuardedBackend(httpcore.SyncBackend):
    """``httpcore.SyncBackend`` that only connects to public addresses it resolved itself."""

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        name = host.strip("[]")
        check_host(name)  # numeric tricks and local names, before any lookup
        try:
            infos = resolve_public(name, port)  # raises UnsafeURL unless every address is public
        except UnresolvableHost as exc:
            raise httpcore.ConnectError(str(exc)) from exc  # a DNS failure is a network error, not a refusal
        source = None if local_address is None else (local_address, 0)
        last_error: OSError | None = None
        for family, socktype, proto, _canon, sockaddr in infos:
            try:
                sock = _connect(family, socktype or socket.SOCK_STREAM, proto, sockaddr, timeout, source)
            except OSError as exc:  # socket.timeout is an OSError
                last_error = exc
                continue
            try:
                for option in socket_options or []:
                    sock.setsockopt(*option)
                if family in (socket.AF_INET, socket.AF_INET6):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass  # a socket stub in tests, or an option the platform lacks; the connection still works
            return SyncStream(sock)
        if isinstance(last_error, socket.timeout):
            raise httpcore.ConnectTimeout(str(last_error))
        raise httpcore.ConnectError(str(last_error) if last_error else f"Could not connect to {name}")

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise UnsafeURL("Unix sockets are not allowed for crawling")


def guarded_transport(**kwargs: typing.Any) -> httpx.HTTPTransport:
    """An ``httpx.HTTPTransport`` whose direct connections go through ``GuardedBackend``.

    Proxy support is left to httpx's environment proxy mounts (see the module note); a transport built here
    with an explicit ``proxy=`` is refused, because the guard would then vet the proxy host, not the target.
    """
    if kwargs.get("proxy") is not None:
        raise ValueError("guarded_transport does not take a proxy")
    transport = httpx.HTTPTransport(**kwargs)
    pool = transport._pool  # httpx keeps the httpcore pool here; it has no public hook for a backend
    if not isinstance(pool, httpcore.ConnectionPool) or not hasattr(pool, "_network_backend"):
        raise RuntimeError("httpx/httpcore internals changed; the connect-time SSRF guard could not be installed")
    pool._network_backend = GuardedBackend()
    return transport
