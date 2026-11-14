"""The editor in a real browser (tests/js/editor/e2e_editor.cjs): edit, undo, redo, insert an FAQ, download, and check
the original scripts survived. Offline: the page comes from a fixture. Skipped when node or Playwright's Chromium is
not available."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "editor" / "e2e_editor.cjs"


def _playwright_ready() -> bool:
    if shutil.which("node") is None:
        return False
    probe = "require('playwright').chromium.launch().then((b) => b.close()).then(() => process.exit(0), () => process.exit(1))"
    try:
        return subprocess.run(["node", "-e", probe], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(not _playwright_ready(), reason="node with Playwright and Chromium is not available")
def test_editor_round_trip_in_a_browser(tmp_path):
    port = _free_port()
    env = {
        **os.environ,
        "RANKSIM_PASSWORD": "e2e-local",
        "RANKSIM_SECRET": "e2e-secret",
        "RANKSIM_DATA_DIR": str(tmp_path / "data"),
    }
    for key in ("SERPER_API_KEY", "SERPAPI_KEY", "SERPAPI_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "OPENROUTER_API_KEY", "BLOB_READ_WRITE_TOKEN"):
        env.pop(key, None)
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/", timeout=1)
                break
            except OSError:
                time.sleep(0.2)
        else:
            pytest.fail("the server did not start")
        done = subprocess.run(
            ["node", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=240,
            env={**os.environ, "BASE": base, "RANKSIM_PASSWORD": "e2e-local"},
        )
        assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
        assert "FAIL" not in done.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
