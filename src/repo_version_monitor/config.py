from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import re
import tomllib

from repo_version_monitor.logs import read_env
from repo_version_monitor.providers import (
    DEFAULT_PROVIDER,
    PREFIX_SEPARATOR,
    SUFFIX_SEPARATOR,
    SUPPORTED_PROVIDERS,
    describe_tag_pattern,
    split_prefixes,
    split_suffixes,
)
from repo_version_monitor.repo_url import normalize_external_url, url_host

logger = logging.getLogger(__name__)

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# GitLab projects may be nested under subgroups: group/subgroup/project.
_GITLAB_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_PRODUCT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
# One tag suffix such as "-ee" or ".Final": whatever trails the version numbers.
# Several alternatives are separated by SUFFIX_SEPARATOR: "-ee|-ce".
_SUFFIX_PATTERN = re.compile(r"[A-Za-z0-9_.+-]+")
# One tag prefix such as "release-" or "release/": whatever leads the version
# numbers. Several alternatives are separated by PREFIX_SEPARATOR.
_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9_.+/-]+")
# http(s)://host[:port][/path] — GitLab may live under a relative URL root.
_EXTERNAL_URL_PATTERN = re.compile(
    r"https?://[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?::\d{1,5})?(?:/[A-Za-z0-9_.~-]+)*"
)


@dataclass(frozen=True)
class ProductConfig:
    name: str
    repository: str
    branch: str | None = None
    provider: str = DEFAULT_PROVIDER
    #: Self-managed instance URL; None means the provider's public instance.
    external_url: str | None = None
    #: Token for that self-managed instance; optional.
    token: str | None = field(default=None, repr=False)
    #: Tag suffix to track, e.g. "-ee"; None tracks plain version tags.
    suffix: str | None = None
    #: Tag prefix to track, e.g. "release-"; None tracks plain version tags.
    prefix: str | None = None


@dataclass(frozen=True)
class GitHubConfig:
    token: str | None = field(repr=False)
    per_page: int
    token_source: str | None = None


@dataclass(frozen=True)
class GitLabConfig:
    """Settings for the public gitlab.com instance.

    Self-managed instances are configured per product (external_url + token).
    """

    token: str | None = field(repr=False)
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
class SmtpConfig:
    """Delivery through a plain SMTP server, as an alternative to Mailgun."""

    enabled: bool = False
    host: str = ""
    port: int = 587
    #: "starttls" (587), "ssl" (465) or "none" (25).
    encryption: str = "starttls"
    username: str = ""
    password: str = field(default="", repr=False)
    from_email: str = ""
    to_emails: list[str] = field(default_factory=list)
    password_source: str | None = None


@dataclass(frozen=True)
class ProxyConfig:
    """Outgoing proxy for every API request (GitHub, GitLab, Mailgun)."""

    enabled: bool = False
    #: "http" or "socks5".
    type: str = "http"
    host: str = ""
    port: int = 8080
    username: str = ""
    password: str = field(default="", repr=False)

    @property
    def url(self) -> str:
        return f"{self.type}://{self.host}:{self.port}"


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
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)

    @property
    def notifications_enabled(self) -> bool:
        return self.mailgun.enabled or self.smtp.enabled


def config_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_product_to_config(
    path: Path,
    name: str,
    repository: str,
    branch: str | None = None,
    provider: str = DEFAULT_PROVIDER,
    external_url: str | None = None,
    token: str | None = None,
    suffix: str | None = None,
    prefix: str | None = None,
) -> None:
    product = ProductConfig(
        name=name,
        repository=repository,
        branch=branch,
        provider=provider,
        external_url=normalize_external_url(external_url) if external_url else None,
        token=token or None,
        suffix=suffix or None,
        prefix=prefix or None,
    )
    validate_product(product)

    if any(_product_key(existing) == _product_key(product) for existing in load_products(path)):
        raise ValueError(f"{_product_label(product)} is already configured.")

    with path.open("a", encoding="utf-8") as file:
        file.write("\n" + _product_block(product))


# Every setting the config may carry, in the order used by config.example.toml.
# Settings written as "" mean "use the built-in default", the same convention
# products already use for branch: an empty value is never a real value here.
_DEFAULT_SETTINGS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("database", (("path", '""'),)),
    ("github", (("token", '""'), ("per_page", "10"))),
    # gitlab.com only; self-managed instances live in their [[products]] block.
    ("gitlab", (("token", '""'),)),
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
    (
        "smtp",
        (
            ("enabled", "false"),
            ("host", '""'),
            ("port", "587"),
            ("encryption", '"starttls"'),
            ("username", '""'),
            ("password", '""'),
            ("from_email", '""'),
            ("to_emails", "[]"),
        ),
    ),
    ("monitor", (("interval_seconds", "3600"), ("notify_on_first_seen", "false"))),
    (
        "proxy",
        (
            ("enabled", "false"),
            ("type", '"http"'),
            ("host", '""'),
            ("port", "8080"),
            ("username", '""'),
            ("password", '""'),
        ),
    ),
)

SUPPORTED_PROXY_TYPES = ("http", "socks5")
SUPPORTED_SMTP_ENCRYPTIONS = ("starttls", "ssl", "none")
DEFAULT_SMTP_PORT = 587

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
    reject_outdated_settings(raw)
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
    updated = ProductConfig(
        name=target.name,
        repository=target.repository,
        branch=branch,
        provider=target.provider,
        external_url=target.external_url,
        token=target.token,
        suffix=target.suffix,
        prefix=target.prefix,
    )
    if any(
        _product_key(product) == _product_key(updated)
        for product in products
        if product is not target
    ):
        raise ValueError(f"{_product_label(updated)} is already configured.")

    _write_products(path, [updated if product is target else product for product in products])
    return target.branch, updated


_UNSET = object()


def delete_product(
    path: Path,
    name: str | None = None,
    repository: str | None = None,
    branch: object = _UNSET,
    provider: str | None = None,
    external_url: str | None = None,
) -> ProductConfig:
    """Delete exactly one product matching the given selectors.

    Raises when nothing matches or when the selection is ambiguous.
    branch left as _UNSET means "any branch"; None/"" matches entries without one.
    provider=None and external_url=None match any value.
    """
    if name is None and repository is None:
        raise ValueError("Specify --name or --repository.")

    products = load_products(path)
    wanted_url = normalize_external_url(external_url) if external_url else None

    def _matches(product: ProductConfig) -> bool:
        if name is not None and product.name != name:
            return False
        if repository is not None and product.repository != repository:
            return False
        if branch is not _UNSET and product.branch != (branch or None):
            return False
        if provider is not None and product.provider != provider:
            return False
        if wanted_url is not None and product.external_url != wanted_url:
            return False
        return True

    matches = [product for product in products if _matches(product)]
    if not matches:
        raise ValueError("No matching product found.")
    if len(matches) > 1:
        candidates = ", ".join(_product_label(p) for p in matches)
        raise ValueError(
            f"Multiple products match: {candidates}. Use --repository "
            "(with --branch, --provider or --external-url) to select exactly one."
        )

    target = matches[0]
    _write_products(path, [product for product in products if product is not target])
    return target


def _product_key(product: ProductConfig) -> tuple[str, str, str, str | None]:
    """What makes a product unique: same path on two instances is two products.

    The tag prefix and suffix are not part of it: they select which tags of the
    same repository to read, so changing them keeps the recorded history.
    """
    return (product.provider, product.external_url or "", product.repository, product.branch)


def _product_label(product: ProductConfig) -> str:
    repository = product.repository
    if product.external_url:
        repository = f"{url_host(product.external_url)}/{repository}"
    label = f"{repository}@{product.branch}" if product.branch else repository
    if product.provider != DEFAULT_PROVIDER:
        label = f"{product.provider}:{label}"
    pattern = describe_tag_pattern(product.suffix, product.prefix)
    return f"{label} ({pattern})" if pattern else label


def _product_block(product: ProductConfig) -> str:
    """Render one [[products]] block; empty values mean "use the default"."""
    provider = "" if product.provider == DEFAULT_PROVIDER else product.provider
    return (
        "[[products]]\n"
        f'name = "{_escape_toml_string(product.name)}"\n'
        f'provider = "{provider}"\n'
        f'external_url = "{product.external_url or ""}"\n'
        f'token = "{_escape_toml_string(product.token or "")}"\n'
        f'repository = "{product.repository}"\n'
        f'branch = "{_escape_toml_string(product.branch or "")}"\n'
        f'prefix = "{_escape_toml_string(product.prefix or "")}"\n'
        f'suffix = "{_escape_toml_string(product.suffix or "")}"\n'
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
    logger.debug("config %s: %d [[products]] entries", path, len(raw.get("products", [])))

    products = []
    for item in raw.get("products", []):
        # Empty strings mean "unset", so `format` can write every key out.
        external_url = item.get("external_url") or None
        product = ProductConfig(
            name=item["name"],
            repository=item["repository"],
            branch=item.get("branch") or None,
            # A missing/empty provider means GitHub, keeping old configs valid.
            provider=item.get("provider") or DEFAULT_PROVIDER,
            external_url=normalize_external_url(external_url) if external_url else None,
            token=item.get("token") or None,
            suffix=item.get("suffix") or None,
            prefix=item.get("prefix") or None,
        )
        validate_product(product)
        logger.debug(
            "config [[products]]: name=%s provider=%s repository=%s branch=%s "
            "external_url=%s prefix=%s suffix=%s token=%s",
            product.name,
            product.provider,
            product.repository,
            product.branch or "(none)",
            product.external_url or "(public instance)",
            product.prefix or "(none)",
            product.suffix or "(plain versions)",
            "set" if product.token else "not set",
        )
        products.append(product)
    return products


def resolve_database_path(config_path: Path) -> Path:
    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    # An empty path means "use the default", the same as leaving the key out.
    db_path = Path(raw.get("database", {}).get("path") or DEFAULT_DATABASE_PATH)
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    logger.debug("config database.path: %s", db_path)
    return db_path


# Settings that moved or were renamed. They are rejected with a hint instead of
# being silently ignored, which would quietly fall back to a default.
_OUTDATED_SETTINGS: tuple[tuple[str, str, str], ...] = (
    (
        "mailgun",
        "base_url",
        'has been renamed to api_url; rename the key, e.g. api_url = "{value}".',
    ),
    (
        "proxy",
        "enable",
        'has been renamed to enabled, matching [mailgun] enabled; rename the key, '
        "e.g. enabled = {value}.",
    ),
    (
        "gitlab",
        "base_url",
        "belongs to the product it applies to now: remove it from [gitlab], which only "
        "configures gitlab.com, and set external_url in the [[products]] block of each "
        'self-managed project (this key held "{value}").',
    ),
    (
        "gitlab",
        "external_url",
        "belongs to the product it applies to now: remove it from [gitlab], which only "
        "configures gitlab.com, and set external_url in the [[products]] block of each "
        'self-managed project (this key held "{value}").',
    ),
)


def reject_outdated_settings(raw: dict) -> None:
    for section, key, hint in _OUTDATED_SETTINGS:
        values = raw.get(section)
        if isinstance(values, dict) and key in values:
            value = values[key]
            # TOML spells booleans in lowercase, so the hint stays copy-pasteable.
            if isinstance(value, bool):
                value = "true" if value else "false"
            raise ValueError(f"[{section}] {key} " + hint.format(value=value))


def load_smtp_config(raw: dict) -> SmtpConfig:
    """Read the [smtp] section; an empty/missing section means no SMTP."""
    smtp_raw = raw.get("smtp", {})
    password_env = smtp_raw.get("password_env", "SMTP_PASSWORD")
    # Priority: environment variable first, then the inline smtp.password value.
    env_password = read_env(password_env)
    password = env_password or smtp_raw.get("password") or ""
    smtp = SmtpConfig(
        enabled=bool(smtp_raw.get("enabled", False)),
        host=(smtp_raw.get("host") or "").strip(),
        # Empty values mean "the default", as everywhere else in the config.
        port=int(smtp_raw.get("port") or DEFAULT_SMTP_PORT),
        encryption=(smtp_raw.get("encryption") or "starttls").lower(),
        username=smtp_raw.get("username") or "",
        password=password,
        from_email=smtp_raw.get("from_email") or "",
        to_emails=list(smtp_raw.get("to_emails") or []),
        password_source=(
            f"env {password_env}"
            if env_password
            else ("config smtp.password" if password else None)
        ),
    )
    validate_smtp(smtp)
    return smtp


def validate_smtp(smtp: SmtpConfig) -> None:
    if not smtp.enabled:
        return
    if smtp.encryption not in SUPPORTED_SMTP_ENCRYPTIONS:
        supported = ", ".join(SUPPORTED_SMTP_ENCRYPTIONS)
        raise ValueError(
            f"Invalid smtp.encryption {smtp.encryption!r}: expected one of {supported}."
        )
    if "://" in smtp.host or "/" in smtp.host:
        raise ValueError(
            f"Invalid smtp.host {smtp.host!r}: use the host only, e.g. smtp.example.com."
        )
    if not 1 <= smtp.port <= 65535:
        raise ValueError(f"Invalid smtp.port {smtp.port}: expected 1-65535.")
    missing = [
        f"smtp.{field_name}"
        for field_name, value in (
            ("host", smtp.host),
            ("from_email", smtp.from_email),
            ("to_emails", smtp.to_emails),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} is required when smtp.enabled is true.")
    if smtp.password and not smtp.username:
        raise ValueError("smtp.password is set but smtp.username is empty.")


def load_proxy_config(raw: dict) -> ProxyConfig:
    """Read the [proxy] section; an empty/missing section means no proxy."""
    proxy_raw = raw.get("proxy", {})
    proxy = ProxyConfig(
        enabled=bool(proxy_raw.get("enabled", False)),
        # Empty values mean "the default", as everywhere else in the config.
        type=(proxy_raw.get("type") or "http").lower(),
        host=(proxy_raw.get("host") or "").strip(),
        port=int(proxy_raw.get("port") or 8080),
        username=proxy_raw.get("username") or "",
        password=proxy_raw.get("password") or "",
    )
    validate_proxy(proxy)
    return proxy


def validate_proxy(proxy: ProxyConfig) -> None:
    if not proxy.enabled:
        return
    if proxy.type not in SUPPORTED_PROXY_TYPES:
        supported = ", ".join(SUPPORTED_PROXY_TYPES)
        raise ValueError(f"Invalid proxy.type {proxy.type!r}: expected one of {supported}.")
    if not proxy.host:
        raise ValueError("proxy.host is required when proxy.enabled is true.")
    # A scheme in host would end up duplicated in the proxy URL.
    if "://" in proxy.host or "/" in proxy.host:
        raise ValueError(
            f"Invalid proxy.host {proxy.host!r}: use the host only, "
            "e.g. 127.0.0.1; the scheme comes from proxy.type."
        )
    if not 1 <= proxy.port <= 65535:
        raise ValueError(f"Invalid proxy.port {proxy.port}: expected 1-65535.")
    if proxy.password and not proxy.username:
        raise ValueError("proxy.password is set but proxy.username is empty.")


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as file:
        raw = tomllib.load(file)

    logger.debug("Reading config %s", path)
    for section, values in raw.items():
        if isinstance(values, dict):
            # Key names only: values may be tokens.
            logger.debug("config [%s]: %s", section, ", ".join(values) or "(empty)")

    reject_outdated_settings(raw)

    github_raw = raw.get("github", {})
    gitlab_raw = raw.get("gitlab", {})
    mailgun_raw = raw.get("mailgun", {})
    database_raw = raw.get("database", {})
    monitor_raw = raw.get("monitor", {})

    db_path = resolve_database_path(path)

    smtp = load_smtp_config(raw)
    mailgun_enabled = bool(mailgun_raw.get("enabled", True))
    if mailgun_enabled and smtp.enabled:
        raise ValueError(
            "mailgun.enabled and smtp.enabled are both true; pick one delivery "
            "channel, otherwise every update would be mailed twice."
        )
    api_key_env = mailgun_raw.get("api_key_env", "MAILGUN_API_KEY")
    # Priority: environment variable first, then the inline mailgun.api_key value.
    env_api_key = read_env(api_key_env)
    api_key = env_api_key or mailgun_raw.get("api_key") or None
    api_key_source = f"env {api_key_env}" if env_api_key else ("config mailgun.api_key" if api_key else None)
    if mailgun_enabled and not api_key:
        raise ValueError(f"Mailgun API key is missing. Set {api_key_env} or mailgun.api_key.")

    token_env = github_raw.get("token_env", "GITHUB_TOKEN")
    # Priority: environment variable first, then the inline github.token value.
    env_token = read_env(token_env)
    # Trailing `or None`: an empty value means "unset", not an empty token.
    token = env_token or github_raw.get("token") or None
    token_source = f"env {token_env}" if env_token else ("config github.token" if token else None)
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
    env_gitlab_token = read_env(gitlab_token_env)
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

    config = AppConfig(
        database=DatabaseConfig(path=db_path),
        github=GitHubConfig(
            token=token,
            per_page=min(max(int(github_raw.get("per_page") or 10), 1), 100),
            token_source=token_source,
        ),
        gitlab=GitLabConfig(token=gitlab_token, token_source=gitlab_token_source),
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
        proxy=load_proxy_config(raw),
        smtp=smtp,
    )
    _log_resolved_settings(config)
    return config


def _source(source: str | None) -> str:
    return f"from {source}" if source else "not set"


def _log_resolved_settings(config: AppConfig) -> None:
    """Report what the config actually resolved to; never logs a secret value."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    logger.debug("config github.token: %s", _source(config.github.token_source))
    logger.debug("config github.per_page: %d", config.github.per_page)
    logger.debug("config gitlab.token: %s", _source(config.gitlab.token_source))
    logger.debug(
        "config monitor: interval_seconds=%d notify_on_first_seen=%s",
        config.monitor.interval_seconds,
        config.monitor.notify_on_first_seen,
    )
    if config.mailgun.enabled:
        logger.debug(
            "config mailgun: domain=%s api_url=%s from=%s to=%s api_key=%s",
            config.mailgun.domain,
            config.mailgun.api_url,
            config.mailgun.from_email,
            ", ".join(config.mailgun.to_emails),
            _source(config.mailgun.api_key_source),
        )
    if config.smtp.enabled:
        logger.debug(
            "config smtp: %s:%d encryption=%s username=%s password=%s from=%s to=%s",
            config.smtp.host,
            config.smtp.port,
            config.smtp.encryption,
            config.smtp.username or "(no authentication)",
            _source(config.smtp.password_source),
            config.smtp.from_email,
            ", ".join(config.smtp.to_emails),
        )
    if not config.notifications_enabled:
        logger.debug("config: mail delivery is disabled, updates are only recorded")
    logger.debug(
        "config proxy: %s",
        f"{config.proxy.url} ({'authenticated' if config.proxy.username else 'anonymous'})"
        if config.proxy.enabled
        else "not set",
    )


def validate_product(product: ProductConfig) -> None:
    """Validate one product entry, including its instance settings."""
    validate_product_name(product.name)
    validate_provider(product.provider)
    validate_repository(product.repository, product.provider)
    if product.branch is not None:
        validate_branch(product.branch)
    if product.external_url is not None:
        validate_external_url(product.external_url, product.provider)
    if product.token and product.provider == DEFAULT_PROVIDER:
        raise ValueError(
            f"Product {product.name!r}: token is only used for self-managed instances; "
            f"the {DEFAULT_PROVIDER} token belongs in the [{DEFAULT_PROVIDER}] section."
        )
    if product.suffix is not None:
        validate_suffix(product.suffix)
    if product.prefix is not None:
        validate_prefix(product.prefix)
    if product.token and product.external_url is None:
        raise ValueError(
            f"Product {product.name!r}: token requires external_url; the token for the "
            f"public instance belongs in the [{product.provider}] section."
        )


def validate_suffix(suffix: str) -> None:
    alternatives = split_suffixes(suffix)
    valid = alternatives and all(
        _SUFFIX_PATTERN.fullmatch(alternative) for alternative in alternatives
    )
    # Rejects empty alternatives too ("-ee|" or "-ee||-ce"), which read as typos.
    if not valid or len(alternatives) != len(suffix.split(SUFFIX_SEPARATOR)):
        raise ValueError(
            f"Invalid suffix {suffix!r}: only letters (A-Z, a-z), digits, "
            f"'-', '_', '.' and '+' are allowed, e.g. \"-ee\"; separate "
            f'alternatives with "{SUFFIX_SEPARATOR}", e.g. "-ee{SUFFIX_SEPARATOR}-ce".'
        )


def validate_prefix(prefix: str) -> None:
    alternatives = split_prefixes(prefix)
    valid = alternatives and all(
        _PREFIX_PATTERN.fullmatch(alternative) for alternative in alternatives
    )
    # Rejects empty alternatives too ("release-|"), which read as typos.
    if not valid or len(alternatives) != len(prefix.split(PREFIX_SEPARATOR)):
        raise ValueError(
            f"Invalid prefix {prefix!r}: only letters (A-Z, a-z), digits, "
            f"'-', '_', '.', '+' and '/' are allowed, e.g. \"release-\"; separate "
            f'alternatives with "{PREFIX_SEPARATOR}", e.g. "release-{PREFIX_SEPARATOR}rel-".'
        )


def validate_external_url(external_url: str, provider: str = DEFAULT_PROVIDER) -> None:
    if provider == DEFAULT_PROVIDER:
        raise ValueError(
            f"external_url is not supported for {DEFAULT_PROVIDER} products: "
            "GitHub Enterprise instances cannot be monitored yet."
        )
    if not _EXTERNAL_URL_PATTERN.fullmatch(external_url):
        raise ValueError(
            f"Invalid external_url {external_url!r}: expected a full instance URL such as "
            "https://gitlab.example.com."
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
