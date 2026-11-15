import runpy, sys
sys.argv = [sys.argv[0], "rs_pages"]
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    g = runpy.run_path(sys.path[0] + "/exp_column_skew.py")
cloud, x, y, sql = g["cloud"], g["x"], g["y"], g["sql"]
cloud.COMPACT_DELTAS = 0  # X's next save compacts: snapshot + delta
sql(x, "UPDATE rs_pages SET html = 'v3' WHERE url = 'https://a/2'")
print("X compact save:", x.save(), x._manifest["db"]["snapshot"])
y.refresh(force=True)
sql(y, "INSERT INTO rs_outcomes (query, recorded_at, note) VALUES ('later', '2026-11-04', 'y')")
try:
    print("Y save after X's compaction:", y.save())
except cloud.CloudError as exc:
    print("Y save after X's compaction: CloudError", exc)
print("Y note of page 2:", g["rows"](y, "SELECT note FROM rs_pages WHERE url='https://a/2'"))
