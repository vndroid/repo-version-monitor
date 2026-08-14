import asyncio

import pytest

from repo_version_monitor.providers import (
    GitHubClient,
    GitHubGraphQLError,
    filter_tags_for_branch,
    normalize_tag_name,
    pick_latest_version_tag,
)
from repo_version_monitor.providers.github import GitHubTag


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeGraphQLClient:
    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.calls = 0

    async def post(self, url, **kwargs):
        payload = self.pages[self.calls]
        self.calls += 1
        return _FakeResponse(payload)


def _refs_page(nodes: list[dict], has_next: bool = False, cursor: str | None = None) -> dict:
    return {
        "data": {
            "repository": {
                "refs": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                }
            }
        }
    }


def _tags(*names: str) -> list[GitHubTag]:
    return [GitHubTag(name=name, commit_sha="sha") for name in names]


def test_filter_matches_v_prefix_and_bare_prefix() -> None:
    tags = _tags("v14.0", "v13.2", "13.1", "REL_13_1", "v12.9")

    filtered = filter_tags_for_branch(tags, "v13")

    assert [tag.name for tag in filtered] == ["v13.2", "13.1"]


def test_filter_bare_branch_also_matches_v_tags() -> None:
    tags = _tags("v13.2", "13.1", "v14.0")

    filtered = filter_tags_for_branch(tags, "13")

    assert [tag.name for tag in filtered] == ["v13.2", "13.1"]


def test_filter_preserves_order_newest_first() -> None:
    tags = _tags("v13.3", "v13.2", "v13.1")

    filtered = filter_tags_for_branch(tags, "v13")

    assert filtered[0].name == "v13.3"


def test_pick_skips_non_version_tags() -> None:
    # GitHub's tags API sorts letter-first; these must never win.
    tags = _tags("with-deprecated-diskstore", "flash-with-wbuf-stack", "8.2.1", "7.4.0")

    picked = pick_latest_version_tag(tags)

    assert picked is not None and picked.name == "8.2.1"


def test_pick_compares_numerically_not_lexicographically() -> None:
    tags = _tags("1.9.0", "1.10.0", "1.2.3")

    picked = pick_latest_version_tag(tags)

    assert picked is not None and picked.name == "1.10.0"


def test_pick_handles_v_prefix_and_ignores_prereleases() -> None:
    tags = _tags("v1.6.38", "1.7.0-rc1", "v1.6.9")

    picked = pick_latest_version_tag(tags)

    assert picked is not None and picked.name == "v1.6.38"


def test_normalize_strips_v_prefix_only_before_digits() -> None:
    assert normalize_tag_name("v1.2.3") == "1.2.3"
    assert normalize_tag_name("1.2.3") == "1.2.3"
    assert normalize_tag_name("vault-1.0") == "vault-1.0"
    assert normalize_tag_name("v") == "v"


def test_pick_returns_none_without_version_tags() -> None:
    assert pick_latest_version_tag(_tags("foo", "bar-baz")) is None


def test_graphql_fetch_paginates_and_parses_annotated_tags() -> None:
    github = GitHubClient(token="t")
    client = _FakeGraphQLClient(
        [
            _refs_page(
                [
                    # Lightweight tag: commit oid directly on target.
                    {"name": "8.2.1", "target": {"oid": "sha-light"}},
                    # Annotated tag: commit nested one level deeper.
                    {"name": "8.2.0", "target": {"oid": "tag-obj", "target": {"oid": "sha-annotated"}}},
                ],
                has_next=True,
                cursor="c1",
            ),
            _refs_page([{"name": "8.1.0", "target": {"oid": "sha-old"}}]),
        ]
    )

    tags = asyncio.run(github.fetch_all_tags_graphql(client, "redis/redis"))

    assert client.calls == 2
    assert [tag.name for tag in tags] == ["8.2.1", "8.2.0", "8.1.0"]
    assert tags[1].commit_sha == "sha-annotated"


def test_graphql_requires_token() -> None:
    github = GitHubClient(token=None)

    with pytest.raises(GitHubGraphQLError):
        asyncio.run(github.fetch_all_tags_graphql(_FakeGraphQLClient([]), "redis/redis"))


def test_graphql_errors_raise() -> None:
    github = GitHubClient(token="t")
    client = _FakeGraphQLClient([{"errors": [{"message": "boom"}]}])

    with pytest.raises(GitHubGraphQLError):
        asyncio.run(github.fetch_all_tags_graphql(client, "redis/redis"))
