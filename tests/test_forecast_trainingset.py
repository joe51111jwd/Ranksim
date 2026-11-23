"""The training-set build (plan step 4): new-site cohorts, the search universe, paid pulls, targets and the split.

Offline. Every provider call goes through httpx.MockTransport. Nothing sleeps for real. Fixtures under
tests/fixtures/forecast_trainingset/ are tiny SYNTHETIC EDITS in the real layouts. The DataForSEO answers reuse
the docs examples in tests/fixtures/forecast/.
"""

from __future__ import annotations

import copy
import gzip
import json
import os
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aeo.ranksim.forecast import budget
from aeo.ranksim.forecast import trainingset as ts
from aeo.ranksim.forecast.asof import Dated, LeakError
from aeo.ranksim.forecast.budget import BudgetError, SpendLedger
from aeo.ranksim.forecast.dataforseo import DataForSeoClient
from aeo.ranksim.forecast.sources import newsites as ns
from aeo.ranksim.forecast.sources import universe as uv
from aeo.ranksim.forecast.sources.panel import load_panel
from aeo.ranksim.forecast.synth import World

FIX = Path(__file__).parent / "fixtures" / "forecast_trainingset"
DFS_FIX = Path(__file__).parent / "fixtures" / "forecast"
NO_SLEEP: Callable[[float], None] = lambda seconds: None


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def dfs_fixture(name: str) -> Any:
    return json.loads((DFS_FIX / name).read_text(encoding="utf-8"))


def refuse_all(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request may be sent here: {request.url}")


# ----------------------------------------------------------------------------------------------
# Registration-day lists from git history.
# ----------------------------------------------------------------------------------------------


def make_nrd_repo(tmp_path: Path) -> Path:
    work = tmp_path / "nrd-src"
    work.mkdir()
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=work, check=True)
    for day, name in (("2025-10-26", "nrd_day1.txt"), ("2025-10-27", "nrd_day2.txt")):
        (work / "nrd-last-10-days.txt").write_text(fixture(name), encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{day}T07:30:00+00:00", "GIT_COMMITTER_DATE": f"{day}T07:30:00+00:00"}
        subprocess.run([*git, "add", "nrd-last-10-days.txt"], cwd=work, check=True, env=env)
        subprocess.run([*git, "commit", "-q", "-m", f"update {day}"], cwd=work, check=True, env=env)
    return work


def test_nrd_daily_diff_from_git_history_raw_and_git_modes(tmp_path):
    source = make_nrd_repo(tmp_path)
    repo = ns.NrdRepo("test", "example/nrd", "nrd-last-10-days.txt", date(2025, 10, 1), "test repo")
    shas = subprocess.run(["git", "log", "--format=%H %cI"], cwd=source, check=True, capture_output=True, text=True).stdout.split("\n")
    by_sha = {}
    for line in shas:
        if line.strip():
            sha, stamp = line.split()
            by_sha[sha] = fixture("nrd_day2.txt" if stamp.startswith("2025-10-27") else "nrd_day1.txt")
    seen: list[str] = []

    def raw(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        sha = request.url.path.split("/")[3]
        return httpx.Response(200, text=by_sha[sha])

    fetcher = ns.NrdFetcher(repo, root=tmp_path / "raw-mode", http=httpx.Client(transport=httpx.MockTransport(raw)),
                            sleep=NO_SLEEP, clone_url=str(source))
    daily, skipped = fetcher.daily_lists(date(2025, 10, 27), date(2025, 10, 27))
    assert skipped == []
    (one,) = daily
    assert one.total_new == 6 and one.list_updated_at == "2025-10-27T07:20:01Z"
    assert one.by_tld == {"com": 5, "net": 1}
    assert len(seen) == 2 and all(url.startswith("https://raw.githubusercontent.com/example/nrd/") for url in seen)
    regs = ns.registrations_from(daily, tlds=("com",), cap_per_day=None)
    assert [r.site for r in regs] == ["brandnewbakery.com", "freshplumbing.com", "gardenfresh.com", "quietsite.com", "xn--caf-dma.com"]
    assert all(r.listed_on == date(2025, 10, 27) and r.registered_on is None for r in regs)
    capped = ns.registrations_from(daily, tlds=("com",), cap_per_day=2)
    assert len(capped) == 2 and capped == ns.registrations_from(daily, tlds=("com",), cap_per_day=2)
    assert {r.site for r in capped} == set(sorted((r.site for r in regs), key=ns.cap_order)[:2])

    # Resumable: a second run reads the saved files and sends nothing.
    again = ns.NrdFetcher(repo, root=tmp_path / "raw-mode", http=httpx.Client(transport=httpx.MockTransport(refuse_all)),
                          sleep=NO_SLEEP, clone_url=str(source))
    assert again.daily_lists(date(2025, 10, 27), date(2025, 10, 27))[0] == daily

    # The git mode reads the same blobs from the clone.
    git_mode = ns.NrdFetcher(repo, root=tmp_path / "git-mode", mode="git", clone_url=str(source))
    assert git_mode.daily_lists(date(2025, 10, 27), date(2025, 10, 27))[0][0].total_new == 6

    # A day with no commit the day before is skipped and reported, never diffed across a gap.
    _, gap = fetcher.daily_lists(date(2025, 10, 27), date(2025, 10, 28))
    assert gap == [{"day": "2025-10-28", "reason": "no commit on this day"}]
    with pytest.raises(ValueError, match="history starts"):
        fetcher.daily_lists(date(2025, 10, 1), date(2025, 10, 2))


def test_excluded_site_never_enters_registrations(tmp_path):
    out = tmp_path / "d.txt.gz"
    out.write_bytes(gzip.compress(b"smartcrewhq.com\nokay.com\n"))
    daily = [ns.DailyList("r", date(2025, 10, 27), "a", "b", None, 2, {"com": 2}, str(out))]
    assert [r.site for r in ns.registrations_from(daily, cap_per_day=None)] == ["okay.com"]


# ----------------------------------------------------------------------------------------------
# A fake Common Crawl, RDAP and DNS behind one MockTransport.
# ----------------------------------------------------------------------------------------------

PRE, POST1, POST2 = "CC-MAIN-TEST-01", "CC-MAIN-TEST-02", "CC-MAIN-TEST-03"
CRAWLS = {
    PRE: ns.Crawl(PRE, datetime(2025, 10, 1, tzinfo=timezone.utc), datetime(2025, 10, 14, tzinfo=timezone.utc)),
    POST1: ns.Crawl(POST1, datetime(2025, 12, 4, tzinfo=timezone.utc), datetime(2025, 12, 17, tzinfo=timezone.utc)),
    POST2: ns.Crawl(POST2, datetime(2026, 2, 6, tzinfo=timezone.utc), datetime(2026, 2, 19, tzinfo=timezone.utc)),
}
PLAN = ns.CrawlPlan((PRE,), (POST1, POST2))


def warc_record(url: str, html_text: str) -> bytes:
    body = html_text.encode("utf-8")
    http_part = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + body
    head = f"WARC/1.0\r\nWARC-Type: response\r\nWARC-Target-URI: {url}\r\nContent-Length: {len(http_part)}\r\n\r\n".encode()
    return gzip.compress(head + http_part, mtime=0)


def cdx_line(surt: str, stamp: str, url: str, status: str = "200", mime: str = "text/html", **extra: Any) -> str:
    record = {"url": url, "mime": mime, "mime-detected": mime, "status": status, "digest": "X", "length": "0", "offset": "0",
              "filename": "none", "charset": "UTF-8", "languages": "eng", **extra}
    return f"{surt} {stamp} {json.dumps(record)}"


class FakeWeb:
    """RDAP, DNS-over-HTTPS and Common Crawl (cluster.idx, CDX blocks, WARC records) for a handful of domains."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.files: dict[str, bytes] = {}
        self.rdap: dict[str, tuple[int, Any]] = {}
        self.dns: dict[str, Any] = {}
        warc = bytearray()
        pages: dict[str, tuple[int, int]] = {}
        for name, site, page in (("bakery", "freshbakery.com", "home_bakery.html"), ("died", "launchdied.com", "home_bakery.html"),
                                 ("parked", "parkedbox.com", "home_parked.html"), ("rebrand", "northwindstudio.com", "home_rebrand.html")):
            record = warc_record(f"https://{site}/", fixture(page))
            pages[name] = (len(warc), len(record))
            warc.extend(record)
        warc_name = "crawl-data/CC-MAIN-TEST-02/warc/test.warc.gz"
        self.files[f"/{warc_name}"] = bytes(warc)

        def home(name: str, site: str, stamp: str) -> str:
            offset, length = pages[name]
            return cdx_line(ns.surt_prefix(site) + ")/", stamp, f"https://{site}/", filename=warc_name, offset=str(offset), length=str(length))

        lines = {
            PRE: [cdx_line("com,oldname)/", "20251005101010", "https://oldname.com/")],
            POST1: [
                home("bakery", "freshbakery.com", "20251210120000"),
                cdx_line("com,freshbakery)/cakes", "20251210120500", "https://freshbakery.com/cakes"),
                home("died", "launchdied.com", "20251211120000"),
                home("parked", "parkedbox.com", "20251212120000"),
                home("rebrand", "northwindstudio.com", "20251213120000"),
                cdx_line("com,oldname)/", "20251214101010", "https://oldname.com/"),
                cdx_line("com,aliasdomain)/", "20251215101010", "https://aliasdomain.com/", status="301", redirect="https://bigbrand.org/"),
            ],
            POST2: [cdx_line("com,freshbakery)/", "20260210120000", "https://freshbakery.com/")],
        }
        for crawl, rows in lines.items():
            rows = sorted(rows)
            filler_low = [cdx_line(f"aaa,filler{i:03d})/", "20251201000000", f"https://filler{i}.aaa/") for i in range(3)]
            filler_high = [cdx_line(f"zzz,filler{i:03d})/", "20251201000000", f"https://filler{i}.zzz/") for i in range(3)]
            blocks = [filler_low, rows[: len(rows) // 2], rows[len(rows) // 2 :], filler_high]
            blob = bytearray()
            cluster = []
            for number, block in enumerate(b for b in blocks if b):
                data = gzip.compress(("\n".join(block) + "\n").encode(), mtime=0)
                key = " ".join(block[0].split(" ")[:2])
                cluster.append(f"{key}\tcdx-00000.gz\t{len(blob)}\t{len(data)}\t{number + 1}")
                blob.extend(data)
            base = f"/cc-index/collections/{crawl}/indexes"
            self.files[f"{base}/cdx-00000.gz"] = bytes(blob)
            self.files[f"{base}/cluster.idx"] = ("\n".join(cluster) + "\n").encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        host = request.url.host
        if host == "rdap.verisign.com":
            site = request.url.path.rsplit("/", 1)[1]
            status, body = self.rdap.get(site, (404, {"errorCode": 404, "title": "Not Found"}))
            return httpx.Response(status, json=body)
        if host == "dns.google":
            site = request.url.params["name"]
            return httpx.Response(200, json=self.dns.get(site, json.loads(fixture("doh_nxdomain.json"))))
        if host == "data.commoncrawl.org":
            data = self.files.get(request.url.path)
            if data is None:
                return httpx.Response(404)
            spec = request.headers.get("range", "")
            if spec.startswith("bytes="):
                start, end = (int(x) for x in spec[6:].split("-"))
                end = min(end, len(data) - 1)
                return httpx.Response(206, content=data[start : end + 1], headers={"content-range": f"bytes {start}-{end}/{len(data)}"})
            return httpx.Response(200, content=data)
        raise AssertionError(f"unexpected host {host}")


def rdap_answer(registration: str, nameservers: tuple[str, ...] = ("alice.ns.cloudflare.com",)) -> dict[str, Any]:
    body = json.loads(fixture("rdap_found.json"))
    body["events"][0]["eventDate"] = registration
    body["nameservers"] = [{"objectClassName": "nameserver", "ldhName": ns_name.upper()} for ns_name in nameservers]
    return body


def cohort_world() -> tuple[FakeWeb, list[ns.Registration]]:
    web = FakeWeb()
    listed = date(2025, 10, 27)
    ok_dns = json.loads(fixture("doh_ok.json"))
    web.rdap = {
        "freshbakery.com": (200, rdap_answer("2025-10-24T10:11:12Z")),
        "launchdied.com": (200, rdap_answer("2025-10-25T10:00:00Z", ("ns1.sedoparking.com", "ns2.sedoparking.com"))),
        "parkedbox.com": (200, rdap_answer("2025-10-25T10:00:00Z", ("ns1.bodis.com",))),
        "northwindstudio.com": (200, rdap_answer("2025-10-26T10:00:00Z")),
        "oldname.com": (200, rdap_answer("2025-10-26T10:00:00Z")),
        "aliasdomain.com": (200, rdap_answer("2025-10-26T10:00:00Z")),
        "relisted.com": (200, rdap_answer("2026-03-01T00:00:00Z")),
        "ancient.com": (200, rdap_answer("2019-05-05T00:00:00Z")),
    }
    web.dns = {site: ok_dns for site in ("freshbakery.com", "parkedbox.com", "northwindstudio.com", "quietlive.com")}
    sites = ["freshbakery.com", "launchdied.com", "parkedbox.com", "northwindstudio.com", "oldname.com", "aliasdomain.com",
             "deadsite.com", "quietlive.com", "relisted.com", "ancient.com"]
    return web, [ns.Registration(site, listed, "test/nrd", "com") for site in sites]


def run_cohort(tmp_path: Path, web: FakeWeb, regs: list[ns.Registration]) -> ns.CohortResult:
    http = httpx.Client(transport=httpx.MockTransport(web.handler))
    return ns.cohort(regs, http=http, plan=PLAN, crawls=CRAWLS, cache_root=tmp_path / "cache", public_root=tmp_path / "public",
                     sleep=NO_SLEEP, now=lambda: datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc))


def test_cohort_keeps_dead_sites_flags_and_funnel(tmp_path):
    web, regs = cohort_world()
    result = run_cohort(tmp_path, web, regs)
    rows = {row.site: row for row in result.rows}
    assert len(rows) == 10, "every registration stays in the table"

    bakery = rows["freshbakery.com"]
    assert bakery.kept and bakery.content_evidence and not bakery.parked
    assert bakery.registered_on == date(2025, 10, 24) and bakery.registered_on_source == "rdap"
    assert bakery.launch_date == date(2025, 12, 10) and bakery.launch_date_source == f"cc_first_content_capture:{POST1}"
    assert bakery.launch_uncertainty_days == (date(2025, 12, 10) - date(2025, 10, 24)).days
    assert bakery.cc_pages_first_content_crawl == 2 and bakery.cc_first_seen == date(2025, 12, 10)
    assert bakery.home_title == "Fresh Bakery | Sourdough bread and cakes in Austin"
    assert bakery.cc_pre_registration_captures == 0

    dead = rows["deadsite.com"]
    assert dead.no_content_evidence and dead.not_registered_now and not dead.kept
    assert dead.rdap_status == "not_found" and dead.dns_resolves is False and dead.launch_date is None

    died = rows["launchdied.com"]
    assert died.kept and died.parked_now and not died.parked, "a site that launched and later parked is still kept (survivorship)"

    parked = rows["parkedbox.com"]
    assert parked.parked and parked.parked_now and not parked.kept and parked.no_content_evidence

    assert rows["oldname.com"].possibly_re_registered and rows["oldname.com"].cc_pre_registration_captures == 1
    assert not rows["oldname.com"].kept
    assert rows["northwindstudio.com"].possible_rebrand and not rows["northwindstudio.com"].kept
    assert rows["aliasdomain.com"].redirects_offsite and not rows["aliasdomain.com"].kept
    assert rows["relisted.com"].re_registered_after_listing and rows["relisted.com"].registered_on == date(2025, 10, 27)
    assert rows["ancient.com"].registration_older_than_list
    quiet = rows["quietlive.com"]
    assert quiet.resolvable_or_crawled and quiet.no_content_evidence and not quiet.kept

    funnel = result.funnel
    assert (funnel.registered, funnel.resolvable_or_crawled, funnel.with_content_evidence, funnel.kept) == (10, 7, 4, 2)
    assert funnel.flags["parked"] == 1 and funnel.flags["possibly_re_registered"] == 1 and funnel.flags["possible_rebrand"] == 1
    assert funnel.lines()[0] == "Registered: 10."

    # Launch-era page text is scrubbed and dated by the capture day.
    page = result.pages["freshbakery.com"]
    assert "hello@freshbakery.com" not in page.text and "555-0147" not in page.text and "[email]" in page.text
    assert "tracking" not in page.text
    dated = result.page_texts()["freshbakery.com"][0]
    assert dated.snapshot_date == date(2025, 12, 10) and "Sourdough bread baked every morning" in dated.value

    # Personal data: contacts in the RDAP answer are never stored.
    stored = (tmp_path / "cache" / "rdap.jsonl").read_text(encoding="utf-8")
    assert "jane" not in stored.lower() and "5555550100" not in stored and "vcard" not in stored.lower()

    # Resumable: a second run sends nothing and gives the same table.
    count = len(web.requests)
    again = ns.cohort(regs, http=httpx.Client(transport=httpx.MockTransport(refuse_all)), plan=PLAN, crawls=CRAWLS,
                      cache_root=tmp_path / "cache", public_root=tmp_path / "public", sleep=NO_SLEEP)
    assert again.rows == result.rows and count == len(web.requests)

    paths = ns.write_cohort(result, tmp_path / "out", "test")
    frame = pd.read_parquet(paths["cohort"])
    assert tuple(frame.columns) == ns.COHORT_COLUMNS and len(frame) == 10
    assert ns.load_pages(Path(paths["pages"]))["freshbakery.com"] == page


def test_cluster_slice_binary_search_reads_only_a_slice(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "CLUSTER_WINDOW", 256)
    monkeypatch.setattr(ns, "CLUSTER_MARGIN", 512)
    keys = [f"com,site{i:05d})/ 20251201000000\tcdx-00000.gz\t{i * 10}\t10\t{i}" for i in range(2000)]
    data = ("\n".join(keys) + "\n").encode()
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start, end = (int(x) for x in request.headers["range"][6:].split("-"))
        end = min(end, len(data) - 1)
        sent.append(request.headers["range"])
        return httpx.Response(206, content=data[start : end + 1], headers={"content-range": f"bytes {start}-{end}/{len(data)}"})

    index = ns.CommonCrawlIndex(httpx.Client(transport=httpx.MockTransport(handler)), public_root=tmp_path, cache_root=tmp_path,
                                sleep=NO_SLEEP)
    lines = index.cluster_slice("CC-MAIN-X", "com,site01000)", "com,site01003-")
    got = [line.key.split(")")[0] for line in lines]
    assert "com,site00999" in got and "com,site01003" in got and "com,site01004" in got
    assert index.bytes_downloaded < len(data) / 4


def test_rdap_and_dns_errors_are_missing_not_false(tmp_path):
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    http = httpx.Client(transport=httpx.MockTransport(broken))
    rdap = ns.RdapLookup(http, tmp_path / "r.jsonl", sleep=NO_SLEEP)
    record = rdap.lookup("example.com")
    assert record.status == "error" and record.registration is None
    assert "example.com" not in rdap.cache, "an error is not cached, so the next run asks again"
    dns = ns.DnsLookup(http, tmp_path / "d.jsonl", sleep=NO_SLEEP).lookup("example.com")
    assert dns.resolves is None
    row = ns.assess(ns.Registration("example.com", date(2025, 10, 27), "t", "com"), rdap=record, dns=dns, crawls=CRAWLS, plan=PLAN,
                    cdx={}, pages={})
    assert row.ns_parking is None and row.dns_resolves is None and not row.not_registered_now


# ----------------------------------------------------------------------------------------------
# The search universe.
# ----------------------------------------------------------------------------------------------

KAGGLE_SCHEMA = pa.schema([("id", pa.int64()), ("palavra", pa.string()), ("mercado", pa.string()), ("fonte", pa.string()),
                           ("volume", pa.int64()), ("concorrencia", pa.float64()), ("cpc", pa.float64()), ("id_padrao", pa.int64()),
                           ("num_palavras", pa.int64()), ("adulto", pa.bool_())])


def kaggle_shard(path: Path, *, letters: str = "abcdefghijklmnopqrstuvwxyz", reverse: bool = False) -> Path:
    words = [
        ("plumber near me", 0.10), ("plumbers near me", 0.12), ("emergency plumber", 0.30), ("cheap plumbing repair", 0.50),
        ("water heater repair", 0.20), ("water heater installation", 0.80), ("drain cleaning service", 0.60),
        ("clogged drain fix", 0.05), ("sewer line replacement", 0.95), ("plumbing supplies", 0.40), ("home depot plumbing", 0.70),
        ("plumber login", 0.01), ("www plumbing com", 0.02), ("roto rooter plumbing prices", 0.90), ("tankless water heater cost", 0.85),
        ("leaky faucet repair", 0.15), ("toilet repair cost", 0.45), ("adult plumber video", 0.33),
    ]
    rows = [{"id": i, "palavra": w, "mercado": "US", "fonte": "dataforseo", "volume": 100 + i, "concorrencia": c, "cpc": 1.0,
             "id_padrao": 1, "num_palavras": len(w.split()), "adulto": w.startswith("adult")} for i, (w, c) in enumerate(words)]
    rows += [{"id": 1000 + i, "palavra": f"{ch}filler topic", "mercado": "US", "fonte": "dataforseo", "volume": 10, "concorrencia": 0.5,
              "cpc": 0.1, "id_padrao": 2, "num_palavras": 2, "adulto": False} for i, ch in enumerate(letters)]
    rows.append({"id": 5000, "palavra": "encanador perto de mim", "mercado": "BR", "fonte": "dataforseo", "volume": 10,
                 "concorrencia": 0.5, "cpc": 0.1, "id_padrao": 3, "num_palavras": 4, "adulto": False})
    if reverse:
        rows = rows[::-1]
    pq.write_table(pa.Table.from_pylist(rows, schema=KAGGLE_SCHEMA), path, row_group_size=7)
    return path


def test_universe_is_deterministic_stratified_and_drops_brand_and_navigational(tmp_path):
    shard = kaggle_shard(tmp_path / "a.parquet")
    dfs_file = FIX / "dfs_keywords_US_en.json"
    seeds = ["plumber", "plumbing", "water heater", "drain", "sewer line", "faucet", "toilet"]
    one = uv.build_universe(seeds, country="US", n=6, niche="example", kaggle_files=[shard], dfs_keyword_files=[dfs_file])
    two = uv.build_universe(seeds, country="US", n=6, niche="example", kaggle_files=[kaggle_shard(tmp_path / "b.parquet", reverse=True)],
                            dfs_keyword_files=[dfs_file])
    assert one.sha256 == two.sha256 and [r.keyword for r in one.rows] == [r.keyword for r in two.rows]
    assert one.counts["selected_by_stratum"] == {"easy": 2, "medium": 2, "hard": 2}
    assert one.counts["dropped"] == {"adult": 1, "brand": 2, "navigational_intent": 1, "navigational_phrase": 1}
    kept = {r.keyword for r in one.candidates}
    assert "home depot plumbing" not in kept and "roto rooter plumbing prices" not in kept and "plumber login" not in kept
    assert "www plumbing com" not in kept and "adult plumber video" not in kept
    assert "plumber near me" in kept and "plumbers near me" in kept
    emergency = next(r for r in one.candidates if r.keyword == "emergency plumber")
    assert emergency.kd == 41.0 and emergency.difficulty_proxy_source == "kd" and emergency.sources == "dfs_keywords+kaggle_google_keywords"
    by_source = {}
    for row in one.candidates:
        by_source.setdefault(row.difficulty_proxy_source, []).append(row)
    for members in by_source.values():
        ordered = sorted(members, key=lambda r: (r.difficulty_proxy, r.keyword_id))
        strata = [r.stratum for r in ordered]
        assert strata == sorted(strata, key=uv.STRATA.index), "thirds are cut inside each proxy source"
    three = uv.build_universe(seeds, country="US", n=6, niche="example", kaggle_files=[shard], dfs_keyword_files=[dfs_file], seed=7)
    assert three.sha256 != one.sha256
    paths = uv.write_universe(one, tmp_path / "out")
    assert [r.keyword for r in uv.load_universe(paths["table"])] == [r.keyword for r in one.rows]
    meta = json.loads(Path(paths["meta"]).read_text())
    assert meta["sha256"] == one.sha256 and meta["rule_hash"] == one.rule_hash


def test_universe_refuses_a_shard_that_misses_part_of_the_alphabet(tmp_path):
    shard = kaggle_shard(tmp_path / "half.parquet", letters="abcdefghijklm")
    with pytest.raises(ValueError, match="only part of the alphabet"):
        uv.build_universe(["plumber"], country="US", n=3, niche="x", kaggle_files=[shard])
    partial = uv.build_universe(["plumber"], country="US", n=3, niche="x", kaggle_files=[shard], allow_partial_coverage=True)
    assert partial.counts["kaggle_missing_letters"] == "noquvxyz"


def test_universe_rule_hash_changes_with_the_rule():
    assert uv.DEFAULT_RULE.rule_hash() == uv.UniverseRule().rule_hash()
    assert uv.DEFAULT_RULE.rule_hash(["acme"]) != uv.DEFAULT_RULE.rule_hash()
    assert uv.UniverseRule(min_volume=10).rule_hash() != uv.DEFAULT_RULE.rule_hash()


def test_kaggle_download_refuses_the_whole_bundle(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://storage.example/bundle/archive.zip?sig=1"})

    with pytest.raises(ConnectionError, match="bundle"):
        uv.download_kaggle_shard("palavras_chave-0011.parquet", http=httpx.Client(transport=httpx.MockTransport(handler)), root=tmp_path)
    with pytest.raises(ValueError):
        uv.download_kaggle_shard("", http=httpx.Client(transport=httpx.MockTransport(handler)), root=tmp_path)


# ----------------------------------------------------------------------------------------------
# The cost table.
# ----------------------------------------------------------------------------------------------


def test_plan_pulls_uses_budget_prices_and_flags_the_30_month_need():
    plan = ts.plan_pulls(5000, 20000, rows_per_site=100)
    lines = {(line.window_months, line.item.split(",")[1].strip() if "," in line.item else line.item): line.usd for line in plan.lines}
    assert lines[(24, "one page per month (the plan's reading)")] == budget.historical_serps_cost(5000, 24) == Decimal("14.40000")
    assert lines[(30, "one page per month (the plan's reading)")] == Decimal("18.00000")
    assert lines[(None, "Traffic curves")] == budget.historical_traffic_cost(20000) == Decimal("26.40000")
    assert lines[(None, "Exact positions")] == budget.ranked_keywords_cost(20000, 100)
    assert ts.plan_pulls(10, 2000).lines[-2].usd == budget.ranked_keywords_cost(2000, 100) == Decimal("48.00000")
    plan_scale = ts.plan_pulls(5000, 20000, position_sites=2000)
    assert [l.usd for l in plan_scale.lines if l.item == "Exact positions"] == [Decimal("48.00000")]
    assert plan_scale.totals_plan_reading[24] == Decimal("14.40000") + Decimal("26.40000") + Decimal("48.00000") + Decimal(3)
    today = date(2026, 11, 22)
    rows = [{"site": "a.com", "launch_date": date(2024, 9, 15)}, {"site": "b.com", "launch_date": date(2025, 6, 1)},
            {"site": "c.com", "launch_date": None, "registered_on": date(2024, 3, 1)}, {"site": "d.com", "launch_date": None}]
    dated = ts.plan_pulls(100, rows, today=today)
    assert dated.sites == 4 and dated.sites_with_age == 3
    assert dated.sites_24_to_30_months == 1 and dated.sites_over_30_months == 1
    assert dated.sites_without_launch_page == {24: 2, 30: 1}
    assert "need the 30-month window" in dated.text()


# ----------------------------------------------------------------------------------------------
# Paid pulls: dry runs, the budget guard, resume without paying twice.
# ----------------------------------------------------------------------------------------------


def make_client(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], *, approve: str | None) -> DataForSeoClient:
    clock = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
    ledger = SpendLedger(tmp_path / "spend.jsonl", now=lambda: clock)
    if approve:
        ledger.approve(Decimal(approve), by="James", note="test")
    return DataForSeoClient("test-login", "test-password", client=httpx.Client(transport=httpx.MockTransport(handler)), ledger=ledger,
                            raw_dir=tmp_path / "raw", sleep=NO_SLEEP, now=lambda: clock)


def labs(log: list[dict[str, Any]], answers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        function = request.url.path.split("/")[-2]
        task = json.loads(request.content)[0]
        log.append({"function": function, "task": task})
        return httpx.Response(200, json=answers[function](task))

    return handler


def serps_answer(task: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dfs_fixture("dfs_historical_serps.json"))
    result = body["tasks"][0]["result"][0]
    result["keyword"] = task["keyword"]
    for snap in result["items"]:
        snap["keyword"] = task["keyword"]
    return body


def test_every_paid_function_is_a_dry_run_by_default(tmp_path):
    client = make_client(tmp_path, refuse_all, approve=None)
    before = client.ledger.path.exists()
    reports = [
        ts.pull_past_result_pages(client, ["plumber near me", "emergency plumber"], months=30, root=tmp_path),
        ts.pull_traffic_curves(client, ["a.com", "b.com"], root=tmp_path),
        ts.pull_exact_positions(client, ["a.com"], root=tmp_path),
    ]
    assert all(report.dry_run and report.calls == 0 and report.spent_usd == 0 for report in reports)
    assert reports[0].planned_usd == budget.historical_serps_estimate(30, 2)
    assert reports[1].planned_usd == budget.historical_traffic_cost(2)
    assert reports[2].planned_usd == budget.ranked_keywords_cost(1, 100)
    samples = ts.run_samples(client, small_sites=["a.com", "b.com"], keywords=["plumber near me"])
    assert samples.dry_run and samples.json_path is None
    assert all(answer.planned_usd <= budget.SAMPLE_MAX_USD for answer in samples.answers)
    assert client.ledger.path.exists() == before and not (tmp_path / "jobs").exists()
    assert "Nothing was sent" in reports[0].lines()[0]


def test_execute_without_approval_is_refused_and_sends_nothing(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve=None)
    with pytest.raises(BudgetError):
        ts.pull_past_result_pages(client, ["plumber near me"], execute=True, root=tmp_path)
    with pytest.raises(BudgetError):
        ts.run_samples(client, small_sites=["a.com"], keywords=["k"], execute=True, out_path=tmp_path / "s.json")
    with pytest.raises(BudgetError, match="spend ledger"):
        ts.pull_traffic_curves(None, ["a.com"], execute=True, root=tmp_path)
    assert log == []


def test_past_result_pages_saved_raw_resumed_without_paying_twice_and_in_the_panel(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve="5.00")
    first = ts.pull_past_result_pages(client, ["plumber near me", "emergency plumber"], months=24, execute=True, root=tmp_path,
                                      panel_dir=tmp_path / "panel", today=date(2026, 11, 22), job="serps-test")
    assert first.calls == 2 and first.calls_from_cache == 0 and len(log) == 2
    # 24 calendar months, the month of today included: 2024-12 .. 2026-11 (priced as 24 pages per search).
    assert log[0]["task"]["date_from"] == "2024-12-01" and log[0]["task"]["date_to"] == "2026-11-22"
    assert first.planned_usd == budget.historical_serps_estimate(24, 2)
    assert all(Path(p).is_file() for p in first.raw_paths)
    assert first.spent_usd == Decimal("0.001000")  # the provider's own cost field, twice
    spent = client.ledger.spent_total
    panel = load_panel(["dataset", "keyword", "rank", "site"], panel_dir=tmp_path / "panel")
    assert set(panel["keyword"]) == {"plumber near me", "emergency plumber"} and set(panel["dataset"]) == {"dfs_labs_historical"}
    assert first.rows == len(panel) == 2

    # A later run on another day reuses the frozen dates, reads the raw answers and pays nothing.
    again = ts.pull_past_result_pages(client, ["plumber near me", "emergency plumber"], months=24, execute=True, root=tmp_path,
                                      panel_dir=tmp_path / "panel", today=date(2026, 12, 5), job="serps-test")
    assert again.calls_from_cache == 2 and len(log) == 2 and client.ledger.spent_total == spent and again.spent_usd == 0
    assert again.manifest["date_from"] == "2024-12-01"


def test_traffic_curves_keep_missing_sites_missing(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json")}),
                         approve="5.00")
    report = ts.pull_traffic_curves(client, ["tinynew.com", "cnn.com"], execute=True, root=tmp_path, today=date(2026, 11, 22), job="t")
    # 30 calendar months, the month of today included: 2024-06 .. 2026-11, the same window as past result pages.
    assert log[0]["task"]["targets"] == ["cnn.com", "tinynew.com"] and log[0]["task"]["date_from"] == "2024-06-01"
    months = pd.read_parquet(report.outputs["traffic_months"])
    targets = pd.read_parquet(report.outputs["traffic_targets"]).set_index("site")
    assert tuple(months.columns) == tuple(ts.TRAFFIC_MONTH_COLUMNS) and tuple(targets.reset_index().columns) == tuple(ts.TRAFFIC_TARGET_COLUMNS)
    assert bool(targets.loc["tinynew.com", "returned"]) is False and targets.loc["tinynew.com", "months_returned"] == 0
    assert pd.isna(targets.loc["tinynew.com", "first_month_with_traffic"])
    assert bool(targets.loc["cnn.com", "returned"]) and targets.loc["cnn.com", "months_with_traffic"] == 3
    assert set(months["site"]) == {"cnn.com"} and (months["label"] == "REAL").all()
    again = ts.pull_traffic_curves(client, ["cnn.com", "tinynew.com"], execute=True, root=tmp_path, job="t")
    assert again.calls_from_cache == 1 and len(log) == 1


def test_exact_positions_tables(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"ranked_keywords": lambda task: dfs_fixture("dfs_ranked_keywords.json")}), approve="5.00")
    report = ts.pull_exact_positions(client, ["dataforseo.com"], rows_per_site=10, execute=True, root=tmp_path, job="p")
    positions = pd.read_parquet(report.outputs["positions"])
    assert tuple(positions.columns) == tuple(ts.POSITION_COLUMNS) and len(positions) == report.rows > 0
    assert positions["keyword_id"].str.len().eq(16).all() and (positions["locale"] == "US_en").all()
    targets = pd.read_parquet(report.outputs["position_targets"])
    assert targets.loc[0, "total_count"] == 3696 and log[0]["task"]["limit"] == 10


def test_samples_record_findings_and_answer_the_plan_questions(tmp_path):
    log: list[dict[str, Any]] = []
    answers = {
        "historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json"),
        "historical_serps": serps_answer,
        "ranked_keywords": lambda task: dfs_fixture("dfs_ranked_keywords.json"),
    }
    client = make_client(tmp_path, labs(log, answers), approve="5.00")
    out = tmp_path / "samples.json"
    report = ts.run_samples(client, small_sites=["tinynew.com", "cnn.com", "other.com"], keywords=["plumber near me"],
                            ranked_targets=["dataforseo.com"], execute=True, out_path=out, today=date(2026, 11, 22))
    assert not report.dry_run and json.loads(out.read_text())["answers"][0]["kind"] == budget.KIND_HISTORICAL_TRAFFIC
    traffic, serps, ranked = report.answers
    assert traffic.findings["sites_with_data"] == 1 and "1 of 3 small new sites" in traffic.answer and "Most show nothing" in traffic.answer
    assert serps.findings["earliest_snapshot"] == "2021-08-01" and "more than the 12 months" in serps.answer and "30 months are covered" in serps.answer
    assert log[1]["task"]["date_from"] == "2024-05-01", "the history sample asks for 30 months"
    assert "1 of 1 sites" in ranked.answer
    assert all(client.ledger.sample_recorded(kind) for kind in (budget.KIND_HISTORICAL_TRAFFIC, budget.KIND_HISTORICAL_SERPS, budget.KIND_RANKED_KEYWORDS))
    assert all(client.ledger.spent_for_kind(kind) <= budget.SAMPLE_MAX_USD for kind in (budget.KIND_HISTORICAL_TRAFFIC, budget.KIND_HISTORICAL_SERPS))
    calls = len(log)
    again = ts.run_samples(client, small_sites=["tinynew.com"], keywords=["k"], execute=True, out_path=out)
    assert len(log) == calls and all(answer.from_ledger for answer in again.answers)


# ----------------------------------------------------------------------------------------------
# Targets and the split.
# ----------------------------------------------------------------------------------------------


def universe_rows(words: list[str], niche: str = "plumbing") -> list[uv.SearchRow]:
    from aeo.ranksim.forecast.sources.dfs_samples import keyword_id

    return [uv.SearchRow(keyword=w, keyword_id=keyword_id("US_en", w), locale="US_en", country="US", niche=niche, seed_term="",
                         search_volume=None, competition=None, cpc=None, kd=None, intent=None, sources="test", difficulty_proxy=None,
                         difficulty_proxy_source=None, stratum=None) for w in words]


def test_select_targets_refuses_text_after_the_launch_month_and_keeps_never_ranking_searches():
    words = ["emergency plumber", "water heater repair", "drain cleaning", "sewer line replacement", "roof repair", "cake shop"]
    universe = universe_rows(words)
    cohort = [{"site": "newplumb.com", "launch_date": date(2025, 12, 10), "niche": "plumbing"}]
    end_of_month = {"newplumb.com": [Dated("Emergency plumber and water heater repair in Austin", "cc", date(2025, 12, 31))]}
    rule = ts.TargetRule(k=2, threshold=0.5, controls_per_site=2)
    picked = ts.select_targets(cohort, universe, end_of_month, ts.token_overlap_score, rule=rule)
    matches = [r.keyword for r in picked.rows if r.selection == "match"]
    controls = [r.keyword for r in picked.rows if r.selection == "control"]
    assert matches == ["emergency plumber", "water heater repair"] and len(controls) == 2 and not set(controls) & set(matches)
    assert all(r.as_of == date(2025, 12, 31) for r in picked.rows)
    # The rule takes no rankings at all: searches where the site never ranks are chosen like any other.
    import inspect

    assert set(inspect.signature(ts.select_targets).parameters) == {"cohort", "universe", "page_text", "score_fn", "rule"}
    later = {"newplumb.com": [Dated("Emergency plumber", "cc", date(2026, 1, 1))]}
    with pytest.raises(LeakError):
        ts.select_targets(cohort, universe, later, ts.token_overlap_score, rule=rule)
    with pytest.raises(LeakError):
        ts.select_targets(cohort, universe, {"newplumb.com": [Dated("Emergency plumber", "undated list", None)]}, ts.token_overlap_score)
    no_launch = ts.select_targets([{"site": "x.com", "launch_date": None}], universe, {}, ts.token_overlap_score)
    assert no_launch.rows == [] and no_launch.sites_without_launch_date == ["x.com"]
    # The hash names the scorer and the rule values.
    other = ts.select_targets(cohort, universe, end_of_month, lambda s, t: 1.0, rule=rule)
    assert other.rule_hash != picked.rule_hash
    assert ts.select_targets(cohort, universe, end_of_month, ts.token_overlap_score, rule=rule).rule_hash == picked.rule_hash


def test_split_has_no_site_and_no_search_group_on_both_sides():
    words = ["plumber near me", "plumbers near me", "emergency plumber", "water heater repair", "drain cleaning", "sewer line",
             "toilet repair", "leaky faucet", "garbage disposal", "sump pump"]
    universe = universe_rows(words)
    cohort, pages = [], {}
    for index in range(10):
        site = f"site{index}.com"
        launch = date(2025, 1 + index, 15)
        cohort.append({"site": site, "launch_date": launch, "launch_date_source": "test", "launch_uncertainty_days": index})
        chosen = [words[(index + j) % len(words)] for j in range(3)]
        pages[site] = [Dated(" ".join(chosen), "cc", launch)]
    targets = ts.select_targets(cohort, universe, pages, ts.token_overlap_score, rule=ts.TargetRule(k=3, threshold=1.0, controls_per_site=1))
    cells = ts.cohort_cells(cohort, targets)
    assert tuple(cells.columns) == ts.CELL_COLUMNS
    assert ts.check_split(cells) == {"sites_on_both_sides": 0, "groups_on_both_sides": 0}
    assert set(cells["split"]) <= set(ts.SPLITS) and (cells["split"] == "test").any() and (cells["split"] == "train").any()
    test_sites = set(cells.loc[cells["split"] == "test", "site"])
    assert test_sites == {"site8.com", "site9.com"}, "the latest launch cohort is held out"
    assert ts.search_group("plumbers near me") == ts.search_group("Plumber near me")
    assert (cells["target_rule_hash"] == targets.rule_hash).all() and cells.attrs["split_rule_hash"] == ts.DEFAULT_SPLIT_RULE.rule_hash()
    assert cells.loc[cells["site"] == "site3.com", "launch_uncertainty_days"].iloc[0] == 3
    # The boundary month is split by site, never by cell.
    same_month = [{"site": f"m{i}.com", "keyword": f"kw{i}", "launch_date": date(2025, 5, 1)} for i in range(10)]
    labels = ts.assign_split(same_month)
    assert labels.count("test") == 2 and labels.count("train") == 8
    assert ts.assign_split([{"site": "a.com", "keyword": "k", "launch_date": None}]) == ["excluded"]
    assert ts.SplitRule(test_share=0.3).rule_hash() != ts.DEFAULT_SPLIT_RULE.rule_hash()


# ----------------------------------------------------------------------------------------------
# The synthetic end-to-end path.
# ----------------------------------------------------------------------------------------------


def test_synthetic_path_builds_every_table(tmp_path):
    world = World(seed=3, niches=2, searches_per_niche=12, established_sites=60, new_sites=12, months=6, start_month=date(2025, 1, 1))
    result = ts.synthetic_training_set(world, out_dir=tmp_path / "syn")
    assert result.label == "SYNTHETIC"
    for key in ("cohort", "cells", "targets", "traffic_months", "traffic_targets", "positions", "position_targets", "meta"):
        assert Path(result.paths[key]).exists(), key
    assert tuple(result.cells.columns) == ts.CELL_COLUMNS and len(result.cells) > 0
    assert ts.check_split(result.cells) == {"sites_on_both_sides": 0, "groups_on_both_sides": 0}
    assert set(result.cells["launch_date_source"]) == {"synthetic_truth"}
    assert result.panel_rows > 0 and len(load_panel(["keyword", "site"], panel_dir=Path(result.paths["panel"]))) == result.panel_rows
    assert (result.traffic_months["label"] == "SYNTHETIC").all() and (result.positions["label"] == "SYNTHETIC").all()
    assert (pd.read_parquet(result.paths["cohort"])["label"] == "SYNTHETIC").all()
    dead = [site for site, info in world.new_sites.items() if not info.alive]
    cohort = result.cohort.set_index("site")
    assert all(bool(cohort.loc[site, "no_content_evidence"]) for site in dead), "dead sites stay in the cohort"
    assert len(cohort) > len(world.new_sites), "decoy registrations stay in too"
    assert json.loads(Path(result.paths["meta"]).read_text())["label"] == "SYNTHETIC"
    again = ts.synthetic_training_set(world, out_dir=tmp_path / "syn2")
    pd.testing.assert_frame_equal(again.cells, result.cells)


# ----------------------------------------------------------------------------------------------
# Regressions found in review.
# ----------------------------------------------------------------------------------------------


def test_missing_dates_from_parquet_stay_missing():
    """pd.NaT is a datetime subclass. A NaT launch date once came back as a date-like NaT. It must be None."""
    import numpy as np

    assert ts._as_date(pd.NaT) is None and ts._as_date(float("nan")) is None and ts._as_date(None) is None
    assert ts._as_date(np.datetime64("2025-01-02")) == date(2025, 1, 2)
    frame = pd.DataFrame({"site": ["a.com"], "launch_date": [pd.NaT]})
    picked = ts.select_targets(frame.to_dict("records"), universe_rows(["k"]), {}, ts.token_overlap_score)
    assert picked.sites_without_launch_date == ["a.com"] and picked.rows == []


def test_a_site_never_gets_another_niches_searches():
    universe = universe_rows(["emergency plumber"], niche="plumbing")
    cohort = [{"site": "dent.com", "launch_date": date(2025, 3, 3), "niche": "dentist"}]
    pages = {"dent.com": [Dated("emergency plumber", "cc", date(2025, 3, 3))]}
    picked = ts.select_targets(cohort, universe, pages, ts.token_overlap_score)
    assert picked.rows == [] and picked.sites_whose_niche_has_no_searches == ["dent.com"]


def test_a_resumed_lookup_reuses_a_covering_cluster_slice(tmp_path):
    web = FakeWeb()
    http = httpx.Client(transport=httpx.MockTransport(web.handler))
    index = ns.CommonCrawlIndex(http, public_root=tmp_path / "public", cache_root=tmp_path / "cache", sleep=NO_SLEEP)
    index.lookup(POST1, ["freshbakery.com", "parkedbox.com", "aliasdomain.com"])
    sent = len(web.requests)
    other = ns.CommonCrawlIndex(http, public_root=tmp_path / "public", cache_root=tmp_path / "cache2", sleep=NO_SLEEP)
    found = other.lookup(POST1, ["launchdied.com"])
    cluster_calls = [u for u in web.requests[sent:] if u.endswith("cluster.idx")]
    assert cluster_calls == [] and found["launchdied.com"].html_200 == 1


def test_a_bad_warc_record_is_missing_not_a_crash(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, content=b"\x1f\x8b\x08\x00not really gzip")

    fetcher = ns.PageFetcher(httpx.Client(transport=httpx.MockTransport(handler)), tmp_path / "p.jsonl", sleep=NO_SLEEP)
    summary = ns.CdxSummary(crawl=POST1, site="a.com", html_200=1,
                            home={"url": "https://a.com/", "captured_at": "2025-12-10T00:00:00Z", "filename": "f", "offset": 0, "length": 20})
    assert fetcher.fetch(summary) is None
    assert not fetcher.cache.path.exists(), "a failed fetch is not cached, so the next run asks again"


def test_a_capture_before_registration_inside_a_later_crawl_is_older_evidence():
    straddle = {"CC-X": ns.Crawl("CC-X", datetime(2025, 10, 20, tzinfo=timezone.utc), datetime(2025, 11, 3, tzinfo=timezone.utc))}
    summary = ns.CdxSummary(crawl="CC-X", site="a.com", captures=3, html_200=3, pages_200=3, first_capture="2025-10-21T00:00:00Z",
                            first_html_200="2025-10-21T00:00:00Z")
    row = ns.assess(ns.Registration("a.com", date(2025, 10, 27), "t", "com"), rdap=None, dns=None, crawls=straddle,
                    plan=ns.CrawlPlan((), ("CC-X",)), cdx={"CC-X": summary}, pages={})
    assert row.possibly_re_registered and not row.kept and row.launch_date is None


def test_seed_terms_with_spaces_match_any_run_of_spaces_and_plurals():
    """re.escape escapes spaces on this Python. The pattern must not depend on that."""
    import pyarrow.compute as pc

    pattern = uv.seed_pattern(["water heater", "plumber"])
    found = pc.match_substring_regex(pa.array(["water  heater repair", "Plumbers near me", "waterheater", "plumbery"]), pattern)
    assert found.to_pylist() == [True, True, False, False]
    assert uv._matched_seed("cheap water heaters", ["plumber", "water heater"]) == "water heater"


def test_tied_difficulty_values_share_one_third():
    rows = [{"difficulty_proxy": v, "difficulty_proxy_source": "competition", "keyword_id": f"{i:02d}", "stratum": None}
            for i, v in enumerate([0.1, 0.2, 0.3, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])]
    uv._terciles(rows)
    by_value = {}
    for row in rows:
        by_value.setdefault(row["difficulty_proxy"], set()).add(row["stratum"])
    assert by_value[1.0] == {"medium"} and all(len(v) == 1 for v in by_value.values())


# ----------------------------------------------------------------------------------------------
# Review fixes: SmartCrew however spelled, dry run = reservation, order-free resume, the 80/20 share.
# ----------------------------------------------------------------------------------------------


def test_training_set_pulls_samples_targets_and_cells_drop_smartcrew_however_it_is_spelled():
    spellings = ["www.smartcrewhq.com", "SmartCrewHQ.com", "https://smartcrewhq.com/", " smartcrewhq.com "]
    assert ts.pull_traffic_curves(None, spellings).units == 0
    assert ts.pull_exact_positions(None, spellings).units == 0
    assert all(answer.planned_units == 0 for answer in ts.run_samples(None, small_sites=spellings, keywords=[]).answers)
    ranked = ts.run_samples(None, small_sites=["a.com"], keywords=["plumber"], ranked_targets=spellings).answers[2]
    assert ranked.planned_units == 0
    picked = ts.select_targets([{"site": s, "launch_date": date(2025, 12, 1), "niche": "plumbing"} for s in spellings],
                               universe_rows(["emergency plumber"]),
                               {s: [Dated("emergency plumber", "cc", date(2025, 12, 2))] for s in spellings}, ts.token_overlap_score)
    assert picked.rows == []
    cells = [{"site": s, "keyword": "emergency plumber", "launch_date": date(2025, 12, 1)} for s in spellings]
    assert ts.assign_split(cells + [{"site": "a.com", "keyword": "k", "launch_date": date(2025, 1, 1)}])[:4] == ["excluded"] * 4


def test_one_site_spelled_several_ways_is_one_paid_target_and_normalised_input_keeps_its_job():
    mixed = ts.pull_traffic_curves(None, ["https://www.B.com/x", "b.com", "A.com", "www.a.com"])
    plain = ts.pull_traffic_curves(None, ["a.com", "b.com"])
    assert mixed.units == 2 and mixed.planned_usd == budget.historical_traffic_cost(2) and mixed.job == plain.job
    assert ts.pull_exact_positions(None, ["www.a.com", "a.com"]).units == 1
    assert ts.run_samples(None, small_sites=["www.a.com", "a.com", "b.com"], keywords=["k"]).answers[0].planned_units == 2
    picked = ts.select_targets([{"site": "www.newplumb.com", "launch_date": date(2025, 12, 10), "niche": "plumbing"},
                                {"site": "newplumb.com", "launch_date": date(2025, 12, 10), "niche": "plumbing"}],
                               universe_rows(["emergency plumber", "roof repair"]),
                               {"newplumb.com": [Dated("Emergency plumber", "cc", date(2025, 12, 31))]}, ts.token_overlap_score)
    assert {row.site for row in picked.rows} == {"newplumb.com"} and len(picked.rows) == 2


def test_past_pages_dry_run_price_equals_what_the_executed_pull_reserves(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve="5.00")
    dry = ts.pull_past_result_pages(client, ["plumber near me"], months=24, root=tmp_path, today=date(2026, 11, 22))
    assert dry.planned_usd == budget.historical_serps_estimate(24) and not (tmp_path / "jobs").exists()
    ts.pull_past_result_pages(client, ["plumber near me"], months=24, execute=True, root=tmp_path, panel_dir=tmp_path / "panel",
                              today=date(2026, 11, 22))
    rows = [json.loads(line)["row"] for line in client.ledger.path.read_text(encoding="utf-8").splitlines()]
    reserved = [Decimal(r["estimate_micros"]) / 1_000_000 for r in rows if r.get("event") == "reserve"]
    assert reserved == [dry.planned_usd]
    assert (log[0]["task"]["date_from"], log[0]["task"]["date_to"]) == ("2024-12-01", "2026-11-22")


def test_an_approval_of_exactly_the_printed_dry_run_cost_covers_the_executed_pull(tmp_path):
    log: list[dict[str, Any]] = []
    dry = ts.pull_past_result_pages(None, ["plumber near me"], months=24, root=tmp_path, today=date(2026, 11, 22))
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve=str(dry.planned_usd))
    report = ts.pull_past_result_pages(client, ["plumber near me"], months=24, execute=True, root=tmp_path,
                                       panel_dir=tmp_path / "panel", today=date(2026, 11, 22))
    assert report.calls == 1 and not report.failures and len(log) == 1


def test_plan_pulls_counts_a_launch_month_page_exactly_when_the_window_reaches_it():
    today = date(2026, 11, 22)
    start, end, covered = ts._history_window(today, 24)
    assert (start, end, covered) == (date(2024, 12, 1), today, 24)
    rows = [{"site": "inside.com", "launch_date": date(2024, 12, 15)}, {"site": "outside.com", "launch_date": date(2024, 11, 30)}]
    assert ts.plan_pulls(10, rows, today=today).sites_without_launch_page[24] == 1


def test_same_searches_in_another_order_on_a_later_day_resume_the_same_job_and_pay_nothing(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve="5.00")
    first = ts.pull_past_result_pages(client, ["plumber near me", "emergency plumber"], months=24, execute=True, root=tmp_path,
                                      panel_dir=tmp_path / "panel", today=date(2026, 11, 22))
    spent = client.ledger.spent_total
    again = ts.pull_past_result_pages(client, ["emergency plumber", "plumber near me", "plumber near me"], months=24, execute=True,
                                      root=tmp_path, panel_dir=tmp_path / "panel", today=date(2026, 12, 5))
    assert again.job == first.job and again.manifest["date_from"] == "2024-12-01"
    assert (len(log), again.calls_from_cache, client.ledger.spent_total, again.spent_usd) == (2, 2, spent, 0)
    # The dry run of a job that already has a manifest prices the frozen dates, not a fresh window.
    later = ts.pull_past_result_pages(None, ["plumber near me", "emergency plumber"], months=24, root=tmp_path, today=date(2027, 3, 1))
    assert later.job == first.job and later.planned_usd == budget.historical_serps_estimate(24, 2)


def test_split_test_share_stays_at_or_under_the_rule_share():
    six = [{"site": f"s{i}.com", "keyword": f"search {i}", "launch_date": date(2025, i + 1, 5)} for i in range(6)]
    labels = ts.assign_split(six)
    assert labels.count("test") == 1 and labels[-1] == "test", "floor(0.2 x 6) = 1 site, the latest launched"
    two = [{"site": f"t{i}.com", "keyword": f"other {i}", "launch_date": date(2025, i + 1, 5)} for i in range(2)]
    assert sorted(ts.assign_split(two)) == ["test", "train"], "two sites still give both sides one site"
    hundred = [{"site": f"h{i}.com", "keyword": f"word{i}", "launch_date": date(2025, 5, 1)} for i in range(100)]
    assert ts.assign_split(hundred, ts.SplitRule(test_share=0.29)).count("test") == 29  # no float round-down to 28
    assert ts.DEFAULT_SPLIT_RULE.version == "split-rule-v2"


# ----------------------------------------------------------------------------------------------
# Follow-ups of the data-layer bug hunt: the traffic window, saved-call counts, the batch check, the spam filter.
# ----------------------------------------------------------------------------------------------


def test_traffic_window_is_n_calendar_months_ending_this_month_like_past_pages(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json")}),
                         approve="5.00")
    for months, job in ((30, "w30"), (1, "w1"), (24, "w24")):
        ts.pull_traffic_curves(client, ["cnn.com"], months=months, execute=True, root=tmp_path, today=date(2026, 11, 22), job=job)
        start, end, covered = ts._history_window(date(2026, 11, 22), months)
        assert (log[-1]["task"]["date_from"], log[-1]["task"]["date_to"], covered) == (start.isoformat(), end.isoformat(), months)
    assert log[1]["task"]["date_from"] == "2026-11-01", "one month is the current month only"
    ts.pull_traffic_curves(client, ["cnn.com"], months=120, execute=True, root=tmp_path, today=date(2026, 11, 22), job="long")
    assert log[-1]["task"]["date_from"] == ts.TRAFFIC_MIN_DATE.isoformat(), "never before the endpoint's first month"


def test_the_saved_call_count_matches_the_clients_own_requests_for_every_pull(tmp_path):
    """The request layouts in trainingset copy the client's. After a run, the dry run must see every call saved."""
    log: list[dict[str, Any]] = []
    answers = {
        "historical_serps": serps_answer,
        "historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json"),
        "ranked_keywords": lambda task: dfs_fixture("dfs_ranked_keywords.json"),
    }
    client = make_client(tmp_path, labs(log, answers), approve="5.00")
    day = date(2026, 11, 22)
    runs = [
        (ts.pull_past_result_pages, ["plumber near me", "emergency plumber"], {"months": 24, "today": day, "panel_dir": tmp_path / "panel"}),
        (ts.pull_traffic_curves, ["cnn.com", "tinynew.com"], {"months": 30, "today": day}),
        (ts.pull_exact_positions, ["dataforseo.com", "other.com"], {"rows_per_site": 10}),
    ]
    for pull, inputs, options in runs:
        before = pull(None, inputs, root=tmp_path, raw_dir=tmp_path / "raw", **options)
        assert before.calls_saved == 0 and before.unpaid_usd == before.planned_usd
        pull(client, inputs, execute=True, root=tmp_path, **options)
        after = pull(None, inputs, root=tmp_path, raw_dir=tmp_path / "raw", **{k: v for k, v in options.items() if k != "panel_dir"})
        assert after.calls_saved == after.calls_planned > 0 and after.unpaid_usd == 0, pull.__name__
        assert "Still to pay: 0.000000 USD" in after.lines()[1]


def test_a_batch_the_approval_cannot_cover_is_refused_before_the_first_call(tmp_path):
    log: list[dict[str, Any]] = []
    one = budget.historical_serps_estimate(24)
    client = make_client(tmp_path, labs(log, {"historical_serps": serps_answer}), approve=str(one))
    with pytest.raises(BudgetError):
        ts.pull_past_result_pages(client, ["plumber near me", "emergency plumber"], months=24, execute=True, root=tmp_path,
                                  panel_dir=tmp_path / "panel", today=date(2026, 11, 22))
    assert log == [] and not (tmp_path / "jobs").exists(), "nothing was sent and no job was frozen"


def test_a_frozen_traffic_job_keeps_its_sites_and_dates_in_the_dry_run(tmp_path):
    log: list[dict[str, Any]] = []
    client = make_client(tmp_path, labs(log, {"historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json")}),
                         approve="5.00")
    first = ts.pull_traffic_curves(client, ["cnn.com", "tinynew.com"], execute=True, root=tmp_path, today=date(2026, 11, 22), job="frozen")
    later = ts.pull_traffic_curves(None, ["other.com"], root=tmp_path, today=date(2027, 3, 1), job="frozen", raw_dir=tmp_path / "raw")
    assert later.units == 2 and later.calls_saved == 1 and later.unpaid_usd == 0
    assert first.manifest["date_from"] == "2024-06-01"


def _kept_row(site: str, *, launch: date = date(2025, 12, 10), ns_parking: bool | None = None, checked: str | None = None) -> ns.CohortRow:
    return ns.CohortRow(
        site=site, registered_on=date(2025, 10, 24), registered_on_source="rdap", listed_on=date(2025, 10, 27), source="t", tld="com",
        rdap_status="found", rdap_registered_on=date(2025, 10, 24), nameservers=(), ns_parking=ns_parking, dns_resolves=True,
        current_state_checked_at=checked, cc_crawls_checked=(), cc_pre_registration_captures=0, cc_first_seen=launch,
        cc_first_content=launch, cc_pages_first_content_crawl=2, home_url=None, home_title=None, launch_date=launch,
        launch_date_source="cc_first_content_capture:X", launch_uncertainty_days=10, parked=False, parked_now=bool(ns_parking),
        possibly_re_registered=False, possible_rebrand=False, no_content_evidence=False, redirects_offsite=False,
        re_registered_after_listing=False, registration_older_than_list=False, not_registered_now=False, resolvable_or_crawled=True,
        content_evidence=True, kept=True,
    )


def _page(site: str, text: str) -> ns.PageText:
    return ns.PageText(site=site, crawl="X", url=f"https://{site}/", captured_at="2025-12-10T00:00:00Z", http_status=200, title=None,
                       meta_description=None, h1=(), h2=(), h3=(), text=text, visible_chars=len(text), parked_text=False,
                       rebrand_text=False, sha256="x")


def test_the_spam_filter_uses_only_registration_and_launch_facts_and_counts_each_rule():
    rows = [
        _kept_row("freshbakery.com"),
        _kept_row("best-online-casino.com"),
        _kept_row("a-b-c-d.com"),
        _kept_row("shop12345.com"),
        _kept_row("pills.com"),
        _kept_row("parkednear.com", ns_parking=True, checked="2025-12-15T00:00:00Z"),
        _kept_row("parkedtoday.com", ns_parking=True, checked="2026-11-22T00:00:00Z"),
    ]
    pages = {"pills.com": _page("pills.com", "Buy viagra online today with free shipping"),
             "freshbakery.com": _page("freshbakery.com", "Sourdough bread baked every morning")}
    result = ns.spam_filter(rows, pages)
    assert result.reasons == {"best-online-casino.com": ns.RULE_SPAM_NAME, "a-b-c-d.com": ns.RULE_SPAM_NAME,
                              "shop12345.com": ns.RULE_SPAM_NAME, "pills.com": ns.RULE_SPAM_TEXT,
                              "parkednear.com": ns.RULE_PARKING_NS}
    assert result.removed == {ns.RULE_PARKING_NS: 1, ns.RULE_SPAM_NAME: 3, ns.RULE_SPAM_TEXT: 1}
    assert result.candidates == 7 and result.kept_after_filter == 2
    assert "parkedtoday.com" not in result.reasons, "today's name servers on a site launched a year ago are never used"
    assert result.ns_not_judged == 6
    assert any("Rule spam_domain_name removed 3 sites." == line for line in result.lines())
    # A row that is not kept is not a candidate, and the filter reads no rankings or traffic at all.
    import inspect

    assert set(inspect.signature(ns.spam_filter).parameters) == {"rows", "pages", "rule"}
    assert set(inspect.signature(ns.spam_reason).parameters) == {"row", "page", "rule"}
    frame = ns.filtered_frame(rows, result)
    assert frame.loc[frame["site"] == "pills.com", "spam_rule"].iloc[0] == ns.RULE_SPAM_TEXT
    assert frame["kept_after_filter"].sum() == 2 and len(frame) == 7
    assert ns.SpamRule(name_max_digits=9).rule_hash() != ns.DEFAULT_SPAM_RULE.rule_hash()


def test_cohort_never_takes_an_excluded_site_however_it_is_spelled(tmp_path):
    regs = [ns.Registration("WWW.SmartCrewHQ.com", date(2025, 10, 27), "t", "com"), ns.Registration("ok.com", date(2025, 10, 27), "t", "com")]
    result = ns.cohort(regs, http=httpx.Client(transport=httpx.MockTransport(refuse_all)), plan=ns.CrawlPlan((), ()), crawls={},
                       cache_root=tmp_path / "c", public_root=tmp_path / "p", use_rdap=False, use_dns=False, use_cc=False)
    assert [row.site for row in result.rows] == ["ok.com"]


def test_an_error_page_is_never_saved_as_the_crawl_list_and_retries_do_not_really_sleep(tmp_path):
    slept: list[float] = []

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not here")

    target = tmp_path / "collinfo.json"
    with pytest.raises(ConnectionError):
        ns.load_crawls(target, http=httpx.Client(transport=httpx.MockTransport(missing)), sleep=slept.append)
    assert not target.exists()

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(ConnectionError):
        ns.load_crawls(target, http=httpx.Client(transport=httpx.MockTransport(down)), sleep=slept.append)
    assert slept and not target.exists()


def test_page_text_without_a_capture_date_is_never_written_and_excluded_sites_are_dropped(tmp_path):
    path = tmp_path / "page_text.jsonl"
    with pytest.raises(ValueError, match="no capture date"):
        ts.write_page_texts(path, [{"site": "ok.com", "captured_on": None, "title": "t"}])
    ts.write_page_texts(path, [{"site": "www.SmartCrewHQ.com", "captured_on": date(2025, 1, 1), "title": "x"},
                               {"site": "https://www.ok.com/", "captured_on": date(2025, 1, 2), "title": "Ok"}])
    texts = ts.load_page_texts(path)
    assert list(texts) == ["ok.com"] and texts["ok.com"][0]["captured_on"] == date(2025, 1, 2)
    assert ts.page_text_dated(texts["ok.com"][0]).snapshot_date == date(2025, 1, 2)


def test_the_synthetic_training_set_writes_its_universe_launch_text_and_what_rebuilds_its_world(tmp_path):
    world = World(4, niches=3, searches_per_niche=30, new_sites=12)
    made = ts.synthetic_training_set(world, out_dir=tmp_path / "syn")
    meta = json.loads((tmp_path / "syn" / ts.SYNTHETIC_META_FILE).read_text())
    params = meta["world_params"]
    assert params["seed"] == 4 and params["new_sites"] == 12 and params["searches_per_niche"] == 30 and params["knobs_default"]
    rebuilt = World(params["seed"], niches=params["niches"], searches_per_niche=params["searches_per_niche"],
                    established_sites=params["established_sites"], new_sites=params["new_sites"],
                    start_month=date.fromisoformat(params["start_month"]), months=params["months"],
                    location_code=params["location_code"])
    assert sorted(rebuilt.new_sites) == sorted(world.new_sites)
    universe = pd.read_parquet(made.paths["universe"])
    assert set(universe["label"]) == {"SYNTHETIC"} and len(universe) == len(world.searches)
    texts = ts.load_page_texts(Path(made.paths["page_text"]))
    alive = {site for site, info in world.new_sites.items() if info.alive}
    assert set(texts) == alive
    assert all(rows[0]["captured_on"] == world.new_sites[site].launch_date for site, rows in texts.items())


def test_a_synthetic_universe_is_spotted_by_its_source_columns_without_the_marker_file(tmp_path):
    import pandas as pd

    from aeo.ranksim.forecast import trainingset as ts

    rows = pd.DataFrame([{"keyword": "roof repair", "locale": "US_en", "sources": "synthetic",
                          "difficulty_proxy_source": "synthetic_difficulty"}])
    path = tmp_path / "universe.parquet"
    rows.to_parquet(path, index=False)
    assert "sources" in (ts.synthetic_table_reason(path) or "")
    real = pd.DataFrame([{"keyword": "roof repair", "locale": "US_en", "sources": "dfs_keywords",
                          "difficulty_proxy_source": "dataforseo_kd"}])
    real_path = tmp_path / "real" / "universe.parquet"
    real_path.parent.mkdir()
    real.to_parquet(real_path, index=False)
    assert ts.synthetic_table_reason(real_path) is None
