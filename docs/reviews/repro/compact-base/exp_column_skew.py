"""Two deployments share the Blob store: X (old schema) and Y (its migration added rs_pages.note). X saves a delta
that changes a page row. Y applies it: _base_apply hashes the delta's rows over the delta's columns (no note) while
Y's base rows are hashed over all its columns (with note). What happens to Y's next saves?"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState, CloudError
from aeo.ranksim.objects import MemoryStore

table = sys.argv[1] if len(sys.argv) > 1 else "rs_pages"
root = Path(tempfile.mkdtemp(prefix="skew-"))


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


def base_entries(state, tbl):
    conn = sqlite3.connect(str(state._base_db))
    try:
        out = []
        for (body,) in conn.execute("SELECT body FROM rows WHERE tbl = ?", (tbl,)):
            out.append(cloud._row_unpack(body))
        return out
    finally:
        conn.close()


store = MemoryStore()
x, y = inst("x", store), inst("y", store)
x.db_file.parent.mkdir(parents=True)
conn = sqlite3.connect(str(x.db_file))
conn.execute("CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT, fetched_at TEXT)")
conn.execute("CREATE TABLE rs_outcomes (id INTEGER PRIMARY KEY, query TEXT, recorded_at TEXT)")
conn.executemany("INSERT INTO rs_pages VALUES (?, ?, '2026-11-01')", [("https://a/1", "v1"), ("https://a/2", "v1")])
conn.executemany("INSERT INTO rs_outcomes (query, recorded_at) VALUES (?, '2026-11-01')", [("q1",), ("q2",)])
conn.commit()
conn.close()
print("X save 1:", x.save())
y.refresh(force=True)
# Y's deploy: its engine runs the migration
sql(y, "ALTER TABLE rs_pages ADD COLUMN note TEXT DEFAULT ''")
sql(y, "ALTER TABLE rs_outcomes ADD COLUMN note TEXT DEFAULT ''")
sql(y, "UPDATE rs_pages SET note = 'y note' WHERE url = 'https://a/2'")
print("Y save 2:", y.save())
x.refresh(force=True)

# X (old schema) refetches page 1 and records an outcome
sql(x, "UPDATE rs_pages SET html = 'v2', fetched_at = '2026-11-02' WHERE url = 'https://a/1'")
sql(x, "INSERT INTO rs_outcomes (query, recorded_at) VALUES ('q3', '2026-11-02')")
print("X save 3:", x.save())
y.refresh(force=True)
print("Y base entries for", table, ":")
for entry in base_entries(y, table):
    print("   ", entry)

for n in range(3):
    sql(y, "INSERT INTO rs_outcomes (query, recorded_at, note) VALUES (?, '2026-11-03', 'y')", f"y{n}")
    try:
        print(f"Y save attempt {n + 1}:", y.save())
    except CloudError as exc:
        print(f"Y save attempt {n + 1}: CloudError:", exc, "| last_error:", y.last_error)
x.refresh(force=True)
print("X sees Y's outcomes:", rows(x, "SELECT query FROM rs_outcomes ORDER BY id"))
y2 = inst("y2", store)
y2.refresh(force=True)
print("fresh instance sees outcomes:", rows(y2, "SELECT query FROM rs_outcomes ORDER BY id"))
