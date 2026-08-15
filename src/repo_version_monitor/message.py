"""The notification email itself, shared by every delivery channel."""

from __future__ import annotations

from dataclasses import dataclass

TEST_SUBJECT = "repo-version-monitor test email"
TEST_BODY = (
    "This is a test email from repo-version-monitor.\n"
    "If you received it, your mail configuration works."
)


@dataclass(frozen=True)
class VersionUpdate:
    product_name: str
    repository: str
    old_tag: str | None
    new_tag: str


def clean(value: str) -> str:
    """Strip control characters (CR/LF etc.) from remote-supplied strings.

    Tag names come from repositories we do not control; without this a tag
    like "v1.0\\r\\nBcc: x@evil.com" could inject email headers.
    """
    return "".join(character for character in value if character.isprintable())


def subject_for(updates: list[VersionUpdate]) -> str:
    if len(updates) == 1:
        update = updates[0]
        return f"{clean(update.product_name)} has a new tag: {clean(update.new_tag)}"
    return f"{len(updates)} repositories have new tags"


def body_for(updates: list[VersionUpdate]) -> str:
    lines = ["Detected GitHub tag updates:", ""]
    for update in updates:
        previous = clean(update.old_tag) if update.old_tag else "(first seen)"
        lines.extend(
            [
                f"- {clean(update.product_name)}",
                f"  Repository: https://github.com/{update.repository}",
                f"  Previous: {previous}",
                f"  Current:  {clean(update.new_tag)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()
