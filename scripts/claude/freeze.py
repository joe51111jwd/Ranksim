"""Freeze and verify the files a preregistered test depends on.

  python scripts/claude/freeze.py write  evals/preregistered/claude-<name> file1 file2 ...
  python scripts/claude/freeze.py verify evals/preregistered/claude-<name>

`write` records the sha256 of every listed file (paths relative to the repo root)
in <dir>/manifest.json. `verify` recomputes them and exits non-zero on any mismatch.
A scoring script calls `verify_manifest` before it opens a held-out day.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_manifest(directory: Path, files: list[str], extra: dict | None = None) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "label": "PREDICTED",
        "frozen_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": {name: sha256_file(ROOT / name) for name in sorted(files)},
    }
    if extra:
        manifest.update(extra)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_manifest(directory: Path) -> dict:
    """Raise SystemExit when a frozen file is missing or differs from the manifest."""
    manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for name, expected in manifest["sha256"].items():
        path = ROOT / name
        if not path.exists():
            bad.append(f"missing: {name}")
        elif sha256_file(path) != expected:
            bad.append(f"changed: {name}")
    if bad:
        raise SystemExit("Frozen files do not match the manifest:\n  " + "\n  ".join(bad))
    return manifest


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "write":
        manifest = write_manifest(ROOT / argv[1], argv[2:])
        print(json.dumps(manifest, indent=2))
        return 0
    if len(argv) == 2 and argv[0] == "verify":
        manifest = verify_manifest(ROOT / argv[1])
        print(f"ok: {len(manifest['sha256'])} files match")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
