import httpx

from aeo.crawl.chunk import chunk_sections
from aeo.crawl.extract import extract_html
from aeo.crawl.fetch import Crawler
from aeo.crawl.sitemap import parse_sitemap
from aeo.crawl.urls import assert_safe_url
from aeo.errors import UnsafeURL
import pytest


PAGE = """<!doctype html>
<html><head>
<title>Best trail mix for long hikes | Northwind</title>
<meta name="description" content="Salt, carbs, and weight.">
<link rel="canonical" href="/trail-mix">
<meta property="article:modified_time" content="2026-01-15">
<script type="application/ld+json">{"@type":"Article","datePublished":"2026-01-01","headline":"Best trail mix"}</script>
</head>
<body>
<nav>Skip this navigation sentence.</nav>
<header><h1>Best trail mix for long hikes</h1></header>
<main>
<p>The best trail mix for long hikes is almonds and salt.</p>
<h2>How to pack it</h2>
<p>Keep the pouch outside the sleeping bag.</p>
</main>
<a href="/secret">nope</a>
<a href="https://other.example/away">away</a>
</body></html>"""


def test_extracts_raw_html_fields_and_drops_nav():
    extracted = extract_html(PAGE, "https://northwind.example/trail-mix")
    assert extracted.title.startswith("Best trail mix")
    assert extracted.meta_description.startswith("Salt")
    assert extracted.canonical == "https://northwind.example/trail-mix"
    assert extracted.h1 == "Best trail mix for long hikes"
    assert extracted.updated == "2026-01-15"
    assert extracted.published == "2026-01-01"
    assert extracted.schema_objects[0]["@type"] == "Article"
    assert "navigation" not in extracted.main_text
    assert "almonds" in extracted.main_text
    assert "How to pack it" in extracted.main_text
    assert "https://northwind.example/secret" in extracted.links
    assert "https://other.example/away" in extracted.links
    assert extracted.word_count > 5


def test_div_text_is_kept_when_paragraphs_are_only_a_stub():
    words = " ".join(["dirtwork"] * 100)
    html = f"<html><head><title>HCSS alternatives</title></head><body><p>See more</p><div>{words}</div></body></html>"
    extracted = extract_html(html, "https://www.facebook.com/fb-answers/hcss-software-alternative/")
    assert extracted.word_count > 80
    assert "dirtwork" in extracted.main_text


def test_chunker_keeps_heading_and_splits_long_sections():
    text = "word " * 400
    chunks = chunk_sections([("Packing", text)], max_chars=200, overlap=20)
    assert len(chunks) > 1
    assert all(chunk.heading_path == "Packing" for chunk in chunks)
    assert len(chunks) <= 40


def test_sitemap_urlset_and_index():
    kind, urls = parse_sitemap(
        """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://example.com/a</loc></url>
          <url><loc>https://example.com/b</loc></url>
        </urlset>"""
    )
    assert kind == "urlset"
    assert urls == ["https://example.com/a", "https://example.com/b"]
    kind, urls = parse_sitemap(
        """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://example.com/s1.xml</loc></sitemap>
        </sitemapindex>"""
    )
    assert kind == "index"
    assert urls == ["https://example.com/s1.xml"]


def test_blocks_private_and_non_http_targets():
    with pytest.raises(UnsafeURL):
        assert_safe_url("http://127.0.0.1/latest", resolve=False)
    with pytest.raises(UnsafeURL):
        assert_safe_url("http://169.254.169.254/latest/meta-data", resolve=False)
    with pytest.raises(ValueError):
        assert_safe_url("file:///etc/passwd", resolve=False)
    assert assert_safe_url("https://northwind.example/trail-mix", resolve=False).endswith("/trail-mix")


def test_crawler_obeys_robots_and_stays_on_site():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url).split("?")[0])
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret\n")
        if url.rstrip("/").endswith("example.com"):
            html = """<html><head><title>Home</title></head><body>
            <h1>Home</h1><p>Public page.</p>
            <a href="/secret">secret</a>
            <a href="/about">about</a>
            <a href="https://other.example/nope">out</a>
            </body></html>"""
            return httpx.Response(200, text=html)
        if url.endswith("/about"):
            return httpx.Response(200, text="<html><title>About</title><body><h1>About</h1><p>About page.</p></body></html>")
        if url.endswith("/secret"):
            raise AssertionError("disallowed URL was fetched")
        return httpx.Response(404, text="missing")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    crawler = Crawler(user_agent="AEOSimulatorBot/0.1", delay_seconds=0, allow_private=True, client=client)
    batch = crawler.crawl("https://example.com/", sitemap_url=None, max_pages=10)
    crawler.close()
    urls = [page.final_url for page in batch.pages]
    assert "https://example.com" in urls or "https://example.com/" in [page.url for page in batch.pages]
    assert any(page.url.endswith("/about") and page.status_code == 200 for page in batch.pages)
    blocked = [page for page in batch.pages if page.blocked]
    assert any(page.url.endswith("/secret") for page in blocked)
    assert all(not url.endswith("/secret") for url in requested)
    assert all("other.example" not in url for url in urls)


def test_main_text_uses_the_fullest_main_or_article_not_the_first():
    from aeo.crawl.extract import extract_html

    filler = " ".join(f"word{index}" for index in range(300))
    empty_first_main = f"<html><body><main></main><main><h1>Real</h1><p>{filler}</p></main></body></html>"
    assert extract_html(empty_first_main, "https://x.example/").word_count >= 300
    card_first = (
        "<html><body><article><p>Related post card with a few words</p></article>"
        f"<div class='post'><h1>Guide</h1><p>{filler}</p><p>{filler}</p></div></body></html>"
    )
    assert extract_html(card_first, "https://x.example/").word_count >= 600
    normal = f"<html><body><nav>Home About</nav><main><h1>Guide</h1><p>{filler}</p></main><div>sidebar teaser</div></body></html>"
    page = extract_html(normal, "https://x.example/")
    assert 300 <= page.word_count < 310 and "teaser" not in page.main_text
