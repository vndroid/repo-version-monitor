from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import httpx

# v1.2.3 or 1.2.3 style tags; excludes prereleases and non-version tags
# like "flash-with-wbuf-stack" or "with-deprecated-diskstore".
_VERSION_TAG_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")

#: Separator for a product's suffix or prefix alternatives: "-ee|-ce".
TAG_PART_SEPARATOR = "|"
SUFFIX_SEPARATOR = TAG_PART_SEPARATOR
PREFIX_SEPARATOR = TAG_PART_SEPARATOR


def _split_alternatives(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split(TAG_PART_SEPARATOR) if part]


def split_suffixes(suffix: str | None) -> list[str]:
    """Suffix alternatives, most preferred first.

    "-ee|-ce" tracks both editions of one repository; when both carry the same
    version, the one listed first wins.
    """
    return _split_alternatives(suffix)


def split_prefixes(prefix: str | None) -> list[str]:
    """Prefix alternatives, most preferred first.

    "release-|rel-" tracks a repository that renamed its tag prefix; when both
    spellings carry the same version, the one listed first wins.
    """
    return _split_alternatives(prefix)


def describe_tag_pattern(suffix: str | None = "", prefix: str | None = "") -> str:
    """Short shape of the tags a product tracks, "" when they are plain versions.

    "-ee" for a suffix alone, "release-*" for a prefix alone and
    "release-*-ee" for both, with "*" standing in for the version numbers.
    """
    if prefix:
        return f"{prefix}*{suffix or ''}"
    return suffix or ""


def strip_tag_prefix(name: str, prefix: str | None = "") -> str:
    """Drop the first matching prefix alternative from a tag name.

    "release-1.1.1" with prefix "release-" becomes "1.1.1"; a tag that carries
    none of the alternatives is returned unchanged.
    """
    for alternative in split_prefixes(prefix):
        if name.startswith(alternative):
            return name[len(alternative) :]
    return name


@dataclass(frozen=True)
class Tag:
    """A repository tag, provider-agnostic."""

    name: str
    commit_sha: str


class TagProvider(Protocol):
    """A code-hosting provider able to list every tag of a repository.

    Implementations should return tags newest first when the underlying API
    supports it; callers still pick the highest version tag themselves.
    """

    async def fetch_tags(self, client: httpx.AsyncClient, repository: str) -> list[Tag]: ...


def normalize_tag_name(name: str) -> str:
    """Strip a leading 'v' from version tags so 'v1.2.3' and '1.2.3' compare equal.

    Only strips when 'v' is followed by a digit, leaving tags like 'vault-1.0' intact.
    """
    if len(name) > 1 and name[0] == "v" and name[1].isdigit():
        return name[1:]
    return name


def parse_version_tag(
    name: str, suffix: str = "", prefix: str = ""
) -> tuple[tuple[int, ...], int, int] | None:
    """Sort key of a release tag, or None when it is not one.

    Repositories that release several editions tag them with a fixed suffix
    (GitLab: v19.2.2-ee); others put a fixed prefix in front of the version
    instead (release-1.1.1). Configure either per product, several alternatives
    separated by "|": a tag must carry one of them, and what remains after
    stripping it must be a plain version. Prereleases never survive this,
    "v19.2.0-rc44-ee" minus "-ee" leaves "v19.2.0-rc44", which is not a version.

    The key is (version numbers, suffix priority, prefix priority), so the same
    version tagged twice resolves to the alternative listed first.
    """
    prefixes = split_prefixes(prefix)
    if not prefixes:
        parsed = _parse_suffixed(name, suffix)
        return None if parsed is None else (*parsed, 0)

    for index, alternative in enumerate(prefixes):
        if not name.startswith(alternative):
            continue
        parsed = _parse_suffixed(name[len(alternative) :], suffix)
        if parsed is not None:
            return (*parsed, len(prefixes) - index)
    return None


def _parse_suffixed(name: str, suffix: str) -> tuple[tuple[int, ...], int] | None:
    """(version numbers, suffix priority) of a tag whose prefix is already gone."""
    alternatives = split_suffixes(suffix)
    if not alternatives:
        version = _plain_version(name)
        return None if version is None else (version, 0)

    for index, alternative in enumerate(alternatives):
        if not name.endswith(alternative):
            continue
        version = _plain_version(name[: -len(alternative)])
        if version is not None:
            return version, len(alternatives) - index
    return None


def _plain_version(name: str) -> tuple[int, ...] | None:
    match = _VERSION_TAG_PATTERN.match(name)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def pick_latest_version_tag(tags: list[Tag], suffix: str = "", prefix: str = "") -> Tag | None:
    """Pick the highest release tag by numeric comparison.

    Tag list order from provider APIs is unreliable, and repositories may
    carry non-version tags; relying on list order picks the wrong tag.
    Only tags carrying one of the ``prefix`` alternatives and one of the
    ``suffix`` alternatives are considered; without either, only plain version
    tags are. Returns None when no tag looks like a release version.
    """
    best: tuple[tuple[tuple[int, ...], int, int], Tag] | None = None
    for tag in tags:
        parsed = parse_version_tag(tag.name, suffix, prefix)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, tag)
    return best[1] if best else None


def filter_tags_for_branch(tags: list[Tag], branch: str, prefix: str = "") -> list[Tag]:
    """Keep tags matching the branch prefix, e.g. branch "v13" matches "v13.*" and "13.*".

    A configured tag prefix is dropped first, so branch "v13" still matches
    "release-13.1.0" when the product sets prefix = "release-".
    """
    # What a tag has to start with once the configured tag prefix is gone.
    branch_starts = {branch}
    if branch.startswith("v"):
        branch_starts.add(branch[1:])
    else:
        branch_starts.add(f"v{branch}")

    def matches(name: str) -> bool:
        candidates = {name, strip_tag_prefix(name, prefix)}
        return any(
            candidate.startswith(start) for candidate in candidates for start in branch_starts
        )

    return [tag for tag in tags if matches(tag.name)]
