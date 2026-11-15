"""One deploy adds a column (every instance runs the same ALTER). A fresh instance C applies a delta carrying a value
in the new column before its own migration runs, so C's base and main hold the default where every other instance
holds the value. A later edit of that row elsewhere then leaves a stale entry in C's base, and C's next save ships it
as a deletion."""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
if len(sys.argv) > 1 and sys.argv[1] == "old":
    import importlib.util

    spec = importlib.util.spec_from_file_location("old_cloud", str(Path(__file__).parent.parent / "old_cloud.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["old_cloud"] = mod
    spec.loader.exec_module(mod)
    CloudState = mod.CloudState
    tag = "old"
else:
    from aeo.ranksim.cloud import CloudState  # noqa: E402
    tag = "new"
from aeo.ranksim.objects import MemoryStore  # noqa: E402

root = Path(__file__).with_name(f"work_cold_delete_{tag}")
shutil.rmtree(root, ignore_errors=True)
store = MemoryStore()
OLD = "CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, intent TEXT, notes TEXT)"
ALTER = "ALTER TABLE rs_keywords ADD COLUMN project_slug VARCHAR(200) DEFAULT ''"


def inst(name):
    return CloudState(data_dir=root / name, db_file=root / name / "rank.db", enabled=True, store=store)


def sql(state, statement, args=()):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def migrate(state):  # what session_factory -> _ensure_ledger_columns does on the next engine
    cols = [r[1] for r in sqlite3.connect(str(state.db_file)).execute("PRAGMA table_info(rs_keywords)")]
    if "project_slug" not in cols:
        sql(state, ALTER)


def show(state):
    return sqlite3.connect(str(state.db_file)).execute("SELECT query, intent, project_slug FROM rs_keywords WHERE query IN ('k1','k2') ORDER BY query").fetchall()


a = inst("a")
a.db_file.parent.mkdir(parents=True)
sql(a, OLD)
for k in ("k1", "k2", "k3"):
    sql(a, "INSERT INTO rs_keywords VALUES (?, 'info', '')", (k,))
print("pre-deploy snapshot:", a.save())

# the deploy. A migrates and fills the new column on k1
migrate(a)
sql(a, "UPDATE rs_keywords SET project_slug = 'acme' WHERE query = 'k1'")
print("a delta:", a.save())

# a fresh instance C of the same deploy: empty migrated db at start-up, then the cold sync
c = inst("c")
c.db_file.parent.mkdir(parents=True)
sql(c, OLD)
migrate(c)
c.refresh(force=True)
migrate(c)  # its engine is created again on the next request
print("c after cold sync:", show(c))

# A edits k1 again (another field)
sql(a, "UPDATE rs_keywords SET intent = 'commercial' WHERE query = 'k1'")
print("a delta:", a.save())
c.refresh(force=True)
print("c after a's edit:", show(c))

# C saves anything (an unrelated row)
sql(c, "UPDATE rs_keywords SET notes = 'x' WHERE query = 'k3'")
print("c save:", c.save())
a.refresh(force=True)
print("a after c's save:", show(a))
print("c:", show(c))
b = inst("b")  # any later fresh instance
b.db_file.parent.mkdir(parents=True)
sql(b, OLD)
migrate(b)
b.refresh(force=True)
migrate(b)
print("fresh b:", show(b))
