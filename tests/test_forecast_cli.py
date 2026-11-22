"""The forecast command line (forecast/cli.py and the forwarder in aeo/ranksim/cli.py).

No network, no keys, no real sleeping. Every HTTP client handed to the commands
here either fails the test when it is called or is an httpx.MockTransport.
"""

from __future__ import annotations

import csv
import io
import json
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
from aeo.ranksim.forecast.synth import World

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
def _no_scoreboard_override(monkeypatch):
    monkeypatch.delenv("RANKSIM_SCOREBOARD_URL", raising=False)
    monkeypatch.delenv("RANKSIM_FORECAST_DIR", raising=False)


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


def test_exit_codes(tmp_path):
    data = tmp_path / "data"
    assert run(["no-such-command"], data)[0] == 2
    assert run(["budget"], data)[0] == 2
    assert run(["status"], data)[0] == 0
    assert run(["marker", "--number", "2", "--source", "seed"], data)[0] == 2
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
