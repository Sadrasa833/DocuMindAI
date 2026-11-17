from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import ChatProvider, EmbeddingProvider
from app.ai.openai_provider import OpenAIChatProvider, OpenAIEmbeddingProvider
from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.users import UserRepository
from app.services.account import AccountService
from app.services.auth import AuthService
from app.services.conversations import ConversationService
from app.services.dispatch import CeleryTaskDispatcher, TaskDispatcher
from app.services.documents import DocumentService
from app.services.email import EmailSender, create_email_sender
from app.services.embeddings import EmbeddingService
from app.services.rag import RAGService
from app.services.scanning import ClamAVScanner, MalwareScanner
from app.services.search import SearchService
from app.services.storage import StorageBackend, create_storage_backend
from app.services.summarization import SummarizationService

bearer_scheme = HTTPBearer(auto_error=False, description="Optional JWT access token")
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def settings_dependency() -> Settings:
    return get_settings()


def get_auth_service(session: DbSession, settings: SettingsDep) -> AuthService:
    return AuthService(session, settings)


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    return create_email_sender(get_settings())


@lru_cache(maxsize=1)
def get_storage_backend() -> StorageBackend:
    return create_storage_backend(get_settings())


def get_account_service(
    session: DbSession,
    settings: SettingsDep,
    email_sender: Annotated[EmailSender, Depends(get_email_sender)],
    storage: Annotated[StorageBackend, Depends(get_storage_backend)],
) -> AccountService:
    return AccountService(
        session,
        settings=settings,
        email_sender=email_sender,
        storage=storage,
    )


@lru_cache(maxsize=1)
def get_malware_scanner() -> MalwareScanner | None:
    settings = get_settings()
    if not settings.malware_scanning_enabled:
        return None
    return ClamAVScanner(
        host=settings.clamav_host,
        port=settings.clamav_port,
        timeout_seconds=settings.clamav_timeout_seconds,
    )


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    return OpenAIEmbeddingProvider(get_settings())


@lru_cache(maxsize=1)
def get_chat_provider() -> ChatProvider:
    return OpenAIChatProvider(get_settings())


@lru_cache(maxsize=1)
def get_task_dispatcher() -> TaskDispatcher:
    return CeleryTaskDispatcher()


def get_embedding_service(
    provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    settings: SettingsDep,
) -> EmbeddingService:
    return EmbeddingService(
        provider,
        dimensions=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )


def get_document_service(
    session: DbSession,
    settings: SettingsDep,
    storage: Annotated[StorageBackend, Depends(get_storage_backend)],
    dispatcher: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    malware_scanner: Annotated[MalwareScanner | None, Depends(get_malware_scanner)],
) -> DocumentService:
    return DocumentService(
        session,
        settings=settings,
        storage=storage,
        dispatcher=dispatcher,
        malware_scanner=malware_scanner,
    )


def get_search_service(
    session: DbSession,
    settings: SettingsDep,
    embedding_service: Annotated[EmbeddingService, Depends(get_embedding_service)],
) -> SearchService:
    return SearchService(session, embedding_service=embedding_service, settings=settings)


def get_rag_service(
    session: DbSession,
    settings: SettingsDep,
    search_service: Annotated[SearchService, Depends(get_search_service)],
    chat_provider: Annotated[ChatProvider, Depends(get_chat_provider)],
) -> RAGService:
    return RAGService(
        session,
        search_service=search_service,
        chat_provider=chat_provider,
        settings=settings,
    )


def get_conversation_service(session: DbSession) -> ConversationService:
    return ConversationService(session)


def get_summarization_service(
    session: DbSession,
    settings: SettingsDep,
    chat_provider: Annotated[ChatProvider, Depends(get_chat_provider)],
) -> SummarizationService:
    return SummarizationService(session, settings=settings, chat_provider=chat_provider)


async def get_current_user(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    token = (
        credentials.credentials if credentials else request.cookies.get(settings.access_cookie_name)
    )
    if not token:
        raise UnauthorizedError("Authentication is required")
    try:
        payload = decode_token(token, "access", settings)
        user_id = uuid.UUID(str(payload["sub"]))
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise UnauthorizedError("Your session is invalid or has expired") from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("Your session is invalid or has expired")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def request_metadata(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("User-Agent")
    ip_address = request.client.host if request.client else None
    return user_agent, ip_address
