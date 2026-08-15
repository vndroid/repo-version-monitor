from __future__ import annotations

import httpx

from repo_version_monitor.message import (
    TEST_BODY,
    TEST_SUBJECT,
    VersionUpdate,
    body_for,
    subject_for,
)

__all__ = ["MailgunClient", "VersionUpdate"]


class MailgunClient:
    """Delivery through the Mailgun HTTP API."""

    def __init__(
        self,
        domain: str,
        api_key: str,
        from_email: str,
        to_emails: list[str],
        api_url: str = "https://api.mailgun.net/v3",
    ) -> None:
        self.domain = domain
        self.api_key = api_key
        self.from_email = from_email
        self.to_emails = to_emails
        # API endpoint; EU accounts use https://api.eu.mailgun.net/v3.
        self.api_url = api_url.rstrip("/")

    async def send_updates(
        self,
        client: httpx.AsyncClient,
        updates: list[VersionUpdate],
        subject_prefix: str = "",
    ) -> None:
        """Send one email covering all updates (all-in-one, regardless of count)."""
        if not updates:
            return

        response = await self._post(
            client, f"{subject_prefix}{subject_for(updates)}", body_for(updates)
        )
        response.raise_for_status()

    async def send_test(self, client: httpx.AsyncClient) -> httpx.Response:
        """Send a test email; returns the raw response so callers can report the result."""
        return await self._post(client, TEST_SUBJECT, TEST_BODY)

    async def _post(
        self, client: httpx.AsyncClient, subject: str, text: str
    ) -> httpx.Response:
        return await client.post(
            f"{self.api_url}/{self.domain}/messages",
            auth=("api", self.api_key),
            data={
                "from": self.from_email,
                "to": self.to_emails,
                "subject": subject,
                "text": text,
            },
        )
