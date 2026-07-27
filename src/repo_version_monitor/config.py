from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import re
import tomllib

logger = logging.getLogger(__name__)

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_PRODUCT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ProductConfig:
    name: str
    repository: str
    branch: str | None = None


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None = field(repr=False)
    per_page: int
    token_source: str | None = None


@dataclass(frozen=True)
class MailgunConfig:
    enabled: bool
    domain: str
    api_key: str = field(repr=False)
    from_email: str
    to_emails: list[str]
    base_url: str
    api_key_source: str | None = None


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path


@dataclass(frozen=True)
class MonitorConfig:
    interval_seconds: int
    notify_on_first_seen: bool


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    github: GitHubConfig
    mailgun: MailgunConfig
    monitor: MonitorConfig
    products: list[ProductConfig]
    source_path: Path


def config_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_product_to_config(
    path: Path, name: str, repository: str, branch: str | None = None
) -> None:
    validate_product_name(name)
    validate_repository(repository)
    if branch is not None:
        validate_branch(branch)
    products = load_products(path)
    if any(
        product.repository == repository and product.branch == branch for product in products
    ):
        label = f"{repository} (branch {branch})" if branch else repository
        raise ValueError(f"{label} is already configured.")

    with path.open("a", encoding="utf-8") as file:
        file.write(
            "\n"
            "[[products]]\n"
            f'name = "{_escape_toml_string(name)}"\n'
            f'repository = "{repository}"\n'
            f'branch = "{_escape_toml_string(branch or "")}"\n'
        )


def format_config(path: Path, template_path: Path | None = None) -> str:
    """Ensure the config exists and is normalized. Returns "created" or "formatted".

    - Missing config: copy it from the template (config.example.toml next to it).
    - Existing config: validate it, then rewrite the [[products]] blocks so each
      is separated by one blank line and always carries a branch key ("" if unset).
    """
    if not path.exists():
        template = template_path or path.parent / "config.example.toml"
        if not template.exists():
            raise FileNotFoundError(f"{path} is missing and template {template} was not found.")
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        return "created"

    # Raises on invalid TOML or invalid product entries.
    products = load_products(path)
    _write_products(path, products)
    return "formatted"


def edit_product_branch(
    path: Path, name: str, branch: str | None, repository: str | None = None
) -> tuple[str | None, ProductConfig]:
    """Change the branch of an existing product, selected by name.

    Returns (old_branch, updated_product). branch=None or "" clears the branch.
    """
    branch = branch or None
    if branch is not None:
        validate_branch(branch)

    products = load_products(path)
    matches = [
        product
        for product in products
        if product.name == name and (repository is None or product.repository == repository)
    ]
    if not matches:
        scope = f" with repository {repository}" if repository else ""
        raise ValueError(f"No product named {name!r}{scope}.")
    if len(matches) > 1:
        candidates = ", ".join(
            f"{p.repository}@{p.branch}" if p.branch else p.repository for p in matches
        )
        raise ValueError(
            f"Multiple products named {name!r}: {candidates}. Use --repository to disambiguate."
        )

    target = matches[0]
    if any(
        product.repository == target.repository and product.branch == branch
        for product in products
        if product is not target
    ):
        label = f"{target.repository} (branch {branch})" if branch else target.repository
        raise ValueError(f"{label} is already configured.")

    updated = ProductConfig(name=target.name, repository=target.repository, branch=branch)
    _write_products(path, [updated if product is target else product for product in products])
    return target.branch, updated


def _write_products(path: Path, products: list[ProductConfig]) -> None:
    """Rewrite all [[products]] blocks (normalized), keeping other content unchanged."""
    text = path.read_text(encoding="utf-8")
    remainder = _strip_product_blocks(text).rstrip("\n")

    blocks = []
    for product in products:
        blocks.append(
            "[[products]]\n"
            f'name = "{_escape_toml_string(product.name)}"\n'
            f'repository = "{product.repository}"\n'
            f'branch = "{_escape_toml_string(product.branch or "")}"\n'
        )

    parts = [part for part in (remainder, *blocks) if part]
    path.write_text("\n\n".join(part.rstrip("\n") for part in parts) + "\n", encoding="utf-8")


def _strip_product_blocks(text: str) -> str:
    """Remove every [[products]] block, keeping all other content unchanged."""
    kept: list[str] = []
    in_product = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[[products]]":
            in_product = True
            continue
        if in_product and stripped.startswith("["):
            in_product = False
        if not in_product:
            kept.append(line)
    return "\n".join(kept)


def load_products(path: Path) -> list[ProductConfig]:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    products = []
    for item in raw.get("products", []):
        name = item["name"]
        repository = item["repository"]
        # An empty branch string means "no branch" (written by `format`).
        branch = item.get("branch") or None
        validate_product_name(name)
        validate_repository(repository)
        if branch is not None:
            validate_branch(branch)
        products.append(ProductConfig(name=name, repository=repository, branch=branch))
    return products


def resolve_database_path(config_path: Path) -> Path:
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    db_path = Path(raw.get("database", {}).get("path", "versions.sqlite3"))
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    return db_path


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    github_raw = raw.get("github", {})
    mailgun_raw = raw.get("mailgun", {})
    database_raw = raw.get("database", {})
    monitor_raw = raw.get("monitor", {})

    db_path = resolve_database_path(path)

    mailgun_enabled = bool(mailgun_raw.get("enabled", True))
    api_key_env = mailgun_raw.get("api_key_env", "MAILGUN_API_KEY")
    # Priority: environment variable first, then the inline mailgun.api_key value.
    env_api_key = os.getenv(api_key_env)
    api_key = env_api_key or mailgun_raw.get("api_key")
    api_key_source = f"env {api_key_env}" if env_api_key else ("config mailgun.api_key" if api_key else None)
    if mailgun_enabled and not api_key:
        raise ValueError(f"Mailgun API key is missing. Set {api_key_env} or mailgun.api_key.")

    token_env = github_raw.get("token_env", "GITHUB_TOKEN")
    # Priority: environment variable first, then the inline github.token value.
    env_token = os.getenv(token_env)
    token = env_token or github_raw.get("token")
    token_source = f"env {token_env}" if env_token else ("config github.token" if token else None)
    if token_source:
        logger.debug("GitHub token loaded from %s.", token_source)
    if not token:
        logger.warning(
            "No GitHub token found (github.token unset and env %s is empty); "
            "requests are unauthenticated and limited to 60/hour.",
            token_env,
        )
    if token_env.startswith(("github_pat_", "ghp_")):
        logger.warning(
            "github.token_env looks like a token value, but it must be an env var NAME "
            '(e.g. token_env = "GITHUB_TOKEN"). Use github.token to inline the token itself.'
        )

    products = load_products(path)
    if not products:
        raise ValueError("At least one [[products]] entry is required.")

    return AppConfig(
        database=DatabaseConfig(path=db_path),
        github=GitHubConfig(
            token=token,
            per_page=min(max(int(github_raw.get("per_page", 10)), 1), 100),
            token_source=token_source,
        ),
        mailgun=MailgunConfig(
            enabled=mailgun_enabled,
            # When disabled, no Mailgun settings are required.
            domain=mailgun_raw["domain"] if mailgun_enabled else mailgun_raw.get("domain", ""),
            api_key=api_key or "",
            from_email=(
                mailgun_raw["from_email"] if mailgun_enabled else mailgun_raw.get("from_email", "")
            ),
            to_emails=list(
                mailgun_raw["to_emails"] if mailgun_enabled else mailgun_raw.get("to_emails", [])
            ),
            base_url=mailgun_raw.get("base_url", "https://api.mailgun.net/v3").rstrip("/"),
            api_key_source=api_key_source,
        ),
        monitor=MonitorConfig(
            interval_seconds=int(monitor_raw.get("interval_seconds", 3600)),
            notify_on_first_seen=bool(monitor_raw.get("notify_on_first_seen", False)),
        ),
        products=products,
        source_path=path,
    )


def validate_product_name(name: str) -> None:
    if not _PRODUCT_NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"Invalid product name {name!r}: only letters (A-Z, a-z), digits, "
            "'-', '_' and '.' are allowed."
        )


def validate_branch(branch: str) -> None:
    if not _PRODUCT_NAME_PATTERN.fullmatch(branch):
        raise ValueError(
            f"Invalid branch {branch!r}: only letters (A-Z, a-z), digits, "
            "'-', '_' and '.' are allowed."
        )


def validate_repository(repository: str) -> None:
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(
            f"Invalid repository {repository!r}: expected 'owner/name' with only "
            "letters, digits, '-', '_' and '.'."
        )


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
