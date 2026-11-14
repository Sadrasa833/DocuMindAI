from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=(?:[\"'(\[]?[A-Z0-9]))")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTIPLE_WHITESPACE = re.compile(r"\s+")


@dataclass(slots=True)
class TextChunk:
    content: str
    page_number: int
    chunk_index: int


@dataclass(slots=True)
class SourcePage:
    page_number: int
    text: str


def clean_page_text(text: str) -> str:
    """Normalize PDF text without destroying sentence boundaries."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"(?<=\w)-\n(?=\w)", "", normalized)
    normalized = _CONTROL_CHARACTERS.sub(" ", normalized)
    normalized = _MULTIPLE_WHITESPACE.sub(" ", normalized)
    return normalized.strip()


def split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []
    sentences = [sentence.strip() for sentence in _SENTENCE_BOUNDARY.split(text)]
    return [sentence for sentence in sentences if sentence]


def _split_oversized_sentence(sentence: str, chunk_size: int) -> list[str]:
    """Split a sentence that cannot fit into a chunk on word boundaries."""
    words = sentence.split()
    if not words:
        return []
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        if len(word) > chunk_size:
            if current:
                pieces.append(" ".join(current))
                current = []
                current_length = 0
            pieces.extend(word[i : i + chunk_size] for i in range(0, len(word), chunk_size))
            continue
        added_length = len(word) + (1 if current else 0)
        if current and current_length + added_length > chunk_size:
            pieces.append(" ".join(current))
            current = [word]
            current_length = len(word)
        else:
            current.append(word)
            current_length += added_length
    if current:
        pieces.append(" ".join(current))
    return pieces


def _build_units(text: str, chunk_size: int) -> list[str]:
    units: list[str] = []
    for sentence in split_sentences(text):
        if len(sentence) <= chunk_size:
            units.append(sentence)
        else:
            units.extend(_split_oversized_sentence(sentence, chunk_size))
    return units


def chunk_page_text(
    text: str, page_number: int, *, chunk_size: int, chunk_overlap: int, start_index: int = 0
) -> list[TextChunk]:
    units = _build_units(text, chunk_size)
    if not units:
        return []

    chunks: list[TextChunk] = []
    current_units: list[str] = []
    current_length = 0
    index = start_index

    def emit() -> None:
        nonlocal index
        if not current_units:
            return
        content = " ".join(current_units).strip()
        if content:
            chunks.append(TextChunk(content=content, page_number=page_number, chunk_index=index))
            index += 1

    for unit in units:
        proposed_length = current_length + len(unit) + (1 if current_units else 0)
        if current_units and proposed_length > chunk_size:
            emit()
            overlap_units: list[str] = []
            overlap_length = 0
            for previous in reversed(current_units):
                added = len(previous) + (1 if overlap_units else 0)
                if overlap_length + added > chunk_overlap or len(previous) > chunk_size:
                    break
                overlap_units.insert(0, previous)
                overlap_length += added
            while overlap_units and (
                sum(len(item) for item in overlap_units) + len(overlap_units) - 1 + len(unit) + 1
                > chunk_size
            ):
                overlap_units.pop(0)
            current_units = overlap_units
            current_length = sum(len(item) for item in overlap_units) + max(
                len(overlap_units) - 1, 0
            )
        if not current_units and len(unit) > chunk_size:
            index = chunks[-1].chunk_index + 1 if chunks else index
        current_units.append(unit)
        current_length += len(unit) + (1 if len(current_units) > 1 else 0)

    emit()
    return chunks


def chunk_pages(pages: list[SourcePage], *, chunk_size: int, chunk_overlap: int) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    next_index = 0
    for page in pages:
        page_chunks = chunk_page_text(
            page.text,
            page.page_number,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            start_index=next_index,
        )
        chunks.extend(page_chunks)
        if page_chunks:
            next_index = page_chunks[-1].chunk_index + 1
    return chunks
