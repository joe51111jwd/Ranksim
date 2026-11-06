"""The tester CLI prints the same product label the result object stores."""

from __future__ import annotations

from datetime import date

from aeo.ranksim.cli import main
from aeo.ranksim.confidence import Calibration, LooFit
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import RankExample, fit_lambdamart
from aeo.ranksim.tester import SimContext


def test_cli_tester_prints_unsure_for_an_in_sample_threshold(tmp_path, capsys, monkeypatch):
    query = "fence cost"
    ours = "https://acme.example/fence"
    rival = "https://rival.example/fence"
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
        RankExample("other query", "https://acme.example/other", 1.0, {"bm25f": 0.4, "word_count": 40}),
        RankExample("other query", "https://rival.example/other", 2.0, {"bm25f": 0.2, "word_count": 20}),
    ]
    model = fit_lambdamart(examples, ["bm25f", "word_count"])
    calibration = Calibration(0.01, 0.99, 0.9, "In-sample threshold chosen on the same pairs.", held_out=False)
    assert calibration.label(2) == "CLEARER"
    ctx = SimContext(
        labels=labels,
        pages=pages,
        fit=LooFit(models={query: model}, calibration=calibration, n_queries=2, n_pairs=2, pairwise=0.5),
        intents={query: "commercial"},
        queries=[query],
        today=date(2026, 11, 6),
        database=f"sqlite:///{tmp_path / 'rank.db'}",
        serp_file="fixture.csv",
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
