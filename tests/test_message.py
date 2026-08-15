from repo_version_monitor.message import VersionUpdate, body_for, clean, subject_for


def test_single_update_subject() -> None:
    update = VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0")

    assert subject_for([update]) == "httpx has a new tag: 0.28.0"


def test_body_contains_repository_and_versions() -> None:
    update = VersionUpdate("httpx", "encode/httpx", "0.27.0", "0.28.0")

    body = body_for([update])

    assert "https://github.com/encode/httpx" in body
    assert "Previous: 0.27.0" in body
    assert "Current:  0.28.0" in body


def test_clean_strips_control_characters() -> None:
    assert clean("v1.0\r\nBcc: x@evil.com") == "v1.0Bcc: x@evil.com"


def test_subject_is_header_injection_safe() -> None:
    update = VersionUpdate("httpx", "encode/httpx", None, "v1.0\r\nBcc: x@evil.com")

    subject = subject_for([update])

    assert "\r" not in subject
    assert "\n" not in subject
