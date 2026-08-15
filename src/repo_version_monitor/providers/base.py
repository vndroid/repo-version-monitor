from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import httpx

# v1.2.3 or 1.2.3 style tags; excludes prereleases and non-version tags
# like "flash-with-wbuf-stack" or "with-deprecated-diskstore".
_VERSION_TAG_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")

#: Separator for a product's suffix alternatives: "-ee|-ce".
SUFFIX_SEPARATOR = "|"


def split_suffixes(suffix: str | None) -> list[str]:
    """Suffix alternatives, most preferred first.

    "-ee|-ce" tracks both editions of one repository; when both carry the same
    version, the one listed first wins.
    """
    if not suffix:
        return []
    return [part for part in suffix.split(SUFFIX_SEPARATOR) if part]


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


def parse_version_tag(name: str, suffix: str = "") -> tuple[tuple[int, ...], int] | None:
    """Sort key of a release tag, or None when it is not one.

    Repositories that release several editions tag them with a fixed suffix
    (GitLab: v19.2.2-ee). Configure it per product, several alternatives
    separated by "|": a tag must carry one of them, and what remains after
    stripping it must be a plain version. Prereleases never survive this,
    "v19.2.0-rc44-ee" minus "-ee" leaves "v19.2.0-rc44", which is not a version.

    The key is (version numbers, suffix priority), so the same version tagged
    for two editions resolves to the alternative listed first.
    """
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


def pick_latest_version_tag(tags: list[Tag], suffix: str = "") -> Tag | None:
    """Pick the highest release tag by numeric comparison.

    Tag list order from provider APIs is unreliable, and repositories may
    carry non-version tags; relying on list order picks the wrong tag.
    Only tags carrying one of the ``suffix`` alternatives are considered;
    without one, only plain version tags are. Returns None when no tag looks
    like a release version.
    """
    best: tuple[tuple[tuple[int, ...], int], Tag] | None = None
    for tag in tags:
        parsed = parse_version_tag(tag.name, suffix)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, tag)
    return best[1] if best else None


def filter_tags_for_branch(tags: list[Tag], branch: str) -> list[Tag]:
    """Keep tags matching the branch prefix, e.g. branch "v13" matches "v13.*" and "13.*"."""
    prefixes = {branch}
    if branch.startswith("v"):
        prefixes.add(branch[1:])
    else:
        prefixes.add(f"v{branch}")
    return [tag for tag in tags if any(tag.name.startswith(prefix) for prefix in prefixes)]
