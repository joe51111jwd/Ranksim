"""Client delivery: share links, the read-only client report, CSV export, branding and the change log.

No network, no real keys. The leak tests are adversarial: other projects, owner-only routes, API keys, page HTML,
test history internals, private notes and server paths must never reach a share link."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aeo.ranksim import workspace as ws
from aeo.ranksim.plugins import share
from aeo.ranksim.serve import create_app
from tests.test_workspace import make_settings

DASHES = ("—", "–")
FAKE_KEYS = {
    "serper_api_key": "FAKE-SERPER-KEY-7c1d0e",
    "openrouter_api_key": "FAKE-OPENROUTER-KEY-55aa",
    "openai_api_key": "FAKE-OPENAI-KEY-9f9f",
}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64
OTHER_NAME = "Acme Hidden Client"


def _csrf(client: TestClient) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get("/").text)
    assert found
    return found.group(1)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("share")
    settings = make_settings(tmp, **FAKE_KEYS)
    app = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp / "projects", public_demo=False)
    ui = app.state.ui
    if not any(getattr(route, "path", "") == "/r/{token}" for route in app.routes):
        share.register(app, ui)  # before P0 wiring the plugin is registered by hand
    client = TestClient(app)
    token = _csrf(client)
    headers = {"x-ranksim-csrf": token}
    assert client.post("/api/v1/demo", json={}, headers=headers).status_code == 200
    made = client.post(
        "/api/v1/projects",
        json={"name": OTHER_NAME, "site": "acme-hidden.example", "queries": "acme hidden widgets\nacme secret gadget"},
        headers=headers,
    )
    assert made.status_code == 200, made.text
    return {"client": client, "ui": ui, "csrf": token, "headers": headers, "demo": ws.DEMO_SLUG, "other": made.json()["slug"], "root": Path(ui.root)}


@pytest.fixture(autouse=True)
def _fresh_limits(env):
    env["ui"]._share_state = None
    yield
    env["ui"]._share_state = None


def _link(env, slug=None, **body) -> dict:
    response = env["client"].post(f"/api/v1/projects/{slug or env['demo']}/share", json=body, headers=env["headers"])
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# create, use, revoke, expiry
# ---------------------------------------------------------------------------


def test_create_link_and_open_the_client_report(env):
    client = env["client"]
    link = _link(env, label="For the client")
    assert share.TOKEN_RE.match(link["token"]) and link["path"] == f"/r/{link['token']}" and link["status"] == "active"
    assert env["demo"] not in link["path"]  # the slug never appears in the URL
    page = client.get(link["path"])
    assert page.status_code == 200
    assert 'name="robots" content="noindex' in page.text
    assert "/r/assets/share.js" in page.text and "<script>" not in page.text and "style=" not in page.text
    assert page.headers["x-robots-tag"].startswith("noindex")
    assert page.headers["referrer-policy"] == "no-referrer"
    assert page.headers["cache-control"] == "no-store"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp and "https:" not in csp
    data = client.get(link["path"] + "/data")
    assert data.status_code == 200 and data.headers["x-robots-tag"].startswith("noindex")
    body = data.json()
    assert body["site"] == "www.northwind-fencing.example"
    assert body["kpis"]["searches"] == 6 and body["preview"] is False
    assert body["data"]["demo"] is True and "not Google results" in body["data"]["note"]
    assert {row["q"] for row in body["rankings"]} and body["issues"] and body["share"]
    listed = client.get(f"/api/v1/projects/{env['demo']}/share").json()["links"]
    assert listed[0]["id"] == link["id"] and listed[0]["last_used"]


def test_assets_are_served_for_the_share_page(env):
    client = env["client"]
    for name, kind in (("share.css", "text/css"), ("share.js", "javascript"), ("sans.woff2", "font/woff2")):
        response = client.get(f"/r/assets/{name}")
        assert response.status_code == 200 and kind in response.headers["content-type"], name
    for name in ("index.html", "../share.py", "secret.key", "studio.js"):
        assert client.get(f"/r/assets/{name}").status_code == 404


def test_revoke_turns_off_every_link_route(env):
    client = env["client"]
    link = _link(env)
    assert client.get(link["path"] + "/data").status_code == 200
    revoked = client.post(f"/api/v1/projects/{env['demo']}/share/{link['id']}/revoke", headers=env["headers"])
    assert revoked.status_code == 200
    assert next(row for row in revoked.json()["links"] if row["id"] == link["id"])["status"] == "revoked"
    for suffix in ("", "/data", "/logo", "/rankings.csv", "/issues.csv"):
        response = client.get(link["path"] + suffix)
        assert response.status_code == 404, suffix
        assert "expired or was turned off" in response.text
    assert client.post(f"/api/v1/projects/{env['demo']}/share/{'x' * 24}/revoke", headers=env["headers"]).status_code == 404
    assert client.post(f"/api/v1/projects/{env['demo']}/share/../revoke", headers=env["headers"]).status_code in {404, 405}


def test_expiry(env, monkeypatch):
    client = env["client"]
    link = _link(env, expires_days=1)
    assert link["expires"]
    assert client.get(link["path"] + "/data").status_code == 200
    later = share._now() + timedelta(days=1, minutes=1)
    monkeypatch.setattr(share, "_now", lambda: later)
    assert client.get(link["path"] + "/data").status_code == 404
    assert client.get(link["path"]).status_code == 404
    status = next(row for row in client.get(f"/api/v1/projects/{env['demo']}/share").json()["links"] if row["id"] == link["id"])
    assert status["status"] == "expired"
    for bad in (0.5, -1, 366, "soon"):
        response = client.post(f"/api/v1/projects/{env['demo']}/share", json={"expires_days": bad}, headers=env["headers"])
        assert response.status_code == 400, bad
    assert _link(env, expires_days=None)["expires"] is None


def test_owner_writes_need_csrf_and_are_blocked_on_the_public_demo(env, tmp_path):
    client = env["client"]
    assert client.post(f"/api/v1/projects/{env['demo']}/share", json={}).status_code == 403
    assert client.post(f"/api/v1/projects/{env['demo']}/share", json={}, headers={"x-ranksim-csrf": "nope"}).status_code == 403
    assert client.post("/api/v1/branding", json={"agency": "x"}).status_code == 403
    assert client.post("/api/v1/branding/logo", content=PNG, headers={"content-type": "image/png"}).status_code == 403
    assert client.post(f"/api/v1/projects/nope/share", json={}, headers=env["headers"]).status_code == 404

    settings = make_settings(tmp_path)
    public = create_app(settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects", public_demo=True)
    if not any(getattr(route, "path", "") == "/r/{token}" for route in public.routes):
        share.register(public, public.state.ui)
    pclient = TestClient(public)
    headers = {"x-ranksim-csrf": _csrf(pclient)}
    assert pclient.post(f"/api/v1/projects/{ws.DEMO_SLUG}/share", json={}, headers=headers).status_code == 403
    assert pclient.post("/api/v1/branding", json={"agency": "x"}, headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# adversarial: forged tokens, other projects, owner routes, leaks
# ---------------------------------------------------------------------------


def test_forged_and_tampered_tokens_get_the_same_answer(env):
    client = env["client"]
    link = _link(env)
    link_id, signature = link["token"].split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    other_sig = share._signature(env["root"], env["other"], link_id)  # right id, signed for another project
    forged = [
        f"{link_id}.{flipped}",
        f"{'a' * 24}.{signature}",
        f"{link_id}.{other_sig}",
        link_id,
        link["token"] + "x",
        link["token"].lower(),
        "assets",
        "..%2f..%2fapi%2fv1%2fprojects",
        "%2e%2e",
    ]
    expected = client.get(f"/r/{'b' * 24}.{'c' * 22}/data")
    assert expected.status_code == 404
    for token in forged:
        response = client.get(f"/r/{token}/data")
        assert response.status_code == 404, token
        if response.headers.get("content-type", "").startswith("application/json"):
            assert response.json() == expected.json()
        assert "projects" not in response.text.lower() or "expired" in response.text
    assert share.resolve_token(env["ui"], None) is None and share.resolve_token(env["ui"], "x" * 5000) is None


def test_a_link_never_reaches_another_project(env):
    client = env["client"]
    demo = client.get(_link(env)["path"] + "/data").json()
    other_link = _link(env, slug=env["other"])
    other = client.get(other_link["path"] + "/data").json()
    assert other["name"] == OTHER_NAME and other["site"] == "acme-hidden.example"
    assert OTHER_NAME not in json.dumps(demo) and "acme" not in json.dumps(demo).lower()
    assert "northwind" not in json.dumps(other).lower()
    # moving a link file to another project does not move the link: the signature binds the slug
    root = env["root"]
    source = root / env["other"] / "plugins" / "share" / "links.json"
    target_dir = root / "zz-copy" / "plugins" / "share"
    target_dir.mkdir(parents=True)
    (root / "zz-copy" / "project.json").write_text((root / env["other"] / "project.json").read_text().replace(env["other"], "zz-copy"))
    (target_dir / "links.json").write_text(source.read_text())
    source.rename(source.with_suffix(".bak"))
    try:
        assert client.get(other_link["path"] + "/data").status_code == 404
    finally:
        source.with_suffix(".bak").rename(source)
        import shutil

        shutil.rmtree(root / "zz-copy")
    assert client.get(other_link["path"] + "/data").status_code == 200


def test_a_share_link_does_not_open_owner_routes(tmp_path):
    settings = make_settings(tmp_path, **FAKE_KEYS)
    app = create_app(
        settings=settings, database=settings.ranksim_database_url, projects_root=tmp_path / "projects",
        public_demo=False, access_password="owner-pass", access_secret="test-secret",
    )
    ui = app.state.ui
    if not any(getattr(route, "path", "") == "/r/{token}" for route in app.routes):
        share.register(app, ui)
    owner = TestClient(app)
    csrf = _csrf(owner)
    assert owner.post("/api/v1/login", json={"password": "owner-pass"}, headers={"x-ranksim-csrf": csrf}).status_code == 200
    assert owner.post("/api/v1/demo", json={}, headers={"x-ranksim-csrf": csrf}).status_code == 200
    link = owner.post(f"/api/v1/projects/{ws.DEMO_SLUG}/share", json={}, headers={"x-ranksim-csrf": csrf}).json()

    stranger = TestClient(app)
    owner_only = [
        f"/api/v1/projects/{ws.DEMO_SLUG}/share",
        f"/api/v1/projects/{ws.DEMO_SLUG}/client-report",
        f"/api/v1/projects/{ws.DEMO_SLUG}/changelog",
        f"/api/v1/projects/{ws.DEMO_SLUG}/issue-plans",
        f"/api/v1/projects/{ws.DEMO_SLUG}/export/rankings.csv",
        f"/api/v1/projects/{ws.DEMO_SLUG}/report",
        f"/api/v1/projects/{ws.DEMO_SLUG}/tests",
        "/api/v1/projects",
        "/api/v1/branding",
        "/api/v1/branding/logo",
    ]
    if _share_is_open(app):
        page = stranger.get(link["path"])
        assert page.status_code == 200
        assert stranger.get(link["path"] + "/data").status_code == 200
        assert "rs_session" not in stranger.cookies
    for path in owner_only:
        response = stranger.get(path, follow_redirects=False)
        assert response.status_code == 401, path
    preview = stranger.get(f"/report/{ws.DEMO_SLUG}", follow_redirects=False)
    assert preview.status_code in {303, 401}


def _share_is_open(app) -> bool:
    """True once the login middleware lets /r/ through (phase B wiring)."""
    from aeo.ranksim import studio

    return studio._is_open("/r/x") if hasattr(studio, "_is_open") else False


def test_nothing_private_leaks_into_the_client_report(env, monkeypatch):
    client = env["client"]
    ui = env["ui"]
    project = ui.project(env["demo"])
    share.log_change(project, "note", "INTERNAL-NOTE-ONLY-OWNER", detail="private detail", public=False)
    share.log_change(project, "note", "Public note for the client", detail="OWNER-DETAIL-XYZ")
    fake_run = {
        "id": "20261113T101010Z-abcdef",
        "search": "vinyl fence installation cost",
        "url": "https://www.northwind-fencing.example/secret-draft/",
        "created": "2026-11-13T10:10:10Z",
        "before": {"position": 5},
        "after": {"position": 2, "of": 7},
        "draft_html": "<html>DRAFT-HTML-SECRET</html>",
        "facts_after": {"title": "DRAFT-TITLE-SECRET"},
    }
    monkeypatch.setattr(ws, "list_draft_runs", lambda project, limit=12: [fake_run])
    hidden = _link(env, label="LINK-LABEL-SECRET")
    _link(env, slug=env["other"], label="OTHER-LINK")
    link = _link(env)
    path = link["path"]
    texts = [client.get(path).text, client.get(path + "/data").text, client.get(path + "/rankings.csv").text, client.get(path + "/issues.csv").text]
    blob = "\n".join(texts)
    forbidden = [
        *FAKE_KEYS.values(),
        env["csrf"],
        str(env["root"]),
        str(env["root"].parent),
        env["demo"],
        env["other"],
        OTHER_NAME,
        "INTERNAL-NOTE-ONLY-OWNER",
        "OWNER-DETAIL-XYZ",
        "private detail",
        "DRAFT-HTML-SECRET",
        "DRAFT-TITLE-SECRET",
        "secret-draft",
        fake_run["id"],
        "LINK-LABEL-SECRET",
        hidden["token"],
        hidden["id"],
        "pull:",
        "secret.key",
        "draft_html",
        "selector",
        "evidence",
        "pairwise",
        "accuracy",
        "ndcg",
        "spearman",
        "<!doctype",
        "rs_session",
    ]
    for value in forbidden:
        assert value not in blob or (value == "<!doctype" and texts[0].count("<!doctype") == 1 and value not in "\n".join(texts[1:])), value
    data = client.get(path + "/data").json()
    assert set(data) == {
        "preview", "agency", "name", "site", "report_date", "google_date", "data", "search_data", "predictions",
        "kpis", "rankings", "issues", "share", "competitors", "changes", "notes",
    }
    summaries = [row["summary"] for row in data["changes"]]
    assert "Public note for the client" in summaries and 'Page change tested for "vinyl fence installation cost"' in summaries
    assert all(set(row) == {"at", "date", "kind", "label", "summary"} for row in data["changes"])
    for issue in data["issues"]:
        assert set(issue) == {"id", "severity", "title", "comment", "fix", "page", "search", "status", "status_label", "plan"}
    for row in data["rankings"]:
        assert set(row) == {"q", "position", "url", "checked", "predicted", "top"}
        if row["predicted"]:
            assert row["predicted"]["label"] == "PREDICTED"
    assert any("PREDICTED" in note for note in data["notes"]) and any("Google" in note for note in data["notes"])


def test_the_client_report_labels_every_number(env):
    data = env["client"].get(_link(env)["path"] + "/data").json()
    assert data["google_date"]
    assert data["predictions"] is True
    assert all(row["predicted"] is None or row["predicted"]["label"] == "PREDICTED" for row in data["rankings"])
    js = (share.SHARE_STATIC / "share.js").read_text(encoding="utf-8")
    for label in ("PREDICTED", "Google data ", "Report date ", "Google positions from "):
        assert label in js


def test_rate_limit(env):
    client = env["client"]
    ui = env["ui"]
    link = _link(env)
    state = share._state(ui)
    state["bad"] = share.RateLimiter(3)
    for _ in range(3):
        assert client.get(f"/r/{'q' * 24}.{'w' * 22}/data").status_code == 404
    blocked = client.get(link["path"] + "/data")
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    state["bad"] = share.RateLimiter(100)
    state["requests"] = share.RateLimiter(2)
    assert client.get(link["path"] + "/data").status_code == 200
    assert client.get(link["path"]).status_code == 200
    assert client.get(link["path"] + "/data").status_code == 429
    assert client.get("/r/assets/share.js").status_code == 429


def test_rate_limiter_window_and_memory_bound():
    limiter = share.RateLimiter(2, window=60, max_keys=10)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=1) and not limiter.allow("a", now=2)
    assert limiter.allow("a", now=61)
    for index in range(50):
        limiter.allow(f"k{index}", now=100)
    assert len(limiter._hits) <= 10


def test_static_share_files_are_clean():
    for name in ("index.html", "share.js", "share.css"):
        text = (share.SHARE_STATIC / name).read_text(encoding="utf-8")
        assert not any(dash in text for dash in DASHES), name
    js = (share.SHARE_STATIC / "share.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js and "document.write" not in js
    css = (share.SHARE_STATIC / "share.css").read_text(encoding="utf-8")
    assert "@media print" in css and "gradient" not in css and "blur" not in css and "serif;" not in css.replace("sans-serif;", "")
    for token in ("--bg: #f6f6f3", "--tile: #ededE8", "--card: #ffffff", "--blue: #3358eb", "--dark: #111111"):
        assert token in css


# ---------------------------------------------------------------------------
# branding and logo validation
# ---------------------------------------------------------------------------


def test_logo_validation():
    assert share.check_logo(PNG, "image/png") == "png"
    assert share.check_logo(JPEG, "image/jpeg") == "jpg"
    assert share.check_logo(WEBP, "image/webp; charset=binary") == "webp"
    bad = [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "image/svg+xml"),
        (b"GIF89a" + b"\x00" * 20, "image/gif"),
        (b"<html><script>alert(1)</script>", "image/png"),
        (JPEG, "image/png"),
        (PNG, "text/html"),
        (b"", "image/png"),
        (PNG + b"\x00" * share.MAX_LOGO_BYTES, "image/png"),
    ]
    for data, mime in bad:
        with pytest.raises(ValueError):
            share.check_logo(data, mime)


def test_branding_and_logo_over_http(env):
    client = env["client"]
    headers = env["headers"]
    assert client.post("/api/v1/branding", json={"agency": "x" * 81}, headers=headers).status_code == 400
    saved = client.post("/api/v1/branding", json={"agency": "  Camarota\x00 SEO  "}, headers=headers)
    assert saved.status_code == 200 and saved.json()["agency"] == "Camarota SEO"
    refused = client.post("/api/v1/branding/logo", content=b"<svg><script>alert(1)</script></svg>", headers={**headers, "content-type": "image/svg+xml"})
    assert refused.status_code == 400
    lying = client.post("/api/v1/branding/logo", content=b"<html>hi</html>", headers={**headers, "content-type": "image/png"})
    assert lying.status_code == 400
    big = client.post("/api/v1/branding/logo", content=PNG + b"\x00" * share.MAX_LOGO_BYTES, headers={**headers, "content-type": "image/png"})
    assert big.status_code == 413
    ok = client.post("/api/v1/branding/logo", content=PNG, headers={**headers, "content-type": "image/png"})
    assert ok.status_code == 200 and ok.json()["logo"] is True
    stored = list((env["root"] / "_plugins" / "share").glob("logo.*"))
    assert [path.name for path in stored] == ["logo.png"]
    replaced = client.post("/api/v1/branding/logo", content=JPEG, headers={**headers, "content-type": "image/jpeg"})
    assert replaced.status_code == 200
    assert [path.name for path in (env["root"] / "_plugins" / "share").glob("logo.*")] == ["logo.jpg"]
    link = _link(env)
    data = client.get(link["path"] + "/data").json()
    assert data["agency"] == {"name": "Camarota SEO", "logo": True}
    logo = client.get(link["path"] + "/logo")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/jpeg" and logo.content == JPEG
    assert logo.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/v1/branding/logo").status_code == 200
    # the agency folder is never a project
    assert "_plugins" not in json.dumps(client.get("/api/v1/projects").json())
    assert client.post("/api/v1/branding/logo/delete", headers=headers).json()["logo"] is False
    assert client.get(link["path"] + "/logo").status_code == 404


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def test_csv_exports(env):
    client = env["client"]
    project = env["ui"].project(env["demo"])
    data = share.client_report(env["ui"], project)
    first_issue = data["issues"][0]
    plan = client.post(
        f"/api/v1/projects/{env['demo']}/issues/{first_issue['id']}/plan",
        json={"status": "doing", "plan": "=HYPERLINK(\"http://evil\") rewriting the page", "title": first_issue["title"]},
        headers=env["headers"],
    )
    assert plan.status_code == 200
    link = _link(env)
    rankings = client.get(link["path"] + "/rankings.csv")
    assert rankings.status_code == 200 and rankings.headers["content-type"].startswith("text/csv")
    assert rankings.headers["content-disposition"].startswith("attachment;") and "rankings" in rankings.headers["content-disposition"]
    lines = rankings.text.lstrip("﻿").splitlines()
    assert lines[0] == "Search,Google position,Google data date,Page,PREDICTED position,PREDICTED of,Google #1"
    assert len(lines) == 1 + len(data["rankings"])
    assert any(line.startswith("vinyl fence installation cost,5,") for line in lines)
    issues = client.get(link["path"] + "/issues.csv").text.lstrip("﻿")
    assert issues.splitlines()[0] == "Severity,Issue,Page,Search,Comment,Fix,Status,Plan"
    assert "In progress" in issues and "'=HYPERLINK" in issues and ",=HYPERLINK" not in issues
    owner = client.get(f"/api/v1/projects/{env['demo']}/export/issues.csv")
    assert owner.status_code == 200 and "In progress" in owner.text
    assert client.get(f"/api/v1/projects/{env['demo']}/export/secrets.csv").status_code == 404
    assert client.get(link["path"] + "/secrets.csv").status_code == 404
    for value, expected in (("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2", "'-2"), ("@SUM", "'@SUM"), ("\tx", "'\tx"), ("plain", "plain"), (None, "")):
        assert share._cell(value) == expected


# ---------------------------------------------------------------------------
# change log
# ---------------------------------------------------------------------------


def test_change_log_helper_hook_and_merge(env):
    client = env["client"]
    ui = env["ui"]
    project = ui.project(env["other"])
    assert share.log_change(project, "publish", "Edits published to /pricing", detail="Webhook 200", key="publish:1")
    assert share.log_change(project, "publish", "Edits published to /pricing", key="publish:1") is None  # idempotent
    assert share.log_change(project, "nonsense", "Falls back to a note")["kind"] == "note"
    assert share.log_change(project, "note", "   ") is None

    class FakePull:
        pull_id = "20261114T120000Z"
        google_kind = "api"
        searches_with_results = 2

    share.after_pull(project=project, report=FakePull(), ui=ui)
    share.after_pull(project=project, report=FakePull(), ui=ui)
    share.after_pull(project=project, report={"pull_id": "20261114T130000Z", "google_kind": "carried"}, ui=ui)
    share.after_pull(project=project, report={}, ui=ui)
    entries = client.get(f"/api/v1/projects/{env['other']}/changelog").json()["entries"]
    summaries = [row["summary"] for row in entries]
    assert summaries.count("Google data refreshed for 2 searches") == 1
    assert "Site pages read again" in summaries and "Edits published to /pricing" in summaries
    assert entries == sorted(entries, key=lambda row: row["at"], reverse=True)
    published = next(row for row in entries if row["summary"] == "Edits published to /pricing")
    assert published["detail"] == "Webhook 200" and published["label"] == "Published"

    note = client.post(f"/api/v1/projects/{env['other']}/changelog", json={"summary": "Kickoff call", "public": False}, headers=env["headers"])
    assert note.status_code == 200
    assert client.post(f"/api/v1/projects/{env['other']}/changelog", json={"summary": ""}, headers=env["headers"]).status_code == 400
    public = share.changelog(project, public_only=True)
    assert "Kickoff call" not in [row["summary"] for row in public]
    assert all("detail" not in row for row in public)


def test_derived_entries_cover_pulls_already_on_disk(env):
    entries = share.changelog(env["ui"].project(env["demo"]))
    assert any(row["id"].startswith("pull:") for row in entries)


def test_resolving_an_issue_is_logged_and_shown_to_the_client(env):
    client = env["client"]
    project = env["ui"].project(env["demo"])
    issue = share.client_report(env["ui"], project)["issues"][-1]
    url = f"/api/v1/projects/{env['demo']}/issues/{issue['id']}/plan"
    assert client.post(url, json={"status": "finished"}, headers=env["headers"]).status_code == 400
    assert client.post(f"/api/v1/projects/{env['demo']}/issues/BAD%20ID/plan", json={"status": "done"}, headers=env["headers"]).status_code == 400
    assert client.post(url, json={"status": "done", "plan": "Rewrote the title", "title": issue["title"]}, headers=env["headers"]).status_code == 200
    assert client.post(url, json={"status": "done", "plan": "Rewrote the title", "title": issue["title"]}, headers=env["headers"]).status_code == 200
    plans = client.get(f"/api/v1/projects/{env['demo']}/issue-plans").json()
    assert plans["plans"][issue["id"]]["status"] == "done" and plans["statuses"]["doing"] == "In progress"
    data = client.get(_link(env)["path"] + "/data").json()
    shown = next(row for row in data["issues"] if row["id"] == issue["id"])
    assert shown["status_label"] == "Done" and shown["plan"] == "Rewrote the title"
    resolved = [row for row in data["changes"] if row["summary"] == f"Issue resolved: {issue['title']}"]
    assert len(resolved) == 1


def test_owner_preview_page_and_data(env):
    client = env["client"]
    page = client.get(f"/report/{env['demo']}")
    assert page.status_code == 200 and "/r/assets/share.js" in page.text and page.headers["x-robots-tag"].startswith("noindex")
    assert client.get("/report/no-such-project").status_code == 404
    data = client.get(f"/api/v1/projects/{env['demo']}/client-report").json()
    assert data["preview"] is True and data["site"] == "www.northwind-fencing.example"


def test_links_persist_across_restarts(env):
    link = _link(env)
    share._keys.clear()  # a new process re-reads the stored key
    assert env["client"].get(link["path"] + "/data").status_code == 200
    key_file = env["root"] / "_plugins" / "share" / "secret.key"
    assert re.match(r"^[0-9a-f]{64}$", key_file.read_text())
