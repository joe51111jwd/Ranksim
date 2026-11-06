"""The tester CLI prints the same product label the result object stores."""

from __future__ import annotations

from datetime import date

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.confidence import Calibration, LooFit
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import RankExample, fit_lambdamart
from aeo.ranksim.tester import SimContext


def test_cli_tester_prints_unsure_for_synthetic_held_out_calibration(tmp_path, capsys, monkeypatch):
    query = "fence cost"
    ours = "https://heldout.invalid/fence"
    rival = "https://rival.invalid/fence"
    labels = [
        SerpLabel(query, ours, 2, None, "manual_csv"),
        SerpLabel(query, rival, 1, None, "manual_csv"),
    ]
    pages = {
        ours: PageDoc(url=ours, title="Hello", body="Hello there from the yard.", word_count=6, is_ours=True),
        rival: PageDoc(url=rival, title="Fence cost", body="Fence cost guide " * 40, word_count=80),
    }
    examples = [
        RankExample(query, ours, 2.0, {"bm25f": 0.1, "word_count": 6}),
        RankExample(query, rival, 1.0, {"bm25f": 0.9, "word_count": 80}),
        RankExample("other query", "https://heldout.invalid/other", 1.0, {"bm25f": 0.4, "word_count": 40}),
        RankExample("other query", "https://rival.invalid/other", 2.0, {"bm25f": 0.2, "word_count": 20}),
    ]
    model = fit_lambdamart(examples, ["bm25f", "word_count"])
    calibration = Calibration(0.01, 0.99, 0.9, "Held-out check passed.", held_out=True)
    assert calibration.label(2) == "CLEARER"
    assert calibration.product_label(2, synthetic=False)[0] == "CLEARER"
    assert calibration.product_label(2, synthetic=True)[0] == "UNSURE"
    ctx = SimContext(
        labels=labels,
        pages=pages,
        fit=LooFit(models={query: model}, calibration=calibration, n_queries=2, n_pairs=2, pairwise=0.5),
        intents={query: "commercial"},
        queries=[query],
        today=date(2026, 11, 6),
        database=f"sqlite:///{tmp_path / 'rank.db'}",
        serp_file="fixture.csv",
        synthetic=True,
    )
    monkeypatch.setattr("aeo.ranksim.tester.load_sim_context", lambda *args, **kwargs: ctx)
    html = tmp_path / "page.html"
    html.write_text("<html><head><title>Fence cost</title></head><body><h1>Fence cost</h1><p>" + ("Fence cost guide. " * 30) + "</p></body></html>")
    serp = tmp_path / "serp.csv"
    serp.write_text(f"query,url,position\n{query},{ours},2\n{query},{rival},1\n")
    main(
        [
            "--database",
            f"sqlite:///{tmp_path / 'rank.db'}",
            "tester",
            str(html),
            "--url",
            ours,
            "--queries",
            query,
            "--serp",
            str(serp),
            "--history",
            str(tmp_path / "history.jsonl"),
            "--json",
        ]
    )
    printed = capsys.readouterr().out
    assert '"confidence": "UNSURE"' in printed
    assert "CLEARER" not in printed


def test_loaded_synthetic_demo_stays_unsure_when_holdout_would_be_clearer(tmp_path, monkeypatch):
    from aeo.ranksim import workspace as ws
    from aeo.ranksim.confidence import loo_fit as real_loo
    from aeo.ranksim.groundtruth import load_labels
    from aeo.ranksim.tester import _score_query, load_sim_context
    from test_workspace import make_settings

    settings = make_settings(tmp_path)
    project, _report = ws.load_synthetic_demo(tmp_path / "projects", settings, settings.ranksim_database_url)
    loaded = load_labels(project.dir / "label.json")
    assert loaded.source == "synthetic"
    assert loaded.rows

    def held_out(examples, feature_names=None, keep_models=False):
        fit = real_loo(examples, feature_names, keep_models=keep_models)
        fit.calibration = Calibration(0.01, 0.99, 0.5, "Held-out check passed.", held_out=True)
        return fit

    monkeypatch.setattr("aeo.ranksim.tester.loo_fit", held_out)
    ctx = load_sim_context(settings.ranksim_database_url, str(project.dir / "label.json"), graph_path=None)
    assert ctx.synthetic is True
    assert ctx.fit.calibration.product_label(2, synthetic=False)[0] == "CLEARER"
    row = loaded.rows[0]
    html = ws.synthetic_page(row.url) or "<html><title>Vinyl</title><body><p>Vinyl fence.</p></body></html>"
    scored, _words = _score_query(ctx, row.url, html, row.query)
    assert scored.confidence == "UNSURE"
    assert scored.error == ""


def test_google_auto_and_imported_sources_are_not_synthetic(tmp_path, monkeypatch):
    from aeo.ranksim.confidence import loo_fit as real_loo
    from aeo.ranksim.groundtruth import live_google_source, load_labels, synthetic_source, write_label_set
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session
    from aeo.ranksim.tester import load_sim_context

    database = f"sqlite:///{tmp_path / 'rank.db'}"
    query = "fence cost"
    rows = [
        SerpLabel(query, "https://heldout.invalid/a", 1, None, "google_auto"),
        SerpLabel(query, "https://heldout.invalid/b", 2, None, "google_auto"),
        SerpLabel("other query", "https://heldout.invalid/c", 1, None, "google_auto"),
        SerpLabel("other query", "https://heldout.invalid/d", 2, None, "google_auto"),
    ]
    session = open_session(database)
    try:
        for row in rows:
            store_fetched_page(session, FetchResult(url=row.url, html=f"<html><title>{row.query}</title><body><p>{row.query} page.</p></body></html>", status="fetched"))
    finally:
        session.close()
    google = tmp_path / "google"
    write_label_set(google / "label.json", "google_auto", "2020-01-02", rows)
    imported = tmp_path / "imported"
    write_label_set(imported / "label.json", "imported_serp", "2020-01-03", rows)

    def held_out(examples, feature_names=None, keep_models=False):
        fit = real_loo(examples, feature_names, keep_models=keep_models)
        fit.calibration = Calibration(0.01, 0.99, 0.5, "Held-out check passed.", held_out=True)
        return fit

    monkeypatch.setattr("aeo.ranksim.tester.loo_fit", held_out)
    google_ctx = load_sim_context(database, str(google / "label.json"), graph_path=None)
    imported_ctx = load_sim_context(database, str(imported / "label.json"), graph_path=None)
    assert load_labels(google / "label.json").source == "google_auto"
    assert live_google_source("google_auto") and not synthetic_source("google_auto")
    assert google_ctx.synthetic is False
    assert imported_ctx.synthetic is False
    assert any("Not a live Google pull" in note for note in imported_ctx.notes)
    assert not any("Not a live Google pull" in note for note in google_ctx.notes)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "label.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="No confidence call was made"):
        load_sim_context(database, str(broken / "label.json"), graph_path=None)
    missing = tmp_path / "missing"
    missing.mkdir()
    (missing / "label.json").write_text('{"rows": [{"query": "q", "url": "https://heldout.invalid/a", "position": 1}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="no source"):
        load_sim_context(database, str(missing / "label.json"), graph_path=None)


def test_older_import_csv_is_scored_instead_of_the_newer_project_label_json(tmp_path, monkeypatch):
    """An explicit CSV under imports/ keeps its own rows when project/label.json is newer."""
    from aeo.ranksim.confidence import loo_fit as real_loo
    from aeo.ranksim.groundtruth import load_labels
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.project import create_project, import_serp_csv
    from aeo.ranksim.store import open_session
    from aeo.ranksim.tester import load_sim_context

    database = f"sqlite:///{tmp_path / 'rank.db'}"
    project = create_project("Acme", "acme.example", ["old fence cost", "new fence cost"], root=tmp_path / "projects", discover_sitemap=False)
    imports = project.dir / "imports"
    imports.mkdir()
    old_csv = imports / "20200101T000000Z-old.csv"
    new_csv = imports / "20200102T000000Z-new.csv"
    old_csv.write_text(
        "query,url,position\n"
        "old fence cost,https://old.invalid/a,1\n"
        "old fence cost,https://old.invalid/b,2\n",
        encoding="utf-8",
    )
    new_csv.write_text(
        "query,url,position\n"
        "new fence cost,https://new.invalid/a,1\n"
        "new fence cost,https://new.invalid/b,2\n",
        encoding="utf-8",
    )
    import_serp_csv(project, old_csv, "2020-01-02", "uploaded CSV (positions seen 2020-01-02)")
    import_serp_csv(project, new_csv, "2020-01-03", "uploaded CSV (positions seen 2020-01-03)")
    latest = load_labels(project.dir / "label.json")
    assert latest.source == "imported_serp"
    assert {row.url for row in latest.rows} == {"https://new.invalid/a", "https://new.invalid/b"}
    session = open_session(database)
    try:
        for url in ("https://old.invalid/a", "https://old.invalid/b", "https://new.invalid/a", "https://new.invalid/b"):
            store_fetched_page(session, FetchResult(url=url, html=f"<html><title>{url}</title><body><p>{url} body.</p></body></html>", status="fetched"))
    finally:
        session.close()
    seen: list = []

    def held_out(examples, feature_names=None, keep_models=False):
        seen.extend(examples)
        fit = real_loo(examples, feature_names, keep_models=keep_models)
        fit.calibration = Calibration(0.01, 0.99, 0.5, "Held-out check passed.", held_out=True)
        return fit

    monkeypatch.setattr("aeo.ranksim.tester.loo_fit", held_out)
    ctx = load_sim_context(database, str(old_csv), graph_path=None)
    assert {(row.query, row.url, row.position) for row in ctx.labels} == {
        ("old fence cost", "https://old.invalid/a", 1.0),
        ("old fence cost", "https://old.invalid/b", 2.0),
    }
    assert {row.url for row in seen} <= {"https://old.invalid/a", "https://old.invalid/b"}
    assert seen
    assert ctx.synthetic is False
    assert any("not a live Google pull" in note for note in ctx.notes)
    assert ctx.fit.calibration.held_out is True
    assert ctx.fit.calibration.product_label(2, synthetic=False)[0] == "CLEARER"
