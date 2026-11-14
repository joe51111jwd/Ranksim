"""Sitemap XML parsing, plain or gzip. The crawler follows sitemap indexes up to two levels deep."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zlib

GZIP_MAGIC = b"\x1f\x8b"
# Uncompressed cap for a gzip sitemap. The protocol allows 50 MB; we read far fewer URLs than that holds,
# and the cap stops a small gzip bomb from expanding without limit.
MAX_GUNZIP_BYTES = 10_000_000
_ENTITY_DECL = re.compile(r"<!ENTITY", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def is_gzip(raw: bytes) -> bool:
    return raw[:2] == GZIP_MAGIC


def gunzip_capped(raw: bytes, *, limit: int = MAX_GUNZIP_BYTES) -> bytes:
    """Decompress gzip data, stopping at ``limit`` bytes. A truncated download still yields what it holds.

    Corrupt data raises ``ValueError``.
    """
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(raw, limit)
    except zlib.error as exc:
        raise ValueError("Sitemap gzip data could not be read") from exc
    return out[:limit]


def decode_sitemap(raw: bytes, *, limit: int = MAX_GUNZIP_BYTES) -> str:
    """Bytes of a sitemap response to text, gunzipping when the body is gzip."""
    if is_gzip(raw):
        raw = gunzip_capped(raw, limit=limit)
    return raw.decode("utf-8", errors="replace")


def parse_sitemap(xml_text: str | bytes) -> tuple[str, list[str]]:
    """Return ``("urlset", page_urls)`` or ``("index", sitemap_urls)``.

    Accepts text, or bytes (gzip or plain). Invalid XML raises ``ValueError``.
    """
    if isinstance(xml_text, (bytes, bytearray)):
        xml_text = decode_sitemap(bytes(xml_text))
    if _ENTITY_DECL.search(xml_text[:4096]):
        raise ValueError("Sitemap XML declares entities; refusing to parse")
    try:
        root = ET.fromstring(xml_text.lstrip("﻿ \t\r\n"))
    except ET.ParseError as exc:
        raise ValueError("Sitemap XML could not be parsed") from exc
    kind = _local(root.tag)
    locs: list[str] = []
    for element in root.iter():
        if _local(element.tag) == "loc" and element.text:
            value = element.text.strip()
            if value:
                locs.append(value)
    if kind == "sitemapindex":
        return "index", locs
    if kind == "urlset":
        return "urlset", locs
    raise ValueError(f"Unsupported sitemap root <{kind}>")
