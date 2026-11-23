"""The data commands (forecast/cli_data.py): pull, cohort, universe, panel, content and updates.

No network, no keys, no real sleeping. Every HTTP client handed to the commands either fails the test when
it is called or is an httpx.MockTransport. Every file lives under tmp_path.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import httpx
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aeo.ranksim.forecast import budget, cli, pipeline
from aeo.ranksim.forecast.budget import SpendLedger
from aeo.ranksim.forecast.sources import updates as up

START = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
FAKE_LOGIN = "fake-dfs-login-7781"
FAKE_PASSWORD = "fake-dfs-password-5523"
TESTS = Path(__file__).parent
DFS_FIX = TESTS / "fixtures" / "forecast"
PANEL_FIX = TESTS / "fixtures" / "forecast_panel"
TS_FIX = TESTS / "fixtures" / "forecast_trainingset"
SOURCES_FIX = TESTS / "fixtures" / "forecast_sources"


class ForbiddenTransport(httpx.BaseTransport):
    """A transport that fails the test if anything is sent."""

    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise AssertionError(f"no HTTP request may be made here: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv("RANKSIM_SCOREBOARD_URL", raising=False)
    monkeypatch.delenv("RANKSIM_FORECAST_DIR", raising=False)
    monkeypatch.delenv("RANKSIM_MODELS_DIR", raising=False)
    monkeypatch.setenv("RANKSIM_MODEL_DOWNLOAD", "0")
    monkeypatch.setenv("RANKSIM_EXPORT_DIR", str(tmp_path / "committed_export"))


@pytest.fixture
def fake_credentials(monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", FAKE_LOGIN)
    monkeypatch.setenv("DATAFORSEO_PASSWORD", FAKE_PASSWORD)


def run(argv: list[str], data: Path, *, client: httpx.Client | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv + ["--dir", str(data)], http_client=client, clock=pipeline.VirtualClock(START), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def ledger_bytes(data: Path) -> bytes | None:
    path = data / "spend.jsonl"
    return path.read_bytes() if path.exists() else None


def write_lines(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def paid_pulls(tmp_path: Path) -> list[list[str]]:
    searches = write_lines(tmp_path / "searches.txt", ["plumber near me", "emergency plumber"])
    sites = write_lines(tmp_path / "sites.txt", ["tinynew.com", "cnn.com"])
    return [
        ["pull", "past-pages", "--searches", str(searches)],
        ["pull", "traffic", "--sites", str(sites)],
        ["pull", "positions", "--sites", str(sites)],
    ]


# ---------------------------------------------------------------------------------------------
# A fake DataForSEO Labs behind one MockTransport.


def dfs_fixture(name: str) -> Any:
    return json.loads((DFS_FIX / name).read_text(encoding="utf-8"))


def serps_answer(task: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dfs_fixture("dfs_historical_serps.json"))
    result = body["tasks"][0]["result"][0]
    result["keyword"] = task["keyword"]
    for snap in result["items"]:
        snap["keyword"] = task["keyword"]
    return body


ANSWERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "historical_serps": serps_answer,
    "historical_bulk_traffic_estimation": lambda task: dfs_fixture("dfs_historical_bulk_traffic.json"),
    "ranked_keywords": lambda task: dfs_fixture("dfs_ranked_keywords.json"),
}


def labs_client(log: list[dict[str, Any]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.dataforseo.com", request.url
        function = request.url.path.split("/")[-2]
        task = json.loads(request.content)[0]
        log.append({"function": function, "task": task})
        return httpx.Response(200, json=ANSWERS[function](task))

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------------------------
# pull: dry run, refusal, resume


def test_every_pull_is_a_dry_run_that_sends_and_spends_nothing(tmp_path, fake_credentials):
    data = tmp_path / "data"
    SpendLedger(data / "spend.jsonl").approve(Decimal("5"), by="test", note="so the guard would allow it")
    before = ledger_bytes(data)
    transport = ForbiddenTransport()
    for argv in paid_pulls(tmp_path):
        code, out, err = run(argv, data, client=httpx.Client(transport=transport))
        assert code == 0, (argv, err)
        assert "Dry run. Nothing was sent and nothing was spent." in out and "Exact cost" in out
        assert "Remaining 5.000000 USD" in out
    assert transport.calls == 0 and ledger_bytes(data) == before
    assert not (data / "raw").exists() and not (data / "cache" / "trainingset" / "jobs").exists()


def test_a_pull_dry_run_needs_no_login_and_prices_from_budget(tmp_path):
    data = tmp_path / "data"
    argv = paid_pulls(tmp_path)
    code, out, _ = run(argv[0] + ["--json"], data)
    assert code == 0
    payload = json.loads(out)
    assert Decimal(payload["plan"]["usd"]) == budget.historical_serps_estimate(24, 2)
    assert payload["report"]["calls_saved"] == 0 and payload["plan"]["guard_would_allow"] is False
    code, out, _ = run(argv[1] + ["--json"], data)
    assert Decimal(json.loads(out)["plan"]["usd"]) == budget.historical_traffic_cost(2)
    code, out, _ = run(argv[2] + ["--json"], data)
    assert Decimal(json.loads(out)["plan"]["usd"]) == budget.ranked_keywords_cost(2, 100)


def test_a_pull_with_execute_and_no_approval_is_refused_with_the_approve_hint(tmp_path, fake_credentials):
    data = tmp_path / "data"
    transport = ForbiddenTransport()
    for argv in paid_pulls(tmp_path):
        code, out, err = run(argv + ["--execute"], data, client=httpx.Client(transport=transport))
        assert code == 2, (argv, out, err)
        assert "Refused by the budget guard" in err and "python -m aeo.ranksim forecast budget approve --amount" in err
    assert transport.calls == 0 and ledger_bytes(data) is None


def test_a_pull_over_one_dollar_is_refused_until_its_sample_is_recorded(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "50", "--by", "James", "--note", "step 4"], data)
    sites = write_lines(tmp_path / "many.txt", [f"site{i}.com" for i in range(900)])
    transport = ForbiddenTransport()
    code, _, err = run(["pull", "traffic", "--sites", str(sites), "--execute"], data, client=httpx.Client(transport=transport))
    assert code == 2 and "no recorded $1 sample" in err and "forecast sample traffic" in err
    assert transport.calls == 0


@pytest.mark.parametrize("which", [0, 1, 2])
def test_a_resumed_pull_reads_saved_answers_and_never_pays_twice(tmp_path, fake_credentials, which):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "step 4"], data)
    argv = paid_pulls(tmp_path)[which]
    log: list[dict[str, Any]] = []
    code, out, err = run(argv + ["--execute", "--json"], data, client=labs_client(log))
    assert code == 0, err
    first = json.loads(out)
    sent = len(log)
    assert sent >= 1 and Decimal(first["spent_usd"]) > 0 and first["report"]["label"] == "REAL"
    spent = SpendLedger(data / "spend.jsonl").spent_total

    # The dry run now sees every call saved and nothing left to pay.
    code, out, _ = run(argv + ["--json"], data, client=httpx.Client(transport=ForbiddenTransport()))
    dry = json.loads(out)
    assert dry["report"]["calls_saved"] == dry["report"]["calls_planned"] and Decimal(dry["plan"]["usd"]) == 0
    assert dry["plan"]["guard_would_allow"] is True

    # Run again: no request, no spend.
    code, out, err = run(argv + ["--execute", "--json"], data, client=labs_client(log))
    assert code == 0, err
    again = json.loads(out)
    assert len(log) == sent and again["report"]["calls_from_cache"] == again["report"]["calls"]
    assert Decimal(again["spent_usd"]) == 0 and SpendLedger(data / "spend.jsonl").spent_total == spent


def test_pull_past_pages_writes_into_its_own_panel_folder_not_the_sample_panel(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "step 4"], data)
    code, out, err = run(paid_pulls(tmp_path)[0] + ["--execute", "--json"], data, client=labs_client([]))
    assert code == 0, err
    assert Path(json.loads(out)["report"]["outputs"]["panel"]) == data / "cache" / "panel_labs"
    assert not (data / "cache" / "panel").exists()


def test_pull_positions_picks_sites_in_a_frozen_label_blind_order(tmp_path):
    data = tmp_path / "data"
    sites = write_lines(tmp_path / "s.txt", [f"site{i}.com" for i in range(10)] + ["www.SmartCrewHQ.com"])
    code, out, _ = run(["pull", "positions", "--sites", str(sites), "--max-sites", "3", "--json"], data)
    one = json.loads(out)["report"]
    reversed_file = write_lines(tmp_path / "r.txt", list(reversed(sites.read_text().split())))
    code, out, _ = run(["pull", "positions", "--sites", str(reversed_file), "--max-sites", "3", "--json"], data)
    two = json.loads(out)["report"]
    assert one["units"] == 3 and one["job"] == two["job"]


def test_pull_plan_prints_costs_approval_and_the_missing_samples(tmp_path):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "50", "--by", "James", "--note", "step 4"], data)
    SpendLedger(data / "spend.jsonl").record_sample(budget.KIND_HISTORICAL_TRAFFIC, {"sites_asked": 3})
    code, out, _ = run(["pull", "plan"], data)
    assert code == 0
    assert "14.400000 USD" in out and "26.400000 USD" in out and "48.000000 USD" in out
    assert "The $1 sample for traffic history is recorded." in out
    assert "The $1 sample for past result pages is MISSING. Run: python -m aeo.ranksim forecast sample historical-serps" in out
    assert "Approved 50.000000 USD" in out
    code, out, _ = run(["pull", "plan", "--json"], data)
    payload = json.loads(out)
    assert {item["kind"] for item in payload["samples_missing"]} == {budget.KIND_HISTORICAL_SERPS, budget.KIND_RANKED_KEYWORDS}
    assert payload["plan"]["searches"] == 5000 and payload["plan"]["sites"] == 20000


def test_pull_traffic_reads_the_kept_sites_of_a_cohort_table(tmp_path):
    data = tmp_path / "data"
    frame = pd.DataFrame({"site": ["a.com", "b.com", "c.com"], "kept": [True, True, False], "kept_after_filter": [True, False, False]})
    frame.to_parquet(tmp_path / "cohort.parquet", index=False)
    code, out, _ = run(["pull", "traffic", "--cohort", str(tmp_path / "cohort.parquet"), "--json"], data)
    assert code == 0 and json.loads(out)["report"]["units"] == 1
    code, out, _ = run(["pull", "traffic", "--cohort", str(tmp_path / "cohort.parquet"), "--which", "all", "--json"], data)
    assert json.loads(out)["report"]["units"] == 3


def test_no_credential_appears_in_pull_output(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "step 4"], data)
    texts = []
    for argv in paid_pulls(tmp_path):
        code, out, err = run(argv + ["--execute"], data, client=labs_client([]))
        texts.append(out + err)
    assert all(FAKE_LOGIN not in text and FAKE_PASSWORD not in text for text in texts)


# ---------------------------------------------------------------------------------------------
# cohort build


def test_cohort_build_from_a_registrations_file_writes_the_table_with_the_spam_filter(tmp_path):
    data = tmp_path / "data"
    regs = tmp_path / "regs.csv"
    regs.write_text("site,listed_on\nfreshbakery.com,2025-10-27\nWWW.SmartCrewHQ.com,2025-10-27\nbest-online-casino-2025.com,2025-10-27\n",
                    encoding="utf-8")
    transport = ForbiddenTransport()
    code, out, err = run(["cohort", "build", "--name", "t", "--registrations", str(regs), "--no-cc", "--no-rdap", "--no-dns"], data,
                         client=httpx.Client(transport=transport))
    assert code == 0, err
    assert transport.calls == 0
    assert "Rule spam_domain_name removed 0 sites." in out and "Kept after the filter: 0." in out
    assert "Excluded client sites dropped: 1." in out
    frame = pd.read_parquet(data / "cache" / "newsites" / "cohort_t.parquet")
    assert set(frame["site"]) == {"freshbakery.com", "best-online-casino-2025.com"}
    assert {"spam_rule", "kept_after_filter"} <= set(frame.columns)
    code, out, _ = run(["cohort", "build", "--name", "t", "--registrations", str(regs), "--no-cc", "--no-rdap", "--no-dns", "--json"], data,
                       client=httpx.Client(transport=transport))
    payload = json.loads(out)
    assert payload["spam_filter"]["removed"] == {"parking_name_servers_near_launch": 0, "spam_domain_name": 0, "spam_launch_text": 0}


def test_cohort_build_refuses_common_crawl_without_a_crawl_and_needs_registrations(tmp_path):
    data = tmp_path / "data"
    regs = write_lines(tmp_path / "r.csv", ["site,listed_on", "a.com,2025-10-27"])
    code, _, err = run(["cohort", "build", "--name", "t", "--registrations", str(regs)], data)
    assert code == 2 and "--after" in err
    code, _, err = run(["cohort", "build", "--name", "t", "--no-cc"], data)
    assert code == 2 and "--registrations" in err


# ---------------------------------------------------------------------------------------------
# universe build


def kaggle_shard(path: Path) -> Path:
    schema = pa.schema([("palavra", pa.string()), ("mercado", pa.string()), ("volume", pa.int64()), ("concorrencia", pa.float64()),
                        ("cpc", pa.float64()), ("adulto", pa.bool_())])
    words = [("plumber near me", 0.1), ("emergency plumber", 0.3), ("plumbing repair", 0.5), ("plumber cost", 0.7),
             ("plumber login", 0.2), ("plumbing supplies", 0.9)]
    rows = [{"palavra": w, "mercado": "US", "volume": 100, "concorrencia": c, "cpc": 1.0, "adulto": False} for w, c in words]
    rows += [{"palavra": f"{ch} filler", "mercado": "US", "volume": 10, "concorrencia": 0.5, "cpc": 0.1, "adulto": False}
             for ch in "abcdefghijklmnopqrstuvwxyz"]
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)
    return path


def test_universe_build_writes_the_table_and_says_the_metrics_are_not_launch_features(tmp_path):
    data = tmp_path / "data"
    shard = kaggle_shard(tmp_path / "shard.parquet")
    code, out, err = run(["universe", "build", "--niche", "plumbing", "--seed-term", "plumber", "--seed-term", "plumbing", "--n", "3",
                          "--kaggle", str(shard), "--json"], data, client=httpx.Client(transport=ForbiddenTransport()))
    assert code == 0, err
    payload = json.loads(out)
    assert payload["counts"]["selected"] == 3 and payload["counts"]["dropped"] == {"navigational_phrase": 1}
    assert Path(payload["paths"]["table"]).is_file() and Path(payload["paths"]["table"]).parent == data / "cache" / "universe"
    code, out, _ = run(["universe", "build", "--niche", "plumbing", "--seed-term", "plumber", "--n", "3", "--kaggle", str(shard)], data)
    assert "never launch-month features" in out
    code, out, _ = run(["pull", "past-pages", "--universe", payload["paths"]["table"], "--json"], data)
    assert json.loads(out)["report"]["units"] == 3


def test_universe_build_without_any_source_file_is_a_usage_error(tmp_path):
    code, _, err = run(["universe", "build", "--niche", "x", "--seed-term", "plumber"], tmp_path / "data",
                       client=httpx.Client(transport=ForbiddenTransport()))
    assert code == 2 and "--download" in err


# ---------------------------------------------------------------------------------------------
# panel build and panel summary


def _gz(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(gzip.compress(src.read_bytes()))
    return dst


def sample_root(data: Path) -> Path:
    root = data / "public" / "dataforseo_samples"
    _gz(PANEL_FIX / "history_US_en.jsonl", root / "google_history/serp_advanced/US_en.json.gz")
    _gz(PANEL_FIX / "serp_advanced_US_en.jsonl", root / "google/serp_advanced/US_en.json.gz")
    return root


def test_panel_build_fills_a_new_folder_and_refuses_to_overwrite_without_the_flag(tmp_path):
    data = tmp_path / "data"
    sample_root(data)
    out = tmp_path / "rebuilt" / "panel"
    code, text, err = run(["panel", "build", "--out", str(out)], data)
    assert code == 0, err
    assert "The folder was new or empty." in text and any(out.glob("dataset=*/locale=*/part-*.parquet"))
    assert (out / "SOURCES.json").is_file()
    stamp = {p: p.stat().st_mtime_ns for p in out.rglob("*.parquet")}
    code, _, err = run(["panel", "build", "--out", str(out)], data)
    assert code == 2 and "--overwrite" in err
    assert {p: p.stat().st_mtime_ns for p in out.rglob("*.parquet")} == stamp, "a refused build leaves the panel as it was"
    code, text, err = run(["panel", "build", "--out", str(out), "--overwrite", "--json"], data)
    assert code == 0, err
    payload = json.loads(text)
    assert payload["replaced_existing"] is True and payload["rows"] > 0
    assert not [p for p in out.parent.iterdir() if p.name.startswith(".")], "no building or old folder is left behind"


def test_panel_build_into_an_existing_empty_folder_is_fine_and_no_files_is_an_error(tmp_path):
    data = tmp_path / "data"
    empty = tmp_path / "empty"
    empty.mkdir()
    code, _, err = run(["panel", "build", "--out", str(empty)], data)
    assert code == 2 and "No result-page sample files" in err
    sample_root(data)
    code, _, err = run(["panel", "build", "--out", str(empty), "--kinds", "history"], data)
    assert code == 0, err


def test_panel_summary_prints_counts_and_writes_the_json(tmp_path):
    data = tmp_path / "data"
    sample_root(data)
    code, _, err = run(["panel", "build"], data)
    assert code == 0, err
    code, out, err = run(["panel", "summary", "--out", str(tmp_path / "summary.json")], data)
    assert code == 0, err
    assert "dfs_history:" in out and "pages at risk of being altered" in out
    assert json.loads((tmp_path / "summary.json").read_text())["datasets"]["dfs_history"]["overall"]["rows"] > 0
    code, out, _ = run(["panel", "summary", "--json"], data)
    assert isinstance(json.loads(out)["datasets"]["dfs_history"]["overall"]["keywords"], int)
    code, _, err = run(["panel", "summary", "--panel-dir", str(tmp_path / "none")], data)
    assert code == 2 and "panel build" in err


# ---------------------------------------------------------------------------------------------
# content score


def test_content_score_says_which_backend_ran_and_labels_the_score_predicted(tmp_path):
    data = tmp_path / "data"
    page = tmp_path / "page.html"
    page.write_text((TS_FIX / "home_bakery.html").read_text(encoding="utf-8"), encoding="utf-8")
    code, out, err = run(["content", "score", "--search", "sourdough bread austin", "--file", str(page), "--json"], data,
                         client=httpx.Client(transport=ForbiddenTransport()))
    assert code == 0, err
    payload = json.loads(out)
    assert payload["label"] == "PREDICTED" and payload["mode"] == "lexical" and payload["backend"] == "the lexical fallback"
    assert payload["fallback_reason"] and payload["read_as"] == "html" and payload["value"] is not None
    text = tmp_path / "page.txt"
    text.write_text("Fresh sourdough bread baked every morning in Austin.", encoding="utf-8")
    code, out, _ = run(["content", "score", "--search", "sourdough bread", "--file", str(text), "--backend", "lexical"], data)
    assert code == 0 and "the lexical fallback" in out and "PREDICTED" in out and "plain text" in out
    code, _, err = run(["content", "score", "--search", "x", "--file", str(tmp_path / "missing.html")], data)
    assert code == 2


# ---------------------------------------------------------------------------------------------
# updates show and updates sync


def packaged_digest() -> str:
    return hashlib.sha256(up.PACKAGED_PATH.read_bytes()).hexdigest()


def test_updates_show_reads_the_packaged_calendar_with_no_network(tmp_path):
    code, out, err = run(["updates", "show", "--kind", "core", "--limit", "2", "--json"], tmp_path / "data",
                         client=httpx.Client(transport=ForbiddenTransport()))
    assert code == 0, err
    payload = json.loads(out)
    assert payload["origin"] == "the packaged copy" and payload["label"] == "REAL"
    assert len(payload["updates"]) == 2 and {item["kind"] for item in payload["updates"]} == {"core"}


def test_updates_sync_fetches_only_with_the_flag_and_never_writes_the_packaged_copy(tmp_path):
    data = tmp_path / "data"
    before = packaged_digest()
    transport = ForbiddenTransport()
    code, out, _ = run(["updates", "sync"], data, client=httpx.Client(transport=transport))
    assert code == 0 and "Nothing was fetched" in out and transport.calls == 0
    assert not up.cache_path(data).exists()

    history = (SOURCES_FIX / "google_status_history_sample.html").read_bytes()
    incident = (SOURCES_FIX / "google_status_incident_sample.html").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "status.search.google.com"
        return httpx.Response(200, content=history if request.url.path.endswith("/history") else incident)

    code, out, err = run(["updates", "sync", "--fetch", "--json"], data, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert code == 0, err
    payload = json.loads(out)
    assert payload["fetched"] is True and payload["count"] > 0 and Path(payload["cache"]) == up.cache_path(data)
    assert up.cache_path(data).is_file() and packaged_digest() == before
    code, out, _ = run(["updates", "show", "--json"], data)
    assert json.loads(out)["origin"] == "the cache"


def test_updates_sync_reports_a_dashboard_failure_and_writes_nothing(tmp_path):
    data = tmp_path / "data"

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    code, _, err = run(["updates", "sync", "--fetch"], data, client=httpx.Client(transport=httpx.MockTransport(broken)))
    assert code == 2 and "could not be read" in err and not up.cache_path(data).exists()


# ---------------------------------------------------------------------------------------------
# Every data command answers --json and prints no dash the docs forbid


def test_every_data_command_answers_json_and_prints_no_long_dashes(tmp_path):
    data = tmp_path / "data"
    sample_root(data)
    shard = kaggle_shard(tmp_path / "shard.parquet")
    regs = write_lines(tmp_path / "r.csv", ["site,listed_on", "a.com,2025-10-27"])
    page = write_lines(tmp_path / "p.txt", ["plumber near me"])
    argvs = [*paid_pulls(tmp_path), ["pull", "plan"],
             ["cohort", "build", "--name", "j", "--registrations", str(regs), "--no-cc", "--no-rdap", "--no-dns"],
             ["universe", "build", "--niche", "p", "--seed-term", "plumber", "--n", "3", "--kaggle", str(shard)],
             ["panel", "build"], ["panel", "summary"], ["content", "score", "--search", "plumber", "--file", str(page)],
             ["updates", "show"], ["updates", "sync"]]
    for argv in argvs:
        code, out, err = run(argv + ["--json"], data, client=httpx.Client(transport=ForbiddenTransport()))
        assert code == 0, (argv, err)
        json.loads(out)
        code, out, err = run(argv + (["--overwrite"] if argv == ["panel", "build"] else []), data,
                             client=httpx.Client(transport=ForbiddenTransport()))
        assert code == 0, (argv, err)
        assert "—" not in out + err and "–" not in out + err, argv


def test_pull_plan_counts_each_site_once_and_never_an_excluded_site(tmp_path):
    sites = write_lines(tmp_path / "s.txt", ["a.com", "www.A.com", "SmartCrewHQ.com", "b.com"])
    code, out, _ = run(["pull", "plan", "--sites", str(sites), "--json"], tmp_path / "data")
    assert code == 0 and json.loads(out)["plan"]["sites"] == 2


def test_cohort_build_reports_a_public_source_failure_as_a_usage_error(tmp_path):
    regs = write_lines(tmp_path / "r.csv", ["site,listed_on", "a.com,2025-10-27"])

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    code, _, err = run(["cohort", "build", "--name", "t", "--registrations", str(regs), "--after", "CC-MAIN-TEST-02", "--no-rdap",
                        "--no-dns"], tmp_path / "data", client=httpx.Client(transport=httpx.MockTransport(broken)))
    assert code == 2 and "could not be read" in err


def test_a_paid_pull_refuses_a_synthetic_table_in_one_sentence_even_as_a_dry_run(tmp_path):
    data = tmp_path / "data"
    labelled = tmp_path / "labelled"
    labelled.mkdir()
    pd.DataFrame({"site": ["made-up-1.test", "made-up-2.test"], "kept": [True, True],
                  "label": ["SYNTHETIC", "SYNTHETIC"]}).to_parquet(labelled / "cohort.parquet", index=False)
    marked = tmp_path / "marked"  # the folder paid synthetic writes: SYNTHETIC.json beside the tables
    marked.mkdir()
    (marked / "SYNTHETIC.json").write_text("{}", encoding="utf-8")
    pd.DataFrame({"keyword": ["fake search"]}).to_parquet(marked / "universe.parquet", index=False)
    pd.DataFrame({"site": ["made-up-3.test"], "kept": [True]}).to_parquet(marked / "cohort.parquet", index=False)
    transport = ForbiddenTransport()
    for argv in (["pull", "traffic", "--cohort", str(labelled / "cohort.parquet")],
                 ["pull", "positions", "--cohort", str(marked / "cohort.parquet")],
                 ["pull", "past-pages", "--universe", str(marked / "universe.parquet")],
                 ["pull", "plan", "--universe", str(marked / "universe.parquet")]):
        code, out, err = run(argv, data, client=httpx.Client(transport=transport))
        assert code == 2, argv
        assert "is SYNTHETIC" in err and "a paid pull never runs on SYNTHETIC names" in err
        assert "USD" not in out + err  # nothing was priced
        assert err.strip().count(". ") == 0  # one sentence
    assert transport.calls == 0 and ledger_bytes(data) is None


def test_a_missing_pull_input_names_the_file_and_the_command_that_builds_it(tmp_path):
    data = tmp_path / "data"
    missing = tmp_path / "nosuch.parquet"
    code, _, err = run(["pull", "past-pages", "--universe", str(missing)], data)
    assert code == 2 and f"The universe table {missing} does not exist" in err
    assert "Build it with: python -m aeo.ranksim forecast universe build" in err
    code, _, err = run(["pull", "traffic", "--cohort", str(missing)], data)
    assert code == 2 and "Build it with: python -m aeo.ranksim forecast cohort build" in err
    code, _, err = run(["pull", "positions", "--sites", str(tmp_path / "nosuch.txt")], data)
    assert code == 2 and "nosuch.txt does not exist. Give --sites a text file with one site per line." in err


def test_pull_plan_prints_the_total_at_the_plan_rate_and_at_the_sample_rate_never_the_same_figure_twice(tmp_path):
    code, out, _ = run(["pull", "plan"], tmp_path / "data")
    assert code == 0
    totals = [line for line in out.splitlines() if line.startswith("Total with 24 months")]
    assert len(totals) == 1
    plan_total = budget.historical_serps_estimate(24, 5000) + budget.historical_traffic_cost(20000) + \
        budget.ranked_keywords_cost(2000, 100) + budget.SAMPLE_MAX_USD * 3
    sample_total = budget.historical_serps_sample_rate_estimate(24, 5000) + budget.historical_traffic_cost(20000) + \
        budget.ranked_keywords_cost(2000, 100) + budget.SAMPLE_MAX_USD * 3
    assert budget.usd_text(plan_total) in totals[0] and budget.usd_text(sample_total) in totals[0]
    assert totals[0].count(budget.usd_text(plan_total)) == 1
    assert "at the rate seen in the free history samples" in totals[0]
    sample_line = budget.usd_text(budget.historical_serps_sample_rate_estimate(24, 5000))
    assert any(line.startswith("24 months, Past result pages at the rate seen in the free history samples") and sample_line in line
               for line in out.splitlines())
