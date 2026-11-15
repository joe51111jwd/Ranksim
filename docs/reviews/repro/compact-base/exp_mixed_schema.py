"""Two deployments share the store (production on schema S, a preview or the next deploy on S + one column).
N has the column (its engine ran ALTER TABLE ... ADD COLUMN), O does not. O edits a row and saves a delta (without the
column); N applies it to its compact base, which hashes the delta rows over fewer columns than its stored hashes."""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim import cloud  # noqa: E402
from aeo.ranksim.cloud import CloudState  # noqa: E402
from aeo.ranksim.objects import MemoryStore  # noqa: E402

root = Path(__file__).with_name("work_mixed")
shutil.rmtree(root, ignore_errors=True)
store = MemoryStore()


def inst(name):
    return CloudState(data_dir=root / name, db_file=root / name / "rank.db", enabled=True, store=store)


def sql(state, statement, args=()):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def rows(state):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return dict(conn.execute("SELECT query, project FROM rs_keywords WHERE query IN ('query 1','query 2','query 3')").fetchall()), conn.execute("SELECT count(*) FROM rs_keywords").fetchone()[0]
    finally:
        conn.close()


def base_entries(state):
    conn = sqlite3.connect(str(state._base_db))
    try:
        return conn.execute("SELECT count(*) FROM rows WHERE tbl='rs_keywords'").fetchone()[0]
    finally:
        conn.close()


n, o = inst("n"), inst("o")
n.db_file.parent.mkdir(parents=True)
conn = sqlite3.connect(str(n.db_file))
conn.execute("CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, project TEXT, created_at TEXT)")
conn.execute("CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT)")
conn.executemany("INSERT INTO rs_keywords VALUES (?, 'demo', '2026-11-01')", ((f"query {k}",) for k in range(10)))
conn.commit()
conn.close()
print("n save:", n.save())
o.refresh(force=True)

# the new deployment's engine migrates N's working db
sql(n, "ALTER TABLE rs_keywords ADD COLUMN note VARCHAR(200) DEFAULT ''")

# O (old schema) edits query 1 and saves
sql(o, "UPDATE rs_keywords SET project = 'edited by o' WHERE query = 'query 1'")
print("o save:", o.save())
n.refresh(force=True)
print("n after o's save:", rows(n), "| n base entries:", base_entries(n), "(10 rows)")

# N makes two ordinary saves of unrelated rows
for k in range(2):
    sql(n, "INSERT OR REPLACE INTO rs_pages VALUES (?, 'x')", (f"u{k}",))
    print("n save:", n.save())
print("n base entries:", base_entries(n))
o.refresh(force=True)
print("o after n's saves:", rows(o))
sql(o, "INSERT OR REPLACE INTO rs_pages VALUES ('o-page', 'y')")
print("o save:", o.save())
n.refresh(force=True)
print("n after o's next save:", rows(n))
print("o:", rows(o))
