import sys
from pathlib import Path

from repo_version_monitor.cli import _render_table, main


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
