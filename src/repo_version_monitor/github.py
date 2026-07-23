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
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        response = await client.get(
            f"https://api.github.com/repos/{repository}/tags",
            headers=headers,
            params={"per_page": self.per_page},
        )
        response.raise_for_status()
        return [
            GitHubTag(name=item["name"], commit_sha=item["commit"]["sha"])
            for item in response.json()
        ]

