"""Reusable RAG smoke evaluation against a running DocuMind API.

The harness uploads a small golden PDF set, runs retrieval and chat, and reports
retrieval hit rate, MRR, answer keyword recall, citation validity, and refusal
accuracy. Generation metrics depend on the configured chat model; the retrieval
and citation invariants are deterministic.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import textwrap
import time
import uuid
from pathlib import Path
from statistics import mean
from typing import Any

import httpx
from reportlab.pdfgen import canvas

REFUSAL_PHRASES = (
    "do not contain enough information",
    "do not have enough information",
    "not enough information",
    "insufficient information",
    "cannot answer",
)


def build_pdf(pages: list[str]) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    for text in pages:
        text_object = pdf.beginText(72, 720)
        text_object.setFont("Helvetica", 11)
        for line in textwrap.wrap(text, width=85):
            text_object.textLine(line)
        pdf.drawText(text_object)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def keyword_recall(answer: str, keyword_groups: list[list[str]]) -> float:
    if not keyword_groups:
        return 1.0
    normalized = answer.casefold()
    matched = 0
    for alternatives in keyword_groups:
        if any(alternative.casefold() in normalized for alternative in alternatives):
            matched += 1
    return matched / len(keyword_groups)


def is_refusal(answer: str) -> bool:
    normalized = answer.casefold()
    return any(phrase in normalized for phrase in REFUSAL_PHRASES)


def _expected_sources(question: dict[str, Any]) -> list[tuple[str, int | None]]:
    expected_document = question.get("expected_document")
    if not expected_document:
        return []
    return [(expected_document, question.get("expected_page"))]


def _source_matches(
    document_name: str | None,
    page_number: int | None,
    expected_sources: list[tuple[str, int | None]],
) -> bool:
    return any(
        document_name == expected_document
        and (expected_page is None or page_number == expected_page)
        for expected_document, expected_page in expected_sources
    )


def _matching_rank(
    results: list[dict[str, Any]],
    expected_sources: list[tuple[str, int | None]],
    top_k: int,
) -> int | None:
    for rank, result in enumerate(results[:top_k], start=1):
        if _source_matches(
            result.get("document_name"),
            result.get("page_number"),
            expected_sources,
        ):
            return rank
    return None


def _citation_metrics(
    citations: list[dict[str, Any]],
    expected_sources: list[tuple[str, int | None]],
) -> tuple[float, float, int]:
    if not citations:
        return (1.0, 1.0, 0) if not expected_sources else (0.0, 0.0, 0)
    supported = sum(
        1
        for citation in citations
        if _source_matches(
            citation.get("document_name"),
            citation.get("page_number"),
            expected_sources,
        )
    )
    covered = {
        source
        for source in expected_sources
        if any(
            _source_matches(
                citation.get("document_name"),
                citation.get("page_number"),
                [source],
            )
            for citation in citations
        )
    }
    precision = supported / len(citations)
    recall = len(covered) / len(expected_sources) if expected_sources else 0.0
    return precision, recall, len(citations) - supported


def evaluate_retrieval_question(
    question: dict[str, Any],
    search_results: list[dict[str, Any]],
    top_k: int,
) -> dict[str, Any]:
    expected_sources = _expected_sources(question)
    rank = _matching_rank(search_results, expected_sources, top_k) if expected_sources else None
    return {
        "id": question["id"],
        "question": question["question"],
        "expected_document": question.get("expected_document"),
        "expected_page": question.get("expected_page"),
        "should_refuse": bool(question.get("should_refuse")),
        "retrieval_hit": rank is not None,
        "retrieval_rank": rank,
    }


def evaluate_question(
    question: dict[str, Any],
    search_results: list[dict[str, Any]],
    chat_payload: dict[str, Any],
    uploaded_document_ids: set[str],
    top_k: int,
) -> dict[str, Any]:
    expected_document = question.get("expected_document")
    expected_page = question.get("expected_page")
    should_refuse = bool(question.get("should_refuse"))
    answer = str(chat_payload.get("answer") or "")
    citations = list(chat_payload.get("citations") or [])
    expected_sources = _expected_sources(question)

    rank = _matching_rank(search_results, expected_sources, top_k) if expected_sources else None

    citation_precision, citation_recall, unsupported_citations = _citation_metrics(
        citations, expected_sources
    )
    citation_target_match = (
        (not citations) if should_refuse else citation_precision == 1.0 and citation_recall == 1.0
    )
    citation_valid = all(
        citation.get("document_id") in uploaded_document_ids
        and bool(citation.get("chunk_id"))
        and isinstance(citation.get("page_number"), int)
        and citation["page_number"] > 0
        for citation in citations
    )
    refused = is_refusal(answer)
    refusal_success = refused and not citations if should_refuse else None

    return {
        "id": question["id"],
        "question": question["question"],
        "expected_document": expected_document,
        "expected_page": expected_page,
        "answer": answer,
        "retrieval_hit": rank is not None,
        "retrieval_rank": rank,
        "keyword_recall": keyword_recall(answer, question.get("answer_keywords") or []),
        "citation_target_match": citation_target_match,
        "citation_valid": citation_valid,
        "citation_count": len(citations),
        "citation_precision": citation_precision,
        "citation_recall": citation_recall,
        "unsupported_citation_count": unsupported_citations,
        "refused": refused,
        "refusal_success": refusal_success,
        "refusal_correct": (refused and not citations) if should_refuse else not refused,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, float | int]:
    answerable = [result for result in results if result["refusal_success"] is None]
    unanswerable = [result for result in results if result["refusal_success"] is not None]
    ranks = [result["retrieval_rank"] for result in answerable]
    citations = sum(result["citation_count"] for result in results)
    unsupported_citations = sum(result["unsupported_citation_count"] for result in results)
    return {
        "questions": len(results),
        "answerable_questions": len(answerable),
        "unanswerable_questions": len(unanswerable),
        "retrieval_hit_rate": mean(1.0 if result["retrieval_hit"] else 0.0 for result in answerable)
        if answerable
        else 0.0,
        "mrr": mean(1.0 / rank if rank else 0.0 for rank in ranks) if ranks else 0.0,
        "answer_keyword_recall": mean(result["keyword_recall"] for result in answerable)
        if answerable
        else 0.0,
        "citation_target_rate": mean(
            1.0 if result["citation_target_match"] else 0.0 for result in answerable
        )
        if answerable
        else 0.0,
        "citation_precision": mean(result["citation_precision"] for result in results),
        "citation_recall": mean(result["citation_recall"] for result in answerable)
        if answerable
        else 0.0,
        "unsupported_citation_rate": unsupported_citations / citations if citations else 0.0,
        "citation_validity_rate": mean(
            1.0 if result["citation_valid"] else 0.0 for result in results
        ),
        "unanswerable_refusal_rate": mean(
            1.0 if result["refusal_success"] else 0.0 for result in unanswerable
        )
        if unanswerable
        else 1.0,
        "refusal_accuracy": mean(1.0 if result["refusal_correct"] else 0.0 for result in results),
    }


def summarize_retrieval_results(results: list[dict[str, Any]]) -> dict[str, float | int]:
    answerable = [result for result in results if not result["should_refuse"]]
    ranks = [result["retrieval_rank"] for result in answerable]
    return {
        "questions": len(results),
        "answerable_questions": len(answerable),
        "unanswerable_questions": len(results) - len(answerable),
        "retrieval_hit_rate": mean(1.0 if result["retrieval_hit"] else 0.0 for result in answerable)
        if answerable
        else 0.0,
        "mrr": mean(1.0 / rank if rank else 0.0 for rank in ranks) if ranks else 0.0,
    }


def validate_dataset(dataset: dict[str, Any], minimum_questions: int = 100) -> list[str]:
    errors: list[str] = []
    documents = dataset.get("documents")
    questions = dataset.get("questions")
    if not isinstance(documents, list) or not documents:
        return ["Dataset must contain a non-empty documents list"]
    if not isinstance(questions, list):
        return ["Dataset must contain a questions list"]

    pages_by_document: dict[str, list[str]] = {}
    for index, document in enumerate(documents):
        filename = document.get("filename") if isinstance(document, dict) else None
        pages = document.get("pages") if isinstance(document, dict) else None
        if not isinstance(filename, str) or not filename:
            errors.append(f"documents[{index}] has no filename")
            continue
        if filename in pages_by_document:
            errors.append(f"duplicate document filename: {filename}")
            continue
        if (
            not isinstance(pages, list)
            or not pages
            or not all(isinstance(page, str) and page.strip() for page in pages)
        ):
            errors.append(f"{filename} must contain non-empty text pages")
            continue
        pages_by_document[filename] = pages

    if len(questions) < minimum_questions:
        errors.append(
            f"dataset has {len(questions)} questions; at least {minimum_questions} are required"
        )

    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    unanswerable = 0
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            errors.append(f"questions[{index}] is not an object")
            continue
        question_id = question.get("id")
        text = question.get("question")
        if not isinstance(question_id, str) or not question_id:
            errors.append(f"questions[{index}] has no id")
            continue
        if question_id in seen_ids:
            errors.append(f"duplicate question id: {question_id}")
        seen_ids.add(question_id)
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{question_id} has no question text")
            continue
        normalized_text = text.strip().casefold()
        if normalized_text in seen_questions:
            errors.append(f"duplicate question text: {question_id}")
        seen_questions.add(normalized_text)

        should_refuse = bool(question.get("should_refuse"))
        expected_document = question.get("expected_document")
        expected_page = question.get("expected_page")
        keywords = question.get("answer_keywords")
        reference_answer = question.get("reference_answer")

        if should_refuse:
            unanswerable += 1
            if expected_document is not None or expected_page is not None:
                errors.append(f"{question_id} is unanswerable but declares an expected source")
            if keywords:
                errors.append(f"{question_id} is unanswerable but has answer keywords")
            continue

        if not isinstance(reference_answer, str) or not reference_answer.strip():
            errors.append(f"{question_id} has no reference answer")
        if expected_document not in pages_by_document:
            errors.append(f"{question_id} references an unknown document: {expected_document}")
            continue
        pages = pages_by_document[expected_document]
        if not isinstance(expected_page, int) or not 1 <= expected_page <= len(pages):
            errors.append(f"{question_id} has an invalid expected page: {expected_page}")
            continue
        if not isinstance(keywords, list) or not keywords:
            errors.append(f"{question_id} has no answer keywords")
            continue
        source_text = pages[expected_page - 1].casefold()
        reference_text = str(reference_answer or "").casefold()
        for group_index, alternatives in enumerate(keywords):
            if (
                not isinstance(alternatives, list)
                or not alternatives
                or not all(
                    isinstance(alternative, str) and alternative.strip()
                    for alternative in alternatives
                )
            ):
                errors.append(
                    f"{question_id} answer_keywords[{group_index}] must be non-empty strings"
                )
                continue
            if not any(alternative.casefold() in source_text for alternative in alternatives):
                errors.append(
                    f"{question_id} answer_keywords[{group_index}] not found on "
                    f"{expected_document} page {expected_page}"
                )
            if not any(alternative.casefold() in reference_text for alternative in alternatives):
                errors.append(
                    f"{question_id} answer_keywords[{group_index}] "
                    "not found in its reference answer"
                )

    if unanswerable == 0:
        errors.append("dataset must contain at least one unanswerable question")
    return errors


class RagEvalClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=True)

    def __enter__(self) -> RagEvalClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def register(self) -> None:
        email = f"rag-eval-{uuid.uuid4().hex[:12]}@example.com"
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePass123",
                "full_name": "RAG Evaluation",
            },
        )
        response.raise_for_status()

    def upload(self, filename: str, data: bytes) -> str:
        response = self.client.post(
            "/api/v1/documents/upload",
            files={"file": (filename, data, "application/pdf")},
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def wait_until_completed(self, document_id: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/documents/{document_id}/status")
            response.raise_for_status()
            payload = response.json()
            if payload["status"] == "completed":
                return
            if payload["status"] == "failed":
                raise RuntimeError(f"Document {document_id} failed: {payload.get('error_message')}")
            time.sleep(1)
        raise TimeoutError(f"Document {document_id} did not finish in time")

    def search(self, query: str, document_ids: list[str], top_k: int) -> list[dict[str, Any]]:
        response = self.client.post(
            "/api/v1/search",
            json={
                "query": query,
                "document_ids": document_ids,
                "top_k": top_k,
            },
        )
        response.raise_for_status()
        return list(response.json()["results"])

    def chat(self, question: str, document_ids: list[str]) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/chat",
            json={"question": question, "document_ids": document_ids},
        )
        response.raise_for_status()
        return dict(response.json())


def run_evaluation(
    base_url: str,
    dataset_path: Path,
    top_k: int,
    timeout: float,
    processing_timeout: float,
    *,
    retrieval_only: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset_errors = validate_dataset(dataset)
    if dataset_errors:
        raise ValueError("Invalid dataset: " + "; ".join(dataset_errors[:5]))
    with RagEvalClient(base_url, timeout) as client:
        client.register()
        document_ids: dict[str, str] = {}
        for document in dataset["documents"]:
            document_id = client.upload(document["filename"], build_pdf(document["pages"]))
            document_ids[document["filename"]] = document_id
        for document_id in document_ids.values():
            client.wait_until_completed(document_id, processing_timeout)

        ordered_ids = list(document_ids.values())
        uploaded_ids = set(ordered_ids)
        results = []
        for question in dataset["questions"]:
            search_results = client.search(question["question"], ordered_ids, top_k)
            if retrieval_only:
                results.append(evaluate_retrieval_question(question, search_results, top_k))
                continue
            chat_payload = client.chat(question["question"], ordered_ids)
            results.append(
                evaluate_question(
                    question,
                    search_results,
                    chat_payload,
                    uploaded_ids,
                    top_k,
                )
            )
    return dataset, results


def print_retrieval_report(
    dataset: dict[str, Any], results: list[dict[str, Any]], top_k: int
) -> None:
    summary = summarize_retrieval_results(results)
    print(f"Dataset: {dataset['name']} (retrieval-only)")
    print(f"Questions: {summary['questions']} ({summary['answerable_questions']} answerable)")
    print()
    print(f"{'id':<28} {'hit':<4} {'rank'}")
    for result in results:
        if result["should_refuse"]:
            continue
        print(
            f"{result['id']:<28} "
            f"{'yes' if result['retrieval_hit'] else 'no':<4} "
            f"{result['retrieval_rank'] or '-'}"
        )
    print()
    print(f"Retrieval hit@{top_k}:       {summary['retrieval_hit_rate']:.1%}")
    print(f"MRR:                       {summary['mrr']:.3f}")


def print_report(dataset: dict[str, Any], results: list[dict[str, Any]], top_k: int) -> None:
    summary = summarize_results(results)
    print(f"Dataset: {dataset['name']}")
    print(f"Questions: {summary['questions']} ({summary['answerable_questions']} answerable)")
    print()
    print(
        f"{'id':<28} {'hit':<4} {'rank':<5} {'keywords':<9} {'cite P/R':<10} "
        f"{'unsupported':<11} {'refused'}"
    )
    for result in results:
        print(
            f"{result['id']:<28} "
            f"{'yes' if result['retrieval_hit'] else 'no':<4} "
            f"{result['retrieval_rank'] or '-':<5} "
            f"{result['keyword_recall']:.0%}     "
            f"{result['citation_precision']:.0%}/{result['citation_recall']:.0%}    "
            f"{result['unsupported_citation_count']:<11} "
            f"{result['refused']}"
        )
    print()
    print(f"Retrieval hit@{top_k}:       {summary['retrieval_hit_rate']:.1%}")
    print(f"MRR:                       {summary['mrr']:.3f}")
    print(f"Answer keyword recall:     {summary['answer_keyword_recall']:.1%}")
    print(f"Citation target match:     {summary['citation_target_rate']:.1%}")
    print(f"Citation precision:        {summary['citation_precision']:.1%}")
    print(f"Citation recall:           {summary['citation_recall']:.1%}")
    print(f"Unsupported citation rate: {summary['unsupported_citation_rate']:.1%}")
    print(f"Citation structural valid: {summary['citation_validity_rate']:.1%}")
    print(f"Unanswerable refusal:      {summary['unanswerable_refusal_rate']:.1%}")
    print(f"Refusal accuracy:          {summary['refusal_accuracy']:.1%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DocuMind RAG smoke evaluation")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="DocuMind API base URL",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("dataset.json"),
        help="Evaluation dataset JSON",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--processing-timeout", type=float, default=180.0)
    parser.add_argument("--min-hit-rate", type=float, default=0.8)
    parser.add_argument("--min-mrr", type=float, default=0.5)
    parser.add_argument("--min-citation-validity", type=float, default=1.0)
    parser.add_argument("--min-citation-precision", type=float, default=0.9)
    parser.add_argument("--min-refusal-accuracy", type=float, default=0.9)
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip chat and report retrieval metrics only",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Verify the checked-in dataset against its source pages without an API",
    )
    parser.add_argument("--output", type=Path, help="Write full results as JSON")
    args = parser.parse_args(argv)

    if args.validate_only:
        dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
        errors = validate_dataset(dataset)
        if errors:
            for error in errors:
                print(f"INVALID: {error}", file=sys.stderr)
            return 2
        questions = dataset["questions"]
        answerable = sum(not question.get("should_refuse") for question in questions)
        print(f"Dataset: {dataset.get('name', 'unnamed')}")
        print(f"Documents: {len(dataset['documents'])}")
        print(
            f"Questions: {len(questions)} "
            f"({answerable} answerable, {len(questions) - answerable} unanswerable)"
        )
        print("Dataset verification: passed")
        return 0

    try:
        dataset, results = run_evaluation(
            args.base_url,
            args.dataset,
            args.top_k,
            args.timeout,
            args.processing_timeout,
            retrieval_only=args.retrieval_only,
        )
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2

    if args.retrieval_only:
        print_retrieval_report(dataset, results, args.top_k)
        summary = summarize_retrieval_results(results)
        mode = "retrieval"
    else:
        print_report(dataset, results, args.top_k)
        summary = summarize_results(results)
        mode = "full"

    if args.output:
        args.output.write_text(
            json.dumps(
                {
                    "dataset": dataset["name"],
                    "mode": mode,
                    "summary": summary,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    failures = []
    if summary["retrieval_hit_rate"] < args.min_hit_rate:
        failures.append(
            f"retrieval hit rate {summary['retrieval_hit_rate']:.1%} < {args.min_hit_rate:.1%}"
        )
    if summary["mrr"] < args.min_mrr:
        failures.append(f"MRR {summary['mrr']:.3f} < {args.min_mrr:.3f}")
    if not args.retrieval_only:
        if summary["citation_validity_rate"] < args.min_citation_validity:
            failures.append(
                f"citation validity {summary['citation_validity_rate']:.1%} < "
                f"{args.min_citation_validity:.1%}"
            )
        if summary["citation_precision"] < args.min_citation_precision:
            failures.append(
                f"citation precision {summary['citation_precision']:.1%} < "
                f"{args.min_citation_precision:.1%}"
            )
        if summary["refusal_accuracy"] < args.min_refusal_accuracy:
            failures.append(
                f"refusal accuracy {summary['refusal_accuracy']:.1%} < "
                f"{args.min_refusal_accuracy:.1%}"
            )
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
