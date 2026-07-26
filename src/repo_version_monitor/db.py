from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

_PRODUCTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    repository TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    latest_tag TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repository, branch)
);
"""

_TAG_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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


def _label(repository: str, db_branch_value: str) -> str:
    return f"{repository}{db_branch_value}"


@dataclass(frozen=True)
class StoredProduct:
    name: str
    repository: str
    latest_tag: str | None
    branch: str | None = None


@dataclass(frozen=True)
class UnnotifiedEvent:
    event_id: int
    product_name: str
    repository: str
    old_tag: str | None
    new_tag: str
    branch: str | None = None


class VersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            self._migrate_legacy(connection)
            connection.executescript(_PRODUCTS_SCHEMA + _TAG_EVENTS_SCHEMA + _META_SCHEMA)

    def _migrate_legacy(self, connection: sqlite3.Connection) -> None:
        """Rebuild pre-branch tables, splitting legacy 'repo@branch' keys into columns."""
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
                    INSERT INTO products (repository, branch, name, latest_tag, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (repository, _db_branch(branch or None), row["name"], row["latest_tag"], row["updated_at"]),
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
                    INSERT INTO tag_events (id, repository, branch, old_tag, new_tag, detected_at, notified_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        repository,
                        _db_branch(branch or None),
                        row["old_tag"],
                        row["new_tag"],
                        row["detected_at"],
                        row["notified_at"],
                    ),
                )

    def get_product(self, repository: str, branch: str | None = None) -> StoredProduct | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT name, repository, branch, latest_tag FROM products "
                "WHERE repository = ? AND branch = ?",
                (repository, _db_branch(branch)),
            ).fetchone()

        if row is None:
            return None
        return StoredProduct(
            name=row["name"],
            repository=row["repository"],
            latest_tag=row["latest_tag"],
            branch=_config_branch(row["branch"]),
        )

    def list_products(self) -> list[StoredProduct]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT name, repository, branch, latest_tag FROM products "
                "ORDER BY name, repository, branch"
            ).fetchall()

        return [
            StoredProduct(
                name=row["name"],
                repository=row["repository"],
                latest_tag=row["latest_tag"],
                branch=_config_branch(row["branch"]),
            )
            for row in rows
        ]

    def upsert_product(
        self, name: str, repository: str, latest_tag: str | None, branch: str | None = None
    ) -> None:
        now = _now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO products (repository, branch, name, latest_tag, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repository, branch) DO UPDATE SET
                    name = excluded.name,
                    latest_tag = excluded.latest_tag,
                    updated_at = excluded.updated_at
                """,
                (repository, _db_branch(branch), name, latest_tag, now),
            )

    def record_event(
        self, repository: str, old_tag: str | None, new_tag: str, branch: str | None = None
    ) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO tag_events (repository, branch, old_tag, new_tag, detected_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (repository, _db_branch(branch), old_tag, new_tag, _now()),
            )
            return int(cursor.lastrowid)

    def list_unnotified_events(self) -> list[UnnotifiedEvent]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.repository, e.branch, e.old_tag, e.new_tag,
                       COALESCE(p.name, e.repository) AS product_name
                FROM tag_events AS e
                LEFT JOIN products AS p
                    ON p.repository = e.repository AND p.branch = e.branch
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
        self, config_hash: str, valid_products: set[tuple[str, str | None]]
    ) -> list[str]:
        """Record the config hash; on change, drop data for products no longer configured.

        valid_products holds (repository, branch) pairs from the config.
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

    def prune_products_not_in(self, valid_products: set[tuple[str, str | None]]) -> list[str]:
        """Delete products (and their tag events) not configured anymore."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute("SELECT repository, branch FROM products").fetchall()
            removed = [
                (row["repository"], row["branch"])
                for row in rows
                if (row["repository"], _config_branch(row["branch"])) not in valid_products
            ]
            for repository, branch in removed:
                connection.execute(
                    "DELETE FROM tag_events WHERE repository = ? AND branch = ?",
                    (repository, branch),
                )
                connection.execute(
                    "DELETE FROM products WHERE repository = ? AND branch = ?",
                    (repository, branch),
                )
        return [_label(repository, branch) for repository, branch in removed]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
