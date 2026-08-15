"""Logging setup: gunicorn-style lines and the -v/--verbose levels."""

from __future__ import annotations

import logging
import os

#: [2026-08-11 12:53:45 -0700] [1] [INFO] message
LOG_FORMAT = "[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"

#: These log every socket read at DEBUG; they stay quiet until -vvv.
NOISY_LOGGERS = ("httpcore", "hpack", "asyncio")


def resolve_level(command: str, log_level: str | None, verbosity: int = 0) -> str:
    """Effective log level: --log-level wins, then -v, then the command default.

    -v is INFO, -vv (= --verbose) is DEBUG; more only widens third-party logs.
    """
    if log_level:
        return log_level.upper()
    if verbosity >= 2:
        return "DEBUG"
    if verbosity == 1:
        return "INFO"
    # 'check' is meant to be run by hand, so it stays quiet by default.
    return "WARNING" if command == "check" else "INFO"


def configure_logging(level: str, verbosity: int = 0) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    # basicConfig does nothing when the root logger already has a handler,
    # so set the level ourselves to stay predictable.
    logging.getLogger().setLevel(numeric)
    for name in NOISY_LOGGERS:
        # NOTSET makes them follow the root level again, so -vvv opens them up.
        logging.getLogger(name).setLevel(
            max(numeric, logging.INFO) if verbosity < 3 else logging.NOTSET
        )


def read_env(name: str) -> str | None:
    """os.getenv that reports what it found, without ever logging the value."""
    value = os.getenv(name)
    logging.getLogger("repo_version_monitor.config").debug(
        "env %s: %s", name, "set" if value else "not set"
    )
    return value
