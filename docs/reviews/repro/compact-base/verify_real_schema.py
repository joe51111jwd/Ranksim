"""Independent check with the app's real schema (store.session_factory) and the exact DDL of the pending
keyword-volume branch: rs_pages gains fetch_method / fetch_log via ALTER TABLE ADD COLUMN.

O: an instance of the old deployment (no new columns), warm before the deploy, finishing a pull job.
N: an instance of the new deployment, cold-started from the store, its engine then runs the ALTERs.
Usage: python verify_real_schema.py <path to cloud.py to test>
"""
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/home/user/Ranksim/src")
spec = importlib.util.spec_from_file_location("cloud_under_test", sys.argv[1])
cloud = importlib.util.module_from_spec(spec)
sys.modules["cloud_under_test"] = cloud
spec.loader.exec_module(cloud)
from aeo.ranksim import store as app_store  # noqa: E402
from aeo.ranksim.objects import MemoryStore  # noqa: E402

root = Path(sys.argv[2])
shutil.rmtree(root, ignore_errors=True)
blob = MemoryStore()


def inst(name):
    return cloud.CloudState(data_dir=root / name, db_file=root / name / "rank.db", enabled=True, store=blob)


def engine(state):
    return app_store.session_factory(f"sqlite:///{state.db_file}")


def sql(state, statement, args=()):
    conn = sqlite3.connect(str(state.db_file))
    conn.execute(statement, args)
    conn.commit()
    conn.close()


def pages(state):
    conn = sqlite3.connect(str(state.db_file))
    try:
        return conn.execute("SELECT url, title FROM rs_pages ORDER BY url").fetchall()
    finally:
        conn.close()


def try_save(state, label):
    try:
        print(f"{label}:", state.save())
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: FAILED {type(exc).__name__}: {exc} (cause: {exc.__cause__!r})")


o = inst("o")
o.db_file.parent.mkdir(parents=True)
engine(o)  # the app creates the schema
with app_store.open_session(f"sqlite:///{o.db_file}") as s:
    for k in range(5):
        app_store.upsert_page(s, url=f"https://ex.com/{k}", title=f"page {k}")
    s.commit()
try_save(o, "o first save (full snapshot)")
(o.projects_dir / "demo").mkdir(parents=True, exist_ok=True)
(o.projects_dir / "demo" / "a.txt").write_text("o")
try_save(o, "o save project")

# --- deploy: N cold-starts. The app creates an empty db (new model, includes the columns), the cold sync renames the
# stored snapshot into place, engines are disposed; the next engine creation runs the migration.
n = inst("n")
n.db_file.parent.mkdir(parents=True)
engine(n)
sql(n, "SELECT 1")
print("n cold sync changed:", n.refresh(force=True))
for ddl in ("ALTER TABLE rs_pages ADD COLUMN fetch_method VARCHAR(32) DEFAULT ''", "ALTER TABLE rs_pages ADD COLUMN fetch_log TEXT DEFAULT ''"):
    sql(n, ddl)  # what the branch's _ensure migration does at the next engine creation
# N does ordinary work: a new page through the app, then a save (base gets the columns via _base_add_columns)
sql(n, "INSERT INTO rs_pages (url, domain, status, raw_html, title, h1, headings_text, body, word_count, table_count, has_faq, link_count, authority_source, fetched_at, fetch_method) VALUES ('https://ex.com/n', 'ex.com', 'fetched', '', 'from n', '', '', '', 0, 0, 0, 0, 'unavailable', '2026-11-15', 'browser')")
try_save(n, "n save (new page)")
o.refresh(force=True)
print("o after n's save:", pages(o))

# --- O (old deployment, in-flight pull job) refetches page 2 and saves at the end of the job
with app_store.open_session(f"sqlite:///{o.db_file}") as s:
    app_store.upsert_page(s, url="https://ex.com/2", title="page 2 refetched by o")
    s.commit()
try_save(o, "o job save")

# --- N catches up, then a user edits a project file on N and N saves
print("n refresh:", n.refresh(force=True))
conn = sqlite3.connect(str(n._base_db))
try:
    print("n base rs_pages entries:", conn.execute("SELECT count(*) FROM rows WHERE tbl='rs_pages'").fetchone()[0], "rows in n db:", len(pages(n)))
except sqlite3.Error:
    print("n base rs_pages rows (full copy):", conn.execute("SELECT count(*) FROM rs_pages").fetchone()[0], "rows in n db:", len(pages(n)))
conn.close()
(n.projects_dir / "demo" / "a.txt").write_text("edited on n")
sql(n, "UPDATE rs_pages SET title = 'n edit' WHERE url = 'https://ex.com/n'")
try_save(n, "n save #1 after o's delta")
try_save(n, "n save #2 (retry)")
probe = inst("probe")
probe.db_file.parent.mkdir(parents=True)
probe.refresh(force=True)
print("project file in storage:", (probe.projects_dir / "demo" / "a.txt").read_text())
print("pages in storage:", pages(probe))
