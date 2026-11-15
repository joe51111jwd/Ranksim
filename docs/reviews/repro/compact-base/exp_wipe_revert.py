"""After the disk-full wipe, the cold sync merges storage into the working copy with no base (files: base {}, db:
merge_sqlite(db, None, theirs)). Does the instance's next save revert edits and resurrect deletions made elsewhere?"""
import errno
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore

compact = "--compact" in sys.argv
if compact:
    cloud.COMPACT_DELTAS = 0

root = Path(tempfile.mkdtemp(prefix="wipe-"))


def inst(name, store):
    return CloudState(data_dir=root / name / "ranksim", db_file=root / name / "ranksim" / "rank.db", enabled=True, store=store)


def sql(state, statement, *args):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def rows(state, statement):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


store = MemoryStore()
a, b = inst("a", store), inst("b", store)
a.db_file.parent.mkdir(parents=True)
conn = sqlite3.connect(str(a.db_file))
conn.execute("CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, intent TEXT, notes TEXT)")
conn.execute("CREATE TABLE rs_rank_checks (id INTEGER PRIMARY KEY, query TEXT, checked_on TEXT, our_position INTEGER)")
conn.executemany("INSERT INTO rs_keywords VALUES (?, 'info', ?)", [("k1", "orig"), ("k2", "orig")])
conn.executemany("INSERT INTO rs_rank_checks (query, checked_on, our_position) VALUES (?, ?, ?)", [("k1", "2026-11-01", 3), ("k2", "2026-11-01", 5)])
conn.commit()
conn.close()
(a.projects_dir / "foo").mkdir(parents=True)
(a.projects_dir / "foo" / "page.html").write_text("one")
(a.projects_dir / "foo" / "old.txt").write_text("old")
(a.projects_dir / "bar").mkdir(parents=True)
(a.projects_dir / "bar" / "x.txt").write_text("bar")
print("A save 1:", a.save())
b.refresh(force=True)
print("B after cold sync: stamp", b.stamp, "files", sorted(str(p.relative_to(b.projects_dir)) for p in b.projects_dir.rglob("*") if p.is_file()))

# A: edits and deletions that B has not seen yet
sql(a, "UPDATE rs_keywords SET notes = 'edited by A' WHERE query = 'k1'")
sql(a, "DELETE FROM rs_rank_checks WHERE query = 'k1'")
(a.projects_dir / "foo" / "page.html").write_text("two")
(a.projects_dir / "foo" / "old.txt").unlink()
shutil.rmtree(a.projects_dir / "bar")
print("A save 2:", a.save(), "manifest db:", a._manifest["db"]["snapshot"], a._manifest["db"]["deltas"])

# B: the disk is full while it catches up, and still full after the sweep -> _wipe -> cold sync
real_extract, calls = tarfile.TarFile.extract, []


def extract(self, *args, **kwargs):
    calls.append(1)
    if len(calls) <= 2:
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_extract(self, *args, **kwargs)


tarfile.TarFile.extract = extract
real_merge, mcalls = cloud.merge_sqlite, []
print("B refresh:", b.refresh(force=True), "stamp", b.stamp, "extract calls", len(calls))
tarfile.TarFile.extract = real_extract
print("B after wipe+cold sync:")
print("  foo/page.html:", (b.projects_dir / "foo" / "page.html").read_text())
print("  foo/old.txt exists:", (b.projects_dir / "foo" / "old.txt").exists())
print("  bar exists:", (b.projects_dir / "bar").exists())
print("  k1:", rows(b, "SELECT notes FROM rs_keywords WHERE query='k1'"))
print("  rank checks:", rows(b, "SELECT query FROM rs_rank_checks ORDER BY id"))

# B: an unrelated write and its save
sql(b, "INSERT INTO rs_keywords VALUES ('k3', 'info', 'new on B')")
print("B save:", b.save())
a.refresh(force=True)
print("A after B's save:")
print("  foo/page.html:", (a.projects_dir / "foo" / "page.html").read_text())
print("  foo/old.txt exists:", (a.projects_dir / "foo" / "old.txt").exists())
print("  bar exists:", (a.projects_dir / "bar").exists(), sorted(p.name for p in (a.projects_dir / "bar").glob("*")) if (a.projects_dir / "bar").exists() else None)
print("  k1:", rows(a, "SELECT notes FROM rs_keywords WHERE query='k1'"))
print("  rank checks:", rows(a, "SELECT query FROM rs_rank_checks ORDER BY id"))
c = inst("c", store)
c.refresh(force=True)
print("C (fresh instance):")
print("  foo/page.html:", (c.projects_dir / "foo" / "page.html").read_text())
print("  bar exists:", (c.projects_dir / "bar").exists())
print("  k1:", rows(c, "SELECT notes FROM rs_keywords WHERE query='k1'"))
print("  rank checks:", rows(c, "SELECT query FROM rs_rank_checks ORDER BY id"))
