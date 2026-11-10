"""One intent source: label file, then keywords.csv, then one rule, then a reported default."""

from datetime import date

import pytest

from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel, load_serp_file
from aeo.ranksim.intents import query_intents
from aeo.ranksim.keywords import Keyword
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.whatif import _intent


def _keyword(query, intent):
    return Keyword(query=query, intent=intent, cluster="c", suggested_target_page="", page_exists=False, notes="")


def _label(query, url, position=1, intent=None):
    return SerpLabel(query, url, position, None, "manual_csv", intent=intent)


def test_the_label_file_wins_then_keywords_then_the_rule_then_a_reported_default():
    keywords = [_keyword("hcss alternative", "comparison"), _keyword("dirt work software", "commercial")]
    labels = [
        _label("dirt work software", "https://a.example/", intent="informational"),
        _label("hcss alternative", "https://b.example/"),
        _label("b2w vs hcss", "https://c.example/"),
        _label("heavy civil field management software", "https://d.example/"),
    ]
    found = query_intents(labels, keywords)
    assert found.intents["dirt work software"] == "informational" and found.origin["dirt work software"] == "label_file"
    assert found.origin["hcss alternative"] == "keywords"
    assert (found.intents["b2w vs hcss"], found.origin["b2w vs hcss"]) == ("comparison", "rule")
    assert found.origin["heavy civil field management software"] == "default"
    note = found.note({label.query for label in labels})
    assert note.startswith("1 of 4 labeled queries have no intent") and "heavy civil field management software" in note


def test_the_label_file_intent_column_is_read_and_unknown_values_ignored(tmp_path):
    path = tmp_path / "serp.csv"
    path.write_text("query,url,position,intent\nq,https://a.example/,1,Comparison\nq,https://b.example/,2,shopping\n")
    rows = load_serp_file(path)
    assert [row.intent for row in rows] == ["comparison", None]


def test_training_what_if_and_the_space_view_use_the_same_intent(monkeypatch):
    query = "b2w vs hcss"  # in no keywords file
    found = query_intents([_label(query, "https://a.example/")], keywords=[])
    pages = {f"https://p{index}.example/": PageDoc(url=f"https://p{index}.example/", title="B2W vs HCSS", body="b2w hcss " * 100, word_count=200)
             for index in range(2)}
    labels = [_label(query, url, position) for position, url in enumerate(pages, start=1)]
    examples, _uncovered, _withheld = build_examples(labels, pages, found.intents, today=date(2026, 11, 10))
    assert {row.intent for row in examples} == {"comparison"}
    assert _intent(query, {}) == "comparison" == found.intents[query]
    assert _space_intents(monkeypatch, labels, pages, found.intents) == {query: "comparison"}


def _space_intents(monkeypatch, labels, pages, intents) -> dict[str, str]:
    """The intent the space view scores each query with (build_space's raw_features call)."""
    import aeo.ranksim.space as space

    seen: dict[str, str] = {}
    real = space.raw_features

    def spy(query, intent, docs, today):
        seen[query] = intent
        return real(query, intent, docs, today)

    monkeypatch.setattr(space, "raw_features", spy)
    monkeypatch.setattr(space, "predict_scores", lambda model, examples: {id(row): -float(index) for index, row in enumerate(examples)})
    models = {label.query: object() for label in labels}
    space.build_space(labels, pages, models, [], our_domain="p0.example", today=date(2026, 11, 10), intents=intents)
    return seen


def test_the_space_view_uses_the_label_file_intent(monkeypatch):
    labels = [
        _label("dirt work software", "https://p0.example/", 1, intent="informational"),  # the label file names it
        _label("dirt work software", "https://p1.example/", 2),
    ]
    pages = {
        url: PageDoc(url=url, title="Dirt work software", body="dirt work software " * 80, word_count=240)
        for url in ("https://p0.example/", "https://p1.example/")
    }
    found = query_intents(labels, keywords=[_keyword("dirt work software", "commercial")])
    assert _space_intents(monkeypatch, labels, pages, found.intents) == {"dirt work software": "informational"}


def test_the_space_command_passes_the_label_file_intents(tmp_path, monkeypatch):
    import aeo.ranksim.space as space
    from aeo.ranksim.cli import main
    from aeo.ranksim.loop import FetchResult, store_fetched_page
    from aeo.ranksim.store import open_session

    class Captured(Exception):
        pass

    captured: dict = {}

    def spy(labels, pages, models, competitors, **kwargs):
        captured.update(kwargs)
        raise Captured

    monkeypatch.setattr(space, "build_space", spy)
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    for url in ("https://p0.example/", "https://p1.example/"):
        html = f"<html><head><title>Dirt work</title></head><body><main><p>{'dirt work software ' * 80}</p></main></body></html>"
        store_fetched_page(session, FetchResult(url=url, html=html, status="fetched"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text(
        "query,url,position,intent\n"
        "zz dirt work software,https://p0.example/,1,informational\nzz dirt work software,https://p1.example/,2,informational\n"
    )
    with pytest.raises(Captured):
        main(["--database", db, "space", "--serp", str(serp), "--competitors", str(tmp_path / "none.csv")])
    assert captured["intents"]["zz dirt work software"] == "informational"
