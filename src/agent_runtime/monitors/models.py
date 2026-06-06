"""Typed monitor contracts for the Agent UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentMonitorMode = Literal["sample_command", "raw_stream"]
AgentMonitorTriggerMode = Literal["deterministic", "llm_judged", "hybrid"]
AgentMonitorStatus = Literal[
    "queued",
    "running",
    "triggered",
    "completed",
    "failed",
    "cancelled",
    "paused",
    "interrupted",
    "archived",
]


class AgentMonitorDraft(BaseModel):
    """One monitor draft extracted from a user prompt."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=160)
    mode: AgentMonitorMode = "sample_command"
    command: str = Field(default="", max_length=4000)
    interval_seconds: int = Field(default=5, ge=1)
    duration_seconds: int = Field(default=300, ge=1)
    condition: str = Field(default="", max_length=1000)
    natural_language_condition: str = Field(default="", max_length=1000)
    trigger_mode: AgentMonitorTriggerMode = "deterministic"
    action_prompt: str = Field(default="", max_length=24000)
    schedule_summary: str = Field(default="", max_length=240)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_details: list[str] = Field(default_factory=list)
    planner_rationale: str = Field(default="", max_length=1000)
    risk_notes: str = Field(default="", max_length=1000)
    judge_interval_seconds: int = Field(default=5, ge=1)


class AgentMonitorDraftRequest(BaseModel):
    """Prompt and context used to draft monitor settings."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)
    context: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    llm_model: str | None = Field(default=None, max_length=240)


class AgentMonitorDraftResponse(BaseModel):
    """Draft response for monitor creation."""

    model_config = ConfigDict(extra="forbid")

    is_monitor_request: bool = False
    drafts: list[AgentMonitorDraft] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)


class AgentMonitorPlan(BaseModel):
    """Structured LLM proposal for one monitor specification."""

    model_config = ConfigDict(extra="forbid")

    is_monitor_request: bool = False
    monitor_intent: str = Field(default="", max_length=240)
    title: str = Field(default="", max_length=160)
    mode: AgentMonitorMode = "sample_command"
    command: str = Field(default="", max_length=4000)
    interval_seconds: int | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=0)
    deterministic_condition: str = Field(default="", max_length=1000)
    natural_language_condition: str = Field(default="", max_length=1000)
    trigger_mode: AgentMonitorTriggerMode = "deterministic"
    action_prompt: str = Field(default="", max_length=24000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_details: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)
    risk_notes: str = Field(default="", max_length=1000)


class AgentMonitorJudgement(BaseModel):
    """Structured LLM judgement over recent monitor output."""

    model_config = ConfigDict(extra="forbid")

    should_trigger: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=1000)
    matched_excerpt: str = Field(default="", max_length=2000)
    recommended_notification_summary: str = Field(default="", max_length=1000)
    task_context: dict[str, Any] = Field(default_factory=dict)


class AgentMonitorCreate(BaseModel):
    """Create one persistent monitor."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=160)
    prompt: str = Field(default="", max_length=24000)
    mode: AgentMonitorMode = "sample_command"
    command: str = Field(min_length=1, max_length=4000)
    interval_seconds: int = Field(default=5, ge=1)
    duration_seconds: int = Field(default=300, ge=1)
    condition: str = Field(default="", max_length=1000)
    natural_language_condition: str = Field(default="", max_length=1000)
    trigger_mode: AgentMonitorTriggerMode = "deterministic"
    action_prompt: str = Field(default="", max_length=24000)
    planner_rationale: str = Field(default="", max_length=1000)
    risk_notes: str = Field(default="", max_length=1000)
    judge_interval_seconds: int = Field(default=5, ge=1)
    status: AgentMonitorStatus = "queued"
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    conversation_id: str = Field(default="", max_length=160)
    gateway_id: str = Field(default="", max_length=160)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_title(self) -> "AgentMonitorCreate":
        self.title = " ".join(str(self.title or "").split())[:160]
        return self


class AgentMonitorUpdate(BaseModel):
    """Patch one persistent monitor."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=160)
    status: AgentMonitorStatus | None = None
    terminal_session_id: str | None = Field(default=None, max_length=160)
    current_execution_id: str | None = Field(default=None, max_length=160)
    latest_observation_id: str | None = Field(default=None, max_length=160)
    trigger_reason: str | None = Field(default=None, max_length=4000)
    triggered_task_id: str | None = Field(default=None, max_length=160)
    error_preview: str | None = Field(default=None, max_length=4000)
    final_summary: str | None = Field(default=None, max_length=12000)
    latest_judge_summary: str | None = Field(default=None, max_length=4000)
    archived_at: str | None = Field(default=None, max_length=80)


class AgentMonitorRecord(BaseModel):
    """One persisted monitor."""

    model_config = ConfigDict(extra="forbid")

    monitor_id: str
    title: str = ""
    prompt: str = ""
    mode: AgentMonitorMode = "sample_command"
    command: str
    interval_seconds: int = 5
    duration_seconds: int = 300
    condition: str = ""
    natural_language_condition: str = ""
    trigger_mode: AgentMonitorTriggerMode = "deterministic"
    action_prompt: str = ""
    planner_rationale: str = ""
    risk_notes: str = ""
    judge_interval_seconds: int = 5
    latest_judge_summary: str = ""
    status: AgentMonitorStatus = "queued"
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    conversation_id: str = ""
    gateway_id: str = ""
    terminal_session_id: str = ""
    current_execution_id: str = ""
    latest_observation_id: str = ""
    trigger_reason: str = ""
    triggered_task_id: str = ""
    error_preview: str = ""
    final_summary: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    archived_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    triggered_at: str = ""


class AgentMonitorObservationRecord(BaseModel):
    """One stored monitor observation or raw output chunk."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str
    monitor_id: str
    sequence: int = 0
    kind: str = "sample"
    status: str = "ok"
    stdout: str = ""
    stderr: str = ""
    output_preview: str = ""
    exit_code: int | None = None
    matched: bool = False
    match_reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class AgentMonitorTriggerRecord(BaseModel):
    """One trigger event recorded for a monitor."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str
    monitor_id: str
    observation_id: str = ""
    reason: str = ""
    notification_id: str = ""
    task_id: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
