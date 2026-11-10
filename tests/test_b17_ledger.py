"""B17 (ledger): fixes for review findings C16, U7, U8, U9 and U10. Fixtures are synthetic; no
network and no paid API."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim.check import CheckResult, CompetitorRow, GapFix, _explain, invented_numbers
from aeo.ranksim.cli import main
from aeo.ranksim.ledger import (
    DEFAULT_PROJECT_SLUG,
    Call,
    RsPrediction,
    _row_hash,
    record_outcome,
    record_prediction,
    render_score,
    score_ledger,
    verify_ledger,
)
from aeo.ranksim.llm import LlmClient, LlmReply, resolve_llm
from aeo.ranksim.store import open_session

QUERY = "dirt work software"
SUBJECT = "https://ours.example/p"
RIVAL = "https://rival.example/"
CLOCK = {"now": datetime(2026, 11, 30, 12, tzinfo=timezone.utc)}


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    import aeo.ranksim.ledger as ledger

    CLOCK["now"] = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(ledger, "_now", lambda: CLOCK["now"])


def _predict(session, *, written, stated=None, query=QUERY):
    CLOCK["now"] = written
    return record_prediction(
        session, kind="draft_check", query=query, subject_url=SUBJECT, subject_html="x", predicted_rank=1, predicted_of=2,
        candidates=[], calls=[Call(RIVAL, "above", 1.0, "HIGH")], confidence="HIGH", calibration_note="",
        provenance={"model": "t"}, created_at=stated, project_slug=DEFAULT_PROJECT_SLUG,
    )


def _observe(session, day, *, recorded):
    CLOCK["now"] = recorded
    record_outcome(session, query=QUERY, observed_on=day, provider="t", positions={SUBJECT: 1, RIVAL: 5}, depth=10,
                   project_slug=DEFAULT_PROJECT_SLUG)


def _append_version_one_row(session, payload, *, stamp="2026-11-05T00:00:00+00:00", slug=DEFAULT_PROJECT_SLUG):
    """A row as another writer leaves it: no ledger_hash_version, a project slug the hash does not bind."""
    previous = session.query(RsPrediction).order_by(RsPrediction.id.desc()).first()
    prev = previous.row_hash if previous else "0" * 64
    session.add(RsPrediction(created_at=stamp, kind="workspace_draft", query=QUERY, subject_url=SUBJECT,
                             project_slug=slug, payload=payload, prev_hash=prev,
                             row_hash=_row_hash(prev, stamp, "workspace_draft", QUERY, SUBJECT, payload)))
    session.commit()


# C16: a row is forward evidence only with a write time stamped by this ledger.


def test_a_version_one_row_with_a_project_and_no_write_time_is_undated_not_scored(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    _predict(session, written=datetime(2026, 11, 1, 12, tzinfo=timezone.utc), query="other query")
    _observe(session, "2026-11-12", recorded=datetime(2026, 11, 12, 12, tzinfo=timezone.utc))
    # Appended on Nov 20, after the Nov 12 result was known, but stated as Nov 5.
    CLOCK["now"] = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    calls = [{"competitor_url": RIVAL, "predicted": "above", "score_gap": 1.0, "confidence": "HIGH", "note": ""}]
    _append_version_one_row(session, {"predicted_rank": 1, "predicted_of": 2, "calls": calls, "provenance": {"dataset": "serper_live"}})
    assert verify_ledger(session)[0]
    score = score_ledger(session)
    assert (score.undated, score.resolved, score.correct, score.legacy) == (1, 0, 0, 0)
    assert "carry no write time" in render_score(score)
    session.close()


def test_a_version_one_row_is_undated_even_with_a_write_time_of_its_own(tmp_path):
    """This ledger writes recorded_at only in version 2 rows, whose hash binds the project. A
    version 1 row's project can be changed without verify noticing, so it is not scored."""
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    calls = [{"competitor_url": RIVAL, "predicted": "above", "score_gap": 1.0, "confidence": "HIGH", "note": ""}]
    _append_version_one_row(session, {"calls": calls, "provenance": {}, "recorded_at": "2026-11-05T00:00:00+00:00"}, slug="other")
    _observe(session, "2026-11-12", recorded=datetime(2026, 11, 12, 12, tzinfo=timezone.utc))
    # Moved into the default project after the fact: verify cannot see it, so it must not be scored there.
    session.execute(text("UPDATE rs_predictions SET project_slug = 'default'"))
    session.commit()
    assert verify_ledger(session)[0]  # the slug of a version 1 row is not in its hash
    score = score_ledger(session)
    assert (score.undated, score.resolved) == (1, 0)
    session.close()


# U7: the "latest local date" cutoff is taken in UTC, whatever offset the stated time carries.


def test_the_cutoff_is_computed_in_utc_whatever_offset_the_stated_time_has(tmp_path):
    session = open_session(f"sqlite:///{tmp_path / 'l.db'}")
    written = datetime(2026, 11, 10, 11, 30, tzinfo=timezone.utc)  # already Nov 11 in UTC+14
    honolulu = timezone(timedelta(hours=-10))
    _predict(session, written=written, stated=written.astimezone(honolulu))
    # Dated Nov 11, so it may have been observed before the prediction; imported on Nov 12.
    _observe(session, "2026-11-11", recorded=datetime(2026, 11, 12, 12, tzinfo=timezone.utc))
    assert score_ledger(session).resolved == 0
    _observe(session, "2026-11-12", recorded=datetime(2026, 11, 13, 12, tzinfo=timezone.utc))
    assert score_ledger(session).resolved == 1
    session.close()


# U8: --expect-head refuses an empty value and accepts a unique prefix of 12 characters or more.


def _three_rows(tmp_path):
    db = f"sqlite:///{tmp_path / 'l.db'}"
    session = open_session(db)
    for day in (5, 6, 7):
        _predict(session, written=datetime(2026, 11, day, tzinfo=timezone.utc))
    head = session.query(RsPrediction).order_by(RsPrediction.id.desc()).first().row_hash
    return db, session, head


def test_an_empty_expected_head_is_refused_not_skipped(tmp_path, capsys):
    db, session, _head = _three_rows(tmp_path)
    session.execute(text("DELETE FROM rs_predictions WHERE id = 3"))
    session.commit()
    session.close()
    with pytest.raises(SystemExit) as raised:
        main(["--database", db, "ledger", "verify", "--expect-head", ""])
    assert raised.value.code == 1
    assert "published head is empty" in capsys.readouterr().out


def test_an_expected_head_may_be_a_unique_prefix_in_any_case(tmp_path, capsys):
    db, session, head = _three_rows(tmp_path)
    session.close()
    main(["--database", db, "ledger", "verify", "--expect-head", head[:12].upper()])
    assert "The published head is prediction 3; 0 predictions were added after it." in capsys.readouterr().out
    main(["--database", db, "ledger", "verify", "--expect-head", head.upper()])
    assert "prediction 3" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "verify", "--expect-head", head[:11]])
    out = capsys.readouterr().out
    assert "first 12 characters" in out and "removed" not in out
    session = open_session(db)
    session.execute(text("DELETE FROM rs_predictions WHERE id = 3"))
    session.commit()
    session.close()
    with pytest.raises(SystemExit):
        main(["--database", db, "ledger", "verify", "--expect-head", head[:12]])
    assert "not in this ledger" in capsys.readouterr().out


# U9: the model name is checked against the provider actually selected.


def _settings(**values):
    base = {"openrouter_api_key": None, "openai_api_key": None, "anthropic_api_key": None, "ranksim_llm_model": None}
    base.update(values)
    return Settings(**base)


def test_a_claude_model_is_not_sent_to_openai_when_auto_picks_openai(tmp_path, monkeypatch):
    import aeo.ranksim.llm as llm

    sent = []
    monkeypatch.setattr(llm.httpx, "post", lambda url, **kwargs: sent.append(url))
    kwargs = {"cache_dir": tmp_path / "c", "cost_log": tmp_path / "l.jsonl"}
    both = {"openai_api_key": "sk-test", "anthropic_api_key": "ant-test", "ranksim_llm_model": "claude-3-5-haiku-20241022"}
    with pytest.raises(ProviderConfigError, match="does not fit the openai provider.*RANKSIM_LLM_PROVIDER=anthropic"):
        resolve_llm(_settings(**both), **kwargs)
    assert sent == []
    chosen = resolve_llm(_settings(**both, ranksim_llm_provider="anthropic"), **kwargs)
    assert (chosen.inner.name, chosen.model) == ("anthropic", "claude-3-5-haiku-20241022")
    # A self-hosted OpenAI-compatible endpoint may serve any name.
    local = resolve_llm(_settings(**both, openai_base_url="http://127.0.0.1:8000/v1"), **kwargs)
    assert (local.inner.name, local.model) == ("openai", "claude-3-5-haiku-20241022")


# U10: digits glued to a word or a period are checked; an honest numbered list is not blocked.

BRIEF = (
    "Query: crm software\nPREDICTED rank 2 of 2\nbeats Rival guide stored_google=4 predicted_rank=1\n"
    "BM25F: A page ahead scores 0.85 on BM25F. This draft scores 0.12. Fix: Use the query in the title, the H1, and the first section."
)


def test_digits_glued_to_a_word_or_a_period_are_checked():
    assert invented_numbers("Expect a top3 spot and x10 traffic.", BRIEF) == ["10", "3"]
    assert invented_numbers("Your page will be No.5 on Google.", BRIEF) == ["5"]
    assert invented_numbers("It scores .85 now and will reach .99.", BRIEF) == [".99"]
    # Restating the brief, a word copied from it, or a heading tag is not an invented number.
    assert invented_numbers("Raise BM25F from .85 and add H3 headings under the H1. It is 2 of 2.", BRIEF) == []


class FakeLlm(LlmClient):
    name, available, model = "fake", True, "fake-model"

    def __init__(self, reply):
        self.reply = reply

    def complete(self, system, user, validate=None, max_tokens=None):
        return LlmReply(text=self.reply, provider="fake", stub=False, model="fake-model")


def test_an_honest_numbered_list_is_not_blocked():
    assert invented_numbers("Fix these: 1. title 2. headings 3. FAQ", BRIEF) == []
    assert invented_numbers("Fix these:\n1) title\n2) headings\n3) FAQ", BRIEF) == []
    # A number that is not part of an ordered list is still checked.
    assert invented_numbers("You will reach rank 3. Then 1.", BRIEF) == ["3"]
    gaps = [GapFix("title match", "A page ahead scores 0.90 on title match. This draft scores 0.10.", "Put the query in the title."),
            GapFix("heading match", "A page ahead scores 0.70 on heading match. This draft scores 0.20.", "Add a heading."),
            GapFix("FAQ", "A page ahead scores 0.60 on FAQ. This draft scores 0.30.", "Add questions and answers.")]
    result = CheckResult(query="crm software", url="https://ours.example/", predicted_rank=2, predicted_of=2, confidence="UNSURE",
                         confidence_note="", competitors=[CompetitorRow("https://r.example/", "Rival guide", 4, 1, "loses", -0.3)],
                         gaps=gaps)
    text_shown, is_template = _explain(FakeLlm("Close three gaps: 1. title 2. headings 3. FAQ."), result)
    assert not is_template and text_shown.startswith("PREDICTED.")
