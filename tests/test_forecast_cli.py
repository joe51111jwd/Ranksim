"""The forecast command line (forecast/cli.py and the forwarder in aeo/ranksim/cli.py).

No network, no keys, no real sleeping. Every HTTP client handed to the commands
here either fails the test when it is called or is an httpx.MockTransport.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from aeo.ranksim.cli import _forecast_argv
from aeo.ranksim.cli import main as ranksim_main
from aeo.ranksim.forecast import cli, pipeline
from aeo.ranksim.forecast.budget import SpendLedger, serp_cost, serper_cost, usd_text
from aeo.ranksim.forecast.config import forecast_paths
from aeo.ranksim.forecast.seeds import SeedManifest
from aeo.ranksim.forecast.synth import World
from aeo.ranksim.forecast.types import Forecast, PullSpec

START = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
FAKE_LOGIN = "fake-dfs-login-7781"
FAKE_PASSWORD = "fake-dfs-password-5523"
FAKE_SERPER = "fake-serper-key-9934"
FIXTURES = Path(__file__).parent / "fixtures"


class ForbiddenTransport(httpx.BaseTransport):
    """A transport that fails the test if anything is sent."""

    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise AssertionError(f"no HTTP request may be made here: {request.method} {request.url}")


@pytest.fixture(autouse=True)
def _no_scoreboard_override(monkeypatch, tmp_path):
    monkeypatch.delenv("RANKSIM_SCOREBOARD_URL", raising=False)
    monkeypatch.delenv("RANKSIM_FORECAST_DIR", raising=False)
    # The committed export of the repo (evals/scoreboard) must never anchor a test scoreboard.
    monkeypatch.setenv("RANKSIM_EXPORT_DIR", str(tmp_path / "committed_export"))


@pytest.fixture
def fake_credentials(monkeypatch):
    monkeypatch.setenv("DATAFORSEO_LOGIN", FAKE_LOGIN)
    monkeypatch.setenv("DATAFORSEO_PASSWORD", FAKE_PASSWORD)
    monkeypatch.setenv("SERPER_API_KEY", FAKE_SERPER)


def run(argv: list[str], data: Path, *, client: httpx.Client | None = None, clock=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv + ["--dir", str(data)], http_client=client, clock=clock or pipeline.VirtualClock(START), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def searches_file(tmp_path: Path, count: int = 2) -> Path:
    path = tmp_path / "searches.txt"
    path.write_text("# a comment\n" + "\n".join(f"plumber {i}" for i in range(count)) + "\n\n", encoding="utf-8")
    return path


def ledger_bytes(data: Path) -> bytes | None:
    path = data / "spend.jsonl"
    return path.read_bytes() if path.exists() else None


# ---------------------------------------------------------------------------------------------
# Paid commands are dry runs by default


def paid_commands(tmp_path: Path) -> list[list[str]]:
    searches = str(searches_file(tmp_path))
    sites = tmp_path / "sites.txt"
    sites.write_text("tinynew.com\nother.com\n", encoding="utf-8")
    return [
        ["read", "--searches", searches, "--location-code", "1026201"],
        ["read-round", "--searches", searches, "--location-code", "1026201"],
        ["read", "--searches", searches, "--location-code", "1026201", "--provider", "serper", "--serper-location", "Austin,Texas,United States"],
        ["sample", "serp", "--searches", searches, "--location-code", "1026201"],
        ["sample", "serp", "--searches", searches, "--location-code", "1026201", "--provider", "serper"],
        ["sample", "historical-serps", "--searches", searches],
        ["sample", "traffic", "--sites", str(sites)],
        ["sample", "ranked-keywords", "--sites", str(sites)],
    ]


def test_every_paid_command_is_a_dry_run_that_spends_nothing(tmp_path, fake_credentials):
    data = tmp_path / "data"
    SpendLedger(data / "spend.jsonl").approve(Decimal("5"), by="test", note="so the guard would allow it")
    before = ledger_bytes(data)
    transport = ForbiddenTransport()
    client = httpx.Client(transport=transport)
    for argv in paid_commands(tmp_path):
        code, out, err = run(argv, data, client=client)
        assert code == 0, (argv, err)
        assert "Dry run. Nothing was sent and nothing was spent." in out
        assert "Provider:" in out and "Calls:" in out and "Exact cost" in out and "Remaining 5.000000 USD" in out
    assert transport.calls == 0
    assert ledger_bytes(data) == before
    assert not (data / "raw").exists()


def test_dry_run_money_comes_from_budget(tmp_path):
    data = tmp_path / "data"
    code, out, _ = run(["read", "--searches", str(searches_file(tmp_path, 3)), "--location-code", "1026201", "--json"], data)
    assert code == 0
    plan = json.loads(out)["plan"]
    assert Decimal(plan["usd"]) == serp_cost(50, pulls=15)
    assert plan["usd_text"] == usd_text(serp_cost(50, pulls=15))
    assert plan["calls"] == 15 and plan["provider"] == "dataforseo" and plan["guard_would_allow"] is False


def test_execute_without_approval_is_refused_and_says_how_to_approve(tmp_path, fake_credentials):
    data = tmp_path / "data"
    transport = ForbiddenTransport()
    for argv in paid_commands(tmp_path):
        code, out, err = run(argv + ["--execute"], data, client=httpx.Client(transport=transport))
        assert code == 2, (argv, out, err)
        assert "Refused by the budget guard" in err
        assert "python -m aeo.ranksim forecast budget approve --amount" in err
    assert transport.calls == 0
    assert ledger_bytes(data) is None


def test_execute_over_the_sample_cap_is_refused_until_the_sample_is_recorded(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "50", "--by", "James", "--note", "readings"], data)
    transport = ForbiddenTransport()
    code, _, err = run(["read", "--searches", str(searches_file(tmp_path, 100)), "--location-code", "1026201", "--execute"], data,
                       client=httpx.Client(transport=transport))
    assert code == 2 and "no recorded $1 sample" in err and "forecast sample serp" in err
    assert transport.calls == 0


def test_read_refuses_the_synthetic_provider(tmp_path):
    code, _, err = run(["read", "--searches", str(searches_file(tmp_path)), "--location-code", "1026201", "--provider", "synthetic"],
                       tmp_path / "data")
    assert code == 2 and "demo" in err


def _serper_handler(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "google.serper.dev"
        assert request.headers["x-api-key"] == FAKE_SERPER
        body = json.loads(request.content)
        calls.append(body)
        page = int(body["page"])
        organic = [
            {"title": f"result {n}", "link": f"https://site{(page - 1) * 10 + n:02d}.com/{body['q'].replace(' ', '-')}", "position": n}
            for n in range(1, 11)
        ]
        return httpx.Response(200, json={"searchParameters": {"q": body["q"], "call": len(calls)}, "organic": organic, "credits": 1})

    return handler


def test_execute_reads_with_a_mocked_provider_and_records_the_spend(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "first serper reading"], data)
    calls: list[dict] = []
    client = httpx.Client(transport=httpx.MockTransport(_serper_handler(calls)))
    argv = ["read", "--searches", str(searches_file(tmp_path, 2)), "--location-code", "1026201", "--provider", "serper",
            "--serper-location", "Austin,Texas,United States", "--pulls", "3", "--gap-seconds", "60", "--execute", "--json"]
    code, out, err = run(argv, data, client=client)
    assert code == 0, err
    summary = json.loads(out)
    assert summary["readings"] == 2 and summary["degraded"] == 0 and summary["label"] == "REAL"
    assert len(calls) == 2 * 3 * 5 and all(call["location"] == "Austin,Texas,United States" for call in calls)
    assert Decimal(summary["spent_usd"]) == serper_cost(30)
    ledger = SpendLedger(data / "spend.jsonl")
    assert ledger.spent_total == serper_cost(30) and ledger.remaining == Decimal("1") - serper_cost(30)
    code, out, _ = run(["status", "--json"], data)
    assert code == 0 and json.loads(out)["counts"]["readings"] == 2 and json.loads(out)["counts"]["pulls"] == 6


# ---------------------------------------------------------------------------------------------
# No secret in any output


def test_no_credential_appears_in_any_output(tmp_path, fake_credentials):
    data = tmp_path / "data"

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status_code": 40100, "status_message": "You are not authorized."})

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    outputs: list[str] = []
    commands = [["status"], ["status", "--json"], ["budget", "show", "--json"], ["locations", "--name", "Austin"]]
    commands += paid_commands(tmp_path)
    commands += [argv + ["--execute"] for argv in paid_commands(tmp_path)]
    for argv in commands:
        code, out, err = run(argv, data, client=client)
        outputs.append(out + err)
    code, out, err = run(["budget", "approve", "--amount", "2", "--by", "James", "--note", "test"], data)
    outputs.append(out + err)
    for argv in (["sample", "serp", "--searches", str(searches_file(tmp_path)), "--location-code", "1026201", "--execute"],
                 ["locations", "--name", "Austin", "--json"]):
        code, out, err = run(argv, data, client=client)
        outputs.append(out + err)
        assert code == 2 and "DataForSEO refused" in err
    joined = "\n".join(outputs)
    for secret in (FAKE_LOGIN, FAKE_PASSWORD, FAKE_SERPER):
        assert secret not in joined


def test_scrubbed_stream_hides_a_credential_that_reaches_it():
    target = io.StringIO()
    stream = cli.ScrubbedStream(target, [FAKE_PASSWORD])
    stream.write(f"oops {FAKE_PASSWORD} leaked\n")
    assert target.getvalue() == "oops [hidden] leaked\n"


def test_missing_dataforseo_login_names_the_variables(tmp_path):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "test"], data)
    code, _, err = run(["locations", "--name", "Austin"], data, client=httpx.Client(transport=ForbiddenTransport()))
    assert code == 2 and "DATAFORSEO_LOGIN" in err and "DATAFORSEO_PASSWORD" in err


# ---------------------------------------------------------------------------------------------
# The forwarder in aeo/ranksim/cli.py


def test_forwarder_reaches_the_forecast_commands(tmp_path, capsys):
    data = tmp_path / "data"
    with pytest.raises(SystemExit) as stop:
        ranksim_main(["forecast", "budget", "show", "--dir", str(data)])
    assert stop.value.code == 0
    assert "Approved 0.000000 USD" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stop:
        ranksim_main(["--database", "sqlite://", "forecast", "status", "--json", "--dir", str(data)])
    assert stop.value.code == 0
    assert json.loads(capsys.readouterr().out)["counts"]["sites"] == 0
    with pytest.raises(SystemExit) as stop:
        ranksim_main(["forecast", "no-such-command"])
    assert stop.value.code == 2


def test_forwarder_only_takes_forecast_in_the_command_position():
    assert _forecast_argv(["forecast", "status", "--json"]) == ["status", "--json"]
    assert _forecast_argv(["--database", "forecast", "gaps"]) is None
    assert _forecast_argv(["--database=x", "forecast", "demo"]) == ["demo"]
    assert _forecast_argv(["gaps", "forecast"]) is None
    assert _forecast_argv(["-h"]) is None


# ---------------------------------------------------------------------------------------------
# Exit codes


def test_exit_codes(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import forecasting

    data = tmp_path / "data"
    assert run(["no-such-command"], data)[0] == 2
    assert run(["budget"], data)[0] == 2
    assert run(["status"], data)[0] == 0
    monkeypatch.setattr(forecasting, "newest_trust_report", lambda root=None: None)
    assert run(["marker", "--number", "2"], data)[0] == 1  # no scored trust report: marker 2 cannot be judged
    assert run(["marker", "--number", "5", "--source", "seed", "--as-of", "2027-03-19"], data)[0] == 1
    assert run(["seeds", "validate", "--manifest", str(tmp_path / "missing.json")], data)[0] == 2
    out = io.StringIO()
    assert cli.main(["--help"], stdout=out, stderr=io.StringIO()) == 0
    assert run(["budget", "approve", "--amount", "-3", "--by", "x", "--note", "y"], data)[0] == 2


def test_a_broken_chain_exits_1(tmp_path):
    from aeo.ranksim.forecast.types import Forecast

    data = tmp_path / "data"
    clock = pipeline.VirtualClock(START)
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock)
    board.add_site("seedone.com", source="seed", launch_date="2026-12-01", launch_date_source="test", built_by="test")
    board.add_search("seedone.com", "plumber prices", location_code=1026201)
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    board.record_forecast(Forecast(site="seedone.com", search="plumber prices", stage="upload", as_of=date(2026, 11, 23),
                                   point=12, range_low=8, range_high=20, label="contested", model_version="m1"))
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forecasts_no_update"))
        connection.execute(text("UPDATE forecasts SET point = 1"))
    board.close()
    code, out, _ = run(["verify", "--no-export"], data)
    assert code == 1 and "do NOT verify" in out and "forecast chain breaks" in out
    assert run(["status"], data)[0] == 1
    code, _, err = run(["report", "--source", "seed", "--stage", "upload", "--as-of", "2027-03-01"], data)
    assert code == 1 and "does not verify" in err


# ---------------------------------------------------------------------------------------------
# Seeds, marker 1, grading, export and verify through the command line


def candidates_csv(tmp_path: Path, count: int = 30) -> Path:
    path = tmp_path / "pool.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(cli.CANDIDATE_COLUMNS)
        for i in range(count):
            writer.writerow([f"plumber search {i:02d}", str(i * 3), "kd", str(100 + i)])
    return path


def test_seed_commands_from_template_to_register(tmp_path):
    data = tmp_path / "data"
    code, out, _ = run(["seeds", "template", "--out", str(tmp_path / "tpl")], data)
    assert code == 0 and (tmp_path / "tpl" / "seed_candidates.csv").exists()
    assert run(["seeds", "template", "--out", str(tmp_path / "tpl")], data)[0] == 2  # never overwrites
    manifest = tmp_path / "seed_manifest.json"
    code, out, err = run(["seeds", "pick", "--candidates", str(candidates_csv(tmp_path)), "--niche", "plumber",
                          "--domain", "seedone.com", "--domain", "seedtwo.com", "--launch-date", "2026-11-30",
                          "--location-code", "1026201", "--per-site", "6", "--out", str(manifest)], data)
    assert code == 0, err
    assert run(["seeds", "validate", "--manifest", str(manifest)], data)[0] == 0
    assert run(["seeds", "register", "--manifest", str(manifest)], data)[0] == 2  # not frozen yet
    code, out, _ = run(["seeds", "freeze", "--manifest", str(manifest), "--json"], data)
    digest = json.loads(out)["hash"]
    assert (tmp_path / "seed_manifest.json.sha256").read_text().strip() == digest
    code, out, _ = run(["seeds", "schedule", "--manifest", str(manifest), "--today", "2026-11-22", "--json"], data)
    schedule = json.loads(out)
    assert code == 0 and Decimal(schedule["cost_with_milestones_usd"]) == serp_cost(50, pulls=5 * 12 * 16)
    code, out, _ = run(["seeds", "register", "--manifest", str(manifest), "--json"], data)
    assert code == 0 and json.loads(out)["searches_added"] == 12
    code, out, _ = run(["read", "--manifest", str(manifest), "--json"], data)
    assert code == 0 and json.loads(out)["searches"] == 12 and json.loads(out)["manifest_hash"] == digest
    code, _, err = run(["seeds", "pick", "--candidates", str(candidates_csv(tmp_path)), "--niche", "roofer",
                        "--domain", "seedthree.com", "--launch-date", "2026-11-30", "--location-code", "1026201",
                        "--per-site", "6", "--out", str(manifest)], data)
    assert code == 2 and "frozen" in err


def _synthetic_readings(data: Path) -> tuple[list[str], int, date]:
    """Two days of SYNTHETIC readings 24 hours apart, written straight into the CLI's scoreboard."""
    world = World(3, niches=1, searches_per_niche=12, established_sites=40, new_sites=1)
    day = world.start + timedelta(days=200)
    clock = pipeline.VirtualClock(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc))
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock)
    source = pipeline.make_source("synthetic", None, None, data / "raw", world=world, clock=clock)
    searches = world.searches_in(world.niches[0])[:4]
    specs = pipeline.search_specs(searches, world.location_code)
    pipeline.take_readings(board, source, specs, clock=clock)
    clock.set(clock.now() + timedelta(hours=24))
    pipeline.take_readings(board, source, specs, clock=clock)
    board.close()
    return searches, world.location_code, day


def test_marker1_sample_and_report_through_the_command_line(tmp_path):
    data = tmp_path / "data"
    searches, location, day = _synthetic_readings(data)
    candidates = tmp_path / "candidates.txt"
    candidates.write_text("\n".join(searches) + "\n", encoding="utf-8")
    code, out, err = run(["marker1", "sample", "--candidates", str(candidates), "--n", "4", "--out", str(tmp_path / "m1.txt")], data)
    assert code == 0, err
    code, out, err = run(["marker1", "report", "--location-code", str(location), "--json"], data)
    result = json.loads(out)
    assert code == 1  # SYNTHETIC readings can never pass a marker
    assert result["report"]["pairs_used"] == 4 and result["report"]["label"] == "SYNTHETIC"
    assert result["evaluation"]["passed"] is None
    code, out, _ = run(["marker1", "report", "--location-code", str(location), "--a-date", (day + timedelta(days=5)).isoformat()], data)
    assert code == 1 and "Pairs used: 0 of 4" in out


def test_grade_report_export_and_verify_catch_truncation(tmp_path):
    data = tmp_path / "data"
    demo_dir = tmp_path / "demo"
    pipeline.demo(3, workdir=demo_dir, sites=2)
    export = tmp_path / "evals"
    code, _, err = run(["import", "--from", str(demo_dir / "export")], data)
    assert code == 0, err
    code, out, _ = run(["report", "--source", "synthetic", "--stage", "d30", "--as-of", "2026-12-31", "--json"], data)
    assert code == 0 and json.loads(out)["label"] == "SYNTHETIC"
    code, out, _ = run(["marker", "--number", "4", "--source", "synthetic", "--as-of", "2026-12-31"], data)
    assert code == 1 and "NOT PASSED (SYNTHETIC)" in out
    code, out, _ = run(["grade", "--date", "2026-12-31"], data)
    assert code == 0 and "Forecasts graded now: 0." in out
    assert run(["export", "--out", str(export)], data)[0] == 0
    assert run(["verify", "--against", str(export)], data)[0] == 0
    board = pipeline.open_scoreboard(forecast_paths(data), clock=pipeline.VirtualClock(datetime(2027, 1, 1, tzinfo=timezone.utc)))
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER grades_no_delete"))
        connection.execute(text("DELETE FROM grades WHERE seq = (SELECT MAX(seq) FROM grades)"))
    board.install_triggers()
    board.close()
    code, out, _ = run(["verify", "--against", str(export)], data)
    assert code == 1
    assert "The scoreboard chains verify." in out
    assert "NOT a prefix" in out and "missing from the database" in out and "Recent rows were cut off." in out


def test_demo_command_prints_only_synthetic_lines(tmp_path):
    code, out, err = run(["demo", "--workdir", str(tmp_path / "demo"), "--sites", "2"], tmp_path / "data")
    assert code == 0, err
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines and all(line.startswith("SYNTHETIC: ") for line in lines)
    assert not any(dash in out for dash in ("\u2014", "\u2013"))
    assert not (tmp_path / "data").exists()  # the demo never touches the real data folder


# ---------------------------------------------------------------------------------------------
# Locations, budget, Search Console and the agency form


def test_locations_search_the_public_csv_offline(tmp_path):
    path = tmp_path / "locations.csv"
    path.write_text(
        "location_code,location_name,location_code_parent,country_iso_code,location_type\n"
        "1026201,\"Austin,Texas,United States\",21176,US,City\n"
        "21176,\"Texas,United States\",2840,US,State\n"
        "1012873,\"Austin,Minnesota,United States\",21148,US,City\n",
        encoding="utf-8",
    )
    code, out, _ = run(["locations", "--name", "austin", "--csv", str(path), "--json"], tmp_path / "data",
                       client=httpx.Client(transport=ForbiddenTransport()))
    found = json.loads(out)
    assert code == 0 and found["total_matches"] == 2
    assert {item["location_code"] for item in found["matches"]} == {1026201, 1012873}


def test_budget_approve_and_show(tmp_path):
    data = tmp_path / "data"
    code, out, _ = run(["budget", "approve", "--amount", "50", "--by", "James", "--note", "first top up"], data)
    assert code == 0 and "Approved in total: 50.000000 USD" in out
    code, out, _ = run(["budget", "show", "--json"], data)
    shown = json.loads(out)
    assert shown["approved_usd"] == "50.000000" and shown["remaining_usd"] == "50.000000"
    code, out, _ = run(["budget", "prices", "--json"], data)
    assert json.loads(out)["plan_items"]["one clean reading (5 pulls, depth 50, Standard queue, repair on)"] == str(serp_cost(50, pulls=5))


def test_gsc_and_form_commands(tmp_path):
    data = tmp_path / "data"
    code, out, err = run(["gsc", "import", "--site", "seedsite.example", "--file", str(FIXTURES / "forecast_gsc" / "daily_queries.csv"),
                          "--kind", "daily", "--json"], data)
    assert code == 0, err
    record = json.loads(out)
    assert (data / record["stored_path"]).exists() and record["freeze_name"].startswith("gsc.seedsite.example.")
    form = tmp_path / "form.json"
    assert run(["form", "template", "--out", str(form)], data)[0] == 0
    code, out, _ = run(["form", "validate", "--file", str(form)], data)
    assert code == 1 and "not valid" in out
    world = World(3, niches=1, searches_per_niche=12, established_sites=40, new_sites=1)
    site = next(iter(world.new_sites))
    filled = tmp_path / "filled.json"
    filled.write_text(json.dumps({"form_version": 1, "answers": world.agency_form(site)}), encoding="utf-8")
    assert run(["form", "validate", "--file", str(filled)], data)[0] == 0
    code, out, _ = run(["form", "import", "--file", str(filled), "--json"], data)
    assert code == 0 and json.loads(out)["site"] == site
    code, out, _ = run(["status", "--json"], data)
    assert json.loads(out)["counts"]["freezes"] == 2


def test_marker1_sample_refuses_to_overwrite_before_freezing(tmp_path):
    """Bug found in review: the sample was frozen before the output file was checked."""
    data = tmp_path / "data"
    candidates = tmp_path / "candidates.txt"
    candidates.write_text("\n".join(f"search {i}" for i in range(10)) + "\n", encoding="utf-8")
    out = tmp_path / "m1.txt"
    out.write_text("something else\n", encoding="utf-8")
    code, _, err = run(["marker1", "sample", "--candidates", str(candidates), "--n", "4", "--out", str(out)], data)
    assert code == 2 and "Nothing was frozen" in err
    code, status, _ = run(["status", "--json"], data)
    assert json.loads(status)["counts"]["freezes"] == 0


# ---------------------------------------------------------------------------------------------
# Round mode, the reader version and the two estimates for past result pages


def test_read_round_dry_run_costs_one_pull_per_search(tmp_path):
    code, out, _ = run(["read-round", "--searches", str(searches_file(tmp_path, 3)), "--location-code", "1026201", "--json"],
                       tmp_path / "data")
    plan = json.loads(out)["plan"]
    assert code == 0 and plan["calls"] == 3 and Decimal(plan["usd"]) == serp_cost(50, pulls=3)
    assert "one round of 3 searches" in plan["item"]


def test_five_rounds_across_a_day_then_assemble(tmp_path, fake_credentials):
    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "round mode"], data)
    calls: list[dict] = []
    client = httpx.Client(transport=httpx.MockTransport(_serper_handler(calls)))
    clock = pipeline.VirtualClock(START)
    argv = ["read-round", "--searches", str(searches_file(tmp_path, 2)), "--location-code", "1026201", "--provider", "serper",
            "--serper-location", "Austin,Texas,United States", "--execute", "--json"]
    for hour in (9, 12, 15, 18, 21):
        clock.set(START.replace(hour=hour))
        code, out, err = run(argv, data, client=client, clock=clock)
        assert code == 0, err
        summary = json.loads(out)
        assert summary["pulls_returned"] == 2 and summary["label"] == "REAL" and summary["freeze_name"]
    code, out, _ = run(["status", "--json"], data, clock=clock)
    assert json.loads(out)["counts"]["readings"] == 0 and json.loads(out)["counts"]["pulls"] == 10
    clock.set(START.replace(hour=23))
    code, out, err = run(["assemble", "--date", START.date().isoformat(), "--json"], data, client=client, clock=clock)
    assert code == 0, err
    built = json.loads(out)
    assert built["rounds"] == 5 and built["readings_built"] == 2 and built["readings_stored"] == 2 and built["degraded"] == 0
    assert built["labels"] == {"REAL": 2} and built["reader_version"] == "reader-v2"
    code, out, _ = run(["assemble", "--date", START.date().isoformat()], data, clock=clock)
    assert code == 0 and "Already stored 2." in out and not any(dash in out for dash in ("\u2014", "\u2013"))
    code, out, _ = run(["assemble", "--date", "2026-12-25"], data, clock=clock)
    assert code == 1 and "No stored pull completed" in out


def test_marker1_report_takes_a_reader_version(tmp_path):
    data = tmp_path / "data"
    searches, location, _day = _synthetic_readings(data)
    code, out, _ = run(["marker1", "report", "--searches", str(_write(tmp_path, searches)), "--location-code", str(location),
                        "--reader-version", "1", "--json"], data)
    result = json.loads(out)
    assert result["report"]["reader"]["reader_version"] == "reader-v1"
    assert result["evaluation"]["label"] == "SYNTHETIC" and "synthetic_check_passed" in result["evaluation"]
    code, _, err = run(["marker1", "report", "--location-code", str(location), "--reader-version", "7"], data)
    assert code == 2


def _write(tmp_path: Path, searches: list[str]) -> Path:
    path = tmp_path / "m1_searches.txt"
    path.write_text("\n".join(searches) + "\n", encoding="utf-8")
    return path


def test_budget_prices_show_the_plan_rate_and_the_sample_rate_for_past_pages(tmp_path):
    code, out, _ = run(["budget", "prices", "--json"], tmp_path / "data")
    items = json.loads(out)["plan_items"]
    plan = next(value for name, value in items.items() if "plan rate" in name)
    seen = next(value for name, value in items.items() if "free history samples" in name)
    assert Decimal(plan) == Decimal("14.40") and Decimal("5") < Decimal(seen) < Decimal("6")
    code, out, _ = run(["sample", "historical-serps", "--searches", str(searches_file(tmp_path, 2))], tmp_path / "data")
    assert code == 0 and "plan rate, 1 page per search per month" in out and "sample rate, 0.38 pages" in out


# ---------------------------------------------------------------------------------------------
# Review fixes: dry run cost, seeds, export, samples, marker 1, round mode, status


def test_read_dry_run_prints_what_execute_reserves_for_a_search_with_an_operator(tmp_path):
    from aeo.ranksim.forecast.dataforseo import DataForSeoClient

    code, out, _ = run(["read", "--search", "plumber site:example.com", "--location-code", "1026201", "--json"], tmp_path / "d")
    assert code == 0
    spec = pipeline.search_specs(["plumber site:example.com"], 1026201)[0]
    assert Decimal(json.loads(out)["plan"]["usd"]) == DataForSeoClient._serp_estimate(spec, "standard_normal", True) * 5
    code, out, _ = run(["read-round", "--search", "plumber site:example.com", "--location-code", "1026201", "--json"],
                       tmp_path / "d")
    assert Decimal(json.loads(out)["plan"]["usd"]) == DataForSeoClient._serp_estimate(spec, "standard_normal", True)


def test_read_refuses_a_gap_below_the_floor(tmp_path):
    code, _, err = run(["read", "--search", "plumber", "--location-code", "1026201", "--gap-seconds", "0"], tmp_path / "d")
    assert code == 2 and "at least 60" in err


def test_seeds_pick_into_an_existing_manifest_never_gives_a_niche_search_to_two_sites(tmp_path):
    data = tmp_path / "data"
    manifest = tmp_path / "seed_manifest.json"
    base = ["seeds", "pick", "--candidates", str(candidates_csv(tmp_path, 90)), "--niche", "plumber",
            "--launch-date", "2026-11-30", "--location-code", "1026201", "--out", str(manifest)]
    assert run(base + ["--domain", "one.com", "--domain", "two.com"], data)[0] == 0
    code, _, err = run(base + ["--domain", "three.com"], data)
    assert code == 0, err
    owners: dict[str, list[str]] = {}
    for site in SeedManifest.load(manifest).sites:
        for item in site.searches:
            owners.setdefault(item.search, []).append(site.domain)
    assert all(len(sites) == 1 for sites in owners.values())
    assert run(["seeds", "validate", "--manifest", str(manifest)], data)[0] == 0


def test_seeds_freeze_and_register_refuse_a_list_picked_after_launch(tmp_path):
    data = tmp_path / "data"
    late = pipeline.VirtualClock(datetime(2027, 1, 10, 9, 0, tzinfo=timezone.utc))
    manifest = tmp_path / "seed_manifest.json"
    code, _, err = run(["seeds", "pick", "--candidates", str(candidates_csv(tmp_path)), "--niche", "plumber",
                        "--domain", "one.com", "--launch-date", "2026-11-30", "--location-code", "1026201",
                        "--per-site", "6", "--out", str(manifest)], data, clock=late)
    assert code == 0, err
    code, _, err = run(["seeds", "freeze", "--manifest", str(manifest)], data, clock=late)
    assert code == 2 and "before launch" in err and not (tmp_path / "seed_manifest.json.sha256").exists()
    # Picked in time but frozen after launch: refused by the freeze-time clock.
    early = tmp_path / "early.json"
    run(["seeds", "pick", "--candidates", str(candidates_csv(tmp_path)), "--niche", "plumber", "--domain", "one.com",
         "--launch-date", "2026-11-30", "--location-code", "1026201", "--per-site", "6", "--out", str(early)], data)
    code, _, err = run(["seeds", "freeze", "--manifest", str(early)], data, clock=late)
    assert code == 2 and "frozen" in err
    # A backfilled pilot records why, and the reason is part of the frozen manifest.
    code, out, err = run(["seeds", "freeze", "--manifest", str(early), "--after-launch-reason", "agency site already live",
                          "--json"], data, clock=late)
    assert code == 0, err
    assert SeedManifest.load(early).after_launch_reason == "agency site already live"


def test_export_refuses_to_overwrite_an_export_the_database_no_longer_holds(tmp_path):
    data = tmp_path / "data"
    committed = tmp_path / "committed_export"  # the default export folder of these tests
    clock = pipeline.VirtualClock(START)
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock)
    board.add_site("seedone.com", source="seed", launch_date="2026-12-01", launch_date_source="test", built_by="test")
    for search in ("plumber prices", "boiler repair"):
        board.add_search("seedone.com", search, location_code=1026201)
    board.register_model_version("m1", description="test", frozen_at=clock.now())
    for search in ("plumber prices", "boiler repair"):
        board.record_forecast(Forecast(site="seedone.com", search=search, stage="upload", as_of=date(2026, 11, 23),
                                       point=12, range_low=8, range_high=20, label="contested", model_version="m1"))
    board.close()
    other = tmp_path / "elsewhere"
    assert run(["export"], data)[0] == 0 and (committed / "HEADS.json").exists()
    assert run(["export", "--out", str(other)], data)[0] == 0
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock, export_dir=None)
    with board.engine.begin() as connection:
        connection.execute(text("DROP TRIGGER forecasts_no_delete"))
        connection.execute(text("DELETE FROM forecasts WHERE seq = 2"))
    board.install_triggers()
    board.close()
    before = (other / "HEADS.json").read_text()
    code, _, err = run(["export", "--out", str(other)], data)
    assert code == 1 and "NOT a prefix" in err and (other / "HEADS.json").read_text() == before
    code, out, err = run(["verify"], data)
    assert code == 1
    code, out, err = run(["report", "--source", "seed", "--stage", "upload", "--as-of", "2026-11-23"], data)
    assert code == 1 and "Rows were deleted" in err  # every report checks the committed export


def test_status_says_when_there_is_no_committed_export_and_lists_due_and_missed_stages(tmp_path):
    data = tmp_path / "data"
    board = pipeline.open_scoreboard(forecast_paths(data), clock=pipeline.VirtualClock(START))
    board.add_site("seedone.com", source="seed", launch_date="2026-11-14", launch_date_source="test", built_by="test")
    board.close()
    code, out, _ = run(["status"], data)
    assert code == 0 and "No committed export exists yet" in out
    assert "seedone.com (seed) d14 on 2026-11-28" not in out
    assert "seedone.com (seed) upload was due on 2026-11-14" in out and "d7 was due on 2026-11-21" in out
    code, out, _ = run(["status", "--json"], data)
    payload = json.loads(out)
    assert payload["committed_export"]["exists"] is False
    assert [item["stage"] for item in payload["stages_missed"]] == ["upload", "d7"]
    assert payload["stages_due"] == []
    clock = pipeline.VirtualClock(datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc))
    code, out, _ = run(["status", "--json"], data, clock=clock)
    assert [item["stage"] for item in json.loads(out)["stages_due"]] == ["d14"]


def test_a_sample_that_was_not_recorded_exits_1_and_says_the_gate_stays_closed(tmp_path, fake_credentials):
    from aeo.ranksim.forecast.budget import KIND_SERPER_SEARCH, SpendLedger

    data = tmp_path / "data"
    run(["budget", "approve", "--amount", "2", "--by", "James", "--note", "sample"], data)

    def empty(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"searchParameters": {"q": body["q"]}, "organic": [], "credits": 1})

    code, out, err = run(["sample", "serp", "--searches", str(searches_file(tmp_path)), "--location-code", "1026201",
                          "--provider", "serper", "--serper-location", "Austin,Texas,United States", "--execute"], data,
                         client=httpx.Client(transport=httpx.MockTransport(empty)))
    assert not SpendLedger(data / "spend.jsonl").sample_recorded(KIND_SERPER_SEARCH)
    assert code == 1 and "Sample recorded" not in out and "NOT recorded" in out and "stays closed" in out


def test_marker1_report_on_a_hand_picked_list_is_not_a_marker_result(tmp_path):
    from aeo.ranksim.forecast.reader import ReplaySource
    from aeo.ranksim.forecast.types import OrganicHit, Pull

    data = tmp_path / "data"
    day_a = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)
    day_b = day_a + timedelta(hours=24)
    sites = [f"site{i:02d}.com" for i in range(1, 51)]

    def pulls(spec: PullSpec, moment: datetime, order: list[str]) -> list[Pull]:
        out = []
        for index in range(5):
            stamp = (moment + timedelta(minutes=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
            hits = tuple(OrganicHit(rank=i, url=f"https://{s}/p", domain=s, site=s) for i, s in enumerate(order, start=1))
            out.append(Pull(pull_id=f"p-{spec.search.replace(' ', '')}-{stamp}-{index}", provider="replay", endpoint="replay",
                            spec=spec, requested_at=stamp, completed_at=stamp, ok=True, status_code=20000, hits=hits))
        return out

    clock = pipeline.VirtualClock(day_a)
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock)
    searches = [f"plumber search {i:02d}" for i in range(40)]
    pipeline.freeze_marker1_sample(board, searches, n=40)
    specs = [PullSpec(s, 1026201) for s in searches]
    calm = set(searches[:4])
    recorded: list[Pull] = []
    for spec in specs:
        recorded += pulls(spec, day_a, sites[:30])
        recorded += pulls(spec, day_b, sites[:30] if spec.search in calm else sites[20:30] + sites[10:20] + sites[:10])
    source = ReplaySource(recorded)
    pipeline.take_readings(board, source, specs, clock=clock)
    clock.set(day_b)
    pipeline.take_readings(board, source, specs, clock=clock)
    board.close()
    later = pipeline.VirtualClock(day_b + timedelta(hours=2))
    assert run(["marker1", "report", "--location-code", "1026201"], data, clock=later)[0] == 1
    picked = _write(tmp_path, sorted(calm))
    code, out, _ = run(["marker1", "report", "--searches", str(picked), "--location-code", "1026201"], data, clock=later)
    assert code == 1 and out.startswith("NOT A MARKER RESULT") and "PASSED (REAL)" not in out
    # The frozen list written to a file is still the frozen sample.
    same = _write(tmp_path, list(reversed(searches)))
    code, out, _ = run(["marker1", "report", "--searches", str(same), "--location-code", "1026201"], data, clock=later)
    assert "NOT A MARKER RESULT" not in out


def test_assemble_never_stores_a_clean_reading_from_pulls_seconds_apart(tmp_path, fake_credentials):
    data = tmp_path / "data"
    clock = pipeline.VirtualClock(START)
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "rounds"], data, clock=clock)
    client = httpx.Client(transport=httpx.MockTransport(_serper_handler([])))
    argv = ["read-round", "--searches", str(searches_file(tmp_path, 1)), "--location-code", "1026201", "--provider", "serper",
            "--serper-location", "Austin,Texas,United States", "--execute"]
    for second in range(5):
        clock.set(START + timedelta(seconds=second))
        assert run(argv, data, client=client, clock=clock)[0] == 0
    clock.set(START.replace(hour=23))
    code, out, _ = run(["assemble", "--date", START.date().isoformat(), "--json"], data, clock=clock)
    built = json.loads(out)
    assert built["pulls_too_close"] == 4 and built["readings_built"] - built["degraded"] == 0


def test_assemble_run_early_then_late_keeps_one_reading_and_says_pulls_were_left_out(tmp_path, fake_credentials):
    data = tmp_path / "data"
    clock = pipeline.VirtualClock(START.replace(hour=1))
    run(["budget", "approve", "--amount", "1", "--by", "James", "--note", "rounds"], data, clock=clock)
    client = httpx.Client(transport=httpx.MockTransport(_serper_handler([])))
    argv = ["read-round", "--searches", str(searches_file(tmp_path, 1)), "--location-code", "1026201", "--provider", "serper",
            "--serper-location", "Austin,Texas,United States", "--execute"]
    day = START.date().isoformat()
    for hour in (1, 5, 10):
        clock.set(START.replace(hour=hour))
        assert run(argv, data, client=client, clock=clock)[0] == 0
    clock.set(START.replace(hour=12))
    assert run(["assemble", "--date", day], data, clock=clock)[0] == 0
    for hour in (14, 19):
        clock.set(START.replace(hour=hour))
        assert run(argv, data, client=client, clock=clock)[0] == 0
    clock.set(START.replace(hour=23, minute=30))
    code, out, _ = run(["assemble", "--date", day], data, clock=clock)
    assert code == 1 and "were NOT used" in out
    board = pipeline.open_scoreboard(forecast_paths(data), clock=clock)
    same_day = [r for r in pipeline.stored_readings(board, "plumber 0", location_code=1026201) if r.taken_at.startswith(day)]
    board.close()
    assert len(same_day) == 1


def test_read_round_says_the_paid_pulls_of_a_round_that_failed_part_way_are_stored(tmp_path, monkeypatch):
    summary = pipeline.RoundRunSummary(provider="dataforseo", label="REAL", searches=2, pulls_returned=2, pulls_ok=1,
                                       failed="the budget guard refused the second task_post.", freeze_name="read_round.x",
                                       spent_usd=None, started_at="2026-11-23T09:00:00Z", finished_at="2026-11-23T09:01:00Z")
    monkeypatch.setattr(cli, "_dry_run_or_refuse", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.Context, "source", lambda self, *args, **kwargs: object())
    monkeypatch.setattr(pipeline, "read_round", lambda *args, **kwargs: summary)
    code, out, _ = run(["read-round", "--search", "plumber 0", "--location-code", "1026201", "--execute"], tmp_path / "d")
    assert code == 1
    assert "failed part way" in out and "were paid for and are stored" in out and "stored nothing" not in out


@pytest.mark.parametrize("error", [httpx.ConnectError("name resolution failed"), ConnectionResetError("reset by peer"),
                                   httpx.ReadTimeout("timed out")])
def test_a_network_error_in_any_command_ends_in_one_sentence_and_exit_1(tmp_path, monkeypatch, error):
    """No traceback: the shared catch in main covers core commands and plug-in commands alike."""

    def boom(ctx, args):
        raise error

    monkeypatch.setattr(cli, "cmd_grade", boom)
    code, out, err = run(["grade", "--date", "2026-12-01"], tmp_path)
    assert code == cli.EXIT_CHECK_FAILED
    assert out == ""
    assert err.count("\n") == 1 and err.startswith("A network request failed (") and "run the same command again" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------------------------
# The shared entry point: closed pipes, server status errors, --dir, SYNTHETIC lines


class ClosedPipe(io.StringIO):
    """An output whose reader has gone away, as `forecast status | head -0` leaves it."""

    def write(self, text: str) -> int:
        raise BrokenPipeError(32, "Broken pipe")


def test_a_closed_output_pipe_exits_quietly_with_a_non_zero_code_and_is_not_called_a_network_failure(tmp_path):
    err = io.StringIO()
    code = cli.main(["status", "--dir", str(tmp_path / "data")], clock=pipeline.VirtualClock(START), stdout=ClosedPipe(),
                    stderr=err)
    assert code != 0
    assert err.getvalue() == ""


def test_a_server_error_status_names_the_code_and_the_host_and_says_running_again_will_not_help(tmp_path, monkeypatch):
    request = httpx.Request("GET", "https://api.example.com/v3/list?apikey=SECRET-QUERY-123&page=2")
    error = httpx.HTTPStatusError("404 Not Found", request=request, response=httpx.Response(404, request=request))

    def boom(ctx, args):
        raise error

    monkeypatch.setattr(cli, "cmd_grade", boom)
    code, out, err = run(["grade", "--date", "2026-12-01"], tmp_path)
    assert code == cli.EXIT_CHECK_FAILED and out == ""
    assert err.count("\n") == 1
    assert "404" in err and "api.example.com" in err and "will not help" in err
    assert "SECRET-QUERY-123" not in err and "?" not in err and "/v3/list" not in err
    assert "Check the connection" not in err
    assert not any(dash in err for dash in ("—", "–"))


def test_status_names_open_searches_with_the_one_function_in_forecasting(tmp_path, monkeypatch):
    from aeo.ranksim.forecast import forecasting

    assert not hasattr(cli, "_open_searches")
    monkeypatch.setattr(forecasting, "open_searches", lambda item: " OPEN-SEARCHES-MARK.")
    data = tmp_path / "data"
    board = pipeline.open_scoreboard(forecast_paths(data), clock=pipeline.VirtualClock(START))
    board.add_site("seedone.com", source="seed", launch_date="2026-11-14", launch_date_source="test", built_by="test")
    board.close()
    code, out, _ = run(["status"], data)
    assert "upload was due on 2026-11-14. OPEN-SEARCHES-MARK." in out


def test_status_prints_one_line_for_stored_brand_flags_that_differ_from_the_brand_rule_and_changes_nothing(tmp_path):
    data = tmp_path / "data"
    board = pipeline.open_scoreboard(forecast_paths(data), clock=pipeline.VirtualClock(START))
    board.add_site("x.com", source="seed", launch_date="2026-12-20", launch_date_source="test", built_by="test")
    board.add_search("x.com", "x login", location_code=1026201)
    code, out, _ = run(["status"], data)
    assert "brand flag" not in out  # nothing differs: no line at all
    board.add_search("x.com", "xbox series prices", location_code=1026201, is_brand=True)  # stored by the older substring rule
    board.close()
    code, out, _ = run(["status"], data)
    flagged = [line for line in out.splitlines() if "brand flag" in line]
    assert len(flagged) == 1
    assert flagged[0].startswith("1 stored brand flags differ") and "x.com 'xbox series prices' stored brand" in flagged[0]
    code, out, _ = run(["status", "--json"], data)
    assert json.loads(out)["brand_flag_differences"] == [
        {"site": "x.com", "search": "xbox series prices", "stored": True, "derived": False}]
    board = pipeline.open_scoreboard(forecast_paths(data), clock=pipeline.VirtualClock(START))
    assert len(board.brand_flag_differences()) == 1  # the stored row was not changed
    board.close()


def test_dir_holds_for_the_whole_process_during_one_command_and_the_old_value_comes_back(tmp_path, monkeypatch):
    seen: list[Path] = []

    def handler(ctx, args):
        seen.append(forecast_paths().root)  # no argument: as the content model and the trust model look it up
        return cli.EXIT_OK

    monkeypatch.setattr(cli, "cmd_grade", handler)
    monkeypatch.setenv("RANKSIM_FORECAST_DIR", str(tmp_path / "old"))
    run(["grade", "--date", "2026-12-01"], tmp_path / "chosen")
    assert seen == [(tmp_path / "chosen").resolve()]
    assert forecast_paths().root == tmp_path / "old"

    monkeypatch.delenv("RANKSIM_FORECAST_DIR")

    def crash(ctx, args):
        seen.append(forecast_paths().root)
        raise ValueError("refused")

    monkeypatch.setattr(cli, "cmd_grade", crash)
    code, _, _ = run(["grade", "--date", "2026-12-01"], tmp_path / "second")
    assert code == cli.EXIT_USAGE and seen[-1] == (tmp_path / "second").resolve()
    assert "RANKSIM_FORECAST_DIR" not in os.environ


def test_demo_with_only_dir_writes_the_demo_into_the_dir_folder(tmp_path, monkeypatch):
    calls: list[Path] = []

    def fake_demo(seed, *, workdir, sites):
        calls.append(Path(workdir))
        return _FakeDemo()

    monkeypatch.setattr(pipeline, "demo", fake_demo)
    code, out, err = run(["demo", "--sites", "2"], tmp_path / "chosen")
    assert calls == [tmp_path / "chosen"], err
    assert f"The demo files are in the folder {tmp_path / 'chosen'}" in out
    run_code = cli.main(["demo", "--workdir", str(tmp_path / "work"), "--dir", str(tmp_path / "chosen")],
                        clock=pipeline.VirtualClock(START), stdout=io.StringIO(), stderr=io.StringIO())
    assert run_code == 0 and calls[-1] == tmp_path / "work"  # --workdir still wins


def test_demo_never_writes_its_synthetic_files_into_a_folder_that_is_not_empty(tmp_path, monkeypatch):
    """A real data folder (even one that only holds models) must never receive the SYNTHETIC scoreboard."""
    calls: list[Path] = []

    def fake_demo(seed, *, workdir, sites):
        calls.append(Path(workdir))
        return _FakeDemo()

    monkeypatch.setattr(pipeline, "demo", fake_demo)
    data = tmp_path / "realdata"
    (data / "models").mkdir(parents=True)
    for argv in (["demo", "--sites", "2", "--dir", str(data)], ["demo", "--sites", "2", "--workdir", str(data)]):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, clock=pipeline.VirtualClock(START), stdout=out, stderr=err)
        assert code == cli.EXIT_USAGE and calls == [], argv
        assert "SYNTHETIC" in err.getvalue() and "not empty" in err.getvalue()
    assert sorted(p.name for p in data.iterdir()) == ["models"]


class _FakeDemo:
    lines = ["SYNTHETIC: a stand-in demo."]
    refusals: list = []
    chain = {"ok": True}
    export_check = {"ok": True}

    def to_dict(self) -> dict:
        return {"label": "SYNTHETIC"}


@pytest.mark.parametrize("payload", [{"label": "SYNTHETIC", "rows": 3}, {"report": {"label": "SYNTHETIC"}, "file": "x"}])
def test_every_text_line_of_a_synthetic_payload_says_synthetic_and_json_is_unchanged(payload):
    out = io.StringIO()
    ctx = cli.Context(paths=forecast_paths("unused"), as_json=False, out=out, err=io.StringIO(),
                      clock=pipeline.VirtualClock(START), settings_factory=lambda: None)
    ctx.emit(payload, ["Hit rate is NOT MEASURABLE (18 of 30 needed).", "Already SYNTHETIC. Left alone.", "", "  indented",
                       "Marker 2 (SYNTHETIC).\nPair accuracy is NOT MEASURABLE (24 of 30 needed)."])
    assert out.getvalue().splitlines() == ["SYNTHETIC: Hit rate is NOT MEASURABLE (18 of 30 needed).",
                                           "Already SYNTHETIC. Left alone.", "", "SYNTHETIC:   indented",
                                           "Marker 2 (SYNTHETIC).",
                                           "SYNTHETIC: Pair accuracy is NOT MEASURABLE (24 of 30 needed)."]
    as_json = io.StringIO()
    replace_ctx = cli.Context(paths=forecast_paths("unused"), as_json=True, out=as_json, err=io.StringIO(),
                              clock=pipeline.VirtualClock(START), settings_factory=lambda: None)
    replace_ctx.emit(payload, ["ignored"])
    assert json.loads(as_json.getvalue()) == payload


@pytest.mark.parametrize("payload", [{"label": "REAL"}, {"rows": 1}, {"a": {"label": "REAL"}, "b": {"label": "SYNTHETIC"}},
                                     {"label": "REAL", "report": {"label": "SYNTHETIC"}}])
def test_real_or_unlabeled_output_is_printed_as_it_is(payload):
    out = io.StringIO()
    ctx = cli.Context(paths=forecast_paths("unused"), as_json=False, out=out, err=io.StringIO(),
                      clock=pipeline.VirtualClock(START), settings_factory=lambda: None)
    ctx.emit(payload, ["Pair accuracy 61.0% (REAL)."])
    assert out.getvalue() == "Pair accuracy 61.0% (REAL).\n"


def test_report_and_marker_on_the_synthetic_source_say_synthetic_on_every_line(tmp_path):
    demo = tmp_path / "demo"
    code, _, err = run(["demo", "--workdir", str(demo), "--sites", "2"], tmp_path / "data")
    assert code == 0, err
    for argv in (["report", "--source", "synthetic", "--stage", "upload"], ["marker", "--number", "3", "--source", "synthetic"]):
        code, out, err = run(argv, demo)
        lines = [line for line in out.splitlines() if line.strip()]
        assert lines, (argv, err)
        assert all("SYNTHETIC" in line for line in lines), [line for line in lines if "SYNTHETIC" not in line]


def test_the_forecasting_commands_use_the_shared_network_sentence_and_let_a_closed_pipe_through():
    """One sentence for all commands: a server status error is not a connection problem, a closed pipe is not a network error."""
    from aeo.ranksim.forecast import cli_forecasting

    class Ctx:
        def __init__(self) -> None:
            self.said: list[tuple[str, int]] = []

        def fail(self, message: str, code: int = cli.EXIT_USAGE) -> int:
            self.said.append((message, code))
            return code

    def raising(error: BaseException):
        def handler(ctx, args):
            raise error

        return cli_forecasting.guarded(handler)

    assert cli_forecasting.network_failure is cli.network_failure
    request = httpx.Request("GET", "https://lists.example.org/top.csv?key=secret-value")
    status = httpx.HTTPStatusError("404 Not Found", request=request, response=httpx.Response(404, request=request))
    ctx = Ctx()
    assert raising(status)(ctx, None) == cli.EXIT_CHECK_FAILED
    message = ctx.said[0][0]
    assert "HTTP 404" in message and "lists.example.org" in message and "secret-value" not in message
    assert "Check the connection" not in message
    ctx = Ctx()
    assert raising(httpx.ConnectError("no route"))(ctx, None) == cli.EXIT_CHECK_FAILED
    assert ctx.said[0][0].startswith("A network request failed (")
    with pytest.raises(BrokenPipeError):
        raising(BrokenPipeError(32, "Broken pipe"))(Ctx(), None)


@pytest.mark.parametrize("status, expected", [(404, "will not help"), (401, "will not help"), (429, "wait a little"),
                                              (503, "wait a little"), (408, "wait a little")])
def test_a_server_status_error_says_whether_trying_again_can_help(status, expected):
    request = httpx.Request("GET", "https://lists.example.org/top.csv?key=secret-value")
    error = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(status, request=request))
    message = cli.network_failure(error)
    assert f"HTTP {status}" in message and expected in message and "secret-value" not in message

