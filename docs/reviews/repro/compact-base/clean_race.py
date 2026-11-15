"""A write that commits while a refresh is merging another instance's delta is marked clean and never uploaded."""
import sys, sqlite3, shutil
from pathlib import Path
sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore

root = Path(sys.argv[1]); shutil.rmtree(root, ignore_errors=True); root.mkdir(parents=True)
store = MemoryStore()
def inst(name):
    return CloudState(data_dir=root / name, db_file=root / name / "rank.db", enabled=True, store=store)
a, b = inst("a"), inst("b")
a.data_dir.mkdir(parents=True)
c = sqlite3.connect(a.db_file); c.execute("CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, project TEXT, created_at TEXT)")
c.execute("INSERT INTO rs_keywords VALUES ('one','demo','x')"); c.commit(); c.close()
print("A save 1:", a.save())
print("B cold sync:", b.refresh(force=True), "B clean:", b._db_is_clean())
c = sqlite3.connect(a.db_file); c.execute("INSERT INTO rs_keywords VALUES ('from-a','demo','x')"); c.commit(); c.close()
print("A save 2 (delta):", a.save())

real_apply = cloud._base_apply
def base_apply(base, minus, plus):
    # A request thread on B whose commit was queued behind merge_sqlite's write lock lands now: the working db is
    # unlocked while the base is updated.
    w = sqlite3.connect(b.db_file, timeout=5); w.execute("INSERT INTO rs_keywords VALUES ('written-on-b','demo','x')"); w.commit(); w.close()
    return real_apply(base, minus, plus)
cloud._base_apply = base_apply
print("B refresh (catch-up of A's delta):", b.refresh(force=True))
cloud._base_apply = real_apply
print("B db clean after refresh:", b._db_is_clean())
print("B's write request middleware save():", b.save())
rows = lambda p: sorted(r[0] for r in sqlite3.connect(p).execute("select query from rs_keywords"))
print("B local rows:", rows(b.db_file))
cc = inst("c"); cc.refresh(force=True)
print("fresh instance C rows (= storage):", rows(cc.db_file))
c = sqlite3.connect(a.db_file); c.close(); a.refresh(force=True)
print("A rows after refresh:", rows(a.db_file))

# Job variant: the job's last rows land in the window, then on_finish records the job and saves.
c = sqlite3.connect(a.db_file); c.execute("INSERT INTO rs_keywords VALUES ('from-a-2','demo','x')"); c.commit(); c.close(); a.save()
def base_apply2(base, minus, plus):
    w = sqlite3.connect(b.db_file, timeout=5); w.execute("INSERT INTO rs_keywords VALUES ('job-last-row','demo','x')"); w.commit(); w.close()
    return real_apply(base, minus, plus)
cloud._base_apply = base_apply2
b.refresh(force=True)
cloud._base_apply = real_apply
b.record_job({"id": "job1", "status": "done", "updated": 1})
print("job on_finish save:", b.save())
d = inst("d"); d.refresh(force=True)
print("storage now: rows", rows(d.db_file), "| job record:", d.job_record("job1"))
