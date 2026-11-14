"""Draft placement, persistence anchor, multi-search test, guidance, and honest labels."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from aeo.ranksim import predict_service
from aeo.ranksim.loop import page_from_html
from aeo.ranksim.predict import (
    ACTIONABLE,
    CLEARER,
    UNSURE,
    Group,
    anchored_position,
    confidence,
    expected_position,
    fit_pairwise,
    guidance,
    term_match,
)

REPO = Path(__file__).resolve().parents[1]
SEARCHES = ["hcss alternative", "dirt work software", "excavation estimating app", "heavy civil platform"]


def page(title: str, h1: str, words: int, *, h2: int = 0, schema: bool = False, table: bool = False, body_terms: str = "") -> str:
    parts = [f"<html><head><title>{title}</title>"]
    if schema:
        parts.append('<script type="application/ld+json">{"@type":"Article"}</script>')
    parts.append(f"</head><body><main><h1>{h1}</h1>")
    for index in range(h2):
        parts.append(f"<h2>Section {index} {body_terms}</h2>")
    filler = " ".join(f"word{index % 97}" for index in range(words))
    parts.append(f"<p>{body_terms} {filler}</p>")
    if table:
        parts.append("<table><tr><td>a</td></tr></table>")
    parts.append("</main></body></html>")
    return "".join(parts)


def strong(search: str, rank: int) -> str:
    # Higher ranked pages put the search words in the title and carry structured data.
    # Length and subheadings do not follow rank, so the signal is clean.
    title = search if rank <= 4 else "Company blog"
    return page(title, "Welcome", 400 + (rank * 37) % 90, h2=2 + rank % 3, schema=rank <= 5 and rank % 2 == 1, body_terms=search)


OURS = page("Heavy civil and dirt work software", "Software for contractors", 360, h2=3, body_terms="hcss alternative dirt work software")


def make_ctx():
    pages = {}
    by_search = {}
    for s_index, search in enumerate(SEARCHES):
        results = []
        for rank in range(1, 9):
            url = f"https://site{rank}-{s_index}.example/{search.replace(' ', '-')}"
            html = strong(search, rank)
            if rank == 3:
                url = "https://ours.example/main"  # our page ranks #3 on every search
                html = OURS
            pages[url] = page_from_html(url, html, is_ours=False)
            results.append(SimpleNamespace(url=url, position=rank))
        by_search[search] = SimpleNamespace(results=results)
    google = SimpleNamespace(by_search=lambda: by_search)
    project = SimpleNamespace(searches=list(SEARCHES))
    return SimpleNamespace(project=project, google=google, pages=pages, database=None)


@pytest.fixture()
def ctx():
    predict_service._CACHE.clear()
    return make_ctx()


def test_unchanged_page_stays_at_its_observed_position(ctx):
    url = "https://ours.example/main"
    out = predict_service.test_searches(ctx, OURS, url, searches=SEARCHES)
    for row in out["searches"]:
        assert row["method"] == "anchored"
        assert row["observed_position"] == 3.0
        assert row["now"] == 3.0
        assert row["with_edit"] == 3.0
        assert row["move"] == 0.0


def test_anchor_moves_by_the_edit_and_not_from_scratch(ctx):
    url = "https://ours.example/main"
    better = page("hcss alternative", "Software for contractors", 360, h2=3, body_terms="hcss alternative dirt work software")
    worse = page("Company blog", "Software for contractors", 360, h2=3, body_terms="dirt work software")
    up = predict_service.test_searches(ctx, better, url, searches=["hcss alternative"])["searches"][0]
    down = predict_service.test_searches(ctx, worse, url, searches=["hcss alternative"])["searches"][0]
    assert up["now"] == down["now"] == 3.0  # anchored on the real position either way
    assert up["with_edit"] < 3.0 < down["with_edit"]
    assert up["move"] > 0 > down["move"]
    assert 1.0 <= up["with_edit"] and down["with_edit"] <= up["candidates"]


def test_anchored_position_zero_change_is_exact():
    model = fit_pairwise([Group("q", [{"a": 1.0}, {"a": 0.0}], [1, 2])], ["a"])
    signals = {"a": 0.5}
    out = anchored_position(model, 4.0, signals, signals, [({"a": 1.0}, 1.0), ({"a": 0.0}, 7.0)], persistence_slope=0.4)
    assert out["predicted"] == 4.0
    assert out["score_change"] == 0.0


def test_multi_search_call_returns_every_requested_search(ctx):
    draft = page("Dirt work software and excavation estimating app", "Dirt work software", 500, h2=4, schema=True, body_terms=" ".join(SEARCHES))
    out = predict_service.test_searches(ctx, draft, "https://ours.example/new-page", searches=SEARCHES)
    assert out["label"] == "PREDICTED"
    assert [row["search"] for row in out["searches"]] == SEARCHES
    for row in out["searches"]:
        assert row["label"] == "PREDICTED"
        assert row["method"] == "new page"
        assert row["now"] is None  # no original was given and the page does not rank
        assert 1.0 <= row["with_edit"] <= row["candidates"]
        assert row["confidence"]["label"] in {UNSURE, CLEARER}
        assert "backtest_pairs" in row["confidence"]


def test_default_targets_are_ranked_and_matching_searches(ctx):
    draft = page("Dirt work software", "Dirt work software", 500)
    out = predict_service.test_searches(ctx, draft, "https://ours.example/main")
    names = [row["search"] for row in out["searches"]]
    assert set(names) == set(SEARCHES)  # ours.example/main ranks on all four
    fresh = predict_service.test_searches(ctx, draft, "https://ours.example/brand-new")
    assert [row["search"] for row in fresh["searches"]] == ["dirt work software"]


def test_new_page_with_original_reports_a_move(ctx):
    original = page("Company blog", "Welcome", 120)
    draft = page("heavy civil platform", "heavy civil platform", 400, h2=5, schema=True, body_terms="heavy civil platform")
    row = predict_service.test_searches(ctx, draft, "https://ours.example/landing", searches=["heavy civil platform"], original_html=original)["searches"][0]
    assert row["now"] is not None and row["with_edit"] is not None
    assert row["move"] == pytest.approx(row["now"] - row["with_edit"], abs=0.11)


def test_guidance_is_stable_and_only_uses_positive_stable_features(ctx):
    draft = page("Notes", "Notes", 420, h2=2, body_terms=" ".join(SEARCHES))
    first = predict_service.test_searches(ctx, draft, "https://ours.example/x", searches=SEARCHES)
    second = predict_service.test_searches(ctx, draft, "https://ours.example/x", searches=SEARCHES)
    assert [row["guidance"] for row in first["searches"]] == [row["guidance"] for row in second["searches"]]
    predictor = predict_service.predictor_for(ctx)
    weights = dict(zip(predictor.model.features, predictor.model.weights))
    seen = 0
    for row in first["searches"]:
        assert len(row["guidance"]) <= 4
        for item in row["guidance"]:
            seen += 1
            assert item["feature"] in ACTIONABLE
            assert weights[item["feature"]] > 0
            assert predictor.stable[item["feature"]] == 1
            assert item["gain"] > 0
            assert item["text"]
    assert seen > 0
    assert "not a guarantee" in first["guidance_note"]


def test_off_topic_draft_gets_no_position(ctx):
    out = predict_service.test_searches(ctx, page("Notes", "Notes", 300), "https://ours.example/x", searches=SEARCHES)
    for row in out["searches"]:
        assert row["method"] == "not placed"
        assert row["with_edit"] is None and row["move"] is None
        assert row["confidence"]["label"] == UNSURE


def test_guidance_lists_nothing_when_no_page_is_above():
    model = fit_pairwise([Group("q", [{"title_match": 1.0}, {"title_match": 0.0}], [1, 2])], ["title_match"])
    assert guidance(model, {"title_match": 0.0}, []) == []
    items = guidance(model, {"title_match": 0.0}, [{"title_match": 1.0}])
    assert items and items[0]["feature"] == "title_match"


def test_confidence_labels():
    assert confidence(None, 0)["label"] == UNSURE
    near = confidence(0.52, 200)
    assert near["label"] == UNSURE and near["near_chance"]
    assert confidence(0.7, 10)["label"] == UNSURE
    clearer = confidence(0.7, 40)
    assert clearer["label"] == CLEARER and clearer["backtest_pairs"] == 40
    for case in (near, clearer):
        assert "accura" not in case["text"].lower()
        assert "backtest" in case["text"].lower()


def _walk(value, path=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}")
            yield f"{path}.{k}", k
    elif isinstance(value, list):
        for index, v in enumerate(value):
            yield from _walk(v, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def test_nothing_in_the_test_output_calls_a_backtest_accuracy(ctx):
    draft = page("hcss alternative", "hcss alternative", 300)
    out = predict_service.test_searches(ctx, draft, "https://ours.example/main", searches=SEARCHES)
    for path, text in _walk(out):
        lowered = text.lower().replace("not accuracy", "")
        assert "accuracy" not in lowered, path
        assert "—" not in text and "–" not in text, path  # no em or en dashes
    assert "Not accuracy" in out["backtest_note"]


def test_committed_eval_never_labels_training_as_accuracy():
    folder = REPO / "evals" / "predict-2026-11-14"
    training = json.loads((folder / "training.json").read_text())
    assert "Not accuracy" in training["selection"]["method"]
    assert "2026-11-09" in training["protocol"] and "not read" in training["protocol"]
    report = json.loads((folder / "report.json").read_text())
    assert report["label"].startswith("PREDICTED")
    assert report["training_sha256"]
    assert "fetched again" in report["html"]["caveat"]
    cov = report["coverage"]
    assert cov["entrant_rows"] == cov["entrant_rows_scorable"] + cov["entrant_rows_without_html"]
    # The original forward report is untouched by this work.
    assert (REPO / "evals" / "forward-2026-11-09" / "report.json").exists()


def test_fit_pairwise_learns_the_sign_and_entrants_start_lower():
    groups = [
        Group(f"q{n}", [{"a": 3.0}, {"a": 2.0}, {"a": 1.0}, {"a": 0.0}], [1, 2, 3, 4]) for n in range(3)
    ]
    model = fit_pairwise(groups, ["a"], l2=0.3)
    assert model.weights[0] > 0
    others = [{"a": 3.0}, {"a": 0.0}]
    plain = expected_position(model, {"a": 1.5}, others, is_new=True)
    model.entrant_logit = -1.0
    handicapped = expected_position(model, {"a": 1.5}, others, is_new=True)
    assert handicapped > plain
    assert math.isclose(plain, 2.0, abs_tol=1e-6)


def test_term_match_joins_split_words():
    assert term_match("all in one dirtwork software", "All-in-one dirt work software") >= 0.8
    assert term_match("hcss alternative", "Company blog") == 0.0


def test_unknown_searches_are_refused(ctx):
    with pytest.raises(predict_service.PredictError):
        predict_service.test_searches(ctx, page("x", "x", 100, body_terms="hcss"), "https://ours.example/x", searches=["not a project search"])


def test_backtest_hides_the_held_out_family_from_site_features(ctx, monkeypatch):
    seen = []
    real = predict_service._groups

    def spy(items, rows):
        seen.append({row[0] for row in rows})
        return real(items, rows)

    monkeypatch.setattr(predict_service, "_groups", spy)
    predict_service.build_predictor(ctx)
    assert seen[0] == set(SEARCHES)  # the final model sees everything
    assert all(len(rows) < len(SEARCHES) for rows in seen[1:])  # each fold hides its family


def test_one_family_project_says_why_every_call_is_unsure():
    predict_service._CACHE.clear()
    ctx = make_ctx()
    variants = ["dirt work software", "best dirt work software", "dirt work software tool"]
    by = ctx.google.by_search()
    ctx.project.searches = variants
    remapped = {name: by[SEARCHES[index]] for index, name in enumerate(variants)}
    ctx.google = SimpleNamespace(by_search=lambda: remapped)
    out = predict_service.test_searches(ctx, page("Dirt work software", "Dirt work software", 400, body_terms="dirt work software"), "https://ours.example/new", searches=variants)
    assert all(row["confidence"]["label"] == UNSURE for row in out["searches"])
    assert all(row["confidence"].get("one_family") for row in out["searches"])
    assert any("close variant" in note for note in out["notes"])


def test_a_site_with_no_rankings_gets_a_plain_note(ctx):
    out = predict_service.test_searches(ctx, page("hcss alternative", "hcss alternative", 400, body_terms="hcss alternative"), "https://brandnew.example/page", searches=SEARCHES[:1])
    assert any("brandnew.example is not in the top 10" in note for note in out["notes"])
    ranked = predict_service.test_searches(ctx, OURS, "https://ours.example/main", searches=SEARCHES[:1])
    assert not any("is not in the top 10" in note for note in ranked["notes"])


def test_summary_and_short_lines(ctx):
    out = predict_service.test_searches(ctx, OURS, "https://ours.example/main", searches=SEARCHES)
    summary = out["summary"]
    assert summary["searches"] == 4 and summary["same"] == 4
    for row in out["searches"]:
        assert row["confidence"]["short"].split(":")[0] in {"Unsure", "Clearer", "Not placed"}
        assert len(row["confidence"]["short"]) <= 80


def test_predictor_is_cached_on_disk_per_pull(tmp_path):
    predict_service._CACHE.clear()
    ctx = make_ctx()
    ctx.project.dir = str(tmp_path)
    ctx.pull_id = "pull-1"
    draft = page("Dirt work software", "Dirt work software", 500, h2=4, body_terms=" ".join(SEARCHES))
    first = predict_service.test_searches(ctx, draft, "https://ours.example/new", searches=SEARCHES)
    files = list((tmp_path / "plugins" / "predict").glob("predictor-pull-1-*.json"))
    assert len(files) == 1
    predict_service._CACHE.clear()  # a new instance: memory is empty, the file is reused

    def boom(_ctx):
        raise AssertionError("rebuilt instead of reading the cache")

    original = predict_service.build_predictor
    predict_service.build_predictor = boom
    try:
        again = predict_service.test_searches(ctx, draft, "https://ours.example/new", searches=SEARCHES)
    finally:
        predict_service.build_predictor = original
    assert again["searches"] == first["searches"]
    ctx.pull_id = "pull-2"
    predict_service._CACHE.clear()
    predict_service.test_searches(ctx, draft, "https://ours.example/new", searches=SEARCHES)
    names = [path.name for path in (tmp_path / "plugins" / "predict").glob("predictor-*.json")]
    assert len(names) == 1 and names[0].startswith("predictor-pull-2-")
