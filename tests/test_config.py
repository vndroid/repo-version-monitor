from pathlib import Path

import pytest

from repo_version_monitor.config import add_product_to_config, load_config, load_products


def test_add_product_to_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"
""".lstrip(),
        encoding="utf-8",
    )

    add_product_to_config(config_path, "httpx", "encode/httpx")
    products = load_products(config_path)

    assert len(products) == 1
    assert products[0].name == "httpx"
    assert products[0].repository == "encode/httpx"


def test_add_product_rejects_duplicates(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(config_path, "HTTPX", "encode/httpx")


def test_mailgun_disabled_requires_no_mailgun_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAILGUN_API_KEY", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
enabled = false

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.mailgun.enabled is False
    assert config.mailgun.api_key == ""
    assert config.mailgun.to_emails == []


def test_mailgun_enabled_by_default_and_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAILGUN_API_KEY", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
domain = "mg.example.com"
from_email = "monitor@mg.example.com"
to_emails = ["you@example.com"]

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Mailgun API key is missing"):
        load_config(config_path)

