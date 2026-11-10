"""Serper scrape JSON becomes page text. No key and no network."""

from __future__ import annotations

import json
from pathlib import Path

from aeo.ranksim.forward_eval import evaluate_snapshot_pair
from aeo.ranksim.recovered_eval import DRAFT_ADOPTION_MARGIN, GATE_SLICES, PUBLISHED_LAMBDAMART
from aeo.ranksim.serper_scrape import (
    SOURCE,
    USABLE_MIN_CHARS,
    html_from_scrape,
    load_scrape_dir,
    scrape_content_map,
)
from tests.test_forward_eval import _page, _write_snapshot


def _write_scrape(root: Path) -> None:
    raw = root / "raw"
    raw.mkdir(parents=True)
    long_text = "haul roads and crew quantities " * 40
    short_text = "too short"
    (raw / "long.json").write_text(
        json.dumps(
            {
                "text": long_text,
                "metadata": {"title": "Dirt work software", "description": "A field crew page."},
                "credits": 2,
            }
        ),
        encoding="utf-8",
    )
    (raw / "short.json").write_text(
        json.dumps({"text": short_text, "metadata": {"og:title": "Thin"}, "credits": 2}),
        encoding="utf-8",
    )
    (raw / "dead.json").write_text(json.dumps({"message": "Scraping failed.", "statusCode": 500}), encoding="utf-8")
    (root / "manifest.csv").write_text(
        "url,status,chars_of_text,has_title,error,credits,sha1,raw_file\n"
        "https://dirt.example/long,200,900,true,,2,aa,raw/long.json\n"
        "https://dirt.example/short,200,9,true,,2,bb,raw/short.json\n"
        "https://dirt.example/dead,500,0,false,Scraping failed.,0,cc,raw/dead.json\n",
        encoding="utf-8",
    )


def test_importer_marks_source_and_date_and_drops_short_text(tmp_path, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "should-not-be-read")
    _write_scrape(tmp_path)
    pages = load_scrape_dir(tmp_path, captured_on="2026-11-10")
    assert [page.url for page in pages] == [
        "https://dirt.example/long",
        "https://dirt.example/short",
        "https://dirt.example/dead",
    ]
    long_page = pages[0]
    assert long_page.source == SOURCE == "serper_scrape"
    assert long_page.captured_on == "2026-11-10"
    assert long_page.title == "Dirt work software"
    assert long_page.description == "A field crew page."
    assert long_page.usable is True
    assert long_page.chars > USABLE_MIN_CHARS
    assert "should-not-be-read" not in long_page.text
    assert pages[1].usable is False
    assert pages[1].chars == len("too short")
    assert pages[2].usable is False
    assert pages[2].error == "Scraping failed."
    filled = scrape_content_map(pages)
    assert list(filled) == ["https://dirt.example/long"]
    html = html_from_scrape(long_page)
    assert "Dirt work software" in html
    assert "A field crew page." in html
    assert "haul roads" in html
    assert "credits" not in html


def test_exactly_500_characters_is_not_usable(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "edge.json").write_text(json.dumps({"text": "a" * 500, "metadata": {"title": "Edge"}}), encoding="utf-8")
    (tmp_path / "manifest.csv").write_text(
        "url,status,error,raw_file\nhttps://edge.example/a,200,,raw/edge.json\n",
        encoding="utf-8",
    )
    page = load_scrape_dir(tmp_path)[0]
    assert page.chars == 500
    assert page.usable is False
    assert scrape_content_map([page]) == {}


def test_scrape_text_is_score_time_only_and_not_a_training_row(tmp_path):
    baseline = tmp_path / "baseline"
    forward = tmp_path / "forward"
    _write_snapshot(
        baseline,
        [
            ("alpha tools", 1, "https://a.example/old"),
            ("alpha tools", 2, "https://b.example/old"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {
            "https://a.example/old": _page("alpha tools guide"),
            "https://b.example/old": _page("alpha tools notes"),
            "https://d.example/one": _page("beta widgets handbook"),
            "https://e.example/two": _page("beta widgets pricing"),
        },
    )
    _write_snapshot(
        forward,
        [
            ("alpha tools", 1, "https://c.example/new"),
            ("alpha tools", 2, "https://a.example/old"),
            ("alpha tools", 3, "https://b.example/old"),
            ("alpha tools", 4, "https://f.example/missing"),
            ("beta widgets", 1, "https://d.example/one"),
            ("beta widgets", 2, "https://e.example/two"),
        ],
        {"https://c.example/new": _page("alpha tools newcomer")},
    )
    body = "<html><head><title>alpha tools scraped</title></head><body><p>" + ("alpha tools crew " * 80) + "</p></body></html>"
    result = evaluate_snapshot_pair(
        baseline,
        forward,
        recovered={"https://f.example/missing": (body, "serper_scrape", "2026-11-10")},
    )
    assert "https://f.example/missing" not in {row.url for row in result.training_examples}
    row = [item for item in result.scored_rows if item["url"] == "https://f.example/missing"][0]
    assert row["html_source"] == "serper_scrape"
    assert row["content_captured_on"] == "2026-11-10"
    assert row["content_missing"] == 0
    assert result.report["coverage"]["html_serper_scrape"] == 1
    assert result.report["label"] == "PREDICTED"


def test_a_raw_path_cannot_leave_the_scrape_directory(tmp_path):
    (tmp_path / "manifest.csv").write_text(
        "url,status,error,raw_file\nhttps://edge.example/a,200,,../secret.json\n",
        encoding="utf-8",
    )
    try:
        load_scrape_dir(tmp_path)
    except ValueError as exc:
        assert "inside the directory" in str(exc)
    else:
        raise AssertionError("a parent path was accepted")


def test_scrape_gate_matches_the_committed_all_slice_rule():
    assert DRAFT_ADOPTION_MARGIN == 0.05
    assert GATE_SLICES == (
        "draft_vs_draft",
        "draft_vs_draft_with_content",
        "draft_vs_returning",
        "draft_vs_returning_with_content",
    )
    assert PUBLISHED_LAMBDAMART["draft_vs_draft"] == {"correct": 34, "pairs": 116}
    assert PUBLISHED_LAMBDAMART["draft_vs_draft_with_content"] == {"correct": 12, "pairs": 22}
    assert USABLE_MIN_CHARS == 500
