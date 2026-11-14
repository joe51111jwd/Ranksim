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


class _OpenMPBeforeLightGBM:
    """A one-shot import hook: the first `import lightgbm` preloads libgomp, then the normal import continues.

    Most cold starts (the private app, every request that does not train or score) never import LightGBM, so they skip
    the preload. find_spec runs before lightgbm's own code loads lib_lightgbm.so, so the library is already there."""

    def find_spec(self, name, path=None, target=None):
        if name == "lightgbm" or name.startswith("lightgbm."):
            _remove_openmp_hook()
            _load_openmp()
        return None  # let the regular finders import it


def _install_openmp_hook() -> None:
    if "lightgbm" in sys.modules:
        _load_openmp()
    elif not any(isinstance(finder, _OpenMPBeforeLightGBM) for finder in sys.meta_path):
        sys.meta_path.insert(0, _OpenMPBeforeLightGBM())


def _remove_openmp_hook() -> None:
    sys.meta_path[:] = [finder for finder in sys.meta_path if not isinstance(finder, _OpenMPBeforeLightGBM)]


_install_openmp_hook()

PASSWORD = os.environ.get("RANKSIM_PASSWORD", "").strip()
PRIVATE = bool(PASSWORD)
DATA = Path(os.environ.get("RANKSIM_DATA_DIR") or os.environ.get("RANKSIM_PUBLIC_DATA") or "/tmp/ranksim")
DATA.mkdir(parents=True, exist_ok=True)
if not PRIVATE:
    os.environ["RANKSIM_PUBLIC_DEMO"] = "1"
elif not os.environ.get("RANKSIM_SECRET"):
    # Without a secret the cookie key comes from the password alone, so one stolen cookie lets someone test password
    # guesses offline. Never print the value, only that it is missing.
    print("ranksim: RANKSIM_SECRET is not set; set a long random value so session cookies do not depend on the password alone",
          file=sys.stderr, flush=True)

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

def _share_login_state(built) -> None:
    """create_app builds its own Access without shared state; swap in one that every instance agrees with.

    On Vercel (cloud storage on) a global lockout and "sign out everywhere" live in the storage manifest each instance
    already reads, so a login costs no Blob operation (Hobby caps them). Off Vercel a folder under DATA is shared by
    one machine's processes. The password and secret are the same, so the signing key and every cookie stay the same.
    """
    from aeo.ranksim.access import Access

    ui = built.state.ui
    secret = os.environ.get("RANKSIM_SECRET") or None
    cloud = getattr(ui, "cloud", None)
    if cloud is not None and cloud.enabled:
        ui.access = Access(PASSWORD, secret, shared=cloud)
        return
    try:
        from aeo.ranksim.objects import DirStore

        ui.access = Access(PASSWORD, secret, store=DirStore(DATA / "objects"))
    except Exception as exc:  # noqa: BLE001 - login must keep working without a shared store
        print(f"ranksim: login limits stay per instance: {type(exc).__name__}", file=sys.stderr, flush=True)


def _build():
    """The FastAPI app for this environment. Vercel looks for a top-level `app`, so it is assigned once below."""
    if PRIVATE:
        from aeo.ranksim.cloud import CloudState

        settings = Settings(_env_file=None, ranksim_database_url=DATABASE)  # provider keys come from the environment
        built = create_app(
            settings=settings,
            database=DATABASE,
            projects_root=DATA / "projects",
            public_demo=False,
            csrf_token=CSRF,
            access_password=PASSWORD,
            access_secret=os.environ.get("RANKSIM_SECRET") or None,
            cloud=CloudState.from_env(DATA, DATA / "rank.db"),
            pull_options={"engines": [], "workers": 8, "delay_seconds": 0.25},
        )
        _share_login_state(built)
        return built
    settings = Settings(_env_file=None, ranksim_database_url=DATABASE, **{name: None for name in KEYS})
    return create_app(settings=settings, database=DATABASE, projects_root=DATA / "projects", public_demo=True, csrf_token=CSRF)


app = _build()
