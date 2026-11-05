"""AI layer guardrails. Fake clients only; no provider is called."""

import pathlib

import pytest

from aeo.ranksim.check import FixRound, _fix_loop, rewrite_problems
from aeo.ranksim.confidence import Calibration
from aeo.ranksim.llm import CachingLlm, LlmClient, LlmError, LlmReply

REPO = pathlib.Path(__file__).resolve().parents[1]


class Scripted(LlmClient):
    name = "openai"
    available = True
    model = "gpt-4o-mini"
    base_url = "https://example.invalid/v1"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user, validate=None):
        self.calls += 1
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return LlmReply(text=item, provider="openai", stub=False, model=self.model, input_tokens=10, output_tokens=5)


def _client(tmp_path, inner, **kw):
    return CachingLlm(inner, tmp_path / "cache", tmp_path / "cost.jsonl", "t", sleep=lambda _s: None, **kw)


def test_retryable_errors_are_retried_then_succeed(tmp_path):
    inner = Scripted([LlmError("HTTP 429", retryable=True), LlmError("HTTP 503", retryable=True), "ok"])
    reply = _client(tmp_path, inner).complete("s", "u")
    assert reply.text == "ok" and inner.calls == 3


def test_permanent_errors_become_a_stub_with_a_note(tmp_path):
    inner = Scripted([LlmError("LLM provider returned HTTP 401. Check the key and model name.")])
    client = _client(tmp_path, inner)
    reply = client.complete("s", "u")
    assert reply.stub and "401" in reply.note and inner.calls == 1
    assert "failed after retries" in client.cost_summary()


def test_spend_cap_refuses_fresh_calls_but_not_cache_hits(tmp_path):
    inner = Scripted(["a", "b"])
    client = _client(tmp_path, inner, max_calls=1)
    assert client.complete("s", "1").text == "a"
    refused = client.complete("s", "2")
    assert refused.stub and "budget reached" in refused.note
    assert client.complete("s", "1").cache_hit
    assert inner.calls == 1


def test_invalid_replies_are_not_cached(tmp_path):
    inner = Scripted(['{"score": 7}', '{"score": 0.7}'])
    client = _client(tmp_path, inner)
    valid = lambda text: '"score": 0.' in text  # noqa: E731
    first = client.complete("s", "u", validate=valid)
    second = client.complete("s", "u", validate=valid)
    assert first.text == '{"score": 7}' and second.text == '{"score": 0.7}'
    assert inner.calls == 2
    assert client.complete("s", "u", validate=valid).cache_hit


def test_cache_key_includes_endpoint(tmp_path):
    a, b = Scripted(["x"]), Scripted(["y"])
    b.base_url = "https://other.invalid/v1"
    assert _client(tmp_path, a).cache_key("s", "u") != _client(tmp_path, b).cache_key("s", "u")


def _page(title, body_words):
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body_words}</p></body></html>"


def test_rewrite_guard_rejects_stuffing_and_large_cuts_but_accepts_a_normal_edit():
    filler = " ".join(f"word{index}" for index in range(400))
    original = _page("Dirt work scheduling for small crews", filler)
    stuffed = _page("Dirt work software dirt work software dirt work software", ("dirt work software " * 60) + filler)
    assert any("of the body" in p for p in rewrite_problems("dirt work software", original, stuffed))
    assert any("in the title" in p for p in rewrite_problems("dirt work software", original, stuffed))
    cut = _page("Dirt work software for small crews", " ".join(filler.split()[:100]))
    assert any("word count fell" in p for p in rewrite_problems("dirt work software", original, cut))
    edited = _page("Dirt work software for small crews", "Dirt work software that schedules crews. " + filler)
    assert rewrite_problems("dirt work software", original, edited) == []


def test_fix_loop_needs_enough_competitors_and_rejects_stuffed_rewrites():
    filler = " ".join(f"word{index}" for index in range(400))
    original = _page("Dirt work scheduling", filler)
    calib = Calibration(None, None, None, "UNSURE")
    llm = Scripted([])
    rounds, _stub, note = _fix_loop(llm, "dirt work software", original, "https://x.example/", [], {}, None, calib, None, "commercial", 2, 3, [])
    assert "only 3 pages" in note and llm.calls == 0 and rounds[0].html == original
    stuffed = _page("Dirt work software dirt work software dirt work software", ("dirt work software " * 80) + filler)
    llm = Scripted([stuffed])
    rounds, _stub, note = _fix_loop(llm, "dirt work software", original, "https://x.example/", [], {}, None, calib, None, "commercial", 3, 6, [])
    assert "rejected" in note
    assert isinstance(rounds[-1], FixRound) and rounds[-1].note.startswith("Rejected")
    assert rounds[-1].predicted_rank == 3  # rank is not credited to a rejected rewrite


def test_ai_eval_refuses_a_paid_judge_run_on_a_tiny_sample(tmp_path, monkeypatch, capsys):
    import aeo.ranksim.llm as llm_module
    from aeo.ranksim.cli import main

    fake = Scripted(['{"score": 0.5}'] * 100)
    fake.cost_summary = lambda: "fake"  # type: ignore[assignment]
    monkeypatch.setattr(llm_module, "resolve_llm", lambda *a, **k: fake)
    monkeypatch.chdir(REPO)
    db = f"sqlite:///{tmp_path / 'ai.db'}"
    main(["--database", db, "ai-eval", "--html", str(tmp_path / "missing.html")])
    out = capsys.readouterr().out
    assert "Coverage:" in out and "Judge not run" in out
    assert fake.calls == 0


def test_relevance_experiment_reports_an_interval():
    from aeo.ranksim.judge import relevance_experiment
    from aeo.ranksim.lambdamart import RankExample

    examples = []
    for q in range(8):
        for position in range(1, 6):
            examples.append(RankExample(f"query {q}", f"https://q{q}p{position}.example/", float(position), {"bm25f": float(position % 3)}))
    scores = {(row.query, row.url): (6 - row.position) / 5 for row in examples}
    result = relevance_experiment(examples, scores, bootstrap=200)
    assert result.measured and result.n_pairs == 8 * 10
    assert result.delta_low is not None and result.delta_high is not None
    assert "95% interval" in result.note and "Random scores" in result.note
    assert "too small" in result.note
