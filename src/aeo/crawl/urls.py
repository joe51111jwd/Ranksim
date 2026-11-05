"""URL normalization and SSRF guards for the crawler."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from aeo.errors import UnsafeURL


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        return host[4:]
    return host


def normalize_url(url: str, *, keep_trailing_slash: bool = False) -> str:
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported")
    if parts.username or parts.password:
        raise ValueError("URLs with credentials are not allowed")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("URL is missing a host")
    port = parts.port
    shown = f"[{host}]" if ":" in host else host  # an IPv6 literal keeps its brackets
    if port and not (
        (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)
    ):
        netloc = f"{shown}:{port}"
    else:
        netloc = shown
    path = parts.path or "/"
    if path != "/" and path.endswith("/") and not keep_trailing_slash:
        path = path[:-1]
    return urlunsplit((parts.scheme, netloc, path, parts.query, ""))


def same_site(left: str, right: str) -> bool:
    return domain_of(left) == domain_of(right) and domain_of(left) != ""


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Only globally routable addresses. is_global also refuses 100.64.0.0/10 (carrier-grade NAT, where
    Alibaba Cloud's metadata service and Tailscale peers live), which is_private does not cover."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_ip(mapped)
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_safe_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve: bool = True,
    keep_trailing_slash: bool = False,
) -> str:
    """Reject non-http URLs and, unless explicitly allowed, private targets.

    ``resolve=False`` checks only the URL itself (scheme, credentials, IP
    literals, localhost). Fetching uses ``resolve=True`` so a public name that
    points at a link-local address is blocked. ``allow_private`` skips both
    checks for tests and local labs.
    """
    normalized = normalize_url(url, keep_trailing_slash=keep_trailing_slash)
    if allow_private:
        return normalized
    host = urlsplit(normalized).hostname or ""
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURL(f"Refusing non-public host {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise UnsafeURL(f"Refusing non-public address {host}")
        return normalized
    if not resolve:
        return normalized
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURL(f"Could not resolve host {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public_ip(ip):
            raise UnsafeURL(f"Host {host} resolves to a non-public address")
    return normalized
