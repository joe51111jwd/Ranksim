"""Offline tests for scripts/claude/r2_wayback.py. No network: httpx.MockTransport and fake clocks only."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts.claude import r2_wayback as wb

HEADER = list(wb.CDX_FIELDS)


def _cdx_json(rows):
    return json.dumps([HEADER] + [list(r) for r in rows])


class FakeTime:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    def mono(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _polite(handler, fake: FakeTime, **kw) -> wb.Polite:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return wb.Polite(client, clock=fake.clock, mono=fake.mono, sleep=fake.sleep, out=lambda _m: None, **kw)


# ------------------------------------------------------------------ CDX parsing


def test_parse_cdx_header_rows_and_empty_bodies():
    text = _cdx_json([("20261005120000", "https://a.example.com/x", "200", "text/html", "D1", "1234")])
    rows = wb.parse_cdx(text)
    assert rows == [{"timestamp": "20261005120000", "original": "https://a.example.com/x", "statuscode": "200",
                     "mimetype": "text/html", "digest": "D1", "length": "1234"}]
    assert wb.parse_cdx("") == []
    assert wb.parse_cdx("[]") == []
    assert wb.parse_cdx(b"  \n") == []
    assert wb.parse_cdx(None) == []
    # already-parsed list (as stored in cdx.jsonl) and a malformed row
    raw = json.loads(text) + [["20261006000000", "short"]]
    assert len(wb.parse_cdx(raw)) == 1


def test_keep_captures_cutoff_status_mime_and_collapse():
    rows = wb.parse_cdx(_cdx_json([
        ("20261003000000", "u", "200", "text/html", "A", "1"),
        ("20261004000000", "u", "200", "text/html", "A", "1"),  # adjacent same digest: collapsed
        ("20261005000000", "u", "200", "text/html; charset=utf-8", "B", "1"),
        ("20261006000000", "u", "200", "application/pdf", "C", "1"),  # not html
        ("20261007000000", "u", "301", "text/html", "D", "1"),  # not 200
        ("20261008000000", "u", "200", "text/html", "A", "1"),  # back to A: a third version
        ("20261117235959", "u", "200", "text/html", "E", "1"),  # exactly the cutoff: kept
        ("20261118000000", "u", "200", "text/html", "F", "1"),  # after cutoff: dropped
        ("2026111800000", "u", "200", "text/html", "G", "1"),  # 13 digits: dropped
    ]))
    kept = wb.keep_captures(rows)
    assert [r["timestamp"] for r in kept] == ["20261003000000", "20261005000000", "20261008000000", "20261117235959"]
    assert [r["digest"] for r in kept] == ["A", "B", "A", "E"]


def test_select_captures_first_last_evenly_spaced():
    caps = [{"timestamp": f"202610{d:02d}000000"} for d in range(1, 21)]
    picked = wb.select_captures(caps, 6)
    assert len(picked) == 6
    assert picked[0] is caps[0] and picked[-1] is caps[-1]
    idx = [caps.index(p) for p in picked]
    assert idx == sorted(set(idx)) and idx == [0, 4, 8, 11, 15, 19]
    assert wb.select_captures(caps[:4], 6) == caps[:4]
    assert wb.select_captures(caps[:7], 6)[0] is caps[0]


def test_timestamp_helpers():
    assert wb.ts_to_iso("20261105123456") == "2026-11-05T12:34:56.000Z"
    assert wb.method_for("20261105123456") == "wayback:20261105123456"
    assert wb.ts_from_method("wayback:20261105123456") == "20261105123456"
    assert wb.ts_from_method("wayback") is None
    assert wb.ts_from_method("http") is None
    assert not wb.valid_ts("20261332000000")
    with pytest.raises(ValueError):
        wb.ts_to_iso("2026")
    assert wb.method_for("20261105123456").startswith("wayback")  # load_page_index keeps excluding it


def test_parse_available_hides_timestamps_after_cutoff():
    inside = {"archived_snapshots": {"closest": {"available": True, "timestamp": "20261110000000", "status": "200",
                                                 "url": "http://web.archive.org/web/x"}}}
    after = {"archived_snapshots": {"closest": {"available": True, "timestamp": "20261118010101", "status": "200"}}}
    assert wb.parse_available(inside) == {"available": True, "closest_ts": "20261110000000",
                                          "closest_after_cutoff": False, "closest_status": "200"}
    got = wb.parse_available(after)
    assert got["closest_after_cutoff"] and got["closest_ts"] is None and got["closest_status"] is None
    assert wb.parse_available({"archived_snapshots": {}})["available"] is False


# ------------------------------------------------------------------ resumability state


def test_jsonl_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "x.jsonl"
    wb.append_jsonl(path, {"a": 1})
    with path.open("a") as handle:
        handle.write('{"a": 2, "b"')  # cut by a crash or a snapshot restore
    assert wb.load_jsonl(path) == [{"a": 1}]
    assert wb.load_jsonl(tmp_path / "missing.jsonl") == []


def test_cdx_and_download_final_states():
    recs = [
        {"url_sha1": "s1", "from": wb.WINDOW_FROM, "to": wb.WINDOW_TO, "outcome": "transient:error:ConnectError"},
        {"url_sha1": "s2", "from": wb.WINDOW_FROM, "to": wb.WINDOW_TO, "outcome": "ok", "raw": []},
        {"url_sha1": "s3", "from": wb.WINDOW_FROM, "to": wb.WINDOW_TO, "outcome": "http_403"},
        {"url_sha1": "s4", "from": wb.WINDOW_FROM, "to": wb.WINDOW_TO, "outcome": "http_429"},
        {"url_sha1": "s5", "from": "20250101", "to": wb.WINDOW_TO, "outcome": "ok"},  # other window
    ]
    assert set(wb.cdx_latest(recs)) == {"s2", "s3"}
    downs = [
        {"url_sha1": "s", "timestamp": "1", "outcome": "stored"},
        {"url_sha1": "s", "timestamp": "2", "outcome": "failed:transient:http_503"},
        {"url_sha1": "s", "timestamp": "3", "outcome": "failed:http_404"},
        {"url_sha1": "s", "timestamp": "4", "outcome": "already_stored"},
    ]
    assert wb.downloads_done(downs) == {("s", "1"), ("s", "3"), ("s", "4")}


# ------------------------------------------------------------------ politeness


def test_pacer_waits_the_gap_on_both_clocks():
    fake = FakeTime()
    pacer = wb.Pacer(1.5, fake.clock, fake.mono, fake.sleep)
    pacer.wait()
    assert fake.slept == []
    pacer.mark()
    fake.t += 0.4
    pacer.wait()
    assert fake.t - pacer.last_wall >= 1.5
    assert all(s <= 1.0 for s in fake.slept)


def test_pacer_ignores_a_wall_clock_that_went_back():
    wall = [5000.0]
    mono = [10.0]
    pacer = wb.Pacer(1.5, lambda: wall[0], lambda: mono[0], lambda s: mono.__setitem__(0, mono[0] + s))
    pacer.mark()
    wall[0] -= 3600  # clock stepped back an hour
    pacer.wait()  # must not sleep for an hour
    assert mono[0] - 10.0 == pytest.approx(1.5)


def test_polite_backs_off_on_429_and_retries():
    fake = FakeTime()
    calls = []

    def handler(request):
        calls.append(fake.t)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "40"})
        return httpx.Response(200, content=b"[]")

    polite = _polite(handler, fake)
    res = polite.get("https://web.archive.org/cdx/search/cdx")
    assert res.status == 200 and len(calls) == 2
    assert calls[1] - calls[0] >= 40  # Retry-After honoured
    assert polite.refusals == 0 and polite.refused_total == 1


def test_client_has_generic_user_agent_and_no_auto_redirects():
    client = wb.make_client()
    try:
        assert client.headers["user-agent"] == "ranksim-research-bot/0.1"
        assert client.follow_redirects is False
    finally:
        client.close()


def test_polite_stops_after_consecutive_refusals():
    fake = FakeTime()
    seen = []

    def handler(request):
        seen.append(request.url.host)
        raise httpx.ConnectError("refused")

    polite = _polite(handler, fake, attempts=3, max_refusals=4, base_backoff=10)
    first = polite.get("https://web.archive.org/a")
    assert first.status is None and first.reason == "error:ConnectError" and len(seen) == 3
    with pytest.raises(wb.ServiceRefused):
        polite.get("https://web.archive.org/b")
    assert len(seen) == 4
    # backoff 10 s then 20 s inside the first get (none after its last attempt); the fourth refusal
    # stops at once. Only the 1.5 s gap before the fourth request is extra (backoffs already cover the others).
    assert sum(fake.slept) == pytest.approx(10 + 20 + 1.5)


def test_requests_are_spaced_at_least_gap_apart():
    fake = FakeTime()
    stamps = []

    def handler(request):
        stamps.append(fake.t)
        return httpx.Response(200, content=b"[]")

    polite = _polite(handler, fake, gap=1.5)
    for _ in range(4):
        polite.get("https://web.archive.org/cdx/search/cdx")
    assert all(b - a >= 1.5 for a, b in zip(stamps, stamps[1:]))


# ------------------------------------------------------------------ run loops (temp files and DB)

URLS = [("https://a.example.com/x", "o30"), ("https://b.example.com/y", "a70")]


def _cdx_handler(calls):
    def handler(request):
        calls.append(str(request.url))
        assert request.url.params.get("filter") == "statuscode:200"
        assert request.url.params.get("collapse") == "digest"
        assert request.url.params.get("to") == wb.WINDOW_TO
        url = request.url.params.get("url")
        if url == URLS[0][0]:
            return httpx.Response(200, text=_cdx_json([
                ("20261005000000", url, "200", "text/html", "A", "9"),
                ("20261110000000", url, "200", "text/html", "B", "9"),
            ]))
        return httpx.Response(200, text="")

    return handler


def test_run_cdx_is_resumable(tmp_path):
    fake = FakeTime()
    calls: list[str] = []
    polite = _polite(_cdx_handler(calls), fake)
    stats = wb.run_cdx(polite, URLS, tmp_path, out=lambda _m: None)
    assert stats["queried"] == 2 and len(calls) == 2
    stored = wb.load_jsonl(tmp_path / "cdx.jsonl")
    assert all("url" not in rec for rec in stored)  # sha1 only, plus raw CDX rows
    assert stored[0]["url_sha1"] == wb.url_sha1(URLS[0][0]) and stored[0]["n_rows"] == 2
    # second run: nothing new to ask
    stats = wb.run_cdx(polite, URLS, tmp_path, out=lambda _m: None)
    assert stats["skipped_done"] == 2 and len(calls) == 2


def test_run_cdx_retries_transient_records_next_time(tmp_path):
    wb.append_jsonl(tmp_path / "cdx.jsonl", {"url_sha1": wb.url_sha1(URLS[0][0]), "from": wb.WINDOW_FROM,
                                             "to": wb.WINDOW_TO, "outcome": "transient:error:ConnectError"})
    calls: list[str] = []
    polite = _polite(_cdx_handler(calls), FakeTime())
    stats = wb.run_cdx(polite, URLS, tmp_path, out=lambda _m: None)
    assert stats["queried"] == 2


DATA_LIB = Path("/workspace/ranksim-data/lib/db.py")
needs_lib = pytest.mark.skipif(not DATA_LIB.exists(), reason="ranksim-data lib not present on this machine")


@needs_lib
def test_run_download_stores_rows_and_never_refetches(tmp_path):
    from lib.db import init_db

    from scripts.claude.refetch_html import write_batch

    con = init_db(tmp_path / "raw.sqlite")
    cdx_calls: list[str] = []
    wb.run_cdx(_polite(_cdx_handler(cdx_calls), FakeTime()), URLS, tmp_path, out=lambda _m: None)
    body = b"<html><head><title>T</title></head><body><p>hello</p></body></html>"
    got: list[str] = []

    def handler(request):
        got.append(str(request.url))
        assert "id_/" in str(request.url)
        return httpx.Response(200, content=body, headers={"content-type": "text/html; charset=utf-8"})

    fake = FakeTime()
    stats = wb.run_download(_polite(handler, fake), con, URLS, tmp_path, write_rows=write_batch, out=lambda _m: None)
    assert stats["stored"] == 2 and len(got) == 2
    rows = con.execute("SELECT url, fetched_at_utc, fetch_method, http_status, sha256 FROM pages ORDER BY id").fetchall()
    assert [tuple(r) for r in rows][0][:4] == (URLS[0][0], "2026-10-05T00:00:00.000Z", "wayback:20261005000000", 200)
    side = wb.load_jsonl(tmp_path / "downloads.jsonl")
    assert {r["outcome"] for r in side} == {"stored"} and all(r.get("downloaded_at_utc") for r in side)
    # rerun: the sidecar says done, no request
    stats = wb.run_download(_polite(handler, fake), con, URLS, tmp_path, write_rows=write_batch, out=lambda _m: None)
    assert len(got) == 2 and stats["skipped_logged"] == 2
    # sidecar lost (snapshot restore) but the DB rows exist: checked before each request, no download
    (tmp_path / "downloads.jsonl").unlink()
    stats = wb.run_download(_polite(handler, fake), con, URLS, tmp_path, write_rows=write_batch, out=lambda _m: None)
    assert len(got) == 2 and stats["already_stored"] == 2
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 2
    con.close()


@needs_lib
def test_run_download_logs_permanent_and_transient_failures(tmp_path):
    from lib.db import init_db

    con = init_db(tmp_path / "raw.sqlite")
    wb.run_cdx(_polite(_cdx_handler([]), FakeTime()), URLS, tmp_path, out=lambda _m: None)
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if "20261005000000" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(503)

    fake = FakeTime()
    polite = _polite(handler, fake, attempts=2, max_refusals=10, base_backoff=1)
    stats = wb.run_download(polite, con, URLS, tmp_path, write_rows=lambda c, rows: [None], out=lambda _m: None)
    assert stats["failed:http_404"] == 1 and stats["failed:transient:http_503"] == 1
    before = state["n"]
    wb.run_download(polite, con, URLS, tmp_path, write_rows=lambda c, rows: [None], out=lambda _m: None)
    assert state["n"] - before == 2  # only the transient capture is retried (2 attempts), the 404 is not
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
    con.close()


def test_download_capture_row_shape_and_redirect_rules():
    fake = FakeTime()

    def handler(request):
        if "20261110000000id_" in str(request.url):
            return httpx.Response(302, headers={"location": "https://web.archive.org/web/20261109000000id_/https://a.example.com/x"})
        return httpx.Response(200, content=b"<html>x</html>", headers={"content-type": "text/html"})

    polite = _polite(handler, fake)
    row, meta = wb.download_capture(polite, "https://a.example.com/x", {"timestamp": "20261005000000", "digest": "A"})
    assert row["fetch_method"] == "wayback:20261005000000" and row["fetched_at_utc"] == "2026-10-05T00:00:00.000Z"
    assert row["html"] == "<html>x</html>" and row["http_status"] == 200 and "outcome" not in meta
    assert meta["downloaded_at_utc"] and meta["sha256"] == row["sha256"]
    row, meta = wb.download_capture(polite, "https://a.example.com/x", {"timestamp": "20261110000000", "digest": "B"})
    assert row is None and meta["outcome"] == "failed:redirect"  # a different capture time is never stored


# ------------------------------------------------------------------ measurement helpers (pure)


def test_build_pairs_ww_wh_and_noise():
    w = [
        {"key": "k", "id": 1, "time": "2026-10-05T12:00:00.000Z", "day": "2026-10-05", "source": "wayback_r2"},
        {"key": "k", "id": 2, "time": "2026-11-09T15:00:00.000Z", "day": "2026-11-09", "source": "wayback_r2"},
        {"key": "k", "id": 3, "time": "2026-11-03T00:00:00.000Z", "day": "2026-11-03", "source": "wayback_legacy"},
    ]
    h = [
        {"key": "k", "id": 10, "time": "2026-11-07T03:10:00.000Z", "day": "2026-11-06", "pick": True},
        {"key": "k", "id": 11, "time": "2026-11-09T21:00:00.000Z", "day": "2026-11-09", "pick": True},
    ]
    specs = wb.build_pairs(w, h)
    ww = [(s["a"]["id"], s["b"]["id"]) for s in specs if s["type"] == "ww"]
    assert ww == [(1, 3), (3, 2)]
    wh = [(s["a"]["id"], s["b"]["id"]) for s in specs if s["type"] == "wh"]
    assert wh == [(3, 10)]  # latest capture on a day before the first own fetch day
    noise = [s for s in specs if s["type"] == "noise"]
    assert [(s["a"]["id"], s["b"]["id"]) for s in noise] == [(2, 11)] and noise[0]["hours"] == 6.0


def test_change_rows_alignment_and_universe():
    days = {"q": ["2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16", "2026-11-17"],
            "r": ["2026-11-16"]}
    cons = {("q", "2026-11-09", "k"): 4, ("q", "2026-11-13", "k"): 7, ("r", "2026-11-16", "k"): 2}
    first = {("q", "k"): "2026-11-09", ("r", "k"): "2026-11-16"}
    qbk = {"k": {"q", "r"}}
    groups = {"q": "o30", "r": "a70"}
    changes = [
        {"key": "k", "site": "s", "type": "ww", "day_a": "2026-11-10", "day_b": "2026-11-12"},
        {"key": "k", "site": "s", "type": "ww", "day_a": "2026-10-05", "day_b": "2026-10-20"},
    ]
    rows = wb.change_rows(changes, cons, days, first, qbk, groups)
    strict = [r for r in rows if r["mode"] == "strict"]
    loose = [r for r in rows if r["mode"] == "loose"]
    assert [(r["serp_day_a"], r["serp_day_b"], r["pos_a"], r["pos_b"]) for r in strict] == [("2026-11-09", "2026-11-13", 4, 7)]
    assert [(r["serp_day_a"], r["serp_day_b"]) for r in loose] == [("2026-11-09", "2026-11-13")]
    assert all(r["query"] == "q" for r in rows)  # a70: one pulled day, never before and after
    assert strict[0]["has_pos_a"] and strict[0]["moved"]


@needs_lib
def test_later_window_extends_without_refetching(tmp_path):
    from lib.db import init_db

    from scripts.claude.refetch_html import write_batch

    con = init_db(tmp_path / "raw.sqlite")
    url = URLS[0][0]

    def cdx(request):
        rows = [("20261005000000", url, "200", "text/html", "A", "9"),
                ("20261120000000", url, "200", "text/html", "C", "9")]
        end = request.url.params.get("to") + "235959"
        return httpx.Response(200, text=_cdx_json([r for r in rows if r[0] <= end]))

    got: list[str] = []

    def replay(request):
        got.append(str(request.url))
        return httpx.Response(200, content=b"<html>x</html>", headers={"content-type": "text/html"})

    one = [URLS[0]]
    wb.run_cdx(_polite(cdx, FakeTime()), one, tmp_path, out=lambda _m: None)
    wb.run_download(_polite(replay, FakeTime()), con, one, tmp_path, write_rows=write_batch, out=lambda _m: None)
    assert [g.split("/web/")[1][:14] for g in got] == ["20261005000000"]
    later = ("20261001", "20261130")
    assert wb.cutoff_for(later) == "20261130235959"
    stats = wb.run_cdx(_polite(cdx, FakeTime()), one, tmp_path, window=later, out=lambda _m: None)
    assert stats["queried"] == 1  # a new window is a new query
    wb.run_download(_polite(replay, FakeTime()), con, one, tmp_path, write_rows=write_batch, window=later,
                    out=lambda _m: None)
    assert [g.split("/web/")[1][:14] for g in got] == ["20261005000000", "20261120000000"]
    assert con.execute("SELECT count(*) FROM pages").fetchone()[0] == 2
    con.close()
