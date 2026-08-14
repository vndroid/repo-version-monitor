from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import time

import httpx

from repo_version_monitor.config import (
    _UNSET,
    add_product_to_config,
    config_file_hash,
    delete_product,
    edit_product_branch,
    format_config,
    load_config,
    load_products,
    read_provider_external_url,
    resolve_database_path,
)
from repo_version_monitor.db import VersionStore
from repo_version_monitor.monitor import VersionMonitor
from repo_version_monitor.providers import DEFAULT_PROVIDER, SUPPORTED_PROVIDERS
from repo_version_monitor.repo_url import host_mismatch_warning, parse_repository_input


def _sync_config_hash(config_path: Path) -> list[str]:
    """Record the config hash in the DB; prune stale product data if it changed."""
    store = VersionStore(resolve_database_path(config_path))
    valid_products = {
        (product.provider, product.repository, product.branch)
        for product in load_products(config_path)
    }
    removed = store.sync_config_hash(config_file_hash(config_path), valid_products)
    for key in removed:
        print(f"Removed stale data for {key} (no longer in config).")
    return removed


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
    parser = argparse.ArgumentParser(
        description="Monitor GitHub/GitLab tags and notify via Mailgun."
    )
    parser.add_argument("--config", default="config.toml", type=Path, help="Path to TOML config.")
    parser.add_argument(
        "--log-level",
        default=None,
        help="Python logging level. Defaults to WARNING for 'check', INFO otherwise.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Run one check and exit.")
    check_parser.add_argument(
        "--name",
        help="Only check products with this name; all same-name entries are checked.",
    )
    check_parser.add_argument(
        "--only-blank",
        action="store_true",
        help="Only check products still shown as '(not checked yet)' in list.",
    )

    add_parser = subparsers.add_parser("add", help="Add a repository to the config.")
    add_parser.add_argument(
        "repository",
        help=(
            "Repository path or URL: owner/name, github.com/owner/name, or "
            "https://gitlab.com/group/sub/project. Without a host, github.com is assumed."
        ),
    )
    add_parser.add_argument("--name", help="Display name. Defaults to the repository name.")
    add_parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        default=None,
        help=(
            "Code-hosting provider. Inferred from the host when possible; required for "
            "self-managed hosts, e.g. --provider=gitlab (default: github)."
        ),
    )
    add_parser.add_argument(
        "--branch",
        help="Track a branch line, e.g. v13: only tags starting with 'v13' or '13' are considered.",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete a product from the config.")
    delete_parser.add_argument("--name", help="Select by product name; errors if ambiguous.")
    delete_parser.add_argument("--repository", help="Select precisely by owner/name repository.")
    delete_parser.add_argument(
        "--branch",
        help="Narrow selection to this branch; pass an empty string for entries without one.",
    )
    delete_parser.add_argument(
        "--provider",
        choices=SUPPORTED_PROVIDERS,
        help="Narrow selection to this provider.",
    )

    edit_parser = subparsers.add_parser("edit", help="Edit the branch of an existing product.")
    edit_parser.add_argument("name", help="Product name as shown by 'list'.")
    edit_parser.add_argument(
        "--branch",
        required=True,
        help="New branch line, e.g. v13. Pass an empty string to clear the branch.",
    )
    edit_parser.add_argument(
        "--repository",
        help="Disambiguate when several products share the same name (owner/name format).",
    )

    list_parser = subparsers.add_parser(
        "list", help="List configured repositories and known latest tags."
    )
    sort_group = list_parser.add_mutually_exclusive_group()
    sort_group.add_argument(
        "--sort-by-name",
        action="store_true",
        help="Sort by product name, case-insensitive (default).",
    )
    sort_group.add_argument(
        "--sort-by-repository",
        action="store_true",
        help="Sort by repository (then branch), case-insensitive.",
    )

    subparsers.add_parser(
        "format",
        help="Create config from template if missing; otherwise validate and normalize it.",
    )

    subparsers.add_parser(
        "resend", help="Resend email for updates whose notification was never sent."
    )

    mailtest_parser = subparsers.add_parser(
        "mailtest", help="Verify mail settings and send a test email via Mailgun."
    )
    mailtest_parser.add_argument(
        "--ignore",
        action="store_true",
        help="Ignore mailgun.enabled = false and send the test email anyway.",
    )

    run_parser = subparsers.add_parser("run", help="Run checks forever.")
    run_parser.add_argument("--interval", type=int, default=None, help="Seconds between checks.")

    args = parser.parse_args()
    default_level = "WARNING" if args.command == "check" else "INFO"
    log_level = args.log_level or default_level
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "add":
        try:
            parsed = parse_repository_input(args.repository, args.provider)
            repository, provider = parsed.repository, parsed.provider
            name = args.name or repository.rsplit("/", 1)[-1]
            add_product_to_config(args.config, name, repository, args.branch, provider)
        except ValueError as exc:
            add_parser.error(str(exc))
        suffix = f", branch {args.branch}" if args.branch else ""
        prefix = f"{provider}:" if provider != DEFAULT_PROVIDER else ""
        print(f"Added {name} ({prefix}{repository}{suffix}) to {args.config}.")
        if args.provider is None and parsed.inferred_from_host and provider != DEFAULT_PROVIDER:
            print(f"Provider {provider} inferred from host {parsed.host}.")
        try:
            configured_url = read_provider_external_url(args.config, provider)
        except ValueError as exc:
            # The product was already written; surface the config problem instead.
            print(f"Warning: {exc}")
        else:
            warning = host_mismatch_warning(parsed, configured_url)
            if warning:
                print(f"Warning: {warning}")
        _sync_config_hash(args.config)
        return

    if args.command == "delete":
        product = delete_product(
            args.config,
            args.name,
            args.repository,
            args.branch if args.branch is not None else _UNSET,
            args.provider,
        )
        label = (
            f"{product.repository}, branch {product.branch}"
            if product.branch
            else product.repository
        )
        if product.provider != DEFAULT_PROVIDER:
            label = f"{product.provider}:{label}"
        print(f"Deleted {product.name} ({label}) from {args.config}.")
        _sync_config_hash(args.config)
        return

    if args.command == "edit":
        old_branch, product = edit_product_branch(
            args.config, args.name, args.branch, args.repository
        )
        print(
            f"Updated {product.name} ({product.repository}): "
            f"branch {old_branch or '(none)'} -> {product.branch or '(none)'}."
        )
        _sync_config_hash(args.config)
        return

    if args.command == "format":
        result = format_config(args.config)
        if result.action == "created":
            print(f"{args.config} did not exist; created it from the template.")
        else:
            print(f"Formatted {args.config}.")
        if result.added_settings:
            print(f"Added {len(result.added_settings)} missing setting(s):")
            for setting in result.added_settings:
                print(f"- {setting}")
        _sync_config_hash(args.config)
        return

    if args.command == "list":
        products = load_products(args.config)
        if not products:
            print("No repositories configured.")
            return

        _sync_config_hash(args.config)
        store = VersionStore(resolve_database_path(args.config))
        stored_by_product = {
            (product.provider, product.repository, product.branch): product
            for product in store.list_products()
        }

        if args.sort_by_repository:
            def sort_key(product):
                return (product.repository.casefold(), product.branch or "")
        else:  # default: --sort-by-name
            def sort_key(product):
                return product.name.casefold()

        id_width = 3 if len(products) >= 100 else 2
        rows = []
        for index, product in enumerate(sorted(products, key=sort_key), start=1):
            stored = stored_by_product.get(
                (product.provider, product.repository, product.branch)
            )
            latest_tag = stored.latest_tag if stored and stored.latest_tag else "(not checked yet)"
            rows.append(
                (
                    str(index).zfill(id_width),
                    product.name,
                    product.provider,
                    product.repository,
                    product.branch or "-",
                    latest_tag,
                )
            )

        for line in _render_table(
            ("ID", "NAME", "PROVIDER", "REPOSITORY", "BRANCH", "LATEST"), rows
        ):
            print(line)
        return

    config = load_config(args.config)
    monitor = VersionMonitor(config)

    if args.command == "check":
        if args.name is not None:
            matched = [p for p in config.products if p.name == args.name]
            if not matched:
                print(f"No product named {args.name!r} in the config.")
                return
            print(f"Checking {len(matched)} product(s) named {args.name!r}.")
        if args.only_blank:
            candidates = matched if args.name is not None else config.products
            monitor.store.initialize()
            blank_count = sum(
                1 for product in candidates if not monitor._has_latest_tag(product)
            )
            if blank_count == 0:
                print("All products already have a latest tag; nothing to check.")
                return
            print(f"Checking {blank_count} unchecked product(s).")
        print(f"GitHub token: {config.github.token_source or 'not set (unauthenticated)'}")
        if any(product.provider == "gitlab" for product in config.products):
            print(f"GitLab token: {config.gitlab.token_source or 'not set (unauthenticated)'}")
        if config.mailgun.enabled:
            print(f"Mailgun API key: {config.mailgun.api_key_source or 'not set'}")
        show_progress = args.log_level is None
        dots_printed = 0

        async def _check() -> list:
            nonlocal dots_printed

            async def _ticker() -> None:
                nonlocal dots_printed
                while True:
                    await asyncio.sleep(1)
                    print(".", end="", flush=True)
                    dots_printed += 1

            ticker = asyncio.create_task(_ticker()) if show_progress else None
            try:
                return await monitor.check_once(args.name, only_blank=args.only_blank)
            finally:
                if ticker is not None:
                    ticker.cancel()

        start = time.monotonic()
        updates = asyncio.run(_check())
        elapsed = time.monotonic() - start
        if dots_printed:
            print()
        print(f"Detected {len(updates)} update(s) in {elapsed:.1f}s.")
        for update in updates:
            old_tag = update.old_tag or "(first seen)"
            print(f"- {update.product_name}: {old_tag} -> {update.new_tag}")
        return

    if args.command == "mailtest":
        mg = config.mailgun
        if not mg.enabled:
            if not args.ignore:
                print(
                    "Email notifications are disabled (mailgun.enabled = false); "
                    "enable them first or pass --ignore."
                )
                return
            print("mailgun.enabled = false ignored (--ignore); proceeding with the test.")
        print("Mailgun configuration:")
        print(f"  domain:   {mg.domain}")
        print(f"  from:     {mg.from_email}")
        print(f"  to:       {', '.join(mg.to_emails) or '(empty)'}")
        print(f"  base_url: {mg.base_url}")
        print(f"  api_key:  {mg.api_key_source or 'not set'}")

        problems = [
            f"{field} is empty"
            for field, value in (
                ("mailgun.domain", mg.domain),
                ("mailgun.from_email", mg.from_email),
                ("mailgun.to_emails", mg.to_emails),
                ("mailgun.api_key", mg.api_key),
            )
            if not value
        ]
        if problems:
            for problem in problems:
                print(f"Config problem: {problem}")
            return

        async def _send_test() -> httpx.Response:
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await monitor.mailgun.send_test(client)

        print("Sending test email...")
        start = time.monotonic()
        try:
            response = asyncio.run(_send_test())
        except httpx.HTTPError as exc:
            print(f"Send failed ({time.monotonic() - start:.1f}s): {exc}")
            return
        status = "Success" if response.is_success else "Failed"
        print(f"{status} (HTTP {response.status_code}) in {time.monotonic() - start:.1f}s.")
        print(f"Response: {response.text.strip()}")
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
