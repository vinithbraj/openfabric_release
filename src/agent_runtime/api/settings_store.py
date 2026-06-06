"""SQLite-backed Agent UI settings persistence."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AgentUiSettingsStore:
    """Persist small server-owned Agent UI settings in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_ui_settings (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, key)
                )
                """
            )
            ensure_store_schema_version(
                connection,
                store_name="agent_ui_settings",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        normalized_namespace = str(namespace or "").strip()
        if not normalized_namespace:
            return {}
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT key, value_json
                FROM agent_ui_settings
                WHERE namespace = ?
                ORDER BY key
                """,
                (normalized_namespace,),
            ).fetchall()
        values: dict[str, Any] = {}
        for row in rows:
            try:
                values[str(row["key"])] = json.loads(str(row["value_json"]))
            except Exception:
                continue
        return values

    def set_namespace_values(self, namespace: str, values: dict[str, Any]) -> None:
        normalized_namespace = str(namespace or "").strip()
        if not normalized_namespace or not values:
            return
        now = utc_now_iso()
        rows: list[tuple[str, str, str, str]] = []
        for key, value in values.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            rows.append(
                (
                    normalized_namespace,
                    normalized_key,
                    json.dumps(value, ensure_ascii=True, sort_keys=True, default=str),
                    now,
                )
            )
        if not rows:
            return
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO agent_ui_settings (namespace, key, value_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def replace_namespace_values(self, namespace: str, values: dict[str, Any]) -> None:
        normalized_namespace = str(namespace or "").strip()
        if not normalized_namespace:
            return
        now = utc_now_iso()
        rows: list[tuple[str, str, str, str]] = []
        for key, value in values.items():
            normalized_key = str(key or "").strip()
            if not normalized_key:
                continue
            rows.append(
                (
                    normalized_namespace,
                    normalized_key,
                    json.dumps(value, ensure_ascii=True, sort_keys=True, default=str),
                    now,
                )
            )
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM agent_ui_settings WHERE namespace = ?",
                (normalized_namespace,),
            )
            if rows:
                connection.executemany(
                    """
                    INSERT INTO agent_ui_settings (namespace, key, value_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows,
                )
