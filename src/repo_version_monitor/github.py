from __future__ import annotations

from dataclasses import dataclass
import re

import httpx

# v1.2.3 or 1.2.3 style tags; excludes prereleases and non-version tags
# like "flash-with-wbuf-stack" or "with-deprecated-diskstore".
_VERSION_TAG_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class GitHubTag:
    name: str
    commit_sha: str


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

    async def fetch_all_tags_graphql(
        self, client: httpx.AsyncClient, repository: str
    ) -> list[GitHubTag]:
        """Fetch every tag via GraphQL, ordered by tag commit date (newest first)."""
        if not self.token:
            raise GitHubGraphQLError("GraphQL requires a token.")

        owner, _, name = repository.partition("/")
        tags: list[GitHubTag] = []
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
                tags.append(GitHubTag(name=node["name"], commit_sha=sha))

            page_info = refs["pageInfo"]
            if not page_info["hasNextPage"]:
                return tags
            cursor = page_info["endCursor"]

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


def normalize_tag_name(name: str) -> str:
    """Strip a leading 'v' from version tags so 'v1.2.3' and '1.2.3' compare equal.

    Only strips when 'v' is followed by a digit, leaving tags like 'vault-1.0' intact.
    """
    if len(name) > 1 and name[0] == "v" and name[1].isdigit():
        return name[1:]
    return name


def pick_latest_version_tag(tags: list[GitHubTag]) -> GitHubTag | None:
    """Pick the highest version-like tag by numeric comparison.

    The GitHub tags API is not sorted by recency, and repositories may carry
    non-version tags; relying on list order picks the wrong tag.
    Returns None when no tag looks like a version.
    """
    best: tuple[tuple[int, ...], GitHubTag] | None = None
    for tag in tags:
        match = _VERSION_TAG_PATTERN.match(tag.name)
        if not match:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, tag)
    return best[1] if best else None


def filter_tags_for_branch(tags: list[GitHubTag], branch: str) -> list[GitHubTag]:
    """Keep tags matching the branch prefix, e.g. branch "v13" matches "v13.*" and "13.*"."""
    prefixes = {branch}
    if branch.startswith("v"):
        prefixes.add(branch[1:])
    else:
        prefixes.add(f"v{branch}")
    return [tag for tag in tags if any(tag.name.startswith(prefix) for prefix in prefixes)]

