from __future__ import annotations

import asyncio
import logging

import httpx

from repo_version_monitor.config import AppConfig
from repo_version_monitor.db import VersionStore
from repo_version_monitor.github import GitHubClient, filter_tags_for_branch
from repo_version_monitor.mailgun import MailgunClient, VersionUpdate

logger = logging.getLogger(__name__)


class VersionMonitor:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = VersionStore(config.database.path)
        self.github = GitHubClient(config.github.token, config.github.per_page)
        self.mailgun = MailgunClient(
            domain=config.mailgun.domain,
            api_key=config.mailgun.api_key,
            from_email=config.mailgun.from_email,
            to_emails=config.mailgun.to_emails,
            base_url=config.mailgun.base_url,
        )

    async def check_once(self) -> list[VersionUpdate]:
        self.store.initialize()
        updates: list[VersionUpdate] = []
        event_ids: list[int] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for product in self.config.products:
                key = product.key
                # One failing repository must not abort checks for the rest.
                try:
                    if product.branch:
                        tags = await self.github.fetch_all_tags(client, product.repository)
                        tags = filter_tags_for_branch(tags, product.branch)
                    else:
                        tags = await self.github.fetch_tags(client, product.repository)
                except httpx.HTTPError:
                    logger.exception("Failed to fetch tags for %s", key)
                    continue

                if not tags:
                    logger.warning("No tags found for %s", key)
                    continue

                latest_tag = tags[0].name
                stored = self.store.get_product(key)

                if stored is None:
                    # Persist immediately so a later mail failure never causes
                    # duplicate events for the same tag on the next cycle.
                    self.store.upsert_product(product.name, key, latest_tag)
                    if self.config.monitor.notify_on_first_seen:
                        event_ids.append(self.store.record_event(key, None, latest_tag))
                        updates.append(VersionUpdate(product.name, key, None, latest_tag))
                    logger.info("Initialized %s at %s", key, latest_tag)
                elif stored.latest_tag != latest_tag:
                    self.store.upsert_product(product.name, key, latest_tag)
                    event_ids.append(
                        self.store.record_event(key, stored.latest_tag, latest_tag)
                    )
                    updates.append(
                        VersionUpdate(product.name, key, stored.latest_tag, latest_tag)
                    )
                    logger.info(
                        "Detected update for %s: %s -> %s",
                        key,
                        stored.latest_tag,
                        latest_tag,
                    )
                else:
                    self.store.upsert_product(product.name, key, latest_tag)
                    logger.info("%s is unchanged at %s", key, latest_tag)

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
