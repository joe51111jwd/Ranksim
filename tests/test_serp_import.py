"""Browser SERP import. Fixtures use example.com only."""

from __future__ import annotations

import json

from aeo.ranksim.cli import main
from aeo.ranksim.serp_import import import_capture


def test_jsonl_keeps_organic_positions_and_skips_rows_without_http(tmp_path):
    text = "\n".join(
        [
            json.dumps(
                {
                    "q": "example software",
                    "ts": "2026-11-06T12:00:00Z",
                    "organic": [
                        ["reddit.com › r/example", "A thread", "No link in this snippet"],
                        ["https://example.com/docs", "Docs", "Install guide"],
                        ["Watch on YouTube", "Video title", "No url here either"],
                        ["https://example.com/pricing", "Pricing", "Plans"],
                    ],
                }
            ),
            json.dumps({"q": "other", "organic": [["example.com › path", "Bare host", "still no scheme"]]}),
        ]
    )
    file_path = tmp_path / "capture.jsonl"
    file_path.write_text(text, encoding="utf-8")
    result = import_capture(file_path)
    assert [(row.query, row.url, row.position) for row in result.rows] == [
        ("example software", "https://example.com/docs", 2),
        ("example software", "https://example.com/pricing", 4),
    ]
    assert result.skipped_no_url == 3


def test_clean_csv_and_json_pass_through_without_inventing(tmp_path):
    csv_path = tmp_path / "clean.csv"
    csv_path.write_text("query,url,position\nexample software,https://example.com/a,1\nexample software,not-a-url,2\n", encoding="utf-8")
    csv_rows = import_capture(csv_path)
    assert len(csv_rows.rows) == 1
    assert csv_rows.rows[0].url == "https://example.com/a"

    json_path = tmp_path / "clean.json"
    json_path.write_text(
        json.dumps(
            [
                {"query": "example software", "url": "https://example.com/b", "position": 3},
                {"query": "example software", "url": "example.com/nope", "position": 4},
            ]
        ),
        encoding="utf-8",
    )
    json_rows = import_capture(json_path)
    assert [(row.url, row.position) for row in json_rows.rows] == [("https://example.com/b", 3)]

    out = tmp_path / "out.csv"
    main(["import-serp", str(json_path), "--out", str(out)])
    written = out.read_text(encoding="utf-8")
    assert "https://example.com/b" in written
    assert "example.com/nope" not in written
