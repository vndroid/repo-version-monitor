from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class StoredProduct:
    name: str
    repository: str
    latest_tag: str | None


@dataclass(frozen=True)
class UnnotifiedEvent:
    event_id: int
    product_name: str
    repository: str
    old_tag: str | None
    new_tag: str


class VersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    repository TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    latest_tag TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tag_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repository TEXT NOT NULL,
                    old_tag TEXT,
                    new_tag TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    notified_at TEXT,
                    FOREIGN KEY(repository) REFERENCES products(repository)
                );
                """
            )

    def get_product(self, repository: str) -> StoredProduct | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT name, repository, latest_tag FROM products WHERE repository = ?",
                (repository,),
            ).fetchone()

        if row is None:
            return None
        return StoredProduct(name=row["name"], repository=row["repository"], latest_tag=row["latest_tag"])

    def list_products(self) -> list[StoredProduct]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT name, repository, latest_tag FROM products ORDER BY name, repository"
            ).fetchall()

        return [
            StoredProduct(name=row["name"], repository=row["repository"], latest_tag=row["latest_tag"])
            for row in rows
        ]

    def upsert_product(self, name: str, repository: str, latest_tag: str | None) -> None:
        now = _now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO products (repository, name, latest_tag, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repository) DO UPDATE SET
                    name = excluded.name,
                    latest_tag = excluded.latest_tag,
                    updated_at = excluded.updated_at
                """,
                (repository, name, latest_tag, now),
            )

    def record_event(self, repository: str, old_tag: str | None, new_tag: str) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO tag_events (repository, old_tag, new_tag, detected_at)
                VALUES (?, ?, ?, ?)
                """,
                (repository, old_tag, new_tag, _now()),
            )
            return int(cursor.lastrowid)

    def list_unnotified_events(self) -> list[UnnotifiedEvent]:
        if not self.path.exists():
            return []

        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.repository, e.old_tag, e.new_tag,
                       COALESCE(p.name, e.repository) AS product_name
                FROM tag_events AS e
                LEFT JOIN products AS p ON p.repository = e.repository
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
            )
            for row in rows
        ]

    def mark_notified(self, event_id: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE tag_events SET notified_at = ? WHERE id = ?",
                (_now(), event_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
