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

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

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
app = create_app(settings=settings, database=DATABASE, projects_root=DATA / "projects", public_demo=True)
