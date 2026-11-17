from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, text: str) -> None: ...


class ConsoleEmailSender:
    """Development backend that logs messages instead of sending them."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, *, to: str, subject: str, text: str) -> None:
        logger.info(
            "Development email message",
            extra={
                "email_to": to,
                "email_subject": subject,
                "email_from": self.settings.email_from,
                "email_body": text,
            },
        )


class SMTPEmailSender:
    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host:
            raise ValueError("SMTP_HOST is required when EMAIL_BACKEND=smtp")
        self.settings = settings

    async def send(self, *, to: str, subject: str, text: str) -> None:
        await asyncio.to_thread(self._send_sync, to, subject, text)

    def _send_sync(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)

        with smtplib.SMTP(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=self.settings.smtp_timeout_seconds,
        ) as client:
            client.ehlo()
            if self.settings.smtp_starttls:
                client.starttls()
                client.ehlo()
            if self.settings.smtp_username and self.settings.smtp_password_value:
                client.login(
                    self.settings.smtp_username,
                    self.settings.smtp_password_value,
                )
            client.send_message(message)


def create_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        return SMTPEmailSender(settings)
    return ConsoleEmailSender(settings)
