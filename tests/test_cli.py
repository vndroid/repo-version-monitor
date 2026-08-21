import logging
import sys
from pathlib import Path

import pytest

from repo_version_monitor.cli import _render_table, main
from repo_version_monitor.config import load_products
from repo_version_monitor.db import VersionStore


def _run_add(config_path: Path, monkeypatch, *add_args: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "add", *add_args],
    )
    main()


def _run_edit(config_path: Path, monkeypatch, *edit_args: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "edit", *edit_args],
    )
    main()


def test_edit_prefix(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "sqlite/sqlite")
    capsys.readouterr()

    _run_edit(config_path, monkeypatch, "sqlite", "--prefix", "version-")

    assert load_products(config_path)[0].prefix == "version-"
    assert "prefix (nil) -> (version-)" in capsys.readouterr().out


def test_edit_prefix_and_suffix_at_once(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool", "--prefix", "release-")
    capsys.readouterr()

    # The value starts with a dash, so argparse needs the '=' form.
    _run_edit(config_path, monkeypatch, "tool", "--prefix", "rel-", "--suffix=-ee")

    product = load_products(config_path)[0]
    assert (product.prefix, product.suffix) == ("rel-", "-ee")
    out = capsys.readouterr().out
    assert "prefix (release-) -> (rel-)" in out and "suffix (nil) -> (-ee)" in out


def test_edit_branch_leaves_the_prefix_alone(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool", "--prefix", "release-")
    capsys.readouterr()

    _run_edit(config_path, monkeypatch, "tool", "--branch", "v13")

    product = load_products(config_path)[0]
    assert (product.branch, product.prefix) == ("v13", "release-")
    # Only the field that was asked for is reported.
    out = capsys.readouterr().out
    assert "branch (nil) -> (v13)" in out and "prefix" not in out


def test_edit_clears_the_prefix_with_an_empty_string(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool", "--prefix", "release-")
    capsys.readouterr()

    _run_edit(config_path, monkeypatch, "tool", "--prefix", "")

    assert load_products(config_path)[0].prefix is None
    assert "prefix (release-) -> (nil)" in capsys.readouterr().out


def test_edit_without_any_field_exits(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool")

    with pytest.raises(SystemExit):
        _run_edit(config_path, monkeypatch, "tool")

    assert "--branch, --prefix, --suffix" in capsys.readouterr().err


def test_edit_with_an_invalid_prefix_exits(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool")

    with pytest.raises(SystemExit):
        _run_edit(config_path, monkeypatch, "tool", "--prefix", "release |")

    assert "Invalid prefix" in capsys.readouterr().err


def test_edit_reports_when_nothing_changed(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool", "--prefix", "release-")
    capsys.readouterr()

    _run_edit(config_path, monkeypatch, "tool", "--prefix", "release-")

    assert "already had those values" in capsys.readouterr().out


def test_add_infers_provider_from_url(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(config_path, monkeypatch, "https://gitlab.com/gitlab-org/gitlab/-/tags")

    product = load_products(config_path)[0]
    assert (product.name, product.provider, product.repository) == (
        "gitlab",
        "gitlab",
        "gitlab-org/gitlab",
    )
    out = capsys.readouterr().out
    assert "gitlab:gitlab-org/gitlab" in out
    assert "inferred from host gitlab.com" in out


def test_add_without_host_defaults_to_github(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(config_path, monkeypatch, "encode/httpx")

    product = load_products(config_path)[0]
    assert (product.provider, product.repository) == ("github", "encode/httpx")


def test_add_self_managed_instance_with_explicit_external_url(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(
        config_path,
        monkeypatch,
        "gitlab-org/gitlab-runner",
        "--provider",
        "gitlab",
        "--external-url",
        "jihulab.com",
    )

    product = load_products(config_path)[0]
    assert (product.provider, product.repository, product.external_url, product.token) == (
        "gitlab",
        "gitlab-org/gitlab-runner",
        "https://jihulab.com",
        None,
    )
    out = capsys.readouterr().out
    assert "gitlab:jihulab.com/gitlab-org/gitlab-runner" in out
    assert "anonymous access" in out


def test_add_self_managed_instance_from_the_repository_host(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(
        config_path,
        monkeypatch,
        "https://git.mycorp.com/group/sub/project",
        "--provider",
        "gitlab",
        "--token",
        "glpat-x",
    )

    product = load_products(config_path)[0]
    assert (product.external_url, product.repository, product.token) == (
        "https://git.mycorp.com",
        "group/sub/project",
        "glpat-x",
    )
    assert "(token)" in capsys.readouterr().out


def test_add_external_url_without_provider_exits(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_add(config_path, monkeypatch, "a/b", "--external-url", "jihulab.com")

    assert "requires --provider" in capsys.readouterr().err
    assert config_path.read_text(encoding="utf-8") == ""


def test_add_external_url_conflicting_with_host_exits(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_add(
            config_path,
            monkeypatch,
            "jihulab.com/a/b",
            "--provider",
            "gitlab",
            "--external-url",
            "git.mycorp.com",
        )

    assert "does not match --external-url" in capsys.readouterr().err


def test_list_shows_the_instance_host(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "public"
provider = "gitlab"
repository = "acme/tool"

[[products]]
name = "internal"
provider = "gitlab"
external_url = "https://jihulab.com"
repository = "acme/tool"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"]
    )

    main()

    lines = capsys.readouterr().out.splitlines()
    # Self-managed products are shown the way they are typed into `add`.
    assert lines[1].split()[:4] == ["01", "internal", "gitlab", "jihulab.com/acme/tool"]
    assert lines[2].split()[:4] == ["02", "public", "gitlab", "acme/tool"]


def test_delete_disambiguates_by_instance(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    _run_add(config_path, monkeypatch, "acme/tool", "--provider", "gitlab")
    _run_add(
        config_path, monkeypatch, "acme/tool", "--provider", "gitlab", "--external-url",
        "jihulab.com",
    )
    capsys.readouterr()

    def _delete(*delete_args: str) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["repo-version-monitor", "--config", str(config_path), "delete", *delete_args],
        )
        main()

    # Ambiguous without the instance.
    with pytest.raises(SystemExit):
        _delete("--repository", "acme/tool")
    assert "Multiple products match" in capsys.readouterr().err

    _delete("--repository", "acme/tool", "--external-url", "jihulab.com")

    remaining = load_products(config_path)
    assert len(remaining) == 1
    assert remaining[0].external_url is None


def test_add_with_suffix_and_list_column(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(
        config_path,
        monkeypatch,
        "https://gitlab.com/gitlab-org/gitlab",
        # The value starts with a dash, so argparse needs the '=' form.
        "--suffix=-ee",
    )

    assert load_products(config_path)[0].suffix == "-ee"
    assert "suffix -ee" in capsys.readouterr().out

    monkeypatch.setattr(
        sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"]
    )
    main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "ID", "NAME", "PROVIDER", "REPOSITORY", "BRANCH", "PREFIX", "SUFFIX", "LATEST"
    ]
    assert lines[1].split()[:7] == [
        "01", "gitlab", "gitlab", "gitlab-org/gitlab", "-", "/", "-ee"
    ]


def test_add_with_prefix_and_list_column(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(config_path, monkeypatch, "acme/tool", "--prefix", "release-")

    assert load_products(config_path)[0].prefix == "release-"
    assert "prefix release-" in capsys.readouterr().out

    monkeypatch.setattr(
        sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"]
    )
    main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split()[:7] == [
        "01", "tool", "github", "acme/tool", "-", "release-", "/"
    ]


def test_list_shows_slash_for_products_without_a_suffix(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[[products]]\nname = "httpx"\nrepository = "encode/httpx"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"]
    )

    main()

    # "-" would read like the beginning of a suffix such as "-ee".
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split()[:7] == ["01", "httpx", "github", "encode/httpx", "-", "/", "/"]


def test_list_shows_stored_latest_tags(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"

[[products]]
name = "gitlab"
provider = "gitlab"
repository = "gitlab-org/gitlab"
suffix = "-ee"

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )
    store = VersionStore(tmp_path / "versions.sqlite3")
    store.initialize()
    store.upsert_product("gitlab", "gitlab-org/gitlab", "19.2.3-ee", provider="gitlab")
    store.upsert_product("httpx", "encode/httpx", "0.28.1")
    monkeypatch.setattr(
        sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"]
    )

    main()

    # The stored rows must line up with the config entries, suffix included.
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split()[-1] == "19.2.3-ee"
    assert lines[2].split()[-1] == "0.28.1"


def test_add_unknown_host_without_provider_exits(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_add(config_path, monkeypatch, "git.mycorp.com/group/project")

    assert "Cannot tell which provider" in capsys.readouterr().err
    assert config_path.read_text(encoding="utf-8") == ""


def test_add_conflicting_provider_exits(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_add(config_path, monkeypatch, "gitlab.com/a/b", "--provider", "github")

    assert "conflicts with host" in capsys.readouterr().err


def _verbose_config(tmp_path: Path) -> Path:
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
    return config_path


@pytest.mark.parametrize("flags", [["--verbose"], ["-vv"], ["-v", "-v"]])
def test_verbose_logs_config_and_environment(
    tmp_path: Path, caplog, monkeypatch, flags: list[str]
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config_path = _verbose_config(tmp_path)
    # Flags work after the subcommand as well as before it.
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "check", "--name", "nope", *flags],
    )

    with caplog.at_level(logging.DEBUG):
        main()

    messages = [record.getMessage() for record in caplog.records]
    assert f"Reading config {config_path}" in messages
    assert "config [mailgun]: enabled" in messages
    assert "env GITHUB_TOKEN: not set" in messages
    assert any(message.startswith("config [[products]]: name=httpx") for message in messages)
    assert any(message.startswith("config database.path:") for message in messages)


def test_single_v_stays_at_info(tmp_path: Path, caplog, monkeypatch) -> None:
    config_path = _verbose_config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "-v", "--config", str(config_path), "check", "--name", "nope"],
    )

    with caplog.at_level(logging.DEBUG):
        main()

    assert logging.getLogger().level == logging.INFO
    assert not [record for record in caplog.records if record.levelno == logging.DEBUG]


def test_log_level_wins_over_verbose(tmp_path: Path, monkeypatch) -> None:
    config_path = _verbose_config(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repo-version-monitor", "--config", str(config_path),
            "check", "--name", "nope", "--verbose", "--log-level", "error",
        ],
    )

    main()

    assert logging.getLogger().level == logging.ERROR


def _check_output(config_path: Path, capsys, monkeypatch, *flags: str) -> list[str]:
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "check", "--name", "httpx", *flags],
    )
    main()
    return capsys.readouterr().out.splitlines()


def test_check_reports_only_configured_sources(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"

[github]
token = "ghp_inline"

[mailgun]
enabled = false

[proxy]
enabled = true
type = "socks5"
host = "127.0.0.1"
port = 1080

[[products]]
name = "httpx"
repository = "encode/httpx"

[[products]]
name = "runner"
provider = "gitlab"
repository = "gitlab-org/gitlab-runner"
""".lstrip(),
        encoding="utf-8",
    )

    lines = _check_output(config_path, capsys, monkeypatch)

    assert "GitHub token: config github.token" in lines
    assert "Proxy: socks5://127.0.0.1:1080" in lines
    # No GitLab token and no mail channel: nothing to report, so no line at all.
    assert not [line for line in lines if line.startswith("GitLab token")]
    assert not [line for line in lines if "not set" in line]


def test_check_says_nothing_when_nothing_is_configured(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
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

    lines = _check_output(config_path, capsys, monkeypatch)

    assert lines[0] == "Checking 1 product(s) named 'httpx'."
    assert lines[1].startswith("Detected ")


def test_verbose_drops_the_source_lines(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[database]
path = "versions.sqlite3"

[github]
token = "ghp_inline"

[mailgun]
enabled = false

[[products]]
name = "httpx"
repository = "encode/httpx"
""".lstrip(),
        encoding="utf-8",
    )

    lines = _check_output(config_path, capsys, monkeypatch, "--verbose")

    # The DEBUG log already reports every setting and where it came from.
    assert not [line for line in lines if line.startswith("GitHub token")]


def test_render_table_alignment() -> None:
    lines = _render_table(
        ("ID", "NAME", "REPOSITORY", "LATEST TAG"),
        [
            ("1", "FastAPI", "fastapi/fastapi", "0.116.0"),
            ("10", "httpx", "encode/httpx", "(not checked yet)"),
        ],
    )

    assert lines[0].startswith("ID  NAME")
    # ID column right-aligned
    assert lines[1].startswith(" 1  ")
    assert lines[2].startswith("10  ")
    # NAME column starts at the same offset on every line
    offset = lines[0].index("NAME")
    assert lines[1].index("FastAPI") == offset
    assert lines[2].index("httpx") == offset


def test_list_sorted_by_name_with_ids(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "zlib"
repository = "madler/zlib"

[[products]]
name = "FastAPI"
repository = "fastapi/fastapi"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["repo-version-monitor", "--config", str(config_path), "list"])

    main()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "ID", "NAME", "PROVIDER", "REPOSITORY", "BRANCH", "PREFIX", "SUFFIX", "LATEST"
    ]
    # case-insensitive sort by name: FastAPI before zlib, ids zero-padded from 01
    assert lines[1].split()[:5] == ["01", "FastAPI", "github", "fastapi/fastapi", "-"]
    assert lines[2].split()[:5] == ["02", "zlib", "github", "madler/zlib", "-"]


def test_list_sort_by_repository(tmp_path: Path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[[products]]
name = "abc"
repository = "zzz/last"

[[products]]
name = "zzz"
repository = "Aaa/first"
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "list", "--sort-by-repository"],
    )

    main()

    lines = capsys.readouterr().out.splitlines()
    # case-insensitive sort by repository, not by name
    assert lines[1].split()[:4] == ["01", "zzz", "github", "Aaa/first"]
    assert lines[2].split()[:4] == ["02", "abc", "github", "zzz/last"]
