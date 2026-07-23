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

    async def send_updates(self, client: httpx.AsyncClient, updates: list[VersionUpdate]) -> None:
        if not updates:
            return

        subject = _subject(updates)
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


def _subject(updates: list[VersionUpdate]) -> str:
    if len(updates) == 1:
        update = updates[0]
        return f"{update.product_name} has a new tag: {update.new_tag}"
    return f"{len(updates)} repositories have new tags"


def _body(updates: list[VersionUpdate]) -> str:
    lines = ["Detected GitHub tag updates:", ""]
    for update in updates:
        previous = update.old_tag or "(first seen)"
        lines.extend(
            [
                f"- {update.product_name}",
                f"  Repository: https://github.com/{update.repository}",
                f"  Previous: {previous}",
                f"  Current:  {update.new_tag}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()

