from repo_version_monitor.github import GitHubTag, filter_tags_for_branch


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
