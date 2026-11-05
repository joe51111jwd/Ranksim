"""Vercel entry point for the PUBLIC SYNTHETIC DEMO of the Ranksim web UI. Not used for local runs.

Run locally instead with:  python -m aeo.ranksim serve

What this entry guarantees, whatever environment variables the host sets:
- public_demo=True: only the synthetic demo (.example sites, labeled SYNTHETIC). No project creation,
  no imports, no page fetching, no competitor edits, no historical SmartCrew data, no old /demo pages.
- Every provider key is forced off, so nothing here can call a paid SERP or LLM API.
- Data lives under /tmp (the only writable folder on Vercel). It is per server instance and is not
  durable: a saved draft test can disappear when the instance is recycled. The page says so.
- smartcrew/ is excluded from the deployment by .vercelignore, so no private corpus is uploaded.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _load_openmp() -> None:
    """LightGBM needs libgomp.so.1. Vercel's Python image has none, so preload the copy in vendor/libgomp (see its README)."""
    import ctypes

    try:
        ctypes.CDLL("libgomp.so.1", mode=ctypes.RTLD_GLOBAL)
    except OSError:
        ctypes.CDLL(str(ROOT / "vendor" / "libgomp" / "libgomp.so.1"), mode=ctypes.RTLD_GLOBAL)


_load_openmp()

DATA = Path(os.environ.get("RANKSIM_PUBLIC_DATA", "/tmp/ranksim"))
DATA.mkdir(parents=True, exist_ok=True)
os.environ["RANKSIM_PUBLIC_DEMO"] = "1"

from aeo.config import Settings  # noqa: E402
from aeo.ranksim.serve import create_app  # noqa: E402

KEYS_OFF = {
    name: None
    for name in (
        "serpapi_key",
        "serper_api_key",
        "dataforseo_login",
        "dataforseo_password",
        "brave_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "openrouter_api_key",
        "perplexity_api_key",
        "gemini_api_key",
    )
}
DATABASE = f"sqlite:///{DATA / 'rank.db'}"
settings = Settings(_env_file=None, ranksim_database_url=DATABASE, **KEYS_OFF)
# Serverless instances of one deployment must accept each other's forms. The public demo has no accounts or
# sessions, so a token shared per deployment loses nothing; it still stops blind cross-site posts.
_deployment = os.environ.get("VERCEL_DEPLOYMENT_ID") or os.environ.get("VERCEL_GIT_COMMIT_SHA") or "ranksim-public-demo"
CSRF = hashlib.sha256(f"ranksim-csrf:{_deployment}".encode()).hexdigest()[:43]
app = create_app(settings=settings, database=DATABASE, projects_root=DATA / "projects", public_demo=True, csrf_token=CSRF)
