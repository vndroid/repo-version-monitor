from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from repo_version_monitor.config import (
    add_product_to_config,
    load_config,
    load_products,
    resolve_database_path,
)
from repo_version_monitor.db import VersionStore
from repo_version_monitor.monitor import VersionMonitor


def _render_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Render rows as aligned columns; the first column (ID) is right-aligned."""
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def render_row(row: tuple[str, ...]) -> str:
        cells = [
            cell.rjust(widths[i]) if i == 0 else cell.ljust(widths[i])
            for i, cell in enumerate(row)
        ]
        return "  ".join(cells).rstrip()

    return [render_row(headers), *(render_row(row) for row in rows)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor GitHub tags and notify via Mailgun.")
    parser.add_argument("--config", default="config.toml", type=Path, help="Path to TOML config.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Run one check and exit.")

    add_parser = subparsers.add_parser("add", help="Add a GitHub repository to the config.")
    add_parser.add_argument("repository", help="GitHub repository in owner/name format.")
    add_parser.add_argument("--name", help="Display name. Defaults to the repository name.")
    add_parser.add_argument(
        "--branch",
        help="Track a branch line, e.g. v13: only tags starting with 'v13' or '13' are considered.",
    )

    subparsers.add_parser("list", help="List configured repositories and known latest tags.")

    subparsers.add_parser(
        "resend", help="Resend email for updates whose notification was never sent."
    )

    run_parser = subparsers.add_parser("run", help="Run checks forever.")
    run_parser.add_argument("--interval", type=int, default=None, help="Seconds between checks.")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "add":
        name = args.name or args.repository.rsplit("/", 1)[-1]
        add_product_to_config(args.config, name, args.repository, args.branch)
        suffix = f", branch {args.branch}" if args.branch else ""
        print(f"Added {name} ({args.repository}{suffix}) to {args.config}.")
        return

    if args.command == "list":
        products = load_products(args.config)
        if not products:
            print("No repositories configured.")
            return

        store = VersionStore(resolve_database_path(args.config))
        stored_by_repository = {
            product.repository: product
            for product in store.list_products()
        }

        id_width = 3 if len(products) >= 100 else 2
        rows = []
        for index, product in enumerate(
            sorted(products, key=lambda product: product.name.casefold()), start=1
        ):
            stored = stored_by_repository.get(product.key)
            latest_tag = stored.latest_tag if stored and stored.latest_tag else "(not checked yet)"
            rows.append(
                (
                    str(index).zfill(id_width),
                    product.name,
                    product.repository,
                    product.branch or "-",
                    latest_tag,
                )
            )

        for line in _render_table(("ID", "NAME", "REPOSITORY", "BRANCH", "LATEST TAG"), rows):
            print(line)
        return

    config = load_config(args.config)
    monitor = VersionMonitor(config)

    if args.command == "check":
        updates = asyncio.run(monitor.check_once())
        print(f"Detected {len(updates)} update(s).")
        for update in updates:
            old_tag = update.old_tag or "(first seen)"
            print(f"- {update.product_name}: {old_tag} -> {update.new_tag}")
        return

    if args.command == "resend":
        if not config.mailgun.enabled:
            print("Email notifications are disabled (mailgun.enabled = false); nothing sent.")
            return
        updates = asyncio.run(monitor.resend_unnotified())
        if not updates:
            print("No unnotified updates found.")
            return
        print(f"Resent notification for {len(updates)} update(s).")
        for update in updates:
            old_tag = update.old_tag or "(first seen)"
            print(f"- {update.product_name}: {old_tag} -> {update.new_tag}")
        return

    if args.command == "run":
        asyncio.run(monitor.run_forever(args.interval))
