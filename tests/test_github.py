from repo_version_monitor.github import (
    GitHubTag,
    filter_tags_for_branch,
    pick_latest_version_tag,
)


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


def test_pick_returns_none_without_version_tags() -> None:
    assert pick_latest_version_tag(_tags("foo", "bar-baz")) is None
