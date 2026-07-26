from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class VersionUpdate:
    product_name: str
    repository: str
    old_tag: str | None
    new_tag: str


class MailgunClient:
    def __init__(
        self,
        domain: str,
        api_key: str,
        from_email: str,
        to_emails: list[str],
        base_url: str = "https://api.mailgun.net/v3",
    ) -> None:
        self.domain = domain
        self.api_key = api_key
        self.from_email = from_email
        self.to_emails = to_emails
        self.base_url = base_url.rstrip("/")

    async def send_updates(
        self,
        client: httpx.AsyncClient,
        updates: list[VersionUpdate],
        subject_prefix: str = "",
    ) -> None:
        """Send one email covering all updates (all-in-one, regardless of count)."""
        if not updates:
            return

        subject = f"{subject_prefix}{_subject(updates)}"
        text = _body(updates)
        response = await client.post(
            f"{self.base_url}/{self.domain}/messages",
            auth=("api", self.api_key),
            data={
                "from": self.from_email,
                "to": self.to_emails,
                "subject": subject,
                "text": text,
            },
        )
        response.raise_for_status()

    async def send_test(self, client: httpx.AsyncClient) -> httpx.Response:
        """Send a test email; returns the raw response so callers can report the result."""
        return await client.post(
            f"{self.base_url}/{self.domain}/messages",
            auth=("api", self.api_key),
            data={
                "from": self.from_email,
                "to": self.to_emails,
                "subject": "repo-version-monitor test email",
                "text": (
                    "This is a test email from repo-version-monitor.\n"
                    "If you received it, your Mailgun configuration works."
                ),
            },
        )


def _clean(value: str) -> str:
    """Strip control characters (CR/LF etc.) from remote-supplied strings.

    Tag names come from repositories we do not control; without this a tag
    like "v1.0\\r\\nBcc: x@evil.com" could inject email headers.
    """
    return "".join(c for c in value if c.isprintable())


def _subject(updates: list[VersionUpdate]) -> str:
    if len(updates) == 1:
        update = updates[0]
        return f"{_clean(update.product_name)} has a new tag: {_clean(update.new_tag)}"
    return f"{len(updates)} repositories have new tags"


def _body(updates: list[VersionUpdate]) -> str:
    lines = ["Detected GitHub tag updates:", ""]
    for update in updates:
        previous = _clean(update.old_tag) if update.old_tag else "(first seen)"
        lines.extend(
            [
                f"- {_clean(update.product_name)}",
                f"  Repository: https://github.com/{update.repository}",
                f"  Previous: {previous}",
                f"  Current:  {_clean(update.new_tag)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()

