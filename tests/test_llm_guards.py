"""LLM explanations may only restate numbers from their brief; the provider and model name must fit.
No network: fake LLM clients only."""

import pytest

from aeo.config import Settings
from aeo.errors import ProviderConfigError
from aeo.ranksim.check import CheckResult, CompetitorRow, _explain, invented_numbers
from aeo.ranksim.llm import LlmClient, LlmReply, model_format_problem, resolve_llm, select_llm_provider


class FakeLlm(LlmClient):
    name, available, model = "fake", True, "fake-model"

    def __init__(self, text):
        self.text = text

    def complete(self, system, user, validate=None, max_tokens=None):
        return LlmReply(text=self.text, provider="fake", stub=False, model="fake-model")


def _result():
    return CheckResult(
        query="hcss alternative", url="https://ours.example/", predicted_rank=4, predicted_of=6, confidence="UNSURE",
        confidence_note="", competitors=[CompetitorRow("https://r.example/", "Rival 2024 guide", 1, 2, "loses", -0.3)],
    )


def test_numbers_must_come_from_the_brief():
    brief = "Query: hcss alternative\nPREDICTED rank 4 of 6\nloses Rival 2024 guide stored_google=1 predicted_rank=2"
    assert invented_numbers("Your page is PREDICTED 4 of 6, behind the 2024 guide.", brief) == []
    assert invented_numbers("You will reach #2 within 3 weeks and gain 40% traffic.", brief) == ["3", "40"]
    assert invented_numbers("Rank 4.0 of 6", brief) == []


def test_an_explanation_with_an_invented_rank_is_not_shown():
    text, is_template = _explain(FakeLlm("This page will rank #3 in 10 days."), _result())
    assert is_template and "not in the brief (10, 3)" in text and "PREDICTED loss versus Rival 2024 guide" in text
    # Known limit: a number that appears anywhere in the brief passes, whatever it is attached to
    # (here 1 is the rival's stored Google position).
    assert not _explain(FakeLlm("The rival is #1 on Google."), _result())[1]
    good, is_template = _explain(FakeLlm("It is PREDICTED 4 of 6 because the rival covers the topic better."), _result())
    assert not is_template and good.startswith("PREDICTED.")


def _settings(**values):
    base = {"openrouter_api_key": None, "openai_api_key": None, "anthropic_api_key": None, "ranksim_llm_model": None}
    base.update(values)
    return Settings(**base)


def test_the_provider_is_chosen_explicitly_or_by_key(tmp_path):
    assert select_llm_provider(_settings(openai_api_key="sk-or-abc")) == ("openrouter", "sk-or-abc")  # an OpenRouter key
    assert select_llm_provider(_settings(openai_api_key="sk-abc", anthropic_api_key="ant"))[0] == "openai"
    assert select_llm_provider(_settings(openrouter_api_key="or", anthropic_api_key="ant", ranksim_llm_provider="anthropic"))[0] == "anthropic"
    with pytest.raises(ProviderConfigError, match="needs ANTHROPIC_API_KEY"):
        select_llm_provider(_settings(openrouter_api_key="or", ranksim_llm_provider="anthropic"))
    with pytest.raises(ProviderConfigError, match="needs OPENAI_API_KEY"):
        select_llm_provider(_settings(openai_api_key="sk-or-abc", ranksim_llm_provider="openai"))
    assert resolve_llm(_settings(), cache_dir=tmp_path / "c", cost_log=tmp_path / "l.jsonl").available is False


def test_a_model_name_that_does_not_fit_the_provider_is_refused(tmp_path):
    kwargs = {"cache_dir": tmp_path / "c", "cost_log": tmp_path / "l.jsonl"}
    with pytest.raises(ProviderConfigError, match="OpenAI model names have no vendor prefix"):
        resolve_llm(_settings(openai_api_key="sk-abc", ranksim_llm_model="openai/gpt-4o-mini"), **kwargs)
    with pytest.raises(ProviderConfigError, match="vendor/model"):
        resolve_llm(_settings(openrouter_api_key="or", ranksim_llm_model="gpt-4o-mini"), **kwargs)
    with pytest.raises(ProviderConfigError, match="start with claude"):
        resolve_llm(_settings(anthropic_api_key="ant", ranksim_llm_model="gpt-4o-mini"), **kwargs)
    assert resolve_llm(_settings(openrouter_api_key="or"), **kwargs).model == "openai/gpt-4o-mini"  # MISSION default
    assert model_format_problem("openai", "meta-llama/Llama-3", openai_base_url="http://localhost:8000/v1") is None
