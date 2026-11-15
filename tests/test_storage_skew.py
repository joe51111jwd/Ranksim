"""Regressions from the second storage review (docs/reviews/storage-compact-base.md): schema skew between instances
and the compact base (findings 1, 2, 4, 5), and writes committed during a merge counted as saved (findings 6, 7).
The disk-full findings (3, 8, 9, 10) are covered in tests/test_disk.py. Each test ports one of the review's repro
scripts in docs/reviews/repro/compact-base/."""

from __future__ import annotations

import sqlite3

from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore


def _inst(tmp_path, name, store) -> CloudState:
    return CloudState(data_dir=tmp_path / name / "ranksim", db_file=tmp_path / name / "ranksim" / "rank.db", enabled=True, store=store)


def _sql(state, statement, args=()) -> None:
    state.db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def _rows(state, statement):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


def _base_entries(state, table) -> int:
    conn = sqlite3.connect(str(state._base_db))
    try:
        return conn.execute("SELECT count(*) FROM rows WHERE tbl = ?", (table,)).fetchone()[0]
    finally:
        conn.close()


KEYWORDS = "CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, intent TEXT, notes TEXT)"
ADD_SLUG = "ALTER TABLE rs_keywords ADD COLUMN project_slug VARCHAR(200) DEFAULT ''"


def _migrate(state) -> None:
    """What the engine's migration (store._ensure_ledger_columns) does when it is next created."""
    cols = [row[1] for row in _rows(state, "PRAGMA table_info(rs_keywords)")]
    if "project_slug" not in cols:
        _sql(state, ADD_SLUG)


def test_a_fresh_instance_keeps_a_new_column_it_receives_before_its_own_migration(tmp_path):
    """Findings 1 and 5 (exp_cold_column_delete.py, exp_cold_before_alter.py): a fresh instance cold-syncs an old
    snapshot plus a delta carrying a value in a column its engine has not added yet. The value must be kept, and the
    fresh instance's later saves must not delete or blank the row anywhere."""
    store = MemoryStore()
    a = _inst(tmp_path, "a", store)
    _sql(a, KEYWORDS)
    for key in ("k1", "k2", "k3"):
        _sql(a, "INSERT INTO rs_keywords VALUES (?, 'info', '')", (key,))
    assert a.save()  # the snapshot from before the deploy
    _migrate(a)
    _sql(a, "UPDATE rs_keywords SET project_slug = 'acme' WHERE query = 'k1'")
    assert a.save()  # a delta that carries the new column

    c = _inst(tmp_path, "c", store)
    c.refresh(force=True)  # fresh: old snapshot by rename, then the delta (before c's engine migrates)
    assert _rows(c, "SELECT project_slug FROM rs_keywords WHERE query = 'k1'") == [("acme",)]
    _migrate(c)  # finds the column there
    _sql(a, "UPDATE rs_keywords SET intent = 'commercial' WHERE query = 'k1'")
    assert a.save()
    c.refresh(force=True)
    _sql(c, "UPDATE rs_keywords SET notes = 'x' WHERE query = 'k3'")
    assert c.save()
    a.refresh(force=True)
    expected = [("k1", "commercial", "acme"), ("k2", "info", "")]
    show = "SELECT query, intent, project_slug FROM rs_keywords WHERE query IN ('k1', 'k2') ORDER BY query"
    assert _rows(a, show) == expected
    assert _rows(c, show) == expected
    b = _inst(tmp_path, "b", store)
    b.refresh(force=True)
    _migrate(b)
    assert _rows(b, show) == expected


def test_a_delta_from_an_older_schema_keeps_the_base_consistent(tmp_path):
    """Findings 2 and 4 (exp_mixed_schema.py, verify_real_schema.py): N runs a deploy that added a column, O (old
    schema, e.g. finishing a job after the deploy) edits a keyed row. N's base must keep one entry per row, and N's
    saves must keep working."""
    store = MemoryStore()
    n, o = _inst(tmp_path, "n", store), _inst(tmp_path, "o", store)
    _sql(n, "CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, project TEXT, created_at TEXT)")
    _sql(n, "CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT)")
    for k in range(10):
        _sql(n, "INSERT INTO rs_keywords VALUES (?, 'demo', '2026-11-01')", (f"query {k}",))
    assert n.save()
    o.refresh(force=True)
    _sql(n, "ALTER TABLE rs_keywords ADD COLUMN note VARCHAR(200) DEFAULT ''")
    _sql(n, "UPDATE rs_keywords SET note = 'kept' WHERE query = 'query 2'")
    _sql(o, "UPDATE rs_keywords SET project = 'edited by o' WHERE query = 'query 1'")
    assert o.save()
    n.refresh(force=True)
    assert _base_entries(n, "rs_keywords") == 10
    for k in range(2):
        _sql(n, "INSERT OR REPLACE INTO rs_pages VALUES (?, 'x')", (f"u{k}",))
        assert n.save()  # used to fail for good: UNIQUE constraint failed
    assert _base_entries(n, "rs_keywords") == 10
    o.refresh(force=True)
    _sql(o, "INSERT OR REPLACE INTO rs_pages VALUES ('o-page', 'y')")
    assert o.save()
    n.refresh(force=True)
    show = "SELECT query, project FROM rs_keywords WHERE query IN ('query 1', 'query 2') ORDER BY query"
    for state in (n, o):
        assert _rows(state, show) == [("query 1", "edited by o"), ("query 2", "demo")]
        assert _rows(state, "SELECT count(*) FROM rs_keywords") == [(10,)]
    assert _rows(n, "SELECT note FROM rs_keywords WHERE query = 'query 2'") == [("kept",)]
    assert n.save() is None  # settled


def test_a_row_appended_by_an_older_schema_is_not_deleted(tmp_path):
    """Finding 2, append-only tables (exp_mixed_log.py): O appends an outcome; N's later deltas must not carry it as
    a deletion that removes it from O."""
    store = MemoryStore()
    n, o = _inst(tmp_path, "n", store), _inst(tmp_path, "o", store)
    _sql(n, "CREATE TABLE rs_outcomes (id INTEGER NOT NULL PRIMARY KEY, observed_on VARCHAR(10), query VARCHAR(500))")
    _sql(n, "CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT)")
    _sql(n, "INSERT INTO rs_outcomes (observed_on, query) VALUES ('2026-11-01', 'seed')")
    assert n.save()
    o.refresh(force=True)
    _sql(n, "ALTER TABLE rs_outcomes ADD COLUMN source_file VARCHAR(500) DEFAULT ''")
    _sql(o, "INSERT INTO rs_outcomes (observed_on, query) VALUES ('2026-11-15', 'observed by o')")
    assert o.save()
    n.refresh(force=True)
    for k in range(2):
        _sql(n, "INSERT OR REPLACE INTO rs_pages VALUES (?, 'x')", (f"u{k}",))
        assert n.save()
    o.refresh(force=True)
    expected = [(1, "seed"), (2, "observed by o")]
    assert _rows(o, "SELECT id, query FROM rs_outcomes ORDER BY id") == expected
    _sql(o, "INSERT OR REPLACE INTO rs_pages VALUES ('o', 'y')")
    assert o.save()
    n.refresh(force=True)
    assert _rows(n, "SELECT id, query FROM rs_outcomes ORDER BY id") == expected


def test_skew_survives_a_compacting_save(tmp_path, monkeypatch):
    """Finding 4 (exp_skew_persist.py, exp_column_skew.py): an old-schema compacting save reaching a new-schema
    instance must not leave its base broken; its later saves and edits of the new column still travel."""
    monkeypatch.setattr(cloud, "COMPACT_DELTAS", 1)
    store = MemoryStore()
    x, y = _inst(tmp_path, "x", store), _inst(tmp_path, "y", store)
    _sql(x, "CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT)")
    for k in range(3):
        _sql(x, "INSERT INTO rs_pages VALUES (?, ?)", (f"https://ex.com/{k}", f"page {k}"))
    assert x.save()
    y.refresh(force=True)
    _sql(y, "ALTER TABLE rs_pages ADD COLUMN note TEXT DEFAULT ''")  # y runs the new deploy, x does not
    for k in range(3):
        _sql(x, "UPDATE rs_pages SET html = ? WHERE url = 'https://ex.com/1'", (f"edit {k}",))
        assert x.save()  # deltas, then compacting saves (old schema)
        y.refresh(force=True)
    _sql(y, "UPDATE rs_pages SET note = 'y note' WHERE url = 'https://ex.com/2'")
    assert y.save()
    assert _base_entries(y, "rs_pages") == 3
    x.refresh(force=True)
    assert _rows(x, "SELECT html FROM rs_pages WHERE url = 'https://ex.com/1'") == [("edit 2",)]
    z = _inst(tmp_path, "z", store)
    z.refresh(force=True)
    assert _rows(z, "SELECT note FROM rs_pages WHERE url = 'https://ex.com/2'") == [("y note",)]


def test_a_write_committed_during_a_catch_up_is_uploaded(tmp_path, monkeypatch):
    """Findings 6 and 7 (exp_clean_absorb.py, clean_race.py): a request thread commits a row (without the cloud lock)
    while another request's refresh merges a delta. That row must not count as saved."""
    store = MemoryStore()
    a, b = _inst(tmp_path, "a", store), _inst(tmp_path, "b", store)
    _sql(a, KEYWORDS)
    _sql(a, "INSERT INTO rs_keywords VALUES ('k1', 'info', '')")
    assert a.save()
    b.refresh(force=True)
    _sql(a, "INSERT INTO rs_keywords VALUES ('from a', 'info', '')")
    assert a.save()
    real = cloud._base_apply

    def during(*args, **kwargs):
        _sql(b, "INSERT INTO rs_keywords VALUES ('written on b', 'info', '')")  # another thread, no lock
        return real(*args, **kwargs)

    monkeypatch.setattr(cloud, "_base_apply", during)
    b.refresh(force=True)
    monkeypatch.setattr(cloud, "_base_apply", real)
    assert b.save(), "the row committed during the merge was marked saved"
    a.refresh(force=True)
    assert ("written on b",) in _rows(a, "SELECT query FROM rs_keywords")


def test_a_write_committed_during_a_snapshot_merge_is_uploaded(tmp_path, monkeypatch):
    """Finding 6/7 in _merge_full's merge branch: a full snapshot merged into a db that has rows."""
    store = MemoryStore()
    a, b = _inst(tmp_path, "a", store), _inst(tmp_path, "b", store)
    _sql(a, KEYWORDS)
    _sql(a, "INSERT INTO rs_keywords VALUES ('k1', 'info', '')")
    assert a.save()
    b.refresh(force=True)
    _sql(a, "INSERT INTO rs_keywords VALUES ('from a', 'info', '')")
    cloud._unlink_db(a._base_db)  # no base on the writer: its next save is a plain full snapshot
    assert a.save()
    real = cloud.make_base_delta

    def during(*args, **kwargs):
        _sql(b, "INSERT INTO rs_keywords VALUES ('written on b', 'info', '')")
        return real(*args, **kwargs)

    monkeypatch.setattr(cloud, "make_base_delta", during)
    b.refresh(force=True)
    monkeypatch.setattr(cloud, "make_base_delta", real)
    assert ("from a",) in _rows(b, "SELECT query FROM rs_keywords")
    assert b.save(), "the row committed during the snapshot merge was marked saved"
    a.refresh(force=True)
    assert ("written on b",) in _rows(a, "SELECT query FROM rs_keywords")
