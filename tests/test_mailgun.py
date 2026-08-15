import asyncio

from repo_version_monitor.mailgun import MailgunClient, VersionUpdate


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _FakeResponse()


def test_resend_backlog_goes_in_single_email_with_prefix() -> None:
    client = _FakeClient()
    mailgun = MailgunClient("mg.example.com", "key", "a@b.com", ["c@d.com"])
    updates = [
        VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0"),
        VersionUpdate("FastAPI", "fastapi/fastapi", "0.115.0", "0.116.0"),
        VersionUpdate("uv", "astral-sh/uv", None, "0.8.0"),
    ]

    asyncio.run(mailgun.send_updates(client, updates, subject_prefix="accumulation:"))

    assert len(client.calls) == 1  # all-in-one: exactly one email
    data = client.calls[0][1]["data"]
    assert data["subject"] == "accumulation:3 repositories have new tags"
    assert "encode/httpx" in data["text"]
    assert "fastapi/fastapi" in data["text"]
    assert "astral-sh/uv" in data["text"]


def test_test_email_goes_to_the_configured_recipients() -> None:
    client = _FakeClient()
    mailgun = MailgunClient("mg.example.com", "key", "a@b.com", ["c@d.com"])

    asyncio.run(mailgun.send_test(client))

    url, kwargs = client.calls[0]
    assert url == "https://api.mailgun.net/v3/mg.example.com/messages"
    assert kwargs["data"]["to"] == ["c@d.com"]
    assert kwargs["data"]["subject"] == "repo-version-monitor test email"
