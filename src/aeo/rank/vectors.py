"""Deterministic hashing embeddings and cosine similarity.

The mock embedder maps tokens into a fixed vector with SHA-256. Similar text
lands nearby because shared tokens increment the same coordinates. It is not a
neural embedding and is labeled ``mock`` wherever it is used.
"""

from __future__ import annotations

import hashlib
import math

from aeo.textutil import tokenize_for_search

DEFAULT_DIM = 64


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same dimension")
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def hash_embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    if dim < 8:
        raise ValueError("embedding dim must be at least 8")
    vector = [0.0] * dim
    for token in tokenize_for_search(text):
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    return l2_normalize(vector)


def format_vector_literal(vector: list[float]) -> str:
    """pgvector text input, e.g. ``[0.100000,-0.200000]``."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
