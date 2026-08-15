from __future__ import annotations

import logging
import re

import pytest

from repo_version_monitor.logs import (
    DATE_FORMAT,
    LOG_FORMAT,
    configure_logging,
    read_env,
    resolve_level,
)


@pytest.mark.parametrize(
    ("command", "log_level", "verbosity", "expected"),
    [
        # Command defaults: check stays quiet, everything else talks.
        ("check", None, 0, "WARNING"),
        ("run", None, 0, "INFO"),
        # -v / -vv / --verbose
        ("check", None, 1, "INFO"),
        ("check", None, 2, "DEBUG"),
        ("check", None, 3, "DEBUG"),
        # --log-level always wins, whatever the verbosity.
        ("check", "error", 0, "ERROR"),
        ("run", "DEBUG", 0, "DEBUG"),
        ("check", "warning", 3, "WARNING"),
    ],
)
def test_resolve_level(command: str, log_level: str | None, verbosity: int, expected: str) -> None:
    assert resolve_level(command, log_level, verbosity) == expected


def test_log_line_looks_like_gunicorn(caplog) -> None:
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    record = logging.LogRecord(
        "repo_version_monitor.cli", logging.INFO, __file__, 1, "Starting", None, None
    )

    line = formatter.format(record)

    # [2026-08-11 12:53:45 -0700] [1] [INFO] Starting
    assert re.fullmatch(
        r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}\] \[\d+\] \[INFO\] Starting", line
    )


def test_noisy_libraries_stay_at_info_until_the_third_v() -> None:
    # httpcore logs every socket read at DEBUG, which buries our own lines.
    configure_logging("DEBUG", verbosity=2)
    assert logging.getLogger("httpcore").level == logging.INFO

    configure_logging("DEBUG", verbosity=3)
    # NOTSET: follow the root logger, which -vvv put at DEBUG.
    assert logging.getLogger("httpcore").level == logging.NOTSET

    configure_logging("WARNING", verbosity=0)
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_read_env_reports_without_leaking_the_value(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "super-secret")
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    with caplog.at_level(logging.DEBUG, logger="repo_version_monitor.config"):
        assert read_env("SECRET_TOKEN") == "super-secret"
        assert read_env("MISSING_TOKEN") is None

    messages = [record.getMessage() for record in caplog.records]
    assert messages == ["env SECRET_TOKEN: set", "env MISSING_TOKEN: not set"]
    assert "super-secret" not in "".join(messages)
