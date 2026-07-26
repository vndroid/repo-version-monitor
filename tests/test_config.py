from pathlib import Path

import pytest

from repo_version_monitor.config import (
    add_product_to_config,
    format_config,
    load_config,
    load_products,
)


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


def test_add_product_with_branch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "postgres", "postgres/postgres", branch="v13")
    products = load_products(config_path)

    assert products[0].branch == "v13"
    assert products[0].key == "postgres/postgres@v13"


def test_same_repo_allowed_with_different_branch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "pg", "postgres/postgres")
    add_product_to_config(config_path, "pg13", "postgres/postgres", branch="v13")
    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(config_path, "pg13-dup", "postgres/postgres", branch="v13")

    assert len(load_products(config_path)) == 2


@pytest.mark.parametrize("name", ["监控工具", "bad name", "name!", ""])
def test_add_product_rejects_invalid_name(tmp_path: Path, name: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid product name"):
        add_product_to_config(config_path, name, "encode/httpx")


def test_add_product_accepts_dotted_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "zlib.net", "madler/zlib")

    assert load_products(config_path)[0].name == "zlib.net"


def test_load_products_rejects_invalid_name_in_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "版本监控"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid product name"):
        load_products(config_path)


def test_format_creates_config_from_template(tmp_path: Path) -> None:
    template = tmp_path / "config.example.toml"
    template.write_text('[[products]]\nname = "httpx"\nrepository = "encode/httpx"\n', encoding="utf-8")
    config_path = tmp_path / "config.toml"

    assert format_config(config_path) == "created"
    assert config_path.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")


def test_format_missing_config_and_template_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        format_config(tmp_path / "config.toml")


def test_format_normalizes_products(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"

[[products]]
name = "httpx"
repository = "encode/httpx"
[[products]]
name = "pg13"
repository = "postgres/postgres"
branch = "v13"
""".lstrip(),
        encoding="utf-8",
    )

    assert format_config(config_path) == "formatted"

    expected = """
[database]
path = "versions.sqlite3"

[[products]]
name = "httpx"
repository = "encode/httpx"
branch = ""

[[products]]
name = "pg13"
repository = "postgres/postgres"
branch = "v13"
""".lstrip()
    assert config_path.read_text(encoding="utf-8") == expected

    # Empty branch reads back as no branch.
    products = load_products(config_path)
    assert products[0].branch is None
    assert products[1].branch == "v13"


def test_format_rejects_invalid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[[products]]\nname = "bad name"\nrepository = "a/b"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid product name"):
        format_config(config_path)


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

