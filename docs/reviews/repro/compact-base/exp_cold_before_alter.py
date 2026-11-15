"""A deploy adds rs_outcomes.note / rs_pages.note. The newest snapshot predates it. Instance Y1 sets note values and
saves deltas. A new instance Y2 cold-syncs: the snapshot is renamed into place (no note column), the deltas after it
are merged while the db still lacks the column (the engine that runs the ALTER was disposed and is only re-created by
the next request). What does Y2 end up with, and what happens when Y2 later edits one of those rows?"""
import sqlite3
import sys
import tempfile
from pathlib import Path

src = sys.argv[1] if len(sys.argv) > 1 else "/home/user/Ranksim/src"
sys.path.insert(0, src)
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore

root = Path(tempfile.mkdtemp(prefix="alter-"))


def inst(name, store):
    return CloudState(data_dir=root / name / "ranksim", db_file=root / name / "ranksim" / "rank.db", enabled=True, store=store)


def sql(state, statement, *args):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def migrate(state):  # what the app's engine does when it is created (store._ensure_ledger_columns)
    conn = sqlite3.connect(str(state.db_file))
    have = {r[1] for r in conn.execute("PRAGMA table_info(rs_pages)")}
    if "note" not in have:
        conn.execute("ALTER TABLE rs_pages ADD COLUMN note TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def rows(state, statement):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return conn.execute(statement).fetchall()
    finally:
        conn.close()


store = MemoryStore()
old = inst("old", store)
old.db_file.parent.mkdir(parents=True)
conn = sqlite3.connect(str(old.db_file))
conn.execute("CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT, fetched_at TEXT)")
conn.executemany("INSERT INTO rs_pages VALUES (?, 'v1', '2026-11-01')", [("https://a/1",), ("https://a/2",)])
conn.commit()
conn.close()
print("old deploy, save 1 (snapshot without note):", old.save())

y1 = inst("y1", store)
y1.refresh(force=True)
migrate(y1)
sql(y1, "UPDATE rs_pages SET note = 'checked by hand' WHERE url = 'https://a/1'")
print("Y1 save 2 (delta with note):", y1.save())

y2 = inst("y2", store)
y2.refresh(force=True)  # cold sync: snapshot 1 + delta 2, before any engine re-runs the migration
migrate(y2)  # the next request's engine
print("Y2 note of page 1 after its cold sync:", rows(y2, "SELECT note FROM rs_pages WHERE url = 'https://a/1'"))
sql(y2, "UPDATE rs_pages SET html = 'v2', fetched_at = '2026-11-03' WHERE url = 'https://a/1'")  # Y2 refetches page 1
print("Y2 save 3:", y2.save())
y1.refresh(force=True)
print("Y1 note of page 1 after Y2's refetch:", rows(y1, "SELECT note, html FROM rs_pages WHERE url = 'https://a/1'"))
