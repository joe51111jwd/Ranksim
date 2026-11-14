"""Cold start: the server and the private Vercel entry point must not import LightGBM (or bs4) at start-up.

Each check runs in a fresh interpreter, so modules other tests imported do not hide a regression.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEAVY = ["lightgbm", "bs4", "lxml", "numpy", "scipy", "pandas"]


def _run(code: str, tmp_path: Path, **env: str) -> dict:
    clean = {key: value for key, value in os.environ.items() if not key.startswith(("RANKSIM_", "BLOB_", "VERCEL_"))}
    for key in list(clean):
        if key.endswith(("_API_KEY", "_KEY")) or key.startswith(("DATAFORSEO", "SERP")):
            clean.pop(key)
    clean.update({"PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "src")]), "RANKSIM_DATA_DIR": str(tmp_path / "data")})
    clean.update(env)
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=clean, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_creating_the_private_server_does_not_import_lightgbm(tmp_path):
    code = f"""
import json, sys
from aeo.config import Settings
from aeo.ranksim.serve import create_app
db = "sqlite:///{(tmp_path / 'rank.db').as_posix()}"
app = create_app(settings=Settings(_env_file=None, ranksim_database_url=db), database=db,
                 projects_root={str(tmp_path / 'projects')!r}, public_demo=False, access_password="pw")
print(json.dumps({{name: name in sys.modules for name in {HEAVY!r}}}))
"""
    loaded = _run(code, tmp_path)
    assert loaded == {name: False for name in HEAVY}


def test_private_entry_point_is_lazy_and_shares_login_state(tmp_path):
    code = """
import json, sys
import app
from aeo.ranksim.objects import DirStore
before = {name: name in sys.modules for name in %r}
hooked = any(type(f).__name__ == "_OpenMPBeforeLightGBM" for f in sys.meta_path)
calls = []
real = app._load_openmp
app._load_openmp = lambda: (calls.append(1), real())
import lightgbm
print(json.dumps({
    "before": before,
    "hooked": hooked,
    "preloaded_once": calls == [1],
    "unhooked": not any(type(f).__name__ == "_OpenMPBeforeLightGBM" for f in sys.meta_path),
    "store": isinstance(app.app.state.ui.access.store, DirStore),
    "enabled": app.app.state.ui.access.enabled,
}))
""" % (HEAVY,)
    out = _run(code, tmp_path, RANKSIM_PASSWORD="pw", RANKSIM_SECRET="sekrit")
    assert out["before"] == {name: False for name in HEAVY}
    assert out["hooked"] and out["preloaded_once"] and out["unhooked"]
    assert out["store"] and out["enabled"]


def test_private_entry_point_login_and_shared_failures(tmp_path):
    code = """
import json
import app
from aeo.ranksim import access
from fastapi.testclient import TestClient
access.FAILURE_DELAY_SECONDS = 0
client = TestClient(app.app)
headers = {"x-ranksim-csrf": app.CSRF}
bad = client.post("/api/v1/login", json={"password": "nope"}, headers=headers).status_code
store = app.app.state.ui.access.store
fails = store.list(access.FAIL_PREFIX)
ok = client.post("/api/v1/login", json={"password": "pw"}, headers=headers)
cookie = ok.cookies.get(access.COOKIE)
session = client.get("/api/v1/session").json()
print(json.dumps({"bad": bad, "fails": len(fails), "ok": ok.status_code, "v2": (cookie or "").startswith("2."),
                  "signed_in": session.get("signed_in")}))
"""
    out = _run(code, tmp_path, RANKSIM_PASSWORD="pw", RANKSIM_SECRET="sekrit")
    assert out == {"bad": 401, "fails": 1, "ok": 200, "v2": True, "signed_in": True}
