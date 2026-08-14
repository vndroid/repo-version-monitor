"""Parse the repository argument accepted by ``add``.

Accepted spellings, all resolving to the same product:

    encode/httpx                              (no host: defaults to github.com)
    github.com/encode/httpx                   (no scheme: https:// is assumed)
    https://github.com/encode/httpx.git
    https://github.com/encode/httpx/releases/tag/0.28.1
    git@gitlab.com:gitlab-org/gitlab.git
    https://gitlab.com/gitlab-org/gitlab/-/tags

The provider is inferred from the host (see providers.PROVIDER_HOSTS). Hosts
that belong to no known provider — i.e. self-managed instances — require an
explicit ``--provider``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from repo_version_monitor.providers import (
    DEFAULT_PROVIDER,
    DEFAULT_PROVIDER_HOSTS,
    PROVIDER_HOSTS,
    SUPPORTED_PROVIDERS,
)

_SCHEME_PATTERN = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")
# scp-like git remote: [user@]host:path (path must not look like a port).
_SCP_PATTERN = re.compile(r"^(?:[^@/]+@)?(?P<host>[A-Za-z0-9_.-]+):(?P<path>(?!\d+/)[^/].*)$")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?::\d{1,5})?$")
# GitLab web URLs put page routes behind a '/-/' separator.
_GITLAB_ROUTE_SEPARATOR = "-"


@dataclass(frozen=True)
class ParsedRepository:
    repository: str
    provider: str
    #: Host taken from the input, lowercased; None when the input had no host.
    host: str | None = None
    #: True when the provider was derived from a well-known host.
    inferred_from_host: bool = False


def parse_repository_input(raw: str, provider: str | None = None) -> ParsedRepository:
    """Split a repository argument into (repository path, provider, host).

    ``provider`` is the explicit ``--provider`` value, or None when unset.
    Raises ValueError on unusable input, on an unknown host without an
    explicit provider, and when the host contradicts ``--provider``.
    """
    if provider is not None and provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Invalid provider {provider!r}: expected one of {supported}.")

    value = (raw or "").strip()
    if not value:
        raise ValueError("Repository is required, e.g. owner/name or github.com/owner/name.")

    value = _strip_scheme(value)
    # Drop query/fragment before splitting on '/'.
    value = value.split("?", 1)[0].split("#", 1)[0]

    parts = [part for part in value.split("/") if part]
    if not parts:
        raise ValueError(f"Invalid repository {raw!r}: no repository path found.")

    host = None
    if _is_host_segment(parts[0], len(parts)):
        host = parts[0].lower()
        parts = parts[1:]

    if _GITLAB_ROUTE_SEPARATOR in parts:
        parts = parts[: parts.index(_GITLAB_ROUTE_SEPARATOR)]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][: -len(".git")]
    parts = [part for part in parts if part]

    resolved = _resolve_provider(raw, host, provider)

    # A pasted URL often carries extra route segments ('/tree/main', '/releases/...').
    # GitHub paths are always owner/name, so anything beyond that is a route.
    if host is not None and resolved == "github" and len(parts) > 2:
        parts = parts[:2]

    if len(parts) < 2:
        raise ValueError(
            f"Invalid repository {raw!r}: expected owner/name"
            + (" (GitLab subgroups allowed)." if resolved == "gitlab" else ".")
        )

    # Imported lazily: config imports the provider registry, and keeping the
    # dependency one-way here avoids any import order surprises.
    from repo_version_monitor.config import validate_repository

    repository = "/".join(parts)
    validate_repository(repository, resolved)

    return ParsedRepository(
        repository=repository,
        provider=resolved,
        host=host,
        inferred_from_host=host is not None and host in PROVIDER_HOSTS,
    )


def host_mismatch_warning(parsed: ParsedRepository, configured_base_url: str | None) -> str | None:
    """Warn when the host in the input is not the instance that will be queried.

    The provider clients are configured globally, so a self-managed host only
    works after ``[gitlab] base_url`` points at it.
    """
    if parsed.host is None:
        return None

    if parsed.provider == "github":
        # The GitHub client always talks to github.com / api.github.com.
        if parsed.host not in ("github.com", "www.github.com"):
            return (
                f"Host {parsed.host} is not github.com; the github provider always queries "
                "github.com. GitHub Enterprise instances are not supported yet."
            )
        return None

    default_host = DEFAULT_PROVIDER_HOSTS[parsed.provider]
    configured_host = _hostname(configured_base_url) or default_host
    if parsed.host != configured_host:
        return (
            f"Host {parsed.host} does not match the configured "
            f"{parsed.provider}.base_url ({configured_host}); tags would be fetched from "
            f"{configured_host}. Set [{parsed.provider}] base_url = "
            f'"https://{parsed.host}" in the config.'
        )
    return None


def _strip_scheme(value: str) -> str:
    match = _SCHEME_PATTERN.match(value)
    if match:
        scheme = match.group("scheme").lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Unsupported scheme {scheme}://: use http:// or https://.")
        value = value[match.end() :]
    else:
        scp = _SCP_PATTERN.match(value)
        if scp:
            value = f"{scp.group('host')}/{scp.group('path')}"

    # Drop any userinfo left over from https://user@host/path.
    first, separator, rest = value.partition("/")
    if "@" in first:
        first = first.rsplit("@", 1)[-1]
    return first + separator + rest


def _is_host_segment(segment: str, total_parts: int) -> bool:
    """Decide whether the first path segment is a host rather than an owner.

    Owner names may contain dots too, so a dotted segment only counts as a host
    when the rest still holds a full owner/name pair (or when it is a host we know).
    """
    lowered = segment.lower()
    if lowered in PROVIDER_HOSTS or lowered in DEFAULT_PROVIDER_HOSTS.values():
        return True
    if lowered.startswith("localhost") and total_parts >= 3:
        return True
    return bool(_HOST_PATTERN.fullmatch(lowered)) and total_parts >= 3


def _resolve_provider(raw: str, host: str | None, provider: str | None) -> str:
    inferred = PROVIDER_HOSTS.get(host) if host else None

    if provider is not None:
        if inferred is not None and inferred != provider:
            raise ValueError(
                f"--provider {provider} conflicts with host {host}, which is {inferred}. "
                f"Drop --provider or fix the repository argument {raw!r}."
            )
        return provider

    if host is None:
        return DEFAULT_PROVIDER
    if inferred is not None:
        return inferred

    supported = ", ".join(SUPPORTED_PROVIDERS)
    raise ValueError(
        f"Cannot tell which provider hosts {host}; it is not a known public instance. "
        f"Pass --provider explicitly (one of: {supported}), "
        f"e.g. --provider=gitlab for a self-managed GitLab."
    )


def _hostname(base_url: str | None) -> str | None:
    if not base_url:
        return None
    value = base_url.strip()
    if not _SCHEME_PATTERN.match(value):
        value = "https://" + value
    return urlsplit(value).netloc.lower() or None
