from __future__ import annotations

import logging

import httpx

from repo_version_monitor.providers.base import Tag

logger = logging.getLogger(__name__)

# Backwards-compatible alias; the shared Tag type lives in providers.base.
GitHubTag = Tag


class GitHubGraphQLError(RuntimeError):
    """GraphQL request failed or returned errors; callers may fall back to REST."""


# Tags ordered by the underlying commit date (newest first) — the REST tags
# endpoint offers no time-based ordering at all.
_TAGS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    refs(refPrefix: "refs/tags/", first: 100, after: $cursor,
         orderBy: {field: TAG_COMMIT_DATE, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        target {
          oid
          ... on Tag { target { oid } }
        }
      }
    }
  }
}
"""


class GitHubClient:
    """Fetch tags from GitHub, preferring GraphQL and falling back to REST."""

    def __init__(self, token: str | None = None, per_page: int = 10) -> None:
        self.token = token
        self.per_page = per_page

    async def fetch_tags(self, client: httpx.AsyncClient, repository: str) -> list[Tag]:
        """Tiered fetch: GraphQL (date-ordered) when a token is configured,
        falling back to the REST tags endpoint otherwise or on failure."""
        if self.token:
            try:
                return await self.fetch_all_tags_graphql(client, repository)
            except (httpx.HTTPError, GitHubGraphQLError):
                logger.warning(
                    "GraphQL tag fetch failed for %s; falling back to REST.",
                    repository,
                    exc_info=True,
                )
        return await self.fetch_all_tags(client, repository)

    async def fetch_all_tags(self, client: httpx.AsyncClient, repository: str) -> list[Tag]:
        """Fetch every tag via REST by following pagination (newest first)."""
        tags: list[Tag] = []
        page = 1
        while True:
            batch = await self._fetch_page(client, repository, per_page=100, page=page)
            tags.extend(batch)
            if len(batch) < 100:
                return tags
            page += 1

    async def fetch_all_tags_graphql(
        self, client: httpx.AsyncClient, repository: str
    ) -> list[Tag]:
        """Fetch every tag via GraphQL, ordered by tag commit date (newest first)."""
        if not self.token:
            raise GitHubGraphQLError("GraphQL requires a token.")

        owner, _, name = repository.partition("/")
        tags: list[Tag] = []
        cursor: str | None = None
        while True:
            response = await client.post(
                "https://api.github.com/graphql",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "repo-version-monitor/0.1.0",
                },
                json={
                    "query": _TAGS_QUERY,
                    "variables": {"owner": owner, "name": name, "cursor": cursor},
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise GitHubGraphQLError(str(payload["errors"]))
            repo_data = payload.get("data", {}).get("repository")
            if repo_data is None:
                raise GitHubGraphQLError(f"Repository {repository} not found via GraphQL.")

            refs = repo_data["refs"]
            for node in refs["nodes"]:
                target = node.get("target") or {}
                # Annotated tags nest the commit one level deeper.
                nested = target.get("target") or {}
                sha = nested.get("oid") or target.get("oid") or ""
                tags.append(Tag(name=node["name"], commit_sha=sha))

            page_info = refs["pageInfo"]
            if not page_info["hasNextPage"]:
                return tags
            cursor = page_info["endCursor"]

    async def _fetch_page(
        self, client: httpx.AsyncClient, repository: str, per_page: int, page: int
    ) -> list[Tag]:
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
            Tag(name=item["name"], commit_sha=item["commit"]["sha"])
            for item in response.json()
        ]
