"""SQLite store for user-guided learning ledger evidence and lessons."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.learning_ledger.models import (
    CapabilityInsight,
    CapabilityInsightWrite,
    CapabilityProposal,
    CapabilityProposalStatus,
    CapabilityProposalWrite,
    LearningActionOutcome,
    LearningActionWrite,
    LearningCacheEvent,
    LearningCacheEventWrite,
    LearningLesson,
    LearningLessonStatus,
    LearningLessonWrite,
    LearningRun,
    LearningRunWrite,
    LearningSummary,
)
from agent_runtime.memory.models import MemoryEntryCreate, MemoryEntryUpdate
from agent_runtime.memory.store import AgentMemoryStore, normalize_model_family
from agent_runtime.prompts.store import (
    PromptTemplateRecord,
    PromptTemplateStore,
    extract_template_variables,
    validate_template_body,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    except Exception:
        return "{}" if isinstance(value, dict) else "[]"


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _clip(value: Any, *, limit: int = 4000) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _unsafe_learning_text(value: str) -> bool:
    text = str(value or "").lower()
    unsafe_fragments = (
        "skip approval",
        "skip-approval",
        "bypass approval",
        "bypass-approval",
        "disable approval",
        "skip validation",
        "skip-validation",
        "bypass validation",
        "bypass-validation",
        "disable validation",
        "ignore safety",
        "ignore policy",
        "disable sandbox",
        "bypass sandbox",
        "without confirmation",
        "never ask for approval",
    )
    return any(fragment in text for fragment in unsafe_fragments)


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
)
_PROMPT_GUIDANCE_START = "<<<CAPABILITY_EVOLUTION_GUIDANCE:{proposal_id}>>>"
_PROMPT_GUIDANCE_END = "<<<END_CAPABILITY_EVOLUTION_GUIDANCE:{proposal_id}>>>"
_OVERLAY_LIST_FIELDS = {
    "semantic_verbs",
    "object_types",
    "semantic_tags",
    "output_object_types",
    "output_fields",
    "output_affordances",
    "examples",
    "safety_notes",
}
_FORBIDDEN_OVERLAY_FIELDS = {
    "capability_id",
    "operation_id",
    "execution_backend",
    "backend_operation",
    "risk_level",
    "read_only",
    "mutates_state",
    "requires_confirmation",
    "argument_schema",
    "required_arguments",
    "optional_arguments",
    "output_schema",
    "side_effect_type",
}


def _unsafe_capability_text(value: Any) -> bool:
    text = str(value or "").lower()
    if _SECRET_RE.search(text):
        return True
    return _unsafe_learning_text(text) or any(
        fragment in text
        for fragment in (
            "lower risk",
            "reduce risk",
            "mark as read only",
            "mark read-only",
            "remove confirmation",
            "does not require confirmation",
            "hot load",
            "autoload executable",
            "auto apply executable",
        )
    )


def _unique_strings(values: Any, *, limit: int = 40, max_chars: int = 1000) -> list[str]:
    items: list[str] = []
    for item in list(values or []) if isinstance(values, list) else []:
        text = _clip(item, limit=max_chars)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _unique_examples(values: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(values or []) if isinstance(values, list) else []:
        if isinstance(item, dict):
            example = {
                _clip(key, limit=80): value
                for key, value in item.items()
                if _clip(key, limit=80)
            }
        else:
            prompt = _clip(item, limit=1200)
            example = {"prompt": prompt} if prompt else {}
        if not example:
            continue
        fingerprint = _json_dumps(example)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        examples.append(example)
        if len(examples) >= limit:
            break
    return examples


def _proposal_text(payload: CapabilityProposalWrite | CapabilityProposal) -> str:
    return "\n".join(
        [
            str(payload.title or ""),
            str(payload.summary or ""),
            str(payload.rationale or ""),
            _json_dumps(payload.draft),
        ]
    )


class AgentLearningLedgerStore:
    """Durable evidence ledger for user-guided agent learning."""

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS learning_runs (
                    request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    parent_request_id TEXT NOT NULL DEFAULT '',
                    agent_mode TEXT NOT NULL DEFAULT '',
                    prompt_signature TEXT NOT NULL DEFAULT '',
                    prompt_excerpt TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    gateway_id TEXT NOT NULL DEFAULT '',
                    gateway_nickname TEXT NOT NULL DEFAULT '',
                    gateway_node TEXT NOT NULL DEFAULT '',
                    gateway_platform TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '',
                    workspace_root TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT 'no_learning_needed',
                    duration_ms REAL,
                    input_tokens_estimate INTEGER NOT NULL DEFAULT 0,
                    output_tokens_estimate INTEGER NOT NULL DEFAULT 0,
                    total_tokens_estimate INTEGER NOT NULL DEFAULT 0,
                    llm_call_count INTEGER NOT NULL DEFAULT 0,
                    confirmation_required INTEGER NOT NULL DEFAULT 0,
                    clarification_required INTEGER NOT NULL DEFAULT 0,
                    auto_approved INTEGER NOT NULL DEFAULT 0,
                    memory_ids_json TEXT NOT NULL DEFAULT '[]',
                    cache_ids_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    final_response_preview TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_runs_updated
                ON learning_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_learning_runs_outcome
                ON learning_runs(outcome);

                CREATE TABLE IF NOT EXISTS learning_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    action_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '',
                    command_hash TEXT NOT NULL DEFAULT '',
                    exit_code INTEGER,
                    error_type TEXT NOT NULL DEFAULT '',
                    error_preview TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_actions_request
                ON learning_actions(request_id);

                CREATE TABLE IF NOT EXISTS learning_cache_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    cache_type TEXT NOT NULL,
                    cache_id TEXT NOT NULL,
                    event TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'neutral',
                    outcome TEXT NOT NULL DEFAULT '',
                    score REAL,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_cache_lookup
                ON learning_cache_events(cache_type, cache_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS learning_lessons (
                    lesson_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'draft',
                    lesson_type TEXT NOT NULL DEFAULT 'general',
                    title TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source_request_id TEXT NOT NULL DEFAULT '',
                    mirrored_memory_id TEXT NOT NULL DEFAULT '',
                    auto_approved INTEGER NOT NULL DEFAULT 0,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    rationale TEXT NOT NULL DEFAULT '',
                    rejection_reason TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_lessons_status
                ON learning_lessons(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_learning_lessons_dedupe
                ON learning_lessons(dedupe_key);

                CREATE TABLE IF NOT EXISTS capability_insights (
                    insight_id TEXT PRIMARY KEY,
                    insight_type TEXT NOT NULL,
                    source_request_id TEXT NOT NULL DEFAULT '',
                    source_stage TEXT NOT NULL DEFAULT '',
                    source_event_type TEXT NOT NULL DEFAULT '',
                    source_error_type TEXT NOT NULL DEFAULT '',
                    target_kind TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    prompt_key TEXT NOT NULL DEFAULT '',
                    capability_id TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    gateway_id TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_capability_insights_request
                ON capability_insights(source_request_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capability_insights_dedupe
                ON capability_insights(dedupe_key);

                CREATE TABLE IF NOT EXISTS capability_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'draft',
                    target_kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    rationale TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source TEXT NOT NULL DEFAULT 'deterministic',
                    source_request_id TEXT NOT NULL DEFAULT '',
                    source_insight_id TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    draft_json TEXT NOT NULL DEFAULT '{}',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    safety_decision_json TEXT NOT NULL DEFAULT '{}',
                    auto_approved INTEGER NOT NULL DEFAULT 0,
                    applied_ref TEXT NOT NULL DEFAULT '',
                    apply_error TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_capability_proposals_status
                ON capability_proposals(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capability_proposals_kind
                ON capability_proposals(target_kind, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_capability_proposals_dedupe
                ON capability_proposals(dedupe_key);
                """
            )
            self._ensure_column(
                connection,
                "learning_lessons",
                "auto_approved",
                "INTEGER NOT NULL DEFAULT 0",
            )
            ensure_store_schema_version(
                connection,
                store_name="agent_learning_ledger",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def record_run(self, payload: LearningRunWrite) -> LearningRun:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM learning_runs WHERE request_id = ?",
                (payload.request_id,),
            ).fetchone()
            created_at = str(existing["created_at"]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO learning_runs (
                    request_id, conversation_id, parent_request_id, agent_mode,
                    prompt_signature, prompt_excerpt, model_name, model_family,
                    gateway_id, gateway_nickname, gateway_node, gateway_platform,
                    cwd, workspace_root, status, outcome, duration_ms,
                    input_tokens_estimate, output_tokens_estimate, total_tokens_estimate,
                    llm_call_count, confirmation_required, clarification_required,
                    auto_approved, memory_ids_json, cache_ids_json, error,
                    final_response_preview, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    parent_request_id = excluded.parent_request_id,
                    agent_mode = excluded.agent_mode,
                    prompt_signature = excluded.prompt_signature,
                    prompt_excerpt = excluded.prompt_excerpt,
                    model_name = excluded.model_name,
                    model_family = excluded.model_family,
                    gateway_id = excluded.gateway_id,
                    gateway_nickname = excluded.gateway_nickname,
                    gateway_node = excluded.gateway_node,
                    gateway_platform = excluded.gateway_platform,
                    cwd = excluded.cwd,
                    workspace_root = excluded.workspace_root,
                    status = excluded.status,
                    outcome = excluded.outcome,
                    duration_ms = excluded.duration_ms,
                    input_tokens_estimate = excluded.input_tokens_estimate,
                    output_tokens_estimate = excluded.output_tokens_estimate,
                    total_tokens_estimate = excluded.total_tokens_estimate,
                    llm_call_count = excluded.llm_call_count,
                    confirmation_required = excluded.confirmation_required,
                    clarification_required = excluded.clarification_required,
                    auto_approved = excluded.auto_approved,
                    memory_ids_json = excluded.memory_ids_json,
                    cache_ids_json = excluded.cache_ids_json,
                    error = excluded.error,
                    final_response_preview = excluded.final_response_preview,
                    evidence_json = excluded.evidence_json,
                    updated_at = excluded.updated_at
                """,
                (
                    payload.request_id,
                    payload.conversation_id,
                    payload.parent_request_id,
                    payload.agent_mode,
                    payload.prompt_signature,
                    payload.prompt_excerpt,
                    payload.model_name,
                    payload.model_family,
                    payload.gateway_id,
                    payload.gateway_nickname,
                    payload.gateway_node,
                    payload.gateway_platform,
                    payload.cwd,
                    payload.workspace_root,
                    payload.status,
                    payload.outcome,
                    payload.duration_ms,
                    int(payload.input_tokens_estimate),
                    int(payload.output_tokens_estimate),
                    int(payload.total_tokens_estimate),
                    int(payload.llm_call_count),
                    1 if payload.confirmation_required else 0,
                    1 if payload.clarification_required else 0,
                    1 if payload.auto_approved else 0,
                    _json_dumps(payload.memory_ids),
                    _json_dumps(payload.cache_ids),
                    _clip(payload.error, limit=2000),
                    _clip(payload.final_response_preview, limit=2000),
                    _json_dumps(payload.evidence),
                    created_at,
                    now,
                ),
            )
        run = self.get_run(payload.request_id)
        if run is None:  # pragma: no cover - defensive
            raise RuntimeError("Learning run was not written.")
        return run

    def record_action(self, payload: LearningActionWrite) -> LearningActionOutcome:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO learning_actions (
                    request_id, action_id, task_id, kind, title, status, cwd,
                    command_hash, exit_code, error_type, error_preview,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.request_id,
                    payload.action_id,
                    payload.task_id,
                    payload.kind,
                    _clip(payload.title, limit=500),
                    payload.status,
                    payload.cwd,
                    payload.command_hash,
                    payload.exit_code,
                    payload.error_type,
                    _clip(payload.error_preview, limit=1200),
                    _json_dumps(payload.evidence),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_actions WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._row_to_action(row)

    def record_cache_event(self, payload: LearningCacheEventWrite) -> LearningCacheEvent:
        now = utc_now_iso()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO learning_cache_events (
                    request_id, cache_type, cache_id, event, status, outcome,
                    score, reason, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.request_id,
                    payload.cache_type,
                    payload.cache_id,
                    payload.event,
                    payload.status,
                    payload.outcome,
                    payload.score,
                    _clip(payload.reason, limit=1000),
                    _json_dumps(payload.evidence),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_cache_events WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._row_to_cache_event(row)

    def record_insight(self, payload: CapabilityInsightWrite) -> CapabilityInsight:
        """Persist one normalized capability-evolution evidence item."""

        now = utc_now_iso()
        dedupe_key = str(payload.dedupe_key or "").strip()
        with self._lock, self._connect() as connection:
            existing = None
            if dedupe_key:
                existing = connection.execute(
                    """
                    SELECT * FROM capability_insights
                    WHERE dedupe_key = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    UPDATE capability_insights
                    SET evidence_json = ?, summary = ?, updated_at = ?
                    WHERE insight_id = ?
                    """,
                    (
                        _json_dumps(payload.evidence),
                        _clip(payload.summary, limit=1000),
                        now,
                        str(existing["insight_id"]),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM capability_insights WHERE insight_id = ?",
                    (str(existing["insight_id"]),),
                ).fetchone()
                return self._row_to_insight(row)

            insight_id = new_id("insight")
            connection.execute(
                """
                INSERT INTO capability_insights (
                    insight_id, insight_type, source_request_id, source_stage,
                    source_event_type, source_error_type, target_kind, target_id,
                    prompt_key, capability_id, model_name, model_family, gateway_id,
                    cwd, summary, evidence_json, dedupe_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    insight_id,
                    payload.insight_type,
                    payload.source_request_id,
                    payload.source_stage,
                    payload.source_event_type,
                    payload.source_error_type,
                    payload.target_kind or "",
                    payload.target_id,
                    payload.prompt_key,
                    payload.capability_id,
                    payload.model_name,
                    payload.model_family,
                    payload.gateway_id,
                    payload.cwd,
                    _clip(payload.summary, limit=1000),
                    _json_dumps(payload.evidence),
                    dedupe_key,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM capability_insights WHERE insight_id = ?",
                (insight_id,),
            ).fetchone()
        return self._row_to_insight(row)

    def create_proposal(self, payload: CapabilityProposalWrite) -> CapabilityProposal | None:
        """Create one capability-evolution proposal, deduping by stable key."""

        title = _clip(payload.title, limit=240)
        if not title or _unsafe_capability_text(_proposal_text(payload)):
            return None
        dedupe_key = str(payload.dedupe_key or "").strip()
        with self._lock, self._connect() as connection:
            if dedupe_key:
                existing = connection.execute(
                    """
                    SELECT * FROM capability_proposals
                    WHERE dedupe_key = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
                if existing is not None:
                    return self._row_to_proposal(existing)
            now = utc_now_iso()
            proposal_id = new_id("proposal")
            safety = dict(payload.safety_decision or {})
            safety.setdefault("safe", True)
            connection.execute(
                """
                INSERT INTO capability_proposals (
                    proposal_id, status, target_kind, title, summary, rationale,
                    confidence, source, source_request_id, source_insight_id,
                    target_id, draft_json, evidence_json, safety_decision_json,
                    auto_approved, applied_ref, apply_error, dedupe_key,
                    created_at, updated_at
                ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', '', ?, ?, ?)
                """,
                (
                    proposal_id,
                    payload.target_kind,
                    title,
                    _clip(payload.summary, limit=1000),
                    _clip(payload.rationale, limit=2000),
                    float(payload.confidence),
                    payload.source,
                    payload.source_request_id,
                    payload.source_insight_id,
                    payload.target_id,
                    _json_dumps(payload.draft),
                    _json_dumps(payload.evidence),
                    _json_dumps(safety),
                    dedupe_key,
                    now,
                    now,
                ),
            )
        return self.get_proposal(proposal_id)

    def list_insights(
        self,
        *,
        source_request_id: str = "",
        limit: int = 100,
    ) -> list[CapabilityInsight]:
        safe_limit = max(1, min(int(limit or 100), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if source_request_id:
            clauses.append("source_request_id = ?")
            params.append(str(source_request_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM capability_insights {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_insight(row) for row in rows]

    def list_proposals(
        self,
        *,
        status: str | None = None,
        target_kind: str | None = None,
        source_request_id: str | None = None,
        limit: int = 100,
    ) -> list[CapabilityProposal]:
        safe_limit = max(1, min(int(limit or 100), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        if target_kind:
            clauses.append("target_kind = ?")
            params.append(str(target_kind))
        if source_request_id:
            clauses.append("source_request_id = ?")
            params.append(str(source_request_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM capability_proposals {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get_proposal(self, proposal_id: str) -> CapabilityProposal | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM capability_proposals WHERE proposal_id = ?",
                (str(proposal_id or "").strip(),),
            ).fetchone()
        return self._row_to_proposal(row) if row is not None else None

    def update_proposal(
        self,
        proposal_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        rationale: str | None = None,
        confidence: float | None = None,
        draft: dict[str, Any] | None = None,
    ) -> CapabilityProposal | None:
        current = self.get_proposal(proposal_id)
        if current is None:
            return None
        assignments: list[str] = []
        params: list[Any] = []
        if title is not None:
            cleaned = _clip(title, limit=240)
            if not cleaned or _unsafe_capability_text(cleaned):
                raise ValueError("Capability proposal title is empty or unsafe.")
            assignments.append("title = ?")
            params.append(cleaned)
        if summary is not None:
            assignments.append("summary = ?")
            params.append(_clip(summary, limit=1000))
        if rationale is not None:
            assignments.append("rationale = ?")
            params.append(_clip(rationale, limit=2000))
        if confidence is not None:
            assignments.append("confidence = ?")
            params.append(max(0.0, min(1.0, float(confidence))))
        if draft is not None:
            if _unsafe_capability_text(_json_dumps(draft)):
                raise ValueError("Capability proposal draft weakens safety policy.")
            assignments.append("draft_json = ?")
            params.append(_json_dumps(draft))
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(proposal_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE capability_proposals SET {', '.join(assignments)} WHERE proposal_id = ?",
                params,
            )
        return self.get_proposal(proposal_id)

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        memory_store: AgentMemoryStore | None = None,
        prompt_template_store: PromptTemplateStore | None = None,
        actor: str = "user",
        auto_approved: bool = False,
    ) -> CapabilityProposal | None:
        """Approve one proposal and apply targets that are safe to activate immediately."""

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return None
        if _unsafe_capability_text(_proposal_text(proposal)):
            raise ValueError("Capability proposal weakens safety policy.")

        pending_status = (
            "approved_pending_apply"
            if proposal.target_kind == "executable_backend_patch"
            else "approved"
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE capability_proposals
                SET status = ?, auto_approved = ?, apply_error = '', updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    pending_status,
                    1 if auto_approved else int(bool(proposal.auto_approved)),
                    utc_now_iso(),
                    proposal_id,
                ),
            )
        proposal = self.get_proposal(proposal_id)
        if proposal is None or proposal.target_kind == "executable_backend_patch":
            return proposal
        return self.apply_proposal(
            proposal_id,
            memory_store=memory_store,
            prompt_template_store=prompt_template_store,
            actor=actor,
        )

    def apply_proposal(
        self,
        proposal_id: str,
        *,
        memory_store: AgentMemoryStore | None = None,
        prompt_template_store: PromptTemplateStore | None = None,
        actor: str = "user",
    ) -> CapabilityProposal | None:
        """Apply one approved proposal to its target artifact."""

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return None
        if proposal.status not in {"approved", "approved_pending_apply", "applied"}:
            raise ValueError("Capability proposal must be approved before it can be applied.")
        try:
            if proposal.target_kind in {"task_memory", "validation_policy"}:
                applied_ref = self._apply_memory_proposal(proposal, memory_store, actor=actor)
            elif proposal.target_kind == "prompt_patch":
                applied_ref = self._apply_prompt_patch_proposal(proposal, prompt_template_store)
            elif proposal.target_kind == "capability_manifest_overlay":
                applied_ref = self._apply_manifest_overlay_proposal(proposal)
            else:
                applied_ref = self._apply_executable_backend_patch_proposal(proposal)
        except Exception as exc:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    UPDATE capability_proposals
                    SET apply_error = ?, updated_at = ?
                    WHERE proposal_id = ?
                    """,
                    (_clip(str(exc), limit=2000), utc_now_iso(), proposal_id),
                )
            raise
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE capability_proposals
                SET status = 'applied', applied_ref = ?, apply_error = '', updated_at = ?
                WHERE proposal_id = ?
                """,
                (_clip(applied_ref, limit=500), utc_now_iso(), proposal_id),
            )
        return self.get_proposal(proposal_id)

    def reject_proposal(self, proposal_id: str, *, reason: str = "") -> CapabilityProposal | None:
        return self._set_proposal_status(proposal_id, "rejected", apply_error=reason)

    def retire_proposal(self, proposal_id: str) -> CapabilityProposal | None:
        return self._set_proposal_status(proposal_id, "retired")

    def approved_manifest_overlays(self) -> dict[str, dict[str, Any]]:
        """Return active planner manifest overlays keyed by capability id."""

        overlays: dict[str, dict[str, Any]] = {}
        proposals = self.list_proposals(
            status="applied",
            target_kind="capability_manifest_overlay",
            limit=500,
        )
        for proposal in proposals:
            capability_id = str(
                proposal.draft.get("capability_id")
                or proposal.target_id
                or ""
            ).strip()
            overlay = self._sanitized_manifest_overlay(proposal.draft.get("overlay") or proposal.draft)
            if capability_id and overlay:
                overlays[capability_id] = self._merge_overlay_payloads(
                    overlays.get(capability_id, {}),
                    overlay,
                )
        return overlays

    def create_lesson(self, payload: LearningLessonWrite) -> LearningLesson | None:
        instruction = _clip(payload.instruction, limit=4000)
        title = _clip(payload.title, limit=240)
        if not instruction or _unsafe_learning_text(f"{title}\n{instruction}"):
            return None
        dedupe_key = str(payload.dedupe_key or "").strip()
        with self._lock, self._connect() as connection:
            if dedupe_key:
                existing = connection.execute(
                    """
                    SELECT * FROM learning_lessons
                    WHERE dedupe_key = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (dedupe_key,),
                ).fetchone()
                if existing is not None:
                    return self._row_to_lesson(existing)
            now = utc_now_iso()
            lesson_id = new_id("learn")
            connection.execute(
                """
                INSERT INTO learning_lessons (
                    lesson_id, status, lesson_type, title, instruction, summary,
                    scope_json, evidence_json, confidence, source_request_id,
                    mirrored_memory_id, auto_approved, tags_json, rationale, rejection_reason,
                    dedupe_key, created_at, updated_at
                ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?, ?, '', ?, ?, ?)
                """,
                (
                    lesson_id,
                    payload.lesson_type,
                    title,
                    instruction,
                    _clip(payload.summary, limit=500),
                    _json_dumps(payload.scope),
                    _json_dumps(payload.evidence),
                    float(payload.confidence),
                    payload.source_request_id,
                    _json_dumps(payload.tags),
                    _clip(payload.rationale, limit=1200),
                    dedupe_key,
                    now,
                    now,
                ),
            )
        return self.get_lesson(lesson_id)

    def list_runs(self, *, limit: int = 50, outcome: str = "") -> list[LearningRun]:
        safe_limit = max(1, min(int(limit or 50), 200))
        clauses: list[str] = []
        params: list[Any] = []
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM learning_runs {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def get_run(self, request_id: str) -> LearningRun | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM learning_runs WHERE request_id = ?",
                (str(request_id or ""),),
            ).fetchone()
        return self._row_to_run(row) if row is not None else None

    def run_actions(self, request_id: str) -> list[LearningActionOutcome]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_actions WHERE request_id = ? ORDER BY id ASC",
                (str(request_id or ""),),
            ).fetchall()
        return [self._row_to_action(row) for row in rows]

    def run_cache_events(self, request_id: str) -> list[LearningCacheEvent]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_cache_events WHERE request_id = ? ORDER BY id ASC",
                (str(request_id or ""),),
            ).fetchall()
        return [self._row_to_cache_event(row) for row in rows]

    def list_lessons(self, *, status: str | None = None, limit: int = 100) -> list[LearningLesson]:
        safe_limit = max(1, min(int(limit or 100), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM learning_lessons {where} ORDER BY updated_at DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [self._row_to_lesson(row) for row in rows]

    def get_lesson(self, lesson_id: str) -> LearningLesson | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM learning_lessons WHERE lesson_id = ?",
                (str(lesson_id or ""),),
            ).fetchone()
        return self._row_to_lesson(row) if row is not None else None

    def update_lesson(
        self,
        lesson_id: str,
        *,
        title: str | None = None,
        instruction: str | None = None,
        summary: str | None = None,
        scope: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> LearningLesson | None:
        current = self.get_lesson(lesson_id)
        if current is None:
            return None
        assignments: list[str] = []
        params: list[Any] = []
        if title is not None:
            assignments.append("title = ?")
            params.append(_clip(title, limit=240))
        if instruction is not None:
            cleaned = _clip(instruction, limit=4000)
            if not cleaned or _unsafe_learning_text(cleaned):
                raise ValueError("Learning instruction is empty or weakens safety policy.")
            assignments.append("instruction = ?")
            params.append(cleaned)
        if summary is not None:
            assignments.append("summary = ?")
            params.append(_clip(summary, limit=500))
        if scope is not None:
            assignments.append("scope_json = ?")
            params.append(_json_dumps(scope))
        if tags is not None:
            assignments.append("tags_json = ?")
            params.append(_json_dumps(tags))
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        params.append(utc_now_iso())
        params.append(lesson_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE learning_lessons SET {', '.join(assignments)} WHERE lesson_id = ?",
                params,
            )
        return self.get_lesson(lesson_id)

    def approve_lesson(
        self,
        lesson_id: str,
        *,
        memory_store: AgentMemoryStore,
        actor: str = "user",
        auto_approved: bool = False,
    ) -> LearningLesson | None:
        lesson = self.get_lesson(lesson_id)
        if lesson is None:
            return None
        if _unsafe_learning_text(lesson.instruction):
            raise ValueError("Learning instruction weakens safety policy.")
        tags = list(dict.fromkeys([*lesson.tags, f"learning-ledger:{lesson.lesson_id}"]))
        scope = dict(lesson.scope or {})
        model_name = str(scope.get("model_name") or "")
        mirrored_id = lesson.mirrored_memory_id
        if mirrored_id and memory_store.get_entry(mirrored_id) is not None:
            entry = memory_store.update_entry(
                mirrored_id,
                MemoryEntryUpdate(
                    instruction=lesson.instruction,
                    summary=lesson.summary or lesson.title,
                    memory_kind=self._memory_kind_for_lesson(lesson),
                    scope=str(scope.get("memory_scope") or "global"),  # type: ignore[arg-type]
                    model_name=model_name,
                    model_family=str(scope.get("model_family") or normalize_model_family(model_name)),
                    task_type=str(scope.get("task_type") or ""),
                    tool_type=str(scope.get("tool_type") or ""),
                    intent_type=str(scope.get("intent_type") or ""),
                    validator_error_type=str(scope.get("validator_error_type") or ""),
                    safe_examples=list(lesson.evidence.get("safe_examples") or []),
                    blocked_examples=list(lesson.evidence.get("blocked_examples") or []),
                    tags=tags,
                    rationale=lesson.rationale,
                ),
                actor=actor,
            )
            if entry is not None and entry.status != "active":
                memory_store.set_status(entry.memory_id, "active", actor=actor)
        else:
            entry = memory_store.create_entry(
                MemoryEntryCreate(
                    instruction=lesson.instruction,
                    summary=lesson.summary or lesson.title,
                    status="active",
                    memory_kind=self._memory_kind_for_lesson(lesson),
                    scope=str(scope.get("memory_scope") or "global"),  # type: ignore[arg-type]
                    model_name=model_name,
                    model_family=str(scope.get("model_family") or normalize_model_family(model_name)),
                    task_type=str(scope.get("task_type") or ""),
                    tool_type=str(scope.get("tool_type") or ""),
                    intent_type=str(scope.get("intent_type") or ""),
                    validator_error_type=str(scope.get("validator_error_type") or ""),
                    safe_examples=list(lesson.evidence.get("safe_examples") or []),
                    blocked_examples=list(lesson.evidence.get("blocked_examples") or []),
                    tags=tags,
                    provenance="learning_ledger",
                    request_id=lesson.source_request_id,
                    rationale=lesson.rationale,
                ),
                actor=actor,
            )
            mirrored_id = entry.memory_id
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE learning_lessons
                SET status = 'approved', mirrored_memory_id = ?, auto_approved = ?,
                    rejection_reason = '', updated_at = ?
                WHERE lesson_id = ?
                """,
                (mirrored_id, 1 if auto_approved else int(bool(lesson.auto_approved)), utc_now_iso(), lesson_id),
            )
        return self.get_lesson(lesson_id)

    def reject_lesson(self, lesson_id: str, *, reason: str = "") -> LearningLesson | None:
        return self._set_lesson_status(lesson_id, "rejected", rejection_reason=reason)

    def retire_lesson(
        self,
        lesson_id: str,
        *,
        memory_store: AgentMemoryStore | None = None,
    ) -> LearningLesson | None:
        lesson = self._set_lesson_status(lesson_id, "retired")
        if lesson is not None and memory_store is not None and lesson.mirrored_memory_id:
            memory_store.set_status(lesson.mirrored_memory_id, "retired", actor="user")
        return lesson

    def restore_lesson(
        self,
        lesson_id: str,
        *,
        memory_store: AgentMemoryStore | None = None,
    ) -> LearningLesson | None:
        lesson = self.get_lesson(lesson_id)
        if lesson is None:
            return None
        if memory_store is not None:
            return self.approve_lesson(lesson_id, memory_store=memory_store)
        return self._set_lesson_status(lesson_id, "draft")

    def digest_note(self, lesson_id: str, note: str) -> LearningLesson | None:
        lesson = self.get_lesson(lesson_id)
        note_text = _clip(note, limit=1200)
        if lesson is None or not note_text:
            return lesson
        instruction = (
            f"{lesson.instruction}\n\nAdditional user guidance to preserve: {note_text}"
            if lesson.instruction
            else note_text
        )
        return self.update_lesson(
            lesson_id,
            instruction=instruction,
            summary=lesson.summary or lesson.title,
        )

    def suspect_cache_ids(self, cache_type: str) -> set[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT cache_id FROM learning_cache_events
                WHERE cache_type = ? AND status IN ('suspect', 'quarantined')
                GROUP BY cache_id
                """,
                (str(cache_type or ""),),
            ).fetchall()
        return {str(row["cache_id"]) for row in rows if str(row["cache_id"] or "").strip()}

    def clear(self) -> LearningSummary:
        """Clear recorded auto-learning ledger history and proposals."""

        with self._lock, self._connect() as connection:
            for table in (
                "capability_proposals",
                "capability_insights",
                "learning_lessons",
                "learning_cache_events",
                "learning_actions",
                "learning_runs",
            ):
                connection.execute(f"DELETE FROM {table}")
        return self.summary()

    def summary(self) -> LearningSummary:
        with self._lock, self._connect() as connection:
            run_rows = connection.execute(
                "SELECT outcome, COUNT(*) AS count, MAX(updated_at) AS last_at FROM learning_runs GROUP BY outcome"
            ).fetchall()
            lesson_rows = connection.execute(
                "SELECT status, COUNT(*) AS count, MAX(updated_at) AS last_at FROM learning_lessons GROUP BY status"
            ).fetchall()
            cache_rows = connection.execute(
                "SELECT status, COUNT(DISTINCT cache_type || ':' || cache_id) AS count FROM learning_cache_events GROUP BY status"
            ).fetchall()
            proposal_rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count, MAX(updated_at) AS last_at
                FROM capability_proposals
                GROUP BY status
                """
            ).fetchall()
            auto_approved_lessons = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM learning_lessons WHERE auto_approved = 1"
                ).fetchone()["count"]
                or 0
            )
            auto_approved_proposals = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM capability_proposals WHERE auto_approved = 1"
                ).fetchone()["count"]
                or 0
            )
        total_runs = sum(int(row["count"] or 0) for row in run_rows)
        successful_runs = sum(
            int(row["count"] or 0)
            for row in run_rows
            if str(row["outcome"]) in {"success", "failed_then_corrected"}
        )
        failed_runs = sum(
            int(row["count"] or 0)
            for row in run_rows
            if str(row["outcome"]) in {"failure", "validation_failed", "gateway_failed", "user_corrected"}
        )
        lesson_counts = {str(row["status"]): int(row["count"] or 0) for row in lesson_rows}
        proposal_counts = {str(row["status"]): int(row["count"] or 0) for row in proposal_rows}
        cache_counts = {str(row["status"]): int(row["count"] or 0) for row in cache_rows}
        return LearningSummary(
            total_runs=total_runs,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            draft_lessons=lesson_counts.get("draft", 0),
            approved_lessons=lesson_counts.get("approved", 0),
            rejected_lessons=lesson_counts.get("rejected", 0),
            retired_lessons=lesson_counts.get("retired", 0),
            auto_approved_lessons=auto_approved_lessons,
            draft_proposals=proposal_counts.get("draft", 0),
            approved_proposals=(
                proposal_counts.get("approved", 0)
                + proposal_counts.get("approved_pending_apply", 0)
            ),
            applied_proposals=proposal_counts.get("applied", 0),
            rejected_proposals=proposal_counts.get("rejected", 0),
            retired_proposals=proposal_counts.get("retired", 0),
            auto_approved_proposals=auto_approved_proposals,
            suspect_cache_entries=cache_counts.get("suspect", 0) + cache_counts.get("quarantined", 0),
            strengthened_cache_entries=cache_counts.get("strengthened", 0),
            last_run_at=max((str(row["last_at"] or "") for row in run_rows), default=""),
            last_lesson_at=max((str(row["last_at"] or "") for row in lesson_rows), default=""),
            last_proposal_at=max((str(row["last_at"] or "") for row in proposal_rows), default=""),
        )

    def _set_lesson_status(
        self,
        lesson_id: str,
        status: LearningLessonStatus,
        *,
        rejection_reason: str = "",
    ) -> LearningLesson | None:
        if self.get_lesson(lesson_id) is None:
            return None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE learning_lessons
                SET status = ?, rejection_reason = ?, updated_at = ?
                WHERE lesson_id = ?
                """,
                (status, _clip(rejection_reason, limit=1000), utc_now_iso(), lesson_id),
            )
        return self.get_lesson(lesson_id)

    def _set_proposal_status(
        self,
        proposal_id: str,
        status: CapabilityProposalStatus,
        *,
        apply_error: str = "",
    ) -> CapabilityProposal | None:
        if self.get_proposal(proposal_id) is None:
            return None
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE capability_proposals
                SET status = ?, apply_error = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                (status, _clip(apply_error, limit=1000), utc_now_iso(), proposal_id),
            )
        return self.get_proposal(proposal_id)

    def _apply_memory_proposal(
        self,
        proposal: CapabilityProposal,
        memory_store: AgentMemoryStore | None,
        *,
        actor: str,
    ) -> str:
        if memory_store is None:
            raise ValueError("Memory store is required to apply memory proposals.")
        draft = dict(proposal.draft or {})
        scope = dict(draft.get("scope") or {})
        evidence = dict(proposal.evidence or {})
        instruction = _clip(draft.get("instruction") or proposal.summary or proposal.title, limit=4000)
        if not instruction or _unsafe_learning_text(instruction):
            raise ValueError("Memory proposal instruction is empty or unsafe.")
        memory_kind = (
            "validation_policy"
            if proposal.target_kind == "validation_policy"
            else str(draft.get("memory_kind") or "task_memory")
        )
        memory_id = str(draft.get("memory_id") or proposal.applied_ref or "").removeprefix("memory:")
        update = MemoryEntryUpdate(
            instruction=instruction,
            summary=_clip(draft.get("summary") or proposal.summary or proposal.title, limit=500),
            memory_kind=memory_kind,  # type: ignore[arg-type]
            scope=str(scope.get("memory_scope") or draft.get("memory_scope") or "global"),  # type: ignore[arg-type]
            model_name=str(scope.get("model_name") or draft.get("model_name") or ""),
            model_family=str(
                scope.get("model_family")
                or draft.get("model_family")
                or normalize_model_family(str(scope.get("model_name") or draft.get("model_name") or ""))
            ),
            task_type=str(scope.get("task_type") or draft.get("task_type") or ""),
            tool_type=str(scope.get("tool_type") or draft.get("tool_type") or ""),
            intent_type=str(scope.get("intent_type") or draft.get("intent_type") or ""),
            validator_error_type=str(
                scope.get("validator_error_type") or draft.get("validator_error_type") or ""
            ),
            safe_examples=list(evidence.get("safe_examples") or draft.get("safe_examples") or []),
            blocked_examples=list(evidence.get("blocked_examples") or draft.get("blocked_examples") or []),
            tags=list(dict.fromkeys([*(draft.get("tags") or []), f"capability-proposal:{proposal.proposal_id}"])),
            rationale=proposal.rationale,
        )
        entry = memory_store.get_entry(memory_id) if memory_id else None
        if entry is not None:
            updated = memory_store.update_entry(memory_id, update, actor=actor)
            if updated is not None and updated.status != "active":
                memory_store.set_status(updated.memory_id, "active", actor=actor)
            return f"memory:{memory_id}"
        created = memory_store.create_entry(
            MemoryEntryCreate(
                instruction=instruction,
                summary=update.summary or proposal.title,
                status="active",
                memory_kind=memory_kind,  # type: ignore[arg-type]
                scope=update.scope or "global",  # type: ignore[arg-type]
                model_name=update.model_name or "",
                model_family=update.model_family or "",
                task_type=update.task_type or "",
                tool_type=update.tool_type or "",
                intent_type=update.intent_type or "",
                validator_error_type=update.validator_error_type or "",
                safe_examples=update.safe_examples or [],
                blocked_examples=update.blocked_examples or [],
                tags=update.tags or [],
                provenance="learning_ledger",
                request_id=proposal.source_request_id,
                rationale=proposal.rationale,
            ),
            actor=actor,
        )
        return f"memory:{created.memory_id}"

    def _apply_prompt_patch_proposal(
        self,
        proposal: CapabilityProposal,
        prompt_template_store: PromptTemplateStore | None,
    ) -> str:
        if prompt_template_store is None:
            raise ValueError("Prompt template store is required to apply prompt patches.")
        draft = dict(proposal.draft or {})
        prompt_key = str(draft.get("prompt_key") or proposal.target_id or "").strip()
        guidance = _clip(draft.get("guidance") or draft.get("instruction") or proposal.summary, limit=4000)
        if not prompt_key or not guidance:
            raise ValueError("Prompt patch proposals require prompt_key and guidance.")
        comparison = prompt_template_store.get_for_editor(prompt_key)
        record = comparison.active_record if comparison is not None else None
        if record is None:
            raise KeyError(f"Prompt template {prompt_key!r} was not found.")
        start = _PROMPT_GUIDANCE_START.format(proposal_id=proposal.proposal_id)
        end = _PROMPT_GUIDANCE_END.format(proposal_id=proposal.proposal_id)
        block = f"{start}\n{guidance.strip()}\n{end}"
        body = str(record.body or "")
        pattern = re.compile(
            re.escape(start) + r".*?" + re.escape(end),
            flags=re.DOTALL,
        )
        if pattern.search(body):
            updated_body = pattern.sub(block, body)
        else:
            updated_body = body.rstrip() + "\n\n" + block + "\n"
        validate_template_body(updated_body)
        previous_variables = set(extract_template_variables(body))
        updated_variables = set(extract_template_variables(updated_body))
        if not previous_variables.issubset(updated_variables):
            missing = ", ".join(sorted(previous_variables - updated_variables))
            raise ValueError(f"Prompt patch removed template variables: {missing}")
        metadata = dict(record.metadata or {})
        rollback_entries = list(metadata.get("capability_evolution_rollbacks") or [])
        rollback_entries.append(
            {
                "proposal_id": proposal.proposal_id,
                "previous_version": record.version,
                "previous_body": body,
                "applied_at": utc_now_iso(),
            }
        )
        metadata["capability_evolution_rollbacks"] = rollback_entries[-20:]
        updated = prompt_template_store.upsert(
            PromptTemplateRecord(
                prompt_key=prompt_key,
                version=int(record.version) + 1,
                body=updated_body,
                status=str(record.status or "active"),
                metadata=metadata,
            )
        )
        return f"prompt:{prompt_key}:v{updated.version}"

    def _apply_manifest_overlay_proposal(self, proposal: CapabilityProposal) -> str:
        draft = dict(proposal.draft or {})
        capability_id = str(draft.get("capability_id") or proposal.target_id or "").strip()
        overlay = self._sanitized_manifest_overlay(draft.get("overlay") or draft)
        if not capability_id or not overlay:
            raise ValueError("Manifest overlay proposals require capability_id and safe overlay fields.")
        return f"capability-overlay:{capability_id}:{proposal.proposal_id}"

    def _apply_executable_backend_patch_proposal(self, proposal: CapabilityProposal) -> str:
        draft = dict(proposal.draft or {})
        if not draft.get("patch") and not draft.get("files"):
            raise ValueError("Executable backend patch proposals require a patch bundle.")
        raise ValueError(
            "Executable backend patches cannot be applied by the learning ledger. "
            "Apply the patch explicitly in a code-editing workflow."
        )

    @staticmethod
    def _sanitized_manifest_overlay(raw_overlay: Any) -> dict[str, Any]:
        if not isinstance(raw_overlay, dict) or _unsafe_capability_text(_json_dumps(raw_overlay)):
            return {}
        if any(field in raw_overlay for field in _FORBIDDEN_OVERLAY_FIELDS):
            return {}
        overlay: dict[str, Any] = {}
        for field in _OVERLAY_LIST_FIELDS:
            if field == "examples":
                examples = _unique_examples(raw_overlay.get(field), limit=40)
                if examples:
                    overlay[field] = examples
                continue
            values = _unique_strings(raw_overlay.get(field), limit=80, max_chars=1200)
            if values:
                overlay[field] = values
        description = _clip(raw_overlay.get("description_append") or "", limit=1200)
        if description:
            overlay["description_append"] = description
        return overlay

    @staticmethod
    def _merge_overlay_payloads(
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current or {})
        for field, value in dict(incoming or {}).items():
            if field == "examples":
                merged[field] = _unique_examples(
                    [*list(merged.get(field) or []), *list(value or [])],
                    limit=80,
                )
            elif field in _OVERLAY_LIST_FIELDS:
                merged[field] = _unique_strings(
                    [*list(merged.get(field) or []), *list(value or [])],
                    limit=100,
                    max_chars=1200,
                )
            elif field == "description_append":
                parts = [str(merged.get(field) or "").strip(), str(value or "").strip()]
                merged[field] = "\n".join(part for part in parts if part)
        return merged

    @staticmethod
    def _memory_kind_for_lesson(lesson: LearningLesson) -> str:
        if lesson.lesson_type == "validation_policy":
            return "validation_policy"
        if lesson.lesson_type in {"performance_hint", "cache_boost"}:
            return "preference_memory"
        return "task_memory"

    @staticmethod
    def _row_to_insight(row: sqlite3.Row) -> CapabilityInsight:
        return CapabilityInsight(
            insight_id=str(row["insight_id"] or ""),
            insight_type=str(row["insight_type"] or "validator_failure"),  # type: ignore[arg-type]
            source_request_id=str(row["source_request_id"] or ""),
            source_stage=str(row["source_stage"] or ""),
            source_event_type=str(row["source_event_type"] or ""),
            source_error_type=str(row["source_error_type"] or ""),
            target_kind=(str(row["target_kind"] or "") or None),  # type: ignore[arg-type]
            target_id=str(row["target_id"] or ""),
            prompt_key=str(row["prompt_key"] or ""),
            capability_id=str(row["capability_id"] or ""),
            model_name=str(row["model_name"] or ""),
            model_family=str(row["model_family"] or ""),
            gateway_id=str(row["gateway_id"] or ""),
            cwd=str(row["cwd"] or ""),
            summary=str(row["summary"] or ""),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            dedupe_key=str(row["dedupe_key"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_proposal(row: sqlite3.Row) -> CapabilityProposal:
        return CapabilityProposal(
            proposal_id=str(row["proposal_id"] or ""),
            status=str(row["status"] or "draft"),  # type: ignore[arg-type]
            target_kind=str(row["target_kind"] or "task_memory"),  # type: ignore[arg-type]
            title=str(row["title"] or ""),
            summary=str(row["summary"] or ""),
            rationale=str(row["rationale"] or ""),
            confidence=float(row["confidence"] or 0.5),
            source=str(row["source"] or "deterministic"),  # type: ignore[arg-type]
            source_request_id=str(row["source_request_id"] or ""),
            source_insight_id=str(row["source_insight_id"] or ""),
            target_id=str(row["target_id"] or ""),
            draft=dict(_json_loads(row["draft_json"], {})),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            safety_decision=dict(_json_loads(row["safety_decision_json"], {})),
            auto_approved=bool(row["auto_approved"]),
            applied_ref=str(row["applied_ref"] or ""),
            apply_error=str(row["apply_error"] or ""),
            dedupe_key=str(row["dedupe_key"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> LearningRun:
        return LearningRun(
            request_id=str(row["request_id"]),
            conversation_id=str(row["conversation_id"] or ""),
            parent_request_id=str(row["parent_request_id"] or ""),
            agent_mode=str(row["agent_mode"] or ""),
            prompt_signature=str(row["prompt_signature"] or ""),
            prompt_excerpt=str(row["prompt_excerpt"] or ""),
            model_name=str(row["model_name"] or ""),
            model_family=str(row["model_family"] or ""),
            gateway_id=str(row["gateway_id"] or ""),
            gateway_nickname=str(row["gateway_nickname"] or ""),
            gateway_node=str(row["gateway_node"] or ""),
            gateway_platform=str(row["gateway_platform"] or ""),
            cwd=str(row["cwd"] or ""),
            workspace_root=str(row["workspace_root"] or ""),
            status=str(row["status"] or ""),
            outcome=str(row["outcome"] or "no_learning_needed"),  # type: ignore[arg-type]
            duration_ms=row["duration_ms"],
            input_tokens_estimate=int(row["input_tokens_estimate"] or 0),
            output_tokens_estimate=int(row["output_tokens_estimate"] or 0),
            total_tokens_estimate=int(row["total_tokens_estimate"] or 0),
            llm_call_count=int(row["llm_call_count"] or 0),
            confirmation_required=bool(row["confirmation_required"]),
            clarification_required=bool(row["clarification_required"]),
            auto_approved=bool(row["auto_approved"]),
            memory_ids=list(_json_loads(row["memory_ids_json"], [])),
            cache_ids=list(_json_loads(row["cache_ids_json"], [])),
            error=str(row["error"] or ""),
            final_response_preview=str(row["final_response_preview"] or ""),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_action(row: sqlite3.Row) -> LearningActionOutcome:
        return LearningActionOutcome(
            id=int(row["id"] or 0),
            request_id=str(row["request_id"] or ""),
            action_id=str(row["action_id"] or ""),
            task_id=str(row["task_id"] or ""),
            kind=str(row["kind"] or ""),
            title=str(row["title"] or ""),
            status=str(row["status"] or ""),
            cwd=str(row["cwd"] or ""),
            command_hash=str(row["command_hash"] or ""),
            exit_code=row["exit_code"],
            error_type=str(row["error_type"] or ""),
            error_preview=str(row["error_preview"] or ""),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _row_to_cache_event(row: sqlite3.Row) -> LearningCacheEvent:
        return LearningCacheEvent(
            id=int(row["id"] or 0),
            request_id=str(row["request_id"] or ""),
            cache_type=str(row["cache_type"] or "plan"),  # type: ignore[arg-type]
            cache_id=str(row["cache_id"] or ""),
            event=str(row["event"] or ""),
            status=str(row["status"] or "neutral"),  # type: ignore[arg-type]
            outcome=str(row["outcome"] or ""),
            score=row["score"],
            reason=str(row["reason"] or ""),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _row_to_lesson(row: sqlite3.Row) -> LearningLesson:
        return LearningLesson(
            lesson_id=str(row["lesson_id"] or ""),
            status=str(row["status"] or "draft"),  # type: ignore[arg-type]
            lesson_type=str(row["lesson_type"] or "general"),  # type: ignore[arg-type]
            title=str(row["title"] or ""),
            instruction=str(row["instruction"] or ""),
            summary=str(row["summary"] or ""),
            scope=dict(_json_loads(row["scope_json"], {})),
            evidence=dict(_json_loads(row["evidence_json"], {})),
            confidence=float(row["confidence"] or 0.5),
            source_request_id=str(row["source_request_id"] or ""),
            mirrored_memory_id=str(row["mirrored_memory_id"] or ""),
            auto_approved=bool(row["auto_approved"]),
            tags=list(_json_loads(row["tags_json"], [])),
            rationale=str(row["rationale"] or ""),
            rejection_reason=str(row["rejection_reason"] or ""),
            dedupe_key=str(row["dedupe_key"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
