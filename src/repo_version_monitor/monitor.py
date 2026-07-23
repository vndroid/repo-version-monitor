from __future__ import annotations

import asyncio
import logging

import httpx

from repo_version_monitor.config import AppConfig
from repo_version_monitor.db import VersionStore
from repo_version_monitor.github import GitHubClient
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
        version_updates: list[tuple[str, str, str]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for product in self.config.products:
                tags = await self.github.fetch_tags(client, product.repository)
                if not tags:
                    logger.warning("No tags found for %s", product.repository)
                    continue

                latest_tag = tags[0].name
                stored = self.store.get_product(product.repository)

                if stored is None:
                    if self.config.monitor.notify_on_first_seen:
                        version_updates.append((product.name, product.repository, latest_tag))
                        event_id = self.store.record_event(product.repository, None, latest_tag)
                        event_ids.append(event_id)
                        updates.append(
                            VersionUpdate(product.name, product.repository, None, latest_tag)
                        )
                    else:
                        self.store.upsert_product(product.name, product.repository, latest_tag)
                    logger.info("Initialized %s at %s", product.repository, latest_tag)
                    continue

                if stored.latest_tag != latest_tag:
                    event_id = self.store.record_event(product.repository, stored.latest_tag, latest_tag)
                    version_updates.append((product.name, product.repository, latest_tag))
                    event_ids.append(event_id)
                    updates.append(
                        VersionUpdate(product.name, product.repository, stored.latest_tag, latest_tag)
                    )
                    logger.info(
                        "Detected update for %s: %s -> %s",
                        product.repository,
                        stored.latest_tag,
                        latest_tag,
                    )
                else:
                    self.store.upsert_product(product.name, product.repository, latest_tag)
                    logger.info("%s is unchanged at %s", product.repository, latest_tag)

            if updates:
                await self.mailgun.send_updates(client, updates)
                for name, repository, latest_tag in version_updates:
                    self.store.upsert_product(name, repository, latest_tag)
                for event_id in event_ids:
                    self.store.mark_notified(event_id)

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
