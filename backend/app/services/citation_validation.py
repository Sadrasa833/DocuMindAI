from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.repositories.chunks import SearchHit

logger = logging.getLogger(__name__)

_CITATION_GROUP = re.compile(r"\[([^\[\]]*)\]")
_CITATION_LABEL = re.compile(r"\bS(\d+)\b", re.IGNORECASE)
_REPEATED_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?])")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_LEADING_CONJUNCTION = re.compile(r"^(?:and|but|or|و|اما|ولی)\s+", re.IGNORECASE)
_SENTENCE_BOUNDARIES = frozenset(".!?۔؟\n;")
_CLAUSE_SPLIT = re.compile(
    r"(?<=[.!?۔؟;])\s+|\n+|,\s*|\s+(?:and|but|or|و|اما|ولی)\s+",
    re.IGNORECASE,
)
_NEGATIONS = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "cannot",
        "without",
        "neither",
        "nor",
        "نه",
        "نیست",
        "نبود",
        "نمی",
        "هیچ",
        "بدون",
        "هرگز",
    }
)
_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "یک": 1,
    "دو": 2,
    "سه": 3,
    "چهار": 4,
    "پنج": 5,
    "شش": 6,
    "هفت": 7,
    "هشت": 8,
    "ده": 10,
    "بیست": 20,
    "سی": 30,
    "شصت": 60,
    "نود": 90,
}
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "more",
        "most",
        "must",
        "of",
        "on",
        "or",
        "our",
        "shall",
        "she",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "و",
        "در",
        "به",
        "از",
        "که",
        "این",
        "آن",
        "است",
        "هست",
        "برای",
        "با",
        "را",
        "می",
        "تا",
        "یا",
        "بر",
        "شود",
        "کرد",
        "بود",
        "خود",
        "های",
        "ها",
    ]
)


class EmbeddingServiceProtocol(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class CitationValidationResult:
    answer: str
    citations: list[dict[str, Any]]
    had_citations: bool
    supported_claims: int
    unsupported_claims: int
    require_citations: bool

    @property
    def should_refuse(self) -> bool:
        if self.unsupported_claims:
            return True
        return not self.citations and (self.had_citations or self.require_citations)


@dataclass(slots=True)
class _ClaimGroup:
    start: int
    end: int
    indexes: list[int]
    claim: str


@dataclass(slots=True)
class _Candidate:
    index: int
    coverage: float
    semantic: float | None
    terms: set[str]


def _citation(hit: SearchHit) -> dict[str, Any]:
    return {
        "document_id": str(hit.chunk.document_id),
        "document_name": hit.document_name,
        "page_number": hit.chunk.page_number,
        "chunk_id": str(hit.chunk.id),
    }


def _clean_spacing(text: str) -> str:
    cleaned = _REPEATED_SPACES.sub(" ", text)
    cleaned = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", cleaned)
    return cleaned.strip()


def resolve_citations(answer: str, hits: list[SearchHit]) -> tuple[str, list[dict[str, Any]]]:
    """Keep only source labels the model actually cited and that exist.

    Source labels are assigned by :func:`build_sources` (S1..Sn). Any fabricated
    or unknown label is removed from the answer and never returned as a citation.
    """
    if not hits:
        return answer, []

    cited_indexes: list[int] = []
    seen_indexes: set[int] = set()
    available = len(hits)

    def replace_group(match: re.Match[str]) -> str:
        labels = list(_CITATION_LABEL.finditer(match.group(1)))
        if not labels:
            return match.group(0)

        valid_labels: list[str] = []
        for item in labels:
            index = int(item.group(1))
            if 1 <= index <= available:
                valid_labels.append(f"S{index}")
                if index - 1 not in seen_indexes:
                    seen_indexes.add(index - 1)
                    cited_indexes.append(index - 1)

        if valid_labels:
            return f"[{', '.join(valid_labels)}]"
        return ""

    sanitized = _CITATION_GROUP.sub(replace_group, answer)
    sanitized = _clean_spacing(sanitized)

    invalid_labels = [
        label for label in _CITATION_LABEL.findall(answer) if not 1 <= int(label) <= available
    ]
    if invalid_labels:
        logger.warning(
            "Discarded fabricated or out-of-range citations",
            extra={"labels": sorted(set(invalid_labels)), "available": available},
        )

    return sanitized, [_citation(hits[index]) for index in cited_indexes]


def build_sources(hits: list[SearchHit]) -> list[dict[str, Any]]:
    return [
        {
            "source_label": f"S{index}",
            "document": hit.document_name,
            "page": hit.chunk.page_number,
            "content": hit.chunk.content,
        }
        for index, hit in enumerate(hits, start=1)
    ]


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return normalized


def _number(token: str) -> str | None:
    if token in _NUMBER_WORDS:
        return str(_NUMBER_WORDS[token])
    if token.isdigit():
        try:
            return str(int(token))
        except ValueError:
            return token
    return None


def _content_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in _WORD.findall(_normalize(text)):
        if token in _STOPWORDS:
            continue
        terms.append(_number(token) or token)
    return terms


def _has_negation(text: str) -> bool:
    normalized = _normalize(text)
    if "n't" in normalized:
        return True
    return any(token in _NEGATIONS for token in _WORD.findall(normalized))


def _clauses(text: str) -> list[str]:
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT.split(text) if clause.strip()]
    return clauses or ([text.strip()] if text.strip() else [])


def _evidence_terms(content: str, *, claim_negated: bool) -> tuple[set[str], set[str]] | None:
    positive: set[str] = set()
    negative: set[str] = set()
    for clause in _clauses(content):
        terms = set(_content_terms(clause))
        if _has_negation(clause):
            negative.update(terms)
        else:
            positive.update(terms)

    if claim_negated:
        if not negative:
            return None
        return positive | negative, negative
    if not positive:
        return None
    return positive, set()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _clean_claim(text: str) -> str:
    claim = _CITATION_GROUP.sub(" ", text)
    claim = _clean_spacing(claim)
    return _LEADING_CONJUNCTION.sub("", claim)


def _lookahead_claim(text: str, start: int) -> str:
    end = len(text)
    for index in range(start, len(text)):
        if text[index] in _SENTENCE_BOUNDARIES:
            end = index
            break
    return _clean_claim(text[start:end])


def _claim_groups(text: str) -> list[_ClaimGroup]:
    groups: list[_ClaimGroup] = []
    previous_end = 0
    previous_claim = ""
    for match in _CITATION_GROUP.finditer(text):
        indexes = [int(item.group(1)) for item in _CITATION_LABEL.finditer(match.group(1))]
        if not indexes:
            continue

        left = previous_end
        for index in range(match.start() - 1, left - 1, -1):
            if text[index] in _SENTENCE_BOUNDARIES:
                left = index + 1
                break

        claim = _clean_claim(text[left : match.start()])
        if not _content_terms(claim):
            claim = previous_claim or _lookahead_claim(text, match.end())

        groups.append(
            _ClaimGroup(
                start=match.start(),
                end=match.end(),
                indexes=list(dict.fromkeys(indexes)),
                claim=claim,
            )
        )
        previous_end = match.end()
        previous_claim = claim or previous_claim
    return groups


def _candidate(
    *,
    index: int,
    claim_terms: set[str],
    claim_negated: bool,
    hit: SearchHit,
    claim_vector: Sequence[float] | None,
) -> _Candidate | None:
    evidence = _evidence_terms(hit.chunk.content, claim_negated=claim_negated)
    if evidence is None:
        return None

    source_terms, negative_terms = evidence
    claim_numbers = {term for term in claim_terms if term.isdigit()}
    if claim_negated and claim_numbers and not claim_numbers <= negative_terms:
        return None

    shared = claim_terms & source_terms
    semantic = None
    if claim_vector is not None:
        semantic = _cosine(claim_vector, hit.chunk.embedding)
    if not shared and semantic is None:
        return None

    return _Candidate(
        index=index,
        coverage=len(shared) / len(claim_terms),
        semantic=semantic,
        terms=source_terms,
    )


def _supporting_indexes(
    claim: str,
    indexes: list[int],
    hits: list[SearchHit],
    *,
    min_support: float,
    claim_vector: Sequence[float] | None,
    semantic_threshold: float,
) -> list[int]:
    claim_terms = set(_content_terms(claim))
    if not claim_terms:
        return []

    numbers = {term for term in claim_terms if term.isdigit()}
    claim_negated = _has_negation(claim)
    candidates = [
        candidate
        for index in indexes
        if (
            candidate := _candidate(
                index=index,
                claim_terms=claim_terms,
                claim_negated=claim_negated,
                hit=hits[index - 1],
                claim_vector=claim_vector,
            )
        )
        is not None
    ]
    candidates.sort(
        key=lambda candidate: (candidate.coverage, candidate.semantic or 0.0),
        reverse=True,
    )

    kept: list[_Candidate] = []
    union_terms: set[str] = set()
    missing_terms = set(claim_terms)
    semantic_supported = False
    for candidate in candidates:
        semantic_strong = (
            candidate.semantic is not None and candidate.semantic >= semantic_threshold
        )
        if semantic_strong:
            semantic_supported = True
        if not semantic_strong and not candidate.terms & missing_terms:
            continue
        kept.append(candidate)
        union_terms.update(candidate.terms)
        missing_terms.difference_update(candidate.terms)
        if not missing_terms:
            break

    coverage = len(claim_terms & union_terms) / len(claim_terms)
    numbers_supported = numbers <= union_terms
    if not numbers_supported or (coverage < min_support and not semantic_supported):
        return []
    kept_indexes = {candidate.index for candidate in kept}
    return [index for index in indexes if index in kept_indexes]


async def validate_citations(
    answer: str,
    hits: list[SearchHit],
    *,
    min_support: float = 0.55,
    require_citations: bool = True,
    embedding_service: EmbeddingServiceProtocol | None = None,
    semantic_threshold: float = 0.45,
) -> CitationValidationResult:
    """Validate claim-level support for every cited source.

    Structural validation removes fabricated labels first. Each remaining
    citation group is checked against the sentence or clause it accompanies
    using numeric, negation, and lexical evidence. Negation is scoped to the
    matching clause so an unrelated negated sentence does not invalidate a
    supported claim. When an embedding service is supplied, semantic similarity
    is also considered. Unsupported citations are removed, and any unsupported
    cited claim makes the result refuse so a partially verified answer is never
    returned as if it were fully supported.
    """
    had_citations = any(
        _CITATION_LABEL.search(match.group(1)) for match in _CITATION_GROUP.finditer(answer)
    )
    if not hits:
        sanitized = _CITATION_GROUP.sub(
            lambda match: "" if _CITATION_LABEL.search(match.group(1)) else match.group(0),
            answer,
        )
        return CitationValidationResult(
            answer=_clean_spacing(sanitized),
            citations=[],
            had_citations=had_citations,
            supported_claims=0,
            unsupported_claims=1 if had_citations else 0,
            require_citations=require_citations,
        )

    sanitized, _ = resolve_citations(answer, hits)
    groups = _claim_groups(sanitized)

    claim_vectors: dict[str, list[float]] = {}
    unique_claims = list(dict.fromkeys(group.claim for group in groups if group.claim))
    if embedding_service is not None and unique_claims:
        try:
            vectors = await embedding_service.embed_documents(unique_claims)
            claim_vectors = dict(zip(unique_claims, vectors, strict=True))
        except Exception:
            logger.exception("Semantic citation verification failed; using deterministic checks")

    parts: list[str] = []
    cursor = 0
    supported_indexes: list[int] = []
    supported_claims = 0
    unsupported_claims = 0
    for group in groups:
        support = _supporting_indexes(
            group.claim,
            group.indexes,
            hits,
            min_support=min_support,
            claim_vector=claim_vectors.get(group.claim),
            semantic_threshold=semantic_threshold,
        )
        if support:
            supported_claims += 1
            for index in support:
                if index not in supported_indexes:
                    supported_indexes.append(index)
            replacement = f"[{', '.join(f'S{index}' for index in support)}]"
        else:
            unsupported_claims += 1
            replacement = ""
            logger.warning(
                "Discarded citations that did not support their claim",
                extra={"labels": [f"S{index}" for index in group.indexes]},
            )
        parts.append(sanitized[cursor : group.start])
        parts.append(replacement)
        cursor = group.end
    parts.append(sanitized[cursor:])

    cleaned_answer = _clean_spacing("".join(parts))
    citations = [_citation(hits[index - 1]) for index in supported_indexes]
    return CitationValidationResult(
        answer=cleaned_answer,
        citations=citations,
        had_citations=had_citations,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        require_citations=require_citations,
    )
