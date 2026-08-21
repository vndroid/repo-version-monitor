"""Code-hosting providers.

Each provider lives in its own module (github.py, gitlab.py, ...) and exposes
a client with an ``async fetch_tags(client, repository) -> list[Tag]`` method.
To add a new provider, create the module and register its name in
SUPPORTED_PROVIDERS, then wire it up in monitor.VersionMonitor.
"""

from repo_version_monitor.providers.base import (
    PREFIX_SEPARATOR,
    SUFFIX_SEPARATOR,
    TAG_PART_SEPARATOR,
    Tag,
    TagProvider,
    describe_tag_pattern,
    filter_tags_for_branch,
    normalize_tag_name,
    pick_latest_version_tag,
    plain_version_name,
    split_prefixes,
    split_suffixes,
    strip_tag_prefix,
)
from repo_version_monitor.providers.github import GitHubClient, GitHubGraphQLError
from repo_version_monitor.providers.gitlab import GitLabClient

DEFAULT_PROVIDER = "github"
SUPPORTED_PROVIDERS = ("github", "gitlab")

# Public hostnames that identify a provider on their own. Self-hosted
# instances use arbitrary domains, so those need an explicit --provider.
PROVIDER_HOSTS = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
}

# Where each provider lives when no host is given.
DEFAULT_PROVIDER_HOSTS = {
    "github": "github.com",
    "gitlab": "gitlab.com",
}

__all__ = [
    "DEFAULT_PROVIDER",
    "DEFAULT_PROVIDER_HOSTS",
    "PROVIDER_HOSTS",
    "SUPPORTED_PROVIDERS",
    "GitHubClient",
    "GitHubGraphQLError",
    "GitLabClient",
    "PREFIX_SEPARATOR",
    "SUFFIX_SEPARATOR",
    "TAG_PART_SEPARATOR",
    "Tag",
    "TagProvider",
    "describe_tag_pattern",
    "filter_tags_for_branch",
    "normalize_tag_name",
    "pick_latest_version_tag",
    "plain_version_name",
    "split_prefixes",
    "split_suffixes",
    "strip_tag_prefix",
]
