"""Vercel entry point for a hosted Ranksim. Not used for local runs (run `python -m aeo.ranksim serve` instead).

Two modes, chosen by the environment:

PRIVATE (RANKSIM_PASSWORD is set): the full tool behind one owner password.
- Create projects, set queries and competitors, import positions, pull live Google results (with SERPER_API_KEY,
  SERPAPI_KEY or the DataForSEO pair), fetch the ranking pages, train the engine, and test drafts.
- Every page and API call needs the password. The session cookie is signed with RANKSIM_SECRET.
- Projects, stored pages and engines persist in a private Vercel Blob store (BLOB_READ_WRITE_TOKEN, set when the
  store is connected to the project). Without that token the data lives only in this instance's /tmp.
- The AI answer engines are not run on a pull, so a pull only spends the Google key.

PUBLIC (no password): the anonymous synthetic demo, exactly as before.
- Only the synthetic demo (.example sites, labeled SYNTHETIC). No project creation, imports, fetching or competitor
  edits. Every provider key is forced off, so nothing can call a paid API. Drafts are never saved.

smartcrew/ is excluded from every deployment by .vercelignore, so no private corpus is uploaded.
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

PASSWORD = os.environ.get("RANKSIM_PASSWORD", "").strip()
PRIVATE = bool(PASSWORD)
DATA = Path(os.environ.get("RANKSIM_DATA_DIR") or os.environ.get("RANKSIM_PUBLIC_DATA") or "/tmp/ranksim")
DATA.mkdir(parents=True, exist_ok=True)
if not PRIVATE:
    os.environ["RANKSIM_PUBLIC_DEMO"] = "1"

from aeo.config import Settings  # noqa: E402
from aeo.ranksim.serve import create_app  # noqa: E402

KEYS = (
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
DATABASE = f"sqlite:///{DATA / 'rank.db'}"
# Serverless instances of one deployment must accept each other's forms, so the CSRF token is shared per deployment.
# The JSON API also needs a custom header, which a cross-site page cannot send.
_deployment = os.environ.get("VERCEL_DEPLOYMENT_ID") or os.environ.get("VERCEL_GIT_COMMIT_SHA") or "ranksim-hosted"
CSRF = hashlib.sha256(f"ranksim-csrf:{_deployment}:{os.environ.get('RANKSIM_SECRET', '')}".encode()).hexdigest()[:43]

if PRIVATE:
    from aeo.ranksim.cloud import CloudState

    settings = Settings(_env_file=None, ranksim_database_url=DATABASE)  # provider keys come from the environment
    cloud = CloudState.from_env(DATA, DATA / "rank.db")
    app = create_app(
        settings=settings,
        database=DATABASE,
        projects_root=DATA / "projects",
        public_demo=False,
        csrf_token=CSRF,
        access_password=PASSWORD,
        access_secret=os.environ.get("RANKSIM_SECRET") or None,
        cloud=cloud,
        pull_options={"engines": [], "workers": 8, "delay_seconds": 0.25},
    )
else:
    settings = Settings(_env_file=None, ranksim_database_url=DATABASE, **{name: None for name in KEYS})
    app = create_app(settings=settings, database=DATABASE, projects_root=DATA / "projects", public_demo=True, csrf_token=CSRF)
