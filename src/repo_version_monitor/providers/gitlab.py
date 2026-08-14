from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from repo_version_monitor.providers.base import Tag

logger = logging.getLogger(__name__)

_PER_PAGE = 100


class GitLabClient:
    """Fetch tags via the GitLab REST API v4.

    See https://docs.gitlab.com/api/tags/ — tags are sorted by update date
    descending by default (order_by=updated, sort=desc), so pages arrive
    newest first. The project is addressed by its URL-encoded path, which
    also supports nested groups like "group/subgroup/project".
    """

    def __init__(self, token: str | None = None, external_url: str = "https://gitlab.com") -> None:
        self.token = token
        # External URL of the instance; self-managed setups point elsewhere.
        self.external_url = external_url.rstrip("/")

    async def fetch_tags(self, client: httpx.AsyncClient, repository: str) -> list[Tag]:
        return await self.fetch_all_tags(client, repository)

    async def fetch_all_tags(self, client: httpx.AsyncClient, repository: str) -> list[Tag]:
        """Fetch every tag by following pagination (newest first)."""
        headers = {"User-Agent": "repo-version-monitor/0.1.0"}
        if self.token:
            headers["PRIVATE-TOKEN"] = self.token

        project_id = quote(repository, safe="")
        tags: list[Tag] = []
        page = 1
        while True:
            response = await client.get(
                f"{self.external_url}/api/v4/projects/{project_id}/repository/tags",
                headers=headers,
                params={
                    "per_page": _PER_PAGE,
                    "page": page,
                    "order_by": "updated",
                    "sort": "desc",
                },
            )
            response.raise_for_status()
            batch = [
                Tag(
                    name=item["name"],
                    # commit.id is the commit SHA even for annotated tags;
                    # target may point at the tag object instead.
                    commit_sha=(item.get("commit") or {}).get("id")
                    or item.get("target")
                    or "",
                )
                for item in response.json()
            ]
            tags.extend(batch)
            if len(batch) < _PER_PAGE:
                return tags
            page += 1
