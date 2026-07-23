from repo_version_monitor.mailgun import VersionUpdate, _body, _subject


def test_single_update_subject() -> None:
    update = VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0")

    assert _subject([update]) == "httpx has a new tag: 0.28.0"


def test_body_contains_repository_and_versions() -> None:
    update = VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0")

    body = _body([update])

    assert "https://github.com/encode/httpx" in body
    assert "Previous: 0.27.0" in body
    assert "Current:  0.28.0" in body

