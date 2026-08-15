"""Delivery through a plain SMTP server.

smtplib is synchronous, so sending runs in a worker thread. That keeps the
dependency list at httpx and costs nothing here: one mail per check at most.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
import logging
import smtplib
import ssl

from repo_version_monitor.message import (
    TEST_BODY,
    TEST_SUBJECT,
    VersionUpdate,
    body_for,
    clean,
    subject_for,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class SmtpClient:
    """Send notification mail over SMTP.

    encryption:
      - "starttls": connect in the clear, then upgrade (usually port 587)
      - "ssl": TLS from the first byte, SMTPS (usually port 465)
      - "none": no encryption at all, only sane for an internal relay
    """

    def __init__(
        self,
        host: str,
        port: int = 587,
        encryption: str = "starttls",
        username: str = "",
        password: str = "",
        from_email: str = "",
        to_emails: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.encryption = encryption
        self.username = username
        self.password = password
        self.from_email = from_email
        self.to_emails = list(to_emails or [])
        self.timeout = timeout

    async def send_updates(
        self,
        client: object,
        updates: list[VersionUpdate],
        subject_prefix: str = "",
    ) -> None:
        """Send one email covering all updates.

        ``client`` is the shared httpx client, unused here; the signature
        matches MailgunClient so the monitor treats both the same way.
        """
        if not updates:
            return

        await self.send(f"{subject_prefix}{subject_for(updates)}", body_for(updates))

    async def send_test(self, client: object = None) -> None:
        """Send a test email; raises on failure, like any other send."""
        await self.send(TEST_SUBJECT, TEST_BODY)

    async def send(self, subject: str, text: str) -> None:
        await asyncio.to_thread(self._send_sync, self._build_message(subject, text))

    def _build_message(self, subject: str, text: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = ", ".join(self.to_emails)
        # Subjects carry remote tag names; keep header injection impossible.
        message["Subject"] = clean(subject)
        message.set_content(text)
        return message

    def _send_sync(self, message: EmailMessage) -> None:
        with self._connect() as server:
            if self.encryption == "starttls":
                server.starttls(context=ssl.create_default_context())
            if self.username:
                server.login(self.username, self.password)
            server.send_message(message)

    def _connect(self) -> smtplib.SMTP:
        if self.encryption == "ssl":
            return smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=ssl.create_default_context()
            )
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout)
