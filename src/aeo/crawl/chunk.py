"""Split extracted sections into passage-sized chunks."""

from __future__ import annotations

from dataclasses import dataclass

MAX_CHARS = 900
OVERLAP = 120
MAX_CHUNKS = 40


@dataclass(frozen=True)
class ChunkDraft:
    heading_path: str
    text: str


def chunk_sections(
    sections: list[tuple[str, str]],
    *,
    max_chars: int = MAX_CHARS,
    overlap: int = OVERLAP,
    limit: int = MAX_CHUNKS,
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for heading, raw in sections:
        text = " ".join(raw.split())
        if not text:
            continue
        if len(text) <= max_chars:
            chunks.append(ChunkDraft(heading, text))
            if len(chunks) >= limit:
                return chunks
            continue
        start = 0
        while start < len(text) and len(chunks) < limit:
            end = min(len(text), start + max_chars)
            if end < len(text):
                space = text.rfind(" ", start + 1, end)
                if space > start:
                    end = space
            piece = text[start:end].strip()
            if piece:
                chunks.append(ChunkDraft(heading, piece))
            if end >= len(text):
                break
            next_start = end - overlap
            if next_start <= start:
                next_start = end
            start = next_start
        if len(chunks) >= limit:
            return chunks
    return chunks
