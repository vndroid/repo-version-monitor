import sys
from pathlib import Path

import pytest

from repo_version_monitor.cli import _render_table, main
from repo_version_monitor.config import load_products


def _run_add(config_path: Path, monkeypatch, *add_args: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["repo-version-monitor", "--config", str(config_path), "add", *add_args],
    )
    main()


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


def test_add_self_managed_gitlab_warns_about_base_url(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")

    _run_add(config_path, monkeypatch, "git.mycorp.com/group/project", "--provider", "gitlab")

    product = load_products(config_path)[0]
    assert (product.provider, product.repository) == ("gitlab", "group/project")
    assert "Warning:" in capsys.readouterr().out


def test_add_self_managed_gitlab_matching_base_url_is_quiet(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[gitlab]\nbase_url = "https://git.mycorp.com"\n', encoding="utf-8"
    )

    _run_add(config_path, monkeypatch, "git.mycorp.com/group/project", "--provider", "gitlab")

    assert "Warning:" not in capsys.readouterr().out


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
    assert lines[0].split() == ["ID", "NAME", "PROVIDER", "REPOSITORY", "BRANCH", "LATEST"]
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
