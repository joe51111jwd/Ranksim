"""Plugins: the loader, every hook, and where the core calls them.

Test plugins are written to a temp folder that stands in for src/aeo/ranksim/plugins (its __path__ is patched), so
nothing is left in the real folder. Fakes for the crawler and Google; no network, no keys.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import textwrap
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_autopilot import _site_pages
from test_project import SEARCHES, SITE, TODAY, FakeCrawler

from aeo.ranksim import discovery, plugins
from aeo.ranksim import workspace as ws
from aeo.ranksim.audit import AuditContext
from aeo.ranksim.serve import create_app
from aeo.ranksim.site import SitePage, page_facts
from tests.test_hosted import FakeGoogle
from tests.test_serve_project import make_settings

PASSWORD = "plugin test password"
EXPIRED = {"error": "This page has expired. Reload it and try again."}

# Records every hook call with exactly the documented arguments: a hook called with a missing or an extra keyword
# raises TypeError, which the loader logs and skips, so the call would be missing from CALLS.
RECORDER = '''
from fastapi import Request
from fastapi.responses import JSONResponse

from aeo.ranksim.api.common import _check_header_csrf, _json_body

CALLS = []


def register(app, ui):
    CALLS.append(("register", {"app": app, "ui": ui}))

    @app.get("/api/v1/alpha/ping")
    def alpha_ping() -> JSONResponse:
        return JSONResponse({"pong": True})

    @app.post("/api/v1/alpha/echo")
    async def alpha_echo(request: Request) -> JSONResponse:
        _check_header_csrf(ui, request)
        body = await _json_body(request, 4096)
        return JSONResponse({"echo": body.get("x")})

    @app.post("/api/v1/alpha/forgot")
    async def alpha_forgot() -> JSONResponse:
        return JSONResponse({"ran": True})  # forgot the CSRF check; the loader refuses it anyway

    async def alpha_hook() -> JSONResponse:
        return JSONResponse({"hooked": True})

    alpha_hook.csrf_exempt = True
    app.post("/api/v1/alpha/hook")(alpha_hook)


def extend_report(report, *, project, meta, database, ctx, ui):
    CALLS.append(("extend_report", {"report": report, "project": project, "meta": meta, "database": database, "ctx": ctx, "ui": ui}))
    report["alpha"] = {"site": project.site, "issues_seen": len(report["issues"])}


def discovery_sources(*, project, pages, ui):
    CALLS.append(("discovery_sources", {"project": project, "pages": pages, "ui": ui}))
    return [
        {"q": "Excavation  Estimating Tips", "weight": 5.0, "source": "alpha"},
        {"q": "dirt crew login", "weight": 9.0, "source": "alpha"},
    ]


def audit_checks():
    def alpha_check(ctx):
        CALLS.append(("audit_check", {"ctx": ctx}))
        return [
            {"severity": "low", "kind": "alpha-note", "title": f"Alpha read {len(ctx.readable)} pages", "comment": "From alpha."},
            {"kind": "no-severity", "title": "dropped"},
        ]

    return [alpha_check]


def after_pull(*, project, report, ui):
    CALLS.append(("after_pull", {"project": project, "report": report, "ui": ui}))


def settings_schema():
    return {"fields": [{"key": "enabled", "type": "bool", "label": "Use alpha"}]}
'''

# Every hook raises. The app, every request and the other plugins must not notice.
BROKEN = '''
def register(app, ui):
    @app.get("/api/v1/broken/half")
    def half():
        return {"half": True}

    raise RuntimeError("register blew up with key=sk-should-not-leak")


def extend_report(report, **kwargs):
    raise RuntimeError("extend_report blew up")


def discovery_sources(**kwargs):
    raise RuntimeError("discovery_sources blew up")


def audit_checks():
    def bad_check(ctx):
        raise RuntimeError("check blew up")

    return [bad_check, "not a function"]


def after_pull(**kwargs):
    raise RuntimeError("after_pull blew up")


def settings_schema():
    raise RuntimeError("settings_schema blew up")
'''


@pytest.fixture()
def plugin_dir(tmp_path, monkeypatch):
    """An empty plugins folder in place of the real one. Write modules into it before the app is created."""
    folder = tmp_path / "plugin_folder"
    folder.mkdir()
    plugins.reset()
    monkeypatch.setattr(plugins, "__path__", [str(folder)])
    yield folder
    plugins.reset()


def _write(folder, name: str, source: str) -> None:
    (folder / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")


def _calls(hook: str, module: str = "alpha") -> list[dict]:
    return [kwargs for name, kwargs in sys.modules[f"aeo.ranksim.plugins.{module}"].CALLS if name == hook]


def _token(client: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def _post(client, path, body, token):
    return client.post(path, json=body, headers={"x-ranksim-csrf": token} if token else {})


def _lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _app(tmp_path, *, google: bool = True, password: str | None = None):
    settings = make_settings(tmp_path, serpapi_key="sk-test-plugins") if google else make_settings(tmp_path)
    options = {"fetcher": FakeCrawler(_site_pages()), "today": TODAY, "workers": 4}
    if google:
        options["serp_provider"] = FakeGoogle()
    login = {"access_password": password, "access_secret": "test-secret"} if password else {}
    app = create_app(
        settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=False, pull_options=options, **login
    )
    client = TestClient(app)
    return app, client, _token(client)


# -- the loader ----------------------------------------------------------------------------------


def test_plugins_load_once_in_name_order_and_skip_private_names(plugin_dir):
    for name in ("c_third", "a_first", "b_second"):
        _write(plugin_dir, name, f"from pathlib import Path\nwith open(Path(__file__).with_name('order.txt'), 'a') as f:\n    f.write('{name} ')\n")
    _write(plugin_dir, "_private", "raise RuntimeError('a name starting with _ is never imported')\n")
    assert plugins.names() == ["a_first", "b_second", "c_third"]
    assert (plugin_dir / "order.txt").read_text().split() == ["a_first", "b_second", "c_third"]
    plugins.load()
    plugins.call_all("anything")
    assert (plugin_dir / "order.txt").read_text().split() == ["a_first", "b_second", "c_third"]  # imported once
    assert "aeo.ranksim.plugins._private" not in sys.modules
    assert plugins.describe()["failed"] == []


def test_call_all_and_collect(plugin_dir):
    _write(plugin_dir, "a", "def hook(*, x):\n    return [x, x + 1]\n")
    _write(plugin_dir, "b", "def hook(*, x):\n    return None\n")
    _write(plugin_dir, "c", "def hook(*, x):\n    return x * 10\n")
    _write(plugin_dir, "d", "def hook(*, x):\n    raise ValueError('no')\n")
    _write(plugin_dir, "e", "hook = 'not callable'\n")
    _write(plugin_dir, "f", "X = 1\n")
    assert plugins.call_all("hook", x=1) == [[1, 2], None, 10]
    assert plugins.collect("hook", x=1) == [1, 2, 10]
    assert plugins.call_all("missing", x=1) == [] and plugins.collect("missing") == []


def test_a_broken_plugin_never_breaks_startup_or_a_request(plugin_dir, tmp_path, caplog):
    _write(plugin_dir, "alpha", RECORDER)
    _write(plugin_dir, "broken", BROKEN)
    _write(plugin_dir, "zz_import", "raise ImportError('a missing dependency, token=abc123secret')\n")
    caplog.set_level(logging.WARNING, logger="aeo.ranksim.plugins")
    caplog.set_level(logging.WARNING, logger="aeo.ranksim.audit")
    app, client, token = _app(tmp_path, google=False)

    listed = client.get("/api/v1/plugins").json()
    assert [row["name"] for row in listed["plugins"]] == ["alpha", "broken"]
    alpha, broken = listed["plugins"]
    assert alpha["schema"] == {"fields": [{"key": "enabled", "type": "bool", "label": "Use alpha"}]}
    assert set(alpha["hooks"]) == set(plugins.HOOKS) and broken["schema"] is None
    assert [row["name"] for row in listed["failed"]] == ["zz_import"]
    assert "ImportError" in listed["failed"][0]["error"] and "abc123secret" not in json.dumps(listed)

    assert _post(client, "/api/v1/demo", {}, token).status_code == 200
    report = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/report")
    assert report.status_code == 200 and report.json()["alpha"]["site"]  # the other plugin still ran
    assert client.get("/api/v1/alpha/ping").json() == {"pong": True}
    assert client.get("/api/v1/broken/half").json() == {"half": True}  # routes added before the raise stay
    pulled = _lines(_post(client, f"/api/v1/projects/{ws.DEMO_SLUG}/pull", {}, token))
    assert pulled[-1]["type"] == "done" and len(_calls("after_pull")) == 1
    analyzed = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))
    assert analyzed[-1]["type"] == "done"

    logged = caplog.text
    for hook in ("broken.register", "broken.extend_report", "broken.discovery_sources", "broken.after_pull", "broken.settings_schema", "bad_check", "zz_import"):
        assert hook in logged, hook
    assert "not callable" in logged
    assert "sk-should-not-leak" not in logged and "abc123secret" not in logged


# -- hooks get the documented arguments -------------------------------------------------------------


def test_every_hook_gets_the_documented_arguments(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path)
    ui = app.state.ui
    [registered] = _calls("register")
    assert registered["app"] is app and registered["ui"] is ui

    done = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))[-1]
    assert done["type"] == "done", done
    [found] = _calls("discovery_sources")
    assert found["project"] is None and found["ui"] is ui  # a first analysis: no project yet
    assert found["pages"] and all(isinstance(page, SitePage) for page in found["pages"])
    [after] = _calls("after_pull")
    assert after["project"].slug == done["slug"] and after["ui"] is ui and after["report"] == done["pull"]

    report = client.get(f"/api/v1/projects/{done['slug']}/report").json()
    [extended] = _calls("extend_report")
    assert extended["project"].slug == done["slug"] and extended["ui"] is ui and extended["database"] == ui.database
    assert extended["meta"].competitors == report["competitors"] and extended["ctx"] is not None
    assert extended["report"] is not report and "alpha" in report
    [checked] = _calls("audit_check")
    assert isinstance(checked["ctx"], AuditContext) and checked["ctx"].project.slug == done["slug"]
    assert checked["ctx"].database == ui.database and checked["ctx"].rankings and checked["ctx"].pages


# -- routes ----------------------------------------------------------------------------------------


def test_plugin_routes_need_the_login_and_the_csrf_header_like_core_routes(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path, password=PASSWORD)
    for path in ("/api/v1/alpha/ping", "/api/v1/plugins"):
        refused = client.get(path)
        assert refused.status_code == 401 and refused.json() == {"error": "Sign in first.", "login": True}
    for path in ("/api/v1/alpha/echo", "/api/v1/alpha/hook"):
        assert _post(client, path, {"x": 1}, token).status_code == 401
    assert _post(client, "/api/v1/login", {"password": PASSWORD}, token).status_code == 200

    assert client.get("/api/v1/alpha/ping").json() == {"pong": True}
    assert client.get("/api/v1/plugins").json()["plugins"][0]["name"] == "alpha"
    core = _post(client, "/api/v1/projects", {"name": "X", "site": "x.example", "queries": ["a b"]}, None)
    for path in ("/api/v1/alpha/echo", "/api/v1/alpha/forgot"):
        for bad in (None, "wrong"):
            refused = _post(client, path, {"x": 1}, bad)
            assert (refused.status_code, refused.json()) == (core.status_code, core.json()) == (403, EXPIRED), path
    assert _post(client, "/api/v1/alpha/echo", {"x": 7}, token).json() == {"echo": 7}
    assert _post(client, "/api/v1/alpha/forgot", {}, token).json() == {"ran": True}
    assert _post(client, "/api/v1/alpha/hook", {}, None).json() == {"hooked": True}  # csrf_exempt, still behind the login
    assert client.post("/api/v1/alpha/echo", content=b"x=1", headers={"x-ranksim-csrf": token, "content-type": "text/plain"}).json() == {"error": "Send JSON."}


# -- where the core calls the hooks -----------------------------------------------------------------


def test_extend_report_and_audit_checks_reach_the_report(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path, google=False)
    assert _post(client, "/api/v1/demo", {}, token).status_code == 200
    report = client.get(f"/api/v1/projects/{ws.DEMO_SLUG}/report").json()
    assert report["alpha"]["site"] == report["site"] and report["alpha"]["issues_seen"] == len(report["issues"])
    assert {"slug", "data", "engine", "kpis", "rankings", "share", "issues", "pages"} <= set(report)
    mine = [issue for issue in report["issues"] if issue["kind"] == "alpha-note"]
    assert len(mine) == 1 and not any(issue["kind"] == "no-severity" for issue in report["issues"])
    note = mine[0]
    assert note["title"] == f"Alpha read {report['pages_read']} pages" and note["id"] == "alpha-note"
    assert note["fix"] == "" and note["page"] is None and note["search"] is None
    assert report["kpis"]["issues"] == len(report["issues"])
    order = ["high", "medium", "low"]
    assert [issue["severity"] for issue in report["issues"]] == sorted((issue["severity"] for issue in report["issues"]), key=order.index)


def test_discovery_sources_rows_reach_the_analysis(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path, google=False)
    first = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))
    done = first[-1]
    assert done["type"] == "done", done
    assert "excavation estimating tips" in done["searches"]  # lowercased, spaces collapsed
    assert not any("dirt crew" in search for search in done["searches"])  # branded rows are dropped like branded headings
    found = next(line for line in first if line.get("step") == "searches")
    assert "excavation estimating tips" in found["searches"]
    setup = client.get(f"/api/v1/projects/{done['slug']}/setup").json()
    assert "excavation estimating tips" in [row["q"] for row in setup["queries"]]  # saved as one of the project's searches
    again = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))[-1]
    assert again["slug"] == done["slug"] and "excavation estimating tips" in again["searches"]
    assert _calls("discovery_sources")[1]["project"].slug == done["slug"]  # the project once it exists
    given = _lines(_post(client, "/api/v1/sites", {"site": SITE, "searches": ["dirt work software"]}, token))[-1]
    assert given["searches"] == ["dirt work software"] and len(_calls("discovery_sources")) == 2  # typed searches win


def test_after_pull_fires_after_every_pull_and_analysis(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path)
    made = _post(client, "/api/v1/projects", {"name": "Dirt Crew", "site": SITE, "queries": SEARCHES}, token).json()
    slug = made["slug"]
    pulled = _lines(_post(client, f"/api/v1/projects/{slug}/pull", {"google": True}, token))
    assert pulled[-1]["type"] == "done"
    [after] = _calls("after_pull")
    assert after["project"].slug == slug and after["report"] == pulled[-1]["report"] and after["report"]["searches_with_results"]

    analyzed = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))
    assert analyzed[-1]["type"] == "done" and analyzed[-1]["slug"] == slug
    assert len(_calls("after_pull")) == 2  # once per analysis, not once more for the pull inside it
    assert _calls("after_pull")[-1]["report"] == analyzed[-1]["pull"]

    classic = client.post(f"/p/{slug}/pull", data={"csrf": token}, follow_redirects=False)
    job = app.state.ui.jobs.wait(classic.headers["location"].split("job=")[-1], timeout=120)
    assert job is not None and job.status == "done", job
    assert len(_calls("after_pull")) == 3 and _calls("after_pull")[-1]["report"]["pull_id"] != after["report"]["pull_id"]


def test_after_pull_fires_after_an_analysis_without_search_data(plugin_dir, tmp_path):
    _write(plugin_dir, "alpha", RECORDER)
    app, client, token = _app(tmp_path, google=False)
    done = _lines(_post(client, "/api/v1/sites", {"site": SITE}, token))[-1]
    assert done["type"] == "done" and done["pull"] is None
    [after] = _calls("after_pull")
    assert after["report"] is None and after["project"].slug == done["slug"]


def test_no_plugins_lists_nothing(plugin_dir, tmp_path):
    app, client, token = _app(tmp_path, google=False)
    assert client.get("/api/v1/plugins").json() == {"plugins": [], "failed": []}


# -- the discovery merge and the data folder ----------------------------------------------------------


def _fixture_pages():
    return [page_facts(url, html) for url, html in _site_pages().items() if url.startswith(f"https://{SITE}/") and not url.endswith(".xml")]


def test_discovery_merges_weighted_rows_by_clear_rules():
    pages = _fixture_pages()
    brand = discovery.brand_of(pages, SITE)
    base = discovery.discover_searches(pages, brand, host=SITE)
    assert base and discovery.discover_searches(pages, brand, host=SITE, extra=[]) == base
    assert discovery.discover_with_plugins(pages, brand, host=SITE) == base  # no plugins: exactly the same list

    def merged(*rows, limit=discovery.MAX_SEARCHES):
        return discovery.discover_searches(pages, brand, host=SITE, extra=list(rows), limit=limit)

    assert "grading estimates" not in merged({"q": "grading estimates", "weight": 2.0})  # under MIN_SCORE on its own
    with_row = merged({"q": "Grading  Estimates", "weight": 3.0, "source": "gsc"})
    assert "grading estimates" in with_row and [q for q in with_row if q != "grading estimates"] == base  # joins, nothing else moves
    assert "grading estimates" in merged({"q": "grading estimates"})  # no weight counts like a title phrase
    last = base[-1]
    assert merged({"q": last, "weight": 100})[0] == last  # a row for a site phrase adds to its score
    assert "grading estimates" in merged({"q": "grading estimates", "weight": 2.0}, {"q": "grading estimates", "weight": 1.0})  # rows add up
    near = merged({"q": f"{base[0]} online", "weight": 50})
    assert near[0] == f"{base[0]} online" and base[0] not in near  # a near duplicate of a stronger phrase is merged away
    capped = merged(*({"q": f"topic{i} grading", "weight": 10 + i} for i in range(40)), limit=discovery.MAX_SEARCHES)
    assert len(capped) == discovery.MAX_SEARCHES and capped[0] == "topic39 grading"
    bad = [
        "not a dict", {"q": 5, "weight": 9}, {"q": "fine words", "weight": "9"}, {"q": "fine words", "weight": -1},
        {"q": "fine words", "weight": math.nan}, {"q": "fine words", "weight": math.inf}, {"q": "fine words", "weight": True},
        {"q": "   ", "weight": 9}, {"q": "x " * 60, "weight": 9}, {"q": "dirt crew pricing", "weight": 9}, {"q": "dirtcrew app", "weight": 9},
    ]
    assert merged(*bad) == base


def test_data_dir_is_inside_the_project_folder(tmp_path):
    project = SimpleNamespace(dir=tmp_path / "projects" / "dirt-crew")
    folder = plugins.data_dir(project, "gsc")
    assert folder == tmp_path / "projects" / "dirt-crew" / "plugins" / "gsc" and folder.is_dir()
    assert plugins.data_dir(project, "gsc") == folder
    for bad in ("", "../escape", "a/b", "GSC", ".hidden", "x" * 80, None):
        with pytest.raises(ValueError):
            plugins.data_dir(project, bad)
