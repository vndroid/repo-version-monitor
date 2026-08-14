from __future__ import annotations

import pytest

from repo_version_monitor.repo_url import (
    ParsedRepository,
    normalize_external_url,
    parse_repository_input,
)


@pytest.mark.parametrize(
    ("raw", "repository", "provider", "host"),
    [
        # No host: github.com is assumed.
        ("encode/httpx", "encode/httpx", "github", None),
        # Host without scheme.
        ("github.com/encode/httpx", "encode/httpx", "github", "github.com"),
        ("gitlab.com/gitlab-org/gitlab", "gitlab-org/gitlab", "gitlab", "gitlab.com"),
        # Full URLs.
        ("https://github.com/encode/httpx", "encode/httpx", "github", "github.com"),
        ("http://gitlab.com/gitlab-org/gitlab", "gitlab-org/gitlab", "gitlab", "gitlab.com"),
        # Trailing slash, .git suffix, case in the host.
        ("https://GitHub.com/encode/httpx.git/", "encode/httpx", "github", "github.com"),
        # Pasted web routes are trimmed.
        (
            "https://github.com/encode/httpx/releases/tag/0.28.1",
            "encode/httpx",
            "github",
            "github.com",
        ),
        ("https://gitlab.com/gitlab-org/gitlab/-/tags", "gitlab-org/gitlab", "gitlab", "gitlab.com"),
        # GitLab subgroups survive.
        (
            "gitlab.com/group/subgroup/project",
            "group/subgroup/project",
            "gitlab",
            "gitlab.com",
        ),
        # scp-style git remote.
        ("git@gitlab.com:gitlab-org/gitlab.git", "gitlab-org/gitlab", "gitlab", "gitlab.com"),
        # Query/fragment noise.
        ("https://github.com/encode/httpx?tab=readme#top", "encode/httpx", "github", "github.com"),
    ],
)
def test_parse_without_explicit_provider(
    raw: str, repository: str, provider: str, host: str | None
) -> None:
    parsed = parse_repository_input(raw)

    assert parsed == ParsedRepository(
        repository=repository,
        provider=provider,
        host=host,
        inferred_from_host=host is not None,
    )


def test_owner_with_dot_is_not_treated_as_host() -> None:
    # Only two segments left, so 'foo.bar' must be the owner, not a host.
    parsed = parse_repository_input("foo.bar/baz")

    assert parsed.repository == "foo.bar/baz"
    assert parsed.host is None
    assert parsed.provider == "github"


def test_explicit_provider_for_self_managed_host() -> None:
    parsed = parse_repository_input("git.mycorp.com/group/project", provider="gitlab")

    assert parsed.repository == "group/project"
    assert parsed.provider == "gitlab"
    assert parsed.host == "git.mycorp.com"
    # Unknown host: the provider came from the flag, not from inference.
    assert parsed.inferred_from_host is False


def test_hostless_subgroup_path_needs_gitlab() -> None:
    # Without a host the default provider is github, which has no subgroups.
    with pytest.raises(ValueError, match="Invalid repository"):
        parse_repository_input("group/subgroup/project")

    parsed = parse_repository_input("group/subgroup/project", provider="gitlab")
    assert parsed.repository == "group/subgroup/project"


def test_explicit_provider_without_host() -> None:
    parsed = parse_repository_input("gitlab-org/gitlab", provider="gitlab")

    assert parsed == ParsedRepository("gitlab-org/gitlab", "gitlab")


def test_explicit_provider_matching_host_is_accepted() -> None:
    parsed = parse_repository_input("https://gitlab.com/a/b", provider="gitlab")

    assert parsed.provider == "gitlab"


def test_unknown_host_without_provider_errors() -> None:
    with pytest.raises(ValueError, match="Cannot tell which provider"):
        parse_repository_input("git.mycorp.com/group/project")


def test_provider_conflicting_with_host_errors() -> None:
    with pytest.raises(ValueError, match="conflicts with host gitlab.com"):
        parse_repository_input("gitlab.com/a/b", provider="github")


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "httpx", "https://github.com/encode", "github.com/onlyone"],
)
def test_incomplete_input_errors(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_repository_input(raw)


def test_unsupported_scheme_errors() -> None:
    with pytest.raises(ValueError, match="Unsupported scheme"):
        parse_repository_input("ftp://github.com/a/b")


def test_invalid_characters_still_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid repository"):
        parse_repository_input("github.com/ow ner/name")


def test_invalid_provider_errors() -> None:
    with pytest.raises(ValueError, match="Invalid provider"):
        parse_repository_input("a/b", provider="bitbucket")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("jihulab.com", "https://jihulab.com"),
        ("https://jihulab.com", "https://jihulab.com"),
        ("https://jihulab.com/", "https://jihulab.com"),
        ("HTTPS://JiHuLab.com", "https://jihulab.com"),
        # http has to be spelled out; it is kept as given.
        ("http://git.mycorp.com", "http://git.mycorp.com"),
        ("git.mycorp.com:8443", "https://git.mycorp.com:8443"),
        # GitLab under a relative URL root.
        ("git.mycorp.com/gitlab", "https://git.mycorp.com/gitlab"),
    ],
)
def test_normalize_external_url(value: str, expected: str) -> None:
    assert normalize_external_url(value) == expected


def test_normalize_external_url_rejects_other_schemes() -> None:
    with pytest.raises(ValueError, match="Unsupported scheme"):
        normalize_external_url("ssh://git.mycorp.com")


def test_self_managed_host_becomes_the_external_url() -> None:
    parsed = parse_repository_input("jihulab.com/gitlab-org/gitlab-runner", provider="gitlab")

    assert parsed.external_url == "https://jihulab.com"
    assert parsed.repository == "gitlab-org/gitlab-runner"


def test_explicit_external_url_without_host_in_repository() -> None:
    parsed = parse_repository_input(
        "gitlab-org/gitlab-runner", provider="gitlab", external_url="jihulab.com"
    )

    assert parsed == ParsedRepository(
        repository="gitlab-org/gitlab-runner",
        provider="gitlab",
        external_url="https://jihulab.com",
        host="jihulab.com",
        inferred_from_host=False,
    )


def test_external_url_may_repeat_the_host_of_the_repository() -> None:
    parsed = parse_repository_input(
        "jihulab.com/group/project", provider="gitlab", external_url="http://jihulab.com"
    )

    # Same host, so no conflict; the explicit scheme wins.
    assert parsed.external_url == "http://jihulab.com"


def test_external_url_conflicting_with_repository_host_errors() -> None:
    with pytest.raises(ValueError, match="does not match --external-url"):
        parse_repository_input(
            "jihulab.com/a/b", provider="gitlab", external_url="git.mycorp.com"
        )


def test_external_url_requires_provider() -> None:
    with pytest.raises(ValueError, match="requires --provider"):
        parse_repository_input("a/b", external_url="jihulab.com")


def test_public_instances_have_no_external_url() -> None:
    assert parse_repository_input("encode/httpx").external_url is None
    assert parse_repository_input("gitlab.com/a/b").external_url is None
    # Spelling out the public instance is not a self-managed one either.
    assert (
        parse_repository_input("a/b", provider="gitlab", external_url="gitlab.com").external_url
        is None
    )


def test_external_url_rejected_for_github() -> None:
    with pytest.raises(ValueError, match="not supported for github"):
        parse_repository_input("a/b", provider="github", external_url="github.mycorp.com")
