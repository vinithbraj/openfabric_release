"""SQLite-backed Agent UI chat history."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.observability.agent_trace import AgentRequestTrace
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def _clip_words(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _clip_preserve_lines(value: Any, *, limit: int = 12000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _json_dumps(value: Any) -> str:
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return ""


def _json_loads(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        loaded = json.loads(value)
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def _learning_summary_empty() -> dict[str, Any]:
    return {
        "counts": {
            "applied": 0,
            "not_applied": 0,
            "retrieved": 0,
            "learned": 0,
            "proposed": 0,
            "failed": 0,
        },
        "event_count": 0,
        "rows": [],
    }


def _learning_summary_safe_text(value: Any, *, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


def _learning_summary_unique(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _learning_summary_ids(detail: dict[str, Any], text: str = "") -> list[str]:
    ids: list[Any] = []
    for key in (
        "cache_id",
        "command_template_id",
        "selected_template_id",
        "template_id",
        "computation_cache_id",
        "entry_id",
        "exact_step_key",
        "lesson_id",
        "proposal_id",
        "target_id",
        "applied_ref",
    ):
        value = detail.get(key)
        if isinstance(value, list):
            ids.extend(value)
        elif value is not None:
            ids.append(value)
    ids.extend(re.findall(r"\b(?:cmdtpl|cache|lrnt|lesson|proposal)-[A-Za-z0-9_-]+\b", text))
    return _learning_summary_unique(ids)[:6]


def _learning_summary_candidate_count(detail: dict[str, Any]) -> int:
    direct_count = detail.get("candidate_count")
    try:
        if direct_count is not None and int(direct_count) > 0:
            return int(direct_count)
    except (TypeError, ValueError):
        pass
    rejected_count = detail.get("checked_candidate_count", detail.get("rejected_candidate_count"))
    try:
        if rejected_count is not None and int(rejected_count) > 0:
            return int(rejected_count)
    except (TypeError, ValueError):
        pass
    total = 0
    for key in ("command_candidate_count", "python_candidate_count"):
        try:
            total += max(0, int(detail.get(key) or 0))
        except (TypeError, ValueError):
            pass
    if total > 0:
        return total
    candidates = detail.get("candidates")
    return len(candidates) if isinstance(candidates, list) else 0


def _learning_summary_percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    percent = number * 100 if number <= 1 else number
    return f"{percent:.0f}%"


def _learning_summary_lrt_not_applied_text(detail: dict[str, Any]) -> str:
    count = _learning_summary_candidate_count(detail)
    threshold = _learning_summary_percent(detail.get("similarity_threshold", detail.get("threshold", 0.92))) or "92%"
    best_score = _learning_summary_percent(
        detail.get("best_rejected_score")
        if detail.get("best_rejected_score") is not None
        else detail.get("best_score", detail.get("selected_score", detail.get("score")))
    )
    checked = f"{count} candidate{'s' if count != 1 else ''} checked" if count > 0 else "no candidates checked"
    if best_score:
        return f"{checked}; best {best_score} was below the {threshold} threshold."
    return f"no candidate reached the {threshold} threshold."


def _learning_summary_lrdirect_not_applied_text(detail: dict[str, Any]) -> str:
    count = _learning_summary_candidate_count(detail)
    if count > 0:
        return f"{count} candidate{'s' if count != 1 else ''} checked; no exact-step replay was used."
    return "No exact-step replay candidate was available."


def _learning_summary_add_row(
    summary: dict[str, Any],
    rows_by_key: dict[str, dict[str, Any]],
    *,
    key: str,
    category: str,
    label: str,
    count: int = 1,
    ids: list[str] | None = None,
    benefited_steps: str = "",
    learned: str = "",
    status: str = "",
) -> None:
    if category not in summary["counts"]:
        category = "proposed"
    safe_count = max(1, int(count or 1))
    safe_ids = _learning_summary_unique(ids or [])[:6]
    safe_key = str(key or "|".join([category, label, benefited_steps, ",".join(safe_ids)]))
    existing = rows_by_key.get(safe_key)
    if existing is not None:
        existing["count"] += safe_count
        existing["ids"] = _learning_summary_unique([*existing.get("ids", []), *safe_ids])[:6]
        if status:
            existing["status"] = _learning_summary_safe_text(
                "; ".join(_learning_summary_unique([existing.get("status", ""), status])[:3]),
                limit=140,
            )
        if learned:
            existing["learned"] = _learning_summary_safe_text(
                " ".join(_learning_summary_unique([existing.get("learned", ""), learned])[:3]),
                limit=280,
            )
        return
    row = {
        "key": safe_key,
        "category": category,
        "label": _learning_summary_safe_text(label, limit=140) or "Learning event",
        "count": safe_count,
        "ids": safe_ids,
        "benefited_steps": _learning_summary_safe_text(benefited_steps, limit=180),
        "learned": _learning_summary_safe_text(learned, limit=280),
        "status": _learning_summary_safe_text(status, limit=140),
    }
    rows_by_key[safe_key] = row


def _learning_summary_text_parts(value: Any, *, depth: int = 0) -> list[str]:
    if value is None or depth > 3:
        return []
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        return [text] if text else []
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_learning_summary_text_parts(item, depth=depth + 1))
        return parts[:80]
    if isinstance(value, dict):
        parts = []
        for key in (
            "reason",
            "summary",
            "title",
            "operation_description",
            "description",
            "status",
            "outcome",
            "cache_type",
            "command_template_id",
            "selected_template_id",
            "template_id",
            "cache_id",
            "exact_step_key",
            "action_label",
            "shell_command",
            "command",
            "execution_snippet",
        ):
            if key in value:
                parts.extend(_learning_summary_text_parts(value[key], depth=depth + 1))
        return parts[:80]
    return []


def _learning_artifact_signal(text: str, detail: dict[str, Any]) -> tuple[str, str, str] | None:
    lower = str(text or "").lower()
    cache_type = str(detail.get("cache_type") or "").strip()
    if not lower:
        return None
    if "lr-ex" in lower or "payload-aware command template" in lower or cache_type == "payload_command_template":
        return (
            "LR-EX payload-aware command template",
            "command preparation; validation",
            "Applied an LR-EX payload-aware command template from execution metadata.",
        )
    if "lr direct" in lower or "lrdirect" in lower:
        return (
            "LR Direct exact-step Python replay" if cache_type == "computation" else "LR Direct exact-step command replay",
            "execution/result reuse" if cache_type == "computation" else "command preparation; validation",
            "Applied LR Direct exact-step replay evidence from execution metadata.",
        )
    if "reused learned command template" in lower or "learned command template" in lower:
        return (
            "learned command template",
            "command preparation; validation",
            "Applied a learned command template from execution metadata.",
        )
    return None


def _learning_summary_add_artifact(
    summary: dict[str, Any],
    rows_by_key: dict[str, dict[str, Any]],
    value: Any,
    *,
    key_prefix: str,
    index: int = 0,
) -> None:
    detail: dict[str, Any] = {}
    if isinstance(value, dict):
        metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
        nested_detail = value.get("detail") if isinstance(value.get("detail"), dict) else {}
        detail = {**metadata, **nested_detail, **value}
    text = " ".join(_learning_summary_unique([
        *_learning_summary_text_parts(value),
        *_learning_summary_text_parts(detail),
    ]))
    signal = _learning_artifact_signal(text, detail)
    if signal is None:
        return
    label, benefited_steps, learned = signal
    ids = _learning_summary_ids(detail, text)
    _learning_summary_add_row(
        summary,
        rows_by_key,
        key=f"{key_prefix}:{label}:{'|'.join(ids) or index}",
        category="applied",
        label=label,
        ids=ids,
        benefited_steps=benefited_steps,
        learned=learned,
        status=str(detail.get("status") or detail.get("outcome") or ""),
    )


def _learning_summary_from_trace(trace: AgentRequestTrace | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    summary = _learning_summary_empty()
    rows_by_key: dict[str, dict[str, Any]] = {}
    request_id = str(getattr(trace, "request_id", "") or "")
    events = list(getattr(trace, "events", []) or [])
    event_types = {str(getattr(event, "event_type", "") or "") for event in events}
    has_lrt_hit = "operator.lrt.hit" in event_types
    has_lrdirect_hit = "operator.lrdirect.hit" in event_types
    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        stage = str(getattr(event, "stage", "") or "")
        detail = getattr(event, "detail", None)
        detail = detail if isinstance(detail, dict) else {}
        status = _learning_summary_safe_text(
            detail.get("status") or detail.get("decision") or getattr(event, "title", "") or event_type,
            limit=140,
        )
        ids = _learning_summary_ids(detail)
        if stage == "memory_check" or event_type == "operator.validation.adjudicated":
            memory_ids = detail.get("memory_ids") if isinstance(detail.get("memory_ids"), list) else []
            use_count = int(detail.get("memory_use_count") or detail.get("memory_count") or len(memory_ids) or 0)
            if use_count > 0:
                _learning_summary_add_row(
                    summary,
                    rows_by_key,
                    key=f"{request_id}:memory:{'|'.join(map(str, memory_ids)) or use_count}",
                    category="applied",
                    label="Previous memory",
                    count=use_count,
                    ids=[str(item) for item in memory_ids],
                    benefited_steps="memory check; downstream planning",
                    learned=f"Applied {use_count} previous memory use{'s' if use_count != 1 else ''}.",
                    status=status,
                )
                continue
        if event_type in {"operator.lrdirect.hit", "operator.command_template_cache.hit"}:
            label = "LR-D applied" if event_type == "operator.lrdirect.hit" else "learned command template"
            if str(detail.get("cache_type") or "") == "payload_command_template":
                label = "LR-EX payload-aware command template"
            learned = (
                "Replayed exact-step cache for this execution step."
                if event_type == "operator.lrdirect.hit"
                else "Applied learned command replay evidence."
            )
            _learning_summary_add_row(
                summary,
                rows_by_key,
                key=f"{request_id}:{event_type}:{'|'.join(ids) or getattr(event, 'event_id', '')}",
                category="applied",
                label=label,
                ids=ids,
                benefited_steps="command preparation; validation",
                learned=learned,
                status=status,
            )
            continue
        if event_type in {"operator.lrt.hit", "operator.cache.hit", "operator.conversation.plan_proposed"}:
            task_count = int(detail.get("task_count") or 0) if str(detail.get("task_count") or "").isdigit() else 0
            task_text = f"{task_count}-step" if task_count > 0 else "learned"
            _learning_summary_add_row(
                summary,
                rows_by_key,
                key=f"{request_id}:{event_type}:{'|'.join(ids) or getattr(event, 'event_id', '')}",
                category="applied",
                label="LR-T applied" if event_type == "operator.lrt.hit" else "learned plan cache",
                ids=ids,
                benefited_steps="decomposition; semantic verb assignment" if event_type == "operator.lrt.hit" else "planning",
                learned=(
                    f"Reused a {task_text} task structure for decomposition and semantic verbs."
                    if event_type == "operator.lrt.hit"
                    else "Applied learned runtime state."
                ),
                status=status,
            )
            continue
        if event_type in {"operator.lrt.lookup", "operator.lrdirect.lookup", "operator.command_template_cache.lookup"}:
            if event_type == "operator.lrt.lookup" and has_lrt_hit:
                continue
            if event_type == "operator.lrdirect.lookup" and has_lrdirect_hit:
                continue
            category = "retrieved"
            label = "learned runtime lookup"
            benefited = "planning"
            learned = "Checked learned runtime candidates."
            row_status = status
            if event_type == "operator.lrt.lookup":
                category = "not_applied"
                label = "LR-T not applied"
                benefited = "decomposition"
                learned = _learning_summary_lrt_not_applied_text(detail)
                row_status = "threshold"
            elif event_type == "operator.lrdirect.lookup":
                category = "not_applied"
                label = "LR-D not applied"
                benefited = "exact-step reuse"
                learned = _learning_summary_lrdirect_not_applied_text(detail)
                row_status = "no replay"
            _learning_summary_add_row(
                summary,
                rows_by_key,
                key=f"{request_id}:{event_type}",
                category=category,
                label=label,
                ids=ids,
                benefited_steps=benefited,
                learned=learned,
                status=row_status,
            )
            continue
        if event_type in {"operator.lrnt.write", "operator.lrdirect.write", "operator.command_template_cache.write"}:
            _learning_summary_add_row(
                summary,
                rows_by_key,
                key=f"{request_id}:{event_type}:{'|'.join(ids) or getattr(event, 'event_id', '')}",
                category="learned",
                label="LRN learned",
                ids=ids,
                benefited_steps="future runs",
                learned="Saved reusable task/step actions for future runs.",
                status=status,
            )
            continue
        _learning_summary_add_artifact(
            summary,
            rows_by_key,
            {
                "event_type": event_type,
                "title": getattr(event, "title", ""),
                "summary": getattr(event, "summary", ""),
                "detail": detail,
            },
            key_prefix=f"{request_id}:event-artifact:{event_type}",
            index=int(getattr(event, "id", 0) or 0),
        )
    metadata = getattr(trace, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    memory_count = int(metadata.get("agent_memory_use_count") or metadata.get("agent_memory_count") or 0)
    if memory_count > 0:
        _learning_summary_add_row(
            summary,
            rows_by_key,
            key=f"{request_id}:metadata-memory:{memory_count}",
            category="applied",
            label="Previous memory",
            count=memory_count,
            ids=[],
            benefited_steps="memory check; downstream planning",
            learned=f"Applied {memory_count} previous memory use{'s' if memory_count != 1 else ''} from trace metadata.",
            status="metadata",
        )
    for group_name in ("operator_execution_records", "operator_confirmation_actions"):
        records = metadata.get(group_name)
        if isinstance(records, list):
            for index, record in enumerate(records):
                _learning_summary_add_artifact(
                    summary,
                    rows_by_key,
                    record,
                    key_prefix=f"{request_id}:metadata:{group_name}",
                    index=index,
                )
    summary["rows"] = list(rows_by_key.values())
    for row in summary["rows"]:
        category = str(row.get("category") or "proposed")
        summary["counts"][category] = int(summary["counts"].get(category, 0)) + int(row.get("count") or 1)
    summary["event_count"] = len(summary["rows"])
    return summary if summary["rows"] else None


@dataclass
class AgentConversationTurn:
    """One durable request/response turn for chat restore and follow-ups."""

    request_id: str
    prompt: str
    final_response: str = ""
    status: str = "completed"
    confirmation_required: bool = False
    clarification_required: bool = False
    display_document: dict[str, Any] | None = None
    response_metrics: dict[str, Any] | None = None
    learning_summary: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AgentConversation:
    """Durable Agent UI conversation state."""

    conversation_id: str
    title: str = ""
    turns: list[AgentConversationTurn] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_request_id: str = ""
    last_status: str = ""
    last_preview: str = ""
    last_response_metrics: dict[str, Any] | None = None
    last_confirmation_required: bool = False
    last_clarification_required: bool = False
    turn_count: int = 0


class AgentConversationStore:
    """Persist Agent UI chat history in SQLite."""

    def __init__(self, db_path: str | Path, *, max_turns_per_conversation: int = 500) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_turns_per_conversation = max(1, int(max_turns_per_conversation or 500))
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
                CREATE TABLE IF NOT EXISTS chats (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_request_id TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '',
                    last_preview TEXT NOT NULL DEFAULT '',
                    turn_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_chats_updated
                ON chats(updated_at DESC);

                CREATE TABLE IF NOT EXISTS chat_turns (
                    request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '',
                    final_response TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'completed',
                    confirmation_required INTEGER NOT NULL DEFAULT 0,
                    clarification_required INTEGER NOT NULL DEFAULT 0,
                    display_document_json TEXT NOT NULL DEFAULT '',
                    learning_summary_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES chats(conversation_id)
                    ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chat_turns_conversation
                ON chat_turns(conversation_id, created_at ASC);
                """
            )
            self._ensure_chat_turn_columns(connection)
            ensure_store_schema_version(
                connection,
                store_name="agent_chats",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_chat_turn_columns(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(chat_turns)").fetchall()
        }
        if "response_metrics_json" not in columns:
            connection.execute(
                "ALTER TABLE chat_turns ADD COLUMN response_metrics_json TEXT NOT NULL DEFAULT ''"
            )
        if "learning_summary_json" not in columns:
            connection.execute(
                "ALTER TABLE chat_turns ADD COLUMN learning_summary_json TEXT NOT NULL DEFAULT ''"
            )

    def create(self, prompt: str = "") -> str:
        conversation_id = new_id("conv")
        now = utc_now_iso()
        title = self.title_from_prompt(prompt)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chats (
                    conversation_id, title, created_at, updated_at,
                    last_request_id, last_status, last_preview, turn_count
                ) VALUES (?, ?, ?, ?, '', '', '', 0)
                """,
                (conversation_id, title, now, now),
            )
        return conversation_id

    def ensure(self, conversation_id: str | None, *, prompt: str = "") -> str:
        normalized = str(conversation_id or "").strip()
        if normalized and self.exists(normalized):
            return normalized
        return self.create(prompt)

    def exists(self, conversation_id: str | None) -> bool:
        normalized = str(conversation_id or "").strip()
        if not normalized:
            return False
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM chats WHERE conversation_id = ?",
                (normalized,),
            ).fetchone()
        return row is not None

    def get(self, conversation_id: str | None) -> AgentConversation | None:
        normalized = str(conversation_id or "").strip()
        if not normalized:
            return None
        with self._lock, self._connect() as connection:
            chat_row = connection.execute(
                "SELECT * FROM chats WHERE conversation_id = ?",
                (normalized,),
            ).fetchone()
            if chat_row is None:
                return None
            turn_rows = connection.execute(
                """
                SELECT * FROM chat_turns
                WHERE conversation_id = ?
                ORDER BY created_at ASC, request_id ASC
                """,
                (normalized,),
            ).fetchall()
        chat = self._row_to_chat(chat_row)
        chat.turns = [self._row_to_turn(row) for row in turn_rows]
        return chat

    def get_turn(self, request_id: str | None) -> AgentConversationTurn | None:
        """Return one durable turn by request id."""

        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM chat_turns WHERE request_id = ?",
                (normalized,),
            ).fetchone()
        return self._row_to_turn(row) if row is not None else None

    def list(self, *, limit: int = 50) -> list[AgentConversation]:
        safe_limit = max(1, min(int(limit or 50), 200))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    chats.*,
                    chat_turns.response_metrics_json AS last_response_metrics_json,
                    chat_turns.confirmation_required AS last_confirmation_required,
                    chat_turns.clarification_required AS last_clarification_required
                FROM chats
                LEFT JOIN chat_turns
                    ON chat_turns.request_id = chats.last_request_id
                ORDER BY updated_at DESC, conversation_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._row_to_chat(row) for row in rows]

    def delete(self, conversation_id: str | None) -> bool:
        normalized = str(conversation_id or "").strip()
        if not normalized:
            return False
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM chat_turns WHERE conversation_id = ?",
                (normalized,),
            )
            cursor = connection.execute(
                "DELETE FROM chats WHERE conversation_id = ?",
                (normalized,),
            )
            return cursor.rowcount > 0

    def append_turn(self, conversation_id: str | None, trace: AgentRequestTrace | None) -> None:
        if not conversation_id or trace is None:
            return
        final_response = str(trace.final_response or trace.error or "").strip()
        self.upsert_turn(
            conversation_id,
            request_id=trace.request_id,
            prompt=trace.prompt,
            final_response=final_response,
            status=trace.status,
            confirmation_required=trace.confirmation_required,
            clarification_required=trace.clarification_required,
            display_document=trace.display_document,
            response_metrics=trace.response_metrics,
            learning_summary=_learning_summary_from_trace(trace),
            created_at=trace.created_at,
        )

    def upsert_turn(
        self,
        conversation_id: str | None,
        *,
        request_id: str,
        prompt: str,
        final_response: str = "",
        status: str = "completed",
        confirmation_required: bool = False,
        clarification_required: bool = False,
        display_document: dict[str, Any] | None = None,
        response_metrics: dict[str, Any] | None = None,
        learning_summary: dict[str, Any] | None = None,
        created_at: str = "",
    ) -> None:
        normalized_conversation_id = str(conversation_id or "").strip()
        normalized_request_id = str(request_id or "").strip()
        if not normalized_conversation_id or not normalized_request_id:
            return
        now = utc_now_iso()
        turn_created_at = str(created_at or now)
        prompt_text = str(prompt or "")
        response_text = _clip_preserve_lines(final_response, limit=24000)
        preview = _clip_words(response_text or prompt_text, limit=300)
        title = self.title_from_prompt(prompt_text)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO chats (
                    conversation_id, title, created_at, updated_at,
                    last_request_id, last_status, last_preview, turn_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    title = CASE
                        WHEN chats.title = '' THEN excluded.title
                        ELSE chats.title
                    END,
                    updated_at = excluded.updated_at,
                    last_request_id = excluded.last_request_id,
                    last_status = excluded.last_status,
                    last_preview = excluded.last_preview
                """,
                (
                    normalized_conversation_id,
                    title,
                    turn_created_at,
                    now,
                    normalized_request_id,
                    str(status or ""),
                    preview,
                ),
            )
            connection.execute(
                """
                INSERT INTO chat_turns (
                    request_id, conversation_id, prompt, final_response, status,
                    confirmation_required, clarification_required, display_document_json,
                    response_metrics_json, learning_summary_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    prompt = excluded.prompt,
                    final_response = excluded.final_response,
                    status = excluded.status,
                    confirmation_required = excluded.confirmation_required,
                    clarification_required = excluded.clarification_required,
                    display_document_json = excluded.display_document_json,
                    response_metrics_json = excluded.response_metrics_json,
                    learning_summary_json = excluded.learning_summary_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_request_id,
                    normalized_conversation_id,
                    prompt_text,
                    response_text,
                    str(status or ""),
                    1 if confirmation_required else 0,
                    1 if clarification_required else 0,
                    _json_dumps(display_document),
                    _json_dumps(response_metrics),
                    _json_dumps(learning_summary),
                    turn_created_at,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE chats
                SET turn_count = (
                    SELECT COUNT(*) FROM chat_turns
                    WHERE conversation_id = chats.conversation_id
                )
                WHERE conversation_id = ?
                """,
                (normalized_conversation_id,),
            )
            self._prune_turns_locked(connection, normalized_conversation_id)

    def _prune_turns_locked(self, connection: sqlite3.Connection, conversation_id: str) -> None:
        connection.execute(
            """
            DELETE FROM chat_turns
            WHERE conversation_id = ?
              AND request_id NOT IN (
                SELECT request_id FROM chat_turns
                WHERE conversation_id = ?
                ORDER BY created_at DESC, request_id DESC
                LIMIT ?
              )
            """,
            (conversation_id, conversation_id, self.max_turns_per_conversation),
        )
        connection.execute(
            """
            UPDATE chats
            SET turn_count = (
                SELECT COUNT(*) FROM chat_turns
                WHERE conversation_id = chats.conversation_id
            )
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )

    @staticmethod
    def title_from_prompt(prompt: str) -> str:
        title = _clip_words(prompt, limit=80)
        return title or "Untitled chat"

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> AgentConversationTurn:
        payload = dict(row)
        return AgentConversationTurn(
            request_id=str(payload.get("request_id") or ""),
            prompt=str(payload.get("prompt") or ""),
            final_response=str(payload.get("final_response") or ""),
            status=str(payload.get("status") or ""),
            confirmation_required=bool(payload.get("confirmation_required")),
            clarification_required=bool(payload.get("clarification_required")),
            display_document=_json_loads(payload.get("display_document_json")),
            response_metrics=_json_loads(payload.get("response_metrics_json")),
            learning_summary=_json_loads(payload.get("learning_summary_json")),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
        )

    @staticmethod
    def _row_to_chat(row: sqlite3.Row) -> AgentConversation:
        payload = dict(row)
        return AgentConversation(
            conversation_id=str(payload.get("conversation_id") or ""),
            title=str(payload.get("title") or ""),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            last_request_id=str(payload.get("last_request_id") or ""),
            last_status=str(payload.get("last_status") or ""),
            last_preview=str(payload.get("last_preview") or ""),
            last_response_metrics=_json_loads(payload.get("last_response_metrics_json")),
            last_confirmation_required=bool(payload.get("last_confirmation_required")),
            last_clarification_required=bool(payload.get("last_clarification_required")),
            turn_count=int(payload.get("turn_count") or 0),
        )


__all__ = [
    "AgentConversation",
    "AgentConversationStore",
    "AgentConversationTurn",
]
