from __future__ import annotations

from evals.rag_eval import evaluate_question, keyword_recall, summarize_results

QUESTION = {
    "id": "renewal-notice",
    "question": "When does the agreement renew?",
    "expected_document": "msa-agreement.pdf",
    "expected_page": 2,
    "answer_keywords": [["annually", "annual"], ["sixty days", "60 days"]],
}

UPLOADED_IDS = {"document-id"}


def make_citation(
    document_id: str = "document-id",
    document_name: str = "msa-agreement.pdf",
    page_number: int = 2,
) -> dict:
    return {
        "document_id": document_id,
        "chunk_id": "chunk-id",
        "document_name": document_name,
        "page_number": page_number,
    }


def test_evaluate_question_tracks_retrieval_and_citation_targets() -> None:
    search_results = [
        {"document_name": "other.pdf", "page_number": 1},
        {"document_name": "msa-agreement.pdf", "page_number": 2},
    ]
    evaluated = evaluate_question(
        QUESTION,
        search_results,
        {
            "answer": "It renews annually and requires sixty days notice.",
            "citations": [make_citation()],
        },
        UPLOADED_IDS,
        top_k=5,
    )

    assert evaluated["retrieval_hit"] is True
    assert evaluated["retrieval_rank"] == 2
    assert evaluated["keyword_recall"] == 1.0
    assert evaluated["citation_target_match"] is True
    assert evaluated["citation_valid"] is True
    assert evaluated["refusal_success"] is None


def test_evaluate_question_detects_bad_citations_and_refusals() -> None:
    question = {
        "id": "unanswerable",
        "question": "What is the ticker?",
        "expected_document": None,
        "expected_page": None,
        "answer_keywords": [],
        "should_refuse": True,
    }
    evaluated = evaluate_question(
        question,
        [],
        {
            "answer": "The documents do not contain enough information.",
            "citations": [],
        },
        UPLOADED_IDS,
        top_k=5,
    )
    assert evaluated["retrieval_hit"] is False
    assert evaluated["refusal_success"] is True
    assert evaluated["citation_valid"] is True

    unsupported = evaluate_question(
        question,
        [],
        {
            "answer": "Acme trades under ACME.",
            "citations": [make_citation(document_id="missing", document_name="unknown.pdf")],
        },
        UPLOADED_IDS,
        top_k=5,
    )
    assert unsupported["refusal_success"] is False
    assert unsupported["citation_target_match"] is False
    assert unsupported["citation_valid"] is False


def test_keyword_recall_matches_alternatives() -> None:
    keyword_groups = [["thirty days"], ["one percent", "1 percent"]]
    assert keyword_recall("Net thirty days, 1 percent interest.", keyword_groups) == 1.0
    assert keyword_recall("Net thirty days.", [["thirty days"], ["sixty days"]]) == 0.5


def test_summarize_results_calculates_retrieval_and_refusal_metrics() -> None:
    answerable = {
        "refusal_success": None,
        "retrieval_hit": True,
        "retrieval_rank": 1,
        "keyword_recall": 1.0,
        "citation_target_match": True,
        "citation_valid": True,
    }
    answerable_second = {
        "refusal_success": None,
        "retrieval_hit": True,
        "retrieval_rank": 2,
        "keyword_recall": 0.5,
        "citation_target_match": True,
        "citation_valid": True,
    }
    unanswerable = {
        "refusal_success": True,
        "retrieval_hit": False,
        "retrieval_rank": None,
        "keyword_recall": 1.0,
        "citation_target_match": True,
        "citation_valid": True,
    }
    summary = summarize_results([answerable, answerable_second, unanswerable])

    assert summary["retrieval_hit_rate"] == 1.0
    assert summary["mrr"] == 0.75
    assert summary["answer_keyword_recall"] == 0.75
    assert summary["unanswerable_refusal_rate"] == 1.0


def test_missed_retrieval_is_counted_in_mrr_denominator() -> None:
    hit = {
        "refusal_success": None,
        "retrieval_hit": True,
        "retrieval_rank": 1,
        "keyword_recall": 1.0,
        "citation_target_match": True,
        "citation_valid": True,
    }
    miss = {
        "refusal_success": None,
        "retrieval_hit": False,
        "retrieval_rank": None,
        "keyword_recall": 0.0,
        "citation_target_match": False,
        "citation_valid": True,
    }
    summary = summarize_results([hit, miss])

    assert summary["retrieval_hit_rate"] == 0.5
    assert summary["mrr"] == 0.5
