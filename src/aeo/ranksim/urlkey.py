"""One key for "the same page whatever the spelling", used only to join URLs (labels to stored
pages, held-out URLs to training rows). It is not a fetchable URL and does not replace
``normalize_url``, whose stricter form other code and tests rely on.

The key drops the scheme, a leading ``www.``, a default port, a trailing slash, the fragment and
tracking parameters, and sorts the remaining query parameters. It matches ``project.loose_key``
on the web-UI branch (PR #2) exactly, including two quirks kept for parity: port 443 is dropped
for http too, and IPv6 literals lose their brackets.
"""

from __future__ import annotations

from urllib.parse import urlsplit

TRACKING_PARAMS = ("utm_", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga", "ref_src")


def match_key(url: str) -> str:
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return text.lower()
    if not host:
        return text.lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    path = (parts.path or "/").rstrip("/") or "/"
    query = "&".join(sorted(pair for pair in parts.query.split("&") if pair and not _tracking(pair.split("=", 1)[0])))
    return netloc + path + (f"?{query}" if query else "")


def _tracking(name: str) -> bool:
    name = name.lower()
    return any(name.startswith(prefix) if prefix.endswith("_") else name == prefix for prefix in TRACKING_PARAMS)


def same_page(left: str | None, right: str | None) -> bool:
    return bool(left) and bool(right) and match_key(left) == match_key(right)
