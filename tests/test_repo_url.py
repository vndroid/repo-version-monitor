from __future__ import annotations

import pytest

from repo_version_monitor.repo_url import (
    ParsedRepository,
    host_mismatch_warning,
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

    assert parsed == ParsedRepository("gitlab-org/gitlab", "gitlab", None, False)


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


def test_no_warning_when_host_matches_configured_external_url() -> None:
    parsed = parse_repository_input("gitlab.com/a/b")

    assert host_mismatch_warning(parsed, "https://gitlab.com") is None
    # Unset external_url falls back to the provider default.
    assert host_mismatch_warning(parsed, None) is None


def test_warning_when_self_managed_host_is_not_configured() -> None:
    parsed = parse_repository_input("git.mycorp.com/a/b", provider="gitlab")

    warning = host_mismatch_warning(parsed, "https://gitlab.com")

    assert warning is not None
    assert "git.mycorp.com" in warning
    assert "external_url" in warning


def test_no_warning_when_self_managed_host_is_configured() -> None:
    parsed = parse_repository_input("git.mycorp.com/a/b", provider="gitlab")

    assert host_mismatch_warning(parsed, "https://git.mycorp.com/") is None


def test_warning_for_non_github_com_host() -> None:
    parsed = parse_repository_input("github.mycorp.com/a/b", provider="github")

    warning = host_mismatch_warning(parsed, None)

    assert warning is not None
    assert "github.com" in warning


def test_no_warning_without_host() -> None:
    parsed = parse_repository_input("a/b")

    assert host_mismatch_warning(parsed, "https://gitlab.com") is None
