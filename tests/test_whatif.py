from datetime import date

from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.whatif import overrides_from_html, render_whatif, score_whatif


def _html(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"


def test_bundle_replaces_html_keeps_pagerank_and_inserts_new_urls():
    pages: dict[str, PageDoc] = {}
    labels: list[SerpLabel] = []
    for query_index in range(4):
        query = f"dirtwork software case {query_index}"
        ours = f"https://www.smartcrewhq.com/case-{query_index}"
        specs = [
            (1, f"https://example.com/{query_index}/best", query, (query + " ") * 40),
            (2, f"https://example.com/{query_index}/mid", "notes", query),
            (3, ours, "ferns", "lobby plants and water"),
        ]
        for position, url, title, body in specs:
            pages[url] = PageDoc(
                url=url,
                title=title,
                h1=title,
                body=body,
                word_count=len(body.split()),
                pagerank=0.4 if "smartcrew" in url else 0.05,
                domain_rank=0.2 if "smartcrew" in url else 0.05,
                homepage_authority=0.15 if "smartcrew" in url else 0.0,
            )
            labels.append(SerpLabel(query, url, position, None, "manual_csv"))
    holdout = "hcss alternative fixture"
    labels.append(SerpLabel(holdout, "https://missing.example/top", 1, None, "manual_csv"))
    labels.append(SerpLabel(holdout, "https://example.com/hold/a", 2, None, "manual_csv"))
    labels.append(SerpLabel(holdout, "https://example.com/hold/b", 3, None, "manual_csv"))
    pages["https://example.com/hold/a"] = PageDoc(
        url="https://example.com/hold/a",
        title="hcss alternative",
        h1="hcss alternative",
        body=("hcss alternative " * 30),
        word_count=60,
    )
    pages["https://example.com/hold/b"] = PageDoc(
        url="https://example.com/hold/b",
        title="notes",
        h1="notes",
        body="lobby plants",
        word_count=2,
    )
    query0 = "dirtwork software case 0"
    ours0 = "https://www.smartcrewhq.com/case-0"
    new_url = "https://www.smartcrewhq.com/new-landing"
    overrides = overrides_from_html(
        {
            ours0: _html(query0, (query0 + " ") * 40),
            new_url: _html("ferns", "lobby plants and water"),
        },
        pages,
    )
    replaced_doc = next(row for row in overrides if row.url == ours0)
    new_doc = next(row for row in overrides if row.url == new_url)
    assert replaced_doc.in_corpus is True
    assert replaced_doc.doc.pagerank == 0.4
    assert new_doc.in_corpus is False
    assert new_doc.doc.pagerank == 0.0
    assert new_doc.doc.domain_rank == 0.2

    report = score_whatif(labels, pages, overrides, today=date(2026, 10, 29))
    first = next(row for row in report.queries if row.query == query0)
    replaced = next(page for page in first.pages if page.url == ours0)
    assert replaced.before_rank == 3
    assert replaced.after_rank is not None and replaced.after_rank < replaced.before_rank
    assert any("title match" in line for line in replaced.feature_lines)
    inserted = next(page for page in first.pages if page.url == new_url)
    assert inserted.before_rank is None
    assert inserted.after_rank is not None
    assert inserted.role.startswith("new URL")

    held = next(row for row in report.queries if row.query == holdout)
    assert held.before_rank is None
    assert held.after_rank is not None
    assert held.real_position is None
    missing = next(gap for gap in held.gaps if gap.url == "https://missing.example/top")
    assert missing.crawled is False
    text = render_whatif(report, {"https://missing.example/top": ("blocked", "robots")})
    assert "Predicted positions. Not live Google ranks." in text
    assert "blocked by robots.txt" in text
    assert "\u2014" not in text and "\u2013" not in text
    assert "No page text was invented." in text
