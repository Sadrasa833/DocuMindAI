from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.api.deps import get_email_sender
from app.core.config import Settings
from app.main import app
from app.services.account import AccountService
from app.services.email import ConsoleEmailSender, SMTPEmailSender, create_email_sender
from tests.conftest import settings as test_settings

RAW_TOKEN = "raw-single-use-token-9f4c2b7a"


class FailingEmailSender:
    async def send(self, *, to: str, subject: str, text: str) -> None:
        raise RuntimeError(f"transport rejected message containing {text}")


async def test_console_sender_withholds_email_body_from_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        secret_key="test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        openai_api_key="sk-test-not-a-real-key",
    )
    caplog.set_level(logging.INFO, logger="app.services.email")
    body = (
        "Confirm your DocuMind AI account by opening this link:\n\n"
        f"http://localhost:3000/verify-email?token={RAW_TOKEN}\n"
    )

    await ConsoleEmailSender(settings).send(
        to="ada@example.com",
        subject="Verify your email address",
        text=body,
    )

    assert RAW_TOKEN not in caplog.text
    assert "token=" not in caplog.text
    assert "localhost:3000/verify-email" not in caplog.text
    assert "Confirm your DocuMind AI account" not in caplog.text

    record = next(record for record in caplog.records if record.name == "app.services.email")
    assert record.email_to == "ada@example.com"
    assert record.email_subject == "Verify your email address"
    assert record.email_body_chars == len(body)
    assert not hasattr(record, "email_body")


async def test_account_emails_do_not_log_tokens_or_urls(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        app.dependency_overrides,
        get_email_sender,
        lambda: ConsoleEmailSender(test_settings),
    )
    caplog.set_level(logging.INFO, logger="app.services.email")

    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ada@example.com",
            "password": "SecurePass123",
            "full_name": "Ada Lovelace",
        },
    )
    assert registered.status_code == 201, registered.text

    requested = await client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "ada@example.com"},
    )
    assert requested.status_code == 202, requested.text

    assert "token=" not in caplog.text
    assert "verify-email" not in caplog.text
    assert "reset-password" not in caplog.text
    assert "Confirm your email address" not in caplog.text
    assert "Use this link" not in caplog.text
    recipients = [record.email_to for record in caplog.records if hasattr(record, "email_to")]
    assert recipients == ["ada@example.com", "ada@example.com"]


async def test_email_transport_errors_do_not_echo_message_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = AccountService(
        AsyncMock(),
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        email_sender=FailingEmailSender(),  # type: ignore[arg-type]
        storage=AsyncMock(),
    )
    caplog.set_level(logging.WARNING, logger="app.services.account")

    body = f"Reset link: http://localhost:3000/reset-password?token={RAW_TOKEN}"
    await service._send_best_effort(
        to="ada@example.com",
        subject="Reset your password",
        text=body,
        purpose=SimpleNamespace(value="password_reset"),  # type: ignore[arg-type]
    )

    assert RAW_TOKEN not in caplog.text
    assert "transport rejected message" not in caplog.text
    record = next(record for record in caplog.records if record.name == "app.services.account")
    assert record.error_type == "RuntimeError"
    assert record.email_to == "ada@example.com"


def test_smtp_sender_still_builds_and_sends_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert (host, port) == ("smtp.example.com", 2525)
            assert timeout == 5.0

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def send_message(self, message: object) -> None:
            sent.append(message)

    settings = Settings(
        _env_file=None,
        environment="test",
        secret_key="test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        openai_api_key="sk-test-not-a-real-key",
        email_backend="smtp",
        smtp_host="smtp.example.com",
        smtp_port=2525,
        smtp_starttls=False,
        smtp_timeout_seconds=5,
    )
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    sender = SMTPEmailSender(settings)
    sender._send_sync("ada@example.com", "Verify", f"token={RAW_TOKEN}")

    assert len(sent) == 1
    message = sent[0]
    assert message["To"] == "ada@example.com"
    assert message["From"] == settings.email_from
    assert message["Subject"] == "Verify"
    assert RAW_TOKEN in message.get_content()


def test_email_sender_factory_preserves_backends() -> None:
    base = {
        "_env_file": None,
        "environment": "test",
        "secret_key": "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz",
        "openai_api_key": "sk-test-not-a-real-key",
    }
    console = create_email_sender(Settings(**base))
    smtp = create_email_sender(Settings(**base, email_backend="smtp", smtp_host="smtp.example.com"))
    assert isinstance(console, ConsoleEmailSender)
    assert isinstance(smtp, SMTPEmailSender)
