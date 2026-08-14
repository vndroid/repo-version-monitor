from __future__ import annotations

from pathlib import Path

from repo_version_monitor.config import (
    AppConfig,
    DatabaseConfig,
    GitHubConfig,
    GitLabConfig,
    MailgunConfig,
    MonitorConfig,
    ProductConfig,
)
from repo_version_monitor.monitor import VersionMonitor, product_key, product_label


def _monitor(tmp_path: Path, products: list[ProductConfig]) -> VersionMonitor:
    config = AppConfig(
        database=DatabaseConfig(path=tmp_path / "versions.sqlite3"),
        github=GitHubConfig(token=None, per_page=10),
        gitlab=GitLabConfig(token="glpat-public"),
        mailgun=MailgunConfig(
            enabled=False,
            domain="",
            api_key="",
            from_email="",
            to_emails=[],
            api_url="https://api.mailgun.net/v3",
        ),
        monitor=MonitorConfig(interval_seconds=3600, notify_on_first_seen=False),
        products=products,
        source_path=tmp_path / "config.toml",
    )
    return VersionMonitor(config)


def test_public_products_share_the_configured_clients(tmp_path: Path) -> None:
    github = ProductConfig(name="httpx", repository="encode/httpx")
    gitlab = ProductConfig(name="runner", repository="a/b", provider="gitlab")
    monitor = _monitor(tmp_path, [github, gitlab])

    assert monitor.client_for(github) is monitor.github
    assert monitor.client_for(gitlab) is monitor.gitlab
    # The public GitLab client keeps using the [gitlab] token.
    assert monitor.gitlab.external_url == "https://gitlab.com"
    assert monitor.gitlab.token == "glpat-public"


def test_self_managed_products_get_their_own_client(tmp_path: Path) -> None:
    first = ProductConfig(
        name="a",
        repository="team/a",
        provider="gitlab",
        external_url="https://jihulab.com",
        token="glpat-jihu",
    )
    second = ProductConfig(
        name="b", repository="team/b", provider="gitlab", external_url="https://jihulab.com",
        token="glpat-jihu",
    )
    other = ProductConfig(
        name="c", repository="team/c", provider="gitlab", external_url="https://git.mycorp.com"
    )
    monitor = _monitor(tmp_path, [first, second, other])

    client = monitor.client_for(first)

    assert client.external_url == "https://jihulab.com"
    assert client.token == "glpat-jihu"
    # Same instance and token: one client is reused.
    assert monitor.client_for(second) is client
    assert monitor.client_for(other) is not client
    # A self-managed instance never inherits the gitlab.com token.
    assert monitor.client_for(other).token is None


def test_product_key_and_label_include_the_instance() -> None:
    product = ProductConfig(
        name="internal",
        repository="team/app",
        branch="v13",
        provider="gitlab",
        external_url="https://git.mycorp.com",
    )

    assert product_key(product) == ("gitlab", "https://git.mycorp.com", "team/app", "v13")
    assert product_label(product) == "gitlab:git.mycorp.com/team/app@v13"
    assert product_key(ProductConfig("httpx", "encode/httpx")) == (
        "github",
        "",
        "encode/httpx",
        None,
    )
