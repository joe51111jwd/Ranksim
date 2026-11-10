"""A bundle page joins only the queries it is meant for, a query with no stored competitor gets no
position, and the report renders bundle runs with honest labels. Synthetic pages."""

from datetime import date

from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.keywords import Keyword
from aeo.ranksim.urlkey import match_key
from aeo.ranksim.whatif import bundle_targets, overrides_from_html, score_whatif

NEW = "https://www.smartcrewhq.com/paving-software"


def _kw(query, target):
    return Keyword(query=query, intent="commercial", cluster="c", suggested_target_page=target, page_exists=False, notes="")


def test_targets_come_from_keywords_and_the_manifest_query_column(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"url,file,query\n{NEW}/,paving.html,Asphalt Paving Software\nhttps://www.smartcrewhq.com/other,o.html,\n")
    targets = bundle_targets([manifest], keywords=[_kw("paving contractor software", NEW), _kw("unrelated", "https://x.example/")])
    assert targets == {"paving contractor software": {match_key(NEW)}, "asphalt paving software": {match_key(NEW)}}


def _html(title, body):
    return f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1><p>{body}</p></main></body></html>"


def test_no_position_for_a_query_without_stored_competitors():
    labels, pages = [], {}
    for q in range(3):
        query = f"paving case {q}"
        for position in range(1, 4):
            url = f"https://site{q}{position}.example/"
            pages[url] = PageDoc(url=url, title=query, h1=query, body=(query + " ") * 40, word_count=120)
            labels.append(SerpLabel(query, url, position, None, "manual_csv"))
    labels.append(SerpLabel("paving contractor software", "https://never-crawled.example/", 1, None, "manual_csv"))
    overrides = overrides_from_html({NEW: _html("Paving software", "paving contractor software " * 60)}, pages)
    targets = {"paving contractor software": {match_key(NEW)}, "paving case 0": {match_key(NEW)}}
    report = score_whatif(labels, pages, overrides, today=date(2026, 11, 10), targets=targets)
    empty = next(row for row in report.queries if row.query == "paving contractor software")
    assert empty.after_rank is None and not empty.pages  # before: "rank 1 of 1"
    joined = next(row for row in report.queries if row.query == "paving case 0")
    assert any(page.url == NEW for page in joined.pages)
    other = next(row for row in report.queries if row.query == "paving case 1")
    assert all(page.url != NEW for page in other.pages)


def test_the_report_renders_a_bundle_run(tmp_path):
    from aeo.ranksim.report import render_report
    from aeo.ranksim.store import open_session, save_test_run

    db = f"sqlite:///{tmp_path / 'r.db'}"
    session = open_session(db)
    save_test_run(
        session,
        source_label="bundle:smartcrew/pr8/manifest.csv",
        weight_label="lambdamart_loo",
        synthetic_corpus=False,
        is_google=True,
        summary_json={
            "predicted": True, "kind": "bundle", "pairwise_accuracy": 0.55, "n_pairs": 120,
            "queries": [{"query": "hcss alternative", "real_position": 4, "before_predicted": 5, "after_predicted": 2,
                         "before_url": NEW, "after_url": NEW, "before_n": 9, "after_n": 9}],
        },
    )
    session.close()
    html = render_report(db, keywords=[])
    assert "slot 5 of 9" in html and "slot 2 of 9" in html and "hcss alternative" in html
    assert "not accuracy on unseen results" in html
    assert "Improved: n/a" not in html  # the plain-test layout was used for bundle runs before
