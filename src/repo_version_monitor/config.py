from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ProductConfig:
    name: str
    repository: str


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None
    per_page: int


@dataclass(frozen=True)
class MailgunConfig:
    domain: str
    api_key: str
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

    products = [
        ProductConfig(name=item["name"], repository=item["repository"])
        for item in raw.get("products", [])
    ]
    if not products:
        raise ValueError("At least one [[products]] entry is required.")

    return AppConfig(
        database=DatabaseConfig(path=db_path),
        github=GitHubConfig(
            token=token,
            per_page=int(github_raw.get("per_page", 10)),
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

