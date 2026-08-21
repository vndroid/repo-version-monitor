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


def test_github_products_write_an_empty_provider_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "httpx", "encode/httpx")

    assert 'provider = ""' in config_path.read_text(encoding="utf-8")
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


def test_add_self_managed_product_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(
        config_path,
        "example",
        "example/project",
        provider="gitlab",
        # No scheme: https is assumed.
        external_url="jihulab.com",
        token="glpat-inline",
    )

    assert config_path.read_text(encoding="utf-8").strip() == (
        """
[[products]]
name = "example"
provider = "gitlab"
external_url = "https://jihulab.com"
token = "glpat-inline"
repository = "example/project"
branch = ""
prefix = ""
suffix = ""
""".strip()
    )
    product = load_products(config_path)[0]
    assert (product.external_url, product.token) == ("https://jihulab.com", "glpat-inline")


def test_same_repository_allowed_across_instances(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "public", "acme/tool", provider="gitlab")
    add_product_to_config(
        config_path, "internal", "acme/tool", provider="gitlab", external_url="jihulab.com"
    )
    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(
            config_path, "dup", "acme/tool", provider="gitlab", external_url="https://jihulab.com/"
        )

    assert len(load_products(config_path)) == 2


@pytest.mark.parametrize("external_url", ["not a url", "ssh://git.mycorp.com", "http://"])
def test_add_product_rejects_invalid_external_url(tmp_path: Path, external_url: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError):
        add_product_to_config(
            config_path, "x", "a/b", provider="gitlab", external_url=external_url
        )


def test_external_url_rejected_for_github_products(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="not supported for github"):
        add_product_to_config(config_path, "x", "a/b", external_url="github.mycorp.com")


def test_product_token_requires_external_url(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="token requires external_url"):
        add_product_to_config(config_path, "x", "a/b", provider="gitlab", token="glpat-x")


def test_add_product_with_suffix_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(
        config_path, "gitlab", "gitlab-org/gitlab", provider="gitlab", suffix="-ee"
    )

    assert 'suffix = "-ee"' in config_path.read_text(encoding="utf-8")
    assert load_products(config_path)[0].suffix == "-ee"


def test_suffix_does_not_make_a_second_product(tmp_path: Path) -> None:
    # The suffix selects which tags to read, it is not part of the identity:
    # the same repository can only be tracked once per branch and instance.
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "ee", "acme/tool", provider="gitlab", suffix="-ee")
    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(config_path, "ce", "acme/tool", provider="gitlab", suffix="-ce")

    assert len(load_products(config_path)) == 1


def test_add_product_with_several_suffixes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(
        config_path, "gitlab", "gitlab-org/gitlab", provider="gitlab", suffix="-ee|-ce"
    )

    assert load_products(config_path)[0].suffix == "-ee|-ce"


@pytest.mark.parametrize("suffix", ["-e e", "-ee!", "后缀", "-ee|", "|", "-ee||-ce"])
def test_add_product_rejects_invalid_suffix(tmp_path: Path, suffix: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid suffix"):
        add_product_to_config(config_path, "x", "a/b", suffix=suffix)


def test_empty_suffix_reads_back_as_none(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[[products]]\nname = "httpx"\nrepository = "encode/httpx"\nsuffix = ""\n',
        encoding="utf-8",
    )

    assert load_products(config_path)[0].suffix is None


def test_add_product_with_prefix_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "tool", "acme/tool", prefix="release-")

    assert 'prefix = "release-"' in config_path.read_text(encoding="utf-8")
    assert load_products(config_path)[0].prefix == "release-"


def test_add_product_with_prefix_and_suffix(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(
        config_path, "tool", "acme/tool", provider="gitlab", suffix="-ee", prefix="release-"
    )

    product = load_products(config_path)[0]
    assert (product.prefix, product.suffix) == ("release-", "-ee")


def test_prefix_does_not_make_a_second_product(tmp_path: Path) -> None:
    # Like the suffix, the prefix selects which tags to read: it is not part
    # of the identity, so the repository still counts as one product.
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "stable", "acme/tool", prefix="release-")
    with pytest.raises(ValueError, match="already configured"):
        add_product_to_config(config_path, "nightly", "acme/tool", prefix="nightly-")

    assert len(load_products(config_path)) == 1


def test_add_product_with_several_prefixes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "tool", "acme/tool", prefix="release-|rel-")

    assert load_products(config_path)[0].prefix == "release-|rel-"


@pytest.mark.parametrize("prefix", ["release -", "release!", "前缀", "release-|", "|", "a||b"])
def test_add_product_rejects_invalid_prefix(tmp_path: Path, prefix: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid prefix"):
        add_product_to_config(config_path, "x", "a/b", prefix=prefix)


def test_prefix_may_contain_a_slash(tmp_path: Path) -> None:
    # "release/1.2.3" is a common tag layout.
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    add_product_to_config(config_path, "x", "a/b", prefix="release/")

    assert load_products(config_path)[0].prefix == "release/"


def test_empty_prefix_reads_back_as_none(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[[products]]\nname = "httpx"\nrepository = "encode/httpx"\nprefix = ""\n',
        encoding="utf-8",
    )

    assert load_products(config_path)[0].prefix is None


def test_editing_the_branch_keeps_the_prefix(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    add_product_to_config(config_path, "tool", "acme/tool", prefix="release-", suffix="-ee")

    edit_product_branch(config_path, "tool", "v13")

    product = load_products(config_path)[0]
    assert (product.branch, product.prefix, product.suffix) == ("v13", "release-", "-ee")


def test_load_config_reads_gitlab_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
enabled = false

[gitlab]
token = "glpat-inline"

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
    assert config.products[0].provider == "gitlab"


@pytest.mark.parametrize("key", ["base_url", "external_url"])
def test_load_config_rejects_instance_url_in_gitlab_section(
    tmp_path: Path, monkeypatch, key: str
) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[mailgun]
enabled = false

[gitlab]
{key} = "https://gitlab.example.com"

[[products]]
name = "runner"
provider = "gitlab"
repository = "gitlab-org/gitlab-runner"
""".lstrip(),
        encoding="utf-8",
    )

    # The instance URL belongs to the product now, not to the section.
    with pytest.raises(ValueError, match="belongs to the product"):
        load_config(config_path)


def test_load_config_rejects_renamed_mailgun_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAILGUN_API_KEY", "key-xxx")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[mailgun]
enabled = false
base_url = "https://api.eu.mailgun.net/v3"

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="renamed to api_url"):
        load_config(config_path)

    # format must not add api_url next to the outdated key either.
    with pytest.raises(ValueError, match="renamed to api_url"):
        format_config(config_path)


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

    assert format_config(config_path).action == "formatted"

    assert config_path.read_text(encoding="utf-8").endswith(
        """
[[products]]
name = "runner"
provider = "gitlab"
external_url = ""
token = ""
repository = "gitlab-org/gitlab-runner"
branch = ""
prefix = ""
suffix = ""
""".lstrip()
    )
    assert load_products(config_path)[0].provider == "gitlab"


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

    result = format_config(config_path)

    assert result.action == "created"
    assert result.added_settings == []
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

    assert format_config(config_path).action == "formatted"

    assert config_path.read_text(encoding="utf-8").endswith(
        """
[[products]]
name = "httpx"
provider = ""
external_url = ""
token = ""
repository = "encode/httpx"
branch = ""
prefix = ""
suffix = ""

[[products]]
name = "pg13"
provider = ""
external_url = ""
token = ""
repository = "postgres/postgres"
branch = "v13"
prefix = ""
suffix = ""
""".lstrip()
    )

    # Empty branch reads back as no branch, empty provider as the default one.
    products = load_products(config_path)
    assert (products[0].branch, products[0].provider) == (None, "github")
    assert products[1].branch == "v13"


def test_format_adds_missing_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"

[mailgun]
enabled = false

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    result = format_config(config_path)

    assert "gitlab.token" in result.added_settings
    assert "database.path" not in result.added_settings
    text = config_path.read_text(encoding="utf-8")
    assert '[gitlab]\ntoken = ""' in text
    # Existing values are never overwritten.
    assert 'path = "versions.sqlite3"' in text
    # Empty settings read back as the built-in defaults.
    assert load_config(config_path).mailgun.api_url == "https://api.mailgun.net/v3"


def test_format_fills_keys_inside_existing_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[gitlab]
# Keep this comment.
token = "glpat-inline"

[monitor]
interval_seconds = 60

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    result = format_config(config_path)

    text = config_path.read_text(encoding="utf-8")
    assert "# Keep this comment." in text
    assert '[gitlab]\n# Keep this comment.\ntoken = "glpat-inline"\n' in text
    assert "[monitor]\ninterval_seconds = 60\nnotify_on_first_seen = false\n" in text
    assert "gitlab.token" not in result.added_settings
    assert "monitor.interval_seconds" not in result.added_settings


def test_format_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[[products]]\nname = "httpx"\nrepository = "encode/httpx"\n', encoding="utf-8"
    )

    format_config(config_path)
    first = config_path.read_text(encoding="utf-8")
    second_result = format_config(config_path)

    assert second_result.added_settings == []
    assert config_path.read_text(encoding="utf-8") == first


def test_empty_settings_fall_back_to_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = ""

[github]
token = ""
per_page = 10

[gitlab]
token = ""

[mailgun]
enabled = false
api_url = ""

[monitor]
interval_seconds = 3600
notify_on_first_seen = false

[[products]]
name = "httpx"
provider = ""
repository = "encode/httpx"
branch = ""
""".lstrip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.database.path == tmp_path / "versions.sqlite3"
    assert config.github.token is None
    assert config.mailgun.api_url == "https://api.mailgun.net/v3"
    assert config.products[0].provider == "github"
    assert config.products[0].branch is None


def _smtp_config(tmp_path: Path, section: str, mailgun: str = "enabled = false") -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[mailgun]
{mailgun}

{section}

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


def test_smtp_defaults_to_disabled(tmp_path: Path) -> None:
    config = load_config(_smtp_config(tmp_path, ""))

    assert config.smtp.enabled is False
    assert config.notifications_enabled is False


def test_load_smtp_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    config_path = _smtp_config(
        tmp_path,
        """
[smtp]
enabled = true
host = "smtp.example.com"
port = 465
encryption = "ssl"
username = "monitor@example.com"
password = "inline-secret"
from_email = "monitor@example.com"
to_emails = ["you@example.com"]
""".strip(),
    )

    smtp = load_config(config_path).smtp

    assert (smtp.enabled, smtp.host, smtp.port, smtp.encryption) == (
        True,
        "smtp.example.com",
        465,
        "ssl",
    )
    assert smtp.to_emails == ["you@example.com"]
    assert smtp.password_source == "config smtp.password"
    # The password is not exposed by repr, so it stays out of logs.
    assert "inline-secret" not in repr(smtp)
    assert load_config(config_path).notifications_enabled is True


def test_smtp_password_prefers_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SMTP_PASSWORD", "env-secret")
    config_path = _smtp_config(
        tmp_path,
        """
[smtp]
enabled = true
host = "smtp.example.com"
username = "monitor@example.com"
password = "inline-secret"
from_email = "monitor@example.com"
to_emails = ["you@example.com"]
""".strip(),
    )

    smtp = load_config(config_path).smtp

    assert smtp.password == "env-secret"
    assert smtp.password_source == "env SMTP_PASSWORD"


def test_mailgun_and_smtp_are_mutually_exclusive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAILGUN_API_KEY", "key-xxx")
    config_path = _smtp_config(
        tmp_path,
        """
[smtp]
enabled = true
host = "smtp.example.com"
from_email = "monitor@example.com"
to_emails = ["you@example.com"]
""".strip(),
        mailgun='enabled = true\ndomain = "mg.example.com"\nfrom_email = "a@b.com"\nto_emails = ["c@d.com"]',
    )

    with pytest.raises(ValueError, match="both true"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("[smtp]\nenabled = true", "smtp.host"),
        (
            '[smtp]\nenabled = true\nhost = "smtp.example.com"\nfrom_email = "a@b.com"',
            "smtp.to_emails is required",
        ),
        (
            '[smtp]\nenabled = true\nhost = "smtp.example.com"\nencryption = "tls"',
            "Invalid smtp.encryption",
        ),
        (
            '[smtp]\nenabled = true\nhost = "smtps://smtp.example.com"',
            "Invalid smtp.host",
        ),
        (
            # 0 means "use the default", like everywhere else; 70000 is just wrong.
            '[smtp]\nenabled = true\nhost = "smtp.example.com"\nport = 70000',
            "Invalid smtp.port",
        ),
        (
            '[smtp]\nenabled = true\nhost = "smtp.example.com"\nfrom_email = "a@b.com"'
            '\nto_emails = ["c@d.com"]\npassword = "x"',
            "smtp.username is empty",
        ),
    ],
)
def test_invalid_smtp_settings(tmp_path: Path, monkeypatch, section: str, message: str) -> None:
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    with pytest.raises(ValueError, match=message):
        load_config(_smtp_config(tmp_path, section))


def test_invalid_smtp_settings_ignored_while_disabled(tmp_path: Path) -> None:
    config = load_config(_smtp_config(tmp_path, '[smtp]\nenabled = false\nencryption = "tls"'))

    assert config.smtp.enabled is False


def _proxy_config(tmp_path: Path, section: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[mailgun]
enabled = false

{section}

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )
    return config_path


def test_proxy_defaults_to_disabled(tmp_path: Path) -> None:
    config = load_config(_proxy_config(tmp_path, ""))

    assert config.proxy.enabled is False
    assert config.proxy.type == "http"


def test_load_socks5_proxy(tmp_path: Path) -> None:
    config_path = _proxy_config(
        tmp_path,
        """
[proxy]
enabled = true
type = "socks5"
host = "127.0.0.1"
port = 1080
username = "user"
password = "secret"
""".strip(),
    )

    proxy = load_config(config_path).proxy

    assert (proxy.enabled, proxy.type, proxy.host, proxy.port) == (True, "socks5", "127.0.0.1", 1080)
    assert (proxy.username, proxy.password) == ("user", "secret")
    assert proxy.url == "socks5://127.0.0.1:1080"
    # The password is not exposed by repr, so it stays out of logs.
    assert "secret" not in repr(proxy)


def test_empty_proxy_values_fall_back_to_defaults(tmp_path: Path) -> None:
    config_path = _proxy_config(
        tmp_path,
        """
[proxy]
enabled = false
type = ""
host = ""
port = 0
username = ""
password = ""
""".strip(),
    )

    proxy = load_config(config_path).proxy

    assert (proxy.type, proxy.port) == ("http", 8080)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[proxy]\nenabled = true\ntype = "ftp"\nhost = "127.0.0.1"', "Invalid proxy.type"),
        ("[proxy]\nenabled = true", "proxy.host is required"),
        (
            '[proxy]\nenabled = true\nhost = "http://127.0.0.1"',
            "Invalid proxy.host",
        ),
        ('[proxy]\nenabled = true\nhost = "127.0.0.1"\nport = 70000', "Invalid proxy.port"),
        (
            '[proxy]\nenabled = true\nhost = "127.0.0.1"\npassword = "secret"',
            "proxy.username is empty",
        ),
    ],
)
def test_invalid_proxy_settings(tmp_path: Path, section: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_proxy_config(tmp_path, section))


def test_load_config_rejects_the_old_proxy_enable_spelling(tmp_path: Path) -> None:
    config_path = _proxy_config(tmp_path, '[proxy]\nenable = true\nhost = "127.0.0.1"')

    # Silently ignoring it would leave the proxy off without telling anyone.
    # The hint has to be valid TOML: "true", not Python's "True".
    with pytest.raises(ValueError, match="rename the key, e.g. enabled = true."):
        load_config(config_path)

    with pytest.raises(ValueError, match="renamed to enabled"):
        format_config(config_path)


def test_invalid_proxy_settings_ignored_while_disabled(tmp_path: Path) -> None:
    # Nothing is validated until the proxy is switched on.
    config = load_config(_proxy_config(tmp_path, '[proxy]\nenabled = false\ntype = "ftp"'))

    assert config.proxy.enabled is False


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

