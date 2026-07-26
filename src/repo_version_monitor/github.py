from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class GitHubTag:
    name: str
    commit_sha: str


class GitHubClient:
    def __init__(self, token: str | None = None, per_page: int = 10) -> None:
        self.token = token
        self.per_page = per_page

    async def fetch_tags(self, client: httpx.AsyncClient, repository: str) -> list[GitHubTag]:
        return await self._fetch_page(client, repository, per_page=self.per_page, page=1)

    async def fetch_all_tags(self, client: httpx.AsyncClient, repository: str) -> list[GitHubTag]:
        """Fetch every tag by following pagination (newest first)."""
        tags: list[GitHubTag] = []
        page = 1
        while True:
            batch = await self._fetch_page(client, repository, per_page=100, page=page)
            tags.extend(batch)
            if len(batch) < 100:
                return tags
            page += 1

    async def _fetch_page(
        self, client: httpx.AsyncClient, repository: str, per_page: int, page: int
    ) -> list[GitHubTag]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-version-monitor/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        response = await client.get(
            f"https://api.github.com/repos/{repository}/tags",
            headers=headers,
            params={"per_page": per_page, "page": page},
        )
        response.raise_for_status()
        return [
            GitHubTag(name=item["name"], commit_sha=item["commit"]["sha"])
            for item in response.json()
        ]


def filter_tags_for_branch(tags: list[GitHubTag], branch: str) -> list[GitHubTag]:
    """Keep tags matching the branch prefix, e.g. branch "v13" matches "v13.*" and "13.*"."""
    prefixes = {branch}
    if branch.startswith("v"):
        prefixes.add(branch[1:])
    else:
        prefixes.add(f"v{branch}")
    return [tag for tag in tags if any(tag.name.startswith(prefix) for prefix in prefixes)]

