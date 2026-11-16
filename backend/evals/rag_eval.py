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


def _matching_rank(
    results: list[dict[str, Any]],
    expected_document: str,
    expected_page: int | None,
    top_k: int,
) -> int | None:
    for rank, result in enumerate(results[:top_k], start=1):
        if result.get("document_name") != expected_document:
            continue
        if expected_page is not None and result.get("page_number") != expected_page:
            continue
        return rank
    return None


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

    rank = None
    if expected_document:
        rank = _matching_rank(search_results, expected_document, expected_page, top_k)

    cited_documents = {citation.get("document_name") for citation in citations}
    citation_target_match = (
        (not citations)
        if should_refuse
        else bool(citations) and cited_documents == {expected_document}
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
        "refused": refused,
        "refusal_success": refusal_success,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, float | int]:
    answerable = [result for result in results if result["refusal_success"] is None]
    unanswerable = [result for result in results if result["refusal_success"] is not None]
    ranks = [result["retrieval_rank"] for result in answerable]
    return {
        "questions": len(results),
        "answerable_questions": len(answerable),
        "unanswerable_questions": len(unanswerable),
        "retrieval_hit_rate": mean(
            1.0 if result["retrieval_hit"] else 0.0 for result in answerable
        )
        if answerable
        else 0.0,
        "mrr": mean(1.0 / rank if rank else 0.0 for rank in ranks) if ranks else 0.0,
        "answer_keyword_recall": mean(
            result["keyword_recall"] for result in answerable
        )
        if answerable
        else 0.0,
        "citation_target_rate": mean(
            1.0 if result["citation_target_match"] else 0.0 for result in answerable
        )
        if answerable
        else 0.0,
        "citation_validity_rate": mean(
            1.0 if result["citation_valid"] else 0.0 for result in results
        ),
        "unanswerable_refusal_rate": mean(
            1.0 if result["refusal_success"] else 0.0 for result in unanswerable
        )
        if unanswerable
        else 1.0,
    }


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
                raise RuntimeError(
                    f"Document {document_id} failed: {payload.get('error_message')}"
                )
            time.sleep(1)
        raise TimeoutError(f"Document {document_id} did not finish in time")

    def search(
        self, query: str, document_ids: list[str], top_k: int
    ) -> list[dict[str, Any]]:
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
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


def print_report(
    dataset: dict[str, Any], results: list[dict[str, Any]], top_k: int
) -> None:
    summary = summarize_results(results)
    print(f"Dataset: {dataset['name']}")
    print(
        f"Questions: {summary['questions']} "
        f"({summary['answerable_questions']} answerable)"
    )
    print()
    print(
        f"{'id':<22} {'hit':<4} {'rank':<5} {'keywords':<9} "
        f"{'citations':<10} {'refused'}"
    )
    for result in results:
        print(
            f"{result['id']:<22} "
            f"{'yes' if result['retrieval_hit'] else 'no':<4} "
            f"{result['retrieval_rank'] or '-':<5} "
            f"{result['keyword_recall']:.0%}     "
            f"{result['citation_count']:<10} "
            f"{result['refused']}"
        )
    print()
    print(f"Retrieval hit@{top_k}:       {summary['retrieval_hit_rate']:.1%}")
    print(f"MRR:                       {summary['mrr']:.3f}")
    print(f"Answer keyword recall:     {summary['answer_keyword_recall']:.1%}")
    print(f"Citation target match:     {summary['citation_target_rate']:.1%}")
    print(f"Citation structural valid: {summary['citation_validity_rate']:.1%}")
    print(f"Unanswerable refusal:      {summary['unanswerable_refusal_rate']:.1%}")


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
    parser.add_argument("--output", type=Path, help="Write full results as JSON")
    args = parser.parse_args(argv)

    try:
        dataset, results = run_evaluation(
            args.base_url,
            args.dataset,
            args.top_k,
            args.timeout,
            args.processing_timeout,
        )
    except Exception as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2

    print_report(dataset, results, args.top_k)
    summary = summarize_results(results)

    if args.output:
        args.output.write_text(
            json.dumps(
                {"dataset": dataset["name"], "summary": summary, "results": results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    failures = []
    if summary["retrieval_hit_rate"] < args.min_hit_rate:
        failures.append(
            f"retrieval hit rate {summary['retrieval_hit_rate']:.1%} < "
            f"{args.min_hit_rate:.1%}"
        )
    if summary["mrr"] < args.min_mrr:
        failures.append(f"MRR {summary['mrr']:.3f} < {args.min_mrr:.3f}")
    if summary["citation_validity_rate"] < args.min_citation_validity:
        failures.append(
            f"citation validity {summary['citation_validity_rate']:.1%} < "
            f"{args.min_citation_validity:.1%}"
        )
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

