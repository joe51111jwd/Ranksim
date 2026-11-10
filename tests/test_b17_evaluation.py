"""B17 review fixes in the evaluation path: the HTML report after a single-page test, the final-split
registry, same-snapshot wording, withheld intervals, the persistence note and dev-run label output.
Synthetic pages and labels, except where a test reads the repo's own label file and registry."""

import json
import re
import shutil
from pathlib import Path

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.snapshot import final_runs

REPO = Path(__file__).resolve().parents[1]
E12_MODEL = "lgbm-98ceb518acedd909"


def _corpus(tmp_path, *, header="query,url,position", row=None, newline="\n", extra=""):
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = [header]
    for q in range(8):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = f"<html><body><main><h1>Dirt work</h1><p>{words} {'filler ' * 200}</p></main></body></html>"
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            query = f"dirt work software {q}"
            lines.append(row(query, url, position) if row else f"{query},{url},{position}")
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_bytes((newline.join(lines) + newline + extra).encode())
    return db, serp


# C12 ------------------------------------------------------------------------------------------


def test_report_renders_after_a_single_page_test_run(tmp_path):
    from aeo.ranksim.keywords import build_keywords
    from aeo.ranksim.loop import page_from_html, run_test
    from aeo.ranksim.report import render_report
    from aeo.ranksim.store import latest_test, open_session

    db = f"sqlite:///{tmp_path / 'rs.db'}"
    html = "<html><head><title>Plumbing</title></head><body><h1>Plumbing</h1><p>Hello world</p></body></html>"
    session = open_session(db)
    try:
        run_test(session, build_keywords(), page_from_html("https://example.com/plumbing", html, is_ours=True), replaces=None, source_label="example")
        assert isinstance(latest_test(session).summary_json["queries"], int)
    finally:
        session.close()
    page = render_report(db)
    assert "Improved:" in page and "Regressed:" in page


# C13 ------------------------------------------------------------------------------------------


def _refreeze_e12(tmp_path):
    """Freeze the repo's label file (byte-identical to the E12 labels) with the CLI defaults, from a
    database with no pages and no registered runs. Nothing is scored."""
    db = f"sqlite:///{tmp_path / 'fresh.db'}"
    out = tmp_path / "refrozen"
    main(["--database", db, "benchmark-freeze", str(REPO / "smartcrew" / "serp.csv"), "--out", str(out), "--as-of", "2026-11-05"])
    return db, out


def test_the_e12_final_run_is_in_the_git_tracked_registry(tmp_path, capsys):
    from aeo.ranksim.snapshot import load_manifest, snapshot_final_key

    rows = [json.loads(line) for line in (REPO / "smartcrew" / "final_runs.jsonl").read_text().splitlines() if line.strip()]
    _db, out = _refreeze_e12(tmp_path)
    manifest = load_manifest(out)
    assert manifest["labels_sha256"].startswith("4bcc6715f997")  # the E12 labels
    assert sorted(manifest["split"]["final_queries"])[0] == "best heavy civil construction software"
    assert {(row["final_key"], row["model_id"]) for row in rows} >= {(snapshot_final_key(out, manifest), E12_MODEL)}
    for row in rows:  # identity and provenance only: no scores or intervals
        assert "protocols" not in row and "notes" not in row


def test_refreezing_the_e12_labels_does_not_allow_a_second_final_run(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "registry-copy.jsonl"
    source = REPO / "smartcrew" / "final_runs.jsonl"
    if source.is_file():
        shutil.copyfile(source, registry)
    monkeypatch.setenv("RANKSIM_FINAL_RUNS", str(registry))
    db, out = _refreeze_e12(tmp_path)
    assert f"Already scored on it: {E12_MODEL}" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already scored on this final split"):
        main(["--database", db, "benchmark", "--snapshot", str(out), "--final", "--bootstrap", "10"])
    assert final_runs(out) == []


def test_line_endings_and_column_order_do_not_change_the_final_split(tmp_path, capsys):
    db, serp_crlf = _corpus(tmp_path, newline="\r\n")
    main(["--database", db, "benchmark-freeze", str(serp_crlf), "--out", str(tmp_path / "a"), "--as-of", "2026-11-01"])
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), "--final", "--bootstrap", "10"])
    variants = {
        "lf": serp_crlf.read_bytes().replace(b"\r\n", b"\n"),
        "columns": "\n".join(
            ["url,position,query"]
            + [f"{url},{position},{query}" for query, url, position in (line.split(",") for line in serp_crlf.read_text().splitlines()[1:])]
        ).encode()
        + b"\n",
    }
    for name, payload in variants.items():
        other = tmp_path / f"serp-{name}.csv"
        other.write_bytes(payload)
        assert other.read_bytes() != serp_crlf.read_bytes()
        capsys.readouterr()
        main(["--database", db, "benchmark-freeze", str(other), "--out", str(tmp_path / name), "--as-of", "2026-11-01"])
        assert "same final split as an earlier snapshot" in capsys.readouterr().out
        with pytest.raises(SystemExit, match="already scored on this final split"):
            main(["--database", db, "benchmark", "--snapshot", str(tmp_path / name), "--final", "--bootstrap", "10"])
        assert final_runs(tmp_path / name) == []


def test_final_key_changes_when_a_held_out_label_changes():
    from aeo.ranksim.groundtruth import SerpLabel
    from aeo.ranksim.snapshot import final_key

    def label(query, url, position):
        return SerpLabel(query=query, url=url, position=position, prior_position=None, source="manual_csv")

    manifest = {"labels_sha256": "x", "split": {"cluster_threshold": 0.3, "final_queries": ["q final"], "dev_queries": ["q dev"]}}
    base = [label("q final", "https://a.example/", 1), label("q final", "https://b.example/", 2), label("q dev", "https://c.example/", 1)]
    key = final_key(manifest, base)
    assert final_key(manifest, list(reversed(base))) == key
    assert final_key(manifest, base[:2] + [label("q dev", "https://c.example/", 3)]) == key  # a dev row only
    assert final_key(manifest, [label("q final", "https://www.a.example", 1)] + base[1:]) == key  # same page key
    assert final_key(manifest, [label("q final", "https://a.example/", 3)] + base[1:]) != key
    other_split = {**manifest, "split": {**manifest["split"], "final_queries": ["q final", "q dev"]}}
    assert final_key(other_split, base) != key


def test_a_fresh_database_still_refuses_a_recorded_final_run(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "a"), "--as-of", "2026-11-01"])
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "a"), "--final", "--bootstrap", "10"])
    registry = [json.loads(line) for line in (tmp_path / "final_runs.jsonl").read_text().splitlines()]
    assert [row["model_id"] for row in registry] == [final_runs(tmp_path / "a")[0]["model_id"]]
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "b"), "--as-of", "2026-11-01"])
    fresh = f"sqlite:///{tmp_path / 'other-checkout.db'}"
    with pytest.raises(SystemExit, match="already scored on this final split"):
        main(["--database", fresh, "benchmark", "--snapshot", str(tmp_path / "b"), "--final", "--bootstrap", "10"])
    assert final_runs(tmp_path / "b") == []


def test_a_run_recorded_only_in_an_old_results_file_protects_a_refrozen_copy(tmp_path, monkeypatch, capsys):
    """E12 was recorded in its snapshot's results.jsonl before rs_final_runs existed. Opening that
    snapshot once registers it, so a re-frozen copy cannot be scored again by the same model."""
    from aeo.ranksim.features import MODEL_FEATURES
    from aeo.ranksim.lambdamart import model_id
    from aeo.ranksim.snapshot import record_final

    db, serp = _corpus(tmp_path)
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "old"), "--as-of", "2026-11-01"])
    record_final(tmp_path / "old", {"model_id": model_id(list(MODEL_FEATURES)), "created_at": "2026-11-05T22:15:34+00:00"})
    before = (tmp_path / "old" / "results.jsonl").read_bytes()
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "old"), "--bootstrap", "10"])  # a dev run
    assert (tmp_path / "old" / "results.jsonl").read_bytes() == before  # never rewritten
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "new"), "--as-of", "2026-11-01"])
    assert "Already scored on it" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="2026-11-05T22:15:34"):
        main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "new"), "--final", "--bootstrap", "10"])
    assert final_runs(tmp_path / "new") == []


# C15 ------------------------------------------------------------------------------------------


def _accuracy_words(text: str) -> list[str]:
    """Every use of "accuracy" that is not the negation "not accuracy"."""
    return [match.group(0) for match in re.finditer(r"(\S+\s+)?accuracy", text, flags=re.IGNORECASE) if not (match.group(1) or "").lower().startswith("not")]


def test_three_way_and_bundle_output_call_same_snapshot_numbers_agreement():
    from aeo.ranksim.whatif import QueryWhatIf, WhatIfReport, render_three_way, render_whatif

    def query(rank, words=200):
        url = "https://www.smartcrewhq.com/"
        return QueryWhatIf(
            query="all in one dirtwork software", before_n=8, swap_n=8, after_n=8, real_position=None, real_url=None,
            real_crawled=False, before_rank=rank, before_url=url, swap_rank=rank, after_rank=rank, after_url=url,
            after_words=words, content_rank=rank, content_url=url, pages=[], gaps=[], order=[],
        )

    def report(rank, words=200):
        return WhatIfReport("n", 0.592, 0.7, 0.1, 0.5, 1, 1, 1, 0, [query(rank, words)], [])

    text = render_three_way(report(2), report(1), report(3))
    assert "Pairwise agreement: 0.592" in text
    assert _accuracy_words(text) == []
    assert "did not already have" not in text
    assert "joins only the queries it targets" in text
    shell = render_whatif(report(1, words=40))
    assert "short crawl shell" in shell
    assert _accuracy_words(shell) == []


# U4 -------------------------------------------------------------------------------------------


def test_the_adoption_note_withholds_an_interval_below_ten_clusters():
    from aeo.ranksim.benchmark import CANDIDATE_PREFIX, Interval, ProtocolResult, ScorerResult, adoption_decision

    def row(name, point):
        return ScorerResult(name, Interval(point, None, None), None, None, None, 100, 0, 5)

    candidate = row(CANDIDATE_PREFIX + "regularized", 0.531)
    candidate.diff_vs_model = Interval(0.003, -0.060, 0.074)
    note = adoption_decision(ProtocolResult("cluster-url", 8, 8, 0, [row("lambdamart", 0.528), candidate], None), "regularized")
    assert "Not adopted" in note and "only 8 clusters" in note
    assert "95% CI" not in note and "-0.060" not in note and "+0.074" not in note


def test_a_benchmark_below_ten_clusters_prints_and_marks_no_trustworthy_interval(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    out = tmp_path / "bench.json"
    main(["--database", db, "benchmark", str(serp), "--as-of", "2026-11-01", "--protocol", "cluster-url",
          "--bootstrap", "10", "--candidate", "regularized", "--json", str(out)])
    text = capsys.readouterr().out
    assert "Intervals withheld" in text and "Candidate regularized" in text
    assert "95% CI" not in text
    payload = json.loads(out.read_text())
    assert [protocol["interval_trustworthy"] for protocol in payload["protocols"]] == [False]
    assert not any("95% CI" in note for note in payload["notes"])


def test_experiments_e16_and_e17_show_no_interval_from_eight_clusters():
    text = (REPO / "docs" / "EXPERIMENTS.md").read_text()
    for entry in ("E16", "E17"):
        start = text.index(f" {entry}. ")
        body = text[start : text.index("\n## ", start)]
        assert "8 clusters" in body
        assert not re.search(r"\(\d\.\d{3} to \d\.\d{3}\)", body), entry
        assert not re.search(r"95% interval -?\d", body), entry


# U5 -------------------------------------------------------------------------------------------


def test_undated_prior_positions_are_not_reported_as_missing(tmp_path, capsys):
    db, serp = _corpus(tmp_path, header="query,url,position,prior_position", row=lambda q, url, p: f"{q},{url},{p},{p + 1}")
    main(["--database", db, "benchmark", str(serp), "--as-of", "2026-11-01", "--protocol", "loqo", "--bootstrap", "10"])
    text = capsys.readouterr().out
    assert "navboost_prior was withheld" in text
    assert "carry no earlier Google position" not in text
    assert "No persistence row: the labels carry prior_position, but it was withheld" in text


# U12 ------------------------------------------------------------------------------------------


def test_a_dev_run_shows_no_final_split_label(tmp_path, capsys):
    db, serp = _corpus(tmp_path)
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "probe"), "--as-of", "2026-11-01"])
    split = json.loads((tmp_path / "probe" / "manifest.json").read_text())["split"]
    final_query, dev_query = sorted(split["final_queries"])[0], sorted(split["dev_queries"])[0]
    final_n, dev_n = final_query.rsplit(" ", 1)[1], dev_query.rsplit(" ", 1)[1]
    serp.write_text(
        serp.read_text()
        + f"{final_query},https://site{final_n}1.example/page,7\n"
        + f"{dev_query},https://site{dev_n}1.example/page,9\n"
    )
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(tmp_path / "snap"), "--as-of", "2026-11-01"])
    assert json.loads((tmp_path / "snap" / "manifest.json").read_text())["split"] == split
    capsys.readouterr()
    main(["--database", db, "benchmark", "--snapshot", str(tmp_path / "snap"), "--bootstrap", "10", "--json", str(tmp_path / "dev.json")])
    text = capsys.readouterr().out
    notes = json.loads((tmp_path / "dev.json").read_text())["notes"]
    for shown in (text, "\n".join(notes)):
        assert final_query not in shown
        assert f"{dev_query!r} https://site{dev_n}1.example/page at 1 and 9" in shown
        assert "1 duplicate query+URL rows in final-split queries are not shown" in shown
    assert "were not loaded" not in text
    assert "Final-split labels were not scored or shown" in text
