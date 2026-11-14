"""A hosted, private Ranksim: password login, the project API end to end, and the Blob state mirror. No network."""

from __future__ import annotations

import json
import re
import time
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_project import SEARCHES, SITE, TODAY, FakeCrawler, _results, page_html

from aeo.ranksim.access import COOKIE, Access
from aeo.ranksim.cloud import LEGACY_PREFIX, POINTER, SAVES_PREFIX, CloudState, merge_sqlite
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
    assert client.get("/studio/js/app.js").status_code == 200 and client.get("/studio/css/base.css").status_code == 200
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
            raise self.BlobError("Vercel Blob: This blob already exists, use `allowOverwrite: true` if you want to overwrite it.")
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


def _instance(folder):
    return CloudState(data_dir=folder, db_file=folder / "rank.db", enabled=True)


def _manifest_of(fake):
    """The manifest inside the newest save of the chain."""
    import io
    import tarfile

    saves = sorted(name for name in fake.items if name.startswith(SAVES_PREFIX))
    if not saves:
        return {}
    with tarfile.open(fileobj=io.BytesIO(fake.items[saves[-1]]), mode="r:gz") as archive:
        return json.loads(archive.extractfile("manifest.json").read())


def _versions(fake, slug=None):
    """The save holding slug's newest version (as a one-item list), or every save in the chain."""
    from aeo.ranksim.cloud import chain_name

    if slug is None:
        return sorted(name for name in fake.items if name.startswith(SAVES_PREFIX))
    seq = (_manifest_of(fake).get("projects") or {}).get(slug)
    return [chain_name(seq)] if seq and chain_name(seq) in fake.items else []


def _members(fake, name):
    import io
    import tarfile

    return tarfile.open(fileobj=io.BytesIO(fake.items[name]), mode="r:gz").getnames()


def _uploads(fake):
    return [call.split(" ", 1)[1] for call in fake.calls if call.startswith(f"put {SAVES_PREFIX}")]


def test_state_saves_and_a_second_instance_picks_it_up(tmp_path, fake_blob):
    one_dir, two_dir = tmp_path / "one", tmp_path / "two"
    db_one = _make_state(one_dir)
    one = _instance(one_dir)
    two = _instance(two_dir)
    assert two.refresh(force=True) is False  # nothing saved yet
    first = one.save()
    assert first and POINTER in fake_blob.items
    assert _versions(fake_blob, "demo") and "db.full" in _members(fake_blob, first)  # one object: projects and database
    changed = []
    two.on_change.append(lambda: changed.append(True))
    assert two.refresh(force=True) is True and changed == [True]
    assert (two_dir / "projects" / "demo" / "project.json").read_text() == '{"name": "Demo"}'
    with sqlite3.connect(two_dir / "rank.db") as conn:
        assert conn.execute("select v from t").fetchall() == [("first",)]
    fake_blob.calls.clear()
    assert two.refresh(force=True) is False and len(fake_blob.calls) == 1  # unchanged: one look for the next save
    assert fake_blob.calls[0].startswith(f"get {SAVES_PREFIX}")
    assert two.refresh() is False and len(fake_blob.calls) == 1  # throttled: no call at all

    with sqlite3.connect(two_dir / "rank.db") as conn:
        conn.execute("insert into t values ('second')")
    (two_dir / "projects" / "demo" / "new.json").write_text("{}")
    fake_blob.calls.clear()
    for _ in range(4):
        two.save()
    assert len(_uploads(fake_blob)) == 1  # one save object; the next three saves had nothing to upload
    assert "db.plus" in _members(fake_blob, _uploads(fake_blob)[0])  # the database went up as a small delta
    assert one.refresh(force=True) is True
    assert (one_dir / "projects" / "demo" / "new.json").is_file()
    with sqlite3.connect(db_one) as conn:
        assert [row[0] for row in conn.execute("select v from t")] == ["first", "second"]


def test_a_save_with_nothing_changed_uploads_nothing(tmp_path, fake_blob):
    one = _instance(tmp_path / "one")
    _make_state(tmp_path / "one")
    one.save()
    fake_blob.calls.clear()
    assert one.save() is None
    assert not [call for call in fake_blob.calls if call.startswith("put")]
    (tmp_path / "one" / "projects" / "other").mkdir()
    (tmp_path / "one" / "projects" / "other" / "project.json").write_text("{}")
    fake_blob.calls.clear()
    one.save()
    puts = _uploads(fake_blob)
    members = _members(fake_blob, puts[0])
    assert len(puts) == 1 and all(name.startswith("p/other/") for name in members if name != "manifest.json")  # only the changed project


def test_concurrent_saves_on_two_projects_never_clobber(tmp_path, fake_blob):
    seed = _instance(tmp_path / "seed")
    _make_state(tmp_path / "seed")
    (tmp_path / "seed" / "projects" / "other").mkdir()
    (tmp_path / "seed" / "projects" / "other" / "project.json").write_text('{"name": "Other"}')
    seed.save()
    a, b = _instance(tmp_path / "a"), _instance(tmp_path / "b")
    a.refresh(force=True)
    b.refresh(force=True)
    # Both instances work at the same time, from the same starting state, on different projects.
    (tmp_path / "a" / "projects" / "demo" / "notes.json").write_text('{"by": "a"}')
    (tmp_path / "b" / "projects" / "other" / "notes.json").write_text('{"by": "b"}')
    a.save()
    b.save()  # the old design would replace a's work here
    reader = _instance(tmp_path / "reader")
    reader.refresh(force=True)
    assert (tmp_path / "reader" / "projects" / "demo" / "notes.json").read_text() == '{"by": "a"}'
    assert (tmp_path / "reader" / "projects" / "other" / "notes.json").read_text() == '{"by": "b"}'
    a.refresh(force=True)
    assert (tmp_path / "a" / "projects" / "other" / "notes.json").is_file()


def test_two_writers_on_one_project_keep_both_files_and_the_newest_edit(tmp_path, fake_blob):
    seed = _instance(tmp_path / "seed")
    _make_state(tmp_path / "seed")
    seed.save()
    a, b = _instance(tmp_path / "a"), _instance(tmp_path / "b")
    a.refresh(force=True)
    b.refresh(force=True)
    demo_a, demo_b = tmp_path / "a" / "projects" / "demo", tmp_path / "b" / "projects" / "demo"
    (demo_a / "draft-a.html").write_text("a")
    (demo_b / "draft-b.html").write_text("b")
    (demo_a / "project.json").write_text('{"name": "A"}')
    (demo_b / "project.json").write_text('{"name": "B"}')
    a.save()
    b.save()  # merges a's version first: a's new file survives, and b's edit of project.json is the newest
    reader = _instance(tmp_path / "reader")
    reader.refresh(force=True)
    folder = tmp_path / "reader" / "projects" / "demo"
    assert (folder / "draft-a.html").read_text() == "a" and (folder / "draft-b.html").read_text() == "b"
    assert (folder / "project.json").read_text() == '{"name": "B"}'


def test_a_deleted_project_is_deleted_everywhere(tmp_path, fake_blob):
    import shutil

    a, b = _instance(tmp_path / "a"), _instance(tmp_path / "b")
    _make_state(tmp_path / "a")
    a.save()
    b.refresh(force=True)
    assert (tmp_path / "b" / "projects" / "demo").is_dir()
    shutil.rmtree(tmp_path / "a" / "projects" / "demo")
    a.save()
    assert b.refresh(force=True) is True
    assert not (tmp_path / "b" / "projects" / "demo").exists()
    c = _instance(tmp_path / "c")
    c.refresh(force=True)
    assert not (tmp_path / "c" / "projects" / "demo").exists()


def _rank_db(folder):
    from aeo import db
    from aeo.ranksim import store

    db._register_sqlite_pragmas()  # WAL mode, as in the app: writes land in rank.db-wal before the main file
    folder.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{folder / 'rank.db'}"
    store.session_factory(url)  # creates every table, including the ledger
    return url


def test_database_rows_from_two_instances_are_merged(tmp_path, fake_blob):
    from aeo.ranksim import store
    from aeo.ranksim.ledger import Call, record_prediction, verify_ledger
    from aeo.ranksim.store import RsPage, open_session, replace_serp

    def predict(session, query):
        record_prediction(
            session, kind="draft", query=query, subject_url="https://a.example/", subject_html="<p>x</p>", predicted_rank=1,
            predicted_of=2, candidates=[], calls=[Call("https://b.example/", "above", 0.1, "UNSURE")], confidence="UNSURE",
            calibration_note="", provenance={}, project_slug="demo",
        )

    hit = lambda url: SerpHit(position=1, url=url, title="t", snippet="", provider="fake", synthetic=False, is_google=True)
    seed_url = _rank_db(tmp_path / "seed")
    session = open_session(seed_url)
    session.add(RsPage(url="https://shared.example/", raw_html="<p>old</p>"))
    replace_serp(session, "dirt work software", [hit("https://old.example/")])
    predict(session, "seed query")
    session.commit()
    session.close()
    seed = _instance(tmp_path / "seed")
    seed.save()

    a, b = _instance(tmp_path / "a"), _instance(tmp_path / "b")
    a.refresh(force=True)
    b.refresh(force=True)
    for folder, page, query in ((tmp_path / "a", "https://from-a.example/", "a query"), (tmp_path / "b", "https://from-b.example/", "b query")):
        session = open_session(f"sqlite:///{folder / 'rank.db'}")
        session.add(RsPage(url=page, raw_html="<p>new</p>"))
        predict(session, query)
        session.commit()
        session.close()
    session = open_session(f"sqlite:///{tmp_path / 'b' / 'rank.db'}")
    replace_serp(session, "dirt work software", [hit("https://new-1.example/"), hit("https://new-2.example/")])
    session.commit()
    session.close()
    a.save()
    b.save()

    reader = _instance(tmp_path / "reader")
    reader.refresh(force=True)
    session = open_session(f"sqlite:///{tmp_path / 'reader' / 'rank.db'}")
    try:
        urls = {row.url for row in session.query(RsPage)}
        assert {"https://shared.example/", "https://from-a.example/", "https://from-b.example/"} <= urls
        serp = [row.url for row in session.query(store.RsSerp).filter(store.RsSerp.query == "dirt work software")]
        assert sorted(serp) == ["https://new-1.example/", "https://new-2.example/"]  # replaced as a group, no leftovers
        ok, message = verify_ledger(session)
        assert ok, message
        from aeo.ranksim.ledger import RsPrediction

        assert sorted(row.query for row in session.query(RsPrediction)) == ["a query", "b query", "seed query"]
    finally:
        session.close()


def test_merge_sqlite_keyed_rows_newest_wins(tmp_path):
    def make(path, rows):
        with sqlite3.connect(path) as conn:
            conn.execute("create table pages (url text primary key, html text, fetched_at text)")
            conn.executemany("insert into pages values (?, ?, ?)", rows)

    make(tmp_path / "base.db", [("u1", "base", "2026-01-01"), ("u2", "base", "2026-01-01")])
    make(tmp_path / "ours.db", [("u1", "ours", "2026-01-03"), ("u2", "base", "2026-01-01"), ("u3", "ours", "2026-01-03")])
    make(tmp_path / "theirs.db", [("u1", "theirs", "2026-01-02"), ("u4", "theirs", "2026-01-02")])  # theirs removed u2
    merge_sqlite(tmp_path / "ours.db", tmp_path / "base.db", tmp_path / "theirs.db")
    with sqlite3.connect(tmp_path / "ours.db") as conn:
        rows = dict(conn.execute("select url, html from pages"))
    assert rows == {"u1": "ours", "u3": "ours", "u4": "theirs"}


def _legacy_tarball(folder) -> bytes:
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted((folder / "projects").rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(folder)), recursive=False)
        archive.add(folder / "rank.db", arcname="rank.db")
    return buffer.getvalue()


def test_the_old_single_tarball_is_migrated_once_and_kept(tmp_path, fake_blob):
    old = tmp_path / "old"
    _make_state(old)
    (old / "projects" / "second").mkdir()
    (old / "projects" / "second" / "project.json").write_text('{"name": "Second"}')
    legacy_name = f"{LEGACY_PREFIX}00000000000000000001-abc.tgz"
    fake_blob.put_blob(legacy_name, _legacy_tarball(old), access="private")
    fake_blob.put_blob("ranksim/HEAD", legacy_name.encode(), access="private")

    fresh = _instance(tmp_path / "fresh")
    assert fresh.refresh(force=True) is True
    assert (tmp_path / "fresh" / "projects" / "second" / "project.json").read_text() == '{"name": "Second"}'
    with sqlite3.connect(tmp_path / "fresh" / "rank.db") as conn:
        assert conn.execute("select v from t").fetchall() == [("first",)]
    assert _manifest_of(fake_blob)["legacy"]["name"] == legacy_name
    assert _versions(fake_blob, "demo") and _versions(fake_blob, "second")
    assert legacy_name in fake_blob.items  # the old tarball is never deleted

    fake_blob.calls.clear()
    other = _instance(tmp_path / "other")
    assert other.refresh(force=True) is True
    assert (tmp_path / "other" / "projects" / "demo" / "project.json").is_file()
    assert not any(call.startswith(f"get {LEGACY_PREFIX}") for call in fake_blob.calls)  # migrated once only


def test_versions_skip_caches_and_never_write_outside_the_folder(tmp_path, fake_blob):
    import io
    import tarfile

    _make_state(tmp_path / "data")
    (tmp_path / "data" / "projects" / "demo" / "llm-cache").mkdir()
    (tmp_path / "data" / "projects" / "demo" / "llm-cache" / "x.json").write_text("{}")
    state = _instance(tmp_path / "data")
    state.save()
    names = tarfile.open(fileobj=io.BytesIO(fake_blob.items[_versions(fake_blob, "demo")[-1]]), mode="r:gz").getnames()
    assert "p/demo/project.json" in names and "p/demo/.ranksim-version.json" in names
    assert not any("llm-cache" in name for name in names)
    assert not any(name.startswith(("/", "..")) for name in names)


def test_storage_errors_surface_as_cloud_errors(tmp_path, fake_blob, monkeypatch):
    from aeo.ranksim.cloud import CloudError

    state = _instance(tmp_path / "data")
    _make_state(tmp_path / "data")

    def broken(*_args, **_kwargs):
        raise fake_blob.BlobError("down")

    from vercel.blob import sync

    monkeypatch.setattr(sync, "query_blobs", broken)
    monkeypatch.setattr("aeo.ranksim.objects.time.sleep", lambda _s: None)
    with pytest.raises(CloudError):
        state.refresh(force=True)
    with pytest.raises(CloudError):
        state.save()


def test_workspace_plugin_data_is_its_own_object(tmp_path, fake_blob):
    a, b = _instance(tmp_path / "a"), _instance(tmp_path / "b")
    _make_state(tmp_path / "a")
    shared = tmp_path / "a" / "projects" / "_plugins" / "gsc"
    shared.mkdir(parents=True)
    (shared / "token.json").write_text('{"connected": true}')
    (tmp_path / "a" / "projects" / ".hidden").mkdir()
    (tmp_path / "a" / "projects" / ".hidden" / "x").write_text("no")
    a.save()
    assert _versions(fake_blob, "_plugins") and not _versions(fake_blob, ".hidden")
    b.refresh(force=True)
    assert (tmp_path / "b" / "projects" / "_plugins" / "gsc" / "token.json").read_text() == '{"connected": true}'
    assert not (tmp_path / "b" / "projects" / ".hidden").exists()
    # plugins.save(ui) still calls cloud.save(): a change to workspace data alone is uploaded alone
    (shared / "usage.json").write_text("{}")
    fake_blob.calls.clear()
    a.save()
    puts = _uploads(fake_blob)
    assert len(puts) == 1 and all(name.startswith("p/_plugins/") for name in _members(fake_blob, puts[0]) if name != "manifest.json")


def test_login_limit_is_per_client_and_says_when_to_retry(hosted, monkeypatch):
    import aeo.ranksim.access as access_module

    monkeypatch.setattr(access_module, "FAILURE_DELAY_SECONDS", 0)
    monkeypatch.setattr("aeo.ranksim.access.time.sleep", lambda _s: None)
    client, token = hosted["client"], hosted["token"]
    for _ in range(access_module.MAX_CLIENT_FAILURES_PER_MINUTE):
        assert client.post("/api/v1/login", json={"password": "nope"}, headers={"x-ranksim-csrf": token, "x-real-ip": "203.0.113.9"}).status_code == 401
    refused = client.post("/api/v1/login", json={"password": PASSWORD}, headers={"x-ranksim-csrf": token, "x-real-ip": "203.0.113.9"})
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) >= 1
    other = client.post("/api/v1/login", json={"password": PASSWORD}, headers={"x-ranksim-csrf": token, "x-real-ip": "198.51.100.7"})
    assert other.status_code == 200  # another client is not locked out by the first one's failures


def test_sign_out_everywhere_revokes_every_session(hosted):
    client, token = hosted["client"], hosted["token"]
    _sign_in(hosted)
    stolen = client.cookies.get(COOKIE)
    assert client.get("/api/v1/projects").status_code == 200
    out = client.post("/api/v1/logout", json={"everywhere": True}, headers={"x-ranksim-csrf": token})
    assert out.status_code == 200 and out.json()["everywhere"] is True
    client.cookies.clear()
    client.cookies.set(COOKIE, stolen)  # a copy of the old cookie no longer works
    assert client.get("/api/v1/projects").status_code == 401
    client.cookies.clear()
    time.sleep(0.01)  # a cookie issued in the revocation's own millisecond is refused by design
    _sign_in(hosted)
    assert client.get("/api/v1/projects").status_code == 200  # signing in again works


def test_security_headers(hosted):
    client = hosted["client"]
    plain = client.get("/")
    assert "camera=()" in plain.headers["permissions-policy"] and plain.headers["cross-origin-opener-policy"] == "same-origin"
    assert "strict-transport-security" not in plain.headers  # only over https
    secure = client.get("/", headers={"x-forwarded-proto": "https"})
    assert secure.headers["strict-transport-security"].startswith("max-age=")


def test_an_old_instance_saving_during_the_rollout_is_merged_once(tmp_path, fake_blob):
    """First deploy: old and new instances overlap. A save in the old format after the migration is not lost, a newer
    edit made on the new side is not overwritten, and importing the same old save twice changes nothing."""
    from aeo.ranksim.cloud import LEGACY_POINTER, REFRESH_EVERY_SECONDS

    old = tmp_path / "old"
    _make_state(old)
    first_name = f"{LEGACY_PREFIX}00000000000000000001-aaa.tgz"
    fake_blob.put_blob(first_name, _legacy_tarball(old), access="private")
    fake_blob.put_blob(LEGACY_POINTER, first_name.encode(), access="private", allow_overwrite=True)

    clock = [1000.0]
    new = CloudState(data_dir=tmp_path / "new", db_file=tmp_path / "new" / "rank.db", enabled=True, clock=lambda: clock[0])
    assert new.refresh(force=True) is True
    demo_new = tmp_path / "new" / "projects" / "demo"
    (demo_new / "notes.json").write_text('{"by": "new"}')  # an edit on the new side after the migration
    new.save()

    # The old instance, unaware, saves again in the old format: it edits project.json and adds a project.
    (old / "projects" / "demo" / "project.json").write_text('{"name": "Demo, renamed on the old instance"}')
    (old / "projects" / "late").mkdir()
    (old / "projects" / "late" / "project.json").write_text('{"name": "Late"}')
    second_name = f"{LEGACY_PREFIX}00000000000000000002-bbb.tgz"
    fake_blob.put_blob(second_name, _legacy_tarball(old), access="private")
    fake_blob.put_blob(LEGACY_POINTER, second_name.encode(), access="private", allow_overwrite=True)

    clock[0] += REFRESH_EVERY_SECONDS + 1
    assert new.refresh() is True
    assert (demo_new / "project.json").read_text() == '{"name": "Demo, renamed on the old instance"}'
    assert (demo_new / "notes.json").read_text() == '{"by": "new"}'  # not removed: the old instance never had it
    assert (tmp_path / "new" / "projects" / "late" / "project.json").is_file()
    assert _manifest_of(fake_blob)["legacy"]["name"] == second_name

    reader = _instance(tmp_path / "reader")
    reader.refresh(force=True)
    assert (tmp_path / "reader" / "projects" / "late" / "project.json").is_file()
    assert (tmp_path / "reader" / "projects" / "demo" / "notes.json").is_file()

    fake_blob.calls.clear()
    clock[0] += REFRESH_EVERY_SECONDS + 1
    assert new.refresh() is False  # the same old save again: nothing to import, nothing uploaded
    assert not [call for call in fake_blob.calls if call.startswith("put")]
