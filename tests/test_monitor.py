from __future__ import annotations

import asyncio
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
from repo_version_monitor.providers import Tag


class _FakeProvider:
    """Returns a fixed tag list, whatever repository is asked for."""

    def __init__(self, *names: str) -> None:
        self.tags = [Tag(name=name, commit_sha="sha") for name in names]

    async def fetch_tags(self, client, repository: str) -> list[Tag]:
        return self.tags


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


def test_check_picks_the_highest_prefixed_tag(tmp_path: Path) -> None:
    # End to end: only "release-" tags count, and 1.10.0 beats 1.9.0 because
    # the prefix is dropped before the version numbers are compared.
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    product = ProductConfig(name="tool", repository="acme/tool", prefix="release-")
    monitor = _monitor(tmp_path, [product])
    monitor.providers["github"] = _FakeProvider(
        "v99.0.0", "release-1.9.0", "release-1.10.0", "release-1.11.0-rc1"
    )

    updates = asyncio.run(monitor.check_once())

    stored = monitor.store.get_product("acme/tool")
    assert stored is not None and stored.latest_tag == "release-1.10.0"
    # notify_on_first_seen is false, so the first check only records the tag.
    assert updates == []


def test_check_reports_an_update_between_prefixed_tags(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    product = ProductConfig(name="tool", repository="acme/tool", prefix="release-")
    monitor = _monitor(tmp_path, [product])
    monitor.store.initialize()
    monitor.store.upsert_product("tool", "acme/tool", "release-1.9.0")
    monitor.providers["github"] = _FakeProvider("release-1.9.0", "release-1.10.0")

    updates = asyncio.run(monitor.check_once())

    assert [(u.old_tag, u.new_tag) for u in updates] == [("release-1.9.0", "release-1.10.0")]


def test_product_key_ignores_the_suffix_but_the_label_shows_it() -> None:
    product = ProductConfig(
        name="internal",
        repository="team/app",
        branch="v13",
        provider="gitlab",
        external_url="https://git.mycorp.com",
        suffix="-ee",
    )

    # Changing the suffix must not change the key, or the history would reset.
    assert product_key(product) == ("gitlab", "https://git.mycorp.com", "team/app", "v13")
    assert product_key(product) == product_key(
        ProductConfig(**{**product.__dict__, "suffix": None})
    )
    assert product_label(product) == "gitlab:git.mycorp.com/team/app@v13 (-ee)"
    # The prefix is not part of the key either; the label shows the tag shape
    # it matches, with "*" standing in for the version numbers.
    with_prefix = ProductConfig(**{**product.__dict__, "prefix": "release-"})
    assert product_key(with_prefix) == product_key(product)
    assert product_label(with_prefix) == "gitlab:git.mycorp.com/team/app@v13 (release-*-ee)"
    assert (
        product_label(ProductConfig("tool", "acme/tool", prefix="release-"))
        == "acme/tool (release-*)"
    )
    assert product_key(ProductConfig("httpx", "encode/httpx")) == (
        "github",
        "",
        "encode/httpx",
        None,
    )
