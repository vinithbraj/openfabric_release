"""Persistent exact-command exceptions for overrideable operator shell blocks."""

from __future__ import annotations

import hashlib
import shlex
import sqlite3
import time
from pathlib import Path

from pydantic import BaseModel

from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_operator_command(command: str) -> str:
    """Return the stable exact-match command string used for safe-list hashing."""

    raw = " ".join(str(command or "").strip().split())
    if not raw:
        return ""
    try:
        return shlex.join(shlex.split(raw, comments=False, posix=True))
    except ValueError:
        return raw


def operator_command_hash(command: str) -> str:
    normalized = normalize_operator_command(command)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class OperatorCommandAllowlistEntry(BaseModel):
    entry_id: str
    command: str
    normalized_command: str
    command_hash: str
    block_reason: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    use_count: int = 0


class OperatorCommandAllowlistStore:
    """SQLite-backed exact-command safe list for overrideable shell blocks."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_command_allowlist (
                    entry_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    normalized_command TEXT NOT NULL,
                    command_hash TEXT NOT NULL UNIQUE,
                    block_reason TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    use_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_operator_command_allowlist_enabled
                ON operator_command_allowlist(enabled)
                """
            )
            ensure_store_schema_version(
                connection,
                store_name="operator_command_allowlist",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> OperatorCommandAllowlistEntry:
        payload = dict(row)
        payload["enabled"] = bool(payload.get("enabled"))
        return OperatorCommandAllowlistEntry.model_validate(payload)

    def list(self, *, include_disabled: bool = True) -> list[OperatorCommandAllowlistEntry]:
        query = "SELECT * FROM operator_command_allowlist"
        if not include_disabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY enabled DESC, updated_at DESC, entry_id"
        with self._connect() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def enabled_hashes(self) -> list[str]:
        return [entry.command_hash for entry in self.list(include_disabled=False)]

    def upsert(
        self,
        command: str,
        *,
        block_reason: str = "",
    ) -> OperatorCommandAllowlistEntry:
        normalized = normalize_operator_command(command)
        if not normalized:
            raise ValueError("Command is required.")
        command_hash = operator_command_hash(normalized)
        entry_id = f"cmd-{command_hash[:16]}"
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO operator_command_allowlist (
                    entry_id, command, normalized_command, command_hash, block_reason,
                    enabled, created_at, updated_at, last_used_at, use_count
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, '', 0)
                ON CONFLICT(command_hash) DO UPDATE SET
                    command = excluded.command,
                    normalized_command = excluded.normalized_command,
                    block_reason = excluded.block_reason,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    entry_id,
                    str(command or "").strip(),
                    normalized,
                    command_hash,
                    str(block_reason or "").strip(),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operator_command_allowlist WHERE command_hash = ?",
                (command_hash,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Command allowlist entry was not persisted.")
        return self._row_to_entry(row)

    def delete(self, entry_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM operator_command_allowlist WHERE entry_id = ?",
                (str(entry_id or "").strip(),),
            )
            return cursor.rowcount > 0

    def mark_used(self, command_hash: str) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE operator_command_allowlist
                SET last_used_at = ?, use_count = use_count + 1, updated_at = ?
                WHERE command_hash = ? AND enabled = 1
                """,
                (now, now, str(command_hash or "").strip()),
            )


__all__ = [
    "OperatorCommandAllowlistEntry",
    "OperatorCommandAllowlistStore",
    "normalize_operator_command",
    "operator_command_hash",
]
