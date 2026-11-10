"""A hosted, private Ranksim: password login, the project API end to end, and the Blob state mirror. No network."""

from __future__ import annotations

import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE, TODAY, FakeCrawler, _results, page_html

from aeo.ranksim.access import COOKIE, Access
from aeo.ranksim.cloud import POINTER, PREFIX, CloudState
from aeo.ranksim.serp import SerpHit
from aeo.ranksim.serve import create_app
from tests.test_serve_project import make_settings

PASSWORD = "correct horse battery staple"
SECRET_KEY = "sk-live-HOSTED-0123456789"


class FakeGoogle:
    name = "fakegoogle"
    is_google = True
    synthetic = False

    def search(self, query: str, count: int = 10) -> list[SerpHit]:
        urls = _results().get(query, [])[:count]
        return [
            SerpHit(position=index, url=url, title=f"Result {index} for {query}", snippet="", provider=self.name, synthetic=False, is_google=True)
            for index, url in enumerate(urls, start=1)
        ]


def _token(client: TestClient) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def _post(client, path, body, token):
    return client.post(path, json=body, headers={"x-ranksim-csrf": token} if token else {})


def _stream(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture()
def hosted(tmp_path):
    settings = make_settings(tmp_path, serpapi_key=SECRET_KEY)

    def preview_fetch(url: str) -> tuple[str, str]:
        return page_html("Live staging page", "Dirt Work Software", extra="dirt work software"), ""

    app = create_app(
        settings=settings,
        database=settings.ranksim_database_url,
        projects_root=tmp_path / "projects",
        public_demo=False,
        access_password=PASSWORD,
        access_secret="test-secret",
        pull_options={"serp_provider": FakeGoogle(), "fetcher": FakeCrawler(), "today": TODAY, "workers": 4},
        preview_fetch=preview_fetch,
    )
    client = TestClient(app)
    return {"client": client, "token": _token(client), "app": app, "tmp": tmp_path}


def _sign_in(hosted) -> None:
    ok = _post(hosted["client"], "/api/v1/login", {"password": PASSWORD}, hosted["token"])
    assert ok.status_code == 200


# -- access ------------------------------------------------------------------------------------


def test_session_cookie_is_signed_and_expires():
    access = Access("pw", "secret")
    value = access.issue(now=1000.0)
    assert access.valid(value, now=1001.0)
    assert not access.valid(value, now=1000.0 + 31 * 24 * 3600)
    expires, _dot, signature = value.partition(".")
    assert not access.valid(f"{int(expires) + 1}.{signature}", now=1001.0)
    assert not access.valid("garbage") and not access.valid(None)
    assert not Access("other password", "secret").valid(value, now=1001.0)  # a new password signs everyone out
    assert Access(None).valid(None)  # no password: open, as on a local run


def test_everything_but_the_shell_needs_the_password(hosted):
    client, token = hosted["client"], hosted["token"]
    assert client.get("/").status_code == 200 and 'data-mode="private"' in client.get("/").text
    assert client.get("/studio/studio.js").status_code == 200
    session = client.get("/api/v1/session").json()
    assert session == {"mode": "private", "login_required": True, "signed_in": False}  # no key status before login
    for path in ("/api/v1/projects", "/api/v1/projects/x/setup"):
        refused = client.get(path)
        assert refused.status_code == 401 and refused.json()["login"] is True
    assert client.get("/projects", follow_redirects=False).headers["location"] == "/"
    assert _post(client, "/api/v1/projects", {"name": "X", "site": "x.com", "queries": ["a"]}, token).status_code == 401
    wrong = _post(client, "/api/v1/login", {"password": "nope"}, token)
    assert wrong.status_code == 401 and COOKIE not in wrong.cookies
    assert _post(client, "/api/v1/login", {"password": PASSWORD}, None).status_code == 403  # needs the header token
    _sign_in(hosted)
    signed = client.get("/api/v1/session").json()
    assert signed["signed_in"] and signed["google"]["ready"] and signed["google"]["provider"] == "SerpAPI"
    assert SECRET_KEY not in json.dumps(signed)
    assert client.get("/api/v1/projects").status_code == 200
    _post(client, "/api/v1/logout", {}, token)
    assert client.get("/api/v1/projects").status_code == 401


# -- the project flow --------------------------------------------------------------------------


def test_create_pull_compare_and_test_a_real_project(hosted):
    client, token = hosted["client"], hosted["token"]
    _sign_in(hosted)
    made = _post(client, "/api/v1/projects", {"name": "Dirt Crew", "site": SITE, "queries": SEARCHES, "competitors": "rival.example"}, token)
    assert made.status_code == 200
    slug = made.json()["slug"]
    setup = client.get(f"/api/v1/projects/{slug}/setup").json()
    assert [row["q"] for row in setup["queries"]] == SEARCHES and not setup["engine"]["ready"]
    assert setup["google"]["ready"] and setup["competitors"] == ["rival.example"]

    pulled = _post(client, f"/api/v1/projects/{slug}/pull", {"google": True}, token)
    assert pulled.status_code == 200 and pulled.headers["content-type"].startswith("application/x-ndjson")
    lines = _stream(pulled)
    assert lines[0]["type"] == "start" and lines[0]["google"] is True
    steps = {line["step"] for line in lines if line["type"] == "progress"}
    assert {"google", "crawl", "train"} <= steps
    done = lines[-1]
    assert done["type"] == "done", lines[-3:]
    assert done["report"]["google_kind"] == "api" and done["report"]["searches_with_results"] == len(SEARCHES)
    assert SECRET_KEY not in pulled.text

    setup = client.get(f"/api/v1/projects/{slug}/setup").json()
    assert setup["engine"]["ready"] and setup["pulls"] and setup["last_pull"]["pull_id"] == done["report"]["pull_id"]
    assert setup["data"]["kind"] == "observed" and "Google results via" in setup["data"]["long"]
    overview = client.get(f"/api/v1/projects/{slug}").json()
    assert overview["engine"]["ready"] and overview["model"]["queries"] >= 2
    query = SEARCHES[0]
    compare = client.get(f"/api/v1/projects/{slug}/compare", params={"q": query}).json()
    assert compare["scored"] >= 2

    fetched = _post(client, f"/api/v1/projects/{slug}/fetch", {"url": "staging.example/new"}, token)
    assert fetched.status_code == 200 and "Live staging page" in fetched.json()["html"]
    run = _post(client, f"/api/v1/projects/{slug}/test", {"q": query, "draft": fetched.json()["html"], "url": f"https://{SITE}/new/"}, token)
    assert run.status_code == 200 and run.json()["saved"] is True and run.json()["after"]["of"] >= 2
    history = client.get(f"/api/v1/projects/{slug}/tests").json()["tests"]
    assert history and history[0]["id"] == run.json()["id"] and history[0]["q"] == query
    again = client.get(f"/api/v1/projects/{slug}/tests/{run.json()['id']}").json()
    assert "Live staging page" in again["draft_html"]

    changed = _post(client, f"/api/v1/projects/{slug}/settings", {"queries": SEARCHES[:2], "competitors": ["a.example", "not a domain!"]}, token)
    assert changed.status_code == 200 and changed.json()["queries"] == SEARCHES[:2] and changed.json()["problems"]
    assert _post(client, f"/api/v1/projects/{slug}/delete", {"confirm": "wrong"}, token).status_code == 400
    assert _post(client, f"/api/v1/projects/{slug}/delete", {"confirm": slug}, token).status_code == 200
    assert client.get(f"/api/v1/projects/{slug}").status_code == 404


def test_a_pull_with_no_key_and_no_positions_says_what_is_needed(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=False, pull_options={"fetcher": FakeCrawler(), "today": TODAY})
    client = TestClient(app)
    token = _token(client)
    assert client.get("/api/v1/session").json()["google"]["ready"] is False
    slug = _post(client, "/api/v1/projects", {"name": "No Key", "site": SITE, "queries": SEARCHES}, token).json()["slug"]
    lines = _stream(_post(client, f"/api/v1/projects/{slug}/pull", {}, token))
    assert lines[0]["google"] is False
    assert lines[-1]["type"] == "error" and "SERPER_API_KEY" in lines[-1]["error"] and "CSV" in lines[-1]["error"]


def test_csv_import_then_pull_without_a_key(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, projects_root=tmp_path / "projects", public_demo=False, pull_options={"fetcher": FakeCrawler(), "today": TODAY, "workers": 4})
    client = TestClient(app)
    token = _token(client)
    slug = _post(client, "/api/v1/projects", {"name": "From CSV", "site": SITE, "queries": SEARCHES}, token).json()["slug"]
    rows = ["query,url,position"] + [f"{q},{url},{i}" for q, urls in _results().items() if q in SEARCHES for i, url in enumerate(urls, start=1)]
    bad = _post(client, f"/api/v1/projects/{slug}/import", {"csv": "nope\n1,2"}, token)
    assert bad.status_code == 400
    assert _post(client, f"/api/v1/projects/{slug}/import", {"csv": "\n".join(rows), "date": "11/09/2026"}, token).status_code == 400
    imported = _post(client, f"/api/v1/projects/{slug}/import", {"csv": "\n".join(rows), "date": "2026-11-09"}, token)
    assert imported.status_code == 200 and imported.json()["rows"] == len(rows) - 1
    lines = _stream(_post(client, f"/api/v1/projects/{slug}/pull", {}, token))
    assert lines[-1]["type"] == "done" and lines[-1]["report"]["google_kind"] != "api"
    assert client.get(f"/api/v1/projects/{slug}/setup").json()["engine"]["ready"]


def test_public_demo_refuses_every_write(tmp_path):
    client = TestClient(create_app(settings=make_settings(tmp_path), projects_root=tmp_path / "projects", public_demo=True))
    token = _token(client)
    assert client.get("/api/v1/session").json()["mode"] == "public"
    for path, body in (
        ("/api/v1/projects", {"name": "X", "site": "x.example", "queries": ["a"]}),
        ("/api/v1/projects/synthetic-demo/pull", {}),
        ("/api/v1/projects/synthetic-demo/import", {"csv": "query,url,position\na,https://a.example/,1"}),
        ("/api/v1/projects/synthetic-demo/settings", {"queries": ["a"]}),
        ("/api/v1/projects/synthetic-demo/fetch", {"url": "https://a.example/"}),
        ("/api/v1/projects/synthetic-demo/delete", {"confirm": "synthetic-demo"}),
    ):
        assert _post(client, path, body, token).status_code == 403, path


# -- the Blob mirror -----------------------------------------------------------------------------


class FakeBlob:
    """In-memory stand-in for vercel.blob.sync: private blobs, etags, prefix listing."""

    class BlobError(Exception):
        pass

    class BlobNotFoundError(BlobError):
        pass

    def __init__(self) -> None:
        self.items: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        self.calls: list[str] = []
        self._n = 0

    def put_blob(self, name, body, *, access, content_type=None, allow_overwrite=False, **_):
        assert access == "private"
        if name in self.items and not allow_overwrite:
            raise self.BlobError("exists")
        self._n += 1
        self.items[name], self.etags[name] = bytes(body), f"etag-{self._n}"
        self.calls.append(f"put {name}")
        return SimpleNamespace(etag=self.etags[name], pathname=name)

    def head_blob(self, name):
        self.calls.append(f"head {name}")
        if name not in self.items:
            raise self.BlobNotFoundError(name)
        return SimpleNamespace(etag=self.etags[name])

    def query_blobs(self, *, prefix=None, **_):
        self.calls.append("list")
        return [SimpleNamespace(pathname=name) for name in self.items if name.startswith(prefix or "")]

    def get_blob(self, name, *, access):
        self.calls.append(f"get {name}")
        return SimpleNamespace(body=self.items[name])

    def delete_blob(self, names, **_):
        for name in [names] if isinstance(names, str) else names:
            self.items.pop(name, None)


@pytest.fixture()
def fake_blob(monkeypatch):
    pytest.importorskip("vercel.blob.sync")
    from vercel.blob import sync

    fake = FakeBlob()
    for name in ("put_blob", "head_blob", "query_blobs", "get_blob", "delete_blob", "BlobError", "BlobNotFoundError"):
        monkeypatch.setattr(sync, name, getattr(fake, name))
    return fake


def _make_state(folder):
    (folder / "projects" / "demo").mkdir(parents=True)
    (folder / "projects" / "demo" / "project.json").write_text('{"name": "Demo"}')
    db = folder / "rank.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('first')")
    return db


def test_state_saves_and_a_second_instance_picks_it_up(tmp_path, fake_blob):
    one_dir, two_dir = tmp_path / "one", tmp_path / "two"
    db_one = _make_state(one_dir)
    one = CloudState(data_dir=one_dir, db_file=db_one, enabled=True)
    two = CloudState(data_dir=two_dir, db_file=two_dir / "rank.db", enabled=True)
    assert two.refresh(force=True) is False  # nothing saved yet
    first = one.save()
    assert first.startswith(PREFIX) and fake_blob.items[POINTER] == first.encode()
    changed = []
    two.on_change.append(lambda: changed.append(True))
    assert two.refresh(force=True) is True and changed == [True]
    assert (two_dir / "projects" / "demo" / "project.json").read_text() == '{"name": "Demo"}'
    with sqlite3.connect(two_dir / "rank.db") as conn:
        assert conn.execute("select v from t").fetchall() == [("first",)]
    fake_blob.calls.clear()
    assert two.refresh(force=True) is False and fake_blob.calls == [f"head {POINTER}"]  # unchanged: one cheap call
    assert two.refresh() is False and fake_blob.calls == [f"head {POINTER}"]  # throttled: no call at all

    with sqlite3.connect(two_dir / "rank.db") as conn:
        conn.execute("insert into t values ('second')")
    (two_dir / "projects" / "demo" / "new.json").write_text("{}")
    for _ in range(4):
        two.save()
    versions = [name for name in fake_blob.items if name.startswith(PREFIX)]
    assert len(versions) <= 3  # old versions are pruned
    assert one.refresh(force=True) is True
    assert (one_dir / "projects" / "demo" / "new.json").is_file()
    with sqlite3.connect(db_one) as conn:
        assert [row[0] for row in conn.execute("select v from t")] == ["first", "second"]


def test_pack_skips_caches_and_never_writes_outside_the_folder(tmp_path):
    db = _make_state(tmp_path / "data")
    (tmp_path / "data" / "projects" / "demo" / "llm-cache").mkdir()
    (tmp_path / "data" / "projects" / "demo" / "llm-cache" / "x.json").write_text("{}")
    state = CloudState(data_dir=tmp_path / "data", db_file=db, enabled=True)
    import io
    import tarfile

    names = tarfile.open(fileobj=io.BytesIO(state.pack()), mode="r:gz").getnames()
    assert "rank.db" in names and "projects/demo/project.json" in names
    assert not any("llm-cache" in name for name in names)
    assert not any(name.startswith(("/", "..")) for name in names)
