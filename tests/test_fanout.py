import json

import pytest

from aeo.errors import FanoutParseError
from aeo.providers.llm import MockLLMClient
from aeo.sim.fanout import expand_question, mock_subqueries, parse_subqueries


def test_mock_fanout_is_deterministic_and_keeps_the_question():
    question = "What is the best trail mix for long hikes?"
    first = mock_subqueries(question)
    second = mock_subqueries(question)
    assert first == second
    assert first[0] == question
    assert len(first) == len(set(item.casefold() for item in first))
    result = expand_question(question, MockLLMClient())
    assert result.synthetic is True
    assert result.provider == "mock"
    assert result.subqueries[0] == question


def test_parser_reads_fenced_json_and_inserts_the_original():
    payload = """Here you go:
```json
{"subqueries": ["trail mix sodium", "trail mix sodium"]}
```
"""
    parsed = parse_subqueries(payload, "What is trail mix sodium?")
    assert parsed[0] == "What is trail mix sodium?"
    assert parsed[1] == "trail mix sodium"
    assert len(parsed) == 2


def test_parser_rejects_non_json():
    with pytest.raises(FanoutParseError):
        parse_subqueries("not json", "original question")


def test_parser_accepts_a_dict():
    parsed = parse_subqueries({"queries": ["alpha"]}, "alpha")
    assert parsed == ["alpha"]
    raw = json.dumps({"subqueries": []})
    assert parse_subqueries(raw, "kept") == ["kept"]
