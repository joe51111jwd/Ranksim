"""One intent source: label file, then keywords.csv, then one rule, then a reported default."""

from datetime import date

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


def test_training_what_if_and_the_space_view_use_the_same_intent():
    query = "b2w vs hcss"  # in no keywords file
    found = query_intents([_label(query, "https://a.example/")], keywords=[])
    pages = {f"https://p{index}.example/": PageDoc(url=f"https://p{index}.example/", title="B2W vs HCSS", body="b2w hcss " * 100, word_count=200)
             for index in range(2)}
    labels = [_label(query, url, position) for position, url in enumerate(pages, start=1)]
    examples, _uncovered, _withheld = build_examples(labels, pages, found.intents, today=date(2026, 11, 10))
    assert {row.intent for row in examples} == {"comparison"}
    assert _intent(query, {}) == "comparison" == found.intents[query]
