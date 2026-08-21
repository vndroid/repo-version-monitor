from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import smtplib
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
    resolve_database_path,
)
from repo_version_monitor.db import VersionStore
from repo_version_monitor.http_client import describe as describe_proxy
from repo_version_monitor.http_client import new_async_client
from repo_version_monitor.logs import configure_logging, resolve_level
from repo_version_monitor.monitor import VersionMonitor, product_key
from repo_version_monitor.providers import DEFAULT_PROVIDER, SUPPORTED_PROVIDERS
from repo_version_monitor.repo_url import parse_repository_input, url_host


def _sync_config_hash(config_path: Path) -> list[str]:
    """Record the config hash in the DB; prune stale product data if it changed."""
    store = VersionStore(resolve_database_path(config_path))
    valid_products = {product_key(product) for product in load_products(config_path)}
    removed = store.sync_config_hash(config_file_hash(config_path), valid_products)
    for key in removed:
        print(f"Removed stale data for {key} (no longer in config).")
    return removed


def _configured_sources(config) -> list[str]:
    """One line per credential/proxy that is actually configured.

    Anything unset is left out: an empty line like "GitLab token: not set" is
    noise for a setup that never wanted one. Use -vv to see every setting.
    """
    lines = []
    if config.github.token_source:
        lines.append(f"GitHub token: {config.github.token_source}")
    uses_public_gitlab = any(
        product.provider == "gitlab" and not product.external_url
        for product in config.products
    )
    if uses_public_gitlab and config.gitlab.token_source:
        lines.append(f"GitLab token: {config.gitlab.token_source}")
    # Self-managed instances authenticate with their own product token.
    for instance in sorted(
        {
            product.external_url
            for product in config.products
            if product.external_url and product.token
        }
    ):
        lines.append(f"{instance} token: config products.token")
    if config.mailgun.enabled and config.mailgun.api_key_source:
        lines.append(f"Mailgun API key: {config.mailgun.api_key_source}")
    elif config.smtp.enabled and config.smtp.password_source:
        lines.append(f"SMTP {config.smtp.host}: {config.smtp.password_source}")
    if config.proxy.enabled:
        lines.append(f"Proxy: {describe_proxy(config.proxy)}")
    return lines


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


def _global_options() -> argparse.ArgumentParser:
    """Options accepted before *and* after the subcommand.

    SUPPRESS keeps the subparser from overwriting a value given up front:
    without it, `-v check` would be reset by the subparser's own default.
    """
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument(
        "--config", type=Path, default=argparse.SUPPRESS, help="Path to TOML config."
    )
    options.add_argument(
        "--log-level",
        default=argparse.SUPPRESS,
        help="Python logging level. Defaults to WARNING for 'check', INFO otherwise.",
    )
    options.add_argument(
        "-v",
        action="count",
        dest="verbose_count",
        default=argparse.SUPPRESS,
        help=(
            "More logs: -v is INFO, -vv is DEBUG (config file keys, environment "
            "variables, resolved settings), -vvv adds the HTTP internals."
        ),
    )
    options.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Same as -vv: DEBUG logs covering config and environment loading.",
    )
    return options


def main() -> None:
    common = _global_options()
    parser = argparse.ArgumentParser(
        description="Monitor GitHub/GitLab tags and notify by email (Mailgun or SMTP).",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", parents=[common], help="Run one check and exit.")
    check_parser.add_argument(
        "--name",
        help="Only check products with this name; all same-name entries are checked.",
    )
    check_parser.add_argument(
        "--only-blank",
        action="store_true",
        help="Only check products still shown as '(not checked yet)' in list.",
    )

    add_parser = subparsers.add_parser("add", parents=[common], help="Add a repository to the config.")
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
        "--external-url",
        help=(
            "Self-managed instance URL, e.g. https://gitlab.example.com (https:// assumed "
            "when omitted). Requires --provider."
        ),
    )
    add_parser.add_argument(
        "--token",
        help="Token for the self-managed instance; optional, anonymous access when omitted.",
    )
    add_parser.add_argument(
        "--branch",
        help="Track a branch line, e.g. v13: only tags starting with 'v13' or '13' are considered.",
    )
    add_parser.add_argument(
        "--suffix",
        help=(
            "Tag suffix to track: only tags like v19.2.2-ee are considered, without it "
            'only plain version tags are. Separate alternatives with "|", most '
            "preferred first: -ee|-ce. Write it as --suffix=-ee, since the value "
            "starts with a dash."
        ),
    )
    add_parser.add_argument(
        "--prefix",
        help=(
            "Tag prefix to track: only tags like release-1.1.1 are considered, and the "
            "version is compared without it. Separate alternatives with \"|\", most "
            "preferred first: release-|rel-."
        ),
    )

    delete_parser = subparsers.add_parser("delete", parents=[common], help="Delete a product from the config.")
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
    delete_parser.add_argument(
        "--external-url",
        help="Narrow selection to this self-managed instance, e.g. https://gitlab.example.com.",
    )

    edit_parser = subparsers.add_parser("edit", parents=[common], help="Edit the branch of an existing product.")
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
        "list", parents=[common], help="List configured repositories and known latest tags."
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
        parents=[common],
        help="Create config from template if missing; otherwise validate and normalize it.",
    )

    subparsers.add_parser(
        "resend",
        parents=[common],
        help="Resend email for updates whose notification was never sent.",
    )

    mailtest_parser = subparsers.add_parser(
        "mailtest", parents=[common], help="Verify mail settings and send a test email."
    )
    mailtest_parser.add_argument(
        "--ignore",
        action="store_true",
        help="Ignore the disabled channel and send the test email anyway.",
    )

    run_parser = subparsers.add_parser("run", parents=[common], help="Run checks forever.")
    run_parser.add_argument("--interval", type=int, default=None, help="Seconds between checks.")

    args = parser.parse_args()
    # SUPPRESS leaves the attributes unset when the flag is absent.
    args.config = getattr(args, "config", None) or Path("config.toml")
    args.log_level = getattr(args, "log_level", None)
    verbosity = max(
        getattr(args, "verbose_count", 0), 2 if getattr(args, "verbose", False) else 0
    )
    log_level = resolve_level(args.command, args.log_level, verbosity)
    configure_logging(log_level, verbosity)
    logging.getLogger(__name__).debug(
        "Starting repo-version-monitor %s (config %s, log level %s)",
        args.command,
        args.config,
        log_level,
    )

    if args.command == "add":
        try:
            parsed = parse_repository_input(args.repository, args.provider, args.external_url)
            repository, provider = parsed.repository, parsed.provider
            name = args.name or repository.rsplit("/", 1)[-1]
            add_product_to_config(
                args.config,
                name,
                repository,
                args.branch,
                provider,
                parsed.external_url,
                args.token,
                args.suffix,
                args.prefix,
            )
        except ValueError as exc:
            add_parser.error(str(exc))
        details = f", branch {args.branch}" if args.branch else ""
        if args.prefix:
            details += f", prefix {args.prefix}"
        if args.suffix:
            details += f", suffix {args.suffix}"
        # Not the tag prefix: this is the "gitlab:" marker in front of the path.
        provider_marker = f"{provider}:" if provider != DEFAULT_PROVIDER else ""
        location = f"{url_host(parsed.external_url)}/" if parsed.external_url else ""
        print(f"Added {name} ({provider_marker}{location}{repository}{details}) to {args.config}.")
        if args.provider is None and parsed.inferred_from_host and provider != DEFAULT_PROVIDER:
            print(f"Provider {provider} inferred from host {parsed.host}.")
        if parsed.external_url:
            auth = "token" if args.token else "anonymous access"
            print(f"Self-managed instance {parsed.external_url} ({auth}).")
        _sync_config_hash(args.config)
        return

    if args.command == "delete":
        try:
            product = delete_product(
                args.config,
                args.name,
                args.repository,
                args.branch if args.branch is not None else _UNSET,
                args.provider,
                args.external_url,
            )
        except ValueError as exc:
            delete_parser.error(str(exc))
        repository = product.repository
        if product.external_url:
            repository = f"{url_host(product.external_url)}/{repository}"
        label = f"{repository}, branch {product.branch}" if product.branch else repository
        if product.prefix:
            label = f"{label}, prefix {product.prefix}"
        if product.suffix:
            label = f"{label}, suffix {product.suffix}"
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
        # Keyed exactly like product_key(), so stored rows line up with the config.
        stored_by_product = {
            (product.provider, product.external_url, product.repository, product.branch): product
            for product in store.list_products()
        }

        def repository_cell(product) -> str:
            """Self-managed products are shown the way they are typed into `add`."""
            if not product.external_url:
                return product.repository
            return f"{url_host(product.external_url)}/{product.repository}"

        if args.sort_by_repository:
            def sort_key(product):
                return (repository_cell(product).casefold(), product.branch or "")
        else:  # default: --sort-by-name
            def sort_key(product):
                return product.name.casefold()

        id_width = 3 if len(products) >= 100 else 2
        rows = []
        for index, product in enumerate(sorted(products, key=sort_key), start=1):
            stored = stored_by_product.get(product_key(product))
            latest_tag = stored.latest_tag if stored and stored.latest_tag else "(not checked yet)"
            rows.append(
                (
                    str(index).zfill(id_width),
                    product.name,
                    product.provider,
                    repository_cell(product),
                    product.branch or "-",
                    # "/" rather than "-", which reads like part of a prefix.
                    product.prefix or "/",
                    # "/" rather than "-", which reads like the start of a suffix.
                    product.suffix or "/",
                    latest_tag,
                )
            )

        for line in _render_table(
            ("ID", "NAME", "PROVIDER", "REPOSITORY", "BRANCH", "PREFIX", "SUFFIX", "LATEST"),
            rows,
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
        # Only report what is actually configured, and stay out of the way at
        # -vv: the DEBUG log already spells out every setting and its source.
        if verbosity < 2:
            for line in _configured_sources(config):
                print(line)
        show_progress = args.log_level is None and verbosity == 0
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
        # With both channels off, --ignore tests whichever one looks configured.
        use_smtp = config.smtp.enabled or (
            not config.mailgun.enabled and bool(config.smtp.host)
        )
        channel = "smtp" if use_smtp else "mailgun"
        if not config.notifications_enabled:
            if not args.ignore:
                print(
                    "Email notifications are disabled (mailgun.enabled and smtp.enabled "
                    "are both false); enable one first or pass --ignore."
                )
                return
            print(f"Notifications disabled, ignored (--ignore); testing {channel}.")

        if use_smtp:
            settings = config.smtp
            print("SMTP configuration:")
            print(f"  host:       {settings.host}:{settings.port}")
            print(f"  encryption: {settings.encryption}")
            print(f"  from:       {settings.from_email}")
            print(f"  to:         {', '.join(settings.to_emails) or '(empty)'}")
            print(f"  username:   {settings.username or '(no authentication)'}")
            print(f"  password:   {settings.password_source or 'not set'}")
            required = (
                ("smtp.host", settings.host),
                ("smtp.from_email", settings.from_email),
                ("smtp.to_emails", settings.to_emails),
            )
        else:
            settings = config.mailgun
            print("Mailgun configuration:")
            print(f"  domain:   {settings.domain}")
            print(f"  from:     {settings.from_email}")
            print(f"  to:       {', '.join(settings.to_emails) or '(empty)'}")
            print(f"  api_url:  {settings.api_url}")
            print(f"  proxy:    {describe_proxy(config.proxy)}")
            print(f"  api_key:  {settings.api_key_source or 'not set'}")
            required = (
                ("mailgun.domain", settings.domain),
                ("mailgun.from_email", settings.from_email),
                ("mailgun.to_emails", settings.to_emails),
                ("mailgun.api_key", settings.api_key),
            )

        problems = [f"{field} is empty" for field, value in required if not value]
        if problems:
            for problem in problems:
                print(f"Config problem: {problem}")
            return

        async def _send_test():
            async with new_async_client(config.proxy) as client:
                sender = monitor.smtp if use_smtp else monitor.mailgun
                return await sender.send_test(client)

        print("Sending test email...")
        start = time.monotonic()
        try:
            response = asyncio.run(_send_test())
        except (httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
            print(f"Send failed ({time.monotonic() - start:.1f}s): {exc}")
            return
        if response is None:  # SMTP raises on failure, so getting here means sent
            print(f"Success in {time.monotonic() - start:.1f}s.")
            return
        status = "Success" if response.is_success else "Failed"
        print(f"{status} (HTTP {response.status_code}) in {time.monotonic() - start:.1f}s.")
        print(f"Response: {response.text.strip()}")
        return

    if args.command == "resend":
        if not config.notifications_enabled:
            print(
                "Email notifications are disabled (mailgun.enabled and smtp.enabled "
                "are both false); nothing sent."
            )
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
