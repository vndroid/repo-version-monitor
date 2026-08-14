from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import re
import tomllib

from repo_version_monitor.providers import DEFAULT_PROVIDER, SUPPORTED_PROVIDERS

logger = logging.getLogger(__name__)

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# GitLab projects may be nested under subgroups: group/subgroup/project.
_GITLAB_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_PRODUCT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ProductConfig:
    name: str
    repository: str
    branch: str | None = None
    provider: str = DEFAULT_PROVIDER


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None = field(repr=False)
    per_page: int
    token_source: str | None = None


@dataclass(frozen=True)
class GitLabConfig:
    token: str | None = field(repr=False)
    #: External URL of the instance, e.g. https://gitlab.example.com.
    external_url: str
    token_source: str | None = None


@dataclass(frozen=True)
class MailgunConfig:
    enabled: bool
    domain: str
    api_key: str = field(repr=False)
    from_email: str
    to_emails: list[str]
    #: Mailgun API endpoint, e.g. https://api.mailgun.net/v3.
    api_url: str
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
    gitlab: GitLabConfig
    mailgun: MailgunConfig
    monitor: MonitorConfig
    products: list[ProductConfig]
    source_path: Path


def config_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_product_to_config(
    path: Path,
    name: str,
    repository: str,
    branch: str | None = None,
    provider: str = DEFAULT_PROVIDER,
) -> None:
    validate_product_name(name)
    validate_provider(provider)
    validate_repository(repository, provider)
    if branch is not None:
        validate_branch(branch)
    products = load_products(path)
    if any(
        product.provider == provider
        and product.repository == repository
        and product.branch == branch
        for product in products
    ):
        label = f"{repository} (branch {branch})" if branch else repository
        raise ValueError(f"{label} is already configured.")

    with path.open("a", encoding="utf-8") as file:
        file.write("\n" + _product_block(ProductConfig(name, repository, branch, provider)))


# Every setting the config may carry, in the order used by config.example.toml.
# Settings written as "" mean "use the built-in default", the same convention
# products already use for branch: an empty value is never a real value here.
_DEFAULT_SETTINGS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("database", (("path", '""'),)),
    ("github", (("token", '""'), ("per_page", "10"))),
    ("gitlab", (("token", '""'), ("external_url", '""'))),
    (
        "mailgun",
        (
            ("enabled", "true"),
            ("domain", '""'),
            ("api_key", '""'),
            ("from_email", '""'),
            ("to_emails", "[]"),
            ("api_url", '""'),
        ),
    ),
    ("monitor", (("interval_seconds", "3600"), ("notify_on_first_seen", "false"))),
)

DEFAULT_DATABASE_PATH = "versions.sqlite3"
DEFAULT_GITLAB_EXTERNAL_URL = "https://gitlab.com"
DEFAULT_MAILGUN_API_URL = "https://api.mailgun.net/v3"


@dataclass(frozen=True)
class FormatResult:
    #: "created" when the config was copied from the template, else "formatted".
    action: str
    #: Settings added by this run, as "section.key".
    added_settings: list[str] = field(default_factory=list)


def format_config(path: Path, template_path: Path | None = None) -> FormatResult:
    """Ensure the config exists, is complete and is normalized.

    - Missing config: copy it from the template (config.example.toml next to it).
    - Existing config: validate it, add every missing setting with an empty or
      default value, then rewrite the [[products]] blocks so each is separated
      by one blank line and always carries provider and branch keys.
    """
    if not path.exists():
        template = template_path or path.parent / "config.example.toml"
        if not template.exists():
            raise FileNotFoundError(f"{path} is missing and template {template} was not found.")
        path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        return FormatResult("created")

    # Raises on invalid TOML or invalid product entries.
    products = load_products(path)
    text, added = _fill_missing_settings(path.read_text(encoding="utf-8"))
    path.write_text(text, encoding="utf-8")
    _write_products(path, products)
    return FormatResult("formatted", added)


def _fill_missing_settings(text: str) -> tuple[str, list[str]]:
    """Add every known setting the config does not define yet.

    Existing values, key order and comments are left untouched: missing keys are
    appended to their section, missing sections to the end of the file.
    """
    raw = tomllib.loads(text)
    # Never add the new spelling next to an outdated one.
    reject_renamed_settings(raw)
    lines = text.splitlines()
    added: list[str] = []

    for section, settings in _DEFAULT_SETTINGS:
        existing = raw.get(section)
        if not isinstance(existing, dict):
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {value}" for key, value in settings)
            added.extend(f"{section}.{key}" for key, _ in settings)
            continue

        missing = [(key, value) for key, value in settings if key not in existing]
        if not missing:
            continue
        insert_at = _section_end_index(lines, section)
        lines[insert_at:insert_at] = [f"{key} = {value}" for key, value in missing]
        added.extend(f"{section}.{key}" for key, _ in missing)

    return "\n".join(lines) + "\n", added


def _section_end_index(lines: list[str], section: str) -> int:
    """Index just past the last non-blank line of a section's block."""
    header = f"[{section}]"
    start = next(index for index, line in enumerate(lines) if line.strip() == header)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        # Any other table header ends this section, including [[products]].
        if lines[index].lstrip().startswith("["):
            end = index
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


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
        candidates = ", ".join(_product_label(p) for p in matches)
        raise ValueError(
            f"Multiple products named {name!r}: {candidates}. Use --repository to disambiguate."
        )

    target = matches[0]
    if any(
        product.provider == target.provider
        and product.repository == target.repository
        and product.branch == branch
        for product in products
        if product is not target
    ):
        label = f"{target.repository} (branch {branch})" if branch else target.repository
        raise ValueError(f"{label} is already configured.")

    updated = ProductConfig(
        name=target.name,
        repository=target.repository,
        branch=branch,
        provider=target.provider,
    )
    _write_products(path, [updated if product is target else product for product in products])
    return target.branch, updated


_UNSET = object()


def delete_product(
    path: Path,
    name: str | None = None,
    repository: str | None = None,
    branch: object = _UNSET,
    provider: str | None = None,
) -> ProductConfig:
    """Delete exactly one product matching the given selectors.

    Raises when nothing matches or when the selection is ambiguous.
    branch left as _UNSET means "any branch"; None/"" matches entries without one.
    provider=None matches any provider.
    """
    if name is None and repository is None:
        raise ValueError("Specify --name or --repository.")

    products = load_products(path)

    def _matches(product: ProductConfig) -> bool:
        if name is not None and product.name != name:
            return False
        if repository is not None and product.repository != repository:
            return False
        if branch is not _UNSET and product.branch != (branch or None):
            return False
        if provider is not None and product.provider != provider:
            return False
        return True

    matches = [product for product in products if _matches(product)]
    if not matches:
        raise ValueError("No matching product found.")
    if len(matches) > 1:
        candidates = ", ".join(_product_label(p) for p in matches)
        raise ValueError(
            f"Multiple products match: {candidates}. "
            "Use --repository (with --branch or --provider) to select exactly one."
        )

    target = matches[0]
    _write_products(path, [product for product in products if product is not target])
    return target


def _product_label(product: ProductConfig) -> str:
    label = (
        f"{product.repository}@{product.branch}" if product.branch else product.repository
    )
    if product.provider != DEFAULT_PROVIDER:
        label = f"{product.provider}:{label}"
    return label


def _product_block(product: ProductConfig) -> str:
    """Render one [[products]] block; an empty provider means the default one."""
    provider = "" if product.provider == DEFAULT_PROVIDER else product.provider
    return (
        "[[products]]\n"
        f'name = "{_escape_toml_string(product.name)}"\n'
        f'provider = "{provider}"\n'
        f'repository = "{product.repository}"\n'
        f'branch = "{_escape_toml_string(product.branch or "")}"\n'
    )


def _write_products(path: Path, products: list[ProductConfig]) -> None:
    """Rewrite all [[products]] blocks (normalized), keeping other content unchanged."""
    text = path.read_text(encoding="utf-8")
    remainder = _strip_product_blocks(text).rstrip("\n")

    blocks = [_product_block(product) for product in products]

    body = "\n\n".join(block.rstrip("\n") for block in blocks)
    if remainder and body:
        # A comment line right before the first block introduces it; keep them
        # together instead of pushing a blank line in between.
        separator = "\n" if remainder.rsplit("\n", 1)[-1].lstrip().startswith("#") else "\n\n"
        body = remainder + separator + body
    else:
        body = body or remainder
    path.write_text(body + "\n", encoding="utf-8")


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
        # A missing/empty provider means GitHub, keeping old configs valid.
        provider = item.get("provider") or DEFAULT_PROVIDER
        validate_product_name(name)
        validate_provider(provider)
        validate_repository(repository, provider)
        if branch is not None:
            validate_branch(branch)
        products.append(
            ProductConfig(name=name, repository=repository, branch=branch, provider=provider)
        )
    return products


def resolve_database_path(config_path: Path) -> Path:
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    # An empty path means "use the default", the same as leaving the key out.
    db_path = Path(raw.get("database", {}).get("path") or DEFAULT_DATABASE_PATH)
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    return db_path


def read_provider_external_url(config_path: Path, provider: str) -> str | None:
    """Return the external_url configured for a provider, or None when unset.

    Reads the raw TOML only, so it works before the full config is valid
    (e.g. during `add`, when Mailgun settings may still be missing).
    """
    if provider == DEFAULT_PROVIDER:
        return None
    try:
        with config_path.open("rb") as file:
            raw = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return _provider_external_url(raw, provider)


def _provider_external_url(raw: dict, provider: str) -> str | None:
    """Read [<provider>] external_url, rejecting the old base_url spelling."""
    reject_renamed_settings(raw)
    external_url = raw.get(provider, {}).get("external_url")
    return external_url.rstrip("/") if isinstance(external_url, str) and external_url else None


# Settings renamed along the way; the old spelling is rejected with a hint
# instead of being silently ignored, which would fall back to a default.
_RENAMED_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("gitlab", "base_url", "external_url"),
    ("mailgun", "base_url", "api_url"),
)


def reject_renamed_settings(raw: dict) -> None:
    for section, old_key, new_key in _RENAMED_SETTINGS:
        values = raw.get(section)
        if isinstance(values, dict) and old_key in values:
            raise ValueError(
                f"[{section}] {old_key} has been renamed to {new_key}; rename the key, "
                f'e.g. {new_key} = "{values[old_key]}".'
            )


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    reject_renamed_settings(raw)

    github_raw = raw.get("github", {})
    gitlab_raw = raw.get("gitlab", {})
    mailgun_raw = raw.get("mailgun", {})
    database_raw = raw.get("database", {})
    monitor_raw = raw.get("monitor", {})

    db_path = resolve_database_path(path)

    mailgun_enabled = bool(mailgun_raw.get("enabled", True))
    api_key_env = mailgun_raw.get("api_key_env", "MAILGUN_API_KEY")
    # Priority: environment variable first, then the inline mailgun.api_key value.
    env_api_key = os.getenv(api_key_env)
    api_key = env_api_key or mailgun_raw.get("api_key") or None
    api_key_source = f"env {api_key_env}" if env_api_key else ("config mailgun.api_key" if api_key else None)
    if mailgun_enabled and not api_key:
        raise ValueError(f"Mailgun API key is missing. Set {api_key_env} or mailgun.api_key.")

    token_env = github_raw.get("token_env", "GITHUB_TOKEN")
    # Priority: environment variable first, then the inline github.token value.
    env_token = os.getenv(token_env)
    # Trailing `or None`: an empty value means "unset", not an empty token.
    token = env_token or github_raw.get("token") or None
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

    gitlab_token_env = gitlab_raw.get("token_env", "GITLAB_TOKEN")
    # Priority: environment variable first, then the inline gitlab.token value.
    env_gitlab_token = os.getenv(gitlab_token_env)
    gitlab_token = env_gitlab_token or gitlab_raw.get("token") or None
    gitlab_token_source = (
        f"env {gitlab_token_env}"
        if env_gitlab_token
        else ("config gitlab.token" if gitlab_token else None)
    )
    if any(product.provider == "gitlab" for product in products) and not gitlab_token:
        logger.info(
            "No GitLab token found (gitlab.token unset and env %s is empty); "
            "requests are unauthenticated and only public projects are reachable.",
            gitlab_token_env,
        )

    return AppConfig(
        database=DatabaseConfig(path=db_path),
        github=GitHubConfig(
            token=token,
            per_page=min(max(int(github_raw.get("per_page") or 10), 1), 100),
            token_source=token_source,
        ),
        gitlab=GitLabConfig(
            token=gitlab_token,
            external_url=_provider_external_url(raw, "gitlab") or DEFAULT_GITLAB_EXTERNAL_URL,
            token_source=gitlab_token_source,
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
            api_url=(mailgun_raw.get("api_url") or DEFAULT_MAILGUN_API_URL).rstrip("/"),
            api_key_source=api_key_source,
        ),
        monitor=MonitorConfig(
            interval_seconds=int(monitor_raw.get("interval_seconds") or 3600),
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


def validate_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise ValueError(f"Invalid provider {provider!r}: expected one of {supported}.")


def validate_repository(repository: str, provider: str = DEFAULT_PROVIDER) -> None:
    if provider == "gitlab":
        # GitLab allows nested groups: group/subgroup/project.
        if not _GITLAB_REPOSITORY_PATTERN.fullmatch(repository):
            raise ValueError(
                f"Invalid repository {repository!r}: expected 'namespace/project' "
                "(subgroups allowed) with only letters, digits, '-', '_' and '.'."
            )
        return
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(
            f"Invalid repository {repository!r}: expected 'owner/name' with only "
            "letters, digits, '-', '_' and '.'."
        )


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
