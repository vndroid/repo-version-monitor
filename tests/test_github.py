import asyncio

import pytest

from repo_version_monitor.providers import (
    GitHubClient,
    GitHubGraphQLError,
    filter_tags_for_branch,
    normalize_tag_name,
    pick_latest_version_tag,
    plain_version_name,
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


def test_pick_with_suffix_ignores_it_when_comparing() -> None:
    # gitlab-org/gitlab only tags editions since the 12.0 CE/EE merge, so the
    # suffix has to be dropped before comparing or v11.2.2 (2018) would win.
    tags = _tags("v19.2.2-ee", "v18.11.9-ee", "v11.2.2", "v11.11.8-ce")

    picked = pick_latest_version_tag(tags, "-ee")

    assert picked is not None and picked.name == "v19.2.2-ee"


def test_pick_with_suffix_still_ignores_prereleases() -> None:
    # "v19.2.0-rc44-ee" minus "-ee" is not a version, so it never wins.
    tags = _tags("v19.2.0-rc44-ee", "v19.1.4-ee", "v11.3.0.pre", "v11.11.0-rc5-ee")

    picked = pick_latest_version_tag(tags, "-ee")

    assert picked is not None and picked.name == "v19.1.4-ee"


def test_pick_with_suffix_skips_tags_without_it() -> None:
    # Only the configured edition is tracked; plain tags belong to another one.
    tags = _tags("v20.0.0", "v19.2.2-ee", "v11.2.2")

    picked = pick_latest_version_tag(tags, "-ee")

    assert picked is not None and picked.name == "v19.2.2-ee"
    # And without a suffix configured, suffixed tags are ignored as before.
    assert pick_latest_version_tag(tags).name == "v20.0.0"


def test_pick_with_several_suffix_alternatives() -> None:
    # Same repository, two editions: both are tracked by one product.
    tags = _tags("v19.2.3-ce", "v19.2.2-ee", "v18.11.9-ee", "v20.0.0-rc1-ce")

    picked = pick_latest_version_tag(tags, "-ee|-ce")

    assert picked is not None and picked.name == "v19.2.3-ce"


def test_pick_prefers_the_first_alternative_on_a_tie() -> None:
    tags = _tags("v19.2.3-ce", "v19.2.3-ee")

    assert pick_latest_version_tag(tags, "-ee|-ce").name == "v19.2.3-ee"
    assert pick_latest_version_tag(tags, "-ce|-ee").name == "v19.2.3-ce"


def test_pick_tries_every_alternative_before_giving_up() -> None:
    # "-e" matches first but leaves "v1.0-e"; "-ee" then leaves a real version.
    assert pick_latest_version_tag(_tags("v1.0-ee"), "-e|-ee").name == "v1.0-ee"


def test_pick_without_suffix_rejects_any_suffix() -> None:
    assert pick_latest_version_tag(_tags("v1.2.3-ubuntu", "v1.2.3-ee")) is None


def test_pick_with_prefix_ignores_it_when_comparing() -> None:
    # Only the "1.1.1" part of "release-1.1.1" is compared, so a text sort
    # ("release-1.9.0" > "release-1.10.0") must not decide the winner.
    tags = _tags("release-1.10.0", "release-1.9.0", "release-1.2.3")

    picked = pick_latest_version_tag(tags, prefix="release-")

    assert picked is not None and picked.name == "release-1.10.0"


def test_pick_with_prefix_skips_tags_without_it() -> None:
    # A prefix selects a tag family; plain tags belong to another one.
    tags = _tags("v20.0.0", "release-1.1.1", "nightly-9.9.9")

    picked = pick_latest_version_tag(tags, prefix="release-")

    assert picked is not None and picked.name == "release-1.1.1"
    # And without a prefix configured, prefixed tags are ignored as before.
    assert pick_latest_version_tag(tags).name == "v20.0.0"


def test_pick_with_prefix_still_ignores_prereleases() -> None:
    # "release-1.2.0-rc1" minus "release-" is not a version, so it never wins.
    tags = _tags("release-1.2.0-rc1", "release-1.1.9", "release-nightly")

    picked = pick_latest_version_tag(tags, prefix="release-")

    assert picked is not None and picked.name == "release-1.1.9"


def test_pick_with_prefix_accepts_a_v_after_it() -> None:
    tags = _tags("release-v1.2.0", "release-v1.1.0")

    assert pick_latest_version_tag(tags, prefix="release-").name == "release-v1.2.0"


def test_pick_with_several_prefix_alternatives() -> None:
    # A repository that renamed its tag prefix is still one product.
    tags = _tags("rel-1.9.0", "release-1.10.0", "release-1.2.3")

    picked = pick_latest_version_tag(tags, prefix="release-|rel-")

    assert picked is not None and picked.name == "release-1.10.0"


def test_pick_prefers_the_first_prefix_alternative_on_a_tie() -> None:
    tags = _tags("rel-1.2.3", "release-1.2.3")

    assert pick_latest_version_tag(tags, prefix="release-|rel-").name == "release-1.2.3"
    assert pick_latest_version_tag(tags, prefix="rel-|release-").name == "rel-1.2.3"


def test_pick_combines_prefix_and_suffix() -> None:
    # Both ends must match: -ce is another edition, and the unprefixed 1.9.0-ee
    # belongs to the tag family this product does not track.
    tags = _tags("release-1.3.0-ee", "release-1.4.0-ce", "release-1.2.0-ee", "1.9.0-ee")

    picked = pick_latest_version_tag(tags, "-ee", "release-")

    assert picked is not None and picked.name == "release-1.3.0-ee"


def test_pick_with_a_slash_prefix() -> None:
    # "release/1.2.3" is a common tag layout; '/' is allowed in a prefix.
    tags = _tags("release/1.2.3", "release/1.10.0")

    assert pick_latest_version_tag(tags, prefix="release/").name == "release/1.10.0"


def test_plain_version_name_strips_the_configured_affixes() -> None:
    assert plain_version_name("release-1.10.0", prefix="release-") == "1.10.0"
    assert plain_version_name("19.2.3-ee", "-ee") == "19.2.3"
    assert plain_version_name("release-1.3.0-ee", "-ee", "release-") == "1.3.0"
    # A leading "v" goes too, so every row of the LATEST column reads alike.
    assert plain_version_name("release-v1.2.0", prefix="release-") == "1.2.0"
    assert plain_version_name("1.2.3") == "1.2.3"


def test_plain_version_name_tries_every_alternative() -> None:
    assert plain_version_name("rel-1.2.3", prefix="release-|rel-") == "1.2.3"
    assert plain_version_name("19.2.3-ce", "-ee|-ce") == "19.2.3"


def test_plain_version_name_keeps_tags_it_cannot_parse() -> None:
    # check falls back to the first listed tag when a repository has no
    # version-like tag at all; showing it unchanged beats mangling it.
    assert plain_version_name("nightly", prefix="release-") == "nightly"
    assert plain_version_name("release-nightly", prefix="release-") == "release-nightly"
    assert plain_version_name("1.2.3-ubuntu", "-ee") == "1.2.3-ubuntu"


def test_filter_for_branch_drops_the_prefix_first() -> None:
    tags = _tags("release-13.2", "release-14.0", "release-13.1", "13.0")

    filtered = filter_tags_for_branch(tags, "v13", "release-")

    assert [tag.name for tag in filtered] == ["release-13.2", "release-13.1", "13.0"]


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
