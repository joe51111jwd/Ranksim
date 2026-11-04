"""Project wiring: REAL AI engine citations in pulls, the PREDICTED simulation in tests,
and the site history headline slot with its measured accuracy.

Fakes only: the fake SERP and crawler from test_project, httpx.MockTransport
engines from test_engines, and a fake LLM. No network, no real keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_engines import Router, SourceReadingLlm, cite_by_domain, client_for
from test_project import OUR_URL, SEARCHES, SITE, TODAY, FakeCrawler, FakeSerp, page_html

from aeo.config import Settings
from aeo.ranksim import cli
from aeo.ranksim import project as proj
from aeo.ranksim.engines import NO_LLM_STATUS, REAL_LABEL, SIM_LABEL
from aeo.ranksim.project_engines import BING_NOTE, NOT_PULLED_STATUS, latest_engines, load_engines, render_engines_text

DASHES = ("\u2014", "\u2013")
ENGINE_KEY_FIELDS = ("openai_api_key", "anthropic_api_key", "perplexity_api_key", "gemini_api_key", "openrouter_api_key")


def make_settings(tmp_path: Path, **keys) -> Settings:
    values = {
        "database_url": f"sqlite:///{tmp_path / 'aeo.db'}",
        "ranksim_database_url": f"sqlite:///{tmp_path / 'rank.db'}",
        "serpapi_key": None,
        "serper_api_key": None,
        "dataforseo_login": None,
        "dataforseo_password": None,
        "brave_api_key": None,
        "crawl_delay_seconds": 0,
        "ranksim_llm_model": None,
    }
    values.update({name: None for name in ENGINE_KEY_FIELDS})
    values.update(keys)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="module")
def keyless(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("keyless")
    settings = make_settings(tmp)
    project = proj.create_project("Dirt Crew", SITE, SEARCHES, root=tmp / "projects", discover_sitemap=False)
    report = proj.pull(project, settings, serp_provider=FakeSerp(), fetcher=FakeCrawler(), today=TODAY, workers=4)
    ctx = proj.load_project_context(project, today=TODAY, database=settings.ranksim_database_url)
    return {"tmp": tmp, "settings": settings, "project": project, "report": report, "ctx": ctx}


def test_pull_without_engine_keys_records_not_run_and_nothing_else(keyless):
    report = keyless["report"]
    assert [step.step for step in report.steps] == ["google", "crawl", "train", "save"]
    assert report.engines_step is not None and [step.step for step in report.all_steps()][:2] == ["google", "engines"]
    assert report.engines_status == {
        "chatgpt": "not run: needs OPENAI_API_KEY",
        "claude": "not run: needs ANTHROPIC_API_KEY",
        "perplexity": "not run: needs PERPLEXITY_API_KEY",
        "gemini": "not run: needs GEMINI_API_KEY",
    }
    snapshot = load_engines(keyless["project"], report.pull_id)
    assert snapshot is not None and set(snapshot.reports) == set(SEARCHES)
    for data in snapshot.reports.values():
        for summary in data["engines"]:
            assert summary["status"].startswith("not run: needs")
            assert summary["domains"] == [] and summary["our_citation_share"] is None
            assert all(run["citations"] == [] and run["answer"] == "" for run in summary["runs"])
    latest = latest_engines(keyless["project"])
    assert latest.pulls == []
    for rows in latest.rows.values():
        assert [row.status for row in rows] == [
            "not run: needs OPENAI_API_KEY",
            "not run: needs ANTHROPIC_API_KEY",
            "not run: needs PERPLEXITY_API_KEY",
            "not run: needs GEMINI_API_KEY",
        ]
    saved = proj.PullReport.from_dict(json.loads((keyless["project"].dir / "pulls" / report.pull_id / "pull.json").read_text()))
    assert saved.engines_step.step == "engines" and saved.engines_status == report.engines_status


def test_pull_with_keys_saves_real_citations_and_never_the_key(tmp_path):
    settings = make_settings(tmp_path, openai_api_key="sk-test-openai-SECRET", gemini_api_key="gem-test-SECRET")
    project = proj.create_project("Dirt Crew", SITE, SEARCHES, root=tmp_path / "projects", discover_sitemap=False)
    router = Router()
    events: list[proj.ProgressEvent] = []
    report = proj.pull(
        project,
        settings,
        serp_provider=FakeSerp(),
        fetcher=FakeCrawler(),
        today=TODAY,
        engines=["chatgpt", "gemini"],
        engine_runs=2,
        engine_client=client_for(router),
        engine_cache_dir=tmp_path / "engine-cache",
        progress=events.append,
    )
    assert router.count("api.openai.com") == len(SEARCHES) * 2
    assert router.count("generativelanguage.googleapis.com") == len(SEARCHES) * 2
    assert report.engines_status["chatgpt"].startswith("ok, answered 4 of 4 searches")
    assert set(report.engines_status) == {"chatgpt", "gemini"}
    engine_events = [event for event in events if event.step == "engines"]
    assert engine_events and engine_events[-1].done == engine_events[-1].total == len(SEARCHES)
    folder = project.dir / "pulls" / report.pull_id
    for name in ("engines.json", "pull.json"):
        text = (folder / name).read_text()
        assert "SECRET" not in text and not any(dash in text for dash in DASHES)
    latest = latest_engines(project, settings=settings)
    assert latest.pulls == [report.pull_id]
    row = {item.engine: item for item in latest.rows["dirt work software"]}
    assert row["chatgpt"].status == "ok" and row["chatgpt"].label == REAL_LABEL
    assert row["chatgpt"].pulled_at and row["chatgpt"].model and row["chatgpt"].ok_runs == 2
    assert row["chatgpt"].cited and row["chatgpt"].top_domains
    assert row["claude"].status == "not run: needs ANTHROPIC_API_KEY"

    # A later pull without keys keeps the older REAL answers, with their own pull time.
    later = proj.pull(project, make_settings(tmp_path), skip_google=True, fetcher=FakeCrawler(), today=TODAY)
    assert later.engines_status["chatgpt"] == "not run: needs OPENAI_API_KEY"
    carried = latest_engines(project, settings=make_settings(tmp_path))
    kept = {item.engine: item for item in carried.rows["dirt work software"]}["chatgpt"]
    assert kept.status == "ok" and kept.pull_id == report.pull_id
    assert "newest pull got no answer" in kept.note
    text = render_engines_text(project, carried)
    assert "ChatGPT REAL, pulled" in text and BING_NOTE in text and not any(dash in text for dash in DASHES)
    # The key is set now but this pull did not ask: say so instead of "needs".
    keyed = latest_engines(project, settings=make_settings(tmp_path, anthropic_api_key="sk-ant-x"))
    assert {item.engine: item for item in keyed.rows["dirt work software"]}["claude"].status == NOT_PULLED_STATUS


def test_engine_runs_are_validated(keyless):
    with pytest.raises(ValueError):
        proj.pull(keyless["project"], keyless["settings"], skip_google=True, fetcher=FakeCrawler(), engine_runs=0)


def test_board_uses_the_site_history_slot_and_prints_its_accuracy(keyless):
    ctx = keyless["ctx"]
    assert ctx.slot_method == "site history" and ctx.site_model is not None
    assert ctx.site_backtest.mae is not None and ctx.content_backtest.mae is not None
    board = proj.test_page(ctx, page_html("Dirt Work Software", "Dirt Work Software"), OUR_URL, save=False)
    assert board.slot_method == "site history"
    for row in board.searches:
        assert row.site_position is not None and 1 <= row.site_position <= row.site_position_of
        assert row.site_position_of == row.predicted_of
        assert row.site_position_low <= row.site_position <= row.site_position_high
        assert "Editing the page text does not change it" in row.site_note
        assert len(row.above_slot) == row.site_position - 1
        assert [comp.real_position for comp in row.above_slot] == [
            item.position for item in row.real_results if not item.tested_url
        ][: row.site_position - 1]
    assert board.summary.predicted_top3 == sum(1 for row in board.searches if row.site_position <= 3)
    assert board.summary.content_top3 == sum(1 for row in board.searches if row.predicted_position <= 3)
    dirt = board.searches[0]
    assert dirt.real_position == 4 and dirt.site_check.startswith("Check (site history): PREDICTED #")
    text = proj.render_board_text(board)
    assert "In backtests this predictor put a hidden page within 2 places of its REAL Google position" in text
    assert "Content model (unvalidated)" in text
    assert "YOUR PAGE (PREDICTED #" in text and not any(dash in text for dash in DASHES)
    # Content blind among on-topic pages: a weaker on-topic page gets the same headline slot, and the board says so.
    weak = proj.test_page(ctx, page_html("Hi", "Hi", paragraphs=2), OUR_URL, save=False)
    assert all(row.topic.on_topic for row in weak.searches)
    assert [row.site_position for row in weak.searches] == [row.site_position for row in board.searches]
    lead = proj.plain_lead(board)
    assert lead.startswith("PREDICTED: your page is on topic for 4 of your 4 key searches")
    assert "comes from your site, not the page text" in lead and "today" not in lead and "page one" not in lead
    assert proj.plain_lead_from_dict(board.to_dict()) == lead


def test_board_ai_rows_without_keys(keyless):
    board = proj.test_page(keyless["ctx"], page_html("Dirt Work Software", "Dirt Work Software"), OUR_URL, save=False, settings=keyless["settings"])
    for row in board.searches:
        assert [item.status for item in row.engines][0] == "not run: needs OPENAI_API_KEY"
        assert row.simulation is not None and row.simulation.status == NO_LLM_STATUS
        assert row.simulation.label == SIM_LABEL
    assert "not run: needs OPENAI_API_KEY" in board.summary.ai_text and BING_NOTE in board.summary.ai_text
    off = proj.test_page(keyless["ctx"], page_html("A", "A"), OUR_URL, save=False, sim_off="not run: switched off for this test")
    assert all(row.simulation.status == "not run: switched off for this test" for row in off.searches)
    plain = proj.test_page(keyless["ctx"], page_html("A", "A"), OUR_URL, save=False, ai=False)
    assert all(row.engines == [] and row.simulation is None for row in plain.searches) and plain.summary.ai_text == ""


def test_board_runs_the_predicted_simulation_with_an_llm(keyless):
    llm = SourceReadingLlm(cite_by_domain(["dirtcrew", "alpha"]))
    seen: list[tuple[str, int, int]] = []
    board = proj.test_page(
        keyless["ctx"],
        page_html("Dirt Work Software", "Dirt Work Software", extra="dirt work software excavation crew"),
        OUR_URL,
        save=False,
        llm=llm,
        sim_runs=2,
        progress=lambda message, done, total: seen.append((message, done, total)),
    )
    for row in board.searches:
        sim = row.simulation
        assert sim.status == "ok" and sim.label.startswith("PREDICTED simulation")
        assert sim.ok_runs == 2 and sim.your_cited_runs == 2 and sim.your_share == 1.0
        assert any(source["is_yours"] for source in sim.sources)
    assert seen[-1][1] == seen[-1][2] == len(SEARCHES)
    # Page text reaches the LLM fenced as data, with the search, never as instructions.
    system, user = llm.prompts[0]
    assert "untrusted data" in system and "<src-" in user
    assert "AI answer simulation PREDICTED: your page cited in" in board.summary.ai_text
    assert not any(dash in json.dumps(board.to_dict()) for dash in DASHES)


def test_cli_engines_command_and_pull_flags(tmp_path, monkeypatch, capsys):
    for name in ("SERPAPI_KEY", "SERPER_API_KEY", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "BRAVE_API_KEY", "OPENAI_API_KEY",
                 "ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(proj, "_crawler_batch", lambda settings, delay: FakeCrawler())
    root = str(tmp_path / "projects")
    base = ["--database", f"sqlite:///{tmp_path / 'rank.db'}", "project"]
    cli.main([*base, "create", "Dirt Crew", "--site", SITE, "--searches", "; ".join(SEARCHES), "--projects-dir", root])
    csv_path = tmp_path / "serp.csv"
    from test_project import _results

    rows = ["query,url,position"]
    for search, urls in _results().items():
        rows.extend(f"{search},{url},{index}" for index, url in enumerate(urls, start=1))
    csv_path.write_text("\n".join(rows) + "\n")
    cli.main([*base, "import-serp", "dirt-crew", str(csv_path), "--projects-dir", root])
    capsys.readouterr()

    cli.main([*base, "pull", "dirt-crew", "--skip-google", "--engines", "none", "--projects-dir", root])
    out = capsys.readouterr().out
    assert "engines:" not in out
    with pytest.raises(SystemExit) as stopped:
        cli.main([*base, "pull", "dirt-crew", "--skip-google", "--runs", "0", "--projects-dir", root])
    assert stopped.value.code == 2
    cli.main([*base, "pull", "dirt-crew", "--skip-google", "--engines", "chatgpt,claude", "--runs", "2", "--projects-dir", root])
    out = capsys.readouterr().out
    assert "engines:" in out and "not run: needs OPENAI_API_KEY" in out

    cli.main([*base, "engines", "dirt-crew", "--projects-dir", root])
    out = capsys.readouterr().out
    assert "ChatGPT: not run: needs OPENAI_API_KEY" in out and "Gemini: not run: needs GEMINI_API_KEY" in out
    cli.main([*base, "engines", "dirt-crew", "--json", "--projects-dir", root])
    data = json.loads(capsys.readouterr().out)
    assert data["label"] == "REAL" and set(data["searches"]) == set(SEARCHES)

    page = tmp_path / "page.html"
    page.write_text(page_html("Dirt Work Software", "Dirt Work Software"))
    cli.main([*base, "test", "dirt-crew", str(page), "--url", OUR_URL, "--projects-dir", root, "--no-sim"])
    out = capsys.readouterr().out
    assert "In backtests this predictor put a hidden page within 2 places" in out
    assert "not run: switched off with --no-sim" in out
    assert not any(dash in out for dash in DASHES)
