"""OpenAI-compatible chat client and a deterministic mock.

The mock's citation choices are a fixed function of run index so tests can
recompute citation share. Real runs use the configured model and are still
sandbox answers, not official ChatGPT, Perplexity, or Google AI Overview output.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

FANOUT_SYSTEM = (
    "Expand the user question into 4 to 6 web search sub-queries that an AI "
    "answer engine might run. Return JSON only, shaped as "
    '{"subqueries": ["..."]}. Include the original question. Do not add commentary.'
)

ANSWER_SYSTEM = (
    "You are answering inside a citation sandbox. Use only the sources provided. "
    "Do not use outside knowledge and do not invent URLs. Cite sources with their "
    "labels, such as [S1]. If the sources do not support a claim, say so. "
    "Start with a direct answer in the first sentence."
)


def mock_include_passage(rank_index: int, run_index: int) -> bool:
    """Decide whether the mock cites a retrieved passage on this run.

    Rank 0 is cited on every run except when ``run_index % 5 == 4``.
    Rank 1 is cited on even runs. Rank 2 is cited when ``run_index % 3 == 0``.
    Later passages are not cited. This is a stand-in for sampling variation.
    """
    if rank_index == 0:
        return run_index % 5 != 4
    if rank_index == 1:
        return run_index % 2 == 0
    if rank_index == 2:
        return run_index % 3 == 0
    return False


@dataclass(frozen=True)
class PassageContext:
    marker: str
    passage_id: str
    url: str
    domain: str
    heading: str
    text: str
    synthetic: bool


def choose_mock_indices(passage_count: int, run_index: int) -> list[int]:
    chosen = [index for index in range(passage_count) if mock_include_passage(index, run_index)]
    if not chosen and passage_count > 0:
        return [0]
    return chosen


class LLMClient:
    name = "base"
    synthetic = False

    def fanout(self, question: str) -> str:
        raise NotImplementedError

    def answer(self, question: str, passages: list[PassageContext], run_index: int) -> str:
        raise NotImplementedError


def render_sources(passages: list[PassageContext]) -> str:
    blocks: list[str] = []
    for passage in passages:
        label = "synthetic source" if passage.synthetic else "source"
        blocks.append(
            f"[{passage.marker}] ({label}) url={passage.url} domain={passage.domain}\n"
            f"heading: {passage.heading}\n"
            f"{passage.text}"
        )
    return "\n\n".join(blocks)


class MockLLMClient(LLMClient):
    name = "mock"
    synthetic = True

    def fanout(self, question: str) -> str:
        from aeo.sim.fanout import mock_fanout_json

        return mock_fanout_json(question)

    def answer(self, question: str, passages: list[PassageContext], run_index: int) -> str:
        if not passages:
            return (
                f"Simulated run {run_index}. Deterministic mock answer, not a live AI engine. "
                "No indexed passages were available, so nothing was cited."
            )
        chosen = [passages[index] for index in choose_mock_indices(len(passages), run_index)]
        sentences = [
            f"Simulated run {run_index}. Deterministic mock answer, not a live AI engine.",
            f"Question: {question}",
        ]
        for passage in chosen:
            label = passage.domain.split(".")[0]
            snippet = " ".join(passage.text.split())[:180]
            sentences.append(f"{label} says: {snippet} [{passage.marker}]")
        return " ".join(sentences)


class OpenAICompatibleLLM(LLMClient):
    name = "openai"
    synthetic = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout))

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def fanout(self, question: str) -> str:
        return self._complete(
            [
                {"role": "system", "content": FANOUT_SYSTEM},
                {"role": "user", "content": question},
            ],
            json_mode=True,
            temperature=0.2,
        )

    def answer(self, question: str, passages: list[PassageContext], run_index: int) -> str:
        sources = render_sources(passages) or "(no sources)"
        return self._complete(
            [
                {"role": "system", "content": ANSWER_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Variation seed: {run_index}. Write an independently worded answer.\n\n"
                        f"Sources:\n{sources}"
                    ),
                },
            ],
            json_mode=False,
            temperature=0.7,
        )

    def _complete(self, messages: list[dict[str, str]], *, json_mode: bool, temperature: float) -> str:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("LLM provider returned an unexpected response shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("LLM provider returned an empty completion")
        return content
