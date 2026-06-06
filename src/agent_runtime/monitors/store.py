"""SQLite store for persistent Agent UI monitors."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.monitors.models import (
    AgentMonitorCreate,
    AgentMonitorObservationRecord,
    AgentMonitorRecord,
    AgentMonitorStatus,
    AgentMonitorTriggerRecord,
    AgentMonitorUpdate,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


OPEN_MONITOR_STATUSES = {"queued", "running", "paused"}


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


def _title_from_command(command: str) -> str:
    text = " ".join(str(command or "").split()).strip()
    return text[:80] or "Untitled monitor"


class AgentMonitorStore:
    """Persist monitors, observations, and trigger records."""

    def __init__(self, db_path: str | Path, *, max_monitor_history: int = 5000) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_monitor_history = max(1, int(max_monitor_history or 5000))
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
                CREATE TABLE IF NOT EXISTS agent_monitors (
                    monitor_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'sample_command',
                    command TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL DEFAULT 5,
                    duration_seconds INTEGER NOT NULL DEFAULT 300,
                    condition TEXT NOT NULL DEFAULT '',
                    natural_language_condition TEXT NOT NULL DEFAULT '',
                    trigger_mode TEXT NOT NULL DEFAULT 'deterministic',
                    action_prompt TEXT NOT NULL DEFAULT '',
                    planner_rationale TEXT NOT NULL DEFAULT '',
                    risk_notes TEXT NOT NULL DEFAULT '',
                    judge_interval_seconds INTEGER NOT NULL DEFAULT 5,
                    latest_judge_summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    agent_mode TEXT NOT NULL DEFAULT 'llm_operator',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    gateway_id TEXT NOT NULL DEFAULT '',
                    terminal_session_id TEXT NOT NULL DEFAULT '',
                    current_execution_id TEXT NOT NULL DEFAULT '',
                    latest_observation_id TEXT NOT NULL DEFAULT '',
                    trigger_reason TEXT NOT NULL DEFAULT '',
                    triggered_task_id TEXT NOT NULL DEFAULT '',
                    error_preview TEXT NOT NULL DEFAULT '',
                    final_summary TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    triggered_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_agent_monitors_status
                ON agent_monitors(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS agent_monitor_observations (
                    observation_id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    kind TEXT NOT NULL DEFAULT 'sample',
                    status TEXT NOT NULL DEFAULT 'ok',
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT '',
                    output_preview TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER,
                    matched INTEGER NOT NULL DEFAULT 0,
                    match_reason TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(monitor_id) REFERENCES agent_monitors(monitor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_monitor_observations_monitor
                ON agent_monitor_observations(monitor_id, sequence ASC);

                CREATE TABLE IF NOT EXISTS agent_monitor_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    notification_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(monitor_id) REFERENCES agent_monitors(monitor_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_monitor_triggers_monitor
                ON agent_monitor_triggers(monitor_id, created_at DESC);
                """
            )
            self._ensure_monitor_columns_locked(connection)
            ensure_store_schema_version(
                connection,
                store_name="agent_monitors",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    def _ensure_monitor_columns_locked(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(agent_monitors)").fetchall()
        existing = {str(row["name"]) for row in rows}
        columns = {
            "natural_language_condition": "TEXT NOT NULL DEFAULT ''",
            "trigger_mode": "TEXT NOT NULL DEFAULT 'deterministic'",
            "planner_rationale": "TEXT NOT NULL DEFAULT ''",
            "risk_notes": "TEXT NOT NULL DEFAULT ''",
            "judge_interval_seconds": "INTEGER NOT NULL DEFAULT 5",
            "latest_judge_summary": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE agent_monitors ADD COLUMN {name} {definition}")
        connection.execute(
            """
            UPDATE agent_monitors
            SET trigger_mode = 'deterministic'
            WHERE trigger_mode IS NULL OR trigger_mode = ''
            """
        )

    @staticmethod
    def _row_to_monitor(row: sqlite3.Row) -> AgentMonitorRecord:
        payload = dict(row)
        payload["context"] = _json_loads(payload.pop("context_json", "{}"))
        if payload.get("trigger_mode") not in {"deterministic", "llm_judged", "hybrid"}:
            payload["trigger_mode"] = "deterministic"
        try:
            judge_interval = int(payload.get("judge_interval_seconds") or 5)
        except (TypeError, ValueError):
            judge_interval = 5
        payload["judge_interval_seconds"] = max(1, judge_interval)
        return AgentMonitorRecord.model_validate(payload)

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> AgentMonitorObservationRecord:
        payload = dict(row)
        payload["detail"] = _json_loads(payload.pop("detail_json", "{}"))
        payload["matched"] = bool(payload.get("matched"))
        return AgentMonitorObservationRecord.model_validate(payload)

    @staticmethod
    def _row_to_trigger(row: sqlite3.Row) -> AgentMonitorTriggerRecord:
        payload = dict(row)
        payload["detail"] = _json_loads(payload.pop("detail_json", "{}"))
        return AgentMonitorTriggerRecord.model_validate(payload)

    def create_monitor(self, payload: AgentMonitorCreate) -> AgentMonitorRecord:
        now = utc_now_iso()
        monitor_id = new_id("mon")
        title = payload.title or _title_from_command(payload.command)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_monitors (
                    monitor_id, title, prompt, mode, command, interval_seconds,
                    duration_seconds, condition, natural_language_condition, trigger_mode,
                    action_prompt, planner_rationale, risk_notes, judge_interval_seconds,
                    status, agent_mode, conversation_id, gateway_id, context_json, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    monitor_id,
                    title,
                    payload.prompt,
                    payload.mode,
                    payload.command,
                    int(payload.interval_seconds or 5),
                    int(payload.duration_seconds or 300),
                    payload.condition,
                    payload.natural_language_condition,
                    payload.trigger_mode,
                    payload.action_prompt,
                    payload.planner_rationale,
                    payload.risk_notes,
                    int(payload.judge_interval_seconds or 5),
                    payload.status,
                    payload.agent_mode,
                    payload.conversation_id,
                    payload.gateway_id,
                    _json_dumps(payload.context),
                    now,
                    now,
                ),
            )
            self._prune_monitors_locked(connection)
        return self.get_monitor(monitor_id)  # type: ignore[return-value]

    def get_monitor(self, monitor_id: str) -> AgentMonitorRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_monitors WHERE monitor_id = ?",
                (str(monitor_id or "").strip(),),
            ).fetchone()
        return self._row_to_monitor(row) if row is not None else None

    def list_monitors(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[AgentMonitorRecord]:
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
        query = "SELECT * FROM agent_monitors"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'triggered' THEN 1 WHEN 'queued' THEN 2 WHEN 'paused' THEN 3 ELSE 4 END, updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 500)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_monitor(row) for row in rows]

    def update_monitor(self, monitor_id: str, updates: AgentMonitorUpdate | dict[str, Any]) -> AgentMonitorRecord | None:
        raw = updates.model_dump(exclude_unset=True) if hasattr(updates, "model_dump") else dict(updates or {})
        setters: list[str] = []
        params: list[Any] = []
        status = raw.get("status")
        if status == "running":
            setters.append("started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END")
            params.append(utc_now_iso())
        if status in {"completed", "failed", "cancelled", "interrupted"}:
            setters.append("completed_at = CASE WHEN completed_at = '' THEN ? ELSE completed_at END")
            params.append(utc_now_iso())
        if status == "triggered":
            setters.append("triggered_at = CASE WHEN triggered_at = '' THEN ? ELSE triggered_at END")
            params.append(utc_now_iso())
        for key, value in raw.items():
            if key in {
                "title",
                "status",
                "terminal_session_id",
                "current_execution_id",
                "latest_observation_id",
                "trigger_reason",
                "triggered_task_id",
                "error_preview",
                "final_summary",
                "latest_judge_summary",
                "archived_at",
            }:
                setters.append(f"{key} = ?")
                params.append(_clip(value, limit=12000 if key == "final_summary" else 4000))
        if not setters:
            return self.get_monitor(monitor_id)
        setters.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(str(monitor_id or "").strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE agent_monitors SET {', '.join(setters)} WHERE monitor_id = ?",
                tuple(params),
            )
        return self.get_monitor(monitor_id)

    def add_observation(
        self,
        *,
        monitor_id: str,
        sequence: int,
        kind: str = "sample",
        status: str = "ok",
        stdout: str = "",
        stderr: str = "",
        output_preview: str = "",
        exit_code: int | None = None,
        matched: bool = False,
        match_reason: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AgentMonitorObservationRecord:
        now = utc_now_iso()
        observation_id = new_id("mon_obs")
        preview = output_preview or stdout or stderr
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_monitor_observations (
                    observation_id, monitor_id, sequence, kind, status, stdout, stderr,
                    output_preview, exit_code, matched, match_reason, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    str(monitor_id or "").strip(),
                    int(sequence or 0),
                    _clip(kind, limit=80),
                    _clip(status, limit=80) or "ok",
                    _clip(stdout),
                    _clip(stderr),
                    _clip(preview, limit=4000),
                    exit_code,
                    1 if matched else 0,
                    _clip(match_reason, limit=1000),
                    _json_dumps(detail or {}),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE agent_monitors
                SET latest_observation_id = ?, updated_at = ?
                WHERE monitor_id = ?
                """,
                (observation_id, now, str(monitor_id or "").strip()),
            )
        return self.get_observation(observation_id)  # type: ignore[return-value]

    def get_observation(self, observation_id: str) -> AgentMonitorObservationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_monitor_observations WHERE observation_id = ?",
                (str(observation_id or "").strip(),),
            ).fetchone()
        return self._row_to_observation(row) if row is not None else None

    def list_observations(
        self,
        monitor_id: str,
        *,
        limit: int = 200,
        after_sequence: int = 0,
    ) -> list[AgentMonitorObservationRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_monitor_observations
                WHERE monitor_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (
                    str(monitor_id or "").strip(),
                    int(after_sequence or 0),
                    max(1, min(int(limit or 200), 1000)),
                ),
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def add_trigger(
        self,
        *,
        monitor_id: str,
        observation_id: str = "",
        reason: str = "",
        notification_id: str = "",
        task_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> AgentMonitorTriggerRecord:
        now = utc_now_iso()
        trigger_id = new_id("mon_trg")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_monitor_triggers (
                    trigger_id, monitor_id, observation_id, reason, notification_id,
                    task_id, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trigger_id,
                    str(monitor_id or "").strip(),
                    str(observation_id or "").strip(),
                    _clip(reason, limit=1000),
                    str(notification_id or "").strip(),
                    str(task_id or "").strip(),
                    _json_dumps(detail or {}),
                    now,
                ),
            )
        return self.get_trigger(trigger_id)  # type: ignore[return-value]

    def get_trigger(self, trigger_id: str) -> AgentMonitorTriggerRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_monitor_triggers WHERE trigger_id = ?",
                (str(trigger_id or "").strip(),),
            ).fetchone()
        return self._row_to_trigger(row) if row is not None else None

    def list_triggers(self, monitor_id: str, *, limit: int = 100) -> list[AgentMonitorTriggerRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_monitor_triggers
                WHERE monitor_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(monitor_id or "").strip(), max(1, min(int(limit or 100), 500))),
            ).fetchall()
        return [self._row_to_trigger(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM agent_monitors WHERE archived_at = '' GROUP BY status"
            ).fetchall()
            archived = connection.execute(
                "SELECT COUNT(*) AS count FROM agent_monitors WHERE archived_at != ''"
            ).fetchone()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        counts["archived"] = int(archived["count"] if archived is not None else 0)
        return counts

    def interrupt_active_monitors(self) -> int:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_monitors
                SET status = 'interrupted',
                    error_preview = CASE
                        WHEN error_preview = '' THEN 'The server restarted before this monitor finished.'
                        ELSE error_preview
                    END,
                    current_execution_id = '',
                    updated_at = ?,
                    completed_at = CASE WHEN completed_at = '' THEN ? ELSE completed_at END
                WHERE status IN ('queued', 'running', 'paused') AND archived_at = ''
                """,
                (now, now),
            )
        return int(cursor.rowcount or 0)

    def archive_monitor(self, monitor_id: str) -> AgentMonitorRecord | None:
        return self.update_monitor(monitor_id, {"archived_at": utc_now_iso(), "status": "archived"})

    def _prune_monitors_locked(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM agent_monitors
            WHERE monitor_id NOT IN (
                SELECT monitor_id FROM agent_monitors
                ORDER BY created_at DESC
                LIMIT ?
            )
              AND status NOT IN ('queued', 'running', 'paused')
            """,
            (self.max_monitor_history,),
        )
