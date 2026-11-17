from __future__ import annotations

import copy
import json
from pathlib import Path

from evals.rag_eval import (
    evaluate_question,
    evaluate_retrieval_question,
    keyword_recall,
    summarize_results,
    summarize_retrieval_results,
    validate_dataset,
)

DATASET_PATH = Path(__file__).resolve().parents[1] / "evals" / "dataset.json"

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
    assert evaluated["citation_precision"] == 1.0
    assert evaluated["citation_recall"] == 1.0
    assert evaluated["unsupported_citation_count"] == 0
    assert evaluated["refusal_success"] is None
    assert evaluated["refusal_correct"] is True


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
    assert evaluated["refusal_correct"] is True
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
    assert unsupported["refusal_correct"] is False
    assert unsupported["citation_target_match"] is False
    assert unsupported["citation_valid"] is False
    assert unsupported["citation_precision"] == 0.0
    assert unsupported["citation_recall"] == 0.0
    assert unsupported["unsupported_citation_count"] == 1


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
        "citation_count": 1,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "unsupported_citation_count": 0,
        "refusal_correct": True,
    }
    answerable_second = {
        "refusal_success": None,
        "retrieval_hit": True,
        "retrieval_rank": 2,
        "keyword_recall": 0.5,
        "citation_target_match": True,
        "citation_valid": True,
        "citation_count": 1,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "unsupported_citation_count": 0,
        "refusal_correct": True,
    }
    unanswerable = {
        "refusal_success": True,
        "retrieval_hit": False,
        "retrieval_rank": None,
        "keyword_recall": 1.0,
        "citation_target_match": True,
        "citation_valid": True,
        "citation_count": 0,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "unsupported_citation_count": 0,
        "refusal_correct": True,
    }
    summary = summarize_results([answerable, answerable_second, unanswerable])

    assert summary["retrieval_hit_rate"] == 1.0
    assert summary["mrr"] == 0.75
    assert summary["answer_keyword_recall"] == 0.75
    assert summary["citation_precision"] == 1.0
    assert summary["citation_recall"] == 1.0
    assert summary["unsupported_citation_rate"] == 0.0
    assert summary["unanswerable_refusal_rate"] == 1.0
    assert summary["refusal_accuracy"] == 1.0


def test_missed_retrieval_is_counted_in_mrr_denominator() -> None:
    hit = {
        "refusal_success": None,
        "retrieval_hit": True,
        "retrieval_rank": 1,
        "keyword_recall": 1.0,
        "citation_target_match": True,
        "citation_valid": True,
        "citation_count": 1,
        "citation_precision": 1.0,
        "citation_recall": 1.0,
        "unsupported_citation_count": 0,
        "refusal_correct": True,
    }
    miss = {
        "refusal_success": None,
        "retrieval_hit": False,
        "retrieval_rank": None,
        "keyword_recall": 0.0,
        "citation_target_match": False,
        "citation_valid": True,
        "citation_count": 0,
        "citation_precision": 0.0,
        "citation_recall": 0.0,
        "unsupported_citation_count": 0,
        "refusal_correct": True,
    }
    summary = summarize_results([hit, miss])

    assert summary["retrieval_hit_rate"] == 0.5
    assert summary["mrr"] == 0.5
    assert summary["citation_precision"] == 0.5
    assert summary["refusal_accuracy"] == 1.0


def test_citation_precision_and_recall_track_page_matches() -> None:
    correct = evaluate_question(
        QUESTION,
        [],
        {"answer": "It renews annually.", "citations": [make_citation(page_number=2)]},
        UPLOADED_IDS,
        top_k=5,
    )
    assert correct["citation_precision"] == 1.0
    assert correct["citation_recall"] == 1.0
    assert correct["unsupported_citation_count"] == 0
    assert correct["citation_target_match"] is True

    wrong = evaluate_question(
        QUESTION,
        [],
        {"answer": "It renews annually.", "citations": [make_citation(page_number=1)]},
        UPLOADED_IDS,
        top_k=5,
    )
    assert wrong["citation_precision"] == 0.0
    assert wrong["citation_recall"] == 0.0
    assert wrong["unsupported_citation_count"] == 1
    assert wrong["citation_target_match"] is False

    mixed = evaluate_question(
        QUESTION,
        [],
        {
            "answer": "It renews annually.",
            "citations": [make_citation(page_number=2), make_citation(page_number=1)],
        },
        UPLOADED_IDS,
        top_k=5,
    )
    assert mixed["citation_precision"] == 0.5
    assert mixed["citation_recall"] == 1.0
    assert mixed["unsupported_citation_count"] == 1
    assert mixed["citation_target_match"] is False


def test_checked_in_benchmark_dataset_is_verified() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    errors = validate_dataset(dataset)

    assert errors == []
    assert len(dataset["documents"]) == 10
    assert len(dataset["questions"]) == 110
    assert sum(not question.get("should_refuse") for question in dataset["questions"]) == 100
    assert sum(bool(question.get("should_refuse")) for question in dataset["questions"]) == 10
    assert validate_dataset(dataset, minimum_questions=111)


def test_dataset_validation_rejects_unverified_answer_keywords() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    broken = copy.deepcopy(dataset)
    question = next(
        question for question in broken["questions"] if not question.get("should_refuse")
    )
    question["answer_keywords"] = [["phrase that is not in the source"]]

    errors = validate_dataset(broken)

    assert any(question["id"] in error and "not found" in error for error in errors)


def test_retrieval_only_summary_counts_misses() -> None:
    hit = evaluate_retrieval_question(
        QUESTION,
        [{"document_name": "msa-agreement.pdf", "page_number": 2}],
        top_k=5,
    )
    miss = evaluate_retrieval_question(
        QUESTION,
        [{"document_name": "other.pdf", "page_number": 1}],
        top_k=5,
    )
    unanswerable = evaluate_retrieval_question(
        {
            "id": "unanswerable",
            "question": "What is the ticker?",
            "expected_document": None,
            "expected_page": None,
            "should_refuse": True,
        },
        [],
        top_k=5,
    )

    assert hit["retrieval_rank"] == 1
    assert miss["retrieval_rank"] is None
    summary = summarize_retrieval_results([hit, miss, unanswerable])
    assert summary["questions"] == 3
    assert summary["answerable_questions"] == 2
    assert summary["retrieval_hit_rate"] == 0.5
    assert summary["mrr"] == 0.5
