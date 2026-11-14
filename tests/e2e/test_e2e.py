"""End-to-end: a real Chromium drives Ranksim against a local server with fakes (no network, no keys).

Skips when node or Playwright is missing (CI runs Python only). RANKSIM_SKIP_E2E=1 skips it on purpose.
Screenshots land in tests/e2e/screenshots/ (git-ignored) at deviceScaleFactor 2.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
NODE = shutil.which("node")


def _has_playwright() -> bool:
    if not NODE:
        return False
    probe = subprocess.run([NODE, "-e", "require('playwright')"], cwd=HERE, capture_output=True, timeout=60)
    return probe.returncode == 0


needs_node = pytest.mark.skipif(not NODE, reason="node is not installed")


@needs_node
@pytest.mark.parametrize("name", ["charts.test.mjs", "hooks.test.mjs"])
def test_node_unit(name):
    run = subprocess.run([NODE, "--test", str(HERE / name)], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(url: str, proc: subprocess.Popen, seconds: float = 60) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {proc.stdout.read() if proc.stdout else ''}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError("server did not start")


@pytest.mark.skipif(os.environ.get("RANKSIM_SKIP_E2E", "").strip() in {"1", "true", "yes"}, reason="RANKSIM_SKIP_E2E is set")
@pytest.mark.skipif(not _has_playwright(), reason="node or Playwright is not installed")
def test_end_to_end(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.endswith(("_API_KEY", "_KEY", "_PASSWORD", "_LOGIN", "_TOKEN", "_SECRET", "_CLIENT_ID"))}
    servers = []
    try:
        base = _start(servers, tmp_path / "data", env)
        more = _start(servers, tmp_path / "more", env)
        jobs = _start(servers, tmp_path / "jobs", env)
        # A third server whose job budget leaves almost no time, so every analysis pauses and the app posts again.
        paused = _start(servers, tmp_path / "paused", {**env, "RANKSIM_JOB_BUDGET_SECONDS": "27", "E2E_SLOW_SECONDS": "1.5"})
        out = str(HERE / "screenshots")
        # The main flow, the plugin features and the job checks run at the same time, each against its own server.
        runs = [
            subprocess.Popen([NODE, str(HERE / "run.mjs"), "--group", "main", "--base", base, "--out", out], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
            subprocess.Popen([NODE, str(HERE / "run.mjs"), "--group", "more", "--base", more, "--out", out], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
            subprocess.Popen([NODE, str(HERE / "run.mjs"), "--group", "jobs", "--base", jobs, "--paused-base", paused, "--out", out], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
        ]
        results = [run.communicate(timeout=900) + (run.returncode,) for run in runs]
    finally:
        for proc in servers:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    for stdout, stderr, code in results:
        lines = [line for line in stdout.splitlines() if line.startswith("{")]
        assert lines, stdout[-3000:] + stderr[-3000:]
        result = json.loads(lines[-1])
        assert result["ok"], json.dumps(result["failures"], indent=1)
        assert code == 0


def _start(servers: list, data: Path, env: dict) -> str:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", str(port), "--data", str(data)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    servers.append(server)
    base = f"http://127.0.0.1:{port}"
    _wait(base + "/api/v1/session", server)
    return base
