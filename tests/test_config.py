import pytest

from repo_version_monitor.config import load_config

VALID_CONFIG = """
[mailgun]
domain = "mg.example.com"
api_key = "test-key"
from_email = "monitor@mg.example.com"
to_emails = ["you@example.com"]

[[products]]
name = "httpx"
repository = "{repository}"
"""


def _write_config(tmp_path, repository: str):
    config_path = tmp_path / "config.toml"
    config_path.write_text(VALID_CONFIG.format(repository=repository))
    return config_path


def test_valid_repository_accepted(tmp_path) -> None:
    config = load_config(_write_config(tmp_path, "encode/httpx"))

    assert config.products[0].repository == "encode/httpx"


@pytest.mark.parametrize(
    "repository",
    [
        "encode/httpx/../../user/emails",
        "encode/httpx?per_page=1",
        "encode",
        "a/b/c",
        "owner/repo#frag",
    ],
)
def test_invalid_repository_rejected(tmp_path, repository) -> None:
    with pytest.raises(ValueError, match="Invalid repository"):
        load_config(_write_config(tmp_path, repository))


def test_secrets_hidden_from_repr(tmp_path) -> None:
    config = load_config(_write_config(tmp_path, "encode/httpx"))

    assert "test-key" not in repr(config)
