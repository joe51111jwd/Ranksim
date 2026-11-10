"""Regression tests for defects an independent review found on this branch."""

import pathlib
import threading
from datetime import datetime, timezone

import httpx
import pytest

from aeo.ranksim.groundtruth import ImportReport, SerpLabel, load_serp_file, navboost_leaks, navboost_withhold_reason
from aeo.ranksim.ledger import Call, RsPrediction, record_check, record_outcome, record_prediction, score_ledger, verify_ledger
from aeo.ranksim.loop import FetchResult, calibrate, store_fetched_page
from aeo.ranksim.serp import BraveFallbackProvider, SerpCollectError, SerpHit
from aeo.ranksim.store import RsPage, open_session, replace_serp

REPO = pathlib.Path(__file__).resolve().parents[1]
HTML = "<html><head><title>{t}</title></head><body><h1>{t}</h1><p>{b}</p></body></html>"


def test_seeded_drafts_do_not_block_a_crawl_of_the_live_page(tmp_path, monkeypatch):
    from aeo.ranksim.bootstrap import seed_corpus_if_empty
    from aeo.ranksim.index_serp import _already_fetched

    monkeypatch.chdir(REPO)
    db = f"sqlite:///{tmp_path / 's.db'}"
    seed_corpus_if_empty(db)
    skipped = _already_fetched(db)
    assert not any("smartcrewhq.com" in url for url in skipped)
    session = open_session(db)
    draft_url = next(row.url for row in session.query(RsPage).all() if row.source == "draft_bundle")
    store_fetched_page(session, FetchResult(url=draft_url, html=HTML.format(t="Live", b="live " * 200), status="fetched"))
    row = session.get(RsPage, draft_url)
    session.close()
    assert row.source == "crawl" and "live" in row.raw_html


def test_calibrate_never_trains_on_draft_html(tmp_path):
    from aeo.ranksim.keywords import build_keywords

    session = open_session(f"sqlite:///{tmp_path / 'c.db'}")
    hits = []
    for position, (url, source) in enumerate(
        [("https://a.example/", "crawl"), ("https://www.smartcrewhq.com/x/", "draft_bundle"), ("https://b.example/", "crawl")], start=1
    ):
        store_fetched_page(session, FetchResult(url=url, html=HTML.format(t="dirt work", b="dirt work " * 100), status="fetched", source=source))
        hits.append(SerpHit(position, url, "", "", "browser_export", False, True))
    replace_serp(session, "dirt work software", hits)
    report, _label, _provider = calibrate(session, build_keywords())
    session.close()
    assert report.n_pairs == 1  # only the two crawled pages form a pair


def test_benchmark_marks_drafts_in_an_old_database(tmp_path, monkeypatch, capsys):
    from aeo.ranksim.cli import main

    monkeypatch.chdir(REPO)
    db = f"sqlite:///{tmp_path / 'old.db'}"
    session = open_session(db)
    draft = (REPO / "smartcrew/pr8_v3/home.html").read_text(encoding="utf-8", errors="replace")
    store_fetched_page(session, FetchResult(url="https://www.smartcrewhq.com/", html=draft, status="fetched", source="unknown"))
    session.close()
    main(["--database", db, "benchmark", "smartcrew/serp.csv", "--bootstrap", "10", "--as-of", "2026-10-29"])
    out = capsys.readouterr().out
    assert "marked as drafts" in out


def test_concurrent_ledger_appends_keep_one_chain(tmp_path):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    open_session(db).close()
    errors = []

    def worker(index):
        session = open_session(db)
        try:
            record_prediction(
                session, kind="draft_check", query="q", subject_url=f"https://ours.example/{index}", subject_html="x",
                predicted_rank=1, predicted_of=2, candidates=[], calls=[Call("https://r.example/", "above", 1.0, "UNSURE")],
                confidence="UNSURE", calibration_note="", provenance={},
                created_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    session = open_session(db)
    ok, message = verify_ledger(session)
    count = session.query(RsPrediction).count()
    session.close()
    assert not errors and count == 8 and ok, message


def _ledger_clock(monkeypatch, moment):
    import aeo.ranksim.ledger as ledger

    monkeypatch.setattr(ledger, "_now", lambda: moment)


def test_placeholder_subjects_are_not_recorded_and_absent_subjects_stay_unresolved(tmp_path, monkeypatch):
    from aeo.ranksim.check import CheckResult, CompetitorRow
    from aeo.ranksim.confidence import Calibration

    _ledger_clock(monkeypatch, datetime(2026, 11, 5, tzinfo=timezone.utc))
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    pasted = CheckResult(query="q", url="https://draft.local/page", predicted_rank=1, predicted_of=2, confidence="UNSURE",
                         confidence_note="", competitors=[CompetitorRow("https://r.example/", "R", 1, 2, "beats", 0.4)])
    assert record_check(session, pasted, html="<html></html>", calibration=Calibration(None, None, None, ""), provenance={}) is None
    record_prediction(session, kind="draft_check", query="q", subject_url="https://ours.example/new", subject_html="x",
                      predicted_rank=1, predicted_of=2, candidates=[], calls=[Call("https://r.example/", "above", 1.0, "UNSURE")],
                      confidence="UNSURE", calibration_note="", provenance={}, created_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
                      project_slug="default")
    _ledger_clock(monkeypatch, datetime(2026, 11, 30, tzinfo=timezone.utc))
    record_outcome(session, query="q", observed_on="2026-11-20", provider="t", positions={"https://r.example/": 3}, depth=10,
                   project_slug="default")
    score = score_ledger(session)
    session.close()
    assert (score.resolved, score.unresolved) == (0, 1)


def test_outcomes_must_postdate_the_prediction_in_any_timezone(tmp_path, monkeypatch):
    _ledger_clock(monkeypatch, datetime(2026, 11, 5, 16, tzinfo=timezone.utc))
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    # 16:00 UTC on 11-05 is already 11-06 in Tokyo; an observation dated 11-06 may predate it.
    record_prediction(session, kind="draft_check", query="q", subject_url="https://ours.example/p", subject_html="x",
                      predicted_rank=1, predicted_of=2, candidates=[], calls=[Call("https://r.example/", "above", 1.0, "UNSURE")],
                      confidence="UNSURE", calibration_note="", provenance={}, created_at=datetime(2026, 11, 5, 16, tzinfo=timezone.utc),
                      project_slug="default")
    _ledger_clock(monkeypatch, datetime(2026, 11, 8, tzinfo=timezone.utc))
    positions = {"https://ours.example/p": 1, "https://r.example/": 2}
    record_outcome(session, query="q", observed_on="2026-11-06", provider="t", positions=positions, depth=10, project_slug="default")
    assert score_ledger(session).resolved == 0
    record_outcome(session, query="q", observed_on="2026-11-07", provider="t", positions=positions, depth=10, project_slug="default")
    assert score_ledger(session).resolved == 1
    session.close()


def test_prior_dates_must_be_iso_and_earlier(tmp_path):
    us = tmp_path / "us.csv"
    us.write_text("query,url,position,prior_position,observed_on,prior_observed_on\nq,https://a.example/,1,3,12/20/2025,01/05/2026\n")
    rows = load_serp_file(us)
    assert navboost_leaks(rows) and "ISO" in navboost_withhold_reason(rows)
    unpadded = [SerpLabel("q", "https://a.example/", 1, 3, "manual_csv", observed_on="2025-9-30", prior_observed_on="2025-10-1")]
    assert navboost_leaks(unpadded)


def test_lowercase_page_column_is_a_snapshot_and_observe_refuses_search_console(tmp_path):
    from aeo.ranksim.cli import main

    manual = tmp_path / "manual.csv"
    manual.write_text("query,page,position,observed_on\nq,https://a.example/,1,2026-10-29\n")
    assert load_serp_file(manual)[0].kind == "serp_snapshot"
    gsc = tmp_path / "gsc.csv"
    gsc.write_text("Query,Page,Clicks,Impressions,CTR,Position\nq,https://a.example/,3,100,3%,7.4\n")
    with pytest.raises(SystemExit, match="Search Console"):
        main(["--database", f"sqlite:///{tmp_path / 'l.db'}", "ledger", "observe", str(gsc), "--observed-on", "2026-11-20", "--provider", "gsc"])


def test_brave_error_bodies_and_429_are_collection_failures(monkeypatch):
    import aeo.ranksim.serp as serp_module

    monkeypatch.setattr(serp_module.time, "sleep", lambda _s: None)
    body = BraveFallbackProvider("k", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"type": "ErrorResponse", "error": {"code": "x"}}))))
    with pytest.raises(SerpCollectError):
        body.search("q")
    limited = BraveFallbackProvider("k", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={}))))
    with pytest.raises(SerpCollectError, match="429"):
        limited.search("q")


def test_report_does_not_count_failed_checks_as_not_ranking(tmp_path):
    from aeo.ranksim.report import _track_block
    from aeo.ranksim.store import RsRankCheck

    db = f"sqlite:///{tmp_path / 'r.db'}"
    session = open_session(db)
    for query in ("a", "b"):
        session.add(RsRankCheck(query=query, checked_on="2026-11-05", our_position=None, our_url=None, provider="serper",
                                synthetic=False, is_google=True, top_json=[], status="error", error="HTTP 503"))
    session.add(RsRankCheck(query="c", checked_on="2026-11-05", our_position=4, our_url="https://www.smartcrewhq.com/", provider="serper",
                            synthetic=False, is_google=True, top_json=[], status="ok"))
    session.commit()
    session.close()
    text = _track_block(db)
    assert "appeared in 1 of 1 collected queries" in text and "failed for 2 queries" in text


def test_a_stuffed_draft_is_flagged_next_to_the_verdict():
    from aeo.ranksim.check import stuffing_warnings
    from aeo.ranksim.features import PageDoc

    filler = " ".join(f"word{index}" for index in range(300))
    stuffed = PageDoc(url="https://x.example/", title="dirt work software dirt work software dirt work software",
                      body=("dirt work software " * 60) + filler)
    normal = PageDoc(url="https://x.example/", title="Dirt work software for crews", body="Dirt work software for crews. " + filler)
    peers = [PageDoc(url=f"https://p{index}.example/", title="Best dirt work software", body="Dirt work software for crews. " + filler) for index in range(3)]
    assert len(stuffing_warnings("dirt work software", stuffed, peers)) == 2
    assert stuffing_warnings("dirt work software", normal, peers) == []
    assert stuffing_warnings("dirt work software", stuffed, []) == []  # nothing to compare with: no claim


def test_labeled_query_without_pages_gets_an_accurate_message():
    from aeo.ranksim.check import check_draft
    from aeo.ranksim.confidence import Calibration

    labels = [SerpLabel("dirt work software", "https://a.example/", 1, None, "manual_csv")]
    result = check_draft(query="dirt work software", html="<html></html>", url="https://x.example/", labels=labels,
                         pages={}, model=None, calibration=Calibration(None, None, None, ""), run_ai=False)
    assert "is in the labeled file" in result.error


def test_an_unchanged_on_topic_draft_passes_the_rewrite_guard():
    from aeo.ranksim.check import rewrite_problems

    sample = (REPO / "smartcrew/pr8_v3/compare-hcss-heavyjob-alternative.html").read_text(errors="replace")
    assert rewrite_problems("hcss heavyjob alternative", sample, sample) == []


def test_llm_budget_counts_every_attempt_and_fails_closed_for_unpriced_models(tmp_path):
    from aeo.ranksim.llm import CachingLlm, LlmClient, LlmError, LlmReply

    class Flaky(LlmClient):
        name, available, model = "openai", True, "gpt-4o-mini"
        sent = 0

        def complete(self, system, user, validate=None, **kwargs):
            self.sent += 1
            raise LlmError("timed out", retryable=True)

    flaky = Flaky()
    client = CachingLlm(flaky, tmp_path / "c", tmp_path / "l.jsonl", "t", max_calls=2, sleep=lambda _s: None)
    for index in range(5):
        client.complete("s", f"u{index}")
    assert flaky.sent == 2

    class Unpriced(LlmClient):
        name, available, model = "openrouter", True, "vendor/unpriced-model"
        sent = 0

        def complete(self, system, user, validate=None, **kwargs):
            self.sent += 1
            return LlmReply(text="ok", provider="openrouter", stub=False, model=self.model, input_tokens=300_000, output_tokens=1)

    unpriced = Unpriced()
    client = CachingLlm(unpriced, tmp_path / "c2", tmp_path / "l2.jsonl", "t", max_usd=0.01)
    replies = [client.complete("s", f"u{index}") for index in range(5)]
    assert unpriced.sent == 1 and "cannot be enforced" in replies[-1].note
    disabled = CachingLlm(Unpriced(), tmp_path / "c3", tmp_path / "l3.jsonl", "t", max_usd=0)
    assert not any(disabled.complete("s", f"u{index}").stub for index in range(3))


def test_truncated_llm_replies_are_failures_not_rewrites(monkeypatch):
    import aeo.ranksim.llm as llm_module

    monkeypatch.setattr(llm_module, "_post", lambda *a, **k: {"choices": [{"message": {"content": "<html>half"}, "finish_reason": "length"}]})
    with pytest.raises(llm_module.LlmError, match="cut off"):
        llm_module.OpenAiLlm("k", "gpt-4o-mini", "https://example.invalid/v1").complete("s", "u", max_tokens=10)
