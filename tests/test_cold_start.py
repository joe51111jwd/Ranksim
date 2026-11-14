"""The public app's cold start: the shell and the session answer without training or importing the model stack.

Runs in a subprocess so sys.modules starts clean. No keys, no network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r"""
import json, sys
import app
from fastapi.testclient import TestClient
heavy = ("lightgbm", "numpy", "scipy", "pandas")
out = {"after_import": [m for m in heavy if m in sys.modules]}
client = TestClient(app.app)
out["shell"] = client.get("/").status_code
out["session"] = client.get("/api/v1/session").json()
out["asset"] = client.get("/studio/js/app.js").status_code
out["static"] = client.get("/static/app.css").status_code
out["healthz"] = client.get("/healthz").status_code
out["after_shell"] = [m for m in heavy if m in sys.modules]
import os
out["built_before_use"] = os.path.isdir(os.path.join(os.environ["RANKSIM_DATA_DIR"], "projects", "synthetic-demo"))
listing = client.get("/api/v1/projects").json()
out["projects"] = [(row["slug"], row["built"]) for row in listing["projects"]]
report = client.get("/api/v1/projects/synthetic-demo/report")
out["report"] = report.status_code
body = report.json()
out["predicted"] = sum(1 for row in body.get("rankings", []) if row.get("predicted"))
out["again"] = client.get("/api/v1/projects/synthetic-demo/report").json() == body
print("RESULT " + json.dumps(out))
"""


def test_public_app_serves_the_shell_without_training_then_builds_the_demo_on_first_use(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("RANKSIM_", "BLOB_", "VERCEL"))}
    for name in ("SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(name, None)
    env["RANKSIM_DATA_DIR"] = str(tmp_path / "data")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "src")])
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]
    line = next(line for line in done.stdout.splitlines() if line.startswith("RESULT "))
    out = json.loads(line.removeprefix("RESULT "))
    assert out["after_import"] == [] and out["after_shell"] == [], out
    assert out["shell"] == 200 and out["asset"] == 200 and out["static"] == 200 and out["healthz"] == 200
    assert out["session"]["mode"] == "public"
    assert out["built_before_use"] is False  # nothing was trained to answer the shell
    assert out["projects"] == [["synthetic-demo", True]]
    assert out["report"] == 200 and out["predicted"] > 0 and out["again"] is True
