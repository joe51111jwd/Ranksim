"""Connection-level guards for the crawler's own httpx client.

- Pinning: the direct connection pool resolves a host itself, refuses the connection if any
  address is not globally routable, and connects to the vetted addresses in resolver order until
  one answers (as socket.create_connection does for a name). The request URL keeps the
  hostname, so the Host header, TLS SNI and certificate checks are unchanged. A name that
  resolved to a public address during the URL check and to a private one at connect time
  (DNS rebinding) is refused here.
- Deadline: every connect, read and write is capped at the time left before a per-request
  deadline, so a server that drips its headers or body one byte at a time cannot hold a fetch
  open past it.
Proxied requests are resolved by the proxy, so their pools get the deadline only.

httpx has no public hook for its network backend; ``install`` replaces the private
``_network_backend`` of each pool and raises if httpx's internals change, so a version bump fails
loudly instead of silently dropping the guard.
"""

from __future__ import annotations

import ipaddress
import socket
import time

import httpcore

from aeo.crawl.urls import _is_public_ip


class UnsafeConnect(httpcore.ConnectError):
    """The host resolved to an address the crawler must not connect to."""


class GuardedBackend(httpcore.NetworkBackend):
    def __init__(self, inner: httpcore.NetworkBackend, *, check_ip: bool) -> None:
        self._inner = inner
        self.check_ip = check_ip
        self.deadline: float | None = None
        self.clock = time.monotonic

    def remaining(self, timeout: float | None, error: type[Exception]) -> float | None:
        if self.deadline is None:
            return timeout
        left = self.deadline - self.clock()
        if left <= 0:
            raise error("The request deadline passed")
        return left if timeout is None else min(timeout, left)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.remaining(timeout, httpcore.ConnectTimeout)
        targets = vetted_addresses(host, port) if self.check_ip else [host]
        failure: Exception | None = None
        for target in targets:
            # Each attempt gets only the time left, so trying more addresses never passes the deadline.
            left = self.remaining(timeout, httpcore.ConnectTimeout)
            try:
                stream = self._inner.connect_tcp(target, port, timeout=left, local_address=local_address, socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc  # a dead address or an unusable address family: try the next one
                continue
            return DeadlineStream(stream, self)
        raise failure

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("The crawler does not connect to unix sockets")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class DeadlineStream(httpcore.NetworkStream):
    def __init__(self, inner: httpcore.NetworkStream, backend: GuardedBackend) -> None:
        self._inner = inner
        self._backend = backend

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, timeout=self._backend.remaining(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, timeout=self._backend.remaining(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        upgraded = self._inner.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=self._backend.remaining(timeout, httpcore.ConnectTimeout)
        )
        return DeadlineStream(upgraded, self._backend)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


def vetted_addresses(host: str, port: int) -> list[str]:
    """The addresses of ``host`` in resolver order, all globally routable; refuses if any is not."""
    name = host.decode() if isinstance(host, bytes) else str(host)
    name = name.strip("[]")
    try:
        literal = ipaddress.ip_address(name)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise UnsafeConnect(f"Refusing to connect to non-public address {name}")
        return [name]
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f"Could not resolve host {name}") from exc
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise httpcore.ConnectError(f"Could not resolve host {name}")
    for address in addresses:
        if not _is_public_ip(ipaddress.ip_address(address)):
            raise UnsafeConnect(f"Host {name} resolves to a non-public address at connect time")
    return addresses


def install(client, *, pin: bool) -> list[GuardedBackend]:
    """Guard every connection pool of an httpx.Client. ``pin`` (False only when private targets are
    allowed) turns on address checks for direct connections; proxies always get the deadline only."""
    transports = [client._transport] + [transport for transport in client._mounts.values() if transport is not None]
    guards = []
    for transport in transports:
        pool = getattr(transport, "_pool", None)
        backend = getattr(pool, "_network_backend", None)
        if pool is None or backend is None:
            raise RuntimeError(
                "httpx internals changed: the crawler cannot pin connections or enforce its deadline. "
                "Check aeo.crawl.guard against the installed httpx and httpcore."
            )
        proxied = isinstance(pool, (httpcore.HTTPProxy, getattr(httpcore, "SOCKSProxy", httpcore.HTTPProxy)))
        guard = backend if isinstance(backend, GuardedBackend) else GuardedBackend(backend, check_ip=pin and not proxied)
        pool._network_backend = guard
        guards.append(guard)
    return guards
