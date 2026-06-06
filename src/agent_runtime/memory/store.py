"""SQLite-backed persistent agent memory store."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.memory.models import (
    MemoryAuditEvent,
    MemoryEntry,
    MemoryEntryCreate,
    MemoryEntryUpdate,
    MemoryProposal,
    MemoryRetrievalContext,
    MemoryRetrievalMatch,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def normalize_model_family(model_name: str) -> str:
    """Return a stable coarse family for model-scoped memory fallback."""

    value = str(model_name or "").strip().lower()
    if not value:
        return ""
    value = value.rsplit("/", 1)[-1]
    value = re.sub(r"\b(?:awq|gptq|gguf|bnb|int4|int8|fp8|fp16|bf16)\b", "", value)
    value = re.sub(r"[-_]*(?:instruct|chat|coder|thinking|base)$", "", value)
    value = re.sub(r"[-_]+", "-", value).strip("-")
    return value or str(model_name or "").strip().lower()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


_MEMORY_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "any",
    "are",
    "action",
    "actions",
    "answer",
    "answers",
    "because",
    "before",
    "being",
    "can",
    "command",
    "commands",
    "computed",
    "could",
    "did",
    "does",
    "failed",
    "failure",
    "for",
    "format",
    "formatted",
    "fresh",
    "from",
    "had",
    "has",
    "have",
    "how",
    "into",
    "its",
    "just",
    "like",
    "may",
    "must",
    "not",
    "now",
    "off",
    "one",
    "only",
    "operator",
    "or",
    "our",
    "out",
    "output",
    "outputs",
    "over",
    "post",
    "read",
    "request",
    "requested",
    "result",
    "results",
    "runtime",
    "shell",
    "should",
    "state",
    "status",
    "stderr",
    "stdout",
    "success",
    "successful",
    "that",
    "the",
    "then",
    "there",
    "this",
    "use",
    "used",
    "using",
    "verification",
    "verified",
    "verify",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "you",
}


def _tokens(value: str) -> set[str]:
    """Return meaningful lowercase tokens for memory relevance."""

    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 1 and token not in _MEMORY_STOPWORDS
    }


def _field_slug(value: str) -> str:
    """Normalize structured retrieval field values for exact comparisons."""

    return str(value or "").strip().lower()


def _entry_tokens(entry: MemoryEntry) -> set[str]:
    """Return all searchable tokens for one memory entry."""

    return _tokens(
        " ".join(
            [
                entry.instruction,
                entry.summary,
                entry.task_type,
                entry.tool_type,
                entry.intent_type,
                entry.memory_kind,
                entry.validator_error_type,
                " ".join(entry.tags),
                " ".join(entry.safe_examples),
                " ".join(entry.blocked_examples),
            ]
        )
    )


def _dedupe_key(entry: MemoryEntry) -> str:
    """Return a coarse key for duplicate memory suppression."""

    text = f"{entry.memory_kind} {entry.task_type} {entry.tool_type} {entry.intent_type} {entry.instruction}"
    tokens = sorted(_tokens(text))
    return " ".join(tokens[:40])


class AgentMemoryStore:
    """Durable SQLite store for user-approved and proposed agent memories."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    memory_id TEXT PRIMARY KEY,
                    instruction TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    memory_kind TEXT NOT NULL DEFAULT 'task_memory',
                    scope TEXT NOT NULL DEFAULT 'global',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    task_type TEXT NOT NULL DEFAULT '',
                    tool_type TEXT NOT NULL DEFAULT '',
                    intent_type TEXT NOT NULL DEFAULT '',
                    validator_error_type TEXT NOT NULL DEFAULT '',
                    safe_examples_json TEXT NOT NULL DEFAULT '[]',
                    blocked_examples_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    provenance TEXT NOT NULL DEFAULT 'manual',
                    request_id TEXT NOT NULL DEFAULT '',
                    rationale TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS memory_audit_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL DEFAULT '',
                    proposal_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL DEFAULT 'user',
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS memory_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    proposal_type TEXT NOT NULL DEFAULT 'create',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    memory_id TEXT NOT NULL DEFAULT '',
                    draft_json TEXT NOT NULL DEFAULT '{}',
                    rationale TEXT NOT NULL DEFAULT '',
                    provenance TEXT NOT NULL DEFAULT 'feedback',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memory_entries_status ON memory_entries(status);
                CREATE INDEX IF NOT EXISTS idx_memory_entries_scope ON memory_entries(scope, model_name, model_family);
                CREATE INDEX IF NOT EXISTS idx_memory_proposals_status ON memory_proposals(status);
                """
            )
            self._ensure_entry_column(conn, "memory_kind", "TEXT NOT NULL DEFAULT 'task_memory'")
            self._ensure_entry_column(conn, "validator_error_type", "TEXT NOT NULL DEFAULT ''")
            self._ensure_entry_column(conn, "safe_examples_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_entry_column(conn, "blocked_examples_json", "TEXT NOT NULL DEFAULT '[]'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_entries_kind ON memory_entries(memory_kind)"
            )
            ensure_store_schema_version(
                conn,
                store_name="agent_memory",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_entry_column(conn: sqlite3.Connection, column_name: str, ddl: str) -> None:
        """Add one memory_entries column for existing local databases."""

        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(memory_entries)").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE memory_entries ADD COLUMN {column_name} {ddl}")

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
        payload = dict(row)
        payload["tags"] = _json_loads(payload.pop("tags_json", "[]"), [])
        payload["safe_examples"] = _json_loads(payload.pop("safe_examples_json", "[]"), [])
        payload["blocked_examples"] = _json_loads(payload.pop("blocked_examples_json", "[]"), [])
        return MemoryEntry.model_validate(payload)

    @staticmethod
    def _row_to_audit(row: sqlite3.Row) -> MemoryAuditEvent:
        payload = dict(row)
        payload["payload"] = _json_loads(payload.pop("payload_json", "{}"), {})
        return MemoryAuditEvent.model_validate(payload)

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> MemoryProposal:
        payload = dict(row)
        payload["draft"] = _json_loads(payload.pop("draft_json", "{}"), {})
        return MemoryProposal.model_validate(payload)

    def _audit(
        self,
        conn: sqlite3.Connection,
        *,
        event_type: str,
        memory_id: str = "",
        proposal_id: str = "",
        actor: str = "user",
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_audit_events
            (event_id, memory_id, proposal_id, event_type, actor, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("mem_evt"),
                str(memory_id or ""),
                str(proposal_id or ""),
                str(event_type or "memory.event"),
                str(actor or "user"),
                utc_now_iso(),
                _json_dumps(payload or {}),
            ),
        )

    def create_entry(self, payload: MemoryEntryCreate, *, actor: str = "user") -> MemoryEntry:
        now = utc_now_iso()
        memory_id = new_id("mem")
        model_family = payload.model_family or normalize_model_family(payload.model_name)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries
                (memory_id, instruction, summary, status, memory_kind, scope, model_name, model_family,
                 task_type, tool_type, intent_type, validator_error_type, safe_examples_json,
                 blocked_examples_json, tags_json, provenance, request_id,
                 rationale, created_at, updated_at, use_count, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '')
                """,
                (
                    memory_id,
                    payload.instruction.strip(),
                    payload.summary.strip(),
                    payload.status,
                    payload.memory_kind,
                    payload.scope,
                    payload.model_name.strip(),
                    model_family.strip(),
                    payload.task_type.strip(),
                    payload.tool_type.strip(),
                    payload.intent_type.strip(),
                    payload.validator_error_type.strip(),
                    _json_dumps(payload.safe_examples),
                    _json_dumps(payload.blocked_examples),
                    _json_dumps(payload.tags),
                    payload.provenance,
                    payload.request_id.strip(),
                    payload.rationale.strip(),
                    now,
                    now,
                ),
            )
            self._audit(conn, event_type="memory.created", memory_id=memory_id, actor=actor, payload=payload.model_dump(mode="json"))
        entry = self.get_entry(memory_id)
        if entry is None:  # pragma: no cover - defensive
            raise RuntimeError("Memory entry was not created.")
        return entry

    def get_entry(self, memory_id: str) -> MemoryEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_entries WHERE memory_id = ?",
                (str(memory_id or ""),),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def list_entries(
        self,
        *,
        status: str | None = None,
        query: str = "",
        memory_kind: str = "",
        scope: str = "",
        model_name: str = "",
        model_family: str = "",
        task_type: str = "",
        tool_type: str = "",
        intent_type: str = "",
        validator_error_type: str = "",
        tag: str = "",
    ) -> list[MemoryEntry]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if memory_kind:
            clauses.append("memory_kind = ?")
            params.append(memory_kind)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if model_name:
            clauses.append("model_name = ?")
            params.append(model_name)
        if model_family:
            clauses.append("model_family = ?")
            params.append(model_family)
        if task_type:
            clauses.append("task_type = ?")
            params.append(task_type)
        if tool_type:
            clauses.append("tool_type = ?")
            params.append(tool_type)
        if intent_type:
            clauses.append("intent_type = ?")
            params.append(intent_type)
        if validator_error_type:
            clauses.append("validator_error_type = ?")
            params.append(validator_error_type)
        normalized_tag = str(tag or "").strip().lower()
        if normalized_tag:
            clauses.append("lower(tags_json) LIKE ?")
            params.append(f"%{normalized_tag}%")
        normalized_query = str(query or "").strip().lower()
        if normalized_query:
            clauses.append(
                "(lower(instruction) LIKE ? OR lower(summary) LIKE ? OR lower(tags_json) LIKE ?)"
            )
            like = f"%{normalized_query}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_entries {where} ORDER BY updated_at DESC, created_at DESC",
                params,
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def update_entry(
        self,
        memory_id: str,
        payload: MemoryEntryUpdate,
        *,
        actor: str = "user",
    ) -> MemoryEntry | None:
        current = self.get_entry(memory_id)
        if current is None:
            return None
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return current
        if "model_family" not in data and "model_name" in data:
            data["model_family"] = normalize_model_family(str(data.get("model_name") or ""))
        allowed = {
            "instruction",
            "summary",
            "memory_kind",
            "scope",
            "model_name",
            "model_family",
            "task_type",
            "tool_type",
            "intent_type",
            "validator_error_type",
            "rationale",
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key in sorted(allowed):
            if key in data and data[key] is not None:
                assignments.append(f"{key} = ?")
                params.append(str(data[key]).strip())
        if "tags" in data and data["tags"] is not None:
            assignments.append("tags_json = ?")
            params.append(_json_dumps(list(data["tags"])))
        if "safe_examples" in data and data["safe_examples"] is not None:
            assignments.append("safe_examples_json = ?")
            params.append(_json_dumps(list(data["safe_examples"])))
        if "blocked_examples" in data and data["blocked_examples"] is not None:
            assignments.append("blocked_examples_json = ?")
            params.append(_json_dumps(list(data["blocked_examples"])))
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(memory_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE memory_entries SET {', '.join(assignments)} WHERE memory_id = ?",
                params,
            )
            self._audit(conn, event_type="memory.updated", memory_id=memory_id, actor=actor, payload=data)
        return self.get_entry(memory_id)

    def set_status(self, memory_id: str, status: str, *, actor: str = "user") -> MemoryEntry | None:
        if status not in {"active", "proposed", "retired"}:
            raise ValueError(f"Unsupported memory status: {status}")
        if self.get_entry(memory_id) is None:
            return None
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_entries SET status = ?, updated_at = ? WHERE memory_id = ?",
                (status, utc_now_iso(), memory_id),
            )
            self._audit(conn, event_type=f"memory.{status}", memory_id=memory_id, actor=actor)
        return self.get_entry(memory_id)

    def audit_events(self, memory_id: str) -> list[MemoryAuditEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_audit_events
                WHERE memory_id = ?
                ORDER BY timestamp ASC
                """,
                (str(memory_id or ""),),
            ).fetchall()
        return [self._row_to_audit(row) for row in rows]

    def create_proposal(
        self,
        *,
        proposal_type: str,
        draft: dict[str, Any],
        memory_id: str = "",
        rationale: str = "",
        provenance: str = "feedback",
        actor: str = "llm",
    ) -> MemoryProposal:
        now = utc_now_iso()
        proposal_id = new_id("memprop")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_proposals
                (proposal_id, proposal_type, status, memory_id, draft_json, rationale,
                 provenance, created_at, updated_at)
                VALUES (?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    proposal_type,
                    str(memory_id or ""),
                    _json_dumps(draft),
                    str(rationale or ""),
                    str(provenance or "feedback"),
                    now,
                    now,
                ),
            )
            self._audit(conn, event_type="memory.proposal.created", proposal_id=proposal_id, actor=actor, payload=draft)
        proposal = self.get_proposal(proposal_id)
        if proposal is None:  # pragma: no cover - defensive
            raise RuntimeError("Memory proposal was not created.")
        return proposal

    def list_proposals(self, *, status: str | None = "proposed") -> list[MemoryProposal]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_proposals {where} ORDER BY updated_at DESC, created_at DESC",
                params,
            ).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get_proposal(self, proposal_id: str) -> MemoryProposal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id = ?",
                (str(proposal_id or ""),),
            ).fetchone()
        return self._row_to_proposal(row) if row is not None else None

    def apply_proposal(self, proposal_id: str, *, actor: str = "user") -> MemoryEntry | None:
        proposal = self.get_proposal(proposal_id)
        if proposal is None or proposal.status != "proposed":
            return None
        entry: MemoryEntry | None = None
        if proposal.proposal_type == "create":
            create_payload = MemoryEntryCreate.model_validate(
                {
                    **proposal.draft,
                    "status": "active",
                    "provenance": proposal.provenance,
                }
            )
            entry = self.create_entry(create_payload, actor=actor)
        elif proposal.proposal_type == "update" and proposal.memory_id:
            entry = self.update_entry(
                proposal.memory_id,
                MemoryEntryUpdate.model_validate(proposal.draft),
                actor=actor,
            )
        elif proposal.proposal_type == "retire" and proposal.memory_id:
            entry = self.set_status(proposal.memory_id, "retired", actor=actor)
        elif proposal.proposal_type == "restore" and proposal.memory_id:
            entry = self.set_status(proposal.memory_id, "active", actor=actor)
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_proposals SET status = 'applied', updated_at = ? WHERE proposal_id = ?",
                (utc_now_iso(), proposal_id),
            )
            self._audit(
                conn,
                event_type="memory.proposal.applied",
                memory_id=entry.memory_id if entry is not None else proposal.memory_id,
                proposal_id=proposal_id,
                actor=actor,
            )
        return entry

    def retrieve_matches(
        self,
        context: MemoryRetrievalContext,
        *,
        record_use: bool = True,
    ) -> list[MemoryRetrievalMatch]:
        """Return relevant memory entries with scoring diagnostics."""

        model_name = str(context.model_name or "").strip()
        family = str(context.model_family or normalize_model_family(model_name)).strip()
        requested_kind = str(context.memory_kind or "").strip()
        requested_error_type = str(context.validator_error_type or "").strip().lower()
        requested_tags = {token for tag in context.tags for token in _tokens(tag)}
        query_tokens = _tokens(
            " ".join(
                [
                    context.prompt,
                    context.task_type,
                    context.tool_type,
                    context.intent_type,
                    str(context.memory_kind or ""),
                    context.validator_error_type,
                    " ".join(context.tags),
                ]
            )
        )
        candidates = self.list_entries(status="active")
        scored: list[MemoryRetrievalMatch] = []
        for entry in candidates:
            entry_kind = str(entry.memory_kind or "task_memory").strip()
            if requested_kind:
                if entry_kind != requested_kind:
                    continue
            elif entry_kind == "validation_policy":
                continue
            structured_mismatched = False
            for attr in ("task_type", "tool_type", "intent_type"):
                requested = _field_slug(str(getattr(context, attr) or ""))
                existing = _field_slug(str(getattr(entry, attr) or ""))
                if requested and existing and requested != existing:
                    structured_mismatched = True
                    break
            if structured_mismatched:
                continue
            if requested_kind == "validation_policy":
                entry_error_type = str(entry.validator_error_type or "").strip().lower()
                if requested_error_type and entry_error_type != requested_error_type:
                    continue
            else:
                entry_error_type = str(entry.validator_error_type or "").strip().lower()
                if requested_error_type and entry_error_type and entry_error_type != requested_error_type:
                    continue
            scope_score = 0
            match_reasons: list[str] = []
            structured_matches: list[str] = []
            if entry.scope == "exact_model":
                if not model_name or entry.model_name != model_name:
                    continue
                scope_score = 300
                match_reasons.append("exact_model")
            elif entry.scope == "model_family":
                if not family or entry.model_family != family:
                    continue
                scope_score = 200
                match_reasons.append("model_family")
            else:
                scope_score = 100
                match_reasons.append("global")
            relevance_score = 0
            structured_match = False
            specific_match = False
            for attr in ("task_type", "intent_type"):
                requested = str(getattr(context, attr) or "").strip().lower()
                existing = str(getattr(entry, attr) or "").strip().lower()
                if requested and existing and requested == existing:
                    relevance_score += 50
                    structured_match = True
                    specific_match = True
                    structured_matches.append(attr)
                    match_reasons.append(f"{attr}:{existing}")
            requested_tool = str(context.tool_type or "").strip().lower()
            existing_tool = str(entry.tool_type or "").strip().lower()
            if requested_tool and existing_tool and requested_tool == existing_tool:
                relevance_score += 30
                structured_match = True
                structured_matches.append("tool_type")
                match_reasons.append(f"tool_type:{existing_tool}")
                specific_match = True
            entry_tags = {token for tag in entry.tags for token in _tokens(tag)}
            tag_overlap = requested_tags & entry_tags
            if tag_overlap:
                relevance_score += 40 * min(3, len(tag_overlap))
                structured_match = True
                specific_match = True
                structured_matches.append("tags")
                match_reasons.append("tags:" + ",".join(sorted(tag_overlap)[:8]))
            entry_tokens = _entry_tokens(entry)
            text_overlap = query_tokens & entry_tokens
            text_overlap_count = len(text_overlap)
            has_structured_context = bool(
                context.task_type or context.tool_type or context.intent_type or requested_tags
            )
            has_structured_entry = bool(
                entry.task_type or entry.tool_type or entry.intent_type or entry_tags
            )
            has_specific_context = bool(context.task_type or context.intent_type or requested_tags)
            has_specific_entry = bool(entry.task_type or entry.intent_type or entry_tags)
            if has_specific_context and has_specific_entry and not specific_match:
                continue
            required_text_overlap = 1 if structured_match else 5
            if has_structured_context and has_structured_entry:
                required_text_overlap = 1 if structured_match else 6
            elif has_structured_context and not has_structured_entry:
                required_text_overlap = 1 if structured_match else 3
            elif has_structured_entry:
                required_text_overlap = 1 if structured_match else 6
            if not structured_match and text_overlap_count < required_text_overlap:
                continue
            if (
                entry.scope == "global"
                and not has_structured_entry
                and not structured_match
                and not tag_overlap
                and text_overlap_count < 3
            ):
                continue
            text_score = min(60, 10 * text_overlap_count)
            relevance_score += text_score
            if text_overlap_count:
                match_reasons.append("text:" + ",".join(sorted(text_overlap)[:8]))
            if relevance_score <= 0:
                continue
            score = scope_score + relevance_score
            scored.append(
                MemoryRetrievalMatch(
                    entry=entry,
                    score=score,
                    scope_score=scope_score,
                    relevance_score=relevance_score,
                    match_reasons=match_reasons,
                    structured_matches=structured_matches,
                    tag_overlap=sorted(tag_overlap),
                    text_overlap=sorted(text_overlap),
                    text_overlap_count=text_overlap_count,
                )
            )
        scored.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        selected: list[MemoryRetrievalMatch] = []
        total_chars = 0
        selected_keys: set[str] = set()
        for match in scored:
            entry = match.entry
            dedupe_key = _dedupe_key(entry)
            if dedupe_key and dedupe_key in selected_keys:
                continue
            entry_chars = len(entry.instruction) + len(entry.summary) + 120
            if selected and total_chars + entry_chars > context.max_chars:
                continue
            selected.append(match)
            if dedupe_key:
                selected_keys.add(dedupe_key)
            total_chars += entry_chars
            if len(selected) >= context.limit:
                break
        if selected and record_use:
            now = utc_now_iso()
            with self._connect() as conn:
                for match in selected:
                    entry = match.entry
                    conn.execute(
                        """
                        UPDATE memory_entries
                        SET use_count = use_count + 1, last_used_at = ?, updated_at = updated_at
                        WHERE memory_id = ?
                        """,
                        (now, entry.memory_id),
                    )
                    self._audit(conn, event_type="memory.used", memory_id=entry.memory_id, actor="runtime")
            selected = [
                match.model_copy(
                    update={
                        "entry": match.entry.model_copy(
                            update={"use_count": match.entry.use_count + 1, "last_used_at": now}
                        )
                    }
                )
                for match in selected
            ]
        return selected

    def retrieve(self, context: MemoryRetrievalContext, *, record_use: bool = True) -> list[MemoryEntry]:
        matches = self.retrieve_matches(context, record_use=record_use)
        selected = [match.entry for match in matches]
        return selected
