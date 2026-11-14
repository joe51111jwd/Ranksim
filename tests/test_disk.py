"""Disk use of cloud storage on a serverless /tmp (Vercel: 512 MB, shared by everything the instance writes).

Measured with a 70 MB rank database (RANKSIM_DISK_TEST_DB_MB=120 for a bigger run) and 30 MB of project files: a cold sync, a catch-up of one delta, a save, and a
full snapshot merged into an instance with unsaved rows. Peak use is the sum of file sizes under the instance's
working folder and the system temp folder, sampled continuously and right before every rename and delete (the
moments a temp file is at its largest).
"""

from __future__ import annotations

import errno
import os
import random
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore, StoreError

MB = 1024 * 1024
DB_MB = int(os.environ.get("RANKSIM_DISK_TEST_DB_MB") or 70)  # 120 for a manual run at production-plus size
PROJECT_MB = 30
# At rest: the working db, the compact base (about half the db's size) and the projects. Every sync and save stays
# within that plus a little: no step holds a second full copy of the database on disk. With a 120 MB db the budget is
# 247 MB (measured 208 MB); Vercel's /tmp is 512 MB.
BUDGET = int(1.6 * DB_MB * MB) + PROJECT_MB * MB + 25 * MB


def _sizes(roots) -> int:
    total = 0
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.lstat(os.path.join(folder, name)).st_size
                except FileNotFoundError:
                    pass
    return total


class Peak:
    """Largest total size of the files under roots while the block runs."""

    def __init__(self, monkeypatch, *roots) -> None:
        self.roots, self.peak, self._stop = roots, 0, threading.Event()
        self.monkeypatch = monkeypatch

    def sample(self) -> None:
        self.peak = max(self.peak, _sizes(self.roots))

    def __enter__(self) -> "Peak":
        sample, real_replace, real_unlink, real_rmtree = self.sample, os.replace, os.unlink, shutil.rmtree

        def replace(src, dst, *args, **kwargs):
            sample()
            return real_replace(src, dst, *args, **kwargs)

        def unlink(path, *args, **kwargs):
            sample()
            return real_unlink(path, *args, **kwargs)

        def rmtree(path, *args, **kwargs):
            sample()
            return real_rmtree(path, *args, **kwargs)

        self.monkeypatch.setattr(os, "replace", replace)
        self.monkeypatch.setattr(os, "unlink", unlink)
        self.monkeypatch.setattr(shutil, "rmtree", rmtree)

        def loop():
            while not self._stop.is_set():
                sample()
                time.sleep(0.002)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join()
        self.sample()
        self.monkeypatch.undo()


WORDS = [os.urandom(4).hex()[: 3 + n % 6] for n in range(2000)]
WEIGHTS = [1 / (rank + 1) for rank in range(len(WORDS))]  # word frequencies follow Zipf's law, as in real text
TAGS = ["<p>", "</p>", "<li>", "</li>", '<a href="/x">', "</a>", "<h2>", "</h2>", "<div class=\"c\">", "</div>"]


def _text(size: int) -> str:
    """Page-like text: words from a large vocabulary inside markup. Compresses about 3.5:1, like the live database
    (66 MB packs to 19 MB)."""
    rng, parts, total = random.Random(size ^ len(WORDS) ^ int.from_bytes(os.urandom(4), "big")), [], 0
    while total < size:
        words = rng.choices(WORDS, WEIGHTS, k=64)
        words[rng.randrange(64)] = rng.choice(TAGS)
        parts.extend(words)
        total += sum(len(word) + 1 for word in words)
    return " ".join(parts)[:size]


def _make_db(path: Path, megabytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT, fetched_at TEXT)")
    conn.execute("CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, project TEXT, created_at TEXT)")
    row = 10_000
    conn.executemany(
        "INSERT INTO rs_pages VALUES (?, ?, ?)",
        ((f"https://site.example/{n}", _text(row), "2026-11-01") for n in range(megabytes * MB // row)),
    )
    conn.executemany("INSERT INTO rs_keywords VALUES (?, 'demo', '2026-11-01')", ((f"query {n}",) for n in range(500)))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _make_projects(root: Path, megabytes: int) -> None:
    for n in range(megabytes):
        folder = root / f"site-{n % 3}" / "pages"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"page-{n}.html").write_text(_text(MB))


def _rows(path: Path, table: str = "rs_pages") -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def systmp(tmp_path, monkeypatch):
    folder = tmp_path / "systmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setenv("TMPDIR", str(folder))
    monkeypatch.setenv("SQLITE_TMPDIR", str(folder))
    return folder


def _instance(root: Path, store) -> CloudState:
    return CloudState(data_dir=root / "ranksim", db_file=root / "ranksim" / "rank.db", enabled=True, store=store)


def test_peak_disk_use_stays_bounded_with_a_70_mb_database(tmp_path, systmp, monkeypatch):
    store = MemoryStore()
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    a, b = _instance(a_root, store), _instance(b_root, store)
    _make_db(a.db_file, DB_MB)
    _make_projects(a.projects_dir, PROJECT_MB)
    peaks = {}

    with Peak(pytest.MonkeyPatch(), a_root, systmp) as meter:
        assert a.save()  # first save: a full snapshot
    peaks["first save"] = meter.peak

    with Peak(pytest.MonkeyPatch(), b_root, systmp) as meter:
        assert b.refresh(force=True)  # cold sync of a fresh instance
    peaks["cold sync"] = meter.peak
    assert _rows(b.db_file) == _rows(a.db_file)
    assert sorted(p.name for p in b.projects_dir.iterdir()) == ["site-0", "site-1", "site-2"]

    conn = sqlite3.connect(str(a.db_file))
    conn.executemany("INSERT INTO rs_keywords VALUES (?, 'demo', '2026-11-14')", ((f"new {n}",) for n in range(50)))
    conn.execute("UPDATE rs_pages SET fetched_at = '2026-11-14' WHERE rowid <= 20")
    conn.commit()
    conn.close()
    (a.projects_dir / "site-0" / "notes.json").write_text("{}")
    with Peak(pytest.MonkeyPatch(), a_root, systmp) as meter:
        assert a.save()  # a delta
    peaks["delta save"] = meter.peak

    with Peak(pytest.MonkeyPatch(), b_root, systmp) as meter:
        assert b.refresh(force=True)  # catch-up of one delta
    peaks["delta catch-up"] = meter.peak
    assert _rows(b.db_file, "rs_keywords") == 550
    assert (b.projects_dir / "site-0" / "notes.json").read_text() == "{}"

    # B has unsaved rows when A uploads a full snapshot: the three-way merge path.
    conn = sqlite3.connect(str(b.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('only on b', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(a.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('only on a', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cloud, "COMPACT_DELTAS", 0)
    with Peak(pytest.MonkeyPatch(), a_root, systmp) as meter:
        assert a.save()
    peaks["snapshot save"] = meter.peak
    with Peak(pytest.MonkeyPatch(), b_root, systmp) as meter:
        assert b.refresh(force=True)
    peaks["snapshot merge"] = meter.peak
    assert _rows(b.db_file, "rs_keywords") == 552

    peaks["at rest"] = _sizes([b_root])
    print(f"base copy {(b_root / 'ranksim' / '.cloud' / 'base.rows').stat().st_size / MB:.0f} MB")
    report = ", ".join(f"{name} {size / MB:.0f} MB" for name, size in peaks.items())
    print(f"peak disk use: {report}")
    for name, size in peaks.items():
        assert size <= BUDGET, f"{name} peaked at {size / MB:.0f} MB (budget {BUDGET / MB:.0f} MB): {report}"
    for root in (a_root, b_root):
        assert not list((root / "ranksim" / ".cloud" / "tmp").glob("*")), "temp files left after a successful sync"
    assert not list(systmp.iterdir())


# -- failures leave nothing behind, crashes are cleaned up, a full disk recovers -----------------


def _leftovers(root: Path) -> list[str]:
    """Files under a working folder that are neither data nor the bookkeeping that is meant to stay."""
    keep = {"rank.db", "rank.db-wal", "rank.db-shm", ".cloud/state.json", ".cloud/base.rows"}
    found = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in keep:
            continue
        if rel.startswith("projects/") and not any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        found.append(rel)
    for path in root.parent.glob(f".{root.name}-*"):
        found.append(path.name)
    return sorted(found)


def _small_pair(tmp_path, store=None):
    store = store or MemoryStore()
    a, b = _instance(tmp_path / "a", store), _instance(tmp_path / "b", store)
    _make_db(a.db_file, 1)
    (a.projects_dir / "demo").mkdir(parents=True)
    (a.projects_dir / "demo" / "page.html").write_text("<p>one</p>")
    a.save()
    return store, a, b


@pytest.mark.parametrize("where", ["fetch", "project", "database", "delta"])
def test_a_failed_sync_leaves_no_temp_files(tmp_path, systmp, monkeypatch, where):
    store, a, b = _small_pair(tmp_path)
    if where == "delta":
        b.refresh(force=True)
        conn = sqlite3.connect(str(a.db_file))
        conn.execute("INSERT INTO rs_keywords VALUES ('later', 'demo', '2026-11-14')")
        conn.commit()
        conn.close()
        (a.projects_dir / "demo" / "page.html").write_text("<p>two</p>")
        a.save()
    boom = sqlite3.DatabaseError("broken")
    if where == "fetch":
        real = store.fetch

        def fetch(name, path):
            Path(path).write_bytes(b"partial")  # half a download is on disk when the connection drops
            raise StoreError("connection reset")

        monkeypatch.setattr(store, "fetch", fetch)
        assert real
    elif where == "project":
        monkeypatch.setattr(cloud, "_merge_files", lambda *a, **k: (_ for _ in ()).throw(KeyError("gone")))
    else:
        monkeypatch.setattr(cloud, "merge_sqlite", lambda *a, **k: (_ for _ in ()).throw(boom))
        monkeypatch.setattr(cloud, "_base_build", lambda *a, **k: (_ for _ in ()).throw(boom))
    with pytest.raises(cloud.CloudError):
        b.refresh(force=True)
    assert _leftovers(tmp_path / "b" / "ranksim") == []
    assert not list(systmp.iterdir())


def test_a_failed_save_leaves_no_temp_files(tmp_path, systmp, monkeypatch):
    store, a, _b = _small_pair(tmp_path)
    conn = sqlite3.connect(str(a.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('later', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(store, "put", lambda *a, **k: (_ for _ in ()).throw(StoreError("down")))
    with pytest.raises(cloud.CloudError):
        a.save()
    assert _leftovers(tmp_path / "a" / "ranksim") == []


def test_start_up_removes_what_a_killed_process_left(tmp_path, systmp):
    store, a, b = _small_pair(tmp_path)
    root = tmp_path / "b" / "ranksim"
    stale = [
        tmp_path / "b" / ".ranksim-incoming-83593792" / "db.full",
        root / ".cloud" / ".base.db.7844f763.tmp",
        root / ".cloud" / "tmp" / "save-12-abcd.tgz",
        root / ".cloud" / ".snap-1234.db",
        root / ".rank.db.99aa.tmp",
    ]
    for path in stale:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
    assert b.refresh(force=True)
    assert [path for path in stale if path.exists()] == []
    assert _leftovers(root) == []


def test_a_full_disk_is_recovered_by_removing_temp_files(tmp_path, systmp, monkeypatch):
    store, a, b = _small_pair(tmp_path)
    real, calls = cloud._unpack_files, []

    def unpack_files(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(cloud, "_unpack_files", unpack_files)
    assert b.refresh(force=True)
    assert _rows(b.db_file) == _rows(a.db_file)
    assert (b.projects_dir / "demo" / "page.html").read_text() == "<p>one</p>"
    assert _leftovers(tmp_path / "b" / "ranksim") == []


def test_a_disk_still_full_wipes_the_local_copy_and_loads_it_again_once(tmp_path, systmp, monkeypatch):
    store, a, b = _small_pair(tmp_path)
    b.refresh(force=True)
    junk = b.projects_dir / "demo" / "huge.bin"  # something filled the disk that is not a temp file
    junk.write_bytes(b"x" * 1000)
    conn = sqlite3.connect(str(a.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('later', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    a.save()
    real, calls = cloud.merge_sqlite, []

    def merge(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 2:
            raise sqlite3.OperationalError("database or disk is full")
        return real(*args, **kwargs)

    monkeypatch.setattr(cloud, "merge_sqlite", merge)
    assert b.refresh(force=True)
    assert not junk.exists()  # the local copy was dropped
    assert _rows(b.db_file, "rs_keywords") == _rows(a.db_file, "rs_keywords")
    assert (b.projects_dir / "demo" / "page.html").read_text() == "<p>one</p>"
    assert b.stamp == a.stamp

    conn = sqlite3.connect(str(a.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('again', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(cloud, "merge_sqlite", real)
    a.save()
    monkeypatch.setattr(cloud, "merge_sqlite", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database or disk is full")))
    monkeypatch.setattr(cloud, "_base_build", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    with pytest.raises(cloud.CloudError):  # a disk that stays full: one wipe and reload, then a plain error
        b.refresh(force=True)
    assert _leftovers(tmp_path / "b" / "ranksim") == []


def test_a_full_disk_during_a_save_is_retried_after_removing_temp_files(tmp_path, systmp, monkeypatch):
    store, a, _b = _small_pair(tmp_path)
    conn = sqlite3.connect(str(a.db_file))
    conn.execute("INSERT INTO rs_keywords VALUES ('later', 'demo', '2026-11-14')")
    conn.commit()
    conn.close()
    real, calls = cloud.make_base_delta, []

    def make_delta(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(cloud, "make_base_delta", make_delta)
    assert a.save()
    assert len(calls) == 2
    assert _leftovers(tmp_path / "a" / "ranksim") == []


def test_one_sync_at_a_time_per_instance(tmp_path, systmp):
    """Concurrent requests on a cold instance: one downloads, the others wait for it and download nothing."""
    gets: list[str] = []

    class Slow(MemoryStore):
        def get(self, name):
            gets.append(name)
            time.sleep(0.05)
            return super().get(name)

    store = Slow()
    a, _b = _instance(tmp_path / "a", store), None
    _make_db(a.db_file, 1)
    (a.projects_dir / "demo").mkdir(parents=True)
    (a.projects_dir / "demo" / "page.html").write_text("<p>one</p>")
    a.save()
    gets.clear()
    b = _instance(tmp_path / "b", store)
    twin = _instance(tmp_path / "b", store)  # a second object on the same folder shares the lock
    assert twin.lock is b.lock
    errors: list[BaseException] = []

    def hit(state):
        try:
            state.refresh()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hit, args=(b,)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert gets.count(cloud.chain_name(1)) == 1
    assert _leftovers(tmp_path / "b" / "ranksim") == []


def test_a_delta_against_the_compact_base_matches_one_against_a_full_copy(tmp_path):
    """make_base_delta (working db against the compact base) produces the same minus and plus rows as make_delta
    (against a full copy), for keyed rows, append-only rows, grouped Google results and a column the base lacks."""
    rng = random.Random(7)

    def build(path, pages, logs, serp):
        conn = sqlite3.connect(str(path))
        conn.execute("CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT, fetched_at TEXT)")
        conn.execute("CREATE TABLE rs_log (id INTEGER PRIMARY KEY, what TEXT, n INTEGER)")
        conn.execute("CREATE TABLE rs_serp (id INTEGER PRIMARY KEY, query TEXT, position INTEGER, url TEXT, fetched_at TEXT)")
        conn.executemany("INSERT INTO rs_pages VALUES (?, ?, ?)", pages)
        conn.executemany("INSERT INTO rs_log VALUES (?, ?, ?)", logs)
        conn.executemany("INSERT INTO rs_serp VALUES (?, ?, ?, ?, ?)", serp)
        conn.commit()
        conn.close()

    for trial in range(5):
        pages = [(f"u{n}", f"html {rng.random()}", "2026-11-01") for n in range(60)]
        logs = [(n, f"w{n}", rng.randrange(5)) for n in range(1, 40)]
        serp = [(n, f"q{n % 7}", n % 10, f"u{n}", "2026-11-01") for n in range(1, 50)]
        base, new = tmp_path / f"base{trial}.db", tmp_path / f"new{trial}.db"
        build(base, pages, logs, serp)
        pages2 = [p for p in pages if rng.random() > 0.1]
        pages2 = [(u, h if rng.random() > 0.2 else h + " changed", f) for u, h, f in pages2] + [(f"n{trial}{k}", "new", "2026-11-14") for k in range(5)]
        logs2 = [l for l in logs if rng.random() > 0.1] + [(100 + k, "added", k) for k in range(4)]
        serp2 = [s for s in serp if s[1] != "q3"] + [(200 + k, "q3", k, f"x{k}", "2026-11-14") for k in range(3)]
        build(new, pages2, logs2, serp2)
        if trial == 4:  # the working db gained a column the base does not have
            conn = sqlite3.connect(str(new))
            conn.execute("ALTER TABLE rs_log ADD COLUMN extra TEXT")
            conn.commit()
            conn.close()
        compact = tmp_path / f"base{trial}.rows"
        cloud._base_build(base, compact)
        results = []
        for maker, ancestor, tag in ((cloud.make_delta, base, "full"), (cloud.make_base_delta, compact, "compact")):
            minus, plus = tmp_path / f"m-{tag}-{trial}.db", tmp_path / f"p-{tag}-{trial}.db"
            count = maker(new, ancestor, minus, plus)
            rows = {}
            for label, path in (("minus", minus), ("plus", plus)):
                conn = sqlite3.connect(str(path))
                for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    rows[(label, table)] = sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                conn.close()
            results.append((count, rows))
        assert results[0] == results[1], f"trial {trial}"
        assert results[0][0] > 0

        # applying the delta to the compact base gives the compact base of the new database
        minus, plus = tmp_path / f"m-compact-{trial}.db", tmp_path / f"p-compact-{trial}.db"
        cloud._base_apply(compact, minus, plus)
        again = tmp_path / f"again-m{trial}.db", tmp_path / f"again-p{trial}.db"
        if trial < 4:
            assert cloud.make_base_delta(new, compact, *again) == 0
