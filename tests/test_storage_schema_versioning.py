from __future__ import annotations

import sqlite3

import pytest

from agent_runtime.api.app_config import load_app_config
from agent_runtime.memory import AgentMemoryStore
from agent_runtime.storage_schema import (
    SCHEMA_META_TABLE,
    StoreSchemaVersionError,
    ensure_store_schema_version,
)


def test_store_schema_version_stamps_new_empty_database(tmp_path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as connection:
        stored = ensure_store_schema_version(
            connection,
            store_name="unit_store",
            current_version=2,
            min_supported_version=2,
        )
        row = connection.execute(
            f"SELECT schema_version FROM {SCHEMA_META_TABLE} WHERE store_name = ?",
            ("unit_store",),
        ).fetchone()

    assert stored == 2
    assert row == (2,)


def test_store_schema_version_treats_unversioned_data_as_legacy(tmp_path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE saved_rows (value TEXT NOT NULL)")
        connection.execute("INSERT INTO saved_rows (value) VALUES ('kept')")

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(StoreSchemaVersionError, match="no longer supported"):
            ensure_store_schema_version(
                connection,
                store_name="unit_store",
                current_version=2,
                min_supported_version=2,
            )


def test_store_schema_version_rejects_newer_database(tmp_path) -> None:
    db_path = tmp_path / "store.db"
    with sqlite3.connect(db_path) as connection:
        ensure_store_schema_version(
            connection,
            store_name="unit_store",
            current_version=1,
            min_supported_version=1,
        )
        connection.execute(
            f"UPDATE {SCHEMA_META_TABLE} SET schema_version = 99 WHERE store_name = ?",
            ("unit_store",),
        )

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(StoreSchemaVersionError, match="newer than this runtime"):
            ensure_store_schema_version(
                connection,
                store_name="unit_store",
                current_version=1,
                min_supported_version=1,
            )


def test_existing_agent_memory_db_without_metadata_keeps_opening(tmp_path) -> None:
    db_path = tmp_path / "agent_memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE memory_entries (
                memory_id TEXT PRIMARY KEY,
                instruction TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                scope TEXT NOT NULL DEFAULT 'global',
                model_name TEXT NOT NULL DEFAULT '',
                model_family TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                tool_type TEXT NOT NULL DEFAULT '',
                intent_type TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                provenance TEXT NOT NULL DEFAULT 'manual',
                request_id TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO memory_entries
                (memory_id, instruction, created_at, updated_at)
            VALUES ('mem_legacy', 'Keep legacy DBs readable.', 'now', 'now')
            """
        )

    store = AgentMemoryStore(db_path)

    assert store.get_entry("mem_legacy") is not None
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            f"SELECT schema_version FROM {SCHEMA_META_TABLE} WHERE store_name = ?",
            ("agent_memory",),
        ).fetchone()
    assert row == (1,)


def test_app_config_defaults_missing_version_to_current(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
server:
  host: 127.0.0.1
  port: 8011
llm:
  base_url: http://127.0.0.1:8000/v1
  api_key: local
""",
        encoding="utf-8",
    )

    loaded, _ = load_app_config(config)

    assert loaded.config_version == 1


def test_app_config_uses_source_defaults_without_implicit_yaml(tmp_path) -> None:
    loaded, resolved_path = load_app_config(cwd=tmp_path)

    assert resolved_path is None
    assert loaded.config_version == 1
    assert loaded.llm.base_url == "http://127.0.0.1:8000/v1"


def test_app_config_rejects_explicit_missing_path(tmp_path) -> None:
    config = tmp_path / "missing.yaml"

    with pytest.raises(FileNotFoundError, match="Explicit app config not found"):
        load_app_config(config)


def test_app_config_rejects_unsupported_version(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("config_version: 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="newer than this runtime supports"):
        load_app_config(config)
