"""Skeptic check: after the disk-full _wipe, does B re-upload its stale copy (reverting A's edits, resurrecting
A's deletions)? ENOSPC is injected deterministically into B's first two sync attempts.

usage: exp.py {old|new} {compact|delta} {early|late}
  early: ENOSPC while the save is read (before any project merge), via _apply_save
  late:  ENOSPC in the db step (after project merges), via _apply_delta/_merge_full
"""
import errno
import importlib.util
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
HERE = Path(__file__).resolve().parent

which, kind, point = sys.argv[1], sys.argv[2], sys.argv[3]
if which == "old":
    spec = importlib.util.spec_from_file_location("old_cloud", HERE / "old_cloud.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["old_cloud"] = mod
    spec.loader.exec_module(mod)
else:
    from aeo.ranksim import cloud as mod
from aeo.ranksim.objects import MemoryStore

root = Path(tempfile.mkdtemp(prefix="stale-", dir=HERE))
store = MemoryStore()
now = [1000.0]


def inst(name):
    return mod.CloudState(data_dir=root / name / "ranksim", db_file=root / name / "ranksim" / "rank.db", enabled=True,
                          store=store, clock=lambda: now[0])


def exec_(state, *stmts):
    conn = sqlite3.connect(str(state.db_file))
    for s in stmts:
        if isinstance(s, tuple):
            conn.execute(*s)
        else:
            conn.execute(s)
    conn.commit()
    conn.close()


def q(state, s):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return conn.execute(s).fetchall()
    finally:
        conn.close()


a, b = inst("a"), inst("b")
a.db_file.parent.mkdir(parents=True)
exec_(a,
      "CREATE TABLE rs_keywords (query VARCHAR(500) PRIMARY KEY, intent VARCHAR(32), cluster VARCHAR(32), suggested_target_page VARCHAR(2000), page_exists BOOLEAN, notes TEXT)",
      "CREATE TABLE rs_rank_checks (id INTEGER PRIMARY KEY, query VARCHAR(500), checked_on VARCHAR(10), our_position INTEGER, created_at DATETIME)",
      "CREATE TABLE rs_pages (url VARCHAR(2000) PRIMARY KEY, raw_html TEXT, fetched_at DATETIME)")
conn = sqlite3.connect(str(a.db_file))
conn.executemany("INSERT INTO rs_keywords VALUES (?, 'info', 'c', '/x', 1, 'orig')", [(f"k{n}",) for n in range(10)])
conn.executemany("INSERT INTO rs_rank_checks (query, checked_on, our_position, created_at) VALUES (?, '2026-11-01', ?, '2026-11-01')", [(f"k{n}", n) for n in range(10)])
conn.executemany("INSERT INTO rs_pages VALUES (?, ?, '2026-11-01')", [(f"https://s/{n}", "html" * 100) for n in range(20)])
conn.commit()
conn.close()
for site in ("alpha", "beta", "gamma"):
    d = a.projects_dir / site / "pages"
    d.mkdir(parents=True)
    (d / "p0.html").write_text(f"{site} p0")
    (d / "p1.html").write_text(f"{site} p1")
    (a.projects_dir / site / "project.json").write_text('{"v": 1}')
print("A save:", a.save())
b.refresh(force=True)
print("B synced to", b.stamp)

# A's changes, saved as N+1
exec_(a,
      "DELETE FROM rs_keywords WHERE query = 'k5'",
      "UPDATE rs_keywords SET notes = 'edited by A' WHERE query = 'k6'",
      "DELETE FROM rs_rank_checks WHERE query = 'k7'",
      "UPDATE rs_pages SET raw_html = 'refetched', fetched_at = '2026-11-15' WHERE url = 'https://s/1'")
(a.projects_dir / "alpha" / "project.json").write_text('{"v": 2}')
(a.projects_dir / "beta" / "pages" / "p1.html").unlink()
shutil.rmtree(a.projects_dir / "gamma")
if kind == "compact":
    saved = mod.COMPACT_DELTAS
    mod.COMPACT_DELTAS = 0
print("A save:", a.save(), "manifest db:", a._manifest.get("db"))
if kind == "compact":
    mod.COMPACT_DELTAS = saved

# B: two disk-full failures, then the last resort
fails = [2]


def arm(name):
    orig = getattr(mod.CloudState, name)

    def wrapper(self, *args, **kw):
        if fails[0] > 0:
            fails[0] -= 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return orig(self, *args, **kw)
    setattr(mod.CloudState, name, wrapper)


if point == "early":
    arm("_apply_save")
else:
    arm("_apply_delta")
    arm("_merge_full")
now[0] += 400
b.refresh()
print("B after refresh: stamp", b.stamp, "fails left", fails[0])
print("B k5:", q(b, "SELECT notes FROM rs_keywords WHERE query='k5'"), "k6:", q(b, "SELECT notes FROM rs_keywords WHERE query='k6'"),
      "rc k7:", q(b, "SELECT count(*) FROM rs_rank_checks WHERE query='k7'"))
print("B alpha/project.json:", (b.projects_dir / "alpha" / "project.json").read_text(),
      "beta p1:", (b.projects_dir / "beta" / "pages" / "p1.html").exists(), "gamma:", (b.projects_dir / "gamma").exists())

# B: one unrelated write, then its save
exec_(b, "INSERT INTO rs_keywords VALUES ('b-new', 'info', 'c', '/y', 0, 'from B')")
now[0] += 1
print("B save:", b.save())
now[0] += 1
a.refresh(force=True)
print(f"[{which}/{kind}/{point}] on A afterwards:",
      "k5 back=", bool(q(a, "SELECT 1 FROM rs_keywords WHERE query='k5'")),
      "k6=", q(a, "SELECT notes FROM rs_keywords WHERE query='k6'")[0][0],
      "rc k7 back=", q(a, "SELECT count(*) FROM rs_rank_checks WHERE query='k7'")[0][0],
      "page1=", q(a, "SELECT raw_html FROM rs_pages WHERE url='https://s/1'")[0][0][:12],
      "alpha/project.json=", (a.projects_dir / "alpha" / "project.json").read_text(),
      "beta p1 back=", (a.projects_dir / "beta" / "pages" / "p1.html").exists(),
      "gamma back=", (a.projects_dir / "gamma").exists(),
      "b-new=", bool(q(a, "SELECT 1 FROM rs_keywords WHERE query='b-new'")))
shutil.rmtree(root, ignore_errors=True)
