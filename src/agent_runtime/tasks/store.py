"""SQLite store for durable Agent UI tasks."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.tasks.models import (
    AgentTaskAttemptRecord,
    AgentTaskAttemptTrigger,
    AgentTaskCheckpointRecord,
    AgentTaskCreate,
    AgentTaskRecord,
    AgentTaskStatus,
    AgentTaskUpdate,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


OPEN_TASK_STATUSES = {"running", "awaiting_confirmation", "awaiting_clarification"}
INTERRUPTIBLE_TASK_STATUSES = {"queued", *OPEN_TASK_STATUSES}


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if isinstance(value, dict) else {}, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _clip(value: Any, *, limit: int = 12000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _title_from_prompt(prompt: str) -> str:
    text = " ".join(str(prompt or "").split()).strip()
    return text[:80] or "Untitled task"


class AgentTaskStore:
    """Persist durable Agent UI tasks, attempts, and checkpoints."""

    def __init__(self, db_path: str | Path, *, max_task_history: int = 5000) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_task_history = max(1, int(max_task_history or 5000))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'task_sheet',
                    status TEXT NOT NULL DEFAULT 'queued',
                    agent_mode TEXT NOT NULL DEFAULT 'llm_operator',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    gateway_id TEXT NOT NULL DEFAULT '',
                    current_request_id TEXT NOT NULL DEFAULT '',
                    latest_request_id TEXT NOT NULL DEFAULT '',
                    current_attempt_id TEXT NOT NULL DEFAULT '',
                    final_response_preview TEXT NOT NULL DEFAULT '',
                    error_preview TEXT NOT NULL DEFAULT '',
                    blocker_reason TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL DEFAULT '',
                    event_run_id TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_status
                ON agent_tasks(status, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_agent_tasks_updated
                ON agent_tasks(updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_task_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    request_id TEXT NOT NULL DEFAULT '',
                    trigger TEXT NOT NULL DEFAULT 'start',
                    status TEXT NOT NULL DEFAULT 'queued',
                    parent_request_id TEXT NOT NULL DEFAULT '',
                    final_response_preview TEXT NOT NULL DEFAULT '',
                    error_preview TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_task_attempts_task
                ON agent_task_attempts(task_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_agent_task_attempts_request
                ON agent_task_attempts(request_id);

                CREATE TABLE IF NOT EXISTS agent_task_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    trace_event_id INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL DEFAULT '',
                    level TEXT NOT NULL DEFAULT 'info',
                    title TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES agent_tasks(task_id),
                    UNIQUE(task_id, request_id, trace_event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_task_checkpoints_task
                ON agent_task_checkpoints(task_id, created_at ASC);
                """
            )
            self._ensure_columns(
                connection,
                "agent_tasks",
                {
                    "event_id": "TEXT NOT NULL DEFAULT ''",
                    "event_run_id": "TEXT NOT NULL DEFAULT ''",
                    "archived_at": "TEXT NOT NULL DEFAULT ''",
                    "completed_at": "TEXT NOT NULL DEFAULT ''",
                },
            )
            ensure_store_schema_version(
                connection,
                store_name="agent_tasks",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, ddl in columns.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> AgentTaskRecord:
        payload = dict(row)
        payload["context"] = _json_loads(payload.pop("context_json", "{}"))
        return AgentTaskRecord.model_validate(payload)

    @staticmethod
    def _row_to_attempt(row: sqlite3.Row) -> AgentTaskAttemptRecord:
        return AgentTaskAttemptRecord.model_validate(dict(row))

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row) -> AgentTaskCheckpointRecord:
        payload = dict(row)
        payload["detail"] = _json_loads(payload.pop("detail_json", "{}"))
        return AgentTaskCheckpointRecord.model_validate(payload)

    def create_task(self, payload: AgentTaskCreate) -> AgentTaskRecord:
        now = utc_now_iso()
        task_id = new_id("task")
        title = payload.title or _title_from_prompt(payload.prompt)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_tasks (
                    task_id, title, prompt, source, status, agent_mode, conversation_id,
                    gateway_id, current_request_id, latest_request_id, current_attempt_id,
                    event_id, event_run_id, context_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    payload.prompt,
                    payload.source,
                    payload.status,
                    payload.agent_mode,
                    payload.conversation_id,
                    payload.gateway_id,
                    payload.current_request_id,
                    payload.latest_request_id,
                    payload.current_attempt_id,
                    payload.event_id,
                    payload.event_run_id,
                    _json_dumps(payload.context),
                    now,
                    now,
                ),
            )
            self._prune_tasks_locked(connection)
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> AgentTaskRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_tasks WHERE task_id = ?",
                (str(task_id or "").strip(),),
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def get_task_by_request_id(self, request_id: str) -> AgentTaskRecord | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT agent_tasks.*
                FROM agent_tasks
                JOIN agent_task_attempts
                  ON agent_task_attempts.task_id = agent_tasks.task_id
                WHERE agent_task_attempts.request_id = ?
                ORDER BY agent_task_attempts.created_at DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[AgentTaskRecord]:
        normalized_status = str(status or "").strip().lower()
        clauses: list[str] = []
        params: list[Any] = []
        if normalized_status and normalized_status != "archived":
            clauses.append("status = ?")
            params.append(normalized_status)
        if normalized_status == "archived":
            clauses.append("archived_at != ''")
        elif not include_archived:
            clauses.append("archived_at = ''")
        query = "SELECT * FROM agent_tasks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'awaiting_confirmation' THEN 1 WHEN 'awaiting_clarification' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 500)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_task(self, task_id: str, updates: AgentTaskUpdate | dict[str, Any]) -> AgentTaskRecord | None:
        raw = updates.model_dump(exclude_unset=True) if hasattr(updates, "model_dump") else dict(updates or {})
        setters: list[str] = []
        params: list[Any] = []
        for key, value in raw.items():
            if key == "status" and value in {"completed", "failed", "cancelled"}:
                setters.append("completed_at = CASE WHEN completed_at = '' THEN ? ELSE completed_at END")
                params.append(utc_now_iso())
            if key in {
                "title",
                "status",
                "conversation_id",
                "gateway_id",
                "current_request_id",
                "latest_request_id",
                "current_attempt_id",
                "final_response_preview",
                "error_preview",
                "blocker_reason",
                "event_id",
                "event_run_id",
                "archived_at",
            }:
                setters.append(f"{key} = ?")
                params.append(_clip(value, limit=12000 if key == "final_response_preview" else 4000))
        if not setters:
            return self.get_task(task_id)
        setters.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(str(task_id or "").strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE agent_tasks SET {', '.join(setters)} WHERE task_id = ?",
                tuple(params),
            )
        return self.get_task(task_id)

    def create_attempt(
        self,
        *,
        task_id: str,
        trigger: AgentTaskAttemptTrigger = "start",
        request_id: str = "",
        parent_request_id: str = "",
        status: AgentTaskStatus = "queued",
    ) -> AgentTaskAttemptRecord:
        now = utc_now_iso()
        attempt_id = new_id("task_att")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_task_attempts (
                    attempt_id, task_id, request_id, trigger, status, parent_request_id,
                    created_at, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    str(task_id or "").strip(),
                    str(request_id or "").strip(),
                    trigger,
                    status,
                    str(parent_request_id or "").strip(),
                    now,
                    now if status == "running" else "",
                    now,
                ),
            )
        return self.get_attempt(attempt_id)  # type: ignore[return-value]

    def get_attempt(self, attempt_id: str) -> AgentTaskAttemptRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_task_attempts WHERE attempt_id = ?",
                (str(attempt_id or "").strip(),),
            ).fetchone()
        return self._row_to_attempt(row) if row is not None else None

    def get_attempt_by_request_id(self, request_id: str) -> AgentTaskAttemptRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_task_attempts
                WHERE request_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(request_id or "").strip(),),
            ).fetchone()
        return self._row_to_attempt(row) if row is not None else None

    def update_attempt(
        self,
        attempt_id: str,
        *,
        request_id: str | None = None,
        status: AgentTaskStatus | None = None,
        final_response_preview: str | None = None,
        error_preview: str | None = None,
        completed_at: str | None = None,
    ) -> AgentTaskAttemptRecord | None:
        updates: list[str] = []
        params: list[Any] = []
        if request_id is not None:
            updates.append("request_id = ?")
            params.append(str(request_id or "").strip())
        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "running":
                updates.append("started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END")
                params.append(utc_now_iso())
        if final_response_preview is not None:
            updates.append("final_response_preview = ?")
            params.append(_clip(final_response_preview))
        if error_preview is not None:
            updates.append("error_preview = ?")
            params.append(_clip(error_preview, limit=4000))
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(str(completed_at or ""))
        elif status in {"completed", "failed", "cancelled"}:
            updates.append("completed_at = CASE WHEN completed_at = '' THEN ? ELSE completed_at END")
            params.append(utc_now_iso())
        if not updates:
            return self.get_attempt(attempt_id)
        updates.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(str(attempt_id or "").strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE agent_task_attempts SET {', '.join(updates)} WHERE attempt_id = ?",
                tuple(params),
            )
        return self.get_attempt(attempt_id)

    def list_attempts(self, task_id: str, *, limit: int = 100) -> list[AgentTaskAttemptRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_task_attempts
                WHERE task_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    str(task_id or "").strip(),
                    max(1, min(int(limit or 100), 500)),
                ),
            ).fetchall()
        return [self._row_to_attempt(row) for row in rows]

    def add_checkpoint(
        self,
        *,
        task_id: str,
        attempt_id: str = "",
        request_id: str = "",
        trace_event_id: int = 0,
        stage: str = "",
        event_type: str = "",
        level: str = "info",
        title: str = "",
        summary: str = "",
        detail: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> AgentTaskCheckpointRecord | None:
        now = created_at or utc_now_iso()
        checkpoint_id = new_id("task_cp")
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO agent_task_checkpoints (
                        checkpoint_id, task_id, attempt_id, request_id, trace_event_id,
                        stage, event_type, level, title, summary, detail_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        checkpoint_id,
                        str(task_id or "").strip(),
                        str(attempt_id or "").strip(),
                        str(request_id or "").strip(),
                        int(trace_event_id or 0),
                        _clip(stage, limit=160),
                        _clip(event_type, limit=160),
                        _clip(level, limit=40) or "info",
                        _clip(title, limit=240),
                        _clip(summary, limit=1200),
                        _json_dumps(detail or {}),
                        now,
                    ),
                )
        except sqlite3.IntegrityError:
            return None
        return self.get_checkpoint(checkpoint_id)

    def get_checkpoint(self, checkpoint_id: str) -> AgentTaskCheckpointRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_task_checkpoints WHERE checkpoint_id = ?",
                (str(checkpoint_id or "").strip(),),
            ).fetchone()
        return self._row_to_checkpoint(row) if row is not None else None

    def list_checkpoints(self, task_id: str, *, limit: int = 200) -> list[AgentTaskCheckpointRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC, trace_event_id ASC
                LIMIT ?
                """,
                (
                    str(task_id or "").strip(),
                    max(1, min(int(limit or 200), 1000)),
                ),
            ).fetchall()
        return [self._row_to_checkpoint(row) for row in rows]

    def latest_checkpoint(self, task_id: str) -> AgentTaskCheckpointRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_task_checkpoints
                WHERE task_id = ?
                ORDER BY created_at DESC, trace_event_id DESC
                LIMIT 1
                """,
                (str(task_id or "").strip(),),
            ).fetchone()
        return self._row_to_checkpoint(row) if row is not None else None

    def get_open_task(self) -> AgentTaskRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_tasks
                WHERE status IN ('running', 'awaiting_confirmation', 'awaiting_clarification')
                  AND archived_at = ''
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def next_queued_task(self) -> AgentTaskRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_tasks
                WHERE status = 'queued' AND archived_at = ''
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def interrupt_active_tasks(self) -> int:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_tasks
                SET status = 'interrupted',
                    blocker_reason = CASE
                        WHEN blocker_reason = '' THEN 'The server restarted before this task finished.'
                        ELSE blocker_reason
                    END,
                    current_request_id = '',
                    current_attempt_id = '',
                    updated_at = ?
                WHERE status IN ('queued', 'running', 'awaiting_confirmation', 'awaiting_clarification')
                  AND archived_at = ''
                """,
                (now,),
            )
        return int(cursor.rowcount or 0)

    def archive_task(self, task_id: str) -> AgentTaskRecord | None:
        return self.update_task(task_id, {"archived_at": utc_now_iso()})

    def delete_task(self, task_id: str) -> bool:
        """Hard-delete one task and its durable run history."""

        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            return False
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM agent_tasks WHERE task_id = ?",
                (normalized_task_id,),
            ).fetchone()
            if existing is None:
                return False
            self._delete_task_id_locked(connection, normalized_task_id)
        return True

    def delete_tasks(self, task_ids: list[str]) -> int:
        """Hard-delete multiple tasks and their attempts/checkpoints."""

        normalized_ids = [
            str(task_id or "").strip()
            for task_id in task_ids
            if str(task_id or "").strip()
        ]
        if not normalized_ids:
            return 0
        deleted = 0
        with self._lock, self._connect() as connection:
            for task_id in normalized_ids:
                existing = connection.execute(
                    "SELECT 1 FROM agent_tasks WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                if existing is None:
                    continue
                self._delete_task_id_locked(connection, task_id)
                deleted += 1
        return deleted

    @staticmethod
    def _delete_task_id_locked(connection: sqlite3.Connection, task_id: str) -> None:
        connection.execute("DELETE FROM agent_task_checkpoints WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM agent_task_attempts WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM agent_tasks WHERE task_id = ?", (task_id,))

    def _prune_tasks_locked(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT task_id FROM agent_tasks
            WHERE task_id NOT IN (
                SELECT task_id FROM agent_tasks
                ORDER BY created_at DESC
                LIMIT ?
            )
              AND status NOT IN ('queued', 'running', 'awaiting_confirmation', 'awaiting_clarification')
            """,
            (self.max_task_history,),
        ).fetchall()
        for row in rows:
            self._delete_task_id_locked(connection, str(row["task_id"] or ""))
