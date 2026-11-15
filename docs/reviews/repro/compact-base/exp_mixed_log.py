"""Mixed schema, append-only table with an integer id (like rs_outcomes): O (no new column) appends a row and saves;
N (new column) applies the delta to its compact base, which keeps a projected entry it can never match again."""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim.cloud import CloudState  # noqa: E402
from aeo.ranksim.objects import MemoryStore  # noqa: E402

root = Path(__file__).with_name("work_mixed_log")
shutil.rmtree(root, ignore_errors=True)
store = MemoryStore()


def inst(name):
    return CloudState(data_dir=root / name, db_file=root / name / "rank.db", enabled=True, store=store)


def sql(state, statement, args=()):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def outcomes(state):
    return sqlite3.connect(str(state.db_file)).execute("SELECT id, query FROM rs_outcomes ORDER BY id").fetchall()


n, o = inst("n"), inst("o")
n.db_file.parent.mkdir(parents=True)
sql(n, "CREATE TABLE rs_outcomes (id INTEGER NOT NULL PRIMARY KEY, observed_on VARCHAR(10), query VARCHAR(500))")
sql(n, "CREATE TABLE rs_pages (url TEXT PRIMARY KEY, html TEXT)")
sql(n, "INSERT INTO rs_outcomes (observed_on, query) VALUES ('2026-11-01', 'seed')")
print("n save:", n.save())
o.refresh(force=True)
sql(n, "ALTER TABLE rs_outcomes ADD COLUMN source_file VARCHAR(500) DEFAULT ''")  # the new deploy's migration

sql(o, "INSERT INTO rs_outcomes (observed_on, query) VALUES ('2026-11-15', 'observed by o')")
print("o save:", o.save())
n.refresh(force=True)
print("n:", outcomes(n))
for k in range(2):
    sql(n, "INSERT OR REPLACE INTO rs_pages VALUES (?, 'x')", (f"u{k}",))
    print("n save:", n.save())
o.refresh(force=True)
print("o after n's saves:", outcomes(o))
sql(o, "INSERT OR REPLACE INTO rs_pages VALUES ('o', 'y')")
print("o save:", o.save())
n.refresh(force=True)
print("n after o's save:", outcomes(n))
