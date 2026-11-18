from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.models.chunk import DocumentChunk
from app.repositories.chunks import SearchHit
from app.services.citation_validation import validate_citations


class FixedEmbeddingService:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.vector) for _ in texts]


def _hit(
    content: str,
    *,
    page_number: int = 1,
    filename: str = "agreement.pdf",
    embedding: list[float] | None = None,
) -> SearchHit:
    chunk = DocumentChunk(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        content=content,
        page_number=page_number,
        chunk_index=page_number - 1,
        embedding=embedding or [1.0, 0.0],
    )
    return SearchHit(chunk=chunk, document_name=filename, similarity=0.9)


async def test_supported_claim_keeps_verified_citation() -> None:
    hit = _hit("Acme Services Agreement. Payment terms are net thirty days from the invoice date.")

    result = await validate_citations("Payment is due in 30 days [S1].", [hit])

    assert not result.should_refuse
    assert result.answer == "Payment is due in 30 days [S1]."
    assert result.citations == [
        {
            "document_id": str(hit.chunk.document_id),
            "document_name": "agreement.pdf",
            "page_number": 1,
            "chunk_id": str(hit.chunk.id),
        }
    ]


async def test_valid_but_irrelevant_citation_is_removed_and_refused() -> None:
    hits = [
        _hit("Payment terms are net thirty days from the invoice date."),
        _hit("The launch date is March 15.", page_number=2, filename="roadmap.pdf"),
    ]

    result = await validate_citations("The agreement was signed in Paris [S1].", hits)

    assert result.should_refuse
    assert result.had_citations
    assert result.unsupported_claims == 1
    assert result.answer == "The agreement was signed in Paris."
    assert result.citations == []


async def test_fabricated_citation_is_rejected() -> None:
    result = await validate_citations(
        "Payment is due in 30 days [S99].",
        [_hit("Payment terms are net thirty days from the invoice date.")],
    )

    assert result.should_refuse
    assert result.answer == "Payment is due in 30 days."
    assert result.citations == []


async def test_multiple_claims_keep_their_own_sources() -> None:
    hits = [
        _hit("Payment terms are net thirty days from the invoice date.", page_number=1),
        _hit(
            "The agreement renews automatically for one year unless either party gives notice.",
            page_number=2,
        ),
    ]

    result = await validate_citations(
        "Payment is due in 30 days [S1]. The agreement renews automatically for one year [S2].",
        hits,
    )

    assert not result.should_refuse
    assert "[S1]" in result.answer
    assert "[S2]" in result.answer
    assert [citation["page_number"] for citation in result.citations] == [1, 2]


async def test_multiple_sources_can_support_one_claim() -> None:
    hits = [
        _hit("Project Atlas launch date is March 15.", page_number=1),
        _hit("Project Atlas primary risk is vendor delay.", page_number=2),
    ]

    result = await validate_citations(
        "Project Atlas launches March 15 and the primary risk is vendor delay [S1, S2].",
        hits,
    )

    assert not result.should_refuse
    assert [citation["page_number"] for citation in result.citations] == [1, 2]


async def test_unrelated_negation_does_not_reject_a_supported_claim() -> None:
    hit = _hit(
        "The agreement does not renew automatically. "
        "Payment terms are net thirty days from the invoice date."
    )

    result = await validate_citations("Payment is due in 30 days [S1].", [hit])

    assert not result.should_refuse
    assert result.answer == "Payment is due in 30 days [S1]."
    assert result.citations


async def test_negated_claim_requires_negated_evidence() -> None:
    positive = _hit("Payment is due in 30 days.")
    refused = await validate_citations("Payment is not due in 30 days [S1].", [positive])
    assert refused.should_refuse
    assert refused.citations == []

    negative = _hit("Payment is not due in 30 days.")
    supported = await validate_citations("Payment is not due in 30 days [S1].", [negative])
    assert not supported.should_refuse
    assert supported.citations


async def test_negated_number_must_appear_in_a_negated_clause() -> None:
    hit = _hit("Payment is due in 30 days, not 60 days.")

    result = await validate_citations("Payment is not due in 30 days [S1].", [hit])

    assert result.should_refuse
    assert result.citations == []


async def test_one_unsupported_claim_refuses_the_whole_answer() -> None:
    hits = [
        _hit("Payment terms are net thirty days from the invoice date.", page_number=1),
        _hit("The agreement renews automatically for one year.", page_number=2),
    ]

    result = await validate_citations(
        "Payment is due in 30 days [S1]. The governing law is Delaware [S2].",
        hits,
    )

    assert result.should_refuse
    assert result.supported_claims == 1
    assert result.unsupported_claims == 1
    assert result.answer == "Payment is due in 30 days [S1]. The governing law is Delaware."
    assert [citation["page_number"] for citation in result.citations] == [1]


async def test_unsupported_claims_refuse_and_do_not_return_citations() -> None:
    result = await validate_citations(
        "The governing law is Delaware [S1].",
        [_hit("Payment terms are net thirty days from the invoice date.")],
    )

    assert result.should_refuse
    assert result.answer == "The governing law is Delaware."
    assert result.citations == []


async def test_conflicting_numeric_evidence_rejects_the_wrong_source() -> None:
    hits = [
        _hit("The notice period is thirty days.", page_number=1),
        _hit("The notice period is sixty days.", page_number=2, filename="amendment.pdf"),
    ]

    result = await validate_citations("The notice period is 60 days [S1, S2].", hits)

    assert not result.should_refuse
    assert result.answer == "The notice period is 60 days [S2]."
    assert [citation["page_number"] for citation in result.citations] == [2]

    wrong_only = await validate_citations("The notice period is 60 days [S1].", hits)
    assert wrong_only.should_refuse


async def test_missing_evidence_refuses_when_no_citation_is_supported() -> None:
    result = await validate_citations(
        "The contract value is five million dollars [S1].",
        [_hit("Payment terms are net thirty days from the invoice date.")],
    )

    assert result.should_refuse
    assert result.unsupported_claims == 1
    assert result.citations == []


async def test_uncited_answer_refuses_when_citations_are_required() -> None:
    result = await validate_citations(
        "Payment is due in 30 days.",
        [_hit("Payment terms are net thirty days from the invoice date.")],
        require_citations=True,
    )

    assert result.should_refuse
    assert not result.had_citations


async def test_uncited_answer_can_pass_when_citations_are_not_required() -> None:
    result = await validate_citations(
        "Payment is due in 30 days.",
        [_hit("Payment terms are net thirty days from the invoice date.")],
        require_citations=False,
    )

    assert not result.should_refuse
    assert result.answer == "Payment is due in 30 days."
    assert result.citations == []


async def test_semantic_check_accepts_a_supported_paraphrase() -> None:
    hit = _hit(
        "Either party may end the agreement with advance written notice.",
        embedding=[1.0, 0.0],
    )
    embedding_service = FixedEmbeddingService([1.0, 0.0])

    without_semantics = await validate_citations(
        "Termination requires prior written approval [S1].",
        [hit],
    )
    assert without_semantics.should_refuse

    result = await validate_citations(
        "Termination requires prior written approval [S1].",
        [hit],
        embedding_service=embedding_service,
        semantic_threshold=0.8,
    )
    assert not result.should_refuse
    assert result.citations
    assert embedding_service.calls == [["Termination requires prior written approval"]]


async def test_persian_claim_support_is_normalized() -> None:
    hit = _hit("مبلغ پرداخت ظرف سی روز الزامی است.")

    result = await validate_citations("پرداخت ظرف 30 روز انجام می‌شود [S1].", [hit])

    assert not result.should_refuse
    assert result.citations
