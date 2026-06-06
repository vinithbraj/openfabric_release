"""SQLite store for LRN-T/LR-T top-level typed task structures."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.memory import normalize_model_family
from agent_runtime.plan_cache import normalize_cache_text_shape, redact_and_clip
from agent_runtime.lrn_total_tasks.models import (
    LrnTotalTaskCandidate,
    LrnTotalTaskEntry,
    LrnTotalTaskLookupContext,
    LrnTotalTaskStats,
    LrnTotalTaskWrite,
)
from agent_runtime.storage_schema import ensure_store_schema_version


SCHEMA_VERSION = 1
MIN_SUPPORTED_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return an ISO timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _stable_hash(value: Any, *, length: int = 32) -> str:
    raw = _json_dumps(value) if not isinstance(value, str) else value
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def normalized_prompt(value: str) -> str:
    """Return the deterministic prompt shape used by LRN-T matching."""

    text = normalize_cache_text_shape(value)
    text = re.sub(r"[.?!,;:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def prompt_signature(value: str) -> str:
    """Return the normalized prompt signature."""

    return _stable_hash(normalized_prompt(value))


def _classification_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    domains = sorted(
        {
            str(domain or "").strip().lower()
            for domain in list(value.get("likely_domains") or [])
            if str(domain or "").strip()
        }
    )
    return {
        "prompt_type": str(value.get("prompt_type") or "").strip().lower(),
        "requires_tools": bool(value.get("requires_tools")),
        "likely_domains": domains,
        "risk_level": str(value.get("risk_level") or "").strip().lower(),
    }


def entry_key_for_write(payload: LrnTotalTaskWrite) -> str:
    """Return the uniqueness key for a learned total-task structure."""

    model_family = payload.model_family or normalize_model_family(payload.model_name)
    return _stable_hash(
        {
            "prompt_signature": prompt_signature(payload.prompt),
            "classification": _classification_snapshot(payload.classification_context),
            "model_family": str(model_family or "").strip().lower(),
            "workflow_mode": str(payload.workflow_mode or "").strip().lower(),
            "registry_contract_hash": str(payload.registry_contract_hash or "").strip(),
            "schema_version": SCHEMA_VERSION,
        }
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalized_prompt(value).split()
        if len(token) > 1
    }


def _similarity(left: str, right: str) -> float:
    left_norm = normalized_prompt(left)
    right_norm = normalized_prompt(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    if not left_tokens or not right_tokens:
        return sequence_score
    overlap = left_tokens & right_tokens
    recall = len(overlap) / max(1, len(left_tokens))
    jaccard = len(overlap) / max(1, len(left_tokens | right_tokens))
    return min(1.0, max(sequence_score, (0.75 * recall) + (0.25 * jaccard)))


def _classification_compatible(
    current: dict[str, Any],
    existing: dict[str, Any],
) -> bool:
    current_snapshot = _classification_snapshot(current)
    existing_snapshot = _classification_snapshot(existing)
    return current_snapshot == existing_snapshot


class AgentLrnTotalTaskStore:
    """Durable LRN-T/LR-T store for successful typed task structures."""

    def __init__(self, db_path: str | Path, *, max_entries: int = 5000) -> None:
        self.db_path = Path(db_path).expanduser()
        self.max_entries = max(1, int(max_entries))
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
                CREATE TABLE IF NOT EXISTS lrn_total_task_entries (
                    entry_id TEXT PRIMARY KEY,
                    entry_key TEXT NOT NULL UNIQUE,
                    prompt_signature TEXT NOT NULL,
                    normalized_prompt TEXT NOT NULL DEFAULT '',
                    prompt_excerpt TEXT NOT NULL DEFAULT '',
                    classification_context_json TEXT NOT NULL DEFAULT '{}',
                    model_name TEXT NOT NULL DEFAULT '',
                    model_family TEXT NOT NULL DEFAULT '',
                    workflow_mode TEXT NOT NULL DEFAULT '',
                    registry_contract_hash TEXT NOT NULL DEFAULT '',
                    tasks_json TEXT NOT NULL DEFAULT '[]',
                    global_constraints_json TEXT NOT NULL DEFAULT '{}',
                    routing_metadata_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    quarantined_at TEXT NOT NULL DEFAULT '',
                    quarantine_reason TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS lrn_total_task_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_lrn_total_prompt_signature
                ON lrn_total_task_entries(prompt_signature);
                CREATE INDEX IF NOT EXISTS idx_lrn_total_lookup
                ON lrn_total_task_entries(status, model_family, workflow_mode, registry_contract_hash);
                CREATE INDEX IF NOT EXISTS idx_lrn_total_updated
                ON lrn_total_task_entries(updated_at);
                """
            )
            ensure_store_schema_version(
                conn,
                store_name="agent_lrn_total_tasks",
                current_version=SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_SCHEMA_VERSION,
            )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LrnTotalTaskEntry:
        payload = dict(row)
        payload["classification_context"] = _json_loads(
            payload.pop("classification_context_json", "{}"),
            {},
        )
        payload["tasks"] = _json_loads(payload.pop("tasks_json", "[]"), [])
        payload["global_constraints"] = _json_loads(
            payload.pop("global_constraints_json", "{}"),
            {},
        )
        payload["routing_metadata"] = _json_loads(
            payload.pop("routing_metadata_json", "{}"),
            {},
        )
        return LrnTotalTaskEntry.model_validate(payload)

    def _prune(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM lrn_total_task_entries
            WHERE entry_id IN (
                SELECT entry_id FROM lrn_total_task_entries
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_entries,),
        )

    def upsert_entry(self, payload: LrnTotalTaskWrite) -> LrnTotalTaskEntry:
        now = utc_now_iso()
        model_family = payload.model_family or normalize_model_family(payload.model_name)
        prompt_shape = normalized_prompt(payload.prompt)
        values = {
            "entry_key": entry_key_for_write(payload.model_copy(update={"model_family": model_family})),
            "prompt_signature": prompt_signature(payload.prompt),
            "normalized_prompt": prompt_shape,
            "prompt_excerpt": redact_and_clip(payload.prompt, max_chars=1000),
            "classification_context_json": _json_dumps(
                _classification_snapshot(payload.classification_context)
            ),
            "model_name": str(payload.model_name or ""),
            "model_family": str(model_family or ""),
            "workflow_mode": str(payload.workflow_mode or "").strip().lower(),
            "registry_contract_hash": str(payload.registry_contract_hash or "").strip(),
            "tasks_json": _json_dumps(payload.tasks),
            "global_constraints_json": _json_dumps(payload.global_constraints),
            "routing_metadata_json": _json_dumps(payload.routing_metadata),
            "schema_version": SCHEMA_VERSION,
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT entry_id FROM lrn_total_task_entries WHERE entry_key = ?",
                (values["entry_key"],),
            ).fetchone()
            if existing is None:
                entry_id = new_id("lrnt")
                conn.execute(
                    """
                    INSERT INTO lrn_total_task_entries
                    (entry_id, entry_key, prompt_signature, normalized_prompt, prompt_excerpt,
                     classification_context_json, model_name, model_family, workflow_mode,
                     registry_contract_hash, tasks_json, global_constraints_json,
                     routing_metadata_json, status, success_count, failure_count, use_count,
                     created_at, updated_at, last_used_at, quarantined_at, quarantine_reason,
                     schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, 0, 0,
                            ?, ?, '', '', '', ?)
                    """,
                    (
                        entry_id,
                        values["entry_key"],
                        values["prompt_signature"],
                        values["normalized_prompt"],
                        values["prompt_excerpt"],
                        values["classification_context_json"],
                        values["model_name"],
                        values["model_family"],
                        values["workflow_mode"],
                        values["registry_contract_hash"],
                        values["tasks_json"],
                        values["global_constraints_json"],
                        values["routing_metadata_json"],
                        now,
                        now,
                        values["schema_version"],
                    ),
                )
            else:
                entry_id = str(existing["entry_id"])
                conn.execute(
                    """
                    UPDATE lrn_total_task_entries
                    SET prompt_signature = ?, normalized_prompt = ?, prompt_excerpt = ?,
                        classification_context_json = ?, model_name = ?, model_family = ?,
                        workflow_mode = ?, registry_contract_hash = ?, tasks_json = ?,
                        global_constraints_json = ?, routing_metadata_json = ?,
                        status = 'active', success_count = success_count + 1,
                        updated_at = ?, quarantined_at = '', quarantine_reason = '',
                        schema_version = ?
                    WHERE entry_id = ?
                    """,
                    (
                        values["prompt_signature"],
                        values["normalized_prompt"],
                        values["prompt_excerpt"],
                        values["classification_context_json"],
                        values["model_name"],
                        values["model_family"],
                        values["workflow_mode"],
                        values["registry_contract_hash"],
                        values["tasks_json"],
                        values["global_constraints_json"],
                        values["routing_metadata_json"],
                        now,
                        values["schema_version"],
                        entry_id,
                    ),
                )
            self._prune(conn)
        entry = self.get_entry(entry_id)
        if entry is None:  # pragma: no cover - defensive
            raise RuntimeError("LRN-T entry was not written.")
        return entry

    def get_entry(self, entry_id: str) -> LrnTotalTaskEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lrn_total_task_entries WHERE entry_id = ?",
                (str(entry_id or ""),),
            ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def retrieve(
        self,
        context: LrnTotalTaskLookupContext,
    ) -> list[LrnTotalTaskCandidate]:
        current_family = str(
            context.model_family or normalize_model_family(context.model_name)
        ).strip().lower()
        current_workflow = str(context.workflow_mode or "").strip().lower()
        current_registry = str(context.registry_contract_hash or "").strip()
        current_signature = prompt_signature(context.prompt)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lrn_total_task_entries ORDER BY updated_at DESC"
            ).fetchall()
        candidates: list[LrnTotalTaskCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.status != "active":
                continue
            if entry.failure_count > 0:
                continue
            if entry.schema_version != SCHEMA_VERSION:
                continue
            if current_family and entry.model_family and entry.model_family != current_family:
                continue
            if current_workflow and entry.workflow_mode and entry.workflow_mode != current_workflow:
                continue
            if current_registry and entry.registry_contract_hash != current_registry:
                continue
            if not _classification_compatible(
                context.classification_context,
                entry.classification_context,
            ):
                continue
            if current_signature == entry.prompt_signature:
                score = 1.0
                reason = "exact normalized prompt match"
            else:
                score = _similarity(context.prompt, entry.normalized_prompt or entry.prompt_excerpt)
                reason = f"normalized prompt similarity {score:.3f}"
            if score < context.similarity_threshold:
                continue
            candidates.append(LrnTotalTaskCandidate(entry=entry, score=score, reason=reason))
        candidates.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        return candidates[: context.limit]

    def rejected_candidates(
        self,
        context: LrnTotalTaskLookupContext,
    ) -> list[LrnTotalTaskCandidate]:
        """Return compatible candidates that missed the active similarity threshold."""

        current_family = str(
            context.model_family or normalize_model_family(context.model_name)
        ).strip().lower()
        current_workflow = str(context.workflow_mode or "").strip().lower()
        current_registry = str(context.registry_contract_hash or "").strip()
        current_signature = prompt_signature(context.prompt)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lrn_total_task_entries ORDER BY updated_at DESC"
            ).fetchall()
        candidates: list[LrnTotalTaskCandidate] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.status != "active":
                continue
            if entry.failure_count > 0:
                continue
            if entry.schema_version != SCHEMA_VERSION:
                continue
            if current_family and entry.model_family and entry.model_family != current_family:
                continue
            if current_workflow and entry.workflow_mode and entry.workflow_mode != current_workflow:
                continue
            if current_registry and entry.registry_contract_hash != current_registry:
                continue
            if not _classification_compatible(
                context.classification_context,
                entry.classification_context,
            ):
                continue
            if current_signature == entry.prompt_signature:
                score = 1.0
                reason = "exact normalized prompt match"
            else:
                score = _similarity(context.prompt, entry.normalized_prompt or entry.prompt_excerpt)
                reason = f"normalized prompt similarity {score:.3f}"
            if score >= context.similarity_threshold:
                continue
            candidates.append(LrnTotalTaskCandidate(entry=entry, score=score, reason=reason))
        candidates.sort(key=lambda item: (item.score, item.entry.updated_at), reverse=True)
        return candidates[: context.limit]

    def mark_used(self, entry_id: str) -> LrnTotalTaskEntry | None:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE lrn_total_task_entries
                SET use_count = use_count + 1, last_used_at = ?, updated_at = ?
                WHERE entry_id = ?
                """,
                (now, now, str(entry_id or "")),
            )
        return self.get_entry(entry_id)

    def mark_failed(
        self,
        entry_id: str,
        *,
        failure_category: str = "",
        quarantine: bool = True,
    ) -> LrnTotalTaskEntry | None:
        now = utc_now_iso()
        reason = redact_and_clip(failure_category, max_chars=500)
        with self._connect() as conn:
            if quarantine:
                conn.execute(
                    """
                    UPDATE lrn_total_task_entries
                    SET failure_count = failure_count + 1, status = 'quarantined',
                        quarantined_at = ?, quarantine_reason = ?, updated_at = ?
                    WHERE entry_id = ?
                    """,
                    (now, reason, now, str(entry_id or "")),
                )
            else:
                conn.execute(
                    """
                    UPDATE lrn_total_task_entries
                    SET failure_count = failure_count + 1, quarantine_reason = ?,
                        updated_at = ?
                    WHERE entry_id = ?
                    """,
                    (reason, now, str(entry_id or "")),
                )
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id: str) -> bool:
        """Delete exactly one learned total-task entry."""

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM lrn_total_task_entries WHERE entry_id = ?",
                (str(entry_id or ""),),
            )
        return int(cursor.rowcount or 0) > 0

    def clear(self) -> LrnTotalTaskStats:
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM lrn_total_task_entries")
            conn.execute(
                """
                INSERT INTO lrn_total_task_meta (key, value)
                VALUES ('last_cleared_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (now,),
            )
        return self.stats()

    def stats(self) -> LrnTotalTaskStats:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_entries,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_entries,
                    SUM(CASE WHEN status = 'quarantined' THEN 1 ELSE 0 END) AS quarantined_entries,
                    SUM(use_count) AS total_uses,
                    MAX(last_used_at) AS last_used_at,
                    MAX(updated_at) AS last_updated_at
                FROM lrn_total_task_entries
                """
            ).fetchone()
            meta = conn.execute(
                "SELECT value FROM lrn_total_task_meta WHERE key = 'last_cleared_at'"
            ).fetchone()
        return LrnTotalTaskStats(
            total_entries=int(row["total_entries"] or 0),
            active_entries=int(row["active_entries"] or 0),
            quarantined_entries=int(row["quarantined_entries"] or 0),
            total_uses=int(row["total_uses"] or 0),
            last_used_at=str(row["last_used_at"] or ""),
            last_updated_at=str(row["last_updated_at"] or ""),
            last_cleared_at=str(meta["value"] if meta is not None else ""),
        )
