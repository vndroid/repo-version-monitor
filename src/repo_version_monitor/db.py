from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

_DEFAULT_PROVIDER = "github"

_PRODUCTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    provider TEXT NOT NULL DEFAULT 'github',
    repository TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    latest_tag TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, repository, branch)
);
"""

_TAG_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'github',
    repository TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    old_tag TEXT,
    new_tag TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    notified_at TEXT
);
"""

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _db_branch(branch: str | None) -> str:
    """Branch as stored in the DB: '@v13' for branch v13, '' for no branch."""
    return f"@{branch}" if branch else ""


def _config_branch(db_value: str) -> str | None:
    """Stored '@v13' back to config-space 'v13'; '' back to None."""
    return db_value.lstrip("@") or None


def _label(provider: str, repository: str, db_branch_value: str) -> str:
    label = f"{repository}{db_branch_value}"
    if provider != _DEFAULT_PROVIDER:
        label = f"{provider}:{label}"
    return label


@dataclass(frozen=True)
class StoredProduct:
    name: str
    repository: str
    latest_tag: str | None
    branch: str | None = None
    provider: str = _DEFAULT_PROVIDER


@dataclass(frozen=True)
class UnnotifiedEvent:
    event_id: int
    product_name: str
    repository: str
    old_tag: str | None
    new_tag: str
    branch: str | None = None
    provider: str = _DEFAULT_PROVIDER


class VersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            self._migrate_legacy(connection)
            connection.executescript(_PRODUCTS_SCHEMA + _TAG_EVENTS_SCHEMA + _META_SCHEMA)

    def _migrate_legacy(self, connection: sqlite3.Connection) -> None:
        """Rebuild older tables: split legacy 'repo@branch' keys, add provider column."""
        products_cols = [row[1] for row in connection.execute("PRAGMA table_info(products)")]
        if products_cols and "branch" not in products_cols:
            rows = connection.execute(
                "SELECT repository, name, latest_tag, updated_at FROM products"
            ).fetchall()
            connection.execute("DROP TABLE products")
            connection.executescript(_PRODUCTS_SCHEMA)
            for row in rows:
                repository, _, branch = row["repository"].partition("@")
                connection.execute(
                    """
                    INSERT INTO products (provider, repository, branch, name, latest_tag, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _DEFAULT_PROVIDER,
                        repository,
                        _db_branch(branch or None),
                        row["name"],
                        row["latest_tag"],
                        row["updated_at"],
                    ),
                )
        elif products_cols and "provider" not in products_cols:
            # Pre-provider schema: everything stored was a GitHub repository.
            rows = connection.execute(
                "SELECT repository, branch, name, latest_tag, updated_at FROM products"
            ).fetchall()
            connection.execute("DROP TABLE products")
            connection.executescript(_PRODUCTS_SCHEMA)
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO products (provider, repository, branch, name, latest_tag, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _DEFAULT_PROVIDER,
                        row["repository"],
                        row["branch"],
                        row["name"],
                        row["latest_tag"],
                        row["updated_at"],
                    ),
                )

        events_cols = [row[1] for row in connection.execute("PRAGMA table_info(tag_events)")]
        if events_cols and "branch" not in events_cols:
            rows = connection.execute(
                "SELECT id, repository, old_tag, new_tag, detected_at, notified_at FROM tag_events"
            ).fetchall()
            connection.execute("DROP TABLE tag_events")
            connection.executescript(_TAG_EVENTS_SCHEMA)
            for row in rows:
                repository, _, branch = row["repository"].partition("@")
                connection.execute(
                    """
                    INSERT INTO tag_events
                        (id, provider, repository, branch, old_tag, new_tag, detected_at, notified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        _DEFAULT_PROVIDER,
                        repository,
                        _db_branch(branch or None),
                        row["old_tag"],
                        row["new_tag"],
                        row["detected_at"],
                        row["notified_at"],
                    ),
                )
        elif events_cols and "provider" not in events_cols:
            connection.execute(
                "ALTER TABLE tag_events ADD COLUMN provider TEXT NOT NULL DEFAULT 'github'"
            )

    def get_product(
        self, repository: str, branch: str | None = None, provider: str = _DEFAULT_PROVIDER
    ) -> StoredProduct | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT name, provider, repository, branch, latest_tag FROM products "
                "WHERE provider = ? AND repository = ? AND branch = ?",
                (provider, repository, _db_branch(branch)),
            ).fetchone()

        if row is None:
            return None
        return StoredProduct(
            name=row["name"],
            repository=row["repository"],
            latest_tag=row["latest_tag"],
            branch=_config_branch(row["branch"]),
            provider=row["provider"],
        )

    def list_products(self) -> list[StoredProduct]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT name, provider, repository, branch, latest_tag FROM products "
                "ORDER BY name, provider, repository, branch"
            ).fetchall()

        return [
            StoredProduct(
                name=row["name"],
                repository=row["repository"],
                latest_tag=row["latest_tag"],
                branch=_config_branch(row["branch"]),
                provider=row["provider"],
            )
            for row in rows
        ]

    def upsert_product(
        self,
        name: str,
        repository: str,
        latest_tag: str | None,
        branch: str | None = None,
        provider: str = _DEFAULT_PROVIDER,
    ) -> None:
        now = _now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO products (provider, repository, branch, name, latest_tag, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, repository, branch) DO UPDATE SET
                    name = excluded.name,
                    latest_tag = excluded.latest_tag,
                    updated_at = excluded.updated_at
                """,
                (provider, repository, _db_branch(branch), name, latest_tag, now),
            )

    def record_event(
        self,
        repository: str,
        old_tag: str | None,
        new_tag: str,
        branch: str | None = None,
        provider: str = _DEFAULT_PROVIDER,
    ) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO tag_events (provider, repository, branch, old_tag, new_tag, detected_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (provider, repository, _db_branch(branch), old_tag, new_tag, _now()),
            )
            return int(cursor.lastrowid)

    def list_unnotified_events(self) -> list[UnnotifiedEvent]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.provider, e.repository, e.branch, e.old_tag, e.new_tag,
                       COALESCE(p.name, e.repository) AS product_name
                FROM tag_events AS e
                LEFT JOIN products AS p
                    ON p.provider = e.provider
                    AND p.repository = e.repository
                    AND p.branch = e.branch
                WHERE e.notified_at IS NULL
                ORDER BY e.id
                """
            ).fetchall()

        return [
            UnnotifiedEvent(
                event_id=row["id"],
                product_name=row["product_name"],
                repository=row["repository"],
                old_tag=row["old_tag"],
                new_tag=row["new_tag"],
                branch=_config_branch(row["branch"]),
                provider=row["provider"],
            )
            for row in rows
        ]

    def mark_notified(self, event_id: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE tag_events SET notified_at = ? WHERE id = ?",
                (_now(), event_id),
            )

    def sync_config_hash(
        self, config_hash: str, valid_products: set[tuple[str, str, str | None]]
    ) -> list[str]:
        """Record the config hash; on change, drop data for products no longer configured.

        valid_products holds (provider, repository, branch) triples from the config.
        Returns human-readable labels of the removed entries.
        """
        self.initialize()
        stored = self.get_meta("config_hash")
        if stored == config_hash:
            return []

        removed = [] if stored is None else self.prune_products_not_in(valid_products)
        self.set_meta("config_hash", config_hash)
        return removed

    def get_meta(self, key: str) -> str | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as connection:
            try:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = ?", (key,)
                ).fetchone()
            except sqlite3.OperationalError:  # meta table absent in pre-existing DBs
                return None
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def prune_products_not_in(
        self, valid_products: set[tuple[str, str, str | None]]
    ) -> list[str]:
        """Delete products (and their tag events) not configured anymore.

        valid_products holds (provider, repository, branch) triples.
        """
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT provider, repository, branch FROM products"
            ).fetchall()
            removed = [
                (row["provider"], row["repository"], row["branch"])
                for row in rows
                if (row["provider"], row["repository"], _config_branch(row["branch"]))
                not in valid_products
            ]
            for provider, repository, branch in removed:
                connection.execute(
                    "DELETE FROM tag_events "
                    "WHERE provider = ? AND repository = ? AND branch = ?",
                    (provider, repository, branch),
                )
                connection.execute(
                    "DELETE FROM products "
                    "WHERE provider = ? AND repository = ? AND branch = ?",
                    (provider, repository, branch),
                )
        return [_label(provider, repository, branch) for provider, repository, branch in removed]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
