"""B21 review fixes for observed results and keyword files: Google Ads totals rows are not queries
(G4), and noise FILE FILE leaves out a list whose date is not YYYY-MM-DD (G5). Synthetic data."""

from pathlib import Path

import pytest

from aeo.ranksim.cli import main
from aeo.ranksim.keywords import load_keyword_file

ADS_TITLE = 'Search keyword report\n"November 1, 2026 - November 9, 2026"\n'
# A Google Ads UI download ends with totals rows, with ' --' in the text columns.
ADS_STATUS_FIRST = (
    "Keyword status,Keyword,Match type,Campaign,Ad group,Status,Currency code,Max. CPC,Clicks,Impr.,CTR,Avg. CPC,Cost\n"
    "Enabled,dirt work software,Phrase match,Brand,AG1,Eligible,USD,1.50,12,340,3.53%,1.20,14.40\n"
    "Enabled,hcss alternative,Exact match,Brand,AG1,Eligible,USD,2.00,3,90,3.33%,2.10,6.30\n"
    "Total: Keywords, --, --, --, --, --,USD, --,15,430,3.49%,1.38,20.70\n"
    "Total: Search, --, --, --, --, --,USD, --,15,430,3.49%,1.38,20.70\n"
    "Total: Account, --, --, --, --, --,USD, --,15,430,3.49%,1.38,20.70\n"
)
ADS_KEYWORD_FIRST = (
    "Keyword,Match type,Campaign,Ad group,Clicks,Impr.,CTR,Avg. CPC,Cost\n"
    "dirt work software,Phrase match,Brand,AG1,12,340,3.53%,1.20,14.40\n"
    "hcss alternative,Exact match,Brand,AG1,3,90,3.33%,2.10,6.30\n"
    "Total: Keywords, --, --, --,15,430,3.49%,1.38,20.70\n"
    "Total: Search, --, --, --,15,430,3.49%,1.38,20.70\n"
    "Total: Account, --, --, --,15,430,3.49%,1.38,20.70\n"
)
WANTED = ["dirt work software", "hcss alternative"]


def _ads_files(tmp_path):
    files = {
        "status_first.csv": ADS_TITLE + ADS_STATUS_FIRST,
        "keyword_first.csv": ADS_TITLE + ADS_KEYWORD_FIRST,
        "keyword_first_no_title.csv": ADS_KEYWORD_FIRST,
        "status_first.tsv": ADS_TITLE + ADS_STATUS_FIRST.replace(",", "\t"),
    }
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    return [tmp_path / name for name in files]


def test_g4_google_ads_totals_rows_and_placeholders_are_not_queries(tmp_path):
    for path in _ads_files(tmp_path):
        assert [row.query for row in load_keyword_file(path)] == WANTED, path.name


def test_g4_track_sends_no_totals_row_as_a_paid_search(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.cli as cli

    sent = []
    resolve = cli.resolve_serp

    def spy(settings, choice):
        provider = resolve(settings, choice)
        search = provider.search
        provider.search = lambda query, *a, **kw: sent.append(query) or search(query, *a, **kw)
        return provider

    monkeypatch.setattr(cli, "resolve_serp", spy)
    for index, path in enumerate(_ads_files(tmp_path)):
        sent.clear()
        cli.main(["--database", f"sqlite:///{tmp_path / f't{index}.db'}", "track", "--mock", "--keywords", str(path)])
        assert sent == WANTED, path.name
        assert "Recorded 2 queries from mock" in capsys.readouterr().out


def test_g4_guard_queries_that_start_with_total_stay_and_plain_lists_keep_every_line(tmp_path):
    table = tmp_path / "table.csv"
    table.write_text(
        "Keyword,Match type,Clicks,Impr.\n"
        "total gym,Phrase match,4,80\n"
        "total war review,Exact match,2,30\n"
        "totals: of a crew,Exact match,1,10\n"
    )
    assert [row.query for row in load_keyword_file(table)] == ["total gym", "total war review", "totals: of a crew"]
    plain = tmp_path / "plain.txt"  # no header row, so every line is a query as before
    plain.write_text("dirt work software\nTotal: Keywords\n--\n")
    assert [row.query for row in load_keyword_file(plain)] == ["dirt work software", "total: keywords", "--"]


def test_g4_placeholder_query_cell_is_dropped_without_a_total_label(tmp_path):
    # Review follow-up R1: in the other fixtures every '--' sits in a 'Total:' row, so the
    # totals check alone removed it. These rows have '--' in the query column and no 'Total:' cell.
    files = {
        # A localized Google Ads download names its totals row in another language.
        "localized_status_first.csv": ADS_TITLE + (
            "Keyword status,Keyword,Match type,Clicks,Impr.\n"
            "Enabled,dirt work software,Phrase match,12,340\n"
            "Gesamt: Konto, --, --,12,340\n"
        ),
        "one_column.csv": "keyword\ndirt work software\n--\n",
        "keyword_first_blank_query.csv": (
            "Keyword,Match type,Clicks,Impr.\n"
            "dirt work software,Phrase match,12,340\n"
            " --,Exact match,0,0\n"
        ),
    }
    for name, body in files.items():
        path = tmp_path / name
        path.write_text(body)
        assert [row.query for row in load_keyword_file(path)] == ["dirt work software"], name


def test_g4_readme_scopes_the_export_column_rule_to_a_header_below_title_rows(tmp_path):
    # Review follow-up R2: a header on the first line needs only a query column, so the README
    # must not state the export column rule for every table.
    first_line = tmp_path / "first_line.csv"
    first_line.write_text("Keyword,Notes\ndirt work software,check later\n")
    assert [row.query for row in load_keyword_file(first_line)] == ["dirt work software"]
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    paragraph = next(block for block in readme.split("\n\n") if "--keywords FILE" in block)
    before = paragraph[: paragraph.index("next to another export column")]
    previous, current = before.rsplit(". ", 2)[-2:]
    assert "title" in previous or "title" in current
    assert "A file read one query per line keeps every line." in paragraph


def _label_file(path, query_days, order="abc"):
    rows = []
    for query, day in query_days:
        rows += [f"{query},https://{name}.example/,{position},{day}\n" for position, name in enumerate(order, 1)]
    path.write_text("query,url,position,date\n" + "".join(rows))
    return path


def _export(path, day, collection, order):
    head = "query,url,position,observed_on,country,language,device,provider,synthetic,collected_at,collection_id,google\n"
    path.write_text(head + "".join(
        f"crew app,https://{name}.example/,{position},{day},us,en,desktop,serper,,{day}T09:00:00,{collection},yes\n"
        for position, name in enumerate(order, 1)
    ))
    return path


def _noise(tmp_path, first, second):
    main(["--database", f"sqlite:///{tmp_path / 'noise.db'}", "noise", str(first), str(second)])


@pytest.mark.parametrize(
    ("first_day", "second_day"),
    [("11/10/2026", "11/10/2026"), ("11/12/2026", "11/10/2026"), ("2026-11-10", "11/10/2026"), ("11/09/2026", "2026-11-10")],
)
def test_g5_noise_files_leave_out_a_list_whose_date_is_not_yyyy_mm_dd(tmp_path, capsys, first_day, second_day):
    pairs = [
        (_label_file(tmp_path / "a.csv", [("crew app", first_day)]), _label_file(tmp_path / "b.csv", [("crew app", second_day)], "acb")),
        (_export(tmp_path / "ea.csv", first_day, "c1", "abc"), _export(tmp_path / "eb.csv", second_day, "c2", "acb")),
    ]
    for first, second in pairs:
        _noise(tmp_path, first, second)
        printed = capsys.readouterr().out
        assert "Left out 1 of 1 queries that are in both files" in printed
        assert "Date that cannot be read as YYYY-MM-DD: 1. Write each date as YYYY-MM-DD." in printed
        assert "Noise ceiling was not computed." in printed
        assert "Agreement between two real snapshots" not in printed and "Queries compared" not in printed


def test_g5_noise_files_still_compare_the_queries_with_readable_dates(tmp_path, capsys):
    first = _label_file(tmp_path / "a.csv", [("crew app", "2026-11-09"), ("paving app", "11/09/2026")])
    second = _label_file(tmp_path / "b.csv", [("crew app", "2026-11-10"), ("paving app", "2026-11-10")], "acb")
    _noise(tmp_path, first, second)
    printed = capsys.readouterr().out
    assert "Left out 1 of 2 queries that are in both files" in printed
    assert "Date that cannot be read as YYYY-MM-DD: 1. Write each date as YYYY-MM-DD." in printed
    assert "Agreement between two real snapshots" in printed and "Queries compared: 1." in printed
