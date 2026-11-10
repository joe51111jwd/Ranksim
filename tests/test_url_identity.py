"""The same page under another spelling joins as one page; crawls keep where redirects ended and
the page's canonical; coverage flags labeled URLs whose stored HTML may be another page."""

from aeo.crawl.fetch import Fetched
from aeo.ranksim.benchmark import _queries_with_pairs_map, coverage_rows, make_folds, split_fold
from aeo.ranksim.features import PageDoc
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.lambdamart import RankExample, _lookup_page
from aeo.ranksim.loop import FetchResult, reextract_pages, store_fetched_page
from aeo.ranksim.store import RsPage, open_session
from aeo.ranksim.urlkey import match_key

# Inputs and outputs computed by running project.loose_key from the web-UI branch (PR #2).
LOOSE_KEY_VECTORS = {
    "https://www.Example.com/Path/": "example.com/Path",
    "http://example.com/Path": "example.com/Path",
    "https://example.com/a?utm_source=x&b=2&a=1#frag": "example.com/a?a=1&b=2",
    "https://example.com:443/": "example.com/",
    "https://example.com:8443/x/": "example.com:8443/x",
    "https://example.com": "example.com/",
    "HTTPS://WWW.EXAMPLE.COM/?gclid=1": "example.com/",
    "https://example.com/a?_ga=1&ref_src=t&fbclid=2&MC_CID=3": "example.com/a",
    "not a url": "not a url",
    "": "",
    "https://example.com/a//": "example.com/a",
    "https://example.com/a?b&a": "example.com/a?a&b",
    "https://sub.www.example.com/": "sub.www.example.com/",
}

CANONICAL_HTML = (
    '<html><head><title>T</title><link rel="canonical" href="https://other.example/page"></head>'
    "<body><main><p>" + " ".join(f"w{index}" for index in range(200)) + "</p></main></body></html>"
)


def test_match_key_equals_the_web_ui_branch_key():
    for url, expected in LOOSE_KEY_VECTORS.items():
        assert match_key(url) == expected, url


def test_lookup_finds_the_same_page_under_another_spelling_and_prefers_exact():
    exact = PageDoc(url="https://www.example.com/a/", title="exact")
    other = PageDoc(url="https://example.com/a", title="variant")
    pages = {"https://www.example.com/a/": exact}
    assert _lookup_page(pages, "http://example.com/a?utm_source=x") is exact
    pages["https://example.com/a"] = other
    assert _lookup_page(pages, "https://example.com/a") is other
    assert _lookup_page(pages, "https://elsewhere.example/a") is None


def test_url_holdout_drops_training_rows_for_the_same_page_under_another_spelling():
    rows = [
        RankExample("q1", "https://www.shared.example/x", 1.0, {}),
        RankExample("q1", "https://a.example/1", 2.0, {}),
        RankExample("q2", "http://shared.example/x/?utm_medium=y", 1.0, {}),
        RankExample("q2", "https://b.example/1", 2.0, {}),
    ]
    usable = _queries_with_pairs_map(rows)
    fold = make_folds(sorted(usable), {"q1": 0, "q2": 1}, "cluster-url")[0]
    train, _test, dropped = split_fold(usable, fold, "cluster-url")
    assert dropped == 1 and [row.url for row in train] == ["https://b.example/1"]


def test_a_crawl_keeps_the_final_url_and_canonical_and_reextract_fills_old_rows(tmp_path):
    db = f"sqlite:///{tmp_path / 'u.db'}"
    session = open_session(db)
    store_fetched_page(
        session, FetchResult(url="https://moved.example/old", html=CANONICAL_HTML, status="fetched", final_url="https://moved.example/new")
    )
    row = session.get(RsPage, "https://moved.example/old")
    assert (row.final_url, row.canonical_url) == ("https://moved.example/new", "https://other.example/page")
    row.canonical_url = None  # a row from before the column existed
    session.commit()
    reextract_pages(session)
    assert session.get(RsPage, "https://moved.example/old").canonical_url == "https://other.example/page"
    session.close()


def test_the_live_fetcher_passes_on_where_redirects_ended(monkeypatch):
    import aeo.ranksim.cli as cli

    class FakeCrawler:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, url):
            return Fetched(url=url, final_url="https://moved.example/new", status_code=200, html=CANONICAL_HTML)

        def close(self):
            pass

    monkeypatch.setattr(cli, "Crawler", FakeCrawler)
    settings = cli.get_settings()
    fetch, close = cli._live_fetcher(settings)
    assert fetch("https://moved.example/old").final_url == "https://moved.example/new"
    close()
    assert cli._fetch_one(settings, "https://moved.example/old").final_url == "https://moved.example/new"


def test_coverage_flags_redirects_and_canonicals_but_not_a_slash_redirect(tmp_path):
    db = f"sqlite:///{tmp_path / 'u.db'}"
    session = open_session(db)
    plain = CANONICAL_HTML.replace('<link rel="canonical" href="https://other.example/page">', "")
    store_fetched_page(session, FetchResult(url="https://moved.example/old", html=plain, status="fetched", final_url="https://moved.example/new"))
    store_fetched_page(session, FetchResult(url="https://slash.example/a", html=plain, status="fetched", final_url="https://slash.example/a/"))
    store_fetched_page(session, FetchResult(url="https://canon.example/a", html=CANONICAL_HTML, status="fetched", final_url="https://canon.example/a"))
    labels = [
        SerpLabel("q", "https://moved.example/old", 1, None, "manual_csv"),
        SerpLabel("q", "https://www.slash.example/a", 2, None, "manual_csv"),
        SerpLabel("q", "https://canon.example/a", 3, None, "manual_csv"),
    ]
    rows = coverage_rows(labels, session)
    session.close()
    assert [(row["reason"], row["redirected"], row["canonical_elsewhere"]) for row in rows] == [
        ("covered", "yes", ""),
        ("covered", "", ""),
        ("covered", "", "yes"),
    ]


def test_index_serp_does_not_recrawl_a_page_stored_under_another_spelling(tmp_path, monkeypatch):
    import aeo.ranksim.index_serp as index_serp
    from aeo.config import get_settings

    db = f"sqlite:///{tmp_path / 'u.db'}"
    session = open_session(db)
    store_fetched_page(session, FetchResult(url="https://example.com/a", html=CANONICAL_HTML, status="fetched"))
    session.close()
    serp = tmp_path / "serp.csv"
    serp.write_text("query,url,position\nq,https://www.example.com/a/,1\n")
    attempted = []
    monkeypatch.setattr(index_serp, "_fetch_host", lambda urls, *_args: attempted.extend(urls) or [])
    summary = index_serp.index_serp_csv(serp, db, get_settings(), include_sitemap=False, saved_dir=tmp_path / "none")
    assert attempted == [] and summary["skipped"] == 1
