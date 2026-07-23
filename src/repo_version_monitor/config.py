from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tomllib

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ProductConfig:
    name: str
    repository: str


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None = field(repr=False)
    per_page: int


@dataclass(frozen=True)
class MailgunConfig:
    domain: str
    api_key: str = field(repr=False)
    from_email: str
    to_emails: list[str]
    base_url: str


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path


@dataclass(frozen=True)
class MonitorConfig:
    interval_seconds: int
    notify_on_first_seen: bool


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    github: GitHubConfig
    mailgun: MailgunConfig
    monitor: MonitorConfig
    products: list[ProductConfig]


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    github_raw = raw.get("github", {})
    mailgun_raw = raw.get("mailgun", {})
    database_raw = raw.get("database", {})
    monitor_raw = raw.get("monitor", {})

    db_path = Path(database_raw.get("path", "versions.sqlite3"))
    if not db_path.is_absolute():
        db_path = path.parent / db_path

    api_key_env = mailgun_raw.get("api_key_env", "MAILGUN_API_KEY")
    api_key = mailgun_raw.get("api_key") or os.getenv(api_key_env)
    if not api_key:
        raise ValueError(f"Mailgun API key is missing. Set {api_key_env} or mailgun.api_key.")

    token_env = github_raw.get("token_env", "GITHUB_TOKEN")
    token = github_raw.get("token") or os.getenv(token_env)

    products = []
    for item in raw.get("products", []):
        repository = item["repository"]
        if not _REPOSITORY_PATTERN.fullmatch(repository):
            raise ValueError(
                f"Invalid repository {repository!r}: expected 'owner/name' with only "
                "letters, digits, '-', '_' and '.'."
            )
        products.append(ProductConfig(name=item["name"], repository=repository))
    if not products:
        raise ValueError("At least one [[products]] entry is required.")

    return AppConfig(
        database=DatabaseConfig(path=db_path),
        github=GitHubConfig(
            token=token,
            per_page=min(max(int(github_raw.get("per_page", 10)), 1), 100),
        ),
        mailgun=MailgunConfig(
            domain=mailgun_raw["domain"],
            api_key=api_key,
            from_email=mailgun_raw["from_email"],
            to_emails=list(mailgun_raw["to_emails"]),
            base_url=mailgun_raw.get("base_url", "https://api.mailgun.net/v3").rstrip("/"),
        ),
        monitor=MonitorConfig(
            interval_seconds=int(monitor_raw.get("interval_seconds", 3600)),
            notify_on_first_seen=bool(monitor_raw.get("notify_on_first_seen", False)),
        ),
        products=products,
    )

