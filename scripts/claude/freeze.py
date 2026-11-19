"""Freeze and verify the files a preregistered test depends on.

  python scripts/claude/freeze.py write  evals/preregistered/claude-<name> file1 file2 ...
  python scripts/claude/freeze.py closure evals/preregistered/claude-<name> module1 module2 -- extra_file ...
  python scripts/claude/freeze.py verify evals/preregistered/claude-<name>

`write` records the sha256 of every listed file (paths relative to the repo root)
in <dir>/manifest.json. `closure` does the same for every repo file the named
modules import, plus the extra files, so a helper cannot change unnoticed. `verify` recomputes them and exits non-zero on any mismatch.
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


def repo_files_for(modules: list[str]) -> list[str]:
    """Repo-relative paths of every repo file that importing ``modules`` loads (their full import closure)."""
    import importlib
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for name in modules:
        importlib.import_module(name)
    found = set()
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        resolved = Path(path).resolve()
        if ROOT in resolved.parents and ".venv" not in resolved.parts:
            found.add(str(resolved.relative_to(ROOT)))
    return sorted(found)


def library_versions() -> dict:
    """Versions behind the bootstrap intervals and the consensus tables."""
    import platform

    found = {"python": platform.python_version()}
    for name in ("numpy", "pandas"):
        try:
            found[name] = __import__(name).__version__
        except ImportError:
            found[name] = None
    return found


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "write":
        manifest = write_manifest(ROOT / argv[1], argv[2:])
        print(json.dumps(manifest, indent=2))
        return 0
    if len(argv) >= 3 and argv[0] == "closure":
        # freeze.py closure <dir> <module> [<module> ...] [-- extra files]
        split = argv.index("--") if "--" in argv else len(argv)
        files = sorted(set(repo_files_for(argv[2:split])) | set(argv[split + 1 :]))
        manifest = write_manifest(ROOT / argv[1], files, extra={"modules": argv[2:split], "versions": library_versions()})
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
