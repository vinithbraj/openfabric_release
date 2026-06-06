"""SQLite store for reliability profiles, timelines, envelopes, and evals."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from agent_runtime.core.ids import new_id
from agent_runtime.reliability.models import (
    ApprovalEnvelope,
    FailureKind,
    ModelCapabilityProfile,
    ReliabilityEvalResult,
    ReliabilityEvent,
    ReliabilityEventKind,
    RecoveryAction,
)
from agent_runtime.storage_schema import ensure_store_schema_version


STORE_SCHEMA_VERSION = 1
MIN_SUPPORTED_STORE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    """Return a compact UTC timestamp."""

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _ensure_column(
    connection: sqlite3.Connection,
    *,
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


class AgentReliabilityStore:
    """Persist Reliability Kernel state in SQLite."""

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
                CREATE TABLE IF NOT EXISTS reliability_events (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    failure_kind TEXT,
                    recovery_action TEXT,
                    stage TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    model_id TEXT NOT NULL DEFAULT 'unknown',
                    status TEXT NOT NULL DEFAULT 'info',
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_profiles (
                    model_id TEXT PRIMARY KEY,
                    total_events INTEGER NOT NULL DEFAULT 0,
                    total_failures INTEGER NOT NULL DEFAULT 0,
                    total_recoveries INTEGER NOT NULL DEFAULT 0,
                    recovered_successes INTEGER NOT NULL DEFAULT 0,
                    schema_failures INTEGER NOT NULL DEFAULT 0,
                    json_failures INTEGER NOT NULL DEFAULT 0,
                    placeholder_failures INTEGER NOT NULL DEFAULT 0,
                    command_failures INTEGER NOT NULL DEFAULT 0,
                    python_failures INTEGER NOT NULL DEFAULT 0,
                    verification_failures INTEGER NOT NULL DEFAULT 0,
                    formatting_failures INTEGER NOT NULL DEFAULT 0,
                    low_confidence_failures INTEGER NOT NULL DEFAULT 0,
                    json_validity_rate REAL NOT NULL DEFAULT 1.0,
                    schema_repair_rate REAL NOT NULL DEFAULT 0.0,
                    placeholder_rate REAL NOT NULL DEFAULT 0.0,
                    command_failure_rate REAL NOT NULL DEFAULT 0.0,
                    python_failure_rate REAL NOT NULL DEFAULT 0.0,
                    verifier_quality REAL NOT NULL DEFAULT 1.0,
                    completion_quality REAL NOT NULL DEFAULT 1.0,
                    average_latency_ms REAL NOT NULL DEFAULT 0.0,
                    recovery_success_rate REAL NOT NULL DEFAULT 0.0,
                    weakness_score REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_envelopes (
                    envelope_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    gateway_node TEXT NOT NULL DEFAULT '',
                    gateway_url TEXT NOT NULL DEFAULT '',
                    cwd_values_json TEXT NOT NULL DEFAULT '[]',
                    action_ids_json TEXT NOT NULL DEFAULT '[]',
                    max_risk TEXT NOT NULL DEFAULT 'medium',
                    mutation_budget INTEGER NOT NULL DEFAULT 2,
                    used_mutations INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reliability_evals (
                    eval_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    total_cases INTEGER NOT NULL,
                    recovered_cases INTEGER NOT NULL,
                    blocked_cases INTEGER NOT NULL,
                    failed_cases INTEGER NOT NULL,
                    score REAL NOT NULL,
                    cases_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reliability_events_request ON reliability_events(request_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reliability_events_model ON reliability_events(model_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reliability_events_kind ON reliability_events(failure_kind)"
            )
            for column, definition in {
                "json_validity_rate": "REAL NOT NULL DEFAULT 1.0",
                "schema_repair_rate": "REAL NOT NULL DEFAULT 0.0",
                "placeholder_rate": "REAL NOT NULL DEFAULT 0.0",
                "command_failure_rate": "REAL NOT NULL DEFAULT 0.0",
                "python_failure_rate": "REAL NOT NULL DEFAULT 0.0",
                "verifier_quality": "REAL NOT NULL DEFAULT 1.0",
                "completion_quality": "REAL NOT NULL DEFAULT 1.0",
            }.items():
                _ensure_column(
                    connection,
                    table="model_profiles",
                    column=column,
                    definition=definition,
                )
            ensure_store_schema_version(
                connection,
                store_name="agent_reliability",
                current_version=STORE_SCHEMA_VERSION,
                min_supported_version=MIN_SUPPORTED_STORE_SCHEMA_VERSION,
            )

    def record_event(
        self,
        *,
        request_id: str,
        event_kind: ReliabilityEventKind,
        title: str,
        summary: str = "",
        model_id: str = "unknown",
        stage: str = "",
        failure_kind: FailureKind | None = None,
        recovery_action: RecoveryAction | None = None,
        status: str = "info",
        evidence: dict[str, Any] | None = None,
    ) -> ReliabilityEvent:
        """Append one reliability event and update its model profile."""

        event = ReliabilityEvent(
            event_id=new_id("rel_evt"),
            request_id=str(request_id or ""),
            event_kind=event_kind,
            failure_kind=failure_kind,
            recovery_action=recovery_action,
            stage=str(stage or ""),
            title=str(title or event_kind),
            summary=str(summary or ""),
            model_id=str(model_id or "unknown"),
            status=str(status or "info"),
            evidence=dict(evidence or {}),
            created_at=utc_now_iso(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reliability_events (
                    event_id, request_id, event_kind, failure_kind, recovery_action,
                    stage, title, summary, model_id, status, evidence_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.request_id,
                    event.event_kind,
                    event.failure_kind,
                    event.recovery_action,
                    event.stage,
                    event.title,
                    event.summary,
                    event.model_id,
                    event.status,
                    _json_dumps(event.evidence),
                    event.created_at,
                ),
            )
            self._update_profile_locked(connection, event)
        return event

    def _update_profile_locked(self, connection: sqlite3.Connection, event: ReliabilityEvent) -> None:
        row = connection.execute(
            "SELECT * FROM model_profiles WHERE model_id = ?",
            (event.model_id,),
        ).fetchone()
        profile = (
            ModelCapabilityProfile.model_validate(dict(row))
            if row is not None
            else ModelCapabilityProfile(
                model_id=event.model_id,
                updated_at=utc_now_iso(),
            )
        )
        values = profile.model_dump(mode="python")
        values["total_events"] = int(values["total_events"]) + 1
        if event.event_kind == "failure_detected":
            values["total_failures"] = int(values["total_failures"]) + 1
            if event.failure_kind == "schema_failure":
                values["schema_failures"] = int(values["schema_failures"]) + 1
            elif event.failure_kind == "json_failure":
                values["json_failures"] = int(values["json_failures"]) + 1
            elif event.failure_kind == "placeholder_command":
                values["placeholder_failures"] = int(values["placeholder_failures"]) + 1
            elif event.failure_kind in {"command_failed", "command_not_found"}:
                values["command_failures"] = int(values["command_failures"]) + 1
            elif event.failure_kind in {"python_failed", "deferred_python_failed"}:
                values["python_failures"] = int(values["python_failures"]) + 1
            elif event.failure_kind in {"verification_failed", "authoritative_value_dropped"}:
                values["verification_failures"] = int(values["verification_failures"]) + 1
                if event.failure_kind == "authoritative_value_dropped":
                    values["formatting_failures"] = int(values["formatting_failures"]) + 1
            elif event.failure_kind == "formatting_failed":
                values["formatting_failures"] = int(values["formatting_failures"]) + 1
            elif event.failure_kind == "model_looping_or_low_confidence":
                values["low_confidence_failures"] = int(values["low_confidence_failures"]) + 1
        elif event.event_kind == "verification_completed" and event.failure_kind == "verification_failed":
            values["total_failures"] = int(values["total_failures"]) + 1
            values["verification_failures"] = int(values["verification_failures"]) + 1
        elif event.event_kind in {"recovery_accepted", "recovery_decided"}:
            values["total_recoveries"] = int(values["total_recoveries"]) + 1
        elif event.event_kind == "outcome_recorded" and event.evidence.get("recovered") is True:
            values["recovered_successes"] = int(values["recovered_successes"]) + 1
        failures = max(1, int(values["total_failures"]))
        total_events = max(1, int(values["total_events"]))
        structured_failures = max(1, int(values["schema_failures"]) + int(values["json_failures"]))
        values["json_validity_rate"] = round(1.0 - (int(values["json_failures"]) / total_events), 4)
        values["schema_repair_rate"] = round(
            min(1.0, int(values["recovered_successes"]) / structured_failures),
            4,
        )
        values["placeholder_rate"] = round(int(values["placeholder_failures"]) / total_events, 4)
        values["command_failure_rate"] = round(int(values["command_failures"]) / total_events, 4)
        values["python_failure_rate"] = round(int(values["python_failures"]) / total_events, 4)
        values["verifier_quality"] = round(
            1.0 - (int(values["verification_failures"]) / total_events),
            4,
        )
        values["completion_quality"] = round(
            1.0 - (int(values["formatting_failures"]) / total_events),
            4,
        )
        values["recovery_success_rate"] = round(int(values["recovered_successes"]) / failures, 4)
        values["weakness_score"] = round(
            min(1.0, int(values["total_failures"]) / max(1, int(values["total_events"]))),
            4,
        )
        values["updated_at"] = utc_now_iso()
        connection.execute(
            """
            INSERT INTO model_profiles (
                model_id, total_events, total_failures, total_recoveries,
                recovered_successes, schema_failures, json_failures,
                placeholder_failures, command_failures, python_failures,
                verification_failures, formatting_failures, low_confidence_failures,
                json_validity_rate, schema_repair_rate, placeholder_rate,
                command_failure_rate, python_failure_rate, verifier_quality,
                completion_quality, average_latency_ms, recovery_success_rate,
                weakness_score, updated_at
            )
            VALUES (
                :model_id, :total_events, :total_failures, :total_recoveries,
                :recovered_successes, :schema_failures, :json_failures,
                :placeholder_failures, :command_failures, :python_failures,
                :verification_failures, :formatting_failures, :low_confidence_failures,
                :json_validity_rate, :schema_repair_rate, :placeholder_rate,
                :command_failure_rate, :python_failure_rate, :verifier_quality,
                :completion_quality, :average_latency_ms, :recovery_success_rate,
                :weakness_score, :updated_at
            )
            ON CONFLICT(model_id) DO UPDATE SET
                total_events=excluded.total_events,
                total_failures=excluded.total_failures,
                total_recoveries=excluded.total_recoveries,
                recovered_successes=excluded.recovered_successes,
                schema_failures=excluded.schema_failures,
                json_failures=excluded.json_failures,
                placeholder_failures=excluded.placeholder_failures,
                command_failures=excluded.command_failures,
                python_failures=excluded.python_failures,
                verification_failures=excluded.verification_failures,
                formatting_failures=excluded.formatting_failures,
                low_confidence_failures=excluded.low_confidence_failures,
                json_validity_rate=excluded.json_validity_rate,
                schema_repair_rate=excluded.schema_repair_rate,
                placeholder_rate=excluded.placeholder_rate,
                command_failure_rate=excluded.command_failure_rate,
                python_failure_rate=excluded.python_failure_rate,
                verifier_quality=excluded.verifier_quality,
                completion_quality=excluded.completion_quality,
                average_latency_ms=excluded.average_latency_ms,
                recovery_success_rate=excluded.recovery_success_rate,
                weakness_score=excluded.weakness_score,
                updated_at=excluded.updated_at
            """,
            values,
        )

    def get_profile(self, model_id: str) -> ModelCapabilityProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_profiles WHERE model_id = ?",
                (str(model_id or "unknown"),),
            ).fetchone()
        return ModelCapabilityProfile.model_validate(dict(row)) if row is not None else None

    def list_profiles(self, limit: int = 100) -> list[ModelCapabilityProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM model_profiles
                ORDER BY updated_at DESC, total_events DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 100), 500)),),
            ).fetchall()
        return [ModelCapabilityProfile.model_validate(dict(row)) for row in rows]

    def list_events(
        self,
        *,
        request_id: str | None = None,
        model_id: str | None = None,
        limit: int = 200,
    ) -> list[ReliabilityEvent]:
        where_parts: list[str] = []
        params: list[Any] = []
        if request_id:
            where_parts.append("request_id = ?")
            params.append(str(request_id))
        if model_id:
            where_parts.append("model_id = ?")
            params.append(str(model_id))
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params.append(max(1, min(int(limit or 200), 1000)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM reliability_events
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    request_id,
                    MIN(created_at) AS first_event_at,
                    MAX(created_at) AS last_event_at,
                    COUNT(*) AS event_count,
                    SUM(CASE WHEN event_kind = 'failure_detected' THEN 1 ELSE 0 END) AS failure_count,
                    SUM(CASE WHEN event_kind IN ('recovery_accepted','recovery_decided') THEN 1 ELSE 0 END) AS recovery_count,
                    SUM(CASE WHEN event_kind = 'verification_completed' THEN 1 ELSE 0 END) AS verification_count
                FROM reliability_events
                GROUP BY request_id
                ORDER BY last_event_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 100), 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ReliabilityEvent:
        payload = dict(row)
        payload["evidence"] = _json_loads(payload.pop("evidence_json", "{}"), {})
        return ReliabilityEvent.model_validate(payload)

    def create_approval_envelope(
        self,
        *,
        request_id: str,
        goal: str,
        gateway_node: str = "",
        gateway_url: str = "",
        cwd_values: list[str] | None = None,
        action_ids: list[str] | None = None,
        max_risk: str = "medium",
        mutation_budget: int = 2,
        model_id: str = "unknown",
    ) -> ApprovalEnvelope:
        now = utc_now_iso()
        envelope = ApprovalEnvelope(
            envelope_id=new_id("rel_env"),
            request_id=str(request_id or ""),
            goal=str(goal or ""),
            gateway_node=str(gateway_node or ""),
            gateway_url=str(gateway_url or ""),
            cwd_values=list(cwd_values or []),
            action_ids=list(action_ids or []),
            max_risk=str(max_risk or "medium"),
            mutation_budget=max(0, int(mutation_budget)),
            used_mutations=0,
            status="active",
            created_at=now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO approval_envelopes (
                    envelope_id, request_id, goal, gateway_node, gateway_url,
                    cwd_values_json, action_ids_json, max_risk, mutation_budget,
                    used_mutations, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.envelope_id,
                    envelope.request_id,
                    envelope.goal,
                    envelope.gateway_node,
                    envelope.gateway_url,
                    _json_dumps(envelope.cwd_values),
                    _json_dumps(envelope.action_ids),
                    envelope.max_risk,
                    envelope.mutation_budget,
                    envelope.used_mutations,
                    envelope.status,
                    envelope.created_at,
                    envelope.updated_at,
                ),
            )
        self.record_event(
            request_id=envelope.request_id,
            event_kind="approval_envelope_created",
            title="Approval envelope created",
            summary="Confirmation created a bounded recovery envelope.",
            model_id=str(model_id or "unknown"),
            evidence=envelope.model_dump(mode="json"),
        )
        return envelope

    def get_latest_envelope(self, request_id: str) -> ApprovalEnvelope | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM approval_envelopes
                WHERE request_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (str(request_id or ""),),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["cwd_values"] = _json_loads(payload.pop("cwd_values_json", "[]"), [])
        payload["action_ids"] = _json_loads(payload.pop("action_ids_json", "[]"), [])
        return ApprovalEnvelope.model_validate(payload)

    def consume_approval_envelope_budget(
        self,
        *,
        envelope_id: str,
        mutation_count: int,
        status: str | None = None,
    ) -> ApprovalEnvelope | None:
        """Consume mutation budget from one approval envelope and return the updated row."""

        count = max(0, int(mutation_count))
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM approval_envelopes WHERE envelope_id = ?",
                (str(envelope_id or ""),),
            ).fetchone()
            if row is None:
                return None
            used = int(row["used_mutations"] or 0) + count
            budget = int(row["mutation_budget"] or 0)
            next_status = str(status or row["status"] or "active")
            if next_status == "active" and budget and used >= budget:
                next_status = "exhausted"
            connection.execute(
                """
                UPDATE approval_envelopes
                SET used_mutations = ?, status = ?, updated_at = ?
                WHERE envelope_id = ?
                """,
                (used, next_status, now, str(envelope_id or "")),
            )
            updated = connection.execute(
                "SELECT * FROM approval_envelopes WHERE envelope_id = ?",
                (str(envelope_id or ""),),
            ).fetchone()
        if updated is None:
            return None
        payload = dict(updated)
        payload["cwd_values"] = _json_loads(payload.pop("cwd_values_json", "[]"), [])
        payload["action_ids"] = _json_loads(payload.pop("action_ids_json", "[]"), [])
        return ApprovalEnvelope.model_validate(payload)

    def record_eval(self, result: ReliabilityEvalResult) -> ReliabilityEvalResult:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO reliability_evals (
                    eval_id, created_at, total_cases, recovered_cases,
                    blocked_cases, failed_cases, score, cases_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.eval_id,
                    result.created_at,
                    result.total_cases,
                    result.recovered_cases,
                    result.blocked_cases,
                    result.failed_cases,
                    result.score,
                    _json_dumps(result.cases),
                ),
            )
        return result

    def list_evals(self, limit: int = 50) -> list[ReliabilityEvalResult]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM reliability_evals
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit or 50), 200)),),
            ).fetchall()
        results: list[ReliabilityEvalResult] = []
        for row in rows:
            payload = dict(row)
            payload["cases"] = _json_loads(payload.pop("cases_json", "[]"), [])
            results.append(ReliabilityEvalResult.model_validate(payload))
        return results

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            event_count = int(connection.execute("SELECT COUNT(*) FROM reliability_events").fetchone()[0])
            run_count = int(
                connection.execute("SELECT COUNT(DISTINCT request_id) FROM reliability_events").fetchone()[0]
            )
            failure_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reliability_events WHERE event_kind = 'failure_detected'"
                ).fetchone()[0]
            )
            recovery_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM reliability_events
                    WHERE event_kind IN ('recovery_accepted', 'recovery_decided')
                    """
                ).fetchone()[0]
            )
            verification_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM reliability_events WHERE event_kind = 'verification_completed'"
                ).fetchone()[0]
            )
        return {
            "event_count": event_count,
            "run_count": run_count,
            "failure_count": failure_count,
            "recovery_count": recovery_count,
            "verification_count": verification_count,
            "profile_count": len(self.list_profiles(limit=500)),
        }
