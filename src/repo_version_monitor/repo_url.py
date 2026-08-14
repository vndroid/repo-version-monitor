"""Parse the repository and instance arguments accepted by ``add``.

Accepted spellings, all resolving to the same product:

    encode/httpx                              (no host: defaults to github.com)
    github.com/encode/httpx                   (no scheme: https:// is assumed)
    https://github.com/encode/httpx.git
    https://github.com/encode/httpx/releases/tag/0.28.1
    git@gitlab.com:gitlab-org/gitlab.git
    https://gitlab.com/gitlab-org/gitlab/-/tags

The provider is inferred from the host (see providers.PROVIDER_HOSTS). A host
belonging to no known provider is a self-managed instance: it needs an explicit
``--provider`` and becomes the product's ``external_url``, which can also be
given directly with ``--external-url``.
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
    #: Self-managed instance URL; None means the provider's public instance.
    external_url: str | None = None
    #: Host the input pointed at, lowercased; None when no host was given.
    host: str | None = None
    #: True when the provider was derived from a well-known host.
    inferred_from_host: bool = False


def normalize_external_url(value: str) -> str:
    """Normalize an instance URL: default to https:// and drop trailing slashes.

    "jihulab.com" and "https://jihulab.com/" both become "https://jihulab.com".
    """
    url = (value or "").strip()
    if not url:
        raise ValueError("external_url is empty; expected e.g. https://gitlab.example.com.")
    match = _SCHEME_PATTERN.match(url)
    if match:
        scheme = match.group("scheme").lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Unsupported scheme {scheme}:// in {value!r}: use http:// or https://.")
        rest = url[match.end() :]
    else:
        # No scheme given: https by default, http has to be spelled out.
        scheme, rest = "https", url
    host, separator, path = rest.partition("/")
    return f"{scheme}://{host.lower()}{separator}{path}".rstrip("/")


def url_host(url: str) -> str:
    """Host (with port) of a normalized instance URL."""
    return urlsplit(url).netloc.lower()


def parse_repository_input(
    raw: str, provider: str | None = None, external_url: str | None = None
) -> ParsedRepository:
    """Resolve the repository path, provider and instance URL of one product.

    ``provider`` and ``external_url`` are the ``--provider`` / ``--external-url``
    values, or None when unset. Raises ValueError on unusable input, on an
    unknown host without an explicit provider, and on contradicting arguments.
    """
    if provider is not None and provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Invalid provider {provider!r}: expected one of {supported}.")
    if external_url is not None and provider is None:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(
            "--external-url requires --provider: an instance URL alone does not say "
            f"which API to use. Pass one of: {supported}."
        )

    instance_url = normalize_external_url(external_url) if external_url else None
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

    if instance_url is not None and host is not None and host != url_host(instance_url):
        raise ValueError(
            f"Host {host} in the repository argument does not match "
            f"--external-url {instance_url}. Drop one of them."
        )

    if _GITLAB_ROUTE_SEPARATOR in parts:
        parts = parts[: parts.index(_GITLAB_ROUTE_SEPARATOR)]
    if parts and parts[-1].endswith(".git"):
        parts[-1] = parts[-1][: -len(".git")]
    parts = [part for part in parts if part]

    resolved = _resolve_provider(raw, host, instance_url, provider)

    # A pasted URL often carries extra route segments ('/tree/main', '/releases/...').
    # GitHub paths are always owner/name, so anything beyond that is a route.
    if host is not None and resolved == "github" and len(parts) > 2:
        parts = parts[:2]

    if len(parts) < 2:
        raise ValueError(
            f"Invalid repository {raw!r}: expected owner/name"
            + (" (GitLab subgroups allowed)." if resolved == "gitlab" else ".")
        )

    # Imported lazily: config imports this module, so keep the dependency one-way.
    from repo_version_monitor.config import validate_external_url, validate_repository

    repository = "/".join(parts)
    validate_repository(repository, resolved)

    resolved_url = _resolve_external_url(host, instance_url, resolved)
    if resolved_url is not None:
        validate_external_url(resolved_url, resolved)

    return ParsedRepository(
        repository=repository,
        provider=resolved,
        external_url=resolved_url,
        host=host or (url_host(instance_url) if instance_url else None),
        inferred_from_host=host is not None and host in PROVIDER_HOSTS,
    )


def _resolve_external_url(host: str | None, instance_url: str | None, provider: str) -> str | None:
    """The instance to query, or None for the provider's public one."""
    if instance_url is not None:
        # gitlab.com spelled out as --external-url is still the public instance.
        return None if url_host(instance_url) in PROVIDER_HOSTS else instance_url
    if host is None or host in PROVIDER_HOSTS:
        return None
    return normalize_external_url(host)


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


def _resolve_provider(
    raw: str, host: str | None, instance_url: str | None, provider: str | None
) -> str:
    known_host = host or (url_host(instance_url) if instance_url else None)
    inferred = PROVIDER_HOSTS.get(known_host) if known_host else None

    if provider is not None:
        if inferred is not None and inferred != provider:
            raise ValueError(
                f"--provider {provider} conflicts with host {known_host}, which is {inferred}. "
                f"Drop --provider or fix the repository argument {raw!r}."
            )
        return provider

    if known_host is None:
        return DEFAULT_PROVIDER
    if inferred is not None:
        return inferred

    supported = ", ".join(SUPPORTED_PROVIDERS)
    raise ValueError(
        f"Cannot tell which provider hosts {known_host}; it is not a known public instance. "
        f"Pass --provider explicitly (one of: {supported}), "
        f"e.g. --provider=gitlab for a self-managed GitLab."
    )
