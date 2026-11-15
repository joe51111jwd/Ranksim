"""A row a request thread commits (without the cloud lock) while another request's catch-up is applying a delta:
_apply_delta checks "clean" before the merge and records the db's stat signature as clean after _base_apply, so the
row counts as already saved. The writer's own save then uploads nothing."""
import sqlite3
import sys
import tempfile
from pathlib import Path

src = sys.argv[1] if len(sys.argv) > 1 else "/home/user/Ranksim/src"
sys.path.insert(0, src)
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore

root = Path(tempfile.mkdtemp(prefix="absorb-"))


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
conn.execute("INSERT INTO rs_keywords VALUES ('k1', 'info', '')")
conn.commit()
conn.close()
a.save()
b.refresh(force=True)
sql(a, "INSERT INTO rs_keywords VALUES ('from a', 'info', '')")
a.save()

hook = "_base_apply" if hasattr(cloud, "_base_apply") else None
if hook:
    real = cloud._base_apply

    def during(*args, **kwargs):
        sql(b, "INSERT INTO rs_keywords VALUES ('written by a request on b', 'info', '')")  # another thread, no lock
        return real(*args, **kwargs)

    cloud._base_apply = during
else:  # the old code merged the delta into base.db second
    real = cloud.merge_sqlite
    n = []

    def during(*args, **kwargs):
        n.append(1)
        if len(n) == 2:
            sql(b, "INSERT INTO rs_keywords VALUES ('written by a request on b', 'info', '')")
        return real(*args, **kwargs)

    cloud.merge_sqlite = during
b.refresh(force=True)  # catch-up by another request (stale hint / due)
if hook:
    cloud._base_apply = real
else:
    cloud.merge_sqlite = real
print("B's own save after its write:", b.save())
a.refresh(force=True)
print("A has:", rows(a, "SELECT query FROM rs_keywords ORDER BY query"))
print("B has:", rows(b, "SELECT query FROM rs_keywords ORDER BY query"))
