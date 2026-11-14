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

PASSWORD = "e2e-pass"
SLOW = float(os.environ.get("E2E_SLOW_SECONDS", "1.2"))
PROVIDER_ENV = (
    "BRAVE_API_KEY", "SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY", "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY", "RANKSIM_LLM_MODEL",
    "RANKSIM_AUTHORITY", "BLOB_READ_WRITE_TOKEN", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
)


def build_app(data: Path):
    for name in PROVIDER_ENV:
        os.environ.pop(name, None)
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

    def preview_fetch(url: str) -> tuple[str, str]:
        if "broken" in url:
            return "", "Fetch failed: connection reset"
        return page_html("Dirt Work Software for crews", "Dirt work software", extra="dirt work software " * 20), ""

    return create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=data / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret="e2e-secret",
        pull_options={"serp_provider": AnyGoogle(), "fetcher": MirrorCrawler(), "today": TODAY, "workers": 4},
        preview_fetch=preview_fetch,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(build_app(Path(args.data)), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
