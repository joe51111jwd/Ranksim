"""The Studio's browser modules, checked without a browser: every import resolves to a file that exports the name,
no module URL carries a query string, and the UI copy has no em or en dashes."""

from __future__ import annotations

import re
from pathlib import Path

from aeo.ranksim import studio

JS = studio.STATIC / "js"
IMPORT = re.compile(r"""^import\s+(?:(?P<names>\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?['"](?P<spec>[^'"]+)['"];?""", re.M)
EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([\w$]+)", re.M)
EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}", re.M)
DASHES = ("—", "–")


def _modules() -> list[Path]:
    return sorted(JS.rglob("*.js"))


def _exports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    names = set(EXPORT.findall(text))
    for group in EXPORT_LIST.findall(text):
        for item in group.split(","):
            item = item.strip()
            if item:
                names.add(item.split(" as ")[-1].strip())
    return names


def test_core_modules_exist():
    names = {path.relative_to(JS).as_posix() for path in _modules()}
    for name in (
        "app.js", "core.js", "hooks.js",
        "screens/overview.js", "screens/issues.js", "screens/searches.js", "screens/competitors.js", "screens/settings.js",
        "editor/preview.js", "editor/editor.js", "editor/test.js", "editor/publish.js",
    ):
        assert name in names, name
    assert (studio.STATIC / "js" / "plugins" / "README.md").is_file() and (studio.STATIC / "css" / "plugins" / "README.md").is_file()
    assert not (studio.STATIC / "studio.js").exists() and not (studio.STATIC / "studio.css").exists()


def test_every_import_resolves_and_is_exported():
    checked = 0
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        for match in IMPORT.finditer(text):
            spec = match.group("spec")
            assert spec.startswith(("./", "../")), f"{path.name}: {spec} must be a relative module path"
            assert "?" not in spec and "#" not in spec, f"{path.name}: {spec} must not carry a query string"
            target = (path.parent / spec).resolve()
            assert target.is_file() and target.is_relative_to(JS.resolve()), f"{path.name}: {spec} does not exist"
            names = match.group("names") or ""
            if names.startswith("{"):
                exported = _exports(target)
                for item in names.strip("{} \n").split(","):
                    item = item.strip()
                    if not item:
                        continue
                    name = item.split(" as ")[0].strip()
                    assert name in exported, f"{path.relative_to(JS)} imports {name} from {spec}, which does not export it"
                    checked += 1
    assert checked > 100


def test_hooks_api_matches_the_build_plan():
    exported = _exports(JS / "hooks.js")
    for name in ("on", "call", "collect", "registerScreen", "registerOverviewTile", "registerEditorPanel", "registerEditorTool",
                 "registerSettingsSection", "registerSearchColumn"):
        assert name in exported, name
    text = (JS / "hooks.js").read_text(encoding="utf-8")
    for event in ("report-loaded", "editor-ready", "draft-changed", "test-finished"):
        assert f"'{event}'" in text, event
        fired = any(f"call('{event}'" in path.read_text(encoding="utf-8") for path in _modules())
        assert fired, f"nothing fires {event}"
    core = _exports(JS / "core.js")
    for name in ("h", "icon", "ICONS", "put", "clear", "debounce", "esc", "hostOf", "pathOf", "looseKey", "initial", "bar", "ring",
                 "request", "get", "post", "api", "stream", "S", "parseLocation", "pathFor", "navigate", "go", "link", "toast",
                 "openSheet", "closeSheet", "page", "footer"):
        assert name in core, name


def test_no_inline_handlers_or_dashes_in_the_studio_files():
    for path in sorted(studio.STATIC.rglob("*")):
        if path.suffix not in {".js", ".css", ".html", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(dash in text for dash in DASHES), path.relative_to(studio.STATIC)
    shell = (studio.STATIC / "studio.html").read_text(encoding="utf-8")
    assert "<script>" not in shell and " onclick=" not in shell.lower()
