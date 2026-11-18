"""Deterministic OpenAI-compatible stub used by the Docker/Celery integration tests."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_DEFAULT_DIMENSIONS = 1536
_CHAT_ANSWER = (
    "The payment terms are net thirty days from the invoice date, and late payments "
    "accrue one percent interest per month [S1]."
)

app = FastAPI(title="DocuMind mock OpenAI")


def _embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _TOKEN_PATTERN.findall(text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        vector[int(digest[:8], 16) % dimensions] += 1.0
        for offset in (1, 2):
            neighbor_digest = hashlib.sha256(f"{token}:{offset}".encode()).hexdigest()
            vector[int(neighbor_digest[:8], 16) % dimensions] += 0.25
    if not any(vector):
        vector[0] = 1.0
    return vector


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def create_embeddings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    inputs = payload.get("input") or []
    if isinstance(inputs, str):
        inputs = [inputs]
    dimensions = int(payload.get("dimensions") or _DEFAULT_DIMENSIONS)
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": _embed(text, dimensions),
            }
            for index, text in enumerate(inputs)
        ],
        "model": payload.get("model", "mock-embedding-model"),
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
    }


async def _stream_answer(payload: dict[str, Any]) -> AsyncIterator[str]:
    base = {
        "id": f"chatcmpl-mock-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": payload.get("model", "mock-chat-model"),
    }
    for index, token in enumerate(_CHAT_ANSWER.split(" ")):
        content = token if index == 0 else f" {token}"
        chunk = {
            **base,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    finish = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(finish)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def create_chat_completion(request: Request):
    payload = await request.json()
    if payload.get("stream"):
        return StreamingResponse(_stream_answer(payload), media_type="text/event-stream")
    return {
        "id": f"chatcmpl-mock-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.get("model", "mock-chat-model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _CHAT_ANSWER},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
