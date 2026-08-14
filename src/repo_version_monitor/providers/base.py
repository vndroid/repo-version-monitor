from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import httpx

# v1.2.3 or 1.2.3 style tags; excludes prereleases and non-version tags
# like "flash-with-wbuf-stack" or "with-deprecated-diskstore".
_VERSION_TAG_PATTERN = re.compile(r"^v?(\d+(?:\.\d+)*)$")


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


def pick_latest_version_tag(tags: list[Tag]) -> Tag | None:
    """Pick the highest version-like tag by numeric comparison.

    Tag list order from provider APIs is unreliable, and repositories may
    carry non-version tags; relying on list order picks the wrong tag.
    Returns None when no tag looks like a version.
    """
    best: tuple[tuple[int, ...], Tag] | None = None
    for tag in tags:
        match = _VERSION_TAG_PATTERN.match(tag.name)
        if not match:
            continue
        version = tuple(int(part) for part in match.group(1).split("."))
        if best is None or version > best[0]:
            best = (version, tag)
    return best[1] if best else None


def filter_tags_for_branch(tags: list[Tag], branch: str) -> list[Tag]:
    """Keep tags matching the branch prefix, e.g. branch "v13" matches "v13.*" and "13.*"."""
    prefixes = {branch}
    if branch.startswith("v"):
        prefixes.add(branch[1:])
    else:
        prefixes.add(f"v{branch}")
    return [tag for tag in tags if any(tag.name.startswith(prefix) for prefix in prefixes)]
