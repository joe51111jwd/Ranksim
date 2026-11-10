"""B16: a second LambdaMART configuration fixed in advance, compared on identical folds and adopted
only by a rule written before it was measured."""

import pytest

from aeo.ranksim.benchmark import (
    ADOPTION_MARGIN,
    CANDIDATE_PREFIX,
    Interval,
    ProtocolResult,
    ScorerResult,
    adoption_decision,
    in_sample_pairwise,
    lambdamart_scorer,
)
from aeo.ranksim.features import MODEL_FEATURES
from aeo.ranksim.lambdamart import RankExample, config_params, model_id


def test_the_default_model_id_is_the_one_recorded_and_the_candidate_has_its_own():
    assert model_id(list(MODEL_FEATURES)) == "lgbm-98ceb518acedd909"  # recorded in E12
    assert model_id(list(MODEL_FEATURES), config="regularized") != model_id(list(MODEL_FEATURES))
    params, rounds = config_params("regularized")
    assert params["label_gain"] == [float(gain) for gain in range(11)] and rounds == 60
    with pytest.raises(ValueError, match="Unknown model config"):
        config_params("tuned-on-final")


def _protocol(diff: Interval, clusters: int) -> ProtocolResult:
    def row(name, point):
        return ScorerResult(name, Interval(point, None, None), None, None, None, 100, 0, 5)

    candidate = row(CANDIDATE_PREFIX + "regularized", 0.6)
    candidate.diff_vs_model = diff
    return ProtocolResult("cluster-url", clusters, clusters, 0, [row("lambdamart", 0.5), candidate], None)


def test_the_adoption_rule_needs_margin_interval_and_clusters():
    clear = Interval(ADOPTION_MARGIN + 0.05, 0.02, 0.15)
    assert "Meets the adoption rule" in adoption_decision(_protocol(clear, 12), "regularized")
    few = adoption_decision(_protocol(clear, 8), "regularized")
    assert "Not adopted" in few and "only 8 clusters" in few
    small = adoption_decision(_protocol(Interval(0.03, 0.01, 0.05), 12), "regularized")
    assert "under 0.05" in small
    noisy = adoption_decision(_protocol(Interval(0.08, -0.01, 0.15), 12), "regularized")
    assert "does not exclude 0" in noisy


def test_in_sample_agreement_is_reported_for_a_configuration():
    examples = [
        RankExample(f"q{q}", f"https://q{q}p{p}.example/", float(p), {"bm25f": float(5 - p), "bm25_title": float(5 - p)})
        for q in range(4)
        for p in range(1, 5)
    ]
    assert in_sample_pairwise(examples, lambdamart_scorer(["bm25f", "bm25_title"], config="regularized")) == 1.0


def test_a_candidate_is_not_allowed_on_the_final_split(tmp_path, capsys):
    from aeo.ranksim.cli import main
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.snapshot import final_runs
    from aeo.ranksim.store import open_session

    db = f"sqlite:///{tmp_path / 'live.db'}"
    session = open_session(db)
    lines = ["query,url,position"]
    for q in range(8):
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/page"
            words = " ".join(["dirt", "work", "software"][: 4 - position] * (60 // position))
            html = f"<html><body><main><h1>Dirt work</h1><p>{words} {'filler ' * 200}</p></main></body></html>"
            store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
            lines.append(f"dirt work software {q},{url},{position}")
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text("\n".join(lines) + "\n")
    snap = tmp_path / "snap"
    main(["--database", db, "benchmark-freeze", str(serp), "--out", str(snap), "--as-of", "2026-11-01"])
    with pytest.raises(SystemExit, match="--candidate is for the dev split"):
        main(["benchmark", "--snapshot", str(snap), "--final", "--candidate", "regularized", "--bootstrap", "10"])
    assert final_runs(snap) == []  # nothing was scored or recorded
