from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.capabilities.monitors import RuntimeStartMonitorCapability
from agent_runtime.monitors import AgentMonitorCreate, AgentMonitorManager, AgentMonitorStore
from agent_runtime.monitors.drafting import (
    MonitorSpecValidationError,
    draft_monitor_from_prompt,
    validate_monitor_draft,
)
from agent_runtime.monitors.models import AgentMonitorDraft


class FakePlannerLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return dict(self.payload)


class FakeJudgeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return dict(self.payload)


def _manager(tmp_path: Path, *, llm_client: Any | None = None) -> AgentMonitorManager:
    return AgentMonitorManager(
        monitor_store=AgentMonitorStore(tmp_path / "monitors.db"),
        event_store=object(),
        llm_client_provider=lambda: llm_client,
        judge_min_interval_seconds=5,
        max_inferred_monitor_duration_seconds=300,
    )


def test_llm_monitor_planner_success_clamps_and_preserves_metadata() -> None:
    planner = FakePlannerLLM(
        {
            "is_monitor_request": True,
            "monitor_intent": "postgres connection saturation",
            "title": "Postgres saturation",
            "mode": "sample_command",
            "command": "psql -c \"select count(*) from pg_stat_activity\"",
            "interval_seconds": 1,
            "duration_seconds": 900,
            "deterministic_condition": "",
            "natural_language_condition": "connection saturation looks high",
            "trigger_mode": "llm_judged",
            "action_prompt": "Explain the saturation signal.",
            "confidence": 0.92,
            "missing_details": [],
            "rationale": "Use pg_stat_activity as a read-only saturation signal.",
            "risk_notes": "Read-only query.",
        }
    )

    response = draft_monitor_from_prompt(
        {"prompt": "monitor postgres connections and alert if saturation looks high"},
        llm_client=planner,
    )

    draft = response.drafts[0]
    assert planner.calls == 1
    assert draft.command.startswith("psql -c")
    assert draft.duration_seconds == 300
    assert draft.trigger_mode == "llm_judged"
    assert draft.natural_language_condition == "connection saturation looks high"
    assert draft.planner_rationale.startswith("Use pg_stat_activity")


def test_monitor_planner_invalid_output_falls_back_and_backticks_skip_llm() -> None:
    destructive = FakePlannerLLM(
        {
            "is_monitor_request": True,
            "command": "rm -rf /tmp/something",
            "confidence": 0.8,
        }
    )

    fallback = draft_monitor_from_prompt(
        {"prompt": "monitor free RAM and tell me if below 2GB"},
        llm_client=destructive,
    )
    assert fallback.drafts[0].command.startswith("free -m")
    assert fallback.drafts[0].condition == "number_lt:2048"

    skipped = FakePlannerLLM({})
    explicit = draft_monitor_from_prompt(
        {"prompt": "/monitor run `printf ready` and alert if contains ready"},
        llm_client=skipped,
    )
    assert skipped.calls == 0
    assert explicit.drafts[0].command == "printf ready"


def test_monitor_spec_validation_rejects_destructive_and_prefers_parseable() -> None:
    parsed = validate_monitor_draft(
        AgentMonitorDraft(
            title="RAM",
            command="free -h",
            condition="number_lt:2048",
        ),
        prompt="monitor free RAM",
    )
    assert "available_mb=" in parsed.command

    with pytest.raises(MonitorSpecValidationError):
        validate_monitor_draft(AgentMonitorDraft(command="rm -rf /tmp/openfabric-test"))


def test_monitor_store_backfills_new_columns_for_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "old-monitors.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE agent_monitors (
                monitor_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT 'sample_command',
                command TEXT NOT NULL,
                interval_seconds INTEGER NOT NULL DEFAULT 5,
                duration_seconds INTEGER NOT NULL DEFAULT 300,
                condition TEXT NOT NULL DEFAULT '',
                action_prompt TEXT NOT NULL DEFAULT '',
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
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent_monitors (
                monitor_id, title, command, created_at, updated_at
            ) VALUES ('mon-old', 'Old', 'printf ok', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """
        )

    monitor = AgentMonitorStore(db_path).get_monitor("mon-old")
    assert monitor is not None
    assert monitor.trigger_mode == "deterministic"
    assert monitor.natural_language_condition == ""
    assert monitor.latest_judge_summary == ""


def test_llm_judge_triggers_low_confidence_suppresses_and_rate_limits(tmp_path: Path) -> None:
    judge = FakeJudgeLLM(
        {
            "should_trigger": True,
            "confidence": 0.9,
            "reason": "Available memory is critically low.",
            "matched_excerpt": "available_mb=128",
            "recommended_notification_summary": "Memory pressure is abnormal.",
            "task_context": {"signal": "memory"},
        }
    )
    manager = _manager(tmp_path, llm_client=judge)
    monitor = manager.monitor_store.create_monitor(
        AgentMonitorCreate(
            title="Memory pressure",
            command="free -m",
            natural_language_condition="memory pressure looks abnormal",
            trigger_mode="llm_judged",
        )
    )

    matched, reason, detail, summary, task_context = manager._evaluate_monitor_output(
        monitor,
        output="available_mb=128 used_mb=16000",
    )
    assert matched is True
    assert "critically low" in reason
    assert summary == "Memory pressure is abnormal."
    assert task_context == {"signal": "memory"}
    assert detail["llm_judge"]["judgement"]["matched_excerpt"] == "available_mb=128"

    matched_again, _, detail_again, _, _ = manager._evaluate_monitor_output(
        monitor,
        output="available_mb=64 used_mb=16064",
    )
    assert matched_again is False
    assert detail_again["llm_judge"]["judge_rate_limited"] is True
    assert judge.calls == 1

    low_confidence = FakeJudgeLLM(
        {
            "should_trigger": True,
            "confidence": 0.2,
            "reason": "Maybe abnormal.",
            "matched_excerpt": "available_mb=4096",
            "recommended_notification_summary": "",
            "task_context": {},
        }
    )
    low_manager = _manager(tmp_path / "low", llm_client=low_confidence)
    low_monitor = low_manager.monitor_store.create_monitor(
        AgentMonitorCreate(
            title="Memory pressure",
            command="free -m",
            natural_language_condition="memory pressure looks abnormal",
            trigger_mode="llm_judged",
        )
    )
    matched_low, _, _, _, _ = low_manager._evaluate_monitor_output(low_monitor, output="available_mb=4096")
    assert matched_low is False


def test_llm_judge_unavailable_and_hybrid_deterministic_fallback(tmp_path: Path) -> None:
    manager = _manager(tmp_path, llm_client=None)
    monitor = manager.monitor_store.create_monitor(
        AgentMonitorCreate(
            title="Qualitative",
            command="printf ok",
            natural_language_condition="output looks unhealthy",
            trigger_mode="llm_judged",
        )
    )
    matched, _, detail, _, _ = manager._evaluate_monitor_output(monitor, output="ok")
    assert matched is False
    assert detail["llm_judge"]["judge_unavailable"] is True
    assert manager.monitor_store.get_monitor(monitor.monitor_id).latest_judge_summary.startswith("LLM judge unavailable")

    judge = FakeJudgeLLM({"should_trigger": True, "confidence": 0.9, "reason": "would trigger"})
    hybrid_manager = _manager(tmp_path / "hybrid", llm_client=judge)
    hybrid = hybrid_manager.monitor_store.create_monitor(
        AgentMonitorCreate(
            title="Hybrid",
            command="printf ready",
            condition="contains:ready",
            natural_language_condition="output looks unhealthy",
            trigger_mode="hybrid",
        )
    )
    matched_hybrid, reason, _, _, _ = hybrid_manager._evaluate_monitor_output(hybrid, output="ready")
    assert matched_hybrid is True
    assert reason == "Output contained `ready`."
    assert judge.calls == 0


def test_runtime_start_monitor_can_create_from_prompt_only(tmp_path: Path) -> None:
    planner = FakePlannerLLM(
        {
            "is_monitor_request": True,
            "monitor_intent": "postgres connection saturation",
            "title": "Postgres saturation",
            "mode": "sample_command",
            "command": "psql -c \"select count(*) from pg_stat_activity\"",
            "interval_seconds": 5,
            "duration_seconds": 300,
            "deterministic_condition": "",
            "natural_language_condition": "connection saturation looks high",
            "trigger_mode": "llm_judged",
            "action_prompt": "Explain the saturation signal.",
            "confidence": 0.9,
            "missing_details": [],
            "rationale": "Read-only connection count.",
            "risk_notes": "",
        }
    )
    manager = _manager(tmp_path, llm_client=planner)

    result = RuntimeStartMonitorCapability().execute(
        {"prompt": "monitor postgres connections for 5 minutes and alert if saturation looks high", "start_now": False},
        {"node_id": "node-1", "execution_context": {"monitor_manager": manager}},
    )

    assert result.status == "success"
    assert result.data_preview.get("needs_clarification") is not True
    monitor = result.data_preview["monitor"]
    assert monitor["command"].startswith("psql -c")
    assert monitor["trigger_mode"] == "llm_judged"
