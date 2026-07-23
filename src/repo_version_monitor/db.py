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


class VersionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
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

    def upsert_product(self, name: str, repository: str, latest_tag: str | None) -> None:
        now = _now()
        with self._connect() as connection:
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
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tag_events (repository, old_tag, new_tag, detected_at)
                VALUES (?, ?, ?, ?)
                """,
                (repository, old_tag, new_tag, _now()),
            )
            return int(cursor.lastrowid)

    def mark_notified(self, event_id: int) -> None:
        with self._connect() as connection:
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

