"""SQLite schema compatibility helpers for durable runtime stores."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


SCHEMA_META_TABLE = "agent_store_schema_meta"


class StoreSchemaVersionError(RuntimeError):
    """Raised when a local SQLite store is outside the supported schema range."""


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def _user_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name != ?
        ORDER BY name
        """,
        (SCHEMA_META_TABLE,),
    ).fetchall()
    return [
        str(row["name"] if isinstance(row, sqlite3.Row) else row[0])
        for row in rows
    ]


def _has_user_data(connection: sqlite3.Connection) -> bool:
    for table in _user_tables(connection):
        try:
            row = connection.execute(
                f"SELECT 1 FROM {_quote_identifier(table)} LIMIT 1"
            ).fetchone()
        except sqlite3.DatabaseError:
            continue
        if row is not None:
            return True
    return False


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def ensure_store_schema_version(
    connection: sqlite3.Connection,
    *,
    store_name: str,
    current_version: int,
    min_supported_version: int = 1,
    legacy_version: int = 1,
) -> int:
    """Validate and stamp one store schema version in a SQLite database.

    Existing databases created before this metadata table are treated as
    ``legacy_version`` so current saved databases continue to open. Future
    breaking migrations can raise ``min_supported_version`` to fail fast with a
    clear error instead of failing later during reads or writes.
    """

    normalized_store_name = str(store_name or "").strip()
    if not normalized_store_name:
        raise ValueError("store_name is required.")
    current = int(current_version)
    minimum = int(min_supported_version)
    legacy = int(legacy_version)
    if current <= 0:
        raise ValueError("current_version must be greater than zero.")
    if minimum <= 0:
        raise ValueError("min_supported_version must be greater than zero.")
    if minimum > current:
        raise ValueError("min_supported_version cannot exceed current_version.")

    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA_META_TABLE} (
            store_name TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            min_supported_schema_version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    row = connection.execute(
        f"""
        SELECT schema_version, min_supported_schema_version
        FROM {SCHEMA_META_TABLE}
        WHERE store_name = ?
        """,
        (normalized_store_name,),
    ).fetchone()
    if row:
        stored = int(row["schema_version"] if isinstance(row, sqlite3.Row) else row[0])
        stored_minimum = int(
            row["min_supported_schema_version"]
            if isinstance(row, sqlite3.Row)
            else row[1]
        )
    else:
        stored = legacy if _has_user_data(connection) else current
        stored_minimum = minimum
    if stored < minimum:
        raise StoreSchemaVersionError(
            f"{normalized_store_name} schema version {stored} is no longer supported; "
            f"supported range is {minimum}..{current}."
        )
    if stored > current:
        raise StoreSchemaVersionError(
            f"{normalized_store_name} schema version {stored} is newer than this runtime supports; "
            f"supported range is {minimum}..{current}."
        )

    if row is None or stored != current or stored_minimum != minimum:
        connection.execute(
            f"""
            INSERT INTO {SCHEMA_META_TABLE}
                (store_name, schema_version, min_supported_schema_version, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(store_name) DO UPDATE SET
                schema_version = excluded.schema_version,
                min_supported_schema_version = excluded.min_supported_schema_version,
                updated_at = excluded.updated_at
            """,
            (normalized_store_name, current, minimum, utc_now_iso()),
        )
    return stored
