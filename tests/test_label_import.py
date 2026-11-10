"""Label files are validated row by row; nothing is silently half-used."""

import pytest

from aeo.ranksim.groundtruth import ImportReport, load_serp_file


def _write(tmp_path, text, name="labels.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_bad_rows_are_dropped_with_reasons_and_row_numbers(tmp_path):
    path = _write(
        tmp_path,
        "query,url,position\n"
        "Dirt  Work Software,https://a.example/,1\n"        # kept, query normalized
        "dirt work software,https://b.example/,nan\n"        # row 3: non-finite
        "dirt work software,https://c.example/,0\n"          # row 4: < 1
        "dirt work software,c.example/x,2\n"                 # row 5: no scheme
        ",https://d.example/,3\n"                            # row 6: blank query
        "dirt work software,https://e.example/,inf\n"        # row 7: non-finite
        "dirt work software,https://f.example/,4\n",         # kept
    )
    report = ImportReport()
    rows = load_serp_file(path, report=report)
    assert [row.url for row in rows] == ["https://a.example/", "https://f.example/"]
    assert rows[0].query == "dirt work software"
    assert report.dropped["position is not a finite number >= 1"] == [3, 4, 7]
    assert report.dropped["URL is not an absolute http(s) URL"] == [5]
    assert report.dropped["blank query or URL"] == [6]
    assert any("dropped 3 rows" in line for line in report.lines())


def test_duplicates_keep_the_best_position_and_ties_are_reported(tmp_path):
    path = _write(
        tmp_path,
        "query,url,position\nq,https://a.example/x/,3\nq,https://a.example/x,1\nq,https://b.example/,1\n",
    )
    report = ImportReport()
    rows = load_serp_file(path, report=report)
    assert len(rows) == 2
    assert {row.position for row in rows if "a.example" in row.url} == {1}
    assert len(report.duplicates) == 1
    assert report.tied_positions == 1


def test_metadata_is_kept_and_non_organic_rows_are_dropped(tmp_path):
    path = _write(
        tmp_path,
        "﻿query,url,position,observed_on,gl,hl,device,provider,type\n"
        "q,https://a.example/,1,2026-10-29,us,en,desktop,serper,organic\n"
        "q,https://ads.example/,1,2026-10-29,us,en,desktop,serper,ad\n"
        "q,https://b.example/,2,2026-10-29,us,en,desktop,serper,organic\n",
    )
    report = ImportReport()
    rows = load_serp_file(path, report=report)
    assert [row.url for row in rows] == ["https://a.example/", "https://b.example/"]
    assert rows[0].observed_on == "2026-10-29" and rows[0].country == "us" and rows[0].language == "en"
    assert rows[0].device == "desktop" and rows[0].provider == "serper"
    assert report.dropped["not an organic result (ad)"] == [3]
    assert "observed_on" in report.metadata_columns


def test_missing_columns_error_lists_what_was_found(tmp_path):
    path = _write(tmp_path, "keyword;page;pos\nq;https://a.example/;1\n")
    with pytest.raises(ValueError, match="Found: 'keyword;page;pos'"):
        load_serp_file(path)


def test_search_console_files_are_marked_as_averages(tmp_path):
    path = _write(tmp_path, "Query,Page,Clicks,Impressions,CTR,Position\nq,https://a.example/,3,100,3%,3.6\n")
    report = ImportReport()
    rows = load_serp_file(path, report=report)
    assert rows[0].kind == "search_console_avg"
    assert any("not one results page" in line for line in report.lines())


def test_smartcrew_file_still_loads_unchanged():
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "smartcrew" / "serp.csv"
    report = ImportReport()
    rows = load_serp_file(path, report=report)
    assert report.kept == 135 and not report.dropped and not report.duplicates
    assert any("cannot be dated" in line for line in report.lines())
    assert len({row.query for row in rows}) == 16


def test_a_serper_export_with_a_link_column_is_a_snapshot(tmp_path):
    # Same input as the integration branch's test for 847ef71.
    path = tmp_path / "serper.csv"
    path.write_text(
        "query,position,link,title\n"
        "hcss alternative,1,https://example.com/a,HCSS alternatives\n"
        "hcss alternative,2,https://example.com/b,Another page\n"
    )
    rows = load_serp_file(path)
    assert [row.url for row in rows] == ["https://example.com/a", "https://example.com/b"]
    assert rows[0].position == 1 and rows[0].kind == "serp_snapshot"
