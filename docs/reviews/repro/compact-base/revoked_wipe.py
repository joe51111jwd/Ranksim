"""A revoked session cookie validates on an instance while (and after) its disk-full wipe resets the manifest."""
import sys, sqlite3, shutil, threading, time, errno
from pathlib import Path
sys.path.insert(0, "/home/user/Ranksim/src")
from aeo.ranksim import cloud
from aeo.ranksim.cloud import CloudState
from aeo.ranksim.objects import MemoryStore, StoreError
from aeo.ranksim.access import Access

root = Path(sys.argv[1]); shutil.rmtree(root, ignore_errors=True); root.mkdir(parents=True)
store = MemoryStore()
inst = lambda n: CloudState(data_dir=root / n, db_file=root / n / "rank.db", enabled=True, store=store)
a, b = inst("a"), inst("b"); a.data_dir.mkdir(parents=True)
c = sqlite3.connect(a.db_file); c.execute("CREATE TABLE rs_keywords (query TEXT PRIMARY KEY, project TEXT, created_at TEXT)")
c.execute("INSERT INTO rs_keywords VALUES ('secret project data','demo','x')"); c.commit(); c.close()
a.save(); b.refresh(force=True)
acc_a, acc_b = Access("pw", "sec", shared=a), Access("pw", "sec", shared=b)
stolen = acc_b.issue(time.time() - 5)
time.sleep(0.01)
print("revoke_all on A:", acc_a.revoke_all())
b.refresh(force=True)
print("B accepts the stolen cookie after catching up the revocation:", acc_b.valid(stolen))

# B's next look at storage hits a full disk twice: _wipe(), then a cold sync (here: slow, then failing).
calls = {"n": 0}
real_catch_up = CloudState._catch_up
def catch_up(self, *, verify=False):
    if self is b:
        raise OSError(errno.ENOSPC, "No space left on device")
    return real_catch_up(self, verify=verify)
CloudState._catch_up = catch_up
real_cold = CloudState._cold_sync
def cold(self):
    if self is b:
        time.sleep(1.0)  # downloading the snapshot and the project files
        raise StoreError("connection reset")
    return real_cold(self)
CloudState._cold_sync = cold
r = threading.Thread(target=lambda: (lambda: b.refresh(force=True))() if True else None)
def run():
    try:
        b.refresh(force=True)
    except Exception as exc:
        print("B refresh:", type(exc).__name__, exc)
r = threading.Thread(target=run); r.start()
time.sleep(0.3)
print("during B's wipe + cold sync, B accepts the stolen cookie:", acc_b.valid(stolen))
r.join()
print("after the failed resync, B accepts the stolen cookie:", acc_b.valid(stolen), "| B._seq", b._seq, "| B._checked set:", b._checked is not None)
print("a non-forced refresh (an ordinary GET) right after:", b.refresh(hint=0), "-> the request is served from the kept working copy:",
      [r[0] for r in sqlite3.connect(b.db_file).execute("select query from rs_keywords")])
