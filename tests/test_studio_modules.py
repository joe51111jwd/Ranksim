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
    for name in ("on", "call", "collect", "registerScreen", "registerOverviewTile", "registerTile", "registerEditorPanel",
                 "registerEditorTool", "registerSettingsSection", "registerSearchColumn"):
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
    # api carries the whole client, stream included (BUILD_PLAN 4.3: "api (get/post/stream)")
    api_line = re.search(r"^export const api = \{([^}]*)\};", (JS / "core.js").read_text(encoding="utf-8"), re.M)
    assert api_line and {"request", "get", "post", "stream"} <= {part.strip() for part in api_line.group(1).split(",")}


# Which build session owns each core module (BUILD_PLAN section 5). core.js and hooks.js are the shared API.
OWNER = {
    "app.js": "shell", "charts.js": "shell", "core.js": "shell", "hooks.js": "shell", "screens/overview.js": "shell", "screens/settings.js": "shell",
    "screens/searches.js": "discover", "screens/competitors.js": "discover", "screens/issues.js": "audit",
    "editor/editor.js": "editor", "editor/preview.js": "editor", "editor/test.js": "predict", "editor/publish.js": "publish",
}
# The names core modules owned by different sessions import from each other (hooks.js "frozen module exports").
FROZEN = {
    "editor/editor.js": ("ED", "draftFor", "normalize", "paintFrame", "paintSide", "renderEditor", "syncFromFrame", "editorCtx", "getCompare"),
    "editor/preview.js": ("facts",),
    "editor/test.js": ("runTest", "renderResult"),
    "editor/publish.js": ("publishButton",),
    "screens/issues.js": ("issueRow",),
}


def test_frozen_module_exports_stay_exported_and_documented():
    hooks = (JS / "hooks.js").read_text(encoding="utf-8")
    frozen = hooks[hooks.index("frozen module exports"):]
    for module, names in FROZEN.items():
        exported = _exports(JS / module)
        assert module in frozen, module
        for name in names:
            assert name in exported, f"{module} must keep exporting {name}"
            assert re.search(rf"\b{name}\b", frozen), f"hooks.js must list {module} {name}"
    # every import from a module another session owns is in the frozen list
    for path in _modules():
        rel = path.relative_to(JS).as_posix()
        if rel.startswith("plugins/"):
            continue
        assert rel in OWNER, f"{rel} has no owner in BUILD_PLAN section 5"
        for match in IMPORT.finditer(path.read_text(encoding="utf-8")):
            target = (path.parent / match.group("spec")).resolve().relative_to(JS.resolve()).as_posix()
            names = match.group("names") or ""
            if target in ("core.js", "hooks.js") or OWNER.get(target) == OWNER[rel] or not names.startswith("{"):
                continue
            for item in names.strip("{} \n").split(","):
                name = item.split(" as ")[0].strip()
                if name:
                    assert name in FROZEN.get(target, ()), f"{rel} imports {name} from {target}; add it to the frozen list"


def test_screen_keys_have_the_shape_the_server_answers():
    # the server answers /app/<slug>/<key> with the app for keys of this shape (p0-backend studio.SCREEN_KEY)
    shape = r"[a-z0-9][a-z0-9_-]{0,39}"
    literal = re.compile(r"""\bregisterScreen\(\s*(['"])([^'"]*)\1""")
    keys = []
    for path in _modules():
        keys += [match.group(2) for match in literal.finditer(path.read_text(encoding="utf-8"))]
    assert {"overview", "issues", "searches", "competitors", "editor", "settings"} <= set(keys)
    assert all(re.fullmatch(shape, key) for key in keys), keys
    assert f"/^{shape}$/" in (JS / "hooks.js").read_text(encoding="utf-8")  # registerScreen warns about any other key


def test_settings_lists_server_plugin_schemas():
    text = (JS / "screens" / "settings.js").read_text(encoding="utf-8")
    assert "key: 'plugins'" in text and "'/api/v1/plugins'" in text
    assert "plugins 80" in (JS / "hooks.js").read_text(encoding="utf-8")


def test_no_inline_handlers_or_dashes_in_the_studio_files():
    for path in sorted(studio.STATIC.rglob("*")):
        if path.suffix not in {".js", ".css", ".html", ".md"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not any(dash in text for dash in DASHES), path.relative_to(studio.STATIC)
    shell = (studio.STATIC / "studio.html").read_text(encoding="utf-8")
    assert "<script>" not in shell and " onclick=" not in shell.lower()
