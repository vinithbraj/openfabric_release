"""SQLite store for persistent scheduled Agent UI events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.events.models import (
    AgentEventCreate,
    AgentEventRecord,
    AgentEventRunRecord,
    AgentEventUpdate,
    AgentNotificationCreate,
    AgentNotificationRecord,
    AgentNotificationUpdate,
    DEFAULT_EVENT_NOTIFY_ON,
    EventRunStatus,
    NotificationStatus,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def parse_utc_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp into UTC when possible."""

    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def add_interval(
    value: str | None,
    interval_seconds: int,
    *,
    minimum_seconds: int = 60,
) -> str:
    """Return ``value`` plus an interval, using now when value is empty."""

    base = parse_utc_iso(value) or datetime.now(UTC)
    return (
        base
        + timedelta(seconds=max(max(1, int(minimum_seconds or 60)), int(interval_seconds or 3600)))
    ).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value if isinstance(value, dict) else {}, sort_keys=True, ensure_ascii=True, default=str)


def _json_list_dumps(value: Any) -> str:
    return json.dumps(value if isinstance(value, list) else [], ensure_ascii=True, default=str)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _json_list_loads(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except Exception:
        return []
    return loaded if isinstance(loaded, list) else []


def _clip(value: Any, *, limit: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _clip_preserve_lines(value: Any, *, limit: int = 12000) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _minimum_event_interval(schedule_type: str, action_type: str) -> int:
    if schedule_type == "once" and action_type == "notification":
        return 1
    return 60


class AgentEventStore:
    """Persist scheduled events and run history."""

    def __init__(self, db_path: str | Path, *, max_run_history: int = 5000) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_run_history = max(1, int(max_run_history or 5000))
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
                CREATE TABLE IF NOT EXISTS agent_events (
                    event_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    schedule_type TEXT NOT NULL DEFAULT 'interval',
                    event_kind TEXT NOT NULL DEFAULT 'scheduled',
                    interval_seconds INTEGER NOT NULL DEFAULT 3600,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    next_run_at TEXT NOT NULL DEFAULT '',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    auto_approve_confirmations INTEGER NOT NULL DEFAULT 1,
                    action_type TEXT NOT NULL DEFAULT 'agent_prompt',
                    notification_message TEXT NOT NULL DEFAULT '',
                    notify_on_json TEXT NOT NULL DEFAULT '["failed","cancelled","skipped","awaiting_confirmation","awaiting_clarification"]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_due
                ON agent_events(status, next_run_at);

                CREATE TABLE IF NOT EXISTS agent_event_runs (
                    event_run_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    task_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    parent_request_id TEXT NOT NULL DEFAULT '',
                    scheduled_for TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    final_response_preview TEXT NOT NULL DEFAULT '',
                    error_preview TEXT NOT NULL DEFAULT '',
                    trace_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(event_id) REFERENCES agent_events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_event_runs_event
                ON agent_event_runs(event_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_event_runs_status
                ON agent_event_runs(event_id, status);

                CREATE TABLE IF NOT EXISTS agent_notifications (
                    notification_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL DEFAULT 'info',
                    title TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'system',
                    source_id TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL DEFAULT '',
                    event_run_id TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'unread',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_notifications_status
                ON agent_notifications(status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_notifications_source
                ON agent_notifications(source_type, source_id);
                """
            )
            self._ensure_columns(
                connection,
                "agent_events",
                {
                    "action_type": "TEXT NOT NULL DEFAULT 'agent_prompt'",
                    "event_kind": "TEXT NOT NULL DEFAULT 'scheduled'",
                    "notification_message": "TEXT NOT NULL DEFAULT ''",
                    "notify_on_json": (
                        "TEXT NOT NULL DEFAULT "
                        "'[\"failed\",\"cancelled\",\"skipped\",\"awaiting_confirmation\",\"awaiting_clarification\"]'"
                    ),
                },
            )
            self._ensure_columns(
                connection,
                "agent_event_runs",
                {
                    "task_id": "TEXT NOT NULL DEFAULT ''",
                },
            )
            ensure_store_schema_version(
                connection,
                store_name="agent_events",
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
    def _row_to_event(row: sqlite3.Row) -> AgentEventRecord:
        payload = dict(row)
        payload["context"] = _json_loads(payload.pop("context_json", "{}"))
        payload["notify_on"] = _json_list_loads(payload.pop("notify_on_json", "[]")) or list(
            DEFAULT_EVENT_NOTIFY_ON
        )
        payload["auto_approve_confirmations"] = bool(payload.get("auto_approve_confirmations"))
        return AgentEventRecord.model_validate(payload)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> AgentEventRunRecord:
        return AgentEventRunRecord.model_validate(dict(row))

    @staticmethod
    def _row_to_notification(row: sqlite3.Row) -> AgentNotificationRecord:
        payload = dict(row)
        payload["metadata"] = _json_loads(payload.pop("metadata_json", "{}"))
        return AgentNotificationRecord.model_validate(payload)

    def create_event(self, payload: AgentEventCreate) -> AgentEventRecord:
        """Create and return one event."""

        now = utc_now_iso()
        minimum_interval = _minimum_event_interval(payload.schedule_type, payload.action_type)
        interval = max(minimum_interval, int(payload.interval_seconds or 3600))
        event_kind = str(payload.event_kind or "scheduled").strip() or "scheduled"
        next_run_at = str(payload.next_run_at or "").strip()
        if not next_run_at and event_kind != "todo":
            next_run_at = add_interval(
                now,
                interval,
                minimum_seconds=minimum_interval,
            )
        title = " ".join(str(payload.title or "").split()).strip() or _clip(payload.prompt, limit=80)
        event_id = new_id("evt")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_events (
                    event_id, title, prompt, status, schedule_type, interval_seconds, timezone,
                    event_kind, next_run_at, last_run_at, context_json, auto_approve_confirmations,
                    action_type, notification_message, notify_on_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    title,
                    payload.prompt,
                    payload.status,
                    payload.schedule_type,
                    interval,
                    str(payload.timezone or "UTC").strip() or "UTC",
                    event_kind,
                    next_run_at,
                    "",
                    _json_dumps(payload.context),
                    1 if payload.auto_approve_confirmations else 0,
                    payload.action_type,
                    payload.notification_message,
                    _json_list_dumps(list(payload.notify_on or DEFAULT_EVENT_NOTIFY_ON)),
                    now,
                    now,
                ),
            )
        created = self.get_event(event_id)
        if created is None:  # pragma: no cover - defensive SQLite boundary
            raise RuntimeError("Failed to create scheduled event.")
        return created

    def get_event(self, event_id: str) -> AgentEventRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_events WHERE event_id = ?",
                (str(event_id or "").strip(),),
            ).fetchone()
        return self._row_to_event(row) if row is not None else None

    def list_events(self, *, include_deleted: bool = False) -> list[AgentEventRecord]:
        query = "SELECT * FROM agent_events"
        params: tuple[Any, ...] = ()
        if not include_deleted:
            query += " WHERE status != 'deleted'"
        query += " ORDER BY status = 'active' DESC, next_run_at ASC, updated_at DESC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_event(row) for row in rows]

    def update_event(self, event_id: str, payload: AgentEventUpdate) -> AgentEventRecord | None:
        """Patch one event and return the updated record."""

        updates: list[str] = []
        params: list[Any] = []
        raw = payload.model_dump(exclude_unset=True)
        existing = self.get_event(event_id)
        effective_schedule_type = str(
            raw.get("schedule_type")
            or (existing.schedule_type if existing is not None else "interval")
        )
        effective_action_type = str(
            raw.get("action_type")
            or (existing.action_type if existing is not None else "agent_prompt")
        )
        effective_event_kind = str(
            raw.get("event_kind")
            or (existing.event_kind if existing is not None else "scheduled")
        )
        minimum_interval = _minimum_event_interval(effective_schedule_type, effective_action_type)
        if effective_event_kind == "todo":
            raw["next_run_at"] = ""
        if (
            existing is not None
            and "interval_seconds" not in raw
            and ("schedule_type" in raw or "action_type" in raw)
            and int(existing.interval_seconds or 0) < minimum_interval
        ):
            raw["interval_seconds"] = minimum_interval
        field_map = {
            "title": "title",
            "prompt": "prompt",
            "status": "status",
            "schedule_type": "schedule_type",
            "event_kind": "event_kind",
            "interval_seconds": "interval_seconds",
            "timezone": "timezone",
            "next_run_at": "next_run_at",
            "context": "context_json",
            "auto_approve_confirmations": "auto_approve_confirmations",
            "action_type": "action_type",
            "notification_message": "notification_message",
            "notify_on": "notify_on_json",
        }
        for field, column in field_map.items():
            if field not in raw:
                continue
            value = raw[field]
            if field == "context":
                value = _json_dumps(value)
            elif field == "notify_on":
                value = _json_list_dumps(value)
            elif field == "auto_approve_confirmations":
                value = 1 if bool(value) else 0
            elif field == "interval_seconds":
                value = max(minimum_interval, int(value or 3600))
            elif field == "title":
                value = " ".join(str(value or "").split()).strip()
            elif field == "timezone":
                value = str(value or "UTC").strip() or "UTC"
            elif field == "event_kind":
                value = str(value or "scheduled").strip() or "scheduled"
            elif field == "status" and value == "deleted":
                value = "deleted"
            updates.append(f"{column} = ?")
            params.append(value)
        if not updates:
            return self.get_event(event_id)
        updates.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(str(event_id or "").strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE agent_events SET {', '.join(updates)} WHERE event_id = ?",
                tuple(params),
            )
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> AgentEventRecord | None:
        """Soft-delete one event while preserving run history."""

        return self.update_event(event_id, AgentEventUpdate(status="deleted"))

    def _open_run_locked(
        self,
        connection: sqlite3.Connection,
        event_id: str,
    ) -> AgentEventRunRecord | None:
        row = connection.execute(
            """
            SELECT * FROM agent_event_runs
            WHERE event_id = ? AND status IN ('queued', 'running', 'awaiting_confirmation', 'awaiting_clarification')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def _has_open_run_locked(self, connection: sqlite3.Connection, event_id: str) -> bool:
        return self._open_run_locked(connection, event_id) is not None

    def get_open_run(self, event_id: str) -> AgentEventRunRecord | None:
        """Return the oldest run currently blocking a scheduled event."""

        with self._lock, self._connect() as connection:
            return self._open_run_locked(connection, str(event_id or "").strip())

    def claim_due_events(self, *, now: str | None = None, limit: int = 10) -> list[AgentEventRecord]:
        """Return due active events and advance each next run once."""

        timestamp = str(now or utc_now_iso())
        claimed: list[AgentEventRecord] = []
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_events
                WHERE status = 'active' AND event_kind != 'todo' AND next_run_at != '' AND next_run_at <= ?
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (timestamp, max(1, int(limit or 10))),
            ).fetchall()
            for row in rows:
                event = self._row_to_event(row)
                is_once = event.schedule_type == "once"
                next_time = "" if is_once else add_interval(timestamp, event.interval_seconds)
                next_status = "completed" if is_once else event.status
                blocking_run = self._open_run_locked(connection, event.event_id)
                if blocking_run is not None:
                    run_id = new_id("evt_run")
                    blocker_label = blocking_run.event_run_id
                    if blocking_run.request_id:
                        blocker_label = f"{blocker_label} ({blocking_run.request_id})"
                    blocked_preview = (
                        "Skipped because scheduled run "
                        f"{blocker_label} is still {blocking_run.status}. "
                        "Open that run to continue or resolve it."
                    )
                    connection.execute(
                        """
                        INSERT INTO agent_event_runs (
                            event_run_id, event_id, request_id, parent_request_id,
                            scheduled_for, started_at, completed_at, status,
                            error_preview, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped', ?, ?, ?)
                        """,
                        (
                            run_id,
                            event.event_id,
                            "",
                            blocking_run.request_id,
                            event.next_run_at,
                            timestamp,
                            timestamp,
                            blocked_preview,
                            timestamp,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        "UPDATE agent_events SET next_run_at = ?, updated_at = ? WHERE event_id = ?",
                        (next_time, timestamp, event.event_id),
                    )
                    if is_once:
                        connection.execute(
                            "UPDATE agent_events SET status = ?, updated_at = ? WHERE event_id = ?",
                            (next_status, timestamp, event.event_id),
                        )
                    if "skipped" in set(event.notify_on or []):
                        self._create_notification_locked(
                            connection,
                            AgentNotificationCreate(
                                level="warning",
                                title=f"Scheduled event skipped: {event.title}",
                                message=blocked_preview,
                                source_type="scheduled_event_run",
                                source_id=run_id,
                                event_id=event.event_id,
                                event_run_id=run_id,
                                metadata={"event_status": "skipped"},
                            ),
                            now=timestamp,
                        )
                    continue
                connection.execute(
                    """
                    UPDATE agent_events
                    SET last_run_at = ?, next_run_at = ?, status = ?, updated_at = ?
                    WHERE event_id = ?
                    """,
                    (timestamp, next_time, next_status, timestamp, event.event_id),
                )
                claimed.append(event)
            self._prune_runs_locked(connection)
        return claimed

    def can_start_run(self, event_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return not self._has_open_run_locked(connection, str(event_id or "").strip())

    def create_run(
        self,
        *,
        event_id: str,
        scheduled_for: str,
        request_id: str = "",
        task_id: str = "",
        status: EventRunStatus = "running",
    ) -> AgentEventRunRecord:
        now = utc_now_iso()
        run_id = new_id("evt_run")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_event_runs (
                    event_run_id, event_id, task_id, request_id, scheduled_for, started_at,
                    status, trace_url, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(event_id or "").strip(),
                    str(task_id or "").strip(),
                    str(request_id or "").strip(),
                    str(scheduled_for or now),
                    now,
                    status,
                    f"/api/agent/trace/{request_id}" if request_id else "",
                    now,
                    now,
                ),
            )
            self._prune_runs_locked(connection)
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, event_run_id: str) -> AgentEventRunRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_event_runs WHERE event_run_id = ?",
                (str(event_run_id or "").strip(),),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def get_run_by_request_id(
        self,
        event_id: str,
        request_id: str,
    ) -> AgentEventRunRecord | None:
        """Return the run for a request within one scheduled event."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM agent_event_runs
                WHERE event_id = ? AND request_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    str(event_id or "").strip(),
                    str(request_id or "").strip(),
                ),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def update_run(
        self,
        event_run_id: str,
        *,
        task_id: str | None = None,
        request_id: str | None = None,
        parent_request_id: str | None = None,
        status: EventRunStatus | None = None,
        final_response_preview: str | None = None,
        error_preview: str | None = None,
        completed_at: str | None = None,
    ) -> AgentEventRunRecord | None:
        updates: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            updates.append("task_id = ?")
            params.append(str(task_id or "").strip())
        if request_id is not None:
            updates.append("request_id = ?")
            params.append(str(request_id or "").strip())
            updates.append("trace_url = ?")
            params.append(f"/api/agent/trace/{request_id}" if request_id else "")
        if parent_request_id is not None:
            updates.append("parent_request_id = ?")
            params.append(str(parent_request_id or "").strip())
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if final_response_preview is not None:
            updates.append("final_response_preview = ?")
            params.append(_clip_preserve_lines(final_response_preview))
        if error_preview is not None:
            updates.append("error_preview = ?")
            params.append(_clip_preserve_lines(error_preview, limit=4000))
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(str(completed_at or ""))
        if not updates:
            return self.get_run(event_run_id)
        updates.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(str(event_run_id or "").strip())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE agent_event_runs SET {', '.join(updates)} WHERE event_run_id = ?",
                tuple(params),
            )
        return self.get_run(event_run_id)

    def list_runs(self, event_id: str, *, limit: int = 100) -> list[AgentEventRunRecord]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_event_runs
                WHERE event_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (str(event_id or "").strip(), max(1, int(limit or 100))),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def _create_notification_locked(
        self,
        connection: sqlite3.Connection,
        payload: AgentNotificationCreate,
        *,
        now: str | None = None,
    ) -> AgentNotificationRecord:
        timestamp = str(now or utc_now_iso())
        notification_id = new_id("ntf")
        title = " ".join(str(payload.title or "").split()).strip() or "Notification"
        connection.execute(
            """
            INSERT INTO agent_notifications (
                notification_id, level, title, message, source_type, source_id,
                event_id, event_run_id, request_id, status, metadata_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unread', ?, ?, ?)
            """,
            (
                notification_id,
                payload.level,
                title,
                _clip_preserve_lines(payload.message, limit=24000),
                str(payload.source_type or "system").strip() or "system",
                str(payload.source_id or "").strip(),
                str(payload.event_id or "").strip(),
                str(payload.event_run_id or "").strip(),
                str(payload.request_id or "").strip(),
                _json_dumps(payload.metadata),
                timestamp,
                timestamp,
            ),
        )
        self._prune_notifications_locked(connection)
        row = connection.execute(
            "SELECT * FROM agent_notifications WHERE notification_id = ?",
            (notification_id,),
        ).fetchone()
        return self._row_to_notification(row)

    def create_notification(self, payload: AgentNotificationCreate) -> AgentNotificationRecord:
        with self._lock, self._connect() as connection:
            return self._create_notification_locked(connection, payload)

    def get_notification(self, notification_id: str) -> AgentNotificationRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_notifications WHERE notification_id = ?",
                (str(notification_id or "").strip(),),
            ).fetchone()
        return self._row_to_notification(row) if row is not None else None

    def list_notifications(
        self,
        *,
        status: NotificationStatus | None = None,
        include_dismissed: bool = False,
        limit: int = 50,
    ) -> list[AgentNotificationRecord]:
        query = "SELECT * FROM agent_notifications"
        params: list[Any] = []
        filters: list[str] = []
        if status:
            filters.append("status = ?")
            params.append(status)
        elif not include_dismissed:
            filters.append("status != 'dismissed'")
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit or 50), 500)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def notification_counts(self) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM agent_notifications GROUP BY status"
            ).fetchall()
        counts = {"unread": 0, "read": 0, "dismissed": 0}
        for row in rows:
            status = str(row["status"] or "")
            if status in counts:
                counts[status] = int(row["count"] or 0)
        counts["visible"] = counts["unread"] + counts["read"]
        return counts

    def notification_exists(self, *, source_type: str, source_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM agent_notifications
                WHERE source_type = ? AND source_id = ?
                LIMIT 1
                """,
                (
                    str(source_type or "").strip(),
                    str(source_id or "").strip(),
                ),
            ).fetchone()
        return row is not None

    def update_notification(
        self,
        notification_id: str,
        payload: AgentNotificationUpdate,
    ) -> AgentNotificationRecord | None:
        raw = payload.model_dump(exclude_unset=True)
        if "status" not in raw:
            return self.get_notification(notification_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_notifications
                SET status = ?, updated_at = ?
                WHERE notification_id = ?
                """,
                (
                    raw["status"],
                    utc_now_iso(),
                    str(notification_id or "").strip(),
                ),
            )
        return self.get_notification(notification_id)

    def mark_all_notifications_read(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_notifications
                SET status = 'read', updated_at = ?
                WHERE status = 'unread'
                """,
                (utc_now_iso(),),
            )
            return int(cursor.rowcount or 0)

    def _prune_runs_locked(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM agent_event_runs
            WHERE event_run_id NOT IN (
                SELECT event_run_id FROM agent_event_runs
                ORDER BY created_at DESC
                LIMIT ?
            )
            """,
            (self.max_run_history,),
        )

    def _prune_notifications_locked(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM agent_notifications
            WHERE notification_id NOT IN (
                SELECT notification_id FROM agent_notifications
                ORDER BY created_at DESC
                LIMIT ?
            )
            """,
            (max(100, self.max_run_history),),
        )
