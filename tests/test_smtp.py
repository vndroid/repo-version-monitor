from __future__ import annotations

import asyncio
import smtplib

import pytest

from repo_version_monitor.message import VersionUpdate
from repo_version_monitor.smtp import SmtpClient


class _FakeServer:
    """Stands in for smtplib.SMTP, recording what the client asks of it."""

    def __init__(self, host: str, port: int, timeout: float = 0, context=None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.messages: list = []

    def __enter__(self) -> "_FakeServer":
        return self

    def __exit__(self, *exc_info) -> None:
        pass

    def starttls(self, context=None) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


def _client(monkeypatch, **kwargs) -> tuple[SmtpClient, list[_FakeServer]]:
    servers: list[_FakeServer] = []

    def factory(*args, **factory_kwargs):
        server = _FakeServer(*args, **factory_kwargs)
        servers.append(server)
        return server

    monkeypatch.setattr(smtplib, "SMTP", factory)
    monkeypatch.setattr(smtplib, "SMTP_SSL", factory)
    settings = {
        "host": "smtp.example.com",
        "port": 587,
        "from_email": "monitor@example.com",
        "to_emails": ["you@example.com", "ops@example.com"],
        **kwargs,
    }
    return SmtpClient(**settings), servers


def test_starttls_login_and_message(monkeypatch) -> None:
    client, servers = _client(monkeypatch, username="user", password="secret")
    updates = [VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0")]

    asyncio.run(client.send_updates(None, updates))

    server = servers[0]
    assert (server.host, server.port) == ("smtp.example.com", 587)
    assert server.started_tls is True
    assert server.login_args == ("user", "secret")
    message = server.messages[0]
    assert message["Subject"] == "httpx has a new tag: 0.28.0"
    assert message["From"] == "monitor@example.com"
    assert message["To"] == "you@example.com, ops@example.com"
    assert "encode/httpx" in message.get_content()


def test_ssl_does_not_call_starttls(monkeypatch) -> None:
    client, servers = _client(monkeypatch, port=465, encryption="ssl")

    asyncio.run(client.send_test())

    assert servers[0].port == 465
    assert servers[0].started_tls is False
    assert servers[0].messages[0]["Subject"] == "repo-version-monitor test email"


def test_plain_connection_skips_tls_and_login(monkeypatch) -> None:
    client, servers = _client(monkeypatch, port=25, encryption="none")

    asyncio.run(client.send_test())

    assert servers[0].started_tls is False
    # No username configured: no login attempt, the usual internal relay setup.
    assert servers[0].login_args is None


def test_backlog_is_one_email_with_prefix(monkeypatch) -> None:
    client, servers = _client(monkeypatch)
    updates = [
        VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0"),
        VersionUpdate("uv", "astral-sh/uv", None, "0.8.0"),
    ]

    asyncio.run(client.send_updates(None, updates, subject_prefix="accumulation:"))

    assert len(servers) == 1 and len(servers[0].messages) == 1
    assert servers[0].messages[0]["Subject"] == "accumulation:2 repositories have new tags"


def test_nothing_is_sent_without_updates(monkeypatch) -> None:
    client, servers = _client(monkeypatch)

    asyncio.run(client.send_updates(None, []))

    assert servers == []


def test_subject_cannot_inject_headers(monkeypatch) -> None:
    client, servers = _client(monkeypatch)
    updates = [VersionUpdate("httpx", "encode/httpx", None, "v1.0\r\nBcc: x@evil.com")]

    asyncio.run(client.send_updates(None, updates))

    subject = servers[0].messages[0]["Subject"]
    assert "\r" not in subject and "\n" not in subject


def test_send_failures_propagate(monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise smtplib.SMTPConnectError(421, "nope")

    monkeypatch.setattr(smtplib, "SMTP", failing)
    client = SmtpClient("smtp.example.com", from_email="a@b.com", to_emails=["c@d.com"])

    with pytest.raises(smtplib.SMTPConnectError):
        asyncio.run(client.send_test())
