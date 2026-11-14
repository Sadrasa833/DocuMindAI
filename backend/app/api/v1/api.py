from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, chat, conversations, documents, search

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(documents.router)
api_router.include_router(search.router)
api_router.include_router(chat.router)
api_router.include_router(conversations.router)
