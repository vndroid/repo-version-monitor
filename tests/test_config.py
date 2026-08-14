from pathlib import Path

import pytest

from repo_version_monitor.config import (
    add_product_to_config,
    delete_product,
    edit_product_branch,
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
    assert products[0].branch is None
    # branch key is always written, empty when not specified
    assert 'branch = ""' in config_path.read_text(encoding="utf-8")


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


def test_add_gitlab_product_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "runner", "gitlab-org/gitlab-runner", provider="gitlab")
    products = load_products(config_path)

    assert products[0].provider == "gitlab"
    # provider is written explicitly for non-github entries only
    assert 'provider = "gitlab"' in config_path.read_text(encoding="utf-8")


def test_github_products_omit_provider_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "httpx", "encode/httpx")

    assert "provider" not in config_path.read_text(encoding="utf-8")
    assert load_products(config_path)[0].provider == "github"


def test_same_repository_allowed_across_providers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "hub", "acme/tool")
    add_product_to_config(config_path, "lab", "acme/tool", provider="gitlab")
    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(config_path, "lab2", "acme/tool", provider="gitlab")

    assert len(load_products(config_path)) == 2


def test_gitlab_allows_nested_subgroups_github_does_not(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "proj", "group/subgroup/project", provider="gitlab")
    with pytest.raises(ValueError, match="Invalid repository"):
        add_product_to_config(config_path, "bad", "group/subgroup/project")


def test_add_product_rejects_unknown_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid provider"):
        add_product_to_config(config_path, "x", "a/b", provider="bitbucket")


def test_load_config_reads_gitlab_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
enabled = false

[gitlab]
token = "glpat-inline"
base_url = "https://gitlab.example.com/"

[[products]]
name = "runner"
provider = "gitlab"
repository = "gitlab-org/gitlab-runner"
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.gitlab.token == "glpat-inline"
    assert config.gitlab.token_source == "config gitlab.token"
    assert config.gitlab.base_url == "https://gitlab.example.com"
    assert config.products[0].provider == "gitlab"


def test_load_config_prefers_gitlab_env_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-env")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
enabled = false

[gitlab]
token = "glpat-inline"

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.gitlab.token == "glpat-env"
    assert config.gitlab.token_source == "env GITLAB_TOKEN"


def test_format_preserves_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "runner"
provider = "gitlab"
repository = "gitlab-org/gitlab-runner"
""".lstrip(),
        encoding="utf-8",
    )

    assert format_config(config_path) == "formatted"

    expected = """
[[products]]
name = "runner"
provider = "gitlab"
repository = "gitlab-org/gitlab-runner"
branch = ""
""".lstrip()
    assert config_path.read_text(encoding="utf-8") == expected


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


def test_edit_product_branch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "grafana", "grafana/grafana")

    old_branch, product = edit_product_branch(config_path, "grafana", "13.0")

    assert old_branch is None
    assert product.branch == "13.0"
    assert load_products(config_path)[0].branch == "13.0"


def test_edit_product_branch_clear_with_empty_string(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "pg", "postgres/postgres", branch="v13")

    old_branch, product = edit_product_branch(config_path, "pg", "")

    assert old_branch == "v13"
    assert product.branch is None


def test_edit_unknown_name_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="No product named"):
        edit_product_branch(config_path, "nope", "v13")


def test_edit_ambiguous_name_requires_repository(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "dup", "a/one")
    add_product_to_config(config_path, "dup", "b/two")

    with pytest.raises(ValueError, match="Use --repository"):
        edit_product_branch(config_path, "dup", "v13")

    # Disambiguated by repository it succeeds.
    _, product = edit_product_branch(config_path, "dup", "v13", repository="b/two")
    assert product.repository == "b/two"


def test_edit_rejects_collision_with_existing_entry(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "pg", "postgres/postgres")
    add_product_to_config(config_path, "pg13", "postgres/postgres", branch="v13")

    with pytest.raises(ValueError, match="already configured"):
        edit_product_branch(config_path, "pg", "v13")


def test_delete_by_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "httpx", "encode/httpx")
    add_product_to_config(config_path, "grafana", "grafana/grafana")

    deleted = delete_product(config_path, name="httpx")

    assert deleted.repository == "encode/httpx"
    assert [p.name for p in load_products(config_path)] == ["grafana"]


def test_delete_ambiguous_name_errors(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "dup", "a/one")
    add_product_to_config(config_path, "dup", "b/two")

    with pytest.raises(ValueError, match="Multiple products match"):
        delete_product(config_path, name="dup")

    # Precise delete via repository succeeds.
    deleted = delete_product(config_path, name="dup", repository="b/two")
    assert deleted.repository == "b/two"
    assert len(load_products(config_path)) == 1


def test_delete_narrows_by_branch(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "pg", "postgres/postgres")
    add_product_to_config(config_path, "pg", "postgres/postgres", branch="v13")

    with pytest.raises(ValueError, match="Multiple products match"):
        delete_product(config_path, repository="postgres/postgres")

    deleted = delete_product(config_path, repository="postgres/postgres", branch="v13")
    assert deleted.branch == "v13"
    remaining = load_products(config_path)
    assert len(remaining) == 1 and remaining[0].branch is None


def test_delete_requires_selector_and_match(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Specify --name or --repository"):
        delete_product(config_path)
    with pytest.raises(ValueError, match="No matching product"):
        delete_product(config_path, name="nope")


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

