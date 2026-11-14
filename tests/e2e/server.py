"""A local Ranksim for the end-to-end suite: private mode, password `e2e-pass`, and fakes for every network call.

Run: python tests/e2e/server.py --port 8799 --data /tmp/somewhere
The fake Google answers the searches in tests/test_project.py; the fake crawler serves the dirtcrew.example
pages; the preview fetch returns a small page for any address. Nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from urllib.parse import urlsplit
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "src", ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fastapi import Request  # module level: the route below resolves its annotation from these globals

PASSWORD = "e2e-pass"
SLOW = float(os.environ.get("E2E_SLOW_SECONDS", "1.2"))
PROVIDER_ENV = (
    "BRAVE_API_KEY", "SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY", "RANKSIM_LLM_MODEL",
    "RANKSIM_AUTHORITY", "BLOB_READ_WRITE_TOKEN", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
)


# The live web as the fake server sees it, and a fake WordPress for publishing tests.
WP_PAGE = "https://www.dirtcrew.example/wp-about/"
WP_API = "https://www.dirtcrew.example/wp-json"


def wp_page(title: str, h1: str, body: str, extra: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title><meta name=\"description\" content=\"About the crew.\">{extra}</head>"
        f"<body><header>Menu</header><h1>{h1}</h1><div class=\"entry-content\">{body}</div><footer>Foot</footer></body></html>"
    )


class Web:
    """url -> html for the fake live web. Query strings are ignored. Unknown pages answer 404."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {}

    @staticmethod
    def key(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}{p.path or '/'}"

    def get(self, url: str):
        return self.pages.get(self.key(url))

    def __call__(self, url: str) -> tuple[str, str]:
        html = self.get(url)
        return (html, "") if html is not None else ("", "HTTP 404")


def wordpress(web: Web):
    """httpx MockTransport handler for a WordPress site with one page (id 42) at WP_PAGE. Updates change the live page."""
    import json
    import re

    import httpx

    item = {"id": 42, "link": WP_PAGE, "slug": "wp-about", "title": {"raw": "About DirtCrew", "rendered": "About DirtCrew"},
            "content": {"raw": "<p>We run dirt work crews.</p>", "rendered": "<p>We run dirt work crews.</p>"},
            "excerpt": {"raw": "", "rendered": ""}, "meta": {}}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not url.startswith(WP_API):
            return httpx.Response(404, json={"message": "no such host in the e2e fake"})
        rest = url[len(WP_API):]
        if request.method == "GET" and rest in ("", "/"):
            return httpx.Response(200, json={"namespaces": ["wp/v2"]})
        if request.method == "GET" and rest.startswith("/wp/v2/users/me"):
            return httpx.Response(200, json={"name": "E2E Owner"})
        if request.method == "GET" and re.match(r"/wp/v2/(pages|posts)\?", rest):
            hit = rest.startswith("/wp/v2/pages") and "slug=wp-about" in rest
            return httpx.Response(200, json=[item] if hit else [])
        if re.match(r"/wp/v2/(pages|posts)/42", rest):
            if request.method == "POST":
                data = json.loads(request.content or b"{}")
                for key in ("title", "content", "excerpt"):
                    if key in data:
                        item[key] = {"raw": data[key], "rendered": data[key]}
                web.pages[WP_PAGE] = wp_page(f"{item['title']['raw']} | DirtCrew", item["title"]["raw"], item["content"]["raw"])
            return httpx.Response(200, json=item)
        return httpx.Response(404, json={"message": f"no mock for {request.method} {url}"})

    return handle


def build_app(data: Path):
    for name in PROVIDER_ENV:
        os.environ.pop(name, None)
    # Seals stored publishing credentials. A fixed test value, never used outside this local fake server.
    os.environ["RANKSIM_SECRET"] = "e2e-only-secretbox-key-0123456789abcdef"
    from test_hosted import FakeGoogle
    from test_project import TODAY, FakeCrawler, page_html
    from test_serve_project import make_settings

    from aeo.ranksim.serve import create_app

    from test_project import SEARCHES, _results

    class AnyGoogle(FakeGoogle):
        """FakeGoogle, but a search it does not know gets the results of a known one, so discovered searches rank."""

        def search(self, query: str, count: int = 10):
            known = _results()
            if query not in known:
                query = SEARCHES[sum(map(ord, query)) % len(SEARCHES)]
            return FakeGoogle.search(self, query, count)

    from aeo.ranksim.loop import FetchResult

    class MirrorCrawler(FakeCrawler):
        """FakeCrawler, plus slow copies of the fake site at slow-*.example (each batch waits SLOW seconds), so two
        analyses overlap in time and a reload can land mid-analysis."""

        def __call__(self, urls):
            slow = [url for url in urls if ".slow-" in url or "//slow-" in url]
            if not slow:
                return FakeCrawler.__call__(self, urls)
            time.sleep(SLOW)
            out = []
            for url in urls:
                host = urlsplit(url).netloc
                if host.startswith(("slow-", "www.slow-")):
                    mirrored = url.replace(host, "www.dirtcrew.example", 1)
                    got = FakeCrawler.__call__(self, [mirrored])[0]
                    out.append(FetchResult(url=url, html=got.html, status=got.status, error=got.error, fetch_ms=got.fetch_ms))
                else:
                    out.extend(FakeCrawler.__call__(self, [url]))
            return out

    data.mkdir(parents=True, exist_ok=True)
    # A placeholder key only marks Google as configured; FakeGoogle answers every search.
    settings = make_settings(data, serpapi_key="e2e-placeholder-not-a-key")

    web = Web()
    web.pages[WP_PAGE] = wp_page("About DirtCrew | DirtCrew", "About DirtCrew", "<p>We run dirt work crews.</p>")

    def preview_fetch(url: str) -> tuple[str, str]:
        if "slow-" in url:
            time.sleep(SLOW * 2)  # a real page takes seconds to fetch; the first-load checks need that gap
        if web.get(url) is not None:
            return web(url)
        if "broken" in url:
            return "", "Fetch failed: connection reset"
        return page_html("Dirt Work Software for crews", "Dirt work software", extra="dirt work software " * 20), ""

    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=data / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret="e2e-secret",
        pull_options={"serp_provider": AnyGoogle(), "fetcher": MirrorCrawler(), "today": TODAY, "workers": 4},
        preview_fetch=preview_fetch,
    )
    import httpx

    handler = wordpress(web)

    # The fake hosts (*.example) never resolve, so let the publishing connectors skip their DNS guard for them only.
    # Every other host still goes through the real check. Same idea as tests/test_publish.py patching assert_safe_url.
    from aeo.ranksim.publishing.connectors import base as connectors

    real_safe, real_pinned = connectors.Connector.safe_url, connectors.Connector.pinned
    fake = lambda url: (urlsplit(url).hostname or "").endswith(".example")  # noqa: E731
    connectors.Connector.safe_url = lambda self, url: url if fake(url) else real_safe(self, url)
    connectors.Connector.pinned = lambda self, url: (url, {}, {}) if fake(url) else real_pinned(self, url)
    app.state.ui.publish_overrides = {
        "http_factory": lambda *args, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)),
        "sleep": lambda seconds: None,
        "verify_waits": (0.0,),
        "proof_fetch": web,
    }

    @app.post("/__e2e/web")
    async def e2e_web(request: Request) -> dict:
        """Test only: put a page on the fake live web, as an owner adding a meta tag to their site would."""
        body = await request.json()
        web.pages[Web.key(body["url"])] = body["html"]
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(build_app(Path(args.data)), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
