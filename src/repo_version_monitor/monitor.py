from __future__ import annotations

import asyncio
import logging

import httpx

from repo_version_monitor.config import (
    DEFAULT_GITLAB_EXTERNAL_URL,
    AppConfig,
    ProductConfig,
    config_file_hash,
)
from repo_version_monitor.db import VersionStore
from repo_version_monitor.http_client import new_async_client
from repo_version_monitor.mailgun import MailgunClient, VersionUpdate
from repo_version_monitor.providers import (
    GitHubClient,
    GitLabClient,
    TagProvider,
    filter_tags_for_branch,
    normalize_tag_name,
    pick_latest_version_tag,
)

logger = logging.getLogger(__name__)


def product_key(product: ProductConfig) -> tuple[str, str, str, str | None]:
    """Database key of a product: provider, instance, repository, branch.

    The tag suffix is not part of it, so changing it in the config keeps the
    stored version and the next check reports it as a normal update.
    """
    return (product.provider, product.external_url or "", product.repository, product.branch)


def product_label(product: ProductConfig) -> str:
    """Human-readable product identity used in logs."""
    repository = product.repository
    if product.external_url:
        repository = f"{product.external_url.split('://', 1)[-1]}/{repository}"
    label = f"{repository}@{product.branch}" if product.branch else repository
    if product.provider != "github":
        label = f"{product.provider}:{label}"
    return f"{label} ({product.suffix})" if product.suffix else label


class VersionMonitor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = VersionStore(config.database.path)
        self.github = GitHubClient(config.github.token, config.github.per_page)
        self.gitlab = GitLabClient(config.gitlab.token, DEFAULT_GITLAB_EXTERNAL_URL)
        # Clients for the public instances; add new providers here. Self-managed
        # instances get their own client, built on demand per (url, token).
        self.providers: dict[str, TagProvider] = {
            "github": self.github,
            "gitlab": self.gitlab,
        }
        self._instances: dict[tuple[str, str | None], TagProvider] = {}
        self.mailgun = MailgunClient(
            domain=config.mailgun.domain,
            api_key=config.mailgun.api_key,
            from_email=config.mailgun.from_email,
            to_emails=config.mailgun.to_emails,
            api_url=config.mailgun.api_url,
        )

    async def check_once(
        self, only_name: str | None = None, only_blank: bool = False
    ) -> list[VersionUpdate]:
        """Check all products, or a subset.

        only_name: only products with this name (all same-name entries).
        only_blank: only products without a latest tag in the database yet.
        """
        self.store.initialize()
        removed = self.store.sync_config_hash(
            config_file_hash(self.config.source_path),
            {product_key(product) for product in self.config.products},
        )
        for key in removed:
            logger.info("Removed stale data for %s (no longer in config).", key)
        updates: list[VersionUpdate] = []
        event_ids: list[int] = []

        products = self.config.products
        if only_name is not None:
            products = [product for product in products if product.name == only_name]
        if only_blank:
            products = [
                product for product in products if not self._has_latest_tag(product)
            ]

        async with new_async_client(self.config.proxy) as client:
            for product in products:
                repo, branch, provider = product.repository, product.branch, product.provider
                instance = product.external_url or ""
                suffix = product.suffix or ""
                label = product_label(product)
                # One failing repository must not abort checks for the rest.
                try:
                    tags = await self.client_for(product).fetch_tags(client, repo)
                except httpx.HTTPError:
                    logger.exception("Failed to fetch tags for %s", label)
                    continue
                if branch:
                    tags = filter_tags_for_branch(tags, branch)

                if not tags:
                    logger.warning("No tags found for %s", label)
                    continue

                # The API's list order is unreliable; pick the highest version tag.
                latest = pick_latest_version_tag(tags, suffix)
                if latest is None:
                    logger.warning(
                        "No version-like tags for %s; falling back to the first listed tag.",
                        label,
                    )
                    latest = tags[0]
                # Store and compare without the 'v' prefix so repositories that
                # switch between 'v1.2.3' and '1.2.3' styles don't cause noise.
                latest_tag = normalize_tag_name(latest.name)
                stored = self.store.get_product(repo, branch, provider, instance)
                stored_tag = (
                    normalize_tag_name(stored.latest_tag)
                    if stored and stored.latest_tag
                    else None
                )

                if stored is None:
                    # Persist immediately so a later mail failure never causes
                    # duplicate events for the same tag on the next cycle.
                    self.store.upsert_product(
                        product.name, repo, latest_tag, branch, provider, instance
                    )
                    if self.config.monitor.notify_on_first_seen:
                        event_ids.append(
                            self.store.record_event(
                                repo, None, latest_tag, branch, provider, instance
                            )
                        )
                        updates.append(VersionUpdate(product.name, repo, None, latest_tag))
                    logger.info("Initialized %s at %s", label, latest_tag)
                elif stored_tag != latest_tag:
                    self.store.upsert_product(
                        product.name, repo, latest_tag, branch, provider, instance
                    )
                    event_ids.append(
                        self.store.record_event(
                            repo, stored_tag, latest_tag, branch, provider, instance
                        )
                    )
                    updates.append(
                        VersionUpdate(product.name, repo, stored_tag, latest_tag)
                    )
                    logger.info(
                        "Detected update for %s: %s -> %s",
                        label,
                        stored_tag,
                        latest_tag,
                    )
                else:
                    self.store.upsert_product(
                        product.name, repo, latest_tag, branch, provider, instance
                    )
                    logger.info("%s is unchanged at %s", label, latest_tag)

            if updates:
                if self.config.mailgun.enabled:
                    # Events with notified_at IS NULL indicate a failed/missed email.
                    await self.mailgun.send_updates(client, updates)
                    for event_id in event_ids:
                        self.store.mark_notified(event_id)
                else:
                    logger.info(
                        "Email notifications disabled; skipping email for %d update(s).",
                        len(updates),
                    )

        return updates

    def client_for(self, product: ProductConfig) -> TagProvider:
        """Client for the instance a product lives on.

        Public instances share the client built from the config; self-managed
        ones get their own, cached per (instance URL, token) so several products
        on the same instance reuse one client.
        """
        if not product.external_url:
            return self.providers[product.provider]

        key = (product.external_url, product.token)
        client = self._instances.get(key)
        if client is None:
            # Only GitLab supports self-managed instances today; config rejects
            # external_url for every other provider.
            client = GitLabClient(product.token, product.external_url)
            self._instances[key] = client
        return client

    def _has_latest_tag(self, product: ProductConfig) -> bool:
        stored = self.store.get_product(
            product.repository, product.branch, product.provider, product.external_url or ""
        )
        return stored is not None and bool(stored.latest_tag)

    async def resend_unnotified(self) -> list[VersionUpdate]:
        """Resend email for recorded events whose notification never went out."""
        self.store.initialize()
        events = self.store.list_unnotified_events()
        if not events:
            return []

        updates = [
            VersionUpdate(event.product_name, event.repository, event.old_tag, event.new_tag)
            for event in events
        ]
        # Always one all-in-one email covering the whole backlog.
        async with new_async_client(self.config.proxy) as client:
            await self.mailgun.send_updates(client, updates, subject_prefix="accumulation:")
        for event in events:
            self.store.mark_notified(event.event_id)
        logger.info("Resent notification for %d event(s).", len(events))
        return updates

    async def run_forever(self, interval_seconds: int | None = None) -> None:
        interval = interval_seconds or self.config.monitor.interval_seconds
        while True:
            try:
                updates = await self.check_once()
                logger.info("Check complete. Updates: %d", len(updates))
            except Exception:
                logger.exception("Version check failed")
            await asyncio.sleep(interval)
