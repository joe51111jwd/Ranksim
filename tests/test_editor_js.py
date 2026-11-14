"""Runs the editor's JavaScript unit tests (tests/js/editor) with node's built-in runner. Skipped without node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_editor_js_unit_tests_pass():
    files = sorted(str(path) for path in (ROOT / "tests" / "js" / "editor").glob("*.test.mjs"))
    assert files, "the editor has node tests"
    done = subprocess.run(["node", "--test", *files], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]
