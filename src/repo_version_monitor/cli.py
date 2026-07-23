from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from repo_version_monitor.config import load_config
from repo_version_monitor.monitor import VersionMonitor


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor GitHub tags and notify via Mailgun.")
    parser.add_argument("--config", default="config.toml", type=Path, help="Path to TOML config.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Run one check and exit.")

    run_parser = subparsers.add_parser("run", help="Run checks forever.")
    run_parser.add_argument("--interval", type=int, default=None, help="Seconds between checks.")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    monitor = VersionMonitor(config)

    if args.command == "check":
        updates = asyncio.run(monitor.check_once())
        print(f"Detected {len(updates)} update(s).")
        for update in updates:
            old_tag = update.old_tag or "(first seen)"
            print(f"- {update.product_name}: {old_tag} -> {update.new_tag}")
        return

    if args.command == "run":
        asyncio.run(monitor.run_forever(args.interval))

