from __future__ import annotations

import asyncio
import logging

import httpx

from repo_version_monitor.config import AppConfig, config_file_hash
from repo_version_monitor.db import VersionStore
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


class VersionMonitor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = VersionStore(config.database.path)
        self.github = GitHubClient(config.github.token, config.github.per_page)
        self.gitlab = GitLabClient(config.gitlab.token, config.gitlab.external_url)
        # One client per provider; add new providers here.
        self.providers: dict[str, TagProvider] = {
            "github": self.github,
            "gitlab": self.gitlab,
        }
        self.mailgun = MailgunClient(
            domain=config.mailgun.domain,
            api_key=config.mailgun.api_key,
            from_email=config.mailgun.from_email,
            to_emails=config.mailgun.to_emails,
            base_url=config.mailgun.base_url,
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
            {
                (product.provider, product.repository, product.branch)
                for product in self.config.products
            },
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            for product in products:
                repo, branch, provider = product.repository, product.branch, product.provider
                label = f"{repo}@{branch}" if branch else repo
                if provider != "github":
                    label = f"{provider}:{label}"
                # One failing repository must not abort checks for the rest.
                try:
                    tags = await self.providers[provider].fetch_tags(client, repo)
                except httpx.HTTPError:
                    logger.exception("Failed to fetch tags for %s", label)
                    continue
                if branch:
                    tags = filter_tags_for_branch(tags, branch)

                if not tags:
                    logger.warning("No tags found for %s", label)
                    continue

                # The API's list order is unreliable; pick the highest version tag.
                latest = pick_latest_version_tag(tags)
                if latest is None:
                    logger.warning(
                        "No version-like tags for %s; falling back to the first listed tag.",
                        label,
                    )
                    latest = tags[0]
                # Store and compare without the 'v' prefix so repositories that
                # switch between 'v1.2.3' and '1.2.3' styles don't cause noise.
                latest_tag = normalize_tag_name(latest.name)
                stored = self.store.get_product(repo, branch, provider)
                stored_tag = (
                    normalize_tag_name(stored.latest_tag)
                    if stored and stored.latest_tag
                    else None
                )

                if stored is None:
                    # Persist immediately so a later mail failure never causes
                    # duplicate events for the same tag on the next cycle.
                    self.store.upsert_product(product.name, repo, latest_tag, branch, provider)
                    if self.config.monitor.notify_on_first_seen:
                        event_ids.append(
                            self.store.record_event(repo, None, latest_tag, branch, provider)
                        )
                        updates.append(VersionUpdate(product.name, repo, None, latest_tag))
                    logger.info("Initialized %s at %s", label, latest_tag)
                elif stored_tag != latest_tag:
                    self.store.upsert_product(product.name, repo, latest_tag, branch, provider)
                    event_ids.append(
                        self.store.record_event(repo, stored_tag, latest_tag, branch, provider)
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
                    self.store.upsert_product(product.name, repo, latest_tag, branch, provider)
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

    def _has_latest_tag(self, product) -> bool:
        stored = self.store.get_product(product.repository, product.branch, product.provider)
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
        async with httpx.AsyncClient(timeout=30.0) as client:
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
