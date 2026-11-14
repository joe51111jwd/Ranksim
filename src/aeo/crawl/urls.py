"""URL normalization and SSRF guards for the crawler."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from aeo.errors import UnsafeURL


class UnresolvableHost(UnsafeURL):
    """The host name did not resolve. A fetch reports this as an error, not as a refused target."""


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


_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Only globally routable addresses. is_global also refuses 100.64.0.0/10 (carrier-grade NAT, where
    Alibaba Cloud's metadata service and Tailscale peers live), which is_private does not cover."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public_ip(mapped)
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 forms that carry an IPv4 address: 6to4, Teredo and NAT64 (64:ff9b::/96). The embedded
        # address must be public too, or a NAT64/6to4 gateway could carry the request to 127.0.0.1.
        embedded = [ip.sixtofour] if ip.sixtofour is not None else []
        if ip.teredo is not None:
            embedded.extend(ip.teredo)
        if ip in _NAT64:
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(not _is_public_ip(inner) for inner in embedded):
            return False
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


# Names that only mean something inside a private network or a cloud host. Resolution would usually catch
# these too, but a split-horizon resolver can answer with anything, so they are refused by name first.
_BLOCKED_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "instance-data.ec2.internal",
    }
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse the inet_aton forms that getaddrinfo accepts but ``ipaddress`` does not.

    ``2130706433``, ``0x7f000001``, ``0177.0.0.1`` and ``127.1`` all mean 127.0.0.1 to the C resolver.
    Returns None when ``host`` is not one of these numeric forms.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values: list[int] = []
    for part in parts:
        if not part:
            return None
        try:
            if part[:2].lower() == "0x":
                value = int(part, 16) if len(part) > 2 else 0
            elif len(part) > 1 and part.startswith("0"):
                value = int(part, 8)
            else:
                if not part.isdigit():
                    return None
                value = int(part, 10)
        except ValueError:
            return None
        values.append(value)
    *head, last = values
    if any(v > 255 for v in head) or last >= 256 ** (4 - len(head)):
        return None
    number = 0
    for v in head:
        number = number * 256 + v
    number = number * 256 ** (4 - len(head)) + last
    return ipaddress.IPv4Address(number)


def check_host(host: str) -> None:
    """Refuse a host by name or literal, without DNS. Raises UnsafeURL."""
    host = host.lower().rstrip(".")
    if not host:
        raise UnsafeURL("URL is missing a host")
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeURL(f"Refusing non-public host {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise UnsafeURL(f"Refusing non-public address {host}")
        return
    legacy = _legacy_ipv4(host)
    if legacy is not None:
        # A non-canonical numeric host is never a real site. Refuse it even when it decodes to a public
        # address, so no resolver quirk can turn it into something else.
        raise UnsafeURL(f"Refusing numeric host {host} (means {legacy})")


def resolve_public(host: str, port: int | None = None) -> list[tuple]:
    """``getaddrinfo`` for ``host`` that raises UnsafeURL unless every address is public.

    Returns the getaddrinfo tuples so a caller can connect to exactly the addresses that were vetted.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise UnresolvableHost(f"Could not resolve host {host}") from exc
    if not infos:
        raise UnresolvableHost(f"Could not resolve host {host}")
    for info in infos:
        raw = str(info[4][0]).split("%", 1)[0]  # drop an IPv6 zone id such as fe80::1%eth0
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise UnsafeURL(f"Host {host} resolves to an unreadable address") from exc
        if not _is_public_ip(ip):
            raise UnsafeURL(f"Host {host} resolves to a non-public address")
    return infos


def assert_safe_url(
    url: str,
    *,
    allow_private: bool = False,
    resolve: bool = True,
    keep_trailing_slash: bool = False,
) -> str:
    """Reject non-http URLs and, unless explicitly allowed, private targets.

    ``resolve=False`` checks only the URL itself (scheme, credentials, IP
    literals, numeric tricks, local names). Fetching uses ``resolve=True`` so a
    public name that points at a link-local address is blocked. ``allow_private``
    skips both checks for tests and local labs.

    This check runs before the request; the Crawler's network backend repeats the
    resolution at connect time (``aeo.crawl.guard``) so DNS rebinding cannot slip
    past it. Unusual ports are allowed on purpose: public sites do serve on them,
    and the address check is what keeps the crawler off internal services.
    """
    normalized = normalize_url(url, keep_trailing_slash=keep_trailing_slash)
    if allow_private:
        return normalized
    host = (urlsplit(normalized).hostname or "").rstrip(".")
    check_host(host)
    try:
        ipaddress.ip_address(host)
        return normalized  # a public literal needs no lookup
    except ValueError:
        pass
    if not resolve:
        return normalized
    resolve_public(host)
    return normalized
