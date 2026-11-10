"""Client-rendered shells are detected narrowly, reported, and never scored as thin pages.
Synthetic HTML."""

from aeo.ranksim.benchmark import coverage_rows
from aeo.ranksim.bootstrap import backfill_js_shell
from aeo.ranksim.corpus import load_corpus_docs
from aeo.ranksim.groundtruth import SerpLabel
from aeo.ranksim.jsshell import js_shell_reason
from aeo.ranksim.lambdamart import build_examples
from aeo.ranksim.loop import FetchResult, store_fetched_page
from aeo.ranksim.store import RsPage, open_session

NEXT_SHELL = (
    '<!DOCTYPE html><html><head><title>Dirt Work Software</title></head><body><div id="__next"></div>'
    '<script src="/_next/static/chunks/main.js"></script><script id="__NEXT_DATA__" type="application/json">{"props":{}}</script>'
    "</body></html>"
)
REACT_SHELL = '<html><head><title>App</title><script src="/app.js"></script></head><body><div id="root"></div></body></html>'
NOSCRIPT_SHELL = (
    '<html><body><noscript>You need to enable JavaScript to run this app.</noscript><div id="mount"></div>'
    '<script src="/bundle.js"></script></body></html>'
)


def _words(count: int) -> str:
    return " ".join(f"word{index}" for index in range(count))


def test_shells_are_detected_and_ordinary_pages_are_not():
    assert "__next" in js_shell_reason(NEXT_SHELL)
    assert "root" in js_shell_reason(REACT_SHELL)
    assert "noscript" in js_shell_reason(NOSCRIPT_SHELL)
    thin_page = f'<html><body><main><h1>Pricing</h1><p>{_words(30)}</p></main><script src="/a.js"></script></body></html>'
    assert js_shell_reason(thin_page) is None  # thin, but its text is in the HTML
    rendered_app = f'<html><body><div id="root"><main><p>{_words(400)}</p></main></div><script src="/a.js"></script></body></html>'
    assert js_shell_reason(rendered_app) is None  # server-rendered into the mount point
    assert js_shell_reason('<html><body><div id="root"></div></body></html>') is None  # no scripts: just empty


def _db(tmp_path):
    db = f"sqlite:///{tmp_path / 's.db'}"
    session = open_session(db)
    full = f"<html><body><main><h1>Dirt work software</h1><p>dirt work software {_words(400)}</p></main></body></html>"
    store_fetched_page(session, FetchResult(url="https://shell.example/", html=NEXT_SHELL, status="fetched"))
    for index in range(1, 4):
        store_fetched_page(session, FetchResult(url=f"https://full{index}.example/", html=full, status="fetched"))
    session.close()
    labels = [SerpLabel("dirt work software", "https://shell.example/", 1, None, "manual_csv")] + [
        SerpLabel("dirt work software", f"https://full{index}.example/", index + 1, None, "manual_csv") for index in range(1, 4)
    ]
    return db, labels


def test_a_shell_is_marked_reported_and_left_out_of_evaluation(tmp_path):
    db, labels = _db(tmp_path)
    session = open_session(db)
    try:
        assert session.get(RsPage, "https://shell.example/").js_shell is True
        assert [row["reason"] for row in coverage_rows(labels, session)][0] == "js_shell"
        pages = load_corpus_docs(session)
    finally:
        session.close()
    examples, uncovered, _withheld = build_examples(labels, pages)
    assert uncovered == 1 and "https://shell.example/" not in {row.url for row in examples}


def test_older_rows_are_backfilled(tmp_path):
    db, _labels = _db(tmp_path)
    session = open_session(db)
    session.query(RsPage).update({RsPage.js_shell: None})
    session.commit()
    session.close()
    assert backfill_js_shell(db) == 1
    session = open_session(db)
    try:
        assert session.get(RsPage, "https://shell.example/").js_shell is True
        assert session.get(RsPage, "https://full1.example/").js_shell is False
    finally:
        session.close()


def test_a_check_refuses_a_shell_draft_and_leaves_out_shell_competitors(tmp_path):
    from aeo.ranksim.check import check_draft
    from aeo.ranksim.confidence import Calibration

    db, labels = _db(tmp_path)
    session = open_session(db)
    try:
        pages = load_corpus_docs(session)
    finally:
        session.close()

    class Model:
        def feature_name(self):
            return ["bm25f"]

        def predict(self, matrix):
            return [float(len(matrix) - index) for index in range(len(matrix))]

        def feature_importance(self, importance_type="gain"):
            return [0.0]

    calibration = Calibration(None, None, None, "")
    refused = check_draft(query="dirt work software", html=REACT_SHELL, url="https://ours.example/", labels=labels,
                          pages=pages, model=Model(), calibration=calibration, run_ai=False)
    assert refused.predicted_rank is None and "client-rendered shell" in refused.error
    draft = f"<html><body><main><h1>Dirt work software</h1><p>dirt work software {_words(400)}</p></main></body></html>"
    checked = check_draft(query="dirt work software", html=draft, url="https://ours.example/", labels=labels,
                          pages=pages, model=Model(), calibration=calibration, run_ai=False)
    assert checked.predicted_of == 4  # three full competitors and the draft, not the shell
    assert "https://shell.example/" in checked.missing_urls
    assert any("client-rendered shells" in warning for warning in checked.warnings)
