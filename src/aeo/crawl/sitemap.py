"""Sitemap XML parsing. One level of sitemap indexes is handled by the crawler."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(xml_text: str) -> tuple[str, list[str]]:
    """Return ``("urlset", page_urls)`` or ``("index", sitemap_urls)``.

    Invalid XML raises ``ValueError``.
    """
    try:
        root = ET.fromstring(xml_text)
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
