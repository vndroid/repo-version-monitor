"""Code-hosting providers.

Each provider lives in its own module (github.py, gitlab.py, ...) and exposes
a client with an ``async fetch_tags(client, repository) -> list[Tag]`` method.
To add a new provider, create the module and register its name in
SUPPORTED_PROVIDERS, then wire it up in monitor.VersionMonitor.
"""

from repo_version_monitor.providers.base import (
    Tag,
    TagProvider,
    filter_tags_for_branch,
    normalize_tag_name,
    pick_latest_version_tag,
)
from repo_version_monitor.providers.github import GitHubClient, GitHubGraphQLError
from repo_version_monitor.providers.gitlab import GitLabClient

DEFAULT_PROVIDER = "github"
SUPPORTED_PROVIDERS = ("github", "gitlab")

__all__ = [
    "DEFAULT_PROVIDER",
    "SUPPORTED_PROVIDERS",
    "GitHubClient",
    "GitHubGraphQLError",
    "GitLabClient",
    "Tag",
    "TagProvider",
    "filter_tags_for_branch",
    "normalize_tag_name",
    "pick_latest_version_tag",
]
