"""Embedding providers. The mock is a hashing embedder; OpenAI is optional."""

from __future__ import annotations

import httpx

from aeo.rank.vectors import hash_embed


class Embedder:
    name = "base"
    synthetic = False

    def embed(self, text: str) -> list[float]:
        vectors = self.embed_many([text])
        return vectors[0] if vectors else []

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class MockEmbedder(Embedder):
    name = "mock"
    synthetic = True

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(text, self.dim) for text in texts]


class OpenAICompatibleEmbedder(Embedder):
    name = "openai"
    synthetic = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dim: int,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.dim = dim
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout))
        self.base_url = base_url.rstrip("/")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = self.client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": batch},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json().get("data") or []
            ordered = sorted(data, key=lambda item: item.get("index", 0))
            if len(ordered) != len(batch):
                raise RuntimeError("Embedding provider returned a different number of vectors than inputs")
            for item in ordered:
                vector = item.get("embedding")
                if not isinstance(vector, list):
                    raise RuntimeError("Embedding provider returned a malformed vector")
                if len(vector) != self.dim:
                    raise RuntimeError(
                        f"Embedding model returned dimension {len(vector)}, but EMBEDDING_DIM is {self.dim}. "
                        "Set EMBEDDING_DIM to the model dimension and reindex."
                    )
                vectors.append([float(value) for value in vector])
        return vectors
