"""Topic gate edges. Floors stay put. Low evidence is UNSURE, not a false off-topic drop."""

from __future__ import annotations

from pathlib import Path

import pytest

from aeo.config import Settings
from aeo.ranksim import workspace as ws
from aeo.ranksim.features import PageDoc
from aeo.ranksim.loop import page_from_html
from aeo.ranksim.project import load_project_context
from aeo.ranksim.relevance import TOPIC_FLOOR_DEFAULT, TOPIC_MARGIN, TopicModel

QUERY = "vinyl fence installation cost"


def _settings(tmp: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp / 'aeo.db'}",
        ranksim_database_url=f"sqlite:///{tmp / 'rank.db'}",
        serpapi_key=None,
        serper_api_key=None,
        dataforseo_login=None,
        dataforseo_password=None,
        brave_api_key=None,
        openai_api_key=None,
        anthropic_api_key=None,
        openrouter_api_key=None,
        perplexity_api_key=None,
        gemini_api_key=None,
        crawl_delay_seconds=0,
        ranksim_llm_model=None,
    )


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("topic")
    settings = _settings(tmp)
    project, report = ws.load_synthetic_demo(tmp / "projects", settings, settings.ranksim_database_url)
    ctx = load_project_context(project, database=settings.ranksim_database_url)
    return {"ctx": ctx, "report": report}


def _clone(url: str) -> PageDoc:
    sentence = (
        "When people search for vinyl fence installation cost, they usually want to know about vinyl and fence. "
        "Here is how installation affects the result, and why cost matters for a fence project. "
    )
    body = sentence * 12
    return PageDoc(
        url=url,
        title="Vinyl fence installation cost",
        h1="Vinyl fence installation cost",
        body=body,
        word_count=len(body.split()),
    )


def test_one_stored_search_low_cosine_is_unvalidated_not_off_topic():
    model = TopicModel({QUERY: [_clone(f"https://ex.example/c{i}") for i in range(4)]})
    plain = (
        "The vinyl fence installation cost is what a homeowner pays a crew to put a vinyl fence around a yard. "
        "The bill covers the vinyl, the posts, the digging, and the hours the crew spends on the installation. "
        "A short fence costs less than a long one because the installation takes less time. "
        "Gates cost extra because the opening needs stronger posts. "
        "Sloped ground raises the vinyl fence installation cost. "
        "Ask the crew to separate the vinyl material from the installation labor. "
        "This note is only about that vinyl fence installation cost. "
    ) * 2
    draft = PageDoc(
        url="https://ex.example/draft",
        title="Vinyl fence installation cost",
        h1="Vinyl fence installation cost",
        body=plain,
        word_count=len(plain.split()),
    )
    check = model.check(QUERY, draft)
    assert TOPIC_MARGIN == 0.5
    assert model.topic_floor > 0.3
    assert check.topic_score is not None and check.topic_score < model.topic_floor
    assert check.found == ["vinyl", "fence", "installation", "cost"]
    assert check.need >= 1
    assert check.on_topic is None
    assert check.status == "uncertain"
    assert "not an off-topic decision" in check.text
    original = model.check(QUERY, _clone("https://ex.example/c0"))
    assert original.on_topic is True
    assert original.topic_score is not None and original.topic_score >= model.topic_floor


def test_missing_query_words_stay_off_topic_on_one_search():
    model = TopicModel({QUERY: [_clone(f"https://ex.example/c{i}") for i in range(4)]})
    cake = (
        "Melt butter with sugar, then beat in eggs and fold flour with cocoa. "
        "Pour the batter into a round pan and bake until a skewer comes out clean. "
        "Cool the layers on a rack before spreading ganache over the top. "
        "Serve slices with cold milk. The crumb should be tender and the frosting should set at room temperature. "
    ) * 3
    doc = PageDoc(url="https://bakery.example/cake", title="Chocolate cake", h1="Chocolate cake", body=cake, word_count=len(cake.split()))
    check = model.check(QUERY, doc)
    assert check.on_topic is False
    assert check.status == "off topic"
    assert check.need >= 2
    assert check.found == []
    assert "vinyl" in check.missing


def test_structured_html_under_the_floor_keeps_an_unsure_slot(demo):
    ctx = demo["ctx"]
    html = """<html><head><title>Vinyl fence installation cost</title></head><body>
<h1>Vinyl fence installation cost</h1>
<h2>What the bill covers</h2>
<p>The vinyl fence installation cost is the amount a homeowner pays a crew to put a vinyl fence around a yard. The bill covers the vinyl panels, the posts, the digging, and the hours the crew spends on the installation.</p>
<h2>Length and gates</h2>
<p>A short vinyl fence costs less than a long one because the installation takes less time. Gates cost extra because the opening needs stronger posts and more careful installation.</p>
<h2>Slopes</h2>
<p>Sloped ground raises the vinyl fence installation cost since each section has to step down the hill. Ask the crew to separate the vinyl material from the installation labor so the cost is easy to read.</p>
<p>This note is only about that vinyl fence installation cost for a homeowner comparing quotes.</p>
</body></html>"""
    doc = page_from_html("https://www.northwind-fencing.example/new-structured/", html, is_ours=True)
    check = ctx.topic.check(QUERY, doc)
    assert TOPIC_MARGIN == 0.5
    assert ctx.topic.topic_floor > 0.35
    assert check.topic_score is not None and check.topic_score < ctx.topic.topic_floor
    assert check.found == ["vinyl", "fence", "installation", "cost"]
    assert check.need == 2
    assert check.status == "uncertain" and check.on_topic is None
    run = ws.run_draft(ctx, QUERY, html, "https://www.northwind-fencing.example/new-structured/")
    assert run["after"]["position"] is not None
    assert run["after"]["confidence"] == "UNSURE"
    assert run["after"]["confidence_note"] == run["topic_after"]["text"]
    assert "unvalidated" in run["after"]["confidence_note"].lower()
    assert "same pairs" not in run["after"]["confidence_note"].lower()
    assert run["topic_after"]["status"] == "uncertain"
    original = ws.synthetic_page(ws.compare_search(ctx, QUERY).your_url)
    stored = page_from_html(ws.compare_search(ctx, QUERY).your_url, original, is_ours=True)
    kept = ctx.topic.check(QUERY, stored)
    assert kept.on_topic is True
    assert kept.topic_score is not None and kept.topic_score >= ctx.topic.topic_floor


def test_cross_search_boilerplate_above_the_floor_stays_uncertain(demo):
    ctx = demo["ctx"]
    other = ctx.topic.readable["how long does a cedar fence last"][0]
    check = ctx.topic.check(QUERY, other)
    assert TOPIC_MARGIN == 0.5
    assert TOPIC_FLOOR_DEFAULT == 0.02
    assert ctx.topic.topic_floor > 0.35
    assert check.topic_score is not None and check.topic_score >= ctx.topic.topic_floor
    assert check.on_topic is None
    assert check.status == "uncertain"
    assert "as well or better" in check.text
    assert check.need == 2
    html = ws.synthetic_page(other.url)
    run = ws.run_draft(ctx, QUERY, html, "https://www.northwind-fencing.example/from-cedar/")
    note = run["after"]["confidence_note"]
    assert run["after"]["position"] is not None
    assert run["after"]["confidence"] == "UNSURE"
    assert run["topic_after"]["status"] == "uncertain"
    assert "as well or better" in note
    assert "Held-out" not in note and "same pairs" not in note
    from aeo.ranksim.workspace_ui import _topic_panel

    rendered = _topic_panel(run)
    assert "as well or better" in rendered
    assert note.split(".")[0] in rendered
    saved = dict(run)
    saved["dataset"] = "observed"
    saved["project"] = "acme"
    import tempfile
    from pathlib import Path

    from aeo.ranksim.ledger import RsPrediction
    from aeo.ranksim.store import open_session

    database = f"sqlite:///{Path(tempfile.mkdtemp()) / 'topic.db'}"
    ws._record_draft_ledger(database, saved, html)
    session = open_session(database)
    try:
        row = session.query(RsPrediction).one()
    finally:
        session.close()
    assert row.project_slug == "acme"
    assert row.payload["calibration_note"] == note
    assert all(call["note"] == note for call in row.payload["calls"])


def test_one_search_model_still_places_the_draft_as_unsure(demo):
    ctx = demo["ctx"]
    original_topic = ctx.topic
    ctx.topic = TopicModel({QUERY: [_clone(f"https://ex.example/c{i}") for i in range(4)]})
    plain = (
        "Vinyl fence installation cost\n\n"
        "The vinyl fence installation cost is what a homeowner pays a crew to put a vinyl fence around a yard. "
        "The bill covers the vinyl, the posts, the digging, and the hours the crew spends on the installation. "
        "A short fence costs less than a long one because the installation takes less time. "
        "Gates cost extra. Sloped ground raises the vinyl fence installation cost. "
        "This note is only about that vinyl fence installation cost."
    )
    try:
        run = ws.run_draft(ctx, QUERY, plain, "https://www.northwind-fencing.example/new/")
    finally:
        ctx.topic = original_topic
    assert run["topic_after"]["status"] == "uncertain"
    assert run["topic_after"]["on_topic"] is None
    assert run["after"]["position"] is not None
    assert run["after"]["confidence"] == "UNSURE"
