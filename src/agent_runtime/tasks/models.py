"""Typed durable task contracts for the Agent UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentTaskStatus = Literal[
    "queued",
    "running",
    "awaiting_confirmation",
    "awaiting_clarification",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
]

AgentTaskSource = Literal["chat", "task_sheet", "scheduled_event"]
AgentTaskAttemptTrigger = Literal["start", "retry", "resume", "confirmation", "clarification", "continuation", "schedule"]


class AgentTaskCreate(BaseModel):
    """Create one durable task."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)
    title: str = Field(default="", max_length=160)
    source: AgentTaskSource = "task_sheet"
    status: AgentTaskStatus = "queued"
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    conversation_id: str = Field(default="", max_length=160)
    gateway_id: str = Field(default="", max_length=160)
    current_request_id: str = Field(default="", max_length=160)
    latest_request_id: str = Field(default="", max_length=160)
    current_attempt_id: str = Field(default="", max_length=160)
    event_id: str = Field(default="", max_length=160)
    event_run_id: str = Field(default="", max_length=160)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_title(self) -> "AgentTaskCreate":
        self.title = " ".join(str(self.title or "").split())[:160]
        return self


class AgentTaskUpdate(BaseModel):
    """Patch one durable task."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=160)
    status: AgentTaskStatus | None = None
    conversation_id: str | None = Field(default=None, max_length=160)
    gateway_id: str | None = Field(default=None, max_length=160)
    current_request_id: str | None = Field(default=None, max_length=160)
    latest_request_id: str | None = Field(default=None, max_length=160)
    current_attempt_id: str | None = Field(default=None, max_length=160)
    final_response_preview: str | None = Field(default=None, max_length=12000)
    error_preview: str | None = Field(default=None, max_length=4000)
    blocker_reason: str | None = Field(default=None, max_length=4000)
    event_id: str | None = Field(default=None, max_length=160)
    event_run_id: str | None = Field(default=None, max_length=160)
    archived_at: str | None = Field(default=None, max_length=80)


class AgentTaskRecord(BaseModel):
    """One persisted durable task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str = ""
    prompt: str
    source: AgentTaskSource = "task_sheet"
    status: AgentTaskStatus = "queued"
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    conversation_id: str = ""
    gateway_id: str = ""
    current_request_id: str = ""
    latest_request_id: str = ""
    current_attempt_id: str = ""
    final_response_preview: str = ""
    error_preview: str = ""
    blocker_reason: str = ""
    event_id: str = ""
    event_run_id: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    archived_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""


class AgentTaskAttemptRecord(BaseModel):
    """One request attempt belonging to a durable task."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    task_id: str
    request_id: str = ""
    trigger: AgentTaskAttemptTrigger = "start"
    status: AgentTaskStatus = "queued"
    parent_request_id: str = ""
    final_response_preview: str = ""
    error_preview: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    updated_at: str = ""


class AgentTaskCheckpointRecord(BaseModel):
    """One persisted trace-derived checkpoint for a durable task."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    task_id: str
    attempt_id: str = ""
    request_id: str = ""
    trace_event_id: int = 0
    stage: str = ""
    event_type: str = ""
    level: str = "info"
    title: str = ""
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
